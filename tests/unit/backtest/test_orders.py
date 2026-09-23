"""注文の状態機械（D06 §5.1 の遷移表の6行）。

遷移表の**各行に1テスト**を置く。実行の中では起きない遷移（期限切れ・失敗時の取消）も、
受付を経由せずにイベントを直接組み立てて検証する（D06 §5.1 の「段階2の意味論テストは、
受付を経由せずに `AcceptedOrder` を直接組み立てて状態機械だけを検証する単体テスト」）。
"""

from __future__ import annotations

import pytest

from odyssey_fx.backtest.domain.events import (
    ORDER_TRANSITIONS,
    OrderEvent,
    apply_order_event,
)
from odyssey_fx.backtest.domain.orders import OrderStatus
from odyssey_fx.backtest.engine.phases import BACKTEST_PHASES
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EventId, FillId, OrderId
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.time import ProcessingPoint, UtcTime

ORDER = OrderId(1)
MOMENT = UtcTime.from_components(2026, 1, 6, 9, 0)


def _at(phase: str, sequence: int = 0) -> ProcessingPoint:
    return ProcessingPoint(time=MOMENT, phase=BACKTEST_PHASES.by_name(phase), sequence=sequence)


def _event(
    to_status: OrderStatus,
    phase: str,
    *,
    seq: int = 1,
    from_status: OrderStatus | None = OrderStatus.PENDING,
    reason: Reason | None = None,
    fill_id: FillId | None = None,
) -> OrderEvent:
    return OrderEvent(
        event_id=EventId(seq),
        order_id=ORDER,
        from_status=from_status,
        to_status=to_status,
        at=_at(phase),
        reason=reason,
        fill_id=fill_id,
    )


def _accepted() -> tuple[OrderEvent, object]:
    event = _event(OrderStatus.PENDING, "ADMISSION", from_status=None)
    return event, apply_order_event(None, event)


def test_the_transition_table_has_exactly_six_rows() -> None:
    """D06 §5.1: 遷移は6本ですべてである。"""
    assert len(ORDER_TRANSITIONS) == 6
    assert [transition.number for transition in ORDER_TRANSITIONS] == [1, 2, 3, 4, 5, 6]


def test_transition1_acceptance_projects_pending() -> None:
    """遷移1: 受付は `PENDING` を投影し、終端理由を持たない。"""
    _, state = _accepted()

    assert state.status is OrderStatus.PENDING  # type: ignore[attr-defined]
    assert state.terminal_reason is None  # type: ignore[attr-defined]


def test_transition2_fill_carries_the_fill_id() -> None:
    """遷移2: 約定は `fill_id` を必ず持ち、理由は持たない（契機は `CloseCause`）。"""
    _, state = _accepted()

    filled = apply_order_event(
        state,  # type: ignore[arg-type]
        _event(OrderStatus.FILLED, "EXECUTION_OPEN", seq=2, fill_id=FillId(1)),
    )

    assert filled.status is OrderStatus.FILLED
    assert filled.terminal_reason is None


def test_transition3_expiry_records_the_reason() -> None:
    """遷移3: 期限切れは `ORDER_EXPIRY` フェーズでだけ起きる。"""
    _, state = _accepted()

    expired = apply_order_event(
        state,  # type: ignore[arg-type]
        _event(OrderStatus.EXPIRED, "ORDER_EXPIRY", seq=2, reason=Reason(ReasonCode.EXPIRED)),
    )

    assert expired.status is OrderStatus.EXPIRED
    assert expired.terminal_reason is not None
    assert expired.terminal_reason.code is ReasonCode.EXPIRED


def test_transition4_run_end_cancels() -> None:
    """遷移4: 末尾に残った注文は `RUN_END` で取り消される。"""
    _, state = _accepted()

    canceled = apply_order_event(
        state,  # type: ignore[arg-type]
        _event(OrderStatus.CANCELED, "RUN_END", seq=2, reason=Reason(ReasonCode.RUN_END)),
    )

    assert canceled.terminal_reason is not None
    assert canceled.terminal_reason.code is ReasonCode.RUN_END


def test_transition5_data_error_cancels_in_any_phase() -> None:
    """遷移5: 実行失敗による取消は、失敗を検出したどのフェーズでも起きる。"""
    _, state = _accepted()

    canceled = apply_order_event(
        state,  # type: ignore[arg-type]
        _event(OrderStatus.CANCELED, "PUBLICATION", seq=2, reason=Reason(ReasonCode.DATA_ERROR)),
    )

    assert canceled.terminal_reason is not None
    assert canceled.terminal_reason.code is ReasonCode.DATA_ERROR


def test_transition6_cancels_a_close_whose_position_already_closed() -> None:
    """遷移6: 対象建玉が先に閉じた決済注文は `POSITION_CLOSED` で取り消される。"""
    _, state = _accepted()

    canceled = apply_order_event(
        state,  # type: ignore[arg-type]
        _event(
            OrderStatus.CANCELED,
            "EXECUTION_OPEN",
            seq=2,
            reason=Reason(ReasonCode.POSITION_CLOSED),
        ),
    )

    assert canceled.terminal_reason is not None
    assert canceled.terminal_reason.code is ReasonCode.POSITION_CLOSED


def test_a_terminal_order_accepts_no_further_transition() -> None:
    """D06 §5.1: 終端状態からの遷移は表に無く、イベントとしても組み立てられない。"""
    _, state = _accepted()
    filled = apply_order_event(
        state,  # type: ignore[arg-type]
        _event(OrderStatus.FILLED, "EXECUTION_OPEN", seq=2, fill_id=FillId(1)),
    )
    assert filled.is_terminal

    with pytest.raises(KernelValueError, match="no order transition FILLED"):
        _event(
            OrderStatus.CANCELED,
            "RUN_END",
            seq=3,
            from_status=OrderStatus.FILLED,
            reason=Reason(ReasonCode.RUN_END),
        )


def test_a_second_event_on_a_terminal_order_is_refused() -> None:
    """D06 §5.1: 終端済みの注文への遷移要求は `KernelValueError` で拒否する。"""
    _, state = _accepted()
    filled = apply_order_event(
        state,  # type: ignore[arg-type]
        _event(OrderStatus.FILLED, "EXECUTION_OPEN", seq=2, fill_id=FillId(1)),
    )

    with pytest.raises(KernelValueError, match="already FILLED"):
        apply_order_event(
            filled, _event(OrderStatus.FILLED, "EXECUTION_OPEN", seq=3, fill_id=FillId(2))
        )


def test_a_transition_outside_the_table_is_refused() -> None:
    """D06 §5.1: 表に無い組み合わせは構築時に拒否する。"""
    with pytest.raises(KernelValueError, match="no order transition"):
        _event(OrderStatus.EXPIRED, "ORDER_EXPIRY", reason=Reason(ReasonCode.RUN_END))


def test_a_transition_in_the_wrong_phase_is_refused() -> None:
    """D06 §5.1: 期限切れは `ORDER_EXPIRY` 以外のフェーズでは起こせない。"""
    with pytest.raises(KernelValueError, match="may not happen in phase"):
        _event(OrderStatus.EXPIRED, "EXECUTION_OPEN", reason=Reason(ReasonCode.EXPIRED))


def test_a_fill_id_is_present_exactly_when_the_order_is_filled() -> None:
    """D06 §3: `fill_id` は約定のときだけ入る。"""
    with pytest.raises(KernelValueError, match="fill_id is present exactly"):
        _event(OrderStatus.FILLED, "EXECUTION_OPEN")
