"""取引カレンダーの単体テスト（D03 §3.4・§11）。

確かめること:

- 週の開閉が現地の日曜 17:00 から金曜 17:00 であり、土日は UTC の曜日判定ではなく
  この宣言から導かれる。
- 宣言した休場が取引セッションから取り除かれる。
- 存在すべき足の列が、休場と短縮セッションを織り込んだものになる。
"""

from __future__ import annotations

from datetime import date, time

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import ClosureRule, WeeklyMoment
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from tests.fixtures.synthetic import market

WEEK = Interval(
    start=UtcTime.parse("2026-01-12T00:00:00Z"), end=UtcTime.parse("2026-01-20T00:00:00Z")
)


# --- 週の開閉 ---------------------------------------------------------------


def test_the_market_is_open_on_a_weekday() -> None:
    assert market.calendar().is_open(UtcTime.parse("2026-01-14T12:00:00Z"))


def test_the_market_is_closed_on_saturday() -> None:
    assert not market.calendar().is_open(UtcTime.parse("2026-01-17T12:00:00Z"))


def test_the_week_opens_at_new_york_17_00_on_sunday() -> None:
    """冬時間の日曜 17:00 は 22:00Z（上位設計書 §4.3.13）。"""
    calendar = market.calendar()
    assert not calendar.is_open(UtcTime.parse("2026-01-18T21:59:59Z"))
    assert calendar.is_open(UtcTime.parse("2026-01-18T22:00:00Z"))


def test_the_week_closes_at_new_york_17_00_on_friday() -> None:
    calendar = market.calendar()
    assert calendar.is_open(UtcTime.parse("2026-01-16T21:59:59Z"))
    assert not calendar.is_open(UtcTime.parse("2026-01-16T22:00:00Z"))


def test_sessions_split_the_week_at_the_weekend() -> None:
    sessions = market.calendar().sessions(WEEK)
    assert len(sessions) == 2
    assert sessions[0].end == UtcTime.parse("2026-01-16T22:00:00Z")
    assert sessions[1].start == UtcTime.parse("2026-01-18T22:00:00Z")


# --- 宣言した休場 -----------------------------------------------------------


def test_sessions_cover_a_window_spanning_many_weeks() -> None:
    """探索範囲は窓の長さから決まる（D03 §3.4 の公開 API）。

    固定日数で探索すると、長い窓の後半にある週がまるごと抜け落ちる。30日の窓なら
    5つの週セッションが返らなければならない。
    """
    long_window = Interval(
        start=UtcTime.parse("2026-01-05T00:00:00Z"),
        end=UtcTime.parse("2026-02-04T00:00:00Z"),
    )
    sessions = market.calendar().sessions(long_window)
    assert len(sessions) == 5
    # 窓全体を覆い、隙間なく昇順に並ぶ。
    assert sessions[0].start == long_window.start
    assert sessions[-1].end == long_window.end
    for earlier, later in zip(sessions, sessions[1:], strict=False):
        assert earlier.end < later.start


def test_sessions_cover_a_window_spanning_a_full_year() -> None:
    """さらに長い窓でも欠落しない（週は年に約52回ある）。"""
    year = Interval(
        start=UtcTime.parse("2026-01-05T00:00:00Z"),
        end=UtcTime.parse("2027-01-04T00:00:00Z"),
    )
    sessions = market.calendar().sessions(year)
    assert len(sessions) >= 52


def test_is_open_still_holds_for_a_single_instant_probe() -> None:
    """1瞬間の問い合わせでも、窓の長さから決まる探索範囲が正しく働く。"""
    calendar = market.calendar()
    assert calendar.is_open(UtcTime.parse("2026-06-10T12:00:00Z"))
    assert not calendar.is_open(UtcTime.parse("2026-06-13T12:00:00Z"))


def test_a_declared_whole_day_closure_removes_that_day() -> None:
    calendar = market.calendar(
        closures=(ClosureRule(local_date=date(2026, 1, 14), covers_whole_day=True, note="休場"),)
    )
    assert not calendar.is_open(UtcTime.parse("2026-01-14T12:00:00Z"))
    assert calendar.is_open(UtcTime.parse("2026-01-13T12:00:00Z"))


def test_a_declared_partial_closure_shortens_the_session() -> None:
    calendar = market.calendar(
        closures=(market.closure(date(2026, 1, 14), time(13, 0), time(17, 0)),)
    )
    # 現地 13:00 は冬時間で 18:00Z。
    assert calendar.is_open(UtcTime.parse("2026-01-14T17:59:59Z"))
    assert not calendar.is_open(UtcTime.parse("2026-01-14T18:00:00Z"))
    assert calendar.is_open(UtcTime.parse("2026-01-14T22:00:00Z"))


def test_closures_are_normalized_into_a_canonical_order() -> None:
    """休場の宣言順は同値性に影響しない（ダイジェストが宣言順に左右されないため）。"""
    first = market.closure(date(2026, 1, 14), time(13, 0), time(17, 0))
    second = market.closure(date(2026, 1, 15), time(13, 0), time(17, 0))
    assert market.calendar(closures=(first, second)) == market.calendar(closures=(second, first))


# --- 存在すべき足 -----------------------------------------------------------


def test_expected_daily_bar_starts_exclude_the_weekend() -> None:
    starts = market.calendar().expected_bar_starts(market.TF_1D_NY17, WEEK)
    assert [str(start) for start in starts] == [
        "2026-01-12T22:00:00Z",
        "2026-01-13T22:00:00Z",
        "2026-01-14T22:00:00Z",
        "2026-01-15T22:00:00Z",
        "2026-01-18T22:00:00Z",
        "2026-01-19T22:00:00Z",
    ]


def test_expected_hourly_bar_starts_skip_a_declared_closure() -> None:
    calendar = market.calendar(
        closures=(market.closure(date(2026, 1, 14), time(13, 0), time(15, 0)),)
    )
    window = Interval(
        start=UtcTime.parse("2026-01-14T17:00:00Z"),
        end=UtcTime.parse("2026-01-14T22:00:00Z"),
    )
    starts = {str(start) for start in calendar.expected_bar_starts(market.TF_1H, window)}
    # 現地 13:00〜15:00 は 18:00Z〜20:00Z。その2本は存在しない。
    assert "2026-01-14T18:00:00Z" not in starts
    assert "2026-01-14T19:00:00Z" not in starts
    assert "2026-01-14T17:00:00Z" in starts
    assert "2026-01-14T20:00:00Z" in starts


# --- 構築時の検査 -----------------------------------------------------------


def test_a_whole_day_closure_must_not_carry_times() -> None:
    with pytest.raises(MarketDataValueError, match="must not carry start/end"):
        ClosureRule(local_date=date(2026, 1, 14), covers_whole_day=True, start=time(1, 0))


def test_a_partial_closure_requires_start_before_end() -> None:
    with pytest.raises(MarketDataValueError, match="start < end"):
        ClosureRule(local_date=date(2026, 1, 14), start=time(17, 0), end=time(13, 0))


def test_the_weekday_must_be_in_range() -> None:
    with pytest.raises(MarketDataValueError, match="0 \\(Monday\\)"):
        WeeklyMoment(weekday=7, at=time(17, 0))
