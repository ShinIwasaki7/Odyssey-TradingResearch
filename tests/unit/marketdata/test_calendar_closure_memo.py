"""休場の UTC 区間を覚えておいても、カレンダーの答えが変わらないこと（D03 §3.4・§3.4.1）。

`TradingCalendar.sessions()` などは休場1件ごとの UTC 区間を使う。その計算（夏時間の解決を
含む）を純粋関数として覚えておく（`_closure_interval_of`）。覚えた値を使っても、毎回
`ClosureRule.utc_interval` で計算し直した場合と同じ答えになることを、3つの形の休場（短縮
セッション・終日休場・取引日単位の休場）と夏時間の切替をまたぐ窓で確かめる。
"""

from __future__ import annotations

from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import pytest

import odyssey_fx.marketdata.domain.calendar as calendar_module
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import ClosureRule, TradingCalendar
from tests.fixtures.synthetic import market

CLOSURES = (
    market.closure(date(2026, 1, 1), trading_day=True, note="元日"),
    market.closure(date(2026, 3, 9), time(10, 0), time(12, 0), note="夏時間の切替直後の短縮"),
    ClosureRule(local_date=date(2026, 11, 3), covers_whole_day=True, note="終日休場"),
    market.closure(date(2026, 12, 24), time(13, 0), time(17, 0), note="短縮"),
    market.closure(date(2026, 12, 25), trading_day=True, note="クリスマス"),
)
YEAR = Interval(
    start=UtcTime.parse("2025-12-28T22:00:00Z"), end=UtcTime.parse("2026-12-31T22:00:00Z")
)


def _uncached(tz: ZoneInfo, rule: ClosureRule, boundary: time | None) -> Interval:
    return rule.utc_interval(tz, trading_day_boundary=boundary)


def _answers(calendar: TradingCalendar) -> tuple[object, ...]:
    probes = [YEAR.start + timedelta(hours=7 * step) for step in range(1300)]
    return (
        calendar.sessions(YEAR),
        tuple(calendar.expected_bar_starts(market.TF_1H, YEAR)),
        tuple(calendar.expected_bar_starts(market.TF_1D_NY17, YEAR)),
        tuple(calendar.is_open(moment) for moment in probes),
    )


def test_remembered_closures_give_the_same_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    calendar = market.calendar(CLOSURES)
    calendar_module._closure_interval_of.cache_clear()
    cold = _answers(calendar)
    warm = _answers(calendar)
    monkeypatch.setattr(calendar_module, "_closure_interval_of", _uncached)
    reference = _answers(calendar)
    assert cold == warm == reference


def test_each_remembered_interval_is_the_declared_one() -> None:
    calendar = market.calendar(CLOSURES)
    boundary = calendar.trading_day_boundary
    for rule in calendar.closures:
        expected = rule.utc_interval(calendar.tz, trading_day_boundary=boundary)
        assert calendar_module._closure_interval_of(calendar.tz, rule, boundary) == expected
