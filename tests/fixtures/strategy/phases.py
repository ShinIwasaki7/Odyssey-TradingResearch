"""バックテストのフェーズ集合（D06 §4.1）。

戦略ランタイムは取引機会の遷移に処理点（時刻・フェーズ・通し番号）を押すが、フェーズの
順位と全列挙は `backtest.engine`（D06）の責務である。段階2 の実装範囲にはエンジンが無い
ので、テストは D06 §4.1 が確定した15件をそのまま組み立てて渡す。

順位と名前は D06 §4.1 の表のとおりで、ランタイムが要求する8つの名前（D05 §6.1）をすべて
含む。ランタイムが引くのは名前だけであり、順位の意味には踏み込まない。
"""

from __future__ import annotations

from typing import Final

from odyssey_fx.common.time import PhaseRank, PhaseSet

__all__ = ["BACKTEST_PHASES", "PHASE_ORDER"]

#: D06 §4.1 の15フェーズ（rank 0〜14）。
PHASE_ORDER: Final[tuple[str, ...]] = (
    "EXECUTION_BAR_COMPLETE",
    "LEDGER_UPDATE",
    "ORDER_EXPIRY",
    "PUBLICATION",
    "OPPORTUNITY_LIFECYCLE",
    "P1_FEATURE",
    "P2_MARKET_STATE",
    "P3_TRIGGER",
    "P4_CONFIRMATION",
    "P5_ORDER_INTENT",
    "ADMISSION",
    "EXECUTION_OPEN",
    "POST_FILL_EVALUATION",
    "POST_FILL_ADMISSION",
    "RUN_END",
)

BACKTEST_PHASES: Final = PhaseSet(
    tuple(PhaseRank(rank=rank, name=name) for rank, name in enumerate(PHASE_ORDER))
)
