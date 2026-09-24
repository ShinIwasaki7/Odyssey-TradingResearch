"""ランタイムのポートを差し替える単純な実装。

実データも I/O も使わない。足は呼び出し側が明示的に与え、無ければ欠損を返す。**黙って古い
足へ戻らない**ところだけ本物（D03 §6.2）と同じにしてある。

`FakePositionContext` は D06 が定める建玉の時点情報のうち、段階2 の部品が読む項目
（方向・約定価格・有効な損切り水準）だけを持つ。D06 の実装が入ったら、その型が同じ構造を
満たす（`catalog.inputs.PositionContextView`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from odyssey_fx.common.ids import PositionId
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, BarKey, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.records.payloads import TradeDirection
from odyssey_fx.strategy.records.records import OutputRecord
from odyssey_fx.strategy.runtime.ports import BarsWindowView, HistoryWindowView

__all__ = [
    "CollectingSink",
    "FakeContextView",
    "FakeMarketDataView",
    "FakePositionContext",
    "MissingInput",
    "hourly_bars",
]


@dataclass(frozen=True, slots=True)
class MissingInput:
    """市場データが読めなかったこと（`marketdata.application.MissingInput` と同じ構造）。"""

    reason: MissingInputReason


@dataclass(frozen=True, slots=True)
class FakePositionContext:
    """建玉の時点情報のうち、段階2 の部品が読む項目（D05 §4.3(5)・D06 §8.4）。"""

    position_id: PositionId
    direction: TradeDirection
    entry_price: Price
    effective_stop_loss: Price | None = None
    effective_take_profit: Price | None = None


class FakeMarketDataView:
    """系列ごとに足の列を持ち、判断時刻で切って返すビュー。

    足は `bar_end <= at` のものだけを見せる（未来参照を構造的に防ぐ）。履歴は「窓の本数ぶん
    そろっていなければ欠損」とし、足りないまま繰り上げない（D03 §6.2）。
    """

    def __init__(self, bars: Mapping[SeriesId, Sequence[Bar]]) -> None:
        self._bars = {
            series: tuple(sorted(items, key=lambda bar: bar.bar_start.value))
            for series, items in bars.items()
        }

    def _visible(self, series: SeriesId, at: UtcTime) -> tuple[Bar, ...]:
        return tuple(bar for bar in self._bars.get(series, ()) if bar.available_at <= at)

    def latest_available(self, series: SeriesId, at: UtcTime) -> Bar | MissingInput:
        visible = self._visible(series, at)
        if not visible:
            return MissingInput(MissingInputReason.LATEST_BAR_UNAVAILABLE)
        return visible[-1]

    def history(
        self,
        series: SeriesId,
        window: HistoryWindowView,
        at: UtcTime,
        *,
        end_offset_bars: int = 0,
    ) -> tuple[Bar, ...] | MissingInput:
        visible = self._visible(series, at)
        if not isinstance(window, BarsWindowView):
            return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)
        end = len(visible) - end_offset_bars
        start = end - window.count
        if end <= 0 or start < 0:
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        return visible[start:end]

    def bar(self, series: SeriesId, bar_start: UtcTime, at: UtcTime) -> Bar | MissingInput:
        for candidate in self._visible(series, at):
            if candidate.bar_start == bar_start:
                return candidate
        return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)

    def history_ending_at(
        self,
        series: SeriesId,
        window: HistoryWindowView,
        base_bar_start: UtcTime,
        at: UtcTime,
        *,
        end_offset_bars: int = 0,
    ) -> tuple[Bar, ...] | MissingInput:
        """基準の足を末尾側の基準にした履歴窓（D03 §6.2 v1.6 と同じ欠損の返し方）。"""
        visible = self._visible(series, at)
        if not isinstance(window, BarsWindowView):
            return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)
        positions = [index for index, bar in enumerate(visible) if bar.bar_start == base_bar_start]
        if not positions:
            return MissingInput(MissingInputReason.LATEST_BAR_UNAVAILABLE)
        end = positions[0] + 1 - end_offset_bars
        start = end - window.count
        if end <= 0 or start < 0:
            return MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
        return visible[start:end]

    def previous_available(
        self,
        series: SeriesId,
        before_bar_start: UtcTime,
        at: UtcTime,
        *,
        max_lookback: HistoryWindowView,
    ) -> Bar | MissingInput:
        """基準の足より前の足を、上限の本数まで新しい側からたどる（予定表は足の列で代用）。"""
        if not isinstance(max_lookback, BarsWindowView):
            return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)
        earlier = [
            bar
            for bar in self._bars.get(series, ())
            if bar.bar_start.value < before_bar_start.value
        ]
        for bar in reversed(earlier[-max_lookback.count :]):
            if bar.available_at <= at:
                return bar
        return MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)

    def expected_latest_key(self, series: SeriesId, at: UtcTime) -> BarKey | None:
        visible = self._visible(series, at)
        return visible[-1].key if visible else None

    def freshness(self, series: SeriesId, bar: Bar) -> UtcTime:
        del series
        return bar.bar_end


class FakeContextView:
    """建玉・口座の時点情報を、テストが明示した値で返す。"""

    def __init__(
        self,
        positions: Mapping[PositionId, FakePositionContext] | None = None,
        account: object | None = None,
    ) -> None:
        self._positions = dict(positions or {})
        self._account = account

    def position_context(self, at: UtcTime, position_id: PositionId | None) -> object | None:
        del at
        if position_id is None:
            if len(self._positions) == 1:
                return next(iter(self._positions.values()))
            return None
        return self._positions.get(position_id)

    def account_context(self, at: UtcTime) -> object:
        del at
        return self._account


@dataclass
class CollectingSink:
    """送出された出力記録をそのまま貯める受け口。"""

    records: list[OutputRecord[object]] = field(default_factory=list)

    def emit(self, records: tuple[OutputRecord[object], ...]) -> None:
        self.records.extend(records)


def hourly_bars(
    series: SeriesId,
    first_start: UtcTime,
    specs: Sequence[tuple[str, str, str, str]],
) -> tuple[Bar, ...]:
    """1時間足を連続して作る（始値・高値・安値・終値を明示する）。

    人工データの値は手計算で検算できることだけを目的にしている（全体計画 §8.2）。足の
    利用可能時刻は足の終了時刻と同じにする（遅延なし）。
    """
    bars: list[Bar] = []
    start = first_start
    for open_value, high_value, low_value, close_value in specs:
        interval = Interval(start=start, end=start + timedelta(hours=1))
        bars.append(
            Bar(
                series=series,
                interval=interval,
                open=Price(decimal_from_str(open_value)),
                high=Price(decimal_from_str(high_value)),
                low=Price(decimal_from_str(low_value)),
                close=Price(decimal_from_str(close_value)),
                volume=decimal_from_str("1000"),
                available_at=interval.end,
                provenance=Provenance(
                    kind=ProvenanceKind.HISTDATA,
                    source_ref="tests/fixtures/strategy",
                ),
            )
        )
        start = interval.end
    return tuple(bars)
