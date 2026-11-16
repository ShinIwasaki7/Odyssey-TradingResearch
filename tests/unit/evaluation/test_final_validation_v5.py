"""最終検証の判定への研究ポリシー第 5 版の条件の当て方（D09 §9.10.16。決定 212〜220）。

受入テスト FV5-T1〜FV5-T5 の判定の関数の側（FV5-T6・FV5-T7 と記録の側は
`tests/integration/app/test_final_validation.py`・`tests/integration/app/test_final_run.py`）:

- FV5-T1: 版 4 以下の候補の判定は今のまま（区間の床・研究期間だけの条件を持たない）。
- FV5-T2: 区間の 1 つが −0.15 を割る（件数が `N` 以上／未満）→ 満たさない／証拠不足と理由
  「最低件数未満で損失の床を割った」。
- FV5-T3: 損失の集中を当てない（決定 213）。
- FV5-T4: `PROFIT_FACTOR`（負け無し、`W = 1.2 × L` ちょうど、run の終わりの仮決済の損失が入る、
  和が合わない入力）。
- FV5-T5: 当てない条件は結果を作らず名前の一覧だけ。判定の関数は近傍・他の試行・日足を取らない。

値はすべて人工（残高・損益は人工の円）で、成績を読まない。価格を作らない。
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import timedelta
from decimal import Decimal
from typing import Final, cast

import pytest

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.trace.result import HypotheticalCloseLine
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import PositionId, RunId
from odyssey_fx.common.money import CurrencyCode, Money, Price, Quantity, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.application import loss_side as loss_side_module
from odyssey_fx.evaluation.application.evaluate_run import OpenPositionEntry, SegmentTradeFacts
from odyssey_fx.evaluation.application.loss_side import run_trade_totals
from odyssey_fx.evaluation.application.ports import ResultRepository
from odyssey_fx.evaluation.domain.final_validation import (
    FINAL_INTERVAL_DAYS,
    RESEARCH_ONLY_CONDITIONS,
    FinalLossSideMaterial,
    FinalRunSummary,
    final_intervals,
    interval_floor_results,
    judge_final_variant,
)
from odyssey_fx.evaluation.domain.metrics import MetricId, MetricUnavailableReason, RatioValue
from odyssey_fx.evaluation.domain.path import PathTradeTotals
from odyssey_fx.evaluation.domain.search import (
    ConditionOutcome,
    ConditionScope,
    EvidenceAxis,
    ProfitabilityAxis,
    ProfitFactorResult,
    SearchVerdict,
)
from odyssey_fx.evaluation.domain.variant import Variant
from tests.fixtures.evaluation.adoption_review import trade
from tests.fixtures.evaluation.loss_side_units import FOLD_FLOORS, totals, v5_standard
from tests.fixtures.evaluation.search_units import JPY, standard

_D = Decimal
_DAY: Final = timedelta(days=1)
_LENGTH: Final = timedelta(days=FINAL_INTERVAL_DAYS)
START: Final = UtcTime.parse("2024-01-01T00:00:00Z")
#: 端数 100 日 ＋ 364 日の区間 3 つ。
SCORING: Final = Interval(start=START, end=START + 100 * _DAY + 3 * _LENGTH)
#: 364 日の区間ちょうど 3 つ（端数なし）。
EVEN: Final = Interval(start=START, end=START + 3 * _LENGTH)
INITIAL: Final = Money(_D("1000000"), JPY)
#: 研究の判定で決まった頻度区分の証拠の要件（合計 8 件。`v5_standard()` の区分）。
REQUIREMENT: Final = v5_standard().sufficiency.classes[0]


def _yen(text: str) -> Money:
    return Money(_D(text), JPY)


def _material(
    points: list[tuple[UtcTime, str]],
    settled: str,
    *,
    scoring: Interval = SCORING,
    trades: PathTradeTotals | None = None,
) -> FinalLossSideMaterial:
    return FinalLossSideMaterial(
        scoring=scoring,
        initial_balance=INITIAL,
        equity_points=tuple((at, _D(amount)) for at, amount in points),
        settled_equity=_yen(settled),
        trades=trades or totals(),
    )


def _summary(
    material: FinalLossSideMaterial | None,
    *,
    net: str = "0.10",
    drawdown: str = "0.20",
    trade_count: int = 40,
) -> FinalRunSummary:
    return FinalRunSummary(
        metrics=(
            (MetricId.NET_RETURN_RATE, RatioValue(_D(net))),
            (MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, RatioValue(_D(drawdown))),
        ),
        trade_count=trade_count,
        loss_side=material,
    )


def _steady() -> FinalLossSideMaterial:
    """どの区間も −0.15 を割らない人工の系列（全体 +10%）。"""
    b1, b2, b3 = (item.interval.start for item in final_intervals(SCORING)[1:])
    return _material(
        [(b1, "1020000"), (b2, "1000000"), (b3, "1050000")],
        "1100000",
    )


# --- FV5-2・FV5-3: 区間の区切り方と境目の資産 --------------------------------------------------


def test_the_scoring_interval_is_cut_from_the_end_by_364_days_with_the_remainder_first() -> None:
    """FV5-2（決定 220）: 終わりから 364 日ずつ、余りは最初の端数の区間（fold の並べ方と同じ）。"""
    items = final_intervals(SCORING)
    assert [item.partial for item in items] == [True, False, False, False]
    assert [item.interval_index for item in items] == [0, 1, 2, 3]
    assert items[0].interval == Interval(start=START, end=START + 100 * _DAY)
    for step, item in enumerate(reversed(items[1:])):
        assert item.interval.end == SCORING.end - step * _LENGTH
        assert item.interval.end - item.interval.start == _LENGTH
    assert all(a.interval.end == b.interval.start for a, b in zip(items, items[1:], strict=False))
    even = final_intervals(EVEN)
    assert [item.partial for item in even] == [False, False, False]
    assert even[0].interval.start == START
    single = final_intervals(Interval(start=START, end=START + _LENGTH))
    assert len(single) == 1 and not single[0].partial


def test_a_boundary_takes_the_last_point_at_or_before_it_and_never_a_later_point() -> None:
    """FV5-3 (2): 境目ちょうどの点を含み、境目より後の点は使わない。点が無ければ初期残高。"""
    boundary = START + 100 * _DAY
    material = _material(
        [
            (START + 10 * _DAY, "1010000"),
            (boundary - _DAY, "1020000"),
            (boundary + _DAY, "500000"),
        ],
        "1000000",
    )
    assert material.equity_at(boundary) == _D("1020000")  # 週末の境目: 前の最後の点
    exact = _material([(boundary, "1030000"), (boundary, "1040000")], "1000000")
    assert exact.equity_at(boundary) == _D("1040000")  # ちょうどの点（並びの最後）
    assert material.equity_at(START) == INITIAL.amount  # 前に点が無い


def test_the_interval_returns_use_the_run_start_the_boundaries_and_the_settled_end() -> None:
    """FV5-3: 最初の始まりは初期残高、途中は境目の資産（前の終わり＝次の始まり）、最後は費用を
    引いた後の終わりの資産。途中の境目で建玉を仮に閉じない。"""
    b1, b2, b3 = (item.interval.start for item in final_intervals(SCORING)[1:])
    material = _material(
        [(b1 - _DAY, "1100000"), (b2, "880000"), (b3, "1000000"), (SCORING.end - _DAY, "999")],
        "1100000",
    )
    results = interval_floor_results(FOLD_FLOORS, material)
    assert [(item.start_equity, item.end_equity) for item in results] == [
        (_D("1000000"), _D("1100000")),
        (_D("1100000"), _D("880000")),
        (_D("880000"), _D("1000000")),
        (_D("1000000"), _D("1100000")),  # 最後の点（999）でなく費用を引いた後の終わりの資産
    ]
    assert [item.observed for item in results][:2] == [_D("0.1"), _D("-0.2")]


def test_only_the_net_loss_floor_is_applied_to_the_intervals() -> None:
    """区間に当てるのは fold の純損失の床（純収益率の指標の条件）だけ。ドローダウンの床は区間に
    置かない（決定 212・218 の注記）。"""
    results = interval_floor_results(FOLD_FLOORS, _steady())
    assert {item.metric for item in results} == {MetricId.NET_RETURN_RATE_CLOSED_OUT}
    assert {item.threshold for item in results} == {_D("-0.15")}
    assert len(results) == len(final_intervals(SCORING))


def test_a_non_positive_start_equity_is_an_observation_shortfall() -> None:
    b1 = final_intervals(SCORING)[1].interval.start
    material = _material([(b1, "0")], "100")
    first, second, *_ = interval_floor_results(FOLD_FLOORS, material)
    assert first.outcome is ConditionOutcome.NOT_MET  # −100%
    assert (second.outcome, second.unavailable_reason, second.observed) == (
        ConditionOutcome.UNCOMPUTABLE,
        MetricUnavailableReason.UNDEFINED_DENOMINATOR,
        None,
    )


# --- FV5-T2: 区間の損失の床 ------------------------------------------------------------------


def _breaching() -> FinalLossSideMaterial:
    """区間 1 が −0.20（床 −0.15 を割る）で、全体は +10% の人工の系列。"""
    b1, b2, b3 = (item.interval.start for item in final_intervals(SCORING)[1:])
    return _material([(b1, "1100000"), (b2, "880000"), (b3, "1000000")], "1100000")


def test_fv5_t2_an_interval_below_the_floor_with_enough_trades_is_below_standard() -> None:
    outcome = judge_final_variant(
        v5_standard(), Variant.STANDARD, _summary(_breaching()), REQUIREMENT
    )
    assert (outcome.verdict, outcome.profitability, outcome.evidence) == (
        SearchVerdict.BELOW_STANDARD,
        ProfitabilityAxis.NOT_MET,
        EvidenceAxis.SUFFICIENT,
    )
    breached = [
        item for item in outcome.interval_floors if item.outcome is ConditionOutcome.NOT_MET
    ]
    assert [item.interval.interval_index for item in breached] == [1]


def test_fv5_t2_an_interval_below_the_floor_with_too_few_trades_is_insufficient() -> None:
    """件数が `N` 未満で区間の床を割った: 証拠不足と理由「最低件数未満で損失の床を割った」。"""
    outcome = judge_final_variant(
        v5_standard(), Variant.STANDARD, _summary(_breaching(), trade_count=3), REQUIREMENT
    )
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert outcome.reasons == ("TOTAL_TRADES_BELOW", "FLOOR_BREACHED_TOTAL_TRADES_BELOW")
    assert outcome.profitability is ProfitabilityAxis.NOT_MET and outcome.hold is False


def test_fv5_t2_an_interval_at_exactly_the_floor_meets_it() -> None:
    """端数の区間も同じ −0.15（D13-7 (a)）。ちょうど −0.15 は満たす（`GE`）。"""
    b1, b2, b3 = (item.interval.start for item in final_intervals(SCORING)[1:])
    material = _material([(b1, "850000"), (b2, "900000"), (b3, "1000000")], "1100000")
    outcome = judge_final_variant(v5_standard(), Variant.STANDARD, _summary(material), REQUIREMENT)
    assert outcome.interval_floors[0].observed == _D("-0.15")
    assert outcome.interval_floors[0].interval.partial
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD


# --- FV5-T3: 損失の集中を当てない（決定 213） ---------------------------------------------------


def test_fv5_t3_a_concentrated_loss_within_the_floors_still_meets_the_standard() -> None:
    """窓の純収益率 0.12（≥ 0.10）、最悪の区間の損失 196,000 円 > 窓の純損益 120,000 円、各区間は
    −0.15 以上で、他の条件を満たす入力: 満たす。損失の集中の条件の結果は作らない。"""
    b1, b2 = (item.interval.start for item in final_intervals(EVEN)[1:])
    material = _material([(b1, "1400000"), (b2, "1204000")], "1120000", scoring=EVEN)
    outcome = judge_final_variant(
        v5_standard(), Variant.STANDARD, _summary(material, net="0.12"), REQUIREMENT
    )
    losses = [
        item.start_equity - item.end_equity
        for item in outcome.interval_floors
        if item.end_equity < item.start_equity
    ]
    assert max(losses) > material.settled_equity.amount - INITIAL.amount  # 集中していれば落ちる
    assert all(item.outcome is ConditionOutcome.MET for item in outcome.interval_floors)
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    assert not any(item.scope is ConditionScope.LOSS_CONCENTRATION for item in outcome.conditions)


# --- FV5-T4: PROFIT_FACTOR（決定 214） ----------------------------------------------------------


def _profit_factor(outcome_conditions: tuple[object, ...]) -> ProfitFactorResult:
    (found,) = [item for item in outcome_conditions if isinstance(item, ProfitFactorResult)]
    return found


@pytest.mark.parametrize(
    ("wins", "losses", "outcome", "verdict"),
    [
        ("50000", "0", ConditionOutcome.MET, SearchVerdict.MEETS_STANDARD),  # 負け無しは満たす
        ("12000", "10000", ConditionOutcome.MET, SearchVerdict.MEETS_STANDARD),  # W = 1.2 × L
        ("11999", "10000", ConditionOutcome.NOT_MET, SearchVerdict.BELOW_STANDARD),
    ],
)
def test_fv5_t4_the_profit_factor_follows_the_path_rule(
    wins: str, losses: str, outcome: ConditionOutcome, verdict: SearchVerdict
) -> None:
    material = dataclasses.replace(_steady(), trades=totals(wins, losses))
    judged = judge_final_variant(v5_standard(), Variant.STANDARD, _summary(material), REQUIREMENT)
    result = _profit_factor(judged.conditions)
    assert result.outcome is outcome and result.profit_factor == material.trades
    assert judged.verdict is verdict


def test_fv5_t4_no_trade_and_no_settlement_is_an_observation_shortfall() -> None:
    material = dataclasses.replace(_steady(), trades=totals("0", "0", closed=0))
    judged = judge_final_variant(v5_standard(), Variant.STANDARD, _summary(material), REQUIREMENT)
    assert _profit_factor(judged.conditions).outcome is ConditionOutcome.UNCOMPUTABLE
    assert judged.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert "METRIC_UNCOMPUTABLE" in judged.reasons


_WIN: Final = trade("2024-01-08T00:00:00Z", "2024-01-09T00:00:00Z", seq=1)  # +4,990 円


def _settlement(seq: int, profit: str) -> HypotheticalCloseLine:
    """run の終わりに仮に閉じた建玉 1 つ（明細の値は人工。損益だけが集合に効く）。"""
    return HypotheticalCloseLine(
        position_id=PositionId(seq=seq),
        side=OrderSide.BUY,
        quantity=Quantity(decimal_from_str("10000")),
        reference_bid=Price(decimal_from_str("150.00")),
        exit_price=Price(decimal_from_str("149.85")),
        commission=_yen("10"),
        slippage=_yen("100"),
        profit=_yen(profit),
    )


def _facts(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = SegmentTradeFacts(
        closed_trades=(_WIN,),
        open_entries=(
            OpenPositionEntry(
                position_id=PositionId(seq=3),
                entry_at=UtcTime.parse("2024-02-01T00:00:00Z"),
                entry_commission=_yen("10"),
            ),
        ),
    )
    monkeypatch.setattr(loss_side_module, "segment_trade_facts", lambda repository, run_id: facts)


_RUN: Final = RunId(ContentDigest.sha256("ab" * 32))
#: 材料の読み出し（`segment_trade_facts`）は差し替えたので、結果の置き場は使わない。
_NO_REPOSITORY: Final = cast("ResultRepository", object())


def test_fv5_t4_the_settlement_loss_at_the_run_end_enters_the_losses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run の終わりの仮決済（−1,500 − 入場の費用 10 = −1,510 円）が負けの合計に入る。"""
    _facts(monkeypatch)
    found = run_trade_totals(
        _NO_REPOSITORY,
        _RUN,
        initial_balance=INITIAL,
        settled_equity=_yen("1003480"),
        settlement_lines=(_settlement(3, "-1500"),),
    )
    assert (found.wins, found.losses) == (_yen("4990"), _yen("1510"))
    assert (found.closed_count, found.settlement_count) == (1, 1)
    material = dataclasses.replace(_steady(), trades=found)
    judged = judge_final_variant(v5_standard(), Variant.STANDARD, _summary(material), REQUIREMENT)
    assert _profit_factor(judged.conditions).profit_factor.losses == _yen("1510")


def test_fv5_t4_a_sum_that_does_not_match_the_run_net_is_a_structure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _facts(monkeypatch)
    with pytest.raises(KernelValueError, match="恒等式"):
        run_trade_totals(
            _NO_REPOSITORY,
            _RUN,
            initial_balance=INITIAL,
            settled_equity=_yen("1003000"),
            settlement_lines=(_settlement(3, "-1500"),),
        )


def test_fv5_t4_a_settled_position_that_is_not_open_in_the_run_is_a_structure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _facts(monkeypatch)
    with pytest.raises(KernelValueError, match="not an open position"):
        run_trade_totals(
            _NO_REPOSITORY,
            _RUN,
            initial_balance=INITIAL,
            settled_equity=_yen("1003480"),
            settlement_lines=(_settlement(9, "-1500"),),
        )


# --- FV5-T5: 当てない条件・局面・判定の入力 ----------------------------------------------------


def test_fv5_t5_research_only_conditions_are_named_and_never_judged() -> None:
    outcome = judge_final_variant(v5_standard(), Variant.STANDARD, _summary(_steady()), REQUIREMENT)
    assert (
        outcome.research_only
        == RESEARCH_ONLY_CONDITIONS
        == (
            ConditionScope.NEIGHBOR_FLOOR,
            ConditionScope.LOSS_CONCENTRATION,
            ConditionScope.DEFLATED_DOWNSIDE_RATIO,
        )
    )
    assert {item.scope for item in outcome.conditions} == {
        ConditionScope.PATH_FLOOR,
        ConditionScope.PATH_AGGREGATE,
    }
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD


def test_fv5_t5_the_judgement_takes_no_neighbor_trial_or_daily_bar_input() -> None:
    assert list(inspect.signature(judge_final_variant).parameters) == [
        "standard",
        "variant",
        "run",
        "requirement",
    ]
    assert [item.name for item in dataclasses.fields(FinalRunSummary)] == [
        "metrics",
        "trade_count",
        "loss_side",
    ]
    assert [item.name for item in dataclasses.fields(FinalLossSideMaterial)] == [
        "scoring",
        "initial_balance",
        "equity_points",
        "settled_equity",
        "trades",
    ]


# --- FV5-T1: 版 4 以下の候補は今のまま -------------------------------------------------------


def test_fv5_t1_a_v4_candidate_has_no_interval_floor_and_no_research_only_list() -> None:
    outcome = judge_final_variant(standard(), Variant.STANDARD, _summary(None), REQUIREMENT)
    assert outcome.interval_floors == () and outcome.research_only == ()
    assert not any(isinstance(item, ProfitFactorResult) for item in outcome.conditions)


def test_the_loss_side_material_belongs_to_v5_candidates_only() -> None:
    with pytest.raises(KernelValueError, match="no loss-side material"):
        judge_final_variant(standard(), Variant.STANDARD, _summary(_steady()), REQUIREMENT)
    with pytest.raises(KernelValueError, match="needs its loss-side material"):
        judge_final_variant(v5_standard(), Variant.STANDARD, _summary(None), REQUIREMENT)


def test_the_material_rejects_unordered_points_and_mixed_currencies() -> None:
    with pytest.raises(KernelValueError, match="昇順"):
        _material([(START + 2 * _DAY, "1"), (START + _DAY, "1")], "1")
    with pytest.raises(KernelValueError, match="通貨"):
        FinalLossSideMaterial(
            scoring=SCORING,
            initial_balance=INITIAL,
            equity_points=(),
            settled_equity=Money(_D("1"), JPY),
            trades=PathTradeTotals(
                wins=Money(_D("0"), CurrencyCode("USD")),
                losses=Money(_D("0"), CurrencyCode("USD")),
                closed_count=0,
                settlement_count=0,
                win_count=0,
                loss_count=0,
            ),
        )
