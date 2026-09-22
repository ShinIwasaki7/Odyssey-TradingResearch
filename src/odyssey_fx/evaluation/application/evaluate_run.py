"""判断履歴から単一 run の数値の結果を作るユースケース（D07 §4〜§10）。

入力は3つだけである（D07 §4.1、Q1 決定）。

1. `BacktestResult`（引数）
2. `RunManifest`（`ResultRepository.read_manifest`）
3. 判断履歴の9表（`ResultRepository.read_table`）

**評価は run を実行し直さない。判断履歴を書き換えない。生の市場データを読み直さない。**
段階2の指標15件はすべて判断履歴と manifest から作れるので、市場データを読む経路を作ると
as-of の規則とアクセス分類の許可が評価側にも割れる（D07 §4.1）。

**D07 §4.2 の読む列に3列だけ足している**。`POSITIONS.opened_at_phase` /
`opened_at_sequence` と `FILLS.processed_at_phase` / `processed_at_sequence` である。
D07 §3 の `TradeRecord.entry_at` / `exit_at` は `ProcessingPoint` 型であり、処理点は
`(時刻, フェーズ, 通し番号)` の3つで1つなので、時刻の列だけでは宣言された型を組み立て
られない（`TRADES` 表の整列鍵 `(entry_at, position_id)` もこの3つで決まる）。フェーズの
順位は run manifest が記録しているフェーズ集合から引く（D06 §9.3）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Final

from odyssey_fx.backtest.domain.fills import CostKind
from odyssey_fx.backtest.domain.orders import CloseCause, OrderSide
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
    EvaluationManifest,
    EvaluationTable,
    run_evaluation_id,
)
from odyssey_fx.evaluation.application.ports import (
    ColumnValueKind,
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
    max_drawdown,
    metric_caveats,
    ratio_of,
    trade_outcome,
)
from odyssey_fx.evaluation.domain.status import (
    CHECK_ID_CHAIN_COMPLETE,
    CHECK_LEVELS,
    CHECK_OPPORTUNITY_COUNT_MATCHES,
    CHECK_ORDER,
    CHECK_REALIZED_MATCHES_BALANCE,
    CHECK_REQUIRED_COLUMNS_PRESENT,
    CHECK_RUN_ID_CONSISTENT,
    CHECK_SINGLE_ACCOUNT_CURRENCY,
    CHECK_SNAPSHOT_ORDER_MONOTONIC,
    CHECK_TRADE_COUNT_MATCHES,
    CheckLevel,
    ConsistencyCheckResult,
    EvaluationStatus,
)

__all__ = [
    "COLUMN_SPECS",
    "INPUT_TABLES",
    "EvaluateRun",
    "EvaluationReport",
]

_STRING = ColumnValueKind.STRING
_DECIMAL = ColumnValueKind.DECIMAL
_INT = ColumnValueKind.INT
_TIME = ColumnValueKind.TIME
_ENUM = ColumnValueKind.ENUM
_LIST = ColumnValueKind.LIST_STRING

#: 段階2で読む9表（D07 §4.2）。**読まない6表**（表1・6・8・10・12・15）は開かない。
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

#: 表ごとに読む列（D07 §4.2）。**9表すべてで `run_id` を先頭列として要求する**。
#: 表ごとに書くと9回同じ列名が並び、1か所で落としても気付けない。
_COLUMNS: Final[dict[TraceTable, tuple[tuple[str, ColumnValueKind], ...]]] = {
    TraceTable.EVALUATIONS: (
        ("evaluation_id", _STRING),
        ("outcome_kind", _ENUM),
        ("outcome_diagnoses", _LIST),
        ("outcome_reason_code", _ENUM),
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
_REJECTED: Final = "REJECTED"
#: 建玉の状態（D06 §8.2 の `PositionStatus`）と取引機会の終端状態（D05 §7.1）。
_CLOSED: Final = "CLOSED"
_TERMINATED: Final = "TERMINATED"
#: 評価の結果区分のうち、見送り（D05 §6.4 の `Skipped`）。
_SKIPPED: Final = "SKIPPED"


def _fatal_failures(checks: Sequence[ConsistencyCheckResult]) -> int:
    """致命の水準で不合格になった検査の件数（D07 §10.1）。"""
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


# --- 読み出した行の取り回し ---------------------------------------------------


class _Rows:
    """読み出した1表を、列名で引ける行の列として持つ。

    表そのものが無い・必須列が欠けている場合は空の列になる。呼び出し側は整合検査 C1 の
    結果を見てから値を使う。
    """

    __slots__ = ("_names", "_rows", "result")

    def __init__(self, result: TableReadResult, specs: Sequence[TraceColumnSpec]) -> None:
        self.result = result
        self._names = tuple(spec.column for spec in specs)
        index = {name: position for position, name in enumerate(self._names)}
        self._rows = tuple(
            {name: row[position] for name, position in index.items()} for row in result.rows
        )

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
    name = _text(row, f"{prefix}_phase")
    phase = phases.get(name)
    if phase is None:
        raise KernelValueError(
            f"the run manifest declares no phase named {name!r}; the trace and the manifest"
            " disagree about the phase set (D06 §9.3)"
        )
    return ProcessingPoint(
        time=_time(row, f"{prefix}_time"),
        phase=phase,
        sequence=int(_text(row, f"{prefix}_sequence")),
    )


def _microseconds(duration: timedelta) -> Decimal:
    """期間をマイクロ秒の十進数にする（浮動小数を経由しない、ADR-0012）。"""
    return decimal_from_int(duration // timedelta(microseconds=1))


def _direction(side: OrderSide) -> Decimal:
    """方向 `d`（買いなら `+1`、売りなら `-1`）。"""
    return decimal_from_int(1 if side is OrderSide.BUY else -1)


# --- ユースケース -------------------------------------------------------------


class EvaluateRun:
    """`evaluate(result, repository, metric_set_version) -> EvaluationReport`（D07 §3）。

    **形の差異**: D07 §3 の型表は `EvaluateRun` を `Protocol` としているが、評価を差し替える
    側は居らず（`app` が唯一の合成点）、実装は1つである。そこで D06 の `RunBacktest` と
    同じく具体クラスとして置く。操作の名前と引数は D07 の宣言どおりである。

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
    ) -> EvaluationReport:
        """判断履歴を読み、指標・集計・診断・整合検査・状態を作る（D07 §4〜§10）。"""
        if not isinstance(result, BacktestResult):
            raise KernelValueError("EvaluateRun.evaluate requires a BacktestResult")
        manifest = repository.read_manifest(result.run_id)
        if not isinstance(manifest, RunManifest):
            raise KernelValueError("ResultRepository.read_manifest must return a RunManifest")

        reads = {
            table: _Rows(
                repository.read_table(result.run_id, table, COLUMN_SPECS[table]),
                COLUMN_SPECS[table],
            )
            for table in INPUT_TABLES
        }
        phases = {phase.name: phase for phase in manifest.phases.ordered()}
        currency = manifest.config.account.currency

        checks: list[ConsistencyCheckResult] = []
        readable = _check_required_columns(reads)
        checks.append(readable)
        checks.append(_check_run_id(result, manifest, reads))

        trades: tuple[TradeRecord, ...] = ()
        diagnostics: tuple[FillDiagnostic, ...] = ()
        categories: tuple[CategoryCount, ...] = ()
        metrics: tuple[MetricRecord, ...] = ()

        if readable.passed:
            try:
                _require_unique_primary_keys(reads)
                trades = _build_trades(reads, phases)
                diagnostics = _build_fill_diagnostics(reads, phases)
                checks.append(_check_trade_count(result, trades))
                checks.append(_check_id_chain(reads, trades))
                if result.status is RunStatus.COMPLETED:
                    # 末尾の集計は正常完走した run だけが持つ（D06 §9.4）。存在しない値
                    # との比較を「不合格」として記録すると、完走しなかった run を「不整合な
                    # run」として説明することになるので、この検査は行わない
                    # （D07 §10.1 の REJECTED）。
                    checks.append(_check_realized_matches_balance(result, manifest, reads))
                checks.append(_check_opportunity_count(result, reads))
                checks.append(_check_snapshot_order(reads, phases))
                checks.append(_check_account_currency(result, currency, reads))
                if result.status is RunStatus.COMPLETED and not _fatal_failures(checks):
                    # 集計と指標も**同じ範囲の中で**組み立てる。集計は評価見送りの診断
                    # （正規化エンコード文字列の列）を読むので、そこが壊れていれば例外に
                    # なりうる。範囲の外で組み立てると、検査がすべて合格したあとで例外に
                    # なり、失敗を説明する成果物が残らない。
                    categories = _build_categories(reads)
                    metrics = _build_metrics(result, manifest, reads, phases, trades, currency)
            except (KernelValueError, ValueError) as exc:
                # **列の値が読めないことで評価を中断しない**（D07 §4.3 の趣旨）。列は
                # 揃っていても、常に埋まるはずの値が空・語彙に無い列挙・十進数として
                # 読めない文字列は起こりうる。例外のまま外へ出すと、失敗を説明する検査の
                # 表も評価 manifest も残らず、「なぜ評価できなかったか」が成果物から
                # 消える。読めなかったことを C1 の不合格として記録し、行を見る残りの検査は
                # 行わない（読めない値の上に積んだ比較は意味を持たない）。
                #
                # **設計文書との差異**: D07 §10.2 の C1 は「9表があり、必須列が欠けて
                # いない」だけを見るとしている。値が読めないことも同じ検査へ寄せたのは、
                # 8件の検査を増やさずに「読み出しの失敗は必ず結果に残る」を満たすためで
                # ある（PR 本文の仮置き事項）。
                readable = _result_of(
                    CHECK_REQUIRED_COLUMNS_PRESENT,
                    passed=False,
                    expected=canonical_text(
                        "every declared column holds a value the design says is always present"
                    ),
                    observed=canonical_text(f"{type(exc).__name__}: {exc}"),
                )
                checks = [readable, *(item for item in checks[1:] if item.check != readable.check)]
                trades = ()
                diagnostics = ()
                categories = ()
                metrics = ()

        checks.sort(key=lambda item: CHECK_ORDER.index(item.check))
        fatal = _fatal_failures(checks)
        warnings = sum(1 for item in checks if not item.passed and item.level is CheckLevel.WARNING)

        if result.status is not RunStatus.COMPLETED:
            # 正常完走していない run の指標は作らない（D07 §10.1、Q6 決定）。失敗した run の
            # 値が正常完走の値と並べられる経路そのものを作らない。
            status = EvaluationStatus.REJECTED
        elif fatal:
            status = EvaluationStatus.FAILED
        else:
            status = EvaluationStatus.COMPLETED

        if status is not EvaluationStatus.COMPLETED:
            # 指標を出さない状態では4表を0行で書く（D07 §8.1・§10.1）。算出できた値も
            # 出さない（採用してよい数値と不整合な判断履歴から出た数値を混ぜない）。
            trades = ()
            diagnostics = ()
            categories = ()
            metrics = ()

        report_rows = {
            EvaluationTable.METRICS: tuple(metrics),
            EvaluationTable.CATEGORY_COUNTS: tuple(categories),
            EvaluationTable.TRADES: tuple(trades),
            EvaluationTable.FILL_DIAGNOSTICS: tuple(diagnostics),
            EvaluationTable.CONSISTENCY_CHECKS: tuple(checks),
        }
        evaluation_manifest = EvaluationManifest(
            run_evaluation_id=run_evaluation_id(
                result.run_id, metric_set_version, self._code_digest
            ),
            run_id=result.run_id,
            # run manifest を**内容で**指す参照（D07 §8.3 の識別の群）。manifest の
            # 入力とポリシーの群のダイジェスト（`ConfigDigest`、D06 §9.3）を使う。保存した
            # JSON のバイト列のダイジェストにはしない。保存形式を変えると参照が変わり、
            # 同じ実行を指せなくなるためである。D07 §8.3 はどちらとも書いていないので、
            # 恒久的な形は人間の決定を要する（仮置き）。
            run_manifest_ref=manifest.config_digest.digest,
            metric_set_version=metric_set_version,
            evaluation_code_digest=self._code_digest,
            run_code_digest=manifest.code_digest,
            run_status=result.status,
            run_failure_reason=manifest.reason,
            account_currency=currency,
            swap_modeled=result.swap_modeled,
            status=status,
            result_digest=_result_digest(report_rows),
            input_tables=INPUT_TABLES,
            fatal_failure_count=fatal,
            warning_failure_count=warnings,
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


# --- 整合検査（D07 §10.2）-----------------------------------------------------


def _result_of(
    check: str,
    *,
    passed: bool,
    expected: str,
    observed: str,
    table: TraceTable | None = None,
) -> ConsistencyCheckResult:
    return ConsistencyCheckResult(
        check=check,
        level=CHECK_LEVELS[check],
        passed=passed,
        table=table,
        expected=expected,
        observed=observed,
    )


def _check_required_columns(reads: Mapping[TraceTable, _Rows]) -> ConsistencyCheckResult:
    """C1: 9表が揃い、必須列が1つも欠けていない（D07 §10.2）。"""
    missing: list[str] = []
    for table in INPUT_TABLES:
        read = reads[table].result
        if not read.table_present:
            missing.append(f"{table.value}: table absent")
            continue
        for column in read.missing_columns:
            missing.append(f"{table.value}.{column}")
    return _result_of(
        CHECK_REQUIRED_COLUMNS_PRESENT,
        passed=not missing,
        expected=canonical_text(tuple(table.value for table in INPUT_TABLES)),
        observed=canonical_text(tuple(missing)),
    )


def _check_run_id(
    result: BacktestResult, manifest: RunManifest, reads: Mapping[TraceTable, _Rows]
) -> ConsistencyCheckResult:
    """C2: 結果 DTO・run manifest・各表の `run_id` 列が一致する（D07 §10.2）。"""
    expected = str(result.run_id)
    observed: set[str] = {str(manifest.run_id)}
    for table in INPUT_TABLES:
        for row in reads[table].records:
            value = row.get("run_id")
            # **空の `run_id` を読み飛ばさない**。飛ばすと、実行に紐付いていない行が
            # 観測値から消えて検査が通り、その行が集計と指標へそのまま入る。全行が
            # `run_id` を持つことは D06 §9.1 が確定しているので、空は不整合である。
            observed.add(f"{table.value}: <missing run_id>" if value is None else value)
    return _result_of(
        CHECK_RUN_ID_CONSISTENT,
        passed=observed == {expected},
        expected=canonical_text(expected),
        observed=canonical_text(tuple(sorted(observed))),
    )


def _check_trade_count(
    result: BacktestResult, trades: Sequence[TradeRecord]
) -> ConsistencyCheckResult:
    """C3: 完了取引の件数が結果 DTO と一致する（D07 §10.2）。"""
    return _result_of(
        CHECK_TRADE_COUNT_MATCHES,
        passed=len(trades) == result.trade_count,
        expected=canonical_text(result.trade_count),
        observed=canonical_text(len(trades)),
        table=TraceTable.POSITIONS,
    )


def _check_id_chain(
    reads: Mapping[TraceTable, _Rows], trades: Sequence[TradeRecord]
) -> ConsistencyCheckResult:
    """C4: 約定 → 注文 → 試行の外部キーが辿れる（D07 §10.2・§8.1）。

    見るのは3つである。(a) 完了取引の取引機会まで辿れたか、(b) 完了取引の行を組み立て
    られずに落とした建玉があるか、(c) 対応する注文が無いために診断を作れなかった約定が
    あるか。(b) と (c) を載せるのは、**行を落としたことが結果から読めるようにする**ため
    である。落としたまま黙っていると、表の行数が少ないことの理由が成果物から消える。
    """
    broken: list[str] = [str(trade.position_id) for trade in trades if trade.opportunity_id is None]
    broken.extend(_unlinked_positions(reads, trades))
    broken.extend(_unlinked_fills(reads))
    return _result_of(
        CHECK_ID_CHAIN_COMPLETE,
        passed=not broken,
        expected=canonical_text(()),
        observed=canonical_text(tuple(sorted(broken))),
        table=TraceTable.POSITIONS,
    )


def _unlinked_positions(
    reads: Mapping[TraceTable, _Rows], trades: Sequence[TradeRecord]
) -> tuple[str, ...]:
    """完了取引の行を組み立てられなかった建玉（入場・決済の約定か確定損益が欠けている）。"""
    built = {str(trade.position_id) for trade in trades}
    missing: list[str] = []
    for row in reads[TraceTable.POSITIONS].records:
        if row.get("status") != _CLOSED:
            continue
        position_id = row.get("position_id")
        if position_id is not None and position_id not in built:
            missing.append(f"{position_id}: incomplete closed position row")
    return tuple(missing)


def _unlinked_fills(reads: Mapping[TraceTable, _Rows]) -> tuple[str, ...]:
    """約定 → 注文 → 試行の連鎖が切れている約定（D07 §10.2 の C4）。

    **入場側だけでなく決済側も見る**。C4 は「各完了取引の `entry_fill_id` /
    `close_fill_id` が表9 にあり、その `order_id` が表7 にあり、その `attempt_id` が
    表4 にある」ことを求めている。注文の有無だけを見ていると、決済注文の試行が表4 から
    落ちている判断履歴でも検査が通り、指標が採用してよい数値として出てしまう。
    """
    orders = _by_key(reads[TraceTable.ORDERS].records, "order_id")
    requests = _by_key(reads[TraceTable.ORDER_REQUESTS].records, "attempt_id")
    missing: list[str] = []
    for row in reads[TraceTable.FILLS].records:
        fill_id = row.get("fill_id")
        order_id = row.get("order_id")
        order = None if order_id is None else orders.get(order_id)
        if order is None:
            missing.append(f"{fill_id}: fill without an accepted order")
            continue
        attempt_id = order.get("attempt_id")
        if attempt_id is None or attempt_id not in requests:
            missing.append(f"{fill_id}: order {order_id} without an order request")
    return tuple(missing)


def _check_realized_matches_balance(
    result: BacktestResult, manifest: RunManifest, reads: Mapping[TraceTable, _Rows]
) -> ConsistencyCheckResult:
    """C5: `最後の balance − 初期残高` が末尾の確定損益と一致する（D07 §10.2）。"""
    summaries = result.summaries
    initial = manifest.config.account.initial_balance
    phases = {phase.name: phase for phase in manifest.phases.ordered()}
    snapshots = _ordered_snapshots(reads, phases)
    if summaries is None or not snapshots:
        return _result_of(
            CHECK_REALIZED_MATCHES_BALANCE,
            passed=False,
            expected=canonical_text("a completed run carries both final summaries and snapshots"),
            observed=canonical_text(
                {"summaries": summaries is not None, "snapshots": len(snapshots)}
            ),
            table=TraceTable.LEDGER_SNAPSHOTS,
        )
    last = snapshots[-1].balance
    if last.currency != initial.currency or summaries.realized.currency != initial.currency:
        # **通貨をまたぐ引き算をしない**。金額の型は通貨違いの演算を拒むので、ここで
        # 引き算に入ると例外で評価が中断し、通貨の食い違いを指す検査（C8）の結果も、
        # 失敗を説明する成果物も残らない。比べられないことを不合格として記録する。
        return _result_of(
            CHECK_REALIZED_MATCHES_BALANCE,
            passed=False,
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
    return _result_of(
        CHECK_REALIZED_MATCHES_BALANCE,
        passed=observed == summaries.realized,
        expected=canonical_text(summaries.realized),
        observed=canonical_text(observed),
        table=TraceTable.LEDGER_SNAPSHOTS,
    )


def _check_opportunity_count(
    result: BacktestResult, reads: Mapping[TraceTable, _Rows]
) -> ConsistencyCheckResult:
    """C6（警告）: 終端理由別の件数の合計が生成総数と一致する（D07 §10.2）。"""
    terminated = sum(
        1
        for row in reads[TraceTable.OPPORTUNITY_TRANSITIONS].records
        if row.get("to_state") == _TERMINATED
    )
    return _result_of(
        CHECK_OPPORTUNITY_COUNT_MATCHES,
        passed=terminated == result.opportunity_count,
        expected=canonical_text(result.opportunity_count),
        observed=canonical_text(terminated),
        table=TraceTable.OPPORTUNITY_TRANSITIONS,
    )


def _check_snapshot_order(
    reads: Mapping[TraceTable, _Rows], phases: Mapping[str, PhaseRank]
) -> ConsistencyCheckResult:
    """C7（警告）: 台帳 snapshot が処理点の昇順に並んでいる（D07 §10.2）。

    並んでいなければ本書の整列鍵で並べ替えて続行し、警告を残す。並べ替えは
    `_ordered_snapshots` が常に行うので、この検査は**保存されていた並び**を見る。
    """
    points = [_point(row, "at", phases) for row in reads[TraceTable.LEDGER_SNAPSHOTS].records]
    ordered = sorted(points, key=lambda point: point.sort_key)
    return _result_of(
        CHECK_SNAPSHOT_ORDER_MONOTONIC,
        passed=points == ordered,
        expected=canonical_text(tuple(str(point) for point in ordered)),
        observed=canonical_text(tuple(str(point) for point in points)),
        table=TraceTable.LEDGER_SNAPSHOTS,
    )


def _check_account_currency(
    result: BacktestResult, currency: CurrencyCode, reads: Mapping[TraceTable, _Rows]
) -> ConsistencyCheckResult:
    """C8: すべての `Money` 列の通貨が口座通貨と一致する（D07 §10.2・§7.1）。

    通貨の混じった合計は意味を持たないため、丸めや読み替えで通さない。
    """
    observed: set[str] = set()
    for row in reads[TraceTable.LEDGER_SNAPSHOTS].records:
        for column in ("balance_currency", "equity_currency"):
            value = row.get(column)
            if value is not None:
                observed.add(value)
    for row in reads[TraceTable.POSITIONS].records:
        value = row.get("realized_currency")
        if value is not None:
            observed.add(value)
    summaries = result.summaries
    if summaries is not None:
        observed.add(summaries.realized.currency.code)
        observed.add(summaries.equity_with_mtm.currency.code)
        observed.add(summaries.hypothetical_closed.currency.code)
        for amount in summaries.cost_breakdown.values():
            observed.add(amount.currency.code)
    return _result_of(
        CHECK_SINGLE_ACCOUNT_CURRENCY,
        passed=observed <= {currency.code},
        expected=canonical_text(currency.code),
        observed=canonical_text(tuple(sorted(observed))),
    )


# --- 取引と診断 ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """台帳 snapshot 1件（第5.2節の #1・#5〜#8 が読む3列）。"""

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


#: 主キーに現れる連番 ID の列と、その型（D02 §7.1、D06 §9.2）。
#: 書き方の揺れ（`FIL:00000001` と `FIL:000000001`）を同じ鍵として扱うために使う。
_KEY_ID_TYPES: Final[dict[str, type[SequentialId]]] = {
    "evaluation_id": EvaluationId,
    "opportunity_id": OpportunityId,
    "attempt_id": AttemptId,
    "order_id": OrderId,
    "fill_id": FillId,
    "position_id": PositionId,
}


def _normalized_key(column: str, value: str | None, kind: ColumnValueKind) -> str:
    """主キーの構成要素を、宣言した型に直してから比べる形にする（D06 §9.2）。

    文字列のまま比べると、同じ値の別の書き方（`2026-01-06T09:00:00Z` と
    `2026-01-06T09:00:00+00:00`、`FIL:00000001` と `FIL:000000001`、`0` と `00`）が別の鍵
    に見える。読み出したあとは型へ直して使うので、重複の判定だけ文字列で行うと、**判定は
    通るのに使う側では同じ値**になる行が残り、並びで結果が変わる（D07 §9.1 の条件1）。

    **空の構成要素は鍵として認めない**。主キーは行を一意に指すためのものなので、欠けた
    まま数えると、主キーを持たない行が集計と指標へ入る（D06 §9.2）。
    """
    if value is None:
        raise KernelValueError(
            f"the primary key column {column!r} must not be empty; a row without a primary"
            " key cannot be told apart from another (D06 §9.2)"
        )
    id_type = _KEY_ID_TYPES.get(column)
    if id_type is not None:
        return str(id_type.parse(value))
    if kind is ColumnValueKind.TIME:
        return str(UtcTime.parse(value))
    if kind is ColumnValueKind.INT:
        return str(int(value))
    if kind is ColumnValueKind.DECIMAL:
        return str(decimal_from_str(value))
    return value


def _require_unique_primary_keys(reads: Mapping[TraceTable, _Rows]) -> None:
    """9表それぞれの主キーが一意であることを確かめる（D06 §9.2）。

    **行を1つでも組み立てる前に行う**。重複した主キーを後勝ちで解決すると、同じ判断履歴
    でも Parquet の格納順によって辿り着く行が変わり、取引・集計・指標・結果のダイジェストが
    変わる（D07 §9.1 の条件1 が禁じている状態）。とくに台帳 snapshot は、同じ処理点に
    違う残高の行が2つあると、並びによって最終残高も最大ドローダウンも変わる。

    値の誤りとして送出する。呼び出し元は検査の範囲の中にあり、必須列の検査（C1）の
    不合格として結果に残る。
    """
    for table, columns in _PRIMARY_KEYS.items():
        kinds = {spec.column: spec.value_kind for spec in COLUMN_SPECS[table]}
        seen: set[tuple[str, ...]] = set()
        for row in reads[table].records:
            key = tuple(
                _normalized_key(column, row.get(column), kinds[column]) for column in columns
            )
            if key in seen:
                raise KernelValueError(
                    f"the primary key {columns} of {table.value} must be unique but"
                    f" {key} appears twice (D06 §9.2); resolving it by row order would make"
                    " the result depend on how the table was stored"
                )
            seen.add(key)


def _by_key(
    rows: Sequence[Mapping[str, str | None]], column: str
) -> dict[str, Mapping[str, str | None]]:
    """列の値で行を引けるようにする（外部キーを辿るため）。

    **同じ値の行が2つあれば拒否する**。後に現れた行で上書きすると、同じ判断履歴でも行の
    並びによって辿り着く行が変わり、取引・集計・指標・結果のダイジェストが変わる
    （D07 §9.1 の条件1 が禁じている状態）。主キーは表ごとに一意であることが D06 §9.2 で
    確定しているので、重複は不整合である。呼び出し元は検査の範囲の中にあり、ここで
    送出した値の誤りは必須列の検査（C1）の不合格として結果に残る。
    """
    indexed: dict[str, Mapping[str, str | None]] = {}
    for row in rows:
        value = row.get(column)
        if value is None:
            continue
        if value in indexed:
            raise KernelValueError(
                f"the trace column {column!r} must be unique but {value!r} appears twice"
                " (D06 §9.2); keeping the last row would make the result depend on the row"
                " order"
            )
        indexed[value] = row
    return indexed


def _build_trades(
    reads: Mapping[TraceTable, _Rows], phases: Mapping[str, PhaseRank]
) -> tuple[TradeRecord, ...]:
    """完了取引の一覧（D07 §5.2・§8.1）。整列鍵は `(entry_at, position_id)`。

    `opportunity_id` は建玉 → 入場約定 → 注文 → 試行の外部キーを辿って埋める。**辿れない
    場合は値を空にせず `None` のままにし、整合検査 C4 の不合格として残す**（連鎖が切れて
    いる判断履歴は不整合である）。
    """
    fills = _by_key(reads[TraceTable.FILLS].records, "fill_id")
    orders = _by_key(reads[TraceTable.ORDERS].records, "order_id")
    requests = _by_key(reads[TraceTable.ORDER_REQUESTS].records, "attempt_id")

    built: list[TradeRecord] = []
    for row in reads[TraceTable.POSITIONS].records:
        if row.get("status") != _CLOSED:
            continue
        entry_fill_id = row.get("entry_fill_id")
        close_fill_id = row.get("close_fill_id")
        realized = _optional_decimal(row, "realized_amount")
        if entry_fill_id is None or close_fill_id is None or realized is None:
            # 完了した建玉は入場・決済の約定と確定損益を必ず持つ（D06 §8.2）。欠けている
            # 行は整合検査 C3 の件数の食い違いとして現れる。
            continue
        entry_fill = fills.get(entry_fill_id)
        close_fill = fills.get(close_fill_id)
        if entry_fill is None or close_fill is None:
            continue
        entry_at = _point(entry_fill, "processed_at", phases)
        exit_at = _point(close_fill, "processed_at", phases)
        close_order = orders.get(_text(close_fill, "order_id"))
        cause = None if close_order is None else close_order.get("terms_cause")
        realized_money = Money(realized, CurrencyCode(_text(row, "realized_currency")))
        built.append(
            TradeRecord(
                trade_seq=1,
                position_id=PositionId.parse(_text(row, "position_id")),
                opportunity_id=_opportunity_of(entry_fill, orders, requests),
                symbol=Symbol(_text(row, "symbol")),
                side=OrderSide(_text(row, "side")),
                quantity=Quantity(_decimal(row, "quantity")),
                entry_fill_id=FillId.parse(entry_fill_id),
                entry_price=Price(_decimal(row, "entry_price")),
                entry_at=entry_at,
                close_fill_id=FillId.parse(close_fill_id),
                exit_price=Price(_decimal(close_fill, "price")),
                exit_at=exit_at,
                close_cause=None if cause is None else CloseCause(cause),
                realized=realized_money,
                holding=exit_at.time - entry_at.time,
                outcome=trade_outcome(realized_money),
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

    辿れないところで `None` を返す。**途中で例外にしない**。連鎖の切れは整合検査 C4 の
    不合格として記録するものであり、例外にすると評価が中断して検査の表が残らない
    （D07 §4.3）。
    """
    order_id = fill.get("order_id")
    order = None if order_id is None else orders.get(order_id)
    if order is None:
        return None
    attempt_id = order.get("attempt_id")
    if attempt_id is None:
        return None
    request = requests.get(attempt_id)
    if request is None:
        return None
    value = request.get("payload_opportunity_id")
    return None if value is None else OpportunityId.parse(value)


def _build_fill_diagnostics(
    reads: Mapping[TraceTable, _Rows], phases: Mapping[str, PhaseRank]
) -> tuple[FillDiagnostic, ...]:
    """約定1件につき1行の診断（D07 §6.2）。整列鍵は `fill_id`。"""
    orders = _by_key(reads[TraceTable.ORDERS].records, "order_id")
    built: list[FillDiagnostic] = []
    for row in reads[TraceTable.FILLS].records:
        order_id = row.get("order_id")
        order = None if order_id is None else orders.get(order_id)
        if order_id is None or order is None:
            # 注文を辿れない約定は整合検査 C4 の対象であり、診断は作れない。**ここで
            # 例外にしない**。例外にすると評価が中断し、失敗を説明する検査の表そのものが
            # 残らない（D07 §4.3）。
            continue
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


# --- 集計（D07 §6.1）----------------------------------------------------------


def _build_categories(reads: Mapping[TraceTable, _Rows]) -> tuple[CategoryCount, ...]:
    """7種の集計（D07 §6.1）。**語彙が有限の集計は0件の鍵も行として出す**。

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


# --- 指標（D07 §5.2）----------------------------------------------------------


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
    currency: CurrencyCode,
) -> tuple[MetricRecord, ...]:
    """指標15件（D07 §5.2）。並びは `MetricId` の宣言順（D07 §8.1）。"""
    unresolved = result.unresolved_intrabar_count > 0
    snapshots = _ordered_snapshots(reads, phases)
    initial = manifest.config.account.initial_balance
    summaries = result.summaries

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

    # #2 完了取引の確定損益の合計。
    if trades:
        total = trades[0].realized
        for trade in trades[1:]:
            total = total + trade.realized
        closed_profit: MetricValue = AmountValue(total)
    else:
        closed_profit = Unavailable(MetricKind.AMOUNT, MetricUnavailableReason.NO_TRADES)
    records.append(make(MetricId.CLOSED_TRADE_PROFIT, closed_profit, len(trades)))

    # #3 完了取引の件数（0 は正しい値であり、値なしにしない）。
    records.append(make(MetricId.TRADE_COUNT, CountValue(len(trades)), len(trades)))

    # #4 勝率。
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
    records.append(make(MetricId.EXPOSURE_RATE, *_exposure(reads, phases, manifest, trades)))

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
        for diagnostic in _build_fill_diagnostics(reads, phases)
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

    order = {metric_id: position for position, metric_id in enumerate(MetricId)}
    records.sort(key=lambda record: order[record.metric_id])
    return tuple(records)


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
    reads: Mapping[TraceTable, _Rows],
    phases: Mapping[str, PhaseRank],
    manifest: RunManifest,
    trades: Sequence[TradeRecord],
) -> tuple[MetricValue, int]:
    """建玉を保有していた時間の割合（D07 §5.2 の #9）。

    保有時間は入場約定の処理時刻から決済約定の処理時刻まで。**未決済建玉は run 末尾までを
    数える**（D07 §5.2 の式）。建玉が1件も無ければ保有時間0は観測された事実なので
    `RatioValue(0)` とし、値なしにしない。
    """
    fills = _by_key(reads[TraceTable.FILLS].records, "fill_id")
    run_interval = manifest.config.run_interval
    held = decimal_from_int(0)
    observations = 0
    for trade in trades:
        held = held + _microseconds(trade.holding)
        observations += 1
    for row in reads[TraceTable.POSITIONS].records:
        if row.get("status") == _CLOSED:
            continue
        entry_fill_id = row.get("entry_fill_id")
        entry_fill = None if entry_fill_id is None else fills.get(entry_fill_id)
        if entry_fill is None:
            continue
        opened_at = _time(entry_fill, "processed_at_time")
        if opened_at < run_interval.end:
            held = held + _microseconds(run_interval.end - opened_at)
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
