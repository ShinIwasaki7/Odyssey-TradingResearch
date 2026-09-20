"""snapshot manifest の人工データ（D03 §3.7・§11）。

識別子の決定論性（`created_at` に依存しない、列挙順に依存しない、分類が違えば変わる）を
テストするための manifest を組み立てる。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    BasisDeclaration,
    ClosureDecision,
    ConversionRecord,
    LegacyAccessRecord,
    PartitionId,
    PartitionRecord,
    SeriesManifest,
    SnapshotManifest,
    SourceFile,
)
from tests.fixtures.synthetic import market

CREATED_AT = UtcTime.parse("2026-09-20T09:00:00Z")
COVERED = Interval(
    start=UtcTime.parse("2016-01-03T22:00:00Z"), end=UtcTime.parse("2023-12-31T22:00:00Z")
)

CONVERSION = ConversionRecord(
    code_version="stage1",
    time_convention="explicit_offset_utc",
    aggregation_rule_version="ny17_v2",
    calendar_id="fx_ny17",
    calendar_version=1,
)

BASIS = BasisDeclaration(value=PriceBasis.BID, verified=False)

REPORT_DIGEST = ContentDigest.sha256("a" * 64)


def digest_for(marker: str) -> ContentDigest:
    """印から決まる固定のダイジェスト（同じ印なら常に同じ値）。"""
    return ContentDigest.sha256(hashlib.sha256(marker.encode("utf-8")).hexdigest())


def source(path: str, symbol_code: str = "USDJPY", timeframe_id: str = "1h") -> SourceFile:
    """原ファイルの記録を1件作る。"""
    from odyssey_fx.common.symbol import Symbol
    from odyssey_fx.common.timeframe import TimeframeRef

    return SourceFile(
        path=path,
        sha256=digest_for(path[0]).hex,
        rows=100,
        symbol=Symbol(symbol_code),
        timeframe=TimeframeRef(timeframe_id, 1),
        declared_basis=PriceBasis.BID,
        provenance_counts=(("dukascopy", 40), ("histdata", 60)),
    )


def partition(series: SeriesId, access_class: AccessClass) -> PartitionRecord:
    """partition の記録を1件作る。"""
    return PartitionRecord(
        partition_id=PartitionId(series=series, access_class=access_class),
        interval=COVERED,
        bar_count=100,
        digest=digest_for(access_class.value[0].lower()),
    )


def manifest(
    *,
    created_at: UtcTime = CREATED_AT,
    sources: Sequence[SourceFile] | None = None,
    series_records: Sequence[SeriesManifest] | None = None,
    partitions: Sequence[PartitionRecord] | None = None,
    closure_decisions: Sequence[ClosureDecision] = (),
    legacy_access: Sequence[LegacyAccessRecord] = (),
    approval: Approval | None = None,
) -> SnapshotManifest:
    """テスト用の manifest を組み立てる。"""
    hourly = market.series()
    if sources is None:
        sources = (source("data/raw/market/USDJPY_1h_merged.csv"),)
    if partitions is None:
        partitions = (partition(hourly, AccessClass.RESEARCH_HISTORY),)
    if series_records is None:
        series_records = (
            SeriesManifest(
                series_id=hourly,
                covered_interval=COVERED,
                bar_count=100,
                partitions=tuple(record.partition_id for record in partitions),
            ),
        )
    return SnapshotManifest(
        created_at=created_at,
        basis_declaration=BASIS,
        sources=tuple(sources),
        conversion=CONVERSION,
        series=tuple(series_records),
        partitions=tuple(partitions),
        integrity_report_ref=REPORT_DIGEST,
        closure_decisions=tuple(closure_decisions),
        legacy_access=tuple(legacy_access),
        approval=approval,
    )


def approved_for(
    partition_ids: Sequence[PartitionId],
    *,
    interval: Interval = COVERED,
) -> SnapshotManifest:
    """指定した partition を記録した、承認済みの manifest を作る。

    as-of ビューと公開フィードは、どちらも「承認済み snapshot の、manifest に記録された
    partition」しか読めない（D03 §3.7.1・§6.1）。その前提を満たす manifest を1行で作る。
    """
    records = tuple(
        PartitionRecord(
            partition_id=partition_id,
            interval=interval,
            bar_count=100,
            digest=digest_for(partition_id.access_class.value),
        )
        for partition_id in partition_ids
    )
    by_series: dict[SeriesId, list[PartitionId]] = {}
    for record in records:
        by_series.setdefault(record.series_id, []).append(record.partition_id)
    series_records = tuple(
        SeriesManifest(
            series_id=series,
            covered_interval=interval,
            bar_count=100 * len(ids),
            partitions=tuple(ids),
        )
        for series, ids in by_series.items()
    )
    return approved(series_records=series_records, partitions=records)


def approved(
    *,
    created_at: UtcTime = CREATED_AT,
    sources: Sequence[SourceFile] | None = None,
    series_records: Sequence[SeriesManifest] | None = None,
    partitions: Sequence[PartitionRecord] | None = None,
    closure_decisions: Sequence[ClosureDecision] = (),
    legacy_access: Sequence[LegacyAccessRecord] = (),
) -> SnapshotManifest:
    """承認済みの manifest（as-of ビューが読める状態、D03 §3.7.1 の3）。"""
    return manifest(
        created_at=created_at,
        sources=sources,
        series_records=series_records,
        partitions=partitions,
        closure_decisions=closure_decisions,
        legacy_access=legacy_access,
        approval=Approval(
            approved_by="reviewer",
            approved_at=UtcTime.parse("2026-09-20T12:00:00Z"),
            comment="受入れ確認済み",
        ),
    )
