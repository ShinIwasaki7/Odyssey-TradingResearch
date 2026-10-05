"""検証 5 点（D03 §14.7・§14.8）。

材料は録画した 2 本の時間ファイル（USDJPY 2020-11-30 の 00 時・01 時）と、00 時台に実証の値を
持つ人工の原データ。01 時台をデータ欠損として補充し、00 時を照合用の時間にする。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta

import pytest

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.refill_plan import RawBarIndex, build_plan
from odyssey_fx.marketdata.application.refill_validation import HourData, validate_refill
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import (
    ArchiveProvenance,
    DecodedTicks,
    FailureKind,
    FinalResult,
    HourKey,
    HourOutcome,
    RefillFilter,
    RefillPlan,
    Tick,
)
from odyssey_fx.marketdata.domain.refill_validation import (
    MismatchKind,
    NeighborSide,
    NeighborStatus,
    NotBuiltReason,
    RefillValidation,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.refill import (
    BI5_00H,
    BI5_01H,
    HOUR_00,
    HOUR_01,
    REFILL_CALENDAR,
    USDJPY_1H,
    USDJPY_15M,
    calendar_ref,
    decoded,
    gap_resolutions,
    manifest_for,
    probe_bar,
    provider_ref,
    raw_bars,
)
from tests.fixtures.synthetic import market

AT = UtcTime.parse("2026-10-01T00:00:00Z")
KEY_00 = HourKey(symbol=market.USDJPY, start=HOUR_00)
KEY_01 = HourKey(symbol=market.USDJPY, start=HOUR_01)
ORIGINALS = (USDJPY_15M, USDJPY_1H)


def _plan(raw: Mapping[SeriesId, Sequence[Bar]] | None = None) -> RefillPlan:
    return build_plan(
        manifest=manifest_for(gap_resolutions()),
        raw_bars=raw_bars() if raw is None else raw,
        calendar=REFILL_CALENDAR,
        calendar_ref=calendar_ref(),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        provider=provider_ref(),
        refill_filter=RefillFilter(),
    )


def _fetched(key: HourKey, ticks: DecodedTicks) -> HourData:
    settings = provider_ref().settings
    path = f"{key.archive_directory}/{settings.source_digest()}/{ticks.tick_digest}.json"
    final = FinalResult(
        hour=key,
        outcome=ticks.outcome,
        from_archive=True,
        tick_digest=ticks.tick_digest,
        tick_count=len(ticks.ticks),
        archive_file=path,
        url=None,
        fetched_at=None,
        http_status=None,
        response_sha256=None,
        attempts=0,
        failure=None,
        detail="",
        at=AT,
    )
    provenance = ArchiveProvenance(
        url=settings.url_for(key),
        fetched_at=AT,
        http_status=200,
        attempts=1,
        response_sha256="c" * 64,
        tick_digest=ticks.tick_digest,
        tick_count=len(ticks.ticks),
        provider_id=settings.id,
        provider_version=settings.version,
        provider_content_digest=provider_ref().content_digest,
        source_digest=settings.source_digest(),
    )
    return HourData(final=final, decoded=ticks, provenance=provenance, archive_path=path)


def _not_fetched(key: HourKey) -> HourData:
    final = FinalResult(
        hour=key,
        outcome=HourOutcome.NOT_FETCHED,
        from_archive=False,
        tick_digest=None,
        tick_count=None,
        archive_file=None,
        url="https://example.invalid",
        fetched_at=None,
        http_status=None,
        response_sha256=None,
        attempts=6,
        failure=FailureKind.TIMEOUT,
        detail="timed out",
        at=AT,
    )
    return HourData(final=final, decoded=None, provenance=None, archive_path=None)


def _validate(
    hours: Mapping[HourKey, HourData] | None = None,
    raw: Mapping[SeriesId, Sequence[Bar]] | None = None,
) -> RefillValidation:
    bars = raw_bars() if raw is None else raw
    plan = _plan(bars)
    data = (
        {KEY_00: _fetched(KEY_00, decoded(BI5_00H)), KEY_01: _fetched(KEY_01, decoded(BI5_01H))}
        if hours is None
        else hours
    )
    return validate_refill(
        plan=plan,
        hours=data,
        originals=ORIGINALS,
        raw=RawBarIndex.build(bars, INITIAL_ACCESS_BOUNDARIES),
        calendar=REFILL_CALENDAR,
    )


def test_the_recorded_hours_pass_and_build_only_the_target_bars() -> None:
    result = _validate()
    assert result.passed, result.failures
    assert [(str(bar.series), str(bar.bar_start)) for bar in result.built_bars] == [
        ("USDJPY/15m/bid", "2020-11-30T01:00:00Z"),
        ("USDJPY/15m/bid", "2020-11-30T01:15:00Z"),
        ("USDJPY/15m/bid", "2020-11-30T01:30:00Z"),
        ("USDJPY/15m/bid", "2020-11-30T01:45:00Z"),
        ("USDJPY/1h/bid", "2020-11-30T01:00:00Z"),
    ]
    for bar in result.built_bars:
        expected = probe_bar(bar.series.timeframe.id, str(bar.bar_start))
        assert (bar.open, bar.high, bar.low, bar.close) == (
            expected.open,
            expected.high,
            expected.low,
            expected.close,
        )
    # 00 時（照合用の時間）の原データ 15分足 4 本と 1時間足 1 本を照合し、すべて一致。
    assert (result.reconciled_count, result.matched_count) == (5, 5)
    assert all(item.consistent for item in result.hourly_consistency)
    assert result.out_of_range_tick_count == 0


def test_neighbors_are_recorded_with_the_review_flag() -> None:
    result = _validate()
    by_side = {(str(item.series), item.side): item for item in result.neighbors}
    before = by_side[("USDJPY/15m/bid", NeighborSide.BEFORE)]
    assert before.status is NeighborStatus.COMPARED
    # 直前の 00:45 の終値 103.880 と補充した 01:00 の始値 103.879 の差（-0.1 pip）。
    assert before.difference == decimal_from_str("-0.001")
    assert before.difference_pips == decimal_from_str("-0.1")
    assert not before.needs_review
    after = by_side[("USDJPY/15m/bid", NeighborSide.AFTER)]
    # 直後の 02:00 は人工の値（150 台）なので 10 pip を大きく超え「要確認」の印が付く。
    assert after.status is NeighborStatus.COMPARED
    assert after.needs_review
    assert after.crosses_closure is False


def _with_close(
    bars: dict[SeriesId, tuple[Bar, ...]],
    series: SeriesId,
    start: UtcTime,
    delta: str,
    *,
    kind: ProvenanceKind | None = None,
) -> None:
    """原データの 1 本の終値を `delta` だけ変える（出所も変えられる）。"""
    changed: list[Bar] = []
    for bar in bars[series]:
        if bar.bar_start == start:
            close = Price(bar.close.value + decimal_from_str(delta))
            bar = replace(
                bar,
                close=close,
                high=max(bar.high, close),
                low=min(bar.low, close),
                provenance=bar.provenance
                if kind is None
                else Provenance(kind=kind, source_ref=bar.provenance.source_ref),
            )
        changed.append(bar)
    bars[series] = tuple(changed)


def test_a_provider_correction_mismatch_fails_without_tolerance() -> None:
    """(ii) 原データの出所が提供元と同じ配信元（dukascopy）で、価格の桁の 1 単位の差は不合格。"""
    bars = raw_bars()
    _with_close(bars, USDJPY_15M, HOUR_00, "0.001", kind=ProvenanceKind.DUKASCOPY)
    result = _validate(raw=bars)
    assert not result.passed
    assert any("provider correction" in reason for reason in result.failures)
    [record] = [item for item in result.reconciled if not item.matched]
    assert record.mismatch is MismatchKind.PROVIDER_CORRECTION
    assert record.differences == (("close", decimal_from_str("-0.001")),)


def test_a_representation_error_below_the_price_scale_matches_after_rounding() -> None:
    """丸め（D03 §14.7 の v1.19 の 1）: 価格の桁より下の表現誤差だけの差は一致とする。"""
    bars = raw_bars()
    _with_close(bars, USDJPY_15M, HOUR_00, "0.0000000000000001")
    result = _validate(raw=bars)
    assert result.passed, result.failures
    assert (result.reconciled_count, result.matched_count) == (5, 5)
    assert result.rounding_matched_count == 1
    assert len(result.built_bars) == 5


def test_a_source_difference_chunk_is_not_written_and_not_failed() -> None:
    """(iii) 原データの出所が histdata で 1 時間前後の足とも合わない差は、その塊を補充しない。"""
    bars = raw_bars()
    _with_close(bars, USDJPY_15M, HOUR_00, "0.002")
    result = _validate(raw=bars)
    [record] = [item for item in result.reconciled if not item.matched]
    assert record.mismatch is MismatchKind.SOURCE_DIFFERENCE
    # 塊（01 時の対象の時間と 00 時の照合用の時間）の対象足は 15分足・1時間足とも作らない。
    assert result.built_bars == ()
    assert {item.reason for item in result.not_built} == {NotBuiltReason.SOURCE_DIFFERENCE}
    assert [
        (str(item.chunk.series), item.chunk.target_count, item.mismatch_count)
        for item in result.source_differences
    ] == [("USDJPY/15m/bid", 4, 1), ("USDJPY/1h/bid", 1, 1)]
    assert {item.max_difference for item in result.source_differences} == {
        decimal_from_str("0.002")
    }
    assert {item.max_difference_pips for item in result.source_differences} == {
        decimal_from_str("0.2")
    }
    # 不合格の理由は「補充した足が 0 本」だけで、照合の不一致そのものは不合格にしない。
    assert all("no bar was built" in reason for reason in result.failures)


def test_a_suspected_time_shift_fails() -> None:
    """(i) 照合用の足が原データの 1 時間前のラベルの足と一致するなら、補正規則を疑って不合格。"""
    bars = raw_bars()
    probe = probe_bar("15m", "2020-11-30T00:00:00Z")
    earlier = HOUR_00 - timedelta(hours=1)
    bars[USDJPY_15M] = tuple(
        replace(bar, open=probe.open, high=probe.high, low=probe.low, close=probe.close)
        if bar.bar_start == earlier
        else bar
        for bar in bars[USDJPY_15M]
    )
    _with_close(bars, USDJPY_15M, HOUR_00, "0.005")
    result = _validate(raw=bars)
    [record] = [item for item in result.reconciled if not item.matched]
    assert record.mismatch is MismatchKind.TIME_SHIFT
    assert not result.passed
    assert any("time shift" in reason for reason in result.failures)


def test_hours_without_ticks_build_no_bars() -> None:
    result = _validate(
        {KEY_00: _fetched(KEY_00, decoded(BI5_00H)), KEY_01: _fetched(KEY_01, decoded(b""))}
    )
    assert not result.passed  # 補充した足が 0 本
    assert {item.reason for item in result.not_built} == {NotBuiltReason.PROVIDER_EMPTY}


def test_a_target_hour_not_fetched_is_recorded_with_its_reason() -> None:
    result = _validate({KEY_00: _fetched(KEY_00, decoded(BI5_00H)), KEY_01: _not_fetched(KEY_01)})
    assert {item.reason for item in result.not_built} == {NotBuiltReason.HOUR_NOT_FETCHED}
    assert {item.detail for item in result.not_built} == {"TIMEOUT"}


def test_a_bar_without_ticks_in_its_interval_is_not_built() -> None:
    first_quarter = DecodedTicks(
        tick_digest="d" * 64,
        ticks=tuple(tick for tick in decoded(BI5_01H).ticks if tick.offset_ms < 15 * 60 * 1000),
    )
    result = _validate(
        {KEY_00: _fetched(KEY_00, decoded(BI5_00H)), KEY_01: _fetched(KEY_01, first_quarter)}
    )
    assert result.passed, result.failures
    reasons = {(str(item.series), str(item.start)): item.reason for item in result.not_built}
    assert reasons[("USDJPY/15m/bid", "2020-11-30T01:15:00Z")] is NotBuiltReason.NO_TICK_IN_BAR
    assert len(result.built_bars) == 2  # 01:00 の 15分足と 1時間足


def test_a_lost_reference_hour_fails_instead_of_writing_unverified_bars() -> None:
    result = _validate({KEY_00: _not_fetched(KEY_00), KEY_01: _fetched(KEY_01, decoded(BI5_01H))})
    assert not result.passed
    assert any("could be compared" in reason for reason in result.failures)


def test_an_unreconciled_chunk_is_not_written_and_not_failed_alone() -> None:
    # 00 時台も 23 時台も欠かせると、01 時の塊の 24 時間以内に完全な時間が無くなる。
    window_hours = [HOUR_00 - timedelta(hours=back) for back in range(0, 5)]
    bars = raw_bars(drop_hours=[HOUR_01, *window_hours])
    plan = _plan(bars)
    assert [hour.reference for hour in plan.hours] == [False]
    result = validate_refill(
        plan=plan,
        hours={KEY_01: _fetched(KEY_01, decoded(BI5_01H))},
        originals=ORIGINALS,
        raw=RawBarIndex.build(bars, INITIAL_ACCESS_BOUNDARIES),
        calendar=REFILL_CALENDAR,
    )
    assert {item.reason for item in result.not_built} == {NotBuiltReason.UNRECONCILED}
    assert [(str(item.series), item.target_count) for item in result.unreconciled] == [
        ("USDJPY/15m/bid", 4),
        ("USDJPY/1h/bid", 1),
    ]
    assert result.built_bars == ()
    assert not result.passed  # 補充した足が 0 本なので空の補充分にはしない


def test_ticks_outside_their_hour_fail_validation() -> None:
    broken = DecodedTicks(
        tick_digest="e" * 64,
        ticks=(*decoded(BI5_01H).ticks, Tick(offset_ms=3_600_000, ask_raw=1, bid_raw=1)),
    )
    result = _validate(
        {KEY_00: _fetched(KEY_00, decoded(BI5_00H)), KEY_01: _fetched(KEY_01, broken)}
    )
    assert result.out_of_range_tick_count == 1
    assert not result.passed


def test_bid_above_ask_is_counted_but_not_failed() -> None:
    swapped = DecodedTicks(
        tick_digest="f" * 64,
        ticks=tuple(
            Tick(offset_ms=tick.offset_ms, ask_raw=tick.bid_raw - 1, bid_raw=tick.bid_raw)
            for tick in decoded(BI5_01H).ticks
        ),
    )
    result = _validate(
        {KEY_00: _fetched(KEY_00, decoded(BI5_00H)), KEY_01: _fetched(KEY_01, swapped)}
    )
    assert result.bid_above_ask_count == len(swapped.ticks)
    assert result.passed, result.failures


def test_a_built_bar_without_provenance_fails() -> None:
    data = _fetched(KEY_01, decoded(BI5_01H))
    stripped = HourData(final=data.final, decoded=data.decoded, provenance=None, archive_path=None)
    result = _validate({KEY_00: _fetched(KEY_00, decoded(BI5_00H)), KEY_01: stripped})
    assert any("no provenance" in reason for reason in result.failures)


def test_a_bar_already_in_the_raw_data_is_a_duplicate() -> None:
    """検証 4: 補充した足の開始時刻が原データにあれば不合格（対象足だけを作るので起きない
    はずだが確かめる）。原データの開始時刻の引き当てにだけ 01:00 の 1時間足を足す。"""
    bars = raw_bars()
    index = RawBarIndex.build(bars, INITIAL_ACCESS_BOUNDARIES)
    starts = dict(index.starts)
    starts[USDJPY_1H] = tuple(sorted((*starts[USDJPY_1H], HOUR_01), key=lambda item: item.value))
    tampered = replace(
        index,
        starts=starts,
        _all_starts={
            series: tuple(item.value for item in items) for series, items in starts.items()
        },
    )
    result = validate_refill(
        plan=_plan(bars),
        hours={
            KEY_00: _fetched(KEY_00, decoded(BI5_00H)),
            KEY_01: _fetched(KEY_01, decoded(BI5_01H)),
        },
        originals=ORIGINALS,
        raw=tampered,
        calendar=REFILL_CALENDAR,
    )
    assert any("already has this bar" in reason for reason in result.failures)


def test_a_reference_hour_the_raw_data_does_not_need_is_rejected() -> None:
    bars = raw_bars()
    plan = _plan(bars)
    with_bar = dict(bars)
    with_bar[USDJPY_15M] = (*bars[USDJPY_15M], probe_bar("15m", "2020-11-30T01:15:00Z"))
    with pytest.raises(MarketDataValueError, match="no chunk needs"):
        validate_refill(
            plan=plan,
            hours={
                KEY_00: _fetched(KEY_00, decoded(BI5_00H)),
                KEY_01: _fetched(KEY_01, decoded(BI5_01H)),
            },
            originals=ORIGINALS,
            raw=RawBarIndex.build(with_bar, INITIAL_ACCESS_BOUNDARIES),
            calendar=REFILL_CALENDAR,
        )
