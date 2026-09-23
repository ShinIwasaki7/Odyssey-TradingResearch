"""約定の事実と費用（上位設計書 §4.7.15 C、D06 §7.3・§7.6）。

約定時刻の精度は2通りある。始値約定は時刻そのものを観測できるので `ExactExecutionTime`、
足の中で保護水準に触れた決済は正確な時刻を観測できないので `BarExecutionInterval` に足の
区間を記録する。**終値時刻を到達時刻として捏造しない**（上位設計書 §4.7.15 C）。

費用は3区分に分ける（D06 §7.6）。手数料だけが残高に反映され、価格に反映済みの滑りと
提示価格の幅は**参考値**として記録だけする（二重計上しない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EventId, FillId, OrderId, PositionId, RunId
from odyssey_fx.common.money import ConversionRate, Money, Price, Quantity
from odyssey_fx.common.refs import EvidenceRef
from odyssey_fx.common.time import Interval, ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey

__all__ = [
    "BarExecutionInterval",
    "CostEntry",
    "CostKind",
    "ExactExecutionTime",
    "ExecutionTime",
    "FillRecord",
]


class CostKind(Enum):
    """費用の区分（D06 §7.6・§9.2）。

    後2者は**価格に反映済み**の参考値であり、金額として残高から控除しない。分けて持つのは、
    「執行モデルを変えたときに動く費用」と「データの提示価格に由来する費用」を評価側が
    別々に集計できるようにするためである。
    """

    COMMISSION = "COMMISSION"
    SLIPPAGE_IN_PRICE = "SLIPPAGE_IN_PRICE"
    SPREAD_IN_PRICE = "SPREAD_IN_PRICE"

    @property
    def reflected_in_balance(self) -> bool:
        """残高に反映するか（手数料だけが真）。"""
        return self is CostKind.COMMISSION


@dataclass(frozen=True, slots=True)
class ExactExecutionTime:
    """約定時刻を特定できた場合（始値約定、D06 §7.1）。"""

    time: UtcTime
    kind: str = "EXACT"

    def __post_init__(self) -> None:
        if self.kind != "EXACT":
            raise KernelValueError(f"ExactExecutionTime.kind must be 'EXACT', got {self.kind!r}")
        if not isinstance(self.time, UtcTime):
            raise KernelValueError("ExactExecutionTime.time must be a UtcTime")


@dataclass(frozen=True, slots=True)
class BarExecutionInterval:
    """足の中で到達したが時刻までは特定できない場合（D06 §7.3）。"""

    bar_key: BarKey
    interval: Interval
    kind: str = "BAR_INTERVAL"

    def __post_init__(self) -> None:
        if self.kind != "BAR_INTERVAL":
            raise KernelValueError(
                f"BarExecutionInterval.kind must be 'BAR_INTERVAL', got {self.kind!r}"
            )
        if not isinstance(self.bar_key, BarKey):
            raise KernelValueError("BarExecutionInterval.bar_key must be a BarKey")
        if not isinstance(self.interval, Interval):
            raise KernelValueError("BarExecutionInterval.interval must be an Interval")
        if self.bar_key.bar_start != self.interval.start:
            raise KernelValueError(
                "BarExecutionInterval.interval must start at the bar's start"
                f" ({self.interval.start} != {self.bar_key.bar_start})"
            )


#: 区分タグ付き union（D06 §7.3）。
ExecutionTime = ExactExecutionTime | BarExecutionInterval


@dataclass(frozen=True, slots=True)
class CostEntry:
    """費用1件（上位設計書 §4.7.15 C）。原通貨額・口座通貨計上額・換算根拠を持つ。"""

    kind: CostKind
    native: Money
    account: Money
    conversion: ConversionRate

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CostKind):
            raise KernelValueError("CostEntry.kind must be a CostKind")
        if not isinstance(self.native, Money):
            raise KernelValueError("CostEntry.native must be a Money")
        if not isinstance(self.account, Money):
            raise KernelValueError("CostEntry.account must be a Money")
        if not isinstance(self.conversion, ConversionRate):
            raise KernelValueError("CostEntry.conversion must be a ConversionRate")
        if self.native.currency != self.conversion.from_currency:
            raise KernelValueError(
                f"CostEntry.native is {self.native.currency} but the rate converts from"
                f" {self.conversion.from_currency}"
            )
        if self.account.currency != self.conversion.to_currency:
            raise KernelValueError(
                f"CostEntry.account is {self.account.currency} but the rate converts to"
                f" {self.conversion.to_currency}"
            )


@dataclass(frozen=True, slots=True)
class FillRecord:
    """約定の事実（上位設計書 §4.7.15 C）。不変。

    銘柄・売買方向・ポリシーは `AcceptedOrder` から辿り、ここに重複させない。
    """

    run_id: RunId
    fill_id: FillId
    event_id: EventId
    order_id: OrderId
    position_id: PositionId
    processed_at: ProcessingPoint
    execution_time: ExecutionTime
    price: Price
    quantity: Quantity
    costs: tuple[CostEntry, ...]
    evidence_ref: EvidenceRef

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise KernelValueError("FillRecord.run_id must be a RunId")
        if not isinstance(self.fill_id, FillId):
            raise KernelValueError("FillRecord.fill_id must be a FillId")
        if not isinstance(self.event_id, EventId):
            raise KernelValueError("FillRecord.event_id must be an EventId")
        if not isinstance(self.order_id, OrderId):
            raise KernelValueError("FillRecord.order_id must be an OrderId")
        if not isinstance(self.position_id, PositionId):
            raise KernelValueError("FillRecord.position_id must be a PositionId")
        if not isinstance(self.processed_at, ProcessingPoint):
            raise KernelValueError("FillRecord.processed_at must be a ProcessingPoint")
        if not isinstance(self.execution_time, (ExactExecutionTime, BarExecutionInterval)):
            raise KernelValueError("FillRecord.execution_time must be an ExecutionTime")
        if not isinstance(self.price, Price):
            raise KernelValueError("FillRecord.price must be a Price")
        if not isinstance(self.quantity, Quantity):
            raise KernelValueError("FillRecord.quantity must be a Quantity")
        if not isinstance(self.costs, tuple) or not all(
            isinstance(entry, CostEntry) for entry in self.costs
        ):
            raise KernelValueError("FillRecord.costs must be a tuple of CostEntry")
        seen = [entry.kind for entry in self.costs]
        if len(set(seen)) != len(seen):
            raise KernelValueError(
                "FillRecord.costs must hold at most one entry per cost kind; the per-kind columns"
                " of the fills table would otherwise be ambiguous (D06 §9.2)"
            )
        if not isinstance(self.evidence_ref, EvidenceRef):
            raise KernelValueError("FillRecord.evidence_ref must be an EvidenceRef")

    def cost_of(self, kind: CostKind) -> CostEntry | None:
        """区分に対応する費用（無ければ `None`、D06 §9.2）。"""
        for entry in self.costs:
            if entry.kind is kind:
                return entry
        return None
