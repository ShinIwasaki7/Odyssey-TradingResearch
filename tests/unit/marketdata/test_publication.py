"""公開フィードの単体テスト（D03 §7・§11）。

確かめること:

- 同時刻のイベント順が `ExecutionBarComplete → Publication → ScheduledBoundary →
  ExecutionOpen` に固定されている。
- 同時刻・同段階内の系列順が `(symbol, 名目長の降順, basis)` で決まる。
- 予定時刻の通知（`ScheduledBoundary`）がデータの到着と独立に出る。
- 執行系列のイベントは執行系列にだけ出る。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import (
    HoldoutAccessViolation,
    MarketDataValueError,
    PartitionContentMismatch,
    SnapshotNotApproved,
)
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.schedule import (
    DelayScenario,
    FixedSeriesDelay,
    SeriesSchedule,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.synthetic import market, snapshots

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


HOURLY_PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
FIFTEEN_PARTITION = PartitionId(series=FIFTEEN, access_class=AccessClass.RESEARCH_HISTORY)
DAILY_PARTITION = PartitionId(series=DAILY, access_class=AccessClass.RESEARCH_HISTORY)

PARTITION_OF = {HOURLY: HOURLY_PARTITION, FIFTEEN: FIFTEEN_PARTITION, DAILY: DAILY_PARTITION}


def _bars(series: SeriesId, timeframe_def: TimeframeDefinition) -> tuple[Bar, ...]:
    return market.make_bars(series, timeframe_def, CALENDAR, WINDOW)


def _context(
    bars_by_series: Mapping[SeriesId, Sequence[Bar]],
) -> tuple[ReadableSnapshot, frozenset[PartitionId], dict[PartitionId, Sequence[Bar]]]:
    """承認済み snapshot・許可 partition・partition ごとの足を組み立てる。

    公開フィードは as-of ビューと同じ関門を通るので（D03 §3.7.1・§6.1）、テストも同じ形で
    入力を渡す。
    """
    partition_bars = {PARTITION_OF[series]: bars for series, bars in bars_by_series.items()}
    allowed = frozenset(partition_bars)
    return snapshots.readable_for(partition_bars), allowed, partition_bars


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
    manifest, allowed, partition_bars = _context(bars)
    feed = build_feed(
        manifest,
        allowed,
        partition_bars,
        {FIFTEEN: SCHEDULES[FIFTEEN]},
        WINDOW,
        execution_series=frozenset({FIFTEEN}),
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
    manifest, allowed, partition_bars = _context(bars)
    feed = build_feed(manifest, allowed, partition_bars, SCHEDULES, WINDOW)
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
    manifest, allowed, partition_bars = _context({HOURLY: bars})
    feed = build_feed(manifest, allowed, partition_bars, {HOURLY: SCHEDULES[HOURLY]}, WINDOW)

    boundaries = {
        event.bar_key.bar_start for event in feed.of_kind(PublicationKind.SCHEDULED_BOUNDARY)
    }
    publications = {event.bar_key.bar_start for event in feed.of_kind(PublicationKind.PUBLICATION)}
    assert missing in boundaries, "存在すべき足には、データが無くても予定境界が出る"
    assert missing not in publications, "データが無い足は公開されない"


def test_the_scheduled_boundaries_cover_every_expected_bar() -> None:
    """予定境界の集合は、実行区間の中で**終わる**期待足の集合と一致する。

    区間の開始より前に始まって区間内で終わる足も対象なので、期待値は区間より手前から
    数える。**区間の終端でちょうど終わる足も対象**である（D06 §10.1 の手順1 が「run_end
    で終了する足までの内部約定・口座更新を解決する」と定めており、その判断時点が起きない
    と末尾処理が1つ手前の足の値で行われる）。
    """
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest, allowed, partition_bars = _context({HOURLY: bars})
    feed = build_feed(manifest, allowed, partition_bars, {HOURLY: SCHEDULES[HOURLY]}, WINDOW)
    boundaries = sorted(str(event.at) for event in feed.of_kind(PublicationKind.SCHEDULED_BOUNDARY))
    search = Interval(start=WINDOW.start - timedelta(hours=2), end=WINDOW.end)
    expected = sorted(
        str(interval.end)
        for start in CALENDAR.expected_bar_starts(market.TF_1H, search)
        if (interval := market.TF_1H.expected_interval(CALENDAR, start)) is not None
        and WINDOW.start <= interval.end <= WINDOW.end
    )
    assert boundaries == expected
    # 区間の開始ちょうどで終わる足の境界も含まれる（半開区間の下端は区間内）。
    assert str(WINDOW.start) in boundaries
    # 区間の終端でちょうど終わる足の境界も含まれる（D06 §10.1 の手順1）。
    assert str(WINDOW.end) in boundaries


def test_the_bar_that_ends_at_the_run_end_is_completed_but_no_open_starts_there() -> None:
    """終端でちょうど終わる足は完了イベントを出し、終端から始まる足は始値を出さない。

    D06 §10.1 は手順1 で「run_end で終了する足までの内部約定・口座更新を解決する」、
    手順5 で「run_end から始まる足の始値処理（rank 11）は行わない」と定めている。判定を
    1つの述語で済ませると、どちらか一方が必ず設計と食い違う。
    """
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest, allowed, partition_bars = _context({HOURLY: bars})
    feed = build_feed(
        manifest,
        allowed,
        partition_bars,
        {HOURLY: SCHEDULES[HOURLY]},
        WINDOW,
        execution_series=frozenset({HOURLY}),
    )
    completions = {str(event.at) for event in feed.of_kind(PublicationKind.EXECUTION_BAR_COMPLETE)}
    opens = {str(event.at) for event in feed.of_kind(PublicationKind.EXECUTION_OPEN)}
    assert str(WINDOW.end) in completions
    assert str(WINDOW.end) not in opens


def test_a_series_whose_bars_are_all_missing_still_gets_its_boundaries() -> None:
    """データが届いていない系列にも、許可期間の内側なら予定境界が出る（D03 §7.1）。

    許可 partition の区間が実行区間を覆っている必要はある（D03 §6.1）ので、記録は本来の
    期間で作り、足だけをすべて落とす——公開が1件も起きない状態を作る。
    """
    bars = _bars(HOURLY, market.TF_1H)
    manifest, allowed, _ = _context({HOURLY: bars})
    # 足が1本も届いていない状態。manifest の記録は残るが、公開する足はない。
    empty: dict[PartitionId, Sequence[Bar]] = {HOURLY_PARTITION: ()}
    with pytest.raises(PartitionContentMismatch):
        # 記録と食い違う足は渡せない（D03 §3.7.1）。欠損は「足が届かない」ことであって、
        # 記録そのものを書き換えることではない。
        build_feed(manifest, allowed, empty, {HOURLY: SCHEDULES[HOURLY]}, WINDOW)

    # 実際の欠損は、記録どおりの足のうち一部が届かない形で起きる。
    missing = UtcTime.parse("2026-01-14T10:00:00Z")
    partial_manifest, partial_allowed, partial_bars = _context(
        {HOURLY: market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=(missing,))}
    )
    feed = build_feed(
        partial_manifest, partial_allowed, partial_bars, {HOURLY: SCHEDULES[HOURLY]}, WINDOW
    )
    boundaries = {
        event.bar_key.bar_start for event in feed.of_kind(PublicationKind.SCHEDULED_BOUNDARY)
    }
    assert missing in boundaries


def test_the_scheduled_boundary_fires_even_when_the_data_is_delayed() -> None:
    """`OnBarClose` は予定時点で起動できる（D03 §7.2）。遅延しても通知は動かない。"""
    bars = {HOURLY: _bars(HOURLY, market.TF_1H)}
    scenario = DelayScenario(
        id="hourly_delay",
        version=1,
        rules=(FixedSeriesDelay(series=HOURLY, delay=timedelta(minutes=5)),),
    )
    manifest, allowed, partition_bars = _context(bars)
    log = build_publication_log(manifest, allowed, partition_bars, SCHEDULES, scenario)
    feed = build_feed(manifest, allowed, partition_bars, SCHEDULES, WINDOW, publication_log=log)

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
    manifest, allowed, partition_bars = _context(bars)
    feed = build_feed(
        manifest,
        allowed,
        partition_bars,
        SCHEDULES,
        WINDOW,
        execution_series=frozenset({FIFTEEN}),
    )
    execution_kinds = {
        PublicationKind.EXECUTION_OPEN,
        PublicationKind.EXECUTION_BAR_COMPLETE,
    }
    series_with_execution_events = {event.series for event in feed if event.kind in execution_kinds}
    assert series_with_execution_events == {FIFTEEN}


def test_the_execution_open_fires_at_the_bar_start() -> None:
    bars = {FIFTEEN: _bars(FIFTEEN, market.TF_15M)}
    manifest, allowed, partition_bars = _context(bars)
    feed = build_feed(
        manifest,
        allowed,
        partition_bars,
        SCHEDULES,
        WINDOW,
        execution_series=frozenset({FIFTEEN}),
    )
    for event in feed.of_kind(PublicationKind.EXECUTION_OPEN):
        assert event.at == event.bar_key.bar_start


def test_a_delay_on_the_execution_series_moves_only_its_publications() -> None:
    """執行系列に遅延を当てても、執行のイベントの時刻は変わらない（D07 §18.3）。

    遅延は戦略向けの公開時刻だけを動かす。執行用データの可用性と戦略向けの公開遅延は
    別のものであり（上位設計書 §4.7.12、D06 §6.4）、足の始値・足の完了は通常の時刻に出る。
    """
    bars = {FIFTEEN: _bars(FIFTEEN, market.TF_15M)}
    manifest, allowed, partition_bars = _context(bars)
    scenario = DelayScenario(
        id="m15_delay",
        version=1,
        rules=(FixedSeriesDelay(series=FIFTEEN, delay=timedelta(seconds=2)),),
    )
    log = build_publication_log(manifest, allowed, partition_bars, SCHEDULES, scenario)
    plain = build_feed(
        manifest, allowed, partition_bars, SCHEDULES, WINDOW, execution_series=frozenset({FIFTEEN})
    )
    delayed = build_feed(
        manifest,
        allowed,
        partition_bars,
        SCHEDULES,
        WINDOW,
        execution_series=frozenset({FIFTEEN}),
        publication_log=log,
    )

    def times(feed: PublicationFeed, kind: PublicationKind) -> list[tuple[str, UtcTime]]:
        return [(str(event.bar_key.bar_start), event.at) for event in feed.of_kind(kind)]

    for kind in (PublicationKind.EXECUTION_OPEN, PublicationKind.EXECUTION_BAR_COMPLETE):
        assert times(delayed, kind) == times(plain, kind)
        assert times(plain, kind)
    plain_publications = dict(times(plain, PublicationKind.PUBLICATION))
    delayed_publications = dict(times(delayed, PublicationKind.PUBLICATION))
    shared = sorted(set(plain_publications) & set(delayed_publications))
    assert shared
    for key in shared:
        assert delayed_publications[key] == plain_publications[key] + timedelta(seconds=2)


# --- 実現した公開記録（D03 §3.6）-------------------------------------------


def test_the_publication_log_records_the_scheduled_and_realized_times() -> None:
    bars = {HOURLY: _bars(HOURLY, market.TF_1H)}
    scenario = DelayScenario(
        id="hourly_delay",
        version=1,
        rules=(FixedSeriesDelay(series=HOURLY, delay=timedelta(seconds=90)),),
    )
    manifest, allowed, partition_bars = _context(bars)
    log = build_publication_log(manifest, allowed, partition_bars, SCHEDULES, scenario)
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


# --- snapshot の関門（D03 §3.7.1 の3・§6.1）--------------------------------


def test_an_unapproved_snapshot_cannot_produce_a_feed() -> None:
    """暫定・未承認の snapshot から公開フィードは作れない（D03 §3.7.1 の3）。

    作れてしまうと「暫定 snapshot はバックテストの入力にできない」という設計が
    成り立たない。
    """
    bars = _bars(HOURLY, market.TF_1H)
    approved = snapshots.approved_for({HOURLY_PARTITION: bars})
    pending = snapshots.manifest(series_records=approved.series, partitions=approved.partitions)
    # 公開フィードは `ReadableSnapshot` しか受け取らないので、未承認の manifest は
    # そもそもその型を作れない段階で止まる。
    with pytest.raises(SnapshotNotApproved, match="has not been approved"):
        ReadableSnapshot(
            manifest=pending, directory_name=str(pending.snapshot_id()), report=IntegrityReport()
        )


def test_a_provisional_snapshot_can_never_be_opened_for_reading() -> None:
    """暫定ディレクトリ配下の snapshot は、承認が付いていても読めない（D03 §3.7.1 の1）。"""
    bars = _bars(HOURLY, market.TF_1H)
    approved = snapshots.approved_for({HOURLY_PARTITION: bars})
    with pytest.raises(SnapshotNotApproved, match="provisional snapshot"):
        ReadableSnapshot(
            manifest=approved,
            directory_name=f"_pending/{approved.snapshot_id()}",
            report=IntegrityReport(),
        )


def test_a_directory_name_that_is_not_the_snapshot_id_is_refused() -> None:
    """ディレクトリ名は最終識別子でなければならない（D03 §3.7.1 の2）。"""
    bars = _bars(HOURLY, market.TF_1H)
    approved = snapshots.approved_for({HOURLY_PARTITION: bars})
    with pytest.raises(MarketDataValueError, match="does not match the manifest"):
        ReadableSnapshot(
            manifest=approved, directory_name="some-other-directory", report=IntegrityReport()
        )


def test_a_quarantined_partition_cannot_produce_a_feed() -> None:
    """未分類の隔離期間はいかなる経路でも読めない（D03 §6.1、ADR-0014）。"""
    quarantined = PartitionId(series=HOURLY, access_class=AccessClass.QUARANTINED_UNASSIGNED)
    manifest = snapshots.readable_for((HOURLY_PARTITION, quarantined))
    with pytest.raises(HoldoutAccessViolation, match="quarantined partitions"):
        build_feed(
            manifest,
            frozenset({HOURLY_PARTITION, quarantined}),
            {HOURLY_PARTITION: _bars(HOURLY, market.TF_1H)},
            {HOURLY: SCHEDULES[HOURLY]},
            WINDOW,
        )


def test_a_partition_missing_from_the_manifest_is_refused() -> None:
    """manifest に記録のない partition は許可できない（何が入っているか確かめられない）。"""
    manifest = snapshots.readable_for((HOURLY_PARTITION,))
    with pytest.raises(MarketDataValueError, match="not recorded in the snapshot manifest"):
        build_feed(
            manifest,
            frozenset({HOURLY_PARTITION, FIFTEEN_PARTITION}),
            {HOURLY_PARTITION: _bars(HOURLY, market.TF_1H)},
            SCHEDULES,
            WINDOW,
        )


def test_bars_of_an_unallowed_partition_are_not_published() -> None:
    """許可外の partition に足を置いても、公開イベントには現れない（D03 §6.1）。"""
    partition_bars = {
        HOURLY_PARTITION: _bars(HOURLY, market.TF_1H),
        FIFTEEN_PARTITION: _bars(FIFTEEN, market.TF_15M),
    }
    feed = build_feed(
        snapshots.readable_for(partition_bars),
        frozenset({HOURLY_PARTITION}),  # 15分足は許可しない。
        partition_bars,
        SCHEDULES,
        WINDOW,
    )
    published = {event.series for event in feed.of_kind(PublicationKind.PUBLICATION)}
    assert published == {HOURLY}
