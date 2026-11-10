"""前向き検証の規則のファイル・規則の登録簿の読込（D09 §9.11.12 の 1。順 6 の F2）。

- **規則のファイル** `configs/policies/forward_validation/forward_validation_v<版>.yaml`:
  前向き検証の 13 項目（取得の開始時点の参照・固定時点・開始・最終検証の期間・長さ・採点区間・
  固定の延期・判定・申請の前提・予約を解く条件・期間が欠けたときの扱い。ほかに
  `schema_version`・`id`/`version`）。
  **検査の規則はコードに置き、ファイルは値だけを持つ**（研究ポリシー・採用審査の規則と同じ。
  D07 §20.2）。ダイジェストは**解決済みの値**（確率は `Decimal`、時刻は UTC）の正規化ダイジェスト
  で、同じ値の書き方の揺れ（`"0.95"` と `"0.950"`、集合の書く順）では変わらない。
- **規則の登録簿** `configs/policies/forward_validation/registry.yaml`: 版・ダイジェスト・効き始めの
  印（`effective_after`。版 1 は `null`＝最初から効く）。要素は書き換えも削除もしない（採用審査の
  規則の登録簿と同じ規則。D16 §10.3）。版 2 以上の効き始めの印は版 2 を足す PR で決めるので、
  それまで版 2 以上の要素は読込で止まる（F2 の仮置き）。
- **登録済みの読込** `load_registered_forward_validation_rules`: 登録簿に版があり、ファイルの
  ダイジェストが登録簿と一致し、取得の開始時点の参照先（窓の設定ファイルとキー）があって値が空で
  ないことを確かめる（D09 §9.11.12 の 1 の読込の検査。受入テスト F-1・F-2）。

どの誤りも構造の誤り（`ConfigError`）で止まる。固定の記録（`research/candidate_fixes/`）の読込は
要決定 F1-E（決定 75）で別の実装 PR に送った（ここでは置き場の位置だけを持つ）。
成績・価格を読まない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.scalars import require_decimal
from odyssey_fx.app.config.sealed_window import SEALED_WINDOW_PATH, load_sealed_window
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.forward_validation import (
    FORWARD_VALIDATION_ID,
    AcquisitionStartRef,
    DeferralExclusion,
    FinalWindowEnd,
    FinalWindowRule,
    FixDeferralRule,
    FixTimeRule,
    FixWeekTreatment,
    ForwardRulesEntry,
    ForwardValidationRules,
    FrequencySource,
    IncompletePeriodRule,
    JudgmentMethod,
    JudgmentPolicyVersion,
    JudgmentRule,
    LengthMethod,
    LengthRule,
    MinTradesSource,
    OnBoundary,
    PairCombination,
    PairJudgment,
    PeriodEnd,
    Prerequisite,
    ReleaseCondition,
    ScoringRule,
    StartBasis,
    StartRule,
    UsableWindow,
    WarmupLength,
    WarmupRule,
    WeekBoundary,
)
from odyssey_fx.marketdata.domain.access import TRADING_WEEK_ZONE

__all__ = [
    "CANDIDATE_FIXES_DIRECTORY",
    "FORWARD_VALIDATION_DIRECTORY",
    "LoadedForwardValidationRules",
    "candidate_fix_path",
    "forward_validation_registry_path",
    "forward_validation_rules_path",
    "load_forward_validation_registry",
    "load_forward_validation_rules",
    "load_registered_forward_validation_rules",
]

#: 置き場（リポジトリの根からの相対パス。仮置き F1-2）。
FORWARD_VALIDATION_DIRECTORY: Final = Path("configs/policies/forward_validation")
_REGISTRY_FILE: Final = "registry.yaml"
#: 固定の記録の置き場（仮置き F1-2。読込は要決定 F1-E で決めた別の実装 PR）。
CANDIDATE_FIXES_DIRECTORY: Final = Path("research/candidate_fixes")

_SCHEMA_VERSION: Final = 1
_REGISTRY_SCHEMA_VERSION: Final = 1
#: 取得の開始時点の参照として、この実装が読める唯一のキー（ADR-0037 第4項の 1）。
_ACQUISITION_START_KEY: Final = "acquisition_start"
#: 週の境目の書き方（`marketdata.domain.access` の取引週の定義と同じ。ADR-0038 第2項）。
_WEEK_BOUNDARIES: Final = {
    (TRADING_WEEK_ZONE.key, "SUNDAY", "17:00"): WeekBoundary.NEW_YORK_SUNDAY_1700,
}


class _AcquisitionStartRefModel(StrictModel):
    path: str
    key: str


class _WeekBoundaryModel(StrictModel):
    zone: str
    weekday: str
    time: str


class _StartModel(StrictModel):
    basis: str
    week_boundary: _WeekBoundaryModel
    on_boundary: str


class _FinalWindowModel(StrictModel):
    start: str
    end: str
    fix_week: str


class _LengthModel(StrictModel):
    method: str
    probability: str
    min_days: int
    min_trades_source: str
    frequency_source: str
    pair_combination: str
    precision_digits: int


class _ScoringModel(StrictModel):
    warmup: str
    warmup_length: str
    period_end: str


class _FixDeferralModel(StrictModel):
    usable_window: str
    exclude: list[str]


class _JudgmentModel(StrictModel):
    method: str
    research_policy: str
    pair_combination: str


class _RulesModel(StrictModel):
    schema_version: int
    id: str
    version: int
    acquisition_start_ref: _AcquisitionStartRefModel
    fix_time: str
    start: _StartModel
    final_window: _FinalWindowModel
    length: _LengthModel
    scoring: _ScoringModel
    fix_deferral: _FixDeferralModel
    judgment: _JudgmentModel
    prerequisite: str
    reservation_release: list[str]
    incomplete_period: str


class _RegistryEntryModel(StrictModel):
    id: str
    version: int
    digest: str
    effective_after: str | None


class _RegistryModel(StrictModel):
    schema_version: int
    entries: list[_RegistryEntryModel]


@dataclass(frozen=True, slots=True)
class LoadedForwardValidationRules:
    """登録済みの規則の読込の結果（規則・ダイジェスト・登録簿の要素・取得の開始時点の値）。"""

    rules: ForwardValidationRules
    digest: ContentDigest
    entry: ForwardRulesEntry
    acquisition_start: UtcTime


def forward_validation_rules_path(repo_root: Path, version: int) -> Path:
    """規則のファイルの位置（`<根>/configs/policies/forward_validation/forward_validation_v<版>.yaml`）。"""
    name = f"{FORWARD_VALIDATION_ID}_v{version}.yaml"
    return Path(repo_root) / FORWARD_VALIDATION_DIRECTORY / name


def forward_validation_registry_path(repo_root: Path) -> Path:
    """規則の登録簿の位置。"""
    return Path(repo_root) / FORWARD_VALIDATION_DIRECTORY / _REGISTRY_FILE


def candidate_fix_path(
    repo_root: Path, experiment_id: ExperimentId, execution: int, fix_no: int
) -> Path:
    """固定の記録の位置（`<根>/research/candidate_fixes/<experiment_id>/<execution>/<fix_no>.yaml`）。"""
    return (
        Path(repo_root) / CANDIDATE_FIXES_DIRECTORY / str(experiment_id) / str(execution)
    ) / f"{fix_no}.yaml"


def _member[
    EnumT: (
        FixTimeRule,
        StartBasis,
        OnBoundary,
        FinalWindowEnd,
        FixWeekTreatment,
        LengthMethod,
        MinTradesSource,
        FrequencySource,
        PairCombination,
        WarmupRule,
        WarmupLength,
        PeriodEnd,
        UsableWindow,
        DeferralExclusion,
        JudgmentMethod,
        JudgmentPolicyVersion,
        PairJudgment,
        Prerequisite,
        ReleaseCondition,
        IncompletePeriodRule,
    )
](enum_type: type[EnumT], value: str, label: str) -> EnumT:
    try:
        return enum_type(value)
    except ValueError:
        allowed = [item.value for item in enum_type]
        raise ConfigError(f"{label}: {value!r} は {allowed} のどれでもない") from None


def _week_boundary_of(model: _WeekBoundaryModel, label: str) -> WeekBoundary:
    key = (model.zone, model.weekday, model.time)
    boundary = _WEEK_BOUNDARIES.get(key)
    if boundary is None:
        raise ConfigError(
            f"{label}: 週の境目 {key} はこの実装が知る取引週の境目"
            f" {sorted(_WEEK_BOUNDARIES)} のどれでもない（ADR-0038 第2項）"
        )
    return boundary


def _utc(text: str, label: str) -> UtcTime:
    try:
        return UtcTime.parse(text)
    except KernelValueError as exc:
        raise ConfigError(f"{label}: UTC の時刻として読めない: {exc}") from exc


def _sorted_values[EnumT: (DeferralExclusion, ReleaseCondition)](
    items: tuple[EnumT, ...],
) -> list[str]:
    return sorted(item.value for item in items)


def load_forward_validation_rules(
    path: Path, *, version: int, text: str | None = None
) -> tuple[ForwardValidationRules, ContentDigest]:
    """規則のファイルを読み、規則と解決済みの値のダイジェストを返す（D09 §9.11.12 の 1）。

    ファイルの `id` / `version` が `forward_validation` と指定の版でなければ設定の誤り。無い・
    読めない・項目の欠け・知らない項目・型の誤り・値の範囲の外も設定の誤り（`ConfigError`）。
    取得の開始時点の参照先は読まない（`load_registered_forward_validation_rules` が読む）。
    """
    payload = load_yaml_mapping(path, text=text)
    model = validate(_RulesModel, payload, path)
    require_schema_version(model.schema_version, _SCHEMA_VERSION, path)
    if (model.id, model.version) != (FORWARD_VALIDATION_ID, version):
        raise ConfigError(
            f"{path}: 規則の id と版が {model.id} v{model.version} だが、{FORWARD_VALIDATION_ID}"
            f" v{version} を読もうとした（D09 §9.11.12 の 1）"
        )
    start, window, length = model.start, model.final_window, model.length
    scoring, deferral, judgment = model.scoring, model.fix_deferral, model.judgment
    try:
        rules = ForwardValidationRules(
            rules_id=model.id,
            version=model.version,
            acquisition_start_ref=AcquisitionStartRef(
                path=model.acquisition_start_ref.path, key=model.acquisition_start_ref.key
            ),
            fix_time=_member(FixTimeRule, model.fix_time, f"{path}: fix_time"),
            start=StartRule(
                basis=_member(StartBasis, start.basis, f"{path}: start.basis"),
                week_boundary=_week_boundary_of(start.week_boundary, f"{path}: start"),
                on_boundary=_member(OnBoundary, start.on_boundary, f"{path}: start.on_boundary"),
            ),
            final_window=FinalWindowRule(
                start=_utc(window.start, f"{path}: final_window.start"),
                end=_member(FinalWindowEnd, window.end, f"{path}: final_window.end"),
                fix_week=_member(
                    FixWeekTreatment, window.fix_week, f"{path}: final_window.fix_week"
                ),
            ),
            length=LengthRule(
                method=_member(LengthMethod, length.method, f"{path}: length.method"),
                probability=require_decimal(length.probability, f"{path}: length.probability"),
                min_days=length.min_days,
                min_trades_source=_member(
                    MinTradesSource, length.min_trades_source, f"{path}: length.min_trades_source"
                ),
                frequency_source=_member(
                    FrequencySource, length.frequency_source, f"{path}: length.frequency_source"
                ),
                pair_combination=_member(
                    PairCombination, length.pair_combination, f"{path}: length.pair_combination"
                ),
                precision_digits=length.precision_digits,
            ),
            scoring=ScoringRule(
                warmup=_member(WarmupRule, scoring.warmup, f"{path}: scoring.warmup"),
                warmup_length=_member(
                    WarmupLength, scoring.warmup_length, f"{path}: scoring.warmup_length"
                ),
                period_end=_member(PeriodEnd, scoring.period_end, f"{path}: scoring.period_end"),
            ),
            fix_deferral=FixDeferralRule(
                usable_window=_member(
                    UsableWindow, deferral.usable_window, f"{path}: fix_deferral.usable_window"
                ),
                exclude=tuple(
                    _member(DeferralExclusion, item, f"{path}: fix_deferral.exclude")
                    for item in deferral.exclude
                ),
            ),
            judgment=JudgmentRule(
                method=_member(JudgmentMethod, judgment.method, f"{path}: judgment.method"),
                research_policy=_member(
                    JudgmentPolicyVersion,
                    judgment.research_policy,
                    f"{path}: judgment.research_policy",
                ),
                pair_combination=_member(
                    PairJudgment, judgment.pair_combination, f"{path}: judgment.pair_combination"
                ),
            ),
            prerequisite=_member(Prerequisite, model.prerequisite, f"{path}: prerequisite"),
            reservation_release=tuple(
                _member(ReleaseCondition, item, f"{path}: reservation_release")
                for item in model.reservation_release
            ),
            incomplete_period=_member(
                IncompletePeriodRule, model.incomplete_period, f"{path}: incomplete_period"
            ),
        )
    except KernelValueError as exc:
        raise ConfigError(f"{path}: 前向き検証の規則として成立しない: {exc}") from exc
    return rules, digest(_resolved(model.schema_version, rules, start.week_boundary))


def _resolved(
    schema_version: int, rules: ForwardValidationRules, week: _WeekBoundaryModel
) -> dict[str, object]:
    """ダイジェストの材料（解決済みの値。キーはファイルのキーと同じ）。"""
    length = rules.length
    return {
        "schema_version": schema_version,
        "id": rules.rules_id,
        "version": rules.version,
        "acquisition_start_ref": {
            "path": rules.acquisition_start_ref.path,
            "key": rules.acquisition_start_ref.key,
        },
        "fix_time": rules.fix_time,
        "start": {
            "basis": rules.start.basis,
            "week_boundary": {"zone": week.zone, "weekday": week.weekday, "time": week.time},
            "on_boundary": rules.start.on_boundary,
        },
        "final_window": {
            "start": rules.final_window.start,
            "end": rules.final_window.end,
            "fix_week": rules.final_window.fix_week,
        },
        "length": {
            "method": length.method,
            "probability": length.probability,
            "min_days": length.min_days,
            "min_trades_source": length.min_trades_source,
            "frequency_source": length.frequency_source,
            "pair_combination": length.pair_combination,
            "precision_digits": length.precision_digits,
        },
        "scoring": {
            "warmup": rules.scoring.warmup,
            "warmup_length": rules.scoring.warmup_length,
            "period_end": rules.scoring.period_end,
        },
        "fix_deferral": {
            "usable_window": rules.fix_deferral.usable_window,
            "exclude": _sorted_values(rules.fix_deferral.exclude),
        },
        "judgment": {
            "method": rules.judgment.method,
            "research_policy": rules.judgment.research_policy,
            "pair_combination": rules.judgment.pair_combination,
        },
        "prerequisite": rules.prerequisite,
        "reservation_release": _sorted_values(rules.reservation_release),
        "incomplete_period": rules.incomplete_period,
    }


def load_forward_validation_registry(path: Path) -> tuple[ForwardRulesEntry, ...]:
    """規則の登録簿を読む（D09 §9.11.12 の 1。採用審査の規則の登録簿と同じ規則）。

    - 主キー `(id, version)` の重複・`forward_validation` 以外の id・版 1 が無いのは登録簿の誤り。
    - 版 1 の `effective_after` は `null`（最初から効く）。版 2 以上の要素は、効き始めの印を決める
      PR まで登録簿の誤りとして止める（F2 の仮置き）。
    - `digest` は 16 進 64 文字（`sha256`）。
    """
    if not path.is_file():
        raise ConfigError(f"前向き検証の規則の登録簿 {path} が無い（D09 §9.11.12 の 1）")
    payload = load_yaml_mapping(path)
    model = validate(_RegistryModel, payload, path)
    require_schema_version(model.schema_version, _REGISTRY_SCHEMA_VERSION, path)
    entries: list[ForwardRulesEntry] = []
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(model.entries):
        label = f"{path}: entries[{index}]"
        key = (item.id, item.version)
        if key in seen:
            raise ConfigError(f"{label}: {item.id} v{item.version} の要素が 2 つある")
        seen.add(key)
        if item.id != FORWARD_VALIDATION_ID:
            raise ConfigError(f"{label}: id は {FORWARD_VALIDATION_ID} だけを載せる（{item.id!r}）")
        if item.version != 1:
            raise ConfigError(
                f"{label}: 版 {item.version} の効き始めの印はまだ決まっていない（版 2 以上は、"
                "その版を"
                "足す PR で効き始めの印を決めてから載せる。D09 §9.11.9 の 5・仮置き F1-14）"
            )
        if item.effective_after is not None:
            raise ConfigError(f"{label}: 版 1 は `effective_after: null`（最初から効く）を持つ")
        try:
            entry_digest = ContentDigest.sha256(item.digest)
        except KernelValueError:
            raise ConfigError(f"{label}: digest は 16 進 64 文字（{item.digest!r}）") from None
        entries.append(
            ForwardRulesEntry(
                rules_id=item.id, version=item.version, digest=entry_digest, effective_after=None
            )
        )
    if not any(entry.version == 1 for entry in entries):
        raise ConfigError(f"{path}: 版 1 の要素が無い（D09 §9.11.12 の 1）")
    return tuple(entries)


def _acquisition_start_of(ref: AcquisitionStartRef, repo_root: Path, label: str) -> UtcTime:
    """取得の開始時点の参照先を読む（参照先のファイルかキーが無い・値が空なら設定の誤り）。"""
    if Path(ref.path) != SEALED_WINDOW_PATH or ref.key != _ACQUISITION_START_KEY:
        raise ConfigError(
            f"{label}: acquisition_start_ref は {SEALED_WINDOW_PATH.as_posix()} の"
            f" {_ACQUISITION_START_KEY} だけを指せる（ADR-0037 第4項の 1）。"
            f"値: {ref.path} の {ref.key}"
        )
    target = Path(repo_root) / SEALED_WINDOW_PATH
    if not target.is_file():
        raise ConfigError(f"{label}: 参照先の設定ファイル {target} が無い")
    window = load_sealed_window(target)
    if window.acquisition_start is None:
        raise ConfigError(f"{label}: 参照先 {target} の {ref.key} が空（取得の開始時点が未定）")
    return window.acquisition_start


def load_registered_forward_validation_rules(
    repo_root: Path, version: int
) -> LoadedForwardValidationRules:
    """登録済みの版の規則を読む（D09 §9.11.12 の 1 の読込の検査。受入テスト F-1・F-2）。

    登録簿に `(forward_validation, version)` の要素が無い・ファイルのダイジェストが登録簿と違う・
    取得の開始時点の参照先が無いときは設定の誤り（`ConfigError`）。
    """
    root = Path(repo_root)
    registry_path = forward_validation_registry_path(root)
    entries = load_forward_validation_registry(registry_path)
    entry = next((item for item in entries if item.version == version), None)
    if entry is None:
        raise ConfigError(
            f"{registry_path}: {FORWARD_VALIDATION_ID} v{version} は登録されていない"
            "（未登録の版は使えない。D09 §9.11.3 の K-43）"
        )
    path = forward_validation_rules_path(root, version)
    if not path.is_file():
        raise ConfigError(f"前向き検証の規則のファイル {path} が無い")
    rules, rules_digest = load_forward_validation_rules(path, version=version)
    if rules_digest != entry.digest:
        raise ConfigError(
            f"{path}: 規則のファイルのダイジェスト {rules_digest.hex} が登録簿の値"
            f" {entry.digest.hex} と違う（版を上げずに中身を変えない。D09 §9.11.12 の 1）"
        )
    acquisition_start = _acquisition_start_of(rules.acquisition_start_ref, root, str(path))
    return LoadedForwardValidationRules(
        rules=rules, digest=rules_digest, entry=entry, acquisition_start=acquisition_start
    )
