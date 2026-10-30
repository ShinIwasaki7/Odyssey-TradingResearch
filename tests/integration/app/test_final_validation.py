"""最終検証のコマンド（関所の最終評価の許可）の受入テスト（D09 v0.5 §9.10.11。D15 の終了条件 E3）。

**人工データと一時の git リポジトリだけ**を使う（`tests/fixtures/final_validation.py`）。封印の
snapshot は manifest だけの人工の形、origin は一時ディレクトリの bare リポジトリ、最終検証の run は
人工の `FakeRunner`（段 2 (f)(g) と段 5 の本番の実装 `app.final_run` は R4b。人工データの
本番の run は `tests/integration/app/test_final_run.py`）。やり直し（`--resume`）は要決定
R2-8 (b)・仮置き R2-21〜R2-27（2026-10-30 に決定。R4b で実装）。戦略の成績・価格・2024 年以降の
実データ・旧 CSV は読まない。番号はD09 §9.10.11 の受入テストの表の番号。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.app.final_validation import (
    EXIT_FAILED,
    EXIT_FAILED_AFTER_CONSUMPTION,
    EXIT_GATE_REFUSED,
    EXIT_OK,
    EXIT_READ_ERROR,
    EXIT_REFUSED,
    CandidateFacts,
    FinalMode,
    FinalValidationOutcome,
    FinalValidationRequest,
    final_validate,
)
from odyssey_fx.app.final_validation_records import (
    ATTEMPTS_DIRECTORY,
    ATTEMPTS_FILE,
    FINAL_EVALUATION_FILE,
    FINAL_LOCK_FILE,
    PERMIT_FILE,
    REPORT_FILE,
    final_directory,
    read_final_ledger,
)
from odyssey_fx.app.holdout_access_log import GitHoldoutAccessLog
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.application.ports import HoldoutAccessLogStop
from odyssey_fx.evaluation.domain.final_validation import (
    CandidateKey,
    FinalReason,
)
from odyssey_fx.evaluation.domain.search import SearchVerdict, StandardPurpose
from odyssey_fx.evaluation.domain.variant import Paired
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.final_validation import (
    DRAWDOWN,
    NET_RETURN,
    SERIES,
    T_FIX,
    FakeRunner,
    FinalWorkspace,
    build_workspace,
    write_fixation,
    write_review,
)
from tests.fixtures.git_origin import isolate_git

#: 人工の窓の取引週の数（[2024-01-01, T_FIX) の中で終わる週）と、消費の対象の数（2 時間足 × 2 組）。
_WEEKS = 53
_TARGETS = _WEEKS * 2 * 2


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git(monkeypatch, tmp_path / "githome")


class _Candidates:
    """人工の候補の事実（記録票・結末記録の代わり）。"""

    def __init__(self, facts: CandidateFacts) -> None:
        self.facts = facts

    def read(self, experiment_dir: Path, execution: int) -> CandidateFacts:
        assert execution == self.facts.key.execution
        return self.facts


@pytest.fixture
def ws(tmp_path: Path) -> FinalWorkspace:
    return build_workspace(tmp_path)


def _run(
    ws: FinalWorkspace,
    runner: FakeRunner | None = None,
    *,
    mode: FinalMode = FinalMode.APPLY,
    facts: CandidateFacts | None = None,
    declaration: Path | None = None,
    log: GitHoldoutAccessLog | None = None,
    no_declaration: bool = False,
) -> FinalValidationOutcome:
    chosen = facts or ws.facts()
    return final_validate(
        FinalValidationRequest(
            experiment_dir=ws.experiment_dir,
            execution=chosen.key.execution,
            repo_root=ws.repo,
            snapshots_root=ws.snapshots,
            out_root=ws.repo,
            declaration=None if no_declaration else declaration or ws.declaration(key=chosen.key),
            by="人工の実行者",
            mode=mode,
        ),
        candidates=_Candidates(chosen),
        runner=runner or FakeRunner(research=ws.research),
        log=log,
    )


def _logs(ws: FinalWorkspace) -> dict[str, bytes]:
    return {
        path.parent.name: path.read_bytes()
        for path in sorted(ws.snapshots.glob("*/access_log.jsonl"))
    }


def _rows(ws: FinalWorkspace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(ws.snapshots.glob("*/access_log.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines())
    return rows


def _ledger(ws: FinalWorkspace) -> list[str]:
    return [row.result.value for row in read_final_ledger(ws.repo).rows]


def _head(ws: FinalWorkspace) -> str:
    return ws.git("rev-parse", "HEAD")


def _origin_head(ws: FinalWorkspace) -> str:
    return ws.git("ls-remote", "origin", "refs/heads/main").split()[0]


def _final(ws: FinalWorkspace, execution: int = 1) -> Path:
    return final_directory(ws.experiment_dir, execution)


def _no_price(outcome: FinalValidationOutcome) -> None:
    text = "\n".join([*outcome.lines, outcome.detail])
    for value in (str(NET_RETURN), str(DRAWDOWN), "157."):
        assert value not in text


def _snapshot(ws: FinalWorkspace) -> tuple[dict[str, bytes], list[str], str, list[str]]:
    final = _final(ws)
    files = sorted(p.name for p in final.iterdir()) if final.exists() else []
    return _logs(ws), _ledger(ws), _origin_head(ws), files


# --- 1・2・12: 1 回目の申請（E3 の本体）と 2 回目の申請 --------------------------------------


def test_1_the_first_application_consumes_records_and_validates(ws: FinalWorkspace) -> None:
    runner = FakeRunner(research=ws.research)
    outcome = _run(ws, runner)
    assert outcome.exit_code == EXIT_OK, outcome.detail
    _no_price(outcome)
    rows = _rows(ws)
    # 消費の対象のすべての partition に版 1 の行が 1 行ずつ（同じ許可の識別子）。
    assert len(rows) == _TARGETS
    assert {row["schema_version"] for row in rows} == {"1"}
    assert {row["reason"] for row in rows} == {"FINAL_EVALUATION"}
    assert {row["permit_id"] for row in rows} == {outcome.permit_id}
    assert {row["purpose"] for row in rows} == {"人工の候補を封印期間で 1 回だけ確かめる"}
    assert all(row["candidate"]["experiment_id"] == ws.key.experiment_id for row in rows)
    assert len({(row["partition"], row["permit_id"]) for row in rows}) == _WEEKS * 2
    # T_fix の後の週は消費しない。
    assert not any("20250112" in str(row["partition"]) for row in rows)
    # 1 つの commit が origin に届き、その commit は消費の記録だけを変える。
    assert _head(ws) == _origin_head(ws) == outcome.commit
    changed = ws.git("show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(changed) == sorted(
        f"data/snapshots/{snapshot}/access_log.jsonl"
        for snapshot in (ws.sealed.standard, ws.sealed.candidate)
    )
    # 控え・最終検証の記録・人が読む記録・台帳の行。
    final = _final(ws)
    assert sorted(p.name for p in final.iterdir()) == sorted(
        [PERMIT_FILE, FINAL_EVALUATION_FILE, REPORT_FILE, ATTEMPTS_FILE]
    )
    permit = json.loads((final / PERMIT_FILE).read_text())
    assert permit["review_row_sha256"] and permit["fixation_sha256"]
    assert set(permit["ledger_tally"]) == {
        "overlapping_executions",
        "overlapping_trials",
        "trial_ledger_head",
    }
    record = json.loads((final / FINAL_EVALUATION_FILE).read_text())
    assert record["ledger_tally"] == permit["ledger_tally"]
    assert record["combined_verdict"]["verdict"] == "MEETS_STANDARD"
    assert record["scoring_start"] == "2024-01-01T00:00:00Z"  # 窓が 2024-01-01 から（ずらさない）
    text = (final / FINAL_EVALUATION_FILE).read_text() + (final / REPORT_FILE).read_text()
    assert str(NET_RETURN) not in text and str(DRAWDOWN) not in text  # 指標の値は参照だけ
    assert _ledger(ws) == ["COMPLETED"]
    assert runner.calls == ["prepare", "plan", "run:standard", "run:candidate"]
    # 状態の導出: すべて CONSUMED。
    from odyssey_fx.app.sealed_intake import snapshot_sealed_partitions

    states = {
        item.partition.state
        for item in snapshot_sealed_partitions(ws.snapshots)
        if item.partition.partition_id.period is not None
        and item.partition.partition_id.period.end <= T_FIX
    }
    assert {state.value for state in states if state is not None} == {"CONSUMED"}


def test_2_a_second_application_of_the_same_candidate_is_refused(ws: FinalWorkspace) -> None:
    assert _run(ws).exit_code == EXIT_OK
    before = _snapshot(ws)
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (EXIT_REFUSED, FinalReason.ALREADY_VALIDATED)
    assert _snapshot(ws) == before


def test_12_every_timeframe_variant_and_duplicate_snapshot_is_consumed(tmp_path: Path) -> None:
    """要決定 R2-2 (a): 同じ銘柄のすべての時間足・両組・すべての承認済み snapshot（重複を含む）。"""
    from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant
    from tests.fixtures.final_validation import sealed_manifest, weeks, write_snapshot

    ws = build_workspace(tmp_path)
    duplicate = write_snapshot(
        ws.snapshots,
        sealed_manifest(TimeRuleVariant.STANDARD, weeks()[:30], approved_at="2026-12-01T00:00:00Z"),
    )
    ws.commit_and_push("a later snapshot that repeats 30 weeks")
    outcome = _run(ws)
    assert outcome.exit_code == EXIT_OK, outcome.detail
    rows = _rows(ws)
    assert len(rows) == _TARGETS + 30 * 2
    permit = json.loads((_final(ws) / PERMIT_FILE).read_text())
    # 読むのは最初に承認された snapshot の方だけ（仮置き R2-9）。
    assert all(not item.startswith(duplicate) for item in permit["reads"]["standard"])
    assert len(permit["reads"]["standard"]) == _WEEKS * 2


# --- 3・4・5・23: 候補でない・台帳・審査・固定・申告 -----------------------------------------


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"combined": SearchVerdict.BELOW_STANDARD}, FinalReason.NOT_MEETS_STANDARD),
        ({"completed": False}, FinalReason.NOT_MEETS_STANDARD),
        ({"current_policy_version": 4}, FinalReason.NOT_CURRENT_POLICY_VERSION),
        ({"purpose": StandardPurpose.MECHANISM_CHECK}, FinalReason.NOT_CURRENT_POLICY_VERSION),
        ({"final_holdout": None}, FinalReason.NO_FINAL_HOLDOUT),
        ({"started_line_digest": "0" * 64}, FinalReason.LEDGER_NOT_ON_ORIGIN),
    ],
)
def test_3_4_a_non_candidate_is_refused_and_nothing_is_written(
    ws: FinalWorkspace, changes: dict[str, object], reason: FinalReason
) -> None:
    before = _snapshot(ws)
    outcome = _run(ws, facts=ws.facts(**changes))
    assert (outcome.exit_code, outcome.reason) == (EXIT_REFUSED, reason)
    assert _snapshot(ws) == before


def test_4_without_a_proceed_review_or_a_fixation_the_application_is_refused(
    tmp_path: Path,
) -> None:
    ws = build_workspace(tmp_path / "a", review=False)
    before = _snapshot(ws)
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.NOT_PROCEEDED_BY_REVIEW,
    )
    assert _snapshot(ws) == before
    other = build_workspace(tmp_path / "b", fixation=False)
    outcome = _run(other)
    assert (outcome.exit_code, outcome.reason) == (EXIT_REFUSED, FinalReason.NOT_FIXED)
    # 審査の行の固定した試行が読めなければ照合を省かず拒否する（Codex 2 巡目）。
    fourth = build_workspace(tmp_path / "d")
    write_review(fourth.repo, [fourth.key], candidate_fixed=False)
    fourth.commit_and_push("a review row without the fixed trial of a variant")
    assert _run(fourth).reason is FinalReason.NOT_FIXED
    # 審査台帳は R3 の読込と同じ検査で読む: 手で書き換えた行（鎖・正規化の誤り）は終了コード 2
    # （仮置き R4b-10）。
    review = fourth.repo / "research" / "adoption_reviews.jsonl"
    review.write_text(review.read_text().replace('"execution":1', '"execution":1 '))
    fourth.commit_and_push("a review row edited by hand")
    edited = _run(fourth)
    assert (edited.exit_code, edited.reason) == (EXIT_READ_ERROR, None)
    # 審査の行が「進めない」なら進めない。
    third = build_workspace(tmp_path / "c")
    write_review(third.repo, [third.key], result="DO_NOT_PROCEED")
    third.commit_and_push("review says do not proceed")
    assert _run(third).reason is FinalReason.NOT_PROCEEDED_BY_REVIEW


def test_4_ledger_lines_that_are_only_in_the_working_tree_are_not_enough(
    ws: FinalWorkspace,
) -> None:
    ws.git("rm", "--quiet", "research/trial_ledger.jsonl")
    ws.commit_and_push("the ledger is gone from origin")
    ws.git("checkout", "HEAD~1", "--", "research/trial_ledger.jsonl")  # 作業木にだけ戻す
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (EXIT_REFUSED, FinalReason.LEDGER_NOT_ON_ORIGIN)


def test_5_23_a_used_declaration_marks_the_candidate_used_and_stays_refused(
    ws: FinalWorkspace,
) -> None:
    logs = _logs(ws)
    outcome = _run(ws, declaration=ws.declaration(used=True))
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.OUTSIDE_VIEWING_DECLARED,
    )
    assert _logs(ws) == logs  # partition の状態は変えない（ADR-0037 第3項の 5）
    assert _ledger(ws) == ["DECLARED_USED"]
    row = read_final_ledger(ws.repo).rows[0].payload
    assert row["declaration_sha256"] and row["permit_id"] == "" and row["partition_count"] == 0
    # 「使っていない」の申告で申請し直しても拒否され、何も書かない（状態 G）。
    again = _run(ws, declaration=ws.declaration(used=False))
    assert (again.exit_code, again.reason) == (EXIT_REFUSED, FinalReason.OUTSIDE_VIEWING_DECLARED)
    assert _ledger(ws) == ["DECLARED_USED"]
    # 同じ experiment_id の別の実行にも当てる（仮置き R2-29 の (2)）。
    second = replace(ws.key, execution=2)
    assert _run(ws, facts=ws.facts(key=second)).reason is FinalReason.OUTSIDE_VIEWING_DECLARED


def test_a_declaration_that_does_not_cover_the_period_or_names_another_candidate(
    ws: FinalWorkspace,
) -> None:
    outcome = _run(ws, declaration=ws.declaration(start="2024-07-01T00:00:00Z"))
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.DECLARATION_NOT_COVERING,
    )
    other = replace(ws.key, experiment_id="f" * 64)
    assert _run(ws, declaration=ws.declaration(key=other)).exit_code == EXIT_READ_ERROR
    assert _run(ws, no_declaration=True).exit_code == EXIT_READ_ERROR
    assert _ledger(ws) == []


# --- 6・7・21: origin ------------------------------------------------------------------------


def test_6_origin_ahead_unreachable_or_an_uncommitted_append_stops_with_8(
    ws: FinalWorkspace,
) -> None:
    before = _logs(ws)
    # 作業木に未コミットの追記。
    log = ws.snapshots / ws.sealed.standard / "access_log.jsonl"
    log.write_text('{"kind":"CONSUMED","partition":"x"}\n')
    outcome = _run(ws)
    assert outcome.exit_code in (EXIT_GATE_REFUSED,), outcome.detail
    log.unlink()
    # origin が先に進んでいる（別のクローンが push した）。
    clone = ws.repo.parent / "clone"
    ws.git("clone", "--quiet", str(ws.origin), str(clone))
    from tests.fixtures.git_origin import git

    git(clone, "config", "user.name", "other", home=ws.home)
    git(clone, "config", "user.email", "other@example.invalid", home=ws.home)
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "another clone", home=ws.home)
    git(clone, "push", "--quiet", "origin", "main", home=ws.home)
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_GATE_REFUSED,
        FinalReason.ORIGIN_NOT_CONFIRMED,
    )
    ws.git("pull", "--quiet", "origin", "main")
    # origin に到達できない。
    ws.origin.rename(ws.origin.with_name("moved.git"))
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_GATE_REFUSED,
        FinalReason.ORIGIN_NOT_CONFIRMED,
    )
    assert _logs(ws) == before
    assert not _final(ws).exists()


def test_7_a_rejected_push_leaves_no_commit_no_append_and_no_run(ws: FinalWorkspace) -> None:
    hook = ws.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho rejected >&2\nexit 1\n")
    hook.chmod(0o755)
    head = _head(ws)
    logs = _logs(ws)
    runner = FakeRunner(research=ws.research)
    outcome = _run(ws, runner)
    assert (outcome.exit_code, outcome.reason) == (EXIT_GATE_REFUSED, FinalReason.PUSH_REJECTED)
    assert _head(ws) == head == _origin_head(ws)
    assert _logs(ws) == logs  # 追記したファイルはコミット済みの内容に戻る（仮置き R2-2）
    assert ws.git("status", "--porcelain", "--", "data") == ""
    assert not any(call.startswith("run:") for call in runner.calls)
    assert not (_final(ws) / FINAL_LOCK_FILE).exists()
    hook.unlink()
    assert _run(ws).exit_code == EXIT_OK  # 状態 A に戻っている


class _CutResponse(GitHoldoutAccessLog):
    """push の応答が途切れる（`reach` なら origin には届く）。"""

    def __init__(self, ws: FinalWorkspace, *, reach: bool) -> None:
        super().__init__(ws.repo, ws.snapshots)
        self._reach = reach

    def _push(self, branch: str) -> None:
        if self._reach:
            super()._push(branch)
        raise HoldoutAccessLogStop("push", "the response was cut (synthetic)")


def test_21_a_cut_push_response_is_settled_by_asking_origin_again(tmp_path: Path) -> None:
    reached = build_workspace(tmp_path / "reached")
    outcome = _run(reached, log=_CutResponse(reached, reach=True))
    assert outcome.exit_code == EXIT_OK, outcome.detail
    assert _ledger(reached) == ["COMPLETED"]
    lost = build_workspace(tmp_path / "lost")
    head = _head(lost)
    outcome = _run(lost, log=_CutResponse(lost, reach=False))
    assert (outcome.exit_code, outcome.reason) == (EXIT_GATE_REFUSED, FinalReason.PUSH_REJECTED)
    assert _head(lost) == head == _origin_head(lost)
    assert _rows(lost) == []


# --- 8・14: 窓 -------------------------------------------------------------------------------


def test_8_a_consumed_week_inside_the_window_cuts_it_and_l_is_checked(ws: FinalWorkspace) -> None:
    """違反の判定で途中の週が CONSUMED なら、最も新しい連続した部分だけ（ここでは L より短い）。"""
    partition = PartitionId(
        series=SERIES,
        access_class=AccessClass.FORWARD_HOLDOUT,
        period_start=UtcTime.parse("2024-09-01T21:00:00Z"),
    )
    line = {
        "actor": "t",
        "kind": "CONSUMED",
        "partition": str(partition),
        "purpose": "",
        "recorded_at": "2026-11-20T00:00:00Z",
    }
    (ws.snapshots / ws.sealed.standard / "access_log.jsonl").write_text(json.dumps(line) + "\n")
    ws.commit_and_push("a violation consumed one week")
    before = _snapshot(ws)
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.WINDOW_SHORTER_THAN_L,
    )
    assert _snapshot(ws) == before


def test_8_a_week_without_any_partition_stops_before_consumption(tmp_path: Path) -> None:
    from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant
    from tests.fixtures.final_validation import sealed_manifest, weeks, write_snapshot

    ws = build_workspace(tmp_path)
    # 候補の組の snapshot を、1 週を欠いたものに替える。
    import shutil

    shutil.rmtree(ws.snapshots / ws.sealed.candidate)
    periods = weeks()
    write_snapshot(
        ws.snapshots, sealed_manifest(TimeRuleVariant.CANDIDATE, periods[:20] + periods[21:])
    )
    ws.commit_and_push("a week is missing in the candidate variant")
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.WINDOW_HAS_MISSING_WEEK,
    )
    assert _rows(ws) == []


def test_14_a_second_execution_only_gets_the_next_window(ws: FinalWorkspace) -> None:
    """要決定 R2-4 (c): 二度目の申請は前の申請が消費した partition を除いた次の窓だけ。"""
    assert _run(ws).exit_code == EXIT_OK
    second = replace(ws.key, execution=2)
    write_review(ws.repo, [ws.key, second])
    write_fixation(ws.repo, second, t_fix=UtcTime.parse("2025-02-01T00:00:00Z"))
    from tests.fixtures.evaluation import trial_ledger

    first = trial_ledger.started("cand", purpose=StandardPurpose.STANDARD)
    opening = trial_ledger.started(
        "cand", execution=2, nonce="1" * 32, purpose=StandardPurpose.STANDARD
    )
    lines = trial_ledger.chain(
        [
            first,
            trial_ledger.finished(first, verdict=SearchVerdict.MEETS_STANDARD),
            opening,
            trial_ledger.finished(opening, verdict=SearchVerdict.MEETS_STANDARD),
        ]
    )
    trial_ledger.write_ledger(ws.repo, lines)
    ws.commit_and_push("second execution reviewed and fixed")
    before = _logs(ws)
    outcome = _run(
        ws,
        facts=ws.facts(key=second, started_line_digest=lines[2].digest.hex),
        declaration=ws.declaration(key=second, end="2025-02-01T00:00:00Z"),
    )
    assert (outcome.exit_code, outcome.reason) == (EXIT_REFUSED, FinalReason.WINDOW_SHORTER_THAN_L)
    assert _logs(ws) == before


# --- 9・10・24: 消費の後の失敗・状態 C・やり直しの入口 -------------------------------------------


def _attempts(ws: FinalWorkspace) -> list[dict[str, Any]]:
    path = _final(ws) / ATTEMPTS_FILE
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_9_a_failure_after_consumption_waits_in_c_and_a_resume_validates(
    ws: FinalWorkspace,
) -> None:
    outcome = _run(ws, FakeRunner(research=ws.research, fail_run=True))
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_FAILED_AFTER_CONSUMPTION,
        FinalReason.FINAL_RUN_FAILED,
    )
    assert len(_rows(ws)) == _TARGETS and _ledger(ws) == []  # 状態 C（台帳に書かない）
    assert not (_final(ws) / FINAL_LOCK_FILE).exists()
    assert [row["event"] for row in _attempts(ws)] == ["STARTED", "FAILED"]
    logs = _logs(ws)
    head = _origin_head(ws)
    # 引数なしは CONSUMED_WITHOUT_RECORD。
    plain = _run(ws)
    assert (plain.exit_code, plain.reason) == (
        EXIT_GATE_REFUSED,
        FinalReason.CONSUMED_WITHOUT_RECORD,
    )
    # --resume は消費の行を増やさず、同じ許可で段 5 をやり直す（要決定 R2-8 (b)）。
    runner = FakeRunner(research=ws.research)
    resumed = _run(ws, runner, mode=FinalMode.RESUME)
    assert resumed.exit_code == EXIT_OK, resumed.detail
    assert _logs(ws) == logs and _origin_head(ws) == head
    assert resumed.permit_id == {row["permit_id"] for row in _rows(ws)}.pop()
    assert _ledger(ws) == ["COMPLETED"]
    row = read_final_ledger(ws.repo).rows[0].payload
    assert (row["resume_count"], row["resume_failures"]) == (1, ["FINAL_RUN_FAILED"])
    record = json.loads((_final(ws) / FINAL_EVALUATION_FILE).read_text())
    assert (record["resume_count"], record["resume_failures"]) == (1, ["FINAL_RUN_FAILED"])
    assert [row["event"] for row in _attempts(ws)] == ["STARTED", "FAILED", "STARTED"]
    assert _run(ws, mode=FinalMode.RESUME).reason is FinalReason.NOT_AWAITING_RECORD


def test_9_a_failure_after_consumption_closes_as_failed(ws: FinalWorkspace) -> None:
    assert _run(ws, FakeRunner(research=ws.research, fail_run=True)).exit_code == 9
    closed = _run(ws, mode=FinalMode.CLOSE_FAILED, no_declaration=True)
    assert closed.exit_code == EXIT_FAILED_AFTER_CONSUMPTION
    assert _ledger(ws) == ["FAILED_AFTER_CONSUMPTION"]
    row = read_final_ledger(ws.repo).rows[0].payload
    assert row["partition_count"] == _TARGETS and row["permit_id"]
    assert row["t_fix"] == str(T_FIX)
    assert (row["resume_count"], row["resume_failures"]) == (0, ["FINAL_RUN_FAILED"])
    again = _run(ws)
    assert (again.exit_code, again.reason) == (EXIT_REFUSED, FinalReason.ALREADY_CONSUMED)
    assert _run(ws, mode=FinalMode.RESUME).reason is FinalReason.NOT_AWAITING_RECORD


def test_r4b_12_close_failed_without_the_permit_copy_takes_t_fix_from_the_fixation(
    ws: FinalWorkspace,
) -> None:
    assert _run(ws, FakeRunner(research=ws.research, fail_run=True)).exit_code == 9
    (_final(ws) / PERMIT_FILE).unlink()  # 段 4 の直後に止まった（人工）
    closed = _run(ws, mode=FinalMode.CLOSE_FAILED, no_declaration=True)
    assert closed.exit_code == EXIT_FAILED_AFTER_CONSUMPTION
    row = read_final_ledger(ws.repo).rows[0].payload
    assert row["t_fix"] == str(T_FIX) and row["consumption_commit"] == ""
    assert any("origin の候補の固定の記録から補った" in line for line in closed.lines)


# --- 24: やり直しの条件（仮置き R2-21〜R2-25） -------------------------------------------------


def _failed_once(ws: FinalWorkspace) -> dict[str, bytes]:
    assert _run(ws, FakeRunner(research=ws.research, fail_run=True)).exit_code == 9
    return _logs(ws)


def _refused_resume(ws: FinalWorkspace, runner: FakeRunner | None = None) -> None:
    logs = _logs(ws)
    files = sorted(str(path) for path in _final(ws).rglob("*"))
    outcome = _run(ws, runner, mode=FinalMode.RESUME)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_GATE_REFUSED,
        FinalReason.RESUME_NOT_ALLOWED,
    ), outcome.detail
    assert _logs(ws) == logs and _ledger(ws) == []
    assert sorted(str(path) for path in _final(ws).rglob("*")) == files
    assert not (_final(ws) / FINAL_LOCK_FILE).exists()


def test_24_resume_without_the_permit_copy_is_refused(ws: FinalWorkspace) -> None:
    _failed_once(ws)
    (_final(ws) / PERMIT_FILE).unlink()
    _refused_resume(ws)


def test_24_resume_with_a_copy_that_differs_from_origin_is_refused(ws: FinalWorkspace) -> None:
    _failed_once(ws)
    path = _final(ws) / PERMIT_FILE
    copy = json.loads(path.read_text())
    copy["targets"] = copy["targets"][1:]
    path.write_text(json.dumps(copy))
    _refused_resume(ws)


def test_24_resume_with_changed_inputs_is_refused(ws: FinalWorkspace) -> None:
    _failed_once(ws)
    runner = FakeRunner(research=ws.research, run_suffix="-changed")
    _refused_resume(ws, runner)


def test_24_resume_after_a_completed_run_or_an_evaluation_is_refused(ws: FinalWorkspace) -> None:
    _failed_once(ws)
    runs = ws.repo / "out_runs"
    runner = FakeRunner(research=ws.research, run_root=runs)
    (runs / "run-standard" / "eval").mkdir(parents=True)
    _refused_resume(ws, runner)


def test_24_unreadable_or_exhausted_attempts_refuse_a_resume(ws: FinalWorkspace) -> None:
    _failed_once(ws)
    path = _final(ws) / ATTEMPTS_FILE
    original = path.read_bytes()
    path.write_bytes(original + b"not json\n")
    _refused_resume(ws)
    path.write_bytes(original)
    for _ in range(2):  # やり直しがまた失敗する（2 回まで）
        again = _run(ws, FakeRunner(research=ws.research, fail_run=True), mode=FinalMode.RESUME)
        assert again.exit_code == EXIT_FAILED_AFTER_CONSUMPTION and _ledger(ws) == []
    assert [row["event"] for row in _attempts(ws)].count("STARTED") == 3
    _refused_resume(ws)  # 上限 2 回を超える


def test_24_a_resume_moves_the_partial_run_aside_without_opening_it(ws: FinalWorkspace) -> None:
    _failed_once(ws)
    runs = ws.repo / "out_runs"
    partial = runs / "run-standard"
    partial.mkdir(parents=True)
    (partial / "DECISIONS.parquet").write_bytes(b"partial")  # 途中の成果物（人工）
    outcome = _run(ws, FakeRunner(research=ws.research, run_root=runs), mode=FinalMode.RESUME)
    assert outcome.exit_code == EXIT_OK, outcome.detail
    moved = _final(ws) / ATTEMPTS_DIRECTORY / "1" / "runs" / "run-standard" / "DECISIONS.parquet"
    assert moved.read_bytes() == b"partial" and not partial.exists()


def test_10_a_stopped_process_leaves_the_lock_and_c_after_the_lock_is_removed(
    ws: FinalWorkspace,
) -> None:
    assert _run(ws, FakeRunner(research=ws.research, fail_run=True)).exit_code == 9
    lock = _final(ws) / FINAL_LOCK_FILE
    lock.write_text("stopped\n")  # 段 4 と段 5 の間で止まった（人工）
    assert _run(ws).exit_code == EXIT_FAILED
    lock.unlink()
    outcome = _run(ws)
    assert outcome.reason is FinalReason.CONSUMED_WITHOUT_RECORD
    assert _run(ws, mode=FinalMode.CLOSE_FAILED).exit_code == EXIT_FAILED_AFTER_CONSUMPTION


def test_10_a_stop_after_the_record_and_the_ledger_row_is_validated_not_failed(
    ws: FinalWorkspace,
) -> None:
    assert _run(ws).exit_code == EXIT_OK
    lock = _final(ws) / FINAL_LOCK_FILE
    lock.write_text("stopped\n")
    assert _run(ws).exit_code == EXIT_FAILED
    lock.unlink()
    outcome = _run(ws)
    assert outcome.reason is FinalReason.ALREADY_VALIDATED
    assert _ledger(ws) == ["COMPLETED"]  # 失敗の行を足さない


def test_d_with_a_record_but_no_ledger_row_fills_the_row_then_refuses(ws: FinalWorkspace) -> None:
    assert _run(ws).exit_code == EXIT_OK
    (ws.repo / "research" / "final_validations.jsonl").write_bytes(b"")
    dry = _run(ws, mode=FinalMode.DRY_RUN)
    assert dry.reason is FinalReason.ALREADY_VALIDATED and _ledger(ws) == []  # 乾式は書かない
    outcome = _run(ws)
    assert outcome.reason is FinalReason.ALREADY_VALIDATED
    assert _ledger(ws) == ["COMPLETED"]


# --- 11: 検査 P2F ---------------------------------------------------------------------------


def test_11_p2f_refuses_an_allowed_set_outside_the_permit_before_consumption(
    ws: FinalWorkspace,
) -> None:
    outside = PartitionId(
        series=SERIES,
        access_class=AccessClass.FORWARD_HOLDOUT,
        period_start=UtcTime.parse("2025-01-19T22:00:00Z"),
    )
    runner = FakeRunner(research=ws.research, extra_allowed=((ws.sealed.standard, outside),))
    before = _snapshot(ws)
    outcome = _run(ws, runner)
    assert (outcome.exit_code, outcome.reason) == (EXIT_REFUSED, FinalReason.P2F_NOT_PASSED)
    assert _snapshot(ws) == before


class _DropRowsAfterPush(GitHoldoutAccessLog):
    """push の後、段 5 の前に origin から消費の行を消す（人工）。"""

    def publish(self, paths, message, **kwargs):  # type: ignore[no-untyped-def]
        commit = super().publish(paths, message, **kwargs)
        root = Path(self._root)
        for rel in paths:
            (root / rel).write_bytes(b"")
        from tests.fixtures.git_origin import git

        git(root, "commit", "--quiet", "-am", "drop the rows (synthetic)")
        git(root, "push", "--quiet", "origin", "main")
        return commit


def test_11_rows_missing_on_origin_before_the_run_fail_after_consumption(
    ws: FinalWorkspace,
) -> None:
    runner = FakeRunner(research=ws.research)
    outcome = _run(ws, runner, log=_DropRowsAfterPush(ws.repo, ws.snapshots))
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_FAILED_AFTER_CONSUMPTION,
        FinalReason.RECORD_NOT_ON_ORIGIN,
    )
    assert not any(call.startswith("run:") for call in runner.calls)


# --- 15・16・22: 順番・乾式実行・取り下げ -------------------------------------------------------


def test_15_22_an_earlier_candidate_blocks_until_it_finishes_or_is_withdrawn(
    ws: FinalWorkspace,
) -> None:
    earlier = CandidateKey(
        experiment_id="e" * 64, execution=1, experiment_name="earlier", experiment_version=1
    )
    write_review(ws.repo, [earlier, ws.key])
    ws.commit_and_push("an earlier candidate passed the review first")
    outcome = _run(ws, mode=FinalMode.DRY_RUN)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.EARLIER_CANDIDATE_PENDING,
    )
    # 先の候補を取り下げる（消費しない）。
    logs = _logs(ws)
    withdrawn = _run(
        ws,
        mode=FinalMode.WITHDRAW,
        facts=ws.facts(key=earlier),
        no_declaration=True,
    )
    assert withdrawn.exit_code == EXIT_OK
    assert _ledger(ws) == ["WITHDRAWN"] and _logs(ws) == logs
    assert _run(ws, mode=FinalMode.DRY_RUN).exit_code == EXIT_OK
    # 取り下げた候補は申請できない。
    again = _run(ws, facts=ws.facts(key=earlier))
    assert (again.exit_code, again.reason) == (EXIT_REFUSED, FinalReason.WITHDRAWN)


def test_16_the_dry_run_prints_counts_only_and_writes_nothing(ws: FinalWorkspace) -> None:
    before = _snapshot(ws)
    runner = FakeRunner(research=ws.research)
    outcome = _run(ws, runner, mode=FinalMode.DRY_RUN)
    assert outcome.exit_code == EXIT_OK, outcome.detail
    assert _snapshot(ws) == before
    assert not _final(ws).exists()
    assert any(line.startswith("消費の対象の partition: ") for line in outcome.lines)
    assert any("[2024-01-01T00:00:00Z, " in line for line in outcome.lines)
    assert "run:standard" not in runner.calls
    _no_price(outcome)


# --- 本番の結線と CLI ---------------------------------------------------------------------


def test_the_production_runner_refuses_a_candidate_without_research_artifacts(
    ws: FinalWorkspace,
) -> None:
    """本番の最終検証の run（`app.final_run`）は、記録票・研究の成果物が無ければ段 2 (f) の
    再計算で止まる（終了コード 7 `RECOMPUTATION_MISMATCH`。何も書かない）。"""
    before = _snapshot(ws)
    outcome = final_validate(
        FinalValidationRequest(
            experiment_dir=ws.experiment_dir,
            execution=1,
            repo_root=ws.repo,
            snapshots_root=ws.snapshots,
            out_root=ws.repo,
            declaration=ws.declaration(),
            by="人工の実行者",
        ),
        candidates=_Candidates(ws.facts()),
    )
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.RECOMPUTATION_MISMATCH,
    )
    assert _snapshot(ws) == before


def test_the_cli_reads_the_arguments_and_reports_a_read_error_with_2(
    ws: FinalWorkspace, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "experiment",
            "final-validate",
            "--experiment-dir",
            str(ws.experiment_dir),
            "--execution",
            "1",
            "--repo-root",
            str(ws.repo),
            "--declaration",
            str(ws.declaration()),
            "--by",
            "人工の実行者",
            "--dry-run",
        ]
    )
    printed = capsys.readouterr().out
    assert code == EXIT_READ_ERROR  # 人工の版のディレクトリには記録票が無い
    assert "終了コード: 2" in printed
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "experiment",
                "final-validate",
                "--experiment-dir",
                str(ws.experiment_dir),
                "--execution",
                "1",
                "--by",
                "x",
                "--dry-run",
                "--withdraw",
            ]
        )
    assert raised.value.code == 2


def test_the_candidate_facts_of_another_execution_are_a_read_error(ws: FinalWorkspace) -> None:
    facts = ws.facts(final_trials=Paired(standard=2, candidate=2))
    assert facts.key.execution == 1
    outcome = _run(
        ws, FakeRunner(research=ws.research, final_trials=Paired(standard=1, candidate=2))
    )
    assert (outcome.exit_code, outcome.reason) == (EXIT_REFUSED, FinalReason.NOT_FIXED)


def test_18_experiment_run_never_takes_the_final_permit_nor_calls_the_gate() -> None:
    """`experiment run` は最終評価の許可を受け取らず、関所の最終評価を呼ばない（D09 §9.1）。"""
    import subprocess
    import sys

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, odyssey_fx.app.cli.main, odyssey_fx.app.composition;"
            " print(sorted(n for n in sys.modules"
            " if n.endswith(('final_validation', 'holdout_gate'))))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "[]"
    root = Path(__file__).resolve().parents[3] / "src" / "odyssey_fx"
    for module in ("app/composition.py", "evaluation/application/run_experiment.py"):
        text = (root / module).read_text(encoding="utf-8")
        assert "final_sources" not in text and "FinalReadGrant" not in text


# --- R4b: 残った軽微な指摘 4 件（仮置き R4b-11〜R4b-14。実装を設計に合わせる） -----------------


def _break_a_log(ws: FinalWorkspace) -> None:
    path = ws.snapshots / ws.sealed.standard / "access_log.jsonl"
    path.write_bytes((path.read_bytes() if path.exists() else b"") + b"not a json line\n")
    ws.commit_and_push("a broken consumption log (synthetic)")


def test_r4b_11_a_broken_log_is_read_only_to_tell_c_from_a(tmp_path: Path) -> None:
    """壊れた消費の記録は C と A を分けるときだけ読む（B は 1、D は書き足して 7、A は 8）。"""
    ws = build_workspace(tmp_path / "a")
    _break_a_log(ws)
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_GATE_REFUSED,
        FinalReason.CONSUMPTION_LOG_BROKEN,
    )
    lock = _final(ws) / FINAL_LOCK_FILE
    lock.parent.mkdir(parents=True)
    lock.write_text("stopped\n")
    assert (_run(ws).exit_code, _run(ws).reason) == (EXIT_FAILED, None)  # 状態 B
    done = build_workspace(tmp_path / "d")
    assert _run(done).exit_code == EXIT_OK
    (done.repo / "research" / "final_validations.jsonl").write_bytes(b"")
    _break_a_log(done)
    filled = _run(done)
    assert (filled.exit_code, filled.reason) == (EXIT_REFUSED, FinalReason.ALREADY_VALIDATED)
    assert _ledger(done) == ["COMPLETED"]  # 状態 D の台帳の行の書き足し


def test_r4b_13_a_report_failure_after_the_record_is_exit_1_and_state_d(
    ws: FinalWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odyssey_fx.app import final_validation as module

    original = module._report_text

    def broken(record: Any) -> str:
        raise OSError("the disk is full (synthetic)")

    monkeypatch.setattr(module, "_report_text", broken)
    outcome = _run(ws)
    assert (outcome.exit_code, outcome.reason) == (EXIT_FAILED, None), outcome.detail
    assert (_final(ws) / FINAL_EVALUATION_FILE).exists()
    assert not (_final(ws) / REPORT_FILE).exists()
    assert not (_final(ws) / FINAL_LOCK_FILE).exists()
    assert _ledger(ws) == ["COMPLETED"]
    monkeypatch.setattr(module, "_report_text", original)
    again = _run(ws)
    assert again.reason is FinalReason.ALREADY_VALIDATED
    assert (_final(ws) / REPORT_FILE).exists()  # 次の起動の段 1 で書き足す
    assert _ledger(ws) == ["COMPLETED"]


class _AnotherPushAfterOurs(GitHoldoutAccessLog):
    """消費の commit の push の直後に、別のクローンが origin へ push する（人工）。"""

    def __init__(self, ws: FinalWorkspace, other: Path) -> None:
        super().__init__(ws.repo, ws.snapshots)
        self._other = other

    def _push(self, branch: str) -> None:
        super()._push(branch)
        from tests.fixtures.git_origin import git

        git(self._other, "pull", "--quiet", "origin", branch)
        (self._other / "unrelated.txt").write_text("another clone\n")
        git(self._other, "add", "unrelated.txt")
        git(self._other, "commit", "--quiet", "-m", "an unrelated push")
        git(self._other, "push", "--quiet", "origin", f"HEAD:{branch}")


def test_r4b_14_a_push_followed_by_another_push_is_still_a_success(
    ws: FinalWorkspace, tmp_path: Path
) -> None:
    from tests.fixtures.git_origin import git

    other = tmp_path / "other"
    git(tmp_path, "clone", "--quiet", str(ws.origin), str(other))
    git(other, "config", "user.name", "other")
    git(other, "config", "user.email", "other@example.invalid")
    outcome = _run(ws, log=_AnotherPushAfterOurs(ws, other))
    assert outcome.exit_code == EXIT_OK, outcome.detail
    assert _origin_head(ws) != outcome.commit  # 先頭は進んでいるが、消費の commit を含む
    assert _ledger(ws) == ["COMPLETED"]


def test_24_resume_with_rows_of_another_permit_is_refused(ws: FinalWorkspace) -> None:
    _failed_once(ws)
    path = ws.snapshots / ws.sealed.standard / "access_log.jsonl"
    lines = path.read_text().splitlines()
    row = json.loads(lines[0])
    row["permit_id"] = "f" * 32
    row["row_id"] = "e" * 32
    from odyssey_fx.common.canonical import encode

    path.write_text("\n".join([*lines, encode(row).decode("utf-8")]) + "\n")
    ws.commit_and_push("a consumption row of another permit (synthetic)")
    _refused_resume(ws)
