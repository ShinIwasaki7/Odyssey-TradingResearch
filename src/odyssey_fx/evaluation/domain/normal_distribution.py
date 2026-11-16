"""標準正規分布の分布関数 `Φ` とその逆関数 `Φ⁻¹` を十進数で求める（D24 §4.3・§4.4、仮置き 9）。

研究ポリシー第 5 版の「試行数で割り引いた確率」（D24 §4。決定 140・163）は、確率 `Φ(·)` と、偶然でも
出る最大の比率の期待値の近似に使う `Φ⁻¹(·)` を判定に使う。判定の値なので `float` を使わない
（D07 §5.5、ADR-0012）。

- `Φ(x) = erfc(−x ÷ √2) ÷ 2` を、誤差関数の級数（引数が小さいとき）と相補誤差関数の連分数（大きい
  とき）で求める。計算は精度 60 桁のコンテキストで行う（D16 の表示だけの割り引いた指標
  `domain.deflated_sharpe` と同じ式。同じ量を 2 通りに作らないため、級数と連分数はここに置き、
  `deflated_sharpe` はここから使う）。
- `Φ⁻¹(p)` は `Φ` のニュートン法で求める。区間 `[−40, 40]` を挟みとして持ち、ニュートンの一歩が
  挟みの外に出るときは二分する（挟み込みつきのニュートン法。どの `0 < p < 1` でも止まる）。
- 返す値はカーネルの精度 28 桁（`kernel_context()`。偶数への丸め）に丸める（D24 §4.4 の「28 桁まで
  求め」）。同じ入力からは常に同じ値になる（乱数・実時計・プロセスのコンテキストを使わない）。
"""

from __future__ import annotations

from decimal import Context, Decimal, localcontext
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int, decimal_from_str, kernel_context

__all__ = [
    "WORKING_PRECISION",
    "cdf_in_context",
    "standard_normal_cdf",
    "standard_normal_ppf",
]

#: 計算の精度（桁）。級数の打ち切りとニュートン法の終わりもこの精度で決める。
WORKING_PRECISION: Final = 60
#: 円周率（60 桁。`Decimal` の定数を外から持ち込まないため十進リテラルで置く）。
_PI: Final = decimal_from_str("3.14159265358979323846264338327950288419716939937510582097494")
#: 級数と連分数の切り替えの境目（誤差関数の引数 `z = |x| ÷ √2`）。
_SERIES_LIMIT: Final = 3
#: 連分数の深さ。
_FRACTION_DEPTH: Final = 400
#: 逆関数の挟みの端（`Φ(±40)` は 0・1 から 10⁻³⁴⁸ 以内）。
_PPF_BOUND: Final = 40
#: ニュートン法の回数の上限（挟み込みで必ず縮むので、ここで止まることは実際には無い）。
_NEWTON_STEPS: Final = 400


def _working() -> Context:
    return Context(prec=WORKING_PRECISION)


def _erf_series(z: Decimal) -> Decimal:
    """`erf(z)` の級数（`|z|` が小さいとき）。呼び出し側のコンテキストで計算する。"""
    one = decimal_from_int(1)
    total = decimal_from_int(0)
    power = z
    factorial = one
    n = 0
    square = z * z
    threshold = one.scaleb(-WORKING_PRECISION - 5)
    while True:
        term = power / (factorial * decimal_from_int(2 * n + 1))
        total = total + (term if n % 2 == 0 else -term)
        if abs(term) < threshold:
            break
        n += 1
        power = power * square
        factorial = factorial * decimal_from_int(n)
    return (decimal_from_int(2) / _PI.sqrt()) * total


def _erfc_fraction(z: Decimal) -> Decimal:
    """`erfc(z)` の連分数（`z` が大きいとき。z > 0）。"""
    half = decimal_from_str("0.5")
    tail = z
    for n in range(_FRACTION_DEPTH, 0, -1):
        tail = z + (decimal_from_int(n) * half) / tail
    return (-(z * z)).exp() / _PI.sqrt() / tail


def cdf_in_context(x: Decimal) -> Decimal:
    """標準正規分布の分布関数 `Φ(x)` を**呼び出し側のコンテキスト**で計算する（丸めない）。

    呼び出し側は精度 `WORKING_PRECISION` のコンテキストの中で呼ぶ（`deflated_sharpe` と本モジュール
    の公開関数がそうしている）。
    """
    half = decimal_from_str("0.5")
    z = abs(x) / decimal_from_int(2).sqrt()
    if z <= decimal_from_int(_SERIES_LIMIT):
        erf = _erf_series(z)
        upper = half * (decimal_from_int(1) + erf)
    else:
        upper = decimal_from_int(1) - half * _erfc_fraction(z)
    return upper if x >= 0 else decimal_from_int(1) - upper


def _density(x: Decimal) -> Decimal:
    """標準正規分布の密度 `φ(x) = exp(−x² ÷ 2) ÷ √(2π)`（呼び出し側のコンテキスト）。"""
    return (-(x * x) / decimal_from_int(2)).exp() / (decimal_from_int(2) * _PI).sqrt()


def _ppf_in_context(p: Decimal) -> Decimal:
    """`Φ⁻¹(p)` の挟み込みつきのニュートン法（呼び出し側のコンテキスト）。"""
    low = decimal_from_int(-_PPF_BOUND)
    high = decimal_from_int(_PPF_BOUND)
    half = decimal_from_str("0.5")
    threshold = decimal_from_int(1).scaleb(-WORKING_PRECISION + 5)
    x = decimal_from_int(0)
    for _ in range(_NEWTON_STEPS):
        gap = cdf_in_context(x) - p
        if gap == 0:
            return x
        if gap < 0:
            low = x
        else:
            high = x
        density = _density(x)
        candidate = x - gap / density if density != 0 else low - decimal_from_int(1)
        if not low < candidate < high:
            candidate = (low + high) * half
        if abs(candidate - x) < threshold:
            return candidate
        x = candidate
    return x  # pragma: no cover - 挟みは毎回縮むので上で止まる


def standard_normal_cdf(x: Decimal) -> Decimal:
    """標準正規分布の分布関数 `Φ(x)`（28 桁に丸めた値。D24 §4.4）。"""
    if not isinstance(x, Decimal) or not x.is_finite():
        raise KernelValueError("standard_normal_cdf requires a finite Decimal")
    with localcontext(_working()):
        value = cdf_in_context(x)
    with localcontext(kernel_context()):
        return +value


def standard_normal_ppf(p: Decimal) -> Decimal:
    """標準正規分布の分布関数の逆関数 `Φ⁻¹(p)`（0 < p < 1。28 桁に丸めた値。D24 §4.4）。"""
    if not isinstance(p, Decimal) or not p.is_finite():
        raise KernelValueError("standard_normal_ppf requires a finite Decimal")
    if not decimal_from_int(0) < p < decimal_from_int(1):
        raise KernelValueError(f"standard_normal_ppf requires 0 < p < 1, got {p}")
    with localcontext(_working()):
        value = _ppf_in_context(p)
    with localcontext(kernel_context()):
        return +value
