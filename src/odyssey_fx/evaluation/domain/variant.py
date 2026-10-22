"""標準・候補の組（D13 §6.4、D09 §3・§6.2 の 2026-10-21 改訂。決定 D13-15 (a)）。

1 回の探索の実験は、承認済みの 2 つの snapshot（時刻の規則の標準の組と候補の組。D12-92 (a)）を
同じ設定・同じ fold でそれぞれ評価する。組は `Variant`（値は実験設定のキーと同じ `standard` /
`candidate`。実装 PR の仮置き 46、2026-10-22 の人間の決定）で表し、組ごとの値は `Paired`
（2 組の値の組。正規化エンコードは `{"standard": …, "candidate": …}` になり、実験設定・研究
ポリシーの `snapshots` の形と同じ）で持つ。組の順は標準 → 候補（改訂 PR の仮置き 35 (a)）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError

__all__ = ["VARIANTS", "Paired", "Variant"]


class Variant(Enum):
    """組（D13 §6.4。仮置き 46）。宣言順が実行と記録の順（標準 → 候補）。"""

    STANDARD = "standard"
    CANDIDATE = "candidate"


#: 組の順（D09 §5.4 の 2026-10-21 改訂。改訂 PR の仮置き 35 (a)）。
VARIANTS: Final[tuple[Variant, ...]] = (Variant.STANDARD, Variant.CANDIDATE)


@dataclass(frozen=True, slots=True)
class Paired[ValueT]:
    """組ごとの値（標準の組と候補の組の 2 つ。D13 §4.6 の表 3 の主キーの `variant`）。"""

    standard: ValueT
    candidate: ValueT

    def of(self, variant: Variant) -> ValueT:
        """組の値。"""
        if variant is Variant.STANDARD:
            return self.standard
        if variant is Variant.CANDIDATE:
            return self.candidate
        raise KernelValueError(f"unknown variant {variant!r}")

    def items(self) -> tuple[tuple[Variant, ValueT], ...]:
        """`(組, 値)` を組の順（標準 → 候補）に。"""
        return ((Variant.STANDARD, self.standard), (Variant.CANDIDATE, self.candidate))

    def require(self, value_type: type, label: str) -> None:
        """両組の値が `value_type` であることを確かめる（構造エラーは `KernelValueError`）。"""
        for variant, value in self.items():
            if not isinstance(value, value_type):
                raise KernelValueError(
                    f"{label}.{variant.value} must be a {value_type.__name__}, got {value!r}"
                )

    def map[OtherT](self, function: Callable[[Variant, ValueT], OtherT]) -> Paired[OtherT]:
        """組ごとに関数を当てた値。"""
        return Paired(
            standard=function(Variant.STANDARD, self.standard),
            candidate=function(Variant.CANDIDATE, self.candidate),
        )

    @staticmethod
    def build[OtherT](function: Callable[[Variant], OtherT]) -> Paired[OtherT]:
        """組ごとに値を作る（標準 → 候補の順に呼ぶ）。"""
        standard = function(Variant.STANDARD)
        candidate = function(Variant.CANDIDATE)
        return Paired(standard=standard, candidate=candidate)
