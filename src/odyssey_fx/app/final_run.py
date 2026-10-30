"""最終検証の run の本番の結線（`FinalRunner` の本番の実装。D09 v0.6 案 §9.10.14・§9.10.15。R4b）。

`app.final_validation` の段 2 (f)(g) と段 5 の口を、記録票と研究履歴の成果物・封印の snapshot から
組み立てる。業務の規則は持たない（再計算は `evaluation.application.final_recompute`、判定は
`evaluation.domain.final_validation`、run は `backtest`）。

- **段 2 (f)**（`prepare`）: 記録票と結末記録の改変・取り違えを確かめ、選定と判定を研究履歴の成果物
  から計算し直し（run はしない）、最終検証に進める試行（組ごと）とウォームアップの数え方を返す。
- **段 2 (g)**（`plan`）: 組ごとに最終検証の run の `RunConfig`（D09 §9.10.14 の表。封印の並び
  `sealed_reads` を含む。D06 v1.18 案 §3）を組み立て、予測 `ConfigDigest`・予測 `RunId` を計算する。
  run のカレンダーは封印の snapshot のカレンダーで、研究履歴の期間の取引セッションが記録票の
  カレンダーと同じことを確かめる（2026-10-30 の人間の決定「(a) 封印のカレンダー（版 2）で走らせる
  （推奨）」）。続けて戦略に依存するデータ能力検査（仮置き R4b-8。manifest と検査報告だけ）。
- **段 5**（`run`）: 記録票の snapshot（研究履歴）と関所の許可の並びが指す封印の snapshot から
  as-of ビュー・執行系列ビュー・公開フィードを組み立て（`FinalSources`）、組の 1 本の連続口座の run
  と評価を行い、終わりの決済の費用を引いた後の資産の点を足した資産の系列から判定の指標を数える
  （仮置き R4b-7）。

出力は件数・識別子・理由の識別子だけ。指標の値は `FinalRunOutput.summary` で最終検証の記録へ渡し、
画面には出さない（D09 §9.10.7）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Final

from odyssey_fx.app.composition import (
    ReadSurfaces,
    SnapshotInputs,
    _original_root,
    _require_matching_calendar,
    _run_use_case,
    _RunPlan,
    _tampering,
    _unit_run_config,
    calendar_ref_of,
    code_digest,
    env_digest,
    git_state,
    lock_digest,
    open_snapshot_inputs,
    symbol_spec_ref_of,
    trial_strategy,
)
from odyssey_fx.app.config.calendars import load_calendar
from odyssey_fx.app.config.experiment_v2 import TEXT_ROLES, ExperimentV2, experiment_v2_from_texts
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.final_validation import (
    SEALED_APPROVED_TABLES,
    CandidateFacts,
    FinalRunOutput,
    FinalRunPlan,
    FinalRunPreparation,
)
from odyssey_fx.app.mt5_run import reads_start, strategy_reads
from odyssey_fx.backtest.domain.policies import RunConfig, RunEndMode
from odyssey_fx.backtest.trace.manifest import RunManifest, config_digest_of
from odyssey_fx.backtest.trace.result import BacktestResult, RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.money import Money
from odyssey_fx.common.refs import CompiledStrategyRef, ConfigDigest
from odyssey_fx.common.refs import run_id as run_id_of
from odyssey_fx.common.symbol import SymbolSpecRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultRepository,
    read_experiment_manifest,
    read_experiment_outcome,
    read_path_records,
    read_selections,
    read_unit_records,
    run_directory,
)
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, equity_series
from odyssey_fx.evaluation.application.final_recompute import (
    RecordedResearch,
    recompute_research,
)
from odyssey_fx.evaluation.domain.experiment import ExperimentManifest, ExperimentOutcome
from odyssey_fx.evaluation.domain.final_validation import (
    WINDOW_START,
    FinalEvaluationPermit,
    FinalReason,
    FinalRunSummary,
    FinalWindow,
    PredictedRun,
    WindowRefusal,
)
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.path import path_metrics
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.asof import AsOfView, ExecutionSeriesView
from odyssey_fx.marketdata.application.mt5_observation import Mt5Observation
from odyssey_fx.marketdata.application.publication import build_feed, build_publication_log
from odyssey_fx.marketdata.application.snapshot_access import (
    FinalSources,
    ReadableSnapshot,
    VerifiedPartitionBars,
)
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.integrity import CheckKind, IntegrityReport, Severity
from odyssey_fx.marketdata.domain.publication_log import PublicationLog
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy, CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy

__all__ = ["CALENDARS_DIRECTORY", "RecordedFinalRunner"]

#: 封印の snapshot のカレンダーのファイルの置き場（`<id>_v<版>.yaml`。番号付きの仮置き）。
CALENDARS_DIRECTORY: Final = Path("configs") / "calendars"

_TINY: Final = timedelta(microseconds=1)


def _refusal(reason: FinalReason, detail: str) -> WindowRefusal:
    return WindowRefusal(reason, detail)


@dataclass(frozen=True, slots=True)
class _Record:
    """記録票・結末記録と、記録票の本文から読み直した実験設定。"""

    manifest: ExperimentManifest
    outcome: ExperimentOutcome
    loaded: ExperimentV2
    symbol_spec_ref: SymbolSpecRef
    timeframe_refs: tuple[TimeframeRef, ...]


@dataclass(frozen=True, slots=True)
class _Planned:
    """段 2 (g) で組み立てた組の run（段 5 が同じものを走らせる）。"""

    config: RunConfig
    config_digest: ConfigDigest
    compiled: CompiledStrategy
    calendar: TradingCalendar
    run_key: RunId

    @property
    def run_id(self) -> str:
        return str(self.run_key)


class _Segments:
    """記録票の本文から経路の区間の `ConfigDigest` と試行のコンパイルを組み立て直す。"""

    def __init__(self, record: _Record, compile_trial: Callable[[int], CompiledStrategy]) -> None:
        self._record = record
        self._compile = compile_trial

    def initial_balance(self, variant: Variant) -> Money:
        return self._record.loaded.body_of(variant).account.initial_balance

    def compiled_ref(self, trial_index: int) -> CompiledStrategyRef:
        return self._compile(trial_index).compiled_ref

    def segment_config_digest(
        self, variant: Variant, trial_index: int, interval: Interval, initial_balance: Money
    ) -> ConfigDigest:
        base = _unit_run_config(
            self._record.loaded.body_of(variant), self._compile(trial_index), interval
        )
        config = replace(
            base,
            account=replace(base.account, initial_balance=initial_balance),
            run_end=RunEndMode.CLOSE_ALL,
        )
        return config_digest_of(
            config,
            symbol_spec_ref=self._record.symbol_spec_ref,
            calendar_ref=calendar_ref_of(self._record.loaded.environment.calendar),
            timeframe_def_refs=self._record.timeframe_refs,
        )


class RecordedFinalRunner:
    """最終検証の run の本番の実装（`app.final_validation.FinalRunner`）。

    `snapshots_root` は snapshot の基点、`out_root` は最終検証の run と評価の成果物の基点
    （`<out_root>/runs/<run_id>/`）、`repo_root` は封印のカレンダー・承認済みの表の一覧と lock を
    読むリポジトリの根。
    """

    def __init__(self, *, repo_root: Path, snapshots_root: Path, out_root: Path) -> None:
        self._repo_root = Path(repo_root)
        self._snapshots_root = Path(snapshots_root)
        self._out_root = Path(out_root)
        self._records: dict[Path, _Record] = {}
        self._compiled: dict[tuple[Path, int], CompiledStrategy] = {}
        self._planned: dict[Variant, _Planned] = {}

    # --- 記録票 ----------------------------------------------------------------------------

    def _record(self, experiment_dir: Path) -> _Record:
        key = Path(experiment_dir).absolute()
        cached = self._records.get(key)
        if cached is not None:
            return cached
        manifest = read_experiment_manifest(key)
        outcome = read_experiment_outcome(key)
        if outcome is None:
            raise ConfigError(f"{experiment_dir} に結末記録が無い")
        loaded = experiment_v2_from_texts(
            {role: manifest.file(role).text for role in TEXT_ROLES},
            {item.role.removeprefix("symbol:"): item.text for item in manifest.symbol_files()},
            registry=INITIAL_CATALOG,
            metric_set_versions=METRIC_SET_VERSIONS,
        )
        environment = loaded.environment
        spec = environment.symbol_specs.get(loaded.body.execution_series.symbol)
        if spec is None:
            raise ConfigError("記録票の銘柄仕様に執行系列の銘柄が無い")
        record = _Record(
            manifest=manifest,
            outcome=outcome,
            loaded=loaded,
            symbol_spec_ref=symbol_spec_ref_of(spec),
            timeframe_refs=tuple(
                sorted((item.ref for item in environment.timeframe_defs.values()), key=str)
            ),
        )
        self._records[key] = record
        return record

    def _compile(self, experiment_dir: Path, record: _Record, trial_index: int) -> CompiledStrategy:
        key = (Path(experiment_dir).absolute(), trial_index)
        cached = self._compiled.get(key)
        if cached is not None:
            return cached
        plan = record.manifest.trials[trial_index]
        definition = trial_strategy(record.loaded.body.strategy, plan.assignment)
        timeframes = {item.ref: item for item in record.loaded.environment.timeframe_defs.values()}
        outcome = compile_strategy(definition, INITIAL_CATALOG, timeframes)
        if not isinstance(outcome, CompileSucceeded):
            raise KernelValueError(f"the trial {trial_index} does not compile any more")
        self._compiled[key] = outcome.compiled
        return outcome.compiled

    # --- 段 2 (f) --------------------------------------------------------------------------

    def prepare(
        self, facts: CandidateFacts, experiment_dir: Path
    ) -> FinalRunPreparation | WindowRefusal:
        try:
            record = self._record(experiment_dir)
        except (ConfigError, KernelValueError) as exc:
            return _refusal(
                FinalReason.RECOMPUTATION_MISMATCH,
                f"the manifest and the outcome cannot be rebuilt: {type(exc).__name__}",
            )
        tampered = _tampering(record.manifest, record.outcome)
        if tampered is not None:
            return _refusal(FinalReason.RECOMPUTATION_MISMATCH, tampered)
        root = _original_root(Path(experiment_dir))
        if root is None:
            return _refusal(
                FinalReason.RECOMPUTATION_MISMATCH, "the artifacts root cannot be derived"
            )
        try:
            recorded = Paired.build(lambda variant: _recorded(Path(experiment_dir), variant))
        except KernelValueError as exc:
            return _refusal(
                FinalReason.RECOMPUTATION_MISMATCH,
                f"the research records cannot be read: {type(exc).__name__}",
            )
        repository = FileSystemResultRepository(root=root)
        outcome = recompute_research(
            manifest=record.manifest,
            outcome=record.outcome,
            recorded=recorded,
            repository=repository,
            evaluator=EvaluateRun(evaluation_code_digest=code_digest()),
            calendar=record.loaded.environment.calendar,
            segments=_Segments(record, lambda index: self._compile(experiment_dir, record, index)),
        )
        if isinstance(outcome, str):
            return _refusal(FinalReason.RECOMPUTATION_MISMATCH, outcome)
        compiled = outcome.map(lambda _, index: self._compile(experiment_dir, record, index))
        calendar = record.loaded.environment.calendar
        timeframe_defs = record.loaded.environment.timeframe_defs

        def warmup_at(start: UtcTime) -> timedelta:
            return _warmup(compiled, calendar, timeframe_defs, start)

        return FinalRunPreparation(final_trials=outcome, warmup=timedelta(0), warmup_at=warmup_at)

    # --- 段 2 (g) --------------------------------------------------------------------------

    def plan(
        self, facts: CandidateFacts, window: FinalWindow, preparation: FinalRunPreparation
    ) -> FinalRunPlan | WindowRefusal:
        store = ParquetSnapshotStore(root=self._snapshots_root)
        experiment_dir = _experiment_dir_of(self._records, facts)
        if experiment_dir is None:
            return _refusal(FinalReason.RECOMPUTATION_MISMATCH, "prepare was not called")
        record = self._records[experiment_dir]
        sealed: dict[str, SnapshotManifest] = {}
        try:
            for variant in VARIANTS:
                for item in window.reads.of(variant):
                    if item.snapshot_id not in sealed:
                        sealed[item.snapshot_id] = store.read_manifest(item.snapshot_id)
            opened = {
                variant: store.open_readable(facts.record_snapshots.of(variant))
                for variant in VARIANTS
            }
            research = {variant: item.manifest for variant, item in opened.items()}
            reports = {variant: item.report for variant, item in opened.items()}
        except Exception as exc:  # noqa: BLE001 - 読めなければ能力検査の不合格
            return _refusal(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"a snapshot manifest cannot be read ({type(exc).__name__})",
            )
        calendar = self._sealed_calendar(sealed.values())
        if isinstance(calendar, WindowRefusal):
            return calendar
        mismatch = _research_sessions_differ(
            record.loaded.environment.calendar, calendar, research.values()
        )
        if mismatch is not None:
            return _refusal(FinalReason.DATA_CAPABILITY_NOT_MET, mismatch)
        code, lock, env = code_digest(), lock_digest(self._repo_root), env_digest()
        calendar_ref = calendar_ref_of(calendar)
        interval = Interval(start=window.scoring_start, end=window.consumption.end)
        planned: dict[Variant, _Planned] = {}
        for variant in VARIANTS:
            compiled = self._compile(experiment_dir, record, preparation.final_trials.of(variant))
            base = _unit_run_config(record.loaded.body_of(variant), compiled, interval)
            config = replace(
                base, run_end=RunEndMode.CLOSE_ALL, sealed_reads=window.reads.of(variant)
            )
            digest = config_digest_of(
                config,
                symbol_spec_ref=record.symbol_spec_ref,
                calendar_ref=calendar_ref,
                timeframe_def_refs=record.timeframe_refs,
            )
            planned[variant] = _Planned(
                config=config,
                config_digest=digest,
                compiled=compiled,
                calendar=calendar,
                run_key=run_id_of(digest, code, lock, env),
            )
            refusal = _strategy_capability(
                record, variant, compiled, window, research[variant], reports[variant]
            )
            if refusal is not None:
                return refusal
        self._planned = planned
        # 研究履歴の partition は、並びがその系列の封印の partition を持つ系列だけを読む（R4b の
        # 仮置き: 封印の期間に足の無い系列は run 区間を覆えない。公開フィードの覆いの検査）。
        research_partitions = Paired.build(
            lambda variant: tuple(
                (facts.record_snapshots.of(variant), item.partition_id)
                for item in research[variant].partitions
                if str(item.partition_id) in record.manifest.allowed_partitions.of(variant)  # type: ignore[union-attr]
                and item.partition_id.series
                in {read.partition_id.series for read in window.reads.of(variant)}
            )
        )
        return FinalRunPlan(
            predicted=Paired.build(
                lambda variant: PredictedRun(
                    config_digest=planned[variant].config_digest.digest.hex,
                    run_id=planned[variant].run_id,
                )
            ),
            research_partitions=research_partitions,
            allowed=Paired.build(
                lambda variant: frozenset(
                    {
                        *research_partitions.of(variant),
                        *(
                            (item.snapshot_id, item.partition_id)
                            for item in planned[variant].config.sealed_reads
                        ),
                    }
                )
            ),
            run_directories=tuple(
                run_directory(self._out_root, planned[variant].run_key) for variant in VARIANTS
            ),
        )

    def _sealed_calendar(
        self, manifests: Iterable[SnapshotManifest]
    ) -> TradingCalendar | WindowRefusal:
        """封印の snapshot のカレンダー（すべて同じ版。2026-10-30 の人間の決定 (a)）。"""
        found = {
            (item.conversion.calendar_id, item.conversion.calendar_version) for item in manifests
        }
        if len(found) != 1:
            return _refusal(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"the sealed snapshots name {len(found)} calendars; one is required",
            )
        ((name, version),) = found
        path = self._repo_root / CALENDARS_DIRECTORY / f"{name}_v{version}.yaml"
        try:
            calendar = load_calendar(path)
        except (ConfigError, KernelValueError, OSError) as exc:
            return _refusal(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"the calendar {name}@v{version} of the sealed snapshots cannot be read"
                f" ({type(exc).__name__})",
            )
        if (calendar.id, calendar.version) != (name, version):
            return _refusal(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"{path.name} is not the calendar {name}@v{version}",
            )
        return calendar

    # --- 段 5 ------------------------------------------------------------------------------

    def run(
        self,
        facts: CandidateFacts,
        plan: FinalRunPlan,
        permit: FinalEvaluationPermit,
        variant: Variant,
    ) -> FinalRunOutput:
        planned = self._planned.get(variant)
        if planned is None or planned.run_id != permit.predicted.of(variant).run_id:
            raise KernelValueError("the final-validation run was not planned for this permit")
        experiment_dir = _experiment_dir_of(self._records, facts)
        if experiment_dir is None:  # pragma: no cover - plan の後
            raise KernelValueError("prepare was not called")
        record = self._records[experiment_dir]
        grant = permit.grant_of(variant)
        if grant.reads != planned.config.sealed_reads:
            raise KernelValueError("the permit reads differ from the run configuration (R4b-3)")
        environment = record.loaded.environment
        store = ParquetSnapshotStore(root=self._snapshots_root)
        research = open_snapshot_inputs(
            snapshots_root=self._snapshots_root,
            snapshot_id=facts.record_snapshots.of(variant),
            calendar=environment.calendar,
            timeframe_defs=environment.timeframe_defs,
        )
        readables: dict[str, ReadableSnapshot] = {}
        sealed_bars: dict[PartitionId, tuple[Bar, ...]] = {}
        for item in grant.reads:
            readable = readables.get(item.snapshot_id)
            if readable is None:
                readable = store.open_readable(item.snapshot_id)
                _require_matching_calendar(readable.manifest, planned.calendar, item.snapshot_id)
                readables[item.snapshot_id] = readable
            sealed_bars[item.partition_id] = tuple(
                store.read_partition(item.snapshot_id, item.partition_id, read_permit=grant)
            )
        sources = FinalSources(grant=grant, snapshots=readables)
        schedules = {
            series: SeriesSchedule(
                series=series, timeframe_def=schedule.timeframe_def, calendar=planned.calendar
            )
            for series, schedule in research.schedules.items()
        }
        allowed_research = (
            frozenset(item for _, item in plan.research_partitions.of(variant))
            & research.allowed_partitions
        )
        inputs = SnapshotInputs(
            snapshot=research.snapshot,
            allowed_partitions=allowed_research | frozenset(sealed_bars),
            partition_bars={
                **{key: research.partition_bars[key] for key in allowed_research},
                **sealed_bars,
            },
            schedules=schedules,
        )
        merged = IntegrityReport(
            results=(
                *research.snapshot.report.results,
                *(
                    result
                    for name in sorted(readables)
                    for result in readables[name].report.results
                ),
            )
        )
        tables = {
            readable.manifest.conversion.mt5.time_rule_table
            for readable in readables.values()
            if readable.manifest.conversion.mt5 is not None
        }
        observation = None
        if research.snapshot.manifest.conversion.mt5 is not None:
            if len(tables) != 1:
                raise KernelValueError("the sealed snapshots name more than one time-rule table")
            observation = Mt5Observation.from_report(merged, next(iter(tables)))
        experiment = record.loaded.body_of(variant).with_run_interval(planned.config.run_interval)
        verified = VerifiedPartitionBars(
            inputs.snapshot,
            inputs.allowed_partitions,
            inputs.partition_bars,
            label="AsOfView",
            final_sources=sources,
        )
        publication_log = (
            PublicationLog()
            if experiment.delay_scenario is None
            else build_publication_log(
                inputs.snapshot,
                inputs.allowed_partitions,
                verified,
                schedules,
                experiment.delay_scenario,
                final_sources=sources,
            )
        )
        surfaces = ReadSurfaces(
            verified=verified,
            market_data=AsOfView(
                snapshot=inputs.snapshot,
                allowed_partitions=inputs.allowed_partitions,
                schedules=schedules,
                partition_bars=verified,
                publication_log=publication_log,
                final_sources=sources,
            ),
            execution_view=ExecutionSeriesView(
                snapshot=inputs.snapshot,
                series=experiment.execution_series,
                allowed_partitions=inputs.allowed_partitions,
                partition_bars=verified,
                schedule=schedules[experiment.execution_series],
                final_sources=sources,
            ),
            feed=partial(build_feed, final_sources=sources),
            observation=observation,
            integrity=merged,
            approved_list=SEALED_APPROVED_TABLES,
        )
        spec = environment.symbol_specs[experiment.execution_series.symbol]
        code, lock, env = code_digest(), lock_digest(self._repo_root), env_digest()
        commit, dirty = git_state(self._repo_root)
        run_plan = _RunPlan(
            experiment=experiment,
            calendar=planned.calendar,
            inputs=inputs,
            spec=spec,
            compiled=planned.compiled,
            config=planned.config,
            config_digest=planned.config_digest,
            symbol_spec_ref=record.symbol_spec_ref,
            calendar_ref=calendar_ref_of(planned.calendar),
            timeframe_refs=record.timeframe_refs,
            code=code,
            lock=lock,
            environment=env,
            run_id=run_id_of(planned.config_digest, code, lock, env),
            git_commit=commit,
            git_dirty=dirty,
            publication_log=publication_log,
            repo_root=self._repo_root,
            surfaces=surfaces,
        )
        result = _run_use_case(run_plan, self._out_root, replace=False).run(
            planned.config, planned.compiled
        )
        repository = FileSystemResultRepository(root=self._out_root)
        report = EvaluateRun(evaluation_code_digest=code).evaluate(
            result, repository, record.manifest.metric_set_version, planned.calendar
        )
        repository.write_evaluation(report, report.rows)
        manifest = repository.read_manifest(result.run_id)
        if not isinstance(manifest, RunManifest):
            raise KernelValueError("the run manifest of the final-validation run cannot be read")
        summary = _summary(result, report.status, repository, planned.config)
        return FinalRunOutput(
            run_id=str(result.run_id),
            run_evaluation_id=str(report.manifest.run_evaluation_id),
            result_digest=report.manifest.result_digest.hex,
            config_digest=manifest.config_digest.digest.hex,
            metric_set_version=report.manifest.metric_set_version,
            summary=summary,
        )


# --- 補助 ------------------------------------------------------------------------------------


def _recorded(directory: Path, variant: Variant) -> RecordedResearch:
    _, runs = read_unit_records(directory, variant)
    starts, ends = read_path_records(directory, variant)
    return RecordedResearch(
        runs=runs,
        selections=read_selections(directory, variant),
        segment_starts=starts,
        segment_ends=ends,
    )


def _experiment_dir_of(records: Mapping[Path, _Record], facts: CandidateFacts) -> Path | None:
    for path, record in records.items():
        if record.manifest.experiment_id.hex == facts.key.experiment_id:
            return path
    return None


def _summary(
    result: BacktestResult,
    status: EvaluationStatus,
    repository: FileSystemResultRepository,
    config: RunConfig,
) -> FinalRunSummary:
    """組の run の要約（仮置き R4b-7: 終わりの決済の費用を引いた後の資産の点を系列に足す）。"""
    summaries = result.summaries
    if (
        result.status is not RunStatus.COMPLETED
        or status is not EvaluationStatus.COMPLETED
        or summaries is None
    ):
        raise KernelValueError(
            f"the final-validation run ended {result.status.value} / evaluation {status.value}"
        )
    initial = config.account.initial_balance
    settled = initial + summaries.realized + summaries.hypothetical_closed
    equities = equity_series(repository, result.run_id)
    if isinstance(equities, str):
        raise KernelValueError(equities)
    metrics = path_metrics(initial, [*equities, settled.amount], settled)
    return FinalRunSummary(metrics=metrics, trade_count=result.trade_count)


def _warmup(
    compiled: Paired[CompiledStrategy],
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    start: UtcTime,
) -> timedelta:
    """`start` から戦略の窓が求める足がそろうまでの長さ（番号付きの仮置き）。

    本数の窓は `start` 以後の予定の足を数え、経過時間の窓は経過時間に足 1 本ぶんを足す。両組の
    最終検証に進める試行の窓の最大。
    """
    longest = timedelta(0)
    for _, strategy in compiled.items():
        for series, reads in strategy_reads(strategy).items():
            definition = timeframe_defs.get(series.timeframe.id)
            if definition is None:
                continue
            schedule = SeriesSchedule(
                series=series,
                timeframe_def=definition,
                calendar=calendar,
            )
            for item in reads:
                end = (
                    _after_bars(schedule, start, item)
                    if isinstance(item, int)
                    else _after_bars(schedule, start + item, 1)
                )
                longest = max(longest, end - start)
    return longest


def _after_bars(schedule: SeriesSchedule, start: UtcTime, count: int) -> UtcTime:
    """`start` 以後の予定の足を `count` 本数えた最後の足の終わり。"""
    definition = schedule.timeframe_def
    span = timedelta(days=7)
    for _ in range(520):
        starts = schedule.calendar.expected_bar_starts(
            definition, Interval(start=start, end=start + span)
        )
        if len(starts) >= count:
            interval = definition.expected_interval(schedule.calendar, starts[count - 1])
            return start if interval is None else interval.end
        span *= 2
        if span > timedelta(days=3650):
            break
    return start


def _research_sessions_differ(
    record: TradingCalendar, sealed: TradingCalendar, manifests: Iterable[SnapshotManifest]
) -> str | None:
    """研究履歴の期間で、記録票のカレンダーと封印のカレンダーの取引セッションが同じか。

    2026-10-30 の人間の決定（最終検証の run のカレンダー (a)）の帰結: 違えば段 2 で止める。
    期間は記録票の snapshot の系列の被覆の始まりから 2024-01-01 まで。
    """
    starts = [item.covered_interval.start for manifest in manifests for item in manifest.series]
    if not starts:
        return "the record snapshot records no series"
    start = min(starts, key=lambda value: value.value)
    if not start < WINDOW_START:
        return None
    period = Interval(start=start, end=WINDOW_START)
    if record.sessions(period) != sealed.sessions(period):
        return (
            f"the trading sessions of the sealed calendar {sealed.id}@v{sealed.version} differ"
            f" from the record calendar {record.id}@v{record.version} in the research period"
            f" {period} (2026-10-30 の人間の決定 (a))"
        )
    return None


def _strategy_capability(
    record: _Record,
    variant: Variant,
    compiled: CompiledStrategy,
    window: FinalWindow,
    research: SnapshotManifest,
    research_report: IntegrityReport,
) -> WindowRefusal | None:
    """戦略に依存するデータ能力検査（仮置き R4b-8。manifest と検査報告だけ。足を読まない）。

    戦略が読む系列・執行系列・解像度階層の系列について、(1) 記録票の snapshot に系列がある、
    (2) 使える窓のすべての取引週に、その組の並びの partition がある、(3) 戦略の窓が遡る履歴の分
    （消費する範囲の始まりより前）に、記録票の snapshot の検査報告の取得不足の区間・ERROR が
    重ならない、を確かめる。封印の snapshot の取得不足・時刻の規則の表は戦略によらない部分
    （段 2 (e)）が消費する範囲のすべての系列について確かめ済み。
    """
    body = record.loaded.body_of(variant)
    series: set[SeriesId] = set(strategy_reads(compiled))
    series.add(body.execution_series)
    series.update(body.execution_policy.resolution_hierarchy.levels)
    recorded = {item.series_id for item in research.series}
    missing = sorted(str(item) for item in series - recorded)
    if missing:
        return _refusal(
            FinalReason.DATA_CAPABILITY_NOT_MET,
            f"the {variant.value} record snapshot lacks the series {missing} the run reads",
        )
    reads = window.reads.of(variant)
    for item in sorted(series, key=str):
        weeks = sum(1 for read in reads if read.partition_id.series == item)
        if weeks != window.weeks:
            return _refusal(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"the {variant.value} reads cover {weeks} of {window.weeks} weeks of {item}",
            )
    calendar = record.loaded.environment.calendar
    definitions = record.loaded.environment.timeframe_defs
    reads_of = strategy_reads(compiled)
    begin = window.consumption.start
    for item in sorted(series, key=str):
        definition = definitions.get(item.timeframe.id)
        if definition is None or item not in reads_of:
            continue
        schedule = SeriesSchedule(series=item, timeframe_def=definition, calendar=calendar)
        begin = min(
            begin,
            reads_start(schedule, reads_of[item], window.scoring_start),
            key=lambda value: value.value,
        )
    if not begin < window.consumption.start:
        return None
    history = Interval(start=begin, end=window.consumption.start)
    symbols = {item.symbol for item in series}
    found = [
        result
        for result in research_report.results
        if result.series.symbol in symbols
        and (
            result.severity is Severity.ERROR
            or (
                result.kind is CheckKind.ACQUISITION_SHORTFALL
                and result.interval is not None
                and result.interval.overlaps(history)
            )
        )
    ]
    if found:
        return _refusal(
            FinalReason.DATA_CAPABILITY_NOT_MET,
            f"{len(found)} shortfall(s) or error(s) of the {variant.value} record snapshot fall in"
            f" the history the run reads ({history})",
        )
    return None
