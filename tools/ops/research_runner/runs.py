"""探索の run の包み `tools/ops/run_experiment.py` との境目（D25-13 (a)。決定 185「呼ぶだけ」）。

- **包みとの約束**（D26 の PR-B。仮置き 35・36）: 実行器は包みを変えずに呼ぶ。ただし起動の前に、
  包みが次の 3 つを持つことを確かめ、無ければ何も起動せずに全体の停止にする（D25 §8。理由の文に
  「包みが D26 の PR-B の前」）。名前は本 PR の仮置きで、PR-B の実装に合わせて直す:
  1. 方式の引数 `--execution-mode`（`EXECUTION_MODES` に `parallel`・`sequential`）。
  2. `caffeinate` を新しいプロセスグループの先頭として起動する印 `RUN_PID_LEADS_GROUP = True`
     （`launch.json` の `run_pid` がグループの番号になる）。
  3. 同じ版の実行中の印のロックが取れない拒否の専用の終了コード `EXIT_RUN_LOCKED`。
- **終わりの知り方**: 包みが書く `exit_code.txt`・`launch.json` の `run_pid`・
  `launcher_pid` の生死。
  `console.log` は読まない（判定が出るため。D14 §2.3.1）。
- **同じ版の走らせ直し**（D25 §4.7）: 包みが書いたファイルを `runner_attempts/<試みの番号>/` に
  移してから包みを呼ぶ（包みは `launch.json` があると拒否する）。中身は読まない。
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Final

from tools.ops.research_runner.ports import WrapperContract

__all__ = [
    "MODE_ARGUMENT",
    "RunFiles",
    "WRAPPER_FILES",
    "WrapperProcess",
    "move_attempt",
    "probe_contract",
    "read_run",
]

MODE_ARGUMENT: Final = "--execution-mode"
#: 包みが版のディレクトリに書くファイル（D25 §4.7 の 2）。
WRAPPER_FILES: Final = (
    "launch.json",
    "started_at_utc.txt",
    "exit_code.txt",
    "finished_at_utc.txt",
    "time.log",
    "console.log",
    "launcher.log",
    "viewer_exit_code.txt",
    "viewer.log",
)


def probe_contract(module: ModuleType | None = None) -> WrapperContract:
    """包みが PR-B の後の形か（属性だけを読む。包みを走らせない）。"""
    if module is None:
        module = importlib.import_module("tools.ops.run_experiment")
    problems: list[str] = []
    modes = getattr(module, "EXECUTION_MODES", None)
    if not isinstance(modes, tuple) or not {"parallel", "sequential"} <= set(modes):
        problems.append("包みが D26 の PR-B の前（方式の引数 --execution-mode が無い）")
    if getattr(module, "RUN_PID_LEADS_GROUP", None) is not True:
        problems.append(
            "包みが D26 の PR-B の前（run_pid がプロセスグループの先頭でない。一時停止できない）"
        )
    locked = getattr(module, "EXIT_RUN_LOCKED", None)
    if isinstance(locked, bool) or not isinstance(locked, int):
        problems.append("包みが D26 の PR-B の前（ロックの拒否の専用の終了コードが無い）")
        locked = None
    return WrapperContract(ready=not problems, problems=tuple(problems), locked_exit_code=locked)


@dataclass(frozen=True, slots=True)
class WrapperProcess:
    """包みを別のプロセスで呼ぶ実物（`Wrapper` の口）。"""

    python: str = sys.executable

    def contract(self) -> WrapperContract:
        return probe_contract()

    def launch(self, *, experiment: str, worktree: Path, repo: Path, mode: str) -> int:
        completed = subprocess.run(  # noqa: S603 - 固定のモジュールと実行器が決めた引数
            [
                self.python,
                "-m",
                "tools.ops.run_experiment",
                "--experiment",
                experiment,
                "--repo",
                str(repo),
                "--worktree",
                str(worktree),
                MODE_ARGUMENT,
                mode,
            ],
            cwd=worktree,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        return completed.returncode


@dataclass(frozen=True, slots=True)
class RunFiles:
    """包みのファイルから読んだ run の様子（成績は読まない）。"""

    launched: bool
    exit_code: int | None
    run_pid: int | None
    launcher_pid: int | None


def _int_file(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def read_run(version_dir: Path) -> RunFiles:
    launch = version_dir / "launch.json"
    run_pid = launcher_pid = None
    if launch.is_file():
        try:
            data = json.loads(launch.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            run_pid = data.get("run_pid") if isinstance(data.get("run_pid"), int) else None
            launcher_pid = (
                data.get("launcher_pid") if isinstance(data.get("launcher_pid"), int) else None
            )
    return RunFiles(
        launched=launch.is_file(),
        exit_code=_int_file(version_dir / "exit_code.txt"),
        run_pid=run_pid,
        launcher_pid=launcher_pid,
    )


def move_attempt(version_dir: Path, attempt: int) -> Path:
    """包みが書いたファイルを `runner_attempts/<attempt>/` に移す（消さない。中身は読まない）。"""
    target = version_dir / "runner_attempts" / str(attempt)
    target.mkdir(parents=True, exist_ok=False)
    for name in WRAPPER_FILES:
        source = version_dir / name
        if source.exists():
            source.replace(target / name)
    return target
