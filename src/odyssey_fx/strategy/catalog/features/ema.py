"""指数移動平均 `ema` v1 / v2（D05 §4.5(6)、Q11 決定。v2 は D05 §9.2）。

平滑化係数は `alpha = 2 / (period + 1)`。窓の**古い側の `period` 本の単純平均**を種 `e` と
し、残りの足について古い順に `e = alpha * x + (1 - alpha) * e` を適用し、最後の `e` を返す。

**状態を持たず、毎回の評価を窓だけで決める**（Q11 決定、選択肢1）。状態に前回値を積み上げる
と、同じ対象区間の答えが run の開始位置に依存する。窓だけで決めれば、待機から再開したとき
も同じ窓を読み直すだけで同じ値が出る（D05 §6.8）。ウォームアップ不足は履歴読み取りの
`WARMUP_INSUFFICIENT` として現れ、見送りになる。

**数値**（D05 §4.5）: 計算は有効桁28・`ROUND_HALF_EVEN` の局所的な `Decimal` の文脈
（`kernel_context()`）で行い、プロセスの既定文脈に依存させない。更新は
`e = (2 * x + (period - 1) * e) / (period + 1)` の形で1回だけ割る。`alpha` を先に丸めて
から掛けると、割り切れる入力でも末尾に丸めの誤差が乗るためである（数式としては同じもの。
紙上トレース T02 §2.4 の検算値はこの形で丸めが1度も働かないことを前提にしている）。
価格刻みへの丸めは行わない（D06 の責務）。

**パラメータどうしの関係**: `window_bars >= 2 * period` を要求する（Q22 決定）。種を作る
`period` 本のあとに平滑化する足が残らない設定を通すと、EMA が単純移動平均に化けたまま動く。
契約は各パラメータの範囲しか持てないので、関係は登録の `parameter_constraint` に置き、
コンパイラがパラメータ解決の後に呼ぶ（D05 §4.1・§5.6 の検査 e）。

**版**: v1 は欠損で評価を見送る（`SkipEvaluation`）。v2 は同じ実装を、履歴窓が読めない
ときに日足1本ぶん待つ読み取り条件（`WaitForInput`）で登録したものである（D05 §9.2。日足
から市場状態を作る連鎖の先頭）。読み取り条件は契約が固定するので版を分けるほかない
（D04 §4.1）。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, localcontext

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import Price, decimal_from_int, kernel_context
from odyssey_fx.strategy.catalog.inputs import (
    ResolvedInputsView,
    ResolvedParameterView,
    int_parameter,
    price_window,
)
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    ParameterConstraint,
    StatelessImplementation,
    declared_implementation_ref,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import PRICE_V1
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline
from odyssey_fx.strategy.declarations.evaluation import AllowedBarClose, EvaluationSpec
from odyssey_fx.strategy.declarations.missing import (
    MissingInputPolicy,
    OnSuperseded,
    SkipEvaluation,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.read_spec import BarsWindow, HistoryWindow, ParameterRef
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputSpec,
    IntValue,
    NumericBounds,
    OutputSpec,
    ParameterSpec,
    ParameterType,
    PortKind,
    UnitRef,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints

__all__ = [
    "CONTRACT",
    "CONTRACT_V2",
    "IMPLEMENTATION_REF",
    "PARAMETER_CONSTRAINT",
    "REGISTRATION",
    "REGISTRATION_V2",
    "evaluate",
    "window_covers_two_periods",
]

IMPLEMENTATION_REF = declared_implementation_ref("ema", 1)

#: v2 の待機（D05 §9.2）: 日足1本ぶん待ち、期限では見送り、追い越されたら失効させる。
_WAIT_ONE_BAR = WaitForInput(
    deadline=BarsDeadline(bars=1),
    on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
    on_superseded=OnSuperseded.EXPIRE_REQUEST,
)


def window_covers_two_periods(parameters: Mapping[str, ResolvedParameterView]) -> bool:
    """`window_bars >= 2 * period` が成り立つか（D05 §4.5、Q22 決定）。

    純粋関数であり、渡された写像だけを読む。整数でない値が来たら例外で終わる（コンパイラは
    例外も拒否として扱う。D05 §4.1）。`atr` も同じ関係を使う。
    """
    period = parameters["period"].value
    window_bars = parameters["window_bars"].value
    if not isinstance(period, IntValue) or not isinstance(window_bars, IntValue):
        raise KernelValueError(
            f"window_bars and period must be integers, got {window_bars!r} and {period!r}"
        )
    return window_bars.value >= 2 * period.value


#: 登録が持つパラメータどうしの関係（D05 §4.5、Q22 決定）。
PARAMETER_CONSTRAINT = ParameterConstraint(
    reads=("window_bars", "period"),
    check=window_covers_two_periods,
    message="種を作る period 本のあとに平滑化する足が残らない（window_bars >= 2 * period）",
)

_PARAMETERS = {
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
}


def _contract(version: int, on_missing: MissingInputPolicy) -> ComponentContract:
    return ComponentContract(
        component_id="ema",
        version=version,
        implementation_ref=IMPLEMENTATION_REF,
        inputs={
            "prices": InputSpec(
                data_type=PRICE_V1,
                kind=PortKind.VALUE,
                arity=InputArity(min_count=1, max_count=1),
                read_spec=HistoryWindow(
                    window=BarsWindow(ParameterRef("window_bars")),
                    max_age=None,
                    on_missing=on_missing,
                    exclude_latest_bars=0,
                ),
            )
        },
        outputs={
            "value": OutputSpec(
                data_type=PRICE_V1,
                kind=PortKind.VALUE,
                reference_schema={},
                retrigger_mode=None,
            )
        },
        parameters=_PARAMETERS,
        evaluation_spec=EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=False),
        state_spec=None,
        temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
    )


CONTRACT = _contract(1, SkipEvaluation())
CONTRACT_V2 = _contract(2, _WAIT_ONE_BAR)


def evaluate(
    inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
) -> ComponentOutputs:
    """窓から指数移動平均を計算する（D05 §4.5(6)）。

    解決済みの窓の本数が `window_bars` と一致することを確かめる。食い違えば、宣言と実際に
    読んだ履歴が別物になっており、黙って計算すると原因の分からない数値差になる。
    """
    prices = price_window(inputs, "prices")
    period = int_parameter(parameters, "period")
    window_bars = int_parameter(parameters, "window_bars")
    if len(prices) != window_bars:
        raise KernelValueError(
            f"ema expected {window_bars} bars from its history window, got {len(prices)}"
        )
    if window_bars < 2 * period:
        raise KernelValueError(
            f"ema needs window_bars >= 2 * period, got window_bars={window_bars}, period={period}"
        )
    values = [price.value for price in prices]
    with localcontext(kernel_context()):
        denominator = decimal_from_int(period + 1)
        keep = decimal_from_int(period - 1)
        two = decimal_from_int(2)
        e: Decimal = sum(values[:period], start=decimal_from_int(0)) / decimal_from_int(period)
        for x in values[period:]:
            e = (two * x + keep * e) / denominator
    return ComponentOutputs(outputs={"value": Price(e)})


REGISTRATION = ComponentRegistration(
    contract=CONTRACT,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
    parameter_constraint=PARAMETER_CONSTRAINT,
)

REGISTRATION_V2 = ComponentRegistration(
    contract=CONTRACT_V2,
    implementation=StatelessImplementation(evaluate=evaluate),
    implementation_ref=IMPLEMENTATION_REF,
    parameter_constraint=PARAMETER_CONSTRAINT,
)
