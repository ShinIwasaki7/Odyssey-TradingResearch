"""原 CSV の読込（D03 §8、ADR-0025）。

`RawBarSource`（`marketdata.application.ports`）を実装する。**ポートの Protocol 定義そのものは
import せず、構造的に満たす**（D01 §2.2 規則7）。読込結果の型（`RawFileContent`）だけは、
application と同じ値を受け渡すために import する（`parquet_store` が partition の算法を
import するのと同じ扱い）。

**値を解釈しない**のがこの adapters の約束である（D03 §8）。polars で CSV を読むが、
全列を文字列として読み、時刻も価格も文字列のまま application へ渡す。Decimal 化と時刻の
解釈は application が行う（ADR-0012 の「Decimal は文字列から構築」、上位設計書 §3.2 の
「時刻の見た目から規約を推測しない」）。DataFrame はこのモジュールの外へ出さない
（D01 §2.2 規則1）。

原 CSV の先頭列は列名を持たない。polars は無名の先頭列に `` という名前を与えるので、
読込時に列対応の宣言が指す名前（既定は `timestamp`）へ付け替える。
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from odyssey_fx.marketdata.application.ports import RawFileContent, RawRow

__all__ = ["CsvRawBarSource"]

#: polars が無名の先頭列に与える名前。
_UNNAMED_FIRST_COLUMN = ""


@dataclass(frozen=True, slots=True)
class CsvRawBarSource:
    """結合済み CSV から行を読む（D03 §8）。

    `root` は原データの基点ディレクトリ（`data/raw/market/` など）。`read_rows` に渡す
    パスはこの基点からの相対パスで、基点の外へ出る指定は拒否する。

    `time_column` は、無名の先頭列に与える名前。列対応の宣言（`ColumnMapping.time_column`）
    と同じ値を渡す。

    読込は `read_file` の1つだけで、**内容のダイジェストと行を同じバイト列から**作る
    （D03 §3.7.1 の「同じ原ファイルなら同じ識別子」を、読込の間のファイル差し替えに対しても
    保つため）。
    """

    root: Path
    time_column: str = "timestamp"

    def _resolve(self, path: str) -> Path:
        """基点からの相対パスを解決する。基点の外を指す指定は拒否する。"""
        root = self.root.resolve()
        candidate = (root / path).resolve()
        if root != candidate and root not in candidate.parents:
            raise ValueError(f"{path!r} resolves outside the raw data root {root}")
        if not candidate.is_file():
            raise FileNotFoundError(f"raw file not found: {candidate}")
        return candidate

    def read_file(self, path: str) -> RawFileContent:
        """1ファイルを**1回だけ**読み、内容の sha256 と全行を同時に返す。

        バイト列を読んでからダイジェストを取り、同じバイト列を polars へ渡す。ダイジェストと
        行を別々の読込から作ると、その間にファイルが差し替わったときに manifest の出所の
        記録が実データと食い違い、「記録どおりでない snapshot」ができてしまう。

        全列を文字列として読む（`infer_schema=False`）。数値へ推論させると、価格が
        二進浮動小数を経由して Decimal の厳密さを失うため（ADR-0012）。
        """
        content = self._resolve(path).read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        frame = pl.read_csv(io.BytesIO(content), infer_schema=False, has_header=True)
        if _UNNAMED_FIRST_COLUMN in frame.columns:
            frame = frame.rename({_UNNAMED_FIRST_COLUMN: self.time_column})
        rows: list[RawRow] = []
        for record in frame.iter_rows(named=True):
            rows.append(
                {name: "" if value is None else str(value) for name, value in record.items()}
            )
        # DataFrame はここで捨てる。外へ出すのは Python の組込み型だけ（D01 §2.2 規則1）。
        return RawFileContent(sha256=digest, rows=tuple(rows))
