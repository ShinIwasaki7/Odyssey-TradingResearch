"""人工市場データの生成器（D03 §11）。

テストが共通で使う定義（時間足定義4件、ニューヨーク 17 時基準のカレンダー、USDJPY の
1時間足系列）と、そこから足を作る関数を置く。実データは読まない。

生成する足は決定論的で、乱数を使わない。価格は足の開始時刻から機械的に決めるので、同じ
区間を2度生成すれば必ず同じ値になる（受入れの決定論性をテストするため）。

**遅延の当て込み**（D08 §9.6 の1・2）: 素の足を作る関数（`make_bars` など）と、遅延シナリオ
（`DelayScenario`、D03 §3.6）を当てる関数（`apply_delay`）を分けてある。同じ素の足に別々の
シナリオを当てられるので、遅延シナリオ別の比較で「差が遅延だけであること」が構造で保証
される。`apply_delay` は `available_at` だけを動かし、OHLC と対象区間は変えない。

**gap と SL/TP 同時到達**（D08 §9.3）: `make_bars` の `gaps` と `straddles` で指定する。どちらも
既定は空で、指定しなければ従来と同じ足が出る。gap は値の水準を飛ばし（以降の足も新しい水準から
続く）、straddle は1本の足の高値・安値を広げて2つの価格を包ませる。どちらも対象区間と
`available_at` を動かさない。同じ足に両方を指定したときの適用順は gap → straddle。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from zoneinfo import ZoneInfo

from odyssey_fx.common.money import Price, PriceOffset, decimal_from_int, decimal_from_str
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import ClosureRule, TradingCalendar, WeeklyMoment
from odyssey_fx.marketdata.domain.schedule import DelayScenario
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


#: `make_bars` の `gaps` / `straddles` の既定（空。書き換えられない写像にしておく）。
NO_GAPS: Mapping[UtcTime, PriceOffset] = MappingProxyType({})
NO_STRADDLES: Mapping[UtcTime, tuple[Price, Price]] = MappingProxyType({})


def make_bars(
    series_id: SeriesId,
    timeframe_def: TimeframeDefinition,
    trading_calendar: TradingCalendar,
    window: Interval,
    *,
    skip_starts: Iterable[UtcTime] = (),
    volume: str = "1000",
    gaps: Mapping[UtcTime, PriceOffset] = NO_GAPS,
    straddles: Mapping[UtcTime, tuple[Price, Price]] = NO_STRADDLES,
) -> tuple[Bar, ...]:
    """カレンダー上存在すべき足をすべて作る（`skip_starts` に挙げたものは作らない）。

    欠損のテストは、生成する足を間引くことで作る。カレンダー側を変えないので、検査は
    「存在すべきなのに無い」と正しく判定できる。

    `gaps`（D08 §9.3.1）: 鍵は飛ばす足の開始時刻、値は直前の足の終値からの差（符号付き。
    正で上、負で下）。その足の始値を「直前の足の終値 ＋ 差」に置き、**その足と以降の足を同じ
    幅だけずらす**（1本だけ飛ばして戻る形にはしない）。足の形（始値・高値・安値・終値の
    相互の差）は変えない。直前の足は、この関数が返す列の1つ前の足である。

    `straddles`（D08 §9.3.2）: 鍵は足の開始時刻、値はその足が必ず包む2つの価格（順不同）。
    高値を2値の高い方以上、安値を低い方以下に広げ、始値と終値は動かさない。どちらが先に
    到達したことにするかは決めない（足内競合の解決は ADR-0030・D06 §7.4 の担当）。

    次の指定は `ValueError` で拒否する: 差が 0 の gap、2値が等しい straddle、作る足の無い時刻
    （カレンダーが区間を持たない時刻・窓の外・`skip_starts` で落とした時刻）、返す列の先頭の足
    への gap（直前の足が無く、差の起点が決まらない）。
    """
    skipped = set(skip_starts)
    starts = trading_calendar.expected_bar_starts(timeframe_def, window)
    _check_targets(gaps, starts, skipped, "gap")
    _check_targets(straddles, starts, skipped, "straddle")
    for at, offset in gaps.items():
        if offset.value == 0:
            raise ValueError(f"gap at {at} must be non-zero (D08 §9.3.1)")
    for at, (first, second) in straddles.items():
        if first == second:
            raise ValueError(f"straddle at {at} needs two different prices (D08 §9.3.2)")

    bars: list[Bar] = []
    shift = PriceOffset(decimal_from_int(0))
    for bar_start in starts:
        if bar_start in skipped:
            continue
        interval = timeframe_def.expected_interval(trading_calendar, bar_start)
        assert interval is not None  # noqa: S101 - expected_bar_starts が保証する
        bar = make_bar(series_id, interval, volume=volume)
        # 適用順は gap → straddle（D08 §9.3.3）。gap が水準を動かし、straddle が幅を広げる。
        if bar_start in gaps:
            if not bars:
                raise ValueError(
                    f"gap at {bar_start} is on the first bar; there is no previous close"
                )
            shift = (bars[-1].close + gaps[bar_start]) - bar.open
        bar = _shifted(bar, shift)
        if bar_start in straddles:
            bar = _straddled(bar, straddles[bar_start])
        bars.append(bar)
    return tuple(bars)


def _check_targets(
    targets: Mapping[UtcTime, object],
    starts: Sequence[UtcTime],
    skipped: set[UtcTime],
    label: str,
) -> None:
    """gap / straddle の鍵が、この呼び出しで作る足の開始時刻であることを確かめる。"""
    known = set(starts)
    for at in targets:
        if at in skipped:
            raise ValueError(f"{label} at {at} targets a bar dropped by skip_starts")
        if at not in known:
            raise ValueError(f"{label} at {at} is not the start of a bar in the window")


def _shifted(bar: Bar, shift: PriceOffset) -> Bar:
    """足の4本値を同じ幅だけずらす（形を保つので OHLC の整合は崩れない）。"""
    if shift.value == 0:
        return bar
    return replace(
        bar,
        open=bar.open + shift,
        high=bar.high + shift,
        low=bar.low + shift,
        close=bar.close + shift,
    )


def _straddled(bar: Bar, prices: tuple[Price, Price]) -> Bar:
    """高値・安値を広げて2つの価格を包ませる。始値・終値は動かさない。"""
    return replace(bar, high=max(bar.high, *prices), low=min(bar.low, *prices))


def apply_delay(bars: Sequence[Bar], scenario: DelayScenario) -> tuple[Bar, ...]:
    """遅延シナリオを当て、各足の `available_at` だけを遅らせる（D08 §9.6 の1）。

    素の足の `available_at` を公開予定の時刻（D03 §3.5 の `scheduled_at`）とみなし、その足に
    当たる遅延（`DelayScenario.delay_for`。複数の規則が当たれば最大）を足す。人工データの素の
    足は通常の公開遅延を 0 として `available_at = bar_end` で作ってあるので、D03 §3.6 の
    `available_at = scheduled_at + delay` と一致する。

    OHLC・対象区間・系列・出所は1つも変えない。遅延が非負であることは規則の型が構築時に保証
    する（D03 §3.6）ので、ここでは検査しない。入力の列は変更せず、新しい列を返す。
    """
    delayed: list[Bar] = []
    for bar in bars:
        delay = scenario.delay_for(bar.series, bar.bar_start)
        delayed.append(replace(bar, available_at=bar.available_at + delay))
    return tuple(delayed)


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
