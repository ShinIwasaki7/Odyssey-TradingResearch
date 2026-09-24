"""成行の注文意図 `market_order_intent` v1（D05 §4.3(3)）。

配送された取引機会の銘柄と方向をそのまま注文意図にする。数量・価格・リスクは決めない
（上位設計書 §4.7.1。数量はエンジン側のリスク方針が決める）。

`expiry=None` は「D06 が定める既定の有効時間に従う」を意味する（D05 §4.3(3)）。戦略側で
既定値を書くと、同じ意味の設定が2か所に分かれる。

**v2**（D05 §9.2）: 後続確認を待つ戦略では、注文意図は**確認結果の配送**で起動する
（D05 §7.7）。入力の型と起動条件は契約が固定するので、`confirmation`（`confirmation_result@v1`
の配送）で起動する版を登録する。注文意図に要る銘柄と方向は確認結果ではなく機会から来るため、
v2 は `opportunity` をランタイムが保持する確認待ちの機会（`RuntimeInputRef(OPPORTUNITY)` を
`CurrentContext` で読む。D05 §6.11）として読む。v1 の配送イベントと v2 の現在コンテキストは
どちらも同じ名前の入力に `Opportunity` を載せるので、実装は v1 と同じである。
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
from odyssey_fx.strategy.declarations.datatypes import (
    CONFIRMATION_RESULT_V1,
    OPPORTUNITY_V1,
    ORDER_INTENT_V1,
)
from odyssey_fx.strategy.declarations.evaluation import AllowedInputEvent, EvaluationSpec
from odyssey_fx.strategy.declarations.read_spec import CurrentContext, DeliveredEvent
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    OutputSpec,
    PortKind,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import Opportunity, OrderIntent, OrderType

__all__ = [
    "CONTRACT",
    "CONTRACT_V2",
    "IMPLEMENTATION_REF",
    "REGISTRATION",
    "REGISTRATION_V2",
    "evaluate",
]

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

#: 確認結果の配送で起動する版（D05 §9.2）。
CONTRACT_V2 = ComponentContract(
    component_id="market_order_intent",
    version=2,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "confirmation": InputSpec(
            data_type=CONFIRMATION_RESULT_V1,
            kind=PortKind.EVENT,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=DeliveredEvent(),
        ),
        "opportunity": InputSpec(
            data_type=OPPORTUNITY_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=CurrentContext(),
        ),
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
        allowed=(AllowedInputEvent(input_names=("confirmation",)),), fixed=False
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

REGISTRATION_V2 = ComponentRegistration(
    contract=CONTRACT_V2,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
)
