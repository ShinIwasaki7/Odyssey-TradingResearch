"""単一実行の指標・取引の記録・集計の語彙（D07 §5・§6・§7.2・§8.1）。

段階2の最小集合は**指標15件・集計7種・約定診断3項目**である（D07 §11）。本モジュールは
それらの型と、値そのものを作る純粋な計算（走査・比率・勝敗）だけを持つ。判断履歴の表を
読む順序・整合検査・状態の決定は `evaluation.application.evaluate_run` が行う。

**「値なし」を 0 や成功値へ置換しない**（D07 §5.1）。値が無い指標は `Unavailable` として
型で表し、行は必ず残す。行ごと落とすと「計算できなかった」と「集計し忘れ」を区別できない。

**丸めは行わない**（D07 §5.1、Q2 決定）。金額と価格差は `Decimal` の加減算だけで厳密に
求まり、比率は除算を1回だけカーネル精度（D02 §4.1）で行ってそのまま保存する。表示用の
桁は報告の関心であり、保存値に桁を決め打つと、同じ判断履歴から出した値が桁の変更で変わる。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from enum import Enum
from typing import Final

from odyssey_fx.backtest.domain.orders import CloseCause, OrderSide
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import FillId, OpportunityId, OrderId, PositionId
from odyssey_fx.common.money import (
    Money,
    Price,
    PriceOffset,
    Quantity,
    decimal_from_int,
    kernel_context,
)
from odyssey_fx.common.reason import MissingInputReason, ReasonCode
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import ProcessingPoint, UtcTime

__all__ = [
    "ADMISSION_REJECTION_KEYS",
    "CATEGORY_KEYS",
    "CLOSE_CAUSE_KEYS",
    "EVALUATION_OUTCOME_KEYS",
    "INTRABAR_METHOD_KEYS",
    "METRIC_INPUTS",
    "METRIC_KINDS",
    "MISSING_INPUT_KEYS",
    "OPPORTUNITY_TERMINAL_KEYS",
    "AmountValue",
    "CategoryCount",
    "CategoryKind",
    "CountValue",
    "Drawdown",
    "DurationValue",
    "FillDiagnostic",
    "MetricCaveat",
    "MetricId",
    "MetricKind",
    "MetricRecord",
    "MetricUnavailableReason",
    "MetricValue",
    "PriceOffsetValue",
    "RatioValue",
    "TradeOutcome",
    "TradeRecord",
    "Unavailable",
    "max_drawdown",
    "metric_caveats",
    "ratio_of",
    "trade_outcome",
]


class MetricId(Enum):
    """段階2の指標15件（D07 §5.2）。

    **宣言順が `METRICS` 表の整列鍵である**（D07 §8.1）。番号は D07 §5.2 の #1〜#15 に
    そのまま対応するので、並べ替えない。
    """

    #: #1 最後の台帳 snapshot の balance − 初期残高。
    NET_PROFIT = "NET_PROFIT"
    #: #2 完了取引の確定損益の合計。
    CLOSED_TRADE_PROFIT = "CLOSED_TRADE_PROFIT"
    #: #3 完了取引の件数。
    TRADE_COUNT = "TRADE_COUNT"
    #: #4 勝ち取引数 ÷ 完了取引数。
    WIN_RATE = "WIN_RATE"
    #: #5 含み損益込みの最大ドローダウン（採用指標）。
    MAX_DRAWDOWN_MTM = "MAX_DRAWDOWN_MTM"
    #: #6 同上の率。
    MAX_DRAWDOWN_MTM_RATE = "MAX_DRAWDOWN_MTM_RATE"
    #: #7 確定損益だけの最大ドローダウン（参考値）。
    MAX_DRAWDOWN_BALANCE = "MAX_DRAWDOWN_BALANCE"
    #: #8 同上の率。
    MAX_DRAWDOWN_BALANCE_RATE = "MAX_DRAWDOWN_BALANCE_RATE"
    #: #9 建玉の保有時間の合計 ÷ run 区間の長さ。
    EXPOSURE_RATE = "EXPOSURE_RATE"
    #: #10 balance へ計上した費用（手数料）の合計。
    COST_CHARGED_TOTAL = "COST_CHARGED_TOTAL"
    #: #11 価格に反映済みの費用の合計（参考値）。
    COST_PRICE_EMBEDDED_TOTAL = "COST_PRICE_EMBEDDED_TOTAL"
    #: #12 エントリー約定の不利約定幅の最大値。
    MAX_ADVERSE_FILL_OFFSET = "MAX_ADVERSE_FILL_OFFSET"
    #: #13 含み損益込みの最終資産（参考値）。
    END_EQUITY_MTM = "END_EQUITY_MTM"
    #: #14 残存建玉を仮に決済した場合の損益（参考値）。
    HYPOTHETICAL_CLOSED_PROFIT = "HYPOTHETICAL_CLOSED_PROFIT"
    #: #15 純損益 ÷ 初期残高（期間で割らない単純収益率）。
    NET_RETURN_RATE = "NET_RETURN_RATE"


class MetricKind(Enum):
    """指標の値の種別（D07 §5.1）。丸めの規則が種別ごとに違う。"""

    AMOUNT = "AMOUNT"
    RATIO = "RATIO"
    COUNT = "COUNT"
    DURATION = "DURATION"
    PRICE_OFFSET = "PRICE_OFFSET"


class MetricUnavailableReason(Enum):
    """値が無いことの理由4件（D07 §10.3）。"""

    #: 完了取引が0件で、取引を分母または対象にする指標が定まらない。
    NO_TRADES = "NO_TRADES"
    #: 対象の行が1件も無い（台帳 snapshot が無い、エントリー約定が無い）。
    NO_OBSERVATIONS = "NO_OBSERVATIONS"
    #: 分母が 0 で比率が定まらない。
    UNDEFINED_DENOMINATOR = "UNDEFINED_DENOMINATOR"
    #: 入力そのものが無い（末尾の集計が `None`）。
    INPUT_NOT_AVAILABLE = "INPUT_NOT_AVAILABLE"


class MetricCaveat(Enum):
    """指標1件だけを見た人にも伝える必要のある注記5件（D07 §7.2）。

    **宣言順が `MetricRecord.caveats` の並びである**。同じ指標から常に同じ並びが出ないと
    結果のダイジェストが揺れる（D07 §9.1 の条件4）。
    """

    #: swap / rollover を計上していない（ADR-0029）。
    SWAP_NOT_MODELED = "SWAP_NOT_MODELED"
    #: 価格に反映済みで、balance から控除していない参考値。
    PRICE_EMBEDDED_COST = "PRICE_EMBEDDED_COST"
    #: 未決済建玉の評価に依存する、または未決済建玉を含まない。
    OPEN_POSITION_EXCLUDED = "OPEN_POSITION_EXCLUDED"
    #: 入場側の費用を含まない（段階2 は取引単位の費用を読まない）。
    ENTRY_COST_EXCLUDED = "ENTRY_COST_EXCLUDED"
    #: 解決できなかった足内競合の約定を含む（ADR-0030）。
    UNRESOLVED_INTRABAR_PRESENT = "UNRESOLVED_INTRABAR_PRESENT"


def _require_kind(actual: MetricKind, expected: MetricKind, label: str) -> None:
    if actual is not expected:
        raise KernelValueError(f"{label}.kind must be {expected.value}, got {actual.value}")


@dataclass(frozen=True, slots=True)
class AmountValue:
    """口座通貨建ての金額（D07 §5.1）。丸めない。"""

    amount: Money
    kind: MetricKind = MetricKind.AMOUNT

    def __post_init__(self) -> None:
        _require_kind(self.kind, MetricKind.AMOUNT, "AmountValue")
        if not isinstance(self.amount, Money):
            raise KernelValueError("AmountValue.amount must be a Money")


@dataclass(frozen=True, slots=True)
class RatioValue:
    """比率（D07 §5.1）。除算を1回だけカーネル精度で行った結果をそのまま持つ。"""

    ratio: Decimal
    kind: MetricKind = MetricKind.RATIO

    def __post_init__(self) -> None:
        _require_kind(self.kind, MetricKind.RATIO, "RatioValue")
        if not isinstance(self.ratio, Decimal) or not self.ratio.is_finite():
            raise KernelValueError(f"RatioValue.ratio must be a finite Decimal, got {self.ratio!r}")


@dataclass(frozen=True, slots=True)
class CountValue:
    """件数（D07 §5.1）。"""

    count: int
    kind: MetricKind = MetricKind.COUNT

    def __post_init__(self) -> None:
        _require_kind(self.kind, MetricKind.COUNT, "CountValue")
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise KernelValueError("CountValue.count must be an int")
        if self.count < 0:
            raise KernelValueError(f"CountValue.count must be >= 0, got {self.count}")


@dataclass(frozen=True, slots=True)
class DurationValue:
    """経過時間（D07 §5.1）。マイクロ秒精度の `timedelta`。"""

    duration: timedelta
    kind: MetricKind = MetricKind.DURATION

    def __post_init__(self) -> None:
        _require_kind(self.kind, MetricKind.DURATION, "DurationValue")
        if not isinstance(self.duration, timedelta):
            raise KernelValueError("DurationValue.duration must be a timedelta")


@dataclass(frozen=True, slots=True)
class PriceOffsetValue:
    """価格差（D07 §5.1）。丸めない。"""

    offset: PriceOffset
    kind: MetricKind = MetricKind.PRICE_OFFSET

    def __post_init__(self) -> None:
        _require_kind(self.kind, MetricKind.PRICE_OFFSET, "PriceOffsetValue")
        if not isinstance(self.offset, PriceOffset):
            raise KernelValueError("PriceOffsetValue.offset must be a PriceOffset")


@dataclass(frozen=True, slots=True)
class Unavailable:
    """値が無いこと（D07 §5.1・§10.3）。

    `kind` は**その指標が値を持てたなら何の種別だったか**を表す。0 や成功値へ置換しない
    ので、種別は値ではなく指標の宣言から来る。
    """

    kind: MetricKind
    reason: MetricUnavailableReason

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MetricKind):
            raise KernelValueError("Unavailable.kind must be a MetricKind")
        if not isinstance(self.reason, MetricUnavailableReason):
            raise KernelValueError("Unavailable.reason must be a MetricUnavailableReason")


#: 区分タグ付き union（D07 §3）。
MetricValue = AmountValue | RatioValue | CountValue | DurationValue | PriceOffsetValue | Unavailable


@dataclass(frozen=True, slots=True)
class MetricRecord:
    """指標1件（D07 §5.1・§8.1）。`METRICS` 表の行。"""

    metric_id: MetricId
    value: MetricValue
    caveats: tuple[MetricCaveat, ...] = ()
    observation_count: int = 0
    inputs: tuple[TraceTable, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, MetricId):
            raise KernelValueError("MetricRecord.metric_id must be a MetricId")
        if not isinstance(
            self.value,
            AmountValue | RatioValue | CountValue | DurationValue | PriceOffsetValue | Unavailable,
        ):
            raise KernelValueError("MetricRecord.value must be a MetricValue")
        if self.value.kind is not METRIC_KINDS[self.metric_id]:
            raise KernelValueError(
                f"{self.metric_id.value} is declared as"
                f" {METRIC_KINDS[self.metric_id].value} in D07 §5.2 but carries a"
                f" {self.value.kind.value} value"
            )
        if not isinstance(self.caveats, tuple) or not all(
            isinstance(item, MetricCaveat) for item in self.caveats
        ):
            raise KernelValueError("MetricRecord.caveats must be a tuple of MetricCaveat")
        # 宣言順に整列していない並びや重複は、同じ入力から違うダイジェストを生む
        # （D07 §9.1 の条件4）。
        declared = tuple(item for item in MetricCaveat if item in set(self.caveats))
        if self.caveats != declared:
            raise KernelValueError(
                "MetricRecord.caveats must follow the MetricCaveat declaration order"
                f" without repeats (D07 §7.2); expected {[c.value for c in declared]}"
            )
        if isinstance(self.observation_count, bool) or not isinstance(self.observation_count, int):
            raise KernelValueError("MetricRecord.observation_count must be an int")
        if self.observation_count < 0:
            raise KernelValueError("MetricRecord.observation_count must be >= 0")
        if not isinstance(self.inputs, tuple) or not all(
            isinstance(item, TraceTable) for item in self.inputs
        ):
            raise KernelValueError("MetricRecord.inputs must be a tuple of TraceTable")


class TradeOutcome(Enum):
    """完了取引の勝敗（D07 §5.2 の #4）。"""

    WIN = "WIN"
    LOSS = "LOSS"
    BREAK_EVEN = "BREAK_EVEN"


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """完了取引1件（D07 §5.2・§8.1）。`TRADES` 表の行。

    `trade_seq` は整列鍵 `(entry_at, position_id)` で並べたあとの 1 起点の通し番号である。
    保存された表だけを見て順序を復元できるようにするために持つ。
    """

    trade_seq: int
    position_id: PositionId
    opportunity_id: OpportunityId | None
    symbol: Symbol
    side: OrderSide
    quantity: Quantity
    entry_fill_id: FillId
    entry_price: Price
    entry_at: ProcessingPoint
    close_fill_id: FillId
    exit_price: Price
    exit_at: ProcessingPoint
    close_cause: CloseCause | None
    realized: Money
    holding: timedelta
    outcome: TradeOutcome

    def __post_init__(self) -> None:
        if isinstance(self.trade_seq, bool) or not isinstance(self.trade_seq, int):
            raise KernelValueError("TradeRecord.trade_seq must be an int")
        if self.trade_seq < 1:
            raise KernelValueError(f"TradeRecord.trade_seq starts at 1, got {self.trade_seq}")
        for name, expected in (
            ("position_id", PositionId),
            ("symbol", Symbol),
            ("side", OrderSide),
            ("quantity", Quantity),
            ("entry_fill_id", FillId),
            ("entry_price", Price),
            ("entry_at", ProcessingPoint),
            ("close_fill_id", FillId),
            ("exit_price", Price),
            ("exit_at", ProcessingPoint),
            ("realized", Money),
            ("holding", timedelta),
            ("outcome", TradeOutcome),
        ):
            if not isinstance(getattr(self, name), expected):
                raise KernelValueError(f"TradeRecord.{name} must be a {expected.__name__}")
        if self.holding < timedelta(0):
            raise KernelValueError(f"TradeRecord.holding must be >= 0, got {self.holding}")


@dataclass(frozen=True, slots=True)
class FillDiagnostic:
    """約定1件の診断（D07 §6.2・§8.1）。`FILL_DIAGNOSTICS` 表の行。

    決済約定は参照価格を持たない（`AcceptedCloseTerms`、D06 §3）ため、`adverse_fill_offset`
    と `reference_to_fill` は `None` になる。**参照価格を約定直前の値へ置き換えない**
    （上位設計書 §4.7.12）。
    """

    fill_id: FillId
    order_id: OrderId
    position_id: PositionId | None
    adverse_fill_offset: PriceOffset | None
    reference_to_fill: timedelta | None
    acceptance_to_fill: timedelta
    reference_observed_at: UtcTime | None
    accepted_at: UtcTime
    filled_at: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.fill_id, FillId):
            raise KernelValueError("FillDiagnostic.fill_id must be a FillId")
        if not isinstance(self.order_id, OrderId):
            raise KernelValueError("FillDiagnostic.order_id must be an OrderId")
        if not isinstance(self.acceptance_to_fill, timedelta):
            raise KernelValueError("FillDiagnostic.acceptance_to_fill must be a timedelta")
        for name, expected in (("accepted_at", UtcTime), ("filled_at", UtcTime)):
            if not isinstance(getattr(self, name), expected):
                raise KernelValueError(f"FillDiagnostic.{name} must be a {expected.__name__}")
        if (self.adverse_fill_offset is None) != (self.reference_observed_at is None):
            raise KernelValueError(
                "FillDiagnostic keeps the adverse fill offset exactly when the order carried a"
                " reference quote (D07 §6.2)"
            )


class CategoryKind(Enum):
    """集計7種（D07 §6.1）。**宣言順が `CATEGORY_COUNTS` 表の第1整列鍵である**。"""

    OPPORTUNITY_TERMINAL_REASON = "OPPORTUNITY_TERMINAL_REASON"
    ENTRY_REJECTION_REASON = "ENTRY_REJECTION_REASON"
    CLOSE_REJECTION_REASON = "CLOSE_REJECTION_REASON"
    EVALUATION_OUTCOME = "EVALUATION_OUTCOME"
    MISSING_INPUT_REASON = "MISSING_INPUT_REASON"
    CLOSE_CAUSE = "CLOSE_CAUSE"
    INTRABAR_METHOD = "INTRABAR_METHOD"


@dataclass(frozen=True, slots=True)
class CategoryCount:
    """集計の鍵1件（D07 §6.1・§8.1）。**0件の鍵も行として出す**。"""

    category: CategoryKind
    key: str
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.category, CategoryKind):
            raise KernelValueError("CategoryCount.category must be a CategoryKind")
        if not isinstance(self.key, str) or not self.key:
            raise KernelValueError("CategoryCount.key must be a non-empty str")
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise KernelValueError("CategoryCount.count must be an int")
        if self.count < 0:
            raise KernelValueError(f"CategoryCount.count must be >= 0, got {self.count}")


#: 取引機会の終端理由（D05 §7.2 の遷移2・3・5〜9・11）。
#:
#: D07 §6.1 は「D02 §8.1 の終端理由7語」と書いているが、D05 §7.2 の遷移9（run 末尾に
#: 残った機会の終端）が `RUN_END` を終端理由として使うため、実装の語彙
#: （`strategy.runtime.opportunities.TERMINAL_REASONS`）は8語である。評価は
#: `strategy.runtime` を import できない（契約 F8）ので写しを置き、
#: `tests/unit/evaluation/test_vocabularies.py` が実装の集合と一致することを機械検査する。
#: 並びは `ReasonCode` の宣言順に固定する（D07 §6.1）。
OPPORTUNITY_TERMINAL_KEYS: Final[tuple[str, ...]] = tuple(
    code.value
    for code in ReasonCode
    if code
    in {
        ReasonCode.RUN_END,
        ReasonCode.EXPIRED,
        ReasonCode.MARKET_STATE_INVALIDATED,
        ReasonCode.SUPERSEDED,
        ReasonCode.CLOSED_BY_ORDER_ACCEPTANCE,
        ReasonCode.CONCURRENCY_LIMIT_REACHED,
        ReasonCode.ORDER_ATTEMPT_REJECTED,
        ReasonCode.FULFILLED_BY_ORDER_ACCEPTANCE,
    }
)

#: 受付前拒否の理由6語（D06 §5.2）。並びは `ReasonCode` の宣言順。
ADMISSION_REJECTION_KEYS: Final[tuple[str, ...]] = tuple(
    code.value
    for code in ReasonCode
    if code
    in {
        ReasonCode.RISK,
        ReasonCode.NO_CANDIDATE,
        ReasonCode.RUN_END,
        ReasonCode.DATA_ERROR,
        ReasonCode.CARRY_NOT_ALLOWED,
        ReasonCode.PROTECTION_INVALID,
    }
)

#: 評価の結果区分5語（D05 §6.4 の `EvaluationOutcome`）。判断履歴の `outcome_kind` 列の値。
#:
#: 段階3 で待機中（`WAITING`）と追い越しで閉じた（`SUPERSEDED`）が加わった（D07 §6.1 v1.4）。
#: 語彙に無い鍵として後ろに足す扱いにはせず、**0件でも行として出す**。そうしないと待機だけが
#: 起きた run で `SUPERSEDED` の行が出ず、遅延シナリオごとに集計の行の集合が変わって並べて
#: 比べられない。その帰結として段階2 の run の集計にも0件の行が2行増える。
EVALUATION_OUTCOME_KEYS: Final[tuple[str, ...]] = (
    "EVALUATED",
    "SKIPPED",
    "FAILED",
    "WAITING",
    "SUPERSEDED",
)

#: 評価見送りの診断コード4語（D02 §8.3）。
MISSING_INPUT_KEYS: Final[tuple[str, ...]] = tuple(reason.value for reason in MissingInputReason)

#: 決済の執行契機4語（D06 §3 の `CloseCause`）。
CLOSE_CAUSE_KEYS: Final[tuple[str, ...]] = tuple(cause.value for cause in CloseCause)

#: 足内競合の解決方法3語（D06 §3 の `ResolutionMethod`）。
#:
#: 評価は `backtest.execution` を import できない（契約 F4）ため写しを置く。判断履歴の
#: 書き出し側（`backtest.trace.recorder`）も同じ理由で列名を写しており、実装の列挙との
#: 一致は `tests/unit/evaluation/test_vocabularies.py` が機械検査する。
INTRABAR_METHOD_KEYS: Final[tuple[str, ...]] = (
    "SINGLE_HIT",
    "RESOLVED_BY_CHILD",
    "UNRESOLVED_SL_PRIORITY",
)

#: 集計ごとの鍵の語彙（D07 §6.1）。**語を足さない**（D07 §1.1）。
CATEGORY_KEYS: Final[dict[CategoryKind, tuple[str, ...]]] = {
    CategoryKind.OPPORTUNITY_TERMINAL_REASON: OPPORTUNITY_TERMINAL_KEYS,
    CategoryKind.ENTRY_REJECTION_REASON: ADMISSION_REJECTION_KEYS,
    CategoryKind.CLOSE_REJECTION_REASON: ADMISSION_REJECTION_KEYS,
    CategoryKind.EVALUATION_OUTCOME: EVALUATION_OUTCOME_KEYS,
    CategoryKind.MISSING_INPUT_REASON: MISSING_INPUT_KEYS,
    CategoryKind.CLOSE_CAUSE: CLOSE_CAUSE_KEYS,
    CategoryKind.INTRABAR_METHOD: INTRABAR_METHOD_KEYS,
}

#: 指標ごとの値の種別（D07 §5.2 の「種別」欄）。
METRIC_KINDS: Final[dict[MetricId, MetricKind]] = {
    MetricId.NET_PROFIT: MetricKind.AMOUNT,
    MetricId.CLOSED_TRADE_PROFIT: MetricKind.AMOUNT,
    MetricId.TRADE_COUNT: MetricKind.COUNT,
    MetricId.WIN_RATE: MetricKind.RATIO,
    MetricId.MAX_DRAWDOWN_MTM: MetricKind.AMOUNT,
    MetricId.MAX_DRAWDOWN_MTM_RATE: MetricKind.RATIO,
    MetricId.MAX_DRAWDOWN_BALANCE: MetricKind.AMOUNT,
    MetricId.MAX_DRAWDOWN_BALANCE_RATE: MetricKind.RATIO,
    MetricId.EXPOSURE_RATE: MetricKind.RATIO,
    MetricId.COST_CHARGED_TOTAL: MetricKind.AMOUNT,
    MetricId.COST_PRICE_EMBEDDED_TOTAL: MetricKind.AMOUNT,
    MetricId.MAX_ADVERSE_FILL_OFFSET: MetricKind.PRICE_OFFSET,
    MetricId.END_EQUITY_MTM: MetricKind.AMOUNT,
    MetricId.HYPOTHETICAL_CLOSED_PROFIT: MetricKind.AMOUNT,
    MetricId.NET_RETURN_RATE: MetricKind.RATIO,
}

#: 指標ごとに読む判断履歴の表（D07 §5.2 の「入力」欄）。末尾の集計から取る指標は空。
METRIC_INPUTS: Final[dict[MetricId, tuple[TraceTable, ...]]] = {
    MetricId.NET_PROFIT: (TraceTable.LEDGER_SNAPSHOTS,),
    MetricId.CLOSED_TRADE_PROFIT: (TraceTable.POSITIONS,),
    MetricId.TRADE_COUNT: (TraceTable.POSITIONS,),
    MetricId.WIN_RATE: (TraceTable.POSITIONS,),
    MetricId.MAX_DRAWDOWN_MTM: (TraceTable.LEDGER_SNAPSHOTS,),
    MetricId.MAX_DRAWDOWN_MTM_RATE: (TraceTable.LEDGER_SNAPSHOTS,),
    MetricId.MAX_DRAWDOWN_BALANCE: (TraceTable.LEDGER_SNAPSHOTS,),
    MetricId.MAX_DRAWDOWN_BALANCE_RATE: (TraceTable.LEDGER_SNAPSHOTS,),
    MetricId.EXPOSURE_RATE: (TraceTable.FILLS, TraceTable.POSITIONS),
    MetricId.COST_CHARGED_TOTAL: (),
    MetricId.COST_PRICE_EMBEDDED_TOTAL: (),
    MetricId.MAX_ADVERSE_FILL_OFFSET: (TraceTable.ORDERS, TraceTable.FILLS),
    MetricId.END_EQUITY_MTM: (),
    MetricId.HYPOTHETICAL_CLOSED_PROFIT: (),
    MetricId.NET_RETURN_RATE: (TraceTable.LEDGER_SNAPSHOTS,),
}

#: 未解決の足内競合が**無くても**付く注記（D07 §7.2 の表）。
_BASE_CAVEATS: Final[dict[MetricId, tuple[MetricCaveat, ...]]] = {
    MetricId.NET_PROFIT: (MetricCaveat.SWAP_NOT_MODELED,),
    MetricId.CLOSED_TRADE_PROFIT: (
        MetricCaveat.SWAP_NOT_MODELED,
        MetricCaveat.OPEN_POSITION_EXCLUDED,
        MetricCaveat.ENTRY_COST_EXCLUDED,
    ),
    MetricId.TRADE_COUNT: (),
    MetricId.WIN_RATE: (),
    MetricId.MAX_DRAWDOWN_MTM: (),
    MetricId.MAX_DRAWDOWN_MTM_RATE: (),
    MetricId.MAX_DRAWDOWN_BALANCE: (MetricCaveat.OPEN_POSITION_EXCLUDED,),
    MetricId.MAX_DRAWDOWN_BALANCE_RATE: (MetricCaveat.OPEN_POSITION_EXCLUDED,),
    MetricId.EXPOSURE_RATE: (),
    MetricId.COST_CHARGED_TOTAL: (MetricCaveat.SWAP_NOT_MODELED,),
    MetricId.COST_PRICE_EMBEDDED_TOTAL: (
        MetricCaveat.SWAP_NOT_MODELED,
        MetricCaveat.PRICE_EMBEDDED_COST,
    ),
    MetricId.MAX_ADVERSE_FILL_OFFSET: (),
    MetricId.END_EQUITY_MTM: (
        MetricCaveat.SWAP_NOT_MODELED,
        MetricCaveat.OPEN_POSITION_EXCLUDED,
    ),
    MetricId.HYPOTHETICAL_CLOSED_PROFIT: (
        MetricCaveat.SWAP_NOT_MODELED,
        MetricCaveat.OPEN_POSITION_EXCLUDED,
    ),
    MetricId.NET_RETURN_RATE: (MetricCaveat.SWAP_NOT_MODELED,),
}

#: 未解決の足内競合が1件でもあるときだけ足す注記（D07 §7.2）。
_UNRESOLVED_INTRABAR_METRICS: Final[frozenset[MetricId]] = frozenset(
    {
        MetricId.NET_PROFIT,
        MetricId.CLOSED_TRADE_PROFIT,
        MetricId.WIN_RATE,
        MetricId.NET_RETURN_RATE,
    }
)


def metric_caveats(metric_id: MetricId, *, unresolved_intrabar: bool) -> tuple[MetricCaveat, ...]:
    """指標1件に付ける注記（D07 §7.2）。宣言順に並べる。

    `UNRESOLVED_INTRABAR_PRESENT` は `BacktestResult.unresolved_intrabar_count > 0` の
    ときだけ付ける。常に付けると、解決済みの run でも未解決があるかのように読める。
    """
    caveats = set(_BASE_CAVEATS[metric_id])
    if unresolved_intrabar and metric_id in _UNRESOLVED_INTRABAR_METRICS:
        caveats.add(MetricCaveat.UNRESOLVED_INTRABAR_PRESENT)
    return tuple(item for item in MetricCaveat if item in caveats)


@dataclass(frozen=True, slots=True)
class Drawdown:
    """最大ドローダウンと、その最大値が出た時点までの最大値（D07 §5.2 の #5〜#8）。

    率（#6・#8）は「その最大値が出た時点の**これまでの最大値**」で割るので、金額だけを
    返すと分母をもう一度走査で求めることになり、同じ規則が2か所に割れる。
    """

    amount: Decimal
    peak: Decimal


def max_drawdown(values: Sequence[Decimal]) -> Drawdown | None:
    """昇順に並んだ系列の最大ドローダウン（D07 §5.2 の #5）。

    `max(これまでの最大値 − 現在値)` を1回の走査で求める。同点の場合は**先に現れた方**を
    採る（後から同じ深さになっても分母は動かさない）。行が1件も無ければ `None`。
    """
    if not values:
        return None
    peak = values[0]
    best = Drawdown(amount=decimal_from_int(0), peak=peak)
    for value in values:
        if value > peak:
            peak = value
        fall = peak - value
        if fall > best.amount:
            best = Drawdown(amount=fall, peak=peak)
    return best


def ratio_of(numerator: Decimal, denominator: Decimal) -> Decimal:
    """比率をカーネル精度で1回だけ割る（D07 §5.1・§9.1 の条件2）。

    呼び出し側が分母 0 を先に弾く。ここで 0 を返すと「0 という比率」と「定まらない」が
    区別できなくなる。
    """
    if denominator == 0:
        raise KernelValueError(
            "ratio_of requires a non-zero denominator; an undefined ratio is reported as"
            " Unavailable(UNDEFINED_DENOMINATOR) (D07 §10.3)"
        )
    with localcontext(kernel_context()):
        return numerator / denominator


def trade_outcome(realized: Money) -> TradeOutcome:
    """完了取引の勝敗（D07 §5.2 の #4）。

    `= 0` は `BREAK_EVEN` とし、勝ちに数えず分母には数える。勝ちへ丸めると、費用でちょうど
    相殺された取引が勝率を押し上げる。
    """
    if not isinstance(realized, Money):
        raise KernelValueError("trade_outcome requires a Money")
    if realized.amount > 0:
        return TradeOutcome.WIN
    if realized.amount < 0:
        return TradeOutcome.LOSS
    return TradeOutcome.BREAK_EVEN
