"""採用審査（研究候補を最終検証へ進めてよいかを、事前に固定した規則で確かめる。D16）。

本モジュールは状態を持たない規則だけを置く（入出力は `evaluation.adapters` と `app` が行う）。

- 審査の規則（`ReviewRules`）と規則の登録簿の要素（`ReviewRulesEntry`。D16 §10.3、仮置き 10）。
- 入口の検査（`check_entrance`。D16 §3.1 の順 1〜8。試行台帳・研究ポリシーの登録簿・審査台帳・
  規則の登録簿だけを読む。仮置き 13）。
- 台帳の累計 L1〜L5（`ledger_tally`。D16 §6。R1-9 (a)・仮置き 7）。
- 候補自身の証拠の最低線（`evidence_floor`。D16 §4。D15-6 (a)）。
- 費用・約定モデルの適合の検査（`cost_model_fit`。D16 §7。D15-8 (a)、R1-5〜R1-7 (a)、仮置き 8・9）。
- 結果の合わせ方（`combine_result`。D16 §9.1。R1-4 (a)）。
- 審査台帳の行（`AdoptionReviewEntry`・`AdoptionReviewLine`）と読込の検査（`review_ledger_defect`。
  D16 §10.2。書き込みの規約は試行台帳と同じ。D09 §10.12）。

**指標の数値は審査台帳の行に入れない**（R1-1 (a)）。行が持つのは結果の値・理由・合否・件数・
ハッシュだけで、数値は版管理の外の数値の記録（`runs/` の下）に置く。
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from enum import Enum
from typing import Final

from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.money import CurrencyCode, decimal_from_int, kernel_context
from odyssey_fx.common.refs import CodeDigest, ContentDigest, LockDigest, PolicyRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.experiment import ExperimentStatus
from odyssey_fx.evaluation.domain.metrics import TradeRecord, trading_day_ends
from odyssey_fx.evaluation.domain.research_policy import RegistryEntry, current_standard_version
from odyssey_fx.evaluation.domain.search import (
    SearchVerdict,
    StandardPurpose,
    TrialLedgerEvent,
    TrialLedgerLine,
    finished_line_of,
    started_line_of,
)
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant
from odyssey_fx.marketdata.domain.calendar import TradingCalendar

__all__ = [
    "ADOPTION_REVIEW_SCHEMA_VERSION",
    "EVIDENCE_NOTE",
    "AdoptionReviewEntry",
    "AdoptionReviewLine",
    "Capability",
    "CostFitMeasures",
    "CostFitResult",
    "CostFitRow",
    "CostModelRow",
    "DeclarationState",
    "DeclarationStatus",
    "DsrSummary",
    "EntranceAccepted",
    "EntranceRejected",
    "FitOutcome",
    "FixedTrial",
    "FloorOutcome",
    "LedgerTally",
    "ReviewDefect",
    "ReviewReason",
    "ReviewResult",
    "ReviewRules",
    "ReviewRulesEntry",
    "ReviewRulesRef",
    "SwapRule",
    "check_entrance",
    "combine_result",
    "cost_model_fit",
    "evidence_floor",
    "ledger_tally",
    "review_ledger_defect",
    "review_line_digest",
    "rules_file_mismatch",
]

#: 審査台帳の行の形式版（D16 §10.2 の `schema_version`）。
ADOPTION_REVIEW_SCHEMA_VERSION: Final = 1

#: 証拠の最低線の注記（D15-6 (a) の文。D16 §4.2 の 5）。
EVIDENCE_NOTE: Final = "研究履歴の中の確認で、独立した確認ではない"

#: 審査ごとの乱数（16 進で 32 文字以上＝128 ビット以上。D16 §10.4）。
#: 試行台帳の `execution_nonce` と同じ形。
_NONCE: Final = re.compile(r"[0-9a-f]{32,}")
_NAME: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


# --- 値の語彙 ------------------------------------------------------------------


class ReviewResult(Enum):
    """審査の結果（D16 §9.1）。`DEFERRED` は研究ポリシーの「保留の印」（D13 §5.5）とは別物。"""

    PROCEED = "PROCEED"
    DO_NOT_PROCEED = "DO_NOT_PROCEED"
    DEFERRED = "DEFERRED"


#: 確定した結果（1 つの実行に 1 行まで。仮置き 12。D16 §9.3）。
FINAL_RESULTS: Final = frozenset({ReviewResult.PROCEED, ReviewResult.DO_NOT_PROCEED})


class ReviewReason(Enum):
    """理由の識別子（D16 §3.1・§4.2・§7.1・§8・§9.1・§11.1）。標準出力にはこれだけを出す。"""

    ENTRY_NOT_IN_LEDGER = "ENTRY_NOT_IN_LEDGER"
    ENTRY_NOT_FINISHED = "ENTRY_NOT_FINISHED"
    ENTRY_STATUS_NOT_COMPLETED = "ENTRY_STATUS_NOT_COMPLETED"
    ENTRY_PURPOSE_NOT_STANDARD = "ENTRY_PURPOSE_NOT_STANDARD"
    ENTRY_VERDICT_NOT_MEETS_STANDARD = "ENTRY_VERDICT_NOT_MEETS_STANDARD"
    ENTRY_POLICY_NOT_CURRENT = "ENTRY_POLICY_NOT_CURRENT"
    ENTRY_ALREADY_REVIEWED = "ENTRY_ALREADY_REVIEWED"
    ENTRY_RULES_VERSION_CHANGED = "ENTRY_RULES_VERSION_CHANGED"
    ARTIFACT_BINDING_MISMATCH = "ARTIFACT_BINDING_MISMATCH"
    EVIDENCE_FLOOR_NOT_MET = "EVIDENCE_FLOOR_NOT_MET"
    EVIDENCE_FLOOR_UNDETERMINED = "EVIDENCE_FLOOR_UNDETERMINED"
    COST_MODEL_NEEDS_CAPABILITY = "COST_MODEL_NEEDS_CAPABILITY"
    #: 適合の検査の `UNDETERMINED`（D16 §7.1・§9.1 の 2。理由の名前は R3 の仮置き）。
    COST_MODEL_UNDETERMINED = "COST_MODEL_UNDETERMINED"
    OUTSIDE_VIEWING_DECLARED = "OUTSIDE_VIEWING_DECLARED"
    DECLARATION_MISSING = "DECLARATION_MISSING"


#: 理由を記録する順（入口の順 → 証拠 → 適合 → 申告。重複なし）。
_REASON_ORDER: Final = {reason: index for index, reason in enumerate(ReviewReason)}


class FloorOutcome(Enum):
    """証拠の最低線の合否（D16 §4.2・§10.2）。"""

    MET = "MET"
    NOT_MET = "NOT_MET"
    UNDETERMINED = "UNDETERMINED"


class FitOutcome(Enum):
    """費用・約定モデルの適合の結果（D16 §7.1）。"""

    FITS = "FITS"
    NEEDS_CAPABILITY = "NEEDS_CAPABILITY"
    UNDETERMINED = "UNDETERMINED"


class CostModelRow(Enum):
    """D13 §8.2 の表の行（D16 §7.2）。値の名前は R3 の仮置き。"""

    BAR_RESOLUTION = "BAR_RESOLUTION"
    FIXED_SPREAD = "FIXED_SPREAD"
    SWAP_ROLLOVER = "SWAP_ROLLOVER"
    SINGLE_POSITION_MARKET_ENTRY = "SINGLE_POSITION_MARKET_ENTRY"
    CURRENCY_CONVERSION = "CURRENCY_CONVERSION"
    NON_LEARNING = "NON_LEARNING"


#: 宣言の形で常に「適合」とする行（仮置き 9。規則のファイルの `declared_fits`）。
DECLARED_ROWS: Final = (
    CostModelRow.BAR_RESOLUTION,
    CostModelRow.SINGLE_POSITION_MARKET_ENTRY,
    CostModelRow.NON_LEARNING,
)


class Capability(Enum):
    """`NEEDS_CAPABILITY` のときに要る能力の名前（D16 §7.2）。"""

    SPREAD_VARIATION = "SPREAD_VARIATION"
    SWAP_ROLLOVER = "SWAP_ROLLOVER"
    CURRENCY_CONVERSION = "CURRENCY_CONVERSION"


class SwapRule(Enum):
    """行 3（スワップ未計上）の規則（R1-5 (a)。日替わりをまたいだ完了取引が 1 件でもあれば）。"""

    ANY_CROSSING = "ANY_CROSSING"


class WindowAnchor(Enum):
    """行 2-1 の窓の起点（仮置き 8。週の開場だけ）。"""

    WEEKLY_OPEN = "WEEKLY_OPEN"


class DsrBasis(Enum):
    """割り引く値の区間（R1-3 (a)。最後の fold の選定区間）。"""

    LAST_FOLD_SELECTION = "LAST_FOLD_SELECTION"


class DeclarationStatus(Enum):
    """申告の有無（D16 §8。R1-8 (a)）。"""

    NOT_DECLARED = "NOT_DECLARED"
    DECLARED = "DECLARED"
    MISSING = "MISSING"


# --- 審査の規則（D16 §10.3）-------------------------------------------------------


def _require_int(value: object, label: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise KernelValueError(f"{label} must be an int >= {minimum}, got {value!r}")


def _require_positive_decimal(value: object, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise KernelValueError(f"{label} must be a positive finite Decimal, got {value!r}")


@dataclass(frozen=True, slots=True)
class ReviewRules:
    """審査の規則のファイルの値（D16 §10.3。値は 2026-10-30 の要決定の決定）。

    最低件数は研究ポリシーから読むので持たない（仮置き 4）。
    """

    rules_id: str
    version: int
    window_anchor: WindowAnchor
    window_length: timedelta
    max_concentration_ratio: Decimal
    max_fixed_cost_share: Decimal
    swap_rule: SwapRule
    require_account_currency: bool
    declared_fits: tuple[CostModelRow, ...]
    dsr_basis: DsrBasis
    dsr_min_trades: int
    dsr_max_flat_day_share: Decimal
    undetermined_result: ReviewResult

    def __post_init__(self) -> None:
        if not isinstance(self.rules_id, str) or not _NAME.fullmatch(self.rules_id):
            raise KernelValueError(f"ReviewRules.rules_id is not a name: {self.rules_id!r}")
        _require_int(self.version, "ReviewRules.version", minimum=1)
        if not isinstance(self.window_anchor, WindowAnchor):
            raise KernelValueError("ReviewRules.window_anchor must be a WindowAnchor")
        if not isinstance(self.window_length, timedelta) or self.window_length <= timedelta(0):
            raise KernelValueError("ReviewRules.window_length must be a positive timedelta")
        _require_positive_decimal(
            self.max_concentration_ratio, "ReviewRules.max_concentration_ratio"
        )
        _require_positive_decimal(self.max_fixed_cost_share, "ReviewRules.max_fixed_cost_share")
        if not isinstance(self.swap_rule, SwapRule):
            raise KernelValueError("ReviewRules.swap_rule must be a SwapRule")
        if not isinstance(self.require_account_currency, bool):
            raise KernelValueError("ReviewRules.require_account_currency must be a bool")
        if self.declared_fits != DECLARED_ROWS:
            raise KernelValueError(
                "ReviewRules.declared_fits must be exactly"
                f" {[row.value for row in DECLARED_ROWS]} (仮置き 9。D16 §7.2)"
            )
        if not isinstance(self.dsr_basis, DsrBasis):
            raise KernelValueError("ReviewRules.dsr_basis must be a DsrBasis")
        _require_int(self.dsr_min_trades, "ReviewRules.dsr_min_trades", minimum=0)
        if (
            not isinstance(self.dsr_max_flat_day_share, Decimal)
            or not self.dsr_max_flat_day_share.is_finite()
            or not decimal_from_int(0) <= self.dsr_max_flat_day_share <= decimal_from_int(1)
        ):
            raise KernelValueError("ReviewRules.dsr_max_flat_day_share must be in [0, 1]")
        if self.undetermined_result is not ReviewResult.DEFERRED:
            raise KernelValueError(
                "ReviewRules.undetermined_result must be DEFERRED (R1-4 (a)。D16 §9.1)"
            )


@dataclass(frozen=True, slots=True)
class ReviewRulesRef:
    """審査の規則の版の参照（審査台帳の行の `review_rules_ref`。D16 §10.2）。"""

    rules_id: str
    version: int
    digest: ContentDigest

    def __post_init__(self) -> None:
        if not isinstance(self.rules_id, str) or not _NAME.fullmatch(self.rules_id):
            raise KernelValueError(f"ReviewRulesRef.rules_id is not a name: {self.rules_id!r}")
        _require_int(self.version, "ReviewRulesRef.version", minimum=1)
        if not isinstance(self.digest, ContentDigest):
            raise KernelValueError("ReviewRulesRef.digest must be a ContentDigest")


@dataclass(frozen=True, slots=True)
class ReviewRulesEntry:
    """審査の規則の登録簿の要素（D16 §10.3。R1-10 (a)）。

    `effective_after` は登録の時点の試行台帳の末尾の行のダイジェスト（版 1 は `None`＝最初から
    効く）。
    """

    rules_id: str
    version: int
    digest: ContentDigest
    effective_after: ContentDigest | None

    def __post_init__(self) -> None:
        if not isinstance(self.rules_id, str) or not _NAME.fullmatch(self.rules_id):
            raise KernelValueError(f"ReviewRulesEntry.rules_id is not a name: {self.rules_id!r}")
        _require_int(self.version, "ReviewRulesEntry.version", minimum=1)
        if not isinstance(self.digest, ContentDigest):
            raise KernelValueError("ReviewRulesEntry.digest must be a ContentDigest")
        if self.effective_after is not None and not isinstance(self.effective_after, ContentDigest):
            raise KernelValueError("ReviewRulesEntry.effective_after must be a ContentDigest")

    @property
    def ref(self) -> ReviewRulesRef:
        return ReviewRulesRef(rules_id=self.rules_id, version=self.version, digest=self.digest)


# --- 審査台帳の行（D16 §10.2）--------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FixedTrial:
    """組の固定した試行（最後の fold で選ばれた試行。D16 §4.1。仮置き 2）。"""

    trial_index: int
    assignment_digest: ContentDigest

    def __post_init__(self) -> None:
        _require_int(self.trial_index, "FixedTrial.trial_index", minimum=0)
        if not isinstance(self.assignment_digest, ContentDigest):
            raise KernelValueError("FixedTrial.assignment_digest must be a ContentDigest")


@dataclass(frozen=True, slots=True)
class CostFitRow:
    """適合の検査の 1 行の結果（D16 §7.1・§7.2）。"""

    row: CostModelRow
    outcome: FitOutcome
    capabilities: tuple[Capability, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.row, CostModelRow):
            raise KernelValueError("CostFitRow.row must be a CostModelRow")
        if not isinstance(self.outcome, FitOutcome):
            raise KernelValueError("CostFitRow.outcome must be a FitOutcome")
        if not isinstance(self.capabilities, tuple) or not all(
            isinstance(item, Capability) for item in self.capabilities
        ):
            raise KernelValueError("CostFitRow.capabilities must be a tuple of Capability")
        if (self.outcome is FitOutcome.NEEDS_CAPABILITY) != bool(self.capabilities):
            raise KernelValueError("CostFitRow has capabilities exactly when it NEEDS_CAPABILITY")


@dataclass(frozen=True, slots=True)
class DsrSummary:
    """割り引いた指標の状態だけ（数値は数値の層。D16 §10.2 の `deflated_sharpe`）。

    `state` は `SHOWN` / `UNAVAILABLE`、`reasons` は値なしの理由、`marks` は印、`assumption_reasons`
    は「仮定に合わない」の理由（どれも識別子の文字列）。
    """

    state: str
    reasons: tuple[str, ...]
    marks: tuple[str, ...]
    assumption_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state not in ("SHOWN", "UNAVAILABLE"):
            raise KernelValueError("DsrSummary.state must be SHOWN or UNAVAILABLE")
        for label in ("reasons", "marks", "assumption_reasons"):
            value = getattr(self, label)
            if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
                raise KernelValueError(f"DsrSummary.{label} must be a tuple of str")
        if (self.state == "SHOWN") == bool(self.reasons):
            raise KernelValueError("DsrSummary has reasons exactly when it is UNAVAILABLE")


@dataclass(frozen=True, slots=True)
class LedgerTally:
    """台帳の累計（D16 §6.1 の L1〜L5 と、候補の開始の行より前だけの L3・L4。R1-9 (a)）。"""

    lineage_executions: int
    lineage_trials: int
    overlapping_executions: int
    overlapping_trials: int
    same_strategy_executions: int
    prior_overlapping_executions: int
    prior_overlapping_trials: int

    def __post_init__(self) -> None:
        for name in LedgerTally.__dataclass_fields__:
            _require_int(getattr(self, name), f"LedgerTally.{name}", minimum=0)


@dataclass(frozen=True, slots=True)
class DeclarationState:
    """申告の有無と申告ファイルのダイジェスト（D16 §8・§10.2 の `declaration`）。"""

    status: DeclarationStatus
    file_digest: ContentDigest | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, DeclarationStatus):
            raise KernelValueError("DeclarationState.status must be a DeclarationStatus")
        if (self.status is DeclarationStatus.MISSING) != (self.file_digest is None):
            raise KernelValueError("DeclarationState has a digest exactly when the file was read")
        if self.file_digest is not None and not isinstance(self.file_digest, ContentDigest):
            raise KernelValueError("DeclarationState.file_digest must be a ContentDigest")


@dataclass(frozen=True, slots=True)
class EvidenceFloorRecord:
    """証拠の最低線の合否（組ごと）と注記（D16 §10.2 の `evidence_floor`）。"""

    outcomes: Paired[FloorOutcome]
    note: str

    def __post_init__(self) -> None:
        if not isinstance(self.outcomes, Paired):
            raise KernelValueError("EvidenceFloorRecord.outcomes must be Paired[FloorOutcome]")
        self.outcomes.require(FloorOutcome, "EvidenceFloorRecord.outcomes")
        if self.note != EVIDENCE_NOTE:
            raise KernelValueError("EvidenceFloorRecord.note must be the D15-6 (a) note")


@dataclass(frozen=True, slots=True)
class AdoptionReviewEntry:
    """審査台帳の 1 行（D16 §10.2。数値を含まない層）。

    `started_line_digest`・`finished_line_digest` は試行台帳のその実行の開始・結末の行の
    ダイジェスト（`trial_ledger_lines`）、`trial_ledger_head` は審査で読んだ試行台帳の末尾の行の
    ダイジェスト。`fixed_trials` の値は固定できなかった組で `None`。記録時刻は入れない。
    """

    schema_version: int
    experiment_id: ExperimentId
    execution: int
    experiment_name: str
    experiment_version: int
    strategy_id: str
    review_nonce: str
    started_line_digest: ContentDigest
    finished_line_digest: ContentDigest
    trial_ledger_head: ContentDigest
    research_policy_ref: PolicyRef
    review_rules_ref: ReviewRulesRef
    fixed_trials: Paired[FixedTrial | None]
    evidence_floor: EvidenceFloorRecord
    cost_model_fit: Paired[tuple[CostFitRow, ...]]
    deflated_sharpe: Paired[DsrSummary]
    ledger_tally: LedgerTally
    declaration: DeclarationState
    result: ReviewResult
    reasons: tuple[ReviewReason, ...]
    numeric_record_digest: ContentDigest
    code_digest: CodeDigest
    lock_digest: LockDigest

    def __post_init__(self) -> None:
        if self.schema_version != ADOPTION_REVIEW_SCHEMA_VERSION or isinstance(
            self.schema_version, bool
        ):
            raise KernelValueError(
                f"AdoptionReviewEntry.schema_version must be {ADOPTION_REVIEW_SCHEMA_VERSION}"
            )
        if not isinstance(self.experiment_id, ExperimentId):
            raise KernelValueError("AdoptionReviewEntry.experiment_id must be an ExperimentId")
        _require_int(self.execution, "AdoptionReviewEntry.execution", minimum=1)
        _require_int(self.experiment_version, "AdoptionReviewEntry.experiment_version", minimum=1)
        for label in ("experiment_name", "strategy_id"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value:
                raise KernelValueError(f"AdoptionReviewEntry.{label} must be a non-empty str")
        if not isinstance(self.review_nonce, str) or not _NONCE.fullmatch(self.review_nonce):
            raise KernelValueError(
                "AdoptionReviewEntry.review_nonce must be at least 32 lowercase hex characters"
            )
        for label in (
            "started_line_digest",
            "finished_line_digest",
            "trial_ledger_head",
            "numeric_record_digest",
        ):
            if not isinstance(getattr(self, label), ContentDigest):
                raise KernelValueError(f"AdoptionReviewEntry.{label} must be a ContentDigest")
        if not isinstance(self.research_policy_ref, PolicyRef):
            raise KernelValueError("AdoptionReviewEntry.research_policy_ref must be a PolicyRef")
        if not isinstance(self.review_rules_ref, ReviewRulesRef):
            raise KernelValueError("AdoptionReviewEntry.review_rules_ref must be a ReviewRulesRef")
        if not isinstance(self.fixed_trials, Paired):
            raise KernelValueError("AdoptionReviewEntry.fixed_trials must be Paired")
        for variant, trial in self.fixed_trials.items():
            if trial is not None and not isinstance(trial, FixedTrial):
                raise KernelValueError(
                    f"AdoptionReviewEntry.fixed_trials.{variant.value} must be a FixedTrial"
                )
        if not isinstance(self.evidence_floor, EvidenceFloorRecord):
            raise KernelValueError(
                "AdoptionReviewEntry.evidence_floor must be an EvidenceFloorRecord"
            )
        if not isinstance(self.cost_model_fit, Paired):
            raise KernelValueError("AdoptionReviewEntry.cost_model_fit must be Paired")
        for variant, rows in self.cost_model_fit.items():
            if not isinstance(rows, tuple) or [item.row for item in rows] != list(CostModelRow):
                raise KernelValueError(
                    f"AdoptionReviewEntry.cost_model_fit.{variant.value} must list every row of"
                    " D13 §8.2 once in order"
                )
        if not isinstance(self.deflated_sharpe, Paired):
            raise KernelValueError("AdoptionReviewEntry.deflated_sharpe must be Paired")
        self.deflated_sharpe.require(DsrSummary, "AdoptionReviewEntry.deflated_sharpe")
        if not isinstance(self.ledger_tally, LedgerTally):
            raise KernelValueError("AdoptionReviewEntry.ledger_tally must be a LedgerTally")
        if not isinstance(self.declaration, DeclarationState):
            raise KernelValueError("AdoptionReviewEntry.declaration must be a DeclarationState")
        if not isinstance(self.result, ReviewResult):
            raise KernelValueError("AdoptionReviewEntry.result must be a ReviewResult")
        if not isinstance(self.reasons, tuple) or not all(
            isinstance(item, ReviewReason) for item in self.reasons
        ):
            raise KernelValueError("AdoptionReviewEntry.reasons must be a tuple of ReviewReason")
        if (self.result is ReviewResult.PROCEED) == bool(self.reasons):
            raise KernelValueError("AdoptionReviewEntry has reasons exactly when it is not PROCEED")
        if not isinstance(self.code_digest, CodeDigest) or not isinstance(
            self.lock_digest, LockDigest
        ):
            raise KernelValueError("AdoptionReviewEntry.code_digest / lock_digest have wrong types")

    @property
    def key(self) -> tuple[ExperimentId, int]:
        """審査した実行 `(experiment_id, execution)`。"""
        return (self.experiment_id, self.execution)


def review_line_digest(entry: AdoptionReviewEntry, prev: ContentDigest | None) -> ContentDigest:
    """審査台帳の行のダイジェスト `digest({entry, prev})`（試行台帳と同じ形。D09 §10.12.6）。"""
    return digest({"entry": entry, "prev": None if prev is None else prev.hex})


@dataclass(frozen=True, slots=True)
class AdoptionReviewLine:
    """審査台帳のファイルの 1 行: 行・直前の行のダイジェスト・この行のダイジェスト。"""

    entry: AdoptionReviewEntry
    prev: ContentDigest | None
    digest: ContentDigest

    def __post_init__(self) -> None:
        if not isinstance(self.entry, AdoptionReviewEntry):
            raise KernelValueError("AdoptionReviewLine.entry must be an AdoptionReviewEntry")
        if self.prev is not None and not isinstance(self.prev, ContentDigest):
            raise KernelValueError("AdoptionReviewLine.prev must be a ContentDigest or None")
        if not isinstance(self.digest, ContentDigest) or self.digest != review_line_digest(
            self.entry, self.prev
        ):
            raise KernelValueError("AdoptionReviewLine.digest must equal digest({entry, prev})")

    @classmethod
    def of(cls, entry: AdoptionReviewEntry, prev: ContentDigest | None) -> AdoptionReviewLine:
        return cls(entry=entry, prev=prev, digest=review_line_digest(entry, prev))


class ReviewDefect(Enum):
    """審査台帳を読めないとする理由（試行台帳の L0〜L4 と同じ形に、審査台帳の規則を足した）。"""

    FILE_MISSING = "FILE_MISSING"
    FILE_UNREADABLE = "FILE_UNREADABLE"
    LINE_CORRUPT = "LINE_CORRUPT"
    CHAIN_BROKEN = "CHAIN_BROKEN"
    ENTRY_INVALID = "ENTRY_INVALID"
    #: 同じ実行に確定した結果の行の後の行がある（仮置き 12。確定は 1 行まで）。
    REVIEW_AFTER_FINAL = "REVIEW_AFTER_FINAL"
    #: 同じ実行の行の規則の版が最初の行と違う（仮置き 12。やり直しは同じ規則の版だけ）。
    RULES_REF_CHANGED = "RULES_REF_CHANGED"


def review_ledger_defect(
    lines: Sequence[AdoptionReviewLine],
) -> tuple[ReviewDefect, int, str] | None:
    """審査台帳の行の列の検査（鎖・確定の後の行・規則の版の一致）。最初の食い違いを返す。"""
    previous: ContentDigest | None = None
    first_ref: dict[tuple[ExperimentId, int], ReviewRulesRef] = {}
    final: set[tuple[ExperimentId, int]] = set()
    for number, line in enumerate(lines, start=1):
        if not isinstance(line, AdoptionReviewLine):
            raise KernelValueError("review_ledger_defect requires AdoptionReviewLine values")
        if line.prev != previous:
            return (ReviewDefect.CHAIN_BROKEN, number, "prev is not the digest of the line before")
        previous = line.digest
        key = line.entry.key
        if key in final:
            return (
                ReviewDefect.REVIEW_AFTER_FINAL,
                number,
                f"({key[0]}, {key[1]}) already has a PROCEED / DO_NOT_PROCEED line",
            )
        ref = first_ref.setdefault(key, line.entry.review_rules_ref)
        if ref != line.entry.review_rules_ref:
            return (
                ReviewDefect.RULES_REF_CHANGED,
                number,
                f"({key[0]}, {key[1]}) is reviewed with another rules version than its first line",
            )
        if line.entry.result in FINAL_RESULTS:
            final.add(key)
    return None


def last_review_digest(lines: Sequence[AdoptionReviewLine]) -> ContentDigest | None:
    """最後の完全な行のダイジェスト（新しい行の `prev`）。"""
    return lines[-1].digest if lines else None


# --- 入口の検査（D16 §3.1）-----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntranceAccepted:
    """入口を通った実行（成果物を開いてよい。仮置き 13）。"""

    started: TrialLedgerLine
    finished: TrialLedgerLine
    rules_entry: ReviewRulesEntry
    prior_reviews: tuple[AdoptionReviewLine, ...]
    research_policy_ref: PolicyRef


@dataclass(frozen=True, slots=True)
class EntranceRejected:
    """入口の拒否（記録を書かない。終了コード 7。D15 の仮置き 7）。"""

    reason: ReviewReason


def _line_position(lines: Sequence[TrialLedgerLine], target: ContentDigest) -> int | None:
    return next((index for index, line in enumerate(lines) if line.digest == target), None)


def applicable_rules_entry(
    trial_lines: Sequence[TrialLedgerLine],
    rules_registry: Sequence[ReviewRulesEntry],
    started: TrialLedgerLine,
) -> ReviewRulesEntry:
    """初回の審査に当てる版（R1-10 (a)。D16 §3.1 の順 8・§9.3）。

    開始の行が、要素の「登録の時点の試行台帳の末尾の行」より後の位置にある版のうち最大のもの
    （`effective_after` が `None` の版は最初から効く）。効き始めの行が台帳に無い要素は登録簿の
    誤り（`KernelValueError`。読込の誤り、終了コード 2）。当てる版が無いときも同じ。
    """
    position = _line_position(trial_lines, started.digest)
    if position is None:  # pragma: no cover - 呼び出し側が台帳の行を渡す
        raise KernelValueError("the STARTED line is not in the trial ledger")
    candidates: list[ReviewRulesEntry] = []
    for entry in rules_registry:
        if entry.effective_after is None:
            candidates.append(entry)
            continue
        after = _line_position(trial_lines, entry.effective_after)
        if after is None:
            raise KernelValueError(
                f"the adoption review rules {entry.rules_id} v{entry.version} take effect after"
                f" the trial ledger line {entry.effective_after.hex}, which is not in the trial"
                " ledger (R1-10 (a))"
            )
        if position > after:
            candidates.append(entry)
    if not candidates:
        raise KernelValueError("no adoption review rules version applies (R1-10 (a))")
    return max(candidates, key=lambda item: item.version)


def check_entrance(
    *,
    trial_lines: Sequence[TrialLedgerLine],
    policy_registry: Sequence[RegistryEntry],
    review_lines: Sequence[AdoptionReviewLine],
    rules_registry: Sequence[ReviewRulesEntry],
    experiment_id: ExperimentId,
    execution: int,
) -> EntranceAccepted | EntranceRejected:
    """入口の検査の順 1〜8（規則のファイル本体のダイジェストの照合を除く。D16 §3.1）。

    最初に不合格になった検査の理由を返す。順 8 の規則のファイルの読込とダイジェストの照合は、
    当てる版が決まった後に呼び出し側が行い、`rules_file_mismatch` で確かめる。
    """
    started = started_line_of(trial_lines, experiment_id, execution)
    if started is None:
        return EntranceRejected(ReviewReason.ENTRY_NOT_IN_LEDGER)
    finished = finished_line_of(trial_lines, experiment_id, execution)
    if finished is None:
        return EntranceRejected(ReviewReason.ENTRY_NOT_FINISHED)
    entry = finished.entry
    if entry.status is not ExperimentStatus.COMPLETED:
        return EntranceRejected(ReviewReason.ENTRY_STATUS_NOT_COMPLETED)
    if started.entry.purpose is not StandardPurpose.STANDARD:
        return EntranceRejected(ReviewReason.ENTRY_PURPOSE_NOT_STANDARD)
    if entry.verdict is not SearchVerdict.MEETS_STANDARD:
        return EntranceRejected(ReviewReason.ENTRY_VERDICT_NOT_MEETS_STANDARD)
    refs = {basis.research_policy_ref for _, basis in started.entry.basis.items()}
    if len(refs) != 1:
        return EntranceRejected(ReviewReason.ENTRY_POLICY_NOT_CURRENT)
    (policy_ref,) = refs
    current = current_standard_version(policy_registry, policy_ref.policy_id)
    registered = next(
        (
            item
            for item in policy_registry
            if (item.policy_id, item.version) == (policy_ref.policy_id, policy_ref.version)
        ),
        None,
    )
    if (
        current is None
        or policy_ref.version != current
        or registered is None
        or registered.digest != policy_ref.digest
    ):
        return EntranceRejected(ReviewReason.ENTRY_POLICY_NOT_CURRENT)
    mine = tuple(line for line in review_lines if line.entry.key == (experiment_id, execution))
    if any(line.entry.result in FINAL_RESULTS for line in mine):
        return EntranceRejected(ReviewReason.ENTRY_ALREADY_REVIEWED)
    if mine:
        first = mine[0].entry.review_rules_ref
        chosen = next(
            (
                item
                for item in rules_registry
                if (item.rules_id, item.version) == (first.rules_id, first.version)
            ),
            None,
        )
        if chosen is None or chosen.digest != first.digest:
            return EntranceRejected(ReviewReason.ENTRY_RULES_VERSION_CHANGED)
    else:
        chosen = applicable_rules_entry(trial_lines, rules_registry, started)
    return EntranceAccepted(
        started=started,
        finished=finished,
        rules_entry=chosen,
        prior_reviews=mine,
        research_policy_ref=policy_ref,
    )


def rules_file_mismatch(
    entry: ReviewRulesEntry, rules: ReviewRules, digest_of_file: ContentDigest
) -> bool:
    """順 8 の後半: 当てる版のファイルが登録簿の要素と一致しないか（一致しなければ `True`）。"""
    return (rules.rules_id, rules.version) != (
        entry.rules_id,
        entry.version,
    ) or digest_of_file != entry.digest


# --- 台帳の累計（D16 §6）------------------------------------------------------------


def _root_of(lines: Sequence[TrialLedgerLine], line: TrialLedgerLine) -> tuple[ExperimentId, int]:
    """系譜の根 `(experiment_id, execution)`（`derived_from` を根までたどる。仮置き 7）。"""
    current = line
    seen: set[tuple[ExperimentId, int]] = set()
    while True:
        key = (current.entry.experiment_id, current.entry.execution)
        if key in seen:  # pragma: no cover - 台帳の行は前の行だけを指す
            raise KernelValueError("the lineage of the trial ledger has a cycle")
        seen.add(key)
        lineage = current.entry.lineage
        source = None if lineage is None else lineage.derived_from
        if source is None:
            return key
        origin = started_line_of(lines, source.experiment_id, source.execution)
        if origin is None:
            # 台帳に無い元を指す行（書き込みの時点で拒否される。D13 §7.1）は、その行を根とする。
            return key
        current = origin


def ledger_tally(lines: Sequence[TrialLedgerLine], started: TrialLedgerLine) -> LedgerTally:
    """台帳の累計 L1〜L5 と、候補の開始の行より前だけの L3・L4（D16 §6.1・§6.2。R1-9 (a)）。

    数えるのは開始の行（開始の行だけの実行も数える）。重なりは `Interval.overlaps`（端が接する
    だけは重ならない）。L1・L2 は系譜の根から派生した全実行（兄弟の枝を含む。仮置き 7）。
    """
    position = _line_position(lines, started.digest)
    if position is None or started.entry.event is not TrialLedgerEvent.STARTED:
        raise KernelValueError("ledger_tally requires the STARTED line of the trial ledger")
    mine = started.entry
    root = _root_of(lines, started)
    lineage_executions = lineage_trials = 0
    overlapping = overlapping_trials = 0
    prior_overlapping = prior_trials = 0
    same_strategy = 0
    for index, line in enumerate(lines):
        entry = line.entry
        if entry.event is not TrialLedgerEvent.STARTED:
            continue
        if _root_of(lines, line) == root:
            lineage_executions += 1
            lineage_trials += entry.trial_count
        if any(
            theirs.overlaps(ours)
            for theirs in entry.validation_intervals
            for ours in mine.validation_intervals
        ):
            overlapping += 1
            overlapping_trials += entry.trial_count
            if index < position:
                prior_overlapping += 1
                prior_trials += entry.trial_count
        if entry.strategy_id == mine.strategy_id:
            same_strategy += 1
    return LedgerTally(
        lineage_executions=lineage_executions,
        lineage_trials=lineage_trials,
        overlapping_executions=overlapping,
        overlapping_trials=overlapping_trials,
        same_strategy_executions=same_strategy,
        prior_overlapping_executions=prior_overlapping,
        prior_overlapping_trials=prior_trials,
    )


# --- 証拠の最低線（D16 §4）--------------------------------------------------------


def evidence_floor(counts: Sequence[int | None], minimum: int | None) -> FloorOutcome:
    """固定した試行の各 fold の検証区間の完了取引の件数の合計と最低件数の比較（D16 §4.2）。

    どれかの fold の件数が `None`（run・評価・事後検査の失敗）か、最低件数が決まらなければ
    `UNDETERMINED`。fold が 1 つも無いときも `UNDETERMINED`。
    """
    if minimum is None or not counts or any(item is None for item in counts):
        return FloorOutcome.UNDETERMINED
    total = sum(item for item in counts if item is not None)
    return FloorOutcome.MET if total >= minimum else FloorOutcome.NOT_MET


# --- 費用・約定モデルの適合（D16 §7）---------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostFitMeasures:
    """適合の検査の印の値（数値の層にだけ記録する。D16 §10.4）。値なしは `None`。"""

    fill_count: int | None
    window_fill_count: int | None
    fill_share: Decimal | None
    time_share: Decimal | None
    concentration_ratio: Decimal | None
    cost_total: Decimal | None
    move_total: Decimal | None
    fixed_cost_share: Decimal | None
    crossing_trades: int | None
    crossing_share: Decimal | None
    holding_median_trading_days: Decimal | None
    settlement_currency: str
    account_currency: str


@dataclass(frozen=True, slots=True)
class CostFitResult:
    """適合の検査の結果（行ごと）と印の値。"""

    rows: tuple[CostFitRow, ...]
    measures: CostFitMeasures


def _fits(row: CostModelRow) -> CostFitRow:
    return CostFitRow(row=row, outcome=FitOutcome.FITS, capabilities=())


def _row(row: CostModelRow, outcome: FitOutcome, capability: Capability) -> CostFitRow:
    if outcome is FitOutcome.NEEDS_CAPABILITY:
        return CostFitRow(row=row, outcome=outcome, capabilities=(capability,))
    return CostFitRow(row=row, outcome=outcome, capabilities=())


def _overlap(first: Interval, second: Interval) -> timedelta:
    start = max(first.start.value, second.start.value)
    end = min(first.end.value, second.end.value)
    return max(end - start, timedelta(0))


def _in_window(calendar: TradingCalendar, moment: UtcTime, length: timedelta) -> bool:
    session = calendar.weekly_session_at(moment)
    if session is None:
        return False
    return session.start.value <= moment.value < session.start.value + length


def _share(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    with localcontext(kernel_context()):
        return decimal_from_int(numerator) / decimal_from_int(denominator)


def _session_shares(
    calendar: TradingCalendar, intervals: Sequence[Interval], length: timedelta
) -> tuple[timedelta, timedelta]:
    """検証区間の取引時間の合計と、そのうち窓（週の開場から `length`）が占める時間。"""
    total = timedelta(0)
    window = timedelta(0)
    for interval in intervals:
        for part in calendar.sessions(interval):
            total += part.duration
            hull = calendar.weekly_session_at(part.start)
            if hull is None:  # pragma: no cover - 取引時間は週の開場区間の中
                continue
            opening = Interval(start=hull.start, end=hull.start + length)
            window += _overlap(part, opening)
    return total, window


def _spread_rows(
    rules: ReviewRules,
    trades: Sequence[TradeRecord],
    intervals: Sequence[Interval],
    calendar: TradingCalendar,
) -> tuple[FitOutcome, dict[str, object]]:
    """行 2（固定のスプレッド）: 2-1 時間帯の集中と 2-2 利幅に対する固定の費用の割合。"""
    length = rules.window_length
    fills = [moment for trade in trades for moment in (trade.entry_at.time, trade.exit_at.time)]
    in_window = sum(1 for moment in fills if _in_window(calendar, moment, length))
    total_time, window_time = _session_shares(calendar, intervals, length)
    fill_share = _share(in_window, len(fills))
    time_share: Decimal | None = None
    ratio: Decimal | None = None
    with localcontext(kernel_context()):
        if total_time > timedelta(0):
            time_share = decimal_from_int(
                int(window_time.total_seconds() * 1_000_000)
            ) / decimal_from_int(int(total_time.total_seconds() * 1_000_000))
        if fill_share is not None and time_share is not None and time_share > 0:
            ratio = fill_share / time_share
        concentration = (
            FitOutcome.UNDETERMINED
            if ratio is None
            else FitOutcome.NEEDS_CAPABILITY
            if ratio > rules.max_concentration_ratio
            else FitOutcome.FITS
        )
        cost = decimal_from_int(0)
        move = decimal_from_int(0)
        missing = False
        for trade in trades:
            quantity = trade.quantity.units
            for spread in (trade.entry_spread_in_price, trade.close_spread_in_price):
                # スプレッドの費用の値なしは区分が当たらない約定（D16 §7.3）なので 0 として数える。
                if spread is not None:
                    cost = cost + spread.amount / quantity
            for slippage in (trade.entry_slippage_in_price, trade.close_slippage_in_price):
                # 滑りの費用はどの約定にも記録される区分なので、値なしは記録の欠け（0 にしない）。
                if slippage is None:
                    missing = True
                else:
                    cost = cost + slippage.amount / quantity
            move = move + abs(trade.exit_price.value - trade.entry_price.value)
        share = None if missing or move == 0 else cost / move
        fixed = (
            FitOutcome.UNDETERMINED
            if share is None
            else FitOutcome.NEEDS_CAPABILITY
            if share > rules.max_fixed_cost_share
            else FitOutcome.FITS
        )
    if FitOutcome.NEEDS_CAPABILITY in (concentration, fixed):
        outcome = FitOutcome.NEEDS_CAPABILITY
    elif FitOutcome.UNDETERMINED in (concentration, fixed):
        outcome = FitOutcome.UNDETERMINED
    else:
        outcome = FitOutcome.FITS
    measures: dict[str, object] = {
        "fill_count": len(fills),
        "window_fill_count": in_window,
        "fill_share": fill_share,
        "time_share": time_share,
        "concentration_ratio": ratio,
        "cost_total": None if missing else cost,
        "move_total": move,
        "fixed_cost_share": share,
    }
    return outcome, measures


def _median_count(counts: Sequence[int]) -> Decimal | None:
    """件数の中央値（偶数個なら中央の 2 つの平均。診断の中央値と同じ取り方）。空なら `None`。"""
    if not counts:
        return None
    ordered = sorted(counts)
    middle = len(ordered) // 2
    with localcontext(kernel_context()):
        if len(ordered) % 2:
            return decimal_from_int(ordered[middle])
        return decimal_from_int(ordered[middle - 1] + ordered[middle]) / decimal_from_int(2)


def _swap_row(
    trades: Sequence[TradeRecord], intervals: Sequence[Interval], calendar: TradingCalendar
) -> tuple[FitOutcome, dict[str, object]]:
    """行 3（スワップ未計上）: 日替わり（取引日の終わり）をまたいだ完了取引（R1-5 (a)）。

    保有時間の中央値は取引日の数（D16 §7.4。2026-11-01 決定）: 完了取引ごとに `(入場, 決済]` に
    入る取引日の終わり（D07 §5.5 の `N` と同じ数え方。`trading_day_ends`）を数え、その中央値。
    取引日の終わりが決まらないカレンダーでは値なし。
    """
    ends: set[UtcTime] = set()
    for interval in intervals:
        found = trading_day_ends(calendar, interval)
        if found is None:
            return FitOutcome.UNDETERMINED, {
                "crossing_trades": None,
                "crossing_share": None,
                "holding_median_trading_days": None,
            }
        ends.update(found)
    ordered = sorted(ends)
    counts = [
        bisect_right(ordered, trade.exit_at.time) - bisect_right(ordered, trade.entry_at.time)
        for trade in trades
    ]
    crossing = sum(1 for count in counts if count >= 1)
    outcome = FitOutcome.NEEDS_CAPABILITY if crossing >= 1 else FitOutcome.FITS
    return outcome, {
        "crossing_trades": crossing,
        "crossing_share": _share(crossing, len(trades)),
        "holding_median_trading_days": _median_count(counts),
    }


def cost_model_fit(
    *,
    rules: ReviewRules,
    trades: Sequence[TradeRecord] | None,
    intervals: Sequence[Interval],
    calendar: TradingCalendar | None,
    symbol: Symbol,
    account_currency: CurrencyCode,
) -> CostFitResult:
    """1 つの組の適合の検査（D16 §7.2〜§7.4）。

    `trades` は固定した試行の 5 本の run の完了取引（どれかの run が失敗していれば `None`）。
    `intervals` は 5 つの検証区間、`calendar` は記録票の取引カレンダー（読めなければ `None`）。
    行 1・4・6 は宣言の形で常に `FITS`（仮置き 9）。行 5 は銘柄の決済通貨と口座の通貨。
    """
    if not isinstance(rules, ReviewRules):
        raise KernelValueError("cost_model_fit requires ReviewRules")
    settlement = symbol.quote
    conversion = (
        FitOutcome.FITS
        if not rules.require_account_currency or settlement == account_currency
        else FitOutcome.NEEDS_CAPABILITY
    )
    if trades is None or calendar is None:
        spread = swap = FitOutcome.UNDETERMINED
        spread_values: dict[str, object] = {}
        swap_values: dict[str, object] = {}
    else:
        spread, spread_values = _spread_rows(rules, trades, intervals, calendar)
        swap, swap_values = _swap_row(trades, intervals, calendar)
    rows = (
        _fits(CostModelRow.BAR_RESOLUTION),
        _row(CostModelRow.FIXED_SPREAD, spread, Capability.SPREAD_VARIATION),
        _row(CostModelRow.SWAP_ROLLOVER, swap, Capability.SWAP_ROLLOVER),
        _fits(CostModelRow.SINGLE_POSITION_MARKET_ENTRY),
        _row(CostModelRow.CURRENCY_CONVERSION, conversion, Capability.CURRENCY_CONVERSION),
        _fits(CostModelRow.NON_LEARNING),
    )

    def value[T](values: dict[str, object], key: str, kind: type[T]) -> T | None:
        item = values.get(key)
        return item if isinstance(item, kind) else None

    measures = CostFitMeasures(
        fill_count=value(spread_values, "fill_count", int),
        window_fill_count=value(spread_values, "window_fill_count", int),
        fill_share=value(spread_values, "fill_share", Decimal),
        time_share=value(spread_values, "time_share", Decimal),
        concentration_ratio=value(spread_values, "concentration_ratio", Decimal),
        cost_total=value(spread_values, "cost_total", Decimal),
        move_total=value(spread_values, "move_total", Decimal),
        fixed_cost_share=value(spread_values, "fixed_cost_share", Decimal),
        crossing_trades=value(swap_values, "crossing_trades", int),
        crossing_share=value(swap_values, "crossing_share", Decimal),
        holding_median_trading_days=value(swap_values, "holding_median_trading_days", Decimal),
        settlement_currency=settlement.code,
        account_currency=account_currency.code,
    )
    return CostFitResult(rows=rows, measures=measures)


# --- 結果の合わせ方（D16 §9.1）---------------------------------------------------------


def combine_result(
    *,
    floors: Paired[FloorOutcome],
    fits: Paired[tuple[CostFitRow, ...]],
    declaration: DeclarationStatus,
) -> tuple[ReviewResult, tuple[ReviewReason, ...]]:
    """結果の合わせ方（D16 §9.1。R1-4 (a)）。当てはまる理由をすべて返す。

    1. 証拠の最低線の不合格・適合の検査の `NEEDS_CAPABILITY`・申告ありのどれかで **進めない**。
    2. 1 に当たらず、証拠の最低線の判定できない・適合の検査の `UNDETERMINED`・申告ファイルの欠けの
       どれかで **保留**。
    3. どれにも当たらなければ **進める**。

    割り引いた指標と台帳の累計は使わない（表示だけ）。
    """
    reasons: set[ReviewReason] = set()
    blocking: set[ReviewReason] = set()
    for variant in VARIANTS:
        floor = floors.of(variant)
        if floor is FloorOutcome.NOT_MET:
            blocking.add(ReviewReason.EVIDENCE_FLOOR_NOT_MET)
        elif floor is FloorOutcome.UNDETERMINED:
            reasons.add(ReviewReason.EVIDENCE_FLOOR_UNDETERMINED)
        for row in fits.of(variant):
            if row.outcome is FitOutcome.NEEDS_CAPABILITY:
                blocking.add(ReviewReason.COST_MODEL_NEEDS_CAPABILITY)
            elif row.outcome is FitOutcome.UNDETERMINED:
                reasons.add(ReviewReason.COST_MODEL_UNDETERMINED)
    if declaration is DeclarationStatus.DECLARED:
        blocking.add(ReviewReason.OUTSIDE_VIEWING_DECLARED)
    elif declaration is DeclarationStatus.MISSING:
        reasons.add(ReviewReason.DECLARATION_MISSING)
    everything = tuple(sorted(reasons | blocking, key=lambda item: _REASON_ORDER[item]))
    if blocking:
        return ReviewResult.DO_NOT_PROCEED, everything
    if reasons:
        return ReviewResult.DEFERRED, everything
    return ReviewResult.PROCEED, ()


def needed_capabilities(fits: Paired[tuple[CostFitRow, ...]]) -> tuple[Capability, ...]:
    """両組の `NEEDS_CAPABILITY` の行が要る能力（重複を除き、宣言の順）。"""
    found = {item for _, rows in fits.items() for row in rows for item in row.capabilities}
    return tuple(item for item in Capability if item in found)


def variants_of[T](build: dict[Variant, T]) -> Paired[T]:
    """組ごとの辞書から `Paired`（両組がそろっていなければ構造エラー）。"""
    if set(build) != set(VARIANTS):
        raise KernelValueError("both variants are required")
    return Paired(standard=build[Variant.STANDARD], candidate=build[Variant.CANDIDATE])
