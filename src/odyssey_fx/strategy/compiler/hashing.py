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

入力の計画のうち遡りの上限の解決値（`InputPlan.resolved_max_lookback`、2026-09-24 の人間の
決定で追加）は対象から外す。読み方（`read_spec`）とパラメータの解決値から一意に決まり、
どちらも対象に入っているので、指紋が区別する構成は変わらない。外すことで、追加の前に
作った指紋（検証戦略 A の `compiled_ref` など）を保つ。

順序に意味を持たせないコレクションは構築時に正規化済みなので、ここで並べ替えを重複実装
しない（D04 §13.2）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields

from odyssey_fx.common import canonical
from odyssey_fx.common.refs import CompiledStrategyRef, StrategyRef
from odyssey_fx.strategy.compiler.compiled import CompiledComponent, InputPlan

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
                "input_plans": _input_plans_payload(component.input_plans),
                "triggers": component.triggers,
            }
            for component in components
        ),
    }
    return CompiledStrategyRef(digest=canonical.digest(payload))


#: 指紋の対象から外す入力の計画のフィールド（モジュール docstring を参照）。
_EXCLUDED_PLAN_FIELDS = frozenset({"resolved_max_lookback"})


def _input_plans_payload(plans: Mapping[str, InputPlan]) -> dict[str, dict[str, object]]:
    """入力の計画を、指紋の対象のフィールドだけを持つ mapping にする。

    正規化エンコードは dataclass を「フィールド名 → 値」の mapping として符号化する
    （D02 §9.3）ので、同じフィールドだけを持つ mapping は追加の前と同じ符号になる。
    """
    return {
        name: {
            field.name: getattr(plan, field.name)
            for field in fields(plan)
            if field.name not in _EXCLUDED_PLAN_FIELDS
        }
        for name, plan in plans.items()
    }
