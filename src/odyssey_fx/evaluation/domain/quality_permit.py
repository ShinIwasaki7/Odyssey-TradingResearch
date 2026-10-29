"""品質確認専用の許可の定義と要求（ADR-0036 第3項・第4項）。

関所（`evaluation.application.holdout_gate`）は、最終評価の許可（ADR-0014・D09 の後続版）とは
**別の型・別の手順**として品質確認専用の許可を持つ（ADR-0036 第3項。P1-3 の「最終評価用の
許可に目的名だけを追加する設計では不十分」）。

**許可の定義**（`QualityCheckDefinition`）: 品質確認の道具ごとに版管理の定義ファイル
（`configs/quality_checks/<道具の名前>.yaml`）を置き、人間が PR の merge で承認する（ADR-0036
第4項。PR #80 の仮置き 6 の決定）。定義が覆ってよいのは、制約の改定文（ADR-0037）が取得と封印の
取り込みを許す範囲（OANDA MT5 の 1 分足と、それから作る partition）の中だけで、許す品質確認は
封印の取り込みの受入れの検査だけ（ADR-0037 第9項、D03 §3.8.1 の注記）。出力は件数・時刻の範囲・
区間・ハッシュ・合否だけ（ADR-0037 第1項の 4）。本モジュールの型はこれらを構築時に確かめる。

**要求**（`QualityCheckRequest`）: 実行しようとする道具の名前と版・設定のダイジェスト・要求する
区分と期間・対象。関所は要求が承認済みの定義と一致することを確かめてから許可を出す。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.evaluation.domain.errors import EvaluationError
from odyssey_fx.marketdata.domain.access import (
    OANDA_SEALED_CLASSES,
    SEALED_RESEARCH_UNTIL,
    AccessClass,
)
from odyssey_fx.marketdata.domain.quality_access import (
    SEALED_INTAKE_OUTPUT_KINDS,
    AccessPeriod,
    PartitionTarget,
    QualityCheckKind,
    QualityOutputKind,
    QualityTarget,
    RawFileTarget,
    ToolRef,
)

__all__ = [
    "SEALED_INTAKE_DATASOURCE",
    "FinalEvaluationPermitNotImplemented",
    "QualityCheckDefinition",
    "QualityCheckRequest",
    "QualityPermitRefusal",
    "QualityReadRefused",
    "QualityTargetKind",
]

#: 許可の定義が覆ってよいデータ源（OANDA MT5 API の 1 分足の列対応の宣言。ADR-0037 第1項の 2）。
SEALED_INTAKE_DATASOURCE: Final = "oanda_mt5_m1"


class FinalEvaluationPermitNotImplemented(EvaluationError):
    """最終評価の許可は D09 の後続版（D15 の R2）の承認まで実装しない（ADR-0036 第11項）。

    呼ばれたら拒否する（実装は D15 の R4）。
    """


class QualityTargetKind(Enum):
    """許可の定義が覆う対象の種類（ADR-0036 第4項の「partition か原ファイルか」）。"""

    RAW_FILE = "RAW_FILE"
    PARTITION = "PARTITION"


def _require_hex64(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise EvaluationError(f"{label} must be 64 lowercase hex digits")


def _require_classes(classes: object, label: str) -> None:
    if (
        not isinstance(classes, frozenset)
        or not classes
        or not all(isinstance(item, AccessClass) for item in classes)
    ):
        raise EvaluationError(f"{label} must be a non-empty frozenset of AccessClass")


@dataclass(frozen=True, slots=True)
class QualityCheckDefinition:
    """承認済みの品質確認の道具の許可の定義（ADR-0036 第4項）。

    `source` は定義ファイルの置き場（拒否の理由に示すためのもの。照合には使わない）。
    """

    tool: ToolRef
    config_digest: str
    datasource: str
    check_kind: QualityCheckKind
    classes: frozenset[AccessClass]
    period: AccessPeriod
    target_kind: QualityTargetKind
    symbols: frozenset[Symbol]
    timeframes: frozenset[str]
    output_kinds: frozenset[QualityOutputKind]
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.tool, ToolRef):
            raise EvaluationError("QualityCheckDefinition.tool must be a ToolRef")
        _require_hex64(self.config_digest, "QualityCheckDefinition.config_digest")
        if self.datasource != SEALED_INTAKE_DATASOURCE:
            raise EvaluationError(
                f"a quality-check permit may only cover the OANDA MT5 one-minute data"
                f" ({SEALED_INTAKE_DATASOURCE}), got {self.datasource!r} (ADR-0036 第4項、"
                " ADR-0037 第1項の 2。旧データの CSV の 2024 年以降は対象にしない)"
            )
        if self.check_kind is not QualityCheckKind.ACCEPTANCE:
            raise EvaluationError(
                "the only quality check allowed on data from 2024 is the acceptance of the"
                f" sealed intake, got {self.check_kind} (ADR-0037 第9項、D03 §3.8.1)"
            )
        _require_classes(self.classes, "QualityCheckDefinition.classes")
        outside = sorted(item.value for item in self.classes if item not in OANDA_SEALED_CLASSES)
        if outside:
            raise EvaluationError(
                f"a quality-check permit may only cover the two OANDA MT5 sealed classes, got"
                f" {outside} (隔離の区分は隔離を保つ。ADR-0036 第4項・第6項、ADR-0038 第6項)"
            )
        if not isinstance(self.period, AccessPeriod):
            raise EvaluationError("QualityCheckDefinition.period must be an AccessPeriod")
        if self.period.start < SEALED_RESEARCH_UNTIL:
            raise EvaluationError(
                f"the period of a quality-check permit starts at {SEALED_RESEARCH_UNTIL} or"
                f" later, got {self.period.start} (ADR-0037 第2項の 1)"
            )
        if not isinstance(self.target_kind, QualityTargetKind):
            raise EvaluationError("QualityCheckDefinition.target_kind must be a QualityTargetKind")
        if (
            not isinstance(self.symbols, frozenset)
            or not self.symbols
            or not all(isinstance(item, Symbol) for item in self.symbols)
        ):
            raise EvaluationError("QualityCheckDefinition.symbols must be a non-empty frozenset")
        if (
            not isinstance(self.timeframes, frozenset)
            or not self.timeframes
            or not all(isinstance(item, str) and item for item in self.timeframes)
        ):
            raise EvaluationError("QualityCheckDefinition.timeframes must be a non-empty frozenset")
        if (
            not isinstance(self.output_kinds, frozenset)
            or not self.output_kinds
            or not all(isinstance(item, QualityOutputKind) for item in self.output_kinds)
        ):
            raise EvaluationError("QualityCheckDefinition.output_kinds must be a non-empty set")
        extra = sorted(
            kind.value for kind in self.output_kinds if kind not in SEALED_INTAKE_OUTPUT_KINDS
        )
        if extra:
            raise EvaluationError(
                f"the sealed intake may only output counts, times, intervals, hashes and"
                f" pass/fail, got {extra} (ADR-0037 第1項の 4)"
            )
        if not isinstance(self.source, str) or not self.source:
            raise EvaluationError("QualityCheckDefinition.source must be a non-empty str")


@dataclass(frozen=True, slots=True)
class QualityCheckRequest:
    """品質確認の読み取りの要求（道具の名前と版・設定のダイジェスト・区分と期間・対象）。"""

    tool: ToolRef
    config_digest: str
    check_kind: QualityCheckKind
    classes: frozenset[AccessClass]
    period: AccessPeriod
    targets: tuple[QualityTarget, ...]
    output_kinds: frozenset[QualityOutputKind]

    def __post_init__(self) -> None:
        if not isinstance(self.tool, ToolRef):
            raise EvaluationError("QualityCheckRequest.tool must be a ToolRef")
        _require_hex64(self.config_digest, "QualityCheckRequest.config_digest")
        if not isinstance(self.check_kind, QualityCheckKind):
            raise EvaluationError("QualityCheckRequest.check_kind must be a QualityCheckKind")
        _require_classes(self.classes, "QualityCheckRequest.classes")
        if not isinstance(self.period, AccessPeriod):
            raise EvaluationError("QualityCheckRequest.period must be an AccessPeriod")
        if (
            not isinstance(self.targets, tuple)
            or not self.targets
            or not all(isinstance(item, PartitionTarget | RawFileTarget) for item in self.targets)
        ):
            raise EvaluationError("QualityCheckRequest.targets must be a non-empty tuple")
        if (
            not isinstance(self.output_kinds, frozenset)
            or not self.output_kinds
            or not all(isinstance(item, QualityOutputKind) for item in self.output_kinds)
        ):
            raise EvaluationError("QualityCheckRequest.output_kinds must be a non-empty set")


class QualityPermitRefusal(Enum):
    """品質確認の許可を出さなかった・記録に失敗した理由（ADR-0036 第7項）。"""

    #: 要求の道具の名前と版に一致する承認済みの定義が無い。
    NO_DEFINITION = "NO_DEFINITION"
    #: 設定のダイジェスト・品質確認の種類・出力の種類が定義と一致しない。
    DEFINITION_MISMATCH = "DEFINITION_MISMATCH"
    #: 要求の区分・期間・対象が定義の外にある。
    OUTSIDE_DEFINITION = "OUTSIDE_DEFINITION"
    #: 隔離の区分を要求した（品質確認の許可があっても隔離を保つ。ADR-0036 第6項）。
    QUARANTINED = "QUARANTINED"
    #: 閲覧の記録が読めない・構造の誤りがある・追記に失敗した（読み取りを始めない）。
    RECORD_FAILED = "RECORD_FAILED"


@dataclass(frozen=True, slots=True)
class QualityReadRefused:
    """品質確認の読み取りを始めない（または終了の行を書けなかった）こと。"""

    kind: QualityPermitRefusal
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, QualityPermitRefusal):
            raise EvaluationError("QualityReadRefused.kind must be a QualityPermitRefusal")
        if not isinstance(self.detail, str) or not self.detail:
            raise EvaluationError("QualityReadRefused.detail must be a non-empty str")
