"""公開予定と遅延シナリオ（D03 §3.5・§3.6）。

3つを分けて持つ（上位設計書 §4.3.13）。

1. 足区間を決める定義（`TimeframeDefinition` と `TradingCalendar`）。
2. 通常いつ利用可能になるはずかを決める公開予定（`SeriesSchedule`）。
3. 今回のシミュレーションで実際にいつ公開するかを決める遅延シナリオ（`DelayScenario`）。

異常な遅延を通常の公開予定へ書き換えて欠損判定から隠さない、という分離である。

遅延は**非負**に限る（D03 §3.5・§3.6）。負の遅延を許すと `available_at` が足の終了前に
なり、設定の誤りから未来の値が見える先読みが起こりうるため、構築時に拒否する。
`available_at = scheduled_at + delay` であり、`scheduled_at = bar_end + 通常遅延` なので
`available_at >= bar_end` が常に成り立つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError, UnsupportedCapability
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = [
    "DelayRule",
    "DelayScenario",
    "FixedSeriesDelay",
    "InjectedBarDelay",
    "SeededRandomDelay",
    "SeriesSchedule",
]


def _require_non_negative_delay(delay: timedelta, label: str) -> None:
    """遅延が非負の `timedelta` であることを確かめる（D03 §3.5・§3.6）。"""
    if not isinstance(delay, timedelta):
        raise MarketDataValueError(f"{label} must be a timedelta, got {type(delay).__name__}")
    if delay < timedelta(0):
        raise MarketDataValueError(
            f"{label} must be >= 0, got {delay};"
            " a negative delay would make a bar visible before it ends (D03 §3.6)"
        )


@dataclass(frozen=True, slots=True)
class SeriesSchedule:
    """系列の公開予定（D03 §3.5）。

    `normal_publication_delay` は非負。初版はすべて 0（足の終了と同時に公開される）。
    """

    series: SeriesId
    timeframe_def: TimeframeDefinition
    calendar: TradingCalendar
    normal_publication_delay: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("SeriesSchedule.series must be a SeriesId")
        if not isinstance(self.timeframe_def, TimeframeDefinition):
            raise MarketDataValueError("SeriesSchedule.timeframe_def must be a TimeframeDefinition")
        if not isinstance(self.calendar, TradingCalendar):
            raise MarketDataValueError("SeriesSchedule.calendar must be a TradingCalendar")
        if self.timeframe_def.ref.id != self.series.timeframe.id:
            raise MarketDataValueError(
                f"SeriesSchedule timeframe definition {self.timeframe_def.ref.id!r} does not"
                f" match the series timeframe {self.series.timeframe.id!r}"
            )
        _require_non_negative_delay(
            self.normal_publication_delay, "SeriesSchedule.normal_publication_delay"
        )

    @property
    def calendar_ref(self) -> tuple[str, int]:
        """カレンダーの版参照（manifest の `conversion` に記録する）。"""
        return (self.calendar.id, self.calendar.version)

    def scheduled_at(self, bar_end: UtcTime) -> UtcTime:
        """通常の公開予定 `bar_end + normal_publication_delay`（D03 §3.5）。"""
        if not isinstance(bar_end, UtcTime):
            raise MarketDataValueError("scheduled_at requires a UtcTime")
        return bar_end + self.normal_publication_delay

    def expected_latest_bar_start(self, at: UtcTime) -> UtcTime | None:
        """判断時刻 `at` に対し「期待される最新の確定足」の開始時刻（D03 §3.5）。

        `bar_end <= at` かつカレンダー上存在すべき最後の足。足が1本も存在しない期間
        （長い休場の直後など）では `None` を返す。探索は高々 `at` の 60 日前までとする
        （それ以前まで遡って足が1本もない系列は、そもそも運用対象にしない）。
        """
        if not isinstance(at, UtcTime):
            raise MarketDataValueError("expected_latest_bar_start requires a UtcTime")
        probe = self.timeframe_def.boundaries(at).start
        limit = at - timedelta(days=60)
        while probe >= limit:
            interval = self.timeframe_def.expected_interval(self.calendar, probe)
            if interval is not None and interval.end <= at:
                return interval.start
            probe = self.timeframe_def.boundaries(probe - timedelta(microseconds=1)).start
        return None


@dataclass(frozen=True, slots=True)
class FixedSeriesDelay:
    """系列全体に固定の遅延を与える規則（D03 §3.6）。"""

    series: SeriesId
    delay: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("FixedSeriesDelay.series must be a SeriesId")
        _require_non_negative_delay(self.delay, "FixedSeriesDelay.delay")

    def delay_for(self, series: SeriesId, bar_start: UtcTime) -> timedelta | None:
        """この規則が与える遅延（対象外なら `None`）。"""
        del bar_start  # 系列全体に掛かるので足の開始時刻は使わない。
        return self.delay if series == self.series else None


@dataclass(frozen=True, slots=True)
class InjectedBarDelay:
    """指定した1本の足だけに遅延を与える規則（D03 §3.6）。"""

    series: SeriesId
    bar_start: UtcTime
    delay: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("InjectedBarDelay.series must be a SeriesId")
        if not isinstance(self.bar_start, UtcTime):
            raise MarketDataValueError("InjectedBarDelay.bar_start must be a UtcTime")
        _require_non_negative_delay(self.delay, "InjectedBarDelay.delay")

    def delay_for(self, series: SeriesId, bar_start: UtcTime) -> timedelta | None:
        """この規則が与える遅延（対象外なら `None`）。"""
        if series == self.series and bar_start == self.bar_start:
            return self.delay
        return None


@dataclass(frozen=True, slots=True)
class SeededRandomDelay:
    """seed 固定の確率的遅延（D03 §3.6）。**初版は能力検査で拒否する**。

    型としては置くが、`DelayScenario` に載せた時点で `UnsupportedCapability` になる。
    将来対応するときは、seed から遅延列を決定論的に導く規則を D06 で決める。
    """

    series: SeriesId
    seed: int
    max_delay: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("SeededRandomDelay.series must be a SeriesId")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise MarketDataValueError(f"SeededRandomDelay.seed must be an int, got {self.seed!r}")
        _require_non_negative_delay(self.max_delay, "SeededRandomDelay.max_delay")

    def delay_for(self, series: SeriesId, bar_start: UtcTime) -> timedelta | None:
        """初版では呼ばれない（`DelayScenario` の構築で拒否される）。"""
        del series, bar_start
        raise UnsupportedCapability(
            "SeededRandomDelay is not supported in the initial version (D03 §3.6)"
        )


#: 遅延規則の判別可能な union（D01 §8 の区分タグ付き union）。
DelayRule = FixedSeriesDelay | InjectedBarDelay | SeededRandomDelay


@dataclass(frozen=True, slots=True)
class DelayScenario:
    """遅延シナリオ（D03 §3.6）。実験のデータ公開設定に置く（部品パラメータではない）。

    複数の規則が同じ足に当たる場合は、**最大の遅延**を採る。足の公開が「最も遅い制約」で
    決まるようにするためで、規則の宣言順に結果が左右されない。
    """

    id: str
    version: int
    rules: tuple[DelayRule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise MarketDataValueError(f"DelayScenario.id must be a non-empty str, got {self.id!r}")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise MarketDataValueError(
                f"DelayScenario.version must be an int, got {self.version!r}"
            )
        if self.version < 1:
            raise MarketDataValueError(f"DelayScenario.version must be >= 1, got {self.version}")
        if not isinstance(self.rules, tuple):
            raise MarketDataValueError("DelayScenario.rules must be a tuple")
        for rule in self.rules:
            if isinstance(rule, SeededRandomDelay):
                raise UnsupportedCapability(
                    "SeededRandomDelay is rejected by the initial capability check (D03 §3.6)"
                )
            if not isinstance(rule, (FixedSeriesDelay, InjectedBarDelay)):
                raise MarketDataValueError(
                    f"DelayScenario.rules must contain delay rules, got {type(rule).__name__}"
                )

    def delay_for(self, series: SeriesId, bar_start: UtcTime) -> timedelta:
        """その足に適用する遅延（当たる規則がなければ 0）。"""
        applied = [
            delay for rule in self.rules if (delay := rule.delay_for(series, bar_start)) is not None
        ]
        return max(applied) if applied else timedelta(0)

    def available_at(
        self, schedule: SeriesSchedule, bar_start: UtcTime, bar_end: UtcTime
    ) -> UtcTime:
        """遅延適用後の利用可能時刻（D03 §3.6）。

        `available_at = scheduled_at + delay` であり、遅延が非負なので必ず
        `available_at >= bar_end` になる。OHLC と対象区間は変えない。
        """
        return schedule.scheduled_at(bar_end) + self.delay_for(schedule.series, bar_start)
