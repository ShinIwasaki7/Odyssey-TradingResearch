"""refill_report（補充の後の残存欠落と連続期間の報告。D03 §14.15）の単体テスト。

人工の旧・新 snapshot の manifest、補充分の manifest と検証記録、不合格の計画の取得記録から、
残存欠落の区間ごとの理由と報告の本文を作る。実データには触れない。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from tools.ops import refill_report as rr
from tools.ops import research_history_gaps as rhg

OLD = "o" * 64
NEW = "n" * 64
REFILL = "a" * 64
REJECTED = "b" * 64


def _record(symbol: str, tf: str, start: str, end: str) -> dict[str, Any]:
    return {
        "series_id": {"symbol": symbol, "timeframe": tf, "basis": "bid"},
        "interval": {"start": start, "end": end},
        "kind": "MISSING_EXPECTED_BAR",
        "outcome": "DATA_GAP",
    }


def _manifest(
    snapshot_id: str, records: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "conversion": {"calendar_id": "fx_ny17", "calendar_version": 2},
        "partitions": [
            {
                "partition_id": {
                    "access_class": "RESEARCH_HISTORY",
                    "series": {"symbol": s, "timeframe": tf, "basis": "bid"},
                },
                "interval": {"start": "2020-01-01T00:00:00Z", "end": "2021-01-01T00:00:00Z"},
            }
            for s in rhg.SYMBOLS
            for tf in rhg.TIMEFRAMES
        ],
        "resolved_classifications": records,
        "sources": sources,
    }


def _write(tmp_path: Path) -> list[str]:
    snapshots = tmp_path / "snapshots"
    # 旧: USDJPY 15m に 3 本（2 区間）の欠落と、AUDJPY 1h に 1 本。
    old_records = [
        _record("USDJPY", "15m@v1", "2020-06-01T10:00:00Z", "2020-06-01T10:15:00Z"),
        _record("USDJPY", "15m@v1", "2020-06-01T10:15:00Z", "2020-06-01T10:30:00Z"),
        _record("USDJPY", "15m@v1", "2020-07-01T10:00:00Z", "2020-07-01T10:15:00Z"),
        _record("AUDJPY", "1h@v1", "2020-08-03T10:00:00Z", "2020-08-03T11:00:00Z"),
    ]
    # 新: 06-01 10:00 は補充できた。10:15 は取得できず、07-01 は未照合、AUDJPY は不合格の計画。
    new_records = [old_records[1], old_records[2], old_records[3]]
    sources = [
        {"path": f"data/raw/market/refill/{REFILL}/USDJPY_15m_refill.csv", "symbol": "USDJPY"},
        {"path": "data/raw/market/USDJPY_15m_merged.csv", "symbol": "USDJPY"},
    ]
    for snapshot_id, records in ((OLD, old_records), (NEW, new_records)):
        (snapshots / snapshot_id).mkdir(parents=True)
        (snapshots / snapshot_id / "manifest.json").write_text(
            json.dumps(_manifest(snapshot_id, records, sources if snapshot_id == NEW else []))
        )
    refill = tmp_path / "refill" / REFILL
    refill.mkdir(parents=True)
    series = {"series": "USDJPY/15m/bid", "timeframe_version": 1}
    (refill / "refill_manifest.json").write_text(
        json.dumps(
            {
                "refill_id": REFILL,
                "series_counts": [{**series, "targets": 3, "built": 1, "not_built": 2}],
                "not_built": [
                    {
                        **series,
                        "start": "2020-06-01T10:15:00Z",
                        "reason": "HOUR_NOT_FETCHED",
                        "detail": "HTTP_404",
                    },
                    {
                        **series,
                        "start": "2020-07-01T10:00:00Z",
                        "reason": "UNRECONCILED",
                        "detail": "",
                    },
                ],
                "unreconciled": [
                    {**series, "chunk_start": "2020-07-01T10:00:00Z", "target_count": 1}
                ],
            }
        )
    )
    (refill / "validation.json").write_text(
        json.dumps(
            {
                "reconciled_count": 8,
                "matched_count": 8,
                "out_of_range_tick_count": 0,
                "bid_above_ask_tick_count": 0,
                "neighbors": [
                    {
                        **series,
                        "chunk_start": "2020-06-01T10:00:00Z",
                        "side": "BEFORE",
                        "difference_pips": "12.5",
                        "needs_review": True,
                    }
                ],
            }
        )
    )
    work = tmp_path / "refill/_work" / REJECTED
    work.mkdir(parents=True)
    (work / "plan.json").write_text(
        json.dumps(
            {
                "target_bars": [
                    {
                        "series": "AUDJPY/1h/bid",
                        "timeframe_version": 1,
                        "start": "2020-08-03T10:00:00Z",
                    }
                ]
            }
        )
    )
    failed = {
        "kind": "validation",
        "passed": False,
        "reasons": ["1 reconciliation bar(s) differ from the raw data"],
        "details": {"not_built": [], "unreconciled": [], "mismatches": []},
    }
    (work / "journal.jsonl").write_text(json.dumps({"digest": "x", "entry": failed}) + "\n")
    return [
        "--snapshot-root",
        str(snapshots),
        "--snapshot-id",
        NEW,
        "--previous-snapshot-id",
        OLD,
        "--refill-root",
        str(tmp_path / "refill"),
        "--rejected-plan",
        REJECTED,
        "--out",
        str(tmp_path / "out"),
    ]


def test_residual_gaps_carry_their_reasons(tmp_path: Path) -> None:
    assert rr.main(_write(tmp_path)) == 0
    with (tmp_path / "out/residual_gaps.csv").open() as f:
        rows = {
            (r["symbol"], r["timeframe"], r["start_utc"]): r["reason"] for r in csv.DictReader(f)
        }
    assert rows == {
        ("AUDJPY", "1h@v1", "2020-08-03T10:00Z"): rr.VALIDATION_REJECTED,
        ("USDJPY", "15m@v1", "2020-06-01T10:15Z"): rr.NOT_FETCHED,
        ("USDJPY", "15m@v1", "2020-07-01T10:00Z"): rr.UNRECONCILED,
    }


def test_the_report_has_the_five_sections_and_no_prices(tmp_path: Path) -> None:
    assert rr.main(_write(tmp_path)) == 0
    report = (tmp_path / "out/refill_report.md").read_text(encoding="utf-8")
    for heading in (
        "## 1. 入力と出力の識別",
        "## 2. 補充の結果",
        "## 3. 残存欠落",
        "## 4. 実行可能な連続期間",
        "## 5. 再現",
    ):
        assert heading in report
    assert f"`{REFILL}`" in report and f"`{REJECTED}`" in report
    assert "| USDJPY/15m/bid | 3 | 1 | 2 | HOUR_NOT_FETCHED 1, UNRECONCILED 1 |" in report
    assert "要確認: USDJPY/15m/bid 2020-06-01T10:00:00Z BEFORE 差 12.5 pip" in report
    assert "| USDJPY 15m@v1 | 2 / 0.75h | 2 / 0.50h |" in report
    assert "戦略の成績" in report


def test_a_bar_outside_any_refill_is_not_planned() -> None:
    gap = rhg.GapInterval(
        "USDJPY",
        "1h@v1",
        rhg.parse_utc("2020-06-02T10:00:00Z"),
        rhg.parse_utc("2020-06-02T12:00:00Z"),
    )
    reasons = {("USDJPY", "1h@v1", rhg.parse_utc("2020-06-02T10:00:00Z")): rr.NOT_FETCHED}
    assert rr.interval_reason(gap, reasons) == f"{rr.NOT_FETCHED}|{rr.NOT_PLANNED}"


def test_missing_inputs_fail_without_writing(tmp_path: Path) -> None:
    args = _write(tmp_path)
    (tmp_path / "refill" / REFILL / "validation.json").unlink()
    assert rr.main(args) == 1
    assert not (tmp_path / "out").exists()
