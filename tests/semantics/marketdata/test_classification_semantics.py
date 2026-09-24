"""確定段階の意味論テスト（D03 §11「意味論（確定段階）」v1.7）。

D03 §11 の契約行（3項目）:

1. 暫定 partition の価格を書き換えた後の分類が、内容不一致で拒否される。
2. 検査報告を差し替えた後の分類が拒否される。
3. カレンダー新版で説明された警告の分類が「対応なし」として拒否されない。

確定段階の再実行は暫定 snapshot の実体を再利用するので、再利用の前に内容を照合し、
不一致なら何も書かずに失敗する（D03 §4「再実行の入力」）。照合は保存された暫定 snapshot を
実際に書き出して読み戻した内容に対して行う。
"""

from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.acceptance import (
    PendingSnapshot,
    RawFile,
    approve,
    build_pending_snapshot,
    finalize,
    out_of_session_exclusions,
    reaccept_with_calendar,
)
from odyssey_fx.marketdata.application.snapshot_access import (
    ReadableSnapshot,
    require_recorded_content,
)
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.classification import (
    ClassificationDecision,
    ClassificationOutcome,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError, PartitionContentMismatch
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.snapshot import Approval, PartitionId
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
CALENDAR = market.calendar()
WINDOW = Interval(
    start=UtcTime.parse("2022-01-05T22:00:00Z"), end=UtcTime.parse("2022-01-07T22:00:00Z")
)
#: 間引く1時間（ニューヨーク現地 05:00〜06:00、冬時間の 10:00〜11:00Z）。
DROPPED = Interval(
    start=UtcTime.parse("2022-01-06T10:00:00Z"), end=UtcTime.parse("2022-01-06T11:00:00Z")
)
PENDING_DIR = "_pending/p1"


def _pending() -> PendingSnapshot:
    source = snapshots.source("data/raw/market/USDJPY_1h_merged.csv")
    raw_file = RawFile(
        path=source.path,
        sha256=source.sha256,
        symbol=source.symbol,
        timeframe=source.timeframe,
        declared_basis=source.declared_basis,
    )
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=(DROPPED.start,))
    return build_pending_snapshot(
        created_at=UtcTime.parse("2026-09-24T09:00:00Z"),
        raw_files=(raw_file,),
        bars_by_file={raw_file.path: bars},
        timeframe_defs=market.TIMEFRAME_DEFS,
        calendar=CALENDAR,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        basis_declaration=snapshots.BASIS,
        conversion=snapshots.CONVERSION,
    )


def _stored(tmp_path: Path) -> tuple[ParquetSnapshotStore, PendingSnapshot]:
    """暫定 snapshot を書き出し、保存された内容を読み戻した形で返す。"""
    pending = _pending()
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_integrity_report(PENDING_DIR, pending.report)
    for partition_id, bars in pending.partition_bars.items():
        store.write_partition(PENDING_DIR, partition_id, bars)
    store.write_manifest(PENDING_DIR, pending.manifest)
    return store, _read_back(store)


def _read_back(store: ParquetSnapshotStore) -> PendingSnapshot:
    manifest = store.read_manifest(PENDING_DIR)
    return PendingSnapshot(
        manifest=manifest,
        report=store.read_integrity_report(PENDING_DIR),
        partition_bars={
            record.partition_id: tuple(store.read_partition(PENDING_DIR, record.partition_id))
            for record in manifest.partitions
        },
    )


def _closure() -> ClassificationDecision:
    return ClassificationDecision(
        kind=CheckKind.MISSING_EXPECTED_BAR,
        interval=DROPPED,
        series=(HOURLY,),
        outcome=ClassificationOutcome.CLOSURE,
    )


def _revised_calendar() -> TradingCalendar:
    """間引いた1時間を休場と宣言した、版 2 のカレンダー。"""
    return market.calendar(
        closures=[market.closure(date(2022, 1, 6), time(5, 0), time(6, 0))], version=2
    )


def _only_partition(pending: PendingSnapshot) -> PartitionId:
    (partition_id,) = pending.partition_bars
    return partition_id


# --- 1. 暫定 partition の価格の書き換え --------------------------------------


def test_classifying_after_the_provisional_prices_were_rewritten_is_refused(
    tmp_path: Path,
) -> None:
    """価格だけを書き換えた暫定 partition は、足数も時刻も同じでも内容不一致で拒否される。"""
    store, _ = _stored(tmp_path)
    partition_id = _only_partition(_read_back(store))
    path = tmp_path / PENDING_DIR / partition_id.directory / "bars.parquet"
    frame = pl.read_parquet(path)
    frame = frame.with_columns(
        [
            (pl.col(column).cast(pl.Float64) + 10).cast(pl.String)
            for column in ("open", "high", "low", "close")
        ]
    )
    frame.write_parquet(path)

    tampered = _read_back(store)
    with pytest.raises(PartitionContentMismatch, match="digest"):
        require_recorded_content(tampered.manifest, tampered.report, tampered.partition_bars)


# --- 2. 検査報告の差し替え ---------------------------------------------------


def test_classifying_after_the_report_was_replaced_is_refused(tmp_path: Path) -> None:
    """人間が分類の根拠にした報告と違う報告に差し替えると、分類は拒否される。"""
    store, _ = _stored(tmp_path)
    path = tmp_path / PENDING_DIR / "integrity_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["results"] = payload["results"][:-1]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    swapped = _read_back(store)
    with pytest.raises(MarketDataValueError, match="not this snapshot's report"):
        require_recorded_content(swapped.manifest, swapped.report, swapped.partition_bars)


def test_an_untouched_provisional_snapshot_passes_the_check(tmp_path: Path) -> None:
    """上の2件が「何をしても失敗する」ために通っているのではないことの確認。"""
    _, stored = _stored(tmp_path)
    require_recorded_content(stored.manifest, stored.report, stored.partition_bars)


# --- 3. カレンダー新版で説明された警告 --------------------------------------


def test_a_warning_explained_by_the_new_calendar_is_not_reported_as_unmatched(
    tmp_path: Path,
) -> None:
    """休場と分類した欠落はカレンダーの新版で最終報告から消えるが、「対応なし」にならない。

    突き合わせは暫定報告と最終報告の和集合に対して行い（D03 §4 v1.7）、確定 snapshot は
    暫定報告を残すので、確定・承認・読み取りのどこでも分類が対応を失わない。
    """
    _, provisional = _stored(tmp_path)
    decisions = (_closure(),)
    final = reaccept_with_calendar(
        provisional,
        calendar=_revised_calendar(),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        aggregation_targets=(),
        excluded=out_of_session_exclusions(provisional, decisions),
    )
    assert not final.report.warnings, "新しいカレンダーが欠落を説明している"

    finalized = finalize(final, decisions, provisional=provisional)
    approved = approve(
        finalized,
        Approval(approved_by="reviewer", approved_at=UtcTime.parse("2026-09-24T12:00:00Z")),
    )
    readable = ReadableSnapshot(
        manifest=approved,
        directory_name=finalized.directory_name,
        report=final.report,
        provisional_report=provisional.report,
    )
    assert readable.snapshot_id == finalized.snapshot_id
