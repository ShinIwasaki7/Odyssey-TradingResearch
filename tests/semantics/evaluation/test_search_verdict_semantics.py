"""D09 の選定・判定・頻度区分の意味論の行（D08 §7.4。段階5 実装 PR 3）。

1行1テストで名前を付けて固定する（D01 §9、D08 §2.1）。入力はすべて人工の単位の結果である。

- #1（`test_1_*`）: 選定関数は検証区間の結果を受け取れない（型）。
- #2（`test_2_*`）: 選定の同点は `trial_index` の小さい方を選ぶ。
- #3（`test_3_*`）: 値なしの指標を持つ試行は候補から外れる。
- #5（`test_5_*`）: 試行の状態の導出（試行済み・未試行・失敗・中断の4区分）。
- #11（`test_11_*`）: 取引件数が fold ごとの要件以上の fold で最低条件を割れば、他の fold の値と
  中央値によらず実験の判定は「満たさない」（D09 §7.3 の実験の判定の手順1）。
- #12（`test_12_*`）: 取引が無く判定に使う指標が値なしの fold は「満たさない」ではなく
  「証拠不足」になる（D09 §7.3 の fold の判定の手順3・5）。
- #13（`test_13_*`）: 頻度区分の関数は選定記録だけを入力にし、検証区間の結果を受け取れない
  （型）。区分は成績の条件を変えない（D09 §7.8）。
- #14（`test_14_*`）: 判定の順序（満たさない → 判定できない → 証拠不足 → 集約条件）。
- #16（`test_16_*`）: 取引が少ない fold で最低条件を割ると、fold の判定は「証拠不足」、理由は
  `FLOOR_NOT_MET_TRADES_BELOW`、その条件の結果は `NOT_MET` のまま残り、実験の判定は「満たす」に
  ならない（レポートの判定の欄の表示は後続の実装 PR で足す）。
- #17 の判定の部分（`test_17_*`）: 用途が `MECHANISM_CHECK` なら、`STANDARD` で `MEETS_STANDARD`
  になる入力が `MET_IN_MECHANISM_CHECK` になり、手順1〜3 の値は用途によらず同じ。

#4・#6〜#8・#15 は探索の実行と記録（後続の実装 PR）で足す。
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Mapping, Sequence
from decimal import Decimal

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.domain.metrics import MetricId, MetricUnavailableReason
from odyssey_fx.evaluation.domain.search import (
    CandidateStatus,
    Comparator,
    ConditionOutcome,
    EvaluationStandard,
    FoldEvidence,
    FoldSelection,
    FoldVerdict,
    FrequencyClass,
    MetricCondition,
    SearchOutcome,
    SearchVerdict,
    SelectionDirection,
    StandardPurpose,
    SufficiencyShortfallKind,
    TrainUnitEvaluation,
    TrialStatus,
    ValidationUnitEvaluation,
    assess_frequency,
    build_search_outcome,
    derive_trial_status,
    select_trial,
)
from odyssey_fx.evaluation.domain.splits import Fold
from tests.fixtures.evaluation.search_units import (
    Override,
    evidence,
    make_fold,
    standard,
    train_unit,
    validation_unit,
)

DD = MetricId.MAX_DRAWDOWN_MTM_RATE
NRR = MetricId.NET_RETURN_RATE
TC = MetricId.TRADE_COUNT


FoldSpec = tuple[list[TrainUnitEvaluation], Mapping[MetricId, Override] | None]


def _outcome(rule: EvaluationStandard, folds_spec: Sequence[FoldSpec]) -> SearchOutcome:
    """`folds_spec` は fold ごとの (選定区間の単位の列, 検証区間の指標の上書き | None)。"""
    items: list[FoldEvidence] = []
    for index, (trains, validation) in enumerate(folds_spec):
        fold = make_fold(index)
        selection = select_trial(index, rule.selection, trains)
        unit = (
            None
            if selection.selected_trial_index is None
            else validation_unit(selection.selected_trial_index, validation)
        )
        items.append(evidence(rule, fold, trains, unit))
    return build_search_outcome(rule, items, [TrialStatus.COMPLETED], ledger_execution=1)


# --- #1 -------------------------------------------------------------------------


def test_1_selection_function_cannot_receive_validation_results() -> None:
    hints = typing.get_type_hints(select_trial)
    assert hints["units"] == Sequence[TrainUnitEvaluation]
    assert not issubclass(ValidationUnitEvaluation, TrainUnitEvaluation)
    rule = standard()
    with pytest.raises(KernelValueError, match="TrainUnitEvaluation"):
        select_trial(0, rule.selection, [validation_unit(0)])  # type: ignore[list-item]


# --- #2 -------------------------------------------------------------------------


@pytest.mark.parametrize("direction", list(SelectionDirection))
def test_2_tie_is_broken_by_the_smallest_trial_index(direction: SelectionDirection) -> None:
    rule = standard(direction=direction)
    units = [
        train_unit(3, {NRR: Decimal("0.5")}),
        train_unit(1, {NRR: Decimal("0.50")}),
        train_unit(2, {NRR: Decimal("0.5000")}),
    ]
    selection = select_trial(0, rule.selection, units)
    assert selection.selected_trial_index == 1


# --- #3 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (MetricUnavailableReason.NO_TRADES, CandidateStatus.EXCLUDED_METRIC_UNAVAILABLE),
        (MetricUnavailableReason.NO_OBSERVATIONS, CandidateStatus.EXCLUDED_METRIC_UNAVAILABLE),
        (
            MetricUnavailableReason.UNDEFINED_DENOMINATOR,
            CandidateStatus.EXCLUDED_METRIC_UNAVAILABLE,
        ),
        (MetricUnavailableReason.INPUT_NOT_AVAILABLE, CandidateStatus.EXCLUDED_NOT_COMPLETED),
    ],
)
def test_3_trials_with_unavailable_metrics_drop_out_of_the_candidates(
    reason: MetricUnavailableReason, expected: CandidateStatus
) -> None:
    # MINIMIZE でも値なしの試行を「0」や「最下位」として並べない。
    rule = standard(direction=SelectionDirection.MINIMIZE)
    units = [train_unit(0, {NRR: reason}), train_unit(1, {NRR: Decimal("0.3")})]
    selection = select_trial(0, rule.selection, units)
    assert selection.selected_trial_index == 1
    assert dict((index, status) for index, _, status in selection.inputs)[0] is expected


# --- #5 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("compile_rejected", "started", "recorded", "expected"),
    [
        (True, False, False, TrialStatus.FAILED),
        (False, True, True, TrialStatus.COMPLETED),
        (False, False, True, TrialStatus.COMPLETED),
        (False, True, False, TrialStatus.ABORTED),
        (False, False, False, TrialStatus.NOT_STARTED),
    ],
)
def test_5_trial_status_is_derived_into_four_states(
    compile_rejected: bool, started: bool, recorded: bool, expected: TrialStatus
) -> None:
    assert (
        derive_trial_status(compile_rejected=compile_rejected, started=started, recorded=recorded)
        is expected
    )


# --- #11 ------------------------------------------------------------------------


def test_11_floor_breach_with_enough_trades_is_below_standard_regardless_of_others() -> None:
    rule = standard()
    good: dict[MetricId, Override] = {NRR: Decimal("0.9"), DD: Decimal("0.01"), TC: 50}
    outcome = _outcome(
        rule,
        [
            ([train_unit(0)], {**good, DD: Decimal("0.6")}),  # 取引 50 件で最低条件を割る
            ([train_unit(0)], good),
            ([train_unit(0)], good),
            ([train_unit(0)], good),
            ([train_unit(0)], good),
        ],
    )
    assert outcome.fold_verdicts[0] == (0, FoldVerdict.FLOOR_BREACHED)
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD
    # 中央値の条件は判定しない（手順1 で決まったため）。
    assert all(item.fold_index is not None for item in outcome.condition_results)


# --- #12 ------------------------------------------------------------------------


def test_12_no_trades_with_unavailable_metrics_is_insufficient_evidence() -> None:
    rule = standard()
    no_trades: dict[MetricId, Override] = {
        TC: 0,
        DD: MetricUnavailableReason.NO_OBSERVATIONS,
        NRR: Decimal("0"),
    }
    outcome = _outcome(rule, [([train_unit(0)], no_trades), ([train_unit(0)], {TC: 30})])
    assert outcome.fold_verdicts[0] == (0, FoldVerdict.INSUFFICIENT_EVIDENCE)
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    kinds = {(item.kind, item.metric) for item in outcome.shortfalls if item.fold_index == 0}
    assert (SufficiencyShortfallKind.FOLD_TRADES_BELOW, None) in kinds
    assert (SufficiencyShortfallKind.METRIC_UNCOMPUTABLE, DD) in kinds


def test_12_unavailable_metric_without_a_trade_requirement_is_insufficient_evidence() -> None:
    # 手順5: 取引件数の要件（0 件）は満たすが、判定に使う指標が観測不足で値なし。
    zero = FrequencyClass(
        name="ALL",
        min_train_trades_per_365d=Decimal("0"),
        min_validation_trades_per_fold=0,
        min_validation_trades_total=0,
    )
    rule = standard(classes=(zero,))
    outcome = _outcome(
        rule, [([train_unit(0)], {TC: 0, DD: MetricUnavailableReason.NO_OBSERVATIONS})]
    )
    assert outcome.fold_verdicts == ((0, FoldVerdict.INSUFFICIENT_EVIDENCE),)
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert outcome.condition_results[0].outcome is ConditionOutcome.UNCOMPUTABLE


# --- #13 ------------------------------------------------------------------------


def test_13_frequency_takes_only_selection_records() -> None:
    hints = typing.get_type_hints(assess_frequency)
    assert hints["selections"] == Sequence[FoldSelection]
    assert hints["folds"] == Sequence[Fold]
    assert set(inspect.signature(assess_frequency).parameters) == {"selections", "folds", "rule"}
    rule = standard()
    with pytest.raises(KernelValueError, match="FoldSelection"):
        assess_frequency([validation_unit(0)], [make_fold(0)], rule.sufficiency)  # type: ignore[list-item]


def test_13_frequency_class_does_not_change_performance_conditions() -> None:
    high = FrequencyClass("HIGH", Decimal("1"), 50, 100)
    low = FrequencyClass("LOW", Decimal("0"), 0, 0)
    spec: list[FoldSpec] = [([train_unit(0, {TC: 40})], {TC: 6, DD: Decimal("0.3")})]
    in_high = _outcome(standard(classes=(high, low)), spec)
    in_low = _outcome(
        standard(classes=(FrequencyClass("HIGH", Decimal("1000"), 50, 100), low)), spec
    )
    assert in_high.frequency is not None and in_high.frequency.class_name == "HIGH"
    assert in_low.frequency is not None and in_low.frequency.class_name == "LOW"
    # 成績の条件の結果は区分によらず同じ（区分が変えるのは証拠の要件だけ）。
    assert in_high.condition_results == in_low.condition_results
    assert in_high.condition_results[0].outcome is ConditionOutcome.NOT_MET
    assert in_high.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE  # 取引 6 < 50
    assert in_low.verdict is SearchVerdict.BELOW_STANDARD  # 要件を満たし最低条件を割った


# --- #14 ------------------------------------------------------------------------

_BREACH: FoldSpec = ([train_unit(0)], {TC: 30, DD: Decimal("0.9")})
_INCOMPLETE: FoldSpec = ([train_unit(0, status=TrialStatus.ABORTED)], None)
_SHORT: FoldSpec = ([train_unit(0)], {TC: 1})
_GOOD: FoldSpec = ([train_unit(0)], {TC: 30})
_WEAK: FoldSpec = ([train_unit(0)], {TC: 30, NRR: Decimal("-0.1")})


@pytest.mark.parametrize(
    ("folds", "expected"),
    [
        ([_INCOMPLETE, _SHORT, _BREACH], SearchVerdict.BELOW_STANDARD),
        ([_SHORT, _INCOMPLETE], SearchVerdict.INCOMPLETE),
        ([_SHORT, _GOOD], SearchVerdict.INSUFFICIENT_EVIDENCE),
        ([_WEAK, _WEAK], SearchVerdict.BELOW_STANDARD),
        ([_GOOD, _GOOD], SearchVerdict.MEETS_STANDARD),
    ],
)
def test_14_verdict_order_is_below_incomplete_insufficient_aggregate(
    folds: list[FoldSpec],
    expected: SearchVerdict,
) -> None:
    assert _outcome(standard(), folds).verdict is expected


# --- #16 ------------------------------------------------------------------------


def test_16_floor_breach_in_a_low_trade_fold_is_insufficient_evidence_with_the_fact_kept() -> None:
    rule = standard()
    outcome = _outcome(
        rule,
        [
            ([train_unit(0)], {TC: 2, DD: Decimal("0.9")}),
            ([train_unit(0)], {TC: 30}),
        ],
    )
    assert outcome.fold_verdicts[0] == (0, FoldVerdict.INSUFFICIENT_EVIDENCE)
    shortfall = next(item for item in outcome.shortfalls if item.fold_index == 0)
    assert shortfall.kind is SufficiencyShortfallKind.FLOOR_NOT_MET_TRADES_BELOW
    assert (shortfall.trial_index, shortfall.required, shortfall.observed) == (0, 5, 2)
    floor = next(item for item in outcome.condition_results if item.fold_index == 0)
    assert floor.outcome is ConditionOutcome.NOT_MET
    assert floor.observed == Decimal("0.9")
    # 実験の判定は「満たす」にならない（証拠不足より良い判定にならない）。
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE


# --- #17（判定の部分） --------------------------------------------------------------


def test_17_mechanism_check_turns_meets_standard_into_met_in_mechanism_check() -> None:
    spec = [_GOOD, _GOOD]
    as_standard = _outcome(standard(purpose=StandardPurpose.STANDARD), spec)
    as_mechanism = _outcome(standard(purpose=StandardPurpose.MECHANISM_CHECK), spec)
    assert as_standard.verdict is SearchVerdict.MEETS_STANDARD
    assert as_mechanism.verdict is SearchVerdict.MET_IN_MECHANISM_CHECK
    assert as_mechanism.purpose is StandardPurpose.MECHANISM_CHECK
    assert as_standard.condition_results == as_mechanism.condition_results


@pytest.mark.parametrize(
    "folds",
    [[_BREACH, _GOOD], [_INCOMPLETE, _GOOD], [_SHORT, _GOOD], [_WEAK, _WEAK]],
)
def test_17_steps_one_to_three_do_not_depend_on_the_purpose(
    folds: list[FoldSpec],
) -> None:
    as_standard = _outcome(standard(purpose=StandardPurpose.STANDARD), folds)
    as_mechanism = _outcome(standard(purpose=StandardPurpose.MECHANISM_CHECK), folds)
    assert as_standard.verdict is as_mechanism.verdict
    assert as_standard.fold_verdicts == as_mechanism.fold_verdicts
    assert as_standard.shortfalls == as_mechanism.shortfalls


@pytest.mark.parametrize("purpose", list(StandardPurpose))
@pytest.mark.parametrize(
    "others",
    [[], [_INCOMPLETE], [_SHORT], [_INCOMPLETE, _SHORT, _GOOD]],
)
def test_14_fold_without_eligible_trial_makes_the_search_below_standard_first(
    purpose: StandardPurpose, others: list[FoldSpec]
) -> None:
    # 足切り「純収益率 0 以上」を通る試行が無い fold は、判定できない fold・証拠不足の fold が
    # あっても、実験の判定を「満たさない」にする（手順1。用途によらない）。
    rule = standard(
        purpose=purpose,
        eligibility=(MetricCondition(NRR, Comparator.GE, Decimal("0")),),
    )
    no_eligible: FoldSpec = ([train_unit(0, {NRR: Decimal("-0.2")})], None)
    outcome = _outcome(rule, [no_eligible, *others])
    assert outcome.fold_verdicts[0] == (0, FoldVerdict.NO_ELIGIBLE_TRIAL)
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD
