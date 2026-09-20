"""公開フィードの単体テスト（D03 §7・§11）。

確かめること:

- 同時刻のイベント順が `ExecutionBarComplete → Publication → ScheduledBoundary →
  ExecutionOpen` に固定されている。
- 同時刻・同段階内の系列順が `(symbol, 名目長の降順, basis)` で決まる。
- 予定時刻の通知（`ScheduledBoundary`）がデータの到着と独立に出る。
- 執行系列のイベントは執行系列にだけ出る。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.time import Interval, PhaseRank, UtcTime
from odyssey_fx.marketdata.application.publication import (
    EVENT_ORDER,
    PublicationFeed,
    PublicationKind,
    build_feed,
    build_publication_log,
)
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.schedule import (
    DelayScenario,
    FixedSeriesDelay,
    SeriesSchedule,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.synthetic import market

HOURLY = market.series()
FIFTEEN = market.series(timeframe_id="15m")
DAILY = market.series(timeframe_id="1d_ny17")
CALENDAR = market.calendar()

WINDOW = Interval(
    start=UtcTime.parse("2026-01-13T22:00:00Z"), end=UtcTime.parse("2026-01-15T22:00:00Z")
)

SCHEDULES = {
    HOURLY: SeriesSchedule(series=HOURLY, timeframe_def=market.TF_1H, calendar=CALENDAR),
    FIFTEEN: SeriesSchedule(series=FIFTEEN, timeframe_def=market.TF_15M, calendar=CALENDAR),
    DAILY: SeriesSchedule(series=DAILY, timeframe_def=market.TF_1D_NY17, calendar=CALENDAR),
}


def _bars(series: SeriesId, timeframe_def: TimeframeDefinition) -> tuple[Bar, ...]:
    return market.make_bars(series, timeframe_def, CALENDAR, WINDOW)


# --- 同時刻の順序（D03 §7.1）------------------------------------------------


def test_the_fixed_same_instant_order_is_the_one_d03_specifies() -> None:
    assert [kind.value for kind in EVENT_ORDER] == [
        "EXECUTION_BAR_COMPLETE",
        "PUBLICATION",
        "SCHEDULED_BOUNDARY",
        "EXECUTION_OPEN",
    ]


def test_events_at_the_same_instant_follow_the_fixed_order() -> None:
    """同じ時刻・同じ系列のイベントは D03 §7.1 の固定順に並ぶ。

    予定境界は公開予定を持つ全系列に出るので、順序の確認は1つの系列に絞る。
    """
    bars = {FIFTEEN: _bars(FIFTEEN, market.TF_15M)}
    feed = build_feed(
        bars, {FIFTEEN: SCHEDULES[FIFTEEN]}, WINDOW, execution_series=frozenset({FIFTEEN})
    )
    boundary = UtcTime.parse("2026-01-14T12:00:00Z")
    at_boundary = [event for event in feed if event.at == boundary]
    assert [event.kind for event in at_boundary] == [
        PublicationKind.EXECUTION_BAR_COMPLETE,
        PublicationKind.PUBLICATION,
        PublicationKind.SCHEDULED_BOUNDARY,
        PublicationKind.EXECUTION_OPEN,
    ]


def test_the_longer_timeframe_is_delivered_first_at_the_same_instant() -> None:
    """同時刻・同段階内の系列順は `(symbol, 名目長の降順, basis)`（D03 §7.1）。"""
    bars = {
        HOURLY: _bars(HOURLY, market.TF_1H),
        FIFTEEN: _bars(FIFTEEN, market.TF_15M),
    }
    feed = build_feed(bars, SCHEDULES, WINDOW)
    boundary = UtcTime.parse("2026-01-14T12:00:00Z")
    publications = [
        event
        for event in feed
        if event.at == boundary and event.kind is PublicationKind.PUBLICATION
    ]
    assert [str(event.series.timeframe.id) for event in publications] == ["1h", "15m"]


# --- 予定通知はデータ到着と独立（D03 §7.1・§7.2）---------------------------


def test_a_missing_bar_still_produces_its_scheduled_boundary() -> None:
    """欠損した足にも予定境界が出る（D03 §7.1「データ到着とは独立」）。

    これがないと、欠損した系列の評価が黙って飛ばされ、見送り・待機・過去値使用・失敗の
    区別（`on_missing`）が働かない（D03 §7.2）。
    """
    missing = UtcTime.parse("2026-01-14T10:00:00Z")
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=(missing,))
    feed = build_feed({HOURLY: bars}, {HOURLY: SCHEDULES[HOURLY]}, WINDOW)

    boundaries = {
        event.bar_key.bar_start for event in feed.of_kind(PublicationKind.SCHEDULED_BOUNDARY)
    }
    publications = {event.bar_key.bar_start for event in feed.of_kind(PublicationKind.PUBLICATION)}
    assert missing in boundaries, "存在すべき足には、データが無くても予定境界が出る"
    assert missing not in publications, "データが無い足は公開されない"


def test_the_scheduled_boundaries_cover_every_expected_bar() -> None:
    """予定境界の集合は、実行区間内で**終わる**期待足の集合と一致する。

    区間の開始より前に始まって区間内で終わる足も対象なので、期待値は区間より手前から
    数える。
    """
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    feed = build_feed({HOURLY: bars}, {HOURLY: SCHEDULES[HOURLY]}, WINDOW)
    boundaries = sorted(str(event.at) for event in feed.of_kind(PublicationKind.SCHEDULED_BOUNDARY))
    search = Interval(start=WINDOW.start - timedelta(hours=2), end=WINDOW.end)
    expected = sorted(
        str(interval.end)
        for start in CALENDAR.expected_bar_starts(market.TF_1H, search)
        if (interval := market.TF_1H.expected_interval(CALENDAR, start)) is not None
        and WINDOW.contains(interval.end)
    )
    assert boundaries == expected
    # 区間の開始ちょうどで終わる足の境界も含まれる（半開区間の下端は区間内）。
    assert str(WINDOW.start) in boundaries


def test_a_series_with_no_bars_at_all_still_gets_its_boundaries() -> None:
    """データが1本も無い系列にも、公開予定があれば予定境界が出る（D03 §7.1）。"""
    feed = build_feed({}, {HOURLY: SCHEDULES[HOURLY]}, WINDOW)
    assert feed.of_kind(PublicationKind.SCHEDULED_BOUNDARY)
    assert not feed.of_kind(PublicationKind.PUBLICATION)


def test_the_scheduled_boundary_fires_even_when_the_data_is_delayed() -> None:
    """`OnBarClose` は予定時点で起動できる（D03 §7.2）。遅延しても通知は動かない。"""
    bars = {HOURLY: _bars(HOURLY, market.TF_1H)}
    scenario = DelayScenario(
        id="hourly_delay",
        version=1,
        rules=(FixedSeriesDelay(series=HOURLY, delay=timedelta(minutes=5)),),
    )
    log = build_publication_log(bars, SCHEDULES, scenario)
    feed = build_feed(bars, SCHEDULES, WINDOW, publication_log=log)

    boundary = UtcTime.parse("2026-01-14T12:00:00Z")
    boundaries = [
        event
        for event in feed
        if event.kind is PublicationKind.SCHEDULED_BOUNDARY and event.at == boundary
    ]
    assert boundaries  # 予定時刻に出る。

    publications = [
        event
        for event in feed
        if event.kind is PublicationKind.PUBLICATION
        and event.bar_key.bar_start == UtcTime.parse("2026-01-14T11:00:00Z")
    ]
    assert len(publications) == 1
    assert publications[0].at == boundary + timedelta(minutes=5)


# --- 執行系列のイベント（D03 §7.3）------------------------------------------


def test_execution_events_are_produced_only_for_the_execution_series() -> None:
    bars = {
        HOURLY: _bars(HOURLY, market.TF_1H),
        FIFTEEN: _bars(FIFTEEN, market.TF_15M),
    }
    feed = build_feed(bars, SCHEDULES, WINDOW, execution_series=frozenset({FIFTEEN}))
    execution_kinds = {
        PublicationKind.EXECUTION_OPEN,
        PublicationKind.EXECUTION_BAR_COMPLETE,
    }
    series_with_execution_events = {event.series for event in feed if event.kind in execution_kinds}
    assert series_with_execution_events == {FIFTEEN}


def test_the_execution_open_fires_at_the_bar_start() -> None:
    bars = {FIFTEEN: _bars(FIFTEEN, market.TF_15M)}
    feed = build_feed(bars, SCHEDULES, WINDOW, execution_series=frozenset({FIFTEEN}))
    for event in feed.of_kind(PublicationKind.EXECUTION_OPEN):
        assert event.at == event.bar_key.bar_start


# --- 実現した公開記録（D03 §3.6）-------------------------------------------


def test_the_publication_log_records_the_scheduled_and_realized_times() -> None:
    bars = {HOURLY: _bars(HOURLY, market.TF_1H)}
    scenario = DelayScenario(
        id="hourly_delay",
        version=1,
        rules=(FixedSeriesDelay(series=HOURLY, delay=timedelta(seconds=90)),),
    )
    log = build_publication_log(bars, SCHEDULES, scenario)
    assert log.records
    for record in log.records:
        assert record.scheduled_at == record.bar_end
        assert record.realized_delay == timedelta(seconds=90)
        assert record.available_at >= record.bar_end


# --- フェーズ対応表（D03 §7.1）----------------------------------------------


def test_phase_ranks_must_agree_with_the_fixed_order() -> None:
    """D06 がフェーズを列挙するまでの固定順と矛盾する対応表は拒否する。"""
    with pytest.raises(MarketDataValueError, match="contradict the fixed same-instant order"):
        PublicationFeed(
            events=(),
            phase_ranks={
                PublicationKind.EXECUTION_BAR_COMPLETE: PhaseRank(5, "LATE"),
                PublicationKind.PUBLICATION: PhaseRank(1, "EARLY"),
            },
        )


def test_consistent_phase_ranks_are_accepted() -> None:
    feed = PublicationFeed(
        events=(),
        phase_ranks={
            PublicationKind.EXECUTION_BAR_COMPLETE: PhaseRank(0, "BAR_COMPLETE"),
            PublicationKind.PUBLICATION: PhaseRank(1, "PUBLISH"),
            PublicationKind.SCHEDULED_BOUNDARY: PhaseRank(2, "BOUNDARY"),
            PublicationKind.EXECUTION_OPEN: PhaseRank(3, "OPEN"),
        },
    )
    assert feed.phase_rank(PublicationKind.PUBLICATION) == PhaseRank(1, "PUBLISH")


def test_a_feed_without_phase_ranks_reports_none() -> None:
    assert PublicationFeed(events=()).phase_rank(PublicationKind.PUBLICATION) is None
