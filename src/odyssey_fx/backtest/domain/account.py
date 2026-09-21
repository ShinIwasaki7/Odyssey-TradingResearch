"""口座仕様と台帳（D06 §4.4・§8.1）。

台帳の現在状態は `AccountLedger` **1つの不変値**で表す。エンジンはその参照を1つだけ持ち、
確定単位（審査 → 組み立て → 参照の差し替え）の最後に新しい値へ差し替える。差し替えは1文
なので、途中まで更新した状態が外から観測されることはない（D06 §4.4）。

`balance` は実現損益・費用を反映した残高で、未実現損益を含めない。予算の分母はこの
`balance` である（上位設計書 §4.7.10）。含み損益込みの `equity` は台帳の項目ではなく、
評価価格を持つ `portfolio.mtm` が判断時点ごとに計算する。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from odyssey_fx.backtest.domain.orders import AcceptedOrder, OrderState, OrderStatus
from odyssey_fx.backtest.domain.positions import Position, PositionRiskAllocation
from odyssey_fx.backtest.domain.reservations import (
    ReservationState,
    ReservationStatus,
    RiskReservation,
)
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AccountId,
    AllocationId,
    EventId,
    OrderId,
    PositionId,
    ReservationId,
)
from odyssey_fx.common.money import CurrencyCode, Money

__all__ = ["AccountLedger", "AccountSpec"]


@dataclass(frozen=True, slots=True)
class AccountSpec:
    """実験設定が与える口座（D06 §8.1）。run manifest の入力群に入る。"""

    account_id: AccountId
    currency: CurrencyCode
    initial_balance: Money

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, AccountId):
            raise KernelValueError("AccountSpec.account_id must be an AccountId")
        if not isinstance(self.currency, CurrencyCode):
            raise KernelValueError("AccountSpec.currency must be a CurrencyCode")
        if not isinstance(self.initial_balance, Money):
            raise KernelValueError("AccountSpec.initial_balance must be a Money")
        if self.initial_balance.currency != self.currency:
            raise KernelValueError(
                f"AccountSpec.initial_balance must be in {self.currency},"
                f" got {self.initial_balance.currency}"
            )
        if self.initial_balance.amount <= 0:
            raise KernelValueError(
                f"AccountSpec.initial_balance must be > 0, got {self.initial_balance}"
            )


def _freeze[K, V](items: Mapping[K, V]) -> Mapping[K, V]:
    """写像を読み取り専用にして凍結する（書き換えを構造的に防ぐ）。"""
    return MappingProxyType(dict(items))


@dataclass(frozen=True, slots=True)
class AccountLedger:
    """1つの確定単位ごとに差し替える台帳の不変値（D06 §4.4・§8.1）。"""

    account_id: AccountId
    balance: Money
    orders: Mapping[OrderId, AcceptedOrder] = field(default_factory=dict)
    order_states: Mapping[OrderId, OrderState] = field(default_factory=dict)
    positions: Mapping[PositionId, Position] = field(default_factory=dict)
    allocations: Mapping[AllocationId, PositionRiskAllocation] = field(default_factory=dict)
    reservations: Mapping[ReservationId, RiskReservation] = field(default_factory=dict)
    reservation_states: Mapping[ReservationId, ReservationState] = field(default_factory=dict)
    processed_event_ids: frozenset[EventId] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, AccountId):
            raise KernelValueError("AccountLedger.account_id must be an AccountId")
        if not isinstance(self.balance, Money):
            raise KernelValueError("AccountLedger.balance must be a Money")
        if not isinstance(self.processed_event_ids, frozenset):
            raise KernelValueError("AccountLedger.processed_event_ids must be a frozenset")
        for name in (
            "orders",
            "order_states",
            "positions",
            "allocations",
            "reservations",
            "reservation_states",
        ):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise KernelValueError(f"AccountLedger.{name} must be a Mapping")
            object.__setattr__(self, name, _freeze(value))

    # --- 参照 ---------------------------------------------------------------

    @property
    def currency(self) -> CurrencyCode:
        """口座通貨。"""
        return self.balance.currency

    def zero(self) -> Money:
        """口座通貨の 0 円。"""
        return Money(self.balance.amount - self.balance.amount, self.balance.currency)

    def open_positions(self) -> tuple[Position, ...]:
        """開いている建玉（`position_id` の連番の昇順）。"""
        return tuple(
            position
            for _, position in sorted(self.positions.items(), key=lambda item: item[0].seq)
            if position.is_open
        )

    def pending_orders(self) -> tuple[AcceptedOrder, ...]:
        """まだ `PENDING` の注文（`order_id` の連番の昇順）。"""
        pending: list[AcceptedOrder] = []
        for order_id, order in sorted(self.orders.items(), key=lambda item: item[0].seq):
            state = self.order_states.get(order_id)
            if state is not None and state.status is OrderStatus.PENDING:
                pending.append(order)
        return tuple(pending)

    def pending_entry_orders(self) -> tuple[AcceptedOrder, ...]:
        """まだ `PENDING` のエントリー注文（建玉枠の検査に使う、D06 §8.2）。"""
        return tuple(order for order in self.pending_orders() if order.is_entry)

    def consumed(self) -> Money:
        """消費済み枠 `U` = HELD 予約額 ＋ 未解放の建玉割当額（上位設計書 §4.7.15 D）。

        `TRANSFERRED` の予約は加算しない（同額の建玉割当が既に数えられているため）。
        """
        total = self.zero()
        for reservation_id, reservation in self.reservations.items():
            state = self.reservation_states.get(reservation_id)
            if state is not None and state.status is ReservationStatus.HELD:
                total = total + reservation.amount
        for allocation in self.allocations.values():
            if not allocation.is_released:
                total = total + allocation.amount
        return total

    def has_processed(self, event_id: EventId) -> bool:
        """同じイベントを既に確定したか（冪等性、D06 §4.4）。"""
        return event_id in self.processed_event_ids

    # --- 差し替え -----------------------------------------------------------

    def committed(
        self,
        *,
        balance: Money | None = None,
        orders: Mapping[OrderId, AcceptedOrder] | None = None,
        order_states: Mapping[OrderId, OrderState] | None = None,
        positions: Mapping[PositionId, Position] | None = None,
        allocations: Mapping[AllocationId, PositionRiskAllocation] | None = None,
        reservations: Mapping[ReservationId, RiskReservation] | None = None,
        reservation_states: Mapping[ReservationId, ReservationState] | None = None,
        event_ids: tuple[EventId, ...] = (),
    ) -> AccountLedger:
        """1つの確定単位ぶんの変更をまとめて適用した新しい台帳を返す（D06 §4.4）。

        写像の引数は**追加・置換する項目だけ**を渡す。`event_ids` に渡したイベントは
        `processed_event_ids` へ同じ差し替えで加える（冪等性の実装）。
        """
        for event_id in event_ids:
            if event_id in self.processed_event_ids:
                raise KernelValueError(
                    f"event {event_id} has already been committed; the caller must check"
                    " has_processed() before building a commit (D06 §4.4)"
                )
        return replace(
            self,
            balance=self.balance if balance is None else balance,
            orders=self.orders if orders is None else {**self.orders, **orders},
            order_states=(
                self.order_states if order_states is None else {**self.order_states, **order_states}
            ),
            positions=self.positions if positions is None else {**self.positions, **positions},
            allocations=(
                self.allocations if allocations is None else {**self.allocations, **allocations}
            ),
            reservations=(
                self.reservations if reservations is None else {**self.reservations, **reservations}
            ),
            reservation_states=(
                self.reservation_states
                if reservation_states is None
                else {**self.reservation_states, **reservation_states}
            ),
            processed_event_ids=self.processed_event_ids | frozenset(event_ids),
        )

    @classmethod
    def opened(cls, spec: AccountSpec) -> AccountLedger:
        """初期残高だけを持つ台帳を作る。"""
        return cls(account_id=spec.account_id, balance=spec.initial_balance)
