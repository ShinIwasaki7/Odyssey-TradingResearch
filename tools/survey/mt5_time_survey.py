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
"""

from __future__ import annotations

import argparse
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
    """火曜の E1 の統計から、一致したずれと、それで説明できる仮説を読む。"""
    if not e1:
        return None
    best = max(e1, key=lambda r: (r["ohlc_exact"], -(r["median_abs_close_diff_points"] or 1e9)))
    ratio = best["ohlc_exact"] / best["compared"] if best["compared"] else 0.0
    matched = ratio >= E1_MATCH_RATIO
    # E1 のずれ h は「API の時刻 − h 時間 = UTC」なので、API の時刻 − UTC = h。
    hyps = [h for h in HYPOTHESES if EXPECTED_TUESDAY[state][h] == best["shift_hours"]]
    return {
        "best_shift_hours": best["shift_hours"],
        "exact_ratio": round(ratio, 3),
        "matched": matched,
        "hypotheses": hyps if matched else [],
    }


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
                if all(h in s for s in e1_sets):
                    y["status"] = "confirmed"
                    y["rule"] = h
                else:
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
        same_time = bool(
            uj
            and uj.readable
            and w.readable
            and (uj.friday_last, uj.sunday_first) == (w.friday_last, w.sunday_first)
        )
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

    remaining = probe.TIME_SURVEY_EXTRA_REQUESTS - extra_used
    if len(auto) > remaining:
        coordinator.append(
            f"T7: 決定木の追加の要求 {len(auto)} が残り {remaining} を超える。"
            "上限内の分だけ出すか、報告して終了する"
        )
        auto = auto[: max(remaining, 0)]
    return Evaluation(rows, years, branches, auto, coordinator, len(recs))


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
            else:
                txt = "E1 なし（CSV なし）"
            cells = (r["rid"], r["pair"], "火曜", r["date"], r["state"], r["count"], "—", "—", txt)
        lines.append(_cells(*cells))
    lines += ["", _cells("通貨", "年", "読めた週末", "読めない週末", "共通の仮説", "判定")]
    lines.append(_cells(*["---"] * 6))
    for (pair, year), y in sorted(ev.years.items()):
        verdict = "直接確認 " + y.get("rule", "") if y["status"] == "confirmed" else "未解決"
        inter = ",".join(y["intersection"]) or "—"
        lines.append(_cells(pair, year, len(y["weeks"]), len(y["unreadable"]), inter, verdict))
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
    args = parser.parse_args(argv)
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
