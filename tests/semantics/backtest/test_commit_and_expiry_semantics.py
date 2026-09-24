"""確定単位・再配送・期限と始値の同時刻の意味論（D06 §4.4・§5.1・§5.3、D06 §11 の #2・#3・#6）。

上位設計書 §4.7.15 E の8項目のうち、テスト戦略（D08 §7.1）が未整備としていた3行を置く。

- #2 受付と約定の原子性: 台帳の差し替えを1回ずつ観測し、どの時点の台帳にも確定単位の
  半分だけが入った状態が無いことを確かめる（実際の run の経路で通す）。
- #3 同じ `event_id` の再配送: 1 run の中で注文イベントがちょうど1回ずつ確定することと、
  確定済みのイベントをもう一度適用しようとしても約定・残高・予約が二重に動かないこと。
- #6 期限と始値の同時刻: 段階2 の実行では起きない（D06 §5.1 の遷移3）。受付を経由せずに
  注文を台帳へ置いて**表現できること**を1件で固定し、起きない理由を受付側の1件で固定する
  （D08 §6.2 末尾の規約）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

import pytest

from odyssey_fx.backtest.admission.admission import AttemptRejected
from odyssey_fx.backtest.domain.account import AccountLedger
from odyssey_fx.backtest.domain.events import OrderEvent
from odyssey_fx.backtest.domain.fills import FillRecord
from odyssey_fx.backtest.domain.orders import (
    AcceptedEntryTerms,
    AcceptedOrder,
    ExecutionCommitment,
    ImmediateAfterFill,
    OrderStatus,
    ProtectionHit,
    ScheduledOpen,
)
from odyssey_fx.backtest.domain.reservations import ReservationStatus, RiskReservation
from odyssey_fx.backtest.engine.loop import EngineContext
from odyssey_fx.backtest.portfolio.ledger import (
    LedgerSnapshot,
    commit_entry_acceptance,
    commit_immediate_close,
)
from odyssey_fx.backtest.trace.recorder import CompositeRow, TraceTable
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EventId, OrderId, ReservationId
from odyssey_fx.common.reason import ReasonCode
from odyssey_fx.common.time import Interval, ProcessingPoint, UtcTime
from tests.fixtures.backtest.harness import (
    ACCOUNT,
    EXECUTION_POLICY,
    RunOutput,
    run_backtest,
)
from tests.fixtures.backtest.paths import (
    DECISION_TIME,
    RUN_INTERVAL,
    STOP_LOSS_BAR,
    TAKE_PROFIT_BAR,
    execution_bars,
    signal_bars,
)
from tests.fixtures.strategy.strategy_a import strategy_a

# --- 観測の道具 -------------------------------------------------------------


class RecordingContext(EngineContext):
    """台帳の参照が差し替えられるたびに、その値を1件ずつ残す実行コンテキスト。

    エンジンは確定単位の最後に `context.ledger` を1文で差し替える（D06 §4.4）。差し替えの
    たびに値を残せば、**外から観測できた台帳のすべて**が手に入る。確定単位の途中の状態が
    どれか1つにでも現れれば、原子性が破れている。
    """

    def __init__(self) -> None:
        self.observed: list[AccountLedger] = []
        super().__init__(ACCOUNT)

    @property
    def ledger(self) -> AccountLedger:
        return self.observed[-1]

    @ledger.setter
    def ledger(self, value: AccountLedger) -> None:
        self.observed.append(value)


def _order_events(output: RunOutput) -> tuple[OrderEvent, ...]:
    return tuple(row for row in output.rows(TraceTable.ORDER_EVENTS) if isinstance(row, OrderEvent))


def _fills(output: RunOutput) -> tuple[FillRecord, ...]:
    return tuple(row for row in output.rows(TraceTable.FILLS) if isinstance(row, FillRecord))


def _orders(output: RunOutput) -> tuple[AcceptedOrder, ...]:
    return tuple(row for row in output.rows(TraceTable.ORDERS) if isinstance(row, AcceptedOrder))


def _reservations(output: RunOutput) -> tuple[RiskReservation, ...]:
    return tuple(
        row.primary
        for row in output.rows(TraceTable.RESERVATIONS)
        if isinstance(row, CompositeRow) and isinstance(row.primary, RiskReservation)
    )


def _run_path(
    conflict: tuple[str, str, str, str], context: EngineContext | None = None
) -> RunOutput:
    """T01 の経路1（利確）・経路2（損切り）を通す。"""
    return run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(conflict=conflict),
        run_interval=RUN_INTERVAL,
        context=context,
    )


def _half_applied_units(ledger: AccountLedger) -> list[str]:
    """確定単位の半分だけが入った箇所を列挙する（空なら整合）。

    D06 §4.4 の表の各行を「片方があるなら、もう片方も同じ台帳にある」の形で読む。
    """
    problems: list[str] = []
    for order_id, order in ledger.orders.items():
        state = ledger.order_states.get(order_id)
        if state is None:
            problems.append(f"{order_id}: the order is in the ledger without its state")
            continue
        eligibility = order.execution.eligibility
        if isinstance(eligibility, (ProtectionHit, ImmediateAfterFill)) and (
            state.status is not OrderStatus.FILLED
        ):
            # エンジン生成の即時決済は受付と約定が1つの確定単位（D06 §4.4 の5行目）。
            problems.append(f"{order_id}: an engine close is visible as {state.status.value}")
        terms = order.terms
        if isinstance(terms, AcceptedEntryTerms):
            reservation = ledger.reservations.get(terms.reservation_id)
            reservation_state = ledger.reservation_states.get(terms.reservation_id)
            if reservation is None or reservation_state is None:
                # エントリーの受付は注文・状態・予約・予約状態を1つの確定単位にする（1行目）。
                problems.append(f"{order_id}: the entry is accepted without its reservation")
                continue
            filled = state.status is OrderStatus.FILLED
            transferred = reservation_state.status is ReservationStatus.TRANSFERRED
            allocated = any(
                allocation.source_reservation_id == terms.reservation_id
                for allocation in ledger.allocations.values()
            )
            if not filled == transferred == allocated:
                # エントリー約定は予約の移管と同額の建玉割当を同じ単位で確定する（3行目）。
                problems.append(
                    f"{order_id}: filled={filled} transferred={transferred} allocated={allocated}"
                )
    for position in ledger.positions.values():
        allocation = next(
            (
                value
                for value in ledger.allocations.values()
                if value.position_id == position.position_id
            ),
            None,
        )
        if allocation is None:
            problems.append(f"{position.position_id}: the position exists without its allocation")
        elif position.is_open == allocation.is_released:
            # 決済約定は建玉の終了と割当の解放を同じ単位で確定する（4行目・5行目）。
            problems.append(
                f"{position.position_id}: open={position.is_open} released={allocation.is_released}"
            )
    return problems


# --- #2 受付と約定の原子性 ---------------------------------------------------


@pytest.mark.parametrize(
    "conflict",
    [pytest.param(STOP_LOSS_BAR, id="stop-loss"), pytest.param(TAKE_PROFIT_BAR, id="take-profit")],
)
def test_no_ledger_ever_shows_half_of_a_commit_unit(conflict: tuple[str, str, str, str]) -> None:
    """D06 §4.4: 受付と約定は確定単位ごとに不可分で、途中の台帳は外から観測されない。

    台帳の差し替えをすべて観測し、どの時点の台帳にも「注文はあるが状態が無い」
    「受付済みのエントリーに予約が無い」「約定したのに予約が移管されていない」
    「建玉が閉じたのに割当が解放されていない」「エンジン生成の決済が `PENDING` のまま見える」
    のどれも無いことを確かめる。経路2 は損切りの到達（エンジン生成の即時決済）、
    経路1 は利確の到達を通る。どちらもエントリーの受付（rank 10）と約定（rank 11）を含む。
    """
    context = RecordingContext()
    output = _run_path(conflict, context)

    assert output.result.status is RunStatus.COMPLETED
    assert len(_fills(output)) == 2, "one entry fill and one protective close"
    assert len(context.observed) > 3
    for index, ledger in enumerate(context.observed):
        assert _half_applied_units(ledger) == [], index


def test_an_engine_close_is_accepted_and_filled_in_one_replacement() -> None:
    """D06 §4.4（エンジン生成の即時決済）: 受付と約定を1つの差し替えにまとめる。

    損切りの到達で生まれる決済注文は、受付イベントと約定イベントが同じ判断時点・同じ
    フェーズ（`EXECUTION_BAR_COMPLETE`）に並び、**その注文が初めて現れた台帳で既に
    `FILLED`** になっている。受付の単位と約定の単位を順に適用していれば、`PENDING` の
    注文を持つ台帳が1つ観測されるはずである。
    """
    context = RecordingContext()
    output = _run_path(STOP_LOSS_BAR, context)

    close = next(
        order for order in _orders(output) if isinstance(order.execution.eligibility, ProtectionHit)
    )
    events = [event for event in _order_events(output) if event.order_id == close.order_id]
    assert [(event.from_status, event.to_status) for event in events] == [
        (None, OrderStatus.PENDING),
        (OrderStatus.PENDING, OrderStatus.FILLED),
    ]
    assert {(event.at.time, event.at.phase.name) for event in events} == {
        (events[0].at.time, "EXECUTION_BAR_COMPLETE")
    }

    first_seen = next(ledger for ledger in context.observed if close.order_id in ledger.orders)
    assert first_seen.order_states[close.order_id].status is OrderStatus.FILLED
    assert all(first_seen.has_processed(event.event_id) for event in events)


def test_a_failing_fill_leaves_the_engine_close_unaccepted() -> None:
    """D06 §4.4: 確定単位の中で検査に失敗したら差し替えを行わず、受付だけが残ることもない。

    経路2 の損切り決済を、約定イベントだけを壊して（別の注文のイベントにする。イベント単体
    としては正しいが、受付で投影した状態には適用できない）決済直前の台帳へもう一度確定させる。
    拒否されること、そして先に適用できたはずの受付イベントも台帳に入らないことを確かめる。
    """
    context = RecordingContext()
    output = _run_path(STOP_LOSS_BAR, context)
    close = next(
        order for order in _orders(output) if isinstance(order.execution.eligibility, ProtectionHit)
    )
    acceptance, fill = (
        event for event in _order_events(output) if event.order_id == close.order_id
    )
    before = next(
        ledger
        for ledger, after in zip(context.observed, context.observed[1:], strict=False)
        if close.order_id not in ledger.orders and close.order_id in after.orders
    )
    after = context.observed[context.observed.index(before) + 1]
    position = after.positions[close.terms.position_id]  # type: ignore[union-attr]
    allocation = next(
        value for value in after.allocations.values() if value.position_id == position.position_id
    )
    broken = replace(fill, order_id=OrderId(_SEEDED))

    with pytest.raises(KernelValueError):
        commit_immediate_close(
            before,
            order=close,
            acceptance_event=acceptance,
            fill_event=broken,
            position=position,
            allocation=allocation,
            balance=after.balance,
        )
    assert close.order_id not in before.orders
    assert not before.has_processed(acceptance.event_id)


# --- #3 同じ event_id の再配送 -----------------------------------------------


def test_every_order_event_of_a_run_is_committed_exactly_once() -> None:
    """上位 §4.7.13 A・D06 §4.4: 注文イベントは1 run の中でちょうど1回ずつ確定する。

    エンジンは注文イベントの識別子を自分で採番するので、1 run の中で同じ `event_id` が
    2度届く経路は無い。判断履歴の注文イベントに重複が無く、その全部が台帳の処理済み記録に
    入っていて、処理済み記録がそれ以外を含まない（約定の `event_id` も注文イベントと同じ値）
    ことで固定する。
    """
    context = RecordingContext()
    output = _run_path(STOP_LOSS_BAR, context)

    event_ids = [event.event_id for event in _order_events(output)]
    assert len(event_ids) == len(set(event_ids)) == 4
    assert context.ledger.processed_event_ids == frozenset(event_ids)
    assert {fill.event_id for fill in _fills(output)} <= set(event_ids)
    # 処理済み記録は差し替えのたびに増えるだけで、同じ識別子を2度加えない。
    for previous, current in zip(context.observed, context.observed[1:], strict=False):
        assert previous.processed_event_ids <= current.processed_event_ids


def test_a_redelivered_order_event_is_never_applied_twice() -> None:
    """上位 §4.7.13 A・D06 §4.4: 再配送で約定・balance 更新・予約解放を二重に実行しない。

    経路2 を走らせ終えた台帳へ、run の中で確定した損切り決済の受付・約定イベントを
    **同じ `event_id` のまま**もう一度確定させようとする。台帳はそれを拒否し（終端した注文への
    遷移も、処理済みの識別子の再追加も受け付けない）、手元の台帳の残高・建玉・割当は
    変わらない。呼び出し側は `has_processed` で再配送を見分けられる。
    """
    context = RecordingContext()
    output = _run_path(STOP_LOSS_BAR, context)
    final = context.ledger
    close = next(
        order for order in _orders(output) if isinstance(order.execution.eligibility, ProtectionHit)
    )
    acceptance, fill = (
        event for event in _order_events(output) if event.order_id == close.order_id
    )
    position = final.positions[close.terms.position_id]  # type: ignore[union-attr]
    allocation = next(
        value for value in final.allocations.values() if value.position_id == position.position_id
    )

    assert final.has_processed(acceptance.event_id)
    assert final.has_processed(fill.event_id)
    with pytest.raises(KernelValueError):
        commit_immediate_close(
            final,
            order=close,
            acceptance_event=acceptance,
            fill_event=fill,
            position=position,
            allocation=allocation,
            balance=final.balance,
        )
    # 状態の検査を通る組み合わせでも、処理済みの識別子だけで拒否される。
    with pytest.raises(KernelValueError, match="already been committed"):
        final.committed(event_ids=(fill.event_id,))
    # 拒否は差し替えより前に起きるので、エンジンが観測した台帳は1つも増えていない。
    assert context.observed[-1] is final


# --- #6 期限と始値の同時刻 ---------------------------------------------------

#: 期限と候補の始値が重なる時刻（`[09:15,09:30)` の始値）。
_COLLISION = DECISION_TIME + timedelta(minutes=15)

#: 置く注文の識別子。run の中の採番（1 から）と重ならない値にする。
_SEEDED = 901


@dataclass(frozen=True, slots=True)
class _Seeded:
    order: AcceptedOrder
    ledger: AccountLedger


def _seeded_order_expiring_at_its_open() -> _Seeded:
    """期限 `expires_at` が候補の始値の時刻と同じ `PENDING` のエントリー注文を持つ台帳。

    受付では作れない注文なので（下の
    `test_a_candidate_open_at_the_expiry_is_refused_at_admission`）、経路1 で実際に
    受け付けた注文と予約を元に、識別子・期限・候補だけを差し替えて組み立てる。
    """
    template = _run_path(TAKE_PROFIT_BAR)
    order = _orders(template)[0]
    reservation = _reservations(template)[0]
    terms = order.terms
    assert isinstance(terms, AcceptedEntryTerms)
    candidate = next(bar for bar in execution_bars() if bar.bar_start == _COLLISION)
    seeded_order = replace(
        order,
        order_id=OrderId(_SEEDED),
        expires_at=_COLLISION,
        execution=ExecutionCommitment(
            policy_ref=order.execution.policy_ref,
            execution_series=order.execution.execution_series,
            eligibility=ScheduledOpen(bar_key=candidate.key, open_time=_COLLISION),
        ),
        terms=replace(terms, reservation_id=ReservationId(_SEEDED)),
    )
    seeded_reservation = replace(
        reservation, reservation_id=ReservationId(_SEEDED), order_id=OrderId(_SEEDED)
    )
    acceptance = OrderEvent(
        event_id=EventId(_SEEDED),
        order_id=OrderId(_SEEDED),
        from_status=None,
        to_status=OrderStatus.PENDING,
        at=order.accepted_at,
    )
    ledger = commit_entry_acceptance(
        AccountLedger.opened(ACCOUNT),
        order=seeded_order,
        event=acceptance,
        reservation=seeded_reservation,
    )
    return _Seeded(order=seeded_order, ledger=ledger)


def test_an_expiry_at_the_candidate_open_is_expressible_and_expires_first() -> None:
    """D06 §5.1 の遷移3・§5.3: 期限と候補の始値が同時刻なら、期限切れを先に確定する。

    約定できる条件は `open_time < expires_at` であり、同時刻では期限切れ（rank 2
    `ORDER_EXPIRY`）が始値処理（rank 11 `EXECUTION_OPEN`）より先に確定する（上位 §4.7.13 B）。

    **段階2 の実行ではこの並びは起きない**。受付は「期限内に候補の始値があること」を
    `open_time < expires_at` で検査してから候補を固定するので（D06 §5.3）、期限と始値が
    重なる注文は受付前拒否（`NO_CANDIDATE`）になり、注文そのものが作られない
    （次のテスト）。起きるようになるのは価格条件を待つ注文を入れる段階6 である（D06 §12）。
    そこで D08 §6.2 の規約に従い、受付を経由せずに注文を台帳へ置いて run を走らせ、
    **表現できること**を固定する。

    置く注文: 候補 `[09:15,09:30)` の始値、期限 09:15。run は 09:15 から始め、戦略は助走が
    足りない宣言にして新しい注文を作らせない（採番が置いた注文と交わらないように）。
    """
    seeded = _seeded_order_expiring_at_its_open()
    context = EngineContext(ACCOUNT)
    context.ledger = seeded.ledger
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=Interval(start=_COLLISION, end=UtcTime.from_components(2026, 1, 6, 10, 0)),
        definition=strategy_a(stop_lookback=50),
        context=context,
    )

    assert output.result.status is RunStatus.COMPLETED
    events = [event for event in _order_events(output) if event.order_id == OrderId(_SEEDED)]
    assert [(event.from_status, event.to_status) for event in events] == [
        (OrderStatus.PENDING, OrderStatus.EXPIRED)
    ]
    expiry = events[0]
    assert expiry.at.time == _COLLISION
    assert expiry.at.phase.name == "ORDER_EXPIRY"
    assert expiry.reason is not None and expiry.reason.code is ReasonCode.EXPIRED
    assert _fills(output) == ()
    # 同じ判断時点の始値処理は行われた（始値が無かったから約定しなかったのではない）。
    open_points: list[ProcessingPoint] = [
        row.at
        for row in output.rows(TraceTable.LEDGER_SNAPSHOTS)
        if isinstance(row, LedgerSnapshot) and row.at.phase.name == "EXECUTION_OPEN"
    ]
    assert any(point.time == _COLLISION for point in open_points)
    assert expiry.at.phase.rank < open_points[0].phase.rank
    # 期限切れは同じ確定単位で予約を解放する（D06 §4.4 の終端の行）。
    state = output.context.ledger.reservation_states[ReservationId(_SEEDED)]
    assert state.status is ReservationStatus.RELEASED
    assert state.last_event_id == expiry.event_id


def test_a_candidate_open_at_the_expiry_is_refused_at_admission() -> None:
    """D06 §5.3: 候補の始値が期限と同時刻なら「期限内の候補なし」で受付前に拒否する。

    上のテストの並びが段階2 の実行で起きない理由を、実際の run で固定する。1本見送る設定
    （`entry_delay_bars=1`）で候補を `[09:15,09:30)` の始値にし、有効時間を15分にして期限を
    ちょうど 09:15 にする。`open_time < expires_at` が成り立たないので `NO_CANDIDATE` になり、
    注文も予約も作られない。有効時間を短くして候補を期限の外へ出す既存の経路6
    （`tests/integration/backtest/test_t01_paths.py::test_path6_rejects_when_the_candidate_falls_outside_the_deadline`）
    と違い、ここでは**境界の等号**を確かめる。
    """
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
        execution_policy=replace(
            EXECUTION_POLICY, entry_delay_bars=1, entry_valid_for=timedelta(minutes=15)
        ),
    )

    rejected = [
        row.primary
        for row in output.rows(TraceTable.ATTEMPT_DECISIONS)
        if isinstance(row, CompositeRow) and isinstance(row.primary, AttemptRejected)
    ]
    assert [row.reason.code for row in rejected] == [ReasonCode.NO_CANDIDATE]
    assert output.rows(TraceTable.ORDERS) == ()
    assert output.rows(TraceTable.RESERVATIONS) == ()
