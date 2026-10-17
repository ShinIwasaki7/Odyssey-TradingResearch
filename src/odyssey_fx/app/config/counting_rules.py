"""数え方の規則のファイルの読込（D03 §15.12、ADR-0018）。

`docs/design/counting_rules/counting_rules_v<版>.yaml` を読み、domain の `CountingRules` にする。
ファイルのバイト列の SHA-256 を計算して持つ（集計の再現の記録に書く。`SnapshotId` には入れない）。
null の項目は `None` のまま渡し、使う処理が `CountingRules.require` で止まる（既定値で埋めない）。

全期間の集計の道具（`tools/survey/full_period_feasibility.py` の `load_counting_rules`）と基盤の
実装が同じこの関数で同じファイルを読む（2026-10-17 の指示 3-C、D12 §11.13 の 15）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.marketdata.domain.counting_rules import CalendarDeclaration, CountingRules

__all__ = ["load_counting_rules"]

_SCHEMA_VERSION = 1


class _CalendarRef(StrictModel):
    id: str
    version: int
    file: str
    sha256: str | None = None


class _HolidayModel(StrictModel):
    file: str
    sha256: str


class _CalendarModel(StrictModel):
    base: _CalendarRef
    holiday_exceptions: _HolidayModel
    v2_2017_year_end_closures: str | None
    declared_as: _CalendarRef | None


class _TableModel(StrictModel):
    file: str
    approved_list: str


class _ShortfallModel(StrictModel):
    reasons: list[str]
    before_acquired_range: str
    reinterpret_as_unrecorded: bool
    bar_with_shortfall_minute: str
    run_policy: str


class _BarsModel(StrictModel):
    timeframes: list[str]
    built_from: str
    build_condition: str
    coverage_denominator: str
    coverage_threshold: str
    call_partial_bar_complete: bool


class _WarmupModel(StrictModel):
    daily_bars: int
    daily_bars_source: str
    partial_daily_bar_counts: bool
    closure_day_breaks_continuity: bool
    all_no_record_scheduled_day_resets: bool
    ready_at: str
    intraday_no_record_bar: str | None


class _SensitivityModel(StrictModel):
    variants: list[str] | None
    out_of_calendar_bars_in_candidate: str | None


class _RulesModel(StrictModel):
    schema_version: int
    id: str
    version: int
    source_sections: str
    time_rule_table: _TableModel
    calendar: _CalendarModel
    minute_status_order: list[str]
    acquisition_shortfall: _ShortfallModel
    bars: _BarsModel
    warmup: _WarmupModel
    sensitivity: _SensitivityModel


def _declaration(model: _CalendarRef, path: Path) -> CalendarDeclaration:
    if model.sha256 is None:
        raise ConfigError(f"{path}: calendar.declared_as には sha256 が要る")
    return CalendarDeclaration(
        calendar_id=model.id, version=model.version, file=model.file, sha256=model.sha256
    )


def load_counting_rules(path: Path) -> CountingRules:
    """数え方の規則のファイルを読む（D03 §15.12）。"""
    if not path.is_file():
        raise ConfigError(f"数え方の規則のファイルが見つからない: {path}")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    payload: dict[str, Any] = load_yaml_mapping(path)
    require_schema_version(payload["schema_version"], _SCHEMA_VERSION, path)
    model = validate(_RulesModel, payload, path)
    calendar = model.calendar
    base = calendar.base
    return CountingRules(
        rules_id=model.id,
        version=model.version,
        sha256=sha256,
        time_rule_table_file=model.time_rule_table.file,
        approved_list_file=model.time_rule_table.approved_list,
        calendar_base=CalendarDeclaration(
            calendar_id=base.id, version=base.version, file=base.file, sha256=base.sha256 or ""
        ),
        holiday_exceptions_file=calendar.holiday_exceptions.file,
        holiday_exceptions_sha256=calendar.holiday_exceptions.sha256,
        v2_2017_year_end_closures=calendar.v2_2017_year_end_closures,
        declared_calendar=None
        if calendar.declared_as is None
        else _declaration(calendar.declared_as, path),
        minute_status_order=tuple(model.minute_status_order),
        shortfall_reasons=tuple(model.acquisition_shortfall.reasons),
        before_acquired_range=model.acquisition_shortfall.before_acquired_range,
        reinterpret_as_unrecorded=model.acquisition_shortfall.reinterpret_as_unrecorded,
        bar_with_shortfall_minute=model.acquisition_shortfall.bar_with_shortfall_minute,
        run_policy=model.acquisition_shortfall.run_policy,
        timeframes=tuple(model.bars.timeframes),
        built_from=model.bars.built_from,
        build_condition=model.bars.build_condition,
        coverage_denominator=model.bars.coverage_denominator,
        coverage_threshold=model.bars.coverage_threshold,
        call_partial_bar_complete=model.bars.call_partial_bar_complete,
        warmup_daily_bars=model.warmup.daily_bars,
        warmup_daily_bars_source=model.warmup.daily_bars_source,
        partial_daily_bar_counts=model.warmup.partial_daily_bar_counts,
        closure_day_breaks_continuity=model.warmup.closure_day_breaks_continuity,
        all_no_record_scheduled_day_resets=model.warmup.all_no_record_scheduled_day_resets,
        ready_at=model.warmup.ready_at,
        intraday_no_record_bar=model.warmup.intraday_no_record_bar,
        variants=None if model.sensitivity.variants is None else tuple(model.sensitivity.variants),
        out_of_calendar_bars_in_candidate=model.sensitivity.out_of_calendar_bars_in_candidate,
    )
