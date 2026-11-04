"""順 7 の機構確認 M1 のやり直し: 取引の通貨ペアに記録の無い分を除いたことで換算できた件数を数える。

D06 v1.22 §19.5 の規則 9（人間の決定 (31)）は、換算の古さを、休場の時間に加えて取引の通貨ペア
（今の run では執行系列の通貨ペア）の 1 分足に記録の無い分も除いて測る。D06 の仮置き C4-4
により、この除き方で換算できた件数は記録票に欄を足さず、M1 のやり直しの記録に価格を読まない
道具で数えて書く。
本道具はそのための記録用の一回の道具（`count_conversion_records.py` と同じ置き方）。

run の根拠記録（表15 の `at_time` と `conversion_paths` の観測時刻 `observed_at`）の換算の
経路ごとに、2 つの古さを数える。

- 今までの測り方（決定 (25) (a)）: 観測時刻から処理点までのうち、取引カレンダーのセッションの
  中の時間。
- 新しい測り方（決定 (31) (a)）: そのうち、取引の通貨ペアの 1 分足に記録のある分だけ
  （1 分ごとの区間 `[m, m + 1 分)` がセッションの中にあり、その分の 1 分足が snapshot にあるもの）。

数え方は `odyssey_fx.backtest.portfolio.conversion` の `session_staleness` と同じ（カレンダーの
`sessions` で区切り、区切りの中に収まる記録のある 1 分足だけを数える）。

**使うのは時刻だけ**: 根拠記録は `kind`・`at_time`・`conversion_paths` の列を読み、経路の JSON
（率を含む）を解いて `observed_at` と脚の有無だけを使う（率の値は使わず、出さない）。snapshot は
取引の通貨ペアの 1 分足の研究期間の partition（`RESEARCH_HISTORY`）の `bar_start` だけを読む。
価格・率・数量・金額は出さない。出すのは件数と分の数だけ。

使い方（リポジトリの根で）::

    uv run python -I docs/data/step7_mechanism_check/count_staleness_exclusion.py \\
        --snapshots <snapshot の基点> --calendar configs/calendars/fx_ny17_oanda_v1.yaml \\
        --limit-minutes 15 <run の成果物のディレクトリ> ...
"""

from __future__ import annotations

import argparse
import bisect
import json
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from odyssey_fx.app.config.calendars import load_calendar
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar

_MINUTE = timedelta(minutes=1)
#: 読む partition（研究期間だけ。封印期間の partition は開かない）。
_PARTITION = "RESEARCH_HISTORY"


def _recorded_minutes(snapshots: Path, snapshot_id: str, pair: str) -> list[datetime]:
    """取引の通貨ペアの 1 分足の記録のある分の始まり（時刻順）。`bar_start` の列だけを読む。"""
    path = snapshots / snapshot_id / f"{pair}_1m_bid" / _PARTITION / "bars.parquet"
    starts = pl.read_parquet(path, columns=["bar_start"])["bar_start"].to_list()
    return sorted(UtcTime.parse(text).value for text in starts)


def _staleness(
    observed: datetime,
    at: datetime,
    calendar: TradingCalendar,
    recorded: Sequence[datetime] | None,
) -> timedelta:
    """`observed` から `at` までの古さ。

    `recorded` が無ければセッションの時間、あれば記録のある分。
    """
    if not observed < at:
        return timedelta(0)
    total = timedelta(0)
    window = Interval(start=UtcTime(observed), end=UtcTime(at))
    for session in calendar.sessions(window):
        start, end = session.start.value, session.end.value
        if recorded is None:
            total += end - start
            continue
        low = bisect.bisect_left(recorded, start)
        high = bisect.bisect_right(recorded, end - _MINUTE)  # 区切りの中に終わる分だけ
        total += _MINUTE * (high - low)
    return total


def _minutes(value: timedelta) -> int:
    return int(value / _MINUTE)


def count(
    run_dir: Path, snapshots: Path, calendar: TradingCalendar, limit: timedelta
) -> dict[str, Any]:
    raw = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["config"]
    config = json.loads(raw) if isinstance(raw, str) else raw
    pair = str(config["execution_series"]).split("/", 1)[0]
    snapshot_id = str(config["snapshot_ref"]["snapshot_id"])
    recorded = _recorded_minutes(snapshots, snapshot_id, pair)
    evidence = pl.read_parquet(
        run_dir / "EVIDENCE.parquet", columns=["kind", "at_time", "conversion_paths"]
    )
    paths = old_over = new_over = rescued = 0
    rescued_by_kind: Counter[str] = Counter()
    old_max = new_max = timedelta(0)
    for kind, at_time, items in evidence.iter_rows():
        for item in items or ():
            path = json.loads(item)
            if not path.get("legs"):
                continue  # 恒等換算（古さ 0）
            paths += 1
            at = UtcTime.parse(at_time).value
            observed = UtcTime.parse(path["observed_at"]).value
            old = _staleness(observed, at, calendar, None)
            new = _staleness(observed, at, calendar, recorded)
            old_max, new_max = max(old_max, old), max(new_max, new)
            old_over += old > limit
            new_over += new > limit
            if old > limit >= new:
                rescued += 1
                rescued_by_kind[str(kind)] += 1
    return {
        "pair": pair,
        "recorded_minutes_in_snapshot": len(recorded),
        "conversion_paths_with_legs": paths,
        "over_limit_by_session_staleness": old_over,
        "over_limit_by_recorded_staleness": new_over,
        "converted_by_the_exclusion": rescued,
        "converted_by_the_exclusion_by_kind": dict(sorted(rescued_by_kind.items())),
        "max_session_staleness_minutes": _minutes(old_max),
        "max_recorded_staleness_minutes": _minutes(new_max),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--snapshots", required=True, help="snapshot の基点")
    parser.add_argument("--calendar", required=True, help="run の取引カレンダー（版 1）")
    parser.add_argument("--limit-minutes", type=int, required=True, help="古さの上限（分）")
    parser.add_argument("runs", nargs="+", help="run の成果物のディレクトリ")
    args = parser.parse_args(argv)
    calendar = load_calendar(Path(args.calendar))
    limit = timedelta(minutes=args.limit_minutes)
    out = {
        Path(run).name: count(Path(run), Path(args.snapshots), calendar, limit) for run in args.runs
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
