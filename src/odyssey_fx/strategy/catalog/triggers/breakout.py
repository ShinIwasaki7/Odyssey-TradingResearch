"""水準突破の検出 `breakout_trigger` v1（D05 §4.3(2)）。

価格が水準を上抜けた（`LONG`）または下抜けた（`SHORT`）ことを取引機会として出す。

**再武装**（D04 §10.4）: 契約は `retrigger_mode=EDGE` で登録する。条件が不成立から成立へ
変わった評価でだけ発火し、成立が続く間は再発火しない。再武装は条件が不成立へ戻った時点で
ある。直前の評価で成立していたかどうかは、契約が宣言する状態（条件の成否）が持つ。

状態は**成立・不成立のどちらでも毎回更新する**。更新を発火時だけにすると、不成立へ戻った
ことを記録できず、再武装が効かなくなる。ただし入力欠損で評価を見送った回は更新しない
（更新の抑止はランタイムの責務。D05 §6.5）。

部品が返すのは方向と根拠値だけで、識別子・銘柄・対象区間はランタイムが付ける（D05 §4.2）。

同じ実装を `retrigger_mode=LEVEL` の契約として登録すると、成立している評価ごとに出力を
出し、状態を持たない形になる。段階2ではその契約を作らない（D04 §10.4 は宣言としては両方を
許す）。

**v2**（D05 §4.9・§9.2）: 同じ実装を、`level` が読めないときに日足1本ぶん待つ読み取り条件
（`WaitForInput`）で登録した版である。検証戦略 B は `level` に日足の高値を接続するので、日足が
未到着のまま1時間足だけが確定する判断時点がある。v1 はそこで見送りに決着して復活しないため、
待てる版が要る。読み取り条件は契約が固定するので版を分けるほかない（D04 §4.1）。`price` の
読み取り条件は v1 と同じである。v1 は段階2 の検証戦略 A のために残す。
"""

from __future__ import annotations

from collections.abc import Mapping

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
    StatefulImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import CONDITION_STATE_V1, OPPORTUNITY_V1, PRICE_V1
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
    BoolValue,
    InputArity,
    InputSpec,
    OutputSpec,
    ParameterSpec,
    ParameterType,
    PortKind,
    RetriggerMode,
    StrValue,
)
from odyssey_fx.strategy.declarations.state_spec import (
    LiteralInitialState,
    ResetTrigger,
    StateSpec,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import (
    ConditionState,
    OpportunityContent,
    TradeDirection,
)

__all__ = [
    "BREAKOUT_LEVEL_KEY",
    "CONTRACT",
    "CONTRACT_V2",
    "IMPLEMENTATION_REF",
    "REGISTRATION",
    "REGISTRATION_V2",
    "evaluate",
]

#: 根拠値の名前（突破した水準）。`OutputSpec.reference_schema` の唯一のキー。
BREAKOUT_LEVEL_KEY = "breakout_level"

IMPLEMENTATION_REF = declared_implementation_ref("breakout_trigger", 1)

#: v2 の待機（D05 §9.2）: 日足1本ぶん待ち、期限では見送り、追い越されたら失効させる
#: （上位設計書 §4.3.14 の「Trigger の標準方針」の具体値）。
_WAIT_ONE_BAR = WaitForInput(
    deadline=BarsDeadline(bars=1),
    on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
    on_superseded=OnSuperseded.EXPIRE_REQUEST,
)


def _contract(version: int, level_on_missing: MissingInputPolicy) -> ComponentContract:
    return ComponentContract(
        component_id="breakout_trigger",
        version=version,
        implementation_ref=IMPLEMENTATION_REF,
        inputs={
            "price": InputSpec(
                data_type=PRICE_V1,
                kind=PortKind.VALUE,
                arity=InputArity(min_count=1, max_count=1),
                read_spec=LatestAvailable(max_age=None, on_missing=SkipEvaluation()),
            ),
            "level": InputSpec(
                data_type=PRICE_V1,
                kind=PortKind.VALUE,
                arity=InputArity(min_count=1, max_count=1),
                read_spec=LatestAvailable(max_age=None, on_missing=level_on_missing),
            ),
        },
        outputs={
            "opportunity": OutputSpec(
                data_type=OPPORTUNITY_V1,
                kind=PortKind.EVENT,
                reference_schema={BREAKOUT_LEVEL_KEY: PRICE_V1},
                retrigger_mode=RetriggerMode.EDGE,
            )
        },
        parameters={
            "direction": ParameterSpec(
                value_type=ParameterType.STR,
                allowed_values=(
                    StrValue(TradeDirection.LONG.value),
                    StrValue(TradeDirection.SHORT.value),
                ),
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


CONTRACT = _contract(1, SkipEvaluation())
CONTRACT_V2 = _contract(2, _WAIT_ONE_BAR)


def evaluate(
    inputs: ResolvedInputsView,
    parameters: Mapping[str, ResolvedParameterView],
    state: object,
) -> ComponentOutputs:
    """成立を判定し、不成立から成立へ変わった評価でだけ取引機会を出す（D05 §4.3(2)）。"""
    if not isinstance(state, ConditionState):
        raise KernelValueError(f"breakout_trigger state must be a ConditionState, got {state!r}")
    price = price_payload(inputs, "price")
    level = price_payload(inputs, "level")
    direction_name = str_parameter(parameters, "direction")
    try:
        direction = TradeDirection(direction_name)
    except ValueError as error:  # pragma: no cover - 許可値の検査がコンパイル時に弾く
        raise KernelValueError(
            f"breakout_trigger direction must be LONG or SHORT, got {direction_name!r}"
        ) from error

    now = price > level if direction is TradeDirection.LONG else price < level
    outputs: dict[str, object] = {}
    if now and not state.satisfied:
        outputs["opportunity"] = OpportunityContent(
            direction=direction,
            reference_values={BREAKOUT_LEVEL_KEY: level},
        )
    return ComponentOutputs(outputs=outputs, new_state=ConditionState(now))


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatefulImplementation(evaluate=evaluate, state_type=CONDITION_STATE_V1),
    implementation_ref=IMPLEMENTATION_REF,
)

REGISTRATION_V2 = ComponentRegistration(
    contract=CONTRACT_V2,
    implementation=StatefulImplementation(evaluate=evaluate, state_type=CONDITION_STATE_V1),
    implementation_ref=IMPLEMENTATION_REF,
)
