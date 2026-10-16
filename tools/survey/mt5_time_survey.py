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
}
#: 火曜の区間（その日の状態）での期待されるずれ。
EXPECTED_TUESDAY: dict[str, dict[str, int]] = {
    "S1": {"US": 2, "EU": 2, "FIXED": 2},
    "S2": {"US": 3, "EU": 2, "FIXED": 2},
    "S3": {"US": 3, "EU": 3, "FIXED": 2},
    "S4": {"US": 3, "EU": 2, "FIXED": 2},
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


def read_e1(e1: Sequence[Mapping[str, Any]] | None, state: str) -> dict[str, Any] | None:
    """火曜の E1 の統計から、一致したずれと、それで説明できる仮説を読む。

    4 値の完全一致が ``E1_MATCH_RATIO`` 以上なら ``evidence`` は ``exact`` で、``hypotheses`` を
    確定の材料にする。完全一致が足りなくても比べた足があれば ``diff_only`` とし、差の中央値が最小の
    ずれ（``diff_best_shift_hours``）と、その仮説（``diff_hypotheses``）、2 番目に小さい差との比
    （``diff_separation``）を記録する。``diff_only`` は確定に使わない（追加の確認を出す材料だけ）。
    """
    if not e1:
        return None
    best = max(e1, key=lambda r: (r["ohlc_exact"], -(r["median_abs_close_diff_points"] or 1e9)))
    ratio = best["ohlc_exact"] / best["compared"] if best["compared"] else 0.0
    matched = ratio >= E1_MATCH_RATIO
    # E1 のずれ h は「API の時刻 − h 時間 = UTC」なので、API の時刻 − UTC = h。
    hyps = [h for h in HYPOTHESES if EXPECTED_TUESDAY[state][h] == best["shift_hours"]]
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
        "diff_hypotheses": [
            h
            for h in HYPOTHESES
            if diff_best is not None and EXPECTED_TUESDAY[state][h] == diff_best
        ],
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
    for (pair, _year), y in years.items():
        if pair != probe.USDJPY and y.get("confirmed_weeks") and not y["unreadable"]:
            if len(y["confirmed_weeks"]) == len(y["weeks"]):
                y["status"] = "confirmed"
                base_unres = years.get((probe.USDJPY, _year), {}).get("unresolved_states")
                if base_unres:  # USDJPY の未解決の状態は、同じ規則を支持する通貨にも残る
                    y["unresolved_states"] = list(base_unres)

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
    args = parser.parse_args(argv)
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
