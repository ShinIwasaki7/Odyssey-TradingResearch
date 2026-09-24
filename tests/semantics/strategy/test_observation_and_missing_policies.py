"""観測・鮮度・履歴窓・欠損方針の意味論（D05 §6.3・§6.7・§6.9・§6.12・§6.1）。

検証戦略 B の宣言では通らない仕組み（D05 §9.4 の末尾の表）を、検証戦略 A に小さな使用箇所
を足した宣言で確かめる。部品カタログにない読み取り条件は、カタログの実装をそのまま使い、
契約の版と読み取り条件だけを変えて登録する（D05 §4.1 の登録の単位）。

- 上流の出力を履歴窓で読む: 保持本数の計画どおりに保持・打ち切り、対象区間の終了時刻を
  基準に窓を切る（§6.12、Q18・Q20・Q21 決定）。
- 鮮度: 上流の出力の鮮度はその出力が観測した足の終了時刻であり、`max_age` がそれに効く
  （§6.7、Q13 決定）。
- 観測区間の一致: 入力をまたいだ同じ位置の要素の観測区間が違えば失敗する（§6.7）。
- 遡り: 許した欠損理由で期待足が欠ければ1本手前の有効な足を使い、評価記録に残す（§6.9）。
- 欠損方針の強さ順: `Error` > `SkipEvaluation` > `WaitForInput` > `UsePrevious`（§6.3、Q27）。
- run 末尾: 残った待機要求は見送りで決着し、`RUN_END_CLOSED` を残す（§6.1）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from odyssey_fx.common.ids import EventId, IdAllocator, RunId
from odyssey_fx.common.reason import MissingInputReason, ReasonCode
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.schedule import DelayScenario, InjectedBarDelay
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.catalog.conditions import compare
from odyssey_fx.strategy.catalog.features import atr, extreme
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    ComponentRegistry,
    StatelessImplementation,
    build_registry,
)
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy, CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline
from odyssey_fx.strategy.declarations.evaluation import EvaluationSchedule, OnBarClose
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.missing import (
    Error,
    MissingInputPolicy,
    OnSuperseded,
    SkipEvaluation,
    UsePrevious,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.read_spec import BarsWindow, LatestAvailable
from odyssey_fx.strategy.declarations.refs import MarketDataField, MarketDataRef, OutputRef
from odyssey_fx.strategy.declarations.specs import InputBinding, IntValue, StrValue
from odyssey_fx.strategy.records.payloads import ConditionState
from odyssey_fx.strategy.records.records import Observation
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from odyssey_fx.strategy.runtime.ports import PublicationBatch
from odyssey_fx.strategy.runtime.requests import (
    Evaluated,
    EvaluationRecord,
    Failed,
    ResolvedInputs,
    RuntimeStepResult,
    Skipped,
    ValueSample,
    Waiting,
)
from odyssey_fx.strategy.runtime.waiting import WaitEventKind
from tests.fixtures.strategy.fakes import CollectingSink, FakeContextView
from tests.fixtures.strategy.phases import BACKTEST_PHASES
from tests.fixtures.strategy.runtime_harness import asof_view, batches
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES, TIMEFRAMES, strategy_a
from tests.fixtures.synthetic import market

M15_SERIES = market.series(timeframe_id="15m")
DATA = Interval(
    start=UtcTime.parse("2026-01-04T22:00:00Z"), end=UtcTime.parse("2026-01-07T00:00:00Z")
)
#: 検証戦略 A の突破水準（20本）がそろい、その出力が4本以上たまる判断時点から始める。
FIRST = UtcTime.parse("2026-01-05T19:00:00Z")
LAST = UtcTime.parse("2026-01-06T06:00:00Z")
#: 遅らせる1時間足（遡り・待機・強さ順の検査に使う）。
LATE_START = UtcTime.parse("2026-01-06T05:00:00Z")
LATE_CLOSE = UtcTime.parse("2026-01-06T06:00:00Z")

_WAIT = WaitForInput(
    deadline=BarsDeadline(bars=1),
    on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
    on_superseded=OnSuperseded.EXPIRE_REQUEST,
)
_PREVIOUS = UsePrevious(
    max_lookback=BarsWindow(3), allowed_reasons=(MissingInputReason.LATEST_BAR_UNAVAILABLE,)
)


def _compare_contract(
    version: int,
    left: MissingInputPolicy,
    right: MissingInputPolicy,
) -> ComponentRegistration:
    """`price_compare` の実装に、別の読み取り条件を持たせた契約の版を登録する。"""
    base = compare.CONTRACT
    contract: ComponentContract = replace(
        base,
        version=version,
        inputs={
            "left": replace(
                base.inputs["left"], read_spec=LatestAvailable(max_age=None, on_missing=left)
            ),
            "right": replace(
                base.inputs["right"],
                read_spec=LatestAvailable(max_age=None, on_missing=right),
            ),
        },
    )
    return replace(compare.REGISTRATION, contract=contract)


PREVIOUS_BOTH = _compare_contract(90, _PREVIOUS, _PREVIOUS)
ERROR_AND_SKIP = _compare_contract(91, Error(), SkipEvaluation())
WAIT_AND_PREVIOUS = _compare_contract(92, _WAIT, _PREVIOUS)
FRESH_READER = _compare_contract(93, SkipEvaluation(), SkipEvaluation())

REGISTRY: ComponentRegistry = build_registry(
    (
        *INITIAL_CATALOG.registrations.values(),
        PREVIOUS_BOTH,
        ERROR_AND_SKIP,
        WAIT_AND_PREVIOUS,
        FRESH_READER,
    )
)


def _on_hour() -> EvaluationSchedule:
    return EvaluationSchedule(triggers=(OnBarClose("h1", SIGNAL_SERIES),))


def _compare(
    instance_id: str, registration: ComponentRegistration, left: object, right: object
) -> ComponentInstance:
    return ComponentInstance(
        instance_id=instance_id,
        contract_ref=contract_ref_for(registration.contract),
        inputs={
            "left": InputBinding(sources=(left,)),  # type: ignore[arg-type]
            "right": InputBinding(sources=(right,)),  # type: ignore[arg-type]
        },
        parameters={"operator": StrValue("GT")},
        evaluation=_on_hour(),
    )


def _close(series: SeriesId = SIGNAL_SERIES) -> MarketDataRef:
    return MarketDataRef(series, MarketDataField.CLOSE)


def _open() -> MarketDataRef:
    return MarketDataRef(SIGNAL_SERIES, MarketDataField.OPEN)


def _bars(*, late: bool = False) -> dict[SeriesId, tuple[Bar, ...]]:
    calendar = market.calendar()
    hourly = market.make_bars(SIGNAL_SERIES, market.TF_1H, calendar, DATA)
    if late:
        scenario = DelayScenario(
            id="late",
            version=1,
            rules=(
                InjectedBarDelay(SIGNAL_SERIES, bar_start=LATE_START, delay=timedelta(hours=3)),
            ),
        )
        hourly = market.apply_delay(hourly, scenario)
    quarter = market.make_bars(M15_SERIES, market.TF_15M, calendar, DATA)
    return {SIGNAL_SERIES: hourly, M15_SERIES: quarter}


def _compiled(*extra: ComponentInstance) -> CompiledStrategy:
    definition = strategy_a()
    definition = replace(definition, components=(*definition.components, *extra))
    result = compile_strategy(definition, REGISTRY, TIMEFRAMES)
    assert isinstance(result, CompileSucceeded), result
    return result.compiled


class _Run:
    def __init__(
        self,
        *extra: ComponentInstance,
        late: bool = False,
        registry: ComponentRegistry = REGISTRY,
    ) -> None:
        self.bars = _bars(late=late)
        self.compiled = _compiled(*extra)
        self.evaluator = StrategyEvaluator(
            compiled=self.compiled,
            registry=registry,
            market_data=asof_view(self.bars),
            context=FakeContextView(),
            sink=CollectingSink(),
            allocator=IdAllocator(RunId(ContentDigest.sha256("e" * 64))),
        )
        self.results: dict[UtcTime, RuntimeStepResult] = {}
        self.next_id = 1

    def until(self, end: UtcTime, start: UtcTime = FIRST) -> _Run:
        for batch in batches(self.bars, start, end, first_batch_id=self.next_id):
            self.next_id = batch.batch_id.seq + 1
            self.results[batch.decision_time] = self.evaluator.step(batch)
        return self

    def record(self, at: UtcTime, instance_id: str) -> EvaluationRecord:
        found = [r for r in self.results[at].evaluations if r.instance_id == instance_id]
        assert len(found) == 1, found
        return found[0]


# --- 上流の出力を履歴窓で読む（D05 §6.12）--------------------------------------------


def _floor_of_levels() -> ComponentInstance:
    """突破水準（`breakout_level.level`）の直近3本の最小を、当該足を除いて読む使用箇所。"""
    return ComponentInstance(
        instance_id="level_floor",
        contract_ref=contract_ref_for(extreme.CONTRACT),
        inputs={"prices": InputBinding(sources=(OutputRef("breakout_level", "level"),))},
        parameters={"lookback": IntValue(3), "mode": StrValue("MIN")},
        evaluation=_on_hour(),
    )


def test_the_retention_plan_bounds_the_output_history() -> None:
    """Q18: 保持本数は読み手の窓から導いた `n + k`（3 + 当該足を除く1）に限られる。"""
    run = _Run(_floor_of_levels())
    level = OutputRef("breakout_level", "level")
    assert run.compiled.output_retention.by_output[level] == 4

    run.until(LAST)

    rows = run.evaluator.state.output_history[level]
    assert len(rows) == 4
    starts = [row.subject.bar_start.value for row in rows if row.subject is not None]
    assert starts == sorted(starts)
    # 最新1件の読み方しか読み手が無い出力参照は、保持本数 1（T02 §14 #14）。
    assert len(run.evaluator.state.output_history[OutputRef("stop_level", "level")]) == 1
    # 繰り返し参照する値でない出力（取引機会のイベント）は保持しない（D05 §6.5）。
    assert OutputRef("entry_trigger", "opportunity") not in run.evaluator.state.output_history


def test_an_output_window_ends_before_the_target_bar_and_propagates_freshness() -> None:
    """Q21: 対象区間の終了時刻以下の観測から当該足を除く指定を当てて末尾3本を読む。"""
    run = _Run(_floor_of_levels()).until(LAST)
    levels = {
        record.payload.subject.bar_start.value: record.payload
        for result in run.results.values()
        for record in result.outputs
        if record.producer == OutputRef("breakout_level", "level")
        and isinstance(record.payload, Observation)
    }
    floor = next(
        record.payload
        for record in run.results[LAST].outputs
        if record.producer == OutputRef("level_floor", "level")
    )
    assert isinstance(floor, Observation)
    ordered = [levels[key] for key in sorted(levels)]
    # 末尾（当該足）を除いた3本。
    window = ordered[-4:-1]
    assert floor.value == min(item.value for item in window)
    # 窓の代表の鮮度は末尾の要素の鮮度（D05 §6.7）。
    assert floor.freshness_time == window[-1].freshness_time
    record = run.record(LAST, "level_floor")
    assert isinstance(record.outcome, Evaluated)


# --- 鮮度（D05 §6.7）----------------------------------------------------------------


def test_an_upstream_output_is_read_with_the_freshness_of_the_bar_it_observed() -> None:
    """Q13: 上流の出力の鮮度は判断時刻ではなく、観測した足の終了時刻である。

    突破水準は当該足を除く窓なので、観測の鮮度は判断時刻の1時間前になる。段階2 の規則
    （鮮度＝その出力を生んだ評価の判断時刻）なら判断時刻そのものだった。`max_age` の判定は
    この値に対して行う（D05 §6.3）。部品には `Observation` ではなく内容だけが渡る。
    """
    seen: list[object] = []

    def spy(inputs: ResolvedInputs, parameters: object) -> ComponentOutputs:
        seen.extend(inputs.by_name["right"])
        return compare.evaluate(inputs, parameters)  # type: ignore[arg-type]

    registration = replace(
        FRESH_READER,
        implementation=StatelessImplementation(evaluate=spy),  # type: ignore[arg-type]
    )
    registry = build_registry(
        (*(item for item in REGISTRY.registrations.values() if item != FRESH_READER), registration)
    )
    run = _Run(
        _compare("reader", FRESH_READER, _close(), OutputRef("breakout_level", "level")),
        registry=registry,
    ).until(FIRST)

    assert isinstance(run.record(FIRST, "reader").outcome, Evaluated)
    (sample,) = seen
    assert isinstance(sample, ValueSample)
    upstream = next(
        record
        for record in run.results[FIRST].outputs
        if record.producer == OutputRef("breakout_level", "level")
    )
    assert isinstance(upstream.payload, Observation)
    assert sample.payload == upstream.payload.value
    assert sample.freshness_time == FIRST - timedelta(hours=1)
    assert sample.freshness_time == upstream.payload.freshness_time
    assert sample.observation_interval == upstream.payload.observation_interval
    assert sample.source_output_id == upstream.output_id


# --- 観測区間の一致（D05 §6.7）------------------------------------------------------


def _atr(instance_id: str, low_series: SeriesId) -> ComponentInstance:
    return ComponentInstance(
        instance_id=instance_id,
        contract_ref=contract_ref_for(atr.CONTRACT),
        inputs={
            "highs": InputBinding(sources=(MarketDataRef(SIGNAL_SERIES, MarketDataField.HIGH),)),
            "lows": InputBinding(sources=(MarketDataRef(low_series, MarketDataField.LOW),)),
            "closes": InputBinding(sources=(_close(),)),
        },
        parameters={"period": IntValue(3), "window_bars": IntValue(6)},
        evaluation=_on_hour(),
    )


def test_aligned_windows_are_evaluated() -> None:
    """3本の窓の同じ位置が同じ足を見ていれば評価する（窓の中で区間が違うのは正常）。"""
    run = _Run(_atr("range", SIGNAL_SERIES)).until(FIRST)
    assert isinstance(run.record(FIRST, "range").outcome, Evaluated)


def test_misaligned_windows_fail_instead_of_being_treated_as_missing() -> None:
    """D05 §6.7: 同じ位置の観測区間が違えば、欠損ではなく宣言と接続の食い違いとして失敗する。"""
    run = _Run(_atr("range", M15_SERIES)).until(FIRST)
    outcome = run.record(FIRST, "range").outcome
    assert isinstance(outcome, Failed)
    assert outcome.reason.code is ReasonCode.DATA_ERROR


# --- 遡り（D05 §6.9）----------------------------------------------------------------


def test_use_previous_reads_the_bar_before_the_missing_one_and_records_it() -> None:
    """期待足が遅れたら1本手前の有効な足を使い、実際に読んだ足を評価記録に残す。"""
    run = _Run(_compare("fallback", PREVIOUS_BOTH, _close(), _open()), late=True)
    run.until(LATE_CLOSE)

    record = run.record(LATE_CLOSE, "fallback")
    assert isinstance(record.outcome, Evaluated)
    previous = [bar for bar in run.bars[SIGNAL_SERIES] if bar.bar_end == LATE_START][0]
    assert [
        (item.input_name, item.source_index, item.used_bar_key, item.reason)
        for item in record.substitutions
    ] == [
        ("left", 0, previous.key, MissingInputReason.LATEST_BAR_UNAVAILABLE),
        ("right", 0, previous.key, MissingInputReason.LATEST_BAR_UNAVAILABLE),
    ]
    assert all(item.freshness_time == LATE_START for item in record.substitutions)
    output = next(
        item.payload
        for item in run.results[LATE_CLOSE].outputs
        if item.producer.instance_id == "fallback"
    )
    # 遡って読んだ値の観測は、実際に読んだ古い足のもの（D05 §6.9 の末尾）。
    assert isinstance(output, Observation)
    assert output.subject == previous.key
    assert output.observation_interval == previous.interval
    assert output.value == ConditionState(previous.close > previous.open)


# --- 欠損方針の強さ順（D05 §6.3、Q27 決定）------------------------------------------


def test_error_wins_over_skip_when_both_inputs_are_missing() -> None:
    """`Error` > `SkipEvaluation`: 1つでも失敗の方針の入力が欠ければ評価は失敗する。"""
    run = _Run(_compare("strict", ERROR_AND_SKIP, _close(), _open()), late=True)
    run.until(LATE_CLOSE)
    outcome = run.record(LATE_CLOSE, "strict").outcome
    assert isinstance(outcome, Failed)
    assert outcome.reason.code is ReasonCode.DATA_ERROR


def test_wait_wins_over_use_previous() -> None:
    """`WaitForInput` > `UsePrevious`: 待てば読める入力があるなら遡らずに待つ。"""
    run = _Run(_compare("patient", WAIT_AND_PREVIOUS, _close(), _open()), late=True)
    run.until(LATE_CLOSE)
    record = run.record(LATE_CLOSE, "patient")
    assert isinstance(record.outcome, Waiting)
    assert record.substitutions == ()
    assert {d.input_name for d in record.outcome.diagnoses} == {"left", "right"}


# --- run 末尾（D05 §6.1）------------------------------------------------------------


def test_the_run_end_closes_waiting_requests_as_skipped() -> None:
    """末尾に残った待機要求は、期限に達していなくても見送りで決着し、出来事を1件残す。"""
    run = _Run(_compare("patient", WAIT_AND_PREVIOUS, _close(), _open()), late=True)
    run.until(LATE_CLOSE)
    waiting = run.record(LATE_CLOSE, "patient")

    result = run.evaluator.step(
        PublicationBatch(
            batch_id=EventId(run.next_id),
            decision_time=LATE_CLOSE + timedelta(minutes=1),
            phases=BACKTEST_PHASES,
            is_run_end=True,
        )
    )

    assert [record.request_id for record in result.evaluations] == [waiting.request_id]
    closed = result.evaluations[0]
    assert isinstance(closed.outcome, Skipped)
    assert [(event.request_id, event.kind) for event in result.wait_events] == [
        (waiting.request_id, WaitEventKind.RUN_END_CLOSED)
    ]
    assert result.wait_events[0].at.phase.name == "RUN_END"
    assert run.evaluator.state.waiting == ()
    assert result.outputs == ()
