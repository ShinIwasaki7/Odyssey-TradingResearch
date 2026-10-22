"""run の高速化の前後で、MT5 経路の run の結果の識別子と判断履歴が変わらないこと。

性能だけの変更であることの確認（高速化の PR の人間の決定 2）。

実行の識別子（`run_id`）にはソースコードのダイジェストが入る（ADR-0006）ので、どんなコード変更でも
`run_id` と結果のダイジェスト（`result_digest`）は変わる。本テストは**コードのダイジェストを固定値に
して**（環境のダイジェストも固定値、`uv.lock` は作業場の固定の文面）、合成データの `experiment run`
の `run_id`・`result_digest`・判断履歴の全表の内容ダイジェストが、高速化の前の main（b0b18c8）で
同じテストを走らせて記録した値と一致することを確かめる。

期待値の作り方: 高速化の差分を入れる前の木（main b0b18c8 にこのテストと作業場の組み立てだけを
足したもの）でこのテストを走らせ、表示した 3 つの値を下の定数に写した。以後、値が変わるのは
run の結果（判断履歴・評価）か識別子の入力が変わったときだけである。

`experiment run` の経路（`app.composition` の結線）を通すので、下位足の供給（`_IntrabarBars`）・
読み取りの関門の内容照合・parquet の読み込み・系列ごとの足の並べ替えがすべて含まれる。

人工データだけを使う（実価格・成績は読まない。行数だけを数える）。
"""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path
from typing import Final

import polars as pl
import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import EXPERIMENT_OUTCOME_FILE
from tests.fixtures.backtest.harness import CODE_DIGEST, ENV_DIGEST
from tests.fixtures.mt5.workspace import Mt5Workspace, build_mt5_workspace

#: 期待値（モジュールの説明の「期待値の作り方」）。2026-10-22 に取り直した（D13 §6.5 の R-9 の
#: 帰結 (1)）: run の設定に run の終わり方（`RunConfig.run_end`。既定値も常に `ConfigDigest` に
#: 入る。D13-23 (a)）を足したので、`ConfigDigest` が変わり、`run_id`・結果のダイジェスト・判断
#: 履歴の全表のダイジェスト（`run_id` 列を含む）の 3 つが変わる。取り直す前の値は
#: `b6e3c2ee…`・`3dec6ef9…`・`731faac9…`（高速化の前の main b0b18c8 で記録）。
EXPECTED_RUN_ID: Final = "305f383f74f1acd365b7a1e598ee2873bc9e028e9361936befdf814c39cbc2be"
EXPECTED_RESULT_DIGEST: Final = "dedbc31ddc9a3d7adc7788d61a53fd7230c648d8c298a5b439f97785fcc55379"
EXPECTED_TRACE_DIGEST: Final = "754aaf5f4f6da9e7b13e5e27ab1a976f6548c7c9458564d79fe0988613873653"
#: 判断履歴の全表から `run_id` 列を除いたダイジェスト。終わり方を足す前の main（55a745c）で同じ
#: テストの組み立てに `without=("run_id",)` を足して記録した値。判断履歴が `run_id` 列以外で
#: 変わらないことを確かめる（D13 §6.5 の R-9 の帰結 (1)）。
EXPECTED_TRACE_DIGEST_WITHOUT_RUN_ID: Final = (
    "15e4d98233cdef3b9c8ebfe16f82d18957c8dd9ccaf8bec930e49677d3f15ebb"
)

DECISION: Final = UtcTime.parse("2026-01-23T20:00:00Z")
RUN: Final = Interval(start=DECISION, end=UtcTime.parse("2026-01-23T21:00:00Z"))
#: 1 分足を作る区間（1 時間足の窓の 20 本＋余裕。木曜 NY 17:00 から金曜の閉場まで）。
DATA: Final = Interval(
    start=UtcTime.parse("2026-01-22T22:00:00Z"), end=UtcTime.parse("2026-01-23T22:00:00Z")
)
FLAT: Final = ("149.005", "149.005", "149.005", "149.005")


def _at(text: str) -> UtcTime:
    return UtcTime.parse(f"2026-01-23T{text}:00Z")


def _prices() -> dict[UtcTime, tuple[str, str, str, str]]:
    """人工の 4 値（`tests/acceptance/test_tick_record_model.py` の突破の筋書きと同じ）。

    20:00 に突破で建ち、20:34〜20:36 の無記録の後の再開の分で損切りと利確の両方に届く（足内の
    競合を 1 分足で解く経路と、執行足の途中の再開の経路を通す）。
    """
    prices: dict[UtcTime, tuple[str, str, str, str]] = {}
    moment = DATA.start
    while moment < DECISION:
        prices[moment] = FLAT
        moment = moment + timedelta(minutes=1)
    hour = UtcTime.parse("2026-01-22T23:00:00Z")
    while hour < _at("19:00"):
        prices[hour] = ("149.005", "150.000", "149.000", "149.005")
        hour = hour + timedelta(hours=1)
    moment = _at("19:00")
    while moment < DECISION:
        prices[moment] = ("149.900", "149.900", "149.900", "149.900")
        moment = moment + timedelta(minutes=1)
    prices[_at("19:00")] = ("149.900", "150.100", "149.800", "149.900")
    prices[_at("19:59")] = ("149.900", "150.040", "149.900", "150.040")
    prices[_at("20:36")] = ("150.000", "152.500", "148.500", "150.000")
    return prices


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Mt5Workspace:
    return build_mt5_workspace(
        tmp_path_factory.mktemp("mt5-identity"),
        data=DATA,
        run=RUN,
        unrecorded=(
            Interval(start=_at("20:34"), end=_at("20:36")),
            Interval(start=_at("20:50"), end=_at("20:53")),
        ),
        prices=_prices(),
    )


def _trace_digest(run_directory: Path, *, without: tuple[str, ...] = ()) -> str:
    """判断履歴の全表（`TraceTable` の各表）の内容のダイジェスト。

    Parquet のバイト列は圧縮設定やライブラリの版で変わりうるので、列名・列の型・全行の値を
    JSON にして SHA-256 を取る。表が無ければ「無い」ことを入れる。
    """
    payload: dict[str, object] = {}
    for table in sorted(TraceTable, key=lambda item: item.value):
        path = run_directory / f"{table.value}.parquet"
        if not path.is_file():
            payload[table.value] = None
            continue
        frame = pl.read_parquet(path)
        frame = frame.drop([name for name in without if name in frame.columns])
        payload[table.value] = {
            "columns": frame.columns,
            "dtypes": [str(dtype) for dtype in frame.dtypes],
            "rows": frame.rows(),
        }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def outcome(
    workspace: Mt5Workspace, tmp_path_factory: pytest.TempPathFactory
) -> tuple[dict[str, object], Path]:
    """コードと環境のダイジェストを固定して `experiment run` を 1 回通した結末記録と成果物。"""
    artifacts = tmp_path_factory.mktemp("mt5-identity-artifacts")
    patch = pytest.MonkeyPatch()
    patch.setattr(composition, "code_digest", lambda: CODE_DIGEST)
    patch.setattr(composition, "env_digest", lambda: ENV_DIGEST)
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = main(workspace.run_argv(artifacts))
    finally:
        patch.undo()
    assert code == 0, out.getvalue() + err.getvalue()
    directory = artifacts / "runs/experiments/strategy_a_mt5_synthetic/v1"
    recorded: dict[str, object] = json.loads(
        (directory / EXPERIMENT_OUTCOME_FILE).read_text(encoding="utf-8")
    )
    return recorded, artifacts


def test_the_run_exercises_fills_and_intrabar_resolution(
    outcome: tuple[dict[str, object], Path],
) -> None:
    """確認の対象が空でないこと（約定と足内の解決の行がある。値は読まない）。"""
    recorded, artifacts = outcome
    assert recorded["status"] == "COMPLETED"
    run_directory = artifacts / "runs" / str(recorded["run_id"])
    assert pl.read_parquet(run_directory / "FILLS.parquet").height > 0
    assert pl.read_parquet(run_directory / "INTRABAR_RESOLUTIONS.parquet").height > 0
    assert pl.read_parquet(run_directory / "RESUMPTIONS.parquet").height > 0


def test_identifiers_and_trace_match_the_record_made_before_the_speedup(
    outcome: tuple[dict[str, object], Path],
) -> None:
    """`run_id`・`result_digest`・判断履歴の全表のダイジェストが高速化の前の記録と一致する。"""
    recorded, artifacts = outcome
    run_directory = artifacts / "runs" / str(recorded["run_id"])
    observed = (
        recorded["run_id"],
        recorded["result_digest"],
        _trace_digest(run_directory),
    )
    expected = (EXPECTED_RUN_ID, EXPECTED_RESULT_DIGEST, EXPECTED_TRACE_DIGEST)
    assert observed == expected, f"observed (run_id, result_digest, trace) = {observed}"


def test_the_trace_is_unchanged_except_the_run_id_column(
    outcome: tuple[dict[str, object], Path],
) -> None:
    """run の終わり方を足しても、判断履歴は `run_id` 列以外で変わらない（D13 §6.5 の R-9 (1)）。"""
    recorded, artifacts = outcome
    run_directory = artifacts / "runs" / str(recorded["run_id"])
    assert _trace_digest(run_directory, without=("run_id",)) == (
        EXPECTED_TRACE_DIGEST_WITHOUT_RUN_ID
    )
