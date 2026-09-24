"""役割ごとの実行時の内容型（上位設計書 §4.3.15、D05 §3・§4.2）。

部品が計算する「中身」の型である。識別子・判断時刻・因果順序といった共通メタデータは
ランタイムが `OutputRecord`（`records.records`）として外から付ける。部品が発生時刻や実行
履歴を書き換えることはない。

**取引機会だけ、部品が返す型と配送される型が異なる**（D05 §4.2）。`Opportunity` の5項目の
うち部品が計算できるのは方向と根拠値の2つだけで、識別子はランタイムが採番し、銘柄は
コンパイラがグラフ上を伝播させ、対象区間は起動した足が決める。いずれも部品の入力からは
復元できない。そこで**部品は `OpportunityContent` を返し、ランタイムが残り3つを付けて
`Opportunity` を組み立てる**。

**確認結果も同じ手法をとる**（D05 §4.8、段階3）。`ConfirmationResult` の3項目のうち部品が
計算できるのは成否だけなので、部品は `ConfirmationOutcome` を返し、ランタイムが機会の
識別子と確認足の区間を付けて `ConfirmationResult` を組み立てる。

データ型識別子（D04 §5）と本モジュールの型の対応表（`PAYLOAD_BINDINGS`）も本モジュールが
正本として持つ。`declarations` はこの表を持てない（`records` への上向き参照になるため。
D04 §5）。登録時の照合は `catalog` が行う（D05 §4.1）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AccountId, OpportunityId, PositionId
from odyssey_fx.common.money import CurrencyCode, Money, Price, PriceOffset, Quantity
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, ProcessingPoint
from odyssey_fx.strategy.declarations import datatypes
from odyssey_fx.strategy.declarations.datatypes import DataTypeRef
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    require_bool,
    require_identifier,
    require_instance,
)

__all__ = [
    "PAYLOAD_BINDINGS",
    "AccountContext",
    "ClosePosition",
    "ConditionState",
    "ConfirmationOutcome",
    "ConfirmationResult",
    "DataTypePayloadBinding",
    "ManagementAction",
    "MarketPermission",
    "Opportunity",
    "OpportunityContent",
    "OrderIntent",
    "OrderType",
    "PositionContext",
    "ProtectionLevels",
    "SetTakeProfit",
    "TradeDirection",
    "UpdateStop",
    "payload_type_for",
]


class TradeDirection(Enum):
    """取引の方向（D05 §3）。"""

    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(Enum):
    """注文の種別（D05 §3）。段階2は成行だけ（指値は能力検査で拒否）。"""

    MARKET = "MARKET"


@dataclass(frozen=True, slots=True)
class ConditionState:
    """条件の成否（上位設計書 §4.3.15 が正本）。項目は1つだけ。

    成立を観測した確定足の識別子は持たない。同じ足で2回評価された場合の重複抑止は、
    評価要求の集約（D05 §6.2、Q6 決定）が担うためである。状態に持たせると同じ規則が
    2か所に分かれる。
    """

    satisfied: bool

    def __post_init__(self) -> None:
        require_bool(self.satisfied, "ConditionState.satisfied")


@dataclass(frozen=True, slots=True)
class MarketPermission:
    """市場状態による取引許可（上位設計書 §4.3.15 が正本）。段階2では使わない。"""

    allow_long: bool
    allow_short: bool

    def __post_init__(self) -> None:
        require_bool(self.allow_long, "MarketPermission.allow_long")
        require_bool(self.allow_short, "MarketPermission.allow_short")


def _require_reference_values(values: object, label: str) -> Mapping[str, object]:
    if not isinstance(values, Mapping):
        raise KernelValueError(f"{label} must be a Mapping, got {values!r}")
    for name in values:
        require_identifier(name, f"{label} key")
    return freeze_mapping(dict(values))


@dataclass(frozen=True, slots=True)
class OpportunityContent:
    """部品が返す取引機会の中身（D05 §4.2）。

    識別子・銘柄・対象区間は持たない。それらはランタイムが付ける。
    """

    direction: TradeDirection
    reference_values: Mapping[str, object]

    def __post_init__(self) -> None:
        require_instance(self.direction, TradeDirection, "OpportunityContent.direction")
        object.__setattr__(
            self,
            "reference_values",
            _require_reference_values(self.reference_values, "OpportunityContent.reference_values"),
        )


@dataclass(frozen=True, slots=True)
class Opportunity:
    """配送される取引機会（上位設計書 §4.3.15 が正本の5フィールド）。

    機会の内容は不変である。失効・確認待ち・発注試行は別のライフサイクル記録
    （`runtime.opportunities`）が持つ（ADR-0031）。
    """

    opportunity_id: OpportunityId
    symbol: Symbol
    direction: TradeDirection
    signal_interval: Interval
    reference_values: Mapping[str, object]

    def __post_init__(self) -> None:
        require_instance(self.opportunity_id, OpportunityId, "Opportunity.opportunity_id")
        require_instance(self.symbol, Symbol, "Opportunity.symbol")
        require_instance(self.direction, TradeDirection, "Opportunity.direction")
        require_instance(self.signal_interval, Interval, "Opportunity.signal_interval")
        object.__setattr__(
            self,
            "reference_values",
            _require_reference_values(self.reference_values, "Opportunity.reference_values"),
        )


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    """後続確認の結果（上位設計書 §4.3.15 が正本）。段階2では使わない。

    `confirmed=False` は「条件が成立しなかった」であり、入力不足・期限切れ・追い越しとは
    区別する。
    """

    opportunity_id: OpportunityId
    confirmation_interval: Interval
    confirmed: bool

    def __post_init__(self) -> None:
        require_instance(self.opportunity_id, OpportunityId, "ConfirmationResult.opportunity_id")
        require_instance(
            self.confirmation_interval, Interval, "ConfirmationResult.confirmation_interval"
        )
        require_bool(self.confirmed, "ConfirmationResult.confirmed")


@dataclass(frozen=True, slots=True)
class ConfirmationOutcome:
    """部品が返す後続確認の中身（D05 §4.2・§4.8、段階3）。

    成否と根拠値だけを持ち、機会の識別子と確認足の区間は持たない。それらはランタイムが
    評価要求から付けて `ConfirmationResult` を組み立てる（部品が別の機会の確認結果を作れ
    ないようにするため）。
    """

    confirmed: bool
    reference_values: Mapping[str, object]

    def __post_init__(self) -> None:
        require_bool(self.confirmed, "ConfirmationOutcome.confirmed")
        object.__setattr__(
            self,
            "reference_values",
            _require_reference_values(
                self.reference_values, "ConfirmationOutcome.reference_values"
            ),
        )


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """注文意図（D05 §3）。

    `expiry=None` は「D06 が定める既定の有効時間に従う」を意味する（D05 §4.3(3)）。
    段階2は成行だけなので `price_condition` は常に `None` である。
    """

    symbol: Symbol
    direction: TradeDirection
    order_type: OrderType
    price_condition: None = None
    expiry: timedelta | None = None

    def __post_init__(self) -> None:
        require_instance(self.symbol, Symbol, "OrderIntent.symbol")
        require_instance(self.direction, TradeDirection, "OrderIntent.direction")
        require_instance(self.order_type, OrderType, "OrderIntent.order_type")
        if self.price_condition is not None:
            raise KernelValueError(
                "OrderIntent.price_condition must be None in stage 2 (limit orders are rejected"
                " by the capability check, D04 §12)"
            )
        if self.expiry is not None:
            if not isinstance(self.expiry, timedelta):
                raise KernelValueError(
                    f"OrderIntent.expiry must be a timedelta or None, got {self.expiry!r}"
                )
            if self.expiry <= timedelta(0):
                raise KernelValueError(f"OrderIntent.expiry must be positive, got {self.expiry!r}")


@dataclass(frozen=True, slots=True)
class ProtectionLevels:
    """初期の保護水準（D05 §3）。

    水準は**解決済みの絶対価格**で持ち、距離型は使わない（上位設計書 §4.7.3）。損切りが
    方向と整合するか（買いなら判断時の価格より下か）の検査は、参照価格を持つ受付側
    （D06）が行う。
    """

    stop_loss: Price
    take_profit: Price | None = None

    def __post_init__(self) -> None:
        require_instance(self.stop_loss, Price, "ProtectionLevels.stop_loss")
        if self.take_profit is not None:
            require_instance(self.take_profit, Price, "ProtectionLevels.take_profit")


@dataclass(frozen=True, slots=True)
class SetTakeProfit:
    """初期の利確水準を置く要求（D04 §11.2 の `SET_TAKE_PROFIT`）。"""

    price: Price
    kind: str = "SET_TAKE_PROFIT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "SET_TAKE_PROFIT", "SetTakeProfit.kind")
        require_instance(self.price, Price, "SetTakeProfit.price")


@dataclass(frozen=True, slots=True)
class ClosePosition:
    """全数量決済の要求（D04 §11.2 の `CLOSE_POSITION`）。"""

    kind: str = "CLOSE_POSITION"

    def __post_init__(self) -> None:
        require_kind(self.kind, "CLOSE_POSITION", "ClosePosition.kind")


@dataclass(frozen=True, slots=True)
class UpdateStop:
    """損切り水準の更新要求（D04 §11.2 の `UPDATE_STOP`、D05 §4.2。段階3）。

    水準は**解決済みの絶対価格**で渡し、距離型は使わない（上位設計書 §4.7.1・§4.7.3）。
    不利な向きへ動かさない判定は部品側（D05 §4.10）、適用の意味論（どの執行足から有効か、
    同じ判断時点の決済要求との競合、価格刻みへの丸め）は D06 §8.3 が持つ。
    """

    stop_loss: Price
    kind: str = "UPDATE_STOP"

    def __post_init__(self) -> None:
        require_kind(self.kind, "UPDATE_STOP", "UpdateStop.kind")
        require_instance(self.stop_loss, Price, "UpdateStop.stop_loss")


#: 区分タグ付き union（D04 §11.2）。段階2 の2区分に、段階3 の損切り水準の更新（`UpdateStop`）を
#: 足した3区分である（D05 §3、T02 §19 の引き渡し #2）。
#:
#: この union は管理要求（`runtime.requests.ManagementRequest.action`）の型であり、判断履歴の
#: 表12（`MANAGEMENT_APPLICATIONS`、D06 §9.2）の列はこの型から導かれる（区分タグ付き union は
#: 全変種のフィールドの和集合を列にする。D06 §9.1 の規則2）。3区分目を足すと表12 に列
#: `action_stop_loss` が増えるので、足したのはエンジンが損切り水準の更新を建玉へ適用する変更
#: （D06 §4.2 の手順6・§8.3）と同じ段階3 実装 PR 5/5 である（2026-09-24 の人間の決定）。
ManagementAction = SetTakeProfit | ClosePosition | UpdateStop


@dataclass(frozen=True, slots=True)
class PositionContext:
    """建玉の時点情報（`position_context@v1`、D06 §8.4）。

    **項目を決めるのは D06**（エンジンが評価時点に供給してよい情報の範囲そのものだから）
    だが、これを読むのは `strategy.catalog` の部品であり、`strategy` は `backtest` を参照
    できない（D01 §3.2）。そこでクラスはここに置き、`backtest.engine` が
    `RuntimeContextView` の実装としてこの型の値を作って渡す。

    `direction` に戦略側の語彙（`TradeDirection`）を使うのは、台帳側の `OrderSide` が
    「買い建玉を決済する売り注文」のように建玉の方向と一致しない場面があるためである。
    未約定注文の一覧・状態は**含めない**（上位設計書 §4.7.12）。
    """

    position_id: PositionId
    symbol: Symbol
    direction: TradeDirection
    quantity: Quantity
    entry_price: Price
    effective_stop_loss: Price
    opened_at: ProcessingPoint
    effective_take_profit: Price | None = None

    def __post_init__(self) -> None:
        require_instance(self.position_id, PositionId, "PositionContext.position_id")
        require_instance(self.symbol, Symbol, "PositionContext.symbol")
        require_instance(self.direction, TradeDirection, "PositionContext.direction")
        require_instance(self.quantity, Quantity, "PositionContext.quantity")
        require_instance(self.entry_price, Price, "PositionContext.entry_price")
        require_instance(self.effective_stop_loss, Price, "PositionContext.effective_stop_loss")
        require_instance(self.opened_at, ProcessingPoint, "PositionContext.opened_at")
        if self.effective_take_profit is not None:
            require_instance(
                self.effective_take_profit, Price, "PositionContext.effective_take_profit"
            )


@dataclass(frozen=True, slots=True)
class AccountContext:
    """口座の時点情報（`account_context@v1`、D06 §8.4）。段階2の部品は読まない。"""

    account_id: AccountId
    currency: CurrencyCode
    balance: Money
    equity: Money
    consumed_risk: Money

    def __post_init__(self) -> None:
        require_instance(self.account_id, AccountId, "AccountContext.account_id")
        require_instance(self.currency, CurrencyCode, "AccountContext.currency")
        for value, label in (
            (self.balance, "balance"),
            (self.equity, "equity"),
            (self.consumed_risk, "consumed_risk"),
        ):
            require_instance(value, Money, f"AccountContext.{label}")
            if value.currency != self.currency:
                raise KernelValueError(
                    f"AccountContext.{label} must be in {self.currency}, got {value.currency}"
                )


@dataclass(frozen=True, slots=True)
class DataTypePayloadBinding:
    """データ型識別子と実行時クラスの対応1件（D05 §4.2）。"""

    data_type: DataTypeRef
    payload_type: type

    def __post_init__(self) -> None:
        require_instance(self.data_type, DataTypeRef, "DataTypePayloadBinding.data_type")
        if not isinstance(self.payload_type, type):
            raise KernelValueError(
                f"DataTypePayloadBinding.payload_type must be a class, got {self.payload_type!r}"
            )


#: データ型識別子と、その型の値として配送される実行時クラスの対応（D05 §4.2）。
#:
#: `position_context@v1` / `account_context@v1` は入力専用で、payload の項目は D06 が定める。
#: `declarations` は識別子と版だけを持つため、この表はここにしか置けない。
PAYLOAD_BINDINGS: Final[tuple[DataTypePayloadBinding, ...]] = (
    DataTypePayloadBinding(datatypes.PRICE_V1, Price),
    DataTypePayloadBinding(datatypes.PRICE_OFFSET_V1, PriceOffset),
    DataTypePayloadBinding(datatypes.RATIO_V1, Decimal),
    DataTypePayloadBinding(datatypes.VOLUME_V1, Decimal),
    DataTypePayloadBinding(datatypes.CONDITION_STATE_V1, ConditionState),
    DataTypePayloadBinding(datatypes.MARKET_PERMISSION_V1, MarketPermission),
    DataTypePayloadBinding(datatypes.OPPORTUNITY_V1, Opportunity),
    DataTypePayloadBinding(datatypes.CONFIRMATION_RESULT_V1, ConfirmationResult),
    DataTypePayloadBinding(datatypes.ORDER_INTENT_V1, OrderIntent),
    DataTypePayloadBinding(datatypes.PROTECTION_LEVELS_V1, ProtectionLevels),
    DataTypePayloadBinding(datatypes.MANAGEMENT_ACTION_V1, SetTakeProfit),
)

#: 部品が返す型が、配送される型と異なるデータ型（D05 §4.2）。確認結果は段階3 で足した。
_COMPONENT_RETURN_TYPES: Final[dict[DataTypeRef, type]] = {
    datatypes.OPPORTUNITY_V1: OpportunityContent,
    datatypes.CONFIRMATION_RESULT_V1: ConfirmationOutcome,
}

#: 同じデータ型を複数の実行時クラスが満たす場合の追加の許容（管理要求の2区分と、段階3 の
#: 損切り水準の更新）。
_ADDITIONAL_PAYLOAD_TYPES: Final[dict[DataTypeRef, tuple[type, ...]]] = {
    datatypes.MANAGEMENT_ACTION_V1: (SetTakeProfit, ClosePosition, UpdateStop),
}


def payload_type_for(
    data_type: DataTypeRef, *, as_component_return: bool = False
) -> tuple[type, ...]:
    """データ型に対応する実行時クラスを返す（D05 §4.2）。

    `as_component_return=True` は「部品が返す型」を問う。取引機会と確認結果だけ、部品が
    返す型（`OpportunityContent` / `ConfirmationOutcome`）と配送される型（`Opportunity` /
    `ConfirmationResult`）が異なる。

    対応の無いデータ型（入力専用の `position_context@v1` / `account_context@v1`）では
    空の組を返す。呼び出し側が「検査できない」ことを明示的に扱えるようにするためで、
    黙って通すのではない。
    """
    if as_component_return and data_type in _COMPONENT_RETURN_TYPES:
        return (_COMPONENT_RETURN_TYPES[data_type],)
    if data_type in _ADDITIONAL_PAYLOAD_TYPES:
        return _ADDITIONAL_PAYLOAD_TYPES[data_type]
    for binding in PAYLOAD_BINDINGS:
        if binding.data_type == data_type:
            return (binding.payload_type,)
    return ()
