"""試行数で割り引いた下側偏差の比率（研究ポリシー第 5 版の設計 D24 §4、決定 140・161〜163）。

受入テスト RP5-11（D24 §10.2）: `M = 1` で `S* = 0`、相関 1 の試行の複製で `N = 1`、相関 0 で
`N = M`、既知の入力で `P_d` の 28 桁の値を固定する。あわせて値なしの扱い（D24 §4.5）を確かめる。
日次の収益率はすべて人工の値で、成績ではない。
"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal
from typing import Final

import pytest

from odyssey_fx.evaluation.domain.deflated_downside import (
    EULER_GAMMA,
    NAPIER_E,
    DeflatedDownsideInputs,
    TrialDailyReturns,
    daily_downside_ratio,
    deflated_downside,
    expected_maximum_z,
)
from odyssey_fx.evaluation.domain.metrics import (
    MetricUnavailableReason,
    annualized_downside_ratio,
)

_D = Decimal

#: 負けの日を含む人工の経路の日次の収益率（250 取引日。5 日周期）。
PATH: Final = tuple(_D(("0.004", "-0.003", "0.002", "-0.001", "0.001")[i % 5]) for i in range(250))
_A: Final = tuple(_D("0.001") * ((i * 7) % 5 - 2) for i in range(60))
_B: Final = tuple(_D("0.001") * ((i * 3) % 7 - 3) for i in range(60))
_C: Final = tuple(_D("0.001") * ((i * 5) % 4 - 1) for i in range(60))


def _trials(*series: tuple[Decimal, ...], folds: int = 2) -> tuple[TrialDailyReturns, ...]:
    return tuple(
        TrialDailyReturns(fold_index=fold, trial_index=index, returns=returns)
        for fold in range(folds)
        for index, returns in enumerate(series)
    )


def _run(
    trial_count: int, trials: tuple[TrialDailyReturns, ...] = (), path: object = PATH
) -> object:
    return deflated_downside(
        DeflatedDownsideInputs(path_returns=path, trial_count=trial_count, trials=trials)  # type: ignore[arg-type]
    )


def test_the_constants_are_fixed_to_28_digits() -> None:
    assert EULER_GAMMA == _D("0.5772156649015328606065120901")
    assert NAPIER_E == _D("2.718281828459045235360287471")
    assert len(EULER_GAMMA.as_tuple().digits) == 28
    assert len(NAPIER_E.as_tuple().digits) == 28


def test_the_daily_ratio_is_the_annualized_downside_ratio_before_annualizing() -> None:
    equities = [_D("1000000")]
    for value in PATH:
        equities.append(equities[-1] * (1 + value))
    annual = annualized_downside_ratio(equities)
    daily = daily_downside_ratio(PATH)
    assert isinstance(annual, Decimal) and isinstance(daily, Decimal)
    assert abs(annual / _D(260).sqrt() - daily) < _D("1e-20")


def test_the_delta_method_matches_an_independent_float_calculation() -> None:
    detail = _run(1)
    r = [float(item) for item in PATH]
    count = len(r)
    mean = sum(r) / count
    shortfalls = [min(item, 0.0) ** 2 for item in r]
    second = sum(shortfalls) / count
    downside = math.sqrt(second)
    ratio = mean / downside
    covariance = sum((a - mean) * (b - second) for a, b in zip(r, shortfalls, strict=True)) / (
        count - 1
    )
    variance = (
        statistics.variance(r) / second
        - ratio * covariance / downside**3
        + ratio**2 * statistics.variance(shortfalls) / (4 * second**2)
    ) / count
    assert detail.ratio is not None and detail.sigma is not None  # type: ignore[attr-defined]
    assert math.isclose(float(detail.ratio), ratio, rel_tol=1e-12)  # type: ignore[attr-defined]
    assert math.isclose(float(detail.sigma), math.sqrt(variance), rel_tol=1e-9)  # type: ignore[attr-defined]


def test_rp5_11_a_single_trial_is_not_deflated() -> None:
    detail = _run(1)
    assert detail.benchmark == 0  # type: ignore[attr-defined]
    assert detail.effective_trials == 1  # type: ignore[attr-defined]
    assert detail.variance is None and detail.mean_correlation is None  # type: ignore[attr-defined]
    assert detail.probability is not None  # type: ignore[attr-defined]


def test_rp5_11_duplicated_trials_have_correlation_one_and_one_effective_trial() -> None:
    noisy = tuple(item + _D("0.0001") * (index % 3) for index, item in enumerate(_A))
    detail = _run(3, _trials(_A, _A, noisy))
    assert detail.mean_correlation is not None  # type: ignore[attr-defined]
    duplicated = _run(3, _trials(_A, _A, _A))
    assert duplicated.mean_correlation == 1  # type: ignore[attr-defined]
    assert duplicated.effective_trials == 1  # type: ignore[attr-defined]
    assert duplicated.benchmark == 0  # type: ignore[attr-defined]


def test_rp5_11_uncorrelated_trials_count_fully() -> None:
    left = tuple(_D("0.001") * ((-1) ** i) for i in range(8))
    right = tuple(_D("0.001") * (1 if i % 4 < 2 else -1) for i in range(8))
    detail = _run(2, _trials(left, right, folds=1))
    assert detail.mean_correlation == 0  # type: ignore[attr-defined]
    assert detail.effective_trials == 2  # type: ignore[attr-defined]


def test_rp5_11_a_negative_mean_correlation_is_clipped_to_zero() -> None:
    left = tuple(_D("0.001") * ((-1) ** i) for i in range(8))
    right = tuple(-item for item in left)
    detail = _run(2, _trials(left, right, folds=1))
    assert detail.mean_correlation == 0  # type: ignore[attr-defined]
    assert detail.effective_trials == 2  # type: ignore[attr-defined]


def test_rp5_11_the_probability_of_a_known_input_is_fixed_to_28_digits() -> None:
    detail = _run(3, _trials(_A, _B, _C))
    assert detail.probability == _D("0.2950699160464866817111763430")  # type: ignore[attr-defined]
    assert detail.benchmark == _D("0.4932081645348950665999105008")  # type: ignore[attr-defined]
    assert detail.effective_trials == _D("2.985585376428716708667775894")  # type: ignore[attr-defined]
    assert detail.variance == _D("0.3372749088590541780619074135")  # type: ignore[attr-defined]
    assert detail.ratio == _D("0.4242640687119285146405066173")  # type: ignore[attr-defined]
    assert (detail.correlation_pairs, detail.excluded_pairs, detail.variance_folds) == (6, 0, 2)  # type: ignore[attr-defined]
    # 同じ入力からは同じ値（乱数・実時計・プロセスのコンテキストを使わない）。
    assert _run(3, _trials(_A, _B, _C)) == detail


def test_rp5_11_a_float_cross_check_of_the_probability() -> None:
    detail = _run(3, _trials(_A, _B, _C))
    statistic = (float(detail.ratio) - float(detail.benchmark)) / float(detail.sigma)  # type: ignore[attr-defined]
    assert math.isclose(
        float(detail.probability),  # type: ignore[attr-defined]
        statistics.NormalDist().cdf(statistic),
        rel_tol=1e-12,
    )


def test_the_expected_maximum_matches_the_design_reference_values() -> None:
    """D24 §4.3 の参考: `N = 4` で約 1.05、`N = 2` で約 0.52、`Z(1) = 0`。"""
    assert expected_maximum_z(_D(1)) == 0
    assert abs(expected_maximum_z(_D(4)) - _D("1.05")) < _D("0.01")
    assert abs(expected_maximum_z(_D(2)) - _D("0.52")) < _D("0.01")
    assert expected_maximum_z(_D("1.0001")) < 0  # 1 に近い N は負（基準値は 0 で切る）


def test_a_zero_variance_trial_is_left_out_of_the_correlation_and_counted() -> None:
    flat = tuple(_D("0") for _ in range(60))
    detail = _run(3, _trials(_A, _B, flat))
    assert detail.excluded_pairs == 4  # type: ignore[attr-defined]
    assert detail.correlation_pairs == 2  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ((_D("0.001"),), MetricUnavailableReason.NO_OBSERVATIONS),
        (tuple(_D("0.001") for _ in range(10)), MetricUnavailableReason.UNDEFINED_DENOMINATOR),
        (MetricUnavailableReason.INPUT_NOT_AVAILABLE, MetricUnavailableReason.INPUT_NOT_AVAILABLE),
    ],
)
def test_a_path_ratio_without_value_leaves_the_probability_without_value(
    path: object, reason: MetricUnavailableReason
) -> None:
    detail = _run(1, path=path)
    assert detail.probability is None  # type: ignore[attr-defined]
    assert detail.unavailable_reason is reason  # type: ignore[attr-defined]


def test_without_two_values_in_any_fold_the_variance_is_an_observation_shortfall() -> None:
    no_loss = tuple(_D("0.001") for _ in range(60))
    detail = _run(2, _trials(_A, no_loss))
    assert detail.variance is None  # type: ignore[attr-defined]
    assert detail.unavailable_reason is MetricUnavailableReason.NO_OBSERVATIONS  # type: ignore[attr-defined]


def test_a_failed_selection_run_that_blocks_the_variance_is_not_judged() -> None:
    missing = MetricUnavailableReason.INPUT_NOT_AVAILABLE
    detail = _run(2, _trials(_A, missing))  # type: ignore[arg-type]
    assert detail.unavailable_reason is MetricUnavailableReason.INPUT_NOT_AVAILABLE  # type: ignore[attr-defined]


def test_a_failed_selection_run_that_does_not_block_the_estimates_is_skipped() -> None:
    trials = (
        *_trials(_A, _B, _C, folds=1),
        TrialDailyReturns(1, 0, _A),
        TrialDailyReturns(1, 1, MetricUnavailableReason.INPUT_NOT_AVAILABLE),
        TrialDailyReturns(1, 2, _C),
    )
    detail = _run(3, trials)
    assert detail.probability is not None  # type: ignore[attr-defined]
    assert detail.excluded_pairs == 2  # type: ignore[attr-defined]


def test_no_compiled_trial_leaves_the_probability_not_judged() -> None:
    detail = _run(0)
    assert detail.unavailable_reason is MetricUnavailableReason.INPUT_NOT_AVAILABLE  # type: ignore[attr-defined]
