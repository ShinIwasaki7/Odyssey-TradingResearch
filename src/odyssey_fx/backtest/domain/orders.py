"""注文の型と状態（D06 §5・§6.1・§6.5・§7.1、上位設計書 §4.7.15 A・B）。

1回の発注試行は `OrderRequest`（審査前の要求）→ `AttemptDecision`（試行の結末）→
`AcceptedOrder`（受付条件を固定した注文）と進む。**受付前に拒否された試行は注文を作らない**
ので、機会から試行への連鎖は `OrderRequest` が保つ。

受付済み注文は不変であり、現在状態は `OrderEvent`（`domain.events`）の列から `OrderState`
へ投影する（D06 §5.1）。状態は `PENDING` / `FILLED` / `CANCELED` / `EXPIRED` の4つで、
遷移は6本しかない（同じ表を `domain.events` が実装する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AccountId,
    AttemptId,
    EventId,
    FillId,
    OpportunityId,
    OrderId,
    OutputId,
    PositionId,
    ReservationId,
    RunId,
)
from odyssey_fx.common.money import Price, Quantity
from odyssey_fx.common.reason import Reason
from odyssey_fx.common.refs import CompiledStrategyRef, EvidenceRef, PolicyRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

__all__ = [
    "AcceptedCloseTerms",
    "AcceptedEntryTerms",
    "AcceptedOrder",
    "CloseCause",
    "CloseRequest",
    "Eligibility",
    "EntryRequest",
    "ExecutionCommitment",
    "ExitPlanRef",
    "ImmediateAfterFill",
    "InitialProtectionPlan",
    "OrderPayload",
    "OrderRequest",
    "OrderSide",
    "OrderStatus",
    "OrderState",
    "OrderType",
    "ProtectionHit",
    "ReferenceQuote",
    "RequestClass",
    "RequestOrigin",
    "ScheduledOpen",
]


class OrderStatus(Enum):
    """受付済み注文の状態（D06 §5.1、上位設計書 §4.7.13 A）。

    `ACCEPTED` は状態ではなく受付イベントであり、直後の状態は `PENDING` である。
    """

    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


#: 終端した状態（ここからの遷移は表に無い、D06 §5.1）。
TERMINAL_ORDER_STATUSES = frozenset({OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED})


class OrderSide(Enum):
    """注文の売買方向（D06 §5.2）。買い建玉の決済は `SELL` になる。"""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """注文の種別。段階2は成行だけ（指値は能力検査で拒否、D06 §12）。"""

    MARKET = "MARKET"


class RequestOrigin(Enum):
    """要求の出どころ（D06 §6.1）。戦略が `ENGINE` を自己申告する経路は作らない。"""

    STRATEGY = "STRATEGY"
    ENGINE = "ENGINE"


class RequestClass(Enum):
    """全順序化の第2鍵（D06 §6.3）。決済（0）がエントリー（1）より先。"""

    CLOSE = "CLOSE"
    ENTRY = "ENTRY"

    @property
    def rank(self) -> int:
        """辞書式順序で使う順位。"""
        return 0 if self is RequestClass.CLOSE else 1


class CloseCause(Enum):
    """決済の執行契機（D06 §7.3・§7.5）。受付拒否の `ReasonCode` とは役割が違う。"""

    STRATEGY_EXIT = "STRATEGY_EXIT"
    EMERGENCY = "EMERGENCY"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


def _require(value: object, expected: type, label: str) -> None:
    if not isinstance(value, expected):
        raise KernelValueError(f"{label} must be a {expected.__name__}, got {value!r}")


def _require_optional(value: object, expected: type, label: str) -> None:
    if value is not None and not isinstance(value, expected):
        raise KernelValueError(f"{label} must be a {expected.__name__} or None, got {value!r}")


def _require_positive_duration(value: object, label: str) -> None:
    if not isinstance(value, timedelta):
        raise KernelValueError(f"{label} must be a timedelta, got {value!r}")
    if value <= timedelta(0):
        raise KernelValueError(f"{label} must be positive, got {value}")


@dataclass(frozen=True, slots=True)
class InitialProtectionPlan:
    """部品が出した初期の保護水準の案（D06 §6.1）。受付層が丸め・検査して凍結する。

    `take_profit` は段階2では常に `None` である。初期の利確は約定後の評価が決める
    （D06 §8.3）。
    """

    stop_loss: Price
    source_output_id: OutputId
    take_profit: Price | None = None

    def __post_init__(self) -> None:
        _require(self.stop_loss, Price, "InitialProtectionPlan.stop_loss")
        _require(self.source_output_id, OutputId, "InitialProtectionPlan.source_output_id")
        _require_optional(self.take_profit, Price, "InitialProtectionPlan.take_profit")
        if self.take_profit is not None:
            raise KernelValueError(
                "InitialProtectionPlan.take_profit must be None in stage 2; the initial take"
                " profit is produced after the fill (D06 §8.3)"
            )


@dataclass(frozen=True, slots=True)
class ExitPlanRef:
    """建玉の保護水準を管理する Exit の解決済み参照（D06 §6.1・§8.2）。"""

    compiled_ref: CompiledStrategyRef
    exit_instance_id: str | None = None

    def __post_init__(self) -> None:
        _require(self.compiled_ref, CompiledStrategyRef, "ExitPlanRef.compiled_ref")
        if self.exit_instance_id is not None and not self.exit_instance_id:
            raise KernelValueError("ExitPlanRef.exit_instance_id must not be empty")


@dataclass(frozen=True, slots=True)
class EntryRequest:
    """新規エントリーの要求内容（上位設計書 §4.7.15 A ＋ D06 §6.1 の `intent_output_id`）。"""

    opportunity_id: OpportunityId
    symbol: Symbol
    side: OrderSide
    order_type: OrderType
    protection: InitialProtectionPlan
    exit_plan_ref: ExitPlanRef
    valid_for: timedelta
    intent_output_id: OutputId
    kind: str = "ENTRY_REQUEST"

    def __post_init__(self) -> None:
        if self.kind != "ENTRY_REQUEST":
            raise KernelValueError(f"EntryRequest.kind must be 'ENTRY_REQUEST', got {self.kind!r}")
        _require(self.opportunity_id, OpportunityId, "EntryRequest.opportunity_id")
        _require(self.symbol, Symbol, "EntryRequest.symbol")
        _require(self.side, OrderSide, "EntryRequest.side")
        _require(self.order_type, OrderType, "EntryRequest.order_type")
        _require(self.protection, InitialProtectionPlan, "EntryRequest.protection")
        _require(self.exit_plan_ref, ExitPlanRef, "EntryRequest.exit_plan_ref")
        _require_positive_duration(self.valid_for, "EntryRequest.valid_for")
        _require(self.intent_output_id, OutputId, "EntryRequest.intent_output_id")

    @property
    def request_class(self) -> RequestClass:
        """全順序化の区分（D06 §6.3）。"""
        return RequestClass.ENTRY

    @property
    def origin_seq(self) -> int:
        """全順序化の第4鍵（発端となった取引機会の連番。D06 §6.3）。"""
        return self.opportunity_id.seq


@dataclass(frozen=True, slots=True)
class CloseRequest:
    """全数量決済の要求内容（上位設計書 §4.7.15 A、D06 §6.1）。

    銘柄・方向・数量は対象建玉から決まるので要求には重複させない。`source_output_id` は
    戦略由来の決済だけが持ち、エンジンが生成する決済（保護到達・緊急決済）では `None`。
    """

    position_id: PositionId
    cause: CloseCause
    valid_for: timedelta
    source_output_id: OutputId | None = None
    kind: str = "CLOSE_REQUEST"

    def __post_init__(self) -> None:
        if self.kind != "CLOSE_REQUEST":
            raise KernelValueError(f"CloseRequest.kind must be 'CLOSE_REQUEST', got {self.kind!r}")
        _require(self.position_id, PositionId, "CloseRequest.position_id")
        _require(self.cause, CloseCause, "CloseRequest.cause")
        _require_positive_duration(self.valid_for, "CloseRequest.valid_for")
        _require_optional(self.source_output_id, OutputId, "CloseRequest.source_output_id")
        if self.cause is not CloseCause.STRATEGY_EXIT and self.source_output_id is not None:
            raise KernelValueError(
                "only a strategy exit carries a source output id; engine-generated closes have"
                " no component output behind them (D06 §6.1)"
            )

    @property
    def request_class(self) -> RequestClass:
        """全順序化の区分（D06 §6.3）。"""
        return RequestClass.CLOSE

    @property
    def origin_seq(self) -> int:
        """全順序化の第4鍵（発端となった建玉の連番。D06 §6.3）。

        上位設計書 §4.7.12 の鍵は `opportunity_id` だが、決済要求は機会を持たない。同じ位置に
        「発端となった対象の連番」を置いて型を揃える。
        """
        return self.position_id.seq


#: 区分タグ付き union（D06 §6.1）。
OrderPayload = EntryRequest | CloseRequest


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """1回の審査対象（上位設計書 §4.7.15 A）。受付前拒否もこの記録に紐付く。"""

    run_id: RunId
    attempt_id: AttemptId
    account_id: AccountId
    strategy_id: str
    created_at: ProcessingPoint
    origin: RequestOrigin
    payload: OrderPayload
    evidence_ref: EvidenceRef
    previous_attempt_id: AttemptId | None = None

    def __post_init__(self) -> None:
        _require(self.run_id, RunId, "OrderRequest.run_id")
        _require(self.attempt_id, AttemptId, "OrderRequest.attempt_id")
        _require(self.account_id, AccountId, "OrderRequest.account_id")
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise KernelValueError("OrderRequest.strategy_id must be a non-empty str")
        _require(self.created_at, ProcessingPoint, "OrderRequest.created_at")
        _require(self.origin, RequestOrigin, "OrderRequest.origin")
        if not isinstance(self.payload, (EntryRequest, CloseRequest)):
            raise KernelValueError(
                f"OrderRequest.payload must be an OrderPayload, got {self.payload!r}"
            )
        _require(self.evidence_ref, EvidenceRef, "OrderRequest.evidence_ref")
        _require_optional(self.previous_attempt_id, AttemptId, "OrderRequest.previous_attempt_id")
        if self.previous_attempt_id is not None:
            raise KernelValueError(
                "OrderRequest.previous_attempt_id must be None in stage 2; re-assessment is"
                " out of scope (D06 §6.1)"
            )
        if isinstance(self.payload, EntryRequest) and self.origin is not RequestOrigin.STRATEGY:
            raise KernelValueError(
                "an entry request always originates from the strategy (D06 §6.1)"
            )

    @property
    def request_class(self) -> RequestClass:
        """全順序化の区分（D06 §6.3）。"""
        return self.payload.request_class

    @property
    def origin_seq(self) -> int:
        """全順序化の第4鍵（エントリーは機会、決済は建玉の連番。D06 §6.3）。"""
        if isinstance(self.payload, EntryRequest):
            return self.payload.opportunity_id.seq
        return self.payload.position_id.seq


@dataclass(frozen=True, slots=True)
class ReferenceQuote:
    """受付判断に使った参照価格（D06 §6.4 の手順3）。

    `derived_from_spread=True` は、bid だけの系列から spread モデルで ask を導いたことを表す。
    """

    price: Price
    basis: PriceBasis
    observed_at: UtcTime
    derived_from_spread: bool
    source_bar: BarKey | None = None

    def __post_init__(self) -> None:
        _require(self.price, Price, "ReferenceQuote.price")
        _require(self.basis, PriceBasis, "ReferenceQuote.basis")
        _require(self.observed_at, UtcTime, "ReferenceQuote.observed_at")
        if not isinstance(self.derived_from_spread, bool):
            raise KernelValueError("ReferenceQuote.derived_from_spread must be a bool")
        _require_optional(self.source_bar, BarKey, "ReferenceQuote.source_bar")


@dataclass(frozen=True, slots=True)
class ScheduledOpen:
    """通常の成行が待つ候補の始値（D06 §7.1）。欠損時に後続の足へ置換しない。"""

    bar_key: BarKey
    open_time: UtcTime
    kind: str = "SCHEDULED_OPEN"

    def __post_init__(self) -> None:
        if self.kind != "SCHEDULED_OPEN":
            raise KernelValueError(
                f"ScheduledOpen.kind must be 'SCHEDULED_OPEN', got {self.kind!r}"
            )
        _require(self.bar_key, BarKey, "ScheduledOpen.bar_key")
        _require(self.open_time, UtcTime, "ScheduledOpen.open_time")


@dataclass(frozen=True, slots=True)
class ImmediateAfterFill:
    """約定直後の緊急決済（D06 §7.5）。同じ始値の中で受付と約定が確定する。"""

    trigger_fill_id: FillId
    open_event_id: EventId
    kind: str = "IMMEDIATE_AFTER_FILL"

    def __post_init__(self) -> None:
        if self.kind != "IMMEDIATE_AFTER_FILL":
            raise KernelValueError(
                f"ImmediateAfterFill.kind must be 'IMMEDIATE_AFTER_FILL', got {self.kind!r}"
            )
        _require(self.trigger_fill_id, FillId, "ImmediateAfterFill.trigger_fill_id")
        _require(self.open_event_id, EventId, "ImmediateAfterFill.open_event_id")


@dataclass(frozen=True, slots=True)
class ProtectionHit:
    """保護水準の到達による決済（D06 §7.3）。既に有効だった保護の版と結び付ける。"""

    position_id: PositionId
    protection_version: int
    execution_bar_key: BarKey
    kind: str = "PROTECTION_HIT"

    def __post_init__(self) -> None:
        if self.kind != "PROTECTION_HIT":
            raise KernelValueError(
                f"ProtectionHit.kind must be 'PROTECTION_HIT', got {self.kind!r}"
            )
        _require(self.position_id, PositionId, "ProtectionHit.position_id")
        if isinstance(self.protection_version, bool) or not isinstance(
            self.protection_version, int
        ):
            raise KernelValueError("ProtectionHit.protection_version must be an int")
        if self.protection_version < 1:
            raise KernelValueError("ProtectionHit.protection_version must be >= 1")
        _require(self.execution_bar_key, BarKey, "ProtectionHit.execution_bar_key")


#: 区分タグ付き union（D06 §7.1）。
Eligibility = ScheduledOpen | ImmediateAfterFill | ProtectionHit


@dataclass(frozen=True, slots=True)
class ExecutionCommitment:
    """約定方式と候補を固定する内容（上位設計書 §4.7.15 B、D06 §7.1）。"""

    policy_ref: PolicyRef
    execution_series: SeriesId
    eligibility: Eligibility

    def __post_init__(self) -> None:
        _require(self.policy_ref, PolicyRef, "ExecutionCommitment.policy_ref")
        _require(self.execution_series, SeriesId, "ExecutionCommitment.execution_series")
        if not isinstance(self.eligibility, (ScheduledOpen, ImmediateAfterFill, ProtectionHit)):
            raise KernelValueError(
                f"ExecutionCommitment.eligibility must be an Eligibility, got {self.eligibility!r}"
            )


@dataclass(frozen=True, slots=True)
class AcceptedEntryTerms:
    """エントリーの受付結果（上位設計書 §4.7.15 B）。"""

    initial_stop: Price
    exit_plan_ref: ExitPlanRef
    reservation_id: ReservationId
    reference_quote: ReferenceQuote
    adverse_fill_limit: Price
    kind: str = "ENTRY_TERMS"

    def __post_init__(self) -> None:
        if self.kind != "ENTRY_TERMS":
            raise KernelValueError(
                f"AcceptedEntryTerms.kind must be 'ENTRY_TERMS', got {self.kind!r}"
            )
        _require(self.initial_stop, Price, "AcceptedEntryTerms.initial_stop")
        _require(self.exit_plan_ref, ExitPlanRef, "AcceptedEntryTerms.exit_plan_ref")
        _require(self.reservation_id, ReservationId, "AcceptedEntryTerms.reservation_id")
        _require(self.reference_quote, ReferenceQuote, "AcceptedEntryTerms.reference_quote")
        _require(self.adverse_fill_limit, Price, "AcceptedEntryTerms.adverse_fill_limit")


@dataclass(frozen=True, slots=True)
class AcceptedCloseTerms:
    """決済の受付結果（上位設計書 §4.7.15 B）。新規予約は作らない。"""

    position_id: PositionId
    cause: CloseCause
    kind: str = "CLOSE_TERMS"

    def __post_init__(self) -> None:
        if self.kind != "CLOSE_TERMS":
            raise KernelValueError(
                f"AcceptedCloseTerms.kind must be 'CLOSE_TERMS', got {self.kind!r}"
            )
        _require(self.position_id, PositionId, "AcceptedCloseTerms.position_id")
        _require(self.cause, CloseCause, "AcceptedCloseTerms.cause")


@dataclass(frozen=True, slots=True)
class AcceptedOrder:
    """受付条件を固定した注文（上位設計書 §4.7.15 B）。不変で、状態は別に投影する。"""

    run_id: RunId
    order_id: OrderId
    attempt_id: AttemptId
    accepted_at: ProcessingPoint
    symbol: Symbol
    side: OrderSide
    quantity: Quantity
    expires_at: UtcTime
    execution: ExecutionCommitment
    terms: AcceptedEntryTerms | AcceptedCloseTerms
    evidence_ref: EvidenceRef

    def __post_init__(self) -> None:
        _require(self.run_id, RunId, "AcceptedOrder.run_id")
        _require(self.order_id, OrderId, "AcceptedOrder.order_id")
        _require(self.attempt_id, AttemptId, "AcceptedOrder.attempt_id")
        _require(self.accepted_at, ProcessingPoint, "AcceptedOrder.accepted_at")
        _require(self.symbol, Symbol, "AcceptedOrder.symbol")
        _require(self.side, OrderSide, "AcceptedOrder.side")
        _require(self.quantity, Quantity, "AcceptedOrder.quantity")
        _require(self.expires_at, UtcTime, "AcceptedOrder.expires_at")
        _require(self.execution, ExecutionCommitment, "AcceptedOrder.execution")
        if not isinstance(self.terms, (AcceptedEntryTerms, AcceptedCloseTerms)):
            raise KernelValueError(
                f"AcceptedOrder.terms must be accepted terms, got {self.terms!r}"
            )
        _require(self.evidence_ref, EvidenceRef, "AcceptedOrder.evidence_ref")
        if isinstance(self.terms, AcceptedEntryTerms) and not isinstance(
            self.execution.eligibility, ScheduledOpen
        ):
            raise KernelValueError(
                "an entry order waits for a scheduled open; the immediate eligibilities belong to"
                " engine-generated closes (D06 §6.2)"
            )

    @property
    def is_entry(self) -> bool:
        """エントリー注文かどうか。"""
        return isinstance(self.terms, AcceptedEntryTerms)


@dataclass(frozen=True, slots=True)
class OrderState:
    """`OrderEvent` の列から投影した現在状態（D06 §5.1）。"""

    order_id: OrderId
    status: OrderStatus
    last_event_id: EventId
    last_processed_at: ProcessingPoint
    terminal_reason: Reason | None = None

    def __post_init__(self) -> None:
        _require(self.order_id, OrderId, "OrderState.order_id")
        _require(self.status, OrderStatus, "OrderState.status")
        _require(self.last_event_id, EventId, "OrderState.last_event_id")
        _require(self.last_processed_at, ProcessingPoint, "OrderState.last_processed_at")
        _require_optional(self.terminal_reason, Reason, "OrderState.terminal_reason")
        if self.terminal_reason is not None and self.status not in TERMINAL_ORDER_STATUSES:
            raise KernelValueError(
                f"only a terminal order carries a terminal reason, got {self.status.value}"
            )

    @property
    def is_terminal(self) -> bool:
        """終端状態かどうか（終端からの遷移は表に無い、D06 §5.1）。"""
        return self.status in TERMINAL_ORDER_STATUSES
