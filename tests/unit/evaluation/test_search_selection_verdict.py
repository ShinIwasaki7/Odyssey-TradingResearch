"""選定・判定・頻度区分・試行の状態の導出の単体テスト（D09 §7.2〜§7.4・§7.8・§10.4・§11.5。PR 3）。

意味論の行（D08 §7.4）は `tests/semantics/evaluation/test_search_verdict_semantics.py` に置き、
ここでは各手順の枝・値の検査・境界を1つずつ確かめる。入力は人工の単位の結果である。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from decimal import Decimal

import pytest

from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import MetricId, MetricUnavailableReason
from odyssey_fx.evaluation.domain.search import (
    AggregateCondition,
    CandidateStatus,
    Comparator,
    ConditionOutcome,
    ConditionResult,
    ConditionScope,
    EvaluationStandard,
    FoldEvidence,
    FoldSelection,
    FoldStatistic,
    FoldVerdict,
    FrequencyAssessment,
    FrequencyClass,
    MetricCondition,
    SearchOutcome,
    SearchVerdict,
    StandardPurpose,
    SufficiencyShortfall,
    SufficiencyShortfallKind,
    TrainUnitEvaluation,
    TrialStatus,
    assess_frequency,
    build_search_outcome,
    candidate_status,
    count_trial_statuses,
    derive_trial_status,
    longest_idle_period,
    select_trial,
    trades_per_365d,
)
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from tests.fixtures.evaluation.search_units import (
    Override,
    digest_for,
    evidence,
    in_fold,
    make_fold,
    standard,
    train_unit,
    validation_unit,
)

DD = MetricId.MAX_DRAWDOWN_MTM_RATE
NRR = MetricId.NET_RETURN_RATE
NP = MetricId.NET_PROFIT
TC = MetricId.TRADE_COUNT
SHARPE = MetricId.ANNUALIZED_SHARPE_RATIO

FoldSpec = tuple[list[TrainUnitEvaluation], Mapping[MetricId, Override] | None]


def _outcome(
    rule: EvaluationStandard,
    folds_spec: Sequence[FoldSpec],
    *,
    validation_kwargs: Mapping[int, Mapping[str, object]] | None = None,
) -> SearchOutcome:
    items: list[FoldEvidence] = []
    for index, (trains, metrics) in enumerate(folds_spec):
        bound = in_fold(trains, index)
        selection = select_trial(index, rule.selection, bound)
        unit = None
        if selection.selected_trial_index is not None:
            extra = dict((validation_kwargs or {}).get(index, {}))
            unit = validation_unit(selection.selected_trial_index, metrics, **extra)  # type: ignore[arg-type]
        items.append(evidence(rule, make_fold(index), trains, unit))
    return build_search_outcome(rule, items, ledger_execution=1)


# --- 試行の状態 ---------------------------------------------------------------------


def test_compile_rejected_unit_with_records_is_a_structural_error() -> None:
    with pytest.raises(KernelValueError, match="コンパイル拒否"):
        derive_trial_status(compile_rejected=True, started=True, recorded=False)


def test_count_trial_statuses_lists_every_state_in_declaration_order() -> None:
    counts = count_trial_statuses([TrialStatus.FAILED, TrialStatus.FAILED, TrialStatus.ABORTED])
    assert counts == (
        (TrialStatus.NOT_STARTED, 0),
        (TrialStatus.COMPLETED, 0),
        (TrialStatus.FAILED, 2),
        (TrialStatus.ABORTED, 1),
    )


# --- 単位の結果の検査 -----------------------------------------------------------------


def test_unit_not_completed_cannot_carry_results() -> None:
    with pytest.raises(KernelValueError, match="試行済みでない"):
        TrainUnitEvaluation(
            fold_index=0,
            trial_index=0,
            status=TrialStatus.ABORTED,
            run_status=RunStatus.COMPLETED,
            run_evaluation_id=None,
            evaluation_status=None,
            post_run_checks_passed=False,
            metrics=(),
        )


def test_unit_with_rejected_evaluation_has_no_metric_rows() -> None:
    unit = train_unit(0, evaluation_status=EvaluationStatus.REJECTED)
    assert unit.metrics == ()
    with pytest.raises(KernelValueError, match="COMPLETED でない"):
        TrainUnitEvaluation(
            fold_index=0,
            trial_index=0,
            status=TrialStatus.COMPLETED,
            run_status=RunStatus.FAILED_CAPABILITY,
            run_evaluation_id=digest_for(1),
            evaluation_status=EvaluationStatus.REJECTED,
            post_run_checks_passed=True,
            metrics=train_unit(1).metrics,
        )


def test_unit_never_carries_aborted_evaluation() -> None:
    with pytest.raises(KernelValueError, match="ABORTED"):
        TrainUnitEvaluation(
            fold_index=0,
            trial_index=0,
            status=TrialStatus.COMPLETED,
            run_status=RunStatus.COMPLETED,
            run_evaluation_id=digest_for(1),
            evaluation_status=EvaluationStatus.ABORTED,
            post_run_checks_passed=True,
            metrics=(),
        )


# --- 候補の区分（D09 §7.4） ---------------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        (train_unit(0, status=TrialStatus.FAILED), CandidateStatus.EXCLUDED_TRIAL_FAILED),
        (train_unit(0, status=TrialStatus.NOT_STARTED), CandidateStatus.EXCLUDED_NOT_COMPLETED),
        (train_unit(0, status=TrialStatus.ABORTED), CandidateStatus.EXCLUDED_NOT_COMPLETED),
        (
            train_unit(0, run_status=RunStatus.FAILED_CAPABILITY),
            CandidateStatus.EXCLUDED_NOT_COMPLETED,
        ),
        (
            train_unit(0, evaluation_status=EvaluationStatus.FAILED),
            CandidateStatus.EXCLUDED_NOT_COMPLETED,
        ),
        (
            # 入力が無い値なしは、事後検査の不合格より先に「完了していない」。
            train_unit(
                0,
                {NRR: MetricUnavailableReason.INPUT_NOT_AVAILABLE},
                post_run_checks_passed=False,
            ),
            CandidateStatus.EXCLUDED_NOT_COMPLETED,
        ),
        (
            # 事後検査の不合格は、観測不足の値なしより先。
            train_unit(0, {NRR: MetricUnavailableReason.NO_TRADES}, post_run_checks_passed=False),
            CandidateStatus.EXCLUDED_POST_RUN_CHECK,
        ),
        (
            train_unit(0, {NRR: MetricUnavailableReason.NO_TRADES}),
            CandidateStatus.EXCLUDED_METRIC_UNAVAILABLE,
        ),
        (train_unit(0), CandidateStatus.CANDIDATE),
    ],
)
def test_candidate_status_follows_the_table_order(
    unit: TrainUnitEvaluation, expected: CandidateStatus
) -> None:
    assert candidate_status(standard().selection, unit) is expected


def test_eligibility_metric_unavailable_excludes_the_trial() -> None:
    rule = standard(eligibility=(MetricCondition(SHARPE, Comparator.GE, Decimal("0")),))
    unit = train_unit(0, {SHARPE: MetricUnavailableReason.UNDEFINED_DENOMINATOR})
    assert candidate_status(rule.selection, unit) is CandidateStatus.EXCLUDED_METRIC_UNAVAILABLE


# --- 選定（D09 §7.2） ---------------------------------------------------------------


def test_selection_applies_eligibility_then_maximizes_and_records_all_trials() -> None:
    rule = standard(eligibility=(MetricCondition(DD, Comparator.LE, Decimal("0.3")),))
    units = [
        train_unit(0, {NRR: Decimal("0.9"), DD: Decimal("0.5")}),  # 足切りで外れる
        train_unit(1, {NRR: Decimal("0.2"), DD: Decimal("0.1"), TC: 17}),
        train_unit(2, status=TrialStatus.FAILED),
        train_unit(3, {NRR: Decimal("0.1"), DD: Decimal("0.1")}),
    ]
    selection = select_trial(4, rule.selection, in_fold(units, 4))
    assert selection.fold_index == 4
    assert selection.selected_trial_index == 1
    assert selection.selected_value == Decimal("0.2")
    assert selection.selected_train_trade_count == 17
    assert selection.inputs == (
        (0, digest_for(1000), CandidateStatus.EXCLUDED_INELIGIBLE),
        (1, digest_for(1001), CandidateStatus.CANDIDATE),
        (2, None, CandidateStatus.EXCLUDED_TRIAL_FAILED),
        (3, digest_for(1003), CandidateStatus.CANDIDATE),
    )


def test_selection_compares_amounts_by_their_number() -> None:
    rule = standard(selection_metric=NP)
    units = [train_unit(0, {NP: Decimal("-5")}), train_unit(1, {NP: Decimal("12.5")})]
    assert select_trial(0, rule.selection, units).selected_value == Decimal("12.5")


def test_selection_without_candidates_selects_nothing() -> None:
    rule = standard()
    selection = select_trial(0, rule.selection, [train_unit(0, status=TrialStatus.FAILED)])
    assert selection.selected_trial_index is None
    assert selection.selected_value is None
    assert selection.selected_train_trade_count is None


def test_selection_rejects_duplicate_trial_indices() -> None:
    with pytest.raises(KernelValueError, match="重複"):
        select_trial(0, standard().selection, [train_unit(0), train_unit(0)])


def test_fold_selection_rejects_a_selected_trial_that_is_not_a_candidate() -> None:
    with pytest.raises(KernelValueError, match="CANDIDATE"):
        FoldSelection(
            fold_index=0,
            selected_trial_index=0,
            selected_value=Decimal("1"),
            selected_train_trade_count=1,
            inputs=((0, None, CandidateStatus.EXCLUDED_TRIAL_FAILED),),
        )


# --- 頻度区分（D09 §7.8） ------------------------------------------------------------


def test_trades_per_365d_divides_once_at_kernel_precision() -> None:
    assert trades_per_365d(10, 365 * 86_400) == Decimal("10")
    assert trades_per_365d(1, 3 * 365 * 86_400) == Decimal(1) / Decimal(3)


def test_frequency_sums_only_folds_with_a_selected_trial() -> None:
    classes = (
        FrequencyClass("HIGH", Decimal("100"), 10, 50),
        FrequencyClass("MID", Decimal("20"), 5, 20),
        FrequencyClass("LOW", Decimal("0"), 1, 5),
    )
    rule = standard(classes=classes)
    folds = [make_fold(0), make_fold(1), make_fold(2)]
    selections = [
        select_trial(0, rule.selection, [train_unit(0, {TC: 30})]),
        select_trial(1, rule.selection, in_fold([train_unit(0, status=TrialStatus.FAILED)], 1)),
        select_trial(2, rule.selection, in_fold([train_unit(0, {TC: 10})], 2)),
    ]
    assessment = assess_frequency(selections, folds, rule.sufficiency)
    assert assessment == FrequencyAssessment("MID", 40, 2 * 365 * 86_400)
    assert assessment.trades_per_365d == Decimal("20")  # 下限ちょうどは区分に入る


def test_frequency_is_none_without_any_selected_trial() -> None:
    rule = standard()
    selection = select_trial(0, rule.selection, [train_unit(0, status=TrialStatus.FAILED)])
    assert assess_frequency([selection], [make_fold(0)], rule.sufficiency) is None


def test_frequency_rejects_mismatched_folds() -> None:
    rule = standard()
    selection = select_trial(0, rule.selection, [train_unit(0)])
    with pytest.raises(KernelValueError, match="番号の集合"):
        assess_frequency([selection], [make_fold(1)], rule.sufficiency)


# --- fold の判定（D09 §7.3 の手順1〜6） ------------------------------------------------


@pytest.mark.parametrize(
    ("trains", "expected"),
    [
        (  # (a) 失敗で結果の欠けた試行がある → 判定できない（観測不足の試行があっても）
            [
                train_unit(0, status=TrialStatus.ABORTED),
                train_unit(1, {NRR: MetricUnavailableReason.NO_TRADES}),
            ],
            FoldVerdict.INCOMPLETE,
        ),
        ([train_unit(0, post_run_checks_passed=False)], FoldVerdict.INCOMPLETE),
        (  # (b) 観測不足の試行がある → 証拠不足（足切りで外れた試行があっても）
            [
                train_unit(0, {NRR: MetricUnavailableReason.NO_TRADES}),
                train_unit(1, {NRR: Decimal("-1")}),
            ],
            FoldVerdict.INSUFFICIENT_EVIDENCE,
        ),
        ([train_unit(0, {NRR: Decimal("-1")})], FoldVerdict.NO_ELIGIBLE_TRIAL),  # (c)
        ([train_unit(0, status=TrialStatus.FAILED)], FoldVerdict.INCOMPLETE),  # (d)
    ],
)
def test_fold_without_candidates_is_judged_from_the_candidate_statuses(
    trains: list[TrainUnitEvaluation], expected: FoldVerdict
) -> None:
    rule = standard(eligibility=(MetricCondition(NRR, Comparator.GE, Decimal("0")),))
    outcome = _outcome(rule, [(trains, None)])
    assert outcome.fold_verdicts == ((0, expected),)
    assert outcome.condition_results == ()  # 候補なしの fold は最低条件の結果を作らない
    assert outcome.frequency is None


def test_no_candidate_shortfalls_are_one_per_trial_metric_and_reason() -> None:
    rule = standard(eligibility=(MetricCondition(SHARPE, Comparator.GE, Decimal("0")),))
    trains = [
        train_unit(
            0,
            {
                NRR: MetricUnavailableReason.NO_TRADES,
                SHARPE: MetricUnavailableReason.UNDEFINED_DENOMINATOR,
            },
        ),
        train_unit(1, {NRR: MetricUnavailableReason.NO_OBSERVATIONS}),
    ]
    outcome = _outcome(rule, [(trains, None)])
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert [(s.trial_index, s.metric, s.reason) for s in outcome.shortfalls] == [
        (0, NRR, MetricUnavailableReason.NO_TRADES),
        (0, SHARPE, MetricUnavailableReason.UNDEFINED_DENOMINATOR),
        (1, NRR, MetricUnavailableReason.NO_OBSERVATIONS),
    ]
    assert {s.kind for s in outcome.shortfalls} == {
        SufficiencyShortfallKind.NO_CANDIDATE_METRIC_UNAVAILABLE
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"run_status": RunStatus.FAILED_CAPABILITY},
        {"evaluation_status": EvaluationStatus.FAILED},
        {"post_run_checks_passed": False},
        {"status": TrialStatus.ABORTED},
    ],
)
def test_selected_fold_without_a_valid_validation_result_is_incomplete(
    kwargs: Mapping[str, object],
) -> None:
    outcome = _outcome(
        standard(),
        [([train_unit(0)], {TC: 30}), ([train_unit(0)], {TC: 30})],
        validation_kwargs={0: kwargs},
    )
    assert outcome.fold_verdicts[0] == (0, FoldVerdict.INCOMPLETE)
    assert outcome.verdict is SearchVerdict.INCOMPLETE
    assert all(item.fold_index != 0 for item in outcome.condition_results)


def test_input_not_available_in_a_judged_metric_makes_the_fold_incomplete() -> None:
    outcome = _outcome(
        standard(), [([train_unit(0)], {TC: 30, NRR: MetricUnavailableReason.INPUT_NOT_AVAILABLE})]
    )
    assert outcome.fold_verdicts == ((0, FoldVerdict.INCOMPLETE),)


def test_low_trade_fold_without_breach_records_fold_trades_below() -> None:
    outcome = _outcome(standard(), [([train_unit(0)], {TC: 4}), ([train_unit(0)], {TC: 30})])
    shortfall = outcome.shortfalls[0]
    assert shortfall.kind is SufficiencyShortfallKind.FOLD_TRADES_BELOW
    assert (shortfall.fold_index, shortfall.required, shortfall.observed) == (0, 5, 4)


def test_uncomputable_floor_is_not_a_breach() -> None:
    zero = FrequencyClass("ALL", Decimal("0"), 0, 0)
    outcome = _outcome(
        standard(classes=(zero,)),
        [([train_unit(0)], {TC: 3, DD: MetricUnavailableReason.NO_OBSERVATIONS})],
    )
    assert outcome.fold_verdicts == ((0, FoldVerdict.INSUFFICIENT_EVIDENCE),)
    result = outcome.condition_results[0]
    assert result.outcome is ConditionOutcome.UNCOMPUTABLE
    assert result.unavailable_reason is MetricUnavailableReason.NO_OBSERVATIONS
    assert result.observed is None


# --- 実験の判定（D09 §7.3 の手順3・4） -------------------------------------------------


def test_total_trades_below_requirement_is_insufficient_evidence() -> None:
    outcome = _outcome(standard(), [([train_unit(0)], {TC: 6}), ([train_unit(0)], {TC: 7})])
    assert outcome.fold_verdicts == ((0, FoldVerdict.FLOORS_MET), (1, FoldVerdict.FLOORS_MET))
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    total = outcome.shortfalls[-1]
    assert total.kind is SufficiencyShortfallKind.TOTAL_TRADES_BELOW
    assert (total.fold_index, total.required, total.observed) == (None, 20, 13)
    # 手順3 で止まった実験は集約条件を判定しない。
    assert all(item.scope is ConditionScope.FOLD_FLOOR for item in outcome.condition_results)


def test_median_of_even_folds_is_the_mean_of_the_two_middle_values() -> None:
    rule = standard(
        aggregate=(
            AggregateCondition(NRR, FoldStatistic.MEDIAN, Comparator.GE, Decimal("0.25")),
            AggregateCondition(NP, FoldStatistic.MEDIAN, Comparator.GT, Decimal("0")),
        )
    )
    values = [Decimal("0.4"), Decimal("-0.3"), Decimal("0.1"), Decimal("0.9")]
    outcome = _outcome(rule, [([train_unit(0)], {TC: 30, NRR: value}) for value in values])
    aggregate = [
        item for item in outcome.condition_results if item.scope is ConditionScope.AGGREGATE
    ]
    assert [item.metric for item in aggregate] == [NRR, NP]  # 書いた順
    assert aggregate[0].observed == Decimal("0.25")
    assert aggregate[0].outcome is ConditionOutcome.MET
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD


def test_median_of_odd_folds_is_the_middle_value_and_can_fail() -> None:
    values = [Decimal("0.4"), Decimal("-0.3"), Decimal("-0.1")]
    outcome = _outcome(standard(), [([train_unit(0)], {TC: 30, NRR: value}) for value in values])
    aggregate = outcome.condition_results[-1]
    assert aggregate.scope is ConditionScope.AGGREGATE
    assert aggregate.observed == Decimal("-0.1")
    assert aggregate.outcome is ConditionOutcome.NOT_MET
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD


def test_empty_aggregate_meets_unconditionally() -> None:
    outcome = _outcome(standard(aggregate=()), [([train_unit(0)], {TC: 30, NRR: Decimal("-1")})])
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD


def test_condition_results_follow_fold_then_written_order() -> None:
    floors = (
        MetricCondition(DD, Comparator.LE, Decimal("0.2")),
        MetricCondition(NRR, Comparator.GT, Decimal("-0.5")),
    )
    outcome = _outcome(
        standard(floors=floors), [([train_unit(0)], {TC: 30}), ([train_unit(0)], {TC: 30})]
    )
    assert [(item.scope, item.fold_index, item.metric) for item in outcome.condition_results] == [
        (ConditionScope.FOLD_FLOOR, 0, DD),
        (ConditionScope.FOLD_FLOOR, 0, NRR),
        (ConditionScope.FOLD_FLOOR, 1, DD),
        (ConditionScope.FOLD_FLOOR, 1, NRR),
        (ConditionScope.AGGREGATE, None, NRR),
    ]


def test_shortfalls_are_sorted_by_primary_key() -> None:
    outcome = _outcome(
        standard(),
        [
            ([train_unit(0)], {TC: 1, DD: Decimal("0.9")}),
            ([train_unit(0)], {TC: 0, DD: MetricUnavailableReason.NO_OBSERVATIONS}),
        ],
    )
    keys = [(s.kind, s.fold_index) for s in outcome.shortfalls]
    assert keys == [
        (SufficiencyShortfallKind.FOLD_TRADES_BELOW, 1),
        (SufficiencyShortfallKind.FLOOR_NOT_MET_TRADES_BELOW, 0),
        # 手順3 に達したので、合計の件数の不足（1 件 < 20 件）も残す。
        (SufficiencyShortfallKind.TOTAL_TRADES_BELOW, None),
        (SufficiencyShortfallKind.METRIC_UNCOMPUTABLE, 1),
    ]


# --- 組み立ての検査 ----------------------------------------------------------------


def test_build_rejects_a_selection_record_that_does_not_match_the_train_results() -> None:
    rule = standard()
    trains = (train_unit(0, {NRR: Decimal("0.1")}), train_unit(1, {NRR: Decimal("0.2")}))
    forged = FoldSelection(
        fold_index=0,
        selected_trial_index=0,
        selected_value=Decimal("0.1"),
        selected_train_trade_count=10,
        inputs=(
            (0, digest_for(1000), CandidateStatus.CANDIDATE),
            (1, digest_for(1001), CandidateStatus.CANDIDATE),
        ),
    )
    item = FoldEvidence(
        fold=make_fold(0), selection=forged, train_units=trains, validation=validation_unit(0)
    )
    with pytest.raises(KernelValueError, match="作り直した値と違う"):
        build_search_outcome(rule, [item], ledger_execution=1)


def test_build_requires_consecutive_folds_from_zero() -> None:
    rule = standard()
    item = evidence(rule, make_fold(1), [train_unit(0)], validation_unit(0))
    with pytest.raises(KernelValueError, match="連番"):
        build_search_outcome(rule, [item], ledger_execution=1)


def test_fold_evidence_requires_the_validation_unit_of_the_selected_trial() -> None:
    rule = standard()
    with pytest.raises(KernelValueError, match="選んだ試行"):
        evidence(rule, make_fold(0), [train_unit(0)], validation_unit(3))
    with pytest.raises(KernelValueError, match="検証区間の単位"):
        evidence(rule, make_fold(0), [train_unit(0)], None)


def test_outcome_carries_trial_counts_ledger_execution_and_purpose() -> None:
    rule = standard(purpose=StandardPurpose.MECHANISM_CHECK)
    item = evidence(rule, make_fold(0), [train_unit(0, {TC: 30})], validation_unit(0, {TC: 30}))
    outcome = build_search_outcome(rule, [item], ledger_execution=7)
    assert outcome.trial_counts[1] == (TrialStatus.COMPLETED, 2)
    assert outcome.ledger_execution == 7
    assert outcome.purpose is StandardPurpose.MECHANISM_CHECK


def test_search_outcome_rejects_meets_standard_under_mechanism_check() -> None:
    rule = standard()
    item = evidence(rule, make_fold(0), [train_unit(0, {TC: 30})], validation_unit(0, {TC: 30}))
    outcome = build_search_outcome(rule, [item], ledger_execution=1)
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    with pytest.raises(KernelValueError, match="MEETS_STANDARD"):
        SearchOutcome(
            selections=outcome.selections,
            fold_verdicts=outcome.fold_verdicts,
            verdict=SearchVerdict.MEETS_STANDARD,
            frequency=outcome.frequency,
            condition_results=outcome.condition_results,
            shortfalls=outcome.shortfalls,
            trial_counts=outcome.trial_counts,
            ledger_execution=1,
            purpose=StandardPurpose.MECHANISM_CHECK,
        )


def test_condition_result_rejects_an_outcome_contradicting_the_comparison() -> None:
    with pytest.raises(KernelValueError, match="contradicts"):
        ConditionResult(
            scope=ConditionScope.FOLD_FLOOR,
            fold_index=0,
            metric=DD,
            statistic=None,
            comparator=Comparator.LE,
            threshold=Decimal("0.2"),
            observed=Decimal("0.3"),
            outcome=ConditionOutcome.MET,
            unavailable_reason=None,
        )


def test_trade_shortfall_requires_observed_below_required() -> None:
    with pytest.raises(KernelValueError, match="not below"):
        SufficiencyShortfall(
            kind=SufficiencyShortfallKind.FOLD_TRADES_BELOW,
            fold_index=0,
            trial_index=0,
            metric=None,
            reason=None,
            required=5,
            observed=5,
        )


# --- 最長の無取引期間（D09 §11.5） ----------------------------------------------------

_T0 = UtcTime.parse("2018-03-01T00:00:00Z")


def _at(hours: int) -> UtcTime:
    return _T0 + timedelta(hours=hours)


def test_longest_idle_is_the_whole_interval_without_holdings() -> None:
    interval = Interval(start=_at(0), end=_at(100))
    assert longest_idle_period(interval, []) == timedelta(hours=100)


def test_longest_idle_uses_the_complement_of_the_union_of_holdings() -> None:
    interval = Interval(start=_at(0), end=_at(100))
    holdings = [
        (_at(10), _at(30)),
        (_at(20), _at(40)),  # 重なる保有区間は和集合にする
        (_at(40), _at(45)),  # 接する保有区間の間に空白は無い
        (_at(70), _at(75)),
    ]
    assert longest_idle_period(interval, holdings) == timedelta(hours=25)  # 45〜70


def test_longest_idle_clips_holdings_and_runs_open_positions_to_the_end() -> None:
    interval = Interval(start=_at(0), end=_at(100))
    holdings = [(_at(-5), _at(5)), (_at(60), None)]
    assert longest_idle_period(interval, holdings) == timedelta(hours=55)  # 5〜60


def test_longest_idle_rejects_a_holding_that_ends_before_it_starts() -> None:
    interval = Interval(start=_at(0), end=_at(100))
    with pytest.raises(KernelValueError, match="ends before"):
        longest_idle_period(interval, [(_at(10), _at(5))])


# --- fold の取り違え・指標の行の欠け・試行の状態の件数（Codex 第1巡の指摘） ----------------


def test_selection_rejects_results_of_another_fold() -> None:
    with pytest.raises(KernelValueError, match="別の fold"):
        select_trial(0, standard().selection, in_fold([train_unit(0)], 1))


def test_fold_evidence_rejects_a_validation_unit_of_another_fold() -> None:
    rule = standard()
    trains = in_fold([train_unit(0)], 1)
    with pytest.raises(KernelValueError, match="別の fold"):
        FoldEvidence(
            fold=make_fold(1),
            selection=select_trial(1, rule.selection, trains),
            train_units=trains,
            validation=validation_unit(0),  # fold 0 の単位
        )


def test_completed_evaluation_must_carry_every_metric_row() -> None:
    rows = tuple(item for item in train_unit(0).metrics if item.metric_id is not MetricId.WIN_RATE)
    with pytest.raises(KernelValueError, match="WIN_RATE"):
        TrainUnitEvaluation(
            fold_index=0,
            trial_index=0,
            status=TrialStatus.COMPLETED,
            run_status=RunStatus.COMPLETED,
            run_evaluation_id=digest_for(1),
            evaluation_status=EvaluationStatus.COMPLETED,
            post_run_checks_passed=True,
            metrics=rows,
        )


def test_trial_counts_are_derived_from_the_fold_units() -> None:
    outcome = _outcome(
        standard(),
        [
            (
                [
                    train_unit(0, {TC: 30}),
                    train_unit(1, status=TrialStatus.FAILED),
                    train_unit(2, status=TrialStatus.ABORTED),
                ],
                {TC: 30},
            ),
            ([train_unit(0, status=TrialStatus.NOT_STARTED)], None),
        ],
    )
    assert outcome.trial_counts == (
        (TrialStatus.NOT_STARTED, 1),
        (TrialStatus.COMPLETED, 2),  # fold 0 の選定区間と検証区間
        (TrialStatus.FAILED, 1),
        (TrialStatus.ABORTED, 1),
    )
