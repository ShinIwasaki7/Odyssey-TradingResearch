"""partition の内容ダイジェストの単体テスト（D03 §3.7.1）。

確かめること:

- 同じ足なら同じ値、渡す順序に依存しない（受入れの決定論性）。
- 足を1本でも変えれば値が変わる（差し替えを検出できる）。
- adapters が書いて返す値と、application が再計算した値が一致する（読み取り側が照合できる）。
"""

from __future__ import annotations

from pathlib import Path

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.partition_digest import (
    PARTITION_COLUMNS,
    partition_digest_hex,
    partition_row,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.snapshot import PartitionId
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
