"""研究ポリシー v1: 事前固定の検査と複雑性の上限（D07 §20、2026-09-25 の人間の決定5）。

**検査の規則はコードに置き、ファイルは上限の値だけを持つ**（D07 §20.2）。規則をファイルで
書ける形にすると、検査の意味が設定で変わり、「共通1種類」（上位設計書 §6）の意味が崩れる。

検査は6件（D07 §20.3 の P1〜P6）で、時点は3つ（事前・保存時・事後）である。

| # | 検査 | 時点 | 合格の条件 |
|---|---|---|---|
| P1 | `hypothesis_present` | 事前 | 仮説が空でない |
| P2 | `research_history_only` | 事前 | 許可 partition のアクセス分類がすべて研究履歴 |
| P3 | `preregistration_unchanged` | 保存時 | 同じ名前と版の記録票が無いか、あっても同じ識別子 |
| P4 | `run_matches_preregistration` | 事後 | run の `ConfigDigest` と `run_id` が予測どおり |
| P5 | `evaluation_rule_matches` | 事後 | 評価の指標集合の版が記録票と一致 |
| P6 | `complexity_within_limits` | 事前 | 複雑性の計測値4件がすべて上限以下 |

**合格は `PASSED` だけ**である。`FAILED` と `UNREADABLE`（計測や照合ができなかった）は
どちらも「合格でない」として扱う（D07 §20.3）。計測できなかった複雑性を 0 で埋めると上限の
内側に見えてしまうので、`None` のまま残して P6 を `UNREADABLE` にする。

計測の**規則**は本モジュールの純粋関数に置き、**材料**（使用箇所ごとの `InstanceProfile`）は
合成（`app`）が組み立てて渡す。材料には部品の契約（出力のデータ型）が要るが、評価は部品
カタログを参照できない（D01 §3.2 の契約 F8。D07 §20.4）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.common.canonical import encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.marketdata.domain.access import AccessClass

__all__ = [
    "DECISION_OUTPUT_TYPES",
    "ComplexityLimits",
    "ComplexityMeasures",
    "InstanceProfile",
    "PolicyCheck",
    "PolicyCheckResult",
    "PolicyCheckStage",
    "ResearchPolicy",
    "all_passed",
    "check_complexity",
    "check_evaluation_rule",
    "check_hypothesis",
    "check_preregistration",
    "check_research_history_only",
    "check_run_matches",
    "failed_checks",
    "measure_complexity",
]

#: 判断を出す使用箇所と数える出力のデータ型（D07 §20.4、D04 §5）。
DECISION_OUTPUT_TYPES: Final[frozenset[str]] = frozenset(
    {"condition_state", "market_permission", "opportunity", "confirmation_result"}
)

#: 複雑性の4つの計測値の名前（宣言順。記録と観測値の表示に使う）。
_MEASURE_NAMES: Final = ("component_kinds", "instances", "parameters", "decision_outputs")


def _text(value: object) -> str:
    """検査の期待値・観測値の文字列（D02 §9.3 の正規化エンコード）。"""
    return encode(value).decode("utf-8")


def _require_count(value: object, label: str, *, positive: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"{label} must be an int, got {value!r}")
    if positive and value < 1:
        raise KernelValueError(f"{label} must be a positive integer, got {value}")
    if not positive and value < 0:
        raise KernelValueError(f"{label} must be >= 0, got {value}")


class PolicyCheck(Enum):
    """研究ポリシーの検査6件（D07 §20.3 の P1〜P6。宣言順がその順）。"""

    HYPOTHESIS_PRESENT = "hypothesis_present"
    RESEARCH_HISTORY_ONLY = "research_history_only"
    PREREGISTRATION_UNCHANGED = "preregistration_unchanged"
    RUN_MATCHES_PREREGISTRATION = "run_matches_preregistration"
    EVALUATION_RULE_MATCHES = "evaluation_rule_matches"
    COMPLEXITY_WITHIN_LIMITS = "complexity_within_limits"


#: 検査の宣言順（`failed_checks` の並びに使う。D07 §19.3）。
_CHECK_ORDER: Final = {check: index for index, check in enumerate(PolicyCheck)}


class PolicyCheckStage(Enum):
    """検査の時点（D07 §20.3）。"""

    #: run の前（P1・P2・P6）。結果は記録票の `pre_run_checks` に入る。
    PRE_RUN = "PRE_RUN"
    #: 記録票の保存時（P3）。結果は結末記録の `outcome_checks` に入る。
    ON_SAVE = "ON_SAVE"
    #: run と評価の後（P4・P5）。結果は結末記録の `outcome_checks` に入る。
    POST_RUN = "POST_RUN"


#: 各検査の時点（D07 §20.3 の表）。
_STAGE_OF: Final = {
    PolicyCheck.HYPOTHESIS_PRESENT: PolicyCheckStage.PRE_RUN,
    PolicyCheck.RESEARCH_HISTORY_ONLY: PolicyCheckStage.PRE_RUN,
    PolicyCheck.PREREGISTRATION_UNCHANGED: PolicyCheckStage.ON_SAVE,
    PolicyCheck.RUN_MATCHES_PREREGISTRATION: PolicyCheckStage.POST_RUN,
    PolicyCheck.EVALUATION_RULE_MATCHES: PolicyCheckStage.POST_RUN,
    PolicyCheck.COMPLEXITY_WITHIN_LIMITS: PolicyCheckStage.PRE_RUN,
}


@dataclass(frozen=True, slots=True)
class PolicyCheckResult:
    """研究ポリシーの検査1件の結果（D07 §20.3）。合格・不合格とも全件を残す。

    `expected` / `observed` は D02 §9.3 の正規化エンコード文字列。
    """

    check: PolicyCheck
    stage: PolicyCheckStage
    outcome: CheckOutcome
    expected: str
    observed: str

    def __post_init__(self) -> None:
        if not isinstance(self.check, PolicyCheck):
            raise KernelValueError("PolicyCheckResult.check must be a PolicyCheck")
        if not isinstance(self.stage, PolicyCheckStage):
            raise KernelValueError("PolicyCheckResult.stage must be a PolicyCheckStage")
        if self.stage is not _STAGE_OF[self.check]:
            raise KernelValueError(
                f"the check {self.check.value} runs at {_STAGE_OF[self.check].value},"
                f" not {self.stage.value} (D07 §20.3)"
            )
        if not isinstance(self.outcome, CheckOutcome):
            raise KernelValueError("PolicyCheckResult.outcome must be a CheckOutcome")
        if not isinstance(self.expected, str) or not isinstance(self.observed, str):
            raise KernelValueError("PolicyCheckResult.expected/observed must be str")

    @property
    def passed(self) -> bool:
        """合格は `PASSED` だけ（D07 §20.3）。"""
        return self.outcome is CheckOutcome.PASSED


def _result(
    check: PolicyCheck, outcome: CheckOutcome, expected: object, observed: object
) -> PolicyCheckResult:
    return PolicyCheckResult(
        check=check,
        stage=_STAGE_OF[check],
        outcome=outcome,
        expected=_text(expected),
        observed=_text(observed),
    )


def all_passed(results: Sequence[PolicyCheckResult]) -> bool:
    """全件の `outcome` が `PASSED` か（D07 §19.4 の「全件合格」）。"""
    return all(item.passed for item in results)


def failed_checks(results: Sequence[PolicyCheckResult]) -> tuple[PolicyCheck, ...]:
    """合格でなかった検査の名前を、検査の宣言順（P1〜P6）で返す（D07 §19.3 の `failed_checks`）。"""
    return tuple(
        sorted({item.check for item in results if not item.passed}, key=_CHECK_ORDER.__getitem__)
    )


# --- 研究ポリシーのファイル（D07 §20.2）---------------------------------------


@dataclass(frozen=True, slots=True)
class ComplexityLimits:
    """複雑性の上限4件（D07 §20.2。いずれも正の整数。Q7 決定の値はファイルが持つ）。"""

    component_kinds: int
    instances: int
    parameters: int
    decision_outputs: int

    def __post_init__(self) -> None:
        for name in _MEASURE_NAMES:
            _require_count(getattr(self, name), f"ComplexityLimits.{name}", positive=True)


@dataclass(frozen=True, slots=True)
class ComplexityMeasures:
    """複雑性の計測値4件（D07 §20.4）。

    `None` は**計測できなかった**ことを表す。0 で埋めない（上限の内側に見えてしまう）。
    """

    component_kinds: int | None
    instances: int | None
    parameters: int | None
    decision_outputs: int | None

    def __post_init__(self) -> None:
        for name in _MEASURE_NAMES:
            value = getattr(self, name)
            if value is not None:
                _require_count(value, f"ComplexityMeasures.{name}", positive=False)


@dataclass(frozen=True, slots=True)
class ResearchPolicy:
    """研究ポリシー（D07 §20.2）。版参照は `(policy_id, version, digest)` で記録票に入る。"""

    policy_id: str
    version: int
    digest: ContentDigest
    limits: ComplexityLimits

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id:
            raise KernelValueError("ResearchPolicy.policy_id must be a non-empty str")
        _require_count(self.version, "ResearchPolicy.version", positive=True)
        if not isinstance(self.digest, ContentDigest):
            raise KernelValueError("ResearchPolicy.digest must be a ContentDigest")
        if not isinstance(self.limits, ComplexityLimits):
            raise KernelValueError("ResearchPolicy.limits must be ComplexityLimits")


# --- 複雑性の計測（D07 §20.4）-------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstanceProfile:
    """使用箇所1件の計測の材料（D07 §20.4）。合成がコンパイル結果と部品の登録から作る。

    `parameter_count` は**解決済みパラメータ**（既定値で埋めたものを含む）の件数。
    `output_data_types` は部品の契約が宣言する出力のデータ型の識別子（版を含まない）。
    """

    instance_id: str
    component_id: str
    parameter_count: int
    output_data_types: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.instance_id, str) or not self.instance_id:
            raise KernelValueError("InstanceProfile.instance_id must be a non-empty str")
        if not isinstance(self.component_id, str) or not self.component_id:
            raise KernelValueError("InstanceProfile.component_id must be a non-empty str")
        _require_count(self.parameter_count, "InstanceProfile.parameter_count", positive=False)
        if not isinstance(self.output_data_types, tuple) or not all(
            isinstance(item, str) and item for item in self.output_data_types
        ):
            raise KernelValueError("InstanceProfile.output_data_types must be a tuple of str")


def measure_complexity(
    profiles: Sequence[InstanceProfile], *, unmeasured_outputs: Sequence[str] = ()
) -> ComplexityMeasures:
    """使用箇所の材料から計測値4件を数える（D07 §20.4 の表）。

    - `component_kinds`: 使われている部品の種類（`component_id`。版は区別しない）の数。
    - `instances`: 使用箇所の数。
    - `parameters`: 全使用箇所の解決済みパラメータの件数の合計。
    - `decision_outputs`: 判断を出すデータ型の出力を1つ以上持つ使用箇所の数。

    `unmeasured_outputs` は、部品の登録が見つからず出力のデータ型を知り得なかった使用箇所の
    ID。1件でもあれば `decision_outputs` は計測できなかった（`None`）とする。0 で埋めると
    上限の内側に見える（D07 §20.3）。
    """
    for profile in profiles:
        if not isinstance(profile, InstanceProfile):
            raise KernelValueError("measure_complexity requires InstanceProfile values")
    ids = [profile.instance_id for profile in profiles]
    if len(set(ids)) != len(ids):
        raise KernelValueError("measure_complexity: instance ids must be unique (D07 §20.4)")
    decision_outputs = (
        None
        if unmeasured_outputs
        else sum(
            1
            for profile in profiles
            if DECISION_OUTPUT_TYPES.intersection(profile.output_data_types)
        )
    )
    return ComplexityMeasures(
        component_kinds=len({profile.component_id for profile in profiles}),
        instances=len(profiles),
        parameters=sum(profile.parameter_count for profile in profiles),
        decision_outputs=decision_outputs,
    )


def _measures_payload(measures: ComplexityMeasures) -> dict[str, int | None]:
    return {name: getattr(measures, name) for name in _MEASURE_NAMES}


def _limits_payload(limits: ComplexityLimits) -> dict[str, int]:
    return {name: getattr(limits, name) for name in _MEASURE_NAMES}


# --- 検査（D07 §20.3）---------------------------------------------------------


def check_hypothesis(hypothesis: str) -> PolicyCheckResult:
    """P1: 仮説が空でない（書式でも拒否するが、記録票の検査として残す）。"""
    present = isinstance(hypothesis, str) and bool(hypothesis.strip())
    return _result(
        PolicyCheck.HYPOTHESIS_PRESENT,
        CheckOutcome.PASSED if present else CheckOutcome.FAILED,
        "a non-blank hypothesis",
        {"characters": len(hypothesis.strip()) if isinstance(hypothesis, str) else 0},
    )


def check_research_history_only(
    allowed_partitions: Mapping[str, AccessClass],
) -> PolicyCheckResult:
    """P2: 許可 partition のアクセス分類がすべて研究履歴（D03 §3.8）。

    観測値には研究履歴でない partition とその分類を書く（合格なら空）。
    """
    outside = {
        partition: access.value
        for partition, access in sorted(allowed_partitions.items())
        if access is not AccessClass.RESEARCH_HISTORY
    }
    return _result(
        PolicyCheck.RESEARCH_HISTORY_ONLY,
        CheckOutcome.FAILED if outside else CheckOutcome.PASSED,
        {"access_classes": [AccessClass.RESEARCH_HISTORY.value]},
        {"partitions_outside_research_history": outside},
    )


def check_complexity(
    measures: ComplexityMeasures,
    limits: ComplexityLimits,
    *,
    unmeasured_causes: Sequence[str] = (),
) -> PolicyCheckResult:
    """P6: 複雑性の計測値4件がすべて上限以下（D07 §20.3・§20.4）。

    計測できなかった値（`None`）があれば `UNREADABLE`。観測値に計測できなかった項目と原因を
    書く。合格として扱わない。
    """
    measured = _measures_payload(measures)
    unmeasured = [name for name, value in measured.items() if value is None]
    over = {
        name: {"measured": value, "limit": getattr(limits, name)}
        for name, value in measured.items()
        if value is not None and value > getattr(limits, name)
    }
    if unmeasured:
        outcome = CheckOutcome.UNREADABLE
    elif over:
        outcome = CheckOutcome.FAILED
    else:
        outcome = CheckOutcome.PASSED
    observed: dict[str, object] = {"measures": measured, "over_limit": over}
    if unmeasured:
        observed["unmeasured"] = unmeasured
        observed["causes"] = list(unmeasured_causes)
    return _result(
        PolicyCheck.COMPLEXITY_WITHIN_LIMITS,
        outcome,
        {"limits": _limits_payload(limits)},
        observed,
    )


def check_preregistration(experiment_id_hex: str, existing_id_hex: str | None) -> PolicyCheckResult:
    """P3: 同じ名前と版の記録票が無いか、あっても同じ識別子（D07 §20.3）。

    `existing_id_hex` は保存先に既にあった記録票の識別子（無ければ `None`）。
    """
    same = existing_id_hex is None or existing_id_hex == experiment_id_hex
    return _result(
        PolicyCheck.PREREGISTRATION_UNCHANGED,
        CheckOutcome.PASSED if same else CheckOutcome.FAILED,
        {"experiment_id": experiment_id_hex},
        {"existing_experiment_id": existing_id_hex},
    )


def check_run_matches(
    *,
    expected_config_digest_hex: str,
    expected_run_id_hex: str,
    observed_config_digest_hex: str | None,
    observed_run_id_hex: str,
    manifest_detail: str | None = None,
) -> PolicyCheckResult:
    """P4: run manifest の `ConfigDigest` が記録票の予測と、実際の `run_id` が結末記録の予測と一致。

    run manifest が読めず `ConfigDigest` を照合できなければ `UNREADABLE`（`manifest_detail` に
    読めなかった理由）。
    """
    expected = {"config_digest": expected_config_digest_hex, "run_id": expected_run_id_hex}
    observed: dict[str, object] = {
        "config_digest": observed_config_digest_hex,
        "run_id": observed_run_id_hex,
    }
    if observed_config_digest_hex is None:
        observed["run_manifest"] = manifest_detail
        outcome = CheckOutcome.UNREADABLE
    elif observed == expected:
        outcome = CheckOutcome.PASSED
    else:
        outcome = CheckOutcome.FAILED
    return _result(PolicyCheck.RUN_MATCHES_PREREGISTRATION, outcome, expected, observed)


def check_evaluation_rule(expected_version: int, observed_version: int) -> PolicyCheckResult:
    """P5: 評価 manifest の指標集合の版が記録票と一致（D07 §20.3）。"""
    return _result(
        PolicyCheck.EVALUATION_RULE_MATCHES,
        CheckOutcome.PASSED if expected_version == observed_version else CheckOutcome.FAILED,
        {"metric_set_version": expected_version},
        {"metric_set_version": observed_version},
    )
