"""足と出所（D03 §3.3）。

`Bar` は確定した1本の足。未確定の足は `Bar` として存在しない（D03 §3.3）。執行モデルの
始値処理は公開フィードの別イベント（D03 §7.3 の `ExecutionOpen`）で扱う。

不変条件（D03 §3.3）:

- `low <= min(open, close)` かつ `max(open, close) <= high`。
- `volume >= 0`。
- **`available_at >= interval.end`**: 足の終了前に確定値が見える構成は構築時に拒否する。
  遅延シナリオ（D03 §3.6）を適用しても `available_at` は後ろへしか動かない。

足の区間が整列規則・カレンダーと整合するか（短縮セッションで切り詰められた区間の受理、
整列上の区間のままの足の拒否）は `validate_against` で検査する。`Bar` 単体はカレンダーを
知らないため、構築時ではなく明示的な検査にしている。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from odyssey_fx.common.money import Price
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = ["Bar", "BarKey", "Provenance", "ProvenanceKind"]


class ProvenanceKind(Enum):
    """足の出所（D03 §3.3）。

    `HISTDATA` / `DUKASCOPY` は原 CSV の `source` 列、`AGGREGATED` は本基盤が 1h から
    生成した上位足（D03 §5）。
    """

    HISTDATA = "histdata"
    DUKASCOPY = "dukascopy"
    AGGREGATED = "aggregated"


@dataclass(frozen=True, slots=True)
class Provenance:
    """出所と生成元の参照（D03 §3.3）。

    `source_ref` は、原データ由来なら原ファイルの POSIX 相対パス、集約足なら生成元系列の
    文字列（`USDJPY/1h/bid`）。価格計算には使わず、出所として保存する（D03 §2）。
    """

    kind: ProvenanceKind
    source_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProvenanceKind):
            raise MarketDataValueError("Provenance.kind must be a ProvenanceKind")
        if not isinstance(self.source_ref, str) or not self.source_ref:
            raise MarketDataValueError(
                f"Provenance.source_ref must be a non-empty str, got {self.source_ref!r}"
            )


@dataclass(frozen=True, slots=True)
class BarKey:
    """足の自然キー（D03 §3.3）。連番 ID は持たない。"""

    series: SeriesId
    bar_start: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("BarKey.series must be a SeriesId")
        if not isinstance(self.bar_start, UtcTime):
            raise MarketDataValueError("BarKey.bar_start must be a UtcTime")

    def __str__(self) -> str:
        return f"{self.series}@{self.bar_start}"

    def sort_key(self) -> tuple[str, str]:
        """整列鍵（系列文字列、開始時刻）。列挙順に依存しない記録のため。"""
        return (str(self.series), str(self.bar_start))


@dataclass(frozen=True, slots=True)
class Bar:
    """確定した1本の足（D03 §3.3）。"""

    series: SeriesId
    interval: Interval
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Decimal
    available_at: UtcTime
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("Bar.series must be a SeriesId")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("Bar.interval must be an Interval")
        for name in ("open", "high", "low", "close"):
            if not isinstance(getattr(self, name), Price):
                raise MarketDataValueError(f"Bar.{name} must be a Price")
        if not isinstance(self.volume, Decimal):
            raise MarketDataValueError("Bar.volume must be a Decimal")
        if not self.volume.is_finite():
            raise MarketDataValueError(f"Bar.volume must be finite, got {self.volume}")
        if self.volume < 0:
            raise MarketDataValueError(f"Bar.volume must be >= 0, got {self.volume}")
        if not isinstance(self.available_at, UtcTime):
            raise MarketDataValueError("Bar.available_at must be a UtcTime")
        if not isinstance(self.provenance, Provenance):
            raise MarketDataValueError("Bar.provenance must be a Provenance")

        if self.low > self.open or self.low > self.close:
            raise MarketDataValueError(
                f"Bar requires low <= min(open, close), got low={self.low},"
                f" open={self.open}, close={self.close}"
            )
        if self.high < self.open or self.high < self.close:
            raise MarketDataValueError(
                f"Bar requires max(open, close) <= high, got high={self.high},"
                f" open={self.open}, close={self.close}"
            )
        if self.low > self.high:
            raise MarketDataValueError(
                f"Bar requires low <= high, got low={self.low}, high={self.high}"
            )
        if self.available_at < self.interval.end:
            raise MarketDataValueError(
                f"Bar requires available_at >= bar_end, got available_at={self.available_at},"
                f" bar_end={self.interval.end};"
                " a confirmed value must not be visible before the bar ends (D03 §3.3)"
            )

    # --- 便宜プロパティ -----------------------------------------------------

    @property
    def bar_start(self) -> UtcTime:
        """足の開始時刻（区間の下端）。"""
        return self.interval.start

    @property
    def bar_end(self) -> UtcTime:
        """足の終了時刻（区間の上端、半開区間なので足には含まれない）。"""
        return self.interval.end

    @property
    def key(self) -> BarKey:
        """自然キー（D03 §3.3）。"""
        return BarKey(series=self.series, bar_start=self.bar_start)

    # --- 検証 ---------------------------------------------------------------

    def validate_against(
        self, timeframe_def: TimeframeDefinition, calendar: TradingCalendar
    ) -> None:
        """区間が定義とカレンダーの期待区間に一致することを検査する（D03 §3.3）。

        期待区間は `TimeframeDefinition.expected_interval()`（短縮セッションで切り詰め
        済み）。DST 切替日の日足・4h 足は名目長と一致しないため、名目長との照合は行わない。
        """
        if timeframe_def.ref.id != self.series.timeframe.id:
            raise MarketDataValueError(
                f"timeframe definition {timeframe_def.ref.id!r} does not match"
                f" series timeframe {self.series.timeframe.id!r}"
            )
        expected = timeframe_def.expected_interval(calendar, self.bar_start)
        if expected is None:
            raise MarketDataValueError(
                f"no bar is expected at {self.bar_start} for {self.series}"
                " (the aligned interval falls entirely inside a declared closure)"
            )
        if expected != self.interval:
            raise MarketDataValueError(
                f"Bar.interval {self.interval} does not match the expected interval"
                f" {expected} for {self.series} (D03 §3.3)"
            )
