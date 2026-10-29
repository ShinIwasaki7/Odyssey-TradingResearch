"""品質確認の閲覧の記録の行と、読み取りの許可の値（ADR-0035 第4項、ADR-0036 第9項）。

2024 年以降の区分（封印の区分と隔離の区分。`PROTECTED_CLASSES`）の品質確認の閲覧は、
リポジトリの根の `research/quality_access_log.jsonl` に 1 行 1 JSON で必ず記録する
（ADR-0035 第4項の 5）。消費の記録（snapshot の `access_log.jsonl`）とは別の記録で、
`HoldoutState` の導出に使わない（P1-3）。

1 回の閲覧は 2 行で記録する（ADR-0035 第4項の 6）。

- **開始の行**（`QualityAccessStart`）: 読み取りの前に書く。日時・道具と版・区分と期間・
  出力の種類（決定の 4 項目）と、許可の識別子・対象・品質確認の種類・記録の種別。
- **終了の行**（`QualityAccessEnd`）: 読み取りの後に書く。結果（完了・途中で停止）と記録の種別。
- **判定の行**（`QualityAccessJudgment`）: 後から人間が品質確認の条件違反と判定した事実
  （ADR-0035 第3項。第 3 の種別）。

2 行を結ぶのは閲覧の識別子（`access_id`）だけで、各行は自分の行の識別子（`row_id`）を持つ。
読込が構造の誤りとして止める条件は `quality_log_defect`。

**読み取りの許可の値**（`QualityReadPermit`）は、関所（`evaluation.application.holdout_gate`）が
品質確認の許可と開始の行の記録に成功した後に作り、`app` の品質確認専用の入口が `marketdata`
の受入れ・品質確認の道具に渡す（ADR-0036 第9項の 2）。`marketdata` は、この値が覆わない 2024 年
以降の partition を読まない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.access import (
    OANDA_SEALED_CLASSES,
    PROTECTED_CLASSES,
    AccessClass,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.sealed_state import ViolationScope
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = [
    "PRE_PERMIT_MECHANISM",
    "SEALED_INTAKE_OUTPUT_KINDS",
    "VIOLABLE_CONDITIONS",
    "AccessPeriod",
    "PartitionTarget",
    "QualityAccessEnd",
    "QualityAccessJudgment",
    "QualityAccessLogContents",
    "QualityAccessLogFailure",
    "QualityLogAppendRefusal",
    "QualityLogAppendRefused",
    "QualityAccessResult",
    "QualityAccessRow",
    "QualityAccessStart",
    "QualityCheckKind",
    "QualityLogDefect",
    "QualityOutputKind",
    "QualityReadPermit",
    "QualityRecordKind",
    "QualityTarget",
    "RawFileTarget",
    "ToolRef",
    "completed_access_ids",
    "quality_log_defect",
    "violation_scopes",
]

#: 許可の仕組みができる前の閲覧の許可の識別子（ADR-0035 第5項。監査 PR #76 の 1 件だけに使う）。
PRE_PERMIT_MECHANISM: Final = "許可の仕組みの前の閲覧"

#: 後から違反と判定できる品質確認の条件（ADR-0035 第3項の条件 1・3・6）。
VIOLABLE_CONDITIONS: Final = frozenset({1, 3, 6})


class QualityCheckKind(Enum):
    """品質確認の種類（ADR-0035 第4項の 6「受入れ・欠落一覧・区間台帳・照合など」）。"""

    ACCEPTANCE = "ACCEPTANCE"
    GAP_LIST = "GAP_LIST"
    INTERVAL_LEDGER = "INTERVAL_LEDGER"
    RECONCILIATION = "RECONCILIATION"
    #: 監査（時刻と件数の数え上げ）。監査 PR #76 の後からの記録（ADR-0035 第5項）。
    AUDIT_COUNT = "AUDIT_COUNT"


class QualityOutputKind(Enum):
    """品質確認の閲覧が出力してよいものの種類（ADR-0035 第2項の表）。"""

    COUNTS = "COUNTS"
    TIMES = "TIMES"
    INTERVALS = "INTERVALS"
    HASHES = "HASHES"
    PASS_FAIL = "PASS_FAIL"
    MATCH_COUNTS = "MATCH_COUNTS"
    EVIDENCE = "EVIDENCE"


#: 2024 年以降の取得と取り込みが出してよいもの（件数・時刻の範囲・区間・ハッシュ・合否だけ。
#: ADR-0037 第1項の 4）。OANDA MT5 の経路の封印の区分の許可はこの範囲に限る。
SEALED_INTAKE_OUTPUT_KINDS: Final = frozenset(
    {
        QualityOutputKind.COUNTS,
        QualityOutputKind.TIMES,
        QualityOutputKind.INTERVALS,
        QualityOutputKind.HASHES,
        QualityOutputKind.PASS_FAIL,
    }
)


class QualityRecordKind(Enum):
    """記録の種別（ADR-0035 第4項の 6）。"""

    #: その場の記録（読み取りの前後に関所が書く）。
    LIVE = "LIVE"
    #: 後から記録したもの（監査 PR #76 の閲覧。ADR-0035 第5項）。
    RETROACTIVE = "RETROACTIVE"


class QualityAccessResult(Enum):
    """閲覧の結果（終了の行。ADR-0035 第4項の 6）。"""

    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


def _require_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MarketDataValueError(f"{label} must be a non-empty str without surrounding spaces")


@dataclass(frozen=True, slots=True)
class ToolRef:
    """品質確認の道具と版（ADR-0036 第4項）。"""

    name: str
    version: str

    def __post_init__(self) -> None:
        _require_text(self.name, "ToolRef.name")
        _require_text(self.version, "ToolRef.version")


@dataclass(frozen=True, slots=True)
class AccessPeriod:
    """対象の期間 `[start, end)`。`end` が `None` なら終わりを置かない（「2024 年以降」）。"""

    start: UtcTime
    end: UtcTime | None

    def __post_init__(self) -> None:
        if not isinstance(self.start, UtcTime):
            raise MarketDataValueError("AccessPeriod.start must be a UtcTime")
        if self.end is not None:
            if not isinstance(self.end, UtcTime):
                raise MarketDataValueError("AccessPeriod.end must be a UtcTime or None")
            if not self.start < self.end:
                raise MarketDataValueError(
                    f"AccessPeriod requires start < end, got [{self.start}, {self.end})"
                )

    def contains_interval(self, interval: Interval) -> bool:
        """`interval` がこの期間の内側にあるか。"""
        if interval.start < self.start:
            return False
        return self.end is None or interval.end <= self.end

    def contains_period(self, other: AccessPeriod) -> bool:
        """期間 `other` がこの期間の内側にあるか。"""
        if other.start < self.start:
            return False
        if self.end is None:
            return True
        return other.end is not None and other.end <= self.end


@dataclass(frozen=True, slots=True)
class PartitionTarget:
    """対象の partition（識別子と、その partition の期間）。"""

    partition_id: PartitionId
    interval: Interval

    def __post_init__(self) -> None:
        if not isinstance(self.partition_id, PartitionId):
            raise MarketDataValueError("PartitionTarget.partition_id must be a PartitionId")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("PartitionTarget.interval must be an Interval")
        period = self.partition_id.period
        if period is not None and period != self.interval:
            raise MarketDataValueError(
                f"PartitionTarget.interval {self.interval} differs from the trading week"
                f" {period} of {self.partition_id}"
            )


@dataclass(frozen=True, slots=True)
class RawFileTarget:
    """対象の原ファイル（置き場とハッシュ。ADR-0035 第4項の 6）。

    `sha256` はファイル全体の SHA-256（小文字の 16 進 64 文字）。後から記録した行
    （`RETROACTIVE`）だけは、ハッシュが記録に無いとき `None` を許す（`QualityAccessStart`）。
    `symbol` はファイルの銘柄（分からなければ `None`）。
    """

    location: str
    sha256: str | None
    symbol: Symbol | None

    def __post_init__(self) -> None:
        _require_text(self.location, "RawFileTarget.location")
        if self.sha256 is not None and (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise MarketDataValueError("RawFileTarget.sha256 must be 64 lowercase hex digits")
        if self.symbol is not None and not isinstance(self.symbol, Symbol):
            raise MarketDataValueError("RawFileTarget.symbol must be a Symbol or None")


QualityTarget = PartitionTarget | RawFileTarget


def _require_classes(classes: object, label: str, allowed: frozenset[AccessClass]) -> None:
    if not isinstance(classes, frozenset) or not classes:
        raise MarketDataValueError(f"{label} must be a non-empty frozenset of AccessClass")
    if not all(isinstance(item, AccessClass) for item in classes):
        raise MarketDataValueError(f"{label} must contain AccessClass")
    outside = sorted(item.value for item in classes if item not in allowed)
    if outside:
        raise MarketDataValueError(f"{label} contains classes outside the allowed set: {outside}")


def _require_output_kinds(kinds: object, label: str) -> None:
    if not isinstance(kinds, frozenset) or not kinds:
        raise MarketDataValueError(f"{label} must be a non-empty frozenset of QualityOutputKind")
    if not all(isinstance(item, QualityOutputKind) for item in kinds):
        raise MarketDataValueError(f"{label} must contain QualityOutputKind")


def _require_targets(targets: object, label: str) -> None:
    if not isinstance(targets, tuple) or not targets:
        raise MarketDataValueError(f"{label} must be a non-empty tuple of targets")
    if not all(isinstance(item, PartitionTarget | RawFileTarget) for item in targets):
        raise MarketDataValueError(f"{label} must contain PartitionTarget or RawFileTarget")


@dataclass(frozen=True, slots=True)
class QualityAccessStart:
    """開始の行（読み取りの前に書く。ADR-0035 第4項の 6 の「必須の記録」）。"""

    row_id: str
    access_id: str
    accessed_at: UtcTime
    tool: ToolRef
    classes: frozenset[AccessClass]
    period: AccessPeriod
    output_kinds: frozenset[QualityOutputKind]
    permit_id: str
    targets: tuple[QualityTarget, ...]
    check_kind: QualityCheckKind
    record_kind: QualityRecordKind

    def __post_init__(self) -> None:
        _require_text(self.row_id, "QualityAccessStart.row_id")
        _require_text(self.access_id, "QualityAccessStart.access_id")
        if not isinstance(self.accessed_at, UtcTime):
            raise MarketDataValueError("QualityAccessStart.accessed_at must be a UtcTime")
        if not isinstance(self.tool, ToolRef):
            raise MarketDataValueError("QualityAccessStart.tool must be a ToolRef")
        _require_classes(self.classes, "QualityAccessStart.classes", PROTECTED_CLASSES)
        if not isinstance(self.period, AccessPeriod):
            raise MarketDataValueError("QualityAccessStart.period must be an AccessPeriod")
        _require_output_kinds(self.output_kinds, "QualityAccessStart.output_kinds")
        _require_text(self.permit_id, "QualityAccessStart.permit_id")
        _require_targets(self.targets, "QualityAccessStart.targets")
        if not isinstance(self.check_kind, QualityCheckKind):
            raise MarketDataValueError("QualityAccessStart.check_kind must be a QualityCheckKind")
        if not isinstance(self.record_kind, QualityRecordKind):
            raise MarketDataValueError("QualityAccessStart.record_kind must be a QualityRecordKind")
        if self.record_kind is QualityRecordKind.LIVE:
            if self.permit_id == PRE_PERMIT_MECHANISM:
                raise MarketDataValueError(
                    "a live access must name the permit it went through; the pre-permit"
                    " exception is limited to the audit of PR #76 (ADR-0035 第5項)"
                )
            unhashed = [
                target.location
                for target in self.targets
                if isinstance(target, RawFileTarget) and target.sha256 is None
            ]
            if unhashed:
                raise MarketDataValueError(
                    f"a live access must record the hash of every raw file, missing for {unhashed}"
                )


@dataclass(frozen=True, slots=True)
class QualityAccessEnd:
    """終了の行（読み取りの後に書く。ADR-0035 第4項の 6）。"""

    row_id: str
    access_id: str
    finished_at: UtcTime
    result: QualityAccessResult
    record_kind: QualityRecordKind

    def __post_init__(self) -> None:
        _require_text(self.row_id, "QualityAccessEnd.row_id")
        _require_text(self.access_id, "QualityAccessEnd.access_id")
        if not isinstance(self.finished_at, UtcTime):
            raise MarketDataValueError("QualityAccessEnd.finished_at must be a UtcTime")
        if not isinstance(self.result, QualityAccessResult):
            raise MarketDataValueError("QualityAccessEnd.result must be a QualityAccessResult")
        if not isinstance(self.record_kind, QualityRecordKind):
            raise MarketDataValueError("QualityAccessEnd.record_kind must be a QualityRecordKind")


@dataclass(frozen=True, slots=True)
class QualityAccessJudgment:
    """判定の行（後から人間が条件違反と判定した事実。ADR-0035 第3項）。"""

    row_id: str
    access_id: str
    violated_conditions: frozenset[int]
    judged_at: UtcTime
    judged_by: str

    def __post_init__(self) -> None:
        _require_text(self.row_id, "QualityAccessJudgment.row_id")
        _require_text(self.access_id, "QualityAccessJudgment.access_id")
        conditions = self.violated_conditions
        if (
            not isinstance(conditions, frozenset)
            or not conditions
            or not conditions <= VIOLABLE_CONDITIONS
            or any(isinstance(item, bool) for item in conditions)
        ):
            raise MarketDataValueError(
                "QualityAccessJudgment.violated_conditions must be a non-empty subset of"
                " {1, 3, 6} (ADR-0035 第3項)"
            )
        if not isinstance(self.judged_at, UtcTime):
            raise MarketDataValueError("QualityAccessJudgment.judged_at must be a UtcTime")
        _require_text(self.judged_by, "QualityAccessJudgment.judged_by")


QualityAccessRow = QualityAccessStart | QualityAccessEnd | QualityAccessJudgment


class QualityLogDefect(Enum):
    """品質確認の閲覧の記録が読めない・構造の誤りがある理由（ADR-0035 第4項の 5・6）。"""

    #: 記録のファイルが無い（追記で作らない。S3 が版管理で置く。第4項の 5 の 5）。
    FILE_MISSING = "FILE_MISSING"
    #: 記録のファイルを読めない。
    FILE_UNREADABLE = "FILE_UNREADABLE"
    #: 改行で終わる行が JSON として読めない・行の形が合わない。
    LINE_CORRUPT = "LINE_CORRUPT"
    #: 行の識別子の重複。
    DUPLICATE_ROW_ID = "DUPLICATE_ROW_ID"
    #: 同じ閲覧の識別子の開始の行が 2 行以上。
    DUPLICATE_START = "DUPLICATE_START"
    #: 同じ閲覧の識別子の終了の行が 2 行以上。
    DUPLICATE_END = "DUPLICATE_END"
    #: 開始の行の無い終了の行。
    END_WITHOUT_START = "END_WITHOUT_START"
    #: 開始の行の無い判定の行。
    JUDGMENT_WITHOUT_START = "JUDGMENT_WITHOUT_START"
    #: 同じ閲覧の識別子の判定の行が 2 行以上。
    DUPLICATE_JUDGMENT = "DUPLICATE_JUDGMENT"
    #: 同じ閲覧の開始の行と終了の行で記録の種別が違う。
    RECORD_KIND_MISMATCH = "RECORD_KIND_MISMATCH"


@dataclass(frozen=True, slots=True)
class QualityAccessLogContents:
    """記録を読んだ結果。`torn_tail` は末尾に改行で終わらない断片があったか（行に入れない）。"""

    rows: tuple[QualityAccessRow, ...]
    torn_tail: bool

    def __post_init__(self) -> None:
        if not isinstance(self.rows, tuple) or not all(
            isinstance(row, QualityAccessStart | QualityAccessEnd | QualityAccessJudgment)
            for row in self.rows
        ):
            raise MarketDataValueError("QualityAccessLogContents.rows must be a tuple of rows")
        if not isinstance(self.torn_tail, bool):
            raise MarketDataValueError("QualityAccessLogContents.torn_tail must be a bool")


@dataclass(frozen=True, slots=True)
class QualityAccessLogFailure:
    """記録が読めない・構造の誤りがある（新しい品質確認の読み取りを始めない）。"""

    kind: QualityLogDefect
    line_number: int | None
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, QualityLogDefect):
            raise MarketDataValueError("QualityAccessLogFailure.kind must be a QualityLogDefect")
        if self.line_number is not None and (
            isinstance(self.line_number, bool)
            or not isinstance(self.line_number, int)
            or self.line_number < 1
        ):
            raise MarketDataValueError("QualityAccessLogFailure.line_number must be an int >= 1")
        _require_text(self.detail, "QualityAccessLogFailure.detail")


class QualityLogAppendRefusal(Enum):
    """記録への追記を断った理由（ADR-0035 第4項の 4・5）。どれも「記録に失敗した」に当たる。"""

    #: ロックを作れない（追記中の書き手がいる、または止まった書き手のロックが残っている）。
    LOCKED = "LOCKED"
    #: 記録が無い・読めない・構造の誤りがある。
    READ_FAILED = "READ_FAILED"
    #: 足すと構造の誤りになる行（同じ閲覧の識別子の 2 行目の開始の行など）。
    ROW_REFUSED = "ROW_REFUSED"
    #: 書き込みか同期に失敗した。
    WRITE_FAILED = "WRITE_FAILED"
    #: 読み直した最後の行が書いた行と一致しない。
    VERIFY_FAILED = "VERIFY_FAILED"


@dataclass(frozen=True, slots=True)
class QualityLogAppendRefused:
    """記録への追記を断ったこと。`read_failure` は `READ_FAILED` のときだけ持つ。"""

    kind: QualityLogAppendRefusal
    detail: str
    read_failure: QualityAccessLogFailure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, QualityLogAppendRefusal):
            raise MarketDataValueError("QualityLogAppendRefused.kind must be a refusal kind")
        _require_text(self.detail, "QualityLogAppendRefused.detail")
        if (self.kind is QualityLogAppendRefusal.READ_FAILED) != isinstance(
            self.read_failure, QualityAccessLogFailure
        ):
            raise MarketDataValueError(
                "QualityLogAppendRefused.read_failure is set exactly when the kind is READ_FAILED"
            )


def quality_log_defect(
    rows: Sequence[QualityAccessRow],
) -> tuple[QualityLogDefect, int, str] | None:
    """行の列の構造の誤りを、先頭から最初に見つけた 1 件で返す（無ければ `None`）。

    ADR-0035 第4項の 6 の読込の検査: 行の識別子の重複、同じ閲覧の識別子の開始の行・終了の行・
    判定の行が 2 行以上、開始の行の無い終了の行・判定の行、開始の行と終了の行の記録の種別の
    食い違い。行番号は 1 始まり（記録のファイルの行の番号と同じ）。
    """
    row_ids: set[str] = set()
    starts: dict[str, QualityAccessStart] = {}
    ends: set[str] = set()
    judgments: set[str] = set()
    for number, row in enumerate(rows, start=1):
        if row.row_id in row_ids:
            return QualityLogDefect.DUPLICATE_ROW_ID, number, f"row id {row.row_id!r} repeats"
        row_ids.add(row.row_id)
        access = row.access_id
        if isinstance(row, QualityAccessStart):
            if access in starts:
                return (
                    QualityLogDefect.DUPLICATE_START,
                    number,
                    f"access {access!r} has a second start row",
                )
            starts[access] = row
        elif isinstance(row, QualityAccessEnd):
            start = starts.get(access)
            if start is None:
                return (
                    QualityLogDefect.END_WITHOUT_START,
                    number,
                    f"the end row of access {access!r} has no start row before it",
                )
            if access in ends:
                return (
                    QualityLogDefect.DUPLICATE_END,
                    number,
                    f"access {access!r} has a second end row",
                )
            if start.record_kind is not row.record_kind:
                return (
                    QualityLogDefect.RECORD_KIND_MISMATCH,
                    number,
                    f"access {access!r} is {start.record_kind.value} at the start and"
                    f" {row.record_kind.value} at the end",
                )
            ends.add(access)
        else:
            if access not in starts:
                return (
                    QualityLogDefect.JUDGMENT_WITHOUT_START,
                    number,
                    f"the judgment row of access {access!r} has no start row before it",
                )
            if access in judgments:
                return (
                    QualityLogDefect.DUPLICATE_JUDGMENT,
                    number,
                    f"access {access!r} has a second judgment row",
                )
            judgments.add(access)
    return None


def completed_access_ids(rows: Sequence[QualityAccessRow]) -> frozenset[str]:
    """「完了」の終了の行がある閲覧の識別子（ADR-0036 第7項の暫定 snapshot の確かめ）。"""
    return frozenset(
        row.access_id
        for row in rows
        if isinstance(row, QualityAccessEnd) and row.result is QualityAccessResult.COMPLETED
    )


def violation_scopes(rows: Sequence[QualityAccessRow]) -> tuple[ViolationScope, ...]:
    """判定の行がある閲覧の対象から、違反の範囲を作る（ADR-0035 第3項、ADR-0038 第3項の 2）。

    partition の対象はその系列とその partition の期間。原ファイルの対象は、その銘柄（分から
    なければすべての銘柄）の封印の区分の全体（原ファイルの期間を決めるデータ源の設定の
    ファイルの期間がまだ無いので、ADR-0035 第3項の「期間が分からなければ、封印の区分の全体と
    重なるとみなす」を当てる）。構造の誤りがある行の列には使わない（呼び出し側が先に
    `quality_log_defect` で止める）。
    """
    defect = quality_log_defect(rows)
    if defect is not None:
        raise MarketDataValueError(
            f"the quality access log has a structural error ({defect[0].value} at line"
            f" {defect[1]}); violation scopes are not derived from it"
        )
    starts = {row.access_id: row for row in rows if isinstance(row, QualityAccessStart)}
    scopes: list[ViolationScope] = []
    for row in rows:
        if not isinstance(row, QualityAccessJudgment):
            continue
        for target in starts[row.access_id].targets:
            if isinstance(target, PartitionTarget):
                scopes.append(
                    ViolationScope(
                        series=target.partition_id.series, symbol=None, period=target.interval
                    )
                )
            else:
                scopes.append(ViolationScope(series=None, symbol=target.symbol, period=None))
    return tuple(scopes)


@dataclass(frozen=True, slots=True)
class QualityReadPermit:
    """読み取りの許可の値（ADR-0036 第9項の 2。`marketdata.domain` に置く型）。

    関所が品質確認の許可と開始の行の記録に成功した後に作る。対象の区分は OANDA MT5 の経路の
    2 つの封印の区分に限る（隔離の区分は品質確認の許可があっても隔離を保ち、旧データの
    2024 年以降は許可の対象にしない。ADR-0036 第4項・第6項）。as-of ビューの許可 partition
    集合には入れない（ADR-0036 第5項の 1）。
    """

    permit_id: str
    access_id: str
    tool: ToolRef
    classes: frozenset[AccessClass]
    period: AccessPeriod
    targets: tuple[QualityTarget, ...]
    output_kinds: frozenset[QualityOutputKind]

    def __post_init__(self) -> None:
        _require_text(self.permit_id, "QualityReadPermit.permit_id")
        if self.permit_id == PRE_PERMIT_MECHANISM:
            raise MarketDataValueError("a read permit is never the pre-permit exception")
        _require_text(self.access_id, "QualityReadPermit.access_id")
        if not isinstance(self.tool, ToolRef):
            raise MarketDataValueError("QualityReadPermit.tool must be a ToolRef")
        _require_classes(self.classes, "QualityReadPermit.classes", OANDA_SEALED_CLASSES)
        if not isinstance(self.period, AccessPeriod):
            raise MarketDataValueError("QualityReadPermit.period must be an AccessPeriod")
        _require_targets(self.targets, "QualityReadPermit.targets")
        _require_output_kinds(self.output_kinds, "QualityReadPermit.output_kinds")
        outside = sorted(
            kind.value for kind in self.output_kinds if kind not in SEALED_INTAKE_OUTPUT_KINDS
        )
        if outside:
            raise MarketDataValueError(
                f"a read permit for the sealed classes may only output counts, times, intervals,"
                f" hashes and pass/fail, got {outside} (ADR-0037 第1項の 4)"
            )

    def covers_partition(self, partition_id: PartitionId) -> bool:
        """この許可が partition の読み取りを覆うか（区分・期間・対象の一覧のすべて）。"""
        if partition_id.access_class not in self.classes:
            return False
        period = partition_id.period
        if period is None or not self.period.contains_interval(period):
            return False
        return any(
            isinstance(target, PartitionTarget) and target.partition_id == partition_id
            for target in self.targets
        )

    def covers_raw_file(self, location: str, sha256: str) -> bool:
        """この許可が原ファイル（置き場とハッシュの両方が一致するもの）を覆うか。"""
        return any(
            isinstance(target, RawFileTarget)
            and target.location == location
            and target.sha256 == sha256
            for target in self.targets
        )
