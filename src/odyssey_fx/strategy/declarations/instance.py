"""使用箇所 `ComponentInstance`（D04 §3、上位設計書 §4.3.5）。

同じ部品（契約）を、戦略の中で別々の設定で何度も使えるようにするための型である。検証
戦略 A では、高値・安値の抽出という1つの契約を `mode=MAX` と `mode=MIN` の2使用箇所で
使う（D05 §4.3、Q8 決定）。

役割（`role`）・出力型の再宣言・実行中の状態は持たせない（上位設計書 §4.3.5）。役割は
戦略側の名前付きフィールドが指し、状態はランタイムが持つ。

契約との照合（入力名・パラメータ名・値・評価条件が契約の範囲内か）は `compiler` が行う
（D04 §12）。ここで見るのは構造的な不変条件だけである。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import ContractRef
from odyssey_fx.strategy.declarations.contract import SCHEMA_VERSION
from odyssey_fx.strategy.declarations.evaluation import EvaluationSchedule
from odyssey_fx.strategy.declarations.specs import (
    InputBinding,
    ParameterValue,
    require_parameter_value,
)
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    require_identifier,
    require_instance,
    require_int,
    require_mapping_of,
)

__all__ = ["ComponentInstance"]


@dataclass(frozen=True, slots=True)
class ComponentInstance:
    """戦略内での部品の使用設定（上位設計書 §4.3.5 の5フィールド ＋ `schema_version`）。"""

    instance_id: str
    contract_ref: ContractRef
    inputs: Mapping[str, InputBinding]
    parameters: Mapping[str, ParameterValue]
    evaluation: EvaluationSchedule
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.instance_id, "ComponentInstance.instance_id")
        require_instance(self.contract_ref, ContractRef, "ComponentInstance.contract_ref")
        require_mapping_of(self.inputs, InputBinding, "ComponentInstance.inputs")
        if not isinstance(self.parameters, Mapping):
            raise KernelValueError("ComponentInstance.parameters must be a Mapping")
        for name, value in self.parameters.items():
            require_identifier(name, "ComponentInstance.parameters key")
            require_parameter_value(value, f"ComponentInstance.parameters[{name!r}]")
        object.__setattr__(self, "inputs", freeze_mapping(dict(self.inputs)))
        object.__setattr__(self, "parameters", freeze_mapping(dict(self.parameters)))
        require_instance(self.evaluation, EvaluationSchedule, "ComponentInstance.evaluation")
        version = require_int(self.schema_version, "ComponentInstance.schema_version")
        if version < 1:
            raise KernelValueError(f"ComponentInstance.schema_version must be >= 1, got {version}")
