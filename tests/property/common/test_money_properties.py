"""float から価格への変換のプロパティテスト（D02 §4.6・§11、ADR-0012）。

`price_from_float` の `exact` が常に `Decimal(repr(x))` と一致すること（float の最短往復
表現を経由し、二進浮動小数の誤差を持ち込まないこと）と、丸め方向の意味を確かめる。
"""

from __future__ import annotations

from decimal import Context, Decimal, localcontext

from hypothesis import given
from hypothesis import strategies as st

from odyssey_fx.common.money import (
    RoundingDirection,
    _quantize_to_step,
    decimal_from_str,
    price_from_float,
)

#: 実運用にある価格刻み。
_TICKS = [decimal_from_str(text) for text in ("0.00001", "0.0001", "0.001", "0.01", "0.1", "1")]

_directions = st.sampled_from(list(RoundingDirection))


@st.composite
def _price_inputs(draw: st.DrawFn) -> tuple[float, Decimal]:
    """価格として意味のある float と、それを下回る価格刻みの組。

    `raw` が刻み未満だと DOWN 丸めで 0 になり `Price` を作れない（D02 §4.3 に沿った正しい
    拒否）ので、`raw >= tick` になる組だけを生成する。
    """
    tick = draw(st.sampled_from(_TICKS))
    raw = draw(
        st.floats(
            min_value=float(tick),
            max_value=1e6,
            allow_nan=False,
            allow_infinity=False,
            allow_subnormal=False,
        )
    )
    return raw, tick


@given(_price_inputs(), _directions)
def test_exact_is_the_shortest_roundtrip_decimal(
    inputs: tuple[float, Decimal], direction: RoundingDirection
) -> None:
    """`exact` は常に `Decimal(repr(raw))`（D02 §4.6 の表）。"""
    raw, tick = inputs
    conversion = price_from_float(raw, tick=tick, direction=direction)
    assert conversion.exact == Decimal(repr(raw))
    assert float(conversion.exact) == raw


@given(_price_inputs(), _directions)
def test_the_conversion_record_keeps_its_inputs(
    inputs: tuple[float, Decimal], direction: RoundingDirection
) -> None:
    raw, tick = inputs
    conversion = price_from_float(raw, tick=tick, direction=direction)
    assert conversion.raw == raw
    assert conversion.tick == tick
    assert conversion.direction is direction


@given(_price_inputs(), _directions)
def test_the_result_lands_on_the_tick_grid(
    inputs: tuple[float, Decimal], direction: RoundingDirection
) -> None:
    raw, tick = inputs
    conversion = price_from_float(raw, tick=tick, direction=direction)
    assert conversion.result.is_on_tick(tick)


@given(_price_inputs())
def test_down_never_exceeds_the_exact_value(inputs: tuple[float, Decimal]) -> None:
    raw, tick = inputs
    conversion = price_from_float(raw, tick=tick, direction=RoundingDirection.DOWN)
    assert conversion.result.value <= conversion.exact


@given(_price_inputs())
def test_up_is_never_below_the_exact_value(inputs: tuple[float, Decimal]) -> None:
    raw, tick = inputs
    conversion = price_from_float(raw, tick=tick, direction=RoundingDirection.UP)
    assert conversion.result.value >= conversion.exact


@given(_price_inputs(), _directions)
def test_the_rounding_error_stays_within_one_tick(
    inputs: tuple[float, Decimal], direction: RoundingDirection
) -> None:
    raw, tick = inputs
    conversion = price_from_float(raw, tick=tick, direction=direction)
    assert abs(conversion.result.value - conversion.exact) < tick


@given(_price_inputs(), _directions)
def test_the_conversion_is_deterministic(
    inputs: tuple[float, Decimal], direction: RoundingDirection
) -> None:
    raw, tick = inputs
    assert price_from_float(raw, tick=tick, direction=direction) == price_from_float(
        raw, tick=tick, direction=direction
    )


@given(_price_inputs(), _directions)
def test_a_value_already_on_the_grid_is_unchanged(
    inputs: tuple[float, Decimal], direction: RoundingDirection
) -> None:
    """一度刻みへ丸めた値をもう一度通しても、どの方向でも値は動かない（冪等性）。"""
    raw, tick = inputs
    on_grid = price_from_float(raw, tick=tick, direction=RoundingDirection.DOWN).result
    again = price_from_float(float(on_grid.value), tick=tick, direction=direction)
    assert again.result == on_grid


# --- 刻みへの丸めの厳密性（Codex レビュー round 3 指摘）---------------------
#
# 十進コンテキストの精度（28桁）を超える値でも、丸め方向の意味が保たれることを確かめる。
# 桁数の多い `Decimal` を直に生成し、`_quantize_to_step` の不変条件を検査する。

#: 最大 60 桁の有限 `Decimal`（符号つき）。精度 28 を大きく超える。
_wide_decimals = st.decimals(
    allow_nan=False,
    allow_infinity=False,
    places=None,
    min_value=Decimal("-1e20"),
    max_value=Decimal("1e20"),
)

#: 正の刻み。桁の位置をいろいろに散らす。
_steps = st.sampled_from(
    [
        decimal_from_str(text)
        for text in ("0.0000000001", "0.001", "0.01", "0.1", "1", "5", "1000", "1E+3")
    ]
)


def _scaled(value: Decimal, exponent: int) -> int:
    """`value` を指数 `exponent` を単位とする整数にする。

    検証側も十進コンテキストの精度に左右されないよう、比較はすべて Python の整数で行う。
    `result + step` のような `Decimal` 演算をそのまま書くと、28桁を超える場面では**検証式の
    ほうが**丸められてしまい、正しい結果を誤判定する。
    """
    sign, digits, value_exponent = value.as_tuple()
    assert isinstance(value_exponent, int)
    mantissa = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        mantissa = -mantissa
    scale: int = 10 ** (value_exponent - exponent)
    return mantissa * scale


def _common_exponent(*values: Decimal) -> int:
    exponents = []
    for value in values:
        _, _, exponent = value.as_tuple()
        assert isinstance(exponent, int)
        exponents.append(exponent)
    return min(exponents)


@given(_wide_decimals, _steps)
def test_down_brackets_the_value_from_below(value: Decimal, step: Decimal) -> None:
    """切り捨ての定義: 結果 <= 値 < 結果 + 刻み。"""
    result = _quantize_to_step(value, step, RoundingDirection.DOWN)
    unit = _common_exponent(value, step, result)
    scaled_value = _scaled(value, unit)
    scaled_result = _scaled(result, unit)
    assert scaled_result <= scaled_value
    assert scaled_value < scaled_result + _scaled(step, unit)


@given(_wide_decimals, _steps)
def test_up_brackets_the_value_from_above(value: Decimal, step: Decimal) -> None:
    """切り上げの定義: 結果 - 刻み < 値 <= 結果。"""
    result = _quantize_to_step(value, step, RoundingDirection.UP)
    unit = _common_exponent(value, step, result)
    scaled_value = _scaled(value, unit)
    scaled_result = _scaled(result, unit)
    assert scaled_value <= scaled_result
    assert scaled_result - _scaled(step, unit) < scaled_value


@given(_wide_decimals, _steps, _directions)
def test_the_result_is_always_an_exact_multiple_of_the_step(
    value: Decimal, step: Decimal, direction: RoundingDirection
) -> None:
    result = _quantize_to_step(value, step, direction)
    unit = _common_exponent(step, result)
    assert _scaled(result, unit) % _scaled(step, unit) == 0


@given(_wide_decimals, _steps)
def test_half_even_never_moves_further_than_half_a_step(value: Decimal, step: Decimal) -> None:
    result = _quantize_to_step(value, step, RoundingDirection.NEAREST_HALF_EVEN)
    unit = _common_exponent(value, step, result)
    distance = abs(_scaled(result, unit) - _scaled(value, unit))
    assert 2 * distance <= _scaled(step, unit)


@given(_wide_decimals, _steps)
def test_down_and_up_agree_exactly_on_grid_values(value: Decimal, step: Decimal) -> None:
    """切り捨てと切り上げが一致するのは、値がちょうど刻みに載っているときだけ。"""
    down = _quantize_to_step(value, step, RoundingDirection.DOWN)
    up = _quantize_to_step(value, step, RoundingDirection.UP)
    unit = _common_exponent(value, step, down, up)
    scaled_down = _scaled(down, unit)
    scaled_up = _scaled(up, unit)
    if scaled_down == scaled_up:
        assert _scaled(value, unit) == scaled_down
    else:
        assert scaled_up - scaled_down == _scaled(step, unit)


@given(_wide_decimals, _steps, _directions)
def test_rounding_does_not_depend_on_the_ambient_context(
    value: Decimal, step: Decimal, direction: RoundingDirection
) -> None:
    """呼び出し側が精度の低いコンテキストを使っていても結果は変わらない。"""
    baseline = _quantize_to_step(value, step, direction)
    with localcontext(Context(prec=5)):
        assert _quantize_to_step(value, step, direction) == baseline
