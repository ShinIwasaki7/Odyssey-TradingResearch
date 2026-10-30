"""最終検証の記録の 2 層（D09 §9.10.5。仮置き R2-5）の読み書き。

- **最終検証の台帳** `research/final_validations.jsonl`（版管理。1 申請の終わりに 1 行。指標の値を
  書かない）。書き込み規約は試行台帳と同じ（追記だけ・行の鎖・ロック
  `research/final_validations.lock`・空のファイルを先に版管理に置く・コミットと push は人間。
  D09 §10.12.1・§10.12.6）。
- **控えと最終検証の記録**（版管理の外）: `<実験の版のディレクトリ>/final/<execution>/` の
  ロック `final.lock`、控え `permit.json`、最終検証の記録 `final_evaluation.json`、人が読む
  `report.md`。

行の項目の名前・行の鎖の持ち方・控えと記録のキーの名前は R4 の仮置き（D09 は中身を決め、名前は
実装に委ねた）。行の値は文字列・整数・入れ子の対象で、正規化エンコード（D02 §9.3）で 1 行 1 JSON。
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.final_pending import (
    FINAL_DIRECTORY,
    FINAL_EVALUATION_FILE,
    FINAL_LEDGER_PATH,
    FINAL_LOCK_FILE,
    PERMIT_FILE,
)
from odyssey_fx.common.canonical import encode
from odyssey_fx.evaluation.domain.final_validation import CandidateKey, FinalLedgerResult

__all__ = [
    "ATTEMPTS_DIRECTORY",
    "ATTEMPTS_FILE",
    "FINAL_DIRECTORY",
    "FINAL_EVALUATION_FILE",
    "FINAL_LEDGER_LOCK_PATH",
    "FINAL_LEDGER_PATH",
    "FINAL_LOCK_FILE",
    "LEDGER_SCHEMA_VERSION",
    "PERMIT_FILE",
    "REPORT_FILE",
    "FinalLedger",
    "FinalLedgerError",
    "LedgerRow",
    "append_attempt",
    "append_ledger_row",
    "candidate_payload",
    "create_lock",
    "final_directory",
    "parse_final_ledger",
    "read_attempts",
    "read_final_ledger",
    "write_json_once",
]

#: 台帳の追記のロック（版管理しない。`.gitignore`）。
FINAL_LEDGER_LOCK_PATH: Final = Path("research") / "final_validations.lock"
REPORT_FILE: Final = "report.md"
#: 段 5 の試みの記録（仮置き R2-25・R2-27。2026-10-30 に決定。R4b）。試みを始める時（段 5 に
#: 入る前）に `STARTED` の行を、その試みが消費の後の失敗で終わったら `FAILED` の行を足す。
#: 最初の試み（段 4 の直後）も 1 回と数える（やり直しの回数は `STARTED` の行の数 − 1。R4b）。
ATTEMPTS_FILE: Final = "attempts.jsonl"
#: やり直しで退けた前の試みの途中の成果物の置き場（仮置き R2-24。`attempts/<n>/`）。
ATTEMPTS_DIRECTORY: Final = "attempts"
#: 台帳の行の書式の版。
LEDGER_SCHEMA_VERSION: Final = 1

_ROW_KEYS: Final = frozenset(
    {
        "schema_version",
        "result",
        "candidate",
        "permit_id",
        "t_fix",
        "consumption_start",
        "consumption_end",
        "partition_count",
        "partitions_digest",
        "consumption_commit",
        "combined_verdict",
        "resume_count",
        "resume_failures",
        "prior_final_validations",
        "reason",
        "final_evaluation_sha256",
        "declaration_sha256",
        "recorded_at",
        "actor",
        "prev",
    }
)


class FinalLedgerError(Exception):
    """台帳を読めない・書けない（`line` は行の番号。読めないときは申請の読込の誤り）。"""

    def __init__(self, detail: str, line: int | None = None) -> None:
        super().__init__(detail if line is None else f"line {line}: {detail}")
        self.detail = detail
        self.line = line


def candidate_payload(candidate: CandidateKey) -> dict[str, str]:
    """台帳・控え・記録の候補の値（消費の行の `candidate` と同じ形。値は文字列）。"""
    return candidate.consumption().payload()


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """台帳の 1 行（読み込んだもの）。"""

    payload: Mapping[str, Any]

    @property
    def result(self) -> FinalLedgerResult:
        return FinalLedgerResult(self.payload["result"])

    @property
    def experiment_id(self) -> str:
        return str(self.payload["candidate"]["experiment_id"])

    @property
    def execution(self) -> int:
        return int(self.payload["candidate"]["execution"])

    @property
    def permit_id(self) -> str:
        return str(self.payload["permit_id"])

    def is_for(self, candidate: CandidateKey) -> bool:
        return (self.experiment_id, self.execution) == (
            candidate.experiment_id,
            candidate.execution,
        )


@dataclass(frozen=True, slots=True)
class FinalLedger:
    """読み込んだ台帳（追記の順）。"""

    rows: tuple[LedgerRow, ...]
    last_digest: str

    def of(self, candidate: CandidateKey) -> tuple[LedgerRow, ...]:
        return tuple(row for row in self.rows if row.is_for(candidate))

    def of_experiment(self, experiment_id: str) -> tuple[LedgerRow, ...]:
        return tuple(row for row in self.rows if row.experiment_id == experiment_id)


def _line_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_final_ledger(data: bytes) -> FinalLedger:
    """台帳の内容を読み、行の形・正規化・鎖を確かめる（違えば `FinalLedgerError`）。"""
    if data and not data.endswith(b"\n"):
        raise FinalLedgerError("the ledger ends with a fragment that is not a full line")
    rows: list[LedgerRow] = []
    previous = ""
    for number, raw in enumerate(data.split(b"\n")[:-1] if data else (), start=1):
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise FinalLedgerError(f"not JSON ({type(exc).__name__})", number) from exc
        if not isinstance(payload, dict) or set(payload) != _ROW_KEYS:
            raise FinalLedgerError("the keys of the row differ from the schema", number)
        if payload["schema_version"] != LEDGER_SCHEMA_VERSION:
            raise FinalLedgerError(f"unknown schema_version {payload['schema_version']!r}", number)
        if encode(payload) != raw:
            raise FinalLedgerError("the line is not in the canonical encoding (D02 §9.3)", number)
        if payload["prev"] != previous:
            raise FinalLedgerError("prev does not equal the digest of the line before", number)
        try:
            row = LedgerRow(payload=payload)
            row.result  # noqa: B018 - 知らない結果を読込で止める
            _ = row.experiment_id, row.execution
        except (KeyError, TypeError, ValueError) as exc:
            raise FinalLedgerError(f"{type(exc).__name__}: {exc}", number) from exc
        rows.append(row)
        previous = _line_digest(raw)
    return FinalLedger(rows=tuple(rows), last_digest=previous)


def read_final_ledger(repo_root: Path) -> FinalLedger:
    """作業木の台帳を読む（無ければ `FinalLedgerError`。空のファイルを版管理に置く）。"""
    path = Path(repo_root) / FINAL_LEDGER_PATH
    if not path.is_file():
        raise FinalLedgerError(
            f"{FINAL_LEDGER_PATH} does not exist; it is placed (empty) under version control and"
            " is never created by an append (D09 §9.10.5)"
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise FinalLedgerError(f"{FINAL_LEDGER_PATH} cannot be read: {exc}") from exc
    return parse_final_ledger(data)


def _lock_text() -> bytes:
    """ロックの中身（人間が読むためのもの。道具はこれを見て判断しない）。"""
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
    ).encode("utf-8")


def create_lock(path: Path) -> None:
    """ロックを排他的に作る（既にあれば `FileExistsError`。書けなければ自分で作ったものを消す）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(descriptor, _lock_text())
        os.fsync(descriptor)
    except OSError:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)


def append_ledger_row(repo_root: Path, values: Mapping[str, Any]) -> LedgerRow:
    """台帳に 1 行追記する（ロックの下で読み直し、鎖をつなぎ、追記して読み直す）。

    `values` は `prev` を除く行の値。失敗は `FinalLedgerError`（書きかけは残さない）。
    """
    root = Path(repo_root)
    lock = root / FINAL_LEDGER_LOCK_PATH
    try:
        create_lock(lock)
    except FileExistsError as exc:
        raise FinalLedgerError(
            f"{FINAL_LEDGER_LOCK_PATH} exists: another append is running, or a stopped writer left"
            " it; a human checks that no process is running and removes it (D09 §10.12.1)"
        ) from exc
    except OSError as exc:
        raise FinalLedgerError(f"{FINAL_LEDGER_LOCK_PATH} cannot be created: {exc}") from exc
    try:
        ledger = read_final_ledger(root)
        payload = {**values, "schema_version": LEDGER_SCHEMA_VERSION, "prev": ledger.last_digest}
        if set(payload) != _ROW_KEYS:
            raise FinalLedgerError(
                f"the row lacks or adds items: {sorted(set(payload) ^ _ROW_KEYS)}"
            )
        line = encode(payload) + b"\n"
        path = root / FINAL_LEDGER_PATH
        size = path.stat().st_size
        try:
            with path.open("ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            with path.open("r+b") as handle:
                handle.truncate(size)
            raise FinalLedgerError(f"{FINAL_LEDGER_PATH} cannot be appended: {exc}") from exc
        again = read_final_ledger(root)
        if not again.rows or dict(again.rows[-1].payload) != payload:
            raise FinalLedgerError("the row read back differs from the row written")
        return again.rows[-1]
    finally:
        lock.unlink(missing_ok=True)


def read_attempts(final_dir: Path) -> list[dict[str, Any]]:
    """`attempts.jsonl` の行（無ければ空。読めなければ `FinalLedgerError`。仮置き R2-25）。"""
    path = Path(final_dir) / ATTEMPTS_FILE
    if not (path.exists() or path.is_symlink()):
        return []
    try:
        if path.is_symlink() or not path.is_file():
            raise FinalLedgerError(f"{ATTEMPTS_FILE} is not a plain file")
        data = path.read_bytes()
    except OSError as exc:
        raise FinalLedgerError(f"{ATTEMPTS_FILE} cannot be read: {exc}") from exc
    if data and not data.endswith(b"\n"):
        raise FinalLedgerError(f"{ATTEMPTS_FILE} ends with a fragment")
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(data.split(b"\n")[:-1] if data else (), start=1):
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise FinalLedgerError(f"{ATTEMPTS_FILE} is not JSON", number) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("event") not in ("STARTED", "FAILED")
            or not isinstance(payload.get("attempt"), int)
            or encode(payload) != raw
        ):
            raise FinalLedgerError(f"{ATTEMPTS_FILE} has an unknown row", number)
        rows.append(payload)
    return rows


def append_attempt(final_dir: Path, payload: Mapping[str, Any]) -> None:
    """`attempts.jsonl` に 1 行足して同期する（失敗は `OSError`）。"""
    path = Path(final_dir) / ATTEMPTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(encode(dict(payload)) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def final_directory(experiment_dir: Path, execution: int) -> Path:
    """`<実験の版のディレクトリ>/final/<execution>/`（D09 §9.10.5）。"""
    return Path(experiment_dir) / FINAL_DIRECTORY / str(execution)


def write_json_once(path: Path, payload: Mapping[str, Any]) -> str:
    """JSON を新しく書き（既にあれば `FileExistsError`）、内容の SHA-256 を返す。

    一時のファイルに全部を書いて同期してから、確定の名前へ排他的に結ぶ（`os.link`）。途中で
    失敗したら一時のファイルを消し、確定の名前には何も残さない（書きかけの記録を「書いた」と
    読まないため。状態 D の導出は確定の名前の有無で見る）。
    """
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()
