"""切り離して起動する処理の小さな包み（D25 §2.4「起動し直したときの照合」）。

実行器は、審査・最終検証・再現・固定の前の確かめ・作業役を、この包みで起動する。包みは
`<状態のディレクトリ>/` に `pid.txt`（包み自身の PID）を書き、`command` を走らせ、標準出力と
標準エラーを `output.log` に保存し、終わったら `exit_code.txt` と `finished_at_utc.txt` を書く。
`output.log` は実行器が読むことがある（審査・固定のコマンドは成績の数値を出さない。D16 §11.2、
PR 4c）。探索の run は `run_experiment.py` が同じ役をするので、この包みを使わない。

使い方（実行器が起動する）::

    python -m tools.ops.research_runner.detached --state-dir <dir> --cwd <dir> -- <command> ...
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

__all__ = ["EXIT_NOT_STARTED", "command_line", "main"]

EXIT_NOT_STARTED: Final = 127


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def command_line(state_dir: Path, cwd: Path, command: Sequence[str]) -> list[str]:
    """この包みで `command` を走らせるコマンド。"""
    return [
        sys.executable,
        "-m",
        "tools.ops.research_runner.detached",
        "--state-dir",
        str(state_dir),
        "--cwd",
        str(cwd),
        "--",
        *command,
    ]


def main(argv: Sequence[str] | None = None) -> int:
    given = list(sys.argv[1:] if argv is None else argv)
    if "--" not in given:
        print("`--` の後に走らせるコマンドを書く", file=sys.stderr)
        return 2
    split = given.index("--")
    parser = argparse.ArgumentParser(prog="python -m tools.ops.research_runner.detached")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    args = parser.parse_args(given[:split])
    command = given[split + 1 :]
    if not command:
        print("走らせるコマンドが無い", file=sys.stderr)
        return 2
    state_dir: Path = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "pid.txt").write_text(f"{os.getpid()}\n", encoding="utf-8")
    with (state_dir / "output.log").open("wb") as output:
        try:
            completed = subprocess.run(  # noqa: S603 - 実行器が組み立てた固定のコマンド
                command,
                cwd=args.cwd,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
            code = completed.returncode
        except OSError as exc:
            output.write(f"起動できない: {exc}\n".encode())
            code = EXIT_NOT_STARTED
    (state_dir / "finished_at_utc.txt").write_text(_now() + "\n", encoding="utf-8")
    # 終了コードを最後に書く（あれば終わり。照合の (2)）。
    tmp = state_dir / "exit_code.txt.tmp"
    tmp.write_text(f"{code}\n", encoding="utf-8")
    os.replace(tmp, state_dir / "exit_code.txt")
    return code


if __name__ == "__main__":
    sys.exit(main())
