"""時刻の調査（D12 §11.7）の段・読み・決定木の単体テスト。人工のデータだけを使う。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.survey import mt5_api_probe as probe
from tools.survey import mt5_time_survey as ts

OFFSETS = {"US": {"S1": (2, 2), "S2": (2, 3), "S3": (3, 3), "S4": (3, 3)}}


def _weekend_times(sunday: str, close_off: int, open_off: int) -> list[int]:
    """金曜の閉場（NY 17:00）の前 3 時間と、日曜の開場の後 3 時間の 1 分足の時刻（API の値）。"""
    sun = datetime.fromisoformat(sunday)
    close = ts._ny_17(sun - timedelta(days=2)).timestamp() + close_off * 3600
    open_ = ts._ny_17(sun).timestamp() + open_off * 3600
    fri = [int(close - 60 * k) for k in range(180, 0, -1)]
    mon = [int(open_ + 60 * k) for k in range(180)]
    return fri + mon


def _rec(
    pair: str, kind: str, date: str, state: str, times: list[int], **kw: Any
) -> dict[str, Any]:
    return {
        "rid": f"T-{pair}-{kind}-{date}",
        "stage": "T",
        "pair": pair,
        "kind": kind,
        "date": date,
        "state": state,
        "returned_count": len(times),
        "bar_times": times,
        "symbol_chart_mode": 0,
        "skipped": None,
        **kw,
    }


def test_stages_have_67_requests_all_before_2024() -> None:
    reqs = [r for v in probe.TIME_SURVEY_STAGES.values() for r in v]
    assert {k: len(v) for k, v in probe.TIME_SURVEY_STAGES.items()} == {
        "A2": 10,
        "A1": 23,
        "B1": 28,
        "B2": 6,
    }
    assert len({r.rid for r in reqs}) == 67
    for r in reqs:
        w = r.window()
        probe.check_window(w)
        if r.kind == "weekend":
            assert w.start.weekday() == 4 and w.start.hour == 18
            assert w.end.weekday() == 0 and w.end.hour == 2
        else:
            assert w.start.weekday() == 1 and w.end - w.start == timedelta(hours=2)
    # 新しい年から（A2 は 2023 が先）
    assert probe.TIME_SURVEY_STAGES["A2"][0].date.startswith("2023")


def test_wrong_weekday_and_2024_plan_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        probe.SurveyRequest("x", "X", "USDJPY", "weekend", "2019-03-11", "S2").window()
    bad = tmp_path / "plan.json"
    item = {"rid": "x", "stage": "X", "pair": "USDJPY", "kind": "tuesday"}
    bad.write_text(json.dumps([{**item, "date": "2024-01-02", "state": "S1"}]), encoding="utf-8")
    with pytest.raises(probe.RequestOutOfRangeError):
        probe.load_plan_file(bad)


@pytest.mark.parametrize(
    ("sunday", "state", "close_off", "open_off", "expected"),
    [
        ("2019-03-10", "S2", 2, 3, ["US"]),
        ("2019-03-10", "S2", 2, 2, ["EU", "FIXED"]),
        ("2019-10-27", "S4", 3, 2, ["EU"]),
        ("2019-07-14", "S3", 2, 2, ["FIXED"]),
    ],
)
def test_read_weekend_matches_the_hypothesis_table(
    sunday: str, state: str, close_off: int, open_off: int, expected: list[str]
) -> None:
    w = ts.read_weekend(_weekend_times(sunday, close_off, open_off), sunday, state)
    assert w.readable and w.hypotheses == expected
    assert (w.close_offset_h, w.open_offset_h) == (close_off, open_off)


def test_blurred_boundary_is_unreadable() -> None:
    times = _weekend_times("2019-01-13", 2, 2)
    times = [t for t in times if t < times[179] - 40 * 60] + times[180:]  # 金曜の最後の 40 分が無い
    w = ts.read_weekend(times, "2019-01-13", "S1")
    assert not w.readable and w.reason == "boundary_blurred"


def _year_records(pair: str, year: int, hyp: str = "US") -> list[dict[str, Any]]:
    out = []
    for i, st in enumerate(probe.STATES):
        d = probe._YEAR_SUNDAYS[year][i]
        out.append(_rec(pair, "weekend", d, st, _weekend_times(d, *OFFSETS[hyp][st])))
    return out


def test_year_confirmed_and_other_pair_confirmed_by_e2_e3_e4() -> None:
    recs = _year_records("USDJPY", 2023)
    for st in ("S2", "S3"):
        d = probe._YEAR_SUNDAYS[2023][probe.STATES.index(st)]
        recs.append(_rec("EURUSD", "weekend", d, st, _weekend_times(d, *OFFSETS["US"][st])))
    ev = ts.evaluate(recs)
    assert ev.years[("USDJPY", 2023)]["status"] == "confirmed"
    assert ev.years[("USDJPY", 2023)]["rule"] == "US"
    assert ev.years[("EURUSD", 2023)]["status"] == "confirmed"
    assert ev.auto_plan == [] and ev.needs_coordinator == []


def test_t2_and_t5_add_requests_and_t7_caps_them() -> None:
    recs = _year_records("USDJPY", 2019)
    recs[0]["bar_times"] = recs[0]["bar_times"][:10]  # S1 が読めない
    ev = ts.evaluate(recs)
    kinds = {b["T"] for b in ev.branches}
    assert {"T2", "T5"} <= kinds
    rids = [a["rid"] for a in ev.auto_plan]
    assert "X-USDJPY-T2019-03-12" in rids and "X-USDJPY-T2019-01-15" in rids
    capped = ts.evaluate(recs, extra_used=probe.TIME_SURVEY_EXTRA_REQUESTS - 1)
    assert len(capped.auto_plan) == 1 and any(c.startswith("T7") for c in capped.needs_coordinator)


def test_other_pair_differing_from_usdjpy_triggers_t4() -> None:
    recs = _year_records("USDJPY", 2023)
    d = probe._YEAR_SUNDAYS[2023][1]
    recs.append(_rec("GBPUSD", "weekend", d, "S2", _weekend_times(d, 2, 2)))
    ev = ts.evaluate(recs)
    assert any(b["T"] == "T4" for b in ev.branches)
    assert {a["rid"] for a in ev.auto_plan} == {"X-GBPUSD-W2023-01-15", "X-GBPUSD-W2023-10-29"}


def test_year_without_single_hypothesis_needs_coordinator_t3() -> None:
    recs = _year_records("USDJPY", 2021)
    d = probe._YEAR_SUNDAYS[2021][3]
    recs[3] = _rec("USDJPY", "weekend", d, "S4", _weekend_times(d, 2, 2))  # 秋だけ固定
    recs[2] = _rec(
        "USDJPY",
        "weekend",
        probe._YEAR_SUNDAYS[2021][2],
        "S3",
        _weekend_times(probe._YEAR_SUNDAYS[2021][2], 3, 3),
    )
    ev = ts.evaluate(recs)
    assert any(b["T"] == "T3" for b in ev.branches)
    assert ev.years[("USDJPY", 2021)]["status"] == "unresolved"
    assert any(c.startswith("T3") for c in ev.needs_coordinator)


def test_e1_stats_find_the_shift_without_returning_prices() -> None:
    base = int(datetime(2023, 3, 7, tzinfo=UTC).timestamp())
    ref = {base + 60 * k: ["150.101", "150.105", "150.099", "150.102"] for k in range(120)}
    api_rows = [
        {"time": t + 3 * 3600, "open": 150.101, "high": 150.105, "low": 150.099, "close": 150.102}
        for t in ref
    ]
    stats = probe.e1_stats(api_rows, ref, 3, 0.001)
    best = max(stats, key=lambda s: s["ohlc_exact"])
    assert best["shift_hours"] == 3 and best["ohlc_exact"] == 120
    assert "150.1" not in json.dumps(stats)
    reading = ts.read_e1(stats, "S2")
    assert reading is not None and reading["matched"] and reading["hypotheses"] == ["US"]


class FakeApi:
    COPY_TICKS_ALL = 1
    TIMEFRAME_M1 = 1

    def __init__(self) -> None:
        self.calls: list[str] = []

    def initialize(self) -> bool:
        return True

    def version(self) -> Any:
        return (5, 0, "t")

    def terminal_info(self) -> Any:
        return SimpleNamespace(build=1, name="t", company="c", maxbars=1, connected=True)

    def symbols_get(self, group: str) -> Any:
        return [SimpleNamespace(name=group.strip("*") + ".cl")]

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(chart_mode=0, point=0.001, digits=3)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def copy_ticks_range(self, *a: Any) -> Any:
        self.calls.append("copy_ticks_range")
        return []

    def copy_rates_range(self, symbol: str, tf: int, start: datetime, end: datetime) -> Any:
        self.calls.append("copy_rates_range")
        t = int(start.timestamp())
        return [{"time": t, "open": 1.234, "high": 1.3, "low": 1.2, "close": 1.25}]

    def last_error(self) -> Any:
        return (1, "Success")

    def shutdown(self) -> Any:
        return None


def test_run_time_survey_keeps_times_only_and_stops_at_limits() -> None:
    api = FakeApi()
    plan = list(probe.TIME_SURVEY_STAGES["A2"])
    recs = probe.run_time_survey(api, plan, max_requests=3)
    assert api.calls == ["copy_rates_range"] * 3
    assert [r.skipped for r in recs[:3]] == [None] * 3
    assert all(r.skipped == "request_limit" for r in recs[3:])
    text = json.dumps([asdict(r) for r in recs])
    assert "1.234" not in text and "1.25" not in text
    assert recs[0].bar_times and recs[0].symbol == "USDJPY.cl"
    frees = iter([10 * 1024**3, 10 * 1024**3, 1 * 1024**3] + [1 * 1024**3] * 40)
    api2 = FakeApi()
    recs2 = probe.run_time_survey(api2, plan, max_requests=99, disk_free=lambda: next(frees))
    assert recs2[0].skipped is None and recs2[1].skipped == "low_disk"
    assert api2.calls == ["copy_rates_range"]


def test_gap_stage_has_112_weeks_sunday_to_saturday_before_2024() -> None:
    reqs = probe.GAP_STAGE
    assert len(reqs) == probe.GAP_REQUESTS == 112
    assert len({r.rid for r in reqs}) == 112
    for r in reqs:
        w = r.window()
        probe.check_window(w)
        assert w.start.weekday() == 6 and w.start.hour == 0
        assert w.end - w.start == timedelta(days=6, hours=3)
    w1 = [datetime.fromisoformat(r.date) for r in reqs if r.state == "W1"]
    # W1 の週の金曜は 6 月の第 1 金曜
    assert all((d + timedelta(days=5)).month == 6 and (d + timedelta(days=5)).day <= 7 for d in w1)
    w2 = [datetime.fromisoformat(r.date) for r in reqs if r.state == "W2"]
    assert all(d.month == 12 and 22 <= d.day <= 28 for d in w2)
    assert max(r.window().end for r in reqs) <= datetime(2023, 12, 31, tzinfo=UTC)


def test_gap_weeks_are_ignored_by_the_time_evaluation() -> None:
    d = probe.GAP_STAGE[0]
    rec = _rec(d.pair, "gapweek", d.date, d.state, [1, 2, 3])
    ev = ts.evaluate([rec])
    assert ev.rows == [] and ev.auto_plan == []
