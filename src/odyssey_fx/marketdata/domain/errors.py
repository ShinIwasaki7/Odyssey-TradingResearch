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
- `SnapshotAlreadyExists`: 書き出し先の snapshot ディレクトリが既にある（D03 §3.7.2・§10。
  成果物の書き込みを「存在すれば失敗」に統一する規則 R4）。
- `Refill*`: 元データの再取得（補充）の失敗（D03 §14.12・§14.18 の 12。名前は 2026-10-01 の
  人間の決定）。基底は `MarketDataError` で、CLI は 1 行の失敗として表示し終了コード 1 で終える。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from odyssey_fx.common.errors import KernelValueError

if TYPE_CHECKING:
    from odyssey_fx.marketdata.domain.integrity import IntegrityReport

__all__ = [
    "HoldoutAccessViolation",
    "IntegrityCheckFailed",
    "MarketDataError",
    "MarketDataValueError",
    "PartitionContentMismatch",
    "RefillAlreadyExists",
    "RefillAlreadyFinalized",
    "RefillChainIncomplete",
    "RefillError",
    "RefillNotFetched",
    "RefillPlanAlreadyExists",
    "RefillPlanDuplicated",
    "RefillPlanEmpty",
    "RefillPlanLocked",
    "RefillPlanNotFound",
    "RefillSourceRefused",
    "RefillStoreInconsistent",
    "RefillValidationFailed",
    "SnapshotAlreadyExists",
    "SnapshotNotApproved",
    "TimeLabelCorrectionFailed",
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

    `report` は止めた理由の構造化された検査結果（件数・系列・時刻を持つ `CheckResult` の列。
    任意）。補充分と原データの重複（`DUPLICATE_TIMESTAMP`）はこれを持つ（D03 v1.17 §14.11。
    PR #59 の仮置き 12 への決定 2026-10-01: 文字列だけの例外にしない）。
    """

    def __init__(self, message: str, *, report: IntegrityReport | None = None) -> None:
        super().__init__(message)
        self.report = report


class TimeLabelCorrectionFailed(MarketDataValueError):
    """原データの時刻ラベルの宣言された補正が成り立たない（D03 §4 の v1.19 の追記）。

    列挙した週が夏時間の暦から導けない（2026-10-05 の人間の決定 DST-1）、補正した足が
    カレンダーで休場の時間帯に入る（同 DST-2）、補充分の入力 snapshot の補正規則が受入れの宣言と
    違う、再取得ツールが数えた補正の本数が manifest の記録と違う、のいずれか。宣言か入力の
    誤りであり、何も書かずに止める（構造エラー）。
    """


class PartitionContentMismatch(MarketDataValueError):
    """渡された足が manifest の記録と一致しない（D03 §3.7.1）。

    partition の鍵が合っていても、足数・区間・内容ダイジェストのいずれかが manifest の
    記録と違えば、その足はこの snapshot の内容ではない。暫定 snapshot や別 snapshot の足を
    承認済み snapshot の内容として読ませないための検査である。
    """


class UnsupportedCapability(MarketDataError):
    """初版が対応しない能力の要求（D03 §3.6）。"""


class SnapshotAlreadyExists(MarketDataError):
    """書き出し先の snapshot ディレクトリが既にある（D03 §3.7.2・§10、R4）。

    成果物の書き込みは「存在すれば、何も書かずに失敗する」。暫定ディレクトリ
    （`_pending/<provisional_id>/`）も確定ディレクトリ（`<snapshot_id>/`）も同じ規則で、
    置換の指示は持たない。作り直すときは人間が先にそのディレクトリを移動または削除する。
    """


# --- 元データの再取得（補充）の失敗（D03 §14.12・§14.18 の 12）------------------------


class RefillError(MarketDataError):
    """再取得ツールの失敗の基底（D03 §14.12）。

    状態×出来事表（D03 §14.12）の「拒否」はすべてこの派生型で表す。何も書かずに止まる
    （書きかけを残さない）ことが、どの派生型にも共通する約束である。
    """


class RefillPlanNotFound(RefillError):
    """指定した取得計画の作業ディレクトリが無い（D03 §14.12 の生成前×出来事2・9・10）。"""


class RefillPlanEmpty(RefillError):
    """対象足が 1 本も無いので計画を作らない（D03 §14.4）。"""


class RefillPlanAlreadyExists(RefillError):
    """同じ `plan_id` の作業ディレクトリが既にある（D03 §14.12 の出来事1、「存在すれば失敗」）。"""


class RefillPlanLocked(RefillError):
    """計画のロックを取れない（D03 §14.9・§14.11.1 の W1）。

    別のコマンドが同じ計画を扱っているか、止まったプロセスのロックが残っている。残った
    ロックは自動で外さない。人間が書き手のプロセスが無いことを確かめて消す（W1）。
    """


class RefillNotFetched(RefillError):
    """最終結果の無い時間ファイルが残っているので書き出せない（D03 §14.12 の出来事10）。"""


class RefillAlreadyFinalized(RefillError):
    """この計画の補充分は書き出し済みである（D03 §14.12 の書き出し済みの行）。"""


class RefillAlreadyExists(RefillError):
    """書き出し先の補充分のディレクトリが既にある（D03 §14.11.1 の W2）。"""


class RefillValidationFailed(RefillError):
    """検証が不合格で、取り直す時間ファイルも無い（D03 §14.12 の不合格×出来事9）。"""


class RefillChainIncomplete(RefillError):
    """受入れに渡した補充分の集合が、入力 snapshot の補充分をすべて含まない（D03 §14.11）。"""


class RefillStoreInconsistent(RefillError):
    """保管場所・作業ディレクトリ・補充分の検算が合わない（D03 §14.11.1 の W5・W6）。

    自動で直さない・置き換えない・消さない。食い違ったパスと理由を表示して止まり、人間が
    `data/raw/market/refill/` の下のそのものを消してからやり直す（W6）。
    """


class RefillSourceRefused(RefillError):
    """提供元が認証・権限・要求の誤りを示す応答（404・429 以外の 4xx）を返した。

    欠落として取得を続けず、計画を止めて原因（URL・HTTP の状態）を表示する（PR #58 の仮置き
    の 3 への人間の修正指示 2026-10-01。型の名前は仮置き）。その時間には最終結果を書かないので、
    設定を直してから同じ計画で `fetch` を再開すれば取り直す。
    """


class RefillPlanDuplicated(RefillError):
    """同じ `plan_id` の補充分を 2 つ以上、受入れに渡した（D03 §14.11）。"""
