"""取得計画の規則（D03 §14.4・§14.10）。

- 対象足は「存在すべき足の欠落」をデータ欠損と分類した、原系列の、研究履歴区分の足。
- 指定したカレンダーで休場になる足は対象にしない。期待区間が整列上の区間と食い違えば止める。
- 照合用の時間は、照合できる足の無い塊にだけ、直前 24 時間以内の「15分足 4 本と 1時間足 1 本が
  そろう最も近い時間」を足す。無ければ足さない（未照合）。
- 対象足が 0 本の計画は作らない。承認前の snapshot は入力にできない。
- 同じ入力から同じ `plan_id`（入力の列挙順に依存しない）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, time, timedelta

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.refill_plan import (
    RawBarIndex,
    build_plan,
    derive_target_bars,
    hour_chunks,
    reference_hour_for,
)
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES, AccessBoundaries
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.classification import (
    ClassificationOutcome,
    ResolvedClassification,
)
from odyssey_fx.marketdata.domain.errors import (
    MarketDataValueError,
    RefillPlanEmpty,
    SnapshotNotApproved,
)
from odyssey_fx.marketdata.domain.refill import HourKey, RefillFilter, RefillPlan
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.refill import (
    HOUR_00,
    HOUR_01,
    USDJPY_1H,
    USDJPY_15M,
    calendar_ref,
    gap_resolutions,
    manifest_for,
    provider_ref,
    raw_bars,
)
from tests.fixtures.synthetic import market

H = timedelta(hours=1)


def _plan(
    *,
    resolved: Sequence[ResolvedClassification] | None = None,
    raw: Mapping[SeriesId, Sequence[Bar]] | None = None,
    refill_filter: RefillFilter | None = None,
    calendar: TradingCalendar | None = None,
    boundaries: AccessBoundaries = INITIAL_ACCESS_BOUNDARIES,
    approved: bool = True,
) -> RefillPlan:
    trading = market.calendar() if calendar is None else calendar
    return build_plan(
        manifest=manifest_for(
            gap_resolutions() if resolved is None else resolved, approved=approved
        ),
        raw_bars=raw_bars() if raw is None else raw,
        calendar=trading,
        calendar_ref=calendar_ref(trading),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=boundaries,
        provider=provider_ref(),
        refill_filter=RefillFilter() if refill_filter is None else refill_filter,
    )


def test_targets_are_the_data_gaps_of_original_series() -> None:
    targets = derive_target_bars(
        manifest_for(gap_resolutions()),
        market.calendar(),
        market.TIMEFRAME_DEFS,
        INITIAL_ACCESS_BOUNDARIES,
        RefillFilter(),
    )
    assert [(str(bar.series), str(bar.start)) for bar in targets] == [
        ("USDJPY/15m/bid", "2020-11-30T01:00:00Z"),
        ("USDJPY/15m/bid", "2020-11-30T01:15:00Z"),
        ("USDJPY/15m/bid", "2020-11-30T01:30:00Z"),
        ("USDJPY/15m/bid", "2020-11-30T01:45:00Z"),
        ("USDJPY/1h/bid", "2020-11-30T01:00:00Z"),
    ]


def test_a_closure_classification_is_not_a_target() -> None:
    resolved = gap_resolutions(outcome=ClassificationOutcome.CLOSURE)
    with pytest.raises(RefillPlanEmpty):
        _plan(resolved=resolved)


def test_a_bar_closed_in_the_given_calendar_is_not_a_target() -> None:
    # 2020-11-29（日）の 20 時（NY）から翌 01 時（NY）を休場とすると、UTC 01 時台は休場になる。
    closed = market.calendar(
        closures=(market.closure(date(2020, 11, 29), time(19, 0), time(21, 0)),), version=2
    )
    with pytest.raises(RefillPlanEmpty):
        _plan(calendar=closed)


def test_a_bar_cut_short_by_a_closure_stops_the_plan() -> None:
    # NY 20:30 から休場にすると、UTC 01:00 の 1時間足が 01:30 で切り詰められる。
    cut = market.calendar(
        closures=(market.closure(date(2020, 11, 29), time(20, 30), time(21, 0)),), version=2
    )
    with pytest.raises(MarketDataValueError, match="not handled silently"):
        _plan(calendar=cut)


def test_bars_outside_the_research_history_are_not_targets() -> None:
    early = AccessBoundaries(
        research_until=UtcTime.parse("2020-11-30T01:00:00Z"),
        holdout_until=UtcTime.parse("2026-01-01T00:00:00Z"),
    )
    with pytest.raises(RefillPlanEmpty):
        _plan(boundaries=early)


def test_the_filter_narrows_the_targets() -> None:
    with pytest.raises(RefillPlanEmpty):
        _plan(refill_filter=RefillFilter(symbols=(market.EURUSD,)))
    narrow = _plan(
        refill_filter=RefillFilter(
            interval=Interval(start=HOUR_01, end=HOUR_01 + timedelta(minutes=30))
        )
    )
    assert len(narrow.target_bars) == 2


def test_an_unapproved_snapshot_is_not_an_input() -> None:
    with pytest.raises(SnapshotNotApproved):
        _plan(approved=False)


def test_a_chunk_without_reconcilable_bars_gets_the_nearest_complete_hour() -> None:
    plan = _plan()
    hours = [(str(hour.hour.start), hour.reference) for hour in plan.hours]
    assert hours == [("2020-11-30T00:00:00Z", True), ("2020-11-30T01:00:00Z", False)]


def test_a_chunk_with_reconcilable_bars_needs_no_reference_hour() -> None:
    # 01 時台の 1時間足だけを欠かせる（15分足は原データにある）。
    resolved = tuple(item for item in gap_resolutions() if item.series_id == USDJPY_1H)
    bars = raw_bars(drop_hours=())
    bars[USDJPY_1H] = tuple(bar for bar in bars[USDJPY_1H] if bar.bar_start != HOUR_01)
    plan = _plan(resolved=resolved, raw=bars)
    assert [hour.reference for hour in plan.hours] == [False]


def test_no_reference_hour_further_than_24_hours() -> None:
    raw = raw_bars()
    index = RawBarIndex.build(raw, INITIAL_ACCESS_BOUNDARIES)
    originals = (USDJPY_15M, USDJPY_1H)
    assert reference_hour_for(market.USDJPY, (HOUR_01,), originals, index) == HOUR_00
    # 塊の直前 24 時間に完全な時間が無ければ見つからない（未照合）。
    far = HOUR_01 + H * 40
    assert reference_hour_for(market.USDJPY, (far,), originals, index) is None


def test_hour_chunks_split_on_gaps() -> None:
    chunks = hour_chunks([HOUR_01 + H, HOUR_00, HOUR_01, HOUR_01 + H * 5])
    assert chunks == ((HOUR_00, HOUR_01, HOUR_01 + H), (HOUR_01 + H * 5,))


def test_the_plan_id_is_deterministic_and_order_free() -> None:
    first = _plan()
    second = _plan(resolved=tuple(reversed(gap_resolutions())))
    assert first.plan_id() == second.plan_id()


def test_the_plan_id_reflects_the_calendar() -> None:
    first = _plan()
    other = market.calendar(version=3)
    second = _plan(calendar=other)
    assert first.plan_id() != second.plan_id()


def test_hours_of_the_plan_are_sorted_by_symbol_and_time() -> None:
    plan = _plan()
    keys = [hour.hour for hour in plan.hours]
    assert keys == sorted(keys, key=HourKey.sort_key)
