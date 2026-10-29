"""試行数で割り引いたシャープレシオの単体テスト（D16 §5。受入テスト T9・T12 の規則の層）。

値は人工の日次の資産の変化で、式を浮動小数で独立に計算した値と照合する（実データは使わない）。
"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.deflated_sharpe import (
    AssumptionReason,
    DeflatedSharpeInputs,
    DsrMark,
    DsrUnavailableReason,
    daily_returns,
    deflated_sharpe,
    normal_cdf,
    normal_ppf,
)

_NORMAL = statistics.NormalDist()
_GAMMA = 0.5772156649015329

#: 人工の日次の資産の変化率（40 日。動かない日は 2 日だけ）。
_RETURNS = tuple(
    Decimal(text)
    for text in (
        "0.004 -0.002 0.003 0.001 -0.004 0.006 0.002 -0.001 0.000 0.005 "
        "-0.003 0.002 0.004 -0.002 0.001 0.003 -0.005 0.007 0.001 0.002 "
        "-0.001 0.003 0.000 -0.002 0.004 0.002 -0.003 0.005 0.001 -0.001 "
        "0.002 0.003 -0.004 0.006 -0.002 0.001 0.002 0.004 -0.001 0.003"
    ).split()
)


def _inputs(**changes: object) -> DeflatedSharpeInputs:
    values: dict[str, object] = {
        "selected_annual": Decimal("1.8"),
        "returns": _RETURNS,
        "trial_annuals": (Decimal("1.8"), Decimal("0.4"), Decimal("-0.6"), Decimal("1.1")),
        "trial_total": 10,
        "trade_count": 45,
        "min_trades": 30,
        "max_flat_day_share": Decimal("0.5"),
    }
    values.update(changes)
    return DeflatedSharpeInputs(**values)  # type: ignore[arg-type]


def _reference(sr_annual: float, trials: list[float], n: int, returns: list[float]) -> float:
    """式（D16 §5.1）の浮動小数での独立な計算。"""
    sr = sr_annual / math.sqrt(260)
    values = [item / math.sqrt(260) for item in trials]
    if n == 1:
        sr0 = 0.0
    else:
        variance = statistics.variance(values)
        sr0 = math.sqrt(variance) * (
            (1 - _GAMMA) * _NORMAL.inv_cdf(1 - 1 / n)
            + _GAMMA * _NORMAL.inv_cdf(1 - 1 / (n * math.e))
        )
    mean = statistics.fmean(returns)
    m2 = statistics.fmean([(item - mean) ** 2 for item in returns])
    g3 = statistics.fmean([(item - mean) ** 3 for item in returns]) / m2**1.5
    g4 = statistics.fmean([(item - mean) ** 4 for item in returns]) / m2**2
    t = len(returns)
    return _NORMAL.cdf(
        (sr - sr0) * math.sqrt(t - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr**2)
    )


@pytest.mark.parametrize("x", ["0", "1.96", "-1.96", "3.5", "-5", "0.3"])
def test_the_normal_distribution_matches_the_standard_library(x: str) -> None:
    assert abs(float(normal_cdf(Decimal(x))) - _NORMAL.cdf(float(x))) < 1e-12


@pytest.mark.parametrize("p", ["0.5", "0.975", "0.01", "0.999", "0.9"])
def test_the_inverse_normal_matches_the_standard_library(p: str) -> None:
    assert abs(float(normal_ppf(Decimal(p))) - _NORMAL.inv_cdf(float(p))) < 1e-9


def test_the_inverse_normal_rejects_probabilities_outside_the_open_interval() -> None:
    with pytest.raises(KernelValueError):
        normal_ppf(Decimal("1"))


def test_the_deflated_sharpe_matches_an_independent_computation() -> None:
    outcome = deflated_sharpe(_inputs())
    assert outcome.shown
    expected = _reference(1.8, [1.8, 0.4, -0.6, 1.1], 10, [float(item) for item in _RETURNS])
    assert outcome.dsr is not None
    assert abs(float(outcome.dsr) - expected) < 1e-9
    assert outcome.marks == ()
    assert outcome.observations == len(_RETURNS)
    assert outcome.flat_days == 2


def test_a_single_trial_is_not_deflated_and_is_marked() -> None:
    """T12: 台帳全体で試行が 1 つ（`N = 1`）なら `SR₀ = 0`（PSR と同じ）、印 `SINGLE_TRIAL`。"""
    outcome = deflated_sharpe(_inputs(trial_total=1, trial_annuals=(Decimal("1.8"),)))
    assert outcome.shown
    assert outcome.sr0 == 0
    assert outcome.variance is None  # 使わない（値なしの理由にしない）
    assert DsrMark.SINGLE_TRIAL in outcome.marks
    assert outcome.dsr is not None
    expected = _reference(1.8, [1.8], 1, [float(item) for item in _RETURNS])
    assert abs(float(outcome.dsr) - expected) < 1e-9


def test_assumption_flags_are_shown_with_the_value() -> None:
    """T9: 「仮定に合わない」の印は値と一緒に出る（表示だけ）。"""
    few = deflated_sharpe(_inputs(trade_count=12))
    assert few.shown
    assert few.marks == (DsrMark.ASSUMPTION_NOT_MET,)
    assert few.assumption_reasons == (AssumptionReason.FEW_TRADES,)
    flat = deflated_sharpe(_inputs(returns=(*_RETURNS[:10], *([Decimal(0)] * 20))))
    assert AssumptionReason.MOSTLY_FLAT_DAYS in flat.assumption_reasons


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"selected_annual": None}, DsrUnavailableReason.SELECTED_VALUE_MISSING),
        ({"returns": (Decimal("0.01"),)}, DsrUnavailableReason.FEW_OBSERVATIONS),
        ({"returns": None}, DsrUnavailableReason.RETURNS_UNAVAILABLE),
        ({"returns": (Decimal(0),) * 10}, DsrUnavailableReason.RETURNS_UNAVAILABLE),
        (
            {"trial_annuals": (Decimal("1.8"), None, None)},
            DsrUnavailableReason.TOO_FEW_TRIALS_FOR_VARIANCE,
        ),
    ],
)
def test_unavailable_values_are_recorded_with_a_reason(
    changes: dict[str, object], reason: DsrUnavailableReason
) -> None:
    outcome = deflated_sharpe(_inputs(**changes))
    assert not outcome.shown
    assert outcome.dsr is None
    assert reason in outcome.unavailable


def test_trials_without_a_value_are_excluded_and_counted() -> None:
    outcome = deflated_sharpe(
        _inputs(trial_annuals=(Decimal("1.8"), None, Decimal("0.2"), Decimal("-0.5")))
    )
    assert outcome.shown
    assert outcome.excluded_trials == 1


def test_daily_returns_carry_the_last_equity_of_each_trading_day() -> None:
    day = [UtcTime.parse(f"2015-01-0{d}T22:00:00Z") for d in (5, 6, 7)]
    points = [
        (UtcTime.parse("2015-01-05T10:00:00Z"), Decimal("1010")),
        (UtcTime.parse("2015-01-05T21:00:00Z"), Decimal("1020")),
        (UtcTime.parse("2015-01-07T12:00:00Z"), Decimal("1000")),
    ]
    returns = daily_returns(Decimal("1000"), points, day)
    assert returns is not None
    assert returns[0] == Decimal("0.02")
    assert returns[1] == 0  # 動かない日は前の日の資産を持ち越す
    assert abs(returns[2] - (Decimal("1000") / Decimal("1020") - 1)) < Decimal("1e-20")
