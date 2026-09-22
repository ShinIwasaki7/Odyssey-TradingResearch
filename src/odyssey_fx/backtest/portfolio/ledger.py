"""台帳の原子的更新と投影（D06 §4.4・§8.1）。

D06 §4.4 は確定単位を5種類に固定している。本モジュールはその5種類を**関数1つずつ**として
持ち、どの単位に何が入るかを1か所だけで表す。関数はいずれも新しい `AccountLedger` を返し、
呼び出し側（`engine`）は返ってきた値で参照を差し替える。差し替えは1文なので、途中まで
更新した状態は観測されない。

| 確定単位 | 関数 |
|---|---|
| エントリーの受付 | `commit_entry_acceptance` |
| 決済の受付 | `commit_close_acceptance` |
| エントリー約定 | `commit_entry_fill` |
| 決済約定 | `commit_close_fill` |
| エンジン生成の即時決済 | `commit_immediate_close`（受付と約定を1つにまとめる） |
| 終端（期限・取消） | `commit_order_termination` |
"""

from __future__ import annotations

from dataclasses import dataclass

from odyssey_fx.backtest.domain.account import AccountLedger
from odyssey_fx.backtest.domain.events import OrderEvent, apply_order_event
from odyssey_fx.backtest.domain.orders import AcceptedEntryTerms, AcceptedOrder
from odyssey_fx.backtest.domain.positions import Position, PositionRiskAllocation
from odyssey_fx.backtest.domain.reservations import (
    ReservationState,
    ReservationStatus,
    RiskReservation,
    move_reservation,
)
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import PositionId
from odyssey_fx.common.money import Money
from odyssey_fx.common.time import ProcessingPoint

__all__ = [
    "LedgerSnapshot",
    "commit_close_acceptance",
    "commit_close_fill",
    "commit_entry_acceptance",
    "commit_entry_fill",
    "commit_immediate_close",
    "commit_order_termination",
    "snapshot_of",
]


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """判断時点の台帳の写し（D06 §8.1・§9.2 の表14）。

    各判断時点の `LEDGER_UPDATE` と `EXECUTION_OPEN` の後に1件ずつ残す。約定の前後で残高と
    枠がどう動いたかを追えるようにするためで、全フェーズで残すと記録量に見合わない。
    """

    at: ProcessingPoint
    balance: Money
    equity: Money
    consumed: Money
    open_position_ids: tuple[PositionId, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.at, ProcessingPoint):
            raise KernelValueError("LedgerSnapshot.at must be a ProcessingPoint")
        for value, label in (
            (self.balance, "balance"),
            (self.equity, "equity"),
            (self.consumed, "consumed"),
        ):
            if not isinstance(value, Money):
                raise KernelValueError(f"LedgerSnapshot.{label} must be a Money")
        if self.balance.currency != self.equity.currency:
            raise KernelValueError("LedgerSnapshot.balance and .equity must share a currency")
        if not isinstance(self.open_position_ids, tuple):
            raise KernelValueError("LedgerSnapshot.open_position_ids must be a tuple")


def snapshot_of(ledger: AccountLedger, at: ProcessingPoint, equity: Money) -> LedgerSnapshot:
    """台帳と評価済み equity から snapshot を作る。"""
    return LedgerSnapshot(
        at=at,
        balance=ledger.balance,
        equity=equity,
        consumed=ledger.consumed(),
        open_position_ids=tuple(position.position_id for position in ledger.open_positions()),
    )


def _state_after(ledger: AccountLedger, event: OrderEvent) -> AccountLedger:
    """注文イベントを投影した状態を持つ台帳（差し替えはしない補助）。"""
    current = ledger.order_states.get(event.order_id)
    projected = apply_order_event(current, event)
    return ledger.committed(order_states={event.order_id: projected}, event_ids=(event.event_id,))


def commit_entry_acceptance(
    ledger: AccountLedger,
    *,
    order: AcceptedOrder,
    event: OrderEvent,
    reservation: RiskReservation,
) -> AccountLedger:
    """エントリーの受付（D06 §4.4）。注文・状態・予約・予約状態を1つの単位で確定する。"""
    if not order.is_entry:
        raise KernelValueError("commit_entry_acceptance requires an entry order")
    projected = apply_order_event(ledger.order_states.get(order.order_id), event)
    return ledger.committed(
        orders={order.order_id: order},
        order_states={order.order_id: projected},
        reservations={reservation.reservation_id: reservation},
        reservation_states={
            reservation.reservation_id: ReservationState(
                reservation_id=reservation.reservation_id,
                status=ReservationStatus.HELD,
                last_event_id=event.event_id,
                last_processed_at=event.at,
            )
        },
        event_ids=(event.event_id,),
    )


def commit_close_acceptance(
    ledger: AccountLedger, *, order: AcceptedOrder, event: OrderEvent
) -> AccountLedger:
    """決済の受付（D06 §4.4）。**予約は作らない**。既存の建玉割当は決済まで保持する。"""
    if order.is_entry:
        raise KernelValueError("commit_close_acceptance requires a close order")
    return _state_after(
        ledger.committed(orders={order.order_id: order}),
        event,
    )


def commit_entry_fill(
    ledger: AccountLedger,
    *,
    order: AcceptedOrder,
    event: OrderEvent,
    position: Position,
    allocation: PositionRiskAllocation,
    balance: Money,
) -> AccountLedger:
    """エントリー約定（D06 §4.4）。予約を移管し、同額の建玉割当を作る。"""
    reservation_state = ledger.reservation_states.get(allocation.source_reservation_id)
    if reservation_state is None:
        raise KernelValueError(
            f"reservation {allocation.source_reservation_id} is unknown to the ledger"
        )
    moved = move_reservation(
        reservation_state, ReservationStatus.TRANSFERRED, event.event_id, event.at
    )
    projected = apply_order_event(ledger.order_states.get(order.order_id), event)
    return ledger.committed(
        balance=balance,
        order_states={order.order_id: projected},
        positions={position.position_id: position},
        allocations={allocation.allocation_id: allocation},
        reservation_states={moved.reservation_id: moved},
        event_ids=(event.event_id,),
    )


def commit_close_fill(
    ledger: AccountLedger,
    *,
    order: AcceptedOrder,
    event: OrderEvent,
    position: Position,
    allocation: PositionRiskAllocation,
    balance: Money,
) -> AccountLedger:
    """決済約定（D06 §4.4）。実現損益・費用・balance と建玉割当の解放を1つの単位にする。"""
    projected = apply_order_event(ledger.order_states.get(order.order_id), event)
    return ledger.committed(
        balance=balance,
        order_states={order.order_id: projected},
        positions={position.position_id: position},
        allocations={allocation.allocation_id: allocation},
        event_ids=(event.event_id,),
    )


def commit_immediate_close(
    ledger: AccountLedger,
    *,
    order: AcceptedOrder,
    acceptance_event: OrderEvent,
    fill_event: OrderEvent,
    position: Position,
    allocation: PositionRiskAllocation,
    balance: Money,
) -> AccountLedger:
    """エンジンが生成する即時決済（D06 §4.4・§7.3・§7.5）。

    受付と約定を**1つの差し替え**にまとめる。順に適用すると、その間で失敗したときに
    「即時に約定するはずのエンジン注文が `PENDING` のまま残る」状態が保存されてしまう。
    """
    accepted = apply_order_event(ledger.order_states.get(order.order_id), acceptance_event)
    filled = apply_order_event(accepted, fill_event)
    return ledger.committed(
        balance=balance,
        orders={order.order_id: order},
        order_states={order.order_id: filled},
        positions={position.position_id: position},
        allocations={allocation.allocation_id: allocation},
        event_ids=(acceptance_event.event_id, fill_event.event_id),
    )


def commit_order_termination(
    ledger: AccountLedger, *, event: OrderEvent, release_reservation: bool = True
) -> AccountLedger:
    """終端（期限・取消）（D06 §4.4）。未約定予約があれば同じ単位で解放する。"""
    order = ledger.orders.get(event.order_id)
    if order is None:
        raise KernelValueError(f"order {event.order_id} is unknown to the ledger")
    updated = _state_after(ledger, event)
    terms = order.terms
    if not release_reservation or not isinstance(terms, AcceptedEntryTerms):
        return updated
    state = updated.reservation_states.get(terms.reservation_id)
    if state is None or state.is_terminal:
        return updated
    released = move_reservation(state, ReservationStatus.RELEASED, event.event_id, event.at)
    return updated.committed(reservation_states={released.reservation_id: released})
