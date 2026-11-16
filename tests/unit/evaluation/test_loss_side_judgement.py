"""研究ポリシーの版 5 以上の判定（研究ポリシー第 5 版の設計 D24 §3・§4・§9.2。実装 PR 4-2）。

受入テスト（D24 §10.2）:

- RP5-6: fold の純損失の床（区間末の評価を含む純収益率 #21 ≥ −0.15）。
- RP5-7: 近傍の床（選定の区間。違反・コンパイル拒否・完走していない近傍・近傍が無い fold）。
- RP5-8: 経路の `PROFIT_FACTOR` の乗算の形（負け無し・件数不足・ちょうど・区間末の仮決済の損失）。
- RP5-10: 損失の集中（当てない・満たさない・`P ≥ 0.15` で必ず満たす）。
- RP5-16（型の部分）: 判定の関数が局面を入力に取らない。

人工の単位の結果だけを使い、snapshot も価格も戦略の成績も読まない。
"""

from __future__ import annotations

import dataclasses
import typing
from decimal import Decimal

import pytest

from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import CurrencyCode, Money, decimal_from_str
from odyssey_fx.evaluation.domain.metrics import MetricId
from odyssey_fx.evaluation.domain.path import (
    PathEnd,
    PathTradeTotals,
    SegmentTrades,
    path_trade_set,
)
from odyssey_fx.evaluation.domain.search import (
    ConditionOutcome,
    ConditionScope,
    DeflatedDownsideResult,
    EvidenceAxis,
    FoldVerdict,
    LossConcentrationResult,
    LossSideEvidence,
    NeighborConditionResult,
    ProfitabilityAxis,
    ProfitFactorResult,
    SearchVerdict,
    SufficiencyShortfallKind,
    TrialStatus,
    VariantOutcome,
    build_variant_outcome,
)
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.evaluation.domain.variant import Variant
from tests.fixtures.evaluation.adoption_review import trade
from tests.fixtures.evaluation.loss_side_units import (
    loss_side,
    totals,
    v5_path,
    v5_standard,
)
from tests.fixtures.evaluation.search_units import (
    evidence,
    judge,
    make_fold,
    path_summary,
    standard,
    train_unit,
    validation_unit,
)
from tests.unit.evaluation.test_path_trade_set import _settled

_D = Decimal
NET = MetricId.NET_RETURN_RATE
CLOSED_OUT = MetricId.NET_RETURN_RATE_CLOSED_OUT
DRAWDOWN = MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK


def _trains(**overrides: dict[MetricId, Decimal | int]) -> list:  # type: ignore[type-arg]
    """3 試行の選定区間の単位（試行 1 の純収益率が最も高く選ばれる）。"""
    base: dict[int, dict[MetricId, Decimal | int]] = {
        0: {NET: _D("0.05")},
        1: {NET: _D("0.20")},
        2: {NET: _D("0.10")},
    }
    for key, values in overrides.items():
        base[int(key.removeprefix("t"))].update(values)
    return [train_unit(index, base[index]) for index in range(3)]


def _folds(
    rule: object,
    *,
    trains: list | None = None,  # type: ignore[type-arg]
    validations: dict[int, dict[MetricId, Decimal | int]] | None = None,
    count: int = 2,
) -> list:  # type: ignore[type-arg]
    found = []
    for index in range(count):
        metrics = (validations or {}).get(index, {})
        found.append(
            evidence(
                rule,  # type: ignore[arg-type]
                make_fold(index),
                trains if trains is not None else _trains(),
                validation_unit(1, metrics),
            )
        )
    return found


def _judge_v5(
    folds: list,  # type: ignore[type-arg]
    *,
    rule: object | None = None,
    path: object | None = None,
    side: LossSideEvidence | None = None,
) -> VariantOutcome:
    chosen = rule if rule is not None else v5_standard()
    return judge(
        chosen,  # type: ignore[arg-type]
        folds,
        path if path is not None else v5_path(len(folds)),  # type: ignore[arg-type]
        loss_side=side if side is not None else loss_side(),
    )


def _results[ResultT](outcome: VariantOutcome, kind: type[ResultT]) -> list[ResultT]:
    return [item for item in outcome.condition_results if isinstance(item, kind)]


# --- 既定の入力は満たす ---------------------------------------------------------------


def test_the_default_v5_inputs_meet_the_standard() -> None:
    outcome = _judge_v5(_folds(v5_standard()))
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    assert outcome.profitability is ProfitabilityAxis.MET
    assert outcome.evidence is EvidenceAxis.SUFFICIENT
    scopes = [item.scope for item in outcome.condition_results]
    assert ConditionScope.NEIGHBOR_FLOOR in scopes
    assert scopes[-2:] == [
        ConditionScope.LOSS_CONCENTRATION,
        ConditionScope.DEFLATED_DOWNSIDE_RATIO,
    ]
    assert len(_results(outcome, ProfitFactorResult)) == 1


def test_a_v5_standard_requires_the_loss_side_inputs_and_a_v5_path() -> None:
    folds = _folds(v5_standard())
    with pytest.raises(KernelValueError, match="LossSideEvidence"):
        build_variant_outcome(v5_standard(), Variant.STANDARD, folds, v5_path(2), None)
    with pytest.raises(KernelValueError, match="PathSummaryV5"):
        build_variant_outcome(v5_standard(), Variant.STANDARD, folds, path_summary(2), loss_side())
    v4_folds = _folds(standard())
    with pytest.raises(KernelValueError, match="版 4 以下"):
        build_variant_outcome(standard(), Variant.STANDARD, v4_folds, path_summary(2), loss_side())


# --- RP5-6 fold の純損失の床 ----------------------------------------------------------


def test_rp5_6_a_fold_below_the_net_loss_floor_with_enough_trades_is_below_standard() -> None:
    folds = _folds(v5_standard(), validations={1: {CLOSED_OUT: _D("-0.16")}})
    outcome = _judge_v5(folds)
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD
    assert dict(outcome.fold_verdicts)[1] is FoldVerdict.FLOOR_BREACHED
    breached = [
        item
        for item in outcome.condition_results
        if item.scope is ConditionScope.FOLD_FLOOR and item.outcome is ConditionOutcome.NOT_MET
    ]
    assert [(item.fold_index, item.metric) for item in breached] == [(1, CLOSED_OUT)]


def test_rp5_6_the_same_breach_below_the_minimum_total_is_insufficient_with_the_reason() -> None:
    folds = _folds(
        v5_standard(),
        validations={
            0: {MetricId.TRADE_COUNT: 3},
            1: {MetricId.TRADE_COUNT: 3, CLOSED_OUT: _D("-0.16")},
        },
    )
    outcome = _judge_v5(folds)
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    kinds = {
        (item.kind, item.fold_index, item.trial_index, item.metric) for item in outcome.shortfalls
    }
    assert (
        SufficiencyShortfallKind.FLOOR_BREACHED_TOTAL_TRADES_BELOW,
        1,
        1,
        CLOSED_OUT,
    ) in kinds
    # 違反は収益性・リスクの軸の観測値として残り、保留にしない（D13 §5.5）。
    assert outcome.profitability is ProfitabilityAxis.NOT_MET
    assert outcome.hold is False


def test_rp5_6_the_floor_is_inclusive_at_minus_fifteen_percent() -> None:
    folds = _folds(v5_standard(), validations={1: {CLOSED_OUT: _D("-0.15")}})
    outcome = _judge_v5(folds, path=v5_path(2, final="1200000", series=("1000000", "1200000")))
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD


# --- RP5-7 近傍の床 ------------------------------------------------------------------


def test_rp5_7_a_neighbour_breaking_the_floor_in_the_selection_interval_is_below_standard() -> None:
    trains = _trains(t2={CLOSED_OUT: _D("-0.31")})
    outcome = _judge_v5(_folds(v5_standard(), trains=trains))
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD
    assert {verdict for _, verdict in outcome.fold_verdicts} == {FoldVerdict.FLOOR_BREACHED}
    breached = [
        (item.fold_index, item.neighbor_trial_index, item.metric)
        for item in _results(outcome, NeighborConditionResult)
        if item.outcome is ConditionOutcome.NOT_MET
    ]
    assert breached == [(0, 2, CLOSED_OUT), (1, 2, CLOSED_OUT)]


def test_rp5_7_a_neighbour_drawdown_above_the_floor_also_breaks_it() -> None:
    trains = _trains(t0={DRAWDOWN: _D("0.31")})
    outcome = _judge_v5(_folds(v5_standard(), trains=trains))
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD


def test_rp5_7_a_neighbour_breach_below_the_minimum_total_names_the_neighbour() -> None:
    trains = _trains(t2={CLOSED_OUT: _D("-0.31")})
    folds = _folds(
        v5_standard(),
        trains=trains,
        validations={0: {MetricId.TRADE_COUNT: 3}, 1: {MetricId.TRADE_COUNT: 3}},
    )
    outcome = _judge_v5(folds)
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    found = {
        (item.fold_index, item.trial_index, item.metric)
        for item in outcome.shortfalls
        if item.kind is SufficiencyShortfallKind.FLOOR_BREACHED_TOTAL_TRADES_BELOW
    }
    assert found == {(0, 2, CLOSED_OUT), (1, 2, CLOSED_OUT)}


def test_rp5_7_a_compile_rejected_neighbour_is_left_out_of_the_judgement() -> None:
    trains = _trains()
    trains[2] = train_unit(2, status=TrialStatus.FAILED)
    outcome = _judge_v5(_folds(v5_standard(), trains=trains))
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    judged = {item.neighbor_trial_index for item in _results(outcome, NeighborConditionResult)}
    assert judged == {0}


def test_rp5_7_a_neighbour_that_did_not_complete_makes_the_fold_not_judged() -> None:
    trains = _trains()
    trains[0] = train_unit(
        0, run_status=RunStatus.FAILED_CAPABILITY, evaluation_status=EvaluationStatus.REJECTED
    )
    outcome = _judge_v5(_folds(v5_standard(), trains=trains))
    assert outcome.verdict is SearchVerdict.INCOMPLETE
    assert {verdict for _, verdict in outcome.fold_verdicts} == {FoldVerdict.INCOMPLETE}
    unjudged = [
        item.neighbor_trial_index
        for item in _results(outcome, NeighborConditionResult)
        if item.outcome is ConditionOutcome.NOT_JUDGED
    ]
    assert set(unjudged) == {0}
    assert outcome.profitability is ProfitabilityAxis.NOT_JUDGED


def test_rp5_7_a_neighbour_failure_does_not_hide_a_breach() -> None:
    trains = _trains(t2={CLOSED_OUT: _D("-0.40")})
    trains[0] = train_unit(0, evaluation_status=EvaluationStatus.FAILED)
    outcome = _judge_v5(_folds(v5_standard(), trains=trains))
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD


def test_rp5_7_a_fold_without_a_usable_neighbour_is_not_applicable() -> None:
    trains = [train_unit(0, {NET: _D("0.20")})]
    folds = [
        evidence(v5_standard(), make_fold(index), trains, validation_unit(0)) for index in range(2)
    ]
    outcome = _judge_v5(folds, side=loss_side(((),)))
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    neighbor = _results(outcome, NeighborConditionResult)
    assert {item.outcome for item in neighbor} == {ConditionOutcome.NOT_APPLICABLE}
    assert [(item.fold_index, item.metric) for item in neighbor] == [
        (0, DRAWDOWN),
        (0, CLOSED_OUT),
        (1, DRAWDOWN),
        (1, CLOSED_OUT),
    ]
    assert all(item.neighbor_trial_index is None for item in neighbor)


def test_rp5_7_only_compile_rejected_neighbours_also_make_the_floor_not_applicable() -> None:
    trains = [
        train_unit(0, status=TrialStatus.FAILED),
        train_unit(1, {NET: _D("0.20")}),
        train_unit(2, status=TrialStatus.FAILED),
    ]
    outcome = _judge_v5(_folds(v5_standard(), trains=trains))
    assert {item.outcome for item in _results(outcome, NeighborConditionResult)} == {
        ConditionOutcome.NOT_APPLICABLE
    }


def test_the_neighbour_floor_does_not_ask_the_neighbour_to_earn() -> None:
    """利益の側（近傍も稼ぐこと）は求めない（決定 139）。損失が床の内側なら満たす。"""
    trains = _trains(t0={NET: _D("-0.20"), CLOSED_OUT: _D("-0.29")})
    outcome = _judge_v5(_folds(v5_standard(), trains=trains))
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD


# --- RP5-8 経路の PROFIT_FACTOR --------------------------------------------------------


def _profit_factor(outcome: VariantOutcome) -> ProfitFactorResult:
    (found,) = _results(outcome, ProfitFactorResult)
    return found


def test_rp5_8_a_path_without_losing_trades_meets_the_profit_factor() -> None:
    path = v5_path(2, trades=totals("50000", "0"))
    outcome = _judge_v5(_folds(v5_standard()), path=path)
    result = _profit_factor(outcome)
    assert result.outcome is ConditionOutcome.MET
    assert result.observed is None
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD


def test_rp5_8_no_losses_with_too_few_trades_is_still_insufficient() -> None:
    folds = _folds(
        v5_standard(),
        validations={0: {MetricId.TRADE_COUNT: 3}, 1: {MetricId.TRADE_COUNT: 3}},
    )
    path = v5_path(2, trades=totals("50000", "0", closed=6))
    outcome = _judge_v5(folds, path=path)
    assert _profit_factor(outcome).outcome is ConditionOutcome.MET
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert SufficiencyShortfallKind.TOTAL_TRADES_BELOW in {item.kind for item in outcome.shortfalls}
    assert outcome.hold is True


def test_rp5_8_exactly_one_point_two_times_the_losses_meets_the_condition() -> None:
    outcome = _judge_v5(_folds(v5_standard()), path=v5_path(2, trades=totals("12000", "10000")))
    assert _profit_factor(outcome).outcome is ConditionOutcome.MET
    below = _judge_v5(_folds(v5_standard()), path=v5_path(2, trades=totals("11999.99", "10000")))
    assert _profit_factor(below).outcome is ConditionOutcome.NOT_MET
    assert below.verdict is SearchVerdict.BELOW_STANDARD


def test_rp5_8_the_multiplicative_form_is_not_fooled_by_rounding_the_ratio() -> None:
    """W ÷ L を 28 桁に丸めると 1.2 になる W < 1.2 × L を、乗算の形は満たさないと判定する。"""
    losses = _D("10000000000000000000000000000001")
    wins = _D("12000000000000000000000000000001")  # 1.2 × L より 0.2 小さい
    result = _judge_v5(
        _folds(v5_standard()),
        path=v5_path(2, trades=totals(str(wins), str(losses))),
    )
    pf = _profit_factor(result)
    assert pf.observed == _D("1.2")
    assert pf.outcome is ConditionOutcome.NOT_MET


def test_rp5_8_a_segment_end_settlement_loss_enters_the_profit_factor() -> None:
    """完了取引は勝ちだけでも、区間末に仮に閉じた建玉の損失（入場の費用込み）が入って割る。"""
    yen = CurrencyCode("JPY")
    initial = Money(decimal_from_str("1000000"), yen)
    win = trade("2020-01-06T00:00:00Z", "2020-01-07T00:00:00Z", seq=1)  # +4,990
    settled = _settled(2, "-4200")  # −4,200 − 入場 10 = −4,210
    segment = SegmentTrades(
        segment_index=0,
        initial_balance=initial,
        settled_equity=Money(decimal_from_str("1000780"), yen),
        closed_trades=(win,),
        settled=(settled,),
    )
    found = PathTradeTotals.of(
        path_trade_set(
            (segment,),
            initial_balance=initial,
            final_balance=Money(decimal_from_str("1000780"), yen),
        )
    )
    assert (found.wins.amount, found.losses.amount) == (_D("4990"), _D("4210"))
    assert (found.closed_count, found.settlement_count) == (1, 1)
    outcome = _judge_v5(_folds(v5_standard()), path=v5_path(2, trades=found))
    assert _profit_factor(outcome).outcome is ConditionOutcome.NOT_MET


def test_a_path_without_trades_or_settlements_leaves_the_profit_factor_uncomputable() -> None:
    path = v5_path(2, trades=totals("0", "0", closed=0))
    outcome = _judge_v5(_folds(v5_standard()), path=path)
    result = _profit_factor(outcome)
    assert result.outcome is ConditionOutcome.UNCOMPUTABLE
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert outcome.profitability is ProfitabilityAxis.NOT_JUDGED


# --- RP5-10 損失の集中 ---------------------------------------------------------------


def _concentration(outcome: VariantOutcome) -> LossConcentrationResult:
    (found,) = _results(outcome, LossConcentrationResult)
    return found


def test_rp5_10_a_path_return_below_the_threshold_is_not_applicable() -> None:
    path = v5_path(2, final="1050000", series=("1000000", "1050000"))
    folds = _folds(v5_standard(), validations={1: {CLOSED_OUT: _D("-0.12")}})
    outcome = _judge_v5(folds, path=path)
    result = _concentration(outcome)
    assert result.outcome is ConditionOutcome.NOT_APPLICABLE
    assert result.loss_concentration.worst_fold_loss == _D("0.12")
    # 当てない条件は満たさない側に数えない（仮置き 17）。
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    assert outcome.profitability is ProfitabilityAxis.MET


def test_rp5_10_a_worst_fold_loss_above_the_path_net_is_below_standard() -> None:
    path = v5_path(2, final="1120000", series=("1000000", "1120000"))
    folds = _folds(v5_standard(), validations={0: {CLOSED_OUT: _D("-0.13")}})
    outcome = _judge_v5(folds, path=path)
    result = _concentration(outcome)
    assert result.outcome is ConditionOutcome.NOT_MET
    assert result.observed == _D("0.13")
    assert result.loss_concentration.worst_fold_index == 0
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD


@pytest.mark.parametrize("net", ["1150000", "1200000", "1500000"])
@pytest.mark.parametrize("loss", ["0", "-0.05", "-0.149", "-0.15"])
def test_rp5_10_at_fifteen_percent_or_more_the_fold_floor_implies_the_concentration(
    net: str, loss: str
) -> None:
    path = v5_path(2, final=net, series=("1000000", net))
    folds = _folds(v5_standard(), validations={1: {CLOSED_OUT: _D(loss)}})
    outcome = _judge_v5(folds, path=path)
    assert _concentration(outcome).outcome is ConditionOutcome.MET


def test_the_concentration_is_recorded_not_applicable_when_the_path_lost() -> None:
    path = v5_path(2, final="900000", series=("1000000", "900000"))
    outcome = _judge_v5(_folds(v5_standard()), path=path)
    assert _concentration(outcome).outcome is ConditionOutcome.NOT_APPLICABLE
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD


# --- 割り引いた確率の 4 値への写し方（RP5-11 の数値は test_deflated_downside.py） ------------


def _deflated(outcome: VariantOutcome) -> DeflatedDownsideResult:
    (found,) = _results(outcome, DeflatedDownsideResult)
    return found


def test_a_low_deflated_probability_is_below_standard() -> None:
    returns = tuple(_D("0.001") if index % 2 else _D("-0.0009") for index in range(200))
    outcome = _judge_v5(_folds(v5_standard()), side=loss_side(path_returns=returns))
    assert _deflated(outcome).outcome is ConditionOutcome.NOT_MET
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD


def test_a_path_without_losing_days_leaves_the_probability_uncomputable() -> None:
    returns = tuple(_D("0.001") for _ in range(50))
    outcome = _judge_v5(_folds(v5_standard()), side=loss_side(path_returns=returns))
    result = _deflated(outcome)
    assert result.outcome is ConditionOutcome.UNCOMPUTABLE
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert (
        SufficiencyShortfallKind.METRIC_UNCOMPUTABLE,
        MetricId.ANNUALIZED_DOWNSIDE_RATIO,
    ) in {(item.kind, item.metric) for item in outcome.shortfalls}


def test_missing_inputs_of_the_probability_make_the_experiment_not_judged() -> None:
    from odyssey_fx.evaluation.domain.metrics import MetricUnavailableReason

    side = loss_side(path_returns=MetricUnavailableReason.INPUT_NOT_AVAILABLE)
    outcome = _judge_v5(_folds(v5_standard()), side=side)
    assert _deflated(outcome).outcome is ConditionOutcome.NOT_JUDGED
    assert outcome.verdict is SearchVerdict.INCOMPLETE


def test_an_incomplete_path_makes_no_v5_path_conditions() -> None:
    path = v5_path(2, end=PathEnd.INCOMPLETE)
    outcome = _judge_v5(_folds(v5_standard()), path=path, side=loss_side(incomplete=True))
    assert outcome.verdict is SearchVerdict.INCOMPLETE
    assert not any(
        isinstance(item, (ProfitFactorResult, LossConcentrationResult, DeflatedDownsideResult))
        for item in outcome.condition_results
    )


# --- RP5-16（型の部分）---------------------------------------------------------------


def test_rp5_16_the_judgement_takes_no_regime_input() -> None:
    """判定の関数と追加の入力の型は、局面の付与の結果（日ごと・取引ごとの局面）を持たない。"""
    hints = typing.get_type_hints(build_variant_outcome)
    assert set(hints) == {"standard", "variant", "folds", "path", "loss_side", "return"}
    side_fields = {field.name: field.type for field in dataclasses.fields(LossSideEvidence)}
    assert set(side_fields) == {"neighbors", "deflated"}
    assert not any("Regime" in str(value) for value in (*hints.values(), *side_fields.values()))


def test_the_v4_judgement_is_unchanged_by_the_v5_types() -> None:
    """版 4 以下の評価基準の結果は版 4 の型のまま（結末記録の形を変えない。RP5-17 の補）。"""
    rule = standard()
    folds = [evidence(rule, make_fold(index), _trains(), validation_unit(1)) for index in range(2)]
    outcome = judge(rule, folds)
    assert all(type(item).__name__ == "ConditionResult" for item in outcome.condition_results)
    assert type(outcome.path).__name__ == "PathSummary"


# --- 結末記録の正規化形（D24 §9.3。保存して読み戻せる）--------------------------------------


def test_a_v5_search_outcome_survives_the_canonical_round_trip() -> None:
    import json

    from odyssey_fx.common.canonical import encode
    from odyssey_fx.evaluation.adapters.fs_store import _exact, _search_outcome_of
    from odyssey_fx.evaluation.domain.search import build_search_outcome

    trains = _trains()
    trains[2] = train_unit(2, status=TrialStatus.FAILED)
    folds = _folds(v5_standard(), trains=trains)
    path = v5_path(2, final="1050000", series=("1000000", "1050000"))
    outcomes = [
        judge(v5_standard(), folds, path, variant=variant, loss_side=loss_side())
        for variant in (Variant.STANDARD, Variant.CANDIDATE)
    ]
    search = build_search_outcome(outcomes[0], outcomes[1], 1)
    raw = json.loads(encode(search))
    back = _exact(raw, _search_outcome_of, "search")
    assert back == search
    kinds = {type(item).__name__ for item in back.variants.standard.condition_results}
    assert kinds == {
        "ConditionResult",
        "NeighborConditionResult",
        "ProfitFactorResult",
        "LossConcentrationResult",
        "DeflatedDownsideResult",
    }
    assert type(back.variants.standard.path).__name__ == "PathSummaryV5"


def test_a_v4_search_outcome_keeps_the_v4_record_shape() -> None:
    """版 4 以下の結果の正規化形は版 5 の項目を持たない（既存の結末記録と同じ形）。"""
    import json

    from odyssey_fx.common.canonical import encode
    from odyssey_fx.evaluation.domain.search import build_search_outcome

    rule = standard()
    folds = [evidence(rule, make_fold(index), _trains(), validation_unit(1)) for index in range(2)]
    outcomes = [
        judge(rule, folds, variant=variant) for variant in (Variant.STANDARD, Variant.CANDIDATE)
    ]
    raw = json.loads(encode(build_search_outcome(outcomes[0], outcomes[1], 1)))
    variant = raw["variants"]["standard"]
    assert "trades" not in variant["path"]
    assert all(
        set(item)
        == {
            "scope",
            "fold_index",
            "metric",
            "statistic",
            "comparator",
            "threshold",
            "observed",
            "outcome",
            "unavailable_reason",
        }
        for item in variant["condition_results"]
    )


def test_rp5_16_the_v5_judgement_modules_never_read_regimes() -> None:
    """版 5 の判定と材料のモジュールは局面を import せず、評価基準の `regimes` も読まない。

    走査の対象は判定（`domain.search`。評価基準の型として `RegimeSpec` だけを持つ）、割り引いた
    確率（`domain.deflated_downside`・`domain.normal_distribution`）、経路の取引の集合の要約
    （`domain.path`）、材料の組み立て（`application.loss_side`・`application.evaluate_run` の
    `run_trade_material`）。局面のモジュール（`domain.regimes`・`application.regimes`）の名前に合わせ、
    import は部分文字列 `regime` で見る。PR 4-3 の局面の試験と同じ基準（RP5-16）。
    """
    import ast
    import inspect

    from odyssey_fx.evaluation.application import evaluate_run
    from odyssey_fx.evaluation.application import loss_side as loss_side_module
    from odyssey_fx.evaluation.domain import deflated_downside, normal_distribution, path, search

    for module in (
        search,
        deflated_downside,
        normal_distribution,
        path,
        loss_side_module,
        evaluate_run,
    ):
        tree = ast.parse(inspect.getsource(module))
        reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "regimes"
            and not (isinstance(node.value, ast.Name) and node.value.id == "self")
        ]
        assert reads == [], module.__name__
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "regime" in node.module
            for alias in node.names
        }
        allowed = {"RegimeSpec"} if module is search else set()
        assert imported <= allowed, module.__name__


def test_the_v5_judgement_does_not_depend_on_the_callers_decimal_precision() -> None:
    """呼び出し側の Decimal の精度を変えても、損失の集中・PROFIT_FACTOR・割り引いた確率の結果は
    同じ（D07 §5.5。Codex 1 巡目の指摘）。"""
    from decimal import localcontext

    path = v5_path(
        2, final="1149500", series=("1000000", "1149500"), trades=totals("12345", "10287")
    )
    folds = _folds(v5_standard(), validations={1: {CLOSED_OUT: _D("-0.149")}})
    expected = _judge_v5(folds, path=path)
    assert _concentration(expected).outcome is ConditionOutcome.MET
    with localcontext() as context:
        context.prec = 2
        assert _judge_v5(folds, path=path) == expected


def test_a_fold_with_a_failed_neighbour_still_counts_its_validation_trades() -> None:
    """近傍の run の失敗で「判定できない」になった fold も、検証の run の取引件数は合計に入る。
    別の fold の床の違反は、合計が最低件数以上なら手順 1 で「満たさない」（設計適合レビュー）。"""
    trains = _trains()
    trains[0] = train_unit(
        0, run_status=RunStatus.FAILED_CAPABILITY, evaluation_status=EvaluationStatus.REJECTED
    )
    rule = v5_standard()
    folds = [
        evidence(rule, make_fold(0), trains, validation_unit(1, {MetricId.TRADE_COUNT: 20})),
        evidence(
            rule,
            make_fold(1),
            _trains(),
            validation_unit(1, {MetricId.TRADE_COUNT: 3, CLOSED_OUT: _D("-0.16")}),
        ),
    ]
    outcome = _judge_v5(folds)
    assert dict(outcome.fold_verdicts) == {0: FoldVerdict.INCOMPLETE, 1: FoldVerdict.FLOOR_BREACHED}
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD
    assert SufficiencyShortfallKind.TOTAL_TRADES_BELOW not in {
        item.kind for item in outcome.shortfalls
    }


def test_a_breach_with_a_failed_neighbour_below_the_minimum_total_is_not_judged() -> None:
    """同じ fold に床の違反と近傍の run の失敗があり、検証の合計が最低件数に届かないときは、
    D24 §3.6 の順（手順 2 の判定できない → 手順 3 の証拠不足）で「判定できない」。"""
    trains = _trains(t2={CLOSED_OUT: _D("-0.40")})
    trains[0] = train_unit(0, evaluation_status=EvaluationStatus.FAILED)
    folds = _folds(
        v5_standard(),
        trains=trains,
        validations={0: {MetricId.TRADE_COUNT: 3}, 1: {MetricId.TRADE_COUNT: 3}},
    )
    outcome = _judge_v5(folds)
    assert outcome.verdict is SearchVerdict.INCOMPLETE
    assert outcome.profitability is ProfitabilityAxis.NOT_JUDGED
    # 違反の事実は理由に残す（件数不足のまま床を割った）。
    assert SufficiencyShortfallKind.FLOOR_BREACHED_TOTAL_TRADES_BELOW in {
        item.kind for item in outcome.shortfalls
    }
