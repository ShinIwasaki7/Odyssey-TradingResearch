"""mt5_gap_aggregate（代表 112 週の空白の集計）の単体テスト。人工データだけを使う。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tools.survey import mt5_gap_aggregate as agg

ROOT = Path(__file__).resolve().parents[2]
CALENDAR = ROOT / "configs/calendars/fx_ny17_v2.yaml"
TIMEFRAMES = ROOT / "configs/calendars/timeframes_v1.yaml"


@pytest.fixture(scope="module")
def calendar() -> TradingCalendar:
    return load_calendar(CALENDAR)


@pytest.fixture(scope="module")
def timeframes() -> dict[str, TimeframeDefinition]:
    return load_timeframes(TIMEFRAMES)


def _e(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def _request(
    pair: str, sunday: str, bars: list[int], kind: str = "W1", **extra: Any
) -> dict[str, Any]:
    day = date.fromisoformat(sunday)
    end = date.fromordinal(day.toordinal() + 6)
    return {
        "rid": f"G-{pair}-{kind}-{sunday}",
        "stage": "G",
        "pair": pair,
        "kind": "gapweek",
        "date": sunday,
        "state": kind,
        "requested_from_api_time": f"{sunday}T00:00:00+00:00",
        "requested_to_api_time": f"{end.isoformat()}T03:00:00+00:00",
        "bar_times": bars,
        **extra,
    }


def _full_week_api(calendar: TradingCalendar, sunday: str, hours: int) -> list[int]:
    """暦の取引時間の全部の分に足がある週（API の時刻。変換 ``hours`` で UTC からずらす）。"""
    req = _request("USDJPY", sunday, [])
    week = agg.parse_request(req)
    shift = hours * 3600
    lo, hi = week.window_api[0] - shift, week.window_api[1] - shift
    return [t + shift for t in agg.expected_minutes(calendar, lo, hi)]


# --- 時刻の規則の表 版 1 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("pair", "day", "row", "status", "standard", "candidates"),
    [
        ("USDJPY", "2023-06-01", 9, "仮定付き", 3, ()),
        ("EURJPY", "2017-12-27", 8, "仮定付き", 2, ()),
        ("USDJPY", "2019-03-20", 1, "仮定付き", 2, (3,)),
        ("EURJPY", "2019-03-20", 2, "仮定付き", 2, (3,)),
        ("USDJPY", "2023-03-15", 3, "確認済み", 3, ()),
        ("GBPUSD", "2023-03-15", 4, "仮定付き", 3, (2,)),
        ("AUDUSD", "2017-11-01", 5, "仮定付き", 2, (3,)),
        ("USDJPY", "2018-03-20", 6, "仮定付き", 2, (3,)),
        ("USDJPY", "2018-10-31", 6, "仮定付き", 2, (3,)),
        ("USDCHF", "2021-03-20", 7, "仮定付き", 3, (2,)),
        ("USDJPY", "2023-11-01", 7, "仮定付き", 3, (2,)),
    ],
)
def test_time_rule_follows_table_v1(
    pair: str, day: str, row: int, status: str, standard: int, candidates: tuple[int, ...]
) -> None:
    rule = agg.time_rule(pair, date.fromisoformat(day))
    assert rule.row == row
    assert rule.status == status
    assert rule.standard.hours == standard
    assert tuple(c.hours for c in rule.candidates) == candidates


def test_time_rule_rejects_outside_research_period() -> None:
    with pytest.raises(agg.AggregateError):
        agg.time_rule("USDJPY", date(2024, 1, 3))


def test_marks_distinguish_confirmed_assumed_and_same_conversion() -> None:
    assert agg.time_rule("USDJPY", date(2023, 3, 15)).mark().startswith("確認済み")
    same = agg.time_rule("USDJPY", date(2023, 6, 1)).mark()
    assert same.startswith("仮定付き") and "候補の間で変換が同じ" in same
    split = agg.time_rule("EURJPY", date(2019, 3, 20)).mark()
    assert "標準 欧州式 +2" in split and "候補 米国式 +3" in split


def test_week_rule_is_constant_for_representative_weeks() -> None:
    assert agg.week_rule("USDJPY", date(2023, 5, 28)).row == 9
    assert agg.week_rule("USDJPY", date(2023, 12, 24)).row == 8
    assert agg.week_rule("USDJPY", date(2019, 3, 10)).row == 1


# --- 入力（時刻だけ） --------------------------------------------------------------


def test_parse_request_reads_only_time_fields() -> None:
    raw = _request("USDJPY", "2023-05-28", [3, 1, 2], open=[1.0], close=[2.0], rates="x")
    week = agg.parse_request(raw)
    assert week.bar_times_api == (1, 2, 3)
    assert week.kind == "W1" and week.year == 2023
    assert not hasattr(week, "open") and not hasattr(week, "close")


def test_parse_request_rejects_missing_field_and_non_sunday() -> None:
    raw = _request("USDJPY", "2023-05-28", [])
    del raw["bar_times"]
    with pytest.raises(agg.AggregateError):
        agg.parse_request(raw)
    with pytest.raises(agg.AggregateError):
        agg.parse_request(_request("USDJPY", "2023-05-29", []))


# --- 空白の検出・長さ・時間帯 ------------------------------------------------------


def test_gap_runs_and_length_bins() -> None:
    m = 60
    runs = agg.gap_runs([0, m, 2 * m, 10 * m, 30 * m])
    assert runs == [(0, 3 * m), (10 * m, 11 * m), (30 * m, 31 * m)]
    assert [agg.length_bin(n) for n in (1, 2, 3, 5, 6, 15, 16, 60, 61)] == [
        "1",
        "2",
        "3-5",
        "3-5",
        "6-15",
        "6-15",
        "16-60",
        "16-60",
        "61+",
    ]


def test_full_week_has_no_gaps_and_complete_bars(
    calendar: TradingCalendar, timeframes: dict[str, TimeframeDefinition]
) -> None:
    bars = _full_week_api(calendar, "2023-05-28", 3)
    week = agg.parse_request(_request("USDJPY", "2023-05-28", bars))
    result = agg.aggregate_week(week, agg.SUMMER, calendar, timeframes)
    assert result.expected == 5 * 1440
    assert result.missing == 0
    assert result.bars_expected == {"15m": 480, "1h": 120, "4h_ny17": 30, "1d_ny17": 5}
    assert result.bars_complete == result.bars_expected
    assert result.longest_complete_days == 5
    assert result.longest_gap_free_minutes == 5 * 1440


def test_gaps_are_counted_with_length_hour_session_weekday_and_bars(
    calendar: TradingCalendar, timeframes: dict[str, TimeframeDefinition]
) -> None:
    bars = _full_week_api(calendar, "2023-05-28", 3)
    shift = 3 * 3600
    # 火曜の取引日の始まり NY 17:01（= UTC 21:01、月曜）の 1 分と、水曜 UTC 10:00 からの 20 分
    one = _e("2023-05-29T21:01") + shift
    twenty = [_e("2023-05-31T10:00") + 60 * i + shift for i in range(20)]
    removed = {one, *twenty}
    week = agg.parse_request(
        _request("USDJPY", "2023-05-28", [t for t in bars if t not in removed])
    )
    result = agg.aggregate_week(week, agg.SUMMER, calendar, timeframes)
    assert result.missing == 21
    assert result.length_bins == {"1": 1, "16-60": 1}
    assert result.longest_run_minutes == 20
    assert result.by_hour_utc == {21: 1, 10: 20}
    assert result.by_session["rollover_ny17"] == 1
    assert result.by_session["london"] == 20 and result.by_session["newyork"] == 0
    assert result.by_weekday == {"Tue": 1, "Wed": 20}
    assert result.bars_complete["15m"] == 480 - 3
    assert result.bars_complete["1h"] == 120 - 2
    assert result.bars_complete["1d_ny17"] == 3
    assert [d.label for d in result.incomplete_days] == ["2023-05-30", "2023-05-31"]
    assert result.longest_complete_days == 2


def test_calendar_short_session_is_not_a_gap(
    calendar: TradingCalendar, timeframes: dict[str, TimeframeDefinition]
) -> None:
    # 2023-12-25 は版 2 で NY 03:00〜17:00 を閉じる短縮。その分は空白と数えない
    bars = _full_week_api(calendar, "2023-12-24", 2)
    inside_closure = _e("2023-12-25T12:00") + 2 * 3600  # NY 07:00
    assert inside_closure not in bars
    week = agg.parse_request(
        _request("USDJPY", "2023-12-24", sorted([*bars, inside_closure]), kind="W2")
    )
    result = agg.aggregate_week(week, agg.WINTER, calendar, timeframes)
    assert result.expected == 5 * 1440 - 14 * 60
    assert result.missing == 0
    assert result.present_outside_session == 1
    christmas = next(d for d in result.daily if d.label == "2023-12-25")
    assert christmas.expected_minutes == 10 * 60  # 前日 17:00〜03:00 で切り詰めた日足
    assert christmas.complete


def test_undeclared_holiday_is_counted_as_gap(
    calendar: TradingCalendar, timeframes: dict[str, TimeframeDefinition]
) -> None:
    # 版 2 に宣言の無い 2022-12-26 の取引日が丸ごと無い → 空白として数えたまま（推測で除かない）
    bars = _full_week_api(calendar, "2022-12-25", 2)
    lo, hi = _e("2022-12-25T22:00") + 7200, _e("2022-12-26T22:00") + 7200
    week = agg.parse_request(
        _request("USDJPY", "2022-12-25", [t for t in bars if not lo <= t < hi], kind="W2")
    )
    result = agg.aggregate_week(week, agg.WINTER, calendar, timeframes)
    assert result.missing == 1440
    assert result.length_bins == {"61+": 1}
    assert [d.label for d in result.incomplete_days] == ["2022-12-26"]


# --- 候補の規則ごとの集計と印 ------------------------------------------------------


def test_candidates_are_aggregated_separately_and_both_reported(
    calendar: TradingCalendar, timeframes: dict[str, TimeframeDefinition]
) -> None:
    # 2019 年の春の米欧の間（行 1）。足は欧州式 +2 で全部ある → 標準は空白 0、候補 +3 は違う結果
    bars = _full_week_api(calendar, "2019-03-17", 2)
    week = agg.parse_request(_request("USDJPY", "2019-03-17", bars, kind="S2"))
    [report] = agg.aggregate([week], calendar, timeframes)
    assert report.rule.row == 1
    assert [r.conversion.hours for r in report.results] == [2, 3]
    assert report.results[0].missing == 0
    assert report.results[1].missing > 0
    assert not report.candidates_agree
    rows = agg.week_rows([report])
    assert [row["role"] for row in rows] == ["標準", "候補"]
    assert all(row["mark"].startswith("仮定付き") for row in rows)
    text = agg.render_report([report], {})
    assert "米国式 +3" in text and "欧州式 +2" in text


def test_same_conversion_week_has_one_result(
    calendar: TradingCalendar, timeframes: dict[str, TimeframeDefinition]
) -> None:
    week = agg.parse_request(_request("EURJPY", "2023-05-28", []))
    [report] = agg.aggregate([week], calendar, timeframes)
    assert len(report.results) == 1 and report.candidates_agree


# --- 出力（時刻と件数だけ）と実行 ---------------------------------------------------


def test_main_writes_time_and_count_outputs_only(tmp_path: Path, calendar: TradingCalendar) -> None:
    bars = _full_week_api(calendar, "2023-05-28", 3)[5:]
    payload = {
        "stage": "G",
        "requests": [_request("USDJPY", "2023-05-28", bars, bid=[123.456], ask=[123.457])],
    }
    summary = tmp_path / "summary_ts-G.json"
    summary.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "out"
    assert (
        agg.main(
            [
                "--summary",
                str(summary),
                "--out-dir",
                str(out),
                "--calendar",
                str(CALENDAR),
                "--timeframes",
                str(TIMEFRAMES),
            ]
        )
        == 0
    )
    names = {p.name for p in out.iterdir()}
    assert names == {
        "week_metrics.csv",
        "daily_bars.csv",
        "gap_runs.csv",
        "report.md",
        "inputs.json",
    }
    for path in out.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "123.45" not in text
        assert "bid" not in text and "ask" not in text
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "測定範囲の結果" in report and "全期間では未確認のこと" in report
    assert "仮定付き" in report
