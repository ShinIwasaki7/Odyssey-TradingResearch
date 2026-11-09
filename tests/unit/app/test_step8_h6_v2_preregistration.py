"""順 8 の改善後の実験（研究仮説 H6 の版 2、EURUSD）の事前登録の確認（D19 §2.1 の手順 4）。

順 5 の版 2 の確認（`test_step5_h2_v2_preregistration.py`。D14 仮置き 16）と同じ形。

`experiment run` は設定の読込の検査と系譜の台帳との照合を run の開始時にしか当てないので、
事前登録の PR の merge の前に、登録する版 2 の実験設定がそのまま読め、系譜の元の実行が台帳に
あり、格子の全試行の戦略がコンパイルを通ることをここで確かめる。

- 実験設定 `configs/experiments/h6_h1_breakout_m15_ema_confirm_eurusd_v2.yaml` が書式 v2 の読込
  （`load_experiment_v2`。研究ポリシー第 4 版の照合 C1〜C7 を含む）を通る。実験名は版 1 と同じで
  `version: 2`。
- 系譜は版 1 の実行番号 1 を指し、変更の種類は `PARAMETERS` の 1 つ、理由の文は分析メモの SHA-256
  を含む。元の実行の開始の行と結末の行が版管理された台帳にある。
- 版 1 から変えたのは利確の倍率 R の軸の値だけ（{1.5, 3.0} → {1.0, 1.5}。人間の決定 66・67）。
  仮説の文・窓 N の軸・試行数・戦略宣言・両組の snapshot・換算の snapshot・研究ポリシー・分割は
  版 1 と同じ（変更が 1 種類であることの機械的な確認）。
- 全 4 試行の戦略がコンパイルを通り、複雑性（事前検査 P6）と試行の数（事前検査 P7）が研究ポリシー
  第 4 版の上限以下。R 1.5 の 2 試行は版 1 の同じ値の試行と同じコンパイル結果になる。

**run はしない**。テストは設定ファイルと台帳だけを読み、snapshot の実体も戦略の成績も読まない
（台帳の結末の行の判定の値は検査しない）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.composition import complexity_profiles, trial_strategy
from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.evaluation.adapters.fs_store import TRIAL_LEDGER_PATH, read_trial_ledger_file
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.research_policy import (
    check_complexity,
    check_trial_count,
    measure_complexity,
)
from odyssey_fx.evaluation.domain.search import (
    ChangeKind,
    enumerate_assignments,
    has_finished_line,
    started_line_of,
)
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.specs import FloatValue, IntValue

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
EXPERIMENT_V1: Final = REPO_ROOT / "configs/experiments/h6_h1_breakout_m15_ema_confirm_eurusd.yaml"
EXPERIMENT_V2: Final = (
    REPO_ROOT / "configs/experiments/h6_h1_breakout_m15_ema_confirm_eurusd_v2.yaml"
)
STRATEGY: Final = REPO_ROOT / "configs/strategies/h6_h1_breakout_m15_ema_confirm_eurusd_v1.yaml"
#: 版 1 の記録票の識別子と実行番号（台帳の行。README §2.1）。
SOURCE_EXPERIMENT_ID: Final = "9cc0dd26abf6e16e65ec267fa8d5ab1e03d3f71f0285f5ff6d11e4f7e697fb13"
SOURCE_EXECUTION: Final = 1
#: 分析メモの SHA-256（人間の決定 66。理由の文に書く）。
ANALYSIS_MEMO_SHA256: Final = "8366c0360e15555d07c69693af3b6c55aae1ba7a5d2dd2a186f37462e919b94b"
N_AXIS: Final = ("breakout_level", "lookback")
R_AXIS: Final = ("take_profit", "reward_risk")

#: 版 2 の格子 N {10, 20} × R {1.0, 1.5}（決定 67）を D09 §5.2 の列挙の順に並べた割当。
EXPECTED_TRIALS: Final = (
    ((N_AXIS, IntValue(10)), (R_AXIS, FloatValue(1.0))),
    ((N_AXIS, IntValue(10)), (R_AXIS, FloatValue(1.5))),
    ((N_AXIS, IntValue(20)), (R_AXIS, FloatValue(1.0))),
    ((N_AXIS, IntValue(20)), (R_AXIS, FloatValue(1.5))),
)
#: 版 2 の R 1.5 の試行番号と、同じ設定の版 1 の試行番号（版 1 は 0 = (10, 1.5)、2 = (20, 1.5)）。
SAME_AS_V1: Final = {1: 0, 3: 2}


def _load(path: Path) -> ExperimentV2:
    return load_experiment_v2(
        path,
        repo_root=REPO_ROOT,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )


@pytest.fixture(scope="module")
def loaded() -> ExperimentV2:
    return _load(EXPERIMENT_V2)


@pytest.fixture(scope="module")
def original() -> ExperimentV2:
    return _load(EXPERIMENT_V1)


def _compiled_refs(experiment: ExperimentV2) -> list[object]:
    search = experiment.search
    assert search is not None
    timeframes = {
        definition.ref: definition for definition in experiment.environment.timeframe_defs.values()
    }
    refs: list[object] = []
    for assignment in enumerate_assignments(search.plan):
        definition = trial_strategy(experiment.body.strategy, assignment)
        outcome = compile_strategy(definition, INITIAL_CATALOG, timeframes)
        assert isinstance(outcome, CompileSucceeded), outcome
        refs.append(outcome.compiled.compiled_ref)
    return refs


def test_version_2_is_the_next_version_of_the_same_experiment(
    loaded: ExperimentV2, original: ExperimentV2
) -> None:
    """同じ実験名の版 2 で、仮説・研究ポリシー・両組の snapshot・換算・戦略宣言・分割は版 1 と同じ。

    実行条件の本体は版の番号だけが違う。
    """
    search = loaded.search
    assert search is not None and original.search is not None
    assert (loaded.values["id"], loaded.values["version"]) == (
        "h6_h1_breakout_m15_ema_confirm_eurusd",
        2,
    )
    assert (original.values["id"], original.values["version"]) == (
        "h6_h1_breakout_m15_ema_confirm_eurusd",
        1,
    )
    assert loaded.hypothesis == original.hypothesis
    assert loaded.research_policy == original.research_policy
    assert loaded.policy.version == 4
    assert loaded.metric_set_version == original.metric_set_version == 3
    for variant in (Variant.STANDARD, Variant.CANDIDATE):
        # 実行条件の本体（戦略・snapshot・換算・口座・リスク・執行・費用など）は版の番号だけが違う。
        assert replace(loaded.body_of(variant), version=1) == original.body_of(variant)
    assert loaded.strategy_path is not None and loaded.strategy_path.resolve() == STRATEGY
    assert loaded.texts["strategy"] == original.texts["strategy"]
    assert search.final_holdout is None
    assert search.split == original.search.split


def test_the_lineage_points_at_execution_1_of_version_1_with_one_change_kind(
    loaded: ExperimentV2,
) -> None:
    """系譜は版 1 の実行番号 1、変更の種類は PARAMETERS の 1 つ。

    理由の文は分析メモの SHA-256 を持つ。
    """
    search = loaded.search
    assert search is not None and search.lineage is not None
    lineage = search.lineage
    assert lineage.derived_from is not None
    assert lineage.derived_from.experiment_id.digest.hex == SOURCE_EXPERIMENT_ID
    assert lineage.derived_from.execution == SOURCE_EXECUTION
    assert lineage.change_kinds == (ChangeKind.PARAMETERS,)
    assert ANALYSIS_MEMO_SHA256 in lineage.note


def test_the_source_execution_is_in_the_versioned_trial_ledger(loaded: ExperimentV2) -> None:
    """元の実行の開始の行（run の開始時の照合と同じ条件。仮置き 56・65）と結末の行が台帳にある。"""
    search = loaded.search
    assert search is not None and search.lineage is not None
    source = search.lineage.derived_from
    assert source is not None
    contents = read_trial_ledger_file(REPO_ROOT / TRIAL_LEDGER_PATH)
    assert isinstance(contents, TrialLedgerContents), contents
    assert started_line_of(contents.lines, source.experiment_id, source.execution) is not None
    assert has_finished_line(contents.lines, source.experiment_id, source.execution)


def test_the_grid_moves_only_the_reward_risk_axis_downwards(
    loaded: ExperimentV2, original: ExperimentV2
) -> None:
    """N {10, 20} × R {1.0, 1.5}、試行番号 0〜3 は D09 §5.2 の列挙の順。

    窓 N の軸・軸の数・試行数・探索の種類は版 1 と同じで、違うのは R の軸の値だけ。
    """
    search = loaded.search
    assert search is not None and original.search is not None
    assignments = enumerate_assignments(search.plan)
    assert [
        tuple(((instance, parameter), value) for instance, parameter, value in item.values)
        for item in assignments
    ] == list(EXPECTED_TRIALS)
    assert search.plan.max_trials == original.search.plan.max_trials == 4
    assert search.plan.kind == original.search.plan.kind
    axes_v2 = {(a.instance_id, a.parameter): a for a in search.plan.axes}
    axes_v1 = {(a.instance_id, a.parameter): a for a in original.search.plan.axes}
    assert set(axes_v2) == set(axes_v1) == {N_AXIS, R_AXIS}
    assert axes_v2[N_AXIS] == axes_v1[N_AXIS]
    assert axes_v2[R_AXIS] != axes_v1[R_AXIS]
    assert replace(axes_v2[R_AXIS], values=axes_v1[R_AXIS].values) == axes_v1[R_AXIS]
    limit = loaded.policy.trial_limit
    assert limit is not None
    assert check_trial_count(len(assignments), limit).outcome is CheckOutcome.PASSED


def test_every_trial_compiles_and_r_1_5_matches_version_1(
    loaded: ExperimentV2, original: ExperimentV2
) -> None:
    """全 4 試行がコンパイルを通り P6 が合格。R 1.5 の 2 試行は版 1 の同じ値の試行と同じ設定。"""
    search = loaded.search
    assert search is not None
    timeframes = {
        definition.ref: definition for definition in loaded.environment.timeframe_defs.values()
    }
    for assignment in enumerate_assignments(search.plan):
        definition = trial_strategy(loaded.body.strategy, assignment)
        outcome = compile_strategy(definition, INITIAL_CATALOG, timeframes)
        assert isinstance(outcome, CompileSucceeded), outcome
        profiles, unmeasured, causes = complexity_profiles(outcome.compiled, INITIAL_CATALOG)
        measures = measure_complexity(profiles, unmeasured_outputs=unmeasured)
        result = check_complexity(measures, loaded.policy.limits, unmeasured_causes=causes)
        assert result.outcome is CheckOutcome.PASSED, result
    refs_v2 = _compiled_refs(loaded)
    refs_v1 = _compiled_refs(original)
    assert len(set(refs_v2)) == len(EXPECTED_TRIALS)
    for v2_index, v1_index in SAME_AS_V1.items():
        assert refs_v2[v2_index] == refs_v1[v1_index]
    assert {refs_v2[0], refs_v2[2]}.isdisjoint(refs_v1)
