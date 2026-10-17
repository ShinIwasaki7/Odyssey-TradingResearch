"""基盤の受入れ（MT5 経路）と全期間の集計の道具（PR #85）の突き合わせ（D12 §11.13 の 15）。

2026-10-17 の指示 3-C「実データでは、集計結果と基盤の足生成・
能力検査の結果を突き合わせてください」の
実データの側。取得済みの原データ（`data/raw/oanda_mt5/<取得日>/`）の指定した通貨・月を、**本番の受入れ**
（`odyssey_fx.app.mt5.accept_mt5_in_memory`。時刻の規則の表・`fx_ny17_oanda` 版 1・
数え方の規則を本番の
入口で読む）で変換の組（標準・候補）ごとにメモリの上の暫定 snapshot にし、PR #85 の最終集計の出力
（main の `docs/data/full_period_feasibility/`）と比べる。

比べるもの（価格は読まない・出さない。件数・時刻だけ）:

1. 日足（`daily_bars.csv`。取引日 × 変換の組）: 取引日の区分、存在すべき分・記録ありの分・
無記録の分・
   取得不足の分、最初と最後の観測の時刻、公開時刻、時刻の規則の表の行と区分の印。
2. 全分無記録の足（`no_record_bars.csv`。15 分足・1 時間足・4 時間足・日足 × 変換の組）: 基盤の
   `NO_RECORD_BAR` の足の集合と、道具の行の集合（比べた範囲の中）。
3. 準備のやり直し（`warmup_resets.csv`。日足 60 本 × 変換の組）: 準備不足になる時刻（基盤は表22
と同じ
   `marketdata.application.read_readiness.read_resets`。D12-127）と、準備が再び整う時刻。基盤の側は比べた
   範囲の外の足を持たないので、整う時刻が範囲の外なら「範囲の外」と記録する。

両方向で比べる: 基盤の取引日の範囲に入る道具の行はすべて出し、片方にしか無いものは不一致にする。

実行（リポジトリの根で）::

    uv run python -m tools.survey.mt5_platform_crosscheck \\
        --root <原データの置き場>/data/raw/oanda_mt5/2026-10-16 \\
        --feasibility-dir docs/data/full_period_feasibility \\
        --pairs USDJPY EURUSD --months 2018-12 2019-01 2019-02 2022-12 2023-01 2023-02 \\
        --out-dir docs/data/mt5_platform_crosscheck
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from odyssey_fx.app.composition import code_version
from odyssey_fx.app.mt5 import accept_mt5_in_memory, load_datasource, load_mt5_rules
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.read_readiness import read_resets
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.readiness import ReadKind, ReadWindowSpec
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant

__all__ = ["DAY_COLUMNS", "compare_days", "compare_no_record", "main"]

DAILY = "1d_ny17"
#: 全分無記録の足を比べる時間足（道具の `no_record_bars.csv` の `timeframe`）。
NO_RECORD_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h_ny17", "1d_ny17")

#: 日足で比べる項目（基盤 `platform_*` と道具 `tool_*` の組）。
DAY_FIELDS: tuple[str, ...] = (
    "class",
    "expected",
    "recorded",
    "unrecorded",
    "shortfall",
    "first_observed",
    "last_observed",
    "published_at",
    "time_rule_row",
    "time_rule_mark",
)

DAY_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "trading_day",
    "interval_start_utc",
    "interval_end_utc",
    *(f"{side}_{name}" for name in DAY_FIELDS for side in ("platform", "tool")),
    "match",
)

NO_RECORD_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "timeframe",
    "interval_start_utc",
    "on_platform",
    "on_tool",
    "match",
)

RESET_COLUMNS: tuple[str, ...] = (
    "pair",
    "variant",
    "no_record_day_start_utc",
    "window_kind",
    "window_bars",
    "platform_insufficient_from",
    "tool_not_ready_from",
    "platform_ready_at",
    "tool_ready_again_at",
    "ready_compared",
    "match",
)

_TOOL_CLASS = {"full": "fully_observed", "partial": "partial", "no_record": "no_record"}


def _iso(moment: UtcTime) -> str:
    return moment.value.strftime("%Y-%m-%dT%H:%MZ")


def _tool_class(row: Mapping[str, str]) -> str:
    """道具の行の区分（`day_class`。取得不足の分があれば取得不足、存在すべき分 0 は休場）。"""
    if int(row["expected_minutes"]) == 0:
        return "closed"
    if int(row["shortfall_minutes"]) > 0:
        return "shortfall"
    return _TOOL_CLASS.get(row["day_class"], row["day_class"])


@dataclass(frozen=True, slots=True)
class _PlatformDay:
    interval: Interval
    bar_class: str
    expected: int
    recorded: int
    shortfall: int
    first_observed: str
    last_observed: str = ""
    published_at: str = ""
    time_rule_row: str = ""
    time_rule_mark: str = ""

    def value(self, name: str) -> Any:
        if name == "class":
            return self.bar_class
        if name == "unrecorded":
            return self.expected - self.recorded - self.shortfall
        return getattr(self, name)


def _platform_days(
    bars: Sequence[Bar],
    no_record: Mapping[UtcTime, tuple[int, Interval]],
    shortfall: Mapping[UtcTime, tuple[Mapping[str, str], Interval]],
    marks: Mapping[int, str],
) -> dict[str, _PlatformDay]:
    days: dict[str, _PlatformDay] = {}
    for bar in bars:
        coverage = bar.coverage
        assert coverage is not None
        days[_iso(bar.bar_start)] = _PlatformDay(
            interval=bar.interval,
            bar_class="fully_observed" if coverage.is_fully_observed else "partial",
            expected=coverage.expected_minutes,
            recorded=coverage.recorded_minutes,
            shortfall=0,
            first_observed=_iso(coverage.first_observed_at),
            last_observed=_iso(coverage.last_observed_at),
            # 日足の公開時刻は区間の終わり（通常遅延 0。D03 §15.5）。観測が早く終わっても早めない。
            published_at=_iso(bar.bar_end),
            time_rule_row=str(coverage.time_rule_row),
            time_rule_mark=marks.get(coverage.time_rule_row, ""),
        )
    for start, (expected, interval) in no_record.items():
        days[_iso(start)] = _PlatformDay(
            interval=interval,
            bar_class="no_record",
            expected=expected,
            recorded=0,
            shortfall=0,
            first_observed="",
            published_at=_iso(interval.end),
        )
    for start, (detail, interval) in shortfall.items():
        first = detail.get("first_observed_at", "")
        days[_iso(start)] = _PlatformDay(
            interval=interval,
            bar_class="shortfall",
            expected=int(detail["expected_minutes"]),
            recorded=int(detail["recorded_minutes"]),
            shortfall=int(detail["shortfall_minutes"]),
            first_observed="" if not first else _iso(UtcTime.parse(first)),
            published_at=_iso(interval.end),
        )
    return days


#: 全分無記録の日・取得不足の日には無い項目（道具の側も空。比べない）。
_ONLY_BUILT = ("last_observed", "time_rule_row", "time_rule_mark")


def _tool_value(row: Mapping[str, str], name: str) -> Any:
    if name == "class":
        return _tool_class(row)
    if name in ("expected", "recorded", "unrecorded", "shortfall"):
        return int(row[f"{name}_minutes"])
    if name in ("first_observed", "last_observed"):
        return row[f"{name}_utc"]
    if name == "published_at":
        return row["published_at_utc"]
    return row[name]


def compare_days(
    pair: str,
    variant: str,
    platform: Mapping[str, _PlatformDay],
    tool_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    """取引日ごとに比べる（基盤の日足の区間の開始と道具の `interval_start_utc` で対応づける）。

    **両方向で比べる**: 基盤の取引日の範囲（最初の日足の開始から最後の日足の終わりまで）に入る道具の
    行はすべて出力し、片方にしか無い取引日は不一致にする（休場の日はどちらの側にも取引日として現れない）。
    """
    out: list[dict[str, Any]] = []
    tool_by_start = {row["interval_start_utc"]: row for row in tool_rows}
    extra: list[str] = []
    if platform:
        lo = min(platform)
        hi = max(_iso(day.interval.end) for day in platform.values())
        extra = [
            start
            for start, row in tool_by_start.items()
            if start not in platform and lo <= start and row["interval_end_utc"] <= hi
        ]
    for start in extra:
        theirs = tool_by_start[start]
        closed = _tool_class(theirs) == "closed"
        record: dict[str, Any] = {
            "pair": pair,
            "variant": variant,
            "trading_day": theirs["trading_day"],
            "interval_start_utc": start,
            "interval_end_utc": theirs["interval_end_utc"],
            "platform_class": "closed" if closed else "",
            "match": closed,
        }
        for name in DAY_FIELDS:
            record[f"tool_{name}"] = _tool_value(theirs, name)
        out.append(record)
    for start in sorted(platform):
        mine = platform[start]
        found = tool_by_start.get(start)
        record = {
            "pair": pair,
            "variant": variant,
            "trading_day": "" if found is None else found["trading_day"],
            "interval_start_utc": start,
            "interval_end_utc": "" if found is None else found["interval_end_utc"],
        }
        compared: list[str] = []
        for name in DAY_FIELDS:
            record[f"platform_{name}"] = mine.value(name)
            record[f"tool_{name}"] = "" if found is None else _tool_value(found, name)
            if mine.bar_class in ("fully_observed", "partial") or name not in _ONLY_BUILT:
                compared.append(name)
        record["match"] = found is not None and all(
            str(record[f"platform_{name}"]) == str(record[f"tool_{name}"]) for name in compared
        )
        out.append(record)
    out.sort(key=lambda row: str(row["interval_start_utc"]))
    return out


def compare_no_record(
    pair: str,
    variant: str,
    platform: Mapping[str, set[str]],
    tool_rows: Sequence[Mapping[str, str]],
    scope: Interval,
) -> list[dict[str, Any]]:
    """全分無記録の足の集合を時間足ごとに比べる（比べた範囲 `scope` の中。両方向）。"""
    lo, hi = _iso(scope.start), _iso(scope.end)
    theirs: dict[str, set[str]] = {}
    for row in tool_rows:
        if lo <= row["interval_start_utc"] and row["interval_end_utc"] <= hi:
            theirs.setdefault(row["timeframe"], set()).add(row["interval_start_utc"])
    out: list[dict[str, Any]] = []
    for timeframe in NO_RECORD_TIMEFRAMES:
        mine = platform.get(timeframe, set())
        other = theirs.get(timeframe, set())
        for start in sorted(mine | other):
            out.append(
                {
                    "pair": pair,
                    "variant": variant,
                    "timeframe": timeframe,
                    "interval_start_utc": start,
                    "on_platform": start in mine,
                    "on_tool": start in other,
                    "match": start in mine and start in other,
                }
            )
    return out


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _next_month(month: tuple[int, int]) -> tuple[int, int]:
    year, number = month
    return (year + 1, 1) if number == 12 else (year, number + 1)


def _groups(months: Sequence[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """連続した月のかたまり（離れた月の間を取得不足として数えないため、かたまりごとに受け入れる）。"""
    groups: list[list[tuple[int, int]]] = []
    for month in sorted(months):
        if groups and _next_month(groups[-1][-1]) == month:
            groups[-1].append(month)
        else:
            groups.append([month])
    return groups


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(".").resolve()
    rules = load_mt5_rules(repo)
    datasource = load_datasource(repo)
    months = [(int(text[:4]), int(text[5:7])) for text in args.months]
    tool_dir = Path(args.feasibility_dir)
    inputs = {
        name: tool_dir / name
        for name in ("daily_bars.csv", "no_record_bars.csv", "warmup_resets.csv")
    }
    daily_rows = _read_csv(inputs["daily_bars.csv"])
    no_record_rows = _read_csv(inputs["no_record_bars.csv"])
    reset_rows_tool = _read_csv(inputs["warmup_resets.csv"])
    marks = {row.row: row.mark.value for row in rules.table.rows}
    window = rules.counting.warmup_daily_bars
    day_out: list[dict[str, Any]] = []
    no_record_out: list[dict[str, Any]] = []
    reset_out: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for pair in args.pairs:
        symbol = Symbol(pair)
        for variant_text in args.variants:
            variant = TimeRuleVariant(variant_text)
            pair_days: list[dict[str, Any]] = []
            pair_no_record: list[dict[str, Any]] = []
            pair_resets: list[dict[str, Any]] = []
            for group in _groups(months):
                pending = accept_mt5_in_memory(
                    rules=rules,
                    datasource=datasource,
                    raw_dir=Path(args.root),
                    symbol=symbol,
                    variant=variant,
                    created_at=UtcTime(datetime.now(UTC)),
                    boundaries=INITIAL_ACCESS_BOUNDARIES,
                    code_version=code_version(),
                    months=group,
                )
                daily = SeriesId(
                    symbol=symbol, timeframe=rules.timeframe_defs[DAILY].ref, basis=PriceBasis.BID
                )
                bars = sorted(
                    (
                        bar
                        for partition_id, items in pending.partition_bars.items()
                        if partition_id.series == daily
                        for bar in items
                    ),
                    key=lambda bar: bar.bar_start.value,
                )
                no_record_daily = {
                    result.interval.start: (
                        int(dict(result.detail)["expected_minutes"]),
                        result.interval,
                    )
                    for result in pending.report.results
                    if result.kind is CheckKind.NO_RECORD_BAR and result.series == daily
                }
                shortfall = {
                    result.interval.start: (dict(result.detail), result.interval)
                    for result in pending.report.results
                    if result.kind is CheckKind.ACQUISITION_SHORTFALL and result.series == daily
                }
                platform = _platform_days(bars, no_record_daily, shortfall, marks)
                pair_days.extend(
                    compare_days(
                        pair,
                        variant.value,
                        platform,
                        [
                            row
                            for row in daily_rows
                            if row["pair"] == pair and row["variant"] == variant.value
                        ],
                    )
                )
                scope = Interval(
                    start=min(day.interval.start for day in platform.values()),
                    end=max(
                        (day.interval.end for day in platform.values()),
                        key=lambda value: value.value,
                    ),
                )
                platform_no_record: dict[str, set[str]] = {}
                for result in pending.report.results:
                    if result.kind is not CheckKind.NO_RECORD_BAR or result.series.symbol != symbol:
                        continue
                    platform_no_record.setdefault(result.series.timeframe.id, set()).add(
                        _iso(result.interval.start)
                    )
                pair_no_record.extend(
                    compare_no_record(
                        pair,
                        variant.value,
                        platform_no_record,
                        [
                            row
                            for row in no_record_rows
                            if row["pair"] == pair and row["variant"] == variant.value
                        ],
                        scope,
                    )
                )
                pair_resets.extend(
                    _compare_resets(
                        pair,
                        variant.value,
                        rules,
                        daily,
                        bars,
                        frozenset(no_record_daily),
                        scope,
                        window,
                        [
                            row
                            for row in reset_rows_tool
                            if row["pair"] == pair and row["variant"] == variant.value
                        ],
                    )
                )
            day_out.extend(pair_days)
            no_record_out.extend(pair_no_record)
            reset_out.extend(pair_resets)
            counts: dict[str, int] = {}
            for row in pair_days:
                key = str(row.get("platform_class") or "")
                counts[key] = counts.get(key, 0) + 1
            summary[f"{pair}/{variant.value}"] = {
                "trading_days": len(pair_days),
                "matched": sum(1 for row in pair_days if row["match"]),
                "mismatched": [row["interval_start_utc"] for row in pair_days if not row["match"]],
                "classes": counts,
                "no_record_bars": {
                    "compared": len(pair_no_record),
                    "matched": sum(1 for row in pair_no_record if row["match"]),
                },
                "history_resets": {
                    "compared": len(pair_resets),
                    "matched": sum(1 for row in pair_resets if row["match"]),
                },
            }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "daily_crosscheck.csv", DAY_COLUMNS, day_out)
    _write_csv(out_dir / "no_record_crosscheck.csv", NO_RECORD_COLUMNS, no_record_out)
    _write_csv(out_dir / "history_resets.csv", RESET_COLUMNS, reset_out)
    record = {
        "tool": "tools/survey/mt5_platform_crosscheck.py",
        "target_commit": _git_head(),
        # 手元の絶対パスは書かない（置き場はリポジトリの外にあるため）。
        "command": (
            "uv run python -m tools.survey.mt5_platform_crosscheck"
            f" --root <原データの置き場>/{Path(args.root).name}"
            f" --feasibility-dir {args.feasibility_dir}"
            f" --pairs {' '.join(args.pairs)} --months {' '.join(args.months)}"
            f" --variants {' '.join(args.variants)}"
            f" --out-dir {args.out_dir}"
        ),
        "raw_root": Path(args.root).name,
        "pairs": list(args.pairs),
        "variants": list(args.variants),
        "months_api_time": list(args.months),
        "feasibility_ref": args.feasibility_ref,
        "feasibility_sha256": {name: _sha(path) for name, path in inputs.items()},
        "rules": {
            "counting_rules_sha256": rules.counting.sha256,
            "time_rule_table_sha256": rules.table.sha256,
            "calendar": f"{rules.calendar.id}@v{rules.calendar.version}",
            "calendar_sha256": rules.counting.calendar().sha256,
            "warmup_daily_bars": window,
        },
        "summary": summary,
        "prices": "出力には件数・時刻だけを書いた（価格は出さない）",
    }
    (out_dir / "run_record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return record


def _compare_resets(
    pair: str,
    variant: str,
    rules: Any,
    daily: SeriesId,
    bars: Sequence[Bar],
    no_record: frozenset[UtcTime],
    scope: Interval,
    window: int,
    tool_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    """準備のやり直し（日足 `window` 本）を比べる。基盤は表22 と同じ `read_resets`（D12-127）。

    道具の `not_ready_from_utc` と基盤の準備不足になる時刻を比べる。準備が再び整う時刻は、
    基盤が比べた
    範囲の中で整うときだけ比べ、範囲の外なら「範囲の外」（`ready_compared` が偽）と記録する。
    """
    if not bars or not no_record:
        return []
    schedule = SeriesSchedule(
        series=daily, timeframe_def=rules.timeframe_defs[DAILY], calendar=rules.calendar
    )
    spec = ReadWindowSpec(kind=ReadKind.BARS, bars=window)
    mine = read_resets(schedule, spec, bars, no_record, scope)
    tool_by_day = {row["not_ready_from_utc"]: row for row in tool_rows}
    out: list[dict[str, Any]] = []
    for reset in mine:
        start = _iso(reset.insufficient_from)
        theirs = tool_by_day.get(start)
        ready = "" if reset.ready_at is None else _iso(reset.ready_at)
        tool_ready = "" if theirs is None else theirs["ready_again_at_utc"]
        compared = reset.ready_at is not None or (
            bool(tool_ready) and tool_ready <= _iso(scope.end)
        )
        out.append(
            {
                "pair": pair,
                "variant": variant,
                "no_record_day_start_utc": _iso(reset.no_record_bar_start),
                "window_kind": spec.kind.value,
                "window_bars": window,
                "platform_insufficient_from": start,
                "tool_not_ready_from": "" if theirs is None else theirs["not_ready_from_utc"],
                "platform_ready_at": ready or "範囲の外",
                "tool_ready_again_at": tool_ready,
                "ready_compared": compared,
                "match": theirs is not None and (not compared or ready == tool_ready),
            }
        )
    scope_lo, scope_hi = _iso(scope.start), _iso(scope.end)
    seen = {row["platform_insufficient_from"] for row in out}
    for row in tool_rows:
        start = row["not_ready_from_utc"]
        if scope_lo <= start <= scope_hi and start not in seen:
            out.append(
                {
                    "pair": pair,
                    "variant": variant,
                    "window_kind": spec.kind.value,
                    "window_bars": window,
                    "tool_not_ready_from": start,
                    "tool_ready_again_at": row["ready_again_at_utc"],
                    "match": False,
                }
            )
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", required=True)
    parser.add_argument(
        "--feasibility-dir",
        required=True,
        help="PR #85 の最終集計の出力（main の docs/data/full_period_feasibility）",
    )
    parser.add_argument("--pairs", nargs="+", required=True)
    parser.add_argument("--months", nargs="+", required=True)
    parser.add_argument("--variants", nargs="+", default=["standard", "candidate"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--feasibility-ref",
        default="",
        help="道具の出力の出どころ（ブランチとコミット。再現の記録に書くだけ）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    record = run(args)
    mismatched = sum(len(item["mismatched"]) for item in record["summary"].values())
    mismatched += sum(
        item[key]["compared"] - item[key]["matched"]
        for item in record["summary"].values()
        for key in ("no_record_bars", "history_resets")
    )
    print(json.dumps(record["summary"], ensure_ascii=False, indent=1))
    return 0 if mismatched == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
