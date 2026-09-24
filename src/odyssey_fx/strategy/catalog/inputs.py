"""部品が受け取るものの形（D05 §4.4 v1.3）。

部品には `ResolvedInputs`（D05 §6.3）と `ResolvedParameter`（D05 §5.3）だけを渡す。市場
データ全体・台帳・他の使用箇所の出力への到達手段は渡さない（全体計画 §5.3.3）。

**なぜここに「形」だけを置くのか**: `ResolvedInputs` の実体は `runtime`、
`ResolvedParameter` の実体は `compiler` にある（D05 §3 の置き場所）。どちらも `catalog`
より**上の層**なので、部品の実装が型を直接 import すると D01 §3.3 の層順序（契約 L2b）に
違反する。そこで `catalog` 側には**構造だけを述べる `Protocol`** を置き、上の層の具体型が
それを構造的に満たす形にする。型の置き場所は D05 のままで、層順序も守れる。

`PositionContextView` も同じ理由による。建玉の payload の項目を定めるのは D06 であり
（D05 §4.2）、段階2 の `strategy` にその型は無い。固定リスクリワード比の利確が読む3項目
（方向・約定価格・有効な損切り水準）だけを構造として要求する。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol, runtime_checkable

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import Price
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    IntValue,
    ParameterValue,
    StrValue,
    UnitRef,
)
from odyssey_fx.strategy.records.payloads import ConditionState, TradeDirection

__all__ = [
    "ContextSnapshotView",
    "EventDeliveryView",
    "InputElementView",
    "PositionContextView",
    "ResolvedInputsView",
    "ResolvedParameterView",
    "ValueSampleView",
    "ValueWindowView",
    "bool_parameter",
    "condition_payload",
    "condition_payloads",
    "decimal_parameter",
    "int_parameter",
    "position_context",
    "price_payload",
    "price_window",
    "single_payload",
    "str_parameter",
]


@runtime_checkable
class ValueSampleView(Protocol):
    """繰り返し参照する値の1件（`runtime.requests.ValueSample` が満たす）。"""

    @property
    def payload(self) -> object: ...


@runtime_checkable
class ValueWindowView(Protocol):
    """繰り返し参照する値の窓（`runtime.requests.ValueWindow` が満たす）。古い順。"""

    @property
    def samples(self) -> tuple[ValueSampleView, ...]: ...


@runtime_checkable
class EventDeliveryView(Protocol):
    """配送されたイベント1件（`runtime.requests.EventDelivery` が満たす）。"""

    @property
    def payload(self) -> object: ...


@runtime_checkable
class ContextSnapshotView(Protocol):
    """現在コンテキストの1件（`runtime.requests.ContextSnapshot` が満たす）。"""

    @property
    def payload(self) -> object: ...


#: 部品から見た入力要素（D05 §6.3 の4区分）。
InputElementView = ValueSampleView | ValueWindowView | EventDeliveryView | ContextSnapshotView


@runtime_checkable
class ResolvedInputsView(Protocol):
    """解決済みの入力（`runtime.requests.ResolvedInputs` が満たす）。

    各入力名の要素の並びは `InputBinding.sources` の順である（D05 §3）。
    """

    @property
    def by_name(self) -> Mapping[str, tuple[InputElementView, ...]]: ...


@runtime_checkable
class ResolvedParameterView(Protocol):
    """解決済みのパラメータ1件（`compiler.compiled.ResolvedParameter` が満たす）。"""

    @property
    def name(self) -> str: ...

    @property
    def value(self) -> ParameterValue: ...

    @property
    def unit(self) -> UnitRef | None: ...

    @property
    def decimal_value(self) -> Decimal | None: ...


@runtime_checkable
class PositionContextView(Protocol):
    """建玉の現在コンテキストのうち、段階2の部品が読む項目（D05 §4.3(5)・D06 §8.4）。"""

    @property
    def direction(self) -> TradeDirection: ...

    @property
    def entry_price(self) -> Price: ...

    @property
    def effective_stop_loss(self) -> Price | None: ...


def _single(inputs: ResolvedInputsView, name: str) -> InputElementView:
    """入力名に対応する唯一の要素を返す。

    接続数が契約の `arity` に収まっていることはコンパイラが検査済みである（D04 §12 #2）。
    ここで数を見るのは、実装の取り違え（`arity=(1,1)` の契約なのに複数を前提にした実装）を
    その場で失敗させるためで、黙って先頭を使わない。
    """
    elements = inputs.by_name.get(name)
    if elements is None:
        raise KernelValueError(f"input {name!r} was not resolved")
    if len(elements) != 1:
        raise KernelValueError(f"input {name!r} must have exactly 1 source, got {len(elements)}")
    return elements[0]


def single_payload(inputs: ResolvedInputsView, name: str) -> object:
    """1件で読む入力（最新値・配送イベント・現在コンテキスト）の中身を返す。"""
    element = _single(inputs, name)
    payload = getattr(element, "payload", None)
    if payload is None:
        raise KernelValueError(f"input {name!r} does not carry a payload: {element!r}")
    return payload


def price_payload(inputs: ResolvedInputsView, name: str) -> Price:
    """1件で読む入力を価格として取り出す。"""
    payload = single_payload(inputs, name)
    if not isinstance(payload, Price):
        raise KernelValueError(f"input {name!r} must carry a Price, got {payload!r}")
    return payload


def price_window(inputs: ResolvedInputsView, name: str) -> tuple[Price, ...]:
    """履歴窓で読む入力を価格の列（古い順）として取り出す。"""
    element = _single(inputs, name)
    samples = getattr(element, "samples", None)
    if samples is None:
        raise KernelValueError(f"input {name!r} does not carry a history window: {element!r}")
    prices: list[Price] = []
    for index, sample in enumerate(samples):
        payload = getattr(sample, "payload", None)
        if not isinstance(payload, Price):
            raise KernelValueError(
                f"input {name!r} sample {index} must carry a Price, got {payload!r}"
            )
        prices.append(payload)
    if not prices:
        raise KernelValueError(f"input {name!r} must not be an empty history window")
    return tuple(prices)


def condition_payload(inputs: ResolvedInputsView, name: str) -> ConditionState:
    """1件で読む入力を条件の成否として取り出す（D05 §4.6、段階3）。

    上流の条件出力はランタイムが `Observation` で包んで配送するが、部品に渡す時点で中身
    （`Observation.value`）だけにする（D05 §6.7）。ここで受けるのはその中身である。
    """
    payload = single_payload(inputs, name)
    if not isinstance(payload, ConditionState):
        raise KernelValueError(f"input {name!r} must carry a ConditionState, got {payload!r}")
    return payload


def condition_payloads(inputs: ResolvedInputsView, name: str) -> tuple[ConditionState, ...]:
    """可変個数で読む入力を、接続の並び（`InputBinding.sources` の順）のまま取り出す。

    並べ替えない（D04 §3）。論理積・論理和は並びに依存しないが、判断履歴で「どの条件が
    偽だったか」を接続の並びから読むためである（D05 §4.6）。
    """
    elements = inputs.by_name.get(name)
    if elements is None:
        raise KernelValueError(f"input {name!r} was not resolved")
    if not elements:
        raise KernelValueError(f"input {name!r} must have at least 1 source")
    conditions: list[ConditionState] = []
    for index, element in enumerate(elements):
        payload = getattr(element, "payload", None)
        if not isinstance(payload, ConditionState):
            raise KernelValueError(
                f"input {name!r} source {index} must carry a ConditionState, got {payload!r}"
            )
        conditions.append(payload)
    return tuple(conditions)


def position_context(inputs: ResolvedInputsView, name: str) -> PositionContextView:
    """現在コンテキストの入力を建玉として取り出す（D05 §4.3(5)）。

    項目を定めるのは D06 なので、**読む3項目がそろっていること**だけを構造として確かめる。
    足りなければ欠損に読み替えず実行失敗にする（`KernelValueError`）。値が無いことと、型が
    違うことは別の問題だからである。
    """
    payload = single_payload(inputs, name)
    for attribute in ("direction", "entry_price", "effective_stop_loss"):
        if not hasattr(payload, attribute):
            raise KernelValueError(
                f"input {name!r} must carry a position context with {attribute!r}, got {payload!r}"
            )
    if not isinstance(payload, PositionContextView):  # pragma: no cover - 直前の検査が保証する
        raise KernelValueError(f"input {name!r} must carry a position context, got {payload!r}")
    return payload


def _parameter(parameters: Mapping[str, ResolvedParameterView], name: str) -> ResolvedParameterView:
    parameter = parameters.get(name)
    if parameter is None:
        raise KernelValueError(f"parameter {name!r} was not resolved")
    return parameter


def int_parameter(parameters: Mapping[str, ResolvedParameterView], name: str) -> int:
    """整数パラメータを取り出す。"""
    value = _parameter(parameters, name).value
    if not isinstance(value, IntValue):
        raise KernelValueError(f"parameter {name!r} must be an integer, got {value!r}")
    return value.value


def bool_parameter(parameters: Mapping[str, ResolvedParameterView], name: str) -> bool:
    """真偽値パラメータを取り出す。"""
    value = _parameter(parameters, name).value
    if not isinstance(value, BoolValue):
        raise KernelValueError(f"parameter {name!r} must be a boolean, got {value!r}")
    return value.value


def str_parameter(parameters: Mapping[str, ResolvedParameterView], name: str) -> str:
    """文字列パラメータを取り出す。"""
    value = _parameter(parameters, name).value
    if not isinstance(value, StrValue):
        raise KernelValueError(f"parameter {name!r} must be a string, got {value!r}")
    return value.value


def decimal_parameter(parameters: Mapping[str, ResolvedParameterView], name: str) -> Decimal:
    """比率・価格・pips のパラメータを厳密な10進数として取り出す（D05 §4.4）。

    `Decimal` 化はランタイムが行い、部品の内部に浮動小数の演算を持ち込まない。変換されて
    いない値が渡ってきたら、黙って `float` で計算せず失敗させる。
    """
    parameter = _parameter(parameters, name)
    if parameter.decimal_value is None:
        raise KernelValueError(
            f"parameter {name!r} must be converted to Decimal before it reaches the component"
            " (D05 §4.4)"
        )
    return parameter.decimal_value
