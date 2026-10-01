"""補充の検証 5 点の結果の型（D03 §14.7・§14.8）。

検証の規則は `marketdata.application.refill_validation` にある。本モジュールは結果を表す
不変の値だけを持つ。合否に使う項目（不合格の理由）と、合否に使わず記録だけする項目
（照合の件数・bid が ask より大きい tick の件数・前後の足との差と「要確認」の印・1時間足と
15分足 4 本の一致）を分けて持つ（D03 §14.7 の表）。

前後の足との差は研究履歴区分の足どうしだけで求める（対象足を研究履歴区分に限り、比べる相手も
同じ区分に限る。D03 §14.4 の 4 と v1.15 の第1巡の指摘への対処）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import ArchiveProvenance, FinalResult, HourKey
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "HourlyConsistency",
    "NeighborSide",
    "NeighborStatus",
    "NeighborCheck",
    "NotBuiltReason",
    "NotBuiltBar",
    "ReconciledBar",
    "RefillValidation",
    "UnreconciledChunk",
    "UsedHour",
]


class NotBuiltReason(Enum):
    """対象足を作らなかった理由（D03 §14.8 の表）。"""

    #: 時間ファイルが `NOT_FETCHED`（報告では「取得できなかった」。HTTP 404 を含む）。
    HOUR_NOT_FETCHED = "HOUR_NOT_FETCHED"
    #: 時間ファイルが `FETCHED_EMPTY`（提供元に tick なし）。
    PROVIDER_EMPTY = "PROVIDER_EMPTY"
    #: 時間ファイルは `FETCHED` だが、その足の区間に tick が無い（区間に tick なし）。
    NO_TICK_IN_BAR = "NO_TICK_IN_BAR"
    #: 塊が未照合（判断待ち。D03 §14.4）。
    UNRECONCILED = "UNRECONCILED"


@dataclass(frozen=True, slots=True)
class NotBuiltBar:
    """作らなかった対象足 1 本と理由（D03 §14.8）。`detail` は取得できなかった理由など。"""

    series: SeriesId
    start: UtcTime
    reason: NotBuiltReason
    detail: str = ""

    def sort_key(self) -> tuple[str, str]:
        """整列鍵 `(系列の文字列, 開始時刻)`。"""
        return (str(self.series), str(self.start))


@dataclass(frozen=True, slots=True)
class UnreconciledChunk:
    """未照合の塊の記録 `(系列, 塊の開始時刻, 対象足の数)`（D03 §14.4・§14.8）。"""

    series: SeriesId
    chunk_start: UtcTime
    target_count: int

    def __post_init__(self) -> None:
        if isinstance(self.target_count, bool) or not isinstance(self.target_count, int):
            raise MarketDataValueError("UnreconciledChunk.target_count must be an int")
        if self.target_count < 1:
            raise MarketDataValueError("UnreconciledChunk.target_count must be >= 1")

    def sort_key(self) -> tuple[str, str]:
        """整列鍵 `(系列の文字列, 塊の開始時刻)`。"""
        return (str(self.series), str(self.chunk_start))


@dataclass(frozen=True, slots=True)
class ReconciledBar:
    """照合用の足 1 本の照合結果（D03 §14.7 の 1・2）。

    原データに実在する足を、同じ規則で tick から作って比べたもの。`differences` は
    `(項目, tick から作った値 − 原データの値)` の列で、一致すれば空。tick から足を作れな
    かった（区間に tick が無い）ときは `built=False` で、一致しないものとして数える。
    """

    series: SeriesId
    start: UtcTime
    hour: HourKey
    built: bool
    differences: tuple[tuple[str, Decimal], ...]

    @property
    def matched(self) -> bool:
        """原データと始値・高値・安値・終値がすべて一致したか（差 0 を一致とする）。"""
        return self.built and not self.differences

    def sort_key(self) -> tuple[str, str]:
        """整列鍵 `(系列の文字列, 開始時刻)`。"""
        return (str(self.series), str(self.start))


class NeighborSide(Enum):
    """前後の足との比較の向き（D03 §14.7 の 3）。"""

    BEFORE = "BEFORE"
    AFTER = "AFTER"


class NeighborStatus(Enum):
    """前後の足との比較の結果の種類（D03 §14.7 の 3）。"""

    #: 比べた（差を記録した）。
    COMPARED = "COMPARED"
    #: 直前・直後の足が研究履歴区分の外なので比べない（差も値も記録しない）。
    OUTSIDE_RESEARCH_HISTORY = "OUTSIDE_RESEARCH_HISTORY"
    #: 原データに直前・直後の足が無い。
    NO_NEIGHBOR = "NO_NEIGHBOR"
    #: 塊の足を 1 本も作らなかったので比べる足が無い。
    NOTHING_BUILT = "NOTHING_BUILT"


@dataclass(frozen=True, slots=True)
class NeighborCheck:
    """対象足の連続する塊 1 つの、直前または直後の足との比較（D03 §14.7 の 3。合否に使わない）。

    `difference` は「補充した最初の足の始値 − 直前の足の終値」または「直後の足の始値 −
    補充した最後の足の終値」、`difference_pips` はそれを pip で表したもの。`crosses_closure`
    は直前・直後の足とのあいだに休場を挟むか、`needs_review` は差が表示閾値（10 pip）を
    超えた「要確認」の印。閾値以下も含めてすべての塊の差を記録する（D03 §14.18 の 7）。
    """

    series: SeriesId
    chunk: Interval
    target_count: int
    side: NeighborSide
    status: NeighborStatus
    neighbor_start: UtcTime | None
    crosses_closure: bool | None
    difference: Decimal | None
    difference_pips: Decimal | None
    needs_review: bool

    def __post_init__(self) -> None:
        compared = self.status is NeighborStatus.COMPARED
        values = (self.neighbor_start, self.crosses_closure, self.difference, self.difference_pips)
        if compared != all(value is not None for value in values):
            raise MarketDataValueError(
                "NeighborCheck records the neighbor and the difference exactly when compared"
            )
        if not compared and any(value is not None for value in values):
            raise MarketDataValueError(
                "NeighborCheck records no neighbor values unless compared (D03 §14.7 の 3)"
            )
        if self.needs_review and not compared:
            raise MarketDataValueError("only a compared chunk can need review")

    def sort_key(self) -> tuple[str, str, str]:
        """整列鍵 `(系列の文字列, 塊の開始時刻, 向き)`。"""
        return (str(self.series), str(self.chunk.start), self.side.value)


@dataclass(frozen=True, slots=True)
class HourlyConsistency:
    """同じ時間の補充した 1時間足が 15分足 4 本の集約と一致するか（合否に使わない）。"""

    hour: HourKey
    consistent: bool

    def sort_key(self) -> tuple[str, str]:
        """整列鍵 `(銘柄, 時刻)`。"""
        return self.hour.sort_key()


@dataclass(frozen=True, slots=True)
class UsedHour:
    """検証に使った時間ファイル 1 本の出所（D03 §14.7 の 5）。

    `final` は取得記録の有効な最終結果、`provenance` は保管場所のファイルに記録された取得の
    出所（保管場所から読んだ時間ファイルも、同じ設定での実際の取得の出所を使う）。
    `NOT_FETCHED` の時間ファイルは保管されないので `provenance` は `None`。
    """

    final: FinalResult
    provenance: ArchiveProvenance | None

    def sort_key(self) -> tuple[str, str]:
        """整列鍵 `(銘柄, 時刻)`。"""
        return self.final.hour.sort_key()


@dataclass(frozen=True, slots=True)
class RefillValidation:
    """検証 5 点の結果（D03 §14.7）。

    `failures` が空なら合格。`built_bars` は補充した足（対象足だけ。系列・開始時刻の順）。
    `not_built` は作らなかった対象足と理由、`unreconciled` は未照合の塊。残りは合否に使わない
    記録（検証記録 `validation.json` の材料。書き出しは後続の実装）。
    """

    failures: tuple[str, ...]
    built_bars: tuple[Bar, ...]
    not_built: tuple[NotBuiltBar, ...]
    unreconciled: tuple[UnreconciledChunk, ...]
    reconciled: tuple[ReconciledBar, ...]
    out_of_range_tick_count: int
    bid_above_ask_count: int
    neighbors: tuple[NeighborCheck, ...]
    hourly_consistency: tuple[HourlyConsistency, ...]
    used_hours: tuple[UsedHour, ...]

    @property
    def passed(self) -> bool:
        """合格か（不合格の理由が 1 つも無い）。"""
        return not self.failures

    @property
    def reconciled_count(self) -> int:
        """照合した足の数（D03 §14.7 の 1）。"""
        return len(self.reconciled)

    @property
    def matched_count(self) -> int:
        """照合して一致した足の数（D03 §14.7 の 1）。"""
        return sum(1 for record in self.reconciled if record.matched)
