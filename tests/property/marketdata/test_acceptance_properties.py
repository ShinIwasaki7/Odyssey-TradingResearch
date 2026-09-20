"""受入れの決定論性と集約の正しさのプロパティテスト（D03 §11）。

確かめること:

- **受入れの決定論性**: 同じ入力なら同じ snapshot 識別子になる。原ファイルの列挙順、
  partition の列挙順、検査の実行順を入れ替えても識別子は変わらない。受入れ実行時刻も
  識別子に影響しない（D03 §4・§3.7.1）。
- **集約の正しさ**: 生成した上位足の始値・高値・安値・終値・出来高が、構成足の集計と一致
  する（D03 §5.1）。
"""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.aggregation import aggregate
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.snapshot import SeriesManifest
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
FOUR_HOUR = market.series(timeframe_id="4h_ny17")
DAILY = market.series(timeframe_id="1d_ny17")
CALENDAR = market.calendar()


# --- 受入れの決定論性（D03 §4・§3.7.1）--------------------------------------

_source_paths = st.lists(
    st.sampled_from(
        [
            "data/raw/market/AUDJPY_1h_merged.csv",
            "data/raw/market/EURUSD_1h_merged.csv",
            "data/raw/market/GBPUSD_1h_merged.csv",
            "data/raw/market/USDJPY_1h_merged.csv",
        ]
    ),
    min_size=1,
    max_size=4,
    unique=True,
)


@given(_source_paths, st.permutations(list(range(4))))
@settings(max_examples=40, deadline=None)
def test_the_snapshot_id_is_independent_of_the_source_order(
    paths: list[str], permutation: list[int]
) -> None:
    """原ファイルの列挙順を入れ替えても同じ識別子になる（D03 §3.7.1 の正規順序）。"""
    sources = [snapshots.source(path, path.split("/")[-1][:6]) for path in paths]
    shuffled = [sources[index] for index in permutation if index < len(sources)]
    assert (
        snapshots.manifest(sources=sources).snapshot_id()
        == snapshots.manifest(sources=shuffled).snapshot_id()
    )


_acceptance_times = st.integers(min_value=0, max_value=100000).map(
    lambda seconds: UtcTime.parse("2026-09-20T00:00:00Z") + timedelta(seconds=seconds)
)


@given(_acceptance_times, _acceptance_times)
@settings(max_examples=40, deadline=None)
def test_the_snapshot_id_is_independent_of_the_acceptance_time(
    first: UtcTime, second: UtcTime
) -> None:
    """受入れ実行時刻は識別に含めない（D03 §3.7）。"""
    assert (
        snapshots.manifest(created_at=first).snapshot_id()
        == snapshots.manifest(created_at=second).snapshot_id()
    )


@given(st.permutations(list(AccessClass)))
@settings(max_examples=20, deadline=None)
def test_the_snapshot_id_is_independent_of_the_partition_order(
    order: list[AccessClass],
) -> None:
    """partition の列挙順を入れ替えても同じ識別子になる。"""
    partitions = tuple(snapshots.partition(HOURLY, access) for access in order)
    series_record = SeriesManifest(
        series_id=HOURLY,
        covered_interval=snapshots.COVERED,
        bar_count=300,
        partitions=tuple(record.partition_id for record in partitions),
    )
    reference_partitions = tuple(snapshots.partition(HOURLY, access) for access in AccessClass)
    reference_record = SeriesManifest(
        series_id=HOURLY,
        covered_interval=snapshots.COVERED,
        bar_count=300,
        partitions=tuple(record.partition_id for record in reference_partitions),
    )
    assert (
        snapshots.manifest(partitions=partitions, series_records=(series_record,)).snapshot_id()
        == snapshots.manifest(
            partitions=reference_partitions, series_records=(reference_record,)
        ).snapshot_id()
    )


# --- 集約の正しさ（D03 §5.1）------------------------------------------------

_windows = st.integers(min_value=0, max_value=20).map(
    lambda days: Interval(
        start=UtcTime.parse("2026-01-05T22:00:00Z") + timedelta(days=days),
        end=UtcTime.parse("2026-01-08T22:00:00Z") + timedelta(days=days),
    )
)


@given(_windows, st.sampled_from([("4h", FOUR_HOUR), ("1d", DAILY)]))
@settings(max_examples=30, deadline=None)
def test_the_aggregate_matches_a_recomputation_from_the_components(
    window: Interval, target: tuple[str, object]
) -> None:
    """上位足の OHLC と出来高は、構成足からの再計算と一致する（D03 §11 のプロパティ）。"""
    label, target_series = target
    target_def = market.TF_4H_NY17 if label == "4h" else market.TF_1D_NY17
    source_bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, window)
    result = aggregate(
        source_bars,
        source_timeframe_def=market.TF_1H,
        target_series=target_series,  # type: ignore[arg-type]
        target_timeframe_def=target_def,
        calendar=CALENDAR,
    )
    by_start = {bar.bar_start: bar for bar in source_bars}
    for aggregated in result.bars:
        components = [
            by_start[start]
            for start in CALENDAR.expected_bar_starts(market.TF_1H, aggregated.interval)
        ]
        assert components
        assert aggregated.open == components[0].open
        assert aggregated.close == components[-1].close
        assert aggregated.high == max(bar.high for bar in components)
        assert aggregated.low == min(bar.low for bar in components)
        assert aggregated.volume == sum(
            (bar.volume for bar in components), start=decimal_from_str("0")
        )


@given(_windows)
@settings(max_examples=20, deadline=None)
def test_aggregated_bars_never_overlap_and_stay_inside_the_source_range(
    window: Interval,
) -> None:
    """上位足は重ならず、構成足が覆う範囲の外へはみ出さない。"""
    source_bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, window)
    if not source_bars:
        return
    result = aggregate(
        source_bars,
        source_timeframe_def=market.TF_1H,
        target_series=FOUR_HOUR,
        target_timeframe_def=market.TF_4H_NY17,
        calendar=CALENDAR,
    )
    covered = Interval(start=source_bars[0].bar_start, end=source_bars[-1].bar_end)
    for aggregated in result.bars:
        assert covered.start <= aggregated.bar_start
        assert aggregated.bar_end <= covered.end
    for earlier, later in zip(result.bars, result.bars[1:], strict=False):
        assert earlier.bar_end <= later.bar_start


@given(_windows)
@settings(max_examples=20, deadline=None)
def test_aggregating_the_same_input_twice_gives_the_same_output(
    window: Interval,
) -> None:
    """集約は決定論的（同じ構成足なら同じ上位足）。"""
    source_bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, window)
    first = aggregate(
        source_bars,
        source_timeframe_def=market.TF_1H,
        target_series=DAILY,
        target_timeframe_def=market.TF_1D_NY17,
        calendar=CALENDAR,
    )
    second = aggregate(
        tuple(reversed(source_bars)),
        source_timeframe_def=market.TF_1H,
        target_series=DAILY,
        target_timeframe_def=market.TF_1D_NY17,
        calendar=CALENDAR,
    )
    assert first == second
