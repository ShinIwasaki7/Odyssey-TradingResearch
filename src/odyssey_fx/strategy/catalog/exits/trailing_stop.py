"""追従する損切り水準の更新 `trailing_stop` v1（D05 §4.10(16)、Q12・Q14 決定）。

建玉の方向が `LONG` なら、`level` が現在の有効な損切り水準より**高い**ときだけ
`UpdateStop(level)` を返す。`SHORT` なら**低い**ときだけ返す。条件を満たさない評価では
**出力を出さない**（出さなかった出力は「今回の評価では発生しなかった」を意味する。D05 §4.1）。

**損切りを不利な向きへ動かさないことを部品側で保証する**（D05 §4.10）。受付側が向きを検査
しても「宣言として不正」としか記録できず、戦略の意図なのか誤りなのかが判断履歴から読めない。
部品が返さなければ「その足では更新がなかった」として残る。

この部品は**建玉が存在する足の確定でだけ評価される**。建玉1件につき1要求を作るのはランタイム
である（D05 §6.11）。適用の意味論（どの執行足から有効か、同じ判断時点の決済要求との競合、
価格刻みへの丸め）は D06 §8.3 の責務で、部品は丸めない。

有効な損切り水準を持たない建玉では比べる基準が無い。欠損に読み替えず実行失敗にする
（固定リスクリワード比の利確と同じ扱い）。
"""

from __future__ import annotations

from collections.abc import Mapping

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    position_context,
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
    MANAGEMENT_ACTION_V1,
    POSITION_CONTEXT_V1,
    PRICE_V1,
)
from odyssey_fx.strategy.declarations.evaluation import AllowedBarClose, EvaluationSpec
from odyssey_fx.strategy.declarations.missing import SkipEvaluation
from odyssey_fx.strategy.declarations.read_spec import CurrentContext, LatestAvailable
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    OutputSpec,
    PortKind,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from odyssey_fx.strategy.records.payloads import TradeDirection, UpdateStop

__all__ = ["CONTRACT", "IMPLEMENTATION_REF", "REGISTRATION", "evaluate"]

IMPLEMENTATION_REF = declared_implementation_ref("trailing_stop", 1)

CONTRACT = ComponentContract(
    component_id="trailing_stop",
    version=1,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "position": InputSpec(
            data_type=POSITION_CONTEXT_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=CurrentContext(),
        ),
        "level": InputSpec(
            data_type=PRICE_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=LatestAvailable(max_age=None, on_missing=SkipEvaluation()),
        ),
    },
    outputs={
        "action": OutputSpec(
            data_type=MANAGEMENT_ACTION_V1,
            kind=PortKind.COMMAND,
            reference_schema={},
            retrigger_mode=None,
        )
    },
    parameters={},
    evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
    state_spec=None,
    temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """有利な向きへ動くときだけ損切り水準の更新を返す（D05 §4.10(16)）。"""
    del parameters  # パラメータを持たない。
    position = position_context(inputs, "position")
    level = price_payload(inputs, "level")
    current = position.effective_stop_loss
    if current is None:
        raise KernelValueError(
            "trailing_stop needs an effective stop loss on the position to compare against"
        )
    if position.direction is TradeDirection.LONG:
        tightens = level > current
    else:
        tightens = level < current
    if not tightens:
        return ComponentOutputs(outputs={})
    return ComponentOutputs(outputs={"action": UpdateStop(stop_loss=level)})


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
)
