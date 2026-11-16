"""最終検証の手順の規則（D09 v0.5 §9.10）: 申請の状態・窓と消費の対象・最終評価の許可・判定。

本モジュールは I/O を持たない純粋な規則だけを置く。git・ファイル・run は `app.final_validation`
（結線）と関所（`evaluation.application.holdout_gate`）が行う。

- **申請の状態**（D09 §9.10.6 の状態表）: B 進行中 → D 最終検証済み → E 消費の後の失敗 →
  F 取り下げ済み → G 申告で使用済み → C 消費済み・記録なし → A 申請前、の順に当てはまるかを見て、
  最初に当てはまったもの（`derive_application_state`）。
- **窓と消費の対象**（D09 §9.10.3。要決定 R2-1 (a)・R2-2 (a)・R2-9 (a)、仮置き R2-9・R2-10・
  R2-12）: `derive_final_window`。
- **最終評価の許可**（D09 §9.10.2 の段 3。要決定 R2-3 (a)）: `FinalEvaluationPermit`。
- **判定**（D09 §9.10.11。要決定 R2-7 (a)）: `judge_final_variant`・`combine_final`。
- **研究ポリシー第 5 版の条件の当て方**（D09 §9.10.16。決定 212〜219・220）: 版 5 を指す候補だけ、
  採点区間を終わりから 364 日ずつに区切った区間の損失の床（`final_intervals`・
  `interval_floor_results`。決定 212）と、run の完了取引と run の終わりの仮決済の集合に当てる
  `PROFIT_FACTOR`（`search.profit_factor_result`。決定 214）を足す。損失の集中・近傍の損失の床・
  試行数で割り引いた確率は当てず（決定 213・215・216）、結果を作らずに名前の一覧
  （`RESEARCH_ONLY_CONDITIONS`）だけを判定に添える。局面は扱わない（決定 217）。版 4 以下の候補の
  判定は変えない。前向き検証の判定（決定 218）はこの関数を使える形にしてあるが、前向き検証の側の
  結線は前向き検証の申請のコマンドの実装 PR（D09 §9.11.13 の F-6）で行う。

本モジュールを作るために読まなかったもの: 戦略の成績・取引表・価格・2024 年以降のデータ・
旧データの CSV。数は件数・日数・時刻だけを扱う。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, localcontext
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import Money, kernel_context
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import (
    MetricId,
    MetricUnavailableReason,
    MetricValue,
    ratio_of,
)
from odyssey_fx.evaluation.domain.path import PathTradeTotals
from odyssey_fx.evaluation.domain.search import (
    NET_RETURN_RATE_METRICS,
    CombinedVerdict,
    Comparator,
    ConditionOutcome,
    ConditionResult,
    ConditionScope,
    EvaluationStandard,
    EvidenceAxis,
    FrequencyClass,
    MetricCondition,
    ProfitabilityAxis,
    SearchVerdict,
    StandardPurpose,
    ValidationRuleV5,
    hold_mark_of,
    path_condition_results,
    path_condition_results_v5,
)
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant
from odyssey_fx.marketdata.domain.access import (
    OANDA_SEALED_CLASSES,
    SEALED_RESEARCH_UNTIL,
    HoldoutState,
    sealed_partition_period,
)
from odyssey_fx.marketdata.domain.consumption_log import ConsumptionCandidate
from odyssey_fx.marketdata.domain.final_grant import FinalReadGrant, GrantedRead
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = [
    "CONVERSION_TIMEFRAME",
    "FINAL_INTERVAL_DAYS",
    "RESEARCH_ONLY_CONDITIONS",
    "WINDOW_START",
    "ApplicationState",
    "CandidateKey",
    "FinalEvaluationPermit",
    "FinalInterval",
    "FinalLedgerResult",
    "FinalLossSideMaterial",
    "FinalReason",
    "FinalRunSummary",
    "FinalVariantOutcome",
    "FinalWindow",
    "IntervalFloorResult",
    "PredictedRun",
    "SealedPartitionFact",
    "WindowRefusal",
    "combine_final",
    "derive_application_state",
    "derive_final_window",
    "final_intervals",
    "interval_floor_results",
    "judge_final_variant",
]

#: 最終検証の窓の始まり（D15-16 (a)、ADR-0038 第5項の 1。研究履歴の境界と同じ）。
WINDOW_START: Final = SEALED_RESEARCH_UNTIL

#: 換算の脚の時間足（D06 v1.20 案 §19.4、決定 (18) (a): 1 分足）。換算用の読み取りの対象は、
#: 換算の脚の通貨ペアのこの時間足の bid の系列の封印の partition（D06 §19.14）。
CONVERSION_TIMEFRAME: Final = "1m"


class FinalReason(Enum):
    """申請の拒否・関所の拒否・消費の後の失敗の理由の識別子（D09 §9.10。画面と台帳に出す）。

    D09 が名前を決めたもの（`ALREADY_VALIDATED` など）に、実装 PR が足したもの（R4 の仮置き。
    `NO_USABLE_WINDOW` など）を加えた。終了コードへの対応は `app.final_validation` が持つ。
    """

    # 申請の拒否（終了コード 7）
    ALREADY_VALIDATED = "ALREADY_VALIDATED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    WITHDRAWN = "WITHDRAWN"
    #: 状態 G（決定 150 の前に書かれた `DECLARED_USED` の行がある候補）の拒否の理由。申告は人間の
    #: 決定 150「申告を無くす」（ADR-0045）で外したので、新しい申請ではこの理由の行を書かない。
    OUTSIDE_VIEWING_DECLARED = "OUTSIDE_VIEWING_DECLARED"
    NOT_MEETS_STANDARD = "NOT_MEETS_STANDARD"
    NOT_CURRENT_POLICY_VERSION = "NOT_CURRENT_POLICY_VERSION"
    NO_FINAL_HOLDOUT = "NO_FINAL_HOLDOUT"
    LEDGER_NOT_ON_ORIGIN = "LEDGER_NOT_ON_ORIGIN"
    NOT_PROCEEDED_BY_REVIEW = "NOT_PROCEEDED_BY_REVIEW"
    NOT_FIXED = "NOT_FIXED"
    EARLIER_CANDIDATE_PENDING = "EARLIER_CANDIDATE_PENDING"
    WINDOW_HAS_MISSING_WEEK = "WINDOW_HAS_MISSING_WEEK"
    DUPLICATE_PARTITION_MISMATCH = "DUPLICATE_PARTITION_MISMATCH"
    WINDOW_SHORTER_THAN_L = "WINDOW_SHORTER_THAN_L"
    NO_USABLE_WINDOW = "NO_USABLE_WINDOW"
    FINAL_HOLDOUT_NOT_COVERING = "FINAL_HOLDOUT_NOT_COVERING"
    DATA_CAPABILITY_NOT_MET = "DATA_CAPABILITY_NOT_MET"
    #: 換算用の読み取りの対象（USDJPY）と同じ週の執行の通貨ペアの partition の snapshot が、
    #: 組・時刻の規則の表・取得の回・カレンダー・集約規則の版・承認のどれかで食い違う（D06 v1.20 案
    #: §19.14、仮置き C1-14）。
    CONVERSION_PARTITION_MISMATCH = "CONVERSION_PARTITION_MISMATCH"
    RECOMPUTATION_MISMATCH = "RECOMPUTATION_MISMATCH"
    P2F_NOT_PASSED = "P2F_NOT_PASSED"
    OUTPUT_NOT_EMPTY = "OUTPUT_NOT_EMPTY"
    NOT_AWAITING_RECORD = "NOT_AWAITING_RECORD"
    NOT_WITHDRAWABLE = "NOT_WITHDRAWABLE"
    #: R4a の本番の結線が段 2 の最初で止めた理由（R4b で本番の run を結線した。今は出さない）。
    FINAL_RUN_UNAVAILABLE = "FINAL_RUN_UNAVAILABLE"
    # 関所の拒否（終了コード 8）
    CONSUMED_WITHOUT_RECORD = "CONSUMED_WITHOUT_RECORD"
    RESUME_NOT_ALLOWED = "RESUME_NOT_ALLOWED"
    ORIGIN_NOT_CONFIRMED = "ORIGIN_NOT_CONFIRMED"
    CONSUMPTION_LOG_BROKEN = "CONSUMPTION_LOG_BROKEN"
    NOT_SEALED = "NOT_SEALED"
    STATE_DIFFERS_ACROSS_SNAPSHOTS = "STATE_DIFFERS_ACROSS_SNAPSHOTS"
    PUSH_REJECTED = "PUSH_REJECTED"
    # 消費の後の失敗（終了コード 9）
    RECORD_NOT_ON_ORIGIN = "RECORD_NOT_ON_ORIGIN"
    PERMIT_RECORD_FAILED = "PERMIT_RECORD_FAILED"
    FINAL_RUN_FAILED = "FINAL_RUN_FAILED"
    P4F_NOT_PASSED = "P4F_NOT_PASSED"
    P5_NOT_PASSED = "P5_NOT_PASSED"
    CLOSED_AS_FAILED = "CLOSED_AS_FAILED"


class FinalLedgerResult(Enum):
    """最終検証の台帳の行の結果（D09 §9.10.5）。"""

    COMPLETED = "COMPLETED"
    FAILED_AFTER_CONSUMPTION = "FAILED_AFTER_CONSUMPTION"
    WITHDRAWN = "WITHDRAWN"
    #: 申告「使った」の行（D09 §9.10.10 の 6）。決定 150（ADR-0045）の後は書かない。決定 150 の
    #: 前に書かれた行を読むためだけに残す（申告の撤去の PR 4b の仮置き。読んだ行は状態 G と順番の
    #: 「終わった」に従来どおり使う）。
    DECLARED_USED = "DECLARED_USED"


class ApplicationState(Enum):
    """1 回の申請の状態（D09 §9.10.6 の状態表。F・G は表の下の箇条）。"""

    A_NOT_APPLIED = "A"
    B_IN_PROGRESS = "B"
    C_CONSUMED_WITHOUT_RECORD = "C"
    D_VALIDATED = "D"
    E_FAILED_AFTER_CONSUMPTION = "E"
    F_WITHDRAWN = "F"
    G_DECLARED_USED = "G"


def derive_application_state(
    *,
    lock: bool,
    record: bool,
    results: frozenset[FinalLedgerResult],
    declared_used_for_experiment: bool,
    consumed_on_origin: bool,
) -> ApplicationState:
    """申請の状態を導く（D09 §9.10.6。順 B → D → E → F → G → C → A）。

    `record` は手元の最終検証の記録、`results` は作業木か origin の既定ブランチ上の台帳の、この
    候補の行の結果、`declared_used_for_experiment` は同じ `experiment_id` のどれかの実行の
    `DECLARED_USED` の行（D09 §9.10.10 の 6・7 の (2)。仮置き R2-29 の (2)）、`consumed_on_origin`
    はこの候補の消費の行が origin の既定ブランチ上にあること。
    """
    if lock:
        return ApplicationState.B_IN_PROGRESS
    if record or FinalLedgerResult.COMPLETED in results:
        return ApplicationState.D_VALIDATED
    if FinalLedgerResult.FAILED_AFTER_CONSUMPTION in results:
        return ApplicationState.E_FAILED_AFTER_CONSUMPTION
    if FinalLedgerResult.WITHDRAWN in results:
        return ApplicationState.F_WITHDRAWN
    if FinalLedgerResult.DECLARED_USED in results or declared_used_for_experiment:
        return ApplicationState.G_DECLARED_USED
    if consumed_on_origin:
        return ApplicationState.C_CONSUMED_WITHOUT_RECORD
    return ApplicationState.A_NOT_APPLIED


@dataclass(frozen=True, slots=True)
class CandidateKey:
    """候補（最終検証を申請する探索の実験の 1 回の実行。D09 §9.10.1）。"""

    experiment_id: str
    execution: int
    experiment_name: str
    experiment_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id:
            raise KernelValueError("CandidateKey.experiment_id must be a non-empty str")
        if not isinstance(self.experiment_name, str) or not self.experiment_name:
            raise KernelValueError("CandidateKey.experiment_name must be a non-empty str")
        for name in ("execution", "experiment_version"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise KernelValueError(f"CandidateKey.{name} must be a positive int")

    def consumption(self) -> ConsumptionCandidate:
        """消費の行の `candidate`（値は文字列。D09 §9.10.4）。"""
        return ConsumptionCandidate(
            experiment_id=self.experiment_id,
            execution=str(self.execution),
            experiment_name=self.experiment_name,
            experiment_version=str(self.experiment_version),
        )

    def matches(self, candidate: ConsumptionCandidate | None) -> bool:
        return candidate is not None and (candidate.experiment_id, candidate.execution) == (
            self.experiment_id,
            str(self.execution),
        )


# --- 窓と消費の対象（D09 §9.10.3）-------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SealedPartitionFact:
    """承認済みの OANDA MT5 の経路の snapshot の封印の区分の partition 1 つ（価格を含まない）。

    `state` は origin の既定ブランチ上の消費の記録から導いた状態、`digest` は manifest の記録の
    内容のダイジェスト（16 進）、`approved_at` は snapshot の承認の時刻（重複のときに読む方を
    決める。仮置き R2-9）。
    """

    snapshot_id: str
    partition_id: PartitionId
    variant: Variant
    digest: str
    approved_at: UtcTime
    state: HoldoutState

    def __post_init__(self) -> None:
        if not isinstance(self.partition_id, PartitionId):
            raise KernelValueError("SealedPartitionFact.partition_id must be a PartitionId")
        if self.partition_id.access_class not in OANDA_SEALED_CLASSES:
            raise KernelValueError("SealedPartitionFact holds the OANDA MT5 sealed classes only")
        if not isinstance(self.variant, Variant) or not isinstance(self.state, HoldoutState):
            raise KernelValueError("SealedPartitionFact.variant / state have the wrong type")
        if not isinstance(self.approved_at, UtcTime):
            raise KernelValueError("SealedPartitionFact.approved_at must be a UtcTime")

    @property
    def period(self) -> Interval:
        period = self.partition_id.period
        assert period is not None  # noqa: S101 - 封印の区分の partition は期間を持つ
        return period


@dataclass(frozen=True, slots=True)
class WindowRefusal:
    """窓を導けない（消費の前に止める）。`reason` と人間が読む `detail`（件数・時刻だけ）。"""

    reason: FinalReason
    detail: str


@dataclass(frozen=True, slots=True)
class FinalWindow:
    """導いた窓（D09 §9.10.3）。

    - `consumption`: 消費する範囲（使える窓の全部。要決定 R2-1 (a)）。
    - `scoring_start`: 採点の始まり（仮置き R2-10。窓が 2024-01-01 から始まらないときはウォーム
      アップの長さだけ後ろにずらす。ずらした分も消費の対象）。
    - `targets`: 消費の対象（`(snapshot_id, partition)`。run が読む銘柄の、消費する範囲の、すべての
      時間足・両組・すべての承認済み snapshot。要決定 R2-2 (a)）。
    - `reads`: 組ごとの「snapshot と partition の並び」（要決定 R2-3 (a)。重複は最初に承認された
      snapshot だけ。仮置き R2-9）。
    - `weeks`: 使える窓の取引週（partition の期間）の数。
    - `conversion`: 換算用の読み取りの対象（`(snapshot_id, partition)`。円以外の決済通貨の候補が
      換算の脚としてだけ読む、執行の通貨ペアでない通貨ペアの 1 分足の封印の partition。両組。
      D06 v1.20 案 §19.14）。**消費の対象（`targets`）に入れない**（決定 (21) (b)）。並び
      （`reads`）には入る。円の候補は空。
    """

    consumption: Interval
    scoring_start: UtcTime
    targets: tuple[tuple[str, PartitionId], ...]
    reads: Paired[tuple[GrantedRead, ...]]
    weeks: int
    conversion: tuple[tuple[str, PartitionId], ...] = ()

    @property
    def scoring(self) -> Interval:
        return Interval(start=self.scoring_start, end=self.consumption.end)

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        """消費の対象を持つ snapshot（消費の行を書く snapshot）。"""
        return tuple(sorted({snapshot for snapshot, _ in self.targets}))

    @property
    def conversion_snapshot_ids(self) -> tuple[str, ...]:
        """換算用の読み取りの対象を持つ snapshot（換算用の読み取りの行を書く snapshot）。"""
        return tuple(sorted({snapshot for snapshot, _ in self.conversion}))

    @property
    def read_snapshot_ids(self) -> tuple[str, ...]:
        """記録の行を書く snapshot のすべて（消費の対象と換算用の読み取りの対象の和）。"""
        return tuple(sorted({*self.snapshot_ids, *self.conversion_snapshot_ids}))


def _slots(t_fix: UtcTime) -> list[Interval]:
    """`[2024-01-01, T_fix)` の中の partition の期間（取引週。終わりが `T_fix` 以下のもの）。"""
    out: list[Interval] = []
    start = WINDOW_START
    while True:
        period = sealed_partition_period(start)
        if period.end > t_fix:
            return out
        out.append(period)
        start = period.end


def derive_final_window(
    facts: Sequence[SealedPartitionFact],
    *,
    symbols: frozenset[str],
    t_fix: UtcTime,
    excluded: Sequence[Interval],
    length_days: int,
    warmup: timedelta,
    conversion_symbols: frozenset[str] = frozenset(),
) -> FinalWindow | WindowRefusal:
    """窓・消費の対象・読む並び・採点の始まりを導き、長さ `L` を確かめる（D09 §9.10.3 の 1〜7）。

    `facts` は承認済みの OANDA MT5 の経路の snapshot の封印の区分の partition（状態は origin の
    既定ブランチ上の消費の記録から導いたもの）、`symbols` は run が読む銘柄、`excluded` は他の
    候補の前向き検証の期間（仮置き R2-8）、`warmup` は最終検証の run のウォームアップの長さ。

    使える窓は、理由の分かる抜け（`CONSUMED` の partition がある週、他の候補の前向き検証の期間に
    重なる週）で区切った連続した部分のうち最も新しいもの（仮置き R2-9、要決定 R2-9 (a)）。その
    中に partition の無い週（組・系列のどれかに partition が無い週）があれば止める。同じ組・系列・
    週の partition の状態が snapshot の間で食い違えば構造の誤り、ダイジェストが違えば止める。

    `conversion_symbols` は換算の脚の通貨ペアのうち候補の執行の通貨ペアでないもの（円以外の決済
    通貨の候補の USDJPY。D06 v1.20 案 §19.14）。その 1 分足（`CONVERSION_TIMEFRAME`）の bid の系列
    の封印の partition を**換算用の読み取りの対象**にする: 消費の対象に入れず（決定 (21) (b)）、
    並びには入れ、同じ週の換算用の読み取りの対象が `CONSUMED` の週は理由の分かる抜けとして窓を
    区切り、使える窓の中で組ごとに partition が無い週があれば止める（`WINDOW_HAS_MISSING_WEEK`）。
    重複は消費の対象と同じく最初に承認された snapshot だけを読む（ダイジェストが違えば止める）。
    """
    if isinstance(length_days, bool) or not isinstance(length_days, int) or length_days < 1:
        raise KernelValueError("derive_final_window.length_days must be a positive int")
    if not isinstance(warmup, timedelta) or warmup < timedelta(0):
        raise KernelValueError("derive_final_window.warmup must be a non-negative timedelta")
    if conversion_symbols & symbols:
        raise KernelValueError(
            "derive_final_window.conversion_symbols excludes the symbols the run executes"
            " (D06 §19.14: the executed pair's partitions are consumed)"
        )
    chosen = [
        fact
        for fact in facts
        if str(fact.partition_id.series.symbol) in symbols and fact.period.end <= t_fix
    ]
    converting = [
        fact
        for fact in facts
        if str(fact.partition_id.series.symbol) in conversion_symbols
        and fact.partition_id.series.timeframe.id == CONVERSION_TIMEFRAME
        and fact.partition_id.series.basis is PriceBasis.BID
        and fact.period.end <= t_fix
    ]
    conversion_series = sorted({fact.partition_id.series for fact in converting}, key=str)
    lacking_conversion = sorted(
        conversion_symbols - {str(item.symbol) for item in conversion_series}
    )
    if lacking_conversion:
        return WindowRefusal(
            FinalReason.WINDOW_HAS_MISSING_WEEK,
            f"no approved sealed {CONVERSION_TIMEFRAME} partition of the conversion legs"
            f" {lacking_conversion} ends by T_fix {t_fix}; the window cannot be converted"
            " (D06 §19.14)",
        )
    series = sorted({fact.partition_id.series for fact in chosen}, key=str)
    lacking = sorted(symbols - {str(item.symbol) for item in series})
    if series and lacking:
        # 要求した銘柄の一部に封印の partition が 1 つも無い（被覆が無い）。存在する側だけを消費
        # しない（D09 §9.10.3 の 5、§9.10.2 の段 2 (e) の系列の被覆）。
        return WindowRefusal(
            FinalReason.WINDOW_HAS_MISSING_WEEK,
            f"no approved sealed partition of {lacking} ends by T_fix {t_fix}; the window of the"
            " other symbols is not consumed alone (仮置き R2-9)",
        )
    if not series:
        return WindowRefusal(
            FinalReason.NO_USABLE_WINDOW,
            f"no approved sealed partition of {sorted(symbols)} ends by T_fix {t_fix}",
        )
    by_key: dict[tuple[Variant, SeriesId, UtcTime], list[SealedPartitionFact]] = {}
    for fact in [*chosen, *converting]:
        by_key.setdefault((fact.variant, fact.partition_id.series, fact.period.start), []).append(
            fact
        )
    for key, items in sorted(by_key.items(), key=lambda pair: str(pair[0])):
        if len({item.state for item in items}) > 1:
            return WindowRefusal(
                FinalReason.STATE_DIFFERS_ACROSS_SNAPSHOTS,
                f"the {key[0].value} partitions of {key[1]} for the week {key[2]} have different"
                f" states across {len(items)} snapshots (仮置き R2-9。構造の誤り)",
            )
    slots = _slots(t_fix)
    blocked: list[bool] = []
    for slot in slots:
        # 換算用の読み取りの対象が `CONSUMED` の週も抜け（換算にも読まない。D06 §19.14）。
        in_slot = [fact for fact in [*chosen, *converting] if fact.period == slot]
        consumed = any(fact.state is HoldoutState.CONSUMED for fact in in_slot)
        forward = any(slot.overlaps(item) for item in excluded)
        blocked.append(consumed or forward)
    # 最も新しい（終わりが最も遅い）連続した部分。
    last: list[int] = []
    for index in range(len(slots) - 1, -1, -1):
        if blocked[index]:
            if last:
                break
            continue
        last.insert(0, index)
    if not last:
        return WindowRefusal(
            FinalReason.NO_USABLE_WINDOW,
            f"every week of [{WINDOW_START}, {t_fix}) is consumed or inside another candidate's"
            " forward period",
        )
    window_slots = [slots[index] for index in last]
    missing: list[str] = []
    for slot in window_slots:
        for variant in VARIANTS:
            for item in [*series, *conversion_series]:
                if not by_key.get((variant, item, slot.start)):
                    missing.append(f"{variant.value} {item} {slot.start}")
    if missing:
        return WindowRefusal(
            FinalReason.WINDOW_HAS_MISSING_WEEK,
            f"{len(missing)} week(s) of the usable window have no approved partition (first:"
            f" {missing[0]}); the window is not shrunk silently (仮置き R2-9)",
        )
    reads: dict[Variant, list[GrantedRead]] = {variant: [] for variant in VARIANTS}
    targets: list[tuple[str, PartitionId]] = []
    conversion: list[tuple[str, PartitionId]] = []
    for slot in window_slots:
        for variant in VARIANTS:
            for item in [*series, *conversion_series]:
                group = by_key[(variant, item, slot.start)]
                if len({fact.digest for fact in group}) > 1:
                    return WindowRefusal(
                        FinalReason.DUPLICATE_PARTITION_MISMATCH,
                        f"the {variant.value} partitions of {item} for the week {slot.start} in"
                        f" {len(group)} snapshots have different digests (仮置き R2-9)",
                    )
                first = min(group, key=lambda fact: (fact.approved_at.value, fact.snapshot_id))
                reads[variant].append(
                    GrantedRead(snapshot_id=first.snapshot_id, partition_id=first.partition_id)
                )
                if item in conversion_series:
                    # 換算用の読み取りの対象は読む partition だけ（消費しないので重複は読まない）。
                    conversion.append((first.snapshot_id, first.partition_id))
                else:
                    targets.extend((fact.snapshot_id, fact.partition_id) for fact in group)
    consumption = Interval(start=window_slots[0].start, end=window_slots[-1].end)
    scoring_start = (
        consumption.start if consumption.start == WINDOW_START else consumption.start + warmup
    )
    if not scoring_start < consumption.end:
        return WindowRefusal(
            FinalReason.WINDOW_SHORTER_THAN_L,
            f"the warmup {warmup} leaves no scoring interval in {consumption} (仮置き R2-10)",
        )
    scored = consumption.end - scoring_start
    if scored < timedelta(days=length_days):
        return WindowRefusal(
            FinalReason.WINDOW_SHORTER_THAN_L,
            f"the scoring interval [{scoring_start}, {consumption.end}) is {scored} long, shorter"
            f" than L = {length_days} days (仮置き R2-12、要決定 R2-9 (a))",
        )

    def ordered(items: list[GrantedRead]) -> tuple[GrantedRead, ...]:
        return tuple(
            sorted(
                items,
                key=lambda read: (
                    str(read.partition_id.series),
                    read.partition_id.period_start.value.isoformat()
                    if read.partition_id.period_start is not None
                    else "",
                ),
            )
        )

    return FinalWindow(
        consumption=consumption,
        scoring_start=scoring_start,
        targets=tuple(sorted(set(targets), key=lambda pair: (pair[0], str(pair[1])))),
        reads=Paired(
            standard=ordered(reads[Variant.STANDARD]),
            candidate=ordered(reads[Variant.CANDIDATE]),
        ),
        weeks=len(window_slots),
        conversion=tuple(sorted(set(conversion), key=lambda pair: (pair[0], str(pair[1])))),
    )


# --- 最終評価の許可（D09 §9.10.2 の段 3）---------------------------------------------------


@dataclass(frozen=True, slots=True)
class PredictedRun:
    """最終検証の run の予測（段 2 (g)）。値は 16 進の文字列（控えに書く）。"""

    config_digest: str
    run_id: str

    def __post_init__(self) -> None:
        for name in ("config_digest", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise KernelValueError(f"PredictedRun.{name} must be a non-empty str")


@dataclass(frozen=True, slots=True)
class FinalEvaluationPermit:
    """関所の最終評価の許可（D09 §9.10.2 の段 3。品質確認の許可とは別の型。ADR-0036 第3項）。

    許可の値は候補・消費の対象・窓・予測の `RunId`・目的を持ち、組ごとの「snapshot と partition の
    並び」（`grant_of`。要決定 R2-3 (a)）を読み取りの面へ渡す。許可の識別子は `app` が作る
    （128 ビットの乱数の 16 進。D01 §2.2 規則 2）。
    """

    permit_id: str
    candidate: CandidateKey
    t_fix: UtcTime
    length_days: int
    window: FinalWindow
    predicted: Paired[PredictedRun]
    purpose: str
    research_partitions: Paired[tuple[tuple[str, PartitionId], ...]] = field(
        default_factory=lambda: Paired(standard=(), candidate=())
    )

    def __post_init__(self) -> None:
        if not isinstance(self.permit_id, str) or len(self.permit_id) != 32:
            raise KernelValueError("FinalEvaluationPermit.permit_id is 128 random bits in hex")
        int(self.permit_id, 16)
        if not isinstance(self.candidate, CandidateKey):
            raise KernelValueError("FinalEvaluationPermit.candidate must be a CandidateKey")
        if not isinstance(self.window, FinalWindow):
            raise KernelValueError("FinalEvaluationPermit.window must be a FinalWindow")
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise KernelValueError("FinalEvaluationPermit.purpose must not be blank")
        if not self.window.consumption.end <= self.t_fix:
            raise KernelValueError("the consumption range ends by T_fix (D15-16 (a))")

    def grant_of(self, variant: Variant) -> FinalReadGrant:
        """組の run が読む「snapshot と partition の並び」（D09 §9.10.3 の 4）。"""
        return FinalReadGrant(permit_id=self.permit_id, reads=self.window.reads.of(variant))

    def allowed_set(self, variant: Variant) -> frozenset[tuple[str, PartitionId]]:
        """組の run の許可 partition 集合（研究履歴の partition と並びの partition の和）。

        並びは、消費の対象のうちその組の run が読む部分と、換算用の読み取りの対象のうちその組の
        run が読む部分を持つ（D06 v1.20 案 §19.14 の P2F の (1)）。研究履歴の partition は記録票の
        取引する通貨ペアの snapshot と換算の snapshot のもの（決定 (23) (a)）。
        """
        reads = {(item.snapshot_id, item.partition_id) for item in self.window.reads.of(variant)}
        return frozenset(reads | set(self.research_partitions.of(variant)))


# --- 判定（D09 §9.10.11。要決定 R2-7 (a)）-------------------------------------------------


#: 最終検証の採点区間を区切る区間の長さ（D09 §9.10.16.2 の FV5-1、決定 212「364 日ごとに区切って
#: 当てる」。研究の fold の検証の区間の長さ。新しい値ではない）。
FINAL_INTERVAL_DAYS: Final = 364

#: 版 5 を指す候補の最終検証で当てない条件（研究期間だけの条件。D09 §9.10.16.2、決定 213・215・
#: 216、§9.10.16.5 の FV5-5）。結果を作らず、名前の一覧だけを判定と記録に添える。並びは D24 §3.1 の
#: 表の順（近傍の床 → 損失の集中 → 割り引いた確率）。
RESEARCH_ONLY_CONDITIONS: Final[tuple[ConditionScope, ...]] = (
    ConditionScope.NEIGHBOR_FLOOR,
    ConditionScope.LOSS_CONCENTRATION,
    ConditionScope.DEFLATED_DOWNSIDE_RATIO,
)


@dataclass(frozen=True, slots=True)
class FinalLossSideMaterial:
    """版 5 を指す候補の最終検証の、組の 1 本の run の損失の側の材料（D09 §9.10.16。決定 212）。

    - `scoring`: 採点区間（run の区間。`run_interval` は採点の始まりから始まる。D09 §9.10.14）。
    - `initial_balance`: run の開始の資産（初期残高）。最初の区間の始まりの資産（FV5-3 の (1)）。
    - `equity_points`: run の資産の系列（台帳 snapshot の含み込みの `equity` と処理点の時刻。処理点
      の昇順）。途中の境目の資産を採る（FV5-3 の (2)）。
    - `settled_equity`: run の終わりの費用を引いた後の資産（残高 ＋ 仮に閉じた損益。仮置き
      R4b-7）。最後の区間の終わりの資産（FV5-3 の (3)）。
    - `trades`: run の完了取引と run の終わりに仮に閉じた建玉の集合の要約（D24 §3.3 と同じ作り方。
      恒等式を確かめた後の値。`PROFIT_FACTOR` の入力。決定 214）。

    近傍・他の試行・局面（日足）の入力は持たない（決定 213・215〜217、FV5-T5）。
    """

    scoring: Interval
    initial_balance: Money
    equity_points: tuple[tuple[UtcTime, Decimal], ...]
    settled_equity: Money
    trades: PathTradeTotals

    def __post_init__(self) -> None:
        if not isinstance(self.scoring, Interval):
            raise KernelValueError("FinalLossSideMaterial.scoring must be an Interval")
        if not isinstance(self.initial_balance, Money) or not isinstance(
            self.settled_equity, Money
        ):
            raise KernelValueError("FinalLossSideMaterial balances must be Money")
        if not isinstance(self.trades, PathTradeTotals):
            raise KernelValueError("FinalLossSideMaterial.trades must be a PathTradeTotals")
        currency = self.initial_balance.currency
        if self.settled_equity.currency != currency or self.trades.wins.currency != currency:
            raise KernelValueError("FinalLossSideMaterial: 残高と取引の集合の通貨が違う")
        if not isinstance(self.equity_points, tuple):
            raise KernelValueError("FinalLossSideMaterial.equity_points must be a tuple")
        previous: UtcTime | None = None
        for item in self.equity_points:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], UtcTime)
                or not isinstance(item[1], Decimal)
                or not item[1].is_finite()
            ):
                raise KernelValueError(
                    "FinalLossSideMaterial.equity_points holds (UtcTime, finite Decimal) pairs"
                )
            if previous is not None and item[0] < previous:
                raise KernelValueError("FinalLossSideMaterial.equity_points は処理点の昇順")
            previous = item[0]

    def equity_at(self, boundary: UtcTime) -> Decimal:
        """途中の境目 `boundary` の資産（FV5-3 の (2)）。

        `boundary` 以前で最後の観測点の値（`boundary` ちょうどの点を含む。`boundary` より後の点は
        使わない）。`boundary` 以前に観測点が無ければ run の開始の資産（FV5-3 の (1)）。
        """
        value = self.initial_balance.amount
        for at, amount in self.equity_points:
            if boundary < at:
                break
            value = amount
        return value


@dataclass(frozen=True, slots=True)
class FinalInterval:
    """採点区間を区切った区間 1 つ（FV5-2）。`partial` は最初の端数の区間の印。"""

    interval_index: int
    interval: Interval
    partial: bool


def final_intervals(scoring: Interval) -> tuple[FinalInterval, ...]:
    """採点区間を終わりから `FINAL_INTERVAL_DAYS` 日ずつさかのぼって区切る（FV5-2。決定 220）。

    研究の fold の検証区間の並べ方（`splits.generate_folds` の手順 1 と端数の fold）と同じく、
    `j = 0, 1, …` の順に `[E − (j+1)·364 日, E − j·364 日)` を始まりが採点の始まり `S` 以上の間
    採り、余り `[S, E − k·364 日)` が 0 より長ければ最初の端数の区間にする。古い順に並べ、番号を
    0 から振る（端数の区間があれば 0）。
    """
    if not isinstance(scoring, Interval):
        raise KernelValueError("final_intervals requires an Interval")
    length = timedelta(days=FINAL_INTERVAL_DAYS)
    full = (scoring.end - scoring.start) // length
    taken = [
        Interval(start=scoring.end - length * (step + 1), end=scoring.end - length * step)
        for step in range(full)
    ]
    ordered: list[tuple[Interval, bool]] = []
    oldest = scoring.end - length * full
    if scoring.start < oldest:
        ordered.append((Interval(start=scoring.start, end=oldest), True))
    ordered.extend((item, False) for item in reversed(taken))
    return tuple(
        FinalInterval(interval_index=index, interval=item, partial=partial)
        for index, (item, partial) in enumerate(ordered)
    )


_COMPARE: Final = {
    Comparator.GE: lambda left, right: left >= right,
    Comparator.GT: lambda left, right: left > right,
    Comparator.LE: lambda left, right: left <= right,
    Comparator.LT: lambda left, right: left < right,
}


@dataclass(frozen=True, slots=True)
class IntervalFloorResult:
    """区間の損失の床 1 件の結果（D09 §9.10.16.2 の FV5-1。決定 212）。

    `metric`・`comparator`・`threshold` は研究ポリシーの fold の純損失の床の行（`fold_floors` の
    純収益率の指標の条件。版 5 は区間末の評価を含む純収益率 ≥ −0.15）。`start_equity`・
    `end_equity` は区間の始まりと終わりの資産（FV5-3）、`observed` は
    `(end_equity − start_equity) ÷ start_equity`。始まりの資産が 0 以下なら観測不足
    （`UNCOMPUTABLE`・`UNDEFINED_DENOMINATOR`）。
    """

    interval: FinalInterval
    metric: MetricId
    comparator: Comparator
    threshold: Decimal
    start_equity: Decimal
    end_equity: Decimal
    observed: Decimal | None
    outcome: ConditionOutcome
    unavailable_reason: MetricUnavailableReason | None

    def __post_init__(self) -> None:
        if not isinstance(self.interval, FinalInterval):
            raise KernelValueError("IntervalFloorResult.interval must be a FinalInterval")
        if self.outcome is ConditionOutcome.UNCOMPUTABLE:
            if (
                self.observed is not None
                or self.unavailable_reason is not MetricUnavailableReason.UNDEFINED_DENOMINATOR
            ):
                raise KernelValueError(
                    "an UNCOMPUTABLE interval floor has no observed value and is"
                    " UNDEFINED_DENOMINATOR"
                )
            return
        if self.outcome not in (ConditionOutcome.MET, ConditionOutcome.NOT_MET):
            raise KernelValueError(f"an interval floor has no {self.outcome.value} outcome")
        if self.observed is None or self.unavailable_reason is not None:
            raise KernelValueError("a MET / NOT_MET interval floor has an observed value only")
        met = _COMPARE[self.comparator](self.observed, self.threshold)
        if met is not (self.outcome is ConditionOutcome.MET):
            raise KernelValueError(
                f"IntervalFloorResult.outcome {self.outcome.value} contradicts"
                f" {self.observed} {self.comparator.value} {self.threshold}"
            )


def interval_floor_results(
    floors: Sequence[MetricCondition], material: FinalLossSideMaterial
) -> tuple[IntervalFloorResult, ...]:
    """区間の損失の床（FV5-1 (a)・FV5-2・FV5-3。決定 212・220）。

    `floors` は研究ポリシーの `fold_floors`。そのうち純収益率の指標の条件（fold の純損失の床）
    だけを各区間に当てる（区間に当てるのは損失の床だけで、ドローダウンの床・利益の条件は区間に
    置かない。決定 212・218 の注記）。結果は区間の古い順、区間の中は条件の書いた順。区間の損益は
    資産の系列の境目の値で測り、途中の境目で建玉を仮に閉じない（FV5-3）。
    """
    if not isinstance(material, FinalLossSideMaterial):
        raise KernelValueError("interval_floor_results requires a FinalLossSideMaterial")
    applied = [item for item in floors if item.metric in NET_RETURN_RATE_METRICS]
    intervals = final_intervals(material.scoring)
    results: list[IntervalFloorResult] = []
    for item in intervals:
        start = (
            material.initial_balance.amount
            if item.interval_index == 0
            else material.equity_at(item.interval.start)
        )
        end = (
            material.settled_equity.amount
            if item.interval_index == len(intervals) - 1
            else material.equity_at(item.interval.end)
        )
        observed: Decimal | None = None
        reason: MetricUnavailableReason | None = None
        if start <= 0:
            reason = MetricUnavailableReason.UNDEFINED_DENOMINATOR
        else:
            with localcontext(kernel_context()):
                profit = end - start
            observed = ratio_of(profit, start)
        for condition in applied:
            if observed is None:
                outcome = ConditionOutcome.UNCOMPUTABLE
            elif _COMPARE[condition.comparator](observed, condition.threshold):
                outcome = ConditionOutcome.MET
            else:
                outcome = ConditionOutcome.NOT_MET
            results.append(
                IntervalFloorResult(
                    interval=item,
                    metric=condition.metric,
                    comparator=condition.comparator,
                    threshold=condition.threshold,
                    start_equity=start,
                    end_equity=end,
                    observed=observed,
                    outcome=outcome,
                    unavailable_reason=reason,
                )
            )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class FinalRunSummary:
    """組の最終検証の run の要約（指標は経路の指標 `PATH_METRICS`、件数は run の取引件数）。

    指標の値は最終検証の記録とレポートにだけ出し、画面には出さない（D09 §9.10.7、D15 §9）。
    `loss_side` は研究ポリシーの版 5 を指す候補の run だけが持つ損失の側の材料（D09 §9.10.16）。
    版 4 以下の候補は `None`（要約の形も判定も今のまま。FV5-T1）。
    """

    metrics: tuple[tuple[MetricId, MetricValue], ...]
    trade_count: int
    loss_side: FinalLossSideMaterial | None = None

    def __post_init__(self) -> None:
        if self.loss_side is not None and not isinstance(self.loss_side, FinalLossSideMaterial):
            raise KernelValueError("FinalRunSummary.loss_side must be a FinalLossSideMaterial")

    def metric(self, metric: MetricId) -> MetricValue | None:
        for key, value in self.metrics:
            if key is metric:
                return value
        return None


@dataclass(frozen=True, slots=True)
class FinalVariantOutcome:
    """組の最終検証の判定（2 軸と 4 値。D13 §5.5 の手順から選定と fold を除いたもの）。

    版 5 を指す候補では、`interval_floors` に区間の損失の床の結果（決定 212）、`research_only` に
    当てない条件の名前（`RESEARCH_ONLY_CONDITIONS`。FV5-5）を持ち、`conditions` の
    `PROFIT_FACTOR` は取引の集合から当てた `ProfitFactorResult`（決定 214）。版 4 以下はどちらも空。
    """

    variant: Variant
    verdict: SearchVerdict
    profitability: ProfitabilityAxis
    evidence: EvidenceAxis
    conditions: tuple[ConditionResult, ...]
    reasons: tuple[str, ...]
    hold: bool | None
    trade_count: int
    required_trades: int | None
    interval_floors: tuple[IntervalFloorResult, ...] = ()
    research_only: tuple[ConditionScope, ...] = ()


def judge_final_variant(
    standard: EvaluationStandard,
    variant: Variant,
    run: FinalRunSummary,
    requirement: FrequencyClass | None,
) -> FinalVariantOutcome:
    """組の最終検証の判定（要決定 R2-7 (a)。版 5 は D09 §9.10.16）。

    組ごとに固定した 1 試行の 1 本の連続口座の run に、現行の版の正式条件（経路の条件:
    最低条件と全体の水準。検証の合計の最低取引件数）を D13 §5.5 の 2 軸と 4 値の手順で当てる:

    1. 最低条件を割り、かつ件数が最低件数以上: 満たさない。
    2. （run の失敗は判定にしない。消費の後の失敗として扱う。D09 §9.10.6）
    3. 件数が最低件数未満、判定に使う指標が値なし、または件数不足のまま最低条件を割った: 証拠不足。
    4. 全体の水準の条件が 1 つでも満たさない: 満たさない。すべて満たす: 満たす（用途が機構の確認
       なら「機構の確認で条件をすべて満たした」）。

    研究ポリシーの版 5（`ValidationRuleV5`）を指す候補は、D24 §3.6 の写し方のまま、区間の損失の床
    （`interval_floor_results`。決定 212）を手順 1・3 の最低条件に、取引の集合から当てる
    `PROFIT_FACTOR`（決定 214）を手順 4 の全体の水準に足す。損失の集中・近傍の損失の床・試行数で
    割り引いた確率は当てず、結果を作らない（決定 213・215・216。名前は `research_only` に置く）。
    版 5 の要約は損失の側の材料（`run.loss_side`）を持つ。版 4 以下は今のまま（FV5-T1）。

    `requirement` はその候補の研究の判定で決まった頻度区分の証拠の要件（`None` は区分が決まら
    なかった候補。件数の要件を当てない）。
    """
    if not isinstance(standard, EvaluationStandard) or not isinstance(run, FinalRunSummary):
        raise KernelValueError("judge_final_variant requires an EvaluationStandard and a summary")
    rule = standard.validation
    interval_floors: tuple[IntervalFloorResult, ...] = ()
    research_only: tuple[ConditionScope, ...] = ()
    if isinstance(rule, ValidationRuleV5):
        material = run.loss_side
        if material is None:
            raise KernelValueError(
                "a final-validation run of a v5 candidate needs its loss-side material"
                " (D09 §9.10.16)"
            )
        conditions = path_condition_results_v5(rule.path, run.metric, material.trades)
        interval_floors = interval_floor_results(rule.fold_floors, material)
        research_only = RESEARCH_ONLY_CONDITIONS
    else:
        if run.loss_side is not None:
            raise KernelValueError(
                "the final-validation run of a candidate of policy version 4 or earlier has no"
                " loss-side material (D09 §9.10.16.4)"
            )
        conditions = path_condition_results(rule.path, run.metric)
    total = run.trade_count
    required = None if requirement is None else requirement.min_validation_trades_total
    total_short = required is not None and total < required
    breaches = [
        *(
            item
            for item in conditions
            if item.scope is ConditionScope.PATH_FLOOR and item.outcome is ConditionOutcome.NOT_MET
        ),
        *(item for item in interval_floors if item.outcome is ConditionOutcome.NOT_MET),
    ]
    uncomputable = [
        *(item for item in conditions if item.outcome is ConditionOutcome.UNCOMPUTABLE),
        *(item for item in interval_floors if item.outcome is ConditionOutcome.UNCOMPUTABLE),
    ]
    reasons: list[str] = []
    if total_short:
        reasons.append("TOTAL_TRADES_BELOW")
    if breaches and total_short:
        reasons.append("FLOOR_BREACHED_TOTAL_TRADES_BELOW")
    if uncomputable:
        reasons.append("METRIC_UNCOMPUTABLE")
    verdict: SearchVerdict
    if breaches and not total_short:
        verdict = SearchVerdict.BELOW_STANDARD
    elif total_short or uncomputable or breaches:
        verdict = SearchVerdict.INSUFFICIENT_EVIDENCE
    elif any(
        item.scope is ConditionScope.PATH_AGGREGATE and item.outcome is ConditionOutcome.NOT_MET
        for item in conditions
    ):
        verdict = SearchVerdict.BELOW_STANDARD
    elif standard.purpose is StandardPurpose.STANDARD:
        verdict = SearchVerdict.MEETS_STANDARD
    else:
        verdict = SearchVerdict.MET_IN_MECHANISM_CHECK
    profitability: ProfitabilityAxis
    if uncomputable:
        profitability = ProfitabilityAxis.NOT_JUDGED
    elif breaches or any(item.outcome is ConditionOutcome.NOT_MET for item in conditions):
        profitability = ProfitabilityAxis.NOT_MET
    else:
        profitability = ProfitabilityAxis.MET
    hold = hold_mark_of(profitability) if verdict is SearchVerdict.INSUFFICIENT_EVIDENCE else None
    return FinalVariantOutcome(
        variant=variant,
        verdict=verdict,
        profitability=profitability,
        evidence=EvidenceAxis.INSUFFICIENT if reasons else EvidenceAxis.SUFFICIENT,
        conditions=conditions,
        reasons=tuple(reasons),
        hold=hold,
        trade_count=total,
        required_trades=required,
        interval_floors=interval_floors,
        research_only=research_only,
    )


_MET: Final = frozenset({SearchVerdict.MEETS_STANDARD, SearchVerdict.MET_IN_MECHANISM_CHECK})


def _combined_hold(items: Sequence[FinalVariantOutcome]) -> bool | None:
    marks = [False if item.verdict is SearchVerdict.BELOW_STANDARD else item.hold for item in items]
    if any(mark is False for mark in marks):
        return False
    if any(mark is None for mark in marks):
        return None
    return True


def combine_final(standard: FinalVariantOutcome, candidate: FinalVariantOutcome) -> CombinedVerdict:
    """組の判定（D09 §7.12 の順。`search.combine_verdicts` と同じ規則を最終検証の組に当てる）。"""
    if standard.variant is not Variant.STANDARD or candidate.variant is not Variant.CANDIDATE:
        raise KernelValueError("combine_final requires the standard and the candidate outcome")
    pair = (standard, candidate)
    verdicts = (standard.verdict, candidate.verdict)
    if SearchVerdict.INCOMPLETE in verdicts:
        return CombinedVerdict(verdict=SearchVerdict.INCOMPLETE, split=False, hold=None)
    met = [item.verdict in _MET for item in pair]
    if all(met):
        return CombinedVerdict(verdict=standard.verdict, split=False, hold=None)
    if any(met):
        other = pair[1] if met[0] else pair[0]
        return CombinedVerdict(
            verdict=SearchVerdict.INSUFFICIENT_EVIDENCE, split=True, hold=_combined_hold((other,))
        )
    if SearchVerdict.BELOW_STANDARD in verdicts:
        return CombinedVerdict(verdict=SearchVerdict.BELOW_STANDARD, split=False, hold=None)
    return CombinedVerdict(
        verdict=SearchVerdict.INSUFFICIENT_EVIDENCE, split=False, hold=_combined_hold(pair)
    )
