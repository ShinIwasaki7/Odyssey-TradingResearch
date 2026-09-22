"""紙上トレース T01 の数値を再現する人工データ（全体計画 §8.2 の段階2 完了条件）。

T01 第9節の run（正常エントリー → 利確の取引1件と、run 末尾まで残る建玉1件）を、**受入れ
コマンドから実行・評価まで通せる形**で作る。値はすべて T01 のものであり、手計算で検算
できることだけを目的にしている。

**日付を 2026 年から 2015 年へずらしている（仮置き。人間の決定が必要）**。T01 の run 区間は
`2026-01-04T22:00Z` から `2026-01-16T22:00Z` だが、2026 年以降は未分類の隔離期間であり
**いかなる経路でも読めない**（D03 §3.8、ADR-0014）。承認済み snapshot を経由する通しの
検証はこの期間では構造的に行えないため、**曜日の並びと冬時間の条件が同じ 2015 年**へ
ずらした（`2015-01-04` は日曜、`2015-01-16` は金曜で、どちらの年も1月は米国東部標準時）。
run 区間の長さ（12 日 = 1,036,800 秒）も、週の開閉の位置も T01 と同じである。価格・数量・
損益・資産は日付に依存しないため、T01 の値はそのまま再現される。

| 経路 | T01 の値 | ここでの作り方 |
|---|---|---|
| 建玉1（P1） | 約定 150.080 / 数量 32,000 / 利確 151.230 / 残高 1,036,736 | 火曜 09:00Z に突破 |
| 建玉2（P2） | 約定 151.000 / 数量 30,000 / 損切り 150.400 / 利確 152.200 | 木曜 10:00Z に突破 |
| 末尾 | 確定損益 36,706 / 含み込み資産 1,051,706 / 仮決済 14,670 | 最終足の終値 151.500 |
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.synthetic import market

__all__ = [
    "ENTRY2_START",
    "series_of",
    "EXPECTED",
    "RUN_INTERVAL",
    "T01_DECISION_TIME",
    "T2_DECISION_TIME",
    "WARMUP_END",
    "bars_for",
    "csv_text",
]

#: run 区間（T01 §1.1 の12日間を 2015 年へずらしたもの）。日曜 17:00 NY から金曜 17:00 NY。
RUN_INTERVAL = Interval(
    start=UtcTime.parse("2015-01-04T22:00:00Z"),
    end=UtcTime.parse("2015-01-16T22:00:00Z"),
)

#: 建玉1 の判断時刻（T01 §2.2 の火曜 09:00Z）。
T01_DECISION_TIME = UtcTime.parse("2015-01-06T09:00:00Z")

#: 建玉1 を生む1時間足の区間の開始（判断時刻の1本前）。
_T1_BAR_START = UtcTime.parse("2015-01-06T08:00:00Z")

#: 突破水準を引き上げるためだけに置く1時間足（終値は低いので発火しない）。
_RAMP_BAR_START = UtcTime.parse("2015-01-06T09:00:00Z")

#: 建玉2 の判断時刻（木曜 10:00Z）。
T2_DECISION_TIME = UtcTime.parse("2015-01-08T10:00:00Z")

#: 建玉2 を生む1時間足の区間の開始。
_T2_BAR_START = UtcTime.parse("2015-01-08T09:00:00Z")

#: 建玉1 の入場約定が起きる執行足の開始（判断時刻と同じ）。
_ENTRY1_START = T01_DECISION_TIME

#: 建玉1 の利確が到達する執行足の開始（T01 §2.6 の 11:00Z）。
_TAKE_PROFIT_START = UtcTime.parse("2015-01-06T11:00:00Z")

#: 建玉2 の入場約定が起きる執行足の開始。
ENTRY2_START = T2_DECISION_TIME

#: 助走が終わる時刻。ここまでは20本の確定足が揃わず、評価は見送りになる（D02 §8.3）。
#: 1時間足は run 開始（日曜 22:00Z）から数え始めるので、20本目が確定するのは月曜 18:00Z。
WARMUP_END = UtcTime.parse("2015-01-05T18:00:00Z")

#: 最終の執行足（T01 §9 の `[21:45,22:00)`）。
_FINAL_EXECUTION_START = UtcTime.parse("2015-01-16T21:45:00Z")

#: 1時間足の4本値（始値・高値・安値・終値）。
_Ohlc = tuple[str, str, str, str]

#: 助走と建玉1 の窓を作る静かな足（T01 §2.1: 高値の最大 150.000、安値の最小 149.500）。
_QUIET_1H: _Ohlc = ("149.505", "150.000", "149.500", "149.505")

#: 建玉1 を生む足（T01 §2.1: 終値 150.040 が窓の高値の最大 150.000 を上抜ける）。
_TRIGGER1_1H: _Ohlc = ("149.900", "150.100", "149.800", "150.040")

#: 突破水準を 151.000 まで引き上げる足。終値は 149.505 なので発火しない。
#: これを置かないと、次に述べる水準帯の足の終値（150.405）が窓の高値の最大（150.100）を
#: 上抜けて、T01 に無い3件目の取引が生まれる。
_RAMP_1H: _Ohlc = ("149.505", "151.000", "149.500", "149.505")

#: 建玉2 の損切り水準 150.400 を作る水準帯の足（安値の最小が 150.400、高値の最大が 150.900）。
_LEVEL_1H: _Ohlc = ("150.405", "150.900", "150.400", "150.405")

#: 建玉2 を生む足（終値 150.950 が窓の高値の最大 150.900 を上抜ける）。
_TRIGGER2_1H: _Ohlc = ("150.405", "150.950", "150.400", "150.950")

#: 建玉1 の受付時の参照価格を作る執行足（T01 §2.1 の `[08:45,09:00)`、終値 150.040）。
_REFERENCE1_15M: _Ohlc = ("150.040", "150.060", "150.040", "150.040")

#: 建玉1 が約定する執行足（T01 §2.1: 始値 bid 150.050 → ask 150.070 → 滑り → 150.080）。
_ENTRY1_15M: _Ohlc = ("150.050", "150.120", "150.020", "150.100")

#: 建玉1 の保有中の執行足（T01 §2.1: 終値はいずれも 150.040 以上、保護水準には触れない）。
_HOLDING1_15M: _Ohlc = ("150.100", "150.200", "150.050", "150.150")

#: 利確に触れる執行足（T01 §2.6: 高値 151.300 が利確 151.240 に到達、安値は損切りに触れない）。
_TAKE_PROFIT_15M: _Ohlc = ("151.000", "151.300", "151.000", "151.200")

#: 建玉1 の決済後、建玉2 の受付までの執行足。終値 151.000 が建玉2 の参照価格になる。
_BETWEEN_15M: _Ohlc = ("151.000", "151.050", "150.950", "151.000")

#: 建玉2 が約定する執行足（始値 bid 150.970 → ask 150.990 → 滑り → 151.000）。
_ENTRY2_15M: _Ohlc = ("150.970", "151.100", "150.900", "151.050")

#: 建玉2 の保有中の執行足（終値 151.050 は入場価格 151.000 以上。保護水準には触れない）。
_HOLDING2_15M: _Ohlc = ("151.050", "151.100", "150.900", "151.050")

#: 最終の執行足（T01 §9: 終値 151.500 が残存建玉の評価価格になる）。
_FINAL_15M: _Ohlc = ("151.400", "151.550", "151.350", "151.500")


@dataclass(frozen=True, slots=True)
class _Expected:
    """T01 が定める期待値（受入れテストが照合する）。"""

    #: 建玉1 の入場約定価格と数量（T01 §2.4）。
    entry1_price: Price
    entry1_quantity: Decimal
    #: 建玉1 の決済約定価格（T01 §2.6）。
    exit1_price: Price
    #: 建玉1 の確定損益（T01 §2.6: 決済側の手数料だけを引いた値）。
    trade1_realized: Decimal
    #: 建玉2 の入場約定価格と数量（T01 §9）。
    entry2_price: Price
    entry2_quantity: Decimal
    #: 末尾の3集計（T01 §9.3）。
    realized: Decimal
    equity_with_mtm: Decimal
    hypothetical_closed: Decimal
    #: 費用の内訳（T01 §9.3）。
    commission: Decimal
    slippage_in_price: Decimal
    spread_in_price: Decimal
    #: 台帳の推移のうち検算に使う値（T01 §9.4）。
    initial_balance: Decimal
    balance_after_entry1: Decimal
    balance_after_exit1: Decimal
    balance_after_entry2: Decimal
    min_equity: Decimal
    #: 最大ドローダウン（T01 §9.4）。
    max_drawdown_mtm: Decimal
    max_drawdown_mtm_rate: Decimal
    max_drawdown_balance: Decimal
    max_drawdown_balance_rate: Decimal
    #: 不利約定幅の最大値（T01 §2.4）。
    max_adverse_fill_offset: Decimal
    #: 建玉1 の保有時間（T01 §2: 09:00Z → 11:15Z）。
    holding1: timedelta


def _decimal(text: str) -> Decimal:
    return decimal_from_str(text)


#: T01 の期待値（第2節・第9節・第9.4節）。
EXPECTED = _Expected(
    entry1_price=Price(_decimal("150.080")),
    entry1_quantity=_decimal("32000"),
    exit1_price=Price(_decimal("151.230")),
    trade1_realized=_decimal("36768"),
    entry2_price=Price(_decimal("151.000")),
    entry2_quantity=_decimal("30000"),
    realized=_decimal("36706"),
    equity_with_mtm=_decimal("1051706"),
    hypothetical_closed=_decimal("14670"),
    commission=_decimal("94"),
    slippage_in_price=_decimal("940"),
    spread_in_price=_decimal("1240"),
    initial_balance=_decimal("1000000"),
    balance_after_entry1=_decimal("999968"),
    balance_after_exit1=_decimal("1036736"),
    balance_after_entry2=_decimal("1036706"),
    min_equity=_decimal("998688"),
    max_drawdown_mtm=_decimal("1312"),
    max_drawdown_mtm_rate=_decimal("0.001312"),
    max_drawdown_balance=_decimal("32"),
    max_drawdown_balance_rate=_decimal("0.000032"),
    max_adverse_fill_offset=_decimal("0.020"),
    holding1=timedelta(hours=2, minutes=15),
)


def _ohlc_1h(bar_start: UtcTime) -> _Ohlc:
    """1時間足の4本値（突破の成否がここで決まる）。"""
    if bar_start == _T1_BAR_START:
        return _TRIGGER1_1H
    if bar_start == _RAMP_BAR_START:
        return _RAMP_1H
    if bar_start == _T2_BAR_START:
        return _TRIGGER2_1H
    if bar_start < _T1_BAR_START:
        return _QUIET_1H
    return _LEVEL_1H


def _ohlc_15m(bar_start: UtcTime) -> _Ohlc:
    """15分足の4本値（約定価格と保護水準の到達がここで決まる）。"""
    if bar_start == _ENTRY1_START:
        return _ENTRY1_15M
    if bar_start == _TAKE_PROFIT_START:
        return _TAKE_PROFIT_15M
    if bar_start == ENTRY2_START:
        return _ENTRY2_15M
    if bar_start == _FINAL_EXECUTION_START:
        return _FINAL_15M
    if bar_start < _ENTRY1_START:
        return _REFERENCE1_15M
    if bar_start < _TAKE_PROFIT_START:
        return _HOLDING1_15M
    if bar_start < ENTRY2_START:
        return _BETWEEN_15M
    return _HOLDING2_15M


#: 時間足ごとの4本値の決め方。
_RULES: Mapping[str, Callable[[UtcTime], _Ohlc]] = {"1h": _ohlc_1h, "15m": _ohlc_15m}


def bars_for(
    timeframe_id: str,
    definition: TimeframeDefinition,
    calendar: TradingCalendar,
    window: Interval = RUN_INTERVAL,
) -> tuple[Bar, ...]:
    """カレンダー上存在すべき足をすべて作る（欠落を1本も作らない）。

    欠落を作らないのは、執行モデルが読む系列の足が欠けていると run を開始できない
    （D06 §10.5 の手順2）ためである。分類の要る警告も出ないので、受入れは分類なしで
    確定できる。
    """
    chooser = _RULES.get(timeframe_id)
    if chooser is None:  # pragma: no cover - 呼び出し側が2つの時間足だけを渡す
        raise AssertionError(f"no synthetic rule for the timeframe {timeframe_id!r}")
    made: list[Bar] = []
    for bar_start in calendar.expected_bar_starts(definition, window):
        interval = definition.expected_interval(calendar, bar_start)
        assert interval is not None  # noqa: S101 - expected_bar_starts が保証する
        open_value, high_value, low_value, close_value = chooser(bar_start)
        made.append(
            Bar(
                series=market.series(market.USDJPY, timeframe_id),
                interval=interval,
                open=Price(_decimal(open_value)),
                high=Price(_decimal(high_value)),
                low=Price(_decimal(low_value)),
                close=Price(_decimal(close_value)),
                volume=_decimal("1000"),
                available_at=interval.end,
                provenance=Provenance(
                    kind=ProvenanceKind.HISTDATA, source_ref="tests/fixtures/acceptance"
                ),
            )
        )
    return tuple(made)


def csv_text(bars: Sequence[Bar]) -> str:
    """足を原 CSV の本文へ戻す（受入れコマンドの入力にする）。"""
    return market.csv_text(bars)


def series_of(timeframe_id: str) -> SeriesId:
    """人工データの系列。"""
    return market.series(market.USDJPY, timeframe_id)
