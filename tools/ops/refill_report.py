"""補充の後の「残存欠落と実行可能な連続期間」の報告を作る（D03 §14.14 の段 10・11、§14.15）。

報告の置き場は ``docs/data/research_history_gaps.md``（RF-9 の決定。新しい snapshot の内容に
書き直し、旧 snapshot の数字は比較の表と旧 snapshot の CSV に残す）。本スクリプトは、その書き
直しの材料になる報告の本文（Markdown）と、残存欠落の区間ごとの理由の CSV を作る。文書そのもの
は人が書き直す（本スクリプトは文書を上書きしない）。

読むもの（戦略の成績は読まない。D03 §14.2 の 7）:

- 新しい snapshot と旧 snapshot の ``manifest.json``（確定済みの分類と ``sources``）。
- 新しい snapshot の ``sources`` が指す補充分の ``refill_manifest.json`` と ``validation.json``
  （補充の置き場 ``data/raw/market/refill/<refill_id>/``）。
- 検証で不合格になり補充分が無い計画の取得記録（``--rejected-plan``。``_work/<plan_id>/`` の
  ``plan.json`` と ``journal.jsonl`` の最後の不合格の行）。

報告の書式（D03 §14.15）:

1. 入力と出力の識別（旧・新 snapshot、カレンダーの版、補充の識別子）。
2. 補充の結果（系列ごとの対象足・補充した本数・作らなかった本数（理由別）、未照合の塊、
   出来高不明、検証の要約、「要確認」の塊）。
3. 残存欠落（系列別・年別・理由別。区間ごとの理由は CSV）。
4. 実行可能な連続期間（20 系列すべて・USDJPY 15 分足の上位、旧 snapshot との比較）。
5. 再現のコマンドと、戦略の成績を読んでいないことの明記。

標準ライブラリだけを使う。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tools.ops import research_history_gaps as rhg

#: 残存欠落の理由（CSV の値）。日本語の意味は ``REASON_LABELS``（D03 §14.15 の 3）。
NOT_FETCHED = "NOT_FETCHED"
PROVIDER_NO_TICKS = "PROVIDER_NO_TICKS"
UNRECONCILED = "UNRECONCILED"
VALIDATION_REJECTED = "VALIDATION_REJECTED"
NOT_PLANNED = "NOT_PLANNED"

REASON_LABELS: dict[str, str] = {
    NOT_FETCHED: "取得できなかった（HTTP 404 を含む）",
    PROVIDER_NO_TICKS: "提供元にも tick が無い",
    UNRECONCILED: "未照合（判断待ち）",
    VALIDATION_REJECTED: "検証で不合格になり補充しなかった",
    NOT_PLANNED: "補充の計画に入っていない（計画の対象外）",
}
REASON_ORDER: tuple[str, ...] = tuple(REASON_LABELS)

#: 補充の manifest の「作らなかった理由」から残存欠落の理由への対応（D03 §14.8 の表）。
NOT_BUILT_TO_REASON: dict[str, str] = {
    "HOUR_NOT_FETCHED": NOT_FETCHED,
    "PROVIDER_EMPTY": PROVIDER_NO_TICKS,
    "NO_TICK_IN_BAR": PROVIDER_NO_TICKS,
    "UNRECONCILED": UNRECONCILED,
}

RESIDUAL_FIELDS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "start_utc",
    "end_utc",
    "duration_hours",
    "year_of_bar_end",
    "reason",
    "holiday_candidate_pending",
)

#: 時間足の足の長さ（残存欠落の区間を足 1 本ずつにほどくため）。
BAR_LENGTH: dict[str, timedelta] = {"15m@v1": timedelta(minutes=15), "1h@v1": timedelta(hours=1)}

REFILL_PREFIX = "data/raw/market/refill/"


class ReportInputError(RuntimeError):
    """報告の入力（manifest・補充分・取得記録）が読めない・食い違う。"""


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise ReportInputError(f"ファイルが無い: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_snapshot(snapshot_root: Path, snapshot_id: str) -> dict[str, Any]:
    """snapshot の manifest を読む（記録された識別子がディレクトリ名と一致すること）。"""
    manifest = _load_json(snapshot_root / snapshot_id / "manifest.json")
    if manifest.get("snapshot_id") != snapshot_id:
        raise ReportInputError(f"snapshot_id が一致しない: {snapshot_root / snapshot_id}")
    return dict(manifest)


def refill_ids_of(manifest: dict[str, Any]) -> list[str]:
    """snapshot の ``sources`` が指す補充分の識別子（整列・重複なし）。"""
    found = {
        src["path"][len(REFILL_PREFIX) :].split("/", 1)[0]
        for src in manifest["sources"]
        if isinstance(src.get("path"), str) and src["path"].startswith(REFILL_PREFIX)
    }
    return sorted(found)


def load_refill(refill_root: Path, refill_id: str) -> dict[str, Any]:
    """補充分の manifest と検証記録を読む（識別子がディレクトリ名と一致すること）。"""
    manifest = _load_json(refill_root / refill_id / "refill_manifest.json")
    if manifest.get("refill_id") != refill_id:
        raise ReportInputError(f"refill_id が一致しない: {refill_root / refill_id}")
    validation = _load_json(refill_root / refill_id / "validation.json")
    return {"manifest": manifest, "validation": validation}


def load_rejected(refill_root: Path, plan_id: str) -> dict[str, Any]:
    """補充分の無い不合格の計画の、対象足と最後の不合格の行の記録を読む（D03 §14.7）。"""
    plan = _load_json(refill_root / "_work" / plan_id / "plan.json")
    journal = refill_root / "_work" / plan_id / "journal.jsonl"
    if not journal.is_file():
        raise ReportInputError(f"取得記録が無い: {journal}")
    last: dict[str, Any] | None = None
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)["entry"]
        except (ValueError, KeyError, TypeError):
            continue  # 書き込みの途中で止まった最後の行（W3）
        if entry.get("kind") == "validation" and entry.get("passed") is False:
            last = entry
    if last is None:
        raise ReportInputError(f"不合格の検証の行が無い: {journal}")
    return {"plan_id": plan_id, "plan": plan, "validation": last}


def _tf_key(series: str, version: int) -> tuple[str, str]:
    symbol, timeframe, _ = series.split("/")
    return symbol, f"{timeframe}@v{version}"


def bar_reasons(
    refills: list[dict[str, Any]], rejected: list[dict[str, Any]]
) -> dict[tuple[str, str, datetime], str]:
    """足 1 本ごとの「補充しなかった理由」（補充分の記録と不合格の計画から）。"""
    reasons: dict[tuple[str, str, datetime], str] = {}
    for item in rejected:
        for target in item["plan"]["target_bars"]:
            symbol, tf = _tf_key(target["series"], target["timeframe_version"])
            reasons[(symbol, tf, rhg.parse_utc(target["start"]))] = VALIDATION_REJECTED
        for not_built in item["validation"]["details"].get("not_built", []):
            symbol, tf = _tf_key(not_built["series"], not_built["timeframe_version"])
            reasons[(symbol, tf, rhg.parse_utc(not_built["start"]))] = NOT_BUILT_TO_REASON[
                not_built["reason"]
            ]
    for refill in refills:
        for not_built in refill["manifest"]["not_built"]:
            symbol, tf = _tf_key(not_built["series"], not_built["timeframe_version"])
            reasons[(symbol, tf, rhg.parse_utc(not_built["start"]))] = NOT_BUILT_TO_REASON[
                not_built["reason"]
            ]
    return reasons


def interval_reason(gap: rhg.GapInterval, reasons: dict[tuple[str, str, datetime], str]) -> str:
    """欠落区間の理由。区間の足ごとの理由が分かれれば ``|`` でつなぐ（理由の順）。"""
    step = BAR_LENGTH[gap.timeframe]
    found: set[str] = set()
    moment = gap.start
    while moment < gap.end:
        found.add(reasons.get((gap.symbol, gap.timeframe, moment), NOT_PLANNED))
        moment += step
    return "|".join(reason for reason in REASON_ORDER if reason in found)


def holiday_candidate_pending(gap: rhg.GapInterval) -> bool:
    """保留した休場の候補（D03 §3.4.2 の候補 1・2・9。RF-11・RF-12・RF-19）に当たるか。"""
    attribution, _, _ = rhg.classify(gap)
    if attribution in (rhg.CALENDAR, rhg.CALENDAR_UNDECLARED_OR_PARTLY_SOURCE):
        return True
    start_ny = gap.start.astimezone(rhg.NEW_YORK)
    return start_ny.weekday() == 4 and start_ny.hour == 16 and gap.hours <= 1.5


def residual_rows(
    merged: dict[tuple[str, str], list[rhg.Interval]],
    reasons: dict[tuple[str, str, datetime], str],
) -> list[dict[str, Any]]:
    """新しい snapshot の残存欠落の区間ごとの行（CSV）。"""
    rows: list[dict[str, Any]] = []
    for symbol in rhg.SYMBOLS:
        for tf in rhg.TIMEFRAMES:
            for start, end in merged[(symbol, tf)]:
                gap = rhg.GapInterval(symbol, tf, start, end)
                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": tf,
                        "start_utc": rhg.fmt_utc(start),
                        "end_utc": rhg.fmt_utc(end),
                        "duration_hours": round(gap.hours, 2),
                        "year_of_bar_end": end.year,
                        "reason": interval_reason(gap, reasons),
                        "holiday_candidate_pending": holiday_candidate_pending(gap),
                    }
                )
    return rows


def _windows(
    merged: dict[tuple[str, str], list[rhg.Interval]],
    bounds: dict[tuple[str, str], rhg.Interval],
) -> tuple[list[rhg.Interval], list[rhg.Interval]]:
    """20 系列すべてと USDJPY 15 分足の、欠落の無い連続区間（長い順）。"""
    lo_all = max(lo for lo, _ in bounds.values())
    hi_all = min(hi for _, hi in bounds.values())
    union = rhg.merge_adjacent([g for gaps in merged.values() for g in gaps])
    all_series = sorted(
        rhg.contiguous_windows(union, lo_all, hi_all), key=lambda w: w[1] - w[0], reverse=True
    )
    key = ("USDJPY", "15m@v1")
    usdjpy: list[rhg.Interval] = []
    if key in bounds:
        lo, hi = bounds[key]
        usdjpy = sorted(
            rhg.contiguous_windows(merged[key], lo, hi), key=lambda w: w[1] - w[0], reverse=True
        )
    return all_series, usdjpy


def _days(window: rhg.Interval) -> str:
    return f"{(window[1] - window[0]).total_seconds() / 86400:.2f}"


def _hours(gaps: list[rhg.Interval]) -> float:
    return sum((e - s).total_seconds() / 3600 for s, e in gaps)


def render_report(
    *,
    old_id: str,
    new_id: str,
    new_manifest: dict[str, Any],
    old_merged: dict[tuple[str, str], list[rhg.Interval]],
    old_bounds: dict[tuple[str, str], rhg.Interval],
    new_merged: dict[tuple[str, str], list[rhg.Interval]],
    new_bounds: dict[tuple[str, str], rhg.Interval],
    refills: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    command: str,
) -> str:
    """報告の本文（Markdown。D03 §14.15 の 1〜5）。価格は書かない（前後の差は pip だけ）。"""
    conversion = new_manifest.get("conversion", {})
    calendar_id = conversion.get("calendar_id")
    calendar_version = conversion.get("calendar_version")
    refill_ids = ", ".join(f"`{item['manifest']['refill_id']}`" for item in refills) or "なし"
    rejected_ids = ", ".join(f"`{item['plan_id']}`" for item in rejected) or "なし"
    lines: list[str] = ["# 補充の後の残存欠落と実行可能な連続期間（報告の材料）", ""]
    lines += ["## 1. 入力と出力の識別", ""]
    lines += [
        "| 項目 | 値 |",
        "|---|---|",
        f"| 旧 snapshot | `{old_id}` |",
        f"| 新 snapshot | `{new_id}` |",
        f"| カレンダー | `{calendar_id}` 版 {calendar_version} |",
        f"| 補充の識別子 | {refill_ids} |",
        f"| 不合格の計画 | {rejected_ids} |",
        "",
    ]

    lines += ["## 2. 補充の結果", ""]
    lines += [
        "補充した足の出来高は 0 で、「出来高不明」を意味する"
        "（実際の出来高として使わない。D03 §14.6）。",
        "",
        "| 系列 | 対象足 | 補充した | 作らなかった | 理由別（作らなかった足） |",
        "|---|---|---|---|---|",
    ]
    for refill in refills:
        manifest = refill["manifest"]
        by_reason: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for item in manifest["not_built"]:
            by_reason[item["series"]][item["reason"]] += 1
        for count in manifest["series_counts"]:
            detail = ", ".join(
                f"{reason} {number}"
                for reason, number in sorted(by_reason[count["series"]].items())
            )
            lines.append(
                f"| {count['series']} | {count['targets']} | {count['built']} |"
                f" {count['not_built']} | {detail or '—'} |"
            )
    lines.append("")
    unreconciled = [item for refill in refills for item in refill["manifest"]["unreconciled"]] + [
        item for rej in rejected for item in rej["validation"]["details"].get("unreconciled", [])
    ]
    lines.append(f"未照合の塊（補充分に書かず、人間の判断を待つ）: {len(unreconciled)}")
    lines += [
        f"- {item['series']} {item['chunk_start']}（対象足 {item['target_count']} 本）"
        for item in unreconciled
    ]
    lines.append("")
    for refill in refills:
        validation = refill["validation"]
        review = [item for item in validation["neighbors"] if item["needs_review"]]
        short = refill["manifest"]["refill_id"][:12]
        lines.append(
            f"検証（`{short}…`）: 照合 {validation['reconciled_count']} 本・"
            f"一致 {validation['matched_count']} 本、"
            f"範囲外の tick {validation['out_of_range_tick_count']} 件、"
            f"bid が ask より大きい tick {validation['bid_above_ask_tick_count']} 件"
            f"（合否に使わない）、「要確認」の印 {len(review)} 件"
        )
        lines += [
            f"- 要確認: {item['series']} {item['chunk_start']} {item['side']}"
            f" 差 {item['difference_pips']} pip"
            for item in review
        ]
    for rej in rejected:
        lines.append(
            f"不合格の計画 `{rej['plan_id'][:12]}…`: " + "; ".join(rej["validation"]["reasons"])
        )
    lines.append("")

    lines += ["## 3. 残存欠落", ""]
    lines += ["### 3.1 系列別（旧 → 新。件数 / 合計時間）", ""]
    lines += ["| 系列 | 旧 | 新 |", "|---|---|---|"]
    for symbol in rhg.SYMBOLS:
        for tf in rhg.TIMEFRAMES:
            old = old_merged.get((symbol, tf), [])
            new = new_merged.get((symbol, tf), [])
            lines.append(
                f"| {symbol} {tf} | {len(old)} / {_hours(old):.2f}h |"
                f" {len(new)} / {_hours(new):.2f}h |"
            )
    total_old = [g for gaps in old_merged.values() for g in gaps]
    total_new = [g for gaps in new_merged.values() for g in gaps]
    lines += [
        f"| **合計** | **{len(total_old)} / {_hours(total_old):.2f}h** |"
        f" **{len(total_new)} / {_hours(total_new):.2f}h** |",
        "",
    ]
    lines += ["### 3.2 年別（新。足の終端の年）", "", "| 年 | 件数 / 合計 |", "|---|---|"]
    by_year: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        by_year[row["year_of_bar_end"]].append(row["duration_hours"])
    for year in sorted(by_year):
        lines.append(f"| {year} | {len(by_year[year])} / {sum(by_year[year]):.2f}h |")
    lines.append("")
    lines += ["### 3.3 理由別（新）", "", "| 理由 | 件数 / 合計 |", "|---|---|"]
    by_reason_rows: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_reason_rows[row["reason"]].append(row["duration_hours"])
    for reason in sorted(by_reason_rows):
        label = " ＋ ".join(REASON_LABELS[part] for part in reason.split("|"))
        hours = by_reason_rows[reason]
        lines.append(f"| {label}（`{reason}`） | {len(hours)} / {sum(hours):.2f}h |")
    pending = [row for row in rows if row["holiday_candidate_pending"]]
    lines += [
        "",
        f"このうち休場の候補（保留）に当たる区間: {len(pending)} / "
        f"{sum(row['duration_hours'] for row in pending):.2f}h（D03 §3.4.2 の候補 1・2・9）",
        "",
    ]

    lines += ["## 4. 実行可能な連続期間", ""]
    old_all, old_usdjpy = _windows(old_merged, old_bounds)
    new_all, new_usdjpy = _windows(new_merged, new_bounds)
    for title, windows in (
        ("20 系列すべてに欠落の無い連続区間の上位 5（新）", new_all),
        ("USDJPY 15 分足だけの上位 5（新）", new_usdjpy),
    ):
        lines += [
            f"### {title}",
            "",
            "| 順位 | 始端（UTC） | 終端（UTC） | 日数 |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {rank} | {rhg.fmt_utc(s)} | {rhg.fmt_utc(e)} | {_days((s, e))} |"
            for rank, (s, e) in enumerate(windows[:5], start=1)
        ]
        lines.append("")
    lines += [
        "### 旧 snapshot との比較",
        "",
        "| 項目 | 旧 | 新 |",
        "|---|---|---|",
        f"| 20 系列の連続区間の数 | {len(old_all)} | {len(new_all)} |",
        f"| 20 系列の最長（日） | {_days(old_all[0]) if old_all else '—'} |"
        f" {_days(new_all[0]) if new_all else '—'} |",
        f"| USDJPY 15 分足の連続区間の数 | {len(old_usdjpy)} | {len(new_usdjpy)} |",
        f"| USDJPY 15 分足の最長（日） | {_days(old_usdjpy[0]) if old_usdjpy else '—'} |"
        f" {_days(new_usdjpy[0]) if new_usdjpy else '—'} |",
        "",
    ]

    lines += ["## 5. 再現", "", "```", command, "```", ""]
    lines.append(
        "戦略の成績（指標・レポート・`runs/`）は読んでいない。snapshot の manifest・補充分の"
        " manifest と検証記録・取得記録だけを読んだ（D03 §14.2 の 7）。"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--snapshot-root", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--snapshot-id", required=True, help="新しい snapshot の識別子")
    parser.add_argument(
        "--previous-snapshot-id",
        default=rhg.DEFAULT_SNAPSHOT_ID,
        help="比較する旧 snapshot の識別子（既定は補充の前の承認済み snapshot）",
    )
    parser.add_argument("--refill-root", type=Path, default=Path("data/raw/market/refill"))
    parser.add_argument(
        "--rejected-plan",
        action="append",
        default=[],
        help="検証で不合格になり補充分が無い計画の plan_id（0 回以上）",
    )
    parser.add_argument("--out", type=Path, required=True, help="報告と CSV の出力先ディレクトリ")
    args = parser.parse_args(argv)

    try:
        new_manifest = load_snapshot(args.snapshot_root, args.snapshot_id)
        old_manifest = load_snapshot(args.snapshot_root, args.previous_snapshot_id)
        refills = [load_refill(args.refill_root, rid) for rid in refill_ids_of(new_manifest)]
        rejected = [load_rejected(args.refill_root, plan) for plan in args.rejected_plan]
    except (ReportInputError, ValueError, KeyError) as exc:
        print(f"報告の入力が読めない: {exc}", file=sys.stderr)
        return 1
    new_merged, new_bounds = rhg.collect_gaps(new_manifest)
    old_merged, old_bounds = rhg.collect_gaps(old_manifest)
    rows = residual_rows(new_merged, bar_reasons(refills, rejected))
    command = " ".join(
        [
            "uv run python tools/ops/refill_report.py",
            f"--snapshot-root {args.snapshot_root}",
            f"--snapshot-id {args.snapshot_id}",
            f"--previous-snapshot-id {args.previous_snapshot_id}",
            f"--refill-root {args.refill_root}",
            *(f"--rejected-plan {plan}" for plan in args.rejected_plan),
            f"--out {args.out}",
        ]
    )
    report = render_report(
        old_id=args.previous_snapshot_id,
        new_id=args.snapshot_id,
        new_manifest=new_manifest,
        old_merged=old_merged,
        old_bounds=old_bounds,
        new_merged=new_merged,
        new_bounds=new_bounds,
        refills=refills,
        rejected=rejected,
        rows=rows,
        command=command,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    rhg.write_csv(args.out / "residual_gaps.csv", RESIDUAL_FIELDS, rows)
    (args.out / "refill_report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
