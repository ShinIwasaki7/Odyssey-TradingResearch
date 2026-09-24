"""紙上トレース T02 の経路1・2・3・8・9 を戦略ランタイム単体で再現する（D05 §6.11・§7.6・§7.7）。

エンジンを使わず、戦略ランタイムの `step` を判断時点ごとに直接呼ぶ。発注提案が出た判断時点では、
エンジンの受付と約定の代わりに受付結果と約定の通知だけを渡す（`confirmation_harness`）。約定
価格（建玉 P1 の 149.390）はエンジン側の値なので、ここでは確かめない（段階3 実装 PR 5/5）。

| 経路 | T02 | 確かめること |
|---|---|---|
| 1 | §3 | 1判断時点で 市場状態 → 取引機会 → 開始足で確認（遷移10）→ 発注提案（遷移4） |
| 2 | §4 | 開始足で未確認（配送しない）、次の15分足で確認成立 |
| 3 | §5 | 15分足4本の確認期限に到達して `EXPIRED`（遷移11）。4本目の確認足は使われない |
| 8 | §10 | トレーリングの評価要求が建玉1件につき1件作られ、`UpdateStop` を返す |
| 9 | §11 | 市場状態が買いを許さない判断時点の発火は、記録したうえで終端する（遷移12） |

あわせて、遷移10・11 の名前付きテスト（D08 §6.2 の規約）、有効性の再検査の4区分（D08 §13.2
#6。確認期限を長くした専用の宣言を2つ使う）、市場状態の連鎖による待機（D05 §7.6）、待機中に
受け取った取引機会が終わった要求の決着（2026-09-24 の人間の決定）を置く。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from odyssey_fx.common.ids import EventId, OpportunityId, PositionId
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.schedule import FixedSeriesDelay, InjectedBarDelay
from odyssey_fx.strategy.catalog.conditions import compare
from odyssey_fx.strategy.catalog.exits import trailing_stop
from odyssey_fx.strategy.catalog.features import ema
from odyssey_fx.strategy.catalog.filters import condition_filter
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    ComponentRegistry,
    StatelessImplementation,
    build_registry,
)
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.missing import (
    Error,
    OnSuperseded,
    SkipEvaluation,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.read_spec import LatestAvailable
from odyssey_fx.strategy.declarations.refs import MarketDataField, MarketDataRef
from odyssey_fx.strategy.declarations.specs import InputBinding
from odyssey_fx.strategy.records.payloads import (
    ConfirmationResult,
    MarketPermission,
    Opportunity,
    OrderIntent,
    OrderType,
    ProtectionLevels,
    TradeDirection,
    UpdateStop,
)
from odyssey_fx.strategy.records.records import Observation
from odyssey_fx.strategy.runtime.confirmation import (
    ConfirmationAttempt,
    ConfirmationAttemptOutcome,
)
from odyssey_fx.strategy.runtime.opportunities import (
    OpportunityState,
    ValidityRecheckOutcome,
)
from odyssey_fx.strategy.runtime.ports import PublicationBatch
from odyssey_fx.strategy.runtime.requests import Evaluated, Failed, Skipped, Waiting
from odyssey_fx.strategy.runtime.waiting import WaitEventKind, WaitUntilBars
from tests.fixtures.strategy.confirmation_harness import ConfirmationRun
from tests.fixtures.strategy.phases import BACKTEST_PHASES
from tests.fixtures.strategy.strategy_b import (
    DAILY_SERIES,
    HOURLY_SERIES,
    M15_SERIES,
    strategy_b,
)

START = UtcTime.parse("2015-01-06T22:00:00Z")
AT_0900 = UtcTime.parse("2015-01-07T09:00:00Z")
AT_0915 = UtcTime.parse("2015-01-07T09:15:00Z")
AT_0930 = UtcTime.parse("2015-01-07T09:30:00Z")
AT_0945 = UtcTime.parse("2015-01-07T09:45:00Z")
AT_1000 = UtcTime.parse("2015-01-07T10:00:00Z")
AT_1100 = UtcTime.parse("2015-01-07T11:00:00Z")
AT_1200 = UtcTime.parse("2015-01-07T12:00:00Z")
DAILY_CLOSE = UtcTime.parse("2015-01-07T22:00:00Z")
AT_2215 = UtcTime.parse("2015-01-07T22:15:00Z")
NEXT_DAY_0900 = UtcTime.parse("2015-01-08T09:00:00Z")

O1 = OpportunityId(1)
P1 = PositionId(1)
#: 確認の開始足（T02 §2.3。`[01-07 08:45, 09:00)`）。
START_BAR = BarKey(series=M15_SERIES, bar_start=UtcTime.parse("2015-01-07T08:45:00Z"))
SIGNAL_INTERVAL = Interval(start=UtcTime.parse("2015-01-07T08:00:00Z"), end=AT_0900)
START_BAR_INTERVAL = Interval(start=START_BAR.bar_start, end=AT_0900)


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _m15(start: str) -> BarKey:
    return BarKey(series=M15_SERIES, bar_start=UtcTime.parse(start))


# --- 経路1（T02 §3）----------------------------------------------------------------


def test_route_1_market_state_opportunity_confirmation_and_proposal_in_one_decision() -> None:
    """T02 §3.2〜§3.4: 01-07 09:00Z の1判断時点で、確認成立から発注提案まで進む。"""
    run = ConfirmationRun().run(START, AT_0900)
    result = run.results[AT_0900]

    # P3: 市場状態は前日 22:00Z の許可（買いのみ）。取引機会 O1 が生まれる（遷移1）。
    opportunity = run.payloads(AT_0900)["entry_trigger"]
    assert opportunity == Opportunity(
        opportunity_id=O1,
        symbol=opportunity.symbol,  # type: ignore[attr-defined]
        direction=TradeDirection.LONG,
        signal_interval=SIGNAL_INTERVAL,
        reference_values={"breakout_level": _price("149.300")},
    )
    lifecycle = run.evaluator.state.opportunities[0]
    assert lifecycle.confirmation_start_bar == START_BAR
    assert lifecycle.deadline_at == WaitUntilBars(series=M15_SERIES, remaining=4)

    # P4: 開始足で確認（`include_start_bar=True`）。確認結果はランタイムが組み立てる。
    confirmation = run.only(AT_0900, "entry_filter")
    assert confirmation.opportunity_id == O1
    assert confirmation.target_interval == START_BAR_INTERVAL
    assert isinstance(confirmation.outcome, Evaluated)
    assert run.payloads(AT_0900)["entry_filter"] == ConfirmationResult(
        opportunity_id=O1, confirmation_interval=START_BAR_INTERVAL, confirmed=True
    )
    assert result.confirmation_attempts == (
        ConfirmationAttempt(
            opportunity_id=O1,
            bar_key=START_BAR,
            request_id=confirmation.request_id,
            outcome=ConfirmationAttemptOutcome.CONFIRMED,
        ),
    )

    # 遷移は 1 → 10 → 4 の順。確認成立の遷移10 は確認結果の出力記録の後に刻む（T02 §14 #11）。
    moves = [(item.from_state, item.to_state, item.at.phase.name) for item in result.transitions]
    assert moves == [
        (None, OpportunityState.OPEN, "P3_TRIGGER"),
        (OpportunityState.OPEN, OpportunityState.CONFIRMED, "P4_CONFIRMATION"),
        (OpportunityState.CONFIRMED, OpportunityState.ORDER_PENDING, "P5_ORDER_INTENT"),
    ]
    confirmation_output = next(
        item for item in result.outputs if item.producer.instance_id == "entry_filter"
    )
    assert confirmation_output.sequence < result.transitions[1].at.sequence

    # 有効性の再検査は確認評価の前（P4）と発注提案の直前（P5）の2回。どちらも成立（表19 に2件）。
    rechecks = result.validity_rechecks
    assert [(item.outcome, item.at.phase.name) for item in rechecks] == [
        (ValidityRecheckOutcome.SATISFIED, "P4_CONFIRMATION"),
        (ValidityRecheckOutcome.SATISFIED, "P5_ORDER_INTENT"),
    ]
    assert rechecks[0].at.sequence < confirmation_output.sequence

    # P5: 注文意図と保護水準は確認結果の配送で起動し、機会と確認足を引き継ぐ。
    for instance_id in ("entry_order", "initial_stop"):
        record = run.only(AT_0900, instance_id)
        assert (record.opportunity_id, record.target_interval) == (O1, START_BAR_INTERVAL)
    (proposal,) = result.proposals
    assert proposal.opportunity_id == O1
    assert proposal.order_intent == OrderIntent(
        symbol=proposal.order_intent.symbol,
        direction=TradeDirection.LONG,
        order_type=OrderType.MARKET,
    )
    assert proposal.protection == ProtectionLevels(stop_loss=_price("148.950"))


def test_route_1_the_permission_applied_is_the_last_one_before_the_opportunity_output() -> None:
    """D05 §7.6: 適用した許可は、取引機会の出力記録より小さい `OutputId` の最後の許可である。"""
    run = ConfirmationRun().run(START, AT_0900)
    permissions = [
        record
        for result in run.results.values()
        for record in result.outputs
        if record.producer.instance_id == "market_state"
    ]
    opportunity_output = next(
        record
        for record in run.results[AT_0900].outputs
        if record.producer.instance_id == "entry_trigger"
    )
    applied = [record for record in permissions if record.output_id < opportunity_output.output_id]
    payload = applied[-1].payload
    assert isinstance(payload, Observation)
    assert payload.value == MarketPermission(allow_long=True, allow_short=False)
    assert payload.freshness_time == START


# --- 経路2（T02 §4）----------------------------------------------------------------


def test_route_2_unconfirmed_on_the_start_bar_and_confirmed_on_the_next() -> None:
    """T02 §4: 開始足で未確認なら配送せず、次の15分足で確認が成立する。"""
    run = ConfirmationRun(m15_variant="route2").run(START, AT_0915)

    first = run.results[AT_0900]
    assert run.payloads(AT_0900)["entry_filter"] == ConfirmationResult(
        opportunity_id=O1, confirmation_interval=START_BAR_INTERVAL, confirmed=False
    )
    # 成立しなかった確認結果は判断履歴に残すが、注文意図と保護水準を起動しない（D05 §7.7）。
    assert run.evaluations(AT_0900, "entry_order") == []
    assert run.evaluations(AT_0900, "initial_stop") == []
    assert first.proposals == ()
    assert [item.outcome for item in first.confirmation_attempts] == [
        ConfirmationAttemptOutcome.NOT_CONFIRMED
    ]
    assert [item.to_state for item in first.transitions] == [OpportunityState.OPEN]

    second = run.results[AT_0915]
    first_bar = Interval(start=AT_0900, end=AT_0915)
    assert run.payloads(AT_0915)["entry_filter"] == ConfirmationResult(
        opportunity_id=O1, confirmation_interval=first_bar, confirmed=True
    )
    assert [(item.bar_key, item.outcome) for item in second.confirmation_attempts] == [
        (_m15("2015-01-07T09:00:00Z"), ConfirmationAttemptOutcome.CONFIRMED)
    ]
    assert [(item.from_state, item.to_state) for item in second.transitions] == [
        (OpportunityState.OPEN, OpportunityState.CONFIRMED),
        (OpportunityState.CONFIRMED, OpportunityState.ORDER_PENDING),
    ]
    # 保護水準は1時間足で最後に計算した値（09:00Z の 148.950、鮮度 08:00Z）を読む（T02 §4）。
    (proposal,) = second.proposals
    assert proposal.protection == ProtectionLevels(stop_loss=_price("148.950"))
    # 機会の記録には確認足ごとに1件、確認足の順に残る。
    (lifecycle,) = run.evaluator.state.opportunities
    assert [item.outcome for item in lifecycle.attempts] == [
        ConfirmationAttemptOutcome.NOT_CONFIRMED,
        ConfirmationAttemptOutcome.CONFIRMED,
    ]


# --- 経路3（T02 §5）----------------------------------------------------------------


def test_route_3_the_confirmation_deadline_expires_the_opportunity() -> None:
    """T02 §5: 15分足4本の期限に 10:00Z で到達し、4本目の確認足は確認に使われない。"""
    run = ConfirmationRun(m15_variant="route3").run(START, AT_1000)

    expiry = run.results[AT_1000]
    assert [
        (item.from_state, item.to_state, item.at.phase.name, item.at.sequence, item.reason)
        for item in expiry.transitions
    ] == [
        (
            OpportunityState.OPEN,
            OpportunityState.TERMINATED,
            "OPPORTUNITY_LIFECYCLE",
            0,
            Reason(code=ReasonCode.EXPIRED),
        )
    ]
    # 期限の判定は確認の評価より先。4本目の確認足では要求を作らない。
    assert run.evaluations(AT_1000, "entry_filter") == []
    assert expiry.confirmation_attempts == ()
    (lifecycle,) = run.evaluator.state.opportunities
    assert [(item.bar_key.bar_start, item.outcome) for item in lifecycle.attempts] == [
        (UtcTime.parse(start), ConfirmationAttemptOutcome.NOT_CONFIRMED)
        for start in (
            "2015-01-07T08:45:00Z",
            "2015-01-07T09:00:00Z",
            "2015-01-07T09:15:00Z",
            "2015-01-07T09:30:00Z",
        )
    ]
    assert all(not result.proposals for result in run.results.values())


# --- 遷移10・11（D05 §7.2。D08 §6.2 の名前付きテスト）-------------------------------


def test_transition_10_a_confirmed_result_moves_the_open_opportunity_to_confirmed() -> None:
    """遷移10: 確認評価が `confirmed=True` を出すと、P4 で `OPEN → CONFIRMED`（理由なし）。"""
    run = ConfirmationRun(m15_variant="route2").run(START, AT_0915)
    transitions = run.results[AT_0915].transitions
    (confirmed,) = [item for item in transitions if item.to_state is OpportunityState.CONFIRMED]
    assert confirmed.from_state is OpportunityState.OPEN
    assert confirmed.at.phase.name == "P4_CONFIRMATION"
    assert confirmed.reason is None


def test_transition_11_the_confirmation_deadline_terminates_with_expired() -> None:
    """遷移11: 確認足の系列で数えた期限に到達すると、ライフサイクル検査で `EXPIRED` 終端。"""
    run = ConfirmationRun(m15_variant="route3").run(START, AT_1000)
    (expired,) = run.results[AT_1000].transitions
    assert expired.reason == Reason(code=ReasonCode.EXPIRED)
    assert expired.at.phase.name == "OPPORTUNITY_LIFECYCLE"
    remaining = [
        lifecycle.deadline_at
        for result in (run.evaluator.state,)
        for lifecycle in result.opportunities
    ]
    assert remaining == [WaitUntilBars(series=M15_SERIES, remaining=0)]


def test_start_bar_is_skipped_when_the_declaration_says_so() -> None:
    """D05 §7.7: `include_start_bar=False` なら開始足では要求を作らず、期限の数え方は変えない。"""
    run = ConfirmationRun(m15_variant="route2", definition=strategy_b(include_start_bar=False))
    run.run(START, AT_0915)

    assert run.evaluations(AT_0900, "entry_filter") == []
    assert run.results[AT_0900].validity_rechecks == ()
    confirmed = run.only(AT_0915, "entry_filter")
    assert confirmed.target_interval == Interval(start=AT_0900, end=AT_0915)
    assert run.results[AT_0915].proposals != ()


# --- 経路8（T02 §10）----------------------------------------------------------------


def test_route_8_one_trailing_request_per_open_position_on_each_hour() -> None:
    """T02 §10: 建玉 P1 の保有中、1時間足の確定ごとに `trailing` の要求を建玉1件につき1件作る。

    10:00Z と 11:00Z は損切り水準（148.950）が現在の水準と同じなので出力なし。建玉が無かった
    09:00Z には要求を作らない。
    """
    run = ConfirmationRun().run(START, AT_1100)

    assert run.evaluations(AT_0900, "trailing") == []
    for at in (AT_1000, AT_1100):
        (record,) = run.evaluations(at, "trailing")
        assert record.position_id == P1
        assert record.opportunity_id is None
        assert record.outcome == Evaluated(output_ids=())


def test_route_8_the_trailing_stop_returns_update_stop_at_noon() -> None:
    """T02 §10: 12:00Z の `stop_level` は 149.150（鮮度 11:00Z）で、`UpdateStop(149.150)` を返す。

    損切り水準の更新を管理要求として建玉へ通すのは段階3 実装 PR 5/5 である（2026-09-24 の
    人間の決定）。それまでは黙って捨てず評価の失敗として残すので、ここでは部品が返した値を
    部品の呼び出しの記録から確かめ、評価記録が失敗であることを確かめる。
    """
    returned: list[object] = []

    def spy(inputs: object, parameters: object) -> ComponentOutputs:
        result = trailing_stop.evaluate(inputs, parameters)  # type: ignore[arg-type]
        returned.extend(result.outputs.values())
        return result

    registry = _replacing(
        replace(trailing_stop.REGISTRATION, implementation=StatelessImplementation(evaluate=spy))
    )
    run = ConfirmationRun(registry=registry).run(START, AT_1200)

    stop_level = run.payloads(AT_1200)["stop_level"]
    assert isinstance(stop_level, Observation)
    assert (stop_level.value, stop_level.freshness_time) == (
        _price("149.150"),
        UtcTime.parse("2015-01-07T11:00:00Z"),
    )
    assert returned == [UpdateStop(stop_loss=_price("149.150"))]
    (record,) = run.evaluations(AT_1200, "trailing")
    assert record.position_id == P1
    assert isinstance(record.outcome, Failed)
    assert run.results[AT_1200].management_requests == ()
    # 付番の前に止めるので、判断履歴に管理要求の無い出力が残らない。
    assert all(item.producer.instance_id != "trailing" for item in run.sink.records)


# --- 経路9（T02 §11）----------------------------------------------------------------


def test_route_9_a_firing_the_market_state_does_not_permit_is_recorded_and_terminated() -> None:
    """T02 §11: 01-08 09:00Z の発火は、許可（買い不可）により遷移12 で終端し配送されない。"""
    run = ConfirmationRun(open_positions=False).run(START, NEXT_DAY_0900)
    result = run.results[NEXT_DAY_0900]

    opportunity = run.payloads(NEXT_DAY_0900)["entry_trigger"]
    assert isinstance(opportunity, Opportunity)
    assert opportunity.opportunity_id == OpportunityId(2)
    assert opportunity.reference_values == {"breakout_level": _price("149.520")}
    (terminated,) = result.transitions
    assert (terminated.opportunity_id, terminated.from_state, terminated.to_state) == (
        OpportunityId(2),
        None,
        OpportunityState.TERMINATED,
    )
    assert terminated.reason == Reason(code=ReasonCode.MARKET_STATE_INVALIDATED)
    assert terminated.at.phase.name == "P3_TRIGGER"
    output = next(item for item in result.outputs if item.producer.instance_id == "entry_trigger")
    assert terminated.at.sequence < output.sequence
    # 確認待ちの機会が無いので確認の要求も再検査も無い（T02 §11 の追えた要点3）。
    assert run.evaluations(NEXT_DAY_0900, "entry_filter") == []
    assert result.validity_rechecks == ()
    assert result.confirmation_attempts == ()


# --- 有効性の再検査の4区分（D08 §13.2 #6、D05 §7.3・§9.4）--------------------------


def _recheck_run(on_missing: SkipEvaluation | Error) -> ConfirmationRun:
    """確認期限を64本に延ばし、日足を2秒遅らせた検証戦略 B（D08 §13.2 #6 の専用宣言）。

    15分足は確認が成立しない版を使う。機会 O1 は 09:00Z に生まれ、22:00Z の日足の確定を
    またいで確認を待ち続ける。22:00Z には日足の条件（`daily_above_ema`）がより新しい足について
    待機中なので読めず、22:00:02Z に「日足終値が EMA を上回らない」へ変わる。
    """
    definition = strategy_b(deadline_bars=64, binding_on_missing=on_missing)
    return ConfirmationRun(
        FixedSeriesDelay(DAILY_SERIES, timedelta(seconds=2)),
        m15_variant="unconfirmed",
        definition=definition,
    )


def test_validity_recheck_satisfied_missing_skipped_and_not_satisfied() -> None:
    """欠損方針が見送りの版: 成立 → 読めず見送り → 不成立（遷移8）の3区分を通す。"""
    run = _recheck_run(SkipEvaluation()).run(START, AT_2215)

    (satisfied,) = run.results[AT_0900].validity_rechecks
    assert satisfied.outcome is ValidityRecheckOutcome.SATISFIED
    assert satisfied.output_id is not None and satisfied.reason is None

    (skipped,) = run.results[DAILY_CLOSE].validity_rechecks
    assert skipped.outcome is ValidityRecheckOutcome.MISSING_SKIPPED
    assert (skipped.output_id, skipped.reason) == (None, None)
    assert skipped.at.phase.name == "P4_CONFIRMATION"
    # 見送っても機会は残り、確認評価は行われる。
    assert isinstance(run.only(DAILY_CLOSE, "entry_filter").outcome, Evaluated)

    at_2215 = run.results[AT_2215]
    (not_satisfied,) = at_2215.validity_rechecks
    assert not_satisfied.outcome is ValidityRecheckOutcome.NOT_SATISFIED
    (terminated,) = at_2215.transitions
    assert (terminated.from_state, terminated.to_state, terminated.reason) == (
        OpportunityState.OPEN,
        OpportunityState.TERMINATED,
        Reason(code=ReasonCode.MARKET_STATE_INVALIDATED),
    )
    assert terminated.at.phase.name == "P4_CONFIRMATION"
    assert not_satisfied.at.sequence < terminated.at.sequence
    # 確認を始める前に機会が終わったので、確認評価も確認試行も無い（D05 §7.7 の末尾）。
    assert run.evaluations(AT_2215, "entry_filter") == []
    assert at_2215.confirmation_attempts == ()


def test_validity_recheck_missing_failed_stops_the_step() -> None:
    """欠損方針が失敗の版: 読めずに失敗し、`DATA_ERROR` を残して以降の評価を行わない。"""
    run = _recheck_run(Error()).run(START, DAILY_CLOSE)
    result = run.results[DAILY_CLOSE]

    (failed,) = result.validity_rechecks
    assert failed.outcome is ValidityRecheckOutcome.MISSING_FAILED
    assert failed.reason == Reason(code=ReasonCode.DATA_ERROR)
    assert failed.output_id is None
    assert run.evaluations(DAILY_CLOSE, "entry_filter") == []
    assert result.confirmation_attempts == ()
    assert result.proposals == ()


# --- 市場状態の連鎖による待機（D05 §7.6）--------------------------------------------


def test_the_trigger_waits_while_the_market_state_chain_waits() -> None:
    """D05 §7.6: 自分の入力はそろっていても、市場状態の連鎖が待機中なら取引機会の評価も待機する。

    検証戦略 B の突破 Trigger は日足の高値を読むので自分の入力で待機してしまう（T02 §7.1）。
    ここでは水準を1時間足の高値に差し替え、連鎖の待機だけで待つ形にする。期限と追い越しの
    扱いは連鎖の起点（`daily_ema`）の宣言を引き継ぎ、市場状態が出た同じ判断時点で再開する。
    """
    hourly_level = _swap(
        strategy_b(),
        "entry_trigger",
        inputs={
            "price": InputBinding(sources=(MarketDataRef(HOURLY_SERIES, MarketDataField.CLOSE),)),
            "level": InputBinding(sources=(MarketDataRef(HOURLY_SERIES, MarketDataField.HIGH),)),
        },
    )
    late = UtcTime.parse("2015-01-07T22:00:02Z")
    run = ConfirmationRun(
        FixedSeriesDelay(DAILY_SERIES, timedelta(seconds=2)), definition=hourly_level
    ).run(START, late)

    trigger = run.only(DAILY_CLOSE, "entry_trigger")
    assert isinstance(trigger.outcome, Waiting)
    assert [(item.input_name, str(item.source)) for item in trigger.outcome.diagnoses] == [
        ("market_state", "market_state.permission")
    ]
    assert trigger.outcome.deadline_at == WaitUntilBars(series=DAILY_SERIES, remaining=1)
    waiting = {item.request.instance_id: item for item in run.evaluator.state.waiting}
    assert "entry_trigger" not in waiting  # 22:00:02Z に再開して決着した

    resumed = run.only(late, "entry_trigger")
    assert resumed.request_id == trigger.request_id
    assert isinstance(resumed.outcome, Evaluated)
    trigger_events = [
        (event.kind, event.at.phase.name, event.arrived)
        for event in run.results[late].wait_events
        if event.request_id == trigger.request_id
    ]
    assert trigger_events == [
        (WaitEventKind.INPUT_ARRIVED, "P3_TRIGGER", ("market_state",)),
        (WaitEventKind.RESUMED, "P3_TRIGGER", ()),
    ]


# --- 待機中に受け取った取引機会が終わった要求（2026-09-24 の人間の決定）----------------


def test_a_waiting_confirmation_is_skipped_when_its_opportunity_expires() -> None:
    """D05 §6.8 の手順2: 待機のあいだに機会が終わった要求は見送りで閉じ、出来事に理由を残す。

    確認部品に「条件が届くまで待つ」版の契約を試験用に登録し、15分足の EMA と比較も待つ版に
    差し替える。09:00Z 以降の15分足を3時間遅らせると、09:15Z〜09:45Z の確認要求は条件を待った
    まま（追い越されても待ち続ける設定）、10:00Z に機会が確認期限で `EXPIRED` になる。3件とも
    見送りで決着し、確認試行も `WAITING` から `SKIPPED` へ置き換わる。
    """
    run = _waiting_filter_run().run(START, AT_1000)

    started = [
        record.request_id
        for at in (AT_0915, AT_0930, AT_0945)
        for record in run.evaluations(at, "entry_filter")
        if isinstance(record.outcome, Waiting)
    ]
    assert len(started) == 3
    result = run.results[AT_1000]
    # 機会の期限切れ（遷移11）を先に判定し、その機会を受け取って待っていた要求を閉じる。
    (expired,) = result.transitions
    assert expired.reason == Reason(code=ReasonCode.EXPIRED)
    ended = [event for event in result.wait_events if event.kind is WaitEventKind.OPPORTUNITY_ENDED]
    assert [event.request_id for event in ended] == started
    assert all(event.reason == Reason(code=ReasonCode.EXPIRED) for event in ended)
    assert all(event.at.phase.name == "OPPORTUNITY_LIFECYCLE" for event in ended)
    assert all(expired.at.sequence < event.at.sequence for event in ended)
    settled = run.evaluations(AT_1000, "entry_filter")
    assert [(record.request_id, type(record.outcome)) for record in settled] == [
        (request_id, Skipped) for request_id in started
    ]
    attempts = {item.request_id: item.outcome for item in result.confirmation_attempts}
    assert [attempts[request_id] for request_id in started] == [
        ConfirmationAttemptOutcome.SKIPPED
    ] * 3
    assert not any(
        item.request.instance_id == "entry_filter" for item in run.evaluator.state.waiting
    )


def test_the_run_end_settles_a_waiting_confirmation_and_its_attempt() -> None:
    """D06 §10.1 の要求4: run 末尾で閉じた確認要求の試行は `SKIPPED` へ置き換わる。"""
    run = _waiting_filter_run().run(START, AT_0915)
    (waiting,) = run.evaluations(AT_0915, "entry_filter")
    assert [item.outcome for item in run.results[AT_0915].confirmation_attempts] == [
        ConfirmationAttemptOutcome.WAITING
    ]

    result = run.evaluator.step(
        PublicationBatch(
            batch_id=EventId(9_999_999),
            decision_time=AT_0930,
            phases=BACKTEST_PHASES,
            is_run_end=True,
        )
    )

    closed = [event for event in result.wait_events if event.request_id == waiting.request_id]
    assert [event.kind for event in closed] == [WaitEventKind.RUN_END_CLOSED]
    assert [(item.request_id, item.outcome) for item in result.confirmation_attempts] == [
        (waiting.request_id, ConfirmationAttemptOutcome.SKIPPED)
    ]
    (lifecycle,) = run.evaluator.state.opportunities
    assert lifecycle.state is OpportunityState.TERMINATED
    assert lifecycle.attempts[-1].outcome is ConfirmationAttemptOutcome.SKIPPED


# --- 補助 ----------------------------------------------------------------------


def _waiting_filter_run() -> ConfirmationRun:
    """確認部品が条件を待つ版に差し替え、09:00Z 以降の15分足を3時間遅らせた検証戦略 B。

    15分足の EMA と比較も待つ版（v2）にするので、15分足が届かないあいだ比較の出力は「まだ
    出ていない」（D05 §6.8 の待機の伝播）になり、確認要求は条件を待つ。
    """
    waiting_filter = _filter_waiting_for_the_condition()
    definition = _swap(
        _swap(
            _swap(strategy_b(), "m15_ema", contract_ref=contract_ref_for(ema.CONTRACT_V2)),
            "m15_above_ema",
            contract_ref=contract_ref_for(compare.CONTRACT_V2),
        ),
        "entry_filter",
        contract_ref=contract_ref_for(waiting_filter.contract),
    )
    delays = tuple(
        InjectedBarDelay(M15_SERIES, bar_start=UtcTime.parse(start), delay=timedelta(hours=3))
        for start in (
            "2015-01-07T09:00:00Z",
            "2015-01-07T09:15:00Z",
            "2015-01-07T09:30:00Z",
            "2015-01-07T09:45:00Z",
        )
    )
    return ConfirmationRun(
        *delays,
        m15_variant="unconfirmed",
        definition=definition,
        registry=_extended(waiting_filter),
    )


def _filter_waiting_for_the_condition() -> ComponentRegistration:
    """条件が届くまで15分足8本ぶん待ち、追い越されても待ち続ける確認部品（試験用の契約）。"""
    wait = WaitForInput(
        deadline=BarsDeadline(bars=8),
        on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
        on_superseded=OnSuperseded.KEEP_WAITING,
    )
    contract = replace(
        condition_filter.CONTRACT,
        version=90,
        inputs={
            **condition_filter.CONTRACT.inputs,
            "condition": replace(
                condition_filter.CONTRACT.inputs["condition"],
                read_spec=LatestAvailable(max_age=None, on_missing=wait),
            ),
        },
    )
    return replace(condition_filter.REGISTRATION, contract=contract)


def _extended(*extra: ComponentRegistration) -> ComponentRegistry:
    return build_registry((*INITIAL_CATALOG.registrations.values(), *extra))


def _replacing(registration: ComponentRegistration) -> ComponentRegistry:
    table = dict(INITIAL_CATALOG.registrations)
    table[registration.key] = registration
    return build_registry(table.values())


def _swap(
    definition: StrategyDefinition, instance_id: str, **overrides: object
) -> StrategyDefinition:
    """使用箇所1件のフィールドだけを差し替えた宣言。"""
    components = []
    for item in definition.components:
        if item.instance_id != instance_id:
            components.append(item)
            continue
        fields: dict[str, object] = {
            "instance_id": item.instance_id,
            "contract_ref": item.contract_ref,
            "inputs": dict(item.inputs),
            "parameters": dict(item.parameters),
            "evaluation": item.evaluation,
        }
        fields.update(overrides)
        components.append(ComponentInstance(**fields))  # type: ignore[arg-type]
    return replace(definition, components=tuple(components))
