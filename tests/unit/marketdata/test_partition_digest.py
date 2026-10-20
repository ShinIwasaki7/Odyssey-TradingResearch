"""partition の内容ダイジェストの単体テスト（D03 §3.7.1）。

確かめること:

- 同じ足なら同じ値、渡す順序に依存しない（受入れの決定論性）。
- 足を1本でも変えれば値が変わる（差し替えを検出できる）。
- adapters が書いて返す値と、application が再計算した値が一致する（読み取り側が照合できる）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.partition_digest import (
    PARTITION_COLUMNS,
    negative_spread_text,
    partition_digest_hex,
    partition_row,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.mt5 import market as m5
from tests.fixtures.synthetic import market

HOURLY = market.series()
CALENDAR = market.calendar()
WINDOW = Interval(
    start=UtcTime.parse("2026-01-13T22:00:00Z"), end=UtcTime.parse("2026-01-14T22:00:00Z")
)
PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)


def test_the_digest_is_stable_for_the_same_content() -> None:
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    assert partition_digest_hex(bars) == partition_digest_hex(bars)


def test_the_digest_ignores_the_order_of_the_bars() -> None:
    """渡す順序は結果に影響しない（受入れの決定論性、D03 §4）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    assert partition_digest_hex(bars) == partition_digest_hex(tuple(reversed(bars)))


def test_changing_one_bar_changes_the_digest() -> None:
    """足を1本差し替えれば値が変わる（差し替えを検出できる）。"""
    bars = list(market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW))
    before = partition_digest_hex(bars)
    bars[3] = market.make_bar(HOURLY, bars[3].interval, volume="999")
    assert partition_digest_hex(bars) != before


def test_removing_one_bar_changes_the_digest() -> None:
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    assert partition_digest_hex(bars[:-1]) != partition_digest_hex(bars)


def test_an_empty_partition_has_a_digest() -> None:
    """足が1本も無い partition にも値がある（記録できる）。"""
    assert len(partition_digest_hex(())) == 64


def test_the_row_has_one_value_per_declared_column() -> None:
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    assert len(partition_row(bars[0])) == len(PARTITION_COLUMNS)


def test_the_adapter_returns_the_same_digest_the_application_computes(
    tmp_path: Path,
) -> None:
    """書いた側と読む側が同じ値を出す（読み取り側が照合できる、D03 §3.7.1）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    written = ParquetSnapshotStore(root=tmp_path).write_partition("snap", PARTITION, bars)
    assert written == partition_digest_hex(bars)


# --- 時刻の文字列の使い回し（run の高速化の PR、人間の決定 1）-------------------------


def _reference_row(bar: Bar) -> tuple[str, ...]:
    """高速化前の `partition_row`（時刻を 1 列ごとに文字列にする）と同じ手順の参照実装。"""
    base = (
        str(bar.bar_start),
        str(bar.bar_end),
        str(bar.open.value),
        str(bar.high.value),
        str(bar.low.value),
        str(bar.close.value),
        str(bar.volume),
        str(bar.available_at),
        bar.provenance.kind.value,
        bar.provenance.source_ref,
    )
    if bar.coverage is not None:
        coverage = bar.coverage
        return base + (
            str(coverage.expected_minutes),
            str(coverage.recorded_minutes),
            str(coverage.first_observed_at),
            str(coverage.last_observed_at),
            str(coverage.time_rule_row),
        )
    if bar.minute is not None:
        return base + (
            str(bar.minute.api_time),
            str(bar.minute.time_rule_row),
            str(bar.minute.spread),
            negative_spread_text(bar.minute.negative_spread),
        )
    return base


def _reference_digest(bars: Iterable[Bar]) -> str:
    """高速化前の `partition_digest_hex` と同じ手順の参照実装。"""
    hasher = hashlib.sha256()
    for bar in sorted(bars, key=lambda item: item.bar_start.value):
        hasher.update("\x1f".join(_reference_row(bar)).encode("utf-8"))
        hasher.update(b"\x1e")
    return hasher.hexdigest()


def test_the_digest_equals_the_one_made_with_a_string_per_column(tmp_path: Path) -> None:
    """MT5 経路の全系列（被覆の属性の上位足・1 分足の属性）で、無記録の分の隙間があっても、
    時刻の文字列を使い回したダイジェストと行が、1 列ごとに文字列にした場合と同じになる。"""
    week = Interval(
        start=UtcTime.parse("2026-01-04T22:00:00Z"), end=UtcTime.parse("2026-01-09T22:00:00Z")
    )
    holes = (
        Interval(
            start=UtcTime.parse("2026-01-06T10:00:00Z"), end=UtcTime.parse("2026-01-06T10:07:00Z")
        ),
        Interval(
            start=UtcTime.parse("2026-01-07T00:00:00Z"), end=UtcTime.parse("2026-01-07T02:00:00Z")
        ),
    )
    table = m5.write_rule_files(tmp_path)
    rows = m5.minute_rows(CALENDAR, week, unrecorded=holes)
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])
    assert pending.partition_bars
    for bars in pending.partition_bars.values():
        assert partition_digest_hex(bars) == _reference_digest(bars)
        assert [partition_row(bar) for bar in bars] == [_reference_row(bar) for bar in bars]


def test_the_digest_equals_the_reference_when_publication_lags_and_bars_have_gaps() -> None:
    """公開時刻が足の終了と違う足・足の間の隙間・逆順の入力でも、参照実装と同じ値になる。"""
    bars = list(market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW))
    bars[2] = market.make_bar(
        HOURLY, bars[2].interval, available_at=bars[2].interval.end + timedelta(minutes=5)
    )
    del bars[5]
    shuffled = bars[::-1]
    assert partition_digest_hex(shuffled) == _reference_digest(shuffled)
    assert [partition_row(bar) for bar in bars] == [_reference_row(bar) for bar in bars]
