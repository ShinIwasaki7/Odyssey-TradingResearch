"""品質確認の閲覧の記録の行の符号化と、partition の読み取りの許可の確かめ（ADR-0035 第4項）。

記録の行は 1 行 1 JSON（`research/quality_access_log.jsonl`）。本モジュールは行の型と JSON
互換の mapping の相互変換だけを担い、ファイルの読み書き（ロック・断片の切り詰め・読み直し）は
`marketdata.adapters.quality_access_log_store` が行う（application は I/O を持たない。D01 §2）。

**partition の読み取りの許可の確かめ**（ADR-0036 第9項の 3、ADR-0037 第1項の 3 の 3）:
OANDA MT5 の経路の 2 つの封印の区分の partition は、読み取りの許可の値（`QualityReadPermit`）が
覆うときだけ読める。値が無ければ読まない（研究履歴だけを読む）。旧データの snapshot の区分
（`LEGACY_HOLDOUT`・`QUARANTINED_UNASSIGNED`）の既存の扱い（as-of ビューの許可集合と
隔離の拒否。D03 §6.1）は変えない。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.access import OANDA_SEALED_CLASSES, AccessClass
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, MarketDataValueError
from odyssey_fx.marketdata.domain.quality_access import (
    AccessPeriod,
    PartitionTarget,
    QualityAccessEnd,
    QualityAccessJudgment,
    QualityAccessResult,
    QualityAccessRow,
    QualityAccessStart,
    QualityCheckKind,
    QualityOutputKind,
    QualityReadPermit,
    QualityRecordKind,
    QualityTarget,
    RawFileTarget,
    ToolRef,
)
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = [
    "QUALITY_ACCESS_LOG_SCHEMA_VERSION",
    "require_partition_read_allowed",
    "row_from_payload",
    "row_payload",
]

#: 行の形の版。
QUALITY_ACCESS_LOG_SCHEMA_VERSION: Final = 1

_START: Final = "START"
_END: Final = "END"
_JUDGMENT: Final = "JUDGMENT"


def require_partition_read_allowed(
    partition_id: PartitionId, read_permit: QualityReadPermit | None
) -> None:
    """partition を読んでよいかを確かめる。読めなければ `HoldoutAccessViolation`。

    OANDA MT5 の経路の封印の区分は、許可の値が覆うときだけ読める（関所を迂回する読み取りの
    経路を作らない。ADR-0036 第8項・第9項の 3）。他の区分はここでは拒否しない（既存の扱い）。
    """
    if not isinstance(partition_id, PartitionId):
        raise MarketDataValueError("require_partition_read_allowed requires a PartitionId")
    if partition_id.access_class not in OANDA_SEALED_CLASSES:
        return
    if read_permit is None:
        raise HoldoutAccessViolation(
            f"the sealed partition {partition_id} is read only under a quality-check permit of"
            " the holdout gate (acceptance) or by the final verification command; no permit was"
            " given (ADR-0036 第9項の 3, ADR-0037 第1項の 3 の 3)"
        )
    if not isinstance(read_permit, QualityReadPermit):
        raise MarketDataValueError("read_permit must be a QualityReadPermit")
    if not read_permit.covers_partition(partition_id):
        raise HoldoutAccessViolation(
            f"the quality-check permit {read_permit.permit_id} does not cover {partition_id}"
            " (ADR-0036 第7項の 3)"
        )


# --- 行の符号化 -------------------------------------------------------------------------


def _series_payload(series: SeriesId) -> dict[str, str]:
    return {
        "basis": series.basis.value,
        "symbol": str(series.symbol),
        "timeframe": str(series.timeframe),
    }


def _interval_payload(interval: Interval) -> dict[str, str]:
    return {"end": str(interval.end), "start": str(interval.start)}


def _target_payload(target: QualityTarget) -> dict[str, Any]:
    if isinstance(target, PartitionTarget):
        partition = target.partition_id
        payload: dict[str, Any] = {
            "access_class": partition.access_class.value,
            "interval": _interval_payload(target.interval),
            "kind": "PARTITION",
            "partition": str(partition),
            "series": _series_payload(partition.series),
        }
        if partition.period_start is not None:
            payload["period_start"] = str(partition.period_start)
        return payload
    return {
        "kind": "RAW_FILE",
        "location": target.location,
        "sha256": target.sha256,
        "symbol": None if target.symbol is None else str(target.symbol),
    }


def row_payload(row: QualityAccessRow) -> Mapping[str, Any]:
    """1 行を JSON 互換の mapping にする（鍵は固定。列は整列して書く）。"""
    if isinstance(row, QualityAccessStart):
        return {
            "access_id": row.access_id,
            "accessed_at": str(row.accessed_at),
            "check_kind": row.check_kind.value,
            "classes": sorted(item.value for item in row.classes),
            "output_kinds": sorted(item.value for item in row.output_kinds),
            "period": {
                "end": None if row.period.end is None else str(row.period.end),
                "start": str(row.period.start),
            },
            "permit_id": row.permit_id,
            "record_kind": row.record_kind.value,
            "row_id": row.row_id,
            "row_kind": _START,
            "schema_version": QUALITY_ACCESS_LOG_SCHEMA_VERSION,
            "targets": [_target_payload(target) for target in row.targets],
            "tool": {"name": row.tool.name, "version": row.tool.version},
        }
    if isinstance(row, QualityAccessEnd):
        return {
            "access_id": row.access_id,
            "finished_at": str(row.finished_at),
            "record_kind": row.record_kind.value,
            "result": row.result.value,
            "row_id": row.row_id,
            "row_kind": _END,
            "schema_version": QUALITY_ACCESS_LOG_SCHEMA_VERSION,
        }
    if isinstance(row, QualityAccessJudgment):
        return {
            "access_id": row.access_id,
            "judged_at": str(row.judged_at),
            "judged_by": row.judged_by,
            "row_id": row.row_id,
            "row_kind": _JUDGMENT,
            "schema_version": QUALITY_ACCESS_LOG_SCHEMA_VERSION,
            "violated_conditions": sorted(row.violated_conditions),
        }
    raise MarketDataValueError("row_payload requires a quality access row")


_START_KEYS: Final = frozenset(
    {
        "access_id",
        "accessed_at",
        "check_kind",
        "classes",
        "output_kinds",
        "period",
        "permit_id",
        "record_kind",
        "row_id",
        "row_kind",
        "schema_version",
        "targets",
        "tool",
    }
)
_END_KEYS: Final = frozenset(
    {
        "access_id",
        "finished_at",
        "record_kind",
        "result",
        "row_id",
        "row_kind",
        "schema_version",
    }
)
_JUDGMENT_KEYS: Final = frozenset(
    {
        "access_id",
        "judged_at",
        "judged_by",
        "row_id",
        "row_kind",
        "schema_version",
        "violated_conditions",
    }
)


def _mapping(value: object, label: str, keys: frozenset[str] | None = None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MarketDataValueError(f"{label} must be an object")
    if keys is not None and set(value) != keys:
        raise MarketDataValueError(
            f"{label} must have exactly the keys {sorted(keys)}, got {sorted(value)}"
        )
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise MarketDataValueError(f"{label} must be a string")
    return value


def _series_from(value: object) -> SeriesId:
    item = _mapping(value, "series", frozenset({"basis", "symbol", "timeframe"}))
    return SeriesId(
        symbol=Symbol(_text(item["symbol"], "series.symbol")),
        timeframe=TimeframeRef.parse(_text(item["timeframe"], "series.timeframe")),
        basis=PriceBasis(_text(item["basis"], "series.basis")),
    )


def _interval_from(value: object) -> Interval:
    item = _mapping(value, "interval", frozenset({"end", "start"}))
    return Interval(
        start=UtcTime.parse(_text(item["start"], "interval.start")),
        end=UtcTime.parse(_text(item["end"], "interval.end")),
    )


def _target_from(value: object) -> QualityTarget:
    item = _mapping(value, "target")
    kind = item.get("kind")
    if kind == "PARTITION":
        keys = {"access_class", "interval", "kind", "partition", "series"}
        if "period_start" in item:
            keys.add("period_start")
        _mapping(item, "target", frozenset(keys))
        period_start = item.get("period_start")
        partition = PartitionId(
            series=_series_from(item["series"]),
            access_class=AccessClass(_text(item["access_class"], "target.access_class")),
            period_start=None
            if period_start is None
            else UtcTime.parse(_text(period_start, "target.period_start")),
        )
        if str(partition) != item["partition"]:
            raise MarketDataValueError(
                f"target.partition {item['partition']!r} does not match its fields ({partition})"
            )
        return PartitionTarget(partition_id=partition, interval=_interval_from(item["interval"]))
    if kind == "RAW_FILE":
        _mapping(item, "target", frozenset({"kind", "location", "sha256", "symbol"}))
        sha256 = item["sha256"]
        symbol = item["symbol"]
        return RawFileTarget(
            location=_text(item["location"], "target.location"),
            sha256=None if sha256 is None else _text(sha256, "target.sha256"),
            symbol=None if symbol is None else Symbol(_text(symbol, "target.symbol")),
        )
    raise MarketDataValueError(f"target.kind must be PARTITION or RAW_FILE, got {kind!r}")


def _texts(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MarketDataValueError(f"{label} must be a list of strings")
    if value != sorted(set(value)):
        raise MarketDataValueError(f"{label} must be sorted without repeats")
    return value


def row_from_payload(payload: object) -> QualityAccessRow:
    """JSON から読んだ 1 行を行の型にする（形が合わなければ `MarketDataValueError`）。"""
    item = _mapping(payload, "row")
    kind = item.get("row_kind")
    if item.get("schema_version") != QUALITY_ACCESS_LOG_SCHEMA_VERSION or isinstance(
        item.get("schema_version"), bool
    ):
        raise MarketDataValueError(
            f"schema_version must be {QUALITY_ACCESS_LOG_SCHEMA_VERSION},"
            f" got {item.get('schema_version')!r}"
        )
    if kind == _START:
        _mapping(item, "start row", _START_KEYS)
        tool = _mapping(item["tool"], "tool", frozenset({"name", "version"}))
        period = _mapping(item["period"], "period", frozenset({"end", "start"}))
        end = period["end"]
        targets = item["targets"]
        if not isinstance(targets, list):
            raise MarketDataValueError("targets must be a list")
        return QualityAccessStart(
            row_id=_text(item["row_id"], "row_id"),
            access_id=_text(item["access_id"], "access_id"),
            accessed_at=UtcTime.parse(_text(item["accessed_at"], "accessed_at")),
            tool=ToolRef(
                name=_text(tool["name"], "tool.name"),
                version=_text(tool["version"], "tool.version"),
            ),
            classes=frozenset(AccessClass(value) for value in _texts(item["classes"], "classes")),
            period=AccessPeriod(
                start=UtcTime.parse(_text(period["start"], "period.start")),
                end=None if end is None else UtcTime.parse(_text(end, "period.end")),
            ),
            output_kinds=frozenset(
                QualityOutputKind(value) for value in _texts(item["output_kinds"], "output_kinds")
            ),
            permit_id=_text(item["permit_id"], "permit_id"),
            targets=tuple(_target_from(target) for target in targets),
            check_kind=QualityCheckKind(_text(item["check_kind"], "check_kind")),
            record_kind=QualityRecordKind(_text(item["record_kind"], "record_kind")),
        )
    if kind == _END:
        _mapping(item, "end row", _END_KEYS)
        return QualityAccessEnd(
            row_id=_text(item["row_id"], "row_id"),
            access_id=_text(item["access_id"], "access_id"),
            finished_at=UtcTime.parse(_text(item["finished_at"], "finished_at")),
            result=QualityAccessResult(_text(item["result"], "result")),
            record_kind=QualityRecordKind(_text(item["record_kind"], "record_kind")),
        )
    if kind == _JUDGMENT:
        _mapping(item, "judgment row", _JUDGMENT_KEYS)
        conditions = item["violated_conditions"]
        if (
            not isinstance(conditions, list)
            or not all(
                isinstance(value, int) and not isinstance(value, bool) for value in conditions
            )
            or conditions != sorted(set(conditions))
        ):
            raise MarketDataValueError(
                "violated_conditions must be a sorted list of distinct integers"
            )
        return QualityAccessJudgment(
            row_id=_text(item["row_id"], "row_id"),
            access_id=_text(item["access_id"], "access_id"),
            violated_conditions=frozenset(conditions),
            judged_at=UtcTime.parse(_text(item["judged_at"], "judged_at")),
            judged_by=_text(item["judged_by"], "judged_by"),
        )
    raise MarketDataValueError(f"row_kind must be START, END or JUDGMENT, got {kind!r}")
