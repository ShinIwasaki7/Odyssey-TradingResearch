"""遷移検出 `condition_transition` v1（D05 §4.6(11)）。

`RISING` は「直前が不成立で今回が成立」、`FALLING` は「直前が成立で今回が不成立」のときだけ
`ConditionState(True)` を返し、それ以外は `ConditionState(False)` を返す。新しい状態は今回の
入力の条件である。状態は成立・不成立のどちらでも毎回更新する（更新を遷移時だけにすると、
戻ったことを記録できない）。入力欠損で見送った回に状態を更新しないのはランタイムの責務
である（D05 §6.5）。

**取引機会を出す部品の再武装（`EDGE`）とは別物である**（D05 §4.6）。再武装は取引機会を
出す出力仕様が決める規則、遷移検出は条件そのものを遷移の条件へ変換する部品であり、取引
機会を出さないので `retrigger_mode` を持たない（D04 §12 #6b）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    condition_payload,
    str_parameter,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    StatefulImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import CONDITION_STATE_V1
from odyssey_fx.strategy.declarations.evaluation import AllowedBarClose, EvaluationSpec
from odyssey_fx.strategy.declarations.missing import SkipEvaluation
from odyssey_fx.strategy.declarations.read_spec import LatestAvailable
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    InputArity,
    InputSpec,
    OutputSpec,
    ParameterSpec,
    ParameterType,
    PortKind,
    StrValue,
)
from odyssey_fx.strategy.declarations.state_spec import (
    LiteralInitialState,
    ResetTrigger,
    StateSpec,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import ConditionState

__all__ = [
    "CONTRACT",
    "EDGE_FALLING",
    "EDGE_RISING",
    "IMPLEMENTATION_REF",
    "REGISTRATION",
    "evaluate",
]

EDGE_RISING: Final = "RISING"
EDGE_FALLING: Final = "FALLING"

IMPLEMENTATION_REF = declared_implementation_ref("condition_transition", 1)

CONTRACT = ComponentContract(
    component_id="condition_transition",
    version=1,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "condition": InputSpec(
            data_type=CONDITION_STATE_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
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
    parameters={
        "edge": ParameterSpec(
            value_type=ParameterType.STR,
            allowed_values=(StrValue(EDGE_RISING), StrValue(EDGE_FALLING)),
        )
    },
    evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
    state_spec=StateSpec(
        state_type=CONDITION_STATE_V1,
        initial=LiteralInitialState({"satisfied": BoolValue(False)}),
        reset_on=(ResetTrigger.RUN_START,),
    ),
    temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
)


def evaluate(
    inputs: ResolvedInputsView,
    parameters: Mapping[str, ResolvedParameterView],
    state: object,
) -> ComponentOutputs:
    """直前の状態と今回の条件から遷移を検出する（D05 §4.6(11)）。"""
    if not isinstance(state, ConditionState):
        raise KernelValueError(
            f"condition_transition state must be a ConditionState, got {state!r}"
        )
    now = condition_payload(inputs, "condition").satisfied
    before = state.satisfied
    edge = str_parameter(parameters, "edge")
    if edge == EDGE_RISING:
        crossed = now and not before
    elif edge == EDGE_FALLING:
        crossed = before and not now
    else:  # pragma: no cover - 許可値の検査がコンパイル時に弾く（D04 §12 #3）
        raise KernelValueError(f"condition_transition edge must be RISING or FALLING, got {edge!r}")
    return ComponentOutputs(
        outputs={"condition": ConditionState(crossed)}, new_state=ConditionState(now)
    )


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatefulImplementation(evaluate=evaluate, state_type=CONDITION_STATE_V1),
    implementation_ref=IMPLEMENTATION_REF,
)
