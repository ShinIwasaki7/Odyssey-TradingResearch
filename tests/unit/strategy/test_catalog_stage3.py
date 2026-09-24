"""段階3 の部品の計算規則と、登録・宣言・内容型の検査（D05 §4.1・§4.2・§4.5〜§4.10・§9.2）。

部品は純粋関数である。解決済みの入力を直接組み立てて、計算規則そのものを確かめる。

| 部品 | 規則 |
|---|---|
| 指数移動平均 | 古い側の period 本の単純平均を種にし、残りを古い順に平滑化する |
| 真の値幅の平均 | 2本目から真の値幅を数え、Wilder の平滑を当てる |
| 価格比較 | 等値は GE / LE が含み、GT / LT が含まない |
| 論理積・論理和 | 接続された全要素の積・和 |
| 遷移検出 | 直前と今回の成否の組でだけ真を返し、状態は毎回更新する |
| 条件から取引許可 | 2つの条件をそのまま許可の2項目にする |
| 固定値の条件 | 宣言した値を返す |
| 条件による後続確認 | 成否だけを返し、未成立も出力として出す |
| 追従する損切り | 有利な向きへ動くときだけ更新を返す |
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import OpportunityId, OutputId, PositionId
from odyssey_fx.common.money import Price, PriceOffset, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.strategy.catalog.conditions import compare, logic, transition
from odyssey_fx.strategy.catalog.exits import trailing_stop
from odyssey_fx.strategy.catalog.features import atr, ema
from odyssey_fx.strategy.catalog.filters import condition_filter
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.orders import market
from odyssey_fx.strategy.catalog.permissions import from_condition
from odyssey_fx.strategy.catalog.protection import level_stop
from odyssey_fx.strategy.catalog.registry import (
    ComponentRegistration,
    ParameterConstraint,
    StatelessImplementation,
)
from odyssey_fx.strategy.catalog.triggers import breakout
from odyssey_fx.strategy.compiler.compiled import ResolvedMarketSource, ResolvedParameter
from odyssey_fx.strategy.declarations.datatypes import (
    CONFIRMATION_RESULT_V1,
    MANAGEMENT_ACTION_V1,
)
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline
from odyssey_fx.strategy.declarations.missing import (
    OnSuperseded,
    SkipEvaluation,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.read_spec import (
    CurrentContext,
    DeliveredEvent,
    HistoryWindow,
    LatestAvailable,
)
from odyssey_fx.strategy.declarations.refs import MarketDataField
from odyssey_fx.strategy.declarations.specs import BoolValue, IntValue, PortKind, StrValue
from odyssey_fx.strategy.records.payloads import (
    ClosePosition,
    ConditionState,
    ConfirmationOutcome,
    ConfirmationResult,
    MarketPermission,
    Opportunity,
    OrderIntent,
    OrderType,
    ProtectionLevels,
    SetTakeProfit,
    TradeDirection,
    UpdateStop,
    payload_type_for,
)
from odyssey_fx.strategy.runtime.requests import (
    ContextSnapshot,
    EventDelivery,
    ResolvedInputs,
    ValueSample,
    ValueWindow,
)
from tests.fixtures.strategy.fakes import FakePositionContext
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES

MOMENT = UtcTime.from_components(2015, 1, 7, 9, 0)
EARLIER = UtcTime.from_components(2015, 1, 7, 8, 0)
SOURCE = ResolvedMarketSource(SIGNAL_SERIES, MarketDataField.CLOSE)

#: 検証戦略 B の待機（D05 §9.2）。
WAIT_ONE_BAR = WaitForInput(
    deadline=BarsDeadline(bars=1),
    on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
    on_superseded=OnSuperseded.EXPIRE_REQUEST,
)


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _sample(payload: object) -> ValueSample:
    return ValueSample(payload=payload, source=SOURCE, freshness_time=MOMENT)


def _window(*texts: str) -> ValueWindow:
    return ValueWindow(samples=tuple(_sample(_price(text)) for text in texts))


def _parameters(**values: object) -> dict[str, ResolvedParameter]:
    return {
        name: ResolvedParameter(name=name, value=value, unit=None, decimal_value=None)  # type: ignore[arg-type]
        for name, value in values.items()
    }


def _condition(satisfied: bool) -> ValueSample:
    return _sample(ConditionState(satisfied))


def _opportunity() -> Opportunity:
    return Opportunity(
        opportunity_id=OpportunityId(1),
        symbol=SIGNAL_SERIES.symbol,
        direction=TradeDirection.LONG,
        signal_interval=Interval(start=EARLIER, end=MOMENT),
        reference_values={},
    )


# --- (6) 指数移動平均 --------------------------------------------------------


def _ema(values: tuple[str, ...], period: int) -> Price:
    result = ema.evaluate(
        ResolvedInputs(by_name={"prices": (_window(*values),)}),
        _parameters(period=IntValue(period), window_bars=IntValue(len(values))),
    )
    assert result.new_state is None
    value = result.outputs["value"]
    assert isinstance(value, Price)
    return value


def test_the_ema_seeds_with_the_simple_average_of_the_oldest_period() -> None:
    """D05 §4.5(6): 種は古い側 period 本の単純平均。残り1本を alpha = 2/3 で平滑化する。"""
    # 種 = (1 + 3) / 2 = 2。(2 * 5 + 1 * 2) / 3 = 4、(2 * 7 + 1 * 4) / 3 = 6。
    assert _ema(("1", "3", "5", "7"), period=2) == _price("6")


def test_the_ema_reproduces_the_paper_trace_daily_value() -> None:
    """T02 §2.4: 古い59本が 149.000、末尾が 149.210 → 149.020（丸めが1度も働かない）。"""
    values = ("149.000",) * 59 + ("149.210",)

    result = _ema(values, period=20)

    assert result == _price("149.020")
    exponent = result.value.as_tuple().exponent
    assert isinstance(exponent, int)
    assert exponent >= -3


def test_the_ema_rejects_a_window_of_the_wrong_length() -> None:
    """宣言した本数と実際に読んだ履歴が食い違ったら、黙って計算しない。"""
    with pytest.raises(KernelValueError, match="expected 6 bars"):
        ema.evaluate(
            ResolvedInputs(by_name={"prices": (_window("1", "2", "3", "4"),)}),
            _parameters(period=IntValue(2), window_bars=IntValue(6)),
        )


def test_the_ema_does_not_depend_on_the_process_decimal_context() -> None:
    """D05 §4.5: 局所的な文脈で計算し、プロセスの既定文脈に依存しない。"""
    import decimal

    values = ("1", "2", "3", "4", "5", "6")
    expected = _ema(values, period=3)
    with decimal.localcontext() as context:
        context.prec = 3
        context.rounding = decimal.ROUND_DOWN
        assert _ema(values, period=3) == expected


def test_the_parameter_relation_requires_twice_the_period() -> None:
    """D05 §4.5（Q22 決定）: `window_bars >= 2 * period` を登録の純粋関数が確かめる。"""
    check = ema.PARAMETER_CONSTRAINT.check

    assert check(_parameters(period=IntValue(20), window_bars=IntValue(40)))
    assert check(_parameters(period=IntValue(20), window_bars=IntValue(60)))
    assert not check(_parameters(period=IntValue(20), window_bars=IntValue(39)))


def test_the_parameter_relation_fails_loudly_on_a_non_integer() -> None:
    """D05 §4.1: 関数が例外で終わった場合もコンパイラは拒否する。黙って通さない。"""
    with pytest.raises(KernelValueError, match="must be integers"):
        ema.PARAMETER_CONSTRAINT.check(_parameters(period=StrValue("20"), window_bars=IntValue(60)))


def test_ema_and_atr_register_the_same_parameter_relation() -> None:
    """D05 §4.5: 同じ関係を `atr` にも使う。"""
    assert ema.REGISTRATION.parameter_constraint is ema.PARAMETER_CONSTRAINT
    assert ema.REGISTRATION_V2.parameter_constraint is ema.PARAMETER_CONSTRAINT
    assert atr.REGISTRATION.parameter_constraint is ema.PARAMETER_CONSTRAINT
    assert ema.PARAMETER_CONSTRAINT.reads == ("window_bars", "period")


# --- (7) 真の値幅の平均 ------------------------------------------------------


def test_the_atr_applies_wilder_smoothing_from_the_second_bar() -> None:
    """D05 §4.5(7): 先頭の足は前足の終値が無いので数えない。"""
    highs = ("10", "12", "13", "15")
    lows = ("9", "10", "11", "11")
    closes = ("9.5", "11", "12", "14")
    # 真の値幅: 2本目 max(2, 2.5, 0.5) = 2.5、3本目 max(2, 2, 0) = 2、4本目 max(4, 3, 1) = 4。
    # 種 = (2.5 + 2) / 2 = 2.25。Wilder: (2.25 * 1 + 4) / 2 = 3.125。
    result = atr.evaluate(
        ResolvedInputs(
            by_name={
                "highs": (_window(*highs),),
                "lows": (_window(*lows),),
                "closes": (_window(*closes),),
            }
        ),
        _parameters(period=IntValue(2), window_bars=IntValue(4)),
    )

    assert result.outputs == {"value": PriceOffset(Decimal("3.125"))}


def test_the_atr_declares_the_observation_alignment() -> None:
    """D05 §4.5: 観測区間の一致を宣言する唯一の段階3 部品。"""
    (requirement,) = atr.CONTRACT.temporal_constraints.alignment
    assert requirement.input_names == ("closes", "highs", "lows")


# --- (8) 価格比較 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("operator", "left", "right", "expected"),
    [
        ("GT", "149.210", "149.020", True),
        ("GT", "149.000", "149.000", False),
        ("GE", "149.000", "149.000", True),
        ("LT", "149.000", "149.000", False),
        ("LE", "149.000", "149.000", True),
        ("LT", "148.810", "149.000", True),
    ],
)
def test_the_price_compare_includes_equality_only_for_ge_and_le(
    operator: str, left: str, right: str, expected: bool
) -> None:
    """D05 §4.6(8): 等値は GE / LE が含み、GT / LT が含まない。"""
    result = compare.evaluate(
        ResolvedInputs(
            by_name={"left": (_sample(_price(left)),), "right": (_sample(_price(right)),)}
        ),
        _parameters(operator=StrValue(operator)),
    )

    assert result.outputs == {"condition": ConditionState(expected)}


# --- (9)(10) 論理積・論理和 ----------------------------------------------------


def _conditions(*values: bool) -> ResolvedInputs:
    return ResolvedInputs(by_name={"conditions": tuple(_condition(value) for value in values)})


def test_all_conditions_is_the_conjunction() -> None:
    """D05 §4.6(9)。"""
    assert logic.evaluate_all(_conditions(True, True, True), {}).outputs == {
        "condition": ConditionState(True)
    }
    assert logic.evaluate_all(_conditions(True, False, True), {}).outputs == {
        "condition": ConditionState(False)
    }


def test_any_condition_is_the_disjunction() -> None:
    """D05 §4.6(10)。"""
    assert logic.evaluate_any(_conditions(False, False), {}).outputs == {
        "condition": ConditionState(False)
    }
    assert logic.evaluate_any(_conditions(False, True), {}).outputs == {
        "condition": ConditionState(True)
    }


def test_the_logic_components_take_two_or_more_inputs() -> None:
    """D05 §4.6: `arity=(2, None)` の可変個数入力。"""
    for contract in (logic.ALL_CONTRACT, logic.ANY_CONTRACT):
        arity = contract.inputs["conditions"].arity
        assert (arity.min_count, arity.max_count) == (2, None)


# --- (11) 遷移検出 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("edge", "before", "now", "expected"),
    [
        ("RISING", False, True, True),
        ("RISING", True, True, False),
        ("RISING", True, False, False),
        ("FALLING", True, False, True),
        ("FALLING", False, False, False),
        ("FALLING", False, True, False),
    ],
)
def test_the_transition_fires_only_on_the_declared_edge(
    edge: str, before: bool, now: bool, expected: bool
) -> None:
    """D05 §4.6(11): 新しい状態は今回の入力の条件（遷移したかどうかに依らない）。"""
    result = transition.evaluate(
        ResolvedInputs(by_name={"condition": (_condition(now),)}),
        _parameters(edge=StrValue(edge)),
        ConditionState(before),
    )

    assert result.outputs == {"condition": ConditionState(expected)}
    assert result.new_state == ConditionState(now)


def test_the_transition_has_no_retrigger_mode() -> None:
    """D05 §4.6: 取引機会を出さないので再武装の設定を持たない（D04 §12 #6b）。"""
    assert transition.CONTRACT.outputs["condition"].retrigger_mode is None


# --- (12)(13) 市場状態 ---------------------------------------------------------


def test_the_permission_maps_the_two_conditions() -> None:
    """D05 §4.7(12): 検証戦略 B は買い許可だけを条件から作り、売りは固定値の偽。"""
    result = from_condition.evaluate(
        ResolvedInputs(
            by_name={"long_allowed": (_condition(True),), "short_allowed": (_condition(False),)}
        ),
        {},
    )

    assert result.outputs == {"permission": MarketPermission(allow_long=True, allow_short=False)}


def test_the_constant_condition_returns_the_declared_value() -> None:
    """D05 §4.7(13): 入力を持たない。"""
    assert dict(from_condition.CONSTANT_CONTRACT.inputs) == {}
    result = from_condition.evaluate_constant(
        ResolvedInputs(by_name={}), _parameters(value=BoolValue(False))
    )

    assert result.outputs == {"condition": ConditionState(False)}


# --- (14) 条件による後続確認 ----------------------------------------------------


def _filter_inputs(satisfied: bool, opportunity: object | None = None) -> ResolvedInputs:
    snapshot = ContextSnapshot(
        payload=_opportunity() if opportunity is None else opportunity, read_at=MOMENT
    )
    return ResolvedInputs(
        by_name={"condition": (_condition(satisfied),), "opportunity": (snapshot,)}
    )


def test_the_filter_returns_only_the_outcome() -> None:
    """D05 §4.8: 機会の識別子と確認足の区間はランタイムが付ける。"""
    result = condition_filter.evaluate(
        _filter_inputs(True), _parameters(include_start_bar=BoolValue(True))
    )

    assert result.outputs == {
        "confirmation": ConfirmationOutcome(confirmed=True, reference_values={})
    }


def test_the_filter_emits_an_unconfirmed_outcome_too() -> None:
    """D05 §4.8: 未成立を出力として残さないと、入力不足・期限切れ・追い越しと区別できない。"""
    result = condition_filter.evaluate(
        _filter_inputs(False), _parameters(include_start_bar=BoolValue(True))
    )

    assert result.outputs == {
        "confirmation": ConfirmationOutcome(confirmed=False, reference_values={})
    }


def test_the_filter_rejects_a_context_that_is_not_an_opportunity() -> None:
    """接続の食い違いは実行失敗にする（欠損に読み替えない）。"""
    with pytest.raises(KernelValueError, match="must carry an Opportunity"):
        condition_filter.evaluate(
            _filter_inputs(True, opportunity=ConditionState(True)),
            _parameters(include_start_bar=BoolValue(True)),
        )


def test_the_filter_declares_the_start_bar_parameter_without_a_default() -> None:
    """D05 §4.8: 使用箇所が値を明示する（既定値なし）。"""
    spec = condition_filter.CONTRACT.parameters["include_start_bar"]
    assert spec.default is None
    assert isinstance(condition_filter.CONTRACT.inputs["opportunity"].read_spec, CurrentContext)
    assert condition_filter.CONTRACT.outputs["confirmation"].kind is PortKind.EVENT


# --- (16) 追従する損切り -------------------------------------------------------


def _trailing(direction: TradeDirection, stop: str, level: str) -> dict[str, object]:
    context = FakePositionContext(
        position_id=PositionId(1),
        direction=direction,
        entry_price=_price("149.390"),
        effective_stop_loss=_price(stop),
    )
    result = trailing_stop.evaluate(
        ResolvedInputs(
            by_name={
                "position": (ContextSnapshot(payload=context, read_at=MOMENT),),
                "level": (_sample(_price(level)),),
            }
        ),
        {},
    )
    return dict(result.outputs)


def test_the_trailing_stop_tightens_a_long_position() -> None:
    """T02 §10: `01-07 12:00Z` に 148.950 → 149.150 へ引き上げる。"""
    assert _trailing(TradeDirection.LONG, "148.950", "149.150") == {
        "action": UpdateStop(stop_loss=_price("149.150"))
    }


def test_the_trailing_stop_does_not_move_on_an_equal_level() -> None:
    """T02 §10: `148.950 > 148.950` は偽なので出力を出さない。"""
    assert _trailing(TradeDirection.LONG, "148.950", "148.950") == {}


def test_the_trailing_stop_never_loosens() -> None:
    """D05 §4.10: 損切りを不利な向きへ動かさないことを部品側で保証する。"""
    assert _trailing(TradeDirection.LONG, "149.150", "148.950") == {}
    assert _trailing(TradeDirection.SHORT, "150.000", "150.100") == {}


def test_the_trailing_stop_tightens_a_short_position_downwards() -> None:
    """D05 §4.10: 売りは水準が下がるときだけ更新する。"""
    assert _trailing(TradeDirection.SHORT, "150.000", "149.900") == {
        "action": UpdateStop(stop_loss=_price("149.900"))
    }


# --- v2 の契約（D05 §9.2） -------------------------------------------------------


def test_the_v2_contracts_wait_one_daily_bar_where_the_paper_trace_says() -> None:
    """D05 §9.2: 日足の連鎖の3段と突破の水準が待機し、他の入力は見送りのまま。"""
    ema_spec = ema.CONTRACT_V2.inputs["prices"].read_spec
    assert isinstance(ema_spec, HistoryWindow)
    assert ema_spec.on_missing == WAIT_ONE_BAR
    for name in ("left", "right"):
        spec = compare.CONTRACT_V2.inputs[name].read_spec
        assert isinstance(spec, LatestAvailable)
        assert spec.on_missing == WAIT_ONE_BAR
    long_spec = from_condition.CONTRACT_V2.inputs["long_allowed"].read_spec
    short_spec = from_condition.CONTRACT_V2.inputs["short_allowed"].read_spec
    assert isinstance(long_spec, LatestAvailable)
    assert isinstance(short_spec, LatestAvailable)
    assert long_spec.on_missing == WAIT_ONE_BAR
    assert short_spec.on_missing == SkipEvaluation()
    level_spec = breakout.CONTRACT_V2.inputs["level"].read_spec
    price_spec = breakout.CONTRACT_V2.inputs["price"].read_spec
    assert isinstance(level_spec, LatestAvailable)
    assert isinstance(price_spec, LatestAvailable)
    assert level_spec.on_missing == WAIT_ONE_BAR
    assert price_spec.on_missing == SkipEvaluation()


def test_the_v2_order_and_protection_start_from_the_confirmation() -> None:
    """D05 §9.2・T02 §1.3: 確認結果の配送で起動し、注文意図は機会を現在コンテキストで読む。"""
    for contract in (market.CONTRACT_V2, level_stop.CONTRACT_V2):
        confirmation = contract.inputs["confirmation"]
        assert confirmation.data_type == CONFIRMATION_RESULT_V1
        assert isinstance(confirmation.read_spec, DeliveredEvent)
        assert contract.evaluation_spec.allowed[0].input_names == ("confirmation",)  # type: ignore[union-attr]
    assert isinstance(market.CONTRACT_V2.inputs["opportunity"].read_spec, CurrentContext)
    assert set(level_stop.CONTRACT_V2.inputs) == {"confirmation", "level"}


def test_the_v2_order_intent_reads_the_opportunity_from_the_context() -> None:
    """D05 §9.2: v1 と同じ実装が、現在コンテキストの機会から注文意図を作る。"""
    confirmation = ConfirmationResult(
        opportunity_id=OpportunityId(1),
        confirmation_interval=Interval(start=EARLIER, end=MOMENT),
        confirmed=True,
    )
    inputs = ResolvedInputs(
        by_name={
            "confirmation": (EventDelivery(payload=confirmation, source_output_id=OutputId(5)),),
            "opportunity": (ContextSnapshot(payload=_opportunity(), read_at=MOMENT),),
        }
    )

    assert market.REGISTRATION_V2.implementation.evaluate is market.evaluate
    result = market.evaluate(inputs, {})

    assert result.outputs == {
        "intent": OrderIntent(
            symbol=SIGNAL_SERIES.symbol,
            direction=TradeDirection.LONG,
            order_type=OrderType.MARKET,
        )
    }


def test_the_v2_level_stop_ignores_the_confirmation_payload() -> None:
    """D05 §9.2: 計算は水準だけを読む。"""
    confirmation = ConfirmationResult(
        opportunity_id=OpportunityId(1),
        confirmation_interval=Interval(start=EARLIER, end=MOMENT),
        confirmed=True,
    )
    result = level_stop.evaluate(
        ResolvedInputs(
            by_name={
                "confirmation": (
                    EventDelivery(payload=confirmation, source_output_id=OutputId(5)),
                ),
                "level": (_sample(_price("148.950")),),
            }
        ),
        {},
    )

    assert result.outputs == {"protection": ProtectionLevels(stop_loss=_price("148.950"))}


def test_a_v2_contract_shares_the_implementation_of_its_v1() -> None:
    """D05 §9.2: 版が増えても実装の件数は増えない（同じ実装参照）。"""
    pairs = (
        (ema.REGISTRATION, ema.REGISTRATION_V2),
        (compare.REGISTRATION, compare.REGISTRATION_V2),
        (from_condition.REGISTRATION, from_condition.REGISTRATION_V2),
        (breakout.REGISTRATION, breakout.REGISTRATION_V2),
        (market.REGISTRATION, market.REGISTRATION_V2),
        (level_stop.REGISTRATION, level_stop.REGISTRATION_V2),
    )
    for v1, v2 in pairs:
        assert v1.implementation_ref == v2.implementation_ref
        assert v1.contract.version == 1
        assert v2.contract.version == 2
        assert contract_ref_for(v1.contract) != contract_ref_for(v2.contract)


# --- 登録と部品テーブル ----------------------------------------------------------

STAGE3_REGISTRATIONS = (
    ema.REGISTRATION,
    ema.REGISTRATION_V2,
    atr.REGISTRATION,
    compare.REGISTRATION,
    compare.REGISTRATION_V2,
    logic.ALL_REGISTRATION,
    logic.ANY_REGISTRATION,
    transition.REGISTRATION,
    from_condition.REGISTRATION,
    from_condition.REGISTRATION_V2,
    from_condition.CONSTANT_REGISTRATION,
    condition_filter.REGISTRATION,
    trailing_stop.REGISTRATION,
    breakout.REGISTRATION_V2,
    market.REGISTRATION_V2,
    level_stop.REGISTRATION_V2,
)


def test_the_catalog_holds_stage_2_and_stage_3_in_one_table() -> None:
    """D05 §4.1: 同じ鍵が2度現れない。段階3 は新しい部品10件と v2 の契約6件。"""
    assert sorted(str(key) for key in INITIAL_CATALOG.registrations) == [
        "all_conditions@v1",
        "any_condition@v1",
        "atr@v1",
        "breakout_trigger@v1",
        "breakout_trigger@v2",
        "condition_filter@v1",
        "condition_transition@v1",
        "constant_condition@v1",
        "ema@v1",
        "ema@v2",
        "extreme_price@v1",
        "fixed_rr_take_profit@v1",
        "level_stop_loss@v1",
        "level_stop_loss@v2",
        "market_order_intent@v1",
        "market_order_intent@v2",
        "permission_from_condition@v1",
        "permission_from_condition@v2",
        "price_compare@v1",
        "price_compare@v2",
        "trailing_stop@v1",
    ]


def test_every_stage_3_registration_is_in_the_catalog() -> None:
    """段階3 の登録は、コンパイラが新しい検査を備えた変更で部品テーブルに載せた（D05 §5.6）。"""
    for registration in STAGE3_REGISTRATIONS:
        assert INITIAL_CATALOG.get(registration.key) is registration
    assert len(INITIAL_CATALOG.registrations) == 5 + len(STAGE3_REGISTRATIONS)


def test_a_parameter_constraint_must_read_declared_parameters() -> None:
    """D05 §4.1 の検査 (e): 関係が読む名前は契約のパラメータになければならない。"""
    constraint = ParameterConstraint(
        reads=("window_bars", "missing"), check=lambda _: True, message="x"
    )
    with pytest.raises(KernelValueError, match="not declared"):
        ComponentRegistration(
            contract=ema.CONTRACT,
            implementation=StatelessImplementation(evaluate=ema.evaluate),
            implementation_ref=ema.IMPLEMENTATION_REF,
            parameter_constraint=constraint,
        )


def test_a_parameter_constraint_must_read_at_least_one_parameter() -> None:
    """D05 §4.1 の検査 (e): 読むパラメータが空の関係は置けない。"""
    with pytest.raises(KernelValueError, match="at least one"):
        ParameterConstraint(reads=(), check=lambda _: True, message="x")


def test_the_parameter_constraint_is_not_part_of_the_contract_digest() -> None:
    """D05 §4.1: 関係は登録に載るので契約の指紋に入らない。"""
    without = ComponentRegistration(
        contract=ema.CONTRACT,
        implementation=StatelessImplementation(evaluate=ema.evaluate),
        implementation_ref=ema.IMPLEMENTATION_REF,
    )
    assert contract_ref_for(without.contract) == contract_ref_for(ema.REGISTRATION.contract)


# --- 内容型（D05 §4.2） ----------------------------------------------------------


def test_a_confirmation_component_returns_the_outcome_type() -> None:
    """D05 §4.2: 確認結果は部品が返す型と配送される型が分かれる。"""
    assert payload_type_for(CONFIRMATION_RESULT_V1, as_component_return=True) == (
        ConfirmationOutcome,
    )
    assert payload_type_for(CONFIRMATION_RESULT_V1) == (ConfirmationResult,)


def test_a_management_component_may_return_an_update_stop() -> None:
    """D05 §4.2・D04 §11.2: `management_action@v1` の部品は `UPDATE_STOP` も返せる。"""
    assert payload_type_for(MANAGEMENT_ACTION_V1) == (SetTakeProfit, ClosePosition, UpdateStop)
    assert UpdateStop(stop_loss=_price("149.150")).kind == "UPDATE_STOP"


def test_the_confirmation_outcome_requires_a_boolean() -> None:
    """成否は真偽値そのもので持つ。"""
    with pytest.raises(KernelValueError):
        ConfirmationOutcome(confirmed=1, reference_values={})  # type: ignore[arg-type]


# --- 待機の宣言（D04 §6.3 v1.9） ---------------------------------------------------


def test_a_wait_declares_all_three_fields() -> None:
    """D04 §6.3: 期限・期限切れの動作・追い越し時の動作はいずれも必須。"""
    with pytest.raises(KernelValueError, match="on_superseded"):
        WaitForInput(
            deadline=BarsDeadline(bars=1),
            on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
            on_superseded="EXPIRE_REQUEST",  # type: ignore[arg-type]
        )


def test_a_read_spec_accepts_the_wait_policy() -> None:
    """D04 §6.3: 読み取り条件の欠損方針に待機を書ける（解禁の判断はコンパイラ）。"""
    assert LatestAvailable(on_missing=WAIT_ONE_BAR).on_missing == WAIT_ONE_BAR
