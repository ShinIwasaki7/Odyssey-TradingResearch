"""部品契約 `ComponentContract`（D04 §3、上位設計書 §4.3.5）。

「何を受け取り、どの条件で評価し、何を返せるか」を宣言する。使用箇所ごとの具体設定は
`ComponentInstance`、戦略全体は `StrategyDefinition` が持つ。

契約は設定ファイル（YAML）に書かず、`catalog` のコード側の登録を正本とする（D04 §13.1）。
戦略ファイルが書くのは使用箇所と役割参照・方針だけである。

**`schema_version`**（D04 §3、Q1 決定）: 保存形式の解釈規則の版。トップレベルに置くことで
内容ハッシュ（D04 §13.2）に入り、「保存形式の解釈規則を変えた」事実が再現性の差として
現れる。設定ファイル先頭の `schema_version`（ADR-0018）と一致しなければ `app.config` が
拒否する。

本型の `__post_init__` が見るのは**構造的な不変条件だけ**である（D04 §13.1）。参照解決・
型整合・銘柄の伝播は `compiler` が行い、3箇所で同じ規則を重複実装しない。例外は
`evaluation_spec` の内部整合（許可する入力イベント名が契約の `inputs` にあるか）で、
これは契約1件を見れば判定でき、外部を参照しないため構築時に弾く（D04 §8）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import ImplementationRef
from odyssey_fx.strategy.declarations.evaluation import AllowedInputEvent, EvaluationSpec
from odyssey_fx.strategy.declarations.read_spec import DeliveredEvent
from odyssey_fx.strategy.declarations.specs import InputSpec, OutputSpec, ParameterSpec
from odyssey_fx.strategy.declarations.state_spec import StateSpec
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    require_identifier,
    require_instance,
    require_int,
    require_mapping_of,
    require_version,
)

__all__ = ["SCHEMA_VERSION", "ComponentContract"]

#: 保存形式の版（D04 §3）。保存形式が変わったときに上げる。段階3 では 1 のまま
#: （2026-09-24 の人間の決定。欠損方針の区分の追加では上げない）。
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ComponentContract:
    """部品の契約（上位設計書 §4.3.5 の9フィールド ＋ `schema_version`）。"""

    component_id: str
    version: int
    implementation_ref: ImplementationRef
    inputs: Mapping[str, InputSpec]
    outputs: Mapping[str, OutputSpec]
    parameters: Mapping[str, ParameterSpec]
    evaluation_spec: EvaluationSpec
    state_spec: StateSpec | None
    temporal_constraints: TemporalConstraints
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.component_id, "ComponentContract.component_id")
        require_version(self.version, "ComponentContract.version")
        require_instance(
            self.implementation_ref, ImplementationRef, "ComponentContract.implementation_ref"
        )
        require_mapping_of(self.inputs, InputSpec, "ComponentContract.inputs")
        require_mapping_of(self.outputs, OutputSpec, "ComponentContract.outputs")
        require_mapping_of(self.parameters, ParameterSpec, "ComponentContract.parameters")
        if not self.outputs:
            raise KernelValueError("ComponentContract.outputs must not be empty")
        object.__setattr__(self, "inputs", freeze_mapping(dict(self.inputs)))
        object.__setattr__(self, "outputs", freeze_mapping(dict(self.outputs)))
        object.__setattr__(self, "parameters", freeze_mapping(dict(self.parameters)))

        require_instance(self.evaluation_spec, EvaluationSpec, "ComponentContract.evaluation_spec")
        if self.state_spec is not None:
            require_instance(self.state_spec, StateSpec, "ComponentContract.state_spec")
        require_instance(
            self.temporal_constraints, TemporalConstraints, "ComponentContract.temporal_constraints"
        )
        version = require_int(self.schema_version, "ComponentContract.schema_version")
        if version < 1:
            raise KernelValueError(f"ComponentContract.schema_version must be >= 1, got {version}")

        self._require_allowed_input_events_exist()
        self._require_alignment_inputs_exist()

    def _require_allowed_input_events_exist(self) -> None:
        """許可する入力イベント名が、配送イベントを読む入力であることを要求する（D04 §8）。"""
        for allowed in self.evaluation_spec.allowed:
            if not isinstance(allowed, AllowedInputEvent):
                continue
            for name in allowed.input_names:
                spec = self.inputs.get(name)
                if spec is None:
                    raise KernelValueError(
                        f"AllowedInputEvent names input {name!r}, which is not declared in"
                        f" ComponentContract.inputs"
                    )
                if not isinstance(spec.read_spec, DeliveredEvent):
                    raise KernelValueError(
                        f"AllowedInputEvent names input {name!r}, which does not read a"
                        f" DeliveredEvent"
                    )

    def _require_alignment_inputs_exist(self) -> None:
        """時刻整合の対象が宣言済みの入力であることを要求する（D04 §9.2）。"""
        for requirement in self.temporal_constraints.alignment:
            for name in requirement.input_names:
                if name not in self.inputs:
                    raise KernelValueError(
                        f"AlignmentRequirement names input {name!r}, which is not declared in"
                        f" ComponentContract.inputs"
                    )
