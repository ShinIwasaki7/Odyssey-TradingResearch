"""記録票の材料を作る合成と設定の部分（D07 §19.2・§20.2・§20.4・§21.2 の手順3）。

- 検証戦略 A・B の複雑性の計測値4件を固定し、上限（Q7 決定: 30・36・33・18）の内側にある
  ことを確かめる（D07 §20.4 が実装 PR 3 の単体テストに求めるもの）。
- 研究ポリシーファイルの読込（D07 §20.2）。
- 記録票の本文から組み立て直した設定が、ファイルから読んだ設定と同じ実行条件になること
  （D07 §21.2 の手順3。本文をファイルシステムへ書き戻さない）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.composition import compile_experiment_strategy, complexity_profiles
from odyssey_fx.app.config import ConfigError
from odyssey_fx.app.config.experiment_v2 import (
    TEXT_ROLES,
    ExperimentV2,
    experiment_v2_from_texts,
    load_experiment_v2,
)
from odyssey_fx.app.config.research_policy import load_research_policy, research_policy_path
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.evaluation.domain.research_policy import (
    ComplexityMeasures,
    check_complexity,
    measure_complexity,
)
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG

REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY = REPO_ROOT / "configs/policies/research/research_policy_v1.yaml"


def _load(relative: str) -> ExperimentV2:
    return load_experiment_v2(
        REPO_ROOT / relative,
        repo_root=REPO_ROOT,
        registry=INITIAL_CATALOG,
        metric_set_versions=frozenset({METRIC_SET_VERSION}),
    )


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        (
            "configs/experiments/strategy_a_t01_v2.yaml",
            ComplexityMeasures(component_kinds=5, instances=6, parameters=6, decision_outputs=1),
        ),
        (
            "configs/experiments/strategy_b_t02_d1_2s.yaml",
            ComplexityMeasures(component_kinds=10, instances=12, parameters=11, decision_outputs=6),
        ),
    ],
)
def test_the_two_strategies_measure_within_the_limits(
    relative: str, expected: ComplexityMeasures
) -> None:
    """検証戦略 A・B の計測値4件（D07 §20.4 の表）と、上限の内側にあること（Q7 決定）。

    パラメータ数は**解決済み**の件数である。起草時に宣言から数えた値（A 6・B 11）と一致した。
    """
    loaded = _load(relative)
    compiled = compile_experiment_strategy(loaded.experiment, loaded.environment.timeframe_defs)
    profiles, unmeasured, _ = complexity_profiles(compiled, INITIAL_CATALOG)

    measures = measure_complexity(profiles, unmeasured_outputs=unmeasured)

    assert measures == expected
    assert check_complexity(measures, loaded.policy.limits).outcome is CheckOutcome.PASSED


def test_the_research_policy_file_holds_the_q7_limits() -> None:
    """研究ポリシー v1 の上限は Q7 決定の値（D07 §20.2）。版参照は `<id>_v<版>.yaml` を指す。"""
    assert research_policy_path(REPO_ROOT, "research_policy", 1) == POLICY
    policy = load_research_policy(POLICY, policy_id="research_policy", version=1)
    limits = policy.limits
    assert (
        limits.component_kinds,
        limits.instances,
        limits.parameters,
        limits.decision_outputs,
    ) == (30, 36, 33, 18)


def test_a_policy_with_other_limits_has_another_digest() -> None:
    """上限だけを書き換えたポリシーは別のダイジェストになる（P3 が拒否する根拠。D07 §20.2）。"""
    text = POLICY.read_text(encoding="utf-8")
    original = load_research_policy(POLICY, policy_id="research_policy", version=1, text=text)
    edited = load_research_policy(
        POLICY,
        policy_id="research_policy",
        version=1,
        text=text.replace("instances: 36", "instances: 37"),
    )
    assert edited.digest != original.digest


def test_a_policy_file_that_names_another_version_is_refused() -> None:
    """実験設定の版参照とファイルの `id` / `version` が食い違えば設定の誤り（D07 §20.2）。"""
    with pytest.raises(ConfigError, match="研究ポリシーの id と版"):
        load_research_policy(POLICY, policy_id="research_policy", version=2)


@pytest.mark.parametrize("policy_id", ["../x", "a/b", ""])
def test_a_policy_id_that_is_not_a_file_name_is_refused(policy_id: str) -> None:
    """研究ポリシーの `id` はファイル名に使うので、区切り文字を含めない（仮置き）。"""
    with pytest.raises(ConfigError):
        research_policy_path(REPO_ROOT, policy_id, 1)


def test_the_texts_rebuild_the_same_configuration_without_touching_files() -> None:
    """記録票の本文から組み立て直すと、ファイルから読んだときと同じ実行条件になる（手順3）。"""
    loaded = _load("configs/experiments/strategy_b_t02_d1_2s.yaml")

    rebuilt = experiment_v2_from_texts(
        {role: loaded.texts[role] for role in TEXT_ROLES},
        {
            str(symbol): text
            for symbol, text in loaded.symbol_texts.items()
            if str(symbol) == "USDJPY"
        },
        registry=INITIAL_CATALOG,
        metric_set_versions=frozenset({METRIC_SET_VERSION}),
    )

    assert rebuilt.experiment == loaded.experiment
    assert rebuilt.policy == loaded.policy
    assert rebuilt.environment.calendar == loaded.environment.calendar
    assert dict(rebuilt.environment.timeframe_defs) == dict(loaded.environment.timeframe_defs)
    assert rebuilt.environment.calendar_path is None
    assert rebuilt.strategy_path is None


def test_rebuilding_without_a_role_is_a_configuration_error() -> None:
    """役割が1つでも欠けた本文からは組み立てない（D07 §19.2 の `resolved_files`）。"""
    loaded = _load("configs/experiments/strategy_b_t02_d1_2s.yaml")
    texts = {role: loaded.texts[role] for role in TEXT_ROLES if role != "calendar"}
    with pytest.raises(ConfigError, match="calendar"):
        experiment_v2_from_texts(
            texts,
            {"USDJPY": loaded.symbol_texts[loaded.experiment.execution_series.symbol]},
            registry=INITIAL_CATALOG,
            metric_set_versions=frozenset({METRIC_SET_VERSION}),
        )
