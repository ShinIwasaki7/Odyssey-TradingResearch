"""MT5 経路の上位足の生成と被覆の属性（D03 §15.4、規則 3。D12-78・D12-81・D12-82・D12-87）。

1 分足から 15 分足・1 時間足・4 時間足・日足を**直接**作る（作られた 1 時間足から 4 時間足・日足を
作る経路は採らない。D03 §15.4）。作る条件は「区間の中の足が存在すべき分のうち、記録ありの分が
1 つ以上あり、取得不足の分が 1 つも無い」こと。

- 記録ありの分が 0 で取得不足も 0 の足は作らない（`NO_RECORD_BAR`）。欠けとも完全とも呼ばない。
- 取得不足の分を 1 つでも含む足は作らない（`ACQUISITION_SHORTFALL`。中に記録があっても作らない）。
- 4 値は記録のある分だけから決める。記録の無い分に価格を置かない（前値で埋めない。D12-81）。
- 被覆率の閾値を置かない（D12-87）。被覆率 1 未満の足は「部分観測の足」（`PARTIAL_COVERAGE_BAR`）。
- `bar_start` は区間の開始（最初の観測時刻ではない）。`available_at` は区間の終わり（D03 §15.5）。

足の区分（作った・部分観測・無記録・取得不足）を返す `classify_bar` は、全期間の集計の道具（PR-B）
が同じ関数で日足を数えるための入口でもある（D12 §11.13 の 15）。
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from fractions import Fraction

from odyssey_fx.common.money import decimal_from_int
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, BarCoverage, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult
from odyssey_fx.marketdata.domain.minute_status import MinuteCounts, MinuteLedger
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = [
    "M1_COVERAGE_RULE_VERSION",
    "BarClass",
    "BarClassification",
    "CoverageAggregation",
    "aggregate_from_minutes",
    "classify_bar",
    "epoch",
    "utc",
]

#: MT5 経路の集約規則の版（D03 §15.6 の 4。数え方の規則の市場データの側の項目と同じ内容）。
M1_COVERAGE_RULE_VERSION = "m1_coverage_v1"

_PARTIAL_BINS: tuple[tuple[str, Fraction], ...] = (
    ("below_0_50", Fraction(1, 2)),
    ("below_0_90", Fraction(9, 10)),
    ("below_0_99", Fraction(99, 100)),
    ("below_1", Fraction(1)),
)


def epoch(moment: UtcTime) -> int:
    """UTC の時刻を epoch 秒にする（分の境目の時刻だけを扱う）。"""
    return int(moment.value.timestamp())


def utc(seconds: int) -> UtcTime:
    """epoch 秒を UTC の時刻にする。"""
    return UtcTime(datetime.fromtimestamp(seconds, UTC))


class BarClass(Enum):
    """上位足の区分（D03 §15.4・§15.7。2026-10-17 の指示 3-B の語）。"""

    FULLY_OBSERVED = "fully_observed"  # 作る（被覆率 1）
    PARTIAL = "partial"  # 作る（被覆率 < 1。「完全」とは呼ばない）
    NO_RECORD = "no_record"  # 作らない（記録ありの分が 0）
    SHORTFALL = "shortfall"  # 作らない（取得不足の分を含む）


@dataclass(frozen=True, slots=True)
class BarClassification:
    """1 本の足の区分と件数（価格を含まない）。"""

    interval: Interval
    bar_class: BarClass
    counts: MinuteCounts
    first_observed: int | None
    last_observed: int | None

    @property
    def is_built(self) -> bool:
        return self.bar_class in (BarClass.FULLY_OBSERVED, BarClass.PARTIAL)


def classify_bar(ledger: MinuteLedger, interval: Interval) -> BarClassification:
    """足の区間の区分（D03 §15.4 の作る条件）。"""
    lo, hi = epoch(interval.start), epoch(interval.end)
    counts = ledger.counts(lo, hi)
    if counts.shortfall:
        bar_class = BarClass.SHORTFALL
    elif counts.recorded == 0:
        bar_class = BarClass.NO_RECORD
    elif counts.recorded == counts.expected:
        bar_class = BarClass.FULLY_OBSERVED
    else:
        bar_class = BarClass.PARTIAL
    first = ledger.first_recorded(lo, hi) if counts.recorded else None
    last = ledger.last_recorded(lo, hi) if counts.recorded else None
    return BarClassification(
        interval=interval,
        bar_class=bar_class,
        counts=counts,
        first_observed=first,
        last_observed=last,
    )


@dataclass(frozen=True, slots=True)
class CoverageAggregation:
    """上位足の生成の結果（D03 §15.4・§15.6 の 2）。"""

    bars: tuple[Bar, ...]
    classifications: tuple[BarClassification, ...]
    findings: tuple[CheckResult, ...]

    def count(self, bar_class: BarClass) -> int:
        return sum(1 for item in self.classifications if item.bar_class is bar_class)


def _minute_index(minute_bars: Sequence[Bar]) -> tuple[list[int], list[Bar]]:
    ordered = sorted(minute_bars, key=lambda bar: bar.bar_start.value)
    return [epoch(bar.bar_start) for bar in ordered], ordered


def aggregate_from_minutes(
    minute_bars: Sequence[Bar],
    ledger: MinuteLedger,
    *,
    target_series: SeriesId,
    target_def: TimeframeDefinition,
    calendar: TradingCalendar,
) -> CoverageAggregation:
    """1 分足から上位足を作る（D03 §15.4）。

    対象は台帳の範囲 `[data_start, data_end)` に区間が収まる足だけ（範囲の外にはみ出す足は
    snapshot の範囲の外なので作らず、報告もしない）。`minute_bars` は 1 系列の 1 分足で、
    セッションの外の足（休場帯の足）を含んでよい（台帳の記録ありの分だけを使う）。
    """
    if minute_bars and minute_bars[0].series.symbol != target_series.symbol:
        raise MarketDataValueError("aggregation must not change the symbol (D03 §15.4)")
    starts, ordered = _minute_index(minute_bars)
    source_series = ordered[0].series if ordered else None
    window = Interval(start=utc(ledger.data_start), end=utc(ledger.data_end))
    bars: list[Bar] = []
    classes: list[BarClassification] = []
    findings: list[CheckResult] = []
    for bar_start in calendar.expected_bar_starts(target_def, window):
        interval = target_def.expected_interval(calendar, bar_start)
        if interval is None:  # pragma: no cover - expected_bar_starts が除いている
            continue
        if interval.start < window.start or window.end < interval.end:
            continue
        item = classify_bar(ledger, interval)
        if item.counts.expected == 0:
            continue  # 区間の中にセッションの分が無い（存在しない足）
        classes.append(item)
        if item.bar_class is BarClass.SHORTFALL:
            reasons = sorted(
                {
                    span.reason.value
                    for span in ledger.shortfall_in(epoch(interval.start), epoch(interval.end))
                }
            )
            findings.append(
                CheckResult.create(
                    CheckKind.ACQUISITION_SHORTFALL,
                    target_series,
                    interval,
                    detail={
                        "expected_minutes": str(item.counts.expected),
                        "reasons": ",".join(reasons),
                        "shortfall_minutes": str(item.counts.shortfall),
                    },
                )
            )
            continue
        if item.bar_class is BarClass.NO_RECORD:
            findings.append(
                CheckResult.create(
                    CheckKind.NO_RECORD_BAR,
                    target_series,
                    interval,
                    detail={"expected_minutes": str(item.counts.expected)},
                )
            )
            continue
        assert item.first_observed is not None and item.last_observed is not None  # noqa: S101
        lo = bisect.bisect_left(starts, epoch(interval.start))
        hi = bisect.bisect_left(starts, epoch(interval.end))
        components = [bar for bar in ordered[lo:hi] if ledger.is_recorded(epoch(bar.bar_start))]
        if len(components) != item.counts.recorded:  # pragma: no cover - 台帳と足は同じ入力
            raise MarketDataValueError(
                f"the ledger records {item.counts.recorded} minutes in {interval} but"
                f" {len(components)} minute bars were supplied"
            )
        rows = {bar.minute.time_rule_row for bar in components if bar.minute is not None}
        if len(rows) != 1:
            # 足の構成分はすべて同じ表の行に入るはず（行の境目は週末の休場の中）。混じれば表か
            # カレンダーの誤りで、構造エラーとして受入れを失敗させる（D03 §15.4）。
            raise MarketDataValueError(
                f"the minute bars of {target_series} {interval} come from time rule rows"
                f" {sorted(rows)}; an upper bar must not mix rows (D03 §15.4)"
            )
        bars.append(
            Bar(
                series=target_series,
                interval=interval,
                open=components[0].open,
                high=max(bar.high for bar in components),
                low=min(bar.low for bar in components),
                close=components[-1].close,
                volume=decimal_from_int(0),
                available_at=interval.end,
                provenance=Provenance(
                    kind=ProvenanceKind.AGGREGATED,
                    source_ref=str(source_series) if source_series is not None else "1m",
                ),
                coverage=BarCoverage(
                    expected_minutes=item.counts.expected,
                    recorded_minutes=item.counts.recorded,
                    first_observed_at=utc(item.first_observed),
                    last_observed_at=utc(item.last_observed),
                    time_rule_row=rows.pop(),
                ),
            )
        )
    partial = [item for item in classes if item.bar_class is BarClass.PARTIAL]
    if partial:
        findings.append(_partial_summary(target_series, partial))
    return CoverageAggregation(
        bars=tuple(bars), classifications=tuple(classes), findings=tuple(findings)
    )


def _partial_summary(series: SeriesId, partial: Sequence[BarClassification]) -> CheckResult:
    """部分観測の足の系列ごとの件数と分布の要約（D03 §15.6 の 2。足ごとの値は partition の列）。"""
    bins = dict.fromkeys((label for label, _ in _PARTIAL_BINS), 0)
    lowest: Fraction | None = None
    for item in partial:
        ratio = Fraction(item.counts.recorded, item.counts.expected)
        lowest = ratio if lowest is None or ratio < lowest else lowest
        for label, bound in _PARTIAL_BINS:
            if ratio < bound:
                bins[label] += 1
                break
    span = Interval(start=partial[0].interval.start, end=partial[-1].interval.end)
    assert lowest is not None  # noqa: S101 - partial は空でない
    detail = {f"bars_{label}": str(count) for label, count in bins.items()}
    detail["partial_bars"] = str(len(partial))
    detail["min_coverage"] = f"{lowest.numerator}/{lowest.denominator}"
    return CheckResult.create(CheckKind.PARTIAL_COVERAGE_BAR, series, span, detail=detail)
