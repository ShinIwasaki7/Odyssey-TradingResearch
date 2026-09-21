"""検証戦略 A を T01 の時刻表どおりに流す（T01 §2・D05 §9）。

紙上トレース T01（承認済み）が、1つの判断時点で「どの型のどのフィールドに何が入るか」を
人間が手で追った結果を、そのまま期待値として使う。数値は人工データであり、手計算で検算
できることだけを目的にしている（全体計画 §8.2）。

T01 §2.2 の判断時点 T = 2026-01-06T09:00Z で起きること:

| フェーズ | 起きること |
|---|---|
| P1 | 直近20本（当該足を除く）の高値の最大 150.000 と安値の最小 149.500 |
| P3 | 終値 150.040 が 150.000 を上回り、直前が不成立なので取引機会を1件生成 |
| P5 | 成行の注文意図と初期の損切り 149.500 がそろい、発注提案を1件返す |
| 約定後 | 約定価格 150.080 と損切り 149.500 から利確 151.240 を返す |

通し番号（`ProcessingPoint.sequence`）は D05 §6.6（v1.3）の規則で決まる。本テストは規則
（`step` 内で 0 から始まる1本の番号を、出力記録と遷移記録が発生順に共有する）を確かめる。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.ids import (
    AttemptId,
    EventId,
    IdAllocator,
    OpportunityId,
    OutputId,
    PositionId,
    RunId,
)
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.reason import ReasonCode
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy, CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.evaluation import RuntimeEventKind
from odyssey_fx.strategy.records.payloads import (
    Opportunity,
    OrderIntent,
    OrderType,
    ProtectionLevels,
    SetTakeProfit,
    TradeDirection,
)
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from odyssey_fx.strategy.runtime.opportunities import OpportunityState
from odyssey_fx.strategy.runtime.ports import (
    AdmissionNotice,
    BarClosure,
    PublicationBatch,
    RuntimeEventNotice,
)
from odyssey_fx.strategy.runtime.requests import Evaluated
from tests.fixtures.strategy.fakes import (
    CollectingSink,
    FakeContextView,
    FakeMarketDataView,
    FakePositionContext,
    hourly_bars,
)
from tests.fixtures.strategy.phases import BACKTEST_PHASES
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES, TIMEFRAMES, strategy_a

#: T01 §2.2 の判断時刻。
DECISION_TIME = UtcTime.from_components(2026, 1, 6, 9, 0)

#: 突破した足の区間（`[08:00, 09:00)`）。
SIGNAL_INTERVAL = Interval(start=UtcTime.from_components(2026, 1, 6, 8, 0), end=DECISION_TIME)

#: T01 §2.1 の人工データが決める水準。
BREAKOUT_LEVEL = Price(decimal_from_str("150.000"))
STOP_LEVEL = Price(decimal_from_str("149.500"))
SIGNAL_CLOSE = Price(decimal_from_str("150.040"))
ENTRY_PRICE = Price(decimal_from_str("150.080"))
TAKE_PROFIT = Price(decimal_from_str("151.240"))


def _bars(*, breakout: bool = True) -> tuple[Bar, ...]:
    """21本の1時間足を作る（20本の窓＋突破した足）。

    窓（先頭20本）の高値の最大は 150.000、安値の最小は 149.500 になるよう明示して置く。
    末尾の足が「当該足」であり、`exclude_latest_bars=1` により窓から外れる。
    """
    specs: list[tuple[str, str, str, str]] = []
    for index in range(20):
        high = "150.000" if index == 3 else "149.900"
        low = "149.500" if index == 7 else "149.700"
        specs.append(("149.800", high, low, "149.850"))
    close = "150.040" if breakout else "149.900"
    specs.append(("149.900", "150.100", "149.800", close))
    first_start = UtcTime.from_components(2026, 1, 5, 12, 0)
    return hourly_bars(SIGNAL_SERIES, first_start, specs)


def _compiled() -> CompiledStrategy:
    result = compile_strategy(strategy_a(), INITIAL_CATALOG, TIMEFRAMES)
    assert isinstance(result, CompileSucceeded), result
    return result.compiled


def _allocator() -> IdAllocator:
    return IdAllocator(RunId(ContentDigest.sha256("a" * 64)))


def _evaluator(
    bars: tuple[Bar, ...],
    positions: dict[PositionId, FakePositionContext] | None = None,
) -> tuple[StrategyEvaluator, CollectingSink]:
    sink = CollectingSink()
    evaluator = StrategyEvaluator(
        compiled=_compiled(),
        registry=INITIAL_CATALOG,
        market_data=FakeMarketDataView({SIGNAL_SERIES: bars}),
        context=FakeContextView(positions=positions),
        sink=sink,
        allocator=_allocator(),
    )
    return evaluator, sink


def _bar_close_batch(batch_id: int = 1) -> PublicationBatch:
    """T01 §2.2 の rank 3 が組み立てる公開バッチ。"""
    bars = _bars()
    return PublicationBatch(
        batch_id=EventId(batch_id),
        decision_time=DECISION_TIME,
        phases=BACKTEST_PHASES,
        available_bars=(bars[-1].key,),
        scheduled_closes=(BarClosure(bar_key=bars[-1].key, interval=SIGNAL_INTERVAL),),
    )


def test_the_breakout_produces_one_entry_proposal() -> None:
    """T01 §2.2: 1回の判断時点で発注提案が1件だけ生まれる。"""
    evaluator, _ = _evaluator(_bars())

    result = evaluator.step(_bar_close_batch())

    assert len(result.proposals) == 1
    proposal = result.proposals[0]
    assert proposal.order_intent == OrderIntent(
        symbol=SIGNAL_SERIES.symbol,
        direction=TradeDirection.LONG,
        order_type=OrderType.MARKET,
        price_condition=None,
        expiry=None,
    )
    assert proposal.protection == ProtectionLevels(stop_loss=STOP_LEVEL, take_profit=None)
    assert proposal.decision_time == DECISION_TIME
    assert proposal.opportunity_id == OpportunityId(1)


def test_the_levels_match_the_paper_trace() -> None:
    """T01 §2.2 の rank 5: 突破水準 150.000 と損切り水準 149.500 が出る。"""
    evaluator, _ = _evaluator(_bars())

    result = evaluator.step(_bar_close_batch())

    levels = {
        record.producer.instance_id: record.payload
        for record in result.outputs
        if record.producer.output_name == "level"
    }
    assert levels == {"breakout_level": BREAKOUT_LEVEL, "stop_level": STOP_LEVEL}


def test_the_opportunity_carries_the_symbol_interval_and_reference_level() -> None:
    """T01 §2.2 の rank 7: ランタイムが識別子・銘柄・対象区間を付ける（D05 §4.2）。"""
    evaluator, _ = _evaluator(_bars())

    result = evaluator.step(_bar_close_batch())

    payloads = [
        record.payload for record in result.outputs if isinstance(record.payload, Opportunity)
    ]
    assert len(payloads) == 1
    opportunity = payloads[0]
    assert opportunity == Opportunity(
        opportunity_id=OpportunityId(1),
        symbol=SIGNAL_SERIES.symbol,
        direction=TradeDirection.LONG,
        signal_interval=SIGNAL_INTERVAL,
        reference_values={"breakout_level": BREAKOUT_LEVEL},
    )


def test_the_proposal_points_at_the_outputs_it_was_built_from() -> None:
    """T01 §2.3: 発注の根拠になった出力の識別子が戻り値に載る（D05 §6.2 手順9）。"""
    evaluator, _ = _evaluator(_bars())

    result = evaluator.step(_bar_close_batch())

    proposal = result.proposals[0]
    by_id = {record.output_id: record for record in result.outputs}
    assert by_id[proposal.intent_output_id].producer.instance_id == "entry_order"
    assert by_id[proposal.protection_output_id].producer.instance_id == "initial_stop"


def test_the_output_ids_follow_the_evaluation_order() -> None:
    """T01 §2.2: 出力は評価順に 1 から採番される（D05 §6.4・§6.6）。"""
    evaluator, sink = _evaluator(_bars())

    result = evaluator.step(_bar_close_batch())

    assert [record.output_id for record in result.outputs] == [
        OutputId(index) for index in range(1, 6)
    ]
    assert [record.producer.instance_id for record in result.outputs] == [
        "breakout_level",
        "stop_level",
        "entry_trigger",
        "entry_order",
        "initial_stop",
    ]
    assert sink.records == list(result.outputs)


def test_the_step_sequence_increases_monotonically() -> None:
    """D05 §6.6: 出力と遷移は `step` 内の1本の通し番号を共有する。"""
    evaluator, _ = _evaluator(_bars())

    result = evaluator.step(_bar_close_batch())

    numbers = [record.sequence for record in result.outputs]
    numbers += [transition.at.sequence for transition in result.transitions]
    assert len(set(numbers)) == len(numbers)
    assert numbers == sorted(numbers) or sorted(numbers) == list(range(len(numbers)))


def test_every_triggered_component_leaves_an_evaluation_record() -> None:
    """D05 §6.4: 起動した使用箇所ごとに評価記録が必ず1件残る。"""
    evaluator, _ = _evaluator(_bars())

    result = evaluator.step(_bar_close_batch())

    assert [record.instance_id for record in result.evaluations] == [
        "breakout_level",
        "stop_level",
        "entry_trigger",
        "entry_order",
        "initial_stop",
    ]
    assert all(isinstance(record.outcome, Evaluated) for record in result.evaluations)


def test_the_opportunity_moves_to_order_pending() -> None:
    """T01 §2.2 の rank 9: 遷移1（生成）と遷移4（発注試行へ）が記録される。"""
    evaluator, _ = _evaluator(_bars())

    result = evaluator.step(_bar_close_batch())

    assert [(transition.from_state, transition.to_state) for transition in result.transitions] == [
        (None, OpportunityState.OPEN),
        (OpportunityState.OPEN, OpportunityState.ORDER_PENDING),
    ]
    assert evaluator.state.opportunities[0].state is OpportunityState.ORDER_PENDING


def test_the_take_profit_is_evaluated_after_the_fill() -> None:
    """T01 §2.5: 約定後の起動点で利確 151.240 が出る（`reward_risk=2.0`）。

    `risk = 150.080 - 149.500 = 0.580`、`151.240 = 150.080 + 0.580 x 2`。
    """
    position_id = PositionId(1)
    evaluator, _ = _evaluator(
        _bars(),
        positions={
            position_id: FakePositionContext(
                position_id=position_id,
                direction=TradeDirection.LONG,
                entry_price=ENTRY_PRICE,
                effective_stop_loss=STOP_LEVEL,
            )
        },
    )
    evaluator.step(_bar_close_batch())

    second = PublicationBatch(
        batch_id=EventId(2),
        decision_time=DECISION_TIME,
        phases=BACKTEST_PHASES,
        runtime_events=(
            RuntimeEventNotice(
                kind=RuntimeEventKind.POSITION_OPENED,
                position_id=position_id,
                opportunity_id=OpportunityId(1),
            ),
        ),
        admissions=(
            AdmissionNotice(
                opportunity_id=OpportunityId(1),
                attempt_id=AttemptId(1),
                accepted=True,
            ),
        ),
    )
    result = evaluator.step(second)

    assert len(result.management_requests) == 1
    request = result.management_requests[0]
    assert request.position_id == position_id
    assert request.action == SetTakeProfit(price=TAKE_PROFIT)
    assert request.decision_time == DECISION_TIME
    by_id = {record.output_id: record for record in result.outputs}
    assert by_id[request.source_output_id].producer.instance_id == "take_profit"


def test_the_accepted_order_terminates_its_own_opportunity() -> None:
    """T01 §2.5 の順1: 受付通知を入口で適用し、遷移5 で終端する。"""
    position_id = PositionId(1)
    evaluator, _ = _evaluator(
        _bars(),
        positions={
            position_id: FakePositionContext(
                position_id=position_id,
                direction=TradeDirection.LONG,
                entry_price=ENTRY_PRICE,
                effective_stop_loss=STOP_LEVEL,
            )
        },
    )
    evaluator.step(_bar_close_batch())

    result = evaluator.step(
        PublicationBatch(
            batch_id=EventId(2),
            decision_time=DECISION_TIME,
            phases=BACKTEST_PHASES,
            admissions=(
                AdmissionNotice(
                    opportunity_id=OpportunityId(1),
                    attempt_id=AttemptId(1),
                    accepted=True,
                ),
            ),
        )
    )

    assert len(result.transitions) == 1
    transition = result.transitions[0]
    assert transition.to_state is OpportunityState.TERMINATED
    assert transition.reason is not None
    assert transition.reason.code is ReasonCode.FULFILLED_BY_ORDER_ACCEPTANCE
    assert transition.attempt_id == AttemptId(1)
    assert transition.phase.name == "POST_FILL_EVALUATION"


def test_a_rejected_order_terminates_with_its_own_reason() -> None:
    """T01 §6.2: リスク審査の拒否は遷移6 で終端する。"""
    evaluator, _ = _evaluator(_bars())
    evaluator.step(_bar_close_batch())

    result = evaluator.step(
        PublicationBatch(
            batch_id=EventId(2),
            decision_time=DECISION_TIME,
            phases=BACKTEST_PHASES,
            admissions=(
                AdmissionNotice(
                    opportunity_id=OpportunityId(1),
                    attempt_id=AttemptId(1),
                    accepted=False,
                ),
            ),
        )
    )

    assert result.transitions[0].reason is not None
    assert result.transitions[0].reason.code is ReasonCode.ORDER_ATTEMPT_REJECTED


def test_the_edge_trigger_does_not_fire_while_the_condition_stays_true() -> None:
    """T01 §2.2 の末尾: 成立が続く間は `EDGE` により再発火しない（D04 §10.4）。"""
    bars = _bars()
    follow_up = hourly_bars(
        SIGNAL_SERIES,
        UtcTime.from_components(2026, 1, 6, 9, 0),
        [("150.040", "150.200", "150.000", "150.150")],
    )
    evaluator, _ = _evaluator(bars + follow_up)
    evaluator.step(_bar_close_batch())

    later = UtcTime.from_components(2026, 1, 6, 10, 0)
    result = evaluator.step(
        PublicationBatch(
            batch_id=EventId(2),
            decision_time=later,
            phases=BACKTEST_PHASES,
            available_bars=(follow_up[0].key,),
            scheduled_closes=(
                BarClosure(
                    bar_key=follow_up[0].key,
                    interval=Interval(start=later - timedelta(hours=1), end=later),
                ),
            ),
        )
    )

    assert not any(isinstance(record.payload, Opportunity) for record in result.outputs)
    assert result.proposals == ()


def test_a_batch_is_not_processed_twice() -> None:
    """D05 §6.1: 直前と同じバッチ識別子で呼ばれたら状態を更新せず拒否する。"""
    evaluator, _ = _evaluator(_bars())
    batch = _bar_close_batch()
    evaluator.step(batch)

    with pytest.raises(Exception, match="already been processed"):
        evaluator.step(batch)
