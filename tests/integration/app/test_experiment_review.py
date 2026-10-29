"""`odyssey-fx experiment review` を人工データの探索の実験で最後まで通す（D16 §13。T1〜T7c ほか）。

T02 の人工データを受け入れて承認した作業場（`tests/fixtures/acceptance/t02_workspace.py`）に、
試験用の研究ポリシー版 3（用途は共通基準の判定。期間分割は選定 2 日・検証 2 日の 5 fold、証拠の
要件は区分 `ALL` で検証の合計の最低件数 1。**テスト用であり研究ポリシーではない**）・空の
試行台帳と審査台帳・
版管理の審査の規則（版 1 と登録簿の写し）を置き、検証戦略 B の 2 試行の探索の実験を 1 回走らせる。

人工データの判定は「満たす」にならないので、試行台帳の結末の行の判定だけを人工の値
`MEETS_STANDARD`（両組。頻度区分 `ALL`）に書き換えた台帳を作る（開始の行は変えないので、成果物の
束縛の記録はそのまま合う）。審査の道具はこの台帳と成果物から審査する。

確かめること（数値は読まない。終了コード・理由の識別子・審査台帳の行の項目の有無・ハッシュだけ）:

- T1: 進める（終了コード 0）。審査台帳に 1 行、行に台帳の累計・割り引いた指標の状態・適合の結果・
  証拠の最低線の合否がある。数値の記録の SHA-256 が行の値と一致。標準出力に数値が無い。
- T5: 申告あり → 進めない（`OUTSIDE_VIEWING_DECLARED`）。T6: 申告ファイルが無い・固定した試行の
  run が失敗 → 保留（終了コード 8）。T7: 進めるの後の審査は `ENTRY_ALREADY_REVIEWED`。
- T7b: 保留の後のやり直しは最初の行の規則の版（版 1）が当たり、版 1 のファイルを書き換えると
  `ENTRY_RULES_VERSION_CHANGED`。T7c: 版 2 の登録より前に開始した実行には版 1、後の実行には版 2。
- T10・T16: 追記を断られると終了コード 1・行なし、やり直しは新しい置き場に書き前の置き場を変えない。
  完全な行を書いた後の同期の失敗は記録あり。T11: 別のディレクトリは `ARTIFACT_BINDING_MISMATCH`。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
from collections.abc import Iterator, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config.adoption_review import load_adoption_review_rules
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.evaluation.adapters import review_store
from odyssey_fx.evaluation.adapters.fs_store import (
    TRIAL_LEDGER_PATH,
    ledger_line_bytes,
    read_trial_ledger_file,
)
from odyssey_fx.evaluation.adapters.review_store import (
    ADOPTION_REVIEW_LEDGER_PATH,
    ADOPTION_REVIEW_LOCK_PATH,
    REVIEW_RECORD_FILE,
    ReviewLedgerContents,
    read_review_ledger_file,
)
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.domain.adoption_review import (
    AdoptionReviewEntry,
    FloorOutcome,
    ReviewResult,
)
from odyssey_fx.evaluation.domain.search import (
    EvidenceAxis,
    LedgerResults,
    ProfitabilityAxis,
    SearchVerdict,
    TrialLedgerEvent,
    TrialLedgerLine,
)
from tests.fixtures.acceptance.t02_workspace import (
    T02Workspace,
    build_workspace,
    install_candidate,
)
from tests.fixtures.evaluation.adoption_review import REPO_ROOT
from tests.fixtures.evaluation.research_policies import (
    TEST_BASIS,
    append_registry_entry,
    policy_v3_text,
    write_policy,
)
from tests.fixtures.evaluation.search_experiments import TWO_FOLD_SPLIT, write_search_experiment

#: 選定 2 日・検証 2 日の 5 fold（試験用）。人工データの完了取引は 2015-01-07 にあり、fold 0 の
#: 検証区間（2015-01-06T22:00Z〜2015-01-08T22:00Z）に入る。
_SPLIT = TWO_FOLD_SPLIT.replace('train_length: "4d"', 'train_length: "2d"').replace(
    'validation_length: "4d"', 'validation_length: "2d"'
)
#: 証拠の要件（試験用）: 区分 `ALL` だけ、検証の合計の最低件数 1。
_SUFFICIENCY = (
    "  sufficiency:\n    classes:\n"
    '      - {name: ALL, min_train_trades_per_365d: "0", min_validation_trades_per_fold: 0,'
    " min_validation_trades_total: 1}"
)
_AXES = "    - {instance: m15_ema, parameter: period, values: [10, 20]}"
_RULES = Path("configs/policies/adoption_review")
_DECIMAL = re.compile(r"\d\.\d")


@dataclass(frozen=True, slots=True)
class _Env:
    workspace: T02Workspace
    out: Path
    directory: Path
    experiment: Path
    experiment_id: ExperimentId
    ledger: bytes


def _cli(args: Sequence[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


def _trial_lines(repo: Path) -> tuple[TrialLedgerLine, ...]:
    contents = read_trial_ledger_file(repo / TRIAL_LEDGER_PATH)
    assert isinstance(contents, TrialLedgerContents), contents
    return contents.lines


def _promote(repo: Path, experiment_id: ExperimentId, execution: int) -> None:
    """その実行の結末の行（台帳の最後の行）の判定を人工の `MEETS_STANDARD` にする。"""
    lines = list(_trial_lines(repo))
    last = lines[-1].entry
    assert (last.experiment_id, last.execution, last.event) == (
        experiment_id,
        execution,
        TrialLedgerEvent.FINISHED,
    )
    assert last.results is not None
    variants = last.results.variants.map(
        lambda _, item: replace(
            item,
            verdict=SearchVerdict.MEETS_STANDARD,
            frequency_class="ALL",
            profitability=ProfitabilityAxis.MET,
            evidence=EvidenceAxis.SUFFICIENT,
            hold=None,
        )
    )
    entry = replace(
        last,
        verdict=SearchVerdict.MEETS_STANDARD,
        results=LedgerResults(split=False, hold=None, variants=variants),
    )
    lines[-1] = TrialLedgerLine.of(entry, lines[-1].prev)
    (repo / TRIAL_LEDGER_PATH).write_bytes(b"".join(ledger_line_bytes(line) for line in lines))


def _run(workspace: T02Workspace, experiment: Path, out: Path) -> None:
    code, stdout, stderr = _cli(
        [
            "experiment",
            "run",
            "--experiment",
            str(experiment),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(workspace.repo),
        ]
    )
    assert code == 0, stdout + stderr


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> _Env:
    root = tmp_path_factory.mktemp("t02-review")
    workspace = build_workspace(root)
    candidate = install_candidate(workspace, root / "candidate")
    basis = TEST_BASIS.replace("1" * 64, workspace.snapshot_id).replace("2" * 64, candidate)
    text = policy_v3_text(
        version=3,
        replace={
            "split": _SPLIT,
            "purpose": "  purpose: STANDARD",
            "basis": basis,
            "sufficiency": _SUFFICIENCY,
        },
    )
    write_policy(workspace.repo, "research_policy", 3, text)
    append_registry_entry(workspace.repo, "research_policy", 3)
    (workspace.repo / "research").mkdir(exist_ok=True)
    (workspace.repo / TRIAL_LEDGER_PATH).write_bytes(b"")
    experiment = write_search_experiment(
        workspace.repo, "review_case.yaml", experiment_id="review_case", axes=_AXES
    )
    out = root / "out"
    _run(workspace, experiment, out)
    lines = _trial_lines(workspace.repo)
    target = lines[-1].entry.experiment_id
    _promote(workspace.repo, target, 1)
    return _Env(
        workspace=workspace,
        out=out,
        directory=out / "runs/experiments/review_case/v1",
        experiment=experiment,
        experiment_id=target,
        ledger=(workspace.repo / TRIAL_LEDGER_PATH).read_bytes(),
    )


def _declare(repo: Path, experiment_id: ExperimentId, execution: int, used: bool) -> Path:
    path = repo / "research/declarations" / f"{experiment_id}_{execution}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 1\n"
        f'experiment_id: "{experiment_id}"\n'
        f"execution: {execution}\n"
        'question: "この候補の選定・調整に、研究の外で見た相場を使ったか（テスト）"\n'
        f'answer: "{"使った" if used else "使っていない"}"\n'
        'recorded_by: "テスト"\n'
        f"used_outside_viewing: {'true' if used else 'false'}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def restore(env: _Env) -> Iterator[None]:
    """各テストは同じ出発点から始める。

    判定を書き換えた試行台帳・空の審査台帳・版 1 の規則・申告なし。
    """
    repo = env.workspace.repo
    (repo / TRIAL_LEDGER_PATH).write_bytes(env.ledger)
    (repo / ADOPTION_REVIEW_LEDGER_PATH).write_bytes(b"")
    (repo / ADOPTION_REVIEW_LOCK_PATH).unlink(missing_ok=True)
    shutil.rmtree(repo / _RULES, ignore_errors=True)
    shutil.copytree(REPO_ROOT / _RULES, repo / _RULES)
    shutil.rmtree(repo / "research/declarations", ignore_errors=True)
    _declare(repo, env.experiment_id, 1, used=False)
    yield
    (repo / ADOPTION_REVIEW_LOCK_PATH).unlink(missing_ok=True)


def _review(
    env: _Env,
    *,
    execution: int = 1,
    directory: Path | None = None,
    snapshots: Path | None = None,
    out: Path | None = None,
) -> tuple[int, str, str]:
    return _cli(
        [
            "experiment",
            "review",
            "--experiment-id",
            str(env.experiment_id),
            "--execution",
            str(execution),
            "--repo-root",
            str(env.workspace.repo),
            "--experiment-dir",
            str(directory or env.directory),
            "--snapshots",
            str(snapshots or env.workspace.repo / "data/snapshots"),
            "--out",
            str(out or env.out),
        ]
    )


def _reviews(env: _Env) -> tuple[AdoptionReviewEntry, ...]:
    contents = read_review_ledger_file(env.workspace.repo / ADOPTION_REVIEW_LEDGER_PATH)
    assert isinstance(contents, ReviewLedgerContents), contents
    return tuple(line.entry for line in contents.lines)


def _record(env: _Env, entry: AdoptionReviewEntry, out: Path | None = None) -> Path:
    base = (out or env.out) / "runs/experiments/review_case/v1/review" / entry.review_nonce
    return base / REVIEW_RECORD_FILE


def _decimals(value: Any) -> set[str]:
    """数値の記録の中の十進の値の文字列（標準出力に出ていないことの照合に使う）。"""
    if isinstance(value, dict):
        return {item for child in value.values() for item in _decimals(child)}
    if isinstance(value, list):
        return {item for child in value for item in _decimals(child)}
    if isinstance(value, str) and _DECIMAL.search(value) and re.fullmatch(r"[-0-9.E+]+", value):
        return {value}
    return set()


# --- T1・E2 の (2) --------------------------------------------------------------------


def test_t1_a_candidate_proceeds_and_is_recorded(env: _Env) -> None:
    code, stdout, stderr = _review(env)
    assert code == 0, stdout + stderr
    assert "結果: PROCEED" in stdout
    (entry,) = _reviews(env)
    assert entry.result is ReviewResult.PROCEED
    assert entry.reasons == ()
    assert entry.review_rules_ref.version == 1
    assert entry.evidence_floor.outcomes.standard is FloorOutcome.MET
    assert entry.ledger_tally.overlapping_executions >= 1  # 台帳の累計がある
    for _, rows in entry.cost_model_fit.items():
        assert len(rows) == 6  # 適合の検査の 6 行
    for _, summary in entry.deflated_sharpe.items():
        assert summary.state in ("SHOWN", "UNAVAILABLE")  # 状態だけ（値は数値の層）
    for _, trial in entry.fixed_trials.items():
        assert trial is not None
    record = _record(env, entry)
    data = record.read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry.numeric_record_digest.hex
    payload = json.loads(data)
    assert set(payload["variants"]) == {"standard", "candidate"}
    for name in ("cost_model_fit", "deflated_sharpe", "evidence_floor", "folds"):
        assert name in payload["variants"]["standard"]
    # 標準出力に数値を出さない（D16 §11.1）。数値の記録の十進の値が 1 つも出ていない。
    assert not _DECIMAL.search(stdout)
    assert not any(value in stdout for value in _decimals(payload))
    # 審査台帳の行には数値が無い（R1-1 (a)）。
    ledger_text = (env.workspace.repo / ADOPTION_REVIEW_LEDGER_PATH).read_text(encoding="utf-8")
    assert not any(value in ledger_text for value in _decimals(payload))


def test_t7_a_proceeded_execution_is_not_reviewed_again(env: _Env) -> None:
    assert _review(env)[0] == 0
    before = (env.workspace.repo / ADOPTION_REVIEW_LEDGER_PATH).read_bytes()
    code, stdout, _ = _review(env)
    assert code == 7
    assert "理由の識別子: ENTRY_ALREADY_REVIEWED" in stdout
    assert (env.workspace.repo / ADOPTION_REVIEW_LEDGER_PATH).read_bytes() == before


# --- T5・T6 -------------------------------------------------------------------------


def test_t5_a_declaration_does_not_proceed(env: _Env) -> None:
    _declare(env.workspace.repo, env.experiment_id, 1, used=True)
    code, stdout, _ = _review(env)
    assert code == 7
    assert "OUTSIDE_VIEWING_DECLARED" in stdout
    (entry,) = _reviews(env)
    assert entry.result is ReviewResult.DO_NOT_PROCEED
    assert entry.declaration.file_digest is not None


def test_t6_a_missing_declaration_defers(env: _Env) -> None:
    shutil.rmtree(env.workspace.repo / "research/declarations")
    code, stdout, _ = _review(env)
    assert code == 8
    assert "理由の識別子: DECLARATION_MISSING" in stdout
    (entry,) = _reviews(env)
    assert entry.result is ReviewResult.DEFERRED


def test_t6_failed_fixed_trial_runs_defer(env: _Env, tmp_path: Path) -> None:
    code, stdout, _ = _review(env, snapshots=tmp_path / "no-snapshots", out=tmp_path / "out")
    assert code == 8
    assert "EVIDENCE_FLOOR_UNDETERMINED" in stdout
    assert "COST_MODEL_UNDETERMINED" in stdout
    (entry,) = _reviews(env)
    assert entry.evidence_floor.outcomes.standard is FloorOutcome.UNDETERMINED


# --- T7b・T7c（規則の版。仮置き 12・R1-10 (a)）--------------------------------------------


def _register_v2(env: _Env) -> None:
    """規則の版 2（値を 1 つ変えた）を、今の試行台帳の末尾の行の後から効く版として登録する。"""
    repo = env.workspace.repo
    v1 = (repo / _RULES / "adoption_review_v1.yaml").read_text(encoding="utf-8")
    v2 = v1.replace("\nversion: 1\n", "\nversion: 2\n", 1).replace(
        'max_share: "0.25"', 'max_share: "0.5"'
    )
    (repo / _RULES / "adoption_review_v2.yaml").write_text(v2, encoding="utf-8")
    _, rules_digest = load_adoption_review_rules(
        repo / _RULES / "adoption_review_v2.yaml", version=2
    )
    tail = _trial_lines(repo)[-1].digest.hex
    registry = repo / _RULES / "registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8")
        + f'  - {{id: adoption_review, version: 2, digest: "{rules_digest.hex}",'
        f' effective_after: "{tail}"}}\n',
        encoding="utf-8",
    )


def test_t7b_a_deferred_review_is_retried_with_its_first_rules_version(env: _Env) -> None:
    repo = env.workspace.repo
    shutil.rmtree(repo / "research/declarations")
    assert _review(env)[0] == 8
    _register_v2(env)
    _declare(repo, env.experiment_id, 1, used=False)
    code, stdout, stderr = _review(env)
    assert code == 0, stdout + stderr
    first, second = _reviews(env)
    assert first.review_rules_ref == second.review_rules_ref
    assert second.review_rules_ref.version == 1


def test_t7b_a_changed_rules_file_stops_the_retry(env: _Env) -> None:
    repo = env.workspace.repo
    shutil.rmtree(repo / "research/declarations")
    assert _review(env)[0] == 8
    rules = repo / _RULES / "adoption_review_v1.yaml"
    rules.write_text(
        rules.read_text(encoding="utf-8").replace('max_share: "0.25"', 'max_share: "0.9"'),
        encoding="utf-8",
    )
    before = (repo / ADOPTION_REVIEW_LEDGER_PATH).read_bytes()
    code, stdout, _ = _review(env)
    assert code == 7
    assert "理由の識別子: ENTRY_RULES_VERSION_CHANGED" in stdout
    assert (repo / ADOPTION_REVIEW_LEDGER_PATH).read_bytes() == before


def test_t7c_a_new_rules_version_applies_only_to_executions_started_after_it(
    env: _Env, tmp_path: Path
) -> None:
    repo = env.workspace.repo
    _register_v2(env)
    # 版 2 の登録より前に開始した実行（実行番号 1）には版 1 が当たる。
    out = tmp_path / "out"
    shutil.copytree(env.out, out, symlinks=True)
    directory = out / "runs/experiments/review_case/v1"
    code, stdout, stderr = _review(env, directory=directory, out=out)
    assert code == 0, stdout + stderr
    # 版 2 の登録より後に開始した実行（同じ版の再実行。実行番号 2）には版 2 が当たる。
    _run(env.workspace, env.experiment, out)
    _promote(repo, env.experiment_id, 2)
    _declare(repo, env.experiment_id, 2, used=False)
    code, stdout, stderr = _review(env, execution=2, directory=directory, out=out)
    assert code == 0, stdout + stderr
    first, second = _reviews(env)
    assert (first.execution, first.review_rules_ref.version) == (1, 1)
    assert (second.execution, second.review_rules_ref.version) == (2, 2)
    # 同じ版の再実行で束縛が後の実行を指すディレクトリは、実行番号 1 の審査に使えない（T11）。
    (repo / ADOPTION_REVIEW_LEDGER_PATH).write_bytes(b"")
    code, stdout, _ = _review(env, directory=directory, out=out)
    assert code == 2
    assert "理由の識別子: ARTIFACT_BINDING_MISMATCH" in stdout


# --- T10・T11・T16 ------------------------------------------------------------------------


def test_t11_another_directory_is_a_binding_mismatch(env: _Env) -> None:
    other = env.directory.parent / "v9"
    shutil.rmtree(other, ignore_errors=True)
    shutil.copytree(env.directory, other, ignore=shutil.ignore_patterns("review"))
    try:
        code, stdout, _ = _review(env, directory=other)
        assert code == 2
        assert "理由の識別子: ARTIFACT_BINDING_MISMATCH" in stdout
        assert _reviews(env) == ()
        assert not (other / "review").exists()
    finally:
        shutil.rmtree(other)


def test_t11_a_tampered_manifest_text_is_a_binding_mismatch(env: _Env, tmp_path: Path) -> None:
    """記録票の本文（カレンダー）だけを書き換えた成果物は審査に使わない（Codex 1 巡目）。"""
    out = tmp_path / "out"
    directory = out / "runs/experiments/review_case/v1"
    shutil.copytree(env.directory, directory, ignore=shutil.ignore_patterns("review"))
    path = directory / "experiment_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    (calendar,) = [item for item in payload["resolved_files"] if item["role"] == "calendar"]
    calendar["text"] = calendar["text"] + "\n# 書き換え\n"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    code, stdout, _ = _review(env, directory=directory, out=out)
    assert code == 2
    assert "理由の識別子: ARTIFACT_BINDING_MISMATCH" in stdout
    assert _reviews(env) == ()
    assert not (directory / "review").exists()


def test_t16_a_refused_append_leaves_no_line_and_a_retry_writes_elsewhere(env: _Env) -> None:
    repo = env.workspace.repo
    (repo / ADOPTION_REVIEW_LOCK_PATH).write_text("{}\n", encoding="utf-8")
    places = env.directory / "review"
    before = set(places.iterdir()) if places.exists() else set()
    code, _, stderr = _review(env)
    assert code == 1
    assert "審査台帳に行を残せなかった" in stderr
    assert _reviews(env) == ()
    (first,) = set(places.iterdir()) - before  # 数値の記録は残るが審査として扱わない
    kept = (first / REVIEW_RECORD_FILE).read_bytes()
    (repo / ADOPTION_REVIEW_LOCK_PATH).unlink()
    assert _review(env)[0] == 0
    (entry,) = _reviews(env)
    assert entry.review_nonce != first.name  # 新しい乱数の置き場（T10 の前者）
    assert (first / REVIEW_RECORD_FILE).read_bytes() == kept


def test_t10_a_failed_sync_after_the_full_line_is_a_recorded_review(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(_: int) -> None:
        raise OSError("sync failed")

    monkeypatch.setattr(review_store, "_sync", failing)
    code, _, stderr = _review(env)
    assert code == 0
    assert "同期に失敗した" in stderr
    monkeypatch.undo()
    assert len(_reviews(env)) == 1
    code, stdout, _ = _review(env)
    assert code == 7
    assert "ENTRY_ALREADY_REVIEWED" in stdout
