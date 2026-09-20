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

from odyssey_fx.common.money import Price
from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.snapshot_access import (
    PartitionedBars,
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
    "DurationWindow",
    "ExecutionSeriesView",
    "MissingInput",
]


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

    manifest: SnapshotManifest
    allowed_partitions: frozenset[PartitionId]
    schedules: Mapping[SeriesId, SeriesSchedule]
    partition_bars: Mapping[PartitionId, Sequence[Bar]]
    publication_log: PublicationLog = PublicationLog()

    def __post_init__(self) -> None:
        if not isinstance(self.publication_log, PublicationLog):
            raise MarketDataValueError("AsOfView.publication_log must be a PublicationLog")
        require_readable_snapshot(
            self.manifest,
            self.allowed_partitions,
            label="AsOfView",
            partition_bars=self.partition_bars,
        )

    # --- 内部 ---------------------------------------------------------------

    def _bars(self) -> PartitionedBars:
        """許可された partition の読み取り面。

        frozen dataclass なので構築時にキャッシュせず、呼ばれるたびに組み立てる。
        （`slots=True` と frozen の組合せでは後からの属性代入ができないため。）
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
        window: BarsWindow | DurationWindow,
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
        if isinstance(end_offset_bars, bool) or not isinstance(end_offset_bars, int):
            raise MarketDataValueError(
                f"history() end_offset_bars must be an int, got {end_offset_bars!r}"
            )
        if end_offset_bars < 0:
            raise MarketDataValueError(
                f"history() end_offset_bars must be >= 0, got {end_offset_bars}"
            )

        schedule = self._schedule(series)
        expected = self.expected_latest_key(series, at)
        if expected is None:
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)

        # 期待される足の開始時刻を新しい順に並べる。欠損を飛ばさず、期待どおりの並びを作る。
        expected_starts = _expected_starts_backwards(
            schedule, expected.bar_start, _needed_bars(window, end_offset_bars)
        )
        if expected_starts is None:
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        if end_offset_bars:
            if len(expected_starts) <= end_offset_bars:
                return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
            expected_starts = expected_starts[end_offset_bars:]

        wanted: tuple[UtcTime, ...]
        if isinstance(window, BarsWindow):
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


def _require_utc(value: UtcTime, operation: str) -> None:
    if not isinstance(value, UtcTime):
        raise MarketDataValueError(f"{operation}() requires a UtcTime")


def _needed_bars(window: BarsWindow | DurationWindow, end_offset_bars: int) -> int:
    """遡って探索する足の本数の上限。

    本数窓は必要本数そのもの、経過時間窓は窓の長さを名目長で割った概算に余裕を足した値
    （DST で足の長さが伸縮するため、概算にしかならない）。
    """
    if isinstance(window, BarsWindow):
        return window.count + end_offset_bars
    if not isinstance(window, DurationWindow):
        raise MarketDataValueError(
            f"history() window must be a BarsWindow or DurationWindow, got {type(window).__name__}"
        )
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
    window: DurationWindow,
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
    """

    manifest: SnapshotManifest
    series: SeriesId
    allowed_partitions: frozenset[PartitionId]
    partition_bars: Mapping[PartitionId, Sequence[Bar]]

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("ExecutionSeriesView.series must be a SeriesId")
        # 戦略側のビューと同じ関門を通す。執行系列だけ検査が緩いと、manifest に無い
        # partition を渡して未記録のデータを読む経路が残ってしまう。
        require_readable_snapshot(
            self.manifest,
            self.allowed_partitions,
            label="ExecutionSeriesView",
            partition_bars=self.partition_bars,
        )

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
