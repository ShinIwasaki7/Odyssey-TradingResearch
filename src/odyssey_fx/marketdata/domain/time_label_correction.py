"""原データの時刻ラベルの宣言された補正（D03 §2・§3.7・§4 の v1.19 の追記）。

原データの一部の週（米国だけが夏時間で欧州が冬時間の週。HistData 由来の 2019〜2026 年の 26 週）は、
時刻のラベルが週全体で実際より 1 時間早い（D03 §2 の「原データの夏時間ズレ」）。受入れは列対応の
宣言の版 3（`legacy_merged_csv_v3`）に**宣言された補正規則**で、その週の足の開始時刻に補正量を
足してから区間を決める。原 CSV は変えない（上書きしない）。

本モジュールは規則の値（`TimeLabelCorrectionRule`）と、snapshot の manifest の `conversion` に
記録する補正の結果（`TimeLabelCorrectionRecord`）と、列挙した週を夏時間の暦から検算する関数
（`verify_correction_weeks`。2026-10-05 の人間の決定 DST-1）を持つ。補正の適用と補正の後の再検査は
`marketdata.application.acceptance`、設定ファイルの読込は `app.config.datasources` にある。

- **対象の週**は、補正の前のラベルで見た半開区間 `[週の開場 − 補正量, 週の閉場 − 補正量)` の UTC の
  値で宣言する（D03 §4）。区間に入る行だけを補正する。
- **対象の系列**は系列の文字列（`USDJPY/15m/bid`。`SeriesId` の `__str__` と同じ形）の明示の列。
- **列の正規順序**（D03 §3.7.1 の v1.19）: 対象の系列は文字列、対象の週は区間の開始、系列ごとの
  動かした足の本数は系列の文字列で整列する（設定に書いた順序によらず同じ `snapshot_id` にする）。
  同じ系列・同じ週を 2 度書いた宣言は拒否する。
- 補正しない週（2016〜2018 年と 2023 年 3 月）は列挙しない。その根拠は D03 §2 の事実と設定の注記に
  書くだけで、機械では確かめない（2026-10-05 の人間の決定 DST-3）。
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError, TimeLabelCorrectionFailed
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

__all__ = [
    "TimeLabelCorrectionRecord",
    "TimeLabelCorrectionRule",
    "correction_rule_of",
    "verify_correction_weeks",
]

#: 系列の文字列の形（`SeriesId.__str__` と同じ。銘柄 6 文字・時間足の id・価格基準）。
_SERIES_PATTERN: Final = re.compile(
    r"[A-Z]{6}/[a-z0-9_]+/(?:" + "|".join(member.value for member in PriceBasis) + r")"
)

#: 規則の識別の形（小文字・数字・下線）。
_RULE_ID_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*")


def _require_int(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketDataValueError(f"{label} must be an int, got {value!r}")
    if value < minimum:
        raise MarketDataValueError(f"{label} must be >= {minimum}, got {value}")
    return value


def _require_zone(key: object, label: str) -> str:
    if not isinstance(key, str) or not key:
        raise MarketDataValueError(f"{label} must be a non-empty str")
    try:
        ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise MarketDataValueError(f"{label}: unknown time zone {key!r}") from exc
    return key


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MarketDataValueError(f"{label} must be a mapping")
    return value


def _keys(mapping: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if frozenset(mapping) != expected:
        raise MarketDataValueError(
            f"{label}: keys {sorted(mapping)} do not match {sorted(expected)}"
        )


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list | tuple):
        raise MarketDataValueError(f"{label} must be a list")
    return list(value)


def _time(value: object, label: str) -> UtcTime:
    if not isinstance(value, str):
        raise MarketDataValueError(f"{label} must be a str")
    try:
        return UtcTime.parse(value)
    except KernelValueError as exc:
        raise MarketDataValueError(f"{label}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class TimeLabelCorrectionRule:
    """原データの時刻ラベルの補正規則 1 件の宣言（D03 §4 の v1.19 の追記・§9）。

    - `rule_id`・`rule_version`: 規則の識別と版（初版は `histdata_us_only_dst_weeks` の版 1）。
    - `shift`: 補正量（+1 時間）。補正の前のラベルにこの値を足す。正の秒単位の値に限る。
    - `series`: 対象の系列の文字列（`USDJPY/15m/bid`）。構築時に文字列の順に整列する。
    - `weeks`: 対象の週。補正の前のラベルで見た半開区間 `[週の開場 − shift, 週の閉場 − shift)`。
      構築時に開始の順に整列する。
    - `daylight_zone`・`standard_zone`: 週を導いた夏時間の暦。「開場の時点で `daylight_zone`
      （`America/New_York`）が夏時間で、`standard_zone`（`Europe/London`）が夏時間でない週」を
      対象にする（D03 §4 の「暦から導けること」）。

    同じ系列・同じ週を 2 度書いた宣言は拒否する（D03 §3.7.1 の v1.19）。
    """

    rule_id: str
    rule_version: int
    shift: timedelta
    series: tuple[str, ...]
    weeks: tuple[Interval, ...]
    daylight_zone: str
    standard_zone: str

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not _RULE_ID_PATTERN.fullmatch(self.rule_id):
            raise MarketDataValueError(
                f"TimeLabelCorrectionRule.rule_id must match {_RULE_ID_PATTERN.pattern},"
                f" got {self.rule_id!r}"
            )
        _require_int(self.rule_version, "TimeLabelCorrectionRule.rule_version", minimum=1)
        if not isinstance(self.shift, timedelta):
            raise MarketDataValueError("TimeLabelCorrectionRule.shift must be a timedelta")
        if self.shift <= timedelta(0) or self.shift % timedelta(seconds=1):
            raise MarketDataValueError(
                f"TimeLabelCorrectionRule.shift must be a positive whole number of seconds,"
                f" got {self.shift}"
            )
        if not isinstance(self.series, tuple) or not self.series:
            raise MarketDataValueError("TimeLabelCorrectionRule.series must be a non-empty tuple")
        for text in self.series:
            if not isinstance(text, str) or not _SERIES_PATTERN.fullmatch(text):
                raise MarketDataValueError(
                    f"TimeLabelCorrectionRule.series must look like USDJPY/1h/bid, got {text!r}"
                )
        twins = sorted({text for text in self.series if self.series.count(text) > 1})
        if twins:
            raise MarketDataValueError(
                f"TimeLabelCorrectionRule.series lists {twins} twice (D03 §3.7.1 の v1.19)"
            )
        if not isinstance(self.weeks, tuple) or not self.weeks:
            raise MarketDataValueError("TimeLabelCorrectionRule.weeks must be a non-empty tuple")
        for week in self.weeks:
            if not isinstance(week, Interval):
                raise MarketDataValueError("TimeLabelCorrectionRule.weeks must contain Interval")
        doubled = sorted({str(week) for week in self.weeks if self.weeks.count(week) > 1})
        if doubled:
            raise MarketDataValueError(
                f"TimeLabelCorrectionRule.weeks lists {doubled} twice (D03 §3.7.1 の v1.19)"
            )
        ordered = tuple(sorted(self.weeks, key=lambda week: week.start.value))
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if earlier.overlaps(later):
                raise MarketDataValueError(
                    f"TimeLabelCorrectionRule.weeks overlap: {earlier} and {later}"
                )
        _require_zone(self.daylight_zone, "TimeLabelCorrectionRule.daylight_zone")
        _require_zone(self.standard_zone, "TimeLabelCorrectionRule.standard_zone")
        object.__setattr__(self, "series", tuple(sorted(self.series)))
        object.__setattr__(self, "weeks", ordered)

    def week_of(self, label: UtcTime) -> Interval | None:
        """補正の前のラベル `label` が入る対象の週（入らなければ `None`）。"""
        starts = [week.start.value for week in self.weeks]
        index = bisect_right(starts, label.value) - 1
        if index >= 0 and self.weeks[index].contains(label):
            return self.weeks[index]
        return None

    def applies(self, series: SeriesId, label: UtcTime) -> bool:
        """系列が対象で、補正の前のラベルが対象の週に入るか。"""
        return str(series) in self.series and self.week_of(label) is not None

    def payload(self) -> Mapping[str, Any]:
        """manifest に記録する宣言の内容（JSON 互換。`snapshot_id` の対象。D03 §3.7）。"""
        return {
            "dst_zones": {"daylight": self.daylight_zone, "standard": self.standard_zone},
            "id": self.rule_id,
            "series": list(self.series),
            "shift_seconds": int(self.shift.total_seconds()),
            "version": self.rule_version,
            "weeks": [{"end": str(week.end), "start": str(week.start)} for week in self.weeks],
        }

    @classmethod
    def from_payload(cls, payload: object, label: str) -> TimeLabelCorrectionRule:
        """manifest の記録から読む。形が違えば `MarketDataValueError`。"""
        mapping = _mapping(payload, label)
        _keys(
            mapping,
            frozenset({"dst_zones", "id", "series", "shift_seconds", "version", "weeks"}),
            label,
        )
        zones = _mapping(mapping["dst_zones"], f"{label}.dst_zones")
        _keys(zones, frozenset({"daylight", "standard"}), f"{label}.dst_zones")
        weeks: list[Interval] = []
        for index, item in enumerate(_list(mapping["weeks"], f"{label}.weeks")):
            week = _mapping(item, f"{label}.weeks[{index}]")
            _keys(week, frozenset({"end", "start"}), f"{label}.weeks[{index}]")
            try:
                weeks.append(
                    Interval(
                        start=_time(week["start"], f"{label}.weeks[{index}].start"),
                        end=_time(week["end"], f"{label}.weeks[{index}].end"),
                    )
                )
            except KernelValueError as exc:
                raise MarketDataValueError(f"{label}.weeks[{index}]: {exc}") from exc
        rule = cls(
            rule_id=mapping["id"],
            rule_version=mapping["version"],
            shift=timedelta(
                seconds=_require_int(mapping["shift_seconds"], f"{label}.shift_seconds", minimum=1)
            ),
            series=tuple(_list(mapping["series"], f"{label}.series")),
            weeks=tuple(weeks),
            daylight_zone=zones["daylight"],
            standard_zone=zones["standard"],
        )
        if list(mapping["series"]) != list(rule.series) or tuple(weeks) != rule.weeks:
            raise MarketDataValueError(f"{label}: the series and weeks must be written sorted")
        return rule


def correction_rule_of(record: TimeLabelCorrectionRecord | None) -> TimeLabelCorrectionRule | None:
    """manifest の記録から当てた規則を取り出す（記録が無ければ「補正なし」の `None`）。"""
    return None if record is None else record.rule


@dataclass(frozen=True, slots=True)
class TimeLabelCorrectionRecord:
    """snapshot の manifest の `conversion` に記録する補正の結果（D03 §3.7・§4 の v1.19 の追記）。

    - `rule`: 当てた補正規則の宣言の内容。
    - `shifted_bar_counts`: 系列ごとの動かした足の本数 `(系列の文字列, 本数)`。受け入れた原
      ファイルの系列のうち規則の対象の系列について、0 本の系列も含めて持つ。系列の文字列で整列する。
    - `recheck_duplicates`・`recheck_out_of_calendar`: 補正の後の再検査の結果（補正していない足と
      重なる足の数、カレンダーで休場の時間帯に入る足の数）。受入れはどちらかが 1 以上なら止まる
      ので、記録される値は 0 である（D03 §4）。

    価格は持たない（封印期間の週も件数と区間だけ）。
    """

    rule: TimeLabelCorrectionRule
    shifted_bar_counts: tuple[tuple[str, int], ...]
    recheck_duplicates: int = 0
    recheck_out_of_calendar: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.rule, TimeLabelCorrectionRule):
            raise MarketDataValueError("TimeLabelCorrectionRecord.rule must be a rule")
        if not isinstance(self.shifted_bar_counts, tuple):
            raise MarketDataValueError(
                "TimeLabelCorrectionRecord.shifted_bar_counts must be a tuple"
            )
        seen: set[str] = set()
        for entry in self.shifted_bar_counts:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise MarketDataValueError(
                    "TimeLabelCorrectionRecord.shifted_bar_counts must hold (series, count) pairs"
                )
            name, count = entry
            if name not in self.rule.series:
                raise MarketDataValueError(
                    f"TimeLabelCorrectionRecord counts {name!r}, which the rule does not target"
                )
            if name in seen:
                raise MarketDataValueError(f"TimeLabelCorrectionRecord counts {name!r} twice")
            seen.add(name)
            _require_int(count, f"TimeLabelCorrectionRecord count of {name}", minimum=0)
        _require_int(self.recheck_duplicates, "recheck_duplicates", minimum=0)
        _require_int(self.recheck_out_of_calendar, "recheck_out_of_calendar", minimum=0)
        object.__setattr__(
            self, "shifted_bar_counts", tuple(sorted(self.shifted_bar_counts, key=lambda e: e[0]))
        )

    def count_of(self, series: SeriesId) -> int | None:
        """系列の動かした足の本数（記録に無い系列は `None`）。"""
        return dict(self.shifted_bar_counts).get(str(series))

    def payload(self) -> Mapping[str, Any]:
        """manifest に書く形（JSON 互換。`snapshot_id` の対象。D03 §3.7）。"""
        return {
            "recheck": {
                "duplicates": self.recheck_duplicates,
                "out_of_calendar": self.recheck_out_of_calendar,
            },
            "rule": self.rule.payload(),
            "shifted_bar_counts": [
                {"count": count, "series": name} for name, count in self.shifted_bar_counts
            ],
        }

    @classmethod
    def from_payload(cls, payload: object, label: str) -> TimeLabelCorrectionRecord:
        """manifest の記録から読む。形が違えば `MarketDataValueError`。"""
        mapping = _mapping(payload, label)
        _keys(mapping, frozenset({"recheck", "rule", "shifted_bar_counts"}), label)
        recheck = _mapping(mapping["recheck"], f"{label}.recheck")
        _keys(recheck, frozenset({"duplicates", "out_of_calendar"}), f"{label}.recheck")
        counts: list[tuple[str, int]] = []
        for index, item in enumerate(
            _list(mapping["shifted_bar_counts"], f"{label}.shifted_bar_counts")
        ):
            entry = _mapping(item, f"{label}.shifted_bar_counts[{index}]")
            _keys(entry, frozenset({"count", "series"}), f"{label}.shifted_bar_counts[{index}]")
            counts.append((entry["series"], entry["count"]))
        record = cls(
            rule=TimeLabelCorrectionRule.from_payload(mapping["rule"], f"{label}.rule"),
            shifted_bar_counts=tuple(counts),
            recheck_duplicates=recheck["duplicates"],
            recheck_out_of_calendar=recheck["out_of_calendar"],
        )
        if [name for name, _ in counts] != [name for name, _ in record.shifted_bar_counts]:
            raise MarketDataValueError(f"{label}.shifted_bar_counts must be written sorted")
        return record


def verify_correction_weeks(rule: TimeLabelCorrectionRule, calendar: TradingCalendar) -> None:
    """列挙した週を夏時間の暦から計算し直して検算する（D03 §4 の v1.19。人間の決定 DST-1）。

    列挙した週はどれも、カレンダーの週の開場区間（`weekly_session_at`。休場を適用する前の区間）の
    うち、**開場の時点で `daylight_zone` が夏時間で `standard_zone` が夏時間でない週**であり、区間は
    その週の開場区間を補正量だけ前へずらしたものでなければならない。満たさない週が 1 つでもあれば
    `TimeLabelCorrectionFailed`（構造エラー）で止め、満たさない週と理由を列挙する。

    逆向き（条件を満たす週がすべて列挙されていること）は求めない。ラベルが正しい週（2016〜2018 年と
    2023 年 3 月）を列挙しないためである（人間の決定 DST-3）。
    """
    if not isinstance(rule, TimeLabelCorrectionRule):
        raise MarketDataValueError("verify_correction_weeks requires a TimeLabelCorrectionRule")
    if not isinstance(calendar, TradingCalendar):
        raise MarketDataValueError("verify_correction_weeks requires a TradingCalendar")
    daylight = ZoneInfo(rule.daylight_zone)
    standard = ZoneInfo(rule.standard_zone)
    problems: list[str] = []
    for week in rule.weeks:
        opening = week.start + rule.shift
        session = calendar.weekly_session_at(opening)
        if session is None:
            problems.append(f"{week}: {opening} is not inside a weekly session of the calendar")
            continue
        expected = Interval(start=week.start + rule.shift, end=week.end + rule.shift)
        if session != expected:
            problems.append(
                f"{week}: shifted by {rule.shift} it is {expected}, but the weekly session is"
                f" {session}"
            )
            continue
        moment = session.start.value
        on_daylight = moment.astimezone(daylight).dst() not in (None, timedelta(0))
        on_standard = moment.astimezone(standard).dst() not in (None, timedelta(0))
        if not on_daylight or on_standard:
            problems.append(
                f"{week}: at the session open {session.start} {rule.daylight_zone} is"
                f" {'on' if on_daylight else 'off'} daylight saving time and"
                f" {rule.standard_zone} is {'on' if on_standard else 'off'}; the rule targets"
                f" weeks where only {rule.daylight_zone} is on it"
            )
    if problems:
        raise TimeLabelCorrectionFailed(
            f"{len(problems)} declared week(s) of {rule.rule_id} version {rule.rule_version} are"
            f" not derivable from the daylight saving calendars: {problems}. Nothing was written"
            " (D03 §4 v1.19, human decision DST-1)"
        )
