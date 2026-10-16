"""最後の調査（D12 付録 K-10。人間の決定 D12-41・第 12 回）の道具の単体テスト。人工のデータだけ。"""

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

FORBIDDEN_WORDS = ("除外", "追加調査待ち", "保留")


def _weekend_times(sunday: str, close_off: int, open_off: int) -> list[int]:
    """金曜の閉場（NY 17:00）の前 3 時間と、日曜の開場の後 3 時間の 1 分足の時刻（API の値）。"""
    sun = datetime.fromisoformat(sunday)
    close = ts._ny_17(sun - timedelta(days=2)).timestamp() + close_off * 3600
    open_ = ts._ny_17(sun).timestamp() + open_off * 3600
    fri = [int(close - 60 * k) for k in range(180, 0, -1)]
    mon = [int(open_ + 60 * k) for k in range(180)]
    return fri + mon


def _stats(best: int, exact: bool = False) -> list[dict[str, Any]]:
    """ずれ ``best`` の差だけが小さい（``exact`` なら 4 値が完全一致する）照合の統計（人工）。"""
    return [
        {
            "shift_hours": h,
            "compared": 100,
            "ohlc_exact": 100 if (exact and h == best) else 0,
            "median_abs_close_diff_points": 0.0
            if (exact and h == best)
            else (4.0 if h == best else 40.0),
        }
        for h in probe.E1_SHIFTS_HOURS
    ]


def _aux_weekend(close_best: int | None, open_best: int | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for side, best in (("close_side", close_best), ("open_side", open_best)):
        out[side] = {b: (_stats(best) if best is not None else []) for b in probe.REF_BASES}
    return out


def _records(
    closes: dict[str, int] | None = None,
    opens: dict[str, int] | None = None,
    aux_close: int | None = None,
    tuesday_best: int | None = None,
    drop: tuple[str, ...] = (),
    control: tuple[int, int] = (2, 2),
) -> list[dict[str, Any]]:
    """最後の調査の 9 要求の記録（人工）。既定は全部が仮説 US の位置。"""
    closes = closes or {}
    opens = opens or {}
    out: list[dict[str, Any]] = []
    for req in probe.FINAL_STAGE:
        if req.rid in drop:
            continue
        rec: dict[str, Any] = {
            "rid": req.rid,
            "stage": req.stage,
            "pair": req.pair,
            "kind": req.kind,
            "date": req.date,
            "state": req.state,
            "skipped": None,
            "executed_at_utc": "2026-10-20T00:00:00+00:00",
            "symbol_chart_mode": 0,
        }
        if req.kind == "weekend":
            if req.state == "S1":
                c, o = control
            else:
                c = closes.get(req.date, ts.EXPECTED[req.state]["US"][0])
                o = opens.get(req.date, ts.EXPECTED[req.state]["US"][1])
            rec["bar_times"] = _weekend_times(req.date, c, o)
            rec["returned_count"] = len(rec["bar_times"])
            rec["e1_aux"] = _aux_weekend(aux_close if aux_close is not None else c, o)
        else:
            rec["bar_times"] = []
            rec["returned_count"] = 120
            best = tuesday_best if tuesday_best is not None else 3
            rec["e1"] = _stats(best)
            rec["e1_aux"] = {"all": {b: _stats(best) for b in probe.REF_BASES}}
        out.append(rec)
    return out


def _states() -> dict[str, str]:
    return {r.date: r.state for r in probe.FINAL_STAGE if r.kind == "weekend" and r.state != "S1"}


def _assert_two_labels_only(res: dict[str, Any]) -> None:
    text = json.dumps(res, ensure_ascii=False) + ts.final_md(res)
    for w in FORBIDDEN_WORDS:
        assert w not in text
    assert res["label"] in (ts.LABEL_CONFIRMED, ts.LABEL_ASSUMED)
    assert {p["label"] for p in res["periods"]} <= {ts.LABEL_CONFIRMED, ts.LABEL_ASSUMED}


# --- 要求の一覧と上限 ------------------------------------------------------------------------


def test_final_stage_is_9_usdjpy_requests_in_2017_and_2019_only() -> None:
    reqs = probe.FINAL_STAGE
    assert len(reqs) == 9 and len({r.rid for r in reqs}) == 9
    assert probe.FINAL_MAX_REQUESTS == 12
    assert {r.pair for r in reqs} == {"USDJPY"}
    for r in reqs:
        w = r.window()
        probe.check_window(w)
        assert w.start.year in (2017, 2019) and w.end.year in (2017, 2019)
        assert w.end < datetime(2024, 1, 1, tzinfo=UTC)
    states = [(r.kind, r.date, r.state) for r in reqs]
    assert ("weekend", "2017-03-05", "S1") in states and ("weekend", "2019-03-03", "S1") in states
    assert [s for k, _, s in states if k == "weekend"].count("S2M") == 3
    assert {d for k, d, s in states if s == "S2E"} == {"2017-03-26", "2019-03-31"}
    assert {d for k, d, _ in states if k == "tuesday"} == {"2017-03-14", "2017-03-21"}


def test_final_plan_enforces_the_limit_of_12() -> None:
    assert len(probe.final_plan(None, 0, 12)) == 9
    with pytest.raises(probe.FinalLimitError):
        probe.final_plan(None, 0, 13)
    with pytest.raises(probe.FinalLimitError):
        probe.final_plan(None, 9, 4)
    redo = probe.final_plan(["K10-USDJPY-W2017-03-05"], 9, 3)
    assert [r.rid for r in redo] == ["K10-USDJPY-W2017-03-05"]
    with pytest.raises(probe.FinalLimitError):
        probe.final_plan(["X-USDJPY-W2018-03-11"], 0, 12)


def test_final_plan_refuses_2024_and_other_years(monkeypatch: pytest.MonkeyPatch) -> None:
    late = probe.SurveyRequest(
        "K10-USDJPY-W2024-03-17", "K10", "USDJPY", "weekend", "2024-03-17", "S2M"
    )
    monkeypatch.setattr(probe, "FINAL_STAGE", (late,))
    with pytest.raises(probe.RequestOutOfRangeError):
        probe.final_plan(None, 0, 12)
    other = probe.SurveyRequest(
        "K10-USDJPY-W2018-03-18", "K10", "USDJPY", "weekend", "2018-03-18", "S2M"
    )
    monkeypatch.setattr(probe, "FINAL_STAGE", (other,))
    with pytest.raises(probe.FinalLimitError):
        probe.final_plan(None, 0, 12)


def test_timesurvey_k10_counts_earlier_runs_and_refuses_extra_intervals(tmp_path: Path) -> None:
    prev = tmp_path / "ts-K10-1"
    prev.mkdir()
    reqs = [{"rid": r.rid, "skipped": None} for r in probe.FINAL_STAGE]
    reqs.append({"rid": "x", "skipped": "request_limit"})
    (prev / "summary_ts-K10.json").write_text(json.dumps({"requests": reqs}), encoding="utf-8")
    assert probe.prior_final_requests(tmp_path) == 9
    out = str(tmp_path / "ts-K10-2")
    # 済み 9 で 4 を指定すると、端末につなぐ前に止まる
    with pytest.raises(probe.FinalLimitError):
        probe.main(["timesurvey", "--stage", "K10", "--out-dir", out, "--max-requests", "4"])
    plan = tmp_path / "plan.json"
    plan.write_text("[]", encoding="utf-8")
    with pytest.raises(probe.FinalLimitError):
        probe.main(
            [
                "timesurvey",
                "--stage",
                "K10",
                "--out-dir",
                out,
                "--max-requests",
                "3",
                "--plan-file",
                str(plan),
            ]
        )
    with pytest.raises(ValueError):
        probe.main(
            [
                "timesurvey",
                "--stage",
                "A2",
                "--out-dir",
                out,
                "--max-requests",
                "3",
                "--only",
                "K10-USDJPY-W2017-03-05",
            ]
        )


# --- 状態「米欧の間の週末」 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sunday", "state", "close_off", "open_off", "expected"),
    [
        ("2017-03-19", "S2M", 3, 3, ["US"]),
        ("2017-03-19", "S2M", 2, 2, ["EU", "FIXED"]),
        ("2019-03-31", "S2E", 3, 3, ["US"]),
        ("2019-03-31", "S2E", 2, 3, ["EU"]),
        ("2019-03-31", "S2E", 2, 2, ["FIXED"]),
    ],
)
def test_between_us_and_eu_states_in_the_hypothesis_table(
    sunday: str, state: str, close_off: int, open_off: int, expected: list[str]
) -> None:
    w = ts.read_weekend(_weekend_times(sunday, close_off, open_off), sunday, state)
    assert w.readable and w.hypotheses == expected


# --- 週末の照合と bid・ask・mid ---------------------------------------------------------------


def _write_csv(root: Path, ticks: list[tuple[datetime, str, str]]) -> None:
    """配布 CSV の形（元の時刻 = ニューヨークの現地時刻 + 7 時間）で tick を書く。"""
    d = root / "USDJPY"
    d.mkdir(parents=True)
    lines = ["<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>"]
    for t, bid, ask in ticks:
        server = t.astimezone(ts.NEW_YORK).replace(tzinfo=None) + timedelta(hours=7)
        lines.append(f"{server:%Y.%m.%d}\t{server:%H:%M:%S}.000\t{bid}\t{ask}\t\t")
    (d / "ticks_USDJPY-oj5k_2017-03.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _weekend_ticks() -> list[tuple[datetime, str, str]]:
    """2017-03-19 の週末（米欧の間）の前後の tick。値は分ごとに変わる（ずれが合うときだけ一致）。"""
    out = []
    fri = datetime(2017, 3, 17, 17, 0, tzinfo=UTC)  # 閉場（21:00 UTC）の前 4 時間
    mon = datetime(2017, 3, 19, 21, 0, tzinfo=UTC)  # 開場の後 4 時間
    for start in (fri, mon):
        for k in range(240):
            t = start + timedelta(minutes=k, seconds=5)
            v = 110 + (k % 97) * 0.013 + (0.5 if start is mon else 0)
            out.append((t, f"{v:.3f}", f"{v + 0.005:.3f}"))
    return out


def test_weekend_reference_and_aux_stats_split_sides_without_prices(tmp_path: Path) -> None:
    _write_csv(tmp_path, _weekend_ticks())
    req = next(r for r in probe.FINAL_STAGE if r.date == "2017-03-19")
    ref = json.loads(json.dumps(ts.build_final_ref([req], tmp_path)))
    assert set(ref[req.rid]) == {"bid", "ask", "mid"}
    loaded = probe.load_ref(ref)[req.rid]
    # API の足 = CSV の bid の足を +3 時間ずらしたもの（仮説 US の位置）
    api_rows = [
        {
            "time": t + 3 * 3600,
            "open": float(v[0]),
            "high": float(v[1]),
            "low": float(v[2]),
            "close": float(v[3]),
        }
        for t, v in loaded["bid"].items()
    ]
    sunday = datetime(2017, 3, 19, tzinfo=UTC).timestamp()
    aux = probe.aux_stats(api_rows, loaded, 3, 0.001, split_at=sunday)
    assert set(aux) == {"close_side", "open_side"}
    for side in aux:
        bid3 = next(r for r in aux[side]["bid"] if r["shift_hours"] == 3)
        assert bid3["compared"] > 100 and bid3["ohlc_exact"] == bid3["compared"]
        ask3 = next(r for r in aux[side]["ask"] if r["shift_hours"] == 3)
        assert ask3["ohlc_exact"] == 0 and ask3["median_abs_close_diff_points"] == 5.0
        assert ask3["median_signed_close_diff_points"] == -5.0
        mid3 = next(r for r in aux[side]["mid"] if r["shift_hours"] == 3)
        assert mid3["median_abs_close_diff_points"] == 2.5
    text = json.dumps(aux)
    assert "110." not in text and "111." not in text


def test_run_time_survey_writes_weekend_aux_and_keeps_tuesday_e1() -> None:
    class Api:
        COPY_TICKS_ALL = 1
        TIMEFRAME_M1 = 1

        def symbols_get(self, group: str) -> Any:
            return [SimpleNamespace(name="USDJPY.cl")]

        def symbol_select(self, symbol: str, enable: bool) -> bool:
            return True

        def symbol_info(self, symbol: str) -> Any:
            return SimpleNamespace(chart_mode=0, point=0.001, digits=3)

        def copy_rates_range(self, symbol: str, tf: int, start: datetime, end: datetime) -> Any:
            t = int(start.timestamp()) + 3600
            return [{"time": t, "open": 110.123, "high": 110.2, "low": 110.1, "close": 110.15}]

        def last_error(self) -> Any:
            return (1, "Success")

    wk = next(r for r in probe.FINAL_STAGE if r.date == "2017-03-19")
    tu = next(r for r in probe.FINAL_STAGE if r.date == "2017-03-21")
    one = {"0": ["110.123", "110.2", "110.1", "110.15"]}
    raw = {
        wk.rid: {"bid": one, "ask": one, "mid": one},
        tu.rid: {"bid": one, "ask": one, "mid": one},
    }
    recs = probe.run_time_survey(
        Api(),  # type: ignore[arg-type]
        [wk, tu],
        max_requests=12,
        e1_ref=probe.load_ref(raw),
        retry_waits=(),
    )
    assert recs[0].e1 is None and recs[0].e1_aux is not None
    assert set(recs[0].e1_aux) == {"close_side", "open_side"}
    assert recs[1].e1 is not None and recs[1].e1_aux is not None
    assert set(recs[1].e1_aux["all"]) == {"bid", "ask", "mid"}
    text = json.dumps([asdict(r) for r in recs])
    assert "110.12" not in text and "110.15" not in text


def test_load_ref_reads_old_bid_only_and_new_per_basis_forms() -> None:
    old = probe.load_ref({"r": {"60": ["1", "1", "1", "1"]}})
    assert old == {"r": {"bid": {60: ["1", "1", "1", "1"]}}}
    new = probe.load_ref({"r": {"bid": {"60": ["1"] * 4}, "mid": {"60": ["1.0005"] * 4}}})
    assert set(new["r"]) == {"bid", "mid"} and 60 in new["r"]["mid"]


# --- 判定の 2 区分（人間の第 12 回の決定） ----------------------------------------------------


def test_all_closes_agree_on_us_gives_confirmed_with_rule_period_and_grounds() -> None:
    res = ts.final_evaluate(_records())
    _assert_two_labels_only(res)
    assert res["label"] == ts.LABEL_CONFIRMED
    assert res["rule"]["hypotheses"] == ["US"] and res["rule"]["api_minus_utc_hours_in_period"] == 3
    assert [(p["year"], p["from"]) for p in res["periods"]] == [
        (2017, "2017-03-12"),
        (2019, "2019-03-10"),
    ]
    assert len(res["rule"]["grounds"]) == 5 + 2 + 1
    assert "candidates" not in res
    assert "規則（確認済み）" in ts.final_md(res)


def test_eu_rule_is_confirmed_too_not_set_aside() -> None:
    eu = {d: 2 for d in ("2017-03-19", "2017-03-26", "2019-03-17", "2019-03-24", "2019-03-31")}
    eu_open = {d: ts.EXPECTED[s]["EU"][1] for d, s in _states().items()}
    res = ts.final_evaluate(_records(closes=eu, opens=eu_open, tuesday_best=2))
    _assert_two_labels_only(res)
    assert res["label"] == ts.LABEL_CONFIRMED
    assert res["rule"]["hypotheses"] == ["EU", "FIXED"]
    assert res["rule"]["api_minus_utc_hours_in_period"] == 2 and res["rule"]["note"]


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"closes": {"2019-03-24": 2}}, "割れた"),
        ({"drop": ("K10-USDJPY-W2019-03-17",)}, "読めない週末"),
        ({"control": (2, 3)}, "対照"),
        ({"tuesday_best": 2}, "補助の比較"),
        ({"aux_close": 2}, "補助の比較"),
    ],
)
def test_otherwise_assumed_with_candidates_and_interpretations(
    kwargs: dict[str, Any], reason: str
) -> None:
    res = ts.final_evaluate(_records(**kwargs))
    _assert_two_labels_only(res)
    assert res["label"] == ts.LABEL_ASSUMED
    assert any(reason in r for r in res["reasons"])
    cands = {c["hypothesis"]: c for c in res["candidates"]}
    assert set(cands) == {"US", "EU", "FIXED"}
    assert cands["US"]["api_minus_utc_hours_in_period"] == 3
    assert cands["EU"]["api_minus_utc_hours_in_period"] == 2
    assert all(c["interpretation"] for c in cands.values())
    assert "rule" not in res
    md = ts.final_md(res)
    assert "候補の規則ごとの解釈" in md and "原データ" in md


def test_price_difference_alone_does_not_confirm() -> None:
    # 閉場が全部読めない（区間の中の足が無い）なら、補助の比較が揃っていても確認済みにしない
    recs = _records()
    for r in recs:
        if r["kind"] == "weekend" and r["state"] != "S1":
            r["bar_times"] = []
    res = ts.final_evaluate(recs)
    assert res["label"] == ts.LABEL_ASSUMED


def test_final_evaluate_cli_writes_report_and_json(tmp_path: Path) -> None:
    s = tmp_path / "summary_ts-K10.json"
    s.write_text(json.dumps({"requests": _records()}), encoding="utf-8")
    rep, js = tmp_path / "report.md", tmp_path / "result.json"
    assert ts.main(["final-evaluate", str(s), "--report", str(rep), "--json-out", str(js)]) == 0
    assert json.loads(js.read_text(encoding="utf-8"))["label"] == ts.LABEL_CONFIRMED
    assert "確認済み" in rep.read_text(encoding="utf-8")
