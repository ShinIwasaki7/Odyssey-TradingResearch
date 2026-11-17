"""実行器（`tools.ops.research_runner`）の試験の人工の世界。

本物の run・GitHub・origin・作業役には触れない。git の履歴と GitHub の PR・issue を辞書で持つ偽物
（`World`）と、偽のプロセス・包み・評価の基盤・作業役を組み、`Runner` に渡す。

- 履歴の各コミットは、作業場（`tests.fixtures.hypothesis_cards.workspace` に試験用の第 5 版を置いた
  もの）の上に重ねるファイルの辞書を持つ。台帳は空のファイルから始める（人工の行だけを足す）。
- 偽の GitHub は、実行器が作った PR に CI の段（`tools.ops.runner_ci.cli.run_checks`）を本物の
  まま当て、全部の検査が通れば必須の検査が合格したとする。自動 merge が指定され検査が合格した
  PR は、作った 1 歩の後の様子の読みで merge のコミットの方式で main に入る（同じ 1 歩の中では
  CI の途中）。
- 台帳の行・カード・記録はすべて人工の値（成績・判定の値・価格を読まない・書かない）。
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.fixtures.hypothesis_cards import install_policy_v5, workspace
from tests.fixtures.runner_ci import trial_ledger
from tools.ops.hypothesis_cards.generate import PreregChecks
from tools.ops.hypothesis_cards.repo import DirView, OverlayView, RepoView
from tools.ops.hypothesis_cards.screening import Verification
from tools.ops.research_runner.ports import (
    Binding,
    Checks,
    Comment,
    GateInputs,
    GateResult,
    MainCheckout,
    PrState,
    PullStatus,
    WorkerOutput,
    WorkerPoll,
    WorkerRequest,
    WorkerStatus,
    WorkerUnavailable,
    WrapperContract,
)
from tools.ops.research_runner.runner import Paths, Ports, Runner
from tools.ops.runner_ci.cli import run_checks
from tools.ops.runner_ci.model import Change, ChangeStatus, CheckInput, PullRequest, Status
from tools.ops.runner_ci.regen import REGENERATOR

TRIAL = "research/trial_ledger.jsonl"
REVIEW = "research/adoption_reviews.jsonl"
FINAL = "research/final_validations.jsonl"
LEDGERS = (TRIAL, REVIEW, FINAL)
START = datetime(2026, 11, 24, 1, 0, tzinfo=UTC)  # 月曜 10:00 JST


@dataclass
class Commit:
    identifier: str
    parents: tuple[str, ...]
    files: dict[str, bytes]


@dataclass
class FakePr:
    number: int
    branch: str
    title: str
    body: str
    commit: str
    base: str
    state: PrState = PrState.OPEN
    auto: bool = False
    ci_ok: bool = True
    ci_messages: tuple[str, ...] = ()
    forced_fail: int = 0
    reruns: int = 0
    merge_commit: str | None = None
    #: 作った時刻（同じ 1 歩の中では CI が走っている途中とする）。
    created_at: datetime | None = None


@dataclass
class FakeIssue:
    number: int
    title: str
    body: str
    label: str
    comments: list[Comment] = field(default_factory=list)
    closed: bool = False


@dataclass
class FakeClock:
    moment: datetime = START

    def now(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


def _changes(base: Mapping[str, bytes], head: Mapping[str, bytes]) -> tuple[Change, ...]:
    found = []
    for path in sorted(set(base) | set(head)):
        if path not in head:
            found.append(Change(path, ChangeStatus.DELETED))
        elif path not in base:
            found.append(Change(path, ChangeStatus.ADDED))
        elif base[path] != head[path]:
            found.append(Change(path, ChangeStatus.MODIFIED))
    return tuple(found)


class World:
    """偽の origin・GitHub・主の作業木・実行用の作業木。"""

    def __init__(self, root: Path, clock: FakeClock) -> None:
        self.clock = clock
        self.workspace = workspace(root / "workspace")
        install_policy_v5(self.workspace)
        self.base = DirView(self.workspace)
        self.repo = root / "repo"
        self.exec_dir = root / "repo" / ".claude" / "worktrees" / "runner-exec"
        self.repo.mkdir(parents=True)
        self.exec_dir.mkdir(parents=True)
        self.counter = itertools.count(1)
        self.numbers = itertools.count(100)
        self.commits: dict[str, Commit] = {}
        self.main = self._commit((), {path: b"" for path in LEDGERS})
        self.branches: dict[str, str] = {}
        self.prs: dict[int, FakePr] = {}
        self.issues: dict[int, FakeIssue] = {}
        self.first_seen: dict[str, int] = {}
        self.exec_head = self.main
        self.main_head = self.main
        self.main_clean = True
        for path in LEDGERS:
            for where in (self.exec_dir, self.repo):
                (where / path).parent.mkdir(parents=True, exist_ok=True)
                (where / path).write_bytes(b"")

    # --- 履歴 ---

    def _commit(self, parents: tuple[str, ...], files: Mapping[str, bytes]) -> str:
        identifier = f"{next(self.counter):040x}"
        self.commits[identifier] = Commit(identifier, parents, dict(files))
        return identifier

    def files(self, rev: str) -> dict[str, bytes]:
        return self.commits[rev].files

    def view(self, rev: str) -> RepoView:
        return OverlayView(self.base, self.files(rev))

    def ancestors(self, rev: str) -> set[str]:
        seen: set[str] = set()
        stack = [rev]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.commits[current].parents)
        return seen

    def put_on_main(self, files: Mapping[str, bytes | None]) -> str:
        """人間の PR を main に入れる（カードを置くなど）。"""
        merged = dict(self.files(self.main))
        for path, data in files.items():
            if data is None:
                merged.pop(path, None)
            else:
                if path not in merged:
                    self.first_seen.setdefault(path, int(self.clock.now().timestamp()))
                merged[path] = data
        self.main = self._commit((self.main,), merged)
        return self.main

    def derive(self, base: str, files: Mapping[str, bytes | None]) -> str:
        merged = dict(self.files(base))
        for path, data in files.items():
            if data is None:
                merged.pop(path, None)
            else:
                merged[path] = data
        return self._commit((base,), merged)

    # --- PR ---

    def open_pr(self, branch: str, title: str, body: str) -> int:
        number = next(self.numbers)
        commit = self.branches[branch]
        base = self.main
        pr = FakePr(number, branch, title, body, commit, base, created_at=self.clock.now())
        self.prs[number] = pr
        return number

    def merge_base(self, first: str, second: str) -> str:
        common = self.ancestors(first) & self.ancestors(second)
        return max(common)  # 識別子は作った順の番号

    def _applied(self, pr: FakePr) -> dict[str, bytes]:
        """PR の変更（合流の基点からの差分）を main の先頭に当てた中身（GitHub の試しの merge）。"""
        base_files = self.files(self.merge_base(self.main, pr.commit))
        head_files = self.files(pr.commit)
        merged = dict(self.files(self.main))
        for change in _changes(base_files, head_files):
            if change.status is ChangeStatus.DELETED:
                merged.pop(change.path, None)
            else:
                merged[change.path] = head_files[change.path]
        return merged

    def ci(self, pr: FakePr) -> None:
        """CI の段の検査 1〜8 を本物のまま当てる（基点は main、先頭は試しの merge）。"""
        base_files, head_files = self.files(self.main), self._applied(pr)
        given = CheckInput(
            base=OverlayView(self.base, base_files),
            head=OverlayView(self.base, head_files),
            changes=_changes(base_files, head_files),
            pull_request=PullRequest(pr.branch, pr.title, pr.body, pr.auto),
        )
        results = run_checks(given, REGENERATOR)
        failed = [check for check in results if check.status is Status.FAIL]
        pr.ci_ok = not failed
        pr.ci_messages = tuple(
            f"検査 {check.number}: {message}" for check in failed for message in check.messages
        )

    def merge(self, number: int) -> None:
        pr = self.prs[number]
        merged = self._applied(pr)
        for path in merged:
            if path not in self.files(self.main):
                self.first_seen.setdefault(path, int(self.clock.now().timestamp()))
        self.main = self._commit((self.main, pr.commit), merged)
        pr.state = PrState.MERGED
        pr.merge_commit = self.main

    def routine(self) -> list[FakePr]:
        return [pr for pr in self.prs.values() if pr.branch.startswith("runner/")]

    def open_issues(self) -> list[FakeIssue]:
        return [issue for issue in self.issues.values() if not issue.closed]

    def answer(self, issue: int, body: str) -> None:
        item = self.issues[issue]
        item.comments.append(
            Comment(f"c{next(self.counter)}", body, self.clock.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        )

    def issue_for(self, reason: str) -> FakeIssue:
        found = [issue for issue in self.open_issues() if f" {reason} " in issue.title]
        assert len(found) == 1, [issue.title for issue in self.open_issues()]
        return found[0]


@dataclass
class FakeGit:
    world: World

    def fetch(self) -> None:
        return None

    def origin_main(self) -> str:
        return self.world.main

    def snapshot(self, rev: str) -> RepoView:
        return self.world.view(rev)

    def commit_files(self, *, base: str, files: Mapping[str, bytes | None], message: str) -> str:
        return self.world.derive(base, files)

    def push_branch(self, commit: str, branch: str) -> None:
        assert branch.startswith(("runner/", "candidate-fix/")), branch
        self.world.branches[branch] = commit

    def first_seen_times(self, ref: str, card_ids: Sequence[str]) -> dict[str, int]:
        return {
            card_id: self.world.first_seen[f"research/hypotheses/queue/{card_id}.yaml"]
            for card_id in card_ids
        }

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return ancestor in self.world.ancestors(descendant)

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        return tuple(
            change.path for change in _changes(self.world.files(base), self.world.files(head))
        )

    def ensure_exec_worktree(self) -> None:
        return None

    def exec_head(self) -> str:
        return self.world.exec_head

    def exec_fast_forward(self, target: str) -> None:
        assert self.is_ancestor(self.world.exec_head, target)
        self.world.exec_head = target
        for path in LEDGERS:
            (self.world.exec_dir / path).write_bytes(self.world.files(target).get(path, b""))

    def exec_dirty_paths(self) -> tuple[str, ...]:
        committed = self.world.files(self.world.exec_head)
        return tuple(
            path
            for path in LEDGERS
            if (self.world.exec_dir / path).read_bytes() != committed.get(path, b"")
        )

    def exec_commit(self, paths: Sequence[str], message: str) -> str:
        files = {path: (self.world.exec_dir / path).read_bytes() for path in paths}
        self.world.exec_head = self.world.derive(self.world.exec_head, files)
        return self.world.exec_head

    def exec_undo_commit(self) -> None:
        self.world.exec_head = self.world.commits[self.world.exec_head].parents[0]

    def exec_push(self) -> None:
        self.world.branches["runner/exec"] = self.world.exec_head

    def exec_untracked(self, prefix: str) -> tuple[str, ...]:
        root = self.world.exec_dir
        return (
            tuple(
                sorted(
                    path.relative_to(root).as_posix()
                    for path in (root / prefix).rglob("*")
                    if path.is_file()
                )
            )
            if (root / prefix).is_dir()
            else ()
        )

    def main_checkout(self) -> MainCheckout:
        return MainCheckout("main", self.world.main_head, self.world.main_clean)

    def main_fast_forward(self, target: str) -> None:
        self.world.main_head = target
        (self.world.repo / FINAL).write_bytes(self.world.files(target).get(FINAL, b""))

    def main_file_matches(self, path: str, rev: str) -> bool:
        return (self.world.repo / path).read_bytes() == self.world.files(rev).get(path)

    def main_restore(self, path: str) -> None:
        (self.world.repo / path).write_bytes(self.world.files(self.world.main_head).get(path, b""))


@dataclass
class FakeGitHub:
    world: World

    def create_pr(self, *, branch: str, title: str, body: str) -> int:
        number = self.world.open_pr(branch, title, body)
        self.world.ci(self.world.prs[number])
        return number

    def pr_status(self, number: int) -> PullStatus:
        pr = self.world.prs[number]
        if pr.created_at == self.world.clock.now() and pr.state is PrState.OPEN:
            return PullStatus(pr.state, Checks.PENDING)
        if pr.state is PrState.OPEN and pr.auto and pr.ci_ok and pr.forced_fail == 0:
            self.world.merge(number)
        checks = Checks.PASS if pr.ci_ok and pr.forced_fail == 0 else Checks.FAIL
        return PullStatus(
            pr.state,
            checks,
            pr.merge_commit,
            self.world.clock.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            if pr.state is not PrState.OPEN
            else None,
        )

    def enable_auto_merge(self, number: int) -> None:
        pr = self.world.prs[number]
        pr.auto = True
        self.world.ci(pr)

    def rerun_checks(self, number: int) -> None:
        pr = self.world.prs[number]
        pr.reruns += 1
        if pr.forced_fail:
            pr.forced_fail -= 1

    def close_pr(self, number: int) -> None:
        self.world.prs[number].state = PrState.CLOSED

    def create_issue(self, *, title: str, body: str, label: str) -> int:
        number = next(self.world.numbers)
        self.world.issues[number] = FakeIssue(number, title, body, label)
        return number

    def open_issues(self, title: str) -> tuple[int, ...]:
        return tuple(
            issue.number
            for issue in self.world.issues.values()
            if issue.title == title and not issue.closed
        )

    def comment(self, number: int, body: str) -> None:
        self.world.issues[number].comments.append(
            Comment(f"r{next(self.world.counter)}", body, "2026-11-24T00:00:00Z")
        )

    def comments(self, number: int) -> tuple[Comment, ...]:
        return tuple(self.world.issues[number].comments)

    def close_issue(self, number: int) -> None:
        self.world.issues[number].closed = True


@dataclass
class FakeProcs:
    world: World
    alive_pids: set[int] = field(default_factory=set)
    leaders: set[int] = field(default_factory=set)
    signals: list[tuple[int, int]] = field(default_factory=list)
    spawned: dict[str, dict[str, Any]] = field(default_factory=dict)
    pids: itertools.count[int] = field(default_factory=lambda: itertools.count(5000))

    def alive(self, pid: int) -> bool:
        return pid in self.alive_pids

    def is_group_leader(self, pid: int) -> bool:
        return pid in self.leaders

    def signal_group(self, group: int, signal: int) -> None:
        import signal as signals

        self.signals.append((group, signal))
        if signal == signals.SIGTERM:
            self.alive_pids.discard(group)

    def spawn(self, command: Sequence[str], *, cwd: Path, state_dir: Path) -> int:
        pid = next(self.pids)
        self.alive_pids.add(pid)
        self.leaders.add(pid)
        # 切り離した処理の小さな包み（`detached.py`）と同じく、最初に自身の PID を書く。
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid.txt").write_text(f"{pid}\n", encoding="utf-8")
        parts = list(command)
        subcommand = parts[5] if len(parts) > 5 and parts[3] == "odyssey-fx" else parts[0]
        self.spawned[f"{subcommand}:{pid}"] = {
            "pid": pid,
            "command": parts,
            "state_dir": state_dir,
            "cwd": cwd,
        }
        return pid

    def by_kind(self, kind: str) -> dict[str, Any]:
        found = [item for key, item in self.spawned.items() if key.startswith(f"{kind}:")]
        assert found, (kind, list(self.spawned))
        return found[-1]

    def finish(self, kind: str, code: int, output: str = "") -> None:
        item = self.by_kind(kind)
        state_dir: Path = item["state_dir"]
        (state_dir / "output.log").write_text(output, encoding="utf-8")
        (state_dir / "exit_code.txt").write_text(f"{code}\n", encoding="utf-8")
        self.alive_pids.discard(int(item["pid"]))


@dataclass
class FakeSystem:
    boot: str = "boot-1"
    disk: int = 100 * 1000**3
    memory: int = 12 * 1024**3

    def boot_id(self) -> str:
        return self.boot

    def free_disk_bytes(self, path: Path) -> int:
        return self.disk

    def free_memory_bytes(self) -> int:
        return self.memory


@dataclass
class FakeWrapper:
    """包み（PR-B の後の形のつもり）。`launch` は版のディレクトリに起動の事実を書く。"""

    world: World
    procs: FakeProcs
    ready: bool = True
    locked_code: int = 10
    launched: list[dict[str, Any]] = field(default_factory=list)
    refuse: bool = False

    def contract(self) -> WrapperContract:
        if not self.ready:
            return WrapperContract(False, ("包みが D26 の PR-B の前（試験）",))
        return WrapperContract(True, (), self.locked_code)

    def launch(
        self,
        *,
        experiment: str,
        worktree: Path,
        repo: Path,
        mode: str,
        snapshots: Path | None = None,
    ) -> int:
        if self.refuse:
            return 2
        name = Path(experiment).stem
        version = 1
        if "_v" in name and name.rsplit("_v", 1)[1].isdigit():
            name, number = name.rsplit("_v", 1)
            version = int(number)
        version_dir = repo / "runs" / "experiments" / name / f"v{version}"
        version_dir.mkdir(parents=True, exist_ok=True)
        if (version_dir / "launch.json").exists():
            return 2
        run_pid = next(self.procs.pids)
        launcher = next(self.procs.pids)
        self.procs.alive_pids |= {run_pid, launcher}
        self.procs.leaders.add(run_pid)
        (version_dir / "launch.json").write_text(
            json.dumps({"run_pid": run_pid, "launcher_pid": launcher}), encoding="utf-8"
        )
        (version_dir / "started_at_utc.txt").write_text(
            self.world.clock.now().strftime("%Y-%m-%dT%H:%M:%SZ") + "\n", encoding="utf-8"
        )
        self.launched.append(
            {
                "experiment": experiment,
                "mode": mode,
                "snapshots": snapshots,
                "dir": version_dir,
                "pids": (run_pid, launcher),
            }
        )
        return 0

    def append_lines(self, count: int) -> None:
        """実行用の作業木の試行台帳に人工の行を `count` 行足す（`experiment run` の代わり）。"""
        path = self.world.exec_dir / TRIAL
        current = len(path.read_bytes().splitlines())
        path.write_bytes(trial_ledger(current + count))

    def finish(self, index: int, code: int = 0, *, lines: int = 1) -> None:
        item = self.launched[index]
        version_dir: Path = item["dir"]
        self.append_lines(lines)
        (version_dir / "time.log").write_text("  123456  maximum resident set size\n")
        (version_dir / "finished_at_utc.txt").write_text(
            self.world.clock.now().strftime("%Y-%m-%dT%H:%M:%SZ") + "\n", encoding="utf-8"
        )
        (version_dir / "exit_code.txt").write_text(f"{code}\n", encoding="utf-8")
        for pid in item["pids"]:
            self.procs.alive_pids.discard(pid)


@dataclass
class FakeEvaluation:
    ledger_code: int = 0
    fix: bool = True
    declaration: bool = False
    bindings: dict[str, Binding] = field(default_factory=dict)
    prepare_calls: int = 0

    def ledger_check(self, worktree: Path, out: Path) -> int:
        return self.ledger_code

    def prepare(self, worktree: Path, verification: Verification) -> PreregChecks:
        self.prepare_calls += 1
        return PreregChecks(verification.trial_count, verification.rejected_count, 1, 1024)

    def binding(self, version_dir: Path) -> Binding | None:
        key = f"{version_dir.parent.name}/{version_dir.name}"
        return self.bindings.get(key, Binding("a" * 64, 1))

    def run_ids(self, version_dir: Path) -> tuple[str, ...]:
        return ()

    def fix_available(self) -> bool:
        return self.fix

    def declaration_required(self) -> bool:
        return self.declaration


@dataclass
class FakeWorkers:
    """作業役の偽物: 役ごとに台本の出力を順に返す（起動の後、最初の様子の読みで終わる）。"""

    script: dict[str, list[WorkerPoll]] = field(default_factory=dict)
    launched: list[WorkerRequest] = field(default_factory=list)
    hold: set[str] = field(default_factory=set)

    def add(self, role: str, output: WorkerOutput | None = None, **kw: Any) -> None:
        status = kw.pop("status", WorkerStatus.DONE)
        self.script.setdefault(role, []).append(WorkerPoll(status, output, **kw))

    def launch(self, request: WorkerRequest) -> None:
        if (
            request.role == "ideator"
            and not self.script.get("ideator")
            and "ideator" not in self.hold
        ):
            # 台本の無い発想役は「定義が無い」（起動の印を残さない）。作業役は同時に 1 つまで
            # （D25 §4.4）なので、走り続ける発想役がカードの作業役を待たせないようにする。
            raise WorkerUnavailable("試験: 発想役の台本が無い")
        self.launched.append(request)

    def poll(self, request: WorkerRequest) -> WorkerPoll:
        if request.role in self.hold:
            return WorkerPoll(WorkerStatus.RUNNING)
        queue = self.script.get(request.role) or []
        if not queue:
            return WorkerPoll(WorkerStatus.RUNNING)
        return queue.pop(0)


@dataclass
class FakeGate:
    passed: bool = True
    calls: int = 0
    seen: list[GateInputs] = field(default_factory=list)

    def check(self, inputs: GateInputs) -> GateResult:
        self.calls += 1
        self.seen.append(inputs)
        return GateResult(tuple((item, self.passed or item != 3) for item in range(1, 6)))


@dataclass
class Rig:
    """試験の 1 つの世界（偽物の組と、毎回作り直す実行器）。"""

    world: World
    clock: FakeClock
    procs: FakeProcs
    system: FakeSystem
    wrapper: FakeWrapper
    evaluation: FakeEvaluation
    workers: FakeWorkers
    gate: FakeGate
    github: FakeGitHub

    @property
    def paths(self) -> Paths:
        return Paths(
            repo=self.world.repo,
            exec_worktree=self.world.exec_dir,
            snapshots=self.world.repo / "data" / "snapshots",
        )

    def runner(self) -> Runner:
        ports = Ports(
            clock=self.clock,
            git=FakeGit(self.world),
            github=self.github,
            procs=self.procs,
            system=self.system,
            wrapper=self.wrapper,
            evaluation=self.evaluation,
            workers=self.workers,
            gate=self.gate,
        )
        return Runner(self.paths, ports)

    def step(self, minutes: float = 5) -> tuple[str, ...]:
        """5 分進めて「1 歩」（実行器は毎回作り直す。状態はファイルから読む）。"""
        self.clock.advance(minutes=minutes)
        return self.runner().step().notes

    def card(self, card_id: str) -> dict[str, Any]:
        path = self.paths.state_dir / "cards" / f"{card_id}.json"
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(
            (self.paths.state_dir / "global.json").read_text(encoding="utf-8")
        )
        return data


def make_rig(root: Path) -> Rig:
    clock = FakeClock()
    world = World(root, clock)
    procs = FakeProcs(world)
    return Rig(
        world=world,
        clock=clock,
        procs=procs,
        system=FakeSystem(),
        wrapper=FakeWrapper(world, procs),
        evaluation=FakeEvaluation(),
        workers=FakeWorkers(),
        gate=FakeGate(),
        github=FakeGitHub(world),
    )
