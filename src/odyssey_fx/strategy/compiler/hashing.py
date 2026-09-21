"""解決済み設定の内容ハッシュ（D05 §5.5、D04 §13.2）。

`CompiledStrategyRef` は「この実行で実際に動く構成」の指紋である。戦略定義の指紋
（`StrategyRef`）と違い、**探索で作った異なるパラメータ割当**と**部品実装の同一性**まで
含む。同じ `strategy_id/version` でもパラメータが違えば別の実行として扱う（上位設計書
§4.3.5）。

`CompiledStrategy` 全体をそのまま対象にしない。`strategy_ref` と `compiled_ref` 自身を
含む自己参照になるためである。対象は次のとおり（D05 §5.5 の3）。

- 戦略定義の指紋（`strategy_ref`）
- 評価順
- 各使用箇所の `(instance_id, contract_ref, implementation_ref, parameters, input_plans, triggers)`

順序に意味を持たせないコレクションは構築時に正規化済みなので、ここで並べ替えを重複実装
しない（D04 §13.2）。
"""

from __future__ import annotations

from collections.abc import Sequence

from odyssey_fx.common import canonical
from odyssey_fx.common.refs import CompiledStrategyRef, StrategyRef
from odyssey_fx.strategy.compiler.compiled import CompiledComponent

__all__ = ["compiled_strategy_ref"]


def compiled_strategy_ref(
    strategy_ref: StrategyRef,
    evaluation_order: Sequence[str],
    components: Sequence[CompiledComponent],
) -> CompiledStrategyRef:
    """解決済み設定の指紋を作る（D05 §5.5 の3）。"""
    payload = {
        "strategy_ref": {
            "strategy_id": strategy_ref.strategy_id,
            "version": strategy_ref.version,
            "digest": strategy_ref.digest.hex,
        },
        "evaluation_order": tuple(evaluation_order),
        "components": tuple(
            {
                "instance_id": component.instance_id,
                "contract_ref": {
                    "component_id": component.contract_ref.component_id,
                    "version": component.contract_ref.version,
                    "digest": component.contract_ref.digest.hex,
                },
                "implementation_ref": {
                    "implementation_id": component.implementation_ref.implementation_id,
                    "digest": component.implementation_ref.digest.hex,
                },
                "parameters": component.parameters,
                "input_plans": component.input_plans,
                "triggers": component.triggers,
            }
            for component in components
        ),
    }
    return CompiledStrategyRef(digest=canonical.digest(payload))
