"""汎用人工データ生成器の gap と SL/TP 同時到達の入り口（D08 §9.3）。

`tests/fixtures/synthetic/market.py` の `make_bars(..., gaps=..., straddles=...)` が、D08 §9.3.1・
§9.3.2 の効果と不変条件、§9.3.3 の共通規則（既定は空・適用順 gap → straddle）を守ることを
1件ずつ確かめる。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.money import Price, PriceOffset, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from tests.fixtures.synthetic import market

SERIES = market.series()
CALENDAR = market.calendar()

#: 火曜 00:00Z から6時間（週の途中なので6本すべて存在する）。
WINDOW = Interval(
    start=UtcTime.from_components(2026, 1, 6, 0, 0),
    end=UtcTime.from_components(2026, 1, 6, 6, 0),
)
THIRD = WINDOW.start + timedelta(hours=2)
FOURTH = WINDOW.start + timedelta(hours=3)


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _offset(text: str) -> PriceOffset:
    return PriceOffset(decimal_from_str(text))


def _plain() -> tuple[Bar, ...]:
    return market.make_bars(SERIES, market.TF_1H, CALENDAR, WINDOW)


def _by_start(bars: tuple[Bar, ...]) -> dict[UtcTime, Bar]:
    return {bar.bar_start: bar for bar in bars}


# --- 共通規則（D08 §9.3.3） --------------------------------------------------


def test_without_gaps_or_straddles_the_bars_are_unchanged() -> None:
    """D08 §9.3.3: どちらも既定は空。指定しなければ従来と同じ足が出る。"""
    assert market.make_bars(SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={}, straddles={}) == (
        _plain()
    )


# --- gap（D08 §9.3.1） -------------------------------------------------------


def test_a_gap_places_the_open_at_the_previous_close_plus_the_offset() -> None:
    """D08 §9.3.1: その足の始値を「直前の足の終値 ＋ 指定した差」に置く。"""
    bars = market.make_bars(SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={THIRD: _offset("-1.250")})
    index = [bar.bar_start for bar in bars].index(THIRD)

    assert bars[index].open == bars[index - 1].close + _offset("-1.250")


def test_bars_after_a_gap_continue_from_the_new_level() -> None:
    """D08 §9.3.1: 以降の足は新しい水準から続く（1本だけ飛ばして戻る形にしない）。"""
    plain = _by_start(_plain())
    gapped = _by_start(
        market.make_bars(SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={THIRD: _offset("2.000")})
    )
    shift = gapped[THIRD].open - plain[THIRD].open

    for start, bar in gapped.items():
        expected = shift if start >= THIRD else _offset("0")
        assert bar.open - plain[start].open == expected
        assert bar.high - plain[start].high == expected
        assert bar.low - plain[start].low == expected
        assert bar.close - plain[start].close == expected


def test_a_gap_keeps_the_ohlc_consistent() -> None:
    """D08 §9.3.1 不変条件1: `low <= min(open, close)` かつ `max(open, close) <= high`。"""
    bars = market.make_bars(SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={THIRD: _offset("-5.000")})

    for bar in bars:
        assert bar.low <= min(bar.open, bar.close)
        assert max(bar.open, bar.close) <= bar.high


def test_a_gap_moves_neither_the_interval_nor_the_availability() -> None:
    """D08 §9.3.1 不変条件2: 対象区間と `available_at` は変えない。"""
    plain = _plain()
    gapped = market.make_bars(
        SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={THIRD: _offset("1.000")}
    )

    assert [(bar.interval, bar.available_at) for bar in gapped] == [
        (bar.interval, bar.available_at) for bar in plain
    ]


def test_a_gap_at_a_time_without_a_bar_is_rejected() -> None:
    """D08 §9.3.1 不変条件3: カレンダーが区間を持たない時刻（週末）は拒否する。"""
    saturday = UtcTime.from_components(2026, 1, 10, 12, 0)
    window = Interval(start=saturday - timedelta(days=1), end=saturday + timedelta(days=2))

    with pytest.raises(ValueError, match="not the start of a bar"):
        market.make_bars(SERIES, market.TF_1H, CALENDAR, window, gaps={saturday: _offset("1")})


def test_a_gap_outside_the_window_is_rejected() -> None:
    """D08 §9.3.1 不変条件3: 作らない足に付けた gap を黙って捨てない。"""
    with pytest.raises(ValueError, match="not the start of a bar"):
        market.make_bars(
            SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={WINDOW.end: _offset("1.000")}
        )


def test_a_gap_on_a_skipped_bar_is_rejected() -> None:
    """D08 §9.3.1 不変条件3: `skip_starts` で落とした足の時刻も拒否する。"""
    with pytest.raises(ValueError, match="skip_starts"):
        market.make_bars(
            SERIES,
            market.TF_1H,
            CALENDAR,
            WINDOW,
            skip_starts=[THIRD],
            gaps={THIRD: _offset("1.000")},
        )


def test_a_zero_gap_is_rejected() -> None:
    """D08 §9.3.1 不変条件4: 差が 0 の指定は拒否する（飛んでいない gap のテストを作らせない）。"""
    with pytest.raises(ValueError, match="non-zero"):
        market.make_bars(SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={THIRD: _offset("0")})


def test_a_gap_on_the_first_bar_is_rejected() -> None:
    """D08 §9.3.1: 差の起点は直前の足の終値なので、列の先頭の足には gap を置けない。"""
    with pytest.raises(ValueError, match="first bar"):
        market.make_bars(
            SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={WINDOW.start: _offset("1.000")}
        )


def test_a_weekend_gap_starts_from_the_last_close_of_the_week() -> None:
    """D08 §9.3.1 週末との関係: 週明けの最初の足の gap は、金曜最後の足の終値から飛ぶ。"""
    friday_last = UtcTime.from_components(2026, 1, 9, 21, 0)
    reopen = UtcTime.from_components(2026, 1, 11, 22, 0)
    window = Interval(start=friday_last, end=reopen + timedelta(hours=1))

    bars = market.make_bars(SERIES, market.TF_1H, CALENDAR, window, gaps={reopen: _offset("-2")})

    assert [bar.bar_start for bar in bars] == [friday_last, reopen]
    assert bars[1].open == bars[0].close + _offset("-2")


# --- SL/TP 同時到達（D08 §9.3.2） -------------------------------------------


def test_a_straddle_widens_the_bar_to_contain_both_prices() -> None:
    """D08 §9.3.2: 高値を2値の高い方以上、安値を低い方以下に広げる（順不同）。"""
    bars = _by_start(
        market.make_bars(
            SERIES,
            market.TF_1H,
            CALENDAR,
            WINDOW,
            straddles={THIRD: (_price("152.000"), _price("148.000"))},
        )
    )

    assert bars[THIRD].high >= _price("152.000")
    assert bars[THIRD].low <= _price("148.000")


def test_a_straddle_keeps_the_ohlc_consistent() -> None:
    """D08 §9.3.2 不変条件1: 広げた足も OHLC の整合を満たす。"""
    bars = market.make_bars(
        SERIES,
        market.TF_1H,
        CALENDAR,
        WINDOW,
        straddles={THIRD: (_price("150.300"), _price("150.400"))},
    )

    for bar in bars:
        assert bar.low <= min(bar.open, bar.close)
        assert max(bar.open, bar.close) <= bar.high


def test_a_straddle_moves_neither_the_open_nor_the_close() -> None:
    """D08 §9.3.2 不変条件2: 始値と終値は動かさない。他の足も変えない。"""
    plain = _by_start(_plain())
    straddled = _by_start(
        market.make_bars(
            SERIES,
            market.TF_1H,
            CALENDAR,
            WINDOW,
            straddles={THIRD: (_price("148.000"), _price("152.000"))},
        )
    )

    assert straddled[THIRD].open == plain[THIRD].open
    assert straddled[THIRD].close == plain[THIRD].close
    assert straddled[THIRD].interval == plain[THIRD].interval
    assert straddled[THIRD].available_at == plain[THIRD].available_at
    assert {start: bar for start, bar in straddled.items() if start != THIRD} == {
        start: bar for start, bar in plain.items() if start != THIRD
    }


def test_a_straddle_with_equal_prices_is_rejected() -> None:
    """D08 §9.3.2 不変条件3: 2値が等しい指定は拒否する（同時到達にならない）。"""
    with pytest.raises(ValueError, match="two different prices"):
        market.make_bars(
            SERIES,
            market.TF_1H,
            CALENDAR,
            WINDOW,
            straddles={THIRD: (_price("150.000"), _price("150.000"))},
        )


def test_a_straddle_on_a_skipped_bar_is_rejected() -> None:
    """D08 §9.3.2 不変条件4: `skip_starts` で落とした足には付けられない。"""
    with pytest.raises(ValueError, match="skip_starts"):
        market.make_bars(
            SERIES,
            market.TF_1H,
            CALENDAR,
            WINDOW,
            skip_starts=[THIRD],
            straddles={THIRD: (_price("148.000"), _price("152.000"))},
        )


def test_a_straddle_at_a_time_without_a_bar_is_rejected() -> None:
    """D08 §9.3.2 不変条件4: 区間の無い時刻には付けられない。"""
    off_grid = THIRD + timedelta(minutes=30)
    with pytest.raises(ValueError, match="not the start of a bar"):
        market.make_bars(
            SERIES,
            market.TF_1H,
            CALENDAR,
            WINDOW,
            straddles={off_grid: (_price("148.000"), _price("152.000"))},
        )


# --- 両方を同じ足に（D08 §9.3.3） ---------------------------------------------


def test_a_gap_is_applied_before_a_straddle_on_the_same_bar() -> None:
    """D08 §9.3.3: 適用順は gap → straddle。gap が始値を動かし、straddle が幅を広げる。"""
    gap_only = _by_start(
        market.make_bars(SERIES, market.TF_1H, CALENDAR, WINDOW, gaps={THIRD: _offset("-3.000")})
    )
    both = _by_start(
        market.make_bars(
            SERIES,
            market.TF_1H,
            CALENDAR,
            WINDOW,
            gaps={THIRD: _offset("-3.000")},
            straddles={THIRD: (_price("140.000"), _price("160.000"))},
        )
    )

    assert both[THIRD].open == gap_only[THIRD].open
    assert both[THIRD].close == gap_only[THIRD].close
    assert both[THIRD].high == _price("160.000")
    assert both[THIRD].low == _price("140.000")
    assert both[FOURTH] == gap_only[FOURTH]
