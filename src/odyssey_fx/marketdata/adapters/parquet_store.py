"""snapshot の保存・読込（D03 §8、ADR-0025）。

`SnapshotStore`（`marketdata.application.ports`）を実装する。ポート定義は import せず、
構造的に満たす（D01 §2.2 規則7）。

- partition ごとに Parquet を書き、読み戻す。価格・出来高は**文字列列**として保存する
  （Decimal を二進浮動小数へ落とさないため、ADR-0012）。
- `manifest.json` を D03 §3.7.1 の**正規順序**で書く。manifest の各列は domain 型の構築時に
  既に正規順序へ整列しているので、ここでは JSON のキーを整列し、その順で書き出すだけでよい。
- DataFrame はこのモジュールの外へ出さない（D01 §2.2 規則1）。

Parquet の入出力は polars で行う（pyarrow は polars の依存として入るが直接は使わない、
ADR-0025）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import polars as pl

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.application.partition_digest import (
    PARTITION_COLUMNS,
    partition_digest_hex,
    partition_row,
)
from odyssey_fx.marketdata.application.report_digest import (
    integrity_report_digest_hex,
    integrity_report_text,
)
from odyssey_fx.marketdata.application.snapshot_access import (
    PENDING_DIRECTORY,
    ReadableSnapshot,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError, SnapshotNotApproved
from odyssey_fx.marketdata.domain.integrity import (
    CheckKind,
    CheckResult,
    IntegrityReport,
    Severity,
)
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    BasisDeclaration,
    ClosureDecision,
    ClosureDecisionKind,
    ConversionRecord,
    DeclarationRecord,
    LegacyAccessRecord,
    LegacyObservation,
    PartitionId,
    PartitionRecord,
    SeriesManifest,
    SnapshotManifest,
    SourceFile,
)

__all__ = ["MANIFEST_SCHEMA_VERSION", "ParquetSnapshotStore"]

#: `manifest.json` の形式版。読み込み時に未知の版を拒否する。
MANIFEST_SCHEMA_VERSION = 1

#: partition の Parquet ファイル名。
_PARTITION_FILE = "bars.parquet"

#: 完全性検査の報告のファイル名。
_INTEGRITY_FILE = "integrity_report.json"

#: `manifest.json` のファイル名。
_MANIFEST_FILE = "manifest.json"


def _dumps(payload: Any) -> str:
    """キーをコードポイント順に整列した JSON（D03 §3.7.1 の正規順序）。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _series_payload(series: SeriesId) -> Mapping[str, str]:
    return {
        "basis": series.basis.value,
        "symbol": str(series.symbol),
        "timeframe": str(series.timeframe),
    }


def _series_from_payload(payload: Mapping[str, Any]) -> SeriesId:
    return SeriesId(
        symbol=Symbol(str(payload["symbol"])),
        timeframe=TimeframeRef.parse(str(payload["timeframe"])),
        basis=PriceBasis(str(payload["basis"])),
    )


def _interval_payload(interval: Interval) -> Mapping[str, str]:
    return {"end": str(interval.end), "start": str(interval.start)}


def _interval_from_payload(payload: Mapping[str, Any]) -> Interval:
    return Interval(
        start=UtcTime.parse(str(payload["start"])), end=UtcTime.parse(str(payload["end"]))
    )


def _partition_payload(partition_id: PartitionId) -> Mapping[str, Any]:
    return {
        "access_class": partition_id.access_class.value,
        "series": _series_payload(partition_id.series),
    }


def _partition_from_payload(payload: Mapping[str, Any]) -> PartitionId:
    return PartitionId(
        series=_series_from_payload(payload["series"]),
        access_class=AccessClass(str(payload["access_class"])),
    )


def _manifest_payload(manifest: SnapshotManifest) -> Mapping[str, Any]:
    """manifest を JSON 互換の構造へ変換する（D03 §3.7.1 の正規順序）。

    列は domain 型の構築時に正規順序へ整列済みなので、その順のまま書く。
    """
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "snapshot_id": str(manifest.snapshot_id()),
        "created_at": str(manifest.created_at),
        "basis_declaration": {
            "value": manifest.basis_declaration.value.value,
            "verified": manifest.basis_declaration.verified,
        },
        "conversion": {
            "aggregation_rule_version": manifest.conversion.aggregation_rule_version,
            "calendar_id": manifest.conversion.calendar_id,
            "calendar_version": manifest.conversion.calendar_version,
            "code_version": manifest.conversion.code_version,
            "time_convention": manifest.conversion.time_convention,
        },
        "integrity_report_ref": {
            "algorithm": manifest.integrity_report_ref.algorithm,
            "hex": manifest.integrity_report_ref.hex,
        },
        "sources": [
            {
                "declared_basis": source.declared_basis.value,
                "path": source.path,
                "provenance_counts": [
                    {"count": count, "source": name} for name, count in source.provenance_counts
                ],
                "rows": source.rows,
                "sha256": source.sha256,
                "symbol": str(source.symbol),
                "timeframe": str(source.timeframe),
            }
            for source in manifest.sources
        ],
        "series": [
            {
                "bar_count": record.bar_count,
                "covered_interval": _interval_payload(record.covered_interval),
                "partitions": [_partition_payload(partition) for partition in record.partitions],
                "series_id": _series_payload(record.series_id),
            }
            for record in manifest.series
        ],
        "partitions": [
            {
                "bar_count": record.bar_count,
                "digest": {"algorithm": record.digest.algorithm, "hex": record.digest.hex},
                "interval": _interval_payload(record.interval),
                "partition_id": _partition_payload(record.partition_id),
            }
            for record in manifest.partitions
        ],
        "closure_decisions": [
            {
                "interval": _interval_payload(decision.interval),
                "kind": decision.kind.value,
                "note": decision.note,
                "series_id": _series_payload(decision.series_id),
            }
            for decision in manifest.closure_decisions
        ],
        "legacy_access": [
            {
                "interval": _interval_payload(record.interval),
                "note": record.note,
                "observation": record.observation.value,
                "series_id": _series_payload(record.series_id),
            }
            for record in manifest.legacy_access
        ],
    }
    if manifest.declaration_record is not None:
        payload["declaration_record"] = {
            "declared_at": str(manifest.declaration_record.declared_at),
            "declared_by": manifest.declaration_record.declared_by,
        }
    if manifest.approval is not None:
        payload["approval"] = {
            "approved_at": str(manifest.approval.approved_at),
            "approved_by": manifest.approval.approved_by,
            "comment": manifest.approval.comment,
        }
    return payload


def _manifest_from_payload(payload: Mapping[str, Any]) -> SnapshotManifest:
    """`manifest.json` の内容から manifest を復元する。"""
    version = payload.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported manifest schema_version {version!r};"
            f" this build reads version {MANIFEST_SCHEMA_VERSION}"
        )
    conversion = payload["conversion"]
    declaration = payload.get("declaration_record")
    approval = payload.get("approval")
    return SnapshotManifest(
        created_at=UtcTime.parse(str(payload["created_at"])),
        basis_declaration=BasisDeclaration(
            value=PriceBasis(str(payload["basis_declaration"]["value"])),
            verified=bool(payload["basis_declaration"]["verified"]),
        ),
        sources=tuple(
            SourceFile(
                path=str(source["path"]),
                sha256=str(source["sha256"]),
                rows=int(source["rows"]),
                symbol=Symbol(str(source["symbol"])),
                timeframe=TimeframeRef.parse(str(source["timeframe"])),
                declared_basis=PriceBasis(str(source["declared_basis"])),
                provenance_counts=tuple(
                    (str(entry["source"]), int(entry["count"]))
                    for entry in source["provenance_counts"]
                ),
            )
            for source in payload["sources"]
        ),
        conversion=ConversionRecord(
            code_version=str(conversion["code_version"]),
            time_convention=str(conversion["time_convention"]),
            aggregation_rule_version=str(conversion["aggregation_rule_version"]),
            calendar_id=str(conversion["calendar_id"]),
            calendar_version=int(conversion["calendar_version"]),
        ),
        series=tuple(
            SeriesManifest(
                series_id=_series_from_payload(record["series_id"]),
                covered_interval=_interval_from_payload(record["covered_interval"]),
                bar_count=int(record["bar_count"]),
                partitions=tuple(
                    _partition_from_payload(partition) for partition in record["partitions"]
                ),
            )
            for record in payload["series"]
        ),
        partitions=tuple(
            PartitionRecord(
                partition_id=_partition_from_payload(record["partition_id"]),
                interval=_interval_from_payload(record["interval"]),
                bar_count=int(record["bar_count"]),
                digest=ContentDigest(
                    algorithm=str(record["digest"]["algorithm"]),
                    hex=str(record["digest"]["hex"]),
                ),
            )
            for record in payload["partitions"]
        ),
        integrity_report_ref=ContentDigest(
            algorithm=str(payload["integrity_report_ref"]["algorithm"]),
            hex=str(payload["integrity_report_ref"]["hex"]),
        ),
        closure_decisions=tuple(
            ClosureDecision(
                series_id=_series_from_payload(decision["series_id"]),
                interval=_interval_from_payload(decision["interval"]),
                kind=ClosureDecisionKind(str(decision["kind"])),
                note=str(decision["note"]),
            )
            for decision in payload["closure_decisions"]
        ),
        legacy_access=tuple(
            LegacyAccessRecord(
                series_id=_series_from_payload(record["series_id"]),
                interval=_interval_from_payload(record["interval"]),
                observation=LegacyObservation(str(record["observation"])),
                note=str(record["note"]),
            )
            for record in payload["legacy_access"]
        ),
        declaration_record=None
        if declaration is None
        else DeclarationRecord(
            declared_by=str(declaration["declared_by"]),
            declared_at=UtcTime.parse(str(declaration["declared_at"])),
        ),
        approval=None
        if approval is None
        else Approval(
            approved_by=str(approval["approved_by"]),
            approved_at=UtcTime.parse(str(approval["approved_at"])),
            comment=str(approval["comment"]),
        ),
    )


def _report_from_payload(payload: Mapping[str, Any]) -> IntegrityReport:
    return IntegrityReport(
        results=tuple(
            CheckResult(
                kind=CheckKind(str(result["kind"])),
                severity=Severity(str(result["severity"])),
                series=_series_from_payload(result["series"]),
                interval=_interval_from_payload(result["interval"]),
                detail=tuple(
                    (str(entry["key"]), str(entry["value"])) for entry in result["detail"]
                ),
            )
            for result in payload["results"]
        )
    )


@dataclass(frozen=True, slots=True)
class ParquetSnapshotStore:
    """partition の Parquet と manifest を扱う（D03 §8）。

    `root` は `data/snapshots/`。`snapshot_dir` は `root` からの相対パス
    （`<snapshot_id>` または `_pending/<provisional_id>`）。
    """

    root: Path

    def _snapshot_path(self, snapshot_dir: str) -> Path:
        root = self.root.resolve()
        candidate = (root / snapshot_dir).resolve()
        if root != candidate and root not in candidate.parents:
            raise ValueError(f"{snapshot_dir!r} resolves outside the snapshot root {root}")
        return candidate

    # --- partition ----------------------------------------------------------

    def write_partition(
        self, snapshot_dir: str, partition_id: PartitionId, bars: Iterable[Bar]
    ) -> str:
        """partition の足を Parquet へ書き、内容のダイジェストを返す。

        価格・出来高は文字列列として保存する。Decimal を浮動小数へ落とすと、書いた値と
        読んだ値が一致しなくなるため（ADR-0012）。
        """
        target = self._snapshot_path(snapshot_dir) / partition_id.directory
        target.mkdir(parents=True, exist_ok=True)
        ordered = sorted(bars, key=lambda bar: bar.bar_start.value)
        rows = [partition_row(bar) for bar in ordered]
        frame = pl.DataFrame(
            {
                column: [row[index] for row in rows]
                for index, column in enumerate(PARTITION_COLUMNS)
            },
            schema=dict.fromkeys(PARTITION_COLUMNS, pl.String),
        )
        frame.write_parquet(target / _PARTITION_FILE)

        # ダイジェストは Parquet のバイト列ではなく論理的な内容から取る（D03 §3.7.1）。
        # 算法は application にあり、読み取り側が同じ値を再計算して照合できる。
        return partition_digest_hex(ordered)

    def read_partition(self, snapshot_dir: str, partition_id: PartitionId) -> Sequence[Bar]:
        """partition の足を開始時刻の昇順で読む。"""
        path = self._snapshot_path(snapshot_dir) / partition_id.directory / _PARTITION_FILE
        if not path.is_file():
            raise FileNotFoundError(f"partition file not found: {path}")
        frame = pl.read_parquet(path)
        series = partition_id.series
        bars: list[Bar] = []
        for record in frame.iter_rows(named=True):
            volume: Decimal = decimal_from_str(str(record["volume"]))
            bars.append(
                Bar(
                    series=series,
                    interval=Interval(
                        start=UtcTime.parse(str(record["bar_start"])),
                        end=UtcTime.parse(str(record["bar_end"])),
                    ),
                    open=Price(decimal_from_str(str(record["open"]))),
                    high=Price(decimal_from_str(str(record["high"]))),
                    low=Price(decimal_from_str(str(record["low"]))),
                    close=Price(decimal_from_str(str(record["close"]))),
                    volume=volume,
                    available_at=UtcTime.parse(str(record["available_at"])),
                    provenance=Provenance(
                        kind=ProvenanceKind(str(record["provenance_kind"])),
                        source_ref=str(record["provenance_ref"]),
                    ),
                )
            )
        # DataFrame はここで捨てる（D01 §2.2 規則1）。
        return tuple(sorted(bars, key=lambda bar: bar.bar_start.value))

    # --- manifest と検査報告 -------------------------------------------------

    def write_manifest(self, snapshot_dir: str, manifest: SnapshotManifest) -> None:
        """`manifest.json` を D03 §3.7.1 の正規順序で書く。"""
        target = self._snapshot_path(snapshot_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / _MANIFEST_FILE).write_text(_dumps(_manifest_payload(manifest)), encoding="utf-8")

    def read_manifest(self, snapshot_dir: str) -> SnapshotManifest:
        """`manifest.json` を読む（D03 §3.7.1）。

        ファイルに書かれた `snapshot_id` と、読み込んだ内容から**再計算した** `snapshot_id`
        の一致を確かめる。一致しない manifest は、内容が書き換えられたか識別子が改変された
        ものであり、そのまま読めば「別の内容を、記録された識別子の snapshot として」扱って
        しまう。
        """
        path = self._snapshot_path(snapshot_dir) / _MANIFEST_FILE
        if not path.is_file():
            raise FileNotFoundError(f"manifest not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = _manifest_from_payload(payload)

        recorded = str(payload.get("snapshot_id", ""))
        recomputed = str(manifest.snapshot_id())
        if recorded != recomputed:
            raise MarketDataValueError(
                f"{path} records snapshot id {recorded!r} but its content hashes to"
                f" {recomputed}; the manifest has been altered (D03 §3.7.1)"
            )
        return manifest

    def open_readable(self, snapshot_id: str) -> ReadableSnapshot:
        """確定・承認済みの snapshot を読み取り可能な形で開く（D03 §3.7.1 の 2・3）。

        `data/snapshots/<snapshot_id>/manifest.json` を読み、**3者の一致**を確かめる。

        1. ファイルに書かれた `snapshot_id`
        2. 内容から再計算した `snapshot_id`
        3. ディレクトリ名

        そのうえで `ReadableSnapshot` を作るので、暫定ディレクトリ（`_pending/`）配下の
        snapshot と、承認の無い snapshot は開けない。読み取り経路はこの型しか受け取らない
        ため、検査を通らない manifest でビューを作ることはできない。
        """
        if PENDING_DIRECTORY in PurePosixPath(snapshot_id).parts:
            raise SnapshotNotApproved(
                f"{snapshot_id!r} is a provisional snapshot; provisional snapshots are never"
                " approved and never readable (D03 §3.7.1 の 1・3)"
            )
        manifest = self.read_manifest(snapshot_id)
        return ReadableSnapshot(manifest=manifest, directory_name=snapshot_id)

    def write_integrity_report(self, snapshot_dir: str, report: IntegrityReport) -> str:
        """検査の報告を書き出し、内容のダイジェストを返す。

        正規化表現とダイジェストは application が持つ（`report_digest`）。書く文字列と
        ダイジェストの対象が同じなので、ファイルの内容と manifest の記録が食い違わない
        （D03 §3.7.1）。
        """
        target = self._snapshot_path(snapshot_dir)
        target.mkdir(parents=True, exist_ok=True)
        text = integrity_report_text(report)
        (target / _INTEGRITY_FILE).write_text(text, encoding="utf-8")
        return integrity_report_digest_hex(report)

    def read_integrity_report(self, snapshot_dir: str) -> IntegrityReport:
        """検査の報告を読む。"""
        path = self._snapshot_path(snapshot_dir) / _INTEGRITY_FILE
        if not path.is_file():
            raise FileNotFoundError(f"integrity report not found: {path}")
        return _report_from_payload(json.loads(path.read_text(encoding="utf-8")))

    # --- 閲覧記録（ADR-0014）------------------------------------------------

    def append_access_log(self, snapshot_dir: str, entry: Mapping[str, str]) -> None:
        """閲覧記録を `access_log.jsonl` へ1行追記する（ADR-0014）。

        追記専用。既存の行は読み書きしない。`entry` は
        `application.access_log.serialize_entry()` が作る文字列の mapping。
        """
        target = self._snapshot_path(snapshot_dir)
        target.mkdir(parents=True, exist_ok=True)
        line = json.dumps(dict(entry), ensure_ascii=False, sort_keys=True)
        with (target / "access_log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read_access_log(self, snapshot_dir: str) -> Sequence[Mapping[str, str]]:
        """`access_log.jsonl` の全行を追記順に読む。"""
        path = self._snapshot_path(snapshot_dir) / "access_log.jsonl"
        if not path.is_file():
            return ()
        entries: list[Mapping[str, str]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append({key: str(value) for key, value in json.loads(line).items()})
        return tuple(entries)
