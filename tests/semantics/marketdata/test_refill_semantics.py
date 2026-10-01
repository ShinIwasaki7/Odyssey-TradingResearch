"""元データの再取得（補充）の意味論テスト（D08 §5 の #6〜#9、D03 §14）。

- #6 補充は架空の価格で埋めない: 区間に tick が 1 件も無い足・取得できなかった時間の足は
  作らない（D03 §14.6・§14.8）。
- #7 補充は対象足以外を書かない: 同じ時間ファイルから作れる対象でない足（照合用の時間・
  原データにある足）は照合にだけ使う（D03 §14.6）。
- #8 識別子の決定論: 同じ入力から同じ `plan_id`（入力の列挙順に依存しない。設定の中身が
  変われば変わる）（D03 §14.10）。`refill_id` は書き出しの実装（後続）で確かめる。
- #9 書き出しは「存在すれば失敗」: 作業ディレクトリ・`plan.json`・保管場所の 1 件を上書き
  しない（D03 §14.11・§14.11.1）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.adapters.refill_store import FsRefillStore
from odyssey_fx.marketdata.application.refill_plan import RawBarIndex, build_plan, create_plan
from odyssey_fx.marketdata.application.refill_validation import HourData, validate_refill
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.errors import RefillPlanAlreadyExists, RefillStoreInconsistent
from odyssey_fx.marketdata.domain.refill import (
    ArchiveProvenance,
    DecodedTicks,
    FailureKind,
    FinalResult,
    HourKey,
    HourOutcome,
    RefillFilter,
    RefillPlan,
    sha256_hex,
)
from odyssey_fx.marketdata.domain.refill_validation import RefillValidation
from tests.fixtures.refill import (
    BI5_00H,
    BI5_01H,
    HOUR_00,
    HOUR_01,
    REFILL_CALENDAR,
    USDJPY_1H,
    USDJPY_15M,
    calendar_ref,
    communication,
    decoded,
    gap_resolutions,
    manifest_for,
    provider_ref,
    raw_bars,
)
from tests.fixtures.synthetic import market

AT = UtcTime.parse("2026-10-01T00:00:00Z")
KEY_00 = HourKey(symbol=market.USDJPY, start=HOUR_00)
KEY_01 = HourKey(symbol=market.USDJPY, start=HOUR_01)


def _plan(*, reverse: bool = False, interval_seconds: int = 8) -> RefillPlan:
    resolved = gap_resolutions()
    return build_plan(
        manifest=manifest_for(tuple(reversed(resolved)) if reverse else resolved),
        raw_bars=raw_bars(),
        calendar=REFILL_CALENDAR,
        calendar_ref=calendar_ref(),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        provider=provider_ref(comm=communication(request_interval_seconds=interval_seconds)),
        refill_filter=RefillFilter(),
    )


def _hour(key: HourKey, ticks: DecodedTicks | None) -> HourData:
    settings = provider_ref().settings
    if ticks is None:
        final = FinalResult(
            hour=key,
            outcome=HourOutcome.NOT_FETCHED,
            from_archive=False,
            tick_digest=None,
            tick_count=None,
            archive_file=None,
            url=settings.url_for(key),
            fetched_at=None,
            http_status=None,
            response_sha256=None,
            attempts=6,
            failure=FailureKind.TIMEOUT,
            detail="",
            at=AT,
        )
        return HourData(final=final, decoded=None, provenance=None, archive_path=None)
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
        response_sha256=sha256_hex(b"x"),
        tick_digest=ticks.tick_digest,
        tick_count=len(ticks.ticks),
        provider_id=settings.id,
        provider_version=settings.version,
        provider_content_digest=provider_ref().content_digest,
        source_digest=settings.source_digest(),
    )
    return HourData(final=final, decoded=ticks, provenance=provenance, archive_path=path)


def _validate(hours: dict[HourKey, HourData]) -> RefillValidation:
    bars = raw_bars()
    return validate_refill(
        plan=_plan(),
        hours=hours,
        originals=(USDJPY_15M, USDJPY_1H),
        raw=RawBarIndex.build(bars, INITIAL_ACCESS_BOUNDARIES),
        calendar=REFILL_CALENDAR,
    )


# --- #6 架空の価格で埋めない ------------------------------------------------------------


def test_bars_without_ticks_are_left_missing_not_filled() -> None:
    """01:00〜01:15 の tick だけがある時間ファイルからは、その区間の足だけを作る。"""
    early = DecodedTicks(
        tick_digest="d" * 64,
        ticks=tuple(tick for tick in decoded(BI5_01H).ticks if tick.offset_ms < 15 * 60 * 1000),
    )
    result = _validate({KEY_00: _hour(KEY_00, decoded(BI5_00H)), KEY_01: _hour(KEY_01, early)})
    built = {(str(bar.series), str(bar.bar_start)) for bar in result.built_bars}
    assert built == {
        ("USDJPY/15m/bid", "2020-11-30T01:00:00Z"),
        ("USDJPY/1h/bid", "2020-11-30T01:00:00Z"),
    }
    # 1時間足は 01:00〜01:15 の tick だけから作られる（後ろの区間を推定で補わない）。
    hourly = next(bar for bar in result.built_bars if bar.series == USDJPY_1H)
    quarter = next(bar for bar in result.built_bars if bar.series == USDJPY_15M)
    assert (hourly.open, hourly.high, hourly.low, hourly.close) == (
        quarter.open,
        quarter.high,
        quarter.low,
        quarter.close,
    )


def test_an_hour_not_fetched_builds_no_bars() -> None:
    result = _validate({KEY_00: _hour(KEY_00, decoded(BI5_00H)), KEY_01: _hour(KEY_01, None)})
    assert result.built_bars == ()


# --- #7 対象足以外を書かない ------------------------------------------------------------


def test_only_target_bars_are_built_and_reference_bars_are_only_compared() -> None:
    plan = _plan()
    result = _validate(
        {KEY_00: _hour(KEY_00, decoded(BI5_00H)), KEY_01: _hour(KEY_01, decoded(BI5_01H))}
    )
    targets = {(bar.series, bar.start) for bar in plan.target_bars}
    built = {(bar.series, bar.bar_start) for bar in result.built_bars}
    assert built == targets
    # 照合用の時間（00 時）の足は照合にだけ使い、補充した足に入らない。
    assert all(record.hour == KEY_00 for record in result.reconciled)
    assert all(bar.bar_start >= HOUR_01 for bar in result.built_bars)


# --- #8 識別子の決定論 -------------------------------------------------------------------


def test_the_same_inputs_give_the_same_plan_id() -> None:
    assert _plan().plan_id() == _plan().plan_id()
    assert _plan().plan_id() == _plan(reverse=True).plan_id()


def test_a_changed_setting_gives_another_plan_id() -> None:
    assert _plan().plan_id() != _plan(interval_seconds=20).plan_id()


# --- #9 存在すれば失敗 -------------------------------------------------------------------


def test_refill_files_are_never_overwritten(tmp_path: Path) -> None:
    store = FsRefillStore(root=tmp_path / "refill")
    plan = _plan()
    plan_id = create_plan(plan, store)
    plan_json = tmp_path / "refill/_work" / plan_id / "plan.json"
    before = plan_json.read_bytes()
    with pytest.raises(RefillPlanAlreadyExists):
        create_plan(plan, store)
    with pytest.raises(RefillStoreInconsistent):
        store.write_plan(plan_id, {"tampered": True})
    assert plan_json.read_bytes() == before
    ticks = decoded(BI5_00H)
    settings = provider_ref().settings
    provenance = ArchiveProvenance(
        url=settings.url_for(KEY_00),
        fetched_at=AT,
        http_status=200,
        attempts=1,
        response_sha256=sha256_hex(BI5_00H),
        tick_digest=ticks.tick_digest,
        tick_count=len(ticks.ticks),
        provider_id=settings.id,
        provider_version=settings.version,
        provider_content_digest=provider_ref().content_digest,
        source_digest=settings.source_digest(),
    )
    path = store.write_archive(KEY_00, BI5_00H, provenance)
    archived = (tmp_path / "refill" / path).read_bytes()
    with pytest.raises(RefillStoreInconsistent):
        store.write_archive(KEY_00, BI5_00H, provenance)
    assert (tmp_path / "refill" / path).read_bytes() == archived
