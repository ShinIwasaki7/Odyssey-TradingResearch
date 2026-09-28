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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId, RunId
from odyssey_fx.common.refs import CodeDigest, ContentDigest, EnvDigest, LockDigest
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, EvaluationReport
from odyssey_fx.evaluation.application.manifest import RunEvaluationId, run_evaluation_id
from odyssey_fx.evaluation.application.ports import (
    BacktestRunner,
    EvaluationReadFailure,
    ExperimentStore,
    ManifestReadFailure,
    ManifestSaveResult,
    ResultReadFailure,
    ResultRepository,
    StoredEvaluation,
)
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentManifest,
    ExperimentOutcome,
    ExperimentStatus,
)
from odyssey_fx.evaluation.domain.research_policy import (
    PolicyCheckResult,
    all_passed,
    check_evaluation_rule,
    check_preregistration,
    check_run_matches,
    failed_checks,
)
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy

__all__ = [
    "ExperimentRefusal",
    "PreparedExperiment",
    "RefusalKind",
    "ReproductionReport",
    "ReproductionVerdict",
    "RunExperiment",
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

    # --- 既存の成果物（D07 §19.6）---------------------------------------------

    def _expected_evaluation_id(self, prepared: PreparedExperiment) -> RunEvaluationId:
        calendar = prepared.calendar
        return run_evaluation_id(
            prepared.expected_run_id,
            prepared.manifest.metric_set_version,
            self._evaluator.code_digest,
            (calendar.id, calendar.version),
        )

    def _check_existing(self, prepared: PreparedExperiment) -> _Reuse | ExperimentRefusal | None:
        """手順1〜3・5: 既存の run（と評価）の成果物を確かめる。何も書かない。

        無ければ `None`（run する）。一致すれば再利用の内容。読めない・一致しなければ拒否。
        """
        run_id = prepared.expected_run_id
        if not self._repository.run_exists(run_id):
            return None
        manifest = self._repository.read_manifest(run_id)
        if isinstance(manifest, ManifestReadFailure):
            return self._conflict(prepared, f"its run manifest cannot be read: {manifest.detail}")
        if not isinstance(manifest, RunManifest):
            raise KernelValueError("ResultRepository.read_manifest returned an unexpected value")
        expected_digest = prepared.manifest.expected_config_digest
        if manifest.run_id != run_id or manifest.config_digest != expected_digest:
            return self._conflict(
                prepared,
                "its run manifest records the run"
                f" {manifest.run_id} with the config digest {manifest.config_digest.digest.hex},"
                f" not the expected {run_id} / {expected_digest.digest.hex}",
            )
        result = self._repository.read_result(run_id)
        if isinstance(result, ResultReadFailure):
            return self._conflict(prepared, f"its result cannot be read: {result.detail}")
        if not isinstance(result, BacktestResult):
            raise KernelValueError("ResultRepository.read_result returned an unexpected value")
        evaluation_id = self._expected_evaluation_id(prepared)
        stored = self._repository.read_evaluation(run_id, evaluation_id)
        if isinstance(stored, EvaluationReadFailure):
            return self._conflict(
                prepared,
                f"its evaluation {evaluation_id} exists but cannot be read: {stored.detail}",
            )
        if stored is not None and (
            stored.run_id != run_id
            or stored.run_evaluation_id != evaluation_id
            or stored.metric_set_version != prepared.manifest.metric_set_version
        ):
            return self._conflict(
                prepared,
                f"its evaluation directory {evaluation_id} holds the evaluation"
                f" {stored.run_evaluation_id} of the run {stored.run_id}",
            )
        return _Reuse(result=result, evaluation=stored)

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
            expected_config_digest_hex=prepared.manifest.expected_config_digest.digest.hex,
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
    """再現の報告（D07 §21.2。`--out` の下の `reproduction.json` に書く）。"""

    experiment_id: ExperimentId
    verdict: ReproductionVerdict
    expected_run_id: RunId
    observed_run_id: RunId | None
    expected_result_digest: ContentDigest
    observed_result_digest: ContentDigest | None

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, ExperimentId):
            raise KernelValueError("ReproductionReport.experiment_id must be an ExperimentId")
        if not isinstance(self.verdict, ReproductionVerdict):
            raise KernelValueError("ReproductionReport.verdict must be a ReproductionVerdict")
        if not isinstance(self.expected_run_id, RunId):
            raise KernelValueError("ReproductionReport.expected_run_id must be a RunId")
        if self.observed_run_id is not None and not isinstance(self.observed_run_id, RunId):
            raise KernelValueError("ReproductionReport.observed_run_id must be a RunId or None")
        if not isinstance(self.expected_result_digest, ContentDigest):
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
