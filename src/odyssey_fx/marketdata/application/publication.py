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

from odyssey_fx.common.time import Interval, PhaseRank, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.publication_log import PublicationLog, PublicationRecord
from odyssey_fx.marketdata.domain.schedule import DelayScenario, SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId

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


def build_publication_log(
    bars_by_series: Mapping[SeriesId, Sequence[Bar]],
    schedules: Mapping[SeriesId, SeriesSchedule],
    scenario: DelayScenario | None = None,
) -> PublicationLog:
    """遅延シナリオを適用した実現公開時刻の記録を作る（D03 §3.6）。

    OHLC と対象区間は変えず、利用可能時刻だけを後ろへ動かす。遅延は非負なので
    `available_at >= bar_end` が常に成り立つ。
    """
    records: list[PublicationRecord] = []
    for series, bars in bars_by_series.items():
        schedule = schedules.get(series)
        if schedule is None:
            raise MarketDataValueError(f"no publication schedule was supplied for {series}")
        for bar in bars:
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
    bars_by_series: Mapping[SeriesId, Sequence[Bar]],
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
    """
    if not isinstance(run_interval, Interval):
        raise MarketDataValueError("build_feed requires an Interval run_interval")

    events: list[PublicationEvent] = []

    # 予定境界は公開予定から導く（データ到着と独立、D03 §7.1）。実行区間に足の終了時刻が
    # 入るものを拾うため、区間の開始より1本ぶん手前から期待足を数える。
    for series, scheduled_series in schedules.items():
        for bar_start in scheduled_series.calendar.expected_bar_starts(
            scheduled_series.timeframe_def,
            _boundary_search_window(scheduled_series, run_interval),
        ):
            expected = scheduled_series.timeframe_def.expected_interval(
                scheduled_series.calendar, bar_start
            )
            if expected is None:  # pragma: no cover - expected_bar_starts が除いている
                continue
            if not run_interval.contains(expected.end):
                continue
            events.append(
                ScheduledBoundary(
                    series=series,
                    bar_key=BarKey(series=series, bar_start=expected.start),
                    bar_end=expected.end,
                )
            )

    for series, bars in bars_by_series.items():
        schedule = schedules.get(series)
        if schedule is None:
            raise MarketDataValueError(f"no publication schedule was supplied for {series}")
        is_execution = series in execution_series
        for bar in bars:
            recorded = None if publication_log is None else publication_log.available_at(bar.key)
            available = recorded if recorded is not None else schedule.scheduled_at(bar.bar_end)

            if is_execution and run_interval.contains(bar.bar_end):
                events.append(
                    ExecutionBarComplete(series=series, bar_key=bar.key, bar_end=bar.bar_end)
                )
            if run_interval.contains(available):
                events.append(Publication(series=series, bar_key=bar.key, available_at=available))
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
