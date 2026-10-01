"""録画した bi5 の解凍と tick から足への集約（D03 §14.3・§14.6、§14.16）。

2026-09-30 の実証で取得した USDJPY 2020-11-30 の 00 時・01 時の時間ファイルを固定の入力にする。
実証では、00 時の bid から作った 15分足 4 本・1時間足 1 本が原データ（`histdata`）と差 0 で
一致した。同じ規則で作った足が実証の値と一致することを確かめる（bid だけを使う・足のラベルは
開始時刻・同じミリ秒はファイル内の順・出来高は 0（出来高不明）・tick の無い区間は作らない）。
"""

from __future__ import annotations

import lzma
import struct
from datetime import timedelta

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.dukascopy_source import decode_bi5
from odyssey_fx.marketdata.application.refill_aggregation import bar_from_ticks, ticks_in
from odyssey_fx.marketdata.domain.bar import ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import HourKey, HourOutcome, Tick, sha256_hex
from tests.fixtures.refill import (
    BI5_00H,
    BI5_01H,
    HOUR_00,
    HOUR_01,
    PROBE_BARS,
    USDJPY_1H,
    USDJPY_15M,
    probe_bar,
    provider_ref,
)
from tests.fixtures.synthetic import market

QUOTE = provider_ref().settings.symbol(market.USDJPY)


def test_the_recorded_hour_decodes_into_ticks() -> None:
    decoded = decode_bi5(BI5_00H)
    assert len(decoded.ticks) == 4198
    assert decoded.ticks[0] == Tick(offset_ms=419, ask_raw=104092, bid_raw=104089)
    assert decoded.outcome is HourOutcome.FETCHED
    # tick の内容のダイジェストは解凍後のバイト列の sha256（D03 §14.10）。
    assert decoded.tick_digest == sha256_hex(lzma.decompress(BI5_00H))


def test_the_tick_digest_does_not_depend_on_the_compression() -> None:
    raw = lzma.decompress(BI5_01H)
    recompressed = lzma.compress(raw, format=lzma.FORMAT_ALONE, preset=1)
    assert recompressed != BI5_01H
    assert decode_bi5(recompressed).tick_digest == decode_bi5(BI5_01H).tick_digest


def test_an_empty_body_is_an_hour_without_ticks() -> None:
    decoded = decode_bi5(b"")
    assert decoded.ticks == ()
    assert decoded.outcome is HourOutcome.FETCHED_EMPTY


@pytest.mark.parametrize(
    "body",
    [b"not lzma at all", lzma.compress(b"x" * 21, format=lzma.FORMAT_ALONE)],
)
def test_content_that_is_not_bi5_is_rejected(body: bytes) -> None:
    with pytest.raises(MarketDataValueError):
        decode_bi5(body)


@pytest.mark.parametrize("row", PROBE_BARS, ids=lambda row: f"{row[0]}-{row[1]}")
def test_bars_from_the_recorded_ticks_match_the_probe(row: tuple[str, ...]) -> None:
    timeframe_id, start = row[0], row[1]
    series = USDJPY_15M if timeframe_id == "15m" else USDJPY_1H
    definition = market.TF_15M if timeframe_id == "15m" else market.TF_1H
    bar_start = UtcTime.parse(start)
    hour_start = UtcTime(bar_start.value.replace(minute=0))
    hour = HourKey(symbol=market.USDJPY, start=hour_start)
    body = BI5_00H if hour_start == HOUR_00 else BI5_01H
    bar = bar_from_ticks(
        series=series,
        interval=definition.boundaries(bar_start),
        hour=hour,
        ticks=decode_bi5(body).ticks,
        quote=QUOTE,
        source_ref="archive",
    )
    expected = probe_bar(timeframe_id, start)
    assert bar is not None
    assert (bar.open, bar.high, bar.low, bar.close) == (
        expected.open,
        expected.high,
        expected.low,
        expected.close,
    )
    assert bar.bar_start == bar_start  # 足のラベルは開始時刻
    assert bar.volume == 0  # 出来高不明（D03 §14.18 の 4）
    assert bar.provenance.kind is ProvenanceKind.DUKASCOPY_REFILL


def test_a_bar_built_from_asks_would_not_match() -> None:
    """bid を使ったことは照合で確かめられる（D03 §14.7 の 2）。"""
    hour = HourKey(symbol=market.USDJPY, start=HOUR_00)
    asks = tuple(
        Tick(offset_ms=tick.offset_ms, ask_raw=tick.bid_raw, bid_raw=tick.ask_raw)
        for tick in decode_bi5(BI5_00H).ticks
    )
    bar = bar_from_ticks(
        series=USDJPY_1H,
        interval=market.TF_1H.boundaries(HOUR_00),
        hour=hour,
        ticks=asks,
        quote=QUOTE,
        source_ref="archive",
    )
    assert bar is not None
    assert bar.open != probe_bar("1h", str(HOUR_00)).open


def test_no_bar_is_built_without_ticks() -> None:
    """架空の価格で埋めない（D03 §14.2 の原則1）。"""
    hour = HourKey(symbol=market.USDJPY, start=HOUR_01)
    bar = bar_from_ticks(
        series=USDJPY_15M,
        interval=market.TF_15M.boundaries(HOUR_01),
        hour=hour,
        ticks=(),
        quote=QUOTE,
        source_ref="archive",
    )
    assert bar is None


def test_ticks_of_the_same_millisecond_keep_the_file_order() -> None:
    hour = HourKey(symbol=market.USDJPY, start=HOUR_01)
    ticks = (
        Tick(offset_ms=10, ask_raw=103910, bid_raw=103900),
        Tick(offset_ms=5, ask_raw=103930, bid_raw=103920),
        Tick(offset_ms=10, ask_raw=103890, bid_raw=103880),
    )
    bar = bar_from_ticks(
        series=USDJPY_15M,
        interval=market.TF_15M.boundaries(HOUR_01),
        hour=hour,
        ticks=ticks,
        quote=QUOTE,
        source_ref="archive",
    )
    assert bar is not None
    assert str(bar.open) == "103.920"
    assert str(bar.close) == "103.880"  # 同じ 10ms の 2 件はファイルの順


def test_ticks_outside_their_hour_are_not_used() -> None:
    hour = HourKey(symbol=market.USDJPY, start=HOUR_01)
    ticks = (
        Tick(offset_ms=-1, ask_raw=1, bid_raw=1),
        Tick(offset_ms=3_600_000, ask_raw=1, bid_raw=1),
        Tick(offset_ms=0, ask_raw=103910, bid_raw=103900),
    )
    selected = ticks_in(hour, ticks, Interval(start=HOUR_01, end=HOUR_01 + timedelta(hours=1)))
    assert selected == (ticks[2],)


def test_the_tick_layout_is_big_endian_iiiff() -> None:
    raw = struct.pack(">iiiff", 7, 1100, 1000, 1.0, 2.0)
    decoded = decode_bi5(lzma.compress(raw, format=lzma.FORMAT_ALONE))
    assert decoded.ticks == (Tick(offset_ms=7, ask_raw=1100, bid_raw=1000),)
