"""指標の値の型と純粋な計算（D07 §5.1・§5.2・§5.5・§7.2・§7.3）。"""

from __future__ import annotations

from datetime import date, time, timedelta
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
from odyssey_fx.common.time import Interval, UtcTime
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
    annualized_return,
    annualized_sharpe_ratio,
    average_trade_profit,
    max_drawdown,
    metric_caveats,
    profit_factor,
    ratio_of,
    trade_outcome,
    trade_profit,
    trading_day_ends,
)
from odyssey_fx.marketdata.domain.calendar import TradingCalendar, WeeklyMoment
from tests.fixtures.synthetic import market

JPY = CurrencyCode("JPY")


def _amounts(*values: str) -> list[Decimal]:
    return [decimal_from_str(value) for value in values]


def test_the_metric_set_has_exactly_the_nineteen_of_the_design() -> None:
    """指標集合 v2 は19件（D07 §5.2 の15件＋§5.5 の4件）。並びは宣言順で固定する（§8.1）。"""
    assert len(list(MetricId)) == 19
    assert list(MetricId)[0] is MetricId.NET_PROFIT
    assert list(MetricId)[14] is MetricId.NET_RETURN_RATE
    assert list(MetricId)[15:] == [
        MetricId.ANNUALIZED_RETURN,
        MetricId.ANNUALIZED_SHARPE_RATIO,
        MetricId.PROFIT_FACTOR,
        MetricId.AVERAGE_TRADE_PROFIT,
    ]
    assert set(METRIC_KINDS) == set(MetricId)
    assert METRIC_KINDS[MetricId.AVERAGE_TRADE_PROFIT] is MetricKind.AMOUNT


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
        MetricCaveat.UNRESOLVED_INTRABAR_PRESENT,
    )


def test_the_entry_cost_caveat_is_gone_in_metric_set_two() -> None:
    """指標集合 v2 は入場費用を含めるので、注記 `ENTRY_COST_EXCLUDED` を列挙から外した（§7.3）。"""
    assert [item.value for item in MetricCaveat] == [
        "SWAP_NOT_MODELED",
        "PRICE_EMBEDDED_COST",
        "OPEN_POSITION_EXCLUDED",
        "UNRESOLVED_INTRABAR_PRESENT",
    ]


def test_the_unresolved_intrabar_caveat_covers_the_new_trade_metrics() -> None:
    """#16・#18・#19 は未解決の足内競合の注記の対象、#17 は対象外（D07 §5.5・§7.2）。"""
    for metric_id in (
        MetricId.ANNUALIZED_RETURN,
        MetricId.PROFIT_FACTOR,
        MetricId.AVERAGE_TRADE_PROFIT,
    ):
        assert MetricCaveat.UNRESOLVED_INTRABAR_PRESENT in metric_caveats(
            metric_id, unresolved_intrabar=True
        )
    assert MetricCaveat.UNRESOLVED_INTRABAR_PRESENT not in metric_caveats(
        MetricId.ANNUALIZED_SHARPE_RATIO, unresolved_intrabar=True
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


# --- 指標集合 v2 の4件（D07 §5.5）と取引損益（D07 §7.3）----------------------


def test_the_trade_profit_subtracts_only_the_entry_commission() -> None:
    """`trade_profit = realized − entry_commission`（D07 §7.3）。記録が無ければ 0 として引く。"""
    realized = Money(decimal_from_str("36768"), JPY)
    assert trade_profit(realized, Money(decimal_from_str("32"), JPY)) == Money(
        decimal_from_str("36736"), JPY
    )
    assert trade_profit(realized, None) == realized


def test_the_annualized_return_multiplies_before_it_divides() -> None:
    """#16 `(#15 × 260) ÷ N`（T01: `0.036706 × 260 ÷ 10 = 0.954356`）。`N = 0` は定まらない。"""
    assert annualized_return(decimal_from_str("0.036706"), 10) == decimal_from_str("0.954356")
    assert annualized_return(decimal_from_str("0.036706"), 0) is None


def test_the_sharpe_ratio_of_the_design_example() -> None:
    """#17 の検算例（D07 §5.5）: `E = 1,000,000 → 1,010,000 → 999,900 → 1,009,899`。

    `r = 0.01, −0.01, 0.01` で、値は `√(65/3) ≈ 4.6547466812563`。**28桁の値を固定する**
    （D07 §5.5 が PR 1 の単体テストで固定するとした値）。式の順（平均 → 分散 → 平方根 →
    比 → `√260` 倍）でカーネル精度の丸めが各演算に入るので、`(65/3).sqrt()` を直接求めた
    値とは末尾の1桁だけ違う。
    """
    value = annualized_sharpe_ratio(_amounts("1000000", "1010000", "999900", "1009899"))
    assert value == decimal_from_str("4.654746681256313723294641513")
    with localcontext(kernel_context()):
        direct = (Decimal(65) / Decimal(3)).sqrt()
    assert isinstance(value, Decimal)
    assert abs(value - direct) <= decimal_from_str("1e-27")


def test_the_sharpe_ratio_needs_two_days_and_a_spread() -> None:
    """`N < 2` は `NO_OBSERVATIONS`、`s = 0` と `E_{k−1} = 0` は `UNDEFINED_DENOMINATOR`。"""
    assert (
        annualized_sharpe_ratio(_amounts("1000000", "1010000"))
        is MetricUnavailableReason.NO_OBSERVATIONS
    )
    assert (
        annualized_sharpe_ratio(_amounts("1000000", "1000000", "1000000"))
        is MetricUnavailableReason.UNDEFINED_DENOMINATOR
    )
    assert (
        annualized_sharpe_ratio(_amounts("1000000", "0", "10"))
        is MetricUnavailableReason.UNDEFINED_DENOMINATOR
    )


def test_the_profit_factor_divides_the_wins_by_the_losses() -> None:
    """#18 `勝ちの合計 ÷ |負けの合計|`。負けが無ければ値なし、0取引なら `NO_TRADES`。"""
    profits = [Money(decimal_from_str(value), JPY) for value in ("300", "-100", "0", "-50")]
    assert profit_factor(profits) == decimal_from_str("2")
    assert (
        profit_factor([Money(decimal_from_str("1"), JPY)])
        is MetricUnavailableReason.UNDEFINED_DENOMINATOR
    )
    assert profit_factor([]) is MetricUnavailableReason.NO_TRADES


def test_the_average_trade_profit_divides_once_without_rounding() -> None:
    """#19 は金額だが除算を含むので、カーネル精度で1回割って丸めない（D07 §5.5）。"""
    profits = [Money(decimal_from_str(value), JPY) for value in ("10", "0", "0")]
    average = average_trade_profit(profits)
    assert average is not None
    with localcontext(kernel_context()):
        assert average.amount == Decimal(10) / Decimal(3)
    assert average_trade_profit([]) is None


def _interval(start: str, end: str) -> Interval:
    return Interval(start=UtcTime.parse(start), end=UtcTime.parse(end))


def test_the_trading_days_of_the_paper_trace_run_are_ten() -> None:
    """T01 の run 区間（2015-01-04 22:00Z〜01-16 22:00Z）の取引日は10日（D07 §5.5 の `N`）。

    週末（金曜 17:00〜日曜 17:00 NY）は開場時間が無いので数えない。区間の終わりちょうどに
    終わる取引日（1/16）は数える（`(start, end]`）。
    """
    ends = trading_day_ends(
        market.calendar(), _interval("2015-01-04T22:00:00Z", "2015-01-16T22:00:00Z")
    )
    assert ends is not None
    assert len(ends) == 10
    assert str(ends[0]) == "2015-01-05T22:00:00Z"
    assert str(ends[-1]) == "2015-01-16T22:00:00Z"


def test_a_trading_day_closure_is_not_counted() -> None:
    """取引日単位の休場（D03 §3.4.1）は開場時間が無いので数えない（元日の例）。"""
    calendar = market.calendar(closures=(market.closure(date(2016, 1, 1), trading_day=True),))
    window = _interval("2015-12-27T22:00:00Z", "2016-01-01T22:00:00Z")
    ends = trading_day_ends(calendar, window)
    assert ends is not None
    assert [str(end) for end in ends] == [
        "2015-12-28T22:00:00Z",
        "2015-12-29T22:00:00Z",
        "2015-12-30T22:00:00Z",
        "2015-12-31T22:00:00Z",
    ]


def test_the_trading_day_boundary_follows_daylight_saving_time() -> None:
    """夏時間（EDT）では NY 17:00 は UTC 21:00 になる（D03 §3.2 の解決規則）。"""
    ends = trading_day_ends(
        market.calendar(), _interval("2015-07-05T21:00:00Z", "2015-07-07T21:00:00Z")
    )
    assert ends is not None
    assert [str(end) for end in ends] == ["2015-07-06T21:00:00Z", "2015-07-07T21:00:00Z"]


def test_a_calendar_without_one_trading_day_boundary_gives_no_days() -> None:
    """週の開閉時刻が違うカレンダーでは取引日の境界が決まらない（D03 §3.4.1）。"""
    calendar = TradingCalendar(
        id="odd",
        version=1,
        tz=market.NEW_YORK,
        weekly_open=WeeklyMoment(weekday=6, at=time(17, 0)),
        weekly_close=WeeklyMoment(weekday=4, at=time(16, 0)),
    )
    assert (
        trading_day_ends(calendar, _interval("2015-01-04T22:00:00Z", "2015-01-09T22:00:00Z"))
        is None
    )
