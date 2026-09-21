"""エンジンの意味論（D06 §4・§8・§10）。

D06 §11 が挙げる意味論テストのうち、判断時点を実際に進めないと確かめられないものを置く。
受付拒否で注文と予約が残らないこと、実行中のデータ不整合、末尾処理の順序、助走中の注文ゼロ、
台帳の恒等式が対象である。
"""

from __future__ import annotations

from datetime import timedelta

from odyssey_fx.backtest.domain.orders import OrderStatus
from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.engine.run_end import RUN_END_STEPS
from odyssey_fx.backtest.portfolio.ledger import LedgerSnapshot
from odyssey_fx.backtest.portfolio.mtm import unrealized
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.ids import PositionId, RunId
from odyssey_fx.common.money import Money, decimal_from_str
from odyssey_fx.common.reason import ReasonCode
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy
from odyssey_fx.strategy.runtime.ports import PublicationBatch
from odyssey_fx.strategy.runtime.requests import RuntimeStepResult
from tests.fixtures.backtest.harness import (
    COST_MODEL,
    EXECUTION_POLICY,
    JPY,
    compiled_strategy,
    run_backtest,
)
from tests.fixtures.backtest.paths import (
    DECISION_TIME,
    RUN_INTERVAL,
    execution_bars,
    signal_bars,
)
from tests.fixtures.strategy.strategy_a import strategy_a


def _money(text: str) -> Money:
    return Money(decimal_from_str(text), JPY)


def test_a_rejected_attempt_leaves_no_order_and_no_reservation() -> None:
    """D06 §5.2: 受付前拒否は注文も予約も作らない。"""
    output = run_backtest(
        signal_bars=signal_bars(stop_low="149.990"),
        execution_bars=execution_bars(reference_close="149.980"),
        run_interval=RUN_INTERVAL,
    )

    assert output.rows(TraceTable.ORDERS) == ()
    assert output.rows(TraceTable.RESERVATIONS) == ()
    assert output.context.ledger.balance == _money("1000000")


def test_a_missing_execution_bar_fails_the_run_and_cancels_pending_orders() -> None:
    """D06 §10.4: 実行中の失敗では未約定注文を `CANCELED`（`DATA_ERROR`）にする。"""
    execution = execution_bars()
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution,
        # 公開フィードは足の到着を知らせるが、執行系列にはその足が無い。
        execution_view_bars=execution[:2],
        run_interval=RUN_INTERVAL,
    )

    assert output.result.status is RunStatus.FAILED_DATA_ERROR
    assert output.result.summaries is None
    states = output.context.ledger.order_states
    assert all(state.status is not OrderStatus.PENDING for state in states.values())


def test_a_failed_run_is_not_reported_as_completed() -> None:
    """D06 §9.4: 失敗した run の結果を正常完走と同じ扱いにしない。"""
    execution = execution_bars()
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution,
        execution_view_bars=execution[:2],
        run_interval=RUN_INTERVAL,
    )

    assert output.manifest.status == RunStatus.FAILED_DATA_ERROR.value
    assert output.manifest.reason is not None
    assert output.manifest.reason.code is ReasonCode.DATA_ERROR


def test_the_final_snapshot_comes_after_the_end_of_run_cancellations() -> None:
    """D06 §10.1: 手順5（取消）→ 手順6（機会の終端）→ 手順7（最終 snapshot）の順。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=Interval(start=DECISION_TIME, end=UtcTime.from_components(2026, 1, 6, 9, 30)),
    )

    snapshots = [
        row for row in output.rows(TraceTable.LEDGER_SNAPSHOTS) if isinstance(row, LedgerSnapshot)
    ]
    final = snapshots[-1]
    assert final.at.phase.name == "RUN_END"
    assert len(RUN_END_STEPS) == 7


def test_the_ledger_identity_holds_at_every_snapshot() -> None:
    """D06 §8.1: どの snapshot でも `equity = balance + 含み損益` が成り立つ。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )

    for row in output.rows(TraceTable.LEDGER_SNAPSHOTS):
        assert isinstance(row, LedgerSnapshot)
        if not row.open_position_ids:
            assert row.equity == row.balance


def test_the_open_position_is_valued_with_the_last_completed_close() -> None:
    """D06 §8.1（Q13 決定）: 含み損益の評価価格は直前に完了した執行足の終値。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=Interval(start=DECISION_TIME, end=UtcTime.from_components(2026, 1, 6, 9, 30)),
    )

    position = output.context.ledger.open_positions()[0]
    last_close = execution_bars()[2].close
    expected = output.context.ledger.balance + unrealized(
        position, last_close, COST_MODEL.spread_model, JPY
    )
    assert output.context.equity == expected


def test_no_order_is_placed_while_the_warmup_is_incomplete() -> None:
    """全体計画 §8.2: 助走中は注文がゼロになる（役割出力が揃わない）。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
        definition=strategy_a(stop_lookback=50),
    )

    assert output.rows(TraceTable.ORDER_REQUESTS) == ()
    assert output.rows(TraceTable.ORDERS) == ()
    assert output.result.trade_count == 0


def test_the_run_manifest_keeps_the_capability_report_whole() -> None:
    """D06 §9.3・§10.5: 能力検査の結果は要約に畳まず全体のまま保存する。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )

    report = output.manifest.capability_report
    assert report.runnable is True
    assert report.compiled_match is True
    assert output.result.capability_report is report
    assert output.manifest.phases.by_name("RUN_END").rank == 14


def test_the_manifest_records_the_intrabar_conflict_ratio() -> None:
    """ADR-0030: 順序を観測できなかった件数と、全競合に対する割合を残す。"""
    from tests.fixtures.backtest.paths import CONFLICT_BAR

    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(conflict=CONFLICT_BAR),
        run_interval=RUN_INTERVAL,
    )

    assert output.manifest.unresolved_intrabar_count == 1
    assert output.manifest.unresolved_intrabar_ratio == decimal_from_str("1")


def test_a_second_run_with_the_same_input_allocates_the_same_ids() -> None:
    """D06 §4.4: 採番順は処理順に一致し、再実行で同じ ID 列になる。"""
    first = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )
    second = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )

    assert first.manifest.id_allocator_snapshot == second.manifest.id_allocator_snapshot
    assert first.manifest.config_digest == second.manifest.config_digest


def test_the_decision_points_stop_at_the_run_end() -> None:
    """D06 §10.1: run_end から始まる足の始値処理（rank 11）は行わない。"""
    end = UtcTime.from_components(2026, 1, 6, 9, 15)
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=Interval(start=DECISION_TIME, end=end),
    )

    fills = output.rows(TraceTable.FILLS)
    assert len(fills) == 1
    snapshots = [
        row for row in output.rows(TraceTable.LEDGER_SNAPSHOTS) if isinstance(row, LedgerSnapshot)
    ]
    assert all(
        not (row.at.time == end and row.at.phase.name == "EXECUTION_OPEN") for row in snapshots
    )
    assert end - DECISION_TIME == timedelta(minutes=15)


def test_a_declared_child_level_without_a_series_blocks_the_run() -> None:
    """ADR-0030: 下位足を宣言したのに走査できなければ実行不可（親足へ落とさない）。"""
    from dataclasses import replace

    from odyssey_fx.backtest.application.run_backtest import capability_report
    from odyssey_fx.backtest.domain.policies import ResolutionHierarchy
    from odyssey_fx.marketdata.domain.integrity import IntegrityReport
    from odyssey_fx.marketdata.domain.series import PriceBasis
    from tests.fixtures.backtest.harness import (
        EXECUTION_POLICY,
        EXECUTION_SERIES,
        SYMBOL_SPEC,
        compiled_strategy,
    )
    from tests.fixtures.synthetic.market import USDJPY, series

    child = series(USDJPY, "5m", PriceBasis.BID)
    policy = replace(
        EXECUTION_POLICY,
        resolution_hierarchy=ResolutionHierarchy(levels=(EXECUTION_SERIES, child)),
    )
    compiled = compiled_strategy()
    report = capability_report(
        _config(compiled),
        compiled,
        integrity=IntegrityReport(),
        execution_policy=policy,
        symbol_spec=SYMBOL_SPEC,
        intrabar_series=None,
    )

    assert report.runnable is False
    assert report.hierarchy_checks == ()


def test_the_hierarchy_checks_run_against_the_parent_bars() -> None:
    """D06 §7.4 の検査1〜4: 親足を渡さないと1件も走らない（見逃しになる）。"""
    from dataclasses import replace

    from odyssey_fx.backtest.application.run_backtest import capability_report
    from odyssey_fx.backtest.domain.policies import ResolutionHierarchy
    from odyssey_fx.marketdata.domain.integrity import IntegrityReport
    from odyssey_fx.marketdata.domain.series import PriceBasis
    from tests.fixtures.backtest.harness import (
        EXECUTION_POLICY,
        EXECUTION_SERIES,
        SYMBOL_SPEC,
        FakeIntrabarSeries,
        compiled_strategy,
    )
    from tests.fixtures.synthetic.market import USDJPY, series

    child = series(USDJPY, "5m", PriceBasis.BID)
    policy = replace(
        EXECUTION_POLICY,
        resolution_hierarchy=ResolutionHierarchy(levels=(EXECUTION_SERIES, child)),
    )
    # 子足を1本も持たない系列を渡すと、被覆の検査が落ちる。
    intrabar = FakeIntrabarSeries({EXECUTION_SERIES: execution_bars(), child: ()})
    compiled = compiled_strategy()
    report = capability_report(
        _config(compiled),
        compiled,
        integrity=IntegrityReport(),
        execution_policy=policy,
        symbol_spec=SYMBOL_SPEC,
        intrabar_series=intrabar,
    )

    assert report.hierarchy_checks != ()
    assert any(check.check == "coverage" and not check.passed for check in report.hierarchy_checks)
    assert report.runnable is False


def _config(compiled: CompiledStrategy) -> RunConfig:
    """能力検査だけを呼ぶための実行設定。"""
    import hashlib

    from odyssey_fx.common.ids import SnapshotId
    from odyssey_fx.common.refs import ContentDigest, PolicyRef, SnapshotRef
    from tests.fixtures.backtest.harness import ACCOUNT, EXECUTION_SERIES

    digest = ContentDigest.sha256(hashlib.sha256(b"capability").hexdigest())
    ref = PolicyRef(policy_kind="risk", policy_id="risk_v1", version=1, digest=digest)
    return RunConfig(
        run_interval=RUN_INTERVAL,
        snapshot_ref=SnapshotRef(snapshot_id=SnapshotId(digest)),
        compiled_ref=compiled.compiled_ref,
        account=ACCOUNT,
        risk_policy_ref=ref,
        execution_policy_ref=ref,
        cost_model_ref=ref,
        conversion_policy_ref=ref,
        delay_scenario_ref=ref,
        execution_series=EXECUTION_SERIES,
    )


def test_the_evidence_rows_carry_their_provenance() -> None:
    """上位設計書 §4.7.15: 根拠記録は市場データ・口座 snapshot・設定の版まで辿れる。

    型としては在るのに中身が空、という記録にしない（D06 §9.2 の表15）。
    """
    from odyssey_fx.backtest.trace.recorder import EvidenceKind, EvidenceRecord

    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )

    evidence = [row for row in output.rows(TraceTable.EVIDENCE) if isinstance(row, EvidenceRecord)]
    request = next(row for row in evidence if row.kind is EvidenceKind.ORDER_REQUEST)
    assert request.output_ids, "the order request points at the outputs behind it"
    assert len(request.evaluation_ids) == len(request.output_ids)
    assert request.market_refs, "the reference quote names the bar it came from"
    assert request.ledger_snapshot_at is not None

    admission = next(row for row in evidence if row.kind is EvidenceKind.ADMISSION)
    assert admission.conversion_paths, "the conversion path is persisted (D06 §8.5.1 の規則8)"
    assert admission.policy_refs

    fill = next(row for row in evidence if row.kind is EvidenceKind.FILL)
    assert fill.market_refs
    assert fill.conversion_paths
    assert fill.policy_refs


def test_the_manifest_records_where_the_run_came_from() -> None:
    """D06 §9.3 の識別の群: コード・lock・環境のダイジェストと git の状態を残す。

    `RunId` がその4つのダイジェストから来ていることも manifest が検査する（ADR-0006）。
    """
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )

    manifest = output.manifest
    assert manifest.code_digest.digest.hex
    assert manifest.lock_digest.digest.hex
    assert manifest.env_digest.digest.hex
    assert manifest.git_commit == "0" * 40
    assert manifest.git_dirty is False


def test_the_config_digest_covers_the_symbol_specification() -> None:
    """D06 §9.3: 入力とポリシーの群すべてが `ConfigDigest` の対象になる。

    価格刻みを変えれば丸めも数量も変わるので、同じダイジェストになってはいけない。
    """
    from odyssey_fx.backtest.trace.manifest import config_digest_of
    from odyssey_fx.common.refs import ContentDigest
    from odyssey_fx.common.symbol import SymbolSpecRef
    from tests.fixtures.backtest.harness import CALENDAR_REF, SYMBOL_SPEC_REF, TIMEFRAME_REFS
    from tests.fixtures.synthetic.market import USDJPY

    compiled = compiled_strategy()
    config = _config(compiled)
    other_spec = SymbolSpecRef(
        symbol=USDJPY, version=2, digest=ContentDigest.sha256("b" * 32 + "c" * 32)
    )

    first = config_digest_of(
        config,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=CALENDAR_REF,
        timeframe_def_refs=TIMEFRAME_REFS,
    )
    second = config_digest_of(
        config,
        symbol_spec_ref=other_spec,
        calendar_ref=CALENDAR_REF,
        timeframe_def_refs=TIMEFRAME_REFS,
    )
    third = config_digest_of(
        config,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref="fx_ny17@v2",
        timeframe_def_refs=TIMEFRAME_REFS,
    )

    assert first != second
    assert first != third


def test_the_entry_delay_does_not_postpone_a_strategy_exit() -> None:
    """D06 §7.1: 1本見送るのは**新規エントリーだけ**で、決済要求には適用しない。

    決済まで遅らせると、戦略が求めた時点より後の価格で約定し、建玉がその間だけ余計に晒される。
    """
    from dataclasses import replace

    from odyssey_fx.backtest.domain.orders import CloseCause, CloseRequest
    from odyssey_fx.backtest.engine.loop import BacktestEngine

    engine = _engine(replace(EXECUTION_POLICY, entry_delay_bars=1))
    decision = DECISION_TIME
    expires = decision + timedelta(minutes=20)
    close = CloseRequest(
        position_id=PositionId(1), cause=CloseCause.STRATEGY_EXIT, valid_for=timedelta(minutes=20)
    )
    assert isinstance(engine, BacktestEngine)

    entry_candidate, _ = engine._candidate(decision, expires, is_entry=True)
    close_candidate, _ = engine._candidate(decision, expires, is_entry=False)

    assert close.request_class.rank == 0
    assert entry_candidate is not None and close_candidate is not None
    # エントリーは1本見送って 09:15、決済は見送らず 09:00。
    assert close_candidate.open_time == decision
    assert entry_candidate.open_time == decision + timedelta(minutes=15)


def _engine(policy: object, runtime: object | None = None) -> object:
    """候補の選び方だけを確かめるためのエンジン（run は実行しない）。"""
    from odyssey_fx.backtest.engine.loop import BacktestEngine, EngineContext, TraceOutputSink
    from odyssey_fx.backtest.trace.manifest import DataCapabilityReport
    from odyssey_fx.common.ids import IdAllocator
    from odyssey_fx.marketdata.domain.integrity import IntegrityReport
    from tests.fixtures.backtest.harness import (
        ACCOUNT,
        CONVERSION_POLICY,
        COST_MODEL,
        RISK_POLICY,
        SYMBOL_SPEC,
        FakeExecutionSeries,
        FakeFeed,
    )
    from tests.fixtures.synthetic.market import calendar

    compiled = compiled_strategy()
    config = _config(compiled)
    context = EngineContext(ACCOUNT)
    return BacktestEngine(
        config=config,
        compiled=compiled,
        runtime=_NullRuntime() if runtime is None else runtime,  # type: ignore[arg-type]
        context=context,
        output_sink=TraceOutputSink(),
        allocator=IdAllocator(_run_id()),
        feed=FakeFeed((), execution_bars()),
        execution_series=FakeExecutionSeries(execution_bars()),
        calendar=calendar(),
        risk_policy=RISK_POLICY,
        execution_policy=policy,  # type: ignore[arg-type]
        cost_model=COST_MODEL,
        conversion_policy=CONVERSION_POLICY,
        symbol_spec=SYMBOL_SPEC,
        capability_report=DataCapabilityReport(compiled_match=True, integrity=IntegrityReport()),
    )


def _run_id() -> RunId:
    from odyssey_fx.backtest.trace.manifest import config_digest_of
    from odyssey_fx.common.refs import run_id
    from tests.fixtures.backtest.harness import (
        CALENDAR_REF,
        CODE_DIGEST,
        ENV_DIGEST,
        LOCK_DIGEST,
        SYMBOL_SPEC_REF,
        TIMEFRAME_REFS,
    )

    digest = config_digest_of(
        _config(compiled_strategy()),
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=CALENDAR_REF,
        timeframe_def_refs=TIMEFRAME_REFS,
    )
    return run_id(digest, CODE_DIGEST, LOCK_DIGEST, ENV_DIGEST)


class _NullRuntime:
    """呼ばれない戦略ランタイム（候補の選び方だけを見るため）。"""

    def step(self, batch: PublicationBatch) -> RuntimeStepResult:  # pragma: no cover
        raise AssertionError("the runtime must not be called in this test")


def test_the_conversion_rate_points_at_its_evidence_record() -> None:
    """上位設計書 §4.7.9 C: 換算率からその根拠記録へ辿れる。

    率だけを残すと「どの観測で、どの設定のもとで換算したか」が結果から追えない。審査の率は
    その審査の根拠記録を、費用の率はその約定の根拠記録を指す。
    """
    from odyssey_fx.backtest.admission.risk_assessment import RiskAssessment
    from odyssey_fx.backtest.domain.fills import FillRecord
    from odyssey_fx.backtest.trace.recorder import EvidenceRecord
    from odyssey_fx.common.refs import EvidenceRef

    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )

    evidence_ids = {
        row.evidence_id
        for row in output.rows(TraceTable.EVIDENCE)
        if isinstance(row, EvidenceRecord)
    }
    assessments = [
        row for row in output.rows(TraceTable.RISK_ASSESSMENTS) if isinstance(row, RiskAssessment)
    ]
    assert assessments
    for assessment in assessments:
        assert assessment.conversion is not None
        assert assessment.conversion.evidence == EvidenceRef(evidence_id=assessment.assessment_id)
        assert assessment.assessment_id in evidence_ids

    fills = [row for row in output.rows(TraceTable.FILLS) if isinstance(row, FillRecord)]
    assert fills
    for fill in fills:
        assert fill.costs
        for entry in fill.costs:
            assert entry.conversion.evidence == fill.evidence_ref
            assert fill.evidence_ref.evidence_id in evidence_ids


def test_an_account_currency_unlike_the_settlement_currency_blocks_the_run() -> None:
    """D06 §8.5: 段階2 は恒等換算だけを通す。決済通貨と口座通貨が違えば実行前に止める。"""
    from dataclasses import replace

    from odyssey_fx.backtest.application.run_backtest import capability_report
    from odyssey_fx.backtest.domain.account import AccountSpec
    from odyssey_fx.common.ids import AccountId
    from odyssey_fx.common.money import CurrencyCode
    from odyssey_fx.marketdata.domain.integrity import IntegrityReport
    from tests.fixtures.backtest.harness import SYMBOL_SPEC

    usd = CurrencyCode("USD")
    account = AccountSpec(
        account_id=AccountId("ACC1"),
        currency=usd,
        initial_balance=Money(decimal_from_str("1000000"), usd),
    )
    compiled = compiled_strategy()
    report = capability_report(
        replace(_config(compiled), account=account),
        compiled,
        integrity=IntegrityReport(),
        execution_policy=EXECUTION_POLICY,
        symbol_spec=SYMBOL_SPEC,
        intrabar_series=None,
    )

    assert report.runnable is False
    assert any("settles in" in text for text in report.diagnostics)

    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
        account=account,
    )
    assert output.result.status is RunStatus.FAILED_CAPABILITY
    assert output.rows(TraceTable.ORDERS) == ()


def test_a_run_id_unlike_the_complete_input_fails_before_anything_is_written() -> None:
    """ADR-0006: `RunId` は完全入力のダイジェスト。違えば1行も書かずに止める。

    manifest を作る段で初めて気付くと、表だけが残り manifest の無いディレクトリになる。
    そのディレクトリは既存成果物の検査に引っかかり、直したあとの再実行まで塞いでしまう。
    """
    import pytest

    from odyssey_fx.common.errors import KernelValueError
    from odyssey_fx.common.ids import RunId
    from tests.fixtures.backtest.harness import build_run

    setup = build_run(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
        allocator_run_id=RunId(_content_digest("not the complete input")),
    )

    with pytest.raises(KernelValueError, match="ADR-0006"):
        setup.use_case.run(setup.config, setup.compiled)

    assert setup.trace_sink.tables == {}
    assert setup.result_writer.manifest is None
    assert setup.result_writer.result is None


def _content_digest(seed: str) -> ContentDigest:
    """テストが使う固定ダイジェスト。"""
    import hashlib

    return ContentDigest.sha256(hashlib.sha256(seed.encode("utf-8")).hexdigest())


class _RunEndRuntime:
    """末尾処理で機会の終端だけを返すランタイム。

    ランタイムは自分の `step` の中で 0 から数えるので、返ってくる処理点の通し番号は
    エンジンが末尾処理で配った番号と重なる。エンジンが振り直しているかを見るための相手役。
    """

    def step(self, batch: PublicationBatch) -> RuntimeStepResult:
        from odyssey_fx.common.ids import OpportunityId
        from odyssey_fx.common.reason import Reason, ReasonCode, RunEndDetail
        from odyssey_fx.common.time import ProcessingPoint
        from odyssey_fx.strategy.runtime.opportunities import (
            OpportunityState,
            OpportunityTransition,
        )

        phase = batch.phases.by_name("RUN_END")
        return RuntimeStepResult(
            transitions=(
                OpportunityTransition(
                    opportunity_id=OpportunityId(1),
                    from_state=OpportunityState.OPEN,
                    to_state=OpportunityState.TERMINATED,
                    at=ProcessingPoint(time=batch.decision_time, phase=phase, sequence=0),
                    phase=phase,
                    reason=Reason(ReasonCode.RUN_END, RunEndDetail(run_end=batch.decision_time)),
                ),
            ),
        )


def test_the_end_of_run_transitions_get_their_own_processing_points() -> None:
    """D06 §10.1: 取消 → 機会の終端 → 最終 snapshot の順を処理点の順で読み直せる。

    ランタイムが返す処理点をそのまま残すと、最終 snapshot と同じ `(時刻, RUN_END, 通し番号)`
    になり、どちらが先だったのかが記録から分からなくなる。
    """
    from odyssey_fx.backtest.engine.clock import PhaseClock
    from odyssey_fx.backtest.engine.loop import BacktestEngine
    from odyssey_fx.strategy.runtime.opportunities import OpportunityTransition

    engine = _engine(EXECUTION_POLICY, runtime=_RunEndRuntime())
    assert isinstance(engine, BacktestEngine)
    clock = PhaseClock(engine._phases, RUN_INTERVAL.end)
    engine._phase_run_end(clock)

    rows = engine.rows
    transitions = [
        row
        for row in rows[TraceTable.OPPORTUNITY_TRANSITIONS]
        if isinstance(row, OpportunityTransition)
    ]
    snapshots = [
        row for row in rows[TraceTable.LEDGER_SNAPSHOTS] if isinstance(row, LedgerSnapshot)
    ]
    assert len(transitions) == 1
    assert transitions[0].at.phase.name == "RUN_END"
    assert snapshots[-1].at.phase.name == "RUN_END"
    # 機会の終端が先、最終 snapshot が後。同じ番号には決してならない。
    assert transitions[0].at.sequence < snapshots[-1].at.sequence


def _child_series() -> SeriesId:
    """執行系列の1段下（5分足・同じ価格基準）。"""
    from odyssey_fx.marketdata.domain.series import PriceBasis
    from tests.fixtures.synthetic.market import USDJPY, series

    return series(USDJPY, "5m", PriceBasis.BID)


def _child_bars(
    parents: tuple[Bar, ...], conflict_children: tuple[tuple[str, str, str, str], ...]
) -> tuple[Bar, ...]:
    """親足1本を5分足3本に割る。競合する親足だけ明示した並びを使う。"""
    from datetime import timedelta as _timedelta

    from tests.fixtures.backtest.harness import bars
    from tests.fixtures.backtest.paths import CONFLICT_BAR

    child_series = _child_series()
    made: list[Bar] = []
    for parent in parents:
        open_text = str(parent.open.value)
        high_text = str(parent.high.value)
        low_text = str(parent.low.value)
        close_text = str(parent.close.value)
        if (open_text, high_text, low_text, close_text) == CONFLICT_BAR:
            specs = list(conflict_children)
        else:
            flat = (close_text, close_text, close_text, close_text)
            specs = [(open_text, high_text, low_text, close_text), flat, flat]
        made.extend(bars(child_series, parent.bar_start, _timedelta(minutes=5), specs))
    return tuple(made)


def test_only_the_child_bars_actually_read_become_evidence() -> None:
    """上位設計書 §4.7.15: 根拠記録に載せるのは**実際に読んだ**観測だけ。

    足の中の競合を下位足で決めたとき、走査を打ち切った先の足まで載せると、裁定に関わって
    いない観測を「見た」と記録することになる。
    """
    from dataclasses import replace

    from odyssey_fx.backtest.domain.fills import FillRecord
    from odyssey_fx.backtest.domain.orders import CloseCause
    from odyssey_fx.backtest.domain.policies import ResolutionHierarchy
    from odyssey_fx.backtest.execution.protection_hits import IntrabarResolution, ResolutionMethod
    from odyssey_fx.backtest.trace.recorder import EvidenceRecord
    from tests.fixtures.backtest.harness import EXECUTION_SERIES
    from tests.fixtures.backtest.paths import CONFLICT_BAR

    # 1本目の子足で利確だけに触れる（そこで走査は終わる）。2本目に損切りの動きを置く。
    conflict_children = (
        ("150.100", "151.300", "150.100", "151.300"),
        ("151.300", "151.300", "149.400", "149.400"),
        ("149.400", "150.200", "149.400", "150.200"),
    )
    parents = execution_bars(conflict=CONFLICT_BAR)
    children = _child_bars(parents, conflict_children)
    child_series = _child_series()
    policy = replace(
        EXECUTION_POLICY,
        resolution_hierarchy=ResolutionHierarchy(levels=(EXECUTION_SERIES, child_series)),
    )

    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=parents,
        run_interval=RUN_INTERVAL,
        execution_policy=policy,
        intrabar={EXECUTION_SERIES: parents, child_series: children},
    )

    assert output.manifest.capability_report.runnable is True
    resolutions = [
        row
        for row in output.rows(TraceTable.INTRABAR_RESOLUTIONS)
        if isinstance(row, IntrabarResolution)
    ]
    assert len(resolutions) == 1
    assert resolutions[0].method is ResolutionMethod.RESOLVED_BY_CHILD
    assert resolutions[0].verdict is CloseCause.TAKE_PROFIT

    fills = [row for row in output.rows(TraceTable.FILLS) if isinstance(row, FillRecord)]
    evidence = {
        row.evidence_id: row
        for row in output.rows(TraceTable.EVIDENCE)
        if isinstance(row, EvidenceRecord)
    }
    record = evidence[fills[-1].evidence_ref.evidence_id]
    read_starts = {ref.interval.start for ref in record.market_refs if ref.series == child_series}
    resolved = resolutions[0].resolved_child_bar_key
    assert resolved is not None
    assert read_starts == {resolved.bar_start}
    # 決め手の子足より後ろの2本（同じ親足の中）は読んでいないので根拠にも載らない。
    later = {
        bar.bar_start
        for bar in children
        if resolved.bar_start < bar.bar_start < resolved.bar_start + timedelta(minutes=15)
    }
    assert len(later) == 2
    assert not (later & read_starts)
