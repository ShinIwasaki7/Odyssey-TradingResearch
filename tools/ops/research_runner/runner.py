"""実行器の本体: 「1 歩進める」（D25 §2.4、決定 186）と状態の機械（表 2-1・表 2-2）。

1 回の `step` で、全体の状態とカードごとの状態を読み、進められる段を順に進め、状態を書いて終わる。
長く待つ処理（探索の run・再現・審査・固定の前の確かめ・最終検証・作業役）はすべて別のプロセスに
切り離し、次の呼び出しでその終わりを見る（起動し直したときの照合。D25 §2.4）。

1 歩の並び:

1. `runs/runner/step.lock` を排他的に作る（作れなければ何もせずに終わる）。
2. 状態を読み、起動の時刻が変わっていれば（G2）記録する（照合は各段が毎回行う）。
3. 前提を確かめる（実行の包みが D26 の PR-B の後か・研究ポリシーの現行が第 5 版か）。
   そろっていなければ全体の停止（`TOOL_FAILURE`）にし、新しい起動をしない。
4. 関所 2 の答え（issue の番号のコメント）と自動で解ける理由の確かめを当てる。
5. カードごとに今の段を進める（表 2-1）。実行記録の定型 PR（枝 `runner/exec`）を進める。
6. 空き（スロット）を、起動待ち（C3）→ 審査の予約 → 最終検証の予約 → 再現の予約 の順に渡す
   （起動の前の確かめ。D25 §4.4・§4.5）。
7. 取り出し（D25 §3.6）、発想役（§3.7）、週の要約（§7）。

全体の停止（G1）の間は、走っている処理の見張り・PR の状態・答えの読込と自動の確かめだけを行い、
新しい起動（run・審査・最終検証・再現・作業役・取り出し）をしない（表 2-2）。

実行器は台帳に書かない（書くのはコマンドだけ）。成績の数値を issue・PR 本文・版管理の記録に
写さない。規則に無い判断は止まって問う。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import signal
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import yaml

from tools.ops.hypothesis_cards.card import CardFormatError, parse_card
from tools.ops.hypothesis_cards.emit import dump_document
from tools.ops.hypothesis_cards.generate import (
    Preregistration,
    change_path,
    experiment_path,
    prereg_note,
    prereg_note_path,
    run_record_path,
    select_policy,
)
from tools.ops.hypothesis_cards.queue import (
    ai_capacity,
    available_card_id,
    card_path,
    order_cards,
    outcome_bytes,
    outcome_path,
    queue_from,
    queued_cards,
)
from tools.ops.hypothesis_cards.repo import LOOPS_DIR, RepoView, ToolFailure
from tools.ops.hypothesis_cards.screening import CARD_PREREG_REJECTED, check_change, screen_card
from tools.ops.research_runner.events import EventLog
from tools.ops.research_runner.ledger_rows import has_candidate_row, review_result
from tools.ops.research_runner.ports import (
    ChangeOutput,
    Checks,
    Clock,
    Evaluation,
    GateCheck,
    Git,
    GitHub,
    IdeatorOutput,
    PrepareFailed,
    Processes,
    PrState,
    PullStatus,
    ReportOutput,
    System,
    VerdictOutput,
    WorkerPoll,
    WorkerRequest,
    Workers,
    WorkerStatus,
    WorkerUnavailable,
    Wrapper,
)
from tools.ops.research_runner.reasons import Action, Scope, options_for, reason
from tools.ops.research_runner.resources import (
    DISK_FLOOR_BYTES,
    MAX_SLOTS,
    estimate_seconds,
    plan_slots,
)
from tools.ops.research_runner.runs import move_attempt, read_run
from tools.ops.research_runner.state import (
    CardPhase,
    CardState,
    GlobalState,
    OpenReason,
    StateStore,
    format_time,
    parse_time,
)
from tools.ops.research_runner.texts import (
    STOP_LABEL,
    WEEKLY_LABEL,
    WEEKLY_TITLE,
    PullText,
    WeeklyCounts,
    cards_pull,
    exec_pull,
    final_pull,
    fix_pull,
    parse_answer,
    prereg_pull,
    question_body,
    question_title,
    report_pull,
    weekly_body,
)

__all__ = ["Paths", "Ports", "Runner", "StepOutcome"]

TRIAL_LEDGER: Final = "research/trial_ledger.jsonl"
REVIEW_LEDGER: Final = "research/adoption_reviews.jsonl"
FINAL_LEDGER: Final = "research/final_validations.jsonl"
LEDGER_LOCKS: Final = (
    "research/trial_ledger.lock",
    "research/adoption_reviews.lock",
)
CANDIDATE_FIXES: Final = "research/candidate_fixes/"
#: 2 本目の起動の前の差分に許す置き場（D25 §4.4 の 4）。
PREREG_PLACES: Final = ("configs/strategies/", "configs/experiments/", "research/")
#: 研究ポリシーの現行がこの版より前なら取り出しを始めない（仮置き 10）。
POLICY_FLOOR: Final = 5
#: 起動した直後の包み・小さな包みが PID を書くまでの猶予（本 PR の仮置き）。
LAUNCH_GRACE: Final = timedelta(minutes=10)
#: ロックの拒否の後に待つ時間（単位 1 つ分。仮置き 36）。
LOCKED_WAIT: Final = timedelta(minutes=15)
#: 主の作業木の食い違いを待つ長さ（仮置き 32）。
MAIN_WAIT: Final = timedelta(days=1)
#: 固定の延期の後にやり直す間隔（本 PR の仮置き。D15-15 (a) の「次の定期取得の承認の後」の代わり）。
FIX_DEFER: Final = timedelta(days=1)
FOUR_WEEKS: Final = timedelta(days=28)
JST: Final = ZoneInfo("Asia/Tokyo")
RUNNER_BY: Final = "research_runner"
#: 最終検証の終了コード（D09 §9.10.6）: 状態 A（消費の行が無い）、B（ロックが残る）、C（消費の後）。
FINAL_STATE_A: Final = frozenset({2, 7, 8})
#: 失敗の種類（D25 §4.6 の `SAME_FAILURE_TWICE`）。
FAIL_PREREG: Final = "prereg"
FAIL_RUN: Final = "run"
FAIL_LEDGER: Final = "ledger"
FAIL_CI: Final = "ci"
_MAX_RSS: Final = re.compile(r"([0-9]+)\s+maximum resident set size")
_TERMINAL: Final = frozenset({CardPhase.C14.name})


@dataclass(frozen=True, slots=True)
class Paths:
    """実行器の置き場。`repo` は人間の主の作業木（`--out` の基点と `runs/`）。"""

    repo: Path
    exec_worktree: Path
    snapshots: Path

    @property
    def runner_dir(self) -> Path:
        return self.repo / "runs" / "runner"

    @property
    def state_dir(self) -> Path:
        return self.runner_dir / "state"

    @property
    def lock_path(self) -> Path:
        return self.runner_dir / "step.lock"

    @property
    def events_path(self) -> Path:
        return self.runner_dir / "log" / "events.jsonl"

    @property
    def deleted_dir(self) -> Path:
        return self.runner_dir / "deleted"

    def proc_dir(self, card: str, name: str, attempt: int) -> Path:
        return self.state_dir / "procs" / card / name / str(attempt)

    def version_dir(self, experiment: str, version: int) -> Path:
        return self.repo / "runs" / "experiments" / experiment / f"v{version}"

    def repro_out(self, experiment: str, version: int) -> Path:
        """再現の出力の基点（自動の削除の対象。`runs/runner/` の外。本 PR の仮置き）。"""
        return self.repo / "runs" / "reproductions" / experiment / f"v{version}"


@dataclass(frozen=True, slots=True)
class Ports:
    clock: Clock
    git: Git
    github: GitHub
    procs: Processes
    system: System
    wrapper: Wrapper
    evaluation: Evaluation
    workers: Workers
    gate: GateCheck
    #: `odyssey-fx` を走らせるコマンドの頭（既定 `uv run --frozen odyssey-fx`）。
    odyssey: tuple[str, ...] = ("uv", "run", "--frozen", "odyssey-fx")


@dataclass(frozen=True, slots=True)
class StepOutcome:
    ran: bool
    notes: tuple[str, ...] = ()


def _phase(card: CardState) -> CardPhase:
    return CardPhase[card.phase]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _Context:
    state: GlobalState
    cards: dict[str, CardState]
    now: datetime
    origin: str
    notes: list[str] = field(default_factory=list)
    view_cache: dict[str, RepoView] = field(default_factory=dict)


class Runner:
    """「1 歩進める」の本体。"""

    def __init__(self, paths: Paths, ports: Ports) -> None:
        self.paths = paths
        self.ports = ports
        self.store = StateStore(paths.state_dir)
        self.events = EventLog(paths.events_path)
        #: 研究ポリシーの現行の版の下限（仮置き 10。試験だけが変える）。
        self.policy_floor = POLICY_FLOOR

    # --- 1 歩 -----------------------------------------------------------------------

    def step(self) -> StepOutcome:
        from tools.ops.research_runner.lock import StepLock

        lock = StepLock(self.paths.lock_path, self.ports.clock.now, self.ports.procs.alive)
        if not lock.acquire():
            return StepOutcome(False, ("別の「1 歩」が走っている",))
        try:
            return self._step()
        finally:
            lock.release()

    def _step(self) -> StepOutcome:
        ports = self.ports
        now = ports.clock.now()
        state = self.store.load_global() or GlobalState()
        boot = ports.system.boot_id()
        restarted = bool(state.boot_id) and state.boot_id != boot
        state.boot_id = boot
        ports.git.fetch()
        ports.git.ensure_exec_worktree()
        ctx = _Context(
            state=state,
            cards={card_id: self.store.load_card(card_id) for card_id in state.cards},
            now=now,
            origin=ports.git.origin_main(),
        )
        if restarted:
            ctx.notes.append("起動し直した（G2 → 各カードに e17 を当てる）")
        self._preconditions(ctx)
        self._pause_request(ctx)
        self._answers(ctx)
        self._auto_checks(ctx)
        for card in self._active(ctx):
            self._advance(ctx, card)
        for stage in (
            self._exec_flow,
            self._cards_pr,
            self._schedule,
            self._intake,
            self._ideator,
            self._weekly,
        ):
            try:
                stage(ctx)
            except ToolFailure as exc:
                ctx.notes.append(f"道具の失敗（次の 1 歩でやり直す）: {exc}")
            finally:
                for card in ctx.cards.values():
                    self.store.save_card(card)
                self.store.save_global(ctx.state)
        state.last_step_at = format_time(now)
        for card in ctx.cards.values():
            self.store.save_card(card)
        self.store.save_global(state)
        return StepOutcome(True, tuple(ctx.notes))

    def _active(self, ctx: _Context) -> list[CardState]:
        return [
            card
            for card in ctx.cards.values()
            if card.phase not in _TERMINAL or card.pr is not None or card.ending is not None
        ]

    def _view(self, ctx: _Context) -> RepoView:
        if ctx.origin not in ctx.view_cache:
            ctx.view_cache[ctx.origin] = self.ports.git.snapshot(ctx.origin)
        return ctx.view_cache[ctx.origin]

    def _pr_status(self, ctx: _Context, number: int) -> PullStatus:
        """PR の様子。merge を見たら origin を読み直す（同じ 1 歩の中で続けて進めるため）。"""
        status = self.ports.github.pr_status(number)
        if status.state is PrState.MERGED and status.merge_commit not in ctx.view_cache:
            self.ports.git.fetch()
            ctx.origin = self.ports.git.origin_main()
        return status

    def _stopped(self, ctx: _Context) -> bool:
        return bool(ctx.state.reasons)

    # --- 前提 ------------------------------------------------------------------------

    def _preconditions(self, ctx: _Context) -> None:
        """包みが PR-B の後か・研究ポリシーの現行が第 5 版か（D25 §8、仮置き 10）。"""
        problems = list(self.ports.wrapper.contract().problems)
        try:
            version = select_policy(self._view(ctx)).version
        except ToolFailure as exc:
            problems.append(f"研究ポリシーを読めない: {exc}")
        else:
            if version < self.policy_floor:
                problems.append(f"研究ポリシーの現行が第 {self.policy_floor} 版より前")
        if problems:
            self._stop_global(ctx, "TOOL_FAILURE_GLOBAL", "；".join(problems))

    # --- 止まる・問う --------------------------------------------------------------------

    def _ask(self, ctx: _Context, identifier: str, card: CardState | None, detail: str) -> int:
        row = reason(identifier)
        stage = CardPhase[card.phase].value if card is not None else "全体"
        body = question_body(
            question=row.question,
            reason=identifier,
            card=card.card_id if card is not None else None,
            stage=stage,
            version=card.version if card is not None else None,
            at=format_time(ctx.now),
            options=options_for(identifier, version=card.version if card is not None else None),
        )
        self.events.add(ctx.now, "stop_new", reason=identifier)
        return self.ports.github.create_issue(
            title=question_title(identifier, card.card_id if card is not None else None),
            body=body,
            label=STOP_LABEL,
        )

    def _stop_global(
        self, ctx: _Context, identifier: str, detail: str = "", *, card: str | None = None
    ) -> None:
        existing = ctx.state.find_reason(identifier)
        if existing is not None:
            return
        issue = self._ask(ctx, identifier, None, detail)
        ctx.state.reasons.append(
            OpenReason(identifier, issue, detail, format_time(ctx.now), card=card)
        )
        ctx.notes.append(f"全体の停止: {identifier}")

    def _stop_card(self, ctx: _Context, card: CardState, identifier: str, detail: str = "") -> None:
        """カードの停止（C13。C4・C5 では状態を変えずに理由の集合に足す。T16）。"""
        if card.find_reason(identifier) is None:
            issue = self._ask(ctx, identifier, card, detail)
            card.reasons.append(OpenReason(identifier, issue, detail, format_time(ctx.now)))
        phase = _phase(card)
        if phase not in {CardPhase.C4, CardPhase.C5, CardPhase.C13}:
            card.return_phase = card.phase
            card.phase = CardPhase.C13.name
        if reason(identifier).scope is Scope.BOTH and ctx.state.find_reason(identifier) is None:
            # 全体の集合にも足す（問いはカードの issue 1 つ。全体の側は自動の確かめで外す）。
            ctx.state.reasons.append(
                OpenReason(identifier, None, detail, format_time(ctx.now), card=card.card_id)
            )
        ctx.notes.append(f"カードの停止: {card.card_id} {identifier}")

    def _settle(self, ctx: _Context, card: CardState) -> None:
        """C4 の run の後に、遅れて足された理由があれば C13 へ（T16 の「run の終わりの後」）。"""
        if card.reasons and _phase(card) not in {CardPhase.C4, CardPhase.C5, CardPhase.C13}:
            card.return_phase = card.phase
            card.phase = CardPhase.C13.name

    def _release_global(self, ctx: _Context, identifier: str) -> None:
        item = ctx.state.find_reason(identifier)
        if item is None:
            return
        ctx.state.reasons.remove(item)
        if item.issue is not None:
            self.ports.github.close_issue(item.issue)

    def _release_card(
        self, ctx: _Context, card: CardState, identifier: str, *, to: str | None = None
    ) -> None:
        """カードの理由を 1 つ外す。集合が空になったら `to`（既定は止まる前の状態）へ（T39）。"""
        item = card.find_reason(identifier)
        if item is None:
            return
        card.reasons.remove(item)
        if item.issue is not None:
            self.ports.github.close_issue(item.issue)
        if to is not None:
            card.resume_to = to
        if not card.reasons and _phase(card) is CardPhase.C13:
            card.phase = card.resume_to or card.return_phase or CardPhase.C14.name
            card.return_phase = None
            card.resume_to = None

    def _retry_or_stop(
        self, ctx: _Context, card: CardState, identifier: str, key: str | None = None
    ) -> bool:
        """再試行 1 回の型（D25 §4.6.1、仮置き 32）。再試行するなら `True`。"""
        slot = key or identifier
        if card.retries.get(slot, 0) == 0:
            card.retries[slot] = 1
            return True
        self._stop_card(ctx, card, identifier)
        return False

    def _count_failure(self, ctx: _Context, kind: str) -> None:
        streaks = ctx.state.failure_streaks
        streaks[kind] = streaks.get(kind, 0) + 1
        if streaks[kind] >= 2:
            self._stop_global(ctx, "SAME_FAILURE_TWICE", kind)

    def _clear_failure(self, ctx: _Context, kind: str) -> None:
        ctx.state.failure_streaks[kind] = 0

    # --- 関所 2 の答え ----------------------------------------------------------------------

    def _new_answers(self, item: OpenReason, choices: int) -> list[tuple[str, int, str]]:
        """問いの後に書かれた、まだ読んでいない番号の答え（書かれた順）。"""
        if item.issue is None:
            return []
        found: list[tuple[str, int, str]] = []
        passed = item.seen_comment is None
        for comment in self.ports.github.comments(item.issue):
            if not passed:
                passed = comment.identifier == item.seen_comment
                continue
            number = parse_answer(comment.body, choices=choices)
            if number is not None:
                found.append((comment.identifier, number, comment.created_at))
            item.seen_comment = comment.identifier
        return found

    def _answers(self, ctx: _Context) -> None:
        for item in list(ctx.state.reasons):
            options = options_for(item.reason)
            for _, number, at in self._new_answers(item, len(options)):
                ctx.state.last_human_answer_at = at
                item.answer = number
                if self._apply_global(ctx, item, options[number - 1].action):
                    break
        for card in ctx.cards.values():
            for item in list(card.reasons):
                options = options_for(item.reason, version=card.version)
                for _, number, at in self._new_answers(item, len(options)):
                    ctx.state.last_human_answer_at = at
                    item.answer = number
                    if self._apply_card(ctx, card, item, options[number - 1].action):
                        break

    def _apply_global(self, ctx: _Context, item: OpenReason, action: Action) -> bool:
        """全体の理由への答え（D25 §4.7 の表）。状態が動いたら `True`。"""
        if action is Action.KEEP:
            return False
        if action is Action.RESET_FAILURES:
            ctx.state.failure_streaks = {}
        elif action is Action.RESET_REJECTS:
            ctx.state.reject_streak = 0
        elif action is Action.RECHECK_DISK:
            if self.ports.system.free_disk_bytes(self.paths.repo) < DISK_FLOOR_BYTES:
                return False
        elif action is Action.IDEATE_ONCE:
            ctx.state.ideator["extra"] = 1
        elif action is Action.END_CARD_AND_RESUME and item.card in ctx.cards:
            self._begin_end(ctx, ctx.cards[item.card], item.reason)
        elif action is Action.RESUME and item.reason == "TOOL_FAILURE_GLOBAL":
            # 前提を確かめ直す（そろっていなければ、この 1 歩の前提の確かめで既に足されている）。
            pass
        self._release_global(ctx, item.reason)
        return True

    def _apply_card(self, ctx: _Context, card: CardState, item: OpenReason, action: Action) -> bool:
        """カードの理由への答え（D25 §4.7 の表の右端の列）。状態が動いたら `True`。"""
        identifier = item.reason
        if action is Action.KEEP:
            return False
        if action in {Action.END_CARD, Action.END_RUN}:
            return self._begin_end(ctx, card, identifier)
        if action is Action.RESUME_RUN:
            run = card.run or {}
            pid = run.get("run_pid")
            if isinstance(pid, int):
                self.ports.procs.signal_group(pid, signal.SIGCONT)
            paused_since = run.get("paused_since")
            if paused_since:
                active = self._active_seconds(card, ctx.now)
                run["paused_seconds"] = int(run.get("paused_seconds", 0)) + int(
                    (ctx.now - parse_time(paused_since)).total_seconds()
                )
                run["paused_since"] = None
                run["limit_seconds"] = active + int(run["estimate_seconds"])
            card.run = run
            card.reasons.remove(item)
            if item.issue is not None:
                self.ports.github.close_issue(item.issue)
            # 他の理由（遅れて届いた再現の不一致など）は集合に残り、run の終わりの後に
            # 止まる（T16）。
            card.phase = CardPhase.C4.name
            return True
        if action is Action.OTHER_CHANGE:
            if identifier == "CONVERSION_STALE" and card.version >= 3:
                return False
            if identifier == "AMBIGUOUS_RULE" and card.return_phase != CardPhase.C7.name:
                return False  # 分析・改善の段で止まったときだけ選べる
            card.redo_note = f"{identifier}: 前の変更の提案は規則で決められなかった"
            card.procs.pop("worker", None)
            self._release_card(ctx, card, identifier, to=CardPhase.C7.name)
            return True
        if action is Action.RERUN_SAME_VERSION:
            return self._rerun_same_version(ctx, card, identifier)
        if action is Action.REDO_STAGE:
            card.retries.pop(identifier, None)
            card.procs.pop("worker", None)
            self._release_card(ctx, card, identifier)
            return True
        if action is Action.LEDGER_REDO:
            self._release_card(ctx, card, identifier, to=CardPhase.C6.name)
            self._request_exec(ctx, card, "run")
            return True
        if action is Action.REWRITE_REPORT:
            card.verifier_failures = 0
            card.checks = {}
            card.procs.pop("worker", None)
            self._release_card(ctx, card, identifier, to=CardPhase.C8.name)
            return True
        if action is Action.REDO_REVIEW:
            card.procs.pop("review", None)
            card.retries.pop("REVIEW_FAILED", None)
            if "review" not in card.reservations:
                card.reservations.append("review")
            self._release_card(ctx, card, identifier, to=CardPhase.C10.name)
            return True
        if action is Action.RECHECK:
            card.procs.pop("fix", None)
            card.main_wait_since = None
            self._release_card(ctx, card, identifier)
            return True
        if action is Action.FINAL_RECOVERED:
            info = card.executions.get(str(card.version), {})
            data = self._main_final_ledger()
            if not has_candidate_row(
                data, str(info.get("experiment_id")), int(info.get("execution", 0))
            ):
                return False
            card.checks["final_recovered"] = True
            self._release_card(ctx, card, identifier, to=CardPhase.C12.name)
            return True
        if action is Action.FINAL_RESTART:
            if self._final_consumed_on_origin(ctx, card):
                return False
            card.procs.pop("final", None)
            if "final" not in card.reservations:
                card.reservations.append("final")
            self._release_card(ctx, card, identifier, to=CardPhase.C12.name)
            return True
        return False

    # --- 自動で解ける理由（D25 §4.7 の「自動」） ------------------------------------------

    def _auto_checks(self, ctx: _Context) -> None:
        state = ctx.state
        if state.find_reason("TOOL_FAILURE_GLOBAL") is not None and self._preconditions_ok(ctx):
            self._release_global(ctx, "TOOL_FAILURE_GLOBAL")
        if state.find_reason("DISK_LOW") is not None:
            if self.ports.system.free_disk_bytes(self.paths.repo) >= DISK_FLOOR_BYTES:
                self._release_global(ctx, "DISK_LOW")
        if state.find_reason("QUEUE_EMPTY") is not None and queued_cards(self._view(ctx)):
            self._release_global(ctx, "QUEUE_EMPTY")
        if state.find_reason("LEDGER_CHECK_FAILED") is not None:
            pending = any(
                card.find_reason("LEDGER_CHECK_FAILED") is not None for card in ctx.cards.values()
            )
            dirty = [p for p in self.ports.git.exec_dirty_paths() if p in _LEDGER_FILES]
            if not pending and not dirty and state.exec_pr is None:
                self._release_global(ctx, "LEDGER_CHECK_FAILED")
        for card in ctx.cards.values():
            ids = card.reason_ids()
            if "CI_FAILED" in ids and card.pr is not None:
                status = self._pr_status(ctx, int(card.pr["number"]))
                if status.state is PrState.MERGED:
                    self._release_card(ctx, card, "CI_FAILED")
            if "CI_FAILED" in ids and card.pr is None and ctx.state.exec_pr is not None:
                if ctx.state.exec_pr.get("card") == card.card_id:
                    status = self._pr_status(ctx, int(ctx.state.exec_pr["number"]))
                    if status.state is PrState.MERGED:
                        self._release_card(ctx, card, "CI_FAILED")
            if "FIXATION_TOOL_MISSING" in ids and self.ports.evaluation.fix_available():
                self._release_card(ctx, card, "FIXATION_TOOL_MISSING", to=CardPhase.C11.name)
            if (
                "REVIEW_NEEDS_DECLARATION" in ids
                and not self.ports.evaluation.declaration_required()
            ):
                card.procs.pop("review", None)
                if "review" not in card.reservations and card.return_phase == CardPhase.C10.name:
                    card.reservations.append("review")
                self._release_card(ctx, card, "REVIEW_NEEDS_DECLARATION")
            if "LEDGER_CHECK_FAILED" in ids and card.checks.get("ledger_committed"):
                self._release_card(ctx, card, "LEDGER_CHECK_FAILED")

    def _preconditions_ok(self, ctx: _Context) -> bool:
        if not self.ports.wrapper.contract().ready:
            return False
        try:
            return select_policy(self._view(ctx)).version >= self.policy_floor
        except ToolFailure:
            return False

    # --- 人間の割り込み（g4） ---------------------------------------------------------------

    def _pause_request(self, ctx: _Context) -> None:
        """週の要約の issue に「止めて」だけのコメントがあれば `HUMAN_PAUSE`（本 PR の仮置き）。"""
        state = ctx.state
        if state.weekly_issue is None:
            return
        passed = state.weekly_seen_comment is None
        for comment in self.ports.github.comments(state.weekly_issue):
            if not passed:
                passed = comment.identifier == state.weekly_seen_comment
                continue
            state.weekly_seen_comment = comment.identifier
            if comment.body.strip() == "止めて":
                state.last_human_answer_at = comment.created_at
                self._stop_global(ctx, "HUMAN_PAUSE")

    # --- カードの段 -----------------------------------------------------------------------

    def _advance(self, ctx: _Context, card: CardState) -> None:
        handlers: dict[CardPhase, Callable[[_Context, CardState], None]] = {
            CardPhase.C1: self._c1_screen,
            CardPhase.C2: self._c2_prereg_pr,
            CardPhase.C4: self._c4_running,
            CardPhase.C5: self._c5_paused,
            CardPhase.C7: self._c7_analysis,
            CardPhase.C8: self._c8_report,
            CardPhase.C9: self._c9_verify,
            CardPhase.C10: self._c10_review,
            CardPhase.C11: self._c11_fix,
            CardPhase.C12: self._c12_final,
            CardPhase.C14: self._c14_done,
        }
        try:
            self._poll_repro(ctx, card)
            if card.ending is not None:
                self._finish_end(ctx, card)
                return
            for _ in range(6):
                before = card.phase
                handler = handlers.get(_phase(card))
                if handler is not None:
                    handler(ctx, card)
                self._settle(ctx, card)
                if card.phase == before:
                    break
        except ToolFailure as exc:
            # git・gh・コマンドの一時の失敗: このカードの道具の失敗として数える（T4・D25 §4.6.1）。
            self._tool_failure(ctx, card, str(exc))
        finally:
            # 外に作った PR・プロセスを状態に残す（次の 1 歩で同じものを作り直さない）。
            self.store.save_card(card)

    # C1 検査中（T1〜T4）

    def _c1_screen(self, ctx: _Context, card: CardState) -> None:
        view = self._view(ctx)
        queue_path = card_path("queue", card.card_id)
        raw = view.read(queue_path)
        if raw is None:
            self._tool_failure(ctx, card, f"{queue_path} が origin/main に無い")
            return
        card.card_sha256 = _sha(raw)
        try:
            screening = screen_card(view, raw, path=queue_path)
        except ToolFailure as exc:
            self._tool_failure(ctx, card, str(exc))
            return
        if screening.card is not None:
            card.symbol = screening.card.symbol
            card.experiment = screening.card.experiment_id
        if not screening.passed or screening.prereg is None or screening.verification is None:
            self._reject(ctx, card, screening.reason or CARD_PREREG_REJECTED, source="queue")
            return
        try:
            checks = self.ports.evaluation.prepare(self.paths.exec_worktree, screening.verification)
        except PrepareFailed:
            self._reject(ctx, card, CARD_PREREG_REJECTED, source="queue")
            return
        except ToolFailure as exc:
            self._tool_failure(ctx, card, str(exc))
            return
        prereg = screening.prereg
        files: dict[str, bytes | None] = dict(prereg.files)
        files[prereg_note_path(card.card_id, 1)] = prereg_note(prereg, checks)
        files[card_path("taken", card.card_id)] = raw
        files[queue_path] = None
        number = self._open_routine_pr(
            ctx,
            files,
            prereg_pull(
                card=card.card_id,
                version=1,
                experiment=prereg.experiment_id,
                at=format_time(ctx.now),
                trials=prereg.trial_count,
                files={path: body for path, body in files.items() if body is not None},
            ),
        )
        card.version = 1
        card.trial_counts["1"] = prereg.trial_count
        card.pr = {"kind": "prereg", "number": number, "version": 1}
        card.retries.pop("TOOL_FAILURE", None)
        card.phase = CardPhase.C2.name

    def _tool_failure(self, ctx: _Context, card: CardState, detail: str) -> None:
        """`TOOL_FAILURE`: 1 回目は同じ状態のまま次の 1 歩でやり直す（T4）。"""
        if self._retry_or_stop(ctx, card, "TOOL_FAILURE"):
            ctx.notes.append(f"{card.card_id}: 道具の失敗（やり直す）: {detail}")

    def _reject(self, ctx: _Context, card: CardState, identifier: str, *, source: str) -> None:
        """棄却の型（T3・T23・T25）: カードを done/ に移す定型 PR を作り、次のカードへ。"""
        if identifier == CARD_PREREG_REJECTED:
            self._count_failure(ctx, FAIL_PREREG)
        self._open_done_pr(ctx, card, identifier, source=source)
        card.outcome = identifier
        card.phase = CardPhase.C14.name
        self.events.add(ctx.now, "card_rejected", card=card.card_id, reason=identifier)
        ctx.state.reject_streak += 1
        self._after_done(ctx)
        if ctx.state.reject_streak >= 3:
            self._stop_global(ctx, "REJECT_THREE_IN_A_ROW")

    def _after_done(self, ctx: _Context) -> None:
        """カードが done/ に移った: 発想役を最大 1 回起動する予約（D25 §3.7 の (1)）。"""
        ctx.state.ideator["after_done"] = 1

    # C2 登録 PR 待ち（T5・T6）

    def _c2_prereg_pr(self, ctx: _Context, card: CardState) -> None:
        status = self._poll_pr(ctx, card)
        if status is None or status.state is not PrState.MERGED:
            return
        version = int(card.pr["version"]) if card.pr is not None else card.version
        card.prereg_commits[str(version)] = status.merge_commit or ctx.origin
        card.pr = None
        ctx.state.reject_streak = 0
        self._clear_failure(ctx, FAIL_PREREG)
        card.phase = CardPhase.C3.name

    def _poll_pr(self, ctx: _Context, card: CardState) -> PullStatus | None:
        """待っている PR の様子。CI が落ちたら 1 回だけ走らせ直し、2 回目で停止（T6）。"""
        if card.pr is None:
            return None
        number = int(card.pr["number"])
        status = self._pr_status(ctx, number)
        if status.state is PrState.OPEN and status.checks is Checks.FAIL:
            if self._retry_or_stop(ctx, card, "CI_FAILED", key=f"CI_FAILED:{number}"):
                self.ports.github.rerun_checks(number)
        elif status.state is PrState.CLOSED and card.pr.get("kind") != "fix":
            # 定型 PR が merge されずに閉じられた（人間か外の操作）。原因の特定は判断。
            self._stop_card(ctx, card, "CI_FAILED", "定型 PR が merge されずに閉じられた")
        return status

    # C3 起動待ち → C4（T7）は `_schedule` が行う。

    # C4 実行中（T8・T9・T10）

    def _active_seconds(self, card: CardState, now: datetime) -> int:
        run = card.run or {}
        launched = parse_time(run["launched_at"])
        paused = int(run.get("paused_seconds", 0))
        if run.get("paused_since"):
            paused += int((now - parse_time(run["paused_since"])).total_seconds())
        return int((now - launched).total_seconds()) - paused

    def _run_alive(self, ctx: _Context, card: CardState) -> tuple[Any, bool]:
        run = card.run or {}
        files = read_run(Path(run["version_dir"]))
        if files.run_pid is not None:
            run["run_pid"] = files.run_pid
        pids = [pid for pid in (files.run_pid, files.launcher_pid) if pid is not None]
        alive = any(self.ports.procs.alive(pid) for pid in pids)
        if not pids:
            alive = ctx.now - parse_time(run["launched_at"]) < LAUNCH_GRACE
        return files, alive

    def _c4_running(self, ctx: _Context, card: CardState) -> None:
        run = card.run
        if run is None:
            self._tool_failure(ctx, card, "実行中の run の記録が無い")
            return
        files, alive = self._run_alive(ctx, card)
        if files.exit_code is not None:
            self._run_finished(ctx, card, files.exit_code)
            return
        if not alive:
            self._run_failed(ctx, card, "プロセスが消えた（exit_code.txt が無い）")
            return
        if self._active_seconds(card, ctx.now) > int(run["limit_seconds"]):
            pid = run.get("run_pid")
            if not isinstance(pid, int) or not self.ports.procs.is_group_leader(pid):
                self._stop_card(ctx, card, "TOOL_FAILURE", "run_pid がプロセスグループの先頭でない")
                return
            self.ports.procs.signal_group(pid, signal.SIGSTOP)
            run["paused_since"] = format_time(ctx.now)
            card.phase = CardPhase.C5.name
            self.events.add(ctx.now, "run_paused", card=card.card_id, version=card.version)
            self._stop_card(ctx, card, "RUN_OVER_ESTIMATE")

    def _run_finished(self, ctx: _Context, card: CardState, code: int) -> None:
        run = card.run or {}
        locked = self.ports.wrapper.contract().locked_exit_code
        if locked is not None and code == locked:
            # 同じ版の実行中の印のロックが取れない拒否: run の失敗に数えず、待って
            # 走らせ直す（仮置き 36）。
            self._move_attempt(card)
            run = card.run or {}
            run["retry_after"] = format_time(ctx.now + LOCKED_WAIT)
            card.phase = CardPhase.C3.name
            return
        if code != 0:
            self._run_failed(ctx, card, f"終了コード {code}")
            return
        version_dir = Path(run["version_dir"])
        binding = self.ports.evaluation.binding(version_dir)
        hours = max(0, self._active_seconds(card, ctx.now)) / 3600
        run["finished"] = True
        self.events.add(ctx.now, "run_done", card=card.card_id, version=card.version, hours=hours)
        self._clear_failure(ctx, FAIL_RUN)
        card.phase = CardPhase.C6.name
        if binding is None:
            self._stop_card(ctx, card, "LEDGER_CHECK_FAILED", "束縛の記録が読めない")
            return
        card.executions[str(card.version)] = {
            "experiment_id": binding.experiment_id,
            "execution": binding.execution,
        }
        self._request_exec(ctx, card, "run")

    def _run_failed(self, ctx: _Context, card: CardState, detail: str) -> None:
        run = card.run or {}
        run["finished"] = True
        self.events.add(ctx.now, "run_failed", card=card.card_id, version=card.version)
        if _phase(card) is CardPhase.C5:
            item = card.find_reason("RUN_OVER_ESTIMATE")
            if item is not None:
                card.reasons.remove(item)
                if item.issue is not None:
                    self.ports.github.close_issue(item.issue)
        card.return_phase = CardPhase.C3.name
        card.phase = CardPhase.C13.name
        self._stop_card(ctx, card, "RUN_FAILED", detail)
        self._count_failure(ctx, FAIL_RUN)
        # 途中で止まった実行の台帳の行（開始の行など）も実行記録の PR でコミットする
        # （D25 §6.1 の 2。残すと起動の前の確かめの 2 で他のカードも起動できない）。
        self._request_exec(ctx, card, "ledger")

    def _move_attempt(self, card: CardState) -> None:
        run = card.run or {}
        attempt = int(run.get("attempt", 1))
        move_attempt(Path(run["version_dir"]), attempt)
        run.update(
            {
                "attempt": attempt + 1,
                "launched_at": None,
                "run_pid": None,
                "paused_since": None,
                "paused_seconds": 0,
                "finished": False,
            }
        )
        card.run = run

    def _rerun_same_version(self, ctx: _Context, card: CardState, identifier: str) -> bool:
        """同じ版の走らせ直し（D25 §4.7。`RUN_FAILED` の 1）。"""
        run = card.run or {}
        if run.get("version_dir"):
            files = read_run(Path(run["version_dir"]))
            pids = [pid for pid in (files.run_pid, files.launcher_pid) if pid is not None]
            if any(self.ports.procs.alive(pid) for pid in pids):
                return False
            self._move_attempt(card)
        self._release_card(ctx, card, identifier, to=CardPhase.C3.name)
        return True

    # C5 一時停止（T9・T11・T12。答えは `_apply_card`）

    def _c5_paused(self, ctx: _Context, card: CardState) -> None:
        files, alive = self._run_alive(ctx, card)
        if files.exit_code is not None or not alive:
            self._run_failed(ctx, card, "一時停止の間に外から終了された")

    # C6 記録 PR 待ち（T13〜T15）は `_exec_flow` が進める。

    # C7 分析中（T17〜T20）

    def _worker(
        self,
        ctx: _Context,
        card: CardState | None,
        role: str,
        extra: Mapping[str, str] | None = None,
    ) -> WorkerPoll | None:
        """作業役を起動するか、終わりを見る。終わっていなければ `None`。"""
        holder = card.procs if card is not None else ctx.state.ideator
        key = "worker"
        proc = holder.get(key) if card is not None else ctx.state.ideator.get("running")
        if not isinstance(proc, dict) or proc.get("role") != role:
            if self._stopped(ctx):
                return None
            attempt = int(card.retries.get(f"worker:{role}", 0)) + 1 if card is not None else 1
            request = WorkerRequest(
                role=role,
                attempt=attempt,
                card_id=card.card_id if card is not None else None,
                version=card.version if card is not None else None,
                extra=dict(extra or {}),
            )
            proc = {
                "role": role,
                "attempt": attempt,
                "started_at": format_time(ctx.now),
                "extra": dict(extra or {}),
            }
            if card is not None:
                card.procs[key] = proc
            else:
                ctx.state.ideator["running"] = proc
            try:
                self.ports.workers.launch(request)
            except WorkerUnavailable as exc:
                if card is not None:
                    card.procs.pop(key, None)
                    self._tool_failure(ctx, card, f"作業役を起動できない: {exc}")
                else:
                    ctx.state.ideator.pop("running", None)
                    ctx.notes.append(f"発想役を起動できない: {exc}")
                return None
            return None
        request = WorkerRequest(
            role=role,
            attempt=int(proc["attempt"]),
            card_id=card.card_id if card is not None else None,
            version=card.version if card is not None else None,
            extra=dict(proc.get("extra") or {}),
        )
        poll = self.ports.workers.poll(request)
        if poll.status is WorkerStatus.RUNNING:
            return None
        if card is not None:
            card.procs.pop(key, None)
        else:
            ctx.state.ideator.pop("running", None)
        if poll.status is WorkerStatus.USAGE_LIMIT:
            self._stop_global(ctx, "USAGE_LIMIT")
            return None
        return poll

    def _worker_failed(self, ctx: _Context, card: CardState, role: str) -> None:
        """出力なし・異常終了: 同じ段を 1 回だけやり直し、2 回目で停止（T20、D25 §5.6）。"""
        if self._retry_or_stop(ctx, card, "WORKER_FAILED", key=f"worker:{role}"):
            ctx.notes.append(f"{card.card_id}: 作業役 {role} をやり直す")

    def _c7_analysis(self, ctx: _Context, card: CardState) -> None:
        extra = {"note": card.redo_note} if card.redo_note else None
        poll = self._worker(ctx, card, "analyst_change", extra)
        if poll is None:
            return
        if poll.status in {WorkerStatus.FAILED, WorkerStatus.LOST}:
            self._worker_failed(ctx, card, "analyst_change")
            return
        if poll.status is WorkerStatus.REJECTED:
            self._stop_card(ctx, card, poll.reason or "CHANGE_OUT_OF_RULES")
            return
        output = poll.output
        if not isinstance(output, ChangeOutput):
            self._worker_failed(ctx, card, "analyst_change")
            return
        card.retries.pop("worker:analyst_change", None)
        card.redo_note = None
        card.report.setdefault("memos", {})[str(card.version)] = {
            "path": output.memo_path,
            "sha256": output.memo_sha256,
        }
        if output.decision == "AMBIGUOUS":
            self._stop_card(ctx, card, "AMBIGUOUS_RULE", output.reason or "")
            return
        if output.decision == "FINISH":
            if card.version < 2:
                self._stop_card(ctx, card, "CHANGE_OUT_OF_RULES", "「終える」は版 2 の後から")
                return
            card.phase = CardPhase.C8.name
            return
        if card.version >= 3:
            self._stop_card(ctx, card, "CHANGE_OUT_OF_RULES", "版 3 の後は改善しない（決定 103）")
            return
        self._improve(ctx, card, output.raw)

    def _improve(self, ctx: _Context, card: CardState, raw_change: bytes) -> None:
        """段 6: 変更の提案を検査し、改善後の事前登録の定型 PR を作る（T17・T18）。"""
        view = self._view(ctx)
        raw_card = view.read(card_path("taken", card.card_id))
        if raw_card is None:
            self._tool_failure(ctx, card, "taken/ のカードが origin/main に無い")
            return
        try:
            screened = check_change(
                view, card=raw_card, change=raw_change, from_version=card.version
            )
        except ToolFailure as exc:
            self._tool_failure(ctx, card, str(exc))
            return
        if not screened.passed or screened.prereg is None or screened.verification is None:
            identifier = screened.reason or "CHANGE_OUT_OF_RULES"
            if identifier == "CHANGE_PREREG_REJECTED":
                self._count_failure(ctx, FAIL_PREREG)
            self._stop_card(ctx, card, identifier)
            return
        try:
            checks = self.ports.evaluation.prepare(self.paths.exec_worktree, screened.verification)
        except PrepareFailed:
            self._count_failure(ctx, FAIL_PREREG)
            self._stop_card(ctx, card, "CHANGE_PREREG_REJECTED", "準備の確かめに通らない")
            return
        except ToolFailure as exc:
            self._tool_failure(ctx, card, str(exc))
            return
        prereg: Preregistration = screened.prereg
        version = prereg.version
        files: dict[str, bytes | None] = dict(prereg.files)
        files[change_path(card.card_id, version)] = raw_change
        files[prereg_note_path(card.card_id, version)] = prereg_note(prereg, checks)
        number = self._open_routine_pr(
            ctx,
            files,
            prereg_pull(
                card=card.card_id,
                version=version,
                experiment=prereg.experiment_id,
                at=format_time(ctx.now),
                trials=prereg.trial_count,
                files={path: body for path, body in files.items() if body is not None},
            ),
        )
        card.version = version
        card.trial_counts[str(version)] = prereg.trial_count
        card.pr = {"kind": "prereg", "number": number, "version": version}
        card.run = None
        card.phase = CardPhase.C2.name

    # C8 報告中（T21）

    def _c8_report(self, ctx: _Context, card: CardState) -> None:
        poll = self._worker(ctx, card, "analyst_report")
        if poll is None:
            return
        output = poll.output
        if poll.status is WorkerStatus.REJECTED:
            self._stop_card(ctx, card, poll.reason or "CHANGE_OUT_OF_RULES")
            return
        if poll.status is not WorkerStatus.DONE or not isinstance(output, ReportOutput):
            self._worker_failed(ctx, card, "analyst_report")
            return
        card.retries.pop("worker:analyst_report", None)
        card.report["text"] = {
            "prediction": output.prediction,
            "change_reasons": list(output.change_reasons),
            "change_contents": list(output.change_contents),
        }
        card.report["comparison"] = {
            "path": output.comparison_path,
            "sha256": output.comparison_sha256,
        }
        card.checks.pop("verifier", None)
        card.phase = CardPhase.C9.name

    # C9 確かめ中（T22〜T25）

    def _c9_verify(self, ctx: _Context, card: CardState) -> None:
        if "gate" not in card.checks:
            if card.repro is None:
                return  # 元の実験の再現を待つ（関門の機械の項目の 4）
            try:
                result = self.ports.gate.check(card.card_id, list(range(1, card.version + 1)))
            except ToolFailure as exc:
                self._tool_failure(ctx, card, str(exc))
                return
            card.checks["gate"] = [[item, ok] for item, ok in result.items]
            if not result.passed:
                self.events.add(ctx.now, "gate_fail", card=card.card_id)
                self._reject(ctx, card, "GATE_FAILED", source="taken")
                return
        if "verifier" not in card.checks:
            poll = self._worker(ctx, card, "verifier")
            if poll is None:
                return
            output = poll.output
            if poll.status is not WorkerStatus.DONE or not isinstance(output, VerdictOutput):
                self._worker_failed(ctx, card, "verifier")
                return
            card.retries.pop("worker:verifier", None)
            if not output.passed:
                card.verifier_failures += 1
                if card.verifier_failures == 1:
                    card.checks.pop("gate", None)
                    card.phase = CardPhase.C8.name
                    return
                self.events.add(ctx.now, "gate_fail", card=card.card_id)
                self._reject(ctx, card, "VERIFIER_FAILED", source="taken")
                return
            card.checks["verifier"] = {"result": "PASS", "reasons": list(output.reasons)}
        self.events.add(ctx.now, "gate_pass", card=card.card_id)
        card.checks["gate_passed"] = True
        self._open_report_pr(ctx, card)
        if "review" not in card.reservations:
            card.reservations.append("review")
        card.phase = CardPhase.C10.name

    def _report_document(self, card: CardState) -> bytes:
        text = card.report.get("text", {})
        verifier = card.checks.get("verifier", {})
        return dump_document(
            {
                "schema_version": 1,
                "card": card.card_id,
                "final_version": card.version,
                "text": {
                    "prediction": text.get("prediction", ""),
                    "change_reasons": list(text.get("change_reasons", [])),
                    "change_contents": list(text.get("change_contents", [])),
                },
                "comparison": dict(card.report.get("comparison", {})),
                "gate": {
                    "items": [
                        {"item": int(item), "result": "PASS" if ok else "FAIL"}
                        for item, ok in card.checks.get("gate", [])
                    ],
                    "verifier": {
                        "result": verifier.get("result", "FAIL"),
                        "reasons": list(verifier.get("reasons", [])),
                    },
                },
            }
        )

    def _open_report_pr(self, ctx: _Context, card: CardState) -> None:
        view = self._view(ctx)
        raw = view.read(card_path("taken", card.card_id))
        files: dict[str, bytes | None] = {
            f"{LOOPS_DIR}/{card.card_id}/report.yaml": self._report_document(card),
            outcome_path(card.card_id): outcome_bytes(
                card.card_id, reason="REPORTED", at=format_time(ctx.now), last_version=card.version
            ),
        }
        if raw is not None:
            files[card_path("done", card.card_id)] = raw
            files[card_path("taken", card.card_id)] = None
        number = self._open_routine_pr(
            ctx,
            files,
            report_pull(
                card=card.card_id,
                version=card.version,
                experiment=card.experiment or "unknown",
                at=format_time(ctx.now),
                files={path: body for path, body in files.items() if body is not None},
            ),
        )
        card.outcome = "REPORTED"
        card.pr = {"kind": "report", "number": number, "version": card.version}
        self._after_done(ctx)

    # C10 入口・審査中（T26〜T30）

    def _c10_review(self, ctx: _Context, card: CardState) -> None:
        self._poll_done_pr(ctx, card)
        proc = card.procs.get("review")
        if proc is None:
            if "review" in card.reservations and self.ports.evaluation.declaration_required():
                card.reservations.remove("review")
                self._stop_card(ctx, card, "REVIEW_NEEDS_DECLARATION")
            return
        if proc.get("result") is not None:
            return  # 審査台帳の行の実行記録の PR を待つ（`_exec_flow`）
        code = self._proc_exit(ctx, proc)
        info = card.executions.get(str(card.version), {})
        eid, execution = str(info.get("experiment_id")), int(info.get("execution", 0))
        ledger = self._exec_file(REVIEW_LEDGER)
        if code is None:
            if self._proc_alive(ctx, proc):
                return
            recorded = review_result(ledger, eid, execution)
            if recorded is None:
                card.procs.pop("review", None)
                if self._retry_or_stop(ctx, card, "REVIEW_FAILED"):
                    card.reservations.append("review")
                return
            code = {"PROCEED": 0, "DO_NOT_PROCEED": 7, "DEFERRED": 8}[recorded]
        if code not in {0, 7, 8}:
            card.procs.pop("review", None)
            if self._retry_or_stop(ctx, card, "REVIEW_FAILED"):
                card.reservations.append("review")
            return
        entered = has_candidate_row(ledger, eid, execution)
        self.events.add(ctx.now, "entrance_pass" if entered else "entrance_fail", card=card.card_id)
        result = {0: "ADVANCE", 7: "NOT_ADVANCE", 8: "DEFERRED"}[code]
        if entered:
            kind = {0: "review_advance", 7: "review_not", 8: "review_deferred"}[code]
            self.events.add(ctx.now, kind, card=card.card_id)
        proc["result"] = result
        card.review = result
        self._request_exec(ctx, card, "review")

    def _review_recorded(self, ctx: _Context, card: CardState) -> None:
        """審査の行の実行記録の PR が入った（または行が無い）: 結果に従う（T28〜T30）。"""
        card.procs.pop("review", None)
        if card.review == "ADVANCE":
            card.phase = CardPhase.C11.name
        elif card.review == "DEFERRED":
            self._stop_card(ctx, card, "REVIEW_DEFERRED")
        else:
            self._finish(ctx, card, "NOT_CANDIDATE")

    def _poll_done_pr(self, ctx: _Context, card: CardState) -> None:
        """一巡の報告の PR（C10〜C14 で遅れて入りうる）。merge されたら外す。"""
        if card.pr is None or card.pr.get("kind") not in {"report", "done"}:
            return
        status = self._poll_pr(ctx, card)
        if status is not None and status.state is PrState.MERGED:
            card.pr = None

    # C11 関所 1 待ち（T31〜T34）

    def _c11_fix(self, ctx: _Context, card: CardState) -> None:
        self._poll_done_pr(ctx, card)
        fix_pr = card.checks.get("fix_pr")
        if isinstance(fix_pr, int):
            # 関所 1 の PR は定型 PR（`card.pr`）とは別に持つ（遅れた一巡の報告の PR と並びうる）。
            status = self._pr_status(ctx, fix_pr)
            if status.state is PrState.MERGED:
                ctx.state.last_human_answer_at = status.closed_at or format_time(ctx.now)
                card.checks.pop("fix_pr")
                card.reservations.append("final")
                card.phase = CardPhase.C12.name
            elif status.state is PrState.CLOSED:
                ctx.state.last_human_answer_at = status.closed_at or format_time(ctx.now)
                card.checks.pop("fix_pr")
                self._finish(ctx, card, "HUMAN_DECLINED")
            elif status.checks is Checks.FAIL:
                if self._retry_or_stop(ctx, card, "CI_FAILED", key=f"CI_FAILED:{fix_pr}"):
                    self.ports.github.rerun_checks(fix_pr)
            return
        if not self.ports.evaluation.fix_available():
            self._stop_card(ctx, card, "FIXATION_TOOL_MISSING")
            return
        if card.fix_retry_after and ctx.now < parse_time(card.fix_retry_after):
            return
        info = card.executions.get(str(card.version), {})
        execution = int(info.get("execution", 0))
        version_dir = self.paths.version_dir(card.experiment, card.version)
        proc = card.procs.get("fix")
        if proc is None:
            if self._stopped(ctx):
                return
            command = [
                *self.ports.odyssey,
                "experiment",
                "fix",
                "--experiment-dir",
                str(version_dir),
                "--execution",
                str(execution),
                "--repo-root",
                str(self.paths.exec_worktree),
                "--snapshots",
                str(self.paths.snapshots),
                "--by",
                RUNNER_BY,
            ]
            self._spawn(ctx, card, "fix", command, slots=0, cwd=self.paths.exec_worktree)
            return
        code = self._proc_exit(ctx, proc)
        if code is None:
            if not self._proc_alive(ctx, proc):
                card.procs.pop("fix", None)
                self._tool_failure(ctx, card, "固定の前の確かめが途中で止まった")
            return
        card.procs.pop("fix", None)
        if code == 8:
            card.fix_retry_after = format_time(ctx.now + FIX_DEFER)
            return
        if code == 7:
            self._stop_card(ctx, card, "FIXATION_CHECK_FAILED")
            return
        if code != 0:
            self._tool_failure(ctx, card, f"固定のコマンドの終了コード {code}")
            return
        records = self.ports.git.exec_untracked(CANDIDATE_FIXES)
        if len(records) != 1:
            self._stop_card(ctx, card, "FIXATION_CHECK_FAILED", "固定の記録が 1 つに決まらない")
            return
        record = self.paths.exec_worktree / records[0]
        body = record.read_bytes()
        record.unlink()
        output = Path(proc["state_dir"]) / "output.log"
        lines = output.read_text(encoding="utf-8").splitlines() if output.is_file() else []
        text = fix_pull(
            card=card.card_id,
            experiment=card.experiment,
            execution=execution,
            at=format_time(ctx.now),
            check_lines=lines,
        )
        commit = self.ports.git.commit_files(
            base=ctx.origin, files={records[0]: body}, message=text.title
        )
        self.ports.git.push_branch(commit, text.branch)
        number = self.ports.github.create_pr(branch=text.branch, title=text.title, body=text.body)
        card.checks["fix_pr"] = number

    # C12 最終検証中（T35〜T38）

    def _main_final_ledger(self) -> bytes | None:
        path = self.paths.repo / FINAL_LEDGER
        return path.read_bytes() if path.is_file() else None

    def _final_consumed_on_origin(self, ctx: _Context, card: CardState) -> bool:
        info = card.executions.get(str(card.version), {})
        data = self._view(ctx).read(FINAL_LEDGER)
        return has_candidate_row(
            data, str(info.get("experiment_id")), int(info.get("execution", 0))
        )

    def _c12_final(self, ctx: _Context, card: CardState) -> None:
        if card.pr is not None and card.pr.get("kind") == "final":
            status = self._poll_pr(ctx, card)
            if status is not None and status.state is PrState.MERGED:
                card.pr = None
                git = self.ports.git
                if git.main_file_matches(FINAL_LEDGER, ctx.origin):
                    git.main_restore(FINAL_LEDGER)
                    checkout = git.main_checkout()
                    if checkout.clean and git.is_ancestor(checkout.head, ctx.origin):
                        git.main_fast_forward(ctx.origin)
                    self._finish(ctx, card, "FINAL_VALIDATED")
                else:
                    self._stop_card(ctx, card, "MAIN_CHECKOUT_NOT_READY", "最終検証の台帳の戻し")
            return
        self._poll_done_pr(ctx, card)
        info = card.executions.get(str(card.version), {})
        eid, execution = str(info.get("experiment_id")), int(info.get("execution", 0))
        if card.checks.pop("final_recovered", False):
            self._open_final_pr(ctx, card, execution)
            return
        proc = card.procs.get("final")
        if proc is None:
            return  # 空きと主の作業木の確かめを待つ（`_schedule`）
        code = self._proc_exit(ctx, proc)
        if code is None:
            if self._proc_alive(ctx, proc):
                return
            if has_candidate_row(self._main_final_ledger(), eid, execution):
                code = 0
            else:
                card.procs.pop("final", None)
                self._stop_card(ctx, card, "FINAL_VALIDATION_FAILED", "プロセスが消えた")
                return
        card.procs.pop("final", None)
        if code == 0:
            self._open_final_pr(ctx, card, execution)
            return
        if code in FINAL_STATE_A and not self._final_consumed_on_origin(ctx, card):
            if self._retry_or_stop(ctx, card, "FINAL_VALIDATION_FAILED", key="FINAL_STATE_A"):
                card.reservations.append("final")
            return
        self._stop_card(ctx, card, "FINAL_VALIDATION_FAILED", f"終了コード {code}")

    def _open_final_pr(self, ctx: _Context, card: CardState, execution: int) -> None:
        if card.pr is not None:
            # 遅れた一巡の報告の PR がまだ入っていない: 先に入るのを待つ（次の 1 歩で作る）。
            card.checks["final_recovered"] = True
            return
        data = self._main_final_ledger() or b""
        files: dict[str, bytes | None] = {FINAL_LEDGER: data}
        before = self._view(ctx).read(FINAL_LEDGER) or b""
        text = final_pull(
            card=card.card_id,
            version=card.version,
            experiment=card.experiment,
            execution=execution,
            at=format_time(ctx.now),
            lines=len(data.splitlines()) - len(before.splitlines()),
            files={FINAL_LEDGER: data},
        )
        number = self._open_pr(ctx, files, text, auto=True)
        card.pr = {"kind": "final", "number": number, "version": card.version}

    # C14 終わり

    def _c14_done(self, ctx: _Context, card: CardState) -> None:
        self._poll_done_pr(ctx, card)

    def _finish(self, ctx: _Context, card: CardState, outcome: str) -> None:
        card.phase = CardPhase.C14.name
        card.reservations = []
        if card.outcome is None:
            card.outcome = outcome
        self.events.add(ctx.now, "card_finished", card=card.card_id, outcome=outcome)

    # 「このカードを終える」（T41・T12）

    def _begin_end(self, ctx: _Context, card: CardState, identifier: str) -> bool:
        if card.procs.get("final") is not None:
            return False  # 最終検証が走っている間は選べない（消費の後に止めると扱いが増える）
        for item in list(card.reasons):
            card.reasons.remove(item)
            if item.issue is not None:
                self.ports.github.close_issue(item.issue)
        card.ending = identifier
        card.reservations = []
        self._terminate(card)
        self._finish_end(ctx, card)
        return True

    def _groups(self, card: CardState) -> list[int]:
        groups: list[int] = []
        run = card.run or {}
        if run.get("version_dir"):
            files = read_run(Path(run["version_dir"]))
            if files.run_pid is not None:
                groups.append(files.run_pid)
        for proc in card.procs.values():
            pid = proc.get("pid")
            if isinstance(pid, int):
                groups.append(pid)
        return groups

    def _terminate(self, card: CardState) -> None:
        """プロセスの組に `SIGTERM` の後に `SIGCONT` を送る（止まっている組も終わる。
        仮置き 35）。"""
        for group in self._groups(card):
            if self.ports.procs.alive(group):
                self.ports.procs.signal_group(group, signal.SIGTERM)
                self.ports.procs.signal_group(group, signal.SIGCONT)

    def _finish_end(self, ctx: _Context, card: CardState) -> None:
        if any(self.ports.procs.alive(group) for group in self._groups(card)):
            return  # 終わったことを確かめられるまで C13 のまま
        outcome = card.ending or "HUMAN_ENDED"
        card.ending = None
        card.procs = {}
        if card.run is not None:
            card.run["finished"] = True
        view = self._view(ctx)
        source = next(
            (place for place in ("taken", "queue") if view.read(card_path(place, card.card_id))),
            None,
        )
        if card.pr is not None and card.pr.get("kind") in {"prereg", "report", "done"}:
            status = self._pr_status(ctx, int(card.pr["number"]))
            if status.state is PrState.OPEN:
                self.ports.github.close_pr(int(card.pr["number"]))
            card.pr = None
        if source is not None:
            self._open_done_pr(ctx, card, outcome, source=source)
        card.outcome = outcome
        self._finish(ctx, card, outcome)
        self._after_done(ctx)

    def _open_done_pr(
        self, ctx: _Context, card: CardState, identifier: str, *, source: str
    ) -> None:
        view = self._view(ctx)
        raw = view.read(card_path(source, card.card_id))
        files: dict[str, bytes | None] = {
            outcome_path(card.card_id): outcome_bytes(
                card.card_id, reason=identifier, at=format_time(ctx.now), last_version=card.version
            )
        }
        if raw is not None:
            files[card_path("done", card.card_id)] = raw
            files[card_path(source, card.card_id)] = None
        experiment = card.experiment
        if not experiment and raw is not None:
            try:
                experiment = parse_card(raw).experiment_id
            except CardFormatError:
                experiment = ""
        number = self._open_routine_pr(
            ctx,
            files,
            report_pull(
                card=card.card_id,
                version=card.version,
                experiment=experiment or "unknown",
                at=format_time(ctx.now),
                files={path: body for path, body in files.items() if body is not None},
            ),
        )
        card.pr = {"kind": "done", "number": number, "version": card.version}

    # --- 元の実験の再現（決定 107。T16） -------------------------------------------------

    def _poll_repro(self, ctx: _Context, card: CardState) -> None:
        proc = card.procs.get("reproduce")
        if proc is None:
            return
        code = self._proc_exit(ctx, proc)
        if code is None:
            if self._proc_alive(ctx, proc):
                return
            code = -1
        card.procs.pop("reproduce", None)
        if code == 0:
            card.repro = "MATCH"
        else:
            card.repro = "MISMATCH"
            self._stop_card(ctx, card, "REPRO_MISMATCH", f"終了コード {code}")

    # --- 切り離した処理 -------------------------------------------------------------------

    def _spawn(
        self,
        ctx: _Context,
        card: CardState,
        name: str,
        command: Sequence[str],
        *,
        slots: int,
        cwd: Path,
    ) -> None:
        attempt = int(card.retries.get(f"spawn:{name}", 0)) + 1
        card.retries[f"spawn:{name}"] = attempt
        state_dir = self.paths.proc_dir(card.card_id, name, attempt)
        if state_dir.exists():
            shutil.rmtree(state_dir)
        state_dir.mkdir(parents=True)
        # 起動の前に「起動した」の印を書く（D25 §2.4 の照合の (0)）。
        proc: dict[str, Any] = {
            "attempt": attempt,
            "started_at": format_time(ctx.now),
            "slots": slots,
            "state_dir": str(state_dir),
            "pid": None,
        }
        card.procs[name] = proc
        self.store.save_card(card)
        proc["pid"] = self.ports.procs.spawn(command, cwd=cwd, state_dir=state_dir)

    def _proc_exit(self, ctx: _Context, proc: Mapping[str, Any]) -> int | None:
        path = Path(proc["state_dir"]) / "exit_code.txt"
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _proc_alive(self, ctx: _Context, proc: Mapping[str, Any]) -> bool:
        pid = proc.get("pid")
        if isinstance(pid, int) and self.ports.procs.alive(pid):
            return True
        return ctx.now - parse_time(str(proc["started_at"])) < LAUNCH_GRACE and pid is None

    # --- 定型 PR -------------------------------------------------------------------------

    def _open_pr(
        self, ctx: _Context, files: Mapping[str, bytes | None], text: PullText, *, auto: bool
    ) -> int:
        commit = self.ports.git.commit_files(base=ctx.origin, files=files, message=text.title)
        self.ports.git.push_branch(commit, text.branch)
        number = self.ports.github.create_pr(branch=text.branch, title=text.title, body=text.body)
        if auto:
            self.ports.github.enable_auto_merge(number)
        return number

    def _open_routine_pr(
        self, ctx: _Context, files: Mapping[str, bytes | None], text: PullText
    ) -> int:
        return self._open_pr(ctx, files, text, auto=True)

    # --- 実行記録の定型 PR（枝 runner/exec。D25 §6.1） ---------------------------------------

    def _request_exec(self, ctx: _Context, card: CardState, purpose: str) -> None:
        queue = ctx.state.exec_queue
        entry = {"card": card.card_id, "version": card.version, "purpose": purpose}
        if entry not in queue:
            queue.append(entry)
        card.checks.pop("ledger_committed", None)

    def _exec_file(self, path: str) -> bytes | None:
        target = self.paths.exec_worktree / path
        return target.read_bytes() if target.is_file() else None

    def _ledger_quiet(self) -> bool:
        """台帳のロックが無く、台帳が改行で終わっている（追記の途中でない）。"""
        root = self.paths.exec_worktree
        if any((root / lock).exists() for lock in LEDGER_LOCKS):
            return False
        for path in (TRIAL_LEDGER, REVIEW_LEDGER):
            data = self._exec_file(path)
            if data and not data.endswith(b"\n"):
                return False
        return True

    def _exec_flow(self, ctx: _Context) -> None:
        state = ctx.state
        if state.exec_pr is not None:
            number = int(state.exec_pr["number"])
            status = self._pr_status(ctx, number)
            card = ctx.cards.get(str(state.exec_pr["card"]))
            if status.state is PrState.MERGED:
                done = state.exec_pr
                state.exec_pr = None
                self._clear_failure(ctx, FAIL_LEDGER)
                if card is not None:
                    self._exec_merged(ctx, card, done)
                    self._advance(ctx, card)
            elif status.checks is Checks.FAIL and card is not None:
                if self._retry_or_stop(ctx, card, "CI_FAILED", key=f"CI_FAILED:{number}"):
                    self.ports.github.rerun_checks(number)
            return
        while state.exec_queue:
            request = state.exec_queue[0]
            card = ctx.cards.get(str(request["card"]))
            if card is None:
                state.exec_queue.pop(0)
                continue
            if not self._ledger_quiet():
                return
            opened = self._commit_exec(ctx, card, request)
            if opened is None:
                return
            state.exec_queue.pop(0)
            if opened:
                return
            # 足す行もファイルも無かった（入口の拒否で審査台帳の行が無いなど）:
            # 直ちに入ったものとする。
            self._exec_merged(ctx, card, request)
            self._advance(ctx, card)

    def _commit_exec(
        self, ctx: _Context, card: CardState, request: Mapping[str, Any]
    ) -> bool | None:
        """台帳の確かめ → コミット → 実行記録の定型 PR。PR を作れば `True`、何も無ければ `False`、
        待つなら `None`（D25 §6.1 の 2・3）。"""
        git = self.ports.git
        root = self.paths.exec_worktree
        code = self.ports.evaluation.ledger_check(root, self.paths.repo)
        if code != 0:
            state_queue = ctx.state.exec_queue
            if state_queue and state_queue[0] == request:
                state_queue.pop(0)
            self._count_failure(ctx, FAIL_LEDGER)
            self._stop_card(ctx, card, "LEDGER_CHECK_FAILED", f"台帳の確かめの終了コード {code}")
            return None
        version = int(request["version"])
        paths = [path for path in git.exec_dirty_paths() if path in _LEDGER_FILES]
        if request["purpose"] == "run":
            record = self._run_record(card, version)
            if record is None:
                self._stop_card(ctx, card, "LEDGER_CHECK_FAILED", "実行記録を作れない")
                return None
            target = root / run_record_path(card.card_id, version)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(record)
            paths.append(run_record_path(card.card_id, version))
        if not paths:
            card.checks["ledger_committed"] = True
            return False
        git.exec_commit(paths, f"[runner] {card.card_id} v{version} の実行記録")
        for path in paths:
            data = self._exec_file(path)
            if path in _LEDGER_FILES and data and not data.endswith(b"\n"):
                git.exec_undo_commit()
                return None
        git.exec_push()
        files = {path: self._exec_file(path) or b"" for path in paths}
        view = self._view(ctx)
        lines = sum(
            len((files[path]).splitlines()) - len((view.read(path) or b"").splitlines())
            for path in paths
            if path in _LEDGER_FILES
        )
        text = exec_pull(
            card=card.card_id,
            version=version,
            experiment=card.experiment,
            at=format_time(ctx.now),
            lines=lines,
            files=files,
        )
        number = self.ports.github.create_pr(branch=text.branch, title=text.title, body=text.body)
        self.ports.github.enable_auto_merge(number)
        ctx.state.exec_pr = {**dict(request), "number": number}
        card.checks["ledger_committed"] = True
        return True

    def _run_record(self, card: CardState, version: int) -> bytes | None:
        """数値なしの実行記録 `run_v<版>.yaml`（D25 §2.3。PR 6 の検査 5 の形）。"""
        run = card.run or {}
        version_dir = Path(
            str(run.get("version_dir") or self.paths.version_dir(card.experiment, version))
        )
        info = card.executions.get(str(version))
        if info is None:
            return None

        def read(name: str) -> str | None:
            path = version_dir / name
            return path.read_text(encoding="utf-8").strip() if path.is_file() else None

        started, finished, code = (
            read("started_at_utc.txt"),
            read("finished_at_utc.txt"),
            read("exit_code.txt"),
        )
        if started is None or finished is None or code is None:
            return None
        rss = None
        time_log = read("time.log")
        if time_log is not None and (matched := _MAX_RSS.search(time_log)) is not None:
            rss = int(matched.group(1))
        artifacts = []
        for name in ("experiment_manifest.json", "search/ledger_execution.json"):
            path = version_dir / name
            if path.is_file():
                artifacts.append(
                    {
                        "path": path.relative_to(self.paths.repo).as_posix(),
                        "sha256": _sha(path.read_bytes()),
                    }
                )
        return dump_document(
            {
                "schema_version": 1,
                "card": card.card_id,
                "version": version,
                "experiment_id": card.experiment,
                "execution": int(info["execution"]),
                "started_at": started,
                "finished_at": finished,
                "exit_code": int(code),
                "max_rss_bytes": rss,
                "record_id": str(info["experiment_id"]),
                "artifacts": artifacts,
                "memo": None,
            }
        )

    def _exec_merged(self, ctx: _Context, card: CardState, request: Mapping[str, Any]) -> None:
        purpose = request.get("purpose")
        if purpose == "run" and _phase(card) is CardPhase.C6:
            if card.version == 1 and "reproduce" not in card.reservations and card.repro is None:
                card.reservations.append("reproduce")
            card.phase = CardPhase.C7.name if card.version < 3 else CardPhase.C8.name
        elif purpose == "review" and _phase(card) is CardPhase.C10:
            self._review_recorded(ctx, card)
        elif purpose == "run" and _phase(card) is CardPhase.C13:
            card.resume_to = CardPhase.C7.name if card.version < 3 else CardPhase.C8.name
            if card.version == 1 and "reproduce" not in card.reservations and card.repro is None:
                card.reservations.append("reproduce")

    # --- 空き（スロット）と起動（D25 §4.4・§4.5） ---------------------------------------------

    def _used(self, ctx: _Context) -> tuple[int, int]:
        """使っている枠の数と、走っている重い処理の数。"""
        used = heavy = 0
        for card in ctx.cards.values():
            run = card.run or {}
            if _phase(card) in {CardPhase.C4, CardPhase.C5} or (
                run.get("launched_at") and not run.get("finished") and card.ending is not None
            ):
                used += int(run.get("slots", 1))
                heavy += 1
            for proc in card.procs.values():
                slots = int(proc.get("slots", 0) or 0)
                if slots:
                    used += slots
                    heavy += 1
        return used, heavy

    def _schedule(self, ctx: _Context) -> None:
        if self._stopped(ctx):
            return
        order = [ctx.cards[card_id] for card_id in ctx.state.cards if card_id in ctx.cards]
        queue: list[tuple[CardState, str]] = []
        queue += [(card, "run") for card in order if _phase(card) is CardPhase.C3]
        for kind in ("review", "final", "reproduce"):
            queue += [
                (card, kind)
                for card in order
                if kind in card.reservations and _phase(card) is not CardPhase.C13
            ]
        for card, kind in queue:
            if self._stopped(ctx):
                return
            used, heavy = self._used(ctx)
            plan = plan_slots(
                used=used,
                heavy_running=heavy,
                free_memory=self.ports.system.free_memory_bytes() if heavy else 0,
                run=kind in {"run"},
            )
            if not plan.allowed:
                return
            if not self._disk_ok(ctx):
                return
            if kind == "run":
                self._launch_run(ctx, card, plan.slots, plan.mode or "sequential")
            elif kind == "review":
                self._launch_review(ctx, card)
            elif kind == "final":
                self._launch_final(ctx, card)
            else:
                self._launch_reproduce(ctx, card)

    def _exec_ready(self, ctx: _Context) -> bool:
        """起動の前の確かめの 2・3（未コミットの台帳の行・ロック・早送り）。"""
        git = self.ports.git
        if not self._ledger_quiet():
            return False
        dirty = [path for path in git.exec_dirty_paths() if path in _LEDGER_FILES]
        running = [
            card
            for card in ctx.cards.values()
            if _phase(card) in {CardPhase.C4, CardPhase.C5}
            or any(int(proc.get("slots", 0) or 0) for proc in card.procs.values())
        ]
        if dirty:
            # 1 本目の開始の行をコミットし、開始の行だけの実行記録の PR を出す
            # （D25 §4.4 の (ii)〜(iv)）。
            if running and ctx.state.exec_pr is None and not ctx.state.exec_queue:
                self._request_exec(ctx, running[0], "ledger")
            return False
        head = git.exec_head()
        if head == ctx.origin:
            return True
        if not git.is_ancestor(head, ctx.origin):
            return False
        if running:
            launch_commits = [
                str((card.run or {}).get("launch_commit") or head) for card in running
            ]
            for commit in launch_commits:
                changed = git.changed_paths(commit, ctx.origin)
                if any(not path.startswith(PREREG_PLACES) for path in changed):
                    return False
        git.exec_fast_forward(ctx.origin)
        return True

    def _disk_ok(self, ctx: _Context) -> bool:
        free = self.ports.system.free_disk_bytes(self.paths.repo)
        if free >= DISK_FLOOR_BYTES:
            return True
        self._auto_delete(ctx)
        if self.ports.system.free_disk_bytes(self.paths.repo) >= DISK_FLOOR_BYTES:
            return True
        self._stop_global(ctx, "DISK_LOW")
        return False

    def _launch_run(self, ctx: _Context, card: CardState, slots: int, mode: str) -> None:
        run = card.run or {}
        retry_after = run.get("retry_after")
        if retry_after and ctx.now < parse_time(str(retry_after)):
            return
        if not self._exec_ready(ctx):
            return
        prereg = card.prereg_commits.get(str(card.version))
        head = self.ports.git.exec_head()
        if prereg and not self.ports.git.is_ancestor(prereg, head):
            return
        version_dir = self.paths.version_dir(card.experiment, card.version)
        trials = int(card.trial_counts.get(str(card.version), 1))
        estimate = estimate_seconds(trials)
        code = self.ports.wrapper.launch(
            experiment=experiment_path(card.experiment, card.version),
            worktree=self.paths.exec_worktree,
            repo=self.paths.repo,
            mode=mode,
        )
        if code != 0:
            self._tool_failure(ctx, card, f"実行の包みが起動を拒否した（終了コード {code}）")
            return
        card.run = {
            "version": card.version,
            "attempt": int(run.get("attempt", 1)) if run.get("version") == card.version else 1,
            "version_dir": str(version_dir),
            "launched_at": format_time(ctx.now),
            "launch_commit": head,
            "paused_since": None,
            "paused_seconds": 0,
            "estimate_seconds": estimate,
            "limit_seconds": 2 * estimate,
            "slots": slots,
            "mode": mode,
            "run_pid": None,
            "finished": False,
        }
        card.retries.pop("TOOL_FAILURE", None)
        card.phase = CardPhase.C4.name

    def _execution(self, card: CardState) -> tuple[str, int]:
        info = card.executions.get(str(card.version), {})
        return str(info.get("experiment_id")), int(info.get("execution", 0))

    def _launch_review(self, ctx: _Context, card: CardState) -> None:
        if self.ports.evaluation.declaration_required():
            card.reservations.remove("review")
            self._stop_card(ctx, card, "REVIEW_NEEDS_DECLARATION")
            return
        if not self._exec_ready(ctx):
            return
        eid, execution = self._execution(card)
        command = [
            *self.ports.odyssey,
            "experiment",
            "review",
            "--experiment-id",
            eid,
            "--execution",
            str(execution),
            "--repo-root",
            str(self.paths.exec_worktree),
            "--experiment-dir",
            str(self.paths.version_dir(card.experiment, card.version)),
            "--snapshots",
            str(self.paths.snapshots),
            "--out",
            str(self.paths.repo),
        ]
        card.reservations.remove("review")
        self._spawn(ctx, card, "review", command, slots=1, cwd=self.paths.exec_worktree)

    def _launch_final(self, ctx: _Context, card: CardState) -> None:
        git = self.ports.git
        checkout = git.main_checkout()
        ready = checkout.clean and checkout.branch == "main"
        if ready and checkout.head != ctx.origin:
            ready = git.is_ancestor(checkout.head, ctx.origin)
            if ready:
                git.main_fast_forward(ctx.origin)
        if not ready:
            since = card.main_wait_since or format_time(ctx.now)
            card.main_wait_since = since
            if ctx.now - parse_time(since) > MAIN_WAIT:
                card.reservations.remove("final")
                self._stop_card(ctx, card, "MAIN_CHECKOUT_NOT_READY")
            return
        card.main_wait_since = None
        if self.ports.evaluation.declaration_required():
            card.reservations.remove("final")
            self._stop_card(ctx, card, "REVIEW_NEEDS_DECLARATION", "最終検証も申告を求める")
            return
        _, execution = self._execution(card)
        command = [
            *self.ports.odyssey,
            "experiment",
            "final-validate",
            "--experiment-dir",
            str(self.paths.version_dir(card.experiment, card.version)),
            "--execution",
            str(execution),
            "--repo-root",
            str(self.paths.repo),
            "--snapshots",
            str(self.paths.snapshots),
            "--out",
            str(self.paths.repo),
            "--by",
            RUNNER_BY,
        ]
        card.reservations.remove("final")
        self._spawn(ctx, card, "final", command, slots=1, cwd=self.paths.repo)

    def _launch_reproduce(self, ctx: _Context, card: CardState) -> None:
        if not self._exec_ready(ctx):
            return
        out = self.paths.repro_out(card.experiment, 1)
        out.mkdir(parents=True, exist_ok=True)
        command = [
            *self.ports.odyssey,
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(self.paths.version_dir(card.experiment, 1)),
            "--snapshots",
            str(self.paths.snapshots),
            "--out",
            str(out),
            "--repo-root",
            str(self.paths.exec_worktree),
        ]
        card.reservations.remove("reproduce")
        self._spawn(ctx, card, "reproduce", command, slots=1, cwd=self.paths.exec_worktree)

    # --- 自動の削除（決定 147、仮置き 19） ----------------------------------------------------

    def _auto_delete(self, ctx: _Context) -> None:
        listed: list[dict[str, Any]] = []
        for card_id in ctx.state.cards:
            card = ctx.cards.get(card_id)
            if card is None or not card.checks.get("gate_passed"):
                continue
            deleted = set(card.checks.get("deleted_versions", []))
            last_open = card.review not in {"NOT_ADVANCE"}
            for version in range(1, card.version + 1):
                if version in deleted or (version == card.version and last_open):
                    continue
                listed += self._delete_version(card, version)
                deleted.add(version)
                card.checks["deleted_versions"] = sorted(deleted)
                if self.ports.system.free_disk_bytes(self.paths.repo) >= DISK_FLOOR_BYTES:
                    break
            if self.ports.system.free_disk_bytes(self.paths.repo) >= DISK_FLOOR_BYTES:
                break
        if listed:
            stamp = ctx.now.strftime("%Y%m%dT%H%M%SZ")
            self.paths.deleted_dir.mkdir(parents=True, exist_ok=True)
            path = self.paths.deleted_dir / f"{stamp}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                for item in listed:
                    handle.write(_json_line(item))
            self.events.add(
                ctx.now,
                "deleted",
                files=len(listed),
                bytes=sum(int(item["size"]) for item in listed),
            )

    def _delete_version(self, card: CardState, version: int) -> list[dict[str, Any]]:
        version_dir = self.paths.version_dir(card.experiment, version)
        targets = [
            self.paths.repo / "runs" / run_id
            for run_id in self.ports.evaluation.run_ids(version_dir)
        ]
        targets.append(self.paths.repro_out(card.experiment, version))
        listed: list[dict[str, Any]] = []
        for target in targets:
            if not target.is_dir():
                continue
            for path in sorted(item for item in target.rglob("*") if item.is_file()):
                data = path.read_bytes()
                listed.append(
                    {
                        "path": path.relative_to(self.paths.repo).as_posix(),
                        "size": len(data),
                        "sha256": _sha(data),
                    }
                )
            shutil.rmtree(target)
        return listed

    # --- 取り出し（D25 §3.6。決定 151） ---------------------------------------------------------

    def _intake(self, ctx: _Context) -> None:
        if self._stopped(ctx):
            return
        waiting = [
            card
            for card in ctx.cards.values()
            if _phase(card) in {CardPhase.C1, CardPhase.C2, CardPhase.C3}
        ]
        used, _ = self._used(ctx)
        if MAX_SLOTS - used < 1 or waiting:
            return
        view = self._view(ctx)
        known = set(ctx.state.cards)
        candidates = [card_id for card_id in queued_cards(view) if card_id not in known]
        if not candidates:
            return
        times = self.ports.git.first_seen_times(ctx.origin, candidates)
        ordered = order_cards(queue_from(times))
        if not ordered:
            return
        chosen = ordered[0]
        card = CardState(
            card_id=chosen.card_id,
            mouth=chosen.mouth,
            phase=CardPhase.C1.name,
            taken_at=format_time(ctx.now),
        )
        ctx.cards[card.card_id] = card
        ctx.state.cards.append(card.card_id)
        ctx.state.ideator.pop("queue_empty_used", None)
        self.events.add(ctx.now, "card_taken", card=card.card_id, mouth=card.mouth)
        self._advance(ctx, card)

    # --- 発想役（D25 §3.7・§5.3。決定 153） ----------------------------------------------------

    def _ideator(self, ctx: _Context) -> None:
        ideator = ctx.state.ideator
        running = ideator.get("running")
        if running is None:
            if self._stopped(ctx) or ctx.state.cards_pr is not None:
                return
            view = self._view(ctx)
            capacity = ai_capacity(view)
            if capacity <= 0:
                return
            empty = not queued_cards(view) or all(
                card_id in ctx.state.cards for card_id in queued_cards(view)
            )
            trigger = None
            if ideator.get("extra"):
                trigger = "extra"
            elif ideator.get("after_done"):
                trigger = "after_done"
            elif empty and not ideator.get("queue_empty_used"):
                trigger = "queue_empty"
            if trigger is None:
                return
            ideator.pop(trigger, None)
            if trigger == "queue_empty":
                ideator["queue_empty_used"] = True
            ideator["trigger"] = trigger
            self.events.add(ctx.now, "ideator_run")
            self._worker(ctx, None, "ideator", {"capacity": str(capacity)})
            return
        poll = self._worker(ctx, None, "ideator", running.get("extra"))
        if poll is None:
            return
        trigger = ideator.pop("trigger", None)
        accepted: dict[str, bytes] = {}
        discarded = 0
        output = poll.output
        if poll.status is WorkerStatus.DONE and isinstance(output, IdeatorOutput):
            view = self._view(ctx)
            capacity = ai_capacity(view)
            for raw in output.cards:
                if len(accepted) >= capacity:
                    discarded += 1
                    continue
                prepared = self._accept_ai_card(view, raw, output.read_paths, accepted)
                if prepared is None:
                    discarded += 1
                else:
                    accepted[prepared[0]] = prepared[1]
        self.events.add(ctx.now, "cards_written", count=len(accepted))
        self.events.add(ctx.now, "cards_discarded", count=discarded)
        if accepted:
            stamp = ctx.now.strftime("%Y%m%dT%H%M%SZ")
            files: dict[str, bytes | None] = {
                card_path("queue", card_id): raw for card_id, raw in accepted.items()
            }
            text = cards_pull(
                stamp=stamp,
                cards=sorted(accepted),
                at=format_time(ctx.now),
                files={path: body for path, body in files.items() if body is not None},
            )
            number = self._open_pr(ctx, files, text, auto=True)
            ctx.state.cards_pr = {"number": number, "cards": sorted(accepted)}
        elif trigger in {"queue_empty", "extra"} and not self._has_takeable(ctx):
            self._stop_global(ctx, "QUEUE_EMPTY")

    def _has_takeable(self, ctx: _Context) -> bool:
        return any(card_id not in ctx.state.cards for card_id in queued_cards(self._view(ctx)))

    def _accept_ai_card(
        self,
        view: RepoView,
        raw: bytes,
        read_paths: Sequence[str],
        accepted: Mapping[str, bytes],
    ) -> tuple[str, bytes] | None:
        """発想役のカードに第3.5節の 1〜6 を当てる（7 は取り出しの後。D25 §5.3 の 5）。"""
        try:
            data = yaml.safe_load(raw.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            return None
        if data.get("mouth") != "ai":
            return None
        try:
            card_id = available_card_id(view, data["id"])
        except (ToolFailure, ValueError):
            return None
        if card_id in accepted:
            return None
        if card_id != data["id"]:
            renamed = _rename_card(raw, card_id)
            if renamed is None:
                return None
            raw = renamed
        try:
            screening = screen_card(
                view, raw, path=card_path("queue", card_id), allowed_read_paths=read_paths
            )
        except ToolFailure:
            return None
        if not screening.passed and screening.reason != CARD_PREREG_REJECTED:
            return None
        return card_id, raw

    def _cards_pr(self, ctx: _Context) -> None:
        pull = ctx.state.cards_pr
        if pull is None:
            return
        number = int(pull["number"])
        status = self._pr_status(ctx, number)
        if status.state is PrState.MERGED:
            ctx.state.cards_pr = None
            self._clear_failure(ctx, FAIL_CI)
        elif status.state is PrState.CLOSED or status.checks is Checks.FAIL:
            # カードは待ち行列に入らない。失敗の種類 CI_FAILED として数える（D25 §5.3 の 5）。
            if status.state is PrState.OPEN:
                self.ports.github.close_pr(number)
            ctx.state.cards_pr = None
            self._count_failure(ctx, FAIL_CI)

    # --- 週の要約・4 週の報告（D25 §7・§4.8） -----------------------------------------------------

    def _weekly(self, ctx: _Context) -> None:
        now_jst = ctx.now.astimezone(JST)
        monday = (now_jst - timedelta(days=now_jst.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if now_jst < monday + timedelta(hours=9):
            return
        previous = monday - timedelta(days=7)
        key = previous.strftime("%Y-%m-%d")
        state = ctx.state
        if state.weekly_last_week == key:
            return
        if state.weekly_issue is None:
            state.weekly_issue = self.ports.github.create_issue(
                title=WEEKLY_TITLE,
                body="実行器の週の要約（D25 §7）。毎週月曜 09:00 JST の後に 1 つのコメントを書く。"
                "成績の数値・判定の値は書かない。止めたいときは「止めて」だけのコメントを書く。\n",
                label=WEEKLY_LABEL,
            )
        counts = self._weekly_counts(ctx, previous.astimezone(UTC), monday.astimezone(UTC))
        self.ports.github.comment(
            state.weekly_issue,
            weekly_body(
                start=previous.strftime("%Y-%m-%d"),
                end=(monday - timedelta(days=1)).strftime("%Y-%m-%d"),
                counts=counts,
            ),
        )
        state.weekly_last_week = key

    def _four_weeks(self, ctx: _Context) -> bool:
        last = ctx.state.last_human_answer_at
        return last is not None and ctx.now - parse_time(last) >= FOUR_WEEKS

    def _weekly_counts(self, ctx: _Context, start: datetime, end: datetime) -> WeeklyCounts:
        tally: dict[str, int] = {}
        stops: dict[str, int] = {}
        hours = 0.0
        deleted_files = deleted_bytes = 0
        written = discarded = 0
        taken = {"human": 0, "ai": 0}
        for item in self.events.between(start, end):
            kind = str(item["kind"])
            tally[kind] = tally.get(kind, 0) + 1
            if kind == "stop_new":
                stops[str(item.get("reason"))] = stops.get(str(item.get("reason")), 0) + 1
            elif kind == "run_done":
                hours += float(item.get("hours", 0.0))
            elif kind == "deleted":
                deleted_files += int(item.get("files", 0))
                deleted_bytes += int(item.get("bytes", 0))
            elif kind == "cards_written":
                written += int(item.get("count", 0))
            elif kind == "cards_discarded":
                discarded += int(item.get("count", 0))
            elif kind == "card_taken":
                mouth = str(item.get("mouth"))
                taken[mouth] = taken.get(mouth, 0) + 1
        view = self._view(ctx)
        queue = [card_id for card_id in queued_cards(view) if card_id not in ctx.state.cards]
        waiting = len(ctx.state.reasons) + sum(len(card.reasons) for card in ctx.cards.values())
        needs = sum(
            1 for card in ctx.cards.values() if card.find_reason("REVIEW_NEEDS_DECLARATION")
        )
        return WeeklyCounts(
            taken_human=taken.get("human", 0),
            taken_ai=taken.get("ai", 0),
            finished=tally.get("card_finished", 0),
            rejected=tally.get("card_rejected", 0),
            queue_human=sum(1 for card_id in queue if card_id.startswith("h-")),
            queue_ai=sum(1 for card_id in queue if card_id.startswith("a-")),
            runs_done=tally.get("run_done", 0),
            runs_failed=tally.get("run_failed", 0),
            runs_paused=tally.get("run_paused", 0),
            run_hours=hours,
            gate_pass=tally.get("gate_pass", 0),
            gate_fail=tally.get("gate_fail", 0),
            entrance_pass=tally.get("entrance_pass", 0),
            entrance_fail=tally.get("entrance_fail", 0),
            review_advance=tally.get("review_advance", 0),
            review_not=tally.get("review_not", 0),
            review_deferred=tally.get("review_deferred", 0),
            stops_new=stops,
            stops_waiting=waiting,
            ideator_runs=tally.get("ideator_run", 0),
            cards_written=written,
            cards_discarded=discarded,
            disk_free_gb=self.ports.system.free_disk_bytes(self.paths.repo) / 1000**3,
            deleted_files=deleted_files,
            deleted_gb=deleted_bytes / 1000**3,
            four_weeks=self._four_weeks(ctx),
            needs_declaration=needs,
        )


_LEDGER_FILES: Final = frozenset({TRIAL_LEDGER, REVIEW_LEDGER})


def _json_line(item: Mapping[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"


_ID_LINE: Final = re.compile(r'^id: "?(?P<value>[^"\n]+)"?$', re.MULTILINE)
_TITLE_LINE: Final = re.compile(r'^title: "?(?P<value>[^"\n]+)"?$', re.MULTILINE)


def _rename_card(raw: bytes, new: str) -> bytes | None:
    """AI の口のカードの識別子に番号を足す（`id` と `title` の 2 行だけを書き換える。第3.2節）。"""
    text = raw.decode("utf-8")
    new_title = new.split("-", 2)[2]
    replaced = _ID_LINE.sub(f'id: "{new}"', text, count=1)
    replaced = _TITLE_LINE.sub(f'title: "{new_title}"', replaced, count=1)
    data = yaml.safe_load(replaced)
    if not isinstance(data, dict) or data.get("id") != new or data.get("title") != new_title:
        return None
    return replaced.encode("utf-8")
