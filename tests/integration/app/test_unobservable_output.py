"""観測できない項目の一覧の画面出力（D03 §15.6 の 5 v1.25、D12-132 (a)、D12 §12.8-6 の決定）。

- 一覧は識別と版の見出しと、表 2 の 8 行（T1・T3・T4 だけ決定の文言）を持つ。
- MT5 経路の snapshot を分類（`classify`）で確定すると、画面出力の末尾に一覧が出る。
- 一覧は検査報告のファイルには入らない（報告のダイジェストと識別子は一覧で変わらない）。

人工データだけを使う（実価格・成績は読まない）。HistData 経路の分類で出ないことは
`test_data_cli.py` の `test_classify_of_a_histdata_snapshot_does_not_print_the_unobservable_list`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.cli import summary
from odyssey_fx.app.cli.main import _write_snapshot, main
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.report_digest import integrity_report_text
from odyssey_fx.marketdata.domain.unobservable import (
    UNOBSERVABLE_ITEMS,
    UNOBSERVABLE_LIST_ID,
    UNOBSERVABLE_LIST_VERSION,
    unobservable_heading,
)
from tests.fixtures.mt5 import market as m5
from tests.fixtures.synthetic import market

CALENDAR = market.calendar()
WEEK = Interval(
    start=UtcTime.parse("2026-01-04T22:00:00Z"), end=UtcTime.parse("2026-01-09T22:00:00Z")
)
ROOT = Path(__file__).resolve().parents[3]


def test_the_list_has_the_decided_rows_and_wording() -> None:
    """D12 §12.8 の「観測できない項目の一覧 版 1」: 表 2 を行ごとに写し、T1・T3・T4 だけ
    直した文言。"""
    assert (UNOBSERVABLE_LIST_ID, UNOBSERVABLE_LIST_VERSION) == ("mt5_m1_unobservable", 1)
    assert [item.condition for item in UNOBSERVABLE_ITEMS] == [
        "T1 時刻の変換規則",
        "T2 逆行・重複",
        "T3 ask ≥ bid",
        "T4 tick の間隔の上限",
        "T5 週末・休場との整合",
        "T6 開場と閉場の位置",
        "T7 配信断の公表",
        "（層 2）足の中の値",
    ]
    items = {item.condition[:2]: item.item for item in UNOBSERVABLE_ITEMS}
    assert items["T1"] == "時刻の規則の表 版 1 で確認済みは行 3 だけ。他の行の変換は仮定付き"
    assert items["T3"] == (
        "各 tick の ask ≥ bid。1 分足のスプレッドが 0 以上でも、足の中の全 tick で ask ≥ bid とは"
        "読まない"
    )
    assert items["T4"] == "1 分より短い配信断。無記録の分が配信断か取引の少ない分かは見分けない"
    assert items["T7"] == "—"
    # D12 §12.8 の表と同じ文言（設計文書の正本とコードの食い違いを検出する）。
    design = (ROOT / "docs/design/D12_data_source_strategy_review.md").read_text(encoding="utf-8")
    for item in UNOBSERVABLE_ITEMS:
        assert f"| {item.condition} | {item.item} |" in design


def test_the_summary_lines_carry_the_heading_and_every_row() -> None:
    lines = summary.unobservable_lines()
    assert f"{unobservable_heading()}:" in lines
    assert "版 1" in unobservable_heading()
    for item in UNOBSERVABLE_ITEMS:
        assert f"  {item.condition}: {item.item}" in lines


def test_classify_of_an_mt5_snapshot_prints_the_list_and_the_report_does_not_carry_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D12 §12.8-6 の 2 の決定「画面出力にだけ毎回出す」: 確定の画面出力の末尾に一覧が出て、検査
    報告のファイルには入らない。"""
    table = m5.write_rule_files(tmp_path)
    spreads = {UtcTime.parse("2026-01-06T10:00:00Z"): -1}
    rows = m5.minute_rows(CALENDAR, WEEK, spreads=spreads)
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])
    out = tmp_path / "data/snapshots"
    store = ParquetSnapshotStore(root=out)
    provisional = str(pending.provisional_id)
    _write_snapshot(store, f"_pending/{provisional}", pending, pending.manifest)
    decisions = tmp_path / "decisions.yaml"
    decisions.write_text("schema_version: 2\ndecisions: []\n", encoding="utf-8")
    capsys.readouterr()

    code = main(
        [
            "data",
            "classify",
            "--pending",
            provisional,
            "--decisions",
            str(decisions),
            "--timeframes",
            str(ROOT / "configs/calendars/timeframes_v2.yaml"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out.splitlines()
    heading = printed.index(f"{unobservable_heading()}:")
    assert printed[heading + 1 :] == [
        f"  {item.condition}: {item.item}" for item in UNOBSERVABLE_ITEMS
    ]
    final = next(path.name for path in out.iterdir() if path.name != "_pending")
    report_text = (out / final / "integrity_report.json").read_text(encoding="utf-8")
    assert report_text == integrity_report_text(pending.report)
    assert "観測できない項目" not in report_text
    assert "NEGATIVE_SPREAD" in report_text
