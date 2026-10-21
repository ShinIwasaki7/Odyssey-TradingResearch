"""1つの実験を記録票の保存 → run → 評価 → 事後検査の順に進める（D07 §19.4・§19.6・§21）。

**記録票の保存が成功するまで run を始めない**（事前固定の構造的な保証。D07 §19.4）。処理の
順は D07 §19.4 の状態×出来事表の行の順である。

1. **検査済み**: 記録票（事前検査 P1・P2・P6 の結果を含む）は合成が組み立て済み。事前検査が
   全件合格なら、記録票を保存する**前に**既存の run 成果物を確かめる（D07 §19.6 の手順1〜3。
   読むだけで何も書かない）。再利用できない衝突なら、記録票も結末記録も書かずに拒否する
   （`RUN_ARTIFACT_CONFLICT`）。
2. **保存を試みる**: 記録票を保存する。同じ版で内容違いなら結末記録を書かずに拒否する
   （`MANIFEST_CONFLICT`。検査 P3 の不合格）。事前検査が合格でなければ、結末記録
   `REJECTED_BY_POLICY` を書いて終わる（run しない）。
3. **記録済み → 実行済み**: run する（または既存の成果物を再利用する）。
4. **実行済み → 評価済み**: 評価する（または既存の評価を再利用する）。
5. **評価済み**: 事後検査 P4・P5 を行い、結末記録 `COMPLETED` か `FAILED_POST_RUN_CHECK` を
   書く。

別プロセスでの再現（D07 §21.2）の判定の規則も本モジュールに置く（`judge_*`）。記録票と結末
記録の読込、設定の組み立て、run と評価の実行は合成（`app`）の仕事であり、ここは判定だけを
持つ（`application` は入出力を持たない。D01 §2.2）。

**探索の実験の準備**（D09 §3・§10.2・§10.5・§10.7。実装 PR 2）: 合成が組み立てた探索の実験の
1回分の入力を `PreparedSearch`（試行ごとの `PreparedTrial`）で受ける。`RunExperiment` は、記録票を
保存する前に、記録票が予測ダイジェストを持つ**すべての単位**（コンパイルが通った全試行の
選定区間と検証区間の単位。選ばれるかどうかによらない）の予測 `RunId` について既存の成果物を
確かめる（`check_search_artifacts`。読むだけ。D09 §10.5 の注記・§10.7 の「検査済み」の行）。

**探索の実験の実行**（D09 §4.1・§10.7 の表・§10.12。実装 PR 4。`execute_search`）: 記録票の保存
（同じ版の再実行の退避を含む）→ 試行台帳の開始の行（採番と成果物からの逆照合 L11）→ 束縛の記録 →
fold ごとに選定区間の単位 → 選定記録 → 検証区間の単位 → 全 fold の後に判定 → 集約表 → 結末記録 →
台帳の結末の行、の順に進める。**台帳に開始の行を書くまで、どの run も始めない**。台帳が読めない・
追記が断られた・照合が合わないときは `TrialLedgerStop` を送出する（表に無い失敗。終了コード 1）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

from odyssey_fx.backtest.domain.policies import RunConfig, RunEndMode
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.result import BacktestResult, HypotheticalCloseLine, RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId, RunId
from odyssey_fx.common.money import Money
from odyssey_fx.common.refs import CodeDigest, ConfigDigest, ContentDigest, EnvDigest, LockDigest
from odyssey_fx.evaluation.application.evaluate_run import (
    EvaluateRun,
    EvaluationReport,
    equity_series,
)
from odyssey_fx.evaluation.application.manifest import RunEvaluationId, run_evaluation_id
from odyssey_fx.evaluation.application.ports import (
    BacktestRunner,
    EvaluationReadFailure,
    ExperimentStore,
    ManifestReadFailure,
    ManifestSaveResult,
    ResultReadFailure,
    ResultRepository,
    SegmentPlanner,
    SegmentRunPlan,
    StoredEvaluation,
    TrialLedgerAppendRefused,
    TrialLedgerContents,
    TrialLedgerReadFailure,
)
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentManifest,
    ExperimentOutcome,
    ExperimentStatus,
)
from odyssey_fx.evaluation.domain.metrics import MetricRecord
from odyssey_fx.evaluation.domain.path import (
    PathEnd,
    PathSegmentEnd,
    PathSegmentKey,
    PathSegmentRow,
    PathSegmentStart,
    PathSummary,
    PlannedSegment,
    path_metrics,
    plan_segments,
)
from odyssey_fx.evaluation.domain.research_policy import (
    PolicyCheck,
    PolicyCheckResult,
    all_passed,
    check_evaluation_rule,
    check_preregistration,
    check_run_matches,
    failed_checks,
)
from odyssey_fx.evaluation.domain.search import (
    TRIAL_LEDGER_SCHEMA_VERSION,
    ChangeKind,
    ComparisonBasis,
    FoldEvidence,
    FoldSelection,
    LedgerResults,
    Lineage,
    LineageSource,
    SearchOutcome,
    TrainUnitEvaluation,
    TrialLedgerBinding,
    TrialLedgerEntry,
    TrialLedgerEvent,
    TrialLedgerLine,
    TrialPhase,
    TrialPlan,
    TrialRunRecord,
    TrialStartRecord,
    TrialStatus,
    TrialUnitKey,
    ValidationUnitEvaluation,
    VariantLedgerResult,
    binding_mismatch,
    build_search_outcome,
    build_variant_outcome,
    finished_entry,
    has_finished_line,
    last_digest,
    lineage_of,
    next_execution,
    select_trial,
    started_line_of,
    unmatched_binding,
)
from odyssey_fx.evaluation.domain.splits import SplitSpec
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy

__all__ = [
    "ExperimentRefusal",
    "LineageRequest",
    "RecordedSearch",
    "ReproductionItem",
    "ReproductionRow",
    "overall_reproduction",
    "PreparedExperiment",
    "PreparedSearch",
    "PreparedTrial",
    "RefusalKind",
    "ReproductionReport",
    "ReproductionVerdict",
    "RunExperiment",
    "TrialLedgerStop",
    "judge_reproduction",
]


@dataclass(frozen=True, slots=True)
class PreparedExperiment:
    """合成が組み立てた1回分の入力（D07 §3 の v2.0 の表）。

    `expected_run_id` と環境の群は**この実行**のもの。`RunExperiment` は結末記録へ写し、既存の
    成果物の確認（D07 §19.6）に使う。記録票の環境の群とは別の値でありうる（D07 §19.2）。
    """

    manifest: ExperimentManifest
    run_config: RunConfig
    compiled: CompiledStrategy
    calendar: TradingCalendar
    expected_run_id: RunId
    code_digest: CodeDigest
    lock_digest: LockDigest
    env_digest: EnvDigest
    git_commit: str
    git_dirty: bool

    def __post_init__(self) -> None:
        for label, expected in (
            ("manifest", ExperimentManifest),
            ("run_config", RunConfig),
            ("compiled", CompiledStrategy),
            ("calendar", TradingCalendar),
            ("expected_run_id", RunId),
            ("code_digest", CodeDigest),
            ("lock_digest", LockDigest),
            ("env_digest", EnvDigest),
        ):
            if not isinstance(getattr(self, label), expected):
                raise KernelValueError(f"PreparedExperiment.{label} must be a {expected.__name__}")
        if not isinstance(self.git_commit, str) or not isinstance(self.git_dirty, bool):
            raise KernelValueError("PreparedExperiment.git_commit/git_dirty must be str/bool")
        if self.manifest.is_search:
            raise KernelValueError(
                "a search experiment is prepared as a PreparedSearch, not a PreparedExperiment"
                " (D09 §3)"
            )

    @property
    def expected_config_digest(self) -> ConfigDigest:
        """記録票の予測 `ConfigDigest`（単一実行の記録票は必ず持つ。D07 §19.2）。"""
        expected = self.manifest.expected_config_digest
        if expected is None:  # pragma: no cover - 記録票の構築時に検査済み
            raise KernelValueError("a single-run manifest carries its expected config digest")
        return expected


def _keyed[ValueT](
    items: object, value_type: type[ValueT], label: str
) -> dict[TrialUnitKey, ValueT]:
    """`(TrialUnitKey, 値)` の組の列を、鍵の重複を拒否して辞書にする。"""
    if not isinstance(items, tuple) or not all(
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], TrialUnitKey)
        and isinstance(item[1], value_type)
        for item in items
    ):
        raise KernelValueError(f"{label} must hold (TrialUnitKey, {value_type.__name__}) pairs")
    mapping = dict(items)
    if len(mapping) != len(items):
        raise KernelValueError(f"{label} holds a unit twice")
    return mapping


@dataclass(frozen=True, slots=True)
class PreparedTrial:
    """探索の試行1つの実行の材料（D09 §3・§10.7）。

    - `plan`: 記録票の `TrialPlan`（割当・コンパイル結果の識別かコンパイル拒否・予測ダイジェスト）。
    - `compiled`: コンパイル結果。コンパイル拒否の試行は `None`（run を作らない。D09 §5.2）。
    - `run_configs`: 単位ごとの `RunConfig`（区間は fold の選定区間か検証区間。D09 §6.2）。
    - `expected_run_ids`: 単位ごとの予測 `RunId`（この実行の環境のダイジェストから。ADR-0006）。

    単位の集合は `plan.expected_config_digests` の鍵と同じで、各 `RunConfig` の `compiled_ref` は
    試行の `compiled_ref` と同じ（探索の run どうしで違うのは `compiled_ref` と `run_interval`
    だけ。D09 §6.2）。
    """

    plan: TrialPlan
    compiled: CompiledStrategy | None
    run_configs: tuple[tuple[TrialUnitKey, RunConfig], ...]
    expected_run_ids: tuple[tuple[TrialUnitKey, RunId], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan, TrialPlan):
            raise KernelValueError("PreparedTrial.plan must be a TrialPlan")
        if self.compiled is not None and not isinstance(self.compiled, CompiledStrategy):
            raise KernelValueError("PreparedTrial.compiled must be a CompiledStrategy or None")
        configs = _keyed(self.run_configs, RunConfig, "PreparedTrial.run_configs")
        run_ids = _keyed(self.expected_run_ids, RunId, "PreparedTrial.expected_run_ids")
        units = {unit for unit, _ in self.plan.expected_config_digests}
        if set(configs) != units or set(run_ids) != units:
            raise KernelValueError(
                "PreparedTrial carries a RunConfig and an expected RunId for exactly the units"
                " whose config digest the manifest predicts (D09 §10.2)"
            )
        if (self.compiled is None) != (self.plan.compiled_ref is None):
            raise KernelValueError(
                "PreparedTrial.compiled is present exactly when the trial compiled (D09 §5.2)"
            )
        if self.compiled is not None and self.compiled.compiled_ref != self.plan.compiled_ref:
            raise KernelValueError("PreparedTrial.compiled does not match the trial's compiled_ref")
        if any(config.compiled_ref != self.plan.compiled_ref for config in configs.values()):
            raise KernelValueError(
                "every RunConfig of a trial carries the trial's compiled_ref (D09 §6.2)"
            )
        order = sorted(units, key=lambda unit: unit.order)
        object.__setattr__(self, "run_configs", tuple((unit, configs[unit]) for unit in order))
        object.__setattr__(self, "expected_run_ids", tuple((unit, run_ids[unit]) for unit in order))

    def run_config(self, unit: TrialUnitKey) -> RunConfig:
        """単位の `RunConfig`。無い単位は構造エラー。"""
        for key, value in self.run_configs:
            if key == unit:
                return value
        raise KernelValueError(f"the trial {self.plan.trial_index} has no unit {unit}")

    def expected_run_id(self, unit: TrialUnitKey) -> RunId:
        """単位の予測 `RunId`。無い単位は構造エラー。"""
        for key, value in self.expected_run_ids:
            if key == unit:
                return value
        raise KernelValueError(f"the trial {self.plan.trial_index} has no unit {unit}")


@dataclass(frozen=True, slots=True)
class PreparedSearch:
    """合成が組み立てた探索の実験の1回分の入力（D09 §3・§10.7）。

    D07 §3 の `PreparedExperiment` の項目のうち `run_config` / `compiled` / `expected_run_id` を
    `trials`（全試行の `PreparedTrial`。`trial_index` の昇順）に置き換えたもの。各試行の `plan` は
    記録票の `trials` の同じ番号の要素と同じ値でなければならない（記録票に固定した設定の run
    だけを行う）。環境の群は**この実行**のもの。
    """

    manifest: ExperimentManifest
    trials: tuple[PreparedTrial, ...]
    calendar: TradingCalendar
    code_digest: CodeDigest
    lock_digest: LockDigest
    env_digest: EnvDigest
    git_commit: str
    git_dirty: bool

    def __post_init__(self) -> None:
        for label, expected in (
            ("manifest", ExperimentManifest),
            ("calendar", TradingCalendar),
            ("code_digest", CodeDigest),
            ("lock_digest", LockDigest),
            ("env_digest", EnvDigest),
        ):
            if not isinstance(getattr(self, label), expected):
                raise KernelValueError(f"PreparedSearch.{label} must be a {expected.__name__}")
        if not isinstance(self.git_commit, str) or not isinstance(self.git_dirty, bool):
            raise KernelValueError("PreparedSearch.git_commit/git_dirty must be str/bool")
        if not self.manifest.is_search:
            raise KernelValueError("PreparedSearch requires the manifest of a search experiment")
        if not isinstance(self.trials, tuple) or not all(
            isinstance(trial, PreparedTrial) for trial in self.trials
        ):
            raise KernelValueError("PreparedSearch.trials must be a tuple of PreparedTrial")
        if tuple(trial.plan for trial in self.trials) != self.manifest.trials:
            raise KernelValueError(
                "PreparedSearch.trials must follow the trials fixed in the manifest (D09 §10.2)"
            )


class RefusalKind(Enum):
    """結末記録を書かずに拒否する2つの経路（D07 §19.4・§19.6）。"""

    #: 記録票が同じ版で内容違い（検査 P3 の不合格）。
    MANIFEST_CONFLICT = "MANIFEST_CONFLICT"
    #: `runs/<expected_run_id>/` があり、再利用できない（D07 §19.6 の手順3・5）。
    RUN_ARTIFACT_CONFLICT = "RUN_ARTIFACT_CONFLICT"


@dataclass(frozen=True, slots=True)
class ExperimentRefusal:
    """拒否の結果（D07 §19.4）。コマンドはこれを終了コード 5 に写す（D07 §21.3）。"""

    kind: RefusalKind
    experiment_id: ExperimentId
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RefusalKind):
            raise KernelValueError("ExperimentRefusal.kind must be a RefusalKind")
        if not isinstance(self.experiment_id, ExperimentId):
            raise KernelValueError("ExperimentRefusal.experiment_id must be an ExperimentId")
        if not isinstance(self.detail, str) or not self.detail:
            raise KernelValueError("ExperimentRefusal.detail must be a non-empty str")


@dataclass(frozen=True, slots=True)
class _Reuse:
    """記録票の保存の前に確かめた、既存の成果物の再利用（D07 §19.6 の手順2）。"""

    result: BacktestResult
    evaluation: StoredEvaluation | None
    #: 再利用する評価の指標（探索の単位だけが読む。D07 v2.11 §19.6、D09 §17.7.4 の2）。
    #: 単一実行の経路と、評価が無い（これから評価する）ときは `None`。
    metrics: tuple[MetricRecord, ...] | None = None


class TrialLedgerStop(Exception):
    """試行台帳が読めない・追記が断られた・照合が合わないので、探索の実行を止めたこと。

    D07 §21.3 の表に無い失敗（終了コード 1）。開始の行の前なら run は1つも始まっておらず、
    結末の行の前なら結末記録まで書いた終端した実行である（D09 §10.12.4 の a〜c・e）。どちらも
    台帳の行を後から足す経路は作らない。
    """


@dataclass(frozen=True, slots=True)
class _UnitResult:
    """単位1つの実行の結果（試行記録と、選定・判定に渡す評価の指標）。"""

    record: TrialRunRecord
    metrics: tuple[MetricRecord, ...]


@dataclass(frozen=True, slots=True)
class _Evaluated:
    """1つの run の評価（同じ実行の中で同じ `RunId` を持つ単位が使い回す）。"""

    run_evaluation_id: RunEvaluationId
    status: EvaluationStatus
    result_digest: ContentDigest
    metric_set_version: int
    metrics: tuple[MetricRecord, ...]


@dataclass(frozen=True, slots=True)
class LineageRequest:
    """実験設定の系譜（D13 §7.1）。台帳の開始の行の系譜は、これと台帳から組み立てる。"""

    derived_from: LineageSource | None
    change_kinds: tuple[ChangeKind, ...]
    note_digest: ContentDigest | None

    def __post_init__(self) -> None:
        if self.derived_from is not None and not isinstance(self.derived_from, LineageSource):
            raise KernelValueError("LineageRequest.derived_from must be a LineageSource or None")
        if not isinstance(self.change_kinds, tuple) or not all(
            isinstance(item, ChangeKind) for item in self.change_kinds
        ):
            raise KernelValueError("LineageRequest.change_kinds must be a tuple of ChangeKind")
        if self.note_digest is not None and not isinstance(self.note_digest, ContentDigest):
            raise KernelValueError("LineageRequest.note_digest must be a ContentDigest or None")


@dataclass(frozen=True, slots=True)
class _FoldsRun:
    """組の全 fold の実行の結果（判定の入力・集約表の材料）。"""

    evidence: tuple[FoldEvidence, ...]
    selections: tuple[FoldSelection, ...]
    records: tuple[TrialRunRecord, ...]
    metrics: dict[TrialUnitKey, tuple[MetricRecord, ...]]


@dataclass(frozen=True, slots=True)
class _PathRun:
    """組の連続口座の検証経路の結果（経路の要約と、区間の終端の記録）。"""

    summary: PathSummary
    ends: tuple[PathSegmentEnd, ...]


@dataclass(frozen=True, slots=True)
class _VariantRun:
    """組 1 つの実行の結果。"""

    folds: _FoldsRun
    path: _PathRun

    @property
    def checks(self) -> tuple[PolicyCheckResult, ...]:
        """組の単位と経路の区間の事後検査 P4・P5 のすべて。"""
        return (
            *(check for record in self.folds.records for check in record.outcome_checks),
            *(check for end in self.path.ends for check in end.outcome_checks),
        )


def _initial_balance(prepared: PreparedSearch, variant: Variant) -> Money:
    """組の経路の最初の残高（実験設定の初期残高。独立口座の単位の `RunConfig` と同じ値）。"""
    for trial in prepared.trials:
        for unit, config in trial.run_configs:
            if unit.variant is variant:
                return config.account.initial_balance
    raise KernelValueError("a search with a selected trial has a unit of every variant")


def _segment_failure(end: PathSegmentEnd) -> str:
    """区間の run の失敗の理由（経路の要約の `end_reason`。D13 §4.6 の表 2 の E5）。"""
    if end.run_status is not RunStatus.COMPLETED:
        return f"the segment run ended {end.run_status.value} (D13 §4.5。仮置き 16)"
    if end.evaluation_status is not EvaluationStatus.COMPLETED:
        status = None if end.evaluation_status is None else end.evaluation_status.value
        return f"the evaluation of the segment run is {status} (D13 §4.5。仮置き 16)"
    if not end.post_run_checks_passed:
        failed = [item.check.value for item in end.outcome_checks if not item.passed]
        return f"the post-run checks {failed} of the segment run did not pass (D13 §4.7 の 2)"
    return "the segment run has no final summaries (D06 §10.3)"


class RunExperiment:
    """`execute(prepared) -> ExperimentOutcome | ExperimentRefusal`（D07 §3・§19.4）。

    **具体クラス**である（`EvaluateRun` と同じ扱い。差し替える側が居ない）。
    """

    __slots__ = ("_evaluator", "_repository", "_runner", "_store")

    def __init__(
        self,
        *,
        store: ExperimentStore,
        runner: BacktestRunner,
        repository: ResultRepository,
        evaluator: EvaluateRun,
    ) -> None:
        if not isinstance(evaluator, EvaluateRun):
            raise KernelValueError("RunExperiment requires an EvaluateRun")
        self._store = store
        self._runner = runner
        self._repository = repository
        self._evaluator = evaluator

    def execute(self, prepared: PreparedExperiment) -> ExperimentOutcome | ExperimentRefusal:
        """1つの実験を進める（D07 §19.4 の状態×出来事表）。"""
        if not isinstance(prepared, PreparedExperiment):
            raise KernelValueError("RunExperiment.execute requires a PreparedExperiment")
        manifest = prepared.manifest
        pre_passed = all_passed(manifest.pre_run_checks)

        # 検査済み: 事前検査が全件合格なら、保存の前に既存の run 成果物を確かめる（読むだけ）。
        reuse: _Reuse | None = None
        if pre_passed:
            checked = self._check_existing(prepared)
            if isinstance(checked, ExperimentRefusal):
                return checked
            reuse = checked

        # 保存を試みる。
        saved = self._store.save_manifest(manifest)
        if saved is ManifestSaveResult.CONFLICT:
            return ExperimentRefusal(
                kind=RefusalKind.MANIFEST_CONFLICT,
                experiment_id=manifest.experiment_id,
                detail=(
                    f"the experiment {manifest.experiment_name} v{manifest.experiment_version}"
                    " already has a manifest with different content (or an unreadable one);"
                    " it was not overwritten and nothing ran. Raise `version` to record a new"
                    " version (preregistration_unchanged, D07 §19.4・§20.3)"
                ),
            )
        if not isinstance(saved, ManifestSaveResult):
            raise KernelValueError("ExperimentStore.save_manifest must return a ManifestSaveResult")
        preregistration = check_preregistration(
            manifest.experiment_id.hex,
            None if saved is ManifestSaveResult.CREATED else manifest.experiment_id.hex,
        )
        if not pre_passed:
            outcome = self._outcome(
                prepared,
                status=ExperimentStatus.REJECTED_BY_POLICY,
                checks=(preregistration,),
                failed=manifest.pre_run_checks,
            )
            self._store.write_outcome(outcome)
            return outcome

        # 記録済み → 実行済み。
        if reuse is not None:
            result = reuse.result
        else:
            result = self._runner.run(prepared.run_config, prepared.compiled)
            if not isinstance(result, BacktestResult):
                raise KernelValueError("BacktestRunner.run must return a BacktestResult")

        # 実行済み → 評価済み。
        if reuse is not None and reuse.evaluation is not None:
            stored = reuse.evaluation
            evaluation_id: RunEvaluationId = stored.run_evaluation_id
            evaluation_status = stored.status
            result_digest = stored.result_digest
            evaluated_version = stored.metric_set_version
        else:
            report = self._evaluate(prepared, result)
            evaluation_id = report.manifest.run_evaluation_id
            evaluation_status = report.status
            result_digest = report.manifest.result_digest
            evaluated_version = report.manifest.metric_set_version

        # 評価済み: 事後検査 P4・P5。
        post = (
            self._run_matches(prepared, result),
            check_evaluation_rule(manifest.metric_set_version, evaluated_version),
        )
        checks = (preregistration, *post)
        status = (
            ExperimentStatus.COMPLETED
            if all_passed(post)
            else ExperimentStatus.FAILED_POST_RUN_CHECK
        )
        outcome = self._outcome(
            prepared,
            status=status,
            checks=checks,
            failed=checks,
            run=(result, reuse is not None),
            evaluation=(evaluation_id.digest, evaluation_status, result_digest),
        )
        self._store.write_outcome(outcome)
        return outcome

    # --- 探索の実験（D09 §4.1・§10.7・§10.12。実装 PR 4）-------------------------

    def execute_search(
        self,
        prepared: PreparedSearch,
        *,
        basis: Paired[ComparisonBasis] | None,
        execution_nonce: str,
        out_base: str,
        running: str,
        segments: SegmentPlanner,
        lineage: LineageRequest | None = None,
    ) -> ExperimentOutcome | ExperimentRefusal:
        """探索の実験の1回の実行を進める（D09 §10.7 の状態×出来事表。2026-10-21 改訂）。

        - `basis`: この実行の組ごとの比較の前提（合成が組み立てる。事前検査が全件合格なら必ず
          渡す。仮置き 27）。
        - `execution_nonce`: 実行ごとの乱数の文字列（`app` が作る。D09 §10.12.1 の W3）。
        - `out_base` / `running`: 成果物の基点と、走らせている実験の版のディレクトリの基点からの
          パス（成果物からの逆照合 L11 と数え直し待ちの判定に使う。D09 §10.12.2）。
        - `segments`: 連続口座の検証経路の区間の run の設定を組み立てる合成の口（D13 §4.7）。
        - `lineage`: 実験設定の系譜（書かなければ `None`。D13 §7.1）。

        1 回の実行で標準 → 候補の両組を評価し、組ごとに全 fold の後にその組の経路の区間を
        区間 0 から順に走らせる（改訂 PR の仮置き 35 (a)）。両組の全 fold と経路が終端した後に、
        組ごとの判定と組の判定を決め、集約表 → 結末記録 → 台帳の結末の行を書く。

        台帳が読めない・追記が断られた・照合が合わないときは `TrialLedgerStop`（終了コード 1）。
        """
        if not isinstance(prepared, PreparedSearch):
            raise KernelValueError("execute_search requires a PreparedSearch")
        manifest = prepared.manifest
        pre_passed = all_passed(manifest.pre_run_checks)

        # 検査済み: 事前検査が全件合格なら、保存の前に全単位の既存の run 成果物を確かめる。
        if pre_passed:
            refusal = self.check_search_artifacts(prepared)
            if refusal is not None:
                return refusal

        # 保存を試みる（同じ版の再実行の退避と「退避中」の印の回復は保存が行う。D09 §11.3）。
        saved = self._store.save_manifest(manifest)
        if saved is ManifestSaveResult.CONFLICT:
            return ExperimentRefusal(
                kind=RefusalKind.MANIFEST_CONFLICT,
                experiment_id=manifest.experiment_id,
                detail=(
                    f"the experiment {manifest.experiment_name} v{manifest.experiment_version}"
                    " already has a manifest with different content (or an unreadable one);"
                    " it was not overwritten and nothing ran. Raise `version` to record a new"
                    " version (preregistration_unchanged, D07 §19.4・§20.3)"
                ),
            )
        if not isinstance(saved, ManifestSaveResult):
            raise KernelValueError("ExperimentStore.save_manifest must return a ManifestSaveResult")
        preregistration = check_preregistration(
            manifest.experiment_id.hex,
            None if saved is ManifestSaveResult.CREATED else manifest.experiment_id.hex,
        )
        if not pre_passed:
            # 台帳には何も書かない（run をせず、どの区間も見ていない。D09 §10.10）。
            outcome = self._search_outcome(
                prepared,
                status=ExperimentStatus.REJECTED_BY_POLICY,
                preregistration=preregistration,
                failed=failed_checks(manifest.pre_run_checks),
                search=None,
            )
            self._store.write_outcome(outcome)
            return outcome
        if not isinstance(basis, Paired):
            raise KernelValueError("a search that passed the pre-run checks needs its basis")
        if not isinstance(segments, SegmentPlanner):
            raise KernelValueError("execute_search requires a SegmentPlanner")

        started, binding = self._start_ledger(
            prepared, basis, execution_nonce, out_base, running, lineage
        )
        standard = manifest.evaluation_standard
        if standard is None:  # pragma: no cover - 探索の記録票は評価基準を持つ
            raise KernelValueError("a search manifest carries its evaluation standard")

        # 探索中: 組（標準 → 候補）→ 組の中は fold → 局面 → 試行、組の全 fold の後に経路の区間
        # （D09 §5.4 の改訂。改訂 PR の仮置き 35 (a)）。同じ `RunId` の評価は組をまたいで共有する。
        cache: dict[RunId, _Evaluated] = {}
        runs: dict[Variant, _VariantRun] = {}
        for variant in VARIANTS:
            folds = self._run_folds(prepared, variant, cache)
            path = self._run_path(prepared, variant, folds.selections, segments, cache)
            runs[variant] = _VariantRun(folds=folds, path=path)

        outcomes = Paired.build(
            lambda variant: build_variant_outcome(
                standard, variant, runs[variant].folds.evidence, runs[variant].path.summary
            )
        )
        search = build_search_outcome(outcomes.standard, outcomes.candidate, binding.execution)
        failed = failed_checks([check for variant in VARIANTS for check in runs[variant].checks])
        status = ExperimentStatus.FAILED_POST_RUN_CHECK if failed else ExperimentStatus.COMPLETED

        # 終端の書き込み: (1) 集約表（組ごと）→ (2) 結末記録 → (3) 台帳の結末の行（D09 §10.7）。
        # 指標は選定・判定に使った値をそのまま渡す（読み出しは `read_evaluation_metrics` だけ。
        # D07 v2.11 §3、D09 §17.7.4 の4）。
        for variant in VARIANTS:
            folds = runs[variant].folds
            self._store.write_aggregate_tables(
                manifest, variant, folds.selections, folds.records, folds.metrics
            )
        outcome = self._search_outcome(
            prepared,
            status=status,
            preregistration=preregistration,
            failed=failed,
            search=search,
        )
        self._store.write_outcome(outcome)
        self._finish_ledger(
            started,
            binding,
            outcome,
            Paired.build(
                lambda variant: (
                    ExperimentStatus.FAILED_POST_RUN_CHECK
                    if failed_checks(runs[variant].checks)
                    else ExperimentStatus.COMPLETED
                )
            ),
        )
        return outcome

    def _read_ledger(self, purpose: str) -> TrialLedgerContents:
        contents = self._store.read_trial_ledger()
        if isinstance(contents, TrialLedgerReadFailure):
            line = "" if contents.line_number is None else f" at line {contents.line_number}"
            raise TrialLedgerStop(
                f"the trial ledger cannot be read ({contents.kind.value}{line}: {contents.detail});"
                f" {purpose} (D09 §10.12.2・§10.12.4)"
            )
        if not isinstance(contents, TrialLedgerContents):
            raise KernelValueError("ExperimentStore.read_trial_ledger returned an unexpected value")
        return contents

    def _append(self, line: TrialLedgerLine, purpose: str) -> None:
        appended = self._store.append_trial_ledger(line)
        if isinstance(appended, TrialLedgerAppendRefused):
            raise TrialLedgerStop(
                f"the trial ledger refused the line ({appended.kind.value}: {appended.detail});"
                f" {purpose} (D09 §10.12.1・§10.12.4)"
            )
        if appended != line:
            raise KernelValueError("ExperimentStore.append_trial_ledger returned another line")

    def _start_ledger(
        self,
        prepared: PreparedSearch,
        basis: Paired[ComparisonBasis],
        execution_nonce: str,
        out_base: str,
        running: str,
        lineage: LineageRequest | None,
    ) -> tuple[TrialLedgerEntry, TrialLedgerBinding]:
        """台帳に開始の行を足し、束縛の記録を書く（D09 §10.12.3 の1・2）。どの run よりも前。"""
        manifest = prepared.manifest
        stopped = "nothing ran (no run started; the manifest is kept)"
        contents = self._read_ledger(stopped)
        bindings = tuple(
            (path, record.detail if isinstance(record, TrialLedgerReadFailure) else record)
            for path, record in self._store.read_ledger_bindings(out_base)
        )
        pending = unmatched_binding(contents.lines, bindings, running)
        if pending is not None:
            path, reason = pending
            raise TrialLedgerStop(
                f"the trial ledger cannot be read (UNMATCHED_BINDING: {path}: {reason}). That"
                " experiment version waits to be counted again; re-run it with the same version"
                f" first; {stopped} (D09 §10.12.2 の L11・数え直し待ち。Q38・Q39)"
            )
        split = manifest.split
        standard = manifest.evaluation_standard
        if not isinstance(split, SplitSpec) or standard is None:  # pragma: no cover
            raise KernelValueError("a search manifest carries its split and evaluation standard")
        written: Lineage | None = None
        if lineage is not None:
            built = lineage_of(
                contents.lines, lineage.derived_from, lineage.change_kinds, lineage.note_digest
            )
            if isinstance(built, str):
                raise TrialLedgerStop(f"{built}; {stopped}")
            written = built
        entry = TrialLedgerEntry(
            schema_version=TRIAL_LEDGER_SCHEMA_VERSION,
            event=TrialLedgerEvent.STARTED,
            experiment_id=manifest.experiment_id,
            execution=next_execution(contents.lines, manifest.experiment_id),
            execution_nonce=execution_nonce,
            experiment_name=manifest.experiment_name,
            experiment_version=manifest.experiment_version,
            strategy_id=manifest.strategy_ref.strategy_id,
            basis=basis,
            trial_count=len(manifest.trials),
            search_plan_digest=digest(manifest.search_plan),
            validation_intervals=tuple(fold.validation for fold in split.folds),
            final_holdout=manifest.final_holdout,
            purpose=standard.purpose,
            lineage=written,
            status=None,
            verdict=None,
            frequency_class=None,
            results=None,
        )
        line = TrialLedgerLine.of(entry, last_digest(contents.lines))
        self._append(line, stopped)
        binding = TrialLedgerBinding(
            schema_version=1,
            experiment_id=manifest.experiment_id,
            execution=entry.execution,
            started_line_digest=line.digest,
        )
        self._store.write_ledger_binding(binding)
        return entry, binding

    def _finish_ledger(
        self,
        started: TrialLedgerEntry,
        binding: TrialLedgerBinding,
        outcome: ExperimentOutcome,
        statuses: Paired[ExperimentStatus],
    ) -> None:
        """結末の行を足す（D09 §10.12.3 の3）。照合が合わなければ書かずに止める。

        最上位は実験の状態と組の判定、組ごとの状態・判定・頻度区分・2 軸・保留の印は `results`
        （実装 PR の仮置き 55）。
        """
        search = outcome.search
        if search is None:  # pragma: no cover - 探索が終端した実行は判定を持つ
            raise KernelValueError("a finished search carries its SearchOutcome")
        stopped = (
            "the outcome is written but the ledger has no FINISHED line for this execution;"
            " it is never added later (re-run the same version to record a new execution)"
        )
        contents = self._read_ledger(stopped)
        mismatch = binding_mismatch(contents.lines, binding)
        if mismatch is not None:
            raise TrialLedgerStop(f"{mismatch}; {stopped} (D09 §10.12.3 の3)")
        if has_finished_line(contents.lines, binding.experiment_id, binding.execution):
            raise TrialLedgerStop(
                f"the ledger already has a FINISHED line of ({binding.experiment_id},"
                f" {binding.execution}); {stopped} (D09 §10.12.3 の3)"
            )
        opening = started_line_of(contents.lines, binding.experiment_id, binding.execution)
        if opening is None or opening.entry != started:  # pragma: no cover - 上の照合で済み
            raise TrialLedgerStop(f"the STARTED line changed; {stopped}")
        results = LedgerResults(
            split=search.combined.split,
            hold=search.combined.hold,
            variants=search.variants.map(
                lambda variant, item: VariantLedgerResult(
                    status=statuses.of(variant),
                    verdict=item.verdict,
                    frequency_class=None if item.frequency is None else item.frequency.class_name,
                    profitability=item.profitability,
                    evidence=item.evidence,
                    hold=item.hold,
                )
            ),
        )
        entry = finished_entry(
            started, status=outcome.status, verdict=search.verdict, results=results
        )
        self._append(TrialLedgerLine.of(entry, last_digest(contents.lines)), stopped)

    def _run_folds(
        self, prepared: PreparedSearch, variant: Variant, cache: dict[RunId, _Evaluated]
    ) -> _FoldsRun:
        """組の fold ごとに選定区間の全単位 → 選定記録 → 検証区間の単位（D09 §4.1 の4・§6.6）。"""
        manifest = prepared.manifest
        split = manifest.split
        standard = manifest.evaluation_standard
        if not isinstance(split, SplitSpec) or standard is None:  # pragma: no cover
            raise KernelValueError("a search manifest carries its split and evaluation standard")
        evidence: list[FoldEvidence] = []
        selections: list[FoldSelection] = []
        records: list[TrialRunRecord] = []
        metrics: dict[TrialUnitKey, tuple[MetricRecord, ...]] = {}
        for fold in split.folds:
            train_units: list[TrainUnitEvaluation] = []
            for trial in prepared.trials:
                index = trial.plan.trial_index
                if not trial.plan.compiled:
                    # 失敗（コンパイル拒否）の単位は run を作らず開始記録も書かない（D09 §10.5）。
                    train_units.append(
                        TrainUnitEvaluation(
                            fold_index=fold.fold_index,
                            trial_index=index,
                            status=TrialStatus.FAILED,
                            run_status=None,
                            run_evaluation_id=None,
                            evaluation_status=None,
                            post_run_checks_passed=False,
                            metrics=(),
                        )
                    )
                    continue
                unit = TrialUnitKey(
                    variant=variant,
                    fold_index=fold.fold_index,
                    phase=TrialPhase.TRAIN,
                    trial_index=index,
                )
                done = self._run_unit(prepared, trial, unit, cache)
                records.append(done.record)
                metrics[unit] = done.metrics
                train_units.append(_train_unit(done))
            # 選定区間の結果だけから選び、検証区間の単位より前に選定記録を保存する（D09 §7.5）。
            selection = select_trial(fold.fold_index, standard.selection, train_units)
            self._store.write_selection(variant, selection)
            selections.append(selection)
            validation: ValidationUnitEvaluation | None = None
            if selection.selected_trial_index is not None:
                chosen = prepared.trials[selection.selected_trial_index]
                unit = TrialUnitKey(
                    variant=variant,
                    fold_index=fold.fold_index,
                    phase=TrialPhase.VALIDATION,
                    trial_index=chosen.plan.trial_index,
                )
                done = self._run_unit(prepared, chosen, unit, cache)
                records.append(done.record)
                metrics[unit] = done.metrics
                validation = _validation_unit(done)
            evidence.append(
                FoldEvidence(
                    fold=fold,
                    selection=selection,
                    train_units=tuple(train_units),
                    validation=validation,
                )
            )
        return _FoldsRun(
            evidence=tuple(evidence),
            selections=tuple(selections),
            records=tuple(records),
            metrics=metrics,
        )

    def _run_path(
        self,
        prepared: PreparedSearch,
        variant: Variant,
        selections: tuple[FoldSelection, ...],
        planner: SegmentPlanner,
        cache: dict[RunId, _Evaluated],
    ) -> _PathRun:
        """組の連続口座の検証経路を区間 0 から順に走らせる（D13 §4.5〜§4.7 の表 2）。

        区間の列は全 fold の選定記録から決める（仮置き 11・14・15）。選んだ試行のある区間は、
        開始記録 → 既存の成果物の確認（無ければ run、一致すれば再利用、一致しない・読めなければ
        構造エラー。仮置き 26）→ 評価 → P4（開始記録と照合）・P5 → 終端の記録の順に進める。
        区間の run が失敗すれば「判定できない」で後ろの区間は走らせず（仮置き 16）、区間の
        終わりの費用を引いた後の残高が 0 以下なら「資金喪失」で終える（仮置き 29）。
        """
        manifest = prepared.manifest
        split = manifest.split
        if not isinstance(split, SplitSpec):  # pragma: no cover
            raise KernelValueError("a search manifest carries its split")
        planned = plan_segments(
            split.folds, [selection.selected_trial_index for selection in selections]
        )
        initial = _initial_balance(prepared, variant)
        balance = initial
        rows: list[PathSegmentRow] = []
        ends: list[PathSegmentEnd] = []
        series: list[Decimal] = []
        trade_count = 0
        end = PathEnd.COMPLETED
        end_segment: int | None = None
        end_reason: str | None = None
        for segment in planned:
            if segment.trial_index is None:
                # 候補なしの区間: 取引せず、残高を現金のまま次へ渡す（仮置き 15。表 2 の E3）。
                rows.append(
                    PathSegmentRow(
                        segment_index=segment.segment_index,
                        fold_indices=segment.fold_indices,
                        trial_index=None,
                        interval=segment.interval,
                        initial_balance=balance,
                        end_balance=balance,
                        carried_boundaries=segment.carried_boundaries,
                        settlement_count=0,
                        settlement_cost=None,
                        trade_count=None,
                        run_id=None,
                        run_reused=None,
                    )
                )
                continue
            done = self._run_segment(prepared, variant, segment, balance, planner, cache)
            ends.append(done)
            row = PathSegmentRow(
                segment_index=segment.segment_index,
                fold_indices=segment.fold_indices,
                trial_index=segment.trial_index,
                interval=segment.interval,
                initial_balance=balance,
                end_balance=done.settled_equity if done.succeeded else None,
                carried_boundaries=segment.carried_boundaries,
                settlement_count=len(done.settlement_lines),
                settlement_cost=done.settlement_cost if done.succeeded else None,
                trade_count=done.trade_count if done.succeeded else None,
                run_id=done.run_id,
                run_reused=done.run_reused,
            )
            rows.append(row)
            if not done.succeeded:
                end = PathEnd.INCOMPLETE
                end_segment = segment.segment_index
                end_reason = _segment_failure(done)
                break
            settled = done.settled_equity
            if settled is None or done.trade_count is None:  # pragma: no cover - succeeded
                raise KernelValueError("a succeeded segment carries its settled equity")
            equities = equity_series(self._repository, done.run_id)
            if isinstance(equities, str):
                raise KernelValueError(
                    f"the path segment {variant.value} {segment.segment_index}: {equities}"
                    " (D07 §5.6)"
                )
            series.extend(equities)
            series.append(settled.amount)
            trade_count += done.trade_count
            balance = settled
            if settled.amount <= 0:
                end = PathEnd.CAPITAL_LOST
                end_segment = segment.segment_index
                end_reason = (
                    f"the settled balance {settled} at the end of segment"
                    f" {segment.segment_index} is not positive (D13 §4.5。仮置き 29)"
                )
                break
        if end is PathEnd.INCOMPLETE:
            summary = PathSummary(
                segments=tuple(rows),
                end=end,
                end_segment=end_segment,
                end_reason=end_reason,
                metrics=(),
                trade_count=None,
                final_balance=None,
            )
        else:
            summary = PathSummary(
                segments=tuple(rows),
                end=end,
                end_segment=end_segment,
                end_reason=end_reason,
                metrics=path_metrics(initial, series, balance),
                trade_count=trade_count,
                final_balance=balance,
            )
        return _PathRun(summary=summary, ends=tuple(ends))

    def _run_segment(
        self,
        prepared: PreparedSearch,
        variant: Variant,
        segment: PlannedSegment,
        balance: Money,
        planner: SegmentPlanner,
        cache: dict[RunId, _Evaluated],
    ) -> PathSegmentEnd:
        """区間 1 つ（表 2 の E2・E4・E5）。

        開始記録 → 既存の成果物 → run → 評価 → P4・P5 → 終端の記録。
        """
        manifest = prepared.manifest
        trial_index = segment.trial_index
        if trial_index is None:  # pragma: no cover - 呼び出し側が分ける
            raise KernelValueError("a segment without a candidate has no run")
        trial = prepared.trials[trial_index]
        compiled = trial.compiled
        if compiled is None or trial.plan.compiled_ref is None:  # pragma: no cover - 選ばれた試行
            raise KernelValueError("a selected trial has compiled (D09 §7.4)")
        plan = planner.plan(variant, compiled, segment.interval, balance)
        if not isinstance(plan, SegmentRunPlan):
            raise KernelValueError("SegmentPlanner.plan must return a SegmentRunPlan")
        config = plan.config
        if (
            config.run_end is not RunEndMode.CLOSE_ALL
            or config.run_interval != segment.interval
            or config.account.initial_balance != balance
            or config.compiled_ref != trial.plan.compiled_ref
        ):
            raise KernelValueError(
                "the segment run configuration must close all at the end of the segment and"
                " start from the passed balance (D13 §4.5)"
            )
        key = PathSegmentKey(variant=variant, segment_index=segment.segment_index)
        start = PathSegmentStart(
            experiment_id=manifest.experiment_id,
            key=key,
            fold_indices=segment.fold_indices,
            trial_index=trial_index,
            compiled_ref=trial.plan.compiled_ref,
            interval=segment.interval,
            initial_balance=balance,
            run_end=config.run_end,
            config_digest=plan.config_digest,
            expected_run_id=plan.expected_run_id,
        )
        self._store.write_path_segment_start(start)
        version = manifest.metric_set_version
        checked = self._inspect_existing(
            plan.expected_run_id, plan.config_digest, version, prepared.calendar, read_metrics=True
        )
        if isinstance(checked, str):
            # 区間の run の既存の成果物が一致しない・読めない: 構造エラーで中断（仮置き 26）。
            raise KernelValueError(
                f"runs/{plan.expected_run_id}/ (the path segment {variant.value}"
                f" {segment.segment_index}) cannot be reused: {checked} (D13 §4.7 の 3)"
            )
        if checked is not None:
            result = checked.result
            reused = True
        else:
            result = self._runner.run(config, compiled)
            if not isinstance(result, BacktestResult):
                raise KernelValueError("BacktestRunner.run must return a BacktestResult")
            reused = False
        evaluated = self._evaluate_unit(prepared, result, checked, cache)
        read = self._repository.read_manifest(result.run_id)
        post = (
            check_run_matches(
                expected_config_digest_hex=plan.config_digest.digest.hex,
                expected_run_id_hex=plan.expected_run_id.hex,
                observed_config_digest_hex=(
                    read.config_digest.digest.hex if isinstance(read, RunManifest) else None
                ),
                observed_run_id_hex=result.run_id.hex,
                manifest_detail=None if isinstance(read, RunManifest) else read.detail,
            ),
            check_evaluation_rule(version, evaluated.metric_set_version),
        )
        summaries = result.summaries
        settled: Money | None = None
        final_balance: Money | None = None
        final_equity: Money | None = None
        lines: tuple[HypotheticalCloseLine, ...] = ()
        trades: int | None = None
        if summaries is not None and evaluated.status is EvaluationStatus.COMPLETED:
            final_balance = balance + summaries.realized
            final_equity = summaries.equity_with_mtm
            # 方式 (ii): 区間の終わりの費用を引いた後の資産 = 残高 + 仮に閉じた損益
            # （仮置き 28、実装 PR の仮置き 45）。
            settled = final_balance + summaries.hypothetical_closed
            lines = summaries.hypothetical_close_lines
            trades = result.trade_count
        end = PathSegmentEnd(
            experiment_id=manifest.experiment_id,
            key=key,
            expected_run_id=plan.expected_run_id,
            run_id=result.run_id,
            run_status=result.status,
            run_reused=reused,
            run_evaluation_id=evaluated.run_evaluation_id.digest,
            evaluation_status=evaluated.status,
            result_digest=evaluated.result_digest,
            final_balance=final_balance,
            final_equity=final_equity,
            settlement_lines=lines,
            settled_equity=settled,
            trade_count=trades,
            outcome_checks=post,
        )
        self._store.write_path_segment_end(end)
        return end

    def _run_unit(
        self,
        prepared: PreparedSearch,
        trial: PreparedTrial,
        unit: TrialUnitKey,
        cache: dict[RunId, _Evaluated],
    ) -> _UnitResult:
        """単位1つ: 開始記録 → run（か再利用）→ 評価（か再利用）→ P4・P5 → 試行記録（§10.5）。"""
        manifest = prepared.manifest
        expected_run_id = trial.expected_run_id(unit)
        expected_digest = trial.plan.expected_config_digest(unit)
        self._store.write_trial_start(
            TrialStartRecord(
                experiment_id=manifest.experiment_id, unit=unit, expected_run_id=expected_run_id
            )
        )
        version = manifest.metric_set_version
        checked = self._inspect_existing(
            expected_run_id, expected_digest, version, prepared.calendar, read_metrics=True
        )
        if isinstance(checked, str):
            # 保存の前に全単位を確かめた（D09 §10.5 の注記）。ここで衝突するのは、その後に別の
            # 書き手が成果物を置いた場合だけで、構造エラーとして止める（中断）。
            raise KernelValueError(
                f"runs/{expected_run_id}/ ({unit.variant.value} fold {unit.fold_index}"
                f" {unit.phase.value} trial {unit.trial_index}) cannot be reused any more:"
                f" {checked} (D09 §10.5)"
            )
        if checked is not None:
            result = checked.result
            reused = True
        else:
            compiled = trial.compiled
            if compiled is None:  # pragma: no cover - コンパイル拒否の試行は単位を持たない
                raise KernelValueError("a trial rejected by the compiler has no unit to run")
            result = self._runner.run(trial.run_config(unit), compiled)
            if not isinstance(result, BacktestResult):
                raise KernelValueError("BacktestRunner.run must return a BacktestResult")
            reused = False
        evaluated = self._evaluate_unit(prepared, result, checked, cache)
        read = self._repository.read_manifest(result.run_id)
        post = (
            check_run_matches(
                expected_config_digest_hex=expected_digest.digest.hex,
                expected_run_id_hex=expected_run_id.hex,
                observed_config_digest_hex=(
                    read.config_digest.digest.hex if isinstance(read, RunManifest) else None
                ),
                observed_run_id_hex=result.run_id.hex,
                manifest_detail=None if isinstance(read, RunManifest) else read.detail,
            ),
            check_evaluation_rule(version, evaluated.metric_set_version),
        )
        record = TrialRunRecord(
            experiment_id=manifest.experiment_id,
            unit=unit,
            status=TrialStatus.COMPLETED,
            expected_run_id=expected_run_id,
            run_id=result.run_id,
            run_status=result.status,
            run_reused=reused,
            run_evaluation_id=evaluated.run_evaluation_id.digest,
            evaluation_status=evaluated.status,
            result_digest=evaluated.result_digest,
            outcome_checks=post,
        )
        self._store.write_trial_run(record)
        return _UnitResult(record=record, metrics=evaluated.metrics)

    def _evaluate_unit(
        self,
        prepared: PreparedSearch,
        result: BacktestResult,
        reuse: _Reuse | None,
        cache: dict[RunId, _Evaluated],
    ) -> _Evaluated:
        """評価する（D07 §19.6 の手順2: 保存済みの評価があれば書かずに使い回す）。

        保存済みの評価を使い回すときは、選定と判定に要る指標の値を保存済みの `METRICS` 表から
        読む（D07 v2.11 §19.6、D09 §17.7.4 の2）。**評価をやり直さない**。指標は単位を始めた
        ときの確認（`_inspect_existing`）で読んである。同じ実行の中で同じ `RunId` を持つ単位
        （D09 §6.2）は、最初の単位の評価を使う。
        """
        stored = None if reuse is None else reuse.evaluation
        cached = cache.get(result.run_id)
        if cached is not None:
            if stored is not None and (
                stored.run_evaluation_id != cached.run_evaluation_id
                or stored.result_digest != cached.result_digest
            ):
                raise KernelValueError(
                    f"the stored evaluation {stored.run_evaluation_id} of runs/{result.run_id}/"
                    " is not the evaluation an earlier unit of this execution used (D09 §6.2)"
                )
            return cached
        if stored is not None:
            metrics = None if reuse is None else reuse.metrics
            if metrics is None:
                raise KernelValueError(
                    f"the metrics of the stored evaluation {stored.run_evaluation_id} were not"
                    " read before reusing it (D07 §19.6, D09 §17.7.4)"
                )
            evaluated = _Evaluated(
                run_evaluation_id=stored.run_evaluation_id,
                status=stored.status,
                result_digest=stored.result_digest,
                metric_set_version=stored.metric_set_version,
                metrics=metrics,
            )
        else:
            report = self._evaluator.evaluate(
                result, self._repository, prepared.manifest.metric_set_version, prepared.calendar
            )
            self._repository.write_evaluation(report, report.rows)
            evaluated = _Evaluated(
                run_evaluation_id=report.manifest.run_evaluation_id,
                status=report.status,
                result_digest=report.manifest.result_digest,
                metric_set_version=report.manifest.metric_set_version,
                metrics=report.metrics,
            )
        cache[result.run_id] = evaluated
        return evaluated

    def reproduce_search(
        self,
        prepared: PreparedSearch,
        recorded: Paired[RecordedSearch],
        segments: SegmentPlanner,
    ) -> tuple[ReproductionRow, ...]:
        """探索の実験を別の成果物の基点でやり直し、保存済みの記録と照合する（D13-25 (a)）。

        (1) 独立口座の単位: 試行記録のある各単位の run をやり直し、`run_id` と結果のダイジェストを
        試行記録の値と照合し、選定区間の結果から選定を計算し直して選定記録と照合する。(2) 経路の
        区間: 組ごとに区間 0 から順に区間の run をやり直し、受け渡す残高を計算し直して（記録の残高
        を読んで使わない。仮置き 20）、各区間の `RunId`・結果のダイジェスト・終わりの残高を区間の
        開始記録・終端の記録と照合する。記録は書かない（run と評価の成果物だけを別の基点に書く）。
        """
        manifest = prepared.manifest
        split = manifest.split
        standard = manifest.evaluation_standard
        if not isinstance(split, SplitSpec) or standard is None:  # pragma: no cover
            raise KernelValueError("a search manifest carries its split and evaluation standard")
        version = manifest.metric_set_version
        cache: dict[RunId, _Evaluated] = {}
        results: dict[RunId, BacktestResult] = {}
        rows: list[ReproductionRow] = []

        def rerun(
            config: RunConfig, compiled: CompiledStrategy, run_id: RunId
        ) -> tuple[BacktestResult, _Evaluated]:
            result = results.get(run_id)
            if result is None:
                result = self._runner.run(config, compiled)
                if not isinstance(result, BacktestResult):
                    raise KernelValueError("BacktestRunner.run must return a BacktestResult")
                results[result.run_id] = result
            return result, self._evaluate_unit(prepared, result, None, cache)

        for variant in VARIANTS:
            record = recorded.of(variant)
            selections: list[int | None] = []
            for fold in split.folds:
                train_units: list[TrainUnitEvaluation] = []
                for trial in prepared.trials:
                    index = trial.plan.trial_index
                    if trial.compiled is None:
                        train_units.append(
                            TrainUnitEvaluation(
                                fold_index=fold.fold_index,
                                trial_index=index,
                                status=TrialStatus.FAILED,
                                run_status=None,
                                run_evaluation_id=None,
                                evaluation_status=None,
                                post_run_checks_passed=False,
                                metrics=(),
                            )
                        )
                        continue
                    unit = TrialUnitKey(
                        variant=variant,
                        fold_index=fold.fold_index,
                        phase=TrialPhase.TRAIN,
                        trial_index=index,
                    )
                    done = self._reproduce_unit(prepared, trial, unit, record, rerun, version)
                    rows.append(done[0])
                    train_units.append(_train_unit(done[1]))
                selection = select_trial(fold.fold_index, standard.selection, train_units)
                saved = record.selections.get(fold.fold_index)
                concluded = record.outcome_selections.get(fold.fold_index)
                rows.append(
                    ReproductionRow(
                        item=ReproductionItem.SELECTION,
                        variant=variant,
                        key=f"f{fold.fold_index}",
                        expected=(
                            (
                                "selected_trial_index",
                                None if saved is None else _hex(saved.selected_trial_index),
                            ),
                            (
                                "outcome_selected_trial_index",
                                None if concluded is None else _hex(concluded.selected_trial_index),
                            ),
                        ),
                        observed=(("selected_trial_index", _hex(selection.selected_trial_index)),),
                        verdict=ReproductionVerdict.REPRODUCED
                        if saved == selection and concluded == selection
                        else ReproductionVerdict.RESULT_MISMATCH,
                    )
                )
                selections.append(selection.selected_trial_index)
                if selection.selected_trial_index is not None:
                    chosen = prepared.trials[selection.selected_trial_index]
                    unit = TrialUnitKey(
                        variant=variant,
                        fold_index=fold.fold_index,
                        phase=TrialPhase.VALIDATION,
                        trial_index=chosen.plan.trial_index,
                    )
                    rows.append(
                        self._reproduce_unit(prepared, chosen, unit, record, rerun, version)[0]
                    )
            rows.extend(
                self._reproduce_path(prepared, variant, split, selections, record, segments, rerun)
            )
        return tuple(rows)

    def _reproduce_unit(
        self,
        prepared: PreparedSearch,
        trial: PreparedTrial,
        unit: TrialUnitKey,
        record: RecordedSearch,
        rerun: Callable[[RunConfig, CompiledStrategy, RunId], tuple[BacktestResult, _Evaluated]],
        version: int,
    ) -> tuple[ReproductionRow, _UnitResult]:
        """単位 1 つをやり直して試行記録と照合する（手順 3〜5 を単位ごとに。仮置き 57）。"""
        compiled = trial.compiled
        if compiled is None:  # pragma: no cover - 呼び出し側が分ける
            raise KernelValueError("a trial rejected by the compiler has no unit to run")
        expected_run_id = trial.expected_run_id(unit)
        result, evaluated = rerun(trial.run_config(unit), compiled, expected_run_id)
        saved = record.runs.get(unit)
        read = self._repository.read_manifest(result.run_id)
        post = (
            check_run_matches(
                expected_config_digest_hex=trial.plan.expected_config_digest(unit).digest.hex,
                expected_run_id_hex=expected_run_id.hex,
                observed_config_digest_hex=(
                    read.config_digest.digest.hex if isinstance(read, RunManifest) else None
                ),
                observed_run_id_hex=result.run_id.hex,
                manifest_detail=None if isinstance(read, RunManifest) else read.detail,
            ),
            check_evaluation_rule(version, evaluated.metric_set_version),
        )
        rerun_record = TrialRunRecord(
            experiment_id=prepared.manifest.experiment_id,
            unit=unit,
            status=TrialStatus.COMPLETED,
            expected_run_id=expected_run_id,
            run_id=result.run_id,
            run_status=result.status,
            run_reused=False,
            run_evaluation_id=evaluated.run_evaluation_id.digest,
            evaluation_status=evaluated.status,
            result_digest=evaluated.result_digest,
            outcome_checks=post,
        )
        if saved is None:
            verdict = ReproductionVerdict.RESULT_MISMATCH
        elif saved.run_id != result.run_id:
            verdict = ReproductionVerdict.RUN_ID_MISMATCH
        elif saved.result_digest != evaluated.result_digest:
            verdict = ReproductionVerdict.RESULT_MISMATCH
        else:
            verdict = ReproductionVerdict.REPRODUCED
        row = ReproductionRow(
            item=ReproductionItem.UNIT,
            variant=unit.variant,
            key=f"f{unit.fold_index}_{unit.phase.value}_t{unit.trial_index}",
            expected=(
                ("run_id", None if saved is None else _hex(saved.run_id)),
                ("result_digest", None if saved is None else _hex(saved.result_digest)),
            ),
            observed=(
                ("run_id", _hex(result.run_id)),
                ("result_digest", _hex(evaluated.result_digest)),
            ),
            verdict=verdict,
        )
        return row, _UnitResult(record=rerun_record, metrics=evaluated.metrics)

    def _reproduce_path(
        self,
        prepared: PreparedSearch,
        variant: Variant,
        split: SplitSpec,
        selections: list[int | None],
        record: RecordedSearch,
        planner: SegmentPlanner,
        rerun: Callable[[RunConfig, CompiledStrategy, RunId], tuple[BacktestResult, _Evaluated]],
    ) -> list[ReproductionRow]:
        """経路の区間を区間 0 から順にやり直し、残高を計算し直して照合する（仮置き 20）。

        各区間の `RunId`・結果のダイジェスト・終わりの残高を、区間の開始記録・終端の記録と、結末記録
        の経路の要約の区間の行（`RunId`・初めと終わりの残高）の両方と照合する。最後に経路の終端と
        最後の残高を結末記録と照合する（D13 §4.5 の「再現」）。
        """
        rows: list[ReproductionRow] = []
        concluded = {row.segment_index: row for row in record.outcome_path.segments}
        balance = _initial_balance(prepared, variant)
        end = PathEnd.COMPLETED
        final: Money | None = balance
        for segment in plan_segments(split.folds, selections):
            if segment.trial_index is None:
                continue
            trial = prepared.trials[segment.trial_index]
            compiled = trial.compiled
            if compiled is None:  # pragma: no cover - 選ばれた試行はコンパイルが通っている
                raise KernelValueError("a selected trial has compiled (D09 §7.4)")
            plan = planner.plan(variant, compiled, segment.interval, balance)
            start = record.segment_starts.get(segment.segment_index)
            saved = record.segment_ends.get(segment.segment_index)
            row = concluded.get(segment.segment_index)
            result, evaluated = rerun(plan.config, compiled, plan.expected_run_id)
            summaries = result.summaries
            settled: Money | None = None
            if summaries is not None and evaluated.status is EvaluationStatus.COMPLETED:
                settled = balance + summaries.realized + summaries.hypothetical_closed
            if start is None or saved is None or row is None:
                verdict = ReproductionVerdict.RESULT_MISMATCH
            elif (
                start.expected_run_id != plan.expected_run_id
                or start.config_digest != plan.config_digest
                or saved.run_id != result.run_id
                or row.run_id != result.run_id
            ):
                verdict = ReproductionVerdict.RUN_ID_MISMATCH
            elif (
                saved.result_digest != evaluated.result_digest
                or saved.settled_equity != settled
                or row.initial_balance != balance
                or row.end_balance != settled
            ):
                verdict = ReproductionVerdict.RESULT_MISMATCH
            else:
                verdict = ReproductionVerdict.REPRODUCED
            rows.append(
                ReproductionRow(
                    item=ReproductionItem.SEGMENT,
                    variant=variant,
                    key=f"s{segment.segment_index}",
                    expected=(
                        ("run_id", None if saved is None else _hex(saved.run_id)),
                        ("result_digest", None if saved is None else _hex(saved.result_digest)),
                        ("settled_balance", None if saved is None else _hex(saved.settled_equity)),
                        ("outcome_run_id", None if row is None else _hex(row.run_id)),
                        (
                            "outcome_initial_balance",
                            None if row is None else _hex(row.initial_balance),
                        ),
                        ("outcome_end_balance", None if row is None else _hex(row.end_balance)),
                    ),
                    observed=(
                        ("run_id", _hex(result.run_id)),
                        ("result_digest", _hex(evaluated.result_digest)),
                        ("initial_balance", _hex(balance)),
                        ("settled_balance", _hex(settled)),
                    ),
                    verdict=verdict,
                )
            )
            if settled is None:
                end, final = PathEnd.INCOMPLETE, None
                break
            balance = final = settled
            if settled.amount <= 0:
                end = PathEnd.CAPITAL_LOST
                break
        outcome_path = record.outcome_path
        rows.append(
            ReproductionRow(
                item=ReproductionItem.SEGMENT,
                variant=variant,
                key="end",
                expected=(
                    ("end", outcome_path.end.value),
                    ("final_balance", _hex(outcome_path.final_balance)),
                ),
                observed=(("end", end.value), ("final_balance", _hex(final))),
                verdict=ReproductionVerdict.REPRODUCED
                if (outcome_path.end, outcome_path.final_balance) == (end, final)
                else ReproductionVerdict.RESULT_MISMATCH,
            )
        )
        reran = len(rows) - 1
        recorded_runs = sum(1 for row in outcome_path.segments if row.run_id is not None)
        if reran != len(record.segment_ends) or reran != recorded_runs:
            rows.append(
                ReproductionRow(
                    item=ReproductionItem.SEGMENT,
                    variant=variant,
                    key="count",
                    expected=(
                        ("segments", str(len(record.segment_ends))),
                        ("outcome_segments", str(recorded_runs)),
                    ),
                    observed=(("segments", str(reran)),),
                    verdict=ReproductionVerdict.RESULT_MISMATCH,
                )
            )
        return rows

    @staticmethod
    def _search_outcome(
        prepared: PreparedSearch,
        *,
        status: ExperimentStatus,
        preregistration: PolicyCheckResult,
        failed: tuple[PolicyCheck, ...],
        search: SearchOutcome | None,
    ) -> ExperimentOutcome:
        """探索の実験の結末記録（D09 §10.6 の表）。単数の run・評価の項目は `None`。"""
        return ExperimentOutcome(
            experiment_id=prepared.manifest.experiment_id,
            status=status,
            expected_run_id=None,
            code_digest=prepared.code_digest,
            lock_digest=prepared.lock_digest,
            env_digest=prepared.env_digest,
            git_commit=prepared.git_commit,
            git_dirty=prepared.git_dirty,
            run_id=None,
            run_status=None,
            run_reused=None,
            run_evaluation_id=None,
            evaluation_status=None,
            result_digest=None,
            outcome_checks=(preregistration,),
            failed_checks=() if status is ExperimentStatus.COMPLETED else failed,
            search=search,
        )

    # --- 既存の成果物（D07 §19.6）---------------------------------------------

    def _check_existing(self, prepared: PreparedExperiment) -> _Reuse | ExperimentRefusal | None:
        """手順1〜3・5: 既存の run（と評価）の成果物を確かめる。何も書かない。

        無ければ `None`（run する）。一致すれば再利用の内容。読めない・一致しなければ拒否。
        """
        checked = self._inspect_existing(
            prepared.expected_run_id,
            prepared.expected_config_digest,
            prepared.manifest.metric_set_version,
            prepared.calendar,
        )
        if isinstance(checked, str):
            return self._conflict(prepared, checked)
        return checked

    def check_search_artifacts(self, prepared: PreparedSearch) -> ExperimentRefusal | None:
        """探索の実験の全単位について既存の run 成果物を確かめる（D09 §10.5 の注記・§10.7）。

        対象は記録票が予測ダイジェストを持つすべての単位（コンパイルが通った全試行の選定区間と、
        選ばれるかどうかが決まっていない検証区間も含む）。各単位の予測 `RunId` に D07 §19.6 の
        手順1〜3 を当て、成果物が「無い」か「ある・再利用できる」なら `None`、再利用できない単位が
        1つでもあれば実験全体を拒否する（`RUN_ARTIFACT_CONFLICT`）。**何も書かない**。同じ `RunId`
        を持つ単位（D09 §6.2）は1回だけ確かめる。再利用できる成果物は実行時に読み出す（D09 §10.5）。
        """
        if not isinstance(prepared, PreparedSearch):
            raise KernelValueError("check_search_artifacts requires a PreparedSearch")
        version = prepared.manifest.metric_set_version
        seen: set[RunId] = set()
        for trial in prepared.trials:
            for unit, expected_digest in trial.plan.expected_config_digests:
                run_id = trial.expected_run_id(unit)
                if run_id in seen:
                    continue
                seen.add(run_id)
                checked = self._inspect_existing(
                    run_id, expected_digest, version, prepared.calendar, read_metrics=True
                )
                if isinstance(checked, str):
                    return ExperimentRefusal(
                        kind=RefusalKind.RUN_ARTIFACT_CONFLICT,
                        experiment_id=prepared.manifest.experiment_id,
                        detail=(
                            f"runs/{run_id}/ (the unit fold {unit.fold_index}"
                            f" {unit.phase.value} trial {unit.trial_index}) already exists and"
                            f" cannot be reused: {checked}. Nothing was written (no manifest,"
                            " no outcome). Replace the run with `odyssey-fx run --replace`, or"
                            " move the evaluation directory away (D07 §19.6, D09 §10.5)"
                        ),
                    )
        return None

    def _inspect_existing(
        self,
        run_id: RunId,
        expected_digest: ConfigDigest,
        metric_set_version: int,
        calendar: TradingCalendar,
        *,
        read_metrics: bool = False,
    ) -> _Reuse | str | None:
        """D07 §19.6 の手順1〜3・5 を1つの予測 `RunId` に当てる（読むだけ）。

        無ければ `None`、再利用できれば再利用の内容、できなければ理由の文字列。
        `read_metrics` なら（探索の単位。D09 §10.5）、再利用する評価の指標の表も
        `read_evaluation_metrics` で読み、読めなければ再利用できない理由にする（再評価で補わない。
        D07 v2.11 §19.6、D09 §17.7.4 の2・3）。
        """
        if not self._repository.run_exists(run_id):
            return None
        manifest = self._repository.read_manifest(run_id)
        if isinstance(manifest, ManifestReadFailure):
            return f"its run manifest cannot be read: {manifest.detail}"
        if not isinstance(manifest, RunManifest):
            raise KernelValueError("ResultRepository.read_manifest returned an unexpected value")
        if manifest.run_id != run_id or manifest.config_digest != expected_digest:
            return (
                "its run manifest records the run"
                f" {manifest.run_id} with the config digest {manifest.config_digest.digest.hex},"
                f" not the expected {run_id} / {expected_digest.digest.hex}"
            )
        result = self._repository.read_result(run_id)
        if isinstance(result, ResultReadFailure):
            return f"its result cannot be read: {result.detail}"
        if not isinstance(result, BacktestResult):
            raise KernelValueError("ResultRepository.read_result returned an unexpected value")
        evaluation_id = run_evaluation_id(
            run_id,
            metric_set_version,
            self._evaluator.code_digest,
            (calendar.id, calendar.version),
        )
        stored = self._repository.read_evaluation(run_id, evaluation_id)
        if isinstance(stored, EvaluationReadFailure):
            return f"its evaluation {evaluation_id} exists but cannot be read: {stored.detail}"
        if stored is not None and (
            stored.run_id != run_id
            or stored.run_evaluation_id != evaluation_id
            or stored.metric_set_version != metric_set_version
        ):
            return (
                f"its evaluation directory {evaluation_id} holds the evaluation"
                f" {stored.run_evaluation_id} of the run {stored.run_id}"
            )
        metrics: tuple[MetricRecord, ...] | None = None
        if read_metrics and stored is not None:
            read = self._repository.read_evaluation_metrics(run_id, evaluation_id)
            if isinstance(read, EvaluationReadFailure):
                return (
                    f"the metrics of its evaluation {evaluation_id} cannot be read: {read.detail}"
                )
            if not isinstance(read, tuple) or not all(
                isinstance(item, MetricRecord) for item in read
            ):
                raise KernelValueError(
                    "ResultRepository.read_evaluation_metrics returned an unexpected value"
                )
            metrics = read
        return _Reuse(result=result, evaluation=stored, metrics=metrics)

    @staticmethod
    def _conflict(prepared: PreparedExperiment, reason: str) -> ExperimentRefusal:
        return ExperimentRefusal(
            kind=RefusalKind.RUN_ARTIFACT_CONFLICT,
            experiment_id=prepared.manifest.experiment_id,
            detail=(
                f"runs/{prepared.expected_run_id}/ already exists and cannot be reused: {reason}."
                " Nothing was written (no manifest, no outcome). Replace the run with"
                " `odyssey-fx run --replace`, or move the evaluation directory away"
                " (D07 §19.6)"
            ),
        )

    # --- 評価と事後検査 ---------------------------------------------------------

    def _evaluate(self, prepared: PreparedExperiment, result: BacktestResult) -> EvaluationReport:
        report = self._evaluator.evaluate(
            result,
            self._repository,
            prepared.manifest.metric_set_version,
            prepared.calendar,
        )
        self._repository.write_evaluation(report, report.rows)
        return report

    def _run_matches(
        self, prepared: PreparedExperiment, result: BacktestResult
    ) -> PolicyCheckResult:
        """P4: run manifest の `ConfigDigest` と実際の `run_id` が予測どおりか。"""
        read = self._repository.read_manifest(result.run_id)
        observed_digest: str | None
        detail: str | None
        if isinstance(read, RunManifest):
            observed_digest = read.config_digest.digest.hex
            detail = None
        else:
            observed_digest = None
            detail = read.detail
        return check_run_matches(
            expected_config_digest_hex=prepared.expected_config_digest.digest.hex,
            expected_run_id_hex=prepared.expected_run_id.hex,
            observed_config_digest_hex=observed_digest,
            observed_run_id_hex=result.run_id.hex,
            manifest_detail=detail,
        )

    @staticmethod
    def _outcome(
        prepared: PreparedExperiment,
        *,
        status: ExperimentStatus,
        checks: tuple[PolicyCheckResult, ...],
        failed: tuple[PolicyCheckResult, ...],
        run: tuple[BacktestResult, bool] | None = None,
        evaluation: tuple[ContentDigest, EvaluationStatus, ContentDigest] | None = None,
    ) -> ExperimentOutcome:
        result, reused = (None, None) if run is None else run
        evaluation_id, evaluation_status, result_digest = (
            (None, None, None) if evaluation is None else evaluation
        )
        return ExperimentOutcome(
            experiment_id=prepared.manifest.experiment_id,
            status=status,
            expected_run_id=prepared.expected_run_id,
            code_digest=prepared.code_digest,
            lock_digest=prepared.lock_digest,
            env_digest=prepared.env_digest,
            git_commit=prepared.git_commit,
            git_dirty=prepared.git_dirty,
            run_id=None if result is None else result.run_id,
            run_status=None if result is None else result.status,
            run_reused=reused,
            run_evaluation_id=evaluation_id,
            evaluation_status=evaluation_status,
            result_digest=result_digest,
            outcome_checks=checks,
            failed_checks=() if status is ExperimentStatus.COMPLETED else failed_checks(failed),
        )


def _unit_fields(done: _UnitResult) -> dict[str, object]:
    """試行記録と評価の指標から、選定・判定の関数の入力の項目を作る（D09 §7.2〜§7.4）。

    事後検査の真偽値は試行記録の P4・P5 の記録から導く（失敗した検査の詳細は試行記録に残る。
    D09 §10.8・§17.7.3 の3）。評価が `COMPLETED` でない単位は指標の行を持たない（D07 §10.1）。
    """
    record = done.record
    return {
        "fold_index": record.unit.fold_index,
        "trial_index": record.unit.trial_index,
        "status": record.status,
        "run_status": record.run_status,
        "run_evaluation_id": record.run_evaluation_id,
        "evaluation_status": record.evaluation_status,
        "post_run_checks_passed": record.post_run_checks_passed,
        "metrics": done.metrics if record.evaluation_status is EvaluationStatus.COMPLETED else (),
    }


def _train_unit(done: _UnitResult) -> TrainUnitEvaluation:
    return TrainUnitEvaluation(**_unit_fields(done))  # type: ignore[arg-type]


def _validation_unit(done: _UnitResult) -> ValidationUnitEvaluation:
    return ValidationUnitEvaluation(**_unit_fields(done))  # type: ignore[arg-type]


# --- 別プロセスでの再現（D07 §21）----------------------------------------------


class ReproductionVerdict(Enum):
    """再現の判定5値（D07 §21.2）。"""

    #: `run_id` と `result_digest` が結末記録と一致した。
    REPRODUCED = "REPRODUCED"
    #: 記録票の入力から組み立てた（または run した）`run_id` / `ConfigDigest` が記録と違う。
    RUN_ID_MISMATCH = "RUN_ID_MISMATCH"
    #: `run_id` は一致したが `result_digest` が違う。
    RESULT_MISMATCH = "RESULT_MISMATCH"
    #: 現在の環境のダイジェストが結末記録と違う（run しない。Q12 決定）。
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    #: 記録票が改変された（識別子の再計算・本文の SHA-256・結末記録との対応が合わない）。
    MANIFEST_TAMPERED = "MANIFEST_TAMPERED"


@dataclass(frozen=True, slots=True)
class ReproductionReport:
    """再現の報告（D07 §21.2。`--out` の下の `reproduction.json` に書く）。

    探索の実験（2026-10-21 改訂。D13-25 (a)）は単数の `run_id`・結果のダイジェストを持たない
    （`expected_*` / `observed_*` は `None`。照合の行は `reproduction.json` に書く。実装 PR の
    仮置き 57）。
    """

    experiment_id: ExperimentId
    verdict: ReproductionVerdict
    expected_run_id: RunId | None
    observed_run_id: RunId | None
    expected_result_digest: ContentDigest | None
    observed_result_digest: ContentDigest | None

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, ExperimentId):
            raise KernelValueError("ReproductionReport.experiment_id must be an ExperimentId")
        if not isinstance(self.verdict, ReproductionVerdict):
            raise KernelValueError("ReproductionReport.verdict must be a ReproductionVerdict")
        if self.expected_run_id is not None and not isinstance(self.expected_run_id, RunId):
            raise KernelValueError("ReproductionReport.expected_run_id must be a RunId")
        if self.observed_run_id is not None and not isinstance(self.observed_run_id, RunId):
            raise KernelValueError("ReproductionReport.observed_run_id must be a RunId or None")
        if self.expected_result_digest is not None and not isinstance(
            self.expected_result_digest, ContentDigest
        ):
            raise KernelValueError("ReproductionReport.expected_result_digest must be a digest")
        if self.observed_result_digest is not None and not isinstance(
            self.observed_result_digest, ContentDigest
        ):
            raise KernelValueError("ReproductionReport.observed_result_digest must be a digest")


def judge_reproduction(
    *,
    expected_run_id: RunId,
    observed_run_id: RunId,
    expected_result_digest: ContentDigest,
    observed_result_digest: ContentDigest,
) -> ReproductionVerdict:
    """手順5: run し直した結果を結末記録と比べる（D07 §21.2）。"""
    if observed_run_id != expected_run_id:
        return ReproductionVerdict.RUN_ID_MISMATCH
    if observed_result_digest != expected_result_digest:
        return ReproductionVerdict.RESULT_MISMATCH
    return ReproductionVerdict.REPRODUCED


# --- 探索の実験の別プロセスでの再現（D13 §4.5・§4.7、D09 §11.4・D07 §21 の 2026-10-21 改訂）---


class ReproductionItem(Enum):
    """再現の照合の行の種類（実装 PR の仮置き 57）。"""

    #: 独立口座の単位の run（`run_id` と結果のダイジェスト）。
    UNIT = "UNIT"
    #: fold の選定（選定区間の結果から選び直した選定記録）。
    SELECTION = "SELECTION"
    #: 連続口座の検証経路の区間の run（`RunId`・結果のダイジェスト・終わりの残高）。
    SEGMENT = "SEGMENT"


@dataclass(frozen=True, slots=True)
class ReproductionRow:
    """再現の照合の 1 行（実装 PR の仮置き 57）。判定は今の 5 値（D07 §21.2）。

    `key` は行の鍵の表示（単位は `f<k>_<局面>_t<i>`、選定は `f<k>`、区間は `s<b>`）。
    `expected` / `observed` は比べた値の表示（`run_id`・結果のダイジェスト・残高・選んだ試行）。
    """

    item: ReproductionItem
    variant: Variant
    key: str
    expected: tuple[tuple[str, str | None], ...]
    observed: tuple[tuple[str, str | None], ...]
    verdict: ReproductionVerdict


@dataclass(frozen=True, slots=True)
class RecordedSearch:
    """組ごとの保存済みの探索の記録（再現の照合先）。

    - `runs` / `selections` / `segment_starts` / `segment_ends`: `search/<組>/` の試行記録・
      選定記録・区間の開始記録と終端の記録（実験の版のディレクトリから読んだもの）。
    - `outcome_selections` / `outcome_path`: 結末記録の組の選定と経路の要約（D13 §4.5 の「再現」:
      選定は選定記録と、区間の残高は開始・終端の記録と結末記録の両方と照合する）。
    """

    runs: Mapping[TrialUnitKey, TrialRunRecord]
    selections: Mapping[int, FoldSelection]
    segment_starts: Mapping[int, PathSegmentStart]
    segment_ends: Mapping[int, PathSegmentEnd]
    outcome_selections: Mapping[int, FoldSelection]
    outcome_path: PathSummary


#: 全体の判定に写す順（最初に当てはまる不一致。実装 PR の仮置き 57）。
_REPRODUCTION_PRIORITY: Final = (
    ReproductionVerdict.MANIFEST_TAMPERED,
    ReproductionVerdict.ENVIRONMENT_MISMATCH,
    ReproductionVerdict.RUN_ID_MISMATCH,
    ReproductionVerdict.RESULT_MISMATCH,
)


def overall_reproduction(rows: Sequence[ReproductionRow]) -> ReproductionVerdict:
    """全行が `REPRODUCED` なら `REPRODUCED`、それ以外は優先順で最初の不一致（仮置き 57）。"""
    verdicts = {row.verdict for row in rows}
    for verdict in _REPRODUCTION_PRIORITY:
        if verdict in verdicts:
            return verdict
    return ReproductionVerdict.REPRODUCED


def _hex(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, RunId):
        return value.hex
    if isinstance(value, ContentDigest):
        return value.hex
    return str(value)
