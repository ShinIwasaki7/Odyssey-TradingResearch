"""局面の付与（研究ポリシー第 5 版の設計 D24 §5、仮置き 12〜15。実装 PR 4-3）。

- 区分の関数 `label_daily_bars`: 十進数の EMA・ATR・中央値で、向き 3 区分 × 値動き 2 区分の
  6 局面と `UNLABELED`（準備不足・ATR 0）を付ける。後の日足を読まない。
- 参照時点の局面 `RegimeLookup`: 公開時刻 `available_at` で日足を選ぶ（足の終わりでは選ばない）。
- 損益の振り分けと整合 `build_regime_report`: 局面の損益の和が経路の純損益と一致すれば表を出し、
  ずらした入力では表を出さず理由を出す。

受入テスト RP5-13（区分の関数と先読みの無さ）と RP5-14（整合）。日足・取引はすべて人工で、
価格・成績を読まない。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Final

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import PositionId
from odyssey_fx.common.money import CurrencyCode, Money, Price, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import MetricUnavailableReason
from odyssey_fx.evaluation.domain.path import PathTradeKind
from odyssey_fx.evaluation.domain.regimes import (
    REGIME_TABLE_ORDER,
    DailyRegime,
    RegimeDirectionRule,
    RegimeLabel,
    RegimeLabelAt,
    RegimeLookup,
    RegimeReport,
    RegimeReportStatus,
    RegimeRole,
    RegimeSpec,
    RegimeTimeframe,
    RegimeTrade,
    RegimeVolatilityRule,
    UnlabeledReason,
    build_regime_report,
    label_daily_bars,
    trading_day_spans,
)
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from tests.fixtures.synthetic import market

JPY: Final = CurrencyCode("JPY")
DAILY: Final = market.series(timeframe_id="1d_ny17")
#: 人工の日足の最初の足の始まり（ニューヨーク 17 時。曜日は区分の関数に効かない）。
START: Final = UtcTime.parse("2020-01-05T22:00:00Z")


def _spec(
    *,
    ema: int = 2,
    slope: int = 1,
    atr: int = 1,
    band: str = "0.5",
    vol_atr: int = 1,
    window: int = 1,
) -> RegimeSpec:
    return RegimeSpec(
        role=RegimeRole.DISPLAY_ONLY,
        timeframe=RegimeTimeframe.DAILY,
        direction=RegimeDirectionRule(
            ema_bars=ema,
            slope_bars=slope,
            atr_bars=atr,
            dead_band_atr_multiple=decimal_from_str(band),
        ),
        volatility=RegimeVolatilityRule(atr_bars=vol_atr, median_window_bars=window),
        label_at=RegimeLabelAt.LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY,
    )


def _bar(index: int, close: str, half_range: str = "1", delay: timedelta | None = None) -> Bar:
    """`index` 日目の日足（始値 = 終値、高値・安値は終値の ±`half_range`）。"""
    start = START + timedelta(days=index)
    interval = Interval(start=start, end=start + timedelta(days=1))
    value = decimal_from_str(close)
    width = decimal_from_str(half_range)
    return Bar(
        series=DAILY,
        interval=interval,
        open=Price(value),
        high=Price(value + width),
        low=Price(value - width),
        close=Price(value),
        volume=Decimal(0),
        available_at=interval.end if delay is None else interval.end + delay,
        provenance=Provenance(kind=ProvenanceKind.HISTDATA, source_ref="test"),
    )


def _bars(closes: list[str], half_ranges: list[str] | None = None) -> tuple[Bar, ...]:
    ranges = half_ranges or ["1"] * len(closes)
    return tuple(
        _bar(index, close, width)
        for index, (close, width) in enumerate(zip(closes, ranges, strict=True))
    )


# --- RP5-13: 区分の関数 -------------------------------------------------------------


def test_rp5_13_the_boundary_of_the_dead_band_counts_as_a_trend() -> None:
    """手で求めた値: 終値 100・101・102、真の値幅 2、EMA 2 本は 100.5 → 101.5、傾き 1 本で
    `g = (101.5 − 100.5) ÷ 2 = 0.5`。不感帯 0.5 ちょうどは上昇。値動きは 1 個の中央値と同じで小。"""
    labels = label_daily_bars(_bars(["100", "101", "102"]), _spec())
    assert [item.label for item in labels] == [
        RegimeLabel.UNLABELED,
        RegimeLabel.UNLABELED,
        RegimeLabel.UP_LOW,
    ]
    assert [item.reason for item in labels[:2]] == [UnlabeledReason.WARMUP_INSUFFICIENT] * 2
    falling = label_daily_bars(_bars(["102", "101", "100"]), _spec())
    assert falling[-1].label is RegimeLabel.DOWN_LOW


def test_rp5_13_the_six_regimes_are_labelled() -> None:
    """向き（上昇・横ばい・下降）× 値動き（直前 2 個の中央値より上か）の 6 局面が出る。"""
    spec = _spec(window=2)
    cases = {
        RegimeLabel.UP_HIGH: (["100", "102", "104"], ["0.5", "0.5", "1"]),
        RegimeLabel.UP_LOW: (["100", "102", "104"], ["1", "1", "0.5"]),
        RegimeLabel.FLAT_HIGH: (["100", "100", "100"], ["1", "1", "3"]),
        RegimeLabel.FLAT_LOW: (["100", "100", "100"], ["3", "3", "1"]),
        RegimeLabel.DOWN_HIGH: (["104", "102", "100"], ["0.5", "0.5", "1"]),
        RegimeLabel.DOWN_LOW: (["104", "102", "100"], ["1", "1", "0.5"]),
    }
    for expected, (closes, ranges) in cases.items():
        assert label_daily_bars(_bars(closes, ranges), spec)[-1].label is expected, expected


def test_rp5_13_warmup_and_a_zero_atr_are_unlabelled_with_reasons() -> None:
    spec = _spec(ema=3, slope=2, atr=2, vol_atr=2, window=3)
    closes = [str(100 + index) for index in range(8)]
    labels = label_daily_bars(_bars(closes), spec)
    # 準備: EMA 3 本（位置 2）＋ 傾き 2 本 → 位置 4、値動きの中央値 3 個（ATR 2 本の後）→ 位置 3。
    assert [item.reason for item in labels[:4]] == [UnlabeledReason.WARMUP_INSUFFICIENT] * 4
    assert all(item.label is not RegimeLabel.UNLABELED for item in labels[4:])
    flat = label_daily_bars(_bars(["100"] * 5, ["0"] * 5), _spec(window=2))
    assert [item.reason for item in flat[2:]] == [UnlabeledReason.ZERO_ATR] * 3


def test_rp5_13_a_label_does_not_depend_on_later_bars() -> None:
    """各日足の局面は、その日足までの日足だけで決まる（後の日足を足しても変わらない）。"""
    spec = _spec(ema=3, slope=2, atr=2, vol_atr=2, window=3)
    closes = ["100", "103", "101", "99", "104", "108", "102", "97", "96", "101"]
    ranges = ["1", "2", "1", "3", "1", "2", "4", "1", "2", "1"]
    bars = _bars(closes, ranges)
    whole = label_daily_bars(bars, spec)
    for end in range(1, len(bars) + 1):
        assert label_daily_bars(bars[:end], spec) == whole[:end]


def test_rp5_13_the_spec_values_of_the_design_need_about_270_bars() -> None:
    """D24 の値（EMA 50・傾き 20・ATR 20・中央値 250）: 位置 268 で初めて付く（約 270 本）。"""
    spec = _spec(ema=50, slope=20, atr=20, vol_atr=20, window=250)
    closes = [str(100 + index % 7) for index in range(300)]
    labels = label_daily_bars(_bars(closes), spec)
    first = next(index for index, item in enumerate(labels) if item.reason is None)
    assert first == 268


def test_the_function_refuses_unsorted_or_mixed_bars() -> None:
    bars = _bars(["100", "101"])
    with pytest.raises(KernelValueError):
        label_daily_bars((bars[1], bars[0]), _spec())


# --- RP5-13: 公開時刻で日足を選ぶ -------------------------------------------------


def _daily(
    index: int, delay_hours: int = 0, label: RegimeLabel = RegimeLabel.UP_LOW
) -> DailyRegime:
    start = START + timedelta(days=index)
    return DailyRegime(
        bar_start=start,
        available_at=start + timedelta(days=1, hours=delay_hours),
        label=label,
        reason=None,
    )


def test_rp5_13_a_bar_ended_but_not_yet_published_is_not_used() -> None:
    """`bar_end ≤ 入場の時点 < available_at` の日足は使わず、1 本前の公開済みの日足で付ける。"""
    lookup = RegimeLookup(
        [
            _daily(0, label=RegimeLabel.DOWN_HIGH),
            _daily(1, delay_hours=3, label=RegimeLabel.UP_HIGH),
        ]
    )
    bar_end = START + timedelta(days=2)
    before = lookup.at(bar_end + timedelta(hours=1))
    assert before.label is RegimeLabel.DOWN_HIGH
    assert before.daily_bar_start == START
    after = lookup.at(bar_end + timedelta(hours=3))
    assert after.label is RegimeLabel.UP_HIGH


def test_rp5_13_a_bar_published_before_an_earlier_delayed_bar_waits_for_it() -> None:
    """遅延で公開の順が前後したら、前の日足が公開されるまで後の日足の局面も使わない（仮置き）。"""
    lookup = RegimeLookup(
        [
            _daily(0, label=RegimeLabel.FLAT_LOW),
            _daily(1, delay_hours=30, label=RegimeLabel.DOWN_LOW),
            _daily(2, label=RegimeLabel.UP_HIGH),
        ]
    )
    moment = START + timedelta(days=3, hours=1)  # 日足 2 は公開済み、日足 1 はまだ
    assert lookup.at(moment).label is RegimeLabel.FLAT_LOW


def test_rp5_13_before_the_first_published_bar_there_is_no_label() -> None:
    found = RegimeLookup([_daily(0)]).at(START + timedelta(hours=1))
    assert found.label is RegimeLabel.UNLABELED
    assert found.reason is UnlabeledReason.NO_PUBLISHED_BAR
    assert found.daily_bar_start is None


# --- RP5-14: 損益の振り分けと整合 ------------------------------------------------------


def _yen(text: str) -> Money:
    return Money(decimal_from_str(text), JPY)


def _trade(
    seq: int, day: int, profit: str, kind: PathTradeKind = PathTradeKind.CLOSED_TRADE
) -> RegimeTrade:
    return RegimeTrade(
        segment_index=0,
        kind=kind,
        position_id=PositionId(seq=seq),
        entry_at=START + timedelta(days=day, hours=2),
        profit=_yen(profit),
    )


#: 日足 0〜3 の局面（日足 k は k+1 日目の始まりに公開。取引は k+1 日目の 2 時に入場）。
DAILY_LABELS: Final = (
    _daily(0, label=RegimeLabel.UP_HIGH),
    _daily(1, label=RegimeLabel.UP_HIGH),
    _daily(2, label=RegimeLabel.DOWN_LOW),
    _daily(3, label=RegimeLabel.FLAT_LOW),
)

TRADES: Final = (
    _trade(1, 0, "500"),  # 公開済みの日足なし → UNLABELED
    _trade(2, 1, "1000"),  # 日足 0 → UP_HIGH
    _trade(3, 2, "-400"),  # 日足 1 → UP_HIGH
    _trade(4, 3, "-300"),  # 日足 2 → DOWN_LOW
    _trade(5, 4, "200", PathTradeKind.SEGMENT_END_SETTLEMENT),  # 日足 3 → FLAT_LOW
)


def _days() -> tuple[Interval, ...]:
    return tuple(
        Interval(start=START + timedelta(days=index), end=START + timedelta(days=index + 1))
        for index in range(5)
    )


def _report(net: str, favored: tuple[RegimeLabel, ...] | None = None) -> RegimeReport:
    return build_regime_report(
        variant=Variant.STANDARD,
        spec=_spec(),
        daily_series=str(DAILY),
        daily=DAILY_LABELS,
        days=_days(),
        trades=TRADES,
        initial_balance=_yen("1000000"),
        path_net=_yen(net),
        favored_regimes=favored,
    )


def test_rp5_14_the_sum_of_the_regime_rows_equals_the_path_net() -> None:
    report = _report("1000", favored=(RegimeLabel.UP_HIGH,))
    assert report.status is RegimeReportStatus.AVAILABLE
    assert report.regime_net == report.path_net == _yen("1000")
    rows = {row.label: row for row in report.table}
    assert tuple(rows) == REGIME_TABLE_ORDER
    up = rows[RegimeLabel.UP_HIGH]
    assert (up.closed_count, up.win_count, up.loss_count, up.total) == (2, 1, 1, _yen("600"))
    assert up.total_rate == decimal_from_str("0.0006")
    assert up.average_win_loss == decimal_from_str("2.5")
    flat = rows[RegimeLabel.FLAT_LOW]
    assert (flat.closed_count, flat.settled_count, flat.total) == (0, 1, _yen("200"))
    assert flat.average_win_loss_unavailable is MetricUnavailableReason.UNDEFINED_DENOMINATOR
    assert rows[RegimeLabel.UNLABELED].total == _yen("500")
    assert rows[RegimeLabel.DOWN_HIGH].average_win_loss_unavailable is (
        MetricUnavailableReason.NO_TRADES
    )
    favored = report.favored
    assert favored is not None
    assert (favored.favored_total, favored.favored_count) == (_yen("600"), 2)
    assert (favored.other_total, favored.other_count) == (_yen("400"), 3)
    assert favored.favored_total_positive


def test_rp5_14_a_shifted_input_shows_no_table_and_says_why() -> None:
    report = _report("1001")
    assert report.status is RegimeReportStatus.NOT_RECONCILED
    assert report.table == ()
    assert report.favored is None
    assert report.reason is not None and "1000" in report.reason and "1001" in report.reason


def test_the_days_are_labelled_at_their_start() -> None:
    report = _report("1000")
    shares = {row.label: (row.days, row.share) for row in report.day_table}
    # 日 0 は公開済みの日足なし、日 1・2 は UP_HIGH、日 3 は DOWN_LOW、日 4 は FLAT_LOW。
    assert shares[RegimeLabel.UNLABELED] == (1, decimal_from_str("0.2"))
    assert shares[RegimeLabel.UP_HIGH] == (2, decimal_from_str("0.4"))
    assert shares[RegimeLabel.DOWN_LOW][0] == 1
    assert report.favored is None and report.favored_regimes is None


def test_trading_day_spans_count_the_open_days_of_a_week() -> None:
    week = Interval(
        start=UtcTime.parse("2015-01-04T22:00:00Z"), end=UtcTime.parse("2015-01-09T22:00:00Z")
    )
    spans = trading_day_spans(market.calendar(), week)
    assert spans is not None
    assert len(spans) == 5
    assert spans[0].start == week.start and spans[-1].end == week.end
