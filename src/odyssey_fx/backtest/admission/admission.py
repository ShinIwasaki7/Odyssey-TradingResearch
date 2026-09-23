"""発注試行の結末と受付（D06 §4.4・§5.2・§5.3・§6.5）。

受付前拒否は注文も予約も作らない。**同じ試行に複数の拒否理由が同時に成立**したときの
代表理由は次の順で決める（D06 §5.2）。

| 順位 | 理由コード | 先に判定する理由 |
|---|---|---|
| 1 | `RUN_END` | 末尾では他の検査の結果によらず受け付けない |
| 2 | `CARRY_NOT_ALLOWED` | 有効時間を長くしても回避できない規則だから |
| 3 | `NO_CANDIDATE` | 期限内に候補の始値が無い |
| 4 | `PROTECTION_INVALID` | 保護水準の妥当性 |
| 5 | `RISK` | 予算・数量・同時保持枠 |
| — | `DATA_ERROR` | 上のどの判定も行えないとき |

本モジュールは**審査と組み立てまで**を行い、台帳への確定は行わない（`portfolio.ledger`
の確定単位が受け持つ）。受付層と台帳層は互いに import できないためで、エンジンが両者を
つなぐ。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from odyssey_fx.backtest.admission.risk_assessment import (
    RiskAssessment,
    RiskAssessmentRef,
    assess_entry,
    non_positive_balance_reason,
)
from odyssey_fx.backtest.domain.account import AccountLedger
from odyssey_fx.backtest.domain.events import OrderEvent
from odyssey_fx.backtest.domain.orders import (
    AcceptedCloseTerms,
    AcceptedEntryTerms,
    AcceptedOrder,
    CloseRequest,
    Eligibility,
    EntryRequest,
    ExecutionCommitment,
    OrderRequest,
    OrderSide,
    OrderStatus,
    ReferenceQuote,
    ScheduledOpen,
)
from odyssey_fx.backtest.domain.policies import CostModel, RiskPolicy
from odyssey_fx.backtest.domain.reservations import RiskReservation
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AttemptId,
    EventId,
    EvidenceId,
    IdAllocator,
    OrderId,
    ReservationId,
)
from odyssey_fx.common.money import ConversionRate, Price, PriceOffset
from odyssey_fx.common.reason import (
    CarryNotAllowedDetail,
    NoCandidateDetail,
    PositionClosedDetail,
    Reason,
    ReasonCode,
    RunEndDetail,
)
from odyssey_fx.common.refs import EvidenceRef, PolicyRef
from odyssey_fx.common.symbol import SymbolSpec
from odyssey_fx.common.time import ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "AdmissionOutcome",
    "AttemptAccepted",
    "AttemptDecision",
    "AttemptRejected",
    "decide_close",
    "decide_entry",
]


@dataclass(frozen=True, slots=True)
class AttemptAccepted:
    """試行が受け付けられた（D06 §6.2）。決済は審査に入らないので参照は `None`。"""

    attempt_id: AttemptId
    order_id: OrderId
    assessment_ref: RiskAssessmentRef | None = None
    kind: str = "ACCEPTED"

    def __post_init__(self) -> None:
        if self.kind != "ACCEPTED":
            raise KernelValueError(f"AttemptAccepted.kind must be 'ACCEPTED', got {self.kind!r}")
        if not isinstance(self.attempt_id, AttemptId):
            raise KernelValueError("AttemptAccepted.attempt_id must be an AttemptId")
        if not isinstance(self.order_id, OrderId):
            raise KernelValueError("AttemptAccepted.order_id must be an OrderId")


@dataclass(frozen=True, slots=True)
class AttemptRejected:
    """試行が受付前に拒否された（D06 §5.2）。注文も予約も作らない。"""

    attempt_id: AttemptId
    reason: Reason
    assessment_ref: RiskAssessmentRef | None = None
    kind: str = "REJECTED"

    def __post_init__(self) -> None:
        if self.kind != "REJECTED":
            raise KernelValueError(f"AttemptRejected.kind must be 'REJECTED', got {self.kind!r}")
        if not isinstance(self.attempt_id, AttemptId):
            raise KernelValueError("AttemptRejected.attempt_id must be an AttemptId")
        if not isinstance(self.reason, Reason):
            raise KernelValueError("AttemptRejected.reason must be a Reason")


#: 区分タグ付き union（D06 §6.2）。
AttemptDecision = AttemptAccepted | AttemptRejected


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    """1件の要求を審査した結果（受付層 → エンジンの受け渡し）。

    D06 §3 の型表には無い**層をまたぐための運び手**である。審査（`admission`）と台帳への
    確定（`portfolio`）は互いに import できないので（D01 §3.3）、受付層は作った値をここに
    まとめて返し、エンジンが確定単位へ渡す。
    """

    decision: AttemptDecision
    assessment: RiskAssessment | None = None
    order: AcceptedOrder | None = None
    reservation: RiskReservation | None = None
    acceptance_event: OrderEvent | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, (AttemptAccepted, AttemptRejected)):
            raise KernelValueError("AdmissionOutcome.decision must be an AttemptDecision")
        accepted = isinstance(self.decision, AttemptAccepted)
        if accepted != (self.order is not None):
            raise KernelValueError("an accepted attempt carries exactly one accepted order")
        if accepted != (self.acceptance_event is not None):
            raise KernelValueError("an accepted attempt carries exactly one acceptance event")
        if not accepted and self.reservation is not None:
            raise KernelValueError("a rejected attempt must not create a reservation (D06 §5.2)")

    @property
    def accepted(self) -> bool:
        """受付できたか。"""
        return isinstance(self.decision, AttemptAccepted)


def _rejected(
    request: OrderRequest, reason: Reason, assessment: RiskAssessment | None = None
) -> AdmissionOutcome:
    return AdmissionOutcome(
        decision=AttemptRejected(
            attempt_id=request.attempt_id,
            reason=reason,
            assessment_ref=None if assessment is None else assessment.ref,
        ),
        assessment=assessment,
    )


def _acceptance_event(order: AcceptedOrder, event_id: EventId) -> OrderEvent:
    return OrderEvent(
        event_id=event_id,
        order_id=order.order_id,
        from_status=None,
        to_status=OrderStatus.PENDING,
        at=order.accepted_at,
    )


def decide_entry(
    request: OrderRequest,
    *,
    ledger: AccountLedger,
    allocator: IdAllocator,
    accepted_at: ProcessingPoint,
    expires_at: UtcTime,
    candidate: ScheduledOpen | None,
    execution_series: SeriesId,
    execution_policy_ref: PolicyRef,
    risk_policy: RiskPolicy,
    risk_policy_ref: PolicyRef,
    cost_model: CostModel,
    symbol_spec: SymbolSpec,
    reference_quote: ReferenceQuote | None,
    decision_bid: Price | None,
    adverse_fill_limit: PriceOffset | None,
    conversion: ConversionRate,
    new_assessment_id: Callable[[], EvidenceId],
    evidence_ref: EvidenceRef,
    run_end: UtcTime | None = None,
    carry_not_allowed: CarryNotAllowedDetail | None = None,
) -> AdmissionOutcome:
    """新規エントリーの要求を受け付ける（D06 §5.2・§5.3・§6.4・§6.5）。

    `new_assessment_id` は審査記録の識別子を採番する関数である。**手順3 まで到達した試行
    だけ**が審査記録を持つ（D06 §4.4）ので、そこへ進むと決まってから採番する。先に採番
    すると、記録を作らない拒否でも識別子の列が飛び、再実行の ID 列が入力以外の事情で変わる。
    """
    payload = request.payload
    if not isinstance(payload, EntryRequest):
        raise KernelValueError("decide_entry requires an entry request")

    if run_end is not None:
        return _rejected(request, Reason(ReasonCode.RUN_END, RunEndDetail(run_end=run_end)))
    if carry_not_allowed is not None:
        return _rejected(request, Reason(ReasonCode.CARRY_NOT_ALLOWED, carry_not_allowed))
    if candidate is None:
        return _rejected(
            request,
            Reason(
                ReasonCode.NO_CANDIDATE,
                NoCandidateDetail(expires_at=expires_at, earliest_candidate=None),
            ),
        )
    # 手順1（残高が非正なら拒否）は参照価格より**前**に判定する（D06 §6.4）。残高だけで
    # 決まる拒否なので、参照価格が取れない判断時点でも本当の理由のまま記録される。
    balance_rejection = non_positive_balance_reason(ledger)
    if balance_rejection is not None:
        return _rejected(request, balance_rejection)
    if reference_quote is None or decision_bid is None or adverse_fill_limit is None:
        # 参照価格も Δ も無いと手順3 より先へ進めない。審査記録は作らない（D06 §4.4）。
        return _rejected(request, Reason(ReasonCode.DATA_ERROR))

    assessment_id = new_assessment_id()
    # 換算率からその根拠記録へ辿れるようにする（上位設計書 §4.7.9 C）。審査記録の識別子は
    # 手順3 へ進むと決まってから採番するので、率へ結び付けられるのもここが最初になる。
    conversion = replace(conversion, evidence=EvidenceRef(evidence_id=assessment_id))

    assessment, rejection = assess_entry(
        payload,
        attempt_id=request.attempt_id,
        assessment_id=assessment_id,
        ledger=ledger,
        risk_policy=risk_policy,
        policy_ref=risk_policy_ref,
        cost_model=cost_model,
        symbol_spec=symbol_spec,
        reference_quote=reference_quote,
        decision_bid=decision_bid,
        adverse_fill_limit=adverse_fill_limit,
        conversion=conversion,
    )
    if rejection is not None:
        return _rejected(request, rejection, assessment)
    if (
        assessment.quantity is None
        or assessment.stop_after_rounding is None
        or assessment.adverse_fill_limit is None
        or assessment.reservation_amount is None
    ):  # pragma: no cover - 受付できた審査は手順8 まで到達している
        raise KernelValueError("an accepted assessment must have reached step 8 (D06 §6.4)")

    order_id = allocator.next(OrderId)
    reservation_id = allocator.next(ReservationId)
    order = AcceptedOrder(
        run_id=request.run_id,
        order_id=order_id,
        attempt_id=request.attempt_id,
        accepted_at=accepted_at,
        symbol=payload.symbol,
        side=payload.side,
        quantity=assessment.quantity,
        expires_at=expires_at,
        execution=ExecutionCommitment(
            policy_ref=execution_policy_ref,
            execution_series=execution_series,
            eligibility=candidate,
        ),
        terms=AcceptedEntryTerms(
            initial_stop=assessment.stop_after_rounding,
            exit_plan_ref=payload.exit_plan_ref,
            reservation_id=reservation_id,
            reference_quote=reference_quote,
            adverse_fill_limit=assessment.adverse_fill_limit,
        ),
        evidence_ref=evidence_ref,
    )
    reservation = RiskReservation(
        run_id=request.run_id,
        reservation_id=reservation_id,
        order_id=order_id,
        account_id=request.account_id,
        created_at=accepted_at,
        amount=assessment.reservation_amount,
        assessment_id=assessment.assessment_id,
    )
    return AdmissionOutcome(
        decision=AttemptAccepted(
            attempt_id=request.attempt_id, order_id=order_id, assessment_ref=assessment.ref
        ),
        assessment=assessment,
        order=order,
        reservation=reservation,
        acceptance_event=_acceptance_event(order, allocator.next(EventId)),
    )


def decide_close(
    request: OrderRequest,
    *,
    ledger: AccountLedger,
    allocator: IdAllocator,
    accepted_at: ProcessingPoint,
    expires_at: UtcTime,
    eligibility: Eligibility | None,
    execution_series: SeriesId,
    execution_policy_ref: PolicyRef,
    evidence_ref: EvidenceRef,
    run_end: UtcTime | None = None,
    carry_not_allowed: CarryNotAllowedDetail | None = None,
) -> AdmissionOutcome:
    """全数量決済の要求を受け付ける（D06 §6.4 の末尾）。

    決済は**新規リスク予算の審査対象ではない**（上位設計書 §4.7.15 A）。代わりに対象建玉の
    存否・数量・期限・執行条件を検査する。審査記録は作らず、`assessment_ref` は `None`。
    """
    payload = request.payload
    if not isinstance(payload, CloseRequest):
        raise KernelValueError("decide_close requires a close request")

    if run_end is not None:
        return _rejected(request, Reason(ReasonCode.RUN_END, RunEndDetail(run_end=run_end)))
    if carry_not_allowed is not None:
        return _rejected(request, Reason(ReasonCode.CARRY_NOT_ALLOWED, carry_not_allowed))

    position = ledger.positions.get(payload.position_id)
    if position is None:
        return _rejected(request, Reason(ReasonCode.DATA_ERROR))
    if not position.is_open:
        return _rejected(
            request,
            Reason(
                ReasonCode.POSITION_CLOSED,
                PositionClosedDetail(position_id=position.position_id, closed_at=accepted_at),
            ),
        )
    if eligibility is None:
        return _rejected(
            request,
            Reason(
                ReasonCode.NO_CANDIDATE,
                NoCandidateDetail(expires_at=expires_at, earliest_candidate=None),
            ),
        )

    order_id = allocator.next(OrderId)
    order = AcceptedOrder(
        run_id=request.run_id,
        order_id=order_id,
        attempt_id=request.attempt_id,
        accepted_at=accepted_at,
        symbol=position.symbol,
        side=OrderSide.SELL if position.side is OrderSide.BUY else OrderSide.BUY,
        quantity=position.quantity,
        expires_at=expires_at,
        execution=ExecutionCommitment(
            policy_ref=execution_policy_ref,
            execution_series=execution_series,
            eligibility=eligibility,
        ),
        terms=AcceptedCloseTerms(position_id=position.position_id, cause=payload.cause),
        evidence_ref=evidence_ref,
    )
    return AdmissionOutcome(
        decision=AttemptAccepted(attempt_id=request.attempt_id, order_id=order_id),
        order=order,
        acceptance_event=_acceptance_event(order, allocator.next(EventId)),
    )
