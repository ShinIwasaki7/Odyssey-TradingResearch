"""期間のアクセス分類と封印状態（D03 §3.8、ADR-0014）。

`AccessClass` は「その期間のデータを何に使ってよいか」の三分類。

- `RESEARCH_HISTORY`（研究履歴、2016〜2023年）: 開発・研究に使える。
- `LEGACY_HOLDOUT`（旧基盤の封印期間、2024〜2025年）: `HoldoutState` を持つ。
- `QUARANTINED_UNASSIGNED`（未分類の隔離期間、2026年以降）: 未観測の確認と別の決定記録
  （ADR）による再分類が行われるまで、**いかなる経路でも読めない**。

`HoldoutState` は封印期間の partition だけが持つ。`SEALED`（未観測と確認できた）から
`CONSUMED`（使用済み、または履歴不明）への遷移は**不可逆**で、逆遷移は存在しない。状態は
manifest に直接書き換えるフィールドを持たず、追記専用の閲覧記録（`access_log.jsonl`）から
導出する（ADR-0014 の消費遷移の直列化）。

**足の所属は `bar_end` で決める**（D03 §3.8）。`bar_end` が境界以上の足は後ろの区分に入る。
2023-12-31 22:00Z に始まり 2024-01-01 22:00Z に終わる日足を研究区分に入れると 2024 年の
情報が研究区分へ漏れるため。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = [
    "INITIAL_ACCESS_BOUNDARIES",
    "AccessBoundaries",
    "AccessClass",
    "HoldoutState",
]


class AccessClass(Enum):
    """期間のアクセス分類（D03 §3.8、ADR-0014）。

    値は partition ディレクトリ名にそのまま使う（例: `USDJPY_1h_bid/RESEARCH_HISTORY/`）。
    """

    RESEARCH_HISTORY = "RESEARCH_HISTORY"
    LEGACY_HOLDOUT = "LEGACY_HOLDOUT"
    QUARANTINED_UNASSIGNED = "QUARANTINED_UNASSIGNED"


class HoldoutState(Enum):
    """封印期間 partition の状態（D03 §3.8、ADR-0014 2026-09-20 改訂）。

    `SEALED` は旧基盤で未観測と確認できた partition だけに与える。使用済み、または履歴が
    確認できない partition は `CONSUMED` とする。`SEALED → CONSUMED` は不可逆。
    """

    SEALED = "SEALED"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class AccessBoundaries:
    """アクセス分類の期間境界（D03 §3.8）。

    `research_until` 未満が研究履歴、`research_until` 以上 `holdout_until` 未満が封印期間、
    `holdout_until` 以上が未分類の隔離期間。境界は暦年の UTC 時刻（ADR-0014 の承認事項9）。
    """

    research_until: UtcTime
    holdout_until: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.research_until, UtcTime):
            raise MarketDataValueError("AccessBoundaries.research_until must be a UtcTime")
        if not isinstance(self.holdout_until, UtcTime):
            raise MarketDataValueError("AccessBoundaries.holdout_until must be a UtcTime")
        if not self.research_until < self.holdout_until:
            raise MarketDataValueError(
                "AccessBoundaries requires research_until < holdout_until, got"
                f" {self.research_until} and {self.holdout_until}"
            )

    def classify(self, bar_end: UtcTime) -> AccessClass:
        """足の終了時刻から所属する分類を決める（D03 §3.8）。

        `bar_start` ではなく `bar_end` を使うのは、区間をまたぐ足の後半の情報が前の区分へ
        漏れないようにするため。
        """
        if not isinstance(bar_end, UtcTime):
            raise MarketDataValueError("AccessBoundaries.classify requires a UtcTime")
        if bar_end < self.research_until:
            return AccessClass.RESEARCH_HISTORY
        if bar_end < self.holdout_until:
            return AccessClass.LEGACY_HOLDOUT
        return AccessClass.QUARANTINED_UNASSIGNED


#: 初版の期間境界（D03 §3.8）。
#: 研究履歴 `[2016-01-01Z, 2024-01-01Z)` / 封印期間 `[2024-01-01Z, 2026-01-01Z)` /
#: 未分類の隔離期間 `[2026-01-01Z, ∞)`。
INITIAL_ACCESS_BOUNDARIES = AccessBoundaries(
    research_until=UtcTime.from_components(2024, 1, 1),
    holdout_until=UtcTime.from_components(2026, 1, 1),
)
