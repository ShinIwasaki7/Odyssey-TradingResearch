"""注文の状態遷移イベントと、そこからの状態投影（D06 §4.4・§5.1）。

`OrderEvent` は `event_id` を持ち、**イベント処理済み記録・注文状態・会計への副作用を
一緒に確定する**（上位設計書 §4.7.13 A）。現在状態は `AcceptedOrder` を書き換えず、この
イベントの列から `OrderState` へ投影する。

遷移は D06 §5.1 の表の6本ですべてで、表に無い組み合わせ（終端状態からの遷移、理由コードの
食い違い、フェーズの食い違い）は `KernelValueError` で拒否する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from odyssey_fx.backtest.domain.orders import OrderState, OrderStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EventId, FillId, OrderId
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.time import ProcessingPoint

__all__ = [
    "ORDER_TRANSITIONS",
    "OrderEvent",
    "OrderTransition",
    "apply_order_event",
    "find_transition",
]


@dataclass(frozen=True, slots=True)
class OrderTransition:
    """D06 §5.1 の遷移表の1行。"""

    number: int
    from_status: OrderStatus | None
    to_status: OrderStatus
    reason_code: ReasonCode | None
    phases: tuple[str, ...]
    trigger: str

    def __post_init__(self) -> None:
        if not self.phases:
            raise KernelValueError("OrderTransition.phases must not be empty")


#: D06 §5.1 の6本の遷移。番号は同節の表の `#` に一致させる。
ORDER_TRANSITIONS: Final[tuple[OrderTransition, ...]] = (
    OrderTransition(
        number=1,
        from_status=None,
        to_status=OrderStatus.PENDING,
        reason_code=None,
        # 受付フェーズは要求の種類で変わる（D06 §6.2 の表）。
        phases=("ADMISSION", "EXECUTION_BAR_COMPLETE", "EXECUTION_OPEN", "POST_FILL_ADMISSION"),
        trigger="accepted",
    ),
    OrderTransition(
        number=2,
        from_status=OrderStatus.PENDING,
        to_status=OrderStatus.FILLED,
        reason_code=None,
        phases=("EXECUTION_OPEN", "EXECUTION_BAR_COMPLETE"),
        trigger="filled",
    ),
    OrderTransition(
        number=3,
        from_status=OrderStatus.PENDING,
        to_status=OrderStatus.EXPIRED,
        reason_code=ReasonCode.EXPIRED,
        phases=("ORDER_EXPIRY",),
        trigger="expired",
    ),
    OrderTransition(
        number=4,
        from_status=OrderStatus.PENDING,
        to_status=OrderStatus.CANCELED,
        reason_code=ReasonCode.RUN_END,
        phases=("RUN_END",),
        trigger="run_end",
    ),
    OrderTransition(
        number=5,
        from_status=OrderStatus.PENDING,
        to_status=OrderStatus.CANCELED,
        reason_code=ReasonCode.DATA_ERROR,
        # 失敗を検出したフェーズはどれにもなりうる（D06 §5.1 の遷移5）。
        phases=(
            "EXECUTION_BAR_COMPLETE",
            "LEDGER_UPDATE",
            "ORDER_EXPIRY",
            "PUBLICATION",
            "OPPORTUNITY_LIFECYCLE",
            "P1_FEATURE",
            "P2_MARKET_STATE",
            "P3_TRIGGER",
            "P4_CONFIRMATION",
            "P5_ORDER_INTENT",
            "ADMISSION",
            "EXECUTION_OPEN",
            "POST_FILL_EVALUATION",
            "POST_FILL_ADMISSION",
            "RUN_END",
        ),
        trigger="data_error",
    ),
    OrderTransition(
        number=6,
        from_status=OrderStatus.PENDING,
        to_status=OrderStatus.CANCELED,
        reason_code=ReasonCode.POSITION_CLOSED,
        phases=("EXECUTION_OPEN", "EXECUTION_BAR_COMPLETE"),
        trigger="position_closed",
    ),
)


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """注文の状態遷移1件（D06 §3・§4.4）。再配送でも同じ `event_id` を持つ。"""

    event_id: EventId
    order_id: OrderId
    from_status: OrderStatus | None
    to_status: OrderStatus
    at: ProcessingPoint
    reason: Reason | None = None
    fill_id: FillId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, EventId):
            raise KernelValueError("OrderEvent.event_id must be an EventId")
        if not isinstance(self.order_id, OrderId):
            raise KernelValueError("OrderEvent.order_id must be an OrderId")
        if self.from_status is not None and not isinstance(self.from_status, OrderStatus):
            raise KernelValueError("OrderEvent.from_status must be an OrderStatus or None")
        if not isinstance(self.to_status, OrderStatus):
            raise KernelValueError("OrderEvent.to_status must be an OrderStatus")
        if not isinstance(self.at, ProcessingPoint):
            raise KernelValueError("OrderEvent.at must be a ProcessingPoint")
        if self.reason is not None and not isinstance(self.reason, Reason):
            raise KernelValueError("OrderEvent.reason must be a Reason or None")
        if self.fill_id is not None and not isinstance(self.fill_id, FillId):
            raise KernelValueError("OrderEvent.fill_id must be a FillId or None")
        if (self.fill_id is not None) != (self.to_status is OrderStatus.FILLED):
            raise KernelValueError(
                "OrderEvent.fill_id is present exactly when the order becomes FILLED (D06 §5.1)"
            )
        # 表に無い組み合わせは構築時に拒否する。状態機械の正本は1か所だけにする。
        find_transition(self.from_status, self.to_status, self.reason, self.at.phase.name)


def find_transition(
    from_status: OrderStatus | None,
    to_status: OrderStatus,
    reason: Reason | None,
    phase_name: str,
) -> OrderTransition:
    """D06 §5.1 の表から該当する遷移を引く。無ければ `KernelValueError`。"""
    reason_code = None if reason is None else reason.code
    for transition in ORDER_TRANSITIONS:
        if transition.from_status is not from_status or transition.to_status is not to_status:
            continue
        if transition.reason_code is not reason_code:
            continue
        if phase_name not in transition.phases:
            raise KernelValueError(
                f"transition {transition.number} ({transition.trigger}) may not happen in phase"
                f" {phase_name!r}; D06 §5.1 allows {list(transition.phases)}"
            )
        return transition
    origin = "(accepted)" if from_status is None else from_status.value
    code = "None" if reason_code is None else reason_code.value
    raise KernelValueError(
        f"no order transition {origin} -> {to_status.value} with reason {code} exists (D06 §5.1)"
    )


def apply_order_event(state: OrderState | None, event: OrderEvent) -> OrderState:
    """イベントを現在状態へ適用して新しい `OrderState` を投影する（D06 §5.1）。

    終端した注文への遷移要求は拒否する（D05 §7.2 の取引機会と同じ扱い）。
    """
    if not isinstance(event, OrderEvent):
        raise KernelValueError("apply_order_event requires an OrderEvent")
    if state is None:
        if event.from_status is not None:
            raise KernelValueError(
                f"order {event.order_id} has no state yet, but the event comes from"
                f" {event.from_status.value}"
            )
    else:
        if state.order_id != event.order_id:
            raise KernelValueError(
                f"event {event.event_id} belongs to {event.order_id}, not {state.order_id}"
            )
        if state.is_terminal:
            raise KernelValueError(
                f"order {event.order_id} is already {state.status.value}; a terminal order accepts"
                " no further transitions (D06 §5.1)"
            )
        if event.from_status is not state.status:
            raise KernelValueError(
                f"order {event.order_id} is {state.status.value}, but the event comes from"
                f" {'(accepted)' if event.from_status is None else event.from_status.value}"
            )
    return OrderState(
        order_id=event.order_id,
        status=event.to_status,
        last_event_id=event.event_id,
        last_processed_at=event.at,
        terminal_reason=event.reason,
    )
