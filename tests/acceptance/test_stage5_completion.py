"""段階5 の完了条件を人工データで確かめる（全体計画 §8.2、D09 §13、D08 §2.4）。

全体計画 §8.2 の段階5 の完了条件は次の3つである（確かめ方の正本は D09 §13 の表）。

1. **選定が train 内で閉じる**: (a) 各 fold の選定記録の `inputs` が、その fold の選定区間の
   評価の識別子だけを指す。(b) 最後の fold の検証区間の人工データだけを変えた snapshot で同じ
   実験を別の版として通し、各 fold の選んだ試行・選定記録の `inputs` の候補の区分・選定区間の
   取引件数が変わらない。
2. **全試行を記録する**: (a) コンパイル拒否の試行が記録票と集約表に `FAILED` として残る。
   (b) 全単位が集約表にあり、状態の件数が結末記録と一致する。(c) 途中で止まった状態を人工に
   作った版のディレクトリに `experiment report` を当て、中断・未試行・試行済みが区別して出る。
   (d) 試行台帳に、完了した実行の開始の行と結末の行が1つずつあり、途中で止まった実行には開始の
   行だけがある。
3. **holdout を通常探索で読めない**: (a) 記録票の `allowed_partitions` と事前検査 P2 が研究履歴
   だけを示す。(b) 評価範囲が研究履歴の境界を超える研究ポリシーを読込時に拒否する。(c) 封印
   partition を持つ人工データで、許可集合に封印 partition が入らない。

D08 §2.4 の規約に従う。

- **コマンドを `subprocess` で起こす**（`experiment run` / `experiment report` / `experiment
  ledger`。D08 §2.3 の規約1）。
- 作業場は封印 partition を持つ T02 の人工データの作業場（`t02_workspace.build_sealed_workspace`。
  D08 §9.5 の例外、D09 §9.8 の Q2 決定）。試験用の研究ポリシー版 3（2 fold。**テスト用であり
  研究ポリシーではない**）と登録簿と空の試行台帳を、テストの中のリポジトリの根に作る。
- 探索は既定の2軸 × 2値（`search_experiments.DEFAULT_AXES`）。15分足 EMA の期間 40 の2試行は
  コンパイルが拒否する（試行の失敗）。
- **戦略の成績は読まない**（判定・指標の値を確かめない。確かめるのは記録の形と出どころ）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from odyssey_fx.app.config.research_policy import (
    research_policy_path,
    research_policy_registry_path,
)
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_OUTCOME_FILE,
    LEDGER_BINDING_FILE,
    REPORT_FILE,
    SEARCH_DIRECTORY,
    TRIAL_METRICS_TABLE,
    TRIAL_UNITS_TABLE,
    UNITS_DIRECTORY,
    read_experiment_manifest,
    read_experiment_outcome,
    read_ledger_binding,
    read_selections,
    read_trial_ledger_file,
    read_unit_records,
)
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.domain.experiment import ExperimentManifest
from odyssey_fx.evaluation.domain.research_policy import PolicyCheck
from odyssey_fx.evaluation.domain.search import (
    CandidateStatus,
    FoldSelection,
    TrialLedgerEvent,
    TrialLedgerLine,
    TrialPhase,
    TrialRunRecord,
    TrialStatus,
    TrialUnitKey,
)
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.marketdata.domain.access import AccessClass
from tests.fixtures.acceptance.t02_workspace import (
    LAST_VALIDATION,
    T02Workspace,
    accept_variant,
    build_sealed_workspace,
    sealed_raw_bars,
)
from tests.fixtures.evaluation.research_policies import (
    append_registry_entry,
    policy_v3_text,
    write_policy,
)
from tests.fixtures.evaluation.search_experiments import install_policy_v3, write_search_experiment

#: 探索の実験の名前（版 1 = 作業場の snapshot、版 2 = 最後の fold の検証区間だけを変えた snapshot、
#: 版 3 = 途中で止める実行）。
_NAME = "strategy_b_search"

#: 台帳のファイル（リポジトリの根からの相対パス。D09 §10.10）。
_LEDGER = Path("research") / "trial_ledger.jsonl"

#: D09 §11.5 の表の9見出し（この順で必ず出る）。
_HEADINGS = (
    "## 1. 判定",
    "## 2. 判定の理由",
    "## 3. fold ごとの成績",
    "## 4. 指標の計算可否",
    "## 5. fold 全体の水準",
    "## 6. 証拠の十分さ",
    "## 7. 比較の前提",
    "## 8. 試行台帳",
    "## 9. 診断（判定に使わない）",
)

#: 途中で止まった実行を待つ上限（秒）。束縛の記録が現れたら止める。
_KILL_WAIT_SECONDS = 120.0


def _odyssey(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """`odyssey-fx` を**別の OS のプロセス**として起こす（D08 §2.3 の 1）。"""
    return subprocess.run(
        [sys.executable, "-m", "odyssey_fx.app.cli.main", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def _run_argv(workspace: T02Workspace, experiment: Path, artifacts: Path) -> list[str]:
    return [
        "experiment",
        "run",
        "--experiment",
        str(experiment),
        "--snapshots",
        str(workspace.repo / "data/snapshots"),
        "--out",
        str(artifacts),
        "--repo-root",
        str(workspace.repo),
    ]


def _version_directory(artifacts: Path, version: int) -> Path:
    return artifacts / "runs" / "experiments" / _NAME / f"v{version}"


@dataclass(frozen=True, slots=True)
class _Run:
    """`experiment run` を別プロセスで1回起こした結果。"""

    returncode: int
    output: str
    artifacts: Path
    directory: Path

    @property
    def manifest(self) -> ExperimentManifest:
        return read_experiment_manifest(self.directory)

    @property
    def selections(self) -> dict[int, FoldSelection]:
        return read_selections(self.directory)

    @property
    def runs(self) -> dict[TrialUnitKey, TrialRunRecord]:
        return read_unit_records(self.directory)[1]

    def table(self, name: str) -> pl.DataFrame:
        return pl.read_parquet(self.directory / SEARCH_DIRECTORY / name)


def _experiment_run(
    workspace: T02Workspace, experiment: Path, artifacts: Path, version: int
) -> _Run:
    artifacts.mkdir(parents=True, exist_ok=True)
    process = _odyssey(_run_argv(workspace, experiment, artifacts), cwd=artifacts)
    return _Run(
        returncode=process.returncode,
        output=process.stdout + process.stderr,
        artifacts=artifacts,
        directory=_version_directory(artifacts, version),
    )


def _experiment_version(workspace: T02Workspace, version: int, snapshot_id: str) -> Path:
    """版 1 の実験設定から `version` と `snapshot` だけを書き換えた実験設定を作業場に置く。"""
    text = (workspace.repo / "configs/experiments" / f"{_NAME}.yaml").read_text(encoding="utf-8")
    for old, new in (
        ("\nversion: 1\n", f"\nversion: {version}\n"),
        (f'snapshot: "{workspace.snapshot_id}"', f'snapshot: "{snapshot_id}"'),
    ):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = workspace.repo / "configs/experiments" / f"{_NAME}_v{version}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _ledger_lines(repo: Path) -> tuple[TrialLedgerLine, ...]:
    contents = read_trial_ledger_file(repo / _LEDGER)
    assert isinstance(contents, TrialLedgerContents), contents
    return contents.lines


def _ledger_events(repo: Path, version: int) -> list[tuple[int, TrialLedgerEvent]]:
    return [
        (line.entry.execution, line.entry.event)
        for line in _ledger_lines(repo)
        if (line.entry.experiment_name, line.entry.experiment_version) == (_NAME, version)
    ]


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> T02Workspace:
    """封印 partition を持つ作業場に、研究ポリシー版 3・空の台帳・探索の実験設定（版 1）を置く。"""
    built = build_sealed_workspace(tmp_path_factory.mktemp("stage5-acceptance"))
    install_policy_v3(built.repo)
    (built.repo / _LEDGER).parent.mkdir(parents=True, exist_ok=True)
    (built.repo / _LEDGER).write_bytes(b"")
    write_search_experiment(built.repo, f"{_NAME}.yaml", experiment_id=_NAME)
    return built


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """版 1 と版 2 の成果物の基点（同じ研究の成果物の置き場をまねる）。"""
    return tmp_path_factory.mktemp("stage5-artifacts")


@pytest.fixture(scope="module")
def completed(workspace: T02Workspace, artifacts: Path) -> _Run:
    """版 1 を別プロセスで最後まで通す。"""
    run = _experiment_run(
        workspace, workspace.repo / "configs/experiments" / f"{_NAME}.yaml", artifacts, 1
    )
    assert run.returncode == 0, run.output
    return run


@pytest.fixture(scope="module")
def variant(
    workspace: T02Workspace,
    artifacts: Path,
    completed: _Run,
    tmp_path_factory: pytest.TempPathFactory,
) -> _Run:
    """最後の fold の検証区間の素の足だけを変えた snapshot を受け入れ、版 2 として通す。"""
    snapshot_id = accept_variant(
        workspace,
        tmp_path_factory.mktemp("variant-raw"),
        sealed_raw_bars(changed=LAST_VALIDATION),
    )
    assert snapshot_id != workspace.snapshot_id
    run = _experiment_run(workspace, _experiment_version(workspace, 2, snapshot_id), artifacts, 2)
    assert run.returncode == 0, run.output
    return run


# --- 1. 選定が train 内で閉じる ----------------------------------------------------


def test_1a_each_selection_reads_only_the_evaluations_of_its_own_train_interval(
    completed: _Run,
) -> None:
    """各 fold の選定記録の `inputs` の評価の識別子は、その fold の選定区間の試行記録の
    `run_evaluation_id` だけ（検証区間の単位の識別子を含まない）。"""
    selections = completed.selections
    runs = completed.runs
    folds = sorted({unit.fold_index for unit in runs})
    assert sorted(selections) == folds == [0, 1]
    for fold in folds:
        train = {
            unit.trial_index: record.run_evaluation_id
            for unit, record in runs.items()
            if unit.fold_index == fold and unit.phase is TrialPhase.TRAIN
        }
        validation = {
            record.run_evaluation_id
            for unit, record in runs.items()
            if unit.fold_index == fold and unit.phase is TrialPhase.VALIDATION
        }
        assert validation, f"fold {fold} has no validation unit"
        inputs = selections[fold].inputs
        referenced = {trial: digest for trial, digest, _ in inputs if digest is not None}
        assert referenced == train
        assert not set(referenced.values()) & validation
        # 識別子の無い入力はコンパイル拒否の試行だけ（選定区間の試行記録が無い）。
        assert {trial for trial, digest, _ in inputs if digest is None} == {
            plan.trial_index for plan in completed.manifest.trials if plan.compile_rejections
        }


def test_1b_changing_the_last_validation_data_changes_no_selection(
    completed: _Run, variant: _Run
) -> None:
    """最後の fold の検証区間の人工データだけを変えた snapshot の版 2 で、各 fold の選んだ試行・
    候補の区分・選定区間の取引件数が版 1 と同じ（評価の識別子は snapshot が違うので比べない）。"""
    assert variant.manifest.snapshot_id != completed.manifest.snapshot_id

    def shape(run: _Run) -> dict[int, Any]:
        return {
            fold: (
                selection.selected_trial_index,
                selection.selected_train_trade_count,
                [(trial, status) for trial, _, status in selection.inputs],
            )
            for fold, selection in run.selections.items()
        }

    assert shape(variant) == shape(completed)
    assert sorted(shape(completed)) == [0, 1]


# --- 2. 全試行を記録する -----------------------------------------------------------


def test_2a_a_compile_rejected_trial_stays_in_the_manifest_and_the_table_as_failed(
    completed: _Run,
) -> None:
    rejected = {plan.trial_index for plan in completed.manifest.trials if plan.compile_rejections}
    assert rejected and len(completed.manifest.trials) == 4
    units = completed.table(TRIAL_UNITS_TABLE)
    statuses = units.filter(pl.col("trial_index").is_in(sorted(rejected))).get_column("status")
    assert statuses.len() > 0
    assert set(statuses.to_list()) == {TrialStatus.FAILED.value}
    for fold, selection in completed.selections.items():
        assert {(trial, status) for trial, _, status in selection.inputs if trial in rejected} == {
            (trial, CandidateStatus.EXCLUDED_TRIAL_FAILED) for trial in rejected
        }, fold


def test_2b_every_unit_is_in_the_table_and_the_counts_match_the_outcome(completed: _Run) -> None:
    units = completed.table(TRIAL_UNITS_TABLE)
    trials = range(len(completed.manifest.trials))
    expected = {(fold, TrialPhase.TRAIN.value, trial) for fold in (0, 1) for trial in trials}
    for fold, selection in completed.selections.items():
        assert selection.selected_trial_index is not None
        expected.add((fold, TrialPhase.VALIDATION.value, selection.selected_trial_index))
    rows = units.select(["fold_index", "phase", "trial_index"]).rows()
    assert sorted(rows) == sorted(expected)

    outcome = read_experiment_outcome(completed.directory)
    assert outcome is not None and outcome.search is not None
    table_counts = units.group_by("status").len()
    assert {status: count for status, count in table_counts.iter_rows()} == {
        status.value: count for status, count in outcome.search.trial_counts if count
    }
    assert sum(count for _, count in outcome.search.trial_counts) == len(expected)


def _stopped_copy(completed: _Run, workspace: T02Workspace, root: Path) -> tuple[Path, Path]:
    """完了した版 1 から途中で止まった状態を人工に作る（成果物の基点とリポジトリの根を返す）。

    fold 0 の選定区間の試行 1 の開始記録の後で止まった状態: 結末記録・レポート・集約表・選定記録・
    fold 0 の検証区間と fold 1 の記録を消し、fold 0 の試行 1 の試行記録だけを消す（開始記録は
    残す）。台帳は別のリポジトリの根へ、この実行の開始の行までを写す。
    """
    artifacts = root / "artifacts"
    for entry in (completed.artifacts / "runs").iterdir():
        if entry.name != "experiments":
            shutil.copytree(entry, artifacts / "runs" / entry.name)
    directory = _version_directory(artifacts, 1)
    shutil.copytree(completed.directory, directory)

    search = directory / SEARCH_DIRECTORY
    (directory / EXPERIMENT_OUTCOME_FILE).unlink()
    (directory / REPORT_FILE).unlink(missing_ok=True)
    for name in (TRIAL_UNITS_TABLE, TRIAL_METRICS_TABLE):
        (search / name).unlink()
    for path in search.glob("selection_f*.json"):
        path.unlink()
    units = search / UNITS_DIRECTORY
    for path in [*units.glob("f1_*"), *units.glob("f0_VALIDATION_*")]:
        path.unlink()
    assert (units / "f0_TRAIN_t1.start.json").is_file()
    (units / "f0_TRAIN_t1.json").unlink()

    repo = root / "repo"
    shutil.copytree(workspace.repo / "configs", repo / "configs")
    binding = read_ledger_binding(directory)
    assert binding is not None
    raw = (workspace.repo / _LEDGER).read_bytes().splitlines(keepends=True)
    lines = _ledger_lines(workspace.repo)
    assert len(raw) == len(lines)
    started = next(
        index for index, line in enumerate(lines) if line.digest == binding.started_line_digest
    )
    (repo / _LEDGER).parent.mkdir(parents=True)
    (repo / _LEDGER).write_bytes(b"".join(raw[: started + 1]))
    return artifacts, repo


def test_2c_a_report_on_a_stopped_execution_tells_aborted_not_started_and_completed_apart(
    completed: _Run, workspace: T02Workspace, tmp_path: Path
) -> None:
    rejected = {plan.trial_index for plan in completed.manifest.trials if plan.compile_rejections}
    assert {0, 1}.isdisjoint(rejected), "試行 0・1 はコンパイルが通る割当のはず"
    artifacts, repo = _stopped_copy(completed, workspace, tmp_path)
    directory = _version_directory(artifacts, 1)
    starts, runs = read_unit_records(directory)
    assert set(starts) - set(runs) == {
        TrialUnitKey(fold_index=0, phase=TrialPhase.TRAIN, trial_index=1)
    }

    process = _odyssey(
        ["experiment", "report", "--experiment-dir", str(directory), "--repo-root", str(repo)],
        cwd=artifacts,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = (directory / REPORT_FILE).read_text(encoding="utf-8")
    # 各単位の状態の表の行（`| 単位 | 割当 | 状態 | 評価の状態 |`）で、単位ごとに区別して出る。
    expected = {
        "f0_TRAIN_t0": TrialStatus.COMPLETED,
        "f0_TRAIN_t1": TrialStatus.ABORTED,
        "f1_TRAIN_t0": TrialStatus.NOT_STARTED,
    }
    for name, status in expected.items():
        rows = [line for line in report.splitlines() if line.startswith(f"| {name} |")]
        assert len(rows) == 1, (name, rows)
        assert f"`{status.value}`" in rows[0].split("|")[3], (name, rows[0])


def test_2d_a_completed_execution_has_one_started_and_one_finished_ledger_line(
    completed: _Run, workspace: T02Workspace
) -> None:
    outcome = read_experiment_outcome(completed.directory)
    assert outcome is not None and outcome.search is not None
    execution = outcome.search.ledger_execution
    assert _ledger_events(workspace.repo, 1) == [
        (execution, TrialLedgerEvent.STARTED),
        (execution, TrialLedgerEvent.FINISHED),
    ]


def test_2d_a_stopped_execution_leaves_only_its_started_ledger_line(
    completed: _Run, workspace: T02Workspace, tmp_path: Path
) -> None:
    """版 3 を別プロセスで起こし、束縛の記録ができたら止める（D09 §10.12.4 の d の位置）。"""
    experiment = _experiment_version(workspace, 3, workspace.snapshot_id)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    directory = _version_directory(artifacts, 3)
    binding = directory / SEARCH_DIRECTORY / LEDGER_BINDING_FILE
    process = subprocess.Popen(  # noqa: S603 - 引数はテストが組み立てた固定の値
        [
            sys.executable,
            "-m",
            "odyssey_fx.app.cli.main",
            *_run_argv(workspace, experiment, artifacts),
        ],
        cwd=artifacts,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + _KILL_WAIT_SECONDS
        while not binding.is_file():
            assert process.poll() is None, "the run ended before it was stopped"
            assert time.monotonic() < deadline, "the binding record did not appear"
            time.sleep(0.01)
    finally:
        process.kill()
        process.wait()

    assert not (directory / EXPERIMENT_OUTCOME_FILE).exists()
    events = _ledger_events(workspace.repo, 3)
    assert [event for _, event in events] == [TrialLedgerEvent.STARTED]


# --- 3. holdout を通常探索で読めない -----------------------------------------------


def test_3a_every_allowed_partition_is_research_history_and_p2_passes(
    completed: _Run, variant: _Run
) -> None:
    for run in (completed, variant):
        manifest = run.manifest
        assert manifest.allowed_partitions
        assert set(manifest.allowed_partitions.values()) == {AccessClass.RESEARCH_HISTORY}
        checks = [
            item
            for item in manifest.pre_run_checks
            if item.check is PolicyCheck.RESEARCH_HISTORY_ONLY
        ]
        assert [item.outcome for item in checks] == [CheckOutcome.PASSED]


def test_3b_a_policy_whose_range_crosses_the_research_boundary_is_refused_on_load(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """評価範囲の終わりが研究履歴の境界（2024-01-01Z）以上の版を登録簿に足すと、`experiment run`
    は読込で拒否し（終了コード 2）、版のディレクトリを作らない（D09 §6.1 の検査6）。"""
    version = 9
    split = (
        "  split:\n"
        '    range: {start: "2015-01-04T22:00:00Z", end: "2024-01-01T00:00:00Z"}\n'
        '    train_length: "1000d"\n'
        '    validation_length: "1000d"\n'
        "    window: ROLLING\n"
        '    purge: "0s"\n'
        "    min_folds: 2"
    )
    registry = research_policy_registry_path(workspace.repo)
    kept = registry.read_bytes()
    write_policy(
        workspace.repo,
        "research_policy",
        version,
        policy_v3_text(version=version, replace={"split": split}),
    )
    try:
        append_registry_entry(
            workspace.repo,
            "research_policy",
            version,
            research_until=UtcTime.from_components(2100, 1, 1),
        )
        name = "strategy_b_search_over_boundary"
        experiment = write_search_experiment(
            workspace.repo, f"{name}.yaml", experiment_id=name, policy_version=version
        )
        before = _ledger_lines(workspace.repo)
        process = _odyssey(_run_argv(workspace, experiment, tmp_path), cwd=tmp_path)
        assert process.returncode == 2, process.stdout + process.stderr
        assert "研究履歴の期間境界" in process.stdout + process.stderr
        assert not (tmp_path / "runs" / "experiments" / name).exists()
        assert _ledger_lines(workspace.repo) == before
    finally:
        registry.write_bytes(kept)
        research_policy_path(workspace.repo, "research_policy", version).unlink()


def test_3c_the_sealed_partitions_never_enter_the_allowed_set(
    workspace: T02Workspace, completed: _Run
) -> None:
    snapshot = json.loads(
        (workspace.repo / "data/snapshots" / workspace.snapshot_id / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    classes = [item["partition_id"]["access_class"] for item in snapshot["partitions"]]
    assert AccessClass.LEGACY_HOLDOUT.value in classes
    assert AccessClass.RESEARCH_HISTORY.value in classes
    allowed = completed.manifest.allowed_partitions
    assert len(allowed) == classes.count(AccessClass.RESEARCH_HISTORY.value)
    assert all(AccessClass.LEGACY_HOLDOUT.value not in key for key in allowed)


# --- レポートと台帳の表示（D09 §11.5・§10.10）---------------------------------------


def test_the_report_of_a_completed_search_has_the_nine_headings_in_order(
    completed: _Run, workspace: T02Workspace
) -> None:
    process = _odyssey(
        [
            "experiment",
            "report",
            "--experiment-dir",
            str(completed.directory),
            "--repo-root",
            str(workspace.repo),
        ],
        cwd=completed.artifacts,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = (completed.directory / REPORT_FILE).read_text(encoding="utf-8")
    assert report.startswith(f"# 実験レポート: {_NAME} 版 1")
    positions = [report.find(heading + "\n") for heading in _HEADINGS]
    assert -1 not in positions, positions
    assert positions == sorted(positions)


def test_the_ledger_command_lists_the_executions(
    completed: _Run, variant: _Run, workspace: T02Workspace
) -> None:
    process = _odyssey(
        [
            "experiment",
            "ledger",
            "--repo-root",
            str(workspace.repo),
            "--out",
            str(completed.artifacts),
        ],
        cwd=completed.artifacts,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert "## 研究ポリシー research_policy 版 3" in process.stdout
    assert _NAME in process.stdout
