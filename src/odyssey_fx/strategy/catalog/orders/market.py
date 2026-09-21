"""成行の注文意図 `market_order_intent` v1（D05 §4.3(3)）。

配送された取引機会の銘柄と方向をそのまま注文意図にする。数量・価格・リスクは決めない
（上位設計書 §4.7.1。数量はエンジン側のリスク方針が決める）。

`expiry=None` は「D06 が定める既定の有効時間に従う」を意味する（D05 §4.3(3)）。戦略側で
既定値を書くと、同じ意味の設定が2か所に分かれる。
"""

from __future__ import annotations

from collections.abc import Mapping

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    single_payload,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    StatelessImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import OPPORTUNITY_V1, ORDER_INTENT_V1
from odyssey_fx.strategy.declarations.evaluation import AllowedInputEvent, EvaluationSpec
from odyssey_fx.strategy.declarations.read_spec import DeliveredEvent
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    OutputSpec,
    PortKind,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import Opportunity, OrderIntent, OrderType

__all__ = ["CONTRACT", "IMPLEMENTATION_REF", "REGISTRATION", "evaluate"]

IMPLEMENTATION_REF = declared_implementation_ref("market_order_intent", 1)

CONTRACT = ComponentContract(
    component_id="market_order_intent",
    version=1,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "opportunity": InputSpec(
            data_type=OPPORTUNITY_V1,
            kind=PortKind.EVENT,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=DeliveredEvent(),
        )
    },
    outputs={
        "intent": OutputSpec(
            data_type=ORDER_INTENT_V1,
            kind=PortKind.COMMAND,
            reference_schema={},
            retrigger_mode=None,
        )
    },
    parameters={},
    evaluation_spec=EvaluationSpec(
        allowed=(AllowedInputEvent(input_names=("opportunity",)),), fixed=False
    ),
    state_spec=None,
    temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """配送された取引機会から成行の注文意図を作る（D05 §4.3(3)）。"""
    payload = single_payload(inputs, "opportunity")
    if not isinstance(payload, Opportunity):
        raise KernelValueError(
            f"market_order_intent input 'opportunity' must carry an Opportunity, got {payload!r}"
        )
    intent = OrderIntent(
        symbol=payload.symbol,
        direction=payload.direction,
        order_type=OrderType.MARKET,
        price_condition=None,
        expiry=None,
    )
    return ComponentOutputs(outputs={"intent": intent})


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
)
