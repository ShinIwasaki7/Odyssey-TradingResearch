"""tick から足への集約（D03 §14.6）。

- tick の時刻 = 時間ファイルの始まり（UTC）＋ミリ秒。ミリ秒が `0 <= ms < 3600000` に入らない
  tick は足に使わない（検証 1 が数えて不合格にする。D03 §14.7 の 1）。
- 価格は整数値を `Decimal` にしてから桁で割って作る（浮動小数を経由しない。ADR-0012）。
  **bid の値だけを使う**（原データの価格基準は bid と宣言されている。D03 §2）。
- 足の区間は時間足定義の整列で決め、足の開始時刻が足のラベル。
- 始値＝区間の最初の tick の bid、高値＝最大、安値＝最小、終値＝最後の tick の bid。同じ
  ミリ秒の tick が複数あるときは解凍後のファイル内の順に並べる（安定な整列）。
- **出来高は 0 を書き、「出来高不明」を意味する**（D03 §14.18 の 4）。実際の出来高 0 ではない。
- 区間に tick が 1 件も無い足は作らない（架空の価格で埋めない。原則1）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Final

from odyssey_fx.common.money import Price, decimal_from_int
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import HourKey, ProviderSymbol, Tick
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = ["REFILL_AGGREGATION_RULE_VERSION", "bar_from_ticks", "tick_time", "ticks_in"]

#: 集約規則の版（D03 §14.10 の仮称 `refill_ticks_v1`）。補充の識別子 `refill_id` に入る。
REFILL_AGGREGATION_RULE_VERSION: Final = "refill_ticks_v1"


def tick_time(hour: HourKey, tick: Tick) -> UtcTime:
    """tick の時刻（時間ファイルの始まり＋ミリ秒。D03 §14.6）。"""
    return hour.start + timedelta(milliseconds=tick.offset_ms)


def ticks_in(hour: HourKey, ticks: Sequence[Tick], interval: Interval) -> tuple[Tick, ...]:
    """区間に入る tick を時刻の順（同じミリ秒はファイル内の順）に返す。

    ミリ秒が時間ファイルの範囲外の tick は含めない（D03 §14.6）。
    """
    selected = [
        tick
        for tick in ticks
        if tick.in_hour and interval.start <= tick_time(hour, tick) < interval.end
    ]
    # `sorted` は安定なので、同じミリ秒の tick はファイル内の順を保つ。
    return tuple(sorted(selected, key=lambda tick: tick.offset_ms))


def bar_from_ticks(
    *,
    series: SeriesId,
    interval: Interval,
    hour: HourKey,
    ticks: Sequence[Tick],
    quote: ProviderSymbol,
    source_ref: str,
) -> Bar | None:
    """区間の tick の bid から足を 1 本作る。tick が無ければ `None`（D03 §14.6・原則1）。

    足の区間は時間ファイルの中に収まっていなければならない（15分足・1時間足の整列では常に
    成り立つ）。`source_ref` はもとの時間ファイル（保管場所の相対パス）を指す（D03 §14.7 の 5）。
    """
    if not (hour.start <= interval.start and interval.end <= hour.interval.end):
        raise MarketDataValueError(
            f"the bar {series} {interval} is not inside the hour file {hour} (D03 §14.6)"
        )
    if quote.symbol != series.symbol:
        raise MarketDataValueError(f"price scale of {quote.symbol} used for {series}")
    selected = ticks_in(hour, ticks, interval)
    if not selected:
        return None
    prices = [Price(quote.price_of(tick.bid_raw)) for tick in selected]
    return Bar(
        series=series,
        interval=interval,
        open=prices[0],
        high=max(prices, key=lambda price: price.value),
        low=min(prices, key=lambda price: price.value),
        close=prices[-1],
        volume=decimal_from_int(0),
        available_at=interval.end,
        provenance=Provenance(kind=ProvenanceKind.DUKASCOPY_REFILL, source_ref=source_ref),
    )
