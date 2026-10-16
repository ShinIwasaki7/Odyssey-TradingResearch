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
        "executed_at_utc": "2026-10-16T00:00:00+00:00",
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
    recs = probe.run_time_survey(api, plan, max_requests=3, retry_waits=())
    assert api.calls == ["copy_rates_range"] * 3
    assert [r.skipped for r in recs[:3]] == [None] * 3
    assert all(r.skipped == "request_limit" for r in recs[3:])
    text = json.dumps([asdict(r) for r in recs])
    assert "1.234" not in text and "1.25" not in text
    assert recs[0].bar_times and recs[0].symbol == "USDJPY.cl"
    frees = iter([10 * 1024**3, 10 * 1024**3, 1 * 1024**3] + [1 * 1024**3] * 40)
    api2 = FakeApi()
    recs2 = probe.run_time_survey(
        api2, plan, max_requests=99, disk_free=lambda: next(frees), retry_waits=()
    )
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


def test_empty_window_is_retried_without_counting_a_new_request() -> None:
    class SyncingApi(FakeApi):
        def __init__(self) -> None:
            super().__init__()
            self.n = 0

        def copy_rates_range(self, symbol: str, tf: int, start: datetime, end: datetime) -> Any:
            self.n += 1
            self.calls.append("copy_rates_range")
            if self.n < 3:  # 取り寄せ中は区間の外の 1 本だけ
                return [{"time": 1_783_647_000, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}]
            return super().copy_rates_range(symbol, tf, start, end)[:0] + [
                {"time": int(start.timestamp()), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
            ]

    api = SyncingApi()
    plan = list(probe.TIME_SURVEY_STAGES["A2"])[:2]
    recs = probe.run_time_survey(api, plan, max_requests=1, retry_waits=(0, 0, 0))
    assert recs[0].returned_count == 1 and recs[0].retries == 2
    assert recs[1].skipped == "request_limit"


def test_latest_record_wins_when_a_request_is_repeated() -> None:
    d = probe._YEAR_SUNDAYS[2023][0]
    old = _rec("USDJPY", "weekend", d, "S1", [], executed_at_utc="2026-10-16T00:00:00+00:00")
    new = _rec(
        "USDJPY",
        "weekend",
        d,
        "S1",
        _weekend_times(d, 2, 2),
        executed_at_utc="2026-10-16T01:00:00+00:00",
    )
    ev = ts.evaluate([new, old])
    assert len(ev.rows) == 1 and ev.rows[0]["readable"]


def test_sync_trigger_requests_ticks_once_and_keeps_no_values() -> None:
    class TickApi(FakeApi):
        def copy_ticks_range(self, *a: Any) -> Any:
            self.calls.append("copy_ticks_range")
            t = int(probe.SYNC_TRIGGER_WINDOW.start.timestamp())
            return [{"time": t, "bid": 141.123, "ask": 141.126}]

    api = TickApi()
    out = probe.sync_trigger(api)
    assert api.calls == ["copy_ticks_range"]
    assert out["count"] == 1 and out["symbol"] == "USDJPY.cl"
    assert "141.12" not in json.dumps(out)
    assert probe.SYNC_TRIGGER_WINDOW.end <= probe.REQUEST_END_LIMIT


# --- 人間の第 11 回の決定（2026-10-16）による改め -------------------------------------------------


def _e1_diff_only(best: int) -> list[dict[str, Any]]:
    """4 値の完全一致 0 本で、ずれ ``best`` の差だけが小さい E1 の統計（人工）。"""
    return [
        {
            "shift_hours": h,
            "compared": 120,
            "ohlc_exact": 0,
            "median_abs_close_diff_points": 4.0 if h == best else 50.0,
        }
        for h in probe.E1_SHIFTS_HOURS
    ]


def test_diff_only_e1_is_recorded_but_not_used_to_confirm() -> None:
    reading = ts.read_e1(_e1_diff_only(2), "S2")
    assert reading is not None
    assert not reading["matched"] and reading["hypotheses"] == []
    assert reading["evidence"] == "diff_only"
    assert reading["diff_best_shift_hours"] == 2
    assert reading["diff_hypotheses"] == ["EU", "FIXED"]
    assert reading["diff_separation"] == 12.5


def test_diff_only_e1_against_weekends_issues_t6_recheck() -> None:
    recs = _year_records("USDJPY", 2017)
    recs.append(_rec("USDJPY", "tuesday", "2017-03-14", "S2", [], e1=_e1_diff_only(2)))
    ev = ts.evaluate(recs)
    y = ev.years[("USDJPY", 2017)]
    assert y["status"] == "confirmed" and y["rule"] == "US"
    assert y["unresolved_states"] == ["S2"]
    assert any(b["T"] == "T6" for b in ev.branches)
    assert [a["rid"] for a in ev.auto_plan] == ["X-USDJPY-T2017-03-14-re"]
    assert any(c.startswith("T6") for c in ev.needs_coordinator)


def test_diff_only_e1_agreeing_with_weekends_adds_nothing() -> None:
    recs = _year_records("USDJPY", 2017)
    recs.append(_rec("USDJPY", "tuesday", "2017-03-14", "S2", [], e1=_e1_diff_only(3)))
    ev = ts.evaluate(recs)
    assert "unresolved_states" not in ev.years[("USDJPY", 2017)]
    assert ev.auto_plan == [] and ev.needs_coordinator == []


def test_e4_minute_differences_within_tolerance_support_the_same_rule() -> None:
    recs = _year_records("USDJPY", 2023)
    d = probe._YEAR_SUNDAYS[2023][1]
    times = _weekend_times(d, 2, 3)
    shifted = [t - 60 for t in times[:180]] + [t + 14 * 60 for t in times[180:]]
    recs.append(_rec("EURUSD", "weekend", d, "S2", shifted))
    ev = ts.evaluate(recs)
    assert not any(b["T"] == "T4" for b in ev.branches)
    rows = {r["boundary"]: r for r in ev.e4_rows}
    assert rows["close"]["diff_min"] == -1.0 and rows["open"]["diff_min"] == 14.0
    assert rows["close"]["supports_same_rule"] and rows["open"]["supports_same_rule"]


def test_e4_one_hour_difference_is_not_absorbed_by_the_tolerance() -> None:
    uj = ts.read_weekend(_weekend_times("2023-03-12", 2, 3), "2023-03-12", "S2")
    other = ts.read_weekend(_weekend_times("2023-03-12", 2, 2), "2023-03-12", "S2")
    cmp = ts.e4_compare(uj, other)
    assert not cmp["supports_same_rule"] and cmp["reason"] == "offset_differs"
    assert cmp["open_diff_min"] == -60.0


def test_price_basis_stats_compare_bid_ask_mid_without_prices() -> None:
    base = int(datetime(2019, 3, 19, tzinfo=UTC).timestamp())
    # 6 時間ぶんの tick。値は時刻とともに変わるので、ずれが合うときだけ差が 0 になる
    ticks = [
        ((base + 60 * k) * 1000, f"{100 + k * 0.01:.3f}", f"{100.004 + k * 0.01:.3f}")
        for k in range(360)
    ]
    ref = ts.csv_minute_bars(ticks)
    api_rows = [
        {"time": t + 2 * 3600, "open": v[0], "high": v[1], "low": v[2], "close": v[3]}
        for t, v in ref["bid"].items()
        if base + 3 * 3600 <= t < base + 5 * 3600
    ]
    res = ts.price_basis_stats(api_rows, ref, 0.001, "S2")
    assert res["by_basis"]["bid"]["diff_best_shift_hours"] == 2
    assert res["by_basis"]["bid"]["diff_hypotheses"] == ["EU", "FIXED"]
    bid2 = next(r for r in res["by_basis"]["bid"]["by_shift"] if r["shift_hours"] == 2)
    assert bid2["ohlc_exact"] == 120
    ask2 = next(r for r in res["by_basis"]["ask"]["by_shift"] if r["shift_hours"] == 2)
    assert ask2["ohlc_exact"] == 0 and ask2["median_abs_close_diff_points"] == 4.0
    assert "100." not in json.dumps(res)


def test_csv_weekend_gaps_read_raw_times_only(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text(
        "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\n"
        "2019.03.08\t23:59:58.000\t1\t2\t\t\n"
        "2019.03.11\t00:00:08.000\t1\t2\t\t\n",
        encoding="utf-8",
    )
    gaps = ts.csv_weekend_gaps(p)
    assert gaps == [
        {
            "last_before_raw": "2019-03-08T23:59:58",
            "first_after_raw": "2019-03-11T00:00:08",
            "gap_hours": 48.003,
            "first_after_as_utc": "2019-03-10T21:00:08+00:00",
        }
    ]


# --- 人間の第 14 回の必須の修正（2026-10-16）: 他の通貨にも CSV 照合（E1）との矛盾判定 ---------


def _e1_exact(best: int, exact: int = 119, compared: int = 120) -> list[dict[str, Any]]:
    """ずれ ``best`` で 4 値の完全一致が ``exact`` / ``compared`` 本の E1 の統計（人工）。"""
    return [
        {
            "shift_hours": h,
            "compared": compared,
            "ohlc_exact": exact if h == best else 0,
            "median_abs_close_diff_points": 0.0 if h == best else 50.0,
        }
        for h in probe.E1_SHIFTS_HOURS
    ]


def _us_2019_with_eurjpy(e1: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """USDJPY と EURJPY の 2019 年の週末は全部 US。EURJPY の火曜 2019-03-19（S2）に E1。"""
    recs = _year_records("USDJPY", 2019) + _year_records("EURJPY", 2019)
    if e1 is not None:
        recs.append(_rec("EURJPY", "tuesday", "2019-03-19", "S2", [], e1=e1))
    return recs


def test_other_pair_exact_e1_against_weekends_is_not_confirmed() -> None:
    # 人間の再現: 境界は US、EURJPY の CSV 照合は 120 本中 119 本が欧州式のずれ（+2 h）で完全一致
    ev = ts.evaluate(_us_2019_with_eurjpy(_e1_exact(2)))
    y = ev.years[("EURJPY", 2019)]
    assert y["status"] != "confirmed"
    assert y["e1_conflict"] == "exact"
    assert any(b["T"] == "T6" and b["pair"] == "EURJPY" for b in ev.branches)
    assert "X-EURJPY-T2019-03-19-re" in [a["rid"] for a in ev.auto_plan]
    assert any(c.startswith("T6: EURJPY 2019") for c in ev.needs_coordinator)
    assert "未解決" in ts._md(ev).split("| EURJPY | 2019 |")[1].splitlines()[0]
    # USDJPY の判定は変わらない
    assert ev.years[("USDJPY", 2019)]["status"] == "confirmed"
    assert ev.years[("USDJPY", 2019)]["rule"] == "US"
    assert not any(b["T"] == "T6" and b["pair"] == "USDJPY" for b in ev.branches)


def test_other_pair_diff_only_e1_against_weekends_issues_t6_recheck() -> None:
    # 完全一致は無いが、差の統計が欧州式のずれ（+2 h）で最小
    ev = ts.evaluate(_us_2019_with_eurjpy(_e1_diff_only(2)))
    y = ev.years[("EURJPY", 2019)]
    assert y["unresolved_states"] == ["S2"]
    assert y["e1_conflict"] == "diff"
    assert any(b["T"] == "T6" and b["pair"] == "EURJPY" for b in ev.branches)
    assert "X-EURJPY-T2019-03-19-re" in [a["rid"] for a in ev.auto_plan]
    assert any(c.startswith("T6: EURJPY 2019 の S2") for c in ev.needs_coordinator)
    assert "unresolved_states" not in ev.years[("USDJPY", 2019)]


@pytest.mark.parametrize("e1", [None, "agree_exact", "agree_diff", "not_compared"])
def test_other_pair_e1_without_conflict_changes_nothing(e1: str | None) -> None:
    stats = {
        None: None,
        "agree_exact": _e1_exact(3),
        "agree_diff": _e1_diff_only(3),
        # 比べた足が無い（比較不能）。食い違いにも一致にも数えない（C8）
        "not_compared": [
            {"shift_hours": h, "compared": 0, "ohlc_exact": 0, "median_abs_close_diff_points": None}
            for h in probe.E1_SHIFTS_HOURS
        ],
    }[e1]
    ev = ts.evaluate(_us_2019_with_eurjpy(stats))
    base = ts.evaluate(_us_2019_with_eurjpy(None))
    y = ev.years[("EURJPY", 2019)]
    assert y["status"] == "confirmed" and "e1_conflict" not in y
    assert not any(b["pair"] == "EURJPY" for b in ev.branches)
    assert ev.needs_coordinator == base.needs_coordinator
    assert ev.auto_plan == base.auto_plan


# --- 人間の回答（評価の道具に C5・C8 を当てる (a)）: 観測と現行基準の区分を分ける -----------------


def _current(ev: ts.Evaluation, pair: str, year: int, state: str) -> dict[str, Any]:
    return next(c for c in ev.current if (c["pair"], c["year"], c["state"]) == (pair, year, state))


def test_pair_without_csv_is_assumed_even_when_boundaries_agree() -> None:
    recs = _year_records("USDJPY", 2023)
    for st in ("S2", "S3"):
        d = probe._YEAR_SUNDAYS[2023][probe.STATES.index(st)]
        recs.append(_rec("EURUSD", "weekend", d, st, _weekend_times(d, *OFFSETS["US"][st])))
    ev = ts.evaluate(recs)
    # 観測（調査中の読み）は境界と E4 の一致のまま残す
    assert ev.years[("EURUSD", 2023)]["status"] == "confirmed"
    for st in ("S2", "S3"):
        c = _current(ev, "EURUSD", 2023, st)
        assert c["label"] == ts.LABEL_ASSUMED
        assert c["reason"].startswith("C8 (i)")
        assert "USDJPY と同じ規則を支持" in c["observed_weekends"][0]
    assert all(c["label"] == ts.LABEL_ASSUMED for c in ev.current)
    md = ts._md(ev)
    assert "確認済み" not in md.split("## 観測と現行基準の区分")[0]
    assert "| 確認済み |" not in md


def test_csv_pair_with_consistent_bid_exact_and_diff_is_confirmed_for_that_week() -> None:
    recs = _us_2019_with_eurjpy(_e1_exact(3))
    recs.append(_rec("EURJPY", "tuesday", "2019-03-12", "S2", [], e1=_e1_diff_only(3)))
    ev = ts.evaluate(recs)
    c = _current(ev, "EURJPY", 2019, "S2")
    assert c["label"] == ts.LABEL_CONFIRMED
    assert c["rule"] == "+3 h（US）"
    assert "2019-03-19" in c["scope"] and "2019-03-12" not in c["scope"]
    assert ev.current_notices == []
    # 同じ通貨の他の状態（照合なし）と、照合の無い USDJPY は仮定付き（C5・C8）
    assert _current(ev, "EURJPY", 2019, "S1")["label"] == ts.LABEL_ASSUMED
    assert _current(ev, "USDJPY", 2019, "S2")["label"] == ts.LABEL_ASSUMED
    assert "| 確認済み |" in ts._md(ev)


@pytest.mark.parametrize(
    ("extra", "why"),
    [
        (None, "週末の読み"),  # 人間の再現: 境界は US、bid の完全一致は欧州式の +2 h
        (_e1_diff_only(3), "差のみ"),  # 完全一致 +2 h と差の統計 +3 h が割れる
    ],
)
def test_csv_pair_with_inconsistent_bid_is_assumed_with_notice(
    extra: list[dict[str, Any]] | None, why: str
) -> None:
    recs = _us_2019_with_eurjpy(_e1_exact(2))
    if extra is not None:
        recs.append(_rec("EURJPY", "tuesday", "2019-03-12", "S2", [], e1=extra))
    ev = ts.evaluate(recs)
    c = _current(ev, "EURJPY", 2019, "S2")
    assert c["label"] == ts.LABEL_ASSUMED
    assert c["reason"].startswith("C7") and why in c["reason"]
    assert any(n.startswith("現行基準: EURJPY 2019 S2") for n in ev.current_notices)


def test_exact_match_that_cannot_tell_candidates_apart_is_assumed() -> None:
    # 両方冬（S1）の完全一致 +2 h は US・EU が同じ変換（C8 (iii)、D12-53 (b)）
    recs = _year_records("USDJPY", 2023)
    recs.append(_rec("USDJPY", "tuesday", "2023-03-07", "S1", [], e1=_e1_exact(2, 118, 118)))
    ev = ts.evaluate(recs)
    c = _current(ev, "USDJPY", 2023, "S1")
    assert c["label"] == ts.LABEL_ASSUMED and c["reason"].startswith("C8 (iii)")
