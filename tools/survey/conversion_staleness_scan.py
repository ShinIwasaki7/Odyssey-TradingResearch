"""研究ポリシー第 4 版の事前確認: 換算の古さとずれの集計（D18 §4.2、決定 37 の作業）。

換算が要る通貨（GBPUSD・AUDUSD・USDCHF。道具の照合用に EURUSD）× 承認済みの 2 組について、
建玉を持っていれば換算の上限（30 分。D06 v1.23 §19.5）で run が止まりうる時点が研究期間に
あるかを、**戦略を走らせず、価格を読まずに**数える。

**数え方は本番の実装を呼ぶ**（D18 の仮置き 9。計算を道具の側で別に書かない）:

- 脚の経路: `backtest.portfolio.conversion.resolve_route`（D06 §19.3）。
- 脚の観測（終わりが処理点以下の最後の記録のある 1 分足）: `last_minute_index`（換算の脚の供給
  `app.conversion` と同じ関数。D06 §19.4）。
- 古さ（規則 9）とずれ（規則 6）: `observation_staleness`・`legs_skew`（エンジンの
  `session_staleness`・`session_skew` が呼ぶ時刻だけの関数。休場と、取引の通貨ペアの 1 分足に
  記録の無い分を除く。D06 v1.22・v1.23 §19.5）。上限ちょうどは許す（`exceeds_limit`）。
- 処理点の集合 (1)（エンジンと同じ判断時点）: 公開イベントの時刻（`marketdata.application.
  publication` の `scheduled_boundary_intervals`・`bar_event_times`。公開フィードと同じ関数）、
  執行足の途中の再開の分（`mt5_observation.recorded_resumptions` とエンジンの
  `mid_bar_resumption_minutes`）、判断時点の列挙（エンジンの `decision_times`）。建玉の有無に
  よらず数える（建玉の無いときに再開の分だけの判断時点を飛ばす規則は当てない。上限側の数）。
- 処理点の集合 (2)（参考の上限側の集合）: 取引カレンダーの営業時間の中のすべての分の境目
  （セッションの始まりと終わりを含む）と run の終わり。(1) を見落としなく覆う（道具が確かめる）。

**読むのは時刻の列だけ**: 承認済みの snapshot の研究履歴の partition（`RESEARCH_HISTORY`）から、
執行の snapshot の 15 分足・1 時間足・4 時間足・日足の `bar_start`・`bar_end`・
`first_observed_at`、取引の通貨ペアの 1 分足の `bar_start`、換算の snapshot の USDJPY の 1 分足の
`bar_start` だけを読む（`ParquetSnapshotStore.read_partition_times`。価格・出来高・スプレッドの
列は開かない）。封印・隔離の partition と、終わりが 2024-01-01T00:00Z より後の partition は、
ファイルを開く前に止める（`ScanRefused`。RP4-17）。出すのは件数・時刻・長さ（分）・ハッシュ・
処理時間だけで、価格・率は出さない。

使い方（リポジトリの根で）::

    uv run --frozen python -m tools.survey.conversion_staleness_scan \\
        --snapshots <本体>/data/snapshots \\
        --out docs/data/step7_conversion_staleness_scan/scan.json
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import subprocess
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.app.conversion import require_conversion_pairing
from odyssey_fx.backtest.domain.policies import ConversionPolicy
from odyssey_fx.backtest.engine.loop import decision_times, mid_bar_resumption_minutes
from odyssey_fx.backtest.portfolio.conversion import (
    RouteHop,
    exceeds_limit,
    last_minute_index,
    legs_skew,
    observation_staleness,
    resolve_route,
)
from odyssey_fx.common.money import CurrencyCode
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.mt5_observation import Mt5Observation, recorded_resumptions
from odyssey_fx.marketdata.application.publication import (
    bar_event_times,
    scheduled_boundary_intervals,
)
from odyssey_fx.marketdata.domain.access import SEALED_RESEARCH_UNTIL, AccessClass
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId, PartitionRecord, SnapshotManifest
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = [
    "PairTimes",
    "ScanRefused",
    "SeriesTimes",
    "decision_points",
    "main",
    "reference_points",
    "require_research_partition",
    "scan_points",
]

_MINUTE: Final = timedelta(minutes=1)
#: 換算の上限（決定 36。D06 v1.23 §19.5）と、参考に数える 15 分（M1 の 3 回目の仮置き 6）。
LIMIT: Final = timedelta(minutes=30)
REFERENCE_LIMIT: Final = timedelta(minutes=15)
#: 口座の通貨と経由の通貨（第 4 版の比較の前提。決定 36）。
ACCOUNT: Final = CurrencyCode("JPY")
POLICY: Final = ConversionPolicy(pivot_currency=CurrencyCode("USD"), max_observation_skew=LIMIT)
USDJPY: Final = Symbol("USDJPY")
#: 読める分類（研究履歴だけ）と、読める期間の終わり（2024 年以降を開かない。ADR-0038 第1項）。
_READABLE: Final = AccessClass.RESEARCH_HISTORY
_RESEARCH_UNTIL: Final = SEALED_RESEARCH_UNTIL
_MINUTE_TIMEFRAME: Final = "1m"

#: 第 4 版の basis（`configs/policies/research/research_policy_v4.yaml`）の snapshot の組。
#: 道具の照合用の EURUSD を先に置く。
PAIRS: Final[Mapping[str, Mapping[str, str]]] = {
    "EURUSD": {
        "standard": "4e6eb8d40df023685b6b0739bd202d132c97997de68e8d55f0b4114c89bb917b",
        "candidate": "9c03bc1283e5c7474dc1602104d943fe7985b11c745dae3135813fc7ddee1b19",
    },
    "GBPUSD": {
        "standard": "b9ca3a2142bf7c498c5b0b1fb803c0c44df6938dad76a11bd6203e9667437f6e",
        "candidate": "35bcd732a0d696eb95766094e35e4aeab0cdb779e3abae1326691155c7f7732e",
    },
    "AUDUSD": {
        "standard": "07362e2223e99b24030b61433c0cc6a78c1a34cbbe9708e1cc04adb744bfe75d",
        "candidate": "b9d213ea6fd957a5d317b33c3472ef3c72a0292d0fed77a4b0003cbf65b074b1",
    },
    "USDCHF": {
        "standard": "1139abd01c3d1f1d4c564e5f123e4a979b42b8ed8ae1fc8b15b0d27752472298",
        "candidate": "3d5e44b84807ddf1e46251e9b430a6bcf0c62323062ceb07dfda1df83fcc6c62",
    },
}
CONVERSION: Final[Mapping[str, str]] = {
    "standard": "ecaaf53fcdedbc1977184b390c36e2714c658829341eed3ec5d71588d3bfc622",
    "candidate": "f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca",
}
CALENDAR_FILE: Final = Path("configs/calendars/fx_ny17_oanda_v1.yaml")
TIMEFRAMES_FILE: Final = Path("configs/calendars/timeframes_v2.yaml")

#: 道具の照合の基準（D18 §4.2）: EURUSD の機構確認の記録 `docs/data/step7_mechanism_check/README.md`
#: 第 8.7 節の「新しい測り方で止まりうる時間帯の目安」（両方の組とも 3 区間、17〜19 分）と、第 9 節
#: の「上限 30 分では 0」。
CROSSCHECK_DATES: Final = ("2020-10-25", "2021-07-18", "2021-09-26")
CROSSCHECK_MINUTES: Final = (17, 19)


class ScanRefused(Exception):
    """読んではいけない partition を読もうとした（読み取りを始めずに止める。RP4-17）。"""


# --- 入力（時刻だけ） ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeriesTimes:
    """作られた足の時刻（開始の昇順。`first_observed_at` は MT5 経路の最初の観測の分）。"""

    starts: tuple[UtcTime, ...]
    ends: tuple[UtcTime, ...]
    first_observed: tuple[UtcTime, ...]

    def containing(self, minute: UtcTime) -> int:
        """`minute` を含む足の位置（無ければ −1）。"""
        index = bisect.bisect_right(self.starts, minute) - 1
        if index >= 0 and minute < self.ends[index]:
            return index
        return -1


@dataclass(frozen=True, slots=True)
class PairTimes:
    """1 通貨 × 1 組の集計の入力（時刻だけ）。

    `schedules` は執行の snapshot の 1 分足以外の系列の公開予定、`bars` はその系列の作られた足の
    時刻、`traded_minutes` は取引の通貨ペアの 1 分足の記録のある分の開始（昇順）、`leg_minutes`
    は経路を探す集合（執行の snapshot と換算の snapshot の通貨ペア）の通貨ペアごとの 1 分足の
    記録のある分の開始（昇順）。
    """

    pair: Symbol
    run_interval: Interval
    calendar: TradingCalendar
    execution_series: SeriesId
    minute_series: SeriesId
    schedules: Mapping[SeriesId, SeriesSchedule]
    bars: Mapping[SeriesId, SeriesTimes]
    observation: Mt5Observation
    traded_minutes: tuple[UtcTime, ...]
    leg_minutes: Mapping[Symbol, tuple[UtcTime, ...]]
    policy: ConversionPolicy = POLICY

    def route(self) -> tuple[RouteHop, ...] | None:
        """決済通貨から口座通貨への経路（本番の `resolve_route`）。"""
        return resolve_route(self.pair.quote, ACCOUNT, frozenset(self.leg_minutes), self.policy)


@dataclass(frozen=True, slots=True)
class _TradedSpans:
    """取引の通貨ペアの記録のある 1 分足の時刻（`RecordedMinuteSpans` の実装。時刻だけ）。"""

    starts: tuple[UtcTime, ...]

    def minute_spans_in(self, interval: Interval) -> Iterator[tuple[UtcTime, UtcTime]]:
        lo = bisect.bisect_left(self.starts, interval.start)
        hi = bisect.bisect_left(self.starts, interval.end)
        for index in range(lo, hi):
            start = self.starts[index]
            yield start, start + _MINUTE


# --- 処理点の集合 -------------------------------------------------------------


def decision_points(times: PairTimes) -> tuple[UtcTime, ...]:
    """処理点の集合 (1): エンジンと同じ判断時点（時刻順。建玉の有無によらない）。"""
    run = times.run_interval
    moments: list[UtcTime] = []
    for series, schedule in times.schedules.items():
        moments.extend(item.end for item in scheduled_boundary_intervals(schedule, run))
        bars = times.bars.get(series)
        if bars is None:
            continue
        is_execution = series == times.execution_series
        for bar_end, first in zip(bars.ends, bars.first_observed, strict=True):
            for _kind, moment in bar_event_times(
                bar_end=bar_end,
                first_observed_at=first,
                available_at=schedule.scheduled_at(bar_end),
                run_interval=run,
                is_execution=is_execution,
            ):
                moments.append(moment)
    execution = times.bars[times.execution_series]

    def first_observed_of(minute: UtcTime) -> UtcTime | None:
        index = execution.containing(minute)
        return None if index < 0 else execution.first_observed[index]

    recorded = frozenset(times.traded_minutes)
    resumptions = recorded_resumptions(times.observation, times.minute_series, run, recorded)
    moments.extend(
        mid_bar_resumption_minutes((item.minute for item in resumptions), first_observed_of, run)
    )
    return decision_times(moments, run)


def reference_points(calendar: TradingCalendar, run: Interval) -> Iterator[UtcTime]:
    """処理点の集合 (2): 営業時間の中のすべての分の境目（時刻順）と run の終わり。"""
    last: UtcTime | None = None
    for session in calendar.sessions(run):
        moment = session.start
        while moment <= session.end:
            if last is None or last < moment:
                yield moment
                last = moment
            moment = moment + _MINUTE
    if last is None or last < run.end:
        yield run.end


# --- 数える ------------------------------------------------------------------


@dataclass
class _Runs:
    """上限を超えた処理点の続いた区間（処理点の並びで隣り合うものを 1 区間にまとめる）。"""

    limit: timedelta
    points: int = 0
    runs: list[dict[str, Any]] = field(default_factory=list)
    _open: dict[str, Any] | None = None

    def add(self, moment: UtcTime, length: timedelta | None) -> None:
        if length is None or not exceeds_limit(length, self.limit):
            self._open = None
            return
        self.points += 1
        minutes = _minutes(length)
        if self._open is None:
            self._open = {
                "start": str(moment),
                "end": str(moment),
                "points": 0,
                "max_minutes": minutes,
            }
            self.runs.append(self._open)
        self._open["end"] = str(moment)
        self._open["points"] += 1
        self._open["max_minutes"] = max(self._open["max_minutes"], minutes)

    def summary(self) -> dict[str, Any]:
        return {
            "limit_minutes": _minutes(self.limit),
            "points_over": self.points,
            "runs": len(self.runs),
            "max_minutes": max((run["max_minutes"] for run in self.runs), default=None),
            "intervals": [
                {**run, "span_minutes": _span_minutes(run["start"], run["end"])}
                for run in self.runs
            ],
        }


def _minutes(value: timedelta) -> int:
    whole, rest = divmod(value, _MINUTE)
    if rest:
        raise ValueError(f"{value} is not a whole number of minutes")
    return int(whole)


def _span_minutes(start: str, end: str) -> int:
    return _minutes(UtcTime.parse(end) - UtcTime.parse(start))


@dataclass
class _Measure:
    """1 つの量（古さ・ずれ）の集計。"""

    histogram: Counter[int] = field(default_factory=Counter)
    over: _Runs = field(default_factory=lambda: _Runs(LIMIT))
    reference: _Runs = field(default_factory=lambda: _Runs(REFERENCE_LIMIT))

    def add(self, moment: UtcTime, length: timedelta | None) -> None:
        if length is not None:
            self.histogram[_minutes(length)] += 1
        self.over.add(moment, length)
        self.reference.add(moment, length)

    def summary(self) -> dict[str, Any]:
        return {
            "max_minutes": max(self.histogram, default=None),
            "distribution_minutes": {
                str(key): self.histogram[key] for key in sorted(self.histogram)
            },
            "over_30": self.over.summary(),
            "over_15": self.reference.summary(),
        }


def scan_points(times: PairTimes, points: Iterable[UtcTime]) -> dict[str, Any]:
    """処理点ごとに古さ（とずれ）を本番の関数で測り、集計する。"""
    route = times.route()
    if route is None:
        raise ValueError(f"no conversion route for {times.pair} (D06 §19.3)")
    legs = tuple(times.leg_minutes[hop.symbol] for hop in route)
    spans = _TradedSpans(times.traded_minutes)
    staleness = _Measure()
    skew = _Measure()
    count = no_observation = 0
    for moment in points:
        count += 1
        observed: list[UtcTime] = []
        for starts in legs:
            index = last_minute_index(starts, moment)
            if index < 0:
                break
            observed.append(starts[index])
        if len(observed) < len(legs):
            no_observation += 1
            staleness.add(moment, None)
            skew.add(moment, None)
            continue
        oldest = min(observed, key=lambda value: value.value)
        staleness.add(moment, observation_staleness(oldest, moment, times.calendar, spans))
        skew.add(moment, legs_skew(observed, times.calendar, spans) if len(legs) > 1 else None)
    return {
        "points": count,
        "no_observation": no_observation,
        "route": [{"symbol": str(hop.symbol), "inverted": hop.inverted} for hop in route],
        "staleness": staleness.summary(),
        "skew": skew.summary() if len(legs) > 1 else None,
    }


# --- 読み取り（時刻の列だけ。研究履歴だけ） ----------------------------------------


class _TimeReader(Protocol):
    def read_partition_times(
        self, snapshot_dir: str, partition_id: PartitionId, columns: Sequence[str]
    ) -> dict[str, tuple[UtcTime, ...]]: ...


def require_research_partition(record: PartitionRecord) -> None:
    """研究履歴の、終わりが 2024-01-01T00:00Z 以前の partition でなければ止める（RP4-17）。"""
    partition = record.partition_id
    if partition.access_class is not _READABLE:
        raise ScanRefused(
            f"{partition} is {partition.access_class.value}; the scan reads only"
            f" {_READABLE.value} partitions (D18 §4.2)"
        )
    if _RESEARCH_UNTIL < record.interval.end:
        raise ScanRefused(
            f"{partition} ends at {record.interval.end}, after {_RESEARCH_UNTIL};"
            " the scan does not open data from 2024 onwards (D18 §4.2)"
        )


def read_times(
    reader: _TimeReader, snapshot_id: str, record: PartitionRecord, columns: Sequence[str]
) -> dict[str, tuple[UtcTime, ...]]:
    """partition の時刻の列を読む。先に `require_research_partition` で確かめる。"""
    require_research_partition(record)
    return reader.read_partition_times(snapshot_id, record.partition_id, columns)


def _research_record(manifest: SnapshotManifest, series: SeriesId) -> PartitionRecord:
    found = [
        record
        for record in manifest.partitions
        if record.partition_id.series == series and record.partition_id.access_class is _READABLE
    ]
    if len(found) != 1:
        raise ScanRefused(f"{series} has {len(found)} research partitions; expected 1")
    return found[0]


def _minute_series(manifest: SnapshotManifest, symbol: Symbol) -> SeriesId:
    for record in manifest.series:
        series = record.series_id
        if (
            series.symbol == symbol
            and series.timeframe.id == _MINUTE_TIMEFRAME
            and series.basis is PriceBasis.BID
        ):
            return series
    raise ScanRefused(f"the snapshot has no 1m bid series of {symbol}")


@dataclass(frozen=True, slots=True)
class _Loaded:
    times: PairTimes
    inputs: dict[str, Any]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_pair(
    snapshots_root: Path,
    pair: str,
    group: str,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
) -> _Loaded:
    """承認済みの 2 つの snapshot から 1 通貨 × 1 組の入力（時刻だけ）を作る。"""
    store = ParquetSnapshotStore(root=snapshots_root)
    execution_id = PAIRS[pair][group]
    conversion_id = CONVERSION[group]
    execution = store.open_readable(execution_id)
    conversion = store.open_readable(conversion_id)
    require_conversion_pairing(
        execution.manifest,
        conversion.manifest,
        calendar_id=calendar.id,
        calendar_version=calendar.version,
        execution_id=execution_id,
        conversion_id=conversion_id,
    )
    observation = Mt5Observation.from_snapshot(execution)
    if observation is None:
        raise ScanRefused(f"{execution_id} is not an MT5 snapshot")
    symbol = Symbol(pair)
    files: dict[str, str] = {}
    schedules: dict[SeriesId, SeriesSchedule] = {}
    bars: dict[SeriesId, SeriesTimes] = {}
    execution_series: SeriesId | None = None
    run_interval: Interval | None = None
    for record in execution.manifest.series:
        series = record.series_id
        if series.timeframe.id == _MINUTE_TIMEFRAME:
            continue
        definition = timeframe_defs[series.timeframe.id]
        if definition.ref != series.timeframe:
            raise ScanRefused(
                f"{series} was accepted with {series.timeframe}, not {definition.ref}"
            )
        schedules[series] = SeriesSchedule(
            series=series, timeframe_def=definition, calendar=calendar
        )
        partition = _research_record(execution.manifest, series)
        columns = read_times(
            store, execution_id, partition, ("bar_start", "bar_end", "first_observed_at")
        )
        bars[series] = SeriesTimes(
            starts=columns["bar_start"],
            ends=columns["bar_end"],
            first_observed=columns["first_observed_at"],
        )
        files[str(partition.partition_id)] = _sha256(
            store.partition_file(execution_id, partition.partition_id)
        )
        if series.timeframe.id == "15m":
            execution_series = series
            run_interval = partition.interval
    if execution_series is None or run_interval is None:
        raise ScanRefused(f"{execution_id} has no 15m series")
    minute_series = _minute_series(execution.manifest, symbol)
    traded_record = _research_record(execution.manifest, minute_series)
    traded = read_times(store, execution_id, traded_record, ("bar_start",))["bar_start"]
    files[str(traded_record.partition_id)] = _sha256(
        store.partition_file(execution_id, traded_record.partition_id)
    )
    usdjpy_series = _minute_series(conversion.manifest, USDJPY)
    usdjpy_record = _research_record(conversion.manifest, usdjpy_series)
    usdjpy = read_times(store, conversion_id, usdjpy_record, ("bar_start",))["bar_start"]
    files[f"{conversion_id[:8]}…/{usdjpy_record.partition_id}"] = _sha256(
        store.partition_file(conversion_id, usdjpy_record.partition_id)
    )
    times = PairTimes(
        pair=symbol,
        run_interval=run_interval,
        calendar=calendar,
        execution_series=execution_series,
        minute_series=minute_series,
        schedules=schedules,
        bars=bars,
        observation=observation,
        traded_minutes=traded,
        leg_minutes={symbol: traded, USDJPY: usdjpy},
    )
    inputs = {
        "execution_snapshot": execution_id,
        "conversion_snapshot": conversion_id,
        "run_interval": {"start": str(run_interval.start), "end": str(run_interval.end)},
        "partition_files_sha256": files,
        "recorded_minutes": {"traded_pair": len(traded), "USDJPY": len(usdjpy)},
    }
    return _Loaded(times=times, inputs=inputs)


# --- 1 通貨 × 1 組 ---------------------------------------------------------------


def scan_one(snapshots_root: str, repo_root: str, pair: str, group: str) -> dict[str, Any]:
    """1 通貨 × 1 組の集計（別プロセスで呼ぶ）。"""
    started = time.perf_counter()
    root = Path(repo_root)
    calendar = load_calendar(root / CALENDAR_FILE)
    timeframe_defs = load_timeframes(root / TIMEFRAMES_FILE)
    loaded = load_pair(Path(snapshots_root), pair, group, calendar, timeframe_defs)
    times = loaded.times
    decisions = decision_points(times)
    reference = tuple(reference_points(calendar, times.run_interval))
    covered = frozenset(reference)
    missing = [str(moment) for moment in decisions if moment not in covered]
    return {
        "pair": pair,
        "group": group,
        "inputs": loaded.inputs,
        "set1_decision_points": scan_points(times, decisions),
        "set2_reference_points": scan_points(times, reference),
        "set1_not_in_set2": missing,
        "seconds": round(time.perf_counter() - started, 1),
    }


def crosscheck(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """EURUSD の照合（D18 §4.2 の「道具の照合」）。"""
    checks: dict[str, Any] = {}
    for result in results:
        if result["pair"] != "EURUSD":
            continue
        reference = result["set2_reference_points"]["staleness"]
        intervals = reference["over_15"]["intervals"]
        dates = sorted({item["start"][:10] for item in intervals})
        maxima = [item["max_minutes"] for item in intervals]
        over_30 = (
            result["set1_decision_points"]["staleness"]["over_30"]["points_over"]
            + reference["over_30"]["points_over"]
        )
        low, high = CROSSCHECK_MINUTES
        ok = (
            tuple(dates) == CROSSCHECK_DATES
            and len(intervals) == len(CROSSCHECK_DATES)
            and all(low <= value <= high for value in maxima)
            and over_30 == 0
        )
        checks[result["group"]] = {
            "expected_dates": list(CROSSCHECK_DATES),
            "expected_minutes": list(CROSSCHECK_MINUTES),
            "set2_over_15_dates": dates,
            "set2_over_15_max_minutes": maxima,
            "over_30_points_both_sets": over_30,
            "outcome": "PASS" if ok else "FAIL",
        }
    return checks


def _git_commit(repo_root: Path) -> str:
    completed = subprocess.run(  # noqa: S603 - 固定の引数
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _git_dirty(repo_root: Path) -> bool:
    """コード・道具・設定に未コミットの変更があるか（記録の出どころを確かめるため）。"""
    completed = subprocess.run(  # noqa: S603 - 固定の引数
        ["git", "-C", str(repo_root), "status", "--porcelain", "--", "src", "tools", "configs"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(completed.stdout.strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--snapshots", required=True, help="snapshot の基点（data/snapshots）")
    parser.add_argument("--repo-root", default=".", help="リポジトリの根（設定ファイルを読む）")
    parser.add_argument("--out", required=True, help="scan.json の出力先")
    parser.add_argument("--workers", type=int, default=4, help="並べるプロセスの数")
    parser.add_argument("--pairs", nargs="*", default=list(PAIRS), help="対象の通貨ペア")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        # 既存の記録を上書きしない（既定は「存在すれば失敗」。AGENTS.md）。集計の前に止める。
        parser.error(f"{out} already exists; the scan does not overwrite a recorded result")
    repo_root = Path(args.repo_root).resolve()
    started_at = datetime.now(tz=UTC).replace(microsecond=0)
    started = time.perf_counter()
    jobs = [(pair, group) for pair in args.pairs for group in ("standard", "candidate")]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(scan_one, args.snapshots, str(repo_root), pair, group)
            for pair, group in jobs
        ]
        results = [future.result() for future in futures]
    tool = Path(__file__).resolve()
    over_30 = {
        f"{result['pair']}/{result['group']}": result["set1_decision_points"]["staleness"][
            "over_30"
        ]["points_over"]
        + (
            0
            if result["set1_decision_points"]["skew"] is None
            else result["set1_decision_points"]["skew"]["over_30"]["points_over"]
        )
        for result in results
    }
    payload = {
        "schema_version": 1,
        "design": "D18 §4.2（決定 37）",
        "limit_minutes": _minutes(LIMIT),
        "reference_limit_minutes": _minutes(REFERENCE_LIMIT),
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "tool": {
            "path": "tools/survey/conversion_staleness_scan.py",
            "sha256": _sha256(tool),
            "git_commit": _git_commit(repo_root),
            "git_dirty": _git_dirty(repo_root),
        },
        "calendar": {
            "file": CALENDAR_FILE.as_posix(),
            "sha256": _sha256(repo_root / CALENDAR_FILE),
        },
        "timeframes": {
            "file": TIMEFRAMES_FILE.as_posix(),
            "sha256": _sha256(repo_root / TIMEFRAMES_FILE),
        },
        "set1_over_30_points": over_30,
        "stop_condition_a": any(
            value > 0 for key, value in over_30.items() if not key.startswith("EURUSD/")
        ),
        "crosscheck_eurusd": crosscheck(results),
        "results": results,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    # 集計の間に別の書き手が作っていても上書きしない（排他的に新しく作る）。
    with out.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({key: payload[key] for key in ("set1_over_30_points", "stop_condition_a")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
