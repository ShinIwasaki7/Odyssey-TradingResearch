"""宣言型の不変条件・正規化・内容ハッシュ（D04 §3・§13）。

宣言は「戦略の形」を表す不変データである。ここで確かめるのは3つだけ。

1. **構築時に弾くもの**: 字種・範囲・区分の違反（D04 §3）。参照解決と型整合はコンパイラの
   仕事なので、ここでは見ない。
2. **正規化**: 順序に意味を持たせないコレクションが安定な鍵で並び、重複を拒否すること
   （D04 §3 の表）。意味の同じ2つの宣言が「書いた順序」の違いだけで別の内容ハッシュに
   なると、実験の同一性が分かれてしまう。
3. **内容ハッシュ**: 契約の指紋から実装参照が外れていること（D04 §13.2、Q2 決定）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import ContentDigest, ImplementationRef
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.strategy.catalog.features import extreme
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import (
    CONDITION_STATE_V1,
    DATA_TYPE_REGISTRY,
    MARKET_DATA_FIELD_TYPES,
    PRICE_V1,
    RUNTIME_TARGET_TYPES,
    VOLUME_V1,
    DataTypeRef,
    require_registered,
)
from odyssey_fx.strategy.declarations.digest import contract_digest, strategy_digest
from odyssey_fx.strategy.declarations.duration import format_duration, parse_duration
from odyssey_fx.strategy.declarations.evaluation import (
    AllowedBarClose,
    AllowedInputEvent,
    EvaluationSchedule,
    EvaluationSpec,
    OnBarClose,
)
from odyssey_fx.strategy.declarations.missing import Error, SkipEvaluation
from odyssey_fx.strategy.declarations.opportunity import (
    OnNewTrigger,
    OnOrderAccepted,
    OpportunityConcurrencySpec,
    OpportunityValiditySpec,
    ValidityBinding,
    ValidityMode,
)
from odyssey_fx.strategy.declarations.read_spec import (
    BarsWindow,
    HistoryWindow,
    LatestAvailable,
    ParameterRef,
)
from odyssey_fx.strategy.declarations.refs import (
    MarketDataField,
    OutputRef,
    RuntimeInputRef,
    RuntimeTarget,
)
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    FloatValue,
    InputArity,
    InputBinding,
    IntValue,
    NumericBounds,
    OutputSpec,
    ParameterSpec,
    ParameterType,
    PortKind,
    StrValue,
)
from odyssey_fx.strategy.declarations.temporal import TemporalConstraints
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES, strategy_a

# --- 字種と範囲 --------------------------------------------------------------


@pytest.mark.parametrize("bad", ["Breakout", "entry-trigger", "", "entry trigger", "entry\n"])
def test_identifiers_must_be_lowercase_words(bad: str) -> None:
    """D04 §3: 識別子は `^[a-z0-9_]+$`。末尾の改行も拒否する。"""
    with pytest.raises(KernelValueError, match=r"\^\[a-z0-9_\]\+\$"):
        OutputRef(bad, "level")


def test_a_version_must_be_at_least_one() -> None:
    """D04 §3: 版は 1 以上の整数。"""
    with pytest.raises(KernelValueError, match=">= 1"):
        DataTypeRef("price", 0)


def test_an_arity_upper_bound_must_not_be_below_the_lower_bound() -> None:
    """D04 §4.1: 上限を下限より小さくできない。"""
    with pytest.raises(KernelValueError, match="must be >= min_count"):
        InputArity(min_count=2, max_count=1)


def test_a_history_window_must_not_exclude_a_negative_number_of_bars() -> None:
    """D04 §6.2: 「当該足を除く」本数は 0 以上。"""
    with pytest.raises(KernelValueError, match="must be >= 0"):
        HistoryWindow(window=BarsWindow(count=5), exclude_latest_bars=-1)


def test_a_max_age_of_zero_is_rejected() -> None:
    """D04 §6.1: 鮮度上限が 0 だと「どの足も古すぎる」ことになり、宣言として意味がない。"""
    with pytest.raises(KernelValueError, match="must be positive"):
        LatestAvailable(max_age=timedelta(0))


def test_the_concurrency_limit_must_be_at_least_one() -> None:
    """D04 §10.3: 同時に保持できる機会の上限は 1 以上。"""
    with pytest.raises(KernelValueError, match="must be >= 1"):
        OpportunityConcurrencySpec(
            max_active=0,
            on_new_trigger=OnNewTrigger.KEEP_EXISTING,
            on_order_accepted=OnOrderAccepted.KEEP_OTHERS,
        )


def test_a_parameter_value_must_match_its_declared_type() -> None:
    """D04 §7: 許可値の型は宣言した型と一致していなければならない。"""
    with pytest.raises(KernelValueError, match="must be a INT value"):
        ParameterSpec(value_type=ParameterType.INT, allowed_values=(StrValue("MAX"),))


def test_a_float_parameter_must_be_finite() -> None:
    """D04 §7: 浮動小数は有限値のみ。"""
    with pytest.raises(KernelValueError, match="must be finite"):
        FloatValue(float("inf"))


def test_bounds_must_constrain_at_least_one_end() -> None:
    """両端とも `None` の範囲は何も制約しない。"""
    with pytest.raises(KernelValueError, match="at least one end"):
        NumericBounds()


def test_an_open_upper_bound_excludes_the_endpoint() -> None:
    """D05 §4.3(5) の `(0, 100]` のような開閉の指定が効く。"""
    bounds = NumericBounds(minimum=0, maximum=100, minimum_inclusive=False)

    assert not bounds.contains(0)
    assert bounds.contains(0.5)
    assert bounds.contains(100)


# --- 正規化と重複の拒否 ------------------------------------------------------


def test_trigger_names_must_be_unique_within_a_schedule() -> None:
    """D04 §8: 起動条件名が重複すると、起動条件ごとの必須入力を宣言できない。"""
    with pytest.raises(KernelValueError, match="must not contain duplicates"):
        EvaluationSchedule(
            triggers=(OnBarClose("h1", SIGNAL_SERIES), OnBarClose("h1", SIGNAL_SERIES))
        )


def test_validity_bindings_are_unique_per_source() -> None:
    """D04 §10.2: 同じ出力に別々の扱いを書けない。"""
    source = OutputRef("filter", "state")
    with pytest.raises(KernelValueError, match="must not contain duplicates"):
        OpportunityValiditySpec(
            bindings=(
                ValidityBinding(source=source, mode=ValidityMode.SNAPSHOT_AT_OPPORTUNITY),
                ValidityBinding(source=source, mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST),
            )
        )


def test_allowed_timeframes_are_sorted_on_construction() -> None:
    """D04 §3: 許可の集合は順序に意味を持たないので、構築時に並べ替える。"""
    first = AllowedBarClose(timeframes=(TimeframeRef("4h_ny17", 1), TimeframeRef("1h", 1)))
    second = AllowedBarClose(timeframes=(TimeframeRef("1h", 1), TimeframeRef("4h_ny17", 1)))

    assert first == second


def test_components_are_sorted_by_instance_id() -> None:
    """D04 §3: 使用箇所の並び順で実行順序を指定しない。構築時に ID 順へ正規化する。"""
    definition = strategy_a()

    assert [item.instance_id for item in definition.components] == sorted(
        item.instance_id for item in definition.components
    )


def test_writing_the_components_in_another_order_gives_the_same_digest() -> None:
    """D04 §13.2: 書いた順序の違いで実験の同一性が分かれない。"""
    definition = strategy_a()
    reordered = type(definition)(
        strategy_id=definition.strategy_id,
        version=definition.version,
        components=tuple(reversed(definition.components)),
        market_state=definition.market_state,
        trigger=definition.trigger,
        execution_filter=definition.execution_filter,
        order=definition.order,
        protection=definition.protection,
        exit=definition.exit,
        entry_policy=definition.entry_policy,
        opportunity_validity=definition.opportunity_validity,
        opportunity_concurrency=definition.opportunity_concurrency,
    )

    assert strategy_digest(definition) == strategy_digest(reordered)


# --- データ型レジストリ ------------------------------------------------------


def test_the_registry_holds_the_thirteen_initial_types() -> None:
    """D04 §5: 初版の登録は13件。"""
    assert len(DATA_TYPE_REGISTRY) == 13


def test_an_unregistered_data_type_is_rejected() -> None:
    """D04 §5: 登録制であり、任意の型識別子は書けない。"""
    with pytest.raises(KernelValueError, match="unregistered data type"):
        require_registered(DataTypeRef("made_up", 1), "test")


def test_volume_is_not_a_price() -> None:
    """D04 §5: 出来高を価格として扱う接続をコンパイル時に拒否できるよう、型を分ける。"""
    assert MARKET_DATA_FIELD_TYPES[MarketDataField.CLOSE] == PRICE_V1
    assert MARKET_DATA_FIELD_TYPES[MarketDataField.VOLUME] == VOLUME_V1
    assert PRICE_V1 != VOLUME_V1


def test_the_runtime_targets_map_to_their_context_types() -> None:
    """D04 §5: 建玉・口座を読む入力にも型が要る。"""
    assert RUNTIME_TARGET_TYPES[RuntimeTarget.POSITION].type_id == "position_context"
    assert RUNTIME_TARGET_TYPES[RuntimeTarget.ACCOUNT].type_id == "account_context"


def test_a_pending_order_target_cannot_be_declared() -> None:
    """D04 §4.3: 未約定注文の参照は列挙に含めない（宣言できない）。"""
    assert [item.value for item in RuntimeTarget] == ["POSITION", "ACCOUNT"]
    with pytest.raises(KernelValueError, match="must be a RuntimeTarget"):
        RuntimeInputRef("PENDING_ORDER")  # type: ignore[arg-type]


# --- 契約の構築時検査 --------------------------------------------------------


def _contract(**overrides: object) -> ComponentContract:
    base: dict[str, object] = {
        "component_id": "probe",
        "version": 1,
        "implementation_ref": ImplementationRef("probe", ContentDigest.sha256("c" * 64)),
        "inputs": {},
        "outputs": {
            "value": OutputSpec(data_type=PRICE_V1, kind=PortKind.VALUE),
        },
        "parameters": {},
        "evaluation_spec": EvaluationSpec(allowed=(AllowedBarClose(),)),
        "state_spec": None,
        "temporal_constraints": TemporalConstraints(),
    }
    base.update(overrides)
    return ComponentContract(**base)  # type: ignore[arg-type]


def test_a_contract_must_declare_at_least_one_output() -> None:
    """出力のない部品は宣言として意味を持たない。"""
    with pytest.raises(KernelValueError, match="outputs must not be empty"):
        _contract(outputs={})


def test_an_allowed_input_event_must_name_a_delivered_event_input() -> None:
    """D04 §8: 許可する入力イベントは、配送イベントを読む入力に限る。"""
    with pytest.raises(KernelValueError, match="not declared in"):
        _contract(evaluation_spec=EvaluationSpec(allowed=(AllowedInputEvent(("missing",)),)))


def test_a_fixed_schedule_must_pin_exactly_one_timeframe() -> None:
    """D04 §8: 評価条件を固定する契約は、制約まで一意に定まっていなければならない。"""
    with pytest.raises(KernelValueError, match="exactly one timeframe"):
        EvaluationSpec(allowed=(AllowedBarClose(timeframes=None),), fixed=True)


# --- 内容ハッシュ ------------------------------------------------------------


def test_the_contract_digest_ignores_the_implementation_reference() -> None:
    """D04 §13.2（Q2 決定）: 実装を直しても人間が管理する戦略の版は変わらない。"""
    original = extreme.CONTRACT
    relabelled = ComponentContract(
        component_id=original.component_id,
        version=original.version,
        implementation_ref=ImplementationRef("other", ContentDigest.sha256("d" * 64)),
        inputs=dict(original.inputs),
        outputs=dict(original.outputs),
        parameters=dict(original.parameters),
        evaluation_spec=original.evaluation_spec,
        state_spec=original.state_spec,
        temporal_constraints=original.temporal_constraints,
    )

    assert contract_digest(original) == contract_digest(relabelled)


def test_the_schema_version_is_part_of_the_digest() -> None:
    """D04 §3: 保存形式の解釈規則を変えた事実が再現性の差として現れる。"""
    original = extreme.CONTRACT
    bumped = ComponentContract(
        component_id=original.component_id,
        version=original.version,
        implementation_ref=original.implementation_ref,
        inputs=dict(original.inputs),
        outputs=dict(original.outputs),
        parameters=dict(original.parameters),
        evaluation_spec=original.evaluation_spec,
        state_spec=original.state_spec,
        temporal_constraints=original.temporal_constraints,
        schema_version=2,
    )

    assert contract_digest(original) != contract_digest(bumped)


def test_a_different_parameter_gives_a_different_strategy_digest() -> None:
    """上位設計書 §4.3.5: パラメータ割当が違えば別の実行として扱う。"""
    assert strategy_digest(strategy_a()) != strategy_digest(strategy_a(reward_risk=3.0))


# --- 期間値の文字列表現 ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("90m", timedelta(minutes=90)),
        ("2h", timedelta(hours=2)),
        ("1d", timedelta(days=1)),
    ],
)
def test_a_duration_is_one_number_and_one_unit(text: str, expected: timedelta) -> None:
    """D04 §13.1: 受理するのは `<正の整数><単位>` の1形式だけ。"""
    assert parse_duration(text) == expected


@pytest.mark.parametrize("bad", ["2", "PT2H", "1h30m", "0h", "-2h", "2H", "2 h", "2h\n"])
def test_other_duration_spellings_are_rejected(bad: str) -> None:
    """複数の書き方を受理すると、同じ意味の設定が別の文字列になる。"""
    with pytest.raises(KernelValueError):
        parse_duration(bad)


def test_a_duration_round_trips_through_the_largest_whole_unit() -> None:
    """書き戻しは単位1つで表せる最大の単位を選ぶ。"""
    assert format_duration(timedelta(hours=2)) == "2h"
    assert format_duration(timedelta(minutes=90)) == "90m"
    assert parse_duration(format_duration(timedelta(days=3))) == timedelta(days=3)


# --- 段階2で宣言できない欠損方針 --------------------------------------------


def test_only_two_missing_input_policies_exist_in_stage_2() -> None:
    """D04 §6.3（Q8 決定）: 待機と遡りは型に無いので宣言できない。"""
    assert SkipEvaluation().kind == "SKIP_EVALUATION"
    assert Error().kind == "ERROR"
    with pytest.raises(KernelValueError, match="SkipEvaluation"):
        LatestAvailable(on_missing="WAIT_FOR_INPUT")  # type: ignore[arg-type]


def test_a_parameter_reference_can_stand_in_for_a_window_size() -> None:
    """D04 §6.2: 窓の本数はパラメータで選べる（解決はコンパイル時）。"""
    window = BarsWindow(count=ParameterRef("lookback"))

    assert isinstance(window.count, ParameterRef)
    assert str(window.count) == "$lookback"


def test_an_input_binding_keeps_the_declared_order_of_its_sources() -> None:
    """D04 §3: 可変個数入力は型付き参照の列であり、並びを保つ。"""
    sources = (OutputRef("b", "out"), OutputRef("a", "out"))

    assert InputBinding(sources=sources).sources == sources


def test_the_condition_state_type_is_what_edge_triggers_remember() -> None:
    """D04 §10.4: 再武装は条件の成否を保持する状態で表す。"""
    assert CONDITION_STATE_V1.type_id == "condition_state"
    assert BoolValue(False).value is False
    assert IntValue(3).value == 3
