"""含み損益の評価（D06 §8.1、Q13 決定・§10.3）。

run 中の含み損益は、その判断時点の**直前に完了した執行足の終値**で評価する。買い建玉は
決済が売りなので終値（bid）をそのまま使い、売り建玉は決済が買いなので spread モデルで
導いた ask を使う。受付時の参照価格（D06 §6.4 の手順3）と同じ出どころにすることで、同じ
判断時点に「約定ずれを測る基準の価格」と「含み損益を評価する価格」の2つが並ばない。

この規則が無いと最大ドローダウン（含み損益込み）が判断履歴から一意に決まらない
（D07 §5.4）。評価価格が取れない判断時点は実行失敗として扱い、架空の価格で埋めない。
"""

from __future__ import annotations

from decimal import localcontext

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.domain.policies import SpreadModel
from odyssey_fx.backtest.domain.positions import Position
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import (
    CurrencyCode,
    Money,
    Price,
    decimal_from_int,
    kernel_context,
)

__all__ = ["exit_price_for", "unrealized"]


def exit_price_for(side: OrderSide, bid_close: Price, spread_model: SpreadModel) -> Price:
    """建玉を決済する側の価格（買い建玉は bid、売り建玉は ask。D06 §8.1・§10.3）。"""
    if not isinstance(side, OrderSide):
        raise KernelValueError("exit_price_for requires an OrderSide")
    if not isinstance(bid_close, Price):
        raise KernelValueError("exit_price_for requires a Price")
    if side is OrderSide.BUY:
        return bid_close
    return spread_model.ask_from_bid(bid_close)


def unrealized(
    position: Position,
    bid_close: Price,
    spread_model: SpreadModel,
    currency: CurrencyCode,
) -> Money:
    """1建玉の含み損益（口座通貨。段階2は決済通貨＝口座通貨の恒等換算）。"""
    if not isinstance(position, Position):
        raise KernelValueError("unrealized requires a Position")
    if not isinstance(currency, CurrencyCode):
        raise KernelValueError("unrealized requires a CurrencyCode")
    price = exit_price_for(position.side, bid_close, spread_model)
    direction = decimal_from_int(1 if position.side is OrderSide.BUY else -1)
    with localcontext(kernel_context()):
        amount = (price.value - position.entry_price.value) * position.quantity.units * direction
    return Money(amount, currency)
