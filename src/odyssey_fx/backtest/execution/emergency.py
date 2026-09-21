"""始値の飛び越えと約定直後の緊急決済（D06 §7.5）。

`EXECUTION_OPEN` フェーズの中の順序は確定済みである（上位設計書 §4.7.12）。

1. 既存建玉の始値が保護水準を飛び越えていれば、同じ始値で**通常の損切り決済**にする。
2. 適格な成行注文を約定させる。
3. 新規建玉を初期化し、初期の損切りを有効化する。
4. 新規約定の直後に損切りを越える gap が成立していれば、同じ始値で**損切り到達**として
   処理する（分類は緊急決済ではない）。
5. gap が無く、約定ずれが Δ を超えていれば、同じ始値で緊急決済する。
6. 4 と 5 が同時に成立する場合は 4 を実行し、約定ずれ超過は診断として残すが2件目の決済を
   実行しない。

本モジュールは 1・4・5 の**判定**だけを持つ。順序そのものはエンジンが実装する。
"""

from __future__ import annotations

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.domain.policies import SpreadModel
from odyssey_fx.backtest.domain.positions import ProtectionState
from odyssey_fx.backtest.execution.fill_model import is_adverse_fill_excessive
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import Price, PriceOffset

__all__ = ["gap_breaches_stop", "needs_emergency_close", "protection_base_price"]


def gap_breaches_stop(
    side: OrderSide, protection: ProtectionState, open_bid: Price, spread_model: SpreadModel
) -> bool:
    """始値が損切り水準を飛び越えているか（D06 §7.5 の手順1・4）。"""
    if not isinstance(protection, ProtectionState):
        raise KernelValueError("gap_breaches_stop requires a ProtectionState")
    if side is OrderSide.BUY:
        return open_bid <= protection.stop_loss
    return spread_model.ask_from_bid(open_bid) >= protection.stop_loss


def protection_base_price(
    side: OrderSide, level: Price, open_price: Price | None, spread_model: SpreadModel
) -> tuple[Price, bool]:
    """保護決済の基準価格（上位設計書 §4.7.12）。

    基準は保護水準そのものだが、**始値が既にその水準を越えている場合は始値を基準**にする。
    到達不能な保護水準の価格で約定させないためである。戻り値の2つ目は、提示価格の幅を
    通したか（始値を基準にしたか）を表す。

    `side` は**決済注文の売買方向**である（買い建玉を閉じる売り注文なら `SELL`）。
    """
    if open_price is None:
        return level, False
    quote = open_price if side is OrderSide.SELL else spread_model.ask_from_bid(open_price)
    if side is OrderSide.SELL:
        # 買い建玉の決済（売り）。始値が水準より下なら始値のほうが不利で、水準には戻れない。
        return (quote, True) if quote < level else (level, False)
    # 売り建玉の決済（買い）。始値が水準より上なら始値のほうが不利。
    return (quote, True) if quote > level else (level, False)


def needs_emergency_close(
    fill: Price, reference: Price, side: OrderSide, limit: PriceOffset
) -> bool:
    """約定ずれ超過による緊急決済が要るか（D06 §7.5 の手順5）。"""
    return is_adverse_fill_excessive(fill, reference, side, limit)
