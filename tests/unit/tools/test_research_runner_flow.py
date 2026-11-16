"""実行器の一巡の通し（D25 §2.1 の段 1〜9、表 2-1 の主な遷移）を人工の世界で確かめる。

本物の run・GitHub・作業役は使わない（`tests.fixtures.research_runner`）。実行器が作った定型 PR には
CI の段（`tools.ops.runner_ci`）の検査 1〜8 を本物のまま当て、全部が通ることを確かめる。
成績の数値・判定の値は扱わない（台帳の行・記録はすべて人工）。
"""

from __future__ import annotations

import signal
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.hypothesis_cards import card_bytes, card_data
from tests.fixtures.research_runner import FINAL, Rig, make_rig
from tests.fixtures.runner_ci import final_ledger
from tools.ops.hypothesis_cards.emit import dump_document
from tools.ops.research_runner.ports import (
    ChangeOutput,
    ReportOutput,
    VerdictOutput,
    WorkerStatus,
)
from tools.ops.research_runner.resources import estimate_seconds
from tools.ops.research_runner.runner import Runner

CARD_ID = "h-20261120-dual-ema"
EXPERIMENT = "c_h20261120_dual_ema_gbpusd"
QUEUE = f"research/hypotheses/queue/{CARD_ID}.yaml"
MEMO = "cd" * 32


def _change(from_version: int, operations: list[dict[str, Any]]) -> bytes:
    return dump_document(
        {
            "schema_version": 1,
            "card": CARD_ID,
            "from_version": from_version,
            "decision": "CHANGE",
            "change_kind": "PARAMETERS",
            "operations": operations,
            "reason": "長い側の移動平均を広げると取引の数がどう変わるかを見る",
            "prediction": "取引が減り、横ばいの局面での負けが減る",
            "memo_sha256": MEMO,
        }
    )


FIRST = _change(
    1,
    [
        {
            "op": "set_axis_values",
            "instance": "slow_ema",
            "parameter": "period",
            "values": [100, 150],
        }
    ],
)
SECOND = _change(
    2,
    [
        {"op": "remove_axis", "instance": "take_profit", "parameter": "reward_risk"},
        {
            "op": "set_axis_values",
            "instance": "slow_ema",
            "parameter": "period",
            "values": [60, 80, 100, 150],
        },
    ],
)


def _analysis(raw: bytes, decision: str = "CHANGE") -> ChangeOutput:
    return ChangeOutput(decision, raw, "analysis/memo.md", MEMO)


REPORT = ReportOutput(
    prediction="上昇の局面で取引が多いと予想した",
    change_reasons=("長い側を広げて取引を絞る", "利確の軸を外して試行を揃える"),
    change_contents=("長い側の期間の格子を変えた", "利確の倍率の軸を外した"),
    comparison_path="analysis/comparison.md",
    comparison_sha256="ef" * 32,
)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    made = make_rig(tmp_path)
    made.world.put_on_main({QUEUE: card_bytes(card_data())})
    return made


def _assert_ci_passed(rig: Rig) -> None:
    for pr in rig.world.routine():
        assert pr.ci_ok, (pr.branch, pr.ci_messages)


def _phase(rig: Rig, card: str = CARD_ID) -> str:
    return str(rig.card(card)["phase"])


def _until(rig: Rig, phase: str, *, card: str = CARD_ID, limit: int = 6) -> None:
    """カードが `phase` になるまで「1 歩」を進める（`limit` 歩まで）。"""
    path = rig.paths.state_dir / "cards" / f"{card}.json"
    for _ in range(limit):
        if path.is_file() and _phase(rig, card) == phase:
            return
        rig.step()
    assert _phase(rig, card) == phase


def _run_version(rig: Rig, version: int) -> None:
    """版 `version` の run が起動している状態から、run を終えて記録の PR を作るまで進める。"""
    _until(rig, "C4")
    assert rig.card(CARD_ID)["version"] == version
    rig.wrapper.finish(len(rig.wrapper.launched) - 1, 0, lines=2)
    rig.step()


def test_a_human_card_goes_through_the_whole_loop(rig: Rig) -> None:
    """取り出し → 事前登録 → 実行 → 記録 → 改善 2 回 → 報告 → 確かめ → 審査 → 関所 1 → 最終検証。"""
    workers = rig.workers
    workers.add("analyst_change", _analysis(FIRST))
    workers.add("analyst_change", _analysis(SECOND))
    workers.add("analyst_report", REPORT)
    workers.add("verifier", VerdictOutput(True))

    rig.step()  # T1・T2: 取り出し、検査、事前登録の定型 PR
    assert _phase(rig) == "C2"
    prereg = rig.world.routine()[0]
    assert prereg.branch == f"runner/prereg-{CARD_ID}-v1"
    assert prereg.auto
    _assert_ci_passed(rig)

    rig.step()  # T5・T7: merge → 起動（空きが 2 つなので並列を要求し、枠を 2 つ使う）
    assert _phase(rig) == "C4"
    assert rig.wrapper.launched[0]["mode"] == "parallel"
    assert rig.card(CARD_ID)["run"]["slots"] == 2
    assert rig.card(CARD_ID)["run"]["limit_seconds"] == 2 * estimate_seconds(4)

    _run_version(rig, 1)  # T8: 記録の PR（台帳の追記と数値なしの実行記録）
    exec_pr = rig.world.routine()[-1]
    assert exec_pr.branch == "runner/exec"
    assert f"research/loops/{CARD_ID}/run_v1.yaml" in rig.world.files(exec_pr.commit)
    _assert_ci_passed(rig)

    _until(rig, "C2")  # T14: merge → 分析役（版 1）→ 改善の事前登録の PR（版 2）
    assert rig.card(CARD_ID)["version"] == 2
    assert "reproduce:" in " ".join(rig.procs.spawned)  # 元の実験の再現（決定 107）
    _assert_ci_passed(rig)
    rig.procs.finish("reproduce", 0)

    _run_version(rig, 2)  # 版 2（再現が終わって枠が空いた）
    assert rig.card(CARD_ID)["repro"] == "MATCH"
    _until(rig, "C2")  # 版 2 の記録の merge → 分析役 → 版 3 の事前登録
    assert rig.card(CARD_ID)["version"] == 3
    _run_version(rig, 3)
    _until(rig, "C10")  # T15・T21・T22: 版 3 の記録 → 報告 → 確かめ → 一巡の報告の PR
    report = [pr for pr in rig.world.routine() if pr.branch == f"runner/report-{CARD_ID}"]
    assert report and report[0].title == f"[runner] 一巡の報告 {CARD_ID} v3"
    _assert_ci_passed(rig)
    rig.step()
    assert "--execution" in rig.procs.by_kind("review")["command"]

    _until(rig, "C10")
    rig.procs.finish("review", 0)  # 審査が「進める」（行は人工の世界では足さない）
    _until(rig, "C11")  # T28: C11 → 固定の前の確かめを起動
    rig.step()
    fix = rig.procs.by_kind("fix")
    record = rig.world.exec_dir / "research/candidate_fixes" / ("a" * 64) / "1" / "1.yaml"
    record.parent.mkdir(parents=True)
    record.write_text("schema_version: 1\n", encoding="utf-8")
    rig.procs.finish("fix", 0, output="固定の前の確かめ: 8 項目 合格\n")
    assert fix["command"][5] == "fix"
    rig.step()  # 関所 1 の PR（定型でない。自動 merge を付けない）
    fix_pr = [pr for pr in rig.world.prs.values() if pr.branch.startswith("candidate-fix/")]
    assert len(fix_pr) == 1 and not fix_pr[0].auto
    assert fix_pr[0].title.startswith("[関所 1] ")
    assert not record.exists()
    rig.world.merge(fix_pr[0].number)  # 人間の merge（関所 1）

    _until(rig, "C12")  # T33・T35: 最終検証を主の作業木で起動
    rig.step()
    final = rig.procs.by_kind("final-validate")
    assert final["cwd"] == rig.world.repo
    (rig.world.repo / FINAL).write_bytes(final_ledger(rig.world.repo / "tmp", 1))
    rig.procs.finish("final-validate", 0)
    _until(rig, "C14")  # 最終検証の台帳の行の定型 PR → merge → 主の作業木を戻して早送り
    final_pr = [pr for pr in rig.world.routine() if pr.branch.startswith("runner/final-")]
    assert final_pr and final_pr[0].branch == f"runner/final-{EXPERIMENT}-1"
    _assert_ci_passed(rig)
    assert rig.world.main_head == rig.world.main
    assert not [issue for issue in rig.world.open_issues() if issue.label == "runner-stop"]


def test_the_wrapper_before_pr_b_stops_everything_without_launching(rig: Rig) -> None:
    """包みが D26 の PR-B の前なら、何も起動せずに全体の停止（D25 §8）。そろえば自動で解ける。"""
    rig.wrapper.ready = False
    rig.step()
    assert rig.state()["reasons"][0]["reason"] == "TOOL_FAILURE_GLOBAL"
    issue = rig.world.issue_for("TOOL_FAILURE")
    assert issue.label == "runner-stop"
    assert "【何を決めるか】" in issue.body and "1. " in issue.body
    assert not rig.wrapper.launched and not rig.world.prs
    rig.step()
    stops = [issue for issue in rig.world.issues.values() if issue.label == "runner-stop"]
    assert len(stops) == 1  # 同じ理由で issue を増やさない
    rig.wrapper.ready = True
    rig.step()
    assert rig.state()["reasons"] == []
    assert issue.closed
    assert _phase(rig) == "C2"


def test_a_rejected_card_is_moved_to_done_and_three_in_a_row_stop(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    for index, title in enumerate(("bad-one", "bad-two", "bad-three")):
        data = card_data(title=title, created_at=f"2026-11-2{index}T03:00:00Z")
        data["evidence_estimate"]["reaches_floor"] = "UNLIKELY"
        rig.world.put_on_main({f"research/hypotheses/queue/{data['id']}.yaml": card_bytes(data)})
        rig.clock.advance(minutes=1)
    for _ in range(3):
        rig.step()
    _assert_ci_passed(rig)
    done = [pr for pr in rig.world.routine() if pr.branch.startswith("runner/report-")]
    assert len(done) == 3
    assert all(pr.title.endswith(" v0") for pr in done)
    assert rig.state()["reasons"][0]["reason"] == "REJECT_THREE_IN_A_ROW"
    issue = rig.world.issue_for("REJECT_THREE_IN_A_ROW")
    rig.world.answer(issue.number, " 2 ")
    rig.step()
    assert rig.state()["reasons"] == [] and rig.state()["reject_streak"] == 0


def _launch(rig: Rig) -> None:
    rig.step()
    rig.step()
    assert _phase(rig) == "C4"


def test_a_run_over_twice_the_estimate_is_paused_and_resumed(rig: Rig) -> None:
    _launch(rig)
    run_pid = rig.wrapper.launched[0]["pids"][0]
    limit = int(rig.card(CARD_ID)["run"]["limit_seconds"])
    rig.step(minutes=limit / 60 + 1)  # T10
    assert _phase(rig) == "C5"
    assert (run_pid, signal.SIGSTOP) in rig.procs.signals
    issue = rig.world.issue_for("RUN_OVER_ESTIMATE")
    rig.world.answer(issue.number, "他: 少し待って")
    rig.step(minutes=30)
    assert _phase(rig) == "C5" and not issue.closed
    rig.world.answer(issue.number, "1")
    rig.step(minutes=10)  # T11: 再開し、上限を「止めた時点まで＋見積り 1 倍」に
    run = rig.card(CARD_ID)["run"]
    assert _phase(rig) == "C4" and issue.closed
    assert (run_pid, signal.SIGCONT) in rig.procs.signals
    assert run["paused_seconds"] >= 40 * 60
    assert run["limit_seconds"] == pytest.approx(limit + 60 + estimate_seconds(4), abs=600)


def test_ending_a_paused_run_terminates_the_group_and_moves_the_card(rig: Rig) -> None:
    _launch(rig)
    run_pid = rig.wrapper.launched[0]["pids"][0]
    rig.step(minutes=int(rig.card(CARD_ID)["run"]["limit_seconds"]) / 60 + 1)
    issue = rig.world.issue_for("RUN_OVER_ESTIMATE")
    rig.world.answer(issue.number, "2")
    rig.step()  # T12: SIGTERM の後に SIGCONT、終わりを確かめてから done/ へ
    assert rig.procs.signals[-2:] == [(run_pid, signal.SIGTERM), (run_pid, signal.SIGCONT)]
    assert _phase(rig) == "C14"
    assert rig.card(CARD_ID)["outcome"] == "RUN_OVER_ESTIMATE"
    rig.step()
    done = [pr for pr in rig.world.routine() if pr.branch == f"runner/report-{CARD_ID}"]
    assert done and done[0].ci_ok, done[0].ci_messages


def test_a_failed_run_is_rerun_as_a_new_attempt_after_the_answer(rig: Rig) -> None:
    _launch(rig)
    rig.wrapper.finish(0, 1)
    rig.step()  # T9
    assert _phase(rig) == "C13"
    assert rig.state()["failure_streaks"]["run"] == 1
    issue = rig.world.issue_for("RUN_FAILED")
    rig.world.answer(issue.number, "1")
    rig.step()  # 包みのファイルを runner_attempts/1/ に移して C3 → 起動し直す
    version_dir = rig.world.repo / "runs/experiments" / EXPERIMENT / "v1"
    assert (version_dir / "runner_attempts/1/launch.json").is_file()
    assert _phase(rig) == "C4"
    assert rig.card(CARD_ID)["run"]["attempt"] == 2
    assert len(rig.wrapper.launched) == 2


def test_two_failures_of_the_same_kind_stop_everything(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    cards = []
    for title, symbol in (("first-card", "GBPUSD"), ("second-card", "AUDUSD")):
        data = card_data(title=title, symbol=symbol)
        cards.append(data["id"])
        rig.world.put_on_main({f"research/hypotheses/queue/{data['id']}.yaml": card_bytes(data)})
        rig.clock.advance(minutes=1)
    _until(rig, "C4", card=cards[0])
    rig.wrapper.finish(0, 1)
    _until(rig, "C4", card=cards[1])
    assert rig.state()["failure_streaks"]["run"] == 1
    rig.wrapper.finish(1, 1)
    rig.step()
    assert "SAME_FAILURE_TWICE" in {item["reason"] for item in rig.state()["reasons"]}


def test_the_lock_refusal_is_not_a_failure_and_waits(rig: Rig) -> None:
    _launch(rig)
    rig.wrapper.finish(0, rig.wrapper.locked_code, lines=0)
    rig.step()
    assert _phase(rig) == "C3"
    assert rig.state()["failure_streaks"].get("run", 0) == 0
    rig.step()
    assert len(rig.wrapper.launched) == 1  # 15 分は待つ
    rig.step(minutes=15)
    assert len(rig.wrapper.launched) == 2


def test_a_vanished_run_without_an_exit_code_is_a_failure(rig: Rig) -> None:
    """起動し直した後（G2 → e17）に run のプロセスが消えていて exit_code.txt が無い: T9。"""
    _launch(rig)
    rig.procs.alive_pids -= set(rig.wrapper.launched[0]["pids"])
    rig.system.boot = "boot-2"
    notes = rig.step()
    assert any("起動し直した" in note for note in notes)
    assert _phase(rig) == "C13"
    assert rig.world.issue_for("RUN_FAILED")


def test_ci_failure_reruns_once_then_stops_and_resolves_on_merge(rig: Rig) -> None:
    rig.step()
    pr = rig.world.routine()[0]
    pr.forced_fail = 2
    rig.step()  # 1 回目: 走らせ直す
    assert pr.reruns == 1 and _phase(rig) == "C2"
    rig.step()  # 2 回目: 停止
    assert _phase(rig) == "C13"
    issue = rig.world.issue_for("CI_FAILED")
    rig.world.merge(pr.number)  # 直されて merge された（自動で解ける）
    rig.step()
    assert issue.closed
    assert _phase(rig) == "C4"


def test_a_second_run_waits_for_the_first_start_line_to_be_merged(tmp_path: Path) -> None:
    """2 本目の起動の前に、1 本目の開始の行の実行記録の PR を merge して早送りする（D25 §4.4）。"""
    rig = make_rig(tmp_path)
    for title, symbol in (("first-card", "GBPUSD"), ("second-card", "AUDUSD")):
        data = card_data(title=title, symbol=symbol)
        rig.world.put_on_main({f"research/hypotheses/queue/{data['id']}.yaml": card_bytes(data)})
        rig.clock.advance(minutes=1)
    rig.step()
    first, second = "h-20261120-first-card", "h-20261120-second-card"
    _until(rig, "C4", card=first)
    # 1 本目は並列で枠を 2 つ使うので、2 枚目は取り出さない。
    rig.step()
    assert not (rig.paths.state_dir / "cards" / f"{second}.json").exists()
    # 1 本目を順番（枠 1 つ）の run として扱うため、状態を直す。
    state_path = rig.paths.state_dir / "cards" / f"{first}.json"
    text = state_path.read_text(encoding="utf-8").replace('"slots": 2', '"slots": 1')
    state_path.write_text(text, encoding="utf-8")
    rig.wrapper.append_lines(1)  # 1 本目の開始の行（未コミット）
    rig.system.memory = 4 * 1024**3
    _until(rig, "C3", card=second)
    rig.step()
    assert len(rig.wrapper.launched) == 1  # 空きメモリが 8 GiB 未満なので待つ
    assert not [pr for pr in rig.world.routine() if pr.branch == "runner/exec"]
    rig.system.memory = 12 * 1024**3
    rig.step()  # 開始の行のコミットを予約する
    rig.step()  # 開始の行だけの実行記録の PR を出す（起動はまだ）
    start = [pr for pr in rig.world.routine() if pr.branch == "runner/exec"]
    assert start and start[0].title == f"[runner] 実行記録 {first} v1"
    _assert_ci_passed(rig)
    assert len(rig.wrapper.launched) == 1
    _until(rig, "C4", card=second)  # merge → 早送り → 2 本目を順番（枠 1 つ）で起動
    assert rig.wrapper.launched[1]["mode"] == "sequential"
    assert rig.card(second)["run"]["slots"] == 1


def test_a_reproduction_mismatch_during_a_run_stops_after_the_run(rig: Rig) -> None:
    rig.workers.add("analyst_change", _analysis(FIRST))
    _launch(rig)
    _run_version(rig, 1)
    _until(rig, "C2")  # 分析 → 版 2 の登録 PR、再現を起動
    _until(rig, "C4")  # 版 2 の起動（再現と並んで枠 1 つずつ）
    rig.procs.finish("reproduce", 6)
    rig.step()  # T16: run は続け、理由の集合に足す
    assert _phase(rig) == "C4"
    assert rig.world.issue_for("REPRO_MISMATCH")
    rig.wrapper.finish(1, 0)
    rig.step()  # run の終わりの後に記録の PR を作ってから C13
    assert _phase(rig) == "C13"
    assert rig.card(CARD_ID)["return_phase"] == "C6"


def test_the_weekly_summary_and_a_pause_request(rig: Rig) -> None:
    rig.step()
    summary = [issue for issue in rig.world.issues.values() if issue.label == "runner-weekly"]
    assert len(summary) == 1
    body = summary[0].comments[0].body
    assert body.startswith("週: 2026-11-16〜2026-11-22\n")
    assert "カード: 取り出した" in body and "ディスク: 空き" in body
    rig.step()
    assert len(summary[0].comments) == 1  # 週に 1 回
    rig.world.answer(summary[0].number, "止めて")
    rig.step()
    assert rig.state()["reasons"][0]["reason"] == "HUMAN_PAUSE"
    issue = rig.world.issue_for("HUMAN_PAUSE")
    rig.world.answer(issue.number, "2")
    rig.step()
    assert rig.state()["reasons"] == []


def test_low_disk_stops_everything_after_trying_to_delete(rig: Rig) -> None:
    rig.step()
    rig.system.disk = 10 * 1000**3
    rig.step()
    assert _phase(rig) == "C3"
    assert rig.world.issue_for("DISK_LOW")
    rig.system.disk = 30 * 1000**3
    rig.step()
    assert rig.state()["reasons"] == []
    assert _phase(rig) == "C4"


def test_the_ideator_fills_an_empty_queue_or_stops(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    ai = card_data(mouth="ai", title="ai-dual-ema", symbol="AUDUSD")
    from tools.ops.research_runner.ports import IdeatorOutput

    rig.workers.add("ideator", IdeatorOutput((card_bytes(ai),), ("ledger/trial_ledger.jsonl",)))
    rig.step()  # 待ち行列が空 → 発想役を 1 回だけ起動
    assert [request.role for request in rig.workers.launched] == ["ideator"]
    rig.step()  # 出力のカードを機械の検査に当て、AI の口の定型 PR で待ち行列に入れる
    cards = [pr for pr in rig.world.routine() if pr.branch.startswith("runner/cards-")]
    assert len(cards) == 1
    _assert_ci_passed(rig)
    rig.step()  # merge → 取り出す
    assert rig.card(ai["id"])["phase"] in {"C1", "C2", "C3", "C4"}


def test_the_ideator_writing_nothing_on_an_empty_queue_stops(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    from tools.ops.research_runner.ports import IdeatorOutput

    rig.workers.add("ideator", IdeatorOutput((), ()))
    rig.step()
    rig.step()
    assert rig.world.issue_for("QUEUE_EMPTY")
    rig.world.put_on_main({QUEUE: card_bytes(card_data())})
    rig.step()  # 自動: 待ち行列にカードが入れば外す
    assert rig.state()["reasons"] == []


def test_a_busy_step_does_nothing(rig: Rig) -> None:
    rig.paths.lock_path.parent.mkdir(parents=True)
    rig.paths.lock_path.write_text('{"pid": 1, "created_at": "2026-11-24T01:00:00Z"}')
    rig.procs.alive_pids.add(1)
    assert Runner.step(rig.runner()).ran is False
    assert not rig.world.prs


def test_worker_usage_limit_stops_everything(rig: Rig) -> None:
    rig.workers.add("analyst_change", status=WorkerStatus.USAGE_LIMIT)
    _launch(rig)
    _run_version(rig, 1)
    _until(rig, "C7")
    rig.step()
    issue = rig.world.issue_for("USAGE_LIMIT")
    rig.workers.add("analyst_change", _analysis(FIRST))
    rig.world.answer(issue.number, "1")
    _until(rig, "C2")
    assert rig.card(CARD_ID)["version"] == 2


def test_an_ambiguous_change_stops_the_card_and_another_change_resumes(rig: Rig) -> None:
    rig.workers.add("analyst_change", ChangeOutput("AMBIGUOUS", b"", "m.md", MEMO, "NO_RULE"))
    rig.workers.add("analyst_change", _analysis(FIRST))
    _launch(rig)
    _run_version(rig, 1)
    _until(rig, "C13")
    issue = rig.world.issue_for("AMBIGUOUS_RULE")
    rig.world.answer(issue.number, "2")  # 範囲を変えずに別の変更（T40）
    _until(rig, "C2")
    assert rig.card(CARD_ID)["version"] == 2
    assert rig.workers.launched[-1].extra.get("note")
