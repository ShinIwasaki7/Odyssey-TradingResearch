"""古さとずれの集計の道具の受入テスト（D18 §6 の RP4-15〜RP4-17。人工データだけ）。

`tools/survey/conversion_staleness_scan.py`（D18 §4.2）が、

- RP4-15: 休場と取引の通貨ペアに記録の無い分を除いた古さを換算の実装（エンジンの
  `session_staleness`）と同じ値で数え、30 分ちょうどを超えに数えないこと。執行足の途中の再開の分を
  処理点の集合 (1) に入れ、再開の分で 30 分を超え足の終わりで上限内に戻る入力で超過を 1 件と
  数えること。処理点の集合 (1) がエンジンの判断時点の列挙（`BacktestEngine.decision_times`）と
  一致すること。
- RP4-16: USDCHF の形（2 本の脚）でずれを換算の実装（`session_skew`）と同じ値で数えること。
- RP4-17: 封印・隔離の partition と 2024 年以降の partition を、読み取りを始めずに止めること。価格の
  列は時刻だけの読み取りで開けないこと。

を確かめる。日付は研究期間（2023 年）の人工の値で、1 分足の価格は換算の実装の側（比べる相手）を
組むためだけの人工の値である（道具は価格を受け取らない）。実データ・封印 partition は読まない。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.engine.loop import BacktestEngine, EngineContext, TraceOutputSink
from odyssey_fx.backtest.portfolio.conversion import (
    observe_path,
    resolve_route,
    session_skew,
    session_staleness,
)
from odyssey_fx.backtest.trace.manifest import DataCapabilityReport, config_digest_of
from odyssey_fx.common.ids import IdAllocator
from odyssey_fx.common.refs import ContentDigest, SnapshotRef, run_id
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.asof import ExecutionSeriesView
from odyssey_fx.marketdata.application.mt5_observation import Mt5MinuteView, Mt5Observation
from odyssey_fx.marketdata.application.publication import build_feed
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES, AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId, PartitionRecord
from tests.fixtures.backtest.conversion import FakeMinutes, FakeQuotes, minute_rates, minute_series
from tests.fixtures.backtest.harness import (
    ACCOUNT,
    CODE_DIGEST,
    CONVERSION_POLICY,
    COST_MODEL,
    ENV_DIGEST,
    EXECUTION_POLICY,
    LOCK_DIGEST,
    RISK_POLICY,
    SYMBOL_SPEC,
    SYMBOL_SPEC_REF,
    _policy_ref,
    calendar_ref_of,
    compiled_strategy,
)
from tests.fixtures.mt5 import market as m5
from tests.fixtures.mt5.run import MT5_TIMEFRAME_REFS
from tests.fixtures.synthetic import market
from tools.survey import conversion_staleness_scan as scan

CALENDAR = market.calendar()
EURUSD = Symbol("EURUSD")
USDCHF = Symbol("USDCHF")
USDJPY = Symbol("USDJPY")
_MINUTE = timedelta(minutes=1)


def _t(text: str) -> UtcTime:
    return UtcTime.parse(f"2023-02-{text}:00Z")


def _starts(bars: Sequence[Bar]) -> tuple[UtcTime, ...]:
    return tuple(sorted((bar.bar_start for bar in bars), key=lambda value: value.value))


def _times(
    pair: Symbol,
    traded: Sequence[Bar],
    usdjpy: Sequence[Bar],
    run: Interval,
) -> scan.PairTimes:
    """取引の通貨ペアと USDJPY の 1 分足の時刻だけから、道具の入力を作る（執行足は使わない）。"""
    execution = SeriesId(symbol=pair, timeframe=market.TF_15M.ref, basis=PriceBasis.BID)
    traded_starts = _starts(traded)
    return scan.PairTimes(
        pair=pair,
        run_interval=run,
        calendar=CALENDAR,
        execution_series=execution,
        minute_series=minute_series(pair),
        schedules={},
        bars={execution: scan.SeriesTimes(starts=(), ends=(), first_observed=())},
        observation=Mt5Observation.from_report(IntegrityReport(), _table_ref()),
        traded_minutes=traded_starts,
        leg_minutes={pair: traded_starts, USDJPY: _starts(usdjpy)},
    )


def _table_ref() -> Any:
    from odyssey_fx.marketdata.domain.time_rule import TimeRuleTableRef, TimeRuleVariant

    return TimeRuleTableRef(
        table_id="synthetic", version=1, sha256="0" * 64, variant=TimeRuleVariant.STANDARD
    )


def _scan_at(times: scan.PairTimes, moment: UtcTime) -> dict[str, Any]:
    return scan.scan_points(times, [moment])


# --- RP4-15: 古さ（規則 9）を換算の実装と同じ値で数える ------------------------------------------

#: 金曜の閉場 2023-02-10T22:00Z、日曜の開場 2023-02-12T22:00Z（NY 17:00。冬時間）。
_FRI = Interval(start=_t("10T21:00"), end=_t("10T22:00"))
_SUN = Interval(start=_t("12T22:00"), end=_t("12T23:00"))


def _weekend_case() -> tuple[tuple[Bar, ...], tuple[Bar, ...]]:
    """USDJPY は金曜 21:40 から日曜 22:20 まで無記録、EURUSD は金曜 21:50〜21:55 が無記録。"""
    usdjpy = (
        *minute_rates(USDJPY, Interval(start=_FRI.start, end=_t("10T21:40")), "150.000"),
        *minute_rates(USDJPY, Interval(start=_t("12T22:20"), end=_SUN.end), "150.000"),
    )
    eurusd = (
        *minute_rates(
            EURUSD, _FRI, "1.500", holes=(Interval(start=_t("10T21:50"), end=_t("10T21:55")),)
        ),
        *minute_rates(EURUSD, _SUN, "1.500"),
    )
    return eurusd, usdjpy


def _engine_staleness(
    pair: Symbol, traded: Sequence[Bar], legs: dict[Symbol, tuple[Bar, ...]], at: UtcTime
) -> tuple[int, int]:
    """換算の実装（エンジンが呼ぶ関数）で測った古さとずれ（分）。"""
    quotes = FakeQuotes(symbols=frozenset(legs), bars=legs)
    route = resolve_route(pair.quote, ACCOUNT.currency, quotes.symbols, scan.POLICY)
    assert route is not None
    path = observe_path(route, at, quotes)
    minutes = FakeMinutes(minute_series=minute_series(pair), bars=tuple(traded))
    staleness = session_staleness(path, at, CALENDAR, minutes)
    skew = session_skew(path, CALENDAR, minutes)
    return int(staleness / _MINUTE), int(skew / _MINUTE)


@pytest.mark.parametrize("moment", ["12T22:00", "12T22:14", "12T22:15", "12T22:20", "12T22:21"])
def test_rp4_15_staleness_matches_the_conversion_implementation(moment: str) -> None:
    eurusd, usdjpy = _weekend_case()
    times = _times(EURUSD, eurusd, usdjpy, Interval(start=_FRI.start, end=_SUN.end))
    at = _t(moment)
    expected, _ = _engine_staleness(EURUSD, eurusd, {EURUSD: eurusd, USDJPY: usdjpy}, at)
    result = _scan_at(times, at)
    assert result["staleness"]["max_minutes"] == expected
    assert result["skew"] is None  # EURUSD の経路は 1 本（USDJPY）


def test_rp4_15_excludes_the_weekend_and_unrecorded_minutes_and_allows_exactly_30() -> None:
    eurusd, usdjpy = _weekend_case()
    times = _times(EURUSD, eurusd, usdjpy, Interval(start=_FRI.start, end=_SUN.end))
    points = [_t(text) for text in ("12T22:00", "12T22:14", "12T22:15", "12T22:20", "12T22:21")]
    result = scan.scan_points(times, points)
    staleness = result["staleness"]
    # 金曜 21:39〜22:00 の 21 分から EURUSD の無記録 5 分を除いて 16 分（休場の時間は数えない）
    assert staleness["distribution_minutes"] == {"1": 1, "16": 1, "30": 1, "31": 1, "36": 1}
    over = staleness["over_30"]
    assert over["points_over"] == 2  # 30 分ちょうど（22:14）は超えに数えない
    assert over["runs"] == 1
    assert over["intervals"][0]["start"] == "2023-02-12T22:15:00Z"
    assert over["intervals"][0]["end"] == "2023-02-12T22:20:00Z"
    assert over["intervals"][0]["max_minutes"] == 36
    assert staleness["over_15"]["points_over"] == 4


# --- RP4-15: 処理点の集合 (1) とエンジンの判断時点 ---------------------------------------

_DATA = Interval(start=_t("06T22:00"), end=_t("07T22:00"))
_RUN = Interval(start=_t("07T10:00"), end=_t("07T11:00"))
#: EURUSD の無記録: 10:15〜10:30 は執行足の全分、10:37〜10:39 は足の途中、10:45〜10:47 は足の始め。
_HOLES = (
    Interval(start=_t("07T10:15"), end=_t("07T10:30")),
    Interval(start=_t("07T10:37"), end=_t("07T10:39")),
    Interval(start=_t("07T10:45"), end=_t("07T10:47")),
)


def _mt5_snapshot(tmp_path: Path) -> tuple[Any, Any]:
    table = m5.write_rule_files(tmp_path, pairs=("EURUSD",), coverage=("2023-01-01", "2023-03-31"))
    rows = m5.minute_rows(CALENDAR, _DATA, unrecorded=_HOLES)
    pending = m5.build_pending(
        CALENDAR,
        table,
        [m5.MonthSpec(2023, 2, rows)],
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        symbol=EURUSD,
    )
    return m5.readable(pending), pending


def _engine(snapshot: Any, pending: Any) -> BacktestEngine:
    """本番の公開フィード・執行系列のビュー・1 分足のビューを結線したエンジン（run しない）。"""
    partition_bars = dict(pending.partition_bars)
    allowed = frozenset(partition_bars)
    schedules = {
        record.series_id: SeriesSchedule(
            series=record.series_id,
            timeframe_def=m5.TIMEFRAME_DEFS[record.series_id.timeframe.id],
            calendar=CALENDAR,
        )
        for record in snapshot.manifest.series
    }
    bars_of: dict[SeriesId, list[Bar]] = {}
    for partition_id, bars in partition_bars.items():
        bars_of.setdefault(partition_id.series, []).extend(bars)
    execution = next(s for s in schedules if s.timeframe.id == "15m")
    minute = next(s for s in schedules if s.timeframe.id == "1m")
    observation = Mt5Observation.from_snapshot(snapshot)
    assert observation is not None
    feed = build_feed(
        snapshot,
        allowed,
        partition_bars,
        schedules,
        _RUN,
        execution_series=frozenset({execution}),
        internal_series=frozenset({minute}),
        access_boundaries=INITIAL_ACCESS_BOUNDARIES,
    )
    compiled = compiled_strategy()
    config = RunConfig(
        run_interval=_RUN,
        snapshot_ref=SnapshotRef(snapshot_id=snapshot.manifest.snapshot_id()),
        compiled_ref=compiled.compiled_ref,
        account=ACCOUNT,
        risk_policy_ref=_policy_ref("risk", "r"),
        execution_policy_ref=_policy_ref("execution", "e"),
        cost_model_ref=_policy_ref("cost", "c"),
        conversion_policy_ref=_policy_ref("conversion", "v"),
        delay_scenario_ref=_policy_ref("delay", "d"),
        execution_series=execution,
    )
    return BacktestEngine(
        config=config,
        compiled=compiled,
        runtime=object(),  # type: ignore[arg-type]
        context=EngineContext(ACCOUNT),
        output_sink=TraceOutputSink(),
        allocator=IdAllocator(
            run_id(
                config_digest_of(
                    config,
                    symbol_spec_ref=SYMBOL_SPEC_REF,
                    calendar_ref=calendar_ref_of(CALENDAR),
                    timeframe_def_refs=MT5_TIMEFRAME_REFS,
                ),
                CODE_DIGEST,
                LOCK_DIGEST,
                ENV_DIGEST,
            )
        ),
        feed=feed,
        execution_series=ExecutionSeriesView(
            snapshot=snapshot,
            series=execution,
            allowed_partitions=allowed,
            partition_bars=partition_bars,
            schedule=schedules[execution],
        ),
        calendar=CALENDAR,
        risk_policy=RISK_POLICY,
        execution_policy=EXECUTION_POLICY,
        cost_model=COST_MODEL,
        conversion_policy=CONVERSION_POLICY,
        symbol_spec=SYMBOL_SPEC,
        capability_report=DataCapabilityReport(compiled_match=True, integrity=IntegrityReport()),
        minutes=Mt5MinuteView(
            observation=observation, minute_series=minute, bars=tuple(bars_of[minute])
        ),
    )


def _pair_times_of(snapshot: Any, pending: Any, usdjpy: tuple[UtcTime, ...]) -> scan.PairTimes:
    """同じ snapshot の足から**時刻だけ**を取り出して道具の入力にする（道具は足を受け取らない）。"""
    by_series: dict[SeriesId, list[Bar]] = {}
    for partition_id, bars in pending.partition_bars.items():
        assert partition_id.access_class is AccessClass.RESEARCH_HISTORY
        by_series.setdefault(partition_id.series, []).extend(bars)
    schedules: dict[SeriesId, SeriesSchedule] = {}
    times: dict[SeriesId, scan.SeriesTimes] = {}
    minute: SeriesId | None = None
    execution: SeriesId | None = None
    for series, bars in by_series.items():
        if series.timeframe.id == "1m":
            minute = series
            continue
        if series.timeframe.id == "15m":
            execution = series
        ordered = sorted(bars, key=lambda bar: bar.bar_start.value)
        schedules[series] = SeriesSchedule(
            series=series, timeframe_def=m5.TIMEFRAME_DEFS[series.timeframe.id], calendar=CALENDAR
        )
        times[series] = scan.SeriesTimes(
            starts=tuple(bar.bar_start for bar in ordered),
            ends=tuple(bar.bar_end for bar in ordered),
            first_observed=tuple(bar.first_observed_at for bar in ordered),
        )
    assert minute is not None and execution is not None
    traded = _starts(by_series[minute])
    observation = Mt5Observation.from_snapshot(snapshot)
    assert observation is not None
    return scan.PairTimes(
        pair=EURUSD,
        run_interval=_RUN,
        calendar=CALENDAR,
        execution_series=execution,
        minute_series=minute,
        schedules=schedules,
        bars=times,
        observation=observation,
        traded_minutes=traded,
        leg_minutes={EURUSD: traded, USDJPY: usdjpy},
    )


def test_rp4_15_set1_equals_the_engine_decision_times(tmp_path: Path) -> None:
    snapshot, pending = _mt5_snapshot(tmp_path)
    engine = _engine(snapshot, pending)
    times = _pair_times_of(snapshot, pending, ())
    points = scan.decision_points(times)
    assert points == engine.decision_times()
    texts = {str(moment)[11:16] for moment in points}
    assert "10:39" in texts  # 執行足の途中の再開の分（D06 §18.5）
    assert "10:47" in texts  # 足の始めの無記録の後の最初の観測の分（始値の段。D03 §15.5）
    assert "10:30" in texts  # 全分無記録の足の予定境界（D03 §7.1）
    assert "10:45" in texts and "11:00" in texts
    # 集合 (2) は集合 (1) を覆う。
    reference = set(scan.reference_points(CALENDAR, _RUN))
    assert set(points) <= reference


def test_rp4_15_counts_the_mid_bar_resumption_once(tmp_path: Path) -> None:
    """再開の分 10:39 で 30 分を超え、足の終わり 10:45 では上限内に戻る入力で、超過は 1 件。

    USDJPY は 09:50 の後 10:41 まで無記録。EURUSD に記録のある分だけを数えるので、10:30 の判断
    時点では 09:50〜10:15 の 25 分（10:15〜10:30 は全分無記録）、再開の分 10:39 では 25 分＋
    10:30〜10:37 の 7 分＝32 分、10:45 では 10:44 の観測から 1 分。
    """
    snapshot, pending = _mt5_snapshot(tmp_path)
    usdjpy = tuple(
        moment
        for moment in _starts(minute_rates(USDJPY, _DATA, "150.000"))
        if not (_t("07T09:50") < moment < _t("07T10:41"))
    )
    times = _pair_times_of(snapshot, pending, usdjpy)
    points = scan.decision_points(times)
    result = scan.scan_points(times, points)
    over = result["staleness"]["over_30"]
    assert over["points_over"] == 1
    assert over["intervals"][0]["start"] == "2023-02-07T10:39:00Z"
    expected, _ = _engine_staleness(
        EURUSD,
        tuple(bar for bar in minute_rates(EURUSD, _DATA, "1.500", holes=_HOLES)),
        {
            EURUSD: minute_rates(EURUSD, _DATA, "1.500", holes=_HOLES),
            USDJPY: tuple(
                bar
                for bar in minute_rates(USDJPY, _DATA, "150.000")
                if not (_t("07T09:50") < bar.bar_start < _t("07T10:41"))
            ),
        },
        _t("07T10:39"),
    )
    assert over["intervals"][0]["max_minutes"] == expected == 32
    # 足の終わり 10:45 では新しい観測（10:44）があり、上限内に戻る。
    at_end = scan.scan_points(times, [_t("07T10:45")])
    assert at_end["staleness"]["max_minutes"] == 1


# --- RP4-16: USDCHF の形（2 本の脚）のずれ（規則 6） -------------------------------------


def _usdchf_case(chf_hole: Interval | None) -> tuple[tuple[Bar, ...], tuple[Bar, ...]]:
    """USDJPY は金曜 21:40 から無記録で日曜の開場から記録あり。USDCHF は `chf_hole` だけ無記録。"""
    usdjpy = (
        *minute_rates(USDJPY, Interval(start=_FRI.start, end=_t("10T21:40")), "150.000"),
        *minute_rates(USDJPY, Interval(start=_t("12T22:30"), end=_SUN.end), "150.000"),
    )
    holes = () if chf_hole is None else (chf_hole,)
    usdchf = (
        *minute_rates(USDCHF, _FRI, "0.900"),
        *minute_rates(USDCHF, _SUN, "0.900", holes=holes),
    )
    return usdchf, usdjpy


@pytest.mark.parametrize(
    ("chf_hole", "moment", "expected_skew"),
    [
        # USDJPY の脚（金曜 21:39）と USDCHF の脚（日曜 22:11）: 金曜 21 分＋日曜 11 分＝32 分。
        (None, "12T22:12", 32),
        # USDCHF の無記録の分（日曜 22:00〜22:10）は数えない（仮置き C4-6）: 21＋4＝25 分。
        (Interval(start=_t("12T22:00"), end=_t("12T22:10")), "12T22:15", 25),
        # 上限ちょうど: 21＋9＝30 分（超えに数えない）。
        (None, "12T22:10", 30),
    ],
)
def test_rp4_16_two_leg_skew_matches_the_conversion_implementation(
    chf_hole: Interval | None, moment: str, expected_skew: int
) -> None:
    usdchf, usdjpy = _usdchf_case(chf_hole)
    times = _times(USDCHF, usdchf, usdjpy, Interval(start=_FRI.start, end=_SUN.end))
    at = _t(moment)
    staleness, skew = _engine_staleness(USDCHF, usdchf, {USDCHF: usdchf, USDJPY: usdjpy}, at)
    assert skew == expected_skew
    result = _scan_at(times, at)
    assert result["route"] == [
        {"symbol": "USDCHF", "inverted": True},
        {"symbol": "USDJPY", "inverted": False},
    ]
    assert result["skew"]["max_minutes"] == skew
    assert result["staleness"]["max_minutes"] == staleness
    assert result["skew"]["over_30"]["points_over"] == (1 if skew > 30 else 0)


# --- RP4-17: 封印・隔離の partition と 2024 年以降を読まない -----------------------------

_SERIES_1M = minute_series(EURUSD)
_DIGEST = ContentDigest(algorithm="sha256", hex="0" * 64)


def _record(access_class: AccessClass, start: str, end: str, period: str | None = None) -> Any:
    return PartitionRecord(
        partition_id=PartitionId(
            series=_SERIES_1M,
            access_class=access_class,
            period_start=None if period is None else UtcTime.parse(period),
        ),
        interval=Interval(start=UtcTime.parse(start), end=UtcTime.parse(end)),
        bar_count=1,
        digest=_DIGEST,
    )


class _Reader:
    """呼ばれたかを記録する時刻の読み取り（ファイルは開かない）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_partition_times(
        self, snapshot_dir: str, partition_id: PartitionId, columns: Sequence[str]
    ) -> dict[str, tuple[UtcTime, ...]]:
        self.calls.append(str(partition_id))
        return {name: () for name in columns}


@pytest.mark.parametrize(
    "record",
    [
        _record(AccessClass.LEGACY_HOLDOUT, "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        _record(AccessClass.QUARANTINED_UNASSIGNED, "2017-01-01T22:00:00Z", "2017-02-01T00:00:00Z"),
        _record(
            AccessClass.HOLDOUT_SINCE_2024,
            "2024-01-01T00:00:00Z",
            "2024-01-07T22:00:00Z",
            period="2024-01-01T00:00:00Z",
        ),
        # 研究履歴の分類でも、終わりが 2024-01-01T00:00Z より後なら読まない。
        _record(AccessClass.RESEARCH_HISTORY, "2017-01-01T22:00:00Z", "2024-01-02T00:00:00Z"),
    ],
)
def test_rp4_17_refuses_sealed_quarantined_and_post_2023_partitions_before_reading(
    record: Any,
) -> None:
    reader = _Reader()
    with pytest.raises(scan.ScanRefused):
        scan.read_times(reader, "snapshot", record, ("bar_start",))
    assert reader.calls == []


def test_rp4_17_reads_the_research_partition_through_the_gate() -> None:
    reader = _Reader()
    record = _record(AccessClass.RESEARCH_HISTORY, "2017-01-01T22:00:00Z", "2023-12-29T22:00:00Z")
    assert scan.read_times(reader, "snapshot", record, ("bar_start",)) == {"bar_start": ()}
    assert reader.calls == ["EURUSD_1m_bid/RESEARCH_HISTORY"]


@pytest.mark.parametrize("columns", [("bar_start", "close"), ("open",), ("spread",), ()])
def test_rp4_17_time_reader_refuses_price_columns_before_opening(
    tmp_path: Path, columns: tuple[str, ...]
) -> None:
    store = ParquetSnapshotStore(root=tmp_path)
    partition = PartitionId(series=_SERIES_1M, access_class=AccessClass.RESEARCH_HISTORY)
    # ファイルは無い。開く前に止まるので FileNotFoundError ではなく列の拒否になる。
    with pytest.raises(MarketDataValueError, match="time columns"):
        store.read_partition_times("snapshot", partition, columns)
