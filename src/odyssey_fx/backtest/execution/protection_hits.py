"""保護水準の到達判定と足内競合解決（D06 §7.3・§7.4、ADR-0030）。

判定は執行足が終了したフェーズで、**その足の開始前に有効だった**保護水準について行う
（終値で計算した更新をその足の過去の高値・安値へ適用しない）。判定価格は買い建玉が bid、
売り建玉が ask である。

片側だけに触れたら `SINGLE_HIT`。両側に触れたら解像度の階層を1段ずつ降り、片方だけに触れる
最初の子足が見つかればその側を採用して `RESOLVED_BY_CHILD`、最小解像度でも順序が観測できな
ければ `UNRESOLVED_SL_PRIORITY` として**損切りを採用**する。損切り優先が「既定の規則」では
なく「階層を降りきった結果の裁定」であることは、記録の `method` から読める。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from odyssey_fx.backtest.domain.orders import CloseCause, OrderSide
from odyssey_fx.backtest.domain.policies import (
    HierarchyCheckResult,
    ResolutionHierarchy,
    SpreadModel,
)
from odyssey_fx.backtest.domain.positions import ProtectionState
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import FillId, PositionId
from odyssey_fx.common.time import Interval
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "ChildBars",
    "IntrabarResolution",
    "ResolutionMethod",
    "hierarchy_checks",
    "resolve_intrabar",
    "touches",
]

#: 親足の区間を覆う下位足を時系列順に返す関数（`engine` がポートから供給する）。
ChildBars = Callable[[SeriesId, Interval], tuple[Bar, ...]]


class ResolutionMethod(Enum):
    """足内競合をどう決めたか（D06 §7.4）。"""

    SINGLE_HIT = "SINGLE_HIT"
    RESOLVED_BY_CHILD = "RESOLVED_BY_CHILD"
    UNRESOLVED_SL_PRIORITY = "UNRESOLVED_SL_PRIORITY"


@dataclass(frozen=True, slots=True)
class IntrabarResolution:
    """足内競合の解決の記録（D06 §7.4）。どの解像度まで降りて決めたかを残す。"""

    position_id: PositionId
    parent_bar_key: BarKey
    method: ResolutionMethod
    series_used: tuple[SeriesId, ...]
    verdict: CloseCause
    fill_id: FillId
    resolved_child_bar_key: BarKey | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.position_id, PositionId):
            raise KernelValueError("IntrabarResolution.position_id must be a PositionId")
        if not isinstance(self.parent_bar_key, BarKey):
            raise KernelValueError("IntrabarResolution.parent_bar_key must be a BarKey")
        if not isinstance(self.method, ResolutionMethod):
            raise KernelValueError("IntrabarResolution.method must be a ResolutionMethod")
        if not isinstance(self.series_used, tuple) or not self.series_used:
            raise KernelValueError("IntrabarResolution.series_used must be a non-empty tuple")
        if self.verdict not in (CloseCause.STOP_LOSS, CloseCause.TAKE_PROFIT):
            raise KernelValueError(
                "IntrabarResolution.verdict must be STOP_LOSS or TAKE_PROFIT,"
                f" got {self.verdict.value}"
            )
        if not isinstance(self.fill_id, FillId):
            raise KernelValueError("IntrabarResolution.fill_id must be a FillId")
        if self.resolved_child_bar_key is not None and not isinstance(
            self.resolved_child_bar_key, BarKey
        ):
            raise KernelValueError(
                "IntrabarResolution.resolved_child_bar_key must be a BarKey or None"
            )
        if (self.method is ResolutionMethod.RESOLVED_BY_CHILD) != (
            self.resolved_child_bar_key is not None
        ):
            raise KernelValueError(
                "a child bar is recorded exactly when the conflict was resolved by a child"
                " (D06 §7.4)"
            )


def touches(
    side: OrderSide, protection: ProtectionState, bar: Bar, spread_model: SpreadModel
) -> tuple[bool, bool]:
    """その足で損切り・利確に触れたか（D06 §7.3）。戻り値は `(損切り, 利確)`。

    判定価格は買い建玉が bid（系列の値そのもの）、売り建玉が ask（spread モデルで導く）。
    """
    if not isinstance(protection, ProtectionState):
        raise KernelValueError("touches requires a ProtectionState")
    if not isinstance(bar, Bar):
        raise KernelValueError("touches requires a Bar")
    if side is OrderSide.BUY:
        high, low = bar.high, bar.low
        stop_hit = low <= protection.stop_loss
        tp_hit = protection.take_profit is not None and high >= protection.take_profit
    else:
        high = spread_model.ask_from_bid(bar.high)
        low = spread_model.ask_from_bid(bar.low)
        stop_hit = high >= protection.stop_loss
        tp_hit = protection.take_profit is not None and low <= protection.take_profit
    return stop_hit, tp_hit


def _resolve_level(
    side: OrderSide,
    protection: ProtectionState,
    parent: Bar,
    hierarchy: ResolutionHierarchy,
    level: int,
    child_bars: ChildBars | None,
    spread_model: SpreadModel,
    used: list[SeriesId],
) -> tuple[CloseCause, ResolutionMethod, BarKey | None]:
    """D06 §7.4 の解決手順を1段ぶん実行し、必要なら次の解像度へ降りる。"""
    if level + 1 >= len(hierarchy.levels) or child_bars is None:
        # 最小解像度でもなお両方に触れ、順序が観測できない（ADR-0030 の裁定）。
        return CloseCause.STOP_LOSS, ResolutionMethod.UNRESOLVED_SL_PRIORITY, None
    child_series = hierarchy.levels[level + 1]
    used.append(child_series)
    for child in child_bars(child_series, parent.interval):
        stop_hit, tp_hit = touches(side, protection, child, spread_model)
        if stop_hit and tp_hit:
            # 同じ子足で両方に触れたら、その子足を親としてさらに降りる（再帰）。
            return _resolve_level(
                side, protection, child, hierarchy, level + 1, child_bars, spread_model, used
            )
        if stop_hit:
            return CloseCause.STOP_LOSS, ResolutionMethod.RESOLVED_BY_CHILD, child.key
        if tp_hit:
            return CloseCause.TAKE_PROFIT, ResolutionMethod.RESOLVED_BY_CHILD, child.key
    # どの子足でも片方にも触れない場合は順序を観測できていない（被覆検査を通っていれば
    # 起こらないが、黙って利確を選ばずに損切り優先の裁定へ落とす）。
    return CloseCause.STOP_LOSS, ResolutionMethod.UNRESOLVED_SL_PRIORITY, None


def resolve_intrabar(
    *,
    position_id: PositionId,
    side: OrderSide,
    protection: ProtectionState,
    parent: Bar,
    hierarchy: ResolutionHierarchy,
    spread_model: SpreadModel,
    fill_id: FillId,
    child_bars: ChildBars | None = None,
) -> IntrabarResolution | None:
    """保護水準の到達を解決する（D06 §7.4）。触れていなければ `None`。"""
    stop_hit, tp_hit = touches(side, protection, parent, spread_model)
    if not stop_hit and not tp_hit:
        return None
    used: list[SeriesId] = [hierarchy.levels[0]]
    if stop_hit and tp_hit:
        verdict, method, child_key = _resolve_level(
            side, protection, parent, hierarchy, 0, child_bars, spread_model, used
        )
    else:
        verdict = CloseCause.STOP_LOSS if stop_hit else CloseCause.TAKE_PROFIT
        method = ResolutionMethod.SINGLE_HIT
        child_key = None
    return IntrabarResolution(
        position_id=position_id,
        parent_bar_key=parent.key,
        method=method,
        series_used=tuple(used),
        verdict=verdict,
        fill_id=fill_id,
        resolved_child_bar_key=child_key,
    )


def hierarchy_checks(
    hierarchy: ResolutionHierarchy,
    parent_bars: tuple[Bar, ...],
    child_bars: ChildBars | None = None,
) -> tuple[HierarchyCheckResult, ...]:
    """階層の適合検査1〜4（D06 §7.4）。検査5（存在）は完全性検査の結果から作る。

    検査は「下位足が宣言されている場合」にだけ走る。階層が1段なら空の組を返す。
    """
    if len(hierarchy.levels) < 2 or child_bars is None:
        return ()
    results: list[HierarchyCheckResult] = []
    # 1段ぶん降りるたびに、いま調べた子足が次の段の親足になる。渡された親足だけを毎回
    # 走査すると、2段目より下の組（levels[1] と levels[2] など）の検査が1件も走らない。
    current = tuple(bar for bar in parent_bars if bar.series == hierarchy.levels[0])
    for level in range(len(hierarchy.levels) - 1):
        parent_series = hierarchy.levels[level]
        child_series = hierarchy.levels[level + 1]
        next_parents: list[Bar] = []
        for parent in current:
            children = child_bars(child_series, parent.interval)
            results.extend(_checks_for_parent(parent_series, child_series, parent, children))
            next_parents.extend(children)
        current = tuple(next_parents)
    return tuple(results)


def _checks_for_parent(
    parent_series: SeriesId, child_series: SeriesId, parent: Bar, children: tuple[Bar, ...]
) -> tuple[HierarchyCheckResult, ...]:
    """1本の親足についての検査1〜4。"""
    intervals = tuple(child.interval for child in children)
    gaps, overlaps = _coverage(parent.interval, intervals)
    coverage = HierarchyCheckResult(
        check="coverage",
        passed=bool(children) and not gaps and not overlaps,
        parent_series=parent_series,
        child_series=child_series,
        parent_bar=parent.key,
        expected_interval=parent.interval,
        child_intervals=intervals,
        coverage_gaps=gaps,
        coverage_overlaps=overlaps,
    )
    mismatched_basis = next(
        (child for child in children if child.series.basis is not parent.series.basis), None
    )
    basis = HierarchyCheckResult(
        check="price_basis",
        passed=mismatched_basis is None,
        parent_series=parent_series,
        child_series=child_series,
        parent_bar=parent.key,
        child_bar=None if mismatched_basis is None else mismatched_basis.key,
        expected_basis=parent.series.basis,
        observed_basis=(
            parent.series.basis if mismatched_basis is None else mismatched_basis.series.basis
        ),
    )
    boundary_child = None
    observed_boundary = parent.interval.start
    if children:
        observed_boundary = children[0].interval.start
        if children[0].interval.start != parent.interval.start:
            boundary_child = children[0]
        elif children[-1].interval.end != parent.interval.end:
            boundary_child = children[-1]
            observed_boundary = children[-1].interval.end
    boundary = HierarchyCheckResult(
        check="bar_boundary",
        passed=bool(children) and boundary_child is None,
        parent_series=parent_series,
        child_series=child_series,
        parent_bar=parent.key,
        child_bar=None if boundary_child is None else boundary_child.key,
        expected_boundary=parent.interval.end,
        observed_boundary=observed_boundary,
    )
    late_child = next(
        (child for child in children if child.available_at > parent.available_at), None
    )
    availability = HierarchyCheckResult(
        check="available_at",
        passed=late_child is None,
        parent_series=parent_series,
        child_series=child_series,
        parent_bar=parent.key,
        child_bar=None if late_child is None else late_child.key,
        expected_available_at=parent.available_at,
        observed_available_at=(
            parent.available_at if late_child is None else late_child.available_at
        ),
    )
    return (coverage, basis, boundary, availability)


def _coverage(
    parent: Interval, children: tuple[Interval, ...]
) -> tuple[tuple[Interval, ...], tuple[Interval, ...]]:
    """親の区間のうち子が覆っていない区間と、子どうしが重なった区間を求める。"""
    ordered = sorted(children, key=lambda interval: interval.start.value)
    gaps: list[Interval] = []
    overlaps: list[Interval] = []
    cursor = parent.start
    for interval in ordered:
        if interval.start > cursor:
            gaps.append(Interval(start=cursor, end=interval.start))
        elif interval.start < cursor:
            overlaps.append(Interval(start=interval.start, end=min(cursor, interval.end)))
        cursor = max(cursor, interval.end)
    if cursor < parent.end:
        gaps.append(Interval(start=cursor, end=parent.end))
    return tuple(gaps), tuple(overlaps)
