"""補充の検証 5 点（D03 §14.7・§14.8）。

書き出しの前に行う規則である（実時計・通信・ファイルを持たない）。入力は取得計画、時間
ファイルごとの有効な最終結果と保管場所から読んだ tick と出所、原データの足の引き当て、
カレンダー。結果（`RefillValidation`）は合否と、補充した足・作らなかった足・未照合の塊・
合否に使わない記録を持つ。

| # | 検証 | 不合格の条件 |
|---|---|---|
| 1 | UTC の時刻 | ミリ秒が時間ファイルの範囲外の tick がある。照合用の足が原データと一致しない |
| 2 | bid／ask の区別 | 照合用の足のうち 1 本でも四本値が原データと異なる（差 0 を一致） |
| 3 | 前後の足との整合 | 合否に使わない（差をすべて記録し、10 pip を超えた塊に「要確認」の印） |
| 4 | 重複 | 補充した足の中に同じ系列・開始時刻が 2 本ある。開始時刻が原データにある |
| 5 | 出所 | 補充した足のもとの時間ファイルの出所の記録が無い |

あわせて、足の不変条件に違反する足（tick の価格が正でないなど）と、補充した足が 0 本で
あることも不合格にする。未照合の塊は合否の外に置き、その足は作らない（D03 §14.4）。

**照合用の足**: 計画の時間ファイル（対象の時間と照合用の時間）に入る、研究履歴区分の原データの
足すべて。照合用の足が原データにある塊で、取得できなかったためにどれも比べられないまま
補充する足ができるときは、検証できない足を書かないよう不合格にする（取り直しで回復する。
D03 §14.12 の不合格×出来事9）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int, kernel_context
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.refill_aggregation import bar_from_ticks
from odyssey_fx.marketdata.application.refill_plan import (
    RawBarIndex,
    hour_chunks,
    reference_hour_for,
)
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import (
    HOUR,
    REFILL_TIMEFRAME_IDS,
    ArchiveProvenance,
    DecodedTicks,
    FinalResult,
    HourKey,
    HourOutcome,
    RefillPlan,
    TargetBar,
)
from odyssey_fx.marketdata.domain.refill_validation import (
    HourlyConsistency,
    NeighborCheck,
    NeighborSide,
    NeighborStatus,
    NotBuiltBar,
    NotBuiltReason,
    ReconciledBar,
    RefillValidation,
    UnreconciledChunk,
    UsedHour,
)
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = ["NEIGHBOR_REVIEW_THRESHOLD_PIPS", "HourData", "validate_refill"]

#: 前後の足との差の「要確認」の表示閾値（10 pip。合否に使わない。D03 §14.18 の 7）。
NEIGHBOR_REVIEW_THRESHOLD_PIPS: Final = decimal_from_int(10)

#: 1 時間の 15分足の本数。
_QUARTERS: Final = 4


@dataclass(frozen=True, slots=True)
class HourData:
    """検証に使う時間ファイル 1 本の材料。

    `final` は有効な最終結果。`FETCHED`・`FETCHED_EMPTY` なら、保管場所から読んで検算を
    通した tick（`decoded`）と、そのファイルに記録された取得の出所（`provenance`）と保管場所の
    相対パス（`archive_path`）を持つ。
    """

    final: FinalResult
    decoded: DecodedTicks | None
    provenance: ArchiveProvenance | None
    archive_path: str | None


def _series_lookup(originals: Sequence[SeriesId]) -> dict[tuple[str, str], SeriesId]:
    return {(str(series.symbol), series.timeframe.id): series for series in originals}


def _ohlc_differences(built: Bar, raw: Bar) -> tuple[tuple[str, Decimal], ...]:
    """`(項目, tick から作った値 − 原データの値)` の列（一致すれば空）。"""
    differences: list[tuple[str, Decimal]] = []
    for name in ("open", "high", "low", "close"):
        made = getattr(built, name).value
        recorded = getattr(raw, name).value
        if made != recorded:
            differences.append((name, kernel_context().subtract(made, recorded)))
    return tuple(differences)


def _bar_chunks(targets: Sequence[TargetBar]) -> list[list[TargetBar]]:
    """系列ごとの対象足の連続する塊（次の足の開始 = 前の足の終端）。"""
    chunks: list[list[TargetBar]] = []
    for target in targets:
        if (
            chunks
            and chunks[-1][-1].series == target.series
            and chunks[-1][-1].interval.end == target.interval.start
        ):
            chunks[-1].append(target)
        else:
            chunks.append([target])
    return chunks


def _crosses_closure(calendar: TradingCalendar, gap: Interval | None) -> bool:
    """直前・直後の足とのあいだ（`gap`）に休場を挟むか。接していれば挟まない。"""
    if gap is None:
        return False
    sessions = calendar.sessions(gap)
    covered = gap.start
    for session in sessions:
        if session.start > covered:
            return True
        covered = max(covered, session.end)
    return covered < gap.end


def validate_refill(
    *,
    plan: RefillPlan,
    hours: Mapping[HourKey, HourData],
    originals: Sequence[SeriesId],
    raw: RawBarIndex,
    calendar: TradingCalendar,
) -> RefillValidation:
    """検証 5 点を行う（D03 §14.7）。すべての時間ファイルに有効な最終結果が要る。"""
    missing = [str(key) for key in plan.hour_keys if key not in hours]
    if missing:
        raise MarketDataValueError(
            f"validation needs a final result for every hour file of the plan; missing {missing}"
        )
    settings = plan.provider.settings
    lookup = _series_lookup(originals)
    failures: list[str] = []

    # --- tick の時刻の範囲（検証 1）と bid／ask の件数（記録のみ）---------------------
    out_of_range = 0
    bid_above_ask = 0
    for key in plan.hour_keys:
        data = hours[key]
        if data.decoded is None:
            continue
        for tick in data.decoded.ticks:
            if not tick.in_hour:
                out_of_range += 1
            if tick.bid_raw > tick.ask_raw:
                bid_above_ask += 1
    if out_of_range:
        failures.append(
            f"{out_of_range} tick(s) lie outside their hour file (0 <= ms < 3600000;"
            " D03 §14.7 の 1)"
        )

    def ticks_of(key: HourKey) -> DecodedTicks | None:
        data = hours[key]
        if data.final.outcome is HourOutcome.NOT_FETCHED:
            return None
        if data.decoded is None:
            raise MarketDataValueError(f"{key} is {data.final.outcome.value} but has no ticks")
        return data.decoded

    def build(series: SeriesId, interval: Interval, key: HourKey) -> Bar | None:
        decoded = ticks_of(key)
        if decoded is None:
            return None
        data = hours[key]
        try:
            return bar_from_ticks(
                series=series,
                interval=interval,
                hour=key,
                ticks=decoded.ticks,
                quote=settings.symbol(series.symbol),
                source_ref=data.archive_path or str(key),
            )
        except (MarketDataValueError, KernelValueError) as exc:
            failures.append(
                f"{series} {interval.start}: the ticks do not make a valid bar ({exc}); D03 §14.7"
            )
            return None

    # --- 塊ごとの照合（検証 1・2）と未照合 --------------------------------------------
    targets_by_hour: dict[HourKey, list[TargetBar]] = {}
    for target in plan.target_bars:
        targets_by_hour.setdefault(target.hour, []).append(target)
    target_keys = {(target.series, target.start) for target in plan.target_bars}
    reference_hours = {hour.hour for hour in plan.hours if hour.reference}
    reconciled: list[ReconciledBar] = []
    unreconciled_hours: set[HourKey] = set()
    unreconciled: list[UnreconciledChunk] = []
    unverified_hours: set[HourKey] = set()
    used_references: set[HourKey] = set()
    symbols = sorted({key.symbol for key in targets_by_hour}, key=str)
    for symbol in symbols:
        target_hours = [key.start for key in targets_by_hour if key.symbol == symbol]
        symbol_series = [series for series in originals if series.symbol == symbol]
        for chunk in hour_chunks(target_hours):
            reference = reference_hour_for(symbol, chunk, symbol_series, raw)
            chunk_keys = [HourKey(symbol=symbol, start=moment) for moment in chunk]
            sources = list(chunk_keys)
            if reference is not None:
                reference_key = HourKey(symbol=symbol, start=reference)
                if reference_key not in reference_hours:
                    raise MarketDataValueError(
                        f"the reference hour {reference_key} is not marked in the plan;"
                        " the raw data differs from the one the plan was built from"
                    )
                sources.append(reference_key)
                used_references.add(reference_key)
            compared = 0
            has_sources = False
            for key in sources:
                for timeframe_id in sorted(REFILL_TIMEFRAME_IDS):
                    series = lookup.get((str(symbol), timeframe_id))
                    if series is None:
                        continue
                    for raw_bar in raw.research_bars_in(series, key.interval):
                        if (series, raw_bar.bar_start) in target_keys:
                            continue  # pragma: no cover - 対象足は原データに無い
                        has_sources = True
                        if ticks_of(key) is None:
                            continue  # 取得できなかった時間の足は比べられない
                        made = build(series, raw_bar.interval, key)
                        reconciled.append(
                            ReconciledBar(
                                series=series,
                                start=raw_bar.bar_start,
                                hour=key,
                                built=made is not None,
                                differences=()
                                if made is None
                                else _ohlc_differences(made, raw_bar),
                            )
                        )
                        compared += 1
            if not has_sources:
                unreconciled_hours.update(chunk_keys)
                counts: dict[SeriesId, list[TargetBar]] = {}
                for key in chunk_keys:
                    for target in targets_by_hour.get(key, []):
                        counts.setdefault(target.series, []).append(target)
                for series, bars in counts.items():
                    unreconciled.append(
                        UnreconciledChunk(
                            series=series,
                            chunk_start=min(bars, key=lambda bar: bar.start.value).start,
                            target_count=len(bars),
                        )
                    )
            elif compared == 0:
                unverified_hours.update(chunk_keys)

    if used_references != reference_hours:
        unexpected = sorted(str(key) for key in reference_hours - used_references)
        raise MarketDataValueError(
            f"the plan marks reference hours {unexpected} that no chunk needs; the raw data"
            " differs from the one the plan was built from (D03 §14.4)"
        )

    mismatches = [record for record in reconciled if not record.matched]
    if mismatches:
        listed = ", ".join(f"{record.series}@{record.start}" for record in mismatches[:10])
        failures.append(
            f"{len(mismatches)} reconciliation bar(s) differ from the raw data ({listed});"
            " no tolerance is applied (D03 §14.7 の 1・2, §14.18 の 6)"
        )

    # --- 対象足を作る ----------------------------------------------------------------
    built: list[Bar] = []
    not_built: list[NotBuiltBar] = []
    for target in plan.target_bars:
        key = target.hour
        data = hours[key]
        if key in unreconciled_hours:
            not_built.append(
                NotBuiltBar(
                    series=target.series, start=target.start, reason=NotBuiltReason.UNRECONCILED
                )
            )
            continue
        if data.final.outcome is HourOutcome.NOT_FETCHED:
            detail = "" if data.final.failure is None else data.final.failure.value
            not_built.append(
                NotBuiltBar(
                    series=target.series,
                    start=target.start,
                    reason=NotBuiltReason.HOUR_NOT_FETCHED,
                    detail=detail,
                )
            )
            continue
        if data.final.outcome is HourOutcome.FETCHED_EMPTY:
            not_built.append(
                NotBuiltBar(
                    series=target.series, start=target.start, reason=NotBuiltReason.PROVIDER_EMPTY
                )
            )
            continue
        made = build(target.series, target.interval, key)
        if made is None:
            not_built.append(
                NotBuiltBar(
                    series=target.series, start=target.start, reason=NotBuiltReason.NO_TICK_IN_BAR
                )
            )
            continue
        if key in unverified_hours:
            failures.append(
                f"{target.series} {target.start}: a bar was built but no reconciliation bar of"
                " its chunk could be compared (its reconciliation hours were not fetched);"
                " retry the failed hours (D03 §14.7, §14.12)"
            )
        built.append(made)

    # --- 重複（検証 4）-----------------------------------------------------------------
    seen: set[tuple[SeriesId, UtcTime]] = set()
    for bar in built:
        bar_key = (bar.series, bar.bar_start)
        if bar_key in seen:
            failures.append(f"{bar.series} {bar.bar_start}: built twice (D03 §14.7 の 4)")
        seen.add(bar_key)
        if raw.has_start(bar.series, bar.bar_start):
            failures.append(
                f"{bar.series} {bar.bar_start}: the raw data already has this bar (D03 §14.7 の 4)"
            )

    # --- 出所（検証 5）-----------------------------------------------------------------
    for bar in built:
        hour_key = TargetBar(series=bar.series, interval=bar.interval).hour
        data = hours[hour_key]
        if data.provenance is None or data.archive_path is None:
            failures.append(
                f"{bar.series} {bar.bar_start}: no provenance of its hour file {hour_key}"
                " (D03 §14.7 の 5)"
            )

    if not built:
        failures.append(
            "no bar was built (every hour was empty or not fetched, or every chunk is"
            " unreconciled); an empty refill is not an input to acceptance (D03 §14.7)"
        )

    # --- 前後の足との整合（検証 3。合否に使わない）---------------------------------------
    built_by_key = {(bar.series, bar.bar_start): bar for bar in built}
    neighbors: list[NeighborCheck] = []
    for chunk_bars in _bar_chunks(plan.target_bars):
        series = chunk_bars[0].series
        chunk_interval = Interval(start=chunk_bars[0].start, end=chunk_bars[-1].interval.end)
        made_bars = [
            built_by_key[(series, target.start)]
            for target in chunk_bars
            if (series, target.start) in built_by_key
        ]
        pip = settings.symbol(series.symbol).pip_size
        for side in (NeighborSide.BEFORE, NeighborSide.AFTER):
            neighbors.append(
                _neighbor_check(
                    series=series,
                    chunk=chunk_interval,
                    target_count=len(chunk_bars),
                    side=side,
                    made=made_bars,
                    raw=raw,
                    calendar=calendar,
                    pip=pip,
                )
            )

    # --- 1時間足と 15分足 4 本の一致（記録のみ）------------------------------------------
    consistency: list[HourlyConsistency] = []
    for key in sorted(targets_by_hour, key=lambda item: item.sort_key()):
        hourly = lookup.get((str(key.symbol), "1h"))
        quarter = lookup.get((str(key.symbol), "15m"))
        if hourly is None or quarter is None:
            continue
        one = built_by_key.get((hourly, key.start))
        quarters = [
            built_by_key.get((quarter, key.start + (HOUR / _QUARTERS) * index))
            for index in range(_QUARTERS)
        ]
        if one is None or any(bar is None for bar in quarters):
            continue
        present = [bar for bar in quarters if bar is not None]
        consistent = (
            one.open == present[0].open
            and one.close == present[-1].close
            and one.high.value == max(bar.high.value for bar in present)
            and one.low.value == min(bar.low.value for bar in present)
        )
        consistency.append(HourlyConsistency(hour=key, consistent=consistent))

    used = tuple(
        UsedHour(final=hours[key].final, provenance=hours[key].provenance) for key in plan.hour_keys
    )
    return RefillValidation(
        failures=tuple(failures),
        built_bars=tuple(sorted(built, key=lambda bar: (str(bar.series), str(bar.bar_start)))),
        not_built=tuple(sorted(not_built, key=lambda item: item.sort_key())),
        unreconciled=tuple(sorted(unreconciled, key=lambda item: item.sort_key())),
        reconciled=tuple(sorted(reconciled, key=lambda item: item.sort_key())),
        out_of_range_tick_count=out_of_range,
        bid_above_ask_count=bid_above_ask,
        neighbors=tuple(sorted(neighbors, key=lambda item: item.sort_key())),
        hourly_consistency=tuple(consistency),
        used_hours=used,
    )


def _neighbor_check(
    *,
    series: SeriesId,
    chunk: Interval,
    target_count: int,
    side: NeighborSide,
    made: Sequence[Bar],
    raw: RawBarIndex,
    calendar: TradingCalendar,
    pip: Decimal,
) -> NeighborCheck:
    """塊 1 つの直前または直後の足との比較（D03 §14.7 の 3）。"""

    def empty(status: NeighborStatus) -> NeighborCheck:
        return NeighborCheck(
            series=series,
            chunk=chunk,
            target_count=target_count,
            side=side,
            status=status,
            neighbor_start=None,
            crosses_closure=None,
            difference=None,
            difference_pips=None,
            needs_review=False,
        )

    if not made:
        return empty(NeighborStatus.NOTHING_BUILT)
    context = kernel_context()
    if side is NeighborSide.BEFORE:
        neighbor = raw.research_bar_before(series, chunk.start)
        if neighbor is None:
            return empty(NeighborStatus.NO_NEIGHBOR)
        gap = (
            None
            if neighbor.bar_end == chunk.start
            else Interval(start=neighbor.bar_end, end=chunk.start)
        )
        difference = context.subtract(made[0].open.value, neighbor.close.value)
    else:
        neighbor = raw.research_bar_after(series, chunk.end)
        if neighbor is None:
            if raw.any_bar_after(series, chunk.end):
                return empty(NeighborStatus.OUTSIDE_RESEARCH_HISTORY)
            return empty(NeighborStatus.NO_NEIGHBOR)
        gap = (
            None
            if neighbor.bar_start == chunk.end
            else Interval(start=chunk.end, end=neighbor.bar_start)
        )
        difference = context.subtract(neighbor.open.value, made[-1].close.value)
    pips = context.divide(difference, pip)
    return NeighborCheck(
        series=series,
        chunk=chunk,
        target_count=target_count,
        side=side,
        status=NeighborStatus.COMPARED,
        neighbor_start=neighbor.bar_start,
        crosses_closure=_crosses_closure(calendar, gap),
        difference=difference,
        difference_pips=pips,
        needs_review=abs(pips) > NEIGHBOR_REVIEW_THRESHOLD_PIPS,
    )
