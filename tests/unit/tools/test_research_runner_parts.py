"""実行器の部品の試験。

理由の表・問いの文・答えの読み方・空きと見積り・包みとの約束・排他・状態の読み書き。
"""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.ops.research_runner.lock import StepLock
from tools.ops.research_runner.reasons import REASONS, Action, Scope, options_for
from tools.ops.research_runner.resources import (
    MEMORY_FLOOR_BYTES,
    estimate_seconds,
    free_memory_from_vm_stat,
    plan_slots,
)
from tools.ops.research_runner.runs import move_attempt, probe_contract, read_run
from tools.ops.research_runner.state import CardState, GlobalState, OpenReason, StateStore
from tools.ops.research_runner.texts import (
    WeeklyCounts,
    parse_answer,
    question_body,
    question_title,
    weekly_body,
)

#: D25 §4.7 の表の理由の識別子（全体の道具の失敗は表示が `TOOL_FAILURE`）。
DESIGNED = {
    "SAME_FAILURE_TWICE",
    "RUN_OVER_ESTIMATE",
    "COST_RISK",
    "SEALED_NEEDED",
    "DISK_LOW",
    "USAGE_LIMIT",
    "REJECT_THREE_IN_A_ROW",
    "QUEUE_EMPTY",
    "HUMAN_PAUSE",
    "CHANGE_OUT_OF_RULES",
    "VALUE_MISSING",
    "CHANGE_PREREG_REJECTED",
    "CONVERSION_STALE",
    "RUN_FAILED",
    "TOOL_FAILURE",
    "CI_FAILED",
    "LEDGER_CHECK_FAILED",
    "REPRO_MISMATCH",
    "GATE_FAILED",
    "VERIFIER_FAILED",
    "REVIEW_FAILED",
    "REVIEW_NEEDS_DECLARATION",
    "REVIEW_DEFERRED",
    "FIXATION_TOOL_MISSING",
    "FIXATION_CHECK_FAILED",
    "MAIN_CHECKOUT_NOT_READY",
    "FINAL_VALIDATION_FAILED",
    "AMBIGUOUS_RULE",
}


def test_every_designed_reason_has_a_question_with_the_recommendation_first() -> None:
    assert DESIGNED <= set(REASONS)
    for identifier, row in REASONS.items():
        assert 2 <= len(row.options) <= 3, identifier
        assert "推奨" in row.options[0].label, identifier
    assert REASONS["LEDGER_CHECK_FAILED"].scope is Scope.BOTH
    assert REASONS["HUMAN_PAUSE"].scope is Scope.GLOBAL
    assert REASONS["RUN_FAILED"].scope is Scope.CARD


def test_version_three_hides_another_change_for_conversion_stale() -> None:
    assert Action.OTHER_CHANGE in {option.action for option in options_for("CONVERSION_STALE")}
    hidden = options_for("CONVERSION_STALE", version=3)
    assert Action.OTHER_CHANGE not in {option.action for option in hidden}


def test_the_question_has_the_protocol_shape_and_no_scores() -> None:
    body = question_body(
        question=REASONS["RUN_FAILED"].question,
        reason="RUN_FAILED",
        card="h-20261120-card",
        stage="C13 停止中",
        version=2,
        at="2026-11-24T01:00:00Z",
        options=options_for("RUN_FAILED"),
    )
    lines = body.splitlines()
    assert lines[0].startswith("【何を決めるか】") and lines[1].startswith("【背景】")
    assert lines[2].startswith("1. ") and "（推奨）" in lines[2]
    assert lines[-1].startswith("返信は番号だけで可。")
    assert question_title("TOOL_FAILURE_GLOBAL", None) == "[runner] 止まった: TOOL_FAILURE 全体"


@pytest.mark.parametrize(
    ("body", "expected"),
    [("1", 1), (" 2 \n", 2), ("他: 1 にしたい", None), ("12", None), ("4", None), ("１", None)],
)
def test_only_a_bare_number_is_an_answer(body: str, expected: int | None) -> None:
    assert parse_answer(body, choices=3) == expected


def test_the_weekly_summary_has_counts_only() -> None:
    body = weekly_body(
        start="2026-11-16",
        end="2026-11-22",
        counts=WeeklyCounts(stops_new={"RUN_FAILED": 1}, four_weeks=True),
    )
    assert body.splitlines()[0] == "週: 2026-11-16〜2026-11-22"
    assert "止まり: 新しく 1（RUN_FAILED 1）" in body
    assert body.rstrip().endswith("前の関所の答えから 4 週が経った")


def test_the_estimate_and_the_slots() -> None:
    assert estimate_seconds(4) == int(2 * (5 * 728 * 4 + 1743 + 1743) * 1.40)
    with pytest.raises(ValueError):
        estimate_seconds(0)
    assert plan_slots(used=0, heavy_running=0, free_memory=0, run=True).mode == "parallel"
    assert plan_slots(used=0, heavy_running=0, free_memory=0, run=False).slots == 1
    low = plan_slots(used=1, heavy_running=1, free_memory=MEMORY_FLOOR_BYTES - 1, run=True)
    assert not low.allowed
    one = plan_slots(used=1, heavy_running=1, free_memory=MEMORY_FLOOR_BYTES, run=True)
    assert (one.allowed, one.slots, one.mode) == (True, 1, "serial")
    assert not plan_slots(used=2, heavy_running=1, free_memory=10**12, run=False).allowed


def test_free_memory_from_vm_stat() -> None:
    text = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               100.\n"
        "Pages active:                             999.\n"
        "Pages inactive:                            50.\n"
        "Pages speculative:                         10.\n"
    )
    assert free_memory_from_vm_stat(text) == 160 * 16384
    assert free_memory_from_vm_stat("garbage") == 0


def test_the_wrapper_contract_needs_pr_b() -> None:
    from tools.ops import run_experiment

    # 本物の包み: PR-B（#194）の前なら止まり、後なら通る（merge の前後のどちらでも落ちない）。
    real = probe_contract(run_experiment)
    assert real.ready is hasattr(run_experiment, "VARIANT_MODES")
    before = types.ModuleType("before")
    before.EXIT_REFUSED = 2  # type: ignore[attr-defined]
    stopped = probe_contract(before)
    assert not stopped.ready and stopped.locked_exit_code is None
    assert "--variant-mode" in stopped.problems[0]
    serial_only = types.ModuleType("serial_only")
    serial_only.VARIANT_MODES = ("serial",)  # type: ignore[attr-defined]
    assert not probe_contract(serial_only).ready
    after = types.ModuleType("after")  # PR #194 の head 170bb91 の包みの形
    after.VARIANT_MODES = ("parallel", "serial")  # type: ignore[attr-defined]
    after.DEFAULT_VARIANT_MODE = "parallel"  # type: ignore[attr-defined]
    contract = probe_contract(after)
    assert contract.ready and contract.locked_exit_code == 10


def test_moving_an_attempt_keeps_the_files(tmp_path: Path) -> None:
    (tmp_path / "launch.json").write_text(json.dumps({"run_pid": 7, "launcher_pid": 8}))
    (tmp_path / "exit_code.txt").write_text("1\n")
    (tmp_path / "experiment_manifest.json").write_text("{}")
    files = read_run(tmp_path)
    assert (files.launched, files.exit_code, files.run_pid, files.launcher_pid) == (True, 1, 7, 8)
    target = move_attempt(tmp_path, 1)
    assert (target / "launch.json").is_file() and not (tmp_path / "launch.json").exists()
    assert (tmp_path / "experiment_manifest.json").is_file()  # experiment run の記録は動かさない
    with pytest.raises(FileExistsError):
        move_attempt(tmp_path, 1)


def test_the_step_lock_is_exclusive_and_stale_locks_are_cleared(tmp_path: Path) -> None:
    now = datetime(2026, 11, 24, tzinfo=UTC)
    alive = {123}
    first = StepLock(tmp_path / "step.lock", lambda: now, lambda pid: pid in alive)
    assert first.acquire()
    second = StepLock(tmp_path / "step.lock", lambda: now, lambda pid: pid in alive)
    assert not second.acquire()
    first.release()
    (tmp_path / "step.lock").write_text('{"pid": 123, "created_at": "2026-11-23T00:00:00Z"}')
    assert not second.acquire()  # 古いが書いたプロセスが生きている
    alive.clear()
    assert second.acquire()  # 30 分より古く、プロセスが居ない
    late = StepLock(tmp_path / "other.lock", lambda: now + timedelta(minutes=1), lambda pid: False)
    (tmp_path / "other.lock").write_text('{"pid": 1, "created_at": "2026-11-24T00:00:00Z"}')
    assert not late.acquire()  # 30 分より新しい


def test_the_state_round_trips(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    card = CardState("h-20261120-card", "human", reasons=[OpenReason("RUN_FAILED", 5)])
    store.save_card(card)
    state = GlobalState(cards=[card.card_id], reasons=[OpenReason("DISK_LOW", 6, card=None)])
    store.save_global(state)
    loaded = store.load_global()
    assert loaded is not None and loaded.phase.name == "G1"
    assert next(store.cards(loaded)) == card
    assert not list(tmp_path.rglob("*.tmp"))
