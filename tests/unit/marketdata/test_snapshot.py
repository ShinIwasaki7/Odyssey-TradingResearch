"""snapshot manifest と識別子の単体テスト（D03 §3.7・§3.7.1・§11）。

確かめること（D03 §11 の「単体」）:

- 識別子が受入れ実行時刻（`created_at`）に依存しない。
- 識別子が原ファイルの列挙順・検査の実行順に依存しない（列を正規順序へ整列するため）。
- 宣言者・宣言日時と承認が識別子に影響しない。
- 警告ごとに解決した分類が違えば識別子が変わる（分類が異なれば別 snapshot）。
- 記入されたままの分類（`closure_decisions`）は識別子に入らない（D03 §3.7.1 v1.7）。
"""

from __future__ import annotations

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.classification import (
    ClassificationDecision,
    ClassificationOutcome,
    ResolvedClassification,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    DeclarationRecord,
    PartitionId,
    SeriesManifest,
)
from tests.fixtures.synthetic import market, snapshots

SERIES = market.series()
GAP = Interval(
    start=UtcTime.parse("2020-05-01T10:00:00Z"), end=UtcTime.parse("2020-05-01T11:00:00Z")
)


# --- 識別子の決定論性（D03 §3.7.1）------------------------------------------


def test_the_snapshot_id_ignores_the_acceptance_time() -> None:
    """受入れ実行時刻は記録のみで識別には使わない（D03 §3.7）。"""
    morning = snapshots.manifest(created_at=UtcTime.parse("2026-09-20T09:00:00Z"))
    evening = snapshots.manifest(created_at=UtcTime.parse("2026-09-20T21:30:00Z"))
    assert morning.snapshot_id() == evening.snapshot_id()


def test_the_snapshot_id_ignores_the_source_enumeration_order() -> None:
    """ファイルシステムの列挙順が識別子に影響しない（D03 §3.7.1 の正規順序）。"""
    first = snapshots.source("data/raw/market/AUDJPY_1h_merged.csv", "AUDJPY")
    second = snapshots.source("data/raw/market/USDJPY_1h_merged.csv")
    forward = snapshots.manifest(sources=(first, second))
    backward = snapshots.manifest(sources=(second, first))
    assert forward.snapshot_id() == backward.snapshot_id()
    assert forward.sources == backward.sources


def test_the_snapshot_id_ignores_the_partition_enumeration_order() -> None:
    research = snapshots.partition(SERIES, AccessClass.RESEARCH_HISTORY)
    holdout = snapshots.partition(SERIES, AccessClass.LEGACY_HOLDOUT)
    series_record = SeriesManifest(
        series_id=SERIES,
        covered_interval=snapshots.COVERED,
        bar_count=200,
        partitions=(research.partition_id, holdout.partition_id),
    )
    forward = snapshots.manifest(partitions=(research, holdout), series_records=(series_record,))
    backward = snapshots.manifest(partitions=(holdout, research), series_records=(series_record,))
    assert forward.snapshot_id() == backward.snapshot_id()


def test_the_snapshot_id_ignores_the_declaration_record() -> None:
    """誰がいつ価格基準を宣言しても同じ識別子になる（D03 §3.7.1 の承認条件2）。"""
    plain = snapshots.manifest()
    recorded = snapshots.manifest().with_approval(
        Approval(approved_by="a", approved_at=UtcTime.parse("2026-09-20T12:00:00Z"))
    )
    assert plain.snapshot_id() == recorded.snapshot_id()


def test_the_snapshot_id_ignores_the_approval() -> None:
    """承認は識別に影響しない（D03 §3.7.1）。"""
    pending = snapshots.manifest()
    approved = snapshots.approved()
    assert pending.snapshot_id() == approved.snapshot_id()
    assert not pending.is_approved
    assert approved.is_approved


# --- 分類が識別子を変える（D03 §3.7.1 v1.7）---------------------------------

LATER = Interval(
    start=UtcTime.parse("2020-06-01T10:00:00Z"), end=UtcTime.parse("2020-06-01T11:00:00Z")
)


def _resolved(
    interval: Interval, outcome: ClassificationOutcome = ClassificationOutcome.DATA_GAP
) -> ResolvedClassification:
    return ResolvedClassification(
        kind=CheckKind.MISSING_EXPECTED_BAR, series_id=SERIES, interval=interval, outcome=outcome
    )


def _decision(
    interval: Interval, outcome: ClassificationOutcome = ClassificationOutcome.DATA_GAP
) -> ClassificationDecision:
    return ClassificationDecision(
        kind=CheckKind.MISSING_EXPECTED_BAR, interval=interval, series=(SERIES,), outcome=outcome
    )


def test_recording_a_resolved_classification_changes_the_snapshot_id() -> None:
    """分類が異なれば別 snapshot である（D03 §3.7.1）。"""
    pending = snapshots.manifest()
    decided = pending.with_classification(
        decisions=(_decision(GAP),),
        resolved=(_resolved(GAP),),
        provisional_report_ref=pending.integrity_report_ref,
    )
    assert pending.snapshot_id() != decided.snapshot_id()


def test_classifying_the_same_gap_differently_changes_the_snapshot_id() -> None:
    """同じ欠落区間を「休場」と「データ欠損」のどちらに分類したかで識別子が変わる。"""
    as_closure = snapshots.manifest(
        resolved_classifications=(_resolved(GAP, ClassificationOutcome.CLOSURE),)
    )
    as_gap = snapshots.manifest(resolved_classifications=(_resolved(GAP),))
    assert as_closure.snapshot_id() != as_gap.snapshot_id()


def test_the_resolved_classification_order_does_not_change_the_snapshot_id() -> None:
    first = _resolved(GAP, ClassificationOutcome.CLOSURE)
    second = _resolved(LATER)
    forward = snapshots.manifest(resolved_classifications=(first, second))
    backward = snapshots.manifest(resolved_classifications=(second, first))
    assert forward.snapshot_id() == backward.snapshot_id()
    assert forward.resolved_classifications == backward.resolved_classifications


def test_the_written_decisions_do_not_enter_the_snapshot_id() -> None:
    """記入されたままの分類は監査用の記録で、識別子の対象外（D03 §3.7.1 v1.7）。

    同じ警告集合を1件の広い区間で書いても、複数の狭い区間で書いても識別子が変わらない
    ようにするためである。
    """
    wide = Interval(start=GAP.start, end=LATER.end)
    resolved = (_resolved(GAP), _resolved(LATER))
    one_wide = snapshots.manifest(
        closure_decisions=(_decision(wide),), resolved_classifications=resolved
    )
    two_narrow = snapshots.manifest(
        closure_decisions=(_decision(GAP), _decision(LATER)), resolved_classifications=resolved
    )
    assert one_wide.snapshot_id() == two_narrow.snapshot_id()
    assert one_wide.closure_decisions != two_narrow.closure_decisions


def test_the_written_decisions_are_stored_in_the_canonical_order() -> None:
    """保存時の整列鍵は `(kind, interval.start, interval.end, 系列, outcome)`（D03 §3.7.1）。"""
    forward = snapshots.manifest(closure_decisions=(_decision(LATER), _decision(GAP)))
    assert [decision.interval for decision in forward.closure_decisions] == [GAP, LATER]


def test_the_provisional_report_ref_defaults_to_the_final_report() -> None:
    """再実行しなかった snapshot では、暫定報告と最終報告のダイジェストが同じ（D03 §3.7）。"""
    manifest = snapshots.manifest()
    assert manifest.provisional_report_ref == manifest.integrity_report_ref


def test_a_different_provisional_report_changes_the_snapshot_id() -> None:
    """暫定報告のダイジェストは識別子の対象（D03 §3.7.1 v1.7）。"""
    from dataclasses import replace

    base = snapshots.manifest()
    rerun = replace(base, provisional_report_ref=snapshots.digest_for("provisional"))
    assert base.snapshot_id() != rerun.snapshot_id()


def test_a_warning_cannot_be_resolved_twice() -> None:
    """解決済みの分類は警告1件に1件（D03 §4 の 9）。"""
    with pytest.raises(MarketDataValueError, match="twice"):
        snapshots.manifest(
            resolved_classifications=(
                _resolved(GAP),
                _resolved(GAP, ClassificationOutcome.CLOSURE),
            )
        )


# --- 識別子の対象（D03 §3.7.1）----------------------------------------------


def test_the_identity_payload_excludes_the_non_deterministic_fields() -> None:
    payload = snapshots.approved().identity_payload()
    assert set(payload) == {
        "basis_declaration",
        "conversion",
        "integrity_report_ref",
        "legacy_access",
        "partitions",
        "provisional_report_ref",
        "resolved_classifications",
        "series",
        "sources",
    }


def test_changing_the_calendar_version_changes_the_snapshot_id() -> None:
    """カレンダー版は変換記録に含まれ、識別子の対象になる（D03 §3.7.1）。"""
    from dataclasses import replace

    base = snapshots.manifest()
    bumped = replace(base, conversion=replace(base.conversion, calendar_version=2))
    assert base.snapshot_id() != bumped.snapshot_id()


# --- 構築時の検査 -----------------------------------------------------------


def test_a_partition_must_belong_to_a_declared_series() -> None:
    """manifest に載っていない系列の partition は記録できない（整合の検査）。"""
    other = market.series(symbol=market.EURUSD)
    declared = SeriesManifest(
        series_id=SERIES,
        covered_interval=snapshots.COVERED,
        bar_count=100,
        partitions=(PartitionId(series=SERIES, access_class=AccessClass.RESEARCH_HISTORY),),
    )
    with pytest.raises(MarketDataValueError, match="not in SnapshotManifest.series"):
        snapshots.manifest(
            series_records=(declared,),
            partitions=(snapshots.partition(other, AccessClass.RESEARCH_HISTORY),),
        )


def test_a_series_manifest_rejects_a_foreign_partition() -> None:
    other = market.series(symbol=market.EURUSD)
    with pytest.raises(MarketDataValueError, match="does not belong to series"):
        SeriesManifest(
            series_id=SERIES,
            covered_interval=snapshots.COVERED,
            bar_count=1,
            partitions=(PartitionId(series=other, access_class=AccessClass.RESEARCH_HISTORY),),
        )


def test_the_partition_directory_carries_the_access_class() -> None:
    """物理分離のため、partition ディレクトリ名に分類を含める（D03 §3.8）。"""
    partition_id = PartitionId(series=SERIES, access_class=AccessClass.LEGACY_HOLDOUT)
    assert partition_id.directory == "USDJPY_1h_bid/LEGACY_HOLDOUT"


def test_the_declaration_record_requires_a_declarer() -> None:
    with pytest.raises(MarketDataValueError, match="declared_by"):
        DeclarationRecord(declared_by="", declared_at=UtcTime.parse("2026-01-01T00:00:00Z"))
