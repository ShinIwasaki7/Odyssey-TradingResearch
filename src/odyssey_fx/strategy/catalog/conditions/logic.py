"""論理積 `all_conditions` v1 / 論理和 `any_condition` v1（D05 §4.6(9)(10)）。

接続された全要素の `satisfied` の論理積（`all_conditions`）または論理和（`any_condition`）を
返す。入力は `arity=(2, None)` の可変個数で、`InputBinding.sources` の並びを保つ（D04 §3）。

**欠損を `False` に変換しない**（上位設計書 §4.3.15）。1件でも欠損すれば `on_missing` に
従い、評価そのものを行わない（ランタイムの責務。部品には揃った入力だけが渡る）。
"""

from __future__ import annotations

from collections.abc import Mapping

from odyssey_fx.common.refs import ImplementationRef
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    condition_payloads,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    StatelessImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import CONDITION_STATE_V1
from odyssey_fx.strategy.declarations.evaluation import AllowedBarClose, EvaluationSpec
from odyssey_fx.strategy.declarations.missing import SkipEvaluation
from odyssey_fx.strategy.declarations.read_spec import LatestAvailable
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    OutputSpec,
    PortKind,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import ConditionState

__all__ = [
    "ALL_CONTRACT",
    "ALL_IMPLEMENTATION_REF",
    "ALL_REGISTRATION",
    "ANY_CONTRACT",
    "ANY_IMPLEMENTATION_REF",
    "ANY_REGISTRATION",
    "evaluate_all",
    "evaluate_any",
]

ALL_IMPLEMENTATION_REF = declared_implementation_ref("all_conditions", 1)
ANY_IMPLEMENTATION_REF = declared_implementation_ref("any_condition", 1)


def _contract(component_id: str, implementation_ref: ImplementationRef) -> ComponentContract:
    return ComponentContract(
        component_id=component_id,
        version=1,
        implementation_ref=implementation_ref,
        inputs={
            "conditions": InputSpec(
                data_type=CONDITION_STATE_V1,
                kind=PortKind.VALUE,
                arity=InputArity(min_count=2, max_count=None),
                read_spec=LatestAvailable(max_age=None, on_missing=SkipEvaluation()),
            )
        },
        outputs={
            "condition": OutputSpec(
                data_type=CONDITION_STATE_V1,
                kind=PortKind.VALUE,
                reference_schema={},
                retrigger_mode=None,
            )
        },
        parameters={},
        evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
        state_spec=None,
        temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
    )


ALL_CONTRACT = _contract("all_conditions", ALL_IMPLEMENTATION_REF)
ANY_CONTRACT = _contract("any_condition", ANY_IMPLEMENTATION_REF)


def evaluate_all(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """接続された条件の論理積（D05 §4.6(9)）。"""
    del parameters  # パラメータを持たない。
    conditions = condition_payloads(inputs, "conditions")
    satisfied = all(condition.satisfied for condition in conditions)
    return ComponentOutputs(outputs={"condition": ConditionState(satisfied)})


def evaluate_any(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """接続された条件の論理和（D05 §4.6(10)）。"""
    del parameters  # パラメータを持たない。
    conditions = condition_payloads(inputs, "conditions")
    satisfied = any(condition.satisfied for condition in conditions)
    return ComponentOutputs(outputs={"condition": ConditionState(satisfied)})


ALL_REGISTRATION = ComponentRegistration(
    contract=ALL_CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate_all),
    implementation_ref=ALL_IMPLEMENTATION_REF,
)

ANY_REGISTRATION = ComponentRegistration(
    contract=ANY_CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate_any),
    implementation_ref=ANY_IMPLEMENTATION_REF,
)
