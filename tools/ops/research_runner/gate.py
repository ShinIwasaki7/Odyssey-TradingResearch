"""関門の機械の項目（D25 §5.5 の 1〜5）。

D22 の関門 X1〜X3・X5 の形のうち、見た成績の範囲の項目を除いたもの。読むものは成績の数値を
含まない部分だけ:

1. 全版の事前登録の merge のコミットが、その版の記録票の `git_commit` の祖先で、`git_dirty` が偽。
   読むのは実行器の状態の merge のコミットと、記録票 `experiment_manifest.json` の 2 つの項目だけ。
2. 台帳に全版の開始と結末の行があり、改善の版の系譜が前の版を指し、変更の種類が 1 つで、理由の
   文のダイジェストがある。読むのは試行台帳の行の種類と系譜の識別子だけ
   （`ledger_rows.lineage_of_execution`）。
3. 全版のレポートが保存済みの成果物から作り直せる（`experiment report` が終了コード 0）。
   終了コードだけを見て、出力は捨てる。
4. 元の実験の再現が一致する（決定 107）。実行器の状態の再現の結果。
5. 改善の版のレポートに「独立した確認ではない」の注記が出ている（決まった注記の文の有無だけ）。

出力は合否（`GateResult`）だけ。読めない・無いものはその項目の不合格にする（安全側）。項目 3 の
コマンドは、起動の前に許可の一覧（`commands.unlisted_command`）に当て、無ければ起動せずに
`CommandNotAllowed`（`COST_RISK`）。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.ops.hypothesis_cards.repo import RepoView
from tools.ops.research_runner.commands import unlisted_command
from tools.ops.research_runner.ledger_rows import lineage_of_execution
from tools.ops.research_runner.ports import CommandNotAllowed, GateInputs, GateResult

__all__ = ["NOT_INDEPENDENT_NOTE", "REPORT_TIMEOUT_SECONDS", "LocalGate", "run_quietly"]

#: 改善の版のレポートの判定の欄の定型の注記（D13 §7.3 の文言。`search_report` の文と同じ）。
NOT_INDEPENDENT_NOTE: Final = (
    "この判定の検証区間は、系譜の先行の実行が既に見た区間を含む（独立した確認ではない）"
)
TRIAL_LEDGER: Final = "research/trial_ledger.jsonl"
ODYSSEY: Final = ("uv", "run", "--frozen", "odyssey-fx")
#: レポートの作り直しの待ちの上限（本 PR の仮置き G8。「1 歩」のロックの 30 分より短く）。
REPORT_TIMEOUT_SECONDS: Final = 20 * 60


def run_quietly(command: Sequence[str], cwd: Path) -> int:
    """コマンドを走らせて終了コードだけを返す（出力は判定の値を含みうるので読まずに捨てる）。"""
    try:
        completed = subprocess.run(  # noqa: S603 - 実行器が組み立てた固定のコマンド
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=REPORT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127
    return completed.returncode


def _plain_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


@dataclass
class LocalGate:
    """`repo` は人間の主の作業木（`runs/` の根）、`exec_worktree` は台帳と登録簿を読む根。"""

    repo: Path
    exec_worktree: Path
    view: Callable[[], RepoView]
    is_ancestor: Callable[[str, str], bool]
    #: レポートの作り直しを走らせるコマンドの頭。
    odyssey: tuple[str, ...] = ODYSSEY
    run: Callable[[Sequence[str], Path], int] = run_quietly
    #: 許可の一覧の `odyssey-fx` の頭（実行器の `Ports.odyssey` と同じ。確かめはこちらに当てる）。
    listed_odyssey: tuple[str, ...] = ODYSSEY

    def _version_dir(self, experiment: str, version: int) -> Path:
        return self.repo / "runs" / "experiments" / experiment / f"v{version}"

    def check(self, inputs: GateInputs) -> GateResult:
        versions = tuple(inputs.versions)
        commands = [self._report_command(inputs.experiment, version) for version in versions]
        for command in commands:
            unlisted = unlisted_command(command, self.listed_odyssey)
            if unlisted is not None:
                raise CommandNotAllowed(unlisted)
        ledger = self.view().read(TRIAL_LEDGER)
        items = (
            (1, bool(versions) and all(self._item1(inputs, v) for v in versions)),
            (2, bool(versions) and self._item2(inputs, ledger)),
            (3, bool(versions) and all(self.run(c, self.exec_worktree) == 0 for c in commands)),
            (4, inputs.repro == "MATCH"),
            (5, all(self._item5(inputs.experiment, v) for v in versions if v >= 2)),
        )
        return GateResult(items)

    def _report_command(self, experiment: str, version: int) -> list[str]:
        return [
            *self.odyssey,
            "experiment",
            "report",
            "--experiment-dir",
            str(self._version_dir(experiment, version)),
            "--repo-root",
            str(self.exec_worktree),
        ]

    def _item1(self, inputs: GateInputs, version: int) -> bool:
        prereg = inputs.prereg_commits.get(str(version))
        manifest = self._version_dir(inputs.experiment, version) / "experiment_manifest.json"
        if not prereg or not _plain_file(manifest):
            return False
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        commit, dirty = data.get("git_commit"), data.get("git_dirty")
        if not isinstance(commit, str) or not commit or dirty is not False:
            return False
        return self.is_ancestor(prereg, commit)

    def _item2(self, inputs: GateInputs, ledger: bytes | None) -> bool:
        previous: tuple[str, int] | None = None
        for version in inputs.versions:
            info = inputs.executions.get(str(version)) or {}
            record = info.get("experiment_id")
            execution = info.get("execution")
            if not isinstance(record, str) or not isinstance(execution, int):
                return False
            found = lineage_of_execution(ledger, record, execution)
            if not found.started or not found.finished:
                return False
            if version >= 2 and (
                previous is None
                or found.derived_from != previous
                or len(found.change_kinds) != 1
                or not found.has_note_digest
            ):
                return False
            previous = (record, execution)
        return True

    def _item5(self, experiment: str, version: int) -> bool:
        report = self._version_dir(experiment, version) / "report.md"
        if not _plain_file(report):
            return False
        try:
            return NOT_INDEPENDENT_NOTE in report.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
