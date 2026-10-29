"""品質確認の閲覧の記録のファイル（`research/quality_access_log.jsonl`。ADR-0035 第4項の 5）。

記録を失わないための規則（ADR-0035 第4項の 5。試行台帳の D09 v0.3 §10.12.1 に揃える）:

1. 追記は 1 回ごとに作って消すロック `research/quality_access_log.lock`（版管理しない）の下で
   行う。ロックを取れなければ記録に失敗したとして断る（止まって残ったロックは自動で外さない）。
2. 記録のファイルは追記で作らない（空のファイルを版管理で置く）。無ければ断る。
3. 末尾に改行で終わらない断片（前の追記が途中で止まった跡）があれば、追記の前にその断片だけを
   切り詰める（断片につなげて追記しない。試行台帳の W3）。改行で終わる行の壊れは止める。
4. 行全体（改行を含む）を 1 回で書いて同期し、読み直して最後の行が書いた行と一致したことを
   もって「記録に成功した」とする。

行は正規化エンコード（D02 §9.3。鍵の整列・空白なし）で書き、読込は正規化エンコードと一致しない
行を壊れた行として止める（手での編集の誤りを見つける）。既存の行を書き換えない・消さない・
並べ替えない。コミットと push は人間が行う。
"""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from odyssey_fx.common.canonical import encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.marketdata.application.quality_access_log import row_from_payload, row_payload
from odyssey_fx.marketdata.domain.quality_access import (
    QualityAccessLogContents,
    QualityAccessLogFailure,
    QualityAccessRow,
    QualityLogAppendRefusal,
    QualityLogAppendRefused,
    QualityLogDefect,
    quality_log_defect,
)

__all__ = [
    "QUALITY_ACCESS_LOCK_PATH",
    "QUALITY_ACCESS_LOG_PATH",
    "QualityAccessLogFile",
    "append_quality_access_row",
    "quality_access_line_bytes",
    "read_quality_access_log_file",
]

#: 記録とロックの置き場（リポジトリの根からの相対パス。D01 §10.4）。
QUALITY_ACCESS_LOG_PATH: Final = Path("research") / "quality_access_log.jsonl"
QUALITY_ACCESS_LOCK_PATH: Final = Path("research") / "quality_access_log.lock"


def quality_access_line_bytes(row: QualityAccessRow) -> bytes:
    """1 行の符号化（正規化エンコード＋改行 1 つ）。"""
    return encode(row_payload(row)) + b"\n"


def _failure(kind: QualityLogDefect, number: int | None, detail: str) -> QualityAccessLogFailure:
    return QualityAccessLogFailure(kind=kind, line_number=number, detail=detail)


def read_quality_access_log_file(
    path: Path,
) -> QualityAccessLogContents | QualityAccessLogFailure:
    """記録のファイルを読み、行の形と構造の誤り（ADR-0035 第4項の 6）を確かめる。

    最後の改行より後の、改行で終わらない断片は書きかけとして行に入れず `torn_tail = True` に
    する（失敗にしない）。
    """
    if not (path.exists() or path.is_symlink()):
        return _failure(
            QualityLogDefect.FILE_MISSING,
            None,
            f"{QUALITY_ACCESS_LOG_PATH} does not exist; it is placed (empty) under version"
            " control and is never created by an append (ADR-0035 第4項の 5 の 5)",
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        return _failure(
            QualityLogDefect.FILE_UNREADABLE,
            None,
            f"{QUALITY_ACCESS_LOG_PATH} cannot be read: {type(exc).__name__}: {exc.strerror}",
        )
    parts = data.split(b"\n")
    complete, fragment = parts[:-1], parts[-1]
    rows: list[QualityAccessRow] = []
    for number, raw in enumerate(complete, start=1):
        try:
            payload = json.loads(raw.decode("utf-8"))
            row = row_from_payload(payload)
            if quality_access_line_bytes(row) != raw + b"\n":
                raise KernelValueError(
                    "the line is not in the canonical encoding of its row (D02 §9.3)"
                )
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            return _failure(QualityLogDefect.LINE_CORRUPT, number, f"{type(exc).__name__}: {exc}")
        rows.append(row)
    found = quality_log_defect(rows)
    if found is not None:
        kind, number, detail = found
        return _failure(kind, number, detail)
    return QualityAccessLogContents(rows=tuple(rows), torn_tail=bool(fragment))


def _lock_text() -> str:
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
    )


def _create_lock(lock: Path) -> None:
    """ロックを排他的に作る（既にあれば `FileExistsError`）。

    作った後の書き込み・同期・閉じるのどれかが失敗したら、**自分で作ったロックを消してから**
    例外をそのまま送出する（残すと以後の追記がすべて「ロックがある」で断られ、人間が消すまで
    続くため。ADR-0035 第4項の 5 の 1「追記の 1 回ごとに作って消すロック」）。
    """
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        try:
            os.write(descriptor, _lock_text().encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        lock.unlink(missing_ok=True)
        raise


def _append_bytes(path: Path, keep: int, data: bytes) -> None:
    """末尾の書きかけを切り詰め（`keep` バイトまで戻す）、行全体を 1 回で書いて同期する。"""
    descriptor = os.open(path, os.O_WRONLY)
    try:
        os.ftruncate(descriptor, keep)
        os.lseek(descriptor, 0, os.SEEK_END)
        written = os.write(descriptor, data)
        if written != len(data):
            raise OSError(f"only {written} of {len(data)} bytes were written")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_quality_access_row(
    root: Path, row: QualityAccessRow
) -> QualityAccessRow | QualityLogAppendRefused:
    """1 行を記録に追記し、読み直して一致を確かめる（ADR-0035 第4項の 5）。

    成功なら `row` を返す。断るとき（ロックを作る前の「記録が無い」を除く）もロックを消して
    から返す。
    """
    path = Path(root) / QUALITY_ACCESS_LOG_PATH
    lock = Path(root) / QUALITY_ACCESS_LOCK_PATH
    if not (path.exists() or path.is_symlink()):
        failure = read_quality_access_log_file(path)
        assert isinstance(failure, QualityAccessLogFailure)  # noqa: S101  無いので必ず読めない
        return QualityLogAppendRefused(
            kind=QualityLogAppendRefusal.READ_FAILED, detail=failure.detail, read_failure=failure
        )
    try:
        _create_lock(lock)
    except FileExistsError:
        return QualityLogAppendRefused(
            kind=QualityLogAppendRefusal.LOCKED,
            detail=(
                f"{QUALITY_ACCESS_LOCK_PATH} exists: another append is running, or a stopped"
                " writer left it. It is never removed automatically; make sure no writer is"
                " running, then delete it (ADR-0035 第4項の 5 の 1)"
            ),
        )
    except OSError as exc:
        return QualityLogAppendRefused(
            kind=QualityLogAppendRefusal.LOCKED,
            detail=(
                f"the lock {QUALITY_ACCESS_LOCK_PATH} could not be created or written; a lock this"
                f" append created was removed again: {exc}"
            ),
        )
    try:
        contents = read_quality_access_log_file(path)
        if isinstance(contents, QualityAccessLogFailure):
            return QualityLogAppendRefused(
                kind=QualityLogAppendRefusal.READ_FAILED,
                detail=contents.detail,
                read_failure=contents,
            )
        found = quality_log_defect((*contents.rows, row))
        if found is not None:
            return QualityLogAppendRefused(
                kind=QualityLogAppendRefusal.ROW_REFUSED,
                detail=f"the new row would make the log invalid ({found[0].value}): {found[2]}",
            )
        data = path.read_bytes()
        keep = data.rfind(b"\n") + 1 if contents.torn_tail else len(data)
        line = quality_access_line_bytes(row)
        try:
            _append_bytes(path, keep, line)
        except OSError as exc:
            return QualityLogAppendRefused(
                kind=QualityLogAppendRefusal.WRITE_FAILED,
                detail=f"writing or syncing the row failed: {exc}",
            )
        reread = read_quality_access_log_file(path)
        if (
            isinstance(reread, QualityAccessLogFailure)
            or not reread.rows
            or reread.rows[-1] != row
            or reread.torn_tail
        ):
            return QualityLogAppendRefused(
                kind=QualityLogAppendRefusal.VERIFY_FAILED,
                detail="the row read back does not match the row written (ADR-0035 第4項の 5)",
            )
        return row
    finally:
        lock.unlink(missing_ok=True)


class QualityAccessLogFile:
    """`QualityAccessLog` ポート（`evaluation.application.ports`）の実装（`app` が結線する）。

    `root` はリポジトリの根（`<root>/research/quality_access_log.jsonl`）。
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def read(self) -> QualityAccessLogContents | QualityAccessLogFailure:
        """記録を読む（行の形と構造の誤りを確かめる）。"""
        return read_quality_access_log_file(self._root / QUALITY_ACCESS_LOG_PATH)

    def append(self, row: QualityAccessRow) -> QualityAccessRow | QualityLogAppendRefused:
        """1 行を追記し、読み直して一致を確かめる。"""
        return append_quality_access_row(self._root, row)
