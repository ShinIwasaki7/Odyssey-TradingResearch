"""as-of ビューのプロパティテスト（D03 §11）。

確かめること:

- **先読み不変**: 将来の足を追加しても、`at` 以前の as-of 結果は変わらない。判断時刻より
  後に起きたことが、過去の判断に影響しないという性質であり、バックテストの結果が未来の
  データに依存しないことの根拠になる。
- 足の可視性は `available_at <= at` と同値である。
"""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.asof import AsOfView, BarsWindow, MissingInput
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
CALENDAR = market.calendar()
PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
SCHEDULES = {HOURLY: SeriesSchedule(series=HOURLY, timeframe_def=market.TF_1H, calendar=CALENDAR)}

PAST = Interval(
    start=UtcTime.parse("2026-01-12T22:00:00Z"), end=UtcTime.parse("2026-01-14T22:00:00Z")
)
EXTENDED = Interval(
    start=UtcTime.parse("2026-01-12T22:00:00Z"), end=UtcTime.parse("2026-01-16T22:00:00Z")
)


def _view(window: Interval) -> AsOfView:
    """その窓の足を持つビュー。manifest はその足から作る（D03 §3.7.1 の照合を通すため）。"""
    partition_bars = {PARTITION: market.make_bars(HOURLY, market.TF_1H, CALENDAR, window)}
    return AsOfView(
        manifest=snapshots.approved_for(partition_bars),
        allowed_partitions=frozenset({PARTITION}),
        schedules=SCHEDULES,
        partition_bars=partition_bars,
    )


#: `PAST` の終わり以前の判断時刻。この範囲では、将来の足を足しても結果が変わってはならない。
_decision_times = st.integers(min_value=0, max_value=47).map(
    lambda hours: PAST.start + timedelta(hours=hours)
)


@given(_decision_times)
@settings(max_examples=40, deadline=None)
def test_adding_future_bars_does_not_change_the_latest_available(at: UtcTime) -> None:
    """将来の足を追加しても `at` 以前の最新足は変わらない（D03 §11 のプロパティ）。"""
    short = _view(PAST).latest_available(HOURLY, at)
    long = _view(EXTENDED).latest_available(HOURLY, at)
    if isinstance(short, MissingInput) or isinstance(long, MissingInput):
        assert isinstance(short, MissingInput) and isinstance(long, MissingInput)
        assert short.reason is long.reason
    else:
        assert short == long


@given(_decision_times, st.integers(min_value=1, max_value=6))
@settings(max_examples=40, deadline=None)
def test_adding_future_bars_does_not_change_the_history(at: UtcTime, count: int) -> None:
    """将来の足を追加しても `at` 以前の履歴窓は変わらない。"""
    window = BarsWindow(count=count)
    short = _view(PAST).history(HOURLY, window, at)
    long = _view(EXTENDED).history(HOURLY, window, at)
    if isinstance(short, MissingInput) or isinstance(long, MissingInput):
        assert isinstance(short, MissingInput) and isinstance(long, MissingInput)
        assert short.reason is long.reason
    else:
        assert short == long


@given(_decision_times)
@settings(max_examples=40, deadline=None)
def test_every_returned_bar_was_already_available(at: UtcTime) -> None:
    """返る足は必ず `available_at <= at`。未来参照は構造的に不可能（D03 §6.2）。"""
    view = _view(EXTENDED)
    latest = view.latest_available(HOURLY, at)
    if not isinstance(latest, MissingInput):
        assert latest.available_at <= at
    history = view.history(HOURLY, BarsWindow(count=3), at)
    if not isinstance(history, MissingInput):
        for bar in history:
            assert bar.available_at <= at


@given(_decision_times, st.integers(min_value=1, max_value=6))
@settings(max_examples=40, deadline=None)
def test_a_history_window_returns_exactly_the_requested_count(at: UtcTime, count: int) -> None:
    """固定本数の窓は必要本数を厳守する（上位設計書 §4.3.10）。"""
    history = _view(EXTENDED).history(HOURLY, BarsWindow(count=count), at)
    if not isinstance(history, MissingInput):
        assert len(history) == count


@given(_decision_times, st.integers(min_value=1, max_value=4))
@settings(max_examples=40, deadline=None)
def test_a_history_window_is_ordered_oldest_first_without_gaps(at: UtcTime, count: int) -> None:
    """窓内の足は古い順で、期待足の並びどおりに連続する（欠損を詰めて繰り上げない）。"""
    history = _view(EXTENDED).history(HOURLY, BarsWindow(count=count), at)
    if isinstance(history, MissingInput):
        return
    starts = [bar.bar_start for bar in history]
    assert starts == sorted(starts, key=lambda value: value.value)
    for earlier, later in zip(history, history[1:], strict=False):
        assert earlier.bar_end == later.bar_start
