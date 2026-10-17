"""全期間のデータ上の実行可能性の検証（人間の指示 2026-10-17 の B）の先行作業の道具。

対象: 取得済みの OANDA MT5 API の 1 分足（8 通貨 × 2017-01〜2023-12。D12 付録 K-17、
``docs/data/oanda_mt5_full_fetch_2026-10-16/manifest.json``）。Mac の上だけ。読むだけ。

**この道具が今出すもの（数え方の設計 PR-A に依存しない先行統計）**:

1. 入力の確認: 672 ファイルを manifest（gzip と展開後の SHA-256・行数・時刻の区間）と照合
   （``mt5_api_probe.verify_full_dir`` を再利用）。取得範囲（通貨 × 最初と最後の足・本数）。
2. 時刻の変換の準備: API の生の時刻を保持したまま、候補の時計（欧州式・米国式。時刻の規則の表
   版 1 の行はどれもこの 2 つのどちらか。D12 §11.7.10）ごとに UTC へ直す。
3. 「足が存在すべき分」: 承認済みの取引カレンダー ``fx_ny17`` 版 2 のセッションの分。
   年末年始の休場の定義ファイル（付録 K-14）の分は別に数える（外した分・その中の無記録）。
4. 無記録（取得済みの系列に足が無い分）と取得不足（要求の失敗・未取得・破損・時刻の解釈不能・
   取得した区間の外）を分けて数える。取得不足は manifest と読み込みの結果から決める。
5. 通貨 × 時計 × 年 × 月の本数・無記録の分数・連続した無記録の長さの分布・取得不足、
   候補の時計で変換が変わる区間（米欧の間の週）の一覧、取引日ごとの被覆（最終集計の入力）。

**価格は読まない・出さない**: CSV の 1 列目（時刻）だけを取り出す。出力は件数・時刻・被覆・
ハッシュだけ。

**この道具が今は出さないもの（PR-A の確定待ち）**: 日足の区分（部分観測・全日無記録・休場）、
連続区間とその開始・終了の理由、準備（必要な履歴本数）の確保とやり直し、研究対象区間、
候補間の差、8 通貨の共通区間。これらは下の「最終集計の骨格」に引数と出力形式だけを置き、
規則はコードに決め打ちせず PR-A の規則表・設定ファイルから読む（``load_counting_rules``・
``load_time_rule_table``）。呼ぶと ``PendingDesignError`` で止まる。

実行（リポジトリの根で。原データは worktree の外にあるので ``--root`` で渡す）::

    uv run python -m tools.survey.full_period_feasibility prelim \\
        --root <原データの置き場>/data/raw/oanda_mt5/2026-10-16 \\
        --out-dir docs/data/full_period_feasibility
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn, Protocol
from zoneinfo import ZoneInfo

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tools.survey.mt5_api_probe import (
    FULL_FETCH_LOG,
    FULL_MANIFEST,
    FULL_PAIRS,
    RATE_FIELDS,
    STATUS_OK,
    verify_full_dir,
)
from tools.survey.mt5_gap_aggregate import (
    AggregateError,
    Exclusion,
    expected_minutes,
    gap_runs,
    load_exclusions,
    time_rule,
)

MINUTE = 60
TOOL_VERSION = 1
CALENDAR_DEFAULT = Path("configs/calendars/fx_ny17_v2.yaml")
TIMEFRAMES_DEFAULT = Path("configs/calendars/timeframes_v1.yaml")
EXCLUSIONS_DEFAULT = Path("tools/survey/oanda_holiday_exceptions_2026-10-16.json")
DOCS_MANIFEST_DEFAULT = Path("docs/data/oanda_mt5_full_fetch_2026-10-16/manifest.json")
OUT_DIR_DEFAULT = Path("docs/data/full_period_feasibility")
#: 研究期間（UTC）。2024 年以降は参照しない。
PERIOD_START = datetime(2017, 1, 1, tzinfo=UTC)
PERIOD_END = datetime(2024, 1, 1, tzinfo=UTC)
DAILY_TIMEFRAME = "1d_ny17"
NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
#: 連続した無記録の長さの区分（分。統計の表示のためで、規則ではない。上端を含む）
RUN_BINS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 1),
    ("2", 2, 2),
    ("3-5", 3, 5),
    ("6-15", 6, 15),
    ("16-60", 16, 60),
    ("61-180", 61, 180),
    ("181-1440", 181, 1440),
    ("1441+", 1441, None),
)
#: 一覧に個別に出す連続した無記録の長さの下限（分。表示のため。規則ではない）
LONG_RUN_LIST_MINUTES = 61


class FeasibilityError(ValueError):
    """入力や設定で集計できないときの誤り。"""


class PendingDesignError(NotImplementedError):
    """数え方の設計（PR-A）が確定するまで実行しない処理を呼んだときの誤り。"""


# --- 入力（時刻だけ） --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileWindow:
    """manifest の 1 要求（通貨 × 月）。区間は API の生の時刻の [lo, hi)（epoch 秒）。"""

    rid: str
    pair: str
    year: int
    month: int
    lo_api: int
    hi_api: int
    acquired: bool  # 状態 ok でファイルを読めた
    reason: str = ""  # acquired でないときの理由（取得不足の内訳）


@dataclass(slots=True)
class PairSeries:
    """1 通貨の、全ファイルを連結した API の生の時刻の列（昇順）と取得の記録。"""

    pair: str
    windows: list[FileWindow] = field(default_factory=list)
    times_api: list[int] = field(default_factory=list)
    rows: int = 0
    duplicates: int = 0
    unaligned: int = 0
    unparseable: int = 0
    out_of_window: int = 0

    @property
    def acquired_windows(self) -> list[FileWindow]:
        return [w for w in self.windows if w.acquired]

    @property
    def shortfall_windows(self) -> list[FileWindow]:
        return [w for w in self.windows if not w.acquired]


def _epoch_text(text: str) -> int:
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise FeasibilityError(f"時刻 {text} にタイムゾーンが無い")
    return int(moment.timestamp())


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_time_column(data: bytes) -> tuple[list[int], int]:
    """展開後の CSV から 1 列目（時刻）だけを取り出す。価格の列は分割もしない。

    返り値は (時刻の列, 解釈できなかった行の数)。見出し行が違えば誤り。
    """
    lines = data.decode("utf-8").splitlines()
    if not lines or lines[0] != ",".join(RATE_FIELDS):
        raise FeasibilityError("見出し行が違う")
    times: list[int] = []
    bad = 0
    for line in lines[1:]:
        head = line.split(",", 1)[0]
        try:
            times.append(int(head))
        except ValueError:
            bad += 1
    return times, bad


def read_pair(root: Path, manifest: Mapping[str, Any], pair: str) -> PairSeries:
    """manifest の通貨 ``pair`` の全ファイルを読み、時刻だけを連結する。

    SHA-256 が合わないファイル・無いファイル・状態が ok でない要求は「取得不足」の区間として
    記録し、その時刻は使わない（無記録に読み替えない）。
    """
    series = PairSeries(pair=pair)
    entries = sorted(
        (e for e in manifest["files"].values() if e["pair"] == pair),
        key=lambda e: (int(e["year"]), int(e["month"])),
    )
    if not entries:
        raise FeasibilityError(f"manifest に通貨 {pair} の要求が無い")
    collected: list[int] = []
    for e in entries:
        lo = _epoch_text(str(e["requested_from_api_time"]))
        hi = _epoch_text(str(e["requested_to_api_time"]))
        base = FileWindow(
            rid=str(e["rid"]),
            pair=pair,
            year=int(e["year"]),
            month=int(e["month"]),
            lo_api=lo,
            hi_api=hi,
            acquired=False,
        )
        reason = ""
        path = root / str(e["file"])
        data = b""
        if e["status"] != STATUS_OK:
            reason = f"状態 {e['status']}"
        elif not path.exists():
            reason = "ファイルが無い"
        elif sha256_file(path) != e["sha256_gz"]:
            reason = "gzip の SHA-256 が合わない"
        else:
            data = gzip.decompress(path.read_bytes())
            if hashlib.sha256(data).hexdigest() != e["sha256_csv"]:
                reason = "展開後の SHA-256 が合わない"
        if reason:
            series.windows.append(replace(base, reason=reason))
            continue
        try:
            times, bad = read_time_column(data)
        except FeasibilityError as exc:
            series.windows.append(replace(base, reason=str(exc)))
            continue
        if bad:
            # 行の一部でも時刻を解釈できなければ、その要求の区間全体を取得不足にする
            series.unparseable += bad
            series.windows.append(replace(base, reason=f"時刻の解釈不能 {bad} 行"))
            continue
        series.windows.append(replace(base, acquired=True))
        series.rows += len(times)
        for t in times:
            if not lo <= t < hi:
                series.out_of_window += 1
            elif t % MINUTE:
                series.unaligned += 1
            else:
                collected.append(t)
    collected.sort()
    deduped: list[int] = []
    for t in collected:
        if deduped and deduped[-1] == t:
            series.duplicates += 1
            continue
        deduped.append(t)
    series.times_api = deduped
    return series


# --- 時刻の変換（候補の時計ごと） ------------------------------------------------------


class ClockFamily(StrEnum):
    """時刻の規則の表 版 1 の変換が属する時計（API の時刻 − UTC が 2 か 3 時間）。

    表の行はどれも、欧州の夏時間に合わせて +2/+3 が変わる時計（欧州式）か、米国の夏時間に
    合わせて変わる時計（米国式）のどちらか（両方冬・両方夏の週は両者が同じ変換）。
    どの区間でどちらを標準仮説・感度の候補にするかは表の内容で、ここでは決めない。
    """

    EU = "eu_clock"
    US = "us_clock"


_FAMILY_ZONE: dict[ClockFamily, ZoneInfo] = {ClockFamily.EU: LONDON, ClockFamily.US: NEW_YORK}


@lru_cache(maxsize=8192)
def _summer(zone_key: str, ordinal: int) -> bool:
    day = date.fromordinal(ordinal)
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=UTC).astimezone(ZoneInfo(zone_key))
    offset = noon.dst()
    return offset is not None and offset != timedelta(0)


def offset_hours(family: ClockFamily, api_epoch: int) -> int:
    """時計 ``family`` で、API の時刻 ``api_epoch`` の「API の時刻 − UTC」（時間）。

    API の時刻の暦日の正午で夏時間かを決める。切替は日曜の未明〜朝（欧州 01:00 UTC、
    米国 07:00 UTC）で、週末の休場（日曜 17:00 ニューヨークの開場より前）の中にある。
    """
    zone = _FAMILY_ZONE[family]
    day = datetime.fromtimestamp(api_epoch, UTC).date()
    return 3 if _summer(str(zone.key), day.toordinal()) else 2


def to_utc(api_epoch: int, family: ClockFamily) -> int:
    return api_epoch - offset_hours(family, api_epoch) * 3600


class TimeConversion(Protocol):
    """通貨と API の時刻から「API の時刻 − UTC」を返す変換（候補の時計、または規則の表の 1 列）。"""

    @property
    def name(self) -> str: ...

    def offset_hours(self, pair: str, api_epoch: int) -> int: ...


@dataclass(frozen=True, slots=True)
class FamilyConversion:
    """候補の時計 1 つを全期間に当てる変換（先行統計で使う）。"""

    family: ClockFamily

    @property
    def name(self) -> str:
        return self.family.value

    def offset_hours(self, pair: str, api_epoch: int) -> int:
        del pair
        return offset_hours(self.family, api_epoch)


def convert_series(
    series: PairSeries, conversion: TimeConversion
) -> tuple[list[int], list[tuple[int, int]], list[tuple[int, int, str]]]:
    """API の生の時刻を UTC に直す（元の列は変えない）。

    返り値は (UTC の時刻の列（昇順）, 取得した区間の UTC の列, 取得不足の区間と理由の列)。
    区間の両端はそれぞれの端の時刻での変換で直す（隣り合う区間は同じ端点で直るので連続のまま）。
    """
    shift_cache: dict[int, int] = {}

    def shift(t: int) -> int:
        day = t // 86400
        if day not in shift_cache:
            shift_cache[day] = conversion.offset_hours(series.pair, t) * 3600
        return shift_cache[day]

    utc = [t - shift(t) for t in series.times_api]
    if any(b <= a for a, b in zip(utc, utc[1:], strict=False)):
        raise FeasibilityError(f"{series.pair}: {conversion.name} の変換で時刻の順が崩れる")
    acquired = [
        (w.lo_api - shift(w.lo_api), w.hi_api - shift(w.hi_api))
        for w in series.windows
        if w.acquired
    ]
    shortfall = [
        (w.lo_api - shift(w.lo_api), w.hi_api - shift(w.hi_api), w.reason)
        for w in series.windows
        if not w.acquired
    ]
    return utc, acquired, shortfall


# --- 足が存在すべき分と、分ごとの状態 ---------------------------------------------------


def _utc(epoch: int) -> UtcTime:
    return UtcTime(datetime.fromtimestamp(epoch, UTC))


def _epoch(moment: UtcTime) -> int:
    return int(moment.value.timestamp())


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%MZ")


def period_bounds() -> tuple[int, int]:
    return int(PERIOD_START.timestamp()), int(PERIOD_END.timestamp())


def holiday_minutes(exclusions: Sequence[Exclusion], expected: Sequence[int]) -> set[int]:
    """休場の定義（付録 K-14）の区間に入る、カレンダー上の存在すべき分。"""
    out: set[int] = set()
    for e in exclusions:
        lo = bisect.bisect_left(expected, e.start)
        hi = bisect.bisect_left(expected, e.end)
        out.update(expected[lo:hi])
    return out


def _in_windows(windows: Sequence[tuple[int, int]], starts: Sequence[int], t: int) -> bool:
    """``t`` が取得した区間（昇順。``starts`` は各区間の始まり）のどれかに入るか。"""
    i = bisect.bisect_right(starts, t) - 1
    return i >= 0 and windows[i][0] <= t < windows[i][1]


@dataclass(slots=True)
class MinuteStatus:
    """1 通貨 × 1 変換の、存在すべき分の状態（UTC の分の開始。昇順）。

    - ``recorded``: 存在すべき分（休場の定義の外）で足がある
    - ``no_record``: 存在すべき分（休場の定義の外・取得した区間の中）で足が無い
    - ``shortfall``: 存在すべき分のうち取得した区間の外（取得不足。無記録に読み替えない）
    - ``holiday``: 休場の定義（付録 K-14）の分（足があってもなくても数えない。別に数える）
    - ``outside``: 存在すべき分の外にある足（カレンダーの休場・週末の中の足）
    """

    pair: str
    conversion: str
    expected: list[int]
    recorded: list[int] = field(default_factory=list)
    no_record: list[int] = field(default_factory=list)
    shortfall: list[int] = field(default_factory=list)
    holiday: list[int] = field(default_factory=list)
    holiday_present: list[int] = field(default_factory=list)
    outside: list[int] = field(default_factory=list)


def classify_minutes(
    pair: str,
    conversion: str,
    utc_times: Sequence[int],
    acquired: Sequence[tuple[int, int]],
    expected: Sequence[int],
    holidays: set[int],
) -> MinuteStatus:
    present = set(utc_times)
    status = MinuteStatus(pair=pair, conversion=conversion, expected=list(expected))
    windows = sorted(acquired)
    starts = [w[0] for w in windows]
    expected_set = set(expected)
    for minute in expected:
        if minute in holidays:
            status.holiday.append(minute)
            if minute in present:
                status.holiday_present.append(minute)
        elif minute in present:
            status.recorded.append(minute)
        elif _in_windows(windows, starts, minute):
            status.no_record.append(minute)
        else:
            status.shortfall.append(minute)
    status.outside = [t for t in utc_times if t not in expected_set]
    return status


def run_bin(minutes: int) -> str:
    for label, low, high in RUN_BINS:
        if minutes >= low and (high is None or minutes <= high):
            return label
    raise FeasibilityError(f"無記録の長さ {minutes} 分がどの区分にも入らない")


def _ym(epoch: int) -> tuple[int, int]:
    moment = datetime.fromtimestamp(epoch, UTC)
    return moment.year, moment.month


def _slice(sorted_values: Sequence[int], lo: int, hi: int) -> list[int]:
    return list(
        sorted_values[bisect.bisect_left(sorted_values, lo) : bisect.bisect_left(sorted_values, hi)]
    )


def _count_in(sorted_values: Sequence[int], lo: int, hi: int) -> int:
    return bisect.bisect_left(sorted_values, hi) - bisect.bisect_left(sorted_values, lo)


# --- 先行統計 ---------------------------------------------------------------------


MONTHLY_COLUMNS: tuple[str, ...] = (
    "pair",
    "conversion",
    "year",
    "month",
    "bars_in_period",
    "expected_minutes_calendar_v2",
    "holiday_def_minutes",
    "holiday_def_bars_present",
    "expected_minutes",
    "recorded_minutes",
    "no_record_minutes",
    "no_record_runs",
    "longest_no_record_run_minutes",
    "shortfall_minutes",
    "bars_outside_expected",
)


def monthly_rows(status: MinuteStatus, utc_times: Sequence[int]) -> list[dict[str, Any]]:
    """通貨 × 変換 × 年 × 月（UTC）の本数と無記録・取得不足の分数。"""
    lo, hi = period_bounds()
    counters: dict[tuple[int, int], Counter[str]] = {}

    def bump(t: int, key: str, n: int = 1) -> None:
        counters.setdefault(_ym(t), Counter())[key] += n

    for t in utc_times:
        if lo <= t < hi:
            bump(t, "bars_in_period")
    for t in status.expected:
        bump(t, "expected_minutes_calendar_v2")
    for t in status.holiday:
        bump(t, "holiday_def_minutes")
    for t in status.holiday_present:
        bump(t, "holiday_def_bars_present")
    for t in status.recorded:
        bump(t, "recorded_minutes")
    for t in status.no_record:
        bump(t, "no_record_minutes")
    for t in status.shortfall:
        bump(t, "shortfall_minutes")
    for t in status.outside:
        if lo <= t < hi:
            bump(t, "bars_outside_expected")
    longest: dict[tuple[int, int], int] = {}
    for start, end in gap_runs(status.no_record):
        key = _ym(start)
        bump(start, "no_record_runs")
        longest[key] = max(longest.get(key, 0), (end - start) // MINUTE)
    rows: list[dict[str, Any]] = []
    for year in range(PERIOD_START.year, PERIOD_END.year):
        for month in range(1, 13):
            c = counters.get((year, month), Counter())
            row: dict[str, Any] = {
                "pair": status.pair,
                "conversion": status.conversion,
                "year": year,
                "month": month,
            }
            for col in MONTHLY_COLUMNS[4:]:
                row[col] = c[col]
            row["expected_minutes"] = (
                c["recorded_minutes"] + c["no_record_minutes"] + c["shortfall_minutes"]
            )
            row["longest_no_record_run_minutes"] = longest.get((year, month), 0)
            rows.append(row)
    return rows


RUN_DIST_COLUMNS: tuple[str, ...] = (
    "pair",
    "conversion",
    "year",
    *(f"runs_{label}" for label, _, _ in RUN_BINS),
    "runs_total",
    "no_record_minutes",
)


def run_distribution_rows(status: MinuteStatus) -> list[dict[str, Any]]:
    """連続した無記録（時刻の上で隣り合う無記録の分の並び）の長さの分布（年ごと）。

    休場（カレンダー・休場の定義）や取得不足の分を挟むと別の並びになる。
    """
    by_year: dict[int, Counter[str]] = {}
    for start, end in gap_runs(status.no_record):
        minutes = (end - start) // MINUTE
        c = by_year.setdefault(_ym(start)[0], Counter())
        c[f"runs_{run_bin(minutes)}"] += 1
        c["runs_total"] += 1
        c["no_record_minutes"] += minutes
    rows: list[dict[str, Any]] = []
    for year in range(PERIOD_START.year, PERIOD_END.year):
        c = by_year.get(year, Counter())
        row: dict[str, Any] = {"pair": status.pair, "conversion": status.conversion, "year": year}
        for col in RUN_DIST_COLUMNS[3:]:
            row[col] = c[col]
        rows.append(row)
    return rows


LONG_RUN_COLUMNS: tuple[str, ...] = (
    "pair",
    "conversion",
    "start_utc",
    "end_utc",
    "minutes",
    "start_new_york",
)


def long_run_rows(status: MinuteStatus) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start, end in gap_runs(status.no_record):
        minutes = (end - start) // MINUTE
        if minutes >= LONG_RUN_LIST_MINUTES:
            rows.append(
                {
                    "pair": status.pair,
                    "conversion": status.conversion,
                    "start_utc": iso(start),
                    "end_utc": iso(end),
                    "minutes": minutes,
                    "start_new_york": datetime.fromtimestamp(start, UTC)
                    .astimezone(NEW_YORK)
                    .strftime("%Y-%m-%d %H:%M"),
                }
            )
    return rows


DAILY_COLUMNS: tuple[str, ...] = (
    "pair",
    "conversion",
    "trading_day",
    "interval_start_utc",
    "interval_end_utc",
    "expected_minutes_calendar_v2",
    "holiday_def_minutes",
    "expected_minutes",
    "recorded_minutes",
    "no_record_minutes",
    "shortfall_minutes",
    "first_recorded_utc",
    "longest_no_record_run_minutes",
)


@dataclass(frozen=True, slots=True)
class TradingDay:
    """カレンダー 版 2 の日足（``1d_ny17``）の 1 区間。休場で切り詰めた区間。"""

    label: str  # 取引日の終わりのニューヨークの日付
    start: int
    end: int  # 区間の終わり（足の公開時刻の基準。D03 の available_at の通常値）


def trading_days(
    calendar: TradingCalendar, timeframes: Mapping[str, TimeframeDefinition]
) -> list[TradingDay]:
    """研究期間の日足の区間（カレンダー上で丸ごと休場の取引日は含まれない）。"""
    lo, hi = period_bounds()
    definition = timeframes[DAILY_TIMEFRAME]
    out: list[TradingDay] = []
    for bar_start in calendar.expected_bar_starts(
        definition, Interval(start=_utc(lo), end=_utc(hi))
    ):
        interval = definition.expected_interval(calendar, bar_start)
        if interval is None:  # pragma: no cover - expected_bar_starts が除いている
            continue
        s, e = _epoch(interval.start), _epoch(interval.end)
        if s < lo or e > hi:
            continue
        label = (interval.end.value.astimezone(calendar.tz) - timedelta(microseconds=1)).date()
        out.append(TradingDay(label=label.isoformat(), start=s, end=e))
    return out


def daily_rows(status: MinuteStatus, days: Sequence[TradingDay]) -> list[dict[str, Any]]:
    """取引日ごとの被覆（最終集計の入力。日足の区分はまだ付けない）。"""
    runs = gap_runs(status.no_record)
    run_starts = [s for s, _ in runs]
    rows: list[dict[str, Any]] = []
    for day in days:
        recorded = _count_in(status.recorded, day.start, day.end)
        first = ""
        if recorded:
            first = iso(status.recorded[bisect.bisect_left(status.recorded, day.start)])
        longest = 0
        i = bisect.bisect_right(run_starts, day.start) - 1
        i = max(i, 0)
        while i < len(runs) and runs[i][0] < day.end:
            s, e = runs[i]
            overlap = min(e, day.end) - max(s, day.start)
            if overlap > 0:
                longest = max(longest, overlap // MINUTE)
            i += 1
        no_record = _count_in(status.no_record, day.start, day.end)
        shortfall = _count_in(status.shortfall, day.start, day.end)
        rows.append(
            {
                "pair": status.pair,
                "conversion": status.conversion,
                "trading_day": day.label,
                "interval_start_utc": iso(day.start),
                "interval_end_utc": iso(day.end),
                "expected_minutes_calendar_v2": _count_in(status.expected, day.start, day.end),
                "holiday_def_minutes": _count_in(status.holiday, day.start, day.end),
                "expected_minutes": recorded + no_record + shortfall,
                "recorded_minutes": recorded,
                "no_record_minutes": no_record,
                "shortfall_minutes": shortfall,
                "first_recorded_utc": first,
                "longest_no_record_run_minutes": longest,
            }
        )
    return rows


# --- 候補の時計で変換が変わる区間（米欧の間の週） ------------------------------------------


@dataclass(frozen=True, slots=True)
class DivergentSpan:
    """欧州式と米国式で変換が違う暦日の連なり（API の時刻の暦日。終わりを含む）。"""

    first_day: date
    last_day: date

    @property
    def season(self) -> str:
        return "spring" if self.first_day.month < 7 else "autumn"

    def utc_bounds(self) -> tuple[int, int]:
        """この暦日の連なりを両方の時計で UTC に直した区間の和（[lo, hi)）。"""
        lo_api = int(datetime.combine(self.first_day, datetime.min.time(), UTC).timestamp())
        hi_api = int(
            datetime.combine(
                self.last_day + timedelta(days=1), datetime.min.time(), UTC
            ).timestamp()
        )
        return lo_api - 3 * 3600, hi_api - 2 * 3600


def divergent_spans() -> list[DivergentSpan]:
    """研究期間で、欧州式と米国式の変換が違う暦日の連なりの一覧。"""
    out: list[DivergentSpan] = []
    day = PERIOD_START.date()
    current: date | None = None
    while day < PERIOD_END.date():
        differs = _summer(str(LONDON.key), day.toordinal()) != _summer(
            str(NEW_YORK.key), day.toordinal()
        )
        if differs and current is None:
            current = day
        if not differs and current is not None:
            out.append(DivergentSpan(current, day - timedelta(days=1)))
            current = None
        day += timedelta(days=1)
    if current is not None:  # pragma: no cover - 研究期間の終わりは両方冬
        out.append(DivergentSpan(current, PERIOD_END.date() - timedelta(days=1)))
    return out


DIVERGENT_COLUMNS: tuple[str, ...] = (
    "pair",
    "span_first_api_day",
    "span_last_api_day",
    "season",
    "table_v1_row",
    "table_v1_status",
    "table_v1_standard",
    "table_v1_candidate",
    "eu_expected_minutes",
    "eu_recorded_minutes",
    "eu_no_record_minutes",
    "us_expected_minutes",
    "us_recorded_minutes",
    "us_no_record_minutes",
    "eu_bars_outside_expected",
    "us_bars_outside_expected",
    "no_record_count_differs",
    "no_record_positions_differ",
)


def divergent_rows(
    pair: str, statuses: Mapping[ClockFamily, MinuteStatus], spans: Sequence[DivergentSpan]
) -> list[dict[str, Any]]:
    """米欧の間の区間ごとに、欧州式と米国式で数えた無記録を並べる。

    表 版 1 の行・標準・候補の欄は、既存の道具 ``mt5_gap_aggregate.time_rule``（付録 K-12・K-14
    で使った表 版 1 の写し）から読んだ参考。PR-A で表をファイルにした後は、その表から読む。
    """
    rows: list[dict[str, Any]] = []
    for span in spans:
        lo, hi = span.utc_bounds()
        mid = span.first_day + (span.last_day - span.first_day) // 2
        rule = time_rule(pair, mid)
        row: dict[str, Any] = {
            "pair": pair,
            "span_first_api_day": span.first_day.isoformat(),
            "span_last_api_day": span.last_day.isoformat(),
            "season": span.season,
            "table_v1_row": rule.row,
            "table_v1_status": rule.status,
            "table_v1_standard": f"{rule.standard.name}+{rule.standard.hours}",
            "table_v1_candidate": "・".join(f"{c.name}+{c.hours}" for c in rule.candidates),
        }
        for family, prefix in ((ClockFamily.EU, "eu"), (ClockFamily.US, "us")):
            s = statuses[family]
            rec = _count_in(s.recorded, lo, hi)
            nor = _count_in(s.no_record, lo, hi)
            sho = _count_in(s.shortfall, lo, hi)
            row[f"{prefix}_expected_minutes"] = rec + nor + sho
            row[f"{prefix}_recorded_minutes"] = rec
            row[f"{prefix}_no_record_minutes"] = nor
            row[f"{prefix}_bars_outside_expected"] = _count_in(s.outside, lo, hi)
        row["no_record_count_differs"] = row["eu_no_record_minutes"] != row["us_no_record_minutes"]
        row["no_record_positions_differ"] = _slice(
            statuses[ClockFamily.EU].no_record, lo, hi
        ) != _slice(statuses[ClockFamily.US].no_record, lo, hi)
        rows.append(row)
    return rows


# --- 取得範囲 ---------------------------------------------------------------------


COVERAGE_COLUMNS: tuple[str, ...] = (
    "pair",
    "files_listed",
    "files_acquired",
    "files_shortfall",
    "rows",
    "bars_used",
    "duplicates",
    "unaligned",
    "unparseable_rows",
    "out_of_window",
    "first_bar_api",
    "last_bar_api",
    "first_bar_utc_eu",
    "last_bar_utc_eu",
    "first_bar_utc_us",
    "last_bar_utc_us",
    "window_api_lo",
    "window_api_hi",
)


def coverage_row(series: PairSeries) -> dict[str, Any]:
    times = series.times_api
    first, last = (times[0], times[-1]) if times else (0, 0)
    return {
        "pair": series.pair,
        "files_listed": len(series.windows),
        "files_acquired": len(series.acquired_windows),
        "files_shortfall": len(series.shortfall_windows),
        "rows": series.rows,
        "bars_used": len(times),
        "duplicates": series.duplicates,
        "unaligned": series.unaligned,
        "unparseable_rows": series.unparseable,
        "out_of_window": series.out_of_window,
        "first_bar_api": iso(first) if times else "",
        "last_bar_api": iso(last) if times else "",
        "first_bar_utc_eu": iso(to_utc(first, ClockFamily.EU)) if times else "",
        "last_bar_utc_eu": iso(to_utc(last, ClockFamily.EU)) if times else "",
        "first_bar_utc_us": iso(to_utc(first, ClockFamily.US)) if times else "",
        "last_bar_utc_us": iso(to_utc(last, ClockFamily.US)) if times else "",
        "window_api_lo": iso(min(w.lo_api for w in series.windows)),
        "window_api_hi": iso(max(w.hi_api for w in series.windows)),
    }


# --- 最終集計の骨格（PR-A の確定待ち。規則はファイルから読む） ----------------------------------


@dataclass(frozen=True, slots=True)
class CountingRules:
    """数え方の規則（PR-A の設計で確定し、版付きの設定ファイルから読む）。

    この道具は規則の値を持たない。欄は PR-A が決める項目の入れ物で、既定値を置かない。

    - ``version`` / ``sha256``: 規則ファイルの版とハッシュ（再現の記録に書く）
    - ``warmup_daily_bars``: 現在の戦略の準備に必要な日足の本数（基盤の品質基準ではない）
    - ``holiday_source``: 休場の判定に使う定義（承認済みカレンダーの版、休場の定義ファイル）
    - ``partial_bar_counts_toward_warmup``: 部分観測の日足を準備の本数に数えるか
    - ``all_no_record_day_resets_warmup``: 予定取引日が全日無記録なら準備をやり直すか
    - ``shortfall_policy``: 取得不足を含む区間の扱い
    """

    version: str
    sha256: str
    warmup_daily_bars: int
    holiday_source: str
    partial_bar_counts_toward_warmup: bool
    all_no_record_day_resets_warmup: bool
    shortfall_policy: str


def _pending(what: str) -> NoReturn:
    raise PendingDesignError(
        f"{what} は数え方の設計（PR-A）の確定後に実装する。規則は PR-A の規則ファイルから読む"
    )


def load_counting_rules(path: Path) -> CountingRules:
    """入力点 1: PR-A の数え方の規則ファイル（形式は PR-A で確定）を読む。"""
    del path
    _pending("数え方の規則ファイルの読み込み")


def load_time_rule_table(path: Path) -> Sequence[TimeConversion]:
    """入力点 2: PR-A で版付きのファイルにした時刻の規則の表を読み、変換の列を返す。

    返り値は [標準仮説, 感度の候補 …] の順で、各要素は ``TimeConversion``（通貨と API の
    時刻から「API の時刻 − UTC」を返す）。表のハッシュは承認済みのものと照合する（D12-48）。
    """
    del path
    _pending("時刻の規則の表ファイルの読み込み")


class DayClass(StrEnum):
    """取引日の区分（名前は指示の語。判定条件は PR-A の規則から読む）。"""

    GENERATED = "generated"  # 日足を生成（全分が記録あり）
    PARTIAL = "partial"  # 部分観測の日足（被覆情報つきで生成。「完全」とは呼ばない）
    ALL_NO_RECORD = "all_no_record"  # 予定取引日の全日無記録（生成しない）
    HOLIDAY = "holiday"  # 休場（生成しない。準備の連続を切らない）
    SHORTFALL = "shortfall"  # 取得不足（必要な入力の不足）


FINAL_DAILY_COLUMNS: tuple[str, ...] = (
    *DAILY_COLUMNS,
    "day_class",
    "coverage_ratio",
    "published_at_utc",
)
SEGMENT_COLUMNS: tuple[str, ...] = (
    "pair",
    "conversion",
    "segment_id",
    "start_trading_day",
    "end_trading_day",
    "daily_bars",
    "partial_daily_bars",
    "start_reason",
    "end_reason",
)
RESEARCH_WINDOW_COLUMNS: tuple[str, ...] = (
    "pair",
    "conversion",
    "segment_id",
    "warmup_complete_published_at_utc",
    "research_start_utc",
    "research_end_utc",
    "research_daily_bars",
)
WARMUP_RESET_COLUMNS: tuple[str, ...] = (
    "pair",
    "conversion",
    "trading_day",
    "reason",
    "bars_before_reset",
)
CANDIDATE_DIFF_COLUMNS: tuple[str, ...] = (
    "pair",
    "standard",
    "candidate",
    "span_start_utc",
    "span_end_utc",
    "field",
    "standard_value",
    "candidate_value",
)
COMMON_WINDOW_COLUMNS: tuple[str, ...] = (
    "conversion",
    "start_utc",
    "end_utc",
    "daily_bars",
    "pairs",
)
COVERAGE_DIST_COLUMNS: tuple[str, ...] = (
    "pair",
    "conversion",
    "coverage_bin",
    "daily_bars",
)


def classify_days(daily: Sequence[Mapping[str, Any]], rules: CountingRules) -> list[dict[str, Any]]:
    """§3-B の「生成できる日足・部分観測・全日無記録・取得不足の数」と被覆率。

    入力は ``daily_rows`` の行、出力は ``FINAL_DAILY_COLUMNS`` の行。公開時刻は区間の終わり。
    """
    del daily, rules
    _pending("日足の区分")


def coverage_distribution(
    classified: Sequence[Mapping[str, Any]], rules: CountingRules
) -> list[dict[str, Any]]:
    """§3-B の「被覆率の分布」。出力は ``COVERAGE_DIST_COLUMNS``（区分の刻みも PR-A から）。"""
    del classified, rules
    _pending("被覆率の分布")


def continuous_segments(
    classified: Sequence[Mapping[str, Any]], rules: CountingRules
) -> list[dict[str, Any]]:
    """§3-B の「連続区間すべての開始・終了・日足本数、各区間が始まる理由と終わる理由」。

    出力は ``SEGMENT_COLUMNS``。最長区間だけにせず、すべての区間を出す。
    """
    del classified, rules
    _pending("連続区間")


def warmup_resets(
    classified: Sequence[Mapping[str, Any]], rules: CountingRules
) -> list[dict[str, Any]]:
    """§3-B の「準備のやり直しが起きる箇所と理由」。出力は ``WARMUP_RESET_COLUMNS``。"""
    del classified, rules
    _pending("準備のやり直し")


def research_windows(
    segments: Sequence[Mapping[str, Any]],
    classified: Sequence[Mapping[str, Any]],
    rules: CountingRules,
) -> list[dict[str, Any]]:
    """§3-B の「準備に必要な本数を確保した後の研究対象区間と本数」。

    研究対象区間の始まりは、準備に必要な最後の日足（例: 60 本目）の公開時刻より前を含めない。
    本数は ``rules.warmup_daily_bars`` から読む。出力は ``RESEARCH_WINDOW_COLUMNS``。
    """
    del segments, classified, rules
    _pending("研究対象区間")


def candidate_differences(
    by_conversion: Mapping[str, Sequence[Mapping[str, Any]]], rules: CountingRules
) -> list[dict[str, Any]]:
    """§3-B の「時刻の規則の候補間で結果が変わる区間と差」。出力は ``CANDIDATE_DIFF_COLUMNS``。"""
    del by_conversion, rules
    _pending("候補間の差")


def common_windows(
    windows_by_pair: Mapping[str, Sequence[Mapping[str, Any]]], rules: CountingRules
) -> list[dict[str, Any]]:
    """§3-B の「8 通貨共通の利用可能区間（参考）」。出力は ``COMMON_WINDOW_COLUMNS``。"""
    del windows_by_pair, rules
    _pending("8 通貨の共通区間")


# --- 実行 -------------------------------------------------------------------------


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row[c] for c in columns})
            n += 1
    return n


def root_label(text: str) -> str:
    """記録に書く置き場の名前。利用者の家のディレクトリを書かず、``data/raw/`` からにする。"""
    marker = "data/raw/"
    return text[text.index(marker) :] if marker in text else text


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


@dataclass(slots=True)
class PrelimResult:
    verify_problems: list[str]
    coverage: list[dict[str, Any]] = field(default_factory=list)
    monthly: list[dict[str, Any]] = field(default_factory=list)
    run_dist: list[dict[str, Any]] = field(default_factory=list)
    long_runs: list[dict[str, Any]] = field(default_factory=list)
    daily: list[dict[str, Any]] = field(default_factory=list)
    divergent: list[dict[str, Any]] = field(default_factory=list)


def run_prelim(
    root: Path,
    pairs: Sequence[str],
    calendar: TradingCalendar,
    timeframes: Mapping[str, TimeframeDefinition],
    exclusions: Sequence[Exclusion],
) -> PrelimResult:
    manifest = json.loads((root / FULL_MANIFEST).read_text(encoding="utf-8"))
    result = PrelimResult(verify_problems=verify_full_dir(root, pairs))
    lo, hi = period_bounds()
    expected = expected_minutes(calendar, lo, hi)
    holidays = holiday_minutes(exclusions, expected)
    days = trading_days(calendar, timeframes)
    spans = divergent_spans()
    for pair in pairs:
        series = read_pair(root, manifest, pair)
        result.coverage.append(coverage_row(series))
        statuses: dict[ClockFamily, MinuteStatus] = {}
        for family in ClockFamily:
            utc, acquired, _shortfall = convert_series(series, FamilyConversion(family))
            status = classify_minutes(pair, family.value, utc, acquired, expected, holidays)
            statuses[family] = status
            result.monthly.extend(monthly_rows(status, utc))
            result.run_dist.extend(run_distribution_rows(status))
            result.long_runs.extend(long_run_rows(status))
            result.daily.extend(daily_rows(status, days))
        result.divergent.extend(divergent_rows(pair, statuses, spans))
    return result


def write_prelim(result: PrelimResult, out_dir: Path) -> dict[str, int]:
    return {
        "coverage_by_pair.csv": _write_csv(
            out_dir / "coverage_by_pair.csv", COVERAGE_COLUMNS, result.coverage
        ),
        "monthly_stats.csv": _write_csv(
            out_dir / "monthly_stats.csv", MONTHLY_COLUMNS, result.monthly
        ),
        "no_record_run_distribution.csv": _write_csv(
            out_dir / "no_record_run_distribution.csv", RUN_DIST_COLUMNS, result.run_dist
        ),
        "long_no_record_runs.csv": _write_csv(
            out_dir / "long_no_record_runs.csv", LONG_RUN_COLUMNS, result.long_runs
        ),
        "daily_coverage.csv": _write_csv(
            out_dir / "daily_coverage.csv", DAILY_COLUMNS, result.daily
        ),
        "candidate_divergent_spans.csv": _write_csv(
            out_dir / "candidate_divergent_spans.csv", DIVERGENT_COLUMNS, result.divergent
        ),
    }


def _cmd_prelim(args: argparse.Namespace) -> int:
    root = Path(args.root)
    pairs = [p for p in args.pairs.split(",") if p] if args.pairs else list(FULL_PAIRS)
    docs_manifest = Path(args.docs_manifest)
    raw_manifest_sha = sha256_file(root / FULL_MANIFEST)
    docs_manifest_sha = sha256_file(docs_manifest)
    if raw_manifest_sha != docs_manifest_sha:
        print("manifest の版管理の写しと原データの置き場の manifest が一致しない", file=sys.stderr)
        return 2
    calendar = load_calendar(Path(args.calendar))
    timeframes = load_timeframes(Path(args.timeframes))
    exclusions = load_exclusions(Path(args.exclusions)) if args.exclusions else ()
    result = run_prelim(root, pairs, calendar, timeframes, exclusions)
    out_dir = Path(args.out_dir)
    counts = write_prelim(result, out_dir)
    run_record = {
        "tool": "tools/survey/full_period_feasibility.py",
        "tool_version": TOOL_VERSION,
        "tool_sha256": sha256_file(Path(__file__)),
        "stage": "prelim（数え方の設計 PR-A に依存しない先行統計。最終判定ではない）",
        "target_commit": _git_head(),
        "command": " ".join(
            [
                "uv run python -m tools.survey.full_period_feasibility",
                *(root_label(x) for x in sys.argv[1:]),
            ]
        ),
        "raw_root": root_label(str(root)),
        "inputs": {
            "manifest_sha256": raw_manifest_sha,
            "manifest_docs_copy": docs_manifest.as_posix(),
            "fetch_log_sha256": sha256_file(root / FULL_FETCH_LOG),
            "calendar": {"path": args.calendar, "sha256": sha256_file(Path(args.calendar))},
            "timeframes": {"path": args.timeframes, "sha256": sha256_file(Path(args.timeframes))},
            "holiday_definition": (
                {"path": args.exclusions, "sha256": sha256_file(Path(args.exclusions))}
                if args.exclusions
                else None
            ),
            "time_rule_table": (
                "候補の時計（欧州式・米国式）を全期間に当てた。表 版 1 の行・標準・候補の欄は"
                " mt5_gap_aggregate.time_rule（表 版 1 の写し）の参考。表のファイルと"
                "ハッシュは PR-A で確定"
            ),
            "counting_rules": "未確定（PR-A）",
        },
        "pairs": pairs,
        "period_utc": [PERIOD_START.isoformat(), PERIOD_END.isoformat()],
        "verify": {
            "files_checked": sum(r["files_listed"] for r in result.coverage),
            "problems": result.verify_problems,
        },
        "outputs": counts,
        "prices": "読んでいない・出力していない（CSV の 1 列目の時刻だけ）",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_record.json").write_text(
        json.dumps(run_record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(render_summary(result, run_record), encoding="utf-8")
    print(json.dumps({"verify_problems": len(result.verify_problems), "outputs": counts}))
    return 0 if not result.verify_problems else 1


# --- 人間向けの要約（出力の CSV と同じ行から作る。価格は含まない） --------------------------------


def _fmt(n: int) -> str:
    return f"{n:,}"


def _sum_by(
    rows: Iterable[Mapping[str, Any]], key: Sequence[str], col: str
) -> dict[tuple[Any, ...], int]:
    out: dict[tuple[Any, ...], int] = {}
    for r in rows:
        k = tuple(r[c] for c in key)
        out[k] = out.get(k, 0) + int(r[col])
    return out


SUMMARY_FAMILIES: tuple[ClockFamily, ...] = (ClockFamily.EU, ClockFamily.US)
FINAL_FUNCTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "生成できる日足・部分観測・全日無記録・取得不足の数、被覆率",
        "classify_days",
        "FINAL_DAILY_COLUMNS",
    ),
    ("被覆率の分布", "coverage_distribution", "COVERAGE_DIST_COLUMNS"),
    (
        "連続区間すべての開始・終了・日足本数、開始と終了の理由",
        "continuous_segments",
        "SEGMENT_COLUMNS",
    ),
    ("準備のやり直しの箇所と理由", "warmup_resets", "WARMUP_RESET_COLUMNS"),
    (
        "準備の後の研究対象区間と本数（必要な最後の日足の公開時刻より前を含めない）",
        "research_windows",
        "RESEARCH_WINDOW_COLUMNS",
    ),
    ("時刻の規則の候補の間で変わる区間と差", "candidate_differences", "CANDIDATE_DIFF_COLUMNS"),
    ("8 通貨共通の利用可能区間（参考）", "common_windows", "COMMON_WINDOW_COLUMNS"),
)
OUTPUT_DESCRIPTIONS: dict[str, str] = {
    "coverage_by_pair.csv": "通貨ごとの取得範囲・本数・時刻の問題の数",
    "monthly_stats.csv": (
        "通貨 × 時計 × 年 × 月（UTC）の本数・存在すべき分・無記録・取得不足・最長の無記録"
    ),
    "no_record_run_distribution.csv": "通貨 × 時計 × 年の、連続した無記録の長さの分布",
    "long_no_record_runs.csv": (
        f"{LONG_RUN_LIST_MINUTES} 分以上の連続した無記録の一覧（開始・終わり・長さ）"
    ),
    "daily_coverage.csv": "通貨 × 時計 × 取引日の被覆（最終集計の入力）",
    "candidate_divergent_spans.csv": "米欧の間の区間ごとの、欧州式と米国式の無記録の比較",
}


def _summary_head(lines: list[str]) -> None:
    lines += [
        "# 全期間のデータ上の実行可能性 — 先行統計（数え方の設計の確定前）",
        "",
        "人間の指示（2026-10-17）の「B. 全期間のデータ上の実行可能性の検証」のうち、"
        "**数え方の設計（PR-A）に依存しない部分**だけの結果。**最終判定ではない**。"
        "日足の区分（部分観測・全日無記録・休場）、連続区間、準備（現在の戦略に必要な日足の本数）の"
        "確保とやり直し、研究対象区間、8 通貨の共通区間は、PR-A の確定後に同じ道具で出す。",
        "",
        "- これは「データ上の実行可能性」の材料で、エンジンの正常動作や実際の市場での約定の"
        "再現性は確かめていない。",
        "- 価格は読んでいない・載せていない。CSV の 1 列目（時刻）だけを読んだ。"
        "件数・時刻・被覆・ハッシュだけ。",
        "- 2024 年以降は要求も参照もしていない。原データ・確定済み snapshot は変えていない。"
        "戦略の成績は使っていない。",
        "- この文書は道具が出力から作った（`tools/survey/full_period_feasibility.py` の"
        " `render_summary`）。",
        "",
    ]


def render_summary(result: PrelimResult, record: Mapping[str, Any]) -> str:
    """先行統計の人間向けの要約（Markdown）。数はすべて出力の CSV と同じ行から数える。"""
    pairs = [str(r["pair"]) for r in result.coverage]
    inputs = record["inputs"]
    key = ("pair", "conversion")
    expected = _sum_by(result.monthly, key, "expected_minutes")
    calendar_minutes = _sum_by(result.monthly, key, "expected_minutes_calendar_v2")
    holiday = _sum_by(result.monthly, key, "holiday_def_minutes")
    no_record = _sum_by(result.monthly, key, "no_record_minutes")
    outside = _sum_by(result.monthly, key, "bars_outside_expected")
    long_runs: Counter[tuple[Any, ...]] = Counter(
        (r["pair"], r["conversion"]) for r in result.long_runs
    )
    days_total: Counter[tuple[Any, ...]] = Counter()
    days_holiday_only: Counter[tuple[Any, ...]] = Counter()
    days_some: Counter[tuple[Any, ...]] = Counter()
    zero_days: dict[tuple[Any, ...], list[str]] = {}
    for r in result.daily:
        k = (r["pair"], r["conversion"])
        if int(r["expected_minutes"]) == 0:
            days_holiday_only[k] += 1
            continue
        days_total[k] += 1
        if int(r["no_record_minutes"]):
            days_some[k] += 1
        if int(r["recorded_minutes"]) == 0:
            zero_days.setdefault(k, []).append(str(r["trading_day"]))
    files = sum(int(r["files_listed"]) for r in result.coverage)
    time_problems = sum(
        int(r[c])
        for r in result.coverage
        for c in ("duplicates", "unaligned", "unparseable_rows", "out_of_window")
    )

    lines: list[str] = []
    _summary_head(lines)
    lines += [
        "## 1. 入力の確認",
        "",
        "| 項目 | 結果 |",
        "|---|---|",
        f"| 照合したファイル | {files}（{len(pairs)} 通貨） |",
        "| manifest との照合（gzip と展開後の SHA-256・行数・見出し・時刻が要求の区間の中。"
        "`mt5_api_probe.verify_full_dir` を再利用） | "
        f"問題 {len(result.verify_problems)} 件 |",
        f"| manifest の SHA-256（版管理の写しと原データの置き場で一致） | "
        f"`{inputs['manifest_sha256']}` |",
        f"| 取得の記録（`fetch_log.jsonl`）の SHA-256 | `{inputs['fetch_log_sha256']}` |",
        "| 取得不足の要求（失敗・未取得・破損・時刻の解釈不能） | "
        f"{sum(int(r['files_shortfall']) for r in result.coverage)} |",
        "| 取得不足の分（存在すべき分のうち取得した区間の外。両方の時計の計） | "
        f"{_fmt(sum(int(r['shortfall_minutes']) for r in result.monthly))} |",
        f"| 重複した時刻・分に揃わない時刻・解釈できない行・区間の外の時刻 | {time_problems} |",
        "",
        "## 2. 取得範囲（詳細は `coverage_by_pair.csv`）",
        "",
        "| 通貨 | 足の本数 | 最初の足（API の時刻） | 最後の足（API の時刻） | "
        "最初の足（UTC。欧州式／米国式） | 最後の足（UTC。欧州式／米国式） |",
        "|---|---|---|---|---|---|",
    ]
    for r in result.coverage:
        lines.append(
            f"| {r['pair']} | {_fmt(int(r['bars_used']))} | {r['first_bar_api']} | "
            f"{r['last_bar_api']} | {r['first_bar_utc_eu']}／{r['first_bar_utc_us']} | "
            f"{r['last_bar_utc_eu']}／{r['last_bar_utc_us']} |"
        )
    lines += [
        "",
        "## 3. 数え方（この先行統計で使った定義。PR-A の規則ではない）",
        "",
        "- **時計**: 時刻の規則の表 版 1 の行は、どれも「欧州の夏時間に合わせて +2/+3 が変わる"
        "時計（欧州式、`eu_clock`）」か「米国の夏時間に合わせて変わる時計（米国式、`us_clock`）」"
        "のどちらか。先行統計は**両方の時計を全期間に当てて**出した。"
        "区間ごとにどちらが標準仮説かは表の内容で、PR-A で表をファイルにした後に"
        "その表から選ぶ。API の生の時刻は変えずに保持する。",
        "- **存在すべき分**: 承認済みの取引カレンダー `fx_ny17` 版 2 のセッションの分"
        f"（研究期間で {_fmt(max(calendar_minutes.values(), default=0))} 分）。",
        "- **休場の定義の分**: 年末年始の休場の定義ファイル（付録 K-14）に入る分"
        f"（{_fmt(max(holiday.values(), default=0))} 分）は、無記録にも記録ありにも数えず"
        "別に数えた（その中にあった足は使っていない）。",
        "- **無記録**: 存在すべき分（休場の定義の外）で、取得した区間の中なのに足が無い分。",
        "- **取得不足**: 存在すべき分のうち、取得した区間の外、または要求の失敗・破損・"
        "時刻の解釈不能の区間の分。無記録に読み替えない。",
        "- **連続した無記録**: 時刻の上で隣り合う無記録の分の並び。"
        "休場や取得不足を挟むと別の並び。",
        "",
        "## 4. 無記録（詳細は `monthly_stats.csv`・`no_record_run_distribution.csv`・"
        "`long_no_record_runs.csv`・`daily_coverage.csv`）",
        "",
        "| 通貨 | 時計 | 存在すべき分（休場の定義の分を除く） | 無記録の分 | 割合 | 取引日 | "
        "無記録が 1 分以上ある日 | "
        f"記録が 0 分の日 | {LONG_RUN_LIST_MINUTES} 分以上の連続 | カレンダーの外の足 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for pair in pairs:
        for fam in SUMMARY_FAMILIES:
            k = (pair, fam.value)
            exp, nr, zd = expected[k], no_record[k], zero_days.get(k, [])
            ratio = f"{100 * nr / exp:.3f}%" if exp else "—"
            lines.append(
                f"| {pair} | {fam.value} | {_fmt(exp)} | {_fmt(nr)} | {ratio} | "
                f"{_fmt(days_total[k])} | {_fmt(days_some[k])} | "
                f"{len(zd)}（{'、'.join(zd) or 'なし'}） | {long_runs[k]} | {_fmt(outside[k])} |"
            )
    run_cols = [f"runs_{label}" for label, _, _ in RUN_BINS]
    lines += [
        "",
        "- 取引日はカレンダー 版 2 の日足の区間。存在すべき分がすべて休場の定義の中の日（通貨・"
        f"時計ごとに {'・'.join(str(x) for x in sorted(set(days_holiday_only.values())))} 日）は"
        "表の取引日から外した。",
        "- 連続した無記録の長さの分布（全通貨の合計。件）:",
        "",
        "| 時計 | " + " | ".join(f"{label} 分" for label, _, _ in RUN_BINS) + " |",
        "|---" * (len(RUN_BINS) + 1) + "|",
    ]
    for fam in SUMMARY_FAMILIES:
        counts = [
            sum(int(r[c]) for r in result.run_dist if r["conversion"] == fam.value)
            for c in run_cols
        ]
        lines.append(f"| {fam.value} | " + " | ".join(_fmt(n) for n in counts) + " |")
    spans = {(r["span_first_api_day"], r["span_last_api_day"]) for r in result.divergent}
    count_diff = sum(1 for r in result.divergent if r["no_record_count_differs"])
    pos_diff = sum(1 for r in result.divergent if r["no_record_positions_differ"])
    lines += [
        "",
        "- 年ごと・月ごとの数は CSV にある。",
        "",
        "## 5. 時計の候補で変換が変わる区間（米欧の間の週。"
        "詳細は `candidate_divergent_spans.csv`）",
        "",
        f"- 研究期間に {len(spans)} 区間。{len(pairs)} 通貨で {len(result.divergent)} 行。",
        f"- 欧州式と米国式で**無記録の分数**が変わる行: {count_diff}。"
        f"**無記録の分の位置**が変わる行: {pos_diff}"
        f"（そのうち分数は同じで位置だけが変わる行: {pos_diff - count_diff}）。",
        "- 表 版 1 の行・標準・候補の欄は、既存の道具 `mt5_gap_aggregate.time_rule`（付録 K-12・"
        "K-14 で使った表 版 1 の写し）から読んだ**参考**。PR-A で表をファイルにした後は、"
        "その表から読む。",
        "",
        "## 6. PR-A の確定後に出す項目と、規則の入力点",
        "",
        "道具に関数の骨格（引数と出力の列）を置いた。呼ぶと「設計の確定待ち」の誤り"
        "（`PendingDesignError`）で止まる。規則の値はコードに持たない。",
        "",
        "| 指示 §3-B の項目 | 関数 | 出力の列 |",
        "|---|---|---|",
        *(f"| {item} | `{fn}` | `{cols}` |" for item, fn, cols in FINAL_FUNCTIONS),
        "",
        "規則の入力点:",
        "",
        "1. `load_counting_rules(path)`: PR-A の数え方の規則ファイル（準備に必要な日足の本数、"
        "休場の判定に使う定義、部分観測の日足を準備に数えるか、全日無記録で準備をやり直すか、"
        "取得不足の扱い。版とハッシュ）。",
        "2. `load_time_rule_table(path)`: PR-A で版付きのファイルにした時刻の規則の表"
        "（標準仮説と感度の候補の変換。ハッシュは承認済みのものと照合）。",
        "",
        "最終集計の入力のデータは `daily_coverage.csv`（通貨 × 時計 × 取引日の、存在すべき分・"
        "休場の定義の分・記録あり・無記録・取得不足・最初の記録の時刻・最長の無記録・"
        "区間の終わり＝公開時刻の基準）。",
        "",
        "## 7. 再現方法",
        "",
        "```",
        str(record["command"]),
        "```",
        "",
        f"- 原データの置き場（リポジトリの根から）: `{record['raw_root']}`",
        f"- 対象のコミット: `{record['target_commit']}`、道具の SHA-256: `{record['tool_sha256']}`",
        f"- 取引カレンダー: `{inputs['calendar']['path']}`（`{inputs['calendar']['sha256']}`）",
        f"- 時間足の定義: `{inputs['timeframes']['path']}`（`{inputs['timeframes']['sha256']}`）",
    ]
    hd = inputs["holiday_definition"]
    if hd:
        lines.append(f"- 休場の定義: `{hd['path']}`（`{hd['sha256']}`）")
    lines += [
        f"- 時刻の規則の表: {inputs['time_rule_table']}",
        f"- 数え方の規則: {inputs['counting_rules']}",
        "- 入力・設定のハッシュ、出力の行数は `run_record.json`。",
        "",
        "## 8. ファイル",
        "",
        "| ファイル | 中身 | 行数 |",
        "|---|---|---|",
        *(
            f"| `{name}` | {OUTPUT_DESCRIPTIONS.get(name, '')} | {_fmt(int(n))} |"
            for name, n in record["outputs"].items()
        ),
        "| `run_record.json` | 再現の記録 | — |",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prelim", help="PR-A に依存しない先行統計を出す")
    p.add_argument("--root", required=True, help="原データの置き場（manifest.json のある所）")
    p.add_argument("--pairs", default="", help="通貨（カンマ区切り。既定は 8 通貨）")
    p.add_argument("--docs-manifest", default=str(DOCS_MANIFEST_DEFAULT))
    p.add_argument("--calendar", default=str(CALENDAR_DEFAULT))
    p.add_argument("--timeframes", default=str(TIMEFRAMES_DEFAULT))
    p.add_argument("--exclusions", default=str(EXCLUSIONS_DEFAULT))
    p.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prelim":
            return _cmd_prelim(args)
    except (FeasibilityError, AggregateError) as exc:
        print(f"誤り: {exc}", file=sys.stderr)
        return 2
    return 2  # pragma: no cover - argparse が required で止める


if __name__ == "__main__":
    raise SystemExit(main())
