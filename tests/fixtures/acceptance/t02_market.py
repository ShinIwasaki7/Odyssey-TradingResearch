"""紙上トレース T02（検証戦略 B・段階3）の数値を再現する人工データ（T02 §16）。

T01 再現生成器（`t01_market`）とは**別の生成器**である（D08 §9.1 の「役割が違う生成器は
混ぜない」）。T01 は日足を作らないが、T02 は日足を**1時間足からの集約で作る**ことが検算の
前提であり（T02 §3.1 の到達可否はこの集約から導かれる）、同じ関数に日足を足すと T01 の
検算値の意味が変わるためである。

- `USDJPY/1h/bid`: 固定 OHLC テーブル（T02 §2.1）。**生成区間の全体**を覆う。
- `USDJPY/1d_ny17/bid`: `aggregate` で1時間足から集約する（D03 §5.1。集約の規則は
  `marketdata.application.aggregation` が持ち、ここはそれを呼ぶだけ）。**日足を直接テーブル
  で与えない**。
- `USDJPY/15m/bid`: 別の固定 OHLC テーブル（T02 §2.3）。

**15分足は集約の入力にも出力にもしない**（D03 §5.1。15分足は執行系列）。T02 §2.3 の開始足
`[01-07 08:45, 09:00)` の終値 149.350 は同じ時刻に終わる1時間足の終値と同じ値に作ってあるが、
**この一致は人工データの設定であり構造的な保証ではない**（T01 §2.1 と同じ扱い）。

**生成区間は run 区間より広い**（T02 §16 の不変条件4）。1時間足は `2014-09-30T22:00Z` から
作る。`daily_ema` の窓60本と `m15_ema` の窓60本がどちらもウォームアップ不足にならないため
であり、既定の生成区間を run 区間に縮めると T02 §2.4 の検算値が1つも出ない。

**遅延の当て込み**（T02 §16 の拡張1・2、D08 §9.6）: `apply_delay` は汎用生成器の同名の関数を
呼ぶ。素の足を作る関数と遅延を当てる関数が分かれているので、遅延シナリオ4ケースが同じ素の
足を共有でき、差が `available_at` だけになる。

表に無い区間の値（T02 §2 の「表に無い区間は直前の行と同じ」）:

- 1時間足: 表の最初の行が生成区間の先頭から `01-06 14:00Z` までを覆う。最後の行
  （`[01-08 08:00, 09:00)`）より後は、その行と同じ値が続く。
- 15分足: 表の最初の行（`[01-06 18:00, 01-07 08:45)`）より前は、その最初の行と同じ値で埋める
  （T02 は直前の行を持たない区間の値を書いていない。検算値の窓はこの区間に届かない）。最後の行
  （`[01-07 13:00, 13:15)`）より後は、その行と同じ値が続く。

日付は 2015年1月である。人工データはアクセス分類の対象外であり、snapshot の partition は
日付によらず研究履歴として作る（D08 §9.5 の規則1）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.aggregation import aggregate as aggregate_bars
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.schedule import DelayScenario
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.synthetic import market

__all__ = [
    "DAILY_TIMEFRAME_ID",
    "EXPECTED",
    "GENERATION_INTERVAL",
    "RUN_INTERVAL",
    "aggregate",
    "apply_delay",
    "bars_for",
    "csv_text",
    "series_of",
]

#: run 区間（T02 §1.1、T01 と同じ12日間）。`RunConfig.run_interval` に渡す値であり、
#: **足を作る区間ではない**。
RUN_INTERVAL = Interval(
    start=UtcTime.parse("2015-01-04T22:00:00Z"),
    end=UtcTime.parse("2015-01-16T22:00:00Z"),
)

#: 生成区間（T02 §16）。ウォームアップの開始まで遡る。`bars_for` の既定の `window`。
GENERATION_INTERVAL = Interval(
    start=UtcTime.parse("2014-09-30T22:00:00Z"),
    end=RUN_INTERVAL.end,
)

#: 日足の時間足（T02 §1.1 の `1d_ny17` v1）。
DAILY_TIMEFRAME_ID = "1d_ny17"

#: 4本値（始値・高値・安値・終値）。
_Ohlc = tuple[str, str, str, str]


def _t(text: str) -> UtcTime:
    return UtcTime.parse(text)


#: 1時間足の表（T02 §2.1）。各行は (その行が始まる時刻, 4本値)。次の行が始まるまで同じ値。
_TABLE_1H: tuple[tuple[UtcTime, _Ohlc], ...] = (
    (GENERATION_INTERVAL.start, ("149.000", "149.050", "148.950", "149.000")),
    (_t("2015-01-06T14:00:00Z"), ("149.000", "149.300", "148.950", "149.250")),
    (_t("2015-01-06T15:00:00Z"), ("149.250", "149.290", "149.150", "149.250")),
    (_t("2015-01-06T21:00:00Z"), ("149.250", "149.290", "149.150", "149.210")),
    (_t("2015-01-06T22:00:00Z"), ("149.250", "149.290", "149.150", "149.250")),
    (_t("2015-01-07T08:00:00Z"), ("149.250", "149.360", "149.150", "149.350")),
    (_t("2015-01-07T09:00:00Z"), ("149.360", "149.520", "149.340", "149.450")),
    (_t("2015-01-07T10:00:00Z"), ("149.450", "149.500", "149.300", "149.400")),
    (_t("2015-01-07T13:00:00Z"), ("149.400", "149.420", "149.050", "149.100")),
    (_t("2015-01-07T14:00:00Z"), ("149.100", "149.150", "148.950", "149.000")),
    (_t("2015-01-07T21:00:00Z"), ("149.000", "149.020", "148.800", "148.810")),
    (_t("2015-01-07T22:00:00Z"), ("148.810", "148.900", "148.700", "148.800")),
    (_t("2015-01-08T08:00:00Z"), ("148.800", "149.750", "148.780", "149.700")),
)

#: 15分足の表（T02 §2.3）。最初の行はそれより前の区間も覆う（モジュールの説明を参照）。
_TABLE_15M: tuple[tuple[UtcTime, _Ohlc], ...] = (
    (GENERATION_INTERVAL.start, ("149.245", "149.280", "149.200", "149.245")),
    (_t("2015-01-07T08:45:00Z"), ("149.245", "149.360", "149.240", "149.350")),
    (_t("2015-01-07T09:00:00Z"), ("149.360", "149.500", "149.340", "149.450")),
    (_t("2015-01-07T09:15:00Z"), ("149.450", "149.500", "149.300", "149.400")),
    (_t("2015-01-07T12:00:00Z"), ("149.400", "149.420", "149.300", "149.400")),
    (_t("2015-01-07T13:00:00Z"), ("149.400", "149.410", "149.050", "149.080")),
)


def _chooser(table: tuple[tuple[UtcTime, _Ohlc], ...]) -> Callable[[UtcTime], _Ohlc]:
    def choose(bar_start: UtcTime) -> _Ohlc:
        chosen = table[0][1]
        for row_start, ohlc in table:
            if row_start.value <= bar_start.value:
                chosen = ohlc
            else:
                break
        return chosen

    return choose


#: 素の系列（固定テーブルから作る時間足）ごとの4本値の決め方。
_RULES: Mapping[str, Callable[[UtcTime], _Ohlc]] = {
    "1h": _chooser(_TABLE_1H),
    "15m": _chooser(_TABLE_15M),
}


@dataclass(frozen=True, slots=True)
class _Expected:
    """T02 §2.4・§16 の検算値のうち、生成器の段階で確かめられるもの。"""

    #: `daily_ema`（`01-06 22:00Z` / `01-07 22:00Z`）。
    daily_ema_jan6: Price
    daily_ema_jan7: Price
    #: `m15_ema`（`01-07 09:00Z`）。
    m15_ema_at_0900: Price
    #: EMA のパラメータ（T02 §1.3。`period=20`、`window_bars=60`）。
    period: int
    window_bars: int


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


#: T02 の検算値（§2.4・§16）。
EXPECTED = _Expected(
    daily_ema_jan6=_price("149.020"),
    daily_ema_jan7=_price("149.000"),
    m15_ema_at_0900=_price("149.255"),
    period=20,
    window_bars=60,
)


def series_of(timeframe_id: str) -> SeriesId:
    """人工データの系列（USDJPY、bid）。"""
    return market.series(market.USDJPY, timeframe_id)


def bars_for(
    timeframe_id: str,
    definition: TimeframeDefinition,
    calendar: TradingCalendar,
    window: Interval = GENERATION_INTERVAL,
) -> tuple[Bar, ...]:
    """素の系列（1時間足・15分足）の足を、カレンダー上存在すべき分だけすべて作る。

    欠落を1本も作らない（執行系列が欠けると run を開始できない。D06 §10.5 の手順2）。
    **日足はここでは作らない**。日足は `aggregate` で1時間足から集約する（T02 §16 の
    不変条件1）。テーブルで与える経路を用意すると、日足高値が1時間足の高値を含むこと
    （T02 §3.1）が規則の帰結ではなく人工データの設定になってしまう。
    """
    chooser = _RULES.get(timeframe_id)
    if chooser is None:
        raise AssertionError(
            f"no raw synthetic rule for the timeframe {timeframe_id!r};"
            " daily bars must be aggregated from the 1h bars (T02 §16)"
        )
    made: list[Bar] = []
    for bar_start in calendar.expected_bar_starts(definition, window):
        interval = definition.expected_interval(calendar, bar_start)
        assert interval is not None  # noqa: S101 - expected_bar_starts が保証する
        open_value, high_value, low_value, close_value = chooser(bar_start)
        made.append(
            Bar(
                series=series_of(timeframe_id),
                interval=interval,
                open=_price(open_value),
                high=_price(high_value),
                low=_price(low_value),
                close=_price(close_value),
                volume=Decimal(1000),
                available_at=interval.end,
                provenance=Provenance(
                    kind=ProvenanceKind.HISTDATA, source_ref="tests/fixtures/acceptance"
                ),
            )
        )
    return tuple(made)


def aggregate(
    bars_1h: tuple[Bar, ...],
    timeframe_def: TimeframeDefinition,
    calendar: TradingCalendar,
) -> tuple[Bar, ...]:
    """1時間足から上位足（日足）を集約する入り口（T02 §16 の拡張4）。

    集約の規則（D03 §5.1）は `marketdata.application.aggregation.aggregate` が持ち、ここは
    それを呼ぶだけである。欠落のある区間は上位足を作らず報告されるが、この生成器は欠落を
    作らないので、報告が1件でも出たら生成器の誤りとして止める。
    """
    for bar in bars_1h:
        if bar.series != series_of("1h"):
            raise AssertionError(f"aggregate() takes the 1h bars only, got {bar.series}")
    result = aggregate_bars(
        bars_1h,
        source_timeframe_def=market.TF_1H,
        target_series=series_of(timeframe_def.ref.id),
        target_timeframe_def=timeframe_def,
        calendar=calendar,
    )
    if result.findings:
        raise AssertionError(f"the T02 generator produced incomplete aggregates: {result.findings}")
    return result.bars


def apply_delay(bars: Sequence[Bar], scenario: DelayScenario) -> tuple[Bar, ...]:
    """遅延シナリオを当て、`available_at` だけを動かす（T02 §16 の拡張1、D08 §9.6 の1）。"""
    return market.apply_delay(bars, scenario)


def csv_text(bars: Sequence[Bar]) -> str:
    """足を原 CSV の本文へ戻す（受入れコマンドの入力にする）。"""
    return market.csv_text(bars)
