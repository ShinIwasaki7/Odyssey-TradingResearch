"""十進数の標準正規分布の `Φ`・`Φ⁻¹`（研究ポリシー第 5 版の設計 D24 §4.4、仮置き 9）。

受入テスト RP5-12（D24 §10.2）: 既知の値と一致し、同じ入力で同じ値。`float` を受け付けない。
既知の値は公表された高精度の値（30 桁以上）を 28 桁に丸めたもの。
"""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import kernel_context
from odyssey_fx.evaluation.domain.deflated_sharpe import normal_cdf, normal_ppf
from odyssey_fx.evaluation.domain.normal_distribution import (
    standard_normal_cdf,
    standard_normal_ppf,
)

_D = Decimal


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        ("0.95", "1.644853626951472714863848908"),  # 1.64485362695147271486384890799…
        ("0.975", "1.959963984540054235524594431"),  # 1.95996398454005423552459443052…
        ("0.5", "0"),
        ("0.05", "-1.644853626951472714863848908"),
    ],
)
def test_rp5_12_the_inverse_matches_known_values(p: str, expected: str) -> None:
    assert standard_normal_ppf(_D(p)) == _D(expected)


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        ("0", "0.5"),
        ("1", "0.8413447460685429485852325456"),  # 0.84134474606854294858523254563…
        ("-1", "0.1586552539314570514147674544"),
        ("5", "0.9999997133484281208060883262"),  # 0.99999971334842812080608832…
    ],
)
def test_rp5_12_the_distribution_matches_known_values(x: str, expected: str) -> None:
    assert standard_normal_cdf(_D(x)) == _D(expected)


def test_rp5_12_the_values_are_28_digit_decimals_and_repeat() -> None:
    first = standard_normal_ppf(_D("0.9"))
    assert isinstance(first, Decimal)
    assert len(first.as_tuple().digits) <= 28
    assert standard_normal_ppf(_D("0.9")) == first
    with localcontext() as context:  # 呼び出し側のコンテキストに依らない
        context.prec = 5
        assert standard_normal_ppf(_D("0.9")) == first
        assert standard_normal_cdf(_D("0.3")) == standard_normal_cdf(_D("0.3"))


def test_rp5_12_the_round_trip_returns_the_probability() -> None:
    for text in ("0.001", "0.2", "0.8", "0.999"):
        p = _D(text)
        back = standard_normal_cdf(standard_normal_ppf(p))
        with localcontext(kernel_context()):
            assert abs(back - p) < _D("1e-26")


def test_rp5_12_float_is_refused() -> None:
    with pytest.raises(KernelValueError):
        standard_normal_cdf(0.5)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError):
        standard_normal_ppf(0.5)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError):
        standard_normal_ppf(_D("1"))
    with pytest.raises(KernelValueError):
        standard_normal_ppf(_D("0"))


def test_a_tiny_probability_still_has_an_inverse() -> None:
    value = standard_normal_ppf(_D("1e-20"))
    assert _D("-9.27") < value < _D("-9.25")


def test_the_display_only_dsr_functions_keep_their_values() -> None:
    """D16 の表示だけの割り引いた指標の `Φ`・`Φ⁻¹`（共有した級数と連分数）は値が変わらない。"""
    assert normal_cdf(_D("1")) == _D("0.8413447460685429")
    assert normal_ppf(_D("0.95")) == _D("1.644853626951473")
