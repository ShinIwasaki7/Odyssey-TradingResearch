"""実験設定の読込（D01 §10.1、D06 §3・§9.3、D07 §8.3、ADR-0018）。

1回の run を回すのに要る宣言を1つの YAML から読む。読むのは次の6群である。

| 群 | 何を決めるか |
|---|---|
| 識別 | 実験の id と版（成果物の説明に載せる） |
| 入力 | 承認済み snapshot の識別子、run 区間、執行系列、解像度階層、seed |
| 口座 | 口座の識別子・通貨・初期残高 |
| ポリシー | リスク・執行・費用・換算の4つ（D06 §7） |
| 戦略 | 部品の使用箇所・役割・入場方針・取引機会の同時保持（D04 §3） |
| 遅延 | 遅延シナリオの版参照（段階2は遅延なしの1件） |

**`Decimal` になる値は文字列で書く**（ADR-0012）。浮動小数として書くと二進浮動小数の誤差が
入る。例外は部品パラメータの `FLOAT` 型で、これは D04 §7 が「価格・pips・比率は `FLOAT` と
単位の組で宣言する」と定めた値であり、`Price` への変換と丸めは D06 の境界が行う。

**Pydantic モデルはこの層の外へ出さない**（D01 §10.1）。外へ出るのは `strategy.declarations`
と `backtest.domain` の frozen dataclass だけである。系列の文字列（`USDJPY/1h/bid`）は
時間足の版を持たないので（D03 §3.1）、時間足定義の読込結果から版を解決する。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pydantic import Field

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.scalars import parse_duration, require_decimal
from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.backtest.domain.policies import (
    ConversionPolicy,
    CostModel,
    ExecutionPolicy,
    FixedSpread,
    ReferenceQuoteSource,
    ResolutionHierarchy,
    RiskPolicy,
)
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AccountId, SnapshotId
from odyssey_fx.common.money import CurrencyCode, Money, PriceOffset
from odyssey_fx.common.refs import ContentDigest, ContractRef, PolicyRef, SnapshotRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.registry import ComponentRegistry, ContractKey
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.digest import contract_ref_for
from odyssey_fx.strategy.declarations.entry_policy import EntryPolicy, ImmediateEntry
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
    OpportunityValiditySpec,
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

__all__ = ["ExperimentConfig", "load_experiment", "parse_series"]

#: この実装が読む実験設定の形式版（D01 §10.1）。
_SCHEMA_VERSION = 1


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


class _IntervalModel(StrictModel):
    start: str
    end: str


class _AccountModel(StrictModel):
    account_id: str
    currency: str
    initial_balance: str


class _RiskModel(StrictModel):
    trial_risk_rate: str
    account_risk_cap: str


class _ExecutionModel(StrictModel):
    entry_valid_for: str
    close_valid_for: str
    adverse_fill_limits: dict[str, str]
    entry_delay_bars: int = 0
    reference_quote_source: Literal["EXECUTION_SERIES_LAST_CLOSE"] = "EXECUTION_SERIES_LAST_CLOSE"


class _CostModel(StrictModel):
    commission_per_unit: str
    entry_slippage: str
    close_slippage: str
    spread_offset: str


class _ConversionModel(StrictModel):
    pivot_currency: str
    max_observation_skew: str


class _ParameterModel(StrictModel):
    type: Literal["BOOL", "INT", "FLOAT", "STR"]
    value: bool | int | float | str


class _TriggerModel(StrictModel):
    name: str
    on: Literal["bar_close", "input_event", "runtime_event"]
    series: str | None = None
    input_name: str | None = None
    event: Literal["POSITION_OPENED"] | None = None


class _ComponentModel(StrictModel):
    instance_id: str
    component: str
    component_version: int
    inputs: dict[str, list[str]] = Field(default_factory=dict)
    parameters: dict[str, _ParameterModel] = Field(default_factory=dict)
    triggers: list[_TriggerModel]


class _RolesModel(StrictModel):
    trigger: str
    order: str
    protection: str
    exit: str
    market_state: str | None = None
    execution_filter: str | None = None


class _ConcurrencyModel(StrictModel):
    max_active: int
    on_new_trigger: Literal["KEEP_EXISTING", "SUPERSEDE_EXISTING"]
    on_order_accepted: Literal["KEEP_OTHERS", "CLOSE_OTHERS"]


class _StrategyModel(StrictModel):
    strategy_id: str
    version: int
    components: list[_ComponentModel]
    roles: _RolesModel
    opportunity_concurrency: _ConcurrencyModel
    entry_policy: Literal["IMMEDIATE"] = "IMMEDIATE"


class _ExperimentModel(StrictModel):
    schema_version: int
    id: str
    version: int
    snapshot: str
    run_interval: _IntervalModel
    execution_series: str
    resolution_hierarchy: list[str]
    account: _AccountModel
    risk_policy: _RiskModel
    execution_policy: _ExecutionModel
    cost_model: _CostModel
    conversion_policy: _ConversionModel
    strategy: _StrategyModel
    seed: int = 0


# --- 読込結果（外へ出る型）---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """実験設定の読込結果（D06 §3 が「解決済みの値」と呼ぶもの）。

    `RunConfig` そのものを組み立てないのは、`RunConfig` が要求する `CompiledStrategyRef` が
    **コンパイル結果**であり、設定ファイルからは決まらないためである（D06 §3）。組み立ては
    合成（`app.composition`）が、戦略をコンパイルしたあとで行う。

    ポリシーの版参照（`PolicyRef`）は**宣言の内容ダイジェスト**から作る（`_policy_ref`）。
    固定の文字列にすると、中身の違うポリシーが同じ参照を持ち、別の結果が同じ `RunId` を
    指してしまう。
    """

    experiment_id: str
    version: int
    snapshot_ref: SnapshotRef
    run_interval: Interval
    execution_series: SeriesId
    account: AccountSpec
    risk_policy: RiskPolicy
    execution_policy: ExecutionPolicy
    cost_model: CostModel
    conversion_policy: ConversionPolicy
    strategy: StrategyDefinition
    seed: int
    policy_refs: Mapping[str, PolicyRef]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_refs", MappingProxyType(dict(self.policy_refs)))

    def policy_ref(self, kind: str) -> PolicyRef:
        """種別で版参照を引く（`risk` / `execution` / `cost` / `conversion` / `delay`）。"""
        ref = self.policy_refs.get(kind)
        if ref is None:  # pragma: no cover - 5種はすべて構築時に作る
            raise ConfigError(f"ポリシーの版参照 {kind!r} が実験設定から作られていない")
        return ref


def _policy_ref(kind: str, declaration: object) -> PolicyRef:
    """宣言されたポリシーの内容から版参照を作る（D02 §9.2、上位設計書 §4.7.15）。

    ダイジェストの対象は**設定ファイルに書かれた宣言そのもの**（文字列と整数の mapping）
    である。解決済みの値（`ExecutionPolicy` など）は期間（`timedelta`）を持ち、正規化
    エンコードは期間を符号化しないと決めている（D02 §9.3、2026-09-21 の決定）ため、値の
    側からはダイジェストを作れない。宣言はダイジェストできる型だけで書かれており、同じ
    宣言からは常に同じ参照が出る。固定の文字列にすると、中身の違うポリシーが同じ参照を
    持ち、別の結果が同じ `RunId` を指してしまう。
    """
    return PolicyRef(
        policy_kind=kind,
        policy_id=f"{kind}_v1",
        version=1,
        digest=digest(declaration),
    )


def _decimal(value: str, label: str) -> Decimal:
    return require_decimal(value, label)


def _account_of(model: _AccountModel) -> AccountSpec:
    try:
        currency = CurrencyCode(model.currency)
        return AccountSpec(
            account_id=AccountId(model.account_id),
            currency=currency,
            initial_balance=Money(
                _decimal(model.initial_balance, "account.initial_balance"), currency
            ),
        )
    except KernelValueError as exc:
        raise ConfigError(f"口座の宣言を読めない: {exc}") from exc


def _risk_of(model: _RiskModel) -> RiskPolicy:
    try:
        return RiskPolicy(
            trial_risk_rate=_decimal(model.trial_risk_rate, "risk_policy.trial_risk_rate"),
            account_risk_cap=_decimal(model.account_risk_cap, "risk_policy.account_risk_cap"),
        )
    except KernelValueError as exc:
        raise ConfigError(f"リスクのポリシーを読めない: {exc}") from exc


def _execution_of(model: _ExecutionModel, hierarchy: ResolutionHierarchy) -> ExecutionPolicy:
    try:
        limits = {
            Symbol(symbol): PriceOffset(
                _decimal(offset, f"execution_policy.adverse_fill_limits[{symbol!r}]")
            )
            for symbol, offset in model.adverse_fill_limits.items()
        }
        return ExecutionPolicy(
            adverse_fill_limits=limits,
            entry_valid_for=parse_duration(
                model.entry_valid_for, "execution_policy.entry_valid_for"
            ),
            close_valid_for=parse_duration(
                model.close_valid_for, "execution_policy.close_valid_for"
            ),
            resolution_hierarchy=hierarchy,
            entry_delay_bars=model.entry_delay_bars,
            reference_quote_source=ReferenceQuoteSource(model.reference_quote_source),
        )
    except KernelValueError as exc:
        raise ConfigError(f"執行のポリシーを読めない: {exc}") from exc


def _cost_of(model: _CostModel, currency: CurrencyCode) -> CostModel:
    try:
        return CostModel(
            commission_per_unit=Money(
                _decimal(model.commission_per_unit, "cost_model.commission_per_unit"), currency
            ),
            entry_slippage=PriceOffset(_decimal(model.entry_slippage, "cost_model.entry_slippage")),
            close_slippage=PriceOffset(_decimal(model.close_slippage, "cost_model.close_slippage")),
            spread_model=FixedSpread(
                offset=PriceOffset(_decimal(model.spread_offset, "cost_model.spread_offset"))
            ),
        )
    except KernelValueError as exc:
        raise ConfigError(f"費用モデルを読めない: {exc}") from exc


def _conversion_of(model: _ConversionModel) -> ConversionPolicy:
    try:
        return ConversionPolicy(
            pivot_currency=CurrencyCode(model.pivot_currency),
            max_observation_skew=parse_duration(
                model.max_observation_skew, "conversion_policy.max_observation_skew"
            ),
        )
    except KernelValueError as exc:
        raise ConfigError(f"換算のポリシーを読めない: {exc}") from exc


def _parameter_of(name: str, model: _ParameterModel) -> ParameterValue:
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
    model: _TriggerModel, timeframe_defs: Mapping[str, TimeframeDefinition]
) -> EvaluationTrigger:
    """起動条件1件（D04 §8 の3区分）。"""
    try:
        if model.on == "bar_close":
            if model.series is None:
                raise ConfigError("`bar_close` の起動条件には `series` が要る")
            return OnBarClose(name=model.name, series=parse_series(model.series, timeframe_defs))
        if model.on == "input_event":
            if model.input_name is None:
                raise ConfigError("`input_event` の起動条件には `input_name` が要る")
            return OnInputEvent(name=model.name, input_name=model.input_name)
        if model.event is None:
            raise ConfigError("`runtime_event` の起動条件には `event` が要る")
        return OnRuntimeEvent(name=model.name, event=RuntimeEventKind(model.event))
    except KernelValueError as exc:
        raise ConfigError(f"起動条件 {model.name!r} を読めない: {exc}") from exc


def _output_ref_of(text: str | None, label: str) -> OutputRef | None:
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


def _required_output_ref(text: str, label: str) -> OutputRef:
    ref = _output_ref_of(text, label)
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


def _component_of(
    model: _ComponentModel,
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


def _strategy_of(
    model: _StrategyModel,
    registry: ComponentRegistry,
    timeframe_defs: Mapping[str, TimeframeDefinition],
) -> StrategyDefinition:
    roles = model.roles
    entry_policy: EntryPolicy = ImmediateEntry()
    try:
        return StrategyDefinition(
            strategy_id=model.strategy_id,
            version=model.version,
            components=tuple(
                _component_of(item, registry, timeframe_defs) for item in model.components
            ),
            market_state=_output_ref_of(roles.market_state, "roles.market_state"),
            trigger=_required_output_ref(roles.trigger, "roles.trigger"),
            execution_filter=_output_ref_of(roles.execution_filter, "roles.execution_filter"),
            order=_required_output_ref(roles.order, "roles.order"),
            protection=_required_output_ref(roles.protection, "roles.protection"),
            exit=_required_output_ref(roles.exit, "roles.exit"),
            entry_policy=entry_policy,
            # 段階2 の束縛は空である（D05 §7.3）。継続成立の条件は段階3 で足す。
            opportunity_validity=OpportunityValiditySpec(bindings=()),
            opportunity_concurrency=OpportunityConcurrencySpec(
                max_active=model.opportunity_concurrency.max_active,
                on_new_trigger=OnNewTrigger(model.opportunity_concurrency.on_new_trigger),
                on_order_accepted=OnOrderAccepted(model.opportunity_concurrency.on_order_accepted),
            ),
        )
    except KernelValueError as exc:
        raise ConfigError(f"戦略の宣言を読めない: {exc}") from exc


def load_experiment(
    path: Path,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    registry: ComponentRegistry,
) -> ExperimentConfig:
    """実験設定を読み、宣言型へ変換する（D01 §10.1、D06 §3）。

    `timeframe_defs` は系列の時間足の版を解決するために要る（D03 §3.1）。`registry` は
    部品 ID と版から契約参照を引くために要る（D05 §4.1）。
    """
    payload: dict[str, Any] = load_yaml_mapping(path)
    model = validate(_ExperimentModel, payload, path)
    require_schema_version(model.schema_version, _SCHEMA_VERSION, path)

    try:
        run_interval = Interval(
            start=UtcTime.parse(model.run_interval.start),
            end=UtcTime.parse(model.run_interval.end),
        )
    except KernelValueError as exc:
        raise ConfigError(f"{path}: run 区間を読めない: {exc}") from exc

    execution_series = parse_series(model.execution_series, timeframe_defs)
    if not model.resolution_hierarchy:
        raise ConfigError(f"{path}: `resolution_hierarchy` は1件以上書くこと（ADR-0030）")
    try:
        hierarchy = ResolutionHierarchy(
            levels=tuple(parse_series(text, timeframe_defs) for text in model.resolution_hierarchy)
        )
    except KernelValueError as exc:
        raise ConfigError(f"{path}: 解像度階層を読めない: {exc}") from exc

    account = _account_of(model.account)
    risk_policy = _risk_of(model.risk_policy)
    execution_policy = _execution_of(model.execution_policy, hierarchy)
    cost_model = _cost_of(model.cost_model, account.currency)
    conversion_policy = _conversion_of(model.conversion_policy)
    strategy = _strategy_of(model.strategy, registry, timeframe_defs)

    try:
        snapshot_ref = SnapshotRef(snapshot_id=SnapshotId(ContentDigest.sha256(model.snapshot)))
    except KernelValueError as exc:
        raise ConfigError(
            f"{path}: `snapshot` は承認済み snapshot の識別子（16進64文字）を書くこと: {exc}"
        ) from exc

    return ExperimentConfig(
        experiment_id=model.id,
        version=model.version,
        snapshot_ref=snapshot_ref,
        run_interval=run_interval,
        execution_series=execution_series,
        account=account,
        risk_policy=risk_policy,
        execution_policy=execution_policy,
        cost_model=cost_model,
        conversion_policy=conversion_policy,
        strategy=strategy,
        seed=model.seed,
        policy_refs={
            "risk": _policy_ref("risk", payload["risk_policy"]),
            "execution": _policy_ref(
                "execution",
                {
                    **payload["execution_policy"],
                    "resolution_hierarchy": [str(level) for level in hierarchy.levels],
                },
            ),
            "cost": _policy_ref("cost", payload["cost_model"]),
            "conversion": _policy_ref("conversion", payload["conversion_policy"]),
            # 遅延シナリオは段階2では「遅延なし」の1件だけである（D03 §7、D06 §9.3）。
            # 版参照だけを manifest へ残し、実現公開時刻は通常の公開予定を使う。
            "delay": _policy_ref("delay", "NO_DELAY"),
        },
    )
