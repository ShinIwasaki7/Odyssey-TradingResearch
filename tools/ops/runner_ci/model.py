"""検査の入力（変更の一覧・ある時点のファイル・PR の情報）と結果の型、git からの読み取り。"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol


class ChangeStatus(Enum):
    """基点から PR の先頭までのファイルの変化（git の `--no-renames` の区分。移動は削除と追加）。"""

    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"


@dataclass(frozen=True, slots=True)
class Change:
    path: str
    status: ChangeStatus


class Snapshot(Protocol):
    """ある時点（コミット）のファイルの読み方。"""

    def read(self, path: str) -> bytes | None:
        """ファイルのバイト列（無ければ `None`）。"""
        ...

    def list(self, prefix: str) -> tuple[str, ...]:
        """`prefix` の下のファイルのパス（辞書順）。"""
        ...


@dataclass(frozen=True, slots=True)
class DictSnapshot:
    """試験用の、辞書で持つ時点。"""

    files: Mapping[str, bytes] = field(default_factory=dict)

    def read(self, path: str) -> bytes | None:
        return self.files.get(path)

    def list(self, prefix: str) -> tuple[str, ...]:
        return tuple(sorted(path for path in self.files if path.startswith(prefix)))


class GitError(RuntimeError):
    """git の呼び出しが失敗した（入力の誤り。終了コード 2）。"""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - 引数は固定の git の部分命令と、検査の入力のコミット
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        check=False,
    )


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    """git のコミットの時点（作業木は読まない）。"""

    repo: Path
    rev: str

    def read(self, path: str) -> bytes | None:
        completed = _git(self.repo, "cat-file", "blob", f"{self.rev}:{path}")
        return completed.stdout if completed.returncode == 0 else None

    def list(self, prefix: str) -> tuple[str, ...]:
        completed = _git(self.repo, "ls-tree", "-r", "--name-only", "-z", self.rev, "--", prefix)
        if completed.returncode != 0:
            raise GitError(f"git ls-tree {self.rev} failed: {completed.stderr.decode().strip()}")
        names = completed.stdout.decode("utf-8").split("\0")
        return tuple(sorted(name for name in names if name))


def resolve(repo: Path, rev: str) -> str:
    """コミットの名前を SHA に直す（無ければ `GitError`）。"""
    completed = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}")
    if completed.returncode != 0:
        raise GitError(f"commit {rev!r} is not available: {completed.stderr.decode().strip()}")
    return completed.stdout.decode().strip()


def merge_base(repo: Path, first: str, second: str) -> str:
    """2 つのコミットの合流の基点（PR の差分と台帳の比べる相手。無ければ `GitError`）。"""
    completed = _git(repo, "merge-base", first, second)
    if completed.returncode != 0:
        raise GitError(
            f"no merge base of {first} and {second}: {completed.stderr.decode().strip()}"
        )
    return completed.stdout.decode().strip()


def git_changes(repo: Path, base: str, head: str) -> tuple[Change, ...]:
    """基点から先頭までの変更の一覧（移動を削除と追加に分ける。パスの辞書順）。"""
    completed = _git(repo, "diff", "--name-status", "--no-renames", "-z", base, head)
    if completed.returncode != 0:
        raise GitError(f"git diff failed: {completed.stderr.decode().strip()}")
    fields = completed.stdout.decode("utf-8").split("\0")
    changes: list[Change] = []
    index = 0
    while index + 1 < len(fields):
        letter, path = fields[index], fields[index + 1]
        index += 2
        if letter == "A":
            status = ChangeStatus.ADDED
        elif letter == "D":
            status = ChangeStatus.DELETED
        elif letter in {"M", "T"}:
            status = ChangeStatus.MODIFIED
        else:
            raise GitError(f"unexpected change status {letter!r} for {path}")
        changes.append(Change(path=path, status=status))
    return tuple(sorted(changes, key=lambda change: change.path))


@dataclass(frozen=True, slots=True)
class PullRequest:
    """PR の情報（GitHub の出来事から読む）。"""

    branch: str
    title: str
    body: str
    auto_merge: bool


@dataclass(frozen=True, slots=True)
class CheckInput:
    """検査の入力。`base` が `None` なのは基点の無い push（新しい枝）だけ。"""

    base: Snapshot | None
    head: Snapshot
    changes: tuple[Change, ...]
    pull_request: PullRequest | None


class Status(Enum):
    PASS = "合格"
    FAIL = "不合格"
    NOT_APPLICABLE = "当てない"


@dataclass(frozen=True, slots=True)
class CheckResult:
    number: int
    name: str
    status: Status
    messages: tuple[str, ...] = ()


def result(
    number: int, name: str, problems: Sequence[str], notes: Sequence[str] = ()
) -> CheckResult:
    """食い違いが無ければ合格、あれば不合格の結果（`notes` は合否に関わらない注記）。"""
    status = Status.FAIL if problems else Status.PASS
    return CheckResult(number, name, status, (*problems, *notes))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
