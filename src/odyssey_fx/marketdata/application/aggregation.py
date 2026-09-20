"""上位足の生成（D03 §5、ADR-0024）。

1時間足（売却側の提示価格、bid）から 4時間足・日足を作る。区間はニューヨーク現地 17 時を
基準にした整列（`4h_ny17` / `1d_ny17`）で決める。15分足からは生成しない（15分足は執行系列）。

規則（D03 §5.1）:

- 始値＝最初の構成足の始値、高値＝最大、安値＝最小、終値＝最後の構成足の終値、
  volume＝合計。
- `bar_start` は**区間の開始**であり、最初の観測時刻ではない。旧基盤の「最初の観測時刻
  ラベル」は採用しない（ADR-0024）。
- `available_at` は構成足の `available_at` の最大値（通常は `bar_end`）。

**不完全足は生成しない**（D03 §5.2）。区間内にカレンダー上期待される構成足がすべて存在
する場合だけ生成し、欠落がある区間は生成せず「存在すべき足の欠落」（`MISSING_EXPECTED_BAR`）
として報告する。一部の構成足で作った不完全足を採用する選択肢は初版に設けない（暗黙の部分
集計を避ける、上位設計書 §3.2）。短縮セッションでは、集約足の区間は期待区間で切り詰めた
ものとし、その区間内に期待される構成足が揃えば完全とみなす。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from odyssey_fx.common.money import decimal_from_int, kernel_context
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = ["AGGREGATION_RULE_VERSION", "AggregationResult", "aggregate"]

#: 集約規則の版（ADR-0024）。manifest の `conversion` に記録する。
AGGREGATION_RULE_VERSION = "ny17_v2"


@dataclass(frozen=True, slots=True)
class AggregationResult:
    """集約の結果（D03 §5）。

    `bars` は完全に揃った区間から生成した上位足、`findings` は欠落により生成できなかった
    区間の報告（`MISSING_EXPECTED_BAR`）。
    """

    bars: tuple[Bar, ...]
    findings: tuple[CheckResult, ...]


def _source_bar_index(source_bars: tuple[Bar, ...]) -> dict[UtcTime, Bar]:
    """構成足を開始時刻で引けるようにする。同じ開始時刻が2本あれば拒否する。"""
    index: dict[UtcTime, Bar] = {}
    for bar in source_bars:
        if bar.bar_start in index:
            raise MarketDataValueError(
                f"duplicate source bar at {bar.bar_start} for {bar.series};"
                " deduplicate before aggregating (D03 §3.9 DUPLICATE_TIMESTAMP)"
            )
        index[bar.bar_start] = bar
    return index


def aggregate(
    source_bars: tuple[Bar, ...],
    *,
    source_timeframe_def: TimeframeDefinition,
    target_series: SeriesId,
    target_timeframe_def: TimeframeDefinition,
    calendar: TradingCalendar,
) -> AggregationResult:
    """構成足から上位足を生成する（D03 §5）。

    `source_bars` は同じ系列の足で、開始時刻の昇順である必要はない（内部で整列する）。
    生成対象の区間は、構成足が覆う範囲でカレンダー上期待される上位足の区間すべて。
    """
    if not isinstance(source_bars, tuple):
        raise MarketDataValueError("aggregate() requires a tuple of source bars")
    if not source_bars:
        return AggregationResult(bars=(), findings=())

    source_series = source_bars[0].series
    for bar in source_bars:
        if bar.series != source_series:
            raise MarketDataValueError(
                "aggregate() requires bars of a single series, got"
                f" {source_series} and {bar.series}"
            )
    if source_series.basis != target_series.basis:
        raise MarketDataValueError(
            f"aggregation must not change the price basis, got {source_series.basis}"
            f" -> {target_series.basis}"
        )
    if source_series.symbol != target_series.symbol:
        raise MarketDataValueError(
            f"aggregation must not change the symbol, got {source_series.symbol}"
            f" -> {target_series.symbol}"
        )

    index = _source_bar_index(source_bars)
    ordered = tuple(sorted(source_bars, key=lambda bar: bar.bar_start.value))
    covered = Interval(start=ordered[0].bar_start, end=ordered[-1].bar_end)

    bars: list[Bar] = []
    findings: list[CheckResult] = []
    for target_start in calendar.expected_bar_starts(target_timeframe_def, covered):
        expected = target_timeframe_def.expected_interval(calendar, target_start)
        if expected is None:  # pragma: no cover - expected_bar_starts が除いている
            continue
        component_starts = calendar.expected_bar_starts(source_timeframe_def, expected)
        if not component_starts:
            # 期待される構成足が1本もない区間は、上位足も存在しない。
            continue
        components = [index.get(start) for start in component_starts]
        if any(component is None for component in components):
            findings.append(
                CheckResult.create(
                    CheckKind.MISSING_EXPECTED_BAR,
                    target_series,
                    expected,
                    detail={
                        "expected_components": str(len(component_starts)),
                        "present_components": str(
                            sum(1 for component in components if component is not None)
                        ),
                        "reason": "incomplete_aggregate",
                    },
                )
            )
            continue

        present = [component for component in components if component is not None]
        present.sort(key=lambda bar: bar.bar_start.value)
        with localcontext(kernel_context()):
            volume: Decimal = sum((bar.volume for bar in present), start=decimal_from_int(0))
        bars.append(
            Bar(
                series=target_series,
                interval=expected,
                open=present[0].open,
                high=max(bar.high for bar in present),
                low=min(bar.low for bar in present),
                close=present[-1].close,
                volume=volume,
                available_at=max(
                    (bar.available_at for bar in present), key=lambda value: value.value
                ),
                provenance=Provenance(
                    kind=ProvenanceKind.AGGREGATED, source_ref=str(source_series)
                ),
            )
        )

    return AggregationResult(
        bars=tuple(bars),
        findings=tuple(sorted(findings, key=lambda result: result.sort_key())),
    )
