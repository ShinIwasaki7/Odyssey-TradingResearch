"""換算の経路の規則（D06 v1.20 案 §19.3〜§19.5、v1.21 案のずれの測り方）。人工の率だけを使う。

CT-8・CT-13・CT-15 と、CT-30 の規則の層（ずれも休場を除いて測る。決定 (28) (a)）と、
CT-31〜CT-33 の規則の層（取引の通貨ペアにも記録の無い分を数えない。決定 (31) (a)。v1.22 案）。
"""

from __future__ import annotations

from datetime import date, time, timedelta
from decimal import Decimal

import pytest

from odyssey_fx.backtest.domain.policies import ConversionPath, ConversionPolicy
from odyssey_fx.backtest.portfolio.conversion import (
    ConversionFailure,
    ConversionUnavailable,
    RouteHop,
    observe_path,
    require_usable,
    resolve_route,
    session_skew,
    session_staleness,
)
from odyssey_fx.common.money import CurrencyCode
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from tests.fixtures.backtest.conversion import (
    EURUSD,
    USDCHF,
    USDJPY,
    FakeMinutes,
    FakeQuotes,
    minute_rates,
    minute_series,
)
from tests.fixtures.backtest.harness import CALENDAR
from tests.fixtures.synthetic import market

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


# --- 決定 (28) (a): ずれも休場を除いて測る（v1.21 案 §19.5。CT-30 の規則の層） ------------


def _pivot(quotes: FakeQuotes, at: UtcTime) -> ConversionPath:
    return observe_path((RouteHop(USDCHF, True), RouteHop(USDJPY)), at, quotes)


def test_session_skew_equals_the_recorded_skew_inside_a_session() -> None:
    """通常: 休場をまたがない 2 本の脚では、休場を除いたずれは記録の `skew`（実際の差）と同じ。"""
    holes = (Interval(start=_at(8, 50), end=_at(9, 0)),)
    quotes = FakeQuotes(
        symbols=frozenset({USDCHF, USDJPY}),
        bars={
            USDCHF: minute_rates(USDCHF, _SPAN, "0.8", holes=holes),
            USDJPY: minute_rates(USDJPY, _SPAN, "150"),
        },
    )
    path = _pivot(quotes, _at(9, 0))
    assert path.skew == timedelta(minutes=10)
    assert session_skew(path, CALENDAR) == path.skew
    require_usable(path, _at(9, 0), POLICY, CALENDAR)


def test_session_skew_of_fewer_than_two_legs_is_zero() -> None:
    """脚が 0 本（恒等）・1 本（直接）の経路のずれは 0。"""
    quotes = FakeQuotes(
        symbols=frozenset({USDJPY}), bars={USDJPY: minute_rates(USDJPY, _SPAN, "150")}
    )
    assert session_skew(observe_path((), _at(9, 0), quotes), CALENDAR) == timedelta(0)
    assert session_skew(observe_path((RouteHop(USDJPY),), _at(9, 0), quotes), CALENDAR) == (
        timedelta(0)
    )


#: 金曜（2026-01-09）の閉場と日曜（2026-01-11）の開場（ニューヨーク 17:00 = 22:00Z）。
_FRIDAY_CLOSE = UtcTime.from_components(2026, 1, 9, 22, 0)
_SUNDAY_OPEN = UtcTime.from_components(2026, 1, 11, 22, 0)
_WEEKEND = Interval(start=_FRIDAY_CLOSE - timedelta(hours=1), end=_SUNDAY_OPEN + timedelta(hours=1))


def test_session_skew_excludes_the_weekend_close() -> None:
    """週またぎ: 金曜の閉場の直前の脚と日曜の開場の後の脚のずれは、週末の時間を数えない。"""
    sunday_hole = (Interval(start=_SUNDAY_OPEN, end=_SUNDAY_OPEN + timedelta(minutes=10)),)
    quotes = FakeQuotes(
        symbols=frozenset({USDCHF, USDJPY}),
        bars={
            USDCHF: minute_rates(USDCHF, _WEEKEND, "0.8", holes=sunday_hole),
            USDJPY: minute_rates(USDJPY, _WEEKEND, "150"),
        },
    )
    at = _SUNDAY_OPEN + timedelta(minutes=10)
    path = _pivot(quotes, at)
    assert path.observed_at == _FRIDAY_CLOSE - timedelta(minutes=1)
    # 記録のずれは実際の差（48 時間 10 分）のまま。
    assert path.skew == timedelta(hours=48, minutes=10)
    # 休場を除くと、金曜の 1 分と日曜の 9 分。
    assert session_skew(path, CALENDAR) == timedelta(minutes=10)
    assert session_staleness(path, at, CALENDAR) == timedelta(minutes=11)
    require_usable(path, at, POLICY, CALENDAR)


def test_session_skew_excludes_a_holiday_closure() -> None:
    """祝日またぎ: 平日の休場をまたぐ 2 本の脚のずれも、休場の時間を数えない。"""
    holiday = market.calendar(
        closures=(
            market.closure(date(2026, 1, 7), start=time(10, 0), end=time(16, 0), note="test"),
        )
    )
    closes = UtcTime.from_components(2026, 1, 7, 15, 0)  # NY 10:00、休場の始まり
    reopens = UtcTime.from_components(2026, 1, 7, 21, 0)  # NY 16:00、休場の終わり
    span = Interval(start=closes - timedelta(hours=1), end=reopens + timedelta(hours=1))
    hole = (Interval(start=reopens, end=reopens + timedelta(minutes=5)),)
    quotes = FakeQuotes(
        symbols=frozenset({USDCHF, USDJPY}),
        bars={
            USDCHF: minute_rates(USDCHF, span, "0.8", holes=hole, calendar=holiday),
            USDJPY: minute_rates(USDJPY, span, "150", calendar=holiday),
        },
    )
    at = reopens + timedelta(minutes=5)
    path = _pivot(quotes, at)
    assert path.skew == timedelta(hours=6, minutes=5)
    assert session_skew(path, holiday) == timedelta(minutes=5)
    require_usable(path, at, POLICY, holiday)


def test_session_skew_beyond_the_limit_is_still_rejected_across_the_weekend() -> None:
    """休場を除いても 15 分を超えるずれは SKEW_EXCEEDED。長さは休場を除いたずれ。"""
    holes = (
        Interval(start=_FRIDAY_CLOSE - timedelta(minutes=10), end=_FRIDAY_CLOSE),
        Interval(start=_SUNDAY_OPEN, end=_SUNDAY_OPEN + timedelta(minutes=10)),
    )
    quotes = FakeQuotes(
        symbols=frozenset({USDCHF, USDJPY}),
        bars={
            USDCHF: minute_rates(USDCHF, _WEEKEND, "0.8", holes=holes),
            USDJPY: minute_rates(USDJPY, _WEEKEND, "150"),
        },
    )
    path = _pivot(quotes, _SUNDAY_OPEN + timedelta(minutes=10))
    assert session_skew(path, CALENDAR) == timedelta(minutes=20)
    with pytest.raises(ConversionUnavailable) as raised:
        require_usable(path, _SUNDAY_OPEN + timedelta(minutes=10), POLICY, CALENDAR)
    assert raised.value.failure is ConversionFailure.SKEW_EXCEEDED
    assert raised.value.length == timedelta(minutes=20)
    assert raised.value.series == minute_series(USDCHF)


# --- 決定 (31) (a): 取引の通貨ペアにも記録の無い分は数えない（v1.22 案 §19.5。CT-31〜CT-33 の
# 規則の層） ---------------------------------------------------------------------------------


def _traded(
    symbol: Symbol,
    span: Interval = _SPAN,
    holes: tuple[Interval, ...] = (),
    calendar: TradingCalendar = CALENDAR,
) -> FakeMinutes:
    """取引の通貨ペアの 1 分足（`holes` の分は無記録）。"""
    return FakeMinutes(
        minute_series=minute_series(symbol),
        bars=minute_rates(symbol, span, "1.1", holes=holes, calendar=calendar),
        unrecorded=holes,
    )


def _usdjpy(
    holes: tuple[Interval, ...],
    span: Interval = _SPAN,
    calendar: TradingCalendar = CALENDAR,
) -> FakeQuotes:
    """USDJPY の 1 分足（`holes` の分は無記録）。"""
    return FakeQuotes(
        symbols=frozenset({USDJPY}),
        bars={USDJPY: minute_rates(USDJPY, span, "150", holes=holes, calendar=calendar)},
    )


def test_ct31_minutes_the_traded_pair_did_not_record_either_are_not_counted() -> None:
    """CT-31（規則の層）: 両方が 60 分無記録の間の古さは、取引の通貨ペアに記録のある分だけ。

    USDJPY の最後の観測は 9:29、9:30〜10:30 は USDJPY も EURUSD も無記録。10:30 の処理点の
    休場だけを除いた古さは 61 分だが、EURUSD に記録のある分は観測の分（9:29）の 1 分だけ。
    記録の `at − observed_at` は実際の差のまま。
    """
    hole = (Interval(start=_at(9, 30), end=_at(10, 30)),)
    path = observe_path((RouteHop(USDJPY),), _at(10, 30), _usdjpy(hole))
    traded = _traded(EURUSD, holes=hole)
    assert path.observed_at == _at(9, 29)
    assert _at(10, 30) - path.observed_at == timedelta(minutes=61)
    assert session_staleness(path, _at(10, 30), CALENDAR) == timedelta(minutes=61)
    assert session_staleness(path, _at(10, 30), CALENDAR, traded) == timedelta(minutes=1)
    require_usable(path, _at(10, 30), POLICY, CALENDAR, traded)
    with pytest.raises(ConversionUnavailable):
        require_usable(path, _at(10, 30), POLICY, CALENDAR)


def test_ct31_a_traded_pair_recording_every_minute_gives_the_same_staleness() -> None:
    """CT-31（規則の層）: 取引の通貨ペアがセッションの毎分に記録を持てば、決定 (25) (a) と同じ値。

    セッションの中・週末をまたぐ処理点・恒等換算のどれでも同じで、結果（換算できる／できない）も
    変わらない。
    """
    quotes = _usdjpy((Interval(start=_at(8, 40), end=_at(9, 0)),))
    traded = _traded(EURUSD)
    for at in (_at(8, 50), _at(9, 0), _at(9, 7)):
        path = observe_path((RouteHop(USDJPY),), at, quotes)
        assert session_staleness(path, at, CALENDAR, traded) == session_staleness(
            path, at, CALENDAR
        )
    weekend = _usdjpy((), span=_WEEKEND)
    weekend_traded = _traded(EURUSD, span=_WEEKEND)
    at = _SUNDAY_OPEN + timedelta(minutes=10)
    path = observe_path((RouteHop(USDJPY),), at, weekend)
    assert session_staleness(path, at, CALENDAR, weekend_traded) == timedelta(minutes=1)
    assert session_staleness(path, at, CALENDAR) == timedelta(minutes=1)
    friday = observe_path(
        (RouteHop(USDJPY),),
        at,
        _usdjpy((Interval(start=_SUNDAY_OPEN, end=at),), span=_WEEKEND),
    )
    assert session_staleness(friday, at, CALENDAR, weekend_traded) == timedelta(minutes=11)
    assert session_staleness(friday, at, CALENDAR) == timedelta(minutes=11)
    identity = observe_path((), at, weekend)
    assert session_staleness(identity, at, CALENDAR, weekend_traded) == timedelta(0)


def test_ct32_staleness_counts_only_minutes_the_traded_pair_recorded() -> None:
    """CT-32（規則の層）: USDJPY だけが無記録の分は今までどおり数える。15 分ちょうどは許す。

    USDJPY の最後の観測は 8:43（8:44〜9:00 が無記録）。9:00 の休場を除いた古さは 17 分。
    EURUSD が 8:50〜8:52 に無記録なら 15 分ちょうどで換算でき、8:51 だけ無記録なら 16 分で
    STALENESS_EXCEEDED。理由の長さは取引の通貨ペアに記録のある分だけを数えた古さ（仮置き C4-5）。
    """
    path = observe_path(
        (RouteHop(USDJPY),), _at(9, 0), _usdjpy((Interval(start=_at(8, 44), end=_at(9, 0)),))
    )
    assert path.observed_at == _at(8, 43)
    two = _traded(EURUSD, holes=(Interval(start=_at(8, 50), end=_at(8, 52)),))
    assert session_staleness(path, _at(9, 0), CALENDAR, two) == timedelta(minutes=15)
    require_usable(path, _at(9, 0), POLICY, CALENDAR, two)
    one = _traded(EURUSD, holes=(Interval(start=_at(8, 51), end=_at(8, 52)),))
    with pytest.raises(ConversionUnavailable) as raised:
        require_usable(path, _at(9, 0), POLICY, CALENDAR, one)
    assert raised.value.failure is ConversionFailure.STALENESS_EXCEEDED
    assert raised.value.length == timedelta(minutes=16)
    assert "traded pair recorded" in str(raised.value)


def test_ct32_the_observed_minute_itself_counts_when_the_traded_pair_recorded_it() -> None:
    """CT-32（境界）: 観測の分（`observed_at` から始まる 1 分）も、取引の通貨ペアの記録で数える。

    処理点の時刻を含む分（終わっていない分）は数えない。
    """
    path = observe_path(
        (RouteHop(USDJPY),), _at(9, 0), _usdjpy((Interval(start=_at(8, 46), end=_at(9, 0)),))
    )
    assert path.observed_at == _at(8, 45)
    assert session_staleness(path, _at(9, 0), CALENDAR, _traded(EURUSD)) == timedelta(minutes=15)
    without_observed = _traded(EURUSD, holes=(Interval(start=_at(8, 45), end=_at(8, 46)),))
    assert session_staleness(path, _at(9, 0), CALENDAR, without_observed) == timedelta(minutes=14)
    # 処理点の分（9:00〜9:01）に記録があっても、9:00 の処理点では数えない。
    assert session_staleness(path, _at(9, 0), CALENDAR, _traded(EURUSD)) == (
        session_staleness(path, _at(9, 0), CALENDAR)
    )


def test_ct32_the_same_counting_holds_across_a_closure() -> None:
    """CT-32（休場）: 休場をはさんでも、セッションの中で取引の通貨ペアの記録のある分を数える。"""
    holiday = market.calendar(
        closures=(
            market.closure(date(2026, 1, 7), start=time(10, 0), end=time(16, 0), note="test"),
        )
    )
    reopens = UtcTime.from_components(2026, 1, 7, 21, 0)  # NY 16:00、休場の終わり
    span = Interval(start=reopens - timedelta(hours=8), end=reopens + timedelta(hours=1))
    at = reopens + timedelta(minutes=5)
    quotes = _usdjpy((Interval(start=reopens, end=at),), span=span, calendar=holiday)
    path = observe_path((RouteHop(USDJPY),), at, quotes)
    assert path.observed_at == UtcTime.from_components(2026, 1, 7, 14, 59)
    assert session_staleness(path, at, holiday) == timedelta(minutes=6)
    traded = _traded(
        EURUSD,
        span=span,
        holes=(Interval(start=reopens, end=reopens + timedelta(minutes=2)),),
        calendar=holiday,
    )
    # 休場の前の観測の分（14:59）の 1 分と、休場明けで EURUSD に記録のある 21:02〜21:05 の 3 分。
    assert session_staleness(path, at, holiday, traded) == timedelta(minutes=4)


def test_ct33_the_skew_counts_only_minutes_the_traded_pair_recorded() -> None:
    """CT-33（規則の層。仮置き C4-6）: USDCHF が無記録で USDJPY だけが動く間のずれは数えない。

    USDCHF（取引の通貨ペア・1 本目の脚）が 8:30〜9:00 に無記録のとき、9:00 のずれは休場だけを
    除くと 30 分（SKEW_EXCEEDED）だが、USDCHF に記録のある分（観測の分 8:29）だけを数えると
    1 分で換算できる。USDCHF に記録があり USDJPY が 16 分無記録なら、ずれは 16 分で
    SKEW_EXCEEDED（検査の順でずれが先。長さは数えたずれ）。
    """
    hole = (Interval(start=_at(8, 30), end=_at(9, 0)),)
    quotes = FakeQuotes(
        symbols=frozenset({USDCHF, USDJPY}),
        bars={
            USDCHF: minute_rates(USDCHF, _SPAN, "0.8", holes=hole),
            USDJPY: minute_rates(USDJPY, _SPAN, "150"),
        },
    )
    path = _pivot(quotes, _at(9, 0))
    traded = FakeMinutes(
        minute_series=minute_series(USDCHF),
        bars=minute_rates(USDCHF, _SPAN, "0.8", holes=hole),
        unrecorded=hole,
    )
    assert path.skew == timedelta(minutes=30)
    assert session_skew(path, CALENDAR) == timedelta(minutes=30)
    assert session_skew(path, CALENDAR, traded) == timedelta(minutes=1)
    assert session_staleness(path, _at(9, 0), CALENDAR, traded) == timedelta(minutes=1)
    require_usable(path, _at(9, 0), POLICY, CALENDAR, traded)
    with pytest.raises(ConversionUnavailable) as plain:
        require_usable(path, _at(9, 0), POLICY, CALENDAR)
    assert plain.value.failure is ConversionFailure.SKEW_EXCEEDED

    usdjpy_hole = (Interval(start=_at(8, 44), end=_at(9, 0)),)
    moving = FakeQuotes(
        symbols=frozenset({USDCHF, USDJPY}),
        bars={
            USDCHF: minute_rates(USDCHF, _SPAN, "0.8"),
            USDJPY: minute_rates(USDJPY, _SPAN, "150", holes=usdjpy_hole),
        },
    )
    stale = _pivot(moving, _at(9, 0))
    full = FakeMinutes(minute_series=minute_series(USDCHF), bars=minute_rates(USDCHF, _SPAN, "0.8"))
    assert session_skew(stale, CALENDAR, full) == timedelta(minutes=16)
    with pytest.raises(ConversionUnavailable) as raised:
        require_usable(stale, _at(9, 0), POLICY, CALENDAR, full)
    assert raised.value.failure is ConversionFailure.SKEW_EXCEEDED
    assert raised.value.length == timedelta(minutes=16)
    assert raised.value.series == minute_series(USDJPY)
