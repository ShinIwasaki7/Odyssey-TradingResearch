"""実行器の本体が外の世界に触れる口（差し替えられる。試験は偽物を渡す）。

実物は `adapters`（git・`gh`・プロセス・`vm_stat`・`odyssey-fx` のコマンド）、`workers`（作業役の
起動と出力の検査。PR 8b の 2 本目）、`gate`（関門の機械の項目。同）にある。

どの口も成績の数値を返さない（識別子・時刻・終了コード・合否・件数・ハッシュだけ）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from tools.ops.hypothesis_cards.generate import PreregChecks
from tools.ops.hypothesis_cards.repo import RepoView
from tools.ops.hypothesis_cards.screening import Verification

__all__ = [
    "Binding",
    "BundleOutOfScope",
    "ChangeOutput",
    "Checks",
    "Clock",
    "CommandNotAllowed",
    "Comment",
    "Evaluation",
    "GateCheck",
    "GateInputs",
    "GateResult",
    "Git",
    "GitHub",
    "IdeatorOutput",
    "MainCheckout",
    "PrState",
    "PrepareFailed",
    "Processes",
    "PullStatus",
    "ReportOutput",
    "System",
    "VerdictOutput",
    "WorkerOutput",
    "WorkerPoll",
    "WorkerRequest",
    "WorkerStatus",
    "WorkerUnavailable",
    "Workers",
    "Wrapper",
    "WrapperContract",
]


class Clock(Protocol):
    def now(self) -> datetime: ...


# --- GitHub ---------------------------------------------------------------------------


class PrState(Enum):
    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"


class Checks(Enum):
    """必須の検査の合否（まだ走っている・合格・どれかが不合格）。"""

    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class PullStatus:
    state: PrState
    checks: Checks
    merge_commit: str | None = None
    #: merge・閉じた時刻（UTC の `YYYY-MM-DDTHH:MM:SSZ`）。
    closed_at: str | None = None


@dataclass(frozen=True, slots=True)
class Comment:
    identifier: str
    body: str
    created_at: str


class GitHub(Protocol):
    """PR・issue の操作（`gh`）。管理者の権限で検査を飛ばす merge はしない（D25 §6.4）。"""

    def create_pr(self, *, branch: str, title: str, body: str) -> int: ...

    def pr_status(self, number: int) -> PullStatus: ...

    def enable_auto_merge(self, number: int) -> None:
        """merge のコミットの方式で自動 merge を指定する（`gh pr merge --auto --merge`）。"""
        ...

    def rerun_checks(self, number: int) -> None: ...

    def close_pr(self, number: int) -> None: ...

    def create_issue(self, *, title: str, body: str, label: str) -> int: ...

    def open_issues(self, title: str) -> tuple[int, ...]:
        """題が `title` と同じ開いた issue の番号（作った直後に止まった issue を見つける）。"""
        ...

    def comment(self, number: int, body: str) -> None: ...

    def comments(self, number: int) -> tuple[Comment, ...]:
        """issue のコメント（書かれた順）。"""
        ...

    def close_issue(self, number: int) -> None: ...


# --- git ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MainCheckout:
    """人間の主の作業木の様子（仮置き 22）。"""

    branch: str | None
    head: str
    clean: bool


class Git(Protocol):
    """git の操作（リポジトリ・実行用の作業木・主の作業木）。"""

    def fetch(self) -> None: ...

    def origin_main(self) -> str:
        """`origin/main` のコミット。"""
        ...

    def snapshot(self, rev: str) -> RepoView: ...

    def commit_files(self, *, base: str, files: Mapping[str, bytes | None], message: str) -> str:
        """`base` の木に `files` を当てたコミットを作る（作業木に触れない。`None` は削除）。"""
        ...

    def push_branch(self, commit: str, branch: str) -> None:
        """`runner/`・`candidate-fix/` の枝を `commit` に置く（実行器だけが書く枝）。"""
        ...

    def first_seen_times(self, ref: str, card_ids: Sequence[str]) -> dict[str, int]: ...

    def is_ancestor(self, ancestor: str, descendant: str) -> bool: ...

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]: ...

    # 実行用の作業木（D25-7 (a)）

    def ensure_exec_worktree(self) -> None: ...

    def exec_head(self) -> str: ...

    def exec_fast_forward(self, target: str) -> None: ...

    def exec_dirty_paths(self) -> tuple[str, ...]:
        """追跡しているファイルのうち、コミットされていない変更があるもの。"""
        ...

    def exec_commit(self, paths: Sequence[str], message: str) -> str: ...

    def exec_undo_commit(self) -> None:
        """直前の `exec_commit` を取り消す（ファイルの中身は作業木に残す）。"""
        ...

    def exec_push(self) -> None:
        """枝 `runner/exec` を origin に置く。"""
        ...

    def exec_untracked(self, prefix: str) -> tuple[str, ...]:
        """実行用の作業木の `prefix` の下の、追跡していないファイル（固定の記録を拾う）。"""
        ...

    # 人間の主の作業木（最終検証。仮置き 22・33）

    def main_checkout(self) -> MainCheckout: ...

    def main_fast_forward(self, target: str) -> None: ...

    def main_file_matches(self, path: str, rev: str) -> bool:
        """主の作業木の `path` の中身が `rev` の中身とバイト単位で同じか。"""
        ...

    def main_restore(self, path: str) -> None:
        """主の作業木の `path` を追跡の内容に戻す（中身が origin と同じと確かめた後だけ）。"""
        ...


# --- プロセス・計算機 -------------------------------------------------------------------


class Processes(Protocol):
    def alive(self, pid: int) -> bool: ...

    def is_group_leader(self, pid: int) -> bool: ...

    def signal_group(self, group: int, signal: int) -> None: ...

    def spawn(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        state_dir: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> int:
        """`command` を端末から切り離し、終了コードと終了時刻を `state_dir` に書く小さな包みで
        起動する（D25 §2.4）。包みの PID を返す。

        `env` は `command` にだけ足す環境変数、`timeout_seconds` は壁時計の時間の上限（包みが
        超えたら `command` のプロセスだけを自分が起動した PID で止め、時間切れの終了コードを書く。
        作業役だけが使う）。
        """
        ...


class System(Protocol):
    def boot_id(self) -> str: ...

    def free_disk_bytes(self, path: Path) -> int: ...

    def free_memory_bytes(self) -> int: ...


# --- 実行の包み（D26 の PR-B との約束） ------------------------------------------------


@dataclass(frozen=True, slots=True)
class WrapperContract:
    """包みが実行器の前提を満たすか（方式の引数・`run_pid` のプロセスグループの分離）。"""

    ready: bool
    problems: tuple[str, ...] = ()
    #: 同じ版の実行中の印のロックが取れない拒否の専用の終了コード（仮置き 36）。
    locked_exit_code: int | None = None


class Wrapper(Protocol):
    def contract(self) -> WrapperContract: ...

    def launch(
        self, *, experiment: str, worktree: Path, repo: Path, snapshots: Path, mode: str
    ) -> int:
        """`run_experiment` で 1 本を切り離して起動する（包みの終了コードを返す。0 = 起動した）。
        `snapshots` は審査・再現・最終検証と同じ snapshot の基点（包みの `--snapshots`）。"""
        ...


# --- 評価の基盤のコマンドと成果物の識別子 ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Binding:
    """探索の記録の束縛（`search/ledger_execution.json`）の識別子だけ。"""

    experiment_id: str
    execution: int


class PrepareFailed(Exception):
    """準備の確かめ（`prepare_search`）がカードの中身の理由で通らない。"""


class Evaluation(Protocol):
    def ledger_check(self, worktree: Path, out: Path) -> int:
        """`experiment ledger`（L0〜L11）の終了コード。"""
        ...

    def prepare(self, worktree: Path, verification: Verification) -> PreregChecks:
        """準備の確かめ。カードの理由なら `PrepareFailed`、道具なら `ToolFailure`。"""
        ...

    def binding(self, version_dir: Path) -> Binding | None: ...

    def run_ids(self, version_dir: Path) -> tuple[str, ...]:
        """版の試行ごとの run の識別子（自動の削除の対象。D25 §4.5）。"""
        ...

    def fix_available(self) -> bool:
        """固定の記録のコマンド（PR 4c）があるか。"""
        ...

    def declaration_required(self) -> bool:
        """審査・最終検証がまだ申告を求めるか（申告の撤去の PR 4b の前か）。"""
        ...


# --- 作業役（実物は `workers`） -----------------------------------------------------------


class WorkerUnavailable(Exception):
    """作業役を起動できない（定義が無い・役の名前が無い）。"""


class CommandNotAllowed(Exception):
    """口の実物が起動しようとしたコマンドが許可の一覧（`commands`）に無い（D25 §4.6 の
    `COST_RISK`）。口は起動せずにこの例外で知らせ、実行器は当たったカードを添えて全体の停止に
    する（`Runner._spawn` と同じ確かめ。作業役の起動と関門の機械の項目の 3 が通る）。"""


class BundleOutOfScope(Exception):
    """読む束を作る段で、許可の一覧の外（`data/`・最終検証と前向き検証の出力・手元の旧データの
    CSV など）のファイルを求められた（D25 §4.6 の `SEALED_NEEDED` の検出の仕方、§5.2 の表）。

    判定は束を作る道具（PR 8b の `Workers.launch` の中身）が行い、写す前にこの例外で知らせる。
    実行器は作業役を起動せず、当たったカードを添えて全体の停止（`SEALED_NEEDED`）にする。
    """


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    """作業役 1 回の起動（`role` は `ideator`・`analyst_change`・`analyst_report`・`verifier`）。"""

    role: str
    attempt: int
    card_id: str | None = None
    version: int | None = None
    #: 発想役が書けるカードの枚数（D25 §3.7）、分析役への追加の材料など。
    extra: Mapping[str, str] = field(default_factory=dict)


class WorkerStatus(Enum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    #: 出力なし・異常終了（`WORKER_FAILED`）。
    FAILED = "FAILED"
    #: 出力が規則の外（`CHANGE_OUT_OF_RULES` など。理由は `reason`）。
    REJECTED = "REJECTED"
    USAGE_LIMIT = "USAGE_LIMIT"
    #: 起動の印はあるが、プロセスも結果も無い（起動し直したときの照合の (4)）。
    LOST = "LOST"
    #: 読むだけのはずの束の写しが変わった・増えた・減った（書き込みの絞りが効いていない形跡）。
    #: 実行器はやり直さずに全体を止め、出力は使わない（人間の決定 245）。理由の説明は `reason`。
    BOUNDARY_BROKEN = "BOUNDARY_BROKEN"


@dataclass(frozen=True, slots=True)
class IdeatorOutput:
    cards: tuple[bytes, ...]
    #: 束の中で読めたファイルの一覧（カードの `read_scope` の検査に使う）。
    read_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangeOutput:
    """分析役の段 5 の出力（`decision` は `CHANGE`・`FINISH`・`AMBIGUOUS`）。"""

    decision: str
    raw: bytes
    memo_path: str
    memo_sha256: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReportOutput:
    """分析役の段 7 の出力（数字なしの文と、版管理の外の比較表の置き場とハッシュ）。"""

    prediction: str
    change_reasons: tuple[str, ...]
    change_contents: tuple[str, ...]
    comparison_path: str
    comparison_sha256: str


@dataclass(frozen=True, slots=True)
class VerdictOutput:
    passed: bool
    reasons: tuple[str, ...] = ()


WorkerOutput = IdeatorOutput | ChangeOutput | ReportOutput | VerdictOutput


@dataclass(frozen=True, slots=True)
class WorkerPoll:
    status: WorkerStatus
    output: WorkerOutput | None = None
    reason: str | None = None


class Workers(Protocol):
    """作業役の起動の口（実物は `workers.ClaudeWorkers`）。

    利用上限で終わった作業役は、回復の答えの後に**同じ** `request`（役・カード・版・試みの番号）で
    もう一度 `launch` されることがある。`launch` はその前の出力・結果を捨てて新しく起動し、`poll`
    が前の起動の利用上限の結果を返さないようにする（本 PR の仮置き）。
    """

    def launch(self, request: WorkerRequest) -> None:
        """起動する。束を作れない（定義が無い）なら `WorkerUnavailable`、束に許可の一覧の外の
        ファイルが要るなら写さずに `BundleOutOfScope`（`SEALED_NEEDED`）、起動のコマンドが許可の
        一覧に無ければ起動せずに `CommandNotAllowed`（`COST_RISK`）。"""
        ...

    def poll(self, request: WorkerRequest) -> WorkerPoll: ...


@dataclass(frozen=True, slots=True)
class GateResult:
    """関門の機械の項目 1〜5 の合否（D25 §5.5）。"""

    items: tuple[tuple[int, bool], ...]

    @property
    def passed(self) -> bool:
        return all(ok for _, ok in self.items)


@dataclass(frozen=True, slots=True)
class GateInputs:
    """関門の機械の項目に渡す、実行器の状態のうちの識別子だけ（成績の数値を持たない）。"""

    card_id: str
    experiment: str
    versions: tuple[int, ...]
    #: 版 → 事前登録の merge のコミット。
    prereg_commits: Mapping[str, str] = field(default_factory=dict)
    #: 版 → `{"experiment_id": <記録票の識別子>, "execution": <実行番号>}`。
    executions: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    #: 元の実験の再現の結果（`MATCH`・`MISMATCH`・`None`）。
    repro: str | None = None


class GateCheck(Protocol):
    def check(self, inputs: GateInputs) -> GateResult:
        """項目 1〜5 の合否。道具が動かなければ `ToolFailure`、項目 3 のコマンドが許可の一覧に
        無ければ `CommandNotAllowed`。"""
        ...
