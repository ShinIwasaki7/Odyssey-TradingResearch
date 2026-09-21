"""取引機会の有効性と同時保持の宣言（D04 §10.2・§10.3）。

## 有効性（`OpportunityValiditySpec`）

取引機会が生まれてから発注要求を作るまでの間に、「どの条件を生成時点の値に固定し
（対象区間束縛）、どの条件が成立し続けることを要求するか（現在状態束縛）」を宣言する
（ADR-0031）。暗黙の既定値は置かない。

**後続確認のない戦略でも、空の `bindings` を設定ファイルに明示的に書かせる**（Q5 決定）。
省略を許すと「書き忘れ」と「意図的に空」を区別できず、暗黙の既定値が生まれる。

束縛が指せるのは**条件の成否を表す出力**（`condition_state@v1`）だけである。条件かどうかを
型で判定できなければ、成立しなくなった機会を `MARKET_STATE_INVALIDATED` で終端する規則
（ADR-0031）を実装できない。検査はコンパイラが行う（D04 §12 #5）。

## 同時保持（`OpportunityConcurrencySpec`）

異なる発火から生まれた取引機会どうしの関係を宣言する（ADR-0032）。**発火は必ず取引機会
として記録する**。上限に達していて有効化しない場合でも、新しい識別子を持つ機会を生成
したうえで終端理由 `CONCURRENCY_LIMIT_REACHED` で終端し、判断履歴に残す。こうすると、
期限切れ・置き換え・受付起因の終了と集計上区別できる。

置換する相手の選び方（最も古い有効な機会1件）と、その適用時点は D05 §7.4 が実装する。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.missing import MissingInputPolicy, SkipEvaluation
from odyssey_fx.strategy.declarations.read_spec import require_missing_policy
from odyssey_fx.strategy.declarations.refs import OutputRef
from odyssey_fx.strategy.declarations.validation import (
    normalized_unique,
    require_instance,
    require_int,
)

__all__ = [
    "OnNewTrigger",
    "OnOrderAccepted",
    "OpportunityConcurrencySpec",
    "OpportunityValiditySpec",
    "ValidityBinding",
    "ValidityMode",
]


class ValidityMode(Enum):
    """束縛の時間的な性質（D04 §10.2）。"""

    #: 対象区間束縛。取引機会の生成時点の出力記録を固定し、以後更新しない。
    SNAPSHOT_AT_OPPORTUNITY = "SNAPSHOT_AT_OPPORTUNITY"
    #: 現在状態束縛。発注要求を作る直前まで、その時点の最新出力で再検査する。
    REQUIRE_UNTIL_ORDER_REQUEST = "REQUIRE_UNTIL_ORDER_REQUEST"


class OnNewTrigger(Enum):
    """同時保持上限に達している状態で新しい発火があったときの扱い（D04 §10.3）。"""

    #: 既存を残す。新しい発火は記録したうえで有効にせず終端する。
    KEEP_EXISTING = "KEEP_EXISTING"
    #: 最も古い既存を `SUPERSEDED` で終端し、新しい機会を有効にする。
    SUPERSEDE_EXISTING = "SUPERSEDE_EXISTING"


class OnOrderAccepted(Enum):
    """ある注文が受け付けられたときの、他の取引機会の扱い（D04 §10.3）。"""

    #: 他の機会をそのまま残す。
    KEEP_OTHERS = "KEEP_OTHERS"
    #: 他の機会を `CLOSED_BY_ORDER_ACCEPTANCE` で終端する。
    CLOSE_OTHERS = "CLOSE_OTHERS"


@dataclass(frozen=True, slots=True)
class ValidityBinding:
    """取引機会が参照し続ける条件1件（D04 §10.2）。"""

    source: OutputRef
    mode: ValidityMode
    on_missing: MissingInputPolicy = SkipEvaluation()

    def __post_init__(self) -> None:
        require_instance(self.source, OutputRef, "ValidityBinding.source")
        require_instance(self.mode, ValidityMode, "ValidityBinding.mode")
        require_missing_policy(self.on_missing, "ValidityBinding.on_missing")


@dataclass(frozen=True, slots=True)
class OpportunityValiditySpec:
    """取引機会の有効性の宣言（D04 §10.2）。空の `bindings` も明示する。"""

    bindings: tuple[ValidityBinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, tuple):
            raise KernelValueError("OpportunityValiditySpec.bindings must be a tuple")
        for index, item in enumerate(self.bindings):
            if not isinstance(item, ValidityBinding):
                raise KernelValueError(
                    f"OpportunityValiditySpec.bindings[{index}] must be a ValidityBinding,"
                    f" got {item!r}"
                )
        # 接続元ごとに1件（D04 §10.2）。同じ出力に別々の `mode` を書けると、同じ条件が
        # 二通りに分類されるうえ、整列鍵でも並びが一意に定まらない。
        object.__setattr__(
            self,
            "bindings",
            normalized_unique(
                self.bindings,
                key=lambda item: (item.source.instance_id, item.source.output_name),
                label="OpportunityValiditySpec.bindings",
            ),
        )


@dataclass(frozen=True, slots=True)
class OpportunityConcurrencySpec:
    """取引機会の同時保持の宣言（D04 §10.3、Q6 決定）。"""

    max_active: int
    on_new_trigger: OnNewTrigger
    on_order_accepted: OnOrderAccepted

    def __post_init__(self) -> None:
        max_active = require_int(self.max_active, "OpportunityConcurrencySpec.max_active")
        if max_active < 1:
            raise KernelValueError(
                f"OpportunityConcurrencySpec.max_active must be >= 1, got {max_active}"
            )
        require_instance(
            self.on_new_trigger, OnNewTrigger, "OpportunityConcurrencySpec.on_new_trigger"
        )
        require_instance(
            self.on_order_accepted, OnOrderAccepted, "OpportunityConcurrencySpec.on_order_accepted"
        )
