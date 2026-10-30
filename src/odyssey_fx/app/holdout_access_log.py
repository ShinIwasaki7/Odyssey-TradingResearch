"""消費の記録のポート `HoldoutAccessLog` の git の実装（D01 §4 v2.8、ADR-0038 第8項の 3）。

`marketdata` の消費の記録（`data/snapshots/<snapshot_id>/access_log.jsonl`。行の型は
`marketdata.domain.consumption_log`）と、git の取得・commit・push を `app` が適合させる（D09 Q11
の決定）。ADR-0014 の「消費遷移の直列化」（ADR-0038 第8項の 3 が引き継ぐ）の手順を、次の操作で
行う。

1. **origin の確かめ**: origin の既定ブランチの名前を origin に尋ね（`ls-remote --symref`）、取得
   する（`.git` の中だけを書く）。
   - `require_published_base`（後からの違反の判定の管理コマンド。PR #114）: 手元の既定ブランチの
     先頭が origin の先頭と同じことと、commit するファイルの作業木の内容が手元の先頭の内容の後ろに
     行を足しただけであること（やり直しで追記を残す方式）。
   - `require_clean_base`（最終検証の段 2 (a)。D09 §9.10.2）: 作業木が既定ブランチの上にあり、
     先頭が origin の先頭と同じで（仮置き R2-20）、消費の対象を持つ snapshot の manifest が先頭に
     あり、その消費の記録が無視されず、作業木の内容が先頭の内容と同じ（追記が無い）こと。
   - `require_logs_match_origin`（受入れの初期状態の例外 2。D09 §9.10.13 の 3、仮置き R2-16）:
     origin の先頭が手元の先頭の祖先（origin に知らない追記が無い）で、すべての確定済みの
     snapshot の消費の記録の作業木の内容が origin の既定ブランチ上の内容と同じこと。
2. **追記**: `append_row` が版 1 の消費の行（D09 §9.10.4）を 1 行追記し、読み直して一致を確かめる。
3. **公開**: `publish` が渡したファイルだけを commit し、origin の既定ブランチへ push する（強制
   しない）。push が断られたら commit を取り消す。最終検証（`restore_on_reject`）は追記した
   ファイルもコミット済みの内容に戻す（origin に届いていない行は記録ではない。仮置き R2-2）。
   push の結果が分からないとき（`confirm_unknown`）は、もう一度 origin の先頭を尋ね、消費の
   commit があれば成功、無ければ拒否と同じ扱い、尋ねられなければ `PushOutcomeUnknown`。

git は `git -C <リポジトリの根>` の子プロセスで動かす。出力は commit の識別子・終了コード・
ファイルの相対パスだけを人間に渡す（価格を扱わない）。
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from odyssey_fx.evaluation.application.ports import HoldoutAccessLogStop, PushOutcomeUnknown
from odyssey_fx.marketdata.domain.consumption_log import (
    ConsumptionRow,
    consumption_line_bytes,
    consumption_row_payload,
)

__all__ = ["ACCESS_LOG_FILE", "REMOTE", "GitHoldoutAccessLog"]

#: 消費の記録のファイル名（ADR-0014。snapshot のディレクトリの中）。
ACCESS_LOG_FILE: Final = "access_log.jsonl"
#: snapshot の manifest のファイル名（D03 §3.7）。
MANIFEST_FILE: Final = "manifest.json"
#: `git check-ignore -v` の 1 行（`<規則の置き場>:<行>:<規則>\t<道>`）。
_CHECK_IGNORE_LINE: Final = re.compile(r"^(?P<source>.*?):(?P<line>\d+):(?P<pattern>[^\t]*)\t")
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
        self._base: str | None = None
        self._branch: str | None = None
        self._remote: str | None = None

    # --- git ------------------------------------------------------------------------------

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - 引数は固定の git の部分コマンドと相対パスだけ
            ["git", "-C", str(self._root), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_bytes(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(  # noqa: S603 - 引数は固定の git の部分コマンドと相対パスだけ
            ["git", "-C", str(self._root), *args],  # noqa: S607
            capture_output=True,
            check=False,
        )

    def _git(self, stage: str, *args: str) -> str:
        try:
            done = self._run(*args)
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

    def _in_tree(self, revision: str, rel: str) -> bool:
        return bool(self._git("base", "ls-tree", "--name-only", revision, "--", rel).strip())

    def _blob(self, revision: str, rel: str) -> bytes | None:
        """`revision` の `rel` の内容（無ければ `None`。git の失敗を「無い」と読まない）。"""
        if not self._in_tree(revision, rel):
            return None
        done = self._run_bytes("show", f"{revision}:{rel}")
        if done.returncode != 0:
            raise HoldoutAccessLogStop("base", f"{rel} cannot be read from {revision[:12]}")
        return done.stdout

    def _worktree(self, rel: str) -> bytes | None:
        path = self._root / rel
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            raise HoldoutAccessLogStop("base", f"{rel} cannot be read: {exc}") from exc

    def _fetched(self) -> tuple[str, str]:
        """既定ブランチの名前を尋ね、取得し、origin の先頭を返す（`.git` の中だけを書く）。"""
        branch = self._default_branch()
        self._git("base", "fetch", "--quiet", REMOTE)
        remote = self._remote_head("base", branch)
        self._branch = branch
        self._remote = remote
        return branch, remote

    # --- 読み取り（最終検証の段 1・段 2、受入れの例外 2）----------------------------------

    def snapshots_relative(self) -> str:
        """snapshot の基点のリポジトリの根からの相対パス。"""
        return self._relative(self._snapshots)

    def fetch_origin(self) -> tuple[str, str]:
        """origin の既定ブランチの名前と先頭（取得の後）。段 1 の最初に呼ぶ（D09 §9.10.2）。"""
        return self._fetched()

    def read_at_origin(self, rel: str) -> bytes | None:
        """origin の既定ブランチの先頭（`fetch_origin` で尋ねたもの）の `rel` の内容。"""
        if self._remote is None:
            raise HoldoutAccessLogStop("base", "fetch_origin was not called")
        return self._blob(self._remote, rel)

    def list_at_origin(self, prefix: str) -> tuple[str, ...]:
        """origin の既定ブランチの先頭の `prefix` の下のファイルの相対パス（追跡されたもの）。"""
        if self._remote is None:
            raise HoldoutAccessLogStop("base", "fetch_origin was not called")
        out = self._git("base", "ls-tree", "-r", "--name-only", self._remote, "--", prefix)
        return tuple(line for line in out.splitlines() if line)

    def has_commit_at_origin(self, commit: str) -> bool | None:
        """`commit` が origin の既定ブランチに含まれるか（尋ねられなければ `None`）。

        先頭がちょうど `commit` なら含まれる。先に進んでいれば取得して祖先かを見る（push の後に
        別の追記が積まれていても、届いた消費の commit を取り消さない）。
        """
        branch = self._branch or self._default_branch()
        try:
            head = self._remote_head("push", branch)
            if head == commit:
                return True
            self._git("push", "fetch", "--quiet", REMOTE)
        except HoldoutAccessLogStop:
            return None
        done = self._run("merge-base", "--is-ancestor", commit, head)
        if done.returncode == 0:
            return True
        if done.returncode == 1:
            return False
        return None

    # --- origin の確かめ -------------------------------------------------------------------

    def require_published_base(self, paths: Sequence[str]) -> None:
        """origin に知らない追記が無いことと、作業木が追記だけであることを確かめる（PR #114）。"""
        branch, remote = self._fetched()
        self._require_on_branch(branch)
        head = self._head("base")
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

    def require_clean_base(self, logs: Sequence[str], manifests: Sequence[str]) -> str:
        """最終検証の段 2 (a)（D09 §9.10.2、仮置き R2-20）。成功したら先頭の commit を返す。

        `logs` は消費の対象を持つ snapshot の消費の記録、`manifests` はその manifest（リポジトリの
        根からの相対パス）。どれかが違えば `HoldoutAccessLogStop`（何も書かない）。
        """
        branch = self._branch
        remote = self._remote
        if branch is None or remote is None:
            branch, remote = self._fetched()
        self._require_on_branch(branch)
        head = self._head("base")
        if head != remote:
            raise HoldoutAccessLogStop(
                "base",
                f"the local {branch} ({head[:12]}) is not the head of {REMOTE}/{branch}"
                f" ({remote[:12]}); the consumption commit must sit on the head of {REMOTE}"
                " (D09 §9.10.2 の段 2 (a)・仮置き R2-20)",
            )
        for rel in manifests:
            committed_manifest = self._blob(head, rel)
            if committed_manifest is None:
                raise HoldoutAccessLogStop(
                    "base",
                    f"{rel} is not committed on {REMOTE}/{branch}; a snapshot whose manifest is"
                    " not on origin is never consumed (D09 §9.10.2 の段 2 (a))",
                )
            if self._worktree(rel) != committed_manifest:
                raise HoldoutAccessLogStop(
                    "base",
                    f"{rel} in the working tree differs from {REMOTE}/{branch}"
                    " (D09 §9.10.2 の段 2 (a))",
                )
        for rel in logs:
            self._require_not_ignored(rel)
            committed = self._blob(head, rel)
            current = self._worktree(rel)
            if committed != current:
                raise HoldoutAccessLogStop(
                    "base",
                    f"{rel} in the working tree differs from the committed content (an"
                    " uncommitted append, or a missing file); restore it before the final"
                    " validation (D09 §9.10.2 の段 2 (a))",
                )
        self._base = head
        return head

    def require_logs_match_origin(self) -> None:
        """受入れの初期状態の例外 2 の照合（D09 §9.10.13 の 3、ADR-0038 第3項の 3）。

        origin の先頭が手元の先頭の祖先で（origin に知らない追記が無い）、確定済みの snapshot の
        消費の記録（作業木と origin の既定ブランチの和）の内容が両方で同じこと。違えば・確かめ
        られなければ `HoldoutAccessLogStop`（受入れを始めない）。
        """
        branch, remote = self._fetched()
        head = self._head("base")
        done = self._run("merge-base", "--is-ancestor", remote, head)
        if done.returncode != 0:
            raise HoldoutAccessLogStop(
                "base",
                f"{REMOTE}/{branch} ({remote[:12]}) is not contained in the local HEAD"
                f" ({head[:12]}); pull first so that no consumption record on origin is unknown"
                " (ADR-0038 第3項の 3)",
            )
        prefix = self.snapshots_relative()
        logs = {
            rel
            for rel in self.list_at_origin(prefix)
            if rel.endswith(f"/{ACCESS_LOG_FILE}") and not _pending(rel, prefix)
        }
        if self._snapshots.is_dir():
            for directory in self._snapshots.iterdir():
                if directory.is_dir() and not directory.name.startswith("_"):
                    candidate = directory / ACCESS_LOG_FILE
                    if candidate.is_file():
                        logs.add(self._relative(candidate))
        for rel in sorted(logs):
            if self._blob(remote, rel) != self._worktree(rel):
                raise HoldoutAccessLogStop(
                    "base",
                    f"{rel} in the working tree differs from {REMOTE}/{branch}; the states of the"
                    " existing sealed partitions cannot be confirmed (ADR-0038 第3項の 3)",
                )

    def _require_on_branch(self, branch: str) -> None:
        current = self._git("base", "rev-parse", "--abbrev-ref", "HEAD").strip()
        if current != branch:
            raise HoldoutAccessLogStop(
                "base",
                f"the working tree is on {current!r}, not on the default branch {branch!r} of"
                f" {REMOTE}; switch to it first (ADR-0014 の消費遷移の直列化)",
            )

    def _require_append_only(self, rel: str) -> None:
        """作業木の `rel` が「HEAD の内容 + 追記」であること（HEAD に無ければ、無視されないこと）。

        HEAD にある記録が作業木から消えていれば止める（消えた記録を空の履歴と読んで、新しい
        ファイルで履歴を置き換えて commit しないため）。
        """
        self._require_not_ignored(rel)
        committed = self._blob("HEAD", rel)
        current = self._worktree(rel)
        if committed is not None and current is None:
            raise HoldoutAccessLogStop(
                "base",
                f"{rel} is committed but missing from the working tree; restore it before anything"
                " is appended (records are append-only)",
            )
        if committed is not None and current is not None and not current.startswith(committed):
            raise HoldoutAccessLogStop(
                "base",
                f"{rel} in the working tree is not the committed content followed by appended"
                " lines; it is never committed as it is (records are append-only)",
            )

    def _require_not_ignored(self, rel: str) -> None:
        """`rel` が git の無視の規則に当たらないこと（まだ無いファイルにも当てる）。

        `check-ignore` は否定の規則（`!`）に当たった道も終了コード 0 で返すので、`-v` で当たった
        規則を読み、最後に当たった規則が否定でなければ無視されるとみなす。
        """
        done = self._run("check-ignore", "-v", "--no-index", "--", rel)
        if done.returncode == 1:
            return  # どの規則にも当たらない
        match = _CHECK_IGNORE_LINE.match(done.stdout)
        if done.returncode != 0 or match is None:
            raise HoldoutAccessLogStop(
                "base", f"whether git ignores {rel} cannot be determined (exit {done.returncode})"
            )
        if not match.group("pattern").startswith("!"):
            raise HoldoutAccessLogStop(
                "base", f"{rel} is ignored by git; it cannot reach {REMOTE} (ADR-0014)"
            )

    # --- 追記 -------------------------------------------------------------------------------

    def append_row(self, snapshot_id: str, row: ConsumptionRow) -> str:
        """版 1 の消費の行を 1 行追記し、読み直して最後の行が書いた行と一致することを確かめる。"""
        directory = self._snapshots / snapshot_id
        if snapshot_id.startswith("_") or not (directory / MANIFEST_FILE).is_file():
            raise HoldoutAccessLogStop(
                "append", f"{snapshot_id} is not a finalized snapshot directory"
            )
        path = directory / ACCESS_LOG_FILE
        line = consumption_line_bytes(row)
        try:
            if path.is_file():
                data = path.read_bytes()
                if data and not data.endswith(b"\n"):
                    raise HoldoutAccessLogStop(
                        "append",
                        f"{self._relative(path)} ends with a fragment that is not a full line; a"
                        " human checks it before anything is appended",
                    )
            with path.open("ab") as handle:
                handle.write(line)
            lines = path.read_bytes().split(b"\n")
        except OSError as exc:
            raise HoldoutAccessLogStop(
                "append", f"writing {self._relative(path)} failed: {exc}"
            ) from exc
        last = lines[-2] if len(lines) >= 2 and lines[-1] == b"" else None
        if last is None or json.loads(last.decode("utf-8")) != consumption_row_payload(row):
            raise HoldoutAccessLogStop(
                "append", f"the line read back from {self._relative(path)} differs"
            )
        return self._relative(path)

    def restore(self, paths: Sequence[str]) -> None:
        """`paths` の作業木をコミット済みの内容に戻す（先頭に無いファイルは消す。仮置き R2-2）。"""
        base = self._base or self._head("restore")
        for rel in paths:
            committed = self._blob(base, rel)
            target = self._root / rel
            try:
                if committed is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(committed)
            except OSError as exc:
                raise HoldoutAccessLogStop("restore", f"{rel} cannot be restored: {exc}") from exc

    # --- 公開 -------------------------------------------------------------------------------

    def _push(self, branch: str) -> None:
        self._git("push", "push", "--quiet", REMOTE, f"HEAD:refs/heads/{branch}")

    def publish(
        self,
        paths: Sequence[str],
        message: str,
        *,
        restore_on_reject: bool = False,
        confirm_unknown: bool = False,
    ) -> str | None:
        """`paths` だけを commit し、origin の既定ブランチへ push する（強制しない）。"""
        if self._base is None or self._branch is None:
            raise HoldoutAccessLogStop("commit", "the base was not checked")
        base, branch = self._base, self._branch
        if self._head("commit") != base:
            raise HoldoutAccessLogStop("commit", "HEAD moved after the check of the base")
        present = [rel for rel in paths if (self._root / rel).is_file()]
        if not present:
            return None
        self._git("commit", "add", "--", *present)
        staged = self._run("diff", "--cached", "--quiet", "--", *present)
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
            self._push(branch)
        except HoldoutAccessLogStop as stop:
            if confirm_unknown:
                reached = self.has_commit_at_origin(commit)
                if reached is None:
                    raise PushOutcomeUnknown(
                        "push",
                        f"{stop.detail}; {REMOTE} cannot be asked whether the commit"
                        f" {commit[:12]} arrived. The lock and the local commit stay; a human"
                        " checks origin (D09 §9.10.2 の段 4)",
                        commit=commit,
                    ) from stop
                if reached:
                    return commit
            self._undo(commit, base, present)
            if restore_on_reject:
                self.restore(present)
                kept = "the appended lines were restored to the committed content"
            else:
                kept = "the appended lines stay in the working tree"
            raise HoldoutAccessLogStop(
                "push",
                f"{stop.detail}; the local commit was undone ({kept}). Derive the state again"
                " before retrying (ADR-0014)",
            ) from stop
        # push は成功した。ここから先の失敗では commit を取り消さない（origin に届いている）。
        # 確かめは「origin の先頭が push した commit を含む（祖先）」（D09 仮置き R4b-14。push の
        # 結果が分からないときの判定と同じ基準。間に別の push が積まれても人間の回復に回さない）。
        reached = self.has_commit_at_origin(commit)
        if reached is None:
            raise HoldoutAccessLogStop(
                "push",
                f"{REMOTE} cannot be asked whether it holds the pushed commit {commit[:12]}",
                commit=commit,
            )
        if not reached:
            raise HoldoutAccessLogStop(
                "push",
                f"{REMOTE}/{branch} does not contain the pushed commit {commit[:12]}",
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


def _pending(rel: str, prefix: str) -> bool:
    """`rel` が暫定・退避（`_` で始まるディレクトリ）の下か。"""
    inside = rel[len(prefix) :].lstrip("/") if prefix else rel
    return inside.startswith("_")
