"""両側照合と照合の方法の記録（D03 v1.22 §14.4.1・§14.7.1・§14.10・§14.11・§14.15 の書式の 2）。

場面（USDJPY。人工の tick から作る。通信しない）: 金曜 2020-11-27 の週の閉場（22:00Z）から日曜
2020-11-29 の週の開場（22:00Z）を挟み、日曜の開場直後の 22 時・23 時台をデータ欠損とする（D03
§14.4.1 の 60 塊と同じ形）。塊の時間ファイルにも直前 24 時間以内にも照合できる足が無いので、
前側（金曜 21 時台）と後側（月曜 00 時台）の両側で照合する。原データの足は同じ人工の tick から作り、
出所だけを `histdata` にする（照合用の足が一致する）。価格の差・出所を書き換えて、配信元の値の差・
提供元の訂正の疑いを作る。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.refill_aggregation import bar_from_ticks
from odyssey_fx.marketdata.application.refill_finalize import (
    build_refill_output,
    failure_details,
    validation_payload,
)
from odyssey_fx.marketdata.application.refill_inventory import VerifiedRefill, rejection_of
from odyssey_fx.marketdata.application.refill_plan import RawBarIndex, build_plan, plan_layout
from odyssey_fx.marketdata.application.refill_validation import HourData, validate_refill
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES, AccessBoundaries
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import (
    PLAN_FORMAT,
    PLAN_FORMAT_V1,
    ArchiveProvenance,
    DecodedTicks,
    FailureKind,
    FinalResult,
    HourKey,
    HourOutcome,
    ReconciliationMethod,
    ReconciliationSide,
    RefillFilter,
    RefillPlan,
    SideRecord,
    SideResult,
    SideUnavailable,
    Tick,
    ValidationRecord,
)
from odyssey_fx.marketdata.domain.refill_manifest import (
    RefillManifest,
    RefillValidationRecord,
)
from odyssey_fx.marketdata.domain.refill_validation import (
    MismatchKind,
    NotBuiltReason,
    RefillValidation,
    UnreconciledCause,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.refill import (
    USDJPY_1H,
    USDJPY_15M,
    calendar_ref,
    gap_resolutions,
    manifest_for,
    provider_ref,
)
from tests.fixtures.synthetic import market
from tools.ops import refill_report

H = timedelta(hours=1)
AT = UtcTime.parse("2026-10-13T00:00:00Z")
CALENDAR = market.calendar(version=2)
ORIGINALS = (USDJPY_15M, USDJPY_1H)

FRI_21 = UtcTime.parse("2020-11-27T21:00:00Z")  # 金曜の週の閉場の直前の時間（NY 16:00 EST）
SUN_22 = UtcTime.parse("2020-11-29T22:00:00Z")  # 日曜の週の開場（NY 17:00 EST）
SUN_23 = SUN_22 + H
MON_00 = UtcTime.parse("2020-11-30T00:00:00Z")
GAP = (SUN_22, SUN_23)
WINDOW = Interval(
    start=UtcTime.parse("2020-11-27T15:00:00Z"), end=UtcTime.parse("2020-11-30T06:00:00Z")
)


def ticks_for(hour: UtcTime) -> DecodedTicks:
    """時間ごとの人工の tick（5 分ごとに 1 件。15分足 1 本に 3 件）。"""
    base = 104_000 + (hour.value.hour * 37 + hour.value.day * 11) % 500
    ticks = tuple(
        Tick(offset_ms=index * 300_000 + 1_000, ask_raw=base + index + 5, bid_raw=base + index % 7)
        for index in range(12)
    )
    digest = hashlib.sha256(repr((str(hour), ticks)).encode()).hexdigest()
    return DecodedTicks(tick_digest=digest, ticks=ticks)


def raw_bars(drop: Sequence[UtcTime] = GAP) -> dict[SeriesId, tuple[Bar, ...]]:
    """原データの代わり（人工の tick から作り、出所を histdata にした足）。`drop` は落とす時間。"""
    quote = provider_ref().settings.symbol(market.USDJPY)
    dropped = set(drop)
    result: dict[SeriesId, tuple[Bar, ...]] = {}
    for series, definition in ((USDJPY_15M, market.TF_15M), (USDJPY_1H, market.TF_1H)):
        bars: list[Bar] = []
        for template in market.make_bars(series, definition, CALENDAR, WINDOW):
            hour = UtcTime(template.bar_start.value.replace(minute=0))
            if hour in dropped:
                continue
            made = bar_from_ticks(
                series=series,
                interval=template.interval,
                hour=HourKey(symbol=market.USDJPY, start=hour),
                ticks=ticks_for(hour).ticks,
                quote=quote,
                source_ref="synthetic",
            )
            assert made is not None
            bars.append(
                replace(
                    made,
                    provenance=Provenance(
                        kind=ProvenanceKind.HISTDATA,
                        source_ref=f"data/raw/market/USDJPY_{definition.ref.id}_merged.csv",
                    ),
                )
            )
        result[series] = tuple(bars)
    return result


def bump(
    raw: dict[SeriesId, tuple[Bar, ...]],
    series: SeriesId,
    start: UtcTime,
    *,
    kind: ProvenanceKind = ProvenanceKind.HISTDATA,
) -> dict[SeriesId, tuple[Bar, ...]]:
    """原データの足 1 本の高値を価格の桁の 1 単位（0.001）上げ、出所を `kind` にする。"""
    changed = dict(raw)
    changed[series] = tuple(
        replace(
            bar,
            high=Price(bar.high.value + decimal_from_str("0.001")),
            provenance=Provenance(kind=kind, source_ref=bar.provenance.source_ref),
        )
        if bar.bar_start == start
        else bar
        for bar in raw[series]
    )
    return changed


def plan_for(
    raw: Mapping[SeriesId, Sequence[Bar]],
    *,
    boundaries: AccessBoundaries = INITIAL_ACCESS_BOUNDARIES,
    gap: Sequence[UtcTime] = GAP,
) -> RefillPlan:
    return build_plan(
        manifest=manifest_for(gap_resolutions(hours=gap)),
        raw_bars=raw,
        calendar=CALENDAR,
        calendar_ref=calendar_ref(CALENDAR),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=boundaries,
        provider=provider_ref(),
        refill_filter=RefillFilter(),
    )


def fetched(key: HourKey) -> HourData:
    ticks = ticks_for(key.start)
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


def not_fetched(key: HourKey) -> HourData:
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


def validate(
    plan: RefillPlan,
    raw: Mapping[SeriesId, Sequence[Bar]],
    *,
    missing: Sequence[UtcTime] = (),
    boundaries: AccessBoundaries = INITIAL_ACCESS_BOUNDARIES,
) -> tuple[RefillValidation, dict[HourKey, HourData]]:
    hours = {
        key: not_fetched(key) if key.start in missing else fetched(key) for key in plan.hour_keys
    }
    result = validate_refill(
        plan=plan,
        hours=hours,
        originals=ORIGINALS,
        raw=RawBarIndex.build(raw, boundaries),
        calendar=CALENDAR,
    )
    return result, hours


def key(moment: UtcTime) -> HourKey:
    return HourKey(symbol=market.USDJPY, start=moment)


# --- 取引日（D03 §3.4.1・§14.4.1 の規則の 2）---------------------------------------


def test_the_trading_day_is_cut_at_the_new_york_17_oclock_boundary() -> None:
    friday = CALENDAR.trading_day_of(FRI_21)
    assert (str(friday.start), str(friday.end)) == (
        "2020-11-26T22:00:00Z",
        "2020-11-27T22:00:00Z",
    )
    monday = CALENDAR.trading_day_of(MON_00)
    assert (str(monday.start), str(monday.end)) == ("2020-11-29T22:00:00Z", "2020-11-30T22:00:00Z")
    # 夏時間の始まりの日曜（2019-03-10。USDJPY の B の後側）: 境界は 21:00Z に移る。
    spring = CALENDAR.trading_day_of(UtcTime.parse("2019-03-10T22:00:00Z"))
    assert (str(spring.start), str(spring.end)) == (
        "2019-03-10T21:00:00Z",
        "2019-03-11T21:00:00Z",
    )
    before = CALENDAR.trading_day_of(UtcTime.parse("2019-03-08T21:00:00Z"))
    assert (str(before.start), str(before.end)) == ("2019-03-07T22:00:00Z", "2019-03-08T22:00:00Z")


# --- 計画（D03 §14.4.1・§14.7.1 の記録先 1・§14.10）-----------------------------------


def test_a_sunday_open_chunk_gets_both_sides_with_their_distances() -> None:
    plan = plan_for(raw_bars())
    assert not plan.is_legacy and plan.format == PLAN_FORMAT
    assert [(str(hour.hour.start), hour.reference) for hour in plan.hours] == [
        (str(FRI_21), True),
        (str(SUN_22), False),
        (str(SUN_23), False),
        (str(MON_00), True),
    ]
    assert plan.reconciliations is not None
    assert [(str(r.series), str(r.chunk_start), r.method) for r in plan.reconciliations] == [
        ("USDJPY/15m/bid", str(SUN_22), ReconciliationMethod.BILATERAL),
        ("USDJPY/1h/bid", str(SUN_22), ReconciliationMethod.BILATERAL),
    ]
    before, after = plan.reconciliations[0].sides
    # 前側は金曜の週の閉場の直前の時間。距離は照合用の時間の終わり（金曜 22:00Z）から塊の始まり
    # （日曜 22:00Z）までの 48 時間。後側は塊の直後の時間で距離 0。
    assert before == SideRecord(
        side=ReconciliationSide.BEFORE, reference_start=FRI_21, distance_minutes=48 * 60
    )
    assert after == SideRecord(
        side=ReconciliationSide.AFTER, reference_start=MON_00, distance_minutes=0
    )
    roles = {str(hour.hour.start): hour.roles for hour in plan.hours}
    assert roles[str(SUN_22)] == ()
    assert [(str(role.series), role.side) for role in roles[str(FRI_21)] or ()] == [
        ("USDJPY/15m/bid", ReconciliationSide.BEFORE),
        ("USDJPY/1h/bid", ReconciliationSide.BEFORE),
    ]


def test_the_plan_round_trips_and_its_id_covers_the_records() -> None:
    plan = plan_for(raw_bars())
    payload = plan.identity_payload()
    assert payload["format"] == PLAN_FORMAT
    assert RefillPlan.from_payload(payload) == plan
    assert RefillPlan.from_payload(payload).plan_id() == plan.plan_id()
    # 照合の方法の記録は plan_id の対象（R-1b。規則を変えた計画は別の識別子になる）。
    altered = dict(payload)
    altered["reconciliations"] = [
        {**entry, "method": "PRECEDING_24H", "sides": []} for entry in payload["reconciliations"]
    ]
    with pytest.raises(MarketDataValueError):
        RefillPlan.from_payload(altered)


def test_a_plan_of_the_v1_22_rules_without_its_records_is_a_structural_error() -> None:
    payload = dict(plan_for(raw_bars()).identity_payload())
    without = {name: value for name, value in payload.items() if name != "reconciliations"}
    with pytest.raises(MarketDataValueError):
        RefillPlan.from_payload(without)
    with pytest.raises(MarketDataValueError, match="required"):
        RefillPlan.from_payload({**payload, "reconciliations": None})


def test_a_plan_made_before_v1_22_keeps_its_id_and_is_read_without_records() -> None:
    raw = raw_bars()
    plan = plan_for(raw)
    layout = plan_layout(
        plan.target_bars,
        ORIGINALS,
        RawBarIndex.build(raw, INITIAL_ACCESS_BOUNDARIES),
        CALENDAR,
        bilateral=False,
    )
    legacy = RefillPlan(
        snapshot_id=plan.snapshot_id,
        calendar=plan.calendar,
        provider=plan.provider,
        filter=plan.filter,
        target_bars=plan.target_bars,
        hours=layout.hours,
    )
    payload = legacy.identity_payload()
    assert payload["format"] == PLAN_FORMAT_V1
    assert "reconciliations" not in payload
    assert all("roles" not in hour for hour in payload["hours"])
    # 記録された中身のまま計算し直す（無い項目を補わない。PR #78 の仮置き 7 の決定）。
    read = RefillPlan.from_payload(payload)
    assert read.is_legacy and read.plan_id() == legacy.plan_id()
    # 前の規則では照合用の時間が無いので、その塊は「未照合（照合できる足が無い）」のまま。
    result, _ = validate(legacy, raw)
    assert result.reconciliations is None
    assert [item.causes for item in result.unreconciled] == [
        (UnreconciledCause.NO_RECONCILIATION_BAR,),
        (UnreconciledCause.NO_RECONCILIATION_BAR,),
    ]


def test_an_incomplete_origin_hour_steps_back_within_the_trading_day() -> None:
    """R-1d: 起点の時間（金曜 21 時台）がそろわなければ、取引日の中で前にそろう時間まで遡る。"""
    raw = raw_bars()
    raw[USDJPY_15M] = tuple(
        bar for bar in raw[USDJPY_15M] if bar.bar_start != FRI_21 + timedelta(minutes=45)
    )
    plan = plan_for(raw)
    assert plan.reconciliations is not None
    before, _ = plan.reconciliations[0].sides
    assert before.reference_start == FRI_21 - H
    assert before.distance_minutes == 49 * 60


def test_no_complete_hour_within_the_trading_day_makes_the_side_unavailable() -> None:
    """取引日の中にそろう時間が無ければ、その側は取得不能（次の取引日へは探さない）。"""
    raw = raw_bars()
    monday = CALENDAR.trading_day_of(MON_00)
    raw[USDJPY_1H] = tuple(bar for bar in raw[USDJPY_1H] if not monday.contains(bar.bar_start))
    plan = plan_for(raw)
    assert plan.reconciliations is not None
    _, after = plan.reconciliations[0].sides
    assert after == SideRecord(
        side=ReconciliationSide.AFTER,
        reference_start=None,
        distance_minutes=None,
        unavailable=SideUnavailable.NO_COMPLETE_HOUR,
    )
    assert MON_00 not in [hour.hour.start for hour in plan.hours]
    result, _ = validate(plan, raw)
    assert [item.causes for item in result.unreconciled] == [
        (UnreconciledCause.BILATERAL_SIDE_UNAVAILABLE,),
        (UnreconciledCause.BILATERAL_SIDE_UNAVAILABLE,),
    ]
    sides = result.unreconciled[0].sides
    assert [side.result for side in sides] == [SideResult.MATCHED, SideResult.UNAVAILABLE]
    assert sides[1].unavailable is SideUnavailable.NO_COMPLETE_HOUR


def test_a_side_whose_origin_lies_outside_research_history_is_unavailable() -> None:
    boundaries = AccessBoundaries(
        research_until=UtcTime.parse("2020-11-29T23:00:01Z"),
        holdout_until=UtcTime.parse("2026-01-01T00:00:00Z"),
    )
    raw = raw_bars(drop=(SUN_22,))
    plan = plan_for(raw, boundaries=boundaries, gap=(SUN_22,))
    assert plan.reconciliations is not None
    _, after = plan.reconciliations[0].sides
    assert after.unavailable is SideUnavailable.OUTSIDE_RESEARCH_HISTORY
    assert after.reference_start is None


# --- 検証（D03 §14.4.1 の規則の 3・§14.7.1）-------------------------------------------


def test_both_sides_matching_refill_the_chunk() -> None:
    raw = raw_bars()
    plan = plan_for(raw)
    result, _ = validate(plan, raw)
    assert result.passed, result.failures
    assert len(result.built_bars) == 10  # 15分足 8 本・1時間足 2 本
    assert (result.reconciled_count, result.matched_count) == (10, 10)
    assert result.unreconciled == () and result.source_differences == ()
    assert result.reconciliations is not None
    for record in result.reconciliations:
        assert record.side_results() == (SideResult.MATCHED, SideResult.MATCHED)
        assert record.planned() in (plan.reconciliations or ())


def test_one_side_with_a_source_difference_does_not_refill() -> None:
    raw = bump(raw_bars(), USDJPY_15M, MON_00)
    plan = plan_for(raw)
    result, _ = validate(plan, raw)
    assert result.built_bars == ()
    assert {item.reason for item in result.not_built} == {NotBuiltReason.SOURCE_DIFFERENCE}
    assert [str(item.chunk.series) for item in result.source_differences] == [
        "USDJPY/15m/bid",
        "USDJPY/1h/bid",
    ]
    assert result.unreconciled == ()
    assert result.reconciliations is not None
    assert result.reconciliations[0].side_results() == (
        SideResult.MATCHED,
        SideResult.SOURCE_DIFFERENCE,
    )
    assert not result.passed  # 補充した足が 0 本


def test_an_unavailable_side_beside_a_source_difference_is_one_unreconciled_state() -> None:
    """R-1e: 片側取得不能・片側配信元の値の差は「未照合」1 つで、原因に両側を書く。"""
    raw = bump(raw_bars(), USDJPY_15M, FRI_21)
    plan = plan_for(raw)
    result, _ = validate(plan, raw, missing=(MON_00,))
    assert result.source_differences == ()
    assert [item.causes for item in result.unreconciled] == [
        (UnreconciledCause.SOURCE_DIFFERENCE, UnreconciledCause.BILATERAL_SIDE_UNAVAILABLE),
        (UnreconciledCause.SOURCE_DIFFERENCE, UnreconciledCause.BILATERAL_SIDE_UNAVAILABLE),
    ]
    before, after = result.unreconciled[0].sides
    assert before.result is SideResult.SOURCE_DIFFERENCE
    assert (after.result, after.unavailable) == (
        SideResult.UNAVAILABLE,
        SideUnavailable.HOUR_NOT_FETCHED,
    )
    assert {item.reason for item in result.not_built} == {NotBuiltReason.UNRECONCILED}
    # 不一致の足と差は照合の記録に残る（決定 A と同じ）。
    assert any(item.mismatch is MismatchKind.SOURCE_DIFFERENCE for item in result.reconciled)


def test_a_provider_correction_on_one_side_fails_regardless_of_the_other() -> None:
    raw = bump(raw_bars(), USDJPY_15M, FRI_21, kind=ProvenanceKind.DUKASCOPY)
    plan = plan_for(raw)
    result, _ = validate(plan, raw, missing=(MON_00,))
    assert not result.passed
    assert any("provider correction" in reason for reason in result.failures)
    assert result.reconciliations is not None
    assert result.reconciliations[0].sides[0].result is SideResult.MISMATCH


def test_a_side_whose_bars_cannot_be_built_is_unavailable() -> None:
    raw = raw_bars()
    plan = plan_for(raw)
    hours = {key_: fetched(key_) for key_ in plan.hour_keys}
    empty = DecodedTicks(tick_digest="e" * 64, ticks=())
    hours[key(MON_00)] = replace(
        hours[key(MON_00)],
        decoded=empty,
        final=replace(hours[key(MON_00)].final, outcome=HourOutcome.FETCHED_EMPTY, tick_count=0),
    )
    result = validate_refill(
        plan=plan,
        hours=hours,
        originals=ORIGINALS,
        raw=RawBarIndex.build(raw, INITIAL_ACCESS_BOUNDARIES),
        calendar=CALENDAR,
    )
    causes = result.unreconciled[0].causes
    assert causes == (
        UnreconciledCause.RECONCILIATION_NOT_BUILT,
        UnreconciledCause.BILATERAL_SIDE_UNAVAILABLE,
    )
    assert result.unreconciled[0].sides[1].unavailable is SideUnavailable.RECONCILIATION_NOT_BUILT
    assert len(result.unreconciled[0].unbuilt_reconciliation_bars) == 5


def test_the_validation_rejects_a_plan_whose_records_differ_from_the_raw_data() -> None:
    raw = raw_bars()
    plan = plan_for(raw)
    changed = dict(raw)
    changed[USDJPY_15M] = tuple(
        bar for bar in raw[USDJPY_15M] if bar.bar_start != FRI_21 + timedelta(minutes=45)
    )
    with pytest.raises(MarketDataValueError, match="differ from the plan"):
        validate(plan, changed)


# --- 補充の manifest・検証記録・取得記録の行（D03 §14.7.1 の記録先 2・§14.11）-------------


def _output() -> tuple[RefillPlan, RefillValidation, RefillManifest]:
    raw = raw_bars()
    plan = plan_for(raw)
    result, hours = validate(plan, raw)
    output = build_refill_output(
        plan=plan, validation=result, hours=hours, code_version="test", created_at=AT
    )
    return plan, result, output.manifest


def test_the_manifest_and_validation_record_carry_the_chunk_records() -> None:
    plan, result, manifest = _output()
    payload = manifest.payload()
    assert payload["format"] == "refill_manifest_v3"
    read = RefillManifest.from_payload(payload)
    assert read.reconciliations == result.reconciliations
    record = RefillValidationRecord.from_payload(
        validation_payload(result, refill_id=manifest.refill_id, plan_id=plan.plan_id())
    )
    assert record.reconciliations == result.reconciliations
    side = payload["reconciliations"][0]["sides"][0]
    assert side == {
        "distance_minutes": 2880,
        "reference_start": str(FRI_21),
        "result": "MATCHED",
        "side": "BEFORE",
        "unavailable": None,
    }


def test_a_manifest_of_a_v1_22_plan_without_its_records_is_a_structural_error() -> None:
    _, _, manifest = _output()
    payload = dict(manifest.payload())
    with pytest.raises(MarketDataValueError, match="required"):
        RefillManifest.from_payload({**payload, "reconciliations": None})
    without = {name: value for name, value in payload.items() if name != "reconciliations"}
    with pytest.raises(MarketDataValueError):
        RefillManifest.from_payload(without)


def test_the_failed_validation_line_keeps_the_chunk_records() -> None:
    raw = bump(raw_bars(), USDJPY_15M, FRI_21)
    plan = plan_for(raw)
    result, _ = validate(plan, raw, missing=(MON_00,))
    assert not result.passed
    record = ValidationRecord(
        passed=False, reasons=result.failures, at=AT, details=failure_details(result)
    )
    rejection = rejection_of(plan, 1, record)
    assert rejection.reconciliations == result.reconciliations
    assert rejection.unreconciled == result.unreconciled
    stripped = dict(record.details)
    stripped["reconciliations"] = None
    with pytest.raises(MarketDataValueError, match="exactly for a plan of the v1.22 rules"):
        rejection_of(plan, 1, replace(record, details=stripped))


# --- 報告（D03 §14.15 の書式の 2）------------------------------------------------------


def test_the_report_lists_chunks_by_method_with_both_sides() -> None:
    plan, result, manifest = _output()
    validation = RefillValidationRecord.from_payload(
        validation_payload(result, refill_id=manifest.refill_id, plan_id=plan.plan_id())
    )
    lines = refill_report._method_lines(
        [VerifiedRefill(manifest=manifest, validation=validation)], [], {}
    )
    text = "\n".join(lines)
    assert f"| 補充分 `{manifest.refill_id[:12]}…` | 両側照合 | 2 | 0 |" in text
    assert "前側 2020-11-27T21:00:00Z 結果 MATCHED 距離 48 時間" in text
    assert "後側 2020-11-30T00:00:00Z 結果 MATCHED 距離 0 時間" in text


def test_the_report_shows_records_before_v1_22_as_a_separate_class() -> None:
    # v1.22 より前の補充分（照合の記録を持たない）の形だけを渡す（検算は manifest 側の試験）。
    legacy = SimpleNamespace(
        manifest=SimpleNamespace(
            refill_id="a" * 64,
            plan_id="b" * 64,
            reconciliations=None,
            plan=None,
            not_built=(),
            unreconciled=(),
            source_differences=(),
        )
    )
    lines = refill_report._method_lines([cast(VerifiedRefill, legacy)], [], {})
    assert any(
        f"| 補充分 `{'a' * 12}…` | {refill_report.NO_METHOD_LABEL} |" in line for line in lines
    )


def test_a_chunk_without_any_built_bar_is_not_counted_as_refilled() -> None:
    """両側とも一致しても、対象の時間を取得できず足を 1 本も作らなかった塊は「補充しなかった」。"""
    mon_02 = MON_00 + 2 * H
    raw = raw_bars(drop=(*GAP, mon_02))
    plan = plan_for(raw, gap=(*GAP, mon_02))
    result, hours = validate(plan, raw, missing=GAP)
    assert result.passed, result.failures
    output = build_refill_output(
        plan=plan, validation=result, hours=hours, code_version="test", created_at=AT
    )
    manifest = output.manifest
    validation = RefillValidationRecord.from_payload(
        validation_payload(result, refill_id=manifest.refill_id, plan_id=plan.plan_id())
    )
    text = "\n".join(
        refill_report._method_lines(
            [VerifiedRefill(manifest=manifest, validation=validation)], [], {}
        )
    )
    short = manifest.refill_id[:12]
    assert f"| 補充分 `{short}…` | 両側照合 | 0 | 2 |" in text
    assert f"| 補充分 `{short}…` | 直前 24 時間以内 | 2 | 0 |" in text
    assert "取得できなかった" in text


def test_an_unreconciled_bilateral_chunk_cannot_also_be_a_source_difference() -> None:
    """R-1e: 片側取得不能の塊は未照合の一覧だけに入る（配信元の値の差の一覧に同時に載せない）。"""
    mon_02 = MON_00 + 2 * H
    raw = raw_bars(drop=(*GAP, mon_02))
    plan = plan_for(raw, gap=(*GAP, mon_02))
    result, hours = validate(plan, raw, missing=(MON_00,))
    assert result.passed, result.failures
    payload = dict(
        build_refill_output(
            plan=plan, validation=result, hours=hours, code_version="test", created_at=AT
        ).manifest.payload()
    )
    assert RefillManifest.from_payload(payload).unreconciled
    tampered = {
        **payload,
        "source_differences": [
            {
                "chunk_start": str(SUN_22),
                "series": "USDJPY/15m/bid",
                "target_count": 8,
                "timeframe_version": USDJPY_15M.timeframe.version,
            }
        ],
    }
    with pytest.raises(MarketDataValueError):
        RefillManifest.from_payload(tampered)
