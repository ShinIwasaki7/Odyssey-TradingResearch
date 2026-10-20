"""全期間のデータ上の実行可能性の最終集計（人間の指示 2026-10-17 の B。§3-B の 9 項目）。

数え方の設計（PR-A、#84）で確定した 2 つの規則ファイルを読み、規則の値をコードに持たない
（D03 §15.12「調査用の道具と本番実装で規則を別々に持たせない」）。

- 数え方の規則: ``docs/design/counting_rules/counting_rules_v1.yaml``（``load_counting_rules``）
- 時刻の規則の表: ``docs/design/time_rules/time_rule_table_v1.yaml``（``load_time_rule_table``。
  読込と検査は基盤の ``app.config.time_rules.load_time_rule_table`` に任せる。承認済みの一覧
  ``approved_time_rule_tables.yaml`` とハッシュを照合する。D03 §15.9 の T1）
- 取引カレンダー: 規則ファイルの ``calendar.declared_as``（``fx_ny17_oanda`` 版 1。SHA-256 を照合）

手順（D03 §15.2〜§15.7、D11 §12、D06 §18.9）:

1. 原データを manifest と照合し（``verify_full_dir``）、時刻の列だけを読む（価格は読まない）。
   破損・重複・分に揃わない時刻があれば受入れと同じく失敗にする（D03 §15.2 (c)・§15.3 の 4）。
2. 変換の組（``standard``・``candidate``。D12-92 (a)）ごとに、API の生の時刻を表で UTC に直す。
3. 取引カレンダーのセッションの分を、休場 → 取得不足 → 記録あり → 無記録 の順に区分する。
   取得の範囲の最初より前はデータの開始前（取得不足に数えない）。
   カレンダーの外に出た足は、``standard`` では休場帯の足（``UNEXPECTED_BAR``）、``candidate`` では
   候補の変換でカレンダーの外に出た足（``CANDIDATE_OUT_OF_CALENDAR``）として週ごとに数え、
   足の生成に使わない。
4. 15 分足・1 時間足・4 時間足・日足を 1 分足から直接作る条件で区分する（全分観測・部分観測・
   無記録の足・取得不足の足）。被覆率の分母は休場を除く存在すべき分。閾値は置かない。
5. 連続区間は取得不足でだけ切る。準備は日足で数え（部分観測は 1 本、休場は途切れない、予定取引日の
   全分無記録でやり直し）、準備完了は必要本数の最後の日足の公開時刻（区間の終わり）。
6. 候補の間の差、8 通貨の共通区間（参考）を出す。

**これは「データ上の実行可能性」の集計で、エンジンの正常動作や実際の市場での約定の再現性は
確かめていない。** 価格は読まない・出さない。戦略の成績は使わない。

実行（リポジトリの根で）::

    uv run python -m tools.survey.full_period_final \\
        --root <原データの置き場>/data/raw/oanda_mt5/2026-10-16 \\
        --out-dir docs/data/full_period_feasibility
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import shlex
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.time_rules import (
    load_time_rule_table as load_approved_time_rule_table,
)
from odyssey_fx.common.time import Interval
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tools.survey.full_period_feasibility import (
    COVERAGE_COLUMNS,
    LONG_RUN_COLUMNS,
    MONTHLY_COLUMNS,
    NEW_YORK,
    PERIOD_END,
    PERIOD_START,
    RUN_BINS,
    RUN_DIST_COLUMNS,
    FeasibilityError,
    MinuteStatus,
    PairSeries,
    _count_in,
    _epoch,
    _git_head,
    _utc,
    _write_csv,
    classify_minutes,
    coverage_row,
    iso,
    long_run_rows,
    monthly_rows,
    period_bounds,
    read_pair,
    root_label,
    run_distribution_rows,
    sha256_file,
)
from tools.survey.mt5_api_probe import FULL_FETCH_LOG, FULL_MANIFEST, FULL_PAIRS, verify_full_dir
from tools.survey.mt5_gap_aggregate import expected_minutes

TOOL_VERSION = 1
COUNTING_RULES_DEFAULT = Path("docs/design/counting_rules/counting_rules_v1.yaml")
DOCS_MANIFEST_DEFAULT = Path("docs/data/oanda_mt5_full_fetch_2026-10-16/manifest.json")
TIMEFRAMES_DEFAULT = Path("configs/calendars/timeframes_v1.yaml")
OUT_DIR_DEFAULT = Path("docs/data/full_period_feasibility")
DAILY = "1d_ny17"
STANDARD = "standard"
CANDIDATE = "candidate"


class UnsupportedRuleError(FeasibilityError):
    """規則ファイルの値が、この道具が実装していない値（または未決の null）のとき。"""


# --- 入力点 1: 数え方の規則 -----------------------------------------------------------

#: この道具が実装している値（ファイルの値が違えば、黙って別の数え方をせずに失敗する）
SUPPORTED: dict[str, Any] = {
    "minute_status_order": ["closed", "acquisition_shortfall", "recorded", "unrecorded"],
    "acquisition_shortfall.before_acquired_range": "data_start",
    "acquisition_shortfall.reinterpret_as_unrecorded": False,
    "acquisition_shortfall.bar_with_shortfall_minute": "not_built",
    "bars.built_from": "1m",
    "bars.build_condition": "at_least_one_recorded_minute_and_no_shortfall",
    "bars.coverage_denominator": "expected_minutes_excluding_closures",
    "bars.coverage_threshold": "none",
    "bars.call_partial_bar_complete": False,
    "warmup.partial_daily_bar_counts": True,
    "warmup.closure_day_breaks_continuity": False,
    "warmup.all_no_record_scheduled_day_resets": True,
    "warmup.ready_at": "available_at_of_last_required_bar",
    "warmup.intraday_no_record_bar": "resets_like_daily",
    "sensitivity.variants": [STANDARD, CANDIDATE],
    "sensitivity.out_of_calendar_bars_in_candidate": "exclude_and_report",
}


@dataclass(frozen=True, slots=True)
class CountingRules:
    """数え方の規則 版 N（D03 §15.12）。値はすべてファイルから読む。"""

    path: str
    sha256: str
    rule_id: str
    version: int
    time_rule_table_file: Path
    approved_list_file: Path
    calendar_file: Path
    calendar_sha256: str
    calendar_id: str
    calendar_version: int
    timeframes: tuple[str, ...]
    warmup_daily_bars: int
    warmup_daily_bars_source: str
    variants: tuple[str, ...]


def _find_null(node: Any, where: str) -> str | None:
    if node is None:
        return where
    if isinstance(node, Mapping):
        for key, value in node.items():
            hit = _find_null(value, f"{where}.{key}" if where else str(key))
            if hit:
                return hit
    if isinstance(node, list):
        for i, value in enumerate(node):
            hit = _find_null(value, f"{where}[{i}]")
            if hit:
                return hit
    return None


def _dig(payload: Mapping[str, Any], dotted: str) -> Any:
    node: Any = payload
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise UnsupportedRuleError(f"数え方の規則に項目 {dotted} が無い")
        node = node[part]
    return node


def load_counting_rules(path: Path) -> CountingRules:
    """入力点 1: 数え方の規則ファイルを読む（D03 §15.12）。

    null の項目（未決）があれば失敗する。この道具が実装していない値でも失敗する（別の数え方で
    黙って埋めない）。カレンダーの SHA-256 は宣言の値と照合する。
    """
    payload = load_yaml_mapping(path)
    if payload.get("id") != "oanda_mt5_counting_rules":
        raise UnsupportedRuleError(f"{path}: 数え方の規則の id が違う")
    null = _find_null(payload, "")
    if null:
        raise UnsupportedRuleError(f"{path}: 項目 {null} が null（要決定が未決）")
    for dotted, want in SUPPORTED.items():
        got = _dig(payload, dotted)
        if got != want:
            raise UnsupportedRuleError(
                f"{path}: {dotted} = {got!r} はこの道具が実装していない値（実装は {want!r}）"
            )
    declared = _dig(payload, "calendar.declared_as")
    calendar_file = Path(str(declared["file"]))
    if sha256_file(calendar_file) != declared["sha256"]:
        raise UnsupportedRuleError(f"{calendar_file} の SHA-256 が規則ファイルの宣言と一致しない")
    daily = _dig(payload, "warmup.daily_bars")
    if not isinstance(daily, int) or isinstance(daily, bool) or daily < 1:
        raise UnsupportedRuleError(f"{path}: warmup.daily_bars が正の整数ではない")
    return CountingRules(
        path=path.as_posix(),
        sha256=sha256_file(path),
        rule_id=str(payload["id"]),
        version=int(payload["version"]),
        time_rule_table_file=Path(str(_dig(payload, "time_rule_table.file"))),
        approved_list_file=Path(str(_dig(payload, "time_rule_table.approved_list"))),
        calendar_file=calendar_file,
        calendar_sha256=str(declared["sha256"]),
        calendar_id=str(declared["id"]),
        calendar_version=int(declared["version"]),
        timeframes=tuple(str(t) for t in _dig(payload, "bars.timeframes")),
        warmup_daily_bars=daily,
        warmup_daily_bars_source=str(_dig(payload, "warmup.daily_bars_source")),
        variants=tuple(str(v) for v in _dig(payload, "sensitivity.variants")),
    )


def verify_warmup_source(rules: CountingRules) -> None:
    """``warmup.daily_bars`` が出典の戦略の宣言の値と一致するか（D03 §15.12）。"""
    source = rules.warmup_daily_bars_source
    file_match = re.match(r"(\S+\.yaml)", source)
    inst_match = re.search(r"instance_id (\S+?)（", source)
    if not file_match or not inst_match:
        raise UnsupportedRuleError(f"warmup.daily_bars_source を読めない: {source}")
    strategy = load_yaml_mapping(Path(file_match.group(1)))
    for component in strategy.get("components", []):
        if component.get("instance_id") == inst_match.group(1):
            value = component["parameters"]["window_bars"]["value"]
            if int(value) != rules.warmup_daily_bars:
                raise UnsupportedRuleError(
                    f"warmup.daily_bars {rules.warmup_daily_bars} が戦略の宣言 {value} と違う"
                )
            return
    raise UnsupportedRuleError(f"戦略の宣言に instance_id {inst_match.group(1)} が無い")


# --- 入力点 2: 時刻の規則の表 ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RulePeriod:
    row: int
    start: date  # 含む
    end: date  # 含まない
    mark: str  # 区分の印（表の mark の値。表示と照合にだけ使う）
    standard_hours: int
    candidate_hours: int | None


@dataclass(frozen=True, slots=True)
class TableConversion:
    """時刻の規則の表の 1 つの変換の組（``standard`` か ``candidate``。D03 §15.9 の 5）。"""

    name: str
    table_sha256: str
    periods: Mapping[str, tuple[RulePeriod, ...]]

    def period(self, pair: str, day: date) -> RulePeriod:
        periods = self.periods.get(pair)
        if not periods:
            raise FeasibilityError(f"時刻の規則の表に通貨 {pair} が無い（取得不足 (d)）")
        i = bisect.bisect_right([p.start for p in periods], day) - 1
        if i < 0 or not periods[i].start <= day < periods[i].end:
            raise FeasibilityError(f"時刻の規則の表が {pair} の {day} を覆わない（取得不足 (d)）")
        return periods[i]

    def hours_of(self, period: RulePeriod) -> int:
        if self.name == CANDIDATE and period.candidate_hours is not None:
            return period.candidate_hours
        return period.standard_hours

    def offset_hours(self, pair: str, api_epoch: int) -> int:
        day = datetime.fromtimestamp(api_epoch, UTC).date()
        return self.hours_of(self.period(pair, day))

    def offset_or_none(self, pair: str, api_epoch: int) -> int | None:
        """表が覆わない通貨・日なら None（取得不足 (d)「時刻を解釈できない」）。"""
        try:
            return self.offset_hours(pair, api_epoch)
        except FeasibilityError:
            return None

    def max_offset_hours(self) -> int:
        return max(
            max(p.standard_hours, p.candidate_hours or p.standard_hours)
            for periods in self.periods.values()
            for p in periods
        )

    def row_of_utc(self, pair: str, utc_epoch: int) -> RulePeriod:
        """取引時間の中の分の行（UTC の暦日で引いても同じ行。D03 §15.9 の 3・T4）。"""
        return self.period(pair, datetime.fromtimestamp(utc_epoch, UTC).date())


def load_time_rule_table(path: Path, approved_list: Path) -> list[TableConversion]:
    """入力点 2: 時刻の規則の表を読み、[standard, candidate] の変換の組を返す（D03 §15.9 の 5）。

    読込と検査は基盤の ``app.config.time_rules.load_time_rule_table`` が行う（本番の受入れと同じ
    規則。D03 §15.12「調査用の道具と本番実装で規則を別々に持たせない」）: T1 承認済みの一覧に
    SHA-256・識別・版があり取り消されていない、T2 通貨ごとに期間が重ならず対象期間を隙間なく覆う、
    T3 境目（両端を除く）が日曜、T5 区分と候補の対応、と表の形（未宣言のキーを拒否）。
    基盤の検査の失敗は ``FeasibilityError`` に包んで返す（集計の道具の失敗の型をそろえる）。
    必須のキーが無い表は基盤が ``ConfigError`` にするが、``KeyError`` が漏れても同じく包む
    （終了コード 2 を保つ。人間の決定 2026-10-20 の 1）。

    本関数が足すのは、``candidate`` の組の作り方（D12-92 (a)）が候補 1 つの行だけで決まっている
    ことの検査（候補が 2 つ以上の行があれば読込で止める）と、通貨ごとの期間の並びへの組み替えだけ。
    """
    try:
        table = load_approved_time_rule_table(path, approved_list)
    except (ConfigError, MarketDataValueError, KeyError) as exc:
        raise FeasibilityError(f"{path} の時刻の規則の表を受け付けない: {exc}") from exc
    per_pair: dict[str, list[RulePeriod]] = {pair: [] for pair in table.pairs}
    for row in table.rows:
        cands = row.candidate_offset_hours
        if len(cands) > 1:
            raise FeasibilityError(
                f"表の行 {row.row} の候補が 1 つでない（T5。組の作り方が未定義）"
            )
        for pair in row.pairs:
            for period in row.periods:
                per_pair[pair].append(
                    RulePeriod(
                        row=row.row,
                        start=period.start,
                        end=period.end,
                        mark=row.mark.value,
                        standard_hours=row.standard_offset_hours,
                        candidate_hours=cands[0] if cands else None,
                    )
                )
    frozen = {
        pair: tuple(sorted(periods, key=lambda p: p.start)) for pair, periods in per_pair.items()
    }
    return [TableConversion(name, table.sha256, frozen) for name in (STANDARD, CANDIDATE)]


# --- 足の区分 ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BarStat:
    """上位足 1 本の区分と被覆（D03 §15.4）。価格は持たない。"""

    timeframe: str
    start: int
    end: int  # 区間の終わり＝公開時刻（遅延なし。D03 §15.5）
    expected: int  # 存在すべき分（休場を除く。取得不足を含む）
    recorded: int
    shortfall: int
    first_observed: int | None
    last_observed: int | None

    @property
    def unrecorded(self) -> int:
        return self.expected - self.recorded - self.shortfall

    @property
    def kind(self) -> str:
        if self.shortfall:
            return "shortfall"  # 取得不足の足（作らない）
        if self.recorded == 0:
            return "no_record"  # 無記録の足（作らない）
        if self.recorded == self.expected:
            return "full"  # 全分観測の足
        return "partial"  # 部分観測の足（「完全」と呼ばない）

    @property
    def built(self) -> bool:
        return self.kind in ("full", "partial")

    @property
    def coverage(self) -> float | None:
        return self.recorded / self.expected if self.expected else None


def bar_intervals(
    calendar: TradingCalendar, timeframes: Mapping[str, TimeframeDefinition], tf: str
) -> list[tuple[int, int]]:
    """研究期間の、カレンダーで切り詰めた足の区間（丸ごと休場の足は含まない）。"""
    lo, hi = period_bounds()
    definition = timeframes[tf]
    out: list[tuple[int, int]] = []
    for start in calendar.expected_bar_starts(definition, Interval(start=_utc(lo), end=_utc(hi))):
        interval = definition.expected_interval(calendar, start)
        if interval is None:  # pragma: no cover - expected_bar_starts が除いている
            continue
        s, e = _epoch(interval.start), _epoch(interval.end)
        if lo <= s and e <= hi:
            out.append((s, e))
    return out


def bar_stats(status: MinuteStatus, tf: str, intervals: Sequence[tuple[int, int]]) -> list[BarStat]:
    out: list[BarStat] = []
    rec = status.recorded
    for s, e in intervals:
        expected = _count_in(status.expected, s, e)
        if expected == 0:
            continue  # データの開始前など、存在すべき分が無い区間
        lo = bisect.bisect_left(rec, s)
        hi = bisect.bisect_left(rec, e)
        out.append(
            BarStat(
                timeframe=tf,
                start=s,
                end=e,
                expected=expected,
                recorded=hi - lo,
                shortfall=_count_in(status.shortfall, s, e),
                first_observed=rec[lo] if hi > lo else None,
                last_observed=rec[hi - 1] if hi > lo else None,
            )
        )
    return out


def trading_day_label(calendar: TradingCalendar, bar: BarStat) -> str:
    end = datetime.fromtimestamp(bar.end, UTC).astimezone(calendar.tz)
    return (end - timedelta(microseconds=1)).date().isoformat()


def closure_days(labels: set[str]) -> list[str]:
    """月〜金の取引日のうち、カレンダーで丸ごと休場の日（日足が存在しない日）。"""
    out: list[str] = []
    day = date(2017, 1, 2)
    while day <= date(2023, 12, 29):
        if day.weekday() < 5 and day.isoformat() not in labels:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


# --- 1 通貨 × 1 変換の組の集計 ------------------------------------------------------------


@dataclass(slots=True)
class PairVariant:
    pair: str
    variant: str
    status: MinuteStatus
    utc_times: list[int]
    bars: dict[str, list[BarStat]]
    outside_by_week: Counter[str]
    data_start: int
    uninterpretable_bars: int  # 時刻の規則の表が覆わない日の足（取得不足 (d)。使わない）
    days: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    resets: list[dict[str, Any]] = field(default_factory=list)
    windows: list[dict[str, Any]] = field(default_factory=list)


def _week_sunday(utc_epoch: int) -> str:
    local = datetime.fromtimestamp(utc_epoch, UTC).astimezone(NEW_YORK)
    if local.hour >= 17 and local.weekday() == 6:
        sunday = local.date()
    else:
        sunday = local.date() - timedelta(days=(local.weekday() + 1) % 7)
    return sunday.isoformat()


DAY = 86400


def convert_with_table(
    series: PairSeries, conversion: TableConversion
) -> tuple[list[int], list[tuple[int, int]], int, int]:
    """API の生の時刻を表の変換で UTC に直す（生の時刻は変えない。D03 §15.3 の 3・5）。

    返り値は (UTC の時刻の列, 取得の範囲（UTC の区間の列）, データの開始, 解釈できない足の数)。

    - 表が覆わない通貨・日（取得不足 (d)）の足は使わず数え、その日は取得の範囲から外す
      （存在すべき分は取得不足として数える。集計全体は止めない。D03 §15.2）。
    - 取得に失敗した月（取得不足 (b)）は取得の範囲に入れない。
    - データの開始は、要求したすべての区間（失敗した月を含む）の最初を UTC に直した時刻。
      表が覆わない日なら、表の変換の最大で直す（取得不足をデータの開始前に読み替えない向き）。
    """
    pair = series.pair
    cache: dict[int, int | None] = {}

    def offset(t: int) -> int | None:
        day = t // DAY
        if day not in cache:
            cache[day] = conversion.offset_or_none(pair, t)
        return cache[day]

    utc: list[int] = []
    uninterpretable = 0
    for t in series.times_api:
        hours = offset(t)
        if hours is None:
            uninterpretable += 1
            continue
        utc.append(t - hours * 3600)
    if any(b <= a for a, b in zip(utc, utc[1:], strict=False)):
        raise FeasibilityError(f"{pair}: {conversion.name} の変換で時刻の順が崩れる")
    acquired: list[tuple[int, int]] = []
    for window in series.windows:
        if not window.acquired:
            continue
        piece: int | None = None
        for day_start in range(window.lo_api, window.hi_api, DAY):
            covered = offset(day_start) is not None
            if covered and piece is None:
                piece = day_start
            if not covered and piece is not None:
                acquired.append(_to_utc_span(piece, day_start, offset))
                piece = None
        if piece is not None:
            acquired.append(_to_utc_span(piece, window.hi_api, offset))
    max_shift = conversion.max_offset_hours() * 3600
    starts = []
    for window in series.windows:
        hours = offset(window.lo_api)
        starts.append(window.lo_api - (hours * 3600 if hours is not None else max_shift))
    return utc, sorted(acquired), min(starts), uninterpretable


def _to_utc_span(lo: int, hi: int, offset: Any) -> tuple[int, int]:
    """API の区間 [lo, hi) を UTC に直す。終わりは区間の中の最後の時刻の変換で引く。"""
    return lo - int(offset(lo)) * 3600, hi - int(offset(hi - 1)) * 3600


def aggregate_pair_variant(
    series: PairSeries,
    conversion: TableConversion,
    expected: Sequence[int],
    intervals: Mapping[str, Sequence[tuple[int, int]]],
    timeframes_order: Sequence[str],
) -> PairVariant:
    utc, acquired, data_start, uninterpretable = convert_with_table(series, conversion)
    # 取得の範囲の最初（要求したすべての区間の最初。取得に失敗した月も含む）より前は
    # データの開始前（取得不足に数えない。D03 §15.2 の順 2 (a)・§15.3 の 5）
    usable = expected[bisect.bisect_left(expected, data_start) :]
    status = classify_minutes(series.pair, conversion.name, utc, acquired, usable, set())
    lo, hi = period_bounds()
    outside: Counter[str] = Counter(_week_sunday(t) for t in status.outside if lo <= t < hi)
    bars = {tf: bar_stats(status, tf, intervals[tf]) for tf in timeframes_order}
    return PairVariant(
        pair=series.pair,
        variant=conversion.name,
        status=status,
        utc_times=utc,
        bars=bars,
        outside_by_week=outside,
        data_start=data_start,
        uninterpretable_bars=uninterpretable,
    )


FINAL_DAILY_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "trading_day",
    "interval_start_utc",
    "interval_end_utc",
    "published_at_utc",
    "time_rule_row",
    "time_rule_mark",
    "expected_minutes",
    "recorded_minutes",
    "unrecorded_minutes",
    "shortfall_minutes",
    "coverage_ratio",
    "first_observed_utc",
    "last_observed_utc",
    "day_class",
)


def _ratio(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def classify_days(
    pv: PairVariant, calendar: TradingCalendar, conversion: TableConversion
) -> list[dict[str, Any]]:
    """§3-B「生成できる日足・部分観測の日足・全日無記録・取得不足の数」と被覆率（日足ごと）。"""
    rows: list[dict[str, Any]] = []
    for bar in pv.bars[DAILY]:
        first_minute = pv.status.expected[bisect.bisect_left(pv.status.expected, bar.start)]
        try:
            period: RulePeriod | None = conversion.row_of_utc(pv.pair, first_minute)
        except FeasibilityError:
            period = None  # 表が覆わない日（取得不足 (d)）
        rows.append(
            {
                "pair": pv.pair,
                "variant": pv.variant,
                "trading_day": trading_day_label(calendar, bar),
                "interval_start_utc": iso(bar.start),
                "interval_end_utc": iso(bar.end),
                "published_at_utc": iso(bar.end),
                "time_rule_row": period.row if period else "",
                "time_rule_mark": period.mark if period else "",
                "expected_minutes": bar.expected,
                "recorded_minutes": bar.recorded,
                "unrecorded_minutes": bar.unrecorded,
                "shortfall_minutes": bar.shortfall,
                "coverage_ratio": _ratio(bar.coverage),
                "first_observed_utc": iso(bar.first_observed) if bar.first_observed else "",
                "last_observed_utc": iso(bar.last_observed) if bar.last_observed else "",
                "day_class": bar.kind,
                "_start": bar.start,
                "_end": bar.end,
            }
        )
    return rows


SEGMENT_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "segment_id",
    "start_trading_day",
    "end_trading_day",
    "start_utc",
    "end_utc",
    "built_daily_bars",
    "full_daily_bars",
    "partial_daily_bars",
    "no_record_days",
    "closure_days_inside",
    "start_reason",
    "end_reason",
)
WARMUP_RESET_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "segment_id",
    "trading_day",
    "reason",
    "built_bars_before_reset",
    "was_ready_before",
    "not_ready_from_utc",
    "ready_again_at_utc",
    "not_ready_hours",
)
RESEARCH_WINDOW_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "segment_id",
    "warmup_daily_bars",
    "warmup_complete_published_at_utc",
    "research_start_utc",
    "research_end_utc",
    "research_daily_bars",
    "research_daily_bars_while_rewarming",
    "warmup_resets",
    "rewarming_hours",
    "note",
)


def continuous_segments(
    pv: PairVariant, labels_closed: Sequence[str], rules: CountingRules
) -> None:
    """§3-B「連続区間すべての開始・終了・日足本数、開始と終了の理由」と、準備・研究対象区間。

    区間は取得不足でだけ切る（D11 §12.1）。準備は日足で数え、部分観測は 1 本、休場は途切れず、
    予定取引日の全分無記録でやり直す（D03 §15.7）。準備完了は必要本数の最後の日足の公開時刻。
    """
    need = rules.warmup_daily_bars
    days = pv.days
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for day in days:
        if day["day_class"] == "shortfall":
            if current:
                groups.append(current)
            current = []
            continue
        current.append(day)
    if current:
        groups.append(current)
    closed = sorted(labels_closed)
    for n, group in enumerate(groups, start=1):
        first, last = group[0], group[-1]
        first_index = days.index(first)
        last_index = days.index(last)
        start_reason = "data_start" if first_index == 0 else "after_acquisition_shortfall"
        end_reason = "data_end" if last_index == len(days) - 1 else "acquisition_shortfall"
        kinds = Counter(d["day_class"] for d in group)
        inside = sum(1 for c in closed if first["trading_day"] < c < last["trading_day"])
        pv.segments.append(
            {
                "pair": pv.pair,
                "variant": pv.variant,
                "segment_id": n,
                "start_trading_day": first["trading_day"],
                "end_trading_day": last["trading_day"],
                "start_utc": first["interval_start_utc"],
                "end_utc": last["interval_end_utc"],
                "built_daily_bars": kinds["full"] + kinds["partial"],
                "full_daily_bars": kinds["full"],
                "partial_daily_bars": kinds["partial"],
                "no_record_days": kinds["no_record"],
                "closure_days_inside": inside,
                "start_reason": start_reason,
                "end_reason": end_reason,
            }
        )
        _warmup(pv, n, group, need)


def _warmup(pv: PairVariant, segment_id: int, group: Sequence[dict[str, Any]], need: int) -> None:
    count = 0
    first_ready: int | None = None
    open_reset: dict[str, Any] | None = None
    seg_end = int(group[-1]["_end"])
    not_ready: list[tuple[int, int]] = []
    for day in group:
        if day["day_class"] == "no_record":
            reset = {
                "pair": pv.pair,
                "variant": pv.variant,
                "segment_id": segment_id,
                "trading_day": day["trading_day"],
                "reason": "all_no_record_scheduled_day",
                "built_bars_before_reset": count,
                "was_ready_before": count >= need,
                "not_ready_from_utc": day["interval_end_utc"],
                "ready_again_at_utc": "",
                "not_ready_hours": "",
                "_from": int(day["_end"]),
            }
            if open_reset is not None:  # 準備が整う前に重ねてやり直し（前のやり直しは整わずに続く）
                hours = (int(day["_end"]) - open_reset["_from"]) / 3600
                open_reset["not_ready_hours"] = f"{hours:.2f}"
                not_ready.append((open_reset["_from"], int(day["_end"])))
            pv.resets.append(reset)
            open_reset = reset
            count = 0
            continue
        count += 1
        if count == need:
            if first_ready is None:
                first_ready = int(day["_end"])
            if open_reset is not None:
                open_reset["ready_again_at_utc"] = day["interval_end_utc"]
                hours = (int(day["_end"]) - open_reset["_from"]) / 3600
                open_reset["not_ready_hours"] = f"{hours:.2f}"
                not_ready.append((open_reset["_from"], int(day["_end"])))
                open_reset = None
    if open_reset is not None:
        hours = (seg_end - open_reset["_from"]) / 3600
        open_reset["not_ready_hours"] = f"{hours:.2f}"
        not_ready.append((open_reset["_from"], seg_end))
    resets_here = [
        r for r in pv.resets if r["segment_id"] == segment_id and r["variant"] == pv.variant
    ]
    if first_ready is None:
        pv.windows.append(
            {
                "pair": pv.pair,
                "variant": pv.variant,
                "segment_id": segment_id,
                "warmup_daily_bars": need,
                "warmup_complete_published_at_utc": "",
                "research_start_utc": "",
                "research_end_utc": "",
                "research_daily_bars": 0,
                "research_daily_bars_while_rewarming": 0,
                "warmup_resets": len(resets_here),
                "rewarming_hours": "",
                "note": "必要本数に届かない",
            }
        )
        return
    built_after = [
        d
        for d in group
        if d["day_class"] in ("full", "partial") and int(d["_start"]) >= first_ready
    ]
    while_rewarming = [
        d for d in built_after if any(lo <= int(d["_start"]) < hi for lo, hi in not_ready)
    ]
    rewarming_hours = sum(hi - lo for lo, hi in not_ready if lo >= first_ready) / 3600
    pv.windows.append(
        {
            "pair": pv.pair,
            "variant": pv.variant,
            "segment_id": segment_id,
            "warmup_daily_bars": need,
            "warmup_complete_published_at_utc": iso(first_ready),
            "research_start_utc": iso(first_ready),
            "research_end_utc": iso(seg_end),
            "research_daily_bars": len(built_after),
            "research_daily_bars_while_rewarming": len(while_rewarming),
            "warmup_resets": len(resets_here),
            "rewarming_hours": f"{rewarming_hours:.2f}",
            "note": "",
        }
    )


# --- 足の件数・被覆率の分布・無記録の足 ------------------------------------------------------

BAR_COUNT_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "timeframe",
    "scheduled_bars",
    "full_bars",
    "partial_bars",
    "no_record_bars",
    "shortfall_bars",
    "built_bars",
    "min_coverage_built",
    "unrecorded_minutes",
    "time_not_interpretable_bars",
)
#: 被覆率の分布の刻み（表示のためで閾値ではない。D12-87）。下端を含み上端を含まない。
COVERAGE_BINS: tuple[tuple[str, float, float], ...] = (
    ("(0,0.50)", 0.0, 0.50),
    ("[0.50,0.90)", 0.50, 0.90),
    ("[0.90,0.95)", 0.90, 0.95),
    ("[0.95,0.99)", 0.95, 0.99),
    ("[0.99,1)", 0.99, 1.0),
)
COVERAGE_DIST_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "timeframe",
    "no_record_0",
    *(f"coverage_{label}" for label, _, _ in COVERAGE_BINS),
    "full_1",
    "shortfall_bars",
)
NO_RECORD_BAR_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "timeframe",
    "interval_start_utc",
    "interval_end_utc",
    "expected_minutes",
)
OUT_OF_CALENDAR_COLUMNS: tuple[str, ...] = ("pair", "variant", "kind", "week_sunday_ny", "bars")


def bar_count_rows(pv: PairVariant) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tf, bars in pv.bars.items():
        kinds = Counter(b.kind for b in bars)
        built = [b.coverage for b in bars if b.built and b.coverage is not None]
        rows.append(
            {
                "pair": pv.pair,
                "variant": pv.variant,
                "timeframe": tf,
                "scheduled_bars": len(bars),
                "full_bars": kinds["full"],
                "partial_bars": kinds["partial"],
                "no_record_bars": kinds["no_record"],
                "shortfall_bars": kinds["shortfall"],
                "built_bars": kinds["full"] + kinds["partial"],
                "min_coverage_built": _ratio(min(built) if built else None),
                "unrecorded_minutes": sum(b.unrecorded for b in bars if not b.shortfall),
                "time_not_interpretable_bars": pv.uninterpretable_bars,
            }
        )
    return rows


def coverage_distribution(pv: PairVariant) -> list[dict[str, Any]]:
    """§3-B「被覆率の分布」（時間足ごと）。刻みは表示のためで閾値ではない。"""
    rows: list[dict[str, Any]] = []
    for tf, bars in pv.bars.items():
        c: Counter[str] = Counter()
        for b in bars:
            if b.kind == "shortfall":
                c["shortfall_bars"] += 1
            elif b.kind == "no_record":
                c["no_record_0"] += 1
            elif b.kind == "full":
                c["full_1"] += 1
            else:
                ratio = b.coverage or 0.0
                for label, low, high in COVERAGE_BINS:
                    if low <= ratio < high:
                        c[f"coverage_{label}"] += 1
                        break
        row: dict[str, Any] = {"pair": pv.pair, "variant": pv.variant, "timeframe": tf}
        for col in COVERAGE_DIST_COLUMNS[3:]:
            row[col] = c[col]
        rows.append(row)
    return rows


def no_record_bar_rows(pv: PairVariant) -> list[dict[str, Any]]:
    return [
        {
            "pair": pv.pair,
            "variant": pv.variant,
            "timeframe": tf,
            "interval_start_utc": iso(b.start),
            "interval_end_utc": iso(b.end),
            "expected_minutes": b.expected,
        }
        for tf, bars in pv.bars.items()
        for b in bars
        if b.kind == "no_record"
    ]


def out_of_calendar_rows(pv: PairVariant) -> list[dict[str, Any]]:
    kind = "UNEXPECTED_BAR" if pv.variant == STANDARD else "CANDIDATE_OUT_OF_CALENDAR"
    return [
        {"pair": pv.pair, "variant": pv.variant, "kind": kind, "week_sunday_ny": w, "bars": n}
        for w, n in sorted(pv.outside_by_week.items())
    ]


# --- 候補の間の差と、8 通貨の共通区間 ------------------------------------------------------

CANDIDATE_DIFF_COLUMNS: tuple[str, ...] = (
    "pair",
    "scope",
    "key",
    "field",
    "standard_value",
    "candidate_value",
)


#: 候補の間で比べる日足の属性（区分・分数・被覆率と観測の時刻。
#: 観測の時刻だけが違う日足も漏らさない）
DAILY_DIFF_FIELDS: tuple[str, ...] = (
    "day_class",
    "expected_minutes",
    "recorded_minutes",
    "unrecorded_minutes",
    "shortfall_minutes",
    "coverage_ratio",
    "first_observed_utc",
    "last_observed_utc",
)


def candidate_differences(standard: PairVariant, candidate: PairVariant) -> list[dict[str, Any]]:
    """§3-B「時刻の規則の候補間で結果が変わる区間と差」。"""
    rows: list[dict[str, Any]] = []

    def add(scope: str, key: str, name: str, a: Any, b: Any) -> None:
        if a != b:
            rows.append(
                {
                    "pair": standard.pair,
                    "scope": scope,
                    "key": key,
                    "field": name,
                    "standard_value": a,
                    "candidate_value": b,
                }
            )

    cand_days = {d["trading_day"]: d for d in candidate.days}
    for day in standard.days:
        other = cand_days.get(day["trading_day"], {})
        for name in DAILY_DIFF_FIELDS:
            add("daily_bar", day["trading_day"], name, day[name], other.get(name, ""))
    for a_row, b_row in zip(bar_count_rows(standard), bar_count_rows(candidate), strict=True):
        for name in (
            "full_bars",
            "partial_bars",
            "no_record_bars",
            "shortfall_bars",
            "unrecorded_minutes",
        ):
            add("bar_counts", a_row["timeframe"], name, a_row[name], b_row[name])
    for a_row, b_row in zip(standard.windows, candidate.windows, strict=False):
        for name in (
            "warmup_complete_published_at_utc",
            "research_end_utc",
            "research_daily_bars",
            "warmup_resets",
        ):
            add("research_window", str(a_row["segment_id"]), name, a_row[name], b_row[name])
    if len(standard.segments) != len(candidate.segments):
        add("segments", "count", "segments", len(standard.segments), len(candidate.segments))
    add(
        "out_of_calendar",
        "total",
        "bars",
        sum(standard.outside_by_week.values()),
        sum(candidate.outside_by_week.values()),
    )
    return rows


COMMON_WINDOW_COLUMNS: tuple[str, ...] = (
    "variant",
    "start_utc",
    "end_utc",
    "trading_days",
    "trading_days_built_in_all_pairs",
    "trading_days_not_built_in_some_pair",
    "warmup_reset_days",
    "pairs",
)


def _iso_epoch(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC).timestamp())


def common_windows(per_pair: Sequence[PairVariant]) -> list[dict[str, Any]]:
    """§3-B「8 通貨共通の利用可能区間（参考）」: 研究対象区間の共通部分。"""
    if not per_pair:
        return []
    spans: list[tuple[int, int]] | None = None
    for pv in per_pair:
        mine = [
            (_iso_epoch(w["research_start_utc"]), _iso_epoch(w["research_end_utc"]))
            for w in pv.windows
            if w["research_start_utc"]
        ]
        if spans is None:
            spans = mine
            continue
        spans = [
            (max(a0, b0), min(a1, b1))
            for a0, a1 in spans
            for b0, b1 in mine
            if max(a0, b0) < min(a1, b1)
        ]
    rows: list[dict[str, Any]] = []
    built = [{d["trading_day"]: d["day_class"] for d in pv.days} for pv in per_pair]
    reset_days = {r["trading_day"] for pv in per_pair for r in pv.resets}
    for lo, hi in spans or []:
        days = [d for d in per_pair[0].days if lo <= int(d["_start"]) and int(d["_end"]) <= hi]
        labels = [d["trading_day"] for d in days]
        all_built = sum(
            1 for label in labels if all(b.get(label) in ("full", "partial") for b in built)
        )
        rows.append(
            {
                "variant": per_pair[0].variant,
                "start_utc": iso(lo),
                "end_utc": iso(hi),
                "trading_days": len(labels),
                "trading_days_built_in_all_pairs": all_built,
                "trading_days_not_built_in_some_pair": len(labels) - all_built,
                "warmup_reset_days": "・".join(sorted(r for r in reset_days if r in labels)),
                "pairs": "・".join(pv.pair for pv in per_pair),
            }
        )
    return rows


# --- 実行 -------------------------------------------------------------------------


@dataclass(slots=True)
class FinalResult:
    coverage: list[dict[str, Any]] = field(default_factory=list)
    per_pair: dict[str, dict[str, PairVariant]] = field(default_factory=dict)
    common: list[dict[str, Any]] = field(default_factory=list)
    differences: list[dict[str, Any]] = field(default_factory=list)


def run_final(
    root: Path,
    pairs: Sequence[str],
    rules: CountingRules,
    conversions: Sequence[TableConversion],
    calendar: TradingCalendar,
    timeframes: Mapping[str, TimeframeDefinition],
) -> FinalResult:
    problems = verify_full_dir(root, pairs)
    if problems:
        raise FeasibilityError(f"manifest との照合で問題 {len(problems)} 件（破損は受入れ失敗）")
    manifest = json.loads((root / FULL_MANIFEST).read_text(encoding="utf-8"))
    lo, hi = period_bounds()
    expected = expected_minutes(calendar, lo, hi)
    intervals = {tf: bar_intervals(calendar, timeframes, tf) for tf in rules.timeframes}
    daily_labels = {
        trading_day_label(calendar, BarStat(DAILY, s, e, 0, 0, 0, None, None))
        for s, e in intervals[DAILY]
    }
    closed = closure_days(daily_labels)
    result = FinalResult()
    by_name = {c.name: c for c in conversions}
    for pair in pairs:
        series = read_pair(root, manifest, pair)
        corrupt = [w for w in series.shortfall_windows if not w.reason.startswith("状態")]
        if series.duplicates or series.unaligned or series.unparseable or corrupt:
            raise FeasibilityError(f"{pair}: 重複・分に揃わない時刻・破損があり受入れは失敗する")
        result.coverage.append(coverage_row(series))
        result.per_pair[pair] = {}
        for name in rules.variants:
            conversion = by_name[name]
            pv = aggregate_pair_variant(series, conversion, expected, intervals, rules.timeframes)
            pv.days = classify_days(pv, calendar, conversion)
            continuous_segments(pv, closed, rules)
            result.per_pair[pair][name] = pv
        result.differences.extend(
            candidate_differences(result.per_pair[pair][STANDARD], result.per_pair[pair][CANDIDATE])
        )
    for name in rules.variants:
        result.common.extend(common_windows([result.per_pair[p][name] for p in pairs]))
    return result


def _all(result: FinalResult) -> Iterable[PairVariant]:
    for variants in result.per_pair.values():
        yield from variants.values()


def write_final(result: FinalResult, out_dir: Path) -> dict[str, int]:
    pvs = list(_all(result))
    tables: list[tuple[str, Sequence[str], Iterable[Mapping[str, Any]]]] = [
        ("coverage_by_pair.csv", COVERAGE_COLUMNS, result.coverage),
        ("bar_counts.csv", BAR_COUNT_COLUMNS, [r for pv in pvs for r in bar_count_rows(pv)]),
        ("daily_bars.csv", FINAL_DAILY_COLUMNS, [d for pv in pvs for d in pv.days]),
        (
            "coverage_distribution.csv",
            COVERAGE_DIST_COLUMNS,
            [r for pv in pvs for r in coverage_distribution(pv)],
        ),
        (
            "no_record_run_distribution.csv",
            RUN_DIST_COLUMNS,
            [r for pv in pvs for r in run_distribution_rows(pv.status)],
        ),
        (
            "long_no_record_runs.csv",
            LONG_RUN_COLUMNS,
            [r for pv in pvs for r in long_run_rows(pv.status)],
        ),
        (
            "monthly_stats.csv",
            MONTHLY_COLUMNS,
            [r for pv in pvs for r in monthly_rows(pv.status, pv.utc_times)],
        ),
        (
            "no_record_bars.csv",
            NO_RECORD_BAR_COLUMNS,
            [r for pv in pvs for r in no_record_bar_rows(pv)],
        ),
        (
            "out_of_calendar_bars.csv",
            OUT_OF_CALENDAR_COLUMNS,
            [r for pv in pvs for r in out_of_calendar_rows(pv)],
        ),
        ("segments.csv", SEGMENT_COLUMNS, [r for pv in pvs for r in pv.segments]),
        ("warmup_resets.csv", WARMUP_RESET_COLUMNS, [r for pv in pvs for r in pv.resets]),
        ("research_windows.csv", RESEARCH_WINDOW_COLUMNS, [r for pv in pvs for r in pv.windows]),
        ("candidate_differences.csv", CANDIDATE_DIFF_COLUMNS, result.differences),
        ("common_windows.csv", COMMON_WINDOW_COLUMNS, result.common),
    ]
    return {name: _write_csv(out_dir / name, cols, rows) for name, cols, rows in tables}


# --- 人間向けの要約（出力と同じ値から作る。価格は含まない） ------------------------------------

OUTPUT_DESCRIPTIONS: dict[str, str] = {
    "coverage_by_pair.csv": "取得範囲（通貨ごとの最初と最後の足・本数・時刻の問題の数）",
    "bar_counts.csv": "通貨 × 組 × 時間足の足の数（全分観測・部分観測・無記録・取得不足）",
    "daily_bars.csv": "日足ごとの区分・被覆率・観測の時刻・公開時刻・時刻の規則の表の行",
    "coverage_distribution.csv": "通貨 × 組 × 時間足の被覆率の分布（刻みは表示用で閾値ではない）",
    "no_record_run_distribution.csv": "連続無記録時間の長さの分布（年ごと）",
    "long_no_record_runs.csv": "61 分以上の連続無記録の一覧",
    "monthly_stats.csv": "通貨 × 組 × 年 × 月の本数・存在すべき分・無記録・取得不足",
    "no_record_bars.csv": "無記録の足（記録 0 分で作らない足）の一覧（全時間足）",
    "out_of_calendar_bars.csv": "カレンダーの外に出た 1 分足の週ごとの件数（足の生成に使わない）",
    "segments.csv": "連続区間すべての開始・終了・日足本数・開始と終了の理由",
    "warmup_resets.csv": "準備のやり直しの箇所・理由・準備が再び整う公開時刻",
    "research_windows.csv": "準備完了後の研究対象区間と日足本数",
    "candidate_differences.csv": "標準と候補で結果が変わる日足・件数・区間",
    "common_windows.csv": "8 通貨共通の研究対象区間（参考）",
}


def _n(value: int) -> str:
    return f"{value:,}"


def render_final_summary(result: FinalResult, record: Mapping[str, Any]) -> str:
    """最終集計の要約（Markdown）。数はすべて出力の CSV と同じ値から作る。"""
    inputs = record["inputs"]
    decision = record.get("decisions")
    status_lines = (
        [
            f"- **標準の組（`standard`）: 確定**（休場帯の足を人間が分類。{decision['ref']}、"
            f"原文 `{decision['file']}`（SHA-256 `{decision['sha256']}`））。"
            "**仮置き: 0 件**（集計の読み方 6 件は同じ決定で確定）。",
        ]
        if decision
        else ["- 標準の組（`standard`）: 休場帯の足の人間の分類待ち（下の 4 節）。"]
    )
    lines: list[str] = [
        "# 全期間のデータ上の実行可能性 — 最終集計（8 通貨 × 2017〜2023）",
        "",
        "人間の指示（2026-10-17）の「B. 全期間のデータ上の実行可能性の検証」の結果。"
        "数え方は設計 PR #84 で確定した規則ファイル（数え方の規則 版 1・時刻の規則の表 版 1・"
        "カレンダー `fx_ny17_oanda` 版 1）から読んだ。",
        "",
        "- **これは「データ上の実行可能性」の集計で、エンジンの正常動作や実際の市場での約定の"
        "再現性は確かめていない。**",
        "- 価格は読んでいない・載せていない（件数・時刻・被覆率・ハッシュだけ）。"
        "戦略の成績は読んでいない。2024 年以降は参照していない。原データは変えていない。",
        "- 部分観測の日足（被覆率 1 未満）は「完全」と呼ばない。必要本数"
        f" {result_need(result)} は現在の戦略（検証戦略 B の日足の EMA）の必要履歴で、"
        "基盤全体の品質基準ではない。",
        "- 組 `standard` は時刻の規則の表の標準仮説、`candidate` は候補を持つ行をすべて候補の変換に"
        "替えた組（感度の検証。D12-92 (a)）。",
        *status_lines,
        "",
        "## 1. 通貨ごとの研究対象区間（使える範囲）と使えない範囲",
        "",
        "研究対象区間は、準備に必要な日足の最後の 1 本の公開時刻から、連続区間の終わりまで"
        "（準備の期間を含めない）。",
        "",
        "| 通貨 | 組 | 連続区間（番号/数） | 研究対象区間（UTC） | 研究対象の日足 | "
        "うち準備のやり直し中 | やり直し | 準備の期間の日足 | 無記録の日（区間の中） | "
        "取得不足の日（通貨全体） |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for pair, variants in result.per_pair.items():
        for name, pv in variants.items():
            shortfall_days = sum(1 for d in pv.days if d["day_class"] == "shortfall")
            segments = {s["segment_id"]: s for s in pv.segments}
            for w in pv.windows:
                seg = segments[w["segment_id"]]
                warm = int(seg["built_daily_bars"]) - int(w["research_daily_bars"])
                lines.append(
                    f"| {pair} | {name} | {w['segment_id']}/{len(pv.segments)} | "
                    f"{w['research_start_utc'] or '—'} 〜 {w['research_end_utc'] or '—'} | "
                    f"{_n(int(w['research_daily_bars']))} | "
                    f"{w['research_daily_bars_while_rewarming']} | "
                    f"{w['warmup_resets']} | {_n(warm)} | {seg['no_record_days']} | "
                    f"{shortfall_days} |"
                )
    resets = sorted({r["trading_day"] for pv in _all(result) for r in pv.resets})
    lines += [
        "",
        f"- 準備のやり直しが起きた取引日（全通貨・全組）: {'、'.join(resets) or 'なし'}。"
        "理由はすべて「予定取引日の全分無記録」。",
        "- 区間を切る取得不足は無く、各通貨・各組の連続区間は 1 つ（開始の理由 `data_start`、"
        "終了の理由 `data_end`）。"
        if all(len(pv.segments) == 1 for pv in _all(result))
        else "- 連続区間が複数ある通貨がある（`segments.csv`）。",
        "",
        "## 2. 日足の区分（`daily_bars.csv`・`bar_counts.csv`）",
        "",
        "| 通貨 | 組 | 予定の日足 | 全分観測 | 部分観測 | 無記録（作らない） | 取得不足 | "
        "被覆率の最小（作った足） |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for pv in _all(result):
        row = next(r for r in bar_count_rows(pv) if r["timeframe"] == DAILY)
        lines.append(
            f"| {pv.pair} | {pv.variant} | {_n(row['scheduled_bars'])} | {_n(row['full_bars'])} | "
            f"{_n(row['partial_bars'])} | {row['no_record_bars']} | {row['shortfall_bars']} | "
            f"{row['min_coverage_built']} |"
        )
    lines += [
        "",
        "日中の時間足の無記録の足（作らない。指標の準備は日足と同じ規則。D12-90 (a)）の数"
        "（`standard` の組）:",
        "",
        "| 通貨 | 15 分足 | 1 時間足 | 4 時間足 |",
        "|---|---|---|---|",
    ]
    for pair, variants in result.per_pair.items():
        counts = {r["timeframe"]: r for r in bar_count_rows(variants[STANDARD])}
        lines.append(
            f"| {pair} | {counts['15m']['no_record_bars']} | {counts['1h']['no_record_bars']} | "
            f"{counts['4h_ny17']['no_record_bars']} |"
        )
    lines += [
        "",
        "## 3. 被覆率と連続無記録時間の分布",
        "",
        "日足の被覆率の分布（`standard`。刻みは表示用で閾値ではない。全時間足は "
        "`coverage_distribution.csv`）:",
        "",
        "| 通貨 | " + " | ".join(COVERAGE_DIST_COLUMNS[3:]) + " |",
        "|---" * (len(COVERAGE_DIST_COLUMNS) - 2) + "|",
    ]
    for pair, variants in result.per_pair.items():
        row = next(r for r in coverage_distribution(variants[STANDARD]) if r["timeframe"] == DAILY)
        lines.append(
            f"| {pair} | " + " | ".join(_n(row[c]) for c in COVERAGE_DIST_COLUMNS[3:]) + " |"
        )
    lines += [
        "",
        "連続無記録時間の長さ（全年の合計の件数。年ごとは `no_record_run_distribution.csv`）:",
        "",
        "| 通貨 | 組 | "
        + " | ".join(f"{label} 分" for label, _, _ in RUN_BINS)
        + " | 無記録の分 |",
        "|---" * (len(RUN_BINS) + 3) + "|",
    ]
    for pv in _all(result):
        dist = run_distribution_rows(pv.status)
        cells = [sum(int(r[f"runs_{label}"]) for r in dist) for label, _, _ in RUN_BINS]
        total = sum(int(r["no_record_minutes"]) for r in dist)
        lines.append(
            f"| {pv.pair} | {pv.variant} | "
            + " | ".join(_n(c) for c in cells)
            + f" | {_n(total)} |"
        )
    lines += [
        "",
        "## 4. 時刻の規則の候補の間の差（`candidate_differences.csv`）",
        "",
        "| 通貨 | 値が変わる日足 | 研究対象区間の差 | カレンダーの外の足（standard／candidate） |",
        "|---|---|---|---|",
    ]
    for pair, variants in result.per_pair.items():
        diffs = [d for d in result.differences if d["pair"] == pair]
        days = {d["key"] for d in diffs if d["scope"] == "daily_bar"}
        window = [f"{d['field']}" for d in diffs if d["scope"] == "research_window"]
        lines.append(
            f"| {pair} | {len(days)} | {'・'.join(window) or 'なし'} | "
            f"{sum(variants[STANDARD].outside_by_week.values())}／"
            f"{sum(variants[CANDIDATE].outside_by_week.values())} |"
        )
    lines += [
        "",
        "- `standard` でカレンダーの外に出た足は休場帯の足（`UNEXPECTED_BAR`）で、人間の分類の対象"
        "（D12-94 (a)）。`candidate` で外に出た足は足の生成に使わず件数だけを報告する"
        "（D12-97 (a)）。"
        "週ごとの件数は `out_of_calendar_bars.csv`。",
        "",
        (
            f"**分類済みの足**（`standard` の休場帯の足。{decision['ref']} で人間が「セッション外の"
            "データ異常」と分類し、使わずに `standard` の組を確定した。表の次の版の検討材料として"
            "D12 §11.7.10 に記録）:"
            if decision
            else "**人間の分類が要る足**（`standard` の休場帯の足。D03 §15.3 の 4・D12-94 (a)。"
            "分類するまで `standard` の組の snapshot は確定しない。この集計ではその足を"
            "足の生成に使っていない）:"
        ),
        "",
        "| 週（NY の日曜） | 足の数（8 通貨の計） | 通貨ごと |",
        "|---|---|---|",
        *_unexpected_rows(result),
        "",
        "## 5. 8 通貨共通の研究対象区間（参考。`common_windows.csv`）",
        "",
        "| 組 | 開始（UTC） | 終わり（UTC） | 取引日 | 全通貨で日足あり | どれかの通貨で日足なし | "
        "準備のやり直しの日 |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in result.common:
        lines.append(
            f"| {c['variant']} | {c['start_utc']} | {c['end_utc']} | {_n(c['trading_days'])} | "
            f"{_n(c['trading_days_built_in_all_pairs'])} | "
            f"{c['trading_days_not_built_in_some_pair']} | {c['warmup_reset_days'] or 'なし'} |"
        )
    lines += [
        "",
        "## 6. 取得範囲と入力（`coverage_by_pair.csv`・`manifest.json`）",
        "",
        "| 通貨 | 足の本数 | 最初の足（API の時刻） | 最後の足（API の時刻） |",
        "|---|---|---|---|",
        *(
            f"| {r['pair']} | {_n(int(r['bars_used']))} | {r['first_bar_api']} | "
            f"{r['last_bar_api']} |"
            for r in result.coverage
        ),
        "",
        "| 入力 | ファイル | SHA-256 |",
        "|---|---|---|",
        f"| 取得記録 manifest | `{inputs['fetch_manifest']['path']}` | "
        f"`{inputs['fetch_manifest']['sha256']}` |",
        f"| 取得の記録 | `fetch_log.jsonl` | `{inputs['fetch_log_sha256']}` |",
        f"| 数え方の規則 版 {inputs['counting_rules']['version']} | "
        f"`{inputs['counting_rules']['path']}` | `{inputs['counting_rules']['sha256']}` |",
        f"| 時刻の規則の表 | `{inputs['time_rule_table']['path']}` | "
        f"`{inputs['time_rule_table']['sha256']}` |",
        f"| 承認済みの表の一覧 | `{inputs['approved_time_rule_tables']['path']}` | "
        f"`{inputs['approved_time_rule_tables']['sha256']}` |",
        f"| カレンダー {inputs['calendar']['id']} 版 {inputs['calendar']['version']} | "
        f"`{inputs['calendar']['path']}` | `{inputs['calendar']['sha256']}` |",
        f"| 時間足の定義 | `{inputs['timeframes']['path']}` | `{inputs['timeframes']['sha256']}` |",
        "",
        "## 7. 再現方法",
        "",
        "```",
        str(record["command"]),
        "```",
        "",
        f"- 原データの置き場（リポジトリの根から）: `{record['raw_root']}`（変更しない）",
        f"- 対象のコミット: `{record['target_commit']}`",
        f"- 道具の SHA-256: `{record['tool_sha256']}`（共通部分 `full_period_feasibility.py`:"
        f" `{record['shared_tool_sha256']}`）",
        f"- 実行時刻（UTC）: {record['started_at_utc']} 〜 {record['finished_at_utc']}",
        "- 入力・出力の一覧とハッシュは `manifest.json`。",
        "",
        "## 8. ファイル",
        "",
        "| ファイル | 中身 | 行数 |",
        "|---|---|---|",
        *(
            f"| `{name}` | {OUTPUT_DESCRIPTIONS.get(name, '')} | {_n(int(n))} |"
            for name, n in record["outputs"].items()
        ),
        "| `manifest.json` | 入力のハッシュ・コマンド・実行時刻・出力の行数 | — |",
        "| `prelim_2026-10-17/` | 規則の確定前の先行統計"
        "（記録として残す。最終の判断には使わない） | — |",
        "",
    ]
    return "\n".join(lines)


def _unexpected_rows(result: FinalResult) -> list[str]:
    weeks: dict[str, dict[str, int]] = {}
    for pair, variants in result.per_pair.items():
        for week, n in variants[STANDARD].outside_by_week.items():
            weeks.setdefault(week, {})[pair] = n
    if not weeks:
        return ["| なし | 0 | — |"]
    return [
        f"| {week} | {sum(per.values())} | "
        + "、".join(f"{p} {n}" for p, n in sorted(per.items()))
        + " |"
        for week, per in sorted(weeks.items())
    ]


def result_need(result: FinalResult) -> str:
    needs = {w["warmup_daily_bars"] for pv in _all(result) for w in pv.windows}
    return "・".join(str(n) for n in sorted(needs)) + " 本" if needs else "—"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="全期間のデータ上の実行可能性の最終集計")
    parser.add_argument("--root", required=True, help="原データの置き場（manifest.json のある所）")
    parser.add_argument("--pairs", default="", help="通貨（カンマ区切り。既定は 8 通貨）")
    parser.add_argument("--counting-rules", default=str(COUNTING_RULES_DEFAULT))
    parser.add_argument("--docs-manifest", default=str(DOCS_MANIFEST_DEFAULT))
    parser.add_argument("--timeframes", default=str(TIMEFRAMES_DEFAULT))
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    parser.add_argument(
        "--decision-file",
        default="",
        help="休場帯の足の分類などの人間の決定の原文のファイル（SHA-256 を記録する）",
    )
    parser.add_argument(
        "--decision-ref", default="", help="決定の記録の場所（例: D12 §0.1.29、D12-99・D12-100）"
    )
    return parser


COMMAND_PREFIX: tuple[str, ...] = ("uv", "run", "python", "-m", "tools.survey.full_period_final")


def recorded_command(argv: Sequence[str]) -> str:
    """再現のコマンド。引数の境界を保つため shlex で引用する（空白を含む引数を割らない）。

    原データの置き場は利用者の家のディレクトリを書かず、リポジトリの根からの形にする。
    """
    return shlex.join([*COMMAND_PREFIX, *(root_label(x) for x in argv)])


def decision_record(file_arg: str, ref: str) -> dict[str, str] | None:
    """人間の決定の参照（原文のファイル名と SHA-256、設計書の節）。無ければ None。"""
    if not file_arg and not ref:
        return None
    if not file_arg or not ref:
        raise FeasibilityError("--decision-file と --decision-ref は両方を渡す")
    path = Path(file_arg)
    return {"ref": ref, "file": path.as_posix(), "sha256": sha256_file(path)}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = datetime.now(UTC)
    try:
        root = Path(args.root)
        raw_sha = sha256_file(root / FULL_MANIFEST)
        if raw_sha != sha256_file(Path(args.docs_manifest)):
            raise FeasibilityError(
                "manifest の版管理の写しと原データの置き場の manifest が一致しない"
            )
        rules = load_counting_rules(Path(args.counting_rules))
        verify_warmup_source(rules)
        conversions = load_time_rule_table(rules.time_rule_table_file, rules.approved_list_file)
        calendar = load_calendar(rules.calendar_file)
        timeframes = load_timeframes(Path(args.timeframes))
        pairs = [p for p in args.pairs.split(",") if p] or list(FULL_PAIRS)
        decision = decision_record(args.decision_file, args.decision_ref)
        result = run_final(root, pairs, rules, conversions, calendar, timeframes)
    except FeasibilityError as exc:
        print(f"誤り: {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    counts = write_final(result, out_dir)
    finished = datetime.now(UTC)
    record = {
        "tool": "tools/survey/full_period_final.py",
        "tool_version": TOOL_VERSION,
        "tool_sha256": sha256_file(Path(__file__)),
        "shared_tool_sha256": sha256_file(Path(__file__).with_name("full_period_feasibility.py")),
        "stage": (
            "final（§3-B の 9 項目。データ上の実行可能性。"
            "エンジンの動作・約定の再現性は確かめていない）"
        ),
        "target_commit": _git_head(),
        "command": recorded_command(sys.argv[1:]),
        "raw_root": root_label(str(root)),
        "decisions": decision,
        "started_at_utc": started.isoformat(timespec="seconds"),
        "finished_at_utc": finished.isoformat(timespec="seconds"),
        "inputs": {
            "fetch_manifest": {"path": DOCS_MANIFEST_DEFAULT.as_posix(), "sha256": raw_sha},
            "fetch_log_sha256": sha256_file(root / FULL_FETCH_LOG),
            "counting_rules": {
                "path": rules.path,
                "id": rules.rule_id,
                "version": rules.version,
                "sha256": rules.sha256,
            },
            "time_rule_table": {
                "path": rules.time_rule_table_file.as_posix(),
                "sha256": sha256_file(rules.time_rule_table_file),
            },
            "approved_time_rule_tables": {
                "path": rules.approved_list_file.as_posix(),
                "sha256": sha256_file(rules.approved_list_file),
            },
            "calendar": {
                "path": rules.calendar_file.as_posix(),
                "id": rules.calendar_id,
                "version": rules.calendar_version,
                "sha256": rules.calendar_sha256,
            },
            "timeframes": {"path": args.timeframes, "sha256": sha256_file(Path(args.timeframes))},
            "warmup_source": rules.warmup_daily_bars_source,
        },
        "pairs": list(result.per_pair),
        "variants": list(rules.variants),
        "period_utc": [PERIOD_START.isoformat(), PERIOD_END.isoformat()],
        "outputs": counts,
        "prices": "読んでいない・出力していない（CSV の 1 列目の時刻だけ）",
        "strategy_results": "読んでいない",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(render_final_summary(result, record), encoding="utf-8")
    print(json.dumps({"outputs": counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
