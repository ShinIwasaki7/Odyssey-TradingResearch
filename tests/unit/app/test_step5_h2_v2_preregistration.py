"""順 5 の改善後の実験（研究仮説 H2 の版 2）の事前登録の確認（D14 §2.5 の手順 4）。

D14 仮置き 16（事前登録の PR での読込とコンパイルの確認）に倣う。

`experiment run` は設定の読込の検査と系譜の台帳との照合を run の開始時にしか当てないので、PR-2 の
merge の前に、登録する版 2 の実験設定がそのまま読め、系譜の元の実行が台帳にあり、格子の全試行の
戦略がコンパイルを通ることをここで確かめる。

- 実験設定 `configs/experiments/h2_daily_uptrend_h1_ema_cross_v2.yaml` が書式 v2 の読込
  （`load_experiment_v2`）を通る。実験名は版 1 と同じで `version: 2`（D14 仮置き 8）。
- 系譜は版 1 の実行番号 1 を指し、変更の種類は `PARAMETERS` の 1 つ、理由の文は分析メモの SHA-256
  を含む（D13 §7.1、D14 §2.5 の (3)、D14 仮置き 7）。元の実行の開始の行と結末の行が版管理された
  台帳にある（開始の行は `composition._lineage_request` が run の開始時に当てる照合と同じ条件。
  実装 PR #98 の仮置き 56・65）。
- 版 1 から変えたのは P の軸の値だけ（{20, 50} → {50, 75}。2026-10-27 の人間の決定）。仮説の文・
  R の軸・試行数・戦略宣言・両組の snapshot・研究ポリシー・分割は版 1 と同じ。
- 全 4 試行の戦略がコンパイルを通り、複雑性（事前検査 P6）と試行の数（事前検査 P7）が研究ポリシー
  第 3 版の上限以下。

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
EXPERIMENT_V1: Final = REPO_ROOT / "configs/experiments/h2_daily_uptrend_h1_ema_cross.yaml"
EXPERIMENT_V2: Final = REPO_ROOT / "configs/experiments/h2_daily_uptrend_h1_ema_cross_v2.yaml"
STRATEGY: Final = REPO_ROOT / "configs/strategies/h2_daily_uptrend_h1_ema_cross_v1.yaml"
#: 版 1 の記録票の識別子と実行番号（台帳の行。README §2.1）。
SOURCE_EXPERIMENT_ID: Final = "9c95335880803f535800f919d4a8d84b4e5291a808941e6c0506a0d6b92efc25"
SOURCE_EXECUTION: Final = 1
#: 分析メモの SHA-256（README §2.3。理由の文に書く。D14 仮置き 7）。
ANALYSIS_MEMO_SHA256: Final = "deb793f920aae482bda43d4d4ecb2d61762a8e1af0e0ce0d14a26ef86d187365"
R_AXIS: Final = ("take_profit", "reward_risk")

#: 版 2 の格子 P {50, 75} × R {1.5, 3.0} を D09 §5.2 の列挙の順に並べた割当。
EXPECTED_TRIALS: Final = (
    ((("h1_ema", "period"), IntValue(50)), (R_AXIS, FloatValue(1.5))),
    ((("h1_ema", "period"), IntValue(50)), (R_AXIS, FloatValue(3.0))),
    ((("h1_ema", "period"), IntValue(75)), (R_AXIS, FloatValue(1.5))),
    ((("h1_ema", "period"), IntValue(75)), (R_AXIS, FloatValue(3.0))),
)


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


def test_version_2_is_the_next_version_of_the_same_experiment(
    loaded: ExperimentV2, original: ExperimentV2
) -> None:
    """同じ実験名の版 2 で、仮説・研究ポリシー・両組の snapshot・戦略宣言・分割は版 1 と同じ。"""
    search = loaded.search
    assert search is not None and original.search is not None
    assert (loaded.values["id"], loaded.values["version"]) == ("h2_daily_uptrend_h1_ema_cross", 2)
    assert (original.values["id"], original.values["version"]) == (
        "h2_daily_uptrend_h1_ema_cross",
        1,
    )
    assert loaded.hypothesis == original.hypothesis
    assert loaded.research_policy == original.research_policy
    assert loaded.metric_set_version == original.metric_set_version == 3
    for variant in (Variant.STANDARD, Variant.CANDIDATE):
        # 実行条件の本体（戦略・snapshot・口座・リスク・執行・費用・換算など）は版の番号だけが違う。
        assert replace(loaded.body_of(variant), version=1) == original.body_of(variant)
    assert loaded.strategy_path is not None and loaded.strategy_path.resolve() == STRATEGY
    assert loaded.texts["strategy"] == original.texts["strategy"]
    assert search.final_holdout is None
    assert search.split == original.search.split


def test_the_lineage_points_at_execution_1_of_version_1_with_one_change_kind(
    loaded: ExperimentV2,
) -> None:
    """系譜は版 1 の実行番号 1、変更の種類は PARAMETERS の 1 つ。

    理由の文は分析メモの SHA-256 を持つ（D14 仮置き 7）。
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


def test_the_grid_moves_only_the_period_axis_upwards(
    loaded: ExperimentV2, original: ExperimentV2
) -> None:
    """P {50, 75} × R {1.5, 3.0}、試行番号 0〜3 は D09 §5.2 の列挙の順。

    R の軸と試行数は版 1 と同じ。
    """
    search = loaded.search
    assert search is not None and original.search is not None
    assignments = enumerate_assignments(search.plan)
    assert [
        tuple(((instance, parameter), value) for instance, parameter, value in item.values)
        for item in assignments
    ] == list(EXPECTED_TRIALS)
    assert search.plan.max_trials == original.search.plan.max_trials == 4
    (r_v2,) = (a for a in search.plan.axes if (a.instance_id, a.parameter) == R_AXIS)
    (r_v1,) = (a for a in original.search.plan.axes if (a.instance_id, a.parameter) == R_AXIS)
    assert r_v2 == r_v1
    limit = loaded.policy.trial_limit
    assert limit is not None
    assert check_trial_count(len(assignments), limit).outcome is CheckOutcome.PASSED


def test_every_trial_compiles_within_the_complexity_limits(loaded: ExperimentV2) -> None:
    """全 4 試行がコンパイルを通り、P6 が合格。

    P 75 でも窓 150 が部品の条件 `window_bars >= 2 * period` を満たす（D14 仮置き 15）。
    """
    search = loaded.search
    assert search is not None
    timeframes = {
        definition.ref: definition for definition in loaded.environment.timeframe_defs.values()
    }
    compiled_refs = set()
    for assignment in enumerate_assignments(search.plan):
        definition = trial_strategy(loaded.body.strategy, assignment)
        outcome = compile_strategy(definition, INITIAL_CATALOG, timeframes)
        assert isinstance(outcome, CompileSucceeded), outcome
        compiled_refs.add(outcome.compiled.compiled_ref)
        profiles, unmeasured, causes = complexity_profiles(outcome.compiled, INITIAL_CATALOG)
        measures = measure_complexity(profiles, unmeasured_outputs=unmeasured)
        result = check_complexity(measures, loaded.policy.limits, unmeasured_causes=causes)
        assert result.outcome is CheckOutcome.PASSED, result
    assert len(compiled_refs) == len(EXPECTED_TRIALS)
    (h1_ema,) = (c for c in loaded.body.strategy.components if c.instance_id == "h1_ema")
    assert dict(h1_ema.parameters)["window_bars"] == IntValue(150)
