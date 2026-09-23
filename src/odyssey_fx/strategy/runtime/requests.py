"""評価要求・評価記録・解決済み入力・`step` の戻り値（D05 §6.2〜§6.4）。

## 評価要求と評価記録

「いつ・どの使用箇所を・何について評価したか」を表す。**起動した使用箇所ごとに評価記録が
必ず1件残る**。入力不足で評価しなかったことも記録に残す（上位設計書 §4.3.15）。評価要求
そのものは `step` の戻り値に入れないので、記録の側が対象（対象区間・取引機会・建玉）を
写して持つ。写さないと「同じバッチで同じ Exit を2つの建玉について評価した」場合に、どの
記録がどの建玉のものか要求の識別子からは復元できない。

段階2の終着は3つだけである（待機と追い越しは段階3）。

| 終着 | 記録 |
|---|---|
| `Evaluated` | 生成した出力の識別子の列 |
| `Skipped` | 欠損の診断 |
| `Failed` | 理由（段階2はデータ誤りのみ） |

## 解決済み入力

読み方の4区分に対応する4つの要素を持つ。要素の並びは接続の宣言順である（可変個数入力は
「型付き参照の列」であり、並びを部品実装が参照しうるため。D04 §3）。

## `step` の戻り値

出力記録・評価記録・発注提案・管理要求・取引機会の遷移を返す。**発注提案と管理要求には
根拠になった出力の識別子を載せる**（D05 §6.2 の手順9、v1.2）。受け取る側がこの識別子を
持たないと、正常経路でも注文要求を組み立てられない（上位設計書 §4.7.8 の根拠の連鎖）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    EvaluationId,
    OpportunityId,
    OutputId,
    PositionId,
    RequestId,
)
from odyssey_fx.common.reason import MissingInputReason, Reason, ReasonCode
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.strategy.compiler.compiled import (
    ResolvedContextSource,
    ResolvedMarketSource,
    ResolvedOutputSource,
    ResolvedSource,
)
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    require_identifier,
    require_instance,
    require_tuple_of,
)
from odyssey_fx.strategy.records.payloads import ManagementAction, OrderIntent, ProtectionLevels
from odyssey_fx.strategy.records.records import OutputRecord
from odyssey_fx.strategy.runtime.opportunities import OpportunityTransition

__all__ = [
    "ContextSnapshot",
    "EntryProposal",
    "EvaluationOutcome",
    "EvaluationRecord",
    "EvaluationRequest",
    "Evaluated",
    "EventDelivery",
    "Failed",
    "InputElement",
    "ManagementRequest",
    "MissingInputDiagnosis",
    "ResolvedInputs",
    "RuntimeStepResult",
    "Skipped",
    "ValueSample",
    "ValueWindow",
]


def _require_source(value: object, label: str) -> ResolvedSource:
    if not isinstance(value, (ResolvedOutputSource, ResolvedMarketSource, ResolvedContextSource)):
        raise KernelValueError(f"{label} must be a ResolvedSource, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class MissingInputDiagnosis:
    """どの入力が、どの接続元で、なぜ読めなかったか（D05 §6.3）。"""

    input_name: str
    source: ResolvedSource
    reason: MissingInputReason

    def __post_init__(self) -> None:
        require_identifier(self.input_name, "MissingInputDiagnosis.input_name")
        _require_source(self.source, "MissingInputDiagnosis.source")
        require_instance(self.reason, MissingInputReason, "MissingInputDiagnosis.reason")


@dataclass(frozen=True, slots=True)
class ValueSample:
    """繰り返し参照する値の1件（D05 §6.3）。

    `freshness_time` は鮮度の基準時刻で、確定足なら足の終了時刻、出力参照ならその出力を
    生んだ評価の判断時刻である。鮮度上限の判定はランタイムが行う（D03 §6.2 が委ねた）。
    """

    payload: object
    source: ResolvedSource
    freshness_time: UtcTime
    source_output_id: OutputId | None = None
    kind: str = "VALUE_SAMPLE"

    def __post_init__(self) -> None:
        require_kind(self.kind, "VALUE_SAMPLE", "ValueSample.kind")
        _require_source(self.source, "ValueSample.source")
        require_instance(self.freshness_time, UtcTime, "ValueSample.freshness_time")
        if self.source_output_id is not None:
            require_instance(self.source_output_id, OutputId, "ValueSample.source_output_id")


@dataclass(frozen=True, slots=True)
class ValueWindow:
    """繰り返し参照する値の窓（D05 §6.3）。**古い順**に並ぶ。"""

    samples: tuple[ValueSample, ...]
    kind: str = "VALUE_WINDOW"

    def __post_init__(self) -> None:
        require_kind(self.kind, "VALUE_WINDOW", "ValueWindow.kind")
        require_tuple_of(self.samples, ValueSample, "ValueWindow.samples")
        if not self.samples:
            raise KernelValueError("ValueWindow.samples must not be empty")


@dataclass(frozen=True, slots=True)
class EventDelivery:
    """配送されたイベント1件（D05 §6.3）。"""

    payload: object
    source_output_id: OutputId
    kind: str = "EVENT_DELIVERY"

    def __post_init__(self) -> None:
        require_kind(self.kind, "EVENT_DELIVERY", "EventDelivery.kind")
        require_instance(self.source_output_id, OutputId, "EventDelivery.source_output_id")


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """現在コンテキストの1件（D05 §6.3）。"""

    payload: object
    read_at: UtcTime
    kind: str = "CONTEXT_SNAPSHOT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "CONTEXT_SNAPSHOT", "ContextSnapshot.kind")
        require_instance(self.read_at, UtcTime, "ContextSnapshot.read_at")


#: 区分タグ付き union（D05 §6.3）。
InputElement = ValueSample | ValueWindow | EventDelivery | ContextSnapshot


@dataclass(frozen=True, slots=True)
class ResolvedInputs:
    """部品へ渡す解決済みの入力（D05 §6.3）。"""

    by_name: Mapping[str, tuple[InputElement, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.by_name, Mapping):
            raise KernelValueError("ResolvedInputs.by_name must be a Mapping")
        for name, elements in self.by_name.items():
            require_identifier(name, "ResolvedInputs.by_name key")
            if not isinstance(elements, tuple):
                raise KernelValueError(
                    f"ResolvedInputs.by_name[{name!r}] must be a tuple, got {elements!r}"
                )
        object.__setattr__(self, "by_name", freeze_mapping(dict(self.by_name)))


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """1回の評価の依頼（D05 §6.2・§6.4）。"""

    request_id: RequestId
    instance_id: str
    trigger_names: tuple[str, ...]
    decision_time: UtcTime
    target_interval: Interval | None = None
    opportunity_id: OpportunityId | None = None
    position_id: PositionId | None = None

    def __post_init__(self) -> None:
        require_instance(self.request_id, RequestId, "EvaluationRequest.request_id")
        require_identifier(self.instance_id, "EvaluationRequest.instance_id")
        require_tuple_of(self.trigger_names, str, "EvaluationRequest.trigger_names")
        if not self.trigger_names:
            raise KernelValueError("EvaluationRequest.trigger_names must not be empty")
        require_instance(self.decision_time, UtcTime, "EvaluationRequest.decision_time")
        if self.target_interval is not None:
            require_instance(self.target_interval, Interval, "EvaluationRequest.target_interval")
        if self.opportunity_id is not None:
            require_instance(self.opportunity_id, OpportunityId, "EvaluationRequest.opportunity_id")
        if self.position_id is not None:
            require_instance(self.position_id, PositionId, "EvaluationRequest.position_id")


@dataclass(frozen=True, slots=True)
class Evaluated:
    """評価が成立し、出力を生んだ（D05 §6.4）。"""

    output_ids: tuple[OutputId, ...] = ()
    kind: str = "EVALUATED"

    def __post_init__(self) -> None:
        require_kind(self.kind, "EVALUATED", "Evaluated.kind")
        require_tuple_of(self.output_ids, OutputId, "Evaluated.output_ids")


@dataclass(frozen=True, slots=True)
class Skipped:
    """入力が欠けていたため評価しなかった（D05 §6.4）。

    見送りは False や価格 0 の出力に変換しない（上位設計書 §4.3.15）。診断を必ず添える。
    """

    diagnoses: tuple[MissingInputDiagnosis, ...]
    kind: str = "SKIPPED"

    def __post_init__(self) -> None:
        require_kind(self.kind, "SKIPPED", "Skipped.kind")
        require_tuple_of(self.diagnoses, MissingInputDiagnosis, "Skipped.diagnoses")
        if not self.diagnoses:
            raise KernelValueError(
                "Skipped.diagnoses must not be empty; an evaluation is only skipped because"
                " something was missing (D05 §6.4)"
            )


@dataclass(frozen=True, slots=True)
class Failed:
    """評価が失敗した（D05 §6.4）。段階2の理由はデータ誤りのみ。"""

    reason: Reason
    kind: str = "FAILED"

    def __post_init__(self) -> None:
        require_kind(self.kind, "FAILED", "Failed.kind")
        require_instance(self.reason, Reason, "Failed.reason")
        if self.reason.code is not ReasonCode.DATA_ERROR:
            raise KernelValueError(
                f"Failed.reason must be DATA_ERROR in stage 2, got {self.reason.code.value}"
            )


#: 区分タグ付き union（D05 §6.4）。
EvaluationOutcome = Evaluated | Skipped | Failed


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    """1回の評価の結果の記録（D05 §6.4）。"""

    request_id: RequestId
    evaluation_id: EvaluationId
    instance_id: str
    trigger_names: tuple[str, ...]
    decision_time: UtcTime
    outcome: EvaluationOutcome
    target_interval: Interval | None = None
    opportunity_id: OpportunityId | None = None
    position_id: PositionId | None = None

    def __post_init__(self) -> None:
        require_instance(self.request_id, RequestId, "EvaluationRecord.request_id")
        require_instance(self.evaluation_id, EvaluationId, "EvaluationRecord.evaluation_id")
        require_identifier(self.instance_id, "EvaluationRecord.instance_id")
        require_tuple_of(self.trigger_names, str, "EvaluationRecord.trigger_names")
        require_instance(self.decision_time, UtcTime, "EvaluationRecord.decision_time")
        if not isinstance(self.outcome, (Evaluated, Skipped, Failed)):
            raise KernelValueError(
                f"EvaluationRecord.outcome must be an EvaluationOutcome, got {self.outcome!r}"
            )
        if self.target_interval is not None:
            require_instance(self.target_interval, Interval, "EvaluationRecord.target_interval")
        if self.opportunity_id is not None:
            require_instance(self.opportunity_id, OpportunityId, "EvaluationRecord.opportunity_id")
        if self.position_id is not None:
            require_instance(self.position_id, PositionId, "EvaluationRecord.position_id")


@dataclass(frozen=True, slots=True)
class EntryProposal:
    """注文意図と保護水準がそろった発注の提案（D05 §6.2 の手順9）。"""

    opportunity_id: OpportunityId
    order_intent: OrderIntent
    protection: ProtectionLevels
    decision_time: UtcTime
    intent_output_id: OutputId
    protection_output_id: OutputId

    def __post_init__(self) -> None:
        require_instance(self.opportunity_id, OpportunityId, "EntryProposal.opportunity_id")
        require_instance(self.order_intent, OrderIntent, "EntryProposal.order_intent")
        require_instance(self.protection, ProtectionLevels, "EntryProposal.protection")
        require_instance(self.decision_time, UtcTime, "EntryProposal.decision_time")
        require_instance(self.intent_output_id, OutputId, "EntryProposal.intent_output_id")
        require_instance(self.protection_output_id, OutputId, "EntryProposal.protection_output_id")


@dataclass(frozen=True, slots=True)
class ManagementRequest:
    """保有中の建玉に対する管理要求（D05 §6.2 の手順9）。"""

    position_id: PositionId
    action: ManagementAction
    decision_time: UtcTime
    source_output_id: OutputId

    def __post_init__(self) -> None:
        require_instance(self.position_id, PositionId, "ManagementRequest.position_id")
        if getattr(self.action, "kind", None) not in ("SET_TAKE_PROFIT", "CLOSE_POSITION"):
            raise KernelValueError(
                f"ManagementRequest.action must be a ManagementAction, got {self.action!r}"
            )
        require_instance(self.decision_time, UtcTime, "ManagementRequest.decision_time")
        require_instance(self.source_output_id, OutputId, "ManagementRequest.source_output_id")


@dataclass(frozen=True, slots=True)
class RuntimeStepResult:
    """1回の `step` が返すもの（D05 §6.2 の手順10）。"""

    outputs: tuple[OutputRecord[object], ...] = ()
    evaluations: tuple[EvaluationRecord, ...] = ()
    proposals: tuple[EntryProposal, ...] = ()
    management_requests: tuple[ManagementRequest, ...] = ()
    transitions: tuple[OpportunityTransition, ...] = ()

    def __post_init__(self) -> None:
        require_tuple_of(self.outputs, OutputRecord, "RuntimeStepResult.outputs")
        require_tuple_of(self.evaluations, EvaluationRecord, "RuntimeStepResult.evaluations")
        require_tuple_of(self.proposals, EntryProposal, "RuntimeStepResult.proposals")
        require_tuple_of(
            self.management_requests, ManagementRequest, "RuntimeStepResult.management_requests"
        )
        require_tuple_of(self.transitions, OpportunityTransition, "RuntimeStepResult.transitions")
