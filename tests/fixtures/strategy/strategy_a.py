"""検証戦略 A の宣言（D04 §14・D05 §9・T01 §1.3）。

1時間足の終値が直近20本の確定高値（当該足を除く）を上抜けたら、後続確認なしの成行で入り、
直近20本の確定安値を初期の損切りに置き、約定後に固定リスクリワード比で利確を置く。

| 使用箇所 | 契約 | 起動条件 |
|---|---|---|
| `breakout_level` | 高値・安値の抽出（最大） | 1時間足の確定 |
| `stop_level` | 高値・安値の抽出（最小） | 同上 |
| `entry_trigger` | 水準突破の検出（買い） | 同上 |
| `entry_order` | 成行の注文意図 | 取引機会の配送 |
| `initial_stop` | 価格水準型の初期損切り | 同上 |
| `take_profit` | 固定リスクリワード比の利確 | 建玉の生成通知 |

評価順は `breakout_level` → `stop_level` → `entry_trigger` → `entry_order` →
`initial_stop` → `take_profit`（D05 §5.4 の規則で一意に定まる）。
"""

from __future__ import annotations

from typing import Final

from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.strategy.catalog.exits import fixed_rr
from odyssey_fx.strategy.catalog.features import extreme
from odyssey_fx.strategy.catalog.orders import market
from odyssey_fx.strategy.catalog.protection import level_stop
from odyssey_fx.strategy.catalog.triggers import breakout
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.entry_policy import ImmediateEntry
from odyssey_fx.strategy.declarations.evaluation import (
    EvaluationSchedule,
    OnBarClose,
    OnInputEvent,
    OnRuntimeEvent,
    RuntimeEventKind,
)
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.opportunity import (
    OnNewTrigger,
    OnOrderAccepted,
    OpportunityConcurrencySpec,
    OpportunityValiditySpec,
)
from odyssey_fx.strategy.declarations.refs import (
    MarketDataField,
    MarketDataRef,
    OutputRef,
    RuntimeInputRef,
    RuntimeTarget,
)
from odyssey_fx.strategy.declarations.specs import (
    FloatValue,
    InputBinding,
    IntValue,
    StrValue,
)
from tests.fixtures.synthetic.market import TIMEFRAME_DEFS, USDJPY

__all__ = [
    "EVALUATION_ORDER",
    "LOOKBACK",
    "REWARD_RISK",
    "SIGNAL_SERIES",
    "TIMEFRAMES",
    "strategy_a",
]

#: 評価系列（T01 §1.1）。
SIGNAL_SERIES: Final = SeriesId(
    symbol=USDJPY, timeframe=TIMEFRAME_DEFS["1h"].ref, basis=PriceBasis.BID
)

#: 時間足定義（コンパイラの能力検査が足の細かさを見るために要る）。
TIMEFRAMES: Final = {item.ref: item for item in TIMEFRAME_DEFS.values()}

#: 直近何本の確定足から高値・安値を取るか（D05 §9）。
LOOKBACK: Final = 20

#: 取ったリスクの何倍で利確するか（D05 §9）。
REWARD_RISK: Final = 2.0

#: D05 §9 が示す評価順。
EVALUATION_ORDER: Final[tuple[str, ...]] = (
    "breakout_level",
    "stop_level",
    "entry_trigger",
    "entry_order",
    "initial_stop",
    "take_profit",
)


def _extreme_instance(
    instance_id: str, mode: str, market_field: MarketDataField
) -> ComponentInstance:
    return ComponentInstance(
        instance_id=instance_id,
        contract_ref=contract_ref_for(extreme.CONTRACT),
        inputs={
            "prices": InputBinding(sources=(MarketDataRef(SIGNAL_SERIES, market_field),)),
        },
        parameters={"lookback": IntValue(LOOKBACK), "mode": StrValue(mode)},
        evaluation=EvaluationSchedule(triggers=(OnBarClose("h1", SIGNAL_SERIES),)),
    )


def strategy_a(
    *,
    lookback: int = LOOKBACK,
    stop_lookback: int | None = None,
    reward_risk: float = REWARD_RISK,
    max_active: int = 1,
    on_new_trigger: OnNewTrigger = OnNewTrigger.KEEP_EXISTING,
    on_order_accepted: OnOrderAccepted = OnOrderAccepted.KEEP_OTHERS,
) -> StrategyDefinition:
    """検証戦略 A の宣言を作る（D05 §9）。

    `stop_lookback` に別の本数を渡すと、T01 §6.1 が使う「ウォームアップの長さが違う宣言」
    （損切り水準だけ履歴が足りない状態）を作れる。
    """
    breakout_level = _extreme_instance("breakout_level", "MAX", MarketDataField.HIGH)
    if lookback != LOOKBACK:
        breakout_level = ComponentInstance(
            instance_id=breakout_level.instance_id,
            contract_ref=breakout_level.contract_ref,
            inputs=dict(breakout_level.inputs),
            parameters={"lookback": IntValue(lookback), "mode": StrValue("MAX")},
            evaluation=breakout_level.evaluation,
        )
    stop_level = _extreme_instance("stop_level", "MIN", MarketDataField.LOW)
    if stop_lookback is not None:
        stop_level = ComponentInstance(
            instance_id=stop_level.instance_id,
            contract_ref=stop_level.contract_ref,
            inputs=dict(stop_level.inputs),
            parameters={"lookback": IntValue(stop_lookback), "mode": StrValue("MIN")},
            evaluation=stop_level.evaluation,
        )

    entry_trigger = ComponentInstance(
        instance_id="entry_trigger",
        contract_ref=contract_ref_for(breakout.CONTRACT),
        inputs={
            "price": InputBinding(sources=(MarketDataRef(SIGNAL_SERIES, MarketDataField.CLOSE),)),
            "level": InputBinding(sources=(OutputRef("breakout_level", "level"),)),
        },
        parameters={"direction": StrValue("LONG")},
        evaluation=EvaluationSchedule(triggers=(OnBarClose("h1", SIGNAL_SERIES),)),
    )
    entry_order = ComponentInstance(
        instance_id="entry_order",
        contract_ref=contract_ref_for(market.CONTRACT),
        inputs={
            "opportunity": InputBinding(sources=(OutputRef("entry_trigger", "opportunity"),)),
        },
        parameters={},
        evaluation=EvaluationSchedule(triggers=(OnInputEvent("opp", "opportunity"),)),
    )
    initial_stop = ComponentInstance(
        instance_id="initial_stop",
        contract_ref=contract_ref_for(level_stop.CONTRACT),
        inputs={
            "opportunity": InputBinding(sources=(OutputRef("entry_trigger", "opportunity"),)),
            "level": InputBinding(sources=(OutputRef("stop_level", "level"),)),
        },
        parameters={},
        evaluation=EvaluationSchedule(triggers=(OnInputEvent("opp", "opportunity"),)),
    )
    take_profit = ComponentInstance(
        instance_id="take_profit",
        contract_ref=contract_ref_for(fixed_rr.CONTRACT),
        inputs={"position": InputBinding(sources=(RuntimeInputRef(RuntimeTarget.POSITION),))},
        parameters={"reward_risk": FloatValue(reward_risk)},
        evaluation=EvaluationSchedule(
            triggers=(OnRuntimeEvent("filled", RuntimeEventKind.POSITION_OPENED),)
        ),
    )

    return StrategyDefinition(
        strategy_id="strategy_a",
        version=1,
        components=(
            breakout_level,
            stop_level,
            entry_trigger,
            entry_order,
            initial_stop,
            take_profit,
        ),
        market_state=None,
        trigger=OutputRef("entry_trigger", "opportunity"),
        execution_filter=None,
        order=OutputRef("entry_order", "intent"),
        protection=OutputRef("initial_stop", "protection"),
        exit=OutputRef("take_profit", "action"),
        entry_policy=ImmediateEntry(),
        opportunity_validity=OpportunityValiditySpec(bindings=()),
        opportunity_concurrency=OpportunityConcurrencySpec(
            max_active=max_active,
            on_new_trigger=on_new_trigger,
            on_order_accepted=on_order_accepted,
        ),
    )
