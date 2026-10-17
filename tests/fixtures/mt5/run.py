"""MT5 経路の snapshot で検証戦略 A の run を通す組み立て（D12 §11.13 の C-1〜C-7）。

本番の部品（as-of ビュー・執行系列のビュー・公開フィード・`app.mt5_run.build_mt5_run_parts`・
`RunBacktest`）をそのまま結線する。違うのは snapshot をファイルではなくメモリから渡すことと、
戦略のコンパイル・識別子のダイジェストをテスト用の固定値で作ることだけである（`app.composition`
の `_run_use_case` と同じ結線）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from odyssey_fx.app.mt5_run import build_mt5_run_parts, publication_rows
from odyssey_fx.backtest.application.run_backtest import RunBacktest
from odyssey_fx.backtest.domain.policies import ExecutionPolicy, ResolutionHierarchy, RunConfig
from odyssey_fx.backtest.engine.loop import EngineContext, TraceOutputSink
from odyssey_fx.backtest.trace.manifest import RunManifest, config_digest_of
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.ids import IdAllocator
from odyssey_fx.common.refs import SnapshotRef, run_id
from odyssey_fx.common.time import Interval
from odyssey_fx.evaluation.adapters.fs_store import FileSystemResultWriter, FileSystemTraceSink
from odyssey_fx.marketdata.application.acceptance import PendingSnapshot
from odyssey_fx.marketdata.application.asof import AsOfView, ExecutionSeriesView
from odyssey_fx.marketdata.application.mt5_observation import Mt5Observation
from odyssey_fx.marketdata.application.publication import PublicationFeed, build_feed
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from tests.fixtures.backtest.harness import (
    ACCOUNT,
    CODE_DIGEST,
    CONVERSION_POLICY,
    COST_MODEL,
    ENV_DIGEST,
    EXECUTION_POLICY,
    EXECUTION_SERIES,
    LOCK_DIGEST,
    RISK_POLICY,
    SYMBOL_SPEC,
    SYMBOL_SPEC_REF,
    CollectingResultWriter,
    CollectingTraceSink,
    FakeIntrabarSeries,
    _policy_ref,
    calendar_ref_of,
    compiled_strategy,
)
from tests.fixtures.mt5.market import BOUNDARIES, TIMEFRAME_DEFS

__all__ = ["MINUTE_SERIES", "MT5_TIMEFRAME_REFS", "Mt5RunOutput", "run_mt5"]

#: 時間足定義 版 2（`1m` を含む）の版参照（`ConfigDigest` の対象。合成と同じく全定義）。
MT5_TIMEFRAME_REFS = tuple(sorted((d.ref for d in TIMEFRAME_DEFS.values()), key=str))

MINUTE_SERIES = SeriesId(
    symbol=EXECUTION_SERIES.symbol,
    timeframe=TIMEFRAME_DEFS["1m"].ref,
    basis=EXECUTION_SERIES.basis,
)


@dataclass(frozen=True, slots=True)
class Mt5RunOutput:
    """1 回の run の成果。"""

    result: BacktestResult
    manifest: RunManifest
    tables: Mapping[TraceTable, tuple[object, ...]]
    context: EngineContext

    def rows(self, table: TraceTable) -> tuple[object, ...]:
        return self.tables.get(table, ())


def run_mt5(
    snapshot: ReadableSnapshot,
    pending: PendingSnapshot,
    calendar: TradingCalendar,
    run_interval: Interval,
    rules_root: Path,
    *,
    definition: StrategyDefinition | None = None,
    artifacts_root: Path | None = None,
    compiled: CompiledStrategy | None = None,
) -> Mt5RunOutput:
    """MT5 経路の snapshot で run を 1 回通す。`artifacts_root` を渡すと成果物をファイルに書く。"""
    partition_bars = dict(pending.partition_bars)
    allowed = frozenset(partition_bars)
    schedules = {
        record.series_id: SeriesSchedule(
            series=record.series_id,
            timeframe_def=TIMEFRAME_DEFS[record.series_id.timeframe.id],
            calendar=calendar,
        )
        for record in snapshot.manifest.series
    }
    bars_of: dict[SeriesId, tuple[Bar, ...]] = {}
    for partition_id, bars in partition_bars.items():
        bars_of[partition_id.series] = tuple(
            sorted(
                (*bars_of.get(partition_id.series, ()), *bars), key=lambda bar: bar.bar_start.value
            )
        )
    compiled = compiled_strategy(definition) if compiled is None else compiled
    observation = Mt5Observation.from_snapshot(snapshot)
    assert observation is not None
    hierarchy = (EXECUTION_SERIES, MINUTE_SERIES)
    parts = build_mt5_run_parts(
        observation=observation,
        compiled=compiled,
        execution_series=EXECUTION_SERIES,
        hierarchy=hierarchy,
        schedules=schedules,
        bars_of=bars_of,
        calendar=calendar,
        run_interval=run_interval,
        repo_root=rules_root,
    )
    feed = (
        PublicationFeed(events=())
        if parts.has_shortfall
        else build_feed(
            snapshot,
            allowed,
            partition_bars,
            schedules,
            run_interval,
            execution_series=frozenset({EXECUTION_SERIES}),
            internal_series=parts.internal_series,
            access_boundaries=BOUNDARIES,
        )
    )
    published = publication_rows(
        feed, {bar.key: bar for bars in bars_of.values() for bar in bars}, schedules, run_interval
    )
    config = RunConfig(
        run_interval=run_interval,
        snapshot_ref=SnapshotRef(snapshot_id=snapshot.manifest.snapshot_id()),
        compiled_ref=compiled.compiled_ref,
        account=ACCOUNT,
        risk_policy_ref=_policy_ref("risk", "r"),
        execution_policy_ref=_policy_ref("execution", "e"),
        cost_model_ref=_policy_ref("cost", "c"),
        conversion_policy_ref=_policy_ref("conversion", "v"),
        delay_scenario_ref=_policy_ref("delay", "d"),
        execution_series=EXECUTION_SERIES,
    )
    calendar_ref = calendar_ref_of(calendar)
    digest = config_digest_of(
        config,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=calendar_ref,
        timeframe_def_refs=MT5_TIMEFRAME_REFS,
    )
    allocator = IdAllocator(run_id(digest, CODE_DIGEST, LOCK_DIGEST, ENV_DIGEST))
    context = EngineContext(ACCOUNT)
    sink = TraceOutputSink()
    market_data = AsOfView(
        snapshot=snapshot,
        allowed_partitions=allowed,
        schedules=schedules,
        partition_bars=partition_bars,
    )
    runtime = StrategyEvaluator(
        compiled=compiled,
        registry=INITIAL_CATALOG,
        market_data=market_data,
        context=context,
        sink=sink,
        allocator=allocator,
    )
    execution_policy: ExecutionPolicy = replace(
        EXECUTION_POLICY, resolution_hierarchy=ResolutionHierarchy(levels=hierarchy)
    )
    trace_sink: CollectingTraceSink | FileSystemTraceSink
    writer: CollectingResultWriter | FileSystemResultWriter
    collecting_trace = CollectingTraceSink()
    collecting_writer = CollectingResultWriter()
    if artifacts_root is None:
        trace_sink, writer = collecting_trace, collecting_writer
    else:
        trace_sink = _Tee(
            FileSystemTraceSink(root=artifacts_root, run_id=allocator.run_id), collecting_trace
        )  # type: ignore[assignment]
        writer = _TeeWriter(FileSystemResultWriter(root=artifacts_root), collecting_writer)  # type: ignore[assignment]
    use_case = RunBacktest(
        runtime=runtime,
        context=context,
        output_sink=sink,
        allocator=allocator,
        feed=feed,
        execution_series=ExecutionSeriesView(
            snapshot=snapshot,
            series=EXECUTION_SERIES,
            allowed_partitions=allowed,
            partition_bars=partition_bars,
            schedule=schedules[EXECUTION_SERIES],
        ),
        calendar=calendar,
        risk_policy=RISK_POLICY,
        execution_policy=execution_policy,
        cost_model=COST_MODEL,
        conversion_policy=CONVERSION_POLICY,
        symbol_spec=SYMBOL_SPEC,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=calendar_ref,
        integrity=snapshot.report,
        trace_sink=trace_sink,
        result_writer=writer,
        code_digest=CODE_DIGEST,
        lock_digest=LOCK_DIGEST,
        env_digest=ENV_DIGEST,
        git_commit="0" * 40,
        git_dirty=False,
        timeframe_refs=MT5_TIMEFRAME_REFS,
        intrabar_series=FakeIntrabarSeries({level: bars_of.get(level, ()) for level in hierarchy}),
        minutes=parts.minutes,
        mt5_capability=parts.capability,
        publication_rows=published,
        history_resets=parts.history_resets,
    )
    result = use_case.run(config, compiled)
    assert collecting_writer.manifest is not None
    return Mt5RunOutput(
        result=result,
        manifest=collecting_writer.manifest,
        tables=dict(collecting_trace.tables),
        context=context,
    )


@dataclass
class _Tee:
    first: FileSystemTraceSink
    second: CollectingTraceSink

    def write(self, table: TraceTable, rows: tuple[object, ...]) -> None:
        self.first.write(table, rows)
        self.second.write(table, rows)


@dataclass
class _TeeWriter:
    first: FileSystemResultWriter
    second: CollectingResultWriter

    def write(self, result: BacktestResult, manifest: RunManifest) -> None:
        self.first.write(result, manifest)
        self.second.write(result, manifest)
