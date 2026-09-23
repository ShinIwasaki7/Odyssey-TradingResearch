"""T01 の8経路が使う人工データ（T01 §2.1・§3・§4・§5・§6・§8・§9）。

値はすべて紙上トレースのもので、手計算で検算できることだけを目的にしている。評価系列
（1時間足）と執行系列（15分足）は**別々の入力**として与える。初版データでは1時間足を
15分足から集約していないため（D03 §2・§5.1）、同じ時刻の終値が一致する保証は無く、
経路4 はその食い違いをわざと作る。
"""

from __future__ import annotations

from datetime import timedelta

from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from tests.fixtures.backtest.harness import EXECUTION_SERIES, bars
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES

__all__ = [
    "CONFLICT_BAR",
    "DECISION_TIME",
    "QUIET_BAR",
    "RUN_INTERVAL",
    "STOP_LOSS_BAR",
    "TAKE_PROFIT_BAR",
    "execution_bars",
    "second_breakout_bars",
    "signal_bars",
]

#: T01 §2.2 の判断時刻。
DECISION_TIME = UtcTime.from_components(2026, 1, 6, 9, 0)

#: 経路1〜5 が使う run 区間（判断時刻から3時間）。
RUN_INTERVAL = Interval(start=DECISION_TIME, end=UtcTime.from_components(2026, 1, 6, 12, 0))

#: 1時間足の窓の先頭（21本ぶん遡る）。
_SIGNAL_START = UtcTime.from_components(2026, 1, 5, 12, 0)


def signal_bars(*, close: str = "150.040", stop_low: str = "149.500") -> tuple[Bar, ...]:
    """評価系列の21本（窓20本＋突破した足）。

    窓の高値の最大は 150.000、安値の最小は `stop_low` になるように明示して置く。末尾の足が
    「当該足」であり、助走の窓からは外れる。
    """
    body = str(decimal_from_str(stop_low) + decimal_from_str("0.005"))
    specs: list[tuple[str, str, str, str]] = [(body, "150.000", stop_low, body)] * 20
    specs.append(("149.900", "150.100", "149.800", close))
    return bars(SIGNAL_SERIES, _SIGNAL_START, timedelta(hours=1), specs)


def second_breakout_bars() -> tuple[Bar, ...]:
    """いったん水準を割り込んでから再び突破する2本の1時間足（経路5b）。

    2本目の終値 150.200 が、そのときの窓の高値の最大（150.100）を上回って再発火する。
    """
    return bars(
        SIGNAL_SERIES,
        DECISION_TIME,
        timedelta(hours=1),
        [
            ("150.040", "150.050", "149.800", "149.850"),
            ("149.850", "150.300", "149.800", "150.200"),
        ],
    )


#: 執行系列の先頭（判断時刻の直前に完了する足）。
_EXECUTION_START = UtcTime.from_components(2026, 1, 6, 8, 45)


#: `[11:00,11:15)` の4本値の既定（利確だけに触れる。経路1）。
TAKE_PROFIT_BAR = ("151.000", "151.300", "151.000", "151.200")

#: 損切りだけに触れる4本値（経路2）。
STOP_LOSS_BAR = ("150.100", "150.900", "149.400", "149.500")

#: 両方に触れる4本値（経路3b）。
CONFLICT_BAR = ("150.100", "151.300", "149.400", "150.200")

#: どちらにも触れない4本値（経路5b で建玉を開いたまま残す）。
QUIET_BAR = ("150.600", "150.900", "150.500", "150.700")


def execution_bars(
    *,
    reference_close: str = "150.040",
    conflict: tuple[str, str, str, str] = TAKE_PROFIT_BAR,
) -> tuple[Bar, ...]:
    """執行系列の13本（`[08:45, 12:00)`）。

    - 先頭の `[08:45,09:00)` が受付時の参照価格の出どころ（D06 §6.4 の手順3）
    - `[09:00,09:15)` の始値 150.050 で約定する
    - `[11:00,11:15)` の4本値が保護水準の到達を決める（経路1・2・3b で差し替える）
    """
    specs: list[tuple[str, str, str, str]] = [
        (reference_close, "150.060", reference_close, reference_close),
        ("150.050", "150.120", "150.020", "150.100"),
    ]
    specs.extend([("150.100", "150.200", "150.050", "150.150")] * 7)
    specs.append(conflict)
    specs.extend([("150.600", "150.900", "150.500", "150.700")] * 3)
    return bars(EXECUTION_SERIES, _EXECUTION_START, timedelta(minutes=15), specs)
