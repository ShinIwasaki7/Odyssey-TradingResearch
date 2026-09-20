"""人工 1時間足から生成した 4時間足・日足の固定出力（D03 §11 の golden）。

集約規則（D03 §5.1、規則 ID `ny17_v2`）が意図せず変わったことを、固定の期待値との突合で
検出する。値が変わる変更は、この期待値の更新と設計文書の改訂を伴うべきものである。

対象期間は 2026 年 3月の夏時間切替週（3月8日に切替）。週の開始（日曜 17:00 現地）から
金曜の週終了までを取り、切替直後の営業日を含む。

なお、米国の夏時間の切替は日曜の未明に起きるため、ニューヨーク 17 時基準のカレンダーでは
**切替をまたぐ日足・4時間足は週末休場の中に入り、取引される足としては現れない**。
23時間・25時間の日足、3時間・5時間の 4時間足は整列規則そのものの性質であり、
`tests/unit/marketdata/test_timeframe_def.py` が UTC の固定値で照合している。ここでは
「切替週でも取引される足の並びが安定していること」を固定する。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time
from pathlib import Path

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.aggregation import AggregationResult, aggregate
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.synthetic import market

GOLDEN_DIR = Path(__file__).parent / "expected"

HOURLY = market.series()
FOUR_HOUR = market.series(timeframe_id="4h_ny17")
DAILY = market.series(timeframe_id="1d_ny17")
CALENDAR = market.calendar()

#: 夏時間の切替（2026-03-08）を含む週。日曜 17:00 現地（= 21:00Z）の週開始から
#: 金曜 17:00 現地（= 21:00Z）の週終了まで。
DST_WEEK = Interval(
    start=UtcTime.parse("2026-03-08T21:00:00Z"), end=UtcTime.parse("2026-03-13T21:00:00Z")
)

#: 短縮セッションを宣言したカレンダー。金曜（2026-03-13）の現地 13:00 以降を休場にする。
#: 切り詰められた区間で上位足が生成されることを固定する（D03 §5.2）。
SHORTENED_CALENDAR = market.calendar(
    closures=(market.closure(date(2026, 3, 13), time(13, 0), time(17, 0), "短縮"),),
    version=2,
)


def _render(bars: Sequence[Bar]) -> str:
    """足を1行1本のテキストへ書き出す（比較と差分の読みやすさのため）。"""
    lines = ["bar_start,bar_end,open,high,low,close,volume,available_at"]
    for bar in bars:
        lines.append(
            ",".join(
                (
                    str(bar.bar_start),
                    str(bar.bar_end),
                    str(bar.open.value),
                    str(bar.high.value),
                    str(bar.low.value),
                    str(bar.close.value),
                    str(bar.volume),
                    str(bar.available_at),
                )
            )
        )
    return "\n".join(lines) + "\n"


def _aggregate(
    target_series: SeriesId,
    target_def: TimeframeDefinition,
    calendar: TradingCalendar = CALENDAR,
) -> AggregationResult:
    source_bars = market.make_bars(HOURLY, market.TF_1H, calendar, DST_WEEK)
    return aggregate(
        source_bars,
        source_timeframe_def=market.TF_1H,
        target_series=target_series,
        target_timeframe_def=target_def,
        calendar=calendar,
    )


def _assert_matches_golden(name: str, actual: str) -> None:
    path = GOLDEN_DIR / name
    assert path.is_file(), (
        f"golden file {path} is missing; regenerate it deliberately and review the diff"
    )
    assert actual == path.read_text(encoding="utf-8"), (
        f"the aggregation output no longer matches {name};"
        " a change here means the ny17_v2 rule changed (D03 §5.1, ADR-0024)"
    )


def test_the_four_hour_aggregate_matches_the_golden_output() -> None:
    _assert_matches_golden(
        "4h_ny17_dst_week.csv", _render(_aggregate(FOUR_HOUR, market.TF_4H_NY17).bars)
    )


def test_the_daily_aggregate_matches_the_golden_output() -> None:
    _assert_matches_golden(
        "1d_ny17_dst_week.csv", _render(_aggregate(DAILY, market.TF_1D_NY17).bars)
    )


def test_the_source_series_matches_the_golden_output() -> None:
    """構成足そのものも固定する。上位足の差が構成足の変化によるものかを切り分けるため。"""
    source_bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, DST_WEEK)
    _assert_matches_golden("1h_dst_week.csv", _render(source_bars))


def test_the_shortened_session_aggregate_matches_the_golden_output() -> None:
    """短縮セッションでは上位足の区間が休場開始で切り詰められる（D03 §5.2）。"""
    _assert_matches_golden(
        "1d_ny17_shortened.csv",
        _render(_aggregate(DAILY, market.TF_1D_NY17, SHORTENED_CALENDAR).bars),
    )


def test_the_shortened_daily_bar_ends_at_the_declared_closure() -> None:
    """固定出力が意図どおりであること（切り詰めが実際に起きていること）を明示する。"""
    bars = _aggregate(DAILY, market.TF_1D_NY17, SHORTENED_CALENDAR).bars
    truncated = [bar for bar in bars if bar.bar_end == UtcTime.parse("2026-03-13T17:00:00Z")]
    assert truncated, "the declared closure should have shortened the final daily bar"
    assert truncated[0].interval.duration.total_seconds() == 20 * 3600
