"""汎用人工データ生成器の gap と SL/TP 同時到達のプロパティテスト（D08 §9.3）。

確かめること（どの差・どの2値・どの足に指定しても成り立つ性質）:

- **OHLC の整合**: `low <= min(open, close)` かつ `max(open, close) <= high`（D08 §9.2 の2）
- **決定論**: 同じ入力からは常に同じ足が出る（D08 §9.2 の1、§9.3.3）
- **時刻を動かさない**: 対象区間と `available_at` は指定の有無で変わらない（§9.3.1 不変条件2）
- **straddle は始値・終値を動かさず、2値を包む**（§9.3.2）
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from odyssey_fx.common.money import Price, PriceOffset
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from tests.fixtures.synthetic import market

SERIES = market.series()
CALENDAR = market.calendar()

#: 金曜から翌週火曜まで（週末をまたぐので、週明けの足も gap の対象に入る）。
WINDOW = Interval(
    start=UtcTime.from_components(2026, 1, 9, 12, 0),
    end=UtcTime.from_components(2026, 1, 13, 0, 0),
)
STARTS = CALENDAR.expected_bar_starts(market.TF_1H, WINDOW)

#: gap の差（0 を除く。±20.000 の範囲で千分の1刻み）。価格が正のまま収まる幅にする。
_offsets = (
    st.integers(min_value=-20_000, max_value=20_000)
    .filter(lambda value: value != 0)
    .map(lambda value: PriceOffset(Decimal(value).scaleb(-3)))
)
_prices = st.integers(min_value=100_000, max_value=200_000).map(
    lambda value: Price(Decimal(value).scaleb(-3))
)
_straddle_values = st.tuples(_prices, _prices).filter(lambda pair: pair[0] != pair[1])

_gaps = st.dictionaries(st.sampled_from(STARTS[1:]), _offsets, max_size=4)
_straddles = st.dictionaries(st.sampled_from(STARTS), _straddle_values, max_size=4)


def _make(
    gaps: dict[UtcTime, PriceOffset], straddles: dict[UtcTime, tuple[Price, Price]]
) -> tuple[Bar, ...]:
    return market.make_bars(SERIES, market.TF_1H, CALENDAR, WINDOW, gaps=gaps, straddles=straddles)


@given(_gaps, _straddles)
@settings(max_examples=60, deadline=None)
def test_every_generated_bar_keeps_the_ohlc_consistent(
    gaps: dict[UtcTime, PriceOffset], straddles: dict[UtcTime, tuple[Price, Price]]
) -> None:
    """どの gap・straddle を指定しても、すべての足が OHLC の整合を満たす。"""
    for bar in _make(gaps, straddles):
        assert bar.low <= min(bar.open, bar.close)
        assert max(bar.open, bar.close) <= bar.high


@given(_gaps, _straddles)
@settings(max_examples=40, deadline=None)
def test_the_same_input_gives_the_same_bars(
    gaps: dict[UtcTime, PriceOffset], straddles: dict[UtcTime, tuple[Price, Price]]
) -> None:
    """同じ入力を2回与えると、同じ足の列が出る（乱数を使わない）。"""
    assert _make(gaps, straddles) == _make(dict(gaps), dict(straddles))


@given(_gaps, _straddles)
@settings(max_examples=40, deadline=None)
def test_gaps_and_straddles_do_not_move_the_intervals_or_the_availability(
    gaps: dict[UtcTime, PriceOffset], straddles: dict[UtcTime, tuple[Price, Price]]
) -> None:
    """gap も straddle も値の話であり、対象区間と `available_at` は変えない。"""
    plain = _make({}, {})
    changed = _make(gaps, straddles)

    assert [(bar.interval, bar.available_at) for bar in changed] == [
        (bar.interval, bar.available_at) for bar in plain
    ]


@given(_gaps, _straddles)
@settings(max_examples=40, deadline=None)
def test_a_straddle_contains_both_prices_without_moving_the_open_or_the_close(
    gaps: dict[UtcTime, PriceOffset], straddles: dict[UtcTime, tuple[Price, Price]]
) -> None:
    """straddle を足しても始値・終値は gap だけの足と同じで、指定した2値を包む。"""
    gap_only = {bar.bar_start: bar for bar in _make(gaps, {})}

    for bar in _make(gaps, straddles):
        before = gap_only[bar.bar_start]
        assert (bar.open, bar.close) == (before.open, before.close)
        if bar.bar_start in straddles:
            first, second = straddles[bar.bar_start]
            assert bar.low <= min(first, second)
            assert max(first, second) <= bar.high
        else:
            assert bar == before


@given(st.sampled_from(STARTS[1:]), _offsets)
@settings(max_examples=40, deadline=None)
def test_a_gap_opens_at_the_previous_close_plus_the_offset(
    at: UtcTime, offset: PriceOffset
) -> None:
    """gap を置いた足の始値は、直前の足の終値 ＋ 指定した差になる。"""
    bars = _make({at: offset}, {})
    index = [bar.bar_start for bar in bars].index(at)

    assert bars[index].open == bars[index - 1].close + offset
