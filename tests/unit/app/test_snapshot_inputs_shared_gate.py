"""run の入力一式（`SnapshotInputs`）が読み取りの関門を1度だけ通すこと（D03 §3.7.1・§6.1）。

as-of ビュー・執行系列ビュー・公開フィードは同じ足を受け取る。内容照合は最初の経路で1度だけ
行い、以後は結果を共有する。照合に失敗した結果は覚えないので、呼び直しても同じ失敗になる。
"""

from __future__ import annotations

import pytest

import odyssey_fx.marketdata.application.snapshot_access as snapshot_access
from odyssey_fx.app.composition import SnapshotInputs
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.asof import AsOfView, ExecutionSeriesView
from odyssey_fx.marketdata.application.publication import build_feed
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import PartitionContentMismatch
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
CALENDAR = market.calendar()
WINDOW = Interval(
    start=UtcTime.parse("2026-01-12T22:00:00Z"), end=UtcTime.parse("2026-01-16T22:00:00Z")
)
PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
SCHEDULES = {HOURLY: SeriesSchedule(series=HOURLY, timeframe_def=market.TF_1H, calendar=CALENDAR)}
BARS = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)


def _inputs(bars: tuple[Bar, ...]) -> SnapshotInputs:
    return SnapshotInputs(
        snapshot=snapshots.readable_for({PARTITION: BARS}),
        allowed_partitions=frozenset({PARTITION}),
        partition_bars={PARTITION: bars},
        schedules=SCHEDULES,
    )


def test_the_content_is_checked_once_for_every_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[PartitionId] = []
    original = snapshot_access.require_matching_partition_content

    def counting(manifest, partition_id, bars):  # type: ignore[no-untyped-def]
        calls.append(partition_id)
        original(manifest, partition_id, bars)

    monkeypatch.setattr(snapshot_access, "require_matching_partition_content", counting)
    inputs = _inputs(BARS)
    verified = inputs.verified_bars("AsOfView")
    assert inputs.verified_bars("AsOfView") is verified
    AsOfView(
        snapshot=inputs.snapshot,
        allowed_partitions=inputs.allowed_partitions,
        schedules=inputs.schedules,
        partition_bars=verified,
    )
    ExecutionSeriesView(
        snapshot=inputs.snapshot,
        series=HOURLY,
        allowed_partitions=inputs.allowed_partitions,
        partition_bars=verified,
        schedule=SCHEDULES[HOURLY],
    )
    build_feed(
        inputs.snapshot,
        inputs.allowed_partitions,
        verified,
        inputs.schedules,
        Interval(
            start=UtcTime.parse("2026-01-13T22:00:00Z"),
            end=UtcTime.parse("2026-01-15T22:00:00Z"),
        ),
    )
    assert calls == [PARTITION]


def test_a_failed_check_is_not_remembered() -> None:
    inputs = _inputs(BARS[:-1])
    for _ in range(2):
        with pytest.raises(PartitionContentMismatch, match="bar\\(s\\) but the manifest records"):
            inputs.verified_bars("AsOfView")
