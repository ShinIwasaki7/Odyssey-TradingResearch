"""`odyssey_fx.common.money` の単体テスト（D02 §4・§11）。"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EvidenceId
from odyssey_fx.common.money import (
    KERNEL_DECIMAL_CONTEXT,
    ConversionRate,
    CurrencyCode,
    FloatConversion,
    Money,
    Price,
    PriceOffset,
    Quantity,
    RoundingDirection,
    convert,
    decimal_from_int,
    decimal_from_str,
    price_from_float,
)
from odyssey_fx.common.refs import EvidenceRef
from odyssey_fx.common.time import UtcTime

D = decimal_from_str
JPY = CurrencyCode("JPY")
USD = CurrencyCode("USD")


# --- Decimal コンテキストと構築 ---------------------------------------------


def test_kernel_context_matches_the_design() -> None:
    assert KERNEL_DECIMAL_CONTEXT.prec == 28
    assert KERNEL_DECIMAL_CONTEXT.rounding == "ROUND_HALF_EVEN"


@pytest.mark.parametrize("text", ["1", "-1.5", "0.001", "1e-5", "+2.", ".5", "1E+10"])
def test_decimal_from_str_accepts_decimal_literals(text: str) -> None:
    assert decimal_from_str(text).is_finite()


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-inf", "sNaN", "", "abc", "1_000", "0x10"])
def test_decimal_from_str_rejects_non_finite_and_malformed_literals(text: str) -> None:
    with pytest.raises(KernelValueError, match="decimal literal"):
        decimal_from_str(text)


def test_decimal_from_str_rejects_non_strings() -> None:
    with pytest.raises(KernelValueError, match="requires a str"):
        decimal_from_str(1.5)  # type: ignore[arg-type]


def test_decimal_from_int_accepts_ints_and_rejects_bools() -> None:
    assert decimal_from_int(1000) == Decimal(1000)
    with pytest.raises(KernelValueError, match="requires an int"):
        decimal_from_int(True)


# --- CurrencyCode -----------------------------------------------------------


def test_currency_code_accepts_three_uppercase_letters() -> None:
    assert str(CurrencyCode("USD")) == "USD"


@pytest.mark.parametrize("code", ["usd", "US", "USDX", "US1", ""])
def test_currency_code_rejects_invalid_codes(code: str) -> None:
    with pytest.raises(KernelValueError, match="CurrencyCode"):
        CurrencyCode(code)


# --- Price / PriceOffset ----------------------------------------------------


@pytest.mark.parametrize("text", ["0", "-1"])
def test_price_must_be_positive(text: str) -> None:
    with pytest.raises(KernelValueError, match="> 0"):
        Price(D(text))


def test_price_rejects_non_finite_values() -> None:
    with pytest.raises(KernelValueError, match="finite"):
        Price(Decimal("Infinity"))


def test_price_offset_allows_any_sign() -> None:
    assert PriceOffset(D("-0.5")).value == D("-0.5")
    assert PriceOffset(D("0")).value == D("0")


def test_price_minus_price_is_an_offset() -> None:
    difference = Price(D("150.500")) - Price(D("150.200"))
    assert isinstance(difference, PriceOffset)
    assert difference.value == D("0.300")


def test_price_plus_and_minus_offset_stays_a_price() -> None:
    base = Price(D("150.000"))
    assert (base + PriceOffset(D("0.5"))).value == D("150.5")
    assert (base - PriceOffset(D("0.5"))).value == D("149.5")
    assert (PriceOffset(D("0.5")) + base).value == D("150.5")


def test_price_plus_offset_rejects_a_non_positive_result() -> None:
    with pytest.raises(KernelValueError, match="stay > 0"):
        Price(D("1.0")) + PriceOffset(D("-1.0"))
    with pytest.raises(KernelValueError, match="stay > 0"):
        Price(D("1.0")) - PriceOffset(D("2.0"))


def test_price_plus_price_is_not_defined() -> None:
    with pytest.raises(TypeError):
        _ = Price(D("1")) + Price(D("2"))  # type: ignore[operator]


def test_price_does_not_compare_with_decimal() -> None:
    with pytest.raises(TypeError):
        _ = Price(D("150")) < D("151")  # type: ignore[operator]


def test_price_does_not_compare_with_price_offset() -> None:
    with pytest.raises(TypeError):
        _ = Price(D("150")) < PriceOffset(D("151"))  # type: ignore[operator]


def test_price_comparisons_within_the_type() -> None:
    low, high = Price(D("1")), Price(D("2"))
    assert low < high and low <= high and high > low and high >= low


def test_price_offset_arithmetic() -> None:
    assert (PriceOffset(D("0.5")) + PriceOffset(D("0.25"))).value == D("0.75")
    assert (PriceOffset(D("0.5")) - PriceOffset(D("0.25"))).value == D("0.25")
    assert (PriceOffset(D("0.5")) * D("2")).value == D("1.0")
    assert (D("2") * PriceOffset(D("0.5"))).value == D("1.0")
    assert (-PriceOffset(D("0.5"))).value == D("-0.5")
    assert abs(PriceOffset(D("-0.5"))).value == D("0.5")
    assert PriceOffset(D("-1")) < PriceOffset(D("1"))


def test_offset_plus_price_falls_back_to_price_addition() -> None:
    """`PriceOffset + Price` は `Price.__radd__` に解決され、`Price` になる（D02 §4.3）。"""
    result = PriceOffset(D("0.5")) + Price(D("150"))
    assert isinstance(result, Price)
    assert result.value == D("150.5")


def test_price_offset_rejects_subtracting_a_price() -> None:
    with pytest.raises(TypeError):
        _ = PriceOffset(D("0.5")) - Price(D("150"))  # type: ignore[operator]


# --- 丸め -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "direction", "expected"),
    [
        ("150.1234", RoundingDirection.DOWN, "150.123"),
        ("150.1234", RoundingDirection.UP, "150.124"),
        ("150.1235", RoundingDirection.NEAREST_HALF_EVEN, "150.124"),
        ("150.1245", RoundingDirection.NEAREST_HALF_EVEN, "150.124"),
        ("150.123", RoundingDirection.DOWN, "150.123"),
    ],
)
def test_round_to_tick_directions(raw: str, direction: RoundingDirection, expected: str) -> None:
    rounded = Price(D(raw)).round_to_tick(D("0.001"), direction)
    assert rounded.value == D(expected)


def test_round_to_tick_rejects_a_non_positive_tick() -> None:
    with pytest.raises(KernelValueError, match="step must be > 0"):
        Price(D("150")).round_to_tick(D("0"), RoundingDirection.DOWN)


def test_round_to_tick_rejects_an_invalid_direction() -> None:
    with pytest.raises(KernelValueError, match="RoundingDirection"):
        Price(D("150")).round_to_tick(D("0.001"), "DOWN")  # type: ignore[arg-type]


def test_round_to_tick_rejects_a_result_at_or_below_zero() -> None:
    with pytest.raises(KernelValueError, match="<= 0"):
        Price(D("0.0004")).round_to_tick(D("0.001"), RoundingDirection.DOWN)


def test_is_on_tick() -> None:
    assert Price(D("150.123")).is_on_tick(D("0.001"))
    assert not Price(D("150.1234")).is_on_tick(D("0.001"))
    with pytest.raises(KernelValueError, match="tick must be > 0"):
        Price(D("150")).is_on_tick(D("0"))


# --- Quantity ---------------------------------------------------------------


@pytest.mark.parametrize("text", ["0", "-1000"])
def test_quantity_must_be_positive(text: str) -> None:
    with pytest.raises(KernelValueError, match="> 0"):
        Quantity(D(text))


def test_round_down_to_step_truncates() -> None:
    result = Quantity(D("2500")).round_down_to_step(D("1000"))
    assert result is not None and result.units == D("2000")


def test_round_down_to_step_returns_none_below_one_step() -> None:
    assert Quantity(D("999")).round_down_to_step(D("1000")) is None


def test_quantity_comparisons() -> None:
    small, large = Quantity(D("1000")), Quantity(D("2000"))
    assert small < large and small <= large and large > small and large >= small


# --- Money ------------------------------------------------------------------


def test_money_addition_requires_the_same_currency() -> None:
    assert (Money(D("100"), JPY) + Money(D("50"), JPY)).amount == D("150")
    with pytest.raises(KernelValueError, match="cannot add"):
        Money(D("100"), JPY) + Money(D("50"), USD)


def test_money_subtraction_requires_the_same_currency() -> None:
    assert (Money(D("100"), JPY) - Money(D("50"), JPY)).amount == D("50")
    with pytest.raises(KernelValueError, match="cannot subtract"):
        Money(D("100"), JPY) - Money(D("50"), USD)


def test_money_comparison_requires_the_same_currency() -> None:
    assert Money(D("50"), JPY) < Money(D("100"), JPY)
    with pytest.raises(KernelValueError, match="cannot compare"):
        _ = Money(D("50"), JPY) < Money(D("100"), USD)


def test_money_scaling() -> None:
    assert (Money(D("100"), JPY) * D("1.5")).amount == D("150.0")
    assert (D("2") * Money(D("100"), JPY)).amount == D("200")
    assert (Money(D("100"), JPY) / D("4")).amount == D("25")
    with pytest.raises(KernelValueError, match="divide Money by zero"):
        Money(D("100"), JPY) / D("0")


def test_money_sign_helpers_and_str() -> None:
    assert (-Money(D("100"), JPY)).amount == D("-100")
    assert abs(Money(D("-100"), JPY)).amount == D("100")
    assert str(Money(D("-100"), JPY)) == "-100 JPY"


def test_money_allows_a_negative_amount() -> None:
    assert Money(D("-100"), JPY).amount == D("-100")


def test_money_round_to_keeps_the_currency() -> None:
    rounded = Money(D("100.567"), JPY).round_to(D("0.01"), RoundingDirection.DOWN)
    assert rounded == Money(D("100.56"), JPY)


def test_money_rejects_a_non_currency_code() -> None:
    with pytest.raises(KernelValueError, match="CurrencyCode"):
        Money(D("100"), "JPY")  # type: ignore[arg-type]


# --- ConversionRate ---------------------------------------------------------


def _rate(value: str = "150.0") -> ConversionRate:
    return ConversionRate(
        from_currency=USD,
        to_currency=JPY,
        rate=D(value),
        observed_at=UtcTime.from_components(2026, 3, 1),
    )


def test_convert_applies_the_rate() -> None:
    assert convert(Money(D("100"), USD), _rate()) == Money(D("15000.0"), JPY)


def test_convert_rejects_a_mismatched_currency() -> None:
    with pytest.raises(KernelValueError, match="conversion rate is"):
        convert(Money(D("100"), JPY), _rate())


def test_conversion_rate_must_be_positive() -> None:
    with pytest.raises(KernelValueError, match="> 0"):
        ConversionRate(USD, JPY, D("0"), UtcTime.from_components(2026, 3, 1))


def test_conversion_rate_accepts_an_evidence_reference() -> None:
    rate = ConversionRate(
        USD,
        JPY,
        D("150"),
        UtcTime.from_components(2026, 3, 1),
        EvidenceRef(EvidenceId(1)),
    )
    assert rate.evidence == EvidenceRef(EvidenceId(1))


def test_conversion_rate_rejects_a_bad_evidence_reference() -> None:
    with pytest.raises(KernelValueError, match="EvidenceRef"):
        ConversionRate(
            USD,
            JPY,
            D("150"),
            UtcTime.from_components(2026, 3, 1),
            EvidenceId(1),  # type: ignore[arg-type]
        )


# --- float からの変換（D02 §4.6）-------------------------------------------


def test_price_from_float_records_the_exact_decimal() -> None:
    conversion = price_from_float(
        150.12345, tick=D("0.001"), direction=RoundingDirection.NEAREST_HALF_EVEN
    )
    assert conversion.raw == 150.12345
    assert conversion.exact == Decimal(repr(150.12345))
    assert conversion.result.value == D("150.123")
    assert conversion.tick == D("0.001")
    assert conversion.direction is RoundingDirection.NEAREST_HALF_EVEN


def test_price_from_float_avoids_binary_float_error() -> None:
    """`Decimal(0.1)` なら誤差が残るが、最短往復表現を経由するので残らない。"""
    conversion = price_from_float(0.1, tick=D("0.1"), direction=RoundingDirection.DOWN)
    assert conversion.exact == D("0.1")
    assert conversion.result.value == D("0.1")


@pytest.mark.parametrize("raw", [math.nan, math.inf, -math.inf])
def test_price_from_float_rejects_non_finite_values(raw: float) -> None:
    with pytest.raises(KernelValueError, match="finite"):
        price_from_float(raw, tick=D("0.001"), direction=RoundingDirection.DOWN)


def test_price_from_float_rejects_a_non_positive_result() -> None:
    with pytest.raises(KernelValueError, match="<= 0"):
        price_from_float(0.0004, tick=D("0.001"), direction=RoundingDirection.DOWN)


def test_price_from_float_rejects_non_numbers() -> None:
    with pytest.raises(KernelValueError, match="requires a float"):
        price_from_float("1.5", tick=D("0.001"), direction=RoundingDirection.DOWN)  # type: ignore[arg-type]


def test_float_conversion_validates_its_fields() -> None:
    valid = price_from_float(150.0, tick=D("0.001"), direction=RoundingDirection.DOWN)
    with pytest.raises(KernelValueError, match="raw"):
        FloatConversion(
            raw=math.nan,
            exact=valid.exact,
            tick=valid.tick,
            direction=valid.direction,
            result=valid.result,
        )
    with pytest.raises(KernelValueError, match="tick"):
        FloatConversion(
            raw=valid.raw,
            exact=valid.exact,
            tick=D("0"),
            direction=valid.direction,
            result=valid.result,
        )
