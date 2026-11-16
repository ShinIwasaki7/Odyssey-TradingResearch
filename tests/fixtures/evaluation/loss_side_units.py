"""研究ポリシーの版 5 以上の判定の純粋関数のテストが使う人工の入力（D24 §3・§4。実装 PR 4-2）。

`tests/fixtures/evaluation/search_units.py` の単位の結果・fold を使い、版 5 の評価基準
（`EvaluationStandardV5`）・経路の要約（`PathSummaryV5`）・判定の追加の入力（`LossSideEvidence`）を
少ない記述で組み立てる。値はすべて人工で、研究ポリシーでも戦略の成績でもない（評価基準の値は
D24 §7.1 の方針の値を写した仮の値）。

- `v5_standard(...)`: 版 5 の評価基準。既定は fold の床（ドローダウン率 ≤ 0.30・#21 ≥ −0.15）、
  経路の床（ドローダウン率 ≤ 0.30）、経路の全体の水準（純収益率 > 0・`PROFIT_FACTOR` ≥ 1.2）、
  損失の集中（倍数 1.0・当てる閾 0.10）、近傍の床（ドローダウン率 ≤ 0.30・#21 ≥ −0.30）、割り引いた
  確率 ≥ 0.95、証拠の要件（fold ごと 0 件・合計 8 件）。
- `v5_path(...)`: 版 5 の経路の要約（取引の集合の要約つき）。
- `steady_returns(...)` / `loss_side(...)`: 割り引いた確率が十分に高くなる人工の日次の収益率と、
  判定の追加の入力。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from odyssey_fx.common.ids import SnapshotId
from odyssey_fx.common.money import Money
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.evaluation.domain.deflated_downside import (
    DailyReturns,
    DeflatedDownsideInputs,
    TrialDailyReturns,
)
from odyssey_fx.evaluation.domain.metrics import MetricId
from odyssey_fx.evaluation.domain.path import PathEnd, PathSummaryV5, PathTradeTotals
from odyssey_fx.evaluation.domain.policy_basis import (
    PREMISE_NAMES,
    VERIFIED_RAW_PREMISE,
    PerSymbolComparisonBasis,
    PerSymbolPolicyBasis,
    PremiseFile,
    SymbolCosts,
    SymbolSnapshots,
    symbol_premise_name,
)
from odyssey_fx.evaluation.domain.regimes import (
    RegimeDirectionRule,
    RegimeLabelAt,
    RegimeRole,
    RegimeSpec,
    RegimeTimeframe,
    RegimeVolatilityRule,
)
from odyssey_fx.evaluation.domain.search import (
    Comparator,
    DeflatedDownsideRule,
    DeflationMethod,
    EvaluationStandardV5,
    FrequencyClass,
    LossConcentrationRule,
    LossSideEvidence,
    MetricCondition,
    NeighborFloorRule,
    Neighborhood,
    NeighborInterval,
    PathBoundary,
    PathRuleV5,
    SufficiencyRule,
    TrialCorrelation,
    ValidationRuleV5,
)
from odyssey_fx.evaluation.domain.variant import Paired
from tests.fixtures.evaluation.search_units import JPY, path_summary, standard

_D = Decimal

#: 既定の fold の床（ドローダウン率 ≤ 0.30、区間末の評価を含む純収益率 ≥ −0.15。D24 §2.2）。
FOLD_FLOORS: Final = (
    MetricCondition(MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, Comparator.LE, _D("0.30")),
    MetricCondition(MetricId.NET_RETURN_RATE_CLOSED_OUT, Comparator.GE, _D("-0.15")),
)


def _snapshots() -> Paired[SnapshotId]:
    return Paired(
        standard=SnapshotId(ContentDigest(algorithm="sha256", hex="1" * 64)),
        candidate=SnapshotId(ContentDigest(algorithm="sha256", hex="2" * 64)),
    )


def _basis() -> PerSymbolPolicyBasis:
    names = sorted({*PREMISE_NAMES, VERIFIED_RAW_PREMISE, symbol_premise_name("USDJPY")})
    return PerSymbolPolicyBasis(
        symbols=(SymbolSnapshots(symbol="USDJPY", snapshots=_snapshots()),),
        conversion_snapshots=_snapshots(),
        target_series=("USDJPY/15m/bid",),
        premises=tuple(
            PremiseFile(name=name, version=1, path=f"configs/premises/{name}.yaml", sha256="3" * 64)
            for name in names
        ),
    )


def _comparison_basis() -> PerSymbolComparisonBasis:
    return PerSymbolComparisonBasis(
        currency="JPY",
        initial_balance=_D("1000000"),
        trial_risk_rate=_D("0.02"),
        account_risk_cap=_D("0.20"),
        commission_per_unit=_D("0.001"),
        entry_delay_bars=0,
        pivot_currency="USD",
        max_observation_skew_seconds=3600,
        symbols=(
            SymbolCosts(
                symbol="USDJPY",
                spread_offset=_D("0.02"),
                entry_slippage=_D("0.01"),
                close_slippage=_D("0.01"),
                adverse_fill_limit=_D("0.05"),
            ),
        ),
    )


def _regimes() -> RegimeSpec:
    return RegimeSpec(
        role=RegimeRole.DISPLAY_ONLY,
        timeframe=RegimeTimeframe.DAILY,
        direction=RegimeDirectionRule(
            ema_bars=50, slope_bars=20, atr_bars=20, dead_band_atr_multiple=_D("0.5")
        ),
        volatility=RegimeVolatilityRule(atr_bars=20, median_window_bars=250),
        label_at=RegimeLabelAt.LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY,
    )


def v5_standard(
    *,
    floors: Sequence[MetricCondition] = FOLD_FLOORS,
    profit_factor: str = "1.2",
    multiple: str = "1.0",
    applies_at: str = "0.10",
    neighbor_net: str = "-0.30",
    min_probability: str = "0.95",
    min_total: int = 8,
) -> EvaluationStandardV5:
    """人工の版 5 の評価基準（選定・分割・用途は `search_units.standard()` と同じ）。"""
    base = standard()
    return EvaluationStandardV5(
        purpose=base.purpose,
        split=base.split,
        selection=base.selection,
        validation=ValidationRuleV5(
            fold_floors=tuple(floors),
            aggregate=(),
            path=PathRuleV5(
                boundary=PathBoundary.CARRY_IF_SAME_TRIAL,
                floors=(
                    MetricCondition(MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, Comparator.LE, _D("0.30")),
                ),
                aggregate=(
                    MetricCondition(MetricId.NET_RETURN_RATE, Comparator.GT, _D("0")),
                    MetricCondition(MetricId.PROFIT_FACTOR, Comparator.GE, _D(profit_factor)),
                ),
            ),
            loss_concentration=LossConcentrationRule(
                worst_fold_metric=MetricId.NET_RETURN_RATE_CLOSED_OUT,
                max_multiple_of_path_net=_D(multiple),
                applies_when_path_net_return_at_least=_D(applies_at),
            ),
            neighbor_floors=NeighborFloorRule(
                neighborhood=Neighborhood.ONE_STEP_IN_WRITTEN_ORDER,
                interval=NeighborInterval.TRAIN,
                floors=(
                    MetricCondition(MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, Comparator.LE, _D("0.30")),
                    MetricCondition(
                        MetricId.NET_RETURN_RATE_CLOSED_OUT, Comparator.GE, _D(neighbor_net)
                    ),
                ),
            ),
            deflated_downside_ratio=DeflatedDownsideRule(
                method=DeflationMethod.DEFLATED_SHARPE_RATIO_FORM,
                downside_target=_D("0"),
                correlation=TrialCorrelation.MEAN_PAIRWISE_PEARSON,
                min_probability=_D(min_probability),
            ),
        ),
        basis=_basis(),
        comparison_basis=_comparison_basis(),
        sufficiency=SufficiencyRule(
            classes=(
                FrequencyClass(
                    name="ALL",
                    min_train_trades_per_365d=_D("0"),
                    min_validation_trades_per_fold=0,
                    min_validation_trades_total=min_total,
                ),
            )
        ),
        regimes=_regimes(),
    )


def totals(
    wins: str = "50000",
    losses: str = "10000",
    *,
    closed: int = 10,
    settlements: int = 0,
    win_count: int | None = None,
    loss_count: int | None = None,
) -> PathTradeTotals:
    """人工の経路の取引の集合の要約（件数は既定で勝ち・負けを合計の有無に合わせる）。"""
    total = closed + settlements
    wins_n = (total - 1 if _D(losses) else total) if win_count is None else win_count
    losses_n = (1 if _D(losses) else 0) if loss_count is None else loss_count
    if not _D(wins):
        wins_n = 0
    return PathTradeTotals(
        wins=Money(_D(wins), JPY),
        losses=Money(_D(losses), JPY),
        closed_count=closed,
        settlement_count=settlements,
        win_count=wins_n,
        loss_count=losses_n,
    )


def v5_path(
    fold_count: int,
    *,
    final: str = "1100000",
    series: Sequence[str] = ("1000000", "1050000", "1100000"),
    end: PathEnd = PathEnd.COMPLETED,
    trades: PathTradeTotals | None = None,
) -> PathSummaryV5:
    """人工の版 5 の経路の要約（`search_units.path_summary` に取引の集合の要約を足す）。"""
    base = path_summary(fold_count, final=final, series=series, end=end)
    return PathSummaryV5(
        segments=base.segments,
        end=base.end,
        end_segment=base.end_segment,
        end_reason=base.end_reason,
        metrics=base.metrics,
        trade_count=base.trade_count,
        final_balance=base.final_balance,
        trades=None if end is PathEnd.INCOMPLETE else (trades or totals()),
    )


def steady_returns(days: int = 200) -> tuple[Decimal, ...]:
    """負けの日が少しある、平均が十分に正の人工の日次の収益率（10 日に 1 日だけ負ける）。"""
    return tuple(_D("-0.001") if index % 10 == 9 else _D("0.002") for index in range(days))


def loss_side(
    neighbors: tuple[tuple[int, ...], ...] = ((1,), (0, 2), (1,)),
    *,
    path_returns: DailyReturns | None = None,
    trial_count: int = 1,
    trials: Sequence[TrialDailyReturns] = (),
    incomplete: bool = False,
) -> LossSideEvidence:
    """人工の判定の追加の入力（既定は 3 試行の 1 軸の格子の近傍と、割り引かない `M = 1`）。"""
    return LossSideEvidence(
        neighbors=neighbors,
        deflated=None
        if incomplete
        else DeflatedDownsideInputs(
            path_returns=steady_returns() if path_returns is None else path_returns,
            trial_count=trial_count,
            trials=tuple(trials),
        ),
    )
