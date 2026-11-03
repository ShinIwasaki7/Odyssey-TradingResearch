"""読み取りの関所（`holdout_gate`）: 品質確認専用の許可（ADR-0036、ADR-0035 第4項）と、
最終評価の許可（D09 v0.5 §9.10。R4）。

関所は 2 種類の許可を**別の型・別の手順**として持つ（ADR-0036 第3項）。品質確認の許可は順 6 の
S3 で実装した（`HoldoutGate`）。最終評価の許可は R4 で実装する（`issue_final_evaluation_permit`・
研究ポリシーの検査 P2F の `final_partitions_match_permit`・`consumption_rows_present`。手順の
結線と git・ファイルは `app.final_validation`）。

品質確認の読み取りの手順（fail-closed。ADR-0036 第7項）:

1. **許可の確認**: 要求の道具の名前と版・設定のダイジェスト・区分と期間・対象が、承認済みの
   定義（`configs/quality_checks/`）と一致することを確かめる。
2. **閲覧の記録**: 記録（`QualityAccessLog`）を読み、構造の誤りが無いことを確かめ、開始の行を
   追記して読み直す。どこかで失敗すれば許可を出さない（読み取りを始めない）。
3. 読み取り（`app` の品質確認専用の入口が、本モジュールの返す読み取りの許可の値を
   `marketdata` の道具へ渡す）。
4. **終了の行**: 道具の終了（完了・途中で停止）を受け取り、終了の行を追記する。書けなければ
   失敗を返す（入口は結果を出さずに失敗として終わる。PR #80 の仮置き 13）。

研究期間の原ファイルの確認の読み取り（ADR-0040。区分は研究履歴だけ）も同じ手順で許可する。
原ファイルの置き場の根は定義の区分で決まる（研究履歴は `data/raw/oanda_mt5/`、封印の区分は
`data/raw/oanda_mt5_sealed/`。ADR-0040 第2項の 2）。

消費の記録（`access_log.jsonl`）は書かない。`HoldoutState` は変わらない。品質確認の許可で
最終評価・run・探索の実験の入力は作れない（読み取りの許可の値は as-of ビューの許可集合へ
渡す型ではない。ADR-0036 第5項）。

識別子（許可・閲覧・行）と時刻は `app` が作って渡す（application は乱数と実時計を読まない。
D01 §2.2 規則2）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.application.ports import QualityAccessLog
from odyssey_fx.evaluation.domain.errors import EvaluationError
from odyssey_fx.evaluation.domain.final_validation import (
    CandidateKey,
    FinalEvaluationPermit,
    FinalReason,
    FinalWindow,
    PredictedRun,
    WindowRefusal,
)
from odyssey_fx.evaluation.domain.quality_permit import (
    QualityCheckDefinition,
    QualityCheckRequest,
    QualityPermitRefusal,
    QualityReadRefused,
    QualityTargetKind,
)
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired
from odyssey_fx.marketdata.domain.access import AccessClass, HoldoutState
from odyssey_fx.marketdata.domain.quality_access import (
    PartitionTarget,
    QualityAccessEnd,
    QualityAccessLogFailure,
    QualityAccessResult,
    QualityAccessStart,
    QualityReadPermit,
    QualityRecordKind,
    RawFileTarget,
)
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = [
    "HoldoutGate",
    "consumption_rows_present",
    "final_partitions_match_permit",
    "issue_final_evaluation_permit",
]


def _refused(kind: QualityPermitRefusal, detail: str) -> QualityReadRefused:
    return QualityReadRefused(kind=kind, detail=detail)


def _match(
    definition: QualityCheckDefinition, request: QualityCheckRequest
) -> QualityReadRefused | None:
    """要求が定義と一致するか（ADR-0036 第4項）。一致しなければ理由を返す。"""
    where = f"the approved definition {definition.source}"
    if definition.config_digest != request.config_digest:
        return _refused(
            QualityPermitRefusal.DEFINITION_MISMATCH,
            f"the config digest {request.config_digest} differs from {where}",
        )
    if definition.check_kind is not request.check_kind:
        return _refused(
            QualityPermitRefusal.DEFINITION_MISMATCH,
            f"the check kind {request.check_kind.value} differs from {where}",
        )
    extra_outputs = sorted(
        kind.value for kind in request.output_kinds if kind not in definition.output_kinds
    )
    if extra_outputs:
        return _refused(
            QualityPermitRefusal.DEFINITION_MISMATCH,
            f"the output kinds {extra_outputs} are not in {where}",
        )
    extra_classes = sorted(item.value for item in request.classes if item not in definition.classes)
    if extra_classes:
        return _refused(
            QualityPermitRefusal.OUTSIDE_DEFINITION,
            f"the classes {extra_classes} are not in {where}",
        )
    if not definition.period.contains_period(request.period):
        return _refused(
            QualityPermitRefusal.OUTSIDE_DEFINITION,
            f"the period [{request.period.start}, {request.period.end}) is outside {where}",
        )
    for target in request.targets:
        problem = _target_problem(definition, request, target)
        if problem is not None:
            return _refused(QualityPermitRefusal.OUTSIDE_DEFINITION, f"{problem} ({where})")
    return None


def _target_problem(
    definition: QualityCheckDefinition,
    request: QualityCheckRequest,
    target: PartitionTarget | RawFileTarget,
) -> str | None:
    if isinstance(target, RawFileTarget):
        if definition.target_kind is not QualityTargetKind.RAW_FILE:
            return f"the raw file {target.location} is not a target kind of the definition"
        if target.sha256 is None:
            return f"the raw file {target.location} has no hash"
        if target.symbol is None or target.symbol not in definition.symbols:
            return f"the symbol of the raw file {target.location} is not in the definition"
        return _raw_location_problem(target, definition.raw_root)
    partition = target.partition_id
    if definition.target_kind is not QualityTargetKind.PARTITION:
        return f"the partition {partition} is not a target kind of the definition"
    if partition.access_class not in request.classes:
        return f"the partition {partition} is outside the requested classes"
    if partition.series.symbol not in definition.symbols:
        return f"the symbol of {partition} is not in the definition"
    if partition.series.timeframe.id not in definition.timeframes:
        return f"the timeframe of {partition} is not in the definition"
    if not request.period.contains_interval(target.interval):
        return f"the partition {partition} is outside the requested period"
    return None


def _raw_location_problem(target: RawFileTarget, raw_root: tuple[str, ...]) -> str | None:
    """原ファイルが定義の区分の原ファイルの置き場にあり、その通貨の場所にあるか。

    置き場の根は定義の区分で決まる（ADR-0040 第2項の 2。`QualityCheckDefinition.raw_root`）:
    封印の区分なら `data/raw/oanda_mt5_sealed/<取得日>/<通貨>/<ファイル>`（D03 §16 の PR #109 の
    仮置き S2b-6。ADR-0036 第4項）、研究履歴の区分なら `data/raw/oanda_mt5/<取得日>/<通貨>/
    <ファイル>`（D03 §17.6 の 2、ADR-0040 第1項の 3）。置き場はパスの部分ごとに比べるので、
    `oanda_mt5` と `oanda_mt5_sealed`・`oanda_mt5x` を取り違えない。この外のファイル（旧データの
    CSV `data/raw/market/`、HistData の 1 分足 `data/raw/histdata/`、区分の違う置き場など）には
    許可を出さない。
    """
    location = target.location
    parts = PurePosixPath(location).parts
    root = len(raw_root)
    if (
        location.startswith("/")
        or "\\" in location
        or any(part in ("", ".", "..") for part in location.split("/"))
        or len(parts) < root + 3
        or parts[:root] != raw_root
    ):
        return (
            f"the raw file {location} is not under {'/'.join(raw_root)}/<date>/<symbol>/;"
            " only the OANDA MT5 raw files of the class of the definition may be covered"
            " (ADR-0036 第4項, ADR-0040 第2項の 2)"
        )
    if parts[root + 1] != str(target.symbol):
        return f"the raw file {location} is not in the directory of its symbol {target.symbol}"
    return None


class HoldoutGate:
    """読み取りの関所の品質確認の許可（ADR-0036）。最終評価の許可は下の関数（R4）。"""

    def __init__(
        self, definitions: Sequence[QualityCheckDefinition], log: QualityAccessLog
    ) -> None:
        if not all(isinstance(item, QualityCheckDefinition) for item in definitions):
            raise EvaluationError("HoldoutGate requires QualityCheckDefinition values")
        self._definitions = tuple(definitions)
        self._log = log

    def check_quality_request(self, request: QualityCheckRequest) -> QualityReadRefused | None:
        """許可の確認だけを行う（記録しない。ADR-0036 第7項の 1）。一致すれば `None`。"""
        if not isinstance(request, QualityCheckRequest):
            raise EvaluationError("check_quality_request requires a QualityCheckRequest")
        if AccessClass.QUARANTINED_UNASSIGNED in request.classes:
            return _refused(
                QualityPermitRefusal.QUARANTINED,
                "the quarantined class stays quarantined even under a quality-check permit"
                " (ADR-0036 第6項)",
            )
        candidates = [
            definition for definition in self._definitions if definition.tool == request.tool
        ]
        if not candidates:
            return _refused(
                QualityPermitRefusal.NO_DEFINITION,
                f"no approved quality-check definition for the tool {request.tool.name}"
                f" version {request.tool.version} (configs/quality_checks/. ADR-0036 第4項)",
            )
        refusals: list[QualityReadRefused] = []
        for definition in candidates:
            refusal = _match(definition, request)
            if refusal is None:
                return None
            refusals.append(refusal)
        return refusals[0]

    def begin_quality_read(
        self,
        request: QualityCheckRequest,
        *,
        permit_id: str,
        access_id: str,
        row_id: str,
        accessed_at: UtcTime,
    ) -> QualityReadPermit | QualityReadRefused:
        """許可の確認 → 閲覧の記録（開始の行）の順に行い、読み取りの許可の値を返す。

        どの段階で失敗しても許可の値を返さない（読み取りを始めない。ADR-0036 第7項）。
        """
        refusal = self.check_quality_request(request)
        if refusal is not None:
            return refusal
        try:
            start = QualityAccessStart(
                row_id=row_id,
                access_id=access_id,
                accessed_at=accessed_at,
                tool=request.tool,
                classes=request.classes,
                period=request.period,
                output_kinds=request.output_kinds,
                permit_id=permit_id,
                targets=request.targets,
                check_kind=request.check_kind,
                record_kind=QualityRecordKind.LIVE,
            )
            permit = QualityReadPermit(
                permit_id=permit_id,
                access_id=access_id,
                tool=request.tool,
                classes=request.classes,
                period=request.period,
                targets=request.targets,
                output_kinds=request.output_kinds,
            )
        except ValueError as exc:
            return _refused(QualityPermitRefusal.RECORD_FAILED, f"{type(exc).__name__}: {exc}")
        contents = self._log.read()
        if isinstance(contents, QualityAccessLogFailure):
            return _refused(
                QualityPermitRefusal.RECORD_FAILED,
                f"the quality access log cannot be used ({contents.kind.value}): {contents.detail}",
            )
        appended = self._log.append(start)
        if appended != start:
            detail = getattr(appended, "detail", "the row was not recorded")
            return _refused(
                QualityPermitRefusal.RECORD_FAILED,
                f"the start row was not recorded; the read does not start: {detail}",
            )
        return permit

    def finish_quality_read(
        self,
        permit: QualityReadPermit,
        *,
        result: QualityAccessResult,
        row_id: str,
        finished_at: UtcTime,
    ) -> QualityAccessEnd | QualityReadRefused:
        """閲覧の記録の終了の行を書く（ADR-0036 第7項の 4 段目）。"""
        if not isinstance(permit, QualityReadPermit):
            raise EvaluationError("finish_quality_read requires a QualityReadPermit")
        try:
            end = QualityAccessEnd(
                row_id=row_id,
                access_id=permit.access_id,
                finished_at=finished_at,
                result=result,
                record_kind=QualityRecordKind.LIVE,
            )
        except ValueError as exc:
            return _refused(QualityPermitRefusal.RECORD_FAILED, f"{type(exc).__name__}: {exc}")
        appended = self._log.append(end)
        if appended != end:
            detail = getattr(appended, "detail", "the row was not recorded")
            return _refused(
                QualityPermitRefusal.RECORD_FAILED,
                f"the end row was not recorded; no result is reported: {detail}",
            )
        return end


# --- 最終評価の許可（D09 v0.5 §9.10。R4）---------------------------------------------------


def final_partitions_match_permit(
    permit: FinalEvaluationPermit,
    allowed: Paired[frozenset[tuple[str, PartitionId]]],
    record_snapshots: Paired[str],
    conversion_snapshots: Paired[str] | None = None,
) -> WindowRefusal | None:
    """研究ポリシーの検査 P2F（`final_partitions_match_permit`）の (1)・(3)（D09 §9.10.8）。

    (1) 組ごとの許可集合が、研究履歴の partition と許可の値の並びの partition の和に等しい（それ
    以外の封印の区分・隔離の区分・旧データの 2024 年以降の partition を含まない。並びは消費の対象
    と換算用の読み取りの対象のうちその組の run が読む部分。D06 v1.20 案 §19.14）。(3) 研究履歴の
    partition は記録票の snapshot のものだけ。記録票の snapshot は、取引する通貨ペアの snapshot
    （`record_snapshots`）と、記録票の換算の snapshot（`conversion_snapshots`。円以外の決済通貨の
    候補だけ。人間の決定 (23) (a)）。合格なら `None`。
    """
    for variant in VARIANTS:
        expected = permit.allowed_set(variant)
        given = allowed.of(variant)
        if given != expected:
            extra = sorted(f"{snapshot}:{pid}" for snapshot, pid in given - expected)
            lacking = sorted(f"{snapshot}:{pid}" for snapshot, pid in expected - given)
            return WindowRefusal(
                FinalReason.P2F_NOT_PASSED,
                f"P2F (1): the {variant.value} allowed set differs from the permit"
                f" (extra {extra[:3]}, lacking {lacking[:3]}. D09 §9.10.8)",
            )
        for snapshot, partition_id in permit.research_partitions.of(variant):
            if partition_id.access_class is not AccessClass.RESEARCH_HISTORY:
                return WindowRefusal(
                    FinalReason.P2F_NOT_PASSED,
                    f"P2F (1): {partition_id} of the {variant.value} run is neither research"
                    " history nor a partition of the permit (D09 §9.10.8)",
                )
            recorded = {record_snapshots.of(variant)}
            if conversion_snapshots is not None:
                recorded.add(conversion_snapshots.of(variant))
            if snapshot not in recorded:
                return WindowRefusal(
                    FinalReason.P2F_NOT_PASSED,
                    f"P2F (3): the research-history partition {partition_id} of the"
                    f" {variant.value} run is read from {snapshot}, not from the snapshots of the"
                    f" manifest {sorted(recorded)} (D09 §9.10.8、人間の決定 (23) (a))",
                )
    return None


def consumption_rows_present(
    permit: FinalEvaluationPermit,
    rows_on_origin: Mapping[tuple[str, str], frozenset[str]],
    conversion_rows_on_origin: Mapping[tuple[str, str], frozenset[str]] | None = None,
) -> WindowRefusal | None:
    """P2F の (2): 消費の対象のすべての partition に、この許可の識別子の消費の行が origin の
    既定ブランチ上にある（`rows_on_origin` は `(snapshot_id, partition)` → 消費の行の許可の
    識別子の集合）。換算用の読み取りの対象のすべての partition には、この許可の換算用の読み取りの
    行（`CONVERSION_READ`）が origin の既定ブランチ上にある（`conversion_rows_on_origin`。
    D06 v1.20 案 §19.14 の P2F の (2)）。"""
    missing = sorted(
        f"{snapshot}:{partition_id}"
        for snapshot, partition_id in permit.window.targets
        if permit.permit_id not in rows_on_origin.get((snapshot, str(partition_id)), frozenset())
    )
    if missing:
        return WindowRefusal(
            FinalReason.RECORD_NOT_ON_ORIGIN,
            f"P2F (2): {len(missing)} target partition(s) have no consumption row of the permit"
            f" {permit.permit_id} on origin (first: {missing[0]}. D09 §9.10.8)",
        )
    conversion_rows = conversion_rows_on_origin or {}
    unread = sorted(
        f"{snapshot}:{partition_id}"
        for snapshot, partition_id in permit.window.conversion
        if permit.permit_id not in conversion_rows.get((snapshot, str(partition_id)), frozenset())
    )
    if unread:
        return WindowRefusal(
            FinalReason.RECORD_NOT_ON_ORIGIN,
            f"P2F (2): {len(unread)} conversion-read partition(s) have no conversion-read row of"
            f" the permit {permit.permit_id} on origin (first: {unread[0]}. D06 §19.14)",
        )
    return None


def issue_final_evaluation_permit(
    *,
    permit_id: str,
    candidate: CandidateKey,
    t_fix: UtcTime,
    length_days: int,
    window: FinalWindow,
    predicted: Paired[PredictedRun],
    purpose: str,
    research_partitions: Paired[tuple[tuple[str, PartitionId], ...]],
    states: Mapping[tuple[str, str], HoldoutState],
) -> FinalEvaluationPermit | WindowRefusal:
    """関所の最終評価の許可を発行する（D09 §9.10.2 の段 3。ADR-0036 第3項の「別の型・別の手順」）。

    呼び出し側（`app.final_validation`）が段 1・段 2 の確かめ（申請の資格・origin の確かめ・
    窓の導出・出力先・能力検査・再計算・run の予測）を終えた後にだけ呼ぶ。ここでは段 2 (d)
    （消費の対象がすべて `SEALED`。`states` は origin の既定ブランチ上の消費の記録から導いた
    `(snapshot_id, partition)` の状態）を確かめ、許可の値を組み立てる。許可の発行の行は書かない
    （仮置き R2-2）。許可は控えとロックとして手元にだけ持つ。
    """
    # 換算用の読み取りの対象も `SEALED` であること（`CONSUMED` は換算にも読まない。D06 v1.20 案
    # §19.14。やり直しはこの許可の換算用の読み取りの行がある partition を `SEALED` と読む。
    # 決定 (24)）。
    not_sealed = sorted(
        f"{snapshot}:{partition_id}"
        for snapshot, partition_id in (*window.targets, *window.conversion)
        if states.get((snapshot, str(partition_id))) is not HoldoutState.SEALED
    )
    if not_sealed:
        return WindowRefusal(
            FinalReason.NOT_SEALED,
            f"{len(not_sealed)} target partition(s) are not SEALED on origin (first:"
            f" {not_sealed[0]}. D09 §9.10.2 の段 2 (d))",
        )
    return FinalEvaluationPermit(
        permit_id=permit_id,
        candidate=candidate,
        t_fix=t_fix,
        length_days=length_days,
        window=window,
        predicted=predicted,
        purpose=purpose,
        research_partitions=research_partitions,
    )
