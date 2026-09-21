"""部品の内部状態の宣言（D04 §9.1、Q3 決定）。

**役割を問わず、どの部品も内部状態を持てる**（Q3 決定、選択肢2）。型と契約を役割で制限
しないので、後から再帰計算の部品（EMA など）を契約を作り直さずに足せる。段階2で実際に
状態を使うのは、取引機会を検出する部品の再武装（D04 §10.4 の `EDGE`）だけである。

状態はランタイムが使用箇所ごとに保持し、部品実装オブジェクトは可変状態を持たない
（ADR-0008）。初期値は宣言に書き切り、実装側の既定値に委ねない。これにより同じ宣言から
同じ初期状態になり、「同一入力の再実行で判断履歴が一致する」（全体計画 §8.2）が実装に
依存しなくなる。

リセットは「`initial` と同じ値へ戻すこと」と定義し、「リセット時だけ別の値」を持たせない。
段階2の契機は run 開始時（`RUN_START`）の1つだけである。

ウォームアップ中に有効な出力を出せない状態は `TemporalConstraints.warmup` で宣言し、
ここには持たせない（同じ規則を2か所に置かないため）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.datatypes import DataTypeRef, require_registered
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.specs import ParameterValue, require_parameter_value
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    normalized_unique,
    require_identifier,
)

__all__ = ["LiteralInitialState", "ResetTrigger", "StateInitializer", "StateSpec"]


class ResetTrigger(Enum):
    """状態を初期値へ戻す契機（D04 §9.1）。初版は run 開始時のみ。"""

    RUN_START = "RUN_START"


@dataclass(frozen=True, slots=True)
class LiteralInitialState:
    """初期値を宣言に書き切る（D04 §9.1）。初版はこの1区分のみ。"""

    values: Mapping[str, ParameterValue]
    kind: str = "LITERAL"

    def __post_init__(self) -> None:
        require_kind(self.kind, "LITERAL", "LiteralInitialState.kind")
        if not isinstance(self.values, Mapping):
            raise KernelValueError("LiteralInitialState.values must be a Mapping")
        for name, value in self.values.items():
            require_identifier(name, "LiteralInitialState.values key")
            require_parameter_value(value, f"LiteralInitialState.values[{name!r}]")
        object.__setattr__(self, "values", freeze_mapping(dict(self.values)))


#: 区分タグ付き union（D04 §9.1）。初版は1区分。
StateInitializer = LiteralInitialState


@dataclass(frozen=True, slots=True)
class StateSpec:
    """内部状態の型・初期値・リセット契機（D04 §9.1）。

    契約と実装の状態型を二重定義せず、`catalog` の登録時に実装の状態型と `state_type` の
    一致を検査する（D05 §4.1 の登録時検査 (b)(c)）。保存・復元の方法は D05 §6.5。
    """

    state_type: DataTypeRef
    initial: StateInitializer
    reset_on: tuple[ResetTrigger, ...] = (ResetTrigger.RUN_START,)

    def __post_init__(self) -> None:
        require_registered(self.state_type, "StateSpec.state_type")
        if not isinstance(self.initial, LiteralInitialState):
            raise KernelValueError(
                f"StateSpec.initial must be a LiteralInitialState, got {self.initial!r}"
            )
        if not isinstance(self.reset_on, tuple):
            raise KernelValueError("StateSpec.reset_on must be a tuple")
        for item in self.reset_on:
            if not isinstance(item, ResetTrigger):
                raise KernelValueError(
                    f"StateSpec.reset_on must contain ResetTrigger, got {item!r}"
                )
        object.__setattr__(
            self,
            "reset_on",
            normalized_unique(
                self.reset_on, key=lambda item: item.value, label="StateSpec.reset_on"
            ),
        )
