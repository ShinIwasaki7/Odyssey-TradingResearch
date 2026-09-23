"""入出力・接続・パラメータの仕様型（D04 §4.1・§4.2・§7・§10.4）。

`InputSpec` は受け口の仕様、`OutputSpec` は出し口の仕様、`InputBinding` は接続先である
（上位設計書 §4.3.6）。いずれも実際の計算結果は持たない。入力名・出力名は `Mapping` の
キーが持ち、仕様の内部に重複させない。

**`PortKind`**（D04 §4.2）は、その口が何を運ぶかの3区分である。`VALUE` は更新まで繰り返し
参照する値、`EVENT` は一度発生した事実・機会、`COMMAND` はエンジンへの要求で、役割ごとの
割り当ては D05 §4.2 が決めている。

**パラメータの数値型**（D04 §7）: `Decimal` と `Price` はパラメータ値の型に入れない。価格・
pips・比率は `FLOAT` と `unit` の組で宣言し、`Price` への変換と価格刻みでの丸めは、銘柄仕様
と丸め方向を持つ D06 の境界で行う。部品へ渡すときの `Decimal` 化は D05 §4.4 が担う。

**再武装モード**（D04 §10.4）は取引機会を出す出力の性質なので、部品全体ではなく
`OutputSpec` に置く。`EDGE` は不成立から成立へ変わった評価でだけ発火し、`LEVEL` は成立
している評価ごとに発火する。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.datatypes import DataTypeRef, require_registered
from odyssey_fx.strategy.declarations.read_spec import InputReadSpec
from odyssey_fx.strategy.declarations.refs import (
    InputSourceRef,
    MarketDataRef,
    OutputRef,
    RuntimeInputRef,
    require_kind,
)
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    normalized_unique,
    require_bool,
    require_identifier,
    require_instance,
    require_int,
    require_tuple_of,
)

__all__ = [
    "BoolValue",
    "FloatValue",
    "InputArity",
    "InputBinding",
    "InputSpec",
    "IntValue",
    "NumericBounds",
    "OutputSpec",
    "ParameterSpec",
    "ParameterType",
    "ParameterValue",
    "PortKind",
    "RetriggerMode",
    "StrValue",
    "UnitRef",
    "parameter_value_of",
    "require_parameter_value",
]

_READ_SPEC_KINDS = ("LATEST_AVAILABLE", "HISTORY_WINDOW", "DELIVERED_EVENT", "CURRENT_CONTEXT")


class PortKind(Enum):
    """口が運ぶものの区分（D04 §4.2）。"""

    #: 更新まで繰り返し参照する値。
    VALUE = "VALUE"
    #: 一度発生した事実・機会。
    EVENT = "EVENT"
    #: 処理を依頼する要求。
    COMMAND = "COMMAND"


class RetriggerMode(Enum):
    """取引機会を出す出力の再発火の仕方（D04 §10.4）。"""

    #: 条件が不成立から成立へ変わった評価でだけ発火する。再武装は不成立へ戻った時点。
    EDGE = "EDGE"
    #: 条件が成立している評価ごとに発火する。発火のたびに新しい取引機会を生成する。
    LEVEL = "LEVEL"


class ParameterType(Enum):
    """パラメータ値の型（上位設計書 §4.3.5）。`bool` と `int` を区別する。"""

    BOOL = "BOOL"
    INT = "INT"
    FLOAT = "FLOAT"
    STR = "STR"


class UnitRef(Enum):
    """数値パラメータの単位（D04 §7）。初版はこの5値。"""

    PIPS = "PIPS"
    PRICE = "PRICE"
    RATIO = "RATIO"
    BARS = "BARS"
    DURATION = "DURATION"


@dataclass(frozen=True, slots=True)
class BoolValue:
    """真偽値のパラメータ値（上位設計書 §4.3.5）。"""

    value: bool
    kind: str = "BOOL"

    def __post_init__(self) -> None:
        require_kind(self.kind, "BOOL", "BoolValue.kind")
        require_bool(self.value, "BoolValue.value")


@dataclass(frozen=True, slots=True)
class IntValue:
    """整数のパラメータ値（上位設計書 §4.3.5）。"""

    value: int
    kind: str = "INT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "INT", "IntValue.kind")
        require_int(self.value, "IntValue.value")


@dataclass(frozen=True, slots=True)
class FloatValue:
    """浮動小数のパラメータ値（上位設計書 §4.3.5）。有限値のみ。"""

    value: float
    kind: str = "FLOAT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "FLOAT", "FloatValue.kind")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise KernelValueError(f"FloatValue.value must be a float, got {self.value!r}")
        if not math.isfinite(self.value):
            raise KernelValueError(f"FloatValue.value must be finite, got {self.value!r}")
        # `1` のような整数リテラルで書かれても、以後 float として扱えるようにする。
        if not isinstance(self.value, float):
            object.__setattr__(self, "value", float(self.value))


@dataclass(frozen=True, slots=True)
class StrValue:
    """文字列のパラメータ値（上位設計書 §4.3.5）。"""

    value: str
    kind: str = "STR"

    def __post_init__(self) -> None:
        require_kind(self.kind, "STR", "StrValue.kind")
        if not isinstance(self.value, str):
            raise KernelValueError(f"StrValue.value must be a str, got {self.value!r}")


#: 区分タグ付き union（上位設計書 §4.3.5）。
ParameterValue = BoolValue | IntValue | FloatValue | StrValue

#: 値の区分と、それが満たす `ParameterType`。
_VALUE_TYPES: dict[type[ParameterValue], ParameterType] = {
    BoolValue: ParameterType.BOOL,
    IntValue: ParameterType.INT,
    FloatValue: ParameterType.FLOAT,
    StrValue: ParameterType.STR,
}


def require_parameter_value(value: object, label: str) -> ParameterValue:
    """4区分のいずれかであることを要求する。"""
    if not isinstance(value, (BoolValue, IntValue, FloatValue, StrValue)):
        raise KernelValueError(f"{label} must be a ParameterValue, got {value!r}")
    return value


def parameter_value_of(value: ParameterValue) -> ParameterType:
    """パラメータ値の区分に対応する `ParameterType` を返す。"""
    return _VALUE_TYPES[type(value)]


@dataclass(frozen=True, slots=True)
class NumericBounds:
    """数値パラメータの許容範囲（D04 §7）。

    開区間・閉区間を端ごとに選べるようにするのは、「比率は 0 より大きく 100 以下」のような
    宣言（D05 §4.3 の固定リスクリワード比）を書き分けるためである。
    """

    minimum: float | None = None
    maximum: float | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    def __post_init__(self) -> None:
        for label, value in (("minimum", self.minimum), ("maximum", self.maximum)):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise KernelValueError(f"NumericBounds.{label} must be a number, got {value!r}")
            if not math.isfinite(value):
                raise KernelValueError(f"NumericBounds.{label} must be finite, got {value!r}")
        require_bool(self.minimum_inclusive, "NumericBounds.minimum_inclusive")
        require_bool(self.maximum_inclusive, "NumericBounds.maximum_inclusive")
        if self.minimum is None and self.maximum is None:
            raise KernelValueError("NumericBounds must constrain at least one end")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise KernelValueError(
                f"NumericBounds.minimum ({self.minimum}) must be <= maximum ({self.maximum})"
            )

    def contains(self, value: float) -> bool:
        """範囲に入っているか。端の開閉は宣言に従う。"""
        if self.minimum is not None:
            if value < self.minimum or (value == self.minimum and not self.minimum_inclusive):
                return False
        if self.maximum is not None:
            if value > self.maximum or (value == self.maximum and not self.maximum_inclusive):
                return False
        return True

    def __str__(self) -> str:
        low = "-inf" if self.minimum is None else str(self.minimum)
        high = "+inf" if self.maximum is None else str(self.maximum)
        return (
            f"{'[' if self.minimum_inclusive else '('}{low}, "
            f"{high}{']' if self.maximum_inclusive else ')'}"
        )


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """パラメータの型・単位・範囲・許可値・既定値（D04 §7）。"""

    value_type: ParameterType
    unit: UnitRef | None = None
    bounds: NumericBounds | None = None
    allowed_values: tuple[ParameterValue, ...] | None = None
    default: ParameterValue | None = None

    def __post_init__(self) -> None:
        require_instance(self.value_type, ParameterType, "ParameterSpec.value_type")
        if self.unit is not None:
            require_instance(self.unit, UnitRef, "ParameterSpec.unit")
        if self.bounds is not None:
            require_instance(self.bounds, NumericBounds, "ParameterSpec.bounds")
            if self.value_type not in (ParameterType.INT, ParameterType.FLOAT):
                raise KernelValueError(
                    "ParameterSpec.bounds is only meaningful for INT and FLOAT parameters,"
                    f" got {self.value_type}"
                )
        if self.allowed_values is not None:
            require_tuple_of(self.allowed_values, object, "ParameterSpec.allowed_values")
            if not self.allowed_values:
                raise KernelValueError("ParameterSpec.allowed_values must not be empty")
            for index, item in enumerate(self.allowed_values):
                value = require_parameter_value(item, f"ParameterSpec.allowed_values[{index}]")
                if parameter_value_of(value) is not self.value_type:
                    raise KernelValueError(
                        f"ParameterSpec.allowed_values[{index}] must be a {self.value_type.value}"
                        f" value, got {value!r}"
                    )
            # 許可値は集合であり順序に意味がない（D04 §3）。正規化エンコード順に整列する。
            object.__setattr__(
                self,
                "allowed_values",
                normalized_unique(
                    self.allowed_values,
                    key=lambda item: (item.kind, str(item.value)),
                    label="ParameterSpec.allowed_values",
                ),
            )
        if self.default is not None:
            default = require_parameter_value(self.default, "ParameterSpec.default")
            if parameter_value_of(default) is not self.value_type:
                raise KernelValueError(
                    f"ParameterSpec.default must be a {self.value_type.value} value,"
                    f" got {default!r}"
                )


@dataclass(frozen=True, slots=True)
class InputArity:
    """入力に接続できる参照元の数（D04 §4.1）。"""

    min_count: int
    max_count: int | None

    def __post_init__(self) -> None:
        minimum = require_int(self.min_count, "InputArity.min_count")
        if minimum < 1:
            raise KernelValueError(f"InputArity.min_count must be >= 1, got {minimum}")
        if self.max_count is None:
            return
        maximum = require_int(self.max_count, "InputArity.max_count")
        if maximum < minimum:
            raise KernelValueError(
                f"InputArity.max_count ({maximum}) must be >= min_count ({minimum})"
            )

    def allows(self, count: int) -> bool:
        """接続数が許容範囲かどうか。"""
        if count < self.min_count:
            return False
        return self.max_count is None or count <= self.max_count

    def __str__(self) -> str:
        return f"({self.min_count}, {'*' if self.max_count is None else self.max_count})"


@dataclass(frozen=True, slots=True)
class InputSpec:
    """入力の受け口の仕様（D04 §4.1）。"""

    data_type: DataTypeRef
    kind: PortKind
    arity: InputArity
    read_spec: InputReadSpec

    def __post_init__(self) -> None:
        require_registered(self.data_type, "InputSpec.data_type")
        require_instance(self.kind, PortKind, "InputSpec.kind")
        require_instance(self.arity, InputArity, "InputSpec.arity")
        if getattr(self.read_spec, "kind", None) not in _READ_SPEC_KINDS:
            raise KernelValueError(
                f"InputSpec.read_spec must be an InputReadSpec, got {self.read_spec!r}"
            )


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """出力の出し口の仕様（D04 §4.1・§10.4・§11.1）。

    `reference_schema` は取引機会の根拠値（突破水準など）の名前と型の宣言で、取引機会を
    出す出力以外では空でなければならない。`retrigger_mode` も同じく取引機会を出す出力だけ
    が持つ。どちらも「取引機会の出力かどうか」の検査はコンパイラが行う（D04 §12 #6b）。
    ここでは `reference_schema` の各値が登録済みのデータ型であることだけを見る。
    """

    data_type: DataTypeRef
    kind: PortKind
    reference_schema: Mapping[str, DataTypeRef] = field(default_factory=dict)
    retrigger_mode: RetriggerMode | None = None

    def __post_init__(self) -> None:
        require_registered(self.data_type, "OutputSpec.data_type")
        require_instance(self.kind, PortKind, "OutputSpec.kind")
        if not isinstance(self.reference_schema, Mapping):
            raise KernelValueError(
                f"OutputSpec.reference_schema must be a Mapping, got {self.reference_schema!r}"
            )
        for name, data_type in self.reference_schema.items():
            require_identifier(name, "OutputSpec.reference_schema key")
            require_registered(data_type, f"OutputSpec.reference_schema[{name!r}]")
        object.__setattr__(self, "reference_schema", freeze_mapping(dict(self.reference_schema)))
        if self.retrigger_mode is not None:
            require_instance(self.retrigger_mode, RetriggerMode, "OutputSpec.retrigger_mode")


@dataclass(frozen=True, slots=True)
class InputBinding:
    """入力に接続する参照元の列（D04 §4.1）。

    **`sources` は並べ替えない**（D04 §3 の表）。可変個数入力は「型付き参照の列」であり、
    並びを部品実装が参照しうるためである。読み取り条件は `InputSpec` だけが持ち、ここで
    上書きしない。
    """

    sources: tuple[InputSourceRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.sources, tuple):
            raise KernelValueError("InputBinding.sources must be a tuple")
        if not self.sources:
            raise KernelValueError("InputBinding.sources must not be empty")
        for index, source in enumerate(self.sources):
            if not isinstance(source, (OutputRef, MarketDataRef, RuntimeInputRef)):
                raise KernelValueError(
                    f"InputBinding.sources[{index}] must be an InputSourceRef, got {source!r}"
                )
