"""高値・安値の抽出 `extreme_price` v1（D05 §4.3(1)）。

窓の中の価格の最大（`MAX`）または最小（`MIN`）を返す。検証戦略 A では、この1つの契約を
2つの使用箇所で使う（突破水準は高値の最大、損切り水準は安値の最小）。役割ごとに契約を
分けないのは、段階3で合成部品を足すときに契約を作り直さずに済むようにするためである
（D05 §4.3、Q8 決定）。

窓は `exclude_latest_bars=1` により**当該足を含まない**（D04 §6.2）。判断のもとになった足
自身の高値を突破水準にすると、その足の終値が必ず水準以下になり、突破が原理的に起きない。

比較は `Price` 同士のみで行う（D02 §4.3）。窓の要素が価格でなければ実行失敗にする。
"""

from __future__ import annotations

from collections.abc import Mapping

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    int_parameter,
    price_window,
    str_parameter,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    StatelessImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import PRICE_V1
from odyssey_fx.strategy.declarations.evaluation import AllowedBarClose, EvaluationSpec
from odyssey_fx.strategy.declarations.missing import SkipEvaluation
from odyssey_fx.strategy.declarations.read_spec import BarsWindow, HistoryWindow, ParameterRef
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    NumericBounds,
    OutputSpec,
    ParameterSpec,
    ParameterType,
    PortKind,
    StrValue,
    UnitRef,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints

__all__ = ["CONTRACT", "IMPLEMENTATION_REF", "MODE_MAX", "MODE_MIN", "REGISTRATION", "evaluate"]

MODE_MAX = "MAX"
MODE_MIN = "MIN"

IMPLEMENTATION_REF = declared_implementation_ref("extreme_price", 1)

CONTRACT = ComponentContract(
    component_id="extreme_price",
    version=1,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "prices": InputSpec(
            data_type=PRICE_V1,
            kind=PortKind.VALUE,
            arity=InputArity(min_count=1, max_count=1),
            read_spec=HistoryWindow(
                window=BarsWindow(ParameterRef("lookback")),
                max_age=None,
                on_missing=SkipEvaluation(),
                exclude_latest_bars=1,
            ),
        )
    },
    outputs={
        "level": OutputSpec(
            data_type=PRICE_V1,
            kind=PortKind.VALUE,
            reference_schema={},
            retrigger_mode=None,
        )
    },
    parameters={
        "lookback": ParameterSpec(
            value_type=ParameterType.INT,
            unit=UnitRef.BARS,
            bounds=NumericBounds(minimum=2, maximum=500),
        ),
        "mode": ParameterSpec(
            value_type=ParameterType.STR,
            allowed_values=(StrValue(MODE_MAX), StrValue(MODE_MIN)),
        ),
    },
    evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
    state_spec=None,
    temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """窓内の価格の最大または最小を返す（D05 §4.3(1)）。

    `lookback` は窓の解決にコンパイラが使う（D04 §12 #3）。ここでも読み、解決済みの窓の
    本数と一致することを確かめる。食い違えば、宣言と実際に読んだ履歴が別物になっており、
    黙って計算すると原因の分からない数値差になるためである。
    """
    prices = price_window(inputs, "prices")
    lookback = int_parameter(parameters, "lookback")
    if len(prices) != lookback:
        raise KernelValueError(
            f"extreme_price expected {lookback} bars from its history window, got {len(prices)}"
        )
    mode = str_parameter(parameters, "mode")
    if mode == MODE_MAX:
        level = max(prices)
    elif mode == MODE_MIN:
        level = min(prices)
    else:  # pragma: no cover - 許可値の検査がコンパイル時に弾く（D04 §12 #3）
        raise KernelValueError(f"extreme_price mode must be MAX or MIN, got {mode!r}")
    return ComponentOutputs(outputs={"level": level})


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
)
