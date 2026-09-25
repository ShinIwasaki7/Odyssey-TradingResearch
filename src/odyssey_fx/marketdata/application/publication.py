"""公開フィード（D03 §7）。

実行区間内の全系列について、4種類のイベントを時刻順に生成する（D03 §7.1）。

| イベント | 発生時刻 | 意味 |
|---|---|---|
| `ExecutionBarComplete` | 足の終了時刻 | 執行足が終了し、足内約定を解決できる |
| `Publication` | 遅延適用後の利用可能時刻 | 足のデータが利用可能になった |
| `ScheduledBoundary` | 足の終了時刻 | カレンダー上その足が終了する予定時刻。データ到着とは独立 |
| `ExecutionOpen` | 足の開始時刻 | 執行足の始値が処理可能になった。戦略には配送しない |

**同時刻の順序**（D03 §7.1）: `ExecutionBarComplete` → `Publication` → `ScheduledBoundary`
→ `ExecutionOpen`。上位設計書 §4.7.12 の「前足終了 → 内部約定 → 公開 → 判断 → 次足 open」
に対応する。フェーズの正式な列挙は D06 が決めるので、本モジュールは**この固定順**を持ち、
各イベント種別に対応する `PhaseRank`（順位付きの処理段階）は引数で受け取れるようにする。

**同時刻・同段階内の系列順**（D03 §7.1）: `(symbol, 名目長の降順, basis)` で固定する。
長い時間足を先に配送することで、日足と 15分足が同時に確定する時刻でも配送順が決まる。

`OnBarClose`（足の確定で戦略評価を起動する条件）は `ScheduledBoundary` に結び付ける
（D03 §7.2）。データが遅延している場合、評価時に `latest_available` が
「最新足が取得できない」を返し、`on_missing` で扱う。`Publication` に結び付けると、遅延した
系列の評価が黙って後ろへずれ、見送りと待機の区別が失われるため。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from odyssey_fx.common.time import Interval, PhaseRank, UtcTime
from odyssey_fx.marketdata.application.snapshot_access import (
    PartitionedBars,
    ReadableSnapshot,
    require_readable_snapshot,
)
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, MarketDataValueError
from odyssey_fx.marketdata.domain.publication_log import PublicationLog, PublicationRecord
from odyssey_fx.marketdata.domain.schedule import DelayScenario, SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = [
    "EVENT_ORDER",
    "ExecutionBarComplete",
    "ExecutionOpen",
    "PublicationEvent",
    "PublicationFeed",
    "PublicationKind",
    "Publication",
    "ScheduledBoundary",
    "build_feed",
    "build_publication_log",
]


class PublicationKind(Enum):
    """公開イベントの種別（D03 §7.1）。"""

    EXECUTION_BAR_COMPLETE = "EXECUTION_BAR_COMPLETE"
    PUBLICATION = "PUBLICATION"
    SCHEDULED_BOUNDARY = "SCHEDULED_BOUNDARY"
    EXECUTION_OPEN = "EXECUTION_OPEN"


#: 同時刻のイベント順（D03 §7.1）。D06 がフェーズを正式に列挙するまでの固定順。
EVENT_ORDER: tuple[PublicationKind, ...] = (
    PublicationKind.EXECUTION_BAR_COMPLETE,
    PublicationKind.PUBLICATION,
    PublicationKind.SCHEDULED_BOUNDARY,
    PublicationKind.EXECUTION_OPEN,
)

_EVENT_RANK: Mapping[PublicationKind, int] = {kind: index for index, kind in enumerate(EVENT_ORDER)}


@dataclass(frozen=True, slots=True)
class ScheduledBoundary:
    """カレンダー上その足が終了する予定時刻の通知（D03 §7.1）。データ到着とは独立。"""

    series: SeriesId
    bar_key: BarKey
    bar_end: UtcTime

    kind = PublicationKind.SCHEDULED_BOUNDARY

    @property
    def at(self) -> UtcTime:
        """イベントの発生時刻。"""
        return self.bar_end


@dataclass(frozen=True, slots=True)
class Publication:
    """足のデータが利用可能になった通知（D03 §7.1）。遅延適用後の時刻で起きる。"""

    series: SeriesId
    bar_key: BarKey
    available_at: UtcTime

    kind = PublicationKind.PUBLICATION

    @property
    def at(self) -> UtcTime:
        """イベントの発生時刻。"""
        return self.available_at


@dataclass(frozen=True, slots=True)
class ExecutionOpen:
    """執行足の始値が処理可能になった通知（D03 §7.1・§7.3）。戦略には配送しない。

    始値だけが処理可能になり、同じ足の高値・安値・終値は `ExecutionBarComplete` まで
    見えない（上位設計書 §4.7.11）。戦略ビューにはその足は終了時刻まで存在しない。
    """

    series: SeriesId
    bar_key: BarKey
    open_time: UtcTime

    kind = PublicationKind.EXECUTION_OPEN

    @property
    def at(self) -> UtcTime:
        """イベントの発生時刻。"""
        return self.open_time


@dataclass(frozen=True, slots=True)
class ExecutionBarComplete:
    """執行足が終了し、足内約定を解決できる通知（D03 §7.1）。"""

    series: SeriesId
    bar_key: BarKey
    bar_end: UtcTime

    kind = PublicationKind.EXECUTION_BAR_COMPLETE

    @property
    def at(self) -> UtcTime:
        """イベントの発生時刻。"""
        return self.bar_end


#: 公開イベントの判別可能な union（D01 §8）。
PublicationEvent = ScheduledBoundary | Publication | ExecutionOpen | ExecutionBarComplete


def _series_order_key(
    series: SeriesId, schedules: Mapping[SeriesId, SeriesSchedule]
) -> tuple[str, int, str]:
    """同時刻・同段階内の系列順（D03 §7.1）。

    `(symbol, 名目長の降順, basis)`。名目長は降順にしたいので、秒数の符号を反転して
    昇順の整列に載せる。
    """
    schedule = schedules.get(series)
    if schedule is None:
        raise MarketDataValueError(f"no publication schedule was supplied for {series}")
    nominal_seconds = int(schedule.timeframe_def.nominal_length.total_seconds())
    return (str(series.symbol), -nominal_seconds, series.basis.value)


@dataclass(frozen=True, slots=True)
class PublicationFeed:
    """時刻順に並べた公開イベント列（D03 §7.1）。

    `PublicationFeed`（`backtest.application.ports`）を構造的に満たす（D01 §2.2 規則7）。
    `phase_ranks` を渡すと、各イベント種別に処理段階（`PhaseRank`）を対応付けられる。
    フェーズの正式な列挙は D06 が決めるため、対応表は外から与える形にしている。
    """

    events: tuple[PublicationEvent, ...]
    phase_ranks: Mapping[PublicationKind, PhaseRank] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple):
            raise MarketDataValueError("PublicationFeed.events must be a tuple")
        if self.phase_ranks is not None:
            for kind, rank in self.phase_ranks.items():
                if not isinstance(kind, PublicationKind):
                    raise MarketDataValueError(
                        "PublicationFeed.phase_ranks keys must be PublicationKind"
                    )
                if not isinstance(rank, PhaseRank):
                    raise MarketDataValueError(
                        "PublicationFeed.phase_ranks values must be PhaseRank"
                    )
            ordered_ranks = [
                self.phase_ranks[kind].rank for kind in EVENT_ORDER if kind in self.phase_ranks
            ]
            if ordered_ranks != sorted(ordered_ranks):
                raise MarketDataValueError(
                    "the supplied phase ranks contradict the fixed same-instant order"
                    f" {[kind.value for kind in EVENT_ORDER]} (D03 §7.1)"
                )

    def phase_rank(self, kind: PublicationKind) -> PhaseRank | None:
        """イベント種別に対応する処理段階（未設定なら `None`）。"""
        if self.phase_ranks is None:
            return None
        return self.phase_ranks.get(kind)

    def of_kind(self, kind: PublicationKind) -> tuple[PublicationEvent, ...]:
        """指定した種別のイベントだけを返す。"""
        return tuple(event for event in self.events if event.kind is kind)

    def __iter__(self) -> Iterator[PublicationEvent]:
        """イベントを時刻順に反復する。"""
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)


def _boundary_search_window(schedule: SeriesSchedule, run_interval: Interval) -> Interval:
    """予定境界を探すための窓（D03 §7.1）。

    実行区間の中で**終わる**足を拾いたいので、区間の開始より手前から始まる足も候補に入れる
    必要がある。手前に取る幅は名目長の2倍（夏時間の切替で足が名目より長くなっても足りる）。
    """
    return Interval(
        start=run_interval.start - schedule.timeframe_def.nominal_length * 2,
        end=run_interval.end,
    )


def _allowed_coverage(
    snapshot: ReadableSnapshot, allowed_partitions: frozenset[PartitionId]
) -> Mapping[SeriesId, tuple[Interval, ...]]:
    """系列ごとに、許可された partition が覆う区間を求める（D03 §6.1）。

    同じ系列で複数の partition が許可されている場合、隣接・重複する区間はつなげる。
    研究期間と封印期間が連続して許可されていれば、その全体が1つの区間になる。
    """
    by_series: dict[SeriesId, list[Interval]] = {}
    for partition_id in allowed_partitions:
        record = snapshot.manifest.partition_record(partition_id)
        if record is None:  # pragma: no cover - 関門が先に拒否する
            continue
        by_series.setdefault(partition_id.series, []).append(record.interval)

    coverage: dict[SeriesId, tuple[Interval, ...]] = {}
    for series, intervals in by_series.items():
        merged: list[Interval] = []
        for interval in sorted(intervals, key=lambda item: item.start.value):
            if merged and interval.start <= merged[-1].end:
                if merged[-1].end < interval.end:
                    merged[-1] = Interval(start=merged[-1].start, end=interval.end)
                continue
            merged.append(interval)
        coverage[series] = tuple(merged)
    return coverage


def _require_run_interval_inside_allowed(
    snapshot: ReadableSnapshot,
    allowed_partitions: frozenset[PartitionId],
    run_interval: Interval,
) -> tuple[SeriesId, ...]:
    """実行区間が許可 partition の区間に収まることを確かめ、境界を出す系列を返す。

    予定境界はデータ到着と独立に出る（D03 §7.1）ので、許可されていない期間を実行区間に
    含めると、禁止期間の境界で戦略評価が起動してしまう。研究期間だけを許可した状態で実行
    区間を封印期間に取れば、封印期間の判断が動き出す。区間の検査は構築時に行い、通らない
    要求は `HoldoutAccessViolation`（構造エラー）で止める（D03 §6.1）。
    """
    coverage = _allowed_coverage(snapshot, allowed_partitions)
    if not coverage:
        raise HoldoutAccessViolation(
            "no partition was granted to this feed; there is no period it may cover (D03 §6.1)"
        )
    for series, intervals in sorted(coverage.items(), key=lambda pair: str(pair[0])):
        if not any(
            interval.start <= run_interval.start and run_interval.end <= interval.end
            for interval in intervals
        ):
            readable = ", ".join(str(interval) for interval in intervals)
            raise HoldoutAccessViolation(
                f"the run interval {run_interval} is not covered by the partitions granted"
                f" for {series} ({readable}); scheduled boundaries would fire outside the"
                " permitted period (D03 §6.1・§7.1)"
            )
    return tuple(coverage)


def _within_run(run_interval: Interval, moment: UtcTime) -> bool:
    """`moment` が実行区間の中か（**終端を含む**、D03 §7.1）。

    足の終了・公開・予定境界は `run_interval.end` と同時刻のものまで出す。バックテストの
    末尾処理は「run_end で終了する足までの内部約定・口座更新を解決する」ところから始まる
    ので（D06 §10.1 の手順1）、終端をまたぐ足ではなく**終端でちょうど終わる足**を落として
    しまうと、末尾の判断時点が1つ丸ごと起きず、残存建玉の最終評価価格が1本手前の足の
    終値になる。

    **足の始値（`ExecutionOpen`）だけは終端を含めない**。D06 §10.1 の手順5 が「run_end
    から始まる足の始値処理は行わない」と定めており、区間の外で約定させないための規則で
    ある。判定を分けているのはこのためで、同じ述語を使い回してはいけない。
    """
    return run_interval.start <= moment <= run_interval.end


def build_publication_log(
    snapshot: ReadableSnapshot,
    allowed_partitions: frozenset[PartitionId],
    partition_bars: Mapping[PartitionId, Sequence[Bar]],
    schedules: Mapping[SeriesId, SeriesSchedule],
    scenario: DelayScenario | None = None,
) -> PublicationLog:
    """遅延シナリオを適用した実現公開時刻の記録を作る（D03 §3.6）。

    OHLC と対象区間は変えず、利用可能時刻だけを後ろへ動かす。遅延は非負なので
    `available_at >= bar_end` が常に成り立つ。

    足は as-of ビューと同じ関門を通した読み取り面から取る（D03 §3.7.1・§6.1）。承認前の
    snapshot と許可されていない partition は、公開の記録にも入れない。
    """
    frozen = require_readable_snapshot(
        snapshot,
        allowed_partitions,
        label="build_publication_log",
        partition_bars=partition_bars,
    )
    readable = PartitionedBars(frozen, allowed_partitions)
    # 公開予定も写し取る。この関数は記録を作って返すだけなので呼び出し中に差し替えられる
    # 余地は小さいが、`AsOfView` と同じ扱いにして経路ごとの差をなくす（D03 §3.5）。
    schedules = MappingProxyType(dict(schedules))

    records: list[PublicationRecord] = []
    for series in readable.series():
        schedule = schedules.get(series)
        if schedule is None:
            raise MarketDataValueError(f"no publication schedule was supplied for {series}")
        for bar in readable.bars_or_empty(series):
            scheduled = schedule.scheduled_at(bar.bar_end)
            available = (
                scheduled
                if scenario is None
                else scenario.available_at(schedule, bar.bar_start, bar.bar_end)
            )
            records.append(
                PublicationRecord(
                    bar_key=bar.key,
                    bar_end=bar.bar_end,
                    scheduled_at=scheduled,
                    available_at=available,
                )
            )
    return PublicationLog(records=tuple(records))


def build_feed(
    snapshot: ReadableSnapshot,
    allowed_partitions: frozenset[PartitionId],
    partition_bars: Mapping[PartitionId, Sequence[Bar]],
    schedules: Mapping[SeriesId, SeriesSchedule],
    run_interval: Interval,
    *,
    execution_series: frozenset[SeriesId] = frozenset(),
    publication_log: PublicationLog | None = None,
    phase_ranks: Mapping[PublicationKind, PhaseRank] | None = None,
) -> PublicationFeed:
    """実行区間内の公開イベント列を組み立てる（D03 §7.1）。

    **予定境界（`ScheduledBoundary`）はデータ到着と独立**に生成する（D03 §7.1）。実在する
    足からではなく、公開予定（時間足定義とカレンダー）が「存在すべき」とする足すべてに
    ついて出す。足が欠損・遅延していても予定時点で評価を起動できるようにするためで、これが
    なければ欠損した系列の評価が黙って飛ばされ、見送り・待機・過去値使用・失敗の区別
    （`on_missing`）が働かない（D03 §7.2、上位設計書 §4.3.13）。

    残る3種類（`Publication` / `ExecutionOpen` / `ExecutionBarComplete`）は、実際に存在する
    足からのみ生成する。データが無ければ公開も足内約定の解決も起きないためである。

    `execution_series` に挙げた系列だけが執行系列のイベントを生む。`publication_log` を
    渡すとその実現時刻を使い、渡さなければ通常の公開予定（足の終了時刻＋通常遅延）を使う。

    並びは `(発生時刻, イベント種別の固定順, 系列順)`。同じ時刻に複数の系列・種別が並んでも
    順序が一意に決まる。

    **読めるのは承認済み snapshot の許可された partition だけ**（D03 §3.7.1 の3・§6.1）。
    as-of ビューと同じ関門を通す。生の足を直接受け取る形にすると、暫定・未承認の snapshot
    から公開フィードを作れてしまい、「暫定 snapshot はバックテストの入力にできない」という
    設計が成り立たない。

    **実行区間は許可 partition の区間に収まっていなければならない**（D03 §6.1）。予定境界は
    データ到着と独立に出るので、許可されていない期間を実行区間に含めると、禁止期間の境界で
    戦略評価が起動してしまう。収まらない場合は構築時に `HoldoutAccessViolation` で拒否する。
    """
    if not isinstance(run_interval, Interval):
        raise MarketDataValueError("build_feed requires an Interval run_interval")
    frozen = require_readable_snapshot(
        snapshot, allowed_partitions, label="build_feed", partition_bars=partition_bars
    )
    readable = PartitionedBars(frozen, allowed_partitions)
    schedules = MappingProxyType(dict(schedules))
    boundary_series = _require_run_interval_inside_allowed(
        snapshot, allowed_partitions, run_interval
    )

    events: list[PublicationEvent] = []
    # 実現した公開時刻の索引（足ごとに記録の列を先頭から探さない）。
    recorded_at = (
        {}
        if publication_log is None
        else {record.bar_key: record.available_at for record in publication_log.records}
    )

    # 予定境界は公開予定から導く（データ到着と独立、D03 §7.1）。ただし出すのは**許可
    # partition を持つ系列**に限る。実行区間に足の終了時刻が入るものを拾うため、区間の
    # 開始より1本ぶん手前から期待足を数える。
    for series in sorted(boundary_series, key=str):
        scheduled_series = schedules.get(series)
        if scheduled_series is None:
            raise MarketDataValueError(f"no publication schedule was supplied for {series}")
        for bar_start in scheduled_series.calendar.expected_bar_starts(
            scheduled_series.timeframe_def,
            _boundary_search_window(scheduled_series, run_interval),
        ):
            expected = scheduled_series.timeframe_def.expected_interval(
                scheduled_series.calendar, bar_start
            )
            if expected is None:  # pragma: no cover - expected_bar_starts が除いている
                continue
            if not _within_run(run_interval, expected.end):
                continue
            events.append(
                ScheduledBoundary(
                    series=series,
                    bar_key=BarKey(series=series, bar_start=expected.start),
                    bar_end=expected.end,
                )
            )

    for series in readable.series():
        schedule = schedules.get(series)
        if schedule is None:
            raise MarketDataValueError(f"no publication schedule was supplied for {series}")
        is_execution = series in execution_series
        for bar in readable.bars_or_empty(series):
            recorded = recorded_at.get(bar.key)
            available = recorded if recorded is not None else schedule.scheduled_at(bar.bar_end)

            if is_execution and _within_run(run_interval, bar.bar_end):
                events.append(
                    ExecutionBarComplete(series=series, bar_key=bar.key, bar_end=bar.bar_end)
                )
            if _within_run(run_interval, available):
                events.append(Publication(series=series, bar_key=bar.key, available_at=available))
            # 始値の処理だけは終端を含めない（D06 §10.1 の手順5）。
            if is_execution and run_interval.contains(bar.bar_start):
                events.append(
                    ExecutionOpen(series=series, bar_key=bar.key, open_time=bar.bar_start)
                )

    ordered = tuple(
        sorted(
            events,
            key=lambda event: (
                event.at.value,
                _EVENT_RANK[event.kind],
                _series_order_key(event.series, schedules),
                str(event.bar_key.bar_start),
            ),
        )
    )
    return PublicationFeed(events=ordered, phase_ranks=phase_ranks)
