"""建玉・保護水準・リスク割当（D06 §8.1〜§8.3、上位設計書 §4.7.15 D）。

保護水準は建玉が持ち、管理権は**単一の Exit 部品**にある（上位設計書 §4.7.6）。更新のたびに
`ProtectionState.version` を1増やし、`effective_from` に「どの執行足から有効か」を持つ。
新規建玉の初期の損切り・初期の利確は**約定した足から**有効で、それ以降の更新は次の執行足
から有効になる（D06 §8.3）。

受付時に確定したリスク枠は決済まで保持し、約定価格で再計算しない（上位設計書 §4.7.4）。
約定時の実リスクとの差は `RiskMeasurement` として別に記録する。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AccountId,
    AllocationId,
    EventId,
    FillId,
    PositionId,
    ReservationId,
)
from odyssey_fx.common.money import Money, Price, Quantity
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import ProcessingPoint
from odyssey_fx.marketdata.domain.bar import BarKey

__all__ = [
    "Position",
    "PositionRiskAllocation",
    "PositionStatus",
    "ProtectionState",
    "RiskMeasurement",
]


class PositionStatus(Enum):
    """建玉の状態（D06 §8.2）。"""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class ProtectionState:
    """建玉に有効な保護水準（D06 §8.3）。

    `owner_instance_id` は保護水準を管理する Exit の使用箇所。`None` は Exit を持たない戦略。
    """

    version: int
    stop_loss: Price
    effective_from: BarKey
    take_profit: Price | None = None
    owner_instance_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise KernelValueError("ProtectionState.version must be an int")
        if self.version < 1:
            raise KernelValueError(f"ProtectionState.version must be >= 1, got {self.version}")
        if not isinstance(self.stop_loss, Price):
            raise KernelValueError("ProtectionState.stop_loss must be a Price")
        if not isinstance(self.effective_from, BarKey):
            raise KernelValueError("ProtectionState.effective_from must be a BarKey")
        if self.take_profit is not None and not isinstance(self.take_profit, Price):
            raise KernelValueError("ProtectionState.take_profit must be a Price or None")
        if self.owner_instance_id is not None and not self.owner_instance_id:
            raise KernelValueError("ProtectionState.owner_instance_id must not be empty")


@dataclass(frozen=True, slots=True)
class Position:
    """建玉（D06 §8.2）。段階2は同時1建玉・全数量決済。"""

    position_id: PositionId
    account_id: AccountId
    strategy_id: str
    symbol: Symbol
    side: OrderSide
    quantity: Quantity
    entry_fill_id: FillId
    entry_price: Price
    opened_at: ProcessingPoint
    protection: ProtectionState
    status: PositionStatus = PositionStatus.OPEN
    close_fill_id: FillId | None = None
    realized: Money | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.position_id, PositionId):
            raise KernelValueError("Position.position_id must be a PositionId")
        if not isinstance(self.account_id, AccountId):
            raise KernelValueError("Position.account_id must be an AccountId")
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise KernelValueError("Position.strategy_id must be a non-empty str")
        if not isinstance(self.symbol, Symbol):
            raise KernelValueError("Position.symbol must be a Symbol")
        if not isinstance(self.side, OrderSide):
            raise KernelValueError("Position.side must be an OrderSide")
        if not isinstance(self.quantity, Quantity):
            raise KernelValueError("Position.quantity must be a Quantity")
        if not isinstance(self.entry_fill_id, FillId):
            raise KernelValueError("Position.entry_fill_id must be a FillId")
        if not isinstance(self.entry_price, Price):
            raise KernelValueError("Position.entry_price must be a Price")
        if not isinstance(self.opened_at, ProcessingPoint):
            raise KernelValueError("Position.opened_at must be a ProcessingPoint")
        if not isinstance(self.protection, ProtectionState):
            raise KernelValueError("Position.protection must be a ProtectionState")
        if not isinstance(self.status, PositionStatus):
            raise KernelValueError("Position.status must be a PositionStatus")
        closed = self.status is PositionStatus.CLOSED
        if closed != (self.close_fill_id is not None):
            raise KernelValueError(
                "Position.close_fill_id is present exactly when the position is CLOSED"
            )
        if closed != (self.realized is not None):
            raise KernelValueError(
                "Position.realized is present exactly when the position is CLOSED"
            )

    @property
    def is_open(self) -> bool:
        """まだ開いているか。"""
        return self.status is PositionStatus.OPEN


@dataclass(frozen=True, slots=True)
class PositionRiskAllocation:
    """建玉へ移管したリスク枠（上位設計書 §4.7.15 D）。決済で解放する。"""

    allocation_id: AllocationId
    position_id: PositionId
    source_reservation_id: ReservationId
    amount: Money
    created_event_id: EventId
    released_event_id: EventId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allocation_id, AllocationId):
            raise KernelValueError("PositionRiskAllocation.allocation_id must be an AllocationId")
        if not isinstance(self.position_id, PositionId):
            raise KernelValueError("PositionRiskAllocation.position_id must be a PositionId")
        if not isinstance(self.source_reservation_id, ReservationId):
            raise KernelValueError(
                "PositionRiskAllocation.source_reservation_id must be a ReservationId"
            )
        if not isinstance(self.amount, Money):
            raise KernelValueError("PositionRiskAllocation.amount must be a Money")
        if not isinstance(self.created_event_id, EventId):
            raise KernelValueError("PositionRiskAllocation.created_event_id must be an EventId")
        if self.released_event_id is not None and not isinstance(self.released_event_id, EventId):
            raise KernelValueError(
                "PositionRiskAllocation.released_event_id must be an EventId or None"
            )

    @property
    def is_released(self) -> bool:
        """解放済みか（消費済み枠 `U` に加算しないか）。"""
        return self.released_event_id is not None


@dataclass(frozen=True, slots=True)
class RiskMeasurement:
    """約定時の実リスクの計測（上位設計書 §4.7.15 D）。固定割当を上書きしない。"""

    position_id: PositionId
    at: ProcessingPoint
    measured: Money
    allocated: Money
    basis: str

    def __post_init__(self) -> None:
        if not isinstance(self.position_id, PositionId):
            raise KernelValueError("RiskMeasurement.position_id must be a PositionId")
        if not isinstance(self.at, ProcessingPoint):
            raise KernelValueError("RiskMeasurement.at must be a ProcessingPoint")
        if not isinstance(self.measured, Money):
            raise KernelValueError("RiskMeasurement.measured must be a Money")
        if not isinstance(self.allocated, Money):
            raise KernelValueError("RiskMeasurement.allocated must be a Money")
        if not isinstance(self.basis, str) or not self.basis:
            raise KernelValueError("RiskMeasurement.basis must be a non-empty str")
