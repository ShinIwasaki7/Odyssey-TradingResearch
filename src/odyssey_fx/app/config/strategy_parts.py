"""戦略宣言の部品を読む共通部分（D04 §13.1、D07 §18.4）。

実験設定の書式 v1 の `strategy:` 節（`experiment.py`）と、書式 v2 が指す戦略ファイル
（`strategy_file.py`）は、**使用箇所・入力の参照元・起動条件・パラメータ・役割・同時保持の
書き方が同じ**である（D07 §18.4 は v1 の略記をそのまま引き継ぐと定めている）。同じ規則を
2か所に書くと受理範囲がずれるので、両者が使う部分をここに1つだけ置く。

**Pydantic モデルはこの層（`app.config`）の外へ出さない**（D01 §10.1）。外へ出るのは
`strategy.declarations` の frozen dataclass だけである。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.models import StrictModel
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import ContractRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.registry import ComponentRegistry, ContractKey
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.evaluation import (
    EvaluationSchedule,
    EvaluationTrigger,
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
)
from odyssey_fx.strategy.declarations.refs import (
    InputSourceRef,
    MarketDataField,
    MarketDataRef,
    OutputRef,
    RuntimeInputRef,
    RuntimeTarget,
)
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    FloatValue,
    InputBinding,
    IntValue,
    ParameterValue,
    StrValue,
)

__all__ = [
    "ComponentModel",
    "ConcurrencyModel",
    "RolesModel",
    "component_of",
    "concurrency_of",
    "output_ref_of",
    "parse_series",
    "required_output_ref",
]


def parse_series(text: str, timeframe_defs: Mapping[str, TimeframeDefinition]) -> SeriesId:
    """`USDJPY/1h/bid` を系列へ読む（D03 §3.1）。

    系列の文字列は時間足の**版を持たない**（同節）。版は時間足定義の読込結果から取る。
    設定に版を書かせると、時間足定義の版を上げたときに実験設定も直す必要があり、記録した
    系列と実際に使った定義が食い違う余地が残る。
    """
    parts = text.split("/")
    if len(parts) != 3:
        raise ConfigError(
            f"系列は `<銘柄>/<時間足>/<価格基準>` の形で書くこと（{text!r} が与えられた）"
        )
    symbol, timeframe_id, basis = parts
    definition = timeframe_defs.get(timeframe_id)
    if definition is None:
        raise ConfigError(
            f"系列 {text!r} の時間足 {timeframe_id!r} の定義が設定に無い"
            f"（設定にあるのは {sorted(timeframe_defs)}）"
        )
    try:
        return SeriesId(symbol=Symbol(symbol), timeframe=definition.ref, basis=PriceBasis(basis))
    except (KernelValueError, ValueError) as exc:
        raise ConfigError(f"系列 {text!r} を読めない: {exc}") from exc


# --- 設定ファイルの形（Pydantic）--------------------------------------------


class ParameterModel(StrictModel):
    type: Literal["BOOL", "INT", "FLOAT", "STR"]
    value: bool | int | float | str


class TriggerModel(StrictModel):
    name: str
    #: 何で起動するか。`on` という名前は使えない（YAML 1.1 は `on` を真偽値として読む）。
    when: Literal["bar_close", "input_event", "runtime_event"]
    series: str | None = None
    input_name: str | None = None
    event: Literal["POSITION_OPENED"] | None = None


class ComponentModel(StrictModel):
    instance_id: str
    component: str
    component_version: int
    inputs: dict[str, list[str]] = Field(default_factory=dict)
    parameters: dict[str, ParameterModel] = Field(default_factory=dict)
    triggers: list[TriggerModel]


class RolesModel(StrictModel):
    trigger: str
    order: str
    protection: str
    exit: str
    market_state: str | None = None
    execution_filter: str | None = None


class ConcurrencyModel(StrictModel):
    max_active: int
    on_new_trigger: Literal["KEEP_EXISTING", "SUPERSEDE_EXISTING"]
    on_order_accepted: Literal["KEEP_OTHERS", "CLOSE_OTHERS"]


# --- 宣言型への変換 ---------------------------------------------------------


def _parameter_of(name: str, model: ParameterModel) -> ParameterValue:
    """パラメータ値1件（上位設計書 §4.3.5 の4区分）。

    宣言した型と値の型が食い違う設定は拒否する。`FLOAT` に整数リテラルを書いた場合だけは
    受けて浮動小数にする（`2` と `2.0` を書き分けさせる意味がない）。
    """
    value = model.value
    label = f"parameters[{name!r}]"
    if model.type == "BOOL":
        if not isinstance(value, bool):
            raise ConfigError(f"{label}: BOOL には真偽値を書くこと（{value!r}）")
        return BoolValue(value)
    if model.type == "INT":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{label}: INT には整数を書くこと（{value!r}）")
        return IntValue(value)
    if model.type == "FLOAT":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{label}: FLOAT には数値を書くこと（{value!r}）")
        return FloatValue(float(value))
    if not isinstance(value, str):
        raise ConfigError(f"{label}: STR には文字列を書くこと（{value!r}）")
    return StrValue(value)


def _source_of(text: str, timeframe_defs: Mapping[str, TimeframeDefinition]) -> InputSourceRef:
    """入力の参照元1件（D04 §4.3 の3区分）。

    書き方は参照元の `__str__` と同じ形にする。`market:<系列>:<項目>` /
    `output:<使用箇所>.<出力名>` / `runtime:<対象>` の3通りだけを受ける。
    """
    if text.startswith("market:"):
        body = text[len("market:") :]
        series_text, separator, field = body.rpartition(":")
        if not separator:
            raise ConfigError(f"市場データの参照は `market:<系列>:<項目>` と書くこと（{text!r}）")
        try:
            return MarketDataRef(
                series=parse_series(series_text, timeframe_defs),
                field=MarketDataField(field),
            )
        except ValueError as exc:
            raise ConfigError(f"市場データの参照 {text!r} を読めない: {exc}") from exc
    if text.startswith("output:"):
        body = text[len("output:") :]
        instance_id, separator, output_name = body.partition(".")
        if not separator:
            raise ConfigError(f"出力の参照は `output:<使用箇所>.<出力名>` と書くこと（{text!r}）")
        try:
            return OutputRef(instance_id=instance_id, output_name=output_name)
        except KernelValueError as exc:
            raise ConfigError(f"出力の参照 {text!r} を読めない: {exc}") from exc
    if text.startswith("runtime:"):
        try:
            return RuntimeInputRef(target=RuntimeTarget(text[len("runtime:") :]))
        except ValueError as exc:
            raise ConfigError(f"実行時入力の参照 {text!r} を読めない: {exc}") from exc
    raise ConfigError(
        f"入力の参照元は `market:` / `output:` / `runtime:` のいずれかで書くこと（{text!r}）"
    )


def _trigger_of(
    model: TriggerModel, timeframe_defs: Mapping[str, TimeframeDefinition]
) -> EvaluationTrigger:
    """起動条件1件（D04 §8 の3区分）。"""
    try:
        if model.when == "bar_close":
            if model.series is None:
                raise ConfigError("`bar_close` の起動条件には `series` が要る")
            return OnBarClose(name=model.name, series=parse_series(model.series, timeframe_defs))
        if model.when == "input_event":
            if model.input_name is None:
                raise ConfigError("`input_event` の起動条件には `input_name` が要る")
            return OnInputEvent(name=model.name, input_name=model.input_name)
        if model.event is None:
            raise ConfigError("`runtime_event` の起動条件には `event` が要る")
        return OnRuntimeEvent(name=model.name, event=RuntimeEventKind(model.event))
    except KernelValueError as exc:
        raise ConfigError(f"起動条件 {model.name!r} を読めない: {exc}") from exc


def output_ref_of(text: str | None, label: str) -> OutputRef | None:
    """役割が指す出力（`<使用箇所>.<出力名>`）。"""
    if text is None:
        return None
    instance_id, separator, output_name = text.partition(".")
    if not separator:
        raise ConfigError(f"{label} は `<使用箇所>.<出力名>` と書くこと（{text!r}）")
    try:
        return OutputRef(instance_id=instance_id, output_name=output_name)
    except KernelValueError as exc:
        raise ConfigError(f"{label} を読めない: {exc}") from exc


def required_output_ref(text: str, label: str) -> OutputRef:
    ref = output_ref_of(text, label)
    if ref is None:  # pragma: no cover - 必須の役割は `None` を取らない
        raise ConfigError(f"{label} は必須である")
    return ref


def _contract_ref(registry: ComponentRegistry, component_id: str, version: int) -> ContractRef:
    """部品カタログから契約参照を引く（D02 §9.2）。

    設定ファイルには部品 ID と版だけを書かせ、契約の指紋はカタログから作る。設定に指紋を
    書かせると、部品の契約を変えたときに全実験設定を直すことになり、書き写しの誤りが
    そのまま「別の契約を指す宣言」になる。
    """
    try:
        key = ContractKey(component_id=component_id, version=version)
    except KernelValueError as exc:
        raise ConfigError(f"部品の参照を読めない: {exc}") from exc
    registration = registry.get(key)
    if registration is None:
        raise ConfigError(
            f"部品 {key} はカタログに無い。段階2のカタログにある部品だけを宣言できる（D05 §4.3）"
        )
    return contract_ref_for(registration.contract)


def component_of(
    model: ComponentModel,
    registry: ComponentRegistry,
    timeframe_defs: Mapping[str, TimeframeDefinition],
) -> ComponentInstance:
    try:
        return ComponentInstance(
            instance_id=model.instance_id,
            contract_ref=_contract_ref(registry, model.component, model.component_version),
            inputs={
                name: InputBinding(
                    sources=tuple(_source_of(text, timeframe_defs) for text in sources)
                )
                for name, sources in model.inputs.items()
            },
            parameters={
                name: _parameter_of(name, parameter) for name, parameter in model.parameters.items()
            },
            evaluation=EvaluationSchedule(
                triggers=tuple(_trigger_of(item, timeframe_defs) for item in model.triggers)
            ),
        )
    except KernelValueError as exc:
        raise ConfigError(f"使用箇所 {model.instance_id!r} を読めない: {exc}") from exc


def concurrency_of(model: ConcurrencyModel) -> OpportunityConcurrencySpec:
    """取引機会の同時保持の宣言（D04 §10.3）。"""
    try:
        return OpportunityConcurrencySpec(
            max_active=model.max_active,
            on_new_trigger=OnNewTrigger(model.on_new_trigger),
            on_order_accepted=OnOrderAccepted(model.on_order_accepted),
        )
    except KernelValueError as exc:
        raise ConfigError(f"取引機会の同時保持の宣言を読めない: {exc}") from exc
