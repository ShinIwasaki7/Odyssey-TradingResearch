"""実験の記録票（実験 manifest）と実験の結末記録（D07 §19、2026-09-25 の人間の決定4）。

1つの実験について、**実行の前に書いて以後書き換えない記録票**（`ExperimentManifest`）と、
**実行の後に書く結末記録**（`ExperimentOutcome`）の2つを残す（D07 §19.1）。

- 記録票は**事前固定の証拠**である。run より前に保存し、同じ版に別の内容を書くことを拒否する
  （研究ポリシーの検査 P3）。
- 結末記録は、記録票の識別子・`run_id`・評価の識別子・結果ダイジェスト・研究ポリシーの事後
  検査の結果を持ち、**記録票から結果へ辿る唯一の経路**である。

**記録票の識別子（`experiment_id`）の入力**は D07 §19.2 の「識別の入力」の4項目だけである
（`experiment_id_of`）。**パスと環境の群と予測した `RunId` は入れない**。パスを入れると同じ
内容を別の場所に置いただけで別の実験になり、環境の群を入れるとコードを1行直しただけで同じ
版が「別の内容」になって検査 P3 が事前固定の違反と誤判定する。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Final

from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId, RunId, SnapshotId
from odyssey_fx.common.refs import (
    CodeDigest,
    CompiledStrategyRef,
    ConfigDigest,
    ContentDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    StrategyRef,
)
from odyssey_fx.evaluation.domain.research_policy import (
    ComplexityLimits,
    ComplexityMeasures,
    PolicyCheck,
    PolicyCheckResult,
)
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.marketdata.domain.access import AccessClass

__all__ = [
    "EXPERIMENT_SCHEMA_VERSION",
    "ExperimentManifest",
    "ExperimentOutcome",
    "ExperimentStatus",
    "ResolvedFile",
    "experiment_id_of",
    "file_sha256",
    "identity_roles",
    "require_experiment_name",
]

#: 記録票の書式の版（実験設定の書式 v2 の解決済み内容。D07 §19.2 の `schema_version`）。
EXPERIMENT_SCHEMA_VERSION: Final = 2

#: 役割名（D07 §19.2 の `resolved_files`）。銘柄仕様は `symbol:<銘柄>`。
ROLE_EXPERIMENT: Final = "experiment"
ROLE_STRATEGY: Final = "strategy"
ROLE_RESEARCH_POLICY: Final = "research_policy"
ROLE_CALENDAR: Final = "calendar"
ROLE_TIMEFRAMES: Final = "timeframes"
ROLE_SYMBOL_PREFIX: Final = "symbol:"

_FIXED_ROLES: Final = frozenset(
    {ROLE_EXPERIMENT, ROLE_STRATEGY, ROLE_RESEARCH_POLICY, ROLE_CALENDAR, ROLE_TIMEFRAMES}
)

#: 実験の名前の字種。名前は保存先のディレクトリ名になる（D07 §19.1）ので、区切り文字・
#: `..`・空白を含む名前は受け付けない（根の外や別の実験のディレクトリを指せないようにする）。
_EXPERIMENT_NAME: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")

#: 16進 64 文字（SHA-256）。照合は `fullmatch`（`$` は末尾改行を許すため）。
_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")


def require_experiment_name(name: object) -> str:
    """実験の名前が保存先のディレクトリ名として安全であることを確かめる。"""
    if not isinstance(name, str) or not _EXPERIMENT_NAME.fullmatch(name):
        raise KernelValueError(
            "the experiment name becomes a directory name under runs/experiments/ and must"
            f" match {_EXPERIMENT_NAME.pattern!r} (D07 §19.1), got {name!r}"
        )
    return name


def file_sha256(text: str) -> str:
    """本文（UTF-8）の SHA-256（16進 64 文字）。記録票の `resolved_files` の `sha256`。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_role(role: object) -> str:
    if not isinstance(role, str) or not role:
        raise KernelValueError("ResolvedFile.role must be a non-empty str")
    if role in _FIXED_ROLES:
        return role
    if role.startswith(ROLE_SYMBOL_PREFIX) and len(role) > len(ROLE_SYMBOL_PREFIX):
        return role
    raise KernelValueError(
        f"unknown role {role!r}; roles are {sorted(_FIXED_ROLES)} or"
        f" {ROLE_SYMBOL_PREFIX}<symbol> (D07 §19.2)"
    )


class ExperimentStatus(Enum):
    """1つの実験の終端3値（D07 §19.4）。"""

    #: 事前・事後の検査が合格し、run と評価が行われた。run や評価そのものの失敗は、それぞれの
    #: 状態（`RunStatus` / `EvaluationStatus`）が表す。
    COMPLETED = "COMPLETED"
    #: 事前検査で合格でないものがあった。run しない。
    REJECTED_BY_POLICY = "REJECTED_BY_POLICY"
    #: 事後検査で合格でないものがあった。成果物は残すが採用しない。
    FAILED_POST_RUN_CHECK = "FAILED_POST_RUN_CHECK"


@dataclass(frozen=True, slots=True)
class ResolvedFile:
    """解決済みの設定ファイル1件の本文と SHA-256（D07 §19.2 の `resolved_files`）。"""

    role: str
    text: str
    sha256: str

    def __post_init__(self) -> None:
        _require_role(self.role)
        if not isinstance(self.text, str):
            raise KernelValueError("ResolvedFile.text must be a str")
        if not isinstance(self.sha256, str) or not _SHA256_HEX.fullmatch(self.sha256):
            raise KernelValueError("ResolvedFile.sha256 must be 64 lowercase hex characters")

    @classmethod
    def of(cls, role: str, text: str) -> ResolvedFile:
        """本文から作る（SHA-256 はここで計算する）。"""
        return cls(role=role, text=text, sha256=file_sha256(text))

    @property
    def intact(self) -> bool:
        """本文の SHA-256 が記録された値と一致するか（本文だけの改変の検出に使う）。"""
        return file_sha256(self.text) == self.sha256


def identity_roles(files: Sequence[ResolvedFile]) -> tuple[tuple[str, str], ...]:
    """識別の入力 3（D07 §19.2）: 参照先ファイルの `(role, sha256)` を役割名の昇順に並べた列。

    役割 `experiment` の要素は入れない（実験設定の値が入力 2 として代わる）。
    """
    return tuple(sorted((item.role, item.sha256) for item in files if item.role != ROLE_EXPERIMENT))


#: 記録票が持つ事前検査（D07 §19.2 の `pre_run_checks`。P1・P2・P6 の全件）。
PRE_RUN_CHECKS: Final = frozenset(
    {
        PolicyCheck.HYPOTHESIS_PRESENT,
        PolicyCheck.RESEARCH_HISTORY_ONLY,
        PolicyCheck.COMPLEXITY_WITHIN_LIMITS,
    }
)
#: 結末記録が持つ検査（D07 §19.3 の `outcome_checks`）。事前検査で止まった場合は P3 だけ、
#: それ以外は P3・P4・P5 の全件。
_REJECTED_OUTCOME_CHECKS: Final = frozenset({PolicyCheck.PREREGISTRATION_UNCHANGED})
_RUN_OUTCOME_CHECKS: Final = frozenset(
    {
        PolicyCheck.PREREGISTRATION_UNCHANGED,
        PolicyCheck.RUN_MATCHES_PREREGISTRATION,
        PolicyCheck.EVALUATION_RULE_MATCHES,
    }
)


def _require_checks(
    checks: object, required: frozenset[PolicyCheck], label: str
) -> tuple[PolicyCheckResult, ...]:
    """検査結果が、決められた検査を**ちょうど1件ずつ、全件**持つことを確かめる。

    欠けを許すと、空の事前検査が「全件合格」と読まれて run が始まる（D07 §19.2・§20.3）。
    検査ごとの時点は `PolicyCheckResult` が構築時に固定している。
    """
    if not isinstance(checks, tuple) or not all(
        isinstance(item, PolicyCheckResult) for item in checks
    ):
        raise KernelValueError(f"{label} must be a tuple of PolicyCheckResult")
    names = [item.check for item in checks]
    if len(set(names)) != len(names) or set(names) != required:
        raise KernelValueError(
            f"{label} must hold exactly one result for each of"
            f" {sorted(check.value for check in required)}, got"
            f" {[check.value for check in names]} (D07 §19.2・§19.3・§20.3)"
        )
    return checks


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    """実験の記録票（D07 §19.2 の表の項目すべて）。

    `allowed_partitions` は許可 partition（`PartitionId` の文字列）からアクセス分類への
    対応。`complexity` は計測値、`complexity_limits` は研究ポリシーの上限である。
    `pre_run_checks` は事前の段階（`PRE_RUN`）の検査 P1・P2・P6 の全件。保存時の検査 P3 は
    入れない（識別子と比べる検査なので、入れると循環する。D07 §19.2）。
    """

    experiment_id: ExperimentId
    experiment_name: str
    experiment_version: int
    schema_version: int
    hypothesis: str
    research_policy_ref: PolicyRef
    metric_set_version: int
    search_plan: str
    split: str
    resolved_files: tuple[ResolvedFile, ...]
    strategy_ref: StrategyRef
    compiled_ref: CompiledStrategyRef
    expected_config_digest: ConfigDigest
    snapshot_id: SnapshotId
    allowed_partitions: Mapping[str, AccessClass]
    complexity: ComplexityMeasures
    complexity_limits: ComplexityLimits
    pre_run_checks: tuple[PolicyCheckResult, ...]
    code_digest: CodeDigest
    lock_digest: LockDigest
    env_digest: EnvDigest
    git_commit: str
    git_dirty: bool

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, ExperimentId):
            raise KernelValueError("ExperimentManifest.experiment_id must be an ExperimentId")
        require_experiment_name(self.experiment_name)
        for label in ("experiment_version", "schema_version", "metric_set_version"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise KernelValueError(f"ExperimentManifest.{label} must be an int >= 1")
        if not isinstance(self.hypothesis, str):
            raise KernelValueError("ExperimentManifest.hypothesis must be a str")
        for label, expected in (
            ("research_policy_ref", PolicyRef),
            ("strategy_ref", StrategyRef),
            ("compiled_ref", CompiledStrategyRef),
            ("expected_config_digest", ConfigDigest),
            ("snapshot_id", SnapshotId),
            ("complexity", ComplexityMeasures),
            ("complexity_limits", ComplexityLimits),
            ("code_digest", CodeDigest),
            ("lock_digest", LockDigest),
            ("env_digest", EnvDigest),
        ):
            if not isinstance(getattr(self, label), expected):
                raise KernelValueError(f"ExperimentManifest.{label} must be a {expected.__name__}")
        for label in ("search_plan", "split", "git_commit"):
            if not isinstance(getattr(self, label), str):
                raise KernelValueError(f"ExperimentManifest.{label} must be a str")
        if not isinstance(self.git_dirty, bool):
            raise KernelValueError("ExperimentManifest.git_dirty must be a bool")
        if not isinstance(self.resolved_files, tuple) or not all(
            isinstance(item, ResolvedFile) for item in self.resolved_files
        ):
            raise KernelValueError("ExperimentManifest.resolved_files must be a tuple")
        roles = [item.role for item in self.resolved_files]
        if len(set(roles)) != len(roles):
            raise KernelValueError("ExperimentManifest.resolved_files holds one file per role")
        missing = _FIXED_ROLES.difference(roles)
        if missing:
            raise KernelValueError(
                f"ExperimentManifest.resolved_files lacks the roles {sorted(missing)} (D07 §19.2)"
            )
        # 役割名の昇順に揃える（並びは意味を持たない。保存と読み戻しで同じ値になるように）。
        object.__setattr__(
            self, "resolved_files", tuple(sorted(self.resolved_files, key=lambda item: item.role))
        )
        if not isinstance(self.allowed_partitions, Mapping) or not all(
            isinstance(key, str) and isinstance(value, AccessClass)
            for key, value in self.allowed_partitions.items()
        ):
            raise KernelValueError(
                "ExperimentManifest.allowed_partitions must map partition ids to AccessClass"
            )
        object.__setattr__(
            self,
            "allowed_partitions",
            MappingProxyType(dict(sorted(self.allowed_partitions.items()))),
        )
        _require_checks(self.pre_run_checks, PRE_RUN_CHECKS, "ExperimentManifest.pre_run_checks")

    def file(self, role: str) -> ResolvedFile:
        """役割名で解決済みのファイルを引く。"""
        for item in self.resolved_files:
            if item.role == role:
                return item
        raise KernelValueError(f"the experiment manifest has no resolved file for {role!r}")

    def symbol_files(self) -> tuple[ResolvedFile, ...]:
        """銘柄仕様（役割 `symbol:<銘柄>`）のファイル。"""
        return tuple(
            item for item in self.resolved_files if item.role.startswith(ROLE_SYMBOL_PREFIX)
        )


def experiment_id_of(
    manifest: ExperimentManifest, experiment_values: Mapping[str, Any]
) -> ExperimentId:
    """記録票の識別子を計算する（D07 §19.2 の「識別の入力」）。

    `experiment_values` は実験設定の YAML を読み込んだ値（解決前の宣言の形）から、パスを持つ
    キー（`strategy` と `environment`）を**キーごと取り除いた** mapping である（入力 2）。
    取り除くのは本関数が行うので、呼び出し側は読み込んだ値をそのまま渡してよい。

    `manifest.experiment_id` そのもの・環境の群（コード・lock・環境のダイジェストと git の
    状態）・パス・予測した `RunId` は入れない（同節）。
    """
    if not isinstance(experiment_values, Mapping):
        raise KernelValueError("experiment_id_of requires the experiment config values")
    values = {
        key: value
        for key, value in experiment_values.items()
        if key not in ("strategy", "environment")
    }
    payload = {
        "experiment_name": manifest.experiment_name,
        "experiment_version": manifest.experiment_version,
        "schema_version": manifest.schema_version,
        "experiment_values": values,
        "referenced_files": [list(pair) for pair in identity_roles(manifest.resolved_files)],
        "hypothesis": manifest.hypothesis,
        "research_policy_ref": manifest.research_policy_ref,
        "metric_set_version": manifest.metric_set_version,
        "search_plan": manifest.search_plan,
        "split": manifest.split,
        "strategy_ref": manifest.strategy_ref,
        "compiled_ref": manifest.compiled_ref,
        "expected_config_digest": manifest.expected_config_digest,
        "snapshot_id": manifest.snapshot_id,
        "allowed_partitions": {
            partition: access.value for partition, access in manifest.allowed_partitions.items()
        },
        "complexity": manifest.complexity,
        "complexity_limits": manifest.complexity_limits,
        "pre_run_checks": list(manifest.pre_run_checks),
    }
    return ExperimentId(digest(payload))


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    """実験の結末記録（D07 §19.3 の表の項目すべて）。

    `expected_run_id` と環境の群は**この実行**のもの（記録票の環境の群とは別の値でありうる）。
    `run_*` は run が行われた（または既存の成果物を再利用した）場合だけ、評価の3項目は評価が
    行われた場合だけ値を持つ。`outcome_checks` は保存時の検査 P3 と事後の検査 P4・P5 の全件
    （事前検査で止まった場合は P3 だけ）。`failed_checks` は `COMPLETED` でないときに合格で
    なかった研究ポリシーの検査（宣言順）で、`COMPLETED` なら空。
    """

    experiment_id: ExperimentId
    status: ExperimentStatus
    expected_run_id: RunId
    code_digest: CodeDigest
    lock_digest: LockDigest
    env_digest: EnvDigest
    git_commit: str
    git_dirty: bool
    run_id: RunId | None
    run_status: RunStatus | None
    run_reused: bool | None
    run_evaluation_id: ContentDigest | None
    evaluation_status: EvaluationStatus | None
    result_digest: ContentDigest | None
    outcome_checks: tuple[PolicyCheckResult, ...]
    failed_checks: tuple[PolicyCheck, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, ExperimentId):
            raise KernelValueError("ExperimentOutcome.experiment_id must be an ExperimentId")
        if not isinstance(self.status, ExperimentStatus):
            raise KernelValueError("ExperimentOutcome.status must be an ExperimentStatus")
        if not isinstance(self.expected_run_id, RunId):
            raise KernelValueError("ExperimentOutcome.expected_run_id must be a RunId")
        for label, expected in (
            ("code_digest", CodeDigest),
            ("lock_digest", LockDigest),
            ("env_digest", EnvDigest),
        ):
            if not isinstance(getattr(self, label), expected):
                raise KernelValueError(f"ExperimentOutcome.{label} must be a {expected.__name__}")
        if not isinstance(self.git_commit, str) or not isinstance(self.git_dirty, bool):
            raise KernelValueError("ExperimentOutcome.git_commit/git_dirty must be str/bool")
        ran = (self.run_id, self.run_status, self.run_reused)
        if any(item is None for item in ran) and any(item is not None for item in ran):
            raise KernelValueError(
                "ExperimentOutcome.run_id / run_status / run_reused are set together (D07 §19.3)"
            )
        evaluated = (self.run_evaluation_id, self.evaluation_status, self.result_digest)
        if any(item is None for item in evaluated) and any(item is not None for item in evaluated):
            raise KernelValueError(
                "ExperimentOutcome.run_evaluation_id / evaluation_status / result_digest are set"
                " together (D07 §19.3)"
            )
        if self.run_evaluation_id is not None and self.run_id is None:
            raise KernelValueError("an evaluation needs a run (D07 §19.4)")
        if not isinstance(self.status, ExperimentStatus):  # pragma: no cover - 上で検査済み
            raise KernelValueError("ExperimentOutcome.status must be an ExperimentStatus")
        _require_checks(
            self.outcome_checks,
            _REJECTED_OUTCOME_CHECKS
            if self.status is ExperimentStatus.REJECTED_BY_POLICY
            else _RUN_OUTCOME_CHECKS,
            "ExperimentOutcome.outcome_checks",
        )
        if not isinstance(self.failed_checks, tuple) or not all(
            isinstance(item, PolicyCheck) for item in self.failed_checks
        ):
            raise KernelValueError("ExperimentOutcome.failed_checks must be a tuple of PolicyCheck")
        if (self.status is ExperimentStatus.COMPLETED) != (not self.failed_checks):
            raise KernelValueError(
                "ExperimentOutcome.failed_checks is empty exactly when the status is COMPLETED"
                " (D07 §19.3)"
            )
        if self.status is ExperimentStatus.REJECTED_BY_POLICY and self.run_id is not None:
            raise KernelValueError("an experiment rejected by the policy does not run (D07 §19.4)")
        if (
            self.status is not ExperimentStatus.REJECTED_BY_POLICY
            and self.run_evaluation_id is None
        ):
            raise KernelValueError(
                "an experiment that passed the pre-run checks has a run and an evaluation"
                " (D07 §19.4)"
            )
