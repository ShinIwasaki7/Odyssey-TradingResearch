"""経路の取引の集合と恒等式（研究ポリシー第 5 版の設計 D24 §3.3、仮置き 4・14。実装 PR 4-1）。

- 集合は、各区間の run の完了取引（取引損益。入場の費用込み）と、各区間の終わりに仮に閉じた建玉
  （仮決済の損益（決済の費用を引いた後）− その建玉の入場の費用）。
- 恒等式: 1 件の損益の和 = 区間の終わりの費用を引いた後の資産 − 区間の初期残高（区間ごと）、
  = 経路の最後の残高 − 初期残高（経路の全体）。
- 受入テスト RP5-9: 恒等式が破れる入力で構造エラー（`KernelValueError`）。

値はすべて人工で、成績を読まない。
"""

from __future__ import annotations

from decimal import localcontext
from typing import Final

import pytest

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.trace.result import HypotheticalCloseLine
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import PositionId
from odyssey_fx.common.money import CurrencyCode, Money, Price, Quantity, decimal_from_str
from odyssey_fx.evaluation.domain.path import (
    PathTradeKind,
    SegmentTrades,
    SettledPosition,
    path_trade_set,
)
from tests.fixtures.evaluation.adoption_review import trade

JPY: Final = CurrencyCode("JPY")


def _yen(text: str) -> Money:
    return Money(decimal_from_str(text), JPY)


def _settled(seq: int, profit: str, entry_commission: str | None = "10") -> SettledPosition:
    """区間の終わりに仮に閉じた建玉 1 つ（明細の値は人工。損益だけが集合に効く）。"""
    return SettledPosition(
        line=HypotheticalCloseLine(
            position_id=PositionId(seq=seq),
            side=OrderSide.BUY,
            quantity=Quantity(decimal_from_str("10000")),
            reference_bid=Price(decimal_from_str("150.00")),
            exit_price=Price(decimal_from_str("149.99")),
            commission=_yen("10"),
            slippage=_yen("100"),
            profit=_yen(profit),
        ),
        entry_commission=None if entry_commission is None else _yen(entry_commission),
    )


#: 完了取引: 勝ち 4,990 円（150.00 → 150.50、手数料 10 円）と負け −2,010 円（150.00 → 149.80）。
WIN: Final = trade("2020-01-06T00:00:00Z", "2020-01-07T00:00:00Z", seq=1)
LOSS: Final = trade("2020-01-08T00:00:00Z", "2020-01-09T00:00:00Z", exit_price="149.80", seq=2)


def _two_segments() -> tuple[SegmentTrades, ...]:
    """区間 0: 勝ち 4,990 と区間の終わりの仮決済（−1,500 − 入場 10 = −1,510）。区間 1: 負け
    −2,010。区間 0 は 1,000,000 → 1,003,480、区間 1 は 1,003,480 → 1,001,470。"""
    return (
        SegmentTrades(
            segment_index=0,
            initial_balance=_yen("1000000"),
            settled_equity=_yen("1003480"),
            closed_trades=(WIN,),
            settled=(_settled(3, "-1500"),),
        ),
        SegmentTrades(
            segment_index=1,
            initial_balance=_yen("1003480"),
            settled_equity=_yen("1001470"),
            closed_trades=(LOSS,),
            settled=(),
        ),
    )


def test_the_set_holds_closed_trades_and_segment_end_settlements_in_order() -> None:
    result = path_trade_set(
        _two_segments(), initial_balance=_yen("1000000"), final_balance=_yen("1001470")
    )
    assert [(item.segment_index, item.kind) for item in result.trades] == [
        (0, PathTradeKind.CLOSED_TRADE),
        (0, PathTradeKind.SEGMENT_END_SETTLEMENT),
        (1, PathTradeKind.CLOSED_TRADE),
    ]
    assert [item.profit for item in result.trades] == [_yen("4990"), _yen("-1510"), _yen("-2010")]
    assert result.net == _yen("1470")


def test_the_settlement_loss_enters_the_losses_with_its_entry_cost() -> None:
    """区間末の含み損を抜かない（D24 §3.2・§3.3）。`W = 4,990`、`L = 1,510 + 2,010 = 3,520`。"""
    result = path_trade_set(
        _two_segments(), initial_balance=_yen("1000000"), final_balance=_yen("1001470")
    )
    assert result.wins == _yen("4990")
    assert result.losses == _yen("3520")
    assert (result.win_count, result.loss_count) == (1, 2)


def test_a_settlement_without_an_entry_commission_record_subtracts_nothing() -> None:
    assert _settled(1, "-1500", entry_commission=None).profit == _yen("-1500")
    assert _settled(1, "250", entry_commission="10").profit == _yen("240")


def test_a_path_without_trades_is_an_empty_set() -> None:
    """取引も仮決済も 0 件の経路は空の集合（値なしの扱いは判定の側。D24 §3.3）。"""
    segment = SegmentTrades(
        segment_index=0,
        initial_balance=_yen("1000000"),
        settled_equity=_yen("1000000"),
        closed_trades=(),
        settled=(),
    )
    result = path_trade_set(
        (segment,), initial_balance=_yen("1000000"), final_balance=_yen("1000000")
    )
    assert result.trades == ()
    assert result.wins == result.losses == _yen("0")


def test_rp5_9_a_broken_segment_identity_is_a_structural_error() -> None:
    """RP5-9: 区間の取引の損益の和が区間の資産の増減と 1 円でも違えば構造エラー。"""
    first, second = _two_segments()
    broken = SegmentTrades(
        segment_index=0,
        initial_balance=first.initial_balance,
        settled_equity=_yen("1003481"),
        closed_trades=first.closed_trades,
        settled=first.settled,
    )
    with pytest.raises(KernelValueError, match="恒等式"):
        path_trade_set(
            (broken, second), initial_balance=_yen("1000000"), final_balance=_yen("1001471")
        )


def test_rp5_9_a_broken_path_identity_is_a_structural_error() -> None:
    """RP5-9: 区間ごとは合っていても、経路の最後の残高と合わなければ構造エラー。"""
    with pytest.raises(KernelValueError, match="恒等式"):
        path_trade_set(
            _two_segments(), initial_balance=_yen("1000000"), final_balance=_yen("1001469")
        )


def test_rp5_9_a_dropped_settlement_breaks_the_identity() -> None:
    """区間の終わりの仮決済を落とした集合（完了取引だけ）は恒等式が破れて止まる。"""
    first, second = _two_segments()
    without_settlement = SegmentTrades(
        segment_index=0,
        initial_balance=first.initial_balance,
        settled_equity=first.settled_equity,
        closed_trades=first.closed_trades,
        settled=(),
    )
    with pytest.raises(KernelValueError, match="恒等式"):
        path_trade_set(
            (without_settlement, second),
            initial_balance=_yen("1000000"),
            final_balance=_yen("1001470"),
        )


def test_the_same_position_twice_in_a_segment_is_refused() -> None:
    with pytest.raises(KernelValueError, match="同じ建玉"):
        SegmentTrades(
            segment_index=0,
            initial_balance=_yen("1000000"),
            settled_equity=_yen("1003480"),
            closed_trades=(WIN,),
            settled=(_settled(1, "-1500"),),
        )


def test_segments_must_be_in_ascending_order() -> None:
    first, second = _two_segments()
    with pytest.raises(KernelValueError, match="昇順"):
        path_trade_set(
            (second, first), initial_balance=_yen("1000000"), final_balance=_yen("1001470")
        )


def test_the_totals_do_not_depend_on_the_callers_decimal_context() -> None:
    """勝ち・負けの合計はカーネルの精度で求める（呼び出し側の精度で丸めない。D07 §5.5）。"""
    result = path_trade_set(
        _two_segments(), initial_balance=_yen("1000000"), final_balance=_yen("1001470")
    )
    with localcontext() as context:
        context.prec = 2
        wins, losses, net = result.wins, result.losses, result.net
    assert (wins, losses, net) == (_yen("4990"), _yen("3520"), _yen("1470"))
