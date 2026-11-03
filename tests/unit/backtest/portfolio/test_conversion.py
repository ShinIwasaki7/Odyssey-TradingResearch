"""換算の経路の規則（D06 v1.20 案 §19.3〜§19.5。CT-8・CT-13・CT-15）。人工の率だけを使う。"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from odyssey_fx.backtest.domain.policies import ConversionPolicy
from odyssey_fx.backtest.portfolio.conversion import (
    ConversionFailure,
    ConversionUnavailable,
    RouteHop,
    observe_path,
    require_usable,
    resolve_route,
    session_staleness,
)
from odyssey_fx.common.money import CurrencyCode
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from tests.fixtures.backtest.conversion import (
    EURUSD,
    USDCHF,
    USDJPY,
    FakeQuotes,
    minute_rates,
    minute_series,
)
from tests.fixtures.backtest.harness import CALENDAR

JPY = CurrencyCode("JPY")
USD = CurrencyCode("USD")
CHF = CurrencyCode("CHF")
POLICY = ConversionPolicy(pivot_currency=USD, max_observation_skew=timedelta(minutes=15))


def _at(hour: int, minute: int = 0) -> UtcTime:
    return UtcTime.from_components(2026, 1, 6, hour, minute)


_SPAN = Interval(start=_at(6), end=_at(13))


# --- CT-13: 経路の決定論 --------------------------------------------------------


def test_ct13_the_route_is_identity_direct_reverse_then_pivot() -> None:
    """CT-13: 同じ入力で常に同じ経路（恒等 → 直接 → 逆 → 基軸通貨経由）。"""
    assert resolve_route(JPY, JPY, frozenset(), POLICY) == ()
    assert resolve_route(USD, JPY, frozenset({USDJPY}), POLICY) == (RouteHop(USDJPY),)
    assert resolve_route(JPY, USD, frozenset({USDJPY}), POLICY) == (RouteHop(USDJPY, True),)
    chfjpy = Symbol("CHFJPY")
    assert resolve_route(CHF, JPY, frozenset({chfjpy, USDCHF, USDJPY}), POLICY) == (
        RouteHop(chfjpy),
    )
    pivot = resolve_route(CHF, JPY, frozenset({USDCHF, USDJPY}), POLICY)
    assert pivot == (RouteHop(USDCHF, True), RouteHop(USDJPY))
    # 探す集合の並びに依らない。
    assert resolve_route(CHF, JPY, frozenset({USDJPY, USDCHF, EURUSD}), POLICY) == pivot
    assert resolve_route(USD, JPY, frozenset({EURUSD}), POLICY) is None


# --- CT-8: 古さちょうど 15 分 ----------------------------------------------------


def test_ct8_staleness_of_exactly_15_minutes_is_allowed() -> None:
    """CT-8: 境界は「超えたら」。15 分ちょうどは換算でき、15 分 1 秒相当（16 分）は拒否。"""
    holes = (Interval(start=_at(8, 46), end=_at(9, 0)),)
    quotes = FakeQuotes(
        symbols=frozenset({USDJPY}), bars={USDJPY: minute_rates(USDJPY, _SPAN, "150", holes=holes)}
    )
    path = observe_path((RouteHop(USDJPY),), _at(9, 0), quotes)
    assert path.observed_at == _at(8, 45)
    assert session_staleness(path, _at(9, 0), CALENDAR) == timedelta(minutes=15)
    require_usable(path, _at(9, 0), POLICY, CALENDAR)
    later = observe_path((RouteHop(USDJPY),), _at(9, 1), quotes)
    assert later.observed_at == _at(9, 0)
    holes_16 = (Interval(start=_at(8, 45), end=_at(9, 0)),)
    stale_quotes = FakeQuotes(
        symbols=frozenset({USDJPY}),
        bars={USDJPY: minute_rates(USDJPY, _SPAN, "150", holes=holes_16)},
    )
    stale = observe_path((RouteHop(USDJPY),), _at(9, 0), stale_quotes)
    with pytest.raises(ConversionUnavailable) as raised:
        require_usable(stale, _at(9, 0), POLICY, CALENDAR)
    assert raised.value.failure is ConversionFailure.STALENESS_EXCEEDED
    assert raised.value.length == timedelta(minutes=16)


def test_the_checks_run_in_the_fixed_order_route_observation_skew_staleness() -> None:
    """仮置き C1-20: 観測が無い脚は NO_OBSERVATION、ずれは古さより先に見る。"""
    empty = FakeQuotes(symbols=frozenset({USDJPY}), bars={})
    with pytest.raises(ConversionUnavailable) as raised:
        observe_path((RouteHop(USDJPY),), _at(9, 0), empty)
    assert raised.value.failure is ConversionFailure.NO_OBSERVATION
    assert raised.value.series == minute_series(USDJPY)
    # USDCHF は 30 分古く、USDJPY は新しい: ずれ（30 分）と古さ（30 分）の両方が超えるが、ずれが先。
    holes = (Interval(start=_at(8, 30), end=_at(9, 0)),)
    quotes = FakeQuotes(
        symbols=frozenset({USDCHF, USDJPY}),
        bars={
            USDCHF: minute_rates(USDCHF, _SPAN, "0.8", holes=holes),
            USDJPY: minute_rates(USDJPY, _SPAN, "150"),
        },
    )
    path = observe_path((RouteHop(USDCHF, True), RouteHop(USDJPY)), _at(9, 0), quotes)
    with pytest.raises(ConversionUnavailable) as raised:
        require_usable(path, _at(9, 0), POLICY, CALENDAR)
    assert raised.value.failure is ConversionFailure.SKEW_EXCEEDED
    assert raised.value.series == minute_series(USDCHF)


# --- CT-15: 執行足の途中の判断時点（1 分足） ---------------------------------------


def _changing(moment: UtcTime) -> str:
    """分ごとに違う率（分の数を率に混ぜる）。"""
    return str(Decimal("150") + Decimal(moment.value.minute) / Decimal(1000))


def test_ct15_the_leg_is_the_last_minute_that_ended_by_the_processing_point() -> None:
    """CT-15: 執行足の途中の処理点でも、直前に終わった記録のある 1 分足から取る。

    15 分の境目の処理点では、15 分足の終値（その足の最後に記録のある分の終値）とその観測時刻
    （その分の開始）と同じ値になる。処理点の時刻を含む分（終わっていない分）は使わない。
    """
    minutes = minute_rates(USDJPY, _SPAN, _changing)
    quotes = FakeQuotes(symbols=frozenset({USDJPY}), bars={USDJPY: minutes})
    mid = observe_path((RouteHop(USDJPY),), _at(9, 7), quotes)
    assert mid.observed_at == _at(9, 6)
    assert mid.rate == Decimal(_changing(_at(9, 6)))
    assert session_staleness(mid, _at(9, 7), CALENDAR) == timedelta(minutes=1)
    boundary = observe_path((RouteHop(USDJPY),), _at(9, 15), quotes)
    in_bar = [bar for bar in minutes if _at(9, 0).value <= bar.bar_start.value < _at(9, 15).value]
    last = in_bar[-1]
    assert (boundary.rate, boundary.observed_at) == (last.close.value, last.bar_start)
