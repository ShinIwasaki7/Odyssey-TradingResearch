"""補充の置き場を読み取り専用で調べる（D03 v1.17 §14.15 の R4、§14.11.1 の W3・W5）。

報告（`tools/ops/refill_report.py`）が残存欠落の状態と履歴を決めるために、補充の置き場から
補充分・計画・取得記録を集める。検算は書き出し（`finalize`）と受入れ（`accept --refill`）と
**同じ本体の関数**で行う（報告の側で読み方を書き直さない）:

- 補充分: `verify_refill_directory`（完成の印・`plan_id`・`refill_id` の計算し直し・manifest の
  形と不変条件・ファイルの集合と sha256・行数）と、`validation.json` を実際に読んだバイト列の
  sha256 の照合と形の検算（`RefillValidationRecord`）。
- 計画: `require_plan_matches`（`plan.json` の形と `plan_id` の計算し直し）。
- 取得記録: `read_journal`（行ごとのダイジェストと行の形 `journal_entry_from_payload`、計画に
  無い時間を指す行）。状態は `derive_state`、書き出し済みの判定は `finalized_refills`。
- 不合格の行の構造的な記録（`details`）は manifest と同じ読み方（`not_built_from_payload`・
  `unreconciled_from_payload`）で読み、計画の対象足を 1 回ずつ指すことを確かめる。

読めないもの・検算が合わないもの・置き場の想定外のもの（`RefillStore.list_unexpected`）は
`problems` に残す（それが対象の計画かどうか分からないので、網羅性を確かめられない）。何も
書かない。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.ports import RefillStore
from odyssey_fx.marketdata.application.refill_fetch import PlanState, derive_state, read_journal
from odyssey_fx.marketdata.application.refill_finalize import verify_refill_directory
from odyssey_fx.marketdata.application.refill_plan import (
    finalized_refills,
    require_plan_matches,
)
from odyssey_fx.marketdata.domain.errors import (
    MarketDataError,
    MarketDataValueError,
    RefillStoreInconsistent,
)
from odyssey_fx.marketdata.domain.refill import RefillPlan, ValidationRecord
from odyssey_fx.marketdata.domain.refill_manifest import (
    REFILL_VALIDATION_FILE,
    RefillManifest,
    RefillValidationRecord,
    not_built_from_payload,
    unreconciled_from_payload,
)
from odyssey_fx.marketdata.domain.refill_validation import NotBuiltBar, UnreconciledChunk
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "PlanRecord",
    "Rejection",
    "RefillInventory",
    "VerifiedRefill",
    "load_verified_refill",
    "rejection_of",
    "survey_refill_store",
]


# --- 補充分 ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifiedRefill:
    """W5 の検算を済ませた補充分 1 つ（manifest と検証記録）。"""

    manifest: RefillManifest
    validation: RefillValidationRecord


def load_verified_refill(store: RefillStore, refill_id: str) -> VerifiedRefill:
    """補充分を W5 の検算をすべて行ってから読む（D03 §14.11.1 の W5）。

    `verify_refill_directory` の後に `validation.json` を読み、読んだバイト列の sha256 が
    manifest の記録と一致すること、形が書き手の形であること、記録した `refill_id`・`plan_id`
    が manifest と一致することを確かめる。合わなければ `RefillStoreInconsistent`（W6）。
    """
    manifest = verify_refill_directory(
        refill_id, store.read_refill_manifest(refill_id), store.list_refill_files(refill_id)
    )
    sha256, payload = store.read_refill_json(refill_id, REFILL_VALIDATION_FILE)
    recorded = [item for item in manifest.files if item.name == REFILL_VALIDATION_FILE]
    if len(recorded) != 1 or recorded[0].sha256 != sha256:
        raise RefillStoreInconsistent(
            f"{refill_id}/{REFILL_VALIDATION_FILE}: the content read has the sha256 {sha256},"
            " which differs from the manifest (D03 §14.11.1 W5・W6)"
        )
    try:
        validation = RefillValidationRecord.from_payload(payload)
    except MarketDataValueError as exc:
        raise RefillStoreInconsistent(
            f"{refill_id}/{REFILL_VALIDATION_FILE} cannot be read: {exc} (D03 §14.11.1 W5・W6)"
        ) from exc
    if (validation.refill_id, validation.plan_id) != (manifest.refill_id, manifest.plan_id):
        raise RefillStoreInconsistent(
            f"{refill_id}/{REFILL_VALIDATION_FILE} records another refill or plan"
            " (D03 §14.11.1 W5・W6)"
        )
    return VerifiedRefill(manifest=manifest, validation=validation)


# --- 計画と取得記録 --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rejection:
    """取得記録の不合格の行 1 つ（D03 §14.7・§14.12）。`line` は 1 始まりの行番号。"""

    line: int
    record: ValidationRecord
    not_built: tuple[NotBuiltBar, ...]
    unreconciled: tuple[UnreconciledChunk, ...]


_DETAIL_KEYS = frozenset({"mismatches", "not_built", "unreconciled"})


def rejection_of(plan: RefillPlan, line: int, record: ValidationRecord) -> Rejection:
    """不合格の行の構造的な記録を読む（書き手は `refill_finalize.failure_details`）。

    鍵の集合が書き手の形と同じで、作らなかった足と未照合の塊の開始が計画の対象足を 1 回ずつ
    指すこと（manifest と同じ規則。D03 §14.11）。合わなければ `MarketDataValueError`。
    """
    label = f"journal line {line} details"
    details = record.details
    if frozenset(details) != _DETAIL_KEYS:
        raise MarketDataValueError(
            f"{label}: keys {sorted(details)} do not match {sorted(_DETAIL_KEYS)}"
        )
    for key in _DETAIL_KEYS:
        if not isinstance(details[key], Sequence) or isinstance(details[key], str):
            raise MarketDataValueError(f"{label}.{key} must be a list")
    not_built = tuple(
        not_built_from_payload(item, f"{label}.not_built[{index}]")
        for index, item in enumerate(details["not_built"])
    )
    unreconciled = tuple(
        unreconciled_from_payload(item, f"{label}.unreconciled[{index}]")
        for index, item in enumerate(details["unreconciled"])
    )
    targets = {(bar.series, bar.start) for bar in plan.target_bars}
    for name, keys in (
        ("not_built", [(item.series, item.start) for item in not_built]),
        ("unreconciled", [(item.series, item.chunk_start) for item in unreconciled]),
    ):
        seen: set[tuple[SeriesId, UtcTime]] = set()
        for bar_key in keys:
            if bar_key not in targets or bar_key in seen:
                raise MarketDataValueError(
                    f"{label}.{name}: {bar_key[0]} {bar_key[1]} does not name a target bar of"
                    " the plan exactly once (D03 §14.11)"
                )
            seen.add(bar_key)
    return Rejection(line=line, record=record, not_built=not_built, unreconciled=unreconciled)


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """作業ディレクトリのある計画 1 つ（検算済み）。

    `state` は `derive_state` が取得記録と補充分の有無から決めた状態（D03 §14.12）。
    `rejections` は取得記録の不合格の行すべて（後で取り直した古い行も含む）。`last_at` は
    取得記録の最後の行の時刻（行が無ければ `None`）。
    """

    plan_id: str
    plan: RefillPlan
    state: PlanState
    rejections: tuple[Rejection, ...]
    last_at: UtcTime | None


def _plan_record(store: RefillStore, plan_id: str, plan: RefillPlan) -> PlanRecord:
    journal = read_journal(plan_id, store.read_journal(plan_id), plan)
    finished = finalized_refills(store, plan_id, plan)
    state = derive_state(plan, journal.entries, finalized=bool(finished))
    rejections = tuple(
        rejection_of(plan, index, entry)
        for index, entry in enumerate(journal.entries, start=1)
        if isinstance(entry, ValidationRecord) and not entry.passed
    )
    last_at = journal.entries[-1].at if journal.entries else None
    return PlanRecord(
        plan_id=plan_id, plan=plan, state=state, rejections=rejections, last_at=last_at
    )


# --- 置き場の調査 ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefillInventory:
    """置き場から集めた、指定した snapshot を入力にした補充分と計画（D03 §14.15 の R4）。

    `problems` は読めなかったもの・検算が合わなかったもの・想定外のもの。1 つでもあれば
    網羅性を確かめられない（`complete` が偽）。
    """

    refills: tuple[VerifiedRefill, ...]
    plans: tuple[PlanRecord, ...]
    problems: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """置き場の中をすべて読めたか（網羅性を確かめたか）。"""
        return not self.problems


def survey_refill_store(store: RefillStore, snapshot_ids: Collection[str]) -> RefillInventory:
    """`snapshot_ids` のどれかを入力にした補充分と計画をすべて集める（読み取り専用）。

    補充分は `load_verified_refill`、計画は `require_plan_matches`・`read_journal`・
    `finalized_refills`・`derive_state` で検算する。対象外の snapshot を入力にしたものは、
    読めれば数えない。読めなければ（対象かどうか分からないので）`problems` に残す。
    """
    wanted = frozenset(snapshot_ids)
    problems: list[str] = [
        f"補充の置き場に想定外のもの（補充分・_ticks・_work・計画以外の名前、ファイル、リンク）:"
        f" {name}"
        for name in store.list_unexpected()
    ]
    refills: list[VerifiedRefill] = []
    for entry in store.list_refills():
        if entry.plan_id is None:
            problems.append(f"書きかけ・読めない補充分: {entry.name}（{entry.problem}）")
            continue
        try:
            verified = load_verified_refill(store, entry.name)
        except MarketDataError as exc:
            problems.append(f"補充分の検算が合わない: {entry.name}（{exc}）")
            continue
        if verified.manifest.snapshot_id in wanted:
            refills.append(verified)
    plans: list[PlanRecord] = []
    for plan_id in store.list_plans():
        try:
            plan = require_plan_matches(plan_id, store.read_plan(plan_id))
            if plan.snapshot_id not in wanted:
                continue
            plans.append(_plan_record(store, plan_id, plan))
        except MarketDataError as exc:
            problems.append(f"計画・取得記録の検算が合わない: _work/{plan_id}（{exc}）")
    return RefillInventory(refills=tuple(refills), plans=tuple(plans), problems=tuple(problems))
