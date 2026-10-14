"""再取得（補充）の取得計画を作る（D03 §14.4・§14.10・§14.12 の出来事1）。

承認済み snapshot の manifest の確定済み分類から対象足を機械的に導き、対象の時間と照合用の
時間を決めて、取得計画（`RefillPlan`）を組み立てる。作業ディレクトリへ書くのは
`create_plan` だけで、それ以外は実時計・通信・ファイルを持たない規則である（D01 §2.2
規則2、D03 §14.16）。原データは `RawBarSource` 越しに読む。

**対象足**（D03 §14.4）は次をすべて満たす足:

1. `resolved_classifications` の要素で、検査種別が「存在すべき足の欠落」
   （`MISSING_EXPECTED_BAR`）、分類結果がデータ欠損（`DATA_GAP`）。
2. 系列が原系列（manifest の `sources` が与える 15分足・1時間足の系列）。
3. 指定したカレンダーで期待区間が存在し（休場にならない）、整列上の区間と一致する。一致
   しない足があれば計画を作らずに構造エラーで止める（規則の想定外なので黙って扱わない）。
4. 足の終端が研究履歴区分に入る（区分を選ぶ入力は持たない。RF-3 の決定）。
5. 絞り込みを指定したときは、その銘柄・区間に入る。

**照合用の時間**（D03 §14.4、§14.18 の 2）: 対象の時間の連続する塊について、塊のどの時間
ファイルにも照合できる足（原データに実在する研究履歴区分の足）が無いときだけ、塊の直前に
ある「原データに 15分足 4 本と 1時間足 1 本がそろう最も近い時間」（24 時間以内）を 1 本だけ
足す。

**両側照合**（D03 §14.4.1。v1.22）: 直前 24 時間以内にも見つからない塊は、欠落の前後の両側で
照合する。前側は欠落の直前の原データの足を含む時間を起点に、その取引日の中で原データにそろう
最後の 1 時間まで遡り、後側は欠落の直後の原データの足を含む時間を起点に、その取引日の中でそろう
最初の 1 時間まで進む（R-1d と PR #78 の仮置き 1・2 の決定）。取引日の中にそろう時間が無い側・
起点が研究履歴区分の外の側は「計画の時点で取得不能」とし、照合用の時間を足さない。

**照合の方法の記録**（D03 §14.7.1 の記録先 1。v1.22）: 計画は、時間ファイルの印の中身（方法と、
両側照合ではどの塊の前側か後側かと距離）と、塊ごとの照合の記録の列を持つ（`plan_id` の対象）。
規則の版は計画の形式の版（`refill_plan_v2`）で区別する。v1.22 より前の計画（`refill_plan_v1`）の
検証は、その計画を作った規則（両側照合なし。塊に照合できる足も照合用の時間も無ければ「未照合」）
で照合の配置を計算し直す（`plan_layout` の `bilateral=False`）。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.acceptance import (
    ColumnMapping,
    RawFile,
    normalize_rows_with_correction,
)
from odyssey_fx.marketdata.application.ports import RawBarSource, RefillStore
from odyssey_fx.marketdata.domain.access import AccessBoundaries, AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.classification import ClassificationOutcome
from odyssey_fx.marketdata.domain.errors import (
    MarketDataValueError,
    RefillAlreadyFinalized,
    RefillPlanAlreadyExists,
    RefillPlanEmpty,
    RefillStoreInconsistent,
    SnapshotNotApproved,
    TimeLabelCorrectionFailed,
)
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.refill import (
    HOUR,
    REFILL_TIMEFRAME_IDS,
    CalendarRef,
    ChunkReconciliation,
    HourKey,
    PlanHour,
    ProviderRef,
    ReconciliationMethod,
    ReconciliationSide,
    ReferenceRole,
    RefillFilter,
    RefillManifestCore,
    RefillPlan,
    SideRecord,
    SideUnavailable,
    TargetBar,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import SnapshotManifest
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = [
    "REFERENCE_SEARCH_HOURS",
    "ChunkLayout",
    "PlanLayout",
    "REFILL_SOURCE_ROOT",
    "REFILL_CALENDAR_VERSIONS",
    "RawBarIndex",
    "build_plan",
    "create_plan",
    "derive_target_bars",
    "finalized_refills",
    "hour_chunks",
    "load_raw_bars",
    "plan_layout",
    "reference_hour_for",
    "require_consistent_refills",
    "require_plan_matches",
]

#: 補充分の置き場（リポジトリからの相対。D03 §14.11）。snapshot の `sources` の `path` も
#: この下を指す。
REFILL_SOURCE_ROOT: Final = "data/raw/market/refill"

#: 計画が受けるカレンダーの版（版 2 または版 3。D03 §14.4・§3.4.2）。
REFILL_CALENDAR_VERSIONS: Final = frozenset({2, 3})

#: 照合用の時間を探す範囲（塊の始まりから遡る時間数。直前 24 時間以内。D03 §14.18 の 2）。
REFERENCE_SEARCH_HOURS: Final = 24

#: 15分足が 1 時間にそろう本数（照合用の時間の条件。D03 §14.4）。
_QUARTERS_PER_HOUR: Final = 4


# --- 原データ（D03 §14.4「読むもの」）----------------------------------------------


def load_raw_bars(
    manifest: SnapshotManifest,
    source: RawBarSource,
    mapping: ColumnMapping,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    calendar: TradingCalendar,
    symbols: Iterable[Symbol],
) -> dict[SeriesId, tuple[Bar, ...]]:
    """manifest の `sources` が指す原データのうち、指定した銘柄のものを読んで足にする。

    読む前に（同じ読込のバイト列で）sha256 と行数が manifest の `sources` と一致することを
    確かめ、一致しなければ何も書かずに失敗する（D03 §14.4。PR #55 の再現スクリプトと同じ
    検査）。同じ系列のファイルが複数ある（補充分を含む snapshot）ときは時刻順に合わせる。

    **原データの時刻ラベルの補正**（D03 §14.4 の v1.19）: manifest の `conversion` に補正規則が
    記録されていれば、受入れと同じ規則を**原ファイル**（補充分の置き場の外のファイル）の行にだけ
    当てた足を返す（対象足の導出・照合用の足・照合用の時間・前後の足のすべてがこの足を使う）。
    補充した足のファイル（`data/raw/market/refill/…`）の行には当てない。原ファイルの行で数えた
    系列ごとの当てた本数が manifest の記録と一致しなければ、何も書かずに失敗する
    （`TimeLabelCorrectionFailed`、構造エラー）。
    """
    record_of_correction = manifest.conversion.time_label_correction
    rule = None if record_of_correction is None else record_of_correction.rule
    refill_prefix = f"{REFILL_SOURCE_ROOT}/"
    wanted = set(symbols)
    bars_by_series: dict[SeriesId, list[Bar]] = {}
    shifted_counts: dict[SeriesId, int] = {}
    for record in manifest.sources:
        if record.symbol not in wanted:
            continue
        content = source.read_file(record.path)
        if content.sha256 != record.sha256 or len(content.rows) != record.rows:
            raise MarketDataValueError(
                f"{record.path}: sha256 {content.sha256} / {len(content.rows)} rows do not"
                f" match the snapshot's source record ({record.sha256} / {record.rows} rows);"
                " the raw data changed after the snapshot was accepted, nothing was written"
                " (D03 §14.4)"
            )
        definition = timeframe_defs.get(record.timeframe.id)
        if definition is None or definition.ref != record.timeframe:
            raise MarketDataValueError(
                f"{record.path}: the timeframe definition {record.timeframe} recorded in the"
                " snapshot is not among the given timeframe definitions (D03 §3.2)"
            )
        raw_file = RawFile(
            path=record.path,
            sha256=record.sha256,
            symbol=record.symbol,
            timeframe=record.timeframe,
            declared_basis=record.declared_basis,
        )
        original = not record.path.startswith(refill_prefix)
        bars, corrected = normalize_rows_with_correction(
            raw_file,
            content.rows,
            mapping,
            definition,
            calendar,
            correction=rule if original else None,
        )
        if original and rule is not None and str(raw_file.series) in rule.series:
            shifted_counts[raw_file.series] = shifted_counts.get(raw_file.series, 0) + len(
                corrected
            )
        bars_by_series.setdefault(raw_file.series, []).extend(bars)
    if record_of_correction is not None:
        differing = [
            f"{series}: {count} (recorded {record_of_correction.count_of(series)})"
            for series, count in sorted(shifted_counts.items(), key=lambda pair: str(pair[0]))
            if record_of_correction.count_of(series) != count
        ]
        if differing:
            raise TimeLabelCorrectionFailed(
                "the time-label correction recorded in the snapshot's manifest shifts a different"
                f" number of raw bars than reading the raw files again: {differing}; nothing was"
                " written (D03 §14.4 v1.19)"
            )
    return {
        series: tuple(sorted(bars, key=lambda bar: bar.bar_start.value))
        for series, bars in bars_by_series.items()
    }


@dataclass(frozen=True, slots=True)
class RawBarIndex:
    """原データの足の引き当て（D03 §14.4・§14.7）。

    照合・前後の比較・照合用の時間に使えるのは、足の終端が研究履歴区分に入る足だけである
    （D03 §14.4 の v1.15 の第1巡の指摘への対処）。`research` はその足だけを系列ごとに
    開始時刻の順で持つ。`starts` は区分によらず原データにある足の開始時刻（価格を持たない。
    重複の検査と「研究履歴区分の外に足がある」の判定に使う）。検索用の鍵の列は構築時に
    1 回だけ作る。写像はすべて読み取り専用（値は tuple）で、構築後に書き換えられない。
    """

    research: Mapping[SeriesId, tuple[Bar, ...]]
    starts: Mapping[SeriesId, tuple[UtcTime, ...]]
    _research_starts: Mapping[SeriesId, tuple[datetime, ...]]
    _research_ends: Mapping[SeriesId, tuple[datetime, ...]]
    _all_starts: Mapping[SeriesId, tuple[datetime, ...]]

    @classmethod
    def build(
        cls, bars_by_series: Mapping[SeriesId, Sequence[Bar]], boundaries: AccessBoundaries
    ) -> RawBarIndex:
        """原データの足から引き当てを作る。"""
        research: dict[SeriesId, tuple[Bar, ...]] = {}
        starts: dict[SeriesId, tuple[UtcTime, ...]] = {}
        for series, bars in bars_by_series.items():
            ordered = sorted(bars, key=lambda bar: bar.bar_start.value)
            starts[series] = tuple(bar.bar_start for bar in ordered)
            research[series] = tuple(
                bar
                for bar in ordered
                if boundaries.classify(bar.bar_end) is AccessClass.RESEARCH_HISTORY
            )
        return cls(
            research=MappingProxyType(research),
            starts=MappingProxyType(starts),
            _research_starts=MappingProxyType(
                {
                    series: tuple(bar.bar_start.value for bar in bars)
                    for series, bars in research.items()
                }
            ),
            _research_ends=MappingProxyType(
                {
                    series: tuple(bar.bar_end.value for bar in bars)
                    for series, bars in research.items()
                }
            ),
            _all_starts=MappingProxyType(
                {
                    series: tuple(moment.value for moment in moments)
                    for series, moments in starts.items()
                }
            ),
        )

    def research_bars_in(self, series: SeriesId, interval: Interval) -> tuple[Bar, ...]:
        """区間に開始時刻が入る研究履歴区分の原データの足（開始時刻の順）。"""
        bars = self.research.get(series, ())
        keys = self._research_starts.get(series, ())
        low = bisect_left(keys, interval.start.value)
        high = bisect_left(keys, interval.end.value)
        return tuple(bars[low:high])

    def research_bar(self, series: SeriesId, start: UtcTime) -> Bar | None:
        """研究履歴区分の原データの足（無ければ `None`）。"""
        bars = self.research.get(series, ())
        keys = self._research_starts.get(series, ())
        index = bisect_left(keys, start.value)
        if index < len(keys) and keys[index] == start.value:
            return bars[index]
        return None

    def research_bar_before(self, series: SeriesId, moment: UtcTime) -> Bar | None:
        """終端が `moment` 以前の、研究履歴区分の原データの最後の足（直前の足）。"""
        bars = self.research.get(series, ())
        keys = self._research_ends.get(series, ())
        index = bisect_right(keys, moment.value)
        return bars[index - 1] if index else None

    def research_bar_after(self, series: SeriesId, moment: UtcTime) -> Bar | None:
        """開始が `moment` 以後の、研究履歴区分の原データの最初の足（直後の足）。"""
        bars = self.research.get(series, ())
        keys = self._research_starts.get(series, ())
        index = bisect_left(keys, moment.value)
        return bars[index] if index < len(bars) else None

    def any_bar_after(self, series: SeriesId, moment: UtcTime) -> bool:
        """区分によらず、開始が `moment` 以後の原データの足があるか（価格は見ない）。"""
        keys = self._all_starts.get(series, ())
        return bisect_left(keys, moment.value) < len(keys)

    def has_start(self, series: SeriesId, start: UtcTime) -> bool:
        """区分によらず、その開始時刻の足が原データにあるか（価格は見ない）。"""
        keys = self._all_starts.get(series, ())
        index = bisect_left(keys, start.value)
        return index < len(keys) and keys[index] == start.value


# --- 対象足（D03 §14.4）--------------------------------------------------------------


def _original_series(manifest: SnapshotManifest) -> frozenset[SeriesId]:
    """原系列（manifest の `sources` が与える系列）。"""
    return frozenset(
        SeriesId(symbol=record.symbol, timeframe=record.timeframe, basis=record.declared_basis)
        for record in manifest.sources
    )


def derive_target_bars(
    manifest: SnapshotManifest,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    boundaries: AccessBoundaries,
    refill_filter: RefillFilter,
) -> tuple[TargetBar, ...]:
    """manifest の確定済み分類から対象足を導く（D03 §14.4 の 1〜5）。

    承認前の snapshot は入力にできない（`SnapshotNotApproved`。D03 §3.7.1 の3）。期待区間が
    整列上の区間と一致しない足（休場の境界で切り詰められる足）があれば構造エラーで止める。
    """
    if not manifest.is_approved:
        raise SnapshotNotApproved(
            "a refill plan is built only from an approved snapshot (D03 §14.4, §3.7.1 の 3)"
        )
    originals = _original_series(manifest)
    targets: dict[tuple[str, str], TargetBar] = {}
    for resolved in manifest.resolved_classifications:
        if resolved.kind is not CheckKind.MISSING_EXPECTED_BAR:
            continue
        if resolved.outcome is not ClassificationOutcome.DATA_GAP:
            continue
        series = resolved.series_id
        if series not in originals or series.timeframe.id not in REFILL_TIMEFRAME_IDS:
            continue
        definition = timeframe_defs.get(series.timeframe.id)
        if definition is None or definition.ref != series.timeframe:
            raise MarketDataValueError(
                f"no timeframe definition {series.timeframe} for {series} (D03 §3.2)"
            )
        start = resolved.interval.start
        aligned = definition.boundaries(start)
        if aligned.start != start:
            raise MarketDataValueError(
                f"the classified gap {series} {resolved.interval} does not start on a bar"
                " boundary (D03 §14.4 の 3)"
            )
        expected = definition.expected_interval(calendar, start)
        if expected is None:
            continue  # 指定したカレンダーで休場になる足は対象にしない（D03 §14.4 の 3）
        if expected != aligned:
            raise MarketDataValueError(
                f"the expected interval {expected} of {series} at {start} differs from the"
                f" aligned interval {aligned}; a bar cut short by a closure boundary is outside"
                " the refill rules and is not handled silently (D03 §14.4 の 3)"
            )
        if boundaries.classify(aligned.end) is not AccessClass.RESEARCH_HISTORY:
            continue  # 研究履歴区分の外は計画に載らない（D03 §14.4 の 4、RF-3）
        if not refill_filter.admits(series, aligned):
            continue
        target = TargetBar(series=series, interval=aligned)
        targets[target.sort_key()] = target
    return tuple(targets[key] for key in sorted(targets))


# --- 時間ファイルと照合用の時間（D03 §14.4）------------------------------------------


def hour_chunks(hours: Iterable[UtcTime]) -> tuple[tuple[UtcTime, ...], ...]:
    """時間の列を連続する塊（次の時間が前の時間 + 1 時間）に分ける。"""
    ordered = sorted(set(hours), key=lambda moment: moment.value)
    chunks: list[list[UtcTime]] = []
    for moment in ordered:
        if chunks and chunks[-1][-1] + HOUR == moment:
            chunks[-1].append(moment)
        else:
            chunks.append([moment])
    return tuple(tuple(chunk) for chunk in chunks)


def _series_of(symbol: Symbol, timeframe_id: str, originals: Iterable[SeriesId]) -> SeriesId | None:
    for series in originals:
        if series.symbol == symbol and series.timeframe.id == timeframe_id:
            return series
    return None


def _chunk_has_reconcilable_bars(
    symbol: Symbol,
    chunk: Sequence[UtcTime],
    originals: Iterable[SeriesId],
    raw: RawBarIndex,
) -> bool:
    """塊のどれかの時間ファイルに、照合できる足（研究履歴区分の原データの足）があるか。"""
    for series in originals:
        if series.symbol != symbol or series.timeframe.id not in REFILL_TIMEFRAME_IDS:
            continue
        for hour in chunk:
            if raw.research_bars_in(series, Interval(start=hour, end=hour + HOUR)):
                return True
    return False


def _is_complete_hour(
    symbol: Symbol, hour: UtcTime, originals: Iterable[SeriesId], raw: RawBarIndex
) -> bool:
    """原データに 15分足 4 本と 1時間足 1 本がそろう時間か（研究履歴区分の足に限る）。"""
    quarter = _series_of(symbol, "15m", originals)
    hourly = _series_of(symbol, "1h", originals)
    if quarter is None or hourly is None:
        return False
    if raw.research_bar(hourly, hour) is None:
        return False
    step = HOUR / _QUARTERS_PER_HOUR
    return all(
        raw.research_bar(quarter, hour + step * index) is not None
        for index in range(_QUARTERS_PER_HOUR)
    )


def reference_hour_for(
    symbol: Symbol,
    chunk: Sequence[UtcTime],
    originals: Iterable[SeriesId],
    raw: RawBarIndex,
) -> UtcTime | None:
    """塊の照合用の時間（D03 §14.4、§14.18 の 2）。要らない・見つからなければ `None`。

    塊のどの時間にも照合できる足があれば要らない。無ければ、塊の始まりの 1〜24 時間前の
    うち、原データに 15分足 4 本と 1時間足 1 本がそろう最も近い時間を返す。
    """
    series = tuple(originals)
    if _chunk_has_reconcilable_bars(symbol, chunk, series, raw):
        return None
    first = chunk[0]
    for back in range(1, REFERENCE_SEARCH_HOURS + 1):
        candidate = first - HOUR * back
        if _is_complete_hour(symbol, candidate, series, raw):
            return candidate
    return None


# --- 照合の配置（D03 §14.4・§14.4.1・§14.7.1）---------------------------------------


@dataclass(frozen=True, slots=True)
class ChunkLayout:
    """塊 1 つ（同じ銘柄の対象の時間の連続する塊）の照合の配置。

    `method` が `None` なのは v1.22 より前の規則で、塊に照合できる足も照合用の時間も無い塊
    （「未照合（照合できる足が無い）」）だけ。`preceding` は直前 24 時間以内の照合用の時間、
    `sides` は両側照合の前側・後側の記録（計画の時点。結果を持たない）。
    """

    symbol: Symbol
    hours: tuple[UtcTime, ...]
    method: ReconciliationMethod | None
    preceding: UtcTime | None = None
    sides: tuple[SideRecord, ...] = ()

    @property
    def keys(self) -> tuple[HourKey, ...]:
        """塊の時間ファイルの鍵。"""
        return tuple(HourKey(symbol=self.symbol, start=moment) for moment in self.hours)

    @property
    def span(self) -> Interval:
        """塊の区間（最初の時間の始まりから最後の時間の終わりまで）。"""
        return Interval(start=self.hours[0], end=self.hours[-1] + HOUR)


@dataclass(frozen=True, slots=True)
class PlanLayout:
    """計画全体の照合の配置（時間ファイルの列と塊ごとの照合の記録）。

    `reconciliations` は v1.22 の規則（`bilateral=True`）のときだけ持ち、v1.22 より前の規則では
    `None`（時間ファイルの印の中身も持たない）。
    """

    hours: tuple[PlanHour, ...]
    reconciliations: tuple[ChunkReconciliation, ...] | None
    chunks: tuple[ChunkLayout, ...]


def _symbol_series(symbol: Symbol, originals: Iterable[SeriesId]) -> tuple[SeriesId, ...]:
    return tuple(
        series
        for series in originals
        if series.symbol == symbol and series.timeframe.id in REFILL_TIMEFRAME_IDS
    )


def _hour_of(moment: UtcTime) -> UtcTime:
    return UtcTime(moment.value.replace(minute=0, second=0, microsecond=0))


def _minutes(start: UtcTime, end: UtcTime) -> int:
    return int((end.value - start.value).total_seconds()) // 60


def _bilateral_side(
    symbol: Symbol,
    chunk: Sequence[UtcTime],
    side: ReconciliationSide,
    originals: Sequence[SeriesId],
    raw: RawBarIndex,
    calendar: TradingCalendar,
) -> SideRecord:
    """両側照合の片側の照合用の時間（D03 §14.4.1 の規則の 2）。

    起点は、前側は欠落の直前の原データの足（研究履歴区分）を含む時間、後側は欠落の直後の原データ
    の足を含む時間。起点の時間に 15分足 4 本と 1時間足 1 本がそろわなければ、起点の時間が属する
    取引日の中で、前側は起点以前のそろう最後の 1 時間まで遡り、後側は起点以後のそろう最初の
    1 時間まで進む（R-1d。PR #78 の仮置き 1）。取引日の中に無ければ取得不能（同 2）。
    """
    series = _symbol_series(symbol, originals)
    chunk_start = chunk[0]
    chunk_end = chunk[-1] + HOUR

    def unavailable(cause: SideUnavailable) -> SideRecord:
        return SideRecord(side=side, reference_start=None, distance_minutes=None, unavailable=cause)

    if side is ReconciliationSide.BEFORE:
        found = [raw.research_bar_before(item, chunk_start) for item in series]
        bars = [bar for bar in found if bar is not None]
        if not bars:
            return unavailable(SideUnavailable.NO_COMPLETE_HOUR)
        origin = _hour_of(max(bars, key=lambda bar: bar.bar_end.value).bar_start)
    else:
        found = [raw.research_bar_after(item, chunk_end) for item in series]
        bars = [bar for bar in found if bar is not None]
        if not bars:
            if any(raw.any_bar_after(item, chunk_end) for item in series):
                return unavailable(SideUnavailable.OUTSIDE_RESEARCH_HISTORY)
            return unavailable(SideUnavailable.NO_COMPLETE_HOUR)
        origin = _hour_of(min(bars, key=lambda bar: bar.bar_start.value).bar_start)
    day = calendar.trading_day_of(origin)
    step = -HOUR if side is ReconciliationSide.BEFORE else HOUR
    candidate = origin
    while day.start <= candidate and candidate + HOUR <= day.end:
        if _is_complete_hour(symbol, candidate, series, raw):
            distance = (
                _minutes(candidate + HOUR, chunk_start)
                if side is ReconciliationSide.BEFORE
                else _minutes(chunk_end, candidate)
            )
            return SideRecord(side=side, reference_start=candidate, distance_minutes=distance)
        candidate = candidate + step
    return unavailable(SideUnavailable.NO_COMPLETE_HOUR)


def chunk_layout(
    symbol: Symbol,
    chunk: Sequence[UtcTime],
    originals: Sequence[SeriesId],
    raw: RawBarIndex,
    calendar: TradingCalendar,
    *,
    bilateral: bool,
) -> ChunkLayout:
    """塊 1 つの照合の配置（D03 §14.4・§14.4.1）。

    塊のどれかの時間ファイルに照合できる足があれば「塊の時間ファイルの中」、無ければ直前
    24 時間以内の照合用の時間（「直前 24 時間以内」）、それも無ければ v1.22 の規則
    （`bilateral=True`）では両側照合、v1.22 より前の規則では方法なし（未照合）。
    """
    hours = tuple(chunk)
    if _chunk_has_reconcilable_bars(symbol, hours, originals, raw):
        return ChunkLayout(symbol=symbol, hours=hours, method=ReconciliationMethod.IN_CHUNK)
    preceding = reference_hour_for(symbol, hours, originals, raw)
    if preceding is not None:
        return ChunkLayout(
            symbol=symbol,
            hours=hours,
            method=ReconciliationMethod.PRECEDING_24H,
            preceding=preceding,
        )
    if not bilateral:
        return ChunkLayout(symbol=symbol, hours=hours, method=None)
    return ChunkLayout(
        symbol=symbol,
        hours=hours,
        method=ReconciliationMethod.BILATERAL,
        sides=tuple(
            _bilateral_side(symbol, hours, side, originals, raw, calendar)
            for side in (ReconciliationSide.BEFORE, ReconciliationSide.AFTER)
        ),
    )


def _chunk_records(
    layout: ChunkLayout, targets_by_hour: Mapping[HourKey, Sequence[TargetBar]]
) -> list[ChunkReconciliation]:
    """塊の、系列ごとの照合の記録（塊の開始時刻はその系列の最初の対象足の開始時刻）。"""
    method = layout.method
    if method is None:
        return []
    firsts: dict[SeriesId, UtcTime] = {}
    for key in layout.keys:
        for target in targets_by_hour.get(key, ()):
            current = firsts.get(target.series)
            if current is None or target.start < current:
                firsts[target.series] = target.start
    return [
        ChunkReconciliation(series=series, chunk_start=start, method=method, sides=layout.sides)
        for series, start in firsts.items()
    ]


def plan_layout(
    targets: Sequence[TargetBar],
    originals: Sequence[SeriesId],
    raw: RawBarIndex,
    calendar: TradingCalendar,
    *,
    bilateral: bool,
) -> PlanLayout:
    """対象足から、時間ファイルの列と塊ごとの照合の記録を決める（D03 §14.4・§14.10・§14.7.1）。

    計画の作成（`bilateral=True`）と、書き出しの検証が計画を計算し直して確かめるとき（計画の
    形式の版に合わせる）の両方が使う。`bilateral=False` は v1.22 より前の計画の規則で、
    印の中身と塊ごとの照合の記録を持たない。
    """
    targets_by_hour: dict[HourKey, list[TargetBar]] = {}
    for target in targets:
        targets_by_hour.setdefault(target.hour, []).append(target)
    hours_by_symbol: dict[Symbol, set[UtcTime]] = {}
    for key in targets_by_hour:
        hours_by_symbol.setdefault(key.symbol, set()).add(key.start)
    chunks: list[ChunkLayout] = []
    records: list[ChunkReconciliation] = []
    roles: dict[HourKey, list[ReferenceRole]] = {}
    for symbol in sorted(hours_by_symbol, key=str):
        for chunk in hour_chunks(hours_by_symbol[symbol]):
            layout = chunk_layout(symbol, chunk, originals, raw, calendar, bilateral=bilateral)
            chunks.append(layout)
            chunk_records = _chunk_records(layout, targets_by_hour)
            records.extend(chunk_records)
            if layout.preceding is not None:
                key = HourKey(symbol=symbol, start=layout.preceding)
                roles.setdefault(key, []).extend(
                    ReferenceRole(
                        series=record.series,
                        chunk_start=record.chunk_start,
                        method=ReconciliationMethod.PRECEDING_24H,
                        side=None,
                        distance_minutes=None,
                    )
                    for record in chunk_records
                )
            for side in layout.sides:
                if side.reference_start is None:
                    continue
                key = HourKey(symbol=symbol, start=side.reference_start)
                roles.setdefault(key, []).extend(
                    ReferenceRole(
                        series=record.series,
                        chunk_start=record.chunk_start,
                        method=ReconciliationMethod.BILATERAL,
                        side=side.side,
                        distance_minutes=side.distance_minutes,
                    )
                    for record in chunk_records
                )
    plan_hours: dict[HourKey, PlanHour] = {}
    for key in targets_by_hour:
        plan_hours[key] = PlanHour(hour=key, reference=False, roles=() if bilateral else None)
    for key, listed in roles.items():
        if key in plan_hours:  # pragma: no cover - そろう時間には対象足が無い
            if not bilateral:
                continue  # v1.22 より前の規則のまま（対象の時間の印を優先する）
            raise MarketDataValueError(f"the reference hour {key} holds a target bar (D03 §14.4)")
        ordered_roles = tuple(sorted(listed, key=lambda role: role.sort_key()))
        plan_hours[key] = PlanHour(
            hour=key, reference=True, roles=ordered_roles if bilateral else None
        )
    return PlanLayout(
        hours=tuple(plan_hours[key] for key in sorted(plan_hours, key=lambda key: key.sort_key())),
        reconciliations=tuple(sorted(records, key=lambda record: record.sort_key()))
        if bilateral
        else None,
        chunks=tuple(chunks),
    )


def build_plan(
    *,
    manifest: SnapshotManifest,
    raw_bars: Mapping[SeriesId, Sequence[Bar]],
    calendar: TradingCalendar,
    calendar_ref: CalendarRef,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    boundaries: AccessBoundaries,
    provider: ProviderRef,
    refill_filter: RefillFilter,
) -> RefillPlan:
    """取得計画を組み立てる（D03 §14.4・§14.10）。対象足が 0 本なら `RefillPlanEmpty`。"""
    if calendar.version not in REFILL_CALENDAR_VERSIONS:
        raise MarketDataValueError(
            f"the calendar {calendar.id} v{calendar.version} is not a refill calendar; a plan"
            f" takes version {' or '.join(str(v) for v in sorted(REFILL_CALENDAR_VERSIONS))}"
            " (D03 §14.4)"
        )
    if (calendar.id, calendar.version) != (calendar_ref.id, calendar_ref.version):
        raise MarketDataValueError(
            f"the calendar {calendar.id} v{calendar.version} does not match its record"
            f" {calendar_ref.id} v{calendar_ref.version}"
        )
    targets = derive_target_bars(manifest, calendar, timeframe_defs, boundaries, refill_filter)
    if not targets:
        raise RefillPlanEmpty(
            "no bar classified as a data gap matches the plan (research history only, the"
            " given calendar and filter); no plan was written (D03 §14.4)"
        )
    layout = plan_layout(
        targets,
        tuple(_original_series(manifest)),
        RawBarIndex.build(raw_bars, boundaries),
        calendar,
        bilateral=True,
    )
    return RefillPlan(
        snapshot_id=str(manifest.snapshot_id()),
        calendar=calendar_ref,
        provider=provider,
        filter=refill_filter,
        target_bars=targets,
        hours=layout.hours,
        reconciliations=layout.reconciliations,
    )


# --- 作業ディレクトリ（D03 §14.11.1・§14.12 の出来事1）-------------------------------


def require_consistent_refills(store: RefillStore) -> None:
    """書きかけの補充分（完成の印が無いもの）が残っていれば止める（D03 §14.11.1 の W6）。"""
    broken = [entry for entry in store.list_refills() if entry.plan_id is None]
    if broken:
        listed = ", ".join(f"{entry.name} ({entry.problem})" for entry in broken)
        raise RefillStoreInconsistent(
            f"incomplete refill directories remain under the refill root: {listed}."
            " Nothing was written; delete them after checking, then retry (D03 §14.11.1 W6)"
        )


def finalized_refills(
    store: RefillStore, plan_id: str, plan: RefillPlan | None
) -> tuple[tuple[str, RefillManifestCore], ...]:
    """この計画の完成した補充分（D03 §14.12「書き出し済み」の判定）を検算して返す。

    `refill_manifest.json` の `plan_id` がこの計画のものを集め、判定と取り直しに使う記録の
    整合を確かめる（D03 §14.11.1 の W5。`plan_id` の鍵だけでは判定しない。PR #58 の仮置き
    の 14 への人間の決定 2026-10-01）:

    - manifest の形（`RefillManifestCore`）が読めること。
    - 計画（`plan`。作業ディレクトリがあるとき）の時間ファイルの列と manifest の時間の列が
      一致すること。
    - 同じ計画の補充分が複数あれば、時間ごとの区分と tick の内容のダイジェストがすべて同じで
      あること（どれも同じ保管場所の tick から書き出すので、違えば食い違い）。

    合わなければ `RefillStoreInconsistent`（W6）。返すのは `(ディレクトリ名, manifest)` の列。
    """
    found: list[tuple[str, RefillManifestCore]] = []
    for entry in store.list_refills():
        if entry.plan_id != plan_id:
            continue
        try:
            core = RefillManifestCore.from_payload(entry.manifest)
        except MarketDataValueError as exc:
            raise RefillStoreInconsistent(
                f"{entry.name}/refill_manifest.json names the plan {plan_id} but cannot be read:"
                f" {exc}. Nothing was written; check the refill directory (D03 §14.11.1 W5・W6)"
            ) from exc
        if core.plan_id != plan_id:  # pragma: no cover - adapters が読んだ値と同じ
            raise RefillStoreInconsistent(f"{entry.name}/refill_manifest.json plan_id differs")
        if plan is not None and [item.hour for item in core.hours] != sorted(
            plan.hour_keys, key=lambda key: key.sort_key()
        ):
            raise RefillStoreInconsistent(
                f"{entry.name}/refill_manifest.json lists hour files that differ from the plan"
                f" {plan_id}. Nothing was written (D03 §14.11.1 W5・W6)"
            )
        if found and core.hours != found[0][1].hours:
            raise RefillStoreInconsistent(
                f"the refill directories {found[0][0]} and {entry.name} of the plan {plan_id}"
                " record different hour results or tick digests. Nothing was written"
                " (D03 §14.11.1 W5・W6)"
            )
        found.append((entry.name, core))
    return tuple(found)


def require_plan_matches(plan_id: str, payload: Mapping[str, object] | None) -> RefillPlan:
    """`plan.json` から `plan_id` を計算し直し、ディレクトリ名と一致することを確かめる（W5）。

    `plan.json` が無い（書きかけの作業ディレクトリ）・形が読めない・一致しないときは
    `RefillStoreInconsistent`（W6）。
    """
    if payload is None:
        raise RefillStoreInconsistent(
            f"_work/{plan_id}/ has no plan.json (an incomplete work directory). Nothing was"
            " written; delete the directory after checking, then plan again (D03 §14.11.1 W4・W6)"
        )
    try:
        plan = RefillPlan.from_payload(payload)
    except MarketDataValueError as exc:
        raise RefillStoreInconsistent(
            f"_work/{plan_id}/plan.json cannot be read as a refill plan: {exc}"
            " (D03 §14.11.1 W5・W6)"
        ) from exc
    recomputed = plan.plan_id()
    if recomputed != plan_id:
        raise RefillStoreInconsistent(
            f"_work/{plan_id}/plan.json describes the plan {recomputed}, not the directory name"
            " (D03 §14.11.1 W5・W6)"
        )
    return plan


def create_plan(plan: RefillPlan, store: RefillStore) -> str:
    """計画を作業ディレクトリに書き、`plan_id` を返す（D03 §14.12 の出来事1）。

    - 書きかけの補充分が残っていれば止める（W6）。
    - この計画の補充分の manifest を検算する（合わなければ `RefillStoreInconsistent`）。
    - 同じ `plan_id` の作業ディレクトリがあれば、書きかけ（`plan.json` が無い・合わない）なら
      `RefillStoreInconsistent`、完成していれば `RefillPlanAlreadyExists`（存在すれば失敗。
      書き出し済みでも同じ）。
    - 作業ディレクトリが無く、この計画の補充分があれば `RefillAlreadyFinalized`。
    - 作業ディレクトリを排他的に作ってから `plan.json` を置く（W2・W4）。
    """
    plan_id = plan.plan_id()
    require_consistent_refills(store)
    finished = finalized_refills(store, plan_id, plan)
    if store.work_dir_exists(plan_id):
        # 書き出し済みでも作業ディレクトリが残っていれば RefillPlanAlreadyExists（D03 §14.12 の
        # 書き出し済み×出来事1）。manifest の検算は上で済ませている。
        require_plan_matches(plan_id, store.read_plan(plan_id))
        raise RefillPlanAlreadyExists(
            f"the plan {plan_id} already exists under _work/; resume it with"
            " `odyssey-fx data refill fetch`. Nothing was written (D03 §14.12)"
        )
    if finished:
        raise RefillAlreadyFinalized(
            f"the plan {plan_id} is already finalized as {', '.join(n for n, _ in finished)};"
            " nothing was written (D03 §14.12)"
        )
    store.create_work_dir(plan_id)
    store.write_plan(plan_id, plan.identity_payload())
    return plan_id
