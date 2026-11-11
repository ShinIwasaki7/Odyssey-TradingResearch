"""前向き検証の規則のファイルの値と、候補の固定の記録の書式の型（D09 §9.11.12。順 6 の F2）。

- **規則のファイル**（`configs/policies/forward_validation/forward_validation_v<版>.yaml`）の値を
  `ForwardValidationRules` に持つ。値は 2026-11-10 の人間の決定（要決定 F1-A〜F1-G＝決定 71〜77、
  仮置き F1-1〜F1-19）と D09 §9.11 の本文の規則の値で、F2 で新しく選ばない（仮置き F1-17）。
  列挙はこの実装が知る規則の名前だけを持ち、決定で選ばれなかった候補（例えば要決定 F1-A の (b)）は
  持たない。集合の値（`fix_deferral.exclude`・`reservation_release`）は決定の集合と一致しなければ
  ならない（一部だけを書くと、決定と違う規則を選んだことになる）。
- **登録簿の要素**は `ForwardRulesEntry`（採用審査の規則の登録簿と同じ形。D16 §10.3）。
- **固定の記録**（`research/candidate_fixes/<experiment_id>/<execution>/<fix_no>.yaml`。
  仮置き F1-2）の書式の型が `CandidateFix`。固定の記録の読込・固定の前の確かめ・前向き検証の
  コマンドは要決定 F1-E（決定 75）で別の実装 PR に送ったので、本モジュールは型と、型だけで
  決まる検査だけを持つ。
  固定の記録には `T_fix`・`S`・`W`・`S'`・`E` を書かない（D09 §9.11.3・§9.11.6）。

どの値も成績・価格を含まない（日数・件数の下限・確率・時刻・ダイジェストだけ）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import CompiledStrategyRef, ContentDigest
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.search import LineageSource
from odyssey_fx.evaluation.domain.variant import Paired
from odyssey_fx.marketdata.domain.access import SEALED_RESEARCH_UNTIL

__all__ = [
    "CANDIDATE_FIX_SCHEMA_VERSION",
    "FORWARD_VALIDATION_ID",
    "REQUIRED_DEFERRAL_EXCLUSIONS",
    "REQUIRED_RELEASE_CONDITIONS",
    "AcquisitionStartRef",
    "CandidateFix",
    "CandidateFixId",
    "DeferralExclusion",
    "FinalWindowEnd",
    "FixCandidate",
    "FinalWindowRule",
    "FixDeferralRule",
    "FixTimeRule",
    "FixWeekTreatment",
    "FixedTrialPin",
    "ForwardRulesEntry",
    "ForwardValidationRules",
    "FrequencyMaterialRef",
    "FrequencySource",
    "IncompletePeriodRule",
    "JudgmentMethod",
    "JudgmentPolicyVersion",
    "JudgmentRule",
    "LengthMethod",
    "LengthRule",
    "MinTradesSource",
    "OnBoundary",
    "PairCombination",
    "PairJudgment",
    "PeriodEnd",
    "Prerequisite",
    "ReleaseCondition",
    "ScoringRule",
    "StartBasis",
    "StartRule",
    "UsableWindow",
    "VersionedRef",
    "WarmupLength",
    "WarmupRule",
    "WeekBoundary",
]

#: 規則の識別（登録簿が載せてよい唯一の id。仮置き F1-2）。
FORWARD_VALIDATION_ID: Final = "forward_validation"
#: 固定の記録の書式の形式版（D09 §9.11.12 の 2 の `schema_version`）。
CANDIDATE_FIX_SCHEMA_VERSION: Final = 1

_NAME: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
#: 参照先の設定ファイルのパス（リポジトリの根からの相対。`/` 区切り、`..` と空の要素を許さない）。
_RELATIVE_PATH: Final = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")


def _require_int(value: object, label: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise KernelValueError(f"{label} must be an int >= {minimum}, got {value!r}")


def _require_member(value: object, enum_type: type[Enum], label: str) -> None:
    if not isinstance(value, enum_type):
        raise KernelValueError(f"{label} must be a {enum_type.__name__}, got {value!r}")


def _require_exact_set[EnumT: Enum](
    values: object, enum_type: type[EnumT], required: frozenset[EnumT], label: str
) -> None:
    if not isinstance(values, tuple) or not all(isinstance(item, enum_type) for item in values):
        raise KernelValueError(f"{label} must be a tuple of {enum_type.__name__}")
    if len(set(values)) != len(values):
        raise KernelValueError(f"{label} has duplicates: {[item.value for item in values]}")
    if frozenset(values) != required:
        raise KernelValueError(
            f"{label} must be exactly {sorted(item.value for item in required)}"
            f" (the decided set), got {sorted(item.value for item in values)}"
        )


def _require_name(value: object, label: str) -> None:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise KernelValueError(f"{label} is not a name: {value!r}")


# --- 規則のファイルの列挙（D09 §9.11.12 の 1。値は決定で選ばれたものだけ）-----------------------


class FixTimeRule(Enum):
    """固定時点の求め方（D15-10b (a)、仮置き F1-3）。"""

    #: main の first-parent の履歴で、固定の記録のファイルを初めて含んだコミットの
    #: committer の時刻。
    FIRST_PARENT_FIRST_INCLUSION_COMMITTER_TIME = "FIRST_PARENT_FIRST_INCLUSION_COMMITTER_TIME"


class StartBasis(Enum):
    """開始 `S` の基準の時刻（D15-10b (a)）。"""

    FIX_TIME = "FIX_TIME"


class WeekBoundary(Enum):
    """取引週の境目（ADR-0038 第2項。`marketdata.domain.access` の取引週の定義と同じ）。"""

    #: ニューヨーク（`America/New_York`）の日曜 17:00。
    NEW_YORK_SUNDAY_1700 = "NEW_YORK_SUNDAY_1700"


class OnBoundary(Enum):
    """基準の時刻がちょうど週の境目のときの扱い（仮置き F1-4。ADR-0037 第2項の 2 と同じ）。"""

    NEXT_BOUNDARY = "NEXT_BOUNDARY"


class FinalWindowEnd(Enum):
    """最終検証の期間の終わり（D15-16 (a)）。"""

    FIX_TIME = "FIX_TIME"


class FixWeekTreatment(Enum):
    """固定時点を含む週の partition の扱い（D15-16 (a)、ADR-0038 第5項の 3）。"""

    EXCLUDED = "EXCLUDED"


class LengthMethod(Enum):
    """長さ `L` の求め方（D15-11 (a)。D13 §5.1 と同じポアソン分布の仮定）。"""

    POISSON = "POISSON"


class MinTradesSource(Enum):
    """最低件数 `N` の出どころ（D09 §9.11.5）。"""

    #: 固定の記録が指す研究ポリシーの版の `sufficiency.min_validation_trades_total`。
    RESEARCH_POLICY_MIN_VALIDATION_TRADES_TOTAL = "RESEARCH_POLICY_MIN_VALIDATION_TRADES_TOTAL"


class FrequencySource(Enum):
    """頻度 `r` の出どころ（決定 72＝要決定 F1-B (a)）。"""

    #: 採用審査の、組ごとに固定した試行を研究履歴の全 fold の検証区間で走らせた完了取引の件数。
    ADOPTION_REVIEW_FIXED_TRIAL_VALIDATION_FOLDS = "ADOPTION_REVIEW_FIXED_TRIAL_VALIDATION_FOLDS"


class PairCombination(Enum):
    """組（標準・候補）ごとの `L` の合わせ方（D09 §9.11.5）。"""

    MAX = "MAX"


class WarmupRule(Enum):
    """ウォームアップの扱い（決定 73＝要決定 F1-C (a)）。"""

    #: 前向きの期間の先頭をウォームアップに使い、採点の始まりを `S + W` にずらす。
    LEADING_PART_OF_PERIOD = "LEADING_PART_OF_PERIOD"


class WarmupLength(Enum):
    """`W` の求め方とカレンダーの出どころ（仮置き F1-19）。"""

    #: 最終検証と同じ求め方（D09 §9.10.3 の 7）で、前向きの期間の承認済みの snapshot の
    #: カレンダーから。
    FINAL_VALIDATION_METHOD_ON_FORWARD_SNAPSHOT_CALENDAR = (
        "FINAL_VALIDATION_METHOD_ON_FORWARD_SNAPSHOT_CALENDAR"
    )


class PeriodEnd(Enum):
    """前向きの期間の終わり `E`（決定 76＝要決定 F1-F (b)、仮置き F1-6）。"""

    #: `S + L` 以後の最初の取引週の境目。
    FIRST_WEEK_BOUNDARY_AT_OR_AFTER_START_PLUS_LENGTH = (
        "FIRST_WEEK_BOUNDARY_AT_OR_AFTER_START_PLUS_LENGTH"
    )


class UsableWindow(Enum):
    """固定の前の確かめの使える窓の定義（D09 §9.10.3 の 5、要決定 R2-9 (a)）。"""

    LATEST_CONTIGUOUS = "LATEST_CONTIGUOUS"


class DeferralExclusion(Enum):
    """使える窓から除くもの（D09 §9.11.10 の項目 6）。"""

    EARLIER_CANDIDATES_PLANNED_FINAL_WINDOWS = "EARLIER_CANDIDATES_PLANNED_FINAL_WINDOWS"
    OTHER_CANDIDATES_RESERVED_FORWARD_PERIODS = "OTHER_CANDIDATES_RESERVED_FORWARD_PERIODS"


class JudgmentMethod(Enum):
    """判定の方式（決定 71＝要決定 F1-A (a)）。"""

    SAME_AS_FINAL_VALIDATION = "SAME_AS_FINAL_VALIDATION"


class JudgmentPolicyVersion(Enum):
    """判定に使う研究ポリシーの版（仮置き F1-8）。"""

    FIXED_RECORD_VERSION = "FIXED_RECORD_VERSION"


class PairJudgment(Enum):
    """組の判定（D09 §7.12）。"""

    D09_SECTION_7_12 = "D09_SECTION_7_12"


class Prerequisite(Enum):
    """前向き検証の申請の前提（仮置き F1-7）。"""

    FINAL_VALIDATION_MEETS_BOTH_PAIRS = "FINAL_VALIDATION_MEETS_BOTH_PAIRS"


class ReleaseCondition(Enum):
    """予約を解く条件（決定 74＝要決定 F1-D (a)）。"""

    #: 最終検証が完了し、組の判定が「満たす（両組）」以外（満たさない・証拠不足・判定できない）。
    FINAL_COMPLETED_NOT_MEETS_BOTH_PAIRS = "FINAL_COMPLETED_NOT_MEETS_BOTH_PAIRS"
    FINAL_FAILED_AFTER_CONSUMPTION = "FINAL_FAILED_AFTER_CONSUMPTION"
    FINAL_DECLARED_USED = "FINAL_DECLARED_USED"
    FINAL_WITHDRAWN = "FINAL_WITHDRAWN"
    #: 前向き検証の申請の前の取り下げ（置き換えられた固定の記録を含む。仮置き F1-12）。
    WITHDRAWN_BEFORE_FORWARD_APPLICATION = "WITHDRAWN_BEFORE_FORWARD_APPLICATION"


class IncompletePeriodRule(Enum):
    """前向きの期間が欠けたときの扱い（仮置き F1-11）。"""

    STOP_WITHOUT_EXTENDING_OR_SHIFTING = "STOP_WITHOUT_EXTENDING_OR_SHIFTING"


#: 使える窓から除くものの決定の集合（D09 §9.11.10 の項目 6）。
REQUIRED_DEFERRAL_EXCLUSIONS: Final = frozenset(DeferralExclusion)
#: 予約を解く条件の決定の集合（決定 74）。
REQUIRED_RELEASE_CONDITIONS: Final = frozenset(ReleaseCondition)


# --- 規則のファイルの構造（D09 §9.11.12 の 1）--------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcquisitionStartRef:
    """取得の開始時点の参照（設定ファイルのパスとキー。D15-10a (a)、ADR-0037 第4項）。"""

    path: str
    key: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.path, str)
            or not _RELATIVE_PATH.fullmatch(self.path)
            or any(part in {".", ".."} for part in self.path.split("/"))
        ):
            raise KernelValueError(
                f"AcquisitionStartRef.path must be a relative path in the repository: {self.path!r}"
            )
        _require_name(self.key, "AcquisitionStartRef.key")


@dataclass(frozen=True, slots=True)
class StartRule:
    """開始の事前規則（D09 §9.11.4 の 1）。"""

    basis: StartBasis
    week_boundary: WeekBoundary
    on_boundary: OnBoundary

    def __post_init__(self) -> None:
        _require_member(self.basis, StartBasis, "StartRule.basis")
        _require_member(self.week_boundary, WeekBoundary, "StartRule.week_boundary")
        _require_member(self.on_boundary, OnBoundary, "StartRule.on_boundary")


@dataclass(frozen=True, slots=True)
class FinalWindowRule:
    """最終検証の期間（D15-16 (a)、ADR-0038 第2項の 2・第5項）。

    始まりは研究履歴の終わり（2024-01-01T00:00Z。`SEALED_RESEARCH_UNTIL`）でなければならない。
    """

    start: UtcTime
    end: FinalWindowEnd
    fix_week: FixWeekTreatment

    def __post_init__(self) -> None:
        if not isinstance(self.start, UtcTime) or self.start != SEALED_RESEARCH_UNTIL:
            raise KernelValueError(
                f"FinalWindowRule.start must be {SEALED_RESEARCH_UNTIL} (D15-16 (a)), got"
                f" {self.start!r}"
            )
        _require_member(self.end, FinalWindowEnd, "FinalWindowRule.end")
        _require_member(self.fix_week, FixWeekTreatment, "FinalWindowRule.fix_week")


@dataclass(frozen=True, slots=True)
class LengthRule:
    """長さ `L` の規則（D15-11 (a)、決定 72、仮置き F1-5。D09 §9.11.5）。

    `probability` は 0 より大きく 1 より小さい `Decimal`、`min_days` と `precision_digits` は
    1 以上。
    """

    method: LengthMethod
    probability: Decimal
    min_days: int
    min_trades_source: MinTradesSource
    frequency_source: FrequencySource
    pair_combination: PairCombination
    precision_digits: int

    def __post_init__(self) -> None:
        _require_member(self.method, LengthMethod, "LengthRule.method")
        probability = self.probability
        if (
            not isinstance(probability, Decimal)
            or not probability.is_finite()
            or not 0 < probability < 1
        ):
            raise KernelValueError(
                f"LengthRule.probability must be a Decimal in (0, 1), got {probability!r}"
            )
        _require_int(self.min_days, "LengthRule.min_days", minimum=1)
        _require_member(self.min_trades_source, MinTradesSource, "LengthRule.min_trades_source")
        _require_member(self.frequency_source, FrequencySource, "LengthRule.frequency_source")
        _require_member(self.pair_combination, PairCombination, "LengthRule.pair_combination")
        _require_int(self.precision_digits, "LengthRule.precision_digits", minimum=1)


@dataclass(frozen=True, slots=True)
class ScoringRule:
    """ウォームアップと採点区間（決定 73・決定 76、仮置き F1-6・F1-19。D09 §9.11.6）。"""

    warmup: WarmupRule
    warmup_length: WarmupLength
    period_end: PeriodEnd

    def __post_init__(self) -> None:
        _require_member(self.warmup, WarmupRule, "ScoringRule.warmup")
        _require_member(self.warmup_length, WarmupLength, "ScoringRule.warmup_length")
        _require_member(self.period_end, PeriodEnd, "ScoringRule.period_end")


@dataclass(frozen=True, slots=True)
class FixDeferralRule:
    """長さが足りないときの固定の延期（D15-15 (a)、要決定 R2-9 (a)。D09 §9.11.10 の項目 6）。"""

    usable_window: UsableWindow
    exclude: tuple[DeferralExclusion, ...]

    def __post_init__(self) -> None:
        _require_member(self.usable_window, UsableWindow, "FixDeferralRule.usable_window")
        _require_exact_set(
            self.exclude,
            DeferralExclusion,
            REQUIRED_DEFERRAL_EXCLUSIONS,
            "FixDeferralRule.exclude",
        )


@dataclass(frozen=True, slots=True)
class JudgmentRule:
    """判定の規則（決定 71、仮置き F1-8。D09 §9.11.7）。"""

    method: JudgmentMethod
    research_policy: JudgmentPolicyVersion
    pair_combination: PairJudgment

    def __post_init__(self) -> None:
        _require_member(self.method, JudgmentMethod, "JudgmentRule.method")
        _require_member(self.research_policy, JudgmentPolicyVersion, "JudgmentRule.research_policy")
        _require_member(self.pair_combination, PairJudgment, "JudgmentRule.pair_combination")


@dataclass(frozen=True, slots=True)
class ForwardValidationRules:
    """前向き検証の規則のファイルの値（D09 §9.11.12 の 1 の 13 項目）。"""

    rules_id: str
    version: int
    acquisition_start_ref: AcquisitionStartRef
    fix_time: FixTimeRule
    start: StartRule
    final_window: FinalWindowRule
    length: LengthRule
    scoring: ScoringRule
    fix_deferral: FixDeferralRule
    judgment: JudgmentRule
    prerequisite: Prerequisite
    reservation_release: tuple[ReleaseCondition, ...]
    incomplete_period: IncompletePeriodRule

    def __post_init__(self) -> None:
        if self.rules_id != FORWARD_VALIDATION_ID:
            raise KernelValueError(
                f"ForwardValidationRules.rules_id must be {FORWARD_VALIDATION_ID!r},"
                f" got {self.rules_id!r}"
            )
        _require_int(self.version, "ForwardValidationRules.version", minimum=1)
        for name, value_type in (
            ("acquisition_start_ref", AcquisitionStartRef),
            ("start", StartRule),
            ("final_window", FinalWindowRule),
            ("length", LengthRule),
            ("scoring", ScoringRule),
            ("fix_deferral", FixDeferralRule),
            ("judgment", JudgmentRule),
        ):
            if not isinstance(getattr(self, name), value_type):
                raise KernelValueError(
                    f"ForwardValidationRules.{name} must be a {value_type.__name__}"
                )
        _require_member(self.fix_time, FixTimeRule, "ForwardValidationRules.fix_time")
        _require_member(self.prerequisite, Prerequisite, "ForwardValidationRules.prerequisite")
        _require_exact_set(
            self.reservation_release,
            ReleaseCondition,
            REQUIRED_RELEASE_CONDITIONS,
            "ForwardValidationRules.reservation_release",
        )
        _require_member(
            self.incomplete_period, IncompletePeriodRule, "ForwardValidationRules.incomplete_period"
        )


@dataclass(frozen=True, slots=True)
class ForwardRulesEntry:
    """前向き検証の規則の登録簿の要素（D09 §9.11.12 の 1。採用審査の規則の登録簿と同じ形）。

    `effective_after` は版 1 では `None`（最初から効く）。版 2 以上の効き始めの印は、版 2 を足す
    PR で決める（本 PR の仮置き。それまで版 2 以上の要素は登録簿の読込で止まる）。
    """

    rules_id: str
    version: int
    digest: ContentDigest
    effective_after: ContentDigest | None

    def __post_init__(self) -> None:
        if self.rules_id != FORWARD_VALIDATION_ID:
            raise KernelValueError(
                f"ForwardRulesEntry.rules_id must be {FORWARD_VALIDATION_ID!r},"
                f" got {self.rules_id!r}"
            )
        _require_int(self.version, "ForwardRulesEntry.version", minimum=1)
        if not isinstance(self.digest, ContentDigest):
            raise KernelValueError("ForwardRulesEntry.digest must be a ContentDigest")
        if self.effective_after is not None and not isinstance(self.effective_after, ContentDigest):
            raise KernelValueError("ForwardRulesEntry.effective_after must be a ContentDigest")

    @property
    def ref(self) -> VersionedRef:
        return VersionedRef(ref_id=self.rules_id, version=self.version, digest=self.digest)


# --- 固定の記録の書式（D09 §9.11.12 の 2）---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VersionedRef:
    """版のある規則のファイルの参照（id・版・ダイジェスト）。研究ポリシーと前向き検証の規則に使う。"""

    ref_id: str
    version: int
    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_name(self.ref_id, "VersionedRef.ref_id")
        _require_int(self.version, "VersionedRef.version", minimum=1)
        if not isinstance(self.digest, ContentDigest):
            raise KernelValueError("VersionedRef.digest must be a ContentDigest")


@dataclass(frozen=True, slots=True)
class FixCandidate:
    """固定する候補（探索の実験の 1 回の実行 `(experiment_id, execution)`）。"""

    experiment_id: ExperimentId
    execution: int

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, ExperimentId):
            raise KernelValueError("FixCandidate.experiment_id must be an ExperimentId")
        _require_int(self.execution, "FixCandidate.execution", minimum=1)


@dataclass(frozen=True, slots=True)
class CandidateFixId:
    """固定の記録の識別子 `(experiment_id, execution, fix_no)`（仮置き F1-2）。"""

    experiment_id: ExperimentId
    execution: int
    fix_no: int

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, ExperimentId):
            raise KernelValueError("CandidateFixId.experiment_id must be an ExperimentId")
        _require_int(self.execution, "CandidateFixId.execution", minimum=1)
        _require_int(self.fix_no, "CandidateFixId.fix_no", minimum=1)

    def same_execution(self, other: CandidateFixId) -> bool:
        """同じ実行（`(experiment_id, execution)` が同じ）か。"""
        return (self.experiment_id, self.execution) == (other.experiment_id, other.execution)


@dataclass(frozen=True, slots=True)
class FixedTrialPin:
    """組ごとの固定した試行（`trial_index`・パラメータの割当のダイジェスト・`compiled_ref`）。"""

    trial_index: int
    assignment_digest: ContentDigest
    compiled_ref: CompiledStrategyRef

    def __post_init__(self) -> None:
        _require_int(self.trial_index, "FixedTrialPin.trial_index", minimum=0)
        if not isinstance(self.assignment_digest, ContentDigest):
            raise KernelValueError("FixedTrialPin.assignment_digest must be a ContentDigest")
        if not isinstance(self.compiled_ref, CompiledStrategyRef):
            raise KernelValueError("FixedTrialPin.compiled_ref must be a CompiledStrategyRef")


@dataclass(frozen=True, slots=True)
class FrequencyMaterialRef:
    """頻度 `r` の材料の識別子とダイジェスト（件数は書かない。仮置き F1-5）。"""

    material_id: str
    digest: ContentDigest

    def __post_init__(self) -> None:
        if not isinstance(self.material_id, str) or not self.material_id.strip():
            raise KernelValueError("FrequencyMaterialRef.material_id must be a non-empty str")
        if not isinstance(self.digest, ContentDigest):
            raise KernelValueError("FrequencyMaterialRef.digest must be a ContentDigest")


@dataclass(frozen=True, slots=True)
class CandidateFix:
    """1 つの候補の固定の記録（D09 §9.11.12 の 2。`schema_version` を除く項目）。

    型だけで決まる検査:

    - `forward_rules` の id は `forward_validation`。
    - `length_days` は 1 以上の整数（規則のファイルの下限との照合は固定の前の確かめの項目 5）。
    - `supersedes`（置き換える前の固定の記録）: 同じ実行を指すなら `fix_no − 1`。`fix_no` が 2 以上
      なら同じ実行の `fix_no − 1` を指す（置き換えのときだけ 2 以上のファイルが増える。仮置き
      F1-12）。別の実行を指すのは別の版の固定（D09 §9.11.9 の 2）。
    - `fixed_by` は空でない。
    """

    candidate: FixCandidate
    fix_no: int
    fixed_trials: Paired[FixedTrialPin]
    record_sheet_digest: ContentDigest
    research_policy: VersionedRef
    forward_rules: VersionedRef
    adoption_review_row_digest: ContentDigest
    length_days: int
    frequency_source: FrequencyMaterialRef
    derived_from: LineageSource | None
    supersedes: CandidateFixId | None
    fixed_by: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, FixCandidate):
            raise KernelValueError("CandidateFix.candidate must be a FixCandidate")
        _require_int(self.fix_no, "CandidateFix.fix_no", minimum=1)
        if not isinstance(self.fixed_trials, Paired):
            raise KernelValueError("CandidateFix.fixed_trials must be a Paired")
        self.fixed_trials.require(FixedTrialPin, "CandidateFix.fixed_trials")
        for name in ("record_sheet_digest", "adoption_review_row_digest"):
            if not isinstance(getattr(self, name), ContentDigest):
                raise KernelValueError(f"CandidateFix.{name} must be a ContentDigest")
        for name in ("research_policy", "forward_rules"):
            if not isinstance(getattr(self, name), VersionedRef):
                raise KernelValueError(f"CandidateFix.{name} must be a VersionedRef")
        if self.forward_rules.ref_id != FORWARD_VALIDATION_ID:
            raise KernelValueError(
                f"CandidateFix.forward_rules must point to {FORWARD_VALIDATION_ID!r},"
                f" got {self.forward_rules.ref_id!r}"
            )
        _require_int(self.length_days, "CandidateFix.length_days", minimum=1)
        if not isinstance(self.frequency_source, FrequencyMaterialRef):
            raise KernelValueError("CandidateFix.frequency_source must be a FrequencyMaterialRef")
        if self.derived_from is not None and not isinstance(self.derived_from, LineageSource):
            raise KernelValueError("CandidateFix.derived_from must be a LineageSource or None")
        self._check_supersedes()
        if not isinstance(self.fixed_by, str) or not self.fixed_by.strip():
            raise KernelValueError("CandidateFix.fixed_by must be a non-empty str")

    def _check_supersedes(self) -> None:
        own, previous = self.fix_id, self.supersedes
        if previous is not None and not isinstance(previous, CandidateFixId):
            raise KernelValueError("CandidateFix.supersedes must be a CandidateFixId or None")
        if own.fix_no >= 2 and (
            previous is None
            or not own.same_execution(previous)
            or previous.fix_no != own.fix_no - 1
        ):
            raise KernelValueError(
                "CandidateFix.supersedes must point to fix_no - 1 of the same execution when"
                f" fix_no >= 2 (fix_no={own.fix_no}, supersedes={previous!r})"
            )
        if (
            previous is not None
            and own.same_execution(previous)
            and previous.fix_no != own.fix_no - 1
        ):
            raise KernelValueError(
                "CandidateFix.supersedes in the same execution must be fix_no - 1"
                f" (fix_no={own.fix_no}, supersedes.fix_no={previous.fix_no})"
            )

    @property
    def fix_id(self) -> CandidateFixId:
        """固定の記録の識別子 `(experiment_id, execution, fix_no)`。"""
        return CandidateFixId(
            experiment_id=self.candidate.experiment_id,
            execution=self.candidate.execution,
            fix_no=self.fix_no,
        )
