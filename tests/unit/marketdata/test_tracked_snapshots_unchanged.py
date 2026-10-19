"""git で追跡している承認済み snapshot の照合が変わらないこと（D12 §12.8 の影響、D03 §15.6 v1.25）。

検査報告に種別 `NEGATIVE_SPREAD` を足し、1 分足の partition に印の列を足しても、git で追跡する
HistData 経路の承認済み snapshot（3 件）の manifest と検査報告は今の実装で読め、

- manifest の内容から再計算した識別子がディレクトリ名と一致し、
- 検査報告（最終・暫定）の再計算したダイジェストが manifest の記録と一致する。

読むのは manifest と検査報告だけ（件数・区間・ハッシュ。価格を含まない）。partition の実体（価格）は
版管理の外で、ここでは読まない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.report_digest import integrity_report_digest_hex
from odyssey_fx.marketdata.domain.integrity import CheckKind

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOTS = ROOT / "data" / "snapshots"

#: git で追跡している承認済み snapshot（HistData 経路）。
TRACKED = (
    "1092f66c999026f5551a2289b0986d0565c411cd0b0bd2c9e312dad85d9bb151",
    "a498b8cf4f90aeca1fa8af7a1e59112db197413eaa8c0a0d318eb2368a770cb3",
    "d37ec89da9365d73bbffe86407c026d94670228762494e8fd41ec4f87455e2db",
)


@pytest.mark.parametrize("snapshot_id", TRACKED)
def test_tracked_snapshots_still_verify(snapshot_id: str) -> None:
    store = ParquetSnapshotStore(root=SNAPSHOTS)
    manifest = store.read_manifest(snapshot_id)  # 記録された識別子と再計算した識別子を照合する
    assert str(manifest.snapshot_id()) == snapshot_id
    assert manifest.is_approved
    assert manifest.conversion.mt5 is None  # HistData 経路
    report = store.read_integrity_report(snapshot_id)
    assert integrity_report_digest_hex(report) == manifest.integrity_report_ref.hex
    assert not [r for r in report.results if r.kind is CheckKind.NEGATIVE_SPREAD]
    provisional = store.read_provisional_report_for(snapshot_id, manifest)
    if provisional is not None:
        assert manifest.provisional_report_ref is not None
        assert integrity_report_digest_hex(provisional) == manifest.provisional_report_ref.hex
