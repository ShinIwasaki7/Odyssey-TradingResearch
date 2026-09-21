"""段階2の初版カタログ（D05 §4.3、Q8 決定）。

検証戦略 A（1時間足の高値突破 → 後続確認なしの成行 → 初期損切り＋固定リスクリワード比の
利確）に必要な最小集合を、**役割ごとに1部品**で揃える。高値と安値の抽出は1部品にまとめ、
使用箇所を2つ置く。段階3で合成部品を足すときに、ここで作る5部品の契約を作り直さずに済む。

| 部品 | 役割 |
|---|---|
| `extreme_price` | 窓内の高値の最大・安値の最小 |
| `breakout_trigger` | 水準突破の検出（取引機会） |
| `market_order_intent` | 成行の注文意図 |
| `level_stop_loss` | 価格水準型の初期損切り |
| `fixed_rr_take_profit` | 固定リスクリワード比の利確 |

**段階2の5部品はいずれも NumPy を使わない**（D05 §4.1）。NumPy の使用は指標部品を足す
D05 v0.2 で改めて判断する（許可範囲そのものは ADR-0021 で確定済み）。
"""

from __future__ import annotations

from typing import Final

from odyssey_fx.strategy.catalog.exits import fixed_rr
from odyssey_fx.strategy.catalog.features import extreme
from odyssey_fx.strategy.catalog.orders import market
from odyssey_fx.strategy.catalog.protection import level_stop
from odyssey_fx.strategy.catalog.registry import ComponentRegistry, build_registry
from odyssey_fx.strategy.catalog.triggers import breakout

__all__ = ["INITIAL_CATALOG"]

#: 段階2の部品テーブル（D05 §4.3）。
INITIAL_CATALOG: Final[ComponentRegistry] = build_registry(
    (
        extreme.REGISTRATION,
        breakout.REGISTRATION,
        market.REGISTRATION,
        level_stop.REGISTRATION,
        fixed_rr.REGISTRATION,
    )
)
