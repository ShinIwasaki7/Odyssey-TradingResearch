"""partition の読み込みで値を使い回しても、読んだ足と失敗が変わらないこと（D03 §3.3・§8）。

`ParquetSnapshotStore.read_partition` は、時刻・価格・出来高・出所を partition の中で同じ
文字列ごとに1度だけ解釈して使い回す。どれも不変の値なので、1行ずつ作り直した場合と同じ足に
なる。足の構築時の検査（D03 §3.3）と文字列の解釈の検査は従来どおり行い、最初に失敗する
行・列と、失敗の型・文言も変わらない。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.partition_digest import PARTITION_COLUMNS, partition_row
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market

HOURLY = market.series()
PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
WINDOW = Interval(
    start=UtcTime.parse("2026-01-12T22:00:00Z"), end=UtcTime.parse("2026-01-16T22:00:00Z")
)


def _reference(rows: list[tuple[str, ...]]) -> tuple[Bar, ...]:
    """使い回しをせず、1行ずつ解釈して足を作る参照実装（高速化前の読み込みと同じ手順）。"""
    bars: list[Bar] = []
    for row in rows:
        record = dict(zip(PARTITION_COLUMNS, row, strict=True))
        volume: Decimal = decimal_from_str(record["volume"])
        bars.append(
            Bar(
                series=HOURLY,
                interval=Interval(
                    start=UtcTime.parse(record["bar_start"]),
                    end=UtcTime.parse(record["bar_end"]),
                ),
                open=Price(decimal_from_str(record["open"])),
                high=Price(decimal_from_str(record["high"])),
                low=Price(decimal_from_str(record["low"])),
                close=Price(decimal_from_str(record["close"])),
                volume=volume,
                available_at=UtcTime.parse(record["available_at"]),
                provenance=Provenance(
                    kind=ProvenanceKind(record["provenance_kind"]),
                    source_ref=record["provenance_ref"],
                ),
            )
        )
    return tuple(sorted(bars, key=lambda bar: bar.bar_start.value))


def _write(root: Path, rows: list[tuple[str, ...]]) -> ParquetSnapshotStore:
    target = root / "snap" / PARTITION.directory
    target.mkdir(parents=True)
    frame = pl.DataFrame(
        {column: [row[index] for row in rows] for index, column in enumerate(PARTITION_COLUMNS)},
        schema=dict.fromkeys(PARTITION_COLUMNS, pl.String),
    )
    frame.write_parquet(target / "bars.parquet")
    return ParquetSnapshotStore(root=root)


def _rows() -> list[tuple[str, ...]]:
    bars = market.make_bars(HOURLY, market.TF_1H, market.calendar(), WINDOW)
    rows = [partition_row(bar) for bar in bars]
    # 保存順が時刻順でなくても、読み込みは時刻順に並べ直す。
    return rows[::-1]


def test_the_bars_read_equal_a_row_by_row_construction(tmp_path: Path) -> None:
    rows = _rows()
    restored = _write(tmp_path, rows).read_partition("snap", PARTITION)
    assert restored == _reference(rows)
    assert len({bar.bar_start for bar in restored}) == len(rows)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        pytest.param("low", "999", id="low-above-open"),
        pytest.param("high", "1", id="high-below-close"),
        pytest.param("available_at", "2026-01-13T00:00:00Z", id="visible-before-end"),
        pytest.param("volume", "-1", id="negative-volume"),
        pytest.param("bar_end", "2026-01-15 00:00:00Z", id="bad-time-literal"),
        pytest.param("open", "abc", id="bad-price-literal"),
        pytest.param("provenance_kind", "unknown", id="unknown-provenance"),
        pytest.param("provenance_ref", "", id="empty-provenance-ref"),
    ],
)
def test_a_broken_row_fails_exactly_like_a_row_by_row_construction(
    tmp_path: Path, column: str, value: str
) -> None:
    """壊れた行の失敗は、1行ずつ作った場合と同じ型・同じ文言になる（前の行と同じ文字列を含む）。"""
    rows = _rows()
    index = PARTITION_COLUMNS.index(column)
    broken = list(rows[3])
    broken[index] = value
    rows[3] = tuple(broken)
    with pytest.raises((KernelValueError, ValueError)) as expected:
        _reference(rows)
    with pytest.raises((KernelValueError, ValueError)) as actual:
        _write(tmp_path, rows).read_partition("snap", PARTITION)
    assert type(actual.value) is type(expected.value)
    assert str(actual.value) == str(expected.value)


def test_a_broken_bar_is_still_refused_by_the_bar_invariant(tmp_path: Path) -> None:
    rows = _rows()
    broken = list(rows[0])
    broken[PARTITION_COLUMNS.index("low")] = "999"
    rows[0] = tuple(broken)
    with pytest.raises(MarketDataValueError, match="low <= min\\(open, close\\)"):
        _write(tmp_path, rows).read_partition("snap", PARTITION)
