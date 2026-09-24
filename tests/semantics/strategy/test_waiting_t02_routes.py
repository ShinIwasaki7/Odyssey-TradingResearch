"""紙上トレース T02 の経路5〜7 を戦略ランタイム単体で再現する（D05 §6.8〜§6.10）。

エンジンを使わず、戦略ランタイムの `step` を判断時点ごとに直接呼ぶ。市場データは T02 再現
生成器（T02 §16）の足に遅延シナリオを当て、本物の as-of ビューで読む。

- 経路5（T02 §7、D05 §9.3 のケース2）: 日足が2秒遅れると日足の連鎖と突破 Trigger が待機し、
  到着した判断時点で評価順に沿って同じ `step` の中で再開する。
- 経路6（T02 §8、ケース3）: 待機期限（日足1本）に到達すると期限到達の出来事と見送りで
  決着し、注文は出ない。
- 経路7（T02 §9、ケース4）: 1時間足の待機要求は次の1時間足の公開で追い越され、新しい足の
  要求がまた待機に入る。
- 引き渡し #3（T02 §19）の3規則: (a) 欠損方針が食い違えば強い方が勝つ（Q27）、(b) 出力参照
  だけが欠けた待機の本数期限は起動条件の系列で数える（Q28）、(c) 期限と追い越しが同時に
  成立したら期限が勝つ（Q29）。

経路5〜7 のうち、市場状態が待機中なら取引機会を出す評価も待機する規則（D05 §7.6）は
ここでは通らない。突破 Trigger は自分の入力（日足の高値）が欠けて待機するので、T02 と同じ
記録になる。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from odyssey_fx.common.ids import IdAllocator, RunId
from odyssey_fx.common.reason import MissingInputReason, ReasonCode
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.schedule import (
    DelayRule,
    DelayScenario,
    FixedSeriesDelay,
    InjectedBarDelay,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.records.payloads import ConditionState, MarketPermission
from odyssey_fx.strategy.records.records import Observation
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from odyssey_fx.strategy.runtime.requests import (
    Evaluated,
    EvaluationRecord,
    RuntimeStepResult,
    Skipped,
    Superseded,
    Waiting,
)
from odyssey_fx.strategy.runtime.waiting import WaitEvent, WaitEventKind, WaitUntilBars
from tests.fixtures.acceptance import t02_market
from tests.fixtures.strategy.fakes import CollectingSink, FakeContextView
from tests.fixtures.strategy.runtime_harness import asof_view, batches
from tests.fixtures.strategy.strategy_b import (
    DAILY_SERIES,
    HOURLY_SERIES,
    M15_SERIES,
    TIMEFRAMES,
    strategy_b,
)
from tests.fixtures.synthetic import market

T = UtcTime.parse("2015-01-07T22:00:00Z")
T_PLUS_2S = UtcTime.parse("2015-01-07T22:00:02Z")
NEXT_HOUR = UtcTime.parse("2015-01-07T23:00:00Z")
NEXT_DAY = UtcTime.parse("2015-01-08T22:00:00Z")
#: T で終わる日足 D(Jan7) と、T で終わる1時間足。
JAN7 = BarKey(series=DAILY_SERIES, bar_start=UtcTime.parse("2015-01-06T22:00:00Z"))
JAN7_INTERVAL = Interval(start=JAN7.bar_start, end=T)
HOUR_2100 = BarKey(series=HOURLY_SERIES, bar_start=UtcTime.parse("2015-01-07T21:00:00Z"))

DAILY_CHAIN = ("daily_ema", "daily_above_ema", "market_state")
TRACKED = (*DAILY_CHAIN, "entry_trigger", "no_short")


def _market(*rules: DelayRule) -> dict[SeriesId, tuple[Bar, ...]]:
    """T02 の人工データに遅延を当てた足（日足は1時間足から集約する。T02 §16）。"""
    calendar = market.calendar()
    hourly = t02_market.bars_for(
        "1h",
        market.TF_1H,
        calendar,
        Interval(
            start=UtcTime.parse("2014-09-30T22:00:00Z"), end=UtcTime.parse("2015-01-09T22:00:00Z")
        ),
    )
    quarter = t02_market.bars_for(
        "15m",
        market.TF_15M,
        calendar,
        Interval(
            start=UtcTime.parse("2015-01-06T00:00:00Z"), end=UtcTime.parse("2015-01-09T22:00:00Z")
        ),
    )
    daily = t02_market.aggregate(hourly, market.TF_1D_NY17, calendar)
    scenario = DelayScenario(id="t02", version=1, rules=tuple(rules))
    return {
        DAILY_SERIES: t02_market.apply_delay(daily, scenario),
        HOURLY_SERIES: t02_market.apply_delay(hourly, scenario),
        M15_SERIES: quarter,
    }


class _Run:
    """戦略 B のランタイムを判断時点ごとに進め、各 `step` の結果を控える。"""

    def __init__(self, *rules: DelayRule) -> None:
        self.bars = _market(*rules)
        result = compile_strategy(strategy_b(), INITIAL_CATALOG, TIMEFRAMES)
        assert isinstance(result, CompileSucceeded), result
        self.evaluator = StrategyEvaluator(
            compiled=result.compiled,
            registry=INITIAL_CATALOG,
            market_data=asof_view(self.bars),
            context=FakeContextView(),
            sink=CollectingSink(),
            allocator=IdAllocator(RunId(ContentDigest.sha256("b" * 64))),
        )
        self.results: dict[UtcTime, RuntimeStepResult] = {}

    def until(self, end: UtcTime) -> _Run:
        for batch in batches(self.bars, T, end):
            self.results[batch.decision_time] = self.evaluator.step(batch)
        return self

    def evaluations(self, at: UtcTime, instance_id: str) -> list[EvaluationRecord]:
        return [
            record for record in self.results[at].evaluations if record.instance_id == instance_id
        ]

    def only(self, at: UtcTime, instance_id: str) -> EvaluationRecord:
        records = self.evaluations(at, instance_id)
        assert len(records) == 1, records
        return records[0]

    def events(self, at: UtcTime) -> list[WaitEvent]:
        return list(self.results[at].wait_events)


def _kinds(events: Sequence[WaitEvent]) -> list[tuple[str, str, str, tuple[str, ...]]]:
    return [
        (str(item.request_id), item.kind.value, item.at.phase.name, item.arrived) for item in events
    ]


# --- 経路5（T02 §7）----------------------------------------------------------------


def test_route_5_the_daily_chain_and_the_trigger_wait_when_the_daily_bar_is_late() -> None:
    """T02 §7.1: 日足が2秒遅れた T では、日足の連鎖3段と突破 Trigger が待機に入る。"""
    run = _Run(FixedSeriesDelay(DAILY_SERIES, timedelta(seconds=2))).until(T)

    daily_ema = run.only(T, "daily_ema")
    assert isinstance(daily_ema.outcome, Waiting)
    assert [(d.input_name, d.reason) for d in daily_ema.outcome.diagnoses] == [
        ("prices", MissingInputReason.INPUT_MISSING_OR_INVALID)
    ]
    assert daily_ema.outcome.deadline_at == WaitUntilBars(series=DAILY_SERIES, remaining=1)
    assert daily_ema.target_interval == JAN7_INTERVAL

    above = run.only(T, "daily_above_ema")
    assert isinstance(above.outcome, Waiting)
    assert [(d.input_name, d.reason) for d in above.outcome.diagnoses] == [
        ("left", MissingInputReason.LATEST_BAR_UNAVAILABLE),
        # 上流が待機中なので「まだ出ていない」（D05 §6.8 の待機の伝播）。
        ("right", MissingInputReason.INPUT_MISSING_OR_INVALID),
    ]
    trigger = run.only(T, "entry_trigger")
    assert isinstance(trigger.outcome, Waiting)
    assert [(d.input_name, d.reason) for d in trigger.outcome.diagnoses] == [
        ("level", MissingInputReason.LATEST_BAR_UNAVAILABLE)
    ]
    # 待機記録は判断履歴に出ないが、固定した問いは状態の中に残る（T02 §7.1 の表）。
    waiting = {item.request.instance_id: item for item in run.evaluator.state.waiting}
    assert waiting["daily_ema"].pinned_bars == {"prices": JAN7}
    assert waiting["entry_trigger"].pinned_bars == {"price": HOUR_2100, "level": JAN7}
    assert waiting["market_state"].pinned_bars == {}

    assert [(kind, phase) for _, kind, phase, _ in _kinds(run.events(T))] == [
        ("WAIT_STARTED", "P1_FEATURE"),
        ("WAIT_STARTED", "P1_FEATURE"),
        ("WAIT_STARTED", "P2_MARKET_STATE"),
        ("WAIT_STARTED", "P3_TRIGGER"),
    ]
    # 入力を持たない `no_short` は待機しない。観測した足は起動した日足で、鮮度は判断時刻。
    no_short = run.results[T].outputs
    constant = next(item for item in no_short if item.producer.instance_id == "no_short")
    assert constant.payload == Observation(
        value=ConditionState(False),
        subject=JAN7,
        observation_interval=JAN7_INTERVAL,
        freshness_time=T,
    )


def test_route_5_the_waiting_requests_resume_in_evaluation_order_at_the_arrival() -> None:
    """T02 §7.2: 日足の到着した判断時点で、元の要求 ID のまま連鎖が評価順に再開する。"""
    run = _Run(FixedSeriesDelay(DAILY_SERIES, timedelta(seconds=2))).until(T_PLUS_2S)
    started = {record.instance_id: record.request_id for record in run.results[T].evaluations}

    for instance_id in (*DAILY_CHAIN, "entry_trigger"):
        record = run.only(T_PLUS_2S, instance_id)
        assert isinstance(record.outcome, Evaluated), record
        assert record.request_id == started[instance_id]
        assert record.decision_time == T_PLUS_2S
        assert record.target_interval == run.only(T, instance_id).target_interval

    assert _kinds(run.events(T_PLUS_2S)) == [
        (str(started["daily_ema"]), "INPUT_ARRIVED", "OPPORTUNITY_LIFECYCLE", ("prices",)),
        (str(started["daily_above_ema"]), "INPUT_ARRIVED", "OPPORTUNITY_LIFECYCLE", ("left",)),
        (str(started["entry_trigger"]), "INPUT_ARRIVED", "OPPORTUNITY_LIFECYCLE", ("level",)),
        (str(started["daily_ema"]), "RESUMED", "P1_FEATURE", ()),
        (str(started["daily_above_ema"]), "INPUT_ARRIVED", "P1_FEATURE", ("right",)),
        (str(started["daily_above_ema"]), "RESUMED", "P1_FEATURE", ()),
        (str(started["market_state"]), "INPUT_ARRIVED", "P2_MARKET_STATE", ("long_allowed",)),
        (str(started["market_state"]), "RESUMED", "P2_MARKET_STATE", ()),
        (str(started["entry_trigger"]), "RESUMED", "P3_TRIGGER", ()),
    ]

    payloads = {
        record.producer.instance_id: record.payload for record in run.results[T_PLUS_2S].outputs
    }
    # 判断時刻は 22:00:02、鮮度は観測した日足の終了時刻 22:00（上位設計書 §4.3.15 の例）。
    ema = payloads["daily_ema"]
    assert isinstance(ema, Observation)
    assert ema.value == t02_market.EXPECTED.daily_ema_jan7
    assert (ema.subject, ema.observation_interval, ema.freshness_time) == (
        JAN7,
        JAN7_INTERVAL,
        T,
    )
    above = payloads["daily_above_ema"]
    assert isinstance(above, Observation) and above.value == ConditionState(False)
    permission = payloads["market_state"]
    assert isinstance(permission, Observation)
    assert permission.value == MarketPermission(allow_long=False, allow_short=False)
    assert permission.freshness_time == T
    # 日足境界では突破が構造的に成立しない（Q23 決定）。取引機会は生まれない。
    trigger = run.only(T_PLUS_2S, "entry_trigger").outcome
    assert trigger == Evaluated(output_ids=())
    assert run.evaluator.state.waiting == ()


# --- 経路6（T02 §8）----------------------------------------------------------------


def test_route_6_the_deadline_settles_the_daily_chain_as_skipped() -> None:
    """T02 §8: 日足1本ぶんの期限に到達すると、期限到達の出来事と見送りで決着する。"""
    run = _Run(FixedSeriesDelay(DAILY_SERIES, timedelta(hours=25))).until(NEXT_DAY)
    started = {record.instance_id: record.request_id for record in run.results[T].evaluations}

    reached = [
        event.request_id
        for event in run.events(NEXT_DAY)
        if event.kind is WaitEventKind.DEADLINE_REACHED
    ]
    for instance_id in DAILY_CHAIN:
        assert started[instance_id] in reached
        settled = [
            record
            for record in run.evaluations(NEXT_DAY, instance_id)
            if record.request_id == started[instance_id]
        ]
        assert len(settled) == 1
        assert isinstance(settled[0].outcome, Skipped)
    deadline_events = [
        event for event in run.events(NEXT_DAY) if event.kind is WaitEventKind.DEADLINE_REACHED
    ]
    assert all(event.at.phase.name == "OPPORTUNITY_LIFECYCLE" for event in deadline_events)
    # 日足の待機要求は対象系列が日足なので、1時間足の公開では追い越されない。
    assert not any(
        isinstance(record.outcome, Superseded) and record.instance_id in DAILY_CHAIN
        for result in run.results.values()
        for record in result.evaluations
    )
    # 同じ判断時点は新しい日足の予定境界でもあるので、日足の連鎖は新しい要求でまた待機する。
    for instance_id in DAILY_CHAIN:
        fresh = [
            record
            for record in run.evaluations(NEXT_DAY, instance_id)
            if record.request_id != started[instance_id]
        ]
        assert [type(record.outcome) for record in fresh] == [Waiting]
    assert all(not result.proposals for result in run.results.values())


def test_route_6_an_older_late_daily_bar_is_an_arrival_but_does_not_resume() -> None:
    """系列全体を25時間遅らせると、前日の日足 D(Jan6) が 23:00 に公開される。

    到着の判定は「その入力が指す系列の足が公開されたこと」（D05 §6.8 の手順1）なので、
    日足を読む入力には到着の出来事が残る。固定した足 D(Jan7) はまだ読めないので再開はせず、
    待機を続ける（部分的に届いた入力で評価を始めない）。T02 §8 の表はこの判断時点を省いて
    いるが、決着（翌日の期限到達と見送り）は変わらない。
    """
    run = _Run(FixedSeriesDelay(DAILY_SERIES, timedelta(hours=25))).until(NEXT_HOUR)
    started = run.only(T, "daily_ema").request_id
    kinds = [event.kind for event in run.events(NEXT_HOUR) if event.request_id == started]
    assert kinds == [WaitEventKind.INPUT_ARRIVED]
    assert run.evaluations(NEXT_HOUR, "daily_ema") == []
    assert started in {item.request.request_id for item in run.evaluator.state.waiting}


# --- 経路7（T02 §9）----------------------------------------------------------------


def test_route_7_the_hourly_waiting_request_is_superseded_by_the_next_hour() -> None:
    """T02 §9: 日足 D(Jan7) だけが25時間遅れると、1時間足の待機要求が次の足に追い越される。"""
    delay = InjectedBarDelay(DAILY_SERIES, bar_start=JAN7.bar_start, delay=timedelta(hours=25))
    run = _Run(delay).until(NEXT_HOUR)
    old = run.only(T, "entry_trigger")
    assert isinstance(old.outcome, Waiting)

    records = run.evaluations(NEXT_HOUR, "entry_trigger")
    assert len(records) == 2
    closed, fresh = records
    assert closed.request_id == old.request_id
    assert closed.target_interval == Interval(start=HOUR_2100.bar_start, end=T)
    assert closed.outcome == Superseded(by_request_id=fresh.request_id)
    assert fresh.target_interval == Interval(start=T, end=NEXT_HOUR)
    # 日足はまだ届いていないので、新しい足の要求もまた待機に入る。
    assert isinstance(fresh.outcome, Waiting)

    superseded = [
        event for event in run.events(NEXT_HOUR) if event.kind is WaitEventKind.SUPERSEDED
    ]
    assert len(superseded) == 1
    assert superseded[0].request_id == old.request_id
    assert superseded[0].at.phase.name == "OPPORTUNITY_LIFECYCLE"
    assert superseded[0].reason is not None
    assert superseded[0].reason.code is ReasonCode.REQUEST_SUPERSEDED
    # 日足の連鎖は対象系列が日足なので追い越されない。
    assert {item.request.instance_id for item in run.evaluator.state.waiting} == {
        *DAILY_CHAIN,
        "entry_trigger",
    }


# --- 引き渡し #3 の3規則（T02 §19）--------------------------------------------------


def test_rule_a_skip_wins_over_wait_when_both_inputs_of_the_trigger_are_missing() -> None:
    """Q27: `breakout_trigger` v2 は `level` が待機、`price` が見送り。両方欠ければ見送る。"""
    run = _Run(
        FixedSeriesDelay(DAILY_SERIES, timedelta(seconds=2)),
        FixedSeriesDelay(HOURLY_SERIES, timedelta(seconds=2)),
    ).until(T)

    record = run.only(T, "entry_trigger")
    assert isinstance(record.outcome, Skipped)
    assert {d.input_name for d in record.outcome.diagnoses} == {"price", "level"}
    assert "entry_trigger" not in {item.request.instance_id for item in run.evaluator.state.waiting}


def test_rule_b_an_output_only_wait_counts_its_deadline_on_the_trigger_series() -> None:
    """Q28: 足りない入力が出力参照だけの `market_state` は、起動条件の日足で期限を数える。"""
    run = _Run(FixedSeriesDelay(DAILY_SERIES, timedelta(seconds=2))).until(T)

    record = run.only(T, "market_state")
    assert isinstance(record.outcome, Waiting)
    assert [d.input_name for d in record.outcome.diagnoses] == ["long_allowed"]
    assert record.outcome.deadline_at == WaitUntilBars(series=DAILY_SERIES, remaining=1)


def test_rule_c_the_deadline_wins_when_it_coincides_with_a_supersession() -> None:
    """Q29: 期限到達と追い越しが同じ判断時点で成立したら、期限が勝つ（T02 §8 の末尾）。

    D(Jan7) だけを遅らせると、翌日の予定境界で期限（日足1本）に到達し、同時に新しい日足
    D(Jan8) が公開されて追い越しも成立する。記録は期限到達と見送りで、追い越しは残らない。
    """
    delay = InjectedBarDelay(DAILY_SERIES, bar_start=JAN7.bar_start, delay=timedelta(hours=25))
    run = _Run(delay).until(NEXT_DAY)
    started = run.only(T, "daily_ema").request_id
    newer = BarKey(series=DAILY_SERIES, bar_start=T)
    assert newer in _published(run, NEXT_DAY)

    events = [event for event in run.events(NEXT_DAY) if event.request_id == started]
    assert [event.kind for event in events] == [WaitEventKind.DEADLINE_REACHED]
    settled = [
        record for record in run.evaluations(NEXT_DAY, "daily_ema") if record.request_id == started
    ]
    assert len(settled) == 1
    assert isinstance(settled[0].outcome, Skipped)


def _published(run: _Run, at: UtcTime) -> list[BarKey]:
    """その判断時点で公開された足（`PublicationBatch.available_bars`）。"""
    for batch in batches(run.bars, at, at):
        return list(batch.available_bars)
    return []
