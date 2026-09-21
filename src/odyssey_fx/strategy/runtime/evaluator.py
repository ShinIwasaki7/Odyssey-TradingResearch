"""戦略ランタイムの評価器（D05 §6・§7・§8）。

エンジンから渡された公開バッチ1件を処理して、出力・評価記録・発注提案・管理要求・取引機会
の遷移を返す。入口は `step` の1つだけである。

1回の `step` で行うこと（D05 §6.2）:

1. 受付結果の通知を適用する（遷移5〜7）。
2. 起動判定と評価要求の生成（足の確定は対象区間ごとに1件、イベントは1件につき1要求）。
3. 評価順に、起動した使用箇所だけを評価する。上流の更新だけで下流を自動評価しない。
4. 入力解決 → 部品の呼び出し → 戻り値の検査（付番の前）。
5. 取引機会の組み立てと同時保持の判定（付番より前。識別子の無い内容を判断履歴に残さない）。
6. 出力の付番と送出、状態の更新。
7. 終端しなかった機会のイベントだけを下流へ配送する。
8. 役割出力から発注提案と管理要求を組み立てる。

**失敗は例外ではなく戻り値で返す**（D05 §6.2）。入力欠損（`Error` 方針）も、戻り値の検査
違反も、部品の呼び出しが例外で終わった場合も、評価記録に失敗として残し、以降の評価を行わ
ずに返す。例外で抜けると、評価記録の唯一の公開経路が戻り値であるため、失敗の診断が判断
履歴から消える。run を終了させるのはエンジンの責務である。

**処理点は自分で組み立てず、エンジンから受け取った材料で作る**（D05 §6.1）。フェーズの順位
と全列挙は D06 の責務なので、ランタイムは**名前だけを要求**して公開バッチのフェーズ集合から
順位を引く。未登録の名前は構築時の誤りとして拒否する。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AttemptId,
    EvaluationId,
    EventId,
    IdAllocator,
    OpportunityId,
    OutputId,
    PositionId,
    RequestId,
)
from odyssey_fx.common.money import Price
from odyssey_fx.common.reason import MissingInputReason, Reason, ReasonCode
from odyssey_fx.common.time import Interval, PhaseRank, ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.strategy.catalog.registry import (
    ComponentImplementation,
    ComponentOutputs,
    ComponentRegistry,
    ContractKey,
    StatefulImplementation,
)
from odyssey_fx.strategy.compiler.compiled import (
    CompiledComponent,
    CompiledStrategy,
    InputPlan,
    ResolvedContextSource,
    ResolvedMarketSource,
    ResolvedOutputSource,
    ResolvedParameter,
)
from odyssey_fx.strategy.declarations.datatypes import OPPORTUNITY_V1
from odyssey_fx.strategy.declarations.evaluation import (
    OnBarClose,
    OnInputEvent,
    OnRuntimeEvent,
)
from odyssey_fx.strategy.declarations.missing import Error as ErrorPolicy
from odyssey_fx.strategy.declarations.opportunity import (
    OnNewTrigger,
    OnOrderAccepted,
    ValidityMode,
)
from odyssey_fx.strategy.declarations.read_spec import (
    CurrentContext,
    DeliveredEvent,
    HistoryWindow,
    LatestAvailable,
)
from odyssey_fx.strategy.declarations.refs import MarketDataField, OutputRef, RuntimeTarget
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    FloatValue,
    IntValue,
    OutputSpec,
    PortKind,
    StrValue,
)
from odyssey_fx.strategy.declarations.state_spec import StateSpec
from odyssey_fx.strategy.records.payloads import (
    ConditionState,
    Opportunity,
    OpportunityContent,
    OrderIntent,
    ProtectionLevels,
    payload_type_for,
)
from odyssey_fx.strategy.records.records import OutputRecord
from odyssey_fx.strategy.runtime.opportunities import (
    OpportunityLifecycle,
    OpportunityState,
    OpportunityTerminal,
    ValiditySnapshot,
)
from odyssey_fx.strategy.runtime.opportunities import (
    OpportunityTransition as Transition,
)
from odyssey_fx.strategy.runtime.ports import (
    AdmissionNotice,
    MarketDataView,
    OutputSink,
    PublicationBatch,
    RuntimeContextView,
)
from odyssey_fx.strategy.runtime.requests import (
    ContextSnapshot,
    EntryProposal,
    Evaluated,
    EvaluationRecord,
    EvaluationRequest,
    EventDelivery,
    Failed,
    InputElement,
    ManagementRequest,
    MissingInputDiagnosis,
    ResolvedInputs,
    RuntimeStepResult,
    Skipped,
    ValueSample,
    ValueWindow,
)

__all__ = [
    "PHASE_NAMES",
    "PHASE_OPPORTUNITY_LIFECYCLE",
    "PHASE_P1_FEATURE",
    "PHASE_P2_MARKET_STATE",
    "PHASE_P3_TRIGGER",
    "PHASE_P4_CONFIRMATION",
    "PHASE_P5_ORDER_INTENT",
    "PHASE_POST_FILL_EVALUATION",
    "PHASE_RUN_END",
    "RuntimeState",
    "StrategyEvaluator",
]

PHASE_OPPORTUNITY_LIFECYCLE: Final = "OPPORTUNITY_LIFECYCLE"
PHASE_P1_FEATURE: Final = "P1_FEATURE"
PHASE_P2_MARKET_STATE: Final = "P2_MARKET_STATE"
PHASE_P3_TRIGGER: Final = "P3_TRIGGER"
PHASE_P4_CONFIRMATION: Final = "P4_CONFIRMATION"
PHASE_P5_ORDER_INTENT: Final = "P5_ORDER_INTENT"
PHASE_POST_FILL_EVALUATION: Final = "POST_FILL_EVALUATION"
PHASE_RUN_END: Final = "RUN_END"

#: ランタイムがフェーズ集合に要求する名前（D05 §6.1）。
#:
#: 上位設計書 §4.3.12 の P1〜P5 に、本書が D06 へ要求した2つ（ライフサイクル検査・約定後の
#: 評価起動点）と、run 末尾処理を加えたもの。D06 §4.1 がこの名前で順位を確定している。
PHASE_NAMES: Final[tuple[str, ...]] = (
    PHASE_OPPORTUNITY_LIFECYCLE,
    PHASE_P1_FEATURE,
    PHASE_P2_MARKET_STATE,
    PHASE_P3_TRIGGER,
    PHASE_P4_CONFIRMATION,
    PHASE_P5_ORDER_INTENT,
    PHASE_POST_FILL_EVALUATION,
    PHASE_RUN_END,
)


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """`step` を跨いで持ち越す状態（D05 §6.5）。

    `step` ごとに**新しい `RuntimeState` へ差し替える**。可変参照はランタイム1インスタンス
    につき1つだけで、他はすべて不変である（D05 §1 の唯一の例外）。

    `latest_outputs` が保持するのは、繰り返し参照する値（`VALUE`）の出力参照ごとに**最新の
    1件だけ**である。配送イベントと要求は保持しない。配送された時点で消費されるものであり、
    保持すると過去のイベントを「最新値」として読めてしまう。
    """

    component_states: Mapping[str, object] = field(default_factory=dict)
    latest_outputs: Mapping[OutputRef, OutputRecord[object]] = field(default_factory=dict)
    opportunities: tuple[OpportunityLifecycle, ...] = ()
    last_batch_id: EventId | None = None
    run_end_seen: bool = False


class _EvaluationFailure(Exception):
    """評価の失敗を `step` の入口まで運ぶための内部例外（外へは出さない）。"""

    def __init__(self, reason: Reason) -> None:
        super().__init__(str(reason))
        self.reason = reason


class _InputsMissing(Exception):
    """必須入力が欠けていたことを運ぶための内部例外（外へは出さない）。"""

    def __init__(self, diagnoses: tuple[MissingInputDiagnosis, ...]) -> None:
        super().__init__("required inputs are missing")
        self.diagnoses = diagnoses


def _data_error(cause: str) -> Reason:
    """段階2の失敗理由（D05 §6.4）。詳細型は市場データ向けなので添えない。"""
    del cause
    return Reason(code=ReasonCode.DATA_ERROR)


class StrategyEvaluator:
    """`CompiledStrategy` を公開バッチに対して評価する（D05 §6）。

    **この型だけが可変**である（D05 §1 の例外）。保持するのは現在の `RuntimeState` への
    参照1つだけで、`step` のたびに新しい状態へ差し替える。
    """

    __slots__ = (
        "_allocator",
        "_compiled",
        "_context",
        "_market_data",
        "_registry",
        "_sink",
        "_state",
    )

    def __init__(
        self,
        compiled: CompiledStrategy,
        registry: ComponentRegistry,
        market_data: MarketDataView,
        context: RuntimeContextView,
        sink: OutputSink,
        allocator: IdAllocator,
    ) -> None:
        self._compiled = compiled
        self._registry = registry
        self._market_data = market_data
        self._context = context
        self._sink = sink
        self._allocator = allocator
        self._state = RuntimeState(component_states=self._initial_states())

    @property
    def state(self) -> RuntimeState:
        """現在の状態（判断履歴の検証とテストが読む）。"""
        return self._state

    def _initial_states(self) -> dict[str, object]:
        """状態の初期値を宣言から組み立てる（D05 §6.5、D04 §9.1）。

        実装側の既定値に委ねない。同じ宣言からは同じ初期状態になり、「同一入力の再実行で
        判断履歴が一致する」が実装に依存しなくなる。
        """
        states: dict[str, object] = {}
        for component in self._compiled.components:
            if component.state_spec is None:
                continue
            states[component.instance_id] = _build_state(component.state_spec)
        return states

    # --- 入口 --------------------------------------------------------------

    def step(self, batch: PublicationBatch) -> RuntimeStepResult:
        """公開バッチ1件を処理する（D05 §6.2）。"""
        if self._state.last_batch_id is not None and batch.batch_id == self._state.last_batch_id:
            raise KernelValueError(
                f"batch {batch.batch_id} has already been processed; the engine must not"
                " redeliver a publication batch (D05 §6.1)"
            )
        if self._state.run_end_seen:
            # run 末尾の合図のあとに評価を続けると、そこで生まれた取引機会を `RUN_END` で
            # 終端する機会がもう無い（末尾の合図は1 run に1回）。終端理由別の集計で機会の
            # 総数が合わなくなるので、末尾以降はどのバッチも受け付けない（D05 §6.1・§7.2）。
            raise KernelValueError(
                f"batch {batch.batch_id} arrived after the end-of-run batch; a run has no"
                " decision points left once its opportunities have been terminated (D05 §6.1)"
            )
        phases = {name: batch.phases.by_name(name) for name in PHASE_NAMES}

        if batch.is_run_end:
            return self._run_end(batch, phases[PHASE_RUN_END])
        return _StepRun(self, batch, phases).execute()

    def _run_end(self, batch: PublicationBatch, phase: PhaseRank) -> RuntimeStepResult:
        """run 末尾に残った取引機会をすべて終端する（D05 §6.1・§7.2 の遷移9）。

        末尾の合図を受けた `step` は起動判定・評価・出力の送出を行わない。終端しないまま run
        が終わると、終端理由別の集計で機会の総数が合わなくなる。

        終端の順序は機会の連番の昇順とする。同じ判断時刻・同じフェーズの中で通し番号が
        決定論的に決まるようにするためである。
        """
        if self.state.run_end_seen:
            raise KernelValueError(
                "the end-of-run batch may only be delivered once per run (D05 §6.1)"
            )
        transitions: list[Transition] = []
        lifecycles = {item.opportunity_id: item for item in self.state.opportunities}
        sequence = 0
        reason = Reason(code=ReasonCode.RUN_END)
        for opportunity_id in sorted(lifecycles, key=lambda item: item.seq):
            lifecycle = lifecycles[opportunity_id]
            if not lifecycle.is_active:
                continue
            at = ProcessingPoint(time=batch.decision_time, phase=phase, sequence=sequence)
            sequence += 1
            moved = lifecycle.moved_to(OpportunityState.TERMINATED, at=at, reason=reason)
            lifecycles[opportunity_id] = moved
            transitions.append(
                Transition(
                    opportunity_id=opportunity_id,
                    from_state=lifecycle.state,
                    to_state=OpportunityState.TERMINATED,
                    at=at,
                    phase=phase,
                    reason=reason,
                )
            )
        self._state = RuntimeState(
            component_states=self.state.component_states,
            latest_outputs=self.state.latest_outputs,
            opportunities=tuple(lifecycles.values()),
            last_batch_id=batch.batch_id,
            run_end_seen=True,
        )
        return RuntimeStepResult(transitions=tuple(transitions))


def _build_state(state_spec: StateSpec) -> object:
    """状態の初期値を宣言から作る（D04 §9.1、D05 §6.5）。"""
    payload_types = payload_type_for(state_spec.state_type)
    if not payload_types:
        raise KernelValueError(
            f"state type {state_spec.state_type} has no runtime payload type (D05 §4.2)"
        )
    payload_type = payload_types[0]
    kwargs = {name: _plain(value) for name, value in state_spec.initial.values.items()}
    return payload_type(**kwargs)


def _plain(value: BoolValue | IntValue | FloatValue | StrValue | object) -> object:
    """宣言のパラメータ値を、内容型のコンストラクタに渡せる素の値へ戻す。"""
    if isinstance(value, (BoolValue, IntValue, FloatValue, StrValue)):
        return value.value
    return value


def _project(bar: Bar, market_field: MarketDataField) -> Price | Decimal:
    """足を宣言された項目へ射影する（D05 §6.3）。

    部品の入力の型は `price@v1` などの単一の内容型なので、足のまま渡さない。渡さないこと
    で、宣言していない項目（当該足の終値など）を部品が覗くこともできなくなる。
    """
    if market_field is MarketDataField.OPEN:
        return bar.open
    if market_field is MarketDataField.HIGH:
        return bar.high
    if market_field is MarketDataField.LOW:
        return bar.low
    if market_field is MarketDataField.CLOSE:
        return bar.close
    return bar.volume


def _is_missing(value: object) -> bool:
    """市場データビューの戻り値が欠損かどうか（`Bar` でなければ欠損）。"""
    return not isinstance(value, Bar)


def _missing_reason(value: object) -> MissingInputReason:
    reason = getattr(value, "reason", None)
    if isinstance(reason, MissingInputReason):
        return reason
    return MissingInputReason.INPUT_MISSING_OR_INVALID


class _StepRun:
    """1回の `step` のあいだだけ生きる作業場（D05 §6.2）。

    ランタイム本体を可変にしないために、1回ぶんの途中結果はすべてここに持ち、最後に新しい
    `RuntimeState` へまとめて差し替える。
    """

    def __init__(
        self,
        evaluator: StrategyEvaluator,
        batch: PublicationBatch,
        phases: Mapping[str, PhaseRank],
    ) -> None:
        state = evaluator.state
        self._evaluator = evaluator
        self._batch = batch
        self._phases = phases
        self._compiled: CompiledStrategy = evaluator._compiled
        self._registry: ComponentRegistry = evaluator._registry
        self._market_data: MarketDataView = evaluator._market_data
        self._context: RuntimeContextView = evaluator._context
        self._sink: OutputSink = evaluator._sink
        self._allocator: IdAllocator = evaluator._allocator

        self._sequence = 0
        self._outputs: list[OutputRecord[object]] = []
        self._evaluations: list[EvaluationRecord] = []
        self._transitions: list[Transition] = []
        self._management: list[ManagementRequest] = []
        self._proposals: list[EntryProposal] = []
        self._lifecycles: dict[OpportunityId, OpportunityLifecycle] = {
            item.opportunity_id: item for item in state.opportunities
        }
        self._component_states: dict[str, object] = dict(state.component_states)
        self._latest_outputs: dict[OutputRef, OutputRecord[object]] = dict(state.latest_outputs)
        self._deliveries: dict[OutputRef, list[OutputRecord[object]]] = {}
        self._origins: dict[
            OutputId, tuple[Interval | None, OpportunityId | None, PositionId | None]
        ] = {}
        self._role_intents: dict[OpportunityId, OutputRecord[object]] = {}
        self._role_protections: dict[OpportunityId, OutputRecord[object]] = {}
        self._failed = False

    # --- 進行 --------------------------------------------------------------

    def execute(self) -> RuntimeStepResult:
        """1回ぶんの評価を行う（D05 §6.2 の手順1〜10）。"""
        self._apply_admissions()
        for component in self._compiled.components:
            if self._failed:
                break
            self._evaluate_component(component)
        if not self._failed:
            self._recheck_validity(PHASE_P4_CONFIRMATION)
            self._assemble_proposals()
        self._commit()
        return RuntimeStepResult(
            outputs=tuple(self._outputs),
            evaluations=tuple(self._evaluations),
            proposals=tuple(self._proposals),
            management_requests=tuple(self._management),
            transitions=tuple(self._transitions),
        )

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value

    def _point(self, phase_name: str) -> ProcessingPoint:
        return ProcessingPoint(
            time=self._batch.decision_time,
            phase=self._phases[phase_name],
            sequence=self._next_sequence(),
        )

    def _commit(self) -> None:
        self._evaluator._state = RuntimeState(
            component_states=self._component_states,
            latest_outputs=self._latest_outputs,
            opportunities=tuple(self._lifecycles.values()),
            last_batch_id=self._batch.batch_id,
            run_end_seen=self._evaluator.state.run_end_seen,
        )

    # --- 受付結果の適用（遷移5〜7） ----------------------------------------

    def _apply_admissions(self) -> None:
        """受付結果の通知を次の `step` の入口で適用する（D05 §7.2 の遷移5〜7、v1.3）。

        記録するフェーズは「受付を判定したフェーズ」ではなく、**通知が配送された処理点**
        （段階2では約定後の評価起動点。D05 §8）である。受付の判定そのものはエンジンが済ませて
        おり、ランタイムはその結果を機会へ写すだけなので、状態が実際に変わるのはここである。
        """
        for notice in self._batch.admissions:
            lifecycle = self._lifecycles.get(notice.opportunity_id)
            if lifecycle is None:
                raise KernelValueError(
                    f"admission notice refers to unknown opportunity {notice.opportunity_id}"
                )
            if lifecycle.state is not OpportunityState.ORDER_PENDING:
                raise KernelValueError(
                    f"opportunity {notice.opportunity_id} is {lifecycle.state.value}, so it has"
                    " no order attempt awaiting admission (D05 §7.2)"
                )
            reason_code = (
                ReasonCode.FULFILLED_BY_ORDER_ACCEPTANCE
                if notice.accepted
                else ReasonCode.ORDER_ATTEMPT_REJECTED
            )
            self._terminate(
                lifecycle,
                Reason(code=reason_code),
                PHASE_POST_FILL_EVALUATION,
                attempt_id=notice.attempt_id,
            )
            if notice.accepted:
                self._close_others(notice)

    def _close_others(self, notice: AdmissionNotice) -> None:
        """他の機会を閉じる設定なら、残りの非終端の機会を終端する（D05 §7.2 の遷移7）。"""
        concurrency = self._compiled.opportunity_concurrency
        if concurrency.on_order_accepted is not OnOrderAccepted.CLOSE_OTHERS:
            return
        others = sorted(
            (
                item
                for item in self._lifecycles.values()
                if item.is_active and item.opportunity_id != notice.opportunity_id
            ),
            key=lambda item: item.opportunity_id.seq,
        )
        for other in others:
            self._terminate(
                other,
                Reason(code=ReasonCode.CLOSED_BY_ORDER_ACCEPTANCE),
                PHASE_POST_FILL_EVALUATION,
                counterpart=notice.opportunity_id,
            )

    def _terminate(
        self,
        lifecycle: OpportunityLifecycle,
        reason: Reason,
        phase_name: str,
        *,
        counterpart: OpportunityId | None = None,
        attempt_id: AttemptId | None = None,
    ) -> None:
        """機会を終端し、遷移を記録する（D05 §7.2）。"""
        at = self._point(phase_name)
        moved = lifecycle.moved_to(
            OpportunityState.TERMINATED, at=at, reason=reason, attempt_id=attempt_id
        )
        self._lifecycles[moved.opportunity_id] = moved
        self._transitions.append(
            Transition(
                opportunity_id=moved.opportunity_id,
                from_state=lifecycle.state,
                to_state=OpportunityState.TERMINATED,
                at=at,
                phase=at.phase,
                reason=reason,
                counterpart=counterpart,
                attempt_id=moved.attempt_id,
            )
        )

    # --- 起動判定と評価要求（手順1〜3） -------------------------------------

    def _build_requests(
        self, component: CompiledComponent
    ) -> list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]]:
        """起動した起動条件から評価要求を作る（D05 §6.2 の手順1〜3）。

        足の確定は**対象区間が同じものだけを1件に集約**し（Q6 決定）、イベントは配送1件・
        通知1件につき1要求を作る。イベントを使用箇所ごとに1件へ畳まないのは、1つのイベントが
        1つの対象を指すからである。同じバッチで2つの建玉が生まれれば通知は2件で、畳むと片方の
        建玉に利確が付かない。
        """
        requests: list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]] = []
        requests.extend(self._bar_close_requests(component))
        requests.extend(self._input_event_requests(component))
        requests.extend(self._runtime_event_requests(component))
        return requests

    def _bar_close_requests(
        self, component: CompiledComponent
    ) -> list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]]:
        by_interval: dict[Interval, list[str]] = {}
        for trigger in component.triggers:
            if not isinstance(trigger, OnBarClose):
                continue
            for closure in self._batch.scheduled_closes:
                if closure.bar_key.series != trigger.series:
                    continue
                by_interval.setdefault(closure.interval, []).append(trigger.name)
        out: list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]] = []
        for interval in sorted(by_interval, key=lambda item: str(item.start)):
            request = EvaluationRequest(
                request_id=self._allocator.next(RequestId),
                instance_id=component.instance_id,
                trigger_names=tuple(sorted(by_interval[interval])),
                decision_time=self._batch.decision_time,
                target_interval=interval,
            )
            out.append((request, {}))
        return out

    def _input_event_requests(
        self, component: CompiledComponent
    ) -> list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]]:
        out: list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]] = []
        for trigger in component.triggers:
            if not isinstance(trigger, OnInputEvent):
                continue
            plan = component.input_plans.get(trigger.input_name)
            if plan is None:
                continue
            for source in plan.sources:
                if not isinstance(source, ResolvedOutputSource):
                    continue
                for record in self._deliveries.get(source.ref, ()):
                    interval, opportunity_id, position_id = self._origins.get(
                        record.output_id, (None, None, None)
                    )
                    if isinstance(record.payload, Opportunity):
                        opportunity_id = record.payload.opportunity_id
                    request = EvaluationRequest(
                        request_id=self._allocator.next(RequestId),
                        instance_id=component.instance_id,
                        trigger_names=(trigger.name,),
                        decision_time=self._batch.decision_time,
                        target_interval=interval,
                        opportunity_id=opportunity_id,
                        position_id=position_id,
                    )
                    out.append((request, {trigger.input_name: (record,)}))
        return out

    def _runtime_event_requests(
        self, component: CompiledComponent
    ) -> list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]]:
        out: list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]] = []
        for trigger in component.triggers:
            if not isinstance(trigger, OnRuntimeEvent):
                continue
            for notice in self._batch.runtime_events:
                if notice.kind is not trigger.event:
                    continue
                request = EvaluationRequest(
                    request_id=self._allocator.next(RequestId),
                    instance_id=component.instance_id,
                    trigger_names=(trigger.name,),
                    decision_time=self._batch.decision_time,
                    target_interval=None,
                    opportunity_id=notice.opportunity_id,
                    position_id=notice.position_id,
                )
                out.append((request, {}))
        return out

    # --- 1つの使用箇所の評価（手順4〜9） -----------------------------------

    def _evaluate_component(self, component: CompiledComponent) -> None:
        for request, delivered in self._build_requests(component):
            if self._failed:
                return
            self._evaluate_once(component, request, delivered)

    def _evaluate_once(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        delivered: Mapping[str, tuple[OutputRecord[object], ...]],
    ) -> None:
        evaluation_id = self._allocator.next(EvaluationId)
        try:
            inputs = self._resolve_inputs(component, request, delivered)
            result = self._call_component(component, inputs, request)
        except _InputsMissing as missing:
            self._record(request, evaluation_id, Skipped(diagnoses=missing.diagnoses))
            return
        except _EvaluationFailure as failure:
            self._record(request, evaluation_id, Failed(reason=failure.reason))
            self._failed = True
            return

        try:
            output_ids = self._publish(component, request, evaluation_id, result)
        except _EvaluationFailure as failure:
            self._record(request, evaluation_id, Failed(reason=failure.reason))
            self._failed = True
            return

        if isinstance(self._implementation(component), StatefulImplementation):
            self._component_states[component.instance_id] = result.new_state
        self._record(request, evaluation_id, Evaluated(output_ids=output_ids))

    def _implementation(self, component: CompiledComponent) -> ComponentImplementation:
        registration = self._registry.get(
            ContractKey(component.contract_ref.component_id, component.contract_ref.version)
        )
        if registration is None:  # pragma: no cover - コンパイル時に解決済み
            raise KernelValueError(f"component {component.instance_id} is no longer registered")
        return registration.implementation

    def _call_component(
        self,
        component: CompiledComponent,
        inputs: ResolvedInputs,
        request: EvaluationRequest,
    ) -> ComponentOutputs:
        """部品を呼び、例外で終わった場合も失敗として扱う（D05 §6.2）。"""
        implementation = self._implementation(component)
        parameters: Mapping[str, ResolvedParameter] = component.parameters
        try:
            if isinstance(implementation, StatefulImplementation):
                state = self._component_states.get(component.instance_id)
                result = implementation.evaluate(inputs, parameters, state)
            else:
                result = implementation.evaluate(inputs, parameters)
        except Exception as error:
            raise _EvaluationFailure(_data_error(f"{component.instance_id}: {error}")) from error
        if not isinstance(result, ComponentOutputs):
            raise _EvaluationFailure(
                _data_error(f"{component.instance_id} did not return ComponentOutputs")
            )
        del request
        return result

    def _record(
        self,
        request: EvaluationRequest,
        evaluation_id: EvaluationId,
        outcome: Evaluated | Skipped | Failed,
    ) -> None:
        """評価記録を1件残す（D05 §6.4）。起動した使用箇所ごとに必ず1件残る。"""
        self._evaluations.append(
            EvaluationRecord(
                request_id=request.request_id,
                evaluation_id=evaluation_id,
                instance_id=request.instance_id,
                trigger_names=request.trigger_names,
                decision_time=request.decision_time,
                outcome=outcome,
                target_interval=request.target_interval,
                opportunity_id=request.opportunity_id,
                position_id=request.position_id,
            )
        )

    # --- 入力解決（D05 §6.3） ----------------------------------------------

    def _required_input_names(
        self, component: CompiledComponent, trigger_names: Sequence[str]
    ) -> frozenset[str]:
        """今回の起動で必須になる入力（D05 §6.3 v1.3）。

        **宣言があればその和集合、1件も無ければ接続済みの入力すべて**を必須とする。段階2の
        5部品は `required_inputs` を宣言しておらず（D05 §4.3）、空集合をそのまま必須とすると
        履歴不足で読めない入力があっても評価を行うことになり、T01 §6.1 が示す「ウォームアップ
        中は見送る」挙動にならないためである。
        """
        declared: set[str] = set()
        found = False
        for name in trigger_names:
            names = component.required_inputs.get(name)
            if names is None:
                continue
            found = True
            declared.update(names)
        if found:
            return frozenset(declared)
        return frozenset(component.input_plans)

    def _resolve_inputs(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        delivered: Mapping[str, tuple[OutputRecord[object], ...]],
    ) -> ResolvedInputs:
        required = self._required_input_names(component, request.trigger_names)
        by_name: dict[str, tuple[InputElement, ...]] = {}
        diagnoses: list[MissingInputDiagnosis] = []
        for input_name, plan in component.input_plans.items():
            elements, missing = self._resolve_one(plan, request, delivered.get(input_name, ()))
            if missing:
                diagnoses.extend(missing)
                if input_name in required:
                    self._apply_missing_policy(plan, tuple(diagnoses))
                continue
            by_name[input_name] = elements
        if diagnoses:
            blocking = [item for item in diagnoses if item.input_name in required]
            if blocking:
                raise _InputsMissing(tuple(diagnoses))
        return ResolvedInputs(by_name=by_name)

    def _apply_missing_policy(
        self, plan: InputPlan, diagnoses: tuple[MissingInputDiagnosis, ...]
    ) -> None:
        """欠損方針に従う（D04 §6.3、D05 §6.3）。欠損を False や 0 に変換しない。"""
        on_missing = getattr(plan.read_spec, "on_missing", None)
        if isinstance(on_missing, ErrorPolicy):
            raise _EvaluationFailure(
                _data_error(f"{plan.input_name}: {diagnoses[-1].reason.value}")
            )

    def _resolve_one(
        self,
        plan: InputPlan,
        request: EvaluationRequest,
        delivered: tuple[OutputRecord[object], ...],
    ) -> tuple[tuple[InputElement, ...], list[MissingInputDiagnosis]]:
        read_spec = plan.read_spec
        if isinstance(read_spec, LatestAvailable):
            return self._resolve_latest(plan, read_spec)
        if isinstance(read_spec, HistoryWindow):
            return self._resolve_history(plan, read_spec)
        if isinstance(read_spec, DeliveredEvent):
            return self._resolve_delivered(plan, delivered)
        if isinstance(read_spec, CurrentContext):
            return self._resolve_context(plan, request)
        raise KernelValueError(  # pragma: no cover - 4区分しかない
            f"unsupported read spec: {read_spec!r}"
        )

    def _resolve_latest(
        self, plan: InputPlan, read_spec: LatestAvailable
    ) -> tuple[tuple[InputElement, ...], list[MissingInputDiagnosis]]:
        elements: list[InputElement] = []
        missing: list[MissingInputDiagnosis] = []
        for source in plan.sources:
            if isinstance(source, ResolvedMarketSource):
                bar = self._market_data.latest_available(source.series, self._batch.decision_time)
                if _is_missing(bar):
                    missing.append(
                        MissingInputDiagnosis(plan.input_name, source, _missing_reason(bar))
                    )
                    continue
                assert isinstance(bar, Bar)
                freshness = self._market_data.freshness(source.series, bar)
                if self._too_old(freshness, read_spec.max_age):
                    missing.append(
                        MissingInputDiagnosis(
                            plan.input_name, source, MissingInputReason.MAX_AGE_EXCEEDED
                        )
                    )
                    continue
                elements.append(
                    ValueSample(
                        payload=_project(bar, source.field),
                        source=source,
                        freshness_time=freshness,
                    )
                )
            elif isinstance(source, ResolvedOutputSource):
                record = self._latest_outputs.get(source.ref)
                if record is None:
                    missing.append(
                        MissingInputDiagnosis(
                            plan.input_name, source, MissingInputReason.INPUT_MISSING_OR_INVALID
                        )
                    )
                    continue
                if self._too_old(record.decision_time, read_spec.max_age):
                    missing.append(
                        MissingInputDiagnosis(
                            plan.input_name, source, MissingInputReason.MAX_AGE_EXCEEDED
                        )
                    )
                    continue
                elements.append(
                    ValueSample(
                        payload=record.payload,
                        source=source,
                        freshness_time=record.decision_time,
                        source_output_id=record.output_id,
                    )
                )
            else:  # pragma: no cover - コンパイル時に拒否される組合せ
                missing.append(
                    MissingInputDiagnosis(
                        plan.input_name, source, MissingInputReason.INPUT_MISSING_OR_INVALID
                    )
                )
        return tuple(elements), missing

    def _resolve_history(
        self, plan: InputPlan, read_spec: HistoryWindow
    ) -> tuple[tuple[InputElement, ...], list[MissingInputDiagnosis]]:
        elements: list[InputElement] = []
        missing: list[MissingInputDiagnosis] = []
        window = plan.resolved_window
        if window is None:  # pragma: no cover - コンパイル時に解決済み
            raise KernelValueError(f"input {plan.input_name!r} has no resolved window")
        for source in plan.sources:
            if not isinstance(source, ResolvedMarketSource):  # pragma: no cover
                missing.append(
                    MissingInputDiagnosis(
                        plan.input_name, source, MissingInputReason.INPUT_MISSING_OR_INVALID
                    )
                )
                continue
            bars = self._market_data.history(
                source.series,
                window,
                self._batch.decision_time,
                end_offset_bars=read_spec.exclude_latest_bars,
            )
            if not isinstance(bars, tuple):
                missing.append(
                    MissingInputDiagnosis(plan.input_name, source, _missing_reason(bars))
                )
                continue
            samples = tuple(
                ValueSample(
                    payload=_project(bar, source.field),
                    source=source,
                    freshness_time=self._market_data.freshness(source.series, bar),
                )
                for bar in bars
            )
            if samples and self._too_old(samples[-1].freshness_time, read_spec.max_age):
                missing.append(
                    MissingInputDiagnosis(
                        plan.input_name, source, MissingInputReason.MAX_AGE_EXCEEDED
                    )
                )
                continue
            elements.append(ValueWindow(samples=samples))
        return tuple(elements), missing

    def _resolve_delivered(
        self, plan: InputPlan, delivered: tuple[OutputRecord[object], ...]
    ) -> tuple[tuple[InputElement, ...], list[MissingInputDiagnosis]]:
        if not delivered:
            return (), [
                MissingInputDiagnosis(
                    plan.input_name, plan.sources[0], MissingInputReason.INPUT_MISSING_OR_INVALID
                )
            ]
        return (
            tuple(
                EventDelivery(payload=record.payload, source_output_id=record.output_id)
                for record in delivered
            ),
            [],
        )

    def _resolve_context(
        self, plan: InputPlan, request: EvaluationRequest
    ) -> tuple[tuple[InputElement, ...], list[MissingInputDiagnosis]]:
        elements: list[InputElement] = []
        missing: list[MissingInputDiagnosis] = []
        at = self._batch.decision_time
        for source in plan.sources:
            if not isinstance(source, ResolvedContextSource):  # pragma: no cover
                continue
            if source.target is RuntimeTarget.POSITION:
                payload = self._context.position_context(at, request.position_id)
            else:
                payload = self._context.account_context(at)
            if payload is None:
                missing.append(
                    MissingInputDiagnosis(
                        plan.input_name, source, MissingInputReason.INPUT_MISSING_OR_INVALID
                    )
                )
                continue
            elements.append(ContextSnapshot(payload=payload, read_at=at))
        return tuple(elements), missing

    def _too_old(self, freshness: UtcTime, max_age: object) -> bool:
        """鮮度上限の判定はランタイムが行う（D03 §6.2 が委ねた）。"""
        if max_age is None:
            return False
        return (self._batch.decision_time - freshness) > max_age  # type: ignore[operator]

    # --- 戻り値の検査・取引機会・付番（手順5〜8） --------------------------

    def _publish(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        evaluation_id: EvaluationId,
        result: ComponentOutputs,
    ) -> tuple[OutputId, ...]:
        """戻り値を検査し、取引機会を組み立ててから付番・送出する（D05 §6.2）。"""
        contract = self._contract_outputs(component)
        self._check_outputs(component, contract, result)

        # 手順6: 付番より先に取引機会を組み立てる。識別子を付ける前に送出すると、判断履歴に
        # 識別子のない内容が残る。
        opportunities: dict[str, Opportunity] = {}
        withheld: set[str] = set()
        for output_name in sorted(result.outputs):
            spec = contract[output_name]
            if spec.data_type != OPPORTUNITY_V1:
                continue
            content = result.outputs[output_name]
            if not isinstance(content, OpportunityContent):  # pragma: no cover - 検査済み
                raise _EvaluationFailure(_data_error(f"{output_name} is not an OpportunityContent"))
            opportunity, admitted = self._admit_opportunity(component, request, content)
            opportunities[output_name] = opportunity
            if not admitted:
                withheld.add(output_name)

        # 手順7・8: 付番して送出し、終端しなかった機会のイベントだけを下流へ配送する。
        records: list[OutputRecord[object]] = []
        for output_name in sorted(result.outputs):
            spec = contract[output_name]
            payload = opportunities.get(output_name, result.outputs[output_name])
            producer = OutputRef(component.instance_id, output_name)
            record: OutputRecord[object] = OutputRecord(
                output_id=self._allocator.next(OutputId),
                evaluation_id=evaluation_id,
                producer=producer,
                decision_time=self._batch.decision_time,
                available_at=self._batch.decision_time,
                sequence=self._next_sequence(),
                payload=payload,
            )
            records.append(record)
            self._outputs.append(record)
            opportunity_id = (
                payload.opportunity_id
                if isinstance(payload, Opportunity)
                else request.opportunity_id
            )
            self._origins[record.output_id] = (
                request.target_interval,
                opportunity_id,
                request.position_id,
            )
            if spec.kind is PortKind.VALUE:
                self._latest_outputs[producer] = record
            elif spec.kind is PortKind.EVENT and output_name not in withheld:
                self._deliveries.setdefault(producer, []).append(record)
            self._capture_role_output(producer, record, request)
        self._sink.emit(tuple(records))
        return tuple(record.output_id for record in records)

    def _contract_outputs(self, component: CompiledComponent) -> Mapping[str, OutputSpec]:
        registration = self._registry.get(
            ContractKey(component.contract_ref.component_id, component.contract_ref.version)
        )
        if registration is None:  # pragma: no cover - コンパイル時に解決済み
            raise KernelValueError(f"component {component.instance_id} is no longer registered")
        return registration.contract.outputs

    def _check_outputs(
        self,
        component: CompiledComponent,
        contract_outputs: Mapping[str, OutputSpec],
        result: ComponentOutputs,
    ) -> None:
        """部品の戻り値を付番の前に検査する（D05 §6.2 の4点）。

        誤った内容が判断履歴と下流へ配送されるのを防ぐため、1つでも違反すれば出力記録を作らず
        状態も更新しない。
        """
        unknown = sorted(set(result.outputs) - set(contract_outputs))
        if unknown:
            raise _EvaluationFailure(
                _data_error(f"{component.instance_id} returned undeclared outputs: {unknown}")
            )
        for output_name, value in result.outputs.items():
            spec = contract_outputs[output_name]
            expected = payload_type_for(spec.data_type, as_component_return=True)
            if expected and not isinstance(value, expected):
                raise _EvaluationFailure(
                    _data_error(
                        f"{component.instance_id}.{output_name} must be one of"
                        f" {[item.__name__ for item in expected]}, got {type(value).__name__}"
                    )
                )
            if isinstance(value, OpportunityContent):
                self._check_reference_values(component, output_name, spec, value)
        self._check_new_state(component, result)

    def _check_reference_values(
        self,
        component: CompiledComponent,
        output_name: str,
        spec: OutputSpec,
        content: OpportunityContent,
    ) -> None:
        """根拠値が宣言した名前と型に一致することを確かめる（D04 §11.1）。"""
        schema = spec.reference_schema
        if set(content.reference_values) != set(schema):
            raise _EvaluationFailure(
                _data_error(
                    f"{component.instance_id}.{output_name} reference values must be"
                    f" {sorted(schema)}, got {sorted(content.reference_values)}"
                )
            )
        for name, data_type in schema.items():
            expected = payload_type_for(data_type)
            if expected and not isinstance(content.reference_values[name], expected):
                raise _EvaluationFailure(
                    _data_error(
                        f"{component.instance_id}.{output_name} reference value {name!r} must be"
                        f" a {expected[0].__name__}"
                    )
                )

    def _check_new_state(self, component: CompiledComponent, result: ComponentOutputs) -> None:
        """新しい状態の型が宣言と一致することを確かめる（D05 §6.2 の検査4）。"""
        state_spec = component.state_spec
        if state_spec is None:
            if result.new_state is not None:
                raise _EvaluationFailure(
                    _data_error(f"{component.instance_id} declares no state but returned one")
                )
            return
        if result.new_state is None:
            raise _EvaluationFailure(
                _data_error(f"{component.instance_id} declares state but returned none")
            )
        expected = payload_type_for(state_spec.state_type)
        if expected and not isinstance(result.new_state, expected):
            raise _EvaluationFailure(
                _data_error(
                    f"{component.instance_id} state must be a {expected[0].__name__},"
                    f" got {type(result.new_state).__name__}"
                )
            )

    # --- 取引機会の同時保持（D05 §7.4） ------------------------------------

    def _admit_opportunity(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        content: OpportunityContent,
    ) -> tuple[Opportunity, bool]:
        """発火を必ず記録したうえで、有効にするかどうかを決める（ADR-0032、D05 §7.4）。"""
        if component.symbol is None:  # pragma: no cover - コンパイル時に拒否される
            raise _EvaluationFailure(
                _data_error(f"{component.instance_id} has no symbol to stamp on an opportunity")
            )
        if request.target_interval is None:  # pragma: no cover - コンパイル時に拒否される
            raise _EvaluationFailure(
                _data_error(f"{component.instance_id} has no target interval for an opportunity")
            )
        opportunity = Opportunity(
            opportunity_id=self._allocator.next(OpportunityId),
            symbol=component.symbol,
            direction=content.direction,
            signal_interval=request.target_interval,
            reference_values=content.reference_values,
        )
        concurrency = self._compiled.opportunity_concurrency
        active = [item for item in self._lifecycles.values() if item.is_active]
        if len(active) < concurrency.max_active:
            # 生成時点の束縛を先に固定してから記録を足す（次の節の理由）。
            self._open(opportunity, self._take_snapshots())
            return opportunity, True
        if concurrency.on_new_trigger is OnNewTrigger.SUPERSEDE_EXISTING:
            replaceable = sorted(
                (item for item in active if item.is_replaceable),
                key=lambda item: item.supersession_key,
            )
            if replaceable:
                # **置換の前に新しい機会の束縛を固定する**。束縛が読めず失敗する場合
                # （`on_missing=Error`）、先に古い機会を終端していると、置換した相手が
                # ひとつも公開されないまま `SUPERSEDED` の遷移だけが判断履歴に残る。
                snapshots = self._take_snapshots()
                self._terminate(
                    replaceable[0],
                    Reason(code=ReasonCode.SUPERSEDED),
                    PHASE_P3_TRIGGER,
                    counterpart=opportunity.opportunity_id,
                )
                self._open(opportunity, snapshots)
                return opportunity, True
        self._reject(opportunity)
        return opportunity, False

    def _open(self, opportunity: Opportunity, snapshots: tuple[ValiditySnapshot, ...]) -> None:
        """遷移1: 生成して有効にする（D05 §7.2）。

        生成時点の束縛（`snapshots`）は**呼び出し元が、状態を変える前に**固定しておく
        （D05 §7.3）。束縛が読めずに失敗しうるため、ここで固定すると途中まで進んだ記録が
        残ってしまう。
        """
        at = self._point(PHASE_P3_TRIGGER)
        lifecycle = OpportunityLifecycle(
            opportunity=opportunity,
            state=OpportunityState.OPEN,
            created_at=at,
            created_decision_time=self._batch.decision_time,
            snapshots=snapshots,
        )
        self._lifecycles[opportunity.opportunity_id] = lifecycle
        self._transitions.append(
            Transition(
                opportunity_id=opportunity.opportunity_id,
                from_state=None,
                to_state=OpportunityState.OPEN,
                at=at,
                phase=at.phase,
            )
        )

    def _reject(self, opportunity: Opportunity) -> None:
        """遷移2: 発火を記録したうえで有効にせず終端する（D04 §10.3）。"""
        at = self._point(PHASE_P3_TRIGGER)
        reason = Reason(code=ReasonCode.CONCURRENCY_LIMIT_REACHED)
        lifecycle = OpportunityLifecycle(
            opportunity=opportunity,
            state=OpportunityState.TERMINATED,
            created_at=at,
            created_decision_time=self._batch.decision_time,
            terminal=OpportunityTerminal(reason=reason, at=at),
        )
        self._lifecycles[opportunity.opportunity_id] = lifecycle
        self._transitions.append(
            Transition(
                opportunity_id=opportunity.opportunity_id,
                from_state=None,
                to_state=OpportunityState.TERMINATED,
                at=at,
                phase=at.phase,
                reason=reason,
            )
        )

    def _take_snapshots(self) -> tuple[ValiditySnapshot, ...]:
        """対象区間束縛を生成時点で固定する（D05 §7.3）。"""
        snapshots: list[ValiditySnapshot] = []
        for binding in self._compiled.opportunity_validity.bindings:
            if binding.mode is not ValidityMode.SNAPSHOT_AT_OPPORTUNITY:
                continue
            record = self._latest_outputs.get(binding.source)
            if record is None:
                if isinstance(binding.on_missing, ErrorPolicy):
                    raise _EvaluationFailure(
                        _data_error(f"validity binding {binding.source} has no output to snapshot")
                    )
                continue
            payload = record.payload
            if not isinstance(payload, ConditionState):
                raise _EvaluationFailure(
                    _data_error(f"validity binding {binding.source} is not a ConditionState")
                )
            snapshots.append(
                ValiditySnapshot(
                    source=binding.source,
                    output_id=record.output_id,
                    satisfied=payload.satisfied,
                )
            )
        return tuple(snapshots)

    # --- 有効性の再検査（D05 §7.3、遷移8） ---------------------------------

    def _recheck_validity(self, phase_name: str, only: OpportunityId | None = None) -> None:
        """継続成立を要求した条件を読み直す（ADR-0031、D05 §7.3）。

        再検査で機会の内容（方向・対象区間・根拠値）は変更しない。欠損を不成立に変換しない。
        段階2の宣言は束縛が空なので実行されないが、段階3で条件を足すだけで動くよう経路は
        実装する（D05 §7.2 の遷移8）。
        """
        bindings = [
            binding
            for binding in self._compiled.opportunity_validity.bindings
            if binding.mode is ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST
        ]
        if not bindings:
            return
        for lifecycle in list(self._lifecycles.values()):
            if not lifecycle.is_replaceable:
                continue
            if only is not None and lifecycle.opportunity_id != only:
                continue
            for binding in bindings:
                record = self._latest_outputs.get(binding.source)
                if record is None:
                    if isinstance(binding.on_missing, ErrorPolicy):
                        raise KernelValueError(
                            f"validity binding {binding.source} has no output to re-check and"
                            " its missing-input policy is Error (D05 §7.3)"
                        )
                    continue
                payload = record.payload
                if not isinstance(payload, ConditionState):
                    raise KernelValueError(
                        f"validity binding {binding.source} must produce a ConditionState"
                    )
                if not payload.satisfied:
                    self._terminate(
                        lifecycle,
                        Reason(code=ReasonCode.MARKET_STATE_INVALIDATED),
                        phase_name,
                    )
                    break

    # --- 役割出力と発注提案（手順9） ---------------------------------------

    def _capture_role_output(
        self,
        producer: OutputRef,
        record: OutputRecord[object],
        request: EvaluationRequest,
    ) -> None:
        """役割フィールドが指す出力を拾う（D05 §6.2 の手順9）。"""
        roles = self._compiled.roles
        if producer == roles.order and request.opportunity_id is not None:
            self._role_intents[request.opportunity_id] = record
        elif producer == roles.protection and request.opportunity_id is not None:
            self._role_protections[request.opportunity_id] = record
        elif roles.exit is not None and producer == roles.exit:
            self._add_management_request(record, request)

    def _add_management_request(
        self, record: OutputRecord[object], request: EvaluationRequest
    ) -> None:
        """保有管理の要求を、宛先の建玉とともに作る（D05 §6.2 の手順9、v1.3）。

        宛先の建玉が無ければ構造エラーで止める。段階2の Exit は約定通知でだけ起動し、通知が
        必ず建玉を運ぶ（D05 §8）ため、この状態はカタログの5部品では起こらない。起きたとすれば
        コンパイラの検査か宣言の誤りであり、黙って捨てると建玉に利確が付かないまま run が進む。
        """
        if request.position_id is None:
            raise KernelValueError(
                f"{request.instance_id} produced a management action without a position to"
                " address it to; in stage 2 the exit role is started by a position-opened"
                " notice, which carries the position (D05 §8)"
            )
        action = record.payload
        if getattr(action, "kind", None) not in ("SET_TAKE_PROFIT", "CLOSE_POSITION"):
            raise _EvaluationFailure(
                _data_error(f"{request.instance_id} did not produce a management action")
            )
        self._management.append(
            ManagementRequest(
                position_id=request.position_id,
                action=action,  # type: ignore[arg-type]
                decision_time=self._batch.decision_time,
                source_output_id=record.output_id,
            )
        )

    def _assemble_proposals(self) -> None:
        """注文意図と保護水準が同じ機会で揃った時点で発注提案を1件作る（D05 §6.2 手順9）。"""
        ready = sorted(
            set(self._role_intents) & set(self._role_protections),
            key=lambda item: item.seq,
        )
        for opportunity_id in ready:
            lifecycle = self._lifecycles.get(opportunity_id)
            if lifecycle is None or not lifecycle.is_replaceable:
                continue
            # 遷移8 の「P5 直前」: 発注要求を作る直前に束縛条件を読み直す（ADR-0031）。
            self._recheck_validity(PHASE_P5_ORDER_INTENT, only=opportunity_id)
            lifecycle = self._lifecycles[opportunity_id]
            if not lifecycle.is_replaceable:
                continue
            intent_record = self._role_intents[opportunity_id]
            protection_record = self._role_protections[opportunity_id]
            intent = intent_record.payload
            protection = protection_record.payload
            if not isinstance(intent, OrderIntent) or not isinstance(
                protection, ProtectionLevels
            ):  # pragma: no cover - 出力の型検査が保証する
                raise KernelValueError("role outputs must be an OrderIntent and ProtectionLevels")
            self._proposals.append(
                EntryProposal(
                    opportunity_id=opportunity_id,
                    order_intent=intent,
                    protection=protection,
                    decision_time=self._batch.decision_time,
                    intent_output_id=intent_record.output_id,
                    protection_output_id=protection_record.output_id,
                )
            )
            at = self._point(PHASE_P5_ORDER_INTENT)
            moved = lifecycle.moved_to(OpportunityState.ORDER_PENDING, at=at)
            self._lifecycles[opportunity_id] = moved
            self._transitions.append(
                Transition(
                    opportunity_id=opportunity_id,
                    from_state=lifecycle.state,
                    to_state=OpportunityState.ORDER_PENDING,
                    at=at,
                    phase=at.phase,
                )
            )
