"""入力の参照元（D04 §4.3）。

入力が「どこから値を取るか」は3種類しかない。

| 区分 | 何を指すか |
|---|---|
| `OutputRef` | 同じ戦略の別の使用箇所の出力 |
| `MarketDataRef` | 市場データの系列と項目（始値・高値・安値・終値・出来高） |
| `RuntimeInputRef` | 評価時点の現在の建玉・口座・取引機会 |

`RuntimeInputRef` は「共有された可変台帳への直接アクセス」ではなく、**エンジンが許可した
時点情報**である（上位設計書 §4.3.5）。対象は建玉・口座・取引機会の3つで、取引機会は
D04 v1.9 で足した（供給元はエンジンではなく戦略ランタイム自身。D05 §6.11）。
`PENDING_ORDER`（未約定注文）は列挙に含めない。

**区分タグ `kind` は dataclass のフィールドとして持つ**（D01 §8）。正規化エンコード
（D02 §9.3）は dataclass を「フィールド名をキーとする mapping」に落とし、型名を含めない。
タグをフィールドに持たないと、フィールド構成が同じ2つの区分が同じバイト列になり、別の
宣言が同じ内容ハッシュを持ってしまう。既定値を与えてあるので、使う側はタグを書かない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.declarations.validation import require_identifier, require_instance

__all__ = [
    "InputSourceRef",
    "MarketDataField",
    "MarketDataRef",
    "OutputRef",
    "RuntimeInputRef",
    "RuntimeTarget",
    "require_kind",
]


def require_kind(value: object, expected: str, label: str) -> str:
    """区分タグが規定の値であることを要求する。"""
    if value != expected:
        raise KernelValueError(f"{label} must be {expected!r}, got {value!r}")
    return expected


class MarketDataField(Enum):
    """足のどの項目を読むか（D04 §4.3）。

    `OPEN` の指定は未確定足への参照権を意味しない。読めるのは確定足だけであり、
    未来参照は as-of ビュー（D03 §6.2）が構造的に防ぐ。
    """

    OPEN = "OPEN"
    HIGH = "HIGH"
    LOW = "LOW"
    CLOSE = "CLOSE"
    VOLUME = "VOLUME"


class RuntimeTarget(Enum):
    """エンジンが供給する現在コンテキストの対象（D04 §4.3）。

    初版は建玉（`POSITION`）と口座（`ACCOUNT`）の2つで、**取引機会（`OPPORTUNITY`）を
    D04 v1.9 で足した**（Q14 決定、選択肢1）。後続確認の部品が「いまどの機会について評価して
    いるか」を読むためのもので、供給元は戦略ランタイム自身である（D05 §6.11）。未約定注文
    （`PENDING_ORDER`）は**列挙に含めない**。列挙に置いてから能力検査で拒否する形にすると、
    供給しない情報を宣言でき、設定ファイルが通る範囲が型から読めなくなる。
    """

    POSITION = "POSITION"
    ACCOUNT = "ACCOUNT"
    OPPORTUNITY = "OPPORTUNITY"


@dataclass(frozen=True, slots=True)
class OutputRef:
    """同じ戦略の別の使用箇所の出力への参照（D04 §4.3、上位設計書 §4.3.5）。"""

    instance_id: str
    output_name: str
    kind: str = "OUTPUT"

    def __post_init__(self) -> None:
        require_identifier(self.instance_id, "OutputRef.instance_id")
        require_identifier(self.output_name, "OutputRef.output_name")
        require_kind(self.kind, "OUTPUT", "OutputRef.kind")

    def __str__(self) -> str:
        return f"{self.instance_id}.{self.output_name}"


@dataclass(frozen=True, slots=True)
class MarketDataRef:
    """市場データの系列と項目への参照（D04 §4.3）。"""

    series: SeriesId
    field: MarketDataField
    kind: str = "MARKET_DATA"

    def __post_init__(self) -> None:
        require_instance(self.series, SeriesId, "MarketDataRef.series")
        require_instance(self.field, MarketDataField, "MarketDataRef.field")
        require_kind(self.kind, "MARKET_DATA", "MarketDataRef.kind")

    def __str__(self) -> str:
        return f"{self.series}:{self.field.value}"


@dataclass(frozen=True, slots=True)
class RuntimeInputRef:
    """エンジンが供給する現在コンテキストへの参照（D04 §4.3）。"""

    target: RuntimeTarget
    kind: str = "RUNTIME_INPUT"

    def __post_init__(self) -> None:
        require_instance(self.target, RuntimeTarget, "RuntimeInputRef.target")
        require_kind(self.kind, "RUNTIME_INPUT", "RuntimeInputRef.kind")

    def __str__(self) -> str:
        return f"runtime:{self.target.value}"


#: 区分タグ付き union（D04 §4.3、D01 §8）。
InputSourceRef = OutputRef | MarketDataRef | RuntimeInputRef
