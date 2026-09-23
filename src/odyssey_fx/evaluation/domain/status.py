"""評価の状態と整合検査（D07 §10）。

全体計画 §5.5.1 が求める「完了 / 失敗 / 拒否 / 中断」を `EvaluationStatus` の4値とし、
判断履歴が自分と食い違っていないことを確かめる検査8件（致命6・警告2）を宣言する。

**致命の検査が1件でも不合格なら、算出できた指標も出さない**（D07 §10.1）。部分的に出すと
「採用してよい数値」と「不整合な判断履歴から出た数値」が同じ表に混ざる。落ちた検査の名前・
期待値・観測値は `CONSISTENCY_CHECKS` 表に残るので、**保存済みの成果物だけを見て失敗を
説明できる**（D07 §8.1）。

検査結果は合格・不合格のどちらも全件を残す。不合格だけを残すと、検査が実施されたのか
どうかが結果から分からない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.errors import KernelValueError

__all__ = [
    "CHECK_LEVELS",
    "CHECK_ORDER",
    "CHECK_ID_CHAIN_COMPLETE",
    "CHECK_OPPORTUNITY_COUNT_MATCHES",
    "CHECK_REALIZED_MATCHES_BALANCE",
    "CHECK_REQUIRED_COLUMNS_PRESENT",
    "CHECK_RUN_ID_CONSISTENT",
    "CHECK_SINGLE_ACCOUNT_CURRENCY",
    "CHECK_SNAPSHOT_ORDER_MONOTONIC",
    "CHECK_TRADE_COUNT_MATCHES",
    "CheckLevel",
    "ConsistencyCheckResult",
    "EvaluationStatus",
]


class EvaluationStatus(Enum):
    """評価の状態4値（D07 §10.1）。段階2で起きるのは前の3つである。"""

    #: 評価が完了した。0取引でもこの値（D07 §10.1「0取引は失敗ではない」）。
    COMPLETED = "COMPLETED"
    #: 入力の run が正常完走していない。指標を算出せず、状態と診断だけを出す（Q6 決定）。
    REJECTED = "REJECTED"
    #: 評価自身が完了できなかった（致命の整合検査が1件でも不合格）。
    FAILED = "FAILED"
    #: 探索の途中で中断された。**段階5（D09）でだけ使う**。
    ABORTED = "ABORTED"


class CheckLevel(Enum):
    """整合検査の水準（D07 §10.2）。

    `FATAL` は1件でも不合格なら指標を出さない。`WARNING` は結果に残したうえで評価を続ける。
    """

    FATAL = "FATAL"
    WARNING = "WARNING"


#: C1: 読む9表が揃い、必須の列が1つも欠けていない。
CHECK_REQUIRED_COLUMNS_PRESENT: Final = "required_columns_present"
#: C2: 実行の識別子が結果 DTO・run manifest・各表の `run_id` 列で一致する。
CHECK_RUN_ID_CONSISTENT: Final = "run_id_consistent"
#: C3: 完了取引の件数が `BacktestResult.trade_count` と一致する。
CHECK_TRADE_COUNT_MATCHES: Final = "trade_count_matches"
#: C4: 完了取引の入場・決済約定から注文・試行まで外部キーが辿れる。
CHECK_ID_CHAIN_COMPLETE: Final = "id_chain_complete"
#: C5: `最後の balance − 初期残高` が末尾の確定損益と一致する。
CHECK_REALIZED_MATCHES_BALANCE: Final = "realized_matches_balance"
#: C6（警告）: 終端理由別の件数の合計が生成総数と一致する。
CHECK_OPPORTUNITY_COUNT_MATCHES: Final = "opportunity_count_matches"
#: C7（警告）: 台帳 snapshot の処理点が昇順に並んでいる。
CHECK_SNAPSHOT_ORDER_MONOTONIC: Final = "snapshot_order_monotonic"
#: C8: すべての `Money` 列の通貨が口座通貨と一致する。
CHECK_SINGLE_ACCOUNT_CURRENCY: Final = "single_account_currency"

#: 検査の宣言順（D07 §10.2 の C1〜C8）。`CONSISTENCY_CHECKS` 表の整列鍵である。
CHECK_ORDER: Final[tuple[str, ...]] = (
    CHECK_REQUIRED_COLUMNS_PRESENT,
    CHECK_RUN_ID_CONSISTENT,
    CHECK_TRADE_COUNT_MATCHES,
    CHECK_ID_CHAIN_COMPLETE,
    CHECK_REALIZED_MATCHES_BALANCE,
    CHECK_OPPORTUNITY_COUNT_MATCHES,
    CHECK_SNAPSHOT_ORDER_MONOTONIC,
    CHECK_SINGLE_ACCOUNT_CURRENCY,
)

#: 検査ごとの水準（D07 §10.2 の「水準」欄）。致命6件・警告2件。
CHECK_LEVELS: Final[dict[str, CheckLevel]] = {
    CHECK_REQUIRED_COLUMNS_PRESENT: CheckLevel.FATAL,
    CHECK_RUN_ID_CONSISTENT: CheckLevel.FATAL,
    CHECK_TRADE_COUNT_MATCHES: CheckLevel.FATAL,
    CHECK_ID_CHAIN_COMPLETE: CheckLevel.FATAL,
    CHECK_REALIZED_MATCHES_BALANCE: CheckLevel.FATAL,
    CHECK_OPPORTUNITY_COUNT_MATCHES: CheckLevel.WARNING,
    CHECK_SNAPSHOT_ORDER_MONOTONIC: CheckLevel.WARNING,
    CHECK_SINGLE_ACCOUNT_CURRENCY: CheckLevel.FATAL,
}


@dataclass(frozen=True, slots=True)
class ConsistencyCheckResult:
    """整合検査1件の結果（D07 §10.2）。

    `expected` と `observed` は D02 §9.3 の正規化エンコード文字列で持つ。型ごとに列を
    分けると検査の種類だけ列が増え、同じ内容から常に同じ文字列が出る性質も失う。
    """

    check: str
    level: CheckLevel
    passed: bool
    table: TraceTable | None
    expected: str
    observed: str

    def __post_init__(self) -> None:
        if self.check not in CHECK_LEVELS:
            raise KernelValueError(
                f"{self.check!r} is not one of the eight consistency checks of D07 §10.2:"
                f" {list(CHECK_ORDER)}"
            )
        if not isinstance(self.level, CheckLevel):
            raise KernelValueError("ConsistencyCheckResult.level must be a CheckLevel")
        if self.level is not CHECK_LEVELS[self.check]:
            raise KernelValueError(
                f"{self.check} is declared as {CHECK_LEVELS[self.check].value} in D07 §10.2,"
                f" got {self.level.value}"
            )
        if not isinstance(self.passed, bool):
            raise KernelValueError("ConsistencyCheckResult.passed must be a bool")
        if self.table is not None and not isinstance(self.table, TraceTable):
            raise KernelValueError("ConsistencyCheckResult.table must be a TraceTable or None")
        for name in ("expected", "observed"):
            if not isinstance(getattr(self, name), str):
                raise KernelValueError(f"ConsistencyCheckResult.{name} must be a str")
