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

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId, RunId
from odyssey_fx.common.refs import CodeDigest, ConfigDigest, ContentDigest, EnvDigest, LockDigest
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
    ComparisonBasis,
    FoldEvidence,
    FoldSelection,
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
    binding_mismatch,
    build_search_outcome,
    finished_entry,
    has_finished_line,
    last_digest,
    next_execution,
    select_trial,
    started_line_of,
    unmatched_binding,
)
from odyssey_fx.evaluation.domain.splits import SplitSpec
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy

__all__ = [
    "ExperimentRefusal",
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
        basis: ComparisonBasis | None,
        execution_nonce: str,
        out_base: str,
        running: str,
    ) -> ExperimentOutcome | ExperimentRefusal:
        """探索の実験の1回の実行を進める（D09 §10.7 の状態×出来事表）。

        - `basis`: この実行の比較の前提（合成が組み立てる。事前検査が全件合格なら必ず渡す）。
        - `execution_nonce`: 実行ごとの乱数の文字列（`app` が作る。D09 §10.12.1 の W3）。
        - `out_base` / `running`: 成果物の基点と、走らせている実験の版のディレクトリの基点からの
          パス（成果物からの逆照合 L11 と数え直し待ちの判定に使う。D09 §10.12.2）。

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
        if not isinstance(basis, ComparisonBasis):
            raise KernelValueError("a search that passed the pre-run checks needs its basis")

        started, binding = self._start_ledger(prepared, basis, execution_nonce, out_base, running)

        # 探索中: fold を番号の昇順に進める（D09 §5.4・§6.6）。
        evidence, selections, records = self._run_folds(prepared)
        standard = manifest.evaluation_standard
        if standard is None:  # pragma: no cover - 探索の記録票は評価基準を持つ
            raise KernelValueError("a search manifest carries its evaluation standard")
        search = build_search_outcome(standard, evidence, binding.execution)
        failed = failed_checks([check for record in records for check in record.outcome_checks])
        status = ExperimentStatus.FAILED_POST_RUN_CHECK if failed else ExperimentStatus.COMPLETED

        # 終端の書き込み: (1) 集約表 → (2) 結末記録 → (3) 台帳の結末の行（D09 §10.7）。
        self._store.write_aggregate_tables(manifest, selections, records)
        outcome = self._search_outcome(
            prepared,
            status=status,
            preregistration=preregistration,
            failed=failed,
            search=search,
        )
        self._store.write_outcome(outcome)
        self._finish_ledger(started, binding, outcome)
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
        basis: ComparisonBasis,
        execution_nonce: str,
        out_base: str,
        running: str,
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
            status=None,
            verdict=None,
            frequency_class=None,
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
        self, started: TrialLedgerEntry, binding: TrialLedgerBinding, outcome: ExperimentOutcome
    ) -> None:
        """結末の行を足す（D09 §10.12.3 の3）。照合が合わなければ書かずに止める。"""
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
        entry = finished_entry(
            started,
            status=outcome.status,
            verdict=search.verdict,
            frequency_class=None if search.frequency is None else search.frequency.class_name,
        )
        self._append(TrialLedgerLine.of(entry, last_digest(contents.lines)), stopped)

    def _run_folds(
        self, prepared: PreparedSearch
    ) -> tuple[tuple[FoldEvidence, ...], tuple[FoldSelection, ...], tuple[TrialRunRecord, ...]]:
        """fold ごとに選定区間の全単位 → 選定記録 → 検証区間の単位（D09 §4.1 の4・§6.6）。"""
        manifest = prepared.manifest
        split = manifest.split
        standard = manifest.evaluation_standard
        if not isinstance(split, SplitSpec) or standard is None:  # pragma: no cover
            raise KernelValueError("a search manifest carries its split and evaluation standard")
        cache: dict[RunId, _Evaluated] = {}
        evidence: list[FoldEvidence] = []
        selections: list[FoldSelection] = []
        records: list[TrialRunRecord] = []
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
                    fold_index=fold.fold_index, phase=TrialPhase.TRAIN, trial_index=index
                )
                done = self._run_unit(prepared, trial, unit, cache)
                records.append(done.record)
                train_units.append(_train_unit(done))
            # 選定区間の結果だけから選び、検証区間の単位より前に選定記録を保存する（D09 §7.5）。
            selection = select_trial(fold.fold_index, standard.selection, train_units)
            self._store.write_selection(selection)
            selections.append(selection)
            validation: ValidationUnitEvaluation | None = None
            if selection.selected_trial_index is not None:
                chosen = prepared.trials[selection.selected_trial_index]
                unit = TrialUnitKey(
                    fold_index=fold.fold_index,
                    phase=TrialPhase.VALIDATION,
                    trial_index=chosen.plan.trial_index,
                )
                done = self._run_unit(prepared, chosen, unit, cache)
                records.append(done.record)
                validation = _validation_unit(done)
            evidence.append(
                FoldEvidence(
                    fold=fold,
                    selection=selection,
                    train_units=tuple(train_units),
                    validation=validation,
                )
            )
        return tuple(evidence), tuple(selections), tuple(records)

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
                f"runs/{expected_run_id}/ (fold {unit.fold_index} {unit.phase.value} trial"
                f" {unit.trial_index}) cannot be reused any more: {checked} (D09 §10.5)"
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
