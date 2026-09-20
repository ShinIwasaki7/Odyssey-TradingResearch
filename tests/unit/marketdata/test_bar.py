"""足の不変条件の単体テスト（D03 §3.3・§11）。

確かめること:

- `available_at < bar_end`（足の終了前に確定値が見える構成）が構築時に拒否される。
- OHLC の上下関係の違反、負の出来高が拒否される。
- 短縮セッションで切り詰められた区間が受理され、整列上の区間のままの足が拒否される。
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, BarKey, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from tests.fixtures.synthetic import market

SERIES = market.series()
INTERVAL = Interval(
    start=UtcTime.parse("2026-01-14T10:00:00Z"), end=UtcTime.parse("2026-01-14T11:00:00Z")
)
PROVENANCE = Provenance(kind=ProvenanceKind.HISTDATA, source_ref="a.csv")


def _bar(**overrides: object) -> Bar:
    fields: dict[str, object] = {
        "series": SERIES,
        "interval": INTERVAL,
        "open": Price(decimal_from_str("150.0")),
        "high": Price(decimal_from_str("151.0")),
        "low": Price(decimal_from_str("149.0")),
        "close": Price(decimal_from_str("150.5")),
        "volume": decimal_from_str("1000"),
        "available_at": INTERVAL.end,
        "provenance": PROVENANCE,
    }
    fields.update(overrides)
    return Bar(**fields)  # type: ignore[arg-type]


# --- available_at の不変条件（D03 §3.3）-------------------------------------


def test_available_at_may_equal_the_bar_end() -> None:
    assert _bar(available_at=INTERVAL.end).available_at == INTERVAL.end


def test_available_at_may_be_later_than_the_bar_end() -> None:
    """遅延シナリオを適用した足は後ろへずれる（D03 §3.6）。"""
    later = INTERVAL.end + timedelta(seconds=2)
    assert _bar(available_at=later).available_at == later


def test_available_at_before_the_bar_end_is_rejected() -> None:
    """足の終了前に確定値が見える構成は構築時に拒否する（D03 §3.3・§11）。"""
    with pytest.raises(MarketDataValueError, match="available_at >= bar_end"):
        _bar(available_at=INTERVAL.end - timedelta(seconds=1))


# --- OHLC と出来高 ----------------------------------------------------------


def test_low_above_the_open_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="low <= min"):
        _bar(low=Price(decimal_from_str("150.2")))


def test_high_below_the_close_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="max\\(open, close\\) <= high"):
        _bar(high=Price(decimal_from_str("150.3")))


def test_negative_volume_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="volume must be >= 0"):
        _bar(volume=decimal_from_str("-1"))


def test_zero_volume_is_accepted() -> None:
    """HistData 由来の出来高は 0（D03 §2）。真の出来高としては解釈しないが値としては有効。"""
    assert _bar(volume=decimal_from_str("0")).volume == decimal_from_str("0")


# --- 自然キー ---------------------------------------------------------------


def test_the_natural_key_is_the_series_and_bar_start() -> None:
    bar = _bar()
    assert bar.key == BarKey(series=SERIES, bar_start=INTERVAL.start)


# --- 短縮セッションとの照合（D03 §3.3・§11）--------------------------------


def _shortened_calendar() -> object:
    """金曜（2026-01-16）の現地 13:00 以降を休場と宣言したカレンダー。"""
    return market.calendar(
        closures=(market.closure(date(2026, 1, 16), time(13, 0), time(17, 0), "短縮セッション"),)
    )


def test_a_bar_truncated_at_the_declared_closure_is_accepted() -> None:
    """短縮セッションでは期待区間が休場開始で切り詰められ、その区間の足が受理される。

    金曜の日足は整列上 22:00Z まで続くが、現地 13:00（= 18:00Z）から休場を宣言すると、
    期待区間は 18:00Z で切り詰められる。
    """
    calendar = _shortened_calendar()
    aligned = market.TF_1D_NY17.boundaries(UtcTime.parse("2026-01-16T12:00:00Z"))
    expected = market.TF_1D_NY17.expected_interval(calendar, aligned.start)  # type: ignore[arg-type]
    assert expected is not None
    assert expected.end == UtcTime.parse("2026-01-16T18:00:00Z")
    assert expected.end < aligned.end

    bar = market.make_bar(market.series(timeframe_id="1d_ny17"), expected)
    bar.validate_against(market.TF_1D_NY17, calendar)  # type: ignore[arg-type]


def test_a_bar_keeping_the_aligned_interval_across_a_closure_is_rejected() -> None:
    """休場に掛かるのに整列上の区間のままの足は拒否される（D03 §3.3・§11）。"""
    calendar = _shortened_calendar()
    aligned = market.TF_1D_NY17.boundaries(UtcTime.parse("2026-01-16T12:00:00Z"))
    bar = market.make_bar(market.series(timeframe_id="1d_ny17"), aligned)
    with pytest.raises(MarketDataValueError, match="does not match the expected interval"):
        bar.validate_against(market.TF_1D_NY17, calendar)  # type: ignore[arg-type]


def test_a_four_hour_bar_fully_inside_a_declared_closure_does_not_exist() -> None:
    """休場の時間帯に丸ごと入る 4時間足は存在しない（D03 §3.2）。"""
    calendar = _shortened_calendar()
    aligned = market.TF_4H_NY17.boundaries(UtcTime.parse("2026-01-16T19:00:00Z"))
    assert market.TF_4H_NY17.expected_interval(calendar, aligned.start) is None  # type: ignore[arg-type]


def test_a_bar_inside_a_full_closure_is_rejected() -> None:
    """区間全体が休場の足はそもそも存在しない（D03 §3.2）。"""
    calendar = market.calendar()
    # 土曜の日足は週末で丸ごと休場。
    aligned = market.TF_1D_NY17.boundaries(UtcTime.parse("2026-01-17T12:00:00Z"))
    bar = market.make_bar(market.series(timeframe_id="1d_ny17"), aligned)
    with pytest.raises(MarketDataValueError, match="no bar is expected"):
        bar.validate_against(market.TF_1D_NY17, calendar)


def test_validate_rejects_a_mismatched_timeframe_definition() -> None:
    bar = market.make_bar(SERIES, INTERVAL)
    with pytest.raises(MarketDataValueError, match="does not match"):
        bar.validate_against(market.TF_1D_NY17, market.calendar())
