"""コンパイル時検査の一覧（D04 §12、D05 §5）。

D04 §12 の検査1〜7（＋6b）が、それぞれ**どの宣言の誤りを拒否するか**を1件ずつ確かめる。
拒否は例外ではなく戻り値で返り、どの検査でどの宣言のどこが原因かを必ず持つ。黙って無視
する経路を作らない。

| 検査 | ここで確かめること |
|---|---|
| #1 | 参照の存在（未登録の契約、無い使用箇所・出力） |
| #2 | 型の整合（データ型・口の区分・接続数・読み方・銘柄） |
| #3 | パラメータ（型・範囲・列挙値・パラメータ参照） |
| #4 | 評価スケジュールが契約の許す範囲内であること |
| #5 | 役割フィールドの型要求と、後続確認と発注方針の整合 |
| #6 | 依存グラフの循環（明示辺＋エンジン上の因果辺） |
| #6b | 出力仕様の付随条件（再武装・根拠値・状態） |
| #7 | 能力検査（段階2で対応しない構成） |
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.refs import ContentDigest, ContractRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import (
    FixedUtcAlignment,
    TimeframeDefinition,
)
from odyssey_fx.strategy.catalog.exits import fixed_rr
from odyssey_fx.strategy.catalog.features import extreme
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.orders import market
from odyssey_fx.strategy.catalog.protection import level_stop
from odyssey_fx.strategy.catalog.registry import (
    ComponentRegistration,
    StatefulImplementation,
    build_registry,
)
from odyssey_fx.strategy.catalog.triggers import breakout
from odyssey_fx.strategy.compiler.compiled import (
    CompileFailed,
    CompileRejection,
    CompileSucceeded,
)
from odyssey_fx.strategy.compiler.graph import EdgeKind, build_dependency_graph
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import CONDITION_STATE_V1
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.entry_policy import AwaitConfirmation, BarsDeadline
from odyssey_fx.strategy.declarations.evaluation import (
    EvaluationSchedule,
    OnBarClose,
    OnRuntimeEvent,
    RuntimeEventKind,
)
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.missing import Error as MissingPolicyError
from odyssey_fx.strategy.declarations.opportunity import (
    OpportunityValiditySpec,
    ValidityBinding,
    ValidityMode,
)
from odyssey_fx.strategy.declarations.refs import (
    MarketDataField,
    MarketDataRef,
    OutputRef,
)
from odyssey_fx.strategy.declarations.specs import BoolValue, InputBinding, IntValue, StrValue
from odyssey_fx.strategy.declarations.state_spec import LiteralInitialState, StateSpec
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES, TIMEFRAMES, strategy_a

breakout_contract = breakout.CONTRACT


def _compile(definition: StrategyDefinition) -> CompileFailed | CompileSucceeded:
    return compile_strategy(definition, INITIAL_CATALOG, TIMEFRAMES)


def _replace(definition: StrategyDefinition, **overrides: object) -> StrategyDefinition:
    """戦略定義の一部を差し替える（宣言は不変なので作り直す）。"""
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


def _swap_component(
    definition: StrategyDefinition, instance_id: str, **overrides: object
) -> StrategyDefinition:
    """1つの使用箇所を差し替えた戦略定義を作る。"""
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


def _rejections(result: CompileFailed | CompileSucceeded) -> list[CompileRejection]:
    assert isinstance(result, CompileFailed), "expected the compilation to be rejected"
    return [error.rejection for error in result.errors]


# --- 成功経路 ----------------------------------------------------------------


def test_the_reference_strategy_compiles() -> None:
    """D05 §9: 検証戦略 A はそのままコンパイルできる。"""
    result = _compile(strategy_a())

    assert isinstance(result, CompileSucceeded)
    assert result.compiled.evaluation_order == (
        "breakout_level",
        "stop_level",
        "entry_trigger",
        "entry_order",
        "initial_stop",
        "take_profit",
    )


def test_the_window_size_is_resolved_to_a_concrete_number() -> None:
    """D04 §12 #3: パラメータ参照はコンパイル時に具体値へ解決する。"""
    result = _compile(strategy_a())
    assert isinstance(result, CompileSucceeded)

    plan = result.compiled.component("breakout_level").input_plans["prices"]
    assert plan.resolved_window is not None
    assert plan.resolved_window.count == 20  # type: ignore[union-attr]


def test_the_ratio_parameter_is_converted_to_a_decimal() -> None:
    """D05 §4.4: 比率は部品へ渡す前に厳密な10進数へ変換する。"""
    result = _compile(strategy_a())
    assert isinstance(result, CompileSucceeded)

    parameter = result.compiled.component("take_profit").parameters["reward_risk"]
    assert parameter.decimal_value is not None
    assert str(parameter.decimal_value) == "2.0"


def test_the_same_declaration_always_compiles_to_the_same_digest() -> None:
    """全体計画 §8.2: 同じ宣言からは同じ解決済み設定になる。"""
    first = _compile(strategy_a())
    second = _compile(strategy_a())
    assert isinstance(first, CompileSucceeded)
    assert isinstance(second, CompileSucceeded)

    assert first.compiled.compiled_ref == second.compiled.compiled_ref


def test_a_different_parameter_changes_the_compiled_digest() -> None:
    """D04 §13.2: 解決済み設定の指紋は探索で作った割当まで含む。"""
    first = _compile(strategy_a())
    second = _compile(strategy_a(reward_risk=3.0))
    assert isinstance(first, CompileSucceeded)
    assert isinstance(second, CompileSucceeded)

    assert first.compiled.compiled_ref != second.compiled.compiled_ref


# --- #1 参照の存在 -----------------------------------------------------------


def test_an_unregistered_contract_is_rejected() -> None:
    """D04 §12 #1: カタログに無い部品は指せない。"""
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        contract_ref=ContractRef("made_up", 1, ContentDigest.sha256("f" * 64)),
    )

    assert _rejections(_compile(definition)) == [CompileRejection.REFERENCE_NOT_FOUND]


def test_a_stale_contract_digest_is_rejected() -> None:
    """D04 §12 #1: 版だけでなく内容ハッシュも照合する。"""
    original = strategy_a().component("breakout_level")
    assert original is not None
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        contract_ref=ContractRef(
            original.contract_ref.component_id,
            original.contract_ref.version,
            ContentDigest.sha256("0" * 64),
        ),
    )

    result = _compile(definition)
    assert _rejections(result) == [CompileRejection.REFERENCE_NOT_FOUND]
    assert isinstance(result, CompileFailed)
    assert "digest does not match" in result.errors[0].message


def test_a_missing_upstream_instance_is_rejected() -> None:
    """D04 §12 #1: 接続元の使用箇所が無ければ拒否する。"""
    definition = _swap_component(
        strategy_a(),
        "entry_trigger",
        inputs={
            "price": InputBinding(sources=(MarketDataRef(SIGNAL_SERIES, MarketDataField.CLOSE),)),
            "level": InputBinding(sources=(OutputRef("nowhere", "level"),)),
        },
    )

    assert CompileRejection.REFERENCE_NOT_FOUND in _rejections(_compile(definition))


# --- #2 型と接続 -------------------------------------------------------------


def test_connecting_volume_to_a_price_input_is_rejected() -> None:
    """D04 §5: 出来高を価格として扱う接続を型で止める。"""
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        inputs={
            "prices": InputBinding(sources=(MarketDataRef(SIGNAL_SERIES, MarketDataField.VOLUME),))
        },
    )

    assert CompileRejection.TYPE_MISMATCH in _rejections(_compile(definition))


def test_two_symbols_in_one_component_are_rejected() -> None:
    """D04 §5: 初版は単一銘柄。使用箇所に2つの銘柄が届いたら拒否する。"""
    other = SeriesId(symbol=Symbol("EURUSD"), timeframe=TimeframeRef("1h", 1), basis=PriceBasis.BID)
    definition = _swap_component(
        strategy_a(),
        "entry_trigger",
        inputs={
            "price": InputBinding(sources=(MarketDataRef(other, MarketDataField.CLOSE),)),
            "level": InputBinding(sources=(OutputRef("breakout_level", "level"),)),
        },
    )

    result = _compile(definition)
    assert CompileRejection.TYPE_MISMATCH in _rejections(result)
    assert isinstance(result, CompileFailed)
    assert any("single symbol" in error.message for error in result.errors)


def test_a_missing_binding_is_rejected() -> None:
    """D04 §4.1: 接続元がないことと、実行時に欠損していることは別に扱う。"""
    definition = _swap_component(strategy_a(), "breakout_level", inputs={})

    assert CompileRejection.TYPE_MISMATCH in _rejections(_compile(definition))


# --- #3 パラメータ -----------------------------------------------------------


def test_a_parameter_outside_its_bounds_is_rejected() -> None:
    """D04 §12 #3: 範囲外のパラメータは拒否する（`lookback` は 2〜500）。"""
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        parameters={"lookback": IntValue(1), "mode": StrValue("MAX")},
    )

    assert _rejections(_compile(definition)) == [CompileRejection.PARAMETER_INVALID]


def test_a_parameter_outside_its_allowed_values_is_rejected() -> None:
    """D04 §12 #3: 列挙外の値は拒否する。"""
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        parameters={"lookback": IntValue(20), "mode": StrValue("MEDIAN")},
    )

    assert _rejections(_compile(definition)) == [CompileRejection.PARAMETER_INVALID]


def test_an_unknown_parameter_name_is_rejected() -> None:
    """契約に無いパラメータを書いても黙って無視しない。"""
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        parameters={"lookback": IntValue(20), "mode": StrValue("MAX"), "extra": IntValue(1)},
    )

    assert CompileRejection.PARAMETER_INVALID in _rejections(_compile(definition))


# --- #4 評価スケジュール -----------------------------------------------------


def test_a_trigger_outside_the_allowed_set_is_rejected() -> None:
    """D04 §12 #4: 契約が許さない起動条件は書けない。"""
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        evaluation=EvaluationSchedule(
            triggers=(OnRuntimeEvent("filled", RuntimeEventKind.POSITION_OPENED),)
        ),
    )

    assert CompileRejection.SCHEDULE_NOT_ALLOWED in _rejections(_compile(definition))


# --- #5 役割フィールド -------------------------------------------------------


def test_a_role_pointing_at_the_wrong_output_type_is_rejected() -> None:
    """D04 §12 #5: 注文意図の役割に取引機会の出力をつなげない。"""
    definition = _replace(strategy_a(), order=OutputRef("entry_trigger", "opportunity"))

    assert CompileRejection.ROLE_MISMATCH in _rejections(_compile(definition))


def test_a_confirmation_policy_without_a_filter_is_rejected() -> None:
    """D04 §12 #5: 後続確認の有無と発注方針が食い違ってはならない。"""
    definition = _replace(
        strategy_a(), entry_policy=AwaitConfirmation(deadline=BarsDeadline(bars=3))
    )

    assert CompileRejection.ROLE_MISMATCH in _rejections(_compile(definition))


# --- #6 依存グラフ -----------------------------------------------------------


def test_the_causal_edges_put_the_exit_downstream_of_the_order() -> None:
    """D04 §12: 約定による起動と建玉の参照は、注文意図から引いた因果辺で表す。"""
    definition = strategy_a()

    graph = build_dependency_graph(definition, order_role=definition.order)

    causal = {
        (edge.source_instance, edge.target_instance, edge.kind)
        for edge in graph.edges
        if edge.kind is not EdgeKind.EXPLICIT_INPUT
    }
    assert causal == {
        ("entry_order", "take_profit", EdgeKind.FILL_TRIGGER),
        ("entry_order", "take_profit", EdgeKind.POSITION_CONTEXT),
    }


def test_two_components_that_read_each_other_are_a_cycle() -> None:
    """D04 §12 #6: 明示接続の閉路を通さない（評価順が決まらない）。"""
    definition = strategy_a()
    definition = _swap_component(
        definition,
        "breakout_level",
        inputs={"prices": InputBinding(sources=(OutputRef("stop_level", "level"),))},
    )
    definition = _swap_component(
        definition,
        "stop_level",
        inputs={"prices": InputBinding(sources=(OutputRef("breakout_level", "level"),))},
    )

    assert CompileRejection.DEPENDENCY_CYCLE in _rejections(_compile(definition))


def test_a_self_loop_through_the_causal_edges_is_found() -> None:
    """D04 §12: 注文が約定して自分を起動する帰還路は自己ループになる。

    この構成は段階2 の部品カタログでは評価スケジュールの検査に先に掛かるため、依存グラフ
    そのものを組み立てて確かめる。因果辺を辺に含めないと、この閉路は見つからない。
    """
    definition = _swap_component(
        strategy_a(),
        "entry_order",
        evaluation=EvaluationSchedule(
            triggers=(OnRuntimeEvent("filled", RuntimeEventKind.POSITION_OPENED),)
        ),
    )

    graph = build_dependency_graph(definition, order_role=definition.order)
    cycle = graph.find_cycle()

    assert cycle is not None
    assert any(edge.kind is EdgeKind.FILL_TRIGGER for edge in cycle)


# --- #6b 出力仕様の付随条件 --------------------------------------------------


def test_an_opportunity_component_cannot_start_only_from_runtime_events() -> None:
    """D05 §6.2: 対象区間を決められない取引機会は作れない。"""
    definition = _swap_component(
        strategy_a(),
        "entry_trigger",
        evaluation=EvaluationSchedule(
            triggers=(OnRuntimeEvent("filled", RuntimeEventKind.POSITION_OPENED),)
        ),
    )

    assert CompileRejection.OUTPUT_SPEC_INVALID in _rejections(_compile(definition))


# --- #7 能力検査 -------------------------------------------------------------


def test_a_timeframe_finer_than_fifteen_minutes_is_rejected() -> None:
    """D04 §12: 段階2は 15 分より細かい足を扱わない。"""
    fine = SeriesId(
        symbol=SIGNAL_SERIES.symbol, timeframe=TimeframeRef("1m", 1), basis=PriceBasis.BID
    )
    timeframes = dict(TIMEFRAMES)
    timeframes[fine.timeframe] = TimeframeDefinition(
        ref=fine.timeframe,
        nominal_length=timedelta(minutes=1),
        alignment=FixedUtcAlignment(step=timedelta(minutes=1)),
    )
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        inputs={"prices": InputBinding(sources=(MarketDataRef(fine, MarketDataField.HIGH),))},
        evaluation=EvaluationSchedule(triggers=(OnBarClose("h1", fine),)),
    )

    result = compile_strategy(definition, INITIAL_CATALOG, timeframes)

    assert CompileRejection.UNSUPPORTED_CONFIGURATION in _rejections(result)


def test_an_unknown_timeframe_cannot_be_waved_through() -> None:
    """時間足の定義が無ければ細かさを判定できないので、黙って通さない。"""
    result = compile_strategy(strategy_a(), INITIAL_CATALOG, {})

    assert CompileRejection.UNSUPPORTED_CONFIGURATION in _rejections(result)


# --- 誤りの伝え方 ------------------------------------------------------------


def test_every_error_names_the_check_and_the_declaration() -> None:
    """D05 §5.2: どの検査でどの宣言のどこが原因かを必ず持つ。"""
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        parameters={"lookback": IntValue(1), "mode": StrValue("MAX")},
    )

    result = _compile(definition)
    assert isinstance(result, CompileFailed)
    error = result.errors[0]
    assert error.check_id == "#3"
    assert error.location.instance_id == "breakout_level"
    assert error.location.field_path == "parameters.lookback"
    assert str(error).startswith("[#3 PARAMETER_INVALID] breakout_level.parameters.lookback:")


def test_errors_in_the_same_stage_are_reported_together() -> None:
    """D05 §5.1: 同じ段の誤りはまとめて返す（1件ずつ直す往復を減らす）。"""
    definition = _swap_component(
        strategy_a(),
        "breakout_level",
        parameters={"lookback": IntValue(1), "mode": StrValue("MEDIAN")},
    )

    result = _compile(definition)
    assert isinstance(result, CompileFailed)
    assert len(result.errors) == 2


def test_a_failed_compilation_never_carries_an_empty_error_list() -> None:
    """失敗と分かるのに理由が無い、という結果を作らせない。"""
    with pytest.raises(Exception, match="must not be empty"):
        CompileFailed(errors=())


# --- Codex レビュー1巡目（改善提案）で足した検査 ----------------------------


def test_an_opportunity_component_cannot_mix_in_a_runtime_event_trigger() -> None:
    """D05 §6.2: 実行時イベントによる起動は対象区間を持たないので、混ぜた宣言も拒否する。

    混在を通すと、足の確定では機会が作れるのに約定通知ではデータ誤りになる、という実行時に
    しか分からない振る舞いが残る。
    """
    definition = _swap_component(
        strategy_a(),
        "entry_trigger",
        evaluation=EvaluationSchedule(
            triggers=(
                OnBarClose("h1", SIGNAL_SERIES),
                OnRuntimeEvent("filled", RuntimeEventKind.POSITION_OPENED),
            )
        ),
    )

    assert CompileRejection.OUTPUT_SPEC_INVALID in _rejections(_compile(definition))


def test_a_validity_binding_must_point_at_a_value_output() -> None:
    """D05 §6.5: ランタイムが読み直せるのは繰り返し参照する値の最新出力だけ。

    条件の成否を配送イベントとして出す出力を束縛に書くと、宣言は通るのに条件がいちども
    効かない。
    """
    definition = _replace(
        strategy_a(),
        opportunity_validity=OpportunityValiditySpec(
            bindings=(
                ValidityBinding(
                    source=OutputRef("entry_trigger", "opportunity"),
                    mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST,
                ),
            )
        ),
    )

    assert CompileRejection.ROLE_MISMATCH in _rejections(_compile(definition))


def test_treating_a_missing_recheck_as_a_failure_is_not_supported_yet() -> None:
    """D05 §6.4・§7.3: 再検査は評価の外側で走るので、失敗を残す評価記録が無い。"""
    definition = _replace(
        strategy_a(),
        opportunity_validity=OpportunityValiditySpec(
            bindings=(
                ValidityBinding(
                    source=OutputRef("breakout_level", "level"),
                    mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST,
                    on_missing=MissingPolicyError(),
                ),
            )
        ),
    )

    assert CompileRejection.UNSUPPORTED_CONFIGURATION in _rejections(_compile(definition))


def test_a_malformed_initial_state_is_rejected_for_any_stateful_component() -> None:
    """D04 §9.1: 初期値は宣言に書き切るので、書き間違いは run が始まる前に分かる。

    再武装の宣言に限らず、状態を宣言するすべての契約に適用する。ここで見なければ、
    ランタイムの組み立て時に状態を作ろうとした時点で落ちる。
    """
    broken = ComponentContract(
        component_id=breakout_contract.component_id,
        version=breakout_contract.version,
        implementation_ref=breakout_contract.implementation_ref,
        inputs=dict(breakout_contract.inputs),
        outputs=dict(breakout_contract.outputs),
        parameters=dict(breakout_contract.parameters),
        evaluation_spec=breakout_contract.evaluation_spec,
        state_spec=StateSpec(
            state_type=CONDITION_STATE_V1,
            initial=LiteralInitialState({"armed": BoolValue(False)}),
        ),
        temporal_constraints=breakout_contract.temporal_constraints,
    )
    registry = build_registry(
        (
            extreme.REGISTRATION,
            ComponentRegistration(
                contract=broken,
                implementation=StatefulImplementation(
                    evaluate=breakout.evaluate, state_type=CONDITION_STATE_V1
                ),
                implementation_ref=broken.implementation_ref,
            ),
            market.REGISTRATION,
            level_stop.REGISTRATION,
            fixed_rr.REGISTRATION,
        )
    )
    definition = _swap_component(
        strategy_a(), "entry_trigger", contract_ref=contract_ref_for(broken)
    )

    result = compile_strategy(definition, registry, TIMEFRAMES)

    assert CompileRejection.OUTPUT_SPEC_INVALID in _rejections(result)
