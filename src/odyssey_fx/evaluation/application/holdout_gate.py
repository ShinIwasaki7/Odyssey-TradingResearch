"""読み取りの関所（`holdout_gate`）の品質確認専用の許可（ADR-0036、ADR-0035 第4項）。

関所は 2 種類の許可を**別の型・別の手順**として持つ（ADR-0036 第3項）。順 6 の S3 では
**品質確認の許可だけ**を実装し、最終評価の許可は D09 の後続版（D15 の R2）の承認まで実装しない
（呼ばれたら拒否する。実装は D15 の R4。ADR-0036 第11項）。

品質確認の読み取りの手順（fail-closed。ADR-0036 第7項）:

1. **許可の確認**: 要求の道具の名前と版・設定のダイジェスト・区分と期間・対象が、承認済みの
   定義（`configs/quality_checks/`）と一致することを確かめる。
2. **閲覧の記録**: 記録（`QualityAccessLog`）を読み、構造の誤りが無いことを確かめ、開始の行を
   追記して読み直す。どこかで失敗すれば許可を出さない（読み取りを始めない）。
3. 読み取り（`app` の品質確認専用の入口が、本モジュールの返す読み取りの許可の値を
   `marketdata` の道具へ渡す）。
4. **終了の行**: 道具の終了（完了・途中で停止）を受け取り、終了の行を追記する。書けなければ
   失敗を返す（入口は結果を出さずに失敗として終わる。PR #80 の仮置き 13）。

消費の記録（`access_log.jsonl`）は書かない。`HoldoutState` は変わらない。品質確認の許可で
最終評価・run・探索の実験の入力は作れない（読み取りの許可の値は as-of ビューの許可集合へ
渡す型ではない。ADR-0036 第5項）。

識別子（許可・閲覧・行）と時刻は `app` が作って渡す（application は乱数と実時計を読まない。
D01 §2.2 規則2）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import NoReturn

from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.application.ports import QualityAccessLog
from odyssey_fx.evaluation.domain.errors import EvaluationError
from odyssey_fx.evaluation.domain.quality_permit import (
    SEALED_RAW_ROOT,
    FinalEvaluationPermitNotImplemented,
    QualityCheckDefinition,
    QualityCheckRequest,
    QualityPermitRefusal,
    QualityReadRefused,
    QualityTargetKind,
)
from odyssey_fx.marketdata.domain.access import AccessClass
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

__all__ = ["HoldoutGate"]


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
        return _raw_location_problem(target)
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


def _raw_location_problem(target: RawFileTarget) -> str | None:
    """原ファイルが封印の原ファイルの置き場にあり、その通貨の場所にあるか（ADR-0036 第4項）。

    置き場は `data/raw/oanda_mt5_sealed/<取得日>/<通貨>/<ファイル>`（D03 §16 の PR #109 の
    仮置き S2b-6）。この外のファイル（旧データの CSV `data/raw/market/`、HistData の 1 分足
    `data/raw/histdata/`、研究期間の `data/raw/oanda_mt5/` など）には許可を出さない。
    """
    location = target.location
    parts = PurePosixPath(location).parts
    root = len(SEALED_RAW_ROOT)
    if (
        location.startswith("/")
        or "\\" in location
        or any(part in ("", ".", "..") for part in location.split("/"))
        or len(parts) < root + 3
        or parts[:root] != SEALED_RAW_ROOT
    ):
        return (
            f"the raw file {location} is not under {'/'.join(SEALED_RAW_ROOT)}/<date>/<symbol>/;"
            " only the OANDA MT5 sealed raw files may be covered (ADR-0036 第4項)"
        )
    if parts[root + 1] != str(target.symbol):
        return f"the raw file {location} is not in the directory of its symbol {target.symbol}"
    return None


class HoldoutGate:
    """読み取りの関所（ADR-0036）。品質確認の許可だけを出す（最終評価の許可は R4）。"""

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

    def request_final_evaluation_permit(self, *args: object, **kwargs: object) -> NoReturn:
        """最終評価の許可は実装しない（D09 の後続版の承認まで。ADR-0036 第11項）。常に拒否する。"""
        raise FinalEvaluationPermitNotImplemented(
            "the final-evaluation permit of the holdout gate is not implemented until the next"
            " version of D09 is approved (D15 の R2・R4, ADR-0036 第11項); nothing was read"
        )
