"""コンパイル結果（D05 §5.2・§5.3）。

コンパイルは「宣言のとおりに動かせる形」へ変換する処理で、結果は成功か失敗のどちらか
である。**ランタイムは `CompiledStrategy` だけを読み、`StrategyDefinition` を再解釈しない**
（宣言の解釈を2か所に置かないため）。

失敗は例外ではなく `CompileFailed` で返す。1件直すたびに往復するのを避けるため、同じ段の
誤りはまとめて返す（D05 §5.1）。各誤りは**どの検査**（`check_id`、D04 §12 の番号）で
**どの宣言のどこ**（`location`）が原因かを必ず持ち、黙って無視する経路を作らない。

コンパイル拒否の区分は**コンパイラ専用**とし、共通の理由コード（D02 §8.1）は実行時に限る
（Q9 決定）。コンパイルは run の前に終わる処理であり、受付前拒否・評価見送りの集計に設計
ミスの分類が混ざらないようにするためである。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import (
    CompiledStrategyRef,
    ContractRef,
    ImplementationRef,
    StrategyRef,
)
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.declarations.datatypes import DataTypeRef
from odyssey_fx.strategy.declarations.entry_policy import (
    AwaitConfirmation,
    BarsDeadline,
    DeadlineAction,
    DurationDeadline,
    EntryPolicy,
    ImmediateEntry,
)
from odyssey_fx.strategy.declarations.evaluation import EvaluationTrigger
from odyssey_fx.strategy.declarations.opportunity import (
    OpportunityConcurrencySpec,
    OpportunityValiditySpec,
)
from odyssey_fx.strategy.declarations.read_spec import (
    BarsWindow,
    DurationWindow,
    InputReadSpec,
    ReadWindow,
)
from odyssey_fx.strategy.declarations.refs import MarketDataField, OutputRef, RuntimeTarget
from odyssey_fx.strategy.declarations.specs import ParameterValue, PortKind, UnitRef
from odyssey_fx.strategy.declarations.state_spec import StateSpec
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    require_identifier,
    require_instance,
    require_int,
    require_tuple_of,
)

__all__ = [
    "CompileError",
    "CompileFailed",
    "CompileRejection",
    "CompileResult",
    "CompileSucceeded",
    "CompiledComponent",
    "CompiledRoles",
    "CompiledStrategy",
    "ConfirmationPlan",
    "DeclarationLocation",
    "InputPlan",
    "OutputRetentionPlan",
    "ResolvedContextSource",
    "ResolvedMarketSource",
    "ResolvedOutputSource",
    "ResolvedParameter",
    "ResolvedSource",
]


class CompileRejection(Enum):
    """コンパイル拒否の区分（D05 §5.2、Q9 決定）。"""

    #: 契約が未登録、`OutputRef` の使用箇所が無い。
    REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"
    #: データ型・口の区分の不一致、銘柄が2つ以上。
    TYPE_MISMATCH = "TYPE_MISMATCH"
    #: 範囲外、列挙外、未解決のパラメータ参照。
    PARAMETER_INVALID = "PARAMETER_INVALID"
    #: 許可された起動条件の範囲外、固定条件の上書き、必須入力の不一致。
    SCHEDULE_NOT_ALLOWED = "SCHEDULE_NOT_ALLOWED"
    #: 役割フィールドの型要求違反、後続確認の有無と発注方針の不整合。
    ROLE_MISMATCH = "ROLE_MISMATCH"
    #: 出力仕様の付随条件の違反（取引機会の出力の再武装・根拠値の宣言など）。
    OUTPUT_SPEC_INVALID = "OUTPUT_SPEC_INVALID"
    #: 明示辺と因果辺の和に閉路がある。
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    #: 対応しない構成（能力検査と、待機・遡り・履歴窓の組合せの検査。D05 §5.6）。
    UNSUPPORTED_CONFIGURATION = "UNSUPPORTED_CONFIGURATION"


@dataclass(frozen=True, slots=True)
class DeclarationLocation:
    """誤りの原因になった宣言の場所（D05 §5.2）。"""

    instance_id: str | None
    field_path: str

    def __post_init__(self) -> None:
        if self.instance_id is not None:
            require_identifier(self.instance_id, "DeclarationLocation.instance_id")
        if not isinstance(self.field_path, str) or not self.field_path:
            raise KernelValueError(
                f"DeclarationLocation.field_path must be a non-empty str, got {self.field_path!r}"
            )

    def __str__(self) -> str:
        if self.instance_id is None:
            return self.field_path
        return f"{self.instance_id}.{self.field_path}"


@dataclass(frozen=True, slots=True)
class CompileError:
    """コンパイル拒否1件（D05 §5.2）。"""

    check_id: str
    rejection: CompileRejection
    location: DeclarationLocation
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.check_id, str) or not self.check_id:
            raise KernelValueError(
                f"CompileError.check_id must be a non-empty str, got {self.check_id!r}"
            )
        require_instance(self.rejection, CompileRejection, "CompileError.rejection")
        require_instance(self.location, DeclarationLocation, "CompileError.location")
        if not isinstance(self.message, str) or not self.message:
            raise KernelValueError(
                f"CompileError.message must be a non-empty str, got {self.message!r}"
            )

    def __str__(self) -> str:
        return f"[{self.check_id} {self.rejection.value}] {self.location}: {self.message}"


@dataclass(frozen=True, slots=True)
class ResolvedParameter:
    """具体値へ解決したパラメータ1件（D05 §4.4・§5.3）。

    `decimal_value` は、単位が比率・価格・pips の浮動小数パラメータをランタイムが厳密な
    10進数へ変換した値である（D05 §4.4）。それ以外では `None`。宣言側は有限の浮動小数の
    まま保持する（D04 §7 を変えない）。
    """

    name: str
    value: ParameterValue
    unit: UnitRef | None = None
    decimal_value: Decimal | None = None

    def __post_init__(self) -> None:
        require_identifier(self.name, "ResolvedParameter.name")
        if self.unit is not None:
            require_instance(self.unit, UnitRef, "ResolvedParameter.unit")
        if self.decimal_value is not None:
            require_instance(self.decimal_value, Decimal, "ResolvedParameter.decimal_value")


@dataclass(frozen=True, slots=True)
class ResolvedOutputSource:
    """他の使用箇所の出力（D05 §5.3）。"""

    instance_id: str
    output_name: str
    kind: str = "OUTPUT"

    def __post_init__(self) -> None:
        require_identifier(self.instance_id, "ResolvedOutputSource.instance_id")
        require_identifier(self.output_name, "ResolvedOutputSource.output_name")

    @property
    def ref(self) -> OutputRef:
        """宣言側の参照型としての表現（`RuntimeState` の鍵に使う）。"""
        return OutputRef(self.instance_id, self.output_name)

    def __str__(self) -> str:
        return f"{self.instance_id}.{self.output_name}"


@dataclass(frozen=True, slots=True)
class ResolvedMarketSource:
    """市場データの系列と項目（D05 §5.3）。"""

    series: SeriesId
    field: MarketDataField
    kind: str = "MARKET_DATA"

    def __post_init__(self) -> None:
        require_instance(self.series, SeriesId, "ResolvedMarketSource.series")
        require_instance(self.field, MarketDataField, "ResolvedMarketSource.field")

    def __str__(self) -> str:
        return f"{self.series}:{self.field.value}"


@dataclass(frozen=True, slots=True)
class ResolvedContextSource:
    """エンジンが供給する現在コンテキスト（D05 §5.3）。"""

    target: RuntimeTarget
    kind: str = "RUNTIME_INPUT"

    def __post_init__(self) -> None:
        require_instance(self.target, RuntimeTarget, "ResolvedContextSource.target")

    def __str__(self) -> str:
        return f"runtime:{self.target.value}"


#: 区分タグ付き union（D05 §5.3）。
ResolvedSource = ResolvedOutputSource | ResolvedMarketSource | ResolvedContextSource


@dataclass(frozen=True, slots=True)
class InputPlan:
    """1つの入力をどう読むかの解決済みの計画（D05 §5.3）。

    `resolved_window` はパラメータ参照を解決した後の窓で、ランタイムはこれをそのまま
    市場データビューへ渡す（D05 §6.3）。窓を使わない読み方では `None`。
    """

    input_name: str
    data_type: DataTypeRef
    kind: PortKind
    read_spec: InputReadSpec
    sources: tuple[ResolvedSource, ...]
    resolved_window: ReadWindow | None = None

    def __post_init__(self) -> None:
        require_identifier(self.input_name, "InputPlan.input_name")
        require_instance(self.data_type, DataTypeRef, "InputPlan.data_type")
        require_instance(self.kind, PortKind, "InputPlan.kind")
        if not isinstance(self.sources, tuple) or not self.sources:
            raise KernelValueError("InputPlan.sources must be a non-empty tuple")
        for index, source in enumerate(self.sources):
            if not isinstance(
                source, (ResolvedOutputSource, ResolvedMarketSource, ResolvedContextSource)
            ):
                raise KernelValueError(
                    f"InputPlan.sources[{index}] must be a ResolvedSource, got {source!r}"
                )
        if self.resolved_window is not None and not isinstance(
            self.resolved_window, (BarsWindow, DurationWindow)
        ):
            raise KernelValueError(
                f"InputPlan.resolved_window must be a window or None, got {self.resolved_window!r}"
            )
        if isinstance(self.resolved_window, BarsWindow) and not isinstance(
            self.resolved_window.count, int
        ):
            raise KernelValueError(
                "InputPlan.resolved_window must have a concrete bar count"
                f" (got {self.resolved_window.count!r})"
            )


@dataclass(frozen=True, slots=True)
class CompiledComponent:
    """解決済みの使用箇所（D05 §5.3）。"""

    instance_id: str
    contract_ref: ContractRef
    implementation_ref: ImplementationRef
    parameters: Mapping[str, ResolvedParameter]
    input_plans: Mapping[str, InputPlan]
    triggers: tuple[EvaluationTrigger, ...]
    required_inputs: Mapping[str, tuple[str, ...]]
    state_spec: StateSpec | None
    symbol: Symbol | None

    def __post_init__(self) -> None:
        require_identifier(self.instance_id, "CompiledComponent.instance_id")
        require_instance(self.contract_ref, ContractRef, "CompiledComponent.contract_ref")
        require_instance(
            self.implementation_ref, ImplementationRef, "CompiledComponent.implementation_ref"
        )
        object.__setattr__(self, "parameters", freeze_mapping(dict(self.parameters)))
        object.__setattr__(self, "input_plans", freeze_mapping(dict(self.input_plans)))
        if not isinstance(self.triggers, tuple) or not self.triggers:
            raise KernelValueError("CompiledComponent.triggers must be a non-empty tuple")
        object.__setattr__(self, "required_inputs", freeze_mapping(dict(self.required_inputs)))
        if self.state_spec is not None:
            require_instance(self.state_spec, StateSpec, "CompiledComponent.state_spec")
        if self.symbol is not None:
            require_instance(self.symbol, Symbol, "CompiledComponent.symbol")


@dataclass(frozen=True, slots=True)
class ConfirmationPlan:
    """後続確認の制御に要る値をまとめた計画（D05 §3・§5.3・§5.6・§7.7）。

    コンパイラが検査 a・b（D04 §12 #8・#9）で読んだ値を1件にまとめる。ランタイムはこの1件
    だけを読んで確認を制御し、宣言と契約を実行時に読み直さない（D05 §5.3）。

    - `filter_instance`: 確認部品の使用箇所（`execution_filter` 役割が指す出力の持ち主）
    - `series`: 確認足の系列（その使用箇所の足の確定の系列。検査 b でただ1つと確かめた）
    - `include_start_bar`: 開始足で確認を始めるか（使用箇所が明示した値。検査 a）
    - `deadline` / `on_deadline`: 確認待ちの発注方針（`AwaitConfirmation`）の期限と動作
    """

    filter_instance: str
    series: SeriesId
    include_start_bar: bool
    deadline: BarsDeadline | DurationDeadline
    on_deadline: DeadlineAction

    def __post_init__(self) -> None:
        require_identifier(self.filter_instance, "ConfirmationPlan.filter_instance")
        require_instance(self.series, SeriesId, "ConfirmationPlan.series")
        require_instance(self.include_start_bar, bool, "ConfirmationPlan.include_start_bar")
        if not isinstance(self.deadline, (BarsDeadline, DurationDeadline)):
            raise KernelValueError(
                "ConfirmationPlan.deadline must be a BarsDeadline or DurationDeadline,"
                f" got {self.deadline!r}"
            )
        require_instance(self.on_deadline, DeadlineAction, "ConfirmationPlan.on_deadline")


@dataclass(frozen=True, slots=True)
class OutputRetentionPlan:
    """出力参照ごとに何本の出力をため込むかの計画（D05 §3・§5.3・§6.12、Q18 決定）。

    本数は**その出力参照を読む入力の計画のうち最大の要求**から導く（D05 §6.12）。最新1件の
    読み方は 1 本、本数で数える履歴窓は「窓の本数＋当該足を除く本数」。読み手が1つも無い
    出力参照は載せない（鍵を作らない）。ランタイムはこの1件だけを読んで保持本数を決める。
    """

    by_output: Mapping[OutputRef, int]

    def __post_init__(self) -> None:
        if not isinstance(self.by_output, Mapping):
            raise KernelValueError("OutputRetentionPlan.by_output must be a Mapping")
        for ref, count in self.by_output.items():
            require_instance(ref, OutputRef, "OutputRetentionPlan.by_output key")
            if require_int(count, f"OutputRetentionPlan.by_output[{ref}]") < 1:
                raise KernelValueError(
                    f"OutputRetentionPlan.by_output[{ref}] must be at least 1, got {count}"
                )
        object.__setattr__(
            self,
            "by_output",
            # 鍵は出力参照（文字列ではない）なので、使用箇所 ID・出力名の順に並べてから包む。
            MappingProxyType(
                dict(
                    sorted(
                        self.by_output.items(),
                        key=lambda item: (item[0].instance_id, item[0].output_name),
                    )
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class CompiledRoles:
    """役割フィールドの解決結果（D05 §5.3）。

    `trigger` / `order` / `protection` は段階2で必須、`market_state` / `execution_filter` /
    `exit` は `None` を許す。

    `confirmation` は段階3 で足した確認の計画である（D05 §5.6）。`execution_filter` が
    `None` の戦略では `None`、そうでなければ必ず持ち、その確認部品の使用箇所は
    `execution_filter` が指す出力の持ち主と一致する。
    """

    trigger: OutputRef
    order: OutputRef
    protection: OutputRef
    market_state: OutputRef | None = None
    execution_filter: OutputRef | None = None
    exit: OutputRef | None = None
    confirmation: ConfirmationPlan | None = None

    def __post_init__(self) -> None:
        require_instance(self.trigger, OutputRef, "CompiledRoles.trigger")
        require_instance(self.order, OutputRef, "CompiledRoles.order")
        require_instance(self.protection, OutputRef, "CompiledRoles.protection")
        for label, value in (
            ("market_state", self.market_state),
            ("execution_filter", self.execution_filter),
            ("exit", self.exit),
        ):
            if value is not None:
                require_instance(value, OutputRef, f"CompiledRoles.{label}")
        if self.execution_filter is None:
            if self.confirmation is not None:
                raise KernelValueError(
                    "CompiledRoles.confirmation must be None when there is no execution_filter"
                )
            return
        require_instance(self.confirmation, ConfirmationPlan, "CompiledRoles.confirmation")
        assert self.confirmation is not None  # require_instance が保証する
        if self.confirmation.filter_instance != self.execution_filter.instance_id:
            raise KernelValueError(
                "CompiledRoles.confirmation.filter_instance must be the execution_filter's"
                f" instance ({self.execution_filter.instance_id!r}),"
                f" got {self.confirmation.filter_instance!r}"
            )


@dataclass(frozen=True, slots=True)
class CompiledStrategy:
    """実行できる形に解決した戦略（D05 §5.3）。

    `components` は `evaluation_order` と同じ並びで保持する（並びが2つあると食い違う）。
    `output_retention` は段階3 で足した出力の保持本数の計画である（D05 §5.3・§6.12）。
    """

    schema_version: int
    strategy_ref: StrategyRef
    compiled_ref: CompiledStrategyRef
    symbol: Symbol
    components: tuple[CompiledComponent, ...]
    evaluation_order: tuple[str, ...]
    roles: CompiledRoles
    entry_policy: EntryPolicy
    opportunity_validity: OpportunityValiditySpec
    opportunity_concurrency: OpportunityConcurrencySpec
    output_retention: OutputRetentionPlan

    def __post_init__(self) -> None:
        require_instance(self.strategy_ref, StrategyRef, "CompiledStrategy.strategy_ref")
        require_instance(self.compiled_ref, CompiledStrategyRef, "CompiledStrategy.compiled_ref")
        require_instance(self.symbol, Symbol, "CompiledStrategy.symbol")
        require_tuple_of(self.components, CompiledComponent, "CompiledStrategy.components")
        require_tuple_of(self.evaluation_order, str, "CompiledStrategy.evaluation_order")
        if tuple(item.instance_id for item in self.components) != self.evaluation_order:
            raise KernelValueError(
                "CompiledStrategy.components must be held in evaluation_order"
                f" (components={[item.instance_id for item in self.components]},"
                f" evaluation_order={list(self.evaluation_order)})"
            )
        require_instance(self.roles, CompiledRoles, "CompiledStrategy.roles")
        if not isinstance(self.entry_policy, (ImmediateEntry, AwaitConfirmation)):
            raise KernelValueError(
                f"CompiledStrategy.entry_policy must be an EntryPolicy, got {self.entry_policy!r}"
            )
        require_instance(
            self.opportunity_validity,
            OpportunityValiditySpec,
            "CompiledStrategy.opportunity_validity",
        )
        require_instance(
            self.opportunity_concurrency,
            OpportunityConcurrencySpec,
            "CompiledStrategy.opportunity_concurrency",
        )
        require_instance(
            self.output_retention, OutputRetentionPlan, "CompiledStrategy.output_retention"
        )

    def component(self, instance_id: str) -> CompiledComponent:
        """使用箇所を ID で引く。コンパイル済みなので存在しない ID は呼び出し側の誤り。"""
        for item in self.components:
            if item.instance_id == instance_id:
                return item
        raise KernelValueError(f"unknown instance_id: {instance_id!r}")


@dataclass(frozen=True, slots=True)
class CompileSucceeded:
    """コンパイル成功（D05 §5.1）。"""

    compiled: CompiledStrategy
    kind: str = "SUCCEEDED"

    def __post_init__(self) -> None:
        require_instance(self.compiled, CompiledStrategy, "CompileSucceeded.compiled")


@dataclass(frozen=True, slots=True)
class CompileFailed:
    """コンパイル失敗（D05 §5.1）。同じ段の誤りをまとめて持つ。"""

    errors: tuple[CompileError, ...]
    kind: str = "FAILED"

    def __post_init__(self) -> None:
        require_tuple_of(self.errors, CompileError, "CompileFailed.errors")
        if not self.errors:
            raise KernelValueError("CompileFailed.errors must not be empty")

    def __str__(self) -> str:
        return "\n".join(str(error) for error in self.errors)


#: 区分タグ付き union（D05 §5.1）。
CompileResult = CompileSucceeded | CompileFailed
