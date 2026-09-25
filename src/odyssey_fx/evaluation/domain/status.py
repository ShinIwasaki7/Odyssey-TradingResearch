"""評価の状態と整合検査（D07 §10）。

全体計画 §5.5.1 が求める「完了 / 失敗 / 拒否 / 中断」を `EvaluationStatus` の4値とし、
判断履歴が自分と食い違っていないことを確かめる検査12件（致命10・警告2）を宣言する。
段階2 の8件（C1〜C8）に、段階4（D07 v2.0 §10.4）で C9〜C12 を足した。

**検査の結果は3区分である**（D07 §10.4、根本対処 R5）。合格（`PASSED`）・不合格
（`FAILED`）に加えて、**検査に要る値が読めず、検査を実施できなかった**ことを
`UNREADABLE` として残す。2区分のままだと、読めなかった検査を合格か不合格のどちらかに
寄せるしかなく、合格に寄せれば見逃し、不合格に寄せれば「検査して食い違いを見つけた」と
誤って説明する。

**致命の検査が1件でも合格でなければ、算出できた指標も出さない**（D07 §10.1）。部分的に
出すと「採用してよい数値」と「不整合な判断履歴から出た数値」が同じ表に混ざる。落ちた検査の
名前・期待値・観測値は `CONSISTENCY_CHECKS` 表に残るので、**保存済みの成果物だけを見て
失敗を説明できる**（D07 §8.1）。

検査結果は3区分のどれでも全件を残す。不合格だけを残すと、検査が実施されたのかどうかが
結果から分からない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.errors import KernelValueError

__all__ = [
    "CHECK_ALL_VALUES_READABLE",
    "CHECK_CALENDAR_MATCHES_RUN",
    "CHECK_ID_CHAIN_COMPLETE",
    "CHECK_INPUT_KEYS_UNIQUE",
    "CHECK_LEVELS",
    "CHECK_OPPORTUNITY_COUNT_MATCHES",
    "CHECK_ORDER",
    "CHECK_REALIZED_MATCHES_BALANCE",
    "CHECK_REQUIRED_COLUMNS_PRESENT",
    "CHECK_RUN_ID_CONSISTENT",
    "CHECK_RUN_MANIFEST_READABLE",
    "CHECK_SINGLE_ACCOUNT_CURRENCY",
    "CHECK_SNAPSHOT_ORDER_MONOTONIC",
    "CHECK_TRADE_COUNT_MATCHES",
    "CheckLevel",
    "CheckOutcome",
    "ConsistencyCheckResult",
    "EvaluationStatus",
]


class EvaluationStatus(Enum):
    """評価の状態4値（D07 §10.1）。段階2・4 で起きるのは前の3つである。"""

    #: 評価が完了した。0取引でもこの値（D07 §10.1「0取引は失敗ではない」）。
    COMPLETED = "COMPLETED"
    #: 入力の run が正常完走していない。指標を算出せず、状態と診断だけを出す（Q6 決定）。
    REJECTED = "REJECTED"
    #: 評価自身が完了できなかった（致命の整合検査が1件でも合格でない）。
    FAILED = "FAILED"
    #: 探索の途中で中断された。**段階5（D09）でだけ使う**。
    ABORTED = "ABORTED"


class CheckLevel(Enum):
    """整合検査の水準（D07 §10.2）。

    `FATAL` は1件でも合格でなければ指標を出さない。`WARNING` は結果に残したうえで評価を
    続ける。
    """

    FATAL = "FATAL"
    WARNING = "WARNING"


class CheckOutcome(Enum):
    """検査1件の結果の3区分（D07 §10.4）。整合検査と研究ポリシーの検査が共有する。"""

    #: 合格。
    PASSED = "PASSED"
    #: 不合格（検査を実施し、食い違いを見つけた）。
    FAILED = "FAILED"
    #: 検査に要る値が読めず、検査を実施できなかった。
    UNREADABLE = "UNREADABLE"


#: C1: 読む9表が揃い、必須の列が1つも欠けていない（値は読まない）。
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
#: C9: 読む列のすべての値が解釈できる（D07 §10.4。v2.0 で新設）。
CHECK_ALL_VALUES_READABLE: Final = "all_values_readable"
#: C10: 受け取った取引カレンダーが run manifest の `calendar_ref` と一致する（v2.0、Q8 決定）。
CHECK_CALENDAR_MATCHES_RUN: Final = "calendar_matches_run"
#: C11: run manifest が読める（v2.0。D07 §10.1.1 の R1-D07-4）。
CHECK_RUN_MANIFEST_READABLE: Final = "run_manifest_readable"
#: C12: 入力の表の主キーに重複が無い（v2.0。D07 §9.3 の R1-D07-5）。
CHECK_INPUT_KEYS_UNIQUE: Final = "input_keys_unique"

#: 検査の宣言順（D07 §10.4 の C1〜C12）。`CONSISTENCY_CHECKS` 表の整列鍵である。
CHECK_ORDER: Final[tuple[str, ...]] = (
    CHECK_REQUIRED_COLUMNS_PRESENT,
    CHECK_RUN_ID_CONSISTENT,
    CHECK_TRADE_COUNT_MATCHES,
    CHECK_ID_CHAIN_COMPLETE,
    CHECK_REALIZED_MATCHES_BALANCE,
    CHECK_OPPORTUNITY_COUNT_MATCHES,
    CHECK_SNAPSHOT_ORDER_MONOTONIC,
    CHECK_SINGLE_ACCOUNT_CURRENCY,
    CHECK_ALL_VALUES_READABLE,
    CHECK_CALENDAR_MATCHES_RUN,
    CHECK_RUN_MANIFEST_READABLE,
    CHECK_INPUT_KEYS_UNIQUE,
)

#: 検査ごとの水準（D07 §10.4 の「水準」欄）。致命10件・警告2件。
CHECK_LEVELS: Final[dict[str, CheckLevel]] = {
    CHECK_REQUIRED_COLUMNS_PRESENT: CheckLevel.FATAL,
    CHECK_RUN_ID_CONSISTENT: CheckLevel.FATAL,
    CHECK_TRADE_COUNT_MATCHES: CheckLevel.FATAL,
    CHECK_ID_CHAIN_COMPLETE: CheckLevel.FATAL,
    CHECK_REALIZED_MATCHES_BALANCE: CheckLevel.FATAL,
    CHECK_OPPORTUNITY_COUNT_MATCHES: CheckLevel.WARNING,
    CHECK_SNAPSHOT_ORDER_MONOTONIC: CheckLevel.WARNING,
    CHECK_SINGLE_ACCOUNT_CURRENCY: CheckLevel.FATAL,
    CHECK_ALL_VALUES_READABLE: CheckLevel.FATAL,
    CHECK_CALENDAR_MATCHES_RUN: CheckLevel.FATAL,
    CHECK_RUN_MANIFEST_READABLE: CheckLevel.FATAL,
    CHECK_INPUT_KEYS_UNIQUE: CheckLevel.FATAL,
}


@dataclass(frozen=True, slots=True)
class ConsistencyCheckResult:
    """整合検査1件の結果（D07 §10.2・§10.4）。

    段階2 の `passed: bool` を `outcome: CheckOutcome` に置き換えた（D07 v2.0 §10.4）。
    `passed` は `outcome is PASSED` を読むための派生の性質であり、保存する列ではない。

    `expected` と `observed` は D02 §9.3 の正規化エンコード文字列で持つ。型ごとに列を
    分けると検査の種類だけ列が増え、同じ内容から常に同じ文字列が出る性質も失う。
    """

    check: str
    level: CheckLevel
    outcome: CheckOutcome
    table: TraceTable | None
    expected: str
    observed: str

    def __post_init__(self) -> None:
        if self.check not in CHECK_LEVELS:
            raise KernelValueError(
                f"{self.check!r} is not one of the twelve consistency checks of D07 §10.4:"
                f" {list(CHECK_ORDER)}"
            )
        if not isinstance(self.level, CheckLevel):
            raise KernelValueError("ConsistencyCheckResult.level must be a CheckLevel")
        if self.level is not CHECK_LEVELS[self.check]:
            raise KernelValueError(
                f"{self.check} is declared as {CHECK_LEVELS[self.check].value} in D07 §10.4,"
                f" got {self.level.value}"
            )
        if not isinstance(self.outcome, CheckOutcome):
            raise KernelValueError("ConsistencyCheckResult.outcome must be a CheckOutcome")
        if self.table is not None and not isinstance(self.table, TraceTable):
            raise KernelValueError("ConsistencyCheckResult.table must be a TraceTable or None")
        for name in ("expected", "observed"):
            if not isinstance(getattr(self, name), str):
                raise KernelValueError(f"ConsistencyCheckResult.{name} must be a str")

    @property
    def passed(self) -> bool:
        """合格したかどうか（`outcome is PASSED`）。読めなかった検査は合格ではない。"""
        return self.outcome is CheckOutcome.PASSED
