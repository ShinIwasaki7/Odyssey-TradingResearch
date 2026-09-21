"""コンパイルの手順（D05 §5.1、D04 §12）。

宣言（戦略の「形」）を、ランタイムがそのまま実行できる `CompiledStrategy` へ変換する。

段は次の順に走り、**前段が失敗したら後段を実行しない**。存在しない参照の型を比較しようと
して二次的な誤りを出さないためである。ただし**同じ段の中では全件を検査**し、まとめて返す
（1件ずつ直す往復を減らす）。

| 段 | 内容 | D04 §12 の検査 |
|---|---|---|
| 1 | 契約の解決（レジストリ参照、版と内容ハッシュの照合） | #1 |
| 2 | 参照の解決（出力参照・役割フィールド・有効性束縛） | #1・#5 |
| 3 | パラメータの解決（型・範囲・列挙値・パラメータ参照） | #3 |
| 4 | 銘柄の伝播と単一銘柄の検査 | #2 |
| 5 | 型と接続の検査（データ型・口の区分・接続数・読み方の組合せ） | #2 |
| 6 | 出力仕様の付随条件 | #6b |
| 7 | 評価スケジュールの検査 | #4 |
| 8 | 依存グラフの構築と循環検出、評価順の導出 | #6 |
| 9 | 能力検査 | #7 |
| 10 | ハッシュ計算と組み立て | — |

検査項目そのものの正本は D04 §12 であり、本モジュールは実行順と失敗時の扱いを足している
（D05 §5.1）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields as dataclass_fields
from decimal import Decimal

from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.registry import (
    ComponentRegistration,
    ComponentRegistry,
    ContractKey,
)
from odyssey_fx.strategy.compiler import capability
from odyssey_fx.strategy.compiler.compiled import (
    CompiledComponent,
    CompiledRoles,
    CompiledStrategy,
    CompileError,
    CompileFailed,
    CompileRejection,
    CompileResult,
    CompileSucceeded,
    DeclarationLocation,
    InputPlan,
    ResolvedContextSource,
    ResolvedMarketSource,
    ResolvedOutputSource,
    ResolvedParameter,
    ResolvedSource,
)
from odyssey_fx.strategy.compiler.graph import build_dependency_graph
from odyssey_fx.strategy.compiler.hashing import compiled_strategy_ref
from odyssey_fx.strategy.declarations import datatypes
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import DataTypeRef
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.digest import contract_digest, strategy_ref_for
from odyssey_fx.strategy.declarations.entry_policy import AwaitConfirmation
from odyssey_fx.strategy.declarations.evaluation import (
    AllowedBarClose,
    AllowedInputEvent,
    AllowedRuntimeEvent,
    AllowedTrigger,
    EvaluationTrigger,
    OnBarClose,
    OnInputEvent,
    OnRuntimeEvent,
)
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.missing import Error as ErrorPolicy
from odyssey_fx.strategy.declarations.opportunity import ValidityMode
from odyssey_fx.strategy.declarations.read_spec import (
    BarsWindow,
    CurrentContext,
    DeliveredEvent,
    DurationWindow,
    HistoryWindow,
    LatestAvailable,
    ParameterRef,
)
from odyssey_fx.strategy.declarations.refs import (
    MarketDataRef,
    OutputRef,
    RuntimeInputRef,
)
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    FloatValue,
    InputSpec,
    IntValue,
    OutputSpec,
    ParameterValue,
    PortKind,
    RetriggerMode,
    StrValue,
    UnitRef,
    parameter_value_of,
)
from odyssey_fx.strategy.declarations.state_spec import LiteralInitialState
from odyssey_fx.strategy.records.payloads import payload_type_for

__all__ = ["ROLE_DATA_TYPES", "ROLE_PORT_KINDS", "compile_strategy"]

#: 役割フィールドが要求する出力のデータ型（D04 §12 #5）。
ROLE_DATA_TYPES: Mapping[str, DataTypeRef] = {
    "market_state": datatypes.MARKET_PERMISSION_V1,
    "trigger": datatypes.OPPORTUNITY_V1,
    "execution_filter": datatypes.CONFIRMATION_RESULT_V1,
    "order": datatypes.ORDER_INTENT_V1,
    "protection": datatypes.PROTECTION_LEVELS_V1,
    "exit": datatypes.MANAGEMENT_ACTION_V1,
}

#: 役割フィールドが要求する出力の口の区分（D05 §4.2）。
ROLE_PORT_KINDS: Mapping[str, PortKind] = {
    "market_state": PortKind.VALUE,
    "trigger": PortKind.EVENT,
    "execution_filter": PortKind.EVENT,
    "order": PortKind.COMMAND,
    "protection": PortKind.COMMAND,
    "exit": PortKind.COMMAND,
}

#: 単位ごとに、部品へ渡す前に厳密な10進数へ変換するかどうか（D05 §4.4）。
_DECIMAL_UNITS = frozenset({UnitRef.RATIO, UnitRef.PRICE, UnitRef.PIPS})

#: 状態の初期値の型検査に使う対応（D04 §10.4）。
#:
#: 鍵は内容型のフィールドの型の**名前**である。内容型は `from __future__ import annotations`
#: のもとで定義されているため、`dataclasses.fields()` が返す型は文字列であり、型オブジェクト
#: として比較できない。名前で引くことで、注釈の評価（`typing.get_type_hints`）を持ち込まずに
#: 済む。
_INITIAL_VALUE_TYPES: Mapping[str, type[ParameterValue]] = {
    "bool": BoolValue,
    "int": IntValue,
    "float": FloatValue,
    "str": StrValue,
}


def _error(
    check_id: str,
    rejection: CompileRejection,
    instance_id: str | None,
    field_path: str,
    message: str,
) -> CompileError:
    return CompileError(
        check_id=check_id,
        rejection=rejection,
        location=DeclarationLocation(instance_id=instance_id, field_path=field_path),
        message=message,
    )


def compile_strategy(
    definition: StrategyDefinition,
    registry: ComponentRegistry,
    timeframes: Mapping[TimeframeRef, TimeframeDefinition],
) -> CompileResult:
    """宣言をコンパイルする（D05 §5.1）。

    `timeframes` は足の細かさの能力検査（D04 §12 の「15m より細かい足」）に使う。時間足の
    名目の長さは `TimeframeRef` ではなく定義本体が持つため（D03 §3.2）、定義を渡せないと
    この検査を黙って飛ばすことになる。
    """
    resolved, errors = _stage1_contracts(definition, registry)
    if errors:
        return CompileFailed(errors=errors)

    errors = _stage2_references(definition, resolved)
    if errors:
        return CompileFailed(errors=errors)

    parameters, errors = _stage3_parameters(definition, resolved)
    if errors:
        return CompileFailed(errors=errors)

    symbols, errors = _stage4_symbols(definition, resolved)
    if errors:
        return CompileFailed(errors=errors)

    input_plans, errors = _stage5_types(definition, resolved, parameters)
    if errors:
        return CompileFailed(errors=errors)

    errors = _stage6_output_specs(definition, resolved)
    if errors:
        return CompileFailed(errors=errors)

    errors = _stage7_schedules(definition, resolved)
    if errors:
        return CompileFailed(errors=errors)

    graph = build_dependency_graph(definition, order_role=definition.order)
    cycle = graph.find_cycle()
    if cycle is not None:
        path = " -> ".join(str(edge) for edge in cycle)
        return CompileFailed(
            errors=(
                _error(
                    "#6",
                    CompileRejection.DEPENDENCY_CYCLE,
                    None,
                    "components",
                    f"the dependency graph has a cycle: {path}",
                ),
            )
        )
    evaluation_order = graph.evaluation_order()

    errors = capability.check_capabilities(definition, timeframes)
    errors += _stage9_history_windows(definition, resolved)
    if errors:
        return CompileFailed(errors=errors)

    return _stage10_assemble(
        definition, resolved, parameters, symbols, input_plans, evaluation_order
    )


# --- 段1: 契約の解決 ---------------------------------------------------------


def _stage1_contracts(
    definition: StrategyDefinition, registry: ComponentRegistry
) -> tuple[dict[str, ComponentRegistration], tuple[CompileError, ...]]:
    resolved: dict[str, ComponentRegistration] = {}
    errors: list[CompileError] = []
    for instance in definition.components:
        ref = instance.contract_ref
        key = ContractKey(ref.component_id, ref.version)
        registration = registry.get(key)
        if registration is None:
            errors.append(
                _error(
                    "#1",
                    CompileRejection.REFERENCE_NOT_FOUND,
                    instance.instance_id,
                    "contract_ref",
                    f"no component {key} is registered in the catalog",
                )
            )
            continue
        expected = contract_digest(registration.contract)
        if ref.digest != expected:
            errors.append(
                _error(
                    "#1",
                    CompileRejection.REFERENCE_NOT_FOUND,
                    instance.instance_id,
                    "contract_ref.digest",
                    f"the contract digest does not match the registered {key}"
                    f" (declared {ref.digest.hex[:12]}…, registered {expected.hex[:12]}…)",
                )
            )
            continue
        resolved[instance.instance_id] = registration
    return resolved, tuple(errors)


# --- 段2: 参照の解決 ---------------------------------------------------------


def _output_spec(
    resolved: Mapping[str, ComponentRegistration], ref: OutputRef
) -> OutputSpec | None:
    registration = resolved.get(ref.instance_id)
    if registration is None:
        return None
    return registration.contract.outputs.get(ref.output_name)


def _stage2_references(
    definition: StrategyDefinition, resolved: Mapping[str, ComponentRegistration]
) -> tuple[CompileError, ...]:
    errors: list[CompileError] = []

    for instance in definition.components:
        contract = resolved[instance.instance_id].contract
        for input_name, binding in instance.inputs.items():
            if input_name not in contract.inputs:
                errors.append(
                    _error(
                        "#1",
                        CompileRejection.REFERENCE_NOT_FOUND,
                        instance.instance_id,
                        f"inputs.{input_name}",
                        f"the contract {contract.component_id}@v{contract.version} declares no"
                        f" input named {input_name!r}",
                    )
                )
                continue
            for index, source in enumerate(binding.sources):
                if not isinstance(source, OutputRef):
                    continue
                if source.instance_id not in resolved:
                    errors.append(
                        _error(
                            "#1",
                            CompileRejection.REFERENCE_NOT_FOUND,
                            instance.instance_id,
                            f"inputs.{input_name}.sources[{index}]",
                            f"no component instance named {source.instance_id!r} exists",
                        )
                    )
                elif _output_spec(resolved, source) is None:
                    errors.append(
                        _error(
                            "#1",
                            CompileRejection.REFERENCE_NOT_FOUND,
                            instance.instance_id,
                            f"inputs.{input_name}.sources[{index}]",
                            f"{source.instance_id!r} has no output named {source.output_name!r}",
                        )
                    )

    errors.extend(_check_roles(definition, resolved))
    errors.extend(_check_validity_bindings(definition, resolved))
    return tuple(errors)


def _check_roles(
    definition: StrategyDefinition, resolved: Mapping[str, ComponentRegistration]
) -> list[CompileError]:
    errors: list[CompileError] = []
    for role, expected_type in ROLE_DATA_TYPES.items():
        ref = getattr(definition, role)
        if ref is None:
            continue
        spec = _output_spec(resolved, ref)
        if spec is None:
            errors.append(
                _error(
                    "#5",
                    CompileRejection.REFERENCE_NOT_FOUND,
                    None,
                    role,
                    f"the role points at {ref}, which does not exist",
                )
            )
            continue
        if spec.data_type != expected_type:
            errors.append(
                _error(
                    "#5",
                    CompileRejection.ROLE_MISMATCH,
                    None,
                    role,
                    f"the role requires an output of type {expected_type}, but {ref} produces"
                    f" {spec.data_type}",
                )
            )
        expected_kind = ROLE_PORT_KINDS[role]
        if spec.kind is not expected_kind:
            errors.append(
                _error(
                    "#5",
                    CompileRejection.ROLE_MISMATCH,
                    None,
                    role,
                    f"the role requires a {expected_kind.value} output, but {ref} is"
                    f" {spec.kind.value} (D05 §4.2)",
                )
            )

    has_filter = definition.execution_filter is not None
    wants_confirmation = isinstance(definition.entry_policy, AwaitConfirmation)
    if has_filter != wants_confirmation:
        errors.append(
            _error(
                "#5",
                CompileRejection.ROLE_MISMATCH,
                None,
                "entry_policy",
                "execution_filter and entry_policy must agree: no filter means ImmediateEntry,"
                " a filter means AwaitConfirmation"
                f" (filter={'set' if has_filter else 'none'},"
                f" policy={type(definition.entry_policy).__name__})",
            )
        )
    return errors


def _check_validity_bindings(
    definition: StrategyDefinition, resolved: Mapping[str, ComponentRegistration]
) -> list[CompileError]:
    """有効性束縛が条件の成否を出す出力を指すことを確かめる（D04 §10.2・§12 #5）。"""
    errors: list[CompileError] = []
    for index, binding in enumerate(definition.opportunity_validity.bindings):
        spec = _output_spec(resolved, binding.source)
        field_path = f"opportunity_validity.bindings[{index}].source"
        if spec is None:
            errors.append(
                _error(
                    "#5",
                    CompileRejection.REFERENCE_NOT_FOUND,
                    None,
                    field_path,
                    f"the validity binding points at {binding.source}, which does not exist",
                )
            )
            continue
        if spec.data_type != datatypes.CONDITION_STATE_V1:
            errors.append(
                _error(
                    "#5",
                    CompileRejection.ROLE_MISMATCH,
                    None,
                    field_path,
                    "a validity binding must point at an output of type"
                    f" {datatypes.CONDITION_STATE_V1}, but {binding.source} produces"
                    f" {spec.data_type}",
                )
            )
        elif spec.kind is not PortKind.VALUE:
            # ランタイムが読み直せるのは繰り返し参照する値の最新出力だけである（D05 §6.5）。
            # 配送イベントの出力を束縛に書くと、宣言は通るのに条件がいちども効かない。
            errors.append(
                _error(
                    "#5",
                    CompileRejection.ROLE_MISMATCH,
                    None,
                    field_path,
                    "a validity binding must point at a VALUE output; the runtime keeps only"
                    f" the latest VALUE output per reference, so a {spec.kind.value} output"
                    " would never constrain an opportunity (D05 §6.5)",
                )
            )
        if binding.mode is ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST and isinstance(
            binding.on_missing, ErrorPolicy
        ):
            # 再検査は評価の外側で走るため、失敗を評価記録として残す先が無い（D05 §6.4）。
            # 判断履歴に残せない失敗の経路を作らないよう、この組合せは段階2 では拒否する
            # （D04 §6.3 v1.8 の制限。解除は段階3、再検査の記録型を足してから）。
            errors.append(
                _error(
                    "#7",
                    CompileRejection.UNSUPPORTED_CONFIGURATION,
                    None,
                    f"opportunity_validity.bindings[{index}].on_missing",
                    "treating a missing re-check as a run failure is not supported in stage 2;"
                    " the re-check runs outside an evaluation, so the failure has no evaluation"
                    " record to be reported in (D05 §6.4・§7.3)",
                )
            )
    return errors


# --- 段3: パラメータの解決 ---------------------------------------------------


def _stage3_parameters(
    definition: StrategyDefinition, resolved: Mapping[str, ComponentRegistration]
) -> tuple[dict[str, dict[str, ResolvedParameter]], tuple[CompileError, ...]]:
    table: dict[str, dict[str, ResolvedParameter]] = {}
    errors: list[CompileError] = []
    for instance in definition.components:
        contract = resolved[instance.instance_id].contract
        resolved_for_instance, instance_errors = _resolve_instance_parameters(instance, contract)
        table[instance.instance_id] = resolved_for_instance
        errors.extend(instance_errors)
    if errors:
        return table, tuple(errors)
    errors.extend(_resolve_parameter_refs(definition, resolved, table))
    return table, tuple(errors)


def _resolve_instance_parameters(
    instance: ComponentInstance, contract: ComponentContract
) -> tuple[dict[str, ResolvedParameter], list[CompileError]]:
    errors: list[CompileError] = []
    resolved: dict[str, ResolvedParameter] = {}

    for name in instance.parameters:
        if name not in contract.parameters:
            errors.append(
                _error(
                    "#3",
                    CompileRejection.PARAMETER_INVALID,
                    instance.instance_id,
                    f"parameters.{name}",
                    f"the contract {contract.component_id}@v{contract.version} declares no"
                    f" parameter named {name!r}",
                )
            )

    for name, spec in contract.parameters.items():
        value = instance.parameters.get(name, spec.default)
        field_path = f"parameters.{name}"
        if value is None:
            errors.append(
                _error(
                    "#3",
                    CompileRejection.PARAMETER_INVALID,
                    instance.instance_id,
                    field_path,
                    f"parameter {name!r} has no value and the contract declares no default",
                )
            )
            continue
        if parameter_value_of(value) is not spec.value_type:
            errors.append(
                _error(
                    "#3",
                    CompileRejection.PARAMETER_INVALID,
                    instance.instance_id,
                    field_path,
                    f"parameter {name!r} must be a {spec.value_type.value} value, got {value!r}",
                )
            )
            continue
        if spec.allowed_values is not None and value not in spec.allowed_values:
            allowed = ", ".join(repr(item.value) for item in spec.allowed_values)
            errors.append(
                _error(
                    "#3",
                    CompileRejection.PARAMETER_INVALID,
                    instance.instance_id,
                    field_path,
                    f"parameter {name!r} must be one of {allowed}, got {value.value!r}",
                )
            )
            continue
        if spec.bounds is not None and isinstance(value, (IntValue, FloatValue)):
            if not spec.bounds.contains(value.value):
                errors.append(
                    _error(
                        "#3",
                        CompileRejection.PARAMETER_INVALID,
                        instance.instance_id,
                        field_path,
                        f"parameter {name!r} must lie in {spec.bounds}, got {value.value}",
                    )
                )
                continue
        resolved[name] = ResolvedParameter(
            name=name,
            value=value,
            unit=spec.unit,
            decimal_value=_decimal_value(value, spec.unit),
        )
    return resolved, errors


def _decimal_value(value: ParameterValue, unit: UnitRef | None) -> Decimal | None:
    """比率・価格・pips の浮動小数を厳密な10進数へ変換する（D05 §4.4）。

    `repr` の最短往復表現を経由するのは、`Decimal(float)` が二進浮動小数の誤差をそのまま
    持ち込むためである（ADR-0012・D02 §4.6）。
    """
    if unit is None or unit not in _DECIMAL_UNITS:
        return None
    if isinstance(value, FloatValue):
        return decimal_from_str(repr(value.value))
    if isinstance(value, IntValue):
        return decimal_from_str(str(value.value))
    return None


def _resolve_parameter_refs(
    definition: StrategyDefinition,
    resolved: Mapping[str, ComponentRegistration],
    parameters: Mapping[str, Mapping[str, ResolvedParameter]],
) -> list[CompileError]:
    """窓とウォームアップのパラメータ参照を具体値へ解決できることを確かめる（D04 §12 #3）。"""
    errors: list[CompileError] = []
    for instance in definition.components:
        contract = resolved[instance.instance_id].contract
        for input_name, spec in contract.inputs.items():
            if not isinstance(spec.read_spec, HistoryWindow):
                continue
            window = spec.read_spec.window
            if isinstance(window, BarsWindow) and isinstance(window.count, ParameterRef):
                error = _resolve_bars_reference(
                    instance.instance_id,
                    f"inputs.{input_name}.window.count",
                    window.count,
                    parameters[instance.instance_id],
                )
                if error is not None:
                    errors.append(error)
        warmup = contract.temporal_constraints.warmup
        if warmup is not None and isinstance(warmup.bars, ParameterRef):
            error = _resolve_bars_reference(
                instance.instance_id,
                "temporal_constraints.warmup.bars",
                warmup.bars,
                parameters[instance.instance_id],
            )
            if error is not None:
                errors.append(error)
    return errors


def _resolve_bars_reference(
    instance_id: str,
    field_path: str,
    reference: ParameterRef,
    parameters: Mapping[str, ResolvedParameter],
) -> CompileError | None:
    parameter = parameters.get(reference.parameter_name)
    if parameter is None:
        return _error(
            "#3",
            CompileRejection.PARAMETER_INVALID,
            instance_id,
            field_path,
            f"parameter reference {reference} cannot be resolved",
        )
    if not isinstance(parameter.value, IntValue) or parameter.value.value < 1:
        return _error(
            "#3",
            CompileRejection.PARAMETER_INVALID,
            instance_id,
            field_path,
            f"parameter reference {reference} must resolve to an integer of at least 1,"
            f" got {parameter.value!r}",
        )
    return None


# --- 段4: 銘柄の伝播 ---------------------------------------------------------


def _stage4_symbols(
    definition: StrategyDefinition, resolved: Mapping[str, ComponentRegistration]
) -> tuple[dict[str, Symbol | None], tuple[CompileError, ...]]:
    """銘柄をグラフ上に伝播させ、使用箇所ごとに1つに定まることを確かめる（D04 §5）。"""
    errors: list[CompileError] = []
    found: dict[str, set[Symbol]] = {}
    for instance in definition.components:
        contract = resolved[instance.instance_id].contract
        symbols: set[Symbol] = set()
        for binding in instance.inputs.values():
            for source in binding.sources:
                if isinstance(source, MarketDataRef):
                    symbols.add(source.series.symbol)
        for trigger in instance.evaluation.triggers:
            if isinstance(trigger, OnBarClose):
                symbols.add(trigger.series.symbol)
        warmup = contract.temporal_constraints.warmup
        if warmup is not None:
            symbols.add(warmup.series.symbol)
        found[instance.instance_id] = symbols

    # 上流から下流へ伝播させる。辺の本数は有限なので、変化が止まるまで繰り返せば収束する。
    changed = True
    while changed:
        changed = False
        for instance in definition.components:
            for binding in instance.inputs.values():
                for source in binding.sources:
                    if not isinstance(source, OutputRef):
                        continue
                    upstream = found.get(source.instance_id, set())
                    if not upstream.issubset(found[instance.instance_id]):
                        found[instance.instance_id] |= upstream
                        changed = True

    symbols_by_instance: dict[str, Symbol | None] = {}
    for instance_id, symbols in found.items():
        if len(symbols) > 1:
            names = ", ".join(sorted(str(item) for item in symbols))
            errors.append(
                _error(
                    "#2",
                    CompileRejection.TYPE_MISMATCH,
                    instance_id,
                    "inputs",
                    f"a component instance must resolve to a single symbol, got {names}"
                    " (multi-symbol strategies are stage 6 / D10)",
                )
            )
            symbols_by_instance[instance_id] = None
            continue
        symbols_by_instance[instance_id] = next(iter(symbols), None)

    errors.extend(_check_priceless_instances(definition, resolved, symbols_by_instance))
    if errors:
        return symbols_by_instance, tuple(errors)

    all_symbols = {symbol for symbol in symbols_by_instance.values() if symbol is not None}
    if len(all_symbols) != 1:
        names = ", ".join(sorted(str(item) for item in all_symbols)) or "none"
        errors.append(
            _error(
                "#2",
                CompileRejection.TYPE_MISMATCH,
                None,
                "components",
                f"a strategy must resolve to exactly one symbol, got {names}",
            )
        )
    return symbols_by_instance, tuple(errors)


def _check_priceless_instances(
    definition: StrategyDefinition,
    resolved: Mapping[str, ComponentRegistration],
    symbols: Mapping[str, Symbol | None],
) -> list[CompileError]:
    """銘柄の決まらない使用箇所は価格の出力を持てない（D04 §5）。"""
    price_types = {datatypes.PRICE_V1, datatypes.PRICE_OFFSET_V1}
    errors: list[CompileError] = []
    for instance in definition.components:
        if symbols.get(instance.instance_id) is not None:
            continue
        contract = resolved[instance.instance_id].contract
        for output_name, spec in contract.outputs.items():
            if spec.data_type in price_types:
                errors.append(
                    _error(
                        "#2",
                        CompileRejection.TYPE_MISMATCH,
                        instance.instance_id,
                        f"outputs.{output_name}",
                        f"no symbol reaches this component instance, so it cannot produce"
                        f" {spec.data_type} (D04 §5)",
                    )
                )
    return errors


# --- 段5: 型と接続 -----------------------------------------------------------

#: 口の区分と参照元ごとに許す読み方（D04 §6.1）。
_ALLOWED_READ_SPECS: Mapping[tuple[PortKind, str], tuple[type, ...]] = {
    (PortKind.VALUE, "OUTPUT"): (LatestAvailable, HistoryWindow),
    (PortKind.VALUE, "MARKET_DATA"): (LatestAvailable, HistoryWindow),
    (PortKind.VALUE, "RUNTIME_INPUT"): (CurrentContext,),
    (PortKind.EVENT, "OUTPUT"): (DeliveredEvent,),
}


def _stage5_types(
    definition: StrategyDefinition,
    resolved: Mapping[str, ComponentRegistration],
    parameters: Mapping[str, Mapping[str, ResolvedParameter]],
) -> tuple[dict[str, dict[str, InputPlan]], tuple[CompileError, ...]]:
    plans: dict[str, dict[str, InputPlan]] = {}
    errors: list[CompileError] = []
    for instance in definition.components:
        contract = resolved[instance.instance_id].contract
        instance_plans: dict[str, InputPlan] = {}
        for input_name, spec in contract.inputs.items():
            binding = instance.inputs.get(input_name)
            if binding is None:
                errors.append(
                    _error(
                        "#2",
                        CompileRejection.TYPE_MISMATCH,
                        instance.instance_id,
                        f"inputs.{input_name}",
                        f"input {input_name!r} is declared by the contract but has no binding"
                        f" (arity requires at least {spec.arity.min_count})",
                    )
                )
                continue
            if not spec.arity.allows(len(binding.sources)):
                errors.append(
                    _error(
                        "#2",
                        CompileRejection.TYPE_MISMATCH,
                        instance.instance_id,
                        f"inputs.{input_name}",
                        f"input {input_name!r} accepts {spec.arity} sources,"
                        f" got {len(binding.sources)}",
                    )
                )
                continue
            sources, source_errors = _resolve_sources(
                instance.instance_id, input_name, spec, binding.sources, resolved
            )
            errors.extend(source_errors)
            if source_errors:
                continue
            instance_plans[input_name] = InputPlan(
                input_name=input_name,
                data_type=spec.data_type,
                kind=spec.kind,
                read_spec=spec.read_spec,
                sources=sources,
                resolved_window=_resolved_window(spec, parameters[instance.instance_id]),
            )
        plans[instance.instance_id] = instance_plans
    return plans, tuple(errors)


def _resolve_sources(
    instance_id: str,
    input_name: str,
    spec: InputSpec,
    sources: tuple[object, ...],
    resolved: Mapping[str, ComponentRegistration],
) -> tuple[tuple[ResolvedSource, ...], list[CompileError]]:
    errors: list[CompileError] = []
    out: list[ResolvedSource] = []
    for index, source in enumerate(sources):
        field_path = f"inputs.{input_name}.sources[{index}]"
        if isinstance(source, OutputRef):
            upstream = _output_spec(resolved, source)
            if upstream is None:  # pragma: no cover - 段2 が先に拒否する
                continue
            if upstream.data_type != spec.data_type:
                errors.append(
                    _error(
                        "#2",
                        CompileRejection.TYPE_MISMATCH,
                        instance_id,
                        field_path,
                        f"input {input_name!r} reads {spec.data_type}, but {source} produces"
                        f" {upstream.data_type}",
                    )
                )
            if upstream.kind is not spec.kind:
                errors.append(
                    _error(
                        "#2",
                        CompileRejection.TYPE_MISMATCH,
                        instance_id,
                        field_path,
                        f"input {input_name!r} is a {spec.kind.value} port, but {source} is"
                        f" {upstream.kind.value} (D04 §5)",
                    )
                )
            out.append(ResolvedOutputSource(source.instance_id, source.output_name))
        elif isinstance(source, MarketDataRef):
            expected = datatypes.MARKET_DATA_FIELD_TYPES[source.field]
            if expected != spec.data_type:
                errors.append(
                    _error(
                        "#2",
                        CompileRejection.TYPE_MISMATCH,
                        instance_id,
                        field_path,
                        f"input {input_name!r} reads {spec.data_type}, but market data field"
                        f" {source.field.value} is {expected}",
                    )
                )
            if spec.kind is not PortKind.VALUE:
                errors.append(
                    _error(
                        "#2",
                        CompileRejection.TYPE_MISMATCH,
                        instance_id,
                        field_path,
                        "market data is a VALUE source, so it cannot feed a"
                        f" {spec.kind.value} port (D04 §5)",
                    )
                )
            out.append(ResolvedMarketSource(source.series, source.field))
        elif isinstance(source, RuntimeInputRef):
            expected = datatypes.RUNTIME_TARGET_TYPES[source.target]
            if expected != spec.data_type:
                errors.append(
                    _error(
                        "#2",
                        CompileRejection.TYPE_MISMATCH,
                        instance_id,
                        field_path,
                        f"input {input_name!r} reads {spec.data_type}, but"
                        f" {source.target.value} supplies {expected}",
                    )
                )
            out.append(ResolvedContextSource(source.target))
        else:  # pragma: no cover - 宣言型が3区分に限っている
            continue

        allowed = _ALLOWED_READ_SPECS.get((spec.kind, str(getattr(source, "kind", ""))))
        if allowed is None or not isinstance(spec.read_spec, allowed):
            errors.append(
                _error(
                    "#2",
                    CompileRejection.TYPE_MISMATCH,
                    instance_id,
                    field_path,
                    f"a {spec.kind.value} port fed by {getattr(source, 'kind', '?')} may not be"
                    f" read as {type(spec.read_spec).__name__} (D04 §6.1)",
                )
            )
    return tuple(out), errors


def _resolved_window(
    spec: InputSpec, parameters: Mapping[str, ResolvedParameter]
) -> BarsWindow | DurationWindow | None:
    """パラメータ参照を解決した窓を返す（D05 §5.3）。窓を使わない読み方では `None`。"""
    if not isinstance(spec.read_spec, HistoryWindow):
        return None
    window = spec.read_spec.window
    if isinstance(window, BarsWindow) and isinstance(window.count, ParameterRef):
        parameter = parameters[window.count.parameter_name]
        assert isinstance(parameter.value, IntValue)  # 段3 が保証する
        return BarsWindow(count=parameter.value.value)
    return window


# --- 段6: 出力仕様の付随条件 -------------------------------------------------


def _stage6_output_specs(
    definition: StrategyDefinition, resolved: Mapping[str, ComponentRegistration]
) -> tuple[CompileError, ...]:
    errors: list[CompileError] = []
    for instance in definition.components:
        contract = resolved[instance.instance_id].contract
        produces_opportunity = False
        for output_name, spec in contract.outputs.items():
            field_path = f"outputs.{output_name}"
            is_opportunity = spec.data_type == datatypes.OPPORTUNITY_V1
            produces_opportunity = produces_opportunity or is_opportunity
            if is_opportunity:
                if spec.retrigger_mode is None:
                    errors.append(
                        _error(
                            "#6b",
                            CompileRejection.OUTPUT_SPEC_INVALID,
                            instance.instance_id,
                            field_path,
                            "an opportunity output must declare a retrigger mode (EDGE or LEVEL)",
                        )
                    )
                elif spec.retrigger_mode is RetriggerMode.EDGE:
                    errors.extend(_check_edge_state(instance.instance_id, field_path, contract))
            else:
                if spec.retrigger_mode is not None:
                    errors.append(
                        _error(
                            "#6b",
                            CompileRejection.OUTPUT_SPEC_INVALID,
                            instance.instance_id,
                            field_path,
                            "only an opportunity output may declare a retrigger mode, got"
                            f" {spec.retrigger_mode.value}",
                        )
                    )
                if spec.reference_schema:
                    errors.append(
                        _error(
                            "#6b",
                            CompileRejection.OUTPUT_SPEC_INVALID,
                            instance.instance_id,
                            field_path,
                            "only an opportunity output may declare a reference schema, got"
                            f" {sorted(spec.reference_schema)}",
                        )
                    )
        if produces_opportunity:
            errors.extend(_check_opportunity_triggers(instance))
        errors.extend(_check_state_initializer(instance.instance_id, contract))
    return tuple(errors)


def _check_edge_state(
    instance_id: str, field_path: str, contract: ComponentContract
) -> list[CompileError]:
    """`EDGE` の契約が直前の成立を保持できる状態を宣言していることを確かめる（D04 §10.4）。

    初期値の項目と型そのものの検査は `_check_state_initializer` が全契約に対して行う。
    ここで見るのは「`EDGE` には条件の成否を表す状態が要る」という追加の要求だけである。
    """
    state_spec = contract.state_spec
    if state_spec is None or state_spec.state_type != datatypes.CONDITION_STATE_V1:
        return [
            _error(
                "#6b",
                CompileRejection.OUTPUT_SPEC_INVALID,
                instance_id,
                field_path,
                "an EDGE opportunity output requires a state of type"
                f" {datatypes.CONDITION_STATE_V1} to remember the previous evaluation"
                f" (got {state_spec.state_type if state_spec else 'no state'})",
            )
        ]
    if not isinstance(state_spec.initial, LiteralInitialState):
        return [
            _error(
                "#6b",
                CompileRejection.OUTPUT_SPEC_INVALID,
                instance_id,
                field_path,
                "an EDGE opportunity output requires its initial state to be written out"
                " in the declaration",
            )
        ]
    return []


def _check_state_initializer(instance_id: str, contract: ComponentContract) -> list[CompileError]:
    """宣言した初期値が状態型の項目と型に一致することを確かめる（D04 §9.1）。

    再武装の宣言（`EDGE`）に限らず、**状態を宣言するすべての契約**に適用する。初期値は宣言に
    書き切り、実装側の既定値に委ねないと決めているので（D04 §9.1）、書き間違いは run が
    始まる前に分かるほうがよい。ここで見なければ、ランタイムの組み立て時に状態を作ろうと
    した時点で落ちる。
    """
    state_spec = contract.state_spec
    if state_spec is None:
        return []
    field_path = "state_spec.initial"
    payload_types = payload_type_for(state_spec.state_type)
    if not payload_types:
        return [
            _error(
                "#6b",
                CompileRejection.OUTPUT_SPEC_INVALID,
                instance_id,
                "state_spec.state_type",
                f"state type {state_spec.state_type} has no runtime payload type (D05 §4.2)",
            )
        ]
    expected = {
        item.name: _INITIAL_VALUE_TYPES.get(_type_name(item.type))
        for item in dataclass_fields(payload_types[0])
    }
    values = state_spec.initial.values
    if set(values) != set(expected):
        return [
            _error(
                "#6b",
                CompileRejection.OUTPUT_SPEC_INVALID,
                instance_id,
                field_path,
                f"the initial state must declare exactly {sorted(expected)}, got {sorted(values)}",
            )
        ]
    errors: list[CompileError] = []
    for name, value_type in expected.items():
        if value_type is not None and not isinstance(values[name], value_type):
            errors.append(
                _error(
                    "#6b",
                    CompileRejection.OUTPUT_SPEC_INVALID,
                    instance_id,
                    field_path,
                    f"the initial state field {name!r} must be a {value_type.__name__},"
                    f" got {values[name]!r}",
                )
            )
    return errors


def _type_name(annotation: object) -> str:
    """フィールドの型注釈から素の型名を取り出す。"""
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _check_opportunity_triggers(instance: ComponentInstance) -> list[CompileError]:
    """取引機会を出す使用箇所が対象区間を決められることを確かめる（D05 §6.2 の手順3）。

    実行時イベントによる起動は対象区間を持たない（約定は足の区間に属さない）。起動条件が
    **1つでも**そうなっていれば、その起動から出た取引機会は対象区間を決められないので、
    足の確定と混ぜた宣言も拒否する。
    """
    return [
        _error(
            "#6b",
            CompileRejection.OUTPUT_SPEC_INVALID,
            instance.instance_id,
            f"evaluation.{trigger.name}",
            "a component that produces opportunities cannot start from a runtime event;"
            " the opportunity would have no signal interval (D05 §6.2)",
        )
        for trigger in instance.evaluation.triggers
        if isinstance(trigger, OnRuntimeEvent)
    ]


# --- 段7: 評価スケジュール ---------------------------------------------------


def _matches(trigger: EvaluationTrigger, allowed: AllowedTrigger) -> bool:
    if isinstance(trigger, OnBarClose) and isinstance(allowed, AllowedBarClose):
        return allowed.allows_timeframe(trigger.series.timeframe)
    if isinstance(trigger, OnInputEvent) and isinstance(allowed, AllowedInputEvent):
        return trigger.input_name in allowed.input_names
    if isinstance(trigger, OnRuntimeEvent) and isinstance(allowed, AllowedRuntimeEvent):
        return trigger.event in allowed.events
    return False


def _stage7_schedules(
    definition: StrategyDefinition, resolved: Mapping[str, ComponentRegistration]
) -> tuple[CompileError, ...]:
    errors: list[CompileError] = []
    for instance in definition.components:
        contract = resolved[instance.instance_id].contract
        spec = contract.evaluation_spec
        for trigger in instance.evaluation.triggers:
            if not any(_matches(trigger, allowed) for allowed in spec.allowed):
                errors.append(
                    _error(
                        "#4",
                        CompileRejection.SCHEDULE_NOT_ALLOWED,
                        instance.instance_id,
                        f"evaluation.{trigger.name}",
                        f"the contract does not allow this trigger ({trigger.kind})",
                    )
                )
        if spec.fixed and len(instance.evaluation.triggers) != 1:
            errors.append(
                _error(
                    "#4",
                    CompileRejection.SCHEDULE_NOT_ALLOWED,
                    instance.instance_id,
                    "evaluation",
                    "the contract fixes its evaluation condition, so exactly one trigger may be"
                    f" declared, got {len(instance.evaluation.triggers)}",
                )
            )
        errors.extend(_check_required_inputs(instance, contract))
    return tuple(errors)


def _check_required_inputs(
    instance: ComponentInstance, contract: ComponentContract
) -> list[CompileError]:
    """起動条件ごとの必須入力が、宣言済みで接続済みであることを確かめる（D04 §12 #4）。

    検査の向きは片方向である（D04 §8 v1.8）。**`required_inputs` のキーはすべて、使用箇所が
    宣言した起動条件名のどれかを指していなければならない**（余計なキーを許さない）が、逆向き
    （すべての起動条件名がキーに現れること）は求めない。`required_inputs` を持つのは契約で、
    起動条件の名前を選ぶのは使用箇所であり（D04 §3.1）、必須入力を1件も宣言しない契約
    （段階2の5部品がそうである。D05 §4.3）を使えなくしないためである。
    """
    errors: list[CompileError] = []
    trigger_names = set(instance.evaluation.names)
    for trigger_name, input_names in contract.evaluation_spec.required_inputs.items():
        if trigger_name not in trigger_names:
            errors.append(
                _error(
                    "#4",
                    CompileRejection.SCHEDULE_NOT_ALLOWED,
                    instance.instance_id,
                    "evaluation",
                    f"the contract declares required inputs for a trigger named"
                    f" {trigger_name!r}, which this instance does not declare"
                    f" (declared: {sorted(trigger_names)})",
                )
            )
            continue
        for input_name in input_names:
            if input_name not in contract.inputs:
                errors.append(
                    _error(
                        "#4",
                        CompileRejection.SCHEDULE_NOT_ALLOWED,
                        instance.instance_id,
                        f"evaluation.{trigger_name}",
                        f"required input {input_name!r} is not declared by the contract",
                    )
                )
            elif input_name not in instance.inputs:
                errors.append(
                    _error(
                        "#4",
                        CompileRejection.SCHEDULE_NOT_ALLOWED,
                        instance.instance_id,
                        f"evaluation.{trigger_name}",
                        f"required input {input_name!r} is not connected",
                    )
                )
    return errors


# --- 段9: 能力検査のうち契約を要するもの -------------------------------------


def _stage9_history_windows(
    definition: StrategyDefinition, resolved: Mapping[str, ComponentRegistration]
) -> tuple[CompileError, ...]:
    errors: list[CompileError] = []
    for instance in definition.components:
        contract = resolved[instance.instance_id].contract
        for input_name, spec in contract.inputs.items():
            binding = instance.inputs.get(input_name)
            if binding is None:  # pragma: no cover - 段5 が先に拒否する
                continue
            has_output_source = any(isinstance(source, OutputRef) for source in binding.sources)
            error = capability.check_output_history_window(
                instance.instance_id, input_name, spec.read_spec, has_output_source
            )
            if error is not None:
                errors.append(error)
    return tuple(errors)


# --- 段10: 組み立て ----------------------------------------------------------


def _stage10_assemble(
    definition: StrategyDefinition,
    resolved: Mapping[str, ComponentRegistration],
    parameters: Mapping[str, Mapping[str, ResolvedParameter]],
    symbols: Mapping[str, Symbol | None],
    input_plans: Mapping[str, Mapping[str, InputPlan]],
    evaluation_order: tuple[str, ...],
) -> CompileResult:
    by_id = {instance.instance_id: instance for instance in definition.components}
    components = tuple(
        CompiledComponent(
            instance_id=instance_id,
            contract_ref=by_id[instance_id].contract_ref,
            implementation_ref=resolved[instance_id].implementation_ref,
            parameters=dict(parameters[instance_id]),
            input_plans=dict(input_plans[instance_id]),
            triggers=by_id[instance_id].evaluation.triggers,
            required_inputs=dict(resolved[instance_id].contract.evaluation_spec.required_inputs),
            state_spec=resolved[instance_id].contract.state_spec,
            symbol=symbols[instance_id],
        )
        for instance_id in evaluation_order
    )
    strategy_ref = strategy_ref_for(definition)
    compiled_ref = compiled_strategy_ref(strategy_ref, evaluation_order, components)
    strategy_symbol = next(symbol for symbol in symbols.values() if symbol is not None)
    roles = CompiledRoles(
        trigger=definition.trigger,
        order=definition.order,
        protection=definition.protection,
        market_state=definition.market_state,
        execution_filter=definition.execution_filter,
        exit=definition.exit,
    )
    compiled = CompiledStrategy(
        schema_version=definition.schema_version,
        strategy_ref=strategy_ref,
        compiled_ref=compiled_ref,
        symbol=strategy_symbol,
        components=components,
        evaluation_order=evaluation_order,
        roles=roles,
        entry_policy=definition.entry_policy,
        opportunity_validity=definition.opportunity_validity,
        opportunity_concurrency=definition.opportunity_concurrency,
    )
    return CompileSucceeded(compiled=compiled)
