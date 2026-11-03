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
- **円以外の決済通貨の候補**（D06 v1.20 案 §19.14）: 段 2 (g) で、`RunConfig` の換算の snapshot を
  記録票のその組の換算の snapshot にし、並びに換算用の読み取りの対象（USDJPY の 1 分足）を入れる。
  研究期間の側は、換算の snapshot を記録票の snapshot と同じ組の照合（D06 §19.2）で、記録票の
  カレンダー（版 1）と照合し、研究履歴の期間の取引セッションの確かめを換算の snapshot にも当てる
  （人間の決定 (22) (a)）。封印の側は、並びの各週の換算用の読み取りの対象と執行の通貨ペアの
  partition の snapshot の manifest を照合する（同じ組・同じ取得の回。仮置き C1-14）。段 5 では、
  換算の脚の 1 分足を、記録票の換算の snapshot（研究履歴）と並びの封印の partition から供給する。
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
from odyssey_fx.app.conversion import (
    ConversionSnapshotMismatch,
    ConversionSource,
    conversion_supply,
    require_conversion_pairing,
)
from odyssey_fx.app.final_validation import (
    SEALED_APPROVED_TABLES,
    CandidateFacts,
    FinalRunOutput,
    FinalRunPlan,
    FinalRunPreparation,
)
from odyssey_fx.app.mt5_run import reads_start, strategy_reads
from odyssey_fx.backtest.application.run_backtest import ConversionSupply
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
        conversion_refs = {
            variant: record.loaded.body_of(variant).conversion_snapshot_ref for variant in VARIANTS
        }
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
            converting = {
                variant: store.open_readable(str(ref.snapshot_id)).manifest
                for variant, ref in conversion_refs.items()
                if ref is not None
            }
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
        refusal = _research_conversion_refusal(record, calendar, research, converting)
        if refusal is not None:
            return refusal
        refusal = _sealed_conversion_refusal(window, sealed)
        if refusal is not None:
            return refusal
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
        # 円以外の決済通貨の候補は、記録票の換算の snapshot の研究履歴の partition も同じ規則で
        # 読む（研究履歴の期間の換算。D06 v1.20 案 §19.14、D09 §9.10.3 の 8 の v0.7 案）。
        conversion_allowed = record.manifest.conversion_allowed_partitions

        def research_of(variant: Variant) -> tuple[tuple[str, PartitionId], ...]:
            series = {read.partition_id.series for read in window.reads.of(variant)}
            own = tuple(
                (facts.record_snapshots.of(variant), item.partition_id)
                for item in research[variant].partitions
                if str(item.partition_id) in record.manifest.allowed_partitions.of(variant)  # type: ignore[union-attr]
                and item.partition_id.series in series
            )
            ref = conversion_refs[variant]
            if ref is None or not isinstance(conversion_allowed, Paired):
                return own
            return own + tuple(
                (str(ref.snapshot_id), item.partition_id)
                for item in converting[variant].partitions
                if str(item.partition_id) in conversion_allowed.of(variant)
                and item.partition_id.series in series
            )

        research_partitions = Paired.build(research_of)
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
        # 換算用の読み取りの対象の足は換算の脚の供給にだけ渡す（執行の側の入力に入れない。
        # D06 v1.20 案 §19.14）。
        conversion_reads = {partition for _, partition in permit.window.conversion}
        conversion_bars = {
            key: bars for key, bars in sealed_bars.items() if key in conversion_reads
        }
        sealed_bars = {
            key: bars for key, bars in sealed_bars.items() if key not in conversion_reads
        }
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
        conversion = self._conversion(
            record,
            planned,
            plan.research_partitions.of(variant),
            conversion_bars,
            sources,
            readables,
            conversion_reads,
            ExecutionSource(
                snapshot=inputs.snapshot,
                allowed_partitions=inputs.allowed_partitions,
                observation=observation,
            ),
        )
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
            conversion=None if conversion is None else conversion(verified),
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

    def _conversion(
        self,
        record: _Record,
        planned: _Planned,
        research_partitions: tuple[tuple[str, PartitionId], ...],
        conversion_bars: Mapping[PartitionId, tuple[Bar, ...]],
        sources: FinalSources,
        readables: Mapping[str, ReadableSnapshot],
        conversion_reads: frozenset[PartitionId] | set[PartitionId],
        execution: ExecutionSource,
    ) -> Callable[[VerifiedPartitionBars], ConversionSupply] | None:
        """換算の脚の供給（円以外の決済通貨の候補だけ。D06 v1.20 案 §19.14）。

        脚の 1 分足は、記録票の換算の snapshot の研究履歴の partition（研究履歴の期間）と、関所の
        許可の並びの換算用の読み取りの対象（封印の期間）から読む。どちらも読み取りの関門を通す
        （並びの partition は並びが指す封印の snapshot の manifest で照合する）。執行の通貨ペアの
        脚（USDCHF 型）は執行の側の入力（研究履歴と並びの消費の対象）から読む。取得不足の区間は、
        研究履歴の snapshot の検査報告に並びの封印の snapshot の検査報告を合わせた索引から引く。
        脚の観測の参照の snapshot は記録票の換算の snapshot（番号付きの仮置き）。
        """
        ref = planned.config.conversion_snapshot_ref
        if ref is None:
            return None
        environment = record.loaded.environment
        conversion_id = str(ref.snapshot_id)
        research = open_snapshot_inputs(
            snapshots_root=self._snapshots_root,
            snapshot_id=conversion_id,
            calendar=environment.calendar,
            timeframe_defs=environment.timeframe_defs,
        )
        allowed_research = (
            frozenset(item for snapshot, item in research_partitions if snapshot == conversion_id)
            & research.allowed_partitions
        )
        allowed = allowed_research | frozenset(conversion_bars)
        verified = VerifiedPartitionBars(
            research.snapshot,
            allowed,
            {
                **{key: research.partition_bars[key] for key in allowed_research},
                **conversion_bars,
            },
            label="ConversionQuotes",
            final_sources=sources,
        )
        sealed_sources = sorted(
            {
                item.snapshot_id
                for item in sources.grant.reads
                if item.partition_id in conversion_reads
            }
        )
        merged = IntegrityReport(
            results=(
                *research.snapshot.report.results,
                *(result for name in sealed_sources for result in readables[name].report.results),
            )
        )
        tables = {
            readables[name].manifest.conversion.mt5.time_rule_table  # type: ignore[union-attr]
            for name in sealed_sources
        }
        mt5 = research.snapshot.manifest.conversion.mt5
        table = next(iter(tables)) if len(tables) == 1 else None
        if table is None and mt5 is not None:
            table = mt5.time_rule_table
        observation = None if table is None else Mt5Observation.from_report(merged, table)
        minute = next(
            (item for item in record.timeframe_refs if item.id == "1m"),
            planned.config.execution_series.timeframe,
        )

        def supply(execution_bars: VerifiedPartitionBars) -> ConversionSupply:
            return conversion_supply(
                execution=ConversionSource(
                    snapshot=execution.snapshot,
                    allowed_partitions=execution.allowed_partitions,
                    partition_bars=execution_bars,
                    observation=execution.observation,
                ),
                conversion=ConversionSource(
                    snapshot=research.snapshot,
                    allowed_partitions=allowed,
                    partition_bars=verified,
                    observation=observation,
                ),
                fallback_timeframe=minute,
            )

        return supply


@dataclass(frozen=True, slots=True)
class ExecutionSource:
    """最終検証の run の執行の側の入力（換算の脚の供給に渡す。USDCHF 型の 1 本目の脚）。"""

    snapshot: ReadableSnapshot
    allowed_partitions: frozenset[PartitionId]
    observation: Mt5Observation | None


# --- 補助 ------------------------------------------------------------------------------------


def _research_conversion_refusal(
    record: _Record,
    sealed_calendar: TradingCalendar,
    research: Mapping[Variant, SnapshotManifest],
    converting: Mapping[Variant, SnapshotManifest],
) -> WindowRefusal | None:
    """研究期間の側の換算の snapshot の確かめ（D06 v1.20 案 §19.14、人間の決定 (22) (a)）。

    組ごとに、換算の snapshot を記録票の取引する通貨ペアの snapshot と同じ組の照合（D06 §19.2 の
    6 項目）で比べる。照合の行 4 の「run に渡したカレンダー」は記録票のカレンダー（研究期間の
    snapshot の版 1）と読む。続けて、研究履歴の期間の取引セッションが記録票のカレンダーと run の
    カレンダー（封印の snapshot の版）とで同じことを、換算の snapshot にも当てる。違えば止める
    （終了コード 7。理由の識別子は記録票の snapshot の確かめと同じ `DATA_CAPABILITY_NOT_MET`）。
    """
    calendar = record.loaded.environment.calendar
    for variant, manifest in sorted(converting.items(), key=lambda pair: pair[0].value):
        try:
            require_conversion_pairing(
                research[variant],
                manifest,
                calendar_id=calendar.id,
                calendar_version=calendar.version,
                execution_id=str(research[variant].snapshot_id()),
                conversion_id=str(manifest.snapshot_id()),
            )
        except ConversionSnapshotMismatch as exc:
            return _refusal(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"the {variant.value} conversion snapshot of the record: {exc}",
            )
        mismatch = _research_sessions_differ(calendar, sealed_calendar, (manifest,))
        if mismatch is not None:
            return _refusal(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"the {variant.value} conversion snapshot of the record: {mismatch}"
                " (人間の決定 (22) (a))",
            )
    return None


def _sealed_conversion_refusal(
    window: FinalWindow, sealed: Mapping[str, SnapshotManifest]
) -> WindowRefusal | None:
    """並びの各週の換算用の読み取りの対象と執行の通貨ペアの partition の照合（仮置き C1-14）。

    組ごと・週ごとに、換算用の読み取りの対象（USDJPY の 1 分足）を読む snapshot と、同じ週の
    執行の通貨ペアの partition を読む snapshot の manifest（価格を含まない）を次で比べる:
    (1) 組（時刻の規則の表の `variant`）、(2) 時刻の規則の表（識別・版・SHA-256）、(3) 取得の回
    （取得記録の SHA-256 `conversion.mt5.fetch_manifest_sha256`。封印の snapshot は取得の回 ×
    通貨 × 組ごとに作られ、取得記録 `manifest.json` は取得の 1 回に 1 つ。D03 §16.3.3・§16.3.6）、
    (4) カレンダーと集約規則の版、(5) 承認。食い違えば `CONVERSION_PARTITION_MISMATCH`（終了
    コード 7。何も書かない）。
    """
    if not window.conversion:
        return None
    converting = set(window.conversion)
    for variant in VARIANTS:
        by_week: dict[str, tuple[set[str], set[str]]] = {}
        for item in window.reads.of(variant):
            start = str(item.partition_id.period_start)
            executed, converted = by_week.setdefault(start, (set(), set()))
            if (item.snapshot_id, item.partition_id) in converting:
                converted.add(item.snapshot_id)
            else:
                executed.add(item.snapshot_id)
        for week, (executed, converted) in sorted(by_week.items()):
            for left in sorted(converted):
                for right in sorted(executed):
                    problems = _acquisition_problems(sealed[left], sealed[right])
                    if problems:
                        return _refusal(
                            FinalReason.CONVERSION_PARTITION_MISMATCH,
                            f"the {variant.value} conversion-read partition of the week {week}"
                            f" in {left} does not pair with the executed partition in {right}:"
                            f" {'; '.join(problems)} (D06 §19.14、仮置き C1-14)",
                        )
    return None


def _acquisition_problems(conversion: SnapshotManifest, execution: SnapshotManifest) -> list[str]:
    """同じ組・同じ取得の回の 5 項目の食い違い（識別子・版・組の名前だけ。価格を含まない）。"""
    problems: list[str] = []
    left, right = conversion.conversion.mt5, execution.conversion.mt5
    if left is None or right is None:
        return ["route: both snapshots must be OANDA MT5 snapshots"]
    if left.time_rule_table.variant != right.time_rule_table.variant:
        problems.append(
            f"variant: {left.time_rule_table.variant.value} vs"
            f" {right.time_rule_table.variant.value}"
        )
    tables = [
        (item.table_id, item.version, item.sha256)
        for item in (left.time_rule_table, right.time_rule_table)
    ]
    if tables[0] != tables[1]:
        problems.append(
            f"time rule table: {tables[0][0]} v{tables[0][1]} vs {tables[1][0]} v{tables[1][1]}"
        )
    if left.fetch_manifest_sha256 != right.fetch_manifest_sha256:
        problems.append(
            f"acquisition round: fetch manifest {left.fetch_manifest_sha256[:12]} vs"
            f" {right.fetch_manifest_sha256[:12]}"
        )
    calendars = [
        (item.calendar_id, item.calendar_version, item.aggregation_rule_version)
        for item in (conversion.conversion, execution.conversion)
    ]
    if calendars[0] != calendars[1]:
        problems.append(
            f"calendar / aggregation: {calendars[0][0]}@v{calendars[0][1]} {calendars[0][2]} vs"
            f" {calendars[1][0]}@v{calendars[1][1]} {calendars[1][2]}"
        )
    if not conversion.is_approved or not execution.is_approved:
        problems.append("approval: both snapshots must be approved")
    return problems


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
