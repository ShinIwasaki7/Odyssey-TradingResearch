"""実験設定の読込（D01 §10.1、D06 §3・§9.3、D07 §8.3・§18、ADR-0018）。

1回の run を回すのに要る宣言を1つの YAML から読む。読むのは次の6群である。

| 群 | 何を決めるか |
|---|---|
| 識別 | 実験の id と版（成果物の説明に載せる） |
| 入力 | 承認済み snapshot の識別子、run 区間、執行系列、解像度階層、seed |
| 口座 | 口座の識別子・通貨・初期残高 |
| ポリシー | リスク・執行・費用・換算の4つ（D06 §7） |
| 戦略 | 部品の使用箇所・役割・入場方針・取引機会の同時保持（D04 §3） |
| 遅延 | 遅延シナリオの版参照（書式 v1 は遅延なしの1件） |

本モジュールは**書式 v1**（`schema_version: 1`）の読込と、書式 v1・v2 に共通する
「実行の本体」（入力・口座・ポリシーの群。D07 §18.2 の「同じ」の行）の解決を持つ。
書式 v2 の読込は `experiment_v2.py` にある。v1 と v2 で同じ実行条件を書けば、この共通部分を
通るので**同じ `ExperimentConfig`（したがって同じ `ConfigDigest`）へ解決する**（D07 §18.5）。

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

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.scalars import parse_duration, require_decimal
from odyssey_fx.app.config.strategy_parts import (
    ComponentModel,
    ConcurrencyModel,
    RolesModel,
    component_of,
    concurrency_of,
    output_ref_of,
    parse_series,
    required_output_ref,
)
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
from odyssey_fx.common.refs import ContentDigest, PolicyRef, SnapshotRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.schedule import DelayScenario
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.registry import ComponentRegistry
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.entry_policy import EntryPolicy, ImmediateEntry
from odyssey_fx.strategy.declarations.opportunity import OpportunityValiditySpec

__all__ = [
    "NO_DELAY_REF",
    "ExperimentConfig",
    "RunBodyModel",
    "load_experiment",
    "parse_series",
    "policy_ref_of",
    "resolve_run_body",
]

#: この実装が読む実験設定の形式版（D01 §10.1）。
_SCHEMA_VERSION = 1


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


class RunBodyModel(StrictModel):
    """書式 v1・v2 に共通する「実行の本体」のキー（D07 §18.2 の「同じ」の行）。

    書式ごとのモデルはこれを継承してキーを足す。共通のキーを1か所で宣言するので、v1 と
    v2 で同じキーの受理範囲がずれない。
    """

    snapshot: str
    run_interval: _IntervalModel
    execution_series: str
    resolution_hierarchy: list[str]
    account: _AccountModel
    risk_policy: _RiskModel
    execution_policy: _ExecutionModel
    cost_model: _CostModel
    conversion_policy: _ConversionModel
    seed: int = 0


class _StrategyModel(StrictModel):
    strategy_id: str
    version: int
    components: list[ComponentModel]
    roles: RolesModel
    opportunity_concurrency: ConcurrencyModel
    entry_policy: Literal["IMMEDIATE"] = "IMMEDIATE"


class _ExperimentModel(RunBodyModel):
    schema_version: int
    id: str
    version: int
    strategy: _StrategyModel


# --- 読込結果（外へ出る型）---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """実験設定の読込結果（D06 §3 が「解決済みの値」と呼ぶもの）。

    `RunConfig` そのものを組み立てないのは、`RunConfig` が要求する `CompiledStrategyRef` が
    **コンパイル結果**であり、設定ファイルからは決まらないためである（D06 §3）。組み立ては
    合成（`app.composition`）が、戦略をコンパイルしたあとで行う。

    ポリシーの版参照（`PolicyRef`）は**宣言の内容ダイジェスト**から作る（`policy_ref_of`）。
    固定の文字列にすると、中身の違うポリシーが同じ参照を持ち、別の結果が同じ `RunId` を
    指してしまう。

    `delay_scenario` は書式 v2 の `delay_scenario` を書いたときだけ入る（D07 §18.3）。
    `None` は遅延なしであり、版参照 `policy_refs["delay"]` は `NO_DELAY_REF` になる。
    遅延を市場データへ当てるのは合成（`app.composition`）である（同節）。
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
    delay_scenario: DelayScenario | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_refs", MappingProxyType(dict(self.policy_refs)))
        if self.delay_scenario is not None and not isinstance(self.delay_scenario, DelayScenario):
            raise ConfigError("ExperimentConfig.delay_scenario must be a DelayScenario or None")

    def policy_ref(self, kind: str) -> PolicyRef:
        """種別で版参照を引く（`risk` / `execution` / `cost` / `conversion` / `delay`）。"""
        ref = self.policy_refs.get(kind)
        if ref is None:  # pragma: no cover - 5種はすべて構築時に作る
            raise ConfigError(f"ポリシーの版参照 {kind!r} が実験設定から作られていない")
        return ref


def policy_ref_of(kind: str, declaration: object) -> PolicyRef:
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


#: 遅延なしの版参照（D03 §7、D06 §9.3、D07 §18.3）。
#:
#: 書式 v1 は遅延なしの1件だけを持ち、書式 v2 は `delay_scenario` を書かない形だけで遅延
#: なしを表す。どちらも**この同じ値**にするので、検証戦略 A の設定を v2 へ書き換えても
#: `ConfigDigest` と `run_id` が変わらない（D07 §18.3・§18.5）。
NO_DELAY_REF: PolicyRef = policy_ref_of("delay", "NO_DELAY")


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


def _strategy_of(
    model: _StrategyModel,
    registry: ComponentRegistry,
    timeframe_defs: Mapping[str, TimeframeDefinition],
) -> StrategyDefinition:
    """書式 v1 の `strategy:` 節（段階2 の宣言だけを書ける）。"""
    roles = model.roles
    entry_policy: EntryPolicy = ImmediateEntry()
    try:
        return StrategyDefinition(
            strategy_id=model.strategy_id,
            version=model.version,
            components=tuple(
                component_of(item, registry, timeframe_defs) for item in model.components
            ),
            market_state=output_ref_of(roles.market_state, "roles.market_state"),
            trigger=required_output_ref(roles.trigger, "roles.trigger"),
            execution_filter=output_ref_of(roles.execution_filter, "roles.execution_filter"),
            order=required_output_ref(roles.order, "roles.order"),
            protection=required_output_ref(roles.protection, "roles.protection"),
            exit=required_output_ref(roles.exit, "roles.exit"),
            entry_policy=entry_policy,
            # 段階2 の束縛は空である（D05 §7.3）。継続成立の条件は書式 v2 の戦略ファイルで書く。
            opportunity_validity=OpportunityValiditySpec(bindings=()),
            opportunity_concurrency=concurrency_of(model.opportunity_concurrency),
        )
    except KernelValueError as exc:
        raise ConfigError(f"戦略の宣言を読めない: {exc}") from exc


def resolve_run_body(
    model: RunBodyModel,
    payload: Mapping[str, Any],
    path: Path,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    *,
    experiment_id: str,
    version: int,
    strategy: StrategyDefinition,
    delay_scenario: DelayScenario | None,
    delay_ref: PolicyRef,
) -> ExperimentConfig:
    """書式 v1・v2 に共通する「実行の本体」を解決済みの値へ変換する（D06 §3、D07 §18.2）。

    `payload` は検証前の YAML の mapping で、ポリシーの版参照（宣言のダイジェスト）の材料に
    する。書式が違っても同じキーには同じ宣言が書かれるので、版参照も同じになる。
    """
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

    try:
        snapshot_ref = SnapshotRef(snapshot_id=SnapshotId(ContentDigest.sha256(model.snapshot)))
    except KernelValueError as exc:
        raise ConfigError(
            f"{path}: `snapshot` は承認済み snapshot の識別子（16進64文字）を書くこと: {exc}"
        ) from exc

    return ExperimentConfig(
        experiment_id=experiment_id,
        version=version,
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
            "risk": policy_ref_of("risk", payload["risk_policy"]),
            "execution": policy_ref_of(
                "execution",
                {
                    **payload["execution_policy"],
                    "resolution_hierarchy": [str(level) for level in hierarchy.levels],
                },
            ),
            "cost": policy_ref_of("cost", payload["cost_model"]),
            "conversion": policy_ref_of("conversion", payload["conversion_policy"]),
            "delay": delay_ref,
        },
        delay_scenario=delay_scenario,
    )


def load_experiment(
    path: Path,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    registry: ComponentRegistry,
) -> ExperimentConfig:
    """書式 v1 の実験設定を読み、宣言型へ変換する（D01 §10.1、D06 §3、D07 §18.5）。

    `timeframe_defs` は系列の時間足の版を解決するために要る（D03 §3.1）。`registry` は
    部品 ID と版から契約参照を引くために要る（D05 §4.1）。
    """
    payload: dict[str, Any] = load_yaml_mapping(path)
    model = validate(_ExperimentModel, payload, path)
    require_schema_version(model.schema_version, _SCHEMA_VERSION, path)
    strategy = _strategy_of(model.strategy, registry, timeframe_defs)
    return resolve_run_body(
        model,
        payload,
        path,
        timeframe_defs,
        experiment_id=model.id,
        version=model.version,
        strategy=strategy,
        # 遅延シナリオは書式 v1 では「遅延なし」の1件だけである（D03 §7、D06 §9.3）。
        # 版参照だけを manifest へ残し、実現公開時刻は通常の公開予定を使う。
        delay_scenario=None,
        delay_ref=NO_DELAY_REF,
    )
