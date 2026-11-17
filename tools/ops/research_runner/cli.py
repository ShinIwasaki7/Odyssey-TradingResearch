"""実行器のコマンド（D25 §2.4・§7）。

使い方（人間の主の作業木の根で。launchd の利用者のエージェントが 5 分ごとに呼ぶ。登録は人間）::

    uv run python -m tools.ops.research_runner step --repo <主の作業木>
    uv run python -m tools.ops.research_runner status --repo <主の作業木>
    uv run python -m tools.ops.research_runner launchd-plist --repo <主の作業木>

- `step`: 「1 歩進める」。別の「1 歩」が走っていれば何もせずに終わる。
- `status`: 全体とカードの状態（段・版・未解除の理由の識別子）を表示する（成績の数値は持たない）。
- `launchd-plist`: launchd の利用者のエージェントの定義の例を標準出力に書く（登録はしない。
  登録は人間が `launchctl bootstrap` で行う）。

終了コード: 0 = 進めた・走っていた・表示した、2 = 入力・道具の誤り。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

from tools.ops.hypothesis_cards.repo import RepoView, ToolFailure
from tools.ops.research_runner.adapters import (
    GhGitHub,
    LocalEvaluation,
    LocalGit,
    LocalProcesses,
    LocalSystem,
    SystemClock,
)
from tools.ops.research_runner.gate import LocalGate
from tools.ops.research_runner.runner import Paths, Ports, Runner
from tools.ops.research_runner.runs import WrapperProcess
from tools.ops.research_runner.state import StateStore
from tools.ops.research_runner.workers import ClaudeWorkers

__all__ = ["build_runner", "main", "plist_text"]

EXEC_WORKTREE = Path(".claude/worktrees/runner-exec")
LABEL = "local.odyssey.research-runner"


def build_runner(repo: Path, exec_worktree: Path, snapshots: Path) -> Runner:
    paths = Paths(repo=repo, exec_worktree=exec_worktree, snapshots=snapshots)
    git = LocalGit(repo, exec_worktree)
    clock = SystemClock()
    procs = LocalProcesses()

    def view() -> RepoView:
        # 束と関門は `origin/main` の時点の版管理のファイルを読む（作業木の汚れを写さない）。
        return git.snapshot(git.origin_main())

    ports = Ports(
        clock=clock,
        git=git,
        github=GhGitHub(repo),
        procs=procs,
        system=LocalSystem(),
        wrapper=WrapperProcess(),
        evaluation=LocalEvaluation(snapshots),
        workers=ClaudeWorkers(repo=repo, view=view, procs=procs, clock=clock),
        gate=LocalGate(
            repo=repo, exec_worktree=exec_worktree, view=view, is_ancestor=git.is_ancestor
        ),
    )
    return Runner(paths, ports)


def plist_text(repo: Path, *, interval: int = 300) -> str:
    """5 分ごとに `step` を呼ぶ利用者のエージェントの定義の例（D25-14 (a)）。"""
    log = repo / "runs" / "runner" / "log"
    arguments = "".join(
        f"\n    <string>{escape(part)}</string>"
        for part in (
            "/usr/bin/env",
            "uv",
            "run",
            "--frozen",
            "python",
            "-m",
            "tools.ops.research_runner",
            "step",
            "--repo",
            str(repo),
        )
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{arguments}
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(str(repo))}</string>
  <key>StartInterval</key>
  <integer>{interval}</integer>
  <key>StandardOutPath</key>
  <string>{escape(str(log / "launchd.out.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(log / "launchd.err.log"))}</string>
</dict>
</plist>
"""


def _status(repo: Path) -> int:
    store = StateStore(repo / "runs" / "runner" / "state")
    state = store.load_global()
    if state is None:
        print("実行器の状態はまだ無い")
        return 0
    reasons = ", ".join(sorted(state.reason_ids())) or "なし"
    print(f"全体: {state.phase.value}（未解除の理由: {reasons}）")
    for card in store.cards(state):
        open_reasons = ", ".join(sorted(card.reason_ids())) or "なし"
        print(f"- {card.card_id}: {card.phase} 版 {card.version}（未解除の理由: {open_reasons}）")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ops.research_runner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("step", "status", "launchd-plist"):
        command = sub.add_parser(name)
        command.add_argument("--repo", type=Path, default=Path("."), help="人間の主の作業木の根")
        if name == "step":
            command.add_argument("--exec-worktree", type=Path, default=None)
            command.add_argument("--snapshots", type=Path, default=None)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    if args.command == "status":
        return _status(repo)
    if args.command == "launchd-plist":
        sys.stdout.write(plist_text(repo))
        return 0
    exec_worktree = (args.exec_worktree or repo / EXEC_WORKTREE).resolve()
    snapshots = (args.snapshots or repo / "data" / "snapshots").resolve()
    try:
        outcome = build_runner(repo, exec_worktree, snapshots).step()
    except ToolFailure as exc:
        print(f"道具の失敗: {exc}", file=sys.stderr)
        return 2
    for note in outcome.notes:
        print(note)
    return 0
