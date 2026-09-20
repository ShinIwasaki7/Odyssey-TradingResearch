"""partition の内容ダイジェスト（D03 §3.7.1）。

partition の実体が「その snapshot の manifest が記録しているものと同じか」を確かめるための
ダイジェストを、足そのものから計算する。保存形式（Parquet）のバイト列ではなく**論理的な
内容**から取るので、圧縮設定やライブラリの版が変わっても、同じ足なら同じ値になる。

`adapters` ではなく `application` に置く理由は、読み取り側（as-of ビュー・公開フィード）が
同じ算法で照合する必要があるためである。adapters に閉じていると、書いた側だけがダイジェスト
を作れて、読む側は「鍵が合っているか」しか確かめられない。それでは、暫定 snapshot や別
snapshot の足を、承認済み manifest の内容として読ませることができてしまう（D03 §3.7.1）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from odyssey_fx.marketdata.domain.bar import Bar

__all__ = ["PARTITION_COLUMNS", "partition_digest_hex", "partition_row"]

#: ダイジェストに入れる列の順序。保存形式の列順と同じで、変えると既存の記録と一致しなくなる。
PARTITION_COLUMNS = (
    "bar_start",
    "bar_end",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "available_at",
    "provenance_kind",
    "provenance_ref",
)

#: 列の区切り（ASCII の unit separator、0x1F）。価格・時刻の文字列には現れない。
_UNIT_SEPARATOR = "\x1f"

#: 行の区切り（ASCII の record separator、0x1E）。
_RECORD_SEPARATOR = b"\x1e"


def partition_row(bar: Bar) -> tuple[str, ...]:
    """足1本を、ダイジェストと保存形式で共通の文字列の列にする。

    価格・出来高は文字列のままにする。浮動小数を経由すると、書いた値と読んだ値が一致
    しなくなる（ADR-0012）。
    """
    return (
        str(bar.bar_start),
        str(bar.bar_end),
        str(bar.open.value),
        str(bar.high.value),
        str(bar.low.value),
        str(bar.close.value),
        str(bar.volume),
        str(bar.available_at),
        bar.provenance.kind.value,
        bar.provenance.source_ref,
    )


def partition_digest_hex(bars: Iterable[Bar]) -> str:
    """partition の足の内容ダイジェスト（16進 64 文字、D03 §3.7.1）。

    足は開始時刻の昇順に整列してから符号化するので、渡す順序は結果に影響しない
    （受入れの決定論性、D03 §4）。
    """
    hasher = hashlib.sha256()
    for bar in sorted(bars, key=lambda item: item.bar_start.value):
        hasher.update(_UNIT_SEPARATOR.join(partition_row(bar)).encode("utf-8"))
        hasher.update(_RECORD_SEPARATOR)
    return hasher.hexdigest()
