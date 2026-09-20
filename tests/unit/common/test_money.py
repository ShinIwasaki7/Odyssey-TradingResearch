"""`odyssey_fx.common.money` の単体テスト（D02 §4・§11）。"""

from __future__ import annotations

import math
from decimal import Context, Decimal, getcontext, localcontext

import pytest

from odyssey_fx.common import money
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


# --- 精度を超える桁数での丸め方向（Codex レビュー round 3 指摘）-------------
#
# 刻みへの丸めを `value / step` から始めると、有効桁が精度（28桁）を超える値では商が先に
# 半偶数丸めされ、指定した丸め方向が失われる。切り捨てのつもりが切り上がると、損切り水準や
# 発注数量が意図と逆に動くため、整数演算で厳密に計算する。

#: 1 のすぐ下（9が40個）。精度28では 1 に丸められてしまう。
_JUST_BELOW_ONE = "0." + "9" * 40

#: 1 のすぐ上（1 のあと 0 が39個と 1）。精度28では 1 に丸められてしまう。
_JUST_ABOVE_ONE = "1." + "0" * 39 + "1"


def test_round_down_keeps_a_value_just_below_the_grid_below_it() -> None:
    """`1.999…9` の切り捨ては 1。商を先に丸めると 2 になってしまっていた。"""
    rounded = Price(D("1." + "9" * 40)).round_to_tick(D("1"), RoundingDirection.DOWN)
    assert rounded.value == D("1")


def test_a_quantity_just_below_one_step_rounds_down_to_nothing() -> None:
    """刻み未満の数量は `None`。商を先に丸めると 1 単位が生まれてしまっていた。"""
    assert Quantity(D(_JUST_BELOW_ONE)).round_down_to_step(D("1")) is None


def test_round_up_keeps_a_value_just_above_the_grid_above_it() -> None:
    """`1.000…01` の切り上げは 2。商を先に丸めると 1 になってしまっていた。"""
    rounded = Price(D(_JUST_ABOVE_ONE)).round_to_tick(D("1"), RoundingDirection.UP)
    assert rounded.value == D("2")


def test_round_up_does_not_move_a_value_already_on_the_grid() -> None:
    assert Price(D("2")).round_to_tick(D("1"), RoundingDirection.UP).value == D("2")
    assert Price(D("2.000")).round_to_tick(D("0.001"), RoundingDirection.UP).value == D("2.000")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.5", "0"),  # 同点 → 偶数側の 0
        ("1.5", "2"),  # 同点 → 偶数側の 2
        ("2.5", "2"),  # 同点 → 偶数側の 2
        ("3.5", "4"),  # 同点 → 偶数側の 4
        ("0.5000000000000000000000000000001", "1"),  # 半分より上（精度28を超える桁で決まる）
        ("1.4999999999999999999999999999999", "1"),  # 半分より下
    ],
)
def test_half_even_ties_and_near_ties(raw: str, expected: str) -> None:
    """同点は偶数側へ。精度を超える桁で決まる僅差も正しく判定する。"""
    money = Money(D(raw), JPY).round_to(D("1"), RoundingDirection.NEAREST_HALF_EVEN)
    assert money.amount == D(expected)


@pytest.mark.parametrize(
    ("raw", "direction", "expected"),
    [
        ("-100.567", RoundingDirection.DOWN, "-100.57"),  # 数直線の下側
        ("-100.567", RoundingDirection.UP, "-100.56"),  # 数直線の上側
        ("-100.565", RoundingDirection.NEAREST_HALF_EVEN, "-100.56"),
        ("-1." + "9" * 40, RoundingDirection.DOWN, "-2"),
        ("-1." + "9" * 40, RoundingDirection.UP, "-1"),
    ],
)
def test_negative_amounts_keep_floor_and_ceiling_semantics(
    raw: str, direction: RoundingDirection, expected: str
) -> None:
    """負の金額でも DOWN は数直線の下側、UP は上側（D02 §4.3）。"""
    step = D("1") if "9" * 40 in raw else D("0.01")
    assert Money(D(raw), JPY).round_to(step, direction).amount == D(expected)


def test_rounding_keeps_the_step_exponent() -> None:
    """結果は刻みの指数をそのまま持つ（表示上の桁が刻みと一致する）。"""
    assert str(Price(D("150.1234")).round_to_tick(D("0.001"), RoundingDirection.DOWN)) == "150.123"
    assert str(Money(D("100.567"), JPY).round_to(D("0.01"), RoundingDirection.DOWN)) == (
        "100.56 JPY"
    )


def test_rounding_handles_a_step_larger_than_one() -> None:
    assert Quantity(D("2500")).round_down_to_step(D("1000")) == Quantity(D("2000"))
    assert Price(D("2500")).round_to_tick(D("1000"), RoundingDirection.UP).value == D("3000")


def test_rounding_is_exact_far_beyond_the_kernel_precision() -> None:
    """60 桁の値でも、刻みに載った厳密な結果を返す。"""
    raw = "1" + "0" * 40 + "." + "5" * 20
    rounded = Price(D(raw)).round_to_tick(D("0.0000000001"), RoundingDirection.DOWN)
    assert rounded.value == D("1" + "0" * 40 + ".5555555555")


# --- 呼び出し側のコンテキストからの独立（Codex レビュー round 2 指摘D）-------
#
# `Decimal` の演算は「現在のコンテキスト」の精度で丸められる。カーネルの算術が呼び出し側の
# コンテキストに左右されると、同じ入力でも呼ばれ方次第で台帳の値が変わり、再現性が崩れる。
# 単項演算子（`-x`、`abs(x)`）も例外ではない。

#: カーネルの精度 28 にちょうど収まる 28 桁の仮数。精度 5 のコンテキストで計算されれば
#: 5 桁に丸められて一目で分かる。28 桁を超える値にするとカーネル自身が丸めるため、
#: 「呼び出し側のコンテキストの影響」だけを見たいここでは 28 桁に合わせる。
_LONG_MANTISSA = "1.234567890123456789012345678"

#: 呼び出し側が持ち込む低精度のコンテキスト。
_LOW_PRECISION = Context(prec=5)


def test_price_offset_negation_ignores_the_ambient_context() -> None:
    offset = PriceOffset(D(_LONG_MANTISSA))
    with localcontext(_LOW_PRECISION):
        assert (-offset).value == D("-" + _LONG_MANTISSA)


def test_price_offset_abs_ignores_the_ambient_context() -> None:
    offset = PriceOffset(D("-" + _LONG_MANTISSA))
    with localcontext(_LOW_PRECISION):
        assert abs(offset).value == D(_LONG_MANTISSA)


def test_money_negation_ignores_the_ambient_context() -> None:
    money = Money(D(_LONG_MANTISSA), JPY)
    with localcontext(_LOW_PRECISION):
        assert (-money).amount == D("-" + _LONG_MANTISSA)


def test_money_abs_ignores_the_ambient_context() -> None:
    negative = -Money(D(_LONG_MANTISSA), JPY)
    with localcontext(_LOW_PRECISION):
        assert abs(negative).amount == D(_LONG_MANTISSA)


def test_binary_arithmetic_ignores_the_ambient_context() -> None:
    """二項演算も同じく、呼び出し側の精度に影響されない。"""
    left = Money(D(_LONG_MANTISSA), JPY)
    with localcontext(_LOW_PRECISION):
        assert (left + Money(D("0"), JPY)).amount == D(_LONG_MANTISSA)
        assert (left - Money(D("0"), JPY)).amount == D(_LONG_MANTISSA)
        assert (left * D("1")).amount == D(_LONG_MANTISSA)
        assert (left / D("1")).amount == D(_LONG_MANTISSA)


def test_price_arithmetic_ignores_the_ambient_context() -> None:
    price = Price(D(_LONG_MANTISSA))
    with localcontext(_LOW_PRECISION):
        assert (price + PriceOffset(D("0"))).value == D(_LONG_MANTISSA)
        assert (price - Price(D("0.000000000000000000000000001"))).value == D(
            "1.234567890123456789012345677"
        )


def test_mutating_the_exported_context_does_not_change_the_arithmetic() -> None:
    """公開されたコンテキストを書き換えても計算は変わらない（round 3 指摘）。

    `Context` は可変オブジェクトなので、1つを使い回していると `.prec` を書き換えただけで
    以後の台帳計算がすべて低精度になってしまう。算術のたびに不変な設定から作り直す。
    """
    original = money.KERNEL_DECIMAL_CONTEXT.prec
    try:
        money.KERNEL_DECIMAL_CONTEXT.prec = 3
        total = Money(D(_LONG_MANTISSA), JPY) + Money(D("0"), JPY)
        assert total.amount == D(_LONG_MANTISSA)
    finally:
        money.KERNEL_DECIMAL_CONTEXT.prec = original
    assert money.KERNEL_DECIMAL_CONTEXT.prec == 28


def test_kernel_context_hands_out_a_fresh_object_each_time() -> None:
    first = money.kernel_context()
    second = money.kernel_context()
    assert first is not second
    assert (first.prec, first.rounding) == (28, "ROUND_HALF_EVEN")
    first.prec = 3
    assert money.kernel_context().prec == 28


def test_the_ambient_context_is_left_untouched() -> None:
    """カーネルの算術はプロセスのコンテキストを書き換えない（D02 §4.1）。"""
    with localcontext(_LOW_PRECISION) as ambient:
        _ = -Money(D(_LONG_MANTISSA), JPY)
        _ = abs(PriceOffset(D(_LONG_MANTISSA)))
        assert ambient.prec == 5
        assert getcontext().prec == 5


# --- FloatConversion の内部整合（Codex レビュー round 1 指摘3）---------------
#
# `FloatConversion` は根拠記録（`EvidenceRef` の対象）として保存されるので、後から読んだ
# ときに矛盾した記録が存在してはならない。`price_from_float` を通さず直接組み立てた値も、
# 構築時に4つのフィールドの整合を検査する。


def test_float_conversion_rejects_an_exact_that_does_not_match_raw() -> None:
    valid = price_from_float(150.12345, tick=D("0.001"), direction=RoundingDirection.DOWN)
    with pytest.raises(KernelValueError, match=r"exact must be Decimal\(repr\(raw\)\)"):
        FloatConversion(
            raw=valid.raw,
            exact=D("999.999"),
            tick=valid.tick,
            direction=valid.direction,
            result=valid.result,
        )


def test_float_conversion_rejects_a_result_that_does_not_match_the_rounding() -> None:
    valid = price_from_float(150.12345, tick=D("0.001"), direction=RoundingDirection.DOWN)
    with pytest.raises(KernelValueError, match="result must be"):
        FloatConversion(
            raw=valid.raw,
            exact=valid.exact,
            tick=valid.tick,
            direction=valid.direction,
            result=Price(D("999.999")),
        )


def test_float_conversion_rejects_a_result_rounded_the_other_way() -> None:
    """方向だけを差し替えた記録も、丸め結果と食い違うので拒否する。"""
    down = price_from_float(150.12345, tick=D("0.001"), direction=RoundingDirection.DOWN)
    assert down.result.value == D("150.123")
    with pytest.raises(KernelValueError, match="result must be"):
        FloatConversion(
            raw=down.raw,
            exact=down.exact,
            tick=down.tick,
            direction=RoundingDirection.UP,
            result=down.result,
        )


def test_float_conversion_rejects_a_mismatched_tick() -> None:
    valid = price_from_float(150.12345, tick=D("0.001"), direction=RoundingDirection.DOWN)
    with pytest.raises(KernelValueError, match="result must be"):
        FloatConversion(
            raw=valid.raw,
            exact=valid.exact,
            tick=D("0.01"),
            direction=valid.direction,
            result=valid.result,
        )


@pytest.mark.parametrize("direction", list(RoundingDirection))
def test_a_record_built_by_price_from_float_is_always_consistent(
    direction: RoundingDirection,
) -> None:
    """`price_from_float` が作る記録は、そのまま再構築しても検査を通る。"""
    made = price_from_float(150.12345, tick=D("0.001"), direction=direction)
    rebuilt = FloatConversion(
        raw=made.raw,
        exact=made.exact,
        tick=made.tick,
        direction=made.direction,
        result=made.result,
    )
    assert rebuilt == made
