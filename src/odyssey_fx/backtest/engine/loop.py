"""判断時点のフェーズ順と、公開フィード駆動のイベントループ（D06 §4）。

`RunBacktest` は公開フィードのイベント列を `available_at` 順に読み、**同じ時刻のイベントを
1つの判断時点にまとめて** D06 §4.1 の15フェーズを因果順に実行する。戦略ランタイムを呼ぶのは
1つの判断時点で最大2回（run 末尾では最大3回）で、2回目・3回目は**新しい `EventId` を持つ
別の公開バッチ**として渡す（同じ `batch_id` での再呼び出しはランタイムが拒む）。

台帳の現在状態は `AccountLedger` 1つの不変値で表し、エンジンは可変参照を1つだけ持つ。
確定単位は `portfolio.ledger` の関数が組み立て、参照の差し替えは1文で行う（D06 §4.4）。

**ポートの置き場所の差異**: D06 §3 は公開フィード・執行系列・カレンダーの `Protocol` を
`application.ports` に置くとしているが、それらを使うのは1つ下の層の `engine` であり、
`engine` は `application` を import できない（D01 §3.3 の契約 L2c）。そこで構造の定義を
ここに置き、`application.ports` が同じ名前で公開する（記録の書き出し口 `TraceSink` と
`ResultWriter` は `application` だけが使うので `application.ports` にそのまま置く）。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Protocol, runtime_checkable

from odyssey_fx.backtest.admission.admission import (
    AdmissionOutcome,
    AttemptAccepted,
    AttemptDecision,
    AttemptRejected,
    decide_close,
    decide_entry,
)
from odyssey_fx.backtest.admission.request_assembly import (
    build_close_payload,
    build_entry_payload,
    build_request,
    entry_output_ids,
    order_payloads,
)
from odyssey_fx.backtest.domain.account import AccountLedger, AccountSpec
from odyssey_fx.backtest.domain.events import OrderEvent
from odyssey_fx.backtest.domain.fills import (
    BarExecutionInterval,
    CostEntry,
    CostKind,
    ExactExecutionTime,
    ExecutionTime,
    FillRecord,
)
from odyssey_fx.backtest.domain.orders import (
    AcceptedCloseTerms,
    AcceptedEntryTerms,
    AcceptedOrder,
    CloseCause,
    CloseRequest,
    EntryRequest,
    ImmediateAfterFill,
    OrderPayload,
    OrderSide,
    OrderStatus,
    ProtectionHit,
    ReferenceQuote,
    RequestOrigin,
    ScheduledOpen,
)
from odyssey_fx.backtest.domain.policies import (
    ConversionPath,
    ConversionPolicy,
    CostModel,
    ExecutionPolicy,
    RiskPolicy,
    RunConfig,
)
from odyssey_fx.backtest.domain.positions import (
    Position,
    PositionRiskAllocation,
    PositionStatus,
    ProtectionState,
    RiskMeasurement,
)
from odyssey_fx.backtest.domain.reservations import ReservationState
from odyssey_fx.backtest.engine.clock import PhaseClock
from odyssey_fx.backtest.engine.phases import (
    BACKTEST_PHASES,
    PHASE_ADMISSION,
    PHASE_EXECUTION_BAR_COMPLETE,
    PHASE_EXECUTION_OPEN,
    PHASE_LEDGER_UPDATE,
    PHASE_ORDER_EXPIRY,
    PHASE_P5_ORDER_INTENT,
    PHASE_POST_FILL_ADMISSION,
    PHASE_POST_FILL_EVALUATION,
    PHASE_RUN_END,
)
from odyssey_fx.backtest.engine.run_end import final_summaries
from odyssey_fx.backtest.execution.cost_model import FillPurpose, cost_entries
from odyssey_fx.backtest.execution.emergency import (
    gap_breaches_stop,
    needs_emergency_close,
    protection_base_price,
)
from odyssey_fx.backtest.execution.fill_model import fill_price
from odyssey_fx.backtest.execution.protection_hits import (
    ChildBars,
    ResolutionMethod,
    resolve_intrabar,
)
from odyssey_fx.backtest.portfolio.conversion import (
    ConversionUnavailable,
    rate_of,
    resolve_path,
)
from odyssey_fx.backtest.portfolio.ledger import (
    LedgerSnapshot,
    commit_close_acceptance,
    commit_close_fill,
    commit_entry_acceptance,
    commit_entry_fill,
    commit_immediate_close,
    commit_order_termination,
    snapshot_of,
)
from odyssey_fx.backtest.portfolio.mtm import unrealized
from odyssey_fx.backtest.trace.manifest import DataCapabilityReport
from odyssey_fx.backtest.trace.recorder import (
    CompositeRow,
    EvidenceKind,
    EvidenceRecord,
    ManagementApplication,
    MarketObservationRef,
    TraceTable,
)
from odyssey_fx.backtest.trace.result import FinalSummaries, RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AllocationId,
    AttemptId,
    EvaluationId,
    EventId,
    EvidenceId,
    FillId,
    IdAllocator,
    OpportunityId,
    OutputId,
    PositionId,
)
from odyssey_fx.common.money import (
    ConversionRate,
    Money,
    Price,
    RoundingDirection,
    decimal_from_int,
    kernel_context,
)
from odyssey_fx.common.reason import (
    CarryNotAllowedDetail,
    DataErrorDetail,
    PositionClosedDetail,
    Reason,
    ReasonCode,
    RunEndDetail,
)
from odyssey_fx.common.refs import EvidenceRef, PolicyRef
from odyssey_fx.common.symbol import SymbolSpec
from odyssey_fx.common.time import Interval, PhaseSet, ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy
from odyssey_fx.strategy.declarations.evaluation import RuntimeEventKind
from odyssey_fx.strategy.declarations.refs import MarketDataField
from odyssey_fx.strategy.records.payloads import (
    AccountContext,
    ClosePosition,
    PositionContext,
    SetTakeProfit,
    TradeDirection,
)
from odyssey_fx.strategy.records.records import OutputRecord
from odyssey_fx.strategy.runtime.ports import (
    AdmissionNotice,
    BarClosure,
    PublicationBatch,
    RuntimeEventNotice,
    StrategyRuntime,
)
from odyssey_fx.strategy.runtime.requests import (
    EntryProposal,
    Failed,
    ManagementRequest,
    RuntimeStepResult,
)

__all__ = [
    "BacktestEngine",
    "Calendar",
    "EngineContext",
    "ExecutionSeries",
    "IntrabarSeries",
    "PublicationFeed",
    "PublicationEventView",
    "TraceOutputSink",
]

_ZERO = decimal_from_int(0)

#: 「判断時刻と同じ時刻に始まる執行足も候補に含める」ための最小の後戻り。
#: 執行系列の `next_bar_key_after` は**より後**に始まる足を返すため、判断時点と同時刻に
#: 始まる足（因果順序上まだ到来していない最初の始値）を候補にするにはここだけ1マイクロ秒
#: 手前から探す（D06 §7.1・§5.3）。
_TINY = timedelta(microseconds=1)

#: 直前の週の終わりを探す窓（診断に載せる `session_close` を求めるためだけに使う）。
_ONE_WEEK = timedelta(days=7)


# --- ポート（構造） ----------------------------------------------------------


@runtime_checkable
class _KindView(Protocol):
    """公開イベントの種別（`marketdata.application` の列挙を値として読む）。"""

    @property
    def value(self) -> str: ...


@runtime_checkable
class PublicationEventView(Protocol):
    """公開イベント1件（D03 §7.1 の4種）。

    種別の列挙そのものは `marketdata.application` にあり `backtest` から import できない
    （契約 F6）。そこで**値として読める種別**と、系列・足・発生時刻だけを構造として要求する。
    """

    @property
    def kind(self) -> _KindView: ...

    @property
    def series(self) -> SeriesId: ...

    @property
    def bar_key(self) -> BarKey: ...

    @property
    def at(self) -> UtcTime: ...


@runtime_checkable
class PublicationFeed(Protocol):
    """`available_at` 順の公開イベント列（D06 §3・§4.3）。

    **形の差異**: D06 §3 の表は `events(interval)` という操作を挙げているが、この口を実装
    するのは `marketdata.application` の公開フィード（D01 §4）であり、そちらは `events` を
    **タプルの項目**として持ち、反復できる値になっている。操作として要求すると本物の
    フィードが構造的に満たさなくなり、合成のためだけの薄い適合層が1つ増える。そこで
    **反復できること**だけを要求し、run 区間で絞るのはエンジン側で行う（区間の外の
    イベントを処理しないという規則は変わらない）。
    """

    def __iter__(self) -> Iterator[PublicationEventView]: ...


@runtime_checkable
class ExecutionSeries(Protocol):
    """執行用系列（D03 §6.3 の3操作）。"""

    def bar(self, bar_key: BarKey) -> Bar | None: ...

    def open_of(self, bar_key: BarKey) -> Price | None: ...

    def next_bar_key_after(self, moment: UtcTime) -> BarKey | None: ...


@runtime_checkable
class IntrabarSeries(Protocol):
    """足内競合を解決する下位足の供給（D06 §7.4）。

    D06 §3 の `ExecutionSeries` は単一系列の3操作しか持たず、下位足を時系列順に走査する
    操作が無い。階層が2段以上のときだけ使う。
    """

    def bars_in(self, series: SeriesId, interval: Interval) -> tuple[Bar, ...]: ...


#: カレンダーは `marketdata.domain` の型をそのまま受ける（D01 §4）。
Calendar = TradingCalendar


class TraceOutputSink:
    """戦略ランタイムからの出力記録の受け口（D05 §6.1 の `OutputSink`）。

    出力記録は**ここで受け取った分だけ**を表1 へ書き、`RuntimeStepResult.outputs` を再送
    しない（同じ `output_id` の行が二重に入り、出力件数が水増しになる。D06 §4.2）。
    """

    __slots__ = ("_records",)

    def __init__(self) -> None:
        self._records: list[OutputRecord[object]] = []

    def emit(self, records: tuple[OutputRecord[object], ...]) -> None:
        """ランタイムが送出した出力記録を貯める。"""
        self._records.extend(records)

    def drain(self) -> tuple[OutputRecord[object], ...]:
        """貯めた記録を取り出して空にする。"""
        drained = tuple(self._records)
        self._records.clear()
        return drained


class EngineContext:
    """建玉・口座の時点情報をランタイムへ供給する（D05 §6.1 の `RuntimeContextView`）。

    エンジンが1 run に1つ持つ**可変の実行コンテキスト**であり、台帳への参照を1つだけ持つ
    （D06 §1 の例外）。読み取り時点より後に確定した値は含めない。段階2の項目はいずれも
    約定または管理要求の適用で確定済みの値である。
    """

    __slots__ = ("equity", "ledger")

    def __init__(self, spec: AccountSpec) -> None:
        self.ledger = AccountLedger.opened(spec)
        self.equity = spec.initial_balance

    def position_context(self, at: UtcTime, position_id: PositionId | None) -> object | None:
        """建玉の時点情報（`position_id` が `None` なら開いている唯一の建玉）。"""
        del at
        if position_id is None:
            positions = self.ledger.open_positions()
            if len(positions) != 1:
                return None
            position = positions[0]
        else:
            found = self.ledger.positions.get(position_id)
            if found is None or not found.is_open:
                return None
            position = found
        return PositionContext(
            position_id=position.position_id,
            symbol=position.symbol,
            direction=(
                TradeDirection.LONG if position.side is OrderSide.BUY else TradeDirection.SHORT
            ),
            quantity=position.quantity,
            entry_price=position.entry_price,
            effective_stop_loss=position.protection.stop_loss,
            effective_take_profit=position.protection.take_profit,
            opened_at=position.opened_at,
        )

    def account_context(self, at: UtcTime) -> object:
        """口座の時点情報（段階2の部品は読まない）。"""
        del at
        return AccountContext(
            account_id=self.ledger.account_id,
            currency=self.ledger.currency,
            balance=self.ledger.balance,
            equity=self.equity,
            consumed_risk=self.ledger.consumed(),
        )


@dataclass(frozen=True, slots=True)
class _DecisionEvents:
    """1つの判断時点に属する公開イベント（D06 §4.2・§4.3）。"""

    bar_complete: BarKey | None = None
    publications: tuple[BarKey, ...] = ()
    boundaries: tuple[tuple[BarKey, UtcTime], ...] = ()
    execution_open: BarKey | None = None


class _RunFailure(Exception):
    """run を止める失敗（D06 §4.2・§10.4）。

    例外で運ぶのは、失敗が「その判断時点の残りのフェーズをすべて飛ばす」性質のもので、
    戻り値で運ぶとフェーズごとに分岐が増えるためである。捕まえるのは `execute` だけで、
    そこで第10.4節の実行失敗として扱う。

    **どのフェーズで検出したか**を一緒に運ぶ。未約定注文の取消をそのフェーズの処理点で
    刻まないと、受付（rank 10）より前の順位で取消が記録され、判断履歴を処理点の順に
    読み直したときに取消が受付より先に来てしまう（D06 §5.1 の遷移5 はどのフェーズでも
    起こりうる）。
    """

    def __init__(self, reason: Reason, phase: str) -> None:
        super().__init__(f"{reason} in {phase}")
        self.reason = reason
        self.phase = phase


class BacktestEngine:
    """1つの run を決定論的に実行する参照実装（D06 §4）。"""

    def __init__(
        self,
        *,
        config: RunConfig,
        compiled: CompiledStrategy,
        runtime: StrategyRuntime,
        context: EngineContext,
        output_sink: TraceOutputSink,
        allocator: IdAllocator,
        feed: PublicationFeed,
        execution_series: ExecutionSeries,
        calendar: Calendar,
        risk_policy: RiskPolicy,
        execution_policy: ExecutionPolicy,
        cost_model: CostModel,
        conversion_policy: ConversionPolicy,
        symbol_spec: SymbolSpec,
        capability_report: DataCapabilityReport,
        intrabar_series: IntrabarSeries | None = None,
        strategy_priority: int = 0,
        phases: PhaseSet = BACKTEST_PHASES,
    ) -> None:
        self._config = config
        self._compiled = compiled
        self._runtime = runtime
        self._context = context
        self._sink = output_sink
        self._allocator = allocator
        self._feed = feed
        self._execution = execution_series
        self._calendar = calendar
        self._risk_policy = risk_policy
        self._execution_policy = execution_policy
        self._cost_model = cost_model
        self._conversion_policy = conversion_policy
        self._symbol_spec = symbol_spec
        self._capability = capability_report
        self._intrabar = intrabar_series
        self._priority = strategy_priority
        self._phases = phases

        self._rows: dict[TraceTable, list[object]] = {table: [] for table in TraceTable}
        self._last_complete: Bar | None = None
        self._status = RunStatus.COMPLETED
        self._failure: Reason | None = None
        self._unresolved = 0
        self._conflicts = 0
        self._trade_count = 0
        self._opportunities: set[OpportunityId] = set()
        self._summaries: FinalSummaries | None = None
        self._cost_totals: dict[CostKind, Money] = {}
        self._measurements: dict[PositionId, RiskMeasurement] = {}
        self._entry_opportunities: dict[AttemptId, OpportunityId] = {}
        self._clock: PhaseClock | None = None
        self._last_snapshot_at: ProcessingPoint | None = None
        self._output_evaluations: dict[OutputId, EvaluationId] = {}

    # --- 読み出し -----------------------------------------------------------

    @property
    def rows(self) -> dict[TraceTable, tuple[object, ...]]:
        """表ごとに集めた行（`TraceSink` へ渡す）。"""
        return {table: tuple(values) for table, values in self._rows.items()}

    @property
    def status(self) -> RunStatus:
        """run の結末。"""
        return self._status

    @property
    def failure_reason(self) -> Reason | None:
        """失敗した場合の理由。"""
        return self._failure

    @property
    def summaries(self) -> FinalSummaries | None:
        """末尾の3集計（正常完走のときだけ）。"""
        return self._summaries

    @property
    def unresolved_intrabar_count(self) -> int:
        """順序を観測できないまま損切りを採った件数（ADR-0030）。"""
        return self._unresolved

    @property
    def intrabar_conflict_count(self) -> int:
        """足内で両側に触れた件数（割合の分母）。"""
        return self._conflicts

    @property
    def trade_count(self) -> int:
        """決済まで終わった建玉の数。"""
        return self._trade_count

    @property
    def opportunity_count(self) -> int:
        """判断履歴に現れた取引機会の総数。"""
        return len(self._opportunities)

    @property
    def ledger(self) -> AccountLedger:
        """現在の台帳（テストと最終集計が読む）。"""
        return self._context.ledger

    # --- 実行 ---------------------------------------------------------------

    def execute(self) -> None:
        """run 区間のイベントを読み、判断時点ごとに15フェーズを実行する（D06 §4.2）。"""
        if not self._capability.runnable:
            self._status = RunStatus.FAILED_CAPABILITY
            self._failure = self._capability.reason
            return
        run_end = self._config.run_interval.end
        grouped = self._group_events()
        for decision_time in sorted(grouped, key=lambda moment: moment.value):
            if decision_time >= run_end:
                break
            try:
                self._decision_point(decision_time, grouped[decision_time], is_run_end=False)
            except _RunFailure as failure:
                self._fail(decision_time, failure)
                self._finalize_tables()
                return
        try:
            self._decision_point(run_end, grouped.get(run_end, _DecisionEvents()), is_run_end=True)
        except _RunFailure as failure:
            self._fail(run_end, failure)
        self._finalize_tables()

    def _group_events(self) -> dict[UtcTime, _DecisionEvents]:
        """公開イベントを判断時刻ごとにまとめる（D06 §4.2・§4.3）。"""
        buckets: dict[UtcTime, dict[str, object]] = {}
        execution_series = self._config.execution_series
        run_interval = self._config.run_interval
        for event in self._feed:
            if event.at < run_interval.start or run_interval.end < event.at:
                # run 区間の外のイベントは処理しない。末尾の時刻ちょうどのイベントは
                # 末尾の判断時点が使うので残す（D06 §10.1 の手順1・2）。
                continue
            bucket = buckets.setdefault(event.at, {"publications": [], "boundaries": []})
            kind = event.kind.value
            if kind == "EXECUTION_BAR_COMPLETE" and event.series == execution_series:
                bucket["bar_complete"] = event.bar_key
            elif kind == "EXECUTION_OPEN" and event.series == execution_series:
                bucket["execution_open"] = event.bar_key
            elif kind == "PUBLICATION":
                publications = bucket["publications"]
                assert isinstance(publications, list)  # noqa: S101 - 直前で list を置いている
                publications.append(event.bar_key)
            elif kind == "SCHEDULED_BOUNDARY":
                boundaries = bucket["boundaries"]
                assert isinstance(boundaries, list)  # noqa: S101 - 直前で list を置いている
                boundaries.append((event.bar_key, event.at))
        grouped: dict[UtcTime, _DecisionEvents] = {}
        for moment, bucket in buckets.items():
            publications = bucket["publications"]
            boundaries = bucket["boundaries"]
            assert isinstance(publications, list)  # noqa: S101
            assert isinstance(boundaries, list)  # noqa: S101
            bar_complete = bucket.get("bar_complete")
            execution_open = bucket.get("execution_open")
            grouped[moment] = _DecisionEvents(
                bar_complete=bar_complete if isinstance(bar_complete, BarKey) else None,
                publications=tuple(publications),
                boundaries=tuple(boundaries),
                execution_open=execution_open if isinstance(execution_open, BarKey) else None,
            )
        return grouped

    def _fail(self, decision_time: UtcTime, failure: _RunFailure) -> None:
        """実行失敗（D06 §10.4）。未約定注文を取消し、予約を解放する。

        取消は**失敗を検出したフェーズ**の処理点で刻む（D06 §5.1 の遷移5）。常に期限の
        フェーズで刻むと、同じ判断時点で受け付けた注文の取消が受付より前の順位になり、
        判断履歴を処理点の順に読み直したときに因果順が逆転する。
        """
        self._status = RunStatus.FAILED_DATA_ERROR
        self._failure = failure.reason
        # 同じ判断時点の時計をそのまま使う。作り直すと通し番号が 0 に戻り、その判断時点で
        # 既に刻んだ処理点と同じ値になってしまう（D02 §3.3 の全順序が壊れる）。
        clock = self._clock
        if clock is None or clock.decision_time != decision_time:  # pragma: no cover - 防御
            clock = PhaseClock(self._phases, decision_time)
        detail = failure.reason.detail if failure.reason.code is ReasonCode.DATA_ERROR else None
        cancel_reason = Reason(ReasonCode.DATA_ERROR, detail)
        for order in self._context.ledger.pending_orders():
            event = OrderEvent(
                event_id=self._allocator.next(EventId),
                order_id=order.order_id,
                from_status=OrderStatus.PENDING,
                to_status=OrderStatus.CANCELED,
                at=clock.next(failure.phase),
                reason=cancel_reason,
            )
            self._context.ledger = commit_order_termination(self._context.ledger, event=event)
            self._emit(TraceTable.ORDER_EVENTS, event)

    # --- 1つの判断時点 -------------------------------------------------------

    def _decision_point(
        self, decision_time: UtcTime, events: _DecisionEvents, *, is_run_end: bool
    ) -> None:
        clock = PhaseClock(self._phases, decision_time)
        # 失敗したときの取消も同じ時計で刻む（`_fail` が読む）。
        self._clock = clock

        self._phase_bar_complete(clock, events)
        self._phase_ledger_update(clock)
        self._phase_order_expiry(clock)

        first = self._phase_publication(clock, events)
        proposals: tuple[EntryProposal, ...] = ()
        management: tuple[ManagementRequest, ...] = ()
        if first is not None:
            proposals = first.proposals
            management = first.management_requests

        notices = self._phase_admission(clock, proposals, management, is_run_end=is_run_end)

        opened: list[RuntimeEventNotice] = []
        if not is_run_end and events.execution_open is not None:
            opened = self._phase_execution_open(clock, events.execution_open)

        post = self._phase_post_fill_evaluation(
            clock, notices, tuple(opened), is_run_end=is_run_end
        )
        if post is not None:
            self._phase_post_fill_admission(clock, post, is_run_end=is_run_end)

        if is_run_end:
            self._phase_run_end(clock)

    # --- rank 0: 執行足の終了 ------------------------------------------------

    def _phase_bar_complete(self, clock: PhaseClock, events: _DecisionEvents) -> None:
        if events.bar_complete is None:
            return
        bar = self._execution.bar(events.bar_complete)
        if bar is None:
            raise _RunFailure(
                self._data_error(events.bar_complete, "execution bar is missing"),
                PHASE_EXECUTION_BAR_COMPLETE,
            )
        self._last_complete = bar
        for position in self._context.ledger.open_positions():
            if position.protection.effective_from.bar_start > bar.bar_start:
                # その足の開始前に有効だった保護水準だけを判定の対象にする（D06 §7.3）。
                continue
            self._resolve_protection(clock, position, bar)

    def _resolve_protection(self, clock: PhaseClock, position: Position, bar: Bar) -> None:
        fill_id = self._allocator.next(FillId)
        resolution = resolve_intrabar(
            position_id=position.position_id,
            side=position.side,
            protection=position.protection,
            parent=bar,
            hierarchy=self._execution_policy.resolution_hierarchy,
            spread_model=self._cost_model.spread_model,
            fill_id=fill_id,
            child_bars=self._child_bars(),
        )
        if resolution is None:
            return
        if resolution.method is not ResolutionMethod.SINGLE_HIT:
            self._conflicts += 1
            if resolution.method is ResolutionMethod.UNRESOLVED_SL_PRIORITY:
                self._unresolved += 1
        level = (
            position.protection.stop_loss
            if resolution.verdict is CloseCause.STOP_LOSS
            else position.protection.take_profit
        )
        if level is None:  # pragma: no cover - 利確が無ければ利確には触れない
            raise KernelValueError("a take profit verdict requires a take profit level")
        self._engine_close(
            clock,
            position=position,
            cause=resolution.verdict,
            phase=PHASE_EXECUTION_BAR_COMPLETE,
            base_price=level,
            spread_applied=False,
            execution_time=BarExecutionInterval(bar_key=bar.key, interval=bar.interval),
            eligibility=ProtectionHit(
                position_id=position.position_id,
                protection_version=position.protection.version,
                execution_bar_key=bar.key,
            ),
            fill_id=fill_id,
        )
        self._emit(TraceTable.INTRABAR_RESOLUTIONS, resolution)

    def _child_bars(self) -> ChildBars | None:
        if self._intrabar is None:
            return None
        series = self._intrabar
        return lambda child, interval: series.bars_in(child, interval)

    # --- rank 1: 台帳更新 ----------------------------------------------------

    def _phase_ledger_update(self, clock: PhaseClock) -> None:
        at = clock.next(PHASE_LEDGER_UPDATE)
        self._record_snapshot(at)

    def _record_snapshot(self, at: ProcessingPoint) -> None:
        equity = self._equity(at.phase.name)
        self._context.equity = equity
        snapshot: LedgerSnapshot = snapshot_of(self._context.ledger, at, equity)
        self._emit(TraceTable.LEDGER_SNAPSHOTS, snapshot)
        # 根拠記録が「どの時点の口座 snapshot を見たか」を指せるようにする。
        self._last_snapshot_at = at

    def _equity(self, phase: str) -> Money:
        """含み損益込みの資産（D06 §8.1、Q13 決定）。"""
        ledger = self._context.ledger
        equity = ledger.balance
        positions = ledger.open_positions()
        if not positions:
            return equity
        if self._last_complete is None:
            raise _RunFailure(
                Reason(
                    ReasonCode.DATA_ERROR,
                    DataErrorDetail(
                        symbol=self._config.execution_series.symbol,
                        timeframe=self._config.execution_series.timeframe,
                        field="close",
                        expected_interval=None,
                        observed_interval=None,
                        cause="no completed execution bar is available to value open positions",
                    ),
                ),
                phase,
            )
        for position in positions:
            equity = equity + unrealized(
                position,
                self._last_complete.close,
                self._cost_model.spread_model,
                ledger.currency,
            )
        return equity

    # --- rank 2: 期限 --------------------------------------------------------

    def _phase_order_expiry(self, clock: PhaseClock) -> None:
        decision_time = clock.decision_time
        for order in self._context.ledger.pending_orders():
            if order.expires_at > decision_time:
                continue
            event = OrderEvent(
                event_id=self._allocator.next(EventId),
                order_id=order.order_id,
                from_status=OrderStatus.PENDING,
                to_status=OrderStatus.EXPIRED,
                at=clock.next(PHASE_ORDER_EXPIRY),
                reason=Reason(ReasonCode.EXPIRED),
            )
            self._context.ledger = commit_order_termination(self._context.ledger, event=event)
            self._emit(TraceTable.ORDER_EVENTS, event)

    # --- rank 3〜9: 公開と第1回の評価 ----------------------------------------

    def _phase_publication(
        self, clock: PhaseClock, events: _DecisionEvents
    ) -> RuntimeStepResult | None:
        if not events.publications and not events.boundaries:
            return None
        closes = tuple(
            BarClosure(bar_key=key, interval=self._interval_of(key, end))
            for key, end in events.boundaries
        )
        batch = PublicationBatch(
            batch_id=self._allocator.next(EventId),
            decision_time=clock.decision_time,
            phases=self._phases,
            available_bars=events.publications,
            scheduled_closes=closes,
        )
        # 第1回の `step` は rank 4〜9 を担う。失敗したらその最後のフェーズで刻む。
        return self._step(batch, PHASE_P5_ORDER_INTENT)

    def _interval_of(self, bar_key: BarKey, bar_end: UtcTime) -> Interval:
        """足の実際の区間（名目の長さから再計算しない、D06 §4.3）。"""
        return Interval(start=bar_key.bar_start, end=bar_end)

    def _step(self, batch: PublicationBatch, phase: str) -> RuntimeStepResult:
        """戦略ランタイムを1回呼び、記録を判断履歴へ移す（D06 §4.2）。"""
        result = self._runtime.step(batch)
        for record in self._sink.drain():
            # 出力から評価へ辿れるようにしておく（根拠記録の `evaluation_ids`）。
            self._output_evaluations[record.output_id] = record.evaluation_id
            self._emit(TraceTable.OUTPUTS, record)
        for evaluation in result.evaluations:
            self._emit(TraceTable.EVALUATIONS, evaluation)
        for transition in result.transitions:
            self._opportunities.add(transition.opportunity_id)
            self._emit(TraceTable.OPPORTUNITY_TRANSITIONS, transition)
        if any(isinstance(record.outcome, Failed) for record in result.evaluations):
            # 失敗した `step` の提案と管理要求は一切使わない（D06 §4.2）。
            failed = next(
                record for record in result.evaluations if isinstance(record.outcome, Failed)
            )
            outcome = failed.outcome
            assert isinstance(outcome, Failed)  # noqa: S101 - 直前の絞り込みが保証する
            raise _RunFailure(outcome.reason, phase)
        return result

    # --- rank 10: 受付 -------------------------------------------------------

    def _phase_admission(
        self,
        clock: PhaseClock,
        proposals: Sequence[EntryProposal],
        management: Sequence[ManagementRequest],
        *,
        is_run_end: bool,
    ) -> tuple[AdmissionNotice, ...]:
        payloads: list[OrderPayload] = []
        for proposal in proposals:
            payloads.append(
                build_entry_payload(
                    proposal,
                    compiled_ref=self._compiled.compiled_ref,
                    exit_instance_id=self._exit_instance_id(),
                    default_valid_for=self._execution_policy.entry_valid_for,
                )
            )
        for request in management:
            if isinstance(request.action, ClosePosition):
                payloads.append(
                    build_close_payload(
                        request, close_valid_for=self._execution_policy.close_valid_for
                    )
                )
        if not payloads:
            return ()
        return self._admit(
            clock,
            order_payloads(payloads, strategy_priority=self._priority),
            phase=PHASE_ADMISSION,
            is_run_end=is_run_end,
        )

    def _exit_instance_id(self) -> str | None:
        exit_ref = self._compiled.roles.exit
        return None if exit_ref is None else exit_ref.instance_id

    def _admit(
        self,
        clock: PhaseClock,
        payloads: Sequence[OrderPayload],
        *,
        phase: str,
        is_run_end: bool,
    ) -> tuple[AdmissionNotice, ...]:
        """全順序に並んだ要求を1件ずつ逐次確定する（D06 §6.3）。"""
        notices: list[AdmissionNotice] = []
        for payload in payloads:
            outcome = self._admit_one(clock, payload, phase=phase, is_run_end=is_run_end)
            if not isinstance(payload, EntryRequest):
                # 決済要求は取引機会を持たないので通知しない（D06 §6.6）。
                continue
            decision = outcome.decision
            notices.append(
                AdmissionNotice(
                    opportunity_id=payload.opportunity_id,
                    attempt_id=decision.attempt_id,
                    accepted=isinstance(decision, AttemptAccepted),
                    reason=None if isinstance(decision, AttemptAccepted) else decision.reason,
                )
            )
        return tuple(notices)

    def _admit_one(
        self, clock: PhaseClock, payload: OrderPayload, *, phase: str, is_run_end: bool
    ) -> AdmissionOutcome:
        decision_time = clock.decision_time
        origin = RequestOrigin.STRATEGY
        evidence_ref = self._evidence(
            clock.peek(phase),
            EvidenceKind.ORDER_REQUEST,
            output_ids=entry_output_ids(payload),
            market_refs=self._reference_market_refs(),
        )
        request = build_request(
            payload,
            allocator=self._allocator,
            run_id=self._allocator.run_id,
            account_id=self._config.account.account_id,
            strategy_id=self._compiled.strategy_ref.strategy_id,
            created_at=clock.next(phase),
            origin=origin,
            evidence_ref=evidence_ref,
        )
        self._emit(TraceTable.ORDER_REQUESTS, request)
        if isinstance(payload, EntryRequest):
            self._entry_opportunities[request.attempt_id] = payload.opportunity_id
        expires_at = decision_time + payload.valid_for
        candidate, carry = self._candidate(decision_time, expires_at)
        run_end = self._config.run_interval.end if is_run_end else None
        accepted_at = clock.next(phase)

        if isinstance(payload, EntryRequest):
            quote, decision_bid = self._reference_quote(payload.side)
            conversion = self._conversion(decision_time)
            outcome = decide_entry(
                request,
                ledger=self._context.ledger,
                allocator=self._allocator,
                accepted_at=accepted_at,
                expires_at=expires_at,
                candidate=candidate,
                execution_series=self._config.execution_series,
                execution_policy_ref=self._config.execution_policy_ref,
                risk_policy=self._risk_policy,
                risk_policy_ref=self._config.risk_policy_ref,
                cost_model=self._cost_model,
                symbol_spec=self._symbol_spec,
                reference_quote=quote,
                decision_bid=decision_bid,
                adverse_fill_limit=self._execution_policy.adverse_fill_limit(payload.symbol),
                conversion=conversion,
                new_assessment_id=lambda: self._allocator.next(EvidenceId),
                evidence_ref=evidence_ref,
                run_end=run_end,
                carry_not_allowed=carry,
            )
        else:
            outcome = decide_close(
                request,
                ledger=self._context.ledger,
                allocator=self._allocator,
                accepted_at=accepted_at,
                expires_at=expires_at,
                eligibility=candidate,
                execution_series=self._config.execution_series,
                execution_policy_ref=self._config.execution_policy_ref,
                evidence_ref=evidence_ref,
                run_end=run_end,
                carry_not_allowed=carry,
            )
        self._record_outcome(outcome, clock, phase)
        return outcome

    def _record_outcome(self, outcome: AdmissionOutcome, clock: PhaseClock, phase: str) -> None:
        self._emit(TraceTable.ATTEMPT_DECISIONS, _decision_row(outcome.decision))
        if outcome.assessment is not None:
            self._emit(TraceTable.RISK_ASSESSMENTS, outcome.assessment)
            self._evidence(
                clock.peek(phase),
                EvidenceKind.ADMISSION,
                attempt_id=outcome.assessment.attempt_id,
                evidence_id=outcome.assessment.assessment_id,
                conversion_paths=(self._identity_path(clock.decision_time),),
                policy_refs=(self._config.risk_policy_ref, self._config.execution_policy_ref),
                market_refs=self._reference_market_refs(),
            )
        if not outcome.accepted or outcome.order is None or outcome.acceptance_event is None:
            return
        ledger = self._context.ledger
        if outcome.reservation is not None:
            self._context.ledger = commit_entry_acceptance(
                ledger,
                order=outcome.order,
                event=outcome.acceptance_event,
                reservation=outcome.reservation,
            )
        else:
            self._context.ledger = commit_close_acceptance(
                ledger, order=outcome.order, event=outcome.acceptance_event
            )
        self._emit(TraceTable.ORDERS, outcome.order)
        self._emit(TraceTable.ORDER_EVENTS, outcome.acceptance_event)

    def _candidate(
        self, decision_time: UtcTime, expires_at: UtcTime
    ) -> tuple[ScheduledOpen | None, CarryNotAllowedDetail | None]:
        """候補の始値を固定する（D06 §5.3・§7.1）。

        `entry_delay_bars` のぶんだけ最初の適格 open を見送る。候補はカレンダーと執行系列の
        足スケジュールから決め、将来価格や実ファイルの欠損を候補選択に使わない。
        """
        key = self._first_candidate_key(decision_time)
        for _ in range(self._execution_policy.entry_delay_bars):
            if key is None:
                break
            key = self._execution.next_bar_key_after(key.bar_start)
        if key is None:
            return None, None
        open_time = key.bar_start
        carry = self._carry_not_allowed(decision_time, open_time)
        if carry is not None:
            return None, carry
        if open_time >= expires_at:
            return None, None
        return ScheduledOpen(bar_key=key, open_time=open_time), None

    def _first_candidate_key(self, decision_time: UtcTime) -> BarKey | None:
        """因果順序上まだ到来していない最初の執行足（判断時点と同時刻の足を含む）。"""
        key = self._execution.next_bar_key_after(decision_time - _TINY)
        return key

    def _carry_not_allowed(
        self, decision_time: UtcTime, open_time: UtcTime
    ) -> CarryNotAllowedDetail | None:
        """候補が週末休場をまたぐか（D06 §5.3）。UTC の土日判定には置き換えない。"""
        if open_time <= decision_time:
            return None
        window = Interval(start=decision_time, end=open_time)
        for session in self._calendar.sessions(window):
            if session.start <= decision_time and open_time <= session.end:
                return None
        return CarryNotAllowedDetail(
            next_candidate=open_time, session_close=self._last_session_close(decision_time)
        )

    def _last_session_close(self, moment: UtcTime) -> UtcTime:
        """`moment` 以前で最後に観測できる週の終わり（診断に載せる）。"""
        window = Interval(start=moment - _ONE_WEEK, end=moment)
        closes = [
            session.end for session in self._calendar.sessions(window) if session.end <= moment
        ]
        return closes[-1] if closes else moment

    def _reference_quote(self, side: OrderSide) -> tuple[ReferenceQuote | None, Price | None]:
        """受付時の参照価格（D06 §6.4 の手順3、Q10 決定）。

        直前に完了した執行足の終値（bid）から取る。**買いは ask、売りは bid** であり
        （上位設計書 §4.7.9 C）、bid のみの系列では買いのときだけ spread モデルで ask を
        導く。戻り値の2つ目はその足の bid そのもので、保護水準の妥当性検査（手順4）が
        売却側・購入側の価格を作るのに使う。

        戦略向けのビューは期待足が未到着なら古い足へ戻らないため、そこからは引けない。
        """
        bar = self._last_complete
        if bar is None:
            return None, None
        bid = bar.close
        derived = side is OrderSide.BUY
        price = self._cost_model.spread_model.ask_from_bid(bid) if derived else bid
        return (
            ReferenceQuote(
                price=price,
                basis=PriceBasis.ASK if derived else PriceBasis.BID,
                observed_at=bar.interval.end,
                derived_from_spread=derived,
                source_bar=bar.key,
            ),
            bid,
        )

    def _identity_path(self, at: UtcTime) -> ConversionPath:
        return resolve_path(
            self._config.account.currency,
            self._config.account.currency,
            at,
            self._conversion_policy,
        )

    def _conversion(self, at: UtcTime) -> ConversionRate:
        """決済通貨から口座通貨への換算率（段階2は恒等換算）。"""
        currency = self._config.account.currency
        try:
            path = resolve_path(currency, currency, at, self._conversion_policy)
        except ConversionUnavailable as error:  # pragma: no cover - 段階2は恒等換算のみ
            raise _RunFailure(Reason(ReasonCode.DATA_ERROR), PHASE_ADMISSION) from error
        return rate_of(path, currency, currency)

    # --- rank 11: 始値処理 ---------------------------------------------------

    def _phase_execution_open(self, clock: PhaseClock, bar_key: BarKey) -> list[RuntimeEventNotice]:
        open_price = self._execution.open_of(bar_key)
        if open_price is None:
            raise _RunFailure(
                self._data_error(bar_key, "execution bar open is missing"), PHASE_EXECUTION_OPEN
            )

        # 手順1: 既存建玉の gap 決済。
        for position in self._context.ledger.open_positions():
            if position.protection.effective_from.bar_start > bar_key.bar_start:
                continue
            if gap_breaches_stop(
                position.side, position.protection, open_price, self._cost_model.spread_model
            ):
                self._close_at_open(clock, position, bar_key, open_price, CloseCause.STOP_LOSS)

        opened: list[RuntimeEventNotice] = []
        # 手順2・3: 適格な成行注文の約定と建玉の初期化。
        for order in self._context.ledger.pending_orders():
            eligibility = order.execution.eligibility
            if not isinstance(eligibility, ScheduledOpen) or eligibility.bar_key != bar_key:
                continue
            if order.is_entry:
                notice = self._fill_entry(clock, order, bar_key, open_price)
                if notice is not None:
                    opened.append(notice)
            else:
                self._fill_strategy_close(clock, order, bar_key, open_price)
        self._record_snapshot(clock.next(PHASE_EXECUTION_OPEN))
        return opened

    def _fill_entry(
        self, clock: PhaseClock, order: AcceptedOrder, bar_key: BarKey, open_price: Price
    ) -> RuntimeEventNotice | None:
        terms = order.terms
        if not isinstance(terms, AcceptedEntryTerms):  # pragma: no cover - is_entry が保証する
            raise KernelValueError("an entry fill requires entry terms")
        price = fill_price(open_price, order.side, FillPurpose.ENTRY, self._cost_model)
        at = clock.next(PHASE_EXECUTION_OPEN)
        position_id = self._allocator.next(PositionId)
        fill_id = self._allocator.next(FillId)
        event_id = self._allocator.next(EventId)
        conversion = self._conversion(clock.decision_time)
        costs = cost_entries(
            self._cost_model,
            purpose=FillPurpose.ENTRY,
            side=order.side,
            quantity=order.quantity,
            currency=self._config.account.currency,
            conversion=conversion,
        )
        fill = FillRecord(
            run_id=order.run_id,
            fill_id=fill_id,
            event_id=event_id,
            order_id=order.order_id,
            position_id=position_id,
            processed_at=at,
            execution_time=ExactExecutionTime(time=bar_key.bar_start),
            price=price,
            quantity=order.quantity,
            costs=costs,
            evidence_ref=self._evidence(
                at,
                EvidenceKind.FILL,
                position_id=position_id,
                conversion_paths=(self._identity_path(at.time),),
                policy_refs=(self._config.execution_policy_ref, self._config.cost_model_ref),
                market_refs=self._fill_market_refs(bar_key, MarketDataField.OPEN),
            ),
        )
        protection = ProtectionState(
            version=1,
            stop_loss=terms.initial_stop,
            effective_from=bar_key,
            owner_instance_id=terms.exit_plan_ref.exit_instance_id,
        )
        position = Position(
            position_id=position_id,
            account_id=self._config.account.account_id,
            strategy_id=self._compiled.strategy_ref.strategy_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            entry_fill_id=fill_id,
            entry_price=price,
            opened_at=at,
            protection=protection,
        )
        allocation = PositionRiskAllocation(
            allocation_id=self._allocator.next(AllocationId),
            position_id=position_id,
            source_reservation_id=terms.reservation_id,
            amount=self._context.ledger.reservations[terms.reservation_id].amount,
            created_event_id=event_id,
        )
        event = OrderEvent(
            event_id=event_id,
            order_id=order.order_id,
            from_status=OrderStatus.PENDING,
            to_status=OrderStatus.FILLED,
            at=at,
            fill_id=fill_id,
        )
        balance = self._apply_costs(self._context.ledger.balance, costs)
        self._context.ledger = commit_entry_fill(
            self._context.ledger,
            order=order,
            event=event,
            position=position,
            allocation=allocation,
            balance=balance,
        )
        self._measurements[position_id] = self._risk_measurement(position, allocation, at)
        self._emit(TraceTable.FILLS, fill)
        self._emit(TraceTable.ORDER_EVENTS, event)

        # 手順4: 約定直後の gap は損切り到達として処理する（緊急決済に分類しない）。
        if gap_breaches_stop(position.side, protection, open_price, self._cost_model.spread_model):
            self._close_at_open(clock, position, bar_key, open_price, CloseCause.STOP_LOSS)
            return None
        # 手順5: 約定ずれ超過なら緊急決済。
        limit = self._execution_policy.adverse_fill_limit(order.symbol)
        if limit is not None and needs_emergency_close(
            price, terms.reference_quote.price, order.side, limit
        ):
            self._emergency_close(clock, position, bar_key, open_price, fill_id)
            return None
        opportunity_id = self._opportunity_of(order)
        if opportunity_id is None:  # pragma: no cover - エントリーは必ず機会を持つ
            return None
        return RuntimeEventNotice(
            kind=RuntimeEventKind.POSITION_OPENED,
            position_id=position_id,
            opportunity_id=opportunity_id,
        )

    def _opportunity_of(self, order: AcceptedOrder) -> OpportunityId | None:
        """約定した注文の発端になった取引機会（D06 §4.2）。

        受付済み注文から `attempt_id` で要求を辿る。通知はどの機会から生まれた建玉かを
        判断履歴に残すためのもので、既に終端した機会の状態を再び変えるものではない。
        """
        return self._entry_opportunities.get(order.attempt_id)

    def _risk_measurement(
        self, position: Position, allocation: PositionRiskAllocation, at: ProcessingPoint
    ) -> RiskMeasurement:
        with localcontext(kernel_context()):
            span = (
                position.entry_price.value - position.protection.stop_loss.value
                if position.side is OrderSide.BUY
                else position.protection.stop_loss.value - position.entry_price.value
            )
            measured = Money(span * position.quantity.units, self._config.account.currency)
        return RiskMeasurement(
            position_id=position.position_id,
            at=at,
            measured=measured,
            allocated=allocation.amount,
            basis="entry_price - initial_stop",
        )

    def _apply_costs(self, balance: Money, costs: Sequence[CostEntry]) -> Money:
        """残高に反映する費用だけを差し引く（D06 §7.6）。

        価格に反映済みの滑りと提示価格の幅は**金額として控除しない**（二重計上になる）。
        集計のための合計だけは区分ごとに貯める。
        """
        updated = balance
        for entry in costs:
            self._cost_totals[entry.kind] = (
                self._cost_totals.get(entry.kind, self._zero()) + entry.account
            )
            if entry.kind.reflected_in_balance:
                updated = updated - entry.account
        return updated

    def _zero(self) -> Money:
        return Money(_ZERO, self._config.account.currency)

    def _fill_strategy_close(
        self, clock: PhaseClock, order: AcceptedOrder, bar_key: BarKey, open_price: Price
    ) -> None:
        terms = order.terms
        if not isinstance(terms, AcceptedCloseTerms):  # pragma: no cover
            raise KernelValueError("a close fill requires close terms")
        position = self._context.ledger.positions.get(terms.position_id)
        if position is None or not position.is_open:
            # 対象建玉が先に閉じた決済注文は取り消す（D06 §5.1 の遷移6）。
            at = clock.next(PHASE_EXECUTION_OPEN)
            event = OrderEvent(
                event_id=self._allocator.next(EventId),
                order_id=order.order_id,
                from_status=OrderStatus.PENDING,
                to_status=OrderStatus.CANCELED,
                at=at,
                reason=Reason(
                    ReasonCode.POSITION_CLOSED,
                    PositionClosedDetail(position_id=terms.position_id, closed_at=at),
                ),
            )
            self._context.ledger = commit_order_termination(self._context.ledger, event=event)
            self._emit(TraceTable.ORDER_EVENTS, event)
            return
        price = fill_price(open_price, order.side, FillPurpose.CLOSE, self._cost_model)
        at = clock.next(PHASE_EXECUTION_OPEN)
        self._settle(
            order=order,
            position=position,
            price=price,
            at=at,
            execution_time=ExactExecutionTime(time=bar_key.bar_start),
            spread_applied=True,
        )

    def _close_at_open(
        self,
        clock: PhaseClock,
        position: Position,
        bar_key: BarKey,
        open_price: Price,
        cause: CloseCause,
    ) -> None:
        """始値が保護水準を飛び越えた場合の決済（D06 §7.5 の手順1・4）。"""
        side = OrderSide.SELL if position.side is OrderSide.BUY else OrderSide.BUY
        base, spread_applied = protection_base_price(
            side, position.protection.stop_loss, open_price, self._cost_model.spread_model
        )
        self._engine_close(
            clock,
            position=position,
            cause=cause,
            phase=PHASE_EXECUTION_OPEN,
            base_price=base,
            spread_applied=spread_applied,
            execution_time=ExactExecutionTime(time=bar_key.bar_start),
            eligibility=ProtectionHit(
                position_id=position.position_id,
                protection_version=position.protection.version,
                execution_bar_key=bar_key,
            ),
            fill_id=self._allocator.next(FillId),
        )

    def _emergency_close(
        self,
        clock: PhaseClock,
        position: Position,
        bar_key: BarKey,
        open_price: Price,
        trigger_fill_id: FillId,
    ) -> None:
        """約定ずれ超過による緊急決済（D06 §7.5 の手順5）。"""
        self._engine_close(
            clock,
            position=position,
            cause=CloseCause.EMERGENCY,
            phase=PHASE_EXECUTION_OPEN,
            base_price=open_price,
            spread_applied=True,
            execution_time=ExactExecutionTime(time=bar_key.bar_start),
            eligibility=ImmediateAfterFill(
                trigger_fill_id=trigger_fill_id, open_event_id=self._allocator.next(EventId)
            ),
            fill_id=self._allocator.next(FillId),
        )

    def _engine_close(
        self,
        clock: PhaseClock,
        *,
        position: Position,
        cause: CloseCause,
        phase: str,
        base_price: Price,
        spread_applied: bool,
        execution_time: ExecutionTime,
        eligibility: ProtectionHit | ImmediateAfterFill,
        fill_id: FillId,
    ) -> None:
        """エンジンが生成する即時決済（D06 §4.4・§6.2・§7.3・§7.5）。

        受付と約定を**1つの差し替え**にまとめる。候補の始値を探す規則は通さない。
        """
        decision_time = clock.decision_time
        side = OrderSide.SELL if position.side is OrderSide.BUY else OrderSide.BUY
        payload = CloseRequest(
            position_id=position.position_id,
            cause=cause,
            valid_for=self._execution_policy.close_valid_for,
        )
        evidence_ref = self._evidence(
            clock.peek(phase), EvidenceKind.ORDER_REQUEST, position_id=position.position_id
        )
        request = build_request(
            payload,
            allocator=self._allocator,
            run_id=self._allocator.run_id,
            account_id=self._config.account.account_id,
            strategy_id=self._compiled.strategy_ref.strategy_id,
            created_at=clock.next(phase),
            origin=RequestOrigin.ENGINE,
            evidence_ref=evidence_ref,
        )
        self._emit(TraceTable.ORDER_REQUESTS, request)
        outcome = decide_close(
            request,
            ledger=self._context.ledger,
            allocator=self._allocator,
            accepted_at=clock.next(phase),
            expires_at=decision_time + payload.valid_for,
            eligibility=eligibility,
            execution_series=self._config.execution_series,
            execution_policy_ref=self._config.execution_policy_ref,
            evidence_ref=evidence_ref,
        )
        self._emit(TraceTable.ATTEMPT_DECISIONS, _decision_row(outcome.decision))
        if not outcome.accepted or outcome.order is None or outcome.acceptance_event is None:
            return
        self._emit(TraceTable.ORDERS, outcome.order)
        self._emit(TraceTable.ORDER_EVENTS, outcome.acceptance_event)
        price = fill_price(
            base_price,
            side,
            FillPurpose.CLOSE,
            self._cost_model,
            use_spread=spread_applied,
        )
        self._settle(
            order=outcome.order,
            position=position,
            price=price,
            at=clock.next(phase),
            execution_time=execution_time,
            spread_applied=spread_applied,
            acceptance_event=outcome.acceptance_event,
            fill_id=fill_id,
        )

    def _settle(
        self,
        *,
        order: AcceptedOrder,
        position: Position,
        price: Price,
        at: ProcessingPoint,
        execution_time: ExecutionTime,
        spread_applied: bool,
        acceptance_event: OrderEvent | None = None,
        fill_id: FillId | None = None,
    ) -> None:
        """決済の約定を確定する（D06 §4.4 の確定単位4・5）。"""
        fill_id = self._allocator.next(FillId) if fill_id is None else fill_id
        event_id = self._allocator.next(EventId)
        conversion = self._conversion(at.time)
        costs = cost_entries(
            self._cost_model,
            purpose=FillPurpose.CLOSE,
            side=order.side,
            quantity=order.quantity,
            currency=self._config.account.currency,
            conversion=conversion,
            spread_applied=spread_applied,
        )
        fill = FillRecord(
            run_id=order.run_id,
            fill_id=fill_id,
            event_id=event_id,
            order_id=order.order_id,
            position_id=position.position_id,
            processed_at=at,
            execution_time=execution_time,
            price=price,
            quantity=order.quantity,
            costs=costs,
            evidence_ref=self._evidence(
                at,
                EvidenceKind.FILL,
                position_id=position.position_id,
                conversion_paths=(self._identity_path(at.time),),
                policy_refs=(self._config.execution_policy_ref, self._config.cost_model_ref),
                market_refs=self._execution_market_refs(execution_time),
            ),
        )
        with localcontext(kernel_context()):
            direction = decimal_from_int(1 if position.side is OrderSide.BUY else -1)
            gross = Money(
                (price.value - position.entry_price.value) * position.quantity.units * direction,
                self._config.account.currency,
            )
        balance = self._apply_costs(self._context.ledger.balance + gross, costs)
        commission = next(
            (entry.account for entry in costs if entry.kind is CostKind.COMMISSION), self._zero()
        )
        closed = Position(
            position_id=position.position_id,
            account_id=position.account_id,
            strategy_id=position.strategy_id,
            symbol=position.symbol,
            side=position.side,
            quantity=position.quantity,
            entry_fill_id=position.entry_fill_id,
            entry_price=position.entry_price,
            opened_at=position.opened_at,
            protection=position.protection,
            status=PositionStatus.CLOSED,
            close_fill_id=fill_id,
            realized=gross - commission,
        )
        allocation = self._allocation_for(position.position_id)
        released = None
        if allocation is not None:
            released = PositionRiskAllocation(
                allocation_id=allocation.allocation_id,
                position_id=allocation.position_id,
                source_reservation_id=allocation.source_reservation_id,
                amount=allocation.amount,
                created_event_id=allocation.created_event_id,
                released_event_id=event_id,
            )
        fill_event = OrderEvent(
            event_id=event_id,
            order_id=order.order_id,
            from_status=OrderStatus.PENDING,
            to_status=OrderStatus.FILLED,
            at=at,
            fill_id=fill_id,
        )
        if released is None:  # pragma: no cover - 段階2は必ず割当がある
            raise KernelValueError("a close fill must release the position's risk allocation")
        if acceptance_event is None:
            self._context.ledger = commit_close_fill(
                self._context.ledger,
                order=order,
                event=fill_event,
                position=closed,
                allocation=released,
                balance=balance,
            )
        else:
            self._context.ledger = commit_immediate_close(
                self._context.ledger,
                order=order,
                acceptance_event=acceptance_event,
                fill_event=fill_event,
                position=closed,
                allocation=released,
                balance=balance,
            )
        self._trade_count += 1
        self._emit(TraceTable.FILLS, fill)
        self._emit(TraceTable.ORDER_EVENTS, fill_event)

    def _allocation_for(self, position_id: PositionId) -> PositionRiskAllocation | None:
        for allocation in self._context.ledger.allocations.values():
            if allocation.position_id == position_id and not allocation.is_released:
                return allocation
        return None

    # --- rank 12: 約定後の評価 -----------------------------------------------

    def _phase_post_fill_evaluation(
        self,
        clock: PhaseClock,
        notices: Sequence[AdmissionNotice],
        opened: Sequence[RuntimeEventNotice],
        *,
        is_run_end: bool,
    ) -> RuntimeStepResult | None:
        if not notices and not opened:
            return None
        batch = PublicationBatch(
            batch_id=self._allocator.next(EventId),
            decision_time=clock.decision_time,
            phases=self._phases,
            runtime_events=tuple(opened),
            admissions=tuple(notices),
        )
        result = self._step(batch, PHASE_POST_FILL_EVALUATION)
        # 【未実装・要決定】同じ建玉への保護水準の更新と決済要求が同時に返ったら、決済を
        # 優先して更新は理由を記録して破棄する（上位設計書 §4.7.6、D06 §8.3）。破棄の理由
        # コードが D02 §8.1 の語彙に無いため、語を決めるまで実装しない。検証戦略 A は両方を
        # 同時に返さないので、段階2の実行では起きない。
        for request in result.management_requests:
            if isinstance(request.action, SetTakeProfit):
                self._apply_take_profit(clock, request, is_run_end=is_run_end)
        return result

    def _apply_take_profit(
        self, clock: PhaseClock, request: ManagementRequest, *, is_run_end: bool = False
    ) -> None:
        """初期の利確を建玉へ適用する（D06 §8.3）。

        run 末尾では保護水準の更新を**記録するが適用しない**（D06 §10.1 の手順3 の表、
        上位設計書 §4.7.13 D）。終了だから未公開の判断を建玉へ反映することはしない。
        """
        at = clock.next(PHASE_POST_FILL_EVALUATION)
        position = self._context.ledger.positions.get(request.position_id)
        action = request.action
        if not isinstance(action, SetTakeProfit):  # pragma: no cover - 呼び出し側が絞る
            return
        if is_run_end:
            self._emit(
                TraceTable.MANAGEMENT_APPLICATIONS,
                CompositeRow(
                    primary=request,
                    parts=(
                        (
                            "application",
                            ManagementApplication,
                            ManagementApplication(
                                at=at,
                                applied=False,
                                reason=Reason(
                                    ReasonCode.RUN_END,
                                    RunEndDetail(run_end=self._config.run_interval.end),
                                ),
                            ),
                        ),
                    ),
                ),
            )
            return
        if position is None or not position.is_open:
            self._emit(
                TraceTable.MANAGEMENT_APPLICATIONS,
                CompositeRow(
                    primary=request,
                    parts=(
                        (
                            "application",
                            ManagementApplication,
                            ManagementApplication(
                                at=at,
                                applied=False,
                                reason=Reason(
                                    ReasonCode.POSITION_CLOSED,
                                    PositionClosedDetail(
                                        position_id=request.position_id, closed_at=at
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            )
            return
        direction = (
            RoundingDirection.DOWN if position.side is OrderSide.BUY else RoundingDirection.UP
        )
        rounded = action.price.round_to_tick(self._symbol_spec.price_tick, direction)
        valid = (
            position.entry_price < rounded
            if position.side is OrderSide.BUY
            else rounded < position.entry_price
        )
        if not valid:
            application = ManagementApplication(
                at=at, applied=False, reason=Reason(ReasonCode.PROTECTION_INVALID)
            )
        else:
            protection = ProtectionState(
                version=position.protection.version + 1,
                stop_loss=position.protection.stop_loss,
                effective_from=position.protection.effective_from,
                take_profit=rounded,
                owner_instance_id=position.protection.owner_instance_id,
            )
            updated = Position(
                position_id=position.position_id,
                account_id=position.account_id,
                strategy_id=position.strategy_id,
                symbol=position.symbol,
                side=position.side,
                quantity=position.quantity,
                entry_fill_id=position.entry_fill_id,
                entry_price=position.entry_price,
                opened_at=position.opened_at,
                protection=protection,
                status=position.status,
            )
            self._context.ledger = self._context.ledger.committed(
                positions={updated.position_id: updated}
            )
            with localcontext(kernel_context()):
                risk = abs(position.entry_price.value - position.protection.stop_loss.value)
                reward = abs(rounded.value - position.entry_price.value)
                ratio: Decimal | None = reward / risk if risk != _ZERO else None
            application = ManagementApplication(
                at=at,
                applied=True,
                protection_version=protection.version,
                rounded_take_profit=rounded,
                realized_reward_risk=ratio,
            )
            self._evidence(
                at,
                EvidenceKind.PROTECTION_UPDATE,
                position_id=position.position_id,
                output_ids=(request.source_output_id,),
                policy_refs=(self._config.execution_policy_ref,),
            )
        self._emit(
            TraceTable.MANAGEMENT_APPLICATIONS,
            CompositeRow(
                primary=request,
                parts=(("application", ManagementApplication, application),),
            ),
        )

    # --- rank 13: 約定後の受付 -----------------------------------------------

    def _phase_post_fill_admission(
        self, clock: PhaseClock, result: RuntimeStepResult, *, is_run_end: bool
    ) -> None:
        payloads: list[OrderPayload] = [
            build_close_payload(request, close_valid_for=self._execution_policy.close_valid_for)
            for request in result.management_requests
            if isinstance(request.action, ClosePosition)
        ]
        if not payloads:
            return
        self._admit(
            clock,
            order_payloads(payloads, strategy_priority=self._priority),
            phase=PHASE_POST_FILL_ADMISSION,
            is_run_end=is_run_end,
        )

    # --- rank 14: 末尾処理 ---------------------------------------------------

    def _phase_run_end(self, clock: PhaseClock) -> None:
        """末尾の手順5〜7（D06 §10.1）。取消 → 機会の終端 → 最終 snapshot の順で行う。"""
        run_end = self._config.run_interval.end
        for order in self._context.ledger.pending_orders():
            event = OrderEvent(
                event_id=self._allocator.next(EventId),
                order_id=order.order_id,
                from_status=OrderStatus.PENDING,
                to_status=OrderStatus.CANCELED,
                at=clock.next(PHASE_RUN_END),
                reason=Reason(ReasonCode.RUN_END, RunEndDetail(run_end=run_end)),
            )
            self._context.ledger = commit_order_termination(self._context.ledger, event=event)
            self._emit(TraceTable.ORDER_EVENTS, event)

        batch = PublicationBatch(
            batch_id=self._allocator.next(EventId),
            decision_time=run_end,
            phases=self._phases,
            is_run_end=True,
        )
        result = self._runtime.step(batch)
        if result.outputs or result.evaluations or result.proposals or result.management_requests:
            raise KernelValueError(
                "the end-of-run step must not produce new judgements; only the terminal"
                " transitions of the remaining opportunities belong there (D06 §10.2)"
            )
        for transition in result.transitions:
            self._opportunities.add(transition.opportunity_id)
            self._emit(TraceTable.OPPORTUNITY_TRANSITIONS, transition)

        self._record_snapshot(clock.next(PHASE_RUN_END))
        self._summaries = self._final_summaries()

    def _final_summaries(self) -> FinalSummaries | None:
        """末尾の3集計（D06 §10.3）。最終評価価格が無ければ組み立てない。"""
        summaries = final_summaries(
            ledger=self._context.ledger,
            initial_balance=self._config.account.initial_balance,
            equity=self._context.equity,
            last_close=None if self._last_complete is None else self._last_complete.close,
            cost_model=self._cost_model,
            cost_totals=self._cost_totals,
        )
        if summaries is None:
            self._status = RunStatus.FAILED_DATA_ERROR
            self._failure = Reason(ReasonCode.DATA_ERROR)
        return summaries

    # --- 記録 ---------------------------------------------------------------

    def _emit(self, table: TraceTable, row: object) -> None:
        self._rows[table].append(row)

    def _finalize_tables(self) -> None:
        """主キーが1件ずつになる表を最終状態から作る（D06 §9.2）。

        予約（表10、主キー `reservation_id`）と建玉（表11、主キー `position_id`）は、
        状態が変わるたびに行を足すと同じ主キーの行が複数できてしまう。run の終わりに
        最終状態を1件ずつ書く。
        """
        ledger = self._context.ledger
        for reservation_id, reservation in sorted(
            ledger.reservations.items(), key=lambda item: item[0].seq
        ):
            self._emit(
                TraceTable.RESERVATIONS,
                CompositeRow(
                    primary=reservation,
                    parts=(
                        (
                            "reservation_state",
                            ReservationState,
                            ledger.reservation_states.get(reservation_id),
                        ),
                    ),
                ),
            )
        for position_id, position in sorted(ledger.positions.items(), key=lambda item: item[0].seq):
            allocation = next(
                (
                    value
                    for value in ledger.allocations.values()
                    if value.position_id == position_id
                ),
                None,
            )
            self._emit(
                TraceTable.POSITIONS,
                CompositeRow(
                    primary=position,
                    parts=(
                        ("position_risk_allocation", PositionRiskAllocation, allocation),
                        (
                            "risk_measurement",
                            RiskMeasurement,
                            self._measurements.get(position_id),
                        ),
                    ),
                ),
            )

    def _evidence(
        self,
        at: ProcessingPoint,
        kind: EvidenceKind,
        *,
        evidence_id: EvidenceId | None = None,
        output_ids: Sequence[OutputId] = (),
        attempt_id: AttemptId | None = None,
        position_id: PositionId | None = None,
        conversion_paths: Sequence[ConversionPath] = (),
        policy_refs: Sequence[PolicyRef] = (),
        market_refs: Sequence[MarketObservationRef] = (),
    ) -> EvidenceRef:
        """根拠記録を1件残し、その参照を返す（D06 §9.2 の表15）。

        上位設計書 §4.7.15 が根拠記録に求める内容（入力の出力 ID、市場データの snapshot・
        系列・区間・項目、読取時点、口座 snapshot、使用した設定の版）をすべて埋める。
        `evaluation_ids` は出力 ID から辿り、`ledger_snapshot_at` は直前に残した台帳
        snapshot の処理点を入れる。空のまま並べると、型としては在るのに中身が無い記録に
        なってしまう。
        """
        identifier = self._allocator.next(EvidenceId) if evidence_id is None else evidence_id
        record = EvidenceRecord(
            evidence_id=identifier,
            at=at,
            kind=kind,
            output_ids=tuple(output_ids),
            evaluation_ids=tuple(
                self._output_evaluations[output_id]
                for output_id in output_ids
                if output_id in self._output_evaluations
            ),
            attempt_id=attempt_id,
            position_id=position_id,
            market_refs=tuple(market_refs),
            conversion_paths=tuple(conversion_paths),
            ledger_snapshot_at=self._last_snapshot_at,
            policy_refs=tuple(policy_refs),
        )
        self._emit(TraceTable.EVIDENCE, record)
        return EvidenceRef(evidence_id=identifier)

    def _fill_market_refs(
        self, bar_key: BarKey, field: MarketDataField
    ) -> tuple[MarketObservationRef, ...]:
        """始値で約定したときの根拠（その執行足のどの項目を見たか）。"""
        bar = self._execution.bar(bar_key)
        return () if bar is None else (self._market_ref(bar, field),)

    def _execution_market_refs(
        self, execution_time: ExecutionTime
    ) -> tuple[MarketObservationRef, ...]:
        """決済の根拠（始値約定なら始値、足の中の到達なら高値と安値）。"""
        if isinstance(execution_time, ExactExecutionTime):
            key = self._execution.next_bar_key_after(execution_time.time - _TINY)
            return () if key is None else self._fill_market_refs(key, MarketDataField.OPEN)
        bar = self._execution.bar(execution_time.bar_key)
        if bar is None:  # pragma: no cover - 到達判定はその足を読んでいる
            return ()
        return (
            self._market_ref(bar, MarketDataField.HIGH),
            self._market_ref(bar, MarketDataField.LOW),
        )

    def _reference_market_refs(self) -> tuple[MarketObservationRef, ...]:
        """受付の根拠になった市場データ（直前に完了した執行足の終値、D06 §6.4 の手順3）。"""
        if self._last_complete is None:
            return ()
        return (self._market_ref(self._last_complete, MarketDataField.CLOSE),)

    def _market_ref(self, bar: Bar, field: MarketDataField) -> MarketObservationRef:
        """その足のどの項目を根拠にしたか（D06 §9.2 の `MarketObservationRef`）。"""
        return MarketObservationRef(
            snapshot_ref=self._config.snapshot_ref,
            series=bar.series,
            interval=bar.interval,
            field=field,
        )

    def _data_error(self, bar_key: BarKey, cause: str) -> Reason:
        return Reason(
            ReasonCode.DATA_ERROR,
            DataErrorDetail(
                symbol=bar_key.series.symbol,
                timeframe=bar_key.series.timeframe,
                field="ohlc",
                expected_interval=None,
                observed_interval=None,
                cause=cause,
            ),
        )


def _decision_row(decision: AttemptDecision) -> CompositeRow:
    """試行の結末を表5 の行にする（D06 §9.1 の規則2）。

    行そのものが区分タグ付き union なので、列は**全変種のフィールドの和集合**になる。
    受け付けた行でも拒否理由の列が（`None` として）並ぶようにする。
    """
    return CompositeRow(primary=decision, variants=(AttemptAccepted, AttemptRejected))
