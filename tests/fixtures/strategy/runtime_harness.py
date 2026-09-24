"""戦略ランタイムをエンジンなしで判断時点ごとに動かす道具（D05 §6.1、D06 §4.3）。

待機・再開・追い越しの意味論は「期待される最新足」（D03 §6.2）に依存するので、単純な
`FakeMarketDataView` ではなく**本物の as-of ビュー**を使う。公開バッチはエンジンと同じ規則
（D06 §4.3）で足の列から組み立てる。

- 公開（`available_bars`）: `available_at` がその判断時刻に等しい足。
- 足の終了予定（`scheduled_closes`）: 区間の終端がその判断時刻に等しい足。遅延を当てても
  区間は変わらないので、データが届いていなくても予定どおり起動する（D03 §7.2）。
- 並びは D03 §7.1 の系列順（名目長の降順）。
- 公開も足の終了も無い判断時点では `step` を呼ばない（D06 §4.2）。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from odyssey_fx.common.ids import EventId
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.asof import AsOfView
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.publication_log import PublicationLog
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from odyssey_fx.strategy.runtime.ports import BarClosure, PublicationBatch
from tests.fixtures.strategy.phases import BACKTEST_PHASES
from tests.fixtures.synthetic import market, snapshots

__all__ = ["asof_view", "batches"]


def asof_view(bars: Mapping[SeriesId, Sequence[Bar]]) -> AsOfView:
    """足の列から、承認済み snapshot を読む as-of ビューを作る（D03 §6.1・§6.2）。"""
    calendar = market.calendar()
    partitions = {
        PartitionId(series=series, access_class=AccessClass.RESEARCH_HISTORY): tuple(items)
        for series, items in bars.items()
    }
    manifest = snapshots.approved_for(partitions)
    readable = ReadableSnapshot(
        manifest=manifest, directory_name=str(manifest.snapshot_id()), report=IntegrityReport()
    )
    schedules = {
        series: SeriesSchedule(
            series=series,
            timeframe_def=market.TIMEFRAME_DEFS[series.timeframe.id],
            calendar=calendar,
        )
        for series in bars
    }
    return AsOfView(
        snapshot=readable,
        allowed_partitions=frozenset(partitions),
        schedules=schedules,
        partition_bars=partitions,
        publication_log=PublicationLog(),
    )


def batches(
    bars: Mapping[SeriesId, Sequence[Bar]],
    start: UtcTime,
    end: UtcTime,
    *,
    first_batch_id: int = 1,
) -> Iterator[PublicationBatch]:
    """`[start, end]` の判断時点ごとの公開バッチを時刻順に返す（D06 §4.3）。"""

    def order(bar: Bar) -> tuple[float, str]:
        return (-bar.interval.duration.total_seconds(), str(bar.series))

    all_bars = [bar for items in bars.values() for bar in items]
    times = sorted(
        {
            moment.value
            for bar in all_bars
            for moment in (bar.available_at, bar.bar_end)
            if start.value <= moment.value <= end.value
        }
    )
    batch_id = first_batch_id
    for moment in times:
        at = UtcTime(moment)
        published = sorted((bar for bar in all_bars if bar.available_at == at), key=order)
        closing = sorted((bar for bar in all_bars if bar.bar_end == at), key=order)
        yield PublicationBatch(
            batch_id=EventId(batch_id),
            decision_time=at,
            phases=BACKTEST_PHASES,
            available_bars=tuple(bar.key for bar in published),
            scheduled_closes=tuple(
                BarClosure(bar_key=bar.key, interval=bar.interval) for bar in closing
            ),
        )
        batch_id += 1
