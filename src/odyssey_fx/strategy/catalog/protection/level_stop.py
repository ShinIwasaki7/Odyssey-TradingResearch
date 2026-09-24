"""価格水準型の初期損切り `level_stop_loss` v1（D05 §4.3(4)）。

接続された水準（検証戦略 A では直近の確定安値）を、そのまま損切り価格として凍結する。
距離型（「20 pips 下」など）は使わず、**解決済みの絶対価格**を渡す（上位設計書 §4.7.3）。
距離のまま渡すと、どの価格を基準に引くかがエンジン側の解釈になり、判断履歴から水準を
読めなくなる。

損切りが方向と整合するか（買いなら約定価格より下か）の検査はここでは行わない。判断時点の
参照価格を持つのは受付側（D06）であり、戦略側にその価格は無い。

初期の利確は持たない（`take_profit=None`）。利確は Exit の責務であり、約定価格が決まって
から評価する（D05 §8）。

**v2**（D05 §9.2）: 後続確認を待つ戦略では、保護水準は取引機会ではなく**確認結果の配送**で
起動する（D05 §7.7）。入力の型と起動条件は契約が固定するので（D04 §4.1・§8）、`opportunity`
の代わりに `confirmation`（`confirmation_result@v1` の配送）を持つ版を登録する。計算は
`level` だけを読むので、実装は v1 と同じである。v1 は段階2 の検証戦略 A のために残す。
"""

from __future__ import annotations

from collections.abc import Mapping

from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    price_payload,
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
    PRICE_V1,
    PROTECTION_LEVELS_V1,
)
from odyssey_fx.strategy.declarations.evaluation import AllowedInputEvent, EvaluationSpec
from odyssey_fx.strategy.declarations.missing import SkipEvaluation
from odyssey_fx.strategy.declarations.read_spec import DeliveredEvent, LatestAvailable
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    OutputSpec,
    PortKind,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import ProtectionLevels

__all__ = [
    "CONTRACT",
    "CONTRACT_V2",
    "IMPLEMENTATION_REF",
    "REGISTRATION",
    "REGISTRATION_V2",
    "evaluate",
]

IMPLEMENTATION_REF = declared_implementation_ref("level_stop_loss", 1)

CONTRACT = ComponentContract(
    component_id="level_stop_loss",
    version=1,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "opportunity": InputSpec(
            data_type=OPPORTUNITY_V1,
            kind=PortKind.EVENT,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=DeliveredEvent(),
        ),
        "level": InputSpec(
            data_type=PRICE_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=LatestAvailable(max_age=None, on_missing=SkipEvaluation()),
        ),
    },
    outputs={
        "protection": OutputSpec(
            data_type=PROTECTION_LEVELS_V1,
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
    component_id="level_stop_loss",
    version=2,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "confirmation": InputSpec(
            data_type=CONFIRMATION_RESULT_V1,
            kind=PortKind.EVENT,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=DeliveredEvent(),
        ),
        "level": InputSpec(
            data_type=PRICE_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=LatestAvailable(max_age=None, on_missing=SkipEvaluation()),
        ),
    },
    outputs={
        "protection": OutputSpec(
            data_type=PROTECTION_LEVELS_V1,
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
    """接続された水準を初期の損切り価格として凍結する（D05 §4.3(4)）。"""
    level = price_payload(inputs, "level")
    return ComponentOutputs(
        outputs={"protection": ProtectionLevels(stop_loss=level, take_profit=None)}
    )


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
