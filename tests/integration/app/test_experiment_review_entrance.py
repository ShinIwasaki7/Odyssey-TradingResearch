"""`odyssey-fx experiment review` の入口と読込の誤り（D16 §3.1・§11.2・§12。T2・T15、E2 の (3)）。

入口の検査は試行台帳・研究ポリシーの登録簿・審査台帳・審査の規則のファイルと登録簿だけを読む
（仮置き 13）ので、`runs/` の成果物の無いリポジトリで確かめられる。

- **順 5 の H2 の版 1・版 2**（版管理の `research/trial_ledger.jsonl`。D16 §12）: 終了コード 7、
  理由の識別子 `ENTRY_VERDICT_NOT_MEETS_STANDARD`、審査台帳が変わらない、`--out` の下に何も
  作られない。
  **判定の値そのものは読まない・出さない**（仮置き 14）。確かめるのは終了コードと理由の識別子だけ。
- **順 7 の残課題 R5**（人間の決定 40 の帰結。D17 §3.3、D15 §6 の E2 の注記）: H2 の版 1・版 2 は
  研究ポリシー第 3 版を指し、第 4 版の登録で第 3 版は「現行の版」でなくなった。入口の検査は順 5
  （判定）を順 6（現行の版）より先に当てるので、実際の拒否の理由は順 5 のまま変わらない。
  順 6 でも止まることは、H2 の**実際の開始の行**に**人工の結末の行**（判定を
  `MEETS_STANDARD` に置いたもの）をつないだ台帳で確かめる（実際の判定の値は読まない）。
- 人工の試行台帳での入口の各検査の不合格（順 1〜8）と、読込の誤り（終了コード 2）。
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config.adoption_review import (
    adoption_review_registry_path,
    load_adoption_review_registry,
)
from odyssey_fx.app.config.research_policy import (
    load_research_policy_registry,
    research_policy_registry_path,
)
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import ContentDigest, PolicyRef
from odyssey_fx.evaluation.adapters.fs_store import TRIAL_LEDGER_PATH, read_trial_ledger_file
from odyssey_fx.evaluation.adapters.review_store import (
    ADOPTION_REVIEW_LEDGER_PATH,
    review_line_bytes,
)
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.domain.adoption_review import (
    EntranceAccepted,
    EntranceRejected,
    ReviewReason,
    ReviewResult,
    check_entrance,
)
from odyssey_fx.evaluation.domain.experiment import ExperimentStatus
from odyssey_fx.evaluation.domain.research_policy import RegistryEntry, current_standard_version
from odyssey_fx.evaluation.domain.search import (
    SearchVerdict,
    StandardPurpose,
    TrialLedgerEntry,
    TrialLedgerLine,
    started_line_of,
)
from tests.fixtures.evaluation.adoption_review import (
    REPO_ROOT,
    install_version_1_rules,
    review_entry,
    review_lines,
)
from tests.fixtures.evaluation.trial_ledger import (
    chain,
    experiment_id,
    finished,
    started,
    write_ledger,
)

#: 順 5 の H2 の版 1・版 2 の実験の識別子（D16 §12。実行番号は 1）。
H2_EXECUTIONS = (
    "9c95335880803f535800f919d4a8d84b4e5291a808941e6c0506a0d6b92efc25",
    "4d6e2b04dadc51f9464e05aef1af95a60ba02e14f47ff2870eaec016576da27b",
)

_POLICY = PolicyRef(policy_kind="research", policy_id="research", version=3, digest=digest("p3"))


def _cli(args: Sequence[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["experiment", "review", *args])
    return code, stdout.getvalue(), stderr.getvalue()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(path: Path) -> list[str]:
    return sorted(str(item.relative_to(path)) for item in path.rglob("*"))


@pytest.mark.parametrize("experiment", H2_EXECUTIONS)
def test_the_step5_h2_executions_do_not_proceed(experiment: str, tmp_path: Path) -> None:
    """E2 の (3): 候補でない実験は「進めない」と機械的に拒否され、審査記録を書かない。"""
    ledger = REPO_ROOT / ADOPTION_REVIEW_LEDGER_PATH
    before = _sha(ledger)
    out = tmp_path / "out"
    out.mkdir()
    code, stdout, _ = _cli(
        [
            "--experiment-id",
            experiment,
            "--execution",
            "1",
            "--repo-root",
            str(REPO_ROOT),
            "--out",
            str(out),
        ]
    )
    assert code == 7
    assert "理由の識別子: ENTRY_VERDICT_NOT_MEETS_STANDARD" in stdout
    assert _sha(ledger) == before
    assert _tree(out) == []


# --- 順 7 の残課題 R5（決定 40 の帰結）---------------------------------------------------


def _h2_started(experiment: str) -> TrialLedgerEntry:
    """版管理の試行台帳から H2 の開始の行だけを取り出す（結末の行と判定の値は読まない）。"""
    contents = read_trial_ledger_file(REPO_ROOT / TRIAL_LEDGER_PATH)
    assert isinstance(contents, TrialLedgerContents)
    line = started_line_of(contents.lines, ExperimentId(ContentDigest.sha256(experiment)), 1)
    assert line is not None
    return line.entry


@pytest.mark.parametrize("experiment", H2_EXECUTIONS)
def test_r5_the_step5_h2_executions_also_point_at_a_policy_version_that_is_not_current(
    experiment: str,
) -> None:
    """R5: H2 は第 3 版を指し、版管理の登録簿の「現行の版」は第 4 版（決定 40）。

    判定を `MEETS_STANDARD` に置いた人工の結末の行でも、入口の順 6 で
    `ENTRY_POLICY_NOT_CURRENT` により止まる。第 4 版の要素を除いた登録簿では順 6 を通る
    （止まる原因が第 4 版の登録であることの対照）。実際の拒否の理由（順 5）は
    `test_the_step5_h2_executions_do_not_proceed` が確かめる。
    """
    opening = _h2_started(experiment)
    refs = {basis.research_policy_ref for _, basis in opening.basis.items()}
    assert {(ref.policy_id, ref.version) for ref in refs} == {("research_policy", 3)}
    registry = load_research_policy_registry(research_policy_registry_path(REPO_ROOT))
    assert current_standard_version(registry, "research_policy") == 4
    lines = chain([opening, finished(opening, verdict=SearchVerdict.MEETS_STANDARD)])
    rules = load_adoption_review_registry(adoption_review_registry_path(REPO_ROOT))
    target = ExperimentId(ContentDigest.sha256(experiment))

    def entrance(entries: Sequence[RegistryEntry]) -> EntranceAccepted | EntranceRejected:
        return check_entrance(
            trial_lines=lines,
            policy_registry=entries,
            review_lines=(),
            rules_registry=tuple(rules)[:1],  # 版 2 は実台帳の行に結びつく（R1-10 (a)）
            experiment_id=target,
            execution=1,
        )

    assert entrance(registry) == EntranceRejected(ReviewReason.ENTRY_POLICY_NOT_CURRENT)
    without_v4 = tuple(item for item in registry if item.version != 4)
    assert isinstance(entrance(without_v4), EntranceAccepted)


# --- 人工の試行台帳での入口（T2）-------------------------------------------------------


def _repo(tmp_path: Path, lines: Sequence[TrialLedgerLine]) -> Path:
    repo = tmp_path / "repo"
    write_ledger(repo, lines)
    registry = repo / "configs/policies/research/registry.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "schema_version: 1\nentries:\n"
        f'  - {{id: research, version: 3, digest: "{_POLICY.digest.hex}", purpose: STANDARD}}\n',
        encoding="utf-8",
    )
    install_version_1_rules(repo)
    (repo / ADOPTION_REVIEW_LEDGER_PATH).write_bytes(b"")
    return repo


def _standard(entry: TrialLedgerEntry) -> TrialLedgerEntry:
    return replace(
        entry, basis=entry.basis.map(lambda _, item: replace(item, research_policy_ref=_POLICY))
    )


def _candidate(verdict: SearchVerdict = SearchVerdict.MEETS_STANDARD) -> list[TrialLedgerLine]:
    opening = _standard(started("a", purpose=StandardPurpose.STANDARD))
    return chain([opening, finished(opening, verdict=verdict)])


def _review(repo: Path, out: Path, name: str = "a") -> tuple[int, str, str]:
    return _cli(
        [
            "--experiment-id",
            str(experiment_id(name)),
            "--execution",
            "1",
            "--repo-root",
            str(repo),
            "--experiment-dir",
            str(out / "runs/experiments/never/v1"),
            "--snapshots",
            str(out / "snapshots"),
            "--out",
            str(out),
        ]
    )


def _rejected(repo: Path, tmp_path: Path, reason: str, name: str = "a") -> None:
    ledger = repo / ADOPTION_REVIEW_LEDGER_PATH
    before = ledger.read_bytes()
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    code, stdout, _ = _review(repo, out, name)
    assert code == 7, stdout
    assert f"理由の識別子: {reason}" in stdout
    assert "審査記録: 書いていない" in stdout
    assert ledger.read_bytes() == before  # 記録を書かない（D15 の仮置き 7）
    assert _tree(out) == []  # `runs/` を開かず、何も作らない（仮置き 13）


def test_order_1_an_execution_absent_from_the_ledger(tmp_path: Path) -> None:
    _rejected(_repo(tmp_path, _candidate()), tmp_path, "ENTRY_NOT_IN_LEDGER", name="zzz")


def test_order_2_an_execution_that_did_not_finish(tmp_path: Path) -> None:
    _rejected(_repo(tmp_path, _candidate()[:1]), tmp_path, "ENTRY_NOT_FINISHED")


def test_order_3_an_execution_that_failed_its_post_run_check(tmp_path: Path) -> None:
    opening = _standard(started("a", purpose=StandardPurpose.STANDARD))
    closing = finished(
        opening, verdict=SearchVerdict.BELOW_STANDARD, status=ExperimentStatus.FAILED_POST_RUN_CHECK
    )
    _rejected(_repo(tmp_path, chain([opening, closing])), tmp_path, "ENTRY_STATUS_NOT_COMPLETED")


def test_order_4_a_mechanism_check(tmp_path: Path) -> None:
    opening = _standard(started("a", purpose=StandardPurpose.MECHANISM_CHECK))
    closing = finished(opening, verdict=SearchVerdict.MET_IN_MECHANISM_CHECK)
    _rejected(_repo(tmp_path, chain([opening, closing])), tmp_path, "ENTRY_PURPOSE_NOT_STANDARD")


def test_order_5_an_execution_below_the_standard(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate(SearchVerdict.INSUFFICIENT_EVIDENCE))
    _rejected(repo, tmp_path, "ENTRY_VERDICT_NOT_MEETS_STANDARD")


def test_order_6_a_verdict_under_an_old_policy_version(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate())
    registry = repo / "configs/policies/research/registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8")
        + f'  - {{id: research, version: 4, digest: "{"4" * 64}", purpose: STANDARD}}\n',
        encoding="utf-8",
    )
    _rejected(repo, tmp_path, "ENTRY_POLICY_NOT_CURRENT")


def test_order_7_an_execution_already_reviewed(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate())
    (entry,) = load_adoption_review_registry(adoption_review_registry_path(repo))
    lines = review_lines(
        [review_entry(experiment_id("a"), result=ReviewResult.DO_NOT_PROCEED, ref=entry.ref)]
    )
    (repo / ADOPTION_REVIEW_LEDGER_PATH).write_bytes(review_line_bytes(lines[0]))
    _rejected(repo, tmp_path, "ENTRY_ALREADY_REVIEWED")


def test_order_8_a_rules_file_changed_without_a_new_version(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate())
    rules = repo / "configs/policies/adoption_review/adoption_review_v1.yaml"
    rules.write_text(
        rules.read_text(encoding="utf-8").replace('max_share: "0.25"', 'max_share: "0.5"'),
        encoding="utf-8",
    )
    _rejected(repo, tmp_path, "ENTRY_RULES_VERSION_CHANGED")


# --- 読込の誤り（T15。終了コード 2、何も書かない）-----------------------------------------


def _read_error(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    ledger = repo / ADOPTION_REVIEW_LEDGER_PATH
    before = ledger.read_bytes() if ledger.exists() else None
    code, _, stderr = _review(repo, out)
    assert code == 2, stderr
    assert stderr.startswith("失敗: ")
    assert (ledger.read_bytes() if ledger.exists() else None) == before
    assert _tree(out) == []


def test_a_broken_trial_ledger_chain_is_a_read_error(tmp_path: Path) -> None:
    first, second = _candidate()
    orphan = TrialLedgerLine.of(second.entry, None)
    _read_error(_repo(tmp_path, [first, orphan]), tmp_path)


def test_a_missing_review_ledger_is_a_read_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate())
    (repo / ADOPTION_REVIEW_LEDGER_PATH).unlink()
    _read_error(repo, tmp_path)


def test_a_corrupt_review_ledger_is_a_read_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate())
    (repo / ADOPTION_REVIEW_LEDGER_PATH).write_bytes(b"{}\n")
    _read_error(repo, tmp_path)


def test_a_missing_rules_registry_is_a_read_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate())
    (repo / "configs/policies/adoption_review/registry.yaml").unlink()
    _read_error(repo, tmp_path)


def test_a_missing_rules_file_of_the_applied_version_is_a_read_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate())
    (repo / "configs/policies/adoption_review/adoption_review_v1.yaml").unlink()
    _read_error(repo, tmp_path)


def test_an_experiment_id_that_is_not_a_digest_is_a_read_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _candidate())
    code, _, stderr = _cli(["--experiment-id", "xyz", "--execution", "1", "--repo-root", str(repo)])
    assert code == 2, stderr
