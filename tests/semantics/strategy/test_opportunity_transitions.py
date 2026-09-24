"""取引機会の遷移表の各行を1つずつ確かめる（D05 §7.2）。

遷移表の行と本ファイルのテストは1対1で対応する。表に行を足したら、ここにもテストを足す。

| # | 遷移 | テスト |
|---|---|---|
| 1 | 生成 → `OPEN` | `test_transition_1_...` |
| 2 | 生成 → 終端（同時保持上限） | `test_transition_2_...` |
| 3 | `OPEN` → 終端（新しい発火を優先） | `test_transition_3_...` |
| 4 | `OPEN` → 発注試行中 | `test_transition_4_...` |
| 5 | 発注試行中 → 終端（自身の受付） | `test_transition_5_...` |
| 6 | 発注試行中 → 終端（自身の拒否） | `test_transition_6_...` |
| 7 | 非終端 → 終端（他の受付） | `test_transition_7_...` |
| 8 | `OPEN` → 終端（条件の崩れ） | `test_transition_8_...` |
| 9 | 非終端 → 終端（run 末尾） | `test_transition_9_...` |
| 10 | `OPEN` → 確認成立（段階3） | `test_transition_10_...` |
| 11 | 確認成立 → 終端（期限。段階3） | `test_transition_11_...` |

遷移1〜9 は段階2のランタイムが実際に起こす。遷移10・11 は段階3で起こるもので、本ファイルでは
**状態機械の側が受け付けること**だけを確かめる。ランタイムが実際に起こすことは、紙上トレース
T02 の経路で `test_confirmation_t02_routes.py` の `test_transition_10_…` / `test_transition_11_…`
が確かめる（遷移12 も同じファイルの経路9）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AttemptId,
    EventId,
    IdAllocator,
    OpportunityId,
    RunId,
)
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.opportunity import OnNewTrigger, OnOrderAccepted
from odyssey_fx.strategy.records.payloads import Opportunity, TradeDirection
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from odyssey_fx.strategy.runtime.opportunities import (
    OpportunityLifecycle,
    OpportunityState,
    OpportunityTerminal,
)
from odyssey_fx.strategy.runtime.ports import (
    AdmissionNotice,
    BarClosure,
    PublicationBatch,
)
from tests.fixtures.strategy.fakes import (
    CollectingSink,
    FakeContextView,
    FakeMarketDataView,
    hourly_bars,
)
from tests.fixtures.strategy.phases import BACKTEST_PHASES
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES, TIMEFRAMES, strategy_a

FIRST_START = UtcTime.from_components(2026, 1, 5, 12, 0)


def _window_specs(count: int = 20) -> list[tuple[str, str, str, str]]:
    specs: list[tuple[str, str, str, str]] = []
    for index in range(count):
        high = "150.000" if index == 3 else "149.900"
        low = "149.500" if index == 7 else "149.700"
        specs.append(("149.800", high, low, "149.850"))
    return specs


#: 発火 → 再武装 → 再発火 を作る3本の足（高値, 終値）。
#:
#: 1本目は窓の高値 150.000 を上抜けて発火する。2本目は窓の高値（150.040 へ上がっている）を
#: 下回るので条件が不成立へ戻り、`EDGE` が再武装する。3本目は再び上抜けて発火する。
FIRE_REARM_FIRE: list[tuple[str, str]] = [
    ("150.040", "150.040"),
    ("149.900", "149.800"),
    ("150.100", "150.100"),
]


def _bars(extras: list[tuple[str, str]]) -> tuple[Bar, ...]:
    """窓の20本に、判断対象の足（高値, 終値）を足した列を作る。"""
    specs = _window_specs()
    for high, close in extras:
        specs.append(("149.900", high, "149.800", close))
    return hourly_bars(SIGNAL_SERIES, FIRST_START, specs)


def _evaluator(
    bars: tuple[Bar, ...],
    *,
    max_active: int = 1,
    on_new_trigger: OnNewTrigger = OnNewTrigger.KEEP_EXISTING,
    on_order_accepted: OnOrderAccepted = OnOrderAccepted.KEEP_OTHERS,
    stop_lookback: int | None = None,
) -> StrategyEvaluator:
    """評価器を組み立てる。

    `stop_lookback` に履歴より長い本数を渡すと、損切り水準だけ評価が見送られ、役割出力が
    そろわないため取引機会が `OPEN` のまま残る（T01 §6.1 と同じ作り方）。
    """
    definition = strategy_a(
        max_active=max_active,
        on_new_trigger=on_new_trigger,
        on_order_accepted=on_order_accepted,
        stop_lookback=stop_lookback,
    )
    result = compile_strategy(definition, INITIAL_CATALOG, TIMEFRAMES)
    assert isinstance(result, CompileSucceeded), result
    return StrategyEvaluator(
        compiled=result.compiled,
        registry=INITIAL_CATALOG,
        market_data=FakeMarketDataView({SIGNAL_SERIES: bars}),
        context=FakeContextView(),
        sink=CollectingSink(),
        allocator=IdAllocator(RunId(ContentDigest.sha256("b" * 64))),
    )


def _close_batch(bars: tuple[Bar, ...], index: int, batch_id: int) -> PublicationBatch:
    """`bars[index]` の確定を伝える公開バッチ。"""
    bar = bars[index]
    return PublicationBatch(
        batch_id=EventId(batch_id),
        decision_time=bar.bar_end,
        phases=BACKTEST_PHASES,
        available_bars=(bar.key,),
        scheduled_closes=(BarClosure(bar_key=bar.key, interval=bar.interval),),
    )


def _lifecycle(seq: int, state: OpportunityState) -> OpportunityLifecycle:
    """状態機械だけを確かめるための、最小限の取引機会。"""
    moment = UtcTime.from_components(2026, 1, 6, 9, 0)
    at = ProcessingPoint(time=moment, phase=BACKTEST_PHASES.by_name("P3_TRIGGER"), sequence=0)
    opportunity = Opportunity(
        opportunity_id=OpportunityId(seq),
        symbol=SIGNAL_SERIES.symbol,
        direction=TradeDirection.LONG,
        signal_interval=Interval(start=moment - timedelta(hours=1), end=moment),
        reference_values={},
    )
    return OpportunityLifecycle(
        opportunity=opportunity,
        state=state,
        created_at=at,
        created_decision_time=moment,
    )


# --- 遷移1・2・3: 生成と同時保持 -------------------------------------------


def test_transition_1_a_firing_within_the_limit_opens_an_opportunity() -> None:
    """遷移1: 有効な機会数が上限未満なら、発火は有効な機会になる。"""
    bars = _bars(FIRE_REARM_FIRE[:1])
    evaluator = _evaluator(bars)

    result = evaluator.step(_close_batch(bars, -1, 1))

    opened = [item for item in result.transitions if item.to_state is OpportunityState.OPEN]
    assert len(opened) == 1
    assert opened[0].from_state is None
    assert opened[0].reason is None
    assert opened[0].phase.name == "P3_TRIGGER"


def test_transition_2_a_firing_over_the_limit_is_recorded_but_not_opened() -> None:
    """遷移2: 上限に達していて既存を残す設定なら、発火は記録して終端する（T01 §6.1）。

    発火を捨てない（ADR-0032）ので、2本目の機会が終端理由つきで判断履歴に残る。
    """
    # 1本目の足で発火し、2本目で不成立へ戻して再武装し、3本目で再び発火させる。
    bars = _bars(FIRE_REARM_FIRE)
    evaluator = _evaluator(bars, max_active=1, on_new_trigger=OnNewTrigger.KEEP_EXISTING)
    evaluator.step(_close_batch(bars, -3, 1))
    evaluator.step(_close_batch(bars, -2, 2))

    result = evaluator.step(_close_batch(bars, -1, 3))

    terminated = [
        item
        for item in result.transitions
        if item.reason is not None and item.reason.code is ReasonCode.CONCURRENCY_LIMIT_REACHED
    ]
    assert len(terminated) == 1
    assert terminated[0].from_state is None
    assert terminated[0].opportunity_id == OpportunityId(2)
    assert result.proposals == ()


def test_transition_3_a_new_firing_can_supersede_the_oldest_opportunity() -> None:
    """遷移3: 新しい発火を優先する設定なら、最も古い機会を置き換える。"""
    bars = _bars(FIRE_REARM_FIRE)
    evaluator = _evaluator(
        bars,
        max_active=1,
        on_new_trigger=OnNewTrigger.SUPERSEDE_EXISTING,
        stop_lookback=50,
    )
    evaluator.step(_close_batch(bars, -3, 1))
    evaluator.step(_close_batch(bars, -2, 2))

    result = evaluator.step(_close_batch(bars, -1, 3))

    superseded = [
        item
        for item in result.transitions
        if item.reason is not None and item.reason.code is ReasonCode.SUPERSEDED
    ]
    assert len(superseded) == 1
    assert superseded[0].opportunity_id == OpportunityId(1)
    assert superseded[0].counterpart == OpportunityId(2)


def test_an_order_pending_opportunity_is_not_superseded() -> None:
    """D05 §7.4 の 3a: 発注試行中の機会は置換の対象にしない。

    注文要求は既にエンジンへ渡っており、受付の可否が返る前に機会だけを終端させると、
    受け付けられた注文に対応する機会が無い状態になる。置換できる機会が無いので、新しい
    発火は上限到達として終端する。
    """
    bars = _bars(FIRE_REARM_FIRE)
    evaluator = _evaluator(bars, max_active=1, on_new_trigger=OnNewTrigger.SUPERSEDE_EXISTING)
    first = evaluator.step(_close_batch(bars, -3, 1))
    assert len(first.proposals) == 1  # 1本目は発注試行へ進む
    evaluator.step(_close_batch(bars, -2, 2))

    result = evaluator.step(_close_batch(bars, -1, 3))

    reasons = [item.reason.code for item in result.transitions if item.reason is not None]
    assert reasons == [ReasonCode.CONCURRENCY_LIMIT_REACHED]


# --- 遷移4: 発注試行へ -------------------------------------------------------


def test_transition_4_matching_role_outputs_move_the_opportunity_to_order_pending() -> None:
    """遷移4: 注文意図と保護水準が同じ機会でそろうと発注試行中になる。"""
    bars = _bars(FIRE_REARM_FIRE[:1])
    evaluator = _evaluator(bars)

    result = evaluator.step(_close_batch(bars, -1, 1))

    pending = [
        item for item in result.transitions if item.to_state is OpportunityState.ORDER_PENDING
    ]
    assert len(pending) == 1
    assert pending[0].from_state is OpportunityState.OPEN
    assert pending[0].reason is None
    assert pending[0].phase.name == "P5_ORDER_INTENT"


# --- 遷移5・6・7: 受付結果 ---------------------------------------------------


def _admission(accepted: bool, opportunity_seq: int = 1) -> PublicationBatch:
    return PublicationBatch(
        batch_id=EventId(99),
        decision_time=UtcTime.from_components(2026, 1, 6, 9, 0),
        phases=BACKTEST_PHASES,
        admissions=(
            AdmissionNotice(
                opportunity_id=OpportunityId(opportunity_seq),
                attempt_id=AttemptId(1),
                accepted=accepted,
            ),
        ),
    )


def test_transition_5_an_accepted_order_fulfils_its_own_opportunity() -> None:
    """遷移5: 自身の注文が受け付けられたら役目を終えて終端する。"""
    bars = _bars(FIRE_REARM_FIRE[:1])
    evaluator = _evaluator(bars)
    evaluator.step(_close_batch(bars, -1, 1))

    result = evaluator.step(_admission(accepted=True))

    assert result.transitions[0].reason is not None
    assert result.transitions[0].reason.code is ReasonCode.FULFILLED_BY_ORDER_ACCEPTANCE
    assert result.transitions[0].from_state is OpportunityState.ORDER_PENDING


def test_transition_6_a_rejected_order_terminates_its_own_opportunity() -> None:
    """遷移6: 受付前の審査で拒否されたら終端する（段階2は再審査なし）。"""
    bars = _bars(FIRE_REARM_FIRE[:1])
    evaluator = _evaluator(bars)
    evaluator.step(_close_batch(bars, -1, 1))

    result = evaluator.step(_admission(accepted=False))

    assert result.transitions[0].reason is not None
    assert result.transitions[0].reason.code is ReasonCode.ORDER_ATTEMPT_REJECTED


def test_transition_7_other_opportunities_close_when_the_setting_says_so() -> None:
    """遷移7: 他の機会の注文が受け付けられ、他を閉じる設定なら終端する。"""
    bars = _bars(FIRE_REARM_FIRE)
    evaluator = _evaluator(
        bars,
        max_active=2,
        on_new_trigger=OnNewTrigger.KEEP_EXISTING,
        on_order_accepted=OnOrderAccepted.CLOSE_OTHERS,
    )
    evaluator.step(_close_batch(bars, -3, 1))
    evaluator.step(_close_batch(bars, -2, 2))
    evaluator.step(_close_batch(bars, -1, 3))

    result = evaluator.step(_admission(accepted=True, opportunity_seq=1))

    closed = [
        item
        for item in result.transitions
        if item.reason is not None and item.reason.code is ReasonCode.CLOSED_BY_ORDER_ACCEPTANCE
    ]
    assert len(closed) == 1
    assert closed[0].opportunity_id == OpportunityId(2)
    assert closed[0].counterpart == OpportunityId(1)


# --- 遷移8: 条件の崩れ -------------------------------------------------------


def test_transition_8_is_expressible_even_though_stage_2_never_fires_it() -> None:
    """遷移8: 継続成立の条件が崩れた機会は市場状態の無効化で終端する。

    段階2の検証戦略 A は束縛が空なので実行時には起きない（D05 §7.2）。ここでは状態機械が
    この終端を受け付けることを確かめる。経路そのものはランタイムに実装済みで、段階3で
    条件を足すだけで動く。
    """
    lifecycle = _lifecycle(1, OpportunityState.OPEN)
    at = ProcessingPoint(
        time=lifecycle.created_decision_time,
        phase=BACKTEST_PHASES.by_name("P4_CONFIRMATION"),
        sequence=1,
    )

    moved = lifecycle.moved_to(
        OpportunityState.TERMINATED,
        at=at,
        reason=Reason(code=ReasonCode.MARKET_STATE_INVALIDATED),
    )

    assert moved.state is OpportunityState.TERMINATED
    assert moved.terminal == OpportunityTerminal(
        reason=Reason(code=ReasonCode.MARKET_STATE_INVALIDATED), at=at
    )


# --- 遷移9: run 末尾 ---------------------------------------------------------


def test_transition_9_the_run_end_batch_terminates_what_is_left() -> None:
    """遷移9: run 末尾に残った機会を終端し、他は何も返さない（T01 §9.2）。"""
    bars = _bars(FIRE_REARM_FIRE)
    evaluator = _evaluator(bars, max_active=2)
    evaluator.step(_close_batch(bars, -3, 1))

    result = evaluator.step(
        PublicationBatch(
            batch_id=EventId(50),
            decision_time=UtcTime.from_components(2026, 1, 16, 22, 0),
            phases=BACKTEST_PHASES,
            is_run_end=True,
        )
    )

    assert result.outputs == ()
    assert result.evaluations == ()
    assert result.proposals == ()
    assert result.management_requests == ()
    assert len(result.transitions) == 1
    assert result.transitions[0].reason is not None
    assert result.transitions[0].reason.code is ReasonCode.RUN_END
    assert result.transitions[0].phase.name == "RUN_END"


def test_nothing_is_accepted_after_the_run_end_batch() -> None:
    """D05 §6.1: 末尾の合図は1 run に1回だけで、そのあとの判断時点は無い。

    末尾のあとに通常のバッチを受け付けると、そこで生まれた取引機会を `RUN_END` で終端する
    機会がもう無く、終端理由別の集計で機会の総数が合わなくなる。
    """
    bars = _bars(FIRE_REARM_FIRE[:1])
    evaluator = _evaluator(bars)
    end = UtcTime.from_components(2026, 1, 16, 22, 0)
    evaluator.step(
        PublicationBatch(
            batch_id=EventId(50), decision_time=end, phases=BACKTEST_PHASES, is_run_end=True
        )
    )

    with pytest.raises(KernelValueError, match="after the end-of-run batch"):
        evaluator.step(
            PublicationBatch(
                batch_id=EventId(51),
                decision_time=end,
                phases=BACKTEST_PHASES,
                is_run_end=True,
            )
        )
    with pytest.raises(KernelValueError, match="after the end-of-run batch"):
        evaluator.step(_close_batch(bars, -1, 52))


def test_a_run_end_batch_must_not_carry_publications() -> None:
    """D05 §6.1: 末尾の合figと通常の公開・通知を同じバッチに混ぜない。"""
    bars = _bars(FIRE_REARM_FIRE[:1])
    with pytest.raises(KernelValueError, match="must not carry publications"):
        PublicationBatch(
            batch_id=EventId(50),
            decision_time=UtcTime.from_components(2026, 1, 16, 22, 0),
            phases=BACKTEST_PHASES,
            available_bars=(bars[-1].key,),
            is_run_end=True,
        )


# --- 遷移10・11: 段階3で起きるもの ------------------------------------------


def test_transition_10_open_can_move_to_confirmed() -> None:
    """遷移10: 後続確認の成立は中間状態への遷移であり、理由を伴わない（Q2 決定）。"""
    lifecycle = _lifecycle(1, OpportunityState.OPEN)
    at = ProcessingPoint(
        time=lifecycle.created_decision_time,
        phase=BACKTEST_PHASES.by_name("P4_CONFIRMATION"),
        sequence=1,
    )

    moved = lifecycle.moved_to(OpportunityState.CONFIRMED, at=at)

    assert moved.state is OpportunityState.CONFIRMED
    assert moved.terminal is None
    assert moved.is_active


def test_transition_11_confirmed_can_expire() -> None:
    """遷移11: 確認期限に到達した機会は期限切れで終端する。"""
    lifecycle = _lifecycle(1, OpportunityState.CONFIRMED)
    at = ProcessingPoint(
        time=lifecycle.created_decision_time,
        phase=BACKTEST_PHASES.by_name("OPPORTUNITY_LIFECYCLE"),
        sequence=1,
    )

    moved = lifecycle.moved_to(
        OpportunityState.TERMINATED, at=at, reason=Reason(code=ReasonCode.EXPIRED)
    )

    assert moved.state is OpportunityState.TERMINATED


# --- 表に無い遷移 ------------------------------------------------------------


def test_a_terminated_opportunity_is_never_revived() -> None:
    """ADR-0031: 終端した機会への遷移要求は拒否する（表に行が無い）。"""
    lifecycle = _lifecycle(1, OpportunityState.OPEN)
    at = ProcessingPoint(
        time=lifecycle.created_decision_time,
        phase=BACKTEST_PHASES.by_name("P3_TRIGGER"),
        sequence=1,
    )
    terminated = lifecycle.moved_to(
        OpportunityState.TERMINATED, at=at, reason=Reason(code=ReasonCode.RUN_END)
    )

    with pytest.raises(KernelValueError, match="already terminated"):
        terminated.moved_to(OpportunityState.OPEN, at=at)


def test_a_terminating_transition_must_record_a_reason() -> None:
    """D05 §7.2: 終端は必ず理由を伴う（状態と理由は別フィールド）。"""
    lifecycle = _lifecycle(1, OpportunityState.OPEN)
    at = ProcessingPoint(
        time=lifecycle.created_decision_time,
        phase=BACKTEST_PHASES.by_name("P3_TRIGGER"),
        sequence=1,
    )

    with pytest.raises(KernelValueError, match="requires a reason"):
        lifecycle.moved_to(OpportunityState.TERMINATED, at=at)


def test_a_reason_outside_the_vocabulary_is_rejected() -> None:
    """上位設計書 §4.5 の語彙以外の理由で終端させない。"""
    lifecycle = _lifecycle(1, OpportunityState.OPEN)
    at = ProcessingPoint(
        time=lifecycle.created_decision_time,
        phase=BACKTEST_PHASES.by_name("P3_TRIGGER"),
        sequence=1,
    )

    with pytest.raises(KernelValueError, match="not a terminal reason"):
        lifecycle.moved_to(OpportunityState.TERMINATED, at=at, reason=Reason(code=ReasonCode.RISK))


def test_an_admission_notice_for_an_unknown_opportunity_is_rejected() -> None:
    """受付通知が知らない機会を指していたら、黙って無視せず止める。"""
    bars = _bars(FIRE_REARM_FIRE[:1])
    evaluator = _evaluator(bars)

    with pytest.raises(KernelValueError, match="unknown opportunity"):
        evaluator.step(_admission(accepted=True, opportunity_seq=7))


def test_all_non_terminal_states_count_towards_the_limit() -> None:
    """D05 §7.1: 受付待ちの機会も同時保持の数に入る。"""
    for state in (
        OpportunityState.OPEN,
        OpportunityState.CONFIRMED,
        OpportunityState.ORDER_PENDING,
    ):
        assert _lifecycle(1, state).is_active


def test_the_oldest_opportunity_is_the_one_that_is_superseded() -> None:
    """D04 §10.3: 置換の鍵は（生成時の判断時刻, 機会の連番）の辞書式順序。"""
    older = _lifecycle(1, OpportunityState.OPEN)
    newer = _lifecycle(2, OpportunityState.OPEN)

    assert older.supersession_key < newer.supersession_key
