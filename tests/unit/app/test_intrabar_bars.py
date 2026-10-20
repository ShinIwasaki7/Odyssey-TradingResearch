"""下位足の供給（`app.composition._IntrabarBars`）の二分探索が、全件の走査と同じ足を返すこと。

`bars_in` は「区間に**収まる**下位足」（D06 §7.4）を返す。以前は系列の全件を先頭から走査して
いた（実データの 1 分足では 15 分足 1 本ごとに約 260 万本）。二分探索に替えても、返す足と並びは
全件の走査と同じでなければならない。足が開始時刻の昇順に並んでいない入力は、構築時に止める
（高速化の PR の人間の決定 4）。

人工データだけを使う（価格は人工の値）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.app.composition import _IntrabarBars
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.backtest.harness import EXECUTION_SERIES, bars
from tests.fixtures.mt5.run import MINUTE_SERIES

START = UtcTime.parse("2026-01-12T22:00:00Z")
SPEC = ("150.000", "150.100", "149.900", "150.000")


def _minutes(count: int, *, holes: frozenset[int] = frozenset()) -> tuple[Bar, ...]:
    """`START` から連続する 1 分足（`holes` の位置の分は作らない）。"""
    made = bars(MINUTE_SERIES, START, timedelta(minutes=1), [SPEC] * count)
    return tuple(bar for index, bar in enumerate(made) if index not in holes)


def _linear(source: tuple[Bar, ...], interval: Interval) -> tuple[Bar, ...]:
    """全件を先頭から走査する参照実装（高速化前の `bars_in` と同じ条件）。"""
    return tuple(
        bar for bar in source if interval.start <= bar.bar_start and bar.bar_end <= interval.end
    )


def _at(minutes: float) -> UtcTime:
    return START + timedelta(minutes=minutes)


MINUTES = _minutes(120, holes=frozenset({5, 6, 7, 30, 59, 60, 119}))
QUARTERS = bars(EXECUTION_SERIES, START, timedelta(minutes=15), [SPEC] * 8)
SUPPLY = _IntrabarBars(bars={MINUTE_SERIES: MINUTES, EXECUTION_SERIES: QUARTERS})

INTERVALS = [
    pytest.param(Interval(start=_at(0), end=_at(15)), id="exact-15m-bar"),
    pytest.param(Interval(start=_at(15), end=_at(30)), id="exact-next-bar"),
    pytest.param(Interval(start=_at(0), end=_at(120)), id="whole-range"),
    pytest.param(Interval(start=_at(5), end=_at(8)), id="only-holes"),
    pytest.param(Interval(start=_at(4), end=_at(9)), id="around-holes"),
    pytest.param(Interval(start=_at(0.5), end=_at(3.5)), id="mid-minute-bounds"),
    pytest.param(Interval(start=_at(10), end=_at(10.5)), id="shorter-than-a-bar"),
    pytest.param(Interval(start=_at(-60), end=_at(0)), id="before-all"),
    pytest.param(Interval(start=_at(-60), end=_at(2)), id="overlapping-the-start"),
    pytest.param(Interval(start=_at(118), end=_at(240)), id="overlapping-the-end"),
    pytest.param(Interval(start=_at(120), end=_at(240)), id="after-all"),
    pytest.param(Interval(start=_at(7.5), end=_at(22.5)), id="15m-bars-cut-in-half"),
]


@pytest.mark.parametrize("interval", INTERVALS)
@pytest.mark.parametrize("series", [MINUTE_SERIES, EXECUTION_SERIES], ids=["1m", "15m"])
def test_bars_in_returns_what_a_full_scan_returns(series: SeriesId, interval: Interval) -> None:
    source = MINUTES if series == MINUTE_SERIES else QUARTERS
    assert SUPPLY.bars_in(series, interval) == _linear(source, interval)


def test_a_series_that_is_not_supplied_returns_no_bars() -> None:
    supply = _IntrabarBars(bars={MINUTE_SERIES: MINUTES})
    assert supply.bars_in(EXECUTION_SERIES, Interval(start=_at(0), end=_at(15))) == ()


def test_an_empty_series_returns_no_bars() -> None:
    supply = _IntrabarBars(bars={MINUTE_SERIES: ()})
    assert supply.bars_in(MINUTE_SERIES, Interval(start=_at(0), end=_at(15))) == ()


def test_every_15m_window_of_the_run_matches_a_full_scan() -> None:
    """解像度階層の適合検査と同じ使い方（15 分足の区間ごと）で、全件の走査と一致する。"""
    for parent in QUARTERS:
        assert SUPPLY.bars_in(MINUTE_SERIES, parent.interval) == _linear(MINUTES, parent.interval)


def test_bars_with_the_same_start_are_kept_in_order() -> None:
    """同じ開始時刻の足が並んでいても（昇順は崩れていない）、全件の走査と同じ足を返す。"""
    doubled = tuple(sorted((*MINUTES[:10], *MINUTES[:10]), key=lambda bar: bar.bar_start.value))
    supply = _IntrabarBars(bars={MINUTE_SERIES: doubled})
    interval = Interval(start=_at(1), end=_at(4))
    assert supply.bars_in(MINUTE_SERIES, interval) == _linear(doubled, interval)


def test_bars_out_of_ascending_order_are_refused_at_construction() -> None:
    """開始時刻の昇順が崩れた入力は、構築時に例外で止める（黙って違う足を返さない）。"""
    shuffled = (MINUTES[1], MINUTES[0], *MINUTES[2:])
    with pytest.raises(MarketDataValueError, match="not in ascending order of bar_start"):
        _IntrabarBars(bars={MINUTE_SERIES: shuffled})


def test_the_supply_does_not_follow_later_changes_to_the_given_mapping() -> None:
    """渡した辞書を後から書き換えても、構築時の足と索引の組から読む。"""
    given = {MINUTE_SERIES: MINUTES}
    supply = _IntrabarBars(bars=given)
    given[MINUTE_SERIES] = MINUTES[::-1]
    interval = Interval(start=_at(0), end=_at(15))
    assert supply.bars_in(MINUTE_SERIES, interval) == _linear(MINUTES, interval)
