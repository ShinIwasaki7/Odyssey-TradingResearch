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

__all__ = [
    "COVERAGE_COLUMNS",
    "MINUTE_COLUMNS",
    "NEGATIVE_SPREAD_FALSE",
    "NEGATIVE_SPREAD_TRUE",
    "PARTITION_COLUMNS",
    "columns_for",
    "negative_spread_text",
    "partition_digest_hex",
    "partition_row",
]

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

#: MT5 経路の上位足に足す列（D03 §15.6 の 3）。被覆の属性を持つ足だけの列で、HistData 経路の足の
#: 行には現れない（既存の partition のダイジェストを変えないため）。
COVERAGE_COLUMNS = (
    "expected_minutes",
    "recorded_minutes",
    "first_observed_at",
    "last_observed_at",
    "time_rule_row",
)

#: MT5 経路の 1 分足に足す列（D03 §15.6 の 3。`spread` は診断用）。`negative_spread` は
#: `spread` < 0 の印（v1.25、D12-131。`spread` から導く値で、ダイジェストにも同じ並びで入る）。
MINUTE_COLUMNS = ("api_time", "time_rule_row", "spread", "negative_spread")

#: 印の列の値（他の列と同じく文字列で保存する）。
NEGATIVE_SPREAD_TRUE = "true"
NEGATIVE_SPREAD_FALSE = "false"


def negative_spread_text(negative: bool) -> str:
    """印の列の文字列（D03 §15.6 の 3 v1.25）。"""
    return NEGATIVE_SPREAD_TRUE if negative else NEGATIVE_SPREAD_FALSE


def columns_for(bar: Bar) -> tuple[str, ...]:
    """足 1 本の行の列名（属性の有無で決まる）。"""
    if bar.coverage is not None:
        return PARTITION_COLUMNS + COVERAGE_COLUMNS
    if bar.minute is not None:
        return PARTITION_COLUMNS + MINUTE_COLUMNS
    return PARTITION_COLUMNS


#: 列の区切り（ASCII の unit separator、0x1F）。価格・時刻の文字列には現れない。
_UNIT_SEPARATOR = "\x1f"

#: 行の区切り（ASCII の record separator、0x1E）。
_RECORD_SEPARATOR = b"\x1e"


def partition_row(bar: Bar) -> tuple[str, ...]:
    """足1本を、ダイジェストと保存形式で共通の文字列の列にする。

    価格・出来高は文字列のままにする。浮動小数を経由すると、書いた値と読んだ値が一致
    しなくなる（ADR-0012）。
    """
    base = (
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
    if bar.coverage is not None:
        coverage = bar.coverage
        return base + (
            str(coverage.expected_minutes),
            str(coverage.recorded_minutes),
            str(coverage.first_observed_at),
            str(coverage.last_observed_at),
            str(coverage.time_rule_row),
        )
    if bar.minute is not None:
        return base + (
            str(bar.minute.api_time),
            str(bar.minute.time_rule_row),
            str(bar.minute.spread),
            negative_spread_text(bar.minute.negative_spread),
        )
    return base


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
