"""部品テーブル（段階2の初版 D05 §4.3、Q8 決定。段階3 の部品を D05 §4.5〜§4.10・§9.2 で追加）。

**段階2の5部品**は、検証戦略 A（1時間足の高値突破 → 後続確認なしの成行 → 初期損切り＋
固定リスクリワード比の利確）に必要な最小集合を、**役割ごとに1部品**で揃えたものである。
高値と安値の抽出は1部品にまとめ、使用箇所を2つ置く。

| 部品 | 役割 |
|---|---|
| `extreme_price` | 窓内の高値の最大・安値の最小 |
| `breakout_trigger` | 水準突破の検出（取引機会） |
| `market_order_intent` | 成行の注文意図 |
| `level_stop_loss` | 価格水準型の初期損切り |
| `fixed_rr_take_profit` | 固定リスクリワード比の利確 |

**段階3 で足した登録**（D05 §4.5〜§4.10・§9.2）。部品（実装）は Q12 の決定どおりの件数で、
v2 は同じ実装を別の読み取り条件・起動条件で登録した契約の版である（D05 §9.2）。段階2 の
v1 は検証戦略 A のために残す。

| 部品 | 版 | 役割 |
|---|---|---|
| `ema` | v1・v2 | 指数移動平均（v2 は日足を1本ぶん待つ） |
| `atr` | v1 | 真の値幅の平均 |
| `price_compare` | v1・v2 | 価格比較（v2 は両入力を1本ぶん待つ） |
| `all_conditions` / `any_condition` | v1 | 論理積・論理和 |
| `condition_transition` | v1 | 遷移検出 |
| `permission_from_condition` | v1・v2 | 条件から取引許可（v2 は買いの条件を1本ぶん待つ） |
| `constant_condition` | v1 | 固定値の条件 |
| `condition_filter` | v1 | 条件の成立による後続確認 |
| `trailing_stop` | v1 | 追従する損切り水準の更新 |
| `breakout_trigger` | v2 | 水準突破の検出（水準を1本ぶん待つ） |
| `market_order_intent` / `level_stop_loss` | v2 | 確認結果の配送で起動する版 |

段階3 の部品を載せたのは、コンパイラが後続確認・待機・遡りなどの能力検査を外し、段階3 の
新しい検査（D05 §5.6 の検査 a〜h）を備えた変更と同時である（部品が先に載ると、検査の無い
まま宣言が通る）。**それらを実行時に動かす仕組みはランタイム側の後続の変更で入る**
（D05 §6.7〜§6.12・§7.6・§7.7）。

**いずれの部品も NumPy を使わない**（D05 §4.1・§4.5 の末尾）。
"""

from __future__ import annotations

from typing import Final

from odyssey_fx.strategy.catalog.conditions import compare, logic, transition
from odyssey_fx.strategy.catalog.exits import fixed_rr, trailing_stop
from odyssey_fx.strategy.catalog.features import atr, ema, extreme
from odyssey_fx.strategy.catalog.filters import condition_filter
from odyssey_fx.strategy.catalog.orders import market
from odyssey_fx.strategy.catalog.permissions import from_condition
from odyssey_fx.strategy.catalog.protection import level_stop
from odyssey_fx.strategy.catalog.registry import ComponentRegistry, build_registry
from odyssey_fx.strategy.catalog.triggers import breakout

__all__ = ["INITIAL_CATALOG"]

#: 部品テーブル（段階2 の5部品 D05 §4.3 と、段階3 の登録 D05 §4.5〜§4.10・§9.2）。
INITIAL_CATALOG: Final[ComponentRegistry] = build_registry(
    (
        # 段階2 の5部品（D05 §4.3）
        extreme.REGISTRATION,
        breakout.REGISTRATION,
        market.REGISTRATION,
        level_stop.REGISTRATION,
        fixed_rr.REGISTRATION,
        # 段階3 の部品（D05 §4.5〜§4.10）
        ema.REGISTRATION,
        atr.REGISTRATION,
        compare.REGISTRATION,
        logic.ALL_REGISTRATION,
        logic.ANY_REGISTRATION,
        transition.REGISTRATION,
        from_condition.REGISTRATION,
        from_condition.CONSTANT_REGISTRATION,
        condition_filter.REGISTRATION,
        trailing_stop.REGISTRATION,
        # 段階3 の v2 契約（D05 §9.2）
        ema.REGISTRATION_V2,
        compare.REGISTRATION_V2,
        from_condition.REGISTRATION_V2,
        breakout.REGISTRATION_V2,
        market.REGISTRATION_V2,
        level_stop.REGISTRATION_V2,
    )
)
