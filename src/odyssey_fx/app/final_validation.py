"""最終検証のコマンドの結線（`odyssey-fx experiment final-validate`。D09 v0.5 §9.10。R4）。

関所の最終評価の許可（ADR-0036 第3項。品質確認の許可とは別の型・別の手順）の 5 段（D09 §9.10.2）を
この順で fail-closed に行う。**段 1 と段 2 では何も書かない**（`.git` の中の取得を除く。例外は
状態 D の台帳の書き足しと申告「使った」の台帳の行。D09 §9.10.6・§9.10.10 の 6）。

1. **申請の確かめ**: origin の既定ブランチの名前を尋ねて取得し、申請の状態（D09 §9.10.6）と申請の
   資格（判定・試行台帳・審査・固定・申告・順番。D09 §9.10.10）を確かめる。
2. **許可の前の確かめ**: 再計算とウォームアップ（`FinalRunner.prepare`。段 2 (f)）→ 窓と消費の
   対象の導出（(b)）→ origin の確かめ（(a)）→ 出力先（(c)）→ 実行前のデータ能力検査のうち戦略に
   よらない部分（(e)）→ run の予測（`FinalRunner.plan`。(g)）→ 検査 P2F の (1)・(3)（(h)）。
   消費の対象が `SEALED` であること（(d)）は許可の発行（段 3）で関所が確かめる。
3. **許可の発行**: 許可の識別子（128 ビットの乱数）とロック `final/<execution>/final.lock`。
4. **消費**: 消費の行（版 1。D09 §9.10.4）を追記して 1 つの commit にし、origin の既定ブランチへ
   push する。拒否されたら commit を取り消し、追記したファイルを戻す（仮置き R2-2）。成功したら
   控え `permit.json`。
5. **公開と評価**: 検査 P2F の (2) と許可集合の照合 → 両組の run と評価（`FinalRunner.run`）→
   P4F・P5 → 判定（要決定 R2-7 (a)）→ 最終検証の記録 → 台帳の行 → ロックの削除。

**消費の後のやり直し（`--resume`）**は入口だけを置き、やり直しの条件（仮置き R2-21〜R2-29。
2026-10-30 の時点で未決）が人間に決まるまで常に拒否する（状態 C なら終了コード 8
`RESUME_NOT_ALLOWED`、他の状態なら終了コード 7）。

**最終検証の run（段 2 (f)(g) と段 5 の run・評価）の本番の実装は、最終検証の run の実行設定が
複数の snapshot をどう指すか（R4 の要決定 A）の決定の後**。本番の結線（`UnavailableFinalRunner`）は
段 2 の最初で止め（終了コード 7 `FINAL_RUN_UNAVAILABLE`。何も書かない）、手順の残りは人工の
`FinalRunner` を渡す受入テストで確かめる。順 6 では実データの最終検証を行わない（研究候補 0 件。
D15 §0.1.2 の §3）。

**出力は件数・時刻・識別子・終了コード・理由の識別子・判定の値だけ**。価格と指標の値は画面に
出さない（指標は最終検証の記録とレポートに出す。D09 §9.10.7、D15 §9）。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Final, Protocol

from odyssey_fx.app.config.final_validation import (
    FIXATIONS_DIRECTORY,
    Fixation,
    fixation_path,
    load_declaration,
    load_fixation,
)
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.research_policy import (
    load_research_policy_registry,
    research_policy_registry_path,
)
from odyssey_fx.app.config.sealed_window import SEALED_WINDOW_PATH, load_sealed_window
from odyssey_fx.app.final_validation_records import (
    FINAL_EVALUATION_FILE,
    FINAL_LEDGER_PATH,
    FINAL_LOCK_FILE,
    PERMIT_FILE,
    REPORT_FILE,
    FinalLedger,
    FinalLedgerError,
    append_ledger_row,
    candidate_payload,
    create_lock,
    final_directory,
    parse_final_ledger,
    read_final_ledger,
    write_json_once,
)
from odyssey_fx.app.holdout_access_log import ACCESS_LOG_FILE, MANIFEST_FILE, GitHoldoutAccessLog
from odyssey_fx.app.sealed_intake import parse_consumption_bytes
from odyssey_fx.common.canonical import encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    TRIAL_LEDGER_PATH,
    parse_trial_ledger_bytes,
    read_experiment_manifest,
    read_experiment_outcome,
    read_ledger_binding,
)
from odyssey_fx.evaluation.application.holdout_gate import (
    consumption_rows_present,
    final_partitions_match_permit,
    issue_final_evaluation_permit,
)
from odyssey_fx.evaluation.application.ports import (
    HoldoutAccessLogStop,
    PushOutcomeUnknown,
    TrialLedgerReadFailure,
)
from odyssey_fx.evaluation.domain.experiment import ExperimentStatus
from odyssey_fx.evaluation.domain.final_validation import (
    ApplicationState,
    CandidateKey,
    FinalEvaluationPermit,
    FinalLedgerResult,
    FinalReason,
    FinalRunSummary,
    FinalVariantOutcome,
    FinalWindow,
    PredictedRun,
    SealedPartitionFact,
    WindowRefusal,
    combine_final,
    derive_application_state,
    derive_final_window,
    judge_final_variant,
)
from odyssey_fx.evaluation.domain.research_policy import current_standard_version
from odyssey_fx.evaluation.domain.search import (
    CombinedVerdict,
    EvaluationStandard,
    FrequencyClass,
    SearchVerdict,
    StandardPurpose,
    TrialLedgerEvent,
    finished_line_of,
    frequency_class_of,
    started_line_of,
)
from odyssey_fx.evaluation.domain.splits import FinalHoldoutSpec
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore, manifest_from_bytes
from odyssey_fx.marketdata.domain.access import OANDA_SEALED_CLASSES, HoldoutState
from odyssey_fx.marketdata.domain.consumption_log import (
    AccessLogReadFailure,
    ConsumptionReason,
    ConsumptionRow,
    ParsedAccessLog,
)
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest

__all__ = [
    "ADOPTION_REVIEWS_PATH",
    "EXIT_FAILED",
    "EXIT_FAILED_AFTER_CONSUMPTION",
    "EXIT_GATE_REFUSED",
    "EXIT_OK",
    "EXIT_READ_ERROR",
    "EXIT_REFUSED",
    "CandidateFacts",
    "CandidateSource",
    "ExperimentCandidates",
    "FinalMode",
    "FinalRunOutput",
    "FinalRunPlan",
    "FinalRunPreparation",
    "FinalRunner",
    "FinalValidationOutcome",
    "FinalValidationRequest",
    "UnavailableFinalRunner",
    "final_validate",
]

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_READ_ERROR: Final = 2
#: 申請の拒否（D09 §9.10.7。仮置き R2-4）。
EXIT_REFUSED: Final = 7
#: 関所の拒否。
EXIT_GATE_REFUSED: Final = 8
#: 消費の後の失敗。
EXIT_FAILED_AFTER_CONSUMPTION: Final = 9

#: 採用審査の台帳（D16 v0.2 §10.1。R3 が読込を実装するまでの最小の読み方。仮置き R2-7）。
ADOPTION_REVIEWS_PATH: Final = Path("research") / "adoption_reviews.jsonl"
#: 封印の snapshot の時刻の規則の表の承認済みの一覧（D03 §15.9 の 2・§16.2）。
SEALED_APPROVED_TABLES: Final = Path("docs/design/time_rules/approved_time_rule_tables_sealed.yaml")

_GATE_REASONS: Final = frozenset(
    {
        FinalReason.CONSUMED_WITHOUT_RECORD,
        FinalReason.RESUME_NOT_ALLOWED,
        FinalReason.ORIGIN_NOT_CONFIRMED,
        FinalReason.CONSUMPTION_LOG_BROKEN,
        FinalReason.NOT_SEALED,
        FinalReason.STATE_DIFFERS_ACROSS_SNAPSHOTS,
        FinalReason.PUSH_REJECTED,
    }
)
_AFTER_CONSUMPTION_REASONS: Final = frozenset(
    {
        FinalReason.RECORD_NOT_ON_ORIGIN,
        FinalReason.PERMIT_RECORD_FAILED,
        FinalReason.FINAL_RUN_FAILED,
        FinalReason.P4F_NOT_PASSED,
        FinalReason.P5_NOT_PASSED,
        FinalReason.CLOSED_AS_FAILED,
    }
)


def exit_code_of(reason: FinalReason) -> int:
    """理由の識別子から終了コード（D09 §9.10.7 の表）。"""
    if reason in _GATE_REASONS:
        return EXIT_GATE_REFUSED
    if reason in _AFTER_CONSUMPTION_REASONS:
        return EXIT_FAILED_AFTER_CONSUMPTION
    return EXIT_REFUSED


class FinalMode(Enum):
    """起動の形（D09 §9.10.7。仮置き R2-4）。"""

    APPLY = "apply"
    DRY_RUN = "dry_run"
    RESUME = "resume"
    CLOSE_FAILED = "close_failed"
    WITHDRAW = "withdraw"


@dataclass(frozen=True, slots=True)
class FinalValidationRequest:
    """コマンドの引数（D09 §9.10.7）。"""

    experiment_dir: Path
    execution: int
    repo_root: Path
    snapshots_root: Path
    out_root: Path
    declaration: Path | None
    by: str
    mode: FinalMode = FinalMode.APPLY


@dataclass(slots=True)
class FinalValidationOutcome:
    """コマンドの結果（CLI が表示する行と終了コード）。"""

    exit_code: int
    reason: FinalReason | None
    detail: str
    lines: list[str] = field(default_factory=list)
    state: ApplicationState | None = None
    permit_id: str | None = None
    commit: str | None = None
    verdict: CombinedVerdict | None = None


class _Stop(Exception):
    """手順を止める（終了コード・理由・人間が読む説明）。"""

    def __init__(self, exit_code: int, reason: FinalReason | None, detail: str) -> None:
        super().__init__(detail)
        self.exit_code = exit_code
        self.reason = reason
        self.detail = detail


def _refuse(refusal: WindowRefusal) -> _Stop:
    return _Stop(exit_code_of(refusal.reason), refusal.reason, refusal.detail)


def _reason(reason: FinalReason, detail: str) -> _Stop:
    return _Stop(exit_code_of(reason), reason, detail)


# --- 候補の事実（記録票・結末記録・束縛の記録・登録簿）--------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateFacts:
    """申請の資格の確かめに使う候補の事実（研究履歴の成果物から。価格・指標の値を含まない）。"""

    key: CandidateKey
    completed: bool
    verdicts: Paired[SearchVerdict] | None
    combined: SearchVerdict | None
    policy_id: str
    policy_version: int
    current_policy_version: int | None
    purpose: StandardPurpose
    final_holdout: FinalHoldoutSpec | None
    symbols: frozenset[str]
    started_line_digest: str | None
    standard: EvaluationStandard
    requirements: Paired[FrequencyClass | None]
    final_trials: Paired[int | None]
    metric_set_version: int
    record_snapshots: Paired[str]
    manifest_sha256: str
    outcome_sha256: str


class CandidateSource(Protocol):
    """候補の事実を読む口（本番は `ExperimentCandidates`）。読めなければ `ConfigError`。"""

    def read(self, experiment_dir: Path, execution: int) -> CandidateFacts: ...


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


class ExperimentCandidates:
    """実験の版のディレクトリの記録票・結末記録・束縛の記録と、研究ポリシーの登録簿から読む。"""

    def __init__(self, repo_root: Path) -> None:
        self._root = Path(repo_root)

    def read(self, experiment_dir: Path, execution: int) -> CandidateFacts:
        directory = Path(experiment_dir).absolute()
        if ".." in directory.parts:
            raise ConfigError(f"`--experiment-dir` に `..` を含めない: {experiment_dir}")
        parents = directory.parents
        if len(parents) < 4 or parents[1].name != "experiments" or parents[2].name != "runs":
            raise ConfigError(
                "`--experiment-dir` は runs/experiments/<名前>/v<版> の形のディレクトリを指す"
                f"（D07 §19.1）: {experiment_dir}"
            )
        try:
            manifest = read_experiment_manifest(directory)
            outcome = read_experiment_outcome(directory)
            binding = read_ledger_binding(directory)
        except KernelValueError as exc:
            raise ConfigError(f"記録票・結末記録・束縛の記録を読めない: {exc}") from exc
        if not manifest.is_search or manifest.evaluation_standard is None:
            raise ConfigError(f"{experiment_dir} は探索の実験の版のディレクトリではない")
        if outcome is not None and outcome.experiment_id != manifest.experiment_id:
            raise ConfigError(f"{experiment_dir} の結末記録は別の実験のもの")
        search = None if outcome is None else outcome.search
        recorded = None if search is None else search.ledger_execution
        if recorded is not None and recorded != execution:
            raise ConfigError(
                f"`--execution` {execution} は、この版のディレクトリの結末記録の実行番号"
                f" {recorded} と違う（前の実行の成果物は退避されている。D09 §11.3）"
            )
        standard = manifest.evaluation_standard
        registry = load_research_policy_registry(research_policy_registry_path(self._root))
        policy = manifest.research_policy_ref
        current = current_standard_version(registry, policy.policy_id)
        snapshots = manifest.snapshot_id
        allowed = manifest.allowed_partitions
        if not isinstance(snapshots, Paired) or not isinstance(allowed, Paired):
            raise ConfigError(f"{experiment_dir} の記録票は組ごとの snapshot を持たない")
        symbols = frozenset(
            key.split("/")[0].split("_")[0] for _, mapping in allowed.items() for key in mapping
        )

        def requirement(variant: Variant) -> FrequencyClass | None:
            if search is None:
                return None
            frequency = search.variants.of(variant).frequency
            return (
                None if frequency is None else frequency_class_of(frequency, standard.sufficiency)
            )

        def final_trial(variant: Variant) -> int | None:
            if search is None:
                return None
            selections = search.variants.of(variant).selections
            return None if not selections else selections[-1].selected_trial_index

        return CandidateFacts(
            key=CandidateKey(
                experiment_id=manifest.experiment_id.hex,
                execution=execution,
                experiment_name=manifest.experiment_name,
                experiment_version=manifest.experiment_version,
            ),
            completed=outcome is not None and outcome.status is ExperimentStatus.COMPLETED,
            verdicts=None
            if search is None
            else Paired.build(lambda variant: search.variants.of(variant).verdict),
            combined=None if search is None else search.combined.verdict,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            current_policy_version=current,
            purpose=standard.purpose,
            final_holdout=manifest.final_holdout,
            symbols=symbols,
            started_line_digest=None if binding is None else binding.started_line_digest.hex,
            standard=standard,
            requirements=Paired.build(requirement),
            final_trials=Paired.build(final_trial),
            metric_set_version=manifest.metric_set_version,
            record_snapshots=Paired.build(lambda variant: str(snapshots.of(variant))),
            manifest_sha256=_sha256(directory / "experiment_manifest.json"),
            outcome_sha256=_sha256(directory / "experiment_outcome.json"),
        )


# --- 最終検証の run（段 2 (f)(g)・段 5）------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FinalRunPreparation:
    """段 2 (f) の再計算の結果と、ウォームアップの長さ（窓の採点の始まりに使う。仮置き R2-10）。"""

    final_trials: Paired[int]
    warmup: timedelta


@dataclass(frozen=True, slots=True)
class FinalRunPlan:
    """段 2 (g): 組ごとの予測と、run が読む許可集合（研究履歴の partition と並びの partition）。"""

    predicted: Paired[PredictedRun]
    research_partitions: Paired[tuple[tuple[str, PartitionId], ...]]
    allowed: Paired[frozenset[tuple[str, PartitionId]]]
    run_directories: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalRunOutput:
    """段 5 の組の run と評価の結果（識別子と要約。指標の値は記録にだけ書く）。"""

    run_id: str
    run_evaluation_id: str
    result_digest: str
    config_digest: str
    metric_set_version: int
    summary: FinalRunSummary


class FinalRunner(Protocol):
    """最終検証の run の口（段 2 (f)(g) と段 5）。失敗は例外（段 5 なら消費の後の失敗）。"""

    def prepare(
        self, facts: CandidateFacts, experiment_dir: Path
    ) -> FinalRunPreparation | WindowRefusal: ...

    def plan(
        self,
        facts: CandidateFacts,
        window: FinalWindow,
        preparation: FinalRunPreparation,
    ) -> FinalRunPlan | WindowRefusal: ...

    def run(
        self,
        facts: CandidateFacts,
        plan: FinalRunPlan,
        permit: FinalEvaluationPermit,
        variant: Variant,
    ) -> FinalRunOutput: ...


class UnavailableFinalRunner:
    """本番の結線: 最終検証の run の実装は R4 の要決定 A の決定の後（何も書かずに止める）。"""

    _DETAIL: Final = (
        "the final-validation run (recomputing the selection and the verdict from the research"
        " artifacts, and one continuous run per variant over several snapshots) is wired after"
        " the human decides how its run configuration refers to several snapshots (R4 の要決定 A)."
        " Nothing was written and nothing was consumed"
    )

    def prepare(
        self, facts: CandidateFacts, experiment_dir: Path
    ) -> FinalRunPreparation | WindowRefusal:
        return WindowRefusal(FinalReason.FINAL_RUN_UNAVAILABLE, self._DETAIL)

    def plan(
        self, facts: CandidateFacts, window: FinalWindow, preparation: FinalRunPreparation
    ) -> FinalRunPlan | WindowRefusal:
        return WindowRefusal(FinalReason.FINAL_RUN_UNAVAILABLE, self._DETAIL)

    def run(
        self,
        facts: CandidateFacts,
        plan: FinalRunPlan,
        permit: FinalEvaluationPermit,
        variant: Variant,
    ) -> FinalRunOutput:
        raise RuntimeError(self._DETAIL)


# --- origin の既定ブランチ上の記録 -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _OriginSnapshot:
    snapshot_id: str
    manifest: SnapshotManifest
    log: ParsedAccessLog | AccessLogReadFailure


def _origin_snapshots(log: GitHoldoutAccessLog) -> dict[str, _OriginSnapshot]:
    """origin の既定ブランチ上の確定済みの snapshot の manifest と消費の記録（価格を読まない）。"""
    prefix = log.snapshots_relative()
    found: dict[str, _OriginSnapshot] = {}
    for rel in log.list_at_origin(prefix):
        parts = rel[len(prefix) :].strip("/").split("/")
        if len(parts) != 2 or parts[1] != MANIFEST_FILE or parts[0].startswith("_"):
            continue
        snapshot_id = parts[0]
        data = log.read_at_origin(rel)
        try:
            manifest = manifest_from_bytes(data or b"", f"{rel} on origin")
        except (KernelValueError, ValueError, KeyError, TypeError) as exc:
            raise _reason(
                FinalReason.CONSUMPTION_LOG_BROKEN,
                f"{rel} on origin cannot be read ({type(exc).__name__}); the states cannot be"
                " derived",
            ) from exc
        if str(manifest.snapshot_id()) != snapshot_id:
            raise _reason(
                FinalReason.CONSUMPTION_LOG_BROKEN, f"{rel} on origin is not {snapshot_id}"
            )
        raw = log.read_at_origin(f"{prefix}/{snapshot_id}/{ACCESS_LOG_FILE}") or b""
        found[snapshot_id] = _OriginSnapshot(
            snapshot_id=snapshot_id,
            manifest=manifest,
            log=parse_consumption_bytes(raw, manifest),
        )
    return found


def _sealed_facts(snapshots: Mapping[str, _OriginSnapshot]) -> list[SealedPartitionFact]:
    """承認済みの OANDA MT5 の経路の snapshot の封印の partition と状態（D09 §9.10.3 の 1）。"""
    out: list[SealedPartitionFact] = []
    for snapshot_id in sorted(snapshots):
        item = snapshots[snapshot_id]
        manifest = item.manifest
        mt5 = manifest.conversion.mt5
        if mt5 is None or not manifest.sealed_initial_states or manifest.approval is None:
            continue
        if isinstance(item.log, AccessLogReadFailure):
            raise _reason(
                FinalReason.CONSUMPTION_LOG_BROKEN,
                f"the consumption log of {snapshot_id} on origin is broken (line"
                f" {item.log.line_number}: {item.log.detail}. D09 §9.10.4)",
            )
        consumed = item.log.consumed()
        variant = Variant(mt5.time_rule_table.variant.value)
        for initial in manifest.sealed_initial_states:
            record = manifest.partition_record(initial.partition_id)
            if record is None or initial.partition_id.access_class not in OANDA_SEALED_CLASSES:
                continue
            state = (
                HoldoutState.CONSUMED
                if initial.state is HoldoutState.CONSUMED or str(initial.partition_id) in consumed
                else HoldoutState.SEALED
            )
            out.append(
                SealedPartitionFact(
                    snapshot_id=snapshot_id,
                    partition_id=initial.partition_id,
                    variant=variant,
                    digest=record.digest.hex,
                    approved_at=manifest.approval.approved_at,
                    state=state,
                )
            )
    return out


def _candidate_rows(
    snapshots: Mapping[str, _OriginSnapshot], key: CandidateKey
) -> list[tuple[str, ConsumptionRow]]:
    """この候補の最終検証の消費の行（origin の既定ブランチ上）。"""
    rows: list[tuple[str, ConsumptionRow]] = []
    for snapshot_id in sorted(snapshots):
        parsed = snapshots[snapshot_id].log
        if isinstance(parsed, AccessLogReadFailure):
            continue
        rows.extend(
            (snapshot_id, row)
            for row in parsed.rows
            if row.reason is ConsumptionReason.FINAL_EVALUATION and key.matches(row.candidate)
        )
    return rows


def _review_rows(log: GitHoldoutAccessLog) -> list[dict[str, Any]]:
    """origin の既定ブランチ上の採用審査の台帳の行（D16 v0.2 §10.2 の形の最小の読み方）。

    読む項目は `experiment_id`・`execution`・`result`・`fixed_trials`（仮置き R2-7）。R3 が台帳の
    読込（鎖・正規化の検査）を実装したら置き換える。ファイルが無ければ行は無い。
    """
    data = log.read_at_origin(ADOPTION_REVIEWS_PATH.as_posix())
    if not data:
        return []
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(data.split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ConfigError(f"{ADOPTION_REVIEWS_PATH} の {number} 行目が JSON でない") from exc
        if not isinstance(payload, dict):
            raise ConfigError(f"{ADOPTION_REVIEWS_PATH} の {number} 行目が対象でない")
        rows.append(payload)
    return rows


def _review_key(row: Mapping[str, Any]) -> tuple[str, int] | None:
    try:
        return str(row["experiment_id"]), int(row["execution"])
    except (KeyError, TypeError, ValueError):
        return None


def _review_fixed_trials(row: Mapping[str, Any]) -> Paired[int] | None:
    """審査の記録の組ごとの固定した試行（`fixed_trials.<組>.trial_index`。D16 §10.2）。"""
    fixed = row.get("fixed_trials")
    if not isinstance(fixed, dict):
        return None
    try:
        return Paired.build(lambda variant: int(fixed[variant.value]["trial_index"]))
    except (KeyError, TypeError, ValueError):
        return None


# --- 表示 --------------------------------------------------------------------------------------


def _covers(outer: Interval, inner: Interval) -> bool:
    """`outer` が `inner` を覆うか（半開区間）。"""
    return outer.start <= inner.start and inner.end <= outer.end


def _digest_of(items: Sequence[str]) -> str:
    return hashlib.sha256(encode(sorted(items))).hexdigest()


def _target_names(targets: Sequence[tuple[str, PartitionId]]) -> list[str]:
    return [f"{snapshot}:{partition}" for snapshot, partition in targets]


def _count_lines(window: FinalWindow) -> list[str]:
    counts: dict[str, int] = {}
    for _, partition_id in window.targets:
        name = partition_id.access_class.value
        counts[name] = counts.get(name, 0) + 1
    by_class = "、".join(f"{name} {count}" for name, count in sorted(counts.items()))
    return [
        f"消費する範囲: [{window.consumption.start}, {window.consumption.end})"
        f"（取引週 {window.weeks}）",
        f"採点の始まり: {window.scoring_start}",
        f"消費の対象の partition: {len(window.targets)}（{by_class}）",
        f"snapshot: {len(window.snapshot_ids)}",
        "読む partition（組ごと）: "
        + "、".join(f"{variant.value} {len(window.reads.of(variant))}" for variant in VARIANTS),
    ]


def _now() -> UtcTime:
    return UtcTime(datetime.now(UTC))


def _new_id() -> str:
    return secrets.token_hex(16)


# --- 台帳の行 ---------------------------------------------------------------------------------


def _ledger_values(
    *,
    result: FinalLedgerResult,
    candidate: CandidateKey,
    recorded_at: UtcTime,
    actor: str,
    reason: str = "",
    permit_id: str = "",
    t_fix: str = "",
    consumption: Interval | None = None,
    targets: Sequence[str] = (),
    commit: str = "",
    combined: str = "",
    prior: int = 0,
    record_sha256: str = "",
    declaration_sha256: str = "",
) -> dict[str, Any]:
    return {
        "result": result.value,
        "candidate": candidate_payload(candidate),
        "permit_id": permit_id,
        "t_fix": t_fix,
        "consumption_start": "" if consumption is None else str(consumption.start),
        "consumption_end": "" if consumption is None else str(consumption.end),
        "partition_count": len(targets),
        "partitions_digest": _digest_of(targets) if targets else "",
        "consumption_commit": commit,
        "combined_verdict": combined,
        "resume_count": 0,
        "prior_final_validations": prior,
        "reason": reason,
        "final_evaluation_sha256": record_sha256,
        "declaration_sha256": declaration_sha256,
        "recorded_at": str(recorded_at),
        "actor": actor,
    }


def _prior_validations(ledgers: Sequence[FinalLedger], key: CandidateKey) -> list[dict[str, str]]:
    """同じ `experiment_id` の前の最終検証（別の実行。表示だけ。仮置き R2-28）。"""
    seen: dict[str, dict[str, str]] = {}
    for ledger in ledgers:
        for row in ledger.of_experiment(key.experiment_id):
            if row.execution == key.execution or row.result not in (
                FinalLedgerResult.COMPLETED,
                FinalLedgerResult.FAILED_AFTER_CONSUMPTION,
            ):
                continue
            seen[row.permit_id] = {
                "execution": str(row.execution),
                "permit_id": row.permit_id,
                "result": row.result.value,
                "combined_verdict": str(row.payload["combined_verdict"]),
            }
    return [seen[name] for name in sorted(seen)]


# --- 手順 ------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Context:
    request: FinalValidationRequest
    log: GitHoldoutAccessLog
    facts: CandidateFacts
    worktree: FinalLedger
    origin: FinalLedger
    snapshots: dict[str, _OriginSnapshot]
    final_dir: Path
    now: Callable[[], UtcTime]
    new_id: Callable[[], str]
    lines: list[str]
    #: 控えと最終検証の記録に写す値（審査・固定の記録のダイジェストと台帳の累計。D09 §9.10.5）。
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> CandidateKey:
        return self.facts.key

    def rows_of(self, key: CandidateKey) -> list[FinalLedgerResult]:
        rows = [*self.worktree.of(key), *self.origin.of(key)]
        return [row.result for row in rows]

    def declared_used(self, experiment_id: str) -> bool:
        return any(
            row.result is FinalLedgerResult.DECLARED_USED
            for ledger in (self.worktree, self.origin)
            for row in ledger.of_experiment(experiment_id)
        )

    def append(self, values: Mapping[str, Any]) -> None:
        try:
            append_ledger_row(self.request.repo_root, values)
        except FinalLedgerError as exc:
            raise _Stop(EXIT_FAILED, None, f"the final-validation ledger row: {exc}") from exc


def final_validate(
    request: FinalValidationRequest,
    *,
    candidates: CandidateSource | None = None,
    runner: FinalRunner | None = None,
    log: GitHoldoutAccessLog | None = None,
    now: Callable[[], UtcTime] = _now,
    new_id: Callable[[], str] = _new_id,
) -> FinalValidationOutcome:
    """最終検証の 5 段を行う（D09 §9.10.2）。結果の終了コードと表示の行を返す。"""
    lines: list[str] = []
    try:
        return _final_validate(
            request,
            candidates=candidates or ExperimentCandidates(request.repo_root),
            runner=runner or UnavailableFinalRunner(),
            log=log or GitHoldoutAccessLog(request.repo_root, request.snapshots_root),
            now=now,
            new_id=new_id,
            lines=lines,
        )
    except _Stop as stop:
        return FinalValidationOutcome(
            exit_code=stop.exit_code, reason=stop.reason, detail=stop.detail, lines=lines
        )
    except HoldoutAccessLogStop as stop:
        return FinalValidationOutcome(
            exit_code=EXIT_GATE_REFUSED,
            reason=FinalReason.ORIGIN_NOT_CONFIRMED,
            detail=f"{stop.stage}: {stop.detail}",
            lines=lines,
        )
    except (ConfigError, KernelValueError) as exc:
        # 設定・記録の読込の誤り（承認済みの表の一覧・窓の設定など）。消費の前にだけ起きる。
        return FinalValidationOutcome(
            exit_code=EXIT_READ_ERROR, reason=None, detail=str(exc), lines=lines
        )


def _final_validate(
    request: FinalValidationRequest,
    *,
    candidates: CandidateSource,
    runner: FinalRunner,
    log: GitHoldoutAccessLog,
    now: Callable[[], UtcTime],
    new_id: Callable[[], str],
    lines: list[str],
) -> FinalValidationOutcome:
    # --- 段 1: 申請の確かめ ---------------------------------------------------------------
    try:
        branch, remote = log.fetch_origin()
    except HoldoutAccessLogStop as stop:
        raise _reason(FinalReason.ORIGIN_NOT_CONFIRMED, stop.detail) from stop
    lines.append(f"origin の既定ブランチ: {branch}（{remote[:12]}）")
    try:
        facts = candidates.read(request.experiment_dir, request.execution)
        worktree = read_final_ledger(request.repo_root)
        origin_data = log.read_at_origin(FINAL_LEDGER_PATH.as_posix())
        if origin_data is None:
            raise FinalLedgerError(f"{FINAL_LEDGER_PATH} is not on origin (D09 §9.10.5)")
        origin = parse_final_ledger(origin_data)
    except (ConfigError, FinalLedgerError) as exc:
        raise _Stop(EXIT_READ_ERROR, None, str(exc)) from exc
    lines.append(
        f"候補: {facts.key.experiment_name} v{facts.key.experiment_version}"
        f"（experiment_id {facts.key.experiment_id}、実行番号 {facts.key.execution}）"
    )
    ctx = _Context(
        request=request,
        log=log,
        facts=facts,
        worktree=worktree,
        origin=origin,
        snapshots=_origin_snapshots(log),
        final_dir=final_directory(request.experiment_dir, facts.key.execution),
        now=now,
        new_id=new_id,
        lines=lines,
    )
    state = _state(ctx)
    lines.append(f"申請の状態: {state.value}")
    outcome = _by_state(ctx, state)
    if outcome is not None:
        outcome.state = state
        return outcome
    fixation, declaration_sha = _eligibility(ctx)
    # --- 段 2: 許可の前の確かめ ------------------------------------------------------------
    permit, plan = _before_permit(ctx, runner, fixation)
    lines.extend(_count_lines(permit.window))
    if request.mode is FinalMode.DRY_RUN:
        lines.append("乾式実行: 段 1・段 2 が通った。何も書いていない（ロックも作っていない）")
        return FinalValidationOutcome(EXIT_OK, None, "dry run passed", lines, state=state)
    # --- 段 3: 許可の発行（ロック）----------------------------------------------------------
    lock = ctx.final_dir / FINAL_LOCK_FILE
    try:
        create_lock(lock)
    except OSError as exc:
        raise _Stop(
            EXIT_FAILED, None, f"the lock {lock.name} cannot be created ({exc}); nothing consumed"
        ) from exc
    lines.append(f"許可の識別子: {permit.permit_id}")
    # --- 段 4: 消費 -------------------------------------------------------------------------
    commit = _consume(ctx, permit, lock)
    lines.append(f"消費の commit: {commit}（origin の {branch} へ push した）")
    prior = _prior_validations((ctx.worktree, ctx.origin), ctx.key)
    try:
        write_json_once(
            ctx.final_dir / PERMIT_FILE,
            _permit_payload(ctx, permit, fixation, commit, prior, declaration_sha),
        )
    except OSError as exc:
        lock.unlink(missing_ok=True)
        raise _reason(
            FinalReason.PERMIT_RECORD_FAILED,
            f"the permit copy cannot be written ({exc}); the partitions are CONSUMED and the"
            " application waits in state C (要決定 R2-8 (b))",
        ) from exc
    # --- 段 5: 公開と評価 --------------------------------------------------------------------
    return _publish_and_evaluate(
        ctx, runner, permit, plan, fixation, commit, prior, declaration_sha, lock
    )


def _state(ctx: _Context) -> ApplicationState:
    broken = sorted(
        name
        for name, item in ctx.snapshots.items()
        if isinstance(item.log, AccessLogReadFailure)
        and item.manifest.conversion.mt5 is not None
        and item.manifest.sealed_initial_states
    )
    if broken:
        raise _reason(
            FinalReason.CONSUMPTION_LOG_BROKEN,
            f"the consumption logs of {len(broken)} sealed snapshot(s) on origin are broken"
            f" (first: {broken[0]}); the state cannot be derived (D09 §9.10.4)",
        )
    rows = _candidate_rows(ctx.snapshots, ctx.key)
    return derive_application_state(
        lock=(ctx.final_dir / FINAL_LOCK_FILE).exists(),
        record=(ctx.final_dir / FINAL_EVALUATION_FILE).exists(),
        results=frozenset(ctx.rows_of(ctx.key)),
        declared_used_for_experiment=ctx.declared_used(ctx.key.experiment_id),
        consumed_on_origin=bool(rows),
    )


def _by_state(ctx: _Context, state: ApplicationState) -> FinalValidationOutcome | None:
    """状態ごとの扱い（D09 §9.10.6 の表の「コマンドの起動」の列と、取り下げ・閉じる・やり直し）。"""
    mode = ctx.request.mode
    if state is ApplicationState.B_IN_PROGRESS:
        raise _Stop(
            EXIT_FAILED,
            None,
            f"the lock {FINAL_LOCK_FILE} exists: another process is running, or a stopped one left"
            " it. A human checks that no process is running and removes it (D09 §9.10.6 の B)",
        )
    if mode is FinalMode.RESUME:
        if state is not ApplicationState.C_CONSUMED_WITHOUT_RECORD:
            raise _reason(
                FinalReason.NOT_AWAITING_RECORD, f"--resume needs state C, got {state.value}"
            )
        raise _reason(
            FinalReason.RESUME_NOT_ALLOWED,
            "the conditions of a resume (仮置き R2-21〜R2-29) are not decided by the human yet;"
            " nothing was read and nothing was written (要決定 R2-8 (b) の入口だけ。R4)",
        )
    if mode is FinalMode.CLOSE_FAILED:
        if state is not ApplicationState.C_CONSUMED_WITHOUT_RECORD:
            raise _reason(
                FinalReason.NOT_AWAITING_RECORD, f"--close-failed needs state C, got {state.value}"
            )
        return _close_failed(ctx)
    if mode is FinalMode.WITHDRAW:
        if state is not ApplicationState.A_NOT_APPLIED:
            raise _reason(
                _STATE_REASON.get(state, FinalReason.NOT_WITHDRAWABLE), "withdraw needs state A"
            )
        ctx.append(
            _ledger_values(
                result=FinalLedgerResult.WITHDRAWN,
                candidate=ctx.key,
                recorded_at=ctx.now(),
                actor=ctx.request.by,
                reason=FinalReason.WITHDRAWN.value,
            )
        )
        ctx.lines.append("取り下げ: 最終検証の台帳に WITHDRAWN の行を書いた（消費していない）")
        return FinalValidationOutcome(EXIT_OK, None, "withdrawn", ctx.lines)
    if state is ApplicationState.D_VALIDATED:
        if mode is not FinalMode.DRY_RUN:
            _backfill_completed(ctx)
        raise _reason(FinalReason.ALREADY_VALIDATED, "the candidate is already validated")
    if state is ApplicationState.C_CONSUMED_WITHOUT_RECORD:
        raise _reason(
            FinalReason.CONSUMED_WITHOUT_RECORD,
            "the consumption rows of this candidate are on origin but no record and no ledger row"
            " exist. A human checks other worktrees and clones; nothing was read",
        )
    reason = _STATE_REASON.get(state)
    if reason is not None:
        raise _reason(reason, f"the application is in state {state.value}")
    return None


_STATE_REASON: Final[Mapping[ApplicationState, FinalReason]] = {
    ApplicationState.D_VALIDATED: FinalReason.ALREADY_VALIDATED,
    ApplicationState.E_FAILED_AFTER_CONSUMPTION: FinalReason.ALREADY_CONSUMED,
    ApplicationState.F_WITHDRAWN: FinalReason.WITHDRAWN,
    ApplicationState.G_DECLARED_USED: FinalReason.OUTSIDE_VIEWING_DECLARED,
    ApplicationState.C_CONSUMED_WITHOUT_RECORD: FinalReason.CONSUMED_WITHOUT_RECORD,
}


def _backfill_completed(ctx: _Context) -> None:
    """最終検証の記録があり台帳の行だけが無いとき、記録から `COMPLETED` の行を書き足す。"""
    if FinalLedgerResult.COMPLETED in ctx.rows_of(ctx.key):
        return
    path = ctx.final_dir / FINAL_EVALUATION_FILE
    if not path.is_file():
        return
    data = path.read_bytes()
    try:
        record = json.loads(data.decode("utf-8"))
        consumption = Interval(
            start=UtcTime.parse(record["consumption"]["start"]),
            end=UtcTime.parse(record["consumption"]["end"]),
        )
        values = _ledger_values(
            result=FinalLedgerResult.COMPLETED,
            candidate=ctx.key,
            recorded_at=ctx.now(),
            actor=ctx.request.by,
            permit_id=str(record["permit_id"]),
            t_fix=str(record["t_fix"]),
            consumption=consumption,
            targets=list(record["targets"]),
            commit=str(record["consumption_commit"]),
            combined=str(record["combined_verdict"]["verdict"]),
            prior=len(record["prior_final_validations"]),
            record_sha256=hashlib.sha256(data).hexdigest(),
            declaration_sha256=str(record["declaration_sha256"]),
        )
    except (KeyError, TypeError, ValueError, KernelValueError) as exc:
        raise _Stop(
            EXIT_FAILED, None, f"{FINAL_EVALUATION_FILE} cannot be read to fill the ledger: {exc}"
        ) from exc
    ctx.append(values)
    ctx.lines.append("最終検証の記録から台帳の COMPLETED の行を書き足した")


def _close_failed(ctx: _Context) -> FinalValidationOutcome:
    """状態 C を、データを読まずに台帳の失敗の行で閉じる（D09 §9.10.6 の C の (iii)）。"""
    rows = _candidate_rows(ctx.snapshots, ctx.key)
    permits = sorted({row.permit_id for _, row in rows})
    if len(permits) != 1:
        raise _Stop(
            EXIT_FAILED, None, f"the consumption rows of this candidate name {len(permits)} permits"
        )
    targets = sorted(f"{snapshot}:{row.partition}" for snapshot, row in rows)
    periods = [
        record.partition_id.period
        for snapshot, row in rows
        for record in ctx.snapshots[snapshot].manifest.partitions
        if str(record.partition_id) == row.partition and record.partition_id.period is not None
    ]
    consumption = (
        Interval(
            start=min((period.start for period in periods), key=lambda value: value.value),
            end=max((period.end for period in periods), key=lambda value: value.value),
        )
        if periods
        else None
    )
    commit = ""
    t_fix = ""
    permit_copy = ctx.final_dir / PERMIT_FILE
    if permit_copy.is_file():
        try:
            copy = json.loads(permit_copy.read_text(encoding="utf-8"))
            commit, t_fix = str(copy["consumption_commit"]), str(copy["t_fix"])
        except (KeyError, ValueError):
            commit, t_fix = "", ""
    ctx.append(
        _ledger_values(
            result=FinalLedgerResult.FAILED_AFTER_CONSUMPTION,
            candidate=ctx.key,
            recorded_at=ctx.now(),
            actor=ctx.request.by,
            reason=FinalReason.CLOSED_AS_FAILED.value,
            permit_id=permits[0],
            t_fix=t_fix,
            consumption=consumption,
            targets=targets,
            commit=commit,
            prior=len(_prior_validations((ctx.worktree, ctx.origin), ctx.key)),
        )
    )
    ctx.lines.append(
        f"状態 C を閉じた: 台帳に FAILED_AFTER_CONSUMPTION の行を書いた（許可 {permits[0]}、"
        f"partition {len(targets)}）"
    )
    return FinalValidationOutcome(
        EXIT_FAILED_AFTER_CONSUMPTION,
        FinalReason.CLOSED_AS_FAILED,
        "closed as failed",
        ctx.lines,
        state=ApplicationState.C_CONSUMED_WITHOUT_RECORD,
    )


def _eligibility(ctx: _Context) -> tuple[Fixation, str]:
    """申請の資格（D09 §9.10.10 の 1〜8）。固定の記録と申告のファイルのダイジェストを返す。"""
    facts = ctx.facts
    key = ctx.key
    # 1. 判定
    met = (SearchVerdict.MEETS_STANDARD, SearchVerdict.MEETS_STANDARD)
    verdicts = (
        None if facts.verdicts is None else (facts.verdicts.standard, facts.verdicts.candidate)
    )
    if not facts.completed or facts.combined is not SearchVerdict.MEETS_STANDARD or verdicts != met:
        raise _reason(
            FinalReason.NOT_MEETS_STANDARD,
            "the execution is not COMPLETED with both variants meeting the standard",
        )
    if (
        facts.purpose is not StandardPurpose.STANDARD
        or facts.current_policy_version != facts.policy_version
    ):
        raise _reason(
            FinalReason.NOT_CURRENT_POLICY_VERSION,
            f"{facts.policy_id} v{facts.policy_version} is not the current version"
            f" ({facts.current_policy_version}) of the registry",
        )
    if facts.final_holdout is None:
        raise _reason(FinalReason.NO_FINAL_HOLDOUT, "the manifest has final_holdout: NONE")
    # 2. 試行台帳（origin の既定ブランチ上）
    data = ctx.log.read_at_origin(TRIAL_LEDGER_PATH.as_posix())
    if data is None:
        raise _reason(
            FinalReason.LEDGER_NOT_ON_ORIGIN,
            f"{TRIAL_LEDGER_PATH} is not on the default branch of origin (D09 §9.10.10 の 2)",
        )
    contents = parse_trial_ledger_bytes(data)
    if isinstance(contents, TrialLedgerReadFailure):
        detail = contents.detail
        raise _Stop(
            EXIT_READ_ERROR, None, f"{TRIAL_LEDGER_PATH} on origin cannot be read ({detail})"
        )
    try:
        experiment_id = ExperimentId(ContentDigest.sha256(key.experiment_id))
    except KernelValueError as exc:
        raise _Stop(EXIT_READ_ERROR, None, f"the experiment id cannot be read: {exc}") from exc
    started = started_line_of(contents.lines, experiment_id, key.execution)
    finished = finished_line_of(contents.lines, experiment_id, key.execution)
    if (
        started is None
        or finished is None
        or facts.started_line_digest is None
        or started.digest.hex != facts.started_line_digest
    ):
        raise _reason(
            FinalReason.LEDGER_NOT_ON_ORIGIN,
            "the STARTED and FINISHED lines of this execution (bound by the ledger binding) are"
            " not both on the trial ledger of origin",
        )
    # 3. 台帳の累計（表示だけ。Q17）
    overlapping = 0
    trials = 0
    for line in contents.lines:
        entry = line.entry
        if entry.event is not TrialLedgerEvent.STARTED or line.digest == started.digest:
            continue
        if any(
            theirs.overlaps(ours)
            for theirs in entry.validation_intervals
            for ours in started.entry.validation_intervals
        ):
            overlapping += 1
            trials += entry.trial_count
    ctx.lines.append(
        f"台帳の累計（同じ検証区間を見た実行・試行。表示だけ）: 実行 {overlapping}、試行 {trials}"
    )
    ctx.extras["ledger_tally"] = {
        "overlapping_executions": overlapping,
        "overlapping_trials": trials,
        "trial_ledger_head": contents.lines[-1].digest.hex if contents.lines else "",
    }
    # 4. 採用審査
    try:
        reviews = _review_rows(ctx.log)
    except ConfigError as exc:
        raise _Stop(EXIT_READ_ERROR, None, str(exc)) from exc
    proceeds = [row for row in reviews if row.get("result") == "PROCEED"]
    mine = next(
        (
            index
            for index, row in enumerate(proceeds)
            if _review_key(row) == (key.experiment_id, key.execution)
        ),
        None,
    )
    if mine is None:
        raise _reason(
            FinalReason.NOT_PROCEEDED_BY_REVIEW,
            f"no PROCEED row of this execution on {ADOPTION_REVIEWS_PATH} of origin",
        )
    # 5. 候補の固定
    rel = fixation_path(key.experiment_id, key.execution)
    text = ctx.log.read_at_origin(rel.as_posix())
    if text is None:
        raise _reason(FinalReason.NOT_FIXED, f"{rel} is not on origin")
    try:
        fixation = load_fixation(rel, text=text.decode("utf-8"))
    except (ConfigError, UnicodeDecodeError) as exc:
        raise _Stop(EXIT_READ_ERROR, None, str(exc)) from exc
    review_trials = _review_fixed_trials(proceeds[mine])
    ctx.extras["review_row_sha256"] = hashlib.sha256(encode(proceeds[mine])).hexdigest()
    ctx.extras["fixation_sha256"] = hashlib.sha256(text).hexdigest()
    if (
        (fixation.experiment_id, fixation.execution) != (key.experiment_id, key.execution)
        or review_trials is None
        or review_trials != fixation.fixed_trials
    ):
        raise _reason(
            FinalReason.NOT_FIXED,
            "the fixation record does not match this execution, or the fixed trials of the review"
            " cannot be read or differ (D09 §9.10.10 の 5)",
        )
    ctx.lines.append(f"候補の固定時点 T_fix: {fixation.t_fix}、長さ L: {fixation.length_days} 日")
    # 6. 申告
    path = ctx.request.declaration
    if path is None:
        raise _Stop(EXIT_READ_ERROR, None, "`--declaration` is required (仮置き R2-6)")
    try:
        declaration = load_declaration(path)
        window = load_sealed_window(ctx.request.repo_root / SEALED_WINDOW_PATH)
    except ConfigError as exc:
        raise _Stop(EXIT_READ_ERROR, None, str(exc)) from exc
    if (declaration.experiment_id, declaration.execution) != (key.experiment_id, key.execution):
        raise _Stop(EXIT_READ_ERROR, None, f"{path} is a declaration of another candidate")
    start = window.acquisition_start
    if start is None:
        raise _Stop(EXIT_READ_ERROR, None, "the acquisition start is not set in the window config")
    required = Interval(start=start, end=fixation.t_fix) if start < fixation.t_fix else None
    if required is not None and not _covers(declaration.period, required):
        raise _reason(
            FinalReason.DECLARATION_NOT_COVERING,
            f"the declaration answers {declaration.period}, not the whole of {required}"
            " (D09 §9.10.10 の 6)",
        )
    declaration_sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if declaration.used_outside_viewing:
        if ctx.request.mode is FinalMode.DRY_RUN:
            ctx.lines.append("申告が「使った」: 本番の起動なら台帳に DECLARED_USED の行を書く")
        else:
            ctx.append(
                _ledger_values(
                    result=FinalLedgerResult.DECLARED_USED,
                    candidate=key,
                    recorded_at=ctx.now(),
                    actor=ctx.request.by,
                    reason=FinalReason.OUTSIDE_VIEWING_DECLARED.value,
                    declaration_sha256=declaration_sha,
                )
            )
            ctx.lines.append("申告が「使った」: 台帳に DECLARED_USED の行を書いた")
        raise _reason(
            FinalReason.OUTSIDE_VIEWING_DECLARED,
            "the declaration answers that market data seen outside the research was used;"
            " the final validation of this candidate is treated as used (ADR-0037 第3項の 3〜5)",
        )
    # 8. 順番（要決定 R2-6 (a)）
    for row in proceeds[:mine]:
        other = _review_key(row)
        if other is None or other == (key.experiment_id, key.execution):
            continue
        earlier = CandidateKey(
            experiment_id=other[0],
            execution=other[1],
            experiment_name="earlier",
            experiment_version=1,
        )
        finished_results = set(ctx.rows_of(earlier))
        if not finished_results and not ctx.declared_used(other[0]):
            raise _reason(
                FinalReason.EARLIER_CANDIDATE_PENDING,
                f"the earlier candidate {other[0]} execution {other[1]} has not finished",
            )
    return fixation, declaration_sha


def _before_permit(
    ctx: _Context, runner: FinalRunner, fixation: Fixation
) -> tuple[FinalEvaluationPermit, FinalRunPlan]:
    """段 2（何も書かない）。許可の値（まだ発行していない）と run の計画を返す。"""
    facts = ctx.facts
    # (f) 再計算とウォームアップ
    prepared = runner.prepare(facts, ctx.request.experiment_dir)
    if isinstance(prepared, WindowRefusal):
        raise _refuse(prepared)
    if prepared.final_trials != fixation.fixed_trials:
        raise _reason(
            FinalReason.NOT_FIXED,
            "the fixed trials differ from the trials recomputed from the research artifacts",
        )
    # (b) 窓と消費の対象
    excluded: list[Interval] = []
    for rel in ctx.log.list_at_origin(FIXATIONS_DIRECTORY.as_posix()):
        if rel == fixation_path(ctx.key.experiment_id, ctx.key.execution).as_posix():
            continue
        text = ctx.log.read_at_origin(rel)
        try:
            other = load_fixation(Path(rel), text=(text or b"").decode("utf-8"))
        except (ConfigError, UnicodeDecodeError) as exc:
            raise _Stop(EXIT_READ_ERROR, None, str(exc)) from exc
        excluded.append(other.forward_period)
    window = derive_final_window(
        _sealed_facts(ctx.snapshots),
        symbols=facts.symbols,
        t_fix=fixation.t_fix,
        excluded=excluded,
        length_days=fixation.length_days,
        warmup=prepared.warmup,
    )
    if isinstance(window, WindowRefusal):
        raise _refuse(window)
    holdout = facts.final_holdout
    assert holdout is not None  # noqa: S101 - 資格の確かめで済んでいる
    if not _covers(holdout.interval, window.consumption):
        raise _reason(
            FinalReason.FINAL_HOLDOUT_NOT_COVERING,
            f"final_holdout {holdout.interval} does not cover the window {window.consumption}"
            " (要決定 R2-5 (a))",
        )
    # (a) origin の確かめ
    prefix = ctx.log.snapshots_relative()
    try:
        ctx.log.require_clean_base(
            [f"{prefix}/{snapshot}/{ACCESS_LOG_FILE}" for snapshot in window.snapshot_ids],
            [f"{prefix}/{snapshot}/{MANIFEST_FILE}" for snapshot in window.snapshot_ids],
        )
    except HoldoutAccessLogStop as stop:
        raise _reason(FinalReason.ORIGIN_NOT_CONFIRMED, stop.detail) from stop
    # (c) 出力先
    if ctx.final_dir.exists() and any(ctx.final_dir.iterdir()):
        raise _reason(FinalReason.OUTPUT_NOT_EMPTY, f"{ctx.final_dir} is not empty")
    # (e) 実行前のデータ能力検査（戦略によらない部分。manifest と検査報告だけ。仮置き R2-11）
    _capability(ctx, window)
    # (g) run の予測
    plan = runner.plan(facts, window, prepared)
    if isinstance(plan, WindowRefusal):
        raise _refuse(plan)
    existing = [path for path in plan.run_directories if path.exists()]
    if existing:
        raise _reason(FinalReason.OUTPUT_NOT_EMPTY, f"{len(existing)} run directory(ies) exist")
    # 段 2 (d) と許可の値（関所。状態は origin の既定ブランチ上の消費の記録から）
    states = {
        (fact.snapshot_id, str(fact.partition_id)): fact.state
        for fact in _sealed_facts(ctx.snapshots)
    }
    permit = issue_final_evaluation_permit(
        permit_id=ctx.new_id(),
        candidate=ctx.key,
        t_fix=fixation.t_fix,
        length_days=fixation.length_days,
        window=window,
        predicted=plan.predicted,
        purpose=holdout.purpose,
        research_partitions=plan.research_partitions,
        states=states,
    )
    if isinstance(permit, WindowRefusal):
        raise _refuse(permit)
    # (h) P2F の (1)・(3)
    refusal = final_partitions_match_permit(permit, plan.allowed, facts.record_snapshots)
    if refusal is not None:
        raise _refuse(refusal)
    return permit, plan


def _capability(ctx: _Context, window: FinalWindow) -> None:
    """段 2 (e): 承認・ERROR なし・時刻の規則の表の承認・取得不足の区間（戦略によらない部分）。"""
    from odyssey_fx.app.config.time_rules import load_approved_tables, require_approved
    from odyssey_fx.marketdata.domain.errors import MarketDataValueError

    store = ParquetSnapshotStore(root=Path(ctx.request.snapshots_root))
    approved = load_approved_tables(ctx.request.repo_root / SEALED_APPROVED_TABLES)
    symbols = {str(partition.series.symbol) for _, partition in window.targets}
    for snapshot_id in window.snapshot_ids:
        try:
            readable = store.open_readable(snapshot_id)
        except Exception as exc:  # noqa: BLE001 - どの失敗も「能力検査の不合格」に倒す
            raise _reason(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"the snapshot {snapshot_id} cannot be opened as approved ({type(exc).__name__})",
            ) from exc
        mt5 = readable.manifest.conversion.mt5
        if mt5 is None:
            raise _reason(FinalReason.DATA_CAPABILITY_NOT_MET, f"{snapshot_id} is not MT5")
        table = mt5.time_rule_table
        try:
            require_approved(
                approved, table_id=table.table_id, version=table.version, sha256=table.sha256
            )
        except MarketDataValueError as exc:
            raise _reason(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"the time-rule table of {snapshot_id} is not approved ({exc})",
            ) from exc
        shortfalls = [
            result
            for result in readable.report.results
            if result.kind is CheckKind.ACQUISITION_SHORTFALL
            and str(result.series.symbol) in symbols
            and result.interval is not None
            and result.interval.overlaps(window.consumption)
        ]
        if shortfalls:
            raise _reason(
                FinalReason.DATA_CAPABILITY_NOT_MET,
                f"{len(shortfalls)} acquisition shortfall(s) in {snapshot_id} overlap the window",
            )


def _consume(ctx: _Context, permit: FinalEvaluationPermit, lock: Path) -> str:
    """段 4: 消費の行の追記 → commit → push（強制しない）。push の成功で commit を返す。"""
    holdout = ctx.facts.final_holdout
    assert holdout is not None  # noqa: S101
    recorded_at = ctx.now()
    prefix = ctx.log.snapshots_relative()
    paths = sorted(
        {f"{prefix}/{snapshot}/{ACCESS_LOG_FILE}" for snapshot, _ in permit.window.targets}
    )
    try:
        for snapshot, partition_id in permit.window.targets:
            ctx.log.append_row(
                snapshot,
                ConsumptionRow(
                    row_id=ctx.new_id(),
                    partition=str(partition_id),
                    reason=ConsumptionReason.FINAL_EVALUATION,
                    permit_id=permit.permit_id,
                    candidate=ctx.key.consumption(),
                    access_id="",
                    recorded_at=recorded_at,
                    actor=ctx.request.by,
                    purpose=holdout.purpose,
                ),
            )
    except HoldoutAccessLogStop as stop:
        ctx.log.restore(paths)
        lock.unlink(missing_ok=True)
        raise _Stop(
            EXIT_FAILED,
            None,
            f"appending the consumption rows failed ({stop.detail}); the appended lines were"
            " restored, nothing reached origin and nothing was consumed",
        ) from stop
    message = (
        f"research: 最終検証の消費の記録（候補 {ctx.key.experiment_name}"
        f" v{ctx.key.experiment_version} の実行 {ctx.key.execution}）\n\n"
        f"許可の識別子: {permit.permit_id}\n"
        f"消費する範囲: [{permit.window.consumption.start}, {permit.window.consumption.end})\n"
        f"消費した partition: {len(permit.window.targets)}（snapshot"
        f" {len(permit.window.snapshot_ids)}）\n\n"
        "odyssey-fx experiment final-validate が書いた（D09 §9.10.2 の段 4）。\n"
    )
    try:
        commit = ctx.log.publish(paths, message, restore_on_reject=True, confirm_unknown=True)
    except PushOutcomeUnknown as unknown:
        raise _Stop(
            EXIT_FAILED,
            None,
            f"{unknown.detail}. The lock stays; nothing is read until a human checks origin"
            " (D09 §9.10.6 の B)",
        ) from unknown
    except HoldoutAccessLogStop as stop:
        if stop.commit is not None:
            raise _Stop(
                EXIT_FAILED,
                None,
                f"{stop.detail}. The lock stays; a human checks origin (D09 §9.10.6 の B)",
            ) from stop
        if stop.stage != "push":
            # commit が作れなかった（push の前）。追記を戻し、ロックを消す（何も消費していない）。
            ctx.log.restore(paths)
            lock.unlink(missing_ok=True)
            raise _Stop(
                EXIT_FAILED,
                None,
                f"committing the consumption rows failed ({stop.stage}: {stop.detail}); the"
                " appended lines were restored and nothing was consumed",
            ) from stop
        lock.unlink(missing_ok=True)
        raise _reason(
            FinalReason.PUSH_REJECTED,
            f"{stop.detail}; nothing was read and nothing was consumed (仮置き R2-2)",
        ) from stop
    if commit is None:  # pragma: no cover - 消費の行を足したので必ず変更がある
        lock.unlink(missing_ok=True)
        raise _Stop(EXIT_FAILED, None, "no change was committed")
    return commit


def _permit_payload(
    ctx: _Context,
    permit: FinalEvaluationPermit,
    fixation: Fixation,
    commit: str,
    prior: Sequence[Mapping[str, str]],
    declaration_sha: str,
) -> dict[str, Any]:
    """控え `permit.json` の中身（D09 §9.10.5 の表。価格・指標の値を含まない）。"""
    window = permit.window
    return {
        "schema_version": 1,
        "permit_id": permit.permit_id,
        "candidate": candidate_payload(permit.candidate),
        "t_fix": str(fixation.t_fix),
        "length_days": fixation.length_days,
        "consumption": {"start": str(window.consumption.start), "end": str(window.consumption.end)},
        "scoring_start": str(window.scoring_start),
        "targets": _target_names(window.targets),
        "reads": {
            variant.value: [
                f"{item.snapshot_id}:{item.partition_id}" for item in window.reads.of(variant)
            ]
            for variant in VARIANTS
        },
        "consumption_commit": commit,
        "predicted": {
            variant.value: {
                "config_digest": permit.predicted.of(variant).config_digest,
                "run_id": permit.predicted.of(variant).run_id,
            }
            for variant in VARIANTS
        },
        "purpose": permit.purpose,
        "manifest_sha256": ctx.facts.manifest_sha256,
        "outcome_sha256": ctx.facts.outcome_sha256,
        "declaration_sha256": declaration_sha,
        "prior_final_validations": list(prior),
        **ctx.extras,
    }


def _publish_and_evaluate(
    ctx: _Context,
    runner: FinalRunner,
    permit: FinalEvaluationPermit,
    plan: FinalRunPlan,
    fixation: Fixation,
    commit: str,
    prior: Sequence[Mapping[str, str]],
    declaration_sha: str,
    lock: Path,
) -> FinalValidationOutcome:
    """段 5: 検査 P2F の (2) → run と評価 → P4F・P5 → 判定 → 記録 → 台帳 → ロックの削除。"""
    facts = ctx.facts
    try:
        ctx.log.fetch_origin()
        snapshots = _origin_snapshots(ctx.log)
        rows: dict[tuple[str, str], set[str]] = {}
        for snapshot_id, item in snapshots.items():
            if isinstance(item.log, AccessLogReadFailure):
                continue
            for row in item.log.rows:
                rows.setdefault((snapshot_id, row.partition), set()).add(row.permit_id)
        refusal = consumption_rows_present(
            permit, {key: frozenset(value) for key, value in rows.items()}
        ) or final_partitions_match_permit(permit, plan.allowed, facts.record_snapshots)
        if refusal is not None:
            raise _refuse(refusal)
        outputs = Paired.build(lambda variant: runner.run(facts, plan, permit, variant))
        for variant, output in outputs.items():
            predicted = permit.predicted.of(variant)
            if (output.config_digest, output.run_id) != (predicted.config_digest, predicted.run_id):
                raise _reason(
                    FinalReason.P4F_NOT_PASSED,
                    f"the {variant.value} run {output.run_id} differs from the prediction",
                )
            if output.metric_set_version != facts.metric_set_version:
                raise _reason(
                    FinalReason.P5_NOT_PASSED,
                    f"the {variant.value} evaluation uses metric set v{output.metric_set_version}",
                )
        judged = Paired.build(
            lambda variant: judge_final_variant(
                facts.standard, variant, outputs.of(variant).summary, facts.requirements.of(variant)
            )
        )
        combined = combine_final(judged.standard, judged.candidate)
        record = _record_payload(
            permit,
            fixation,
            commit,
            prior,
            declaration_sha,
            outputs,
            judged,
            combined,
            facts,
            ctx.extras,
        )
        record_sha = write_json_once(ctx.final_dir / FINAL_EVALUATION_FILE, record)
        (ctx.final_dir / REPORT_FILE).write_text(_report_text(record), encoding="utf-8")
    except _Stop as stop:
        lock.unlink(missing_ok=True)
        if stop.exit_code == EXIT_FAILED_AFTER_CONSUMPTION:
            raise
        raise _reason(FinalReason.FINAL_RUN_FAILED, stop.detail) from stop
    except Exception as exc:  # noqa: BLE001 - 段 5 のどの失敗も消費の後の失敗（状態 C）
        lock.unlink(missing_ok=True)
        raise _reason(
            FinalReason.FINAL_RUN_FAILED,
            f"the final-validation run or its record failed ({type(exc).__name__}); the"
            " partitions are CONSUMED and the application waits in state C (要決定 R2-8 (b))",
        ) from exc
    try:
        append_ledger_row(
            ctx.request.repo_root,
            _ledger_values(
                result=FinalLedgerResult.COMPLETED,
                candidate=ctx.key,
                recorded_at=ctx.now(),
                actor=ctx.request.by,
                permit_id=permit.permit_id,
                t_fix=str(fixation.t_fix),
                consumption=permit.window.consumption,
                targets=_target_names(permit.window.targets),
                commit=commit,
                combined=combined.verdict.value,
                prior=len(prior),
                record_sha256=record_sha,
                declaration_sha256=declaration_sha,
            ),
        )
    except FinalLedgerError as exc:
        lock.unlink(missing_ok=True)
        raise _Stop(
            EXIT_FAILED,
            None,
            f"the record was written but the ledger row was not ({exc}); the next start fills it"
            " (D09 §9.10.6 の B 行)",
        ) from exc
    lock.unlink(missing_ok=True)
    ctx.lines.append(f"最終検証の記録: {ctx.final_dir / FINAL_EVALUATION_FILE}")
    for variant, judged_item in judged.items():
        ctx.lines.append(f"  {variant.value} の組の判定: {judged_item.verdict.value}")
    ctx.lines.append(f"組の判定: {combined.verdict.value}（「満たす」とは限らない）")
    return FinalValidationOutcome(
        EXIT_OK,
        None,
        "validated",
        ctx.lines,
        state=ApplicationState.D_VALIDATED,
        permit_id=permit.permit_id,
        commit=commit,
        verdict=combined,
    )


def _record_payload(
    permit: FinalEvaluationPermit,
    fixation: Fixation,
    commit: str,
    prior: Sequence[Mapping[str, str]],
    declaration_sha: str,
    outputs: Paired[FinalRunOutput],
    judged: Paired[FinalVariantOutcome],
    combined: CombinedVerdict,
    facts: CandidateFacts,
    extras: Mapping[str, Any],
) -> dict[str, Any]:
    """最終検証の記録 `final_evaluation.json`（D09 §9.10.5。指標の値は評価の成果物を参照する）。"""

    def variant_block(variant: Variant) -> dict[str, Any]:
        output = outputs.of(variant)
        item = judged.of(variant)
        return {
            "run_id": output.run_id,
            "run_evaluation_id": output.run_evaluation_id,
            "result_digest": output.result_digest,
            "verdict": item.verdict.value,
            "profitability": item.profitability.value,
            "evidence": item.evidence.value,
            "reasons": list(item.reasons),
            "hold": item.hold,
        }

    window = permit.window
    return {
        "schema_version": 1,
        "permit_id": permit.permit_id,
        "candidate": candidate_payload(permit.candidate),
        "t_fix": str(fixation.t_fix),
        "length_days": fixation.length_days,
        "consumption": {"start": str(window.consumption.start), "end": str(window.consumption.end)},
        "scoring_start": str(window.scoring_start),
        "targets": _target_names(window.targets),
        "consumption_commit": commit,
        "purpose": permit.purpose,
        "declaration_sha256": declaration_sha,
        "manifest_sha256": facts.manifest_sha256,
        "outcome_sha256": facts.outcome_sha256,
        "prior_final_validations": list(prior),
        "variants": {variant.value: variant_block(variant) for variant in VARIANTS},
        "combined_verdict": {
            "verdict": combined.verdict.value,
            "split": combined.split,
            "hold": combined.hold,
        },
        "checks": {"P2F": "PASSED", "P4F": "PASSED", "P5": "PASSED"},
        **extras,
    }


def _report_text(record: Mapping[str, Any]) -> str:
    """人が読む `report.md`（判定の値と識別子。指標の値は評価の成果物を参照する）。"""
    lines = [
        "# 最終検証の記録",
        "",
        f"- 許可の識別子: {record['permit_id']}",
        f"- 候補: {record['candidate']['experiment_name']}"
        f" v{record['candidate']['experiment_version']}"
        f"（実行番号 {record['candidate']['execution']}）",
        f"- 固定時点 T_fix: {record['t_fix']}、長さ L: {record['length_days']} 日",
        f"- 消費する範囲: [{record['consumption']['start']}, {record['consumption']['end']})",
        f"- 採点の始まり: {record['scoring_start']}",
        f"- 消費した partition: {len(record['targets'])}、"
        f"消費の commit: {record['consumption_commit']}",
        f"- 同じ実験の前の最終検証: {len(record['prior_final_validations'])} 回（表示だけ）",
        "",
        "## 判定",
        "",
    ]
    for variant, block in record["variants"].items():
        lines.append(
            f"- {variant}: {block['verdict']}（収益性・リスクの軸 {block['profitability']}、"
            f"証拠の軸 {block['evidence']}、run {block['run_id']}）"
        )
    lines.append(f"- 組の判定: {record['combined_verdict']['verdict']}")
    lines.append("")
    lines.append("最終検証の結果も、実運用で利益が出ることの証明ではない（D09 §9.9 の規則 4）。")
    return "\n".join(lines) + "\n"
