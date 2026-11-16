"""試行数で割り引いた下側偏差の比率（研究ポリシー第 5 版の判定の材料。D24 §4、決定 140・161〜163）。

経路の下側偏差の比率（日次の収益率の平均 ÷ 下側偏差。目標値 0、分母は全日数。決定 161）を、その
実験の試行数 `M` と試行どうしの相関で割り引き（割り引いたシャープ比の形。決定 162・163）、比率の
真の値が基準値を超える確率 `P_d` を求める。**台帳の累計は使わない**（決定 Q17、D23 の仮置き 8）。

```
r_k  = E_k ÷ E_{k−1} − 1（#17 と同じ日次の作り方。D24 の仮置き 8）、T = 取引日の数
m    = (Σ r_k) ÷ T、D_k = min(r_k, 0)²、d = √((Σ D_k) ÷ T)、S = m ÷ d（日次）
Var(S) ≈ (1/T) × [ s_r² ÷ d² − S × c_rD ÷ d³ + S² × s_D² ÷ (4 × d⁴) ]、σ = √Var(S)（デルタ法。§4.2）
N    = ρ̄ + (1 − ρ̄) × M（ρ̄ は選定の区間の試行の対のピアソン相関の平均を 0〜1 に切った値。§4.3）
Z(N) = (1 − γ) × Φ⁻¹(1 − 1/N) + γ × Φ⁻¹(1 − 1/(N × e))（Z(1) = 0）
S*   = √V × max(0, Z(N))（V は fold ごとの試行の比率の標本分散の平均。M = 1 なら S* = 0）
P_d  = Φ((S − S*) ÷ σ)
```

- 統計（平均・分散・共分散・相関）は各演算をカーネルの精度で行う（評価の指標と同じ。D07 §5.5）。
  `Φ`・`Φ⁻¹` は `domain.normal_distribution`（精度 60 桁で計算し 28 桁に丸める。仮置き 9）。
  定数 γ（オイラーの定数）と e（ネイピア数）は 28 桁の十進数で固定する（仮置き 9）。
- 値なし（D24 §4.5）: 理由は `MetricUnavailableReason` で返す。観測不足（`NO_OBSERVATIONS`・
  `UNDEFINED_DENOMINATOR`）は判定で証拠不足、入力が無い（`INPUT_NOT_AVAILABLE`）は判定できない。

ここは純粋関数と記録の型だけを持つ。日次の収益率の系列は `evaluation.application` が台帳の表から
作って渡す（D24 の仮置き 10。系列そのものは保存しない）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int, decimal_from_str, kernel_context
from odyssey_fx.evaluation.domain.metrics import MetricUnavailableReason
from odyssey_fx.evaluation.domain.normal_distribution import (
    standard_normal_cdf,
    standard_normal_ppf,
)

__all__ = [
    "EULER_GAMMA",
    "NAPIER_E",
    "DailyReturns",
    "DeflatedDownsideDetail",
    "DeflatedDownsideInputs",
    "TrialDailyReturns",
    "daily_downside_ratio",
    "deflated_downside",
    "expected_maximum_z",
]

#: オイラーの定数 γ（28 桁。D24 の仮置き 9）。
EULER_GAMMA: Final = decimal_from_str("0.5772156649015328606065120901")
#: ネイピア数 e（28 桁。D24 の仮置き 9）。
NAPIER_E: Final = decimal_from_str("2.718281828459045235360287471")

#: 日次の収益率の系列（取引日の順）か、系列を作れない理由。
type DailyReturns = tuple[Decimal, ...] | MetricUnavailableReason


def _require_returns(value: object, label: str) -> None:
    if isinstance(value, MetricUnavailableReason):
        return
    if not isinstance(value, tuple) or not all(
        isinstance(item, Decimal) and item.is_finite() for item in value
    ):
        raise KernelValueError(f"{label} must be a tuple of finite Decimals or a reason")


@dataclass(frozen=True, slots=True)
class TrialDailyReturns:
    """選定の区間の試行 1 つの日次の収益率（D24 §4.1 の `S_{n,k}` と §4.3 の相関の材料）。

    `returns` は、その fold の選定の区間の独立口座の run の資産の系列に #17 と同じ日次の作り方を
    当てた系列。run が無い・失敗した（試行の結果が有効でない）単位は `INPUT_NOT_AVAILABLE`。
    """

    fold_index: int
    trial_index: int
    returns: DailyReturns

    def __post_init__(self) -> None:
        for label in ("fold_index", "trial_index"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise KernelValueError(f"TrialDailyReturns.{label} must be an int >= 0")
        _require_returns(self.returns, "TrialDailyReturns.returns")


@dataclass(frozen=True, slots=True)
class DeflatedDownsideInputs:
    """割り引きの入力（D24 §4。仮置き 6・7・10）。

    - `path_returns`: 経路の日次の収益率（区間の終わりの費用を引いた後の点を含む経路の資産の系列。
      D24 の仮置き 8）。
    - `trial_count`: 試行の数 `M`（列挙した試行のうちコンパイル拒否でない試行の数。仮置き 6）。
      0（全試行がコンパイル拒否）なら割り引けないので、入力が無い値なしにする。
    - `trials`: 選定の区間の試行ごとの日次の収益率（fold と試行の番号の昇順。コンパイル拒否の試行は
      含めない）。`M = 1` なら使わない。
    """

    path_returns: DailyReturns
    trial_count: int
    trials: tuple[TrialDailyReturns, ...]

    def __post_init__(self) -> None:
        _require_returns(self.path_returns, "DeflatedDownsideInputs.path_returns")
        if (
            isinstance(self.trial_count, bool)
            or not isinstance(self.trial_count, int)
            or self.trial_count < 0
        ):
            raise KernelValueError("DeflatedDownsideInputs.trial_count must be an int >= 0")
        if not isinstance(self.trials, tuple) or not all(
            isinstance(item, TrialDailyReturns) for item in self.trials
        ):
            raise KernelValueError("DeflatedDownsideInputs.trials must be TrialDailyReturns")
        keys = [(item.fold_index, item.trial_index) for item in self.trials]
        if keys != sorted(set(keys)):
            raise KernelValueError(
                "DeflatedDownsideInputs.trials は (fold_index, trial_index) の昇順で重複しない"
            )


@dataclass(frozen=True, slots=True)
class DeflatedDownsideDetail:
    """割り引きの材料と結果（結末記録の詳細の項目。D24 §9.3 の表 3 の行）。

    - `trial_count`（M）・`observations`（経路の取引日の数 T）・`ratio`（経路の日次の比率 S）・
      `sigma`（σ）・`mean_correlation`（ρ̄。0〜1 に切った値）・`correlation_pairs`（相関の平均に
      入れた対の数）・`excluded_pairs`（分散 0 などで外した対の数）・`effective_trials`（N）・
      `variance`（V）・`variance_folds`（V に入れた fold の数）・`benchmark`（S*）・
      `probability`（P_d）。
    - `unavailable_reason`: `probability` が値なしの理由（値があれば `None`）。
    - `M = 1` のときは `mean_correlation`・`variance` が `None`、`effective_trials` が 1、
      `benchmark` が 0（割り引かない。仮置き 6）。
    """

    trial_count: int
    observations: int | None
    ratio: Decimal | None
    sigma: Decimal | None
    mean_correlation: Decimal | None
    correlation_pairs: int
    excluded_pairs: int
    effective_trials: Decimal | None
    variance: Decimal | None
    variance_folds: int
    benchmark: Decimal | None
    probability: Decimal | None
    unavailable_reason: MetricUnavailableReason | None

    def __post_init__(self) -> None:
        for label in ("trial_count", "correlation_pairs", "excluded_pairs", "variance_folds"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise KernelValueError(f"DeflatedDownsideDetail.{label} must be an int >= 0")
        if self.observations is not None and (
            isinstance(self.observations, bool)
            or not isinstance(self.observations, int)
            or self.observations < 0
        ):
            raise KernelValueError("DeflatedDownsideDetail.observations must be an int or None")
        for label in (
            "ratio",
            "sigma",
            "mean_correlation",
            "effective_trials",
            "variance",
            "benchmark",
            "probability",
        ):
            value = getattr(self, label)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise KernelValueError(f"DeflatedDownsideDetail.{label} must be a Decimal or None")
        if (self.probability is None) == (self.unavailable_reason is None):
            raise KernelValueError(
                "DeflatedDownsideDetail: 確率と値なしの理由は、どちらか一方だけを持つ"
            )
        if self.unavailable_reason is not None and not isinstance(
            self.unavailable_reason, MetricUnavailableReason
        ):
            raise KernelValueError("DeflatedDownsideDetail.unavailable_reason の型が違う")
        if self.probability is not None and not 0 <= self.probability <= 1:
            raise KernelValueError("DeflatedDownsideDetail.probability は 0 以上 1 以下")


@dataclass(frozen=True, slots=True)
class _Ratio:
    """日次の比率 `S` と、その推定量の標準誤差 `σ`（デルタ法）。"""

    observations: int
    ratio: Decimal
    sigma: Decimal | None


def daily_downside_ratio(returns: Sequence[Decimal]) -> Decimal | MetricUnavailableReason:
    """日次の下側偏差の比率 `S = m ÷ d`（D24 §4.1。目標値 0、分母は全日数。決定 161）。

    #23（`annualized_downside_ratio`）の手順 2・3 と同じ順で計算し、年率化（× √260）しない。
    `T < 2` なら `NO_OBSERVATIONS`、`d = 0`（負けの日が無い）なら `UNDEFINED_DENOMINATOR`。
    """
    found = _ratio(returns, with_sigma=False)
    return found if isinstance(found, MetricUnavailableReason) else found.ratio


def _ratio(returns: Sequence[Decimal], *, with_sigma: bool) -> _Ratio | MetricUnavailableReason:
    values = tuple(returns)
    for value in values:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise KernelValueError("the daily returns must be finite Decimals")
    days = len(values)
    if days < 2:
        return MetricUnavailableReason.NO_OBSERVATIONS
    zero = decimal_from_int(0)
    with localcontext(kernel_context()):
        count = decimal_from_int(days)
        total = zero
        for value in values:
            total = total + value
        mean = total / count
        shortfalls = []
        for value in values:
            below = value if value < zero else zero
            shortfalls.append(below * below)
        squares = zero
        for item in shortfalls:
            squares = squares + item
        downside_mean = squares / count
        downside = downside_mean.sqrt()
        if downside == 0:
            return MetricUnavailableReason.UNDEFINED_DENOMINATOR
        ratio = mean / downside
        if not with_sigma:
            return _Ratio(observations=days, ratio=ratio, sigma=None)
        # デルタ法（D24 §4.2）。標本分散・共分散は T − 1 で割る。
        degrees = decimal_from_int(days - 1)
        var_r = zero
        var_d = zero
        cov = zero
        for value, item in zip(values, shortfalls, strict=True):
            dr = value - mean
            dd = item - downside_mean
            var_r = var_r + dr * dr
            var_d = var_d + dd * dd
            cov = cov + dr * dd
        var_r = var_r / degrees
        var_d = var_d / degrees
        cov = cov / degrees
        square = downside_mean  # d² = μ2
        cube = square * downside
        fourth = square * square
        bracket = (
            var_r / square
            - ratio * cov / cube
            + ratio * ratio * var_d / (decimal_from_int(4) * fourth)
        )
        variance = bracket / count
        if variance <= 0:
            return MetricUnavailableReason.UNDEFINED_DENOMINATOR
        return _Ratio(observations=days, ratio=ratio, sigma=variance.sqrt())


def _sample_variance(values: Sequence[Decimal]) -> Decimal:
    with localcontext(kernel_context()):
        count = decimal_from_int(len(values))
        total = decimal_from_int(0)
        for value in values:
            total = total + value
        mean = total / count
        squares = decimal_from_int(0)
        for value in values:
            squares = squares + (value - mean) * (value - mean)
        return squares / decimal_from_int(len(values) - 1)


def _pearson(left: Sequence[Decimal], right: Sequence[Decimal]) -> Decimal | None:
    """ピアソンの相関（どちらかの分散が 0 なら `None`）。"""
    if len(left) != len(right):
        raise KernelValueError(
            "同じ fold の選定の区間の試行は同じ取引日の数を持つ（日次の収益率の長さが違う）"
        )
    if len(left) < 2:
        return None
    with localcontext(kernel_context()):
        count = decimal_from_int(len(left))
        mean_l = decimal_from_int(0)
        mean_r = decimal_from_int(0)
        for a, b in zip(left, right, strict=True):
            mean_l = mean_l + a
            mean_r = mean_r + b
        mean_l = mean_l / count
        mean_r = mean_r / count
        cross = decimal_from_int(0)
        sq_l = decimal_from_int(0)
        sq_r = decimal_from_int(0)
        for a, b in zip(left, right, strict=True):
            da = a - mean_l
            db = b - mean_r
            cross = cross + da * db
            sq_l = sq_l + da * da
            sq_r = sq_r + db * db
        if sq_l == 0 or sq_r == 0:
            return None
        return cross / (sq_l * sq_r).sqrt()


def expected_maximum_z(effective_trials: Decimal) -> Decimal:
    """`N` 個の独立な標準正規の最大の期待値の近似 `Z(N)`（D24 §4.3。`Z(1) = 0`）。"""
    if not isinstance(effective_trials, Decimal) or effective_trials < 1:
        raise KernelValueError("expected_maximum_z requires N >= 1")
    one = decimal_from_int(1)
    if effective_trials == one:
        return decimal_from_int(0)
    with localcontext(kernel_context()):
        first_p = one - one / effective_trials
        second_p = one - one / (effective_trials * NAPIER_E)
    first = standard_normal_ppf(first_p)
    second = standard_normal_ppf(second_p)
    with localcontext(kernel_context()):
        return (one - EULER_GAMMA) * first + EULER_GAMMA * second


def _first_reason(reasons: Sequence[MetricUnavailableReason]) -> MetricUnavailableReason | None:
    """入力が無いことによる値なしを先に、無ければ最初の観測不足の理由（D09 §7.3 の順）。"""
    if MetricUnavailableReason.INPUT_NOT_AVAILABLE in reasons:
        return MetricUnavailableReason.INPUT_NOT_AVAILABLE
    return reasons[0] if reasons else None


def deflated_downside(inputs: DeflatedDownsideInputs) -> DeflatedDownsideDetail:
    """割り引いた確率 `P_d` とその材料を求める（D24 §4.1〜§4.5）。"""
    if not isinstance(inputs, DeflatedDownsideInputs):
        raise KernelValueError("deflated_downside requires DeflatedDownsideInputs")
    reasons: list[MetricUnavailableReason] = []
    observations: int | None = None
    ratio: Decimal | None = None
    sigma: Decimal | None = None
    path = inputs.path_returns
    if isinstance(path, MetricUnavailableReason):
        reasons.append(path)
    else:
        found = _ratio(path, with_sigma=True)
        observations = len(path)
        if isinstance(found, MetricUnavailableReason):
            reasons.append(found)
            # σ だけが定まらないときも比率は表示する（材料の記録。D24 §9.3）。
            plain = _ratio(path, with_sigma=False)
            if not isinstance(plain, MetricUnavailableReason):
                ratio = plain.ratio
        else:
            ratio = found.ratio
            sigma = found.sigma

    count = inputs.trial_count
    mean_correlation: Decimal | None = None
    variance: Decimal | None = None
    variance_folds = 0
    pairs = 0
    excluded = 0
    effective: Decimal | None = None
    benchmark: Decimal | None = None
    if count == 0:
        reasons.append(MetricUnavailableReason.INPUT_NOT_AVAILABLE)
    elif count == 1:
        effective = decimal_from_int(1)
        benchmark = decimal_from_int(0)
    else:
        by_fold: dict[int, list[TrialDailyReturns]] = {}
        for item in inputs.trials:
            by_fold.setdefault(item.fold_index, []).append(item)
        fold_variances: list[Decimal] = []
        correlations: list[Decimal] = []
        trial_missing = False
        for fold in sorted(by_fold):
            items = by_fold[fold]
            ratios: list[Decimal] = []
            for item in items:
                if isinstance(item.returns, MetricUnavailableReason):
                    trial_missing = trial_missing or (
                        item.returns is MetricUnavailableReason.INPUT_NOT_AVAILABLE
                    )
                    continue
                value = daily_downside_ratio(item.returns)
                if not isinstance(value, MetricUnavailableReason):
                    ratios.append(value)
            if len(ratios) >= 2:
                fold_variances.append(_sample_variance(ratios))
            for position, left in enumerate(items):
                for right in items[position + 1 :]:
                    if isinstance(left.returns, MetricUnavailableReason) or isinstance(
                        right.returns, MetricUnavailableReason
                    ):
                        excluded += 1
                        continue
                    correlation = _pearson(left.returns, right.returns)
                    if correlation is None:
                        excluded += 1
                        continue
                    correlations.append(correlation)
        pairs = len(correlations)
        variance_folds = len(fold_variances)
        zero = decimal_from_int(0)
        one = decimal_from_int(1)
        with localcontext(kernel_context()):
            if fold_variances:
                total = zero
                for value in fold_variances:
                    total = total + value
                variance = total / decimal_from_int(len(fold_variances))
            if correlations:
                total = zero
                for value in correlations:
                    total = total + value
                mean_correlation = min(one, max(zero, total / decimal_from_int(len(correlations))))
        if variance is None or mean_correlation is None:
            if trial_missing:
                reasons.append(MetricUnavailableReason.INPUT_NOT_AVAILABLE)
            if variance is None:
                reasons.append(MetricUnavailableReason.NO_OBSERVATIONS)
            if mean_correlation is None:
                reasons.append(
                    MetricUnavailableReason.UNDEFINED_DENOMINATOR
                    if excluded
                    else MetricUnavailableReason.NO_OBSERVATIONS
                )
        else:
            with localcontext(kernel_context()):
                effective = mean_correlation + (one - mean_correlation) * decimal_from_int(count)
            z = expected_maximum_z(effective)
            with localcontext(kernel_context()):
                benchmark = variance.sqrt() * max(zero, z)

    reason = _first_reason(reasons)
    probability: Decimal | None = None
    if reason is None:
        if ratio is None or sigma is None or benchmark is None:  # pragma: no cover - 上で決まる
            raise KernelValueError("deflated_downside: the ratio, sigma and benchmark are present")
        with localcontext(kernel_context()):
            statistic = (ratio - benchmark) / sigma
        probability = standard_normal_cdf(statistic)
    return DeflatedDownsideDetail(
        trial_count=count,
        observations=observations,
        ratio=ratio,
        sigma=sigma,
        mean_correlation=mean_correlation,
        correlation_pairs=pairs,
        excluded_pairs=excluded,
        effective_trials=effective,
        variance=variance,
        variance_folds=variance_folds,
        benchmark=benchmark,
        probability=probability,
        unavailable_reason=reason,
    )
