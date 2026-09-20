"""snapshot manifest とその構成型（D03 §3.7・§3.7.1）。

snapshot は「受入れが固定した市場データの1つの版」であり、manifest はその内容を識別・
再現するための記録である。識別子（`SnapshotId`）は決定論的な内容だけから作り、実行時刻・
操作者・承認者は含めない（D03 §3.7.1）。

**`SnapshotId` の対象**: `sources`、`conversion`（カレンダー版を含む）、
`basis_declaration`（値と `verified` のみ）、`series`、`partitions`、
`integrity_report_ref`、`closure_decisions`、`legacy_access`。

**対象外**: `created_at`、`declaration_record`（宣言者・宣言日時）、`access_log`、
`approval`。同じ宣言内容なら誰がいつ宣言・承認しても同じ `snapshot_id` になる。

**列の正規順序**（D03 §3.7.1）: ファイルシステムの列挙順や検査の実行順に依存しないよう、
符号化前に次の鍵で整列する。

- `sources`: `path`（POSIX 相対パス、コードポイント順）
- `series`: `SeriesId` の文字列
- `partitions`: `(series_id 文字列, access_class, interval.start)`
- `closure_decisions` / `legacy_access`: `(series_id 文字列, interval.start)`

manifest の保存形式（`manifest.json`）も同じ順序で書く（D03 §3.7.1）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from odyssey_fx.common import canonical
from odyssey_fx.common.ids import SnapshotId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

__all__ = [
    "Approval",
    "BasisDeclaration",
    "ClosureDecision",
    "ClosureDecisionKind",
    "ConversionRecord",
    "DeclarationRecord",
    "LegacyAccessRecord",
    "LegacyObservation",
    "PartitionId",
    "PartitionRecord",
    "SeriesManifest",
    "SnapshotManifest",
    "SourceFile",
]


def _require_non_empty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MarketDataValueError(f"{label} must be a non-empty str, got {value!r}")
    return value


def _require_non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketDataValueError(f"{label} must be an int, got {value!r}")
    if value < 0:
        raise MarketDataValueError(f"{label} must be >= 0, got {value}")
    return value


# --- 価格基準の宣言（D03 §2・§3.7）------------------------------------------


@dataclass(frozen=True, slots=True)
class BasisDeclaration:
    """価格基準の宣言のうち識別に関わる部分（D03 §3.7）。

    ファイルから検証できる事実ではないので `verified` は常に `False`（初版）。この型は
    `SnapshotId` の対象であり、宣言者・宣言日時は `DeclarationRecord` に分けて持つ。
    """

    value: PriceBasis
    verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.value, PriceBasis):
            raise MarketDataValueError("BasisDeclaration.value must be a PriceBasis")
        if not isinstance(self.verified, bool):
            raise MarketDataValueError("BasisDeclaration.verified must be a bool")


@dataclass(frozen=True, slots=True)
class DeclarationRecord:
    """宣言の記録（D03 §3.7）。**`SnapshotId` の計算対象外**。

    同じ宣言内容なら誰がいつ宣言しても同じ `snapshot_id` になる（D03 §3.7.1 の承認条件2）。
    """

    declared_by: str
    declared_at: UtcTime

    def __post_init__(self) -> None:
        _require_non_empty_str(self.declared_by, "DeclarationRecord.declared_by")
        if not isinstance(self.declared_at, UtcTime):
            raise MarketDataValueError("DeclarationRecord.declared_at must be a UtcTime")


# --- 原ファイル（D03 §3.7）--------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceFile:
    """受け入れた原ファイル1件の記録（D03 §3.7）。

    `provenance_counts` は `source` 列の値ごとの行数（`histdata` / `dukascopy`）。
    キーの順に依存しないよう、整列済みの組の列で持つ。
    """

    path: str
    sha256: str
    rows: int
    symbol: Symbol
    timeframe: TimeframeRef
    declared_basis: PriceBasis
    provenance_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_str(self.path, "SourceFile.path")
        if self.path.startswith("/") or "\\" in self.path:
            raise MarketDataValueError(
                f"SourceFile.path must be a relative POSIX path, got {self.path!r}"
            )
        digest = ContentDigest.sha256(self.sha256)  # 形式を共通カーネルの規則で検査する
        del digest
        _require_non_negative_int(self.rows, "SourceFile.rows")
        if not isinstance(self.symbol, Symbol):
            raise MarketDataValueError("SourceFile.symbol must be a Symbol")
        if not isinstance(self.timeframe, TimeframeRef):
            raise MarketDataValueError("SourceFile.timeframe must be a TimeframeRef")
        if not isinstance(self.declared_basis, PriceBasis):
            raise MarketDataValueError("SourceFile.declared_basis must be a PriceBasis")
        if not isinstance(self.provenance_counts, tuple):
            raise MarketDataValueError("SourceFile.provenance_counts must be a tuple")
        counts: list[tuple[str, int]] = []
        for entry in self.provenance_counts:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise MarketDataValueError(
                    "SourceFile.provenance_counts must contain (source, count) pairs"
                )
            name, count = entry
            _require_non_empty_str(name, "SourceFile.provenance_counts key")
            _require_non_negative_int(count, "SourceFile.provenance_counts value")
            counts.append((name, count))
        normalized = tuple(sorted(counts, key=lambda pair: pair[0]))
        if normalized != self.provenance_counts:
            object.__setattr__(self, "provenance_counts", normalized)

    def sort_key(self) -> str:
        """D03 §3.7.1 の整列鍵（`path` のコードポイント順）。"""
        return self.path


@dataclass(frozen=True, slots=True)
class ConversionRecord:
    """変換の版の記録（D03 §3.7）。

    `time_convention` は原データの時刻規約（初版は `explicit_offset_utc`: オフセットが
    明示された UTC）。時刻の見た目から規約を推測しない（上位設計書 §3.2）。
    """

    code_version: str
    time_convention: str
    aggregation_rule_version: str
    calendar_id: str
    calendar_version: int

    def __post_init__(self) -> None:
        _require_non_empty_str(self.code_version, "ConversionRecord.code_version")
        _require_non_empty_str(self.time_convention, "ConversionRecord.time_convention")
        _require_non_empty_str(
            self.aggregation_rule_version, "ConversionRecord.aggregation_rule_version"
        )
        _require_non_empty_str(self.calendar_id, "ConversionRecord.calendar_id")
        if isinstance(self.calendar_version, bool) or not isinstance(self.calendar_version, int):
            raise MarketDataValueError(
                f"ConversionRecord.calendar_version must be an int, got {self.calendar_version!r}"
            )
        if self.calendar_version < 1:
            raise MarketDataValueError(
                f"ConversionRecord.calendar_version must be >= 1, got {self.calendar_version}"
            )


# --- partition と系列（D03 §3.7・§3.8）--------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionId:
    """partition の識別（D03 §3.8）。

    `__str__` は partition ディレクトリの相対パス（`USDJPY_1h_bid/RESEARCH_HISTORY`）。
    アクセス分類をディレクトリ名に含めることで、物理的な分離を保つ（D03 §3.8）。
    """

    series: SeriesId
    access_class: AccessClass

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("PartitionId.series must be a SeriesId")
        if not isinstance(self.access_class, AccessClass):
            raise MarketDataValueError("PartitionId.access_class must be an AccessClass")

    @property
    def directory(self) -> str:
        """partition ディレクトリの相対パス（POSIX 区切り）。"""
        name = f"{self.series.symbol}_{self.series.timeframe.id}_{self.series.basis.value}"
        return f"{name}/{self.access_class.value}"

    def __str__(self) -> str:
        return self.directory

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3 の `CanonicalScalar`）。"""
        return self.directory


@dataclass(frozen=True, slots=True)
class PartitionRecord:
    """partition 1件の記録（D03 §3.7）。"""

    partition_id: PartitionId
    interval: Interval
    bar_count: int
    digest: ContentDigest

    def __post_init__(self) -> None:
        if not isinstance(self.partition_id, PartitionId):
            raise MarketDataValueError("PartitionRecord.partition_id must be a PartitionId")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("PartitionRecord.interval must be an Interval")
        _require_non_negative_int(self.bar_count, "PartitionRecord.bar_count")
        if not isinstance(self.digest, ContentDigest):
            raise MarketDataValueError("PartitionRecord.digest must be a ContentDigest")

    @property
    def series_id(self) -> SeriesId:
        """この partition が属する系列。"""
        return self.partition_id.series

    @property
    def access_class(self) -> AccessClass:
        """この partition のアクセス分類。"""
        return self.partition_id.access_class

    def sort_key(self) -> tuple[str, str, str]:
        """D03 §3.7.1 の整列鍵 `(series_id 文字列, access_class, interval.start)`。"""
        return (str(self.series_id), self.access_class.value, str(self.interval.start))


@dataclass(frozen=True, slots=True)
class SeriesManifest:
    """系列1件の記録（D03 §3.7）。"""

    series_id: SeriesId
    covered_interval: Interval
    bar_count: int
    partitions: tuple[PartitionId, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, SeriesId):
            raise MarketDataValueError("SeriesManifest.series_id must be a SeriesId")
        if not isinstance(self.covered_interval, Interval):
            raise MarketDataValueError("SeriesManifest.covered_interval must be an Interval")
        _require_non_negative_int(self.bar_count, "SeriesManifest.bar_count")
        if not isinstance(self.partitions, tuple):
            raise MarketDataValueError("SeriesManifest.partitions must be a tuple")
        for partition in self.partitions:
            if not isinstance(partition, PartitionId):
                raise MarketDataValueError("SeriesManifest.partitions must contain PartitionId")
            if partition.series != self.series_id:
                raise MarketDataValueError(
                    f"partition {partition} does not belong to series {self.series_id}"
                )
        normalized = tuple(
            sorted(self.partitions, key=lambda partition: partition.access_class.value)
        )
        if normalized != self.partitions:
            object.__setattr__(self, "partitions", normalized)

    def sort_key(self) -> str:
        """D03 §3.7.1 の整列鍵（`SeriesId` の文字列）。"""
        return str(self.series_id)


# --- 欠落区間の分類と旧基盤の履歴（D03 §3.4・§3.7）--------------------------


class ClosureDecisionKind(Enum):
    """欠落区間に対する人間の分類（D03 §3.4・§4 の 9）。

    - `CLOSURE`: 休場だった。カレンダーへ追加して版を上げる。
    - `DATA_GAP`: データ欠損。そのまま欠損として扱う。
    """

    CLOSURE = "CLOSURE"
    DATA_GAP = "DATA_GAP"


@dataclass(frozen=True, slots=True)
class ClosureDecision:
    """欠落区間1件の分類（D03 §3.7）。`SnapshotId` の対象。"""

    series_id: SeriesId
    interval: Interval
    kind: ClosureDecisionKind
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, SeriesId):
            raise MarketDataValueError("ClosureDecision.series_id must be a SeriesId")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("ClosureDecision.interval must be an Interval")
        if not isinstance(self.kind, ClosureDecisionKind):
            raise MarketDataValueError("ClosureDecision.kind must be a ClosureDecisionKind")
        if not isinstance(self.note, str):
            raise MarketDataValueError("ClosureDecision.note must be a str")

    def sort_key(self) -> tuple[str, str]:
        """D03 §3.7.1 の整列鍵 `(series_id 文字列, interval.start)`。"""
        return (str(self.series_id), str(self.interval.start))


class LegacyObservation(Enum):
    """旧基盤での閲覧・使用の有無（ADR-0014）。

    - `NOT_OBSERVED`: 未観測と確認できた。封印状態 `SEALED` の根拠になる。
    - `OBSERVED`: 使用済み。`CONSUMED` とする。
    - `UNKNOWN`: 履歴が確認できない。`SEALED` にはせず `CONSUMED` として扱う。
    """

    NOT_OBSERVED = "NOT_OBSERVED"
    OBSERVED = "OBSERVED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class LegacyAccessRecord:
    """旧基盤から引き継いだ閲覧・使用履歴1件（D03 §3.7、ADR-0014）。

    封印期間 partition の初期状態を決める根拠。未観測と確認できた区間だけが `SEALED` の
    候補になる。
    """

    series_id: SeriesId
    interval: Interval
    observation: LegacyObservation
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, SeriesId):
            raise MarketDataValueError("LegacyAccessRecord.series_id must be a SeriesId")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("LegacyAccessRecord.interval must be an Interval")
        if not isinstance(self.observation, LegacyObservation):
            raise MarketDataValueError("LegacyAccessRecord.observation must be a LegacyObservation")
        if not isinstance(self.note, str):
            raise MarketDataValueError("LegacyAccessRecord.note must be a str")

    def sort_key(self) -> tuple[str, str]:
        """D03 §3.7.1 の整列鍵 `(series_id 文字列, interval.start)`。"""
        return (str(self.series_id), str(self.interval.start))


@dataclass(frozen=True, slots=True)
class Approval:
    """人間の受入れ承認（D03 §3.7）。**`SnapshotId` の計算対象外**。

    これが記入されるまで、as-of ビュー・公開フィードから snapshot を読めない
    （D03 §3.7.1 の3）。
    """

    approved_by: str
    approved_at: UtcTime
    comment: str = ""

    def __post_init__(self) -> None:
        _require_non_empty_str(self.approved_by, "Approval.approved_by")
        if not isinstance(self.approved_at, UtcTime):
            raise MarketDataValueError("Approval.approved_at must be a UtcTime")
        if not isinstance(self.comment, str):
            raise MarketDataValueError("Approval.comment must be a str")


# --- manifest 本体 ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    """snapshot manifest（D03 §3.7）。

    各列は構築時に D03 §3.7.1 の正規順序へ並べ替える。`snapshot_id` は
    `identity_payload()` のダイジェストであり、`created_at`・`declaration_record`・
    `approval`・閲覧記録は対象に含めない。
    """

    created_at: UtcTime
    basis_declaration: BasisDeclaration
    sources: tuple[SourceFile, ...]
    conversion: ConversionRecord
    series: tuple[SeriesManifest, ...]
    partitions: tuple[PartitionRecord, ...]
    integrity_report_ref: ContentDigest
    closure_decisions: tuple[ClosureDecision, ...] = ()
    legacy_access: tuple[LegacyAccessRecord, ...] = ()
    declaration_record: DeclarationRecord | None = None
    approval: Approval | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.created_at, UtcTime):
            raise MarketDataValueError("SnapshotManifest.created_at must be a UtcTime")
        if not isinstance(self.basis_declaration, BasisDeclaration):
            raise MarketDataValueError(
                "SnapshotManifest.basis_declaration must be a BasisDeclaration"
            )
        if not isinstance(self.conversion, ConversionRecord):
            raise MarketDataValueError("SnapshotManifest.conversion must be a ConversionRecord")
        if not isinstance(self.integrity_report_ref, ContentDigest):
            raise MarketDataValueError(
                "SnapshotManifest.integrity_report_ref must be a ContentDigest"
            )
        if self.declaration_record is not None and not isinstance(
            self.declaration_record, DeclarationRecord
        ):
            raise MarketDataValueError(
                "SnapshotManifest.declaration_record must be a DeclarationRecord or None"
            )
        if self.approval is not None and not isinstance(self.approval, Approval):
            raise MarketDataValueError("SnapshotManifest.approval must be an Approval or None")

        self._normalize("sources", SourceFile, lambda item: item.sort_key())
        self._normalize("series", SeriesManifest, lambda item: item.sort_key())
        self._normalize("partitions", PartitionRecord, lambda item: item.sort_key())
        self._normalize("closure_decisions", ClosureDecision, lambda item: item.sort_key())
        self._normalize("legacy_access", LegacyAccessRecord, lambda item: item.sort_key())

        declared = {record.series_id for record in self.series}
        for partition in self.partitions:
            if partition.series_id not in declared:
                raise MarketDataValueError(
                    f"partition {partition.partition_id} refers to series"
                    f" {partition.series_id}, which is not in SnapshotManifest.series"
                )

    def _normalize(self, name: str, item_type: type, key: Any) -> None:
        """列を型検査し、D03 §3.7.1 の正規順序へ並べ替える。"""
        values = getattr(self, name)
        if not isinstance(values, tuple):
            raise MarketDataValueError(f"SnapshotManifest.{name} must be a tuple")
        for value in values:
            if not isinstance(value, item_type):
                raise MarketDataValueError(
                    f"SnapshotManifest.{name} must contain {item_type.__name__}"
                )
        normalized = tuple(sorted(values, key=key))
        if normalized != values:
            object.__setattr__(self, name, normalized)

    # --- 識別（D03 §3.7.1）--------------------------------------------------

    def identity_payload(self) -> Mapping[str, Any]:
        """`SnapshotId` のダイジェスト対象（D03 §3.7.1）。

        含めるもの: `sources`、`conversion`、`basis_declaration`、`series`、`partitions`、
        `integrity_report_ref`、`closure_decisions`、`legacy_access`。

        含めないもの: `created_at`、`declaration_record`、`access_log`、`approval`。
        """
        return {
            "basis_declaration": self.basis_declaration,
            "closure_decisions": self.closure_decisions,
            "conversion": self.conversion,
            "integrity_report_ref": self.integrity_report_ref,
            "legacy_access": self.legacy_access,
            "partitions": self.partitions,
            "series": self.series,
            "sources": self.sources,
        }

    def snapshot_id(self) -> SnapshotId:
        """内容から決まる snapshot の識別子（D03 §3.7.1）。

        同じ原ファイル・設定・コード版・分類なら同じ値になる。受入れ実行時刻や承認者を
        変えても値は変わらない。
        """
        return SnapshotId(canonical.digest(self.identity_payload()))

    @property
    def is_approved(self) -> bool:
        """人間の受入れ承認が記入されているか（D03 §3.7.1 の3）。"""
        return self.approval is not None

    def partition_record(self, partition_id: PartitionId) -> PartitionRecord | None:
        """指定した partition の記録（未登録なら `None`）。"""
        for record in self.partitions:
            if record.partition_id == partition_id:
                return record
        return None

    def with_closure_decisions(self, decisions: tuple[ClosureDecision, ...]) -> SnapshotManifest:
        """分類を記入した manifest を返す（D03 §3.7.1 の確定段階）。

        分類が変われば `snapshot_id` も変わる。分類が異なれば別 snapshot である。
        """
        return SnapshotManifest(
            created_at=self.created_at,
            basis_declaration=self.basis_declaration,
            sources=self.sources,
            conversion=self.conversion,
            series=self.series,
            partitions=self.partitions,
            integrity_report_ref=self.integrity_report_ref,
            closure_decisions=decisions,
            legacy_access=self.legacy_access,
            declaration_record=self.declaration_record,
            approval=self.approval,
        )

    def with_approval(self, approval: Approval) -> SnapshotManifest:
        """承認を記入した manifest を返す。`snapshot_id` は変わらない（D03 §3.7.1）。"""
        return SnapshotManifest(
            created_at=self.created_at,
            basis_declaration=self.basis_declaration,
            sources=self.sources,
            conversion=self.conversion,
            series=self.series,
            partitions=self.partitions,
            integrity_report_ref=self.integrity_report_ref,
            closure_decisions=self.closure_decisions,
            legacy_access=self.legacy_access,
            declaration_record=self.declaration_record,
            approval=approval,
        )
