"""判断履歴の平坦化と末尾の3集計（D06 §9.1・§9.2・§10.3）。

平坦化の3規則（接頭辞・区分タグ付き union・複合表）と、値の書き方（`ProcessingPoint` は
3列、`Reason` は2列、`Money` は2列、`Decimal` は文字列）を確かめる。末尾の3集計は
T01 §9.3 の数値と一致させる。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.backtest.admission.admission import AttemptAccepted, AttemptRejected
from odyssey_fx.backtest.domain.account import AccountLedger
from odyssey_fx.backtest.domain.fills import CostKind
from odyssey_fx.backtest.domain.orders import (
    CloseCause,
    CloseRequest,
    OrderSide,
    RequestOrigin,
)
from odyssey_fx.backtest.domain.positions import (
    Position,
    PositionRiskAllocation,
    ProtectionState,
)
from odyssey_fx.backtest.domain.reservations import (
    ReservationState,
    ReservationStatus,
    RiskReservation,
)
from odyssey_fx.backtest.engine.phases import BACKTEST_PHASES
from odyssey_fx.backtest.engine.run_end import final_summaries
from odyssey_fx.backtest.portfolio.ledger import LedgerSnapshot
from odyssey_fx.backtest.trace.recorder import (
    CompositeRow,
    canonical_text,
    flatten,
    flatten_row,
    flatten_union,
    plain_decimal,
)
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AttemptId,
    EventId,
    EvidenceId,
    FillId,
    OrderId,
    PositionId,
    ReservationId,
    RunId,
)
from odyssey_fx.common.money import Money, Price, Quantity, decimal_from_str
from odyssey_fx.common.reason import Reason, ReasonCode, RunEndDetail
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import ProcessingPoint, UtcTime
from tests.fixtures.backtest.harness import ACCOUNT, COST_MODEL, JPY
from tests.fixtures.backtest.paths import execution_bars
from tests.fixtures.synthetic.market import USDJPY

MOMENT = UtcTime.from_components(2026, 1, 6, 9, 0)
AT = ProcessingPoint(time=MOMENT, phase=BACKTEST_PHASES.by_name("ADMISSION"), sequence=1)


def _money(text: str) -> Money:
    return Money(decimal_from_str(text), JPY)


def test_a_processing_point_becomes_three_columns() -> None:
    """D06 §9.1: `ProcessingPoint` は `*_time` / `*_phase` / `*_sequence`。"""
    snapshot = LedgerSnapshot(
        at=AT, balance=_money("1000000"), equity=_money("998688"), consumed=_money("19904")
    )

    columns = flatten(snapshot)

    assert columns["at_time"] == "2026-01-06T09:00:00Z"
    assert columns["at_phase"] == "ADMISSION"
    assert columns["at_sequence"] == 1


def test_money_becomes_an_amount_and_a_currency() -> None:
    """D06 §9.1: `Money` は `*_amount`（文字列）と `*_currency`。"""
    snapshot = LedgerSnapshot(
        at=AT, balance=_money("1000000"), equity=_money("998688"), consumed=_money("19904")
    )

    columns = flatten(snapshot)

    assert columns["balance_amount"] == "1000000"
    assert columns["balance_currency"] == "JPY"


def test_a_reason_becomes_a_code_and_a_detail() -> None:
    """D06 §9.1: `Reason` は `*_code` と正規化エンコードの `*_detail`。"""
    rejected = AttemptRejected(
        attempt_id=AttemptId(1),
        reason=Reason(ReasonCode.RUN_END, RunEndDetail(run_end=MOMENT)),
    )

    columns = flatten_union(rejected, (AttemptAccepted, AttemptRejected))

    assert columns["reason_code"] == "RUN_END"
    assert columns["reason_detail"] == canonical_text(RunEndDetail(run_end=MOMENT))


def test_a_union_row_carries_the_union_of_all_variants() -> None:
    """D06 §9.1 の規則2: 行そのものが union なら列は全変種の和集合。"""
    accepted = AttemptAccepted(attempt_id=AttemptId(1), order_id=OrderId(1))

    columns = flatten_union(accepted, (AttemptAccepted, AttemptRejected))

    assert columns["kind"] == "ACCEPTED"
    assert columns["order_id"] == "ORD:00000001"
    assert columns["reason_code"] is None
    assert columns["reason_detail"] is None


def test_a_nested_union_field_gets_the_field_prefix() -> None:
    """D06 §9.1 の規則1・2: `payload` は `payload_kind` ＋ 全変種のフィールド。"""
    from odyssey_fx.backtest.domain.orders import OrderRequest
    from odyssey_fx.common.refs import EvidenceRef

    request = OrderRequest(
        run_id=RunId(ContentDigest.sha256("2" * 64)),
        attempt_id=AttemptId(2),
        account_id=ACCOUNT.account_id,
        strategy_id="strategy_a",
        created_at=AT,
        origin=RequestOrigin.ENGINE,
        payload=CloseRequest(
            position_id=PositionId(1), cause=CloseCause.TAKE_PROFIT, valid_for=timedelta(minutes=20)
        ),
        evidence_ref=EvidenceRef(evidence_id=EvidenceId(1)),
    )

    columns = flatten(request)

    assert columns["payload_kind"] == "CLOSE_REQUEST"
    assert columns["payload_position_id"] == "POS:00000001"
    assert columns["payload_opportunity_id"] is None
    assert columns["origin"] == "ENGINE"


def test_a_composite_row_prefixes_the_secondary_type() -> None:
    """D06 §9.1 の規則3: 従の型は型名のスネークケースを接頭辞にする。"""
    reservation = RiskReservation(
        run_id=RunId(ContentDigest.sha256("3" * 64)),
        reservation_id=ReservationId(1),
        order_id=OrderId(1),
        account_id=ACCOUNT.account_id,
        created_at=AT,
        amount=_money("19904"),
        assessment_id=EvidenceId(2),
    )
    state = ReservationState(
        reservation_id=ReservationId(1),
        status=ReservationStatus.TRANSFERRED,
        last_event_id=EventId(3),
        last_processed_at=AT,
    )

    columns = flatten_row(
        CompositeRow(primary=reservation, parts=(("reservation_state", ReservationState, state),))
    )

    assert columns["reservation_id"] == "RSV:00000001"
    assert columns["reservation_state_status"] == "TRANSFERRED"
    assert columns["amount_amount"] == "19904"


def test_a_missing_secondary_still_produces_its_columns() -> None:
    """D06 §9.1: その行に無い列は `None` にする（表の形が行ごとに変わらない）。"""
    position = Position(
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
            version=1,
            stop_loss=Price(decimal_from_str("149.500")),
            effective_from=execution_bars()[1].key,
        ),
    )

    columns = flatten_row(
        CompositeRow(
            primary=position,
            parts=(("position_risk_allocation", PositionRiskAllocation, None),),
        )
    )

    assert columns["position_id"] == "POS:00000001"
    assert columns["position_risk_allocation_amount_amount"] is None
    assert columns["position_risk_allocation_position_id"] is None
    assert columns["protection_stop_loss"] == "149.5"


def test_the_final_summaries_match_the_paper_trace() -> None:
    """T01 §9.3: 確定 36,706・資産 1,051,706・仮決済 14,670・費用の内訳。"""
    ledger = AccountLedger.opened(ACCOUNT).committed(
        balance=_money("1036706"),
        positions={
            PositionId(2): Position(
                position_id=PositionId(2),
                account_id=ACCOUNT.account_id,
                strategy_id="strategy_a",
                symbol=USDJPY,
                side=OrderSide.BUY,
                quantity=Quantity(decimal_from_str("30000")),
                entry_fill_id=FillId(3),
                entry_price=Price(decimal_from_str("151.000")),
                opened_at=AT,
                protection=ProtectionState(
                    version=2,
                    stop_loss=Price(decimal_from_str("150.400")),
                    take_profit=Price(decimal_from_str("152.200")),
                    effective_from=execution_bars()[1].key,
                ),
            )
        },
    )

    summaries = final_summaries(
        ledger=ledger,
        initial_balance=ACCOUNT.initial_balance,
        equity=_money("1051706"),
        last_close=Price(decimal_from_str("151.500")),
        cost_model=COST_MODEL,
        cost_totals={
            CostKind.COMMISSION: _money("94"),
            CostKind.SLIPPAGE_IN_PRICE: _money("940"),
            CostKind.SPREAD_IN_PRICE: _money("1240"),
        },
    )

    assert summaries is not None
    assert summaries.realized == _money("36706")
    assert summaries.equity_with_mtm == _money("1051706")
    assert summaries.hypothetical_closed == _money("14670")
    assert summaries.cost_breakdown[CostKind.COMMISSION] == _money("94")
    assert summaries.cost_breakdown[CostKind.SLIPPAGE_IN_PRICE] == _money("940")
    assert summaries.cost_breakdown[CostKind.SPREAD_IN_PRICE] == _money("1240")


def test_the_summaries_are_not_built_without_a_final_price() -> None:
    """D06 §10.3: 最終評価価格が無ければ3集計を組み立てない（架空の価格で埋めない）。"""
    ledger = AccountLedger.opened(ACCOUNT).committed(
        positions={
            PositionId(2): Position(
                position_id=PositionId(2),
                account_id=ACCOUNT.account_id,
                strategy_id="strategy_a",
                symbol=USDJPY,
                side=OrderSide.BUY,
                quantity=Quantity(decimal_from_str("30000")),
                entry_fill_id=FillId(3),
                entry_price=Price(decimal_from_str("151.000")),
                opened_at=AT,
                protection=ProtectionState(
                    version=1,
                    stop_loss=Price(decimal_from_str("150.400")),
                    effective_from=execution_bars()[1].key,
                ),
            )
        }
    )

    assert (
        final_summaries(
            ledger=ledger,
            initial_balance=ACCOUNT.initial_balance,
            equity=ACCOUNT.initial_balance,
            last_close=None,
            cost_model=COST_MODEL,
            cost_totals={},
        )
        is None
    )


# --- 数値列の十進表記（D06 §9.1）--------------------------------------------


def test_the_decimal_notation_is_fixed_point_and_round_trips() -> None:
    """D06 §9.1: 人が読める固定小数で書き、`Decimal(文字列)` で厳密に往復する。

    値は T01 の価格・数量・金額・率である。指数表記を使わないこと、末尾ゼロを落とすこと、
    ゼロと負号の扱いを1つの表で確かめる。
    """
    cases = {
        "150.080": "150.08",
        "149.500": "149.5",
        "151.240": "151.24",
        "32000": "32000",
        "19904": "19904",
        "1000000": "1000000",
        "1036736": "1036736",
        "-18880": "-18880",
        "0.622": "0.622",
        "0.02": "0.02",
        "1": "1",
    }
    for source, expected in cases.items():
        value = decimal_from_str(source)
        text = plain_decimal(value)
        assert text == expected
        assert decimal_from_str(text) == value


def test_the_same_value_always_produces_the_same_text() -> None:
    """D06 §9.1 の規則1: 書き手が持っていた桁数で文字列が変わらない。

    これが崩れると、同じ入力の再実行で判断履歴を文字列のまま比べられなくなる
    （D06 §4.4 の「許容誤差は完全一致」）。
    """
    assert plain_decimal(decimal_from_str("149.500")) == plain_decimal(decimal_from_str("149.5"))
    assert plain_decimal(decimal_from_str("150.00")) == plain_decimal(decimal_from_str("150"))
    assert plain_decimal(decimal_from_str("1E+6")) == plain_decimal(decimal_from_str("1000000"))


def test_zero_loses_its_sign_and_its_scale() -> None:
    """D06 §9.1 の規則3: ゼロは常に `0`（`-0` も `0.00` も同じ文字列）。"""
    assert plain_decimal(decimal_from_str("0")) == "0"
    assert plain_decimal(decimal_from_str("-0")) == "0"
    assert plain_decimal(decimal_from_str("0.00")) == "0"


def test_a_small_magnitude_keeps_a_leading_zero() -> None:
    """D06 §9.1 の規則2: 指数表記を使わず、小数点の前に `0` を補う。"""
    assert plain_decimal(decimal_from_str("5E-4")) == "0.0005"
    assert plain_decimal(decimal_from_str("-5E-4")) == "-0.0005"


def test_a_magnitude_too_large_for_fixed_point_is_refused() -> None:
    """D06 §9.1 の規則4: 固定小数で書けない大きさは、黙って別表記へ落とさず失敗させる。"""
    with pytest.raises(KernelValueError, match="fixed-point decimal"):
        plain_decimal(decimal_from_str("1E+5000"))
