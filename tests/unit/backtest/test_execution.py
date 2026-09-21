"""執行モデル（D06 §7.1〜§7.5、ADR-0030）。

約定価格・約定ずれ・提示価格の幅・保護水準の到達判定・足内競合解決を、人工データで1つずつ
確かめる。経路3a（下位足で解決）はここで検証する（実データでは5分足が無いため、T01 §4.1 が
「人工データ限定」と断った構成である）。
"""

from __future__ import annotations

from datetime import timedelta

from odyssey_fx.backtest.domain.orders import CloseCause, OrderSide
from odyssey_fx.backtest.domain.policies import ResolutionHierarchy
from odyssey_fx.backtest.domain.positions import ProtectionState
from odyssey_fx.backtest.execution.cost_model import FillPurpose, cost_entries
from odyssey_fx.backtest.execution.emergency import (
    gap_breaches_stop,
    needs_emergency_close,
    protection_base_price,
)
from odyssey_fx.backtest.execution.fill_model import (
    adverse_fill_excess,
    fill_price,
    is_adverse_fill_excessive,
)
from odyssey_fx.backtest.execution.protection_hits import (
    ResolutionMethod,
    hierarchy_checks,
    resolve_intrabar,
    touches,
)
from odyssey_fx.backtest.execution.spread import quote_for_side
from odyssey_fx.backtest.portfolio.conversion import identity_path, rate_of
from odyssey_fx.common.ids import FillId, PositionId
from odyssey_fx.common.money import Price, PriceOffset, Quantity, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.series import PriceBasis
from tests.fixtures.backtest.harness import (
    COST_MODEL,
    EXECUTION_SERIES,
    JPY,
    FakeIntrabarSeries,
    bars,
)
from tests.fixtures.synthetic.market import USDJPY, series

SPREAD = COST_MODEL.spread_model
MOMENT = UtcTime.from_components(2026, 1, 6, 11, 0)
CHILD_SERIES = series(USDJPY, "5m", PriceBasis.BID)


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _offset(text: str) -> PriceOffset:
    return PriceOffset(decimal_from_str(text))


def test_the_buy_quote_adds_the_spread() -> None:
    """D06 §7.2: bid のみの系列では `ask = bid + offset`。"""
    assert quote_for_side(_price("150.040"), OrderSide.BUY, SPREAD) == _price("150.060")
    assert quote_for_side(_price("150.040"), OrderSide.SELL, SPREAD) == _price("150.040")


def test_the_entry_fill_applies_the_slippage_against_the_order() -> None:
    """T01 §2.4: 始値 bid 150.050 → ask 150.070 → 滑り 0.010 → 150.080。"""
    assert fill_price(_price("150.050"), OrderSide.BUY, FillPurpose.ENTRY, COST_MODEL) == _price(
        "150.080"
    )


def test_the_close_of_a_long_uses_the_bid_and_moves_down() -> None:
    """D06 §7.1: 買い建玉の決済は売りなので bid を基準に価格を下げる。"""
    assert fill_price(
        _price("151.240"), OrderSide.SELL, FillPurpose.CLOSE, COST_MODEL, use_spread=False
    ) == _price("151.230")


def test_the_adverse_fill_excess_is_never_negative() -> None:
    """上位設計書 §4.7.9 C: `max(0, d x (P_fill - P_ref))`。"""
    assert adverse_fill_excess(_price("150.080"), _price("150.060"), OrderSide.BUY) == _offset(
        "0.020"
    )
    assert adverse_fill_excess(_price("150.040"), _price("150.060"), OrderSide.BUY) == _offset("0")


def test_the_limit_itself_is_allowed() -> None:
    """上位設計書 §4.7.9 C: 上限一致は許容する。"""
    assert not is_adverse_fill_excessive(
        _price("150.110"), _price("150.060"), OrderSide.BUY, _offset("0.05")
    )
    assert is_adverse_fill_excessive(
        _price("150.111"), _price("150.060"), OrderSide.BUY, _offset("0.05")
    )


def test_an_emergency_close_follows_the_same_rule() -> None:
    """D06 §7.5 の手順5: 約定ずれが Δ を超えたら緊急決済する。"""
    assert needs_emergency_close(
        _price("150.200"), _price("150.060"), OrderSide.BUY, _offset("0.05")
    )


def _protection(stop: str, take_profit: str | None = None) -> ProtectionState:
    key = bars(
        EXECUTION_SERIES,
        MOMENT,
        timedelta(minutes=15),
        [("151.000", "151.300", "151.000", "151.200")],
    )[0].key
    return ProtectionState(
        version=2,
        stop_loss=_price(stop),
        effective_from=key,
        take_profit=None if take_profit is None else _price(take_profit),
    )


def _parent(high: str, low: str) -> Bar:
    return bars(
        EXECUTION_SERIES,
        MOMENT,
        timedelta(minutes=15),
        [(low, high, low, high)],
    )[0]


def test_a_long_is_judged_on_the_bid() -> None:
    """D06 §7.3: 買い建玉の判定価格は bid、売り建玉は ask。"""
    stop_hit, tp_hit = touches(
        OrderSide.BUY, _protection("149.500", "151.240"), _parent("151.300", "151.000"), SPREAD
    )
    assert (stop_hit, tp_hit) == (False, True)


def test_a_single_hit_is_recorded_as_such() -> None:
    """D06 §7.4: 片側だけなら `SINGLE_HIT`。"""
    resolution = resolve_intrabar(
        position_id=PositionId(1),
        side=OrderSide.BUY,
        protection=_protection("149.500", "151.240"),
        parent=_parent("151.300", "151.000"),
        hierarchy=ResolutionHierarchy(levels=(EXECUTION_SERIES,)),
        spread_model=SPREAD,
        fill_id=FillId(1),
    )

    assert resolution is not None
    assert resolution.method is ResolutionMethod.SINGLE_HIT
    assert resolution.verdict is CloseCause.TAKE_PROFIT


def test_a_conflict_without_a_child_prefers_the_stop() -> None:
    """T01 §4.2: 階層が1段なら両側に触れた時点で最小解像度に達している。"""
    resolution = resolve_intrabar(
        position_id=PositionId(1),
        side=OrderSide.BUY,
        protection=_protection("149.500", "151.240"),
        parent=_parent("151.300", "149.400"),
        hierarchy=ResolutionHierarchy(levels=(EXECUTION_SERIES,)),
        spread_model=SPREAD,
        fill_id=FillId(1),
    )

    assert resolution is not None
    assert resolution.method is ResolutionMethod.UNRESOLVED_SL_PRIORITY
    assert resolution.verdict is CloseCause.STOP_LOSS
    assert resolution.resolved_child_bar_key is None


def _children() -> FakeIntrabarSeries:
    """親足 `[11:00,11:15)` を覆う5分足3本（T01 §4.1）。

    最初の子足で利確だけに触れるので、走査はそこで打ち切られる。
    """
    return FakeIntrabarSeries(
        {
            CHILD_SERIES: bars(
                CHILD_SERIES,
                MOMENT,
                timedelta(minutes=5),
                [
                    ("151.000", "151.300", "151.000", "151.200"),
                    ("151.200", "151.250", "149.400", "149.500"),
                    ("149.500", "149.600", "149.400", "149.500"),
                ],
            )
        }
    )


def test_a_conflict_is_resolved_by_the_first_child_that_touches_one_side() -> None:
    """T01 §4.1: 下位足で解決したら `RESOLVED_BY_CHILD` と使った系列を残す。"""
    resolution = resolve_intrabar(
        position_id=PositionId(1),
        side=OrderSide.BUY,
        protection=_protection("149.500", "151.240"),
        parent=_parent("151.300", "149.400"),
        hierarchy=ResolutionHierarchy(levels=(EXECUTION_SERIES, CHILD_SERIES)),
        spread_model=SPREAD,
        fill_id=FillId(2),
        child_bars=_children().bars_in,
    )

    assert resolution is not None
    assert resolution.method is ResolutionMethod.RESOLVED_BY_CHILD
    assert resolution.verdict is CloseCause.TAKE_PROFIT
    assert resolution.series_used == (EXECUTION_SERIES, CHILD_SERIES)
    assert resolution.resolved_child_bar_key is not None
    assert resolution.resolved_child_bar_key.bar_start == MOMENT


def test_the_hierarchy_checks_pass_for_a_complete_cover() -> None:
    """D06 §7.4 の検査1〜4: 被覆・価格基準・足境界・利用可能時刻。"""
    parent = _parent("151.300", "149.400")
    results = hierarchy_checks(
        ResolutionHierarchy(levels=(EXECUTION_SERIES, CHILD_SERIES)),
        (parent,),
        _children().bars_in,
    )

    assert [result.check for result in results] == [
        "coverage",
        "price_basis",
        "bar_boundary",
        "available_at",
    ]
    assert all(result.passed for result in results)


def test_a_gap_in_the_children_fails_the_coverage_check() -> None:
    """D06 §7.4 の検査1: 隙間があれば実行不可（暗黙に親足の4本値へ落とさない）。"""
    partial = FakeIntrabarSeries(
        {
            CHILD_SERIES: bars(
                CHILD_SERIES,
                MOMENT,
                timedelta(minutes=5),
                [("151.000", "151.300", "151.000", "151.200")],
            )
        }
    )
    results = hierarchy_checks(
        ResolutionHierarchy(levels=(EXECUTION_SERIES, CHILD_SERIES)),
        (_parent("151.300", "149.400"),),
        partial.bars_in,
    )

    coverage = results[0]
    assert not coverage.passed
    assert coverage.coverage_gaps == (
        Interval(start=MOMENT + timedelta(minutes=5), end=MOMENT + timedelta(minutes=15)),
    )


def test_a_hierarchy_of_one_level_runs_no_child_checks() -> None:
    """D06 §7.4: 検査1〜4 は下位足が宣言されている場合にだけ走る。"""
    assert hierarchy_checks(ResolutionHierarchy(levels=(EXECUTION_SERIES,)), (), None) == ()


def test_a_gap_below_the_stop_is_detected() -> None:
    """D06 §7.5 の手順1: 始値が損切りを飛び越えているか。"""
    assert gap_breaches_stop(OrderSide.BUY, _protection("149.500"), _price("149.400"), SPREAD)
    assert not gap_breaches_stop(OrderSide.BUY, _protection("149.500"), _price("150.050"), SPREAD)


def test_an_unreachable_protection_price_is_not_used() -> None:
    """上位設計書 §4.7.12: 始値が既に水準を越えていれば始値を基準にする。"""
    base, spread_applied = protection_base_price(
        OrderSide.SELL, _price("149.500"), _price("149.400"), SPREAD
    )
    assert base == _price("149.400")
    assert spread_applied is True

    base, spread_applied = protection_base_price(
        OrderSide.SELL, _price("149.500"), _price("150.000"), SPREAD
    )
    assert base == _price("149.500")
    assert spread_applied is False


def test_the_spread_cost_only_appears_on_the_buy_side() -> None:
    """D06 §7.6・T01 §9.3: bid のみの系列では買い側の約定にだけ提示幅が立つ。"""
    conversion = rate_of(identity_path(MOMENT), JPY, JPY)
    buy = cost_entries(
        COST_MODEL,
        purpose=FillPurpose.ENTRY,
        side=OrderSide.BUY,
        quantity=Quantity(decimal_from_str("32000")),
        currency=JPY,
        conversion=conversion,
    )
    sell = cost_entries(
        COST_MODEL,
        purpose=FillPurpose.CLOSE,
        side=OrderSide.SELL,
        quantity=Quantity(decimal_from_str("32000")),
        currency=JPY,
        conversion=conversion,
    )

    assert [entry.kind.value for entry in buy] == [
        "COMMISSION",
        "SLIPPAGE_IN_PRICE",
        "SPREAD_IN_PRICE",
    ]
    assert [entry.kind.value for entry in sell] == ["COMMISSION", "SLIPPAGE_IN_PRICE"]
