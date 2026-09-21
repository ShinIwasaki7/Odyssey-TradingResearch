"""未約定エントリーが占有する口座枠（上位設計書 §4.7.15 D、D06 §6.5・§8.1）。

予約はエントリーの受付と同じ確定単位で作り、**決済は予約を作らない**。状態は別の投影
`ReservationState` で持ち、約定は `HELD → TRANSFERRED`、取消・失効は `HELD → RELEASED`
とする。`TRANSFERRED` は予約としての終端であり、二重解放・再移管を禁止する。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AccountId,
    EventId,
    EvidenceId,
    OrderId,
    ReservationId,
    RunId,
)
from odyssey_fx.common.money import Money
from odyssey_fx.common.time import ProcessingPoint

__all__ = [
    "RESERVATION_TRANSITIONS",
    "ReservationState",
    "ReservationStatus",
    "RiskReservation",
    "move_reservation",
]


class ReservationStatus(Enum):
    """予約の状態（上位設計書 §4.7.15 D）。"""

    HELD = "HELD"
    TRANSFERRED = "TRANSFERRED"
    RELEASED = "RELEASED"


#: 許される遷移（`HELD` からの2本だけ。終端からは動かない）。
RESERVATION_TRANSITIONS: Final[tuple[tuple[ReservationStatus, ReservationStatus], ...]] = (
    (ReservationStatus.HELD, ReservationStatus.TRANSFERRED),
    (ReservationStatus.HELD, ReservationStatus.RELEASED),
)


@dataclass(frozen=True, slots=True)
class RiskReservation:
    """受付時に固定した消費枠（上位設計書 §4.7.15 D）。口座通貨。

    上位設計書は `assessment_ref: RiskAssessmentRef` と書くが、参照型の置き場所は受付層
    （`admission`）であり `domain` からは参照できない（D01 §3.3 の層順序）。
    `RiskAssessmentRef` は `assessment_id: EvidenceId` 1件だけを持つ参照なので、**同じ値**を
    ここに直接持ち、受付層がそれを包んで `RiskAssessmentRef` にする。識別子を二重に持つ
    ことにはならない。
    """

    run_id: RunId
    reservation_id: ReservationId
    order_id: OrderId
    account_id: AccountId
    created_at: ProcessingPoint
    amount: Money
    assessment_id: EvidenceId

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise KernelValueError("RiskReservation.run_id must be a RunId")
        if not isinstance(self.reservation_id, ReservationId):
            raise KernelValueError("RiskReservation.reservation_id must be a ReservationId")
        if not isinstance(self.order_id, OrderId):
            raise KernelValueError("RiskReservation.order_id must be an OrderId")
        if not isinstance(self.account_id, AccountId):
            raise KernelValueError("RiskReservation.account_id must be an AccountId")
        if not isinstance(self.created_at, ProcessingPoint):
            raise KernelValueError("RiskReservation.created_at must be a ProcessingPoint")
        if not isinstance(self.amount, Money):
            raise KernelValueError("RiskReservation.amount must be a Money")
        if not isinstance(self.assessment_id, EvidenceId):
            raise KernelValueError("RiskReservation.assessment_id must be an EvidenceId")
        if self.amount.amount < 0:
            raise KernelValueError(f"RiskReservation.amount must be >= 0, got {self.amount}")


@dataclass(frozen=True, slots=True)
class ReservationState:
    """予約の現在状態の投影（上位設計書 §4.7.15 D）。"""

    reservation_id: ReservationId
    status: ReservationStatus
    last_event_id: EventId
    last_processed_at: ProcessingPoint

    def __post_init__(self) -> None:
        if not isinstance(self.reservation_id, ReservationId):
            raise KernelValueError("ReservationState.reservation_id must be a ReservationId")
        if not isinstance(self.status, ReservationStatus):
            raise KernelValueError("ReservationState.status must be a ReservationStatus")
        if not isinstance(self.last_event_id, EventId):
            raise KernelValueError("ReservationState.last_event_id must be an EventId")
        if not isinstance(self.last_processed_at, ProcessingPoint):
            raise KernelValueError("ReservationState.last_processed_at must be a ProcessingPoint")

    @property
    def is_terminal(self) -> bool:
        """予約として終端したか。"""
        return self.status is not ReservationStatus.HELD


def move_reservation(
    state: ReservationState,
    to_status: ReservationStatus,
    event_id: EventId,
    at: ProcessingPoint,
) -> ReservationState:
    """予約の状態を進める。表に無い遷移は `KernelValueError`。"""
    if not isinstance(state, ReservationState):
        raise KernelValueError("move_reservation requires a ReservationState")
    if (state.status, to_status) not in RESERVATION_TRANSITIONS:
        raise KernelValueError(
            f"reservation {state.reservation_id} cannot move {state.status.value} ->"
            f" {to_status.value}; a transferred or released reservation is terminal"
            " (upper design §4.7.15 D)"
        )
    return ReservationState(
        reservation_id=state.reservation_id,
        status=to_status,
        last_event_id=event_id,
        last_processed_at=at,
    )
