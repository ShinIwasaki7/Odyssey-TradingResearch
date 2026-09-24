"""段階3 でエンジンに足した規則の意味論テスト（D06 §4.2 の手順5・6・停止判定、§8.3、§9.2）。

検証戦略 B の run（`tests/acceptance/test_stage3_completion.py`）は正常な経路しか通らない。
ここでは戦略ランタイムを**台本どおりの戻り値を返す相手役**に差し替え、エンジン側の規則を
1つずつ確かめる。

| 規則 | 正本 |
|---|---|
| 第1回の `step` の損切り更新は受付（rank 10）の直前に適用する | D06 §4.2 の手順6・§8.3 |
| 丸め・参照価格との検査・不利な更新の拒否・`effective_from` | D06 §8.3 の適用意味論の表 |
| 同じ判断時点の決済が勝つ（`SUPERSEDED_BY_EXIT`）、run 末尾は `RUN_END` | D06 §8.3・§10.1 |
| 再検査が「読めず失敗した」なら受付より前で run を止める | D06 §4.2 の停止判定 (b) |
| 遷移・待機の出来事・再検査の処理点を、ランタイムの順を保って振り直す | D06 §4.4、D05 §6.6 |
| 確認試行は主キーで置き換え、遡った入力は評価の識別子と組み合わせて書く | D06 §9.2 の表17・表18 |
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.domain.positions import Position, PositionStatus, ProtectionState
from odyssey_fx.backtest.engine.clock import PhaseClock
from odyssey_fx.backtest.engine.loop import BacktestEngine, EngineContext, TraceOutputSink
from odyssey_fx.backtest.engine.phases import BACKTEST_PHASES
from odyssey_fx.backtest.trace.manifest import DataCapabilityReport
from odyssey_fx.backtest.trace.recorder import CompositeRow, ManagementApplication, TraceTable
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.ids import (
    EvaluationId,
    EventId,
    FillId,
    IdAllocator,
    OpportunityId,
    OutputId,
    PositionId,
    RequestId,
)
from odyssey_fx.common.money import Price, Quantity, decimal_from_str
from odyssey_fx.common.reason import MissingInputReason, Reason, ReasonCode
from odyssey_fx.common.time import ProcessingPoint
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.strategy.compiler.compiled import ResolvedMarketSource
from odyssey_fx.strategy.declarations.opportunity import ValidityMode
from odyssey_fx.strategy.declarations.refs import MarketDataField, OutputRef
from odyssey_fx.strategy.records.payloads import ClosePosition, UpdateStop
from odyssey_fx.strategy.runtime.confirmation import (
    ConfirmationAttempt,
    ConfirmationAttemptOutcome,
)
from odyssey_fx.strategy.runtime.opportunities import (
    OpportunityState,
    OpportunityTransition,
    ValidityRecheck,
    ValidityRecheckOutcome,
)
from odyssey_fx.strategy.runtime.ports import PublicationBatch
from odyssey_fx.strategy.runtime.requests import (
    Evaluated,
    EvaluationRecord,
    ManagementRequest,
    RuntimeStepResult,
    SubstitutedInput,
)
from odyssey_fx.strategy.runtime.waiting import WaitEvent, WaitEventKind
from tests.fixtures.backtest.harness import (
    ACCOUNT,
    CONVERSION_POLICY,
    COST_MODEL,
    EXECUTION_POLICY,
    EXECUTION_SERIES,
    RISK_POLICY,
    SYMBOL_SPEC,
    FakeExecutionSeries,
    FakeFeed,
    bars,
    compiled_strategy,
)
from tests.fixtures.backtest.paths import DECISION_TIME, RUN_INTERVAL, execution_bars
from tests.fixtures.synthetic.market import USDJPY, calendar
from tests.semantics.backtest.test_engine_semantics import _config, _run_id

_P1 = PositionId(1)
_ENTRY = Price(decimal_from_str("150.080"))
_STOP = Price(decimal_from_str("149.500"))
#: 直前に完了した執行足の終値（bid）。損切りの検査はこれと比べる（D06 §8.3）。
_LAST_CLOSE = "150.700"


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


class _Scripted:
    """台本どおりの戻り値を順に返す戦略ランタイム。尽きたら空の結果を返す。"""

    def __init__(self, *results: RuntimeStepResult) -> None:
        self._results: Iterator[RuntimeStepResult] = iter(results)

    def step(self, batch: PublicationBatch) -> RuntimeStepResult:
        del batch
        return next(self._results, RuntimeStepResult())


def _engine(runtime: object, *, with_position: bool = True) -> BacktestEngine:
    """建玉 P1（買い、損切り 149.500）を持つ台帳のエンジン（run は実行しない）。"""
    compiled = compiled_strategy()
    context = EngineContext(ACCOUNT)
    if with_position:
        position = Position(
            position_id=_P1,
            account_id=ACCOUNT.account_id,
            strategy_id="strategy_a",
            symbol=USDJPY,
            side=OrderSide.BUY,
            quantity=Quantity(Decimal(32000)),
            entry_fill_id=FillId(1),
            entry_price=_ENTRY,
            opened_at=ProcessingPoint(
                time=DECISION_TIME,
                phase=BACKTEST_PHASES.by_name("EXECUTION_OPEN"),
                sequence=0,
            ),
            protection=ProtectionState(
                version=1,
                stop_loss=_STOP,
                effective_from=BarKey(series=EXECUTION_SERIES, bar_start=DECISION_TIME),
                owner_instance_id="take_profit",
            ),
            status=PositionStatus.OPEN,
        )
        context.ledger = context.ledger.committed(positions={_P1: position})
    engine = BacktestEngine(
        config=_config(compiled),
        compiled=compiled,
        runtime=runtime,  # type: ignore[arg-type]
        context=context,
        output_sink=TraceOutputSink(),
        allocator=IdAllocator(_run_id()),
        feed=FakeFeed((), execution_bars()),
        execution_series=FakeExecutionSeries(execution_bars()),
        calendar=calendar(),
        risk_policy=RISK_POLICY,
        execution_policy=EXECUTION_POLICY,
        cost_model=COST_MODEL,
        conversion_policy=CONVERSION_POLICY,
        symbol_spec=SYMBOL_SPEC,
        capability_report=DataCapabilityReport(compiled_match=True, integrity=IntegrityReport()),
    )
    return engine


def _last_complete(engine: BacktestEngine, close: str = _LAST_CLOSE) -> None:
    """rank 0 で処理し終えた最新の執行足を置く（参照価格の出どころ。D06 §8.3）。"""
    (bar,) = bars(
        EXECUTION_SERIES,
        DECISION_TIME - (execution_bars()[0].interval.duration),
        execution_bars()[0].interval.duration,
        (("150.600", "150.900", "150.500", close),),
    )
    engine._last_complete = bar


def _update(stop: str, *, source: int = 7) -> ManagementRequest:
    return ManagementRequest(
        position_id=_P1,
        action=UpdateStop(stop_loss=_price(stop)),
        decision_time=DECISION_TIME,
        source_output_id=OutputId(source),
    )


def _apply(
    engine: BacktestEngine, *requests: ManagementRequest, is_run_end: bool = False
) -> list[ManagementApplication]:
    clock = PhaseClock(engine._phases, DECISION_TIME)
    engine._apply_protection_updates(clock, requests, phase="ADMISSION", is_run_end=is_run_end)
    applications = []
    for row in engine.rows[TraceTable.MANAGEMENT_APPLICATIONS]:
        assert isinstance(row, CompositeRow)
        application = row.parts[0][2]
        assert isinstance(application, ManagementApplication)
        applications.append(application)
    return applications


def _position(engine: BacktestEngine) -> Position:
    return engine.ledger.positions[_P1]


# --- 損切り水準の更新の適用（D06 §8.3） ---------------------------------------------


def test_a_stop_update_is_applied_at_admission_rounded_down_from_the_next_bar() -> None:
    """買いの損切りは切り下げて丸め、受付の処理点で適用し、次の執行足から有効にする。

    `149.9005` は刻み 0.001 へ切り下げて `149.900`。判断時刻ちょうどに始まる執行足が
    「まだ到来していない最初の執行足」＝次の執行足である（D06 §8.3、上位設計書 §4.7.7）。
    既存の利確は触らない。
    """
    engine = _engine(_Scripted())
    _last_complete(engine)
    (application,) = _apply(engine, _update("149.9005"))

    assert application.applied is True
    assert application.at.phase.name == "ADMISSION"
    assert application.rounded_stop_loss == _price("149.900")
    assert application.protection_version == 2
    protection = _position(engine).protection
    assert protection.stop_loss == _price("149.900")
    assert protection.version == 2
    assert protection.effective_from == BarKey(series=EXECUTION_SERIES, bar_start=DECISION_TIME)
    assert protection.take_profit is None
    # 根拠記録（表15）に更新の根拠が1件残る。
    (evidence,) = engine.rows[TraceTable.EVIDENCE]
    assert evidence.kind.value == "PROTECTION_UPDATE"  # type: ignore[attr-defined]
    assert evidence.output_ids == (OutputId(7),)  # type: ignore[attr-defined]


def test_a_stop_update_at_or_above_the_bid_is_not_applied() -> None:
    """買いの損切りは参照価格の bid より下でなければならない（D06 §8.3）。

    bid と同じ水準は不合格。適用しなかった要求は `PROTECTION_INVALID` で表12 に残し、run は
    止めない。建玉の保護水準は変わらない。
    """
    engine = _engine(_Scripted())
    _last_complete(engine)
    (application,) = _apply(engine, _update(_LAST_CLOSE))

    assert application.applied is False
    assert application.reason == Reason(ReasonCode.PROTECTION_INVALID)
    assert _position(engine).protection.stop_loss == _STOP
    assert _position(engine).protection.version == 1


def test_a_stop_update_worse_than_the_current_level_is_not_applied() -> None:
    """丸めた結果が現在の水準より不利になる更新は適用しない（D06 §8.3）。

    部品は有利な向きだけを返す（D05 §4.10）が、丸めで不利側へ動くことがあるので、エンジン
    側でも確かめる。
    """
    engine = _engine(_Scripted())
    _last_complete(engine)
    (application,) = _apply(engine, _update("149.4999"))

    assert application.applied is False
    assert application.reason == Reason(ReasonCode.PROTECTION_INVALID)
    assert _position(engine).protection.stop_loss == _STOP


def test_a_close_request_supersedes_a_stop_update_from_the_first_step() -> None:
    """同じ判断時点の決済要求が勝ち、更新は `SUPERSEDED_BY_EXIT` で適用しない（D06 §8.3）。

    競合は適用より前に解決する（D06 §4.2 の手順6）。第1回の `step` でも第2回と同じ規則である。
    """
    engine = _engine(_Scripted())
    _last_complete(engine)
    close = ManagementRequest(
        position_id=_P1,
        action=ClosePosition(),
        decision_time=DECISION_TIME,
        source_output_id=OutputId(8),
    )
    (application,) = _apply(engine, _update("149.900"), close)

    assert application.applied is False
    assert application.reason == Reason(ReasonCode.SUPERSEDED_BY_EXIT)
    assert application.at.phase.name == "ADMISSION"
    assert _position(engine).protection.stop_loss == _STOP


def test_a_stop_update_at_the_end_of_the_run_is_recorded_but_not_applied() -> None:
    """run 末尾の規則が衝突の規則より優先し、更新は `RUN_END` で適用しない（D06 §8.3・§10.1）。"""
    engine = _engine(_Scripted())
    _last_complete(engine)
    (application,) = _apply(engine, _update("149.900"), is_run_end=True)

    assert application.applied is False
    assert application.reason is not None
    assert application.reason.code is ReasonCode.RUN_END
    assert _position(engine).protection.stop_loss == _STOP


def test_a_stop_update_without_a_completed_execution_bar_is_not_applied() -> None:
    """参照価格を作れない（完了した執行足が1本も無い）判断時点では適用せず `DATA_ERROR`。"""
    engine = _engine(_Scripted())
    (application,) = _apply(engine, _update("149.900"))

    assert application.applied is False
    assert application.reason is not None
    assert application.reason.code is ReasonCode.DATA_ERROR
    assert _position(engine).protection.stop_loss == _STOP


def test_a_stop_update_for_a_closed_position_is_rejected_as_closed() -> None:
    """閉じた（台帳に無い）建玉への要求は `POSITION_CLOSED` で拒否し、run は止めない。"""
    engine = _engine(_Scripted(), with_position=False)
    _last_complete(engine)
    (application,) = _apply(engine, _update("149.900"))

    assert application.applied is False
    assert application.reason is not None
    assert application.reason.code is ReasonCode.POSITION_CLOSED


# --- 停止判定 (b)（D06 §4.2） -----------------------------------------------------------


def _failed_recheck(at: ProcessingPoint) -> ValidityRecheck:
    return ValidityRecheck(
        opportunity_id=OpportunityId(1),
        source=OutputRef("daily_above_ema", "condition"),
        mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST,
        at=at,
        outcome=ValidityRecheckOutcome.MISSING_FAILED,
        reason=Reason(ReasonCode.DATA_ERROR),
    )


class _FailingRecheckRuntime:
    """最初の `step` で「読めず失敗した」再検査を返すランタイム。"""

    def __init__(self) -> None:
        self.calls = 0

    def step(self, batch: PublicationBatch) -> RuntimeStepResult:
        self.calls += 1
        if self.calls > 1:
            return RuntimeStepResult()
        at = ProcessingPoint(
            time=batch.decision_time, phase=batch.phases.by_name("P5_ORDER_INTENT"), sequence=0
        )
        return RuntimeStepResult(validity_rechecks=(_failed_recheck(at),))


def test_a_recheck_that_fails_to_read_stops_the_run_before_admission() -> None:
    """D06 §4.2 の停止判定 (b): `MISSING_FAILED` の再検査があれば、その判断時点で run を止める。

    再検査は評価の外側で走るので評価記録（`Failed`）は作られない。失敗の診断は表19 に残し、
    受付以降のフェーズを1つも実行しない（`DATA_ERROR` の実行失敗）。
    """
    runtime = _FailingRecheckRuntime()
    engine = _engine(runtime, with_position=False)
    engine.execute()

    assert engine.status is RunStatus.FAILED_DATA_ERROR
    assert engine.failure_reason is not None
    assert engine.failure_reason.code is ReasonCode.DATA_ERROR
    (recheck,) = engine.rows[TraceTable.VALIDITY_RECHECKS]
    assert isinstance(recheck, ValidityRecheck)
    assert recheck.outcome is ValidityRecheckOutcome.MISSING_FAILED
    assert engine.rows[TraceTable.ORDER_REQUESTS] == ()
    assert runtime.calls == 1


# --- 段階3 の列の書き出し（D06 §4.2 の手順5、§4.4、§9.2） -----------------------------------


def _at(batch: PublicationBatch, phase: str, sequence: int) -> ProcessingPoint:
    return ProcessingPoint(
        time=batch.decision_time, phase=batch.phases.by_name(phase), sequence=sequence
    )


class _Stage3ColumnsRuntime:
    """段階3 の3列と遡った入力を返すランタイム。

    1回目: 同じフェーズ（`P4_CONFIRMATION`）で 再検査(0) → 遷移(1) → 待機の出来事(2) の順に
    刻み、確認試行を `WAITING` で返す。2回目: 同じ確認試行を `SKIPPED` に書き換えて返す。
    """

    def __init__(self) -> None:
        self.calls = 0

    def step(self, batch: PublicationBatch) -> RuntimeStepResult:
        self.calls += 1
        bar_key = BarKey(series=EXECUTION_SERIES, bar_start=DECISION_TIME)
        if self.calls == 1:
            return RuntimeStepResult(
                evaluations=(
                    EvaluationRecord(
                        request_id=RequestId(1),
                        evaluation_id=EvaluationId(1),
                        instance_id="entry_filter",
                        trigger_names=("m15",),
                        decision_time=batch.decision_time,
                        outcome=Evaluated(()),
                        substitutions=(
                            SubstitutedInput(
                                input_name="prices",
                                source_index=0,
                                source=ResolvedMarketSource(
                                    series=EXECUTION_SERIES, field=MarketDataField.CLOSE
                                ),
                                freshness_time=batch.decision_time,
                                reason=MissingInputReason.LATEST_BAR_UNAVAILABLE,
                                used_bar_key=bar_key,
                            ),
                        ),
                    ),
                ),
                # 返す列の並びとは逆に、処理点の番号は 再検査 → 遷移 → 待機の出来事 の順。
                wait_events=(
                    WaitEvent(
                        request_id=RequestId(1),
                        kind=WaitEventKind.WAIT_STARTED,
                        at=_at(batch, "P4_CONFIRMATION", 2),
                    ),
                ),
                transitions=(
                    OpportunityTransition(
                        opportunity_id=OpportunityId(1),
                        from_state=OpportunityState.OPEN,
                        to_state=OpportunityState.CONFIRMED,
                        at=_at(batch, "P4_CONFIRMATION", 1),
                        phase=batch.phases.by_name("P4_CONFIRMATION"),
                    ),
                ),
                validity_rechecks=(
                    ValidityRecheck(
                        opportunity_id=OpportunityId(1),
                        source=OutputRef("daily_above_ema", "condition"),
                        mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST,
                        at=_at(batch, "P4_CONFIRMATION", 0),
                        outcome=ValidityRecheckOutcome.MISSING_SKIPPED,
                    ),
                ),
                confirmation_attempts=(
                    ConfirmationAttempt(
                        opportunity_id=OpportunityId(1),
                        bar_key=bar_key,
                        request_id=RequestId(1),
                        outcome=ConfirmationAttemptOutcome.WAITING,
                    ),
                ),
            )
        if self.calls == 2:
            return RuntimeStepResult(
                confirmation_attempts=(
                    ConfirmationAttempt(
                        opportunity_id=OpportunityId(1),
                        bar_key=bar_key,
                        request_id=RequestId(1),
                        outcome=ConfirmationAttemptOutcome.SKIPPED,
                    ),
                ),
            )
        return RuntimeStepResult()


def _two_steps(runtime: _Stage3ColumnsRuntime) -> BacktestEngine:
    engine = _engine(runtime, with_position=False)
    for moment in (DECISION_TIME, RUN_INTERVAL.end):
        clock = PhaseClock(engine._phases, moment)
        batch = PublicationBatch(
            batch_id=engine._allocator.next(EventId),
            decision_time=moment,
            phases=engine._phases,
        )
        engine._step(batch, "P5_ORDER_INTENT", clock)
    return engine


def test_the_runtime_order_of_transitions_waits_and_rechecks_is_kept() -> None:
    """D06 §4.4・D05 §6.6: 3種類の記録はランタイムの `step` 内で1本の通し番号を共有する。

    エンジンは番号を自分の時計で振り直すが、ランタイムが刻んだ順（再検査 → 遷移 → 待機の
    出来事）を保つ。種類ごとに振り直すと、種類をまたいだ前後関係が返した列の並びで決まって
    しまう。
    """
    engine = _two_steps(_Stage3ColumnsRuntime())
    (recheck,) = engine.rows[TraceTable.VALIDITY_RECHECKS]
    (transition,) = engine.rows[TraceTable.OPPORTUNITY_TRANSITIONS]
    (wait,) = engine.rows[TraceTable.WAIT_EVENTS]
    assert isinstance(recheck, ValidityRecheck)
    assert isinstance(transition, OpportunityTransition)
    assert isinstance(wait, WaitEvent)
    assert (recheck.at.sequence, transition.at.sequence, wait.at.sequence) == (0, 1, 2)
    assert {recheck.at.phase.name, transition.at.phase.name, wait.at.phase.name} == {
        "P4_CONFIRMATION"
    }


def test_a_rewritten_confirmation_attempt_replaces_its_row() -> None:
    """D06 §9.2 の表18（Q26 決定）: 同じ `(opportunity_id, bar_key)` の試行は行を置き換える。

    待機に入った試行（`WAITING`）が後の `step` で決着すると、表18 にはその確認足について
    **最後の結末が1行だけ**残る。
    """
    engine = _two_steps(_Stage3ColumnsRuntime())
    (attempt,) = engine.rows[TraceTable.CONFIRMATION_ATTEMPTS]
    assert isinstance(attempt, ConfirmationAttempt)
    assert attempt.outcome is ConfirmationAttemptOutcome.SKIPPED


def test_a_substituted_input_is_written_with_its_evaluation_id() -> None:
    """D06 §9.2 の表17: 遡った入力は、その評価記録の `evaluation_id` と組み合わせて1行になる。"""
    engine = _two_steps(_Stage3ColumnsRuntime())
    (row,) = engine.rows[TraceTable.INPUT_SUBSTITUTIONS]
    assert isinstance(row, CompositeRow)
    assert row.primary.evaluation_id == EvaluationId(1)  # type: ignore[attr-defined]
    substitution = row.parts[0][2]
    assert isinstance(substitution, SubstitutedInput)
    assert substitution.input_name == "prices"
