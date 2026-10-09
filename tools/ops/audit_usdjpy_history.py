"""USDJPY の 15 分足・1 時間足の生成経路と欠落の再現可能な監査（読むだけ）。

記録文書は ``docs/data/audit_usdjpy_2026-10.md``。本スクリプトはその表の数字と CSV を再現する。

読むもの（どれも書き換えない）:

- 原 CSV ``<data-root>/raw/market/USDJPY_<15m|1h>_merged.csv``（D03 §2。旧基盤から移管した集約足）
- HistData の 1 分足 ``<data-root>/raw/histdata/USDJPY/*/DAT_ASCII_USDJPY_M1_*.csv``（原 CSV の
  元になったと見られるファイル。取得の手順は repo に無い）
- 補充分 ``<data-root>/raw/market/refill/<refill_id>/``（補充の manifest と USDJPY の足のファイル）
- snapshot の manifest ``<data-root>/snapshots/<snapshot_id>/manifest.json``
  （補正前・補正後・補充後）
- 設定 ``configs/calendars/fx_ny17_v1.yaml``・``fx_ny17_v2.yaml``・``timeframes_v1.yaml``
  （app 層の読込関数 ``odyssey_fx.app.config.calendars`` で読む）

出すもの: 件数・時刻・区間・ハッシュだけ。**価格の値は表示も保存もしない**。価格の文字列を読むのは
「1 分足から 15 分足・1 時間足を作り直したとき原 CSV の始値・高値・安値・終値と一致するか」の件数を
数えるときだけで、対象は研究履歴区分（足の終端が 2024-01-01T00:00Z 以前。ADR-0014）に限る。
封印期間（2024〜2025）と未割当の期間（2026）は時刻の有無だけを数える。snapshot の partition の
実体（Parquet）は読まない。戦略の成績（``runs/``）は読まない。

実行（リポジトリの根で）::

    uv run python -m tools.ops.audit_usdjpy_history --data-root <data ディレクトリ> \
        --out-dir docs/data/audit_usdjpy_2026-10
"""

from __future__ import annotations

import argparse
import bisect
import calendar as std_calendar
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tools.ops import research_history_gaps as rhg

SYMBOL = "USDJPY"
SNAPSHOT_PRE = "a498b8cf4f90aeca1fa8af7a1e59112db197413eaa8c0a0d318eb2368a770cb3"
SNAPSHOT_CORRECTED = "d37ec89da9365d73bbffe86407c026d94670228762494e8fd41ec4f87455e2db"
SNAPSHOT_FINAL = "1092f66c999026f5551a2289b0986d0565c411cd0b0bd2c9e312dad85d9bb151"
REFILL_ID = "5b6e8d1065617f1aec088c8e16d95f52872a6316e7014c051cac58da29771af6"
SNAPSHOTS: tuple[tuple[str, str], ...] = (
    ("補正前", SNAPSHOT_PRE),
    ("補正後", SNAPSHOT_CORRECTED),
    ("補充後", SNAPSHOT_FINAL),
)

TIMEFRAMES: tuple[str, ...] = ("15m@v1", "1h@v1")
STEP: dict[str, int] = {"15m@v1": 900, "1h@v1": 3600}
TF_ID: dict[str, str] = {"15m@v1": "15m", "1h@v1": "1h"}

#: HistData の 1 分足のラベルを UTC へ読む既定の差（UTC−5）。週ごとの差は ``align_m1`` が選ぶ
HISTDATA_OFFSET = 5 * 3600
#: 研究履歴区分の終わり（ADR-0014。足の終端がこれ以前なら研究履歴）
RESEARCH_END = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())

Span = tuple[int, int]


# --- 小さな道具 ------------------------------------------------------------------


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%MZ")


def to_epoch(moment: datetime) -> int:
    return int(moment.astimezone(UTC).timestamp())


def year_of_end(end: int) -> int:
    """足（区間）の終端の年。終端ちょうど 1 月 1 日 0:00 は前の年に数える（bar_end 基準）。"""
    return datetime.fromtimestamp(end - 1, UTC).year


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def runs(bars: Iterable[int], step: int) -> list[Span]:
    """足の開始時刻の列を、前の足の終端と次の足の開始が一致するものどうしでつなぐ。"""
    out: list[Span] = []
    for start in sorted(bars):
        if out and out[-1][1] == start:
            out[-1] = (out[-1][0], start + step)
        else:
            out.append((start, start + step))
    return out


def count_between(sorted_values: Sequence[int], lo: int, hi: int) -> int:
    return bisect.bisect_left(sorted_values, hi) - bisect.bisect_left(sorted_values, lo)


def hours(spans: Iterable[Span]) -> float:
    return sum(e - s for s, e in spans) / 3600


# --- 原 CSV・1 分足・補充分の読込（時刻だけ） --------------------------------------


@dataclass(frozen=True)
class MergedFile:
    """原 CSV 1 本の時刻と出所（価格は持たない）。"""

    path: Path
    sha256: str
    starts: tuple[int, ...]
    sources: tuple[str, ...]


def read_merged(path: Path) -> MergedFile:
    """原 CSV の先頭列（足の開始時刻）と ``source`` 列だけを読む。"""
    starts: list[int] = []
    sources: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        source_col = header.index("source")
        for row in reader:
            if not row:
                continue
            starts.append(to_epoch(datetime.fromisoformat(row[0])))
            sources.append(row[source_col])
    return MergedFile(path, sha256_of(path), tuple(starts), tuple(sources))


@dataclass(frozen=True)
class Correction:
    """manifest に記録された時刻ラベルの補正（D03 §4 の v1.19）を整数の時刻で持つ。"""

    weeks: tuple[Span, ...]
    shift: int
    series: frozenset[str]
    counts: dict[str, int]


def read_correction(manifest: dict[str, Any]) -> Correction | None:
    rule = rhg.label_correction(manifest)
    if rule is None:
        return None
    return Correction(
        weeks=tuple((to_epoch(s), to_epoch(e)) for s, e in rule.weeks),
        shift=int(rule.shift.total_seconds()),
        series=rule.series,
        counts=dict(rule.counts),
    )


def apply_correction(
    starts: Sequence[int], correction: Correction | None, series_text: str
) -> tuple[list[int], int]:
    """受入れと同じ補正（対象の週のラベルに補正量を足す）を当てた開始時刻と、動かした本数。"""
    if correction is None or series_text not in correction.series:
        return list(starts), 0
    out: list[int] = []
    moved = 0
    for start in starts:
        if any(lo <= start < hi for lo, hi in correction.weeks):
            out.append(start + correction.shift)
            moved += 1
        else:
            out.append(start)
    return out, moved


@dataclass(frozen=True)
class M1File:
    name: str
    sha256: str
    rows: int
    first: int
    last: int


def _m1_label(stamp: str) -> int:
    """``YYYYMMDD HHMMSS``（HistData のラベル。時刻帯の表記なし）を、そのまま整数時刻に読む。"""
    return std_calendar.timegm(
        (
            int(stamp[0:4]),
            int(stamp[4:6]),
            int(stamp[6:8]),
            int(stamp[9:11]),
            int(stamp[11:13]),
            int(stamp[13:15]),
        )
    )


def m1_paths(histdata_dir: Path) -> list[Path]:
    return sorted(histdata_dir.glob("*/DAT_ASCII_*_M1_*.csv"))


def read_m1_labels(histdata_dir: Path) -> tuple[list[int], list[M1File]]:
    """HistData の 1 分足のラベルを昇順で返す（UTC へは直さない）。価格の列は読まない。"""
    labels: list[int] = []
    files: list[M1File] = []
    for path in m1_paths(histdata_dir):
        stamps: list[int] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    stamps.append(_m1_label(line[:15]))
        files.append(M1File(path.name, sha256_of(path), len(stamps), min(stamps), max(stamps)))
        labels.extend(stamps)
    labels.sort()
    return labels, files


@dataclass(frozen=True)
class M1Alignment:
    """週ごとに選んだ「ラベル → UTC」の差（秒）。週の外のラベルは既定の UTC−5 で読む。

    ``windows`` はラベルの半開区間と差の組（ラベルの昇順）。差は候補 UTC−5・UTC−4 のうち、
    1 分足を 15 分の区画に区切ったものと補正後の原 CSV の 15 分足の重なりが多い方（同数なら
    UTC−5）。データから選んだ値であり、HistData の公表仕様から決めたものではない。
    """

    windows: tuple[tuple[int, int, int], ...]

    def offset(self, label: int) -> int:
        i = bisect.bisect_right(self.windows, (label, 1 << 62, 0)) - 1
        if i >= 0 and self.windows[i][0] <= label < self.windows[i][1]:
            return self.windows[i][2]
        return HISTDATA_OFFSET

    def utc(self, labels: Sequence[int]) -> list[int]:
        return sorted(label + self.offset(label) for label in labels)


M1_OFFSETS: tuple[int, ...] = (5 * 3600, 4 * 3600)


def align_m1(labels: Sequence[int], sessions: Sequence[Span], raw_15m: set[int]) -> M1Alignment:
    """週の開場区間ごとに、1 分足のラベルを UTC へ読む差を選ぶ（``M1Alignment``）。"""
    windows: list[tuple[int, int, int]] = []
    for open_, close in sessions:
        # 週のラベルの範囲（どちらの差で読んでも週の前後 3 時間に入るもの）
        lo = open_ - 3 * 3600 - max(M1_OFFSETS)
        hi = close + 3 * 3600 - min(M1_OFFSETS)
        week = labels[bisect.bisect_left(labels, lo) : bisect.bisect_left(labels, hi)]
        if not week:
            continue
        best = HISTDATA_OFFSET
        best_score = -1
        for offset in M1_OFFSETS:
            buckets = {(m + offset) - (m + offset) % 900 for m in week}
            score = len(buckets & raw_15m)
            if score > best_score:
                best, best_score = offset, score
        windows.append((lo, hi, best))
    return M1Alignment(tuple(windows))


def read_refill_starts(refill_dir: Path, series_text: str) -> tuple[list[int], dict[str, Any]]:
    """補充分の manifest に記録された系列の足のファイルを検算して開始時刻を返す。"""
    manifest = json.loads((refill_dir / "refill_manifest.json").read_text(encoding="utf-8"))
    starts: list[int] = []
    for entry in manifest["files"]:
        if entry["series"] != series_text:
            continue
        path = refill_dir / entry["name"]
        if sha256_of(path) != entry["sha256"]:
            raise rhg.RawSourceMismatch(f"補充分の sha256 が manifest と一致しない: {path}")
        merged = read_merged(path)
        if len(merged.starts) != int(entry["rows"]):
            raise rhg.RawSourceMismatch(f"補充分の行数が manifest と一致しない: {path}")
        starts.extend(merged.starts)
    return sorted(starts), manifest


# --- カレンダー -------------------------------------------------------------------


def expected_starts(
    calendar: TradingCalendar, definition: TimeframeDefinition, lo: int, hi: int
) -> list[int]:
    window = Interval(
        start=UtcTime(datetime.fromtimestamp(lo, UTC)), end=UtcTime(datetime.fromtimestamp(hi, UTC))
    )
    return [to_epoch(t.value) for t in calendar.expected_bar_starts(definition, window)]


def weekly_sessions(calendar: TradingCalendar, lo: int, hi: int) -> list[Span]:
    """[lo, hi) に掛かる週の開場区間（休場を適用する前。D03 §3.4 の ``weekly_session_at``）。"""
    seen: dict[int, int] = {}
    probe = lo - 7 * 86400
    while probe < hi + 7 * 86400:
        session = calendar.weekly_session_at(UtcTime(datetime.fromtimestamp(probe, UTC)))
        if session is not None:
            seen[to_epoch(session.start.value)] = to_epoch(session.end.value)
        probe += 86400
    return sorted((s, e) for s, e in seen.items() if e > lo and s < hi)


# --- snapshot の manifest ---------------------------------------------------------


def load_manifest(snapshot_root: Path, snapshot_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (snapshot_root / snapshot_id / "manifest.json").read_text(encoding="utf-8")
    )
    return payload


def snapshot_spans(
    manifest: dict[str, Any], timeframe: str, kind: str, outcome: str | None = None
) -> list[Span]:
    """manifest の確定済み分類から、USDJPY の該当の記録をつないだ区間（全アクセス区分）。"""
    records: list[Span] = []
    for record in manifest["resolved_classifications"]:
        sid = record["series_id"]
        if (sid["symbol"], sid["timeframe"], sid["basis"]) != (SYMBOL, timeframe, "bid"):
            continue
        if record["kind"] != kind or (outcome is not None and record["outcome"] != outcome):
            continue
        records.append(
            (
                to_epoch(rhg.parse_utc(record["interval"]["start"])),
                to_epoch(rhg.parse_utc(record["interval"]["end"])),
            )
        )
    merged: list[Span] = []
    for s, e in sorted(records):
        if merged and merged[-1][1] == s:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def source_record(manifest: dict[str, Any], timeframe: str) -> dict[str, Any] | None:
    for src in manifest["sources"]:
        if (
            src["symbol"] == SYMBOL
            and src["timeframe"] == timeframe
            and "refill" not in src["path"]
        ):
            record: dict[str, Any] = src
            return record
    return None


# --- 監査の本体 --------------------------------------------------------------------


@dataclass(frozen=True)
class SeriesAudit:
    """1 時間足ぶんの監査の中間結果（時刻と出所だけ）。"""

    timeframe: str
    merged: MergedFile
    corrected_rows: tuple[int, ...]  # 原 CSV の行の順の補正後の開始時刻
    corrected: tuple[int, ...]  # 昇順
    source_at: dict[int, str]
    moved: int
    refill: tuple[int, ...]
    expected: tuple[int, ...]
    span: Span


def build_series(
    timeframe: str,
    raw_dir: Path,
    refill_dir: Path,
    correction: Correction | None,
    calendar_v2: TradingCalendar,
    definitions: dict[str, TimeframeDefinition],
) -> SeriesAudit:
    merged = read_merged(raw_dir / f"{SYMBOL}_{TF_ID[timeframe]}_merged.csv")
    series_text = f"{SYMBOL}/{TF_ID[timeframe]}/bid"
    corrected_rows, moved = apply_correction(merged.starts, correction, series_text)
    if len(set(corrected_rows)) != len(corrected_rows):
        raise rhg.RawSourceMismatch(f"補正の後に同じ開始時刻の足が重なった: {series_text}")
    source_at = dict(zip(corrected_rows, merged.sources, strict=True))
    refill, _ = read_refill_starts(refill_dir, series_text)
    step = STEP[timeframe]
    lo = min(corrected_rows)
    hi = max(max(corrected_rows), max(refill) if refill else 0) + step
    expected = expected_starts(calendar_v2, definitions[TF_ID[timeframe]], lo, hi)
    return SeriesAudit(
        timeframe=timeframe,
        merged=merged,
        corrected_rows=tuple(corrected_rows),
        corrected=tuple(sorted(corrected_rows)),
        source_at=source_at,
        moved=moved,
        refill=tuple(refill),
        expected=tuple(expected),
        span=(lo, hi),
    )


def hash_rows(
    audits: dict[str, SeriesAudit], manifests: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """原 CSV の sha256・行数と、各 snapshot の manifest の ``sources`` の記録の突き合わせ。"""
    rows: list[dict[str, Any]] = []
    for tf, audit in audits.items():
        for label, sid in SNAPSHOTS:
            record = source_record(manifests[sid], tf)
            rows.append(
                {
                    "file": audit.merged.path.name,
                    "snapshot": f"{label} {sid[:8]}",
                    "sha256_actual": audit.merged.sha256,
                    "sha256_recorded": record["sha256"] if record else "",
                    "rows_actual": len(audit.merged.starts),
                    "rows_recorded": record["rows"] if record else "",
                    "match": bool(
                        record
                        and record["sha256"] == audit.merged.sha256
                        and int(record["rows"]) == len(audit.merged.starts)
                    ),
                }
            )
    return rows


def source_counts(audits: dict[str, SeriesAudit]) -> list[dict[str, Any]]:
    """原 CSV の出所（``source`` 列）の年ごとの行数（補正後の足の終端の年）。"""
    rows: list[dict[str, Any]] = []
    for tf, audit in audits.items():
        counter: Counter[tuple[int, str]] = Counter()
        for start, source in audit.source_at.items():
            counter[(year_of_end(start + STEP[tf]), source)] += 1
        rows.extend(
            {"timeframe": tf, "year_of_bar_end": y, "source": s, "rows": n}
            for (y, s), n in sorted(counter.items())
        )
    return rows


def access_class(year: int) -> str:
    if year <= 2023:
        return "RESEARCH_HISTORY"
    if year <= 2025:
        return "LEGACY_HOLDOUT"
    return "QUARANTINED_UNASSIGNED"


def year_rows(
    audits: dict[str, SeriesAudit], final_manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    """年ごとの保有と欠落（足の終端の年）。元 CSV からの計算と snapshot の記録を並べる。"""
    rows: list[dict[str, Any]] = []
    for tf, audit in audits.items():
        step = STEP[tf]
        expected = set(audit.expected)
        missing_raw = sorted(expected - set(audit.corrected))
        missing_final = sorted(expected - set(audit.corrected) - set(audit.refill))
        gaps_final = runs(missing_final, step)
        snap_gaps = snapshot_spans(final_manifest, tf, "MISSING_EXPECTED_BAR", "DATA_GAP")
        by_year: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        for name, values in (
            ("raw", list(audit.corrected)),
            ("refill", list(audit.refill)),
            ("expected", list(audit.expected)),
            ("missing_raw", missing_raw),
            ("missing_final", missing_final),
        ):
            for start in values:
                by_year[year_of_end(start + step)][name].append(start)
        for year in sorted(by_year):
            bucket = by_year[year]
            gy = [g for g in gaps_final if year_of_end(g[1]) == year]
            sy = [g for g in snap_gaps if year_of_end(g[1]) == year]
            held = bucket["raw"] + bucket["refill"]
            rows.append(
                {
                    "timeframe": tf,
                    "year_of_bar_end": year,
                    "access_class": access_class(year),
                    "first_bar_utc": iso(min(held)) if held else "",
                    "last_bar_end_utc": iso(max(held) + step) if held else "",
                    "raw_rows": len(bucket["raw"]),
                    "refill_rows": len(bucket["refill"]),
                    "expected_bars": len(bucket["expected"]),
                    "missing_bars_raw_csv": len(bucket["missing_raw"]),
                    "missing_bars_after_refill": len(bucket["missing_final"]),
                    "gap_intervals_raw_csv": len(gy),
                    "gap_hours_raw_csv": hours(gy),
                    "gap_intervals_snapshot": len(sy),
                    "gap_hours_snapshot": hours(sy),
                    "same_intervals": gy == sy,
                }
            )
    return rows


def stage_rows(
    audits: dict[str, SeriesAudit], m1_minutes: Sequence[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """欠落区間ごとの発生段階（元 CSV と 1 分足を直接数えた判定）。

    段階: 補正前の原 CSV の欠落（G0）→ 受入れの時刻補正の後（G1）→ 補充の後（G2）。期待する足は
    カレンダー ``fx_ny17`` v2 で決める（休場の宣言の中は数えない）。G1 の区間ごとに 1 分足の有無を
    数え、1 分足にも無いのか、1 分足にはあって原 CSV で欠けたのかを分ける。補正で消えた G0 の
    区間は 2 つ目の戻り値に分ける。
    """
    rows: list[dict[str, Any]] = []
    vanished: list[dict[str, Any]] = []
    for tf, audit in audits.items():
        step = STEP[tf]
        expected = set(audit.expected)
        g0 = expected - set(audit.merged.starts)
        g1 = expected - set(audit.corrected)
        refill = set(audit.refill)
        for s, e in runs(g1, step):
            bars = list(range(s, e, step))
            m1_counts = [count_between(m1_minutes, b, b + step) for b in bars]
            with_m1 = sum(1 for c in m1_counts if c > 0)
            in_g0 = sum(1 for b in bars if b in g0)
            filled = sum(1 for b in bars if b in refill)
            i = bisect.bisect_left(audit.corrected, s)
            j = bisect.bisect_left(audit.corrected, e)
            prev_bar = audit.corrected[i - 1] if i > 0 else None
            next_bar = audit.corrected[j] if j < len(audit.corrected) else None
            if with_m1 == 0:
                origin = "M1_ABSENT"
            elif with_m1 == len(bars):
                origin = "M1_PRESENT"
            else:
                origin = "M1_PARTIAL"
            if in_g0 == len(bars):
                acceptance = "RAW_GAP"
            elif in_g0 == 0:
                acceptance = "APPEARED_BY_CORRECTION"
            else:
                acceptance = "PARTLY_BY_CORRECTION"
            if filled == len(bars):
                after = "FILLED"
            elif filled == 0:
                after = "REMAINS"
            else:
                after = "PARTLY_FILLED"
            rows.append(
                {
                    "timeframe": tf,
                    "start_utc": iso(s),
                    "end_utc": iso(e),
                    "hours": (e - s) / 3600,
                    "year_of_bar_end": year_of_end(e),
                    "access_class": access_class(year_of_end(e)),
                    "bars": len(bars),
                    "m1_minutes": sum(m1_counts),
                    "bars_with_m1": with_m1,
                    "origin": origin,
                    "acceptance": acceptance,
                    "refilled_bars": filled,
                    "after_refill": after,
                    "prev_raw_bar_utc": "" if prev_bar is None else iso(prev_bar),
                    "prev_raw_source": "" if prev_bar is None else audit.source_at[prev_bar],
                    "next_raw_bar_utc": "" if next_bar is None else iso(next_bar),
                    "next_raw_source": "" if next_bar is None else audit.source_at[next_bar],
                }
            )
        for s, e in runs(g0 - g1, step):
            vanished.append(
                {
                    "timeframe": tf,
                    "start_utc": iso(s),
                    "end_utc": iso(e),
                    "bars": (e - s) // step,
                    "m1_minutes": count_between(m1_minutes, s, e),
                }
            )
    return rows, vanished


def unexpected_rows(audits: dict[str, SeriesAudit]) -> list[dict[str, Any]]:
    """カレンダー v2 で休場の時間帯に入る足（補正前・補正後の原 CSV、補充分）の本数。"""
    rows: list[dict[str, Any]] = []
    for tf, audit in audits.items():
        expected = set(audit.expected)
        rows.append(
            {
                "timeframe": tf,
                "raw_uncorrected": sum(1 for b in audit.merged.starts if b not in expected),
                "raw_corrected": sum(1 for b in audit.corrected if b not in expected),
                "refill": sum(1 for b in audit.refill if b not in expected),
            }
        )
    return rows


def week_rows(
    audits: dict[str, SeriesAudit],
    calendar_v2: TradingCalendar,
    m1_minutes: Sequence[int],
    alignment: M1Alignment,
    correction: Correction | None,
) -> list[dict[str, Any]]:
    """週ごとの時刻ラベルの位置（週の開場・閉場との差）と 1 分足との照合。

    ``first_offset_h``: 補正前の原 CSV の週の最初の足の開始 − 週の開場（時間）。
    ``last_offset_h``: 補正前の週の最後の足の終端 − 週の閉場（時間）。
    ``us_dst`` / ``uk_dst``: 週の開場の時点で ``America/New_York`` / ``Europe/London`` が夏時間か。
    ``m1_label_to_utc_h``: ``align_m1`` が選んだ 1 分足のラベル → UTC の差（時間。−5 か −4）。
    ``m1_first_offset_h`` / ``m1_last_offset_h``: その差で読んだ 1 分足の最初の分 − 週の開場、
    最後の分の終端 − 週の閉場（時間）。
    ``histdata_rows_not_in_m1_*``: 出所 ``histdata`` の足のうち、1 分足を同じ時間足に区切った
    区画に無いものの本数（補正前のラベル・補正後のラベル）。
    ``m1_buckets_not_in_raw``: 1 分足の区画にあるのに補正後の原 CSV に足が無い本数。
    """
    rows: list[dict[str, Any]] = []
    rule_opens: set[int] = set()
    if correction is not None:
        rule_opens = {start + correction.shift for start, _ in correction.weeks}
    london = ZoneInfo("Europe/London")
    for tf, audit in audits.items():
        step = STEP[tf]
        unc = sorted(audit.merged.starts)
        source_unc = dict(zip(audit.merged.starts, audit.merged.sources, strict=True))
        for open_, close in weekly_sessions(calendar_v2, *audit.span):
            lo, hi = open_ - 3 * 3600, close + 3 * 3600
            week_unc = unc[bisect.bisect_left(unc, lo) : bisect.bisect_left(unc, hi)]
            if not week_unc:
                continue
            m1_lo = bisect.bisect_left(m1_minutes, lo)
            m1_hi = bisect.bisect_left(m1_minutes, hi)
            week_m1 = m1_minutes[m1_lo:m1_hi]
            buckets = {m - m % step for m in week_m1}
            opened = datetime.fromtimestamp(open_, UTC)
            label_offset = alignment.offset(open_ - HISTDATA_OFFSET)
            week_cor = audit.corrected[
                bisect.bisect_left(audit.corrected, lo) : bisect.bisect_left(audit.corrected, hi)
            ]
            hist_unc = [b for b in week_unc if source_unc[b] == "histdata"]
            hist_cor = [b for b in week_cor if audit.source_at[b] == "histdata"]
            cor_set = set(week_cor)
            rows.append(
                {
                    "timeframe": tf,
                    "week_open_utc": iso(open_),
                    "week_close_utc": iso(close),
                    "sources": "|".join(sorted({source_unc[b] for b in week_unc})),
                    "first_offset_h": (week_unc[0] - open_) / 3600,
                    "last_offset_h": (week_unc[-1] + step - close) / 3600,
                    "in_correction_rule": open_ in rule_opens,
                    "us_dst": bool(opened.astimezone(rhg.NEW_YORK).dst()),
                    "uk_dst": bool(opened.astimezone(london).dst()),
                    "m1_label_to_utc_h": -label_offset / 3600,
                    "m1_minutes": m1_hi - m1_lo,
                    "m1_first_offset_h": (week_m1[0] - open_) / 3600 if week_m1 else "",
                    "m1_last_offset_h": (week_m1[-1] + 60 - close) / 3600 if week_m1 else "",
                    "histdata_rows_not_in_m1_uncorrected": sum(
                        1 for b in hist_unc if b not in buckets
                    ),
                    "histdata_rows_not_in_m1_corrected": sum(
                        1 for b in hist_cor if b not in buckets
                    ),
                    "m1_buckets_not_in_raw": sum(1 for b in buckets if b not in cor_set),
                }
            )
    return rows


def _m1_aggregates(
    histdata_dir: Path, alignment: M1Alignment
) -> dict[int, dict[int, list[Decimal]]]:
    """研究履歴区分の 1 分足を時間足の区画ごとに 4 値へまとめる（内部だけで使う）。"""
    tables: dict[int, dict[int, list[Decimal]]] = {step: {} for step in set(STEP.values())}
    for path in m1_paths(histdata_dir):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split(";")
                if len(parts) < 5:
                    continue
                label = _m1_label(parts[0])
                minute = label + alignment.offset(label)
                if minute - minute % 3600 + 3600 > RESEARCH_END:
                    continue
                o, h, low, c = (Decimal(x) for x in parts[1:5])
                for step, table in tables.items():
                    bucket = minute - minute % step
                    agg = table.get(bucket)
                    if agg is None:
                        table[bucket] = [o, h, low, c]
                        continue
                    if h > agg[1]:
                        agg[1] = h
                    if low < agg[2]:
                        agg[2] = low
                    agg[3] = c
    return tables


def ohlc_rows(
    audits: dict[str, SeriesAudit], histdata_dir: Path, alignment: M1Alignment
) -> list[dict[str, Any]]:
    """1 分足から作り直した足と原 CSV の足の一致の件数（研究履歴区分だけ。価格は出さない）。

    作り方: 1 分足を週ごとの差（``align_m1``）で UTC に読み、補正後のラベルの区画ごとに、
    始値＝最初の 1 分足の始値、高値＝最大、安値＝最小、終値＝最後の 1 分足の終値とする。
    比べるのは 4 値すべての一致だけで、差の大きさは計算しない。1 分足は 1 時間の区画が
    研究履歴区分に収まるもの（終端 2024-01-01T00:00Z 以前）だけを読む。
    """
    tables = _m1_aggregates(histdata_dir, alignment)
    counter: Counter[tuple[str, int, str, str]] = Counter()
    for tf, audit in audits.items():
        step = STEP[tf]
        with audit.merged.path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            cols = [header.index(name) for name in ("open", "high", "low", "close")]
            src_col = header.index("source")
            for row, start in zip(reader, audit.corrected_rows, strict=True):
                if start + step > RESEARCH_END:
                    continue
                agg = tables[step].get(start)
                if agg is None:
                    verdict = "NO_M1"
                else:
                    verdict = "EQUAL" if [Decimal(row[k]) for k in cols] == agg else "DIFFERENT"
                counter[(tf, year_of_end(start + step), row[src_col], verdict)] += 1
    return [
        {"timeframe": tf, "year_of_bar_end": y, "source": s, "verdict": v, "bars": n}
        for (tf, y, s, v), n in sorted(counter.items())
    ]


def closure_rows(
    audits: dict[str, SeriesAudit],
    calendar_v1: TradingCalendar,
    calendar_v2: TradingCalendar,
    definitions: dict[str, TimeframeDefinition],
    m1_minutes: Sequence[int],
) -> list[dict[str, Any]]:
    """カレンダー v2 の休場の宣言が閉じる足の区間（v1 で期待し v2 で期待しない足）ごとの事実。"""
    rows: list[dict[str, Any]] = []
    notes = {rule.local_date.isoformat(): rule.note for rule in calendar_v2.closures}
    for tf, audit in audits.items():
        step = STEP[tf]
        v1 = set(expected_starts(calendar_v1, definitions[TF_ID[tf]], *audit.span))
        closed = sorted(v1 - set(audit.expected))
        cor = audit.corrected
        for s, e in runs(closed, step):
            i = bisect.bisect_left(cor, s)
            j = bisect.bisect_left(cor, e)
            prev_end = cor[i - 1] + step if i > 0 else None
            next_start = cor[j] if j < len(cor) else None
            local_date = datetime.fromtimestamp(e - 1, UTC).astimezone(rhg.NEW_YORK).date()
            rows.append(
                {
                    "timeframe": tf,
                    "local_date": local_date.isoformat(),
                    "start_utc": iso(s),
                    "end_utc": iso(e),
                    "hours": (e - s) / 3600,
                    "note": notes.get(local_date.isoformat(), ""),
                    "raw_rows_inside": j - i,
                    "refill_rows_inside": count_between(audit.refill, s, e),
                    "m1_minutes_inside": count_between(m1_minutes, s, e),
                    "raw_prev_bar_end_utc": "" if prev_end is None else iso(prev_end),
                    "raw_next_bar_start_utc": "" if next_start is None else iso(next_start),
                    "edges_equal_raw_gap": prev_end == s and next_start == e,
                }
            )
    return rows


# --- 会話中の数字の再現 -----------------------------------------------------------


def dst_facts(raw_dir: Path, manifest_pre: dict[str, Any], correction: Correction) -> Counter[str]:
    """20 系列の原 CSV の時刻だけから、夏時間ズレの数字（D03 §2）を数え直す。"""
    out: Counter[str] = Counter()
    sources = rhg.source_records(manifest_pre)
    for symbol in rhg.SYMBOLS:
        for tf in TIMEFRAMES:
            stamps = rhg.load_raw_timestamps(raw_dir, symbol, tf, sources.get((symbol, tf)), None)
            starts = sorted(to_epoch(datetime.fromisoformat(s)) for s in stamps)
            present = set(starts)
            step = STEP[tf]
            for ws, we in correction.weeks:
                open_, close = ws + correction.shift, we + correction.shift
                out[f"shifted_{tf}"] += count_between(starts, ws, we)
                fri = sum(1 for b in range(close - 3600, close, step) if b not in present)
                sun = count_between(starts, open_ - 3600, open_)
                out["friday_missing_bars"] += fri
                out["sunday_out_of_session_bars"] += sun
                out["groups"] += int(fri > 0) + int(sun > 0)
                out["weeks_without_pre_open_hour"] += int(sun == 0)
                lacking = [
                    b
                    for b in range(open_, open_ + 3600, step)
                    if (b - correction.shift) not in present
                ]
                out["open_hour_missing_after_correction"] += int(bool(lacking))
    return out


def gap_totals(manifest: dict[str, Any]) -> tuple[int, float, dict[str, tuple[int, float]]]:
    """研究履歴区分の 20 系列の欠落区間の数と時間（PR #55 の数え方。``research_history_gaps``）。"""
    merged, _ = rhg.collect_gaps(manifest)
    per_tf: dict[str, tuple[int, float]] = {}
    for (symbol, tf), spans in merged.items():
        if symbol == SYMBOL:
            per_tf[tf] = (len(spans), sum((e - s).total_seconds() for s, e in spans) / 3600)
    count = sum(len(v) for v in merged.values())
    total = sum((e - s).total_seconds() for v in merged.values() for s, e in v) / 3600
    return count, total, per_tf


def overlap_groups(spans: Iterable[Span]) -> int:
    """時刻帯が重なる区間を 1 つにまとめた数（15 分足と 1 時間足の欠落を合わせた「か所」）。"""
    groups = 0
    reach = -1
    for s, e in sorted(spans):
        if s >= reach:
            groups += 1
        reach = max(reach, e)
    return groups


def check_rows(
    raw_dir: Path,
    refill_manifest: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
    audits: dict[str, SeriesAudit],
) -> list[dict[str, Any]]:
    """会話・記録文書の数字と、本スクリプトで数え直した値の対照表。"""
    final, corrected, pre = (
        manifests[SNAPSHOT_FINAL],
        manifests[SNAPSHOT_CORRECTED],
        manifests[SNAPSHOT_PRE],
    )
    correction = read_correction(corrected)
    rows: list[dict[str, Any]] = []

    def add(item: str, where: str, expected: object, actual: object) -> None:
        rows.append(
            {
                "item": item,
                "where": where,
                "stated": expected,
                "reproduced": actual,
                "match": str(expected) == str(actual),
            }
        )

    if correction is not None:
        facts = dst_facts(raw_dir, pre, correction)
        add("補正した足 15分足（20 系列）", "D03 §2・§14.18.2", 124595, facts["shifted_15m@v1"])
        add("補正した足 1時間足（20 系列）", "D03 §2・§14.18.2", 31150, facts["shifted_1h@v1"])
        add("補正の対象の週", "D03 §2・§4", 26, len(correction.weeks))
        add(
            "補正前の見かけの欠落（金曜 20 時台）の足", "D03 §2", 1300, facts["friday_missing_bars"]
        )
        add(
            "補正前の見かけの休場帯の足（日曜 20 時台）",
            "D03 §2",
            1270,
            facts["sunday_out_of_session_bars"],
        )
        add(
            "見かけの警告の足の合計",
            "D03 §2",
            2570,
            facts["friday_missing_bars"] + facts["sunday_out_of_session_bars"],
        )
        add("見かけの警告の系列×週×種別のまとまり", "D03 §2", 1030, facts["groups"])
        add(
            "日曜の開場前 1 時間が原データに丸ごと無い系列×週",
            "D03 §2",
            10,
            facts["weeks_without_pre_open_hour"],
        )
        add(
            "補正後に日曜の開場直後の足が無い系列×週",
            "D03 §2",
            13,
            facts["open_hour_missing_after_correction"],
        )
        for tf, audit in audits.items():
            add(
                f"USDJPY {TF_ID[tf]} の補正した足（manifest の記録との一致）",
                "snapshot d37ec89d の conversion",
                correction.counts.get(f"{SYMBOL}/{TF_ID[tf]}/bid"),
                audit.moved,
            )

    for label, manifest, n_stated, h_stated in (
        ("補充後 1092f66c", final, 252, 803.25),
        ("補充前（補正後）d37ec89d", corrected, 444, 1767.25),
        ("補正前 a498b8cf", pre, 733, 2058.25),
    ):
        count, total, per_tf = gap_totals(manifest)
        add(
            f"研究履歴 20 系列の欠落区間（{label}）",
            "research_history_gaps.md §2・§11・§12",
            n_stated,
            count,
        )
        add(f"同 合計時間（{label}）", "同上", h_stated, total)
        if manifest is final:
            add(
                "USDJPY 15分足 区間・時間（補充後）",
                "同 §2・§3.2",
                "10/34.0",
                f"{per_tf['15m@v1'][0]}/{per_tf['15m@v1'][1]:.1f}",
            )
            add(
                "USDJPY 1時間足 区間・時間（補充後）",
                "同 §2",
                "7/31.0",
                f"{per_tf['1h@v1'][0]}/{per_tf['1h@v1'][1]:.1f}",
            )
            merged, _ = rhg.collect_gaps(manifest)
            spans = [
                (to_epoch(s), to_epoch(e)) for tf in TIMEFRAMES for s, e in merged[(SYMBOL, tf)]
            ]
            add(
                "USDJPY 残存欠落の か所（15分足・1時間足を重ねる）・区間",
                "D09 §17.8",
                "10/17",
                f"{overlap_groups(spans)}/{len(spans)}",
            )
        if manifest is pre:
            add(
                "USDJPY 15分足 補正前の区間・時間",
                "research_history_gaps.md §9",
                "33/94.0",
                f"{per_tf['15m@v1'][0]}/{per_tf['15m@v1'][1]:.1f}",
            )
            bars = sum(
                1
                for r in pre["resolved_classifications"]
                if r["series_id"]["symbol"] == SYMBOL
                and r["series_id"]["timeframe"] == "15m@v1"
                and r["outcome"] == "DATA_GAP"
                and r["kind"] == "MISSING_EXPECTED_BAR"
                and rhg.parse_utc(r["interval"]["end"]).timestamp() <= RESEARCH_END
            )
            add(
                "USDJPY 15分足 補正前の欠落の足（研究履歴）", "同 §9（D07 §23.2 の 376）", 376, bars
            )

    add(
        "補充した足（補充分 5b6e8d10）",
        "research_history_gaps.md 冒頭",
        2410,
        sum(int(f["rows"]) for f in refill_manifest["files"] if f["series"] is not None),
    )
    unrec = refill_manifest["unreconciled"]
    add("未照合の塊（補充分 5b6e8d10）", "D03 §14.18.2・unreconciled_recheck §4", 60, len(unrec))
    by_tf = Counter(u["series"].split("/")[1] for u in unrec)
    add("同 15分足/1時間足", "unreconciled_recheck §4", "30/30", f"{by_tf['15m']}/{by_tf['1h']}")
    add(
        "同 USDJPY",
        "unreconciled_recheck §4",
        4,
        sum(1 for u in unrec if u["series"].startswith(SYMBOL)),
    )
    causes = sorted({c for u in unrec for c in u["causes"]})
    add("同 原因の種類", "unreconciled_recheck §2・§4", "NO_RECONCILIATION_BAR", "|".join(causes))

    def ticks(lo: str, hi: str) -> int:
        return sum(
            int(h["tick_count"] or 0)
            for h in refill_manifest["hours"]
            if h["hour"]["symbol"] == SYMBOL and lo <= h["hour"]["start"] < hi
        )

    add(
        "区間 A（2017-01-01T22Z〜01-02T07Z）の tick 数",
        "unreconciled_recheck §1",
        1245,
        ticks("2017-01-01T22", "2017-01-02T07"),
    )
    add(
        "区間 B（2019-03-10T21Z〜22Z）の tick 数",
        "unreconciled_recheck §1",
        611,
        ticks("2019-03-10T21", "2019-03-10T22"),
    )
    for name, start, stated in (
        ("A", "2017-01-01T22:00:00+00:00", "2016-12-30T21:45Z"),
        ("B", "2019-03-10T21:00:00+00:00", "2019-03-08T21:45Z"),
    ):
        cor = audits["15m@v1"].corrected
        idx = bisect.bisect_left(cor, to_epoch(datetime.fromisoformat(start)))
        add(
            f"区間 {name} の直前の原データの 15分足",
            "unreconciled_recheck §2",
            stated,
            iso(cor[idx - 1]),
        )

    merged_final, bounds = rhg.collect_gaps(final)
    gaps = sorted((s, e) for tf in TIMEFRAMES for s, e in merged_final[(SYMBOL, tf)])
    lo, hi = bounds[(SYMBOL, "15m@v1")]
    windows = rhg.contiguous_windows(gaps, lo, hi)
    best = max(windows, key=lambda w: w[1] - w[0])
    add(
        "USDJPY 15分足・1時間足に欠落の無い最長の連続区間（研究履歴）",
        "D09 §17.8.5 の段 3 の素材",
        "—（826 日は土曜 12:00Z 揃えと余白の後の値）",
        f"{rhg.fmt_utc(best[0])}〜{rhg.fmt_utc(best[1])}",
    )
    return rows


# --- 出力 ------------------------------------------------------------------------


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def print_table(title: str, rows: list[dict[str, Any]], limit: int | None = None) -> None:
    print(f"\n## {title}（{len(rows)} 行）")
    if not rows:
        return
    print("\t".join(rows[0]))
    for row in rows if limit is None else rows[:limit]:
        print("\t".join(str(v) for v in row.values()))


def summarize_stages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str, str]] = Counter()
    for r in rows:
        counter[(r["timeframe"], r["origin"], r["acceptance"], r["after_refill"])] += 1
    return [
        {
            "timeframe": k[0],
            "origin": k[1],
            "acceptance": k[2],
            "after_refill": k[3],
            "intervals": n,
        }
        for k, n in sorted(counter.items())
    ]


def summarize_weeks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """15 分足の週を、夏時間の組・補正の対象か・ラベルの位置・1 分足の差で数える。"""
    counter: Counter[tuple[Any, ...]] = Counter()
    for r in rows:
        if r["timeframe"] != "15m@v1":
            continue
        counter[
            (
                r["us_dst"],
                r["uk_dst"],
                r["in_correction_rule"],
                r["sources"],
                r["first_offset_h"],
                r["last_offset_h"],
                r["m1_label_to_utc_h"],
            )
        ] += 1
    keys = (
        "us_dst",
        "uk_dst",
        "in_correction_rule",
        "sources",
        "first_offset_h",
        "last_offset_h",
        "m1_label_to_utc_h",
    )
    return [
        {**dict(zip(keys, k, strict=True)), "weeks": n}
        for k, n in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--config-root", type=Path, default=Path("configs/calendars"))
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--skip-ohlc",
        action="store_true",
        help="1 分足から作り直した足との一致の件数（価格の文字列を内部で読む）を数えない",
    )
    args = parser.parse_args(argv)

    data_root: Path = args.data_root
    raw_dir = data_root / "raw" / "market"
    histdata_dir = data_root / "raw" / "histdata" / SYMBOL
    refill_dir = raw_dir / "refill" / REFILL_ID
    snapshot_root = data_root / "snapshots"
    manifests = {sid: load_manifest(snapshot_root, sid) for _, sid in SNAPSHOTS}
    correction = read_correction(manifests[SNAPSHOT_CORRECTED])
    calendar_v1 = load_calendar(args.config_root / "fx_ny17_v1.yaml")
    calendar_v2 = load_calendar(args.config_root / "fx_ny17_v2.yaml")
    definitions = load_timeframes(args.config_root / "timeframes_v1.yaml")

    try:
        audits = {
            tf: build_series(tf, raw_dir, refill_dir, correction, calendar_v2, definitions)
            for tf in TIMEFRAMES
        }
        m1_labels, m1_files = read_m1_labels(histdata_dir)
        sessions = weekly_sessions(calendar_v2, *audits["15m@v1"].span)
        alignment = align_m1(m1_labels, sessions, set(audits["15m@v1"].corrected))
        m1_minutes = alignment.utc(m1_labels)
        refill_manifest = json.loads(
            (refill_dir / "refill_manifest.json").read_text(encoding="utf-8")
        )
        tables: dict[str, list[dict[str, Any]]] = {}
        tables["hashes"] = hash_rows(audits, manifests)
        tables["m1_files"] = [
            {
                "file": f.name,
                "sha256": f.sha256,
                "rows": f.rows,
                "first_label": iso(f.first).replace("Z", ""),
                "last_label": iso(f.last).replace("Z", ""),
            }
            for f in m1_files
        ]
        tables["sources_by_year"] = source_counts(audits)
        tables["years"] = year_rows(audits, manifests[SNAPSHOT_FINAL])
        stages, vanished = stage_rows(audits, m1_minutes)
        tables["gap_stages"] = stages
        tables["gap_stage_summary"] = summarize_stages(stages)
        tables["vanished_by_correction"] = vanished
        tables["out_of_session_bars"] = unexpected_rows(audits)
        tables["weeks"] = week_rows(audits, calendar_v2, m1_minutes, alignment, correction)
        tables["week_summary"] = summarize_weeks(tables["weeks"])
        tables["closures"] = closure_rows(audits, calendar_v1, calendar_v2, definitions, m1_minutes)
        if not args.skip_ohlc:
            tables["ohlc_match_research_history"] = ohlc_rows(audits, histdata_dir, alignment)
        tables["checks"] = check_rows(raw_dir, refill_manifest, manifests, audits)
    except (rhg.RawSourceMismatch, FileNotFoundError) as exc:
        print(f"失敗: {exc}", file=sys.stderr)
        return 1

    for name, rows in tables.items():
        print_table(name, rows, limit=None if name != "weeks" else 0)
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in tables.items():
            write_csv(args.out_dir / f"{name}.csv", rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
