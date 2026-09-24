"""T02 再現生成器の不変条件と検算値（T02 §2・§16、D08 §9.6）。

| 確かめること | 出どころ |
|---|---|
| 日足は1時間足から集約し、T02 §2.2 の値になる | T02 §16 の不変条件1 |
| 遅延を当てても OHLC と区間は変わらず、`available_at` だけ動く | T02 §16 の不変条件2・D08 §9.6 |
| 15分足は集約に使わない（日足を直接作る経路が無い） | T02 §16 の不変条件3 |
| 日足は `01-06 22:00Z` に60本以上、15分足は `01-07 09:00Z` に60本以上ある | T02 §16 の不変条件4 |
| 生成器の足から EMA を計算すると 149.020 / 149.000 / 149.255 になる | T02 §2.4・§16 の検算値 |
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.schedule import DelayScenario, FixedSeriesDelay, InjectedBarDelay
from odyssey_fx.strategy.catalog.features import ema, extreme
from odyssey_fx.strategy.compiler.compiled import ResolvedMarketSource, ResolvedParameter
from odyssey_fx.strategy.declarations.refs import MarketDataField
from odyssey_fx.strategy.declarations.specs import IntValue, StrValue
from odyssey_fx.strategy.runtime.requests import ResolvedInputs, ValueSample, ValueWindow
from tests.fixtures.acceptance import t02_market
from tests.fixtures.acceptance.t02_market import EXPECTED
from tests.fixtures.synthetic import market

CALENDAR = market.calendar()
DAILY = t02_market.series_of(t02_market.DAILY_TIMEFRAME_ID)

JAN6_CLOSE = UtcTime.parse("2015-01-06T22:00:00Z")
JAN7_CLOSE = UtcTime.parse("2015-01-07T22:00:00Z")
CONFIRMATION_TIME = UtcTime.parse("2015-01-07T09:00:00Z")


def _bars_1h() -> tuple[Bar, ...]:
    return t02_market.bars_for("1h", market.TF_1H, CALENDAR)


def _bars_15m() -> tuple[Bar, ...]:
    return t02_market.bars_for("15m", market.TF_15M, CALENDAR)


def _daily() -> tuple[Bar, ...]:
    return t02_market.aggregate(_bars_1h(), market.TF_1D_NY17, CALENDAR)


def _confirmed_by(bars: tuple[Bar, ...], at: UtcTime) -> tuple[Bar, ...]:
    """判断時刻までに確定した足（区間の終端が判断時刻以下）を古い順に返す。"""
    return tuple(bar for bar in bars if bar.interval.end.value <= at.value)


def _ema_of(bars: tuple[Bar, ...]) -> object:
    window = bars[-EXPECTED.window_bars :]
    source = ResolvedMarketSource(bars[-1].series, MarketDataField.CLOSE)
    samples = tuple(
        ValueSample(payload=bar.close, source=source, freshness_time=bar.available_at)
        for bar in window
    )
    parameters = {
        "period": ResolvedParameter(
            name="period", value=IntValue(EXPECTED.period), unit=None, decimal_value=None
        ),
        "window_bars": ResolvedParameter(
            name="window_bars", value=IntValue(EXPECTED.window_bars), unit=None, decimal_value=None
        ),
    }
    result = ema.evaluate(
        ResolvedInputs(by_name={"prices": (ValueWindow(samples=samples),)}), parameters
    )
    return result.outputs["value"]


# --- 不変条件 ------------------------------------------------------------------


def test_the_daily_bars_are_aggregated_from_the_hourly_table() -> None:
    """T02 §2.2: D(Jan6) と D(Jan7) の4本値は1時間足の集約から出る。"""
    daily = {bar.bar_start: bar for bar in _daily()}
    jan6 = daily[UtcTime.parse("2015-01-05T22:00:00Z")]
    jan7 = daily[UtcTime.parse("2015-01-06T22:00:00Z")]

    assert [str(v) for v in (jan6.open, jan6.high, jan6.low, jan6.close)] == [
        "149.000",
        "149.300",
        "148.950",
        "149.210",
    ]
    assert [str(v) for v in (jan7.open, jan7.high, jan7.low, jan7.close)] == [
        "149.250",
        "149.520",
        "148.800",
        "148.810",
    ]
    assert jan6.available_at == JAN6_CLOSE
    assert jan6.series == DAILY


def test_the_generator_has_no_direct_daily_table() -> None:
    """T02 §16 の不変条件1・3: 日足を直接作る経路は無い。15分足は集約に使わない。"""
    with pytest.raises(AssertionError, match="aggregated from the 1h bars"):
        t02_market.bars_for("1d_ny17", market.TF_1D_NY17, CALENDAR)
    with pytest.raises(AssertionError, match="1h bars only"):
        t02_market.aggregate(_bars_15m(), market.TF_1D_NY17, CALENDAR)


def test_the_warmup_windows_are_covered() -> None:
    """T02 §16 の不変条件4: 日足は 01-06 22:00Z に60本以上、15分足は 01-07 09:00Z に60本以上。"""
    assert len(_confirmed_by(_daily(), JAN6_CLOSE)) >= 60
    assert len(_confirmed_by(_bars_15m(), CONFIRMATION_TIME)) >= 60


def test_the_default_window_is_the_generation_interval_not_the_run_interval() -> None:
    """T02 §16: 既定の生成区間はウォームアップの開始まで遡る。"""
    assert t02_market.GENERATION_INTERVAL.start < t02_market.RUN_INTERVAL.start
    assert t02_market.GENERATION_INTERVAL.end == t02_market.RUN_INTERVAL.end
    assert _bars_1h()[0].bar_start == UtcTime.parse("2014-09-30T22:00:00Z")


def test_the_generator_leaves_no_gap() -> None:
    """D08 §9.4 と同じ形: カレンダー上存在すべき足を1本も落とさない。"""
    for timeframe_id, definition in (("1h", market.TF_1H), ("15m", market.TF_15M)):
        expected = CALENDAR.expected_bar_starts(definition, t02_market.GENERATION_INTERVAL)
        bars = t02_market.bars_for(timeframe_id, definition, CALENDAR)
        assert [bar.bar_start for bar in bars] == list(expected)


def test_the_generator_is_deterministic() -> None:
    """D08 §9.2 の不変条件1: 同じ入力からは常に同じ足が出る。"""
    assert _bars_1h() == _bars_1h()
    assert t02_market.csv_text(_bars_15m()) == t02_market.csv_text(_bars_15m())


# --- 遅延の当て込み（D08 §9.6 の1・2） ----------------------------------------------

SCENARIOS = (
    DelayScenario(id="none", version=1, rules=()),
    DelayScenario(id="d1_2s", version=1, rules=(FixedSeriesDelay(DAILY, timedelta(seconds=2)),)),
    DelayScenario(id="d1_25h", version=1, rules=(FixedSeriesDelay(DAILY, timedelta(hours=25)),)),
    DelayScenario(
        id="d1_bar_hold",
        version=1,
        rules=(InjectedBarDelay(DAILY, JAN6_CLOSE, timedelta(hours=25)),),
    ),
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.id)
def test_a_delay_moves_only_the_availability(scenario: DelayScenario) -> None:
    """T02 §16 の不変条件2: 4ケースは同じ素の足を共有し、差は `available_at` だけ。"""
    raw = _daily()

    delayed = t02_market.apply_delay(raw, scenario)

    assert len(delayed) == len(raw)
    for before, after in zip(raw, delayed, strict=True):
        assert (after.series, after.interval, after.open, after.high, after.low, after.close) == (
            before.series,
            before.interval,
            before.open,
            before.high,
            before.low,
            before.close,
        )
        assert after.volume == before.volume
        assert after.provenance == before.provenance
        delay = scenario.delay_for(before.series, before.bar_start)
        assert after.available_at == before.available_at + delay


def test_the_raw_bars_are_not_changed_by_a_delay() -> None:
    """D08 §9.6 の2: 素の足は遅延を当てた後も元のまま（別のケースが共有できる）。"""
    raw = _daily()
    snapshot = tuple(bar.available_at for bar in raw)

    t02_market.apply_delay(raw, SCENARIOS[1])

    assert tuple(bar.available_at for bar in raw) == snapshot


def test_an_injected_delay_holds_back_one_bar_only() -> None:
    """D03 §3.6: 指定した1本だけが遅れる（ケース4）。"""
    raw = _daily()
    delayed = t02_market.apply_delay(raw, SCENARIOS[3])

    moved = [after for before, after in zip(raw, delayed, strict=True) if before != after]
    assert [bar.bar_start for bar in moved] == [JAN6_CLOSE]
    assert moved[0].available_at == UtcTime.parse("2015-01-08T23:00:00Z")


def test_a_daily_delay_leaves_the_hourly_bars_alone() -> None:
    """D03 §3.6: 系列の違う足には当たらない。"""
    raw = _bars_1h()
    assert t02_market.apply_delay(raw, SCENARIOS[2]) == raw


# --- 検算値（T02 §2.4・§16） -------------------------------------------------------


def test_the_daily_ema_reproduces_the_paper_trace() -> None:
    """T02 §2.4: `01-06 22:00Z` は 149.020、`01-07 22:00Z` は 149.000。"""
    daily = _daily()

    assert _ema_of(_confirmed_by(daily, JAN6_CLOSE)) == EXPECTED.daily_ema_jan6
    assert _ema_of(_confirmed_by(daily, JAN7_CLOSE)) == EXPECTED.daily_ema_jan7


def test_the_fifteen_minute_ema_reproduces_the_paper_trace() -> None:
    """T02 §2.4: `01-07 09:00Z` は 149.255（末尾が確認の開始足 149.350）。"""
    confirmed = _confirmed_by(_bars_15m(), CONFIRMATION_TIME)

    assert str(confirmed[-1].close) == "149.350"
    assert _ema_of(confirmed) == EXPECTED.m15_ema_at_0900


def test_the_stop_level_reproduces_the_paper_trace() -> None:
    """T02 §2.4: `01-07 09:00Z` の安値20本（当該足を除く）の最小は 148.950。"""
    confirmed = _confirmed_by(_bars_1h(), CONFIRMATION_TIME)
    window = confirmed[-21:-1]
    source = ResolvedMarketSource(window[-1].series, MarketDataField.LOW)
    samples = tuple(
        ValueSample(payload=bar.low, source=source, freshness_time=bar.available_at)
        for bar in window
    )
    result = extreme.evaluate(
        ResolvedInputs(by_name={"prices": (ValueWindow(samples=samples),)}),
        {
            "lookback": ResolvedParameter(
                name="lookback", value=IntValue(20), unit=None, decimal_value=None
            ),
            "mode": ResolvedParameter(
                name="mode", value=StrValue("MIN"), unit=None, decimal_value=None
            ),
        },
    )

    assert str(result.outputs["level"]) == "148.950"
