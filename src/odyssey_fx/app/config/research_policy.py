"""研究ポリシーファイルと版の登録簿の読込（D07 §20.2、D09 §6.1・§7.1・§10.9）。

`configs/policies/research/<id>_v<version>.yaml` を読み、`ResearchPolicy` へ変換する。**検査の
規則はコードに置き、ファイルは値だけを持つ**（D07 §20.2）。

**版ごとの形**（D07 §20.2、D09 §10.9）。どのキーを持つかは版の番号で決まり、持たない版に
書けば未宣言キーとして拒否する。

| 版 | キー |
|---|---|
| 1 | `schema_version` / `id` / `version` / `complexity_limits` |
| 2 | 版 1 ＋ `search_limits: {trials}`（1実験の試行数の上限。Q7 決定） |
| 3 以上 | 版 2 ＋ `evaluation_standard: {purpose, split, selection, validation, sufficiency}` |

実験設定の書式 v2 は研究ポリシーを版参照 `{id, version}` だけで指す（D07 §18.2）。読んだ
ファイルの `id` / `version` が参照と一致することを確かめる（食い違えば設定の誤り）。

**ダイジェスト**は研究ポリシーファイルの**すべての値の項目**の正規化ダイジェストである
（D07 §20.2 v2.7、D09 §10.9）。版 1 は従来どおり形式版・`id`・版・上限4件で、版 2 は
`search_limits`、版 3 以上は `evaluation_standard` を足す。`evaluation_standard` は**解決済みの
値**（長さは秒数、閾値は `Decimal`、時刻は UTC の正規形）で入れるので、同じ値の書き方の揺れ
（`"90m"` と `"5400s"`、`"1.50"` と `"1.5"`）ではダイジェストが変わらない。

**研究ポリシーを読むときの検査**（違反は設定の誤り `ConfigError`、終了コード 2）: 期間分割の
標準規則の検査（D09 §6.1 の 1〜3・5〜7）と評価基準の検査 E1〜E5（D09 §7.1・§7.8）。検査の規則
そのものは `evaluation.domain.splits` / `evaluation.domain.search` の型が持ち、ここは値の読み取り
と例外の言い換えだけをする。

**版の登録簿**（`configs/policies/research/registry.yaml`。D09 §10.9、2026-09-29 の人間の決定 Q8・
2026-09-30 の Q33）: 書式 v2 の読込が研究ポリシーファイルを読んだ直後に、(1) 登録簿を読み、
(2) 版参照 `(id, version)` の要素を引き、(3) 無ければ拒否し、(4) `digest` が違えば拒否し、
(5) 評価基準の群を持つ版で要素の `purpose` がファイルの `evaluation_standard.purpose` と違えば
拒否する。**読込が登録簿へ書き足すことはしない**。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.scalars import require_decimal
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import MetricId
from odyssey_fx.evaluation.domain.research_policy import (
    ComplexityLimits,
    RegistryEntry,
    ResearchPolicy,
)
from odyssey_fx.evaluation.domain.search import (
    AggregateCondition,
    Comparator,
    EvaluationStandard,
    FoldStatistic,
    FrequencyClass,
    MetricCondition,
    SelectionDirection,
    SelectionRule,
    StandardPurpose,
    SufficiencyRule,
    ValidationRule,
)
from odyssey_fx.evaluation.domain.splits import (
    SplitStandard,
    SplitStandardViolation,
    SplitWindow,
    folds_for_policy,
)
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.strategy.declarations.duration import DURATION_UNITS

__all__ = [
    "RESEARCH_POLICY_DIRECTORY",
    "RESEARCH_POLICY_REGISTRY_FILE",
    "RESEARCH_POLICY_REGISTRY_SCHEMA_VERSION",
    "RESEARCH_POLICY_SCHEMA_VERSION",
    "load_research_policy",
    "load_research_policy_registry",
    "research_policy_path",
    "research_policy_registry_path",
    "verify_registered",
]

#: 研究ポリシーファイルの形式版（D07 §20.2）。
RESEARCH_POLICY_SCHEMA_VERSION: Final = 1

#: 研究ポリシーファイルの置き場（リポジトリの根からの相対パス。D01 §10.1 の `policies/`）。
RESEARCH_POLICY_DIRECTORY: Final = Path("configs/policies/research")

#: 版の登録簿のファイル名（`RESEARCH_POLICY_DIRECTORY` の中。D09 §10.9）。
RESEARCH_POLICY_REGISTRY_FILE: Final = "registry.yaml"

#: 版の登録簿の形式版（D09 §10.9。用途を足しても 1 のまま）。
RESEARCH_POLICY_REGISTRY_SCHEMA_VERSION: Final = 1

#: 試行数の上限を持つ最初の版（D09 §10.9。Q7 決定）。
_FIRST_VERSION_WITH_TRIAL_LIMIT: Final = 2

#: 評価基準の群を持つ最初の版（D09 §10.9。v0.2）。
_FIRST_VERSION_WITH_STANDARD: Final = 3

#: 研究ポリシーの `id` の字種（ファイル名に使うため、区切り文字や `..` を含めない）。
_POLICY_ID: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")

#: 期間分割の長さの書き方（D04 §13.1 の `<整数><単位>`。purge だけは 0 を許すので、ここでは
#: 0 も受け、正であることは長さごとに確かめる。D09 §6.1）。照合は `fullmatch`。
_LENGTH: Final = re.compile(r"(?P<amount>0|[1-9][0-9]*)(?P<unit>[smhd])")


class _LimitsModel(StrictModel):
    component_kinds: int
    instances: int
    parameters: int
    decision_outputs: int


class _SearchLimitsModel(StrictModel):
    trials: int


class _IntervalModel(StrictModel):
    start: str
    end: str


class _SplitModel(StrictModel):
    range: _IntervalModel
    train_length: str
    validation_length: str
    window: str
    purge: str
    min_folds: int


class _ConditionModel(StrictModel):
    metric: str
    comparator: str
    threshold: str


class _AggregateModel(StrictModel):
    metric: str
    statistic: str
    comparator: str
    threshold: str


class _SelectionModel(StrictModel):
    metric: str
    direction: str
    eligibility: list[_ConditionModel]


class _ValidationModel(StrictModel):
    fold_floors: list[_ConditionModel]
    aggregate: list[_AggregateModel]


class _FrequencyClassModel(StrictModel):
    name: str
    min_train_trades_per_365d: str
    min_validation_trades_per_fold: int
    min_validation_trades_total: int


class _SufficiencyModel(StrictModel):
    classes: list[_FrequencyClassModel]


class _EvaluationStandardModel(StrictModel):
    purpose: str
    split: _SplitModel
    selection: _SelectionModel
    validation: _ValidationModel
    sufficiency: _SufficiencyModel


class _ResearchPolicyV1Model(StrictModel):
    schema_version: int
    id: str
    version: int
    complexity_limits: _LimitsModel


class _ResearchPolicyV2Model(_ResearchPolicyV1Model):
    search_limits: _SearchLimitsModel


class _ResearchPolicyV3Model(_ResearchPolicyV2Model):
    evaluation_standard: _EvaluationStandardModel


class _RegistryEntryModel(StrictModel):
    id: str
    version: int
    digest: str
    purpose: str | None = None


class _RegistryModel(StrictModel):
    schema_version: int
    entries: list[_RegistryEntryModel]


def research_policy_path(repo_root: Path, policy_id: str, version: int) -> Path:
    """版参照 `{id, version}` が指すファイルの位置。

    `<根>/configs/policies/research/<id>_v<版>.yaml`（D07 §20.2）。
    """
    if not _POLICY_ID.fullmatch(policy_id):
        raise ConfigError(
            f"研究ポリシーの id {policy_id!r} はファイル名に使うので、英数字・下線・ハイフンで"
            " 書くこと（D07 §20.2）"
        )
    return repo_root / RESEARCH_POLICY_DIRECTORY / f"{policy_id}_v{version}.yaml"


def research_policy_registry_path(repo_root: Path) -> Path:
    """版の登録簿の位置（`<根>/configs/policies/research/registry.yaml`。D09 §10.9）。"""
    return repo_root / RESEARCH_POLICY_DIRECTORY / RESEARCH_POLICY_REGISTRY_FILE


# --- 値の読み取り -------------------------------------------------------------


def _member[EnumT: Enum](enum_type: type[EnumT], value: str, label: str) -> EnumT:
    """語彙の文字列を列挙型にする。語彙に無ければ設定の誤り（D09 §7.1 の検査 E1・E5）。"""
    try:
        return enum_type(value)
    except ValueError:
        allowed = [member.value for member in enum_type]
        raise ConfigError(f"{label} が {value!r} だが、書けるのは {allowed} である") from None


def _seconds(text: str, label: str, *, positive: bool) -> int:
    """長さ `<整数><単位>`（D04 §13.1）を秒数の整数にする（D09 §6.1）。

    暦月・取引日では数えない。`positive` なら 0 を拒否する（選定区間・検証区間の長さ。検査1）。
    purge は 0 を許す（`"0s"`。検査5）。
    """
    match = _LENGTH.fullmatch(text)
    if match is None:
        raise ConfigError(
            f"{label} が {text!r} だが、長さは `<整数><単位>`（単位は s / m / h / d の1つ。"
            '例: "90m"）で書くこと（D09 §6.1、D04 §13.1）'
        )
    seconds = int(match.group("amount")) * DURATION_UNITS[match.group("unit")]
    if positive and seconds == 0:
        raise ConfigError(
            f"{label} が {text!r} だが、長さは正でなければならない（D09 §6.1 の検査1）"
        )
    return seconds


def _utc(text: str, label: str) -> UtcTime:
    try:
        return UtcTime.parse(text)
    except KernelValueError as exc:
        raise ConfigError(f"{label}: {exc}") from exc


def _split_of(model: _SplitModel, label: str) -> SplitStandard:
    start = _utc(model.range.start, f"{label}.range.start")
    end = _utc(model.range.end, f"{label}.range.end")
    if not start < end:
        raise ConfigError(
            f"{label}.range が [{start}, {end}) で空である。評価範囲は start < end で書くこと"
            "（D09 §6.1 の検査1）"
        )
    if model.min_folds < 1:
        raise ConfigError(
            f"{label}.min_folds が {model.min_folds} だが、1 以上で書くこと（D09 §6.1 の検査7）"
        )
    return SplitStandard(
        range=Interval(start=start, end=end),
        train_seconds=_seconds(model.train_length, f"{label}.train_length", positive=True),
        validation_seconds=_seconds(
            model.validation_length, f"{label}.validation_length", positive=True
        ),
        window=_member(SplitWindow, model.window, f"{label}.window"),
        purge_seconds=_seconds(model.purge, f"{label}.purge", positive=False),
        min_folds=model.min_folds,
    )


def _condition_of(model: _ConditionModel, label: str) -> MetricCondition:
    return MetricCondition(
        metric=_member(MetricId, model.metric, f"{label}.metric"),
        comparator=_member(Comparator, model.comparator, f"{label}.comparator"),
        threshold=require_decimal(model.threshold, f"{label}.threshold"),
    )


def _aggregate_of(model: _AggregateModel, label: str) -> AggregateCondition:
    return AggregateCondition(
        metric=_member(MetricId, model.metric, f"{label}.metric"),
        statistic=_member(FoldStatistic, model.statistic, f"{label}.statistic"),
        comparator=_member(Comparator, model.comparator, f"{label}.comparator"),
        threshold=require_decimal(model.threshold, f"{label}.threshold"),
    )


def _frequency_class_of(model: _FrequencyClassModel, label: str) -> FrequencyClass:
    rate: Decimal = require_decimal(
        model.min_train_trades_per_365d, f"{label}.min_train_trades_per_365d"
    )
    return FrequencyClass(
        name=model.name,
        min_train_trades_per_365d=rate,
        min_validation_trades_per_fold=model.min_validation_trades_per_fold,
        min_validation_trades_total=model.min_validation_trades_total,
    )


def _standard_of(
    model: _EvaluationStandardModel, path: Path, research_until: UtcTime
) -> EvaluationStandard:
    """評価基準の群を読み、検査（D09 §6.1 の 1〜3・5〜7、§7.1 の E1〜E5）を当てる。"""
    label = "evaluation_standard"
    try:
        purpose = _member(StandardPurpose, model.purpose, f"{label}.purpose")
        split = _split_of(model.split, f"{label}.split")
        selection = SelectionRule(
            metric=_member(MetricId, model.selection.metric, f"{label}.selection.metric"),
            direction=_member(
                SelectionDirection, model.selection.direction, f"{label}.selection.direction"
            ),
            eligibility=tuple(
                _condition_of(item, f"{label}.selection.eligibility[{index}]")
                for index, item in enumerate(model.selection.eligibility)
            ),
        )
        validation = ValidationRule(
            fold_floors=tuple(
                _condition_of(item, f"{label}.validation.fold_floors[{index}]")
                for index, item in enumerate(model.validation.fold_floors)
            ),
            aggregate=tuple(
                _aggregate_of(item, f"{label}.validation.aggregate[{index}]")
                for index, item in enumerate(model.validation.aggregate)
            ),
        )
        sufficiency = SufficiencyRule(
            classes=tuple(
                _frequency_class_of(item, f"{label}.sufficiency.classes[{index}]")
                for index, item in enumerate(model.sufficiency.classes)
            )
        )
        # 検査6・7（と生成の後の不変条件2・3）。fold は記録票を作るときにもう一度同じ規則で
        # 生成する（同じ規則からは同じ fold が出る。D09 §6.1）。
        folds_for_policy(split, research_until=research_until)
        return EvaluationStandard(
            purpose=purpose,
            split=split,
            selection=selection,
            validation=validation,
            sufficiency=sufficiency,
        )
    except ConfigError as exc:
        raise ConfigError(f"{path}: 研究ポリシーの評価基準の誤り: {exc}") from exc
    except SplitStandardViolation as exc:
        raise ConfigError(f"{path}: 研究ポリシーの期間分割の標準規則の誤り: {exc}") from exc
    except KernelValueError as exc:
        raise ConfigError(f"{path}: 研究ポリシーの評価基準として成立しない: {exc}") from exc


def _model_for(payload: dict[str, Any]) -> type[_ResearchPolicyV1Model]:
    """版の番号から、そのファイルが持つキーの形を選ぶ（D09 §10.9）。

    版が整数として読めない場合は版 1 の形で検証させ、型の誤りとして報告させる。
    """
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        return _ResearchPolicyV1Model
    if version >= _FIRST_VERSION_WITH_STANDARD:
        return _ResearchPolicyV3Model
    if version >= _FIRST_VERSION_WITH_TRIAL_LIMIT:
        return _ResearchPolicyV2Model
    return _ResearchPolicyV1Model


def load_research_policy(
    path: Path,
    *,
    policy_id: str,
    version: int,
    text: str | None = None,
    research_until: UtcTime = INITIAL_ACCESS_BOUNDARIES.research_until,
) -> ResearchPolicy:
    """研究ポリシーファイルを読む（D07 §20.2、D09 §10.9）。`text` は `load_yaml_mapping` と同じ。

    読んだファイルの `id` / `version` が、実験設定の版参照 `{policy_id, version}` と一致する
    ことを確かめる。`research_until` は研究履歴の期間境界（D03 §3.8）で、評価基準の評価範囲の
    検査（D09 §6.1 の検査6）に使う。
    """
    payload = load_yaml_mapping(path, text=text)
    model = validate(_model_for(payload), payload, path)
    require_schema_version(model.schema_version, RESEARCH_POLICY_SCHEMA_VERSION, path)
    if (model.id, model.version) != (policy_id, version):
        raise ConfigError(
            f"{path}: 研究ポリシーの id と版が {model.id} v{model.version} だが、実験設定は"
            f" {policy_id} v{version} を指している（D07 §20.2）"
        )
    limits = model.complexity_limits
    # ダイジェストの入力: 研究ポリシーファイルのすべての値の項目（D07 §20.2 v2.7、D09 §10.9）。
    # 版 1 の項目の形は変えない（版 1 のダイジェストを保ち、段階4 の記録票の検査 P3 を通す）。
    resolved: dict[str, object] = {
        "schema_version": model.schema_version,
        "id": model.id,
        "version": model.version,
        "complexity_limits": {
            "component_kinds": limits.component_kinds,
            "instances": limits.instances,
            "parameters": limits.parameters,
            "decision_outputs": limits.decision_outputs,
        },
    }
    trial_limit: int | None = None
    standard: EvaluationStandard | None = None
    if isinstance(model, _ResearchPolicyV2Model):
        trial_limit = model.search_limits.trials
        resolved["search_limits"] = {"trials": trial_limit}
    if isinstance(model, _ResearchPolicyV3Model):
        standard = _standard_of(model.evaluation_standard, path, research_until)
        resolved["evaluation_standard"] = standard
    try:
        return ResearchPolicy(
            policy_id=model.id,
            version=model.version,
            digest=digest(resolved),
            limits=ComplexityLimits(
                component_kinds=limits.component_kinds,
                instances=limits.instances,
                parameters=limits.parameters,
                decision_outputs=limits.decision_outputs,
            ),
            trial_limit=trial_limit,
            evaluation_standard=standard,
        )
    except KernelValueError as exc:
        raise ConfigError(f"{path}: 研究ポリシーとして成立しない: {exc}") from exc


# --- 版の登録簿（D09 §10.9）----------------------------------------------------


def load_research_policy_registry(
    path: Path, *, text: str | None = None
) -> tuple[RegistryEntry, ...]:
    """版の登録簿を読む（D09 §10.9 の照合の (1)）。

    - 主キー `(id, version)` の要素が2つあれば、登録簿そのものを設定の誤りとして拒否する。
    - 評価基準の群を持つ版（版 3 以上）の要素は `purpose` が必須、版 1・2 の要素は `purpose` を
      書けば登録簿の誤り（Q33 決定）。
    - `digest` は研究ポリシーのダイジェストの 16 進 64 文字（`sha256`）。
    """
    if text is None and not path.is_file():
        raise ConfigError(
            f"研究ポリシーの版の登録簿 {path} が無い。版は登録簿に載ったものだけを使える"
            "（D09 §10.9。2026-09-29 の人間の決定 Q8）"
        )
    payload = load_yaml_mapping(path, text=text)
    model = validate(_RegistryModel, payload, path)
    require_schema_version(model.schema_version, RESEARCH_POLICY_REGISTRY_SCHEMA_VERSION, path)
    entries: list[RegistryEntry] = []
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(model.entries):
        label = f"{path}: entries[{index}]"
        key = (item.id, item.version)
        if key in seen:
            raise ConfigError(
                f"{label}: 研究ポリシー {item.id} v{item.version} の要素が2つある。登録簿の主キーは"
                " (id, version)（D09 §10.9）"
            )
        seen.add(key)
        has_purpose = "purpose" in item.model_fields_set
        purpose: StandardPurpose | None = None
        if item.version >= _FIRST_VERSION_WITH_STANDARD:
            if not has_purpose or item.purpose is None:
                raise ConfigError(
                    f"{label}: 評価基準の群を持つ版（版 {_FIRST_VERSION_WITH_STANDARD} 以上）"
                    "の要素は `purpose`（STANDARD / MECHANISM_CHECK）が必須"
                    "（D09 §10.9。Q33 決定）"
                )
            purpose = _member(StandardPurpose, item.purpose, f"{label}.purpose")
        elif has_purpose:
            raise ConfigError(
                f"{label}: 版 {item.version} は評価基準の群を持たないので `purpose` を書かない"
                "（D09 §10.9。Q33 決定）"
            )
        try:
            entries.append(
                RegistryEntry(
                    policy_id=item.id,
                    version=item.version,
                    digest=ContentDigest(algorithm="sha256", hex=item.digest),
                    purpose=purpose,
                )
            )
        except KernelValueError as exc:
            raise ConfigError(f"{label}: 登録簿の要素として成立しない: {exc}") from exc
    return tuple(entries)


def verify_registered(
    policy: ResearchPolicy, entries: Sequence[RegistryEntry], *, registry_path: Path
) -> RegistryEntry:
    """読んだ研究ポリシーを版の登録簿と照合する（D09 §10.9 の照合の (2)〜(5)）。

    (2) 版参照 `(id, version)` の要素を引き、(3) 無ければ拒否（未登録の版は使えない）、(4) あっても
    `digest` がファイルから計算した値と違えば拒否、(5) 評価基準の群を持つ版で要素の `purpose` が
    ファイルの `evaluation_standard.purpose` と違えば拒否する。どれも設定の誤り（終了コード 2）。
    登録簿へ書き足すことはしない。
    """
    entry = next(
        (
            item
            for item in entries
            if (item.policy_id, item.version) == (policy.policy_id, policy.version)
        ),
        None,
    )
    if entry is None:
        raise ConfigError(
            f"{registry_path}: 研究ポリシー {policy.policy_id} v{policy.version} は登録簿に無い。"
            "未登録の版は使えない（D09 §10.9。2026-09-29 の人間の決定 Q8）。版を足すときは、"
            "ファイルと同じコミットで登録簿へ1行足す"
        )
    if entry.digest != policy.digest:
        raise ConfigError(
            f"{registry_path}: 研究ポリシー {policy.policy_id} v{policy.version} の中身が登録簿の"
            f" ダイジェスト {entry.digest.hex} と違う（ファイルから計算した値は"
            f" {policy.digest.hex}）。版を上げずに中身を変えない。直すときは新しい版を登録する"
            "（D09 §10.9。Q8 決定）"
        )
    standard = policy.evaluation_standard
    if standard is not None and entry.purpose is not standard.purpose:
        registered = None if entry.purpose is None else entry.purpose.value
        raise ConfigError(
            f"{registry_path}: 研究ポリシー {policy.policy_id} v{policy.version} の用途が、"
            f"登録簿では {registered}、ファイルでは {standard.purpose.value} で食い違う"
            "（D09 §10.9 の照合 (5)。Q33 決定）"
        )
    return entry
