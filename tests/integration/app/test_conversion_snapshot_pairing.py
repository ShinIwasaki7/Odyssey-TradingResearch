"""換算の snapshot の要否・照合と研究期間の run（D06 v1.20 案 §19.2・§19.16 の CT-9・CT-10・CT-17・
CT-26）。

人工データの MT5 経路の作業場（`tests/fixtures/mt5/conversion_workspace.py`）で
`experiment run` を通す。
実データ・成績・実価格は読まない（結末の状態・識別子・区分・通貨の名前だけを見る）。
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config import ConfigError
from odyssey_fx.app.config.experiment_v2 import _validated
from odyssey_fx.app.conversion import ConversionSnapshotMismatch, require_conversion_pairing
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_MANIFEST_FILE,
    EXPERIMENT_OUTCOME_FILE,
)
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant
from tests.fixtures.mt5.conversion_workspace import (
    ConversionWorkspace,
    build_conversion_workspace,
    experiment_text,
)


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> ConversionWorkspace:
    return build_conversion_workspace(tmp_path_factory.mktemp("conversion-workspace"))


def _ids(workspace: ConversionWorkspace) -> tuple[str, str, str]:
    snapshots = workspace.snapshots
    return (
        snapshots[("EURUSD", TimeRuleVariant.STANDARD)],
        snapshots[("USDJPY", TimeRuleVariant.STANDARD)],
        snapshots[("USDJPY", TimeRuleVariant.CANDIDATE)],
    )


def _run(workspace: ConversionWorkspace, name: str, text: str, out: Path) -> tuple[int, str]:
    experiment = workspace.write_experiment(name, text)
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(workspace.run_argv(experiment, out))
    return code, stdout.getvalue() + stderr.getvalue()


def _no_runs(out: Path) -> bool:
    runs = out / "runs"
    return not runs.exists() or not any(
        path.is_dir() and path.name != "experiments" for path in runs.iterdir()
    )


# --- CT-10: 要否とキーの書き方 -------------------------------------------------


def test_ct10_a_foreign_settlement_without_a_conversion_snapshot_is_refused(
    workspace: ConversionWorkspace, tmp_path: Path
) -> None:
    eurusd, _, _ = _ids(workspace)
    text = experiment_text(name="eurusd_missing", snapshot=eurusd)
    code, output = _run(workspace, "eurusd_missing", text, tmp_path)
    assert code == 2, output
    assert "conversion_snapshot" in output
    assert _no_runs(tmp_path)


def test_ct10_a_yen_settlement_with_a_conversion_snapshot_is_refused(
    workspace: ConversionWorkspace, tmp_path: Path
) -> None:
    _, usdjpy, _ = _ids(workspace)
    text = experiment_text(
        name="usdjpy_with_conversion",
        snapshot=usdjpy,
        symbol="USDJPY",
        strategy="configs/strategies/strategy_a_v1.yaml",
        conversion_line=f'conversion_snapshot: "{usdjpy}"\n',
    )
    code, output = _run(workspace, "usdjpy_with_conversion", text, tmp_path)
    assert code == 2, output
    assert "換算の snapshot" in output
    assert _no_runs(tmp_path)


def _payload(text: str) -> dict[str, Any]:
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    return loaded


def test_ct10_the_key_forms_follow_the_single_and_search_rules(
    workspace: ConversionWorkspace,
) -> None:
    """単一実行で `conversion_snapshots`、探索で `conversion_snapshot` を書けば拒否する。"""
    eurusd, usdjpy, candidate = _ids(workspace)
    single = _payload(experiment_text(name="single", snapshot=eurusd))
    single["conversion_snapshots"] = {"standard": usdjpy, "candidate": candidate}
    with pytest.raises(ConfigError, match="conversion_snapshots"):
        _validated(single, Path("single.yaml"), (2,))
    search = _payload(experiment_text(name="search", snapshot=eurusd))
    del search["snapshot"]
    del search["run_interval"]
    search["snapshots"] = {"standard": eurusd, "candidate": eurusd}
    search["search_plan"] = {
        "kind": "GRID",
        "max_trials": 1,
        "axes": [{"instance": "breakout_level", "parameter": "lookback", "values": [20]}],
    }
    search["split"] = "STANDARD"
    search["final_holdout"] = "NONE"
    search["conversion_snapshot"] = usdjpy
    with pytest.raises(ConfigError, match="conversion_snapshot"):
        _validated(search, Path("search.yaml"), (2,))


# --- CT-9: 同じ組の照合 -----------------------------------------------------------


def test_ct9_a_conversion_snapshot_of_another_variant_stops_before_anything_is_written(
    workspace: ConversionWorkspace, tmp_path: Path
) -> None:
    eurusd, _, candidate = _ids(workspace)
    text = experiment_text(
        name="eurusd_wrong_variant",
        snapshot=eurusd,
        conversion_line=f'conversion_snapshot: "{candidate}"\n',
    )
    code, output = _run(workspace, "eurusd_wrong_variant", text, tmp_path)
    assert code == 1, output
    assert "variant" in output and "D06 §19.2" in output
    assert _no_runs(tmp_path)
    assert not (tmp_path / "runs/experiments").exists()


def test_ct9_each_pairing_item_is_checked_and_named(workspace: ConversionWorkspace) -> None:
    """経路・組・時刻の規則の表・カレンダー・集約規則の版・承認のどれが違っても止まり、項目が出る。"""
    eurusd, usdjpy, _ = _ids(workspace)
    store = ParquetSnapshotStore(root=workspace.snapshots_root)
    execution = store.open_readable(eurusd).manifest
    conversion = store.open_readable(usdjpy).manifest
    kwargs: dict[str, Any] = {
        "calendar_id": conversion.conversion.calendar_id,
        "calendar_version": conversion.conversion.calendar_version,
        "execution_id": eurusd,
        "conversion_id": usdjpy,
    }
    require_conversion_pairing(execution, conversion, **kwargs)
    mt5 = conversion.conversion.mt5
    assert mt5 is not None
    table = mt5.time_rule_table
    cases = {
        "route": replace(conversion, conversion=replace(conversion.conversion, mt5=None)),
        "time rule table": replace(
            conversion,
            conversion=replace(
                conversion.conversion,
                mt5=replace(mt5, time_rule_table=replace(table, version=table.version + 1)),
            ),
        ),
        "calendar": replace(
            conversion, conversion=replace(conversion.conversion, calendar_version=9)
        ),
        "aggregation rule": replace(
            conversion,
            conversion=replace(conversion.conversion, aggregation_rule_version="other"),
        ),
        "approval": replace(conversion, approval=None),
    }
    for item, manifest in cases.items():
        with pytest.raises(ConversionSnapshotMismatch, match=item):
            require_conversion_pairing(execution, manifest, **kwargs)
    with pytest.raises(ConversionSnapshotMismatch, match="calendar"):
        require_conversion_pairing(execution, conversion, **{**kwargs, "calendar_version": 7})


# --- CT-26・CT-17: 研究期間の run --------------------------------------------------


@pytest.fixture(scope="module")
def converted(
    workspace: ConversionWorkspace, tmp_path_factory: pytest.TempPathFactory
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    eurusd, usdjpy, _ = _ids(workspace)
    out = tmp_path_factory.mktemp("conversion-run")
    text = experiment_text(
        name="eurusd_converted",
        snapshot=eurusd,
        conversion_line=f'conversion_snapshot: "{usdjpy}"\n',
    )
    code, output = _run(workspace, "eurusd_converted", text, out)
    assert code == 0, output
    directory = out / "runs/experiments/eurusd_converted/v1"
    outcome = json.loads((directory / EXPERIMENT_OUTCOME_FILE).read_text(encoding="utf-8"))
    manifest = json.loads((directory / EXPERIMENT_MANIFEST_FILE).read_text(encoding="utf-8"))
    return outcome, manifest, out


def test_ct26_a_research_run_with_a_conversion_snapshot_reads_research_history_only(
    workspace: ConversionWorkspace, converted: tuple[dict[str, Any], dict[str, Any], Path]
) -> None:
    """CT-26: 研究期間の run は並びが空で、換算の snapshot も研究履歴の partition だけを読む。"""
    outcome, manifest, out = converted
    _, usdjpy, _ = _ids(workspace)
    assert manifest["conversion_snapshot_id"] == usdjpy
    classes = {item["access_class"] for item in manifest["conversion_allowed_partitions"]}
    assert classes == {"RESEARCH_HISTORY"}
    run_manifest = json.loads(
        (out / "runs" / outcome["run_id"] / "manifest.json").read_text(encoding="utf-8")
    )
    config = json.loads(run_manifest["config"])
    assert "sealed_reads" not in config
    assert config["conversion_snapshot_ref"]["snapshot_id"] == usdjpy
    assert run_manifest["capability_report"]["conversion"]["route_found"] is True
    legs = run_manifest["capability_report"]["conversion"]["legs"]
    assert [leg["series"] for leg in legs] == ["USDJPY/1m/bid"]


def test_ct17_the_converted_run_passes_the_single_currency_check(
    converted: tuple[dict[str, Any], dict[str, Any], Path],
) -> None:
    """CT-17: 円以外の決済通貨の run でも評価の整合検査（C8 を含む）が合格し、実験が完走する。"""
    outcome, _, out = converted
    assert outcome["status"] == "COMPLETED"
    assert outcome["run_status"] == "COMPLETED"
    run_directory = out / "runs" / outcome["run_id"]
    checks = list((run_directory / "eval").rglob("CONSISTENCY_CHECKS*"))
    assert checks, "the evaluation must write its consistency checks"
    # 約定と時価評価の換算が実際に通っている（行の数と種別だけを見る）。
    assert pl.read_parquet(run_directory / "FILLS.parquet").height == 2
    kinds = set(pl.read_parquet(run_directory / "EVIDENCE.parquet")["kind"].to_list())
    assert {"ADMISSION", "FILL", "MARK_TO_MARKET"} <= kinds
