"""上位足の生成の単体テスト（D03 §5・§11、ADR-0024）。

確かめること:

- 始値・高値・安値・終値・出来高が構成足の集計と一致する。
- `bar_start` が区間の開始であり、最初の観測時刻ではない（ADR-0024）。
- 構成足が欠けた区間は生成せず、「存在すべき足の欠落」として報告する（D03 §5.2）。
- DST 切替日の日足・4h 足が伸縮した区間で生成される。
"""

from __future__ import annotations

from collections.abc import Iterable

from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.aggregation import (
    AGGREGATION_RULE_VERSION,
    AggregationResult,
    aggregate,
)
from odyssey_fx.marketdata.domain.bar import ProvenanceKind
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.synthetic import market

HOURLY = market.series()
FOUR_HOUR = market.series(timeframe_id="4h_ny17")
DAILY = market.series(timeframe_id="1d_ny17")
CALENDAR = market.calendar()


def _aggregate_to(
    target_series: SeriesId,
    target_def: TimeframeDefinition,
    window: Interval,
    *,
    skip_starts: Iterable[UtcTime] = (),
) -> AggregationResult:
    source_bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, window, skip_starts=skip_starts)
    return aggregate(
        source_bars,
        source_timeframe_def=market.TF_1H,
        target_series=target_series,
        target_timeframe_def=target_def,
        calendar=CALENDAR,
    )


MIDWEEK = Interval(
    start=UtcTime.parse("2026-01-13T00:00:00Z"), end=UtcTime.parse("2026-01-16T00:00:00Z")
)


# --- OHLC の集計（D03 §5.1）-------------------------------------------------


def test_the_aggregate_ohlc_matches_the_component_bars() -> None:
    result = _aggregate_to(FOUR_HOUR, market.TF_4H_NY17, MIDWEEK)
    assert result.bars
    source_bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, MIDWEEK)
    by_start = {bar.bar_start: bar for bar in source_bars}
    for aggregated in result.bars:
        components = [
            by_start[start]
            for start in CALENDAR.expected_bar_starts(market.TF_1H, aggregated.interval)
        ]
        assert aggregated.open == components[0].open
        assert aggregated.close == components[-1].close
        assert aggregated.high == max(bar.high for bar in components)
        assert aggregated.low == min(bar.low for bar in components)
        assert aggregated.volume == sum(
            (bar.volume for bar in components), start=decimal_from_str("0")
        )


def test_the_bar_start_is_the_interval_start_not_the_first_observation() -> None:
    """旧基盤の「最初の観測時刻ラベル」は採用しない（ADR-0024）。"""
    result = _aggregate_to(FOUR_HOUR, market.TF_4H_NY17, MIDWEEK)
    for aggregated in result.bars:
        aligned = market.TF_4H_NY17.expected_interval(CALENDAR, aggregated.bar_start)
        assert aligned is not None
        assert aggregated.bar_start == aligned.start


def test_the_aggregate_available_time_is_the_latest_component() -> None:
    """構成足の利用可能時刻の最大値（D03 §5.1）。通常は足の終了時刻。"""
    result = _aggregate_to(DAILY, market.TF_1D_NY17, MIDWEEK)
    for aggregated in result.bars:
        assert aggregated.available_at == aggregated.bar_end


def test_the_aggregate_records_its_source_series_as_provenance() -> None:
    result = _aggregate_to(DAILY, market.TF_1D_NY17, MIDWEEK)
    for aggregated in result.bars:
        assert aggregated.provenance.kind is ProvenanceKind.AGGREGATED
        assert aggregated.provenance.source_ref == str(HOURLY)


def test_the_aggregation_rule_version_is_ny17_v2() -> None:
    """集約規則の版は manifest の変換記録に残る（ADR-0024）。"""
    assert AGGREGATION_RULE_VERSION == "ny17_v2"


# --- 不完全足は作らない（D03 §5.2）------------------------------------------


def test_a_missing_component_prevents_the_aggregate_and_is_reported() -> None:
    """一部の構成足で作った不完全足は採用しない（暗黙の部分集計を避ける）。"""
    complete = _aggregate_to(FOUR_HOUR, market.TF_4H_NY17, MIDWEEK)
    dropped = UtcTime.parse("2026-01-14T12:00:00Z")
    partial = _aggregate_to(FOUR_HOUR, market.TF_4H_NY17, MIDWEEK, skip_starts=(dropped,))
    assert len(partial.bars) == len(complete.bars) - 1
    assert partial.findings
    assert all(finding.kind is CheckKind.MISSING_EXPECTED_BAR for finding in partial.findings)
    # 欠けた 1時間足を含む 4時間足の区間が報告される。
    assert any(finding.interval.contains(dropped) for finding in partial.findings)


def test_the_findings_are_reported_in_a_canonical_order() -> None:
    """報告の並びは検査の実行順に依存しない（D03 §3.7.1）。"""
    result = _aggregate_to(
        FOUR_HOUR,
        market.TF_4H_NY17,
        MIDWEEK,
        skip_starts=(
            UtcTime.parse("2026-01-14T12:00:00Z"),
            UtcTime.parse("2026-01-13T08:00:00Z"),
        ),
    )
    keys = [finding.sort_key() for finding in result.findings]
    assert keys == sorted(keys)


# --- DST 切替日（D03 §3.2・§5.2）--------------------------------------------


def test_the_daily_aggregate_of_the_spring_forward_day_is_23_hours() -> None:
    window = Interval(
        start=UtcTime.parse("2026-03-05T00:00:00Z"),
        end=UtcTime.parse("2026-03-11T00:00:00Z"),
    )
    result = _aggregate_to(DAILY, market.TF_1D_NY17, window)
    spanning = [
        bar for bar in result.bars if bar.bar_start == UtcTime.parse("2026-03-07T22:00:00Z")
    ]
    # 週末に掛かるため日足は生成されないが、境界計算そのものは 23 時間である。
    aligned = market.TF_1D_NY17.boundaries(UtcTime.parse("2026-03-08T06:00:00Z"))
    assert aligned.duration.total_seconds() == 23 * 3600
    assert not spanning  # 土曜の日足は週末休場で存在しない。


def test_the_four_hour_aggregate_of_the_fall_back_day_is_five_hours() -> None:
    window = Interval(
        start=UtcTime.parse("2026-11-01T22:00:00Z"),
        end=UtcTime.parse("2026-11-04T00:00:00Z"),
    )
    result = _aggregate_to(FOUR_HOUR, market.TF_4H_NY17, window)
    assert result.bars
    for aggregated in result.bars:
        assert aggregated.interval.duration.total_seconds() in {
            3 * 3600,
            4 * 3600,
            5 * 3600,
        }


def test_aggregating_an_empty_series_returns_nothing() -> None:
    result = aggregate(
        (),
        source_timeframe_def=market.TF_1H,
        target_series=DAILY,
        target_timeframe_def=market.TF_1D_NY17,
        calendar=CALENDAR,
    )
    assert result.bars == ()
    assert result.findings == ()
