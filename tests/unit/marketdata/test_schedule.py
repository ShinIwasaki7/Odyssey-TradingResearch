"""公開予定と遅延シナリオの単体テスト（D03 §3.5・§3.6・§11）。

確かめること:

- 負の遅延が構築時に拒否される（先読みを設定の誤りからも起こさせない）。
- 遅延を適用しても `available_at >= bar_end` が保たれる。
- seed 付き確率的遅延が能力検査で拒否される。
- 「期待される最新の確定足」がカレンダー上の最後の足になり、週末を跨いでも前営業日の足を
  正しく返す。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError, UnsupportedCapability
from odyssey_fx.marketdata.domain.schedule import (
    DelayScenario,
    FixedSeriesDelay,
    InjectedBarDelay,
    SeededRandomDelay,
    SeriesSchedule,
)
from tests.fixtures.synthetic import market

SERIES = market.series()
DAILY = market.series(timeframe_id="1d_ny17")


def _schedule(delay: timedelta = timedelta(0)) -> SeriesSchedule:
    return SeriesSchedule(
        series=SERIES,
        timeframe_def=market.TF_1H,
        calendar=market.calendar(),
        normal_publication_delay=delay,
    )


# --- 非負の遅延（D03 §3.5・§3.6）--------------------------------------------


def test_a_negative_normal_publication_delay_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="must be >= 0"):
        _schedule(delay=timedelta(seconds=-1))


def test_a_negative_fixed_series_delay_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="must be >= 0"):
        FixedSeriesDelay(series=SERIES, delay=timedelta(seconds=-1))


def test_a_negative_injected_bar_delay_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="must be >= 0"):
        InjectedBarDelay(
            series=SERIES,
            bar_start=UtcTime.parse("2026-01-14T10:00:00Z"),
            delay=timedelta(seconds=-1),
        )


def test_the_scheduled_time_defaults_to_the_bar_end() -> None:
    bar_end = UtcTime.parse("2026-01-14T11:00:00Z")
    assert _schedule().scheduled_at(bar_end) == bar_end


def test_a_delay_never_moves_the_availability_before_the_bar_end() -> None:
    schedule = _schedule()
    scenario = DelayScenario(
        id="daily_two_seconds",
        version=1,
        rules=(FixedSeriesDelay(series=SERIES, delay=timedelta(seconds=2)),),
    )
    bar_start = UtcTime.parse("2026-01-14T10:00:00Z")
    bar_end = UtcTime.parse("2026-01-14T11:00:00Z")
    available = scenario.available_at(schedule, bar_start, bar_end)
    assert available == bar_end + timedelta(seconds=2)
    assert available >= bar_end


# --- 遅延規則の適用 ---------------------------------------------------------


def test_an_injected_delay_only_affects_the_named_bar() -> None:
    target = UtcTime.parse("2026-01-14T10:00:00Z")
    scenario = DelayScenario(
        id="one_bar",
        version=1,
        rules=(InjectedBarDelay(series=SERIES, bar_start=target, delay=timedelta(minutes=5)),),
    )
    assert scenario.delay_for(SERIES, target) == timedelta(minutes=5)
    assert scenario.delay_for(SERIES, target + timedelta(hours=1)) == timedelta(0)


def test_overlapping_rules_take_the_largest_delay() -> None:
    """複数の規則が当たる足は最も遅い制約で決まる（宣言順に左右されない）。"""
    target = UtcTime.parse("2026-01-14T10:00:00Z")
    rules = (
        FixedSeriesDelay(series=SERIES, delay=timedelta(seconds=2)),
        InjectedBarDelay(series=SERIES, bar_start=target, delay=timedelta(minutes=5)),
    )
    forward = DelayScenario(id="s", version=1, rules=rules)
    backward = DelayScenario(id="s", version=1, rules=tuple(reversed(rules)))
    assert forward.delay_for(SERIES, target) == timedelta(minutes=5)
    assert backward.delay_for(SERIES, target) == timedelta(minutes=5)


def test_a_delay_for_another_series_does_not_apply() -> None:
    scenario = DelayScenario(
        id="daily_only",
        version=1,
        rules=(FixedSeriesDelay(series=DAILY, delay=timedelta(seconds=2)),),
    )
    assert scenario.delay_for(SERIES, UtcTime.parse("2026-01-14T10:00:00Z")) == timedelta(0)


# --- 能力検査（D03 §3.6）----------------------------------------------------


def test_a_seeded_random_delay_is_rejected_by_the_capability_check() -> None:
    """seed 付き確率的遅延は将来用で、初版は能力検査で拒否する（D03 §3.6）。"""
    rule = SeededRandomDelay(series=SERIES, seed=1, max_delay=timedelta(seconds=5))
    with pytest.raises(UnsupportedCapability, match="SeededRandomDelay"):
        DelayScenario(id="random", version=1, rules=(rule,))


# --- 期待される最新の確定足（D03 §3.5）-------------------------------------


def test_the_expected_latest_bar_is_the_last_completed_one() -> None:
    schedule = _schedule()
    at = UtcTime.parse("2026-01-14T12:30:00Z")
    assert schedule.expected_latest_bar_start(at) == UtcTime.parse("2026-01-14T11:00:00Z")


def test_the_expected_latest_bar_requires_the_bar_to_have_ended() -> None:
    """`bar_end <= at` の足だけが対象。進行中の足は返さない（D03 §3.5）。"""
    schedule = _schedule()
    at = UtcTime.parse("2026-01-14T12:00:00Z")
    assert schedule.expected_latest_bar_start(at) == UtcTime.parse("2026-01-14T11:00:00Z")


def test_the_expected_latest_bar_reaches_back_across_the_weekend() -> None:
    """週末明けの直後は、直近の確定足が金曜のものであることが正常（上位設計書 §4.3.10）。"""
    schedule = SeriesSchedule(
        series=DAILY, timeframe_def=market.TF_1D_NY17, calendar=market.calendar()
    )
    at = UtcTime.parse("2026-01-19T00:00:00Z")  # 月曜未明
    assert schedule.expected_latest_bar_start(at) == UtcTime.parse("2026-01-15T22:00:00Z")


def test_the_schedule_rejects_a_mismatched_timeframe_definition() -> None:
    with pytest.raises(MarketDataValueError, match="does not"):
        SeriesSchedule(series=SERIES, timeframe_def=market.TF_1D_NY17, calendar=market.calendar())
