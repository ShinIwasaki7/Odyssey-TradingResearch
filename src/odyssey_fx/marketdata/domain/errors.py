"""marketdata の構造エラー（D01 §8、D03 §6.1・§3.7.1）。

値・参照・能力の違反は例外で実行を止め、入力欠損（`MissingInputReason`）とは区別する
（D01 §2.2 規則5）。本パッケージの基底例外は `MarketDataError` であり、共通カーネルの
`KernelValueError`（D02 §10）を継承して「不変条件違反は ValueError 系」という扱いを
共通カーネルと揃える。

- `MarketDataValueError`: domain 型の不変条件違反（足の OHLC 整合、負の遅延など）。
- `HoldoutAccessViolation`: 許可されていない partition への読み取り要求（D03 §6.1）。
  入力欠損ではなく構造エラーであり、`MissingInput` に読み替えない。
- `SnapshotNotApproved`: 承認前・暫定の snapshot を読もうとした（D03 §3.7.1 の3）。
- `IntegrityCheckFailed`: 完全性検査に重大な違反があり受入れを中止した（D03 §4 の 4）。
- `UnsupportedCapability`: 初版が受け付けない能力の要求（D03 §3.6 の `SeededRandomDelay`）。
"""

from __future__ import annotations

from odyssey_fx.common.errors import KernelValueError

__all__ = [
    "HoldoutAccessViolation",
    "IntegrityCheckFailed",
    "MarketDataError",
    "MarketDataValueError",
    "PartitionContentMismatch",
    "SnapshotNotApproved",
    "UnsupportedCapability",
]


class MarketDataError(KernelValueError):
    """`marketdata` の構造エラーの基底（D01 §8）。"""


class MarketDataValueError(MarketDataError):
    """domain 型の不変条件違反（D03 §3）。"""


class HoldoutAccessViolation(MarketDataError):
    """許可されていない partition を読もうとした（D03 §6.1）。

    入力欠損ではない。`MissingInputPolicy` の対象にせず、実行を止める。
    """


class SnapshotNotApproved(MarketDataError):
    """承認されていない snapshot を読もうとした（D03 §3.7.1 の3）。

    暫定 snapshot（`_pending/`）と、最終ディレクトリにあるが `approval` が未記入の
    snapshot の両方がこれに当たる。
    """


class IntegrityCheckFailed(MarketDataValueError):
    """完全性検査に重大な違反があり、受入れを中止した（D03 §4 の 4）。

    重複した開始時刻、OHLC の整合違反、タイムゾーン違反、整列に合わない開始時刻は、
    いずれも構造的に無効なデータである。これらを残したまま snapshot を確定・承認できる
    経路を作らないため、暫定 manifest の生成と確定の両方で送出する。
    """


class PartitionContentMismatch(MarketDataValueError):
    """渡された足が manifest の記録と一致しない（D03 §3.7.1）。

    partition の鍵が合っていても、足数・区間・内容ダイジェストのいずれかが manifest の
    記録と違えば、その足はこの snapshot の内容ではない。暫定 snapshot や別 snapshot の足を
    承認済み snapshot の内容として読ませないための検査である。
    """


class UnsupportedCapability(MarketDataError):
    """初版が対応しない能力の要求（D03 §3.6）。"""
