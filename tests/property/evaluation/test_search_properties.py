"""選定と判定の性質（D09 §5.4 の決定論・§7.2。段階5 実装 PR 3）。

任意の人工の選定区間の結果について次を確かめる。

- 評価の並び順を入れ替えても、選定記録（選んだ試行・値・候補の区分）は変わらない。
- 選んだ試行は、足切りを通った候補の中で選定の指標が最良で、同点なら番号が最小。
- 同じ入力から判定を2回作ると同じ結果になる（決定論）。fold の並び順にもよらない。
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from odyssey_fx.evaluation.domain.metrics import MetricId, MetricUnavailableReason
from odyssey_fx.evaluation.domain.search import (
    CandidateStatus,
    Comparator,
    MetricCondition,
    SelectionDirection,
    TrainUnitEvaluation,
    TrialStatus,
    build_search_outcome,
    select_trial,
)
from tests.fixtures.evaluation.search_units import (
    evidence,
    make_fold,
    standard,
    train_unit,
    validation_unit,
)

NRR = MetricId.NET_RETURN_RATE
DD = MetricId.MAX_DRAWDOWN_MTM_RATE
TC = MetricId.TRADE_COUNT

_values = st.one_of(
    st.integers(min_value=-5, max_value=5).map(lambda n: Decimal(n) / Decimal(10)),
    st.sampled_from(list(MetricUnavailableReason)),
)


@st.composite
def _train_units(draw: st.DrawFn) -> list[TrainUnitEvaluation]:
    count = draw(st.integers(min_value=1, max_value=6))
    units: list[TrainUnitEvaluation] = []
    for index in range(count):
        status = draw(
            st.sampled_from([TrialStatus.COMPLETED] * 4 + [TrialStatus.FAILED, TrialStatus.ABORTED])
        )
        units.append(
            train_unit(
                index,
                {
                    NRR: draw(_values),
                    DD: draw(st.integers(min_value=0, max_value=5).map(lambda n: Decimal(n) / 10)),
                    TC: draw(st.integers(min_value=0, max_value=40)),
                },
                status=status,
                post_run_checks_passed=draw(st.booleans()) or draw(st.booleans()),
            )
        )
    return units


@settings(max_examples=300, deadline=None)
@given(
    units=_train_units(),
    direction=st.sampled_from(list(SelectionDirection)),
    data=st.data(),
)
def test_selection_does_not_depend_on_input_order_and_picks_the_best(
    units: list[TrainUnitEvaluation], direction: SelectionDirection, data: st.DataObject
) -> None:
    rule = standard(
        direction=direction,
        eligibility=(MetricCondition(DD, Comparator.LE, Decimal("0.3")),),
    )
    shuffled = data.draw(st.permutations(units))
    selection = select_trial(0, rule.selection, units)
    assert select_trial(0, rule.selection, shuffled) == selection

    eligible = [
        unit
        for unit in units
        if dict((i, s) for i, _, s in selection.inputs)[unit.trial_index]
        is CandidateStatus.CANDIDATE
    ]
    if not eligible:
        assert selection.selected_trial_index is None
        return
    values = {unit.trial_index: unit.value_of(NRR).ratio for unit in eligible}  # type: ignore[union-attr]
    best = (
        max(values.values()) if direction is SelectionDirection.MAXIMIZE else min(values.values())
    )
    assert selection.selected_value == best
    assert selection.selected_trial_index == min(i for i, v in values.items() if v == best)


@settings(max_examples=150, deadline=None)
@given(
    folds=st.lists(_train_units(), min_size=1, max_size=4),
    validation_trades=st.lists(st.integers(min_value=0, max_value=30), min_size=4, max_size=4),
    data=st.data(),
)
def test_search_outcome_is_deterministic_and_independent_of_fold_order(
    folds: list[list[TrainUnitEvaluation]], validation_trades: list[int], data: st.DataObject
) -> None:
    rule = standard()
    items = []
    for index, trains in enumerate(folds):
        selection = select_trial(index, rule.selection, trains)
        unit = (
            None
            if selection.selected_trial_index is None
            else validation_unit(selection.selected_trial_index, {TC: validation_trades[index]})
        )
        items.append(evidence(rule, make_fold(index), trains, unit))
    statuses = [unit.status for trains in folds for unit in trains]
    first = build_search_outcome(rule, items, statuses, ledger_execution=1)
    again = build_search_outcome(rule, items, statuses, ledger_execution=1)
    reordered = build_search_outcome(
        rule, data.draw(st.permutations(items)), statuses, ledger_execution=1
    )
    assert first == again == reordered
