"""指標の値の型と純粋な計算（D07 §5.1・§5.2・§7.2）。"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import (
    CurrencyCode,
    Money,
    PriceOffset,
    decimal_from_str,
    kernel_context,
)
from odyssey_fx.evaluation.domain.metrics import (
    METRIC_KINDS,
    AmountValue,
    CategoryCount,
    CategoryKind,
    CountValue,
    DurationValue,
    MetricCaveat,
    MetricId,
    MetricKind,
    MetricRecord,
    MetricUnavailableReason,
    PriceOffsetValue,
    RatioValue,
    TradeOutcome,
    Unavailable,
    max_drawdown,
    metric_caveats,
    ratio_of,
    trade_outcome,
)

JPY = CurrencyCode("JPY")


def _amounts(*values: str) -> list[Decimal]:
    return [decimal_from_str(value) for value in values]


def test_the_metric_set_has_exactly_the_fifteen_of_the_design() -> None:
    """段階2の指標は15件（D07 §5.2）。並びは宣言順で固定する（D07 §8.1）。"""
    assert len(list(MetricId)) == 15
    assert list(MetricId)[0] is MetricId.NET_PROFIT
    assert list(MetricId)[-1] is MetricId.NET_RETURN_RATE
    assert set(METRIC_KINDS) == set(MetricId)


def test_the_drawdown_scan_finds_the_deepest_fall_and_its_peak() -> None:
    """`max(これまでの最大値 − 現在値)` と、その時点の最大値を返す（D07 §5.2 の #5・#6）。

    T01 §9.4 の含み損益込みの資産の推移で確かめる。最大は入場直後の 1,312 円で、分母は
    そのときの最大値 1,000,000 円である（あとから出る 30 円の沈みは採らない）。
    """
    fall = max_drawdown(_amounts("1000000", "998688", "1036736", "1036706", "1051706"))
    assert fall is not None
    assert fall.amount == decimal_from_str("1312")
    assert fall.peak == decimal_from_str("1000000")
    assert ratio_of(fall.amount, fall.peak) == decimal_from_str("0.001312")


def test_the_drawdown_scan_uses_the_balance_column_the_same_way() -> None:
    """確定損益だけで走査すると 32 円になる（T01 §9.4、D07 §5.2 の #7・#8）。"""
    fall = max_drawdown(_amounts("1000000", "999968", "1036736", "1036706", "1036706"))
    assert fall is not None
    assert fall.amount == decimal_from_str("32")
    assert ratio_of(fall.amount, fall.peak) == decimal_from_str("0.000032")


def test_the_drawdown_of_an_empty_series_is_absent_not_zero() -> None:
    """行が1件も無ければ `None`（値なしを 0 へ置換しない、D07 §5.1）。"""
    assert max_drawdown([]) is None


def test_the_drawdown_keeps_the_first_peak_when_two_falls_tie() -> None:
    """同じ深さの沈みが2度あるときは先に現れた方の最大値を分母にする（D07 §5.2 の #5）。

    後から同じ深さになっても分母を動かさない。動かすと同じ判断履歴から2つの率が出る。
    """
    fall = max_drawdown(_amounts("100", "90", "100", "110", "100"))
    assert fall is not None
    assert fall.amount == decimal_from_str("10")
    assert fall.peak == decimal_from_str("100")


def test_a_monotonically_rising_series_has_no_drawdown() -> None:
    fall = max_drawdown(_amounts("100", "110", "120"))
    assert fall is not None
    assert fall.amount == decimal_from_str("0")


def test_the_ratio_divides_once_at_the_kernel_precision() -> None:
    """比率は除算を1回だけカーネル精度で行い、丸めない（D07 §5.1、Q2 決定）。"""
    with localcontext(kernel_context()) as context:
        expected = decimal_from_str("1") / decimal_from_str("3")
        assert context.prec == 28
    assert ratio_of(decimal_from_str("1"), decimal_from_str("3")) == expected


def test_a_zero_denominator_is_refused_instead_of_returning_zero() -> None:
    """分母 0 は例外にする。0 を返すと「0 という比率」と区別できない（D07 §10.3）。"""
    with pytest.raises(KernelValueError):
        ratio_of(decimal_from_str("1"), decimal_from_str("0"))


@pytest.mark.parametrize(
    ("realized", "expected"),
    [
        ("1", TradeOutcome.WIN),
        ("-1", TradeOutcome.LOSS),
        ("0", TradeOutcome.BREAK_EVEN),
    ],
)
def test_the_trade_outcome_splits_win_loss_and_break_even(
    realized: str, expected: TradeOutcome
) -> None:
    """`= 0` は勝ちに数えない（D07 §5.2 の #4）。"""
    assert trade_outcome(Money(decimal_from_str(realized), JPY)) is expected


def test_the_caveats_follow_the_declaration_order() -> None:
    """注記は宣言順に並ぶ（D07 §7.2）。並びが揺れると結果のダイジェストが揺れる。"""
    caveats = metric_caveats(MetricId.CLOSED_TRADE_PROFIT, unresolved_intrabar=True)
    assert caveats == (
        MetricCaveat.SWAP_NOT_MODELED,
        MetricCaveat.OPEN_POSITION_EXCLUDED,
        MetricCaveat.ENTRY_COST_EXCLUDED,
        MetricCaveat.UNRESOLVED_INTRABAR_PRESENT,
    )


def test_the_unresolved_intrabar_caveat_is_added_only_when_there_is_one() -> None:
    """未解決の足内競合が無い run では注記を付けない（D07 §7.2）。"""
    assert MetricCaveat.UNRESOLVED_INTRABAR_PRESENT not in metric_caveats(
        MetricId.NET_PROFIT, unresolved_intrabar=False
    )
    assert MetricCaveat.UNRESOLVED_INTRABAR_PRESENT in metric_caveats(
        MetricId.NET_PROFIT, unresolved_intrabar=True
    )


def test_the_reference_metrics_carry_the_open_position_caveat() -> None:
    """参考値には未決済建玉に関する注記が付く（D07 §5.3・§7.2）。"""
    for metric_id in (
        MetricId.CLOSED_TRADE_PROFIT,
        MetricId.MAX_DRAWDOWN_BALANCE,
        MetricId.MAX_DRAWDOWN_BALANCE_RATE,
        MetricId.END_EQUITY_MTM,
        MetricId.HYPOTHETICAL_CLOSED_PROFIT,
    ):
        assert MetricCaveat.OPEN_POSITION_EXCLUDED in metric_caveats(
            metric_id, unresolved_intrabar=False
        )


def test_a_metric_record_refuses_a_value_of_the_wrong_kind() -> None:
    """宣言した種別と違う値は構築時に拒否する（D07 §5.2 の「種別」欄）。"""
    with pytest.raises(KernelValueError):
        MetricRecord(
            metric_id=MetricId.NET_PROFIT,
            value=CountValue(1),
            inputs=(TraceTable.LEDGER_SNAPSHOTS,),
        )


def test_a_metric_record_refuses_caveats_out_of_order() -> None:
    """注記の並びが宣言順でなければ拒否する（D07 §9.1 の条件4）。"""
    with pytest.raises(KernelValueError):
        MetricRecord(
            metric_id=MetricId.NET_PROFIT,
            value=AmountValue(Money(decimal_from_str("1"), JPY)),
            caveats=(MetricCaveat.UNRESOLVED_INTRABAR_PRESENT, MetricCaveat.SWAP_NOT_MODELED),
        )


def test_a_metric_record_keeps_the_unavailable_value_as_a_row() -> None:
    """値なしでも行は残る（D07 §5.1）。種別は指標の宣言から来る。"""
    record = MetricRecord(
        metric_id=MetricId.WIN_RATE,
        value=Unavailable(MetricKind.RATIO, MetricUnavailableReason.NO_TRADES),
    )
    assert record.value.kind is MetricKind.RATIO
    assert isinstance(record.value, Unavailable)
    assert record.value.reason is MetricUnavailableReason.NO_TRADES


def test_the_value_kinds_refuse_a_mismatched_tag() -> None:
    """区分タグを付け替えた値は構築時に拒否する（D01 §8）。"""
    with pytest.raises(KernelValueError):
        AmountValue(Money(decimal_from_str("1"), JPY), kind=MetricKind.RATIO)
    with pytest.raises(KernelValueError):
        RatioValue(decimal_from_str("1"), kind=MetricKind.AMOUNT)
    with pytest.raises(KernelValueError):
        CountValue(1, kind=MetricKind.RATIO)
    with pytest.raises(KernelValueError):
        DurationValue(timedelta(0), kind=MetricKind.COUNT)
    with pytest.raises(KernelValueError):
        PriceOffsetValue(PriceOffset(decimal_from_str("0")), kind=MetricKind.AMOUNT)


def test_a_category_count_refuses_a_negative_count() -> None:
    with pytest.raises(KernelValueError):
        CategoryCount(category=CategoryKind.CLOSE_CAUSE, key="TAKE_PROFIT", count=-1)
