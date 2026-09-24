"""条件の成立による後続確認 `condition_filter` v1（D05 §4.8(14)）。

`ConfirmationOutcome(confirmed=condition.satisfied, reference_values={})` を返す。機会の識別子と
確認足の区間は**ランタイムが付けて** `ConfirmationResult` を組み立てる（D05 §4.8）。部品に
3つとも作らせると、別の機会の確認結果を作れてしまうためである。

`confirmed=False` も**出力として出す**。上位設計書 §4.3.15 は「条件未成立」を入力不足・
期限切れ・追い越しと区別することを求めており、未成立を出力として残さないと4者を判断履歴で
区別できない。

`opportunity` 入力は、ランタイム自身が保持する確認待ちの取引機会（`RuntimeInputRef(OPPORTUNITY)`
を `CurrentContext` で読む。D05 §6.11）である。部品の計算には使わないが、評価要求がどの
機会を指しているかを契約の上で明示するために必須の入力にしてある。渡された中身が取引機会で
なければ、接続の食い違いとして実行失敗にする。

`include_start_bar`（開始足で確認を始めるか）は契約のパラメータに置き、使用箇所が値を明示
する（全体計画 §5.3.3・上位設計書 §4.3.13）。値を読むのはコンパイラで、確認の計画に載せる
（D05 §5.3）。部品の計算は開始足かどうかに依らない。
"""

from __future__ import annotations

from collections.abc import Mapping

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    condition_payload,
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
    CONDITION_STATE_V1,
    CONFIRMATION_RESULT_V1,
    OPPORTUNITY_V1,
)
from odyssey_fx.strategy.declarations.evaluation import AllowedBarClose, EvaluationSpec
from odyssey_fx.strategy.declarations.missing import SkipEvaluation
from odyssey_fx.strategy.declarations.read_spec import CurrentContext, LatestAvailable
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    OutputSpec,
    ParameterSpec,
    ParameterType,
    PortKind,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import ConfirmationOutcome, Opportunity

__all__ = ["CONTRACT", "IMPLEMENTATION_REF", "REGISTRATION", "evaluate"]

IMPLEMENTATION_REF = declared_implementation_ref("condition_filter", 1)

CONTRACT = ComponentContract(
    component_id="condition_filter",
    version=1,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "condition": InputSpec(
            data_type=CONDITION_STATE_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=LatestAvailable(max_age=None, on_missing=SkipEvaluation()),
        ),
        "opportunity": InputSpec(
            data_type=OPPORTUNITY_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=CurrentContext(),
        ),
    },
    outputs={
        "confirmation": OutputSpec(
            data_type=CONFIRMATION_RESULT_V1,
            kind=PortKind.EVENT,
            reference_schema={},
            retrigger_mode=None,
        )
    },
    parameters={"include_start_bar": ParameterSpec(value_type=ParameterType.BOOL)},
    evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
    state_spec=None,
    temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """条件の成否をそのまま確認の成否にする（D05 §4.8(14)）。"""
    del parameters  # 開始足の扱いはランタイムが確認の計画から読む（D05 §7.7）。
    opportunity = single_payload(inputs, "opportunity")
    if not isinstance(opportunity, Opportunity):
        raise KernelValueError(
            f"condition_filter input 'opportunity' must carry an Opportunity, got {opportunity!r}"
        )
    condition = condition_payload(inputs, "condition")
    outcome = ConfirmationOutcome(confirmed=condition.satisfied, reference_values={})
    return ComponentOutputs(outputs={"confirmation": outcome})


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
)
