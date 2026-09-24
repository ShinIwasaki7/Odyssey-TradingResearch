"""段階3 のコンパイラ（D05 §5.3・§5.6、D04 §12 #8〜#15）。

| 何を確かめるか | 節 |
|---|---|
| 検証戦略 B がコンパイルを通る。評価順・確認の計画・保持本数の計画 | D05 §9.2・§5.3・§6.12 |
| 能力検査から外した構成が通る（確認・待機・遡り・区間の一致・履歴窓・再検査） | D05 §5.6 |
| 新しい検査 a〜h がそれぞれ拒否する宣言と、その拒否の区分・検査の番号 | D05 §5.6、D04 §12 #8〜#15 |
| 検証戦略 A のコンパイル結果と指紋が変わらない | D05 §5.5 |
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.catalog.conditions import compare
from odyssey_fx.strategy.catalog.features import ema, extreme
from odyssey_fx.strategy.catalog.filters import condition_filter
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.inputs import ResolvedInputsView, ResolvedParameterView
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    ComponentRegistry,
    ParameterConstraint,
    StatelessImplementation,
    build_registry,
    declared_implementation_ref,
)
from odyssey_fx.strategy.compiler import capability
from odyssey_fx.strategy.compiler.compiled import (
    CompiledStrategy,
    CompileFailed,
    CompileRejection,
    CompileSucceeded,
    ConfirmationPlan,
    InputPlan,
    OutputRetentionPlan,
    ResolvedOutputSource,
)
from odyssey_fx.strategy.compiler.graph import EdgeKind, build_dependency_graph
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import (
    MARKET_PERMISSION_V1,
    OPPORTUNITY_V1,
    PRICE_V1,
)
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline, DeadlineAction
from odyssey_fx.strategy.declarations.evaluation import (
    AllowedInputEvent,
    EvaluationSchedule,
    EvaluationSpec,
    OnBarClose,
    OnInputEvent,
    OnRuntimeEvent,
    RuntimeEventKind,
)
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.missing import Error as MissingPolicyError
from odyssey_fx.strategy.declarations.missing import UsePrevious
from odyssey_fx.strategy.declarations.opportunity import (
    OpportunityValiditySpec,
    ValidityBinding,
    ValidityMode,
)
from odyssey_fx.strategy.declarations.read_spec import (
    BarsWindow,
    DeliveredEvent,
    DurationWindow,
    HistoryWindow,
    LatestAvailable,
    ParameterRef,
)
from odyssey_fx.strategy.declarations.refs import OutputRef
from odyssey_fx.strategy.declarations.specs import (
    InputArity,
    InputBinding,
    InputSpec,
    IntValue,
    OutputSpec,
    PortKind,
)
from odyssey_fx.strategy.declarations.temporal import (
    AlignmentRequirement,
    AlignmentRule,
    TemporalConstraints,
)
from tests.fixtures.strategy import strategy_a as fixture_a
from tests.fixtures.strategy.strategy_b import (
    DAILY_SERIES,
    EVALUATION_ORDER_BY_RULE,
    EVALUATION_ORDER_IN_DESIGN,
    HOURLY_SERIES,
    M15_SERIES,
    TIMEFRAMES,
    strategy_b,
)

#: 検証戦略 A の解決済み設定の指紋（段階3 のコンパイラへ変えても同じであること。D05 §5.5）。
STRATEGY_A_COMPILED_DIGEST = "83bd61486c71366200419a965cc072fb305aafde9f56fc1fc032cb225c4a2b2e"


# --- 共有の道具 ---------------------------------------------------------------


def _compile(
    definition: StrategyDefinition, registry: ComponentRegistry = INITIAL_CATALOG
) -> CompileFailed | CompileSucceeded:
    return compile_strategy(definition, registry, TIMEFRAMES)


def _compiled(definition: StrategyDefinition) -> CompiledStrategy:
    result = _compile(definition)
    assert isinstance(result, CompileSucceeded), str(result)
    return result.compiled


def _failed(result: CompileFailed | CompileSucceeded) -> list[tuple[str, CompileRejection]]:
    assert isinstance(result, CompileFailed), "expected the compilation to be rejected"
    return [(error.check_id, error.rejection) for error in result.errors]


def _replace(definition: StrategyDefinition, **overrides: object) -> StrategyDefinition:
    fields: dict[str, object] = {
        "strategy_id": definition.strategy_id,
        "version": definition.version,
        "components": definition.components,
        "market_state": definition.market_state,
        "trigger": definition.trigger,
        "execution_filter": definition.execution_filter,
        "order": definition.order,
        "protection": definition.protection,
        "exit": definition.exit,
        "entry_policy": definition.entry_policy,
        "opportunity_validity": definition.opportunity_validity,
        "opportunity_concurrency": definition.opportunity_concurrency,
    }
    fields.update(overrides)
    return StrategyDefinition(**fields)  # type: ignore[arg-type]


def _swap(
    definition: StrategyDefinition, instance_id: str, **overrides: object
) -> StrategyDefinition:
    components = []
    for item in definition.components:
        if item.instance_id != instance_id:
            components.append(item)
            continue
        fields: dict[str, object] = {
            "instance_id": item.instance_id,
            "contract_ref": item.contract_ref,
            "inputs": dict(item.inputs),
            "parameters": dict(item.parameters),
            "evaluation": item.evaluation,
        }
        fields.update(overrides)
        components.append(ComponentInstance(**fields))  # type: ignore[arg-type]
    return _replace(definition, components=tuple(components))


def _with_registrations(*extra: ComponentRegistration) -> ComponentRegistry:
    return build_registry((*INITIAL_CATALOG.registrations.values(), *extra))


def _two_series(name_a: str, series_a: SeriesId, name_b: str, series_b: SeriesId) -> object:
    return EvaluationSchedule(triggers=(OnBarClose(name_a, series_a), OnBarClose(name_b, series_b)))


# --- 検証戦略 B（D05 §9.2） --------------------------------------------------


def test_strategy_b_compiles() -> None:
    """D05 §9.2: 検証戦略 B の使用箇所12件と戦略全体の宣言がコンパイルを通る。"""
    compiled = _compiled(strategy_b())

    assert len(compiled.components) == 12
    assert str(compiled.symbol) == "USDJPY"
    assert compiled.roles.market_state == OutputRef("market_state", "permission")
    assert compiled.roles.execution_filter == OutputRef("entry_filter", "confirmation")
    assert compiled.roles.exit == OutputRef("trailing", "action")


def test_strategy_b_is_ordered_by_the_stage_rule() -> None:
    """D05 §5.4: 段ごとに `instance_id` 順（v1.3 の決定）で並べた評価順になる。

    市場状態 → 取引機会（D05 §7.6 の因果辺）と、注文意図 → 追従（D04 §12 の建玉の因果辺）
    を含む。
    """
    compiled = _compiled(strategy_b())

    assert compiled.evaluation_order == EVALUATION_ORDER_BY_RULE
    order = compiled.evaluation_order
    assert order.index("market_state") < order.index("entry_trigger")
    assert order.index("entry_order") < order.index("trailing")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "要決定: D05 §9.2 末尾・T02 §1.3 に書かれた評価順は、D05 §5.4 の段の規則からは導けない"
        "（規則の出力は EVALUATION_ORDER_BY_RULE）。どちらに合わせるかは人間の決定を待つ"
    ),
)
def test_strategy_b_matches_the_order_written_in_the_design() -> None:
    """D05 §9.2 末尾が書く評価順（`daily_ema` → … → `trailing`）。"""
    assert _compiled(strategy_b()).evaluation_order == EVALUATION_ORDER_IN_DESIGN


def test_the_design_order_and_the_rule_order_hold_the_same_components() -> None:
    """2つの並びの違いは順序だけで、使用箇所の集合は同じである。"""
    assert sorted(EVALUATION_ORDER_IN_DESIGN) == sorted(EVALUATION_ORDER_BY_RULE)


def test_the_market_state_edge_is_drawn_for_strategy_b() -> None:
    """D04 §12 #11: 市場状態から取引機会を出す使用箇所へ因果辺を1本引く。"""
    definition = strategy_b()
    graph = build_dependency_graph(
        definition,
        order_role=definition.order,
        market_state_role=definition.market_state,
        trigger_role=definition.trigger,
    )

    market_edges = [edge for edge in graph.edges if edge.kind is EdgeKind.MARKET_STATE]
    assert [(edge.source_instance, edge.target_instance) for edge in market_edges] == [
        ("market_state", "entry_trigger")
    ]


def test_strategy_b_carries_the_confirmation_plan() -> None:
    """D05 §5.3・§7.7（T02 §1.3）: 確認部品・確認足の系列・開始足・期限を1件にまとめる。"""
    plan = _compiled(strategy_b()).roles.confirmation

    assert plan == ConfirmationPlan(
        filter_instance="entry_filter",
        series=M15_SERIES,
        include_start_bar=True,
        deadline=BarsDeadline(bars=4),
        on_deadline=DeadlineAction.EXPIRE,
    )


def test_the_confirmation_plan_follows_the_declared_start_bar() -> None:
    """D05 §4.8: 開始足で確認を始めるかは使用箇所が明示した値をそのまま載せる。"""
    plan = _compiled(strategy_b(include_start_bar=False, deadline_bars=2)).roles.confirmation

    assert plan is not None
    assert plan.include_start_bar is False
    assert plan.deadline == BarsDeadline(bars=2)


def test_strategy_b_keeps_one_output_per_reference() -> None:
    """D05 §6.12（T02 §12）: 戦略 B は出力参照を最新1件でしか読まないので、どれも 1 本。"""
    retention = _compiled(strategy_b()).output_retention

    assert dict(retention.by_output) == {
        OutputRef("daily_above_ema", "condition"): 1,
        OutputRef("daily_ema", "value"): 1,
        OutputRef("m15_above_ema", "condition"): 1,
        OutputRef("m15_ema", "value"): 1,
        OutputRef("no_short", "condition"): 1,
        OutputRef("stop_level", "level"): 1,
    }


def test_event_outputs_are_not_retained() -> None:
    """D05 §6.12: 配送イベント（確認結果）の出力は保持本数の計画に載せない。"""
    retention = _compiled(strategy_b()).output_retention

    assert OutputRef("entry_filter", "confirmation") not in retention.by_output
    assert OutputRef("entry_trigger", "opportunity") not in retention.by_output


# --- 検証戦略 A は変わらない（D05 §5.5） --------------------------------------


def test_strategy_a_compiles_to_the_same_digest() -> None:
    """段階3 の検査と計画を足しても、検証戦略 A の解決済み設定の指紋は変わらない。"""
    compiled = _compiled(fixture_a.strategy_a())

    assert compiled.compiled_ref.digest.hex == STRATEGY_A_COMPILED_DIGEST
    assert compiled.evaluation_order == fixture_a.EVALUATION_ORDER


def test_strategy_a_has_no_confirmation_plan() -> None:
    """D05 §5.6: 確認部品の無い戦略では確認の計画は `None`。"""
    assert _compiled(fixture_a.strategy_a()).roles.confirmation is None


def test_strategy_a_retains_its_latest_value_outputs_once() -> None:
    """D05 §6.12: 最新1件で読まれる出力は 1 本。配送イベントは載らない。"""
    retention = _compiled(fixture_a.strategy_a()).output_retention

    assert dict(retention.by_output) == {
        OutputRef("breakout_level", "level"): 1,
        OutputRef("stop_level", "level"): 1,
    }


def test_the_plan_types_reject_malformed_values() -> None:
    """保持本数は 1 以上。確認の計画は確認部品と一致する。"""
    with pytest.raises(ValueError, match="at least 1"):
        OutputRetentionPlan(by_output={OutputRef("a", "b"): 0})
    compiled = _compiled(strategy_b())
    assert compiled.roles.confirmation is not None
    with pytest.raises(ValueError, match="filter_instance"):
        replace(
            compiled.roles,
            confirmation=replace(compiled.roles.confirmation, filter_instance="other"),
        )
    with pytest.raises(ValueError, match="must be None"):
        replace(_compiled(fixture_a.strategy_a()).roles, confirmation=compiled.roles.confirmation)


# --- 解除した構成（D05 §5.6 の表） --------------------------------------------


def test_failing_the_run_on_a_missing_recheck_is_now_accepted() -> None:
    """D05 §5.6: 再検査の記録（`ValidityRecheck`）が書き先になったので `Error` を解除した。"""
    definition = _replace(
        strategy_b(),
        opportunity_validity=OpportunityValiditySpec(
            bindings=(
                ValidityBinding(
                    source=OutputRef("daily_above_ema", "condition"),
                    mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST,
                    on_missing=MissingPolicyError(),
                ),
            )
        ),
    )

    assert isinstance(_compile(definition), CompileSucceeded)


def _compare_with_left(on_missing: object, version: int) -> ComponentRegistration:
    """`price_compare` の左入力の欠損方針だけを差し替えた試験用の契約を登録する。"""
    contract = replace(
        compare.CONTRACT,
        version=version,
        inputs={
            **compare.CONTRACT.inputs,
            "left": replace(
                compare.CONTRACT.inputs["left"],
                read_spec=LatestAvailable(max_age=None, on_missing=on_missing),  # type: ignore[arg-type]
            ),
        },
    )
    return replace(compare.REGISTRATION, contract=contract)


_GO_BACK = UsePrevious(
    max_lookback=BarsWindow(2), allowed_reasons=(MissingInputReason.LATEST_BAR_UNAVAILABLE,)
)


def test_going_back_on_a_market_data_latest_read_is_accepted() -> None:
    """D04 §12 #10: 遡りは最新1件の読み方で、接続元が市場データ参照なら書ける。"""
    registration = _compare_with_left(_GO_BACK, version=90)
    definition = _swap(
        strategy_b(), "m15_above_ema", contract_ref=contract_ref_for(registration.contract)
    )

    assert isinstance(_compile(definition, _with_registrations(registration)), CompileSucceeded)


def test_an_alignment_requirement_is_now_accepted() -> None:
    """D05 §5.6: 観測区間の一致は守らせる仕組み（D05 §6.7）ができたので解除した。"""
    contract = replace(
        compare.CONTRACT,
        version=91,
        temporal_constraints=TemporalConstraints(
            warmup=None,
            alignment=(
                AlignmentRequirement(
                    input_names=("left", "right"), rule=AlignmentRule.SAME_OBSERVATION_INTERVAL
                ),
            ),
        ),
    )
    registration = replace(compare.REGISTRATION, contract=contract)
    definition = _swap(strategy_b(), "m15_above_ema", contract_ref=contract_ref_for(contract))

    assert isinstance(_compile(definition, _with_registrations(registration)), CompileSucceeded)


def _stop_level_reading(upstream: str) -> StrategyDefinition:
    """`stop_level` が上流の価格の出力を、本数で数える履歴窓（20本、当該足を除く1本）で読む。"""
    return _swap(
        strategy_b(),
        "stop_level",
        inputs={"prices": InputBinding(sources=(OutputRef(upstream, "value"),))},
    )


def test_reading_an_output_through_a_bar_window_is_accepted() -> None:
    """D05 §5.6: 本数で数える履歴窓で出力参照を読む接続は解除した（Q18 決定）。

    保持本数は「窓の本数＋当該足を除く本数」（D05 §6.12）。同じ出力を最新1件でも読むので、
    最大値の 21 本になる。
    """
    compiled = _compiled(_stop_level_reading("m15_ema"))

    assert compiled.output_retention.by_output[OutputRef("m15_ema", "value")] == 21


# --- 検査 a（D04 §12 #8）: 確認部品の開始足の宣言 -------------------------------


def test_a_confirmation_component_must_state_the_start_bar() -> None:
    """検査 a: 使用箇所が `include_start_bar` を明示しなければ `ROLE_MISMATCH`。"""
    definition = _swap(strategy_b(), "entry_filter", parameters={})

    assert _failed(_compile(definition)) == [("#8", CompileRejection.ROLE_MISMATCH)]


def test_a_confirmation_contract_must_declare_the_start_bar() -> None:
    """検査 a: 契約が `include_start_bar` を持たない確認部品は `ROLE_MISMATCH`。"""
    contract = replace(condition_filter.CONTRACT, version=92, parameters={})
    registration = replace(condition_filter.REGISTRATION, contract=contract)
    definition = _swap(
        strategy_b(), "entry_filter", contract_ref=contract_ref_for(contract), parameters={}
    )

    result = _compile(definition, _with_registrations(registration))

    assert ("#8", CompileRejection.ROLE_MISMATCH) in _failed(result)


# --- 検査 e（D04 §12 #12）: パラメータどうしの関係 -------------------------------


def test_an_indicator_window_shorter_than_twice_the_period_is_rejected() -> None:
    """検査 e: `window_bars >= 2 * period` を登録の検証関数がパラメータ解決の後に確かめる。"""
    definition = _swap(
        strategy_b(), "m15_ema", parameters={"period": IntValue(20), "window_bars": IntValue(30)}
    )

    result = _compile(definition)

    assert _failed(result) == [("#12", CompileRejection.PARAMETER_INVALID)]
    assert isinstance(result, CompileFailed)
    assert "window_bars=30" in result.errors[0].message


def test_a_relation_check_that_raises_is_rejected() -> None:
    """検査 e: 検証関数が例外で終わった場合も黙って通さず拒否する。"""

    def broken(parameters: Mapping[str, ResolvedParameterView]) -> bool:
        raise RuntimeError("boom")

    registration = replace(
        ema.REGISTRATION,
        parameter_constraint=ParameterConstraint(
            reads=("period",), check=broken, message="relation broke"
        ),
    )
    registry = build_registry(
        (
            *(
                item
                for item in INITIAL_CATALOG.registrations.values()
                if item is not ema.REGISTRATION
            ),
            registration,
        )
    )

    result = _compile(strategy_b(), registry)

    assert _failed(result) == [("#12", CompileRejection.PARAMETER_INVALID)]
    assert isinstance(result, CompileFailed)
    assert "RuntimeError: boom" in result.errors[0].message


# --- 検査 b・g・h（D04 §12 #9・#14・#15）: 起動系列がただ1つ ----------------------


def test_a_confirmation_component_on_two_series_is_rejected() -> None:
    """検査 b: 確認足の系列が2つあると開始足も期限も決まらない。"""
    definition = _swap(
        strategy_b(),
        "entry_filter",
        evaluation=_two_series("m15", M15_SERIES, "h1", HOURLY_SERIES),
    )

    assert _failed(_compile(definition)) == [("#9", CompileRejection.SCHEDULE_NOT_ALLOWED)]


def test_an_exit_component_on_two_series_is_rejected() -> None:
    """検査 g: 同じ建玉への損切り更新が同じ判断時点に2件出うる。"""
    definition = _swap(
        strategy_b(),
        "trailing",
        evaluation=_two_series("h1", HOURLY_SERIES, "m15", M15_SERIES),
    )

    assert _failed(_compile(definition)) == [("#14", CompileRejection.SCHEDULE_NOT_ALLOWED)]


def test_an_output_only_wait_on_two_series_is_rejected() -> None:
    """検査 h: 出力参照だけの入力の本数の待機期限は、起動系列が1つでなければ数えられない。

    `market_state` の `long_allowed`（出力参照だけ・`BarsDeadline(1)` で待つ）が、日足と
    1時間足の2系列で起動する宣言。
    """
    definition = _swap(
        strategy_b(),
        "market_state",
        evaluation=_two_series("d1", DAILY_SERIES, "h1", HOURLY_SERIES),
    )

    assert _failed(_compile(definition)) == [("#15", CompileRejection.SCHEDULE_NOT_ALLOWED)]


def test_a_market_data_wait_on_two_series_is_not_checked_by_h() -> None:
    """検査 h は接続元が出力参照だけの入力に限る。市場データの待機は系列で数えられる。

    `daily_ema`（市場データを待つ）を2系列で起動しても、検査 h は拒否しない。
    """
    definition = _swap(
        strategy_b(),
        "daily_ema",
        evaluation=_two_series("d1", DAILY_SERIES, "h1", HOURLY_SERIES),
    )

    assert isinstance(_compile(definition), CompileSucceeded)


# --- 検査 d（D04 §12 #11）: 市場状態の因果辺を含めた循環 --------------------------


def _permission_from_opportunity() -> ComponentRegistration:
    """取引機会の配送から取引許可を出す試験用の部品（市場状態が取引機会の下流になる）。"""

    def evaluate(
        inputs: ResolvedInputsView, parameters: Mapping[str, ResolvedParameterView]
    ) -> ComponentOutputs:
        raise AssertionError("the compiler never evaluates components")

    implementation_ref = declared_implementation_ref("permission_probe", 1)
    contract = ComponentContract(
        component_id="permission_probe",
        version=1,
        implementation_ref=implementation_ref,
        inputs={
            "opportunity": InputSpec(
                data_type=OPPORTUNITY_V1,
                kind=PortKind.EVENT,
                arity=InputArity(min_count=1, max_count=1),
                read_spec=DeliveredEvent(),
            )
        },
        outputs={
            "permission": OutputSpec(
                data_type=MARKET_PERMISSION_V1,
                kind=PortKind.VALUE,
                reference_schema={},
                retrigger_mode=None,
            )
        },
        parameters={},
        evaluation_spec=EvaluationSpec(
            allowed=(AllowedInputEvent(input_names=("opportunity",)),), fixed=False
        ),
        state_spec=None,
        temporal_constraints=TemporalConstraints(warmup=None, alignment=()),
    )
    return ComponentRegistration(
        contract=contract,
        implementation=StatelessImplementation(evaluate=evaluate),
        implementation_ref=implementation_ref,
    )


def test_a_cycle_through_the_market_state_edge_is_rejected() -> None:
    """検査 d: 市場状態が取引機会の下流にあると、因果辺と合わせて閉路になる。"""
    registration = _permission_from_opportunity()
    definition = _swap(
        strategy_b(),
        "market_state",
        contract_ref=contract_ref_for(registration.contract),
        inputs={"opportunity": InputBinding(sources=(OutputRef("entry_trigger", "opportunity"),))},
        evaluation=EvaluationSchedule(triggers=(OnInputEvent("opp", "opportunity"),)),
    )

    result = _compile(definition, _with_registrations(registration))

    assert _failed(result) == [("#11", CompileRejection.DEPENDENCY_CYCLE)]
    assert isinstance(result, CompileFailed)
    assert "MARKET_STATE" in result.errors[0].message


# --- 検査 c（D04 §12 #10）: 待機・遡りの組合せ ------------------------------------


def test_going_back_on_an_output_reference_is_rejected() -> None:
    """検査 c: 出力参照は最新1件しか保持しないので、遡る先が無い。"""
    registration = _compare_with_left(_GO_BACK, version=93)
    definition = _swap(
        strategy_b(),
        "m15_above_ema",
        contract_ref=contract_ref_for(registration.contract),
        inputs={
            "left": InputBinding(sources=(OutputRef("m15_ema", "value"),)),
            "right": InputBinding(sources=(OutputRef("m15_ema", "value"),)),
        },
    )

    result = _compile(definition, _with_registrations(registration))

    assert _failed(result) == [("#10", CompileRejection.UNSUPPORTED_CONFIGURATION)]


def test_going_back_on_a_history_window_is_rejected() -> None:
    """検査 c: 遡りは固定本数の窓の穴埋めには使えない（D04 §6.3）。"""
    contract = replace(
        ema.CONTRACT,
        version=94,
        inputs={
            "prices": replace(
                ema.CONTRACT.inputs["prices"],
                read_spec=HistoryWindow(
                    window=BarsWindow(ParameterRef("window_bars")), on_missing=_GO_BACK
                ),
            )
        },
    )
    registration = replace(ema.REGISTRATION, contract=contract)
    definition = _swap(strategy_b(), "m15_ema", contract_ref=contract_ref_for(contract))

    result = _compile(definition, _with_registrations(registration))

    assert _failed(result) == [("#10", CompileRejection.UNSUPPORTED_CONFIGURATION)]


# --- 検査 f（D04 §12 #13）: 出力参照を履歴窓で読む接続 ------------------------------


def test_reading_an_output_of_a_two_series_upstream_is_rejected() -> None:
    """検査 f の (2): 上流が2系列で起動すると「過去 N 本」がどちらの系列か決まらない。"""
    definition = _swap(
        _stop_level_reading("m15_ema"),
        "m15_ema",
        evaluation=_two_series("m15", M15_SERIES, "h1", HOURLY_SERIES),
    )

    assert _failed(_compile(definition)) == [("#13", CompileRejection.UNSUPPORTED_CONFIGURATION)]


def _history_plan(
    upstream: str, window: BarsWindow | DurationWindow
) -> dict[str, dict[str, InputPlan]]:
    return {
        "reader": {
            "prices": InputPlan(
                input_name="prices",
                data_type=PRICE_V1,
                kind=PortKind.VALUE,
                read_spec=HistoryWindow(window=BarsWindow(3)),
                sources=(ResolvedOutputSource(upstream, "value"),),
                resolved_window=window,
            )
        }
    }


def _reader_definition(
    upstream_schedule: EvaluationSchedule, reader_schedule: EvaluationSchedule
) -> StrategyDefinition:
    """検査 f の関数だけに渡す宣言（契約の許す起動条件は段7 が見るので、ここでは問わない）。"""
    base = fixture_a.strategy_a()
    upstream = ComponentInstance(
        instance_id="upstream",
        contract_ref=contract_ref_for(ema.CONTRACT),
        inputs={},
        parameters={},
        evaluation=upstream_schedule,
    )
    reader = ComponentInstance(
        instance_id="reader",
        contract_ref=contract_ref_for(extreme.CONTRACT),
        inputs={"prices": InputBinding(sources=(OutputRef("upstream", "value"),))},
        parameters={},
        evaluation=reader_schedule,
    )
    return _replace(base, components=(*base.components, upstream, reader))


_ON_HOUR = EvaluationSchedule(triggers=(OnBarClose("h1", HOURLY_SERIES),))


def test_reading_an_output_through_a_duration_window_is_rejected() -> None:
    """検査 f の (1): 経過時間の窓は保持本数をコンパイル時に導けない（拒否を続ける）。"""
    from datetime import timedelta

    errors = capability.check_output_history_windows(
        _reader_definition(_ON_HOUR, _ON_HOUR),
        _history_plan("upstream", DurationWindow(timedelta(hours=3))),
    )

    assert [(error.check_id, error.rejection) for error in errors] == [
        ("#13", CompileRejection.UNSUPPORTED_CONFIGURATION)
    ]
    assert "condition 1" in errors[0].message


def test_reading_an_output_of_a_runtime_event_upstream_is_rejected() -> None:
    """検査 f の (3): 実行時イベントで起動した出力は観測した足を持たない。"""
    errors = capability.check_output_history_windows(
        _reader_definition(
            EvaluationSchedule(
                triggers=(OnRuntimeEvent("filled", RuntimeEventKind.POSITION_OPENED),)
            ),
            _ON_HOUR,
        ),
        _history_plan("upstream", BarsWindow(3)),
    )

    assert [error.check_id for error in errors] == ["#13"]
    assert "condition 3" in errors[0].message


def test_a_reader_started_by_an_event_cannot_use_an_output_history() -> None:
    """検査 f の (4): 読む側に対象区間が無いと窓の末尾を決められない。"""
    errors = capability.check_output_history_windows(
        _reader_definition(_ON_HOUR, EvaluationSchedule(triggers=(OnInputEvent("opp", "prices"),))),
        _history_plan("upstream", BarsWindow(3)),
    )

    assert [error.check_id for error in errors] == ["#13"]
    assert "condition 4" in errors[0].message


# --- 拒否を続ける構成 -----------------------------------------------------------


def test_a_warmup_requirement_is_still_rejected() -> None:
    """D05 §5.6: ウォームアップ本数（`WarmupSpec`）は段階3 でも拒否を続ける。"""
    from odyssey_fx.strategy.declarations.temporal import WarmupSpec

    errors = capability.check_temporal_constraints(
        {
            "x": replace(
                compare.CONTRACT,
                temporal_constraints=TemporalConstraints(
                    warmup=WarmupSpec(series=HOURLY_SERIES, bars=5), alignment=()
                ),
            )
        }
    )

    assert [(error.check_id, error.rejection) for error in errors] == [
        ("#7", CompileRejection.UNSUPPORTED_CONFIGURATION)
    ]


# --- 指紋を計算できない宣言（D05 §5.5・§9.2） --------------------------------------


def test_a_confirmation_deadline_in_time_is_rejected_not_raised() -> None:
    """期間で書いた確認期限は指紋を計算できない。例外ではなく拒否として返す。"""
    from datetime import timedelta

    from odyssey_fx.strategy.declarations.entry_policy import AwaitConfirmation, DurationDeadline

    definition = _replace(
        strategy_b(),
        entry_policy=AwaitConfirmation(deadline=DurationDeadline(timedelta(hours=1))),
    )

    assert _failed(_compile(definition)) == [("#7", CompileRejection.UNSUPPORTED_CONFIGURATION)]


def test_a_registered_contract_with_a_duration_is_rejected_not_raised() -> None:
    """期間値を持つ契約も指紋を計算できない。段1 で拒否として返す。"""
    from datetime import timedelta

    contract = replace(
        compare.CONTRACT,
        version=95,
        inputs={
            **compare.CONTRACT.inputs,
            "left": replace(
                compare.CONTRACT.inputs["left"],
                read_spec=LatestAvailable(max_age=timedelta(hours=2)),
            ),
        },
    )
    registration = replace(compare.REGISTRATION, contract=contract)
    stand_in = replace(contract_ref_for(compare.CONTRACT), version=95)
    definition = _swap(strategy_b(), "m15_above_ema", contract_ref=stand_in)

    assert _failed(_compile(definition, _with_registrations(registration))) == [
        ("#7", CompileRejection.UNSUPPORTED_CONFIGURATION)
    ]
