"""MT5 API の時刻の調査（D12 §11.7）の Mac 側の道具。

- ``e1-ref``: 配布 CSV の tick から、火曜の区間の前後 5 時間の bid の 1 分足（UTC）を作り、
  Windows に渡す gzip の JSON にする（E1 の照合は Windows の上でメモリの中で行い、
  API の価格は保存しない）。
- ``evaluate``: 回収した ``summary_ts-*.json`` から、週末の境界（E2）・夏時間の切替の前後（E3）・
  通貨の間の同じ時刻（E4）・配布 CSV との一致（E1）を読み、決定木（T1〜T7）の分岐と、
  自動で足す要求（決定木の追加の計画）と、司令塔の判断が要る事項を書く。価格の値は出さない。

判定の規則（D12 §11.7.2a・§11.7.3）:

- 仮説 US・EU・固定ごとに、各状態（S1〜S4）の金曜の閉場と日曜の開場のずれ（API の時刻 − UTC、時間）
  を ``EXPECTED`` に持つ。週末の区間の観察（金曜の最後の足と日曜の最初の足）を、
  ニューヨーク 17:00 の閉場・開場と比べて時間に丸め、合う仮説を残す。
  丸めの残りが ``TOLERANCE_MINUTES`` を超えたら読めない（T5）。
- 1 つの年の USDJPY について、読めた週末の状態の全部で合う仮説が 1 つだけで、
  読めた週末が 2 つ以上なら、その年の読めた週末を「直接確認」とする。
  配布 CSV のある通貨・月では、火曜の E1 が同じ仮説を示すことも求める。
- 他の通貨は、その週末の E2 が USDJPY の同じ年の確定した仮説で説明でき、金曜の最後と日曜の最初の足の
  時刻が USDJPY と同じ（E4）で、USDJPY のその年が確定していれば「直接確認」。
- 開場の位置だけ、通貨の間の一致だけでは確定しない。組合せが揃わないものは「未解決」。

人間の決定（第 11 回、2026-10-16）による改め:

- E4（通貨の間の同じ時刻）は「同じ分」で認定しない。閉場・開場のずれ（時間）が USDJPY と同じで、
  分単位の差が ±``TOLERANCE_MINUTES`` 以内なら「同じ時刻の規則を支持する」と読む。±15 分は境界付近の
  更新時刻の差の許容（補助）で、同じ時刻の認定ではない。分単位の差は ``e4_residuals`` で全部残す。
- E1 は 4 値の完全一致が無くても、ずれごとの差の統計を証拠の一部として記録する（``evidence`` が
  ``diff_only``）。差が最小のずれの仮説が週末の仮説と違えば T6 を出し、読み直しの要求と司令塔への
  事項を出す。差の統計は時刻の確定には使わない（調査漏れの修正。付録 K-9.5）。

最後の調査（付録 K-10。人間の決定 D12-41 と第 12 回の決定）:

- ``final-ref``: 配布 CSV から、最後の調査の要求（週末と火曜）の前後 5 時間の bid・ask・mid の
  1 分足を作る。
- ``final-evaluate``: 回収した最後の調査の要求を読み、2017・2019 年の米欧の切替日の間の期間を
  「確認済み」（規則を根拠付きで特定。規則名・適用期間・根拠を出す）か「仮定付き」（特定できない。
  候補の規則ごとの解釈を並べる。原データはそのまま）のどちらかに分ける。区分はこの 2 つだけ。
  価格の差が最小という理由だけでは確認済みにしない（bid・ask・mid の照合は補助）。
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tools.survey import mt5_api_probe as probe

NEW_YORK = ZoneInfo("America/New_York")
TOLERANCE_MINUTES = 15
#: 週末の休場とみなす最短の空白（時間）。
WEEKEND_GAP_HOURS = 24
HYPOTHESES: tuple[str, ...] = ("US", "EU", "FIXED")
#: 状態ごとの (金曜の閉場のずれ, 日曜の開場のずれ)（D12 §11.7.3 の最初の表）。
EXPECTED: dict[str, dict[str, tuple[int, int]]] = {
    "S1": {"US": (2, 2), "EU": (2, 2), "FIXED": (2, 2)},
    "S2": {"US": (2, 3), "EU": (2, 2), "FIXED": (2, 2)},
    "S3": {"US": (3, 3), "EU": (3, 3), "FIXED": (2, 2)},
    "S4": {"US": (3, 3), "EU": (3, 2), "FIXED": (2, 2)},
    # 米欧の間の週末（金曜・日曜とも米国だけ夏時間。付録 K-10.2）
    "S2M": {"US": (3, 3), "EU": (2, 2), "FIXED": (2, 2)},
    # 米欧の間で日曜が欧州の切替日の週末（金曜は米国だけ夏時間、日曜の開場は両方夏時間）
    "S2E": {"US": (3, 3), "EU": (2, 3), "FIXED": (2, 2)},
}
#: 火曜の区間（その日の状態）での期待されるずれ。
EXPECTED_TUESDAY: dict[str, dict[str, int]] = {
    "S1": {"US": 2, "EU": 2, "FIXED": 2},
    "S2": {"US": 3, "EU": 2, "FIXED": 2},
    "S3": {"US": 3, "EU": 3, "FIXED": 2},
    "S4": {"US": 3, "EU": 2, "FIXED": 2},
    "S2M": {"US": 3, "EU": 2, "FIXED": 2},
    "S2E": {"US": 3, "EU": 3, "FIXED": 2},  # その週の火曜は欧州の切替の後
}
#: E1 で「一致」とみなす、4 値が完全に一致した足の割合。
E1_MATCH_RATIO = 0.9
#: 配布 CSV の置き場（D12 §2.4）。
CSV_ROOT = Path("/Users/sanchez/Desktop/Odyssey-TradingResearch/data/raw/oanda")


def _ny_17(date: datetime) -> datetime:
    """その日（ニューヨークの日付）の 17:00 を UTC で返す。"""
    local = datetime(date.year, date.month, date.day, 17, tzinfo=NEW_YORK)
    return local.astimezone(UTC)


@dataclass
class WeekendReading:
    """週末の区間の読み（時刻だけ）。"""

    readable: bool
    reason: str = ""
    friday_last: int | None = None
    sunday_first: int | None = None
    close_offset_h: int | None = None
    open_offset_h: int | None = None
    close_residual_min: float | None = None
    open_residual_min: float | None = None
    hypotheses: list[str] = field(default_factory=list)


def read_weekend(bar_times: Sequence[int], sunday: str, state: str) -> WeekendReading:
    """金曜の最後の足と日曜の最初の足から、閉場・開場のずれと、合う仮説を読む（E2・E3）。"""
    times = sorted(bar_times)
    if len(times) < 2:
        return WeekendReading(False, "bars_missing")
    gaps = [(b - a, i) for i, (a, b) in enumerate(zip(times, times[1:], strict=False))]
    gap, i = max(gaps)
    if gap < WEEKEND_GAP_HOURS * 3600:
        return WeekendReading(False, "no_weekend_gap")
    last, first = times[i], times[i + 1]
    sun = datetime.fromisoformat(sunday)
    close_utc = _ny_17(sun - timedelta(days=2)).timestamp()
    open_utc = _ny_17(sun).timestamp()
    close_d = (last + 60 - close_utc) / 3600  # 最後の足の終わり（始め + 1 分）
    open_d = (first - open_utc) / 3600
    ch, oh = round(close_d), round(open_d)
    cres, ores = (close_d - ch) * 60, (open_d - oh) * 60
    reading = WeekendReading(
        True,
        "",
        last,
        first,
        ch,
        oh,
        round(cres, 1),
        round(ores, 1),
    )
    if abs(cres) > TOLERANCE_MINUTES or abs(ores) > TOLERANCE_MINUTES:
        reading.readable = False
        reading.reason = "boundary_blurred"
        return reading
    reading.hypotheses = [h for h in HYPOTHESES if EXPECTED[state][h] == (ch, oh)]
    if not reading.hypotheses:
        reading.readable = False
        reading.reason = "no_hypothesis_matches"
    return reading


def read_e1(
    e1: Sequence[Mapping[str, Any]] | None,
    state: str,
    expected: Mapping[str, int] | None = None,
) -> dict[str, Any] | None:
    """火曜の E1 の統計から、一致したずれと、それで説明できる仮説を読む。

    4 値の完全一致が ``E1_MATCH_RATIO`` 以上なら ``evidence`` は ``exact`` で、``hypotheses`` を
    確定の材料にする。完全一致が足りなくても比べた足があれば ``diff_only`` とし、差の中央値が最小の
    ずれ（``diff_best_shift_hours``）と、その仮説（``diff_hypotheses``）、2 番目に小さい差との比
    （``diff_separation``）を記録する。``diff_only`` は確定に使わない（追加の確認を出す材料だけ）。

    ``expected`` は仮説ごとの期待されるずれ（省くと ``EXPECTED_TUESDAY[state]``）。週末の区間の
    金曜の側・月曜の側を読むときに、閉場・開場の期待を渡す。
    """
    if not e1:
        return None
    exp = expected if expected is not None else EXPECTED_TUESDAY[state]
    best = max(e1, key=lambda r: (r["ohlc_exact"], -(r["median_abs_close_diff_points"] or 1e9)))
    ratio = best["ohlc_exact"] / best["compared"] if best["compared"] else 0.0
    matched = ratio >= E1_MATCH_RATIO
    # E1 のずれ h は「API の時刻 − h 時間 = UTC」なので、API の時刻 − UTC = h。
    hyps = [h for h in HYPOTHESES if exp[h] == best["shift_hours"]]
    diffs = sorted(
        (r["median_abs_close_diff_points"], r["shift_hours"])
        for r in e1
        if r["compared"] and r["median_abs_close_diff_points"] is not None
    )
    diff_best = diffs[0][1] if diffs else None
    separation = None
    if len(diffs) >= 2:
        separation = round(diffs[1][0] / diffs[0][0], 1) if diffs[0][0] else float("inf")
    evidence = "exact" if matched else ("diff_only" if diffs else "none")
    return {
        "best_shift_hours": best["shift_hours"],
        "exact_ratio": round(ratio, 3),
        "matched": matched,
        "hypotheses": hyps if matched else [],
        "evidence": evidence,
        "diff_best_shift_hours": diff_best,
        "diff_separation": separation,
        "diff_by_shift": {str(r["shift_hours"]): r["median_abs_close_diff_points"] for r in e1},
        "diff_hypotheses": [h for h in HYPOTHESES if diff_best is not None and exp[h] == diff_best],
    }


def e4_compare(uj: WeekendReading, w: WeekendReading) -> dict[str, Any]:
    """同じ週末の USDJPY と他の通貨の閉場・開場を比べる（E4。人間の第 11 回の基準）。

    分単位の差（他の通貨 − USDJPY）を返し、ずれの時間が同じで差が ±``TOLERANCE_MINUTES`` 以内なら
    ``supports_same_rule``。±15 分は境界付近の更新時刻の差の許容で、同じ時刻の認定ではない。
    """
    out: dict[str, Any] = {"supports_same_rule": False}
    if not (uj.readable and w.readable):
        out["reason"] = "unreadable"
        return out
    assert uj.friday_last is not None and w.friday_last is not None
    assert uj.sunday_first is not None and w.sunday_first is not None
    out["close_diff_min"] = (w.friday_last - uj.friday_last) / 60
    out["open_diff_min"] = (w.sunday_first - uj.sunday_first) / 60
    out["same_offset_hours"] = (w.close_offset_h, w.open_offset_h) == (
        uj.close_offset_h,
        uj.open_offset_h,
    )
    within = (
        abs(out["close_diff_min"]) <= TOLERANCE_MINUTES
        and abs(out["open_diff_min"]) <= TOLERANCE_MINUTES
    )
    out["within_tolerance"] = within
    out["supports_same_rule"] = bool(out["same_offset_hours"] and within)
    if not out["supports_same_rule"]:
        out["reason"] = "offset_differs" if not out["same_offset_hours"] else "beyond_tolerance"
    return out


def e4_residuals(
    by_week: Mapping[tuple[str, str], WeekendReading], states: Mapping[tuple[str, str], str]
) -> list[dict[str, Any]]:
    """通貨 × 週末 × 境界（閉場・開場）の分単位の残差の表（時刻だけ。元の時刻は変えない）。"""
    out: list[dict[str, Any]] = []
    for (pair, sunday), w in sorted(by_week.items()):
        if pair == probe.USDJPY:
            continue
        uj = by_week.get((probe.USDJPY, sunday))
        if uj is None:
            continue
        cmp = e4_compare(uj, w)
        for side, own, base, off, base_off, res in (
            (
                "close",
                w.friday_last,
                uj.friday_last,
                w.close_offset_h,
                uj.close_offset_h,
                w.close_residual_min,
            ),
            (
                "open",
                w.sunday_first,
                uj.sunday_first,
                w.open_offset_h,
                uj.open_offset_h,
                w.open_residual_min,
            ),
        ):
            out.append(
                {
                    "pair": pair,
                    "sunday": sunday,
                    "state": states.get((pair, sunday), ""),
                    "boundary": side,
                    "pair_bar_time": _iso(own),
                    "usdjpy_bar_time": _iso(base),
                    "diff_min": None if own is None or base is None else (own - base) / 60,
                    "pair_offset_h": off,
                    "usdjpy_offset_h": base_off,
                    "pair_residual_min": res,
                    "supports_same_rule": cmp["supports_same_rule"],
                }
            )
    return out


def _iso(t: int | None) -> str:
    return "" if t is None else datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M")


def year_of(date: str) -> int:
    return int(date[:4])


@dataclass
class Evaluation:
    rows: list[dict[str, Any]]
    years: dict[tuple[str, int], dict[str, Any]]
    branches: list[dict[str, Any]]
    auto_plan: list[dict[str, str]]
    needs_coordinator: list[str]
    sent: int
    e4_rows: list[dict[str, Any]] = field(default_factory=list)


def evaluate(records: Iterable[Mapping[str, Any]], extra_used: int = 0) -> Evaluation:
    """回収した記録の全部から、読み・年ごとの判定・決定木の分岐・自動の追加の計画を作る。"""
    # 空白の測定の代表週（gapweek）は時刻の判定に使わない（集計は時刻の確定の後に Mac で行う）。
    # 同じ要求を再び出した場合（取り寄せ待ちの空振りのやり直し）は、最後に実行した記録だけを使う。
    latest: dict[str, Mapping[str, Any]] = {}
    for r in records:
        if r.get("skipped") or r["kind"] not in ("weekend", "tuesday"):
            continue
        if r["rid"] not in latest or r["executed_at_utc"] >= latest[r["rid"]]["executed_at_utc"]:
            latest[r["rid"]] = r
    recs = list(latest.values())
    rows: list[dict[str, Any]] = []
    by_week: dict[tuple[str, str], WeekendReading] = {}
    for r in recs:
        row: dict[str, Any] = {
            "rid": r["rid"],
            "pair": r["pair"],
            "kind": r["kind"],
            "date": r["date"],
            "state": r["state"],
            "count": r["returned_count"],
            "chart_mode": r["symbol_chart_mode"],
        }
        if r["kind"] == "weekend":
            w = read_weekend(r["bar_times"], r["date"], r["state"])
            by_week[(r["pair"], r["date"])] = w
            row.update(
                readable=w.readable,
                reason=w.reason,
                close_offset_h=w.close_offset_h,
                open_offset_h=w.open_offset_h,
                close_residual_min=w.close_residual_min,
                open_residual_min=w.open_residual_min,
                hypotheses=w.hypotheses,
                friday_last=w.friday_last,
                sunday_first=w.sunday_first,
            )
        else:
            row["e1"] = read_e1(r.get("e1"), r["state"])
        rows.append(row)

    years: dict[tuple[str, int], dict[str, Any]] = {}
    branches: list[dict[str, Any]] = []
    auto: list[dict[str, str]] = []
    coordinator: list[str] = []
    done_ids = {r["rid"] for r in recs}

    def add(req: probe.SurveyRequest, why: str) -> None:
        if req.rid in done_ids or any(a["rid"] == req.rid for a in auto):
            return
        auto.append({**req.__dict__})
        branches[-1].setdefault("requests", []).append(req.rid)
        _ = why

    # USDJPY の年ごと
    for (pair, sunday), w in sorted(by_week.items()):
        key = (pair, year_of(sunday))
        y = years.setdefault(key, {"weeks": [], "unreadable": []})
        (y["weeks"] if w.readable else y["unreadable"]).append((sunday, w.hypotheses))
    for (pair, year), y in sorted(years.items()):
        sets = [set(h) for _, h in y["weeks"]]
        inter = set.intersection(*sets) if sets else set()
        y["intersection"] = sorted(inter)
        y["status"] = "unresolved"
        if pair == probe.USDJPY:
            tue = [
                row
                for row in rows
                if row["pair"] == pair and row["kind"] == "tuesday" and year_of(row["date"]) == year
            ]
            e1_sets = [
                set(t["e1"]["hypotheses"]) for t in tue if t.get("e1") and t["e1"]["matched"]
            ]
            if len(inter) == 1 and len(sets) >= 2:
                h = next(iter(inter))
                # 完全一致しない E1 も、差の統計が別の仮説なら T6 の材料（確定には使わない）
                diff_conflicts = [
                    t
                    for t in tue
                    if t.get("e1")
                    and t["e1"]["evidence"] == "diff_only"
                    and h not in t["e1"]["diff_hypotheses"]
                ]
                if all(h in s for s in e1_sets):
                    y["status"] = "confirmed"
                    y["rule"] = h
                if diff_conflicts and y["status"] == "confirmed":
                    states_c = sorted({t["state"] for t in diff_conflicts})
                    y["unresolved_states"] = states_c
                    branches.append(
                        {
                            "T": "T6",
                            "pair": pair,
                            "year": year,
                            "note": "完全一致しない E1 の差の統計が別の仮説（"
                            + ", ".join(
                                f"{t['date']} {t['e1']['diff_best_shift_hours']:+d} h"
                                for t in diff_conflicts
                            )
                            + "）。確定には使わない",
                        }
                    )
                    for t in diff_conflicts:
                        add(
                            probe.SurveyRequest(
                                f"X-{pair}-T{t['date']}-re",
                                "X",
                                pair,
                                "tuesday",
                                t["date"],
                                t["state"],
                            ),
                            "T6",
                        )
                    coordinator.append(
                        f"T6: {pair} {year} の {'・'.join(states_c)} の週で、週末は {h} だが"
                        "完全一致しない E1 の差の統計が別のずれを示す。読み直しの要求を出す。"
                        "割れたままなら、その状態の週を採用を妨げる未解決とする"
                    )
                if y["status"] != "confirmed":
                    branches.append(
                        {"T": "T6", "pair": pair, "year": year, "note": "E1 と E2・E3 が別の仮説"}
                    )
                    for t in tue:
                        add(
                            probe.SurveyRequest(
                                f"X-{pair}-T{t['date']}-re",
                                "X",
                                pair,
                                "tuesday",
                                t["date"],
                                t["state"],
                            ),
                            "T6",
                        )
            elif sets and not inter:
                branches.append(
                    {"T": "T3", "pair": pair, "year": year, "note": "年の途中の変更の疑い"}
                )
                coordinator.append(
                    f"T3: {pair} {year} の 4 状態が 1 つの仮説で揃わない。"
                    "春と秋の間の週末の二分探索"
                    "（最大 4 要求）を行うか、司令塔の判断を仰ぐ"
                )
            if year == 2019 and inter == {"US"}:
                branches.append(
                    {
                        "T": "T2",
                        "pair": pair,
                        "year": year,
                        "note": "2019-03-19 の −2 時間と食い違う",
                    }
                )
                for d in ("2019-03-12", "2019-03-26"):  # どちらも米国だけ夏時間（S2）
                    add(probe.SurveyRequest(f"X-{pair}-T{d}", "X", pair, "tuesday", d, "S2"), "T2")
        for sunday, _h in y["unreadable"]:
            branches.append(
                {"T": "T5", "pair": pair, "year": year, "note": f"週末 {sunday} が読めない"}
            )
            tue_date = (datetime.fromisoformat(sunday) + timedelta(days=2)).date().isoformat()
            state = next(r["state"] for r in recs if r["pair"] == pair and r["date"] == sunday)
            add(
                probe.SurveyRequest(f"X-{pair}-T{tue_date}", "X", pair, "tuesday", tue_date, state),
                "T5",
            )

    # 他の通貨（E4）
    for (pair, sunday), w in sorted(by_week.items()):
        if pair == probe.USDJPY:
            continue
        year = year_of(sunday)
        base = years.get((probe.USDJPY, year), {})
        uj = by_week.get((probe.USDJPY, sunday))
        same_time = bool(uj and e4_compare(uj, w)["supports_same_rule"])
        rule = base.get("rule")
        y = years[(pair, year)]
        y.setdefault("e4", []).append((sunday, same_time))
        if w.readable and rule and rule in w.hypotheses and same_time:
            y.setdefault("confirmed_weeks", []).append(sunday)
        elif w.readable and rule and (rule not in w.hypotheses or not same_time):
            branches.append(
                {"T": "T4", "pair": pair, "year": year, "note": f"{sunday} が USDJPY と違う"}
            )
            state_rows = probe._YEAR_SUNDAYS[year]
            for st in ("S1", "S4"):
                d = state_rows[probe.STATES.index(st)]
                add(probe.SurveyRequest(f"X-{pair}-W{d}", "X", pair, "weekend", d, st), "T4")
    for (pair, _year), y in sorted(years.items()):
        if pair != probe.USDJPY and y.get("confirmed_weeks") and not y["unreadable"]:
            if len(y["confirmed_weeks"]) == len(y["weeks"]):
                y["status"] = "confirmed"
                y["rule"] = years[(probe.USDJPY, _year)]["rule"]
                base_unres = years.get((probe.USDJPY, _year), {}).get("unresolved_states")
                if base_unres:  # USDJPY の未解決の状態は、同じ規則を支持する通貨にも残る
                    y["unresolved_states"] = list(base_unres)
                _other_pair_e1(pair, _year, y, rows, branches, coordinator, add)

    remaining = probe.TIME_SURVEY_EXTRA_REQUESTS - extra_used
    if len(auto) > remaining:
        coordinator.append(
            f"T7: 決定木の追加の要求 {len(auto)} が残り {remaining} を超える。"
            "上限内の分だけ出すか、報告して終了する"
        )
        auto = auto[: max(remaining, 0)]
    states = {(r["pair"], r["date"]): r["state"] for r in recs}
    e4_rows = e4_residuals(by_week, states)
    return Evaluation(rows, years, branches, auto, coordinator, len(recs), e4_rows)


def _e1_conflicts(
    tue: Sequence[Mapping[str, Any]], h: str
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """火曜の E1（bid）のうち、週末の仮説 ``h`` と食い違うものを、完全一致と差の統計に分けて返す。

    比べた足が無い・比べられない E1（``evidence`` が ``none``、または E1 が無い）は、どちらにも
    数えない。食い違いが無いことの根拠にもしない（C8。観察が無いことを「矛盾なし」と扱わない）。
    """
    exact = [
        t for t in tue if t.get("e1") and t["e1"]["matched"] and h not in t["e1"]["hypotheses"]
    ]
    diff = [
        t
        for t in tue
        if t.get("e1")
        and t["e1"]["evidence"] == "diff_only"
        and h not in t["e1"]["diff_hypotheses"]
    ]
    return exact, diff


def _other_pair_e1(
    pair: str,
    year: int,
    y: dict[str, Any],
    rows: Sequence[Mapping[str, Any]],
    branches: list[dict[str, Any]],
    coordinator: list[str],
    add: Any,
) -> None:
    """他の通貨の年に、配布 CSV との照合（E1）との矛盾の判定を当てる（USDJPY と同じ判定）。

    人間の第 14 回の必須の修正（D12 §11.7.2a の「配布 CSV のある通貨・月」と §11.7.3 の T6）:
    E4 で USDJPY と同じ規則を支持しても、その通貨の火曜の E1 が週末の仮説と食い違えば、

    - 4 値の完全一致（``exact``）が別の仮説なら、その年を「直接確認」にしない。
    - 差の統計（``diff_only``）が別の仮説なら、その状態の週を未解決とする（確定には使わない）。

    どちらも T6 の分岐（食い違いの通知）・食い違った火曜の読み直しの要求・司令塔への事項を出す。
    E1 は bid の照合（足の価格基準。C7 (c) の主の判定）。E1 の無い通貨・月は何も変えない。
    """
    h = y["rule"]
    tue = [
        r
        for r in rows
        if r["pair"] == pair and r["kind"] == "tuesday" and year_of(r["date"]) == year
    ]
    exact, diff = _e1_conflicts(tue, h)
    for kind, conflicts in (("exact", exact), ("diff", diff)):
        if not conflicts:
            continue
        what = "完全一致した E1" if kind == "exact" else "完全一致しない E1 の差の統計"
        shift = "best_shift_hours" if kind == "exact" else "diff_best_shift_hours"
        states_c = sorted({t["state"] for t in conflicts})
        if kind == "exact":
            y["status"] = "unresolved"
            y["e1_conflict"] = "exact"
        else:
            y["unresolved_states"] = sorted(set(y.get("unresolved_states", [])) | set(states_c))
            y.setdefault("e1_conflict", "diff")
        branches.append(
            {
                "T": "T6",
                "pair": pair,
                "year": year,
                "note": f"{what}が週末の仮説 {h} と別の仮説（"
                + ", ".join(f"{t['date']} {t['e1'][shift]:+d} h" for t in conflicts)
                + "）",
            }
        )
        for t in conflicts:
            add(
                probe.SurveyRequest(
                    f"X-{pair}-T{t['date']}-re", "X", pair, "tuesday", t["date"], t["state"]
                ),
                "T6",
            )
        coordinator.append(
            f"T6: {pair} {year} の {'・'.join(states_c)} の週で、週末（E4 で USDJPY と同じ規則）は "
            f"{h} だが{what}が別のずれを示す。読み直しの要求を出す。"
            + (
                "その年は直接確認にしない"
                if kind == "exact"
                else "割れたままなら、その状態の週を採用を妨げる未解決とする"
            )
        )


def build_e1_ref(
    plan: Iterable[probe.SurveyRequest], csv_root: Path = CSV_ROOT
) -> dict[str, dict[str, list[str]]]:
    """火曜の区間ごとに、配布 CSV の bid で作った 1 分足（UTC の分の始め → 4 値の文字列）を作る。

    区間の前後 5 時間を含む（ずれの候補 −4〜+4 時間を覆う）。CSV は 1 ファイルを 1 回だけ読む。
    """
    by_file: dict[Path, list[tuple[str, datetime, datetime]]] = {}
    for req in plan:
        if req.kind != "tuesday":
            continue
        path = csv_root / req.pair / f"ticks_{req.pair}-oj5k_{req.date[:7]}.csv"
        if path.exists():
            w = req.window()
            span = (req.rid, w.start - timedelta(hours=5), w.end + timedelta(hours=5))
            by_file.setdefault(path, []).append(span)
    out: dict[str, dict[str, list[str]]] = {}
    for path, spans in by_file.items():
        lo, hi = min(s[1] for s in spans), max(s[2] for s in spans)
        bars: dict[str, dict[int, list[Decimal]]] = {rid: {} for rid, _, _ in spans}
        for ms, bid, _ask in probe.read_oanda_csv(path, lo, hi):
            m = ms // 60000 * 60
            b = Decimal(bid)
            for rid, s, e in spans:
                if s.timestamp() <= m < e.timestamp():
                    o = bars[rid].get(m)
                    bars[rid][m] = (
                        [b, b, b, b] if o is None else [o[0], max(o[1], b), min(o[2], b), b]
                    )
        for rid, bb in bars.items():
            out[rid] = {str(t): [str(v) for v in vals] for t, vals in sorted(bb.items())}
    return out


E4_FIELDS: tuple[str, ...] = (
    "pair",
    "sunday",
    "state",
    "boundary",
    "pair_bar_time",
    "usdjpy_bar_time",
    "diff_min",
    "pair_offset_h",
    "usdjpy_offset_h",
    "pair_residual_min",
    "supports_same_rule",
)


def write_e4_csv(rows: Iterable[Mapping[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=E4_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in E4_FIELDS})


#: 補助の比較で作る配布 CSV の 1 分足の価格の種類。
PRICE_BASES: tuple[str, ...] = ("bid", "ask", "mid")


def csv_minute_bars(
    ticks: Iterable[tuple[int, str, str]],
) -> dict[str, dict[int, tuple[float, float, float, float]]]:
    """配布 CSV の tick（UTC のミリ秒, bid, ask）から bid・ask・mid の 1 分足（分の始め）。"""
    out: dict[str, dict[int, list[float]]] = {b: {} for b in PRICE_BASES}
    for ms, bid, ask in ticks:
        m = ms // 60000 * 60
        vals = {"bid": float(bid), "ask": float(ask)}
        vals["mid"] = (vals["bid"] + vals["ask"]) / 2
        for b in PRICE_BASES:
            v = vals[b]
            o = out[b].get(m)
            out[b][m] = [v, v, v, v] if o is None else [o[0], max(o[1], v), min(o[2], v), v]
    return {b: {t: (v[0], v[1], v[2], v[3]) for t, v in bars.items()} for b, bars in out.items()}


def price_basis_stats(
    api_rows: Sequence[Mapping[str, Any]],
    ref: Mapping[str, Mapping[int, tuple[float, float, float, float]]],
    point: float,
    state: str,
) -> dict[str, Any]:
    """API の 1 分足と配布 CSV の bid・ask・mid の 1 分足を、ずれ（時間）ごとに比べた差の統計。

    補助の比較（人間の第 11 回の決定の 3）。価格の値は返さない（差は point 単位）。
    差が最小のずれは記録するだけで、時刻の確定には使わない。
    """
    api = {
        int(r["time"]): tuple(float(r[k]) for k in ("open", "high", "low", "close"))
        for r in api_rows
    }
    out: dict[str, Any] = {"state": state, "api_bars": len(api), "by_basis": {}}
    for basis in PRICE_BASES:
        rows: list[dict[str, Any]] = []
        for h in probe.E1_SHIFTS_HOURS:
            pairs = [
                (a, ref[basis][t - h * 3600]) for t, a in api.items() if t - h * 3600 in ref[basis]
            ]
            d = sorted(abs(a[3] - b[3]) / point for a, b in pairs)
            sd = sorted((a[3] - b[3]) / point for a, b in pairs)
            exact = sum(
                all(abs(x - y) < point / 2 for x, y in zip(a, b, strict=True)) for a, b in pairs
            )
            rows.append(
                {
                    "shift_hours": h,
                    "compared": len(pairs),
                    "ohlc_exact": exact,
                    "median_abs_close_diff_points": round(d[len(d) // 2], 1) if d else None,
                    "median_signed_close_diff_points": round(sd[len(sd) // 2], 1) if sd else None,
                    "share_close_within_1_point": round(sum(x <= 1 for x in d) / len(d), 3)
                    if d
                    else None,
                }
            )
        best = min(
            (r for r in rows if r["median_abs_close_diff_points"] is not None),
            key=lambda r: float(r["median_abs_close_diff_points"]),
            default=None,
        )
        out["by_basis"][basis] = {
            "by_shift": rows,
            "diff_best_shift_hours": best["shift_hours"] if best else None,
            "diff_hypotheses": [
                h for h in HYPOTHESES if best and EXPECTED_TUESDAY[state][h] == best["shift_hours"]
            ],
        }
    return out


def csv_weekend_gaps(path: Path, min_hours: float = 24.0) -> list[dict[str, str | float]]:
    """配布 CSV の元の時刻（変換の前）で ``min_hours`` 以上の空白の前後の tick の時刻。

    価格は読まない。
    """
    out: list[dict[str, str | float]] = []
    prev: datetime | None = None
    with path.open(encoding="utf-8") as f:
        next(f, None)
        for line in f:
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            t = datetime.strptime(f"{parts[0]} {parts[1][:8]}", "%Y.%m.%d %H:%M:%S")
            if prev is not None and (t - prev).total_seconds() >= min_hours * 3600:
                out.append(
                    {
                        "last_before_raw": prev.isoformat(),
                        "first_after_raw": t.isoformat(),
                        "gap_hours": round((t - prev).total_seconds() / 3600, 3),
                        "first_after_as_utc": probe.server_time_to_utc(
                            t.strftime("%Y.%m.%d"), t.strftime("%H:%M:%S.000")
                        ).isoformat(),
                    }
                )
            prev = t
    return out


# --- 最後の調査（付録 K-10。人間の決定 D12-41・第 12 回の決定） -----------------------------------

#: 判定する期間（各年の米国の切替日の日曜から、欧州の切替日の日曜 01:00 UTC の前まで）。
FINAL_PERIODS: dict[int, tuple[str, str]] = {
    2017: ("2017-03-12", "2017-03-26"),
    2019: ("2019-03-10", "2019-03-31"),
}
#: 対照の週末の状態と、既知のずれ（閉場・開場とも +2 時間）。
FINAL_CONTROL_STATE = "S1"
FINAL_CONTROL_OFFSETS = (2, 2)
#: 出力の区分はこの 2 つだけ（人間の第 12 回の決定）。
LABEL_CONFIRMED = "確認済み"
LABEL_ASSUMED = "仮定付き"


def build_final_ref(
    plan: Iterable[probe.SurveyRequest], csv_root: Path = CSV_ROOT
) -> dict[str, dict[str, dict[str, list[str]]]]:
    """最後の調査の要求ごとに、配布 CSV の bid・ask・mid の 1 分足を作る。

    形は 要求 → 種類 → UTC の分の始め → 4 値の文字列。区間の前後 5 時間を含む（ずれの候補
    −4〜+4 時間を覆う）。区間が月をまたげば両方の月のファイルを読む。CSV は 1 ファイルを 1 回だけ
    読む。週末の区間も作る（付録 K-10.2 の道具の変更 1）。
    """
    by_file: dict[Path, list[tuple[str, datetime, datetime]]] = {}
    for req in plan:
        w = req.window()
        lo, hi = w.start - timedelta(hours=5), w.end + timedelta(hours=5)
        for month in sorted({lo.strftime("%Y-%m"), hi.strftime("%Y-%m")}):
            path = csv_root / req.pair / f"ticks_{req.pair}-oj5k_{month}.csv"
            if path.exists():
                by_file.setdefault(path, []).append((req.rid, lo, hi))
    acc: dict[str, dict[str, dict[int, list[Decimal]]]] = {}
    for path, spans in by_file.items():
        lo, hi = min(s[1] for s in spans), max(s[2] for s in spans)
        for ms, bid, ask in probe.read_oanda_csv(path, lo, hi):
            m = ms // 60000 * 60
            b, a = Decimal(bid), Decimal(ask)
            vals = {"bid": b, "ask": a, "mid": (b + a) / 2}
            for rid, s, e in spans:
                if not s.timestamp() <= m < e.timestamp():
                    continue
                for basis in probe.REF_BASES:
                    bars = acc.setdefault(rid, {}).setdefault(basis, {})
                    v = vals[basis]
                    o = bars.get(m)
                    bars[m] = [v, v, v, v] if o is None else [o[0], max(o[1], v), min(o[2], v), v]
    return {
        rid: {
            basis: {str(t): [str(v) for v in vals] for t, vals in sorted(bars.items())}
            for basis, bars in by_basis.items()
        }
        for rid, by_basis in acc.items()
    }


def _aux_item(
    rid: str,
    date: str,
    side: str,
    basis: str,
    stats: Sequence[Mapping[str, Any]] | None,
    expected: Mapping[str, int],
    role: str,
) -> dict[str, Any]:
    """bid・ask・mid の照合の 1 つの読み（補助）。

    完全一致があればそのずれ、無ければ差が最小のずれ。
    """
    r = read_e1(stats, "", expected) if stats else None
    item: dict[str, Any] = {
        "rid": rid,
        "date": date,
        "side": side,
        "basis": basis,
        "role": role,
        "expected": dict(expected),
    }
    if r is None or r["evidence"] == "none":
        item.update(evidence="none", observed_shift_hours=None)
        return item
    shift = r["best_shift_hours"] if r["matched"] else r["diff_best_shift_hours"]
    item.update(
        evidence=r["evidence"],
        observed_shift_hours=shift,
        exact_ratio=r["exact_ratio"],
        diff_separation=r["diff_separation"],
        diff_by_shift=r["diff_by_shift"],
    )
    return item


def _side_ok(offset: int | None, residual: float | None) -> bool:
    return offset is not None and residual is not None and abs(residual) <= TOLERANCE_MINUTES


def _final_boundary(req: probe.SurveyRequest, rec: Mapping[str, Any] | None) -> dict[str, Any]:
    """週末の境界の読み（閉場と開場を別々に。±15 分は境界付近の更新時刻の差の許容だけ）。"""
    item: dict[str, Any] = {
        "rid": req.rid,
        "date": req.date,
        "state": req.state,
        "role": "対照" if req.state == FINAL_CONTROL_STATE else "判定",
        "expected_close": {h: EXPECTED[req.state][h][0] for h in HYPOTHESES},
        "expected_open": {h: EXPECTED[req.state][h][1] for h in HYPOTHESES},
        "close_readable": False,
        "open_readable": False,
        "close_offset_h": None,
        "open_offset_h": None,
    }
    if rec is None or not rec.get("bar_times"):
        item["reason"] = "not_returned"
        return item
    w = read_weekend(rec["bar_times"], req.date, req.state)
    close_ok = _side_ok(w.close_offset_h, w.close_residual_min)
    open_ok = _side_ok(w.open_offset_h, w.open_residual_min)
    item.update(
        close_readable=close_ok,
        open_readable=open_ok,
        reason=w.reason,
        close_offset_h=w.close_offset_h if close_ok else None,
        open_offset_h=w.open_offset_h if open_ok else None,
        close_residual_min=w.close_residual_min,
        open_residual_min=w.open_residual_min,
        friday_last=_iso(w.friday_last),
        sunday_first=_iso(w.sunday_first),
    )
    return item


def _final_aux(req: probe.SurveyRequest, rec: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if req.kind == "weekend":
        role = "対照" if req.state == FINAL_CONTROL_STATE else "判定"
        sides = (rec or {}).get("e1_aux") or {}
        for side, idx in (("close_side", 0), ("open_side", 1)):
            exp = {h: EXPECTED[req.state][h][idx] for h in HYPOTHESES}
            for basis in probe.REF_BASES:
                stats = (sides.get(side) or {}).get(basis)
                out.append(_aux_item(req.rid, req.date, side, basis, stats, exp, role))
        return out
    exp = dict(EXPECTED_TUESDAY[req.state])
    by_basis = dict(((rec or {}).get("e1_aux") or {}).get("all") or {})
    if "bid" not in by_basis and rec and rec.get("e1"):
        by_basis["bid"] = rec["e1"]
    for basis in probe.REF_BASES:
        out.append(_aux_item(req.rid, req.date, "all", basis, by_basis.get(basis), exp, "補助"))
    return out


def final_evaluate(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """最後の調査の記録から、期間を「確認済み」か「仮定付き」に分ける（区分はこの 2 つだけ）。

    確認済みの条件（付録 K-10.5 の (i)。(ii) は人間の第 12 回の決定で「仮定付き」に置き換えた）:

    - 判定の週末（S2M・S2E。2017・2019 年の 5 つ）の金曜の閉場が全部読めて、全部が同じずれ。
      ±15 分は境界付近の更新時刻の差の許容だけに使う。
    - 対照の週末（S1）が既知のずれ（閉場・開場とも +2 時間）で読める。
    - 補助の比較（bid・ask・mid。週末の両側と火曜）が、そのずれと矛盾しない（比べられないものは
      矛盾に数えない）。補助の比較は確認済みにする根拠にはしない。

    どれかが欠ければ「仮定付き」とし、候補の規則ごとの解釈と、各根拠の支持・不支持を並べる
    （候補は選ばない）。原データ（足の時刻・配布 CSV）は変えない。
    """
    final_ids = {r.rid for r in probe.FINAL_STAGE}
    latest: dict[str, Mapping[str, Any]] = {}
    for r in records:
        if r.get("skipped") or r["rid"] not in final_ids:
            continue
        if r["rid"] not in latest or r["executed_at_utc"] >= latest[r["rid"]]["executed_at_utc"]:
            latest[r["rid"]] = r
    boundary = [
        _final_boundary(req, latest.get(req.rid))
        for req in probe.FINAL_STAGE
        if req.kind == "weekend"
    ]
    aux = [a for req in probe.FINAL_STAGE for a in _final_aux(req, latest.get(req.rid))]
    judged = [b for b in boundary if b["role"] == "判定"]
    controls = [b for b in boundary if b["role"] == "対照"]
    reasons: list[str] = []
    unreadable = [b["rid"] for b in judged if not b["close_readable"]]
    if unreadable:
        reasons.append("金曜の閉場が読めない週末がある: " + ", ".join(unreadable))
    closes = {b["close_offset_h"] for b in judged if b["close_readable"]}
    if len(closes) > 1:
        reasons.append(
            "金曜の閉場のずれが週末で割れた: "
            + ", ".join(
                f"{b['date']} {b['close_offset_h']:+d} h" for b in judged if b["close_readable"]
            )
        )
    bad_controls = [
        b["rid"]
        for b in controls
        if (b["close_offset_h"], b["open_offset_h"]) != FINAL_CONTROL_OFFSETS
    ]
    if bad_controls:
        reasons.append("対照の週末が既知のずれ（+2 時間）で読めない: " + ", ".join(bad_controls))
    offset = next(iter(closes)) if len(closes) == 1 else None
    rule_hyps = [
        h
        for h in HYPOTHESES
        if offset is not None and all(b["expected_close"][h] == offset for b in judged)
    ]
    if offset is not None and not rule_hyps:
        reasons.append(f"閉場のずれ {offset:+d} h に合う仮説が無い")
    judged_aux = [a for a in aux if a["role"] != "対照" and a["observed_shift_hours"] is not None]
    if rule_hyps:
        conflicts = [
            a
            for a in judged_aux
            if a["observed_shift_hours"] not in {a["expected"][h] for h in rule_hyps}
        ]
        if conflicts:
            reasons.append(
                "補助の比較（bid・ask・mid）が閉場と別のずれを示す: "
                + ", ".join(
                    f"{a['date']} {a['side']} {a['basis']} {a['observed_shift_hours']:+d} h"
                    for a in conflicts
                )
            )
    confirmed = not reasons and bool(rule_hyps)
    label = LABEL_CONFIRMED if confirmed else LABEL_ASSUMED
    out: dict[str, Any] = {
        "label": label,
        "periods": [
            {"year": year, "from": start, "to_before": f"{end}T01:00Z", "label": label}
            for year, (start, end) in sorted(FINAL_PERIODS.items())
        ],
        "boundary": boundary,
        "aux": aux,
        "requests_read": len(latest),
        "raw_data_note": (
            "原データ（API の足の時刻・配布 CSV）は変えていない。生の価格は保存していない"
        ),
        "other_pairs_note": (
            "この判定は USDJPY だけの要求による。他の通貨に当てはめる根拠は、"
            "通貨の間の時刻の判定（E4。付録 K-9.5）で同じ時刻の規則を支持した範囲に限る"
        ),
    }
    if confirmed:
        assert offset is not None
        out["rule"] = {
            "hypotheses": rule_hyps,
            "api_minus_utc_hours_in_period": offset,
            "note": (
                "この期間では同じずれになる仮説を全部挙げた（期間の変換はどれでも同じ）"
                if len(rule_hyps) > 1
                else ""
            ),
            "grounds": [
                f"{b['date']}（{b['state']}）の金曜の閉場 {b['close_offset_h']:+d} h"
                f"（残り {b['close_residual_min']} 分）"
                for b in judged
            ]
            + [f"{b['date']}（対照）の閉場・開場 +2 h" for b in controls]
            + [f"補助の比較 {len(judged_aux)} 件が矛盾しない（確認済みの根拠にはしていない）"],
        }
    else:
        out["reasons"] = reasons
        out["candidates"] = _final_candidates(boundary, aux)
    return out


def _final_candidates(
    boundary: Sequence[Mapping[str, Any]], aux: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """仮定付きのときの、候補の規則ごとの解釈と、各根拠の支持・不支持（候補は選ばない）。"""
    out: list[dict[str, Any]] = []
    for h in HYPOTHESES:
        n = EXPECTED["S2M"][h][0]
        support: list[str] = []
        against: list[str] = []
        for b in boundary:
            if b["role"] != "判定":
                continue
            for side, key in (("閉場", "close"), ("開場", "open")):
                obs = b[f"{key}_offset_h"]
                if obs is None:
                    continue
                txt = f"{b['date']} {side} {obs:+d} h"
                (support if b[f"expected_{key}"][h] == obs else against).append(txt)
        for a in aux:
            obs = a["observed_shift_hours"]
            if a["role"] == "対照" or obs is None:
                continue
            txt = f"{a['date']} {a['side']} {a['basis']} {obs:+d} h（{a['evidence']}）"
            (support if a["expected"][h] == obs else against).append(txt)
        out.append(
            {
                "hypothesis": h,
                "api_minus_utc_hours_in_period": n,
                "interpretation": (
                    f"{h} を仮定すると、期間の中は API の時刻 − {n} 時間 = UTC"
                    "（欧州の切替日の 01:00 UTC の後は両方夏時間の規則）"
                ),
                "supported_by": support,
                "not_supported_by": against,
            }
        )
    return out


def final_md(res: Mapping[str, Any]) -> str:
    """最後の調査の判定の報告（Markdown。価格の値は含まない）。"""
    lines = [
        "# 最後の調査の判定（付録 K-10。区分は「確認済み」「仮定付き」の 2 つ）",
        "",
        "価格の値は含まない。",
        "",
        f"区分: **{res['label']}**（読んだ要求 {res['requests_read']}）",
        "",
        _cells("年", "期間", "区分"),
        _cells("---", "---", "---"),
    ]
    for p in res["periods"]:
        lines.append(_cells(p["year"], f"{p['from']} 〜 {p['to_before']} の前", p["label"]))
    if "rule" in res:
        rule = res["rule"]
        lines += [
            "",
            "## 規則（確認済み）",
            f"- 規則: {', '.join(rule['hypotheses'])}（期間の中の API の時刻 − UTC = "
            f"{rule['api_minus_utc_hours_in_period']:+d} 時間）",
        ]
        if rule["note"]:
            lines.append(f"- 注: {rule['note']}")
        lines.append("- 適用期間: 上の表の期間（USDJPY）")
        lines += [f"- 根拠: {g}" for g in rule["grounds"]]
    else:
        lines += ["", "## 確認済みにできなかった理由"]
        lines += [f"- {r}" for r in res["reasons"]] or ["- なし"]
        lines += [
            "",
            "## 候補の規則ごとの解釈（どれも選ばない。採用する仮説は戦略成績を見る前に固定する）",
            _cells("候補", "期間のずれ", "解釈", "支持する根拠", "支持しない根拠"),
            _cells(*["---"] * 5),
        ]
        for c in res["candidates"]:
            lines.append(
                _cells(
                    c["hypothesis"],
                    f"{c['api_minus_utc_hours_in_period']:+d} h",
                    c["interpretation"],
                    "、".join(c["supported_by"]) or "—",
                    "、".join(c["not_supported_by"]) or "—",
                )
            )
    lines += [
        "",
        "## 週末の境界（閉場・開場。±15 分は境界付近の更新時刻の差の許容だけ）",
        _cells(
            "要求", "日曜", "状態", "役割", "閉場(h)", "開場(h)", "閉場の残り(分)", "開場の残り(分)"
        ),
        _cells(*["---"] * 8),
    ]
    for b in res["boundary"]:
        lines.append(
            _cells(
                b["rid"],
                b["date"],
                b["state"],
                b["role"],
                "—" if b["close_offset_h"] is None else b["close_offset_h"],
                "—" if b["open_offset_h"] is None else b["open_offset_h"],
                b.get("close_residual_min", "—"),
                b.get("open_residual_min", "—"),
            )
        )
    lines += [
        "",
        "## 補助の比較（bid・ask・mid。差の統計だけ。確認済みの根拠にはしない）",
        _cells("要求", "側", "種類", "役割", "読み", "ずれ(h)", "期待（US/EU/FIXED）"),
        _cells(*["---"] * 7),
    ]
    for a in res["aux"]:
        e = a["expected"]
        obs = a["observed_shift_hours"]
        lines.append(
            _cells(
                a["rid"],
                a["side"],
                a["basis"],
                a["role"],
                a["evidence"],
                "—" if obs is None else f"{obs:+d}",
                f"{e['US']}/{e['EU']}/{e['FIXED']}",
            )
        )
    lines += ["", f"- {res['raw_data_note']}", f"- {res['other_pairs_note']}"]
    return "\n".join(lines) + "\n"


def _cells(*cells: object) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _md(ev: Evaluation) -> str:
    lines = ["# 時刻の調査の評価（価格の値は含まない）", "", f"記録した要求: {ev.sent}", ""]
    lines.append(
        _cells(
            "要求", "通貨", "種類", "日付", "状態", "件数", "金曜の閉場(h)", "日曜の開場(h)", "読み"
        )
    )
    lines.append(_cells(*["---"] * 9))
    for r in ev.rows:
        if r["kind"] == "weekend":
            hyp = ",".join(r["hypotheses"]) if r["readable"] else f"読めない（{r['reason']}）"
            close = f"{r['close_offset_h']}（{r['close_residual_min']} 分）"
            open_ = f"{r['open_offset_h']}（{r['open_residual_min']} 分）"
            cells = (
                r["rid"],
                r["pair"],
                "週末",
                r["date"],
                r["state"],
                r["count"],
                close,
                open_,
                hyp,
            )
        else:
            e1 = r.get("e1")
            if e1:
                hyps = ",".join(e1["hypotheses"]) or "—"
                txt = (
                    f"E1 ずれ {e1['best_shift_hours']} h、完全一致 {e1['exact_ratio']}、仮説 {hyps}"
                )
                if e1["evidence"] == "diff_only":
                    dh = ",".join(e1["diff_hypotheses"]) or "—"
                    txt += (
                        f"（差のみ: 最小 {e1['diff_best_shift_hours']} h、"
                        f"次との比 {e1['diff_separation']}、仮説 {dh}。確定に使わない）"
                    )
            else:
                txt = "E1 なし（CSV なし）"
            cells = (r["rid"], r["pair"], "火曜", r["date"], r["state"], r["count"], "—", "—", txt)
        lines.append(_cells(*cells))
    lines += ["", _cells("通貨", "年", "読めた週末", "読めない週末", "共通の仮説", "判定")]
    lines.append(_cells(*["---"] * 6))
    for (pair, year), y in sorted(ev.years.items()):
        verdict = "直接確認 " + y.get("rule", "") if y["status"] == "confirmed" else "未解決"
        if y.get("unresolved_states"):
            verdict += "（" + "・".join(y["unresolved_states"]) + " の週は未解決）"
        inter = ",".join(y["intersection"]) or "—"
        lines.append(_cells(pair, year, len(y["weeks"]), len(y["unreadable"]), inter, verdict))
    if ev.e4_rows:
        lines += [
            "",
            "## E4（通貨の間。±15 分は境界付近の更新時刻の差の許容。同じ時刻の認定ではない）",
        ]
        lines.append(
            _cells("通貨", "週末", "状態", "閉場の差(分)", "開場の差(分)", "同じ規則を支持")
        )
        lines.append(_cells(*["---"] * 6))
        pairs: dict[tuple[str, str], dict[str, Any]] = {}
        for r in ev.e4_rows:
            pairs.setdefault((r["pair"], r["sunday"]), {"state": r["state"]})[r["boundary"]] = r
        for (pair, sunday), d in sorted(pairs.items()):
            lines.append(
                _cells(
                    pair,
                    sunday,
                    d["state"],
                    d["close"]["diff_min"],
                    d["open"]["diff_min"],
                    "はい" if d["close"]["supports_same_rule"] else "いいえ",
                )
            )
    lines += ["", "## 決定木の分岐"]
    for b in ev.branches:
        reqs = ", ".join(b.get("requests", [])) or "追加なし"
        lines.append(f"- {b['T']}: {b['pair']} {b['year']} {b['note']} → {reqs}")
    if not ev.branches:
        lines.append("- なし")
    lines += ["", "## 司令塔の判断が要る事項"]
    lines += [f"- {c}" for c in ev.needs_coordinator] or ["- なし"]
    lines += ["", f"## 自動で足す要求（{len(ev.auto_plan)}）"]
    lines += [f"- {a['rid']}" for a in ev.auto_plan] or ["- なし"]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("e1-ref")
    e.add_argument("--out", required=True)
    v = sub.add_parser("evaluate")
    v.add_argument("summaries", nargs="+")
    v.add_argument("--extra-used", type=int, default=0)
    v.add_argument("--report", required=True)
    v.add_argument("--plan-out", required=True)
    v.add_argument("--e4-out", help="E4 の分単位の残差の CSV（時刻だけ）")
    a = sub.add_parser(
        "s2-audit", help="保存済みの API の 1 分足と配布 CSV の bid・ask・mid の差の統計"
    )
    a.add_argument("--api-m1", required=True)
    a.add_argument("--csv", required=True)
    a.add_argument("--state", required=True)
    a.add_argument("--point", type=float, required=True)
    b = sub.add_parser("csv-boundaries", help="配布 CSV の元の時刻での週末の空白の位置（時刻だけ）")
    b.add_argument("--csv", required=True)
    fr = sub.add_parser("final-ref", help="最後の調査の参照（配布 CSV の bid・ask・mid の 1 分足）")
    fr.add_argument("--out", required=True)
    fe = sub.add_parser("final-evaluate", help="最後の調査の判定（確認済み／仮定付き）")
    fe.add_argument("summaries", nargs="+")
    fe.add_argument("--report", required=True)
    fe.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    if args.command == "final-ref":
        fref = build_final_ref(probe.FINAL_STAGE)
        Path(args.out).write_bytes(gzip.compress(json.dumps(fref).encode("utf-8"), mtime=0))
        counts = {rid: {k: len(v) for k, v in bb.items()} for rid, bb in fref.items()}
        print(json.dumps(counts, ensure_ascii=False))
        return 0
    if args.command == "final-evaluate":
        frecs: list[dict[str, Any]] = []
        for path in args.summaries:
            frecs += json.loads(Path(path).read_text(encoding="utf-8"))["requests"]
        res = final_evaluate(frecs)
        Path(args.report).write_text(final_md(res), encoding="utf-8")
        Path(args.json_out).write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        summary = {"label": res["label"], "reasons": res.get("reasons", [])}
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if args.command == "s2-audit":
        api_rows = list(csv.DictReader(Path(args.api_m1).open(encoding="utf-8")))
        times = [int(r["time"]) for r in api_rows]
        lo = datetime.fromtimestamp(min(times), UTC) - timedelta(hours=5)
        hi = datetime.fromtimestamp(max(times), UTC) + timedelta(hours=6)
        ticks = list(probe.read_oanda_csv(Path(args.csv), lo, hi))
        res = price_basis_stats(api_rows, csv_minute_bars(ticks), args.point, args.state)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.command == "csv-boundaries":
        print(json.dumps(csv_weekend_gaps(Path(args.csv)), ensure_ascii=False))
        return 0
    if args.command == "e1-ref":
        plan = [r for reqs in probe.TIME_SURVEY_STAGES.values() for r in reqs]
        plan += [
            probe.SurveyRequest(f"X-{probe.USDJPY}-T{d}", "X", probe.USDJPY, "tuesday", d, "S2")
            for d in ("2019-03-12", "2019-03-26")
        ]
        ref = build_e1_ref(plan)
        Path(args.out).write_bytes(gzip.compress(json.dumps(ref).encode("utf-8"), mtime=0))
        print(json.dumps({rid: len(b) for rid, b in ref.items()}, ensure_ascii=False))
        return 0
    records: list[dict[str, Any]] = []
    for s in args.summaries:
        records += json.loads(Path(s).read_text(encoding="utf-8"))["requests"]
    ev = evaluate(records, args.extra_used)
    Path(args.report).write_text(_md(ev), encoding="utf-8")
    if args.e4_out:
        write_e4_csv(ev.e4_rows, Path(args.e4_out))
    Path(args.plan_out).write_text(
        json.dumps(ev.auto_plan, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "sent": ev.sent,
                "branches": [b["T"] for b in ev.branches],
                "auto_requests": len(ev.auto_plan),
                "needs_coordinator": ev.needs_coordinator,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
