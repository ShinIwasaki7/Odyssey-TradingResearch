"""探索の実験のレポートが使う純粋関数の意味論（D09 §10.10・§10.4・§8・§11.5。段階5 実装 PR 5）。

- #15 の後半（`test_15_*`）: 台帳の数え方 (a)(b) は、その実行の開始の行より前の完全な行だけを
  数える。後から台帳が伸びても数字は変わらない（D09 §10.10、D07 §22.1 の決定論）。(a) は用途・
  戦略によらず検証区間の重なりで数え、(b) は同じ戦略の先行の実行を、開始の行より前にある結末の
  行の判定とともに並べる。
- #25 の材料（`test_25_*`）: 途中で止まった実行の検証済みの fold の最低条件は、判定と同じ比べ方で
  結果を出し、入力が無いことによる値なし（`INPUT_NOT_AVAILABLE`）は比べずに理由を返す（D09 §10.4。
  Q36 決定）。
- 頑健性の表の中央値は判定の `MEDIAN` と同じ規則（偶数個なら中央の2つの平均。D09 §8・§7.3）。

台帳の行・単位の結果はすべて人工のもの（テスト用）。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import MetricId, MetricUnavailableReason
from odyssey_fx.evaluation.domain.search import (
    Comparator,
    ConditionOutcome,
    ConditionResult,
    MetricCondition,
    SearchVerdict,
    StandardPurpose,
    count_prior_executions,
    finished_line_of,
    interim_floor_results,
    median_of,
)
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from tests.fixtures.evaluation.search_units import standard, validation_unit
from tests.fixtures.evaluation.trial_ledger import chain, finished, started

_OTHER_INTERVAL = (
    Interval(
        start=UtcTime.parse("2016-01-08T22:00:00Z"), end=UtcTime.parse("2016-01-12T22:00:00Z")
    ),
)


def test_15_the_counts_use_only_the_lines_before_the_started_line() -> None:
    """(a)(b) は開始の行より前だけ。後ろに足された行（同じ区間・同じ戦略）は数えない。"""
    earlier = started("earlier")
    mine = started("mine")
    later = started("later")
    lines = chain([earlier, finished(earlier, verdict=SearchVerdict.BELOW_STANDARD), mine, later])
    counts = count_prior_executions(lines, lines[2])
    assert (counts.overlapping_executions, counts.overlapping_trials) == (1, earlier.trial_count)
    assert [item.experiment_name for item in counts.same_strategy] == ["exp_earlier"]
    assert counts.same_strategy[0].verdict is SearchVerdict.BELOW_STANDARD

    grown = chain([earlier, finished(earlier), mine, later, finished(later), finished(mine)])
    regrown = count_prior_executions(grown, grown[2])
    assert (regrown.overlapping_executions, regrown.overlapping_trials) == (1, 4)


def test_15_a_finished_line_after_the_started_line_is_not_read_for_b() -> None:
    """(b) の判定は開始の行より前にある結末の行だけから写す（無ければ「結末の行なし」）。"""
    earlier = started("earlier")
    mine = started("mine")
    lines = chain([earlier, mine, finished(earlier, verdict=SearchVerdict.BELOW_STANDARD)])
    counts = count_prior_executions(lines, lines[1])
    assert counts.same_strategy[0].verdict is None
    assert finished_line_of(lines, earlier.experiment_id, 1) is lines[2]


def test_15_a_counts_overlap_regardless_of_purpose_and_strategy_while_b_needs_the_strategy() -> (
    None
):
    """(a) は用途・戦略・名前によらず検証区間の重なりで数える。重ならない行は数えない。"""
    standard_line = started("std", purpose=StandardPurpose.STANDARD)
    other_strategy = replace(started("other"), strategy_id="another_strategy")
    disjoint = replace(started("far"), validation_intervals=_OTHER_INTERVAL)
    mine = started("mine")
    lines = chain([standard_line, other_strategy, disjoint, mine])
    counts = count_prior_executions(lines, lines[3])
    assert counts.overlapping_executions == 2
    assert counts.overlapping_trials == 8
    assert [item.experiment_name for item in counts.same_strategy] == ["exp_std", "exp_far"]
    assert counts.same_strategy[0].purpose is StandardPurpose.STANDARD


def test_15_the_started_line_must_be_a_started_line_of_the_ledger() -> None:
    lines = chain([started("a")])
    with pytest.raises(KernelValueError):
        count_prior_executions(lines, chain([started("b")])[0])
    closing = chain([started("a"), finished(started("a"))])
    with pytest.raises(KernelValueError):
        count_prior_executions(closing, closing[1])


def test_25_interim_floor_results_compare_like_the_judgement_and_keep_input_not_available() -> None:
    rule = standard(
        floors=(
            MetricCondition(MetricId.MAX_DRAWDOWN_MTM_RATE, Comparator.LE, Decimal("0.2")),
            MetricCondition(MetricId.NET_RETURN_RATE, Comparator.GE, Decimal("0")),
            MetricCondition(MetricId.NET_PROFIT, Comparator.GE, Decimal("0")),
        )
    ).validation
    unit = validation_unit(
        0,
        {
            MetricId.MAX_DRAWDOWN_MTM_RATE: Decimal("0.5"),
            MetricId.NET_RETURN_RATE: MetricUnavailableReason.INPUT_NOT_AVAILABLE,
            MetricId.NET_PROFIT: MetricUnavailableReason.NO_TRADES,
        },
    )
    results = interim_floor_results(0, rule, unit)
    assert [condition.metric for condition, _ in results] == [
        MetricId.MAX_DRAWDOWN_MTM_RATE,
        MetricId.NET_RETURN_RATE,
        MetricId.NET_PROFIT,
    ]
    first, second, third = (result for _, result in results)
    assert isinstance(first, ConditionResult) and first.outcome is ConditionOutcome.NOT_MET
    assert second is MetricUnavailableReason.INPUT_NOT_AVAILABLE
    assert isinstance(third, ConditionResult) and third.outcome is ConditionOutcome.UNCOMPUTABLE
    assert third.unavailable_reason is MetricUnavailableReason.NO_TRADES


def test_25_interim_floor_results_need_a_completed_evaluation() -> None:
    unit = validation_unit(0, evaluation_status=EvaluationStatus.REJECTED)
    with pytest.raises(KernelValueError):
        interim_floor_results(0, standard().validation, unit)


def test_the_median_of_the_spread_table_follows_the_judgement_rule() -> None:
    assert median_of([Decimal("3"), Decimal("1"), Decimal("2")]) == Decimal("2")
    assert median_of([Decimal("1"), Decimal("2")]) == Decimal("1.5")
    with pytest.raises(KernelValueError):
        median_of([])
