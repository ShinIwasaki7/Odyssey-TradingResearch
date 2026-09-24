"""adapters の単体テスト（D03 §8・§11、ADR-0025）。

確かめること:

- CSV は全列を文字列として読み、値を解釈しない（Decimal 化は application の責務）。
- Parquet の往復で価格・出来高が厳密に保たれる（浮動小数を経由しない、ADR-0012）。
- `manifest.json` の往復で snapshot 識別子が変わらない。
- 閲覧記録が追記専用で読み戻せる。

実データは読まない。人工データを一時ディレクトリに書いて使う。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.csv_source import CsvRawBarSource
from odyssey_fx.marketdata.adapters.parquet_store import (
    MANIFEST_SCHEMA_VERSION,
    ParquetSnapshotStore,
)
from odyssey_fx.marketdata.application.access_log import (
    AccessLogEntry,
    AccessLogEntryKind,
    serialize_entry,
)
from odyssey_fx.marketdata.application.report_digest import integrity_report_digest_hex
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.classification import (
    ClassificationDecision,
    ClassificationOutcome,
    ResolvedClassification,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError, SnapshotNotApproved
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
CALENDAR = market.calendar()
WINDOW = Interval(
    start=UtcTime.parse("2026-01-13T22:00:00Z"), end=UtcTime.parse("2026-01-14T22:00:00Z")
)
PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)


# --- CSV 読込（D03 §8）------------------------------------------------------


def test_the_csv_source_returns_strings_without_interpreting_them(tmp_path: Path) -> None:
    """値の解釈は application の責務。adapters は文字列のまま渡す（ADR-0025）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    (tmp_path / "USDJPY_1h_merged.csv").write_text(market.csv_text(bars), encoding="utf-8")

    source = CsvRawBarSource(root=tmp_path)
    rows = source.read_file("USDJPY_1h_merged.csv").rows
    assert len(rows) == len(bars)
    for row in rows:
        assert all(isinstance(value, str) for value in row.values())
    # 先頭の無名列は宣言した名前に付け替わる。
    assert rows[0]["timestamp"] == bars[0].bar_start.value.strftime("%Y-%m-%d %H:%M:%S+00:00")
    assert rows[0]["open"] == str(bars[0].open.value)
    assert rows[0]["source"] == "histdata"


def test_the_csv_source_hashes_the_bytes_it_read(tmp_path: Path) -> None:
    """返すダイジェストは、行を作ったのと**同じバイト列**の sha256 である（D03 §3.7.1）。

    ダイジェストと行を別々の読込から作ると、その間にファイルが差し替わったときに manifest
    の出所の記録が実データと食い違い、「記録どおりでない snapshot」ができてしまう。
    """
    content = b",open,high,low,close,volume,source\n2026-01-01 00:00:00+00:00,1,1,1,1,0,histdata\n"
    (tmp_path / "a.csv").write_bytes(content)

    result = CsvRawBarSource(root=tmp_path).read_file("a.csv")
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert len(result.sha256) == 64
    assert len(result.rows) == 1


def test_the_csv_source_reads_the_file_only_once(tmp_path: Path) -> None:
    """読込は1回。ファイルを開いた回数で確かめる。"""
    (tmp_path / "a.csv").write_bytes(
        b",open,high,low,close,volume,source\n2026-01-01 00:00:00+00:00,1,1,1,1,0,histdata\n"
    )
    opened: list[str] = []
    real_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        opened.append(self.name)
        return real_read_bytes(self)

    with mock.patch.object(Path, "read_bytes", counting_read_bytes):
        CsvRawBarSource(root=tmp_path).read_file("a.csv")
    assert opened == ["a.csv"]


def test_the_csv_source_refuses_a_path_outside_its_root(tmp_path: Path) -> None:
    source = CsvRawBarSource(root=tmp_path / "market")
    (tmp_path / "market").mkdir()
    (tmp_path / "secret.csv").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the raw data root"):
        source.read_file("../secret.csv")


# --- Parquet の往復（D03 §8）------------------------------------------------


def test_a_partition_round_trips_without_losing_decimal_precision(tmp_path: Path) -> None:
    """価格・出来高は文字列列として保存し、浮動小数を経由しない（ADR-0012）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_partition("snap", PARTITION, bars)
    restored = store.read_partition("snap", PARTITION)
    assert tuple(restored) == bars


def test_the_partition_digest_is_stable_for_the_same_content(tmp_path: Path) -> None:
    """同じ内容なら同じダイジェスト（受入れの決定論性、D03 §3.7.1）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    store = ParquetSnapshotStore(root=tmp_path)
    first = store.write_partition("snap_a", PARTITION, bars)
    second = store.write_partition("snap_b", PARTITION, tuple(reversed(bars)))
    assert first == second


def test_the_partition_directory_carries_the_access_class(tmp_path: Path) -> None:
    """物理分離のため、実体はアクセス分類ごとのディレクトリに置く（D03 §3.8）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_partition("snap", PARTITION, bars)
    assert (tmp_path / "snap" / "USDJPY_1h_bid" / "RESEARCH_HISTORY" / "bars.parquet").is_file()


# --- manifest の往復（D03 §3.7.1）------------------------------------------


def test_the_manifest_round_trips_and_keeps_its_snapshot_id(tmp_path: Path) -> None:
    manifest = snapshots.approved()
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_manifest("snap", manifest)
    restored = store.read_manifest("snap")
    assert restored.snapshot_id() == manifest.snapshot_id()
    assert restored == manifest


def test_the_manifest_json_is_written_with_sorted_keys(tmp_path: Path) -> None:
    """保存形式も正規順序で書く（D03 §3.7.1）。"""
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_manifest("snap", snapshots.approved())
    text = (tmp_path / "snap" / "manifest.json").read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert list(payload) == sorted(payload)


def test_reading_an_unknown_manifest_version_is_refused(tmp_path: Path) -> None:
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_manifest("snap", snapshots.approved())
    path = tmp_path / "snap" / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported manifest schema_version"):
        store.read_manifest("snap")


# --- 検査報告と閲覧記録 -----------------------------------------------------


def test_the_integrity_report_round_trips(tmp_path: Path) -> None:
    report = IntegrityReport(
        results=(
            CheckResult.create(
                CheckKind.MISSING_EXPECTED_BAR, HOURLY, WINDOW, detail={"reason": "gap"}
            ),
        )
    )
    store = ParquetSnapshotStore(root=tmp_path)
    digest = store.write_integrity_report("snap", report)
    assert len(digest) == 64
    assert store.read_integrity_report("snap") == report


def test_the_access_log_is_append_only(tmp_path: Path) -> None:
    """既存の行を書き換えず、追記した順に読み戻せる（ADR-0014）。"""
    store = ParquetSnapshotStore(root=tmp_path)
    holdout = PartitionId(series=HOURLY, access_class=AccessClass.LEGACY_HOLDOUT)
    for kind in (AccessLogEntryKind.GRANTED, AccessLogEntryKind.CONSUMED):
        store.append_access_log(
            "snap",
            serialize_entry(
                AccessLogEntry(
                    kind=kind,
                    partition_id=holdout,
                    recorded_at=UtcTime.parse("2026-09-20T00:00:00Z"),
                    actor="tester",
                )
            ),
        )
    entries = store.read_access_log("snap")
    assert [entry["kind"] for entry in entries] == ["GRANTED", "CONSUMED"]


def test_an_absent_access_log_reads_as_empty(tmp_path: Path) -> None:
    assert ParquetSnapshotStore(root=tmp_path).read_access_log("snap") == ()


def test_reading_a_missing_partition_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="partition file not found"):
        ParquetSnapshotStore(root=tmp_path).read_partition("snap", PARTITION)


def test_a_zero_volume_bar_round_trips(tmp_path: Path) -> None:
    """HistData 由来の出来高 0 も厳密に保たれる（D03 §2）。"""
    interval = Interval(
        start=UtcTime.parse("2026-01-14T10:00:00Z"),
        end=UtcTime.parse("2026-01-14T11:00:00Z"),
    )
    bar = market.make_bar(HOURLY, interval, volume="0")
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_partition("snap", PARTITION, (bar,))
    (restored,) = store.read_partition("snap", PARTITION)
    assert restored.volume == decimal_from_str("0")


# --- 確定・承認済み snapshot を開く（D03 §3.7.1 の 2・3）--------------------


def _write_snapshot(
    store: ParquetSnapshotStore,
    manifest: SnapshotManifest,
    *,
    directory: str | None = None,
    report: IntegrityReport | None = None,
) -> str:
    """manifest と検査報告を書き、そのディレクトリ名を返す。

    読み取りの関門は報告のダイジェストと分類の対応も見る（D03 §3.7.1）ので、manifest だけ
    では開けない。
    """
    target = str(manifest.snapshot_id()) if directory is None else directory
    store.write_manifest(target, manifest)
    store.write_integrity_report(target, snapshots.EMPTY_REPORT if report is None else report)
    return target


def test_open_readable_accepts_a_correctly_placed_snapshot(tmp_path: Path) -> None:
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest = snapshots.approved_for({PARTITION: bars})
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest)

    readable = store.open_readable(directory)
    assert readable.snapshot_id == manifest.snapshot_id()
    assert readable.manifest == manifest


def test_open_readable_refuses_a_provisional_snapshot(tmp_path: Path) -> None:
    """暫定ディレクトリに承認付き manifest を置いても開けない（D03 §3.7.1 の1）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest = snapshots.approved_for({PARTITION: bars})
    store = ParquetSnapshotStore(root=tmp_path)
    pending_dir = f"_pending/{manifest.snapshot_id()}"
    _write_snapshot(store, manifest, directory=pending_dir)

    with pytest.raises(SnapshotNotApproved, match="provisional snapshot"):
        store.open_readable(pending_dir)


def test_open_readable_refuses_a_mismatched_directory_name(tmp_path: Path) -> None:
    """ディレクトリ名が最終識別子と違えば開けない（D03 §3.7.1 の2）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest = snapshots.approved_for({PARTITION: bars})
    store = ParquetSnapshotStore(root=tmp_path)
    _write_snapshot(store, manifest, directory="wrong-directory")

    with pytest.raises(MarketDataValueError, match="does not match the manifest"):
        store.open_readable("wrong-directory")


def test_reading_a_manifest_with_an_altered_id_is_refused(tmp_path: Path) -> None:
    """ファイルの `snapshot_id` を書き換えた manifest は読めない（D03 §3.7.1）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest = snapshots.approved_for({PARTITION: bars})
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest)

    path = tmp_path / directory / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["snapshot_id"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MarketDataValueError, match="has been altered"):
        store.read_manifest(directory)


def test_reading_a_manifest_with_altered_content_is_refused(tmp_path: Path) -> None:
    """内容を書き換えれば再計算値が変わり、記録された識別子と食い違う。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest = snapshots.approved_for({PARTITION: bars})
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest)

    path = tmp_path / directory / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["partitions"][0]["bar_count"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MarketDataValueError, match="has been altered"):
        store.read_manifest(directory)


# --- 書いた内容と manifest の記録が一致する（D03 §3.7.1）-------------------


def test_the_written_report_digest_matches_the_application_value(tmp_path: Path) -> None:
    """adapters が返すダイジェストは application の計算値と一致する。"""
    report = IntegrityReport(
        results=(
            CheckResult.create(
                CheckKind.MISSING_EXPECTED_BAR, HOURLY, WINDOW, detail={"reason": "gap"}
            ),
        )
    )
    written = ParquetSnapshotStore(root=tmp_path).write_integrity_report("snap", report)
    assert written == integrity_report_digest_hex(report)


def test_the_written_report_file_hashes_to_the_recorded_digest(tmp_path: Path) -> None:
    """書いたファイルそのものを読み直して再計算しても同じ値になる。"""
    report = IntegrityReport(
        results=(CheckResult.create(CheckKind.UNEXPECTED_BAR, HOURLY, WINDOW),)
    )
    store = ParquetSnapshotStore(root=tmp_path)
    written = store.write_integrity_report("snap", report)

    text = (tmp_path / "snap" / "integrity_report.json").read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == written
    # 読み戻した報告からの再計算も一致する。
    assert integrity_report_digest_hex(store.read_integrity_report("snap")) == written


# --- 検査報告も検証する（D03 §3.7.1）---------------------------------------


def test_open_readable_refuses_a_snapshot_without_its_report(tmp_path: Path) -> None:
    """報告が無ければ開けない（manifest が参照しているものが見つからない）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest = snapshots.approved_for({PARTITION: bars})
    store = ParquetSnapshotStore(root=tmp_path)
    directory = str(manifest.snapshot_id())
    store.write_manifest(directory, manifest)  # 報告は書かない。

    with pytest.raises(MarketDataValueError, match="is missing"):
        store.open_readable(directory)


def test_open_readable_refuses_an_altered_report(tmp_path: Path) -> None:
    """報告のファイルを書き換えると、ダイジェストが合わず開けない。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    manifest = snapshots.approved_for({PARTITION: bars})
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest)

    # 報告に結果を1件足す（manifest のダイジェストは元のまま）。
    tampered = IntegrityReport(
        results=(CheckResult.create(CheckKind.SOURCE_TRANSITION, HOURLY, WINDOW),)
    )
    store.write_integrity_report(directory, tampered)

    with pytest.raises(MarketDataValueError, match="does not match the digest"):
        store.open_readable(directory)


def test_open_readable_refuses_unclassified_warnings(tmp_path: Path) -> None:
    """未分類の警告が残る manifest は、最終識別子の名前で置いても開けない。

    `FinalizedSnapshot` を直接作って確定段階を飛ばしても、読み取りの関門で止まる
    （D03 §3.7.1 の2、§4 の 9）。
    """
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    report = IntegrityReport(
        results=(CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, HOURLY, WINDOW),)
    )
    # 報告のダイジェストは正しいが、分類が記入されていない manifest。
    base = snapshots.approved_for({PARTITION: bars})
    manifest = SnapshotManifest(
        created_at=base.created_at,
        basis_declaration=base.basis_declaration,
        sources=base.sources,
        conversion=base.conversion,
        series=base.series,
        partitions=base.partitions,
        integrity_report_ref=ContentDigest.sha256(integrity_report_digest_hex(report)),
        approval=base.approval,
    )
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest, report=report)

    with pytest.raises(SnapshotNotApproved, match="still unclassified"):
        store.open_readable(directory)


def test_open_readable_accepts_a_snapshot_whose_warnings_are_classified(
    tmp_path: Path,
) -> None:
    """分類が記入されていれば開ける。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    report = IntegrityReport(
        results=(CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, HOURLY, WINDOW),)
    )
    base = snapshots.approved_for({PARTITION: bars})
    manifest = SnapshotManifest(
        created_at=base.created_at,
        basis_declaration=base.basis_declaration,
        sources=base.sources,
        conversion=base.conversion,
        series=base.series,
        partitions=base.partitions,
        integrity_report_ref=ContentDigest.sha256(integrity_report_digest_hex(report)),
        closure_decisions=(_gap_decision(),),
        resolved_classifications=(_gap_resolved(),),
        approval=base.approval,
    )
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest, report=report)

    readable = store.open_readable(directory)
    assert readable.snapshot_id == manifest.snapshot_id()
    assert readable.report == report


def _gap_decision(
    outcome: ClassificationOutcome = ClassificationOutcome.DATA_GAP,
) -> ClassificationDecision:
    return ClassificationDecision(
        kind=CheckKind.MISSING_EXPECTED_BAR,
        interval=WINDOW,
        series=(HOURLY,),
        outcome=outcome,
        note="試験用",
        calendar_ref="fx_ny17@v2" if outcome is ClassificationOutcome.CLOSURE else None,
    )


def _gap_resolved(
    outcome: ClassificationOutcome = ClassificationOutcome.DATA_GAP,
) -> ResolvedClassification:
    return ResolvedClassification(
        kind=CheckKind.MISSING_EXPECTED_BAR, series_id=HOURLY, interval=WINDOW, outcome=outcome
    )


# --- 分類と暫定報告の保存（D03 §3.7 v1.7）-----------------------------------


def _rerun_manifest(provisional: IntegrityReport, final: IntegrityReport) -> SnapshotManifest:
    """確定段階で再実行した snapshot の manifest（暫定報告を別に記録する）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    base = snapshots.approved_for({PARTITION: bars})
    return SnapshotManifest(
        created_at=base.created_at,
        basis_declaration=base.basis_declaration,
        sources=base.sources,
        conversion=base.conversion,
        series=base.series,
        partitions=base.partitions,
        integrity_report_ref=ContentDigest.sha256(integrity_report_digest_hex(final)),
        provisional_report_ref=ContentDigest.sha256(integrity_report_digest_hex(provisional)),
        closure_decisions=(_gap_decision(ClassificationOutcome.CLOSURE),),
        resolved_classifications=(_gap_resolved(ClassificationOutcome.CLOSURE),),
        approval=base.approval,
    )


def _provisional_report() -> IntegrityReport:
    return IntegrityReport(
        results=(CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, HOURLY, WINDOW),)
    )


def test_the_classification_round_trips_through_the_manifest(tmp_path: Path) -> None:
    """分類・解決済みの分類・暫定報告のダイジェストが manifest.json を往復する。"""
    manifest = _rerun_manifest(_provisional_report(), IntegrityReport())
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_manifest("snap", manifest)
    restored = store.read_manifest("snap")
    assert restored == manifest
    assert restored.closure_decisions[0].calendar_ref == "fx_ny17@v2"
    assert restored.provisional_report_ref != restored.integrity_report_ref


def test_the_manifest_is_written_with_schema_version_two(tmp_path: Path) -> None:
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_manifest("snap", snapshots.manifest())
    payload = json.loads((tmp_path / "snap" / "manifest.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert "resolved_classifications" in payload
    assert "provisional_report_ref" in payload


def test_a_version_one_manifest_is_refused(tmp_path: Path) -> None:
    """形式版 1 の manifest は読み替えずに拒否する（識別子の計算対象が異なる）。"""
    store = ParquetSnapshotStore(root=tmp_path)
    store.write_manifest("snap", snapshots.manifest())
    path = tmp_path / "snap" / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        store.read_manifest("snap")


def test_open_readable_reads_the_provisional_report(tmp_path: Path) -> None:
    """再実行した snapshot は暫定報告も読んで照合し、分類の対応を暫定報告で取る。"""
    provisional = _provisional_report()
    manifest = _rerun_manifest(provisional, IntegrityReport())
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest, report=IntegrityReport())
    store.write_provisional_report(directory, provisional)

    readable = store.open_readable(directory)
    assert readable.provisional_report == provisional
    assert (tmp_path / directory / "integrity_report_provisional.json").is_file()


def test_open_readable_refuses_a_missing_provisional_report(tmp_path: Path) -> None:
    manifest = _rerun_manifest(_provisional_report(), IntegrityReport())
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest, report=IntegrityReport())

    with pytest.raises(MarketDataValueError, match="is missing"):
        store.open_readable(directory)


def test_open_readable_refuses_an_altered_provisional_report(tmp_path: Path) -> None:
    manifest = _rerun_manifest(_provisional_report(), IntegrityReport())
    store = ParquetSnapshotStore(root=tmp_path)
    directory = _write_snapshot(store, manifest, report=IntegrityReport())
    store.write_provisional_report(
        directory,
        IntegrityReport(results=(CheckResult.create(CheckKind.UNEXPECTED_BAR, HOURLY, WINDOW),)),
    )

    with pytest.raises(MarketDataValueError, match="provisional report has been altered"):
        store.open_readable(directory)
