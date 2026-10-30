"""採用審査の記録の読み書き（D16 §10。審査台帳は版管理、数値の記録は版管理の外）。

**審査台帳** `research/adoption_reviews.jsonl`（D16 §10.2）は試行台帳と同じ規約で読み書きする
（D09 §10.10・§10.12）: 1 行は `{digest, entry, prev}` の正規化エンコード＋改行 1 つ、追記だけ
（書き換え・削除・並べ替えをしない）、行のダイジェストと直前の行のダイジェストで鎖にする、記録時刻を
入れない。ファイルは追記で作らない（空のファイルを版管理で置く。無ければ読込の誤り）。追記は
1 回ごとに作って消すロック `research/adoption_reviews.lock`（版管理しない）の下で行い、ロックの
下で読み直して末尾が変わっていないことを確かめ、末尾の書きかけ（改行で終わらない断片）を
切り詰めてから行全体を 1 回で書いて同期する。

**数値の記録**（D16 §10.4）は `runs/experiments/<名前>/v<版>/review/<審査の乱数>/` に
`review_record.json`（数値）と `review.md`（人が読む形）を「存在すれば失敗」で書く。数値は
人間と分析役だけが読む（R1-2 (a)）。
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Final

from odyssey_fx.common.canonical import encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import CodeDigest, ContentDigest, LockDigest, PolicyRef
from odyssey_fx.evaluation.adapters.fs_store import ensure_experiment_directory, write_new_file
from odyssey_fx.evaluation.domain.adoption_review import (
    EVIDENCE_NOTE,
    AdoptionReviewEntry,
    AdoptionReviewLine,
    Capability,
    CostFitRow,
    CostModelRow,
    DeclarationState,
    DeclarationStatus,
    DsrSummary,
    EvidenceFloorRecord,
    FitOutcome,
    FixedTrial,
    FloorOutcome,
    LedgerTally,
    ReviewDefect,
    ReviewReason,
    ReviewResult,
    ReviewRulesRef,
    last_review_digest,
    review_ledger_defect,
    review_line_digest,
)
from odyssey_fx.evaluation.domain.variant import Paired

__all__ = [
    "ADOPTION_REVIEW_LEDGER_PATH",
    "ADOPTION_REVIEW_LOCK_PATH",
    "REVIEW_DIRECTORY",
    "REVIEW_MARKDOWN_FILE",
    "REVIEW_RECORD_FILE",
    "ReviewAppendOutcome",
    "ReviewAppendRefused",
    "ReviewAppendRefusal",
    "ReviewLedgerContents",
    "ReviewLedgerReadFailure",
    "append_review_ledger_file",
    "read_review_ledger_file",
    "review_line_bytes",
    "write_review_record",
]

#: 審査台帳とロックの置き場（リポジトリの根からの相対パス。D16 §10.1・§10.2）。
ADOPTION_REVIEW_LEDGER_PATH: Final = Path("research") / "adoption_reviews.jsonl"
ADOPTION_REVIEW_LOCK_PATH: Final = Path("research") / "adoption_reviews.lock"
#: 数値の記録の置き場（実験の版のディレクトリの下。D16 §10.1・§10.4）。
REVIEW_DIRECTORY: Final = "review"
REVIEW_RECORD_FILE: Final = "review_record.json"
REVIEW_MARKDOWN_FILE: Final = "review.md"

_READ_ERRORS: Final = (ValueError, KeyError, TypeError, AttributeError, ArithmeticError)


@dataclass(frozen=True, slots=True)
class ReviewLedgerContents:
    """読めた審査台帳（完全な行と、末尾の書きかけの有無）。"""

    lines: tuple[AdoptionReviewLine, ...]
    torn_tail: bool


@dataclass(frozen=True, slots=True)
class ReviewLedgerReadFailure:
    """審査台帳を読めない理由（最初に当たった 1 件）。"""

    kind: ReviewDefect
    line_number: int | None
    detail: str


class ReviewAppendRefusal(Enum):
    """追記を断った理由（試行台帳の `TrialLedgerRefusal` と同じ区分）。"""

    READ_FAILED = "READ_FAILED"
    LOCKED = "LOCKED"
    TAIL_CHANGED = "TAIL_CHANGED"
    WRITE_FAILED = "WRITE_FAILED"


@dataclass(frozen=True, slots=True)
class ReviewAppendRefused:
    """追記を断った（完全な行は残っていない）。"""

    kind: ReviewAppendRefusal
    detail: str


@dataclass(frozen=True, slots=True)
class ReviewAppendOutcome:
    """完全な行が残った追記。`sync_failed` は行を書いた後の同期が失敗したこと（記録あり。
    D16 §11.3 の S4 → S5）。"""

    line: AdoptionReviewLine
    sync_failed: bool


# --- 符号化 ---------------------------------------------------------------------


def review_line_bytes(line: AdoptionReviewLine) -> bytes:
    """審査台帳の 1 行の符号化: `{digest, entry, prev}` の正規化エンコード＋改行 1 つ。"""
    return (
        encode(
            {
                "digest": line.digest.hex,
                "entry": line.entry,
                "prev": None if line.prev is None else line.prev.hex,
            }
        )
        + b"\n"
    )


def _keys(payload: object, names: Sequence[str], label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise KernelValueError(f"{label} must be a JSON object")
    if set(payload) != set(names):
        raise KernelValueError(
            f"{label} must have exactly the keys {sorted(names)}, got {sorted(payload)}"
        )
    return payload


def _int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"{label} must be an int, got {value!r}")
    return value


def _str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise KernelValueError(f"{label} must be a str, got {value!r}")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise KernelValueError(f"{label} must be a JSON array")
    return value


def _digest(payload: object, label: str) -> ContentDigest:
    item = _keys(payload, ("algorithm", "hex"), label)
    return ContentDigest(algorithm=_str(item["algorithm"], label), hex=_str(item["hex"], label))


def _wrapped(payload: object, label: str) -> ContentDigest:
    return _digest(_keys(payload, ("digest",), label)["digest"], label)


def _paired(payload: object, label: str) -> tuple[Any, Any]:
    item = _keys(payload, ("candidate", "standard"), label)
    return item["standard"], item["candidate"]


def _fixed_trial(payload: object) -> FixedTrial | None:
    if payload is None:
        return None
    item = _keys(payload, ("assignment_digest", "trial_index"), "fixed_trials")
    return FixedTrial(
        trial_index=_int(item["trial_index"], "trial_index"),
        assignment_digest=_digest(item["assignment_digest"], "assignment_digest"),
    )


def _fit_rows(payload: object) -> tuple[CostFitRow, ...]:
    rows: list[CostFitRow] = []
    for raw in _list(payload, "cost_model_fit"):
        item = _keys(raw, ("capabilities", "outcome", "row"), "cost_model_fit[]")
        rows.append(
            CostFitRow(
                row=CostModelRow(_str(item["row"], "row")),
                outcome=FitOutcome(_str(item["outcome"], "outcome")),
                capabilities=tuple(
                    Capability(_str(value, "capabilities"))
                    for value in _list(item["capabilities"], "capabilities")
                ),
            )
        )
    return tuple(rows)


def _strings(payload: object, label: str) -> tuple[str, ...]:
    return tuple(_str(value, label) for value in _list(payload, label))


def _dsr(payload: object) -> DsrSummary:
    item = _keys(payload, ("assumption_reasons", "marks", "reasons", "state"), "deflated_sharpe")
    return DsrSummary(
        state=_str(item["state"], "state"),
        reasons=_strings(item["reasons"], "reasons"),
        marks=_strings(item["marks"], "marks"),
        assumption_reasons=_strings(item["assumption_reasons"], "assumption_reasons"),
    )


def review_entry_of(payload: object) -> AdoptionReviewEntry:
    """審査台帳の行の正規化形から型へ（キーの集合と型を厳密に確かめる）。"""
    names = tuple(AdoptionReviewEntry.__dataclass_fields__)
    item = _keys(payload, names, "the review entry")
    policy = _keys(
        item["research_policy_ref"],
        ("digest", "policy_id", "policy_kind", "version"),
        "research_policy_ref",
    )
    rules = _keys(item["review_rules_ref"], ("digest", "rules_id", "version"), "review_rules_ref")
    floor = _keys(item["evidence_floor"], ("note", "outcomes"), "evidence_floor")
    floor_standard, floor_candidate = _paired(floor["outcomes"], "evidence_floor.outcomes")
    fixed_standard, fixed_candidate = _paired(item["fixed_trials"], "fixed_trials")
    fit_standard, fit_candidate = _paired(item["cost_model_fit"], "cost_model_fit")
    dsr_standard, dsr_candidate = _paired(item["deflated_sharpe"], "deflated_sharpe")
    tally = _keys(item["ledger_tally"], tuple(LedgerTally.__dataclass_fields__), "ledger_tally")
    declaration = _keys(item["declaration"], ("file_digest", "status"), "declaration")
    note = _str(floor["note"], "evidence_floor.note")
    if note != EVIDENCE_NOTE:
        raise KernelValueError("evidence_floor.note is not the D15-6 (a) note")
    return AdoptionReviewEntry(
        schema_version=_int(item["schema_version"], "schema_version"),
        experiment_id=ExperimentId(ContentDigest.sha256(_str(item["experiment_id"], "id"))),
        execution=_int(item["execution"], "execution"),
        experiment_name=_str(item["experiment_name"], "experiment_name"),
        experiment_version=_int(item["experiment_version"], "experiment_version"),
        strategy_id=_str(item["strategy_id"], "strategy_id"),
        review_nonce=_str(item["review_nonce"], "review_nonce"),
        started_line_digest=_digest(item["started_line_digest"], "started_line_digest"),
        finished_line_digest=_digest(item["finished_line_digest"], "finished_line_digest"),
        trial_ledger_head=_digest(item["trial_ledger_head"], "trial_ledger_head"),
        research_policy_ref=PolicyRef(
            policy_kind=_str(policy["policy_kind"], "policy_kind"),
            policy_id=_str(policy["policy_id"], "policy_id"),
            version=_int(policy["version"], "version"),
            digest=_digest(policy["digest"], "digest"),
        ),
        review_rules_ref=ReviewRulesRef(
            rules_id=_str(rules["rules_id"], "rules_id"),
            version=_int(rules["version"], "version"),
            digest=_digest(rules["digest"], "digest"),
        ),
        fixed_trials=Paired(
            standard=_fixed_trial(fixed_standard), candidate=_fixed_trial(fixed_candidate)
        ),
        evidence_floor=EvidenceFloorRecord(
            outcomes=Paired(
                standard=FloorOutcome(_str(floor_standard, "evidence_floor")),
                candidate=FloorOutcome(_str(floor_candidate, "evidence_floor")),
            ),
            note=note,
        ),
        cost_model_fit=Paired(standard=_fit_rows(fit_standard), candidate=_fit_rows(fit_candidate)),
        deflated_sharpe=Paired(standard=_dsr(dsr_standard), candidate=_dsr(dsr_candidate)),
        ledger_tally=LedgerTally(**{name: _int(tally[name], name) for name in tally}),
        declaration=DeclarationState(
            status=DeclarationStatus(_str(declaration["status"], "declaration.status")),
            file_digest=None
            if declaration["file_digest"] is None
            else _digest(declaration["file_digest"], "declaration.file_digest"),
        ),
        result=ReviewResult(_str(item["result"], "result")),
        reasons=tuple(
            ReviewReason(_str(value, "reasons")) for value in _list(item["reasons"], "r")
        ),
        numeric_record_digest=_digest(item["numeric_record_digest"], "numeric_record_digest"),
        code_digest=CodeDigest(_wrapped(item["code_digest"], "code_digest")),
        lock_digest=LockDigest(_wrapped(item["lock_digest"], "lock_digest")),
    )


# --- 読込 ---------------------------------------------------------------------------


def _failure(kind: ReviewDefect, number: int | None, detail: str) -> ReviewLedgerReadFailure:
    return ReviewLedgerReadFailure(kind=kind, line_number=number, detail=detail)


def _raw_line(data: bytes) -> tuple[Mapping[str, Any], ContentDigest, ContentDigest | None] | str:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return f"the line is not UTF-8 JSON: {exc}"
    if not isinstance(payload, dict) or set(payload) != {"digest", "entry", "prev"}:
        return "the line is not an object with exactly digest / entry / prev"
    try:
        if encode(payload) != data:
            return "the line is not in the canonical encoding"
        recorded = ContentDigest.sha256(_str(payload["digest"], "digest"))
        prev = payload["prev"]
        previous = None if prev is None else ContentDigest.sha256(_str(prev, "prev"))
    except (*_READ_ERRORS, KernelValueError) as exc:
        return f"the line cannot be checked: {type(exc).__name__}: {exc}"
    return payload, recorded, previous


def read_review_ledger_file(path: Path) -> ReviewLedgerContents | ReviewLedgerReadFailure:
    """審査台帳のファイルを読み、行の形と鎖と審査台帳の規則を確かめる（最初の食い違いを 1 件）。

    最後の改行より後の断片は書きかけとして行に入れず `torn_tail = true` にする（失敗にしない）。
    """
    if not (path.exists() or path.is_symlink()):
        return _failure(
            ReviewDefect.FILE_MISSING,
            None,
            f"{ADOPTION_REVIEW_LEDGER_PATH} does not exist; it is placed (empty) under version"
            " control and is never created by an append. Restore it from the history",
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        return _failure(
            ReviewDefect.FILE_UNREADABLE,
            None,
            f"{ADOPTION_REVIEW_LEDGER_PATH} cannot be read: {type(exc).__name__}",
        )
    parts = data.split(b"\n")
    complete, fragment = parts[:-1], parts[-1]
    lines: list[AdoptionReviewLine] = []
    for number, raw in enumerate(complete, start=1):
        checked = _raw_line(raw)
        if isinstance(checked, str):
            return _failure(ReviewDefect.LINE_CORRUPT, number, checked)
        payload, recorded, prev = checked
        try:
            entry = review_entry_of(payload["entry"])
            if encode(entry) != encode(payload["entry"]):
                raise KernelValueError("the entry is not in the canonical form it was saved in")
        except (*_READ_ERRORS, KernelValueError) as exc:
            return _failure(ReviewDefect.ENTRY_INVALID, number, f"{type(exc).__name__}: {exc}")
        if review_line_digest(entry, prev) != recorded:
            return _failure(
                ReviewDefect.LINE_CORRUPT, number, "digest does not match {entry, prev}"
            )
        lines.append(AdoptionReviewLine(entry=entry, prev=prev, digest=recorded))
    found = review_ledger_defect(lines)
    if found is not None:
        kind, number, detail = found
        return _failure(kind, number, detail)
    return ReviewLedgerContents(lines=tuple(lines), torn_tail=bool(fragment))


# --- 追記 ---------------------------------------------------------------------------


def _lock_text() -> str:
    """ロックの中身（人間が読むためのもの。ツールはこれを見て判断しない）。"""
    return (
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "host": socket.gethostname(),
                "pid": os.getpid(),
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def _sync(descriptor: int) -> None:
    """行を書いた後の同期（テストが失敗を差し込めるよう 1 か所に置く）。"""
    os.fsync(descriptor)


def append_review_ledger_file(
    root: Path, line: AdoptionReviewLine
) -> ReviewAppendOutcome | ReviewAppendRefused:
    """1 行を審査台帳へ追記する（試行台帳の追記と同じ (1)〜(6)。D09 §10.12.1）。

    (1) 台帳が無ければロックも作らずに `READ_FAILED`。あればロックを排他的に作る（できなければ
    `LOCKED`）。(2) ロックの下で読み直し、(3) 最後の完全な行のダイジェストと `line.prev` を照合し
    （違えば `TAIL_CHANGED`）、(4) 末尾の書きかけを切り詰め、(5) 行を 1 回で書いて同期し、
    (6) ロックを消す。完全な行を書いた後の同期の失敗は `sync_failed = True` で記録ありとして
    返す（D16 §11.3）。
    """
    if line.digest != review_line_digest(line.entry, line.prev):
        raise KernelValueError("the review line digest does not match {entry, prev}")
    path = Path(root) / ADOPTION_REVIEW_LEDGER_PATH
    lock = Path(root) / ADOPTION_REVIEW_LOCK_PATH
    if not (path.exists() or path.is_symlink()):
        return ReviewAppendRefused(
            kind=ReviewAppendRefusal.READ_FAILED,
            detail=f"{ADOPTION_REVIEW_LEDGER_PATH} does not exist",
        )
    try:
        write_new_file(lock, _lock_text())
    except FileExistsError:
        return ReviewAppendRefused(
            kind=ReviewAppendRefusal.LOCKED,
            detail=(
                f"{ADOPTION_REVIEW_LOCK_PATH} exists: another append is running, or a stopped"
                " writer left it. It is never removed automatically; make sure no writer is"
                " running, then delete it"
            ),
        )
    try:
        contents = read_review_ledger_file(path)
        if isinstance(contents, ReviewLedgerReadFailure):
            return ReviewAppendRefused(kind=ReviewAppendRefusal.READ_FAILED, detail=contents.detail)
        tail = last_review_digest(contents.lines)
        if tail != line.prev:
            return ReviewAppendRefused(
                kind=ReviewAppendRefusal.TAIL_CHANGED,
                detail="the last line of the review ledger changed after it was read",
            )
        data = path.read_bytes()
        keep = len(data) if not contents.torn_tail else data.rfind(b"\n") + 1
        encoded = review_line_bytes(line)
        descriptor = os.open(path, os.O_WRONLY)
        try:
            try:
                os.ftruncate(descriptor, keep)
                os.lseek(descriptor, 0, os.SEEK_END)
                written = os.write(descriptor, encoded)
            except OSError as exc:
                return ReviewAppendRefused(
                    kind=ReviewAppendRefusal.WRITE_FAILED, detail=f"writing the line failed: {exc}"
                )
            if written != len(encoded):
                return ReviewAppendRefused(
                    kind=ReviewAppendRefusal.WRITE_FAILED,
                    detail=f"only {written} of {len(encoded)} bytes were written",
                )
            try:
                _sync(descriptor)
            except OSError:
                return ReviewAppendOutcome(line=line, sync_failed=True)
        finally:
            os.close(descriptor)
        return ReviewAppendOutcome(line=line, sync_failed=False)
    finally:
        lock.unlink(missing_ok=True)


# --- 数値の記録（版管理の外）----------------------------------------------------------


def write_review_record(
    artifacts_root: Path,
    version_directory: Path,
    nonce: str,
    record: Mapping[str, Any],
    markdown: str,
) -> tuple[Path, ContentDigest]:
    """数値の記録を `<版のディレクトリ>/review/<乱数>/` に「存在すれば失敗」で書く。

    返すのは置き場と `review_record.json` のバイト列の SHA-256（審査台帳の
    `numeric_record_digest`）。置き場が既にあれば `FileExistsError`（前の置き場を上書きしない）。
    """
    ensure_experiment_directory(artifacts_root, version_directory)
    review = version_directory / REVIEW_DIRECTORY
    if review.is_symlink() or (review.exists() and not review.is_dir()):
        raise KernelValueError(f"{review} is a symbolic link or not a directory")
    review.mkdir(exist_ok=True)
    place = review / nonce
    place.mkdir()
    data = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_new_file(place / REVIEW_RECORD_FILE, data)
    write_new_file(place / REVIEW_MARKDOWN_FILE, markdown)
    return place, ContentDigest.sha256(hashlib.sha256(data.encode("utf-8")).hexdigest())
