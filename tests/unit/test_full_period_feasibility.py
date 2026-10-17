"""full_period_feasibility（全期間のデータ上の実行可能性の先行統計）の単体テスト。人工データだけ。"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app.config.calendars import load_calendar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from tools.survey import full_period_feasibility as fpf
from tools.survey.mt5_api_probe import RATE_FIELDS

ROOT = Path(__file__).resolve().parents[2]
CALENDAR = ROOT / "configs/calendars/fx_ny17_v2.yaml"
HEADER = ",".join(RATE_FIELDS)


def _e(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


@pytest.fixture(scope="module")
def calendar() -> TradingCalendar:
    return load_calendar(CALENDAR)


# --- 時刻の変換 ---------------------------------------------------------------------


def test_offset_hours_between_weeks_differ_by_clock() -> None:
    between = _e("2019-03-12T10:00:00")  # 米国だけ夏
    assert fpf.offset_hours(fpf.ClockFamily.EU, between) == 2
    assert fpf.offset_hours(fpf.ClockFamily.US, between) == 3
    winter = _e("2019-01-15T10:00:00")
    summer = _e("2019-07-02T10:00:00")
    for family in fpf.ClockFamily:
        assert fpf.offset_hours(family, winter) == 2
        assert fpf.offset_hours(family, summer) == 3
    assert fpf.to_utc(between, fpf.ClockFamily.US) == between - 3 * 3600


def test_divergent_spans_cover_us_eu_between_weeks() -> None:
    spans = fpf.divergent_spans()
    assert len(spans) == 14  # 7 年 × 春・秋
    firsts = {(s.first_day, s.last_day) for s in spans}
    assert (date(2017, 3, 12), date(2017, 3, 25)) in firsts
    assert (date(2019, 3, 10), date(2019, 3, 30)) in firsts
    assert (date(2023, 10, 29), date(2023, 11, 4)) in firsts
    spring = spans[0]
    assert spring.season == "spring"
    lo, hi = spring.utc_bounds()
    assert fpf.iso(lo) == "2017-03-11T21:00Z"
    assert fpf.iso(hi) == "2017-03-25T22:00Z"


# --- 読み込み（時刻だけ） -------------------------------------------------------------


def test_read_time_column_reads_only_first_field() -> None:
    data = f"{HEADER}\n60,x,x,x,x,0,0,0\nbad,0,0,0,0,0,0,0\n".encode()
    times, bad = fpf.read_time_column(data)
    assert times == [60]
    assert bad == 1
    with pytest.raises(fpf.FeasibilityError):
        fpf.read_time_column(b"time,open\n60,0\n")


def _write_month(
    root: Path, pair: str, year: int, month: int, times: list[int], *, status: str = "ok"
) -> dict[str, Any]:
    rel = f"{pair}/{pair}_{year}-{month:02d}_m1.csv.gz"
    body = (HEADER + "\n" + "".join(f"{t},0,0,0,0,0,0,0\n" for t in times)).encode()
    gz = gzip.compress(body, mtime=0)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gz)
    nxt = (year + month // 12, month % 12 + 1)
    return {
        "rid": f"F-{pair}-{year}-{month:02d}",
        "pair": pair,
        "year": year,
        "month": month,
        "file": rel,
        "status": status,
        "requested_from_api_time": f"{year}-{month:02d}-01T00:00:00+00:00",
        "requested_to_api_time": f"{nxt[0]}-{nxt[1]:02d}-01T00:00:00+00:00",
        "rows": len(times),
        "sha256_gz": hashlib.sha256(gz).hexdigest(),
        "sha256_csv": hashlib.sha256(body).hexdigest(),
    }


def test_read_pair_separates_shortfall_and_counts_time_problems(tmp_path: Path) -> None:
    jan = _e("2019-01-07T00:00:00")
    good = _write_month(tmp_path, "USDJPY", 2019, 1, [jan, jan, jan + 30, jan + 60])
    broken = _write_month(tmp_path, "USDJPY", 2019, 2, [_e("2019-02-04T00:00:00")])
    broken["sha256_gz"] = "0" * 64
    failed = _write_month(tmp_path, "USDJPY", 2019, 3, [], status="error")
    manifest = {"files": {e["rid"]: e for e in (good, broken, failed)}}
    series = fpf.read_pair(tmp_path, manifest, "USDJPY")
    assert series.times_api == [jan, jan + 60]
    assert series.duplicates == 1
    assert series.unaligned == 1
    assert [w.reason for w in series.shortfall_windows] == [
        "gzip の SHA-256 が合わない",
        "状態 error",
    ]
    assert len(series.acquired_windows) == 1
    with pytest.raises(fpf.FeasibilityError):
        fpf.read_pair(tmp_path, manifest, "EURUSD")


def test_convert_series_keeps_raw_times(tmp_path: Path) -> None:
    t = _e("2019-03-12T10:00:00")
    entry = _write_month(tmp_path, "USDJPY", 2019, 3, [t])
    series = fpf.read_pair(tmp_path, {"files": {entry["rid"]: entry}}, "USDJPY")
    eu, eu_windows, shortfall = fpf.convert_series(series, fpf.FamilyConversion(fpf.ClockFamily.EU))
    us, _, _ = fpf.convert_series(series, fpf.FamilyConversion(fpf.ClockFamily.US))
    assert eu == [t - 2 * 3600]
    assert us == [t - 3 * 3600]
    assert series.times_api == [t]  # 生の時刻は変えない
    assert fpf.iso(eu_windows[0][0]) == "2019-02-28T22:00Z"  # 3 月 1 日は両方冬
    assert shortfall == []


# --- 分の状態と統計 -----------------------------------------------------------------


def test_classify_minutes_distinguishes_no_record_shortfall_holiday() -> None:
    base = _e("2019-01-08T00:00:00")
    expected = [base + 60 * i for i in range(10)]
    acquired = [(base, base + 6 * 60)]  # 6 分目以降は取得していない
    holidays = {base + 60}
    utc = [base, base + 60, base + 120, base + 5 * 60, base - 60]
    status = fpf.classify_minutes("USDJPY", "eu_clock", utc, acquired, expected, holidays)
    assert status.recorded == [base, base + 120, base + 5 * 60]
    assert status.no_record == [base + 180, base + 240]
    assert status.shortfall == [base + 60 * i for i in range(6, 10)]
    assert status.holiday == [base + 60]
    assert status.holiday_present == [base + 60]
    assert status.outside == [base - 60]

    monthly = fpf.monthly_rows(status, utc)
    jan = next(r for r in monthly if (r["year"], r["month"]) == (2019, 1))
    assert jan["no_record_minutes"] == 2
    assert jan["no_record_runs"] == 1
    assert jan["longest_no_record_run_minutes"] == 2
    assert jan["shortfall_minutes"] == 4
    assert jan["expected_minutes"] == 9  # 休場の定義の 1 分を除く
    assert jan["holiday_def_minutes"] == 1
    assert len(monthly) == 84

    dist = fpf.run_distribution_rows(status)
    y2019 = next(r for r in dist if r["year"] == 2019)
    assert y2019["runs_2"] == 1
    assert y2019["runs_total"] == 1


def test_run_bin_and_long_runs() -> None:
    assert fpf.run_bin(1) == "1"
    assert fpf.run_bin(61) == "61-180"
    assert fpf.run_bin(5000) == "1441+"
    with pytest.raises(fpf.FeasibilityError):
        fpf.run_bin(0)
    base = _e("2019-01-08T00:00:00")
    status = fpf.MinuteStatus(
        pair="USDJPY",
        conversion="us_clock",
        expected=[],
        no_record=[base + 60 * i for i in range(61)],
    )
    rows = fpf.long_run_rows(status)
    assert rows[0]["minutes"] == 61
    assert rows[0]["start_new_york"] == "2019-01-07 19:00"


def test_daily_rows_counts_by_trading_day() -> None:
    day = fpf.TradingDay(
        label="2019-01-08", start=_e("2019-01-07T22:00:00"), end=_e("2019-01-08T22:00:00")
    )
    minutes = list(range(day.start, day.end, 60))
    status = fpf.MinuteStatus(
        pair="USDJPY",
        conversion="eu_clock",
        expected=minutes,
        recorded=minutes[5:],
        no_record=minutes[:5],
    )
    (row,) = fpf.daily_rows(status, [day])
    assert row["expected_minutes"] == 1440
    assert row["recorded_minutes"] == 1435
    assert row["no_record_minutes"] == 5
    assert row["first_recorded_utc"] == "2019-01-07T22:05Z"
    assert row["longest_no_record_run_minutes"] == 5
    assert row["interval_end_utc"] == "2019-01-08T22:00Z"


def test_trading_days_skip_calendar_closures(
    calendar: TradingCalendar,
) -> None:
    from odyssey_fx.app.config.calendars import load_timeframes

    days = fpf.trading_days(
        calendar, load_timeframes(ROOT / "configs/calendars/timeframes_v1.yaml")
    )
    labels = {d.label for d in days}
    assert "2017-12-25" not in labels  # 版 2 の取引日単位の休場
    assert "2017-01-02" in labels
    assert all(d.start < d.end for d in days)


def test_divergent_rows_compare_clocks() -> None:
    span = fpf.DivergentSpan(date(2021, 3, 14), date(2021, 3, 27))
    lo, _ = span.utc_bounds()
    minute = lo + 24 * 3600
    eu = fpf.MinuteStatus("EURUSD", "eu_clock", expected=[minute], no_record=[minute])
    us = fpf.MinuteStatus("EURUSD", "us_clock", expected=[minute], recorded=[minute])
    (row,) = fpf.divergent_rows("EURUSD", {fpf.ClockFamily.EU: eu, fpf.ClockFamily.US: us}, [span])
    assert row["table_v1_row"] == 7
    assert row["table_v1_standard"] == "米国式+3"
    assert row["eu_no_record_minutes"] == 1
    assert row["us_no_record_minutes"] == 0
    assert row["no_record_count_differs"] is True
    assert row["no_record_positions_differ"] is True
    moved = fpf.MinuteStatus("EURUSD", "us_clock", expected=[minute], no_record=[minute + 60])
    (same_count,) = fpf.divergent_rows(
        "EURUSD", {fpf.ClockFamily.EU: eu, fpf.ClockFamily.US: moved}, [span]
    )
    assert same_count["no_record_count_differs"] is False
    assert same_count["no_record_positions_differ"] is True


def test_write_prelim_outputs_no_price_columns(tmp_path: Path) -> None:
    counts = fpf.write_prelim(fpf.PrelimResult(verify_problems=[]), tmp_path)
    assert set(counts) == {
        "coverage_by_pair.csv",
        "monthly_stats.csv",
        "no_record_run_distribution.csv",
        "long_no_record_runs.csv",
        "daily_coverage.csv",
        "candidate_divergent_spans.csv",
    }
    price_words = {"open", "high", "low", "close", "spread", "bid", "ask", "price"}
    for name in counts:
        header = (tmp_path / name).read_text(encoding="utf-8").splitlines()[0].split(",")
        assert not price_words & set(header)
    assert json.dumps(counts)


def test_root_label_drops_home_directory() -> None:
    assert (
        fpf.root_label("/Users/x/repo/data/raw/oanda_mt5/2026-10-16")
        == "data/raw/oanda_mt5/2026-10-16"
    )
    assert fpf.root_label("--pairs") == "--pairs"


def test_render_summary_from_synthetic_result() -> None:
    base = _e("2019-01-08T00:00:00")
    eu = fpf.MinuteStatus(
        pair="USDJPY",
        conversion="eu_clock",
        expected=[base, base + 60],
        recorded=[base],
        no_record=[base + 60],
    )
    us = fpf.MinuteStatus(
        pair="USDJPY", conversion="us_clock", expected=[base, base + 60], recorded=[base, base + 60]
    )
    day = fpf.TradingDay(label="2019-01-08", start=base, end=base + 120)
    result = fpf.PrelimResult(verify_problems=[])
    result.coverage.append(
        {
            "pair": "USDJPY",
            "files_listed": 1,
            "files_shortfall": 0,
            "bars_used": 2,
            "duplicates": 0,
            "unaligned": 0,
            "unparseable_rows": 0,
            "out_of_window": 0,
            "first_bar_api": "a",
            "last_bar_api": "b",
            "first_bar_utc_eu": "c",
            "first_bar_utc_us": "d",
            "last_bar_utc_eu": "e",
            "last_bar_utc_us": "f",
        }
    )
    for s in (eu, us):
        result.monthly.extend(fpf.monthly_rows(s, s.recorded))
        result.run_dist.extend(fpf.run_distribution_rows(s))
        result.daily.extend(fpf.daily_rows(s, [day]))
    record = {
        "inputs": {
            "manifest_sha256": "m",
            "fetch_log_sha256": "l",
            "calendar": {"path": "c", "sha256": "1"},
            "timeframes": {"path": "t", "sha256": "2"},
            "holiday_definition": None,
            "time_rule_table": "x",
            "counting_rules": "未確定（PR-A）",
        },
        "command": "cmd",
        "raw_root": "data/raw/x",
        "target_commit": "abc",
        "tool_sha256": "def",
        "outputs": {"monthly_stats.csv": 168},
    }
    text = fpf.render_summary(result, record)
    assert "| USDJPY | eu_clock | 2 | 1 | 50.000% | 1 | 1 | 0（なし） | 0 | 0 |" in text
    assert "| USDJPY | us_clock | 2 | 0 | 0.000% | 1 | 0 | 0（なし） | 0 | 0 |" in text
    assert "最終判定ではない" in text
    assert "full_period_final.py" in text
