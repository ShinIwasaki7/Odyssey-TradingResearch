"""MT5 経路の受入れ（D03 §15.3。OANDA MT5 API の 1 分足 → snapshot）。

手順（D03 §15.3 の 1〜8）:

1. 原ファイルの登録: 取得記録（`manifest.json`）と各月のファイルの SHA-256（gzip と展開後）・行数・
   最初と最後の足の時刻・状態を照合する。食い違えば何も書かずに失敗する（取得不足 (c) 破損）。
   照合そのもの（ファイルを読むこと）は adapters が行い、本モジュールは結果を突き合わせる。
2. 列対応: `time`（API の生の時刻）・`open`・`high`・`low`・`close`・`spread`。出来高は使わない
   （「出来高不明」。D12-31）。`spread` は診断にだけ残す（D12-27）。価格基準は bid の宣言。
3. 時刻の変換: API の時刻の暦日で時刻の規則の表の行を引き、`UTC = API の時刻 − offset_hours`。
   生の時刻は `api_time` として保存し、足は行番号 `time_rule_row` を持つ。
4. 構造検査: 重複（`DUPLICATE_TIMESTAMP`）・OHLC の整合（`OHLC_INCONSISTENT`）・分の境目に無い時刻
   （`IRREGULAR_INTERVAL`）は ERROR で受入れを失敗させる。カレンダーの休場の時間帯に入る 1 分足は、
   `standard` の組では `UNEXPECTED_BAR`（WARN、人間の分類が要る。D12-94 (a)）、`candidate` の組では
   足の生成に使わず件数を `CANDIDATE_OUT_OF_CALENDAR` に載せる（D12-97 (a)）。
5. 取得の範囲: 要求区間を表で UTC に直した区間の和を manifest に記録する。
6. 1 分ごとの区分（`minute_status.classify_minutes`）。無記録の区間（`UNRECORDED_MINUTES`）と
   取得不足の区間（`ACQUISITION_SHORTFALL`）を検査報告に載せる。
7. 上位足の生成（`m1_aggregation.aggregate_from_minutes`）。
8. 以降（アクセス分類と partition、暫定 manifest、分類と確定）は D03 §4 のまま。

負のスプレッド（`spread` < 0）は受入れを失敗させず、snapshot に入る 1 分足の連続区間を
`NEGATIVE_SPREAD`（WARN、記録のみ）に載せる（D03 §15.3 の 4・§15.6 の 2 v1.25。D12-131）。

人間の分類の対象は既存の明示集合のまま（MT5 経路で現れうるのは `UNEXPECTED_BAR` だけ）。
`MISSING_EXPECTED_BAR` は出さない（存在すべき足が無い理由は無記録か取得不足に機械で決まる）。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from odyssey_fx.common.money import Price, decimal_from_int, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.acceptance import PendingSnapshot, classify_partitions
from odyssey_fx.marketdata.application.m1_aggregation import (
    M1_COVERAGE_RULE_VERSION,
    BarClass,
    CoverageAggregation,
    aggregate_from_minutes,
    epoch,
    utc,
)
from odyssey_fx.marketdata.application.partition_digest import partition_digest_hex
from odyssey_fx.marketdata.application.report_digest import integrity_report_digest_hex
from odyssey_fx.marketdata.domain.access import AccessBoundaries
from odyssey_fx.marketdata.domain.bar import Bar, MinuteSource, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import IntegrityCheckFailed, MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.minute_status import (
    MINUTE,
    MinuteLedger,
    ShortfallReason,
    classify_minutes,
    merge_spans,
)
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    BasisDeclaration,
    ConversionRecord,
    Mt5AcquisitionRecord,
    Mt5ConversionRecord,
    Mt5FailedSpan,
    PartitionId,
    PartitionRecord,
    SeriesCoverageCounts,
    SeriesManifest,
    SnapshotManifest,
    SourceFile,
)
from odyssey_fx.marketdata.domain.time_rule import (
    TimeRuleTable,
    TimeRuleVariant,
    api_date_of,
)
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = [
    "MT5_ROUTE",
    "MT5_TIME_CONVENTION",
    "Mt5AcceptanceFailed",
    "Mt5Acquisition",
    "Mt5FetchEntry",
    "Mt5RawRow",
    "Mt5ReadFile",
    "acquisition_of",
    "build_mt5_pending",
    "convert_minutes",
    "months_of",
    "negative_spread_findings",
    "reaccept_mt5",
    "verify_fetch_files",
]

#: 経路の名前（D03 §15.1。列対応の宣言の `route`）。
MT5_ROUTE = "oanda_mt5_m1"

#: 時刻の規約（manifest の `conversion.time_convention`）。API の生の時刻を表で UTC に直す。
MT5_TIME_CONVENTION = "api_time_via_time_rule_table"

#: 取得記録の状態のうち取得済みを表す値（取得記録の書式。D12 付録 K-17）。
_STATUS_OK = "ok"


class Mt5AcceptanceFailed(IntegrityCheckFailed):
    """MT5 経路の受入れ全体の失敗（取得不足 (c) 破損、構造検査の ERROR。何も書かない）。"""


# --- 入力（adapters が読んだ事実） ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Mt5FetchEntry:
    """取得記録（`manifest.json`）の要求 1 件（通貨 × 月）。時刻は API の生の時刻（epoch 秒）。"""

    rid: str
    symbol: Symbol
    file: str
    status: str
    requested_from_api: int
    requested_to_api: int
    rows: int
    first_bar_time: int | None
    last_bar_time: int | None
    sha256_gz: str
    sha256_csv: str

    @property
    def acquired(self) -> bool:
        """取得記録の状態が取得済みか。"""
        return self.status == _STATUS_OK


@dataclass(frozen=True, slots=True)
class Mt5RawRow:
    """1 分足の行（列対応を当てた後。価格は文字列のまま）。"""

    api_time: int
    open: str
    high: str
    low: str
    close: str
    spread: int


@dataclass(frozen=True, slots=True)
class Mt5ReadFile:
    """adapters が読んだファイル 1 件（照合の材料と行）。"""

    entry: Mt5FetchEntry
    sha256_gz: str
    sha256_csv: str
    header_ok: bool
    rows: tuple[Mt5RawRow, ...]


def verify_fetch_files(entries: Sequence[Mt5FetchEntry], files: Mapping[str, Mt5ReadFile]) -> None:
    """取得記録と読んだファイルの照合（D03 §15.3 の 1）。食い違えば受入れ全体を失敗させる。

    状態が取得済みの要求はすべてファイルが要る。SHA-256（gzip・展開後）・行数・見出し・最初と最後の
    足の時刻が記録と一致しなければ破損（取得不足 (c)）。取得済みでない要求はファイルを読まない
    （取得不足 (b) として区間に残す）。
    """
    problems: list[str] = []
    for entry in entries:
        if not entry.acquired:
            continue
        read = files.get(entry.rid)
        if read is None:
            problems.append(f"{entry.rid}: the file {entry.file} was not read")
            continue
        if read.sha256_gz != entry.sha256_gz:
            problems.append(f"{entry.rid}: gzip SHA-256 differs from the fetch record")
        if read.sha256_csv != entry.sha256_csv:
            problems.append(f"{entry.rid}: CSV SHA-256 differs from the fetch record")
        if not read.header_ok:
            problems.append(f"{entry.rid}: the header row differs from the declared columns")
        if len(read.rows) != entry.rows:
            problems.append(f"{entry.rid}: {len(read.rows)} rows but the record says {entry.rows}")
        if read.rows:
            first, last = read.rows[0].api_time, read.rows[-1].api_time
            if first != entry.first_bar_time or last != entry.last_bar_time:
                problems.append(f"{entry.rid}: the first/last bar times differ from the record")
    if problems:
        raise Mt5AcceptanceFailed(
            "the fetched files do not match the fetch record (acquisition shortfall (c),"
            " corrupt); acceptance fails and nothing is written (D03 §15.2・§15.3 の 1): "
            + "; ".join(problems)
        )


# --- 取得の範囲（D03 §15.3 の 5） ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Mt5Acquisition:
    """1 通貨の取得の範囲（UTC の epoch 秒）。"""

    symbol: Symbol
    data_start: int
    data_end: int
    acquired: tuple[tuple[int, int], ...]
    failed: tuple[tuple[int, int, ShortfallReason], ...]

    def record(self) -> Mt5AcquisitionRecord:
        """manifest に書く形。"""
        return Mt5AcquisitionRecord(
            symbol=self.symbol,
            data_interval=Interval(start=utc(self.data_start), end=utc(self.data_end)),
            acquired=tuple(Interval(start=utc(lo), end=utc(hi)) for lo, hi in self.acquired),
            failed=tuple(
                Mt5FailedSpan(interval=Interval(start=utc(lo), end=utc(hi)), reason=reason.value)
                for lo, hi, reason in self.failed
            ),
        )

    @classmethod
    def from_record(cls, record: Mt5AcquisitionRecord) -> Mt5Acquisition:
        """manifest の記録から戻す（確定段階の再実行。原ファイルは読み直さない）。"""
        return cls(
            symbol=record.symbol,
            data_start=epoch(record.data_interval.start),
            data_end=epoch(record.data_interval.end),
            acquired=tuple((epoch(item.start), epoch(item.end)) for item in record.acquired),
            failed=tuple(
                (
                    epoch(item.interval.start),
                    epoch(item.interval.end),
                    ShortfallReason(item.reason),
                )
                for item in record.failed
            ),
        )


def _day_start(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), UTC).timestamp())


def _convert_window(
    table: TimeRuleTable, variant: TimeRuleVariant, symbol: Symbol, lo_api: int, hi_api: int
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """要求区間（API の時刻）を暦日ごとに表で UTC に直す。

    戻り値は（直せた区間、表が覆わず直せなかった区間）。直せない区間の UTC の位置は、表が無いので
    API の時刻のまま置く（取得不足 (d) の位置の目安。研究期間の外にしか起きない）。
    """
    converted: list[tuple[int, int]] = []
    uninterpretable: list[tuple[int, int]] = []
    day = api_date_of(lo_api)
    while _day_start(day) < hi_api:
        start = max(_day_start(day), lo_api)
        end = min(_day_start(day + timedelta(days=1)), hi_api)
        row = table.row_for(str(symbol), day)
        if row is None:
            uninterpretable.append((start, end))
        else:
            offset = row.offset_hours(variant) * 3600
            converted.append((start - offset, end - offset))
        day += timedelta(days=1)
    return converted, uninterpretable


def acquisition_of(
    symbol: Symbol,
    entries: Iterable[Mt5FetchEntry],
    table: TimeRuleTable,
    variant: TimeRuleVariant,
) -> Mt5Acquisition:
    """取得記録から取得の範囲を求める（D03 §15.3 の 5）。"""
    acquired: list[tuple[int, int]] = []
    failed: list[tuple[int, int, ShortfallReason]] = []
    for entry in entries:
        if entry.symbol != symbol:
            continue
        converted, uninterpretable = _convert_window(
            table, variant, symbol, entry.requested_from_api, entry.requested_to_api
        )
        if entry.acquired:
            acquired.extend(converted)
        else:
            failed.extend(
                (lo, hi, ShortfallReason.REQUEST_FAILED_OR_NOT_FETCHED) for lo, hi in converted
            )
        failed.extend(
            (lo, hi, ShortfallReason.TIME_NOT_INTERPRETABLE) for lo, hi in uninterpretable
        )
    merged = merge_spans(acquired)
    if not merged:
        raise Mt5AcceptanceFailed(
            f"no month of {symbol} was acquired; there is nothing to accept (D03 §15.2)"
        )
    data_start = merged[0][0]
    data_end = max([merged[-1][1], *(hi for _, hi, _ in failed if hi > merged[0][0])])
    return Mt5Acquisition(
        symbol=symbol,
        data_start=data_start - data_start % MINUTE,
        data_end=data_end - data_end % MINUTE,
        acquired=merged,
        failed=tuple(sorted(failed)),
    )


# --- 1 分足（D03 §15.3 の 2〜4） ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Converted:
    """変換した 1 分足と構造検査の結果。"""

    bars: tuple[Bar, ...]
    errors: tuple[CheckResult, ...]


def convert_minutes(
    files: Sequence[Mt5ReadFile],
    *,
    series: SeriesId,
    table: TimeRuleTable,
    variant: TimeRuleVariant,
) -> _Converted:
    """行を UTC の 1 分足にする（D03 §15.3 の 2〜4 の構造検査まで）。"""
    pair = str(series.symbol)
    prices: dict[str, Price] = {}

    def price(text: str) -> Price:
        value = prices.get(text)
        if value is None:
            value = prices[text] = Price(decimal_from_str(text))
        return value

    zero = decimal_from_int(0)
    seen: dict[int, int] = {}
    bars: list[Bar] = []
    errors: list[CheckResult] = []
    for read in files:
        if not read.entry.acquired:
            continue
        provenance = Provenance(kind=ProvenanceKind.OANDA_MT5, source_ref=read.entry.file)
        for row in read.rows:
            if row.api_time % MINUTE:
                start = utc(row.api_time - row.api_time % MINUTE)
                errors.append(
                    CheckResult.create(
                        CheckKind.IRREGULAR_INTERVAL,
                        series,
                        Interval(start=start, end=start + timedelta(minutes=1)),
                        detail={"reason": "api_time_not_on_minute_boundary"},
                    )
                )
                continue
            rule = table.row_for(pair, api_date_of(row.api_time))
            if rule is None:
                continue  # 表が覆わない（取得不足 (d)。区間は取得の範囲の側で数える）
            minute = row.api_time - rule.offset_hours(variant) * 3600
            if minute in seen:
                seen[minute] += 1
                continue
            seen[minute] = 1
            values = [price(text) for text in (row.open, row.high, row.low, row.close)]
            open_, high, low, close = values
            if (low > open_ or low > close or high < open_ or high < close or low > high) or any(
                value.value <= 0 for value in values
            ):
                start = utc(minute)
                errors.append(
                    CheckResult.create(
                        CheckKind.OHLC_INCONSISTENT,
                        series,
                        Interval(start=start, end=start + timedelta(minutes=1)),
                        detail={"reason": "low_high_bounds_violated"},
                    )
                )
                continue
            start = utc(minute)
            end = utc(minute + MINUTE)
            bars.append(
                Bar(
                    series=series,
                    interval=Interval(start=start, end=end),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=zero,
                    available_at=end,
                    provenance=provenance,
                    minute=MinuteSource(
                        api_time=row.api_time, time_rule_row=rule.row, spread=row.spread
                    ),
                )
            )
    for minute, count in seen.items():
        if count > 1:
            start = utc(minute)
            errors.append(
                CheckResult.create(
                    CheckKind.DUPLICATE_TIMESTAMP,
                    series,
                    Interval(start=start, end=start + timedelta(microseconds=1)),
                    detail={"rows": str(count)},
                )
            )
    bars.sort(key=lambda bar: bar.bar_start.value)
    return _Converted(bars=tuple(bars), errors=tuple(errors))


# --- 組み立て（D03 §15.3 の 4〜8） -------------------------------------------------------


def _sessions(calendar: TradingCalendar, lo: int, hi: int) -> list[tuple[int, int]]:
    window = Interval(start=utc(lo), end=utc(hi))
    return [(epoch(item.start), epoch(item.end)) for item in calendar.sessions(window)]


def _in_sessions(starts: Sequence[int], sessions: Sequence[tuple[int, int]], minute: int) -> bool:
    import bisect

    index = bisect.bisect_right(starts, minute) - 1
    return index >= 0 and minute < sessions[index][1]


#: 週の区切りの暦（D12-123: 集計 PR と同じ NY 暦の日曜〜土曜）。
_NEW_YORK = ZoneInfo("America/New_York")


def months_of(entries: Iterable[Mt5FetchEntry]) -> tuple[str, ...]:
    """要求の年月（API の時刻の要求の始まりの `YYYY-MM`。昇順・重複なし。D12-125）。"""
    found = {
        datetime.fromtimestamp(entry.requested_from_api, UTC).strftime("%Y-%m") for entry in entries
    }
    return tuple(sorted(found))


def _week_of(minute: int) -> Interval:
    """休場帯の足を数える週（D12-97 (a) の「週ごと」）。

    NY 暦の日曜 0:00 から次の日曜 0:00 まで（日曜〜土曜）。全期間の集計（PR #85）の週と揃える
    （D12-123。人間の決定の原文は D12 §0.1.30）。
    """
    local = utc(minute).value.astimezone(_NEW_YORK)
    # 日曜（weekday 6）まで戻る。
    sunday = local.date() - timedelta(days=(local.weekday() + 1) % 7)
    start = datetime.combine(sunday, time(0, 0), _NEW_YORK)
    end = datetime.combine(sunday + timedelta(days=7), time(0, 0), _NEW_YORK)
    return Interval(start=UtcTime(start.astimezone(UTC)), end=UtcTime(end.astimezone(UTC)))


def negative_spread_findings(
    minute_series: SeriesId, minute_bars: Iterable[Bar]
) -> tuple[CheckResult, ...]:
    """負のスプレッドの連続区間を `NEGATIVE_SPREAD`（WARN、記録のみ）にする。

    D03 §15.6 の 2 v1.25。数えるのは渡された足（snapshot に入る 1 分足）だけ（D12 §12.8 の
    追加 A の決定）。隣り合う UTC の分の足がどちらも負のときだけ 1 区間につなぐ（間に無記録の分・
    休場・負でない分があれば別の区間）。detail は分数だけで、spread の値は書かない（原値は
    partition の列が正本）。受入れを失敗させない（D12-131）。
    """
    runs: list[tuple[int, int]] = []
    for minute in sorted(
        epoch(bar.bar_start)
        for bar in minute_bars
        if bar.minute is not None and bar.minute.negative_spread
    ):
        if runs and runs[-1][1] == minute:
            runs[-1] = (runs[-1][0], minute + MINUTE)
        else:
            runs.append((minute, minute + MINUTE))
    return tuple(
        CheckResult.create(
            CheckKind.NEGATIVE_SPREAD,
            minute_series,
            Interval(start=utc(lo), end=utc(hi)),
            detail={"minutes": str((hi - lo) // MINUTE)},
        )
        for lo, hi in runs
    )


@dataclass(frozen=True, slots=True)
class _Mt5Built:
    """系列ごとの足・台帳・検査結果。"""

    minute_series: SeriesId
    minute_bars: tuple[Bar, ...]
    ledger: MinuteLedger
    aggregations: Mapping[SeriesId, CoverageAggregation]
    findings: tuple[CheckResult, ...]
    weeks_checked: int
    weeks_failed: int


def _build_series(
    *,
    minute_series: SeriesId,
    minute_bars: Sequence[Bar],
    acquisition: Mt5Acquisition,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    targets: Sequence[str],
    variant: TimeRuleVariant,
) -> _Mt5Built:
    sessions = _sessions(calendar, acquisition.data_start, acquisition.data_end)
    session_starts = [lo for lo, _ in sessions]
    findings: list[CheckResult] = []
    kept: list[Bar] = []
    recorded: list[int] = []
    outside_by_week: dict[Interval, int] = {}
    failed_weeks: set[Interval] = set()
    weeks_with_data: set[Interval] = set()
    for bar in minute_bars:
        minute = epoch(bar.bar_start)
        if not acquisition.data_start <= minute < acquisition.data_end:
            continue
        week = _week_of(minute)
        weeks_with_data.add(week)
        if _in_sessions(session_starts, sessions, minute):
            kept.append(bar)
            recorded.append(minute)
            continue
        failed_weeks.add(week)
        if variant is TimeRuleVariant.CANDIDATE:
            # D12-97 (a): 候補の組では足の生成に使わず（分母にも分子にも入れない）、件数を報告する。
            outside_by_week[week] = outside_by_week.get(week, 0) + 1
            continue
        # D12-94 (a): 標準の組では休場帯の足として報告し、人間が分類するまで確定しない。
        kept.append(bar)
        findings.append(
            CheckResult.create(
                CheckKind.UNEXPECTED_BAR,
                minute_series,
                bar.interval,
                detail={"reason": "outside_declared_sessions"},
            )
        )
    findings.extend(negative_spread_findings(minute_series, kept))
    for week, count in sorted(outside_by_week.items(), key=lambda item: item[0].start.value):
        findings.append(
            CheckResult.create(
                CheckKind.CANDIDATE_OUT_OF_CALENDAR,
                minute_series,
                week,
                detail={"bars": str(count), "variant": variant.value},
            )
        )
    ledger = classify_minutes(
        sessions=sessions,
        recorded_minutes=recorded,
        acquired=acquisition.acquired,
        failed=acquisition.failed,
        data_start=acquisition.data_start,
        data_end=acquisition.data_end,
    )
    for lo, hi in ledger.unrecorded:
        findings.append(
            CheckResult.create(
                CheckKind.UNRECORDED_MINUTES,
                minute_series,
                Interval(start=utc(lo), end=utc(hi)),
                detail={"minutes": str((hi - lo) // MINUTE)},
            )
        )
    for span in ledger.shortfall:
        findings.append(
            CheckResult.create(
                CheckKind.ACQUISITION_SHORTFALL,
                minute_series,
                Interval(start=utc(span.start), end=utc(span.end)),
                detail={
                    "minutes": str((span.end - span.start) // MINUTE),
                    "reasons": span.reason.value,
                },
            )
        )
    aggregations: dict[SeriesId, CoverageAggregation] = {}
    for target in targets:
        definition = timeframe_defs.get(target)
        if definition is None:
            raise MarketDataValueError(f"no timeframe definition was supplied for {target!r}")
        target_series = SeriesId(
            symbol=minute_series.symbol, timeframe=definition.ref, basis=minute_series.basis
        )
        result = aggregate_from_minutes(
            kept, ledger, target_series=target_series, target_def=definition, calendar=calendar
        )
        aggregations[target_series] = result
        findings.extend(result.findings)
    return _Mt5Built(
        minute_series=minute_series,
        minute_bars=tuple(kept),
        ledger=ledger,
        aggregations=aggregations,
        findings=tuple(findings),
        weeks_checked=len(weeks_with_data),
        weeks_failed=len(failed_weeks),
    )


def _require_no_errors(report: IntegrityReport) -> None:
    errors = report.errors
    if not errors:
        return
    counts: dict[str, int] = {}
    for result in errors:
        counts[result.kind.value] = counts.get(result.kind.value, 0) + 1
    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    raise Mt5AcceptanceFailed(
        f"the integrity check reported {len(errors)} error(s) ({summary}); acceptance fails and"
        " no snapshot is produced (D03 §15.3 の 4)"
    )


def _counts_of(built: _Mt5Built, series: SeriesId) -> SeriesCoverageCounts:
    unrecorded, _, _ = built.ledger.unrecorded_runs_total()
    shortfall = sum((span.end - span.start) // MINUTE for span in built.ledger.shortfall)
    if series == built.minute_series:
        return SeriesCoverageCounts(
            unrecorded_minutes=unrecorded,
            shortfall_minutes=shortfall,
            built_bars=len(built.minute_bars),
            partial_bars=0,
            no_record_bars=0,
            shortfall_bars=0,
        )
    aggregation = built.aggregations[series]
    return SeriesCoverageCounts(
        unrecorded_minutes=unrecorded,
        shortfall_minutes=shortfall,
        built_bars=len(aggregation.bars),
        partial_bars=aggregation.count(BarClass.PARTIAL),
        no_record_bars=aggregation.count(BarClass.NO_RECORD),
        shortfall_bars=aggregation.count(BarClass.SHORTFALL),
    )


def _assemble(
    *,
    created_at: UtcTime,
    built: _Mt5Built,
    sources: tuple[SourceFile, ...],
    boundaries: AccessBoundaries,
    basis_declaration: BasisDeclaration,
    conversion: ConversionRecord,
    extra_errors: Sequence[CheckResult] = (),
) -> PendingSnapshot:
    report = IntegrityReport(results=tuple(dict.fromkeys((*built.findings, *extra_errors))))
    _require_no_errors(report)
    bars_by_series: dict[SeriesId, tuple[Bar, ...]] = {built.minute_series: built.minute_bars}
    for series, aggregation in built.aggregations.items():
        bars_by_series[series] = aggregation.bars
    series_manifests: list[SeriesManifest] = []
    partitions: list[PartitionRecord] = []
    partition_bars: dict[PartitionId, tuple[Bar, ...]] = {}
    for series, bars in bars_by_series.items():
        if not bars:
            # 1 本も作れなかった系列は記録しない（作れなかった事実は報告に残る。D03 §5.2 と同じ）。
            continue
        ordered = tuple(sorted(bars, key=lambda bar: bar.bar_start.value))
        partition_ids: list[PartitionId] = []
        for access_class, group in classify_partitions(ordered, boundaries).items():
            partition_id = PartitionId(series=series, access_class=access_class)
            partition_ids.append(partition_id)
            partition_bars[partition_id] = group
            partitions.append(
                PartitionRecord(
                    partition_id=partition_id,
                    interval=Interval(start=group[0].bar_start, end=group[-1].bar_end),
                    bar_count=len(group),
                    digest=ContentDigest.sha256(partition_digest_hex(group)),
                )
            )
        spans = [record.interval for record in partitions if record.partition_id.series == series]
        series_manifests.append(
            SeriesManifest(
                series_id=series,
                covered_interval=Interval(
                    start=min((span.start for span in spans), key=lambda value: value.value),
                    end=max((span.end for span in spans), key=lambda value: value.value),
                ),
                bar_count=len(ordered),
                partitions=tuple(partition_ids),
                coverage_counts=_counts_of(built, series),
            )
        )
    manifest = SnapshotManifest(
        created_at=created_at,
        basis_declaration=basis_declaration,
        sources=sources,
        conversion=conversion,
        series=tuple(series_manifests),
        partitions=tuple(partitions),
        integrity_report_ref=ContentDigest.sha256(integrity_report_digest_hex(report)),
        closure_decisions=(),
    )
    return PendingSnapshot(manifest=manifest, report=report, partition_bars=partition_bars)


def build_mt5_pending(
    *,
    created_at: UtcTime,
    symbol: Symbol,
    entries: Sequence[Mt5FetchEntry],
    files: Mapping[str, Mt5ReadFile],
    source_paths: Mapping[str, str],
    fetch_manifest_sha256: str,
    column_mapping: str,
    table: TimeRuleTable,
    variant: TimeRuleVariant,
    calendar: TradingCalendar,
    minute_def: TimeframeDefinition,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    targets: Sequence[str],
    boundaries: AccessBoundaries,
    basis_declaration: BasisDeclaration,
    code_version: str,
) -> PendingSnapshot:
    """MT5 経路の暫定 snapshot を組み立てる（D03 §15.3 の 1〜8）。

    `entries` は取得記録のこの通貨の要求すべて、`files` は読んだファイル（要求の `rid` で引く）、
    `source_paths` は manifest に書く原ファイルの POSIX 相対パス（`rid` で引く）。
    """
    if basis_declaration.value is not PriceBasis.BID:
        raise MarketDataValueError("the MT5 route declares bid prices (D12-26)")
    own = [entry for entry in entries if entry.symbol == symbol]
    verify_fetch_files(own, files)
    acquisition = acquisition_of(symbol, own, table, variant)
    minute_series = SeriesId(symbol=symbol, timeframe=minute_def.ref, basis=PriceBasis.BID)
    ordered_files = [files[entry.rid] for entry in own if entry.acquired]
    converted = convert_minutes(ordered_files, series=minute_series, table=table, variant=variant)
    built = _build_series(
        minute_series=minute_series,
        minute_bars=converted.bars,
        acquisition=acquisition,
        calendar=calendar,
        timeframe_defs=timeframe_defs,
        targets=targets,
        variant=variant,
    )
    sources = tuple(
        SourceFile(
            path=source_paths[entry.rid],
            sha256=entry.sha256_gz,
            rows=entry.rows,
            symbol=symbol,
            timeframe=minute_def.ref,
            declared_basis=PriceBasis.BID,
            provenance_counts=((ProvenanceKind.OANDA_MT5.value, entry.rows),),
        )
        for entry in own
        if entry.acquired
    )
    conversion = ConversionRecord(
        code_version=code_version,
        time_convention=MT5_TIME_CONVENTION,
        aggregation_rule_version=M1_COVERAGE_RULE_VERSION,
        calendar_id=calendar.id,
        calendar_version=calendar.version,
        mt5=Mt5ConversionRecord(
            route=MT5_ROUTE,
            column_mapping=column_mapping,
            fetch_manifest_sha256=fetch_manifest_sha256,
            time_rule_table=table.ref(variant),
            acquisitions=(acquisition.record(),),
            weeks_checked=built.weeks_checked,
            weeks_failed=built.weeks_failed,
            months=months_of(own),
        ),
    )
    return _assemble(
        created_at=created_at,
        built=built,
        sources=sources,
        boundaries=boundaries,
        basis_declaration=basis_declaration,
        conversion=conversion,
        extra_errors=converted.errors,
    )


def reaccept_mt5(
    pending: PendingSnapshot,
    *,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    targets: Sequence[str],
    boundaries: AccessBoundaries,
    excluded: Collection[tuple[SeriesId, Interval]] = frozenset(),
) -> PendingSnapshot:
    """MT5 経路の確定段階の再実行（D03 §15.3 の 8 → §4 の 9）。

    暫定 snapshot の 1 分足と manifest の取得の範囲を再利用し、新しいカレンダーで 1 分ごとの区分と
    上位足を作り直す。原ファイルは読み直さない（D03 §4 の 9 と同じ理由）。`excluded` はセッション外
    データ異常として除外する 1 分足。
    """
    conversion = pending.manifest.conversion
    mt5 = conversion.mt5
    if mt5 is None:
        raise MarketDataValueError("reaccept_mt5 requires an MT5 snapshot (D03 §15.1)")
    excluded_set = frozenset(excluded)
    minute_bars: dict[SeriesId, list[Bar]] = {}
    for partition_id, bars in pending.partition_bars.items():
        for bar in bars:
            if bar.provenance.kind is not ProvenanceKind.OANDA_MT5:
                continue
            if (bar.series, bar.interval) in excluded_set:
                continue
            minute_bars.setdefault(partition_id.series, []).append(bar)
    if len(minute_bars) != 1:
        raise MarketDataValueError(
            "an MT5 snapshot carries exactly one 1m series; the provisional snapshot has"
            f" {len(minute_bars)}"
        )
    ((minute_series, minute_list),) = minute_bars.items()
    record = mt5.acquisition_of(minute_series.symbol)
    if record is None:
        raise MarketDataValueError(f"the manifest has no acquisition record for {minute_series}")
    built = _build_series(
        minute_series=minute_series,
        minute_bars=sorted(minute_list, key=lambda bar: bar.bar_start.value),
        acquisition=Mt5Acquisition.from_record(record),
        calendar=calendar,
        timeframe_defs=timeframe_defs,
        targets=targets,
        variant=mt5.time_rule_table.variant,
    )
    new_mt5 = Mt5ConversionRecord(
        route=mt5.route,
        column_mapping=mt5.column_mapping,
        fetch_manifest_sha256=mt5.fetch_manifest_sha256,
        time_rule_table=mt5.time_rule_table,
        acquisitions=mt5.acquisitions,
        weeks_checked=built.weeks_checked,
        weeks_failed=built.weeks_failed,
        months=mt5.months,
    )
    new_conversion = ConversionRecord(
        code_version=conversion.code_version,
        time_convention=conversion.time_convention,
        aggregation_rule_version=conversion.aggregation_rule_version,
        calendar_id=calendar.id,
        calendar_version=calendar.version,
        mt5=new_mt5,
    )
    return _assemble(
        created_at=pending.manifest.created_at,
        built=built,
        sources=pending.manifest.sources,
        boundaries=boundaries,
        basis_declaration=pending.manifest.basis_declaration,
        conversion=new_conversion,
    )
