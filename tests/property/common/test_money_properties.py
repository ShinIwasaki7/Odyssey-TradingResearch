"""float から価格への変換のプロパティテスト（D02 §4.6・§11、ADR-0012）。

`price_from_float` の `exact` が常に `Decimal(repr(x))` と一致すること（float の最短往復
表現を経由し、二進浮動小数の誤差を持ち込まないこと）と、丸め方向の意味を確かめる。
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from odyssey_fx.common.money import RoundingDirection, decimal_from_str, price_from_float

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
