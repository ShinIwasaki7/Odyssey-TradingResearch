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

本モジュールを作るために読まなかったもの: 戦略の成績・取引表・価格・2024 年以降のデータ・
旧データの CSV。数は件数・日数・時刻だけを扱う。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import MetricId, MetricValue
from odyssey_fx.evaluation.domain.search import (
    CombinedVerdict,
    ConditionOutcome,
    ConditionResult,
    ConditionScope,
    EvaluationStandard,
    EvidenceAxis,
    FrequencyClass,
    ProfitabilityAxis,
    SearchVerdict,
    StandardPurpose,
    hold_mark_of,
    path_condition_results,
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
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = [
    "WINDOW_START",
    "ApplicationState",
    "CandidateKey",
    "FinalEvaluationPermit",
    "FinalLedgerResult",
    "FinalReason",
    "FinalRunSummary",
    "FinalVariantOutcome",
    "FinalWindow",
    "PredictedRun",
    "SealedPartitionFact",
    "WindowRefusal",
    "combine_final",
    "derive_application_state",
    "derive_final_window",
    "judge_final_variant",
]

#: 最終検証の窓の始まり（D15-16 (a)、ADR-0038 第5項の 1。研究履歴の境界と同じ）。
WINDOW_START: Final = SEALED_RESEARCH_UNTIL


class FinalReason(Enum):
    """申請の拒否・関所の拒否・消費の後の失敗の理由の識別子（D09 §9.10。画面と台帳に出す）。

    D09 が名前を決めたもの（`ALREADY_VALIDATED` など）に、実装 PR が足したもの（R4 の仮置き。
    `NO_USABLE_WINDOW` など）を加えた。終了コードへの対応は `app.final_validation` が持つ。
    """

    # 申請の拒否（終了コード 7）
    ALREADY_VALIDATED = "ALREADY_VALIDATED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    WITHDRAWN = "WITHDRAWN"
    OUTSIDE_VIEWING_DECLARED = "OUTSIDE_VIEWING_DECLARED"
    NOT_MEETS_STANDARD = "NOT_MEETS_STANDARD"
    NOT_CURRENT_POLICY_VERSION = "NOT_CURRENT_POLICY_VERSION"
    NO_FINAL_HOLDOUT = "NO_FINAL_HOLDOUT"
    LEDGER_NOT_ON_ORIGIN = "LEDGER_NOT_ON_ORIGIN"
    NOT_PROCEEDED_BY_REVIEW = "NOT_PROCEEDED_BY_REVIEW"
    NOT_FIXED = "NOT_FIXED"
    DECLARATION_NOT_COVERING = "DECLARATION_NOT_COVERING"
    EARLIER_CANDIDATE_PENDING = "EARLIER_CANDIDATE_PENDING"
    WINDOW_HAS_MISSING_WEEK = "WINDOW_HAS_MISSING_WEEK"
    DUPLICATE_PARTITION_MISMATCH = "DUPLICATE_PARTITION_MISMATCH"
    WINDOW_SHORTER_THAN_L = "WINDOW_SHORTER_THAN_L"
    NO_USABLE_WINDOW = "NO_USABLE_WINDOW"
    FINAL_HOLDOUT_NOT_COVERING = "FINAL_HOLDOUT_NOT_COVERING"
    DATA_CAPABILITY_NOT_MET = "DATA_CAPABILITY_NOT_MET"
    RECOMPUTATION_MISMATCH = "RECOMPUTATION_MISMATCH"
    P2F_NOT_PASSED = "P2F_NOT_PASSED"
    OUTPUT_NOT_EMPTY = "OUTPUT_NOT_EMPTY"
    NOT_AWAITING_RECORD = "NOT_AWAITING_RECORD"
    NOT_WITHDRAWABLE = "NOT_WITHDRAWABLE"
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
    """

    consumption: Interval
    scoring_start: UtcTime
    targets: tuple[tuple[str, PartitionId], ...]
    reads: Paired[tuple[GrantedRead, ...]]
    weeks: int

    @property
    def scoring(self) -> Interval:
        return Interval(start=self.scoring_start, end=self.consumption.end)

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        return tuple(sorted({snapshot for snapshot, _ in self.targets}))


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
) -> FinalWindow | WindowRefusal:
    """窓・消費の対象・読む並び・採点の始まりを導き、長さ `L` を確かめる（D09 §9.10.3 の 1〜7）。

    `facts` は承認済みの OANDA MT5 の経路の snapshot の封印の区分の partition（状態は origin の
    既定ブランチ上の消費の記録から導いたもの）、`symbols` は run が読む銘柄、`excluded` は他の
    候補の前向き検証の期間（仮置き R2-8）、`warmup` は最終検証の run のウォームアップの長さ。

    使える窓は、理由の分かる抜け（`CONSUMED` の partition がある週、他の候補の前向き検証の期間に
    重なる週）で区切った連続した部分のうち最も新しいもの（仮置き R2-9、要決定 R2-9 (a)）。その
    中に partition の無い週（組・系列のどれかに partition が無い週）があれば止める。同じ組・系列・
    週の partition の状態が snapshot の間で食い違えば構造の誤り、ダイジェストが違えば止める。
    """
    if isinstance(length_days, bool) or not isinstance(length_days, int) or length_days < 1:
        raise KernelValueError("derive_final_window.length_days must be a positive int")
    if not isinstance(warmup, timedelta) or warmup < timedelta(0):
        raise KernelValueError("derive_final_window.warmup must be a non-negative timedelta")
    chosen = [
        fact
        for fact in facts
        if str(fact.partition_id.series.symbol) in symbols and fact.period.end <= t_fix
    ]
    series = sorted({fact.partition_id.series for fact in chosen}, key=str)
    if not series:
        return WindowRefusal(
            FinalReason.NO_USABLE_WINDOW,
            f"no approved sealed partition of {sorted(symbols)} ends by T_fix {t_fix}",
        )
    by_key: dict[tuple[Variant, SeriesId, UtcTime], list[SealedPartitionFact]] = {}
    for fact in chosen:
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
        in_slot = [fact for fact in chosen if fact.period == slot]
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
            for item in series:
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
    for slot in window_slots:
        for variant in VARIANTS:
            for item in series:
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
        """組の run の許可 partition 集合（研究履歴の partition と並びの partition の和）。"""
        reads = {(item.snapshot_id, item.partition_id) for item in self.window.reads.of(variant)}
        return frozenset(reads | set(self.research_partitions.of(variant)))


# --- 判定（D09 §9.10.11。要決定 R2-7 (a)）-------------------------------------------------


@dataclass(frozen=True, slots=True)
class FinalRunSummary:
    """組の最終検証の run の要約（指標は経路の指標 `PATH_METRICS`、件数は run の取引件数）。

    指標の値は最終検証の記録とレポートにだけ出し、画面には出さない（D09 §9.10.7、D15 §9）。
    """

    metrics: tuple[tuple[MetricId, MetricValue], ...]
    trade_count: int

    def metric(self, metric: MetricId) -> MetricValue | None:
        for key, value in self.metrics:
            if key is metric:
                return value
        return None


@dataclass(frozen=True, slots=True)
class FinalVariantOutcome:
    """組の最終検証の判定（2 軸と 4 値。D13 §5.5 の手順から選定と fold を除いたもの）。"""

    variant: Variant
    verdict: SearchVerdict
    profitability: ProfitabilityAxis
    evidence: EvidenceAxis
    conditions: tuple[ConditionResult, ...]
    reasons: tuple[str, ...]
    hold: bool | None
    trade_count: int
    required_trades: int | None


def judge_final_variant(
    standard: EvaluationStandard,
    variant: Variant,
    run: FinalRunSummary,
    requirement: FrequencyClass | None,
) -> FinalVariantOutcome:
    """組の最終検証の判定（要決定 R2-7 (a)）。

    組ごとに固定した 1 試行の 1 本の連続口座の run に、現行の版の正式条件（経路の条件:
    最低条件と全体の水準。検証の合計の最低取引件数）を D13 §5.5 の 2 軸と 4 値の手順で当てる:

    1. 最低条件を割り、かつ件数が最低件数以上: 満たさない。
    2. （run の失敗は判定にしない。消費の後の失敗として扱う。D09 §9.10.6）
    3. 件数が最低件数未満、判定に使う指標が値なし、または件数不足のまま最低条件を割った: 証拠不足。
    4. 全体の水準の条件が 1 つでも満たさない: 満たさない。すべて満たす: 満たす（用途が機構の確認
       なら「機構の確認で条件をすべて満たした」）。

    `requirement` はその候補の研究の判定で決まった頻度区分の証拠の要件（`None` は区分が決まら
    なかった候補。件数の要件を当てない）。
    """
    if not isinstance(standard, EvaluationStandard) or not isinstance(run, FinalRunSummary):
        raise KernelValueError("judge_final_variant requires an EvaluationStandard and a summary")
    conditions = path_condition_results(standard.validation.path, run.metric)
    total = run.trade_count
    required = None if requirement is None else requirement.min_validation_trades_total
    total_short = required is not None and total < required
    breaches = [
        item
        for item in conditions
        if item.scope is ConditionScope.PATH_FLOOR and item.outcome is ConditionOutcome.NOT_MET
    ]
    uncomputable = [item for item in conditions if item.outcome is ConditionOutcome.UNCOMPUTABLE]
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
    elif any(item.outcome is ConditionOutcome.NOT_MET for item in conditions):
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
