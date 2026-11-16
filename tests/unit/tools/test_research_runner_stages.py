"""確かめ・審査・関所 1・最終検証の段（表 2-1 の C9〜C12）と、止まる理由の扱い（D25 §4.6.1・§4.7）。

状態のファイルを直接置いて、その段から「1 歩」を進める（人工の世界。成績は扱わない）。
"""

from __future__ import annotations

import signal
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.hypothesis_cards import card_bytes, card_data
from tests.fixtures.research_runner import FINAL, REVIEW, Rig, make_rig
from tests.fixtures.runner_ci import final_ledger, review_ledger
from tools.ops.research_runner.adapters import NotYetGate, NotYetWorkers
from tools.ops.research_runner.ports import ReportOutput, VerdictOutput
from tools.ops.research_runner.runner import Ports, Runner
from tools.ops.research_runner.state import CardState, GlobalState, StateStore, format_time

CARD_ID = "h-20261120-dual-ema"
EXPERIMENT = "c_h20261120_dual_ema_gbpusd"
RAW = card_bytes(card_data())
REPORT = ReportOutput("予想の文", ("理由の文",), ("中身の文",), "analysis/c.md", "ef" * 32)


def _inject(rig: Rig, phase: str, *, place: str = "done", **fields: Any) -> None:
    """カードを `place` に置いた main と、`phase` の状態のファイルを作る。"""
    rig.world.put_on_main({f"research/hypotheses/{place}/{CARD_ID}.yaml": RAW})
    store = StateStore(rig.paths.state_dir)
    card = CardState(
        card_id=CARD_ID,
        mouth="human",
        phase=phase,
        taken_at=format_time(rig.clock.now()),
        symbol="GBPUSD",
        experiment=EXPERIMENT,
        version=fields.pop("version", 2),
        executions={"2": {"experiment_id": "a" * 64, "execution": 1}},
        repro="MATCH",
    )
    for key, value in fields.items():
        setattr(card, key, value)
    store.save_card(card)
    store.save_global(GlobalState(boot_id="boot-1", cards=[CARD_ID], weekly_last_week="2026-11-16"))


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path)


def _phase(rig: Rig) -> str:
    return str(rig.card(CARD_ID)["phase"])


def _reasons(rig: Rig) -> set[str]:
    return {item["reason"] for item in rig.card(CARD_ID)["reasons"]}


# --- C9 確かめ中 ----------------------------------------------------------------------


def test_the_gate_failure_rejects_the_card(rig: Rig) -> None:
    _inject(rig, "C9", place="taken", report={"text": {}, "comparison": {}})
    rig.gate.passed = False
    rig.step()  # T23（D25-16 (a): 棄却の型）
    assert _phase(rig) == "C14"
    assert rig.card(CARD_ID)["outcome"] == "GATE_FAILED"
    assert rig.world.routine()[0].ci_ok, rig.world.routine()[0].ci_messages


def test_the_verifier_rewrites_once_then_rejects(rig: Rig) -> None:
    _inject(rig, "C9", place="taken")
    rig.workers.add("verifier", VerdictOutput(False, ("REASON_GAP",)))
    rig.workers.add("analyst_report", REPORT)
    rig.workers.add("verifier", VerdictOutput(False, ("REASON_GAP",)))
    rig.step()
    rig.step()  # T24: 1 回目の不合格は報告を書き直させる
    assert _phase(rig) == "C8"
    for _ in range(4):
        rig.step()
    assert _phase(rig) == "C14"  # T25: 2 回目は棄却
    assert rig.card(CARD_ID)["outcome"] == "VERIFIER_FAILED"


def test_the_worker_not_yet_available_is_a_tool_failure(rig: Rig) -> None:
    """作業役の実物は PR 8b。本 PR の実物では道具の失敗（1 回やり直した後に停止）。"""
    _inject(rig, "C8", place="taken")
    runner = rig.runner()
    ports = runner.ports
    replaced = Ports(
        **{**{name: getattr(ports, name) for name in ports.__dataclass_fields__}},
    )
    object.__setattr__(replaced, "workers", NotYetWorkers())
    object.__setattr__(replaced, "gate", NotYetGate())
    rig.clock.advance(minutes=5)
    Runner(rig.paths, replaced).step()
    rig.clock.advance(minutes=5)
    Runner(rig.paths, replaced).step()
    assert _phase(rig) == "C13"
    assert _reasons(rig) == {"TOOL_FAILURE"}


# --- C10 入口・審査中 -----------------------------------------------------------------


def _review(rig: Rig, code: int, *, row: bool = True) -> None:
    _inject(rig, "C10", reservations=["review"])
    rig.step()  # 空きで審査を起動
    review = rig.procs.by_kind("review")
    assert "--snapshots" in review["command"]
    if row:
        (rig.world.exec_dir / REVIEW).write_bytes(review_ledger(1))
    rig.procs.finish("review", code)
    rig.step()


def test_a_review_that_does_not_proceed_ends_the_card(rig: Rig) -> None:
    _review(rig, 7)
    for _ in range(2):
        rig.step()
    assert _phase(rig) == "C14"
    exec_pr = [pr for pr in rig.world.routine() if pr.branch == "runner/exec"]
    assert exec_pr and exec_pr[0].ci_ok, exec_pr[0].ci_messages


def test_an_entrance_refusal_without_a_row_ends_the_card_without_a_pr(rig: Rig) -> None:
    _review(rig, 7, row=False)
    assert _phase(rig) == "C14"
    assert not [pr for pr in rig.world.routine() if pr.branch == "runner/exec"]


def test_a_deferred_review_stops_and_can_be_redone(rig: Rig) -> None:
    _review(rig, 8)
    for _ in range(2):
        rig.step()
    assert _reasons(rig) == {"REVIEW_DEFERRED"}
    issue = rig.world.issue_for("REVIEW_DEFERRED")
    rig.world.answer(issue.number, "2")
    rig.step()
    assert _phase(rig) == "C10"
    assert len([key for key in rig.procs.spawned if key.startswith("review:")]) == 2


def test_a_review_read_error_retries_once_then_stops(rig: Rig) -> None:
    _review(rig, 2, row=False)
    assert _phase(rig) == "C10"
    rig.step()
    rig.procs.finish("review", 2)
    rig.step()
    assert _reasons(rig) == {"REVIEW_FAILED"}


def test_the_review_waits_for_the_declaration_removal(rig: Rig) -> None:
    """申告の撤去（PR 4b）の前は審査を起動せずに止まり、入れば自動で起動する。"""
    rig.evaluation.declaration = True
    _inject(rig, "C10", reservations=["review"])
    rig.step()
    assert _reasons(rig) == {"REVIEW_NEEDS_DECLARATION"}
    assert not rig.procs.spawned
    rig.evaluation.declaration = False
    rig.step()
    assert _phase(rig) == "C10"
    assert rig.procs.by_kind("review")


# --- C11 関所 1 待ち ------------------------------------------------------------------


def test_the_fixation_tool_missing_waits_and_resumes(rig: Rig) -> None:
    rig.evaluation.fix = False
    _inject(rig, "C11")
    rig.step()
    assert _reasons(rig) == {"FIXATION_TOOL_MISSING"}
    rig.evaluation.fix = True
    rig.step()
    assert _phase(rig) == "C11"
    assert rig.procs.by_kind("fix")


def test_a_deferred_fixation_retries_a_day_later(rig: Rig) -> None:
    _inject(rig, "C11")
    rig.step()
    rig.procs.finish("fix", 8)
    rig.step()
    assert _phase(rig) == "C11" and not _reasons(rig)
    rig.step(minutes=60)
    assert len([key for key in rig.procs.spawned if key.startswith("fix:")]) == 1
    rig.step(minutes=24 * 60)
    assert len([key for key in rig.procs.spawned if key.startswith("fix:")]) == 2


def test_a_fixation_check_failure_stops(rig: Rig) -> None:
    _inject(rig, "C11")
    rig.step()
    rig.procs.finish("fix", 7)
    rig.step()
    assert _reasons(rig) == {"FIXATION_CHECK_FAILED"}


def test_a_closed_fixation_pr_ends_the_card(rig: Rig) -> None:
    _inject(rig, "C11")
    rig.step()
    record = rig.world.exec_dir / "research/candidate_fixes" / ("a" * 64) / "1" / "1.yaml"
    record.parent.mkdir(parents=True)
    record.write_text("schema_version: 1\n", encoding="utf-8")
    rig.procs.finish("fix", 0)
    rig.step()
    fix = [pr for pr in rig.world.prs.values() if pr.branch.startswith("candidate-fix/")]
    assert fix and not fix[0].auto
    assert fix[0].ci_ok, fix[0].ci_messages  # 定型でない PR として CI を通る
    rig.github.close_pr(fix[0].number)  # 人間が閉じた（T34）
    rig.step()
    assert _phase(rig) == "C14"
    assert rig.card(CARD_ID)["outcome"] == "HUMAN_DECLINED"
    assert rig.state()["last_human_answer_at"]


def test_a_fixation_pr_that_failed_ci_resolves_when_it_is_merged(rig: Rig) -> None:
    """固定の記録の PR の CI が 2 回落ちて止まっても、直されて merge されれば自動で解け、
    最終検証の予約へ進む（`card.pr` ではなく `fix_pr` で持つ PR）。"""
    _inject(rig, "C11")
    rig.step()
    record = rig.world.exec_dir / "research/candidate_fixes" / ("a" * 64) / "1" / "1.yaml"
    record.parent.mkdir(parents=True)
    record.write_text("schema_version: 1\n", encoding="utf-8")
    rig.procs.finish("fix", 0)
    rig.step()
    fix = next(pr for pr in rig.world.prs.values() if pr.branch.startswith("candidate-fix/"))
    fix.forced_fail = 2
    rig.step()  # 1 回目: 走らせ直す
    rig.step()  # 2 回目: 停止
    assert _phase(rig) == "C13" and _reasons(rig) == {"CI_FAILED"}
    rig.world.merge(fix.number)  # 人間が直して merge した
    rig.step()
    assert _reasons(rig) == set()
    assert _phase(rig) == "C12"
    assert "fix_pr" not in rig.card(CARD_ID)["checks"]


# --- C12 最終検証中 -------------------------------------------------------------------


def test_the_main_checkout_must_be_clean_and_on_main(rig: Rig) -> None:
    _inject(rig, "C12", reservations=["final"])
    rig.world.main_clean = False
    rig.step()
    assert not rig.procs.spawned and not _reasons(rig)
    rig.step(minutes=25 * 60)  # 1 日待っても直らない（仮置き 32）
    assert _reasons(rig) == {"MAIN_CHECKOUT_NOT_READY"}


def test_final_validation_state_a_retries_once_and_state_b_stops(rig: Rig) -> None:
    _inject(rig, "C12", reservations=["final"])
    rig.step()
    command = rig.procs.by_kind("final-validate")["command"]
    assert "--declaration" not in command and "--by" in command
    rig.procs.finish("final-validate", 8)  # 状態 A（消費の行が origin に無い）
    rig.step()
    assert _phase(rig) == "C12"
    rig.step()
    rig.procs.finish("final-validate", 1)  # 状態 B（ロックが残る）
    rig.step()
    assert _reasons(rig) == {"FINAL_VALIDATION_FAILED"}


def test_a_finished_final_validation_goes_through_its_record_pr(rig: Rig) -> None:
    _inject(rig, "C12", reservations=["final"])
    rig.step()
    (rig.world.repo / FINAL).write_bytes(final_ledger(rig.world.repo / "tmp", 1))
    rig.procs.finish("final-validate", 0)
    for _ in range(3):
        rig.step()
    assert _phase(rig) == "C14"
    assert rig.card(CARD_ID)["outcome"] == "FINAL_VALIDATED"


def test_ending_a_card_is_refused_while_the_final_validation_runs(rig: Rig) -> None:
    _inject(rig, "C12", reservations=["final"])
    rig.step()
    card = StateStore(rig.paths.state_dir).load_card(CARD_ID)
    runner = rig.runner()
    from tools.ops.research_runner.runner import _Context

    ctx = _Context(state=GlobalState(), cards={CARD_ID: card}, now=rig.clock.now(), origin="")
    assert runner._begin_end(ctx, card, "HUMAN_ENDED") is False  # noqa: SLF001
    assert not rig.procs.signals


# --- 全体の理由 -----------------------------------------------------------------------


def test_a_ledger_check_failure_stops_the_card_and_everything(rig: Rig) -> None:
    rig.world.put_on_main({f"research/hypotheses/queue/{CARD_ID}.yaml": RAW})
    for _ in range(2):
        rig.step()
    assert _phase(rig) == "C4"
    rig.evaluation.ledger_code = 1
    rig.wrapper.finish(0, 0)
    rig.step()
    rig.step()
    assert "LEDGER_CHECK_FAILED" in _reasons(rig)
    assert "LEDGER_CHECK_FAILED" in {item["reason"] for item in rig.state()["reasons"]}
    issue = rig.world.issue_for("LEDGER_CHECK_FAILED")
    rig.evaluation.ledger_code = 0
    rig.world.answer(issue.number, "2")  # 確かめからやり直す
    for _ in range(3):
        rig.step()
    assert "LEDGER_CHECK_FAILED" not in {item["reason"] for item in rig.state()["reasons"]}
    assert _phase(rig) in {"C7", "C6"}


def test_a_research_policy_before_version_five_stops_the_intake(rig: Rig) -> None:
    rig.world.put_on_main({f"research/hypotheses/queue/{CARD_ID}.yaml": RAW})
    runner = rig.runner()
    runner.policy_floor = 6
    rig.clock.advance(minutes=5)
    runner.step()
    assert rig.state()["reasons"][0]["detail"].startswith("研究ポリシーの現行が第 6 版より前")
    assert not rig.world.prs


def test_ending_a_running_card_terminates_its_process_groups(rig: Rig) -> None:
    rig.world.put_on_main({f"research/hypotheses/queue/{CARD_ID}.yaml": RAW})
    for _ in range(2):
        rig.step()
    run_pid = rig.wrapper.launched[0]["pids"][0]
    rig.wrapper.finish(0, 3)  # 失敗 → RUN_FAILED（カードの停止）
    rig.procs.alive_pids.add(run_pid)  # 子が残っている
    rig.step()
    issue = rig.world.issue_for("RUN_FAILED")
    rig.world.answer(issue.number, "2")  # このカードを終える（T41）
    rig.step()
    assert (run_pid, signal.SIGTERM) in rig.procs.signals
    assert _phase(rig) == "C14"
    assert issue.closed
