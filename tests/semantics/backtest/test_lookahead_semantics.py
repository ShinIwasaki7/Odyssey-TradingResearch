"""先読み不変: 未来の足を足しても、run の部品出力と意思決定は変わらない（上位 §7.2 #1）。

市場データの見え方だけなら `tests/property/marketdata/test_asof_properties.py` が押さえている。
ここでは**戦略の評価からバックテストの約定・台帳まで通した run** について、同じ run 区間を
「run 区間の後ろに未来の足を足した入力」でもう一度回し、判断履歴（trace の全表）が完全に
一致することを確かめる（D08 §13.3 #1）。比較は許容誤差なしの完全一致（D06 §9.1）。

足す未来の足は、漏れれば判断が変わるように作る: 評価系列には窓の高値を大きく上抜く gap を、
執行系列には損切りを飛び越える gap を置く（D08 §9.3 の生成器の入り口）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.money import PriceOffset, decimal_from_str
from odyssey_fx.common.time import Interval
from odyssey_fx.marketdata.domain.bar import Bar
from tests.fixtures.backtest.harness import CALENDAR, EXECUTION_SERIES, flatten_all, run_backtest
from tests.fixtures.backtest.paths import (
    CONFLICT_BAR,
    QUIET_BAR,
    RUN_INTERVAL,
    STOP_LOSS_BAR,
    TAKE_PROFIT_BAR,
    execution_bars,
    signal_bars,
)
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES
from tests.fixtures.synthetic import market

#: run 区間の終わりから6時間ぶんの未来。
_FUTURE = Interval(start=RUN_INTERVAL.end, end=RUN_INTERVAL.end + timedelta(hours=6))


def _future_signal_bars() -> tuple[Bar, ...]:
    """run 区間の後ろの1時間足。2本目で 3.000 上へ飛ぶ（漏れれば新しい突破に見える）。"""
    return market.make_bars(
        SIGNAL_SERIES,
        market.TF_1H,
        CALENDAR,
        _FUTURE,
        gaps={_FUTURE.start + timedelta(hours=1): PriceOffset(decimal_from_str("3.000"))},
    )


def _future_execution_bars() -> tuple[Bar, ...]:
    """run 区間の後ろの15分足。2本目で 3.000 下へ飛ぶ（漏れれば損切りの到達に見える）。"""
    return market.make_bars(
        EXECUTION_SERIES,
        market.TF_15M,
        CALENDAR,
        _FUTURE,
        gaps={_FUTURE.start + timedelta(minutes=15): PriceOffset(decimal_from_str("-3.000"))},
    )


@pytest.mark.parametrize(
    "conflict",
    [TAKE_PROFIT_BAR, STOP_LOSS_BAR, CONFLICT_BAR, QUIET_BAR],
    ids=["take_profit", "stop_loss", "conflict", "open_at_run_end"],
)
def test_adding_future_bars_does_not_change_the_decisions_of_the_run(
    conflict: tuple[str, str, str, str],
) -> None:
    """上位 §7.2 #1: 将来のデータを追加しても、過去時点の部品出力・意思決定が変わらない。"""
    signal = signal_bars()
    execution = execution_bars(conflict=conflict)
    future_signal = _future_signal_bars()
    future_execution = _future_execution_bars()
    assert all(RUN_INTERVAL.end <= bar.bar_start for bar in future_signal + future_execution)

    baseline = run_backtest(signal_bars=signal, execution_bars=execution, run_interval=RUN_INTERVAL)
    extended = run_backtest(
        signal_bars=signal + future_signal,
        execution_bars=execution + future_execution,
        run_interval=RUN_INTERVAL,
    )

    assert flatten_all(extended) == flatten_all(baseline)
    assert extended.result == baseline.result
    assert extended.context.ledger.balance == baseline.context.ledger.balance


def test_the_future_bars_change_the_run_once_the_run_covers_them() -> None:
    """上の一致が空振りでないことの確認: run 区間が未来の足を含めば、判断は実際に変わる。

    建玉を開いたまま run 区間の終わりを迎える入力（どちらの保護水準にも触れない足）で、
    run 区間を未来の足の終わりまで延ばすと、損切りを飛び越える gap で取引が1件成立する。
    """
    signal = signal_bars() + _future_signal_bars()
    execution = execution_bars(conflict=QUIET_BAR) + _future_execution_bars()

    short = run_backtest(signal_bars=signal, execution_bars=execution, run_interval=RUN_INTERVAL)
    long = run_backtest(
        signal_bars=signal,
        execution_bars=execution,
        run_interval=Interval(start=RUN_INTERVAL.start, end=_FUTURE.end),
    )

    assert short.result.trade_count == 0
    assert long.result.trade_count == 1
