"""audit_usdjpy_history（USDJPY の生成経路と欠落の監査スクリプト）の単体テスト。

人工データだけを使う。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tools.ops import audit_usdjpy_history as audit


def _e(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def test_runs_joins_only_touching_bars() -> None:
    a = _e("2019-03-15T20:00")
    assert audit.runs([a + 900, a, a + 2700], 900) == [(a, a + 1800), (a + 2700, a + 3600)]


def test_year_of_end_counts_new_year_midnight_in_previous_year() -> None:
    assert audit.year_of_end(_e("2024-01-01T00:00")) == 2023
    assert audit.year_of_end(_e("2024-01-01T00:15")) == 2024


def test_apply_correction_moves_only_bars_inside_declared_weeks() -> None:
    week = (_e("2019-03-10T20:00"), _e("2019-03-15T20:00"))
    rule = audit.Correction(
        weeks=(week,), shift=3600, series=frozenset({"USDJPY/15m/bid"}), counts={}
    )
    inside, outside = _e("2019-03-10T20:00"), _e("2019-03-15T20:00")
    moved, count = audit.apply_correction([inside, outside], rule, "USDJPY/15m/bid")
    assert moved == [inside + 3600, outside]
    assert count == 1
    untouched, none = audit.apply_correction([inside], rule, "EURUSD/15m/bid")
    assert untouched == [inside] and none == 0


def test_align_m1_picks_offset_with_largest_overlap() -> None:
    # 週の開場 21:00Z。1 分足のラベル 17:00 は UTC−4 なら 21:00Z、UTC−5 なら 22:00Z。
    open_, close = _e("2020-06-07T21:00"), _e("2020-06-12T21:00")
    labels = [_e("2020-06-07T17:00") + 60 * k for k in range(120)]
    raw_15m = {_e("2020-06-07T21:00") + 900 * k for k in range(8)}
    alignment = audit.align_m1(labels, [(open_, close)], raw_15m)
    assert alignment.offset(labels[0]) == 4 * 3600
    assert alignment.utc(labels)[0] == open_
    # 週の外のラベルは既定の UTC−5
    assert alignment.offset(_e("2021-01-01T00:00")) == audit.HISTDATA_OFFSET


def test_overlap_groups_merges_overlapping_timeframes() -> None:
    spans = [(0, 3600), (0, 1800), (7200, 9000), (8000, 9000)]
    assert audit.overlap_groups(spans) == 2


def test_ohlc_rows_report_only_counts(tmp_path: Path) -> None:
    raw = tmp_path / "USDJPY_15m_merged.csv"
    raw.write_text(
        ",open,high,low,close,volume,source\n"
        "2019-06-03 13:00:00+00:00,1.0,3.0,0.5,2.0,0,histdata\n"
        "2019-06-03 13:15:00+00:00,1.0,1.0,1.0,1.0,0,histdata\n",
        encoding="utf-8",
    )
    m1_dir = tmp_path / "histdata" / "HISTDATA_COM_ASCII_USDJPY_M12019"
    m1_dir.mkdir(parents=True)
    # ラベルは UTC−4（夏）で読むと 13:00Z と 13:01Z。2 本目の足の区画には 1 分足が無い。
    (m1_dir / "DAT_ASCII_USDJPY_M1_2019.csv").write_text(
        "20190603 090000;1.0;3.0;1.0;1.5;0\n20190603 090100;1.5;2.0;0.5;2.0;0\n",
        encoding="utf-8",
    )
    merged = audit.read_merged(raw)
    series = audit.SeriesAudit(
        timeframe="15m@v1",
        merged=merged,
        corrected_rows=merged.starts,
        corrected=merged.starts,
        source_at=dict(zip(merged.starts, merged.sources, strict=True)),
        moved=0,
        refill=(),
        expected=merged.starts,
        span=(merged.starts[0], merged.starts[-1] + 900),
    )
    labels, _ = audit.read_m1_labels(tmp_path / "histdata")
    alignment = audit.align_m1(
        labels, [(_e("2019-06-02T21:00"), _e("2019-06-07T21:00"))], set(merged.starts)
    )
    rows = audit.ohlc_rows({"15m@v1": series}, tmp_path / "histdata", alignment)
    assert {(r["verdict"], r["bars"]) for r in rows} == {("EQUAL", 1), ("NO_M1", 1)}
    assert all(
        set(r) == {"timeframe", "year_of_bar_end", "source", "verdict", "bars"} for r in rows
    )


def test_series_year_rows_separates_before_and_after_refill_gaps() -> None:
    """補充前（原 CSV）と補充後の欠落の列が別々に数えられる（補充で値が変わる）。

    期待する足 12 本のうち、原 CSV に無いのは 2 区間（3 本・2 本 ＝ 1.25 時間）。補充分で
    1 区間（3 本）をすべて埋め、もう 1 区間は 1 本だけ埋める。補充後は 1 区間・1 本・0.25 時間。
    """
    step = 900
    base = _e("2020-06-08T00:00")
    expected = tuple(base + step * k for k in range(12))
    absent = {expected[2], expected[3], expected[4], expected[8], expected[9]}
    refill = (expected[2], expected[3], expected[4], expected[8])
    raw = tuple(b for b in expected if b not in absent)
    merged = audit.MergedFile(
        path=Path("USDJPY_15m_merged.csv"),
        sha256="",
        starts=raw,
        sources=tuple("histdata" for _ in raw),
    )
    series = audit.SeriesAudit(
        timeframe="15m@v1",
        merged=merged,
        corrected_rows=raw,
        corrected=raw,
        source_at=dict.fromkeys(raw, "histdata"),
        moved=0,
        refill=refill,
        expected=expected,
        span=(expected[0], expected[-1] + step),
    )
    snapshot = [(expected[9], expected[9] + step)]
    (row,) = audit.series_year_rows("15m@v1", series, snapshot)
    assert row["year_of_bar_end"] == 2020
    assert row["raw_rows"] == 7
    assert row["refill_rows"] == 4
    assert row["expected_bars"] == 12
    assert row["missing_bars_raw_csv"] == 5
    assert row["missing_bars_after_refill"] == 1
    assert row["gap_intervals_raw_csv"] == 2
    assert row["gap_hours_raw_csv"] == 1.25
    assert row["gap_intervals_after_refill"] == 1
    assert row["gap_hours_after_refill"] == 0.25
    assert row["gap_intervals_snapshot"] == 1
    assert row["gap_hours_snapshot"] == 0.25
    assert row["same_intervals"] is True
    # snapshot の記録が補充前の区間のままなら不一致と出る
    raw_like = [(expected[2], expected[5]), (expected[8], expected[10])]
    (stale,) = audit.series_year_rows("15m@v1", series, raw_like)
    assert stale["gap_intervals_raw_csv"] == 2
    assert stale["gap_intervals_after_refill"] == 1
    assert stale["same_intervals"] is False


def test_series_year_rows_counts_year_spanning_gap_in_end_year() -> None:
    """年をまたぐ欠落区間は終端の年に 1 区間として数える（補充前・補充後とも）。"""
    step = 3600
    base = _e("2020-12-31T22:00")
    expected = tuple(base + step * k for k in range(4))  # 22:00, 23:00, 00:00, 01:00
    raw = (expected[0], expected[3])
    merged = audit.MergedFile(
        path=Path("USDJPY_1h_merged.csv"), sha256="", starts=raw, sources=("h", "h")
    )
    series = audit.SeriesAudit(
        timeframe="1h@v1",
        merged=merged,
        corrected_rows=raw,
        corrected=raw,
        source_at=dict.fromkeys(raw, "h"),
        moved=0,
        refill=(),
        expected=expected,
        span=(expected[0], expected[-1] + step),
    )
    rows = {r["year_of_bar_end"]: r for r in audit.series_year_rows("1h@v1", series, [])}
    assert rows[2020]["missing_bars_raw_csv"] == 1 and rows[2020]["gap_intervals_raw_csv"] == 0
    assert rows[2021]["missing_bars_raw_csv"] == 1 and rows[2021]["gap_intervals_raw_csv"] == 1
    assert rows[2021]["gap_hours_raw_csv"] == 2.0
