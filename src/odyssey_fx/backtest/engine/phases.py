"""判断時点のフェーズ集合（D06 §4.1、Q1 決定）。

1つの判断時刻 T に属するフェーズを因果順に15件（rank 0〜14）並べる。フェーズ集合は run 全体
で1つであり、判断時点ごとに変えない。`RUN_END` も集合に含め、run 末尾の判断時点でだけ使う。

| rank | 名前 | 内容 |
|---|---|---|
| 0 | `EXECUTION_BAR_COMPLETE` | 直前の執行足の足内約定を解決する |
| 1 | `LEDGER_UPDATE` | 含み損益の再評価と台帳 snapshot の記録 |
| 2 | `ORDER_EXPIRY` | 期限に達した未約定注文を失効させる |
| 3 | `PUBLICATION` | 同じ時刻の確定足をまとめて戦略へ公開する |
| 4 | `OPPORTUNITY_LIFECYCLE` | 取引機会の期限・失効の検査 |
| 5〜9 | `P1_FEATURE`〜`P5_ORDER_INTENT` | 部品の評価（上位設計書 §4.3.12 の P1〜P5） |
| 10 | `ADMISSION` | 要求組立・全順序化・審査・予約・受付 |
| 11 | `EXECUTION_OPEN` | 次の執行足の始値処理 |
| 12 | `POST_FILL_EVALUATION` | 約定後の評価と受付結果の通知 |
| 13 | `POST_FILL_ADMISSION` | 約定後に生まれた決済要求の受付 |
| 14 | `RUN_END` | 末尾処理（run 末尾の判断時点だけ） |

**フェーズ名に数字を使う**（Q1 決定）。上位設計書 §4.3.12 が確定した P0〜P5 という呼び方が
全文書で使われており、名前に数字を残すと判断履歴のフェーズ列から因果順が名前だけで読める。
共通カーネルの名前規則はこの決定に合わせて `^[A-Z][A-Z0-9_]*$` へ緩めてある（D02 §3.3 v1.5）。
"""

from __future__ import annotations

from typing import Final

from odyssey_fx.common.time import PhaseRank, PhaseSet

__all__ = [
    "BACKTEST_PHASES",
    "PHASE_ADMISSION",
    "PHASE_EXECUTION_BAR_COMPLETE",
    "PHASE_EXECUTION_OPEN",
    "PHASE_LEDGER_UPDATE",
    "PHASE_ORDER",
    "PHASE_ORDER_EXPIRY",
    "PHASE_P5_ORDER_INTENT",
    "PHASE_POST_FILL_ADMISSION",
    "PHASE_POST_FILL_EVALUATION",
    "PHASE_RUN_END",
]

PHASE_EXECUTION_BAR_COMPLETE: Final = "EXECUTION_BAR_COMPLETE"
PHASE_LEDGER_UPDATE: Final = "LEDGER_UPDATE"
PHASE_ORDER_EXPIRY: Final = "ORDER_EXPIRY"
PHASE_ADMISSION: Final = "ADMISSION"
PHASE_EXECUTION_OPEN: Final = "EXECUTION_OPEN"
PHASE_P5_ORDER_INTENT: Final = "P5_ORDER_INTENT"
PHASE_POST_FILL_EVALUATION: Final = "POST_FILL_EVALUATION"
PHASE_POST_FILL_ADMISSION: Final = "POST_FILL_ADMISSION"
PHASE_RUN_END: Final = "RUN_END"

#: D06 §4.1 の15フェーズを rank の昇順に並べた名前。
PHASE_ORDER: Final[tuple[str, ...]] = (
    PHASE_EXECUTION_BAR_COMPLETE,
    PHASE_LEDGER_UPDATE,
    PHASE_ORDER_EXPIRY,
    "PUBLICATION",
    "OPPORTUNITY_LIFECYCLE",
    "P1_FEATURE",
    "P2_MARKET_STATE",
    "P3_TRIGGER",
    "P4_CONFIRMATION",
    PHASE_P5_ORDER_INTENT,
    PHASE_ADMISSION,
    PHASE_EXECUTION_OPEN,
    PHASE_POST_FILL_EVALUATION,
    PHASE_POST_FILL_ADMISSION,
    PHASE_RUN_END,
)

#: run manifest に記録するフェーズ集合（D02 §3.3）。
BACKTEST_PHASES: Final = PhaseSet(
    tuple(PhaseRank(rank=rank, name=name) for rank, name in enumerate(PHASE_ORDER))
)
