"""MT5 経路の受入れ（D03 §15.3・§15.6・§15.9、D12 §11.13 の行 2〜4）。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.mt5_acceptance import (
    Mt5AcceptanceFailed,
    Mt5FetchEntry,
    Mt5RawRow,
    Mt5ReadFile,
    reaccept_mt5,
    verify_fetch_files,
)
from odyssey_fx.marketdata.domain.access import AccessBoundaries
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant
from tests.fixtures.mt5 import market as m5
from tests.fixtures.synthetic import market

CALENDAR = market.calendar()
WEEK = Interval(
    start=UtcTime.parse("2026-01-04T22:00:00Z"), end=UtcTime.parse("2026-01-09T22:00:00Z")
)


def _rows(
    unrecorded: tuple[Interval, ...] = (),
    prices: dict[UtcTime, tuple[str, str, str, str]] | None = None,
) -> tuple[Mt5RawRow, ...]:
    return m5.minute_rows(CALENDAR, WEEK, unrecorded=unrecorded, prices=prices)


def test_a_file_that_differs_from_the_fetch_record_fails_the_whole_acceptance(
    tmp_path: Path,
) -> None:
    """D03 §15.2 の取得不足 (c)・§15.3 の 1: 破損は区間の記録で済ませず、受入れ全体を失敗させる。"""
    table = m5.write_rule_files(tmp_path)
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, _rows())])
    assert pending.manifest.conversion.mt5 is not None
    entry = Mt5FetchEntry(
        rid="r",
        symbol=m5.USDJPY,
        file="f",
        status="ok",
        requested_from_api=0,
        requested_to_api=60,
        rows=1,
        first_bar_time=0,
        last_bar_time=0,
        sha256_gz="a" * 64,
        sha256_csv="b" * 64,
    )
    broken = Mt5ReadFile(
        entry=entry, sha256_gz="c" * 64, sha256_csv="b" * 64, header_ok=True, rows=()
    )
    with pytest.raises(Mt5AcceptanceFailed, match="corrupt"):
        verify_fetch_files([entry], {"r": broken})
    with pytest.raises(Mt5AcceptanceFailed, match="not read"):
        verify_fetch_files([entry], {})


def test_upper_bars_follow_the_build_condition(tmp_path: Path) -> None:
    """D03 §15.4: 1 分足から直接作り、4 値は記録のある分だけ。全分無記録の 4 時間足は作らず、1
    時間が全分
    無記録の 4 時間足は部分観測（被覆率 1 にならない）。`bar_start` は区間の開始。"""
    table = m5.write_rule_files(tmp_path)
    hole = Interval(
        start=UtcTime.parse("2026-01-06T15:00:00Z"), end=UtcTime.parse("2026-01-06T16:00:00Z")
    )
    whole = Interval(
        start=UtcTime.parse("2026-01-07T02:00:00Z"), end=UtcTime.parse("2026-01-07T06:00:00Z")
    )
    prices = {UtcTime.parse("2026-01-06T14:59:00Z"): ("150.000", "151.000", "149.000", "150.500")}
    pending = m5.build_pending(
        CALENDAR, table, [m5.MonthSpec(2026, 1, _rows(unrecorded=(hole, whole), prices=prices))]
    )
    four = {
        bar.bar_start: bar
        for pid, bars in pending.partition_bars.items()
        if pid.series.timeframe.id == "4h_ny17"
        for bar in bars
    }
    partial = four[UtcTime.parse("2026-01-06T14:00:00Z")]
    assert partial.coverage is not None
    assert (partial.coverage.expected_minutes, partial.coverage.recorded_minutes) == (240, 180)
    assert partial.high.value == 151 and partial.low.value == 149
    assert UtcTime.parse("2026-01-07T02:00:00Z") not in four
    no_record = [
        r
        for r in pending.report.results
        if r.kind is CheckKind.NO_RECORD_BAR
        and r.series.timeframe.id == "4h_ny17"
        and WEEK.contains(r.interval.start)
    ]
    assert [r.interval.start for r in no_record] == [UtcTime.parse("2026-01-07T02:00:00Z")]
    # 5 種は人間の分類の対象にならない（分類が空のまま確定できる）。
    m5.readable(pending)


def test_out_of_session_bars_in_standard_need_classification(tmp_path: Path) -> None:
    """D12-94 (a): 標準の組で休場帯に入る 1 分足は `UNEXPECTED_BAR`（WARN）で、分類まで確定しない。
    セッション外データ異常と分類すれば、確定段階の再実行（原ファイルを読み直さない）で除かれる。"""
    table = m5.write_rule_files(tmp_path)
    rows = list(_rows())
    saturday = UtcTime.parse("2026-01-10T03:00:00Z")
    rows.append(
        Mt5RawRow(
            api_time=int(saturday.value.timestamp()) + 7200,
            open="150.000",
            high="150.000",
            low="150.000",
            close="150.000",
            spread=1,
        )
    )
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, tuple(rows))])
    unexpected = [r for r in pending.report.results if r.kind is CheckKind.UNEXPECTED_BAR]
    assert [r.interval.start for r in unexpected] == [saturday]
    assert pending.manifest.conversion.mt5 is not None
    assert pending.manifest.conversion.mt5.weeks_failed == 1
    with pytest.raises(MarketDataValueError):
        m5.readable(pending)
    rerun = reaccept_mt5(
        pending,
        calendar=CALENDAR,
        timeframe_defs=m5.TIMEFRAME_DEFS,
        targets=m5.TARGETS,
        boundaries=AccessBoundaries(
            research_until=UtcTime.parse("2027-01-01T00:00:00Z"),
            holdout_until=UtcTime.parse("2028-01-01T00:00:00Z"),
        ),
        excluded={(unexpected[0].series, unexpected[0].interval)},
    )
    assert not [r for r in rerun.report.results if r.kind is CheckKind.UNEXPECTED_BAR]


def test_candidate_variant_excludes_out_of_calendar_bars_and_reports_them(tmp_path: Path) -> None:
    """D12-97 (a): 候補の組でカレンダーの外に出る 1 分足は足の生成に使わず、
    週ごとの件数を報告する。"""
    table = m5.write_rule_files(tmp_path)
    rows = list(_rows())
    friday_after_close = UtcTime.parse("2026-01-09T22:30:00Z")
    rows.append(
        Mt5RawRow(
            api_time=int(friday_after_close.value.timestamp()) + 7200,
            open="150.000",
            high="150.000",
            low="150.000",
            close="150.000",
            spread=1,
        )
    )
    pending = m5.build_pending(
        CALENDAR, table, [m5.MonthSpec(2026, 1, tuple(rows))], variant=TimeRuleVariant.CANDIDATE
    )
    reports = [r for r in pending.report.results if r.kind is CheckKind.CANDIDATE_OUT_OF_CALENDAR]
    assert len(reports) == 1
    assert dict(reports[0].detail)["bars"] == "1"
    assert not [r for r in pending.report.results if r.kind is CheckKind.UNEXPECTED_BAR]
    minute_bars = [
        bar
        for pid, bars in pending.partition_bars.items()
        if pid.series.timeframe.id == "1m"
        for bar in bars
    ]
    assert friday_after_close not in {bar.bar_start for bar in minute_bars}
    assert pending.manifest.conversion.mt5 is not None
    assert pending.manifest.conversion.mt5.time_rule_table.variant is TimeRuleVariant.CANDIDATE


def test_manifest_round_trips_and_the_table_hash_changes_the_snapshot_id(tmp_path: Path) -> None:
    """D03 §15.6 の 4: MT5 の記録と系列の件数は manifest に書かれて読み戻せ、表のハッシュ・
    変換の組を
    変えると `snapshot_id` が変わる。"""
    table = m5.write_rule_files(tmp_path)
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, _rows())])
    store = ParquetSnapshotStore(root=tmp_path / "snapshots")
    store.write_manifest("p", pending.manifest)
    assert store.read_manifest("p") == pending.manifest
    other = m5.build_pending(
        CALENDAR, replace(table, sha256="c" * 64), [m5.MonthSpec(2026, 1, _rows())]
    )
    assert other.manifest.snapshot_id() != pending.manifest.snapshot_id()
    candidate = m5.build_pending(
        CALENDAR, table, [m5.MonthSpec(2026, 1, _rows())], variant=TimeRuleVariant.CANDIDATE
    )
    assert candidate.manifest.snapshot_id() != pending.manifest.snapshot_id()
    assert all(record.coverage_counts is not None for record in pending.manifest.series)
