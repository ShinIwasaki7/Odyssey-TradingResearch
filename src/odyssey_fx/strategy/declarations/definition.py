"""戦略定義 `StrategyDefinition`（D04 §3、上位設計書 §4.3.5）。

部品の使用箇所をつなぎ、どの出力がどの役割を担うかを**名前付きフィールド**で指定する。
使用箇所の並び順で実行順序を指定しない（順序は依存グラフから決まる。D05 §5.4）。

役割フィールドの型要求（`trigger` は取引機会、`order` は注文意図、など）と、
`execution_filter` の有無と `entry_policy` の整合は `compiler` が検査する（D04 §12 #5）。

**`components` は `instance_id` 順に正規化する**（D04 §3 の表）。意味の同じ2つの設定
ファイルが、書いた順序の違いだけで別の内容ハッシュになると、実験の同一性とキャッシュが
分かれてしまうためである。
"""

from __future__ import annotations

from dataclasses import dataclass

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.contract import SCHEMA_VERSION
from odyssey_fx.strategy.declarations.entry_policy import (
    AwaitConfirmation,
    EntryPolicy,
    ImmediateEntry,
)
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.opportunity import (
    OpportunityConcurrencySpec,
    OpportunityValiditySpec,
)
from odyssey_fx.strategy.declarations.refs import OutputRef
from odyssey_fx.strategy.declarations.validation import (
    normalized_unique,
    require_identifier,
    require_instance,
    require_int,
    require_tuple_of,
    require_version,
)

__all__ = ["StrategyDefinition"]


def _require_output_ref(value: object, label: str, *, optional: bool) -> OutputRef | None:
    if value is None:
        if optional:
            return None
        raise KernelValueError(f"{label} is required")
    return require_instance(value, OutputRef, label)


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    """部品をつないだ戦略全体（上位設計書 §4.3.5 の12フィールド ＋ `schema_version`）。"""

    strategy_id: str
    version: int
    components: tuple[ComponentInstance, ...]
    market_state: OutputRef | None
    trigger: OutputRef
    execution_filter: OutputRef | None
    order: OutputRef
    protection: OutputRef
    exit: OutputRef
    entry_policy: EntryPolicy
    opportunity_validity: OpportunityValiditySpec
    opportunity_concurrency: OpportunityConcurrencySpec
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.strategy_id, "StrategyDefinition.strategy_id")
        require_version(self.version, "StrategyDefinition.version")
        require_tuple_of(self.components, ComponentInstance, "StrategyDefinition.components")
        if not self.components:
            raise KernelValueError("StrategyDefinition.components must contain at least one entry")
        object.__setattr__(
            self,
            "components",
            normalized_unique(
                self.components,
                key=lambda item: item.instance_id,
                label="StrategyDefinition.components",
            ),
        )

        _require_output_ref(self.market_state, "StrategyDefinition.market_state", optional=True)
        _require_output_ref(self.trigger, "StrategyDefinition.trigger", optional=False)
        _require_output_ref(
            self.execution_filter, "StrategyDefinition.execution_filter", optional=True
        )
        _require_output_ref(self.order, "StrategyDefinition.order", optional=False)
        _require_output_ref(self.protection, "StrategyDefinition.protection", optional=False)
        _require_output_ref(self.exit, "StrategyDefinition.exit", optional=False)

        if not isinstance(self.entry_policy, (ImmediateEntry, AwaitConfirmation)):
            raise KernelValueError(
                f"StrategyDefinition.entry_policy must be an EntryPolicy, got {self.entry_policy!r}"
            )
        require_instance(
            self.opportunity_validity,
            OpportunityValiditySpec,
            "StrategyDefinition.opportunity_validity",
        )
        require_instance(
            self.opportunity_concurrency,
            OpportunityConcurrencySpec,
            "StrategyDefinition.opportunity_concurrency",
        )
        version = require_int(self.schema_version, "StrategyDefinition.schema_version")
        if version < 1:
            raise KernelValueError(f"StrategyDefinition.schema_version must be >= 1, got {version}")

    def component(self, instance_id: str) -> ComponentInstance | None:
        """使用箇所を ID で引く。見つからなければ `None`（拒否はコンパイラが行う）。"""
        for item in self.components:
            if item.instance_id == instance_id:
                return item
        return None
