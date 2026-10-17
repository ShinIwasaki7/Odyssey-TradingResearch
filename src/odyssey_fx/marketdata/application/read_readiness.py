"""読み取りの準備不足の期間（D06 §18.10 の表22、D03 §15.7。D12-112・D12-119・D12-127）。

全分無記録の足を必要とする読み取りは、その足の予定の公開時刻（遅延シナリオ込み）以後「準備不足」
（`WARMUP_INSUFFICIENT`）を返す（D03 §15.7）。本モジュールは、戦略が宣言した読み取りの窓（本数窓・
末尾から除く本数・経過時間の窓・最新足だけ。`ReadWindowSpec`）ごとに、

- 準備不足になる時刻（その無記録の足のせいで読み取りが初めて準備不足を返す時刻）と、
- 復帰する時刻（読み取りが実際に足を返すようになる時刻。後に続く無記録の足も考えた結果）

を、as-of ビューと同じ読み取りの条件（`window_starts`・期待される最新足・予定の公開時刻・公開記録
の時刻）から導く。人間の決定 D12-127「準備不足になる時刻と復帰する時刻は、実際の読み取り条件・
カレンダー・公開遅延に一致させ」（原文は D12 §0.1.30.1）による。経過時間の窓を本数に換算しない。

読み取りの状態が変わるのは、期待される最新足が変わる時刻（足の終わり）、無記録の足の予定の
公開時刻（公開記録があればその時刻）、作られた足が見えるようになる時刻だけなので、その時刻を
順に評価する。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.asof import BarsWindow, DurationWindow, window_starts
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.readiness import ReadKind, ReadWindowSpec
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule

__all__ = ["ReadReset", "read_resets"]

_TINY = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class ReadReset:
    """表22 の 1 行の中身（系列と窓は呼び出し側が持つ）。"""

    no_record_bar_start: UtcTime
    insufficient_from: UtcTime
    ready_bar_start: UtcTime | None
    ready_at: UtcTime | None


@dataclass(frozen=True, slots=True)
class _State:
    culprits: tuple[UtcTime, ...]
    readable: bool
    base: UtcTime | None


class _Reader:
    """1 系列 × 1 窓の読み取りの条件（as-of ビューの `history`・`latest_available` と同じ）。"""

    def __init__(
        self,
        schedule: SeriesSchedule,
        spec: ReadWindowSpec,
        bars: Sequence[Bar],
        no_record: frozenset[UtcTime],
        published: Mapping[BarKey, UtcTime],
    ) -> None:
        self.schedule = schedule
        self.spec = spec
        self.built = {bar.bar_start: bar for bar in bars}
        self.no_record = no_record
        self.published = published
        self.data_start = bars[0].bar_start

    def _key(self, start: UtcTime) -> BarKey:
        return BarKey(series=self.schedule.series, bar_start=start)

    def _end(self, start: UtcTime) -> UtcTime | None:
        interval = self.schedule.timeframe_def.expected_interval(self.schedule.calendar, start)
        return None if interval is None else interval.end

    def passed(self, start: UtcTime, at: UtcTime) -> bool:
        """無記録の足の予定の公開時刻（公開記録があればその時刻）が `at` 以前か（D03 §15.7）。"""
        return self.publication_at(start) <= at

    def publication_at(self, start: UtcTime) -> UtcTime:
        recorded = self.published.get(self._key(start))
        if recorded is not None:
            return recorded
        end = self._end(start)
        assert end is not None  # noqa: S101 - 期待足の開始だけを渡す
        return self.schedule.scheduled_at(end)

    def visible_at(self, start: UtcTime) -> UtcTime | None:
        """作られた足が見えるようになる時刻（as-of ビューの `_available_at`）。"""
        bar = self.built.get(start)
        if bar is None:
            return None
        recorded = self.published.get(bar.key)
        if recorded is not None:
            return recorded
        scheduled = self.schedule.scheduled_at(bar.bar_end)
        return max(bar.available_at, scheduled, key=lambda value: value.value)

    def needed(self, base: UtcTime) -> tuple[tuple[UtcTime, ...], bool]:
        """基準の足から読む足（新しい順）と、窓がデータの開始前に及ぶか。"""
        spec = self.spec
        if spec.kind is ReadKind.LATEST:
            return (base,), base < self.data_start
        window: BarsWindow | DurationWindow
        if spec.kind is ReadKind.BARS:
            assert spec.bars is not None  # noqa: S101 - ReadWindowSpec の不変条件
            window = BarsWindow(spec.bars)
        else:
            assert spec.duration is not None  # noqa: S101 - ReadWindowSpec の不変条件
            window = DurationWindow(spec.duration)
        wanted = window_starts(self.schedule, window, base, spec.exclude_latest_bars)
        if wanted is None:
            return (), True
        return wanted, any(start < self.data_start for start in wanted)

    def state(self, at: UtcTime) -> _State:
        base = self.schedule.expected_latest_bar_start(at)
        if base is None:
            return _State(culprits=(), readable=False, base=None)
        wanted, before_data = self.needed(base)
        checked = wanted if self.spec.kind is ReadKind.LATEST else (base, *wanted)
        culprits = tuple(
            sorted(
                {
                    start
                    for start in checked
                    if start >= self.data_start
                    and start in self.no_record
                    and self.passed(start, at)
                },
                key=lambda value: value.value,
            )
        )
        if culprits or before_data or not wanted:
            return _State(culprits=culprits, readable=False, base=base)
        readable = all(
            (visible := self.visible_at(start)) is not None and visible <= at for start in wanted
        )
        return _State(culprits=(), readable=readable, base=base)


def _event_times(reader: _Reader, lo: UtcTime, hi: UtcTime) -> list[UtcTime]:
    """読み取りの状態が変わりうる時刻（`[lo, hi]`）。"""
    schedule = reader.schedule
    times: set[UtcTime] = {lo}
    for start in schedule.calendar.expected_bar_starts(
        schedule.timeframe_def, Interval(start=lo - timedelta(days=7), end=hi)
    ):
        end = reader._end(start)
        if end is None:  # pragma: no cover - 期待足には区間がある
            continue
        candidates = [end]
        if start in reader.no_record:
            candidates.append(reader.publication_at(start))
        visible = reader.visible_at(start)
        if visible is not None:
            candidates.append(visible)
        times.update(moment for moment in candidates if lo <= moment <= hi)
    return sorted(times, key=lambda value: value.value)


def _chunked_events(reader: _Reader, lo: UtcTime, hi: UtcTime) -> Iterator[UtcTime]:
    """`_event_times` を 30 日ずつ作って順に返す（snapshot の終わりまで一度に作らない）。"""
    cursor = lo
    step = timedelta(days=30)
    previous: UtcTime | None = None
    while cursor <= hi:
        upper = min(cursor + step, hi, key=lambda value: value.value)
        for moment in _event_times(reader, cursor, upper):
            if previous is not None and moment <= previous:
                continue
            previous = moment
            yield moment
        if upper == hi:
            return
        cursor = upper


def read_resets(
    schedule: SeriesSchedule,
    spec: ReadWindowSpec,
    bars: Sequence[Bar],
    no_record: frozenset[UtcTime],
    run_interval: Interval,
    published: Mapping[BarKey, UtcTime] | None = None,
) -> tuple[ReadReset, ...]:
    """1 系列 × 1 窓の表22 の行（D06 §18.10、D12-127）。

    run の準備に効く行だけを返す: 準備不足になる時刻が run の終わり以前で、復帰する時刻が run の
    始まりより後（または snapshot の終わりまで復帰しない）もの。run の開始前に起きて run の中まで
    影響するものも含める（D12-119）。`bars` は系列の作られた足（時刻順）、`no_record` は全分無記録の
    足の開始。
    """
    if not bars or not no_record:
        return ()
    reader = _Reader(schedule, spec, bars, no_record, published or {})
    data_end = max(
        [bars[-1].bar_end]
        + [end for start in no_record if (end := reader._end(start)) is not None],
        key=lambda value: value.value,
    )
    last_moment = max(
        [schedule.scheduled_at(data_end)]
        + [reader.publication_at(start) for start in no_record]
        + [visible for bar in bars if (visible := reader.visible_at(bar.bar_start)) is not None],
        key=lambda value: value.value,
    )
    # 開始: run の始まりで準備不足の途中なら、その準備不足が始まった所まで遡る。
    lo = run_interval.start
    span = timedelta(days=7)
    while lo > reader.data_start:
        state = reader.state(lo)
        if not state.culprits and (state.readable or state.base is None):
            break
        lo = max(lo - span, reader.data_start, key=lambda value: value.value)
        span = span * 2
    out: list[ReadReset] = []
    first_seen: dict[UtcTime, UtcTime] = {}
    in_run = False
    for at in _chunked_events(reader, lo, last_moment):
        state = reader.state(at)
        if state.culprits:
            in_run = True
            for culprit in state.culprits:
                first_seen.setdefault(culprit, at)
            continue
        if in_run and state.readable:
            for culprit, since in first_seen.items():
                out.append(
                    ReadReset(
                        no_record_bar_start=culprit,
                        insufficient_from=since,
                        ready_bar_start=state.base,
                        ready_at=at,
                    )
                )
            first_seen = {}
            in_run = False
        if not in_run and at > run_interval.end:
            break
    for culprit, since in first_seen.items():
        out.append(
            ReadReset(
                no_record_bar_start=culprit,
                insufficient_from=since,
                ready_bar_start=None,
                ready_at=None,
            )
        )
    return tuple(
        sorted(
            (
                item
                for item in out
                if item.insufficient_from <= run_interval.end
                and (item.ready_at is None or item.ready_at > run_interval.start)
            ),
            key=lambda item: item.no_record_bar_start.value,
        )
    )
