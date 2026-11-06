"""順 8 の元の実験（研究仮説 H6、EURUSD）の事前登録の確認。

D19 の手順 1、D19 の仮置き 30、D14 仮置き 16。

`experiment run` は設定の読込の検査を run の開始時にしか当てないので、事前登録の PR の merge の
前に、登録する実験設定がそのまま読め、格子の全試行の戦略がコンパイルを通ることをここで確かめる。

- 実験設定 `configs/experiments/h6_h1_breakout_m15_ema_confirm_eurusd.yaml` が書式 v2 の読込
  （`load_experiment_v2`）を通る。研究ポリシー第 4 版の登録簿・前提の記録・EURUSD の両組の
  snapshot・換算の snapshot の組・比較の前提の照合（D18 §3.3 の C1〜C7）を含む。
- 格子は D19 §7.2 の H6 の列のとおり 4 試行で、試行番号は D09 §5.2 の列挙の順（N が遅く、R が速く
  変わる）。
- 全 4 試行の戦略がコンパイルを通り（既存の宣言に無い部品の版の組み合わせ: 突破 v1 と確認待ちの
  入場、`market_order_intent` v2・`level_stop_loss` v2、空の有効性の束縛。D19 の仮置き 30）、複雑性
  （事前検査 P6）と試行の数（事前検査 P7）が研究ポリシー第 4 版の上限以下。

**run はしない**。テストは設定ファイルだけを読み、snapshot の実体も戦略の成績も読まない。
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
from odyssey_fx.strategy.declarations.entry_policy import (
    AwaitConfirmation,
    BarsDeadline,
    DeadlineAction,
)
from odyssey_fx.strategy.declarations.opportunity import OnNewTrigger, OnOrderAccepted
from odyssey_fx.strategy.declarations.specs import BoolValue, FloatValue, IntValue, StrValue

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
EXPERIMENT: Final = REPO_ROOT / "configs/experiments/h6_h1_breakout_m15_ema_confirm_eurusd.yaml"
STRATEGY: Final = REPO_ROOT / "configs/strategies/h6_h1_breakout_m15_ema_confirm_eurusd_v1.yaml"
#: 研究ポリシー第 4 版の `basis.symbols.EURUSD`（D18 §3.1）。
STANDARD_SNAPSHOT: Final = "4e6eb8d40df023685b6b0739bd202d132c97997de68e8d55f0b4114c89bb917b"
CANDIDATE_SNAPSHOT: Final = "9c03bc1283e5c7474dc1602104d943fe7985b11c745dae3135813fc7ddee1b19"
#: 研究ポリシー第 4 版の `basis.conversion_snapshots`（USDJPY の両組。D18 §3.1）。
CONVERSION_STANDARD: Final = "ecaaf53fcdedbc1977184b390c36e2714c658829341eed3ec5d71588d3bfc622"
CONVERSION_CANDIDATE: Final = "f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca"

#: D19 §7.2 の H6 の格子（仮置き 27）を D09 §5.2 の列挙の順に並べた割当。
EXPECTED_TRIALS: Final = (
    (
        (("breakout_level", "lookback"), IntValue(10)),
        (("take_profit", "reward_risk"), FloatValue(1.5)),
    ),
    (
        (("breakout_level", "lookback"), IntValue(10)),
        (("take_profit", "reward_risk"), FloatValue(3.0)),
    ),
    (
        (("breakout_level", "lookback"), IntValue(20)),
        (("take_profit", "reward_risk"), FloatValue(1.5)),
    ),
    (
        (("breakout_level", "lookback"), IntValue(20)),
        (("take_profit", "reward_risk"), FloatValue(3.0)),
    ),
)


@pytest.fixture(scope="module")
def loaded() -> ExperimentV2:
    return load_experiment_v2(
        EXPERIMENT,
        repo_root=REPO_ROOT,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )


def test_the_experiment_loads_as_a_search_on_policy_v4(loaded: ExperimentV2) -> None:
    """研究ポリシー第 4 版を指す探索の実験として読め、EURUSD と換算の両組の snapshot が
    役割ごとに入る。"""
    search = loaded.search
    assert search is not None
    assert (loaded.research_policy.policy_id, loaded.research_policy.version) == (
        "research_policy",
        4,
    )
    assert loaded.policy.version == 4
    assert loaded.metric_set_version == 3
    assert search.final_holdout is None and search.lineage is None
    assert str(loaded.body_of(Variant.STANDARD).snapshot_ref.snapshot_id) == STANDARD_SNAPSHOT
    assert str(loaded.body_of(Variant.CANDIDATE).snapshot_ref.snapshot_id) == CANDIDATE_SNAPSHOT
    for variant, expected in (
        (Variant.STANDARD, CONVERSION_STANDARD),
        (Variant.CANDIDATE, CONVERSION_CANDIDATE),
    ):
        conversion = loaded.body_of(variant).conversion_snapshot_ref
        assert conversion is not None
        assert str(conversion.snapshot_id) == expected
    assert loaded.strategy_path is not None and loaded.strategy_path.resolve() == STRATEGY
    assert loaded.body.strategy.strategy_id == "h6_h1_breakout_m15_ema_confirm_eurusd"


def test_the_grid_is_the_four_trials_of_d19_in_the_enumeration_order(
    loaded: ExperimentV2,
) -> None:
    """N {10, 20} × R {1.5, 3.0}、試行番号 0〜3 は D09 §5.2 の列挙の順（D19 §7.2・仮置き 27）。"""
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
    """全 4 試行がコンパイルを通り（H6 の部品の版の組み合わせ。D19 の仮置き 30）、P6 が合格。"""
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


def test_the_declaration_uses_the_component_versions_of_d19(loaded: ExperimentV2) -> None:
    """部品の版は D19 §7.2 の H6 の列のとおり（突破 v1、確認結果で起動する注文意図・損切りの v2）。
    有効性の束縛は空で、確認待ちの入場は 15 分足 4 本の期限。"""
    strategy = loaded.body.strategy
    versions = {
        c.instance_id: (c.contract_ref.component_id, c.contract_ref.version)
        for c in strategy.components
    }
    assert versions == {
        "breakout_level": ("extreme_price", 1),
        "stop_level": ("extreme_price", 1),
        "entry_trigger": ("breakout_trigger", 1),
        "m15_ema": ("ema", 1),
        "m15_above_ema": ("price_compare", 1),
        "entry_filter": ("condition_filter", 1),
        "entry_order": ("market_order_intent", 2),
        "initial_stop": ("level_stop_loss", 2),
        "take_profit": ("fixed_rr_take_profit", 1),
    }
    assert strategy.opportunity_validity.bindings == ()
    assert strategy.market_state is None
    entry = strategy.entry_policy
    assert isinstance(entry, AwaitConfirmation)
    assert isinstance(entry.deadline, BarsDeadline) and entry.deadline.bars == 4
    assert entry.on_deadline is DeadlineAction.EXPIRE


def test_the_fixed_values_are_copied_from_the_existing_declarations(
    loaded: ExperimentV2,
) -> None:
    """軸でない値は既存の宣言の固定値（D19 の仮置き 28）: 15 分足の EMA は期間 20・窓 60、
    `include_start_bar=True`、損切りの窓は 1 時間足の安値 20 本、同時の保持は A・B と同じ。"""
    strategy = loaded.body.strategy
    parameters = {c.instance_id: dict(c.parameters) for c in strategy.components}
    assert parameters["m15_ema"] == {"period": IntValue(20), "window_bars": IntValue(60)}
    assert parameters["entry_filter"] == {"include_start_bar": BoolValue(True)}
    assert parameters["stop_level"] == {"lookback": IntValue(20), "mode": StrValue("MIN")}
    assert parameters["breakout_level"]["mode"] == StrValue("MAX")
    assert parameters["m15_above_ema"] == {"operator": StrValue("GT")}
    assert parameters["entry_trigger"] == {"direction": StrValue("LONG")}
    concurrency = strategy.opportunity_concurrency
    assert concurrency.max_active == 1
    assert concurrency.on_new_trigger is OnNewTrigger.KEEP_EXISTING
    assert concurrency.on_order_accepted is OnOrderAccepted.KEEP_OTHERS
