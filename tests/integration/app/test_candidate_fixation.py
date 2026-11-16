"""候補の固定の記録のコマンドの受入テスト（D09 §9.11.13 の F-4・F-5。順 11 の PR 4c）。

**人工データと一時の git リポジトリだけ**を使う（`tests/fixtures/final_validation.py` の作業場に、
前向き検証の規則・研究ポリシーの登録簿・審査の数値の記録の人工の写しを足す）。封印の snapshot は
manifest だけの人工の形、origin は一時ディレクトリの bare リポジトリ、ウォームアップは人工の
`FakeRunner`。実際の封印の partition・審査台帳・最終検証の台帳は読まず、消費もしない。研究の外の
相場の申告のファイルは置かない（決定 150。固定のコマンドは申告を求めない）。

- F-4: 固定時点は main の first-parent の履歴で記録を初めて含んだコミットの committer の時刻
  （merge のコミット）。main に入った後に変わった記録は読込で拒否する。開始と期間の終わりの計算の
  細部（夏時間の切替の週・境目ちょうど）は
  `tests/unit/evaluation/test_candidate_fixation_rules.py`。
- F-5: 固定の前の確かめの項目 1〜8 の合否（人工の台帳と manifest）と、終了コード（0・7・8・2）。

頻度 `r` と取引の件数は人工の値で、出力に現れないことも確かめる。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from odyssey_fx.app.candidate_fixation import (
    EXIT_DEFERRED,
    EXIT_OK,
    EXIT_READ_ERROR,
    EXIT_REFUSED,
    FixFacts,
    FixMode,
    FixOutcome,
    FixRequest,
    fix_candidate,
    show_fix,
)
from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config.candidate_fix import candidate_fix_text, load_candidate_fix
from odyssey_fx.app.final_validation import CandidateFacts
from odyssey_fx.app.final_validation_records import append_ledger_row, candidate_payload
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import CompiledStrategyRef, ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.review_store import review_line_bytes
from odyssey_fx.evaluation.domain.adoption_review import FixedTrial, ReviewResult
from odyssey_fx.evaluation.domain.candidate_fixation import (
    FixCheckItem,
    FixCheckReason,
    FixVerdict,
    forward_period,
    length_for_rate,
)
from odyssey_fx.evaluation.domain.final_validation import CandidateKey
from odyssey_fx.evaluation.domain.forward_validation import CandidateFixId, FixedTrialPin
from odyssey_fx.evaluation.domain.search import (
    ChangeKind,
    Lineage,
    LineageSource,
    SearchVerdict,
    StandardPurpose,
)
from odyssey_fx.evaluation.domain.variant import Paired
from tests.fixtures.evaluation import trial_ledger
from tests.fixtures.evaluation.adoption_review import review_entry, review_lines
from tests.fixtures.final_validation import FakeRunner, FinalWorkspace, build_workspace
from tests.fixtures.git_origin import git, isolate_git

#: 確かめの時刻（人工の封印の partition は 2025-02-09 の週まである）。
_NOW = UtcTime.parse("2025-03-01T00:00:00Z")
#: 人工の検証区間（2 年。頻度 `r` = 件数 × 365 ÷ 731）。
_VALIDATION = (
    Interval(
        start=UtcTime.parse("2015-01-01T00:00:00Z"), end=UtcTime.parse("2017-01-01T00:00:00Z")
    ),
)
_VALIDATION_DAYS = 731
#: 人工の固定した試行（審査の行・記録票の両方で同じ番号と割当）。
_TRIAL = 2
_ASSIGNMENT = ContentDigest.sha256("d" * 64)
_COMPILED = CompiledStrategyRef(ContentDigest.sha256("c" * 64))
_SHEET = ContentDigest.sha256("e" * 64)
#: 人工の件数（出力に現れてはいけない印。`r` が大きく `L` は下限の 364 日になる）。
_MANY_TRADES = 7919
#: `L` が人工の窓（約 405 日）より長くなる件数。
_FEW_TRADES = 37


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git(monkeypatch, tmp_path / "githome")


class _Source:
    def __init__(self, facts: FixFacts) -> None:
        self.facts = facts

    def read(self, experiment_dir: Path, execution: int) -> FixFacts:
        assert execution == self.facts.candidate.key.execution
        return self.facts


def _pin() -> FixedTrialPin:
    return FixedTrialPin(trial_index=_TRIAL, assignment_digest=_ASSIGNMENT, compiled_ref=_COMPILED)


def _facts(ws: FinalWorkspace, key: CandidateKey | None = None) -> FixFacts:
    candidate: CandidateFacts = ws.facts() if key is None else ws.facts(key=key)
    return FixFacts(candidate=candidate, record_sheet_digest=_SHEET, trials={_TRIAL: _pin()})


def _minimum(ws: FinalWorkspace) -> int:
    requirement = ws.facts().requirements.standard
    assert requirement is not None
    return requirement.min_validation_trades_total


def _expected_length(ws: FinalWorkspace, trades: int) -> int:
    from odyssey_fx.app.config.forward_validation import load_registered_forward_validation_rules

    rules = load_registered_forward_validation_rules(ws.repo, 1).rules.length
    rate = Decimal(trades) * Decimal(365) / Decimal(_VALIDATION_DAYS)
    length = length_for_rate(rate, minimum_trades=_minimum(ws), rule=rules)
    assert length is not None
    return length


def _numeric_record(ws: FinalWorkspace, key: CandidateKey, nonce: str, trades: int) -> str:
    """審査の数値の記録（人工。版管理の外）を書き、ファイルの SHA-256 を返す。"""
    place = ws.experiment_dir / "review" / nonce
    place.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "experiment_id": key.experiment_id,
        "execution": key.execution,
        "review_nonce": nonce,
        "variants": {
            "standard": {"trade_total": trades},
            "candidate": {"trade_total": trades},
        },
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (place / "review_record.json").write_text(data, encoding="utf-8")
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _write_reviews(
    ws: FinalWorkspace, keys: list[CandidateKey], *, trades: int, policy_version: int = 3
) -> None:
    entries = []
    for index, key in enumerate(keys):
        nonce = f"{index + 1:x}" * 32
        sha = _numeric_record(ws, key, nonce, trades)
        base = review_entry(
            ExperimentId(ContentDigest.sha256(key.experiment_id)),
            key.execution,
            result=ReviewResult.PROCEED,
            nonce=nonce,
        )
        fixed = FixedTrial(trial_index=_TRIAL, assignment_digest=_ASSIGNMENT)
        entries.append(
            replace(
                base,
                fixed_trials=Paired(standard=fixed, candidate=fixed),
                numeric_record_digest=ContentDigest.sha256(sha),
                research_policy_ref=replace(base.research_policy_ref, version=policy_version),
            )
        )
    path = ws.repo / "research" / "adoption_reviews.jsonl"
    path.write_bytes(b"".join(review_line_bytes(line) for line in review_lines(entries)))


def _research_registry(ws: FinalWorkspace, versions: tuple[int, ...] = (3,)) -> None:
    path = ws.repo / "configs" / "policies" / "research" / "registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        f'  - {{id: research, version: {version}, digest: "{digest("policy").hex}",'
        " purpose: STANDARD}\n"
        for version in versions
    )
    path.write_text(f"schema_version: 1\nentries:\n{rows}", encoding="utf-8")


def _trial_lines(
    ws: FinalWorkspace, names: list[str], derived: dict[str, str] | None = None
) -> dict[str, CandidateKey]:
    entries = []
    keys: dict[str, CandidateKey] = {}
    for name in names:
        source = (derived or {}).get(name)
        opening = replace(
            trial_ledger.started(name, purpose=StandardPurpose.STANDARD),
            validation_intervals=_VALIDATION,
            lineage=None
            if source is None
            else Lineage(
                derived_from=LineageSource(
                    experiment_id=trial_ledger.experiment_id(source), execution=1
                ),
                change_kinds=(ChangeKind.PARAMETERS,),
                viewed_before=_VALIDATION,
                note_digest=None,
            ),
        )
        entries.extend(
            [opening, trial_ledger.finished(opening, verdict=SearchVerdict.MEETS_STANDARD)]
        )
        keys[name] = CandidateKey(
            experiment_id=trial_ledger.experiment_id(name).hex,
            execution=1,
            experiment_name=f"exp_{name}",
            experiment_version=1,
        )
    lines = trial_ledger.chain(entries)
    trial_ledger.write_ledger(ws.repo, lines)
    ws.started_digest = lines[2 * names.index("cand")].digest.hex
    return keys


def _workspace(
    tmp_path: Path,
    *,
    trades: int = _MANY_TRADES,
    names: tuple[str, ...] = ("cand",),
    derived: dict[str, str] | None = None,
) -> tuple[FinalWorkspace, dict[str, CandidateKey]]:
    """固定の前の作業場（審査が「進める」、固定の記録はまだ無い）。`names` は審査の順。"""
    ws = build_workspace(tmp_path, review=False, fixation=False)
    _research_registry(ws)
    keys = _trial_lines(ws, list(names), derived)
    _write_reviews(ws, [keys[name] for name in names], trades=trades)
    ws.commit_and_push("reviewed: proceed")
    return ws, keys


def _fix(
    ws: FinalWorkspace,
    *,
    mode: FixMode = FixMode.CREATE,
    key: CandidateKey | None = None,
    record: Path | None = None,
    supersede: bool = False,
    runner: FakeRunner | None = None,
    now: UtcTime = _NOW,
) -> FixOutcome:
    chosen = key or ws.key
    return fix_candidate(
        FixRequest(
            experiment_dir=ws.experiment_dir,
            execution=chosen.execution,
            repo_root=ws.repo,
            snapshots_root=ws.snapshots,
            by="人工の記録者",
            mode=mode,
            supersede=supersede,
            record=record,
        ),
        source=_Source(_facts(ws, chosen)),
        runner=runner or FakeRunner(research=ws.research),
        now=lambda: now,
    )


def _reasons(outcome: FixOutcome) -> dict[int, tuple[str, ...]]:
    return {
        item.item.value: tuple(reason.value for reason in item.reasons)
        for item in outcome.outcomes
        if item.reasons
    }


#: 7 文字以上の 16 進の語（コミットの SHA・ダイジェスト）。試験用リポジトリのコミットの SHA は
#: 実行ごとに違い、件数の印（例 `37`）をたまたま含むことがあるので、数字の確かめの前に除く。
_HEX_WORD = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{7,}(?![0-9A-Za-z])")


def _assert_no_numbers(text: str) -> None:
    """`text` に取引の件数（人工の印）と `trade_total` が現れない（仮置き F1-5）。

    16 進の語（SHA・ダイジェスト）を伏せた後の文字列で、部分一致で確かめる（件数がほかの文字と
    くっついて出ても落ちる）。16 進の語の中の偶然の一致だけを数えない。
    """
    masked = _HEX_WORD.sub("<hex>", text)
    for value in (str(_MANY_TRADES), str(_FEW_TRADES)):
        assert value not in masked, f"件数の印 {value} が出力に現れた"
    assert "trade_total" not in masked


def _no_numbers(outcome: FixOutcome) -> None:
    """出力に `r` と取引の件数が現れない（仮置き F1-5）。"""
    _assert_no_numbers("\n".join([*outcome.lines, outcome.detail]))


def _merge(ws: FinalWorkspace, rel: str, when: str) -> str:
    """固定の記録を別の枝で commit し、main へ merge のコミット（時刻 `when`）で入れる。"""
    ws.git("checkout", "--quiet", "-b", "candidate-fix/cand-1")
    ws.git("add", rel)
    git(ws.repo, "commit", "--quiet", "-m", "fix record", home=ws.home, date="2025-02-20T09:00:00Z")
    ws.git("checkout", "--quiet", "main")
    git(
        ws.repo,
        "merge",
        "--quiet",
        "--no-ff",
        "-m",
        "[関所 1] merge",
        "candidate-fix/cand-1",
        home=ws.home,
        date=when,
    )
    ws.git("push", "--quiet", "origin", "main")
    return ws.git("rev-parse", "HEAD")


# --- F-5: 8 項目がすべて合格して記録を書く --------------------------------------------------


def test_f5_all_eight_items_pass_and_the_record_is_written(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    assert not (ws.repo / "research" / "declarations").exists()
    outcome = _fix(ws)
    assert (outcome.exit_code, outcome.verdict) == (EXIT_OK, FixVerdict.PASSED), outcome.lines
    assert [item.item for item in outcome.outcomes] == list(FixCheckItem)
    assert all(item.passed for item in outcome.outcomes)
    assert outcome.written and outcome.record_path is not None
    rel = outcome.record_path
    assert rel == f"research/candidate_fixes/{ws.key.experiment_id}/1/1.yaml"
    text = (ws.repo / rel).read_text(encoding="utf-8")
    record = load_candidate_fix(rel, text=text)
    assert candidate_fix_text(record) == text
    assert record.length_days == _expected_length(ws, _MANY_TRADES) == 364
    assert record.fix_no == 1 and record.supersedes is None
    assert record.fixed_trials.standard == _pin()
    assert record.record_sheet_digest == _SHEET
    assert (record.research_policy.ref_id, record.research_policy.version) == ("research", 3)
    assert (record.forward_rules.ref_id, record.forward_rules.version) == ("forward_validation", 1)
    assert record.frequency_source.material_id.startswith("review/")
    # 固定時点・開始・期間の終わりは書かない（D09 §9.11.3・§9.11.6）。
    keys = {line.split(":", 1)[0] for line in text.splitlines() if line and line[0].isalpha()}
    assert keys.isdisjoint({"t_fix", "start", "end", "warmup", "scoring_start"})
    assert any(line.startswith("項目 6") and "合格" in line for line in outcome.lines)
    _no_numbers(outcome)


def test_f5_the_dry_run_and_the_check_write_nothing(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    dry = _fix(ws, mode=FixMode.DRY_RUN)
    assert dry.exit_code == EXIT_OK and not dry.written
    assert not (ws.repo / "research" / "candidate_fixes").exists()
    made = _fix(ws)
    assert made.exit_code == EXIT_OK and made.record_path is not None
    before = (ws.repo / made.record_path).read_bytes()
    checked = _fix(ws, mode=FixMode.CHECK, record=Path(made.record_path))
    assert (checked.exit_code, checked.verdict) == (EXIT_OK, FixVerdict.PASSED), checked.lines
    assert (ws.repo / made.record_path).read_bytes() == before
    # 同じ記録を 2 度作らない（作業木にある記録も「ほかの有効な記録」として数える）。
    again = _fix(ws)
    assert again.exit_code == EXIT_REFUSED
    assert _reasons(again)[2] == (FixCheckReason.OTHER_VALID_FIX_EXISTS.value,)


# --- 出力に件数が現れないことの確かめそのもの（空の確かめでないこと・SHA との偶然の一致） ---------


#: 件数の印を含む 16 進の語（決め打ち）。12 文字は出力の「origin の既定ブランチ」の行の短い SHA、
#: 40 文字はコミットの SHA、64 文字はダイジェストの長さ。数字だけの SHA も作る。
_LUCKY_SHAS = (
    f"{_FEW_TRADES}{_MANY_TRADES}a{_FEW_TRADES}b0c",
    f"{_FEW_TRADES}{_MANY_TRADES}000{_FEW_TRADES}0",
    f"ab{_FEW_TRADES}" + "0" * 31 + f"{_MANY_TRADES}c",
    f"{_FEW_TRADES}{_MANY_TRADES}" * 10 + "abcd",
)


@pytest.mark.parametrize(
    "leak",
    [
        f"件数: {_MANY_TRADES}",
        f"取引 {_FEW_TRADES}件",
        f"r={_FEW_TRADES}",
        f"({_MANY_TRADES})",
        f"origin の既定ブランチ: main（{_LUCKY_SHAS[0]}）、件数 {_FEW_TRADES}",
        '{"trade_total": 1}',
    ],
)
def test_the_no_numbers_check_fails_when_a_count_is_shown(tmp_path: Path, leak: str) -> None:
    """件数が出力に出たら確かめは落ちる（SHA と同じ行に出ても）。実際の出力に足して確かめる。"""
    ws, _ = _workspace(tmp_path)
    outcome = _fix(ws)
    _no_numbers(outcome)
    with pytest.raises(AssertionError):
        _no_numbers(replace(outcome, lines=[*outcome.lines, leak]))
    with pytest.raises(AssertionError):
        _no_numbers(replace(outcome, detail=leak))


def test_a_sha_that_happens_to_contain_the_counts_does_not_fail_the_check(tmp_path: Path) -> None:
    """SHA・ダイジェストが件数の印と偶然一致しても落ちない（不安定だった確かめの決め打ちの再現）。"""
    ws, _ = _workspace(tmp_path)
    outcome = _fix(ws)
    first = outcome.lines[0]
    assert first.startswith("origin の既定ブランチ: main（") and first.endswith("）")
    for sha in _LUCKY_SHAS:
        assert str(_FEW_TRADES) in sha and str(_MANY_TRADES) in sha
        lines = [f"origin の既定ブランチ: main（{sha}）", *outcome.lines[1:], f"commit {sha}"]
        _no_numbers(replace(outcome, lines=lines, detail=f"sha256:{sha}"))


# --- F-4: 固定時点は main の first-parent の履歴から -------------------------------------------


def test_f4_t_fix_is_the_committer_time_of_the_first_parent_merge_commit(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    made = _fix(ws)
    assert made.record_path is not None
    merge = _merge(ws, made.record_path, "2025-02-21T12:34:56Z")
    shown = show_fix(experiment_id=ws.key.experiment_id, execution=1, repo_root=ws.repo)
    assert shown.exit_code == EXIT_OK, shown.detail
    assert shown.fix is not None
    assert shown.fix.commit == merge
    # 枝のコミット（2025-02-20）ではなく、main へ入れた merge のコミットの時刻。
    assert shown.fix.t_fix == UtcTime.parse("2025-02-21T12:34:56Z")
    period = forward_period(shown.fix.t_fix, shown.fix.length_days)
    assert shown.fix.forward_period == period
    assert period.start == UtcTime.parse("2025-02-23T22:00:00Z")
    assert f"前向きの期間: [{period.start}, {period.end})" in shown.lines


def test_f4_a_record_changed_after_it_entered_main_is_refused(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    made = _fix(ws)
    assert made.record_path is not None
    ws.commit_and_push("fix record", date=UtcTime.parse("2025-02-21T00:00:00Z"))
    path = ws.repo / made.record_path
    path.write_text(path.read_text(encoding="utf-8").replace("人工の記録者", "別の記録者"))
    ws.commit_and_push("edited the fix record")
    shown = show_fix(experiment_id=ws.key.experiment_id, execution=1, repo_root=ws.repo)
    assert shown.exit_code == EXIT_READ_ERROR
    assert "changed on the default branch" in shown.detail


def test_f4_without_a_record_on_origin_show_says_not_fixed(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    made = _fix(ws)
    assert made.written
    # 作業木にあっても origin に無ければ固定されていない（merge が固定時点）。
    shown = show_fix(experiment_id=ws.key.experiment_id, execution=1, repo_root=ws.repo)
    assert shown.exit_code == EXIT_REFUSED and shown.fix is None


# --- F-5: 項目ごとの外れ -------------------------------------------------------------------------


def test_f5_item_1_without_a_proceed_row_nothing_is_built(tmp_path: Path) -> None:
    ws, keys = _workspace(tmp_path)
    other = CandidateKey(
        experiment_id="9" * 64, execution=1, experiment_name="other", experiment_version=1
    )
    _write_reviews(ws, [other], trades=_MANY_TRADES)
    ws.commit_and_push("only another candidate was reviewed")
    outcome = _fix(ws)
    assert (outcome.exit_code, outcome.verdict) == (EXIT_REFUSED, FixVerdict.REFUSED)
    assert _reasons(outcome) == {1: (FixCheckReason.REVIEW_NOT_PROCEEDED.value,)}
    assert not (ws.repo / "research" / "candidate_fixes").exists()


def test_f5_item_1_trials_of_the_record_must_match_the_review(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    made = _fix(ws, mode=FixMode.CREATE)
    assert made.record_path is not None
    path = ws.repo / made.record_path
    text = path.read_text(encoding="utf-8").replace("trial_index: 2", "trial_index: 3", 1)
    path.write_text(text, encoding="utf-8")
    checked = _fix(ws, mode=FixMode.CHECK, record=Path(made.record_path))
    assert checked.exit_code == EXIT_REFUSED
    assert FixCheckReason.REVIEW_TRIALS_MISMATCH.value in _reasons(checked)[1]
    assert FixCheckReason.DIGEST_MISMATCH.value in _reasons(checked)[8]


def test_f5_item_2_supersede_only_before_the_final_application(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    first = _fix(ws)
    assert first.record_path is not None
    ws.commit_and_push("fix record 1", date=UtcTime.parse("2025-02-21T00:00:00Z"))
    refused = _fix(ws)
    assert _reasons(refused) == {2: (FixCheckReason.OTHER_VALID_FIX_EXISTS.value,)}
    replaced = _fix(ws, mode=FixMode.DRY_RUN, supersede=True)
    assert replaced.exit_code == EXIT_OK, replaced.lines
    assert replaced.record_path == f"research/candidate_fixes/{ws.key.experiment_id}/1/2.yaml"
    # 最終検証を申請した後（台帳の行がある）は、同じ実行の置き換えを許さない。
    append_ledger_row(
        ws.repo,
        {
            "result": "WITHDRAWN",
            "candidate": candidate_payload(ws.key),
            "permit_id": "",
            "t_fix": "",
            "consumption_start": "",
            "consumption_end": "",
            "partition_count": 0,
            "partitions_digest": "",
            "consumption_commit": "",
            "combined_verdict": "",
            "resume_count": 0,
            "resume_failures": [],
            "prior_final_validations": 0,
            "reason": "WITHDRAWN",
            "final_evaluation_sha256": "",
            "declaration_sha256": "",
            "recorded_at": "2025-02-22T00:00:00Z",
            "actor": "人工の実行者",
        },
    )
    applied = _fix(ws, mode=FixMode.DRY_RUN, supersede=True)
    assert applied.exit_code == EXIT_REFUSED
    assert _reasons(applied)[2] == (FixCheckReason.SUPERSEDED_ALREADY_APPLIED.value,)


def _withdrawn_row(key: CandidateKey) -> dict[str, object]:
    return {
        "result": "WITHDRAWN",
        "candidate": candidate_payload(key),
        "permit_id": "",
        "t_fix": "",
        "consumption_start": "",
        "consumption_end": "",
        "partition_count": 0,
        "partitions_digest": "",
        "consumption_commit": "",
        "combined_verdict": "",
        "resume_count": 0,
        "resume_failures": [],
        "prior_final_validations": 0,
        "reason": "WITHDRAWN",
        "final_evaluation_sha256": "",
        "declaration_sha256": "",
        "recorded_at": "2025-02-22T00:00:00Z",
        "actor": "人工の実行者",
    }


def test_f5_item_2_a_new_version_supersedes_the_fix_of_its_lineage(tmp_path: Path) -> None:
    """別の版（別の実行）の固定は、系譜の元の実行の有効な固定の記録を置き換える。

    D09 §9.11.9 の 2。
    """
    ws, keys = _workspace(tmp_path, names=("orig", "cand"), derived={"cand": "orig"})
    original = _fix(ws, key=keys["orig"], now=UtcTime.parse("2025-01-20T00:00:00Z"))
    assert original.written, original.lines
    ws.commit_and_push("orig fixed", date=UtcTime.parse("2025-01-20T00:00:00Z"))
    # 元の版は最終検証の前に取り下げた（予約は解かれ、順番では「終わった」）。
    append_ledger_row(ws.repo, _withdrawn_row(keys["orig"]))
    ws.commit_and_push("orig withdrawn")
    made = _fix(ws)
    assert made.exit_code == EXIT_OK, made.lines
    assert made.record_path is not None
    record = load_candidate_fix(
        made.record_path, text=(ws.repo / made.record_path).read_text(encoding="utf-8")
    )
    assert record.fix_no == 1
    assert record.supersedes == CandidateFixId(
        experiment_id=ExperimentId(ContentDigest.sha256(keys["orig"].experiment_id)),
        execution=1,
        fix_no=1,
    )
    assert record.derived_from is not None
    assert record.derived_from.experiment_id.hex == keys["orig"].experiment_id
    # 置き換えの元を書かない記録は項目 2 で外れる。
    path = ws.repo / made.record_path
    text = path.read_text(encoding="utf-8")
    start = text.index("supersedes:\n")
    end = text.index("fixed_by:")
    path.write_text(text[:start] + "supersedes: null\n" + text[end:], encoding="utf-8")
    checked = _fix(ws, mode=FixMode.CHECK, record=Path(made.record_path))
    assert _reasons(checked) == {2: (FixCheckReason.SUPERSEDES_MISMATCH.value,)}


def test_f5_item_3_the_forward_rules_must_be_registered_current_and_on_origin(
    tmp_path: Path,
) -> None:
    ws, _ = _workspace(tmp_path)
    made = _fix(ws)
    assert made.record_path is not None
    path = ws.repo / made.record_path
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "  id: forward_validation\n  version: 1\n", "  id: forward_validation\n  version: 2\n"
        ),
        encoding="utf-8",
    )
    checked = _fix(ws, mode=FixMode.CHECK, record=Path(made.record_path))
    assert FixCheckReason.FORWARD_RULES_NOT_REGISTERED.value in _reasons(checked)[3]
    path.write_text(text, encoding="utf-8")
    # 作業木の登録簿が origin と違う（push されていない）。
    registry = ws.repo / "configs" / "policies" / "forward_validation" / "registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8") + "# 手元だけの書き足し\n")
    local = _fix(ws, mode=FixMode.CHECK, record=Path(made.record_path))
    assert _reasons(local)[3] == (FixCheckReason.INPUT_NOT_ON_ORIGIN.value,)


def test_f5_item_4_the_research_policy_must_be_the_current_version(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    _research_registry(ws, versions=(3, 4))
    ws.commit_and_push("research policy version 4 registered")
    outcome = _fix(ws)
    assert outcome.exit_code == EXIT_REFUSED
    assert _reasons(outcome) == {4: (FixCheckReason.RESEARCH_POLICY_NOT_CURRENT.value,)}
    assert not outcome.written


def test_f5_item_5_zero_frequency_stops_and_a_wrong_length_is_refused(tmp_path: Path) -> None:
    zero, _ = _workspace(tmp_path / "zero", trades=0)
    outcome = _fix(zero)
    assert outcome.exit_code == EXIT_REFUSED
    assert _reasons(outcome) == {5: (FixCheckReason.FREQUENCY_ZERO.value,)}
    ws, _ = _workspace(tmp_path / "wrong")
    made = _fix(ws)
    assert made.record_path is not None
    path = ws.repo / made.record_path
    path.write_text(
        path.read_text(encoding="utf-8").replace("length_days: 364", "length_days: 365"),
        encoding="utf-8",
    )
    checked = _fix(ws, mode=FixMode.CHECK, record=Path(made.record_path))
    assert _reasons(checked)[5] == (FixCheckReason.LENGTH_MISMATCH.value,)
    assert "規則で計算した L: 364 日" in "\n".join(checked.lines)
    # 審査の数値の記録が審査の行のダイジェストと違えば `r` は求まらない。
    record = next((ws.experiment_dir / "review").glob("*/review_record.json"))
    record.write_text(record.read_text(encoding="utf-8") + " ", encoding="utf-8")
    broken = _fix(ws, mode=FixMode.DRY_RUN, key=ws.key)
    assert FixCheckReason.FREQUENCY_UNAVAILABLE.value in _reasons(broken)[5]


def test_f5_item_6_a_short_window_defers_the_fix(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path, trades=_FEW_TRADES)
    expected = _expected_length(ws, _FEW_TRADES)
    assert expected > 405
    outcome = _fix(ws)
    assert (outcome.exit_code, outcome.verdict) == (EXIT_DEFERRED, FixVerdict.DEFERRED)
    assert _reasons(outcome) == {6: (FixCheckReason.WINDOW_SHORTER_THAN_L.value,)}
    assert not outcome.written
    assert not (ws.repo / "research" / "candidate_fixes").exists()
    assert any("固定を延期する" in line for line in outcome.lines)
    _no_numbers(outcome)
    # 窓が伸びた後（次の定期取得の承認の後）は通る: 人工の時刻を 2 年先にしても partition が無い
    # 週は窓に入らないので、ここでは延期のまま（窓は承認済みの partition だけで数える）。
    later = _fix(ws, now=UtcTime.parse("2027-03-01T00:00:00Z"))
    assert later.exit_code == EXIT_DEFERRED


def test_f5_item_6_an_earlier_pending_candidate_defers_the_fix(tmp_path: Path) -> None:
    ws, keys = _workspace(tmp_path, names=("early", "cand"))
    assert keys["cand"] == ws.key
    outcome = _fix(ws)
    assert outcome.exit_code == EXIT_DEFERRED
    assert _reasons(outcome) == {6: (FixCheckReason.EARLIER_CANDIDATE_PENDING.value,)}


def test_f5_item_6_and_7_another_candidates_reservation(tmp_path: Path) -> None:
    ws, keys = _workspace(tmp_path, names=("early", "cand"))
    early = keys["early"]
    # 先の候補を固定して main に入れ（固定時点 2025-01-20）、最終検証を「満たす（両組）」で終えた
    # ことにする（予約は続く。決定 74）。
    made = _fix(ws, key=early, now=UtcTime.parse("2025-01-20T00:00:00Z"))
    assert made.written, made.lines
    ws.commit_and_push("early fixed", date=UtcTime.parse("2025-01-20T00:00:00Z"))
    row = {
        "result": "COMPLETED",
        "candidate": candidate_payload(early),
        "permit_id": "p" * 32,
        "t_fix": "2025-01-20T00:00:00Z",
        "consumption_start": "2024-01-01T00:00:00Z",
        "consumption_end": "2025-01-12T22:00:00Z",
        "partition_count": 1,
        "partitions_digest": "0" * 64,
        "consumption_commit": "0" * 40,
        "combined_verdict": SearchVerdict.MEETS_STANDARD.value,
        "resume_count": 0,
        "resume_failures": [],
        "prior_final_validations": 0,
        "reason": "",
        "final_evaluation_sha256": "0" * 64,
        "declaration_sha256": "",
        "recorded_at": "2025-01-25T00:00:00Z",
        "actor": "人工の実行者",
    }
    append_ledger_row(ws.repo, row)
    ws.commit_and_push("early validated")
    outcome = _fix(ws)
    assert outcome.exit_code == EXIT_REFUSED, outcome.lines
    reasons = _reasons(outcome)
    assert reasons[7] == (FixCheckReason.RESERVATION_NOT_ENDED.value,)
    # 予約した期間 [2025-01-26, …) は窓から除かれ、使える窓はその前で終わる。
    assert 6 not in reasons
    assert any("2025-01-26T22:00:00Z)" in line for line in outcome.lines)


def test_f5_item_8_a_tampered_or_unreadable_record_is_refused(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    made = _fix(ws)
    assert made.record_path is not None
    path = ws.repo / made.record_path
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(_SHEET.hex, "7" * 64), encoding="utf-8")
    tampered = _fix(ws, mode=FixMode.CHECK, record=Path(made.record_path))
    assert _reasons(tampered) == {8: (FixCheckReason.DIGEST_MISMATCH.value,)}
    path.write_text(text + "unknown_key: 1\n", encoding="utf-8")
    unreadable = _fix(ws, mode=FixMode.CHECK, record=Path(made.record_path))
    assert unreadable.exit_code == EXIT_REFUSED
    assert _reasons(unreadable) == {8: (FixCheckReason.RECORD_UNREADABLE.value,)}


def test_f5_unreachable_origin_and_bad_arguments_are_read_errors(tmp_path: Path) -> None:
    ws, _ = _workspace(tmp_path)
    ws.git("remote", "set-url", "origin", str(tmp_path / "missing.git"))
    outcome = _fix(ws)
    assert outcome.exit_code == EXIT_READ_ERROR and outcome.verdict is None
    assert not (ws.repo / "research" / "candidate_fixes").exists()


# --- CLI ---------------------------------------------------------------------------------


def test_the_cli_reports_read_errors_and_shows_a_fixed_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws, _ = _workspace(tmp_path)
    # 本番の記録票の読み口は、記録票の無い版のディレクトリを読込の誤りにする（終了コード 2）。
    code = main(
        [
            "experiment",
            "fix",
            "--experiment-dir",
            str(ws.experiment_dir),
            "--execution",
            "1",
            "--repo-root",
            str(ws.repo),
            "--by",
            "人工の記録者",
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_READ_ERROR
    assert "何も書いていない" in out and "終了コード: 2" in out
    made = _fix(ws)
    assert made.record_path is not None
    ws.commit_and_push("fix record", date=UtcTime.parse("2025-02-21T00:00:00Z"))
    code = main(
        [
            "experiment",
            "fix-show",
            "--experiment-id",
            ws.key.experiment_id,
            "--execution",
            "1",
            "--repo-root",
            str(ws.repo),
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "固定時点 T_fix: 2025-02-21T00:00:00Z" in out
    assert "開始 S: 2025-02-23T22:00:00Z" in out
