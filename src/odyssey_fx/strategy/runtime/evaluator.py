"""戦略ランタイムの評価器（D05 §6・§7・§8）。

エンジンから渡された公開バッチ1件を処理して、出力・評価記録・発注提案・管理要求・取引機会
の遷移・待機の出来事を返す。入口は `step` の1つだけである。

1回の `step` で行うこと（D05 §6.2・§6.8）:

1. 受付結果の通知を適用する（遷移5〜7）。
2. **ライフサイクル検査**（段階3、D05 §6.8 の手順1・2）。待機中の評価要求について、期限 →
   追い越し → 失効の順に判定し（Q29 決定）、どれも成立しなければ市場データの到着を調べる。
3. 起動判定と評価要求の生成（足の確定は対象区間ごとに1件、イベントは1件につき1要求）。
4. 評価順に、起動した使用箇所と、再開できる待機要求だけを評価する。上流の更新だけで下流を
   自動評価しない。出力参照の到着はここで評価順に沿って判定する（D05 §6.8 の手順1）。
5. 入力解決 → 欠損方針の適用（強い方針が勝つ。Q27 決定）→ 観測区間の一致の検査 → 部品の
   呼び出し → 戻り値の検査（付番の前）。
6. 取引機会の組み立てと同時保持の判定（付番より前。識別子の無い内容を判断履歴に残さない）。
7. 出力の付番と送出、状態の更新。繰り返し参照する値（`VALUE`）の出力は `Observation` で
   包み（段階3、D05 §6.7）、履歴窓で読まれる出力は保持する（D05 §6.12）。
8. 終端しなかった機会のイベントだけを下流へ配送する。
9. 役割出力から発注提案と管理要求を組み立てる。

**失敗は例外ではなく戻り値で返す**（D05 §6.2）。入力欠損（`Error` 方針）も、待機の期限切れ
（`on_deadline=ERROR`）も、戻り値の検査違反も、部品の呼び出しが例外で終わった場合も、評価
記録に失敗として残し、以降の評価を行わずに返す。

**処理点は自分で組み立てず、エンジンから受け取った材料で作る**（D05 §6.1）。フェーズの順位
と全列挙は D06 の責務なので、ランタイムは**名前だけを要求**して公開バッチのフェーズ集合から
順位を引く。

## 評価がどのフェーズに属するか

待機の出来事（`WaitEvent`）は処理点を持つ。ライフサイクル検査の出来事は
`OPPORTUNITY_LIFECYCLE`、使用箇所の評価で起きた出来事はその使用箇所のフェーズで刻む。
フェーズは上位設計書 §4.3.12 の P1〜P5 が役割の名前であることから、役割フィールドで決める
（市場状態 → P2、取引機会 → P3、後続確認 → P4、注文意図・保護水準・決済 → P5、役割の無い
使用箇所 → P1。T02 §1.5・§7.2 の記録と同じ）。受付結果や実行時イベントを運ぶ第2回の
`step` は約定後の評価起動点（`POST_FILL_EVALUATION`）で刻む（D05 §8、D06 §4.2）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from types import MappingProxyType
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
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.catalog.registry import (
    ComponentImplementation,
    ComponentOutputs,
    ComponentRegistration,
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
    ResolvedSource,
)
from odyssey_fx.strategy.declarations.datatypes import OPPORTUNITY_V1
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline
from odyssey_fx.strategy.declarations.evaluation import (
    OnBarClose,
    OnInputEvent,
    OnRuntimeEvent,
)
from odyssey_fx.strategy.declarations.missing import Error as ErrorPolicy
from odyssey_fx.strategy.declarations.missing import (
    MissingInputPolicy,
    SkipEvaluation,
    UsePrevious,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.opportunity import (
    OnNewTrigger,
    OnOrderAccepted,
    ValidityMode,
)
from odyssey_fx.strategy.declarations.read_spec import (
    BarsWindow,
    CurrentContext,
    DeliveredEvent,
    DurationWindow,
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
from odyssey_fx.strategy.records.records import Observation, OutputRecord
from odyssey_fx.strategy.runtime.opportunities import (
    OpportunityLifecycle,
    OpportunityState,
    OpportunityTerminal,
    ValiditySnapshot,
)
from odyssey_fx.strategy.runtime.opportunities import (
    OpportunityTransition as Transition,
)
from odyssey_fx.strategy.runtime.output_history import (
    RetainedOutput,
    retain,
    window_ending_at,
)
from odyssey_fx.strategy.runtime.ports import (
    AdmissionNotice,
    HistoryWindowView,
    MarketDataView,
    OutputSink,
    PublicationBatch,
    ResolvedBarsWindow,
    RuntimeContextView,
)
from odyssey_fx.strategy.runtime.requests import (
    ContextSnapshot,
    EntryProposal,
    Evaluated,
    EvaluationOutcome,
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
    SubstitutedInput,
    Superseded,
    ValueSample,
    ValueWindow,
    Waiting,
)
from odyssey_fx.strategy.runtime.supersession import pinned_target_bar, supersedes
from odyssey_fx.strategy.runtime.waiting import (
    LifecycleVerdict,
    PolicyStrength,
    WaitEvent,
    WaitEventKind,
    WaitingRequest,
    deadline_reached,
    deadline_series,
    effective_strength,
    judge_lifecycle,
    resolve_deadline,
    strongest_policy,
    tick_deadline,
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

#: 足の確定で起動した要求のうち、同じ使用箇所・同じ系列でいちばん新しい足の要求。
#: 追い越しの `by_request_id`（押しのけた側）を決めるのに使う（D05 §6.10 の3）。
_LatestRequests = Mapping[tuple[str, SeriesId], tuple[BarKey, RequestId]]


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """`step` を跨いで持ち越す状態（D05 §6.5）。

    `step` ごとに**新しい `RuntimeState` へ差し替える**。可変参照はランタイム1インスタンス
    につき1つだけで、他はすべて不変である（D05 §1 の唯一の例外）。

    - `latest_outputs`: 繰り返し参照する値（`VALUE`）の出力参照ごとに**最新の1件**（到着順）。
    - `output_history`: 保持本数の計画（`OutputRetentionPlan`）に載った出力参照だけ、観測した
      足の順に過去の出力を持つ（段階3、D05 §6.12）。
    - `waiting`: 待機中の評価要求（段階3、D05 §6.8）。判断履歴には出ない（T02 §7.1）。
    - `request_subjects`: 待機中の要求が対象にした足（出力の `Observation.subject` と、追い越し
      の対象系列の材料。D05 §6.7・§6.10）。
    - `latest_requests`: 使用箇所・系列ごとのいちばん新しい足の要求（追い越しの押しのけた側）。
    """

    component_states: Mapping[str, object] = field(default_factory=dict)
    latest_outputs: Mapping[OutputRef, OutputRecord[object]] = field(default_factory=dict)
    opportunities: tuple[OpportunityLifecycle, ...] = ()
    last_batch_id: EventId | None = None
    run_end_seen: bool = False
    output_history: Mapping[OutputRef, tuple[RetainedOutput, ...]] = field(default_factory=dict)
    waiting: tuple[WaitingRequest, ...] = ()
    request_subjects: Mapping[RequestId, BarKey] = field(default_factory=dict)
    latest_requests: _LatestRequests = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 可変参照はランタイム1インスタンスにつき1つだけ（D05 §1・§6.5）。対応表は読み取り
        # 専用の写しにして持ち、`state` を読んだ側が保持や履歴を書き換えられないようにする。
        for name in (
            "component_states",
            "latest_outputs",
            "output_history",
            "request_subjects",
            "latest_requests",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


class _EvaluationFailure(Exception):
    """評価の失敗を `step` の入口まで運ぶための内部例外（外へは出さない）。"""

    def __init__(self, reason: Reason) -> None:
        super().__init__(str(reason))
        self.reason = reason


def _data_error(cause: str) -> Reason:
    """失敗理由（D05 §6.4）。詳細型は市場データ向けなので添えない。"""
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
        """状態の初期値を宣言から組み立てる（D05 §6.5、D04 §9.1）。"""
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
            # 終端する機会がもう無い（末尾の合図は1 run に1回）。
            raise KernelValueError(
                f"batch {batch.batch_id} arrived after the end-of-run batch; a run has no"
                " decision points left once its opportunities have been terminated (D05 §6.1)"
            )
        phases = {name: batch.phases.by_name(name) for name in PHASE_NAMES}

        if batch.is_run_end:
            return self._run_end(batch, phases[PHASE_RUN_END])
        return _StepRun(self, batch, phases).execute()

    def _run_end(self, batch: PublicationBatch, phase: PhaseRank) -> RuntimeStepResult:
        """run 末尾に残った取引機会と待機要求を閉じる（D05 §6.1・§7.2 の遷移9）。

        起動判定・評価・出力の送出は行わない。非終端の取引機会を機会の連番の昇順に
        `RUN_END` で終端し、**その後に**待機中の要求を要求 ID の昇順に `Skipped`（まだ足りて
        いなかった入力の診断）で決着させ、`WaitEvent(RUN_END_CLOSED)` を1件ずつ残す。期限には
        到達していないので `DEADLINE_REACHED` を使わず、`on_deadline` にも従わない（run が
        終わっただけであり、データ誤りとして集計させない）。部品は呼ばない。
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
        evaluations: list[EvaluationRecord] = []
        wait_events: list[WaitEvent] = []
        for waiting in sorted(self.state.waiting, key=lambda item: item.request.request_id.seq):
            at = ProcessingPoint(time=batch.decision_time, phase=phase, sequence=sequence)
            sequence += 1
            wait_events.append(
                WaitEvent(
                    request_id=waiting.request.request_id,
                    kind=WaitEventKind.RUN_END_CLOSED,
                    at=at,
                )
            )
            request = waiting.request
            evaluations.append(
                EvaluationRecord(
                    request_id=request.request_id,
                    evaluation_id=self._allocator.next(EvaluationId),
                    instance_id=request.instance_id,
                    trigger_names=request.trigger_names,
                    decision_time=batch.decision_time,
                    outcome=Skipped(diagnoses=waiting.missing),
                    target_interval=request.target_interval,
                    opportunity_id=request.opportunity_id,
                    position_id=request.position_id,
                )
            )
        self._state = replace(
            self.state,
            opportunities=tuple(lifecycles.values()),
            last_batch_id=batch.batch_id,
            run_end_seen=True,
            waiting=(),
            request_subjects={},
        )
        return RuntimeStepResult(
            evaluations=tuple(evaluations),
            transitions=tuple(transitions),
            wait_events=tuple(wait_events),
        )


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
    """足を宣言された項目へ射影する（D05 §6.3）。"""
    if market_field is MarketDataField.OPEN:
        return bar.open
    if market_field is MarketDataField.HIGH:
        return bar.high
    if market_field is MarketDataField.LOW:
        return bar.low
    if market_field is MarketDataField.CLOSE:
        return bar.close
    return bar.volume


def _resolved_window(
    window: BarsWindow | DurationWindow | None, input_name: str
) -> HistoryWindowView:
    """解決済みの窓を、市場データビューの受け口の形で返す（D05 §6.3 v1.4）。

    本数窓はコンパイラが整数へ解決している（D05 §5.3）ので、その整数だけを持つ
    `ResolvedBarsWindow` にして渡す。経過時間窓はそのままで受け口の形を満たす。
    """
    if window is None:  # pragma: no cover - コンパイル時に解決済み
        raise KernelValueError(f"input {input_name!r} has no resolved window")
    if isinstance(window, BarsWindow):
        if not isinstance(window.count, int):  # pragma: no cover - InputPlan が拒否する
            raise KernelValueError(
                f"input {input_name!r} still refers to a parameter for its window size"
                f" ({window.count}); the compiler resolves window sizes (D04 §12, D05 §5.3)"
            )
        return ResolvedBarsWindow(count=window.count)
    return window


def _missing_reason(value: object) -> MissingInputReason:
    reason = getattr(value, "reason", None)
    if isinstance(reason, MissingInputReason):
        return reason
    return MissingInputReason.INPUT_MISSING_OR_INVALID


def _value_of(payload: object) -> object:
    """出力記録の内容から、部品が返した内容を取り出す（D05 §6.7）。

    段階3 のランタイムは繰り返し参照する値を `Observation` で包むので、読む側（部品への
    入力、有効性の束縛）は包みを外して内容だけを使う。部品は `Observation` を受け取らない。
    """
    if isinstance(payload, Observation):
        return payload.value
    return payload


@dataclass(slots=True)
class _Resolution:
    """1回の評価の入力解決の途中結果（D05 §6.3）。

    要素と欠損を**接続元の位置**つきで持つ。遡り（D05 §6.9）で欠けた位置へ代わりの観測を
    差し込んでも、要素の並びが接続の宣言順のまま保たれるようにするためである（D04 §3）。
    """

    elements: dict[str, list[tuple[int, InputElement]]] = field(default_factory=dict)
    missing: dict[str, list[tuple[int, MissingInputDiagnosis]]] = field(default_factory=dict)

    def diagnoses(self, names: Sequence[str] | None = None) -> tuple[MissingInputDiagnosis, ...]:
        """欠損の診断を、入力の宣言順・接続元の順に並べて返す。"""
        out: list[MissingInputDiagnosis] = []
        for name, items in self.missing.items():
            if names is not None and name not in names:
                continue
            out.extend(diagnosis for _, diagnosis in items)
        return tuple(out)

    def inputs(self) -> ResolvedInputs:
        """欠けた入力を除いて、部品へ渡す形にする。"""
        by_name: dict[str, tuple[InputElement, ...]] = {}
        for name, items in self.elements.items():
            if name in self.missing:
                continue
            by_name[name] = tuple(element for _, element in sorted(items, key=lambda i: i[0]))
        return ResolvedInputs(by_name=by_name)


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
        self._now: UtcTime = batch.decision_time
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
        self._wait_events: list[WaitEvent] = []
        self._management: list[ManagementRequest] = []
        self._proposals: list[EntryProposal] = []
        self._lifecycles: dict[OpportunityId, OpportunityLifecycle] = {
            item.opportunity_id: item for item in state.opportunities
        }
        self._component_states: dict[str, object] = dict(state.component_states)
        self._latest_outputs: dict[OutputRef, OutputRecord[object]] = dict(state.latest_outputs)
        self._output_history: dict[OutputRef, tuple[RetainedOutput, ...]] = dict(
            state.output_history
        )
        self._waiting: dict[RequestId, WaitingRequest] = {
            item.request.request_id: item for item in state.waiting
        }
        self._subjects: dict[RequestId, BarKey] = dict(state.request_subjects)
        self._latest_requests: dict[tuple[str, SeriesId], tuple[BarKey, RequestId]] = dict(
            state.latest_requests
        )
        self._superseded: dict[str, list[tuple[WaitingRequest, SeriesId, WaitEvent]]] = {}
        self._emitted: set[OutputRef] = set()
        self._deliveries: dict[OutputRef, list[OutputRecord[object]]] = {}
        self._origins: dict[
            OutputId,
            tuple[Interval | None, OpportunityId | None, PositionId | None, BarKey | None],
        ] = {}
        self._role_intents: dict[OpportunityId, OutputRecord[object]] = {}
        self._role_protections: dict[OpportunityId, OutputRecord[object]] = {}
        self._failed = False

    # --- 進行 --------------------------------------------------------------

    def execute(self) -> RuntimeStepResult:
        """1回ぶんの評価を行う（D05 §6.2 の手順1〜10）。"""
        self._apply_admissions()
        self._lifecycle_check()
        for component in self._compiled.components:
            if self._failed:
                break
            self._evaluate_component(component)
        self._settle_superseded(None)
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
            wait_events=tuple(self._wait_events),
        )

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value

    def _point(self, phase_name: str) -> ProcessingPoint:
        return ProcessingPoint(
            time=self._now,
            phase=self._phases[phase_name],
            sequence=self._next_sequence(),
        )

    def _commit(self) -> None:
        waiting_ids = set(self._waiting)
        self._evaluator._state = RuntimeState(
            component_states=self._component_states,
            latest_outputs=self._latest_outputs,
            opportunities=tuple(self._lifecycles.values()),
            last_batch_id=self._batch.batch_id,
            run_end_seen=self._evaluator.state.run_end_seen,
            output_history=self._output_history,
            waiting=tuple(self._waiting[key] for key in sorted(waiting_ids, key=lambda i: i.seq)),
            request_subjects={
                key: value for key, value in self._subjects.items() if key in waiting_ids
            },
            latest_requests=self._latest_requests,
        )

    def _phase_of(self, component: CompiledComponent) -> str:
        """使用箇所の評価が属するフェーズの名前（本モジュール冒頭の「評価がどのフェーズに属するか」）。"""
        if self._batch.runtime_events or self._batch.admissions:
            return PHASE_POST_FILL_EVALUATION
        roles = self._compiled.roles
        instance = component.instance_id
        if roles.market_state is not None and roles.market_state.instance_id == instance:
            return PHASE_P2_MARKET_STATE
        if roles.trigger.instance_id == instance:
            return PHASE_P3_TRIGGER
        if roles.execution_filter is not None and roles.execution_filter.instance_id == instance:
            return PHASE_P4_CONFIRMATION
        fifth = [roles.order, roles.protection, roles.exit]
        if any(ref is not None and ref.instance_id == instance for ref in fifth):
            return PHASE_P5_ORDER_INTENT
        return PHASE_P1_FEATURE

    # --- 受付結果の適用（遷移5〜7） ----------------------------------------

    def _apply_admissions(self) -> None:
        """受付結果の通知を次の `step` の入口で適用する（D05 §7.2 の遷移5〜7、v1.3）。"""
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

    # --- ライフサイクル検査（D05 §6.8 の手順1・2、§6.10） -------------------

    def _lifecycle_check(self) -> None:
        """待機中の要求を、期限 → 追い越し → 失効 → 到着の順に検査する（D05 §6.8）。

        規則(c)（Q29 決定）: 同じ判断時点で2つ以上が成立したら、先に成立したものだけで決着
        させ、後ろの判定は行わない。**市場データ参照の到着だけ**をここで判定し、出力参照の
        到着は評価の段で評価順に沿って判定する（D05 §6.8 の手順1）。
        """
        closed = frozenset(closure.bar_key.series for closure in self._batch.scheduled_closes)
        published_series = {key.series for key in self._batch.available_bars}
        for request_id in sorted(self._waiting, key=lambda item: item.seq):
            if self._failed:
                return
            waiting = self._waiting[request_id]
            waiting = replace(waiting, deadline_at=tick_deadline(waiting.deadline_at, closed))
            self._waiting[request_id] = waiting
            subject = self._subjects.get(request_id)
            target_series = None if subject is None else subject.series
            superseded = supersedes(
                waiting, target_series, self._batch.available_bars
            ) and self._newer_request_exists(waiting, target_series)
            verdict = judge_lifecycle(
                deadline_reached=deadline_reached(waiting.deadline_at, self._now),
                superseded=superseded,
                invalidated=self._opportunity_ended(waiting),
            )
            if verdict is LifecycleVerdict.DEADLINE:
                self._close_on_deadline(waiting)
            elif verdict is LifecycleVerdict.SUPERSEDED:
                assert target_series is not None  # noqa: S101 - supersedes() が保証する
                self._close_on_supersession(waiting, target_series)
            elif verdict is LifecycleVerdict.INVALIDATED:
                raise KernelValueError(
                    f"waiting request {request_id} carries an opportunity that has ended; how"
                    " such a waiting request is settled is not decided yet (D05 §6.8 step 2"
                    " names the check but not the outcome)"
                )
            else:
                self._record_market_arrivals(waiting, published_series)

    def _newer_request_exists(
        self, waiting: WaitingRequest, target_series: SeriesId | None
    ) -> bool:
        """押しのける側の要求（固定した足より新しい足の要求）があるか、この `step` で生まれるか。

        追い越しは「新しい足の要求が古い足の要求を押しのける」後着優先であり（D05 §6.10 の
        2・3）、押しのけた側の要求 ID を記録する。その要求は、足の終了予定がこの判断時点に
        あるなら評価の段で作られる（新しい要求を作るのは追い越しの判定より後。同4）。
        """
        pinned = pinned_target_bar(waiting, target_series)
        if pinned is None or target_series is None:
            return False
        instance = waiting.request.instance_id
        latest = self._latest_requests.get((instance, target_series))
        if latest is not None and pinned.bar_start < latest[0].bar_start:
            return True
        component = self._compiled.component(instance)
        triggered = any(
            isinstance(trigger, OnBarClose) and trigger.series == target_series
            for trigger in component.triggers
        )
        return triggered and any(
            closure.bar_key.series == target_series and pinned.bar_start < closure.bar_key.bar_start
            for closure in self._batch.scheduled_closes
        )

    def _opportunity_ended(self, waiting: WaitingRequest) -> bool:
        """受信済みの取引機会が終わっているか（失効の判定の材料。D05 §6.8 の手順2）。"""
        if waiting.opportunity is None:
            return False
        lifecycle = self._lifecycles.get(waiting.opportunity.opportunity_id)
        return lifecycle is not None and not lifecycle.is_active

    def _close_on_deadline(self, waiting: WaitingRequest) -> None:
        """期限に到達した要求を `on_deadline` に従って決着させる（D05 §6.8）。"""
        request = waiting.request
        self._wait_events.append(
            WaitEvent(
                request_id=request.request_id,
                kind=WaitEventKind.DEADLINE_REACHED,
                at=self._point(PHASE_OPPORTUNITY_LIFECYCLE),
            )
        )
        del self._waiting[request.request_id]
        if waiting.on_deadline is WaitDeadlineAction.ERROR:
            self._record(request, Failed(reason=_data_error("wait deadline reached")))
            self._failed = True
            return
        self._record(request, Skipped(diagnoses=waiting.missing))

    def _close_on_supersession(self, waiting: WaitingRequest, series: SeriesId) -> None:
        """追い越された要求を閉じる（D05 §6.10、`on_superseded=EXPIRE_REQUEST`）。

        出来事はこの処理点に刻む。評価記録（`Superseded(by_request_id)`）は、押しのけた側の
        要求 ID が決まった時点（その使用箇所の評価の段で新しい要求を作った直後）に残す。
        """
        request = waiting.request
        event = WaitEvent(
            request_id=request.request_id,
            kind=WaitEventKind.SUPERSEDED,
            at=self._point(PHASE_OPPORTUNITY_LIFECYCLE),
            reason=Reason(code=ReasonCode.REQUEST_SUPERSEDED),
        )
        self._wait_events.append(event)
        del self._waiting[request.request_id]
        self._superseded.setdefault(request.instance_id, []).append((waiting, series, event))

    def _settle_superseded(self, instance_id: str | None) -> None:
        """追い越した要求の評価記録を残す（`None` なら残っているものをすべて）。

        押しのけた側の要求が**実際に作られていること**（固定した足より新しい足の要求）を
        確かめてから記録する。この `step` がその使用箇所の番より前に失敗して新しい要求が
        作られなかった場合は、追い越しを取り消して待機に戻す（出来事も残さない）。押しのけた
        側を持たない `Superseded` を残すと、要求の連鎖が判断履歴で壊れるためである。
        """
        names = list(self._superseded) if instance_id is None else [instance_id]
        for name in names:
            for waiting, series, event in self._superseded.pop(name, []):
                latest = self._latest_requests.get((name, series))
                pinned = pinned_target_bar(waiting, series)
                if latest is None or pinned is None or not pinned.bar_start < latest[0].bar_start:
                    self._wait_events.remove(event)
                    self._waiting[waiting.request.request_id] = waiting
                    continue
                self._record(waiting.request, Superseded(by_request_id=latest[1]))

    def _record_market_arrivals(self, waiting: WaitingRequest, published: set[SeriesId]) -> None:
        """市場データ参照の入力の到着を記録する（D05 §6.8 の手順1・3）。

        その入力が指す系列の足が公開されたら届いたとみなす。別の系列の足の到着は再開の理由に
        しない。同じ処理点で届いた入力は1件の `INPUT_ARRIVED` にまとめる（T02 §14 #13）。
        """
        arrived: list[str] = []
        remaining: list[MissingInputDiagnosis] = []
        for diagnosis in waiting.missing:
            source = diagnosis.source
            if isinstance(source, ResolvedMarketSource) and source.series in published:
                if diagnosis.input_name not in arrived:
                    arrived.append(diagnosis.input_name)
                continue
            remaining.append(diagnosis)
        if not arrived:
            return
        self._wait_events.append(
            WaitEvent(
                request_id=waiting.request.request_id,
                kind=WaitEventKind.INPUT_ARRIVED,
                at=self._point(PHASE_OPPORTUNITY_LIFECYCLE),
                arrived=tuple(arrived),
            )
        )
        self._waiting[waiting.request.request_id] = replace(waiting, missing=tuple(remaining))

    # --- 起動判定と評価要求（手順1〜3） -------------------------------------

    def _build_requests(
        self, component: CompiledComponent
    ) -> list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]]:
        """起動した起動条件から評価要求を作る（D05 §6.2 の手順1〜3）。

        足の確定は**対象区間が同じものだけを1件に集約**し（Q6 決定）、イベントは配送1件・
        通知1件につき1要求を作る。
        """
        requests: list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]] = []
        requests.extend(self._bar_close_requests(component))
        requests.extend(self._input_event_requests(component))
        requests.extend(self._runtime_event_requests(component))
        for request, _ in requests:
            subject = self._subjects.get(request.request_id)
            if subject is None:
                continue
            key = (request.instance_id, subject.series)
            latest = self._latest_requests.get(key)
            if latest is None or latest[0].bar_start < subject.bar_start:
                self._latest_requests[key] = (subject, request.request_id)
        return requests

    def _bar_close_requests(
        self, component: CompiledComponent
    ) -> list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]]:
        by_interval: dict[Interval, list[tuple[str, BarKey]]] = {}
        for trigger in component.triggers:
            if not isinstance(trigger, OnBarClose):
                continue
            for closure in self._batch.scheduled_closes:
                if closure.bar_key.series != trigger.series:
                    continue
                by_interval.setdefault(closure.interval, []).append((trigger.name, closure.bar_key))
        out: list[tuple[EvaluationRequest, dict[str, tuple[OutputRecord[object], ...]]]] = []
        for interval in sorted(by_interval, key=lambda item: str(item.start)):
            entries = by_interval[interval]
            request = EvaluationRequest(
                request_id=self._allocator.next(RequestId),
                instance_id=component.instance_id,
                trigger_names=tuple(sorted(name for name, _ in entries)),
                decision_time=self._now,
                target_interval=interval,
            )
            # 観測した足（`Observation.subject`）は対象区間を与えた足（D05 §6.7）。区間が同じで
            # 系列の違う起動を集約したときは、系列の正規表記がいちばん小さい足を採る。
            keys = sorted({key for _, key in entries}, key=lambda key: str(key.series))
            self._subjects[request.request_id] = keys[0]
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
                    interval, opportunity_id, position_id, subject = self._origins.get(
                        record.output_id, (None, None, None, None)
                    )
                    if isinstance(record.payload, Opportunity):
                        opportunity_id = record.payload.opportunity_id
                    request = EvaluationRequest(
                        request_id=self._allocator.next(RequestId),
                        instance_id=component.instance_id,
                        trigger_names=(trigger.name,),
                        decision_time=self._now,
                        target_interval=interval,
                        opportunity_id=opportunity_id,
                        position_id=position_id,
                    )
                    if subject is not None:
                        self._subjects[request.request_id] = subject
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
                    decision_time=self._now,
                    target_interval=None,
                    opportunity_id=notice.opportunity_id,
                    position_id=notice.position_id,
                )
                out.append((request, {}))
        return out

    # --- 1つの使用箇所の評価（手順4〜9） -----------------------------------

    def _evaluate_component(self, component: CompiledComponent) -> None:
        """使用箇所1件の番に行うこと（D05 §6.2 の手順4、§6.8 の手順1・4・5、§6.10 の4）。

        新しい要求は追い越しの判定（ライフサイクル検査）より後に作る。追い越した要求の記録を
        残してから、待機中の要求の再開、新しい要求の評価の順に進む。
        """
        phase = self._phase_of(component)
        new_requests = self._build_requests(component)
        self._settle_superseded(component.instance_id)
        self._resume_waiting(component, phase)
        for request, delivered in new_requests:
            if self._failed:
                return
            self._evaluate_once(component, request, delivered, phase)

    def _resume_waiting(self, component: CompiledComponent, phase: str) -> None:
        """この使用箇所の待機要求のうち、入力がそろったものを再開する（D05 §6.8）。

        出力参照の入力は、**上流の使用箇所が同じ `step` の中でその出力を出したこと**で届いた
        とみなす。評価順は上流が先に来ることを保証しているので、上流が同じ `step` で再開して
        出力を出せば、下流の待機要求も同じ `step` で再開できる。
        """
        mine = sorted(
            (
                item
                for item in self._waiting.values()
                if item.request.instance_id == component.instance_id
            ),
            key=lambda item: item.request.request_id.seq,
        )
        for waiting in mine:
            if self._failed:
                return
            arrived: list[str] = []
            remaining: list[MissingInputDiagnosis] = []
            for diagnosis in waiting.missing:
                source = diagnosis.source
                if isinstance(source, ResolvedOutputSource) and source.ref in self._emitted:
                    if diagnosis.input_name not in arrived:
                        arrived.append(diagnosis.input_name)
                    continue
                remaining.append(diagnosis)
            if arrived:
                self._wait_events.append(
                    WaitEvent(
                        request_id=waiting.request.request_id,
                        kind=WaitEventKind.INPUT_ARRIVED,
                        at=self._point(phase),
                        arrived=tuple(arrived),
                    )
                )
                waiting = replace(waiting, missing=tuple(remaining))
                self._waiting[waiting.request.request_id] = waiting
            if waiting.missing:
                # 部分的に届いた入力で評価を始めない（上位設計書 §4.3.14）。
                continue
            self._evaluate_once(component, waiting.request, {}, phase, resumed=waiting)

    def _evaluate_once(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        delivered: Mapping[str, tuple[OutputRecord[object], ...]],
        phase: str,
        *,
        resumed: WaitingRequest | None = None,
    ) -> None:
        """評価要求1件を評価する（D05 §6.2 の手順5〜8、§6.3 の欠損方針、§6.8 の手順4・5）。"""
        resolution = self._resolve_inputs(component, request, delivered, resumed)
        required = self._required_input_names(component, request.trigger_names)
        blocking = [name for name in resolution.missing if name in required]
        substitutions: tuple[SubstitutedInput, ...] = ()
        if blocking:
            strengths = [
                effective_strength(
                    _on_missing(component.input_plans[name]),
                    [diagnosis.reason for _, diagnosis in resolution.missing[name]],
                )
                for name in blocking
            ]
            strongest = strongest_policy(strengths)
            if strongest is PolicyStrength.WAIT_FOR_INPUT:
                self._wait(component, request, delivered, resolution, blocking, phase, resumed)
                return
            if strongest is PolicyStrength.USE_PREVIOUS:
                substituted = self._substitute(component, request, resolution, blocking, resumed)
                if substituted is not None:
                    substitutions = substituted
                    strongest = PolicyStrength.USE_PREVIOUS
                else:
                    strongest = PolicyStrength.SKIP_EVALUATION
            if strongest is not PolicyStrength.USE_PREVIOUS:
                self._mark_resumed(resumed, phase)
                if strongest is PolicyStrength.ERROR:
                    reason = _data_error(f"{blocking[0]}: missing")
                    self._record(request, Failed(reason=reason))
                    self._failed = True
                    return
                self._record(request, Skipped(diagnoses=resolution.diagnoses()))
                return
        self._mark_resumed(resumed, phase)
        inputs = resolution.inputs()
        evaluation_id = self._allocator.next(EvaluationId)
        try:
            self._check_alignment(component, inputs)
            result = self._call_component(component, inputs)
            output_ids = self._publish(
                component, request, evaluation_id, result, inputs, substitutions
            )
        except _EvaluationFailure as failure:
            self._record(request, Failed(reason=failure.reason), evaluation_id=evaluation_id)
            self._failed = True
            return
        if isinstance(self._implementation(component), StatefulImplementation):
            self._component_states[component.instance_id] = result.new_state
        self._record(
            request,
            Evaluated(output_ids=output_ids),
            evaluation_id=evaluation_id,
            substitutions=substitutions,
        )

    def _mark_resumed(self, resumed: WaitingRequest | None, phase: str) -> None:
        """待機から再開して決着することを記録し、待機から外す（D05 §6.8 の手順4・5）。"""
        if resumed is None:
            return
        request_id = resumed.request.request_id
        self._wait_events.append(
            WaitEvent(request_id=request_id, kind=WaitEventKind.RESUMED, at=self._point(phase))
        )
        self._waiting.pop(request_id, None)

    def _wait(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        delivered: Mapping[str, tuple[OutputRecord[object], ...]],
        resolution: _Resolution,
        blocking: Sequence[str],
        phase: str,
        resumed: WaitingRequest | None,
    ) -> None:
        """待機に入る、または待機を続ける（D05 §6.8）。

        期限は**最初に足りなくなった入力**（入力の宣言順で最初の、待機を宣言した入力）の宣言を
        使い、本数の期限を数える系列は規則(b)（Q28 決定）で決める。
        """
        missing = resolution.diagnoses(blocking)
        if resumed is not None:
            # 読み直しても足りなければ、同じ期限のまま待機を続ける（新しい記録は作らない）。
            self._waiting[request.request_id] = replace(resumed, missing=missing)
            return
        first = next(
            name
            for name in blocking
            if isinstance(_on_missing(component.input_plans[name]), WaitForInput)
        )
        policy = _on_missing(component.input_plans[first])
        assert isinstance(policy, WaitForInput)  # noqa: S101 - 直前の絞り込みが保証する
        market_series = [
            diagnosis.source.series
            for diagnosis in missing
            if isinstance(diagnosis.source, ResolvedMarketSource)
        ]
        trigger_series = [
            trigger.series
            for trigger in component.triggers
            if isinstance(trigger, OnBarClose) and trigger.name in request.trigger_names
        ]
        series = (
            deadline_series(market_series, trigger_series)
            if isinstance(policy.deadline, BarsDeadline)
            else None
        )
        deadline_at = resolve_deadline(policy.deadline, started=self._now, series=series)
        started_at = self._point(phase)
        opportunity = next(
            (
                record.payload
                for records in delivered.values()
                for record in records
                if isinstance(record.payload, Opportunity)
            ),
            None,
        )
        self._waiting[request.request_id] = WaitingRequest(
            request=request,
            missing=missing,
            started_at=started_at,
            deadline_at=deadline_at,
            on_deadline=policy.on_deadline,
            on_superseded=policy.on_superseded,
            pinned_bars=self._pin_bars(component),
            pinned_events={
                name: tuple(
                    EventDelivery(payload=record.payload, source_output_id=record.output_id)
                    for record in records
                )
                for name, records in delivered.items()
            },
            opportunity=opportunity,
        )
        self._record(request, Waiting(diagnoses=missing, deadline_at=deadline_at))
        self._wait_events.append(
            WaitEvent(request_id=request.request_id, kind=WaitEventKind.WAIT_STARTED, at=started_at)
        )

    def _pin_bars(self, component: CompiledComponent) -> dict[str, BarKey]:
        """市場データ参照の入力ごとに、期待される最新足を固定する（D05 §6.8）。

        履歴窓でも**当該足を除く指定を適用する前の基準足**を固定する（再開時に同じ指定を
        もう一度渡すため）。出力参照・現在状態の入力は固定しない（T02 §14 #16）。固定する足は
        入力名ごとに1本なので、市場データの接続元を2つ以上持つ入力は待機に入れない。
        """
        pinned: dict[str, BarKey] = {}
        for name, plan in component.input_plans.items():
            if not isinstance(plan.read_spec, (LatestAvailable, HistoryWindow)):
                continue
            markets = [src for src in plan.sources if isinstance(src, ResolvedMarketSource)]
            if not markets:
                continue
            if len({source.series for source in markets}) > 1:
                # 同じ系列の複数の項目（高値と安値など）は1本の足の鍵で固定できる。系列が違う
                # 接続元は入力名ごとに1本という待機記録の形（D05 §3）に収まらない。
                raise KernelValueError(
                    f"{component.instance_id}.{name} reads several market series; a waiting"
                    " request pins one bar per input name (D05 §3 WaitingRequest)"
                )
            key = self._market_data.expected_latest_key(markets[0].series, self._now)
            if key is not None:
                pinned[name] = key
        return pinned

    def _implementation(self, component: CompiledComponent) -> ComponentImplementation:
        return self._registration(component).implementation

    def _registration(self, component: CompiledComponent) -> ComponentRegistration:
        registration = self._registry.get(
            ContractKey(component.contract_ref.component_id, component.contract_ref.version)
        )
        if registration is None:  # pragma: no cover - コンパイル時に解決済み
            raise KernelValueError(f"component {component.instance_id} is no longer registered")
        return registration

    def _call_component(
        self, component: CompiledComponent, inputs: ResolvedInputs
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
        return result

    def _record(
        self,
        request: EvaluationRequest,
        outcome: EvaluationOutcome,
        *,
        evaluation_id: EvaluationId | None = None,
        substitutions: tuple[SubstitutedInput, ...] = (),
    ) -> None:
        """評価記録を1件残す（D05 §6.4）。

        判断時刻はこの `step` の判断時刻である。待機から再開した評価は**再開した判断時刻**を
        持ち、元の対象足の終了時刻へ遡らせない（D05 §6.8 の手順5）。待機を始めた時刻は
        `WaitEvent(WAIT_STARTED)` が持つ。評価識別子は毎回新しく採番する。
        """
        self._evaluations.append(
            EvaluationRecord(
                request_id=request.request_id,
                evaluation_id=(
                    self._allocator.next(EvaluationId) if evaluation_id is None else evaluation_id
                ),
                instance_id=request.instance_id,
                trigger_names=request.trigger_names,
                decision_time=self._now,
                outcome=outcome,
                target_interval=request.target_interval,
                opportunity_id=request.opportunity_id,
                position_id=request.position_id,
                substitutions=substitutions,
            )
        )

    # --- 入力解決（D05 §6.3・§6.7・§6.8・§6.12） -----------------------------

    def _required_input_names(
        self, component: CompiledComponent, trigger_names: Sequence[str]
    ) -> frozenset[str]:
        """今回の起動で必須になる入力（D05 §6.3 v1.3）。

        **宣言があればその和集合、1件も無ければ接続済みの入力すべて**を必須とする。
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
        resumed: WaitingRequest | None,
    ) -> _Resolution:
        """入力を解決する（D05 §6.3）。待機から再開した評価は固定した足で読み直す（§6.8）。"""
        resolution = _Resolution()
        for input_name, plan in component.input_plans.items():
            read_spec = plan.read_spec
            pinned = None if resumed is None else resumed.pinned_bars.get(input_name)
            if isinstance(read_spec, LatestAvailable):
                self._resolve_latest(plan, read_spec, pinned, resolution)
            elif isinstance(read_spec, HistoryWindow):
                self._resolve_history(plan, read_spec, request, pinned, resolution)
            elif isinstance(read_spec, DeliveredEvent):
                self._resolve_delivered(plan, delivered, resumed, resolution)
            elif isinstance(read_spec, CurrentContext):
                self._resolve_context(plan, request, resolution)
            else:  # pragma: no cover - 4区分しかない
                raise KernelValueError(f"unsupported read spec: {read_spec!r}")
        return resolution

    def _add(self, resolution: _Resolution, name: str, index: int, element: InputElement) -> None:
        resolution.elements.setdefault(name, []).append((index, element))

    def _miss(
        self,
        resolution: _Resolution,
        name: str,
        index: int,
        source: ResolvedSource,
        reason: MissingInputReason,
    ) -> None:
        resolution.missing.setdefault(name, []).append(
            (index, MissingInputDiagnosis(name, source, reason))
        )

    def _upstream_pending(self, ref: OutputRef) -> bool:
        """上流がこの出力を**まだ出していない**か（D05 §6.8 の「待機の伝播」）。

        上流が待機中なら、その出力を読む入力は「まだ出ていない」として扱い、下流自身が宣言
        した欠損方針に従う。ただし「まだ出ていない」のは、待機中の問いが**保持済みの最新
        出力より新しい足**についてのものだけである。追い越されても待ち続ける設定
        （`KEEP_WAITING`、D05 §6.10）で古い足の要求が残っていても、新しい足の出力が既に
        あればそれを読む（最新1件の読み方の意味を変えない。D05 §6.5）。同じ `step` の中で
        上流が出力を出していれば読める。観測した足が定まらない待機・出力は比べられないので、
        待機中として扱う。
        """
        if ref in self._emitted:
            return False
        latest = self._latest_outputs.get(ref)
        latest_payload = None if latest is None else latest.payload
        latest_subject = latest_payload.subject if isinstance(latest_payload, Observation) else None
        for item in self._waiting.values():
            if item.request.instance_id != ref.instance_id:
                continue
            asked = self._subjects.get(item.request.request_id)
            if asked is None or latest_subject is None:
                return True
            if latest_subject.bar_start < asked.bar_start:
                return True
        return False

    def _resolve_latest(
        self,
        plan: InputPlan,
        read_spec: LatestAvailable,
        pinned: BarKey | None,
        resolution: _Resolution,
    ) -> None:
        name = plan.input_name
        for index, source in enumerate(plan.sources):
            if isinstance(source, ResolvedMarketSource):
                if pinned is not None:
                    bar = self._market_data.bar(source.series, pinned.bar_start, self._now)
                else:
                    bar = self._market_data.latest_available(source.series, self._now)
                if not isinstance(bar, Bar):
                    self._miss(resolution, name, index, source, _missing_reason(bar))
                    continue
                freshness = self._market_data.freshness(source.series, bar)
                if self._too_old(freshness, read_spec.max_age):
                    self._miss(resolution, name, index, source, MissingInputReason.MAX_AGE_EXCEEDED)
                    continue
                self._add(resolution, name, index, _sample(bar, source, freshness))
            elif isinstance(source, ResolvedOutputSource):
                record = self._latest_outputs.get(source.ref)
                if record is None or self._upstream_pending(source.ref):
                    self._miss(
                        resolution, name, index, source, MissingInputReason.INPUT_MISSING_OR_INVALID
                    )
                    continue
                sample = _output_sample(record, source)
                if self._too_old(sample.freshness_time, read_spec.max_age):
                    self._miss(resolution, name, index, source, MissingInputReason.MAX_AGE_EXCEEDED)
                    continue
                self._add(resolution, name, index, sample)
            else:  # pragma: no cover - コンパイル時に拒否される組合せ
                self._miss(
                    resolution, name, index, source, MissingInputReason.INPUT_MISSING_OR_INVALID
                )

    def _resolve_history(
        self,
        plan: InputPlan,
        read_spec: HistoryWindow,
        request: EvaluationRequest,
        pinned: BarKey | None,
        resolution: _Resolution,
    ) -> None:
        name = plan.input_name
        for index, source in enumerate(plan.sources):
            if isinstance(source, ResolvedOutputSource):
                self._resolve_output_history(plan, read_spec, request, index, source, resolution)
                continue
            if not isinstance(source, ResolvedMarketSource):  # pragma: no cover
                self._miss(
                    resolution, name, index, source, MissingInputReason.INPUT_MISSING_OR_INVALID
                )
                continue
            window = _resolved_window(plan.resolved_window, name)
            if pinned is not None:
                bars = self._market_data.history_ending_at(
                    source.series,
                    window,
                    pinned.bar_start,
                    self._now,
                    end_offset_bars=read_spec.exclude_latest_bars,
                )
            else:
                bars = self._market_data.history(
                    source.series,
                    window,
                    self._now,
                    end_offset_bars=read_spec.exclude_latest_bars,
                )
            if not isinstance(bars, tuple):
                self._miss(resolution, name, index, source, _missing_reason(bars))
                continue
            samples = tuple(
                _sample(bar, source, self._market_data.freshness(source.series, bar))
                for bar in bars
            )
            if samples and self._too_old(samples[-1].freshness_time, read_spec.max_age):
                self._miss(resolution, name, index, source, MissingInputReason.MAX_AGE_EXCEEDED)
                continue
            self._add(resolution, name, index, ValueWindow(samples=samples))

    def _resolve_output_history(
        self,
        plan: InputPlan,
        read_spec: HistoryWindow,
        request: EvaluationRequest,
        index: int,
        source: ResolvedOutputSource,
        resolution: _Resolution,
    ) -> None:
        """上流の出力を履歴窓で読む（D05 §6.12 の「どう読むか」、Q21 決定）。

        窓の末尾はその評価要求の対象区間の終了時刻で決める。待機から再開した評価も同じ基準
        なので、待った評価と待たなかった評価の答えが一致する。
        """
        name = plan.input_name
        window = plan.resolved_window
        if request.target_interval is None or not isinstance(window, BarsWindow):
            raise KernelValueError(  # pragma: no cover - コンパイラの検査 f が拒否する
                f"{name}: an output history window needs a bar count and a target interval"
                " (D05 §5.6 check f)"
            )
        if not isinstance(window.count, int):  # pragma: no cover - InputPlan が拒否する
            raise KernelValueError(f"{name}: unresolved window size {window.count}")
        rows = self._output_history.get(source.ref, ())
        selected = window_ending_at(
            rows, request.target_interval.end, window.count, read_spec.exclude_latest_bars
        )
        if selected is None or self._upstream_pending(source.ref):
            self._miss(resolution, name, index, source, MissingInputReason.INPUT_MISSING_OR_INVALID)
            return
        samples = tuple(_output_sample(row.record, source) for row in selected)
        if self._too_old(samples[-1].freshness_time, read_spec.max_age):
            self._miss(resolution, name, index, source, MissingInputReason.MAX_AGE_EXCEEDED)
            return
        self._add(resolution, name, index, ValueWindow(samples=samples))

    def _resolve_delivered(
        self,
        plan: InputPlan,
        delivered: Mapping[str, tuple[OutputRecord[object], ...]],
        resumed: WaitingRequest | None,
        resolution: _Resolution,
    ) -> None:
        name = plan.input_name
        if resumed is not None:
            # 再開時には再配送されないので、待機記録が持つ配送を使う（D05 §6.8 の手順4）。
            events = resumed.pinned_events.get(name, ())
        else:
            events = tuple(
                EventDelivery(payload=record.payload, source_output_id=record.output_id)
                for record in delivered.get(name, ())
            )
        if not events:
            self._miss(
                resolution,
                name,
                0,
                plan.sources[0],
                MissingInputReason.INPUT_MISSING_OR_INVALID,
            )
            return
        for index, event in enumerate(events):
            self._add(resolution, name, index, event)

    def _resolve_context(
        self, plan: InputPlan, request: EvaluationRequest, resolution: _Resolution
    ) -> None:
        """現在状態の入力は、再開した場合も**今回の判断時刻**で読む（D05 §6.8 の手順4）。"""
        name = plan.input_name
        for index, source in enumerate(plan.sources):
            if not isinstance(source, ResolvedContextSource):  # pragma: no cover
                continue
            if source.target is RuntimeTarget.POSITION:
                payload = self._context.position_context(self._now, request.position_id)
            else:
                payload = self._context.account_context(self._now)
            if payload is None:
                self._miss(
                    resolution, name, index, source, MissingInputReason.INPUT_MISSING_OR_INVALID
                )
                continue
            self._add(resolution, name, index, ContextSnapshot(payload=payload, read_at=self._now))

    def _substitute(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        resolution: _Resolution,
        blocking: Sequence[str],
        resumed: WaitingRequest | None,
    ) -> tuple[SubstitutedInput, ...] | None:
        """過去値へ遡る（D05 §6.9）。1件でも遡れなければ `None`（見送りになる）。

        欠けた期待足の1本手前から古い側へ、上限（`InputPlan.resolved_max_lookback`）まで
        たどって最初の有効な足を使う。遡って選んだ足も `max_age` の判定を受ける。許す欠損
        理由は宣言が選ぶ（上位設計書 §4.3.13「破損データや計算例外まで一律に過去値で隠さない」）。
        """
        del request
        found: list[tuple[str, int, SubstitutedInput, ValueSample]] = []
        for name in blocking:
            plan = component.input_plans[name]
            policy = _on_missing(plan)
            read_spec = plan.read_spec
            lookback = plan.resolved_max_lookback
            if not isinstance(policy, UsePrevious) or not isinstance(read_spec, LatestAvailable):
                return None
            if lookback is None:  # pragma: no cover - コンパイラが解決する
                return None
            for index, diagnosis in resolution.missing[name]:
                source = diagnosis.source
                if diagnosis.reason not in policy.allowed_reasons:
                    return None
                if not isinstance(source, ResolvedMarketSource):
                    return None
                pinned = None if resumed is None else resumed.pinned_bars.get(name)
                expected = pinned or self._market_data.expected_latest_key(source.series, self._now)
                if expected is None:
                    return None
                bar = self._market_data.previous_available(
                    source.series,
                    expected.bar_start,
                    self._now,
                    max_lookback=_resolved_window(lookback, name),
                )
                if not isinstance(bar, Bar):
                    return None
                freshness = self._market_data.freshness(source.series, bar)
                if self._too_old(freshness, read_spec.max_age):
                    return None
                found.append(
                    (
                        name,
                        index,
                        SubstitutedInput(
                            input_name=name,
                            source_index=index,
                            source=source,
                            freshness_time=freshness,
                            reason=diagnosis.reason,
                            used_bar_key=bar.key,
                        ),
                        _sample(bar, source, freshness),
                    )
                )
        for name, index, _, sample in found:
            self._add(resolution, name, index, sample)
        for name in blocking:
            resolution.missing.pop(name, None)
        return tuple(item for _, _, item, _ in found)

    def _too_old(self, freshness: UtcTime, max_age: object) -> bool:
        """鮮度上限の判定はランタイムが行う（D03 §6.2 が委ねた）。"""
        if max_age is None:
            return False
        return (self._now - freshness) > max_age  # type: ignore[operator]

    def _check_alignment(self, component: CompiledComponent, inputs: ResolvedInputs) -> None:
        """観測区間の一致を、入力をまたいだ同じ位置の要素で突き合わせる（D05 §6.7）。

        要素数（最新1件なら 1、履歴窓なら本数）が等しく、各位置の観測区間が等しいこと。
        違反は欠損ではなく宣言と接続の食い違いなので失敗にする。**同じ入力の中で要素ごとに
        区間が違うのは正常である**（窓の要素は1本ずつ別の足）。
        """
        contract = self._registration(component).contract
        for requirement in contract.temporal_constraints.alignment:
            sequences: list[tuple[Interval | None, ...]] = []
            for name in requirement.input_names:
                elements = inputs.by_name.get(name)
                if elements is None:
                    continue
                sequences.append(_observation_intervals(elements))
            if len({len(item) for item in sequences}) > 1 or any(
                item != sequences[0] for item in sequences[1:]
            ):
                raise _EvaluationFailure(
                    _data_error(
                        f"{component.instance_id}: inputs {requirement.input_names} do not"
                        " observe the same intervals (D05 §6.7)"
                    )
                )

    # --- 戻り値の検査・取引機会・付番（手順5〜8） --------------------------

    def _publish(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        evaluation_id: EvaluationId,
        result: ComponentOutputs,
        inputs: ResolvedInputs,
        substitutions: tuple[SubstitutedInput, ...] = (),
    ) -> tuple[OutputId, ...]:
        """戻り値を検査し、取引機会を組み立ててから付番・送出する（D05 §6.2）。"""
        contract = self._registration(component).contract.outputs
        self._check_outputs(component, contract, result)

        # 手順6: 付番より先に取引機会を組み立てる。
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

        subject = self._subjects.get(request.request_id)
        observed = _substituted_observation(inputs, substitutions)
        freshness = _freshness_of(inputs, self._now)
        records: list[OutputRecord[object]] = []
        for output_name in sorted(result.outputs):
            spec = contract[output_name]
            payload: object = opportunities.get(output_name, result.outputs[output_name])
            if spec.kind is PortKind.VALUE:
                payload = self._observe(component, request, subject, freshness, payload, observed)
            producer = OutputRef(component.instance_id, output_name)
            record: OutputRecord[object] = OutputRecord(
                output_id=self._allocator.next(OutputId),
                evaluation_id=evaluation_id,
                producer=producer,
                decision_time=self._now,
                available_at=self._now,
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
                subject,
            )
            if spec.kind is PortKind.VALUE:
                self._keep_value(record)
            elif spec.kind is PortKind.EVENT and output_name not in withheld:
                self._deliveries.setdefault(producer, []).append(record)
            self._capture_role_output(producer, record, request)
        self._sink.emit(tuple(records))
        return tuple(record.output_id for record in records)

    def _observe(
        self,
        component: CompiledComponent,
        request: EvaluationRequest,
        subject: BarKey | None,
        freshness: UtcTime,
        value: object,
        observed: tuple[BarKey, Interval] | None = None,
    ) -> Observation[object]:
        """繰り返し参照する値を `Observation` で包む（D05 §6.7、Q13 決定）。

        包むかどうかは戦略の宣言に依らない（T02 §14 #17）。観測した足と対象区間は評価要求
        のもの、鮮度は入力ごとの代表の鮮度基準時刻の最小値である。

        過去値へ遡った評価（`observed` がある）では、観測した足と観測区間を**実際に読んだ
        古い足**のものにする（D05 §6.9 の末尾）。下流の `max_age` と観測区間の一致の判定に
        古さがそのまま伝わるようにするためである。
        """
        if observed is not None:
            return Observation(
                value=value,
                subject=observed[0],
                observation_interval=observed[1],
                freshness_time=freshness,
            )
        del component
        if subject is None or request.target_interval is None:
            # 対象区間を持たない評価（実行時イベントとその連鎖で起動した評価）は観測した足が
            # 定まらない。観測した足と区間を空にして包み、最新1件の保持だけを更新する
            # （D05 §6.12「観測した足が定まらない出力は積まない」、`RetainedOutput.subject`）。
            return Observation(
                value=value, subject=None, observation_interval=None, freshness_time=freshness
            )
        return Observation(
            value=value,
            subject=subject,
            observation_interval=request.target_interval,
            freshness_time=freshness,
        )

    def _keep_value(self, record: OutputRecord[object]) -> None:
        """最新1件と履歴を**同じ処理で同時に**更新する（D05 §6.5・§6.12）。"""
        producer = record.producer
        self._latest_outputs[producer] = record
        self._emitted.add(producer)
        payload = record.payload
        subject = payload.subject if isinstance(payload, Observation) else None
        interval = payload.observation_interval if isinstance(payload, Observation) else None
        self._output_history = retain(
            self._output_history,
            self._compiled.output_retention.by_output,
            RetainedOutput(record=record, subject=subject, observation_interval=interval),
        )

    def _check_outputs(
        self,
        component: CompiledComponent,
        contract_outputs: Mapping[str, OutputSpec],
        result: ComponentOutputs,
    ) -> None:
        """部品の戻り値を付番の前に検査する（D05 §6.2 の4点）。"""
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
            self._open(opportunity, self._take_snapshots())
            return opportunity, True
        if concurrency.on_new_trigger is OnNewTrigger.SUPERSEDE_EXISTING:
            replaceable = sorted(
                (item for item in active if item.is_replaceable),
                key=lambda item: item.supersession_key,
            )
            if replaceable:
                # 置換の前に新しい機会の束縛を固定する（D05 §7.3 v1.3）。
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
        """遷移1: 生成して有効にする（D05 §7.2）。"""
        at = self._point(PHASE_P3_TRIGGER)
        lifecycle = OpportunityLifecycle(
            opportunity=opportunity,
            state=OpportunityState.OPEN,
            created_at=at,
            created_decision_time=self._now,
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
            created_decision_time=self._now,
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
            payload = _value_of(record.payload)
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
        """継続成立を要求した条件を読み直す（ADR-0031、D05 §7.3）。"""
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
                payload = _value_of(record.payload)
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
        """保有管理の要求を、宛先の建玉とともに作る（D05 §6.2 の手順9、v1.3）。"""
        if request.position_id is None:
            raise KernelValueError(
                f"{request.instance_id} produced a management action without a position to"
                " address it to; in stage 2 the exit role is started by a position-opened"
                " notice, which carries the position (D05 §8)"
            )
        action = _value_of(record.payload)
        if getattr(action, "kind", None) not in ("SET_TAKE_PROFIT", "CLOSE_POSITION"):
            raise _EvaluationFailure(
                _data_error(f"{request.instance_id} did not produce a management action")
            )
        self._management.append(
            ManagementRequest(
                position_id=request.position_id,
                action=action,  # type: ignore[arg-type]
                decision_time=self._now,
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
            self._recheck_validity(PHASE_P5_ORDER_INTENT, only=opportunity_id)
            lifecycle = self._lifecycles[opportunity_id]
            if not lifecycle.is_replaceable:
                continue
            intent_record = self._role_intents[opportunity_id]
            protection_record = self._role_protections[opportunity_id]
            intent = _value_of(intent_record.payload)
            protection = _value_of(protection_record.payload)
            if not isinstance(intent, OrderIntent) or not isinstance(
                protection, ProtectionLevels
            ):  # pragma: no cover - 出力の型検査が保証する
                raise KernelValueError("role outputs must be an OrderIntent and ProtectionLevels")
            self._proposals.append(
                EntryProposal(
                    opportunity_id=opportunity_id,
                    order_intent=intent,
                    protection=protection,
                    decision_time=self._now,
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


# --- 補助 ----------------------------------------------------------------------


def _on_missing(plan: InputPlan) -> MissingInputPolicy:
    """入力の欠損方針（読み方が持つ `on_missing`）。持たない読み方は見送りと同じに扱う。"""
    policy = getattr(plan.read_spec, "on_missing", None)
    if isinstance(policy, (SkipEvaluation, ErrorPolicy, WaitForInput, UsePrevious)):
        return policy
    return SkipEvaluation()


def _sample(bar: Bar, source: ResolvedMarketSource, freshness: UtcTime) -> ValueSample:
    """市場データの足を射影した1件（D05 §6.3）。観測区間と鍵は射影元の足のもの（§6.7）。"""
    return ValueSample(
        payload=_project(bar, source.field),
        source=source,
        freshness_time=freshness,
        observation_interval=bar.interval,
        subject=bar.key,
    )


def _output_sample(record: OutputRecord[object], source: ResolvedOutputSource) -> ValueSample:
    """上流の出力を読んだ1件（D05 §6.7 の「読む側の扱い」）。

    内容は `Observation.value`、鮮度・観測区間・観測した足は `Observation` の同名の
    フィールドである。部品は `Observation` を受け取らない。
    """
    payload = record.payload
    if isinstance(payload, Observation):
        return ValueSample(
            payload=payload.value,
            source=source,
            freshness_time=payload.freshness_time,
            source_output_id=record.output_id,
            observation_interval=payload.observation_interval,
            subject=payload.subject,
        )
    return ValueSample(  # pragma: no cover - 繰り返し参照する値は常に包まれている
        payload=payload,
        source=source,
        freshness_time=record.decision_time,
        source_output_id=record.output_id,
    )


def _freshness_of(inputs: ResolvedInputs, default: UtcTime) -> UtcTime:
    """出力の鮮度基準時刻（D05 §6.7）。

    入力ごとに代表の鮮度基準時刻を1つ決め、その最小値を採る。代表は最新1件ならその値、
    履歴窓なら末尾（いちばん新しい要素）の値である。市場データも上流出力も読まない評価では
    判断時刻（`default`）。配送イベントと現在状態は鮮度基準時刻を持たないので数えない。
    """
    times: list[UtcTime] = []
    for elements in inputs.by_name.values():
        for element in elements:
            if isinstance(element, ValueSample):
                times.append(element.freshness_time)
            elif isinstance(element, ValueWindow):
                times.append(element.samples[-1].freshness_time)
    if not times:
        return default
    return min(times, key=lambda item: item.value)


def _substituted_observation(
    inputs: ResolvedInputs, substitutions: tuple[SubstitutedInput, ...]
) -> tuple[BarKey, Interval] | None:
    """遡って読んだ足のうち、いちばん古い足とその区間（D05 §6.9 の末尾）。

    遡った接続元が2つ以上あって読んだ足が違うときは、いちばん古い足を採る（古さを隠さない
    向き）。遡っていなければ `None`。
    """
    keys = [item.used_bar_key for item in substitutions if item.used_bar_key is not None]
    if not keys:
        return None
    oldest = min(keys, key=lambda key: key.bar_start.value)
    for elements in inputs.by_name.values():
        for element in elements:
            if (
                isinstance(element, ValueSample)
                and element.subject == oldest
                and element.observation_interval is not None
            ):
                return oldest, element.observation_interval
    return None  # pragma: no cover - 遡った足は必ず入力に入っている


def _observation_intervals(elements: tuple[InputElement, ...]) -> tuple[Interval | None, ...]:
    """観測区間の一致の検査に使う、入力の要素ごとの観測区間の並び（D05 §6.7）。"""
    out: list[Interval | None] = []
    for element in elements:
        if isinstance(element, ValueSample):
            out.append(element.observation_interval)
        elif isinstance(element, ValueWindow):
            out.extend(sample.observation_interval for sample in element.samples)
    return tuple(out)
