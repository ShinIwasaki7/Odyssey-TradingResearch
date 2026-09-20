"""as-of ビューの意味論テスト（D03 §6・§11）。

D03 §11 の「意味論」の項目を1件1テストで固定する。

- 未確定の上位足を参照できない。
- 遅延を注入しても OHLC は変わらず、公開時刻だけが動く。
- 期待足が未到着のとき、古い足へ黙って戻らない。
- 範囲外 partition の要求は構造エラー（`HoldoutAccessViolation`）になる。
- 承認前の snapshot は読めない（`SnapshotNotApproved`）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta

import pytest

from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.asof import AsOfView, BarsWindow, MissingInput
from odyssey_fx.marketdata.application.publication import build_publication_log
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, SnapshotNotApproved
from odyssey_fx.marketdata.domain.publication_log import PublicationLog
from odyssey_fx.marketdata.domain.schedule import (
    DelayScenario,
    FixedSeriesDelay,
    SeriesSchedule,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    PartitionId,
    SeriesManifest,
    SnapshotManifest,
)
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
DAILY = market.series(timeframe_id="1d_ny17")
CALENDAR = market.calendar()

WINDOW = Interval(
    start=UtcTime.parse("2026-01-12T22:00:00Z"), end=UtcTime.parse("2026-01-16T22:00:00Z")
)

HOURLY_SCHEDULE = SeriesSchedule(series=HOURLY, timeframe_def=market.TF_1H, calendar=CALENDAR)
DAILY_SCHEDULE = SeriesSchedule(series=DAILY, timeframe_def=market.TF_1D_NY17, calendar=CALENDAR)
SCHEDULES = {HOURLY: HOURLY_SCHEDULE, DAILY: DAILY_SCHEDULE}

HOURLY_PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
DAILY_PARTITION = PartitionId(series=DAILY, access_class=AccessClass.RESEARCH_HISTORY)


def _bars(
    series: SeriesId,
    timeframe_def: TimeframeDefinition,
    *,
    skip_starts: Iterable[UtcTime] = (),
) -> tuple[Bar, ...]:
    return market.make_bars(series, timeframe_def, CALENDAR, WINDOW, skip_starts=skip_starts)


def _manifest(*partition_ids: PartitionId) -> SnapshotManifest:
    records = tuple(
        snapshots.partition(partition_id.series, partition_id.access_class)
        for partition_id in partition_ids
    )
    by_series: dict[SeriesId, list[PartitionId]] = {}
    for record in records:
        by_series.setdefault(record.series_id, []).append(record.partition_id)
    series_records = tuple(
        SeriesManifest(
            series_id=series,
            covered_interval=snapshots.COVERED,
            bar_count=100,
            partitions=tuple(ids),
        )
        for series, ids in by_series.items()
    )
    return snapshots.approved(series_records=series_records, partitions=records)


def _view(
    *,
    hourly_skip: Iterable[UtcTime] = (),
    allowed: Sequence[PartitionId] = (HOURLY_PARTITION, DAILY_PARTITION),
    publication_log: PublicationLog | None = None,
    approved: bool = True,
) -> AsOfView:
    manifest = _manifest(HOURLY_PARTITION, DAILY_PARTITION)
    if not approved:
        manifest = snapshots.manifest(
            series_records=manifest.series, partitions=manifest.partitions
        )
    partition_bars: dict[PartitionId, Sequence[Bar]] = {
        HOURLY_PARTITION: _bars(HOURLY, market.TF_1H, skip_starts=hourly_skip),
        DAILY_PARTITION: _bars(DAILY, market.TF_1D_NY17),
    }
    return AsOfView(
        manifest=manifest,
        allowed_partitions=frozenset(allowed),
        schedules=SCHEDULES,
        partition_bars=partition_bars,
        publication_log=PublicationLog() if publication_log is None else publication_log,
    )


# --- 未来参照は構造的に不可能（D03 §6.2）------------------------------------


def test_an_unfinished_daily_bar_is_not_visible() -> None:
    """未確定の上位足を参照できない（D03 §11 の意味論）。

    水曜の正午時点では、その日の日足（現地 17 時に終わる）はまだ確定していない。
    """
    view = _view()
    at = UtcTime.parse("2026-01-14T12:00:00Z")
    latest = view.latest_available(DAILY, at)
    assert not isinstance(latest, MissingInput)
    # 返るのは前の日足であり、進行中の足ではない。
    assert latest.bar_end <= at
    assert latest.bar_end == UtcTime.parse("2026-01-13T22:00:00Z")


def test_a_bar_is_invisible_before_its_availability() -> None:
    view = _view()
    bar_start = UtcTime.parse("2026-01-14T10:00:00Z")
    before = view.bar(HOURLY, bar_start, UtcTime.parse("2026-01-14T10:59:59Z"))
    assert isinstance(before, MissingInput)
    after = view.bar(HOURLY, bar_start, UtcTime.parse("2026-01-14T11:00:00Z"))
    assert not isinstance(after, MissingInput)


# --- 遅延注入（D03 §3.6・§11）-----------------------------------------------


def test_injecting_a_delay_moves_only_the_availability_not_the_ohlc() -> None:
    """遅延注入で公開時刻だけが動き、OHLC と対象区間は変わらない（D03 §3.6）。"""
    bars = _bars(DAILY, market.TF_1D_NY17)
    scenario = DelayScenario(
        id="daily_two_seconds",
        version=1,
        rules=(FixedSeriesDelay(series=DAILY, delay=timedelta(seconds=2)),),
    )
    log = build_publication_log({DAILY: bars}, SCHEDULES, scenario)
    delayed = _view(publication_log=log)
    plain = _view()

    at = UtcTime.parse("2026-01-14T22:00:00Z")  # 日足の終了ちょうど
    without_delay = plain.latest_available(DAILY, at)
    with_delay = delayed.latest_available(DAILY, at)

    assert not isinstance(without_delay, MissingInput)
    # 2秒遅れているので、終了ちょうどではまだ見えない。
    assert isinstance(with_delay, MissingInput)
    assert with_delay.reason is MissingInputReason.LATEST_BAR_UNAVAILABLE

    later = delayed.latest_available(DAILY, at + timedelta(seconds=2))
    assert not isinstance(later, MissingInput)
    # OHLC と対象区間は遅延の有無で変わらない。
    assert later.interval == without_delay.interval
    assert (later.open, later.high, later.low, later.close) == (
        without_delay.open,
        without_delay.high,
        without_delay.low,
        without_delay.close,
    )


def test_a_missing_expected_bar_does_not_fall_back_to_an_older_one() -> None:
    """期待足が未到着でも古い足へ黙って戻らない（D03 §6.2）。"""
    missing = UtcTime.parse("2026-01-14T10:00:00Z")
    view = _view(hourly_skip=(missing,))
    at = UtcTime.parse("2026-01-14T11:00:00Z")
    latest = view.latest_available(HOURLY, at)
    assert isinstance(latest, MissingInput)
    assert latest.reason is MissingInputReason.LATEST_BAR_UNAVAILABLE


def test_the_expected_latest_key_is_independent_of_arrival() -> None:
    """期待される最新足はカレンダーと定義から決まり、到着の有無を見ない（D03 §6.2）。"""
    missing = UtcTime.parse("2026-01-14T10:00:00Z")
    view = _view(hourly_skip=(missing,))
    key = view.expected_latest_key(HOURLY, UtcTime.parse("2026-01-14T11:00:00Z"))
    assert key is not None
    assert key.bar_start == missing


# --- 履歴窓（D03 §6.2）------------------------------------------------------


def test_a_history_window_requires_every_expected_bar() -> None:
    """窓内の期待足がすべて揃わなければ欠損として扱う（期間を短縮しない）。"""
    missing = UtcTime.parse("2026-01-14T09:00:00Z")
    view = _view(hourly_skip=(missing,))
    at = UtcTime.parse("2026-01-14T12:00:00Z")
    history = view.history(HOURLY, BarsWindow(count=4), at)
    assert isinstance(history, MissingInput)
    assert history.reason is MissingInputReason.INPUT_MISSING_OR_INVALID


def test_a_complete_history_window_returns_the_bars_oldest_first() -> None:
    view = _view()
    at = UtcTime.parse("2026-01-14T12:00:00Z")
    history = view.history(HOURLY, BarsWindow(count=3), at)
    assert not isinstance(history, MissingInput)
    assert [str(bar.bar_start) for bar in history] == [
        "2026-01-14T09:00:00Z",
        "2026-01-14T10:00:00Z",
        "2026-01-14T11:00:00Z",
    ]


def test_an_end_offset_excludes_the_most_recent_bars() -> None:
    """「当該足を除く過去 N 本」を表現する（D03 §6.2）。"""
    view = _view()
    at = UtcTime.parse("2026-01-14T12:00:00Z")
    history = view.history(HOURLY, BarsWindow(count=2), at, end_offset_bars=1)
    assert not isinstance(history, MissingInput)
    assert [str(bar.bar_start) for bar in history] == [
        "2026-01-14T09:00:00Z",
        "2026-01-14T10:00:00Z",
    ]


def test_a_window_reaching_before_the_data_start_reports_warmup() -> None:
    view = _view()
    at = UtcTime.parse("2026-01-13T00:00:00Z")
    history = view.history(HOURLY, BarsWindow(count=500), at)
    assert isinstance(history, MissingInput)
    assert history.reason is MissingInputReason.WARMUP_INSUFFICIENT


# --- partition の境界（D03 §6.1・§11）---------------------------------------


def test_reading_a_series_outside_the_allowed_partitions_is_a_structural_error() -> None:
    """範囲外 partition の要求は入力欠損ではなく構造エラー（D03 §6.1）。"""
    view = _view(allowed=(HOURLY_PARTITION,))
    with pytest.raises(HoldoutAccessViolation, match="not inside the allowed partitions"):
        view.latest_available(DAILY, UtcTime.parse("2026-01-14T22:00:00Z"))


def test_reading_outside_the_readable_range_is_a_structural_error() -> None:
    view = _view()
    with pytest.raises(HoldoutAccessViolation, match="outside the readable range"):
        view.bar(
            HOURLY,
            UtcTime.parse("2030-01-14T10:00:00Z"),
            UtcTime.parse("2030-01-14T11:00:00Z"),
        )


# --- 承認（D03 §3.7.1 の3・§11）---------------------------------------------


def test_an_unapproved_snapshot_cannot_be_read() -> None:
    """承認が記入されるまで as-of ビューは snapshot を読めない（D03 §3.7.1）。"""
    with pytest.raises(SnapshotNotApproved, match="has not been approved"):
        _view(approved=False)


# --- 鮮度（D03 §6.2）--------------------------------------------------------


def test_the_freshness_reference_of_a_confirmed_bar_is_its_end() -> None:
    """確定足の鮮度基準は足の終了時刻。遅れて到着しても新鮮にはならない。"""
    view = _view()
    bar = view.bar(
        HOURLY,
        UtcTime.parse("2026-01-14T10:00:00Z"),
        UtcTime.parse("2026-01-14T12:00:00Z"),
    )
    assert not isinstance(bar, MissingInput)
    assert view.freshness(HOURLY, bar) == bar.bar_end
