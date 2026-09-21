"""固定リスクリワード比の利確 `fixed_rr_take_profit` v1（D05 §4.3(5)）。

建玉の約定価格と有効な損切り水準から「取ったリスクの何倍で利確するか」を決める。

    risk = |約定価格 − 損切り水準|
    買い: 利確 = 約定価格 + risk × reward_risk
    売り: 利確 = 約定価格 − risk × reward_risk

約定価格が決まってからでないと計算できないため、この部品は**約定通知で起動する**
（D05 §8）。次の足まで待つと、初期の利確水準を持たない建玉が生まれる。

`reward_risk` は厳密な10進数としてランタイムから渡される（D05 §4.4）。部品の内部に浮動
小数の演算を持ち込まないためで、変換されていなければ失敗させる。価格刻みへの丸めは行わ
ない（D06 の責務）。

有効な損切り水準が無い建玉では利確を計算できない。欠損に読み替えず実行失敗にする
（ランタイムが `Failed` として判断履歴に残す。D05 §6.2）。
"""

from __future__ import annotations

from collections.abc import Mapping

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    decimal_parameter,
    position_context,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    StatelessImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import (
    MANAGEMENT_ACTION_V1,
    POSITION_CONTEXT_V1,
)
from odyssey_fx.strategy.declarations.evaluation import (
    AllowedRuntimeEvent,
    EvaluationSpec,
    RuntimeEventKind,
)
from odyssey_fx.strategy.declarations.read_spec import CurrentContext
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    NumericBounds,
    OutputSpec,
    ParameterSpec,
    ParameterType,
    PortKind,
    UnitRef,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import SetTakeProfit, TradeDirection

__all__ = ["CONTRACT", "IMPLEMENTATION_REF", "REGISTRATION", "evaluate"]

IMPLEMENTATION_REF = declared_implementation_ref("fixed_rr_take_profit", 1)

CONTRACT = ComponentContract(
    component_id="fixed_rr_take_profit",
    version=1,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "position": InputSpec(
            data_type=POSITION_CONTEXT_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=CurrentContext(),
        )
    },
    outputs={
        "action": OutputSpec(
            data_type=MANAGEMENT_ACTION_V1,
            kind=PortKind.COMMAND,
            reference_schema={},
            retrigger_mode=None,
        )
    },
    parameters={
        "reward_risk": ParameterSpec(
            value_type=ParameterType.FLOAT,
            unit=UnitRef.RATIO,
            bounds=NumericBounds(
                minimum=0,
                maximum=100,
                minimum_inclusive=False,
                maximum_inclusive=True,
            ),
        )
    },
    evaluation_spec=EvaluationSpec(
        allowed=(AllowedRuntimeEvent(events=(RuntimeEventKind.POSITION_OPENED,)),), fixed=False
    ),
    state_spec=None,
    temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """約定価格と損切り水準から初期の利確水準を決める（D05 §4.3(5)）。"""
    position = position_context(inputs, "position")
    stop_loss = position.effective_stop_loss
    if stop_loss is None:
        raise KernelValueError(
            "fixed_rr_take_profit needs an effective stop loss on the position to size the"
            " take profit"
        )
    entry = position.entry_price
    reward_risk = decimal_parameter(parameters, "reward_risk")

    if position.direction is TradeDirection.LONG:
        risk = entry - stop_loss
        if risk.value <= 0:
            raise KernelValueError(
                f"a long position must have its stop loss below the entry price"
                f" (entry {entry}, stop {stop_loss})"
            )
        take_profit = entry + risk * reward_risk
    else:
        risk = stop_loss - entry
        if risk.value <= 0:
            raise KernelValueError(
                f"a short position must have its stop loss above the entry price"
                f" (entry {entry}, stop {stop_loss})"
            )
        take_profit = entry - risk * reward_risk

    return ComponentOutputs(outputs={"action": SetTakeProfit(price=take_profit)})


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
)
