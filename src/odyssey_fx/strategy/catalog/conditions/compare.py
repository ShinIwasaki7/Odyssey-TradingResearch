"""価格比較 `price_compare` v1 / v2（D05 §4.6(8)。v2 は D05 §9.2）。

`ConditionState(left <operator> right)` を返す。比較は `Price` 同士だけで行う（D02 §4.3）。
等値は `GE` / `LE` が含み、`GT` / `LT` が含まない。

**版**: v1 は欠損で評価を見送る。v2 は同じ実装を、`left` / `right` が読めないときに日足
1本ぶん待つ読み取り条件で登録したものである（D05 §9.2）。検証戦略 B では日足 EMA の出力を
`right` に読み、上流が待機しているあいだその出力は「まだ出ていない」ので、下流も待機できる
必要がある（D05 §6.8 の「待機の伝播」）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    price_payload,
    str_parameter,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    StatelessImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import CONDITION_STATE_V1, PRICE_V1
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline
from odyssey_fx.strategy.declarations.evaluation import AllowedBarClose, EvaluationSpec
from odyssey_fx.strategy.declarations.missing import (
    MissingInputPolicy,
    OnSuperseded,
    SkipEvaluation,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.read_spec import LatestAvailable
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    OutputSpec,
    ParameterSpec,
    ParameterType,
    PortKind,
    StrValue,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import ConditionState

__all__ = [
    "CONTRACT",
    "CONTRACT_V2",
    "IMPLEMENTATION_REF",
    "OPERATORS",
    "REGISTRATION",
    "REGISTRATION_V2",
    "evaluate",
]

#: 比較演算子の許可値（D05 §4.6(8)）。
OPERATORS: Final = ("GT", "GE", "LT", "LE")

IMPLEMENTATION_REF = declared_implementation_ref("price_compare", 1)

#: v2 の待機（D05 §9.2）: 日足1本ぶん待ち、期限では見送り、追い越されたら失効させる。
_WAIT_ONE_BAR = WaitForInput(
    deadline=BarsDeadline(bars=1),
    on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
    on_superseded=OnSuperseded.EXPIRE_REQUEST,
)


def _price_input(on_missing: MissingInputPolicy) -> InputSpec:
    return InputSpec(
        data_type=PRICE_V1,
        kind=PortKind.VALUE,
        arity=InputArity(min_count=1, max_count=1),
        read_spec=LatestAvailable(max_age=None, on_missing=on_missing),
    )


def _contract(version: int, on_missing: MissingInputPolicy) -> ComponentContract:
    return ComponentContract(
        component_id="price_compare",
        version=version,
        implementation_ref=IMPLEMENTATION_REF,
        inputs={"left": _price_input(on_missing), "right": _price_input(on_missing)},
        outputs={
            "condition": OutputSpec(
                data_type=CONDITION_STATE_V1,
                kind=PortKind.VALUE,
                reference_schema={},
                retrigger_mode=None,
            )
        },
        parameters={
            "operator": ParameterSpec(
                value_type=ParameterType.STR,
                allowed_values=tuple(StrValue(name) for name in OPERATORS),
            )
        },
        evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
        state_spec=None,
        temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
    )


CONTRACT = _contract(1, SkipEvaluation())
CONTRACT_V2 = _contract(2, _WAIT_ONE_BAR)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """2つの価格を比べて条件の成否を返す（D05 §4.6(8)）。"""
    left = price_payload(inputs, "left")
    right = price_payload(inputs, "right")
    operator = str_parameter(parameters, "operator")
    if operator == "GT":
        satisfied = left > right
    elif operator == "GE":
        satisfied = left >= right
    elif operator == "LT":
        satisfied = left < right
    elif operator == "LE":
        satisfied = left <= right
    else:  # pragma: no cover - 許可値の検査がコンパイル時に弾く（D04 §12 #3）
        raise KernelValueError(
            f"price_compare operator must be one of {OPERATORS}, got {operator!r}"
        )
    return ComponentOutputs(outputs={"condition": ConditionState(satisfied)})


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
)

REGISTRATION_V2 = ComponentRegistration(
    contract=CONTRACT_V2,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
)
