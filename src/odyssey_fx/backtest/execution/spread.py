"""提示価格の幅（D06 §7.2）。

段階2の受入れデータは bid のみなので、ask は `SpreadModel` から導く。**spread を価格にも
費用にも重複して加算しない**ため、価格を作る経路はここ（と `domain.policies` の
`FixedSpread.ask_from_bid`）だけにする。金額としての記録は `CostKind.SPREAD_IN_PRICE` の
参考値で行い、残高からは控除しない。
"""

from __future__ import annotations

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.domain.policies import SpreadModel
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import Price

__all__ = ["ask", "bid", "quote_for_side", "uses_spread"]


def bid(observed_bid: Price) -> Price:
    """bid 系列の値をそのまま bid として読む。"""
    if not isinstance(observed_bid, Price):
        raise KernelValueError("bid requires a Price")
    return observed_bid


def ask(observed_bid: Price, model: SpreadModel) -> Price:
    """bid から ask を導く（D06 §7.2）。"""
    return model.ask_from_bid(observed_bid)


def uses_spread(side: OrderSide) -> bool:
    """その売買方向が ask を使うか（買いだけ真）。

    bid のみの系列では、**買い側の約定にだけ**提示価格の幅が費用として立つ
    （D06 §7.6・T01 §9.3）。
    """
    if not isinstance(side, OrderSide):
        raise KernelValueError("uses_spread requires an OrderSide")
    return side is OrderSide.BUY


def quote_for_side(observed_bid: Price, side: OrderSide, model: SpreadModel) -> Price:
    """売買方向に対応する提示価格（買いは ask、売りは bid。上位設計書 §4.7.11）。"""
    return ask(observed_bid, model) if uses_spread(side) else bid(observed_bid)
