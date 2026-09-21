"""受付（D06 §5.2・§5.3・§6.3・§6.4・§6.5）。

全順序化の鍵、丸めの方向、予算の式、審査がどこまで進んだかの記録を確かめる。数値は
T01 §2.3 の手計算（予算 20,000 円・1通貨あたり 0.622 円・数量 32,000）と一致する。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from odyssey_fx.backtest.admission.admission import AttemptRejected, decide_entry
from odyssey_fx.backtest.admission.request_assembly import (
    AdmissionKey,
    build_request,
    order_payloads,
)
from odyssey_fx.backtest.admission.risk_assessment import (
    assess_entry,
    non_positive_balance_reason,
    round_adverse_limit,
    round_stop,
)
from odyssey_fx.backtest.domain.account import AccountLedger
from odyssey_fx.backtest.domain.orders import (
    CloseCause,
    CloseRequest,
    EntryRequest,
    ExitPlanRef,
    InitialProtectionPlan,
    OrderSide,
    OrderType,
    ReferenceQuote,
    RequestClass,
    RequestOrigin,
    ScheduledOpen,
)
from odyssey_fx.backtest.portfolio.conversion import identity_path, rate_of
from odyssey_fx.common.ids import (
    AttemptId,
    EvidenceId,
    IdAllocator,
    OpportunityId,
    OutputId,
    PositionId,
    RunId,
)
from odyssey_fx.common.money import Money, Price, PriceOffset, decimal_from_str
from odyssey_fx.common.reason import ReasonCode
from odyssey_fx.common.refs import (
    CompiledStrategyRef,
    ContentDigest,
    EvidenceRef,
    PolicyRef,
)
from odyssey_fx.common.time import PhaseRank, ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.series import PriceBasis
from tests.fixtures.backtest.harness import (
    ACCOUNT,
    COST_MODEL,
    EXECUTION_SERIES,
    JPY,
    RISK_POLICY,
    SYMBOL_SPEC,
)
from tests.fixtures.synthetic.market import USDJPY

MOMENT = UtcTime.from_components(2026, 1, 6, 9, 0)
DIGEST = ContentDigest.sha256("0" * 64)
POLICY = PolicyRef(policy_kind="risk", policy_id="risk_v1", version=1, digest=DIGEST)
RUN_ID = RunId(digest=DIGEST)
POINT = ProcessingPoint(MOMENT, PhaseRank(10, "ADMISSION"), 0)


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _money(text: str) -> Money:
    return Money(decimal_from_str(text), JPY)


def _entry(
    stop: str = "149.500", opportunity: int = 1, side: OrderSide = OrderSide.BUY
) -> EntryRequest:
    return EntryRequest(
        opportunity_id=OpportunityId(opportunity),
        symbol=USDJPY,
        side=side,
        order_type=OrderType.MARKET,
        protection=InitialProtectionPlan(stop_loss=_price(stop), source_output_id=OutputId(5)),
        exit_plan_ref=ExitPlanRef(compiled_ref=CompiledStrategyRef(digest=DIGEST)),
        valid_for=timedelta(minutes=20),
        intent_output_id=OutputId(4),
    )


def _close(position: int = 1) -> CloseRequest:
    return CloseRequest(
        position_id=PositionId(position),
        cause=CloseCause.STRATEGY_EXIT,
        valid_for=timedelta(minutes=20),
        source_output_id=OutputId(6),
    )


def _quote(price: str = "150.060", *, derived: bool = True) -> ReferenceQuote:
    """参照価格（買いは ask、売りは bid。上位設計書 §4.7.9 C）。"""
    return ReferenceQuote(
        price=_price(price),
        basis=PriceBasis.ASK if derived else PriceBasis.BID,
        observed_at=MOMENT,
        derived_from_spread=derived,
    )


def _assess(
    stop: str = "149.500",
    *,
    decision_bid: str = "150.040",
    ledger: AccountLedger | None = None,
    side: OrderSide = OrderSide.BUY,
) -> tuple[object, object]:
    quote = _quote() if side is OrderSide.BUY else _quote(decision_bid, derived=False)
    return assess_entry(
        _entry(stop, side=side),
        attempt_id=AttemptId(1),
        assessment_id=EvidenceId(2),
        ledger=AccountLedger.opened(ACCOUNT) if ledger is None else ledger,
        risk_policy=RISK_POLICY,
        policy_ref=POLICY,
        cost_model=COST_MODEL,
        symbol_spec=SYMBOL_SPEC,
        reference_quote=quote,
        decision_bid=_price(decision_bid),
        adverse_fill_limit=PriceOffset(decimal_from_str("0.05")),
        conversion=rate_of(identity_path(MOMENT), JPY, JPY),
    )


# --- 全順序化 ---------------------------------------------------------------


def test_a_close_is_ordered_before_an_entry() -> None:
    """D06 §6.3: 決済（0）がエントリー（1）より先。"""
    ordered = order_payloads([_entry(), _close()])

    assert isinstance(ordered[0], CloseRequest)
    assert isinstance(ordered[1], EntryRequest)


def test_entries_are_ordered_by_the_opportunity_sequence() -> None:
    """D06 §6.3: 同順位内は発端となった対象の連番の昇順。"""
    ordered = order_payloads([_entry(opportunity=3), _entry(opportunity=2)])

    assert [payload.origin_seq for payload in ordered] == [2, 3]


def test_the_key_compares_by_sequence_not_by_text() -> None:
    """D06 §6.3: ID の比較は連番で行い、8桁ゼロ詰めの文字列では比較しない。"""
    smaller = AdmissionKey(
        decision_time=MOMENT,
        request_class=RequestClass.ENTRY,
        strategy_priority=0,
        origin_seq=9_999_999,
        attempt_seq=1,
    )
    larger = AdmissionKey(
        decision_time=MOMENT,
        request_class=RequestClass.ENTRY,
        strategy_priority=0,
        origin_seq=100_000_000,
        attempt_seq=1,
    )

    assert smaller < larger
    assert str(smaller.origin_seq) > str(larger.origin_seq)


# --- 丸め -------------------------------------------------------------------


def test_the_stop_of_a_buy_rounds_away_from_the_entry() -> None:
    """D06 §6.5: 意図より近い損切りへ黙って変更しない（買いは `DOWN`）。"""
    assert round_stop(_price("149.5004"), OrderSide.BUY, SYMBOL_SPEC) == _price("149.500")
    assert round_stop(_price("149.5004"), OrderSide.SELL, SYMBOL_SPEC) == _price("149.501")


def test_the_adverse_limit_does_not_widen() -> None:
    """D06 §6.5: 許容不利約定幅は値幅を広げない向きへ丸める。"""
    assert round_adverse_limit(PriceOffset(decimal_from_str("0.0509")), SYMBOL_SPEC) == PriceOffset(
        decimal_from_str("0.050")
    )


# --- 審査の8手順 ------------------------------------------------------------


def test_the_budget_follows_the_paper_trace() -> None:
    """T01 §2.3 の手順2: 試行予算 20,000・口座残余 200,000・受付予算 20,000。"""
    assessment, rejection = _assess()

    assert rejection is None
    assert assessment.budget.trial_budget == _money("20000")  # type: ignore[attr-defined]
    assert assessment.budget.account_remaining == _money("200000")  # type: ignore[attr-defined]
    assert assessment.budget.admission_budget == _money("20000")  # type: ignore[attr-defined]


def test_the_quantity_is_rounded_down_to_the_step() -> None:
    """T01 §2.3 の手順6: `20000 / 0.622 = 32154.3...` を刻み 1000 で切り下げて 32,000。"""
    assessment, rejection = _assess()

    assert rejection is None
    assert assessment.quantity.units == decimal_from_str("32000")  # type: ignore[attr-defined]
    assert assessment.reservation_amount == _money("19904")  # type: ignore[attr-defined]
    assert assessment.reached_step == 8  # type: ignore[attr-defined]


def test_an_invalid_protection_stops_at_step_four() -> None:
    """T01 §5.2: 買いの損切りが判断時 bid 以上なら `PROTECTION_INVALID`。"""
    assessment, rejection = _assess("149.990", decision_bid="149.980")

    assert rejection is not None
    assert rejection.code is ReasonCode.PROTECTION_INVALID  # type: ignore[attr-defined]
    assert assessment.reached_step == 4  # type: ignore[attr-defined]
    assert assessment.stop_after_rounding is None  # type: ignore[attr-defined]
    assert assessment.quantity is None  # type: ignore[attr-defined]
    assert assessment.reservation_amount is None  # type: ignore[attr-defined]


def test_the_checks_carry_the_observed_values() -> None:
    """D06 §6.4 の手順8: どの検査で落ちたかの実値を残す。"""
    assessment, _ = _assess("149.990", decision_bid="149.980")

    check = assessment.checks[-1]  # type: ignore[attr-defined]
    assert check.check == "protection_direction"
    assert check.passed is False
    assert check.limit == decimal_from_str("149.980")
    assert check.observed == decimal_from_str("149.990")


def test_a_tiny_budget_is_rejected_for_the_minimum_quantity() -> None:
    """D06 §6.4 の手順6: 最小数量未満なら拒否し、切り上げない。"""
    small = AccountLedger.opened(
        type(ACCOUNT)(
            account_id=ACCOUNT.account_id,
            currency=JPY,
            initial_balance=_money("100"),
        )
    )
    assessment, rejection = _assess(ledger=small)

    assert rejection is not None
    assert rejection.code is ReasonCode.RISK  # type: ignore[attr-defined]
    assert assessment.reached_step == 6  # type: ignore[attr-defined]
    assert assessment.quantity is None  # type: ignore[attr-defined]


# --- 売りの参照価格（D06 §6.4 の手順3・4）-----------------------------------


def test_a_short_is_checked_against_the_ask() -> None:
    """上位設計書 §4.7.9 B・C: 売りの参照価格は bid、保護水準の検査は ask と比べる。

    直前に完了した執行足の終値（bid）150.040 に対し ask は 150.060 である。損切り
    150.050 は ask より下なので、売りの保護水準としては不正になる。
    """
    assessment, rejection = _assess("150.050", side=OrderSide.SELL)

    assert rejection is not None
    assert rejection.code is ReasonCode.PROTECTION_INVALID  # type: ignore[attr-defined]
    check = assessment.checks[-1]  # type: ignore[attr-defined]
    assert check.limit == decimal_from_str("150.060")
    assert check.observed == decimal_from_str("150.050")


def test_a_short_sizes_from_the_bid_reference() -> None:
    """同節: 売りの1通貨あたりの予約リスクは bid を基準に測る。

    `P_limit = 150.040 - 0.050 = 149.990`、`d x (P_limit - S) = 150.500 - 149.990 = 0.510`、
    費用予算 0.012 を足して 0.522。`20000 / 0.522 = 38314.1...` を刻み 1000 で切り下げて
    38,000 通貨になる。
    """
    assessment, rejection = _assess("150.500", side=OrderSide.SELL)

    assert rejection is None
    assert assessment.quantity is not None  # type: ignore[attr-defined]
    assert assessment.quantity.units == decimal_from_str("38000")  # type: ignore[attr-defined]
    assert assessment.adverse_fill_limit == _price("149.990")  # type: ignore[attr-defined]
    assert assessment.reference_quote.derived_from_spread is False  # type: ignore[attr-defined]


# --- 手順1: 残高（D06 §6.4）-------------------------------------------------


def _drained() -> AccountLedger:
    """残高を使い切った台帳（口座仕様は正の初期残高しか許さないので差し替えで作る）。"""
    return replace(AccountLedger.opened(ACCOUNT), balance=_money("0"))


def test_a_non_positive_balance_is_rejected_before_the_reference_price() -> None:
    """D06 §6.4 の手順1: 残高が非正なら、参照価格を作る前に拒否する。

    残高だけで決まる拒否なので、参照価格が取れない判断時点でも `RISK` のまま記録される。
    後ろに置くと、同じ状況が「データが無い」（`DATA_ERROR`）として残り、本当の原因が
    判断履歴から読めなくなる。
    """
    reason = non_positive_balance_reason(_drained())

    assert reason is not None
    assert reason.code is ReasonCode.RISK
    assert reason.detail is not None
    assert reason.detail.check == "positive_balance"  # type: ignore[attr-defined]
    assert reason.detail.observed == _money("0")  # type: ignore[attr-defined]


def test_a_funded_balance_passes_step_one() -> None:
    """残高が正なら手順1 は理由を返さない（以降の手順へ進む）。"""
    assert non_positive_balance_reason(AccountLedger.opened(ACCOUNT)) is None


def test_the_balance_rejection_carries_no_assessment_record() -> None:
    """D06 §4.4: 審査記録を持つのは手順3 へ到達した試行だけ。

    手順1 の拒否では参照価格が無く、`RiskAssessment` を作れない。発注試行の記録が指す
    審査記録は `None` になる。参照価格が取れない状況でも拒否理由は `RISK` のままである。
    """
    allocator = IdAllocator(RUN_ID)
    request = build_request(
        _entry(),
        allocator=allocator,
        run_id=RUN_ID,
        account_id=ACCOUNT.account_id,
        strategy_id="strategy_a",
        created_at=POINT,
        origin=RequestOrigin.STRATEGY,
        evidence_ref=EvidenceRef(evidence_id=EvidenceId(1)),
    )
    outcome = decide_entry(
        request,
        ledger=_drained(),
        allocator=allocator,
        accepted_at=POINT,
        expires_at=MOMENT + timedelta(minutes=20),
        candidate=ScheduledOpen(
            bar_key=BarKey(series=EXECUTION_SERIES, bar_start=MOMENT),
            open_time=MOMENT,
        ),
        execution_series=EXECUTION_SERIES,
        execution_policy_ref=POLICY,
        risk_policy=RISK_POLICY,
        risk_policy_ref=POLICY,
        cost_model=COST_MODEL,
        symbol_spec=SYMBOL_SPEC,
        reference_quote=None,
        decision_bid=None,
        adverse_fill_limit=None,
        conversion=rate_of(identity_path(MOMENT), JPY, JPY),
        new_assessment_id=lambda: allocator.next(EvidenceId),
        evidence_ref=EvidenceRef(evidence_id=EvidenceId(1)),
    )

    assert outcome.accepted is False
    assert outcome.assessment is None
    decision = outcome.decision
    assert isinstance(decision, AttemptRejected)
    assert decision.reason.code is ReasonCode.RISK
    assert decision.assessment_ref is None
