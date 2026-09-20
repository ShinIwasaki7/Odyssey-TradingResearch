"""実現した公開時刻の記録（D03 §3.6、上位設計書 §4.3.13）。

遅延シナリオを適用した結果、各足が実際にいつ利用可能になったかの列。run に保存し、
「予定 `bar_end` に対し実際の到着が `available_at`」という差を後から追跡できるようにする。
異常な遅延を通常の公開予定へ書き換えて欠損判定から隠さないための記録である。

記録は足の自然キー（`BarKey`）ごとに1件。同じ足に2件は持てない（同じ足が2度公開される
ことはないため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = ["PublicationLog", "PublicationRecord"]


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    """1本の足の公開記録（D03 §3.6）。"""

    bar_key: BarKey
    bar_end: UtcTime
    scheduled_at: UtcTime
    available_at: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.bar_key, BarKey):
            raise MarketDataValueError("PublicationRecord.bar_key must be a BarKey")
        for name in ("bar_end", "scheduled_at", "available_at"):
            if not isinstance(getattr(self, name), UtcTime):
                raise MarketDataValueError(f"PublicationRecord.{name} must be a UtcTime")
        if self.scheduled_at < self.bar_end:
            raise MarketDataValueError(
                f"PublicationRecord requires scheduled_at >= bar_end, got"
                f" {self.scheduled_at} and {self.bar_end}"
            )
        if self.available_at < self.scheduled_at:
            raise MarketDataValueError(
                f"PublicationRecord requires available_at >= scheduled_at, got"
                f" {self.available_at} and {self.scheduled_at};"
                " delays are non-negative (D03 §3.6)"
            )

    @property
    def realized_delay(self) -> timedelta:
        """予定からの実際のずれ。"""
        return self.available_at - self.scheduled_at

    def sort_key(self) -> tuple[str, str]:
        """整列鍵（系列文字列、足の開始時刻）。"""
        return self.bar_key.sort_key()


@dataclass(frozen=True, slots=True)
class PublicationLog:
    """実現した公開時刻の列（D03 §3.6）。

    構築時に足の自然キーで整列し、重複を拒否する。記録順（シミュレーションの処理順）が
    保存内容に影響しないようにするため。
    """

    records: tuple[PublicationRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple):
            raise MarketDataValueError("PublicationLog.records must be a tuple")
        seen: set[tuple[str, str]] = set()
        for record in self.records:
            if not isinstance(record, PublicationRecord):
                raise MarketDataValueError("PublicationLog.records must contain PublicationRecord")
            key = record.sort_key()
            if key in seen:
                raise MarketDataValueError(
                    f"PublicationLog contains two records for the same bar: {record.bar_key}"
                )
            seen.add(key)
        normalized = tuple(sorted(self.records, key=lambda record: record.sort_key()))
        if normalized != self.records:
            object.__setattr__(self, "records", normalized)

    def available_at(self, bar_key: BarKey) -> UtcTime | None:
        """その足の実際の公開時刻（記録がなければ `None`）。"""
        for record in self.records:
            if record.bar_key == bar_key:
                return record.available_at
        return None
