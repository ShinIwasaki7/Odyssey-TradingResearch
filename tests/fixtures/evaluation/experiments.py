"""実験の記録票・結末記録と `RunExperiment` の単体テスト用の部品（D07 §19〜§21）。

- `make_manifest`: 記録票を1つ作る（識別子は `experiment_id_of` で計算する）。
- `Workbench`: `RunExperiment` のポート3つ（記録票の保存・run・結果の読み書き）の偽物を束ね、
  呼ばれた順を `log` に残す。記録票が run より前に保存されること（D07 §19.4）を順序で確かめる
  ために、保存と run と評価の書き込みを同じ記録に並べる。

評価そのものは本物の `EvaluateRun` を使い、判断履歴は T01 の人工の表（`traces.t01_tables`）で
与える。run は偽物で、呼ばれると T01 の結果 DTO を返し、その run の成果物が「ある」状態にする。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId, RunId
from odyssey_fx.common.refs import (
    CodeDigest,
    ContentDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    StrategyRef,
)
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun
from odyssey_fx.evaluation.application.manifest import EvaluationTable, RunEvaluationId
from odyssey_fx.evaluation.application.ports import (
    EvaluationReadFailure,
    ManifestReadFailure,
    ManifestSaveResult,
    ResultReadFailure,
    StoredEvaluation,
    TableReadResult,
    TraceColumnSpec,
)
from odyssey_fx.evaluation.application.run_experiment import PreparedExperiment, RunExperiment
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentManifest,
    ExperimentOutcome,
    ResolvedFile,
    experiment_id_of,
)
from odyssey_fx.evaluation.domain.research_policy import (
    ComplexityLimits,
    ComplexityMeasures,
    PolicyCheckResult,
    check_complexity,
    check_hypothesis,
    check_research_history_only,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy
from tests.fixtures.backtest.harness import compiled_strategy
from tests.fixtures.evaluation import traces

__all__ = [
    "EXPERIMENT_VALUES",
    "LIMITS",
    "Workbench",
    "make_manifest",
]

#: Q7 決定の上限（D07 §20.2）。
LIMITS = ComplexityLimits(component_kinds=30, instances=36, parameters=33, decision_outputs=18)

#: 検証戦略 A の計測値（D07 §20.4 の表）。
MEASURES = ComplexityMeasures(component_kinds=5, instances=6, parameters=6, decision_outputs=1)

#: 実験設定の値（YAML を読み込んだ形。パスを持つキーも含める。識別子の計算が除く）。
EXPERIMENT_VALUES: Mapping[str, Any] = {
    "schema_version": 2,
    "id": "strategy_a_t01",
    "version": 1,
    "hypothesis": "T01 の経路どおりに記録する",
    "strategy": "configs/strategies/strategy_a_v1.yaml",
    "environment": {
        "calendar": "configs/calendars/fx_ny17_v1.yaml",
        "timeframes": "configs/calendars/timeframes_v1.yaml",
        "symbols": "configs/symbols",
    },
    "seed": 0,
}

_RESEARCH_HISTORY = {"USDJPY_15m_bid/RESEARCH_HISTORY": AccessClass.RESEARCH_HISTORY}


def _digest(seed: str) -> ContentDigest:
    return digest(seed)


def _files() -> tuple[ResolvedFile, ...]:
    return (
        ResolvedFile.of("experiment", "schema_version: 2\n"),
        ResolvedFile.of("strategy", "schema_version: 1\n"),
        ResolvedFile.of("research_policy", "schema_version: 1\n"),
        ResolvedFile.of("calendar", "schema_version: 1\n"),
        ResolvedFile.of("timeframes", "schema_version: 1\n"),
        ResolvedFile.of("symbol:USDJPY", "schema_version: 1\n"),
    )


def make_manifest(
    *,
    run_manifest: RunManifest | None = None,
    hypothesis: str = "T01 の経路どおりに記録する",
    allowed_partitions: Mapping[str, AccessClass] | None = None,
    measures: ComplexityMeasures = MEASURES,
    values: Mapping[str, Any] = EXPERIMENT_VALUES,
    code_seed: str = "code",
    resolved_files: tuple[ResolvedFile, ...] | None = None,
    pre_run_checks: tuple[PolicyCheckResult, ...] | None = None,
) -> ExperimentManifest:
    """記録票を1つ作る。事前検査は与えた値から本物の規則で計算する（D07 §20.3）。"""
    manifest = run_manifest or traces.manifest_for()
    allowed = dict(_RESEARCH_HISTORY if allowed_partitions is None else allowed_partitions)
    checks = pre_run_checks or (
        check_hypothesis(hypothesis),
        check_research_history_only(allowed),
        check_complexity(measures, LIMITS),
    )
    draft = ExperimentManifest(
        experiment_id=ExperimentId(_digest("draft")),
        experiment_name="strategy_a_t01",
        experiment_version=1,
        schema_version=2,
        hypothesis=hypothesis,
        research_policy_ref=PolicyRef(
            policy_kind="research",
            policy_id="research_policy",
            version=1,
            digest=_digest("policy"),
        ),
        metric_set_version=2,
        search_plan="NONE",
        split="NONE",
        resolved_files=_files() if resolved_files is None else resolved_files,
        strategy_ref=StrategyRef(strategy_id="strategy_a", version=1, digest=_digest("strategy")),
        compiled_ref=manifest.config.compiled_ref,
        expected_config_digest=manifest.config_digest,
        snapshot_id=manifest.config.snapshot_ref.snapshot_id,
        allowed_partitions=allowed,
        complexity=measures,
        complexity_limits=LIMITS,
        pre_run_checks=checks,
        code_digest=CodeDigest(_digest(code_seed)),
        lock_digest=LockDigest(_digest("lock")),
        env_digest=EnvDigest(_digest("env")),
        git_commit="0" * 40,
        git_dirty=False,
    )
    return replace(draft, experiment_id=experiment_id_of(draft, values))


@dataclass
class _Repository:
    """`ResultRepository` の偽物。判断履歴は T01 の表、run の有無は `runs` が持つ。"""

    log: list[str]
    inner: traces.FakeRepository
    runs: dict[str, BacktestResult] = field(default_factory=dict)
    placeholders: set[str] = field(default_factory=set)
    evaluations: dict[str, StoredEvaluation | EvaluationReadFailure] = field(default_factory=dict)
    manifest_failure: str | None = None

    def read_manifest(self, run_id: RunId) -> RunManifest | ManifestReadFailure:
        if self.manifest_failure is not None:
            return ManifestReadFailure(run_id=run_id, detail=self.manifest_failure)
        return self.inner.read_manifest(run_id)

    def read_table(
        self, run_id: RunId, table: TraceTable, columns: tuple[TraceColumnSpec, ...]
    ) -> TableReadResult:
        return self.inner.read_table(run_id, table, columns)

    def write_evaluation(
        self, report: Any, rows: Mapping[EvaluationTable, tuple[object, ...]]
    ) -> None:
        self.log.append("write_evaluation")
        manifest = report.manifest
        self.evaluations[str(manifest.run_evaluation_id)] = StoredEvaluation(
            run_id=manifest.run_id,
            run_evaluation_id=manifest.run_evaluation_id,
            metric_set_version=manifest.metric_set_version,
            status=manifest.status,
            result_digest=manifest.result_digest,
        )

    def read_result(self, run_id: RunId) -> BacktestResult | ResultReadFailure:
        result = self.runs.get(str(run_id))
        if result is None:
            return ResultReadFailure(run_id=run_id, detail="result.json does not exist")
        return result

    def run_exists(self, run_id: RunId) -> bool:
        return str(run_id) in self.runs or str(run_id) in self.placeholders

    def read_evaluation(
        self, run_id: RunId, run_evaluation_id: RunEvaluationId
    ) -> StoredEvaluation | EvaluationReadFailure | None:
        return self.evaluations.get(str(run_evaluation_id))


@dataclass
class _Store:
    """`ExperimentStore` の偽物。保存の結果と書いた結末記録を残す。"""

    log: list[str]
    existing: ExperimentId | None = None
    unreadable: bool = False
    outcomes: list[ExperimentOutcome] = field(default_factory=list)
    saved: list[ExperimentManifest] = field(default_factory=list)

    def save_manifest(self, manifest: ExperimentManifest) -> ManifestSaveResult:
        self.log.append("save_manifest")
        if self.unreadable:
            return ManifestSaveResult.CONFLICT
        if self.existing is None:
            self.existing = manifest.experiment_id
            self.saved.append(manifest)
            return ManifestSaveResult.CREATED
        if self.existing == manifest.experiment_id:
            return ManifestSaveResult.ALREADY_IDENTICAL
        return ManifestSaveResult.CONFLICT

    def read_manifest(self, path: str) -> ExperimentManifest:  # pragma: no cover - 使わない
        raise NotImplementedError

    def write_outcome(self, outcome: ExperimentOutcome) -> None:
        self.log.append("write_outcome")
        self.outcomes.append(outcome)


@dataclass
class _Runner:
    """`BacktestRunner` の偽物。呼ばれると T01 の結果 DTO を返し、成果物を「ある」にする。"""

    log: list[str]
    repository: _Repository
    result: BacktestResult

    def run(self, config: RunConfig, compiled: CompiledStrategy) -> BacktestResult:
        self.log.append("run")
        self.repository.runs[str(self.result.run_id)] = self.result
        return self.result


class Workbench:
    """`RunExperiment` と偽物のポート一式。"""

    def __init__(self, *, run_manifest: RunManifest | None = None) -> None:
        self.run_manifest = run_manifest or traces.manifest_for()
        self.log: list[str] = []
        self.repository = _Repository(
            log=self.log, inner=traces.repository_for(manifest=self.run_manifest)
        )
        self.result = traces.result_for(self.run_manifest)
        self.runner = _Runner(log=self.log, repository=self.repository, result=self.result)
        self.store = _Store(log=self.log)
        self.evaluator = EvaluateRun(evaluation_code_digest=CodeDigest(_digest("evaluation")))
        self.use_case = RunExperiment(
            store=self.store,
            runner=self.runner,
            repository=self.repository,
            evaluator=self.evaluator,
        )

    def prepared(
        self, manifest: ExperimentManifest, *, expected_run_id: RunId | None = None
    ) -> PreparedExperiment:
        """1回分の入力（この実行の予測 `RunId` は既定で T01 の run の識別子）。"""
        return PreparedExperiment(
            manifest=manifest,
            run_config=self.run_manifest.config,
            compiled=compiled_strategy(),
            calendar=traces.CALENDAR,
            expected_run_id=expected_run_id or self.run_manifest.run_id,
            code_digest=self.run_manifest.code_digest,
            lock_digest=self.run_manifest.lock_digest,
            env_digest=self.run_manifest.env_digest,
            git_commit="0" * 40,
            git_dirty=False,
        )
