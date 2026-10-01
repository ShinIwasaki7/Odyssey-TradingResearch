"""評価基準の型と、研究ポリシーを読むときの検査 E1〜E4（D09 §7.1〜§7.3・§7.8。段階5 実装 PR 1）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import METRIC_KINDS, MetricId, MetricKind
from odyssey_fx.evaluation.domain.search import (
    REFERENCE_METRICS,
    AggregateCondition,
    Comparator,
    EvaluationStandard,
    FoldStatistic,
    FrequencyClass,
    MetricCondition,
    SelectionDirection,
    SelectionRule,
    StandardPurpose,
    SufficiencyRule,
    ValidationRule,
    is_selectable_metric,
)
from odyssey_fx.evaluation.domain.splits import SplitStandard, SplitWindow


def _floor(
    metric: MetricId = MetricId.MAX_DRAWDOWN_MTM_RATE, comparator: Comparator = Comparator.LE
) -> MetricCondition:
    return MetricCondition(metric=metric, comparator=comparator, threshold=Decimal("0.2"))


def _class(name: str, rate: str, per_fold: int = 1, total: int = 1) -> FrequencyClass:
    return FrequencyClass(
        name=name,
        min_train_trades_per_365d=Decimal(rate),
        min_validation_trades_per_fold=per_fold,
        min_validation_trades_total=total,
    )


# --- E1 ----------------------------------------------------------------------


def test_only_adopted_ratio_and_amount_metrics_are_selectable() -> None:
    """E1: 比率か金額の採用指標だけ。参考値・#3・時間・価格差は書けない（D09 §7.1）。"""
    selectable = {metric for metric in MetricId if is_selectable_metric(metric)}
    expected = {
        metric
        for metric, kind in METRIC_KINDS.items()
        if kind in (MetricKind.RATIO, MetricKind.AMOUNT) and metric not in REFERENCE_METRICS
    }
    assert selectable == expected
    assert MetricId.TRADE_COUNT not in selectable
    assert MetricId.MAX_ADVERSE_FILL_OFFSET not in selectable
    assert REFERENCE_METRICS == {
        MetricId.MAX_DRAWDOWN_BALANCE,
        MetricId.MAX_DRAWDOWN_BALANCE_RATE,
        MetricId.COST_PRICE_EMBEDDED_TOTAL,
        MetricId.END_EQUITY_MTM,
        MetricId.HYPOTHETICAL_CLOSED_PROFIT,
    }


@pytest.mark.parametrize(
    ("metric", "word"),
    [
        (MetricId.TRADE_COUNT, "取引件数"),
        (MetricId.END_EQUITY_MTM, "参考値"),
        (MetricId.MAX_ADVERSE_FILL_OFFSET, "PRICE_OFFSET"),
    ],
)
def test_conditions_and_selection_refuse_unselectable_metrics(metric: MetricId, word: str) -> None:
    """E1 は足切り・最低条件・集約条件・選定の指標のすべてに当たる。"""
    with pytest.raises(KernelValueError, match=word):
        MetricCondition(metric=metric, comparator=Comparator.GE, threshold=Decimal("1"))
    with pytest.raises(KernelValueError, match=word):
        AggregateCondition(
            metric=metric,
            statistic=FoldStatistic.MEDIAN,
            comparator=Comparator.GE,
            threshold=Decimal("1"),
        )
    with pytest.raises(KernelValueError, match=word):
        SelectionRule(metric=metric, direction=SelectionDirection.MAXIMIZE, eligibility=())


def test_a_threshold_must_be_a_finite_decimal() -> None:
    """閾値は `Decimal`（D09 §7.1。`float` を使わない）。"""
    with pytest.raises(KernelValueError):
        MetricCondition(
            metric=MetricId.NET_RETURN_RATE,
            comparator=Comparator.GE,
            threshold=0.1,  # type: ignore[arg-type]
        )
    with pytest.raises(KernelValueError):
        MetricCondition(
            metric=MetricId.NET_RETURN_RATE, comparator=Comparator.GE, threshold=Decimal("NaN")
        )


# --- E2・E3 ------------------------------------------------------------------


def test_fold_floors_must_not_be_empty_but_aggregate_and_eligibility_may() -> None:
    """E2: 最低条件の無い評価基準は書けない。集約条件と足切りの空は許す。"""
    with pytest.raises(KernelValueError, match="E2"):
        ValidationRule(fold_floors=(), aggregate=())
    assert ValidationRule(fold_floors=(_floor(),), aggregate=()).aggregate == ()
    assert (
        SelectionRule(
            metric=MetricId.NET_RETURN_RATE, direction=SelectionDirection.MAXIMIZE, eligibility=()
        ).eligibility
        == ()
    )


def test_the_same_condition_is_not_written_twice_in_one_list() -> None:
    """E3: 同じ一覧に `(metric, comparator)`（集約条件は statistic も）が同じ条件を2つ書かない。"""
    with pytest.raises(KernelValueError, match="E3"):
        ValidationRule(fold_floors=(_floor(), _floor()), aggregate=())
    with pytest.raises(KernelValueError, match="E3"):
        SelectionRule(
            metric=MetricId.NET_RETURN_RATE,
            direction=SelectionDirection.MAXIMIZE,
            eligibility=(_floor(), _floor()),
        )
    median = AggregateCondition(
        metric=MetricId.NET_RETURN_RATE,
        statistic=FoldStatistic.MEDIAN,
        comparator=Comparator.GE,
        threshold=Decimal("0"),
    )
    with pytest.raises(KernelValueError, match="E3"):
        ValidationRule(fold_floors=(_floor(),), aggregate=(median, median))
    # 比較が違えば別の条件（範囲の上下を書ける）。
    both = ValidationRule(
        fold_floors=(_floor(comparator=Comparator.LE), _floor(comparator=Comparator.GE)),
        aggregate=(),
    )
    assert [item.comparator for item in both.fold_floors] == [Comparator.LE, Comparator.GE]


# --- E4 ----------------------------------------------------------------------


def test_frequency_classes_follow_the_e4_rules() -> None:
    """E4: 1つ以上、名前が一意で `[A-Z][A-Z0-9_]*`、厳密に降順、最後の下限が 0、整数が 0 以上。"""
    assert len(SufficiencyRule(classes=(_class("ONLY", "0"),)).classes) == 1
    assert len(SufficiencyRule(classes=(_class("HIGH", "50"), _class("LOW", "0"))).classes) == 2
    with pytest.raises(KernelValueError, match="E4"):
        SufficiencyRule(classes=())
    with pytest.raises(KernelValueError, match="E4"):
        SufficiencyRule(classes=(_class("A", "10"), _class("A", "0")))
    with pytest.raises(KernelValueError, match="E4"):
        SufficiencyRule(classes=(_class("A", "10"), _class("B", "10"), _class("C", "0")))
    with pytest.raises(KernelValueError, match="E4"):
        SufficiencyRule(classes=(_class("A", "10"), _class("B", "1")))
    with pytest.raises(KernelValueError, match="E4"):
        _class("low", "0")
    with pytest.raises(KernelValueError, match="E4"):
        _class("LOW\n", "0")
    with pytest.raises(KernelValueError, match="E4"):
        _class("LOW", "0", per_fold=-1)
    # 下限の書き方の違い（"0.0" と "0"）は同じ値として比べる。
    assert SufficiencyRule(classes=(_class("LOW", "0.0"),)).classes[0].name == "LOW"


# --- EvaluationStandard -----------------------------------------------------


def test_an_evaluation_standard_holds_the_five_groups() -> None:
    """版 3 の評価基準の群は用途・分割・選定・検証・証拠の要件の5つ（D09 §3・§10.9）。"""
    start = UtcTime.parse("2020-01-01T00:00:00Z")
    split = SplitStandard(
        range=Interval(start=start, end=UtcTime.parse("2020-02-01T00:00:00Z")),
        train_seconds=86_400 * 10,
        validation_seconds=86_400 * 5,
        window=SplitWindow.ROLLING,
        purge_seconds=0,
        min_folds=1,
    )
    standard = EvaluationStandard(
        purpose=StandardPurpose.MECHANISM_CHECK,
        split=split,
        selection=SelectionRule(
            metric=MetricId.NET_RETURN_RATE, direction=SelectionDirection.MAXIMIZE, eligibility=()
        ),
        validation=ValidationRule(fold_floors=(_floor(),), aggregate=()),
        sufficiency=SufficiencyRule(classes=(_class("ONLY", "0"),)),
    )
    assert standard.purpose is StandardPurpose.MECHANISM_CHECK
    with pytest.raises(KernelValueError):
        EvaluationStandard(
            purpose="STANDARD",  # type: ignore[arg-type]
            split=split,
            selection=standard.selection,
            validation=standard.validation,
            sufficiency=standard.sufficiency,
        )
