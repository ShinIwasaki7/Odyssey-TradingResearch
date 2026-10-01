"""再取得（補充）のコマンドの通し試験（D03 §10 の v1.15、§14.12 の出来事1・2・9）。

人工の原 CSV（USDJPY 2020-11-30 の 01 時台を 15分足・1時間足の両方で欠かせる。00 時台は
実証の値）を受け入れ → 欠落をデータ欠損と分類 → 承認し、`data refill plan` と
`data refill fetch` を実際のコマンドとして実行する。提供元へは通信せず、取得元を固定の応答を
返す偽物に差し替える。

確かめること:

- 計画は承認済み snapshot だけを入力にし、`plan_id` を表示する。同じ計画を 2 度作ると失敗する。
- 取得は時間ファイルを保管場所に置き、取得記録に最終結果を書き、再実行では取り直さない。
- 承認前の snapshot・存在しない計画は 1 行の失敗で終わる（終了コード 1）。
- 画面に価格を出さない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.classification import classifiable_warnings
from tests.fixtures.refill import (
    BI5_00H,
    BI5_01H,
    HOUR_00,
    HOUR_01,
    USDJPY_1H,
    USDJPY_15M,
    FakeTickSource,
    raw_bars,
    url_of,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS = REPO_ROOT / "configs"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """原 CSV と設定を置いた作業場（USDJPY だけ）。"""
    root = tmp_path / "repo"
    raw = root / "data/raw/market"
    raw.mkdir(parents=True)
    configs = root / "configs"
    for name in ("calendars", "datasources"):
        (configs / name).mkdir(parents=True)
        for path in sorted((CONFIGS / name).glob("*.yaml")):
            (configs / name / path.name).write_text(path.read_text(encoding="utf-8"), "utf-8")
    (configs / "symbols").mkdir()
    (configs / "symbols/USDJPY.yaml").write_text(
        (CONFIGS / "symbols/USDJPY.yaml").read_text(encoding="utf-8"), "utf-8"
    )
    from tests.fixtures.synthetic import market

    # 日足が 1 本できるよう、日曜の開場から火曜の開場まで（上位足の欠落も分類できるように）。
    window = Interval(
        start=UtcTime.parse("2020-11-29T22:00:00Z"), end=UtcTime.parse("2020-12-01T22:00:00Z")
    )
    bars = raw_bars(window=window)
    for series, name in ((USDJPY_15M, "15m"), (USDJPY_1H, "1h")):
        (raw / f"USDJPY_{name}_merged.csv").write_text(market.csv_text(bars[series]), "utf-8")
    return root


def _run(*args: str) -> int:
    return main(list(args))


def _approved_snapshot(repo: Path, *, approve: bool = True) -> str:
    configs = repo / "configs"
    snapshots = repo / "data/snapshots"
    assert (
        _run(
            "data",
            "accept",
            "--datasource",
            str(configs / "datasources/legacy_merged_csv_v1.yaml"),
            "--calendar",
            str(configs / "calendars/fx_ny17_v1.yaml"),
            "--timeframes",
            str(configs / "calendars/timeframes_v1.yaml"),
            "--symbols",
            str(configs / "symbols"),
            "--out",
            str(snapshots),
            "--repo-root",
            str(repo),
        )
        == 0
    )
    (pending,) = sorted((snapshots / "_pending").iterdir())
    store = ParquetSnapshotStore(root=snapshots)
    report = store.read_integrity_report(f"_pending/{pending.name}")
    outcomes = {"MISSING_EXPECTED_BAR": "DATA_GAP", "UNEXPECTED_BAR": "OUT_OF_SESSION_DATA"}
    lines = ["schema_version: 2", "decisions:"]
    for result in classifiable_warnings((report,)):
        lines.extend(
            [
                f"  - kind: {result.kind.value}",
                "    interval:",
                f'      start: "{result.interval.start}"',
                f'      end: "{result.interval.end}"',
                f"    series: [{result.series}]",
                f"    outcome: {outcomes[result.kind.value]}",
                "    note: 試験の分類",
            ]
        )
    decisions = repo / "decisions.yaml"
    decisions.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert (
        _run(
            "data",
            "classify",
            "--pending",
            pending.name,
            "--decisions",
            str(decisions),
            "--timeframes",
            str(configs / "calendars/timeframes_v1.yaml"),
            "--out",
            str(snapshots),
        )
        == 0
    )
    (final,) = sorted(path.name for path in snapshots.iterdir() if path.name != "_pending")
    if approve:
        assert (
            _run("data", "approve", "--snapshot", final, "--by", "試験", "--out", str(snapshots))
            == 0
        )
    return final


def _plan(repo: Path, snapshot: str, *extra: str) -> int:
    configs = repo / "configs"
    return _run(
        "data",
        "refill",
        "plan",
        "--snapshot",
        snapshot,
        "--calendar",
        str(configs / "calendars/fx_ny17_v2.yaml"),
        "--provider",
        str(configs / "datasources/dukascopy_tick_v1.yaml"),
        "--out",
        str(repo / "data/raw/market/refill"),
        "--snapshots",
        str(repo / "data/snapshots"),
        "--datasource",
        str(configs / "datasources/legacy_merged_csv_v1.yaml"),
        "--timeframes",
        str(configs / "calendars/timeframes_v1.yaml"),
        "--repo-root",
        str(repo),
        *extra,
    )


def _plan_id(repo: Path) -> str:
    (work,) = sorted((repo / "data/raw/market/refill/_work").iterdir())
    return work.name


def test_plan_then_fetch_through_the_commands(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = _approved_snapshot(repo)
    capsys.readouterr()
    assert _plan(repo, snapshot) == 0
    plan_id = _plan_id(repo)
    shown = capsys.readouterr().out
    assert f"取得計画の識別子（plan_id）: {plan_id}" in shown
    assert "対象足: 5 本" in shown
    assert "照合用の時間 1" in shown
    plan = json.loads(
        (repo / f"data/raw/market/refill/_work/{plan_id}/plan.json").read_text(encoding="utf-8")
    )
    assert plan["snapshot_id"] == snapshot

    source = FakeTickSource({url_of(HOUR_00): [BI5_00H], url_of(HOUR_01): [BI5_01H]})
    monkeypatch.setattr(composition, "tick_archive_source", lambda: source)
    refill = str(repo / "data/raw/market/refill")
    assert _run("data", "refill", "fetch", "--plan", plan_id, "--out", refill) == 0
    shown = capsys.readouterr().out
    assert "PLANNED → FETCH_DONE" in shown
    assert "最終結果 FETCHED: 2" in shown
    assert "104." not in shown and "103." not in shown  # 価格を出さない
    # 再実行は取り直さない（偽の取得元は応答を使い切っている）。
    assert _run("data", "refill", "fetch", "--plan", plan_id, "--out", refill) == 0
    assert "取得済みとして飛ばした時間ファイル: 2" in capsys.readouterr().out


def test_the_same_plan_cannot_be_created_twice(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = _approved_snapshot(repo)
    assert _plan(repo, snapshot) == 0
    capsys.readouterr()
    assert _plan(repo, snapshot) == 1
    assert "already exists" in capsys.readouterr().err


def test_an_unapproved_snapshot_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snapshot = _approved_snapshot(repo, approve=False)
    capsys.readouterr()
    assert _plan(repo, snapshot) == 1
    assert "approved" in capsys.readouterr().err
    assert not (repo / "data/raw/market/refill/_work").exists()


def test_a_filter_without_targets_writes_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = _approved_snapshot(repo)
    capsys.readouterr()
    assert _plan(repo, snapshot, "--symbols", "EURUSD") == 1
    assert "no plan was written" in capsys.readouterr().err
    assert not (repo / "data/raw/market/refill/_work").exists()


def test_changed_raw_data_stops_the_plan(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snapshot = _approved_snapshot(repo)
    path = repo / "data/raw/market/USDJPY_1h_merged.csv"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    capsys.readouterr()
    assert _plan(repo, snapshot) == 1
    assert "do not match the snapshot" in capsys.readouterr().err


def test_fetching_an_unknown_plan_fails_cleanly(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    refill = str(repo / "data/raw/market/refill")
    assert _run("data", "refill", "fetch", "--plan", "a" * 64, "--out", refill) == 1
    assert "no refill plan" in capsys.readouterr().err
