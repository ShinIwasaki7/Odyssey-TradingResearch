"""検証戦略 B の宣言（D05 §9.2・T02 §1.3）。

日足の終値が日足 EMA を上回るときだけ買いを許し、1時間足の終値が利用可能な日足高値を
上抜けたら取引機会を作り、15分足の終値が15分足 EMA を上回るのを最大4本待って確認し、成行で
入る。初期の損切りは直近20本の1時間足安値、保有中は1時間足ごとに安値へ追従させる。

| 使用箇所 | 契約 | 起動条件 | 役割 |
|---|---|---|---|
| `daily_ema` | `ema` v2（日足を1本ぶん待つ） | 日足の確定 | — |
| `daily_above_ema` | `price_compare` v2 | 日足の確定 | — |
| `no_short` | `constant_condition` v1 | 日足の確定 | — |
| `market_state` | `permission_from_condition` v2 | 日足の確定 | 市場状態 |
| `entry_trigger` | `breakout_trigger` v2 | 1時間足の確定 | 取引機会 |
| `m15_ema` | `ema` v1 | 15分足の確定 | — |
| `m15_above_ema` | `price_compare` v1 | 15分足の確定 | — |
| `entry_filter` | `condition_filter` v1 | 15分足の確定 | 後続確認 |
| `entry_order` | `market_order_intent` v2 | 確認結果の配送 | 注文意図 |
| `initial_stop` | `level_stop_loss` v2 | 確認結果の配送 | 初期損切り |
| `stop_level` | `extreme_price` v1 | 1時間足の確定 | — |
| `trailing` | `trailing_stop` v1 | 1時間足の確定 | 決済（追従する損切り） |

`entry_order` の `opportunity` 入力は D05 §9.2 の表に書かれていないが、`market_order_intent`
v2 の契約が必須入力（接続数 1）として持つ（D05 §9.2 の本文「v2 も `RuntimeInputRef(OPPORTUNITY)`
の入力を1つ持つ」）。表の接続に加えてここで接続する。

日足の系列は T02 と同じ `1d_ny17`（1時間足から集約する日足。T02 §16）を使う。
"""

from __future__ import annotations

from typing import Final

from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.catalog.conditions import compare
from odyssey_fx.strategy.catalog.exits import trailing_stop
from odyssey_fx.strategy.catalog.features import ema, extreme
from odyssey_fx.strategy.catalog.filters import condition_filter
from odyssey_fx.strategy.catalog.orders import market
from odyssey_fx.strategy.catalog.permissions import from_condition
from odyssey_fx.strategy.catalog.protection import level_stop
from odyssey_fx.strategy.catalog.triggers import breakout
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.entry_policy import (
    AwaitConfirmation,
    BarsDeadline,
    DeadlineAction,
)
from odyssey_fx.strategy.declarations.evaluation import (
    EvaluationSchedule,
    OnBarClose,
    OnInputEvent,
)
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.missing import Error as MissingPolicyError
from odyssey_fx.strategy.declarations.missing import SkipEvaluation
from odyssey_fx.strategy.declarations.opportunity import (
    OnNewTrigger,
    OnOrderAccepted,
    OpportunityConcurrencySpec,
    OpportunityValiditySpec,
    ValidityBinding,
    ValidityMode,
)
from odyssey_fx.strategy.declarations.refs import (
    MarketDataField,
    MarketDataRef,
    OutputRef,
    RuntimeInputRef,
    RuntimeTarget,
)
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    InputBinding,
    IntValue,
    StrValue,
)
from tests.fixtures.synthetic.market import TIMEFRAME_DEFS, USDJPY, series

__all__ = [
    "DAILY_SERIES",
    "EVALUATION_ORDER",
    "HOURLY_SERIES",
    "M15_SERIES",
    "TIMEFRAMES",
    "strategy_b",
]

#: 日足（1時間足から集約する日足。T02 §16）。
DAILY_SERIES: Final[SeriesId] = series(USDJPY, "1d_ny17")
#: 1時間足（取引機会・損切り水準・追従の系列）。
HOURLY_SERIES: Final[SeriesId] = series(USDJPY, "1h")
#: 15分足（確認足の系列）。
M15_SERIES: Final[SeriesId] = series(USDJPY, "15m")

#: 時間足定義（コンパイラの能力検査が足の細かさを見るために要る）。
TIMEFRAMES: Final = {item.ref: item for item in TIMEFRAME_DEFS.values()}

#: D05 §9.2 末尾と T02 §1.3 が書いている評価順（D05 v2.4・T02 v1.1）。
#:
#: D05 §5.4 の規則（段ごとに `instance_id` 順。v1.3 の決定）を、取引機会の参照の因果辺
#: （`entry_trigger` → `entry_filter`・`entry_order`。D04 v1.13 §12、2026-09-24 の人間の
#: 再決定）を含めてこの宣言に当てた出力である。段0 = 上流を持たない4件、段1 = その直下の
#: 2件、段2 = `market_state`、段3 = `entry_trigger`、段4 = `entry_filter`、
#: 段5 = `entry_order`・`initial_stop`、段6 = `trailing`。
EVALUATION_ORDER: Final[tuple[str, ...]] = (
    "daily_ema",
    "m15_ema",
    "no_short",
    "stop_level",
    "daily_above_ema",
    "m15_above_ema",
    "market_state",
    "entry_trigger",
    "entry_filter",
    "entry_order",
    "initial_stop",
    "trailing",
)


def _on_close(name: str, series_id: SeriesId) -> EvaluationSchedule:
    return EvaluationSchedule(triggers=(OnBarClose(name, series_id),))


def _on_confirmation() -> EvaluationSchedule:
    return EvaluationSchedule(triggers=(OnInputEvent("conf", "confirmation"),))


def strategy_b(
    *,
    include_start_bar: bool = True,
    deadline_bars: int = 4,
    binding_on_missing: SkipEvaluation | MissingPolicyError | None = None,
) -> StrategyDefinition:
    """検証戦略 B の宣言を作る（D05 §9.2）。

    引数は意味論テストが宣言を少しだけ変えるためのもので、既定値が検証戦略 B そのものである。
    有効性の再検査の4区分を通す意味論テスト（D08 §13.2 #6）は、確認期限（`deadline_bars`）を
    長くし、有効性束縛の欠損方針（`binding_on_missing`）を見送りと失敗の2通りにした宣言を使う。
    """
    on_missing = SkipEvaluation() if binding_on_missing is None else binding_on_missing
    daily_close = MarketDataRef(DAILY_SERIES, MarketDataField.CLOSE)
    components = (
        ComponentInstance(
            instance_id="daily_ema",
            contract_ref=contract_ref_for(ema.CONTRACT_V2),
            inputs={"prices": InputBinding(sources=(daily_close,))},
            parameters={"period": IntValue(20), "window_bars": IntValue(60)},
            evaluation=_on_close("d1", DAILY_SERIES),
        ),
        ComponentInstance(
            instance_id="daily_above_ema",
            contract_ref=contract_ref_for(compare.CONTRACT_V2),
            inputs={
                "left": InputBinding(sources=(daily_close,)),
                "right": InputBinding(sources=(OutputRef("daily_ema", "value"),)),
            },
            parameters={"operator": StrValue("GT")},
            evaluation=_on_close("d1", DAILY_SERIES),
        ),
        ComponentInstance(
            instance_id="no_short",
            contract_ref=contract_ref_for(from_condition.CONSTANT_CONTRACT),
            inputs={},
            parameters={"value": BoolValue(False)},
            evaluation=_on_close("d1", DAILY_SERIES),
        ),
        ComponentInstance(
            instance_id="market_state",
            contract_ref=contract_ref_for(from_condition.CONTRACT_V2),
            inputs={
                "long_allowed": InputBinding(sources=(OutputRef("daily_above_ema", "condition"),)),
                "short_allowed": InputBinding(sources=(OutputRef("no_short", "condition"),)),
            },
            parameters={},
            evaluation=_on_close("d1", DAILY_SERIES),
        ),
        ComponentInstance(
            instance_id="entry_trigger",
            contract_ref=contract_ref_for(breakout.CONTRACT_V2),
            inputs={
                "price": InputBinding(
                    sources=(MarketDataRef(HOURLY_SERIES, MarketDataField.CLOSE),)
                ),
                "level": InputBinding(sources=(MarketDataRef(DAILY_SERIES, MarketDataField.HIGH),)),
            },
            parameters={"direction": StrValue("LONG")},
            evaluation=_on_close("h1", HOURLY_SERIES),
        ),
        ComponentInstance(
            instance_id="m15_ema",
            contract_ref=contract_ref_for(ema.CONTRACT),
            inputs={
                "prices": InputBinding(sources=(MarketDataRef(M15_SERIES, MarketDataField.CLOSE),))
            },
            parameters={"period": IntValue(20), "window_bars": IntValue(60)},
            evaluation=_on_close("m15", M15_SERIES),
        ),
        ComponentInstance(
            instance_id="m15_above_ema",
            contract_ref=contract_ref_for(compare.CONTRACT),
            inputs={
                "left": InputBinding(sources=(MarketDataRef(M15_SERIES, MarketDataField.CLOSE),)),
                "right": InputBinding(sources=(OutputRef("m15_ema", "value"),)),
            },
            parameters={"operator": StrValue("GT")},
            evaluation=_on_close("m15", M15_SERIES),
        ),
        ComponentInstance(
            instance_id="entry_filter",
            contract_ref=contract_ref_for(condition_filter.CONTRACT),
            inputs={
                "condition": InputBinding(sources=(OutputRef("m15_above_ema", "condition"),)),
                "opportunity": InputBinding(sources=(RuntimeInputRef(RuntimeTarget.OPPORTUNITY),)),
            },
            parameters={"include_start_bar": BoolValue(include_start_bar)},
            evaluation=_on_close("m15", M15_SERIES),
        ),
        ComponentInstance(
            instance_id="entry_order",
            contract_ref=contract_ref_for(market.CONTRACT_V2),
            inputs={
                "confirmation": InputBinding(sources=(OutputRef("entry_filter", "confirmation"),)),
                "opportunity": InputBinding(sources=(RuntimeInputRef(RuntimeTarget.OPPORTUNITY),)),
            },
            parameters={},
            evaluation=_on_confirmation(),
        ),
        ComponentInstance(
            instance_id="initial_stop",
            contract_ref=contract_ref_for(level_stop.CONTRACT_V2),
            inputs={
                "confirmation": InputBinding(sources=(OutputRef("entry_filter", "confirmation"),)),
                "level": InputBinding(sources=(OutputRef("stop_level", "level"),)),
            },
            parameters={},
            evaluation=_on_confirmation(),
        ),
        ComponentInstance(
            instance_id="stop_level",
            contract_ref=contract_ref_for(extreme.CONTRACT),
            inputs={
                "prices": InputBinding(sources=(MarketDataRef(HOURLY_SERIES, MarketDataField.LOW),))
            },
            parameters={"lookback": IntValue(20), "mode": StrValue("MIN")},
            evaluation=_on_close("h1", HOURLY_SERIES),
        ),
        ComponentInstance(
            instance_id="trailing",
            contract_ref=contract_ref_for(trailing_stop.CONTRACT),
            inputs={
                "position": InputBinding(sources=(RuntimeInputRef(RuntimeTarget.POSITION),)),
                "level": InputBinding(sources=(OutputRef("stop_level", "level"),)),
            },
            parameters={},
            evaluation=_on_close("h1", HOURLY_SERIES),
        ),
    )
    return StrategyDefinition(
        strategy_id="strategy_b",
        version=1,
        components=components,
        market_state=OutputRef("market_state", "permission"),
        trigger=OutputRef("entry_trigger", "opportunity"),
        execution_filter=OutputRef("entry_filter", "confirmation"),
        order=OutputRef("entry_order", "intent"),
        protection=OutputRef("initial_stop", "protection"),
        exit=OutputRef("trailing", "action"),
        entry_policy=AwaitConfirmation(
            deadline=BarsDeadline(bars=deadline_bars), on_deadline=DeadlineAction.EXPIRE
        ),
        opportunity_validity=OpportunityValiditySpec(
            bindings=(
                ValidityBinding(
                    source=OutputRef("daily_above_ema", "condition"),
                    mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST,
                    on_missing=on_missing,
                ),
            )
        ),
        opportunity_concurrency=OpportunityConcurrencySpec(
            max_active=1,
            on_new_trigger=OnNewTrigger.KEEP_EXISTING,
            on_order_accepted=OnOrderAccepted.KEEP_OTHERS,
        ),
    )
