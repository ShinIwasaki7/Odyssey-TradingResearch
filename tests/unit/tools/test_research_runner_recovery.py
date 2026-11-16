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
from tools.ops.research_runner.ports import PrState
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
    from tools.ops.research_runner.ports import WorkerStatus

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
