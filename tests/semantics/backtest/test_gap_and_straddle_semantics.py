"""gap と SL/TP 同時到達を、汎用生成器の入り口で作って手計算と突き合わせる（上位 §7.2 #5）。

足は `tests/fixtures/synthetic/market.py` の `make_bars` で作り、`gaps` / `straddles` だけで
条件を指定する（D08 §9.3）。値は生成器の決定論的な価格なので、手計算の根拠を docstring に書く。

共通の場面: 金曜 2026-01-23 20:00Z（ニューヨーク 15:00、週の終わりの2時間前）に検証戦略 A が
買いの突破を判断する。窓の高値の最大は 150.000、安値の最小（損切り）は 149.000。

- 参照価格: 直前に完了した執行足 `[19:45,20:00)` の終値 150.92。ask は ＋0.02 で 150.94
- 数量: `(150.94 + Δ0.05 − 149.000) + (手数料 0.001 × 2 ＋ 決済滑り 0.01) = 2.002`、
  `20000 / 2.002 = 9990.0…` → 1000 単位へ切り捨てて 9000
- 約定: `[20:00,20:15)` の始値 bid 150.00 → ask 150.02 → 滑り 0.01 → 150.03
- 利確: `150.03 + 2 × (150.03 − 149.000) = 152.090`
- 生成器の素の足は安値が 149.50 以上、高値が 151.49 以下なので、条件を足さなければ損切りにも
  利確にも触れない
"""

from __future__ import annotations

from datetime import timedelta

from odyssey_fx.backtest.domain.fills import FillRecord
from odyssey_fx.backtest.domain.orders import CloseCause, CloseRequest, OrderRequest
from odyssey_fx.backtest.execution.protection_hits import IntrabarResolution, ResolutionMethod
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.money import Money, Price, PriceOffset, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from tests.fixtures.backtest.harness import (
    CALENDAR,
    EXECUTION_SERIES,
    JPY,
    RunOutput,
    bars,
    run_backtest,
)
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES
from tests.fixtures.synthetic import market

#: 判断時刻（金曜 20:00Z）。
DECISION_TIME = UtcTime.from_components(2026, 1, 23, 20, 0)

#: 週明けの最初の執行足（日曜ニューヨーク 17:00 ＝ 冬時間の 22:00Z）。
WEEK_REOPEN = UtcTime.from_components(2026, 1, 25, 22, 0)

#: 執行系列の先頭（判断時刻の直前に完了する足。参照価格の出どころ）。
_EXECUTION_START = DECISION_TIME - timedelta(minutes=15)


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _signal_bars() -> tuple[Bar, ...]:
    """評価系列の21本（窓20本＋判断時刻に確定する突破の足）。"""
    specs = [("149.005", "150.000", "149.000", "149.005")] * 20
    specs.append(("149.900", "150.100", "149.800", "150.040"))
    return bars(SIGNAL_SERIES, DECISION_TIME - timedelta(hours=21), timedelta(hours=1), specs)


def _fills(output: RunOutput) -> tuple[FillRecord, ...]:
    return tuple(row for row in output.rows(TraceTable.FILLS) if isinstance(row, FillRecord))


def _close_causes(output: RunOutput) -> list[CloseCause]:
    return [
        row.payload.cause
        for row in output.rows(TraceTable.ORDER_REQUESTS)
        if isinstance(row, OrderRequest) and isinstance(row.payload, CloseRequest)
    ]


def test_a_weekend_gap_below_the_stop_closes_at_the_gapped_open() -> None:
    """上位 §7.2 #5・D06 §7.5: 週明けの窓開けで損切りを飛び越えたら、その始値で損切り決済する。

    手計算: 金曜最後の執行足 `[21:45,22:00)` の終値は 150.12。週明けの足に差 −1.500 の gap を
    置くと始値は 148.62 で、損切り 149.000 を飛び越える。決済は到達不能な損切りの価格ではなく
    始値 bid 148.62 を基準にし、決済の滑り 0.01 を不利な方向へ当てて 148.61。
    残高: `1,000,000 + (148.61 − 150.03) × 9000 − 手数料 9 × 2 = 987,202`。
    """
    run_end = WEEK_REOPEN + timedelta(minutes=30)
    execution = market.make_bars(
        EXECUTION_SERIES,
        market.TF_15M,
        CALENDAR,
        Interval(start=_EXECUTION_START, end=run_end),
        gaps={WEEK_REOPEN: PriceOffset(decimal_from_str("-1.500"))},
    )
    index = [bar.bar_start for bar in execution].index(WEEK_REOPEN)
    friday_last, gapped = execution[index - 1], execution[index]
    assert friday_last.bar_end == DECISION_TIME + timedelta(hours=2)
    assert friday_last.close == _price("150.12")
    assert gapped.open == _price("148.62")

    output = run_backtest(
        signal_bars=_signal_bars(),
        execution_bars=execution,
        run_interval=Interval(start=DECISION_TIME, end=run_end),
    )

    entry, close = _fills(output)
    assert entry.price == _price("150.03")
    assert entry.quantity.units == decimal_from_str("9000")
    assert close.price == _price("148.61")
    assert close.processed_at.time == WEEK_REOPEN
    assert _close_causes(output) == [CloseCause.STOP_LOSS]
    assert output.context.ledger.balance == Money(decimal_from_str("987202"), JPY)


def test_a_bar_touching_both_the_stop_and_the_target_is_counted_as_unresolved() -> None:
    """ADR-0030・D06 §7.4: 損切りと利確の両方に触れた足は、実行側が未解決として数える。

    手計算: 損切り 149.000、利確 152.090。`[20:30,20:45)` の足に 148.500 と 152.500 を包ませる。
    生成器は高値・安値を広げるだけで、どちらが先に触れたかは決めない。執行系列の階層は
    15分足の1段だけなので、実行側はこの足の中の順序を観測できず、未解決の競合として1件数える。
    """
    straddled_start = DECISION_TIME + timedelta(minutes=30)
    run_end = DECISION_TIME + timedelta(hours=1)
    execution = market.make_bars(
        EXECUTION_SERIES,
        market.TF_15M,
        CALENDAR,
        Interval(start=_EXECUTION_START, end=run_end),
        straddles={straddled_start: (_price("152.500"), _price("148.500"))},
    )

    output = run_backtest(
        signal_bars=_signal_bars(),
        execution_bars=execution,
        run_interval=Interval(start=DECISION_TIME, end=run_end),
    )

    resolutions = [
        row
        for row in output.rows(TraceTable.INTRABAR_RESOLUTIONS)
        if isinstance(row, IntrabarResolution)
    ]
    assert len(resolutions) == 1
    assert resolutions[0].parent_bar_key.bar_start == straddled_start
    assert resolutions[0].method is ResolutionMethod.UNRESOLVED_SL_PRIORITY
    assert output.result.unresolved_intrabar_count == 1
