"""費用の計算（D06 §7.6）。

費用は3区分に分ける。**手数料だけが残高に反映**され、価格に反映済みの滑り
（`SLIPPAGE_IN_PRICE`）と提示価格の幅（`SPREAD_IN_PRICE`）は参考値として記録だけする。
両者を1区分に畳まないのは、評価側が「執行モデルを変えたときに動く費用」と「データの提示
価格に由来する費用」を分けて集計できるようにするためである（T01 §9.3）。

費用予算 `C(Q)` は数量に比例する（往復手数料＋損切り決済の滑り）。`RiskPolicy` に固定額で
置けないことは T01 経路1 の手順6 で判明した。
"""

from __future__ import annotations

from decimal import localcontext

from odyssey_fx.backtest.domain.fills import CostEntry, CostKind
from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.domain.policies import CostModel
from odyssey_fx.backtest.execution.spread import uses_spread
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import (
    ConversionRate,
    CurrencyCode,
    Money,
    Quantity,
    convert,
    kernel_context,
)

__all__ = ["FillPurpose", "cost_entries"]


class FillPurpose:
    """約定の目的（エントリーか決済か）。滑りの設定を使い分けるために持つ。"""

    ENTRY = "ENTRY"
    CLOSE = "CLOSE"


def cost_entries(
    model: CostModel,
    *,
    purpose: str,
    side: OrderSide,
    quantity: Quantity,
    currency: CurrencyCode,
    conversion: ConversionRate,
    spread_applied: bool = True,
    commission_conversion: ConversionRate | None = None,
) -> tuple[CostEntry, ...]:
    """1件の約定に紐付く費用の記録（D06 §7.6・§9.2）。

    `spread_applied=False` は、提示価格の幅を通さずに価格が決まった約定
    （保護水準そのものを基準にした決済など）を表す。買い側でも幅の費用は立たない。

    `currency` は口座通貨、`conversion` は決済通貨から口座通貨への率 `X_f`（D06 v1.20 案
    §19.6 の (2)）。滑りとスプレッドは価格の単位なので原通貨額は決済通貨（`conversion` の
    `from_currency`）で、計上額は `X_f` で口座通貨にする。手数料は口座通貨建てのまま換算せず、
    `commission_conversion`（口座通貨 → 口座通貨の恒等の率。仮置き C1-9）を記録する。`None` なら
    `conversion` を使う（決済通貨が口座通貨と同じ run。今までと同じ記録）。
    """
    if purpose not in (FillPurpose.ENTRY, FillPurpose.CLOSE):
        raise KernelValueError(f"unknown fill purpose: {purpose!r}")
    slippage_offset = model.entry_slippage if purpose == FillPurpose.ENTRY else model.close_slippage
    settlement = conversion.from_currency
    commission_rate = conversion if commission_conversion is None else commission_conversion
    with localcontext(kernel_context()):
        commission = Money(model.commission_per_unit.amount * quantity.units, currency)
        slippage = Money(slippage_offset.value * quantity.units, settlement)
        spread = Money(model.spread_model.offset.value * quantity.units, settlement)
    entries = [
        CostEntry(
            kind=CostKind.COMMISSION,
            native=commission,
            account=convert(commission, commission_rate),
            conversion=commission_rate,
        ),
        CostEntry(
            kind=CostKind.SLIPPAGE_IN_PRICE,
            native=slippage,
            account=convert(slippage, conversion),
            conversion=conversion,
        ),
    ]
    if spread_applied and uses_spread(side):
        entries.append(
            CostEntry(
                kind=CostKind.SPREAD_IN_PRICE,
                native=spread,
                account=convert(spread, conversion),
                conversion=conversion,
            )
        )
    return tuple(entries)
