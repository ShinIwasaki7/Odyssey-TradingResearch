"""履歴窓の長さを日足の本数に直す（D25 §3.5 の 3。D14 §3.2・D22 §7.1.3 と同じ数え方）。

評価範囲の前の余白の前提は「全戦略の履歴窓の最大 = 日足 60 本」（D14 §1.1、D13 §2.1）なので、
コンパイルした戦略の各入力の読み取りの窓（パラメータを解決した後の窓）を、日足の本数に直した
最大で比べる（第3.5節の「部品の窓を日足の本数に直した最大」）。

- 本数の窓: （本数 ＋ 当該足を除く本数）× 接続元の系列の時間足の名目の長さ。高値・安値の窓のように
  当該足を除く窓は 1 本多く数える（D22 §7.1.3 の表の注記）。
- 経過時間の窓: その長さのまま（暦の時間。週末を含むので、日足の本数より多めに数える側）。
- 過去値への遡り（`UsePrevious`）の上限も同じに数える。
- 接続元が他の使用箇所の出力なら、その使用箇所の足の確定の起動（`bar_close`）の系列のうち最も長い
  時間足で数える。足の確定で起動しない使用箇所なら、その入力の接続元を遡って同じに数える。
  どこまで遡っても時間足が決まらなければ、日足に直せないので不合格として扱う（本 PR の仮置き）。
- 日足 1 本は 24 時間（名目の長さ。`1d_ny17` の名目の長さと同じ）。

接続をたどった窓の和（例: 部品の出力の窓のさらに窓）は数えず、窓ごとの最大で比べる（第3.5節の
「最大」の字句と D14 の前例のとおり。本 PR の仮置き）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.compiler.compiled import (
    CompiledComponent,
    CompiledStrategy,
    ResolvedMarketSource,
    ResolvedOutputSource,
)
from odyssey_fx.strategy.declarations.evaluation import OnBarClose
from odyssey_fx.strategy.declarations.read_spec import BarsWindow, DurationWindow, HistoryWindow

__all__ = ["DAILY_BAR", "HISTORY_LIMIT_DAILY_BARS", "HistoryMeasure", "measure_history"]

#: 日足 1 本の名目の長さ。
DAILY_BAR: Final = timedelta(days=1)
#: 履歴窓の上限（日足の本数。D14 §1.1・§3.1 の 3）。
HISTORY_LIMIT_DAILY_BARS: Final = 60


@dataclass(frozen=True, slots=True)
class HistoryMeasure:
    """1 つの戦略の履歴窓の最大（`longest`）と、日足に直せなかった入力（`unmeasured`）。"""

    longest: timedelta
    unmeasured: tuple[str, ...]

    @property
    def within_limit(self) -> bool:
        return not self.unmeasured and self.longest <= DAILY_BAR * HISTORY_LIMIT_DAILY_BARS


def _bar_length(
    component: CompiledComponent,
    by_id: Mapping[str, CompiledComponent],
    timeframes: Mapping[TimeframeRef, TimeframeDefinition],
    seen: frozenset[str] = frozenset(),
) -> timedelta | None:
    """使用箇所の出力 1 つ分の時間（足の確定の起動の系列の最も長い時間足）。"""
    lengths = [
        timeframes[trigger.series.timeframe].nominal_length
        for trigger in component.triggers
        if isinstance(trigger, OnBarClose) and trigger.series.timeframe in timeframes
    ]
    if lengths:
        return max(lengths)
    upstream: list[timedelta] = []
    for plan in component.input_plans.values():
        for source in plan.sources:
            length = _source_length(source, by_id, timeframes, seen | {component.instance_id})
            if length is not None:
                upstream.append(length)
    return max(upstream) if upstream else None


def _source_length(
    source: object,
    by_id: Mapping[str, CompiledComponent],
    timeframes: Mapping[TimeframeRef, TimeframeDefinition],
    seen: frozenset[str],
) -> timedelta | None:
    if isinstance(source, ResolvedMarketSource):
        definition = timeframes.get(source.series.timeframe)
        return None if definition is None else definition.nominal_length
    if isinstance(source, ResolvedOutputSource):
        upstream = by_id.get(source.instance_id)
        if upstream is None or source.instance_id in seen:
            return None
        return _bar_length(upstream, by_id, timeframes, seen)
    return None


def _window_length(window: object, bar: timedelta | None, extra_bars: int) -> timedelta | None:
    if isinstance(window, DurationWindow):
        return window.duration
    if isinstance(window, BarsWindow) and isinstance(window.count, int):
        return None if bar is None else bar * (window.count + extra_bars)
    return None


def measure_history(
    compiled: CompiledStrategy, timeframes: Mapping[TimeframeRef, TimeframeDefinition]
) -> HistoryMeasure:
    """コンパイルした戦略の履歴窓の最大を測る。"""
    by_id = {component.instance_id: component for component in compiled.components}
    longest = timedelta(0)
    unmeasured: list[str] = []
    for component in compiled.components:
        for name, plan in component.input_plans.items():
            extra = (
                plan.read_spec.exclude_latest_bars
                if isinstance(plan.read_spec, HistoryWindow)
                else 0
            )
            windows = [
                (plan.resolved_window, extra),
                (plan.resolved_max_lookback, 0),
            ]
            for window, extra_bars in windows:
                if window is None:
                    continue
                for source in plan.sources:
                    bar = _source_length(source, by_id, timeframes, frozenset())
                    length = _window_length(window, bar, extra_bars)
                    if length is None:
                        unmeasured.append(f"{component.instance_id}.{name}")
                    else:
                        longest = max(longest, length)
    return HistoryMeasure(longest=longest, unmeasured=tuple(dict.fromkeys(unmeasured)))
