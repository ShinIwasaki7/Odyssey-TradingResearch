"""MT5 API の代表 112 週の 1 分足の空白（欠損）の集計（D12 §11.9。Mac 側の道具。読むだけ）。

入力は空白の測定（段 G。付録 K-9.3）で回収した ``summary_ts-G.json``。読むのは要求ごとの
**時刻と件数だけ**（``rid``・``pair``・``state``・``date``・``requested_from_api_time``・
``requested_to_api_time``・``bar_times``）。価格の列は入力に無いが、あっても読まない
（``REQUEST_KEYS`` 以外の項目は捨てる）。出力も時刻と件数だけ。

手順（D12 §11.9.3・§11.9.6、D12-36・D12-39・D12-51 (b)）:

1. 時刻の変換は、期間別の時刻の規則の表 版 1（D12 §11.7.10。D12-48 で承認）で決める。
   変換は「API の時刻 − UTC」の時間数。仮定付きの範囲は標準仮説と感度の候補のそれぞれで
   UTC に直して集計し、どの規則による結果かと印（「確認済み」「仮定付き（…）」）を付ける。
2. 取引時間の中の分は、承認済みの取引カレンダー ``fx_ny17`` 版 2（D03 §3.4・§3.4.1）の
   セッション（週の開閉の中から宣言した休場・短縮を除いた区間）。その中で 1 分足が無い分を
   空白と数える。根拠の無い空白は必要な入力の不足（D12-36）として数え、多いという理由で
   正常な無更新に分けない。カレンダーに宣言の無い休場は推測で足さない（長い空白は一覧に出す）。
3. 指標 M1〜M6（D12 §11.9.3）を週ごとに出す。完全な足は「空白の分を 1 つでも含む足は欠け」
   （D12 §4.1 の層 3）。足の区間はカレンダーで切り詰めた区間（``expected_interval``）。
   ウォームアップは日足（``1d_ny17``）60 本。1 週では 60 本の連続を観測できないので、
   言えるのは観測した日の完全・欠けだけ（D12 §11.9.6 の 2）。

作業役の仮置き（付録 K-12 に記録。設計の決定ではない）:

- 取引のセッション（M3）は現地時刻で東京 09:00〜18:00、ロンドン 08:00〜17:00、
  ニューヨーク 08:00〜17:00、日替わりはニューヨーク 17:00〜18:00（重なりは両方に数える）。
- 曜日（M3）は取引日（ニューヨーク 17:00 で区切る）の終わりの日の曜日。
- 空白の 1 件は、時刻の上で連続した空白の分の並び（休場をまたぐと別の件）。

出典のある休場の除外（付録 K-14。D12-60 (a)・D12-61 (a)・D12-66 (a)・D12-68 (a)）:
``--exclusions`` に休場の定義ファイル（``oanda_holiday_exceptions/1``。各行に告知の URL と
SHA-256）を渡すと、その区間の分を「足が存在すべき時間」から外し、「休場（出典あり）」として
行ごとに別に数える（外した分・その中で足が無かった分・その中にあった足）。区間をはみ出す
空白は入力の不足のまま数え、前値で足を埋めない。休場の中にあった足は空白を埋めるのに使わない。
指定しないとき（既定）は旧の挙動のまま（出力も旧と同じ）。

実行（リポジトリの根で）::

    uv run python -m tools.survey.mt5_gap_aggregate \\
        --summary data/raw/oanda_mt5_probe/2026-10-16/ts-G-9/summary_ts-G.json \\
        --out-dir data/raw/oanda_mt5_probe/2026-10-16/gap_summary

    # 付録 K-14（年末年始の休場を外した再集計）
    uv run python -m tools.survey.mt5_gap_aggregate \\
        --summary data/raw/oanda_mt5_probe/2026-10-16/ts-G-9/summary_ts-G.json \\
        --exclusions tools/survey/oanda_holiday_exceptions_2026-10-16.json \\
        --out-dir data/raw/oanda_mt5_probe/2026-10-16/gap_summary_r16
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

MINUTE = 60
TIME_RULE_TABLE_VERSION = 1
CALENDAR_DEFAULT = Path("configs/calendars/fx_ny17_v2.yaml")
TIMEFRAMES_DEFAULT = Path("configs/calendars/timeframes_v1.yaml")
#: 完全な足の割合（M4）を出す時間足
COMPLETENESS_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h_ny17", "1d_ny17")
#: 戦略が読む時間足（M5。D12 §11.9.3）。日足が完全ならその日の 15 分足・1 時間足も完全
STRATEGY_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "1d_ny17")
#: ウォームアップの必要本数（日足。strategy_b_v1.yaml の window_bars。D09 §17.8.5）
WARMUP_DAILY_BARS = 60
#: 空白の長さの区分（M2。分。上端を含む。最後は上限なし）
LENGTH_BINS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 1),
    ("2", 2, 2),
    ("3-5", 3, 5),
    ("6-15", 6, 15),
    ("16-60", 16, 60),
    ("61+", 61, None),
)
#: カレンダーに無い休場・短縮の候補として一覧に出す空白の長さの下限（分）
LONG_GAP_MINUTES = 60
#: 入力の要求から読む項目（これ以外、特に価格の項目は読まない）
REQUEST_KEYS: tuple[str, ...] = (
    "rid",
    "pair",
    "state",
    "date",
    "requested_from_api_time",
    "requested_to_api_time",
    "bar_times",
)
#: 取引のセッション（M3。作業役の仮置き）: 名前 → (タイムゾーン, 開始, 終了)
SESSIONS: tuple[tuple[str, str, time, time], ...] = (
    ("tokyo", "Asia/Tokyo", time(9), time(18)),
    ("london", "Europe/London", time(8), time(17)),
    ("newyork", "America/New_York", time(8), time(17)),
    ("rollover_ny17", "America/New_York", time(17), time(18)),
)
WEEKDAYS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")


class AggregateError(ValueError):
    """入力や規則の表で集計できないときの誤り。"""


# --- 出典のある休場（足が存在すべき時間から外す区間。付録 K-14） ----------------------

EXCLUSIONS_SCHEMA = "oanda_holiday_exceptions/1"
JST_OFFSET = timedelta(hours=9)
SHA256_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class Exclusion:
    """足が存在すべき時間から外す区間 [start, end)（UTC の epoch 秒）。出典つき。"""

    id: str
    kind: str
    start: int
    end: int
    sources: tuple[str, ...]  # URL

    def contains(self, epoch: int) -> bool:
        return self.start <= epoch < self.end


def _parse_utc_text(text: str, where: str) -> datetime:
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise AggregateError(f"{where}: 時刻 {text} にタイムゾーンが無い")
    return moment


def parse_exclusions(payload: Mapping[str, Any]) -> tuple[Exclusion, ...]:
    """休場の定義を読む。日本時間と UTC の対応、出典（URL と SHA-256）、重なりを確かめる。"""
    if payload.get("schema") != EXCLUSIONS_SCHEMA:
        raise AggregateError(f"休場の定義の schema が {EXCLUSIONS_SCHEMA} ではない")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise AggregateError("休場の定義に entries の列が無い")
    out: list[Exclusion] = []
    for raw in entries:
        ident = str(raw.get("id"))
        for key in ("kind", "start_jst", "end_jst", "start_utc", "end_utc", "sources"):
            if not raw.get(key):
                raise AggregateError(f"休場 {ident} に項目 {key} が無い")
        start_jst = _parse_utc_text(str(raw["start_jst"]), ident)
        end_jst = _parse_utc_text(str(raw["end_jst"]), ident)
        start_utc = _parse_utc_text(str(raw["start_utc"]), ident)
        end_utc = _parse_utc_text(str(raw["end_utc"]), ident)
        for jst in (start_jst, end_jst):
            if jst.utcoffset() != JST_OFFSET:
                raise AggregateError(f"休場 {ident} の日本時間 {jst} が UTC+9 ではない")
        if start_jst != start_utc or end_jst != end_utc:
            raise AggregateError(f"休場 {ident} の日本時間と UTC が 9 時間の差で一致しない")
        if not start_utc < end_utc:
            raise AggregateError(f"休場 {ident} の開始が終端より前ではない")
        sources = raw["sources"]
        urls: list[str] = []
        for source in sources:
            url, digest = source.get("url"), str(source.get("sha256", ""))
            if not url or len(digest) != 64 or not set(digest) <= SHA256_HEX:
                raise AggregateError(f"休場 {ident} の出典に URL か SHA-256 が無い")
            urls.append(str(url))
        out.append(
            Exclusion(
                id=ident,
                kind=str(raw["kind"]),
                start=int(start_utc.timestamp()),
                end=int(end_utc.timestamp()),
                sources=tuple(urls),
            )
        )
    out.sort(key=lambda e: e.start)
    for before, after in zip(out, out[1:], strict=False):
        if after.start < before.end:
            raise AggregateError(f"休場 {before.id} と {after.id} が重なる")
    if len({e.id for e in out}) != len(out):
        raise AggregateError("休場の id が重複している")
    return tuple(out)


def load_exclusions(path: Path) -> tuple[Exclusion, ...]:
    return parse_exclusions(json.loads(path.read_text(encoding="utf-8")))


def _exclusion_of(exclusions: Sequence[Exclusion], epoch: int) -> Exclusion | None:
    for exclusion in exclusions:
        if exclusion.contains(epoch):
            return exclusion
    return None


# --- 時刻の規則の表 版 1（D12 §11.7.10） --------------------------------------------


@dataclass(frozen=True, slots=True)
class Conversion:
    """1 つの規則による変換（API の時刻 − UTC の時間数）。"""

    name: str
    hours: int


@dataclass(frozen=True, slots=True)
class TimeRule:
    """表 版 1 の 1 行を、ある通貨・週に当てた結果。"""

    row: int
    status: str  # "確認済み" か "仮定付き"
    standard: Conversion
    candidates: tuple[Conversion, ...]

    @property
    def conversions(self) -> tuple[Conversion, ...]:
        return (self.standard, *self.candidates)

    @property
    def same_across_candidates(self) -> bool:
        return not self.candidates

    def mark(self) -> str:
        if self.status == "確認済み":
            return (
                f"確認済み（表 版 1 の行 {self.row}、{self.standard.name} +{self.standard.hours}）"
            )
        if self.same_across_candidates:
            return (
                f"仮定付き（表 版 1 の行 {self.row}、候補の間で変換が同じ +{self.standard.hours}）"
            )
        cands = "・".join(f"{c.name} +{c.hours}" for c in self.candidates)
        return (
            f"仮定付き（表 版 1 の行 {self.row}、標準 {self.standard.name} +{self.standard.hours}、"
            f"候補 {cands}）"
        )


US = Conversion("米国式", 3)
EU = Conversion("欧州式", 2)
WINTER = Conversion("両方冬", 2)
SUMMER = Conversion("両方夏", 3)
RESEARCH_YEARS = range(2017, 2024)


def _is_summer(tz: ZoneInfo, day: date) -> bool:
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=UTC).astimezone(tz)
    offset = noon.dst()
    return offset is not None and offset != timedelta(0)


def time_rule(pair: str, day: date) -> TimeRule:
    """表 版 1 で、通貨 ``pair`` の日 ``day``（UTC の暦日）に当たる規則。

    米国の夏時間の中で欧州が冬時間の日を「米欧の間」とする（春・秋とも同じ形になる）。
    表の行の境目は切替の日曜（週末の休場の中）なので、取引日の中の日では行が 1 つに決まる。
    """
    if day.year not in RESEARCH_YEARS:
        raise AggregateError(f"{day} は研究期間 2017〜2023 の外で、表 版 1 に行が無い")
    us_summer = _is_summer(NEW_YORK, day)
    eu_summer = _is_summer(LONDON, day)
    if not us_summer and not eu_summer:
        return TimeRule(8, "仮定付き", WINTER, ())
    if us_summer and eu_summer:
        return TimeRule(9, "仮定付き", SUMMER, ())
    if eu_summer:  # pragma: no cover - 欧州だけ夏の日は 2017〜2023 に無い
        raise AggregateError(f"{day} は欧州だけ夏時間で、表 版 1 に行が無い")
    spring = day.month < 7
    year = day.year
    if year in (2017, 2019) and spring:
        return TimeRule(1 if pair == "USDJPY" else 2, "仮定付き", EU, (US,))
    if year == 2023 and spring:
        if pair == "USDJPY":
            return TimeRule(3, "確認済み", US, ())
        return TimeRule(4, "仮定付き", US, (EU,))
    if year in (2017, 2019):
        return TimeRule(5, "仮定付き", EU, (US,))
    if year == 2018:
        return TimeRule(6, "仮定付き", EU, (US,))
    return TimeRule(7, "仮定付き", US, (EU,))


def week_rule(pair: str, sunday: date) -> TimeRule:
    """週（日曜 ``sunday`` からの月〜金）の規則。週の中で行が変わったら誤り。"""
    rules = {time_rule(pair, sunday + timedelta(days=offset)) for offset in range(1, 6)}
    if len(rules) != 1:
        raise AggregateError(f"{pair} の {sunday} からの週で、表 版 1 の行が 1 つに決まらない")
    return rules.pop()


# --- 入力（時刻だけ） --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeekInput:
    rid: str
    pair: str
    kind: str  # W1 / W2
    sunday: date
    window_api: tuple[int, int]
    bar_times_api: tuple[int, ...]

    @property
    def year(self) -> int:
        return self.sunday.year


def _epoch(text: str) -> int:
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp())


def parse_request(raw: Mapping[str, Any]) -> WeekInput:
    """要求 1 件から時刻の項目だけを取り出す（``REQUEST_KEYS`` の外は読まない）。"""
    picked = {key: raw.get(key) for key in REQUEST_KEYS}
    missing = [key for key, value in picked.items() if value is None]
    if missing:
        raise AggregateError(f"要求 {picked.get('rid')} に項目が無い: {missing}")
    bars = picked["bar_times"]
    if not isinstance(bars, list) or not all(isinstance(v, int) for v in bars):
        raise AggregateError(f"要求 {picked['rid']} の bar_times は整数（epoch 秒）の列ではない")
    sunday = date.fromisoformat(str(picked["date"]))
    if sunday.weekday() != 6:
        raise AggregateError(f"要求 {picked['rid']} の date {sunday} は日曜ではない")
    return WeekInput(
        rid=str(picked["rid"]),
        pair=str(picked["pair"]),
        kind=str(picked["state"]),
        sunday=sunday,
        window_api=(
            _epoch(str(picked["requested_from_api_time"])),
            _epoch(str(picked["requested_to_api_time"])),
        ),
        bar_times_api=tuple(sorted(bars)),
    )


def read_summary(path: Path) -> list[WeekInput]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    requests = payload.get("requests") if isinstance(payload, dict) else None
    if not isinstance(requests, list):
        raise AggregateError(f"{path} に requests の列が無い")
    return [parse_request(item) for item in requests if item.get("kind") == "gapweek"]


# --- 1 週の集計 ------------------------------------------------------------------


def _utc(epoch: int) -> UtcTime:
    return UtcTime(datetime.fromtimestamp(epoch, UTC))


def _to_epoch(moment: UtcTime) -> int:
    return int(moment.value.timestamp())


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%MZ")


def ny_local(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).astimezone(NEW_YORK).strftime("%Y-%m-%d %H:%M")


def expected_minutes(calendar: TradingCalendar, lo: int, hi: int) -> list[int]:
    """[lo, hi) の中で、カレンダーのセッションに開始が入る 1 分足の開始時刻（epoch 秒）。"""
    out: list[int] = []
    for session in calendar.sessions(Interval(start=_utc(lo), end=_utc(hi))):
        start = _to_epoch(session.start)
        end = _to_epoch(session.end)
        first = start + (-start) % MINUTE
        out.extend(range(first, end, MINUTE))
    return out


def gap_runs(missing: Sequence[int]) -> list[tuple[int, int]]:
    """空白の分の開始時刻（昇順）を、時刻の上で連続するものどうし [開始, 終端) にまとめる。"""
    out: list[tuple[int, int]] = []
    for start in missing:
        if out and out[-1][1] == start:
            out[-1] = (out[-1][0], start + MINUTE)
        else:
            out.append((start, start + MINUTE))
    return out


def length_bin(minutes: int) -> str:
    for label, low, high in LENGTH_BINS:
        if minutes >= low and (high is None or minutes <= high):
            return label
    raise AggregateError(f"空白の長さ {minutes} 分がどの区分にも入らない")


def sessions_of(epoch: int) -> list[str]:
    moment = datetime.fromtimestamp(epoch, UTC)
    names: list[str] = []
    for name, tz_key, start, end in SESSIONS:
        local = moment.astimezone(ZoneInfo(tz_key)).time()
        if start <= local < end:
            names.append(name)
    return names


def trading_weekday(calendar: TradingCalendar, epoch: int) -> str:
    """取引日（ニューヨーク 17:00 で区切る）の終わりの日の曜日。"""
    day = calendar.trading_day_of(_utc(epoch))
    end_local = day.end.value.astimezone(calendar.tz)
    return WEEKDAYS[end_local.date().weekday()]


@dataclass(frozen=True, slots=True)
class DailyBar:
    start: int
    end: int
    label: str  # 取引日の終わりのニューヨークの日付
    expected_minutes: int
    missing_minutes: int

    @property
    def complete(self) -> bool:
        return self.missing_minutes == 0


@dataclass(slots=True)
class ExcludedCount:
    """1 つの休場（出典あり）の区間について、ある週で数えたもの。"""

    exclusion: Exclusion
    minutes: int = 0  # 取引カレンダーのセッションの中で外した分
    missing: int = 0  # そのうち足が無かった分（旧の集計では空白と数えていた分）
    bars_present: int = 0  # そのうち足があった分（使わない。空白を埋めない）


@dataclass(slots=True)
class WeekResult:
    conversion: Conversion
    window_utc: tuple[int, int]
    expected: int = 0
    missing: int = 0
    present_outside_session: int = 0
    unaligned: int = 0
    runs: list[tuple[int, int]] = field(default_factory=list)
    length_bins: Counter[str] = field(default_factory=Counter)
    by_hour_utc: Counter[int] = field(default_factory=Counter)
    by_session: Counter[str] = field(default_factory=Counter)
    by_weekday: Counter[str] = field(default_factory=Counter)
    bars_expected: dict[str, int] = field(default_factory=dict)
    bars_complete: dict[str, int] = field(default_factory=dict)
    daily: list[DailyBar] = field(default_factory=list)
    longest_gap_free_minutes: int = 0
    exclusions_applied: bool = False
    excluded: list[ExcludedCount] = field(default_factory=list)

    @property
    def excluded_minutes(self) -> int:
        return sum(c.minutes for c in self.excluded)

    @property
    def excluded_missing(self) -> int:
        return sum(c.missing for c in self.excluded)

    @property
    def excluded_bars_present(self) -> int:
        return sum(c.bars_present for c in self.excluded)

    @property
    def missing_ratio(self) -> float:
        return self.missing / self.expected if self.expected else 0.0

    @property
    def longest_run_minutes(self) -> int:
        return max(((e - s) // MINUTE for s, e in self.runs), default=0)

    @property
    def longest_complete_days(self) -> int:
        best = current = 0
        for bar in self.daily:
            current = current + 1 if bar.complete else 0
            best = max(best, current)
        return best

    @property
    def incomplete_days(self) -> list[DailyBar]:
        return [bar for bar in self.daily if not bar.complete]

    def comparable(self) -> tuple[Any, ...]:
        """候補の間で結果が同じかを比べるための値（変換の名前と窓は除く）。"""
        return (
            self.expected,
            self.missing,
            self.present_outside_session,
            tuple((e - s) for s, e in self.runs),
            tuple(sorted(self.by_hour_utc.items())),
            tuple(sorted(self.bars_complete.items())),
            tuple((d.label, d.missing_minutes) for d in self.daily),
            tuple((c.exclusion.id, c.minutes, c.missing, c.bars_present) for c in self.excluded),
        )


def _count_in(sorted_values: Sequence[int], lo: int, hi: int) -> int:
    return bisect.bisect_left(sorted_values, hi) - bisect.bisect_left(sorted_values, lo)


def aggregate_week(
    week: WeekInput,
    conversion: Conversion,
    calendar: TradingCalendar,
    timeframes: Mapping[str, TimeframeDefinition],
    exclusions: Sequence[Exclusion] = (),
) -> WeekResult:
    shift = conversion.hours * 3600
    lo, hi = week.window_api[0] - shift, week.window_api[1] - shift
    result = WeekResult(conversion=conversion, window_utc=(lo, hi))
    bars = [t - shift for t in week.bar_times_api if week.window_api[0] <= t < week.window_api[1]]
    result.unaligned = sum(1 for t in bars if t % MINUTE)
    present = set(bars)
    in_session = expected_minutes(calendar, lo, hi)
    session_set = set(in_session)
    result.present_outside_session = sum(1 for t in present if t not in session_set)
    expected = in_session
    if exclusions:
        # 出典のある休場の分を「足が存在すべき時間」から外し、休場ごとに別に数える
        result.exclusions_applied = True
        counts = {
            e.id: ExcludedCount(exclusion=e) for e in exclusions if e.start < hi and lo < e.end
        }
        expected = []
        for minute in in_session:
            hit = _exclusion_of(exclusions, minute)
            if hit is None:
                expected.append(minute)
                continue
            count = counts[hit.id]
            count.minutes += 1
            if minute in present:
                count.bars_present += 1
            else:
                count.missing += 1
        result.excluded = [c for c in counts.values() if c.minutes]
    missing = [t for t in expected if t not in present]
    result.expected = len(expected)
    result.missing = len(missing)
    result.runs = gap_runs(missing)
    for start, end in result.runs:
        result.length_bins[length_bin((end - start) // MINUTE)] += 1
    for minute in missing:
        result.by_hour_utc[datetime.fromtimestamp(minute, UTC).hour] += 1
        for name in sessions_of(minute):
            result.by_session[name] += 1
        result.by_weekday[trading_weekday(calendar, minute)] += 1

    window = Interval(start=_utc(lo), end=_utc(hi))
    for tf_id in COMPLETENESS_TIMEFRAMES:
        definition = timeframes[tf_id]
        total = complete = 0
        for bar_start in calendar.expected_bar_starts(definition, window):
            interval = definition.expected_interval(calendar, bar_start)
            if interval is None:  # pragma: no cover - expected_bar_starts が除いている
                continue
            s, e = _to_epoch(interval.start), _to_epoch(interval.end)
            if s < lo or e > hi:
                continue  # 窓の端で切れる足は数えない
            n_expected = _count_in(expected, s, e)
            if exclusions and n_expected == 0:
                continue  # 足の区間がすべて出典のある休場の中（存在すべき足ではない）
            total += 1
            n_missing = _count_in(missing, s, e)
            if n_missing == 0:
                complete += 1
            if tf_id == "1d_ny17":
                label = interval.end.value.astimezone(calendar.tz)
                result.daily.append(
                    DailyBar(
                        start=s,
                        end=e,
                        label=(label - timedelta(microseconds=1)).date().isoformat(),
                        expected_minutes=n_expected,
                        missing_minutes=n_missing,
                    )
                )
        result.bars_expected[tf_id] = total
        result.bars_complete[tf_id] = complete

    best = current = 0
    for minute in expected:
        current = current + 1 if minute in present else 0
        best = max(best, current)
    result.longest_gap_free_minutes = best
    return result


@dataclass(frozen=True, slots=True)
class WeekReport:
    week: WeekInput
    rule: TimeRule
    results: tuple[WeekResult, ...]

    @property
    def candidates_agree(self) -> bool:
        first = self.results[0].comparable()
        return all(r.comparable() == first for r in self.results[1:])


def aggregate(
    weeks: Iterable[WeekInput],
    calendar: TradingCalendar,
    timeframes: Mapping[str, TimeframeDefinition],
    exclusions: Sequence[Exclusion] = (),
) -> list[WeekReport]:
    reports: list[WeekReport] = []
    for week in weeks:
        rule = week_rule(week.pair, week.sunday)
        results = tuple(
            aggregate_week(week, c, calendar, timeframes, exclusions) for c in rule.conversions
        )
        reports.append(WeekReport(week=week, rule=rule, results=results))
    return reports


# --- 出力（時刻と件数だけ） ------------------------------------------------------


def _pct(value: float) -> str:
    return f"{100 * value:.3f}"


def week_rows(reports: Sequence[WeekReport]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for index, result in enumerate(report.results):
            row: dict[str, Any] = {
                "rid": report.week.rid,
                "pair": report.week.pair,
                "kind": report.week.kind,
                "year": report.week.year,
                "rule_row": report.rule.row,
                "status": report.rule.status,
                "mark": report.rule.mark(),
                "conversion": f"{result.conversion.name} +{result.conversion.hours}",
                "role": "標準" if index == 0 else "候補",
                "candidates_agree": report.candidates_agree,
                "window_utc_from": iso(result.window_utc[0]),
                "window_utc_to": iso(result.window_utc[1]),
                "m1_expected_minutes": result.expected,
                "m1_missing_minutes": result.missing,
                "m1_missing_pct": _pct(result.missing_ratio),
                "bars_outside_session": result.present_outside_session,
                "unaligned_bars": result.unaligned,
                "m2_runs": len(result.runs),
                "m2_longest_run_minutes": result.longest_run_minutes,
            }
            for label, _, _ in LENGTH_BINS:
                row[f"m2_len_{label}"] = result.length_bins.get(label, 0)
            for name, _, _, _ in SESSIONS:
                row[f"m3_session_{name}"] = result.by_session.get(name, 0)
            for day in WEEKDAYS:
                row[f"m3_weekday_{day}"] = result.by_weekday.get(day, 0)
            for hour in range(24):
                row[f"m3_hour_{hour:02d}"] = result.by_hour_utc.get(hour, 0)
            for tf_id in COMPLETENESS_TIMEFRAMES:
                row[f"m4_{tf_id}_expected"] = result.bars_expected.get(tf_id, 0)
                row[f"m4_{tf_id}_complete"] = result.bars_complete.get(tf_id, 0)
            row["m5_longest_complete_days"] = result.longest_complete_days
            row["m5_longest_gap_free_minutes"] = result.longest_gap_free_minutes
            row["m6_daily_bars"] = len(result.daily)
            row["m6_incomplete_days"] = " ".join(d.label for d in result.incomplete_days)
            if result.exclusions_applied:
                row["holiday_excluded_minutes"] = result.excluded_minutes
                row["holiday_missing_minutes"] = result.excluded_missing
                row["holiday_bars_present"] = result.excluded_bars_present
                row["holiday_ids"] = " ".join(c.exclusion.id for c in result.excluded)
            rows.append(row)
    return rows


def daily_rows(reports: Sequence[WeekReport]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for result in report.results:
            for bar in result.daily:
                rows.append(
                    {
                        "rid": report.week.rid,
                        "pair": report.week.pair,
                        "kind": report.week.kind,
                        "year": report.week.year,
                        "conversion": f"{result.conversion.name} +{result.conversion.hours}",
                        "trading_day": bar.label,
                        "start_utc": iso(bar.start),
                        "end_utc": iso(bar.end),
                        "expected_minutes": bar.expected_minutes,
                        "missing_minutes": bar.missing_minutes,
                        "complete": bar.complete,
                    }
                )
    return rows


def run_rows(reports: Sequence[WeekReport]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for result in report.results:
            for start, end in result.runs:
                rows.append(
                    {
                        "rid": report.week.rid,
                        "pair": report.week.pair,
                        "kind": report.week.kind,
                        "year": report.week.year,
                        "conversion": f"{result.conversion.name} +{result.conversion.hours}",
                        "start_utc": iso(start),
                        "end_utc": iso(end),
                        "start_ny": ny_local(start),
                        "end_ny": ny_local(end),
                        "minutes": (end - start) // MINUTE,
                    }
                )
    return rows


def holiday_rows(reports: Sequence[WeekReport]) -> list[dict[str, Any]]:
    """休場（出典あり）として外した分を、週 × 休場の行ごとに出す。"""
    rows: list[dict[str, Any]] = []
    for report in reports:
        for result in report.results:
            for count in result.excluded:
                rows.append(
                    {
                        "rid": report.week.rid,
                        "pair": report.week.pair,
                        "kind": report.week.kind,
                        "year": report.week.year,
                        "conversion": f"{result.conversion.name} +{result.conversion.hours}",
                        "holiday_id": count.exclusion.id,
                        "holiday_kind": count.exclusion.kind,
                        "start_utc": iso(count.exclusion.start),
                        "end_utc": iso(count.exclusion.end),
                        "start_ny": ny_local(count.exclusion.start),
                        "end_ny": ny_local(count.exclusion.end),
                        "excluded_minutes": count.minutes,
                        "missing_minutes": count.missing,
                        "bars_present": count.bars_present,
                        "sources": " ".join(count.exclusion.sources),
                    }
                )
    return rows


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def render_report(
    reports: Sequence[WeekReport],
    inputs: Mapping[str, str],
    exclusions: Sequence[Exclusion] = (),
) -> str:
    """通貨 × 週の種類 × 年の表（D12 §11.9.6 の 4 の区分）を Markdown で書く。"""
    lines = [
        "# 空白の集計（D12 §11.9。道具 tools/survey/mt5_gap_aggregate.py）",
        "",
        f"- 時刻の規則の表: 版 {TIME_RULE_TABLE_VERSION}（D12 §11.7.10）。"
        f"カレンダー: fx_ny17 版 {inputs.get('calendar_version', '?')}",
        *(f"- 入力 {key}: `{value}`" for key, value in inputs.items()),
        "",
    ]
    if exclusions:
        lines += [
            "## 休場（出典あり）として「足が存在すべき時間」から外した区間（付録 K-14）",
            "",
            "区間をはみ出す空白は入力の不足のまま数えた。休場の中にあった足は使っていない"
            "（前値で埋めない）。",
            "",
            "| 休場 | 種類 | 開始（NY） | 終端（NY） | 外した分（全週） | うち足が無かった分 "
            "| うち足があった分 |",
            "|---|---|---|---|---|---|---|",
        ]
        for exclusion in exclusions:
            counts = [
                c
                for rep in reports
                for c in rep.results[0].excluded
                if c.exclusion.id == exclusion.id
            ]
            lines.append(
                f"| {exclusion.id} | {exclusion.kind} | {ny_local(exclusion.start)} | "
                f"{ny_local(exclusion.end)} | {sum(c.minutes for c in counts)} | "
                f"{sum(c.missing for c in counts)} | {sum(c.bars_present for c in counts)} |"
            )
        lines.append("")
    lines += [
        "## 測定範囲の結果（仮定付きの印つき。標準仮説の変換。候補で違う週は下に別記）",
        "",
        "| 通貨 | 週 | 年 | 印 | M1 空白の分 / 取引時間の分（%） | 件数 | 最長（分） "
        "| 15分足 完全/全 | 1時間足 | 4時間足 | 日足 | M5 完全な日足の最長連続 | 欠けた日足 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        r = report.results[0]
        c = r.bars_complete
        x = r.bars_expected
        lines.append(
            f"| {report.week.pair} | {report.week.kind} | {report.week.year} | "
            f"{report.rule.mark()} | {r.missing} / {r.expected}（{_pct(r.missing_ratio)}） | "
            f"{len(r.runs)} | {r.longest_run_minutes} | "
            f"{c['15m']}/{x['15m']} | {c['1h']}/{x['1h']} | {c['4h_ny17']}/{x['4h_ny17']} | "
            f"{c['1d_ny17']}/{x['1d_ny17']} | {r.longest_complete_days} | "
            f"{' '.join(d.label for d in r.incomplete_days) or 'なし'} |"
        )
    differing = [rep for rep in reports if not rep.candidates_agree]
    lines += ["", "## 候補の規則で結果が違う週（両方を載せ、どちらも選ばない）", ""]
    if not differing:
        lines.append("なし（候補のある週が無いか、候補でも同じ結果）。")
    for report in differing:
        for r in report.results:
            lines.append(
                f"- {report.week.rid} {r.conversion.name} +{r.conversion.hours}: "
                f"空白 {r.missing} / {r.expected}、欠けた日足 "
                f"{' '.join(d.label for d in r.incomplete_days) or 'なし'}"
            )
    lines += [
        "",
        f"## 長い空白（{LONG_GAP_MINUTES} 分を超える。カレンダー版 2 に宣言の無い休場・短縮の"
        "候補の調べ先。出典は未確認で、空白として数えたまま）",
        "",
        "| 週 | 開始（NY） | 終端（NY） | 分 |",
        "|---|---|---|---|",
    ]
    for report in reports:
        r = report.results[0]
        for start, end in r.runs:
            if (end - start) // MINUTE > LONG_GAP_MINUTES:
                lines.append(
                    f"| {report.week.rid} | {ny_local(start)} | {ny_local(end)} | "
                    f"{(end - start) // MINUTE} |"
                )
    lines += [
        "",
        "## 全期間では未確認のこと",
        "",
        "- 測定していない週の空白の頻度。代表週の結果が他の週に当てはまるか。",
        f"- 完全な日足が {WARMUP_DAILY_BARS} 本続く期間があるか（実行可能期間の長さ）。"
        "1 週は日足 5 本前後で、60 本の連続は観測できない。",
        "- 代表週の空白から全期間の実行不能を断定しない（D12-39）。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, default=CALENDAR_DEFAULT)
    parser.add_argument("--timeframes", type=Path, default=TIMEFRAMES_DEFAULT)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--exclusions",
        type=Path,
        default=None,
        help="出典のある休場の定義（oanda_holiday_exceptions/1）。既定は無し（旧の挙動）",
    )
    args = parser.parse_args(argv)

    calendar = load_calendar(args.calendar)
    timeframes = load_timeframes(args.timeframes)
    weeks = read_summary(args.summary)
    exclusions = load_exclusions(args.exclusions) if args.exclusions else ()
    reports = aggregate(weeks, calendar, timeframes, exclusions)

    out: Path = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "week_metrics.csv", week_rows(reports))
    write_csv(out / "daily_bars.csv", daily_rows(reports))
    write_csv(out / "gap_runs.csv", run_rows(reports))
    inputs = {
        "summary_sha256": sha256_of(args.summary),
        "calendar": str(args.calendar),
        "calendar_sha256": sha256_of(args.calendar),
        "calendar_version": str(calendar.version),
        "timeframes_sha256": sha256_of(args.timeframes),
    }
    if args.exclusions:
        inputs["exclusions"] = str(args.exclusions)
        inputs["exclusions_sha256"] = sha256_of(args.exclusions)
        write_csv(out / "holiday_excluded.csv", holiday_rows(reports))
    (out / "report.md").write_text(render_report(reports, inputs, exclusions), encoding="utf-8")
    (out / "inputs.json").write_text(
        json.dumps({**inputs, "weeks": len(reports)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"weeks={len(reports)} out={out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
