"""run 末尾の手順と末尾の3集計（D06 §10.1・§10.3）。

予定した run_end では、手順1〜4（内部約定の解決・期限・通常の評価・受付前拒否）を通常の
フェーズがそのまま行い、rank 14 で手順5〜7 を**この順**に行う。

1. 手順5: 残った受付済み `PENDING` を `CANCELED`（理由 `RUN_END`）にし、未約定予約を解放する
2. 手順6: 残存する取引機会を `is_run_end=True` の `step` で終端する
3. 手順7: 残存建玉は未決済のまま MTM 評価して最終 snapshot を保存する

注文の取消を機会の終端より先に置くのは、機会の終端理由が注文の状態に依存しないためであり、
逆にすると終端済みの機会に対応する注文が後から取り消される記録になる。最終 snapshot を
最後に置くのは、取消と終端の結果を含んだ台帳を保存するためである。

本モジュールは順序に依存しない**集計の式**だけを持つ（順序そのものは `loop` が実装する）。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import localcontext

from odyssey_fx.backtest.domain.account import AccountLedger
from odyssey_fx.backtest.domain.fills import CostKind
from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.domain.policies import CostModel
from odyssey_fx.backtest.execution.cost_model import FillPurpose
from odyssey_fx.backtest.execution.fill_model import fill_price
from odyssey_fx.backtest.portfolio.mtm import exit_price_for
from odyssey_fx.backtest.trace.result import FinalSummaries
from odyssey_fx.common.money import Money, Price, decimal_from_int, kernel_context

__all__ = ["RUN_END_STEPS", "final_summaries"]

#: 末尾処理の手順（D06 §10.1）。順序そのものを1か所に書いておく。
RUN_END_STEPS: tuple[str, ...] = (
    "resolve intrabar fills and update the ledger",
    "expire orders that reached their deadline",
    "run the ordinary evaluation and keep the judgement history",
    "reject the resulting order intents before admission with RUN_END",
    "cancel the remaining accepted orders and release their reservations",
    "terminate the remaining opportunities with the end-of-run step",
    "value the remaining positions and save the final ledger snapshot",
)


def final_summaries(
    *,
    ledger: AccountLedger,
    initial_balance: Money,
    equity: Money,
    last_close: Price | None,
    cost_model: CostModel,
    cost_totals: Mapping[CostKind, Money],
) -> FinalSummaries | None:
    """末尾の3集計（D06 §10.3、上位設計書 §4.7.13 E）。

    最終評価価格が無いまま残存建玉がある場合は `None` を返す。含み損益を評価できないまま
    `equity_with_mtm` と `hypothetical_closed` を埋めると、「保有建玉を架空価格で閉じない」
    という規則に反する。

    `hypothetical_closed` は**計算だけ**行い、注文・約定・完了取引数・balance・リスク枠を
    変更しない。
    """
    positions = ledger.open_positions()
    if positions and last_close is None:
        return None
    currency = ledger.currency
    zero = Money(decimal_from_int(0), currency)
    hypothetical = zero
    for position in positions:
        if last_close is None:  # pragma: no cover - 直前で判定済み
            continue
        closing_side = OrderSide.SELL if position.side is OrderSide.BUY else OrderSide.BUY
        base = exit_price_for(position.side, last_close, cost_model.spread_model)
        price = fill_price(base, closing_side, FillPurpose.CLOSE, cost_model, use_spread=False)
        with localcontext(kernel_context()):
            direction = decimal_from_int(1 if position.side is OrderSide.BUY else -1)
            gross = (price.value - position.entry_price.value) * position.quantity.units * direction
            commission = cost_model.commission_per_unit.amount * position.quantity.units
        hypothetical = hypothetical + Money(gross - commission, currency)
    return FinalSummaries(
        realized=ledger.balance - initial_balance,
        equity_with_mtm=equity,
        hypothetical_closed=hypothetical,
        cost_breakdown={kind: cost_totals.get(kind, zero) for kind in CostKind},
    )
