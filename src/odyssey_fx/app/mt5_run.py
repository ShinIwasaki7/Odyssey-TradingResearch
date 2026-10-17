"""MT5 経路の run の結線（D06 §18.2・§18.8〜§18.10）。

承認済みの snapshot と戦略の宣言から、run の開始の時に決まるもの（被覆の報告・
適合検査で欠けに数えない
区間・表20・表22・約定の側の 1 分足のビュー）を組み立てる。業務の規則は持たず、規則は市場データ・
バックテストの層の関数が持つ（D01 §7）。HistData の経路の run でも表20 は同じ形で作る。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from fractions import Fraction
from pathlib import Path

from odyssey_fx.app.config.time_rules import (
    approved_list_sha256,
    load_approved_tables,
    load_time_rule_table,
    require_approved,
)
from odyssey_fx.backtest.application.run_backtest import Mt5Capability
from odyssey_fx.backtest.engine.loop import PublicationEventView
from odyssey_fx.backtest.execution.protection_hits import ExcusedSpans
from odyssey_fx.backtest.trace.manifest import CoverageCapability, SeriesCoverageReport
from odyssey_fx.backtest.trace.recorder import HistoryResetRow, PublicationRow
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.mt5_observation import Mt5MinuteView, Mt5Observation
from odyssey_fx.marketdata.application.publication import PublicationKind
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.readiness import ScheduledBar, history_resets
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.time_rule import TimeRuleMark, TimeRuleTable, TimeRuleTableRef
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy, ResolvedMarketSource
from odyssey_fx.strategy.declarations.read_spec import BarsWindow, DurationWindow, HistoryWindow

__all__ = [
    "DEFAULT_APPROVED_LIST",
    "Mt5RunParts",
    "build_mt5_run_parts",
    "publication_rows",
    "strategy_windows",
]

#: 承認済みの時刻の規則の表の一覧（D03 §15.9 の 2）。run の開始の時に読む（D06 §18.9 の 3）。
DEFAULT_APPROVED_LIST = Path("docs/design/time_rules/approved_time_rule_tables.yaml")

_TINY = timedelta(microseconds=1)
_MINUTE_TIMEFRAME = "1m"


@dataclass(frozen=True, slots=True)
class Mt5RunParts:
    """MT5 経路の run の結線の部品。"""

    minutes: Mt5MinuteView
    capability: Mt5Capability
    history_resets: tuple[HistoryResetRow, ...]
    minute_series: SeriesId


def publication_rows(
    feed: Iterable[PublicationEventView],
    bars: Mapping[BarKey, Bar],
    schedules: Mapping[SeriesId, SeriesSchedule],
    run_interval: Interval,
) -> tuple[PublicationRow, ...]:
    """表20: run 区間に戦略へ公開した足 1 本につき 1 行（D06 §9.2 v1.11・§18.10）。

    実現した公開時刻は公開フィードのイベントの時刻（遅延シナリオを当てた後）、予定の公開時刻は
    通常の公開予定（足の終了＋通常遅延）。被覆の列は被覆の属性を持つ足（MT5 経路の上位足）だけ。
    行は `(実現した公開時刻, 系列, 足の開始)` の順に並べる（フィードの同時刻の並びに依存しない）。
    """
    rows: list[PublicationRow] = []
    for event in feed:
        if event.kind.value != PublicationKind.PUBLICATION.value:
            continue
        if not (run_interval.start <= event.at <= run_interval.end):
            continue
        bar = bars.get(event.bar_key)
        if bar is None:  # pragma: no cover - 公開は実在する足からだけ出る
            continue
        coverage = bar.coverage
        rows.append(
            PublicationRow(
                bar_key=bar.key,
                bar_end=bar.bar_end,
                scheduled_at=schedules[bar.series].scheduled_at(bar.bar_end),
                available_at=event.at,
                expected_minutes=None if coverage is None else coverage.expected_minutes,
                recorded_minutes=None if coverage is None else coverage.recorded_minutes,
                first_observed_at=None if coverage is None else coverage.first_observed_at,
                last_observed_at=None if coverage is None else coverage.last_observed_at,
                time_rule_row=None if coverage is None else coverage.time_rule_row,
            )
        )
    rows.sort(
        key=lambda row: (
            row.available_at.value,
            str(row.bar_key.series),
            row.bar_key.bar_start.value,
        )
    )
    return tuple(rows)


def strategy_windows(compiled: CompiledStrategy) -> dict[SeriesId, set[int]]:
    """戦略が読む市場データの系列と、その窓の本数（D06 §18.10 の表22 の「窓の本数」）。

    本数窓（`BarsWindow`）は本数＋末尾から除く本数、最新足だけを読む入力は 1 本。経過時間の窓は
    本数に直せない（足の長さが変わる）ので表22 に載せない（準備の判定は as-of
    ビューが同じ規則で行う）。
    """
    out: dict[SeriesId, set[int]] = {}
    for series, windows in strategy_reads(compiled).items():
        counts = {item for item in windows if isinstance(item, int)}
        if counts:
            out[series] = counts
    return out


def strategy_reads(compiled: CompiledStrategy) -> dict[SeriesId, set[int | timedelta]]:
    """戦略が読む市場データの系列と、その窓（本数、または経過時間）。

    能力検査の取得不足の照合と被覆の報告は、経過時間の窓だけで読む系列も含めてここから作る
    （D06 §18.9 の 2・4）。
    """
    out: dict[SeriesId, set[int | timedelta]] = {}
    for component in compiled.components:
        for plan in component.input_plans.values():
            for source in plan.sources:
                if not isinstance(source, ResolvedMarketSource):
                    continue
                window = plan.resolved_window
                offset = 0
                read = plan.read_spec
                if isinstance(read, HistoryWindow):
                    offset = read.exclude_latest_bars
                item: int | timedelta
                if window is None:
                    item = 1
                elif isinstance(window, BarsWindow) and isinstance(window.count, int):
                    item = window.count + offset
                elif isinstance(window, DurationWindow):
                    item = window.duration
                else:  # pragma: no cover - コンパイラが解決済みの窓だけを渡す
                    continue
                out.setdefault(source.series, set()).add(item)
    return out


def _scheduled_bars(
    schedule: SeriesSchedule,
    bars: Sequence[Bar],
    no_record: frozenset[UtcTime],
    *,
    start: UtcTime,
    end: UtcTime,
) -> list[ScheduledBar]:
    built = {bar.bar_start: bar for bar in bars}
    out: list[ScheduledBar] = []
    for bar_start in schedule.calendar.expected_bar_starts(
        schedule.timeframe_def, Interval(start=start, end=end)
    ):
        interval = schedule.timeframe_def.expected_interval(schedule.calendar, bar_start)
        if interval is None:  # pragma: no cover
            continue
        found = built.get(bar_start)
        if found is None and bar_start not in no_record:
            # 作られず、無記録の足でもない（取得不足、またはデータの範囲の外）。準備の連続を切る。
            out.append(
                ScheduledBar(
                    bar_start=bar_start,
                    available_at=schedule.scheduled_at(interval.end),
                    built=False,
                )
            )
            continue
        out.append(
            ScheduledBar(
                bar_start=bar_start,
                available_at=schedule.scheduled_at(interval.end),
                built=found is not None,
                no_record=found is None,
            )
        )
    return out


def _history_reset_rows(
    observation: Mt5Observation,
    compiled: CompiledStrategy,
    schedules: Mapping[SeriesId, SeriesSchedule],
    bars_of: Mapping[SeriesId, tuple[Bar, ...]],
    run_interval: Interval,
) -> tuple[HistoryResetRow, ...]:
    """表22（D06 §18.10）。run の準備に効く無記録の足（公開の予定が run の終わり以前で、準備が
    整うのが run の始まりより後、またはデータの終わりまで整わないもの）だけを載せる。"""
    rows: list[HistoryResetRow] = []
    for series, windows in sorted(
        strategy_windows(compiled).items(), key=lambda item: str(item[0])
    ):
        schedule = schedules.get(series)
        bars = bars_of.get(series, ())
        if schedule is None or not bars:
            continue
        no_record = observation.no_record.get(series, frozenset())
        if not no_record:
            continue
        sequence = _scheduled_bars(
            schedule, bars, no_record, start=bars[0].bar_start, end=run_interval.end
        )
        for window in sorted(windows):
            for reset in history_resets(sequence, window):
                if reset.no_record_available_at > run_interval.end:
                    continue
                if reset.ready_at is not None and reset.ready_at <= run_interval.start:
                    continue
                rows.append(
                    HistoryResetRow(
                        series=series,
                        no_record_bar_key=BarKey(
                            series=series, bar_start=reset.no_record_bar_start
                        ),
                        window_bars=window,
                        ready_bar_key=None
                        if reset.ready_bar_start is None
                        else BarKey(series=series, bar_start=reset.ready_bar_start),
                        ready_at=reset.ready_at,
                    )
                )
    return tuple(rows)


def _lookback_start(schedule: SeriesSchedule, start: UtcTime, count: int) -> UtcTime:
    """`start` より前の予定の足を `count` 本さかのぼった足の開始（履歴の分の区間の始まり）。"""
    definition = schedule.timeframe_def
    probe = definition.boundaries(start - _TINY).start
    earliest = start
    found = 0
    for _ in range(max(count * 50, 500)):
        interval = definition.expected_interval(schedule.calendar, probe)
        if interval is not None:
            earliest = interval.start
            found += 1
            if found >= count:
                break
        probe = definition.boundaries(probe - _TINY).start
    return earliest


def _merge_shortfall(
    observation: Mt5Observation, series: Iterable[SeriesId], interval: Interval
) -> tuple[tuple[Interval, str], ...]:
    seen: dict[tuple[str, str, str], tuple[Interval, str]] = {}
    for item in series:
        for span, reasons in observation.shortfall_overlapping(item, interval):
            seen.setdefault((str(span.start), str(span.end), reasons), (span, reasons))
    return tuple(seen[key] for key in sorted(seen))


def _series_report(
    observation: Mt5Observation,
    series: SeriesId,
    minute_series: SeriesId,
    interval: Interval,
    bars: Sequence[Bar],
) -> SeriesCoverageReport:
    runs = observation.unrecorded_in(minute_series, interval)
    lengths = [int((run.end - run.start) / timedelta(minutes=1)) for run in runs]
    longest_index = max(range(len(runs)), key=lambda index: lengths[index], default=None)
    in_range = [bar for bar in bars if interval.start <= bar.bar_start < interval.end]
    partial = [
        bar for bar in in_range if bar.coverage is not None and not bar.coverage.is_fully_observed
    ]
    ratios = [
        Fraction(bar.coverage.recorded_minutes, bar.coverage.expected_minutes)
        for bar in in_range
        if bar.coverage is not None
    ]
    lowest = min(ratios, default=None)
    no_record = [
        item
        for item in observation.no_record_intervals.get(series, ())
        if interval.start <= item.start < interval.end
    ]
    return SeriesCoverageReport(
        series=series,
        interval=interval,
        unrecorded_minutes=sum(lengths),
        unrecorded_runs=len(runs),
        longest_unrecorded_minutes=max(lengths, default=0),
        longest_unrecorded=None if longest_index is None else runs[longest_index],
        partial_bars=len(partial),
        no_record_bars=len(no_record),
        min_coverage=None if lowest is None else f"{lowest.numerator}/{lowest.denominator}",
        shortfall=_merge_shortfall(observation, (series, minute_series), interval),
    )


def _mark_days(
    table: TimeRuleTable | None, symbol: str, interval: Interval
) -> tuple[tuple[str, int], ...]:
    """run 区間の暦日（UTC）を区分の印ごとに数える（D06 §18.9 の 4）。"""
    if table is None:
        return ()
    counts = dict.fromkeys((mark.value for mark in TimeRuleMark), 0)
    day = interval.start.value.date()
    last = (interval.end - _TINY).value.date()
    while day <= last:
        row = table.row_for(symbol, day)
        if row is not None:
            counts[row.mark.value] += 1
        day += timedelta(days=1)
    return tuple(counts.items())


def _excused(
    observation: Mt5Observation, minute_series: SeriesId, calendar: TradingCalendar
) -> ExcusedSpans:
    def excused(child: SeriesId, interval: Interval) -> tuple[Interval, ...]:
        spans = list(observation.unrecorded_in(minute_series, interval))
        cursor = interval.start
        for session in calendar.sessions(interval):
            if cursor < session.start:
                spans.append(Interval(start=cursor, end=session.start))
            cursor = max(cursor, session.end, key=lambda value: value.value)
        if cursor < interval.end:
            spans.append(Interval(start=cursor, end=interval.end))
        del child
        return tuple(spans)

    return excused


def _load_table(
    repo_root: Path, approved_list: Path, ref: TimeRuleTableRef
) -> tuple[TimeRuleTable | None, bool]:
    """snapshot の表を承認済みの一覧から引いて読む（D06 §18.9 の 3）。承認済みでなければ偽。"""
    approved = load_approved_tables(approved_list)
    try:
        row = require_approved(
            approved, table_id=ref.table_id, version=ref.version, sha256=ref.sha256
        )
    except MarketDataValueError:
        return None, False
    return load_time_rule_table(repo_root / row.file, approved_list), True


def build_mt5_run_parts(
    *,
    observation: Mt5Observation,
    compiled: CompiledStrategy,
    execution_series: SeriesId,
    hierarchy: Sequence[SeriesId],
    schedules: Mapping[SeriesId, SeriesSchedule],
    bars_of: Mapping[SeriesId, tuple[Bar, ...]],
    calendar: TradingCalendar,
    run_interval: Interval,
    repo_root: Path,
    approved_list: Path = DEFAULT_APPROVED_LIST,
) -> Mt5RunParts:
    """MT5 経路の run の部品を組み立てる（D06 §18.2・§18.9・§18.10）。"""
    minute_candidates = [
        series
        for series in schedules
        if series.symbol == execution_series.symbol
        and series.timeframe.id == _MINUTE_TIMEFRAME
        and series.basis == execution_series.basis
    ]
    if len(minute_candidates) != 1:
        raise MarketDataValueError(
            f"an MT5 snapshot must carry one 1m series for {execution_series.symbol} (D06 §18.2)"
        )
    minute_series = minute_candidates[0]
    approved_path = repo_root / approved_list
    table, approved = _load_table(repo_root, approved_path, observation.table_ref)
    reports: list[SeriesCoverageReport] = []
    executed = [execution_series, *(item for item in hierarchy if item != execution_series)]
    for series in executed:
        reports.append(
            _series_report(
                observation, series, minute_series, run_interval, bars_of.get(series, ())
            )
        )
    for series, reads in sorted(strategy_reads(compiled).items(), key=lambda item: str(item[0])):
        if series in executed:
            continue
        schedule = schedules.get(series)
        if schedule is None:
            continue
        counts = [item for item in reads if isinstance(item, int)]
        durations = [item for item in reads if isinstance(item, timedelta)]
        starts = [run_interval.start]
        if counts:
            starts.append(_lookback_start(schedule, run_interval.start, max(counts)))
        if durations:
            # 経過時間の窓は末尾の足の終わりから遡るので、足 1 本ぶんの余裕を足して数える。
            starts.append(
                _lookback_start(
                    schedule,
                    run_interval.start - max(durations),
                    1,
                )
            )
        start = min(starts, key=lambda value: value.value)
        reports.append(
            _series_report(
                observation,
                series,
                minute_series,
                Interval(start=start, end=run_interval.end),
                bars_of.get(series, ()),
            )
        )
    coverage = CoverageCapability(
        time_rule_table=observation.table_ref,
        approved_list_sha256=approved_list_sha256(approved_path),
        table_approved=approved,
        series=tuple(reports),
        mark_days=_mark_days(table, str(execution_series.symbol), run_interval),
    )
    excused = _excused(observation, minute_series, calendar)
    return Mt5RunParts(
        minutes=Mt5MinuteView(
            observation=observation,
            minute_series=minute_series,
            bars=bars_of.get(minute_series, ()),
        ),
        capability=Mt5Capability(coverage=coverage, excused=excused),
        history_resets=_history_reset_rows(observation, compiled, schedules, bars_of, run_interval),
        minute_series=minute_series,
    )
