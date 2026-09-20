"""受入れ手順の単体テスト（D03 §4・§11）。

確かめること:

- 原の行が、宣言した列対応どおりに足へ正規化される。
- 時刻の規約が宣言制であり、オフセットのない値・UTC 以外のオフセットが拒否される。
- 価格が文字列から Decimal に変換され、float を経由しない（ADR-0012）。
- 足がアクセス分類ごとの partition に `bar_end` 基準で分かれる。
- 未分類の警告が残る状態では確定できない。
"""

from __future__ import annotations

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.application.acceptance import (
    ColumnMapping,
    RawFile,
    classify_partitions,
    normalize_rows,
)
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES, AccessClass
from odyssey_fx.marketdata.domain.bar import Bar, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import PriceBasis
from tests.fixtures.synthetic import market

MAPPING = ColumnMapping(
    time_column="timestamp",
    open_column="open",
    high_column="high",
    low_column="low",
    close_column="close",
    volume_column="volume",
    source_column="source",
)

RAW_FILE = RawFile(
    path="data/raw/market/USDJPY_1h_merged.csv",
    sha256="b" * 64,
    symbol=Symbol("USDJPY"),
    timeframe=TimeframeRef("1h", 1),
    declared_basis=PriceBasis.BID,
)


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "timestamp": "2026-01-14 10:00:00+00:00",
        "open": "150.123",
        "high": "150.900",
        "low": "149.800",
        "close": "150.500",
        "volume": "0",
        "source": "histdata",
    }
    row.update(overrides)
    return row


def _normalize(rows: list[dict[str, str]]) -> tuple[Bar, ...]:
    return normalize_rows(RAW_FILE, rows, MAPPING, market.TF_1H, market.calendar())


# --- 列対応と正規化（D03 §4 の 2〜3）----------------------------------------


def test_a_row_becomes_a_bar_with_the_declared_columns() -> None:
    (bar,) = _normalize([_row()])
    assert bar.series == market.series()
    assert bar.interval == Interval(
        start=UtcTime.parse("2026-01-14T10:00:00Z"),
        end=UtcTime.parse("2026-01-14T11:00:00Z"),
    )
    assert bar.open.value == decimal_from_str("150.123")
    assert bar.provenance.kind is ProvenanceKind.HISTDATA
    assert bar.provenance.source_ref == RAW_FILE.path


def test_the_available_time_defaults_to_the_bar_end() -> None:
    """正規化の段階では公開遅延を適用しない（D03 §3.5・§3.6 が別に扱う）。"""
    (bar,) = _normalize([_row()])
    assert bar.available_at == bar.bar_end


def test_prices_keep_their_exact_decimal_representation() -> None:
    """文字列から Decimal を作るので、二進浮動小数の誤差が入らない（ADR-0012）。"""
    (bar,) = _normalize([_row(open="150.1", close="150.3")])
    assert bar.open.value == decimal_from_str("150.1")
    assert bar.close.value == decimal_from_str("150.3")


def test_a_missing_declared_column_is_rejected() -> None:
    row = _row()
    del row["close"]
    with pytest.raises(MarketDataValueError, match="declared column"):
        _normalize([row])


# --- 時刻の規約（D03 §4 の 2〜3、上位設計書 §3.2）---------------------------


def test_a_timestamp_without_an_offset_is_rejected() -> None:
    """時刻の見た目から UTC を補わない。オフセットがなければ拒否する。"""
    with pytest.raises(KernelValueError, match="invalid UtcTime literal"):
        _normalize([_row(timestamp="2026-01-14 10:00:00")])


def test_a_timestamp_with_a_foreign_offset_is_rejected() -> None:
    with pytest.raises(KernelValueError, match="invalid UtcTime literal"):
        _normalize([_row(timestamp="2026-01-14 10:00:00+09:00")])


def test_an_unsupported_time_convention_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="unsupported time convention"):
        ColumnMapping(
            time_column="t",
            open_column="o",
            high_column="h",
            low_column="l",
            close_column="c",
            volume_column="v",
            source_column="s",
            time_convention="guess_from_values",
        )


# --- 出所（D03 §3.3）--------------------------------------------------------


def test_an_unknown_source_value_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="unknown source value"):
        _normalize([_row(source="tradingview")])


def test_a_raw_file_may_not_declare_the_aggregated_provenance() -> None:
    """`aggregated` は本基盤が生成した上位足のための出所（D03 §5）。原データは名乗れない。"""
    with pytest.raises(MarketDataValueError, match="must not declare"):
        _normalize([_row(source="aggregated")])


# --- partition の分類（D03 §3.8・§4 の 7）-----------------------------------


def test_bars_are_grouped_by_their_bar_end_access_class() -> None:
    daily_series = market.series(timeframe_id="1d_ny17")
    calendar = market.calendar()
    # 2023-12-31 22:00Z に始まり 2024-01-01 22:00Z に終わる日足。
    spanning = market.TF_1D_NY17.expected_interval(calendar, UtcTime.parse("2023-12-31T23:00:00Z"))
    assert spanning is not None
    assert spanning.start == UtcTime.parse("2023-12-31T22:00:00Z")
    assert spanning.end == UtcTime.parse("2024-01-01T22:00:00Z")

    earlier = market.TF_1D_NY17.expected_interval(calendar, UtcTime.parse("2023-12-29T00:00:00Z"))
    assert earlier is not None

    grouped = classify_partitions(
        [
            market.make_bar(daily_series, earlier),
            market.make_bar(daily_series, spanning),
        ],
        INITIAL_ACCESS_BOUNDARIES,
    )
    assert len(grouped[AccessClass.RESEARCH_HISTORY]) == 1
    assert len(grouped[AccessClass.LEGACY_HOLDOUT]) == 1
    assert grouped[AccessClass.LEGACY_HOLDOUT][0].interval == spanning
