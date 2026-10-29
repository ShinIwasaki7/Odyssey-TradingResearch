"""消費の記録のポート `HoldoutAccessLog` の git の実装（D01 §4 v2.8、ADR-0038 第8項の 3）。

`marketdata` の消費の記録の追記（`ParquetSnapshotStore.append_access_log` と
`marketdata.application.access_log.serialize_entry`）と、git の取得・commit・push を `app` が
適合させる（D09 Q11 の決定）。ADR-0014 の「消費遷移の直列化」（ADR-0038 第8項の 3 が引き継ぐ）の
手順を、次の 3 つの操作で行う。

1. `require_published_base`: origin を取得し、手元の既定ブランチの先頭が origin の既定ブランチの
   先頭と同じこと（origin に知らない追記が無いこと）と、commit するファイルの作業木の内容が手元の
   先頭の内容の後ろに行を足しただけであることを確かめる。
2. `append_consumed`: 消費の記録（`data/snapshots/<snapshot_id>/access_log.jsonl`）に `CONSUMED`
   の行を 1 行追記し、読み直して一致を確かめる。
3. `publish`: 渡したファイルだけを commit し、origin の既定ブランチへ push する（強制しない）。
   push が断られたら commit を取り消し（作業木の追記は残す）、止める。

**【仮置き】最小の形**（`evaluation.application.ports.HoldoutAccessLog` の注記）: 消費の記録の
行の項目は今の `AccessLogEntry` の形（ADR-0014）のまま。D09 の後続版（D15 の R2）が行の項目と
行の鍵を決めたら、R4 で作り直す。

git は `git -C <リポジトリの根>` の子プロセスで動かす。出力は commit の識別子・終了コード・
ファイルの相対パスだけを人間に渡す（価格を扱わない）。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.application.ports import HoldoutAccessLogStop
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.access_log import (
    AccessLogEntry,
    AccessLogEntryKind,
    serialize_entry,
)
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = ["ACCESS_LOG_FILE", "REMOTE", "GitHoldoutAccessLog"]

#: 消費の記録のファイル名（ADR-0014。snapshot のディレクトリの中）。
ACCESS_LOG_FILE: Final = "access_log.jsonl"
#: push 先のリモート（ADR-0014 の「origin への push を compare-and-set とする」）。
REMOTE: Final = "origin"


class GitHoldoutAccessLog:
    """`HoldoutAccessLog` の実装（`repo_root` の git の作業木と `snapshots_root` の消費の記録）。

    `snapshots_root` は `repo_root` の中に無ければならない（commit するファイルの相対パスを作る
    ため）。
    """

    def __init__(self, repo_root: Path, snapshots_root: Path) -> None:
        self._root = Path(repo_root).resolve()
        self._snapshots = Path(snapshots_root).resolve()
        if self._root != self._snapshots and self._root not in self._snapshots.parents:
            raise HoldoutAccessLogStop(
                "base",
                f"the snapshot root {snapshots_root} is outside the repository {repo_root}",
            )
        self._store = ParquetSnapshotStore(root=self._snapshots)
        self._base: str | None = None
        self._branch: str | None = None

    # --- git ------------------------------------------------------------------------------

    def _git(self, stage: str, *args: str) -> str:
        try:
            done = subprocess.run(  # noqa: S603 - 引数は固定の git の部分コマンドと相対パスだけ
                ["git", "-C", str(self._root), *args],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise HoldoutAccessLogStop(stage, f"git could not be started: {exc}") from exc
        if done.returncode != 0:
            raise HoldoutAccessLogStop(
                stage, f"git {args[0]} failed (exit {done.returncode}): {done.stderr.strip()}"
            )
        return done.stdout

    def _head(self, stage: str) -> str:
        return self._git(stage, "rev-parse", "--verify", "HEAD").strip()

    def _default_branch(self) -> str:
        """origin の既定ブランチの名前（origin に尋ねる。手元の `origin/HEAD` に頼らない）。"""
        out = self._git("base", "ls-remote", "--symref", REMOTE, "HEAD")
        for line in out.splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                return line[len("ref: refs/heads/") : -len("\tHEAD")]
        raise HoldoutAccessLogStop("base", f"the default branch of {REMOTE} cannot be determined")

    def _remote_head(self, stage: str, branch: str) -> str:
        out = self._git(stage, "ls-remote", REMOTE, f"refs/heads/{branch}").split()
        if not out:
            raise HoldoutAccessLogStop(stage, f"{REMOTE} has no branch {branch}")
        return out[0]

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self._root).as_posix()

    # --- HoldoutAccessLog -----------------------------------------------------------------

    def require_published_base(self, paths: Sequence[str]) -> None:
        """origin に知らない追記が無いことと、作業木が追記だけであることを確かめる。"""
        branch = self._default_branch()
        self._git("base", "fetch", "--quiet", REMOTE)
        current = self._git("base", "rev-parse", "--abbrev-ref", "HEAD").strip()
        if current != branch:
            raise HoldoutAccessLogStop(
                "base",
                f"the working tree is on {current!r}, not on the default branch {branch!r} of"
                f" {REMOTE}; switch to it first (ADR-0014 の消費遷移の直列化)",
            )
        head = self._head("base")
        remote = self._remote_head("base", branch)
        if head != remote:
            raise HoldoutAccessLogStop(
                "base",
                f"the local {branch} ({head[:12]}) is not the {branch} of {REMOTE}"
                f" ({remote[:12]}); pull (or push) first so that no unknown record exists"
                " (ADR-0014 の消費の手順 (1))",
            )
        for rel in paths:
            self._require_append_only(rel)
        self._base = head
        self._branch = branch

    def _require_append_only(self, rel: str) -> None:
        """作業木の `rel` が「HEAD の内容 + 追記」であること（HEAD に無ければ、無視されないこと）。

        HEAD にある記録が作業木から消えていれば止める（消えた記録を空の履歴と読んで、新しい
        ファイルで履歴を置き換えて commit しないため）。
        """
        path = self._root / rel
        committed = subprocess.run(  # noqa: S603
            ["git", "-C", str(self._root), "show", f"HEAD:{rel}"],  # noqa: S607
            capture_output=True,
            check=False,
        )
        in_head = committed.returncode == 0
        if in_head and not path.is_file():
            raise HoldoutAccessLogStop(
                "base",
                f"{rel} is committed but missing from the working tree; restore it before anything"
                " is appended (records are append-only)",
            )
        if not path.is_file():
            return
        if in_head:
            if not path.read_bytes().startswith(committed.stdout):
                raise HoldoutAccessLogStop(
                    "base",
                    f"{rel} in the working tree is not the committed content followed by appended"
                    " lines; it is never committed as it is (records are append-only)",
                )
        # `check-ignore` は否定の規則（`!`）に当たった道も「当たった」と返す版があるので使わない。
        ignored = self._git(
            "base", "ls-files", "--others", "--ignored", "--exclude-standard", "--", rel
        )
        if ignored.strip():
            raise HoldoutAccessLogStop(
                "base", f"{rel} is ignored by git; it cannot reach {REMOTE} (ADR-0014)"
            )

    def append_consumed(
        self,
        snapshot_id: str,
        partition_id: PartitionId,
        *,
        recorded_at: UtcTime,
        actor: str,
        purpose: str,
    ) -> str:
        """`CONSUMED` の行を 1 行追記し、読み直して最後の行が書いた行と一致することを確かめる。"""
        directory = self._snapshots / snapshot_id
        if snapshot_id.startswith("_") or not (directory / "manifest.json").is_file():
            raise HoldoutAccessLogStop(
                "append", f"{snapshot_id} is not a finalized snapshot directory"
            )
        path = directory / ACCESS_LOG_FILE
        if path.is_file():
            data = path.read_bytes()
            if data and not data.endswith(b"\n"):
                raise HoldoutAccessLogStop(
                    "append",
                    f"{self._relative(path)} ends with a fragment that is not a full line; a human"
                    " checks it before anything is appended",
                )
        entry = AccessLogEntry(
            kind=AccessLogEntryKind.CONSUMED,
            partition_id=partition_id,
            recorded_at=recorded_at,
            actor=actor,
            purpose=purpose,
        )
        payload = dict(serialize_entry(entry))
        try:
            self._store.append_access_log(snapshot_id, payload)
            lines = path.read_bytes().split(b"\n")
        except OSError as exc:
            raise HoldoutAccessLogStop(
                "append", f"writing {self._relative(path)} failed: {exc}"
            ) from exc
        last = lines[-2] if len(lines) >= 2 and lines[-1] == b"" else None
        if last is None or json.loads(last.decode("utf-8")) != payload:
            raise HoldoutAccessLogStop(
                "append", f"the line read back from {self._relative(path)} differs"
            )
        return self._relative(path)

    def publish(self, paths: Sequence[str], message: str) -> str | None:
        """`paths` だけを commit し、origin の既定ブランチへ push する（強制しない）。"""
        if self._base is None or self._branch is None:
            raise HoldoutAccessLogStop("commit", "require_published_base was not called")
        base, branch = self._base, self._branch
        if self._head("commit") != base:
            raise HoldoutAccessLogStop("commit", "HEAD moved after the check of the base")
        present = [rel for rel in paths if (self._root / rel).is_file()]
        if not present:
            return None
        self._git("commit", "add", "--", *present)
        staged = subprocess.run(  # noqa: S603
            ["git", "-C", str(self._root), "diff", "--cached", "--quiet", "--", *present],  # noqa: S607
            capture_output=True,
            check=False,
        )
        if staged.returncode == 0:
            return None  # 作業木は既に commit 済みの内容と同じ（やり直しで残りが無い）
        try:
            self._git("commit", "commit", "--quiet", "-m", message, "--", *present)
        except HoldoutAccessLogStop:
            # commit は作られていない。この回に index へ置いたファイルだけを外す。
            self._git("commit", "reset", "--quiet", base, "--", *present)
            raise
        commit = self._head("commit")
        try:
            self._git("push", "push", "--quiet", REMOTE, f"HEAD:refs/heads/{branch}")
        except HoldoutAccessLogStop as stop:
            self._undo(commit, base, present)
            raise HoldoutAccessLogStop(
                "push",
                f"{stop.detail}; the local commit was undone (the appended lines stay in the"
                " working tree). Derive the state again before retrying (ADR-0014)",
            ) from stop
        # push は成功した。ここから先の失敗では commit を取り消さない（origin に届いている）。
        try:
            reached = self._remote_head("push", branch)
        except HoldoutAccessLogStop as stop:
            raise HoldoutAccessLogStop(
                "push", f"{stop.detail} (the commit {commit} was pushed)", commit=commit
            ) from stop
        if reached != commit:
            raise HoldoutAccessLogStop(
                "push",
                f"{REMOTE}/{branch} is {reached[:12]}, not the pushed commit {commit[:12]}",
                commit=commit,
            )
        return commit

    def _undo(self, commit: str, base: str, paths: Sequence[str]) -> None:
        """push できなかった commit を取り消す（作業木は変えない）。

        先頭を `base` に戻し、この commit のファイルだけを index から外す（他に index に置かれた
        変更には触れない）。
        """
        if self._head("push") == commit:
            self._git("push", "reset", "--soft", "--quiet", base)
            self._git("push", "reset", "--quiet", base, "--", *paths)
