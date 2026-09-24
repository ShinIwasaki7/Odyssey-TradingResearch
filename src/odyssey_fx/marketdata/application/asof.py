"""判断時点の as-of 読み取りと執行系列ビュー（D03 §6）。

`AsOfView` は「判断時刻 `at` の時点で知れる情報だけ」を返す。未来参照は構造的に不可能で、
すべての操作が `at` を必須にし、`available_at > at` の足は返さない（D03 §6.2）。

読める partition は構築時に固定する（D03 §6.1）。範囲外の系列・区間への要求は
`HoldoutAccessViolation`（構造エラー。入力欠損ではない）。承認されていない snapshot は
`SnapshotNotApproved` で拒否する（D03 §3.7.1 の3）。

`MarketDataView`（`strategy.runtime.ports`）と `ExecutionSeries`
（`backtest.application.ports`）のポート定義は、それぞれの利用側パッケージが定義する
（D01 §4）。定義がまだ存在しない段階なので import せず、D03 §6.2・§6.3 のメソッド名で
構造的に満たす（D01 §2.2 規則7）。

戦略ビューと執行系列ビューは**別インスタンス**にする（D03 §6.3）。執行系列は足の始値だけを
先に公開する段階（D03 §7.3）を持つため、戦略ビューには渡さない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from odyssey_fx.common.money import Price
from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.snapshot_access import (
    PartitionedBars,
    ReadableSnapshot,
    require_readable_snapshot,
)
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.publication_log import PublicationLog
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest

__all__ = [
    "AsOfView",
    "BarsWindow",
    "BarsWindowLike",
    "DurationWindow",
    "DurationWindowLike",
    "ExecutionSeriesView",
    "HistoryWindowLike",
    "MissingInput",
]

#: 予定上の足を先へ辿る上限の本数。開場中の足が1本も無いまま進む区間は休場であり、
#: 15分足ならおよそ52日ぶんに当たる。run 区間より長い休場は扱わないので、これを超えたら
#: 「次の予定は無い」として `None` を返す。
_SCHEDULE_PROBE_LIMIT: Final = 5000


@dataclass(frozen=True, slots=True)
class MissingInput:
    """入力が得られなかったことと、その診断コード（D03 §6.2、D02 §8.3）。

    構造エラー（例外）とは区別する。戦略ランタイムが `on_missing`（見送り / 待機 /
    過去値使用 / 失敗）で扱う（上位設計書 §4.3.10）。
    """

    reason: MissingInputReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, MissingInputReason):
            raise MarketDataValueError("MissingInput.reason must be a MissingInputReason")


@runtime_checkable
class BarsWindowLike(Protocol):
    """本数で指定する履歴窓として受け取れる形（D03 §6.2 v1.5）。"""

    @property
    def count(self) -> int: ...


@runtime_checkable
class DurationWindowLike(Protocol):
    """経過時間で指定する履歴窓として受け取れる形（D03 §6.2 v1.5）。"""

    @property
    def duration(self) -> timedelta: ...


#: `history()` が受け取る履歴窓（D03 §6.2 v1.5、2026-09-22 の人間の決定）。
#:
#: 呼び出し側（戦略ランタイム）は `marketdata.application` を参照できない（契約 F2）。
#: 具体クラスを要求すると、両方を参照できる層で窓を言い換えるほかなくなるので、**構造**
#: （本数か経過時間を読み出せること）だけを要求する。下の `BarsWindow` /
#: `DurationWindow` は `marketdata` 自身が窓を組み立てるときの具体型である。
HistoryWindowLike = BarsWindowLike | DurationWindowLike


@dataclass(frozen=True, slots=True)
class BarsWindow:
    """本数で指定する履歴窓（上位設計書 §4.3.10）。

    `count` は必要本数を表す正の整数。揃わない場合に期間を短縮した計算へ暗黙に変更しない。
    """

    count: int

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise MarketDataValueError(f"BarsWindow.count must be an int, got {self.count!r}")
        if self.count < 1:
            raise MarketDataValueError(f"BarsWindow.count must be >= 1, got {self.count}")


@dataclass(frozen=True, slots=True)
class DurationWindow:
    """経過時間で指定する履歴窓（D03 §6.2）。

    末尾足の終了時刻を `at'` として、`bar_end` が `(at' - duration, at']` に入る足。範囲内の
    期待足がすべて必要で、データ開始前に及べば助走不足、全期間休場で空なら欠損として扱う
    （空履歴からの計算は許さない）。
    """

    duration: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.duration, timedelta):
            raise MarketDataValueError("DurationWindow.duration must be a timedelta")
        if self.duration <= timedelta(0):
            raise MarketDataValueError(f"DurationWindow.duration must be > 0, got {self.duration}")


@dataclass(frozen=True, slots=True)
class AsOfView:
    """判断時点の as-of 読み取り（D03 §6）。

    `MarketDataView`（`strategy.runtime.ports`）を構造的に満たす。
    """

    snapshot: ReadableSnapshot
    allowed_partitions: frozenset[PartitionId]
    schedules: Mapping[SeriesId, SeriesSchedule]
    partition_bars: Mapping[PartitionId, Sequence[Bar]]
    publication_log: PublicationLog = PublicationLog()

    def __post_init__(self) -> None:
        if not isinstance(self.publication_log, PublicationLog):
            raise MarketDataValueError("AsOfView.publication_log must be a PublicationLog")
        frozen = require_readable_snapshot(
            self.snapshot,
            self.allowed_partitions,
            label="AsOfView",
            partition_bars=self.partition_bars,
        )
        # 呼び出し元が渡した可変な mapping を保持したままにすると、構築時の照合をすり抜けた
        # 後で中身を差し替えられる（D03 §6.1）。不変な写しへ置き換える。
        #
        # 公開予定も同じ。通常遅延 5 分の予定でビューを作ったあと、元の辞書を遅延 0 の
        # 予定へ差し替えると、足の終了と同時にその足が見えてしまう（D03 §3.5・§6.2 の
        # 先読み禁止に反する）。`SeriesSchedule` 自体は frozen なので、値の差し替えだけを
        # 防げばよい。
        object.__setattr__(self, "partition_bars", frozen)
        object.__setattr__(self, "schedules", MappingProxyType(dict(self.schedules)))

    @property
    def manifest(self) -> SnapshotManifest:
        """読んでいる snapshot の manifest。"""
        return self.snapshot.manifest

    # --- 内部 ---------------------------------------------------------------

    def _bars(self) -> PartitionedBars:
        """許可された partition の読み取り面。

        `partition_bars` は構築時に不変な写しへ置き換えてあるので、ここで組み立て直しても
        外部の変更は入らない。frozen dataclass かつ `slots=True` なので、読み取り面そのもの
        をキャッシュする属性は持てない。
        """
        return PartitionedBars(self.partition_bars, self.allowed_partitions)

    def _schedule(self, series: SeriesId) -> SeriesSchedule:
        schedule = self.schedules.get(series)
        if schedule is None:
            raise MarketDataValueError(f"no publication schedule was supplied for {series}")
        return schedule

    def _available_at(self, bar: Bar) -> UtcTime:
        """その足が実際に利用可能になる時刻（D03 §3.5・§6.2）。

        下限は**通常の公開予定** `bar_end + normal_publication_delay`（D03 §3.5）である。
        正規化の段階では足自身の `available_at` を足の終了時刻に置くので、これを下限に
        しないと、通常遅延のある系列を「足の終了と同時に見える」と扱ってしまう。公開
        フィードは予定どおり遅らせるため、その差の窓で as-of ビューだけが先を読むことに
        なる（D03 §6.2 の「未来参照は構造的に不可能」に反する）。

        実現した公開記録（`PublicationLog`）があればそれを使う。記録は遅延シナリオの適用
        結果であり、遅延は非負なので（D03 §3.6）通常の公開予定を下回らない。下回る記録は
        設定の誤りなので構造エラーで拒否する。
        """
        scheduled = self._schedule(bar.series).scheduled_at(bar.bar_end)
        recorded = self.publication_log.available_at(bar.key)
        if recorded is None:
            return max(bar.available_at, scheduled, key=lambda value: value.value)
        if recorded < scheduled:
            raise MarketDataValueError(
                f"the publication log makes {bar.key} available at {recorded}, before its"
                f" scheduled time {scheduled}; delays are non-negative (D03 §3.5・§3.6)"
            )
        return recorded

    def _visible_bars(self, series: SeriesId, at: UtcTime) -> tuple[Bar, ...]:
        """`at` の時点で見えている足（`available_at <= at`）。"""
        return tuple(
            bar for bar in self._bars().require_series(series) if self._available_at(bar) <= at
        )

    # --- 読み取り操作（D03 §6.2）-------------------------------------------

    def expected_latest_key(self, series: SeriesId, at: UtcTime) -> BarKey | None:
        """判断時刻に対し存在すべき最新足の鍵（D03 §6.2）。

        カレンダーと時間足定義から機械的に決まる。データが届いているかは見ない。
        期待される足が1本もない場合（データ開始前など）は `None`。
        """
        _require_utc(at, "expected_latest_key")
        schedule = self._schedule(series)
        start = schedule.expected_latest_bar_start(at)
        if start is None:
            return None
        return BarKey(series=series, bar_start=start)

    def latest_available(self, series: SeriesId, at: UtcTime) -> Bar | MissingInput:
        """`available_at <= at` の足のうち、期待される最新足（D03 §6.2）。

        期待足が未到着なら `LATEST_BAR_UNAVAILABLE` を返す。**古い足へ黙って戻らない**。
        """
        _require_utc(at, "latest_available")
        expected = self.expected_latest_key(series, at)
        if expected is None:
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        bars_view = self._bars()
        if bars_view.starts_before_data(series, expected.bar_start):
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        bars_view.require_covered(series, expected.bar_start)
        for bar in self._visible_bars(series, at):
            if bar.bar_start == expected.bar_start:
                return bar
        return MissingInput(MissingInputReason.LATEST_BAR_UNAVAILABLE)

    def bar(self, series: SeriesId, bar_start: UtcTime, at: UtcTime) -> Bar | MissingInput:
        """指定した足（D03 §6.2）。`available_at > at` なら不可視。"""
        _require_utc(at, "bar")
        _require_utc(bar_start, "bar")
        self._bars().require_covered(series, bar_start)
        for candidate in self._visible_bars(series, at):
            if candidate.bar_start == bar_start:
                return candidate
        return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)

    def freshness(self, series: SeriesId, bar: Bar) -> UtcTime:
        """鮮度基準時刻（D03 §6.2）。確定足は `bar_end`。

        許容する古さ（`max_age`）の追加検査は戦略ランタイムが行い、ビューは基準時刻だけを
        返す（上位設計書 §4.3.10）。古い足が遅れて到着したから新鮮になった、とは扱わない。
        """
        if bar.series != series:
            raise MarketDataValueError(
                f"freshness() was given a bar of {bar.series} for series {series}"
            )
        return bar.bar_end

    def history(
        self,
        series: SeriesId,
        window: HistoryWindowLike,
        at: UtcTime,
        *,
        end_offset_bars: int = 0,
    ) -> tuple[Bar, ...] | MissingInput:
        """履歴窓を読む（D03 §6.2）。

        期待される最新足から `end_offset_bars` 本手前を末尾とし、本数窓なら `count` 本、
        経過時間窓なら末尾足の終了時刻から遡る範囲を返す。**窓内の期待足がすべて存在し
        利用可能でなければ** `INPUT_MISSING_OR_INVALID`。データ開始前を含めば
        `WARMUP_INSUFFICIENT`（欠損を削除して窓外の古い足を繰り上げることはしない）。
        """
        _require_utc(at, "history")
        _require_window(window)
        _require_offset(end_offset_bars, "history")
        expected = self.expected_latest_key(series, at)
        if expected is None:
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        return self._window_from(series, window, expected.bar_start, at, end_offset_bars)

    def history_ending_at(
        self,
        series: SeriesId,
        window: HistoryWindowLike,
        base_bar_start: UtcTime,
        at: UtcTime,
        *,
        end_offset_bars: int = 0,
    ) -> tuple[Bar, ...] | MissingInput:
        """基準の足を指定して読む履歴窓（D03 §6.2 v1.6）。

        `history` との違いは基準の求め方だけである。`history` は「`at` における期待される
        最新足」を基準にし、本操作は**呼び出し側が渡した足**を基準にする。`end_offset_bars`
        の適用は `history` とまったく同じで、基準の足から `end_offset_bars` 本手前が窓の
        末尾になる。待機していた評価が再開したとき、待機に入ったときに固定した足から同じ窓
        を読み直すための操作である（D05 §6.8 の手順4）。

        - 基準の足が `at` の時点で不可視なら `LATEST_BAR_UNAVAILABLE`。
        - `at` における期待される最新足より後の足を基準にしたら、未来参照なので構造エラー。
        """
        _require_utc(at, "history_ending_at")
        _require_utc(base_bar_start, "history_ending_at")
        _require_window(window)
        _require_offset(end_offset_bars, "history_ending_at")
        expected = self.expected_latest_key(series, at)
        if expected is None:
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        if expected.bar_start < base_bar_start:
            raise MarketDataValueError(
                f"history_ending_at() was asked for a window based on {base_bar_start}, after"
                f" the latest bar expected at {at} ({expected.bar_start}); that would read the"
                " future (D03 §6.2)"
            )
        bars_view = self._bars()
        if bars_view.starts_before_data(series, base_bar_start):
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        bars_view.require_covered(series, base_bar_start)
        if not any(bar.bar_start == base_bar_start for bar in self._visible_bars(series, at)):
            return MissingInput(MissingInputReason.LATEST_BAR_UNAVAILABLE)
        return self._window_from(series, window, base_bar_start, at, end_offset_bars)

    def previous_available(
        self,
        series: SeriesId,
        before_bar_start: UtcTime,
        at: UtcTime,
        *,
        max_lookback: HistoryWindowLike,
    ) -> Bar | MissingInput:
        """指定した足の1本手前から古い側へたどり、最初に見つかった有効な足（D03 §6.2 v1.6）。

        過去値へ遡る欠損方針（`USE_PREVIOUS`、D05 §6.9）のための操作である。戦略側は
        カレンダーにも時間足定義にも到達できないので、前の足の開始時刻を自分で数えられない。

        - `max_lookback` が本数なら、予定上の足をその本数までたどる。
        - 経過時間なら、`at` からその時間だけさかのぼった範囲（足の終了時刻が
          `(at - duration, at]` に入る足。`history` の経過時間窓と同じ数え方）までたどる。
          **経過時間を本数へ直すのはビューの側**である。
        - 範囲に有効な足が無ければ `INPUT_MISSING_OR_INVALID`、見つかる前にデータ開始前へ
          及べば `WARMUP_INSUFFICIENT`。上限に既定値は置かない。
        """
        _require_utc(at, "previous_available")
        _require_utc(before_bar_start, "previous_available")
        _require_window(max_lookback)
        schedule = self._schedule(series)
        definition = schedule.timeframe_def
        bars_view = self._bars()
        visible = {bar.bar_start: bar for bar in self._visible_bars(series, at)}
        remaining: int | None = None
        lower_bound: UtcTime | None = None
        if isinstance(max_lookback, BarsWindowLike):
            remaining = max_lookback.count
        else:
            lower_bound = at - max_lookback.duration
        probe = definition.boundaries(before_bar_start - timedelta(microseconds=1)).start
        for _ in range(_SCHEDULE_PROBE_LIMIT):
            interval = definition.expected_interval(schedule.calendar, probe)
            if interval is not None:
                if remaining is not None:
                    if remaining <= 0:
                        return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)
                    remaining -= 1
                elif lower_bound is not None and interval.end <= lower_bound:
                    return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)
                if bars_view.starts_before_data(series, interval.start):
                    return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
                bars_view.require_covered(series, interval.start)
                found = visible.get(interval.start)
                if found is not None:
                    return found
            probe = definition.boundaries(probe - timedelta(microseconds=1)).start
        return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)  # pragma: no cover

    def _window_from(
        self,
        series: SeriesId,
        window: HistoryWindowLike,
        base_start: UtcTime,
        at: UtcTime,
        end_offset_bars: int,
    ) -> tuple[Bar, ...] | MissingInput:
        """基準の足から窓を切る（`history` と `history_ending_at` に共通の手順）。"""
        schedule = self._schedule(series)
        # 期待される足の開始時刻を新しい順に並べる。欠損を飛ばさず、期待どおりの並びを作る。
        expected_starts = _expected_starts_backwards(
            schedule, base_start, _needed_bars(window, end_offset_bars)
        )
        if expected_starts is None:
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        if end_offset_bars:
            if len(expected_starts) <= end_offset_bars:
                return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
            expected_starts = expected_starts[end_offset_bars:]

        wanted: tuple[UtcTime, ...]
        if isinstance(window, BarsWindowLike):
            if len(expected_starts) < window.count:
                return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
            wanted = expected_starts[: window.count]
        else:
            selected = _duration_selection(schedule, expected_starts, window)
            if selected is None:
                return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
            if not selected:
                return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)
            wanted = selected

        bars_view = self._bars()
        for start in wanted:
            # データ開始前に及ぶ窓は助走不足。分類の境界を越える要求（構造エラー）とは
            # 区別する（上位設計書 §4.3.10）。
            if bars_view.starts_before_data(series, start):
                return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
            bars_view.require_covered(series, start)
        visible = {bar.bar_start: bar for bar in self._visible_bars(series, at)}
        collected: list[Bar] = []
        for start in wanted:
            found = visible.get(start)
            if found is None:
                return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)
            collected.append(found)
        collected.reverse()  # 古い順に返す。
        return tuple(collected)


def _require_offset(end_offset_bars: int, operation: str) -> None:
    if isinstance(end_offset_bars, bool) or not isinstance(end_offset_bars, int):
        raise MarketDataValueError(
            f"{operation}() end_offset_bars must be an int, got {end_offset_bars!r}"
        )
    if end_offset_bars < 0:
        raise MarketDataValueError(
            f"{operation}() end_offset_bars must be >= 0, got {end_offset_bars}"
        )


def _require_utc(value: UtcTime, operation: str) -> None:
    if not isinstance(value, UtcTime):
        raise MarketDataValueError(f"{operation}() requires a UtcTime")


def _require_window(window: HistoryWindowLike) -> None:
    """履歴窓が受け取れる形であることを確かめる（D03 §6.2 v1.5）。

    構造だけを要求するので、具体クラスの `__post_init__` は通っていない。本数窓の本数が
    解決済みの正の整数であることは、ここで確かめる（未解決のパラメータ参照を既定値で
    埋めない）。
    """
    if isinstance(window, BarsWindowLike):
        count = window.count
        if isinstance(count, bool) or not isinstance(count, int):
            raise MarketDataValueError(
                f"history() window count must be a resolved int, got {count!r}"
            )
        if count < 1:
            raise MarketDataValueError(f"history() window count must be >= 1, got {count}")
        return
    if isinstance(window, DurationWindowLike):
        duration = window.duration
        if not isinstance(duration, timedelta):
            raise MarketDataValueError(
                f"history() window duration must be a timedelta, got {duration!r}"
            )
        if duration <= timedelta(0):
            raise MarketDataValueError(f"history() window duration must be > 0, got {duration}")
        return
    raise MarketDataValueError(
        f"history() window must expose a bar count or a duration, got {type(window).__name__}"
    )


def _needed_bars(window: HistoryWindowLike, end_offset_bars: int) -> int:
    """遡って探索する足の本数の上限。

    本数窓は必要本数そのもの、経過時間窓は窓の長さを名目長で割った概算に余裕を足した値
    （DST で足の長さが伸縮するため、概算にしかならない）。
    """
    if isinstance(window, BarsWindowLike):
        return window.count + end_offset_bars
    return end_offset_bars + 1  # 経過時間窓は `_duration_selection` が必要なだけ伸ばす。


def _expected_starts_backwards(
    schedule: SeriesSchedule, latest_start: UtcTime, count: int
) -> tuple[UtcTime, ...] | None:
    """期待される足の開始時刻を、新しい順に `count` 本ぶん返す。

    データ開始前まで遡って本数が揃わない場合は `None`（助走不足）。探索は高々 `count` の
    数十倍の境界までとし、長い休場が続いても終了する。
    """
    starts: list[UtcTime] = []
    probe = latest_start
    definition = schedule.timeframe_def
    guard = max(count * 50, 500)
    for _ in range(guard):
        interval = definition.expected_interval(schedule.calendar, probe)
        if interval is not None:
            starts.append(interval.start)
            if len(starts) >= count:
                return tuple(starts)
        probe = definition.boundaries(probe - timedelta(microseconds=1)).start
    return None


def _duration_selection(
    schedule: SeriesSchedule,
    expected_starts: tuple[UtcTime, ...],
    window: DurationWindowLike,
) -> tuple[UtcTime, ...] | None:
    """経過時間窓に入る足の開始時刻を新しい順に返す（D03 §6.2）。

    末尾足の終了時刻を `at'` として、`bar_end` が `(at' - duration, at']` に入る足。範囲内の
    期待足をすべて集めるため、必要なだけ遡って期待足を追加する。データ開始前に及べば
    `None`（助走不足）。
    """
    if not expected_starts:  # pragma: no cover - 呼び出し側が空でないことを保証する
        return ()
    definition = schedule.timeframe_def
    last_interval = definition.expected_interval(schedule.calendar, expected_starts[0])
    if last_interval is None:  # pragma: no cover - 期待足なので必ず存在する
        return ()
    lower_bound = last_interval.end - window.duration

    selected: list[UtcTime] = []
    probe = expected_starts[0]
    guard = 100000
    for _ in range(guard):
        interval = definition.expected_interval(schedule.calendar, probe)
        if interval is not None:
            if interval.end <= lower_bound:
                return tuple(selected)
            selected.append(interval.start)
        probe = definition.boundaries(probe - timedelta(microseconds=1)).start
    return None  # pragma: no cover - guard に達するほど長い窓は使わない


@dataclass(frozen=True, slots=True)
class ExecutionSeriesView:
    """執行系列のビュー（D03 §6.3）。

    `ExecutionSeries`（`backtest.application.ports`）を同じ snapshot から構造的に満たす。
    戦略ビューとは**別インスタンス**で、足の始値だけを先に公開する段階（D03 §7.3）を持つ
    ため戦略側には渡さない。

    `schedule` は**予定上の足**を答えるために持つ。注文の約定候補は予定から決めるので
    （D06 §5.3）、実ファイルに足があるかどうかを候補選択に混ぜない。
    """

    snapshot: ReadableSnapshot
    series: SeriesId
    allowed_partitions: frozenset[PartitionId]
    partition_bars: Mapping[PartitionId, Sequence[Bar]]
    schedule: SeriesSchedule

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("ExecutionSeriesView.series must be a SeriesId")
        if not isinstance(self.schedule, SeriesSchedule):
            raise MarketDataValueError("ExecutionSeriesView.schedule must be a SeriesSchedule")
        if self.schedule.series != self.series:
            raise MarketDataValueError(
                f"ExecutionSeriesView of {self.series} was given a schedule for"
                f" {self.schedule.series}"
            )
        # 戦略側のビューと同じ関門を通す。執行系列だけ検査が緩いと、manifest に無い
        # partition を渡して未記録のデータを読む経路が残ってしまう。
        frozen = require_readable_snapshot(
            self.snapshot,
            self.allowed_partitions,
            label="ExecutionSeriesView",
            partition_bars=self.partition_bars,
        )
        object.__setattr__(self, "partition_bars", frozen)

    @property
    def manifest(self) -> SnapshotManifest:
        """読んでいる snapshot の manifest。"""
        return self.snapshot.manifest

    def _bars(self) -> tuple[Bar, ...]:
        return PartitionedBars(self.partition_bars, self.allowed_partitions).require_series(
            self.series
        )

    def bar(self, bar_key: BarKey) -> Bar | None:
        """執行足そのもの（D03 §6.3）。

        執行モデルは足内約定を解決するために高値・安値・終値を必要とするので、ここでは
        `available_at` による遮蔽を行わない。足の始値だけが先に処理可能になる段階は、
        公開フィードの `ExecutionOpen` と `ExecutionBarComplete` の順序が表す（D03 §7.3）。
        """
        if bar_key.series != self.series:
            raise MarketDataValueError(
                f"ExecutionSeriesView of {self.series} was asked for {bar_key.series}"
            )
        for bar in self._bars():
            if bar.bar_start == bar_key.bar_start:
                return bar
        return None

    def open_of(self, bar_key: BarKey) -> Price | None:
        """執行足の始値（D03 §6.3）。足がなければ `None`。"""
        found = self.bar(bar_key)
        return None if found is None else found.open

    def next_bar_key_after(self, moment: UtcTime) -> BarKey | None:
        """`moment` より後に始まる最初の執行足の鍵（D03 §6.3）。

        注文の約定候補を選ぶために使う。実ファイルの欠損を候補選択に使わないよう、足が
        存在しない区間は飛ばさず `None` を返す判断は呼び出し側（受付層）が行う。
        """
        _require_utc(moment, "next_bar_key_after")
        for bar in self._bars():
            if moment < bar.bar_start:
                return bar.key
        return None

    def next_scheduled_open_after(self, moment: UtcTime) -> BarKey | None:
        """`moment` より後に始まる最初の**予定上の**執行足の鍵（D03 §6.3）。

        カレンダーと時間足定義だけから決める。実ファイルに足があるかどうかは見ない。
        注文の約定候補はこの操作で選ぶ（D06 §5.3 の「実ファイルの欠損を候補選択に
        使わない」）。実在する足から選ぶと、休場でない区間で足が1本欠けているだけで候補が
        先へずれ、データの欠損が受付結果を変えてしまう。

        休場が続く区間は飛ばして次の開場中の足を返す。`_SCHEDULE_PROBE_LIMIT` 本ぶん進んでも
        開場中の足が無ければ `None`（run 区間より長い休場は扱わない）。
        """
        _require_utc(moment, "next_scheduled_open_after")
        definition = self.schedule.timeframe_def
        probe = definition.boundaries(moment).end
        for _ in range(_SCHEDULE_PROBE_LIMIT):
            interval = definition.expected_interval(self.schedule.calendar, probe)
            if interval is not None and moment < interval.start:
                return BarKey(series=self.series, bar_start=interval.start)
            probe = definition.boundaries(probe).end
        return None
