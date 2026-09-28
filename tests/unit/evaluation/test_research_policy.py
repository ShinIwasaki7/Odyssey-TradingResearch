"""研究ポリシー v1 の各検査の合否と、複雑性の計測・上限の境界（D07 §20、D07 §17.2 の実装 PR 3）。"""

from __future__ import annotations

import json

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.domain.research_policy import (
    ComplexityLimits,
    ComplexityMeasures,
    InstanceProfile,
    PolicyCheck,
    PolicyCheckResult,
    PolicyCheckStage,
    all_passed,
    check_complexity,
    check_evaluation_rule,
    check_hypothesis,
    check_preregistration,
    check_research_history_only,
    check_run_matches,
    failed_checks,
    measure_complexity,
)
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.marketdata.domain.access import AccessClass

#: Q7 決定の上限（検証戦略 B の約3倍。D07 §20.2）。
LIMITS = ComplexityLimits(component_kinds=30, instances=36, parameters=33, decision_outputs=18)


def _profile(
    instance_id: str, component_id: str, parameters: int, *outputs: str
) -> InstanceProfile:
    return InstanceProfile(
        instance_id=instance_id,
        component_id=component_id,
        parameter_count=parameters,
        output_data_types=tuple(outputs),
    )


# --- P1 ----------------------------------------------------------------------


def test_a_hypothesis_passes_and_a_blank_one_fails() -> None:
    """P1: 仮説が空でない（D07 §20.3）。空白だけも不合格。"""
    assert check_hypothesis("遅延2秒でも同じ判断になる").outcome is CheckOutcome.PASSED
    blank = check_hypothesis("  \n")
    assert blank.outcome is CheckOutcome.FAILED
    assert blank.stage is PolicyCheckStage.PRE_RUN
    assert json.loads(blank.observed) == {"characters": 0}


# --- P2 ----------------------------------------------------------------------


def test_only_research_history_partitions_pass() -> None:
    """P2: 許可 partition のアクセス分類がすべて研究履歴なら合格（D03 §3.8）。"""
    result = check_research_history_only(
        {
            "USDJPY_1h_bid/RESEARCH_HISTORY": AccessClass.RESEARCH_HISTORY,
            "USDJPY_15m_bid/RESEARCH_HISTORY": AccessClass.RESEARCH_HISTORY,
        }
    )
    assert result.outcome is CheckOutcome.PASSED


def test_a_holdout_partition_fails_and_is_named() -> None:
    """P2: 研究履歴以外の partition が1つでもあれば不合格。観測値にその partition を書く。"""
    result = check_research_history_only(
        {
            "USDJPY_1h_bid/RESEARCH_HISTORY": AccessClass.RESEARCH_HISTORY,
            "USDJPY_1h_bid/LEGACY_HOLDOUT": AccessClass.LEGACY_HOLDOUT,
        }
    )
    assert result.outcome is CheckOutcome.FAILED
    assert json.loads(result.observed) == {
        "partitions_outside_research_history": {
            "USDJPY_1h_bid/LEGACY_HOLDOUT": "LEGACY_HOLDOUT",
        }
    }


# --- P6 と計測 -------------------------------------------------------------------


def test_the_four_measures_follow_the_counting_rules() -> None:
    """部品の種類・使用箇所・解決済みパラメータ・判断を出す使用箇所を数える（D07 §20.4 の表）。"""
    profiles = (
        _profile("fast", "ema", 2, "price"),
        _profile("slow", "ema", 2, "price"),
        _profile("above", "price_compare", 1, "condition_state"),
        _profile("state", "permission_from_condition", 0, "market_permission"),
        _profile("trigger", "breakout_trigger", 1, "opportunity"),
        _profile("filter", "condition_filter", 1, "confirmation_result"),
        _profile("order", "market_order_intent", 0, "order_intent"),
    )
    assert measure_complexity(profiles) == ComplexityMeasures(
        component_kinds=6, instances=7, parameters=7, decision_outputs=4
    )


def test_an_instance_whose_outputs_are_unknown_is_not_counted_as_zero() -> None:
    """出力のデータ型を知り得ない使用箇所があれば、判断を出す使用箇所の数は計測できない（`None`）。"""
    measures = measure_complexity((_profile("a", "ema", 2, "price"),), unmeasured_outputs=("a",))
    assert measures.decision_outputs is None
    assert measures.instances == 1


@pytest.mark.parametrize(
    "field", ["component_kinds", "instances", "parameters", "decision_outputs"]
)
def test_a_measure_equal_to_its_limit_passes_and_one_more_fails(field: str) -> None:
    """P6: 上限以下は合格、上限を1つ超えると不合格（D07 §20.3。境界）。"""
    at_limit = {
        name: getattr(LIMITS, name)
        for name in ("component_kinds", "instances", "parameters", "decision_outputs")
    }
    assert check_complexity(ComplexityMeasures(**at_limit), LIMITS).outcome is CheckOutcome.PASSED

    over = dict(at_limit)
    over[field] += 1
    result = check_complexity(ComplexityMeasures(**over), LIMITS)
    assert result.outcome is CheckOutcome.FAILED
    assert list(json.loads(result.observed)["over_limit"]) == [field]


def test_an_unmeasured_value_is_unreadable_not_passed() -> None:
    """P6: 計測できなかった値があれば `UNREADABLE`。合格として扱わず、原因を観測値に残す。"""
    result = check_complexity(
        ComplexityMeasures(component_kinds=1, instances=1, parameters=0, decision_outputs=None),
        LIMITS,
        unmeasured_causes=("a: the registry has no x@v1",),
    )
    assert result.outcome is CheckOutcome.UNREADABLE
    assert not result.passed
    observed = json.loads(result.observed)
    assert observed["unmeasured"] == ["decision_outputs"]
    assert observed["causes"] == ["a: the registry has no x@v1"]


def test_limits_must_be_positive() -> None:
    """上限は正の整数（D07 §3 の `ComplexityLimits`）。"""
    with pytest.raises(KernelValueError):
        ComplexityLimits(component_kinds=0, instances=1, parameters=1, decision_outputs=1)


# --- P3〜P5 --------------------------------------------------------------------


def test_the_preregistration_passes_when_absent_or_identical_and_fails_otherwise() -> None:
    """P3: 同じ名前と版の記録票が無いか、あっても同じ識別子なら合格（D07 §20.3）。"""
    own = "a" * 64
    assert check_preregistration(own, None).outcome is CheckOutcome.PASSED
    assert check_preregistration(own, own).outcome is CheckOutcome.PASSED
    changed = check_preregistration(own, "b" * 64)
    assert changed.outcome is CheckOutcome.FAILED
    assert changed.stage is PolicyCheckStage.ON_SAVE


def test_the_run_must_match_both_the_config_digest_and_the_run_id() -> None:
    """P4: `ConfigDigest` と `run_id` の両方が予測どおりで合格。読めなければ `UNREADABLE`。"""
    same = {
        "expected_config_digest_hex": "c" * 64,
        "expected_run_id_hex": "d" * 64,
    }
    assert (
        check_run_matches(
            **same, observed_config_digest_hex="c" * 64, observed_run_id_hex="d" * 64
        ).outcome
        is CheckOutcome.PASSED
    )
    assert (
        check_run_matches(
            **same, observed_config_digest_hex="e" * 64, observed_run_id_hex="d" * 64
        ).outcome
        is CheckOutcome.FAILED
    )
    assert (
        check_run_matches(
            **same, observed_config_digest_hex="c" * 64, observed_run_id_hex="f" * 64
        ).outcome
        is CheckOutcome.FAILED
    )
    unreadable = check_run_matches(
        **same,
        observed_config_digest_hex=None,
        observed_run_id_hex="d" * 64,
        manifest_detail="manifest.json does not exist",
    )
    assert unreadable.outcome is CheckOutcome.UNREADABLE
    assert unreadable.stage is PolicyCheckStage.POST_RUN


def test_the_evaluation_rule_must_match_the_metric_set_version() -> None:
    """P5: 評価の指標集合の版が記録票と一致して合格（D07 §20.3）。"""
    assert check_evaluation_rule(2, 2).outcome is CheckOutcome.PASSED
    assert check_evaluation_rule(2, 1).outcome is CheckOutcome.FAILED


# --- 集約 ----------------------------------------------------------------------


def test_only_passed_counts_as_passed_and_failures_keep_the_declared_order() -> None:
    """全件合格は `PASSED` だけ。合格でない検査名は P1〜P6 の宣言順で返す（D07 §19.3）。"""
    results = (
        check_complexity(
            ComplexityMeasures(component_kinds=1, instances=1, parameters=1, decision_outputs=None),
            LIMITS,
        ),
        check_hypothesis(""),
        check_research_history_only({"x": AccessClass.RESEARCH_HISTORY}),
    )
    assert not all_passed(results)
    assert failed_checks(results) == (
        PolicyCheck.HYPOTHESIS_PRESENT,
        PolicyCheck.COMPLEXITY_WITHIN_LIMITS,
    )


def test_a_check_result_is_bound_to_its_stage() -> None:
    """検査ごとの時点は固定である（D07 §20.3 の表）。別の時点の結果は作れない。"""
    with pytest.raises(KernelValueError):
        PolicyCheckResult(
            check=PolicyCheck.HYPOTHESIS_PRESENT,
            stage=PolicyCheckStage.POST_RUN,
            outcome=CheckOutcome.PASSED,
            expected="",
            observed="",
        )
