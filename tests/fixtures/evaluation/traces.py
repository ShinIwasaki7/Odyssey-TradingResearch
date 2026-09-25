"""紙上トレース T01 の判断履歴を手で組み立てる（D07 §11 の golden・単体テスト）。

T01 第9節の run（完了取引1件と残存建玉1件）が9表へ残す行を、**列の辞書として直接**書く。
バックテストを走らせずに指標の式だけを確かめたいので、行の出どころは実行ではなく紙上
トレースの数値そのものである。列名は D06 §9.1 の平坦化規則で得られるもので、
`tests/unit/evaluation/test_columns.py` が実装の宣言と一致することを機械検査する。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.backtest.domain.fills import CostKind
from odyssey_fx.backtest.domain.policies import ResolutionHierarchy, RunConfig
from odyssey_fx.backtest.engine.phases import BACKTEST_PHASES
from odyssey_fx.backtest.trace.manifest import DataCapabilityReport, RunManifest
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import BacktestResult, FinalSummaries, RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import AccountId, RunId, SnapshotId
from odyssey_fx.common.money import CurrencyCode, Money, decimal_from_str
from odyssey_fx.common.refs import (
    CodeDigest,
    CompiledStrategyRef,
    ConfigDigest,
    ContentDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    SnapshotRef,
)
from odyssey_fx.common.refs import run_id as run_id_of
from odyssey_fx.common.symbol import Symbol, SymbolSpecRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.application.evaluate_run import COLUMN_SPECS, INPUT_TABLES
from odyssey_fx.evaluation.application.manifest import EvaluationTable
from odyssey_fx.evaluation.application.ports import (
    ManifestReadFailure,
    TableReadResult,
    TraceColumnSpec,
)
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from tests.fixtures.synthetic import market

__all__ = [
    "CALENDAR",
    "JPY",
    "RUN_INTERVAL",
    "FakeRepository",
    "Row",
    "all_input_tables",
    "closed_trade_only",
    "manifest_for",
    "repository_for",
    "result_for",
    "t01_tables",
]

JPY = CurrencyCode("JPY")
USDJPY = Symbol("USDJPY")

#: T01 §1.1 の run 区間（12 日 = 1,036,800 秒）。
RUN_INTERVAL = Interval(
    start=UtcTime.parse("2015-01-04T22:00:00Z"),
    end=UtcTime.parse("2015-01-16T22:00:00Z"),
)

#: T01 の run が使った取引カレンダー（NY 17:00 の週の開閉、休場なし。D03 §3.4 の初版と同じ）。
#: run manifest の `calendar_ref`（`fx_ny17@v1`）と一致する（D07 §10.4 の C10）。
CALENDAR = market.calendar()

#: 1行を列の辞書で表す。
Row = dict[str, str | None]


def _digest(seed: str) -> ContentDigest:
    return digest(seed)


def _policy_ref(kind: str) -> PolicyRef:
    return PolicyRef(policy_kind=kind, policy_id=f"{kind}_v1", version=1, digest=_digest(kind))


#: T01 §1.1 の口座。
ACCOUNT = AccountSpec(
    account_id=AccountId("ACC1"),
    currency=JPY,
    initial_balance=Money(decimal_from_str("1000000"), JPY),
)

#: T01 §1.1 の執行系列。
EXECUTION_SERIES = SeriesId(symbol=USDJPY, timeframe=TimeframeRef("15m", 1), basis=PriceBasis.BID)

_CODE = CodeDigest(digest=_digest("code"))
_LOCK = LockDigest(digest=_digest("lock"))
_ENV = EnvDigest(digest=_digest("env"))


def manifest_for(
    *,
    initial_balance: str = "1000000",
    currency: CurrencyCode = JPY,
    run_interval: Interval = RUN_INTERVAL,
    status: str = "COMPLETED",
) -> RunManifest:
    """T01 の設定に対応する run manifest（D06 §9.3）。"""
    account = AccountSpec(
        account_id=AccountId("ACC1"),
        currency=currency,
        initial_balance=Money(decimal_from_str(initial_balance), currency),
    )
    config = RunConfig(
        run_interval=run_interval,
        snapshot_ref=SnapshotRef(snapshot_id=SnapshotId(_digest("snapshot"))),
        compiled_ref=CompiledStrategyRef(digest=_digest("compiled")),
        account=account,
        risk_policy_ref=_policy_ref("risk"),
        execution_policy_ref=_policy_ref("execution"),
        cost_model_ref=_policy_ref("cost"),
        conversion_policy_ref=_policy_ref("conversion"),
        delay_scenario_ref=_policy_ref("delay"),
        execution_series=EXECUTION_SERIES,
    )
    config_digest = ConfigDigest(digest=digest(config))
    return RunManifest(
        run_id=run_id_of(config_digest, _CODE, _LOCK, _ENV),
        config=config,
        config_digest=config_digest,
        code_digest=_CODE,
        lock_digest=_LOCK,
        env_digest=_ENV,
        phases=BACKTEST_PHASES,
        id_allocator_snapshot={},
        capability_report=DataCapabilityReport(compiled_match=True, integrity=IntegrityReport()),
        resolution_hierarchy=ResolutionHierarchy(levels=(EXECUTION_SERIES,)),
        unresolved_intrabar_count=0,
        unresolved_intrabar_ratio=decimal_from_str("0"),
        swap_modeled=False,
        status=status,
        symbol_spec_ref=SymbolSpecRef(symbol=USDJPY, version=1, digest=_digest("spec")),
        calendar_ref="fx_ny17@v1",
        timeframe_def_refs=(TimeframeRef("15m", 1), TimeframeRef("1h", 1)),
    )


def result_for(
    manifest: RunManifest,
    *,
    status: RunStatus = RunStatus.COMPLETED,
    trade_count: int = 1,
    opportunity_count: int = 2,
    unresolved_intrabar_count: int = 0,
    with_summaries: bool = True,
) -> BacktestResult:
    """T01 §9.3 の末尾集計を持つ結果 DTO（D06 §9.4）。"""
    summaries = (
        FinalSummaries(
            realized=Money(decimal_from_str("36706"), JPY),
            equity_with_mtm=Money(decimal_from_str("1051706"), JPY),
            hypothetical_closed=Money(decimal_from_str("14670"), JPY),
            cost_breakdown={
                CostKind.COMMISSION: Money(decimal_from_str("94"), JPY),
                CostKind.SLIPPAGE_IN_PRICE: Money(decimal_from_str("940"), JPY),
                CostKind.SPREAD_IN_PRICE: Money(decimal_from_str("1240"), JPY),
            },
        )
        if with_summaries
        else None
    )
    return BacktestResult(
        run_id=manifest.run_id,
        status=status,
        trace_tables={
            table: f"runs/{manifest.run_id}/{table.value}.parquet" for table in TraceTable
        },
        capability_report=manifest.capability_report,
        manifest_ref=f"runs/{manifest.run_id}/manifest.json",
        unresolved_intrabar_count=unresolved_intrabar_count,
        trade_count=trade_count,
        opportunity_count=opportunity_count,
        summaries=summaries,
    )


def _row(table: TraceTable, run_id: str, **values: str | None) -> Row:
    """宣言した列をすべて持つ1行を作る（書いていない列は `None`）。"""
    row: Row = {spec.column: None for spec in COLUMN_SPECS[table]}
    row["run_id"] = run_id
    for name, value in values.items():
        assert name in row, (table.value, name)
        row[name] = value
    return row


def _costs(*, commission: str | None, slippage: str | None, spread: str | None) -> Row:
    """約定1件の区分別の費用の列（D06 §9.2 の表9。記録が無い区分は金額・通貨とも空）。"""
    columns: Row = {}
    for prefix, amount in (
        ("cost_commission", commission),
        ("cost_slippage_in_price", slippage),
        ("cost_spread_in_price", spread),
    ):
        columns[f"{prefix}_amount"] = amount
        columns[f"{prefix}_currency"] = None if amount is None else "JPY"
    return columns


def t01_tables(run_id: str) -> dict[TraceTable, list[Row]]:
    """T01 第9節の run が9表へ残す行（数値は T01 §2・§9・§9.4）。"""
    ledger = [
        ("2015-01-06T09:00:00Z", "LEDGER_UPDATE", "0", "1000000", "1000000"),
        ("2015-01-06T09:00:00Z", "EXECUTION_OPEN", "1", "999968", "998688"),
        ("2015-01-06T11:15:00Z", "LEDGER_UPDATE", "0", "1036736", "1036736"),
        ("2015-01-08T10:00:00Z", "EXECUTION_OPEN", "1", "1036706", "1036706"),
        ("2015-01-16T22:00:00Z", "RUN_END", "0", "1036706", "1051706"),
    ]
    return {
        TraceTable.EVALUATIONS: [
            _row(
                TraceTable.EVALUATIONS,
                run_id,
                evaluation_id="EVAL:00000001",
                request_id="REQ:00000001",
                decision_time="2015-01-06T08:45:00Z",
                outcome_kind="EVALUATED",
                outcome_diagnoses="[]",
            ),
            _row(
                TraceTable.EVALUATIONS,
                run_id,
                evaluation_id="EVAL:00000002",
                request_id="REQ:00000002",
                decision_time="2015-01-06T09:00:00Z",
                outcome_kind="SKIPPED",
                outcome_diagnoses=(
                    '["{\\"input_name\\":\\"prices\\",\\"reason\\":\\"WARMUP_INSUFFICIENT\\"}"]'
                ),
            ),
        ],
        TraceTable.OPPORTUNITY_TRANSITIONS: [
            # 主キーは `(opportunity_id, at)`（D06 §9.2）。処理点は3列で1つの値である。
            _row(
                TraceTable.OPPORTUNITY_TRANSITIONS,
                run_id,
                opportunity_id="OPP:00000001",
                at_time="2015-01-06T09:00:00Z",
                at_phase="POST_FILL_EVALUATION",
                at_sequence="0",
                to_state="TERMINATED",
                reason_code="FULFILLED_BY_ORDER_ACCEPTANCE",
            ),
            _row(
                TraceTable.OPPORTUNITY_TRANSITIONS,
                run_id,
                opportunity_id="OPP:00000002",
                at_time="2015-01-08T10:00:00Z",
                at_phase="POST_FILL_EVALUATION",
                at_sequence="0",
                to_state="TERMINATED",
                reason_code="FULFILLED_BY_ORDER_ACCEPTANCE",
            ),
        ],
        TraceTable.ORDER_REQUESTS: [
            _row(
                TraceTable.ORDER_REQUESTS,
                run_id,
                attempt_id="ATT:00000001",
                payload_kind="ENTRY_REQUEST",
                payload_opportunity_id="OPP:00000001",
            ),
            _row(
                TraceTable.ORDER_REQUESTS,
                run_id,
                attempt_id="ATT:00000002",
                payload_kind="CLOSE_REQUEST",
                payload_position_id="POS:00000001",
            ),
            _row(
                TraceTable.ORDER_REQUESTS,
                run_id,
                attempt_id="ATT:00000003",
                payload_kind="ENTRY_REQUEST",
                payload_opportunity_id="OPP:00000002",
            ),
        ],
        TraceTable.ATTEMPT_DECISIONS: [
            _row(
                TraceTable.ATTEMPT_DECISIONS,
                run_id,
                attempt_id=f"ATT:{index:08d}",
                kind="ACCEPTED",
                order_id=f"ORD:{index:08d}",
            )
            for index in (1, 2, 3)
        ],
        TraceTable.ORDERS: [
            _row(
                TraceTable.ORDERS,
                run_id,
                order_id="ORD:00000001",
                attempt_id="ATT:00000001",
                accepted_at_time="2015-01-06T09:00:00Z",
                side="BUY",
                terms_kind="ENTRY_TERMS",
                terms_reference_quote_price="150.06",
                terms_reference_quote_observed_at="2015-01-06T09:00:00Z",
            ),
            _row(
                TraceTable.ORDERS,
                run_id,
                order_id="ORD:00000002",
                attempt_id="ATT:00000002",
                accepted_at_time="2015-01-06T11:15:00Z",
                side="SELL",
                terms_kind="CLOSE_TERMS",
                terms_cause="TAKE_PROFIT",
                terms_position_id="POS:00000001",
            ),
            _row(
                TraceTable.ORDERS,
                run_id,
                order_id="ORD:00000003",
                attempt_id="ATT:00000003",
                accepted_at_time="2015-01-08T10:00:00Z",
                side="BUY",
                terms_kind="ENTRY_TERMS",
                terms_reference_quote_price="151.02",
                terms_reference_quote_observed_at="2015-01-08T10:00:00Z",
            ),
        ],
        TraceTable.FILLS: [
            _row(
                TraceTable.FILLS,
                run_id,
                fill_id="FIL:00000001",
                order_id="ORD:00000001",
                position_id="POS:00000001",
                processed_at_time="2015-01-06T09:00:00Z",
                processed_at_phase="EXECUTION_OPEN",
                processed_at_sequence="0",
                price="150.08",
                quantity="32000",
                # T01 §2.6 / D07 §7.3: 入場約定の費用（0.001・0.010・0.020 × 32000）。
                **_costs(commission="32", slippage="320", spread="640"),
            ),
            _row(
                TraceTable.FILLS,
                run_id,
                fill_id="FIL:00000002",
                order_id="ORD:00000002",
                position_id="POS:00000001",
                processed_at_time="2015-01-06T11:15:00Z",
                processed_at_phase="EXECUTION_BAR_COMPLETE",
                processed_at_sequence="0",
                price="151.23",
                quantity="32000",
                # 売りの決済は bid 基準なので提示価格の幅の記録が無い（T01 §2.6）。
                **_costs(commission="32", slippage="320", spread=None),
            ),
            _row(
                TraceTable.FILLS,
                run_id,
                fill_id="FIL:00000003",
                order_id="ORD:00000003",
                position_id="POS:00000002",
                processed_at_time="2015-01-08T10:00:00Z",
                processed_at_phase="EXECUTION_OPEN",
                processed_at_sequence="0",
                price="151",
                quantity="30000",
                # 末尾集計の区分別合計（94・940・1,240）と合う P2 の入場費用。
                **_costs(commission="30", slippage="300", spread="600"),
            ),
        ],
        TraceTable.POSITIONS: [
            _row(
                TraceTable.POSITIONS,
                run_id,
                position_id="POS:00000001",
                symbol="USDJPY",
                side="BUY",
                quantity="32000",
                entry_price="150.08",
                entry_fill_id="FIL:00000001",
                opened_at_time="2015-01-06T09:00:00Z",
                opened_at_phase="EXECUTION_OPEN",
                opened_at_sequence="0",
                status="CLOSED",
                close_fill_id="FIL:00000002",
                realized_amount="36768",
                realized_currency="JPY",
            ),
            _row(
                TraceTable.POSITIONS,
                run_id,
                position_id="POS:00000002",
                symbol="USDJPY",
                side="BUY",
                quantity="30000",
                entry_price="151",
                entry_fill_id="FIL:00000003",
                opened_at_time="2015-01-08T10:00:00Z",
                opened_at_phase="EXECUTION_OPEN",
                opened_at_sequence="0",
                status="OPEN",
            ),
        ],
        TraceTable.INTRABAR_RESOLUTIONS: [
            _row(
                TraceTable.INTRABAR_RESOLUTIONS,
                run_id,
                fill_id="FIL:00000002",
                position_id="POS:00000001",
                method="SINGLE_HIT",
            )
        ],
        TraceTable.LEDGER_SNAPSHOTS: [
            _row(
                TraceTable.LEDGER_SNAPSHOTS,
                run_id,
                at_time=at_time,
                at_phase=phase,
                at_sequence=sequence,
                balance_amount=balance,
                balance_currency="JPY",
                equity_amount=equity,
                equity_currency="JPY",
            )
            for at_time, phase, sequence, balance, equity in ledger
        ],
    }


def closed_trade_only(run_id: str) -> dict[TraceTable, list[Row]]:
    """残存建玉を持たない判断履歴（D07 §5.2 の #9 の検算値に対応する形）。

    T01 が定める建玉を保有していた時間の割合の検算値 `0.0078125` は、**完了した取引の
    保有時間だけ**を数えた値である。残存建玉を落とした履歴でその値になることを確かめる。
    """
    tables = t01_tables(run_id)
    tables[TraceTable.POSITIONS] = [
        row for row in tables[TraceTable.POSITIONS] if row["status"] == "CLOSED"
    ]
    tables[TraceTable.FILLS] = [
        row for row in tables[TraceTable.FILLS] if row["fill_id"] != "FIL:00000003"
    ]
    return tables


@dataclass
class FakeRepository:
    """判断履歴を列の辞書として供給する読み書き口（`ResultRepository` を構造的に満たす）。

    表そのものを落としたり、列を欠かせたりできるようにしてあるのは、整合検査 C1 が
    「表が無い」と「行が0件」を区別することを確かめるためである（D07 §4.3）。
    """

    manifest: RunManifest
    tables: Mapping[TraceTable, Sequence[Row]]
    absent: frozenset[TraceTable] = frozenset()
    dropped_columns: Mapping[TraceTable, frozenset[str]] = field(default_factory=dict)
    written: list[tuple[object, Mapping[EvaluationTable, tuple[object, ...]]]] = field(
        default_factory=list
    )
    #: 空でなければ、run manifest を読めなかったことを返す（D07 §10.1.1 の R1-D07-4）。
    manifest_failure: str | None = None

    def read_manifest(self, run_id: RunId) -> RunManifest | ManifestReadFailure:
        assert str(run_id) == str(self.manifest.run_id)
        if self.manifest_failure is not None:
            return ManifestReadFailure(run_id=run_id, detail=self.manifest_failure)
        return self.manifest

    def read_table(
        self, run_id: object, table: TraceTable, columns: tuple[TraceColumnSpec, ...]
    ) -> TableReadResult:
        if table in self.absent:
            return TableReadResult(table=table, table_present=False)
        dropped = self.dropped_columns.get(table, frozenset())
        missing = tuple(spec.column for spec in columns if spec.column in dropped)
        if missing:
            return TableReadResult(table=table, table_present=True, missing_columns=missing)
        rows = tuple(
            tuple(row.get(spec.column) for spec in columns) for row in self.tables.get(table, ())
        )
        return TableReadResult(table=table, table_present=True, rows=rows)

    def write_evaluation(
        self, report: object, rows: Mapping[EvaluationTable, tuple[object, ...]]
    ) -> None:
        self.written.append((report, rows))


def repository_for(
    tables: Mapping[TraceTable, Sequence[Row]] | None = None,
    *,
    manifest: RunManifest | None = None,
    absent: frozenset[TraceTable] = frozenset(),
    dropped_columns: Mapping[TraceTable, frozenset[str]] | None = None,
    manifest_failure: str | None = None,
) -> FakeRepository:
    """T01 の履歴を持つ読み書き口を1つ作る。"""
    run_manifest = manifest_for() if manifest is None else manifest
    return FakeRepository(
        manifest=run_manifest,
        tables=t01_tables(str(run_manifest.run_id)) if tables is None else tables,
        absent=absent,
        dropped_columns={} if dropped_columns is None else dropped_columns,
        manifest_failure=manifest_failure,
    )


def all_input_tables() -> tuple[TraceTable, ...]:
    """段階2で読む9表（テストが表の顔ぶれを確かめるために使う）。"""
    return INPUT_TABLES
