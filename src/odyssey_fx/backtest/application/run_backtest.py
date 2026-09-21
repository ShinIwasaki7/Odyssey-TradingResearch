"""1回の run を実行するユースケース（D06 §4.2・§9.3・§9.4・§10.5）。

入力はコンパイル済み戦略と実行設定、出力は `BacktestResult`・run manifest・判断履歴である。
手順は3段ある。

1. 実行前のデータ能力検査（D06 §10.5）。解決済み戦略と設定の参照の照合、銘柄別ポリシーの
   照合、解像度階層の適合検査を行い、不合格なら `FAILED_CAPABILITY` で開始しない。
2. エンジンで判断時点を進める（D06 §4）。
3. 判断履歴を `TraceSink` へ、結果と manifest を `ResultWriter` へ渡す。

**解決済みのポリシーは構築時に受け取る**。`RunConfig` は版参照（`PolicyRef`）だけを持ち、
参照を解決するのは合成の責務（`app`）だからである（D06 §3）。
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from odyssey_fx.backtest.application.ports import (
    Calendar,
    ExecutionSeries,
    IntrabarSeries,
    PublicationFeed,
    ResultWriter,
    TraceSink,
)
from odyssey_fx.backtest.domain.policies import (
    ConversionPolicy,
    CostModel,
    ExecutionPolicy,
    HierarchyCheckResult,
    RiskPolicy,
    RunConfig,
)
from odyssey_fx.backtest.engine.loop import BacktestEngine, EngineContext, TraceOutputSink
from odyssey_fx.backtest.engine.phases import BACKTEST_PHASES
from odyssey_fx.backtest.execution.protection_hits import hierarchy_checks
from odyssey_fx.backtest.trace.manifest import (
    DataCapabilityReport,
    RunManifest,
    config_digest_of,
)
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import BacktestResult, RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import IdAllocator
from odyssey_fx.common.money import decimal_from_int, kernel_context
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.symbol import SymbolSpec, SymbolSpecRef
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy
from odyssey_fx.strategy.runtime.ports import StrategyRuntime

__all__ = ["RunBacktest", "capability_report"]

_ZERO = decimal_from_int(0)


def capability_report(
    config: RunConfig,
    compiled: CompiledStrategy,
    *,
    integrity: IntegrityReport,
    execution_policy: ExecutionPolicy,
    symbol_spec: SymbolSpec,
    intrabar_series: IntrabarSeries | None = None,
) -> DataCapabilityReport:
    """実行前のデータ能力検査（D06 §10.5）。合格・不合格のどちらでも全体を保存する。"""
    reasons: list[str] = []
    compiled_match = compiled.compiled_ref == config.compiled_ref
    if not compiled_match:
        reasons.append("the compiled strategy does not match the reference in the run config")
    if compiled.symbol != config.execution_series.symbol:
        reasons.append("the compiled strategy and the execution series use different symbols")
    if execution_policy.adverse_fill_limit(config.execution_series.symbol) is None:
        reasons.append(
            f"the execution policy has no adverse fill limit for {config.execution_series.symbol}"
        )
    if symbol_spec.symbol != config.execution_series.symbol:
        reasons.append("the symbol spec does not describe the traded symbol")
    if execution_policy.resolution_hierarchy.levels[0] != config.execution_series:
        reasons.append("the first level of the resolution hierarchy is not the execution series")
    if integrity.has_errors():
        reasons.append("the snapshot integrity report contains errors")

    hierarchy = execution_policy.resolution_hierarchy
    checks: tuple[HierarchyCheckResult, ...] = ()
    if len(hierarchy.levels) > 1:
        if intrabar_series is None:
            # 下位足を宣言しているのに走査する手段が無ければ、検査1〜4 を1件も実行できない。
            # 黙って親足の4本値へ落とさず実行不可にする（ADR-0030）。
            reasons.append(
                "the resolution hierarchy declares child levels but no series to scan them"
            )
        else:
            # 親足を渡さないと検査が1件も走らず、被覆の欠けや価格基準の食い違いを
            # 見逃したまま実行可能と報告してしまう（D06 §7.4 の検査1〜4）。
            checks = hierarchy_checks(
                hierarchy,
                intrabar_series.bars_in(hierarchy.levels[0], config.run_interval),
                intrabar_series.bars_in,
            )
            if not checks:
                reasons.append(
                    "the execution series has no bars in the run interval to check the hierarchy"
                )
    if any(not check.passed for check in checks):
        reasons.append("the resolution hierarchy does not fit the data")
    runnable = not reasons
    return DataCapabilityReport(
        compiled_match=compiled_match,
        integrity=integrity,
        hierarchy_checks=checks,
        runnable=runnable,
        reason=None if runnable else Reason(ReasonCode.DATA_ERROR),
    )


class RunBacktest:
    """`run(config, compiled) -> BacktestResult`（D06 §3・§4.2）。"""

    def __init__(
        self,
        *,
        runtime: StrategyRuntime,
        context: EngineContext,
        output_sink: TraceOutputSink,
        allocator: IdAllocator,
        feed: PublicationFeed,
        execution_series: ExecutionSeries,
        calendar: Calendar,
        risk_policy: RiskPolicy,
        execution_policy: ExecutionPolicy,
        cost_model: CostModel,
        conversion_policy: ConversionPolicy,
        symbol_spec: SymbolSpec,
        symbol_spec_ref: SymbolSpecRef,
        integrity: IntegrityReport,
        trace_sink: TraceSink,
        result_writer: ResultWriter,
        intrabar_series: IntrabarSeries | None = None,
        timeframe_refs: tuple[TimeframeRef, ...] = (),
        strategy_priority: int = 0,
    ) -> None:
        self._runtime = runtime
        self._context = context
        self._sink = output_sink
        self._allocator = allocator
        self._feed = feed
        self._execution_series = execution_series
        self._calendar = calendar
        self._risk_policy = risk_policy
        self._execution_policy = execution_policy
        self._cost_model = cost_model
        self._conversion_policy = conversion_policy
        self._symbol_spec = symbol_spec
        self._symbol_spec_ref = symbol_spec_ref
        self._integrity = integrity
        self._trace_sink = trace_sink
        self._result_writer = result_writer
        self._intrabar = intrabar_series
        self._timeframe_refs = timeframe_refs
        self._priority = strategy_priority

    def run(self, config: RunConfig, compiled: CompiledStrategy) -> BacktestResult:
        """1回の run を実行し、結果 DTO を返す。"""
        if not isinstance(config, RunConfig):
            raise KernelValueError("RunBacktest.run requires a RunConfig")
        report = capability_report(
            config,
            compiled,
            integrity=self._integrity,
            execution_policy=self._execution_policy,
            symbol_spec=self._symbol_spec,
            intrabar_series=self._intrabar,
        )
        engine = BacktestEngine(
            config=config,
            compiled=compiled,
            runtime=self._runtime,
            context=self._context,
            output_sink=self._sink,
            allocator=self._allocator,
            feed=self._feed,
            execution_series=self._execution_series,
            calendar=self._calendar,
            risk_policy=self._risk_policy,
            execution_policy=self._execution_policy,
            cost_model=self._cost_model,
            conversion_policy=self._conversion_policy,
            symbol_spec=self._symbol_spec,
            capability_report=report,
            intrabar_series=self._intrabar,
            strategy_priority=self._priority,
        )
        engine.execute()

        rows = engine.rows
        for table in TraceTable:
            self._trace_sink.write(table, rows[table])

        manifest = RunManifest(
            run_id=self._allocator.run_id,
            config=config,
            config_digest=config_digest_of(config),
            phases=BACKTEST_PHASES,
            id_allocator_snapshot=self._allocator.snapshot(),
            capability_report=report,
            resolution_hierarchy=self._execution_policy.resolution_hierarchy,
            unresolved_intrabar_count=engine.unresolved_intrabar_count,
            unresolved_intrabar_ratio=_ratio(
                engine.unresolved_intrabar_count, engine.intrabar_conflict_count
            ),
            swap_modeled=self._cost_model.swap_modeled,
            status=engine.status.value,
            symbol_spec_ref=self._symbol_spec_ref,
            calendar_ref=f"{self._calendar.id}@v{self._calendar.version}",
            timeframe_def_refs=self._timeframe_refs,
            reason=engine.failure_reason,
        )
        result = BacktestResult(
            run_id=self._allocator.run_id,
            status=engine.status,
            trace_tables={
                table: f"runs/{self._allocator.run_id}/{table.value}.parquet"
                for table in TraceTable
            },
            capability_report=report,
            manifest_ref=f"runs/{self._allocator.run_id}/manifest.json",
            swap_modeled=self._cost_model.swap_modeled,
            unresolved_intrabar_count=engine.unresolved_intrabar_count,
            trade_count=engine.trade_count,
            opportunity_count=engine.opportunity_count,
            summaries=engine.summaries if engine.status is RunStatus.COMPLETED else None,
        )
        self._result_writer.write(result, manifest)
        return result


def _ratio(count: int, total: int) -> Decimal:
    """全競合に対する割合（ADR-0030）。競合が無ければ 0。"""
    if total <= 0:
        return _ZERO
    with localcontext(kernel_context()):
        return decimal_from_int(count) / decimal_from_int(total)
