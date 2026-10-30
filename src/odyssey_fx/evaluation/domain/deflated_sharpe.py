"""試行数で割り引いたシャープレシオ（採用審査の表示だけの指標。D16 §5、D15-7 (a)）。

Bailey と López de Prado（2014）の割り引いたシャープレシオを、取引日ごとの値で求める（D16 §5.1）。

```
SR̂  = 割り引く値（年率化シャープレシオ ÷ √260。取引日ごとの値）
SR₀ = √V × ( (1 − γ_E) × Φ⁻¹(1 − 1/N) + γ_E × Φ⁻¹(1 − 1/(N × e)) )
DSR = Φ( (SR̂ − SR₀) × √(T − 1) ÷ √(1 − g₃ × SR̂ + ((g₄ − 1) ÷ 4) × SR̂²) )
```

**表示だけで、審査の結果に使わない**（D13-17 (a)・D15-7 (a)。D16 §5.5）。

計算は `Decimal` で行う（評価の指標と同じく `float` を使わない。D07 §5.5、ADR-0012）。標準正規分布の
分布関数 `Φ` は誤差関数の級数（引数が小さいとき）と相補誤差関数の連分数（大きいとき）で、逆関数
`Φ⁻¹` は二分法で、精度 60 桁のコンテキストの下で求め、記録する値は有効数字 16 桁に丸める
（R3 の仮置き。値は表示だけなので、丸めは結果に影響しない）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int, decimal_from_str, kernel_context
from odyssey_fx.common.time import UtcTime

__all__ = [
    "AssumptionReason",
    "DeflatedSharpeInputs",
    "DeflatedSharpeOutcome",
    "DsrMark",
    "DsrUnavailableReason",
    "daily_returns",
    "deflated_sharpe",
    "normal_cdf",
    "normal_ppf",
]

#: 計算の精度（桁）。級数の打ち切りと二分法の終わりもこの精度で決める。
_PRECISION: Final = 60
#: 記録する値の有効数字（桁）。
_RECORD_DIGITS: Final = 16
#: 円周率（60 桁。`Decimal` の定数を外から持ち込まないため十進リテラルで置く）。
_PI: Final = decimal_from_str("3.14159265358979323846264338327950288419716939937510582097494")
#: オイラーの定数 γ_E（60 桁）。
_EULER_GAMMA: Final = decimal_from_str(
    "0.577215664901532860606512090082402431042159335939923598805767"
)
#: 年率化の係数の取引日の数（D07 §5.5 の #17 と同じ）。
_TRADING_DAYS: Final = 260
#: 級数と連分数の切り替えの境目（誤差関数の引数 `z = |x| ÷ √2`）。
_SERIES_LIMIT: Final = 3
#: 連分数の深さ。
_FRACTION_DEPTH: Final = 400
#: 二分法の回数（区間 [-40, 40] を 2^-260 まで縮める）。
_BISECTION_STEPS: Final = 260
_PPF_BOUND: Final = 40


def _context() -> Context:
    return Context(prec=_PRECISION)


def _record(value: Decimal) -> Decimal:
    """記録する値（有効数字 16 桁）。"""
    with localcontext(Context(prec=_RECORD_DIGITS)):
        return +value


def _erf_series(z: Decimal) -> Decimal:
    """`erf(z)` の級数（`|z|` が小さいとき）。呼び出し側のコンテキストで計算する。"""
    one = decimal_from_int(1)
    total = decimal_from_int(0)
    power = z
    factorial = one
    n = 0
    square = z * z
    threshold = one.scaleb(-_PRECISION - 5)
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


def _cdf(x: Decimal) -> Decimal:
    """標準正規分布の分布関数（呼び出し側のコンテキストで計算する）。"""
    half = decimal_from_str("0.5")
    z = abs(x) / decimal_from_int(2).sqrt()
    if z <= decimal_from_int(_SERIES_LIMIT):
        erf = _erf_series(z)
        upper = half * (decimal_from_int(1) + erf)
    else:
        upper = decimal_from_int(1) - half * _erfc_fraction(z)
    return upper if x >= 0 else decimal_from_int(1) - upper


def normal_cdf(x: Decimal) -> Decimal:
    """標準正規分布の分布関数 `Φ(x)`（有効数字 16 桁）。"""
    if not isinstance(x, Decimal) or not x.is_finite():
        raise KernelValueError("normal_cdf requires a finite Decimal")
    with localcontext(_context()):
        return _record(_cdf(x))


def _ppf(p: Decimal) -> Decimal:
    low = decimal_from_int(-_PPF_BOUND)
    high = decimal_from_int(_PPF_BOUND)
    half = decimal_from_str("0.5")
    for _ in range(_BISECTION_STEPS):
        middle = (low + high) * half
        if _cdf(middle) < p:
            low = middle
        else:
            high = middle
    return (low + high) * half


def normal_ppf(p: Decimal) -> Decimal:
    """標準正規分布の分布関数の逆関数 `Φ⁻¹(p)`（0 < p < 1。有効数字 16 桁）。"""
    if not isinstance(p, Decimal) or not p.is_finite():
        raise KernelValueError("normal_ppf requires a finite Decimal")
    if not decimal_from_int(0) < p < decimal_from_int(1):
        raise KernelValueError(f"normal_ppf requires 0 < p < 1, got {p}")
    with localcontext(_context()):
        return _record(_ppf(p))


class DsrUnavailableReason(Enum):
    """割り引いた指標が値を持たない理由（D16 §5.1。値なしは審査の結果に影響しない）。"""

    #: 割り引く値（最後の fold の選定区間の選ばれた試行の年率化シャープレシオ）が値なし。
    SELECTED_VALUE_MISSING = "SELECTED_VALUE_MISSING"
    #: 観測の数 `T` が 2 未満。
    FEW_OBSERVATIONS = "FEW_OBSERVATIONS"
    #: `N ≥ 2` で、ばらつき `V` を求める試行（値のある試行）が 2 未満。
    TOO_FEW_TRIALS_FOR_VARIANCE = "TOO_FEW_TRIALS_FOR_VARIANCE"
    #: 根号の中が 0 以下。
    NONPOSITIVE_ROOT = "NONPOSITIVE_ROOT"
    #: 日次の資産の変化（歪み・尖りの入力）を読めない、または変化のばらつきが 0。
    RETURNS_UNAVAILABLE = "RETURNS_UNAVAILABLE"
    #: 選定記録・集約表・選定の run が読めない（入力の欠け）。
    INPUT_UNREADABLE = "INPUT_UNREADABLE"


class DsrMark(Enum):
    """割り引いた指標に添える印（D16 §5.1・§5.4）。"""

    ASSUMPTION_NOT_MET = "ASSUMPTION_NOT_MET"
    SINGLE_TRIAL = "SINGLE_TRIAL"


class AssumptionReason(Enum):
    """「仮定に合わない」の理由（D16 §5.4。仮置き 5）。"""

    FEW_TRADES = "FEW_TRADES"
    MOSTLY_FLAT_DAYS = "MOSTLY_FLAT_DAYS"


@dataclass(frozen=True, slots=True)
class DeflatedSharpeInputs:
    """割り引いた指標の入力（D16 §5.1 の表。R1-3 (a)）。

    - `selected_annual`: 最後の fold の選定区間での、選ばれた試行の年率化シャープレシオ（集約表の
      選定の行。値なしは `None`）。
    - `returns`: その選定の run の日次の資産の変化率 `r_k`（k = 1〜T。読めなければ `None`）。
    - `trial_annuals`: 同じ選定区間の全試行の年率化シャープレシオ（値なしは `None`。除いた数を
      記録）。
    - `trial_total`: 試行の総数 `N`（台帳の数え方 (a) の試行の数の合計。L4。仮置き 6）。
    - `trade_count`: その区間の完了取引の件数（「仮定に合わない」の印。読めなければ `None`）。
    - `min_trades` / `max_flat_day_share`: 印の条件（審査の規則のファイル）。
    """

    selected_annual: Decimal | None
    returns: tuple[Decimal, ...] | None
    trial_annuals: tuple[Decimal | None, ...]
    trial_total: int
    trade_count: int | None
    min_trades: int
    max_flat_day_share: Decimal

    def __post_init__(self) -> None:
        if self.selected_annual is not None and (
            not isinstance(self.selected_annual, Decimal) or not self.selected_annual.is_finite()
        ):
            raise KernelValueError("DeflatedSharpeInputs.selected_annual must be a finite Decimal")
        if self.returns is not None and not all(
            isinstance(item, Decimal) and item.is_finite() for item in self.returns
        ):
            raise KernelValueError("DeflatedSharpeInputs.returns must be finite Decimals")
        if not all(
            item is None or (isinstance(item, Decimal) and item.is_finite())
            for item in self.trial_annuals
        ):
            raise KernelValueError("DeflatedSharpeInputs.trial_annuals must be Decimals or None")
        for label in ("trial_total", "min_trades"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise KernelValueError(f"DeflatedSharpeInputs.{label} must be an int >= 0")
        if self.trade_count is not None and (
            isinstance(self.trade_count, bool)
            or not isinstance(self.trade_count, int)
            or self.trade_count < 0
        ):
            raise KernelValueError("DeflatedSharpeInputs.trade_count must be an int >= 0 or None")
        if not isinstance(self.max_flat_day_share, Decimal):
            raise KernelValueError("DeflatedSharpeInputs.max_flat_day_share must be a Decimal")


@dataclass(frozen=True, slots=True)
class DeflatedSharpeOutcome:
    """割り引いた指標の結果（D16 §5.1。値は数値の層にだけ記録する）。

    `unavailable` が空でなければ値なし（`dsr` は `None`）。`marks` は値の有無によらず付く。
    `variance` は `N = 1` のとき `None`（使わない）。
    """

    dsr: Decimal | None
    sr0: Decimal | None
    sr_hat: Decimal | None
    trial_total: int
    variance: Decimal | None
    observations: int | None
    skewness: Decimal | None
    kurtosis: Decimal | None
    excluded_trials: int
    flat_days: int | None
    unavailable: tuple[DsrUnavailableReason, ...]
    marks: tuple[DsrMark, ...]
    assumption_reasons: tuple[AssumptionReason, ...]

    @property
    def shown(self) -> bool:
        """値を持つか（`SHOWN` / `UNAVAILABLE`）。"""
        return self.dsr is not None


def _moments(returns: Sequence[Decimal]) -> tuple[Decimal, Decimal] | None:
    """歪み `g₃` と尖り `g₄`（標準化積率。母集団の積率）。ばらつきが 0 なら `None`。"""
    count = decimal_from_int(len(returns))
    mean = sum(returns, decimal_from_int(0)) / count
    deviations = [value - mean for value in returns]
    m2 = sum((item * item for item in deviations), decimal_from_int(0)) / count
    if m2 == 0:
        return None
    m3 = sum((item * item * item for item in deviations), decimal_from_int(0)) / count
    m4 = sum((item * item * item * item for item in deviations), decimal_from_int(0)) / count
    return m3 / (m2 * m2.sqrt()), m4 / (m2 * m2)


def _sample_variance(values: Sequence[Decimal]) -> Decimal:
    count = len(values)
    mean = sum(values, decimal_from_int(0)) / decimal_from_int(count)
    squares = sum(((value - mean) * (value - mean) for value in values), decimal_from_int(0))
    return squares / decimal_from_int(count - 1)


def deflated_sharpe(inputs: DeflatedSharpeInputs) -> DeflatedSharpeOutcome:
    """割り引いた指標を求める（D16 §5.1・§5.4。表示だけ）。"""
    if not isinstance(inputs, DeflatedSharpeInputs):
        raise KernelValueError("deflated_sharpe requires DeflatedSharpeInputs")
    with localcontext(_context()):
        root_days = decimal_from_int(_TRADING_DAYS).sqrt()
        marks: list[DsrMark] = []
        reasons: list[AssumptionReason] = []
        unavailable: list[DsrUnavailableReason] = []
        returns = inputs.returns
        flat_days = None if returns is None else sum(1 for item in returns if item == 0)
        if inputs.trade_count is not None and inputs.trade_count < inputs.min_trades:
            reasons.append(AssumptionReason.FEW_TRADES)
        if (
            returns
            and flat_days is not None
            and decimal_from_int(flat_days) / decimal_from_int(len(returns))
            > inputs.max_flat_day_share
        ):
            reasons.append(AssumptionReason.MOSTLY_FLAT_DAYS)
        if reasons:
            marks.append(DsrMark.ASSUMPTION_NOT_MET)
        n = inputs.trial_total
        if n == 1:
            marks.append(DsrMark.SINGLE_TRIAL)
        values = [item / root_days for item in inputs.trial_annuals if item is not None]
        excluded = len(inputs.trial_annuals) - len(values)
        sr_hat = None if inputs.selected_annual is None else inputs.selected_annual / root_days
        observations = None if returns is None else len(returns)
        if sr_hat is None:
            unavailable.append(DsrUnavailableReason.SELECTED_VALUE_MISSING)
        if returns is None:
            unavailable.append(DsrUnavailableReason.RETURNS_UNAVAILABLE)
        elif len(returns) < 2:
            unavailable.append(DsrUnavailableReason.FEW_OBSERVATIONS)
        variance: Decimal | None = None
        if n >= 2:
            if len(values) < 2:
                unavailable.append(DsrUnavailableReason.TOO_FEW_TRIALS_FOR_VARIANCE)
            else:
                variance = _sample_variance(values)
        elif n < 1:
            unavailable.append(DsrUnavailableReason.TOO_FEW_TRIALS_FOR_VARIANCE)
        moments = None
        if returns is not None and len(returns) >= 2:
            moments = _moments(returns)
            if moments is None:
                unavailable.append(DsrUnavailableReason.RETURNS_UNAVAILABLE)
        sr0: Decimal | None = None
        if n == 1:
            sr0 = decimal_from_int(0)
        elif variance is not None:
            e = decimal_from_int(1).exp()
            one = decimal_from_int(1)
            first = _ppf(one - one / decimal_from_int(n))
            second = _ppf(one - one / (decimal_from_int(n) * e))
            sr0 = variance.sqrt() * ((one - _EULER_GAMMA) * first + _EULER_GAMMA * second)
        dsr: Decimal | None = None
        if not unavailable and sr_hat is not None and sr0 is not None and moments is not None:
            g3, g4 = moments
            assert returns is not None  # noqa: S101  上で確かめた
            radicand = (
                decimal_from_int(1)
                - g3 * sr_hat
                + ((g4 - decimal_from_int(1)) / decimal_from_int(4)) * sr_hat * sr_hat
            )
            if radicand <= 0:
                unavailable.append(DsrUnavailableReason.NONPOSITIVE_ROOT)
            else:
                statistic = (
                    (sr_hat - sr0) * decimal_from_int(len(returns) - 1).sqrt() / radicand.sqrt()
                )
                dsr = _cdf(statistic)
        skewness = None if moments is None else _record(moments[0])
        kurtosis = None if moments is None else _record(moments[1])
        return DeflatedSharpeOutcome(
            dsr=None if dsr is None else _record(dsr),
            sr0=None if sr0 is None else _record(sr0),
            sr_hat=None if sr_hat is None else _record(sr_hat),
            trial_total=n,
            variance=None if variance is None else _record(variance),
            observations=observations,
            skewness=skewness,
            kurtosis=kurtosis,
            excluded_trials=excluded,
            flat_days=flat_days,
            unavailable=tuple(dict.fromkeys(unavailable)),
            marks=tuple(marks),
            assumption_reasons=tuple(reasons),
        )


def daily_returns(
    initial: Decimal,
    points: Sequence[tuple[UtcTime, Decimal]],
    day_ends: Sequence[UtcTime],
) -> tuple[Decimal, ...] | None:
    """#17 と同じ日次の資産の変化率 `r_k = E_k ÷ E_{k−1} − 1`（D07 §5.5）。

    `E_0` は初期残高、`E_k` は k 番目の取引日の終わり以前で最後の台帳 snapshot の `equity`（無ければ
    `E_{k−1}` を持ち越す。評価の #17 と同じ）。`points` は処理点の昇順。`E_{k−1} = 0` なら `None`。
    """
    equities = [initial]
    position = 0
    current = initial
    for end in day_ends:
        while position < len(points) and points[position][0] <= end:
            current = points[position][1]
            position += 1
        equities.append(current)
    returns: list[Decimal] = []
    with localcontext(kernel_context()):
        for previous, value in zip(equities, equities[1:], strict=False):
            if previous == 0:
                return None
            returns.append(value / previous - decimal_from_int(1))
    return tuple(returns)
