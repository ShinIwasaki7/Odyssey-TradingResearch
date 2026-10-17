"""解像度階層の適合検査の MT5 経路の読み方（D06 §18.2、D12-110。人間の決定の原文は D12 §0.1.30）
。"""

from __future__ import annotations

from datetime import timedelta

from odyssey_fx.backtest.domain.policies import ResolutionHierarchy
from odyssey_fx.backtest.execution.protection_hits import hierarchy_checks
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.backtest.harness import EXECUTION_SERIES, bars
from tests.fixtures.synthetic import market

MINUTE = market.series(timeframe_id="1m")
START = UtcTime.parse("2026-01-23T11:00:00Z")


def _checks(excused: bool) -> dict[str, bool]:
    parent = bars(EXECUTION_SERIES, START, timedelta(minutes=15), [("150", "150", "150", "150")])
    children = bars(
        MINUTE,
        START + timedelta(minutes=1),
        timedelta(minutes=1),
        [("150", "150", "150", "150")] * 14,
    )
    hole = (Interval(start=START, end=START + timedelta(minutes=1)),)

    def child_bars(series: SeriesId, interval: Interval) -> tuple:  # type: ignore[type-arg]
        return tuple(b for b in children if interval.start <= b.bar_start < interval.end)

    results = hierarchy_checks(
        ResolutionHierarchy(levels=(EXECUTION_SERIES, MINUTE)),
        parent,
        child_bars,
        excused=(lambda series, interval: hole) if excused else None,
    )
    return {result.check: result.passed for result in results}


def test_an_unrecorded_first_minute_passes_the_boundary_check_on_the_mt5_route() -> None:
    """D12-110 (a): 最初の分 11:00 が無記録で 1 分足が 11:01〜11:14 のとき、MT5 経路では検査
    1（被覆）と
    検査 3（足境界）が合格。従来の読み方（無記録を知らない）では両方とも不合格のまま。"""
    mt5 = _checks(excused=True)
    assert mt5["coverage"] and mt5["bar_boundary"]
    plain = _checks(excused=False)
    assert not plain["coverage"] and not plain["bar_boundary"]
