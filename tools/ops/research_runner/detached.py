"""切り離して起動する処理の小さな包み（D25 §2.4「起動し直したときの照合」）。

実行器は、審査・最終検証・再現・固定の前の確かめ・作業役を、この包みで起動する。包みは
`<状態のディレクトリ>/` に `pid.txt`（包み自身の PID）を書き、`command` を走らせ、標準出力と
標準エラーを `output.log` に保存し、終わったら `exit_code.txt` と `finished_at_utc.txt` を書く。
`output.log` は実行器が読むことがある（審査・固定のコマンドは成績の数値を出さない。D16 §11.2、
PR 4c）。探索の run は `run_experiment.py` が同じ役をするので、この包みを使わない。

作業役のときだけ、次の 2 つを使う（後追いの PR の仮置き H3・H5）:

- `--env KEY=VALUE`（繰り返せる）: `command` にだけ足す環境変数（作業役の文脈を絞る）。
- `--timeout-seconds N`: 壁時計の時間の上限。超えたら、包みが自分で起動した `command` の
  プロセスだけを、その PID で止める（`SIGTERM`、30 秒待って `SIGKILL`）。`timed_out.txt` を書き、
  終了コードは `EXIT_TIMED_OUT`（124）。名前の型でプロセスを探して止めることはしない。

使い方（実行器が起動する）::

    python -m tools.ops.research_runner.detached --state-dir <dir> --cwd <dir> -- <command> ...
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Final

__all__ = ["EXIT_NOT_STARTED", "EXIT_TIMED_OUT", "TIMED_OUT_FILE", "command_line", "main"]

EXIT_NOT_STARTED: Final = 127
#: 時間切れで止めたときの終了コード（`timeout` コマンドの慣例と同じ値）。
EXIT_TIMED_OUT: Final = 124
TIMED_OUT_FILE: Final = "timed_out.txt"
#: 時間切れの `SIGTERM` の後、`SIGKILL` までに待つ秒数。
_TERMINATE_GRACE_SECONDS: Final = 30


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def command_line(
    state_dir: Path,
    cwd: Path,
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> list[str]:
    """この包みで `command` を走らせるコマンド。"""
    options: list[str] = []
    for key, value in sorted((env or {}).items()):
        options += ["--env", f"{key}={value}"]
    if timeout_seconds is not None:
        options += ["--timeout-seconds", str(int(timeout_seconds))]
    return [
        sys.executable,
        "-m",
        "tools.ops.research_runner.detached",
        "--state-dir",
        str(state_dir),
        "--cwd",
        str(cwd),
        *options,
        "--",
        *command,
    ]


def _parse_env(items: Sequence[str]) -> dict[str, str] | None:
    found: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key or not key.replace("_", "").isalnum():
            return None
        found[key] = value
    return found


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    output: IO[bytes],
    env: Mapping[str, str] | None,
    timeout: int | None,
) -> tuple[int, bool]:
    """`command` を走らせ、（終了コード, 時間切れか）を返す。"""
    child_env = None if not env else {**os.environ, **env}
    process = subprocess.Popen(  # noqa: S603 - 実行器が組み立てた固定のコマンド
        list(command),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        env=child_env,
    )
    try:
        return process.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        # 自分が起動した子だけを、その PID で止める（名前の型で探さない）。
        process.terminate()
        try:
            process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return EXIT_TIMED_OUT, True


def main(argv: Sequence[str] | None = None) -> int:
    given = list(sys.argv[1:] if argv is None else argv)
    if "--" not in given:
        print("`--` の後に走らせるコマンドを書く", file=sys.stderr)
        return 2
    split = given.index("--")
    parser = argparse.ArgumentParser(prog="python -m tools.ops.research_runner.detached")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=int, default=None)
    args = parser.parse_args(given[:split])
    command = given[split + 1 :]
    if not command:
        print("走らせるコマンドが無い", file=sys.stderr)
        return 2
    env = _parse_env(args.env)
    if env is None:
        print("--env は KEY=VALUE の形で書く", file=sys.stderr)
        return 2
    timeout: int | None = args.timeout_seconds
    if timeout is not None and timeout < 1:
        print("--timeout-seconds は 1 以上", file=sys.stderr)
        return 2
    state_dir: Path = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "pid.txt").write_text(f"{os.getpid()}\n", encoding="utf-8")
    timed_out = False
    with (state_dir / "output.log").open("wb") as output:
        try:
            code, timed_out = _run(command, cwd=args.cwd, output=output, env=env, timeout=timeout)
        except OSError as exc:
            output.write(f"起動できない: {exc}\n".encode())
            code = EXIT_NOT_STARTED
    if timed_out:
        (state_dir / TIMED_OUT_FILE).write_text(f"{timeout}\n", encoding="utf-8")
    (state_dir / "finished_at_utc.txt").write_text(_now() + "\n", encoding="utf-8")
    # 終了コードを最後に書く（あれば終わり。照合の (2)）。
    tmp = state_dir / "exit_code.txt.tmp"
    tmp.write_text(f"{code}\n", encoding="utf-8")
    os.replace(tmp, state_dir / "exit_code.txt")
    return code


if __name__ == "__main__":
    sys.exit(main())
