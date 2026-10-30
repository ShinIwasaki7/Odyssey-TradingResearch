"""関所の最終評価の許可のうち、読み取りの面へ渡す部分（D09 §9.10.3 の 4、要決定 R2-3 (a)）。

最終検証の窓の partition は、取得の回 × 通貨 × 組ごとの複数の snapshot にまたがる（D03 §16.3.3）。
要決定 R2-3 の決定 (a)「許可に snapshot と partition の並びを持たせる」により、関所が発行する
最終評価の許可の値（`evaluation.domain.final_validation.FinalEvaluationPermit`）は、系列ごとに
「snapshot と partition の並び」を持ち、as-of ビュー・執行系列ビュー・`SnapshotStore.read_partition`
はその並び（本モジュールの `FinalReadGrant`）を読む（D03 §6.1 の改訂。D09 §16.4 の 3）。

同じ組・同じ系列・同じ週の partition が 2 つ以上の承認済み snapshot にあるとき、読むのは最初に
承認された snapshot の 1 つだけで、消費はすべてに行う（仮置き R2-9）。したがって並びの中の
partition の識別子は重ならない（読む側は partition の識別子から読む snapshot が 1 つに決まる）。
並びに入るのは OANDA MT5 の経路の 2 つの封印の区分の partition だけ（研究履歴の partition は
記録票の snapshot から読む。D09 §9.10.3 の 8）。
"""

from __future__ import annotations

from dataclasses import dataclass

from odyssey_fx.marketdata.domain.access import OANDA_SEALED_CLASSES
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = ["FinalReadGrant", "GrantedRead", "require_ordered_reads"]


@dataclass(frozen=True, slots=True)
class GrantedRead:
    """最終評価の許可が読ませる partition 1 つと、それを読む snapshot（最終の識別子）。"""

    snapshot_id: str
    partition_id: PartitionId

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id:
            raise MarketDataValueError("GrantedRead.snapshot_id must be a non-empty str")
        if self.snapshot_id.startswith("_") or "/" in self.snapshot_id:
            raise MarketDataValueError(
                f"GrantedRead.snapshot_id {self.snapshot_id!r} is not a finalized snapshot id"
            )
        if not isinstance(self.partition_id, PartitionId):
            raise MarketDataValueError("GrantedRead.partition_id must be a PartitionId")
        if self.partition_id.access_class not in OANDA_SEALED_CLASSES:
            raise MarketDataValueError(
                f"a final-evaluation grant reads only the OANDA MT5 sealed classes, got"
                f" {self.partition_id} (D09 §9.10.3 の 8)"
            )


@dataclass(frozen=True, slots=True)
class FinalReadGrant:
    """系列ごとの「snapshot と partition の並び」（要決定 R2-3 (a)）。

    `reads` は系列（文字列の順）→ 期間の始まりの順に並ぶ。partition の識別子は重ならない。
    """

    permit_id: str
    reads: tuple[GrantedRead, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.permit_id, str) or not self.permit_id:
            raise MarketDataValueError("FinalReadGrant.permit_id must be a non-empty str")
        if not isinstance(self.reads, tuple) or not all(
            isinstance(item, GrantedRead) for item in self.reads
        ):
            raise MarketDataValueError("FinalReadGrant.reads must be a tuple of GrantedRead")
        if not self.reads:
            raise MarketDataValueError("a final-evaluation grant reads at least one partition")
        seen: set[PartitionId] = set()
        for item in self.reads:
            if item.partition_id in seen:
                raise MarketDataValueError(
                    f"the partition {item.partition_id} is granted twice; one snapshot is read"
                    " for each partition (仮置き R2-9)"
                )
            seen.add(item.partition_id)
        ordered = sorted(
            self.reads,
            key=lambda item: (str(item.partition_id.series), _start(item.partition_id)),
        )
        if list(self.reads) != ordered:
            raise MarketDataValueError(
                "FinalReadGrant.reads are ordered by series, then by the period start"
            )

    @property
    def partitions(self) -> frozenset[PartitionId]:
        """読ませる partition の識別子。"""
        return frozenset(item.partition_id for item in self.reads)

    @property
    def snapshot_ids(self) -> frozenset[str]:
        """読む snapshot の識別子。"""
        return frozenset(item.snapshot_id for item in self.reads)

    def source_of(self, partition_id: PartitionId) -> str | None:
        """`partition_id` を読む snapshot の識別子（許可に無ければ `None`）。"""
        for item in self.reads:
            if item.partition_id == partition_id:
                return item.snapshot_id
        return None

    def covers(self, snapshot_id: str, partition_id: PartitionId) -> bool:
        """`snapshot_id` の `partition_id` を読ませるか。"""
        return self.source_of(partition_id) == snapshot_id


def _start(partition_id: PartitionId) -> str:
    start = partition_id.period_start
    return "" if start is None else start.value.isoformat()


def require_ordered_reads(reads: object, label: str) -> None:
    """並びの形の規則（仮置き R2-9・R4b-2。`FinalReadGrant.reads` と同じ規則）。

    要素は `GrantedRead`、partition の識別子は重ならず、系列の順・同じ系列の中は期間の始まりの順。
    外れれば `MarketDataValueError`。run の設定の並び（`RunConfig.sealed_reads`。D06 v1.18 案 §3）
    が使う（空の並びも受け付ける）。
    """
    if not isinstance(reads, tuple) or not all(isinstance(item, GrantedRead) for item in reads):
        raise MarketDataValueError(f"{label} must be a tuple of GrantedRead")
    seen: set[PartitionId] = set()
    for item in reads:
        if item.partition_id in seen:
            raise MarketDataValueError(
                f"the partition {item.partition_id} appears twice in {label}; one snapshot is"
                " read for each partition (仮置き R2-9)"
            )
        seen.add(item.partition_id)
    ordered = sorted(
        reads, key=lambda item: (str(item.partition_id.series), _start(item.partition_id))
    )
    if list(reads) != ordered:
        raise MarketDataValueError(f"{label} are ordered by series, then by the period start")
