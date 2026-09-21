"""成行の約定価格と約定ずれ（D06 §7.1）。

方式は確定済みで、受付後、**因果順序上まだ到来していない最初の執行足の始値**で約定する。
約定価格の基準は買いが ask、売りが bid で、滑りは注文の目的（エントリー／決済）で使い分け、
**常に不利な方向**へ適用する。

| 目的 | 基準価格 | 適用する滑り | 不利な方向 |
|---|---|---|---|
| 新規エントリー（買い） | 対象 open の ask | `entry_slippage` | 価格を上げる |
| 新規エントリー（売り） | 対象 open の bid | `entry_slippage` | 価格を下げる |
| 決済（買い建玉を閉じる＝売り） | bid | `close_slippage` | 価格を下げる |
| 決済（売り建玉を閉じる＝買い） | ask | `close_slippage` | 価格を上げる |
"""

from __future__ import annotations

from decimal import localcontext

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.domain.policies import CostModel
from odyssey_fx.backtest.execution.cost_model import FillPurpose
from odyssey_fx.backtest.execution.spread import quote_for_side
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import (
    Price,
    PriceOffset,
    decimal_from_int,
    kernel_context,
)

__all__ = ["adverse_fill_excess", "apply_slippage", "fill_price", "is_adverse_fill_excessive"]


def apply_slippage(base: Price, side: OrderSide, slippage: PriceOffset) -> Price:
    """売買方向の不利な向きへ滑りを適用する（買いは上げ、売りは下げ）。"""
    if not isinstance(base, Price):
        raise KernelValueError("apply_slippage requires a Price")
    if not isinstance(slippage, PriceOffset):
        raise KernelValueError("apply_slippage requires a PriceOffset")
    return base + slippage if side is OrderSide.BUY else base - slippage


def fill_price(
    observed_bid: Price, side: OrderSide, purpose: str, model: CostModel, *, use_spread: bool = True
) -> Price:
    """約定価格（D06 §7.1）。

    `use_spread=False` は、提示価格の幅を通さずに基準価格が決まる約定（保護水準そのものを
    基準にした決済）を表す。滑りだけを不利な向きへ適用する。
    """
    if purpose not in (FillPurpose.ENTRY, FillPurpose.CLOSE):
        raise KernelValueError(f"unknown fill purpose: {purpose!r}")
    base = quote_for_side(observed_bid, side, model.spread_model) if use_spread else observed_bid
    slippage = model.entry_slippage if purpose == FillPurpose.ENTRY else model.close_slippage
    return apply_slippage(base, side, slippage)


def adverse_fill_excess(fill: Price, reference: Price, side: OrderSide) -> PriceOffset:
    """約定ずれ `max(0, d × (P_fill − P_ref))`（上位設計書 §4.7.9 C）。"""
    direction = decimal_from_int(1 if side is OrderSide.BUY else -1)
    with localcontext(kernel_context()):
        raw = (fill.value - reference.value) * direction
        zero = raw - raw
        return PriceOffset(raw if raw > zero else zero)


def is_adverse_fill_excessive(
    fill: Price, reference: Price, side: OrderSide, limit: PriceOffset
) -> bool:
    """約定ずれが許容不利約定幅 Δ を超えたか。**上限一致は許容する**（同節）。"""
    if not isinstance(limit, PriceOffset):
        raise KernelValueError("is_adverse_fill_excessive requires a PriceOffset limit")
    return adverse_fill_excess(fill, reference, side) > limit
