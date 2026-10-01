"""研究履歴区分の欠落一覧（事実の記録）を snapshot の manifest から作り直す。

記録文書は ``docs/data/research_history_gaps.md``。本スクリプトはその数字と CSV を再現する。

- 一次資料: ``<snapshot_root>/<snapshot_id>/manifest.json`` の ``resolved_classifications``
  （D03 §3.7。完全性検査の警告を人間が休場 ``CLOSURE`` / データ欠損 ``DATA_GAP`` に確定した
  分類）。独自の「存在すべき足」の定義は作らない。
- 対象: 10 銘柄 × {15m@v1, 1h@v1} の 20 系列（bid）。``RESEARCH_HISTORY`` の partition の
  範囲に足の終端（bar_end）が入る記録だけを数える。4 時間足・日足は 1 時間足からの導出系列
  （D03 §5.1）なので集計しない。
- 隣接する（前の終端 == 次の始端）``DATA_GAP`` の記録を結合して 1 つの「欠落区間」にする。
- 原因の帰属は時刻のパターンによる機械的な分類で、決定ではない（文書の第 4 節）。
- 原データ（``data/raw/market/<SYMBOL>_<tf>_merged.csv``。git 管理外）があれば、各欠落区間に
  足の開始時刻が入る行の数を数える（0 なら原データにその足が無い）。数える前に、各ファイルの
  sha256 と行数が manifest の ``sources``（受入れ時の原データの記録。D03 §3.7）と一致することを
  確かめ、1 つでも欠けるか一致しなければ何も書かずに失敗する。補充分を含む snapshot（D03
  §14.11）では、同じ系列の補充した足のファイル（``data/raw/market/refill/<refill_id>/…``）の行も
  同じ検査の後に数える。

戦略の成績（指標・レポート）は読まない。標準ライブラリだけを使う。
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_SNAPSHOT_ID = "a498b8cf4f90aeca1fa8af7a1e59112db197413eaa8c0a0d318eb2368a770cb3"

SYMBOLS: tuple[str, ...] = (
    "AUDJPY",
    "AUDUSD",
    "EURGBP",
    "EURJPY",
    "EURUSD",
    "GBPJPY",
    "GBPUSD",
    "USDCAD",
    "USDCHF",
    "USDJPY",
)
TIMEFRAMES: tuple[str, ...] = ("15m@v1", "1h@v1")
RAW_FILE_SUFFIX: dict[str, str] = {"15m@v1": "15m", "1h@v1": "1h"}

NEW_YORK = ZoneInfo("America/New_York")

#: 帰属の語彙（CSV の値）。日本語の意味は記録文書の第 4 節。
SOURCE_DATA = "SOURCE_DATA"
SOURCE_DATA_KNOWN = "SOURCE_DATA_KNOWN"
CALENDAR = "CALENDAR"
CALENDAR_UNDECLARED_OR_PARTLY_SOURCE = "CALENDAR_UNDECLARED_OR_PARTLY_SOURCE"
CALENDAR_ADJACENT_OR_SOURCE = "CALENDAR_ADJACENT_OR_SOURCE"
UNKNOWN = "UNKNOWN"

ATTRIBUTION_ORDER: tuple[str, ...] = (
    SOURCE_DATA,
    SOURCE_DATA_KNOWN,
    CALENDAR,
    CALENDAR_UNDECLARED_OR_PARTLY_SOURCE,
    CALENDAR_ADJACENT_OR_SOURCE,
    UNKNOWN,
)

INTERVAL_FIELDS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "start_utc",
    "end_utc",
    "duration_hours",
    "year_of_bar_end",
    "attribution",
    "confidence",
    "rationale",
    "in_all10_overlap",
    "matches_other_timeframe_gap",
    "raw_rows_in_interval",
    "raw_prev_row_utc",
    "raw_next_row_utc",
)
OVERLAP_FIELDS: tuple[str, ...] = ("timeframe", "start_utc", "end_utc", "n_symbols", "symbols")

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class GapInterval:
    """結合後の欠落区間 1 件。"""

    symbol: str
    timeframe: str
    start: datetime
    end: datetime

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def fmt_utc(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def merge_adjacent(intervals: list[Interval]) -> list[Interval]:
    """前の終端と次の始端が一致する区間をつなぐ（重なりは扱わない。記録は足 1 本ずつ）。"""
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start == merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def contiguous_windows(gaps: list[Interval], lo: datetime, hi: datetime) -> list[Interval]:
    """[lo, hi) から欠落区間を除いた、欠落の無い連続区間。"""
    cursor = lo
    windows: list[Interval] = []
    for start, end in gaps:
        s, e = max(start, lo), min(end, hi)
        if s >= hi or e <= lo:
            continue
        if s > cursor:
            windows.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < hi:
        windows.append((cursor, hi))
    return [(s, e) for s, e in windows if e > s]


def research_history_bounds(
    manifest: dict[str, Any], symbol: str, timeframe: str
) -> Interval | None:
    spans = [
        (parse_utc(p["interval"]["start"]), parse_utc(p["interval"]["end"]))
        for p in manifest["partitions"]
        if p["partition_id"]["access_class"] == "RESEARCH_HISTORY"
        and p["partition_id"]["series"]["symbol"] == symbol
        and p["partition_id"]["series"]["timeframe"] == timeframe
        and p["partition_id"]["series"]["basis"] == "bid"
    ]
    if not spans:
        return None
    return min(s for s, _ in spans), max(e for _, e in spans)


def collect_gaps(
    manifest: dict[str, Any],
) -> tuple[dict[tuple[str, str], list[Interval]], dict[tuple[str, str], Interval]]:
    """系列ごとに、研究履歴に入る DATA_GAP の記録を結合した区間と、研究履歴の範囲を返す。"""
    raw: dict[tuple[str, str], list[Interval]] = defaultdict(list)
    wanted = {(s, t) for s in SYMBOLS for t in TIMEFRAMES}
    for record in manifest["resolved_classifications"]:
        sid = record["series_id"]
        key = (sid["symbol"], sid["timeframe"])
        if sid["basis"] != "bid" or key not in wanted or record["outcome"] != "DATA_GAP":
            continue
        raw[key].append(
            (parse_utc(record["interval"]["start"]), parse_utc(record["interval"]["end"]))
        )

    merged: dict[tuple[str, str], list[Interval]] = {}
    bounds: dict[tuple[str, str], Interval] = {}
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            key = (symbol, tf)
            span = research_history_bounds(manifest, symbol, tf)
            if span is None:
                merged[key] = []
                continue
            bounds[key] = span
            lo, hi = span
            in_rh = [(s, e) for s, e in raw.get(key, []) if lo < e <= hi]
            merged[key] = merge_adjacent(in_rh)
    return merged, bounds


def sweep_overlap(merged: dict[tuple[str, str], list[Interval]]) -> list[dict[str, Any]]:
    """同じ時間足の銘柄横断で、欠落が重なる最小の区間ごとに該当銘柄を並べる。"""
    rows: list[dict[str, Any]] = []
    for tf in TIMEFRAMES:
        at_point: dict[datetime, list[tuple[int, str]]] = defaultdict(list)
        for symbol in SYMBOLS:
            for s, e in merged[(symbol, tf)]:
                at_point[s].append((1, symbol))
                at_point[e].append((-1, symbol))
        active: set[str] = set()
        prev: datetime | None = None
        for t in sorted(at_point):
            if prev is not None and active:
                rows.append(
                    {
                        "timeframe": tf,
                        "start_utc": fmt_utc(prev),
                        "end_utc": fmt_utc(t),
                        "n_symbols": len(active),
                        "symbols": "|".join(sorted(active)),
                    }
                )
            for delta, symbol in sorted(at_point[t], key=lambda x: -x[0]):
                if delta == 1:
                    active.add(symbol)
                else:
                    active.discard(symbol)
            prev = t
    return rows


def classify(gap: GapInterval) -> tuple[str, str, str]:
    """時刻のパターンによる原因の帰属（帰属・確度・根拠）。決定ではない。"""
    s, e, hours = gap.start, gap.end, gap.hours
    s_ny = s.astimezone(NEW_YORK)
    day = s.date().isoformat()
    if day == "2020-11-30" and hours >= 15:
        return (
            SOURCE_DATA_KNOWN,
            "HIGH",
            "2020-11-30 単発の大規模欠損。manifest の分類の注記に「既知の休場と一致しない」と明記",
        )
    if day == "2021-05-31" and hours >= 15:
        return (
            SOURCE_DATA_KNOWN,
            "HIGH",
            "2021-05-31 単発の大規模欠損。manifest の分類の注記に「既知の休場と一致しない」と明記",
        )
    if (s.year, s.month, s.day) == (2017, 1, 1) and (e.year, e.month, e.day) == (2017, 1, 2):
        return (
            CALENDAR,
            "LOW",
            "2017 年元日（日曜）の週の開場（日 17:00 NY）から月 02:00 NY までの 9 時間。"
            "fx_ny17 v2 の休場宣言は 2017-12-25 から始まり、2017 年の元日の前後は未宣言。"
            "2018 年以降の元日の宣言（前日 17:00〜当日 17:00 NY の 24 時間）とは"
            "長さも位置も一致しない",
        )
    if (s.year, s.month, s.day) == (2016, 12, 26):
        return (
            CALENDAR_UNDECLARED_OR_PARTLY_SOURCE,
            "LOW",
            "2016 年クリスマス翌日の大きな欠落。2016 年は休場宣言が無い年",
        )
    if s_ny.weekday() == 4 and s_ny.hour == 16 and hours <= 1.5:
        return (
            SOURCE_DATA,
            "MEDIUM",
            "週の閉場（金 17:00 NY）の 1 時間前。開場中のはずの時間帯",
        )
    if s_ny.weekday() == 6 and 17 <= s_ny.hour < 20 and hours <= 3.5:
        return (
            SOURCE_DATA,
            "MEDIUM",
            "週の開場（日 17:00 NY）から 3 時間以内。開場中のはずの時間帯",
        )
    if s.month == 12 and 23 <= s.day <= 26 and hours <= 2:
        return (
            CALENDAR_ADJACENT_OR_SOURCE,
            "LOW",
            "クリスマスの短縮宣言に時間的に隣接する短い欠落。宣言区間そのものではない",
        )
    if hours <= 4:
        return (
            SOURCE_DATA,
            "MEDIUM",
            "上のパターンに当たらない 4 時間以下の欠落。休場宣言に該当なし",
        )
    return (
        UNKNOWN,
        "LOW",
        "4 時間を超え、上のパターンに当たらない欠落。個別の確認が要る",
    )


class RawSourceMismatch(RuntimeError):
    """原 CSV が snapshot の受入れ時の記録（manifest の ``sources``）と一致しない。"""


#: manifest の ``sources`` の ``path`` が原データの基点からの相対になる接頭辞（D03 §3.7）。
RAW_ROOT_PREFIX = "data/raw/market/"


def source_records(manifest: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """manifest の ``sources`` を (銘柄, 時間足) で引けるようにする。

    補充分を含む snapshot では同じ系列に原ファイルと補充した足のファイルが並ぶ（D03 §14.11）。
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for src in manifest["sources"]:
        grouped[(src["symbol"], src["timeframe"])].append(src)
    return dict(grouped)


def _source_path(raw_dir: Path, symbol: str, timeframe: str, record: dict[str, Any]) -> Path:
    path = record.get("path")
    if isinstance(path, str) and path.startswith(RAW_ROOT_PREFIX):
        return raw_dir / path[len(RAW_ROOT_PREFIX) :]
    return raw_dir / f"{symbol}_{RAW_FILE_SUFFIX[timeframe]}_merged.csv"


def load_raw_timestamps(
    raw_dir: Path,
    symbol: str,
    timeframe: str,
    expected: dict[str, Any] | list[dict[str, Any]] | None,
) -> list[str]:
    """原 CSV の先頭列（足の開始時刻、``YYYY-MM-DD HH:MM:SS+00:00``）を昇順で返す。

    ファイルの sha256 と行数（見出しを除く）が manifest の記録と一致しなければ
    ``RawSourceMismatch`` を送出する。同じ系列の記録が複数（原ファイルと補充した足のファイル）
    あれば、すべてを検査して合わせる。
    """
    if not expected:
        raise RawSourceMismatch(f"manifest の sources に記録が無い: {symbol} {timeframe}")
    records = expected if isinstance(expected, list) else [expected]
    stamps: list[str] = []
    for record in records:
        path = _source_path(raw_dir, symbol, timeframe, record)
        if not path.is_file():
            raise RawSourceMismatch(f"原 CSV が無い: {path}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != record["sha256"]:
            raise RawSourceMismatch(
                f"sha256 が manifest と一致しない: {path}（{digest} != {record['sha256']}）"
            )
        reader = csv.reader(data.decode("utf-8").splitlines())
        next(reader, None)
        rows = [row[0] for row in reader if row]
        if len(rows) != record["rows"]:
            raise RawSourceMismatch(
                f"行数が manifest と一致しない: {path}（{len(rows)} != {record['rows']}）"
            )
        stamps.extend(rows)
    stamps.sort()
    return stamps


def raw_rows_between(stamps: list[str], start: datetime, end: datetime) -> int:
    lo = start.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00:00")
    hi = end.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00:00")
    return bisect.bisect_left(stamps, hi) - bisect.bisect_left(stamps, lo)


def _stamp_to_utc(stamp: str) -> str:
    return fmt_utc(datetime.fromisoformat(stamp))


def raw_neighbors(stamps: list[str], start: datetime, end: datetime) -> tuple[str, str]:
    """区間の直前（始端より前の最後）と直後（終端以後の最初）の原 CSV の行の時刻。無ければ空。"""
    lo = start.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00:00")
    hi = end.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00:00")
    i = bisect.bisect_left(stamps, lo)
    j = bisect.bisect_left(stamps, hi)
    prev = _stamp_to_utc(stamps[i - 1]) if i > 0 else ""
    nxt = _stamp_to_utc(stamps[j]) if j < len(stamps) else ""
    return prev, nxt


def build_interval_rows(
    merged: dict[tuple[str, str], list[Interval]],
    overlap_rows: list[dict[str, Any]],
    raw_dir: Path | None,
    sources: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    all10: dict[str, list[Interval]] = defaultdict(list)
    for r in overlap_rows:
        if r["n_symbols"] == len(SYMBOLS):
            all10[r["timeframe"]].append((parse_utc(r["start_utc"]), parse_utc(r["end_utc"])))

    rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            other = TIMEFRAMES[1] if tf == TIMEFRAMES[0] else TIMEFRAMES[0]
            stamps = (
                load_raw_timestamps(raw_dir, symbol, tf, sources.get((symbol, tf)))
                if raw_dir is not None
                else None
            )
            for s, e in merged[(symbol, tf)]:
                gap = GapInterval(symbol, tf, s, e)
                attribution, confidence, rationale = classify(gap)
                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": tf,
                        "start_utc": fmt_utc(s),
                        "end_utc": fmt_utc(e),
                        "duration_hours": round(gap.hours, 2),
                        "year_of_bar_end": e.year,
                        "attribution": attribution,
                        "confidence": confidence,
                        "rationale": rationale,
                        "in_all10_overlap": any(ws <= s and e <= we for ws, we in all10[tf]),
                        "matches_other_timeframe_gap": any(
                            s < oe and os_ < e for os_, oe in merged[(symbol, other)]
                        ),
                        "raw_rows_in_interval": ""
                        if stamps is None
                        else raw_rows_between(stamps, s, e),
                        "raw_prev_row_utc": ""
                        if stamps is None
                        else raw_neighbors(stamps, s, e)[0],
                        "raw_next_row_utc": ""
                        if stamps is None
                        else raw_neighbors(stamps, s, e)[1],
                    }
                )
    return rows


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def print_summary(
    rows: list[dict[str, Any]],
    overlap_rows: list[dict[str, Any]],
    merged: dict[tuple[str, str], list[Interval]],
    bounds: dict[tuple[str, str], Interval],
) -> None:
    print("## 系列別（件数 / 合計時間 / 最長）")
    for symbol in SYMBOLS:
        parts = []
        for tf in TIMEFRAMES:
            gaps = merged[(symbol, tf)]
            hours = [(e - s).total_seconds() / 3600 for s, e in gaps]
            parts.append(f"{tf} {len(gaps)} / {sum(hours):.2f}h / {max(hours, default=0):.2f}h")
        print(f"{symbol}: " + " | ".join(parts))

    print("\n## 年別（bar_end の年。時間足ごとに 10 銘柄を合算）")
    by_year: dict[tuple[str, int], list[float]] = defaultdict(list)
    for r in rows:
        by_year[(r["timeframe"], r["year_of_bar_end"])].append(r["duration_hours"])
    for tf in TIMEFRAMES:
        for year in range(2016, 2024):
            hs = by_year.get((tf, year), [])
            print(f"{tf} {year}: {len(hs)} / {sum(hs):.2f}h")

    print("\n## 銘柄横断の重なり（全10銘柄 / 2〜9銘柄 / 1銘柄）")
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in overlap_rows:
        n = r["n_symbols"]
        bucket = "all10" if n == len(SYMBOLS) else ("multi" if n >= 2 else "single")
        span_hours = (parse_utc(r["end_utc"]) - parse_utc(r["start_utc"])).total_seconds() / 3600
        buckets[(r["timeframe"], bucket)].append(span_hours)
    for tf in TIMEFRAMES:
        for bucket in ("all10", "multi", "single"):
            hs = buckets.get((tf, bucket), [])
            print(f"{tf} {bucket}: {len(hs)} / {sum(hs):.2f}h")

    print("\n## 帰属（全区間）")
    by_attr: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_attr[r["attribution"]].append(r["duration_hours"])
    for attr in ATTRIBUTION_ORDER:
        hs = by_attr.get(attr, [])
        print(f"{attr}: {len(hs)} / {sum(hs):.2f}h")
    print(f"合計: {len(rows)}")
    print(f"全10銘柄の重なりに含まれる区間: {sum(1 for r in rows if r['in_all10_overlap'])}")
    print(
        "他の時間足の欠落と時間が重ならない区間: "
        f"{sum(1 for r in rows if not r['matches_other_timeframe_gap'])}"
    )
    checked = [r for r in rows if r["raw_rows_in_interval"] != ""]
    if checked:
        present = [r for r in checked if r["raw_rows_in_interval"] > 0]
        print(f"原データで確認した区間: {len(checked)}（行が 1 本以上ある区間: {len(present)}）")
        for r in present:
            print(
                f"  {r['symbol']} {r['timeframe']} {r['start_utc']}..{r['end_utc']}: "
                f"{r['raw_rows_in_interval']} 行"
            )
    else:
        print("原データは読まなかった（--raw-dir が無い）")

    print("\n## 欠落の無い連続区間")
    usdjpy = ("USDJPY", "15m@v1")
    if usdjpy in bounds:
        lo, hi = bounds[usdjpy]
        windows = sorted(
            contiguous_windows(merged[usdjpy], lo, hi), key=lambda w: w[1] - w[0], reverse=True
        )
        print(f"USDJPY 15m 単独（全 {len(windows)} 区間）の上位 5:")
        for i, (s, e) in enumerate(windows[:5], start=1):
            print(f"  {i}. {fmt_utc(s)} .. {fmt_utc(e)} {(e - s).total_seconds() / 86400:.2f} 日")
    lo_all = max(lo for lo, _ in bounds.values())
    hi_all = min(hi for _, hi in bounds.values())
    union = merge_adjacent([g for gaps in merged.values() for g in gaps])
    windows = sorted(
        contiguous_windows(union, lo_all, hi_all), key=lambda w: w[1] - w[0], reverse=True
    )
    print(f"20 系列共通（範囲 {fmt_utc(lo_all)} .. {fmt_utc(hi_all)}）の上位 5:")
    for i, (s, e) in enumerate(windows[:5], start=1):
        print(f"  {i}. {fmt_utc(s)} .. {fmt_utc(e)} {(e - s).total_seconds() / 86400:.2f} 日")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--snapshot-root", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw/market"),
        help=(
            "原 CSV の置き場。ディレクトリが無ければ原データでの確認列を空にする。"
            "あれば 20 ファイルすべてが manifest の sources と一致することを要求する"
        ),
    )
    parser.add_argument("--out", type=Path, required=True, help="CSV の出力先ディレクトリ")
    args = parser.parse_args(argv)

    manifest_path = args.snapshot_root / args.snapshot_id / "manifest.json"
    with manifest_path.open() as f:
        manifest = json.load(f)
    if manifest.get("snapshot_id") != args.snapshot_id:
        print(f"snapshot_id が一致しない: {manifest_path}", file=sys.stderr)
        return 1

    merged, bounds = collect_gaps(manifest)
    overlap_rows = sweep_overlap(merged)
    raw_dir = args.raw_dir if args.raw_dir.is_dir() else None
    try:
        rows = build_interval_rows(merged, overlap_rows, raw_dir, source_records(manifest))
    except RawSourceMismatch as exc:
        print(f"原データが snapshot の記録と一致しない: {exc}", file=sys.stderr)
        return 1

    write_csv(args.out / "gap_intervals.csv", INTERVAL_FIELDS, rows)
    write_csv(args.out / "cross_symbol_overlap.csv", OVERLAP_FIELDS, overlap_rows)
    print_summary(rows, overlap_rows, merged, bounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
