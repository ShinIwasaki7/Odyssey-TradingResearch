"""通貨・価格・数量・金額（D02 §4）。

台帳・丸め・予約計算は `Decimal`、Feature 計算は `float`（ADR-0012）。`Decimal` は文字列
または整数からのみ構築し、`Decimal(float)` は使わない。float から注文価格へ移すのは
`price_from_float`（D02 §4.6）だけで、丸め前の float・丸め規則・丸め後の価格を
`FloatConversion` として根拠記録に残せる形にする。

`Decimal(` の呼び出しは本モジュールと `canonical.py` に限る（D02 §4.6、
`tests/architecture/test_decimal_construction.py` が機械検査する）。他モジュールは
`decimal_from_str` / `decimal_from_int` を使う。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from enum import Enum
from typing import Any, Final, overload

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import EvidenceRef
from odyssey_fx.common.time import UtcTime

__all__ = [
    "KERNEL_DECIMAL_CONTEXT",
    "ConversionRate",
    "CurrencyCode",
    "FloatConversion",
    "Money",
    "Price",
    "PriceOffset",
    "Quantity",
    "RoundingDirection",
    "convert",
    "decimal_from_int",
    "decimal_from_str",
    "price_from_float",
]

#: カーネルの算術に使う Decimal コンテキスト（D02 §4.1）。
#: プロセス全体のコンテキスト（`decimal.getcontext()`）は変更せず、
#: 算術のたびに `with localcontext(KERNEL_DECIMAL_CONTEXT)` で持ち込む。
KERNEL_DECIMAL_CONTEXT: Final = Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

#: `CurrencyCode.code` に許す字種（D02 §4.2）。
_CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$")

#: `decimal_from_str` が受け付ける十進リテラル（指数表記を含む。特殊値は拒否）。
_DECIMAL_LITERAL_PATTERN: Final = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


class RoundingDirection(Enum):
    """丸めの方向（D02 §4.3）。

    どちらへ丸めるか（SL は買いなら DOWN、売りなら UP 等）の選択は執行ポリシー（D06）の
    責務であり、本モジュールは機構だけを提供する。
    """

    DOWN = "DOWN"
    UP = "UP"
    NEAREST_HALF_EVEN = "NEAREST_HALF_EVEN"


#: 丸め方向と `decimal` の丸めモードの対応。
#: `ROUND_FLOOR` / `ROUND_CEILING` は負の値でも数直線上の下側／上側へ丸める。
_ROUNDING_MODES: Final = {
    RoundingDirection.DOWN: ROUND_FLOOR,
    RoundingDirection.UP: ROUND_CEILING,
    RoundingDirection.NEAREST_HALF_EVEN: ROUND_HALF_EVEN,
}


# --- Decimal の構築 ---------------------------------------------------------


def decimal_from_str(text: str) -> Decimal:
    """十進リテラルから `Decimal` を作る（D02 §4.1・§4.6）。

    `NaN` / `Infinity` と、十進リテラル以外の文字列は拒否する。float を文字列化して
    渡す経路を塞ぐため、引数は `str` に限る。
    """
    if not isinstance(text, str):
        raise KernelValueError(f"decimal_from_str requires a str, got {type(text).__name__}")
    if not _DECIMAL_LITERAL_PATTERN.match(text):
        raise KernelValueError(f"invalid decimal literal: {text!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - 正規表現で弾かれる
        raise KernelValueError(f"invalid decimal literal: {text!r}") from exc
    return value


def decimal_from_int(value: int) -> Decimal:
    """整数から `Decimal` を作る（D02 §4.6）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"decimal_from_int requires an int, got {type(value).__name__}")
    return Decimal(value)


def _require_finite_decimal(value: Any, label: str) -> Decimal:
    """`Decimal` であり有限（`NaN` / `Infinity` でない）ことを確かめる（D02 §4.1）。"""
    if not isinstance(value, Decimal):
        raise KernelValueError(f"{label} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise KernelValueError(f"{label} must be finite, got {value}")
    return value


def _quantize_to_step(value: Decimal, step: Decimal, direction: RoundingDirection) -> Decimal:
    """`step` の整数倍へ丸める。`step` は有限かつ正であること。"""
    _require_finite_decimal(step, "step")
    if step <= 0:
        raise KernelValueError(f"step must be > 0, got {step}")
    if not isinstance(direction, RoundingDirection):
        raise KernelValueError(f"direction must be a RoundingDirection, got {direction!r}")
    with localcontext(KERNEL_DECIMAL_CONTEXT):
        multiples = (value / step).to_integral_value(rounding=_ROUNDING_MODES[direction])
        return multiples * step


# --- 通貨 -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurrencyCode:
    """ISO 4217 の3文字通貨コード（D02 §4.2）。"""

    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _CURRENCY_PATTERN.match(self.code):
            raise KernelValueError(f"CurrencyCode must match ^[A-Z]{{3}}$, got {self.code!r}")

    def __str__(self) -> str:
        return self.code

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3: `CurrencyCode` は `__str__`）。"""
        return self.code


# --- 価格と価格差 -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceOffset:
    """価格の差（D02 §4.3）。符号は任意で、価格差・許容不利約定幅 Δ・spread に使う。"""

    value: Decimal

    def __post_init__(self) -> None:
        _require_finite_decimal(self.value, "PriceOffset.value")

    def __str__(self) -> str:
        return str(self.value)

    def __add__(self, other: PriceOffset) -> PriceOffset:
        if not isinstance(other, PriceOffset):
            return NotImplemented
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            return PriceOffset(self.value + other.value)

    def __sub__(self, other: PriceOffset) -> PriceOffset:
        if not isinstance(other, PriceOffset):
            return NotImplemented
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            return PriceOffset(self.value - other.value)

    def __neg__(self) -> PriceOffset:
        return PriceOffset(-self.value)

    def __abs__(self) -> PriceOffset:
        return PriceOffset(abs(self.value))

    def __mul__(self, factor: Decimal) -> PriceOffset:
        if not isinstance(factor, Decimal):
            return NotImplemented
        _require_finite_decimal(factor, "factor")
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            return PriceOffset(self.value * factor)

    def __rmul__(self, factor: Decimal) -> PriceOffset:
        return self.__mul__(factor)

    def __lt__(self, other: PriceOffset) -> bool:
        if not isinstance(other, PriceOffset):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: PriceOffset) -> bool:
        if not isinstance(other, PriceOffset):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other: PriceOffset) -> bool:
        if not isinstance(other, PriceOffset):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other: PriceOffset) -> bool:
        if not isinstance(other, PriceOffset):
            return NotImplemented
        return self.value >= other.value


@dataclass(frozen=True, slots=True)
class Price:
    """提示価格・約定価格・SL/TP 水準（D02 §4.3）。常に正。

    `Price + Price` は意味を持たないので定義しない。価格差は `PriceOffset` で表す。
    """

    value: Decimal

    def __post_init__(self) -> None:
        _require_finite_decimal(self.value, "Price.value")
        if self.value <= 0:
            raise KernelValueError(f"Price.value must be > 0, got {self.value}")

    def __str__(self) -> str:
        return str(self.value)

    def __add__(self, other: PriceOffset) -> Price:
        if not isinstance(other, PriceOffset):
            return NotImplemented
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            result = self.value + other.value
        if result <= 0:
            raise KernelValueError(f"Price + PriceOffset must stay > 0, got {result}")
        return Price(result)

    def __radd__(self, other: PriceOffset) -> Price:
        return self.__add__(other)

    @overload
    def __sub__(self, other: Price) -> PriceOffset: ...

    @overload
    def __sub__(self, other: PriceOffset) -> Price: ...

    def __sub__(self, other: Price | PriceOffset) -> PriceOffset | Price:
        """`Price - Price` は価格差、`Price - PriceOffset` は価格（D02 §4.3）。"""
        if isinstance(other, Price):
            with localcontext(KERNEL_DECIMAL_CONTEXT):
                return PriceOffset(self.value - other.value)
        if isinstance(other, PriceOffset):
            with localcontext(KERNEL_DECIMAL_CONTEXT):
                result = self.value - other.value
            if result <= 0:
                raise KernelValueError(f"Price - PriceOffset must stay > 0, got {result}")
            return Price(result)
        return NotImplemented

    def __lt__(self, other: Price) -> bool:
        if not isinstance(other, Price):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: Price) -> bool:
        if not isinstance(other, Price):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other: Price) -> bool:
        if not isinstance(other, Price):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other: Price) -> bool:
        if not isinstance(other, Price):
            return NotImplemented
        return self.value >= other.value

    def round_to_tick(self, tick: Decimal, direction: RoundingDirection) -> Price:
        """価格刻み `tick` の整数倍へ丸める（D02 §4.3）。

        方向の選択（SL は買いなら DOWN、売りなら UP 等）は D06 の責務であり、本メソッドは
        機構だけを提供する。丸めた結果が 0 以下になる場合は `KernelValueError`。
        """
        rounded = _quantize_to_step(self.value, tick, direction)
        if rounded <= 0:
            raise KernelValueError(f"rounding {self.value} to tick {tick} left {rounded} (<= 0)")
        return Price(rounded)

    def is_on_tick(self, tick: Decimal) -> bool:
        """価格刻み `tick` の整数倍に載っているかを判定する（D02 §4.3）。"""
        _require_finite_decimal(tick, "tick")
        if tick <= 0:
            raise KernelValueError(f"tick must be > 0, got {tick}")
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            return self.value % tick == 0


# --- 数量 -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Quantity:
    """基軸通貨の単位数（D02 §4.4）。常に正。

    「建玉なし」は数量 0 ではなく値の不在（`None`）で表す。
    """

    units: Decimal

    def __post_init__(self) -> None:
        _require_finite_decimal(self.units, "Quantity.units")
        if self.units <= 0:
            raise KernelValueError(f"Quantity.units must be > 0, got {self.units}")

    def __str__(self) -> str:
        return str(self.units)

    def round_down_to_step(self, step: Decimal) -> Quantity | None:
        """数量刻み `step` の整数倍へ切り下げる（D02 §4.4）。

        `step` 未満になれば `None` を返す。切り上げはしない。
        """
        rounded = _quantize_to_step(self.units, step, RoundingDirection.DOWN)
        if rounded <= 0:
            return None
        return Quantity(rounded)

    def __lt__(self, other: Quantity) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.units < other.units

    def __le__(self, other: Quantity) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.units <= other.units

    def __gt__(self, other: Quantity) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.units > other.units

    def __ge__(self, other: Quantity) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.units >= other.units


# --- 金額 -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Money:
    """通貨建ての金額（D02 §4.5）。符号は任意（損益・手数料を表せる）。"""

    amount: Decimal
    currency: CurrencyCode

    def __post_init__(self) -> None:
        _require_finite_decimal(self.amount, "Money.amount")
        if not isinstance(self.currency, CurrencyCode):
            raise KernelValueError("Money.currency must be a CurrencyCode")

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"

    def _require_same_currency(self, other: Money, operation: str) -> None:
        if self.currency != other.currency:
            raise KernelValueError(
                f"cannot {operation} {self.currency} and {other.currency};"
                " convert explicitly with a ConversionRate"
            )

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other, "add")
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other, "subtract")
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    def __mul__(self, factor: Decimal) -> Money:
        if not isinstance(factor, Decimal):
            return NotImplemented
        _require_finite_decimal(factor, "factor")
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            return Money(self.amount * factor, self.currency)

    def __rmul__(self, factor: Decimal) -> Money:
        return self.__mul__(factor)

    def __truediv__(self, divisor: Decimal) -> Money:
        if not isinstance(divisor, Decimal):
            return NotImplemented
        _require_finite_decimal(divisor, "divisor")
        if divisor == 0:
            raise KernelValueError("cannot divide Money by zero")
        with localcontext(KERNEL_DECIMAL_CONTEXT):
            return Money(self.amount / divisor, self.currency)

    def _compare(self, other: Money, operation: str) -> None:
        if self.currency != other.currency:
            raise KernelValueError(f"cannot {operation} {self.currency} and {other.currency}")

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._compare(other, "compare")
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._compare(other, "compare")
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._compare(other, "compare")
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._compare(other, "compare")
        return self.amount >= other.amount

    def round_to(self, step: Decimal, direction: RoundingDirection) -> Money:
        """金額を `step` の整数倍へ丸める（D02 §4.5）。"""
        return Money(_quantize_to_step(self.amount, step, direction), self.currency)


@dataclass(frozen=True, slots=True)
class ConversionRate:
    """通貨換算率とその観測時点（D02 §4.5）。

    換算率の取得は D03 / D06 の責務であり、`common` は値と適用（`convert`）だけを持つ。
    """

    from_currency: CurrencyCode
    to_currency: CurrencyCode
    rate: Decimal
    observed_at: UtcTime
    evidence: EvidenceRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.from_currency, CurrencyCode):
            raise KernelValueError("ConversionRate.from_currency must be a CurrencyCode")
        if not isinstance(self.to_currency, CurrencyCode):
            raise KernelValueError("ConversionRate.to_currency must be a CurrencyCode")
        _require_finite_decimal(self.rate, "ConversionRate.rate")
        if self.rate <= 0:
            raise KernelValueError(f"ConversionRate.rate must be > 0, got {self.rate}")
        if not isinstance(self.observed_at, UtcTime):
            raise KernelValueError("ConversionRate.observed_at must be a UtcTime")
        if self.evidence is not None and not isinstance(self.evidence, EvidenceRef):
            raise KernelValueError("ConversionRate.evidence must be an EvidenceRef or None")


def convert(money: Money, rate: ConversionRate) -> Money:
    """`rate` を適用して別通貨の `Money` にする（D02 §4.5）。

    `money.currency` が `rate.from_currency` と一致しなければ `KernelValueError`。
    """
    if not isinstance(money, Money):
        raise KernelValueError("convert requires a Money")
    if not isinstance(rate, ConversionRate):
        raise KernelValueError("convert requires a ConversionRate")
    if money.currency != rate.from_currency:
        raise KernelValueError(
            f"conversion rate is {rate.from_currency}->{rate.to_currency},"
            f" but the amount is in {money.currency}"
        )
    with localcontext(KERNEL_DECIMAL_CONTEXT):
        return Money(money.amount * rate.rate, rate.to_currency)


# --- float からの変換（D02 §4.6、ADR-0012）----------------------------------


@dataclass(frozen=True, slots=True)
class FloatConversion:
    """float から価格への変換の根拠記録（D02 §4.6）。

    Feature 計算（float）の結果を注文価格・水準へ移した経緯を、丸め前の float・厳密化した
    `Decimal`・適用した丸め規則・丸め後の価格として残す。`EvidenceRef` の対象として保存
    できる形にする。
    """

    raw: float
    exact: Decimal
    tick: Decimal
    direction: RoundingDirection
    result: Price

    def __post_init__(self) -> None:
        if isinstance(self.raw, bool) or not isinstance(self.raw, float):
            raise KernelValueError(f"FloatConversion.raw must be a float, got {self.raw!r}")
        if not math.isfinite(self.raw):
            raise KernelValueError(f"FloatConversion.raw must be finite, got {self.raw!r}")
        _require_finite_decimal(self.exact, "FloatConversion.exact")
        _require_finite_decimal(self.tick, "FloatConversion.tick")
        if self.tick <= 0:
            raise KernelValueError(f"FloatConversion.tick must be > 0, got {self.tick}")
        if not isinstance(self.direction, RoundingDirection):
            raise KernelValueError("FloatConversion.direction must be a RoundingDirection")
        if not isinstance(self.result, Price):
            raise KernelValueError("FloatConversion.result must be a Price")


def price_from_float(raw: float, *, tick: Decimal, direction: RoundingDirection) -> FloatConversion:
    """Feature 計算の float を価格刻みへ丸めて `Price` にする（D02 §4.6、ADR-0012）。

    `Decimal(float)` は使わず、float の最短往復表現（`repr`）を経由して `Decimal` にする。
    `raw` が `nan` / `inf` の場合は `KernelValueError`。
    """
    if isinstance(raw, bool) or not isinstance(raw, (float, int)):
        raise KernelValueError(f"price_from_float requires a float, got {type(raw).__name__}")
    raw_float = float(raw)
    if not math.isfinite(raw_float):
        raise KernelValueError(f"price_from_float requires a finite float, got {raw_float!r}")
    # ADR-0012: float は直接 Decimal 化せず、最短往復表現の文字列を経由する。
    exact = Decimal(repr(raw_float))
    rounded = _quantize_to_step(exact, tick, direction)
    if rounded <= 0:
        raise KernelValueError(
            f"rounding {raw_float!r} to tick {tick} ({direction.value}) left {rounded} (<= 0)"
        )
    return FloatConversion(
        raw=raw_float,
        exact=exact,
        tick=tick,
        direction=direction,
        result=Price(rounded),
    )
