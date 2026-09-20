"""marketdata が必要とする入出力のポート（D01 §4、D03 §8）。

ポートは**利用側**の application 層が `typing.Protocol` で定義し、adapters が実装する
（依存性逆転、D01 §4）。実装側はこの定義を import せず構造的に満たす（D01 §2.2 規則7）。

- `RawBarSource`: 原 CSV の読込。列対応の宣言に従い、**文字列のまま**行を返す。Decimal 化
  と時刻の解釈は application が行う（D03 §4 の 3、ADR-0025）。
- `SnapshotStore`: snapshot の保存・読込。partition ごとの実体と manifest を扱う。
  DataFrame は adapters の外へ出さない（D01 §2.2 規則1）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest

__all__ = ["RawBarSource", "RawRow", "SnapshotStore"]

#: 原ファイルの1行。列名から**文字列**への mapping（D03 §4 の 3）。
#: adapters は値を解釈せず、時刻も価格も文字列のまま渡す。時刻の見た目から規約を推測
#: しないため（上位設計書 §3.2）。
RawRow = Mapping[str, str]


class RawBarSource(Protocol):
    """原データの読込ポート（D01 §4、D03 §8）。"""

    def read_rows(self, path: str) -> Sequence[RawRow]:
        """1ファイルの全行を、宣言された列名の文字列 mapping として読む。

        値の解釈（時刻・Decimal 化）は行わない。`path` はリポジトリからの相対パス。
        """
        ...

    def file_sha256(self, path: str) -> str:
        """ファイル内容の sha256（16進 64 文字）。manifest の `sources` に記録する。"""
        ...


class SnapshotStore(Protocol):
    """snapshot の保存・読込ポート（D01 §4、D03 §8）。"""

    def write_partition(
        self, snapshot_dir: str, partition_id: PartitionId, bars: Iterable[Bar]
    ) -> str:
        """partition の足を書き出し、内容のダイジェスト（16進 64 文字）を返す。"""
        ...

    def read_partition(self, snapshot_dir: str, partition_id: PartitionId) -> Sequence[Bar]:
        """partition の足を昇順で読む。"""
        ...

    def write_manifest(self, snapshot_dir: str, manifest: SnapshotManifest) -> None:
        """`manifest.json` を D03 §3.7.1 の正規順序で書く。"""
        ...

    def read_manifest(self, snapshot_dir: str) -> SnapshotManifest:
        """`manifest.json` を読む。"""
        ...

    def write_integrity_report(self, snapshot_dir: str, report: IntegrityReport) -> str:
        """完全性検査の報告を書き出し、内容のダイジェスト（16進 64 文字）を返す。"""
        ...
