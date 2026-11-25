"""実行器の口の実物（git・プロセス・切り離した処理の包み）の試験。

一時の git の作業場と本物の子プロセスで確かめる。

実際の origin・GitHub には触れない（bare の origin はテストの中で作る）。`gh` は呼ばない。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.fixtures.git_origin import git, isolate_git, publish_repo
from tools.ops.research_runner.adapters import (
    GhGitHub,
    LocalGit,
    LocalProcesses,
    LocalSystem,
    checks_from_rollup,
)
from tools.ops.research_runner.detached import main as detached_main
from tools.ops.research_runner.ports import Checks, PrState

TRIAL = "research/trial_ledger.jsonl"


@pytest.fixture
def local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalGit:
    isolate_git(monkeypatch, tmp_path / "home")
    repo = tmp_path / "repo"
    (repo / "research").mkdir(parents=True)
    (repo / TRIAL).write_bytes(b"")
    (repo / "research/hypotheses/queue").mkdir(parents=True)
    (repo / "research/hypotheses/queue/h-20261120-card.yaml").write_bytes(b"id: x\n")
    publish_repo(repo, tmp_path / "origin")
    adapter = LocalGit(repo, repo / ".claude/worktrees/runner-exec")
    adapter.fetch()
    return adapter


def test_commit_files_builds_a_commit_without_touching_the_worktree(local: LocalGit) -> None:
    base = local.origin_main()
    commit = local.commit_files(
        base=base,
        files={
            "research/hypotheses/taken/h-20261120-card.yaml": b"id: x\n",
            "research/hypotheses/queue/h-20261120-card.yaml": None,
        },
        message="[runner] test",
    )
    listing = git(local.repo, "ls-tree", "-r", "--name-only", commit)
    assert "research/hypotheses/taken/h-20261120-card.yaml" in listing
    assert "research/hypotheses/queue/h-20261120-card.yaml" not in listing
    assert (local.repo / "research/hypotheses/queue/h-20261120-card.yaml").is_file()
    assert git(local.repo, "status", "--porcelain") == ""
    local.push_branch(commit, "runner/prereg-h-20261120-card-v1")
    assert git(local.repo, "ls-remote", "origin", "runner/prereg-h-20261120-card-v1")
    view = local.snapshot(commit)
    assert view.read("research/hypotheses/taken/h-20261120-card.yaml") == b"id: x\n"
    assert local.changed_paths(base, commit) == (
        "research/hypotheses/queue/h-20261120-card.yaml",
        "research/hypotheses/taken/h-20261120-card.yaml",
    )
    assert local.is_ancestor(base, commit) and not local.is_ancestor(commit, base)


def test_only_runner_branches_are_pushed(local: LocalGit) -> None:
    from tools.ops.hypothesis_cards.repo import ToolFailure

    with pytest.raises(ToolFailure):
        local.push_branch(local.origin_main(), "main")


def test_the_exec_worktree_commits_only_the_ledger(local: LocalGit) -> None:
    local.ensure_exec_worktree()
    local.ensure_exec_worktree()  # 2 回目は何もしない
    root = local.exec_worktree
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "runner/exec"
    assert local.exec_dirty_paths() == ()
    (root / TRIAL).write_bytes(b'{"line": 1}\n')
    (root / "research/candidate_fixes/a/1").mkdir(parents=True)
    (root / "research/candidate_fixes/a/1/1.yaml").write_bytes(b"x: 1\n")
    assert local.exec_dirty_paths() == (TRIAL,)
    assert local.exec_untracked("research/candidate_fixes/") == (
        "research/candidate_fixes/a/1/1.yaml",
    )
    head = local.exec_commit([TRIAL], "[runner] ledger")
    assert local.exec_dirty_paths() == ()
    assert local.exec_head() == head
    local.exec_push()
    assert git(local.repo, "ls-remote", "origin", "runner/exec").startswith(head)
    local.exec_undo_commit()
    assert local.exec_dirty_paths() == (TRIAL,)


def test_the_main_checkout_is_read_and_fast_forwarded(local: LocalGit) -> None:
    checkout = local.main_checkout()
    assert checkout.branch == "main" and checkout.clean
    other = local.commit_files(
        base=checkout.head, files={TRIAL: b'{"line": 1}\n'}, message="ledger"
    )
    (local.repo / TRIAL).write_bytes(b'{"line": 1}\n')
    assert not local.main_checkout().clean
    assert local.main_file_matches(TRIAL, other)
    local.main_restore(TRIAL)
    assert local.main_checkout().clean
    local.main_fast_forward(other)
    assert local.main_checkout().head == other


def test_the_detached_wrapper_writes_the_exit_code(tmp_path: Path) -> None:
    state = tmp_path / "state"
    code = detached_main(
        ["--state-dir", str(state), "--cwd", str(tmp_path), "--", sys.executable, "-c", "exit(3)"]
    )
    assert code == 3
    assert (state / "exit_code.txt").read_text() == "3\n"
    assert (state / "finished_at_utc.txt").is_file()
    assert int((state / "pid.txt").read_text()) == os.getpid()
    assert detached_main(["--state-dir", str(state), "--cwd", str(tmp_path)]) == 2


def test_a_process_group_is_paused_resumed_and_terminated(tmp_path: Path) -> None:
    """一時停止（SIGSTOP）・再開（SIGCONT）・終了（SIGTERM の後に SIGCONT）をグループ全体へ。"""
    procs = LocalProcesses()
    leader = subprocess.Popen(
        [sys.executable, "-c", "import time\nwhile True: time.sleep(0.05)"],
        start_new_session=True,
    )
    try:
        assert procs.alive(leader.pid) and procs.is_group_leader(leader.pid)
        procs.signal_group(leader.pid, signal.SIGSTOP)
        time.sleep(0.2)
        status = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(leader.pid)], capture_output=True, text=True
        ).stdout
        assert status.strip().startswith("T")
        procs.signal_group(leader.pid, signal.SIGTERM)
        procs.signal_group(leader.pid, signal.SIGCONT)
        leader.wait(timeout=10)
        assert not procs.alive(leader.pid)
    finally:
        if leader.poll() is None:
            leader.kill()
    procs.signal_group(leader.pid, signal.SIGCONT)  # 居ないグループは何もしない


def test_spawn_runs_the_command_through_the_detached_wrapper(tmp_path: Path) -> None:
    procs = LocalProcesses()
    state = tmp_path / "proc"
    root = Path(__file__).resolve().parents[3]
    pid = procs.spawn([sys.executable, "-c", "exit(0)"], cwd=root, state_dir=state)
    assert procs.is_group_leader(pid) or not procs.alive(pid)
    for _ in range(100):
        if (state / "exit_code.txt").is_file():
            break
        time.sleep(0.05)
    assert (state / "exit_code.txt").read_text() == "0\n"


def test_spawn_runs_the_command_in_a_directory_without_the_tools_package(tmp_path: Path) -> None:
    """作業役の束（`tools/` の無いディレクトリ）でも包みが起動し、コマンドだけが束で走る
    （Codex 2 巡目の指摘）。走らせるのは `pwd` を書くだけの小さなコマンド。"""
    procs = LocalProcesses()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    state = tmp_path / "proc"
    command = [sys.executable, "-c", "import os; print(os.getcwd())"]
    procs.spawn(command, cwd=bundle, state_dir=state)
    for _ in range(200):
        if (state / "exit_code.txt").is_file():
            break
        time.sleep(0.05)
    assert (state / "exit_code.txt").read_text() == "0\n"
    assert Path((state / "output.log").read_text().strip()).resolve() == bundle.resolve()


def test_the_system_reads_disk_and_memory() -> None:
    system = LocalSystem()
    assert system.free_disk_bytes(Path(".")) > 0
    assert system.free_memory_bytes() >= 0
    assert isinstance(system.boot_id(), str)


def test_a_missing_command_is_a_failed_command_not_an_exception(tmp_path: Path) -> None:
    """コマンドが無い（macOS の外の `vm_stat` など）ときも例外にせず、失敗として扱う
    （空きメモリは 0 の安全側。CI の Linux でも落ちない）。"""
    from tools.ops.research_runner.adapters import _run

    completed = _run([str(tmp_path / "no-such-command")])
    assert completed.returncode == 127 and completed.stdout == b""


def _wait_exit(state: Path, rounds: int = 400) -> str:
    for _ in range(rounds):
        if (state / "exit_code.txt").is_file():
            return (state / "exit_code.txt").read_text()
        time.sleep(0.05)
    raise AssertionError("包みが終了コードを書かなかった")


def test_the_wrapper_adds_the_env_only_to_the_command(tmp_path: Path) -> None:
    procs = LocalProcesses()
    state = tmp_path / "proc"
    command = [sys.executable, "-c", "import os; print(os.environ.get('RUNNER_PROBE_X', '-'))"]
    procs.spawn(command, cwd=tmp_path, state_dir=state, env={"RUNNER_PROBE_X": "1"})
    assert _wait_exit(state) == "0\n"
    assert (state / "output.log").read_text().strip() == "1"


def test_the_wrapper_stops_only_its_command_when_the_time_is_up(tmp_path: Path) -> None:
    """時間切れ: 包みが自分で起動したコマンドだけを止め、時間切れの印と終了コード 124 を書く。"""
    from tools.ops.research_runner.detached import EXIT_TIMED_OUT, TIMED_OUT_FILE

    procs = LocalProcesses()
    state = tmp_path / "proc"
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    procs.spawn(command, cwd=tmp_path, state_dir=state, timeout_seconds=1)
    assert _wait_exit(state) == f"{EXIT_TIMED_OUT}\n"
    assert (state / TIMED_OUT_FILE).is_file()


def test_the_wrapper_command_line_carries_the_options(tmp_path: Path) -> None:
    from tools.ops.research_runner.detached import command_line

    line = command_line(tmp_path, tmp_path, ["claude", "-p"], env={"A": "1"}, timeout_seconds=9)
    split = line.index("--")
    assert line[split + 1 :] == ["claude", "-p"]
    assert line[line.index("--env") + 1] == "A=1"
    assert line[line.index("--timeout-seconds") + 1] == "9"
    assert "--env" not in command_line(tmp_path, tmp_path, ["x"])


# --- PR の検査の一覧の読み方（`gh` は呼ばない） ---
# 一覧の形は 2026-11-25 の実物の PR（#210・#212）の読み取りに合わせた。

LEDGER = "ledger / routine PR checks"
CI = "lint / types / imports / tests"


def _run_item(
    name: str, workflow: str, started: str, *, status: str = "COMPLETED", conclusion: str = ""
) -> dict[str, str]:
    return {
        "__typename": "CheckRun",
        "name": name,
        "workflowName": workflow,
        "startedAt": started,
        "status": status,
        "conclusion": conclusion,
    }


def _ledger(started: str, conclusion: str, *, status: str = "COMPLETED") -> dict[str, str]:
    return _run_item(LEDGER, "Ledger and routine PR", started, status=status, conclusion=conclusion)


def _ci(started: str, conclusion: str = "", *, status: str = "COMPLETED") -> dict[str, str]:
    return _run_item(CI, "CI", started, status=status, conclusion=conclusion)


def test_a_cancelled_run_replaced_by_a_newer_run_is_not_a_failure() -> None:
    # 実行器が PR を開いた直後に自動 merge を有効にすると、台帳の検査の 1 本目は取り消され、
    # 2 本目が走る（`ledger.yml` の `cancel-in-progress`）。落ちた検査は無い。
    rollup = [
        _ledger("2026-11-25T10:35:35Z", "CANCELLED"),
        _ci("2026-11-25T10:35:36Z", status="IN_PROGRESS"),
        _ci("2026-11-25T10:35:27Z", status="IN_PROGRESS"),
        _ledger("2026-11-25T10:35:42Z", "SUCCESS"),
    ]
    assert checks_from_rollup(rollup) is Checks.PENDING
    done = [
        _ledger("2026-11-25T10:35:35Z", "CANCELLED"),
        _ci("2026-11-25T10:35:36Z", "SUCCESS"),
        _ci("2026-11-25T10:35:27Z", "SUCCESS"),
        _ledger("2026-11-25T10:35:42Z", "SUCCESS"),
    ]
    assert checks_from_rollup(done) is Checks.PASS


def test_the_order_of_the_list_does_not_decide_which_run_is_newest() -> None:
    rollup = [
        _ledger("2026-11-25T10:35:42Z", "SUCCESS"),
        _ledger("2026-11-25T10:35:35Z", "CANCELLED"),
        _ci("2026-11-25T10:35:36Z", "SUCCESS"),
    ]
    assert checks_from_rollup(rollup) is Checks.PASS


@pytest.mark.parametrize(
    "conclusion", ["FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"]
)
def test_the_newest_run_of_a_check_still_fails_the_checks(conclusion: str) -> None:
    rollup = [
        _ledger("2026-11-25T10:35:35Z", "SUCCESS"),
        _ledger("2026-11-25T10:35:42Z", conclusion),
        _ci("2026-11-25T10:35:36Z", "SUCCESS"),
    ]
    assert checks_from_rollup(rollup) is Checks.FAIL


def test_a_failure_of_one_check_is_not_hidden_by_another_check() -> None:
    rollup = [
        _ledger("2026-11-25T10:35:42Z", "SUCCESS"),
        _ci("2026-11-25T10:35:27Z", "FAILURE"),
    ]
    assert checks_from_rollup(rollup) is Checks.FAIL
    # 名前が同じでも定義（workflow）が違えば別の検査として数える。
    other = [
        _run_item("checks", "A", "2026-11-25T10:00:00Z", conclusion="FAILURE"),
        _run_item("checks", "B", "2026-11-25T10:05:00Z", conclusion="SUCCESS"),
    ]
    assert checks_from_rollup(other) is Checks.FAIL


def test_runs_at_the_same_time_use_the_later_one_in_the_list() -> None:
    rollup = [
        _ledger("2026-11-25T10:41:06Z", "CANCELLED"),
        _ledger("2026-11-25T10:41:06Z", "SUCCESS"),
    ]
    assert checks_from_rollup(rollup) is Checks.PASS
    assert checks_from_rollup(list(reversed(rollup))) is Checks.FAIL


def test_no_checks_yet_and_running_checks_are_pending() -> None:
    assert checks_from_rollup([]) is Checks.PENDING
    assert checks_from_rollup([_ci("2026-11-25T10:35:27Z", status="QUEUED")]) is Checks.PENDING
    assert (
        checks_from_rollup([_ci("2026-11-25T10:35:27Z", "SKIPPED"), _ledger("", "NEUTRAL")])
        is Checks.PASS
    )


def _queued(name: str, workflow: str) -> dict[str, str | None]:
    # 待ちの実行は開始の時刻がまだ無いことがある。
    return {
        "__typename": "CheckRun",
        "name": name,
        "workflowName": workflow,
        "startedAt": None,
        "status": "QUEUED",
        "conclusion": "",
    }


@pytest.mark.parametrize("older", ["SUCCESS", "CANCELLED", "FAILURE"])
def test_a_waiting_replacement_keeps_the_check_pending_whatever_the_older_run_was(
    older: str,
) -> None:
    # 置き換えの実行が待ち・実行中の間は、古い実行の結果（合格・取り消し・失敗）で決めない。
    waiting: list[Mapping[str, str | None]] = [
        _ledger("2026-11-25T10:35:35Z", older),
        _queued(LEDGER, "Ledger and routine PR"),
        _ci("2026-11-25T10:35:36Z", "SUCCESS"),
    ]
    assert checks_from_rollup(waiting) is Checks.PENDING
    assert checks_from_rollup(list(reversed(waiting))) is Checks.PENDING
    running = [
        _ledger("2026-11-25T10:35:35Z", older),
        _ledger("2026-11-25T10:35:42Z", "", status="IN_PROGRESS"),
        _ci("2026-11-25T10:35:36Z", "SUCCESS"),
    ]
    assert checks_from_rollup(running) is Checks.PENDING


def test_a_failed_check_wins_over_another_check_that_is_still_running() -> None:
    rollup = [
        _ledger("2026-11-25T10:35:42Z", "FAILURE"),
        _ci("2026-11-25T10:35:36Z", status="IN_PROGRESS"),
    ]
    assert checks_from_rollup(rollup) is Checks.FAIL


def test_a_finished_run_without_a_start_time_is_ordered_by_its_end_time() -> None:
    # 待ちのまま取り消された実行は開始の時刻が無いことがある。終わりの時刻で新旧を決める。
    cancelled_later = {
        "__typename": "CheckRun",
        "name": LEDGER,
        "workflowName": "Ledger and routine PR",
        "startedAt": None,
        "completedAt": "2026-11-25T10:36:00Z",
        "status": "COMPLETED",
        "conclusion": "CANCELLED",
    }
    passed = _ledger("2026-11-25T10:35:35Z", "SUCCESS")
    assert checks_from_rollup([cancelled_later, passed]) is Checks.FAIL
    cancelled_earlier = {**cancelled_later, "completedAt": "2026-11-25T10:35:00Z"}
    assert checks_from_rollup([cancelled_earlier, passed]) is Checks.PASS


def test_commit_statuses_are_read_by_their_context() -> None:
    def status(state: str, started: str) -> dict[str, str]:
        return {
            "__typename": "StatusContext",
            "context": "ci/outside",
            "state": state,
            "startedAt": started,
        }

    assert (
        checks_from_rollup(
            [status("FAILURE", "2026-11-25T10:00:00Z"), status("SUCCESS", "2026-11-25T10:05:00Z")]
        )
        is Checks.PASS
    )
    assert checks_from_rollup([status("ERROR", "2026-11-25T10:05:00Z")]) is Checks.FAIL


def test_pr_status_reads_the_newest_run_of_each_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = {
        "state": "OPEN",
        "mergeCommit": None,
        "closedAt": None,
        "statusCheckRollup": [
            _ledger("2026-11-25T10:35:35Z", "CANCELLED"),
            _ledger("2026-11-25T10:35:42Z", "SUCCESS"),
            _ci("2026-11-25T10:35:36Z", "SUCCESS"),
        ],
    }
    calls: list[tuple[str, ...]] = []

    def fake_gh(self: GhGitHub, *args: str, stdin: bytes | None = None) -> str:
        calls.append(args)
        return json.dumps(answer)

    monkeypatch.setattr(GhGitHub, "_gh", fake_gh)
    status = GhGitHub(tmp_path).pr_status(210)
    assert status.state is PrState.OPEN
    assert status.checks is Checks.PASS
    assert calls == [
        ("pr", "view", "210", "--json", "state,mergeCommit,closedAt,statusCheckRollup")
    ]
