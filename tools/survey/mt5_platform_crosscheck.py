"""基盤の受入れ（MT5 経路）と全期間の集計の道具（PR #85）の取引日ごとの数え方の突き合わせ（D12
§11.13 の 15）。

2026-10-17 の指示 3-C「実データでは、集計結果と基盤の足生成・
能力検査の結果を突き合わせてください」の
実データの側。取得済みの原データ（`data/raw/oanda_mt5/<取得日>/`）の指定した通貨・月を、**本番の受入れ**
（`odyssey_fx.app.mt5.accept_mt5_in_memory`。時刻の規則の表・`fx_ny17_oanda` 版 1・
数え方の規則を本番の
入口で読む）でメモリの上の暫定 snapshot にし、日足（`1d_ny17`）の区分と件数を、
全期間の集計の道具の出力
（`docs/data/full_period_feasibility/daily_coverage.csv`）の同じ取引日の行と比べる。

比べる項目（価格は読まない・出さない。件数・時刻だけ）:

- 存在すべき分（被覆率の分母）・記録ありの分・無記録の分・取得不足の分
- 最初の観測の時刻（道具の `first_recorded_utc`、基盤の日足の `first_observed_at`）
- 取引日の区分（基盤: 作った日足（全分観測／部分観測）・全分無記録の日足・取得不足の日足。道具:
件数から
  同じ規則で読む）

あわせて、基盤の準備のやり直し（`marketdata.domain.readiness.history_resets`。窓の本数は数え方の規則の
`warmup.daily_bars`）の箇所と、能力検査の取得不足の照合の結果（区間の数）を出す。道具の側の準備の数え方は
PR #85 で未実装（`warmup_resets` が `PendingDesignError`）なので、基盤の出力だけを記録し、PR
#85 の最終集計の
後に同じ CSV で比べられる形にする。

比べる月は、時刻の規則の表の変換が欧州式と米国式で同じ期間（両方冬・両方夏。行 8・9）に限る。道具の
`daily_coverage.csv` は時計ごと（`eu_clock`・`us_clock`）の全期間の値で、
その期間なら標準仮説の変換と同じ
だからである（候補の間で変換が違う期間は、道具の最終集計が表から行を選んだ後に比べる）。

実行（リポジトリの根で）::

    uv run python -m tools.survey.mt5_platform_crosscheck \\
        --root <原データの置き場>/data/raw/oanda_mt5/2026-10-16 \\
        --feasibility-csv <PR #85 の docs/data/full_period_feasibility/daily_coverage.csv> \\
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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from odyssey_fx.app.composition import code_version
from odyssey_fx.app.mt5 import accept_mt5_in_memory, load_datasource, load_mt5_rules
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.readiness import ScheduledBar, history_resets
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.time_rule import TimeRuleMark, TimeRuleVariant

__all__ = ["DAY_COLUMNS", "compare_days", "main"]

DAILY = "1d_ny17"
#: 比べる時計（両方冬・両方夏の期間なので、どちらでも同じ値。行 8・9）。
CLOCK = "eu_clock"

DAY_COLUMNS: tuple[str, ...] = (
    "pair",
    "trading_day",
    "interval_start_utc",
    "interval_end_utc",
    "platform_class",
    "tool_class",
    "platform_expected",
    "tool_expected",
    "platform_recorded",
    "tool_recorded",
    "platform_unrecorded",
    "tool_unrecorded",
    "platform_shortfall",
    "tool_shortfall",
    "platform_first_observed",
    "tool_first_observed",
    "match",
)

RESET_COLUMNS: tuple[str, ...] = (
    "pair",
    "no_record_day_start_utc",
    "window_bars",
    "ready_day_start_utc",
    "ready_at_utc",
)


def _iso(moment: UtcTime) -> str:
    return moment.value.strftime("%Y-%m-%dT%H:%MZ")


def _tool_class(row: Mapping[str, str]) -> str:
    """道具の件数から取引日の区分を読む（基盤の作る条件と同じ規則。D03 §15.4）。"""
    expected = int(row["expected_minutes"])
    if expected == 0:
        return "closed"
    if int(row["shortfall_minutes"]) > 0:
        return "shortfall"
    recorded = int(row["recorded_minutes"])
    if recorded == 0:
        return "no_record"
    return "fully_observed" if recorded == expected else "partial"


@dataclass(frozen=True, slots=True)
class _PlatformDay:
    interval: Interval
    bar_class: str
    expected: int
    recorded: int
    shortfall: int
    first_observed: str


def _platform_days(
    bars: Sequence[Bar],
    no_record: Mapping[UtcTime, tuple[int, Interval]],
    shortfall: Mapping[UtcTime, tuple[Mapping[str, str], Interval]],
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
        )
    for start, (expected, interval) in no_record.items():
        days[_iso(start)] = _PlatformDay(
            interval=interval,
            bar_class="no_record",
            expected=expected,
            recorded=0,
            shortfall=0,
            first_observed="",
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
        )
    return days


def compare_days(
    pair: str, platform: Mapping[str, _PlatformDay], tool_rows: Sequence[Mapping[str, str]]
) -> list[dict[str, Any]]:
    """取引日ごとに比べる（基盤の日足の区間の開始と道具の `interval_start_utc` で対応づける）。

    **両方向で比べる**: 基盤の取引日の範囲（最初の日足の開始から最後の日足の終わりまで）
    に入る道具の行は
    すべて出力し、片方にしか無い取引日は不一致にする（休場の日はどちらの側にも取引日として現れない）。
    """
    out: list[dict[str, Any]] = []
    tool_by_start: dict[str, Mapping[str, str]] = {
        row["interval_start_utc"]: row for row in tool_rows
    }
    if platform:
        lo = min(platform)
        hi = max(_iso(day.interval.end) for day in platform.values())
        extra = [
            start
            for start, row in tool_by_start.items()
            if start not in platform and lo <= start and row["interval_end_utc"] <= hi
        ]
    else:
        extra = []
    for start in extra:
        theirs = tool_by_start[start]
        tool_class = _tool_class(theirs)
        # 道具の行で存在すべき分が 0（休場の定義で全分が外れた日。道具はカレンダー 版 2
        # の日足の区間を
        # 使うので行が残る）は、基盤に日足が無いこと（`fx_ny17_oanda` 版 1 で休場）と同じ意味。
        closed = tool_class == "closed"
        out.append(
            {
                "pair": pair,
                "trading_day": theirs["trading_day"],
                "interval_start_utc": start,
                "interval_end_utc": theirs["interval_end_utc"],
                "platform_class": "closed" if closed else "",
                "tool_class": tool_class,
                "tool_expected": int(theirs["expected_minutes"]),
                "tool_recorded": int(theirs["recorded_minutes"]),
                "tool_unrecorded": int(theirs["no_record_minutes"]),
                "tool_shortfall": int(theirs["shortfall_minutes"]),
                "tool_first_observed": theirs["first_recorded_utc"],
                "match": closed,
            }
        )
    for start in sorted(platform):
        mine = platform[start]
        found = tool_by_start.get(start)
        record: dict[str, Any] = {
            "pair": pair,
            "trading_day": "" if found is None else found["trading_day"],
            "interval_start_utc": start,
            "interval_end_utc": "" if found is None else found["interval_end_utc"],
            "platform_class": mine.bar_class,
            "tool_class": "" if found is None else _tool_class(found),
            "platform_expected": mine.expected,
            "tool_expected": "" if found is None else int(found["expected_minutes"]),
            "platform_recorded": mine.recorded,
            "tool_recorded": "" if found is None else int(found["recorded_minutes"]),
            "platform_unrecorded": mine.expected - mine.recorded - mine.shortfall,
            "tool_unrecorded": "" if found is None else int(found["no_record_minutes"]),
            "platform_shortfall": mine.shortfall,
            "tool_shortfall": "" if found is None else int(found["shortfall_minutes"]),
            "platform_first_observed": mine.first_observed,
            "tool_first_observed": "" if found is None else found["first_recorded_utc"],
        }
        record["match"] = found is not None and all(
            record[f"platform_{name}"] == record[f"tool_{name}"]
            for name in (
                "class",
                "expected",
                "recorded",
                "unrecorded",
                "shortfall",
                "first_observed",
            )
        )
        out.append(record)
    out.sort(key=lambda row: str(row["interval_start_utc"]))
    return out


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(".").resolve()
    rules = load_mt5_rules(repo)
    datasource = load_datasource(repo)
    months = [(int(text[:4]), int(text[5:7])) for text in args.months]
    tool_rows: list[dict[str, str]] = []
    with Path(args.feasibility_csv).open(encoding="utf-8") as handle:
        tool_rows = [row for row in csv.DictReader(handle) if row["conversion"] == CLOCK]
    day_rows: list[dict[str, Any]] = []
    reset_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    window = rules.counting.warmup_daily_bars
    for pair in args.pairs:
        symbol = Symbol(pair)
        # 連続した月のかたまりごとに受け入れる（離れた月の間を取得不足として数えないため）。
        groups: list[list[tuple[int, int]]] = []
        for month in sorted(months):
            if groups and _next_month(groups[-1][-1]) == month:
                groups[-1].append(month)
            else:
                groups.append([month])
        pair_days: list[dict[str, Any]] = []
        for group in groups:
            for year, number in group:
                _require_same_conversion(rules, pair, year, number)
            pending = accept_mt5_in_memory(
                rules=rules,
                datasource=datasource,
                raw_dir=Path(args.root),
                symbol=symbol,
                variant=TimeRuleVariant.STANDARD,
                created_at=UtcTime(datetime.now(UTC)),
                boundaries=INITIAL_ACCESS_BOUNDARIES,
                code_version=code_version(),
                months=group,
            )
            daily = SeriesId(
                symbol=symbol, timeframe=rules.timeframe_defs[DAILY].ref, basis=rules_basis()
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
            no_record = {
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
            platform = _platform_days(bars, no_record, shortfall)
            rows = compare_days(pair, platform, [row for row in tool_rows if row["pair"] == pair])
            pair_days.extend(rows)
            sequence = [
                ScheduledBar(
                    bar_start=UtcTime.parse(start.replace("Z", ":00Z")),
                    available_at=UtcTime.parse(start.replace("Z", ":00Z")),
                    built=platform[start].bar_class in ("fully_observed", "partial"),
                    no_record=platform[start].bar_class == "no_record",
                )
                for start in sorted(platform)
            ]
            ends = {_iso(bar.bar_start): bar.bar_end for bar in bars}
            for reset in history_resets(sequence, window):
                ready_start = None if reset.ready_bar_start is None else _iso(reset.ready_bar_start)
                reset_rows.append(
                    {
                        "pair": pair,
                        "no_record_day_start_utc": _iso(reset.no_record_bar_start),
                        "window_bars": window,
                        "ready_day_start_utc": ready_start or "",
                        "ready_at_utc": "" if ready_start is None else _iso(ends[ready_start]),
                    }
                )
        day_rows.extend(pair_days)
        counts: dict[str, int] = {}
        for row in pair_days:
            counts[row["platform_class"]] = counts.get(row["platform_class"], 0) + 1
        summary[pair] = {
            "trading_days": len(pair_days),
            "matched": sum(1 for row in pair_days if row["match"]),
            "mismatched": [row["interval_start_utc"] for row in pair_days if not row["match"]],
            "classes": counts,
            "history_resets": sum(1 for row in reset_rows if row["pair"] == pair),
        }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "daily_crosscheck.csv", DAY_COLUMNS, day_rows)
    _write_csv(out_dir / "history_resets.csv", RESET_COLUMNS, reset_rows)
    record = {
        "tool": "tools/survey/mt5_platform_crosscheck.py",
        "target_commit": _git_head(),
        # 手元の絶対パスは書かない（置き場はリポジトリの外にあるため）。
        "command": (
            "uv run python -m tools.survey.mt5_platform_crosscheck"
            f" --root <原データの置き場>/{Path(args.root).name}"
            " --feasibility-csv <道具の daily_coverage.csv>"
            f" --pairs {' '.join(args.pairs)} --months {' '.join(args.months)}"
            f" --out-dir {args.out_dir}"
        ),
        "raw_root": Path(args.root).name,
        "pairs": list(args.pairs),
        "months_api_time": list(args.months),
        "clock_compared": CLOCK,
        "feasibility_csv_sha256": _sha(Path(args.feasibility_csv)),
        "feasibility_csv_ref": args.feasibility_ref,
        "rules": {
            "counting_rules_sha256": rules.counting.sha256,
            "time_rule_table_sha256": rules.table.sha256,
            "calendar": f"{rules.calendar.id}@v{rules.calendar.version}",
            "calendar_sha256": rules.counting.calendar().sha256,
            "warmup_daily_bars": window,
        },
        "summary": summary,
        "prices": "読んでいない値は無いが、出力には件数・時刻だけを書いた（価格は出さない）",
    }
    (out_dir / "run_record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return record


def rules_basis() -> Any:
    from odyssey_fx.marketdata.domain.series import PriceBasis

    return PriceBasis.BID


def _next_month(month: tuple[int, int]) -> tuple[int, int]:
    year, number = month
    return (year + 1, 1) if number == 12 else (year, number + 1)


def _require_same_conversion(rules: Any, pair: str, year: int, month: int) -> None:
    """比べる月が、変換が候補の間で同じ期間（行の印が仮定付き（同じ）か確認済み）だけにあること。"""
    day = datetime(year, month, 1, tzinfo=UTC).date()
    while day.month == month:
        row = rules.table.row_for(pair, day)
        if row is None or row.mark is TimeRuleMark.ASSUMED_DIFFERS:
            raise SystemExit(
                f"{pair} {year}-{month:02d} contains {day}, where the candidates differ;"
                " compare it after the tool picks the table row"
            )
        day += timedelta(days=1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", required=True)
    parser.add_argument("--feasibility-csv", required=True)
    parser.add_argument("--pairs", nargs="+", required=True)
    parser.add_argument("--months", nargs="+", required=True)
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
    print(json.dumps(record["summary"], ensure_ascii=False, indent=1))
    return 0 if mismatched == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
