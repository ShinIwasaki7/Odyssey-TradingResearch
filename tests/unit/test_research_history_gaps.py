"""research_history_gaps（研究履歴の欠落一覧の再現スクリプト）の単体テスト。"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.ops import research_history_gaps as rhg


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_merge_adjacent_joins_only_touching_records() -> None:
    a, b, c, d = (
        _t("2019-03-15T20:00"),
        _t("2019-03-15T20:15"),
        _t("2019-03-15T20:30"),
        _t("2019-03-15T21:00"),
    )
    assert rhg.merge_adjacent([(b, c), (a, b), (d, _t("2019-03-15T21:15"))]) == [
        (a, c),
        (d, _t("2019-03-15T21:15")),
    ]


def test_contiguous_windows_clip_to_bounds() -> None:
    lo, hi = _t("2020-01-01T00:00"), _t("2020-01-10T00:00")
    gaps = [
        (_t("2019-12-31T00:00"), _t("2020-01-02T00:00")),
        (_t("2020-01-05T00:00"), _t("2020-01-06T00:00")),
    ]
    assert rhg.contiguous_windows(gaps, lo, hi) == [
        (_t("2020-01-02T00:00"), _t("2020-01-05T00:00")),
        (_t("2020-01-06T00:00"), hi),
    ]


def test_classify_patterns() -> None:
    def attr(start: str, end: str) -> str:
        return rhg.classify(rhg.GapInterval("USDJPY", "15m@v1", _t(start), _t(end)))[0]

    # 金 16:00 NY（夏時間）= 20:00 UTC の 1 時間
    assert attr("2019-03-15T20:00", "2019-03-15T21:00") == rhg.SOURCE_DATA
    assert attr("2021-05-31T04:00", "2021-06-01T00:00") == rhg.SOURCE_DATA_KNOWN
    assert attr("2017-01-01T22:00", "2017-01-02T07:00") == rhg.CALENDAR
    assert attr("2016-12-26T07:00", "2016-12-26T23:00") == rhg.CALENDAR_UNDECLARED_OR_PARTLY_SOURCE
    assert attr("2019-12-25T08:00", "2019-12-25T09:00") == rhg.CALENDAR_ADJACENT_OR_SOURCE
    assert attr("2019-05-27T03:00", "2019-05-27T08:00") == rhg.UNKNOWN


def test_raw_rows_between_counts_bar_starts_in_half_open_interval() -> None:
    stamps = [
        "2019-03-15 19:45:00+00:00",
        "2019-03-15 20:00:00+00:00",
        "2019-03-15 21:00:00+00:00",
    ]
    assert rhg.raw_rows_between(stamps, _t("2019-03-15T20:00"), _t("2019-03-15T21:00")) == 1
    assert rhg.raw_rows_between(stamps, _t("2019-03-15T20:15"), _t("2019-03-15T21:00")) == 0


def _record(symbol: str, tf: str, start: str, end: str, outcome: str) -> dict[str, Any]:
    return {
        "series_id": {"symbol": symbol, "timeframe": tf, "basis": "bid"},
        "interval": {"start": start, "end": end},
        "kind": "MISSING_EXPECTED_BAR",
        "outcome": outcome,
    }


RAW_HEADER = ",open,high,low,close,volume,source\n"
RAW_ROW = "2019-03-15 19:45:00+00:00,1,1,1,1,0,histdata\n"


def _write_snapshot(tmp_path: Path, raw_content: dict[tuple[str, str], str]) -> list[str]:
    """20 系列の研究履歴 partition と原 CSV を持つ最小の snapshot を作り、main の引数を返す。"""
    snapshot_id = "s" * 8
    partitions = [
        {
            "partition_id": {
                "access_class": "RESEARCH_HISTORY",
                "series": {"symbol": s, "timeframe": tf, "basis": "bid"},
            },
            "interval": {"start": "2019-01-01T00:00:00Z", "end": "2020-01-01T00:00:00Z"},
        }
        for s in rhg.SYMBOLS
        for tf in rhg.TIMEFRAMES
    ]
    records = [
        _record("USDJPY", "15m@v1", "2019-03-15T20:00:00Z", "2019-03-15T20:15:00Z", "DATA_GAP"),
        _record("USDJPY", "15m@v1", "2019-03-15T20:15:00Z", "2019-03-15T20:30:00Z", "DATA_GAP"),
        _record("USDJPY", "15m@v1", "2019-03-16T20:00:00Z", "2019-03-16T20:15:00Z", "CLOSURE"),
        _record("USDJPY", "15m@v1", "2021-03-15T20:00:00Z", "2021-03-15T20:15:00Z", "DATA_GAP"),
    ]
    raw = tmp_path / "raw"
    raw.mkdir()
    sources = []
    for s in rhg.SYMBOLS:
        for tf in rhg.TIMEFRAMES:
            accepted = RAW_HEADER + RAW_ROW
            path = raw / f"{s}_{rhg.RAW_FILE_SUFFIX[tf]}_merged.csv"
            path.write_text(raw_content.get((s, tf), accepted))
            sources.append(
                {
                    "symbol": s,
                    "timeframe": tf,
                    "rows": 1,
                    "sha256": hashlib.sha256(accepted.encode()).hexdigest(),
                }
            )
    manifest = {
        "snapshot_id": snapshot_id,
        "partitions": partitions,
        "resolved_classifications": records,
        "sources": sources,
    }
    (tmp_path / "snap" / snapshot_id).mkdir(parents=True)
    (tmp_path / "snap" / snapshot_id / "manifest.json").write_text(json.dumps(manifest))
    return [
        "--snapshot-root",
        str(tmp_path / "snap"),
        "--snapshot-id",
        snapshot_id,
        "--raw-dir",
        str(raw),
        "--out",
        str(tmp_path / "out"),
    ]


def test_main_writes_csvs_from_manifest(tmp_path: Path) -> None:
    code = rhg.main(_write_snapshot(tmp_path, {}))

    assert code == 0
    out = tmp_path / "out"
    with (out / "gap_intervals.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [(r["symbol"], r["start_utc"], r["end_utc"]) for r in rows] == [
        ("USDJPY", "2019-03-15T20:00Z", "2019-03-15T20:30Z")
    ]
    assert rows[0]["attribution"] == rhg.SOURCE_DATA
    assert rows[0]["raw_rows_in_interval"] == "0"
    with (out / "cross_symbol_overlap.csv").open() as f:
        overlap = list(csv.DictReader(f))
    assert [(r["n_symbols"], r["symbols"]) for r in overlap] == [("1", "USDJPY")]


def test_main_fails_when_raw_csv_differs_from_snapshot_sources(tmp_path: Path) -> None:
    # 受入れ後に書き換えた（区間内に足を足した）原 CSV は snapshot の記録と一致しない
    edited = RAW_HEADER + RAW_ROW + "2019-03-15 20:00:00+00:00,1,1,1,1,0,histdata\n"
    code = rhg.main(_write_snapshot(tmp_path, {("USDJPY", "15m@v1"): edited}))

    assert code == 1
    assert not (tmp_path / "out").exists()
