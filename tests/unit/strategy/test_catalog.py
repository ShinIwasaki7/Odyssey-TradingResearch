"""段階2の5部品の計算規則と、登録時の検査（D05 §4）。

部品は純粋関数である。同じ入力・パラメータ・状態に対して常に同じ結果を返し、実時計・乱数・
I/O を読まない。ここでは計算規則そのものを、解決済みの入力を直接組み立てて確かめる。

| 部品 | 規則 |
|---|---|
| 高値・安値の抽出 | 窓内の最大または最小 |
| 水準突破の検出 | 不成立から成立へ変わった評価でだけ発火し、状態は毎回更新する |
| 成行の注文意図 | 機会の銘柄と方向をそのまま注文意図にする |
| 価格水準型の初期損切り | 接続された水準を絶対価格として凍結する |
| 固定リスクリワード比の利確 | 約定価格と損切り水準から利確水準を決める |
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import OpportunityId, OutputId, PositionId
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.refs import ContentDigest, ImplementationRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.strategy.catalog.exits import fixed_rr
from odyssey_fx.strategy.catalog.features import extreme
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.orders import market
from odyssey_fx.strategy.catalog.protection import level_stop
from odyssey_fx.strategy.catalog.registry import (
    ComponentOutputs,
    ComponentRegistration,
    ContractKey,
    StatefulImplementation,
    StatelessImplementation,
    build_registry,
)
from odyssey_fx.strategy.catalog.triggers import breakout
from odyssey_fx.strategy.compiler.compiled import (
    ResolvedMarketSource,
    ResolvedParameter,
)
from odyssey_fx.strategy.declarations.datatypes import CONDITION_STATE_V1, PRICE_V1
from odyssey_fx.strategy.declarations.refs import MarketDataField
from odyssey_fx.strategy.declarations.specs import (
    FloatValue,
    IntValue,
    StrValue,
    UnitRef,
)
from odyssey_fx.strategy.records.payloads import (
    ConditionState,
    Opportunity,
    OpportunityContent,
    OrderIntent,
    OrderType,
    ProtectionLevels,
    SetTakeProfit,
    TradeDirection,
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

MOMENT = UtcTime.from_components(2026, 1, 6, 9, 0)
EARLIER = UtcTime.from_components(2026, 1, 6, 8, 0)
SOURCE = ResolvedMarketSource(SIGNAL_SERIES, MarketDataField.CLOSE)


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _sample(text: str) -> ValueSample:
    return ValueSample(payload=_price(text), source=SOURCE, freshness_time=MOMENT)


def _window(*texts: str) -> ResolvedInputs:
    return ResolvedInputs(
        by_name={"prices": (ValueWindow(samples=tuple(_sample(text) for text in texts)),)}
    )


def _parameters(**values: object) -> dict[str, ResolvedParameter]:
    parameters: dict[str, ResolvedParameter] = {}
    for name, value in values.items():
        unit = UnitRef.RATIO if isinstance(value, FloatValue) else None
        decimal_value: Decimal | None = None
        if isinstance(value, FloatValue):
            decimal_value = decimal_from_str(repr(value.value))
        parameters[name] = ResolvedParameter(
            name=name,
            value=value,  # type: ignore[arg-type]
            unit=unit,
            decimal_value=decimal_value,
        )
    return parameters


# --- (1) 高値・安値の抽出 ---------------------------------------------------


def test_the_extreme_component_returns_the_window_maximum() -> None:
    """D05 §4.3(1): `MAX` は窓内の最大を返す。"""
    result = extreme.evaluate(
        _window("149.900", "150.000", "149.800"),
        _parameters(lookback=IntValue(3), mode=StrValue("MAX")),
    )

    assert result.outputs == {"level": _price("150.000")}
    assert result.new_state is None


def test_the_extreme_component_returns_the_window_minimum() -> None:
    """D05 §4.3(1): `MIN` は窓内の最小を返す。"""
    result = extreme.evaluate(
        _window("149.900", "150.000", "149.500"),
        _parameters(lookback=IntValue(3), mode=StrValue("MIN")),
    )

    assert result.outputs == {"level": _price("149.500")}


def test_the_extreme_component_rejects_a_window_of_the_wrong_length() -> None:
    """宣言した本数と実際に読んだ履歴が食い違ったら、黙って計算しない。"""
    with pytest.raises(KernelValueError, match="expected 5 bars"):
        extreme.evaluate(
            _window("149.900", "150.000"),
            _parameters(lookback=IntValue(5), mode=StrValue("MAX")),
        )


# --- (2) 水準突破の検出 ------------------------------------------------------


def _breakout_inputs(price: str, level: str) -> ResolvedInputs:
    return ResolvedInputs(by_name={"price": (_sample(price),), "level": (_sample(level),)})


def test_the_breakout_fires_when_the_condition_turns_true() -> None:
    """D05 §4.3(2): 不成立から成立へ変わった評価でだけ発火する。"""
    result = breakout.evaluate(
        _breakout_inputs("150.040", "150.000"),
        _parameters(direction=StrValue("LONG")),
        ConditionState(False),
    )

    assert result.outputs == {
        "opportunity": OpportunityContent(
            direction=TradeDirection.LONG,
            reference_values={"breakout_level": _price("150.000")},
        )
    }
    assert result.new_state == ConditionState(True)


def test_the_breakout_does_not_fire_while_the_condition_stays_true() -> None:
    """D04 §10.4: 成立が続く間は再発火しない。状態は成立のまま更新する。"""
    result = breakout.evaluate(
        _breakout_inputs("150.040", "150.000"),
        _parameters(direction=StrValue("LONG")),
        ConditionState(True),
    )

    assert result.outputs == {}
    assert result.new_state == ConditionState(True)


def test_the_breakout_rearms_when_the_condition_turns_false() -> None:
    """D04 §10.4: 再武装は条件が不成立へ戻った時点。"""
    result = breakout.evaluate(
        _breakout_inputs("149.900", "150.000"),
        _parameters(direction=StrValue("LONG")),
        ConditionState(True),
    )

    assert result.outputs == {}
    assert result.new_state == ConditionState(False)


def test_a_short_breakout_compares_the_other_way() -> None:
    """D05 §4.3(2): 売りは水準を下回ったときに発火する。"""
    result = breakout.evaluate(
        _breakout_inputs("149.400", "149.500"),
        _parameters(direction=StrValue("SHORT")),
        ConditionState(False),
    )

    assert result.outputs["opportunity"] == OpportunityContent(
        direction=TradeDirection.SHORT,
        reference_values={"breakout_level": _price("149.500")},
    )


def test_the_breakout_requires_a_condition_state() -> None:
    """状態の型が違えば、黙って既定値で動かさず失敗させる。"""
    with pytest.raises(KernelValueError, match="must be a ConditionState"):
        breakout.evaluate(
            _breakout_inputs("150.040", "150.000"),
            _parameters(direction=StrValue("LONG")),
            None,
        )


# --- (3) 成行の注文意図 ------------------------------------------------------


def _opportunity(direction: TradeDirection = TradeDirection.LONG) -> Opportunity:
    return Opportunity(
        opportunity_id=OpportunityId(1),
        symbol=SIGNAL_SERIES.symbol,
        direction=direction,
        signal_interval=Interval(start=EARLIER, end=MOMENT),
        reference_values={},
    )


def test_the_market_order_intent_copies_the_symbol_and_direction() -> None:
    """D05 §4.3(3): 数量・価格は決めない（エンジン側のリスク方針が決める）。"""
    delivery = EventDelivery(payload=_opportunity(), source_output_id=OutputId(1))

    result = market.evaluate(ResolvedInputs(by_name={"opportunity": (delivery,)}), _parameters())

    assert result.outputs == {
        "intent": OrderIntent(
            symbol=SIGNAL_SERIES.symbol,
            direction=TradeDirection.LONG,
            order_type=OrderType.MARKET,
            price_condition=None,
            expiry=None,
        )
    }


# --- (4) 価格水準型の初期損切り ---------------------------------------------


def test_the_level_stop_freezes_the_absolute_price() -> None:
    """D05 §4.3(4): 距離型は使わず、解決済みの絶対価格を凍結する。"""
    delivery = EventDelivery(payload=_opportunity(), source_output_id=OutputId(1))

    result = level_stop.evaluate(
        ResolvedInputs(by_name={"opportunity": (delivery,), "level": (_sample("149.500"),)}),
        _parameters(),
    )

    assert result.outputs == {
        "protection": ProtectionLevels(stop_loss=_price("149.500"), take_profit=None)
    }


# --- (5) 固定リスクリワード比の利確 -----------------------------------------


def _position_inputs(direction: TradeDirection, entry: str, stop: str | None) -> ResolvedInputs:
    context = FakePositionContext(
        position_id=PositionId(1),
        direction=direction,
        entry_price=_price(entry),
        effective_stop_loss=None if stop is None else _price(stop),
    )
    return ResolvedInputs(by_name={"position": (ContextSnapshot(payload=context, read_at=MOMENT),)})


def test_the_fixed_reward_risk_take_profit_matches_the_paper_trace() -> None:
    """T01 §2.5: `150.080` と `149.500` から `151.240`（`risk = 0.580`、比 2.0）。"""
    result = fixed_rr.evaluate(
        _position_inputs(TradeDirection.LONG, "150.080", "149.500"),
        _parameters(reward_risk=FloatValue(2.0)),
    )

    assert result.outputs == {"action": SetTakeProfit(price=_price("151.240"))}


def test_a_short_position_places_the_take_profit_below_the_entry() -> None:
    """D05 §4.3(5): 売りは約定価格から下へ、取ったリスクの倍数だけ離す。"""
    result = fixed_rr.evaluate(
        _position_inputs(TradeDirection.SHORT, "150.000", "150.500"),
        _parameters(reward_risk=FloatValue(2.0)),
    )

    assert result.outputs == {"action": SetTakeProfit(price=_price("149.000"))}


def test_the_take_profit_needs_an_effective_stop_loss() -> None:
    """D05 §4.3(5): 損切り水準が無ければ比率を当てる基準が無い。"""
    with pytest.raises(KernelValueError, match="effective stop loss"):
        fixed_rr.evaluate(
            _position_inputs(TradeDirection.LONG, "150.080", None),
            _parameters(reward_risk=FloatValue(2.0)),
        )


def test_a_long_stop_above_the_entry_is_rejected() -> None:
    """向きが矛盾した建玉では利確を計算しない。"""
    with pytest.raises(KernelValueError, match="below the entry price"):
        fixed_rr.evaluate(
            _position_inputs(TradeDirection.LONG, "149.000", "150.000"),
            _parameters(reward_risk=FloatValue(2.0)),
        )


def test_the_ratio_must_arrive_as_a_decimal() -> None:
    """D05 §4.4: 比率は厳密な10進数で渡す。部品に浮動小数の演算を持ち込まない。"""
    parameters = {
        "reward_risk": ResolvedParameter(
            name="reward_risk", value=FloatValue(2.0), unit=UnitRef.RATIO, decimal_value=None
        )
    }
    with pytest.raises(KernelValueError, match="converted to Decimal"):
        fixed_rr.evaluate(_position_inputs(TradeDirection.LONG, "150.080", "149.500"), parameters)


# --- 登録時の検査（D05 §4.1） -----------------------------------------------


def test_the_initial_catalog_holds_the_five_stage_2_components() -> None:
    """D05 §4.3（Q8 決定）: 役割ごとに1部品、高値と安値は1部品を2使用箇所で使う。

    段階3 の登録が加わっても、段階2 の5部品は v1 のまま同じ登録で残る（D05 §9.2）。
    """
    stage_2 = (
        extreme.REGISTRATION,
        breakout.REGISTRATION,
        market.REGISTRATION,
        level_stop.REGISTRATION,
        fixed_rr.REGISTRATION,
    )
    for registration in stage_2:
        assert INITIAL_CATALOG.get(registration.key) is registration


def test_none_of_the_stage_2_components_use_numpy() -> None:
    """D05 §4.1: 段階2の5部品はいずれも NumPy を使わない。"""
    import pathlib

    catalog_root = pathlib.Path(extreme.__file__).resolve().parents[1]
    sources = [path.read_text(encoding="utf-8") for path in catalog_root.rglob("*.py")]

    assert not any("import numpy" in text for text in sources)


def test_a_stateful_contract_needs_a_stateful_implementation() -> None:
    """D05 §4.1 の検査 (b): 状態を宣言した契約の実装は状態付きでなければならない。"""
    with pytest.raises(KernelValueError, match="must be stateful"):
        ComponentRegistration(
            contract=breakout.CONTRACT,
            implementation=StatelessImplementation(evaluate=extreme.evaluate),
            implementation_ref=breakout.IMPLEMENTATION_REF,
        )


def test_a_stateless_contract_rejects_a_stateful_implementation() -> None:
    """D05 §4.1 の検査 (c): 状態を宣言しない契約の実装は状態なしでなければならない。"""
    with pytest.raises(KernelValueError, match="must be stateless"):
        ComponentRegistration(
            contract=extreme.CONTRACT,
            implementation=StatefulImplementation(
                evaluate=breakout.evaluate, state_type=CONDITION_STATE_V1
            ),
            implementation_ref=extreme.IMPLEMENTATION_REF,
        )


def test_the_registered_implementation_reference_must_match_the_contract() -> None:
    """D05 §4.1 の検査 (d): 取り違えると再現性の識別が壊れる。"""
    with pytest.raises(KernelValueError, match="must match the contract"):
        ComponentRegistration(
            contract=extreme.CONTRACT,
            implementation=StatelessImplementation(evaluate=extreme.evaluate),
            implementation_ref=ImplementationRef("other", ContentDigest.sha256("e" * 64)),
        )


def test_a_component_cannot_be_registered_twice() -> None:
    """同じ鍵が2度現れる静的テーブルは、どちらが使われるか読めない。"""
    with pytest.raises(KernelValueError, match="registered more than once"):
        build_registry((extreme.REGISTRATION, extreme.REGISTRATION))


def test_an_output_value_of_none_is_rejected() -> None:
    """D05 §4.1: 出さなかった出力はキーごと省く。`None` を出力値にしない。"""
    with pytest.raises(KernelValueError, match="omit the key instead"):
        ComponentOutputs(outputs={"level": None})


def test_the_registry_key_is_the_component_id_and_version() -> None:
    """D05 §4.1: レジストリの鍵は部品 ID と版の組。"""
    assert INITIAL_CATALOG.get(ContractKey("extreme_price", 1)) is extreme.REGISTRATION
    assert INITIAL_CATALOG.get(ContractKey("extreme_price", 2)) is None


def test_the_data_type_of_every_stage_2_output_has_a_runtime_type() -> None:
    """D05 §4.1 の検査 (a): 出力のデータ型は実行時クラスへ対応していなければならない。"""
    for registration in INITIAL_CATALOG.registrations.values():
        for spec in registration.contract.outputs.values():
            assert spec.data_type != PRICE_V1 or spec.kind is not None
