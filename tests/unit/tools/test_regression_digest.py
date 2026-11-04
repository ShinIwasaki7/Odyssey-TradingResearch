"""回帰確認用のダイジェスト（D06 v1.20 案 §19.15。受入テスト CT-18。仮置き C1-12）。

人工の成果物で確かめる（実データ・成績・価格は読まない）。

- 小さな人工の成果物（`runs/<run_id>/` の run manifest・判断履歴の表と、`eval/<run_evaluation_id>/`
  の評価 manifest・評価 5 表を、実際の置き場と列名で書いたもの）: run の識別子とコードのダイジェスト
  だけが違う 2 つで同じ値（整合検査の表の C2 の `expected`・`observed` に埋め込まれた run の識別子を
  含めて）、金額の列を 1 つ変えると違う値、後の run にだけある列が既定の値でなければ失敗。
- 人工データの MT5 経路の作業場（`tests/fixtures/mt5/workspace.py`）で `experiment run` を、コードの
  ダイジェストだけを変えて 2 回通した成果物: `run_id` と `result_digest` は違い、回帰確認用の
  ダイジェストは一致する（判断履歴の 22 表と評価 5 表の実際の列で、整列鍵が揃っていること）。
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import polars as pl
import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main as odyssey_main
from odyssey_fx.backtest.trace.recorder import TraceTable, table_columns
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.refs import CodeDigest
from odyssey_fx.evaluation.adapters.fs_store import EXPERIMENT_OUTCOME_FILE
from odyssey_fx.evaluation.application.manifest import EvaluationTable
from tests.fixtures.backtest.harness import ENV_DIGEST
from tests.fixtures.mt5.workspace import build_mt5_workspace
from tools.ops.regression_digest import (
    EVALUATION_SORT_KEYS,
    TRACE_SORT_KEYS,
    RegressionDigestError,
    compare_runs,
    is_identity_default,
    main,
)


def _hex(name: str) -> str:
    return digest(name).hex


@dataclass(frozen=True, slots=True)
class _Ids:
    run_id: str
    evaluation_id: str
    code_digest: str


_BEFORE: Final = _Ids(_hex("run before"), _hex("evaluation before"), _hex("code before"))
_AFTER: Final = _Ids(_hex("run after"), _hex("evaluation after"), _hex("code after"))


def _parquet(path: Path, columns: Mapping[str, list[Any]], dtypes: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(dict(columns), schema=dict(dtypes)).write_parquet(path)


def _write_artifact(
    root: Path,
    ids: _Ids,
    *,
    balance: str = "1000100",
    extra: Mapping[str, list[Any]] | None = None,
    reverse: bool = False,
) -> Path:
    """人工の成果物を 1 本書く（実際の置き場・ファイル名・列名。値は形だけの人工の値）。

    `reverse` は判断履歴の表の格納順を逆にする（整列鍵で並べるので結果は変わらない）。
    `extra` は約定の表（`FILLS`）に足す列（後の run にだけある列の確かめ）。
    """
    run = root / "runs" / ids.run_id
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": ids.run_id,
                "config_digest": _hex("config"),
                "code_digest": ids.code_digest,
                "lock_digest": _hex("lock"),
                "env_digest": _hex("env"),
                "status": "COMPLETED",
            }
        ),
        encoding="utf-8",
    )

    def order(items: list[Any]) -> list[Any]:
        return items[::-1] if reverse else items

    fills: dict[str, list[Any]] = {
        "run_id": [ids.run_id] * 2,
        "fill_id": order(["FIL:00000001", "FIL:00000002"]),
        "cost_commission_amount": order(["10", "10"]),
        "cost_commission_currency": ["JPY", "JPY"],
        **(dict(extra) if extra else {}),
    }
    _parquet(
        run / "FILLS.parquet",
        fills,
        {name: pl.List(pl.String()) if name == "paths" else pl.String() for name in fills},
    )
    _parquet(
        run / "LEDGER_SNAPSHOTS.parquet",
        {
            "run_id": [ids.run_id] * 2,
            "at_time": order(["2026-01-23T20:00:00Z", "2026-01-23T21:00:00Z"]),
            "at_phase": ["EXECUTION_OPEN", "EXECUTION_OPEN"],
            "at_sequence": [0, 0],
            "balance_amount": order(["1000000", balance]),
            "balance_currency": ["JPY", "JPY"],
        },
        {
            "run_id": pl.String(),
            "at_time": pl.String(),
            "at_phase": pl.String(),
            "at_sequence": pl.Int64(),
            "balance_amount": pl.String(),
            "balance_currency": pl.String(),
        },
    )
    evaluation = run / "eval" / ids.evaluation_id
    evaluation.mkdir(parents=True)
    (evaluation / "evaluation.json").write_text(
        json.dumps(
            {
                "run_evaluation_id": ids.evaluation_id,
                "run_id": ids.run_id,
                "run_manifest_ref": _hex("config"),
                "evaluation_code_digest": ids.code_digest,
                "run_code_digest": ids.code_digest,
                "result_digest": _hex(f"result {ids.run_id}"),
            }
        ),
        encoding="utf-8",
    )
    text: dict[str, list[Any]] = {"metric_id": ["NET_PROFIT"], "value_amount_amount": ["100"]}
    _parquet(evaluation / "METRICS.parquet", text, dict.fromkeys(text, pl.String()))
    counts: dict[str, list[Any]] = {
        "category": ["CLOSE_CAUSE"],
        "key": ["TAKE_PROFIT"],
        "count": [1],
    }
    _parquet(
        evaluation / "CATEGORY_COUNTS.parquet",
        counts,
        {"category": pl.String(), "key": pl.String(), "count": pl.Int64()},
    )
    trades: dict[str, list[Any]] = {
        "position_id": ["POS:00000001"],
        "entry_at_time": ["2026-01-23T20:00:00Z"],
        "entry_at_phase": ["EXECUTION_OPEN"],
        "entry_at_sequence": [0],
        "realized_amount": ["110"],
    }
    _parquet(
        evaluation / "TRADES.parquet",
        trades,
        {name: pl.Int64() if name == "entry_at_sequence" else pl.String() for name in trades},
    )
    diagnostics: dict[str, list[Any]] = {"fill_id": ["FIL:00000001", "FIL:00000002"]}
    _parquet(evaluation / "FILL_DIAGNOSTICS.parquet", diagnostics, {"fill_id": pl.String()})
    checks: dict[str, list[Any]] = {
        "check": ["required_columns_present", "run_id_consistent"],
        "level": ["FATAL", "FATAL"],
        "outcome": ["PASSED", "PASSED"],
        "table": [None, None],
        # C2 の期待値と観測値は run の識別子の正規化エンコード（実際の成果物と同じ形）。
        "expected": ['"ok"', f'"{ids.run_id}"'],
        "observed": ['"ok"', f'["{ids.run_id}"]'],
    }
    _parquet(evaluation / "CONSISTENCY_CHECKS.parquet", checks, dict.fromkeys(checks, pl.String()))
    return run


def test_artifacts_differing_only_in_run_id_and_code_digest_have_the_same_digest(
    tmp_path: Path,
) -> None:
    before = _write_artifact(tmp_path / "before", _BEFORE)
    after = _write_artifact(tmp_path / "after", _AFTER, reverse=True)
    result = compare_runs(before, after)
    assert result.matches
    assert result.before_digest == result.after_digest
    assert len(result.before_digest) == 64
    assert result.failures == () and result.after_only_columns == ()
    assert set(result.compared_tables) == {table.value for table in EvaluationTable} | {
        "FILLS",
        "LEDGER_SNAPSHOTS",
    }
    assert result.dropped_columns["FILLS"] == ("run_id",)
    assert result.dropped_columns["CONSISTENCY_CHECKS"] == ()


def test_changing_one_money_column_changes_the_digest(tmp_path: Path) -> None:
    before = _write_artifact(tmp_path / "before", _BEFORE)
    after = _write_artifact(tmp_path / "after", _AFTER, balance="1000101")
    result = compare_runs(before, after)
    assert not result.matches
    assert result.before_digest != result.after_digest
    assert result.failures == ()


def test_another_runs_identifier_is_not_replaced(tmp_path: Path) -> None:
    """置き換えはその run 自身の識別子だけ（他の run の識別子が埋まっていれば違う値になる）。"""
    before = _write_artifact(tmp_path / "before", _BEFORE)
    after = _write_artifact(tmp_path / "after", _AFTER)
    checks = after / "eval" / _AFTER.evaluation_id / "CONSISTENCY_CHECKS.parquet"
    frame = pl.read_parquet(checks).with_columns(
        pl.col("observed").str.replace(_AFTER.run_id, _BEFORE.run_id, literal=True)
    )
    frame.write_parquet(checks)
    assert not compare_runs(before, after).matches


def test_an_after_only_column_with_identity_defaults_is_dropped(tmp_path: Path) -> None:
    identity = '{"legs":[],"observed_at":"2026-01-23T20:00:00Z","rate":"1e0","skew":"0.000000"}'
    before = _write_artifact(tmp_path / "before", _BEFORE)
    after = _write_artifact(
        tmp_path / "after",
        _AFTER,
        extra={"conversion": [None, identity], "paths": [[], [identity]]},
    )
    result = compare_runs(before, after)
    assert result.matches
    assert result.after_only_columns == ("FILLS.conversion", "FILLS.paths")


def test_an_after_only_column_with_a_non_default_value_fails(tmp_path: Path) -> None:
    converted = '{"legs":[{"series":"USDJPY/1m/bid"}],"rate":"150e0"}'
    before = _write_artifact(tmp_path / "before", _BEFORE)
    after = _write_artifact(tmp_path / "after", _AFTER, extra={"conversion": [None, converted]})
    result = compare_runs(before, after)
    assert result.before_digest == result.after_digest  # 列はダイジェストから除く
    assert result.failures == ("FILLS.conversion",)
    assert not result.matches


def test_a_column_missing_from_the_after_run_is_a_mismatch(tmp_path: Path) -> None:
    """前の run にだけある列は除かない（列が消えたことはダイジェストの違いに出る）。"""
    before = _write_artifact(tmp_path / "before", _BEFORE, extra={"gap_kind": [None, None]})
    after = _write_artifact(tmp_path / "after", _AFTER)
    assert not compare_runs(before, after).matches


def test_a_column_of_paths_under_the_run_directory_is_dropped(tmp_path: Path) -> None:
    """値のある行がすべて `runs/<run_id>/` の下のパスの列は除く（手順 3 の識別子を含むパス）。"""

    def paths(ids: _Ids) -> dict[str, list[Any]]:
        return {"source": [f"runs/{ids.run_id}/FILLS.parquet", None]}

    before = _write_artifact(tmp_path / "before", _BEFORE, extra=paths(_BEFORE))
    after = _write_artifact(tmp_path / "after", _AFTER, extra=paths(_AFTER))
    result = compare_runs(before, after)
    assert result.matches
    assert result.dropped_columns["FILLS"] == ("run_id", "source")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ([], True),
        ('{"legs":[],"rate":"1e0","skew":"0.000000"}', True),
        ('{"legs":[],"rate":"150e0"}', False),
        ("JPY", False),
        (0, False),
    ],
)
def test_identity_defaults(value: object, expected: bool) -> None:
    assert is_identity_default(value) is expected


def test_the_cli_prints_only_the_two_digests_and_the_verdict(tmp_path: Path) -> None:
    before = _write_artifact(tmp_path / "before", _BEFORE)
    after = _write_artifact(tmp_path / "after", _AFTER)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(["--before", str(before), "--after", str(after)])
    lines = stdout.getvalue().splitlines()
    assert code == 0
    digest_value = compare_runs(before, after).before_digest
    assert lines == [
        f"前の run の回帰確認用のダイジェスト: {digest_value}",
        f"後の run の回帰確認用のダイジェスト: {digest_value}",
        "判定: 一致",
    ]
    changed = _write_artifact(tmp_path / "changed", _AFTER, balance="1")
    with redirect_stdout(io.StringIO()) as printed:
        assert main(["--before", str(before), "--after", str(changed)]) == 1
    assert "判定: 不一致" in printed.getvalue()
    assert "1000100" not in printed.getvalue()  # 金額の値を出さない


def test_a_broken_artifact_is_an_input_error(tmp_path: Path) -> None:
    before = _write_artifact(tmp_path / "before", _BEFORE)
    after = _write_artifact(tmp_path / "after", _AFTER)
    (after / "eval" / _AFTER.evaluation_id / "TRADES.parquet").unlink()
    with pytest.raises(RegressionDigestError):
        compare_runs(before, after)
    with redirect_stderr(io.StringIO()):
        assert main(["--before", str(before), "--after", str(tmp_path / "nowhere")]) == 2


def test_the_sort_keys_cover_every_table_with_declared_columns() -> None:
    """整列鍵は判断履歴の全表と評価 5 表にあり、判断履歴の鍵は宣言された列である。"""
    assert set(TRACE_SORT_KEYS) == set(TraceTable)
    assert set(EVALUATION_SORT_KEYS) == set(EvaluationTable)
    for table, keys in TRACE_SORT_KEYS.items():
        assert set(keys) <= set(table_columns(table)), table


# --- 人工データの run を 2 回通した成果物（実際の 22 表・評価 5 表）-----------------------------


def _run_once(workspace_argv: Sequence[str], code: CodeDigest) -> Path:
    patch = pytest.MonkeyPatch()
    patch.setattr(composition, "code_digest", lambda: code)
    patch.setattr(composition, "env_digest", lambda: ENV_DIGEST)
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            status = odyssey_main(list(workspace_argv))
    finally:
        patch.undo()
    assert status == 0, out.getvalue() + err.getvalue()
    return Path(workspace_argv[workspace_argv.index("--out") + 1])


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, list[str]]:
    from tests.integration.app.test_mt5_run_identity import DATA, RUN, _prices

    root = tmp_path_factory.mktemp("regression-digest")
    workspace = build_mt5_workspace(root / "workspace", data=DATA, run=RUN, prices=_prices())
    found: list[Path] = []
    run_ids: list[str] = []
    for name in ("before", "after"):
        artifacts = _run_once(workspace.run_argv(root / name), CodeDigest(digest(f"code {name}")))
        outcome = json.loads(
            (
                artifacts / "runs/experiments/strategy_a_mt5_synthetic/v1" / EXPERIMENT_OUTCOME_FILE
            ).read_text(encoding="utf-8")
        )
        run_ids.append(str(outcome["run_id"]))
        found.append(artifacts / "runs" / str(outcome["run_id"]))
    return found[0], found[1], run_ids


def test_two_synthetic_runs_with_different_code_digests_match(
    two_runs: tuple[Path, Path, list[str]],
) -> None:
    before, after, run_ids = two_runs
    assert run_ids[0] != run_ids[1]  # コードのダイジェストが違うので run の識別子は違う
    result = compare_runs(before, after)
    assert result.matches, result.failures
    assert set(result.compared_tables) == {table.value for table in EvaluationTable} | {
        table.value for table in TraceTable
    }
    for table in TraceTable:
        assert result.dropped_columns[table.value] == ("run_id",)
    for evaluation_table in EvaluationTable:
        assert result.dropped_columns[evaluation_table.value] == ()
