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
    "kernel_context",
    "price_from_float",
]

#: カーネルの算術の設定（D02 §4.1）。ここが唯一の正本で、以後変更されない。
_KERNEL_PRECISION: Final = 28
_KERNEL_ROUNDING: Final = ROUND_HALF_EVEN
_KERNEL_TRAPS: Final = (InvalidOperation, DivisionByZero, Overflow)


def kernel_context() -> Context:
    """カーネルの算術に使う Decimal コンテキストを新しく作る（D02 §4.1）。

    `Context` は可変オブジェクトなので、1つを使い回して公開すると、誰かが `.prec` を
    書き換えただけで以後のカーネルの計算がすべて変わってしまう。算術のたびに上の不変な
    設定から作り直すことで、外部からの書き換えが計算に影響しないようにする。

    `common` 内で Decimal の算術を行うモジュール（`symbol` など）はこれを使う。
    """
    return Context(
        prec=_KERNEL_PRECISION,
        rounding=_KERNEL_ROUNDING,
        traps=list(_KERNEL_TRAPS),
    )


#: カーネルの算術に使う Decimal コンテキスト（D02 §4.1 が名前を定める公開値）。
#: **設定の参照用**であり、実際の算術には使わない（この値を書き換えても計算は変わらない）。
#: プロセス全体のコンテキスト（`decimal.getcontext()`）も変更せず、算術のたびに
#: `with localcontext(kernel_context())` で新しいコンテキストを持ち込む。
KERNEL_DECIMAL_CONTEXT: Final = kernel_context()

#: `CurrencyCode.code` に許す字種（D02 §4.2）。照合は `fullmatch`（`$` は末尾の改行を許すため）。
_CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$")

#: `decimal_from_str` が受け付ける十進リテラル（指数表記を含む。特殊値は拒否）。
#: 照合は `fullmatch`（`$` は末尾の改行を許すため）。
_DECIMAL_LITERAL_PATTERN: Final = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


class RoundingDirection(Enum):
    """丸めの方向（D02 §4.3）。

    どちらへ丸めるか（SL は買いなら DOWN、売りなら UP 等）の選択は執行ポリシー（D06）の
    責務であり、本モジュールは機構だけを提供する。
    """

    DOWN = "DOWN"
    UP = "UP"
    NEAREST_HALF_EVEN = "NEAREST_HALF_EVEN"


# `decimal` の丸めモード（`ROUND_FLOOR` / `ROUND_CEILING`）との対応表は持たない。
# 刻みへの丸めは `_quantize_to_step` が整数演算で行うため、コンテキストの丸めモードに
# 依存しない（依存させると精度を超えた値で方向が失われる）。


# --- Decimal の構築 ---------------------------------------------------------


def decimal_from_str(text: str) -> Decimal:
    """十進リテラルから `Decimal` を作る（D02 §4.1・§4.6）。

    `NaN` / `Infinity` と、十進リテラル以外の文字列は拒否する。float を文字列化して
    渡す経路を塞ぐため、引数は `str` に限る。
    """
    if not isinstance(text, str):
        raise KernelValueError(f"decimal_from_str requires a str, got {type(text).__name__}")
    if not _DECIMAL_LITERAL_PATTERN.fullmatch(text):
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


def _as_scaled_int(value: Decimal, exponent: int) -> int:
    """`value` を「指数 `exponent` を単位とする整数」として表す。

    `value` の指数は `exponent` 以上でなければならない（呼び出し側が小さいほうの指数に
    そろえる）。整数演算なので、桁数がいくら多くても丸めは起きない。
    """
    sign, digits, value_exponent = value.as_tuple()
    if not isinstance(value_exponent, int):  # pragma: no cover - 呼び出し前に有限性を検査済み
        raise KernelValueError(f"cannot scale a special Decimal: {value!r}")
    mantissa = 0
    for digit in digits:
        mantissa = mantissa * 10 + digit
    if sign:
        mantissa = -mantissa
    scale: int = 10 ** (value_exponent - exponent)
    return mantissa * scale


def _quantize_to_step(value: Decimal, step: Decimal, direction: RoundingDirection) -> Decimal:
    """`step` の整数倍へ丸める（D02 §4.3・§4.4）。`step` は有限かつ正であること。

    十進コンテキストの精度に依存しない**厳密な整数演算**で計算する。`value / step` を
    コンテキスト内で先に求めると、有効桁が精度（28桁）を超える値では商が先に半偶数丸めされ、
    指定した丸め方向が失われる。例えば `0.999…9`（9が40個）を刻み 1 で切り捨てると、商が
    先に 1 へ丸められてしまい 1 が返る（正しくは 0）。

    手順: 両者の指数の小さいほうへそろえて整数（仮数）にし、整数の商と余りから
    切り捨て・切り上げ・半偶数丸めを決め、結果は `Decimal((sign, digits, exponent))` で
    厳密に組み立てる。最後の掛け算もコンテキスト内で行うと再び丸められるため、整数のまま
    行う。
    """
    _require_finite_decimal(value, "value")
    _require_finite_decimal(step, "step")
    if step <= 0:
        raise KernelValueError(f"step must be > 0, got {step}")
    if not isinstance(direction, RoundingDirection):
        raise KernelValueError(f"direction must be a RoundingDirection, got {direction!r}")

    _, _, value_exponent = value.as_tuple()
    _, _, step_exponent = step.as_tuple()
    if not isinstance(value_exponent, int) or not isinstance(step_exponent, int):
        # pragma: no cover - 有限性は上で検査済み
        raise KernelValueError("cannot quantize a special Decimal")

    # 小さいほうの指数へそろえると、両者とも整数（仮数）で表せる。
    exponent = min(value_exponent, step_exponent)
    scaled_value = _as_scaled_int(value, exponent)
    scaled_step = _as_scaled_int(step, exponent)

    # Python の `//` と `%` は除数が正なら常に床（floor）方向なので、負の値でも
    # 数直線の下側へ丸まる（D02 §4.3 が定める DOWN / UP の意味に一致する）。
    quotient, remainder = divmod(scaled_value, scaled_step)
    if remainder:
        if direction is RoundingDirection.UP:
            quotient += 1
        elif direction is RoundingDirection.NEAREST_HALF_EVEN:
            # 余りの2倍と除数を比べる。ちょうど半分なら偶数側へ寄せる。
            doubled = 2 * remainder
            if doubled > scaled_step or (doubled == scaled_step and quotient % 2):
                quotient += 1

    # 結果 = quotient × step。step の仮数を整数で掛け、指数は step の指数をそのまま使う。
    # ここでコンテキスト内の乗算を使うと再び丸められるので、整数のまま組み立てる。
    step_mantissa = _as_scaled_int(step, step_exponent)
    product = quotient * step_mantissa
    sign = 1 if product < 0 else 0
    digits = tuple(int(char) for char in str(abs(product)))
    return Decimal((sign, digits, step_exponent))


# --- 通貨 -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurrencyCode:
    """ISO 4217 の3文字通貨コード（D02 §4.2）。"""

    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _CURRENCY_PATTERN.fullmatch(self.code):
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
        with localcontext(kernel_context()):
            return PriceOffset(self.value + other.value)

    def __sub__(self, other: PriceOffset) -> PriceOffset:
        if not isinstance(other, PriceOffset):
            return NotImplemented
        with localcontext(kernel_context()):
            return PriceOffset(self.value - other.value)

    def __neg__(self) -> PriceOffset:
        # 単項演算子もコンテキストの精度で丸められるため、カーネルのコンテキストで行う。
        with localcontext(kernel_context()):
            return PriceOffset(-self.value)

    def __abs__(self) -> PriceOffset:
        with localcontext(kernel_context()):
            return PriceOffset(abs(self.value))

    def __mul__(self, factor: Decimal) -> PriceOffset:
        if not isinstance(factor, Decimal):
            return NotImplemented
        _require_finite_decimal(factor, "factor")
        with localcontext(kernel_context()):
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
        with localcontext(kernel_context()):
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
            with localcontext(kernel_context()):
                return PriceOffset(self.value - other.value)
        if isinstance(other, PriceOffset):
            with localcontext(kernel_context()):
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
        with localcontext(kernel_context()):
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
        with localcontext(kernel_context()):
            return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other, "subtract")
        with localcontext(kernel_context()):
            return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        # 単項演算子もコンテキストの精度で丸められるため、カーネルのコンテキストで行う。
        with localcontext(kernel_context()):
            return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        with localcontext(kernel_context()):
            return Money(abs(self.amount), self.currency)

    def __mul__(self, factor: Decimal) -> Money:
        if not isinstance(factor, Decimal):
            return NotImplemented
        _require_finite_decimal(factor, "factor")
        with localcontext(kernel_context()):
            return Money(self.amount * factor, self.currency)

    def __rmul__(self, factor: Decimal) -> Money:
        return self.__mul__(factor)

    def __truediv__(self, divisor: Decimal) -> Money:
        if not isinstance(divisor, Decimal):
            return NotImplemented
        _require_finite_decimal(divisor, "divisor")
        if divisor == 0:
            raise KernelValueError("cannot divide Money by zero")
        with localcontext(kernel_context()):
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
    with localcontext(kernel_context()):
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

        # 4つのフィールドは互いに整合していなければならない。根拠記録（`EvidenceRef` の
        # 対象）として保存される以上、後から読んだときに矛盾した記録が存在してはならず、
        # `price_from_float` を通さずに組み立てた値もここで弾く。
        expected_exact = Decimal(repr(self.raw))
        if self.exact != expected_exact:
            raise KernelValueError(
                f"FloatConversion.exact must be Decimal(repr(raw)) = {expected_exact},"
                f" got {self.exact}"
            )
        expected_result = _quantize_to_step(self.exact, self.tick, self.direction)
        if self.result.value != expected_result:
            raise KernelValueError(
                f"FloatConversion.result must be {self.exact} rounded to tick {self.tick}"
                f" ({self.direction.value}) = {expected_result}, got {self.result.value}"
            )


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
