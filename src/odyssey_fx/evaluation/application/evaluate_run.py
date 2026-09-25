"""判断履歴から単一 run の数値の結果を作るユースケース（D07 §4〜§10）。

入力は4つである（D07 §4.1。Q1 決定の3つに、段階4 の Q8 決定で取引カレンダーを足した）。

1. `BacktestResult`（引数）
2. `RunManifest`（`ResultRepository.read_manifest`。読めなければ `ManifestReadFailure`）
3. 判断履歴の9表（`ResultRepository.read_table`）
4. 取引カレンダー（引数。run manifest の `calendar_ref` と一致することを C10 で確かめる）

**評価は run を実行し直さない。判断履歴を書き換えない。生の市場データを読み直さない。**
カレンダーは市場データではないので、Q1 の決定が退けた経路（as-of の規則とアクセス分類の
許可を評価側にも置くこと）は生じない（D07 §4.1 v2.0）。

**読む列は D07 §4.2 の表がそのまま正本である**（v2.0 で表2 の `request_id` /
`decision_time`、表9 の区分別の費用の列を足した）。処理点は `(時刻, フェーズ, 通し番号)` の
3つで1つなので、フェーズの順位は run manifest が記録しているフェーズ集合から引く（D06 §9.3）。

**値が読めないことで評価を中断しない**（D07 §10.4、根本対処 R5）。評価は次の順に進む。

1. 表と必須列の有無を見る（C1。値は読まない）。
2. 読めた表の全セルを、列ごとの規則（空を許すか・どの型に直すか・どの語彙か）で解釈し、
   読めない値を集める（C9）。
3. 残りの検査を、**その検査が読む列に読めない値が無いときだけ**実施する。読めない値のある
   列（または無い表、読めない run manifest）を読む検査は `UNREADABLE` として残す。
4. 致命の検査がすべて合格したときだけ、指標・集計・取引・診断を作る。ここで例外が出たら
   それは入力の欠陥ではなく実装の誤りである（D01 §2.2 規則5）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from odyssey_fx.backtest.domain.fills import CostKind
from odyssey_fx.backtest.domain.orders import CloseCause, OrderSide
from odyssey_fx.backtest.domain.positions import PositionStatus
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.recorder import TraceTable, canonical_text, flatten_row
from odyssey_fx.backtest.trace.result import BacktestResult, FinalSummaries, RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AttemptId,
    EvaluationId,
    FillId,
    OpportunityId,
    OrderId,
    PositionId,
    RequestId,
    SequentialId,
)
from odyssey_fx.common.money import (
    CurrencyCode,
    Money,
    Price,
    PriceOffset,
    Quantity,
    decimal_from_int,
    decimal_from_str,
)
from odyssey_fx.common.refs import CodeDigest, ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import PhaseRank, ProcessingPoint, UtcTime
from odyssey_fx.evaluation.application.manifest import (
    CalendarRef,
    EvaluationManifest,
    EvaluationTable,
    run_evaluation_id,
)
from odyssey_fx.evaluation.application.ports import (
    ColumnValueKind,
    ManifestReadFailure,
    ResultRepository,
    TableReadResult,
    TraceColumnSpec,
)
from odyssey_fx.evaluation.domain.metrics import (
    CATEGORY_KEYS,
    METRIC_INPUTS,
    AmountValue,
    CategoryCount,
    CategoryKind,
    CountValue,
    FillDiagnostic,
    MetricId,
    MetricKind,
    MetricRecord,
    MetricUnavailableReason,
    MetricValue,
    PriceOffsetValue,
    RatioValue,
    TradeOutcome,
    TradeRecord,
    Unavailable,
    annualized_return,
    annualized_sharpe_ratio,
    average_trade_profit,
    max_drawdown,
    metric_caveats,
    profit_factor,
    ratio_of,
    trade_outcome,
    trade_profit,
    trading_day_ends,
)
from odyssey_fx.evaluation.domain.status import (
    CHECK_ALL_VALUES_READABLE,
    CHECK_CALENDAR_MATCHES_RUN,
    CHECK_ID_CHAIN_COMPLETE,
    CHECK_INPUT_KEYS_UNIQUE,
    CHECK_LEVELS,
    CHECK_OPPORTUNITY_COUNT_MATCHES,
    CHECK_ORDER,
    CHECK_REALIZED_MATCHES_BALANCE,
    CHECK_REQUIRED_COLUMNS_PRESENT,
    CHECK_RUN_ID_CONSISTENT,
    CHECK_RUN_MANIFEST_READABLE,
    CHECK_SINGLE_ACCOUNT_CURRENCY,
    CHECK_SNAPSHOT_ORDER_MONOTONIC,
    CHECK_TRADE_COUNT_MATCHES,
    CheckLevel,
    CheckOutcome,
    ConsistencyCheckResult,
    EvaluationStatus,
)
from odyssey_fx.marketdata.domain.calendar import TradingCalendar

__all__ = [
    "COLUMN_SPECS",
    "INPUT_TABLES",
    "EvaluateRun",
    "EvaluationReport",
    "calendar_ref_text",
]

_STRING = ColumnValueKind.STRING
_DECIMAL = ColumnValueKind.DECIMAL
_INT = ColumnValueKind.INT
_TIME = ColumnValueKind.TIME
_ENUM = ColumnValueKind.ENUM
_LIST = ColumnValueKind.LIST_STRING

#: 読む9表（D07 §4.2）。**読まない表**（表1・6・8・10・12・15 と段階3 の表16〜19）は開かない。
INPUT_TABLES: Final[tuple[TraceTable, ...]] = (
    TraceTable.EVALUATIONS,
    TraceTable.OPPORTUNITY_TRANSITIONS,
    TraceTable.ORDER_REQUESTS,
    TraceTable.ATTEMPT_DECISIONS,
    TraceTable.ORDERS,
    TraceTable.FILLS,
    TraceTable.POSITIONS,
    TraceTable.INTRABAR_RESOLUTIONS,
    TraceTable.LEDGER_SNAPSHOTS,
)

#: 約定1件の区分別の費用の列（D06 §9.2 の表9、Q12 決定）。`(区分, 金額の列, 通貨の列)`。
_FILL_COST_COLUMNS: Final[tuple[tuple[CostKind, str, str], ...]] = (
    (CostKind.COMMISSION, "cost_commission_amount", "cost_commission_currency"),
    (
        CostKind.SLIPPAGE_IN_PRICE,
        "cost_slippage_in_price_amount",
        "cost_slippage_in_price_currency",
    ),
    (
        CostKind.SPREAD_IN_PRICE,
        "cost_spread_in_price_amount",
        "cost_spread_in_price_currency",
    ),
)

#: 表ごとに読む列（D07 §4.2）。**9表すべてで `run_id` を先頭列として要求する**。
#: 表ごとに書くと9回同じ列名が並び、1か所で落としても気付けない。
_COLUMNS: Final[dict[TraceTable, tuple[tuple[str, ColumnValueKind], ...]]] = {
    TraceTable.EVALUATIONS: (
        ("evaluation_id", _STRING),
        ("outcome_kind", _ENUM),
        ("outcome_diagnoses", _LIST),
        ("outcome_reason_code", _ENUM),
        # v2.0: 評価要求ごとの最終の結果区分（D07 §4.2・§6.3）。
        ("request_id", _STRING),
        ("decision_time", _TIME),
    ),
    TraceTable.OPPORTUNITY_TRANSITIONS: (
        ("opportunity_id", _STRING),
        ("at_time", _TIME),
        ("at_phase", _STRING),
        ("at_sequence", _INT),
        ("to_state", _ENUM),
        ("reason_code", _ENUM),
    ),
    TraceTable.ORDER_REQUESTS: (
        ("attempt_id", _STRING),
        ("payload_kind", _ENUM),
        ("payload_opportunity_id", _STRING),
        ("payload_position_id", _STRING),
    ),
    TraceTable.ATTEMPT_DECISIONS: (
        ("attempt_id", _STRING),
        ("kind", _ENUM),
        ("order_id", _STRING),
        ("reason_code", _ENUM),
    ),
    TraceTable.ORDERS: (
        ("order_id", _STRING),
        ("attempt_id", _STRING),
        ("accepted_at_time", _TIME),
        ("side", _ENUM),
        ("terms_kind", _ENUM),
        ("terms_cause", _ENUM),
        ("terms_position_id", _STRING),
        ("terms_reference_quote_price", _DECIMAL),
        ("terms_reference_quote_observed_at", _TIME),
    ),
    TraceTable.FILLS: (
        ("fill_id", _STRING),
        ("order_id", _STRING),
        ("position_id", _STRING),
        ("processed_at_time", _TIME),
        ("processed_at_phase", _STRING),
        ("processed_at_sequence", _INT),
        ("price", _DECIMAL),
        ("quantity", _DECIMAL),
        # v2.0: 取引単位の費用と入場費用を含む取引損益（D07 §4.2・§7.3）。
        *(
            column
            for _, amount, currency in _FILL_COST_COLUMNS
            for column in ((amount, _DECIMAL), (currency, _STRING))
        ),
    ),
    TraceTable.POSITIONS: (
        ("position_id", _STRING),
        ("symbol", _STRING),
        ("side", _ENUM),
        ("quantity", _DECIMAL),
        ("entry_price", _DECIMAL),
        ("entry_fill_id", _STRING),
        ("opened_at_time", _TIME),
        ("opened_at_phase", _STRING),
        ("opened_at_sequence", _INT),
        ("status", _ENUM),
        ("close_fill_id", _STRING),
        ("realized_amount", _DECIMAL),
        ("realized_currency", _STRING),
    ),
    TraceTable.INTRABAR_RESOLUTIONS: (
        ("fill_id", _STRING),
        ("position_id", _STRING),
        ("method", _ENUM),
    ),
    TraceTable.LEDGER_SNAPSHOTS: (
        ("at_time", _TIME),
        ("at_phase", _STRING),
        ("at_sequence", _INT),
        ("balance_amount", _DECIMAL),
        ("balance_currency", _STRING),
        ("equity_amount", _DECIMAL),
        ("equity_currency", _STRING),
    ),
}

#: 実際に読み出しへ渡す宣言。`run_id` を先頭に足し、すべて必須列とする（D07 §4.2・§4.3）。
COLUMN_SPECS: Final[dict[TraceTable, tuple[TraceColumnSpec, ...]]] = {
    table: (
        TraceColumnSpec(table=table, column="run_id", value_kind=_STRING, required=True),
        *(
            TraceColumnSpec(table=table, column=name, value_kind=kind, required=True)
            for name, kind in columns
        ),
    )
    for table, columns in _COLUMNS.items()
}

#: 表ごとの主キー（D06 §9.2 の「主キー」欄）。処理点は3列で1つの値である（D06 §9.1）。
_PRIMARY_KEYS: Final[dict[TraceTable, tuple[str, ...]]] = {
    TraceTable.EVALUATIONS: ("evaluation_id",),
    TraceTable.OPPORTUNITY_TRANSITIONS: (
        "opportunity_id",
        "at_time",
        "at_phase",
        "at_sequence",
    ),
    TraceTable.ORDER_REQUESTS: ("attempt_id",),
    TraceTable.ATTEMPT_DECISIONS: ("attempt_id",),
    TraceTable.ORDERS: ("order_id",),
    TraceTable.FILLS: ("fill_id",),
    TraceTable.POSITIONS: ("position_id",),
    TraceTable.INTRABAR_RESOLUTIONS: ("fill_id",),
    TraceTable.LEDGER_SNAPSHOTS: ("at_time", "at_phase", "at_sequence"),
}


#: 注文の種別（D06 §3 の `AcceptedEntryTerms` / `AcceptedCloseTerms` の区分タグ）。
_ENTRY_TERMS: Final = "ENTRY_TERMS"
_CLOSE_TERMS: Final = "CLOSE_TERMS"
#: 発注要求の種別（D06 §3 の `EntryRequest` / `CloseRequest`）。
_ENTRY_REQUEST: Final = "ENTRY_REQUEST"
_CLOSE_REQUEST: Final = "CLOSE_REQUEST"
#: 発注試行の結果（D06 §6 の `AttemptAccepted` / `AttemptRejected`）。
_ACCEPTED: Final = "ACCEPTED"
_REJECTED: Final = "REJECTED"
#: 建玉の状態（D06 §8.2 の `PositionStatus`）と取引機会の終端状態（D05 §7.1）。
_CLOSED: Final = PositionStatus.CLOSED.value
_TERMINATED: Final = "TERMINATED"
#: 評価の結果区分のうち、見送り（D05 §6.4 の `Skipped`）。
_SKIPPED: Final = "SKIPPED"


def calendar_ref_text(calendar: TradingCalendar) -> str:
    """run manifest の `calendar_ref` と同じ書き方のカレンダーの版参照（D06 §9.3）。

    run manifest の値は合成（`app.composition.calendar_ref_of`）が `"<id>@v<version>"` と
    書く。評価は `app` を import できないので同じ書き方をここにも置き、2つが一致することを
    `tests/unit/evaluation/test_calendar_input.py` が機械検査する。
    """
    return f"{calendar.id}@v{calendar.version}"


# --- 列ごとの読み取りの規則（D07 §10.4）----------------------------------------

#: 空（`None`）を許す列。**ここに無い列は設計上いつも埋まっている**（D07 §10.4）。条件付きで
#: 埋まる列（決済済みの建玉だけが持つ `close_fill_id` など）は空を許し、条件の食い違いは
#: 件数と外部キーの検査（C3・C4）が見る。
_NULLABLE: Final[frozenset[tuple[TraceTable, str]]] = frozenset(
    {
        (TraceTable.EVALUATIONS, "outcome_diagnoses"),
        (TraceTable.EVALUATIONS, "outcome_reason_code"),
        (TraceTable.OPPORTUNITY_TRANSITIONS, "reason_code"),
        (TraceTable.ORDER_REQUESTS, "payload_opportunity_id"),
        (TraceTable.ORDER_REQUESTS, "payload_position_id"),
        (TraceTable.ATTEMPT_DECISIONS, "order_id"),
        (TraceTable.ATTEMPT_DECISIONS, "reason_code"),
        (TraceTable.ORDERS, "terms_cause"),
        (TraceTable.ORDERS, "terms_position_id"),
        (TraceTable.ORDERS, "terms_reference_quote_price"),
        (TraceTable.ORDERS, "terms_reference_quote_observed_at"),
        (TraceTable.FILLS, "position_id"),
        *(
            (TraceTable.FILLS, column)
            for _, amount, currency in _FILL_COST_COLUMNS
            for column in (amount, currency)
        ),
        (TraceTable.POSITIONS, "close_fill_id"),
        (TraceTable.POSITIONS, "realized_amount"),
        (TraceTable.POSITIONS, "realized_currency"),
        (TraceTable.INTRABAR_RESOLUTIONS, "position_id"),
    }
)

#: 2列で1つの値を表す組。**片方だけが空なら、空の側は読めない値である**（どちらも空なのは
#: 「その値が無い」ことで、読めない値ではない。D07 §4.2 の費用の列の規則）。
_PAIRED_COLUMNS: Final[dict[TraceTable, tuple[tuple[str, str], ...]]] = {
    TraceTable.FILLS: tuple((amount, currency) for _, amount, currency in _FILL_COST_COLUMNS),
    TraceTable.ORDERS: (("terms_reference_quote_price", "terms_reference_quote_observed_at"),),
}

#: 連番 ID の列と、その型（D02 §7.1、D06 §9.2）。
_ID_TYPES: Final[dict[str, type[SequentialId]]] = {
    "evaluation_id": EvaluationId,
    "request_id": RequestId,
    "opportunity_id": OpportunityId,
    "attempt_id": AttemptId,
    "order_id": OrderId,
    "fill_id": FillId,
    "position_id": PositionId,
    "payload_opportunity_id": OpportunityId,
    "payload_position_id": PositionId,
    "terms_position_id": PositionId,
    "entry_fill_id": FillId,
    "close_fill_id": FillId,
}

#: 値を型に直して**計算に使う**列挙の列と、その語彙（D07 §10.4 の「列挙の語彙に無い」）。
#:
#: 集計の鍵として**だけ**使う列（結果区分・理由コード・足内競合の解決方法・取引機会の状態）は
#: ここに入れない。段階2 で確定した「語彙に無い鍵が判断履歴にあったら行として残す」
#: （D07 §6.1 v1.3）を保つためである（PR 本文の「解釈した点」）。
_ENUM_VOCABULARIES: Final[dict[tuple[TraceTable, str], frozenset[str]]] = {
    (TraceTable.ORDER_REQUESTS, "payload_kind"): frozenset({_ENTRY_REQUEST, _CLOSE_REQUEST}),
    (TraceTable.ATTEMPT_DECISIONS, "kind"): frozenset({_ACCEPTED, _REJECTED}),
    (TraceTable.ORDERS, "side"): frozenset(side.value for side in OrderSide),
    (TraceTable.ORDERS, "terms_kind"): frozenset({_ENTRY_TERMS, _CLOSE_TERMS}),
    (TraceTable.ORDERS, "terms_cause"): frozenset(cause.value for cause in CloseCause),
    (TraceTable.POSITIONS, "side"): frozenset(side.value for side in OrderSide),
    (TraceTable.POSITIONS, "status"): frozenset(status.value for status in PositionStatus),
}

#: 価格・数量として正の値でなければならない十進数の列（D02 §4.3・§4.4）。
_POSITIVE_DECIMAL_TYPES: Final[dict[str, Callable[[Decimal], object]]] = {
    "price": Price,
    "entry_price": Price,
    "terms_reference_quote_price": Price,
    "quantity": Quantity,
}


def _parse_sequence(text: str) -> int:
    value = int(text)
    if value < 0:
        raise KernelValueError(f"a processing sequence must be >= 0, got {value}")
    return value


def _cell_is_readable(
    table: TraceTable,
    spec: TraceColumnSpec,
    raw: str | None,
    phases: Mapping[str, PhaseRank] | None,
) -> bool:
    """セル1つが読めるか（D07 §10.4 の「読めない値」の定義に当たらないか）。

    読めない値は3つ: (1) 設計上いつも埋まっているはずの列が空、(2) 列挙の語彙に無い、
    (3) 十進数・時刻・識別子・通貨などとして解釈できない。
    """
    column = spec.column
    if raw is None:
        return (table, column) in _NULLABLE
    if raw == "":
        return False
    try:
        _parse_cell(table, spec, raw, phases)
    except (ValueError, TypeError):
        # `KernelValueError` は `ValueError` の派生（D02 §4.6）。`int()` と JSON の
        # 読み取りの失敗も `ValueError` として来る。
        return False
    return True


def _parse_cell(
    table: TraceTable,
    spec: TraceColumnSpec,
    raw: str,
    phases: Mapping[str, PhaseRank] | None,
) -> None:
    """セル1つを宣言した型に直す。直せなければ `ValueError` を送出する。"""
    column = spec.column
    kind = spec.value_kind
    if column in _ID_TYPES:
        _ID_TYPES[column].parse(raw)
    elif column.endswith("_phase"):
        # フェーズの名前は run manifest のフェーズ集合から引く（D06 §9.3）。manifest が
        # 読めないときは集合を確かめられないので、空でないことだけを見る（C11 が別に
        # 不合格になり、処理点を使う検査は manifest を読めないことで `UNREADABLE` になる）。
        if phases is not None and raw not in phases:
            raise KernelValueError(f"the run manifest declares no phase named {raw!r}")
    elif column.endswith("_currency"):
        CurrencyCode(raw)
    elif column == "symbol":
        Symbol(raw)
    elif kind is ColumnValueKind.TIME:
        UtcTime.parse(raw)
    elif kind is ColumnValueKind.INT:
        _parse_sequence(raw)
    elif kind is ColumnValueKind.DECIMAL:
        value = decimal_from_str(raw)
        positive = _POSITIVE_DECIMAL_TYPES.get(column)
        if positive is not None:
            positive(value)
    elif kind is ColumnValueKind.LIST_STRING:
        _missing_input_reasons(raw)
    elif kind is ColumnValueKind.ENUM:
        vocabulary = _ENUM_VOCABULARIES.get((table, column))
        if vocabulary is not None and raw not in vocabulary:
            raise KernelValueError(f"{raw!r} is not in the vocabulary of {table.value}.{column}")


@dataclass(frozen=True, slots=True)
class _Unreadable:
    """読めなかった値1件（D07 §10.4 の C9 の観測値）。"""

    table: TraceTable
    column: str
    key: tuple[str, ...]
    raw: str | None

    @property
    def sort_key(self) -> tuple[int, tuple[str, ...], int, str]:
        """整列鍵の順（表の宣言順・主キー・列の宣言順）。行の並びに依らない。"""
        columns = [spec.column for spec in COLUMN_SPECS[self.table]]
        return (
            INPUT_TABLES.index(self.table),
            self.key,
            columns.index(self.column),
            "" if self.raw is None else self.raw,
        )

    def describe(self) -> str:
        shown = "<empty>" if self.raw is None else self.raw
        return f"{self.table.value}.{self.column}[{','.join(self.key)}] = {shown}"


# --- 読み出した行の取り回し ---------------------------------------------------


class _Rows:
    """読み出した1表を、列名で引ける行の列として持つ。

    表そのものが無い・必須列が欠けている場合は空の列になり、`usable` が偽になる。
    """

    __slots__ = ("_rows", "result")

    def __init__(self, result: TableReadResult, specs: Sequence[TraceColumnSpec]) -> None:
        self.result = result
        index = {spec.column: position for position, spec in enumerate(specs)}
        self._rows = tuple(
            {name: row[position] for name, position in index.items()} for row in result.rows
        )

    @property
    def usable(self) -> bool:
        """表があり、要求した列が1つも欠けていない（C1 の表ごとの合格）。"""
        return self.result.table_present and not self.result.missing_columns

    @property
    def records(self) -> tuple[Mapping[str, str | None], ...]:
        """行の列。"""
        return self._rows


def _text(row: Mapping[str, str | None], column: str) -> str:
    value = row.get(column)
    if value is None:
        raise KernelValueError(f"the trace column {column!r} must not be empty on this row")
    return value


def _decimal(row: Mapping[str, str | None], column: str) -> Decimal:
    return decimal_from_str(_text(row, column))


def _optional_decimal(row: Mapping[str, str | None], column: str) -> Decimal | None:
    value = row.get(column)
    return None if value is None else decimal_from_str(value)


def _time(row: Mapping[str, str | None], column: str) -> UtcTime:
    return UtcTime.parse(_text(row, column))


def _optional_time(row: Mapping[str, str | None], column: str) -> UtcTime | None:
    value = row.get(column)
    return None if value is None else UtcTime.parse(value)


def _point(
    row: Mapping[str, str | None], prefix: str, phases: Mapping[str, PhaseRank]
) -> ProcessingPoint:
    """`*_time` / `*_phase` / `*_sequence` の3列から処理点を組み立てる（D06 §9.1）。

    フェーズの順位は run manifest が記録したフェーズ集合から引く。判断履歴の列はフェーズの
    名前しか持たないので、順位を評価側で決め打つと、フェーズ集合を変えた run の並びが
    実行時の因果順と食い違う。
    """
    return ProcessingPoint(
        time=_time(row, f"{prefix}_time"),
        phase=phases[_text(row, f"{prefix}_phase")],
        sequence=int(_text(row, f"{prefix}_sequence")),
    )


def _microseconds(duration: timedelta) -> Decimal:
    """期間をマイクロ秒の十進数にする（浮動小数を経由しない、ADR-0012）。"""
    return decimal_from_int(duration // timedelta(microseconds=1))


def _direction(side: OrderSide) -> Decimal:
    """方向 `d`（買いなら `+1`、売りなら `-1`）。"""
    return decimal_from_int(1 if side is OrderSide.BUY else -1)


def _index(
    rows: Sequence[Mapping[str, str | None]], column: str
) -> dict[str, list[Mapping[str, str | None]]]:
    """列の値で行を引けるようにする（検査用。同じ値の行が2つあっても全部持つ）。

    主キーの重複は C12 が見つけて不合格にする。検査の側でどちらかの行を黙って採ると
    （後勝ち）、行の並びで観測値が変わるので、**候補の行をすべて持ち、すべてが条件を
    満たすときだけ満たす**と読む。
    """
    indexed: dict[str, list[Mapping[str, str | None]]] = {}
    for row in rows:
        value = row.get(column)
        if value is not None:
            indexed.setdefault(value, []).append(row)
    return indexed


def _by_key(
    rows: Sequence[Mapping[str, str | None]], column: str
) -> dict[str, Mapping[str, str | None]]:
    """列の値で行を引けるようにする（指標を作るとき。主キーは C12 で一意と確かめてある）。"""
    indexed: dict[str, Mapping[str, str | None]] = {}
    for row in rows:
        value = row.get(column)
        if value is None:
            continue
        if value in indexed:  # pragma: no cover - C12 が合格したあとでは起きない
            raise KernelValueError(
                f"the trace column {column!r} must be unique but {value!r} appears twice;"
                " the input-key check (C12) should have failed first"
            )
        indexed[value] = row
    return indexed


def _fatal_failures(checks: Sequence[ConsistencyCheckResult]) -> int:
    """致命の水準で合格しなかった検査の件数（不合格と読めなかったの両方。D07 §10.4）。"""
    return sum(1 for item in checks if not item.passed and item.level is CheckLevel.FATAL)


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """1回の評価の結果（D07 §8.1・§10.1）。

    **どの状態でも5表すべてを書く**。指標を出さない状態では4表が0行になる。表の有無で状態を
    表すと、書き出しが途中で落ちた成果物と区別できない。
    """

    manifest: EvaluationManifest
    status: EvaluationStatus
    metrics: tuple[MetricRecord, ...]
    categories: tuple[CategoryCount, ...]
    trades: tuple[TradeRecord, ...]
    fill_diagnostics: tuple[FillDiagnostic, ...]
    checks: tuple[ConsistencyCheckResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, EvaluationManifest):
            raise KernelValueError("EvaluationReport.manifest must be an EvaluationManifest")
        if not isinstance(self.status, EvaluationStatus):
            raise KernelValueError("EvaluationReport.status must be an EvaluationStatus")
        if self.status is not self.manifest.status:
            raise KernelValueError(
                "EvaluationReport.status and its manifest must agree"
                f" ({self.status.value} != {self.manifest.status.value})"
            )
        if self.status is not EvaluationStatus.COMPLETED and (
            self.metrics or self.categories or self.trades or self.fill_diagnostics
        ):
            raise KernelValueError(
                "an evaluation that did not complete outputs no metrics, categories, trades or"
                " fill diagnostics; only the consistency checks and the manifest (D07 §10.1)"
            )

    @property
    def rows(self) -> dict[EvaluationTable, tuple[object, ...]]:
        """5表の行（D07 §8.1）。整列は作るときに済ませてある。"""
        return {
            EvaluationTable.METRICS: tuple(self.metrics),
            EvaluationTable.CATEGORY_COUNTS: tuple(self.categories),
            EvaluationTable.TRADES: tuple(self.trades),
            EvaluationTable.FILL_DIAGNOSTICS: tuple(self.fill_diagnostics),
            EvaluationTable.CONSISTENCY_CHECKS: tuple(self.checks),
        }


# --- 検査の文脈 ---------------------------------------------------------------

#: 検査が run manifest を読むことを表す印（依存の一覧に入れる）。
_MANIFEST: Final = "run manifest"


@dataclass(frozen=True, slots=True)
class _Context:
    """検査が共有する読み取りの結果。"""

    result: BacktestResult
    manifest: RunManifest | None
    reads: Mapping[TraceTable, _Rows]
    unreadable: tuple[_Unreadable, ...]

    @property
    def run_manifest(self) -> RunManifest:
        """読めた run manifest。検査は依存（`needs_manifest`）を確かめてから呼ぶ。"""
        if self.manifest is None:
            raise KernelValueError(
                "a check that reads the run manifest ran although the manifest was unreadable;"
                " it should have been recorded as UNREADABLE (D07 §10.4)"
            )
        return self.manifest

    @property
    def phases(self) -> Mapping[str, PhaseRank]:
        """run manifest が記録したフェーズ集合（名前で引く。D06 §9.3）。"""
        return {phase.name: phase for phase in self.run_manifest.phases.ordered()}

    def blocked(
        self, columns: Iterable[tuple[TraceTable, str]], *, needs_manifest: bool
    ) -> tuple[str, ...]:
        """検査が読む入力のうち、読めなかったもの（空なら検査を実施できる）。"""
        unreadable = {(item.table, item.column) for item in self.unreadable}
        items: set[str] = set()
        for table, column in columns:
            if not self.reads[table].usable:
                items.add(f"{table.value}: table or required columns missing")
            elif (table, column) in unreadable:
                items.add(f"{table.value}.{column}")
        if needs_manifest and self.manifest is None:
            items.add(_MANIFEST)
        return tuple(sorted(items))


def _result_of(
    check: str,
    *,
    outcome: CheckOutcome,
    expected: str,
    observed: str,
    table: TraceTable | None = None,
) -> ConsistencyCheckResult:
    return ConsistencyCheckResult(
        check=check,
        level=CHECK_LEVELS[check],
        outcome=outcome,
        table=table,
        expected=expected,
        observed=observed,
    )


def _judged(
    check: str, passed: bool, *, expected: str, observed: str, table: TraceTable | None = None
) -> ConsistencyCheckResult:
    return _result_of(
        check,
        outcome=CheckOutcome.PASSED if passed else CheckOutcome.FAILED,
        expected=expected,
        observed=observed,
        table=table,
    )


def _guarded(
    check: str,
    context: _Context,
    columns: Iterable[tuple[TraceTable, str]],
    run: Callable[[], ConsistencyCheckResult],
    *,
    needs_manifest: bool = False,
    table: TraceTable | None = None,
) -> ConsistencyCheckResult:
    """検査が読む入力が読めるときだけ実施し、読めなければ `UNREADABLE` として残す。

    **例外で検査を抜けることはしない**（D07 §10.4）。読めなかった入力の名前を観測値に残す。
    """
    blocked = context.blocked(columns, needs_manifest=needs_manifest)
    if blocked:
        return _result_of(
            check,
            outcome=CheckOutcome.UNREADABLE,
            expected=canonical_text(()),
            observed=canonical_text(blocked),
            table=table,
        )
    return run()


def _columns_of(table: TraceTable, *names: str) -> tuple[tuple[TraceTable, str], ...]:
    return tuple((table, name) for name in names)


#: 完了取引の組み立てに要る列（C3・C4 が読む）。
_TRADE_LINK_COLUMNS: Final[tuple[tuple[TraceTable, str], ...]] = (
    *_columns_of(
        TraceTable.POSITIONS,
        "position_id",
        "status",
        "entry_fill_id",
        "close_fill_id",
        "realized_amount",
    ),
    *_columns_of(TraceTable.FILLS, "fill_id"),
)


# --- ユースケース -------------------------------------------------------------


class EvaluateRun:
    """`evaluate(result, repository, metric_set_version, calendar) -> EvaluationReport`（D07 §3）。

    **具体クラス**である（D07 §3 v1.3、2026-09-22 の人間の決定）。評価を差し替える側は
    居らず（`app` が唯一の合成点）、実装は1つである。

    評価時のコードのダイジェストは**構築時に受け取る**（D07 §9.2、Q5 決定）。パッケージの
    ソース内容を読むのは入出力であり、`application` は入出力を持たない。算出は合成
    （`app.composition`）が D02 §9.4 と同じ規則で行う。
    """

    __slots__ = ("_code_digest",)

    def __init__(self, *, evaluation_code_digest: CodeDigest) -> None:
        if not isinstance(evaluation_code_digest, CodeDigest):
            raise KernelValueError("EvaluateRun requires a CodeDigest for the evaluating code")
        self._code_digest = evaluation_code_digest

    def evaluate(
        self,
        result: BacktestResult,
        repository: ResultRepository,
        metric_set_version: int,
        calendar: TradingCalendar,
    ) -> EvaluationReport:
        """判断履歴を読み、指標・集計・診断・整合検査・状態を作る（D07 §4〜§10）。"""
        if not isinstance(result, BacktestResult):
            raise KernelValueError("EvaluateRun.evaluate requires a BacktestResult")
        if not isinstance(calendar, TradingCalendar):
            raise KernelValueError("EvaluateRun.evaluate requires a TradingCalendar (D07 §4.1)")
        read = repository.read_manifest(result.run_id)
        if isinstance(read, ManifestReadFailure):
            manifest: RunManifest | None = None
            manifest_failure: ManifestReadFailure | None = read
        elif isinstance(read, RunManifest):
            manifest = read
            manifest_failure = None
        else:
            raise KernelValueError(
                "ResultRepository.read_manifest must return a RunManifest or a ManifestReadFailure"
            )

        reads = {
            table: _Rows(
                repository.read_table(result.run_id, table, COLUMN_SPECS[table]),
                COLUMN_SPECS[table],
            )
            for table in INPUT_TABLES
        }
        phases = (
            None if manifest is None else {phase.name: phase for phase in manifest.phases.ordered()}
        )
        context = _Context(
            result=result,
            manifest=manifest,
            reads=reads,
            unreadable=_scan_unreadable(reads, phases),
        )
        calendar_ref: CalendarRef = (calendar.id, calendar.version)

        checks = [
            _check_required_columns(reads),
            _check_run_id(context),
            _check_trade_count(context),
            _check_id_chain(context),
            _check_opportunity_count(context),
            _check_snapshot_order(context),
            _check_account_currency(context),
            _check_all_values_readable(context),
            _check_calendar(context, calendar),
            _check_manifest_readable(result, manifest_failure),
            _check_input_keys_unique(context),
        ]
        if result.status is RunStatus.COMPLETED:
            # 末尾の集計は正常完走した run だけが持つ（D06 §9.4）。存在しない値との比較を
            # 「不合格」として記録すると、完走しなかった run を「不整合な run」として説明する
            # ことになるので、この検査だけは行わない（D07 §10.1 の REJECTED【確定】）。
            checks.append(_check_realized_matches_balance(context))
        checks.sort(key=lambda item: CHECK_ORDER.index(item.check))

        fatal = _fatal_failures(checks)
        warnings = sum(1 for item in checks if not item.passed and item.level is CheckLevel.WARNING)
        unreadable_checks = sum(1 for item in checks if item.outcome is CheckOutcome.UNREADABLE)

        if result.status is not RunStatus.COMPLETED:
            # 正常完走していない run の指標は作らない（D07 §10.1、Q6 決定）。致命の検査が
            # 合格しなくても `REJECTED` を優先する（R1-D07-1 の決定）。原因（run が正常完走
            # していない）が先にあり、致命の不合格は件数と検査の表に全件残る。
            status = EvaluationStatus.REJECTED
        elif fatal:
            status = EvaluationStatus.FAILED
        else:
            status = EvaluationStatus.COMPLETED

        trades: tuple[TradeRecord, ...] = ()
        diagnostics: tuple[FillDiagnostic, ...] = ()
        categories: tuple[CategoryCount, ...] = ()
        metrics: tuple[MetricRecord, ...] = ()
        if status is EvaluationStatus.COMPLETED:
            # 致命の検査がすべて合格した（C9・C11 を含む）ので、読む列の値はすべて解釈でき、
            # run manifest もある。ここで例外が出たら実装の誤りである（D01 §2.2 規則5）。
            run_phases = context.phases
            trades = _build_trades(reads, run_phases)
            diagnostics = _build_fill_diagnostics(reads)
            categories = _build_categories(reads)
            metrics = _build_metrics(
                result, context.run_manifest, reads, run_phases, trades, calendar
            )

        report_rows = {
            EvaluationTable.METRICS: tuple(metrics),
            EvaluationTable.CATEGORY_COUNTS: tuple(categories),
            EvaluationTable.TRADES: tuple(trades),
            EvaluationTable.FILL_DIAGNOSTICS: tuple(diagnostics),
            EvaluationTable.CONSISTENCY_CHECKS: tuple(checks),
        }
        evaluation_manifest = EvaluationManifest(
            run_evaluation_id=run_evaluation_id(
                result.run_id, metric_set_version, self._code_digest, calendar_ref
            ),
            run_id=result.run_id,
            # run manifest を**内容で**指す参照（D07 §8.3【確定】）。入力とポリシーの群の
            # ダイジェスト（`ConfigDigest`、D06 §9.3）を使う。manifest が読めないときは
            # run 由来の5項目を `None` で書く（D07 §10.1.1 の R1-D07-4）。
            run_manifest_ref=None if manifest is None else manifest.config_digest.digest,
            metric_set_version=metric_set_version,
            calendar_ref=calendar_ref,
            evaluation_code_digest=self._code_digest,
            run_code_digest=None if manifest is None else manifest.code_digest,
            run_status=None if manifest is None else result.status,
            run_failure_reason=None if manifest is None else manifest.reason,
            account_currency=None if manifest is None else manifest.config.account.currency,
            swap_modeled=result.swap_modeled,
            status=status,
            result_digest=_result_digest(report_rows),
            input_tables=INPUT_TABLES,
            fatal_failure_count=fatal,
            warning_failure_count=warnings,
            unreadable_check_count=unreadable_checks,
        )
        return EvaluationReport(
            manifest=evaluation_manifest,
            status=status,
            metrics=tuple(metrics),
            categories=tuple(categories),
            trades=tuple(trades),
            fill_diagnostics=tuple(diagnostics),
            checks=tuple(checks),
        )


def _result_digest(rows: Mapping[EvaluationTable, tuple[object, ...]]) -> ContentDigest:
    """5表の全行を整列鍵の順に並べた列のダイジェスト（D07 §9.2）。

    Parquet のファイルそのものはメタデータや圧縮設定でバイト列が変わりうるため、再現性の
    判定はファイルの一致ではなくこの値の一致で行う。行は**保存する形**（D06 §9.1 の平坦化）
    に落としてから符号化するので、ダイジェストと保存された表の内容が食い違わない。
    """
    payload = {table.value: [flatten_row(row) for row in rows[table]] for table in EvaluationTable}
    return digest(payload)


# --- 読めない値の走査（D07 §10.4 の C9）----------------------------------------


def _scan_unreadable(
    reads: Mapping[TraceTable, _Rows], phases: Mapping[str, PhaseRank] | None
) -> tuple[_Unreadable, ...]:
    """読めた表の全セルを解釈し、読めない値を整列鍵の順に集める（D07 §10.4）。

    表が無い・必須列が欠けている表は C1 の担当なので走査しない（列や表が無いことと、値が
    読めないことを区別する。D07 §4.3）。
    """
    found: list[_Unreadable] = []
    for table in INPUT_TABLES:
        read = reads[table]
        if not read.usable:
            continue
        specs = COLUMN_SPECS[table]
        for row in read.records:
            key = tuple(row.get(column) or "" for column in _PRIMARY_KEYS[table])
            for spec in specs:
                raw = row.get(spec.column)
                if not _cell_is_readable(table, spec, raw, phases):
                    found.append(_Unreadable(table=table, column=spec.column, key=key, raw=raw))
            for first, second in _PAIRED_COLUMNS.get(table, ()):
                if (row.get(first) is None) != (row.get(second) is None):
                    missing = first if row.get(first) is None else second
                    found.append(_Unreadable(table=table, column=missing, key=key, raw=None))
    return tuple(sorted(found, key=lambda item: item.sort_key))


# --- 整合検査（D07 §10.2・§10.4）-----------------------------------------------


def _check_required_columns(reads: Mapping[TraceTable, _Rows]) -> ConsistencyCheckResult:
    """C1: 9表が揃い、必須列が1つも欠けていない（D07 §10.2）。**値は読まない**。"""
    missing: list[str] = []
    for table in INPUT_TABLES:
        read = reads[table].result
        if not read.table_present:
            missing.append(f"{table.value}: table absent")
            continue
        for column in read.missing_columns:
            missing.append(f"{table.value}.{column}")
    return _judged(
        CHECK_REQUIRED_COLUMNS_PRESENT,
        not missing,
        expected=canonical_text(tuple(table.value for table in INPUT_TABLES)),
        observed=canonical_text(tuple(missing)),
    )


def _check_run_id(context: _Context) -> ConsistencyCheckResult:
    """C2: 結果 DTO・run manifest・各表の `run_id` 列が一致する（D07 §10.2）。"""

    def run() -> ConsistencyCheckResult:
        expected = str(context.result.run_id)
        observed: set[str] = {str(context.run_manifest.run_id)}
        for table in INPUT_TABLES:
            for row in context.reads[table].records:
                observed.add(_text(row, "run_id"))
        return _judged(
            CHECK_RUN_ID_CONSISTENT,
            observed == {expected},
            expected=canonical_text(expected),
            observed=canonical_text(tuple(sorted(observed))),
        )

    return _guarded(
        CHECK_RUN_ID_CONSISTENT,
        context,
        tuple((table, "run_id") for table in INPUT_TABLES),
        run,
        needs_manifest=True,
    )


def _complete_closed_positions(
    reads: Mapping[TraceTable, _Rows],
) -> tuple[list[Mapping[str, str | None]], list[str]]:
    """完了取引の行を組み立てられる建玉と、組み立てられない建玉（D07 §5.2・§8.1）。

    完了した建玉は入場・決済の約定と確定損益を必ず持つ（D06 §8.2）。欠けている建玉、
    約定の行が見つからない建玉は、組み立てられないものとして名前を返す。
    """
    fills = _index(reads[TraceTable.FILLS].records, "fill_id")
    complete: list[Mapping[str, str | None]] = []
    incomplete: list[str] = []
    for row in reads[TraceTable.POSITIONS].records:
        if row.get("status") != _CLOSED:
            continue
        entry_fill_id = row.get("entry_fill_id")
        close_fill_id = row.get("close_fill_id")
        if (
            entry_fill_id is None
            or close_fill_id is None
            or row.get("realized_amount") is None
            or entry_fill_id not in fills
            or close_fill_id not in fills
        ):
            incomplete.append(f"{row.get('position_id')}: incomplete closed position row")
            continue
        complete.append(row)
    return complete, incomplete


def _check_trade_count(context: _Context) -> ConsistencyCheckResult:
    """C3: 完了取引の件数（表11）が結果 DTO と一致する（D07 §10.2）。"""

    def run() -> ConsistencyCheckResult:
        complete, _ = _complete_closed_positions(context.reads)
        return _judged(
            CHECK_TRADE_COUNT_MATCHES,
            len(complete) == context.result.trade_count,
            expected=canonical_text(context.result.trade_count),
            observed=canonical_text(len(complete)),
            table=TraceTable.POSITIONS,
        )

    return _guarded(
        CHECK_TRADE_COUNT_MATCHES, context, _TRADE_LINK_COLUMNS, run, table=TraceTable.POSITIONS
    )


def _check_id_chain(context: _Context) -> ConsistencyCheckResult:
    """C4: 約定 → 注文 → 試行の外部キーが辿れる（D07 §10.2・§8.1）。

    見るのは4つである。(a) 完了取引の取引機会まで辿れたか、(b) 完了取引の行を組み立て
    られずに落とした建玉があるか、(c) 対応する注文・試行が無い約定があるか（入場側だけで
    なく決済側も）、(d) 決済約定が入場約定より前の時刻にある建玉があるか。(b)〜(d) を載せる
    のは、**行を落としたこと・組み立てられないことが結果から読めるようにする**ためである。
    """
    reads = context.reads

    def run() -> ConsistencyCheckResult:
        complete, broken = _complete_closed_positions(reads)
        fills = _index(reads[TraceTable.FILLS].records, "fill_id")
        orders = _index(reads[TraceTable.ORDERS].records, "order_id")
        requests = _index(reads[TraceTable.ORDER_REQUESTS].records, "attempt_id")
        for row in complete:
            position_id = str(row.get("position_id"))
            entry_rows = fills[_text(row, "entry_fill_id")]
            close_rows = fills[_text(row, "close_fill_id")]
            if not all(_links_to_opportunity(fill, orders, requests) for fill in entry_rows):
                broken.append(position_id)
            entered = max(_time(fill, "processed_at_time") for fill in entry_rows)
            closed = min(_time(fill, "processed_at_time") for fill in close_rows)
            if closed < entered:
                broken.append(f"{position_id}: closed before it was opened")
        broken.extend(_unlinked_fills(reads, orders, requests))
        return _judged(
            CHECK_ID_CHAIN_COMPLETE,
            not broken,
            expected=canonical_text(()),
            observed=canonical_text(tuple(sorted(broken))),
            table=TraceTable.POSITIONS,
        )

    columns = (
        *_TRADE_LINK_COLUMNS,
        *_columns_of(TraceTable.FILLS, "order_id", "processed_at_time"),
        *_columns_of(TraceTable.ORDERS, "order_id", "attempt_id"),
        *_columns_of(TraceTable.ORDER_REQUESTS, "attempt_id", "payload_opportunity_id"),
    )
    return _guarded(CHECK_ID_CHAIN_COMPLETE, context, columns, run, table=TraceTable.POSITIONS)


def _links_to_opportunity(
    fill: Mapping[str, str | None],
    orders: Mapping[str, list[Mapping[str, str | None]]],
    requests: Mapping[str, list[Mapping[str, str | None]]],
) -> bool:
    """入場約定から取引機会まで外部キーが辿れるか（D06 §9.2 の ID 連鎖）。"""
    order_rows = orders.get(_text(fill, "order_id"), [])
    if not order_rows:
        return False
    for order in order_rows:
        request_rows = requests.get(_text(order, "attempt_id"), [])
        if not request_rows or any(
            request.get("payload_opportunity_id") is None for request in request_rows
        ):
            return False
    return True


def _unlinked_fills(
    reads: Mapping[TraceTable, _Rows],
    orders: Mapping[str, list[Mapping[str, str | None]]],
    requests: Mapping[str, list[Mapping[str, str | None]]],
) -> tuple[str, ...]:
    """約定 → 注文 → 試行の連鎖が切れている約定（D07 §10.2 の C4）。

    **入場側だけでなく決済側も見る**。注文の有無だけを見ていると、決済注文の試行が表4 から
    落ちている判断履歴でも検査が通り、指標が採用してよい数値として出てしまう。
    """
    missing: list[str] = []
    for row in reads[TraceTable.FILLS].records:
        fill_id = row.get("fill_id")
        order_id = _text(row, "order_id")
        order_rows = orders.get(order_id, [])
        if not order_rows:
            missing.append(f"{fill_id}: fill without an accepted order")
            continue
        if any(_text(order, "attempt_id") not in requests for order in order_rows):
            missing.append(f"{fill_id}: order {order_id} without an order request")
    return tuple(missing)


def _check_realized_matches_balance(context: _Context) -> ConsistencyCheckResult:
    """C5: `最後の balance − 初期残高` が末尾の確定損益と一致する（D07 §10.2）。"""

    def run() -> ConsistencyCheckResult:
        summaries = context.result.summaries
        initial = context.run_manifest.config.account.initial_balance
        snapshots = _ordered_snapshots(context.reads, context.phases)
        if summaries is None or not snapshots:
            return _judged(
                CHECK_REALIZED_MATCHES_BALANCE,
                False,
                expected=canonical_text(
                    "a completed run carries both final summaries and snapshots"
                ),
                observed=canonical_text(
                    {"summaries": summaries is not None, "snapshots": len(snapshots)}
                ),
                table=TraceTable.LEDGER_SNAPSHOTS,
            )
        last = snapshots[-1].balance
        if last.currency != initial.currency or summaries.realized.currency != initial.currency:
            # **通貨をまたぐ引き算をしない**。金額の型は通貨違いの演算を拒むので、ここで
            # 引き算に入ると例外で評価が中断する。比べられないことを不合格として記録する
            # （通貨の食い違いそのものは C8 が指す）。
            return _judged(
                CHECK_REALIZED_MATCHES_BALANCE,
                False,
                expected=canonical_text(initial.currency.code),
                observed=canonical_text(
                    tuple(
                        sorted(
                            {last.currency.code, summaries.realized.currency.code}
                            - {initial.currency.code}
                        )
                    )
                ),
                table=TraceTable.LEDGER_SNAPSHOTS,
            )
        observed = last - initial
        return _judged(
            CHECK_REALIZED_MATCHES_BALANCE,
            observed == summaries.realized,
            expected=canonical_text(summaries.realized),
            observed=canonical_text(observed),
            table=TraceTable.LEDGER_SNAPSHOTS,
        )

    columns = _columns_of(
        TraceTable.LEDGER_SNAPSHOTS,
        "at_time",
        "at_phase",
        "at_sequence",
        "balance_amount",
        "balance_currency",
        "equity_amount",
        "equity_currency",
    )
    return _guarded(
        CHECK_REALIZED_MATCHES_BALANCE,
        context,
        columns,
        run,
        needs_manifest=True,
        table=TraceTable.LEDGER_SNAPSHOTS,
    )


def _check_opportunity_count(context: _Context) -> ConsistencyCheckResult:
    """C6（警告）: 終端理由別の件数の合計が生成総数と一致する（D07 §10.2）。"""

    def run() -> ConsistencyCheckResult:
        terminated = sum(
            1
            for row in context.reads[TraceTable.OPPORTUNITY_TRANSITIONS].records
            if row.get("to_state") == _TERMINATED
        )
        return _judged(
            CHECK_OPPORTUNITY_COUNT_MATCHES,
            terminated == context.result.opportunity_count,
            expected=canonical_text(context.result.opportunity_count),
            observed=canonical_text(terminated),
            table=TraceTable.OPPORTUNITY_TRANSITIONS,
        )

    return _guarded(
        CHECK_OPPORTUNITY_COUNT_MATCHES,
        context,
        _columns_of(TraceTable.OPPORTUNITY_TRANSITIONS, "to_state"),
        run,
        table=TraceTable.OPPORTUNITY_TRANSITIONS,
    )


def _check_snapshot_order(context: _Context) -> ConsistencyCheckResult:
    """C7（警告）: 台帳 snapshot が処理点の昇順に並んでいる（D07 §10.2）。

    並んでいなければ本書の整列鍵で並べ替えて続行し、警告を残す。並べ替えは
    `_ordered_snapshots` が常に行うので、この検査は**保存されていた並び**を見る。フェーズの
    順位は run manifest から引くので、manifest が読めなければ実施できない。
    """

    def run() -> ConsistencyCheckResult:
        phases = context.phases
        points = [
            _point(row, "at", phases) for row in context.reads[TraceTable.LEDGER_SNAPSHOTS].records
        ]
        ordered = sorted(points, key=lambda point: point.sort_key)
        return _judged(
            CHECK_SNAPSHOT_ORDER_MONOTONIC,
            points == ordered,
            expected=canonical_text(tuple(str(point) for point in ordered)),
            observed=canonical_text(tuple(str(point) for point in points)),
            table=TraceTable.LEDGER_SNAPSHOTS,
        )

    return _guarded(
        CHECK_SNAPSHOT_ORDER_MONOTONIC,
        context,
        _columns_of(TraceTable.LEDGER_SNAPSHOTS, "at_time", "at_phase", "at_sequence"),
        run,
        needs_manifest=True,
        table=TraceTable.LEDGER_SNAPSHOTS,
    )


#: 通貨の列（C8 が読む。v2.0 で表9 の区分別の費用の通貨を足した。D07 §7.3）。
_CURRENCY_COLUMNS: Final[tuple[tuple[TraceTable, str], ...]] = (
    *_columns_of(TraceTable.LEDGER_SNAPSHOTS, "balance_currency", "equity_currency"),
    *_columns_of(TraceTable.POSITIONS, "realized_currency"),
    *_columns_of(TraceTable.FILLS, *(currency for _, _, currency in _FILL_COST_COLUMNS)),
)


def _check_account_currency(context: _Context) -> ConsistencyCheckResult:
    """C8: すべての `Money` 列の通貨が口座通貨と一致する（D07 §10.2・§7.1・§7.3）。

    通貨の混じった合計は意味を持たないため、丸めや読み替えで通さない。
    """

    def run() -> ConsistencyCheckResult:
        currency = context.run_manifest.config.account.currency
        observed: set[str] = set()
        for table, column in _CURRENCY_COLUMNS:
            for row in context.reads[table].records:
                value = row.get(column)
                if value is not None:
                    observed.add(value)
        summaries = context.result.summaries
        if summaries is not None:
            observed.add(summaries.realized.currency.code)
            observed.add(summaries.equity_with_mtm.currency.code)
            observed.add(summaries.hypothetical_closed.currency.code)
            for amount in summaries.cost_breakdown.values():
                observed.add(amount.currency.code)
        return _judged(
            CHECK_SINGLE_ACCOUNT_CURRENCY,
            observed <= {currency.code},
            expected=canonical_text(currency.code),
            observed=canonical_text(tuple(sorted(observed))),
        )

    return _guarded(
        CHECK_SINGLE_ACCOUNT_CURRENCY, context, _CURRENCY_COLUMNS, run, needs_manifest=True
    )


def _check_all_values_readable(context: _Context) -> ConsistencyCheckResult:
    """C9: 読む列のすべての値が解釈できる（D07 §10.4）。

    観測値は読めない値の件数と、整列鍵の順で最初の1件（`表.列[主キー] = 元の文字列`）。
    """
    found = context.unreadable
    observed: dict[str, object] = {"count": len(found)}
    if found:
        observed["first"] = found[0].describe()
    return _judged(
        CHECK_ALL_VALUES_READABLE,
        not found,
        expected=canonical_text({"count": 0}),
        observed=canonical_text(observed),
    )


def _check_calendar(context: _Context, calendar: TradingCalendar) -> ConsistencyCheckResult:
    """C10: 受け取ったカレンダーが run manifest の `calendar_ref` と一致する（D07 §10.4）。

    一致しないカレンダーで数えると、run と評価で別の取引日を使うことになる（Q8 決定）。
    """

    def run() -> ConsistencyCheckResult:
        expected = context.run_manifest.calendar_ref
        observed = calendar_ref_text(calendar)
        return _judged(
            CHECK_CALENDAR_MATCHES_RUN,
            observed == expected,
            expected=canonical_text(expected),
            observed=canonical_text(observed),
        )

    return _guarded(CHECK_CALENDAR_MATCHES_RUN, context, (), run, needs_manifest=True)


def _check_manifest_readable(
    result: BacktestResult, failure: ManifestReadFailure | None
) -> ConsistencyCheckResult:
    """C11: run manifest が読める（D07 §10.4、§10.1.1 の R1-D07-4）。"""
    expected = canonical_text(f"runs/{result.run_id}/manifest.json")
    if failure is None:
        return _judged(CHECK_RUN_MANIFEST_READABLE, True, expected=expected, observed=expected)
    return _judged(CHECK_RUN_MANIFEST_READABLE, False, expected=expected, observed=failure.detail)


def _normalized_key(column: str, value: str | None, kind: ColumnValueKind) -> str:
    """主キーの構成要素を、宣言した型に直してから比べる形にする（D06 §9.2）。

    文字列のまま比べると、同じ値の別の書き方（`2015-01-06T09:00:00Z` と
    `2015-01-06T09:00:00+00:00`、`FIL:00000001` と `FIL:000000001`、`0` と `00`）が別の鍵
    に見える。読み出したあとは型へ直して使うので、重複の判定だけ文字列で行うと、**判定は
    通るのに使う側では同じ値**になる行が残り、並びで結果が変わる（D07 §9.1 の条件1）。
    """
    text = "" if value is None else value
    id_type = _ID_TYPES.get(column)
    if id_type is not None:
        return str(id_type.parse(text))
    if kind is ColumnValueKind.TIME:
        return str(UtcTime.parse(text))
    if kind is ColumnValueKind.INT:
        return str(int(text))
    return text


def _check_input_keys_unique(context: _Context) -> ConsistencyCheckResult:
    """C12: 入力の表の主キーに重複が無い（D07 §10.4・§9.3 の R1-D07-5）。

    **行を1つでも組み立てる前に**見つけて不合格にし、どちらかの行を黙って採らない（後勝ちに
    しない）。重複した主キーを後勝ちで解決すると、同じ判断履歴でも Parquet の格納順によって
    辿り着く行が変わり、取引・集計・指標・結果のダイジェストが変わる。

    D07 §10.4 が挙げる4表（表11・表9・表7・表4）に加えて、**読む9表すべての主キー**を
    確かめる（PR 本文の仮置き）。段階2 の実装が9表すべてで重複を拒んでいたのを保つため
    である。とくに台帳 snapshot は、同じ処理点に違う残高の行が2つあると、並びによって最終
    残高も最大ドローダウンも変わる。
    """

    def run() -> ConsistencyCheckResult:
        duplicates: list[tuple[int, tuple[str, ...], str]] = []
        for position, table in enumerate(INPUT_TABLES):
            columns = _PRIMARY_KEYS[table]
            kinds = {spec.column: spec.value_kind for spec in COLUMN_SPECS[table]}
            seen: set[tuple[str, ...]] = set()
            for row in context.reads[table].records:
                key = tuple(
                    _normalized_key(column, row.get(column), kinds[column]) for column in columns
                )
                if key in seen:
                    duplicates.append((position, key, f"{table.value}[{','.join(key)}]"))
                seen.add(key)
        duplicates.sort()
        observed: dict[str, object] = {"count": len(duplicates)}
        if duplicates:
            observed["first"] = duplicates[0][2]
        return _judged(
            CHECK_INPUT_KEYS_UNIQUE,
            not duplicates,
            expected=canonical_text({"count": 0}),
            observed=canonical_text(observed),
        )

    columns = tuple((table, column) for table in INPUT_TABLES for column in _PRIMARY_KEYS[table])
    return _guarded(CHECK_INPUT_KEYS_UNIQUE, context, columns, run)


# --- 取引と診断 ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """台帳 snapshot 1件（第5.2節の #1・#5〜#8・#17 が読む3列）。"""

    at: ProcessingPoint
    balance: Money
    equity: Money


def _ordered_snapshots(
    reads: Mapping[TraceTable, _Rows], phases: Mapping[str, PhaseRank]
) -> tuple[_Snapshot, ...]:
    """処理点の昇順に並べた台帳 snapshot（D07 §9.1 の条件1）。

    Parquet の格納順に依存させない。集計の前に必ず並べ替える。
    """
    snapshots = [
        _Snapshot(
            at=_point(row, "at", phases),
            balance=Money(
                _decimal(row, "balance_amount"),
                CurrencyCode(_text(row, "balance_currency")),
            ),
            equity=Money(
                _decimal(row, "equity_amount"),
                CurrencyCode(_text(row, "equity_currency")),
            ),
        )
        for row in reads[TraceTable.LEDGER_SNAPSHOTS].records
    ]
    return tuple(sorted(snapshots, key=lambda item: item.at.sort_key))


def _fill_costs(fill: Mapping[str, str | None]) -> dict[CostKind, Money | None]:
    """約定1件の区分別の費用（D06 §9.2 の表9）。記録が無い区分は `None`（0 にしない）。"""
    costs: dict[CostKind, Money | None] = {}
    for kind, amount_column, currency_column in _FILL_COST_COLUMNS:
        amount = fill.get(amount_column)
        currency = fill.get(currency_column)
        costs[kind] = (
            None
            if amount is None or currency is None
            else Money(decimal_from_str(amount), CurrencyCode(currency))
        )
    return costs


def _build_trades(
    reads: Mapping[TraceTable, _Rows], phases: Mapping[str, PhaseRank]
) -> tuple[TradeRecord, ...]:
    """完了取引の一覧（D07 §5.2・§7.3・§8.1）。整列鍵は `(entry_at, position_id)`。

    `opportunity_id` は建玉 → 入場約定 → 注文 → 試行の外部キーを辿って埋める。入場約定と
    決済約定の区分別の費用を写し、入場費用を含む取引損益で勝敗を決める（D07 §7.3、Q10）。
    """
    fills = _by_key(reads[TraceTable.FILLS].records, "fill_id")
    orders = _by_key(reads[TraceTable.ORDERS].records, "order_id")
    requests = _by_key(reads[TraceTable.ORDER_REQUESTS].records, "attempt_id")

    built: list[TradeRecord] = []
    complete, _ = _complete_closed_positions(reads)
    for row in complete:
        entry_fill = fills[_text(row, "entry_fill_id")]
        close_fill = fills[_text(row, "close_fill_id")]
        entry_at = _point(entry_fill, "processed_at", phases)
        exit_at = _point(close_fill, "processed_at", phases)
        close_order = orders.get(_text(close_fill, "order_id"))
        cause = None if close_order is None else close_order.get("terms_cause")
        realized = Money(
            _decimal(row, "realized_amount"), CurrencyCode(_text(row, "realized_currency"))
        )
        entry_costs = _fill_costs(entry_fill)
        close_costs = _fill_costs(close_fill)
        profit = trade_profit(realized, entry_costs[CostKind.COMMISSION])
        built.append(
            TradeRecord(
                trade_seq=1,
                position_id=PositionId.parse(_text(row, "position_id")),
                opportunity_id=_opportunity_of(entry_fill, orders, requests),
                symbol=Symbol(_text(row, "symbol")),
                side=OrderSide(_text(row, "side")),
                quantity=Quantity(_decimal(row, "quantity")),
                entry_fill_id=FillId.parse(_text(row, "entry_fill_id")),
                entry_price=Price(_decimal(row, "entry_price")),
                entry_at=entry_at,
                close_fill_id=FillId.parse(_text(row, "close_fill_id")),
                exit_price=Price(_decimal(close_fill, "price")),
                exit_at=exit_at,
                close_cause=None if cause is None else CloseCause(cause),
                realized=realized,
                holding=exit_at.time - entry_at.time,
                outcome=trade_outcome(profit),
                entry_commission=entry_costs[CostKind.COMMISSION],
                entry_slippage_in_price=entry_costs[CostKind.SLIPPAGE_IN_PRICE],
                entry_spread_in_price=entry_costs[CostKind.SPREAD_IN_PRICE],
                close_commission=close_costs[CostKind.COMMISSION],
                close_slippage_in_price=close_costs[CostKind.SLIPPAGE_IN_PRICE],
                close_spread_in_price=close_costs[CostKind.SPREAD_IN_PRICE],
                trade_profit=profit,
            )
        )
    built.sort(key=lambda trade: (trade.entry_at.sort_key, trade.position_id.seq))
    return tuple(
        replace(trade, trade_seq=position) for position, trade in enumerate(built, start=1)
    )


def _opportunity_of(
    fill: Mapping[str, str | None],
    orders: Mapping[str, Mapping[str, str | None]],
    requests: Mapping[str, Mapping[str, str | None]],
) -> OpportunityId | None:
    """入場約定から取引機会まで外部キーを辿る（D06 §9.2 の ID 連鎖）。

    C4 が合格したあとで呼ぶので辿り着けるが、型は段階2 の宣言（`OpportunityId | None`）の
    ままにする。
    """
    order = orders.get(_text(fill, "order_id"))
    if order is None:
        return None
    request = requests.get(_text(order, "attempt_id"))
    if request is None:
        return None
    value = request.get("payload_opportunity_id")
    return None if value is None else OpportunityId.parse(value)


def _build_fill_diagnostics(reads: Mapping[TraceTable, _Rows]) -> tuple[FillDiagnostic, ...]:
    """約定1件につき1行の診断（D07 §6.2）。整列鍵は `fill_id`。"""
    orders = _by_key(reads[TraceTable.ORDERS].records, "order_id")
    built: list[FillDiagnostic] = []
    for row in reads[TraceTable.FILLS].records:
        order_id = _text(row, "order_id")
        order = orders[order_id]
        filled_at = _time(row, "processed_at_time")
        reference = _optional_decimal(order, "terms_reference_quote_price")
        observed_at = _optional_time(order, "terms_reference_quote_observed_at")
        side = OrderSide(_text(order, "side"))
        offset = (
            None
            if reference is None
            else PriceOffset(_direction(side) * (_decimal(row, "price") - reference))
        )
        position_id = row.get("position_id")
        built.append(
            FillDiagnostic(
                fill_id=FillId.parse(_text(row, "fill_id")),
                order_id=OrderId.parse(order_id),
                position_id=None if position_id is None else PositionId.parse(position_id),
                adverse_fill_offset=offset,
                reference_to_fill=None if observed_at is None else filled_at - observed_at,
                acceptance_to_fill=filled_at - _time(order, "accepted_at_time"),
                reference_observed_at=observed_at,
                accepted_at=_time(order, "accepted_at_time"),
                filled_at=filled_at,
            )
        )
    built.sort(key=lambda item: item.fill_id.seq)
    return tuple(built)


# --- 集計（D07 §6.1・§6.3）----------------------------------------------------


def _build_categories(reads: Mapping[TraceTable, _Rows]) -> tuple[CategoryCount, ...]:
    """8種の集計（D07 §6.1・§6.3）。**語彙が有限の集計は0件の鍵も行として出す**。

    出さないと「一度も起きなかった」と「集計していない」を後から区別できない。
    """
    counts: dict[CategoryKind, dict[str, int]] = {
        kind: dict.fromkeys(CATEGORY_KEYS[kind], 0) for kind in CategoryKind
    }

    for row in reads[TraceTable.OPPORTUNITY_TRANSITIONS].records:
        if row.get("to_state") != _TERMINATED:
            continue
        _tally(counts, CategoryKind.OPPORTUNITY_TERMINAL_REASON, row.get("reason_code"))

    requests = _by_key(reads[TraceTable.ORDER_REQUESTS].records, "attempt_id")
    for row in reads[TraceTable.ATTEMPT_DECISIONS].records:
        if row.get("kind") != _REJECTED:
            continue
        attempt_id = row.get("attempt_id")
        request = None if attempt_id is None else requests.get(attempt_id)
        payload = None if request is None else request.get("payload_kind")
        if payload == _ENTRY_REQUEST:
            _tally(counts, CategoryKind.ENTRY_REJECTION_REASON, row.get("reason_code"))
        elif payload == _CLOSE_REQUEST:
            _tally(counts, CategoryKind.CLOSE_REJECTION_REASON, row.get("reason_code"))

    for row in reads[TraceTable.EVALUATIONS].records:
        outcome = row.get("outcome_kind")
        _tally(counts, CategoryKind.EVALUATION_OUTCOME, outcome)
        if outcome != _SKIPPED:
            continue
        for reason in _missing_input_reasons(row.get("outcome_diagnoses")):
            _tally(counts, CategoryKind.MISSING_INPUT_REASON, reason)

    for outcome in _final_outcomes_by_request(reads[TraceTable.EVALUATIONS].records):
        _tally(counts, CategoryKind.EVALUATION_REQUEST_FINAL_OUTCOME, outcome)

    for row in reads[TraceTable.ORDERS].records:
        if row.get("terms_kind") != _CLOSE_TERMS:
            continue
        _tally(counts, CategoryKind.CLOSE_CAUSE, row.get("terms_cause"))

    for row in reads[TraceTable.INTRABAR_RESOLUTIONS].records:
        _tally(counts, CategoryKind.INTRABAR_METHOD, row.get("method"))

    return tuple(
        CategoryCount(category=kind, key=key, count=counts[kind][key])
        for kind in CategoryKind
        for key in _category_order(kind, counts[kind])
    )


def _final_outcomes_by_request(rows: Sequence[Mapping[str, str | None]]) -> tuple[str, ...]:
    """評価要求ごとの**最後の記録**の結果区分（D07 §6.3、Q13 決定）。

    最後の記録は `decision_time` の昇順、同じ時刻なら `evaluation_id` の昇順で最後のもの。
    待機をはさんだ要求は記録が2件出るが、要求単位では1件と数える。識別子は宣言した型に直して
    から比べる（書き方の揺れで別の要求に見えないように）。
    """
    finals: dict[str, tuple[tuple[datetime, int], str]] = {}
    for row in rows:
        request = str(RequestId.parse(_text(row, "request_id")))
        order = (
            _time(row, "decision_time").value,
            EvaluationId.parse(_text(row, "evaluation_id")).seq,
        )
        outcome = _text(row, "outcome_kind")
        current = finals.get(request)
        if current is None or order > current[0]:
            finals[request] = (order, outcome)
    return tuple(outcome for _, outcome in finals.values())


def _category_order(kind: CategoryKind, counted: Mapping[str, int]) -> tuple[str, ...]:
    """集計の鍵を出す順（D07 §6.1・§8.1）。

    語彙の鍵は**宣言順**、語彙に無い鍵はそのあとに**符号順**で並べる。語彙に無い鍵を
    見つけた順に出すと、判断履歴の行の並びが変わるだけで結果のダイジェストが変わり、
    行の並びに依存しないという決定論の条件（D07 §9.1 の条件1）が崩れる。
    """
    declared = CATEGORY_KEYS[kind]
    extra = sorted(key for key in counted if key not in set(declared))
    return (*declared, *extra)


def _tally(counts: dict[CategoryKind, dict[str, int]], kind: CategoryKind, key: str | None) -> None:
    """鍵1件を数える。語彙に無い鍵は**捨てずに足す**（D07 §6.1 の「語を足さない」は
    こちらが語彙を発明しないという意味であり、判断履歴が実際に書いた語を落としてよい
    という意味ではない。落とすと件数の合計が生成総数と合わなくなり、なぜ合わないかも
    結果から読めなくなる）。
    """
    if key is None:
        return
    counts[kind][key] = counts[kind].get(key, 0) + 1


def _missing_input_reasons(encoded: str | None) -> tuple[str, ...]:
    """評価見送りの診断から理由コードを取り出す（D07 §6.1）。

    `outcome_diagnoses` は D02 §9.3 の正規化エンコード文字列の列である（D06 §9.1）。
    正規化エンコードは JSON 互換のテキストなので、`reason` の項目をそのまま読める。
    読めない文字列は `ValueError`（`json.JSONDecodeError`）を送出し、C9 の読めない値になる。
    """
    if encoded is None:
        return ()
    items = json.loads(encoded) if encoded.startswith("[") else [encoded]
    reasons: list[str] = []
    for item in items:
        payload = json.loads(item) if isinstance(item, str) else item
        reason = payload.get("reason") if isinstance(payload, dict) else None
        if isinstance(reason, str):
            reasons.append(reason)
    return tuple(reasons)


# --- 指標（D07 §5.2・§5.5）----------------------------------------------------


def _record(
    metric_id: MetricId,
    value: MetricValue,
    *,
    observations: int,
    unresolved_intrabar: bool,
) -> MetricRecord:
    return MetricRecord(
        metric_id=metric_id,
        value=value,
        caveats=metric_caveats(metric_id, unresolved_intrabar=unresolved_intrabar),
        observation_count=observations,
        inputs=METRIC_INPUTS[metric_id],
    )


def _build_metrics(
    result: BacktestResult,
    manifest: RunManifest,
    reads: Mapping[TraceTable, _Rows],
    phases: Mapping[str, PhaseRank],
    trades: Sequence[TradeRecord],
    calendar: TradingCalendar,
) -> tuple[MetricRecord, ...]:
    """指標集合 v2 の19件（D07 §5.2・§5.5）。並びは `MetricId` の宣言順（D07 §8.1）。"""
    unresolved = result.unresolved_intrabar_count > 0
    snapshots = _ordered_snapshots(reads, phases)
    initial = manifest.config.account.initial_balance
    currency = manifest.config.account.currency
    summaries = result.summaries
    profits = [trade.trade_profit for trade in trades]

    def make(metric_id: MetricId, value: MetricValue, observations: int) -> MetricRecord:
        return _record(metric_id, value, observations=observations, unresolved_intrabar=unresolved)

    records: list[MetricRecord] = []

    # #1 純損益。
    net_profit: Money | None = None if not snapshots else snapshots[-1].balance - initial
    records.append(
        make(
            MetricId.NET_PROFIT,
            AmountValue(net_profit)
            if net_profit is not None
            else Unavailable(MetricKind.AMOUNT, MetricUnavailableReason.NO_OBSERVATIONS),
            len(snapshots),
        )
    )

    # #2 完了取引の取引損益（入場費用込み）の合計（D07 §7.3）。
    if profits:
        total = profits[0]
        for profit in profits[1:]:
            total = total + profit
        closed_profit: MetricValue = AmountValue(total)
    else:
        closed_profit = Unavailable(MetricKind.AMOUNT, MetricUnavailableReason.NO_TRADES)
    records.append(make(MetricId.CLOSED_TRADE_PROFIT, closed_profit, len(trades)))

    # #3 完了取引の件数（0 は正しい値であり、値なしにしない）。
    records.append(make(MetricId.TRADE_COUNT, CountValue(len(trades)), len(trades)))

    # #4 勝率（勝敗は入場費用込みの取引損益の符号。Q10 決定）。
    if trades:
        wins = sum(1 for trade in trades if trade.outcome is TradeOutcome.WIN)
        win_rate: MetricValue = RatioValue(
            ratio_of(decimal_from_int(wins), decimal_from_int(len(trades)))
        )
    else:
        win_rate = Unavailable(MetricKind.RATIO, MetricUnavailableReason.NO_TRADES)
    records.append(make(MetricId.WIN_RATE, win_rate, len(trades)))

    # #5〜#8 最大ドローダウン（含み損益込みが採用指標、確定損益が参考値）。
    for amount_id, rate_id, column in (
        (MetricId.MAX_DRAWDOWN_MTM, MetricId.MAX_DRAWDOWN_MTM_RATE, "equity"),
        (MetricId.MAX_DRAWDOWN_BALANCE, MetricId.MAX_DRAWDOWN_BALANCE_RATE, "balance"),
    ):
        series = [
            (snapshot.equity if column == "equity" else snapshot.balance).amount
            for snapshot in snapshots
        ]
        fall = max_drawdown(series)
        if fall is None:
            records.append(
                make(
                    amount_id,
                    Unavailable(MetricKind.AMOUNT, MetricUnavailableReason.NO_OBSERVATIONS),
                    0,
                )
            )
            records.append(
                make(
                    rate_id,
                    Unavailable(MetricKind.RATIO, MetricUnavailableReason.NO_OBSERVATIONS),
                    0,
                )
            )
            continue
        records.append(make(amount_id, AmountValue(Money(fall.amount, currency)), len(series)))
        rate: MetricValue = (
            Unavailable(MetricKind.RATIO, MetricUnavailableReason.UNDEFINED_DENOMINATOR)
            if fall.peak == 0
            else RatioValue(ratio_of(fall.amount, fall.peak))
        )
        records.append(make(rate_id, rate, len(series)))

    # #9 建玉を保有していた時間の割合。
    records.append(make(MetricId.EXPOSURE_RATE, *_exposure(manifest, trades)))

    # #10・#11 費用の集計（末尾の集計から取る）。
    records.append(
        make(
            MetricId.COST_CHARGED_TOTAL,
            _cost(summaries, currency, (CostKind.COMMISSION,)),
            0 if summaries is None else 1,
        )
    )
    records.append(
        make(
            MetricId.COST_PRICE_EMBEDDED_TOTAL,
            _cost(
                summaries,
                currency,
                (CostKind.SLIPPAGE_IN_PRICE, CostKind.SPREAD_IN_PRICE),
            ),
            0 if summaries is None else 1,
        )
    )

    # #12 エントリー約定の不利約定幅の最大値。
    offsets = [
        diagnostic.adverse_fill_offset
        for diagnostic in _build_fill_diagnostics(reads)
        if diagnostic.adverse_fill_offset is not None
    ]
    if offsets:
        zero = PriceOffset(decimal_from_int(0))
        worst = max(offsets, key=lambda item: item.value)
        adverse: MetricValue = PriceOffsetValue(worst if worst > zero else zero)
    else:
        adverse = Unavailable(MetricKind.PRICE_OFFSET, MetricUnavailableReason.NO_OBSERVATIONS)
    records.append(make(MetricId.MAX_ADVERSE_FILL_OFFSET, adverse, len(offsets)))

    # #13・#14 末尾の参考値。
    records.append(
        make(
            MetricId.END_EQUITY_MTM,
            AmountValue(summaries.equity_with_mtm)
            if summaries is not None
            else Unavailable(MetricKind.AMOUNT, MetricUnavailableReason.INPUT_NOT_AVAILABLE),
            0 if summaries is None else 1,
        )
    )
    records.append(
        make(
            MetricId.HYPOTHETICAL_CLOSED_PROFIT,
            AmountValue(summaries.hypothetical_closed)
            if summaries is not None
            else Unavailable(MetricKind.AMOUNT, MetricUnavailableReason.INPUT_NOT_AVAILABLE),
            0 if summaries is None else 1,
        )
    )

    # #15 単純収益率。
    if net_profit is None:
        net_return: MetricValue = Unavailable(
            MetricKind.RATIO, MetricUnavailableReason.NO_OBSERVATIONS
        )
    elif initial.amount == 0:
        net_return = Unavailable(MetricKind.RATIO, MetricUnavailableReason.UNDEFINED_DENOMINATOR)
    else:
        net_return = RatioValue(ratio_of(net_profit.amount, initial.amount))
    records.append(make(MetricId.NET_RETURN_RATE, net_return, len(snapshots)))

    # #16・#17 取引日で数える年率化（D07 §5.5、Q8 決定）。
    day_ends = trading_day_ends(calendar, manifest.config.run_interval)
    records.append(make(MetricId.ANNUALIZED_RETURN, *_annualized_return(net_return, day_ends)))
    records.append(
        make(
            MetricId.ANNUALIZED_SHARPE_RATIO,
            *_annualized_sharpe(initial.amount, snapshots, day_ends),
        )
    )

    # #18 プロフィットファクター。
    factor = profit_factor(profits)
    records.append(
        make(
            MetricId.PROFIT_FACTOR,
            Unavailable(MetricKind.RATIO, factor)
            if isinstance(factor, MetricUnavailableReason)
            else RatioValue(factor),
            len(trades),
        )
    )

    # #19 平均取引損益。
    average = average_trade_profit(profits)
    records.append(
        make(
            MetricId.AVERAGE_TRADE_PROFIT,
            Unavailable(MetricKind.AMOUNT, MetricUnavailableReason.NO_TRADES)
            if average is None
            else AmountValue(average),
            len(trades),
        )
    )

    order = {metric_id: position for position, metric_id in enumerate(MetricId)}
    records.sort(key=lambda record: order[record.metric_id])
    return tuple(records)


def _annualized_return(
    net_return: MetricValue, day_ends: tuple[UtcTime, ...] | None
) -> tuple[MetricValue, int]:
    """#16 `(#15 × 260) ÷ N`（D07 §5.5）。#15 が値なしなら同じ理由で値なしにする。"""
    if day_ends is None:
        # 取引日の境界が1つに決まらないカレンダー（PR 本文の仮置き）。
        return Unavailable(MetricKind.RATIO, MetricUnavailableReason.INPUT_NOT_AVAILABLE), 0
    days = len(day_ends)
    if isinstance(net_return, Unavailable):
        return Unavailable(MetricKind.RATIO, net_return.reason), days
    if not isinstance(net_return, RatioValue):
        raise KernelValueError("#15 NET_RETURN_RATE is a ratio or unavailable (D07 §5.2)")
    value = annualized_return(net_return.ratio, days)
    if value is None:
        return (
            Unavailable(MetricKind.RATIO, MetricUnavailableReason.UNDEFINED_DENOMINATOR),
            days,
        )
    return RatioValue(value), days


def _annualized_sharpe(
    initial: Decimal,
    snapshots: Sequence[_Snapshot],
    day_ends: tuple[UtcTime, ...] | None,
) -> tuple[MetricValue, int]:
    """#17 年率化シャープレシオ（D07 §5.5）。

    日次の資産 `E_0 = 初期残高`、`E_k` = k 番目の取引日の終わり**以前で最後**の台帳 snapshot の
    `equity`（無ければ `E_{k−1}` を持ち越す）。基準列は採用指標の最大ドローダウン（#5）と
    同じ含み損益込み（`equity`）である。
    """
    if day_ends is None:
        return Unavailable(MetricKind.RATIO, MetricUnavailableReason.INPUT_NOT_AVAILABLE), 0
    equities = [initial]
    position = 0
    current = initial
    for end in day_ends:
        while position < len(snapshots) and snapshots[position].at.time <= end:
            current = snapshots[position].equity.amount
            position += 1
        equities.append(current)
    value = annualized_sharpe_ratio(equities)
    if isinstance(value, MetricUnavailableReason):
        return Unavailable(MetricKind.RATIO, value), len(day_ends)
    return RatioValue(value), len(day_ends)


def _cost(
    summaries: FinalSummaries | None, currency: CurrencyCode, kinds: Sequence[CostKind]
) -> MetricValue:
    """費用区分の合計（D07 §5.2 の #10・#11）。

    末尾の集計が無い（run が正常完走していない）場合は値なしにする。区分そのものが
    費用の記録に現れなかった場合は 0 円であり、これは観測された事実なので値なしにしない。
    """
    if summaries is None:
        return Unavailable(MetricKind.AMOUNT, MetricUnavailableReason.INPUT_NOT_AVAILABLE)
    total = Money(decimal_from_int(0), currency)
    for kind in kinds:
        amount = summaries.cost_breakdown.get(kind)
        if amount is not None:
            total = total + amount
    return AmountValue(total)


def _exposure(
    manifest: RunManifest,
    trades: Sequence[TradeRecord],
) -> tuple[MetricValue, int]:
    """建玉を保有していた時間の割合（D07 §5.2 の #9、2026-09-22 の人間の決定）。

    **完了取引（`status=CLOSED`）だけを数える**。保有時間は入場約定の処理時刻から決済約定の
    処理時刻までで、未決済建玉は含めない。建玉が1件も無ければ保有時間0は観測された事実
    なので `RatioValue(0)` とし、値なしにしない。
    """
    run_interval = manifest.config.run_interval
    held = decimal_from_int(0)
    observations = 0
    for trade in trades:
        held = held + _microseconds(trade.holding)
        observations += 1
    if observations == 0:
        return RatioValue(decimal_from_int(0)), 0
    total = _microseconds(run_interval.duration)
    if total == 0:
        return (
            Unavailable(MetricKind.RATIO, MetricUnavailableReason.UNDEFINED_DENOMINATOR),
            observations,
        )
    return RatioValue(ratio_of(held, total)), observations
