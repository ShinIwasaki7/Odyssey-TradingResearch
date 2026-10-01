"""補充の置き場の読み書き（D03 §8・§14.11・§14.11.1、D01 §4 v2.9）。

`RefillStore`（`marketdata.application.ports`）を実装する。ポートの定義は import せず構造的に
満たす（D01 §2.2 規則7）。置き場（`data/raw/market/refill/`）の下だけを扱い、原データと
snapshot には書かない（D03 §14.2 の原則2）。

書き方は D03 §14.11.1 の規則に従う:

- **W1 書き手は 1 つ**: 計画のロック `_work/<plan_id>/lock` と時間のロック
  `_ticks/<SYMBOL>/<YYYY>/<MM>/<DD>/<HH>h/lock` を排他的に作る。ロックには作成時刻（UTC）・
  ホスト名・プロセス ID を書く。残ったロックは自動で外さない。外すのは自分が作ったもの
  （書いた内容が同じもの）だけ。
- **W2 一時名に書いてから排他的に作成する**: 一時名に全体を書いて確定（fsync）してから、
  ハードリンクの作成（同じ名前が既にあれば失敗する）で本来の名前に置き、一時名を消す。
  名前の変更（置き換えうる）は使わない。ディレクトリも排他的に作る。
- **W3 取得記録の行にダイジェストを付ける**: 1 行は `{"digest": …, "entry": …}` の正規化
  エンコード。追記は 1 行を 1 回の書き込みで末尾に足して確定する。
- 置き場の下のディレクトリ・ファイルがシンボリックリンクなら、辿らずに食い違いとして止める。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import socket
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from odyssey_fx.common import canonical
from odyssey_fx.marketdata.adapters.dukascopy_source import decode_bi5
from odyssey_fx.marketdata.application.ports import JournalLine, RefillDirectory
from odyssey_fx.marketdata.domain.errors import (
    MarketDataValueError,
    RefillPlanAlreadyExists,
    RefillPlanLocked,
    RefillPlanNotFound,
    RefillStoreInconsistent,
)
from odyssey_fx.marketdata.domain.refill import (
    WORK_DIRECTORY,
    ArchiveProvenance,
    ArchiveRead,
    HourKey,
    require_hex_digest,
    sha256_hex,
)

__all__ = ["FsRefillStore"]

#: 保管場所の 1 件の形式の印。
_ARCHIVE_FORMAT: Final = "refill_tick_archive_v1"

#: 補充分の完成の印（D03 §14.11.1 の W4）。
_REFILL_MANIFEST: Final = "refill_manifest.json"

_PLAN_FILE: Final = "plan.json"
_JOURNAL_FILE: Final = "journal.jsonl"
_LOCK_FILE: Final = "lock"
_TEMP_PREFIX: Final = ".tmp-"

_HEX: Final = re.compile(r"[0-9a-f]{64}")


def _lock_content() -> bytes:
    """ロックに書く内容（作成時刻・ホスト名・プロセス ID。W1）。一意の印も添える。"""
    payload = {
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "token": uuid.uuid4().hex,
    }
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _journal_line(entry: Mapping[str, Any]) -> bytes:
    """取得記録の 1 行（W3）。内容の正規化エンコードの sha256 を添える。"""
    digest = sha256_hex(canonical.encode(entry))
    return canonical.encode({"digest": digest, "entry": entry}) + b"\n"


def _parse_journal_line(segment: bytes) -> tuple[Mapping[str, Any] | None, str]:
    try:
        decoded = json.loads(segment.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"not JSON ({exc})"
    if not isinstance(decoded, dict) or set(decoded) != {"digest", "entry"}:
        return None, "not a {digest, entry} record"
    entry = decoded["entry"]
    if not isinstance(entry, dict):
        return None, "the entry is not a mapping"
    try:
        recomputed = sha256_hex(canonical.encode(entry))
    except ValueError as exc:
        return None, f"the entry cannot be encoded ({exc})"
    if recomputed != decoded["digest"]:
        return None, "the digest does not match the entry"
    return entry, ""


class FsRefillStore:
    """ファイルシステム上の補充の置き場（D03 §14.11）。`root` は `data/raw/market/refill/`。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._held_locks: dict[Path, bytes] = {}

    # --- パス --------------------------------------------------------------------------

    def _root_chain(self) -> list[Path]:
        """リンクでないことを確かめる道筋: 置き場そのものと、現在のディレクトリから置き場まで
        の途中のディレクトリ（置き場が現在のディレクトリの下にあるとき）。

        現在のディレクトリより上（`/var` など環境のリンク）は利用者の環境なので見ない。
        """
        absolute = self._root if self._root.is_absolute() else Path.cwd() / self._root
        try:
            relative = absolute.relative_to(Path.cwd())
        except ValueError:
            return [self._root]
        chain: list[Path] = []
        current = Path.cwd()
        for part in relative.parts:
            current = current / part
            chain.append(current)
        return chain or [self._root]

    def _require_plain_root(self) -> None:
        """置き場そのものがリンクでもディレクトリでないものでもないことを確かめる（W6）。

        置き場がリンクだと、`_work`・`_ticks` の作成と書き込みがリンク先（所定の置き場の外）で
        行われる。リンクは辿らずに食い違いとして止める。
        """
        for path in self._root_chain():
            if path.is_symlink():
                raise RefillStoreInconsistent(
                    f"{path} (the refill root or a directory on the way to it) is a symbolic"
                    " link; the refill store never follows links. Nothing was written"
                    " (D03 §14.11.1 W6)"
                )
        if self._root.exists() and not self._root.is_dir():
            raise RefillStoreInconsistent(
                f"the refill root {self._root} is not a directory (D03 §14.11)"
            )

    def _inside(self, relative: str, *, create_parents: bool) -> Path:
        """置き場からの相対パスを、リンクを辿らずに組み立てる。

        途中のディレクトリがシンボリックリンク・ディレクトリでないものなら食い違い（W6）。
        `create_parents` なら、無い親ディレクトリを作る（作るのは置き場の下だけ）。
        """
        parts = Path(relative).parts
        if not parts or any(part in ("", ".", "..") or "/" in part for part in parts):
            raise MarketDataValueError(f"invalid path under the refill root: {relative!r}")
        self._require_plain_root()
        current = self._root
        if create_parents:
            current.mkdir(parents=True, exist_ok=True)
        for part in parts[:-1]:
            current = current / part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise RefillStoreInconsistent(
                    f"{current} is a symbolic link or not a directory; the refill store never"
                    " follows links. Nothing was written (D03 §14.11.1 W6)"
                )
            if create_parents and not current.exists():
                try:
                    current.mkdir()
                except FileExistsError:
                    if current.is_symlink() or not current.is_dir():
                        raise RefillStoreInconsistent(
                            f"{current} appeared as a link or a file (D03 §14.11.1 W6)"
                        ) from None
        return current / parts[-1]

    def _work_dir(self, plan_id: str) -> Path:
        require_hex_digest(plan_id, "plan_id")
        return self._inside(f"{WORK_DIRECTORY}/{plan_id}", create_parents=False)

    def _work_file(self, plan_id: str, name: str) -> Path:
        directory = self._work_dir(plan_id)
        _refuse_link(directory)
        if not directory.is_dir():
            raise RefillPlanNotFound(f"no refill plan {plan_id} under {WORK_DIRECTORY}/")
        return directory / name

    def _hour_dir(self, hour: HourKey, *, create: bool) -> Path:
        # 末尾に仮の名前を継ぐと、`_inside` が時間のディレクトリまでを検査（と作成）する。
        return self._inside(f"{hour.archive_directory}/x", create_parents=create).parent

    # --- 一時名に書いてから排他的に作成する（W2）---------------------------------------

    @staticmethod
    def _place(target: Path, content: bytes) -> bool:
        """`target` が無ければ `content` を置いて `True`、既にあれば何も置かず `False`。"""
        temp = target.parent / f"{_TEMP_PREFIX}{target.name}-{os.getpid()}-{uuid.uuid4().hex}"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp, target, follow_symlinks=False)
            except FileExistsError:
                return False
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:  # pragma: no cover - 消えていれば何もしない
                pass
        _fsync_directory(target.parent)
        return True

    # --- 補充分 ------------------------------------------------------------------------

    def list_refills(self) -> tuple[RefillDirectory, ...]:
        """置き場の直下の補充分（名前が 16進 64 文字のもの）を名前順に返す。"""
        self._require_plain_root()
        if not self._root.exists():
            return ()
        found: list[RefillDirectory] = []
        for path in sorted(self._root.iterdir(), key=lambda item: item.name):
            name = path.name
            if not _HEX.fullmatch(name):
                continue  # `_ticks`・`_work`・利用者の置いたもの（補充分は識別子の名前）
            if path.is_symlink() or not path.is_dir():
                found.append(RefillDirectory(name=name, plan_id=None, problem="not a directory"))
                continue
            manifest = path / _REFILL_MANIFEST
            if manifest.is_symlink() or not manifest.is_file():
                found.append(
                    RefillDirectory(name=name, plan_id=None, problem=f"{_REFILL_MANIFEST} missing")
                )
                continue
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                plan_id = payload["plan_id"] if isinstance(payload, dict) else None
                require_hex_digest(plan_id, "refill_manifest.json plan_id")
            except (OSError, ValueError, KeyError) as exc:
                found.append(
                    RefillDirectory(
                        name=name, plan_id=None, problem=f"{_REFILL_MANIFEST} unreadable ({exc})"
                    )
                )
                continue
            found.append(RefillDirectory(name=name, plan_id=plan_id, problem="", manifest=payload))
        return tuple(found)

    # --- 作業ディレクトリ --------------------------------------------------------------

    def work_dir_exists(self, plan_id: str) -> bool:
        """作業ディレクトリが（書きかけ・リンクを含め）あるか。"""
        directory = self._work_dir(plan_id)
        return directory.is_symlink() or directory.exists()

    def create_work_dir(self, plan_id: str) -> None:
        """作業ディレクトリを排他的に作る（W2）。既にあれば `RefillPlanAlreadyExists`。"""
        require_hex_digest(plan_id, "plan_id")
        directory = self._inside(f"{WORK_DIRECTORY}/{plan_id}", create_parents=True)
        try:
            directory.mkdir()
        except FileExistsError:
            raise RefillPlanAlreadyExists(
                f"{directory} already exists; nothing was written (D03 §14.12)"
            ) from None

    def write_plan(self, plan_id: str, payload: Mapping[str, Any]) -> None:
        """`plan.json` を置く（W2）。既にあれば食い違い。"""
        target = self._work_file(plan_id, _PLAN_FILE)
        if not self._place(target, canonical.encode(payload) + b"\n"):
            raise RefillStoreInconsistent(f"{target} already exists (D03 §14.11.1 W2・W6)")

    def read_plan(self, plan_id: str) -> Mapping[str, Any] | None:
        """`plan.json` を読む。無ければ `None`。"""
        target = self._work_file(plan_id, _PLAN_FILE)
        _refuse_link(target)
        if not target.exists():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RefillStoreInconsistent(
                f"{target} cannot be read ({exc}) (D03 §14.11.1 W5・W6)"
            ) from exc
        if not isinstance(payload, dict):
            raise RefillStoreInconsistent(f"{target} is not a JSON object (D03 §14.11.1 W5・W6)")
        return payload

    # --- ロック（W1）-------------------------------------------------------------------

    def _acquire(self, target: Path) -> bool:
        content = _lock_content()
        if not self._place(target, content):
            return False
        self._held_locks[target] = content
        return True

    def _release(self, target: Path) -> None:
        content = self._held_locks.pop(target, None)
        if content is None or target.is_symlink():
            return  # 自分のものでない（リンクに置き換えられた）ものには触らない
        try:
            current = target.read_bytes()
        except FileNotFoundError:
            return
        if current == content:
            target.unlink()
            _fsync_directory(target.parent)

    def acquire_plan_lock(self, plan_id: str) -> None:
        """計画のロックを取る。取れなければ `RefillPlanLocked`（W1）。"""
        target = self._work_file(plan_id, _LOCK_FILE)
        if not self._acquire(target):
            _refuse_link(target)
            try:
                holder = target.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                holder = "(unreadable)"
            raise RefillPlanLocked(
                f"{target} exists (held by {holder}); another command is handling the plan, or"
                " a stopped process left it. Check that no such process remains, delete the"
                " lock, then retry. Nothing was written (D03 §14.11.1 W1)"
            )

    def release_plan_lock(self, plan_id: str) -> None:
        """自分が作った計画のロックを外す。"""
        self._release(self._work_file(plan_id, _LOCK_FILE))

    # --- 取得記録（W3）-----------------------------------------------------------------

    def read_journal(self, plan_id: str) -> tuple[JournalLine, ...]:
        """取得記録の全行を読み、行ごとのダイジェストを検算する。"""
        target = self._work_file(plan_id, _JOURNAL_FILE)
        _refuse_link(target)
        if not target.exists():
            return ()
        content = target.read_bytes()
        lines: list[JournalLine] = []
        offset = 0
        while offset < len(content):
            end = content.find(b"\n", offset)
            if end < 0:
                lines.append(
                    JournalLine(offset=offset, entry=None, problem="not terminated by a newline")
                )
                break
            entry, problem = _parse_journal_line(content[offset:end])
            lines.append(JournalLine(offset=offset, entry=entry, problem=problem))
            offset = end + 1
        return tuple(lines)

    def truncate_journal(self, plan_id: str, offset: int) -> None:
        """取得記録を `offset` バイトに切り詰める（不完全な最後の行を除く。W3）。"""
        target = self._work_file(plan_id, _JOURNAL_FILE)
        _refuse_link(target)
        size = target.stat().st_size
        if not 0 <= offset <= size:
            raise MarketDataValueError(f"cannot truncate {target} to {offset} bytes (size {size})")
        os.truncate(target, offset)

    def append_journal(self, plan_id: str, entry: Mapping[str, Any]) -> None:
        """取得記録の末尾に 1 行を追記して確定する（W3）。"""
        target = self._work_file(plan_id, _JOURNAL_FILE)
        line = _journal_line(entry)
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        try:
            written = os.write(fd, line)
            while written < len(line):  # pragma: no cover - 通常のファイルは一度に書ける
                written += os.write(fd, line[written:])
            os.fsync(fd)
        finally:
            os.close(fd)

    # --- tick の保管場所 ---------------------------------------------------------------

    def acquire_hour_lock(self, hour: HourKey) -> bool:
        """時間のロックを取る。取れなければ `False`（W1）。"""
        return self._acquire(self._hour_dir(hour, create=True) / _LOCK_FILE)

    def release_hour_lock(self, hour: HourKey) -> None:
        """自分が作った時間のロックを外す。"""
        self._release(self._hour_dir(hour, create=False) / _LOCK_FILE)

    def _archive_dir(self, hour: HourKey, source_digest: str, *, create: bool) -> Path:
        require_hex_digest(source_digest, "source_digest")
        return self._inside(
            f"{hour.archive_directory}/{source_digest}/x", create_parents=create
        ).parent

    def list_archive(self, hour: HourKey, source_digest: str) -> tuple[str, ...]:
        """その時間・その設定の完成したファイルの名前（`<tick_digest>.json`）を返す。"""
        directory = self._archive_dir(hour, source_digest, create=False)
        if directory.is_symlink():
            raise RefillStoreInconsistent(f"{directory} is a symbolic link (D03 §14.11.1 W6)")
        if not directory.exists():
            return ()
        names: list[str] = []
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.name.startswith(_TEMP_PREFIX):
                continue  # 一時名は成果物ではない（W2）
            if path.is_symlink() or not path.is_file():
                raise RefillStoreInconsistent(
                    f"{path} is not a plain file in the tick archive (D03 §14.11.1 W6)"
                )
            names.append(path.name)
        return tuple(names)

    def read_archive(
        self, hour: HourKey, source_digest: str, tick_digest: str
    ) -> ArchiveRead | None:
        """保管場所の 1 件を読む。無ければ `None`。形が読めなければ食い違い。"""
        require_hex_digest(tick_digest, "tick_digest")
        directory = self._archive_dir(hour, source_digest, create=False)
        target = directory / f"{tick_digest}.json"
        relative = f"{hour.archive_directory}/{source_digest}/{tick_digest}.json"
        _refuse_link(target)
        if not target.exists():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "body_base64",
                "format",
                "provenance",
            }:
                raise ValueError("not an archive record")
            if payload["format"] != _ARCHIVE_FORMAT:
                raise ValueError(f"unknown format {payload['format']!r}")
            body = base64.b64decode(payload["body_base64"], validate=True)
            provenance = ArchiveProvenance.from_payload(payload["provenance"], relative)
        except (OSError, ValueError, binascii.Error, TypeError) as exc:
            raise RefillStoreInconsistent(
                f"{target} cannot be read as an archived hour ({exc}). Nothing was written"
                " (D03 §14.11.1 W5・W6)"
            ) from exc
        try:
            decoded = decode_bi5(body)
            problem = ""
        except MarketDataValueError as exc:
            decoded = None
            problem = str(exc)
        return ArchiveRead(
            path=relative,
            hour=hour,
            directory_source_digest=source_digest,
            file_tick_digest=tick_digest,
            provenance=provenance,
            body_sha256=sha256_hex(body),
            decoded=decoded,
            decode_error=problem,
        )

    def write_archive(self, hour: HourKey, body: bytes, provenance: ArchiveProvenance) -> str:
        """保管場所に 1 件を置く（W2）。既にあれば食い違い（上書きしない）。"""
        directory = self._archive_dir(hour, provenance.source_digest, create=True)
        name = f"{provenance.tick_digest}.json"
        record = {
            "body_base64": base64.b64encode(body).decode("ascii"),
            "format": _ARCHIVE_FORMAT,
            "provenance": provenance.payload(),
        }
        if not self._place(directory / name, canonical.encode(record) + b"\n"):
            raise RefillStoreInconsistent(
                f"{directory / name} already exists; the archive is never overwritten"
                " (D03 §14.11.1 W2・W6)"
            )
        return f"{hour.archive_directory}/{provenance.source_digest}/{name}"


def _refuse_link(path: Path) -> None:
    """置き場の中のファイルがリンクなら、辿らずに食い違いとして止める（W6）。"""
    if path.is_symlink():
        raise RefillStoreInconsistent(
            f"{path} is a symbolic link; the refill store never follows links."
            " Nothing was written (D03 §14.11.1 W6)"
        )


def _fsync_directory(directory: Path) -> None:
    """ディレクトリの項目の追加・削除を確定する（できない環境では何もしない）。"""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - 環境による
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - 環境による
        pass
    finally:
        os.close(fd)
