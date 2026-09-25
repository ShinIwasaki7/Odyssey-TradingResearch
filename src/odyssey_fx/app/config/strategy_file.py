"""戦略ファイルの読込（D04 §3・§10・§13.1、D07 §18.4）。

戦略宣言は実験設定とは別のファイル `configs/strategies/<strategy_id>_v<version>.yaml` に置き、
書式 v2 の実験設定からパスで指す（D07 §18.4、Q11 決定）。同じ戦略を複数の実験（遅延シナリオ
4ケースなど）で使っても、戦略の本文は1か所に保てる。

書き方は D04 §13.1 の一般規則に従う。

- 区分タグ付き union は `kind:` で書く（D01 §8）。
- 宣言に現れる期間は `<正の整数><単位>`（`"1h"`・`"30m"`）の1形式だけを受ける。
- 未宣言キーは拒否する（ADR-0018）。

使用箇所・入力の参照元・起動条件・パラメータ・役割・同時保持は、書式 v1 の `strategy:` 節の
略記をそのまま引き継ぐ（`strategy_parts.py`）。段階3 の宣言のために足したキーは
`entry_policy`（`kind:` 付き）・`opportunity_validity`・役割 `market_state` /
`execution_filter` の3つである（D07 §18.4 の表）。

**書式 v1 の `entry_policy: "IMMEDIATE"`（文字列）は受け付けない**（D07 §18.4）。区分タグ付き
union を `kind:` で書く規則（D01 §8）に揃える。

**`opportunity_validity` は省略できない**（D04 §10.2、Q5 決定。ADR-0031 の「暗黙の既定値
なし」）。束縛の無い戦略は `{bindings: []}` と明示する。束縛1件の `on_missing` も省略できない
（同節の「必須指定」）。`entry_policy` も省略できない（書式 v1 が持っていた既定値
`IMMEDIATE` を戦略ファイルでは置かない）。暗黙の既定値を置かない原則（ADR-0031。有効性束縛と
同じ）に従い、戦略ファイルの中で束縛だけが必須・入場方針は任意という非対称を作らないため
である（2026-09-25 の人間の決定。D07 §18.4）。

トップレベルの `schema_version` は戦略宣言の保存形式の版（D04 §3。段階3 でも 1）であり、
実験設定の版（2）とは別の版である（D07 §18.4）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.strategy_parts import (
    ComponentModel,
    ConcurrencyModel,
    RolesModel,
    component_of,
    concurrency_of,
    output_ref_of,
    required_output_ref,
)
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.registry import ComponentRegistry
from odyssey_fx.strategy.declarations.contract import SCHEMA_VERSION
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.duration import parse_duration
from odyssey_fx.strategy.declarations.entry_policy import (
    AwaitConfirmation,
    BarsDeadline,
    DeadlineAction,
    DurationDeadline,
    EntryPolicy,
    ImmediateEntry,
)
from odyssey_fx.strategy.declarations.missing import (
    Error,
    MissingInputPolicy,
    OnSuperseded,
    SkipEvaluation,
    UsePrevious,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.opportunity import (
    OpportunityValiditySpec,
    ValidityBinding,
    ValidityMode,
)
from odyssey_fx.strategy.declarations.read_spec import BarsWindow, DurationWindow

__all__ = ["load_strategy_file"]


# --- 設定ファイルの形（Pydantic）--------------------------------------------


class _BarsDeadlineModel(StrictModel):
    kind: Literal["BARS"]
    bars: int


class _DurationDeadlineModel(StrictModel):
    kind: Literal["DURATION"]
    duration: str


_DeadlineModel = Annotated[_BarsDeadlineModel | _DurationDeadlineModel, Field(discriminator="kind")]


class _ImmediateModel(StrictModel):
    kind: Literal["IMMEDIATE"]


class _AwaitConfirmationModel(StrictModel):
    kind: Literal["AWAIT_CONFIRMATION"]
    deadline: _DeadlineModel
    on_deadline: Literal["EXPIRE"]


_EntryPolicyModel = Annotated[
    _ImmediateModel | _AwaitConfirmationModel, Field(discriminator="kind")
]


class _SkipEvaluationModel(StrictModel):
    kind: Literal["SKIP_EVALUATION"]


class _ErrorModel(StrictModel):
    kind: Literal["ERROR"]


class _WaitForInputModel(StrictModel):
    kind: Literal["WAIT_FOR_INPUT"]
    deadline: _DeadlineModel
    on_deadline: Literal["SKIP_EVALUATION", "ERROR"]
    on_superseded: Literal["EXPIRE_REQUEST", "KEEP_WAITING"]


class _BarsWindowModel(StrictModel):
    kind: Literal["BARS"]
    count: int


class _DurationWindowModel(StrictModel):
    kind: Literal["DURATION"]
    duration: str


class _UsePreviousModel(StrictModel):
    kind: Literal["USE_PREVIOUS"]
    max_lookback: Annotated[_BarsWindowModel | _DurationWindowModel, Field(discriminator="kind")]
    allowed_reasons: list[str]


_MissingPolicyModel = Annotated[
    _SkipEvaluationModel | _ErrorModel | _WaitForInputModel | _UsePreviousModel,
    Field(discriminator="kind"),
]


class _BindingModel(StrictModel):
    source: str
    mode: Literal["SNAPSHOT_AT_OPPORTUNITY", "REQUIRE_UNTIL_ORDER_REQUEST"]
    on_missing: _MissingPolicyModel


class _ValidityModel(StrictModel):
    bindings: list[_BindingModel]


class _StrategyFileModel(StrictModel):
    schema_version: int
    strategy_id: str
    version: int
    components: list[ComponentModel]
    roles: RolesModel
    entry_policy: _EntryPolicyModel
    opportunity_validity: _ValidityModel
    opportunity_concurrency: ConcurrencyModel


# --- 宣言型への変換 ---------------------------------------------------------


def _duration(text: str, label: str) -> timedelta:
    """D04 §13.1 の期間（`<正の整数><単位>`）。ISO 8601 や素の数値は拒否する。"""
    try:
        return parse_duration(text)
    except KernelValueError as exc:
        raise ConfigError(f"{label}: {exc}") from exc


def _deadline_of(
    model: _BarsDeadlineModel | _DurationDeadlineModel, label: str
) -> BarsDeadline | DurationDeadline:
    """期限（D04 §10.1。本数か経過時間）。"""
    if isinstance(model, _BarsDeadlineModel):
        return BarsDeadline(bars=model.bars)
    return DurationDeadline(duration=_duration(model.duration, f"{label}.duration"))


def _entry_policy_of(model: _ImmediateModel | _AwaitConfirmationModel) -> EntryPolicy:
    """発注方針（D04 §10.1）。"""
    if isinstance(model, _ImmediateModel):
        return ImmediateEntry()
    return AwaitConfirmation(
        deadline=_deadline_of(model.deadline, "entry_policy.deadline"),
        on_deadline=DeadlineAction(model.on_deadline),
    )


def _missing_policy_of(
    model: _SkipEvaluationModel | _ErrorModel | _WaitForInputModel | _UsePreviousModel,
    label: str,
) -> MissingInputPolicy:
    """欠損方針（D04 §6.3 の4区分）。

    4区分とも書けるようにする。どの区分をどこで使えるか（有効性束縛で遡りを使えない等）は
    コンパイラが検査する（D04 §12、D05 §5.6）。ここで狭めると、同じ規則が2か所に分かれる。
    """
    if isinstance(model, _SkipEvaluationModel):
        return SkipEvaluation()
    if isinstance(model, _ErrorModel):
        return Error()
    if isinstance(model, _WaitForInputModel):
        return WaitForInput(
            deadline=_deadline_of(model.deadline, f"{label}.deadline"),
            on_deadline=WaitDeadlineAction(model.on_deadline),
            on_superseded=OnSuperseded(model.on_superseded),
        )
    lookback = model.max_lookback
    window: BarsWindow | DurationWindow = (
        BarsWindow(count=lookback.count)
        if isinstance(lookback, _BarsWindowModel)
        else DurationWindow(duration=_duration(lookback.duration, f"{label}.max_lookback"))
    )
    try:
        reasons = tuple(MissingInputReason(item) for item in model.allowed_reasons)
    except ValueError as exc:
        raise ConfigError(
            f"{label}.allowed_reasons: 欠損理由は"
            f" {[member.value for member in MissingInputReason]} のいずれかで書くこと: {exc}"
        ) from exc
    return UsePrevious(max_lookback=window, allowed_reasons=reasons)


def _validity_of(model: _ValidityModel) -> OpportunityValiditySpec:
    """取引機会の有効性の宣言（D04 §10.2）。"""
    bindings = []
    for index, item in enumerate(model.bindings):
        label = f"opportunity_validity.bindings[{index}]"
        source = required_output_ref(item.source, f"{label}.source")
        bindings.append(
            ValidityBinding(
                source=source,
                mode=ValidityMode(item.mode),
                on_missing=_missing_policy_of(item.on_missing, f"{label}.on_missing"),
            )
        )
    return OpportunityValiditySpec(bindings=tuple(bindings))


def load_strategy_file(
    path: Path,
    registry: ComponentRegistry,
    timeframe_defs: Mapping[str, TimeframeDefinition],
) -> StrategyDefinition:
    """戦略ファイルを読み、戦略宣言（`StrategyDefinition`）へ変換する（D04 §13.1、D07 §18.4）。

    `timeframe_defs` は系列の時間足の版を解決するために、`registry` は部品 ID と版から契約
    参照を引くために要る（書式 v1 の `strategy:` 節と同じ）。
    """
    payload = load_yaml_mapping(path)
    model = validate(_StrategyFileModel, payload, path)
    require_schema_version(model.schema_version, SCHEMA_VERSION, path)
    roles = model.roles
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
            entry_policy=_entry_policy_of(model.entry_policy),
            opportunity_validity=_validity_of(model.opportunity_validity),
            opportunity_concurrency=concurrency_of(model.opportunity_concurrency),
        )
    except (KernelValueError, ConfigError) as exc:
        raise ConfigError(f"{path}: 戦略の宣言を読めない: {exc}") from exc
