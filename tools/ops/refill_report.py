"""補充の後の「残存欠落と実行可能な連続期間」の報告を作る（D03 §14.14 の段 10・11、§14.15）。

報告の置き場は ``docs/data/research_history_gaps.md``（RF-9 の決定。新しい snapshot の内容に
書き直し、旧 snapshot の数字は比較の表と旧 snapshot の CSV に残す）。本スクリプトは、その書き
直しの材料になる報告の本文（Markdown）と、残存欠落の区間ごとの CSV を作る。文書そのものは人が
書き直す（本スクリプトは文書を上書きしない）。

報告の規則は D03 v1.17 §14.15（2026-10-01 の人間の決定）:

- **入力の限定**: 正式な報告の入力は、分類と確定を済ませ承認された新 snapshot に限る。承認されて
  いない snapshot からの出力は、表題とファイル名に「下書き」と明示する。
- **残存欠落 1 件ごとに分けて示す**: (a) 現在の状態（理由の語彙）、(b) 取得・検証を試みた履歴
  （関係する計画と結果を全件。上書きしない）、(c) 休場の候補の印（理由とは別の列）、(d) 根拠と
  なる計画の識別子。
- **前後関係は参照関係で決める**: 記録 X が記録 Y より前とみなすのは、X の補充分が Y の入力
  snapshot に（重ねた補充を辿って）含まれるときだけ。補充分の数や識別子の並びで 1 つを選ばない。
  比較できない記録が残れば併記する。
- **自動収集**: 補充の置き場から、入力 snapshot を問わず計画と補充分をすべて集め、足ごとに
  突き合わせる（2026-10-01 の人間の決定「R4 は置き場の全計画」）。置き場の中に読めないものが
  あって網羅性を確かめられなければ、どの記録も無い足を「対象だが計画・試行されていない」と
  せず「理由未確定」とする。
- **件数の表示**: 理由ごとに件数の列を分け、複数の理由を含む区間を 1 つの理由として数えない。

**入力の検算は本体の関数だけで行う**（2026-10-01 の人間の決定。報告の側で読み方を書き直さ
ない）: snapshot の manifest は ``snapshot_store(...).read_manifest``（内容から識別子を計算し
直す）、補充分・計画・取得記録は ``marketdata.application.refill_inventory``（書き出しと受入れが
使う ``verify_refill_directory``・``require_plan_matches``・``read_journal``・
``finalized_refills``・``derive_state`` を呼ぶ）。本スクリプトに残すのは、検算済みの型からの
集計と表示だけ。

読むもの（戦略の成績は読まない。D03 §14.2 の 7）: snapshot の ``manifest.json``、補充分の
``refill_manifest.json`` と ``validation.json``、作業ディレクトリの ``plan.json`` と
``journal.jsonl``。

出力（報告と CSV）は「存在すれば失敗」: 出力先にどちらかが既にあれば、どちらも書かずに止める。

実行はリポジトリの根で ``uv run python -m tools.ops.refill_report …``（``tools`` パッケージとして
import するため、ファイルのパスを直接渡す形では動かない）。
"""

from __future__ import annotations

import argparse
import csv
import shlex
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from odyssey_fx.app.composition import refill_store, snapshot_manifest_payload, snapshot_store
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.ports import RefillStore
from odyssey_fx.marketdata.application.refill_fetch import PlanState
from odyssey_fx.marketdata.application.refill_finalize import refill_ids_in_sources
from odyssey_fx.marketdata.application.refill_inventory import (
    RefillInventory,
    Rejection,
    VerifiedRefill,
    load_verified_refill,
    survey_refill_store,
)
from odyssey_fx.marketdata.domain.errors import MarketDataError
from odyssey_fx.marketdata.domain.refill import RefillPlan
from odyssey_fx.marketdata.domain.refill_validation import NotBuiltBar, NotBuiltReason
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import SnapshotManifest
from tools.ops import research_history_gaps as rhg

# --- 語彙 ----------------------------------------------------------------------------

#: 残存欠落の足の「現在の状態」（D03 §14.15 の 3。CSV の値）。日本語の意味は ``STATE_LABELS``。
NOT_FETCHED = "NOT_FETCHED"
PROVIDER_NO_TICKS = "PROVIDER_NO_TICKS"
UNRECONCILED = "UNRECONCILED"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
VALIDATION_REJECTED = "VALIDATION_REJECTED"
NOT_PLANNED = "NOT_PLANNED"
UNDETERMINED = "UNDETERMINED"

STATE_LABELS: dict[str, str] = {
    NOT_FETCHED: "取得できなかった（HTTP 404 を含む）",
    PROVIDER_NO_TICKS: "提供元にも tick が無い",
    UNRECONCILED: "未照合（判断待ち）",
    OUT_OF_SCOPE: "補充の対象外（研究履歴区分の外）",
    VALIDATION_REJECTED: "検証で不合格になり補充しなかった",
    NOT_PLANNED: "対象だが計画・試行されていない",
    UNDETERMINED: "理由未確定",
}
STATE_ORDER: tuple[str, ...] = tuple(STATE_LABELS)

#: 記録の結果のうち、状態の語彙に無いもの（履歴にだけ出る）。状態としては「理由未確定」。
BUILT = "BUILT"  # 補充分が足を作り、その補充分が新 snapshot に入っている（なお欠落なら食い違い）
BUILT_NOT_IN_SNAPSHOT = "BUILT_NOT_IN_SNAPSHOT"  # 足を作ったが、その補充分は新 snapshot に無い
IN_PROGRESS = "IN_PROGRESS"  # 計画はあるが、書き出しも不合格もまだ無い

RESULT_LABELS: dict[str, str] = {
    **STATE_LABELS,
    BUILT: "補充した（新 snapshot に入っている）",
    BUILT_NOT_IN_SNAPSHOT: "補充した（その補充分は新 snapshot に無い）",
    IN_PROGRESS: "計画・取得の途中（書き出しも不合格もまだ無い）",
}
RESULT_TO_STATE: dict[str, str] = {
    NOT_FETCHED: NOT_FETCHED,
    PROVIDER_NO_TICKS: PROVIDER_NO_TICKS,
    UNRECONCILED: UNRECONCILED,
    VALIDATION_REJECTED: VALIDATION_REJECTED,
    BUILT: UNDETERMINED,
    BUILT_NOT_IN_SNAPSHOT: UNDETERMINED,
    IN_PROGRESS: UNDETERMINED,
}

#: 作らなかった理由（本体の語彙。D03 §14.8 の表）から結果への対応。
NOT_BUILT_TO_RESULT: dict[NotBuiltReason, str] = {
    NotBuiltReason.HOUR_NOT_FETCHED: NOT_FETCHED,
    NotBuiltReason.PROVIDER_EMPTY: PROVIDER_NO_TICKS,
    NotBuiltReason.NO_TICK_IN_BAR: PROVIDER_NO_TICKS,
    NotBuiltReason.UNRECONCILED: UNRECONCILED,
}

#: 休場の候補（保留。D03 §3.4.2 の候補 1・2・9。RF-11・RF-12・RF-19）。
HOLIDAY_CANDIDATES: tuple[str, ...] = ("1", "2", "9")

RESIDUAL_FIELDS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "start_utc",
    "end_utc",
    "duration_hours",
    "year_of_bar_end",
    "bar_count",
    "states",
    *(f"bars_{state}" for state in STATE_ORDER),
    "bars_with_unordered_records",
    "holiday_candidate_pending",
    "holiday_candidates",
    "basis_plan_ids",
    "history",
)

#: 時間足の足の長さ（残存欠落の区間を足 1 本ずつにほどくため）。
BAR_LENGTH: dict[str, timedelta] = {"15m@v1": timedelta(minutes=15), "1h@v1": timedelta(hours=1)}

BarKey = tuple[str, str, datetime]


class ReportInputError(RuntimeError):
    """報告の入力（snapshot・補充分）が読めない・食い違う。"""


# --- 読み込み（本体の検算を呼ぶだけ）----------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """本体の読込で識別子を計算し直して確かめた snapshot の manifest と、その集計用の形。"""

    snapshot_id: str
    manifest: SnapshotManifest
    payload: dict[str, Any]

    @property
    def refill_ids(self) -> tuple[str, ...]:
        """``sources`` が指す補充分の識別子（本体の ``refill_ids_in_sources``）。"""
        return refill_ids_in_sources([source.path for source in self.manifest.sources])


def load_snapshot(snapshot_root: Path, snapshot_id: str) -> Snapshot:
    """確定済みの snapshot の manifest を本体の読込で読む（D03 §3.7.1、§14.15 の R1）。

    ``snapshot_store(...).read_manifest`` が形・承認の記録の構造を確かめ、内容から識別子を計算
    し直して記録と照らす。その識別子がディレクトリ名と一致することを確かめてから使う（改変
    された manifest から報告を作らない）。暫定 snapshot（``_pending/``）は入力にできない。
    """
    try:
        manifest = snapshot_store(snapshot_root).read_manifest(snapshot_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReportInputError(
            f"snapshot の manifest を確かめられない: {snapshot_root / snapshot_id} ({exc})"
        ) from exc
    if str(manifest.snapshot_id()) != snapshot_id:
        raise ReportInputError(
            f"snapshot_id がディレクトリ名と一致しない: {snapshot_root / snapshot_id}"
        )
    return Snapshot(snapshot_id, manifest, dict(snapshot_manifest_payload(manifest)))


def load_refill(store: RefillStore, refill_id: str) -> VerifiedRefill:
    """補充分を本体の W5 の検算（``load_verified_refill``）を済ませて読む。"""
    try:
        return load_verified_refill(store, refill_id)
    except MarketDataError as exc:
        raise ReportInputError(f"補充分の検算が合わない: {refill_id} ({exc})") from exc


def snapshot_chain(
    snapshot_root: Path, store: RefillStore, snapshot_id: str
) -> dict[str, frozenset[str]]:
    """対象 snapshot とその前の snapshot ごとに、含む補充分を重ねた補充まで辿った集合。

    snapshot S の集合は、S の ``sources`` が指す補充分と、その補充分の入力 snapshot の集合の
    和。前後関係（D03 §14.15 の R3）の判定に使う。読めない snapshot・補充分があれば止める
    （前後関係を確かめられないため）。
    """
    closures: dict[str, frozenset[str]] = {}
    _close(snapshot_root, store, snapshot_id, closures)
    return closures


def extend_chain(
    snapshot_root: Path,
    store: RefillStore,
    chain: dict[str, frozenset[str]],
    snapshot_ids: set[str],
) -> list[str]:
    """集めた記録の入力 snapshot についても、補充分の参照を辿った集合を ``chain`` に足す。

    R4 は置き場の全計画を集めるので、新 snapshot の祖先でない snapshot を入力にした記録も
    ある。その前後関係（R3）を確かめるために辿る。読めなければ止めずに、網羅性を確かめられ
    ない理由として返す（その記録の前後を確かめられないため）。
    """
    problems: list[str] = []
    for snapshot_id in sorted(snapshot_ids - set(chain)):
        try:
            _close(snapshot_root, store, snapshot_id, chain)
        except ReportInputError as exc:
            problems.append(f"記録の入力 snapshot の補充分の参照を辿れない: {snapshot_id}（{exc}）")
    return problems


def _close(
    snapshot_root: Path,
    store: RefillStore,
    snapshot_id: str,
    closures: dict[str, frozenset[str]],
) -> None:
    """``snapshot_id`` から補充分の参照を辿り、``closures`` に足す（読めなければ止める）。"""
    visiting: set[str] = set()
    found_here: dict[str, frozenset[str]] = {}

    def closure(current: str) -> frozenset[str]:
        if current in closures:
            return closures[current]
        if current in found_here:
            return found_here[current]
        if current in closures:
            return closures[current]
        if current in visiting:
            raise ReportInputError(f"snapshot と補充分の参照が循環している: {current}")
        visiting.add(current)
        found: set[str] = set()
        for refill_id in load_snapshot(snapshot_root, current).refill_ids:
            found.add(refill_id)
            found |= closure(load_refill(store, refill_id).manifest.snapshot_id)
        visiting.discard(current)
        found_here[current] = frozenset(found)
        return found_here[current]

    closure(snapshot_id)
    closures.update(found_here)


# --- 記録（検算済みの型から作る）--------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """取得・検証を試みた記録 1 件（補充分 1 つ、取得記録の不合格の行 1 つ、または途中の計画）。

    ``is_state`` は、その計画の現在の状態を表す記録か（D03 §14.12: 補充分があれば補充分、
    無ければ最後の検証が不合格のままならその行、どちらでもなければ途中の計画）。状態を表さない
    記録（後で取り直した計画の古い不合格の行など）は履歴にだけ出る。
    """

    plan_id: str
    input_snapshot: str
    source: str
    refill_id: str | None
    recorded_at: str
    is_state: bool
    results: dict[BarKey, str]
    rejection: Rejection | None = None


@dataclass
class Collection:
    """補充の置き場から集めた記録と、網羅性を確かめられなかった理由。"""

    records: list[Record]
    problems: list[str]
    plan_count: int = 0
    refill_count: int = 0

    @property
    def complete(self) -> bool:
        return not self.problems


def _bar_key(series: SeriesId, start: UtcTime) -> BarKey:
    return str(series.symbol), str(series.timeframe), start.value


def _targets(plan: RefillPlan) -> list[BarKey]:
    return [_bar_key(bar.series, bar.start) for bar in plan.target_bars]


def _not_built(items: tuple[NotBuiltBar, ...]) -> dict[BarKey, str]:
    return {_bar_key(item.series, item.start): NOT_BUILT_TO_RESULT[item.reason] for item in items}


def records_from(inventory: RefillInventory, in_snapshot: frozenset[str]) -> Collection:
    """本体が集めて検算した補充分と計画（``survey_refill_store``）から記録を作る。"""
    records: list[Record] = []
    plans_with_refill: set[str] = set()
    for refill in inventory.refills:
        manifest = refill.manifest
        built = BUILT if manifest.refill_id in in_snapshot else BUILT_NOT_IN_SNAPSHOT
        not_built = _not_built(manifest.not_built)
        records.append(
            Record(
                plan_id=manifest.plan_id,
                input_snapshot=manifest.snapshot_id,
                source=f"refill:{manifest.refill_id}",
                refill_id=manifest.refill_id,
                recorded_at=str(manifest.created_at),
                is_state=True,
                results={bar: not_built.get(bar, built) for bar in _targets(manifest.plan)},
            )
        )
        plans_with_refill.add(manifest.plan_id)
    for plan in inventory.plans:
        targets = _targets(plan.plan)
        rejected_now = plan.state is PlanState.REJECTED and plan.plan_id not in plans_with_refill
        for rejection in plan.rejections:
            not_built = _not_built(rejection.not_built)
            records.append(
                Record(
                    plan_id=plan.plan_id,
                    input_snapshot=plan.plan.snapshot_id,
                    source=f"journal:{rejection.line}",
                    refill_id=None,
                    recorded_at=str(rejection.record.at),
                    is_state=rejected_now and rejection is plan.rejections[-1],
                    results={bar: not_built.get(bar, VALIDATION_REJECTED) for bar in targets},
                    rejection=rejection,
                )
            )
        if plan.plan_id not in plans_with_refill and not rejected_now:
            records.append(
                Record(
                    plan_id=plan.plan_id,
                    input_snapshot=plan.plan.snapshot_id,
                    source="plan",
                    refill_id=None,
                    recorded_at="" if plan.last_at is None else str(plan.last_at),
                    is_state=True,
                    results=dict.fromkeys(targets, IN_PROGRESS),
                )
            )
    return Collection(
        records,
        list(inventory.problems),
        plan_count=len(inventory.plans),
        refill_count=len(inventory.refills),
    )


def collect_records(
    snapshot_root: Path,
    store: RefillStore,
    chain: dict[str, frozenset[str]],
    in_snapshot: frozenset[str],
) -> Collection:
    """置き場にある計画・補充分の記録を、入力 snapshot を問わずすべて集める（R4）。

    集めて検算するのは本体（``survey_refill_store``）。記録の入力 snapshot の参照も辿って
    ``chain`` に足す（R3 の前後関係のため。足ごとの突き合わせは ``bar_states``）。置き場その
    ものや記録の入力 snapshot が読めなければ、網羅性を確かめられないとする。
    """
    try:
        inventory = survey_refill_store(store)
    except (MarketDataError, OSError) as exc:
        return Collection([], [f"補充の置き場が読めない: {exc}"])
    inputs = {item.manifest.snapshot_id for item in inventory.refills} | {
        item.plan.snapshot_id for item in inventory.plans
    }
    problems = extend_chain(snapshot_root, store, chain, inputs)
    collection = records_from(inventory, in_snapshot)
    collection.problems.extend(problems)
    return collection


# --- 足ごとの状態 ----------------------------------------------------------------------


@dataclass(frozen=True)
class BarState:
    """残存欠落の足 1 本の現在の状態（D03 §14.15 の (a)・(d)）。"""

    states: tuple[str, ...]
    basis: tuple[str, ...]
    unordered: bool


def precedes(earlier: Record, later: Record, chain: dict[str, frozenset[str]]) -> bool:
    """``earlier`` が ``later`` より前と確かめられるか（参照関係だけで決める）。

    ``earlier`` の補充分が ``later`` の入力 snapshot に（重ねた補充を辿って）含まれるときだけ
    前とする。補充分の無い記録（不合格・途中）は、どの記録の入力にも入らないので前と言えない。
    """
    if earlier.refill_id is None:
        return False
    return earlier.refill_id in chain.get(later.input_snapshot, frozenset())


def bar_states(
    bars: list[BarKey],
    collection: Collection,
    chain: dict[str, frozenset[str]],
) -> dict[BarKey, BarState]:
    """残存欠落の足ごとの現在の状態。

    その足を対象にした「状態を表す記録」のうち、参照関係で後の記録が無いもの（最も後の記録）の
    結果を状態にする。最も後の記録が 2 つ以上あれば併記する（1 つを選ばない）。どの記録も無い足
    は、網羅性を確かめられたときだけ「対象だが計画・試行されていない」、確かめられなければ
    「理由未確定」。
    """
    covering: dict[BarKey, list[Record]] = defaultdict(list)
    wanted = set(bars)
    for record in collection.records:
        if not record.is_state:
            continue
        for bar in record.results:
            if bar in wanted:
                covering[bar].append(record)
    result: dict[BarKey, BarState] = {}
    for bar in bars:
        found = covering.get(bar, [])
        if not found:
            state = NOT_PLANNED if collection.complete else UNDETERMINED
            result[bar] = BarState((state,), (), False)
            continue
        latest = [
            record
            for record in found
            if not any(precedes(record, other, chain) for other in found if other is not record)
        ]
        states = {RESULT_TO_STATE[record.results[bar]] for record in latest}
        result[bar] = BarState(
            states=tuple(state for state in STATE_ORDER if state in states),
            basis=tuple(sorted({record.plan_id for record in latest})),
            unordered=len(latest) > 1,
        )
    return result


def _bars_of(gap: rhg.GapInterval) -> list[BarKey]:
    step = BAR_LENGTH[gap.timeframe]
    bars: list[BarKey] = []
    moment = gap.start
    while moment < gap.end:
        bars.append((gap.symbol, gap.timeframe, moment))
        moment += step
    return bars


# --- 休場の候補の印 --------------------------------------------------------------------


def candidate_number(gap: rhg.GapInterval) -> str | None:
    """PR #55 の欠落区間が保留の休場の候補（D03 §3.4.2 の候補 1・2・9）のどれに当たるか。"""
    attribution, _, _ = rhg.classify(gap)
    if attribution == rhg.CALENDAR_UNDECLARED_OR_PARTLY_SOURCE:
        return "1"
    if attribution == rhg.CALENDAR:
        return "2"
    start_ny = gap.start.astimezone(rhg.NEW_YORK)
    if start_ny.weekday() == 4 and start_ny.hour == 16 and gap.hours <= 1.5:
        return "9"
    return None


def candidate_intervals(
    merged: dict[tuple[str, str], list[rhg.Interval]],
) -> dict[tuple[str, str], list[tuple[datetime, datetime, str]]]:
    """候補区間: 候補を定めた snapshot（PR #55）の欠落区間のうち、候補に当たるもの（系列ごと）。"""
    found: dict[tuple[str, str], list[tuple[datetime, datetime, str]]] = defaultdict(list)
    for (symbol, tf), gaps in merged.items():
        for start, end in gaps:
            number = candidate_number(rhg.GapInterval(symbol, tf, start, end))
            if number is not None:
                found[(symbol, tf)].append((start, end, number))
    return found


def overlapping_candidates(
    gap: rhg.GapInterval,
    candidates: dict[tuple[str, str], list[tuple[datetime, datetime, str]]],
) -> list[str]:
    """残存欠落と重なる候補区間の番号（同じ系列。部分補充で欠落が短くなっても重なれば付く）。"""
    numbers = {
        number
        for start, end, number in candidates.get((gap.symbol, gap.timeframe), [])
        if gap.start < end and start < gap.end
    }
    return [number for number in HOLIDAY_CANDIDATES if number in numbers]


# --- 残存欠落の行 ----------------------------------------------------------------------


def _history(bars: list[BarKey], collection: Collection) -> list[tuple[str, str, str, str, int]]:
    """区間の足を対象にした記録をすべて（状態を表さない記録も）。記録した時刻の順。"""
    counts: Counter[tuple[str, str, str, str]] = Counter()
    wanted = set(bars)
    for record in collection.records:
        for bar, result in record.results.items():
            if bar in wanted:
                counts[(record.recorded_at, record.plan_id, record.source, result)] += 1
    return [(*key, count) for key, count in sorted(counts.items())]


def residual_rows(
    merged: dict[tuple[str, str], list[rhg.Interval]],
    collection: Collection,
    chain: dict[str, frozenset[str]],
    candidates: dict[tuple[str, str], list[tuple[datetime, datetime, str]]],
) -> list[dict[str, Any]]:
    """新しい snapshot の残存欠落の区間ごとの行（CSV）。"""
    gaps = [
        rhg.GapInterval(symbol, tf, start, end)
        for symbol in rhg.SYMBOLS
        for tf in rhg.TIMEFRAMES
        for start, end in merged[(symbol, tf)]
    ]
    states = bar_states([bar for gap in gaps for bar in _bars_of(gap)], collection, chain)
    rows: list[dict[str, Any]] = []
    for gap in gaps:
        bars = _bars_of(gap)
        per_state: Counter[str] = Counter()
        basis: set[str] = set()
        unordered = 0
        for bar in bars:
            state = states[bar]
            per_state.update(state.states)
            basis.update(state.basis)
            unordered += state.unordered
        numbers = overlapping_candidates(gap, candidates)
        rows.append(
            {
                "symbol": gap.symbol,
                "timeframe": gap.timeframe,
                "start_utc": rhg.fmt_utc(gap.start),
                "end_utc": rhg.fmt_utc(gap.end),
                "duration_hours": round(gap.hours, 2),
                "year_of_bar_end": gap.end.year,
                "bar_count": len(bars),
                "states": "|".join(state for state in STATE_ORDER if per_state[state]),
                **{f"bars_{state}": per_state[state] for state in STATE_ORDER},
                "bars_with_unordered_records": unordered,
                "holiday_candidate_pending": bool(numbers),
                "holiday_candidates": "|".join(numbers),
                "basis_plan_ids": "|".join(sorted(basis)),
                "history": " ; ".join(
                    f"{at or '時刻なし'} plan={plan} {source} {result}×{count}"
                    for at, plan, source, result, count in _history(bars, collection)
                ),
            }
        )
    return rows


# --- 報告の本文 ------------------------------------------------------------------------


def _windows(
    merged: dict[tuple[str, str], list[rhg.Interval]],
    bounds: dict[tuple[str, str], rhg.Interval],
) -> tuple[list[rhg.Interval], list[rhg.Interval]]:
    """20 系列すべてと USDJPY 15 分足の、欠落の無い連続区間（長い順）。"""
    if not bounds:
        return [], []
    lo_all = max(lo for lo, _ in bounds.values())
    hi_all = min(hi for _, hi in bounds.values())
    union = rhg.merge_adjacent([g for gaps in merged.values() for g in gaps])
    all_series = sorted(
        rhg.contiguous_windows(union, lo_all, hi_all), key=lambda w: w[1] - w[0], reverse=True
    )
    key = ("USDJPY", "15m@v1")
    usdjpy: list[rhg.Interval] = []
    if key in bounds:
        lo, hi = bounds[key]
        usdjpy = sorted(
            rhg.contiguous_windows(merged[key], lo, hi), key=lambda w: w[1] - w[0], reverse=True
        )
    return all_series, usdjpy


def _days(window: rhg.Interval) -> str:
    return f"{(window[1] - window[0]).total_seconds() / 86400:.2f}"


def _hours(gaps: list[rhg.Interval]) -> float:
    return sum((e - s).total_seconds() / 3600 for s, e in gaps)


def render_report(
    *,
    old_id: str,
    new: Snapshot,
    candidates_id: str,
    old_merged: dict[tuple[str, str], list[rhg.Interval]],
    old_bounds: dict[tuple[str, str], rhg.Interval],
    new_merged: dict[tuple[str, str], list[rhg.Interval]],
    new_bounds: dict[tuple[str, str], rhg.Interval],
    refills: list[VerifiedRefill],
    collection: Collection,
    rows: list[dict[str, Any]],
    command: str,
) -> str:
    """報告の本文（Markdown。D03 §14.15 の 1〜5）。価格は書かない（前後の差は pip だけ）。"""
    manifest = new.manifest
    approval = manifest.approval
    refill_ids = ", ".join(f"`{item.manifest.refill_id}`" for item in refills) or "なし"
    rejected = [r for r in collection.records if r.is_state and r.rejection is not None]
    rejected_ids = ", ".join(f"`{r.plan_id}`" for r in rejected) or "なし"
    title = "# 補充の後の残存欠落と実行可能な連続期間（報告の材料）"
    lines: list[str] = [f"# 【下書き】{title[2:]}" if approval is None else title, ""]
    if approval is None:
        lines += [
            "**下書き**: 新 snapshot は承認されていない。正式な残存欠落の報告の入力は、分類と確定を"
            "済ませ承認された新 snapshot に限る（D03 §14.15）。承認の後に作り直すこと。",
            "",
        ]
    lines += ["## 1. 入力と出力の識別", ""]
    lines += [
        "| 項目 | 値 |",
        "|---|---|",
        f"| 旧 snapshot | `{old_id}` |",
        f"| 新 snapshot | `{new.snapshot_id}` |",
        "| 新 snapshot の承認 | "
        + (
            f"承認済み（{approval.approved_by}、{approval.approved_at}）"
            if approval is not None
            else "未承認（この出力は下書き）"
        )
        + " |",
        f"| カレンダー | `{manifest.conversion.calendar_id}` 版"
        f" {manifest.conversion.calendar_version} |",
        f"| 補充の識別子（新 snapshot に入っているもの） | {refill_ids} |",
        f"| 不合格のままの計画（自動収集） | {rejected_ids} |",
        f"| 休場の候補区間を定めた snapshot | `{candidates_id}` |",
        "",
    ]
    lines.append(
        f"補充の置き場の自動収集: 計画 {collection.plan_count} 件・補充分"
        f" {collection.refill_count} 件（入力 snapshot を問わず置き場のすべて）。"
        + (
            "置き場の中はすべて読めた（網羅性を確かめた）。"
            if collection.complete
            else "置き場に読めないものがあり網羅性を確かめられないので、どの記録も無い足は"
            "「理由未確定」とした:"
        )
    )
    lines += [f"- {problem}" for problem in collection.problems]
    lines += [
        "",
        "人間が消した作業ディレクトリ（`_work/<plan_id>/`）の計画は置き場から分からないので、"
        "この収集に入らない。",
        "",
    ]

    lines += ["## 2. 補充の結果", ""]
    lines += [
        "補充した足の出来高は 0 で、「出来高不明」を意味する"
        "（実際の出来高として使わない。D03 §14.6）。",
        "",
        "補充分ごとの行（補充を重ねたとき、前の補充分の「作らなかった」には後の補充分が"
        "補充した足も含まれる。行を足し合わせない）。",
        "",
        "| 補充分 | 系列 | 対象足 | 補充した | 作らなかった | 理由別（作らなかった足） |",
        "|---|---|---|---|---|---|",
    ]
    for refill in refills:
        by_reason: dict[SeriesId, Counter[str]] = defaultdict(Counter)
        for item in refill.manifest.not_built:
            by_reason[item.series][item.reason.value] += 1
        for count in refill.manifest.series_counts:
            detail = ", ".join(
                f"{reason} {number}" for reason, number in sorted(by_reason[count.series].items())
            )
            lines.append(
                f"| `{refill.manifest.refill_id[:12]}…` | {count.series} | {count.targets} |"
                f" {count.built} |"
                f" {count.not_built} | {detail or '—'} |"
            )
    lines.append("")
    unreconciled = [item for refill in refills for item in refill.manifest.unreconciled] + [
        item for rej in rejected if rej.rejection is not None for item in rej.rejection.unreconciled
    ]
    lines.append(f"未照合の塊（補充分に書かず、人間の判断を待つ）: {len(unreconciled)}")
    lines += [
        f"- {item.series} {item.chunk_start}（対象足 {item.target_count} 本）"
        for item in unreconciled
    ]
    lines.append("")
    for refill in refills:
        validation = refill.validation
        review = [item for item in validation.neighbors if item.needs_review]
        short = refill.manifest.refill_id[:12]
        lines.append(
            f"検証（`{short}…`）: 照合 {validation.reconciled_count} 本・"
            f"一致 {validation.matched_count} 本、"
            f"範囲外の tick {validation.out_of_range_tick_count} 件、"
            f"bid が ask より大きい tick {validation.bid_above_ask_tick_count} 件"
            f"（合否に使わない）、「要確認」の印 {len(review)} 件"
        )
        lines += [
            f"- 要確認: {item.series} {item.chunk.start} {item.side.value}"
            f" 差 {item.difference_pips} pip"
            for item in review
        ]
    for rej in rejected:
        assert rej.rejection is not None
        lines.append(
            f"不合格の計画 `{rej.plan_id[:12]}…`: " + "; ".join(rej.rejection.record.reasons)
        )
    lines.append("")

    lines += ["## 3. 残存欠落", ""]
    lines += [
        "区間ごとの (a) 現在の状態・(b) 試行の履歴・(c) 休場の候補の印・(d) 根拠の計画は"
        " CSV の列 `states`（と状態ごとの足の数 `bars_<状態>`）・`history`・"
        "`holiday_candidate_pending`／`holiday_candidates`・`basis_plan_ids`。",
        "",
    ]
    lines += ["### 3.1 系列別（旧 → 新。件数 / 合計時間）", ""]
    lines += ["| 系列 | 旧 | 新 |", "|---|---|---|"]
    for symbol in rhg.SYMBOLS:
        for tf in rhg.TIMEFRAMES:
            old = old_merged.get((symbol, tf), [])
            new_gaps = new_merged.get((symbol, tf), [])
            lines.append(
                f"| {symbol} {tf} | {len(old)} / {_hours(old):.2f}h |"
                f" {len(new_gaps)} / {_hours(new_gaps):.2f}h |"
            )
    total_old = [g for gaps in old_merged.values() for g in gaps]
    total_new = [g for gaps in new_merged.values() for g in gaps]
    lines += [
        f"| **合計** | **{len(total_old)} / {_hours(total_old):.2f}h** |"
        f" **{len(total_new)} / {_hours(total_new):.2f}h** |",
        "",
    ]
    lines += ["### 3.2 年別（新。足の終端の年）", "", "| 年 | 件数 / 合計 |", "|---|---|"]
    by_year: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        by_year[row["year_of_bar_end"]].append(row["duration_hours"])
    for year in sorted(by_year):
        lines.append(f"| {year} | {len(by_year[year])} / {sum(by_year[year]):.2f}h |")
    lines.append("")
    lines += [
        "### 3.3 状態別（新）",
        "",
        "状態ごとに列を分けて数える。1 つの区間に複数の状態の足があれば、その区間は各行に数える"
        "（区間の数の列の合計は区間の総数を超えうる）。比較できない記録を併記した足は、併記した"
        "状態のそれぞれに数える。",
        "",
        "| 状態 | その状態の足を含む区間 | 足の数 | 足の時間の合計 |",
        "|---|---|---|---|",
    ]
    for state in STATE_ORDER:
        column = f"bars_{state}"
        with_state = [row for row in rows if row[column]]
        bars = sum(row[column] for row in rows)
        hours = sum(
            row[column] * BAR_LENGTH[row["timeframe"]].total_seconds() / 3600 for row in rows
        )
        lines.append(
            f"| {STATE_LABELS[state]}（`{state}`） | {len(with_state)} | {bars} | {hours:.2f}h |"
        )
    mixed = [row for row in rows if "|" in row["states"]]
    unordered = sum(row["bars_with_unordered_records"] for row in rows)
    pending = [row for row in rows if row["holiday_candidate_pending"]]
    lines += [
        "",
        f"- 複数の状態の足を含む区間: {len(mixed)} / 区間の総数 {len(rows)}",
        f"- 比較できない記録を併記した足: {unordered} 本（CSV の `bars_with_unordered_records`）",
        f"- 休場の候補（保留）の区間と重なる残存欠落: {len(pending)} 区間 / "
        f"{sum(row['duration_hours'] for row in pending):.2f}h（D03 §3.4.2 の候補 1・2・9。"
        "状態とは別の印）",
        "",
    ]

    lines += ["## 4. 実行可能な連続期間", ""]
    old_all, old_usdjpy = _windows(old_merged, old_bounds)
    new_all, new_usdjpy = _windows(new_merged, new_bounds)
    for heading, windows in (
        ("20 系列すべてに欠落の無い連続区間の上位 5（新）", new_all),
        ("USDJPY 15 分足だけの上位 5（新）", new_usdjpy),
    ):
        lines += [
            f"### {heading}",
            "",
            "| 順位 | 始端（UTC） | 終端（UTC） | 日数 |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {rank} | {rhg.fmt_utc(s)} | {rhg.fmt_utc(e)} | {_days((s, e))} |"
            for rank, (s, e) in enumerate(windows[:5], start=1)
        ]
        lines.append("")
    lines += [
        "### 旧 snapshot との比較",
        "",
        "| 項目 | 旧 | 新 |",
        "|---|---|---|",
        f"| 20 系列の連続区間の数 | {len(old_all)} | {len(new_all)} |",
        f"| 20 系列の最長（日） | {_days(old_all[0]) if old_all else '—'} |"
        f" {_days(new_all[0]) if new_all else '—'} |",
        f"| USDJPY 15 分足の連続区間の数 | {len(old_usdjpy)} | {len(new_usdjpy)} |",
        f"| USDJPY 15 分足の最長（日） | {_days(old_usdjpy[0]) if old_usdjpy else '—'} |"
        f" {_days(new_usdjpy[0]) if new_usdjpy else '—'} |",
        "",
    ]

    lines += ["## 5. 再現", "", "```", command, "```", ""]
    lines.append(
        "戦略の成績（指標・レポート・`runs/`）は読んでいない。snapshot の manifest・補充分の"
        " manifest と検証記録・計画と取得記録だけを読んだ（D03 §14.2 の 7）。"
    )
    return "\n".join(lines) + "\n"


def write_outputs(
    csv_path: Path, rows: list[dict[str, Any]], report_path: Path, report: str
) -> None:
    """CSV と報告を排他的に作成する（既にあれば失敗し、上書きしない）。"""
    with csv_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(RESIDUAL_FIELDS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with report_path.open("x", encoding="utf-8") as handle:
        handle.write(report)


def reproduction_command(args: argparse.Namespace) -> str:
    """再現のコマンド（引数はシェル向けに引用する。空白や記号を含むパスでもそのまま動く）。"""
    return shlex.join(
        [
            "uv",
            "run",
            "python",
            "-m",
            "tools.ops.refill_report",
            "--snapshot-root",
            str(args.snapshot_root),
            "--snapshot-id",
            args.snapshot_id,
            "--previous-snapshot-id",
            args.previous_snapshot_id,
            "--candidates-snapshot-id",
            args.candidates_snapshot_id,
            "--refill-root",
            str(args.refill_root),
            "--out",
            str(args.out),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--snapshot-root", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--snapshot-id", required=True, help="新しい snapshot の識別子")
    parser.add_argument(
        "--previous-snapshot-id",
        default=rhg.DEFAULT_SNAPSHOT_ID,
        help="比較する旧 snapshot の識別子（既定は補充の前の承認済み snapshot）",
    )
    parser.add_argument(
        "--candidates-snapshot-id",
        default=rhg.DEFAULT_SNAPSHOT_ID,
        help="休場の候補区間を定めた snapshot（既定は PR #55 の欠落一覧の snapshot）",
    )
    parser.add_argument("--refill-root", type=Path, default=Path("data/raw/market/refill"))
    parser.add_argument("--out", type=Path, required=True, help="報告と CSV の出力先ディレクトリ")
    args = parser.parse_args(argv)

    store = refill_store(args.refill_root)
    try:
        new = load_snapshot(args.snapshot_root, args.snapshot_id)
        old = load_snapshot(args.snapshot_root, args.previous_snapshot_id)
        candidates_snapshot = load_snapshot(args.snapshot_root, args.candidates_snapshot_id)
        refills = [load_refill(store, refill_id) for refill_id in new.refill_ids]
        chain = snapshot_chain(args.snapshot_root, store, args.snapshot_id)
    except ReportInputError as exc:
        print(f"報告の入力が読めない: {exc}", file=sys.stderr)
        return 1
    collection = collect_records(args.snapshot_root, store, chain, frozenset(new.refill_ids))
    new_merged, new_bounds = rhg.collect_gaps(new.payload)
    old_merged, old_bounds = rhg.collect_gaps(old.payload)
    candidates = candidate_intervals(rhg.collect_gaps(candidates_snapshot.payload)[0])
    rows = residual_rows(new_merged, collection, chain, candidates)
    report = render_report(
        old_id=args.previous_snapshot_id,
        new=new,
        candidates_id=args.candidates_snapshot_id,
        old_merged=old_merged,
        old_bounds=old_bounds,
        new_merged=new_merged,
        new_bounds=new_bounds,
        refills=refills,
        collection=collection,
        rows=rows,
        command=reproduction_command(args),
    )
    suffix = "" if new.manifest.is_approved else "_draft"
    csv_path = args.out / f"residual_gaps{suffix}.csv"
    report_path = args.out / f"refill_report{suffix}.md"
    existing = [path for path in (csv_path, report_path) if path.exists() or path.is_symlink()]
    if existing:
        print(
            "出力先に報告が既にある（上書きしない。どちらも書かなかった）: "
            + ", ".join(str(path) for path in existing),
            file=sys.stderr,
        )
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    write_outputs(csv_path, rows, report_path, report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
