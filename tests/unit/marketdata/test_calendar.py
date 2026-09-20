"""取引カレンダーの単体テスト（D03 §3.4・§11）。

確かめること:

- 週の開閉が現地の日曜 17:00 から金曜 17:00 であり、土日は UTC の曜日判定ではなく
  この宣言から導かれる。
- 宣言した休場が取引セッションから取り除かれる。
- 存在すべき足の列が、休場と短縮セッションを織り込んだものになる。
"""

from __future__ import annotations

import dataclasses
from datetime import date, time
from typing import Any

import pytest

from odyssey_fx.common import canonical
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import (
    ClosureRule,
    TradingCalendar,
    WeeklyMoment,
)
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


# --- 週の開場区間の覚え書き（受入れのホットパス最適化）-----------------------
#
# 受入れは「その時刻を含む足が存在すべきか」を足ごとに問うので、週の開場区間の計算が
# 数百万回走る。計算は現地時刻から UTC への変換（夏時間の解決を含む）を伴って重いため、
# 週の開始日を鍵に覚えている。覚え書きは**計算結果を変えない**ことが条件なので、
# その点をここで確かめる。


def test_the_weekly_session_cache_is_not_part_of_the_calendar_value() -> None:
    """覚え書きの有無で同値性・ハッシュ・表示が変わらない。

    取引カレンダーは snapshot の識別子の一部（版として）であり、比較・ハッシュの対象に
    なる。覚え書きが値の一部になると、「同じ宣言なのに問い合わせたかどうかで別物」に
    なってしまう。
    """
    warmed = market.calendar()
    warmed.sessions(WEEK)  # 覚え書きを埋める
    fresh = market.calendar()

    assert warmed == fresh
    assert hash(warmed) == hash(fresh)
    assert repr(warmed) == repr(fresh)


def test_the_calendar_has_no_cache_field() -> None:
    """覚え書きはカレンダーの**フィールドではない**（D02 §3 の不変型、D02 §9.3）。

    正規化エンコードは dataclass の全フィールドを符号化するので、覚え書きをフィールドに
    すると正規形が「問い合わせ済みかどうか」で変わる。宣言したフィールドだけを持つことを
    直接確かめる。
    """
    names = [field.name for field in dataclasses.fields(TradingCalendar)]
    assert names == ["id", "version", "tz", "weekly_open", "weekly_close", "closures"]


def test_the_calendar_rejects_an_extra_constructor_argument() -> None:
    """覚え書きを外から注入できない。

    覚え書きをフィールドに持たせると、細工した中身を構築時に渡してセッションの計算結果を
    変えられてしまう（実際に、週の開場区間を差し替えると、平日の昼が「休場」と判定
    できた）。フィールドが無ければ、その経路は構造的に存在しない。

    構築子を型の付かない呼び出し可能オブジェクトとして呼ぶのは、型検査で止まる書き方でも
    **実行時に必ず拒否される**ことを確かめるためである。型検査を抑制するコメントは使わない。
    """
    construct: Any = TradingCalendar
    common = {
        "id": "fx_ny17",
        "version": 1,
        "tz": market.NEW_YORK,
        "weekly_open": WeeklyMoment(weekday=6, at=time(17, 0)),
        "weekly_close": WeeklyMoment(weekday=4, at=time(17, 0)),
        "closures": (),
    }
    # 宣言どおりの引数だけなら構築できる（この検査自体が空虚でないことの確認）。
    assert isinstance(construct(**common), TradingCalendar)

    with pytest.raises(TypeError):
        construct(**common, _session_cache={})


def test_the_canonical_form_does_not_depend_on_past_queries() -> None:
    """正規化エンコードの対象が、セッション計算の前後で変わらない（D02 §9.3）。

    取引カレンダーは時間帯（`ZoneInfo`）を持つため、そのままでは正規化エンコードに
    掛けられない。ここで確かめたいのは「符号化の対象になるフィールドの値が、問い合わせを
    したかどうかで変わらない」ことなので、時間帯を名前に置き換えた写像を符号化して
    突き合わせる。覚え書きがフィールドにあった頃は、この写像に現地日付を鍵とする辞書が
    現れて符号化が失敗し、かつ状態によって内容が変わっていた。
    """

    def encodable(calendar: TradingCalendar) -> dict[str, object]:
        """符号化できる素の値だけに写す（時間帯と現地時刻は文字列にする）。"""
        payload: dict[str, object] = {}
        for field in dataclasses.fields(calendar):
            value = getattr(calendar, field.name)
            if field.name == "tz":
                payload[field.name] = value.key
            elif field.name in ("weekly_open", "weekly_close"):
                payload[field.name] = f"{value.weekday}@{value.at.isoformat()}"
            elif field.name == "closures":
                payload[field.name] = tuple("/".join(rule.sort_key()) for rule in value)
            else:
                payload[field.name] = value
        return payload

    warmed = market.calendar()
    warmed.sessions(WEEK)
    warmed.expected_bar_starts(market.TF_1H, WEEK)

    assert canonical.encode(encodable(warmed)) == canonical.encode(encodable(market.calendar()))


def test_repeated_queries_return_the_same_sessions() -> None:
    """同じ問い合わせを繰り返しても結果が変わらない（覚え書きが答えを汚さない）。"""
    calendar = market.calendar()
    assert calendar.sessions(WEEK) == calendar.sessions(WEEK) == market.calendar().sessions(WEEK)


@pytest.mark.parametrize(
    "window",
    [
        # 夏時間の切替を含む週（春の切り替え・秋の切り戻し）。現地時刻の解決が
        # 特殊な経路を通るので、覚え書きの有無で差が出るならここに出る。
        Interval(
            start=UtcTime.parse("2026-03-06T00:00:00Z"),
            end=UtcTime.parse("2026-03-13T00:00:00Z"),
        ),
        Interval(
            start=UtcTime.parse("2025-10-29T00:00:00Z"),
            end=UtcTime.parse("2025-11-05T00:00:00Z"),
        ),
        # 複数週にまたがる長い窓（探索範囲の詰め方を変えたので、端の週も拾えること）。
        Interval(
            start=UtcTime.parse("2026-01-01T00:00:00Z"),
            end=UtcTime.parse("2026-02-15T00:00:00Z"),
        ),
        # 1日に満たない短い窓。
        Interval(
            start=UtcTime.parse("2026-01-14T09:00:00Z"),
            end=UtcTime.parse("2026-01-14T10:00:00Z"),
        ),
    ],
)
def test_the_cache_does_not_change_the_sessions(window: Interval) -> None:
    """覚え書きを持たない状態と、埋めた状態で、同じ区間が返る。

    最適化の前後で意味論が変わらないことの直接の確認である。別の窓で覚え書きを埋めてから
    問い合わせても、窓ごとに計算した場合と同じ結果でなければならない。
    """
    cold = market.calendar().sessions(window)

    warmed = market.calendar()
    warmed.sessions(WEEK)  # 別の窓で先に覚えさせる
    warmed.sessions(
        Interval(
            start=UtcTime.parse("2020-01-01T00:00:00Z"),
            end=UtcTime.parse("2020-03-01T00:00:00Z"),
        )
    )

    assert warmed.sessions(window) == cold


def test_the_cache_does_not_change_the_expected_bar_starts() -> None:
    """存在すべき足の列も、覚え書きの有無で変わらない（D03 §3.4）。"""
    window = Interval(
        start=UtcTime.parse("2026-03-06T00:00:00Z"), end=UtcTime.parse("2026-03-13T00:00:00Z")
    )
    cold = market.calendar().expected_bar_starts(market.TF_1H, window)

    warmed = market.calendar()
    warmed.sessions(WEEK)
    assert warmed.expected_bar_starts(market.TF_1H, window) == cold


def test_a_declared_closure_still_applies_after_the_cache_is_warm() -> None:
    """覚え書きは週の開場区間だけで、宣言した休場はそこから別に取り除かれる。

    休場まで覚えてしまうと、休場を含む週の問い合わせが「休場前の答え」を返しうる。
    """
    closed_day = date(2026, 1, 14)
    calendar = market.calendar(
        closures=[market.closure(closed_day, time(9, 0), time(12, 0), note="短縮")]
    )
    calendar.sessions(WEEK)  # 先に覚えさせる

    assert not calendar.is_open(UtcTime.parse("2026-01-14T15:00:00Z"))
    assert calendar.is_open(UtcTime.parse("2026-01-14T18:00:00Z"))
