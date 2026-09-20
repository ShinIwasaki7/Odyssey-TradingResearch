"""人工市場データの生成器（D03 §11）。

テストが共通で使う定義（時間足定義4件、ニューヨーク 17 時基準のカレンダー、USDJPY の
1時間足系列）と、そこから足を作る関数を置く。実データは読まない。

生成する足は決定論的で、乱数を使わない。価格は足の開始時刻から機械的に決めるので、同じ
区間を2度生成すれば必ず同じ値になる（受入れの決定論性をテストするため）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from odyssey_fx.common.money import Price, decimal_from_int, decimal_from_str
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import ClosureRule, TradingCalendar, WeeklyMoment
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import (
    FixedUtcAlignment,
    SessionAlignment,
    TimeframeDefinition,
)

NEW_YORK = ZoneInfo("America/New_York")

USDJPY = Symbol("USDJPY")
EURUSD = Symbol("EURUSD")

TF_15M = TimeframeDefinition(
    ref=TimeframeRef("15m", 1),
    nominal_length=timedelta(minutes=15),
    alignment=FixedUtcAlignment(step=timedelta(minutes=15)),
)
TF_1H = TimeframeDefinition(
    ref=TimeframeRef("1h", 1),
    nominal_length=timedelta(hours=1),
    alignment=FixedUtcAlignment(step=timedelta(hours=1)),
)
TF_4H_NY17 = TimeframeDefinition(
    ref=TimeframeRef("4h_ny17", 1),
    nominal_length=timedelta(hours=4),
    alignment=SessionAlignment(
        tz=NEW_YORK,
        anchors_local=(
            time(1, 0),
            time(5, 0),
            time(9, 0),
            time(13, 0),
            time(17, 0),
            time(21, 0),
        ),
    ),
)
TF_1D_NY17 = TimeframeDefinition(
    ref=TimeframeRef("1d_ny17", 1),
    nominal_length=timedelta(days=1),
    alignment=SessionAlignment(tz=NEW_YORK, anchors_local=(time(17, 0),)),
)

TIMEFRAME_DEFS = {
    "15m": TF_15M,
    "1h": TF_1H,
    "4h_ny17": TF_4H_NY17,
    "1d_ny17": TF_1D_NY17,
}


def calendar(closures: Sequence[ClosureRule] = (), version: int = 1) -> TradingCalendar:
    """ニューヨーク 17 時基準のカレンダー（D03 §3.4 の初版と同じ週の開閉）。"""
    return TradingCalendar(
        id="fx_ny17",
        version=version,
        tz=NEW_YORK,
        weekly_open=WeeklyMoment(weekday=6, at=time(17, 0)),
        weekly_close=WeeklyMoment(weekday=4, at=time(17, 0)),
        closures=tuple(closures),
    )


def closure(local_day: date, start: time, end: time, note: str = "") -> ClosureRule:
    """短縮セッションの宣言を作る。"""
    return ClosureRule(local_date=local_day, start=start, end=end, note=note)


def series(
    symbol: Symbol = USDJPY,
    timeframe_id: str = "1h",
    basis: PriceBasis = PriceBasis.BID,
) -> SeriesId:
    """系列の識別を作る。"""
    return SeriesId(symbol=symbol, timeframe=TimeframeRef(timeframe_id, 1), basis=basis)


def _price_at(bar_start: UtcTime, offset: int = 0) -> Decimal:
    """足の開始時刻から決まる価格（乱数を使わない）。

    2016-01-01 からの経過時間を分に直し、100 で割った余りを 150 に足す。値そのものに意味は
    なく、「同じ足なら常に同じ価格」であることだけが必要。
    """
    epoch = UtcTime.from_components(2016, 1, 1)
    minutes = int((bar_start - epoch).total_seconds() // 60)
    return decimal_from_str("150") + decimal_from_int((minutes + offset) % 100) / decimal_from_int(
        100
    )


def make_bar(
    series_id: SeriesId,
    interval: Interval,
    *,
    volume: str = "1000",
    available_at: UtcTime | None = None,
    provenance_kind: ProvenanceKind = ProvenanceKind.HISTDATA,
    source_ref: str = "data/raw/market/USDJPY_1h_merged.csv",
) -> Bar:
    """決定論的な OHLC を持つ足を1本作る。

    始値と終値は開始時刻から決まり、高値・安値はその両側に 0.5 ずつ広げる。不変条件
    `low <= min(open, close)` と `max(open, close) <= high` を必ず満たす。
    """
    open_value = _price_at(interval.start)
    close_value = _price_at(interval.start, offset=7)
    high_value = max(open_value, close_value) + decimal_from_str("0.5")
    low_value = min(open_value, close_value) - decimal_from_str("0.5")
    return Bar(
        series=series_id,
        interval=interval,
        open=Price(open_value),
        high=Price(high_value),
        low=Price(low_value),
        close=Price(close_value),
        volume=decimal_from_str(volume),
        available_at=interval.end if available_at is None else available_at,
        provenance=Provenance(kind=provenance_kind, source_ref=source_ref),
    )


def make_bars(
    series_id: SeriesId,
    timeframe_def: TimeframeDefinition,
    trading_calendar: TradingCalendar,
    window: Interval,
    *,
    skip_starts: Iterable[UtcTime] = (),
    volume: str = "1000",
) -> tuple[Bar, ...]:
    """カレンダー上存在すべき足をすべて作る（`skip_starts` に挙げたものは作らない）。

    欠損のテストは、生成する足を間引くことで作る。カレンダー側を変えないので、検査は
    「存在すべきなのに無い」と正しく判定できる。
    """
    skipped = set(skip_starts)
    bars: list[Bar] = []
    for bar_start in trading_calendar.expected_bar_starts(timeframe_def, window):
        if bar_start in skipped:
            continue
        interval = timeframe_def.expected_interval(trading_calendar, bar_start)
        assert interval is not None  # noqa: S101 - expected_bar_starts が保証する
        bars.append(make_bar(series_id, interval, volume=volume))
    return tuple(bars)


def csv_rows(bars: Sequence[Bar]) -> tuple[dict[str, str], ...]:
    """足を原 CSV の行（すべて文字列）へ戻す。

    受入れの正規化をテストするための入力。時刻は原ファイルと同じ空白区切りの
    `YYYY-MM-DD HH:MM:SS+00:00` 形式にする。
    """
    rows: list[dict[str, str]] = []
    for bar in bars:
        rows.append(
            {
                "timestamp": bar.bar_start.value.strftime("%Y-%m-%d %H:%M:%S+00:00"),
                "open": str(bar.open.value),
                "high": str(bar.high.value),
                "low": str(bar.low.value),
                "close": str(bar.close.value),
                "volume": str(bar.volume),
                "source": bar.provenance.kind.value,
            }
        )
    return tuple(rows)


def csv_text(bars: Sequence[Bar]) -> str:
    """足を原 CSV の本文へ戻す（先頭列は無名、D03 §2）。"""
    lines = [",open,high,low,close,volume,source"]
    for row in csv_rows(bars):
        lines.append(
            ",".join(
                (
                    row["timestamp"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    row["source"],
                )
            )
        )
    return "\n".join(lines) + "\n"
