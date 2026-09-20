"""二段階の受入れフローの単体テスト（D03 §3.7.1・§4 の 8〜9・§11）。

確かめること:

- 暫定段階では分類が空で、暫定の識別子が計算できる。
- 未分類の警告が残る状態では確定できない。
- 分類を記入すると識別子が変わり、分類の内容が違えば別の識別子になる。
- 受入れ実行時刻を変えても暫定・最終の識別子は変わらない。
"""

from __future__ import annotations

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.acceptance import (
    PendingSnapshot,
    build_pending_snapshot,
    finalize,
    provisional_id,
)
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.snapshot import ClosureDecision, ClosureDecisionKind
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
CALENDAR = market.calendar()
WINDOW = Interval(
    start=UtcTime.parse("2022-01-05T22:00:00Z"), end=UtcTime.parse("2022-01-07T22:00:00Z")
)
DROPPED = UtcTime.parse("2022-01-06T10:00:00Z")

RAW_FILE = snapshots.source("data/raw/market/USDJPY_1h_merged.csv")


def _build(created_at: UtcTime, *, skip_starts=(DROPPED,)) -> PendingSnapshot:  # type: ignore[no-untyped-def]
    from odyssey_fx.marketdata.application.acceptance import RawFile

    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=skip_starts)
    raw_file = RawFile(
        path=RAW_FILE.path,
        sha256=RAW_FILE.sha256,
        symbol=RAW_FILE.symbol,
        timeframe=RAW_FILE.timeframe,
        declared_basis=RAW_FILE.declared_basis,
    )
    # partition のダイジェストは adapters が返す値。テストでは内容から決まる固定値を使う。
    from odyssey_fx.marketdata.application.acceptance import classify_partitions
    from odyssey_fx.marketdata.domain.snapshot import PartitionId

    grouped = classify_partitions(bars, INITIAL_ACCESS_BOUNDARIES)
    digests = {
        PartitionId(series=HOURLY, access_class=access): snapshots.digest_for(access.value).hex
        for access in grouped
    }
    return build_pending_snapshot(
        created_at=created_at,
        raw_files=(raw_file,),
        bars_by_file={raw_file.path: bars},
        timeframe_defs=market.TIMEFRAME_DEFS,
        calendar=CALENDAR,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        basis_declaration=snapshots.BASIS,
        conversion=snapshots.CONVERSION,
        partition_digests=digests,
        integrity_report_digest=snapshots.REPORT_DIGEST,
    )


# --- 暫定段階（D03 §3.7.1 の 1）---------------------------------------------


def test_the_pending_snapshot_has_no_closure_decisions() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    assert pending.manifest.closure_decisions == ()
    assert pending.provisional_id == provisional_id(pending.manifest)


def test_the_pending_snapshot_reports_the_missing_bar() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    warnings = pending.report.warnings
    assert warnings
    assert any(result.interval.start == DROPPED for result in warnings)


def test_the_provisional_id_ignores_the_acceptance_time() -> None:
    morning = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    evening = _build(UtcTime.parse("2026-09-20T21:30:00Z"))
    assert morning.provisional_id == evening.provisional_id


def test_a_provisional_id_cannot_be_taken_after_the_decisions_are_recorded() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    decided = pending.manifest.with_closure_decisions(
        (
            ClosureDecision(
                series_id=HOURLY,
                interval=Interval(start=DROPPED, end=DROPPED + market.TF_1H.nominal_length),
                kind=ClosureDecisionKind.DATA_GAP,
            ),
        )
    )
    with pytest.raises(MarketDataValueError, match="already carries decisions"):
        provisional_id(decided)


# --- 確定段階（D03 §3.7.1 の 2、§4 の 9）-----------------------------------


def _decision(kind: ClosureDecisionKind) -> ClosureDecision:
    return ClosureDecision(
        series_id=HOURLY,
        interval=Interval(start=DROPPED, end=DROPPED + market.TF_1H.nominal_length),
        kind=kind,
    )


def test_an_unclassified_warning_blocks_the_finalization() -> None:
    """未分類の警告が残る場合は確定できない（D03 §10 の classify コマンド）。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    with pytest.raises(MarketDataValueError, match="still unclassified"):
        finalize(pending, ())


def test_recording_the_decision_yields_a_different_snapshot_id() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    final = finalize(pending, (_decision(ClosureDecisionKind.DATA_GAP),))
    assert final.snapshot_id() != pending.provisional_id


def test_classifying_the_gap_differently_yields_a_different_snapshot_id() -> None:
    """分類が異なれば別 snapshot である（D03 §3.7.1）。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    as_gap = finalize(pending, (_decision(ClosureDecisionKind.DATA_GAP),))
    as_closure = finalize(pending, (_decision(ClosureDecisionKind.CLOSURE),))
    assert as_gap.snapshot_id() != as_closure.snapshot_id()


def test_the_final_snapshot_id_ignores_the_acceptance_time() -> None:
    morning = finalize(
        _build(UtcTime.parse("2026-09-20T09:00:00Z")),
        (_decision(ClosureDecisionKind.DATA_GAP),),
    )
    evening = finalize(
        _build(UtcTime.parse("2026-09-20T21:30:00Z")),
        (_decision(ClosureDecisionKind.DATA_GAP),),
    )
    assert morning.snapshot_id() == evening.snapshot_id()


def test_a_clean_series_finalizes_without_decisions() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    assert pending.report.warnings == ()
    final = finalize(pending, ())
    assert final.snapshot_id() == pending.provisional_id


# --- partition の分割（D03 §4 の 7）-----------------------------------------


def test_the_pending_snapshot_records_one_partition_per_access_class() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    # 2022 年のデータなのですべて研究履歴に入る。
    assert len(pending.partition_bars) == 1
    (partition_id,) = pending.partition_bars
    assert partition_id.access_class.value == "RESEARCH_HISTORY"
    assert pending.manifest.partition_record(partition_id) is not None
