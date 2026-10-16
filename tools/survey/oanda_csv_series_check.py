"""保有済みの OANDA 配布 tick CSV を主系列（正本）候補として検査する（Mac 側。読むだけ）。

人間の第 16 回の回答（D12-67 の原文。付録 K-15）の段 1。配布 CSV を MT5 API とは独立した系列として
検査し、**API の欠損を CSV で埋める構成にはしない**（両者の品質を並べるだけ）。戦略の成績は見ない。

検査項目（通貨 × 月。1 ファイル = 日本時間の 1 か月。FAQ 2191）:

1. 時刻: 元の時刻（MT5 のサーバー時刻 = ニューヨークの現地時刻 + 7 時間。FAQ 2190。付録 K-9.6 の (a)
   で確かめた変換 ``mt5_api_probe.server_time_to_utc``）を UTC に直す。書式の違反、逆行、
   日本時間の月の外の行、2024 年以降の行（あれば止める）。
2. bid/ask: 逆転（ask < bid）、同値、欠落（空・数値でない）、小数の桁の分布、
   スプレッドの分布（point）、tick の間の bid の跳び（pip）。
3. 重複: 同じ時刻の連続、同じ内容（時刻・bid・ask）の連続。
4. 更新のない時間: 取引時間の中の tick の間隔の分布と、tick の無い分（空白）の件数・長さ・時間帯。
5. 取引時間との整合: 承認済みの取引カレンダー ``fx_ny17`` 版 2 のセッションの外の tick、
   K-13 で告知の揃った年末年始の休場（3 月のファイルには掛からない。掛かるかを判定して出す）。

足の生成（同じ経路）: tick の bid から 1 分足を作り（区間に tick が 1 件も無い分は足を作らない。
架空の価格で埋めない）、本基盤の集約 ``odyssey_fx.marketdata.application.aggregation.aggregate``
（D03 §5。不完全な区間は足を作らず欠落として報告する）で 1 分足 → 15 分足 → 1 時間足 → 4 時間足 →
日足と順に作る。照合として 1 時間足 → 日足（D03 §5.1 の経路）も作り、日足の集合が同じことを
確かめる。「空白」の定義は 2 通り: (1) 現行規則（空白の分を 1 分でも含む足は欠け）、(2) K-13 で
説明できた年末年始の休場（``--exclusions``。K-14 の再集計と同じ定義ファイル）を足が存在すべき
時間から外した場合（D12-66 (a)。外す区間が窓に掛からなければ (1) と同じ）。

出力は統計値と時刻だけ（価格の水準は書かない。差は point・pip）。生の tick は書き出さない。

実行（リポジトリの根で）::

    uv run python -m tools.survey.oanda_csv_series_check \\
        --csv-root /Users/sanchez/Desktop/Odyssey-TradingResearch/data/raw/oanda \\
        --probe-root /Users/sanchez/Desktop/Odyssey-TradingResearch/data/raw/oanda_mt5_probe \\
        --out-dir /Users/sanchez/Desktop/Odyssey-TradingResearch/data/raw/oanda_csv_check/2026-10-16
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.common.money import Price, decimal_from_int
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.application.aggregation import aggregate
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import FixedUtcAlignment, TimeframeDefinition
from tools.survey import mt5_api_probe as probe
from tools.survey.mt5_gap_aggregate import (
    LENGTH_BINS,
    WARMUP_DAILY_BARS,
    AggregateError,
    gap_runs,
    length_bin,
    time_rule,
)

MINUTE = 60
MINUTE_MS = 60_000
HOUR_MS = 3_600_000
CALENDAR_DEFAULT = Path("configs/calendars/fx_ny17_v2.yaml")
TIMEFRAMES_DEFAULT = Path("configs/calendars/timeframes_v1.yaml")
TOKYO = ZoneInfo("Asia/Tokyo")
NEW_YORK = ZoneInfo("America/New_York")
#: 価格を整数にするときの桁（5 桁 = EURUSD の point。JPY の 3 桁はこの中に収まる）
PRICE_SCALE_DIGITS = 5
#: 1 pip = 10 point（JPY は 0.01、USD は 0.0001。どちらも point の 10 倍）
POINTS_PER_PIP = 10
#: tick の間の bid の跳びとして数える下限（pip）。桁の誤りの検出用の目安で、品質の閾値ではない
JUMP_PIPS = 20
#: tick の間隔の区分（ミリ秒。取引時間の中、同じセッションの中の連続した tick の間）
INTERVAL_THRESHOLDS_MS: tuple[tuple[str, int], ...] = (
    ("ge_1min", 60_000),
    ("ge_5min", 300_000),
    ("ge_15min", 900_000),
    ("ge_60min", 3_600_000),
)
#: 生成する時間足の経路（1 分足 → 15 分足 → 1 時間足 → 4 時間足 → 日足）
CHAIN: tuple[str, ...] = ("15m", "1h", "4h_ny17", "1d_ny17")
#: 配布 CSV の対象の通貨（FAQ 965。付録 I-2）
CSV_PAIRS: tuple[str, ...] = ("USDJPY", "EURJPY", "EURUSD", "GBPJPY")
FILE_NAME = re.compile(r"ticks_(?P<pair>[A-Z]{6})-oj5k_(?P<year>\d{4})-(?P<month>\d{2})\.csv")
LINE = re.compile(
    rb"(\d{4})\.(\d{2})\.(\d{2})\t(\d{2}):(\d{2}):(\d{2})\.(\d{3})\t(\d+)\.(\d+)\t(\d+)\.(\d+)\t\t\r?\n?"
)
HEADER = b"<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>"
RESEARCH_END_YEAR = 2023


class CheckError(ValueError):
    """検査できない入力（2024 年以降の行、名前の形の違うファイルなど）。"""


# --- 時刻 -----------------------------------------------------------------------


def jst_month_window(year: int, month: int) -> tuple[int, int]:
    """ファイルの月（日本時間の 1 日 0:00 から翌月 1 日 0:00。FAQ 2191）を UTC のミリ秒で返す。"""
    start = datetime(year, month, 1, tzinfo=TOKYO)
    nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=TOKYO)
    return int(start.timestamp() * 1000), int(nxt.timestamp() * 1000)


class ServerClock:
    """元の時刻（サーバー時刻）の時ごとの UTC の始まりを覚えて変換を速くする。

    変換の規則そのものは ``mt5_api_probe.server_time_to_utc``（ニューヨークの現地時刻 + 7 時間）。
    その時の中で夏時間の切替が起きる時（次の時の始まりが 1 時間後でない）は、行ごとに変換する。
    """

    def __init__(self) -> None:
        self._base: dict[bytes, int | None] = {}

    @staticmethod
    def _hour_start(day: str, hour: int) -> int:
        moment = probe.server_time_to_utc(day, f"{hour:02d}:00:00.000")
        return int(moment.timestamp() * 1000)

    def to_utc_ms(self, d: bytes, hh: bytes, mm: bytes, ss: bytes, ms: bytes) -> int:
        key = d + hh
        if key not in self._base:
            day = d.decode()
            hour = int(hh)
            start = self._hour_start(day, hour)
            moment = datetime.strptime(day, "%Y.%m.%d") + timedelta(hours=hour + 1)
            nxt = self._hour_start(moment.strftime("%Y.%m.%d"), moment.hour)
            self._base[key] = start if nxt - start == HOUR_MS else None
        base = self._base[key]
        offset = int(mm) * MINUTE_MS + int(ss) * 1000 + int(ms)
        if base is not None:
            return base + offset
        text = f"{hh.decode()}:{mm.decode()}:{ss.decode()}.{ms.decode()}"
        return int(probe.server_time_to_utc(d.decode(), text).timestamp() * 1000)


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def iso_min(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%MZ")


def ny_min(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).astimezone(NEW_YORK).strftime("%Y-%m-%d %H:%M")


def _utc(epoch: int) -> UtcTime:
    return UtcTime(datetime.fromtimestamp(epoch, UTC))


def _epoch(moment: UtcTime) -> int:
    return int(moment.value.timestamp())


def session_bounds(calendar: TradingCalendar, lo_ms: int, hi_ms: int) -> list[tuple[int, int]]:
    """窓の中のセッション（カレンダー版 2。宣言した休場・短縮を除く）を UTC のミリ秒で返す。"""
    window = Interval(start=_utc(lo_ms // 1000), end=_utc(hi_ms // 1000))
    return [(_epoch(s.start) * 1000, _epoch(s.end) * 1000) for s in calendar.sessions(window)]


def expected_minutes(sessions: Sequence[tuple[int, int]]) -> list[int]:
    """セッションの中に開始が入る 1 分足の開始時刻（epoch 秒、昇順）。"""
    out: list[int] = []
    for start_ms, end_ms in sessions:
        start, end = start_ms // 1000, end_ms // 1000
        first = start + (-start) % MINUTE
        out.extend(range(first, end, MINUTE))
    return out


# --- 1 ファイルの走査 -------------------------------------------------------------


def _quantile(counter: Mapping[int, int], q: float) -> int | None:
    total = sum(counter.values())
    if total == 0:
        return None
    rank = q * (total - 1)
    seen = 0
    for value in sorted(counter):
        seen += counter[value]
        if seen > rank:
            return value
    return max(counter)  # pragma: no cover - 上の繰り返しで必ず返る


def _scaled(int_part: bytes, frac: bytes) -> int:
    return int(int_part + frac) * int(10 ** (PRICE_SCALE_DIGITS - len(frac)))


@dataclass(slots=True)
class FileScan:
    """1 ファイルの走査の結果（統計値と、分ごとの bid の 4 値（整数。出力しない））。"""

    pair: str
    month: str
    window_ms: tuple[int, int]
    sha256: str = ""
    size_bytes: int = 0
    rows: int = 0
    header_ok: bool = False
    format_violations: int = 0
    empty_or_non_numeric_price: int = 0
    last_or_volume_not_empty: int = 0
    nonpositive_price: int = 0
    backward: int = 0
    same_time_consecutive: int = 0
    same_content_consecutive: int = 0
    ask_lt_bid: int = 0
    ask_eq_bid: int = 0
    bid_digits: Counter[int] = field(default_factory=Counter)
    ask_digits: Counter[int] = field(default_factory=Counter)
    spread_points: Counter[int] = field(default_factory=Counter)
    jump_pips: Counter[int] = field(default_factory=Counter)
    interval_ms: Counter[int] = field(default_factory=Counter)
    first_ms: int | None = None
    last_ms: int | None = None
    outside_month: int = 0
    outside_session: int = 0
    outside_session_ny_dow_hour: Counter[str] = field(default_factory=Counter)
    ticks_in_session: int = 0
    minutes: dict[int, list[int]] = field(default_factory=dict)  # 分の始め（秒）→ [o, h, l, c]

    @property
    def point_digits(self) -> int:
        return self.bid_digits.most_common(1)[0][0] if self.bid_digits else PRICE_SCALE_DIGITS

    @property
    def scale_per_point(self) -> int:
        return int(10 ** (PRICE_SCALE_DIGITS - self.point_digits))


def _diagnose(line: bytes, scan: FileScan) -> None:
    """正規表現に合わない行の内訳（どれにも当てはまらなければ書式の違反だけ数える）。"""
    scan.format_violations += 1
    parts = line.rstrip(b"\r\n").split(b"\t")
    if len(parts) == 6:
        if any(not re.fullmatch(rb"\d+(\.\d+)?", p) for p in parts[2:4]):
            scan.empty_or_non_numeric_price += 1
        if parts[4] or parts[5]:
            scan.last_or_volume_not_empty += 1


def scan_file(path: Path, calendar: TradingCalendar) -> FileScan:
    """1 ファイルを 1 行ずつ読む（原データは変更しない）。"""
    found = FILE_NAME.fullmatch(path.name)
    if found is None:
        raise CheckError(f"{path.name} は配布 CSV の名前の形ではない")
    year, month = int(found["year"]), int(found["month"])
    if year > RESEARCH_END_YEAR:
        raise CheckError(f"{path.name} は 2024 年以降のファイルで、読まない")
    window = jst_month_window(year, month)
    scan = FileScan(pair=found["pair"], month=f"{year:04d}-{month:02d}", window_ms=window)
    margin = 2 * 24 * HOUR_MS  # 窓の外の行もセッションの判定ができるように前後 2 日を足す
    sessions = session_bounds(calendar, window[0] - margin, window[1] + margin)
    starts = [s for s, _ in sessions]
    clock = ServerClock()
    digest = hashlib.sha256()
    prev_ms: int | None = None
    prev_key: tuple[int, int, int] | None = None
    prev_session = -1
    prev_bid = 0
    with path.open("rb") as handle:
        header = handle.readline()
        digest.update(header)
        scan.size_bytes += len(header)
        scan.header_ok = header.rstrip(b"\r\n") == HEADER
        for line in handle:
            digest.update(line)
            scan.size_bytes += len(line)
            scan.rows += 1
            m = LINE.fullmatch(line)
            if m is None:
                _diagnose(line, scan)
                continue
            y, mo, dd, hh, mi, ss, ms, b_int, b_frac, a_int, a_frac = m.groups()
            if int(y) > RESEARCH_END_YEAR:
                raise CheckError(f"{path.name} に 2024 年以降の行がある（読むのを止めた）")
            t = clock.to_utc_ms(y + b"." + mo + b"." + dd, hh, mi, ss, ms)
            bid = _scaled(b_int, b_frac)
            ask = _scaled(a_int, a_frac)
            scan.bid_digits[len(b_frac)] += 1
            scan.ask_digits[len(a_frac)] += 1
            if bid <= 0 or ask <= 0:
                scan.nonpositive_price += 1
            if ask < bid:
                scan.ask_lt_bid += 1
            elif ask == bid:
                scan.ask_eq_bid += 1
            key = (t, bid, ask)
            if prev_ms is not None:
                if t < prev_ms:
                    scan.backward += 1
                elif t == prev_ms:
                    scan.same_time_consecutive += 1
                    if key == prev_key:
                        scan.same_content_consecutive += 1
            if scan.first_ms is None or t < scan.first_ms:
                scan.first_ms = t
            if scan.last_ms is None or t > scan.last_ms:
                scan.last_ms = t
            if not window[0] <= t < window[1]:
                scan.outside_month += 1
            idx = bisect.bisect_right(starts, t) - 1
            in_session = idx >= 0 and t < sessions[idx][1]
            if not in_session:
                scan.outside_session += 1
                ny = datetime.fromtimestamp(t / 1000, UTC).astimezone(NEW_YORK)
                scan.outside_session_ny_dow_hour[ny.strftime("%a %H")] += 1
                prev_session = -1
            else:
                scan.ticks_in_session += 1
                if prev_session == idx and prev_ms is not None and t >= prev_ms:
                    scan.interval_ms[t - prev_ms] += 1
                    scan.jump_pips[abs(bid - prev_bid)] += 1  # 整数の倍率のまま。最後に pip へ
                prev_session = idx
                prev_bid = bid
                if window[0] <= t < window[1]:
                    minute = t // MINUTE_MS * MINUTE
                    bar = scan.minutes.get(minute)
                    if bar is None:
                        scan.minutes[minute] = [bid, bid, bid, bid]
                    else:
                        if bid > bar[1]:
                            bar[1] = bid
                        if bid < bar[2]:
                            bar[2] = bid
                        bar[3] = bid
            prev_ms = t
            prev_key = key
            scan.spread_points[ask - bid] += 1
    scan.sha256 = digest.hexdigest()
    # 単位の直し（整数の倍率 → point・pip）
    per_point = scan.scale_per_point
    scan.spread_points = Counter({k // per_point: v for k, v in scan.spread_points.items()})
    regrouped: Counter[int] = Counter()
    for k, v in scan.jump_pips.items():
        regrouped[k // (per_point * POINTS_PER_PIP)] += v
    scan.jump_pips = regrouped
    return scan


# --- 足の生成（同じ経路） -----------------------------------------------------------


ONE_MINUTE = TimeframeDefinition(
    ref=TimeframeRef(id="1m", version=1),
    nominal_length=timedelta(minutes=1),
    alignment=FixedUtcAlignment(step=timedelta(minutes=1)),
)


def minute_bars(scan: FileScan, expected: Iterable[int]) -> tuple[Bar, ...]:
    """tick の bid から 1 分足を作る（存在すべき分で、tick が 1 件以上ある分だけ）。

    出来高は 0 を書き「出来高不明」を意味する（D12-31・D03 §14.18 の 4 と同じ）。
    """
    series = SeriesId(Symbol(scan.pair), ONE_MINUTE.ref, PriceBasis.BID)
    scale = Decimal(10) ** PRICE_SCALE_DIGITS
    bars: list[Bar] = []
    for minute in expected:
        values = scan.minutes.get(minute)
        if values is None:
            continue
        o, h, lo, c = (Price(Decimal(v) / scale) for v in values)
        interval = Interval(start=_utc(minute), end=_utc(minute + MINUTE))
        bars.append(
            Bar(
                series=series,
                interval=interval,
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=decimal_from_int(0),
                available_at=interval.end,
                provenance=Provenance(
                    kind=ProvenanceKind.AGGREGATED,
                    source_ref=f"oanda_tick_csv/{scan.pair}/{scan.month}",
                ),
            )
        )
    return tuple(bars)


@dataclass(frozen=True, slots=True)
class ChainResult:
    """1 分足から順に作った足（開始時刻の集合）と、窓の中で存在すべき足。"""

    built: dict[str, frozenset[int]]
    expected: dict[str, tuple[tuple[int, int], ...]]  # 窓に収まる存在すべき足の区間（秒）
    daily_from_1h: frozenset[int]


def build_chain(
    pair: str,
    bars_1m: tuple[Bar, ...],
    calendar: TradingCalendar,
    timeframes: Mapping[str, TimeframeDefinition],
    window_s: tuple[int, int],
) -> ChainResult:
    """1 分足 → 15 分足 → 1 時間足 → 4 時間足 → 日足を、本基盤の集約（D03 §5）で順に作る。"""
    built: dict[str, frozenset[int]] = {}
    expected: dict[str, tuple[tuple[int, int], ...]] = {}
    window = Interval(start=_utc(window_s[0]), end=_utc(window_s[1]))
    source = bars_1m
    source_def = ONE_MINUTE
    by_tf: dict[str, tuple[Bar, ...]] = {}
    for tf_id in CHAIN:
        target = timeframes[tf_id]
        result_bars = aggregate(
            source,
            source_timeframe_def=source_def,
            target_series=SeriesId(Symbol(pair), target.ref, PriceBasis.BID),
            target_timeframe_def=target,
            calendar=calendar,
        ).bars
        by_tf[tf_id] = result_bars
        built[tf_id] = frozenset(_epoch(b.bar_start) for b in result_bars)
        intervals: list[tuple[int, int]] = []
        for start in calendar.expected_bar_starts(target, window):
            interval = target.expected_interval(calendar, start)
            if interval is None:  # pragma: no cover - expected_bar_starts が除いている
                continue
            s, e = _epoch(interval.start), _epoch(interval.end)
            if window_s[0] <= s and e <= window_s[1]:
                intervals.append((s, e))
        expected[tf_id] = tuple(intervals)
        source, source_def = result_bars, target
    daily_from_1h = frozenset(
        _epoch(b.bar_start)
        for b in aggregate(
            by_tf["1h"],
            source_timeframe_def=timeframes["1h"],
            target_series=SeriesId(Symbol(pair), timeframes["1d_ny17"].ref, PriceBasis.BID),
            target_timeframe_def=timeframes["1d_ny17"],
            calendar=calendar,
        ).bars
    )
    return ChainResult(built=built, expected=expected, daily_from_1h=daily_from_1h)


def complete_runs(flags: Sequence[bool]) -> list[int]:
    """完全な日足の連続の長さの列（欠けで切る）。"""
    runs: list[int] = []
    current = 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def evaluable_after_warmup(runs: Sequence[int], warmup: int = WARMUP_DAILY_BARS) -> int:
    """完全な日足の連続のそれぞれで、ウォームアップ ``warmup`` 本の後に残る日足の合計。

    現行規則（D12 §11.9.6・K-12.5）: run の開始の前に完全な日足が ``warmup`` 本続くこと。
    """
    return sum(max(0, n - warmup) for n in runs)


# --- 定義 (2): K-13 で説明できた年末年始の休場（K-14 と同じ定義ファイル） --------------


def read_exclusion_spans(path: Path | None) -> tuple[tuple[str, int, int], ...]:
    """休場の定義ファイルから ``id``・``start_utc``・``end_utc`` だけを読む（epoch 秒）。

    定義の検証（出典の URL・SHA-256、日本時間との対応、重なり）は K-14 の道具
    ``mt5_gap_aggregate.parse_exclusions`` の役目で、ここでは繰り返さない。
    """
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    spans: list[tuple[str, int, int]] = []
    for entry in payload.get("entries", []):
        lo = datetime.fromisoformat(str(entry["start_utc"]).replace("Z", "+00:00"))
        hi = datetime.fromisoformat(str(entry["end_utc"]).replace("Z", "+00:00"))
        spans.append((str(entry["id"]), int(lo.timestamp()), int(hi.timestamp())))
    return tuple(sorted(spans, key=lambda item: item[1]))


def _excluded(spans: Sequence[tuple[str, int, int]], epoch: int) -> bool:
    return any(lo <= epoch < hi for _, lo, hi in spans)


@dataclass(slots=True)
class FileReport:
    scan: FileScan
    expected: list[int]
    missing: list[int]
    runs: list[tuple[int, int]]
    chain: ChainResult
    daily: list[dict[str, Any]]
    exclusions_in_window: tuple[str, ...]

    def flags(self, rule: int) -> list[bool]:
        key = "complete" if rule == 1 else "complete_rule2"
        return [bool(d[key]) for d in self.daily]


def analyse(
    scan: FileScan,
    calendar: TradingCalendar,
    timeframes: Mapping[str, TimeframeDefinition],
    exclusions: Sequence[tuple[str, int, int]] = (),
) -> FileReport:
    lo_ms, hi_ms = scan.window_ms
    lo_s, hi_s = lo_ms // 1000, hi_ms // 1000
    sessions = session_bounds(calendar, lo_ms, hi_ms)
    expected = expected_minutes(sessions)
    present = scan.minutes
    missing = [m for m in expected if m not in present]
    in_window = [x for x in exclusions if x[1] < hi_s and lo_s < x[2]]
    missing2 = [m for m in missing if not _excluded(in_window, m)]
    bars = minute_bars(scan, expected)
    chain = build_chain(scan.pair, bars, calendar, timeframes, (lo_s, hi_s))
    daily: list[dict[str, Any]] = []
    for s, e in chain.expected["1d_ny17"]:
        gaps = missing[bisect.bisect_left(missing, s) : bisect.bisect_left(missing, e)]
        gaps2 = missing2[bisect.bisect_left(missing2, s) : bisect.bisect_left(missing2, e)]
        ny17 = sum(
            1 for g in gaps if datetime.fromtimestamp(g, UTC).astimezone(NEW_YORK).hour == 17
        )
        label = (datetime.fromtimestamp(e, UTC).astimezone(NEW_YORK) - timedelta(seconds=1)).date()
        built = s in chain.built["1d_ny17"]
        if built != (not gaps):  # pragma: no cover - 集約の規則（D03 §5.2）と分の数えが食い違う
            raise CheckError(f"{scan.pair} {label}: 集約の日足と空白の分の数えが合わない")
        daily.append(
            {
                "pair": scan.pair,
                "month": scan.month,
                "trading_day": label.isoformat(),
                "start_utc": iso_min(s),
                "end_utc": iso_min(e),
                "expected_minutes": bisect.bisect_left(expected, e)
                - bisect.bisect_left(expected, s),
                "missing_minutes": len(gaps),
                "missing_in_ny17_hour": ny17,
                "complete": built,
                "missing_minutes_rule2": len(gaps2),
                "complete_rule2": not gaps2,
            }
        )
    return FileReport(
        scan=scan,
        expected=expected,
        missing=missing,
        runs=gap_runs(missing),
        chain=chain,
        daily=daily,
        exclusions_in_window=tuple(x[0] for x in in_window),
    )


# --- API の 1 分足との並べ比較（時刻だけ） -------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiWindow:
    rid: str
    pair: str
    kind: str
    window_api: tuple[int, int]
    bar_times_api: tuple[int, ...]
    source: str


def read_api_windows(probe_root: Path) -> list[ApiWindow]:
    """``summary_ts-*.json`` の要求のうち、時刻（``bar_times``）のある週末・火曜の窓。

    読むのは ``rid``・``pair``・``kind``・要求の区間・``bar_times`` だけ（価格の項目は読まない）。
    同じ ``rid`` が何度もあれば、足の返った最後の回を使う（空の回は通信の失敗の記録）。
    """
    chosen: dict[str, ApiWindow] = {}
    for path in sorted(probe_root.glob("*/ts-*/summary_ts-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("requests", []):
            if item.get("kind") not in ("weekend", "tuesday"):
                continue
            bars = item.get("bar_times")
            if not isinstance(bars, list) or not bars:
                continue
            frm = datetime.fromisoformat(str(item["requested_from_api_time"]))
            to = datetime.fromisoformat(str(item["requested_to_api_time"]))
            chosen[str(item["rid"])] = ApiWindow(
                rid=str(item["rid"]),
                pair=str(item["pair"]),
                kind=str(item["kind"]),
                window_api=(int(frm.timestamp()), int(to.timestamp())),
                bar_times_api=tuple(sorted(int(v) for v in bars)),
                source=str(path.relative_to(probe_root)),
            )
    return list(chosen.values())


def _shift_hours(pair: str, api_epoch: int, role: str) -> tuple[int, str, bool]:
    """API の時刻 1 つを表 版 1 で UTC に直すずれ（時間）、その規則の印、候補の有無。

    行は UTC の日付で決まるので、API の時刻 − 2 時間の日付で引く（窓の端と取引時間の中では、
    2 時間と 3 時間で UTC の日付が変わる時刻（API の時刻 02:00〜03:00）に行の境目は無い）。
    """
    day = (datetime.fromtimestamp(api_epoch, UTC) - timedelta(hours=2)).date()
    rule = time_rule(pair, day)
    conv = rule.candidates[0] if role == "候補" and rule.candidates else rule.standard
    return conv.hours, rule.mark(), bool(rule.candidates)


def compare_api(
    window: ApiWindow,
    report: FileReport,
    calendar: TradingCalendar,
    role: str,
) -> dict[str, Any] | None:
    """1 つの API の窓で、取引時間の中の分の空白の有無・位置を API と CSV で並べる。

    CSV の値で API を埋めない。返すのは件数と空白の位置（時刻）だけ。窓が CSV の月の外に
    掛かるとき、候補の役で候補の無い窓のときは ``None``。
    """
    lo_shift, lo_mark, lo_cand = _shift_hours(window.pair, window.window_api[0], role)
    hi_shift, hi_mark, hi_cand = _shift_hours(window.pair, window.window_api[1] - MINUTE, role)
    if role == "候補" and not (lo_cand or hi_cand):
        return None
    lo = window.window_api[0] - lo_shift * 3600
    hi = window.window_api[1] - hi_shift * 3600
    file_lo, file_hi = (v // 1000 for v in report.scan.window_ms)
    if lo < file_lo or hi > file_hi:
        return None
    api_utc = {t - _shift_hours(window.pair, t, role)[0] * 3600 for t in window.bar_times_api}
    expected = expected_minutes(session_bounds(calendar, lo * 1000, hi * 1000))
    exp_set = set(expected)
    api_missing = {m for m in expected if m not in api_utc}
    csv_missing = {m for m in expected if m not in report.scan.minutes}
    api_only = sorted(api_missing - csv_missing)
    csv_only = sorted(csv_missing - api_missing)
    return {
        "rid": window.rid,
        "pair": window.pair,
        "kind": window.kind,
        "month": report.scan.month,
        "role": role,
        "rule_from": lo_mark,
        "rule_to": hi_mark,
        "shift_hours": f"{lo_shift}/{hi_shift}",
        "window_utc_from": iso_min(lo),
        "window_utc_to": iso_min(hi),
        "expected_minutes": len(expected),
        "api_missing": len(api_missing),
        "csv_missing": len(csv_missing),
        "both_missing": len(api_missing & csv_missing),
        "api_only_missing": len(api_only),
        "csv_only_missing": len(csv_only),
        "api_bars_outside_session": sum(1 for t in api_utc if lo <= t < hi and t not in exp_set),
        "api_only_runs": " ".join(
            f"{ny_min(s)}+{(e - s) // MINUTE}" for s, e in gap_runs(api_only)
        ),
        "csv_only_runs": " ".join(
            f"{ny_min(s)}+{(e - s) // MINUTE}" for s, e in gap_runs(csv_only)
        ),
        "source": window.source,
    }


# --- 出力（統計値と時刻だけ） --------------------------------------------------------


def _q(counter: Mapping[int, int], q: float) -> str:
    value = _quantile(counter, q)
    return "" if value is None else str(value)


def _ny_hour(epoch: int) -> int:
    return datetime.fromtimestamp(epoch, UTC).astimezone(NEW_YORK).hour


def file_row(report: FileReport) -> dict[str, Any]:
    scan = report.scan
    chain = report.chain
    runs1 = complete_runs(report.flags(1))
    runs2 = complete_runs(report.flags(2))
    daily_starts = frozenset(s for s, _ in chain.expected["1d_ny17"])
    total_missing = len(report.missing)
    row: dict[str, Any] = {
        "pair": scan.pair,
        "month": scan.month,
        "sha256": scan.sha256,
        "bytes": scan.size_bytes,
        "rows": scan.rows,
        "header_ok": scan.header_ok,
        "t1_format_violations": scan.format_violations,
        "t1_backward": scan.backward,
        "t1_first_utc": iso_ms(scan.first_ms) if scan.first_ms is not None else "",
        "t1_last_utc": iso_ms(scan.last_ms) if scan.last_ms is not None else "",
        "t1_jst_month_from_utc": iso_ms(scan.window_ms[0]),
        "t1_jst_month_to_utc": iso_ms(scan.window_ms[1]),
        "t1_rows_outside_jst_month": scan.outside_month,
        "t2_empty_or_non_numeric": scan.empty_or_non_numeric_price,
        "t2_last_or_volume_not_empty": scan.last_or_volume_not_empty,
        "t2_nonpositive": scan.nonpositive_price,
        "t2_ask_lt_bid": scan.ask_lt_bid,
        "t2_ask_eq_bid": scan.ask_eq_bid,
        "t2_bid_digits": " ".join(f"{k}:{v}" for k, v in sorted(scan.bid_digits.items())),
        "t2_ask_digits": " ".join(f"{k}:{v}" for k, v in sorted(scan.ask_digits.items())),
        "t2_spread_points_min": min(scan.spread_points, default=""),
        "t2_spread_points_p50": _q(scan.spread_points, 0.5),
        "t2_spread_points_p95": _q(scan.spread_points, 0.95),
        "t2_spread_points_p99": _q(scan.spread_points, 0.99),
        "t2_spread_points_max": max(scan.spread_points, default=""),
        f"t2_jumps_ge_{JUMP_PIPS}pips": sum(v for k, v in scan.jump_pips.items() if k >= JUMP_PIPS),
        "t2_jump_pips_max": max(scan.jump_pips, default=""),
        "t3_same_time_consecutive": scan.same_time_consecutive,
        "t3_same_content_consecutive": scan.same_content_consecutive,
        "t4_ticks_in_session": scan.ticks_in_session,
        "t4_interval_ms_p50": _q(scan.interval_ms, 0.5),
        "t4_interval_ms_p90": _q(scan.interval_ms, 0.9),
        "t4_interval_ms_p99": _q(scan.interval_ms, 0.99),
        "t4_interval_ms_p999": _q(scan.interval_ms, 0.999),
        "t4_interval_ms_max": max(scan.interval_ms, default=""),
    }
    for label, threshold in INTERVAL_THRESHOLDS_MS:
        row[f"t4_intervals_{label}"] = sum(v for k, v in scan.interval_ms.items() if k >= threshold)
    row.update(
        {
            "t4_expected_minutes": len(report.expected),
            "t4_missing_minutes": total_missing,
            "t4_missing_pct": f"{100 * total_missing / len(report.expected):.3f}"
            if report.expected
            else "",
            "t4_missing_in_ny17_hour": sum(1 for g in report.missing if _ny_hour(g) == 17),
            "t4_gap_runs": len(report.runs),
            "t4_longest_gap_minutes": max(((e - s) // MINUTE for s, e in report.runs), default=0),
        }
    )
    bins = Counter(length_bin((e - s) // MINUTE) for s, e in report.runs)
    for label, _, _ in LENGTH_BINS:
        row[f"t4_len_{label}"] = bins.get(label, 0)
    row.update(
        {
            "t5_ticks_outside_session": scan.outside_session,
            "t5_outside_session_ny": " ".join(
                f"{k}:{v}" for k, v in sorted(scan.outside_session_ny_dow_hour.items())
            ),
            "t5_k13_exclusions_in_window": " ".join(report.exclusions_in_window) or "なし",
        }
    )
    for tf_id in CHAIN:
        exp = chain.expected[tf_id]
        row[f"bars_{tf_id}_complete"] = sum(1 for s, _ in exp if s in chain.built[tf_id])
        row[f"bars_{tf_id}_expected"] = len(exp)
    row.update(
        {
            "daily_from_1h_equal": (daily_starts & chain.daily_from_1h)
            == (daily_starts & chain.built["1d_ny17"]),
            "daily_incomplete_rule1": sum(1 for f in report.flags(1) if not f),
            "daily_longest_complete_run_rule1": max(runs1, default=0),
            "daily_evaluable_after_warmup_rule1": evaluable_after_warmup(runs1),
            "daily_incomplete_rule2": sum(1 for f in report.flags(2) if not f),
            "daily_longest_complete_run_rule2": max(runs2, default=0),
            "daily_evaluable_after_warmup_rule2": evaluable_after_warmup(runs2),
            "daily_incomplete_only_ny17_hour": sum(
                1
                for d in report.daily
                if d["missing_minutes"] and d["missing_minutes"] == d["missing_in_ny17_hour"]
            ),
            "daily_incomplete_le5min": sum(
                1 for d in report.daily if 0 < d["missing_minutes"] <= 5
            ),
        }
    )
    return row


def gap_rows(report: FileReport) -> list[dict[str, Any]]:
    return [
        {
            "pair": report.scan.pair,
            "month": report.scan.month,
            "start_utc": iso_min(s),
            "end_utc": iso_min(e),
            "start_ny": ny_min(s),
            "end_ny": ny_min(e),
            "minutes": (e - s) // MINUTE,
        }
        for s, e in report.runs
    ]


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
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
    rows: Sequence[Mapping[str, Any]],
    compare: Sequence[Mapping[str, Any]],
    inputs: Mapping[str, Any],
) -> str:
    lines = [
        "# 配布 tick CSV の主系列候補としての検査（tools/survey/oanda_csv_series_check.py）",
        "",
        "- 統計値と時刻だけ（価格の水準は書かない。スプレッドは point、跳びは pip）。",
        f"- カレンダー: `{inputs['calendar']}`（{inputs['calendar_sha256']}）。"
        f"時間足: {inputs['timeframes_sha256']}。休場の定義: {inputs.get('exclusions') or 'なし'}",
        f"- ウォームアップ: 日足 {WARMUP_DAILY_BARS} 本。"
        "定義 (1) 現行規則（空白を 1 分でも含む足は欠け）、(2) K-13 の年末年始を外す",
        "",
        "## 通貨 × 月",
        "",
        "| 通貨 | 月 | 行 | 書式違反 | 逆行 | 月の外 | 同時刻連続 | 同内容連続 | ask<bid "
        "| ask=bid | 桁 bid | spread p50/p99/max | 跳び≥20pip | 間隔 p50/p99/max (ms) "
        "| 空白の分 / 取引時間の分 | 空白の件数・最長 | NY17時台 | セッション外の tick "
        "| 15m | 1h | 4h | 日足 完全/全 | 最長連続 (1)/(2) | ウォームアップ後 (1)/(2) "
        "| 年末年始 |",
        "|" + "---|" * 25,
    ]
    for r in rows:
        spread = f"{r['t2_spread_points_p50']}/{r['t2_spread_points_p99']}"
        lines.append(
            f"| {r['pair']} | {r['month']} | {r['rows']:,} | {r['t1_format_violations']} | "
            f"{r['t1_backward']} | {r['t1_rows_outside_jst_month']} | "
            f"{r['t3_same_time_consecutive']:,} | {r['t3_same_content_consecutive']:,} | "
            f"{r['t2_ask_lt_bid']} | {r['t2_ask_eq_bid']} | {r['t2_bid_digits']} | "
            f"{spread}/{r['t2_spread_points_max']} | {r[f't2_jumps_ge_{JUMP_PIPS}pips']} | "
            f"{r['t4_interval_ms_p50']}/{r['t4_interval_ms_p99']}/{r['t4_interval_ms_max']} | "
            f"{r['t4_missing_minutes']} / {r['t4_expected_minutes']}（{r['t4_missing_pct']}%） | "
            f"{r['t4_gap_runs']}・{r['t4_longest_gap_minutes']} | {r['t4_missing_in_ny17_hour']} | "
            f"{r['t5_ticks_outside_session']} | "
            f"{r['bars_15m_complete']}/{r['bars_15m_expected']} | "
            f"{r['bars_1h_complete']}/{r['bars_1h_expected']} | "
            f"{r['bars_4h_ny17_complete']}/{r['bars_4h_ny17_expected']} | "
            f"{r['bars_1d_ny17_complete']}/{r['bars_1d_ny17_expected']} | "
            f"{r['daily_longest_complete_run_rule1']}/{r['daily_longest_complete_run_rule2']} | "
            f"{r['daily_evaluable_after_warmup_rule1']}/"
            f"{r['daily_evaluable_after_warmup_rule2']} | "
            f"{r['t5_k13_exclusions_in_window']} |"
        )
    lines += [
        "",
        "## API の 1 分足との並べ比較（同じ窓・同じ通貨。時刻だけ。CSV で API を埋めない）",
        "",
        "| 要求 | 規則 | ずれ | 取引時間の分 | API 空白 | CSV 空白 | 両方 | API だけ | CSV だけ "
        "| API だけの位置（NY+分） | CSV だけの位置 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in compare:
        lines.append(
            f"| {c['rid']} | {c['role']} | {c['shift_hours']} | {c['expected_minutes']} | "
            f"{c['api_missing']} | {c['csv_missing']} | {c['both_missing']} | "
            f"{c['api_only_missing']} | {c['csv_only_missing']} | {c['api_only_runs'] or '—'} | "
            f"{c['csv_only_runs'] or '—'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--csv-root", type=Path, required=True)
    parser.add_argument("--probe-root", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, default=CALENDAR_DEFAULT)
    parser.add_argument("--timeframes", type=Path, default=TIMEFRAMES_DEFAULT)
    parser.add_argument("--exclusions", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=None, help="ファイル名の一部で絞る（試験用）")
    args = parser.parse_args(argv)

    calendar = load_calendar(args.calendar)
    timeframes = load_timeframes(args.timeframes)
    exclusions = read_exclusion_spans(args.exclusions)
    paths = sorted(args.csv_root.glob("*/ticks_*-oj5k_*.csv"))
    if args.only:
        paths = [p for p in paths if any(key in p.name for key in args.only)]
    windows = read_api_windows(args.probe_root)
    rows: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    compare: list[dict[str, Any]] = []
    for path in paths:
        scan = scan_file(path, calendar)
        report = analyse(scan, calendar, timeframes, exclusions)
        rows.append(file_row(report))
        daily.extend(report.daily)
        gaps.extend(gap_rows(report))
        for window in windows:
            if window.pair != scan.pair:
                continue
            for role in ("標準", "候補"):
                try:
                    item = compare_api(window, report, calendar, role)
                except AggregateError:  # 研究期間の外の日（表 版 1 に行が無い）
                    item = None
                if item is not None:
                    compare.append(item)
        print(f"{scan.pair} {scan.month} rows={scan.rows} gaps={len(report.missing)}", flush=True)
        scan.minutes.clear()

    out: Path = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "file_metrics.csv", rows)
    write_csv(out / "daily_bars.csv", daily)
    write_csv(out / "gap_runs.csv", gaps)
    write_csv(out / "api_compare.csv", compare)
    inputs: dict[str, Any] = {
        "calendar": str(args.calendar),
        "calendar_sha256": sha256_of(args.calendar),
        "timeframes_sha256": sha256_of(args.timeframes),
        "exclusions": str(args.exclusions) if args.exclusions else None,
        "exclusions_sha256": sha256_of(args.exclusions) if args.exclusions else None,
        "tool_sha256": sha256_of(Path(__file__)),
        "csv_files": {f"{r['pair']}_{r['month']}": r["sha256"] for r in rows},
        "api_windows": len(windows),
    }
    (out / "report.md").write_text(render_report(rows, compare, inputs), encoding="utf-8")
    (out / "inputs.json").write_text(
        json.dumps(inputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"files={len(rows)} compare={len(compare)} out={out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
