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
足す。無ければ足さず、その塊は「未照合」になる（書き出しの検証が扱う）。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.acceptance import ColumnMapping, RawFile, normalize_rows
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
)
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.refill import (
    HOUR,
    REFILL_TIMEFRAME_IDS,
    CalendarRef,
    HourKey,
    PlanHour,
    ProviderRef,
    RefillFilter,
    RefillPlan,
    TargetBar,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import SnapshotManifest
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = [
    "REFERENCE_SEARCH_HOURS",
    "RawBarIndex",
    "build_plan",
    "create_plan",
    "derive_target_bars",
    "finalized_refills",
    "hour_chunks",
    "load_raw_bars",
    "reference_hour_for",
    "require_consistent_refills",
    "require_plan_matches",
]

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
    """
    wanted = set(symbols)
    bars_by_series: dict[SeriesId, list[Bar]] = {}
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
        bars = normalize_rows(raw_file, content.rows, mapping, definition, calendar)
        bars_by_series.setdefault(raw_file.series, []).extend(bars)
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
    1 回だけ作る。
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
            research=research,
            starts=starts,
            _research_starts={
                series: tuple(bar.bar_start.value for bar in bars)
                for series, bars in research.items()
            },
            _research_ends={
                series: tuple(bar.bar_end.value for bar in bars)
                for series, bars in research.items()
            },
            _all_starts={
                series: tuple(moment.value for moment in moments)
                for series, moments in starts.items()
            },
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
    originals = _original_series(manifest)
    raw = RawBarIndex.build(raw_bars, boundaries)
    hours_by_symbol: dict[Symbol, set[UtcTime]] = {}
    for target in targets:
        hours_by_symbol.setdefault(target.series.symbol, set()).add(target.hour.start)
    plan_hours: dict[HourKey, PlanHour] = {}
    for symbol, hours in hours_by_symbol.items():
        for moment in hours:
            key = HourKey(symbol=symbol, start=moment)
            plan_hours[key] = PlanHour(hour=key, reference=False)
        for chunk in hour_chunks(hours):
            reference = reference_hour_for(symbol, chunk, originals, raw)
            if reference is not None:
                key = HourKey(symbol=symbol, start=reference)
                plan_hours.setdefault(key, PlanHour(hour=key, reference=True))
    ordered = tuple(plan_hours[key] for key in sorted(plan_hours, key=lambda key: key.sort_key()))
    return RefillPlan(
        snapshot_id=str(manifest.snapshot_id()),
        calendar=calendar_ref,
        provider=provider,
        filter=refill_filter,
        target_bars=targets,
        hours=ordered,
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


def finalized_refills(store: RefillStore, plan_id: str) -> tuple[str, ...]:
    """この計画の完成した補充分のディレクトリ名（D03 §14.12「書き出し済み」の判定）。"""
    return tuple(entry.name for entry in store.list_refills() if entry.plan_id == plan_id)


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
    - この計画の補充分が既にあれば `RefillAlreadyFinalized`（計画を作る前に補充分を探す）。
    - 同じ `plan_id` の作業ディレクトリがあれば、書きかけ（`plan.json` が無い・合わない）なら
      `RefillStoreInconsistent`、完成していれば `RefillPlanAlreadyExists`（存在すれば失敗）。
    - 作業ディレクトリを排他的に作ってから `plan.json` を置く（W2・W4）。
    """
    plan_id = plan.plan_id()
    require_consistent_refills(store)
    finished = finalized_refills(store, plan_id)
    if finished:
        raise RefillAlreadyFinalized(
            f"the plan {plan_id} is already finalized as {', '.join(finished)};"
            " nothing was written (D03 §14.12)"
        )
    if store.work_dir_exists(plan_id):
        require_plan_matches(plan_id, store.read_plan(plan_id))
        raise RefillPlanAlreadyExists(
            f"the plan {plan_id} already exists under _work/; resume it with"
            " `odyssey-fx data refill fetch`. Nothing was written (D03 §14.12)"
        )
    store.create_work_dir(plan_id)
    store.write_plan(plan_id, plan.identity_payload())
    return plan_id
