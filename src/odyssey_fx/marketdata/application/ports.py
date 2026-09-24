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
from dataclasses import dataclass
from typing import Protocol

from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest

__all__ = ["RawBarSource", "RawFileContent", "RawRow", "SnapshotStore"]

#: 原ファイルの1行。列名から**文字列**への mapping（D03 §4 の 3）。
#: adapters は値を解釈せず、時刻も価格も文字列のまま渡す。時刻の見た目から規約を推測
#: しないため（上位設計書 §3.2）。
RawRow = Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RawFileContent:
    """1ファイルを1回読んだ結果（D03 §4 の 1〜3）。

    内容のダイジェストと行を**同じバイト列から**作る。別々に読むと、その間にファイルが
    差し替わったときに manifest の出所の記録（sha256・行数）が実データと食い違い、
    「記録どおりでない snapshot」ができてしまう。
    """

    sha256: str
    rows: tuple[RawRow, ...]


class RawBarSource(Protocol):
    """原データの読込ポート（D01 §4、D03 §8）。"""

    def read_file(self, path: str) -> RawFileContent:
        """1ファイルを1回読み、内容の sha256 と全行を同時に返す。

        値の解釈（時刻・Decimal 化）は行わない。`path` は原データの基点からの相対パス。
        ダイジェストと行が同じ読込に由来することを保証するのがこのポートの役目である。
        """
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

    def write_provisional_report(self, snapshot_dir: str, report: IntegrityReport) -> str:
        """暫定段階の検査報告（人間が分類の根拠にした報告）を書き出す（D03 §3.7 v1.7）。

        確定段階で 5〜7 を再実行し、最終報告と異なる場合だけ書く。内容のダイジェスト
        （16進 64 文字）を返す。
        """
        ...
