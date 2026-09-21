"""部品の登録（D05 §4.1）。

部品は「契約（宣言）＋実装＋実装参照」を1組として登録する。レジストリは静的テーブルで、
**実行時の登録 API を持たない**（D04 §5 のデータ型レジストリと同じ扱い）。実行中に品揃えが
変わると、同じ設定ファイルが実行ごとに別の意味を持ちうるためである。

実装は2形式だけ（ADR-0008・全体計画 §5.3.3）。

| 区分 | 呼び出し形 |
|---|---|
| `StatelessImplementation` | `evaluate(inputs, parameters) -> ComponentOutputs` |
| `StatefulImplementation` | `evaluate(inputs, parameters, state) -> ComponentOutputs` |

実装は純粋関数であり、実時計・乱数・環境変数・I/O・グローバル可変状態を読まない。実装
オブジェクトは可変状態を持たず、状態はランタイムが使用箇所ごとに保持する（D05 §6.5）。

**出さなかった出力は「今回の評価では発生しなかった」を意味する**。`None` を出力値として
入れない。取引機会を出す部品が発火しない評価がこれに当たる。

登録時の検査（D05 §4.1）:

1. 契約が未登録のデータ型を使っていないこと。
2. 状態を宣言した契約の実装は状態付きで、その状態型が契約と一致すること。
3. 状態を宣言しない契約の実装は状態なしであること。
4. **登録の実装参照が契約の実装参照と一致すること**。静的テーブルで取り違えると、契約が
   指す実装と実際に呼ぶ関数、さらに解決済み設定の指紋（D05 §5.5）に入る実装の同一性が
   食い違い、再現性の識別が壊れる。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from odyssey_fx.common import canonical
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import ImplementationRef
from odyssey_fx.strategy.catalog.inputs import ResolvedInputsView, ResolvedParameterView
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.datatypes import DataTypeRef, is_registered
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.validation import (
    freeze_mapping,
    require_identifier,
    require_instance,
    require_version,
)
from odyssey_fx.strategy.records.payloads import payload_type_for

__all__ = [
    "ComponentImplementation",
    "ComponentOutputs",
    "ComponentRegistration",
    "ComponentRegistry",
    "ContractKey",
    "StatefulImplementation",
    "StatelessImplementation",
    "build_registry",
    "declared_implementation_ref",
]


def declared_implementation_ref(implementation_id: str, revision: int) -> ImplementationRef:
    """実装の同一性を表す参照を作る（D02 §9.2、D05 §5.5 の5）。

    対象は**宣言した実装識別子と改訂番号の2項目だけ**である。実装のソース内容を読まないのは、
    `strategy` に I/O を置けない（D01 §5）ためであり、run 全体のソース内容は `CodeDigest`
    （D02 §9.4）が別に識別している。この指紋の役目は、解決済み設定の指紋（D05 §5.5 の3）に
    部品単位の改訂を映すことに限られる。

    したがって **`revision` は計算規則を変えたら必ず上げる**という約束と対になる。上げ忘れる
    と、規則が変わったのに解決済み設定の指紋が同じままになる。
    """
    return ImplementationRef(
        implementation_id=implementation_id,
        digest=canonical.digest({"implementation_id": implementation_id, "revision": revision}),
    )


#: 状態なしの実装の呼び出し形（D05 §4.1）。
StatelessEvaluate = Callable[
    [ResolvedInputsView, Mapping[str, ResolvedParameterView]], "ComponentOutputs"
]

#: 状態ありの実装の呼び出し形（D05 §4.1）。
StatefulEvaluate = Callable[
    [ResolvedInputsView, Mapping[str, ResolvedParameterView], object], "ComponentOutputs"
]


@dataclass(frozen=True, slots=True)
class ComponentOutputs:
    """1回の評価の結果（D05 §4.1）。

    `outputs` のキー集合は契約の `outputs` のキー集合の**部分集合**でなければならない。
    検査はランタイムが付番の前に行う（D05 §6.2）。
    """

    outputs: Mapping[str, object]
    new_state: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outputs, Mapping):
            raise KernelValueError("ComponentOutputs.outputs must be a Mapping")
        for name, value in self.outputs.items():
            require_identifier(name, "ComponentOutputs.outputs key")
            if value is None:
                raise KernelValueError(
                    f"ComponentOutputs.outputs[{name!r}] must not be None; omit the key instead"
                    " (D05 §4.1)"
                )
        object.__setattr__(self, "outputs", freeze_mapping(dict(self.outputs)))


@dataclass(frozen=True, slots=True)
class StatelessImplementation:
    """状態を持たない実装（D05 §4.1）。"""

    evaluate: StatelessEvaluate
    kind: str = "STATELESS"

    def __post_init__(self) -> None:
        require_kind(self.kind, "STATELESS", "StatelessImplementation.kind")
        if not callable(self.evaluate):
            raise KernelValueError("StatelessImplementation.evaluate must be callable")


@dataclass(frozen=True, slots=True)
class StatefulImplementation:
    """状態を持つ実装（D05 §4.1）。状態はランタイムが渡し、実装は保持しない。"""

    evaluate: StatefulEvaluate
    state_type: DataTypeRef
    kind: str = "STATEFUL"

    def __post_init__(self) -> None:
        require_kind(self.kind, "STATEFUL", "StatefulImplementation.kind")
        if not callable(self.evaluate):
            raise KernelValueError("StatefulImplementation.evaluate must be callable")
        require_instance(self.state_type, DataTypeRef, "StatefulImplementation.state_type")


#: 区分タグ付き union（D05 §4.1）。
ComponentImplementation = StatelessImplementation | StatefulImplementation


@dataclass(frozen=True, slots=True)
class ContractKey:
    """レジストリの鍵（D05 §4.1）。部品 ID と版の組。"""

    component_id: str
    version: int

    def __post_init__(self) -> None:
        require_identifier(self.component_id, "ContractKey.component_id")
        require_version(self.version, "ContractKey.version")

    def __str__(self) -> str:
        return f"{self.component_id}@v{self.version}"


@dataclass(frozen=True, slots=True)
class ComponentRegistration:
    """契約・実装・実装参照の1組（D05 §4.1）。"""

    contract: ComponentContract
    implementation: ComponentImplementation
    implementation_ref: ImplementationRef

    def __post_init__(self) -> None:
        require_instance(self.contract, ComponentContract, "ComponentRegistration.contract")
        if not isinstance(self.implementation, (StatelessImplementation, StatefulImplementation)):
            raise KernelValueError(
                "ComponentRegistration.implementation must be a ComponentImplementation,"
                f" got {self.implementation!r}"
            )
        require_instance(
            self.implementation_ref,
            ImplementationRef,
            "ComponentRegistration.implementation_ref",
        )
        self._require_registered_data_types()
        self._require_state_agreement()
        self._require_matching_implementation_ref()

    @property
    def key(self) -> ContractKey:
        """レジストリの鍵。"""
        return ContractKey(self.contract.component_id, self.contract.version)

    def _require_registered_data_types(self) -> None:
        """(a) 契約が未登録のデータ型を使っていないこと（D05 §4.1）。

        宣言型の構築時にも登録済みかどうかは見ているが、ここでもう一度見るのは、
        **実行時クラスとの対応表（D05 §4.2）が引けること**まで含めて登録時に確かめるため
        である。入力専用のデータ型（建玉・口座）は対応表を持たないので、出力側だけを見る。
        """
        for name, spec in self.contract.outputs.items():
            if not is_registered(spec.data_type):
                raise KernelValueError(
                    f"{self.key}: output {name!r} uses an unregistered data type {spec.data_type}"
                )
            if not payload_type_for(spec.data_type, as_component_return=True):
                raise KernelValueError(
                    f"{self.key}: output {name!r} uses data type {spec.data_type}, which has no"
                    " runtime payload type (D05 §4.2)"
                )
        for name, input_spec in self.contract.inputs.items():
            if not is_registered(input_spec.data_type):
                raise KernelValueError(
                    f"{self.key}: input {name!r} uses an unregistered data type"
                    f" {input_spec.data_type}"
                )

    def _require_state_agreement(self) -> None:
        """(b)(c) 契約と実装の状態の取り扱いが一致すること（D05 §4.1・D04 §9.1）。"""
        state_spec = self.contract.state_spec
        if state_spec is None:
            if isinstance(self.implementation, StatefulImplementation):
                raise KernelValueError(
                    f"{self.key}: the contract declares no state, so its implementation must be"
                    " stateless"
                )
            return
        if not isinstance(self.implementation, StatefulImplementation):
            raise KernelValueError(
                f"{self.key}: the contract declares state, so its implementation must be stateful"
            )
        if self.implementation.state_type != state_spec.state_type:
            raise KernelValueError(
                f"{self.key}: the implementation state type ({self.implementation.state_type})"
                f" must match the contract state type ({state_spec.state_type})"
            )

    def _require_matching_implementation_ref(self) -> None:
        """(d) 登録と契約の実装参照が一致すること（D05 §4.1）。"""
        if self.implementation_ref != self.contract.implementation_ref:
            raise KernelValueError(
                f"{self.key}: the registered implementation_ref ({self.implementation_ref})"
                f" must match the contract's ({self.contract.implementation_ref})"
            )


@dataclass(frozen=True, slots=True)
class ComponentRegistry:
    """静的な部品テーブル（D05 §4.1）。"""

    registrations: Mapping[ContractKey, ComponentRegistration]

    def __post_init__(self) -> None:
        if not isinstance(self.registrations, Mapping):
            raise KernelValueError("ComponentRegistry.registrations must be a Mapping")
        for key, registration in self.registrations.items():
            require_instance(key, ContractKey, "ComponentRegistry.registrations key")
            require_instance(
                registration, ComponentRegistration, f"ComponentRegistry.registrations[{key}]"
            )
            if registration.key != key:
                raise KernelValueError(
                    f"ComponentRegistry.registrations[{key}] is registered under the wrong key"
                    f" (its contract is {registration.key})"
                )
        object.__setattr__(self, "registrations", MappingProxyType(dict(self.registrations)))

    def get(self, key: ContractKey) -> ComponentRegistration | None:
        """登録を引く。未登録は `None`（拒否はコンパイラが理由付きで行う。D05 §5.2）。"""
        return self.registrations.get(key)

    def __contains__(self, key: ContractKey) -> bool:
        return key in self.registrations


def build_registry(registrations: Iterable[ComponentRegistration]) -> ComponentRegistry:
    """登録の列からレジストリを作る。同じ鍵が2度現れたら拒否する。"""
    table: dict[ContractKey, ComponentRegistration] = {}
    for registration in registrations:
        key = registration.key
        if key in table:
            raise KernelValueError(f"component {key} is registered more than once")
        table[key] = registration
    return ComponentRegistry(table)
