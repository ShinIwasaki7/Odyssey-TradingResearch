"""実行器の口の実物（git・`gh`・プロセス・計算機・評価の基盤のコマンド）。

- git: 定型 PR のコミットは作業木に触れずに一時の索引で作る（`commit_files`。本 PR の仮置き）。
  枝を書くのは `runner/`・`candidate-fix/` だけ。管理者の権限で検査を飛ばす merge はしない。
- `gh`: PR の作成・様子・自動 merge の指定（merge のコミットの方式）・issue の問いとコメント。
- プロセス: 切り離した処理は新しいセッション（プロセスグループの先頭）で起動する。
- 作業役と関門の機械の項目は PR 8b が入れる。本 PR の実物は「まだ無い」で、実行器は道具の失敗と
  して止まる。

どの実物も成績の数値を読まない（`console.log`・評価の結果・指標・取引表を開かない）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from tools.ops.hypothesis_cards.generate import PreregChecks
from tools.ops.hypothesis_cards.queue import first_seen_times
from tools.ops.hypothesis_cards.repo import RepoView, ToolFailure
from tools.ops.hypothesis_cards.screening import Verification
from tools.ops.research_runner.detached import command_line
from tools.ops.research_runner.ports import (
    Binding,
    Checks,
    Comment,
    GateResult,
    MainCheckout,
    PrepareFailed,
    PrState,
    PullStatus,
    WorkerPoll,
    WorkerRequest,
    WorkerStatus,
    WorkerUnavailable,
)
from tools.ops.research_runner.resources import free_memory_from_vm_stat
from tools.ops.runner_ci.model import GitSnapshot

__all__ = [
    "GhGitHub",
    "LocalEvaluation",
    "LocalGit",
    "LocalProcesses",
    "LocalSystem",
    "NotYetGate",
    "NotYetWorkers",
    "SystemClock",
]

_OWNED_BRANCHES: Final = ("runner/", "candidate-fix/")
_PR_URL: Final = re.compile(r"/pull/([0-9]+)\s*$")
_ISSUE_URL: Final = re.compile(r"/issues/([0-9]+)\s*$")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC).replace(microsecond=0)


def _run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - 固定の部分命令と実行器が決めた引数
        list(args),
        cwd=cwd,
        env=None if env is None else dict(env),
        input=stdin,
        capture_output=True,
        check=False,
    )


def _ok(completed: subprocess.CompletedProcess[bytes], what: str) -> str:
    if completed.returncode != 0:
        raise ToolFailure(f"{what} が失敗した: {completed.stderr.decode(errors='replace').strip()}")
    return completed.stdout.decode("utf-8").strip()


@dataclass(frozen=True, slots=True)
class LocalGit:
    """`repo` は人間の主の作業木、`exec_worktree` は実行用の作業木（枝 `runner/exec`）。"""

    repo: Path
    exec_worktree: Path
    remote: str = "origin"
    branch: str = "main"

    def _git(self, *args: str, cwd: Path | None = None, **kw: Any) -> str:
        return _ok(_run(["git", "-C", str(cwd or self.repo), *args], **kw), f"git {args[0]}")

    def fetch(self) -> None:
        self._git("fetch", "-q", "--prune", self.remote)

    def origin_main(self) -> str:
        return self._git("rev-parse", f"{self.remote}/{self.branch}^{{commit}}")

    def snapshot(self, rev: str) -> RepoView:
        return GitSnapshot(self.repo, rev)

    def commit_files(self, *, base: str, files: Mapping[str, bytes | None], message: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            self._git("read-tree", base, env=env)
            for path, data in sorted(files.items()):
                if data is None:
                    self._git("update-index", "--force-remove", "--", path, env=env)
                    continue
                blob = self._git("hash-object", "-w", "--stdin", stdin=data)
                self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = self._git("write-tree", env=env)
            return self._git("commit-tree", tree, "-p", base, "-m", message)

    def push_branch(self, commit: str, branch: str) -> None:
        if not branch.startswith(_OWNED_BRANCHES):
            raise ToolFailure(f"実行器が書かない枝: {branch}")
        self._git("push", "-q", "--force", self.remote, f"{commit}:refs/heads/{branch}")

    def first_seen_times(self, ref: str, card_ids: Sequence[str]) -> dict[str, int]:
        return first_seen_times(self.repo, ref, card_ids)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        completed = _run(
            ["git", "-C", str(self.repo), "merge-base", "--is-ancestor", ancestor, descendant]
        )
        return completed.returncode == 0

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        out = self._git("diff", "--name-only", "--no-renames", base, head)
        return tuple(line for line in out.splitlines() if line)

    def ensure_exec_worktree(self) -> None:
        if (self.exec_worktree / ".git").exists():
            return
        self.exec_worktree.parent.mkdir(parents=True, exist_ok=True)
        self._git(
            "worktree",
            "add",
            "-q",
            "-B",
            "runner/exec",
            str(self.exec_worktree),
            f"{self.remote}/{self.branch}",
        )

    def exec_head(self) -> str:
        return self._git("rev-parse", "HEAD", cwd=self.exec_worktree)

    def exec_fast_forward(self, target: str) -> None:
        self._git("merge", "-q", "--ff-only", target, cwd=self.exec_worktree)

    def exec_dirty_paths(self) -> tuple[str, ...]:
        completed = _run(
            [
                "git",
                "-C",
                str(self.exec_worktree),
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=no",
            ]
        )
        _ok(completed, "git status")
        # `-z` の各項目は「XY パス」（先頭の空白を落とさないよう、出力を切り詰めずに読む）。
        entries = completed.stdout.decode("utf-8").split("\0")
        return tuple(sorted(item[3:] for item in entries if len(item) > 3))

    def exec_commit(self, paths: Sequence[str], message: str) -> str:
        self._git("add", "--", *paths, cwd=self.exec_worktree)
        self._git("commit", "-q", "-m", message, "--", *paths, cwd=self.exec_worktree)
        return self.exec_head()

    def exec_undo_commit(self) -> None:
        self._git("reset", "-q", "--soft", "HEAD~1", cwd=self.exec_worktree)
        self._git("reset", "-q", cwd=self.exec_worktree)

    def exec_push(self) -> None:
        self._git(
            "push",
            "-q",
            "--force",
            self.remote,
            "HEAD:refs/heads/runner/exec",
            cwd=self.exec_worktree,
        )

    def exec_untracked(self, prefix: str) -> tuple[str, ...]:
        out = self._git(
            "ls-files", "--others", "--exclude-standard", "--", prefix, cwd=self.exec_worktree
        )
        return tuple(sorted(line for line in out.splitlines() if line))

    def main_checkout(self) -> MainCheckout:
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        status = self._git("status", "--porcelain", "--untracked-files=no")
        return MainCheckout(
            branch=None if branch == "HEAD" else branch,
            head=self._git("rev-parse", "HEAD"),
            clean=not status,
        )

    def main_fast_forward(self, target: str) -> None:
        self._git("merge", "-q", "--ff-only", target)

    def main_file_matches(self, path: str, rev: str) -> bool:
        local = self.repo / path
        completed = _run(["git", "-C", str(self.repo), "cat-file", "blob", f"{rev}:{path}"])
        return (
            local.is_file() and completed.returncode == 0 and local.read_bytes() == completed.stdout
        )

    def main_restore(self, path: str) -> None:
        self._git("checkout", "-q", "--", path)


@dataclass(frozen=True, slots=True)
class GhGitHub:
    """`gh` の呼び出し（リポジトリは `repo` の origin）。"""

    repo: Path

    def _gh(self, *args: str, stdin: bytes | None = None) -> str:
        return _ok(_run(["gh", *args], cwd=self.repo, stdin=stdin), f"gh {' '.join(args[:2])}")

    def create_pr(self, *, branch: str, title: str, body: str) -> int:
        existing = json.loads(
            self._gh("pr", "list", "--head", branch, "--state", "open", "--json", "number")
        )
        if existing:
            # 前の 1 歩が PR を作った後に止まった: 同じ枝の開いた PR を使う（本文は作り直さない）。
            return int(existing[0]["number"])
        out = self._gh(
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            branch,
            "--title",
            title,
            "--body-file",
            "-",
            stdin=body.encode("utf-8"),
        )
        matched = _PR_URL.search(out)
        if matched is None:
            raise ToolFailure("gh pr create の出力から PR の番号を読めない")
        return int(matched.group(1))

    def pr_status(self, number: int) -> PullStatus:
        data = json.loads(
            self._gh(
                "pr", "view", str(number), "--json", "state,mergeCommit,closedAt,statusCheckRollup"
            )
        )
        state = PrState(str(data.get("state", "OPEN")))
        rollup = data.get("statusCheckRollup") or []
        checks = Checks.PASS if rollup else Checks.PENDING
        for item in rollup:
            conclusion = str(item.get("conclusion") or item.get("state") or "").upper()
            status = str(item.get("status") or "COMPLETED").upper()
            if conclusion in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
                checks = Checks.FAIL
                break
            if status != "COMPLETED" and conclusion not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                checks = Checks.PENDING
        merge = data.get("mergeCommit") or {}
        closed = data.get("closedAt")
        return PullStatus(
            state=state,
            checks=checks,
            merge_commit=merge.get("oid") if isinstance(merge, dict) else None,
            closed_at=str(closed).replace(".000", "") if closed else None,
        )

    def enable_auto_merge(self, number: int) -> None:
        self._gh("pr", "merge", str(number), "--auto", "--merge")

    def rerun_checks(self, number: int) -> None:
        branch = json.loads(self._gh("pr", "view", str(number), "--json", "headRefName"))[
            "headRefName"
        ]
        runs = json.loads(
            self._gh(
                "run", "list", "--branch", branch, "--json", "databaseId,conclusion", "-L", "5"
            )
        )
        for item in runs:
            if item.get("conclusion") == "failure":
                self._gh("run", "rerun", str(item["databaseId"]), "--failed")

    def close_pr(self, number: int) -> None:
        self._gh("pr", "close", str(number))

    def create_issue(self, *, title: str, body: str, label: str) -> int:
        completed = _run(
            ["gh", "issue", "create", "--title", title, "--body-file", "-", "--label", label],
            cwd=self.repo,
            stdin=body.encode("utf-8"),
        )
        if completed.returncode != 0:
            # ラベルがまだ無い（人間が作る。PR 本文の「人間が行うこと」）ときはラベルなしで作る。
            completed = _run(
                ["gh", "issue", "create", "--title", title, "--body-file", "-"],
                cwd=self.repo,
                stdin=body.encode("utf-8"),
            )
        out = _ok(completed, "gh issue create")
        matched = _ISSUE_URL.search(out)
        if matched is None:
            raise ToolFailure("gh issue create の出力から issue の番号を読めない")
        return int(matched.group(1))

    def comment(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--body-file", "-", stdin=body.encode("utf-8"))

    def comments(self, number: int) -> tuple[Comment, ...]:
        data = json.loads(
            self._gh("api", "--paginate", f"repos/{{owner}}/{{repo}}/issues/{number}/comments")
        )
        return tuple(
            Comment(str(item["id"]), str(item.get("body") or ""), str(item["created_at"]))
            for item in data
        )

    def close_issue(self, number: int) -> None:
        self._gh("issue", "close", str(number))


class LocalProcesses:
    def alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        try:
            finished, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return True
        return finished == 0

    def is_group_leader(self, pid: int) -> bool:
        try:
            return os.getpgid(pid) == pid
        except ProcessLookupError:
            return False

    def signal_group(self, group: int, signal: int) -> None:
        try:
            os.killpg(group, signal)
        except ProcessLookupError:
            return

    def spawn(self, command: Sequence[str], *, cwd: Path, state_dir: Path) -> int:
        state_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(  # noqa: S603 - 実行器が組み立てた固定のコマンド
            command_line(state_dir, cwd, command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return process.pid


class LocalSystem:
    def boot_id(self) -> str:
        completed = _run(["sysctl", "-n", "kern.boottime"])
        return completed.stdout.decode().strip() if completed.returncode == 0 else ""

    def free_disk_bytes(self, path: Path) -> int:
        return shutil.disk_usage(path).free

    def free_memory_bytes(self) -> int:
        completed = _run(["vm_stat"])
        return (
            free_memory_from_vm_stat(completed.stdout.decode()) if completed.returncode == 0 else 0
        )


@dataclass(frozen=True, slots=True)
class LocalEvaluation:
    """評価の基盤のコマンドと成果物の識別子の読み取り（中身は変えない。D25 §2.2）。"""

    snapshots: Path
    odyssey: tuple[str, ...] = ("uv", "run", "--frozen", "odyssey-fx")

    def ledger_check(self, worktree: Path, out: Path) -> int:
        # 出力は版ごとの一覧（判定の値を含みうる）なので読まずに捨て、終了コードだけを見る。
        completed = subprocess.run(  # noqa: S603 - 固定のコマンド
            [
                *self.odyssey,
                "experiment",
                "ledger",
                "--repo-root",
                str(worktree),
                "--out",
                str(out),
            ],
            cwd=worktree,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode

    def prepare(self, worktree: Path, verification: Verification) -> PreregChecks:
        from odyssey_fx.app.composition import prepare_search
        from odyssey_fx.app.config.loader import ConfigError
        from odyssey_fx.common.errors import KernelValueError

        loaded = verification.loaded
        if loaded is None:
            raise ToolFailure("読込の結果が無い（準備の確かめを当てられない）")
        started = time.monotonic()
        try:
            prepare_search(loaded=loaded, snapshots_root=self.snapshots, repo_root=worktree)
        except (ConfigError, KernelValueError) as exc:
            raise PrepareFailed(type(exc).__name__) from exc
        except OSError as exc:
            raise ToolFailure(f"準備の確かめで snapshot を開けない: {type(exc).__name__}") from exc
        seconds = int(time.monotonic() - started)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS はバイト、Linux はキロバイト。
        max_rss = rss if sys.platform == "darwin" else rss * 1024
        return PreregChecks(
            trial_count=verification.trial_count,
            rejected_count=verification.rejected_count,
            seconds=seconds,
            max_rss_bytes=max_rss,
        )

    def binding(self, version_dir: Path) -> Binding | None:
        from odyssey_fx.common.errors import KernelValueError
        from odyssey_fx.evaluation.adapters.fs_store import read_ledger_binding

        try:
            found = read_ledger_binding(version_dir)
        except (OSError, ValueError, KernelValueError):
            return None
        if found is None:
            return None
        return Binding(experiment_id=found.experiment_id.hex, execution=found.execution)

    def run_ids(self, version_dir: Path) -> tuple[str, ...]:
        import polars as pl

        found: set[str] = set()
        for table in sorted((version_dir / "search").rglob("trial_units.parquet")):
            try:
                column = pl.read_parquet(table, columns=["run_id"])["run_id"]
            except (OSError, pl.exceptions.PolarsError):
                continue
            found |= {str(value) for value in column.to_list() if value}
        return tuple(sorted(item for item in found if re.fullmatch(r"[0-9a-f]{64}", item)))

    def fix_available(self) -> bool:
        return importlib.util.find_spec("odyssey_fx.app.candidate_fixation") is not None

    def declaration_required(self) -> bool:
        """最終検証のコマンドがまだ引数 `--declaration` を持つか（申告の撤去の PR 4b の前）。"""
        spec = importlib.util.find_spec("odyssey_fx.app.cli.main")
        if spec is None or spec.origin is None:
            return True
        return '"--declaration"' in Path(spec.origin).read_text(encoding="utf-8")


class NotYetWorkers:
    """作業役の実物は PR 8b で入る（本 PR では起動できない）。"""

    def launch(self, request: WorkerRequest) -> None:
        raise WorkerUnavailable(f"作業役 {request.role} の定義は PR 8b で入る")

    def poll(self, request: WorkerRequest) -> WorkerPoll:
        return WorkerPoll(WorkerStatus.LOST)


class NotYetGate:
    """関門の機械の項目（D25 §5.5）の実物は PR 8b で入る。"""

    def check(self, card_id: str, versions: Sequence[int]) -> GateResult:
        raise ToolFailure("関門の機械の項目は PR 8b で入る")
