"""marketdata が必要とする入出力のポート（D01 §4、D03 §8）。

ポートは**利用側**の application 層が `typing.Protocol` で定義し、adapters が実装する
（依存性逆転、D01 §4）。実装側はこの定義を import せず構造的に満たす（D01 §2.2 規則7）。

- `RawBarSource`: 原 CSV の読込。列対応の宣言に従い、**文字列のまま**行を返す。Decimal 化
  と時刻の解釈は application が行う（D03 §4 の 3、ADR-0025）。
- `SnapshotStore`: snapshot の保存・読込。partition ごとの実体と manifest を扱う。
  DataFrame は adapters の外へ出さない（D01 §2.2 規則1）。
- `TickArchiveSource`: 元データの再取得（補充）で、提供元へ時間ファイル 1 本を要求し、応答を
  返す（D01 §4 v2.9、D03 §14.16）。通信・待ち・LZMA の解凍と tick の読み取り・取得時刻の
  実時計はこの実装の側にある。再試行の判断と待ち時間の計算は application の規則である。
- `RefillStore`: 補充の作業ディレクトリ・tick の保管場所・補充分の読み書き（D01 §4 v2.9）。
  書き方は D03 §14.11.1 の規則 W1〜W6 に従う（ロック、一時名に書いてから排他的に作成、
  取得記録の行のダイジェスト、完成の印、上書きしない）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.refill import (
    ArchiveProvenance,
    ArchiveRead,
    DecodedTicks,
    HourKey,
    HttpResult,
)
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest

__all__ = [
    "JournalLine",
    "RawBarSource",
    "RawFileContent",
    "RawRow",
    "RefillDirectory",
    "RefillFileStat",
    "RefillStore",
    "SnapshotStore",
    "TickArchiveSource",
]

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

    def create_directory(self, snapshot_dir: str, snapshot_id: str) -> None:
        """snapshot を書き始める前に、書き出し先のディレクトリを新しく作る（D03 §3.7.2、R4）。

        ディレクトリが既にあれば、**何も書かずに** `SnapshotAlreadyExists` で失敗する。
        ディレクトリ名が `snapshot_id` と一致しなければ `MarketDataValueError` で失敗する。
        """
        ...

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


# --- 元データの再取得（補充）（D01 §4 v2.9、D03 §14）----------------------------------


class TickArchiveSource(Protocol):
    """提供元の時間ファイルの取得ポート（D01 §4 v2.9、D03 §14.5・§14.9・§14.16）。

    要求 1 回・待ち・解凍・実時計だけを持つ。何回・いつ要求するか（間隔・再試行・指数
    バックオフ・一時停止）は application の規則が決める。
    """

    def request(self, url: str, timeout_seconds: int) -> HttpResult:
        """URL へ 1 回だけ要求し、応答（または応答が無かったこと）を返す。例外にしない。"""
        ...

    def decode(self, body: bytes) -> DecodedTicks:
        """bi5 の中身（LZMA 圧縮の `>iiiff` の列）を解凍して tick を読み取る。

        空のバイト列は tick 0 件として読む。bi5 として読めなければ `MarketDataValueError`。
        """
        ...

    def wait(self, seconds: float) -> None:
        """指定した秒数だけ待つ。"""
        ...

    def now(self) -> UtcTime:
        """いまの UTC の時刻（取得記録の行の時刻に使う）。"""
        ...


@dataclass(frozen=True, slots=True)
class JournalLine:
    """取得記録 `journal.jsonl` の 1 行を読んだ結果（D03 §14.11.1 の W3）。

    `entry` は行の内容。行が改行で終わらない・JSON として読めない・ダイジェストが合わない
    なら `None` で、`problem` に理由を持つ。`offset` は行の先頭のバイト位置（最後の行が
    不完全なとき、書き手が追記の前にここで切り詰める）。
    """

    offset: int
    entry: Mapping[str, Any] | None
    problem: str


@dataclass(frozen=True, slots=True)
class RefillDirectory:
    """補充の置き場の直下にある補充分のディレクトリ 1 つ（D03 §14.11・§14.12）。

    完成の印 `refill_manifest.json` があれば、その `plan_id` と manifest の内容（`manifest`。
    JSON として読んだもの）を持つ。無ければ書きかけで `plan_id`・`manifest` は `None`（D03
    §14.11.1 の W4・W6）。`problem` は読めなかった理由。manifest の形の検算は application が
    行う（W5）。
    """

    name: str
    plan_id: str | None
    problem: str
    manifest: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RefillFileStat:
    """補充分のディレクトリにあるファイル 1 つの検算の材料（D03 §14.11.1 の W5）。

    `sha256` は内容の sha256、`csv_rows` は内容を CSV として読んだ記録（行）の数で、空行を
    数えない（足のファイルは見出しの 1 行と足 1 本につき 1 行。D03 v1.17 §14.11.1 の W5）。
    CSV として読めなければ `None`。どちらも同じ読込のバイト列から求める。
    """

    name: str
    sha256: str
    csv_rows: int | None


class RefillStore(Protocol):
    """補充の置き場の読み書きポート（D01 §4 v2.9、D03 §14.11・§14.11.1）。

    補充の置き場（`data/raw/market/refill/`）の下だけを扱う。原データと snapshot には
    書かない（D03 §14.2 の原則2）。
    """

    def list_refills(self) -> tuple[RefillDirectory, ...]:
        """置き場の直下の補充分のディレクトリ（`_ticks`・`_work` を除く）を名前順に返す。"""
        ...

    def list_plans(self) -> tuple[str, ...]:
        """作業ディレクトリの置き場 `_work/` の直下の計画の識別子（名前順。読み取り専用）。

        名前が 16進 64 文字の、リンクでないディレクトリだけを返す。それ以外のものは
        `list_unexpected` が返す（報告の網羅性の確認。D03 v1.17 §14.15 の R4）。
        """
        ...

    def list_unexpected(self) -> tuple[str, ...]:
        """置き場の直下と `_work/` の直下にある想定外のものを、置き場からの相対パスで返す。

        想定するのは、置き場の直下の補充分（16進 64 文字の名前）・`_ticks`・`_work`
        （リンクでないディレクトリ）と、`_work/` の直下の計画（16進 64 文字の名前の、リンクで
        ないディレクトリ）。それ以外の名前・ファイル・シンボリックリンクを返す（D03 v1.17
        §14.15 の R4。読み取り専用）。補充分の名前のディレクトリでないものは `list_refills` が
        書きかけとして返すので、ここには含めない。
        """
        ...

    def work_dir_exists(self, plan_id: str) -> bool:
        """作業ディレクトリ `_work/<plan_id>/` が（書きかけを含め）あるか。"""
        ...

    def create_work_dir(self, plan_id: str) -> None:
        """作業ディレクトリを排他的に作る。既にあれば `RefillPlanAlreadyExists`（W2）。"""
        ...

    def write_plan(self, plan_id: str, payload: Mapping[str, Any]) -> None:
        """`plan.json` を一時名に書いてから排他的に作成する（W2・W4）。"""
        ...

    def read_plan(self, plan_id: str) -> Mapping[str, Any] | None:
        """`plan.json` の内容を読む。無ければ `None`（書きかけ）。読めなければ食い違い。"""
        ...

    def acquire_plan_lock(self, plan_id: str) -> None:
        """計画のロックを排他的に作る（作成時刻・ホスト・プロセス ID を書く。W1）。

        既にあれば `RefillPlanLocked`。残ったロックは自動で外さない。
        """
        ...

    def release_plan_lock(self, plan_id: str) -> None:
        """自分が作った計画のロックを外す。"""
        ...

    def read_journal(self, plan_id: str) -> tuple[JournalLine, ...]:
        """取得記録の全行を読む（無ければ空）。行ごとのダイジェストを検算する（W3）。"""
        ...

    def truncate_journal(self, plan_id: str, offset: int) -> None:
        """取得記録を `offset` バイトに切り詰める（不完全な最後の行を除くときだけ。W3）。"""
        ...

    def append_journal(self, plan_id: str, entry: Mapping[str, Any]) -> None:
        """取得記録の末尾に 1 行（内容とダイジェスト）を 1 回の書き込みで追記する（W3）。"""
        ...

    def acquire_hour_lock(self, hour: HourKey) -> bool:
        """保管場所の時間のロックを排他的に作る。取れなければ `False`（W1）。"""
        ...

    def release_hour_lock(self, hour: HourKey) -> None:
        """自分が作った時間のロックを外す。"""
        ...

    def list_archive(self, hour: HourKey, source_digest: str) -> tuple[str, ...]:
        """その時間・その設定のディレクトリにある完成したファイルの名前を返す（W4）。"""
        ...

    def read_archive(
        self, hour: HourKey, source_digest: str, tick_digest: str
    ) -> ArchiveRead | None:
        """保管場所の 1 件を読む。無ければ `None`。形が読めなければ食い違い（W5・W6）。"""
        ...

    def write_archive(self, hour: HourKey, body: bytes, provenance: ArchiveProvenance) -> str:
        """保管場所に 1 件を置き、置き場からの相対パスを返す（W2。既にあれば食い違い）。"""
        ...

    # --- 補充分（D03 §14.11・§14.11.1。書き出し `finalize` と受入れ `accept --refill`）---

    def create_refill_dir(self, refill_id: str) -> None:
        """補充分のディレクトリ `<refill_id>/` を排他的に作る（W2）。

        既にあれば（空・書きかけ・リンクを含め）何も書かずに `RefillAlreadyExists`。
        """
        ...

    def write_refill_file(self, refill_id: str, name: str, content: bytes) -> None:
        """補充分のファイル 1 つを一時名に書いてから排他的に作成する（W2）。

        既にあれば食い違い（`RefillStoreInconsistent`。上書きしない）。完成の印
        `refill_manifest.json` は呼び出し側が最後に書く（W4）。
        """
        ...

    def read_refill_manifest(self, refill_id: str) -> Mapping[str, Any] | None:
        """補充分の `refill_manifest.json` を JSON として読む。無ければ `None`（書きかけ）。"""
        ...

    def read_refill_json(self, refill_id: str, name: str) -> tuple[str, Mapping[str, Any]]:
        """補充分のファイル 1 つを JSON の object として読み、読んだバイト列の sha256 と返す。

        sha256 と内容は同じ読込から求める（検算した後の差し替えを、実際に読んだ内容で照らす
        ため。D03 §14.11.1 の W5）。無い・リンク・JSON の object として読めなければ食い違い
        （`RefillStoreInconsistent`）。
        """
        ...

    def list_refill_files(self, refill_id: str) -> tuple[RefillFileStat, ...]:
        """補充分のディレクトリにあるファイル（`refill_manifest.json` を除く）を返す。

        名前順。各ファイルの sha256 と CSV の行の数を同じ読込から求める。通常のファイルでない
        もの（ディレクトリ・リンク）や一時名（`.tmp-`）の残りがあれば食い違い
        （`RefillStoreInconsistent`。D03 v1.17 §14.11.1 の W5・W6）。
        """
        ...
