"""実行器の口の実物（git・プロセス・切り離した処理の包み）の試験。

一時の git の作業場と本物の子プロセスで確かめる。

実際の origin・GitHub には触れない（bare の origin はテストの中で作る）。`gh` は呼ばない。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.fixtures.git_origin import git, isolate_git, publish_repo
from tools.ops.research_runner.adapters import LocalGit, LocalProcesses, LocalSystem
from tools.ops.research_runner.detached import main as detached_main

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
