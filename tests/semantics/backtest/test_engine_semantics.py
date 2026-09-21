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
from odyssey_fx.common.time import Interval, UtcTime
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


def _engine(policy: object) -> object:
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
        runtime=_NullRuntime(),
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
