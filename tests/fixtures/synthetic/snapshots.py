"""snapshot manifest の人工データ（D03 §3.7・§11）。

識別子の決定論性（`created_at` に依存しない、列挙順に依存しない、分類が違えば変わる）を
テストするための manifest を組み立てる。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.partition_digest import partition_digest_hex
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
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


def readable_for(
    partition_bars: Mapping[PartitionId, Sequence[Bar]] | Sequence[PartitionId],
    *,
    interval: Interval = COVERED,
) -> ReadableSnapshot:
    """読み取り可能な snapshot（承認済み・最終ディレクトリ）を1行で作る。

    読み取り経路は `ReadableSnapshot` しか受け取らない（D03 §3.7.1 の 2・3）。ディレクトリ名
    は manifest から再計算した最終識別子にする。
    """
    manifest = approved_for(partition_bars, interval=interval)
    return ReadableSnapshot(manifest=manifest, directory_name=str(manifest.snapshot_id()))


def record_for(partition_id: PartitionId, bars: Sequence[Bar]) -> PartitionRecord:
    """実際の足から partition の記録を作る（足数・区間・ダイジェストを実物に合わせる）。

    読み取り側は、渡された足が manifest の記録どおりかを照合する（D03 §3.7.1）。テストの
    manifest も実際の足から作らないと、その照合に落ちる。
    """
    ordered = sorted(bars, key=lambda bar: bar.bar_start.value)
    interval = (
        COVERED if not ordered else Interval(start=ordered[0].bar_start, end=ordered[-1].bar_end)
    )
    return PartitionRecord(
        partition_id=partition_id,
        interval=interval,
        bar_count=len(ordered),
        digest=ContentDigest.sha256(partition_digest_hex(ordered)),
    )


def approved_for(
    partition_bars: Mapping[PartitionId, Sequence[Bar]] | Sequence[PartitionId],
    *,
    interval: Interval = COVERED,
) -> SnapshotManifest:
    """指定した partition を記録した、承認済みの manifest を作る。

    as-of ビューと公開フィードは、どちらも「承認済み snapshot の、manifest に記録された
    partition」しか読めず、さらに渡された足が記録どおりかを照合する（D03 §3.7.1・§6.1）。

    partition ごとの足を渡すと、足数・区間・ダイジェストをその足から作る。partition の
    識別子だけを渡した場合は足が空の記録になる（足を読まない経路のテスト用）。
    """
    if isinstance(partition_bars, Mapping):
        records = tuple(
            record_for(partition_id, bars)
            for partition_id, bars in sorted(partition_bars.items(), key=lambda pair: str(pair[0]))
        )
    else:
        records = tuple(
            PartitionRecord(
                partition_id=partition_id,
                interval=interval,
                bar_count=0,
                digest=ContentDigest.sha256(partition_digest_hex(())),
            )
            for partition_id in partition_bars
        )
    by_series: dict[SeriesId, list[PartitionId]] = {}
    for record in records:
        by_series.setdefault(record.series_id, []).append(record.partition_id)
    series_records = tuple(
        SeriesManifest(
            series_id=series,
            covered_interval=interval,
            bar_count=sum(record.bar_count for record in records if record.series_id == series),
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
