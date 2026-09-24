"""市場状態の部品（D05 §4.7）。

- `permission_from_condition` v1 / v2: `MarketPermission(allow_long=long_allowed.satisfied,
  allow_short=short_allowed.satisfied)` を返す。
- `constant_condition` v1: `ConditionState(value)` を返す（入力を持たない）。

段階3 は**変換部品を1つだけ置き、条件の作り方は価格比較と論理合成に任せる**（D05 §4.7）。
検証戦略 B の「日足終値が日足 EMA を上回るなら買い許可」は、`price_compare(GT)` の出力を
`long_allowed` に、`constant_condition(value=False)` の出力を `short_allowed` に接続して表す。
`short_allowed` を省略可にしないのは、省略時の既定が暗黙の許可または禁止になるためである
（ADR-0031 の「暗黙の既定値を設けない」）。

入力を1つも持たない `constant_condition` は、依存グラフで評価順の先頭の段に来る。銘柄は
使用箇所の起動条件（`OnBarClose.series`）から伝播する（D04 §5 の供給元2）。

**版**: `permission_from_condition` v2 は同じ実装を、`long_allowed` が読めないときに日足
1本ぶん待つ読み取り条件で登録したものである（D05 §9.2）。`short_allowed` は入力を持たない
固定値の条件から来るので常に読め、待機の対象にしない。
"""

from __future__ import annotations

from collections.abc import Mapping

from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    bool_parameter,
    condition_payload,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    StatelessImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import CONDITION_STATE_V1, MARKET_PERMISSION_V1
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
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import ConditionState, MarketPermission

__all__ = [
    "CONSTANT_CONTRACT",
    "CONSTANT_IMPLEMENTATION_REF",
    "CONSTANT_REGISTRATION",
    "CONTRACT",
    "CONTRACT_V2",
    "IMPLEMENTATION_REF",
    "REGISTRATION",
    "REGISTRATION_V2",
    "evaluate",
    "evaluate_constant",
]

IMPLEMENTATION_REF = declared_implementation_ref("permission_from_condition", 1)
CONSTANT_IMPLEMENTATION_REF = declared_implementation_ref("constant_condition", 1)

#: v2 の待機（D05 §9.2）: 日足1本ぶん待ち、期限では見送り、追い越されたら失効させる。
_WAIT_ONE_BAR = WaitForInput(
    deadline=BarsDeadline(bars=1),
    on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
    on_superseded=OnSuperseded.EXPIRE_REQUEST,
)


def _condition_input(on_missing: MissingInputPolicy) -> InputSpec:
    return InputSpec(
        data_type=CONDITION_STATE_V1,
        kind=PortKind.VALUE,
        arity=InputArity(min_count=1, max_count=1),
        read_spec=LatestAvailable(max_age=None, on_missing=on_missing),
    )


def _contract(version: int, long_on_missing: MissingInputPolicy) -> ComponentContract:
    return ComponentContract(
        component_id="permission_from_condition",
        version=version,
        implementation_ref=IMPLEMENTATION_REF,
        inputs={
            "long_allowed": _condition_input(long_on_missing),
            "short_allowed": _condition_input(SkipEvaluation()),
        },
        outputs={
            "permission": OutputSpec(
                data_type=MARKET_PERMISSION_V1,
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


CONTRACT = _contract(1, SkipEvaluation())
CONTRACT_V2 = _contract(2, _WAIT_ONE_BAR)

CONSTANT_CONTRACT = ComponentContract(
    component_id="constant_condition",
    version=1,
    implementation_ref=CONSTANT_IMPLEMENTATION_REF,
    inputs={},
    outputs={
        "condition": OutputSpec(
            data_type=CONDITION_STATE_V1,
            kind=PortKind.VALUE,
            reference_schema={},
            retrigger_mode=None,
        )
    },
    parameters={"value": ParameterSpec(value_type=ParameterType.BOOL)},
    evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
    state_spec=None,
    temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """2つの条件から取引許可を作る（D05 §4.7(12)）。"""
    del parameters  # パラメータを持たない。
    long_allowed = condition_payload(inputs, "long_allowed")
    short_allowed = condition_payload(inputs, "short_allowed")
    permission = MarketPermission(
        allow_long=long_allowed.satisfied, allow_short=short_allowed.satisfied
    )
    return ComponentOutputs(outputs={"permission": permission})


def evaluate_constant(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """宣言した固定値の条件を返す（D05 §4.7(13)）。"""
    del inputs  # 入力を持たない。
    return ComponentOutputs(
        outputs={"condition": ConditionState(bool_parameter(parameters, "value"))}
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

CONSTANT_REGISTRATION = ComponentRegistration(
    contract=CONSTANT_CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate_constant),
    implementation_ref=CONSTANT_IMPLEMENTATION_REF,
)
