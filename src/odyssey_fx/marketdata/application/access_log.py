"""封印期間の閲覧記録と状態の導出（D03 §3.8、ADR-0014）。

封印期間（`LEGACY_HOLDOUT`）の partition が「未使用のまま（`SEALED`）」か「使用済み
（`CONSUMED`）」かは、manifest に直接書き換えるフィールドを持たず、**追記専用の閲覧記録**
（`data/snapshots/<snapshot_id>/access_log.jsonl`、git 管理）から導出する。

`SEALED → CONSUMED` は不可逆で、逆遷移は存在しない。したがって導出は単純で、「消費の記録が
1件でもあれば `CONSUMED`、なければ初期状態のまま」になる。初期状態は旧基盤の履歴
（manifest の `legacy_access`）が決め、**未観測と確認できた partition だけ** `SEALED` に
する。使用済み、または履歴不明の partition は `CONSUMED` とする（fail-closed）。

本モジュールが担うのは記録の追記と状態の導出だけで、許可の発行・origin への push を伴う
封印解除の手順（fail-closed な gate）は D07（`evaluation.application.holdout_gate`）が
実装する（D03 §3.8）。記録の行は1行1 JSON（JSON Lines）で、追記した順に並ぶ。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.access import AccessClass, HoldoutState
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.snapshot import (
    LegacyAccessRecord,
    LegacyObservation,
    PartitionId,
)

__all__ = [
    "AccessLogEntry",
    "AccessLogEntryKind",
    "append_entry",
    "derive_holdout_state",
    "initial_holdout_state",
    "serialize_entry",
]


class AccessLogEntryKind(Enum):
    """閲覧記録の種別（ADR-0014）。

    - `GRANTED`: 許可が発行された（まだデータは公開されていない）。
    - `CONSUMED`: 消費が確定した。この記録があれば partition は `CONSUMED`。
    - `RESEARCH_OPT_IN`: 既に使用済みの partition を、研究・開発用途として明示的に
      読んだ記録。holdout 成績としての集計は禁止される。
    """

    GRANTED = "GRANTED"
    CONSUMED = "CONSUMED"
    RESEARCH_OPT_IN = "RESEARCH_OPT_IN"


@dataclass(frozen=True, slots=True)
class AccessLogEntry:
    """閲覧記録1件（ADR-0014、D03 §3.7）。

    記録は追記専用なので、この型に「取り消し」は存在しない。`recorded_at` は記録時刻で、
    `snapshot_id` の計算には入らない（D03 §3.7.1）。
    """

    kind: AccessLogEntryKind
    partition_id: PartitionId
    recorded_at: UtcTime
    actor: str
    purpose: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AccessLogEntryKind):
            raise MarketDataValueError("AccessLogEntry.kind must be an AccessLogEntryKind")
        if not isinstance(self.partition_id, PartitionId):
            raise MarketDataValueError("AccessLogEntry.partition_id must be a PartitionId")
        if not isinstance(self.recorded_at, UtcTime):
            raise MarketDataValueError("AccessLogEntry.recorded_at must be a UtcTime")
        if not isinstance(self.actor, str) or not self.actor:
            raise MarketDataValueError("AccessLogEntry.actor must be a non-empty str")
        if not isinstance(self.purpose, str):
            raise MarketDataValueError("AccessLogEntry.purpose must be a str")


def serialize_entry(entry: AccessLogEntry) -> Mapping[str, str]:
    """1件の記録を、JSON Lines へ書ける文字列の mapping へ変換する。

    実際のファイル追記は adapters が行う（application は I/O を持たない、D01 §2）。
    キーは固定で、値はすべて文字列にする（数値の表現差で記録が揺れないようにするため）。
    """
    return {
        "actor": entry.actor,
        "kind": entry.kind.value,
        "partition": str(entry.partition_id),
        "purpose": entry.purpose,
        "recorded_at": str(entry.recorded_at),
    }


def append_entry(
    entries: Sequence[AccessLogEntry], entry: AccessLogEntry
) -> tuple[AccessLogEntry, ...]:
    """記録を末尾へ追記する（追記専用、ADR-0014）。

    既存の記録は並べ替えも削除もしない。同じ partition に対する2度目の消費記録も拒否
    しない（`CONSUMED` は冪等な終端状態であり、重複した記録は履歴として残す）。
    """
    if not isinstance(entry, AccessLogEntry):
        raise MarketDataValueError("append_entry requires an AccessLogEntry")
    return (*entries, entry)


def initial_holdout_state(
    partition_id: PartitionId, legacy_access: Sequence[LegacyAccessRecord]
) -> HoldoutState:
    """旧基盤の履歴から封印期間 partition の初期状態を決める（ADR-0014）。

    **未観測と確認できた場合だけ** `SEALED`。記録がない、観測済み、履歴不明のいずれも
    `CONSUMED` に倒す（fail-closed）。
    """
    if partition_id.access_class is not AccessClass.LEGACY_HOLDOUT:
        raise MarketDataValueError(
            f"only LEGACY_HOLDOUT partitions carry a holdout state, got"
            f" {partition_id.access_class.value} for {partition_id} (D03 §3.8)"
        )
    relevant = [record for record in legacy_access if record.series_id == partition_id.series]
    if not relevant:
        return HoldoutState.CONSUMED
    if all(record.observation is LegacyObservation.NOT_OBSERVED for record in relevant):
        return HoldoutState.SEALED
    return HoldoutState.CONSUMED


def derive_holdout_state(
    partition_id: PartitionId,
    legacy_access: Sequence[LegacyAccessRecord],
    entries: Sequence[AccessLogEntry],
) -> HoldoutState:
    """閲覧記録から現在の封印状態を導く（D03 §3.8、ADR-0014）。

    消費の記録が1件でもあれば `CONSUMED`。`SEALED → CONSUMED` は不可逆なので、後から
    `SEALED` へ戻ることはない。許可だけが記録されていて消費が記録されていない場合は
    `SEALED` のままで、データはまだ公開されていない（fail-closed な順序、ADR-0014）。
    """
    state = initial_holdout_state(partition_id, legacy_access)
    if state is HoldoutState.CONSUMED:
        return state
    for entry in entries:
        if entry.partition_id != partition_id:
            continue
        if entry.kind is AccessLogEntryKind.CONSUMED:
            return HoldoutState.CONSUMED
    return HoldoutState.SEALED
