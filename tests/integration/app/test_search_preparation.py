"""探索の実験の準備: 読込 → 試行の列挙 → 試行ごとのコンパイル → 記録票の値（D09 §4.1 の1・2）。

段階5 の実装 PR 2 の統合テスト（D09 §14 の分割案 A の PR 2）。T02 の人工データを受け入れて
承認した作業場（`tests/fixtures/acceptance/t02_workspace.py`）に、試験用の研究ポリシー版 3
（2 fold。テスト用であり研究ポリシーではない）と探索の実験設定（検証戦略 B の2軸 × 2値）を
作り、合成の `prepare_search` を通す。**run はしない**（探索の実行は後続の実装 PR）。

確かめること:

- 2軸 × 2 fold の実験から、試行数・単位数・単位ごとの予測ダイジェストが記録票に事前に全部入る。
- コンパイル拒否の試行が失敗（`compiled_ref = None`）として `compile_rejections` を持つ。
- 同じ試行を同じ区間で走らせる単位は、別の fold でも同じ予測 `RunId` を持つ（D09 §6.2）。
- 事前検査は P1・P2・P6・P7（P6 はコンパイルが通った試行の最大、P7 は列挙した試行の数）。
- 記録票の保存の前に、全単位の既存の run 成果物を確かめる（選ばれるかどうかによらず検証区間の
  単位も。D09 §10.5 の注記）。
- `experiment run` は探索の実験をまだ受けない（終了コード 2。何も書かない）。
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemExperimentStore,
    FileSystemResultRepository,
)
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.evaluation.application.run_experiment import (
    PreparedSearch,
    RefusalKind,
    RunExperiment,
)
from odyssey_fx.evaluation.domain.research_policy import PolicyCheck
from odyssey_fx.evaluation.domain.search import TrialPhase, TrialUnitKey
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.registry import ComponentRegistry
from odyssey_fx.strategy.declarations.specs import IntValue
from tests.fixtures.acceptance.t02_workspace import T02Workspace, build_workspace
from tests.fixtures.evaluation.search_experiments import (
    install_policy_v3,
    write_search_experiment,
)


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> T02Workspace:
    """T02 の人工データを受け入れて承認し、試験用の研究ポリシー版 3 と版 4 を載せた作業場。

    版 4 は試行数の上限だけを 3 に下げたもの（P7 の不合格を作るため）。
    """
    built = build_workspace(tmp_path_factory.mktemp("t02-search"))
    install_policy_v3(built.repo)
    install_policy_v3(built.repo, version=4, trials=3)
    return built


def _load(workspace: T02Workspace, path: Path) -> ExperimentV2:
    return load_experiment_v2(
        path,
        repo_root=workspace.repo,
        registry=INITIAL_CATALOG,
        metric_set_versions=frozenset({METRIC_SET_VERSION}),
    )


def _prepare(workspace: T02Workspace, path: Path) -> PreparedSearch:
    return composition.prepare_search(
        loaded=_load(workspace, path),
        snapshots_root=workspace.repo / "data/snapshots",
        repo_root=workspace.repo,
    )


@pytest.fixture(scope="module")
def prepared(workspace: T02Workspace) -> PreparedSearch:
    return _prepare(workspace, write_search_experiment(workspace.repo))


def _unit(fold: int, phase: TrialPhase, trial: int) -> TrialUnitKey:
    return TrialUnitKey(fold_index=fold, phase=phase, trial_index=trial)


def test_every_trial_and_unit_is_fixed_in_the_manifest_before_any_run(
    prepared: PreparedSearch,
) -> None:
    """2軸 × 2値 × 2 fold: 4 試行の割当と、通った試行の4単位の予測ダイジェストが記録票に入る。"""
    manifest = prepared.manifest
    assert manifest.is_search
    assert len(manifest.trials) == 4
    assert [
        tuple(value for _, _, value in trial.assignment.values) for trial in manifest.trials
    ] == [
        (IntValue(10), IntValue(10)),
        (IntValue(10), IntValue(20)),
        (IntValue(40), IntValue(10)),
        (IntValue(40), IntValue(20)),
    ]
    for trial in manifest.trials[:2]:
        assert trial.compiled and not trial.compile_rejections
        assert [unit for unit, _ in trial.expected_config_digests] == [
            _unit(0, TrialPhase.TRAIN, trial.trial_index),
            _unit(0, TrialPhase.VALIDATION, trial.trial_index),
            _unit(1, TrialPhase.TRAIN, trial.trial_index),
            _unit(1, TrialPhase.VALIDATION, trial.trial_index),
        ]
    assert manifest.compiled_ref is None and manifest.expected_config_digest is None
    assert manifest.trials[0].compiled_ref != manifest.trials[1].compiled_ref


def test_a_trial_rejected_by_the_compiler_is_kept_as_a_failure(prepared: PreparedSearch) -> None:
    """期間 40 は `window_bars >= 2 * period` を満たさず、試行の失敗として拒否の区分を残す。"""
    for trial in prepared.manifest.trials[2:]:
        assert trial.compiled_ref is None
        assert trial.expected_config_digests == ()
        assert trial.compile_rejections
        assert all('"instance_id":"m15_ema"' in item for item in trial.compile_rejections)
    for prepared_trial in prepared.trials[2:]:
        assert prepared_trial.compiled is None and prepared_trial.run_configs == ()


def test_the_units_differ_only_in_the_compiled_ref_and_the_interval(
    prepared: PreparedSearch,
) -> None:
    """単位の `RunConfig` は区間とコンパイル結果だけが違い、同じ試行・同じ区間は同じ `RunId`。

    fold 0 の検証区間と fold 1 の選定区間は同じ区間なので、同じ試行の2単位は1つの run を共有する
    （D09 §6.2）。
    """
    folds = prepared.manifest.split.folds  # type: ignore[union-attr]
    first, second = prepared.trials[:2]
    for trial in (first, second):
        assert trial.run_config(
            _unit(0, TrialPhase.TRAIN, trial.plan.trial_index)
        ).run_interval == (folds[0].train)
        assert (
            trial.run_config(_unit(1, TrialPhase.VALIDATION, trial.plan.trial_index)).run_interval
            == folds[1].validation
        )
        shared = trial.expected_run_id(_unit(0, TrialPhase.VALIDATION, trial.plan.trial_index))
        assert shared == trial.expected_run_id(_unit(1, TrialPhase.TRAIN, trial.plan.trial_index))
        assert len({run_id for _, run_id in trial.expected_run_ids}) == 3
    configs = [config for trial in (first, second) for _, config in trial.run_configs]
    assert len({(config.account, config.seed, config.snapshot_ref) for config in configs}) == 1
    assert not {run_id for _, run_id in first.expected_run_ids} & {
        run_id for _, run_id in second.expected_run_ids
    }


def test_the_pre_run_checks_cover_every_unit(prepared: PreparedSearch) -> None:
    """事前検査は P1・P2・P6・P7 の4件（D09 §10.2・§10.8）。許可集合は研究履歴だけ。"""
    manifest = prepared.manifest
    checks = {item.check: item for item in manifest.pre_run_checks}
    assert set(checks) == {
        PolicyCheck.HYPOTHESIS_PRESENT,
        PolicyCheck.RESEARCH_HISTORY_ONLY,
        PolicyCheck.COMPLEXITY_WITHIN_LIMITS,
        PolicyCheck.TRIAL_COUNT_WITHIN_LIMIT,
    }
    assert all(item.outcome is CheckOutcome.PASSED for item in checks.values())
    assert '"trial_count":4' in checks[PolicyCheck.TRIAL_COUNT_WITHIN_LIMIT].observed
    assert set(manifest.allowed_partitions.values()) == {AccessClass.RESEARCH_HISTORY}
    assert manifest.complexity.instances is not None


def test_the_preparation_is_deterministic(
    workspace: T02Workspace, prepared: PreparedSearch
) -> None:
    """同じ実験設定からは同じ記録票の識別子と同じ予測 `RunId` が出る（D09 §5.4）。"""
    again = _prepare(workspace, write_search_experiment(workspace.repo, "again.yaml"))
    assert again.manifest.experiment_id == prepared.manifest.experiment_id
    assert [trial.expected_run_ids for trial in again.trials] == [
        trial.expected_run_ids for trial in prepared.trials
    ]


def test_more_trials_than_the_policy_limit_fail_p7(workspace: T02Workspace) -> None:
    """列挙した試行の数（コンパイル拒否を含む）が研究ポリシーの上限を超えれば P7 が不合格。"""
    path = write_search_experiment(workspace.repo, "over_limit.yaml", policy_version=4)
    checks = {item.check: item for item in _prepare(workspace, path).manifest.pre_run_checks}
    assert checks[PolicyCheck.TRIAL_COUNT_WITHIN_LIMIT].outcome is CheckOutcome.FAILED


def test_a_search_where_no_trial_compiles_cannot_measure_its_complexity(
    workspace: T02Workspace,
) -> None:
    """通った試行が1件も無ければ複雑性は計測できず、P6 は UNREADABLE（D09 §10.2）。"""
    path = write_search_experiment(
        workspace.repo,
        "none_compiles.yaml",
        axes="    - {instance: m15_ema, parameter: period, values: [40, 50]}",
        max_trials=2,
    )
    manifest = _prepare(workspace, path).manifest
    assert all(not trial.compiled for trial in manifest.trials)
    checks = {item.check: item for item in manifest.pre_run_checks}
    assert checks[PolicyCheck.COMPLEXITY_WITHIN_LIMITS].outcome is CheckOutcome.UNREADABLE
    assert manifest.complexity.instances is None


def test_the_trials_compile_against_the_given_registry(workspace: T02Workspace) -> None:
    """試行のコンパイルは渡した部品の登録を使う（読込・複雑性の計測と同じ登録。D09 §5.2）。

    部品が1つも無い登録を渡せば、全試行が契約の未登録としてコンパイル拒否になる。
    """
    prepared = composition.prepare_search(
        loaded=_load(workspace, write_search_experiment(workspace.repo, "registry.yaml")),
        snapshots_root=workspace.repo / "data/snapshots",
        repo_root=workspace.repo,
        registry=ComponentRegistry(registrations={}),
    )
    assert all(not trial.compiled for trial in prepared.manifest.trials)
    assert all(
        "REFERENCE_NOT_FOUND" in item
        for trial in prepared.manifest.trials
        for item in trial.compile_rejections
    )


class _NoRunner:
    def run(self, config: object, compiled: object) -> object:  # pragma: no cover - 呼ばれない
        raise AssertionError("the existing-artifact check must not run anything")


def _use_case(artifacts: Path, prepared: PreparedSearch) -> RunExperiment:
    return RunExperiment(
        store=FileSystemExperimentStore(
            root=artifacts,
            experiment_name=prepared.manifest.experiment_name,
            experiment_version=prepared.manifest.experiment_version,
            identity_of=composition.recompute_experiment_id,
        ),
        runner=_NoRunner(),  # type: ignore[arg-type]
        repository=FileSystemResultRepository(root=artifacts),
        evaluator=EvaluateRun(evaluation_code_digest=prepared.code_digest),
    )


def test_every_unit_is_checked_for_existing_artifacts_before_saving(
    prepared: PreparedSearch, tmp_path: Path
) -> None:
    """選ばれるかどうかが決まっていない検証区間の単位の衝突でも実験全体を拒否する（§10.5）。"""
    use_case = _use_case(tmp_path, prepared)
    assert use_case.check_search_artifacts(prepared) is None

    unit = _unit(1, TrialPhase.VALIDATION, 1)
    run_id = prepared.trials[1].expected_run_id(unit)
    (tmp_path / "runs" / str(run_id)).mkdir(parents=True)
    refusal = use_case.check_search_artifacts(prepared)

    assert refusal is not None
    assert refusal.kind is RefusalKind.RUN_ARTIFACT_CONFLICT
    assert "fold 1 VALIDATION trial 1" in refusal.detail
    assert sorted(path.name for path in tmp_path.iterdir()) == ["runs"]


def _invoke(argv: list[str]) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue() + err.getvalue()


def test_experiment_run_does_not_accept_a_search_experiment_yet(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """探索の実行は後続の実装 PR で有効にする。それまでは設定の誤りとして終了コード 2（仮置き）。"""
    code, output = _invoke(
        [
            "experiment",
            "run",
            "--experiment",
            str(write_search_experiment(workspace.repo, "cli.yaml")),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(tmp_path / "out"),
            "--repo-root",
            str(workspace.repo),
        ]
    )
    assert code == 2, output
    assert "探索の実験" in output
    assert not (tmp_path / "out").exists()


def test_the_run_command_refuses_a_search_experiment(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """`run` は1つの run だけを作る。探索の実験の区間は fold が持つので受けない（D09 §6.2）。"""
    code, output = _invoke(
        [
            "run",
            "--experiment",
            str(write_search_experiment(workspace.repo, "run_cli.yaml")),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(tmp_path / "out"),
            "--repo-root",
            str(workspace.repo),
        ]
    )
    assert code == 1, output
    assert "探索の実験" in output
    assert not (tmp_path / "out").exists()
