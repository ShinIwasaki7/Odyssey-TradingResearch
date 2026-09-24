"""真の値幅の平均 `atr` v1（D05 §4.5(7)、Q12 決定）。

各足の真の値幅を `max(high - low, |high - prev_close|, |low - prev_close|)` とし、古い側の
`period` 本の単純平均を種にして `e = (e * (period - 1) + tr) / period`（Wilder の平滑）を
古い順に適用する。窓の先頭の足は前足の終値が無いため真の値幅を計算せず、2本目から数える。

**観測区間の一致を宣言する唯一の段階3 部品**（D05 §4.5）。高値・安値・終値の3本の窓が
別々の系列や区間から来ると、真の値幅が別の足の高値と安値から計算される。契約は
`AlignmentRequirement(("closes", "highs", "lows"), SAME_OBSERVATION_INTERVAL)` を持ち、守らせる
のはランタイムである（D05 §6.7）。部品は3本の窓の本数が揃っていることだけを確かめる。

状態を持たず窓だけで決める点、数値の文脈、`window_bars >= 2 * period` の関係は `ema` と
同じである（関係の関数も共有する）。出力は価格差（`PriceOffset`）であり、刻みへの丸めは
行わない。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, localcontext

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import PriceOffset, decimal_from_int, kernel_context
from odyssey_fx.strategy.catalog.features.ema import PARAMETER_CONSTRAINT
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    int_parameter,
    price_window,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    StatelessImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import PRICE_OFFSET_V1, PRICE_V1
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
    UnitRef,
)
from odyssey_fx.strategy.declarations.temporal import (
    AlignmentRequirement,
    AlignmentRule,
    TemporalConstraints,
)

__all__ = ["CONTRACT", "IMPLEMENTATION_REF", "REGISTRATION", "evaluate"]

IMPLEMENTATION_REF = declared_implementation_ref("atr", 1)


def _window_input() -> InputSpec:
    return InputSpec(
        data_type=PRICE_V1,
        kind=PortKind.VALUE,
        arity=InputArity(min_count=1, max_count=1),
        read_spec=HistoryWindow(
            window=BarsWindow(ParameterRef("window_bars")),
            max_age=None,
            on_missing=SkipEvaluation(),
            exclude_latest_bars=0,
        ),
    )


CONTRACT = ComponentContract(
    component_id="atr",
    version=1,
    implementation_ref=IMPLEMENTATION_REF,
    inputs={
        "highs": _window_input(),
        "lows": _window_input(),
        "closes": _window_input(),
    },
    outputs={
        "value": OutputSpec(
            data_type=PRICE_OFFSET_V1,
            kind=PortKind.VALUE,
            reference_schema={},
            retrigger_mode=None,
        )
    },
    parameters={
        "period": ParameterSpec(
            value_type=ParameterType.INT,
            unit=UnitRef.BARS,
            bounds=NumericBounds(minimum=2, maximum=500),
        ),
        "window_bars": ParameterSpec(
            value_type=ParameterType.INT,
            unit=UnitRef.BARS,
            bounds=NumericBounds(minimum=4, maximum=2000),
        ),
    },
    evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
    state_spec=None,
    temporal_constraints=TemporalConstraints(
        warmup=None,
        alignment=(
            AlignmentRequirement(
                input_names=("closes", "highs", "lows"),
                rule=AlignmentRule.SAME_OBSERVATION_INTERVAL,
            ),
        ),
    ),
)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """高値・安値・終値の窓から真の値幅の平均を計算する（D05 §4.5(7)）。"""
    highs = price_window(inputs, "highs")
    lows = price_window(inputs, "lows")
    closes = price_window(inputs, "closes")
    period = int_parameter(parameters, "period")
    window_bars = int_parameter(parameters, "window_bars")
    for name, window in (("highs", highs), ("lows", lows), ("closes", closes)):
        if len(window) != window_bars:
            raise KernelValueError(
                f"atr expected {window_bars} bars in {name!r}, got {len(window)}"
            )
    if window_bars < 2 * period:
        raise KernelValueError(
            f"atr needs window_bars >= 2 * period, got window_bars={window_bars}, period={period}"
        )
    with localcontext(kernel_context()):
        true_ranges: list[Decimal] = []
        for index in range(1, window_bars):
            high = highs[index].value
            low = lows[index].value
            previous_close = closes[index - 1].value
            true_ranges.append(
                max(high - low, abs(high - previous_close), abs(low - previous_close))
            )
        divisor = decimal_from_int(period)
        keep = decimal_from_int(period - 1)
        e: Decimal = sum(true_ranges[:period], start=decimal_from_int(0)) / divisor
        for tr in true_ranges[period:]:
            e = (e * keep + tr) / divisor
    return ComponentOutputs(outputs={"value": PriceOffset(e)})


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
    parameter_constraint=PARAMETER_CONSTRAINT,
)
