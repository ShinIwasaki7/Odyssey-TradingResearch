"""データ型レジストリ（D04 §5）。

接続の両端が「同じ種類の値」をやり取りしているかを、Python のクラス名を動的に読み込まずに
判定するための仕組みである。登録済みの `(type_id, version)` だけを宣言に書けるようにし、
接続検証（D04 §12 #2）はこの組の一致で行う。

**レジストリが持つのは識別子と版だけ**である。実行時クラス（`records` 側の内容型）との
対応は `declarations` に置かない。`declarations` は `strategy` の最下層で `records` を参照
できず（D01 §3.3）、クラスを直接持てば禁止された上向き import になり、クラス名の文字列で
持てば検査できない対応表になるためである。対応表の正本は `records`、登録時の照合は
`catalog` にあり、D05 §4.2 が確定している。

単位（pips / 価格 / 比率）はデータ型ではなく `ParameterSpec.unit` が持つ（D04 §7）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.refs import MarketDataField, RuntimeTarget
from odyssey_fx.strategy.declarations.validation import require_identifier, require_version

__all__ = [
    "ACCOUNT_CONTEXT_V1",
    "CONDITION_STATE_V1",
    "CONFIRMATION_RESULT_V1",
    "DATA_TYPE_REGISTRY",
    "MANAGEMENT_ACTION_V1",
    "MARKET_DATA_FIELD_TYPES",
    "MARKET_PERMISSION_V1",
    "OPPORTUNITY_V1",
    "ORDER_INTENT_V1",
    "POSITION_CONTEXT_V1",
    "PRICE_OFFSET_V1",
    "PRICE_V1",
    "PROTECTION_LEVELS_V1",
    "RATIO_V1",
    "RUNTIME_TARGET_TYPES",
    "VOLUME_V1",
    "DataTypeRef",
    "is_registered",
    "require_registered",
]


@dataclass(frozen=True, slots=True)
class DataTypeRef:
    """データ型の識別子と版（D04 §5）。

    `__str__` は `"<type_id>@v<version>"`（D02 §6 の `TimeframeRef` に合わせる）。
    """

    type_id: str
    version: int

    def __post_init__(self) -> None:
        require_identifier(self.type_id, "DataTypeRef.type_id")
        require_version(self.version, "DataTypeRef.version")

    def __str__(self) -> str:
        return f"{self.type_id}@v{self.version}"

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3 の `CanonicalScalar`）。"""
        return str(self)


PRICE_V1: Final = DataTypeRef("price", 1)
PRICE_OFFSET_V1: Final = DataTypeRef("price_offset", 1)
RATIO_V1: Final = DataTypeRef("ratio", 1)
CONDITION_STATE_V1: Final = DataTypeRef("condition_state", 1)
MARKET_PERMISSION_V1: Final = DataTypeRef("market_permission", 1)
OPPORTUNITY_V1: Final = DataTypeRef("opportunity", 1)
CONFIRMATION_RESULT_V1: Final = DataTypeRef("confirmation_result", 1)
ORDER_INTENT_V1: Final = DataTypeRef("order_intent", 1)
PROTECTION_LEVELS_V1: Final = DataTypeRef("protection_levels", 1)
MANAGEMENT_ACTION_V1: Final = DataTypeRef("management_action", 1)
POSITION_CONTEXT_V1: Final = DataTypeRef("position_context", 1)
ACCOUNT_CONTEXT_V1: Final = DataTypeRef("account_context", 1)
VOLUME_V1: Final = DataTypeRef("volume", 1)

#: 初版の登録（D04 §5）。静的テーブルであり、実行時の登録 API は持たない。
DATA_TYPE_REGISTRY: Final[frozenset[DataTypeRef]] = frozenset(
    {
        PRICE_V1,
        PRICE_OFFSET_V1,
        RATIO_V1,
        CONDITION_STATE_V1,
        MARKET_PERMISSION_V1,
        OPPORTUNITY_V1,
        CONFIRMATION_RESULT_V1,
        ORDER_INTENT_V1,
        PROTECTION_LEVELS_V1,
        MANAGEMENT_ACTION_V1,
        POSITION_CONTEXT_V1,
        ACCOUNT_CONTEXT_V1,
        VOLUME_V1,
    }
)

#: 市場データの項目とデータ型の対応（D04 §5）。
#:
#: 市場データ参照は `OutputSpec` を持たないため、接続元の型を項目から決める。出来高を
#: 独立した型にするのは、出来高を価格として扱う接続をコンパイル時に拒否するためである。
MARKET_DATA_FIELD_TYPES: Final[dict[MarketDataField, DataTypeRef]] = {
    MarketDataField.OPEN: PRICE_V1,
    MarketDataField.HIGH: PRICE_V1,
    MarketDataField.LOW: PRICE_V1,
    MarketDataField.CLOSE: PRICE_V1,
    MarketDataField.VOLUME: VOLUME_V1,
}

#: 建玉・口座を読む入力の対象とデータ型の対応（D04 §5）。
#:
#: payload に何の項目が入るかは「エンジンが評価時点に供給してよい情報の範囲」そのもの
#: であるため D06 が確定し、`declarations` は識別子と版だけを持つ。
RUNTIME_TARGET_TYPES: Final[dict[RuntimeTarget, DataTypeRef]] = {
    RuntimeTarget.POSITION: POSITION_CONTEXT_V1,
    RuntimeTarget.ACCOUNT: ACCOUNT_CONTEXT_V1,
}


def is_registered(data_type: DataTypeRef) -> bool:
    """登録済みのデータ型かどうか（D04 §5）。"""
    return data_type in DATA_TYPE_REGISTRY


def require_registered(data_type: DataTypeRef, label: str) -> DataTypeRef:
    """登録済みのデータ型であることを要求する（D04 §5）。"""
    if not isinstance(data_type, DataTypeRef):
        raise KernelValueError(f"{label} must be a DataTypeRef, got {data_type!r}")
    if not is_registered(data_type):
        raise KernelValueError(f"{label} refers to an unregistered data type: {data_type}")
    return data_type
