"""消費の記録の行（`data/snapshots/<snapshot_id>/access_log.jsonl`）の型と読込（D09 §9.10.4）。

封印の区分の partition が「未使用のまま（`SEALED`）」か「使用済み（`CONSUMED`）」かは、
追記専用の消費の記録から導く（ADR-0038 第8項の 1 が引き継ぐ ADR-0014）。行の形は 2 つある。

- **版 0**（版の項目を持たない今までの行。`AccessLogEntry` の 5 項目 `kind`・`partition`・
  `recorded_at`・`actor`・`purpose`。値はすべて文字列）。追記専用で書き換えないので、読み続ける。
- **版 1**（D09 §9.10.4 の表。仮置き R2-3 の決定「このまま」）。`schema_version` = `"1"`、
  `row_id`、`kind`、`partition`、`reason`、`permit_id`、`candidate`、`access_id`、
  `recorded_at`、`actor`、`purpose`。行の鍵は `(partition, permit_id)`。符号化は正規化エンコード
  （鍵を整列し空白を入れない。D02 §9.3）で 1 行 1 JSON。`kind` は `CONSUMED`（消費）か
  `CONVERSION_READ`（換算用の読み取り。D06 v1.20 案 §19.14、仮置き C1-16 (a)。円以外の決済通貨の
  候補の最終検証で、換算のためだけに読む執行の通貨ペアでない通貨ペアの封印の partition。**消費に
  数えない**。ADR-0038 第8項の 1 の 2026-11-03 の注記）。

行の型は関所（`evaluation`）も使うので `marketdata.domain` に置く（`evaluation` は
`marketdata.application` を参照できない。D01 §3.2 の契約 F7。D09 §9.10.4 の「型の置き場」）。
ファイルの読み書きは adapters・`app` が行い、本モジュールは行の符号化・読込の検査・状態の材料
だけを持つ（I/O を持たない）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.common.canonical import encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = [
    "SCHEMA_VERSION_1",
    "AccessLogEntry",
    "AccessLogEntryKind",
    "AccessLogReadFailure",
    "ConsumptionCandidate",
    "ConsumptionKind",
    "ConsumptionReason",
    "ConsumptionRow",
    "LegacyLine",
    "ParsedAccessLog",
    "consumption_line_bytes",
    "consumption_row_payload",
    "parse_access_log",
]

#: 版 1 の行の `schema_version` の値（D09 §9.10.4）。
SCHEMA_VERSION_1: Final = "1"

#: 版 1 の行の項目（すべて必須。`candidate` だけが入れ子の対象）。
_V1_KEYS: Final = frozenset(
    {
        "schema_version",
        "row_id",
        "kind",
        "partition",
        "reason",
        "permit_id",
        "candidate",
        "access_id",
        "recorded_at",
        "actor",
        "purpose",
    }
)
#: 候補の項目（最終検証・前向き検証の行。D09 §9.10.4 の表の `candidate`）。
_CANDIDATE_KEYS: Final = ("experiment_id", "execution", "experiment_name", "experiment_version")


class AccessLogEntryKind(Enum):
    """閲覧記録の種別（ADR-0014）。

    - `GRANTED`: 許可が発行された（データはまだ公開されていない）。版 1 では書かない（R2-2）。
    - `CONSUMED`: 消費が確定した。この記録があれば partition は `CONSUMED`。
    - `RESEARCH_OPT_IN`: 既に使用済みの partition を、研究・開発用途として明示的に読んだ記録。
      2 つの封印の区分と旧データの 2024 年以降の区分には書かない（ADR-0038 第8項の 1）。
    """

    GRANTED = "GRANTED"
    CONSUMED = "CONSUMED"
    RESEARCH_OPT_IN = "RESEARCH_OPT_IN"


class ConsumptionKind(Enum):
    """版 1 の行の種別（D09 §9.10.4 の `kind`。D06 v1.20 案 §19.14 の仮置き C1-16 (a)）。

    - `CONSUMED`: 消費（この行があれば partition は `CONSUMED`）。
    - `CONVERSION_READ`: 換算用の読み取り（最終検証の許可の下で換算の脚としてだけ読んだ記録）。
      状態の導出に数えない（partition は `SEALED` のまま。決定 (21) (b)）。理由は
      `FINAL_EVALUATION` だけ。版 1 の行の種別が `CONSUMED` だけだった読込（C2 より前の道具）は、
      この行を知らない種別として構造の誤りで止める側に倒れる（読み違えない）。
    """

    CONSUMED = "CONSUMED"
    CONVERSION_READ = "CONVERSION_READ"


class ConsumptionReason(Enum):
    """版 1 の消費の行の理由（D09 §9.10.4 の表の `reason`）。

    `FORWARD_EVALUATION` は前向き検証の規則（F1）の承認まで書く経路が無い（仮置き R2-14）。
    """

    FINAL_EVALUATION = "FINAL_EVALUATION"
    FORWARD_EVALUATION = "FORWARD_EVALUATION"
    VIOLATION_JUDGMENT = "VIOLATION_JUDGMENT"


@dataclass(frozen=True, slots=True)
class AccessLogEntry:
    """閲覧記録1件（版 0 の行。ADR-0014、D03 §3.7）。

    記録は追記専用なので、この型に「取り消し」は存在しない。`recorded_at` は記録時刻で、
    `snapshot_id` の計算には入らない（D03 §3.7.1）。
    """

    kind: AccessLogEntryKind
    partition_id: PartitionId
    recorded_at: UtcTime
    actor: str
    purpose: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AccessLogEntryKind):
            raise MarketDataValueError("AccessLogEntry.kind must be an AccessLogEntryKind")
        if not isinstance(self.partition_id, PartitionId):
            raise MarketDataValueError("AccessLogEntry.partition_id must be a PartitionId")
        if not isinstance(self.recorded_at, UtcTime):
            raise MarketDataValueError("AccessLogEntry.recorded_at must be a UtcTime")
        if not isinstance(self.actor, str) or not self.actor:
            raise MarketDataValueError("AccessLogEntry.actor must be a non-empty str")
        if not isinstance(self.purpose, str):
            raise MarketDataValueError("AccessLogEntry.purpose must be a str")


@dataclass(frozen=True, slots=True)
class ConsumptionCandidate:
    """消費の行の候補（`{experiment_id, execution, experiment_name, experiment_version}`）。

    値はすべて文字列で持つ（行の値は文字列。D09 §9.10.4 の「符号化」）。
    """

    experiment_id: str
    execution: str
    experiment_name: str
    experiment_version: str

    def __post_init__(self) -> None:
        for name in _CANDIDATE_KEYS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise MarketDataValueError(f"ConsumptionCandidate.{name} must be a non-empty str")

    def payload(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in _CANDIDATE_KEYS}


@dataclass(frozen=True, slots=True)
class ConsumptionRow:
    """版 1 の消費の記録の行 1 行（D09 §9.10.4）。`kind` の既定は `CONSUMED`。

    - 最終検証・前向き検証: `permit_id` は許可の識別子、`candidate` は候補、`access_id` は空、
      `purpose` は記録票の `final_holdout.purpose` の写し。
    - 後からの違反の判定: `permit_id` は判定の行の識別子、`candidate` は空（`None`）、
      `access_id` は品質確認の閲覧の識別子、`purpose` は空。
    - 換算用の読み取り（`kind` = `CONVERSION_READ`。仮置き C1-16 (a)）: 理由は
      `FINAL_EVALUATION`、許可の識別子・候補・目的は同じ許可の消費の行と同じ。
    """

    row_id: str
    partition: str
    reason: ConsumptionReason
    permit_id: str
    candidate: ConsumptionCandidate | None
    access_id: str
    recorded_at: UtcTime
    actor: str
    purpose: str
    kind: ConsumptionKind = ConsumptionKind.CONSUMED

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ConsumptionKind):
            raise MarketDataValueError("ConsumptionRow.kind must be a ConsumptionKind")
        if (
            self.kind is ConsumptionKind.CONVERSION_READ
            and self.reason is not ConsumptionReason.FINAL_EVALUATION
        ):
            raise MarketDataValueError(
                "a CONVERSION_READ row is written by a final evaluation only (D06 §19.14、"
                "仮置き C1-16 (a))"
            )
        for name in ("row_id", "partition", "permit_id", "actor"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise MarketDataValueError(f"ConsumptionRow.{name} must be a non-empty str")
        if not isinstance(self.reason, ConsumptionReason):
            raise MarketDataValueError("ConsumptionRow.reason must be a ConsumptionReason")
        if not isinstance(self.recorded_at, UtcTime):
            raise MarketDataValueError("ConsumptionRow.recorded_at must be a UtcTime")
        if not isinstance(self.access_id, str) or not isinstance(self.purpose, str):
            raise MarketDataValueError("ConsumptionRow.access_id and purpose must be str")
        if self.candidate is not None and not isinstance(self.candidate, ConsumptionCandidate):
            raise MarketDataValueError("ConsumptionRow.candidate must be a ConsumptionCandidate")
        if self.reason is ConsumptionReason.VIOLATION_JUDGMENT:
            if self.candidate is not None or not self.access_id or self.purpose:
                raise MarketDataValueError(
                    "a VIOLATION_JUDGMENT row carries the access id, no candidate and no purpose"
                    " (D09 §9.10.4)"
                )
        elif self.candidate is None or self.access_id or not self.purpose.strip():
            raise MarketDataValueError(
                f"a {self.reason.value} row carries the candidate and the purpose, and no access"
                " id (D09 §9.10.4)"
            )

    @property
    def key(self) -> tuple[str, str]:
        """行の鍵 `(partition, permit_id)`（D09 §9.10.4）。"""
        return (self.partition, self.permit_id)

    @property
    def consumes(self) -> bool:
        """状態の導出に数える行か（消費の行。換算用の読み取りの行は数えない）。"""
        return self.kind is ConsumptionKind.CONSUMED


def consumption_row_payload(row: ConsumptionRow) -> dict[str, object]:
    """版 1 の行の値（文字列。`candidate` だけが文字列の値を持つ入れ子の対象）。"""
    return {
        "access_id": row.access_id,
        "actor": row.actor,
        "candidate": {} if row.candidate is None else row.candidate.payload(),
        "kind": row.kind.value,
        "partition": row.partition,
        "permit_id": row.permit_id,
        "purpose": row.purpose,
        "reason": row.reason.value,
        "recorded_at": str(row.recorded_at),
        "row_id": row.row_id,
        "schema_version": SCHEMA_VERSION_1,
    }


def consumption_line_bytes(row: ConsumptionRow) -> bytes:
    """版 1 の 1 行の符号化（正規化エンコード＋改行 1 つ。D02 §9.3）。"""
    return encode(consumption_row_payload(row)) + b"\n"


@dataclass(frozen=True, slots=True)
class LegacyLine:
    """版 0 の行 1 行（鍵を持たない。`partition` は識別子の文字列のまま）。"""

    kind: AccessLogEntryKind
    partition: str


@dataclass(frozen=True, slots=True)
class AccessLogReadFailure:
    """消費の記録を読めない（構造の誤り。D09 §9.10.4）。その snapshot の状態は導けない。"""

    line_number: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class ParsedAccessLog:
    """読み込んだ消費の記録（追記の順）。"""

    legacy: tuple[LegacyLine, ...]
    rows: tuple[ConsumptionRow, ...]

    def consumed(self) -> frozenset[str]:
        """消費の行（版 0 と版 1 の `CONSUMED` の行）がある partition の識別子の文字列。

        換算用の読み取りの行（`CONVERSION_READ`）は数えない（その partition は `SEALED` のまま。
        D06 v1.20 案 §19.14、決定 (21) (b)）。
        """
        return frozenset(
            {line.partition for line in self.legacy if line.kind is AccessLogEntryKind.CONSUMED}
            | {row.partition for row in self.rows if row.consumes}
        )

    def rows_of_permit(self, permit_id: str) -> tuple[ConsumptionRow, ...]:
        return tuple(row for row in self.rows if row.permit_id == permit_id)


#: 封印の区分の partition に受け付ける行の種別（版 0 の `CONSUMED` と版 1 の 2 つ）。
_SEALED_KINDS: Final = frozenset(
    {AccessLogEntryKind.CONSUMED.value, ConsumptionKind.CONVERSION_READ.value}
)


def _failure(number: int | None, detail: str) -> AccessLogReadFailure:
    return AccessLogReadFailure(line_number=number, detail=detail)


def _str_values(payload: Mapping[str, object], keys: Iterable[str]) -> bool:
    return all(isinstance(payload[key], str) for key in keys)


def _row_of(payload: Mapping[str, object]) -> ConsumptionRow:
    """版 1 の行の値から行を作る（形が違えば `ValueError` の系統）。"""
    if set(payload) != _V1_KEYS:
        missing = sorted(_V1_KEYS - set(payload))
        extra = sorted(set(payload) - _V1_KEYS)
        raise KernelValueError(
            f"the keys of a version-1 row differ (missing {missing}, extra {extra})"
        )
    if not _str_values(payload, _V1_KEYS - {"candidate"}):
        raise KernelValueError("the values of a version-1 row are strings")
    kind = ConsumptionKind(str(payload["kind"]))  # 知らない種別は ValueError（構造の誤り）
    candidate_value = payload["candidate"]
    if not isinstance(candidate_value, dict):
        raise KernelValueError("the candidate of a version-1 row is an object")
    candidate: ConsumptionCandidate | None
    if candidate_value:
        if set(candidate_value) != set(_CANDIDATE_KEYS) or not all(
            isinstance(value, str) for value in candidate_value.values()
        ):
            raise KernelValueError("the candidate holds its four string items")
        candidate = ConsumptionCandidate(**{key: candidate_value[key] for key in _CANDIDATE_KEYS})
    else:
        candidate = None
    reason = ConsumptionReason(str(payload["reason"]))
    return ConsumptionRow(
        row_id=str(payload["row_id"]),
        partition=str(payload["partition"]),
        reason=reason,
        permit_id=str(payload["permit_id"]),
        candidate=candidate,
        access_id=str(payload["access_id"]),
        recorded_at=UtcTime.parse(str(payload["recorded_at"])),
        actor=str(payload["actor"]),
        purpose=str(payload["purpose"]),
        kind=kind,
    )


def _legacy_of(payload: Mapping[str, object]) -> LegacyLine:
    """版 0 の行の値から行を作る（`kind` と `partition` の文字列。他の項目は読まない）。

    版 0 の行は追記専用で書き換えないので、今までの読み方（種別と partition だけを見る）のまま
    読む（D09 §9.10.4 の「版 0 として読み続ける」）。
    """
    if not {"kind", "partition"} <= set(payload) or not _str_values(payload, ("kind", "partition")):
        raise KernelValueError("a version-0 row holds its kind and partition as strings")
    return LegacyLine(
        kind=AccessLogEntryKind(str(payload["kind"])), partition=str(payload["partition"])
    )


def parse_access_log(
    data: bytes,
    *,
    partitions: frozenset[str],
    sealed_partitions: frozenset[str],
) -> ParsedAccessLog | AccessLogReadFailure:
    """消費の記録の内容を読み、構造の誤り（D09 §9.10.4 の「読込」）を確かめる。

    `partitions` はその snapshot の manifest の partition の識別子の文字列、`sealed_partitions`
    はそのうち 2 つの封印の区分のもの。次はすべて構造の誤り（その snapshot の状態を導けない）:
    JSON でない行、改行で終わらない最後の行、正規化と一致しない版 1 の行、知らない
    `schema_version`・`kind`・`reason`、版 1 の行の必須の項目の欠け、鍵の重複、2 つの封印の
    区分の partition への `GRANTED`・`RESEARCH_OPT_IN` の行、manifest に無い partition を指す行、
    封印の区分でない partition への換算用の読み取りの行（`CONVERSION_READ`。換算用の読み取りは
    封印の partition だけに書く。C2 の仮置き）。版 0 の行どうしの重複は数えない（鍵を持たない）。
    封印の区分の partition に受け付ける種別は、版 0 の `CONSUMED` と版 1 の `CONSUMED`・
    `CONVERSION_READ`（D06 v1.20 案 §19.14、仮置き C1-16 (a)）。
    """
    if not isinstance(data, bytes):
        raise MarketDataValueError("parse_access_log requires bytes")
    if data and not data.endswith(b"\n"):
        return _failure(None, "the log ends with a fragment that is not a full line")
    legacy: list[LegacyLine] = []
    rows: list[ConsumptionRow] = []
    keys: set[tuple[str, str]] = set()
    for number, raw in enumerate(data.split(b"\n")[:-1] if data else (), start=1):
        if not raw.strip():
            return _failure(number, "an empty line")
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise KernelValueError("a line is a JSON object")
            if "schema_version" in payload:
                if payload["schema_version"] != SCHEMA_VERSION_1:
                    raise KernelValueError(
                        f"an unknown schema_version {payload['schema_version']!r}"
                    )
                row = _row_of(payload)
                if consumption_line_bytes(row) != raw + b"\n":
                    raise KernelValueError(
                        "the version-1 line is not in the canonical encoding of its row (D02 §9.3)"
                    )
                partition, kind = row.partition, row.kind.value
            else:
                line = _legacy_of(payload)
                partition, kind = line.partition, line.kind.value
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            return _failure(number, f"{type(exc).__name__}: {exc}")
        if partition not in partitions:
            return _failure(number, f"the line points at {partition}, which the manifest lacks")
        if partition in sealed_partitions and kind not in _SEALED_KINDS:
            return _failure(
                number,
                f"a {kind} line on the sealed partition {partition} (ADR-0038 第8項の 1)",
            )
        if partition not in sealed_partitions and kind == ConsumptionKind.CONVERSION_READ.value:
            return _failure(
                number,
                f"a CONVERSION_READ line on {partition}, which is not a sealed partition"
                " (D06 §19.14)",
            )
        if "schema_version" in payload:
            if row.key in keys:
                return _failure(number, f"the key {row.key} appears twice (D09 §9.10.4)")
            keys.add(row.key)
            rows.append(row)
        else:
            legacy.append(line)
    return ParsedAccessLog(legacy=tuple(legacy), rows=tuple(rows))
