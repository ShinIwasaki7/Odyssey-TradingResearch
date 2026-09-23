"""台帳・含み損益・通貨換算（D06 §4.4・§8.1・§8.5）。

確定単位の原子性と冪等性、消費済み枠の式、含み損益の評価価格、恒等換算の経路を確かめる。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.backtest.domain.account import AccountLedger
from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.domain.positions import (
    Position,
    PositionRiskAllocation,
    ProtectionState,
)
from odyssey_fx.backtest.domain.reservations import (
    ReservationState,
    ReservationStatus,
    RiskReservation,
    move_reservation,
)
from odyssey_fx.backtest.engine.phases import BACKTEST_PHASES
from odyssey_fx.backtest.portfolio.conversion import (
    ConversionUnavailable,
    identity_path,
    rate_of,
    resolve_path,
)
from odyssey_fx.backtest.portfolio.ledger import snapshot_of
from odyssey_fx.backtest.portfolio.mtm import exit_price_for, unrealized
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AllocationId,
    EventId,
    EvidenceId,
    FillId,
    OrderId,
    PositionId,
    ReservationId,
    RunId,
)
from odyssey_fx.common.money import (
    CurrencyCode,
    Money,
    Price,
    Quantity,
    decimal_from_str,
)
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import ProcessingPoint, UtcTime
from tests.fixtures.backtest.harness import ACCOUNT, CONVERSION_POLICY, COST_MODEL, JPY
from tests.fixtures.backtest.paths import execution_bars
from tests.fixtures.synthetic.market import USDJPY

MOMENT = UtcTime.from_components(2026, 1, 6, 9, 0)
AT = ProcessingPoint(time=MOMENT, phase=BACKTEST_PHASES.by_name("EXECUTION_OPEN"), sequence=0)


def _money(text: str) -> Money:
    return Money(decimal_from_str(text), JPY)


def _position(stop: str = "149.500") -> Position:
    key = execution_bars()[1].key
    return Position(
        position_id=PositionId(1),
        account_id=ACCOUNT.account_id,
        strategy_id="strategy_a",
        symbol=USDJPY,
        side=OrderSide.BUY,
        quantity=Quantity(decimal_from_str("32000")),
        entry_fill_id=FillId(1),
        entry_price=Price(decimal_from_str("150.080")),
        opened_at=AT,
        protection=ProtectionState(
            version=1, stop_loss=Price(decimal_from_str(stop)), effective_from=key
        ),
    )


def _reservation(amount: str = "19904") -> RiskReservation:
    return RiskReservation(
        run_id=_run_id(),
        reservation_id=ReservationId(1),
        order_id=OrderId(1),
        account_id=ACCOUNT.account_id,
        created_at=AT,
        amount=_money(amount),
        assessment_id=EvidenceId(2),
    )


def _run_id() -> RunId:

    return RunId(ContentDigest.sha256("1" * 64))


def _held() -> ReservationState:
    return ReservationState(
        reservation_id=ReservationId(1),
        status=ReservationStatus.HELD,
        last_event_id=EventId(1),
        last_processed_at=AT,
    )


def test_the_consumed_budget_counts_held_reservations() -> None:
    """上位設計書 §4.7.15 D: `U` = HELD 予約額 ＋ 未解放の建玉割当額。"""
    ledger = AccountLedger.opened(ACCOUNT).committed(
        reservations={ReservationId(1): _reservation()},
        reservation_states={ReservationId(1): _held()},
    )

    assert ledger.consumed() == _money("19904")


def test_a_transferred_reservation_is_not_counted_twice() -> None:
    """同節: `TRANSFERRED` の予約は加算せず、同額の建玉割当だけを数える。"""
    transferred = move_reservation(_held(), ReservationStatus.TRANSFERRED, EventId(2), AT)
    allocation = PositionRiskAllocation(
        allocation_id=AllocationId(1),
        position_id=PositionId(1),
        source_reservation_id=ReservationId(1),
        amount=_money("19904"),
        created_event_id=EventId(2),
    )
    ledger = AccountLedger.opened(ACCOUNT).committed(
        reservations={ReservationId(1): _reservation()},
        reservation_states={ReservationId(1): transferred},
        allocations={AllocationId(1): allocation},
    )

    assert ledger.consumed() == _money("19904")


def test_a_released_allocation_frees_the_budget() -> None:
    """同節: 解放済みの建玉割当は消費済み枠に入らない。"""
    allocation = PositionRiskAllocation(
        allocation_id=AllocationId(1),
        position_id=PositionId(1),
        source_reservation_id=ReservationId(1),
        amount=_money("19904"),
        created_event_id=EventId(2),
        released_event_id=EventId(3),
    )
    ledger = AccountLedger.opened(ACCOUNT).committed(allocations={AllocationId(1): allocation})

    assert ledger.consumed() == _money("0")


def test_a_reservation_cannot_be_released_twice() -> None:
    """同節: `TRANSFERRED` は予約として終端であり、二重解放・再移管を禁止する。"""
    transferred = move_reservation(_held(), ReservationStatus.TRANSFERRED, EventId(2), AT)

    with pytest.raises(KernelValueError, match="terminal"):
        move_reservation(transferred, ReservationStatus.RELEASED, EventId(3), AT)


def test_the_same_event_is_not_committed_twice() -> None:
    """D06 §4.4: 同じ `event_id` を2度確定しようとしたら拒否する（冪等性）。"""
    ledger = AccountLedger.opened(ACCOUNT).committed(event_ids=(EventId(1),))

    assert ledger.has_processed(EventId(1))
    with pytest.raises(KernelValueError, match="already been committed"):
        ledger.committed(event_ids=(EventId(1),))


def test_a_commit_returns_a_new_value_and_leaves_the_old_one_alone() -> None:
    """D06 §4.4: 台帳は不変値で、差し替えるまで外から観測されない。"""
    before = AccountLedger.opened(ACCOUNT)
    after = before.committed(balance=_money("999968"), event_ids=(EventId(1),))

    assert before.balance == ACCOUNT.initial_balance
    assert after.balance == _money("999968")
    assert not before.has_processed(EventId(1))


def test_the_unrealised_pnl_uses_the_last_completed_close() -> None:
    """T01 §2.4: `(150.040 - 150.080) x 32000 = -1280`。"""
    value = unrealized(
        _position(), Price(decimal_from_str("150.040")), COST_MODEL.spread_model, JPY
    )

    assert value == _money("-1280")


def test_a_short_is_valued_on_the_ask() -> None:
    """D06 §8.1: 売り建玉は決済が買いなので spread モデルで導いた ask を使う。"""
    assert exit_price_for(
        OrderSide.SELL, Price(decimal_from_str("150.040")), COST_MODEL.spread_model
    ) == Price(decimal_from_str("150.060"))


def test_the_snapshot_reports_the_open_positions() -> None:
    """D06 §8.1: 台帳 snapshot は残高・資産・枠・開いている建玉を持つ。"""
    ledger = AccountLedger.opened(ACCOUNT).committed(positions={PositionId(1): _position()})

    snapshot = snapshot_of(ledger, AT, _money("998688"))

    assert snapshot.balance == ACCOUNT.initial_balance
    assert snapshot.equity == _money("998688")
    assert snapshot.open_position_ids == (PositionId(1),)


def test_the_identity_conversion_has_no_legs() -> None:
    """D06 §8.5.1 の規則1: 決済通貨と口座通貨が同じなら脚0本・率1・ずれ0。"""
    path = resolve_path(JPY, JPY, MOMENT, CONVERSION_POLICY)

    assert path.is_identity
    assert path.rate == decimal_from_str("1")
    assert path.skew == timedelta(0)
    assert rate_of(path, JPY, JPY).rate == decimal_from_str("1")


def test_a_cross_currency_conversion_is_refused_in_stage_two() -> None:
    """D06 §8.5.1: 経路が組めなければ拒否する（黙って率1に落とさない）。"""
    with pytest.raises(ConversionUnavailable):
        resolve_path(CurrencyCode("USD"), JPY, MOMENT, CONVERSION_POLICY)


def test_a_path_without_two_hops_has_no_skew() -> None:
    """D06 §8.5.1 の規則5: 1ホップ以下ならずれは0。"""
    path = identity_path(MOMENT)

    assert path.skew == timedelta(0)
