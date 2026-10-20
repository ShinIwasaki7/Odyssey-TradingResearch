"""時刻の規則の表と承認済みの一覧の読込（D03 §15.9、ADR-0018。検査 T1）。

`docs/design/time_rules/time_rule_table_v<版>.yaml`（人間が承認した宣言）と
`docs/design/time_rules/approved_time_rule_tables.yaml`（承認済みの表の一覧）を読む。

- 表のファイルのバイト列の SHA-256 を計算し、承認済みの一覧の行（識別・版・SHA-256）と照合する。
  一覧に無い表、取り消された（`revoked` が null でない）表は受け付けない（検査 T1。D12-48）。
- 表の構造の検査（T2・T3・T5）は domain の `TimeRuleTable` が構築時に行う。

全期間の集計の道具（`tools/survey/full_period_final.py` の `load_time_rule_table`）は
この関数を呼んで、本番と同じ規則で変換の列を作る（D03 §15.9 の 5、D12 §11.13 の 15）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.time_rule import (
    TimeRuleMark,
    TimeRulePeriod,
    TimeRuleRow,
    TimeRuleTable,
)

__all__ = [
    "ApprovedTable",
    "approved_list_sha256",
    "load_approved_tables",
    "load_time_rule_table",
    "require_approved",
]

_SCHEMA_VERSION = 1


class _ConversionModel(StrictModel):
    name: str
    offset_hours: int


class _RowModel(StrictModel):
    row: int
    pairs: list[str]
    label: str
    periods: list[dict[str, date]]
    status: str
    mark: str
    standard: _ConversionModel
    candidates: list[_ConversionModel]
    evidence: dict[str, str]
    observed_scope: str
    extension_assumption: str
    decision: str


class _TableModel(StrictModel):
    schema_version: int
    id: str
    version: int
    judgment_criteria_version: int
    source_section: str
    approval: dict[str, Any]
    conversion_definition: str
    lookup_key: str
    coverage: dict[str, Any]
    marks: dict[str, str]
    rows: list[_RowModel]


class _ApprovedRowModel(StrictModel):
    id: str
    version: int
    file: str
    sha256: str
    judgment_criteria_version: int
    approved_by: list[str]
    file_recorded_by: str
    revoked: str | None


class _ApprovedModel(StrictModel):
    schema_version: int
    tables: list[_ApprovedRowModel]


@dataclass(frozen=True, slots=True)
class ApprovedTable:
    """承認済みの一覧の 1 行（D03 §15.9 の 2）。"""

    table_id: str
    version: int
    file: str
    sha256: str
    revoked: str | None


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise ConfigError(f"ファイルが見つからない: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approved_list_sha256(path: Path) -> str:
    """承認済みの一覧のファイルの SHA-256（run manifest に記録だけとして残す。D06 §18.9 の 3）。"""
    return _sha256(path)


def load_approved_tables(path: Path) -> tuple[ApprovedTable, ...]:
    """承認済みの表の一覧を読む。"""
    payload = load_yaml_mapping(path)
    require_schema_version(payload["schema_version"], _SCHEMA_VERSION, path)
    model = validate(_ApprovedModel, payload, path)
    return tuple(
        ApprovedTable(
            table_id=row.id,
            version=row.version,
            file=row.file,
            sha256=row.sha256,
            revoked=row.revoked,
        )
        for row in model.tables
    )


def require_approved(
    approved: tuple[ApprovedTable, ...], *, table_id: str, version: int, sha256: str
) -> ApprovedTable:
    """表の識別・版・SHA-256 が承認済みの一覧にあり、取り消されていないこと（T1。D12-48）。"""
    for row in approved:
        if row.sha256 != sha256:
            continue
        if row.table_id != table_id or row.version != version:
            raise MarketDataValueError(
                f"the time rule table with SHA-256 {sha256} is approved as {row.table_id}"
                f" v{row.version}, not {table_id} v{version} (D03 §15.9 T1)"
            )
        if row.revoked is not None:
            raise MarketDataValueError(
                f"the time rule table {table_id} v{version} ({sha256}) was revoked:"
                f" {row.revoked} (D03 §15.9 T1)"
            )
        return row
    raise MarketDataValueError(
        f"the time rule table {table_id} v{version} ({sha256}) is not in the approved list;"
        " a run accepts approved tables only (D12-48, D03 §15.9 T1)"
    )


def _period(raw: dict[str, date], path: Path, row: int) -> TimeRulePeriod:
    if set(raw) != {"from", "to"}:
        raise ConfigError(f"{path}: 行 {row} の期間は from と to だけを持つ")
    return TimeRulePeriod(start=raw["from"], end=raw["to"])


def load_time_rule_table(path: Path, approved_list: Path) -> TimeRuleTable:
    """表を読み、承認済みの一覧と照合して domain の表にする（D03 §15.9 の 1・2・4・5）。"""
    sha256 = _sha256(path)
    payload = load_yaml_mapping(path)
    require_schema_version(payload["schema_version"], _SCHEMA_VERSION, path)
    model = validate(_TableModel, payload, path)
    require_approved(
        load_approved_tables(approved_list),
        table_id=model.id,
        version=model.version,
        sha256=sha256,
    )
    coverage = model.coverage
    if set(coverage) != {"pairs", "from", "to"}:
        raise ConfigError(f"{path}: coverage は pairs・from・to を持つ")
    known_marks = {mark.value for mark in TimeRuleMark}
    if set(model.marks) != known_marks:
        raise ConfigError(f"{path}: marks の語彙が {sorted(known_marks)} と違う")
    rows: list[TimeRuleRow] = []
    for row in model.rows:
        try:
            mark = TimeRuleMark(row.mark)
        except ValueError:
            raise ConfigError(f"{path}: 行 {row.row} の mark {row.mark!r} が語彙に無い") from None
        rows.append(
            TimeRuleRow(
                row=row.row,
                pairs=tuple(row.pairs),
                periods=tuple(_period(item, path, row.row) for item in row.periods),
                mark=mark,
                standard_offset_hours=row.standard.offset_hours,
                candidate_offset_hours=tuple(item.offset_hours for item in row.candidates),
            )
        )
    start, end = coverage["from"], coverage["to"]
    if not isinstance(start, date) or not isinstance(end, date):
        raise ConfigError(f"{path}: coverage の from・to は日付")
    pairs = coverage["pairs"]
    if not isinstance(pairs, list) or not all(isinstance(item, str) for item in pairs):
        raise ConfigError(f"{path}: coverage.pairs は文字列の列")
    return TimeRuleTable(
        table_id=model.id,
        version=model.version,
        sha256=sha256,
        pairs=tuple(pairs),
        coverage_start=start,
        coverage_end=end,
        rows=tuple(rows),
    )
