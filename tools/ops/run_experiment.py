"""実行の包み: 探索の実験を 1 本走らせ、終わったら研究の画面を必ず作り直す。

設計は計画書 D14 §2.3.1（2026-10-28 の人間の決定。D14 §0.5 の 7 回目、仮置き 17〜22）。実行の
指示ごとに一時のスクリプトを書くと手順が抜ける（版 2 では画面の作り直しが抜けた）ので、手順を
この 1 本に固定する。

順に行うこと（書くファイルは ``<repo>/runs/experiments/<名前>/v<版>/`` の下）:

1. 実験設定（書式 v2）の ``id`` と ``version`` から版のディレクトリを決める。``launch.json`` が
   既にあれば拒否する（終了コード 2、何も書かない。仮置き 19）。
2. ``started_at_utc.txt`` と ``launch.json``（実行前の事実。仮置き 22）を書く。
3. 自分自身を端末から切り離した別のプロセス（新しいセッション）として起動し直し、起動した側は
   すぐに終わる。子のプロセスの標準出力と標準エラーは ``launcher.log``。``--foreground`` を
   付けると切り離さない。
4. ``caffeinate -i /usr/bin/time -l -o time.log uv run --frozen odyssey-fx experiment run …`` を
   走らせる。標準出力と標準エラーは ``console.log`` に保存し、読まない（判定が画面に出るため）。
5. ``exit_code.txt`` と ``finished_at_utc.txt``（開始と同じ ISO 8601 の形。仮置き 20）を書く。
6. run の終了コードによらず研究の画面を作り直してブラウザで開き、結果を
   ``viewer_exit_code.txt`` と ``viewer.log`` に残す（仮置き 21）。画面の道具が失敗しても run の
   扱いは変えない。

しないこと: 成績・``console.log``・レポート・画面の HTML を読むこと、台帳のコミットと push（人間が
行う。D14-11 (a)）、snapshot の事前確認、再現の実行（仮置き 18）、止まった run の再実行。

使い方（worktree の中で。D14 §2.3 の 3）::

    uv run python -m tools.ops.run_experiment \\
        --experiment configs/experiments/<名前>.yaml --repo <リポジトリ>

標準ライブラリと PyYAML だけを使う。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

#: 起動の拒否・設定の誤りの終了コード。
EXIT_REFUSED = 2
#: コマンドを起動できなかったときに書く終了コード（シェルの「見つからない」と同じ）。
EXIT_NOT_STARTED = 127


class LaunchError(Exception):
    """起動を拒否する理由（何も書かずに終わる）。"""


@dataclass(frozen=True)
class Plan:
    """1 回の実行の置き場とコマンドの材料。"""

    experiment: str
    experiment_id: str
    experiment_version: int
    repo: Path
    worktree: Path
    snapshots: Path
    version_dir: Path

    @property
    def launch_path(self) -> Path:
        return self.version_dir / "launch.json"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_from_args(args: argparse.Namespace) -> Plan:
    """実験設定を読み、版のディレクトリを決める（ファイルは書かない）。"""
    worktree = args.worktree.resolve()
    repo = args.repo.resolve()
    experiment_path = worktree / args.experiment
    try:
        data = yaml.safe_load(experiment_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LaunchError(f"実験設定を読めない: {experiment_path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise LaunchError(f"実験設定が対応表でない: {experiment_path}")
    experiment_id = data.get("id")
    version = data.get("version")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise LaunchError(f"実験設定に id が無い: {experiment_path}")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise LaunchError(f"実験設定の version が 1 以上の整数でない: {experiment_path}")
    snapshots = (args.snapshots or repo / "data" / "snapshots").resolve()
    version_dir = repo / "runs" / "experiments" / experiment_id / f"v{version}"
    return Plan(
        experiment=str(args.experiment),
        experiment_id=experiment_id,
        experiment_version=version,
        repo=repo,
        worktree=worktree,
        snapshots=snapshots,
        version_dir=version_dir,
    )


def build_run_command(plan: Plan) -> list[str]:
    """run のコマンド（版 1・版 2 と同じ形）。"""
    return [
        "caffeinate",
        "-i",
        "/usr/bin/time",
        "-l",
        "-o",
        str(plan.version_dir / "time.log"),
        "uv",
        "run",
        "--frozen",
        "odyssey-fx",
        "experiment",
        "run",
        "--experiment",
        plan.experiment,
        "--snapshots",
        str(plan.snapshots),
        "--out",
        str(plan.repo),
        "--repo-root",
        str(plan.worktree),
    ]


def build_viewer_command(plan: Plan) -> list[str]:
    """研究の画面を作り直して開くコマンド（D14 §2.3 の 5）。"""
    return [
        sys.executable,
        "-m",
        "tools.viewer.research_viewer",
        "--runs-root",
        str(plan.repo),
        "--repo-root",
        str(plan.worktree),
        "--open",
    ]


def _git(directory: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _line_count(path: Path) -> int | None:
    try:
        return len(path.read_bytes().splitlines())
    except OSError:
        return None


def launch_facts(plan: Plan, started_at: str, *, mode: str) -> dict[str, Any]:
    """`launch.json` の中身（機械的に取れる事実だけ。仮置き 22）。"""
    ledger = plan.worktree / "research" / "trial_ledger.jsonl"
    return {
        "experiment": plan.experiment,
        "experiment_version": plan.experiment_version,
        "started_at_utc": started_at,
        "mode": mode,
        "launcher_pid": None,
        "run_pid": None,
        "host": "local Mac",
        "cwd": str(plan.worktree),
        "command": build_run_command(plan),
        "viewer_command": build_viewer_command(plan),
        "console_log": str(plan.version_dir / "console.log"),
        "time_log": str(plan.version_dir / "time.log"),
        "exit_code_file": str(plan.version_dir / "exit_code.txt"),
        "finished_at_file": str(plan.version_dir / "finished_at_utc.txt"),
        "worktree": str(plan.worktree),
        "worktree_branch": _git(plan.worktree, "rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_commit": _git(plan.worktree, "rev-parse", "HEAD"),
        "main_checkout_commit": _git(plan.repo, "rev-parse", "HEAD"),
        "uv_lock_sha256": _sha256(plan.worktree / "uv.lock"),
        "ledger": str(ledger),
        "ledger_lines_before": _line_count(ledger),
        "ledger_sha256_before": _sha256(ledger),
        "note": "stdout/stderr are saved only; not read by the launcher (D14 §2.3.1).",
    }


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _update_launch(plan: Plan, **fields: Any) -> None:
    data = json.loads(plan.launch_path.read_text(encoding="utf-8"))
    data.update(fields)
    _write_json(plan.launch_path, data)


def prepare(plan: Plan, *, mode: str) -> None:
    """手順 1・2: 二重起動を拒否し、開始時刻と `launch.json` を書く。

    `launch.json` を排他的に作って起動を予約してから、ほかのファイルを書く。存在の確認と書き込み
    の間に別の起動が入り込むと、両方が受け付けられて記録が上書きされるため（PR #103 の 1 巡目）。
    """
    plan.version_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(plan.launch_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    except FileExistsError as exc:
        raise LaunchError(
            f"この版は既に起動されている（{plan.launch_path} がある）。再実行は人間の決定を待つ"
        ) from exc
    started_at = _now()
    (plan.version_dir / "started_at_utc.txt").write_text(started_at + "\n", encoding="utf-8")
    _write_json(plan.launch_path, launch_facts(plan, started_at, mode=mode))


def execute(plan: Plan) -> int:
    """手順 4〜6: run を走らせ、終了を記録し、研究の画面を作り直す。run の終了コードを返す。"""
    _update_launch(plan, launcher_pid=os.getpid())
    with (plan.version_dir / "console.log").open("wb") as console:
        try:
            process = subprocess.Popen(
                build_run_command(plan),
                cwd=plan.worktree,
                stdin=subprocess.DEVNULL,
                stdout=console,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            console.write(f"run を起動できない: {exc}\n".encode())
            code = EXIT_NOT_STARTED
        else:
            _update_launch(plan, run_pid=process.pid)
            code = process.wait()
    (plan.version_dir / "exit_code.txt").write_text(f"{code}\n", encoding="utf-8")
    (plan.version_dir / "finished_at_utc.txt").write_text(_now() + "\n", encoding="utf-8")
    refresh_viewer(plan)
    return code


def refresh_viewer(plan: Plan) -> int:
    """手順 6: 研究の画面を作り直す。失敗しても例外にしない（run の扱いを変えない）。"""
    try:
        completed = subprocess.run(
            build_viewer_command(plan),
            cwd=plan.worktree,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        code, stdout, stderr = EXIT_NOT_STARTED, "", f"画面の道具を起動できない: {exc}\n"
    else:
        code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    (plan.version_dir / "viewer.log").write_text(
        f"[stdout]\n{stdout}[stderr]\n{stderr}", encoding="utf-8"
    )
    (plan.version_dir / "viewer_exit_code.txt").write_text(f"{code}\n", encoding="utf-8")
    return code


def child_command(plan: Plan) -> list[str]:
    """子のプロセスのコマンド。子は worktree で動くので、置き場はすべて絶対パスで渡す。"""
    return [
        sys.executable,
        "-m",
        "tools.ops.run_experiment",
        "--experiment",
        plan.experiment,
        "--repo",
        str(plan.repo),
        "--worktree",
        str(plan.worktree),
        "--snapshots",
        str(plan.snapshots),
        "--child",
    ]


def spawn_child(plan: Plan) -> int:
    """手順 3: 端末から切り離した子のプロセスで手順 4〜6 を行う。子の PID を返す。"""
    with (plan.version_dir / "launcher.log").open("ab") as log:
        process = subprocess.Popen(
            child_command(plan),
            cwd=plan.worktree,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return process.pid


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--experiment", required=True, help="worktree からの実験設定のパス（書式 v2）"
    )
    parser.add_argument(
        "--repo", type=Path, required=True, help="メインの checkout の根（--out の置き場）"
    )
    parser.add_argument(
        "--worktree", type=Path, default=Path("."), help="実行する worktree（既定: いまの場所）"
    )
    parser.add_argument(
        "--snapshots", type=Path, help="snapshot の置き場（既定: <repo>/data/snapshots）"
    )
    parser.add_argument("--foreground", action="store_true", help="端末から切り離さずに走らせる")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = plan_from_args(args)
        if args.child:
            if not plan.launch_path.exists():
                raise LaunchError(f"{plan.launch_path} が無い（--child は包みが付ける）")
            return execute(plan)
        prepare(plan, mode="foreground" if args.foreground else "detached")
    except LaunchError as exc:
        print(f"起動しない: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if args.foreground:
        return execute(plan)
    try:
        pid = spawn_child(plan)
    except OSError as exc:
        # 何も走っていないので、この起動が書いたファイルを消して再起動できるようにする
        # （PR #103 の仮置き 5 の人間の決定。D14 仮置き 27）。
        for name in ("launch.json", "started_at_utc.txt", "launcher.log"):
            (plan.version_dir / name).unlink(missing_ok=True)
        print(f"起動しない: 切り離した実行を起動できない: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    # 成績は出さない（置き場と PID だけ）。
    print(f"実行を切り離して起動した: {plan.version_dir}（包みの PID {pid}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
