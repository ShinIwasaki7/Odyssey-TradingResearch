"""探索の run の包み `tools/ops/run_experiment.py` との境目（D25-13 (a)。決定 185「呼ぶだけ」）。

- **包みとの約束**（D26 の PR-B。仮置き 35・36）: 実行器は包みを変えずに呼ぶ。ただし起動の前に、
  包みが PR-B の後の形かを確かめ、前なら何も起動せずに全体の停止にする（D25 §8。理由の文に
  「包みが D26 の PR-B の前」）。main 4c947a8（PR #194 の merge）の実物で確認済み（本 PR の
  仮置き 1）:
  1. 方式の引数 `--variant-mode`（包みの `VARIANT_MODES` に `parallel`・`serial`。包みの既定は
     `parallel`、`experiment run`・`experiment reproduce` の既定は `serial` なので、実行器は
     毎回の方式を必ず渡す）。PR-B の後かはこの属性で見分ける（PR-B は main に入ったが、D25 §8 の
     「包みが PR-B の前なら何も起動しない」の守りとして残す）。snapshot の基点は `--snapshots`
     で審査・再現・最終検証と同じものを渡す。
  2. `caffeinate` を新しいプロセスグループの先頭として起動する（`launch.json` の `run_pid` が
     グループの番号）。PR-B の包みには印の属性が無いので 1 と同じ PR で入ることを前提にし、
     グループへ合図を送る前に毎回 `run_pid` がグループの先頭かを確かめる。
  3. 同じ版の実行中の印のロックが取れない拒否の専用の終了コード 10（`experiment run`・
     `experiment reproduce` の終了コード。D07 の終了コードの表の PR-B の改訂、D26 の仮置き 3）。
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
    "EXIT_RUN_LOCKED",
    "MODE_ARGUMENT",
    "PARALLEL",
    "SERIAL",
    "RunFiles",
    "WRAPPER_FILES",
    "WrapperProcess",
    "move_attempt",
    "probe_contract",
    "read_run",
]

MODE_ARGUMENT: Final = "--variant-mode"
#: 方式の値（包みの `VARIANT_MODES`。`experiment reproduce --variant-mode` も同じ値）。
PARALLEL: Final = "parallel"
SERIAL: Final = "serial"
#: ロックが取れない拒否の専用の終了コード（D26 の仮置き 3。run の終了コードとして
#: `exit_code.txt` に書かれる。`experiment reproduce` も同じ値）。
EXIT_RUN_LOCKED: Final = 10
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
    modes = getattr(module, "VARIANT_MODES", None)
    if not isinstance(modes, tuple) or not {PARALLEL, SERIAL} <= set(modes):
        problems.append(
            "包みが D26 の PR-B の前（方式の引数 --variant-mode と、run_pid を先頭とする"
            "プロセスグループの分離が無い）"
        )
    locked = None if problems else EXIT_RUN_LOCKED
    return WrapperContract(ready=not problems, problems=tuple(problems), locked_exit_code=locked)


@dataclass(frozen=True, slots=True)
class WrapperProcess:
    """包みを別のプロセスで呼ぶ実物（`Wrapper` の口）。"""

    python: str = sys.executable

    def contract(self) -> WrapperContract:
        return probe_contract()

    def launch(
        self, *, experiment: str, worktree: Path, repo: Path, snapshots: Path, mode: str
    ) -> int:
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
                "--snapshots",
                str(snapshots),
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
