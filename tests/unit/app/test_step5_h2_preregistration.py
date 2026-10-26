"""順 5 の元の実験（研究仮説 H2）の事前登録の確認（D14 §2.2 の手順 1、D14 仮置き 16）。

`experiment run` は設定の読込の検査を run の開始時にしか当てないので、事前登録の PR の merge の
前に、登録する実験設定がそのまま読め、格子の全試行の戦略がコンパイルを通ることをここで確かめる。

- 実験設定 `configs/experiments/h2_daily_uptrend_h1_ema_cross.yaml` が書式 v2 の読込
  （`load_experiment_v2`）を通る。研究ポリシー第 3 版の登録簿・前提の記録・両組の snapshot・比較の
  前提の照合（D13-16 (a)・D13-21 (a)）を含む。
- 格子は D14 §4.3 の表のとおり 4 試行で、試行番号は D09 §5.2 の列挙の順（P が遅く、R が速く
  変わる）。
- 全 4 試行の戦略がコンパイルを通り、複雑性（事前検査 P6）と試行の数（事前検査 P7）が研究ポリシー
  第 3 版の上限以下。

**run はしない**。テストは設定ファイルだけを読み、snapshot の実体も戦略の成績も読まない
（snapshot の実体が要る確認は PR 本文に書く。D14 仮置き 16）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.composition import complexity_profiles, trial_strategy
from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.research_policy import (
    check_complexity,
    check_trial_count,
    measure_complexity,
)
from odyssey_fx.evaluation.domain.search import enumerate_assignments
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.specs import FloatValue, IntValue

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
EXPERIMENT: Final = REPO_ROOT / "configs/experiments/h2_daily_uptrend_h1_ema_cross.yaml"
STRATEGY: Final = REPO_ROOT / "configs/strategies/h2_daily_uptrend_h1_ema_cross_v1.yaml"
STANDARD_SNAPSHOT: Final = "ecaaf53fcdedbc1977184b390c36e2714c658829341eed3ec5d71588d3bfc622"
CANDIDATE_SNAPSHOT: Final = "f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca"

#: D14 §4.3 の H2 の行（仮置き 13）を D09 §5.2 の列挙の順に並べた割当。
EXPECTED_TRIALS: Final = (
    ((("h1_ema", "period"), IntValue(20)), (("take_profit", "reward_risk"), FloatValue(1.5))),
    ((("h1_ema", "period"), IntValue(20)), (("take_profit", "reward_risk"), FloatValue(3.0))),
    ((("h1_ema", "period"), IntValue(50)), (("take_profit", "reward_risk"), FloatValue(1.5))),
    ((("h1_ema", "period"), IntValue(50)), (("take_profit", "reward_risk"), FloatValue(3.0))),
)


@pytest.fixture(scope="module")
def loaded() -> ExperimentV2:
    return load_experiment_v2(
        EXPERIMENT,
        repo_root=REPO_ROOT,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )


def test_the_experiment_loads_as_a_search_on_policy_v3(loaded: ExperimentV2) -> None:
    """研究ポリシー第 3 版を指す探索の実験として読め、両組の snapshot が役割ごとに入る。"""
    search = loaded.search
    assert search is not None
    assert (loaded.research_policy.policy_id, loaded.research_policy.version) == (
        "research_policy",
        3,
    )
    assert loaded.metric_set_version == 3
    assert search.final_holdout is None and search.lineage is None
    assert str(loaded.body_of(Variant.STANDARD).snapshot_ref.snapshot_id) == STANDARD_SNAPSHOT
    assert str(loaded.body_of(Variant.CANDIDATE).snapshot_ref.snapshot_id) == CANDIDATE_SNAPSHOT
    assert loaded.strategy_path is not None and loaded.strategy_path.resolve() == STRATEGY
    assert loaded.body.strategy.strategy_id == "h2_daily_uptrend_h1_ema_cross"


def test_the_grid_is_the_four_trials_of_d14_in_the_enumeration_order(
    loaded: ExperimentV2,
) -> None:
    """P {20, 50} × R {1.5, 3.0}、試行番号 0〜3 は D09 §5.2 の列挙の順（D14 §4.3・仮置き 13）。"""
    search = loaded.search
    assert search is not None
    assignments = enumerate_assignments(search.plan)
    assert [
        tuple(((instance, parameter), value) for instance, parameter, value in item.values)
        for item in assignments
    ] == list(EXPECTED_TRIALS)
    limit = loaded.policy.trial_limit
    assert limit is not None
    assert check_trial_count(len(assignments), limit).outcome is CheckOutcome.PASSED


def test_every_trial_compiles_within_the_complexity_limits(loaded: ExperimentV2) -> None:
    """全 4 試行がコンパイルを通り（H2 の宣言は既存の部品だけで書ける）、P6 が合格。"""
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


def test_the_h1_ema_window_is_fixed_at_three_times_the_largest_period(
    loaded: ExperimentV2,
) -> None:
    """`h1_ema.window_bars` は 150（D14 仮置き 15）。部品の条件 `window_bars >= 2 * period` を
    全試行で満たす。"""
    (h1_ema,) = (c for c in loaded.body.strategy.components if c.instance_id == "h1_ema")
    assert dict(h1_ema.parameters)["window_bars"] == IntValue(150)
