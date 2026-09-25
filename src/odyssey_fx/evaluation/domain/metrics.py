"""単一実行の指標・取引の記録・集計の語彙（D07 §5・§6・§7・§8.1）。

段階2の最小集合は**指標15件・集計7種・約定診断3項目**だった（D07 §11）。段階4 の
**指標集合 v2**（D07 v2.0 §5.5）で指標を19件、集計を8種（要求単位の集計、§6.3）にし、
取引の記録に取引単位の費用と入場費用を含む取引損益を足した（§7.3）。本モジュールは
それらの型と、値そのものを作る純粋な計算（走査・比率・勝敗・年率化・取引日の数え方）
だけを持つ。判断履歴の表を読む順序・整合検査・状態の決定は
`evaluation.application.evaluate_run` が行う。

**「値なし」を 0 や成功値へ置換しない**（D07 §5.1）。値が無い指標は `Unavailable` として
型で表し、行は必ず残す。行ごと落とすと「計算できなかった」と「集計し忘れ」を区別できない。

**丸めは行わない**（D07 §5.1、Q2 決定）。金額と価格差は `Decimal` の加減算だけで厳密に
求まり、比率は除算を1回だけカーネル精度（D02 §4.1）で行ってそのまま保存する。表示用の
桁は報告の関心であり、保存値に桁を決め打つと、同じ判断履歴から出した値が桁の変更で変わる。

**指標集合 v2 の4件（#16〜#19）は演算が複数回になる**ので、「除算を1回だけ」の代わりに
**演算の順序を式で固定し**、各演算をカーネル精度（28桁・`ROUND_HALF_EVEN`）で行って最終値を
丸めない（D07 §5.5、人間の決定3）。平方根は同じコンテキストの `Decimal.sqrt()` を使い、
`float` と非整数の指数のべき乗は使わない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
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
from odyssey_fx.common.time import Interval, ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.calendar import ClosureRule, TradingCalendar

__all__ = [
    "ADMISSION_REJECTION_KEYS",
    "CATEGORY_KEYS",
    "CLOSE_CAUSE_KEYS",
    "TRADING_DAYS_PER_YEAR",
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
    "annualized_return",
    "annualized_sharpe_ratio",
    "average_trade_profit",
    "max_drawdown",
    "metric_caveats",
    "profit_factor",
    "ratio_of",
    "trade_outcome",
    "trade_profit",
    "trading_day_ends",
]


class MetricId(Enum):
    """指標集合 v2 の19件（D07 §5.2 の15件＋§5.5 の4件）。

    **宣言順が `METRICS` 表の整列鍵である**（D07 §8.1）。番号は D07 §5.2・§5.5 の
    #1〜#19 にそのまま対応するので、並べ替えない。
    """

    #: #1 最後の台帳 snapshot の balance − 初期残高。
    NET_PROFIT = "NET_PROFIT"
    #: #2 完了取引の取引損益（入場費用込み、`trade_profit`）の合計（D07 §7.3）。
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
    #: #16 単純年率化リターン `(#15 × 260) ÷ N`（D07 §5.5）。
    ANNUALIZED_RETURN = "ANNUALIZED_RETURN"
    #: #17 年率化シャープレシオ（日次・無リスク金利 0、D07 §5.5）。
    ANNUALIZED_SHARPE_RATIO = "ANNUALIZED_SHARPE_RATIO"
    #: #18 プロフィットファクター（D07 §5.5）。
    PROFIT_FACTOR = "PROFIT_FACTOR"
    #: #19 平均取引損益 `Σ trade_profit ÷ 完了取引数`（D07 §5.5）。
    AVERAGE_TRADE_PROFIT = "AVERAGE_TRADE_PROFIT"


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
    """指標1件だけを見た人にも伝える必要のある注記4件（D07 §7.2・§7.3）。

    段階2 の `ENTRY_COST_EXCLUDED`（入場側の費用を含まない）は、指標集合 v2 で取引損益に
    入場費用を含めたので**列挙から外した**（D07 v2.0 §7.3）。

    **宣言順が `MetricRecord.caveats` の並びである**。同じ指標から常に同じ並びが出ないと
    結果のダイジェストが揺れる（D07 §9.1 の条件4）。
    """

    #: swap / rollover を計上していない（ADR-0029）。
    SWAP_NOT_MODELED = "SWAP_NOT_MODELED"
    #: 価格に反映済みで、balance から控除していない参考値。
    PRICE_EMBEDDED_COST = "PRICE_EMBEDDED_COST"
    #: 未決済建玉の評価に依存する、または未決済建玉を含まない。
    OPEN_POSITION_EXCLUDED = "OPEN_POSITION_EXCLUDED"
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
    """完了取引1件（D07 §5.2・§7.3・§8.1）。`TRADES` 表の行。

    `trade_seq` は整列鍵 `(entry_at, position_id)` で並べたあとの 1 起点の通し番号である。
    保存された表だけを見て順序を復元できるようにするために持つ。

    **v2.0 で足した7項目**（D07 §7.3）: 入場約定と決済約定の区分別の費用6つ（表9 の
    区分別の列をそのまま写す。`None` はその区分の**費用記録が無い**ことで、0 円に置き換え
    ない）と、入場費用を含む取引損益 `trade_profit = realized − entry_commission`。
    勝敗（`outcome`）は `trade_profit` の符号で決める（Q10 決定）。
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
    entry_commission: Money | None
    entry_slippage_in_price: Money | None
    entry_spread_in_price: Money | None
    close_commission: Money | None
    close_slippage_in_price: Money | None
    close_spread_in_price: Money | None
    trade_profit: Money

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
            ("trade_profit", Money),
        ):
            if not isinstance(getattr(self, name), expected):
                raise KernelValueError(f"TradeRecord.{name} must be a {expected.__name__}")
        for name in _TRADE_COST_FIELDS:
            value = getattr(self, name)
            if value is not None and not isinstance(value, Money):
                raise KernelValueError(f"TradeRecord.{name} must be a Money or None")
        if self.holding < timedelta(0):
            raise KernelValueError(f"TradeRecord.holding must be >= 0, got {self.holding}")
        if self.trade_profit != trade_profit(self.realized, self.entry_commission):
            raise KernelValueError(
                "TradeRecord.trade_profit must be realized − entry_commission (D07 §7.3);"
                f" got {self.trade_profit}"
            )
        if self.outcome is not trade_outcome(self.trade_profit):
            raise KernelValueError(
                "TradeRecord.outcome is decided by the sign of trade_profit (D07 §7.3, Q10)"
            )


#: 取引の記録が持つ区分別の費用6項目（D07 §7.3）。並びは入場3つ・決済3つ。
_TRADE_COST_FIELDS: Final[tuple[str, ...]] = (
    "entry_commission",
    "entry_slippage_in_price",
    "entry_spread_in_price",
    "close_commission",
    "close_slippage_in_price",
    "close_spread_in_price",
)


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
    """集計8種（D07 §6.1 の7種＋§6.3 の1種）。**宣言順が `CATEGORY_COUNTS` 表の第1整列鍵**。

    8種目の `EVALUATION_REQUEST_FINAL_OUTCOME` は、評価要求ごとに**最後の記録**の結果区分を
    1件と数える（要求単位。D07 §6.3、Q13 決定）。既存の `EVALUATION_OUTCOME` は記録単位の
    まま変えない。2つを並べると「待機に入った記録が何件あり、その要求が最終的にどう決着
    したか」が読める。
    """

    OPPORTUNITY_TERMINAL_REASON = "OPPORTUNITY_TERMINAL_REASON"
    ENTRY_REJECTION_REASON = "ENTRY_REJECTION_REASON"
    CLOSE_REJECTION_REASON = "CLOSE_REJECTION_REASON"
    EVALUATION_OUTCOME = "EVALUATION_OUTCOME"
    MISSING_INPUT_REASON = "MISSING_INPUT_REASON"
    CLOSE_CAUSE = "CLOSE_CAUSE"
    INTRABAR_METHOD = "INTRABAR_METHOD"
    EVALUATION_REQUEST_FINAL_OUTCOME = "EVALUATION_REQUEST_FINAL_OUTCOME"


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
    # 語彙は記録単位の集計と同じ5語（D05 §6.4。D07 §6.3）。
    CategoryKind.EVALUATION_REQUEST_FINAL_OUTCOME: EVALUATION_OUTCOME_KEYS,
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
    MetricId.ANNUALIZED_RETURN: MetricKind.RATIO,
    MetricId.ANNUALIZED_SHARPE_RATIO: MetricKind.RATIO,
    MetricId.PROFIT_FACTOR: MetricKind.RATIO,
    MetricId.AVERAGE_TRADE_PROFIT: MetricKind.AMOUNT,
}

#: 指標ごとに読む判断履歴の表（D07 §5.2・§5.5 の「入力」欄）。末尾の集計から取る指標は空。
#: run manifest と取引カレンダーは判断履歴の表ではないので載せない。
METRIC_INPUTS: Final[dict[MetricId, tuple[TraceTable, ...]]] = {
    MetricId.NET_PROFIT: (TraceTable.LEDGER_SNAPSHOTS,),
    # v2.0 で入場約定の手数料（表9）を引くので、表9 も入力になる（D07 §7.3）。
    MetricId.CLOSED_TRADE_PROFIT: (TraceTable.FILLS, TraceTable.POSITIONS),
    MetricId.TRADE_COUNT: (TraceTable.POSITIONS,),
    # 勝敗を入場費用込みの取引損益で決めるので、表9 も入力になる（D07 §7.3、Q10 決定）。
    MetricId.WIN_RATE: (TraceTable.FILLS, TraceTable.POSITIONS),
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
    MetricId.ANNUALIZED_RETURN: (TraceTable.LEDGER_SNAPSHOTS,),
    MetricId.ANNUALIZED_SHARPE_RATIO: (TraceTable.LEDGER_SNAPSHOTS,),
    MetricId.PROFIT_FACTOR: (TraceTable.FILLS, TraceTable.POSITIONS),
    MetricId.AVERAGE_TRADE_PROFIT: (TraceTable.FILLS, TraceTable.POSITIONS),
}

#: 未解決の足内競合が**無くても**付く注記（D07 §7.2 の表、v2.0 の §5.5・§7.3）。
_BASE_CAVEATS: Final[dict[MetricId, tuple[MetricCaveat, ...]]] = {
    MetricId.NET_PROFIT: (MetricCaveat.SWAP_NOT_MODELED,),
    # 指標集合 v2 では入場費用を含めるので `ENTRY_COST_EXCLUDED` を付けない（D07 §7.3）。
    MetricId.CLOSED_TRADE_PROFIT: (
        MetricCaveat.SWAP_NOT_MODELED,
        MetricCaveat.OPEN_POSITION_EXCLUDED,
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
    MetricId.ANNUALIZED_RETURN: (MetricCaveat.SWAP_NOT_MODELED,),
    MetricId.ANNUALIZED_SHARPE_RATIO: (MetricCaveat.SWAP_NOT_MODELED,),
    MetricId.PROFIT_FACTOR: (
        MetricCaveat.SWAP_NOT_MODELED,
        MetricCaveat.OPEN_POSITION_EXCLUDED,
    ),
    MetricId.AVERAGE_TRADE_PROFIT: (
        MetricCaveat.SWAP_NOT_MODELED,
        MetricCaveat.OPEN_POSITION_EXCLUDED,
    ),
}

#: 未解決の足内競合が1件でもあるときだけ足す注記（D07 §7.2、v2.0 の §5.5）。
_UNRESOLVED_INTRABAR_METRICS: Final[frozenset[MetricId]] = frozenset(
    {
        MetricId.NET_PROFIT,
        MetricId.CLOSED_TRADE_PROFIT,
        MetricId.WIN_RATE,
        MetricId.NET_RETURN_RATE,
        MetricId.ANNUALIZED_RETURN,
        MetricId.PROFIT_FACTOR,
        MetricId.AVERAGE_TRADE_PROFIT,
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


def trade_outcome(profit: Money) -> TradeOutcome:
    """完了取引の勝敗（D07 §5.2 の #4）。

    指標集合 v2 では**入場費用込みの取引損益（`trade_profit`）**を渡す（D07 §7.3、Q10 決定）。
    `= 0` は `BREAK_EVEN` とし、勝ちに数えず分母には数える。勝ちへ丸めると、費用でちょうど
    相殺された取引が勝率を押し上げる。
    """
    if not isinstance(profit, Money):
        raise KernelValueError("trade_outcome requires a Money")
    if profit.amount > 0:
        return TradeOutcome.WIN
    if profit.amount < 0:
        return TradeOutcome.LOSS
    return TradeOutcome.BREAK_EVEN


def trade_profit(realized: Money, entry_commission: Money | None) -> Money:
    """入場費用を含む取引損益 `realized − entry_commission`（D07 §7.3）。

    建玉の確定損益（`realized`）は決済側の手数料だけを含み、入場の手数料は約定時に残高へ
    計上されている（上位 §4.7.15 C）。入場の手数料を引けば、取引1件の損益が balance の動きと
    一致する。**価格に反映済みの費用（滑り・提示価格の幅）は引かない**（二重計上になる。
    D07 §7.2）。入場の手数料の記録が無い（`None`）ときは 0 として引く。
    """
    if not isinstance(realized, Money):
        raise KernelValueError("trade_profit requires the realized profit as a Money")
    if entry_commission is None:
        return realized
    if not isinstance(entry_commission, Money):
        raise KernelValueError("trade_profit requires the entry commission as a Money or None")
    return realized - entry_commission


#: 年率化の係数（年 260 取引日 = 週5日 × 52週）。**指標集合 v2 の定義の一部**であり、設定で
#: 変えない（変えると同じ run の年率化の値が設定で変わる。D07 §5.5）。
TRADING_DAYS_PER_YEAR: Final = 260


def trading_day_ends(calendar: TradingCalendar, interval: Interval) -> tuple[UtcTime, ...] | None:
    """run 区間に終わりが入る取引日の、終わりの時刻の列（D07 §5.5 の `N`、Q8 決定）。

    取引日の区間は、取引カレンダーが取引日単位の休場に使う区間（D03 §3.4.1: 現地日付
    `d` の前日の取引日の境界〜`d` の取引日の境界。初版カレンダーでは NY 17:00〜17:00）を
    そのまま使う。取引日の境界と夏時間の解決を評価側で決め直さないためである。

    **数える取引日**は、終わりが `(interval.start, interval.end]` にあり、かつ**その区間に
    開場時間が少しでもある**もの（`calendar.sessions` が空でない）である。週末（金曜 17:00〜
    日曜 17:00）と取引日単位の休場は開場時間が無いので数えない。

    取引日の境界が1つに決まらないカレンダー（週の開閉の時刻が違う）では `None` を返す
    （D03 §3.4.1 の `trading_day_boundary`）。
    """
    if not isinstance(calendar, TradingCalendar):
        raise KernelValueError("trading_day_ends requires a TradingCalendar")
    if not isinstance(interval, Interval):
        raise KernelValueError("trading_day_ends requires an Interval")
    boundary = calendar.trading_day_boundary
    if boundary is None:
        return None
    first: date = interval.start.value.astimezone(calendar.tz).date()
    last: date = interval.end.value.astimezone(calendar.tz).date() + timedelta(days=1)
    ends: list[UtcTime] = []
    day = first
    while day <= last:
        span = ClosureRule(local_date=day, covers_trading_day=True).utc_interval(
            calendar.tz, trading_day_boundary=boundary
        )
        if interval.start < span.end <= interval.end and calendar.sessions(span):
            ends.append(span.end)
        day = day + timedelta(days=1)
    return tuple(ends)


def annualized_return(net_return: Decimal, trading_days: int) -> Decimal | None:
    """#16 単純年率化リターン `(#15 × 260) ÷ N`（D07 §5.5）。**この順に計算する**。

    `N = 0` なら `None`（呼び出し側が `UNDEFINED_DENOMINATOR` にする）。
    """
    if not isinstance(net_return, Decimal) or not net_return.is_finite():
        raise KernelValueError("annualized_return requires a finite Decimal")
    if isinstance(trading_days, bool) or not isinstance(trading_days, int) or trading_days < 0:
        raise KernelValueError("annualized_return requires a non-negative int of trading days")
    if trading_days == 0:
        return None
    with localcontext(kernel_context()):
        scaled = net_return * decimal_from_int(TRADING_DAYS_PER_YEAR)
        return scaled / decimal_from_int(trading_days)


def annualized_sharpe_ratio(equities: Sequence[Decimal]) -> Decimal | MetricUnavailableReason:
    """#17 年率化シャープレシオ（日次・無リスク金利 0、D07 §5.5）。

    `equities` は `E_0, E_1, …, E_N`（`E_0` は初期残高、`E_k` は k 番目の取引日の終わりの
    資産）。式は D07 §5.5 のとおり**この順に**計算し、各演算をカーネル精度で行う。

    1. `r_k = E_k ÷ E_{k−1} − 1`（k = 1〜N）
    2. `m = (Σ r_k) ÷ N`
    3. `v = (Σ (r_k − m)²) ÷ (N − 1)`
    4. `s = v.sqrt()`
    5. `(m ÷ s) × 260.sqrt()`

    値が定まらないときは理由を返す: `N < 2` なら `NO_OBSERVATIONS`、`E_{k−1} = 0` または
    `s = 0` なら `UNDEFINED_DENOMINATOR`（無限大を値にしない）。
    """
    values = tuple(equities)
    for value in values:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise KernelValueError("annualized_sharpe_ratio requires finite Decimals")
    days = len(values) - 1
    if days < 2:
        return MetricUnavailableReason.NO_OBSERVATIONS
    with localcontext(kernel_context()):
        returns: list[Decimal] = []
        for previous, current in zip(values, values[1:], strict=False):
            if previous == 0:
                return MetricUnavailableReason.UNDEFINED_DENOMINATOR
            returns.append(current / previous - decimal_from_int(1))
        total = decimal_from_int(0)
        for value in returns:
            total = total + value
        mean = total / decimal_from_int(days)
        squares = decimal_from_int(0)
        for value in returns:
            deviation = value - mean
            squares = squares + deviation * deviation
        variance = squares / decimal_from_int(days - 1)
        spread = variance.sqrt()
        if spread == 0:
            return MetricUnavailableReason.UNDEFINED_DENOMINATOR
        return (mean / spread) * decimal_from_int(TRADING_DAYS_PER_YEAR).sqrt()


def profit_factor(profits: Sequence[Money]) -> Decimal | MetricUnavailableReason:
    """#18 プロフィットファクター（D07 §5.5）。

    `(勝ち取引の trade_profit の合計) ÷ (負け取引の trade_profit の合計の絶対値)`。勝敗は
    `trade_outcome` と同じ符号の規則で決める。0取引なら `NO_TRADES`、負け取引が無ければ
    `UNDEFINED_DENOMINATOR`（無限大を値にしない）。
    """
    if not profits:
        return MetricUnavailableReason.NO_TRADES
    wins = decimal_from_int(0)
    losses = decimal_from_int(0)
    has_loss = False
    with localcontext(kernel_context()):
        for profit in profits:
            outcome = trade_outcome(profit)
            if outcome is TradeOutcome.WIN:
                wins = wins + profit.amount
            elif outcome is TradeOutcome.LOSS:
                losses = losses + profit.amount
                has_loss = True
        if not has_loss:
            return MetricUnavailableReason.UNDEFINED_DENOMINATOR
        return wins / abs(losses)


def average_trade_profit(profits: Sequence[Money]) -> Money | None:
    """#19 平均取引損益 `(Σ trade_profit) ÷ 完了取引数`（D07 §5.5）。

    金額だが除算を含むので、カーネル精度で1回割って丸めない。0取引なら `None`（呼び出し側が
    `NO_TRADES` にする）。
    """
    if not profits:
        return None
    total = profits[0]
    for profit in profits[1:]:
        total = total + profit
    with localcontext(kernel_context()):
        return Money(total.amount / decimal_from_int(len(profits)), total.currency)
