"""取引日単位の休場のプロパティテスト（D03 §3.4.1・§11「プロパティ」v1.9）。

確かめること（どの日付に宣言しても成り立つ性質。夏時間の切替を含む 3 月・11 月の取引日を
必ず含める）:

- 閉じる区間は、その日の 17:00 NY に終わる日足（`1d_ny17`）の整列上の区間と一致する
  （取引日の境界を新しく定めず、日足と同じ定義を使っていること）。
- 1 時間足と 4 時間足で、存在すべき足の集合は「休場なしの集合からその区間の中の足を
  除いたもの」にちょうど等しい。区間の外の足（当日 17:00 以降を含む）は1本も消えず、
  切り詰められもしない。
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import example, given, settings
from hypothesis import strategies as st

from odyssey_fx.common.time import Interval
from tests.fixtures.synthetic import market

BASE = market.calendar()

_days = st.dates(min_value=date(2016, 1, 1), max_value=date(2026, 12, 31))


@given(_days)
@settings(max_examples=60, deadline=None)
@example(date(2021, 3, 14))  # 夏時間の開始を含む取引日（23 時間）
@example(date(2021, 3, 15))  # 開始直後の平日
@example(date(2021, 11, 7))  # 夏時間の終了を含む取引日（25 時間）
@example(date(2021, 11, 8))  # 終了直後の平日
@example(date(2019, 3, 11))
@example(date(2019, 11, 4))
def test_a_trading_day_closure_removes_exactly_its_daily_bar(local_day: date) -> None:
    calendar = market.calendar(closures=(market.closure(local_day, trading_day=True),))
    (rule,) = calendar.closures
    interval = rule.utc_interval(calendar.tz, trading_day_boundary=calendar.trading_day_boundary)

    assert market.TF_1D_NY17.boundaries(interval.start) == interval
    local_end = interval.end.value.astimezone(calendar.tz)
    assert (local_end.date(), local_end.hour, local_end.minute) == (local_day, 17, 0)

    window = Interval(
        start=interval.start - timedelta(days=2), end=interval.end + timedelta(days=2)
    )
    for timeframe in (market.TF_1H, market.TF_4H_NY17):
        base = set(BASE.expected_bar_starts(timeframe, window))
        with_closure = set(calendar.expected_bar_starts(timeframe, window))
        assert with_closure == {start for start in base if not interval.contains(start)}
        # 区間の外の足は、休場なしのときと同じ区間で存在すべき（切り詰めも起きない）。
        for start in with_closure:
            assert timeframe.expected_interval(calendar, start) == timeframe.expected_interval(
                BASE, start
            )
