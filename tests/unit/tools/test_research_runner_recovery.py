"""実行器の回復の点検表（PR #193 本文）の穴の再発防止（Codex 4 巡目の指摘 2 件と同じ型）。

外部のもの（PR・issue・コミット・切り離した処理・ロック・予約）ごとに、(b) 操作の直後・状態の
保存の前に止まったら次の 1 歩で回復するか、(c) 外で閉じられた・消えた・失敗したら回復するか
止まりの issue を出すか、を人工の世界で確かめる。成績の数値は扱わない（台帳の行・記録は人工）。

「実行器が止まった」は、それ以後の状態の保存がすべて行われない形で真似る（`_kill`。停電・強制終了
では `finally` の保存も走らないため）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.hypothesis_cards import card_bytes, card_data
from tests.fixtures.research_runner import (
    FINAL,
    REVIEW,
    FakeGit,
    FakeGitHub,
    Rig,
    make_rig,
)
from tests.fixtures.runner_ci import final_ledger, review_ledger
from tools.ops.research_runner.ports import PrState, WorkerStatus
from tools.ops.research_runner.state import CardState, GlobalState, StateStore, format_time

CARD_ID = "h-20261120-dual-ema"
EXPERIMENT = "c_h20261120_dual_ema_gbpusd"
QUEUE = f"research/hypotheses/queue/{CARD_ID}.yaml"
RAW = card_bytes(card_data())


class _Crash(BaseException):
    """実行器のプロセスが途中で止まった（`ToolFailure` ではない）。"""


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    made = make_rig(tmp_path)
    made.world.put_on_main({QUEUE: RAW})
    return made


@contextmanager
def _kill(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], None]]:
    """呼ぶと「実行器が止まった」になる関数を渡す。止まった後の状態の保存はすべて捨てる。"""
    killed = {"on": False}
    save_card, save_global = StateStore.save_card, StateStore.save_global

    def card(self: StateStore, value: CardState) -> None:
        if not killed["on"]:
            save_card(self, value)

    def whole(self: StateStore, value: GlobalState) -> None:
        if not killed["on"]:
            save_global(self, value)

    def kill() -> None:
        killed["on"] = True
        raise _Crash

    monkeypatch.setattr(StateStore, "save_card", card)
    monkeypatch.setattr(StateStore, "save_global", whole)
    yield kill
    monkeypatch.undo()


def _phase(rig: Rig) -> str:
    return str(rig.card(CARD_ID)["phase"])


def _reasons(rig: Rig) -> set[str]:
    return {item["reason"] for item in rig.card(CARD_ID)["reasons"]}


def _global_reasons(rig: Rig) -> set[str]:
    return {item["reason"] for item in rig.state()["reasons"]}


def _launch(rig: Rig) -> None:
    rig.step()
    rig.step()
    assert _phase(rig) == "C4"


def _exec_prs(rig: Rig) -> list[Any]:
    return [pr for pr in rig.world.routine() if pr.branch == "runner/exec"]


def _until(rig: Rig, phase: str, *, limit: int = 6) -> None:
    for _ in range(limit):
        if _phase(rig) == phase:
            return
        rig.step()
    assert _phase(rig) == phase


def _inject(rig: Rig, phase: str, **fields: Any) -> None:
    """カードを done/ に置いた main と、`phase` の状態のファイルを作る（版 2・再現は一致）。"""
    rig.world.put_on_main({f"research/hypotheses/done/{CARD_ID}.yaml": RAW, QUEUE: None})
    store = StateStore(rig.paths.state_dir)
    card = CardState(
        card_id=CARD_ID,
        mouth="human",
        phase=phase,
        taken_at=format_time(rig.clock.now()),
        symbol="GBPUSD",
        experiment=EXPERIMENT,
        version=2,
        executions={"2": {"experiment_id": "a" * 64, "execution": 1}},
        repro="MATCH",
    )
    for key, value in fields.items():
        setattr(card, key, value)
    store.save_card(card)
    store.save_global(GlobalState(boot_id="boot-1", cards=[CARD_ID], weekly_last_week="2026-11-16"))


# --- 実行記録の PR（Codex 4 巡目の指摘 1・2） ----------------------------------------------


def test_a_run_record_request_survives_a_stop_before_the_global_state_is_saved(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run の終わりでカードを C6 として保存した後、全体の状態を保存する前に実行器が止まっても、
    次の 1 歩はカードに残った要求から実行記録の PR を作る（Codex 4 巡目の指摘 2）。"""
    _launch(rig)
    with _kill(monkeypatch) as kill:

        def stop_before_global(self: StateStore, value: GlobalState) -> None:
            kill()

        monkeypatch.setattr(StateStore, "save_global", stop_before_global)
        rig.wrapper.finish(0, 0, lines=2)
        with pytest.raises(_Crash):
            rig.step()
    assert _phase(rig) == "C6"
    assert rig.state()["exec_queue"] == []  # 全体の状態には要求が残らなかった
    _until(rig, "C7")
    prs = _exec_prs(rig)
    assert len(prs) == 1 and prs[0].ci_ok, prs[0].ci_messages
    assert prs[0].state is PrState.MERGED


def _closed_exec_pr(rig: Rig) -> Any:
    _launch(rig)
    rig.wrapper.finish(0, 0, lines=2)
    rig.step()  # 実行記録の PR を作る（同じ 1 歩の中では CI の途中）
    (pull,) = _exec_prs(rig)
    rig.github.close_pr(pull.number)  # merge されずに閉じられた
    rig.step()
    return pull


def test_a_closed_run_record_pr_stops_and_resumes_when_reopened_and_merged(rig: Rig) -> None:
    """実行記録の PR が merge されずに閉じられたら、止まりの issue で問う。開き直されて merge
    されれば自動で外れて続ける（Codex 4 巡目の指摘 1）。"""
    pull = _closed_exec_pr(rig)
    assert _phase(rig) == "C13" and _reasons(rig) == {"CI_FAILED"}
    issue = rig.world.issue_for("CI_FAILED")
    pull.state = PrState.OPEN  # 人間が開き直して merge した
    rig.world.merge(pull.number)
    rig.step()
    assert issue.closed
    assert _phase(rig) == "C7"
    assert rig.state()["exec_pr"] is None


def test_ending_a_card_after_its_run_record_pr_was_closed_sends_the_rows_again(
    rig: Rig,
) -> None:
    """閉じられた実行記録の PR に「このカードを終える」と答えると、カードは終わり、台帳の行は
    同じコミットのまま新しい PR で送り直す（行を消さず、後の起動と実行記録を止めない）。"""
    pull = _closed_exec_pr(rig)
    rig.world.answer(rig.world.issue_for("CI_FAILED").number, "2")
    for _ in range(3):
        rig.step()
    assert _phase(rig) == "C14"
    prs = _exec_prs(rig)
    assert len(prs) == 2 and prs[1].commit == pull.commit
    assert prs[1].state is PrState.MERGED and prs[1].ci_ok, prs[1].ci_messages
    assert rig.state()["exec_pr"] is None and rig.state()["exec_sending"] is None
    assert FakeGit(rig.world).is_ancestor(rig.world.exec_head, rig.world.main)


def test_a_run_record_that_cannot_be_made_does_not_block_the_queue(rig: Rig) -> None:
    """実行記録を作れない（包みのファイルが欠けた）ときは、要求を待ちから外して問う。答え
    「確かめからやり直す」で同じ要求を出し直す（後ろの要求を止めない）。"""
    _launch(rig)
    rig.wrapper.finish(0, 0, lines=2)
    finished = rig.world.repo / "runs/experiments" / EXPERIMENT / "v1" / "finished_at_utc.txt"
    saved = finished.read_bytes()
    finished.unlink()
    rig.step()
    assert "LEDGER_CHECK_FAILED" in _reasons(rig)
    assert rig.state()["exec_queue"] == []
    assert rig.card(CARD_ID)["checks"]["failed_exec"] == {"purpose": "run", "version": 1}
    finished.write_bytes(saved)
    rig.world.answer(rig.world.issue_for("LEDGER_CHECK_FAILED").number, "2")
    _until(rig, "C7")


def test_a_review_record_merged_while_the_card_was_stopped_is_followed_after_resuming(
    rig: Rig,
) -> None:
    """審査の行の実行記録の PR が停止中（C13）に入っても、外れて C10 に戻った後に審査の結果に
    従う（入ったことはカードの要求が無いことで分かる）。"""
    rig.world.branches["runner/report-x"] = rig.world.main
    report = rig.world.open_pr("runner/report-x", "[runner] 報告", "本文")
    _inject(
        rig,
        "C10",
        reservations=["review"],
        pr={"kind": "report", "version": 2, "number": report},
    )
    rig.step()  # 審査を起動
    (rig.world.exec_dir / REVIEW).write_bytes(review_ledger(1))
    rig.procs.finish("review", 0)
    rig.step()  # 審査の結果 → 審査の行の実行記録の PR
    rig.github.close_pr(report)  # 一巡の報告の PR が閉じられた → 停止
    rig.step()  # 停止中に審査の行の PR が入る
    assert _phase(rig) == "C13"
    assert rig.state()["exec_pr"] is None
    rig.world.prs[report].state = PrState.OPEN  # 開き直されて merge された
    rig.world.merge(report)
    rig.step()
    assert _phase(rig) == "C11"


# --- 予約（空きを待つ処理） -----------------------------------------------------------------


def test_the_final_reservation_comes_back_after_the_declaration_stop_in_c12(rig: Rig) -> None:
    """最終検証の起動の前に申告の撤去の前と分かって止まり、撤去が入って自動で外れたら、外した
    最終検証の予約を戻して起動する（C12 で待ち続けない）。"""
    rig.evaluation.declaration = True
    _inject(rig, "C12", reservations=["final"])
    rig.step()
    assert _reasons(rig) == {"REVIEW_NEEDS_DECLARATION"}
    assert not rig.procs.spawned
    rig.evaluation.declaration = False
    rig.step()
    assert _phase(rig) == "C12"
    assert rig.procs.by_kind("final-validate")


def test_a_vanished_reproduction_is_relaunched_not_counted_as_a_mismatch(rig: Rig) -> None:
    """再現の処理が終了コードを書かずに消えた（再起動など）: 不一致とは数えず起動し直す。"""
    from tests.unit.tools.test_research_runner_flow import FIRST, _analysis

    rig.workers.add("analyst_change", _analysis(FIRST))
    _launch(rig)
    rig.wrapper.finish(0, 0, lines=2)
    for _ in range(4):
        rig.step()
        if [key for key in rig.procs.spawned if key.startswith("reproduce:")]:
            break
    first = rig.procs.by_kind("reproduce")
    rig.procs.alive_pids.discard(int(first["pid"]))
    rig.step(minutes=15)
    rig.step()
    assert not [issue for issue in rig.world.open_issues() if "REPRO_MISMATCH" in issue.title]
    assert rig.card(CARD_ID)["repro"] is None
    assert len([key for key in rig.procs.spawned if key.startswith("reproduce:")]) == 2


# --- 最終検証の消費の後 -----------------------------------------------------------------------


def test_ending_a_card_is_refused_after_the_final_validation_consumed(rig: Rig) -> None:
    """最終検証が消費した後、記録の PR が閉じられても「このカードを終える」は受けない（消費の
    記録を失わない）。開き直されて merge されれば自動で外れて終わる。"""
    _inject(rig, "C12", reservations=["final"])
    rig.step()
    (rig.world.repo / FINAL).write_bytes(final_ledger(rig.world.repo / "tmp", 1))
    rig.procs.finish("final-validate", 0)
    rig.step()  # 記録の PR を作る
    pull = next(pr for pr in rig.world.prs.values() if pr.branch.startswith("runner/final-"))
    rig.github.close_pr(pull.number)
    rig.step()
    assert _reasons(rig) == {"CI_FAILED"}
    issue = rig.world.issue_for("CI_FAILED")
    rig.world.answer(issue.number, "2")  # このカードを終える
    rig.step()
    assert _phase(rig) == "C13" and not issue.closed
    pull.state = PrState.OPEN
    rig.world.merge(pull.number)
    for _ in range(2):
        rig.step()
    assert _phase(rig) == "C14"
    assert rig.card(CARD_ID)["outcome"] == "FINAL_VALIDATED"


# --- issue（止まりの問い・週の要約） --------------------------------------------------------


def test_a_stop_issue_created_just_before_the_runner_stopped_is_not_left_twice(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """止まりの issue を作った直後（番号を状態に保存する前）に実行器が止まっても、次の 1 歩の後に
    開いた問いは 1 つだけ（状態に無い issue は閉じる）。"""
    _launch(rig)
    rig.wrapper.finish(0, 1)
    create = FakeGitHub.create_issue
    with _kill(monkeypatch) as kill:

        def create_then_stop(self: FakeGitHub, **kw: Any) -> int:
            number = create(self, **kw)
            if "RUN_FAILED" in kw["title"]:
                kill()
            return number

        monkeypatch.setattr(FakeGitHub, "create_issue", create_then_stop)
        with pytest.raises(_Crash):
            rig.step()
    rig.step()
    assert rig.world.issue_for("RUN_FAILED")  # 開いた問いは 1 つ
    assert _reasons(rig) == {"RUN_FAILED"}


def test_the_weekly_comment_is_not_written_twice_after_a_stop(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """週の要約を書いた直後（週を保存する前）に実行器が止まっても、同じ週を二度書かない。"""
    rig.step()  # 1 週目の要約（issue も作る）
    rig.clock.advance(days=7)
    comment = FakeGitHub.comment
    with _kill(monkeypatch) as kill:

        def comment_then_stop(self: FakeGitHub, number: int, body: str) -> None:
            comment(self, number, body)
            if body.startswith("週: 2026-11-23"):
                kill()

        monkeypatch.setattr(FakeGitHub, "comment", comment_then_stop)
        with pytest.raises(_Crash):
            rig.step()
    rig.step()
    (weekly,) = [issue for issue in rig.world.issues.values() if issue.label == "runner-weekly"]
    headers = [item.body.splitlines()[0] for item in weekly.comments]
    assert headers.count("週: 2026-11-23〜2026-11-29") == 1


# --- 台帳のロック・残った台帳の行 -----------------------------------------------------------


def test_a_ledger_lock_left_without_a_writer_stops_everything_and_clears(tmp_path: Path) -> None:
    """台帳のロックが、書いているはずの処理が居ないのに 15 分より長く残ったら全体の停止で問う
    （黙って待ち続けない）。ロックが消えれば自動で外れる。"""
    rig = make_rig(tmp_path)
    lock = rig.world.exec_dir / "research/trial_ledger.lock"
    lock.write_text("", encoding="utf-8")
    rig.step()
    assert "LEDGER_CHECK_FAILED" not in _global_reasons(rig)
    rig.step(minutes=16)
    assert "LEDGER_CHECK_FAILED" in _global_reasons(rig)
    issue = rig.world.issue_for("LEDGER_CHECK_FAILED")
    lock.unlink()
    rig.step()
    assert "LEDGER_CHECK_FAILED" not in _global_reasons(rig)
    assert issue.closed


def test_leftover_ledger_rows_are_sent_after_the_card_was_ended(rig: Rig) -> None:
    """台帳の確かめに落ちたカードを「このカードを終える」で終えた後も、毎回の 1 歩で確かめ直し、
    通れば残った台帳の行を実行記録の PR で送り、全体の理由を外す（D25 §4.7 の表の 3）。"""
    _launch(rig)
    rig.evaluation.ledger_code = 1
    rig.wrapper.finish(0, 0, lines=2)
    rig.step()
    assert "LEDGER_CHECK_FAILED" in _reasons(rig)
    rig.world.answer(rig.world.issue_for("LEDGER_CHECK_FAILED").number, "3")
    rig.step()
    assert _phase(rig) == "C14"
    assert "LEDGER_CHECK_FAILED" in _global_reasons(rig)  # 確かめが通るまで残る
    rig.evaluation.ledger_code = 0
    for _ in range(3):
        rig.step()
    assert "LEDGER_CHECK_FAILED" not in _global_reasons(rig)
    prs = _exec_prs(rig)
    assert prs and prs[-1].state is PrState.MERGED and prs[-1].ci_ok, prs[-1].ci_messages


# --- 包みとの約束（main 4c947a8 の実物） ----------------------------------------------------


def test_the_wrapper_gets_the_same_snapshots_as_the_other_commands(rig: Rig) -> None:
    """包みにも審査・再現・最終検証と同じ snapshot の基点を `--snapshots` で渡す（包みの既定の
    `<repo>/data/snapshots` に頼らない）。"""
    _launch(rig)
    assert rig.wrapper.launched[0]["snapshots"] == rig.paths.snapshots


# --- Codex 5 巡目 ----------------------------------------------------------------------------


def _consumption_row() -> bytes:
    """人工の消費の行（D09 §9.10.4 の版 1 の形。識別子だけ。区分・値は人工）。"""
    import json

    row = {
        "schema_version": "1",
        "row_id": "0" * 32,
        "kind": "CONSUMED",
        "partition": "artificial",
        "reason": "FINAL_EVALUATION",
        "permit_id": "1" * 32,
        "candidate": {"experiment_id": "a" * 64, "execution": "1"},
        "access_id": "",
        "recorded_at": "2026-11-24T00:00:00Z",
        "actor": "research_runner",
        "purpose": "",
    }
    return (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_a_final_validation_that_consumed_before_failing_is_not_restarted(rig: Rig) -> None:
    """最終検証が消費の行を origin に送った後、結果を書く前に終わった（台帳の行は無い）: 消費の
    記録で消費済みと見て、状態 A として起動し直さない。答え「状態 A なので起動し直す」も受けない
    （Codex 5 巡目の指摘 1）。"""
    _inject(rig, "C12", reservations=["final"])
    rig.step()
    rig.world.put_on_main({"data/snapshots/s-artificial/access_log.jsonl": _consumption_row()})
    rig.procs.finish("final-validate", 8)
    rig.step()
    assert _reasons(rig) == {"FINAL_VALIDATION_FAILED"}
    rig.world.answer(rig.world.issue_for("FINAL_VALIDATION_FAILED").number, "3")
    rig.step()
    assert _reasons(rig) == {"FINAL_VALIDATION_FAILED"}
    assert len([key for key in rig.procs.spawned if key.startswith("final-validate:")]) == 1


def test_a_binding_repaired_after_the_stop_is_read_again(rig: Rig) -> None:
    """run の終わりに束縛の記録が読めずに止まり、人間が直して「確かめからやり直す」と答えたら、
    束縛を読み直して実行記録を作る（Codex 5 巡目の指摘 2）。"""
    _launch(rig)
    key = f"{EXPERIMENT}/v1"
    rig.evaluation.bindings[key] = None  # type: ignore[assignment]
    rig.wrapper.finish(0, 0, lines=2)
    rig.step()
    assert "LEDGER_CHECK_FAILED" in _reasons(rig)
    del rig.evaluation.bindings[key]
    rig.world.answer(rig.world.issue_for("LEDGER_CHECK_FAILED").number, "2")
    _until(rig, "C7")
    prs = _exec_prs(rig)
    assert prs and prs[-1].ci_ok, prs[-1].ci_messages


# --- Codex 6 巡目 ----------------------------------------------------------------------------


def test_a_review_recorded_before_its_exit_code_is_not_run_again(rig: Rig) -> None:
    """審査が台帳に追記した後、終了コードを書く前に消えても、審査台帳の行（`entry` の中の識別子
    と結果）から結果を読み、審査を起動し直さない（Codex 6 巡目の指摘 1）。"""
    import json

    ledger = review_ledger(1)
    entry = json.loads(ledger.splitlines()[0])["entry"]
    executions = {"2": {"experiment_id": entry["experiment_id"], "execution": entry["execution"]}}
    _inject(rig, "C10", reservations=["review"], executions=executions)
    rig.step()  # 審査を起動
    review = rig.procs.by_kind("review")
    (rig.world.exec_dir / REVIEW).write_bytes(ledger)
    rig.procs.alive_pids.discard(int(review["pid"]))  # 終了コードを書く前に消えた
    for _ in range(3):
        rig.step()
    assert len([key for key in rig.procs.spawned if key.startswith("review:")]) == 1
    assert _reasons(rig) == {"REVIEW_DEFERRED"}  # 台帳の結果（人工の行は「延期」）に従う


def test_fixation_records_of_two_cards_are_not_mixed_up(rig: Rig) -> None:
    """別のカードの固定の記録が同じ実行用の作業木に並んでも、この実行の置き場の記録だけを
    取り出して関所 1 の PR を作る（Codex 6 巡目の指摘 2）。"""
    _inject(rig, "C11")
    rig.step()
    root = rig.world.exec_dir / "research/candidate_fixes"
    mine = root / ("a" * 64) / "1" / "1.yaml"
    other = root / ("b" * 64) / "1" / "1.yaml"
    for record in (mine, other):
        record.parent.mkdir(parents=True)
        record.write_text("schema_version: 1\n", encoding="utf-8")
    rig.procs.finish("fix", 0)
    rig.step()
    assert "FIXATION_CHECK_FAILED" not in _reasons(rig)
    fix = [pr for pr in rig.world.prs.values() if pr.branch.startswith("candidate-fix/")]
    assert len(fix) == 1
    assert other.is_file() and not mine.exists()  # 別のカードの記録は残す


# --- Codex 7 巡目 ----------------------------------------------------------------------------


def _deferred_then(result: Any) -> tuple[bytes, bytes, dict[str, Any]]:
    """同じ実行の「保留」の 1 行だけの台帳と、その後にやり直しの審査の行を足した台帳。"""
    from odyssey_fx.evaluation.adapters.review_store import review_line_bytes
    from odyssey_fx.evaluation.domain.adoption_review import ReviewResult
    from tests.fixtures.evaluation.adoption_review import review_entry, review_lines
    from tests.fixtures.evaluation.trial_ledger import experiment_id

    eid = experiment_id("again")
    first = review_entry(eid, 1, result=ReviewResult.DEFERRED, nonce="1" * 32)
    later = review_entry(eid, 1, result=result, nonce="2" * 32)
    before = b"".join(review_line_bytes(line) for line in review_lines([first]))
    after = b"".join(review_line_bytes(line) for line in review_lines([first, later]))
    assert after.startswith(before)
    return before, after, {"2": {"experiment_id": str(eid), "execution": 1}}


def test_the_latest_review_row_is_read_after_a_deferral() -> None:
    """同じ実行に「保留」の行があり、やり直しの審査が「進める」を足したら、最新の行を読む
    （Codex 7 巡目の指摘 1。直す前は先頭の「保留」を返した）。"""
    from odyssey_fx.evaluation.domain.adoption_review import ReviewResult
    from tools.ops.research_runner.ledger_rows import review_result, review_row_count

    before, after, executions = _deferred_then(ReviewResult.PROCEED)
    eid = executions["2"]["experiment_id"]
    assert review_result(after, eid, 1) == "PROCEED"
    assert review_row_count(before, eid, 1) == 1
    assert review_result(after, eid, 1, after=1) == "PROCEED"
    assert review_result(before, eid, 1, after=1) is None  # 起動の後に何も書かれていない


def _re_review(rig: Rig, before: bytes, executions: dict[str, Any]) -> dict[str, Any]:
    """「保留」の行が台帳にある C10 のカードで、やり直しの審査を起動する。"""
    _inject(rig, "C10", reservations=["review"], executions=executions, review="DEFERRED")
    rig.world.put_on_main({REVIEW: before})  # 前の審査の「保留」の行は main に入っている
    rig.step()  # 実行用の作業木を早送りし、やり直しの審査を起動
    review = rig.procs.by_kind("review")
    assert rig.card(CARD_ID)["procs"]["review"]["prior_rows"] == 1
    return review


def test_a_re_review_that_proceeded_before_its_exit_code_follows_the_latest_row(
    rig: Rig,
) -> None:
    """「保留」の後のやり直しの審査が「進める」を書き、終了コードを書く前に消えても、古い
    「保留」で止まらず、最新の「進める」に従って関所 1 へ進む（Codex 7 巡目の指摘 1）。"""
    from odyssey_fx.evaluation.domain.adoption_review import ReviewResult

    before, after, executions = _deferred_then(ReviewResult.PROCEED)
    review = _re_review(rig, before, executions)
    (rig.world.exec_dir / REVIEW).write_bytes(after)
    rig.procs.alive_pids.discard(int(review["pid"]))  # 終了コードを書く前に消えた
    _until(rig, "C11")
    assert "REVIEW_DEFERRED" not in _reasons(rig)
    assert len([key for key in rig.procs.spawned if key.startswith("review:")]) == 1


def test_a_re_review_that_vanished_without_writing_is_run_again(rig: Rig) -> None:
    """やり直しの審査が何も書かずに消えたら、古い「保留」の行を結果と読まず、審査を 1 回だけ
    起動し直す（同じ型の点検で見つけた穴。直す前は古い「保留」で止まった）。"""
    from odyssey_fx.evaluation.domain.adoption_review import ReviewResult

    before, _, executions = _deferred_then(ReviewResult.PROCEED)
    review = _re_review(rig, before, executions)
    rig.procs.alive_pids.discard(int(review["pid"]))
    rig.step()
    rig.step()
    assert "REVIEW_DEFERRED" not in _reasons(rig)
    assert len([key for key in rig.procs.spawned if key.startswith("review:")]) == 2


@pytest.mark.parametrize("trigger", ["queue_empty", "after_done", "extra"])
def test_an_ideator_stopped_by_the_usage_limit_is_launched_again_after_recovery(
    tmp_path: Path, trigger: str
) -> None:
    """発想役が利用上限で終わったら、起動のきっかけを戻す。停止に「回復を待って続ける」と答えた
    後に発想役をもう一度起動し、それでも書けなければ待ち行列が空の停止で問う（Codex 7 巡目の
    指摘 2。直す前はきっかけを使い切ったまま外し、何も進まなかった）。"""
    from tools.ops.research_runner.ports import IdeatorOutput, WorkerStatus

    rig = make_rig(tmp_path)  # 待ち行列は空
    if trigger != "queue_empty":
        store = StateStore(rig.paths.state_dir)
        store.save_global(
            GlobalState(
                boot_id="boot-1",
                weekly_last_week="2026-11-16",
                ideator={trigger: 1, "queue_empty_used": True},
            )
        )
    rig.workers.add("ideator", status=WorkerStatus.USAGE_LIMIT)
    rig.step()  # 発想役を起動
    rig.step()  # 利用上限で終わる → 全体の停止
    issue = rig.world.issue_for("USAGE_LIMIT")
    assert rig.state()["ideator"].get("running") is None
    rig.workers.add("ideator", IdeatorOutput((), ()))
    rig.world.answer(issue.number, "1")  # 回復を待って続ける
    for _ in range(3):
        rig.step()
    assert [request.role for request in rig.workers.launched] == ["ideator", "ideator"]
    if trigger != "after_done":  # `done/` の後の 1 回は、書けなくても問わない（D25 §3.7）
        assert rig.world.issue_for("QUEUE_EMPTY")


def test_an_ideator_that_could_not_be_launched_keeps_its_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ型の点検: 発想役を起動できなかった（定義が無いなど）ときも、待ち行列が空のときの
    1 回を使い切らず、次の 1 歩でもう一度起動する。"""
    from tests.fixtures.research_runner import FakeWorkers
    from tools.ops.research_runner.ports import IdeatorOutput, WorkerRequest, WorkerUnavailable

    rig = make_rig(tmp_path)
    original = FakeWorkers.launch
    calls = {"launch": 0}

    def unavailable_once(self: FakeWorkers, request: WorkerRequest) -> None:
        calls["launch"] += 1
        if calls["launch"] == 1:
            raise WorkerUnavailable("試験")
        original(self, request)

    monkeypatch.setattr(FakeWorkers, "launch", unavailable_once)
    rig.step()
    assert "queue_empty_used" not in rig.state()["ideator"]
    rig.workers.add("ideator", IdeatorOutput((), ()))
    rig.step()
    rig.step()
    assert calls["launch"] == 2
    assert rig.world.issue_for("QUEUE_EMPTY")


@pytest.mark.parametrize("stage", ["C8", "C9"])
def test_card_workers_stopped_by_the_usage_limit_are_launched_again(rig: Rig, stage: str) -> None:
    """同じ型の点検: カードの作業役（報告を書く分析役・確かめ役）は、利用上限の停止の答えの後に
    段がもう一度当たって起動し直す（きっかけを失わない）。"""
    role = {"C8": "analyst_report", "C9": "verifier"}[stage]
    checks = {"gate": [[item, True] for item in range(1, 6)]} if stage == "C9" else {}
    _inject(rig, stage, checks=checks)
    rig.workers.add(role, status=WorkerStatus.USAGE_LIMIT)
    rig.step()
    rig.step()
    issue = rig.world.issue_for("USAGE_LIMIT")
    rig.workers.hold.add(role)
    rig.world.answer(issue.number, "1")
    rig.step()
    roles = [request.role for request in rig.workers.launched if request.role == role]
    assert roles == [role, role]
    assert _phase(rig) == stage


# --- 設計適合のレビュー: 自動の削除の範囲と一覧（D25 §4.5。取り消せない操作） -----------------


def _hex(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode()).hexdigest()


def _put(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _deletion_world(rig: Rig, last_verdict: Any) -> dict[str, Any]:
    """確かめを通ったカード A（版 2）と、確かめを通っていないカード B の世界を作る。A の最後の版の
    試行台帳の結末の行の判定は `last_verdict`（人工の値）。"""
    from odyssey_fx.evaluation.adapters.fs_store import ledger_line_bytes
    from odyssey_fx.evaluation.domain.search import SearchVerdict
    from tests.fixtures.evaluation.trial_ledger import chain, finished, started

    one = started("del1", nonce="1" * 32)
    two = started("del2", nonce="2" * 32)
    rows = [
        one,
        finished(one, verdict=SearchVerdict.BELOW_STANDARD),
        two,
        finished(two, verdict=last_verdict),
    ]
    ledger = b"".join(ledger_line_bytes(line) for line in chain(rows))
    rig.world.put_on_main({"research/trial_ledger.jsonl": ledger})
    rig.world.exec_head = rig.world.main
    (rig.world.exec_dir / "research/trial_ledger.jsonl").write_bytes(ledger)
    executions = {
        "1": {"experiment_id": str(one.experiment_id), "execution": one.execution},
        "2": {"experiment_id": str(two.experiment_id), "execution": two.execution},
    }
    _inject(
        rig,
        "C10",
        reservations=["review"],
        executions=executions,
        checks={"gate_passed": True},
    )
    store = StateStore(rig.paths.state_dir)
    other = CardState(
        card_id="h-20261120-other",
        mouth="human",
        phase="C7",
        taken_at=format_time(rig.clock.now()),
        experiment="c_other",
        version=1,
    )
    store.save_card(other)
    state = GlobalState(
        boot_id="boot-1", cards=[CARD_ID, other.card_id], weekly_last_week="2026-11-16"
    )
    store.save_global(state)
    repo = rig.world.repo
    runs = {version: _hex(f"run-{version}") for version in ("a1", "a2", "b1")}
    version_dirs = {
        "a1": rig.paths.version_dir(EXPERIMENT, 1),
        "a2": rig.paths.version_dir(EXPERIMENT, 2),
        "b1": rig.paths.version_dir("c_other", 1),
    }
    by_dir = {str(path): (runs[key],) for key, path in version_dirs.items()}
    rig.evaluation.run_ids = lambda version_dir: by_dir.get(str(version_dir), ())  # type: ignore[method-assign]
    paths = {
        "run_a1": _put(repo / "runs" / runs["a1"] / "trades.parquet"),
        "run_a2": _put(repo / "runs" / runs["a2"] / "trades.parquet"),
        "run_b1": _put(repo / "runs" / runs["b1"] / "trades.parquet"),
        "repro_a1": _put(rig.paths.repro_out(EXPERIMENT, 1) / "out.json"),
        "repro_a2": _put(rig.paths.repro_out(EXPERIMENT, 2) / "out.json"),
        "record_a1": _put(version_dirs["a1"] / "record.json"),
        "search_a1": _put(version_dirs["a1"] / "search" / "trial_units.parquet"),
        "fix": _put(rig.world.exec_dir / "research/candidate_fixes" / ("a" * 64) / "1" / "1.yaml"),
        "runner": _put(rig.paths.runner_dir / "state" / "keep.txt"),
        "not_hex": _put(repo / "runs" / "not-a-run-id" / "trades.parquet"),
    }
    return paths


def _low_disk_step(rig: Rig) -> None:
    rig.system.disk = 10 * 1000**3  # 消しても足りない（全部を消そうとする）
    rig.step()


@pytest.mark.parametrize("verdict", ["BELOW_STANDARD", "INSUFFICIENT_EVIDENCE"])
def test_auto_deletion_removes_only_what_the_design_allows(rig: Rig, verdict: str) -> None:
    """消してよいもの（確かめを通ったカードの最後でない版と、判定が「満たす」でない最後の版の
    `runs/<run_id>/` と再現の出力）だけが消え、ほか（記録票・探索の記録・固定の記録・台帳・
    `runs/runner/`・確かめを通っていないカードの出力・64 桁の 16 進でない名前）は残る。"""
    from odyssey_fx.evaluation.domain.search import SearchVerdict

    paths = _deletion_world(rig, SearchVerdict[verdict])
    ledgers = {
        name: (rig.world.exec_dir / name).read_bytes() for name in ("research/trial_ledger.jsonl",)
    }
    _low_disk_step(rig)
    assert "DISK_LOW" in _global_reasons(rig)
    gone = {"run_a1", "run_a2", "repro_a1", "repro_a2"}
    for name, path in paths.items():
        assert path.exists() is (name not in gone), name
    for name, data in ledgers.items():
        assert (rig.world.exec_dir / name).read_bytes() == data


def test_auto_deletion_keeps_a_last_version_that_meets_the_standard(rig: Rig) -> None:
    """最後の版の判定が「満たす」なら、審査で「進めない」でも審査の入口を通りうるので残す（D25 §4.5
    の 2。直す前は審査の結果だけで消していた）。最後でない版は消す。"""
    from odyssey_fx.evaluation.domain.search import SearchVerdict

    paths = _deletion_world(rig, SearchVerdict.MEETS_STANDARD)
    card = StateStore(rig.paths.state_dir).load_card(CARD_ID)
    card.review = "NOT_ADVANCE"
    StateStore(rig.paths.state_dir).save_card(card)
    _low_disk_step(rig)
    assert not paths["run_a1"].exists() and not paths["repro_a1"].exists()
    assert paths["run_a2"].exists() and paths["repro_a2"].exists()


def test_auto_deletion_keeps_a_last_version_without_an_outcome_row(rig: Rig) -> None:
    """最後の版の結末の行が無い（判定が読めない）なら、残す側に倒す。"""
    from odyssey_fx.evaluation.domain.search import SearchVerdict

    paths = _deletion_world(rig, SearchVerdict.BELOW_STANDARD)
    card = StateStore(rig.paths.state_dir).load_card(CARD_ID)
    card.executions["2"] = {"experiment_id": "f" * 64, "execution": 1}
    StateStore(rig.paths.state_dir).save_card(card)
    _low_disk_step(rig)
    assert paths["run_a2"].exists() and paths["repro_a2"].exists()
    assert not paths["run_a1"].exists()


def test_the_deletion_list_is_written_before_each_removal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """消す前に一覧（パス・大きさ・SHA-256）を `runs/runner/deleted/` に書く（消した後、一覧を
    書く前に止まっても一覧を失わない）。"""
    import hashlib
    import json
    import shutil

    from odyssey_fx.evaluation.domain.search import SearchVerdict

    paths = _deletion_world(rig, SearchVerdict.BELOW_STANDARD)
    expected = {
        path.relative_to(rig.world.repo).as_posix(): path.read_bytes()
        for name, path in paths.items()
        if name in {"run_a1", "run_a2", "repro_a1", "repro_a2"}
    }
    original = shutil.rmtree
    seen: list[set[str]] = []

    def checked(target: Any, *args: Any, **kwargs: Any) -> None:
        listed = set()
        for listing in rig.paths.deleted_dir.glob("*.jsonl"):
            listed |= {json.loads(line)["path"] for line in listing.read_text().splitlines()}
        root = Path(target)
        if root.is_relative_to(rig.world.repo / "runs") and not root.is_relative_to(
            rig.paths.runner_dir
        ):
            inside = {
                item.relative_to(rig.world.repo).as_posix()
                for item in root.rglob("*")
                if item.is_file()
            }
            assert inside <= listed, inside - listed
            seen.append(inside)
        original(target, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", checked)
    _low_disk_step(rig)
    assert set().union(*seen) == set(expected)
    (listing,) = rig.paths.deleted_dir.glob("*.jsonl")
    rows = [json.loads(line) for line in listing.read_text().splitlines()]
    assert {row["path"] for row in rows} == set(expected)
    for row in rows:
        data = expected[row["path"]]
        assert row["size"] == len(data)
        assert row["sha256"] == hashlib.sha256(data).hexdigest()


def test_run_ids_that_are_not_64_hex_digits_are_never_listed(tmp_path: Path) -> None:
    """消す対象の `runs/<run_id>/` は、探索の記録の `run_id` が 64 桁の 16 進のものだけ（`..` や
    別の名前で `runs/` の外や別の置き場を消さない）。"""
    import polars as pl

    from tools.ops.research_runner.adapters import LocalEvaluation

    good = _hex("good")
    table = tmp_path / "v1" / "search" / "s1" / "trial_units.parquet"
    table.parent.mkdir(parents=True)
    pl.DataFrame({"run_id": [good, "..", "runner", good.upper(), good[:63], ""]}).write_parquet(
        table
    )
    assert LocalEvaluation(snapshots=tmp_path).run_ids(tmp_path / "v1") == (good,)


# --- 設計適合のレビュー: 作業役のセッションは同時に 1 つまで（D25 §4.4、仮置き 15） -----------


def _report() -> Any:
    from tools.ops.research_runner.ports import ReportOutput

    return ReportOutput("予想の文", ("理由の文",), ("中身の文",), "analysis/c.md", "ef" * 32)


def _verdict() -> Any:
    from tools.ops.research_runner.ports import VerdictOutput

    return VerdictOutput(True)


def _load_global(rig: Rig) -> GlobalState:
    whole = StateStore(rig.paths.state_dir).load_global()
    assert whole is not None
    return whole


def test_a_card_worker_waits_for_the_running_ideator_and_keeps_its_stage(tmp_path: Path) -> None:
    """発想役が走っている間、カードの分析役は起動しない。発想役が終われば、待っていた段がもう一度
    当たって起動する（待たされた側は何も失わない）。"""
    from tools.ops.research_runner.ports import IdeatorOutput

    rig = make_rig(tmp_path)
    StateStore(rig.paths.state_dir).save_global(
        GlobalState(
            boot_id="boot-1",
            weekly_last_week="2026-11-16",
            ideator={"after_done": 1, "queue_empty_used": True},
        )
    )
    rig.workers.hold.add("ideator")
    rig.step()  # done/ に移った後の 1 回 → 発想役を起動（走り続ける）
    assert [item.role for item in rig.workers.launched] == ["ideator"]
    ideator = dict(rig.state()["ideator"])
    _inject(rig, "C8")
    store = StateStore(rig.paths.state_dir)
    whole = _load_global(rig)
    whole.ideator = ideator  # 走っている発想役の起動の印を残す
    store.save_global(whole)
    for _ in range(2):
        rig.step()
    assert [item.role for item in rig.workers.launched] == ["ideator"]
    assert _phase(rig) == "C8"
    rig.workers.hold.discard("ideator")
    rig.workers.add("ideator", IdeatorOutput((), ()))
    rig.workers.hold.add("analyst_report")
    for _ in range(2):
        rig.step()
    assert [item.role for item in rig.workers.launched] == ["ideator", "analyst_report"]


def test_the_ideator_waits_for_a_card_worker_without_using_its_trigger(tmp_path: Path) -> None:
    """カードの作業役が走っている間、発想役は起動せず、待ち行列が空のときの 1 回を使い切らない。
    カードの作業役が終われば発想役が起動する。"""
    rig = make_rig(tmp_path)
    _inject(rig, "C8")
    rig.workers.hold.add("analyst_report")
    rig.workers.hold.add("ideator")
    rig.step()
    assert [item.role for item in rig.workers.launched] == ["analyst_report"]
    assert "queue_empty_used" not in rig.state()["ideator"]
    rig.step()
    assert [item.role for item in rig.workers.launched] == ["analyst_report"]
    rig.workers.hold.discard("analyst_report")
    rig.workers.add("analyst_report", _report())
    rig.workers.add("verifier", _verdict())
    for _ in range(4):
        rig.step()
    roles = [item.role for item in rig.workers.launched]
    assert roles == ["analyst_report", "verifier", "ideator"]


def test_two_cards_do_not_run_workers_at_the_same_time(rig: Rig) -> None:
    """カード同士でも作業役は同時に 1 つ。先のカードの作業役が終われば、後のカードが起動する。"""
    _inject(rig, "C8")
    store = StateStore(rig.paths.state_dir)
    other = CardState(
        card_id="h-20261120-other",
        mouth="human",
        phase="C8",
        taken_at=format_time(rig.clock.now()),
        experiment="c_other",
        version=2,
    )
    store.save_card(other)
    whole = _load_global(rig)
    whole.cards.append(other.card_id)
    store.save_global(whole)
    rig.workers.hold.add("analyst_report")
    for _ in range(2):
        rig.step()
    first = [(item.role, item.card_id) for item in rig.workers.launched]
    assert first == [("analyst_report", CARD_ID)]
    rig.workers.hold.discard("analyst_report")
    rig.workers.add("analyst_report", _report())
    rig.workers.add("verifier", _verdict())
    rig.workers.hold.add("verifier")
    for _ in range(3):
        rig.step()
    # 先のカードの確かめ役が走っている間、後のカードの分析役は待つ
    assert [(item.role, item.card_id) for item in rig.workers.launched] == [
        ("analyst_report", CARD_ID),
        ("verifier", CARD_ID),
    ]
    rig.workers.hold.discard("verifier")
    rig.workers.add("analyst_report", _report())
    for _ in range(3):
        rig.step()
    cards = [item.card_id for item in rig.workers.launched if item.role == "analyst_report"]
    assert cards == [CARD_ID, other.card_id]


# --- 後追いの PR: 段の外に残った作業役の起動の印（作業役は同時に 1 つのまま） -------


def _second_card(rig: Rig, phase: str = "C8") -> CardState:
    store = StateStore(rig.paths.state_dir)
    other = CardState(
        card_id="h-20261120-other",
        mouth="human",
        phase=phase,
        taken_at=format_time(rig.clock.now()),
        experiment="c_other",
        version=2,
    )
    store.save_card(other)
    whole = _load_global(rig)
    whole.cards.append(other.card_id)
    store.save_global(whole)
    return other


def _stop_by_hand(rig: Rig, identifier: str, *, back_to: str) -> int:
    """作業役が走っている間に、カードを停止（C13）に移す（遅れて届いた止まりの理由の代わり）。"""
    from tools.ops.research_runner.state import OpenReason

    number = rig.github.create_issue(
        title=f"[runner] 止まった: {identifier} {CARD_ID}", body="", label="runner-stop"
    )
    store = StateStore(rig.paths.state_dir)
    card = store.load_card(CARD_ID)
    card.phase = "C13"
    card.return_phase = back_to
    card.reasons.append(OpenReason(identifier, number, "", format_time(rig.clock.now())))
    store.save_card(card)
    return number


def _launched(rig: Rig) -> list[tuple[str, str | None]]:
    return [(item.role, item.card_id) for item in rig.workers.launched]


def test_a_worker_left_behind_by_a_stopped_card_is_cleared_when_it_ends(rig: Rig) -> None:
    """作業役が走っている間にカードが停止（C13）に移っても、作業役が終われば起動の印が外れ、
    ほかのカードの作業役が人間の解除を待たずに起動する。走っている間は待つ（同時に 1 つ）。"""
    _inject(rig, "C8")
    other = _second_card(rig)
    rig.workers.hold.add("analyst_report")
    rig.step()
    assert _launched(rig) == [("analyst_report", CARD_ID)]
    _stop_by_hand(rig, "REPRO_MISMATCH", back_to="C8")
    for _ in range(2):
        rig.step()
    assert _launched(rig) == [("analyst_report", CARD_ID)]  # 走っている間は待つ
    assert "worker" in rig.card(CARD_ID)["procs"]
    rig.workers.hold.discard("analyst_report")
    rig.workers.add("analyst_report", _report())  # 止まったカードの作業役が終わる
    rig.step()
    assert "worker" not in rig.card(CARD_ID)["procs"]
    assert _launched(rig) == [("analyst_report", CARD_ID), ("analyst_report", other.card_id)]
    assert _phase(rig) == "C13"


def test_an_answer_that_discards_a_running_worker_waits_for_it_to_end(rig: Rig) -> None:
    """答え（範囲を変えずに別の変更を選ぶ）で走っている作業役の結果を捨てても、その作業役が
    終わるまで次の作業役を起動しない（すぐに印を外すと 2 つ同時に動く）。"""
    _inject(rig, "C7")
    rig.workers.hold.add("analyst_change")
    rig.step()
    assert _launched(rig) == [("analyst_change", CARD_ID)]
    issue = _stop_by_hand(rig, "CHANGE_OUT_OF_RULES", back_to="C7")
    rig.world.answer(issue, "1")
    rig.step()
    assert _phase(rig) == "C7"
    assert rig.card(CARD_ID)["procs"]["worker"]["stale"] is True
    rig.step()
    assert _launched(rig) == [("analyst_change", CARD_ID)]  # 前の作業役が走っている間は待つ
    rig.workers.hold.discard("analyst_change")
    rig.workers.add("analyst_change", status=WorkerStatus.FAILED)  # 前の作業役が終わる（捨てる）
    rig.step()
    assert _launched(rig) == [("analyst_change", CARD_ID), ("analyst_change", CARD_ID)]


def test_ending_a_card_waits_for_its_running_worker(rig: Rig) -> None:
    """「このカードを終える」は、走っている作業役が終わるまで終わりにしない（印を消すと、ほかの
    カードの作業役が同時に起動しうる）。終われば終わり、ほかのカードの作業役が起動する。"""
    _inject(rig, "C8")
    other = _second_card(rig)
    rig.workers.hold.add("analyst_report")
    rig.step()
    issue = _stop_by_hand(rig, "REPRO_MISMATCH", back_to="C8")
    rig.world.answer(issue, "2")  # このカードを終える
    for _ in range(2):
        rig.step()
    assert _phase(rig) == "C13"
    assert _launched(rig) == [("analyst_report", CARD_ID)]
    rig.workers.hold.discard("analyst_report")
    rig.workers.add("analyst_report", _report())
    for _ in range(2):
        rig.step()
    assert _phase(rig) == "C14"
    assert ("analyst_report", other.card_id) in _launched(rig)


# --- 後追いの PR: 自動の削除の名前とシンボリックリンク（取り消せない操作は残す側） ---


@pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "../c_other", "x\\y", "-x"])
def test_auto_deletion_keeps_everything_when_the_experiment_name_is_not_a_place_name(
    rig: Rig, bad: str
) -> None:
    """実験名が置き場の名前でない（空・`..`・区切り文字など）なら、そのカードの出力を何も消さない。"""
    from odyssey_fx.evaluation.domain.search import SearchVerdict

    paths = _deletion_world(rig, SearchVerdict.BELOW_STANDARD)
    store = StateStore(rig.paths.state_dir)
    card = store.load_card(CARD_ID)
    card.experiment = bad
    store.save_card(card)
    runs = {_hex(f"run-{key}") for key in ("a1", "a2")}
    rig.evaluation.run_ids = lambda version_dir: tuple(sorted(runs))  # type: ignore[method-assign]
    escaped = _put(rig.world.repo / "runs" / "v1" / "keep.txt")
    _low_disk_step(rig)
    for path in (*paths.values(), escaped):
        assert path.exists(), path
    assert not list(rig.paths.deleted_dir.glob("*.jsonl")) or not any(
        listing.read_text() for listing in rig.paths.deleted_dir.glob("*.jsonl")
    )


def test_auto_deletion_does_not_follow_or_remove_symbolic_links(rig: Rig, tmp_path: Path) -> None:
    """置き場の中にリンクがあれば、その置き場を丸ごと残し、リンクの先を一覧にも書かない。置き場
    そのものがリンクでも残す。リンクの無い置き場は従来どおり消す。"""
    import json
    import shutil

    from odyssey_fx.evaluation.domain.search import SearchVerdict

    paths = _deletion_world(rig, SearchVerdict.BELOW_STANDARD)
    outside = tmp_path / "outside"
    secret = _put(outside / "sealed.csv", "outside")
    (paths["run_a1"].parent / "link.csv").symlink_to(secret)
    run_a2 = paths["run_a2"].parent
    shutil.move(str(run_a2), str(outside / "a2"))
    run_a2.symlink_to(outside / "a2", target_is_directory=True)
    _low_disk_step(rig)
    assert paths["run_a1"].exists() and (paths["run_a1"].parent / "link.csv").is_symlink()
    assert run_a2.is_symlink() and (outside / "a2" / "trades.parquet").exists()
    assert secret.read_text() == "outside"
    assert not paths["repro_a1"].exists() and not paths["repro_a2"].exists()
    listed = set()
    for listing in rig.paths.deleted_dir.glob("*.jsonl"):
        listed |= {json.loads(line)["path"] for line in listing.read_text().splitlines()}
    assert listed and not any("link.csv" in item or "a2" in item.split("/")[1] for item in listed)


# --- 後追いの PR: 止まる条件の検出（費用の危険・封印が要る。D25 §4.6） ---------------------------


def test_a_command_outside_the_allowed_list_stops_everything_before_launching(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段が許可の一覧に無いコマンドを要したら（ここでは一覧から審査を外して真似る）、起動せずに
    当たったカードを添えて全体の停止（`COST_RISK`）。「そのカードを終えて続ける」で終えて外れる。"""
    from tools.ops.research_runner import commands

    _inject(rig, "C10", reservations=["review"], checks={"gate_passed": True})
    monkeypatch.setattr(
        commands, "ALLOWED_EXPERIMENT_COMMANDS", commands.ALLOWED_EXPERIMENT_COMMANDS - {"review"}
    )
    rig.step()
    assert "COST_RISK" in _global_reasons(rig)
    (item,) = [row for row in rig.state()["reasons"] if row["reason"] == "COST_RISK"]
    assert item["card"] == CARD_ID
    assert not [key for key in rig.procs.spawned if key.startswith("review:")]
    rig.step()
    assert not [key for key in rig.procs.spawned if key.startswith("review:")]
    rig.world.answer(rig.world.issue_for("COST_RISK").number, "2")
    for _ in range(2):
        rig.step()
    assert "COST_RISK" not in _global_reasons(rig)
    assert _phase(rig) == "C14"


def test_the_allowed_list_names_the_design_commands_only() -> None:
    from tools.ops.research_runner.commands import unlisted_command

    head = ("uv", "run", "--frozen", "odyssey-fx")
    for sub in ("run", "report", "ledger", "reproduce", "review", "final-validate", "fix"):
        assert unlisted_command([*head, "experiment", sub, "--x"], head) is None
    for program in ("git", "gh", "claude"):
        assert unlisted_command([program, "status"], head) is None
    assert unlisted_command([*head, "data", "fetch"], head) == "odyssey-fx data fetch"
    assert unlisted_command([*head, "experiment", "purchase"], head) == (
        "odyssey-fx experiment purchase"
    )
    assert unlisted_command(["aws", "s3", "cp"], head) == "aws"
    assert unlisted_command([], head) == "（空のコマンド）"


def test_a_bundle_that_needs_files_outside_the_allowed_list_stops_everything(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読む束を作る段で許可の一覧の外のファイルを求められたら（束を作る道具が知らせる）、作業役を
    起動せず、当たったカードを添えて全体の停止（`SEALED_NEEDED`）。起動の印を残さない。"""
    from tests.fixtures.research_runner import FakeWorkers
    from tools.ops.research_runner.ports import BundleOutOfScope, WorkerRequest

    def out_of_scope(self: FakeWorkers, request: WorkerRequest) -> None:
        raise BundleOutOfScope("data/ の下のファイル")

    _inject(rig, "C8")
    monkeypatch.setattr(FakeWorkers, "launch", out_of_scope)
    rig.step()
    (item,) = [row for row in rig.state()["reasons"] if row["reason"] == "SEALED_NEEDED"]
    assert item["card"] == CARD_ID
    assert "worker" not in rig.card(CARD_ID)["procs"]
    assert _phase(rig) == "C8"
    assert "data/" not in rig.world.issue_for("SEALED_NEEDED").body
