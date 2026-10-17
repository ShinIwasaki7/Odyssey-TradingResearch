"""数え方の規則（D03 §15.12。`docs/design/counting_rules/counting_rules_v<版>.yaml`）。

全期間の集計の道具（PR-B）と基盤の実装（受入れ・足の生成・準備の数え方）が**同じファイル**を読む
ための型である（2026-10-17 の指示 3-C「調査用の道具と本番実装で規則を別々に持たせない」）。
読込（YAML の解釈とファイルのハッシュ）は `app.config.counting_rules` が行う。

**値が null の項目は要決定が未決の意味**で、その項目を使う処理は既定値で埋めずに失敗する
（`require`）。また、実装が持つ手順と規則の値が食い違えば、実装を黙って使わずに失敗する
（`require_supported`）。手順（1 分ごとの区分の順、足を作る条件など）は設計書が定めたもので、
ファイルの値はその宣言である。ファイルの値を変えた版が来たときに、古い手順のまま数えないための
照合である。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = ["CalendarDeclaration", "CountingRules", "PendingRule", "UnsupportedRule"]

T = TypeVar("T")


class PendingRule(MarketDataValueError):
    """null（要決定が未決）の項目を使おうとした（D03 §15.12）。"""


class UnsupportedRule(MarketDataValueError):
    """規則のファイルの値が、実装の持つ手順と食い違う（D03 §15.12）。"""


@dataclass(frozen=True, slots=True)
class CalendarDeclaration:
    """休場の判定に使うカレンダーの宣言（D03 §15.6 の 1、D12-93 (a)）。"""

    calendar_id: str
    version: int
    file: str
    sha256: str


#: 実装が持つ手順の値（D03 §15.2・§15.4・§15.7・§15.9。設計書が定めた手順の宣言）。
_IMPLEMENTED: dict[str, object] = {
    "minute_status_order": ("closed", "acquisition_shortfall", "recorded", "unrecorded"),
    "shortfall_reasons": (
        "outside_acquired_range",
        "request_failed_or_not_fetched",
        "corrupt",
        "time_not_interpretable",
    ),
    "before_acquired_range": "data_start",
    "reinterpret_as_unrecorded": False,
    "bar_with_shortfall_minute": "not_built",
    "run_policy": "not_runnable",
    "built_from": "1m",
    "build_condition": "at_least_one_recorded_minute_and_no_shortfall",
    "coverage_denominator": "expected_minutes_excluding_closures",
    "coverage_threshold": "none",
    "call_partial_bar_complete": False,
    "partial_daily_bar_counts": True,
    "closure_day_breaks_continuity": False,
    "all_no_record_scheduled_day_resets": True,
    "ready_at": "available_at_of_last_required_bar",
    "intraday_no_record_bar": "resets_like_daily",
    "v2_2017_year_end_closures": "inherit",
    "out_of_calendar_bars_in_candidate": "exclude_and_report",
}


@dataclass(frozen=True, slots=True)
class CountingRules:
    """数え方の規則 1 版（D03 §15.12）。`None` の項目は要決定が未決。"""

    rules_id: str
    version: int
    sha256: str
    time_rule_table_file: str
    approved_list_file: str
    calendar_base: CalendarDeclaration
    holiday_exceptions_file: str
    holiday_exceptions_sha256: str
    v2_2017_year_end_closures: str | None
    declared_calendar: CalendarDeclaration | None
    minute_status_order: tuple[str, ...]
    shortfall_reasons: tuple[str, ...]
    before_acquired_range: str
    reinterpret_as_unrecorded: bool
    bar_with_shortfall_minute: str
    run_policy: str
    timeframes: tuple[str, ...]
    built_from: str
    build_condition: str
    coverage_denominator: str
    coverage_threshold: str
    call_partial_bar_complete: bool
    warmup_daily_bars: int
    warmup_daily_bars_source: str
    partial_daily_bar_counts: bool
    closure_day_breaks_continuity: bool
    all_no_record_scheduled_day_resets: bool
    ready_at: str
    intraday_no_record_bar: str | None
    variants: tuple[str, ...] | None
    out_of_calendar_bars_in_candidate: str | None

    @staticmethod
    def require(value: T | None, item: str) -> T:
        """null の項目を使うときの関門（既定値で埋めない）。"""
        if value is None:
            raise PendingRule(
                f"the counting rule {item!r} is null, i.e. its decision is still pending;"
                " a processing step that needs it stops instead of filling a default (D03 §15.12)"
            )
        return value

    def calendar(self) -> CalendarDeclaration:
        """MT5 経路の休場の判定に使うカレンダーの宣言（D12-93 (a)）。"""
        return self.require(self.declared_calendar, "calendar.declared_as")

    def require_supported(self, *items: str) -> None:
        """指定した項目の値が実装の手順と同じことを確かめる（D03 §15.12）。

        項目を指定しなければ、実装が持つすべての手順の項目を照合する。
        """
        names = items or tuple(_IMPLEMENTED)
        for name in names:
            if name not in _IMPLEMENTED:
                raise MarketDataValueError(f"unknown counting rule item {name!r}")
            value = getattr(self, name)
            if value is None:
                self.require(value, name)
            if value != _IMPLEMENTED[name]:
                raise UnsupportedRule(
                    f"the counting rules {self.rules_id} v{self.version} declare {name}={value!r},"
                    f" but this implementation counts with {_IMPLEMENTED[name]!r};"
                    " a different rule needs a new implementation, not a silent default"
                    " (D03 §15.12)"
                )
        if not items:
            variants = self.require(self.variants, "sensitivity.variants")
            if not set(variants) <= {"standard", "candidate"}:
                raise UnsupportedRule(
                    f"the counting rules declare variants {variants!r}; this implementation builds"
                    " the standard and candidate variants only (D12-92 (a))"
                )
