"""最終評価の許可の並び（要決定 R2-3 (a)）と、旧データの 2024 年以降の塞ぎ（D09 §9.10.13 の 2）。

as-of ビューは許可の並びが指す snapshot の封印の partition を読み、並びの外の封印の partition・
旧データの封印期間は許可集合に入れない。`SnapshotStore.read_partition` は並びが覆う partition だけを
読み、旧データの snapshot の 2024 年以降の partition はどの許可でも読まない（受入テスト 19）。
人工の足だけを使う（価格は人工の値）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.asof import AsOfView
from odyssey_fx.marketdata.application.snapshot_access import FinalSources, ReadableSnapshot
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, MarketDataValueError
from odyssey_fx.marketdata.domain.final_grant import FinalReadGrant, GrantedRead
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market
from tests.fixtures.synthetic import snapshots as synthetic

HOURLY = market.series()
CALENDAR = market.calendar()
SCHEDULE = SeriesSchedule(series=HOURLY, timeframe_def=market.TF_1H, calendar=CALENDAR)
RESEARCH = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
WEEK = UtcTime.parse("2024-01-07T22:00:00Z")
SEALED = PartitionId(series=HOURLY, access_class=AccessClass.HOLDOUT_SINCE_2024, period_start=WEEK)
RESEARCH_WINDOW = Interval(
    start=UtcTime.parse("2023-12-26T00:00:00Z"), end=UtcTime.parse("2023-12-29T00:00:00Z")
)
SEALED_WINDOW = Interval(
    start=UtcTime.parse("2024-01-08T00:00:00Z"), end=UtcTime.parse("2024-01-10T00:00:00Z")
)


def _bars(window: Interval) -> tuple[Bar, ...]:
    return market.make_bars(HOURLY, market.TF_1H, CALENDAR, window)


def _readable(bars: dict[PartitionId, Sequence[Bar]]) -> ReadableSnapshot:
    return synthetic.readable_for(bars)


RESEARCH_BARS = _bars(RESEARCH_WINDOW)
SEALED_BARS = _bars(SEALED_WINDOW)


def _sources() -> tuple[ReadableSnapshot, FinalSources]:
    primary = _readable({RESEARCH: RESEARCH_BARS})
    sealed = _readable({SEALED: SEALED_BARS})
    grant = FinalReadGrant(
        permit_id="p" * 32,
        reads=(GrantedRead(snapshot_id=str(sealed.snapshot_id), partition_id=SEALED),),
    )
    return primary, FinalSources(grant=grant, snapshots={str(sealed.snapshot_id): sealed})


def test_the_view_reads_the_granted_partition_from_its_own_snapshot() -> None:
    primary, sources = _sources()
    view = AsOfView(
        snapshot=primary,
        allowed_partitions=frozenset({RESEARCH, SEALED}),
        schedules={HOURLY: SCHEDULE},
        partition_bars={RESEARCH: RESEARCH_BARS, SEALED: SEALED_BARS},
        final_sources=sources,
    )
    assert SEALED in view.allowed_partitions


def test_without_the_grant_a_sealed_partition_never_enters_the_view() -> None:
    primary, _ = _sources()
    with pytest.raises(HoldoutAccessViolation, match="sealed partitions from 2024"):
        AsOfView(
            snapshot=primary,
            allowed_partitions=frozenset({RESEARCH, SEALED}),
            schedules={HOURLY: SCHEDULE},
            partition_bars={RESEARCH: RESEARCH_BARS, SEALED: SEALED_BARS},
        )


def test_bars_that_differ_from_the_source_manifest_are_refused() -> None:
    primary, sources = _sources()
    with pytest.raises(MarketDataValueError):
        AsOfView(
            snapshot=primary,
            allowed_partitions=frozenset({RESEARCH, SEALED}),
            schedules={HOURLY: SCHEDULE},
            partition_bars={RESEARCH: RESEARCH_BARS, SEALED: SEALED_BARS[:-1]},
            final_sources=sources,
        )


def test_the_grant_holds_only_sealed_partitions_once_and_in_order() -> None:
    with pytest.raises(MarketDataValueError):
        GrantedRead(snapshot_id="s", partition_id=RESEARCH)
    read = GrantedRead(snapshot_id="s", partition_id=SEALED)
    with pytest.raises(MarketDataValueError):
        FinalReadGrant(permit_id="p" * 32, reads=(read, read))
    later = PartitionId(
        series=HOURLY,
        access_class=AccessClass.HOLDOUT_SINCE_2024,
        period_start=UtcTime.parse("2024-01-14T22:00:00Z"),
    )
    with pytest.raises(MarketDataValueError):
        FinalReadGrant(
            permit_id="p" * 32, reads=(GrantedRead("s", later), GrantedRead("s", SEALED))
        )
    with pytest.raises(MarketDataValueError):
        FinalSources(grant=FinalReadGrant(permit_id="p" * 32, reads=(read,)), snapshots={})


def _stored(
    tmp_path: Path, bars: dict[PartitionId, Sequence[Bar]]
) -> tuple[ParquetSnapshotStore, str]:
    store = ParquetSnapshotStore(root=tmp_path)
    manifest = synthetic.approved_for(bars)
    snapshot_id = str(manifest.snapshot_id())
    (tmp_path / snapshot_id).mkdir()
    for partition_id, items in bars.items():
        store.write_partition(snapshot_id, partition_id, items)
    store.write_integrity_report(snapshot_id, IntegrityReport())
    store.write_manifest(snapshot_id, manifest)
    return store, snapshot_id


def test_read_partition_reads_a_sealed_partition_only_under_a_grant_of_that_snapshot(
    tmp_path: Path,
) -> None:
    store, snapshot_id = _stored(tmp_path, {SEALED: SEALED_BARS})
    with pytest.raises(HoldoutAccessViolation):
        store.read_partition(snapshot_id, SEALED)
    other = FinalReadGrant(permit_id="p" * 32, reads=(GrantedRead("0" * 64, SEALED),))
    with pytest.raises(HoldoutAccessViolation):
        store.read_partition(snapshot_id, SEALED, read_permit=other)
    grant = FinalReadGrant(permit_id="p" * 32, reads=(GrantedRead(snapshot_id, SEALED),))
    assert len(store.read_partition(snapshot_id, SEALED, read_permit=grant)) == len(SEALED_BARS)


@pytest.mark.parametrize(
    "access_class", [AccessClass.LEGACY_HOLDOUT, AccessClass.QUARANTINED_UNASSIGNED]
)
def test_legacy_partitions_from_2024_are_read_by_no_path(
    tmp_path: Path, access_class: AccessClass
) -> None:
    """旧データの snapshot の 2024 年以降の partition（分類・承認の検算も含めて読まない）。"""
    frozen = PartitionId(series=HOURLY, access_class=access_class)
    store, snapshot_id = _stored(tmp_path, {frozen: SEALED_BARS})
    with pytest.raises(HoldoutAccessViolation, match="frozen"):
        store.read_partition(snapshot_id, frozen)


def test_synthetic_legacy_holdout_before_2024_stays_readable_for_acceptance(
    tmp_path: Path,
) -> None:
    """人工データの封印 partition（D08 §9.5 の例外。2024 年より前）は受入れ・分類の検算で読める。"""
    holdout = PartitionId(series=HOURLY, access_class=AccessClass.LEGACY_HOLDOUT)
    store, snapshot_id = _stored(tmp_path, {holdout: RESEARCH_BARS})
    assert len(store.read_partition(snapshot_id, holdout)) == len(RESEARCH_BARS)
