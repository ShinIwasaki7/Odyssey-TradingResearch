"""採用審査の規則のファイル・規則の登録簿・申告ファイルの読込（D16 §8・§10.3。仮置き 10）。

- **規則のファイル** `configs/policies/adoption_review/adoption_review_v<版>.yaml`: 費用・約定
  モデルの適合の印と閾値（R1-5〜R1-7 (a)）、割り引いた指標の「仮定に合わない」印の条件
  （仮置き 5）、判定
  できないときの結果（R1-4 (a)）。**検査の規則はコードに置き、ファイルは値だけを持つ**（研究
  ポリシーと同じ。D07 §20.2）。ダイジェストは**解決済みの値**（長さは秒数、閾値は `Decimal`）の
  正規化ダイジェストで、同じ値の書き方の揺れ（`"PT2H"` と `"PT120M"`）では変わらない。
- **規則の登録簿** `configs/policies/adoption_review/registry.yaml`: 版・ダイジェスト・効き始めの
  行（`effective_after`。登録の時点の試行台帳の末尾の行のダイジェスト。版 1 は `null`＝最初から
  効く。R1-10 (a)）。要素は書き換えも削除もしない（研究ポリシーの登録簿と同じ規則。D09 §10.9）。
- **申告ファイル** `research/declarations/<実験の識別子>_<実行番号>.yaml`（R1-8 (a)）: 審査の前に
  司令塔が人間に問うた回答の原文。無い・形が違うときは「申告ファイルの欠け」（保留）として扱う。

どれも成績の数値を持たない（入口の検査は台帳と登録簿だけで行う。仮置き 13）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.scalars import parse_duration, require_decimal
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.evaluation.domain.adoption_review import (
    CostModelRow,
    DeclarationStatus,
    DsrBasis,
    ReviewResult,
    ReviewRules,
    ReviewRulesEntry,
    SwapRule,
    WindowAnchor,
)

__all__ = [
    "ADOPTION_REVIEW_DIRECTORY",
    "ADOPTION_REVIEW_ID",
    "DECLARATION_DIRECTORY",
    "LoadedDeclaration",
    "adoption_review_registry_path",
    "adoption_review_rules_path",
    "declaration_path",
    "load_adoption_review_registry",
    "load_adoption_review_rules",
    "load_declaration",
]

#: 置き場（リポジトリの根からの相対パス。仮置き 10）。
ADOPTION_REVIEW_DIRECTORY: Final = Path("configs/policies/adoption_review")
_REGISTRY_FILE: Final = "registry.yaml"
#: 規則の識別（登録簿が載せてよい唯一の id。R3 の仮置き）。
ADOPTION_REVIEW_ID: Final = "adoption_review"
#: 申告ファイルの置き場（R1-8 (a)。D16 §8.1 の案）。
DECLARATION_DIRECTORY: Final = Path("research/declarations")

_SCHEMA_VERSION: Final = 1
_REGISTRY_SCHEMA_VERSION: Final = 1
_DECLARATION_SCHEMA_VERSION: Final = 1
_HEX64: Final = re.compile(r"[0-9a-f]{64}")


class _WindowModel(StrictModel):
    anchor: str
    length: str


class _ConcentrationModel(StrictModel):
    window: _WindowModel
    max_ratio: str


class _FixedCostModel(StrictModel):
    max_share: str


class _SwapModel(StrictModel):
    rule: str


class _ConversionModel(StrictModel):
    require_account_currency: bool


class _CostModelFitModel(StrictModel):
    spread_session_concentration: _ConcentrationModel
    fixed_cost_share: _FixedCostModel
    swap_rollover: _SwapModel
    currency_conversion: _ConversionModel
    declared_fits: list[str]


class _AssumptionModel(StrictModel):
    min_trades: int
    max_flat_day_share: str


class _DeflatedModel(StrictModel):
    basis: str
    assumption_flags: _AssumptionModel


class _RulesModel(StrictModel):
    schema_version: int
    id: str
    version: int
    cost_model_fit: _CostModelFitModel
    deflated_sharpe: _DeflatedModel
    undetermined_result: str


class _RegistryEntryModel(StrictModel):
    id: str
    version: int
    digest: str
    effective_after: str | None


class _RegistryModel(StrictModel):
    schema_version: int
    entries: list[_RegistryEntryModel]


class _DeclarationModel(StrictModel):
    schema_version: int
    experiment_id: str
    execution: int
    question: str
    answer: str
    recorded_by: str
    used_outside_viewing: bool


def adoption_review_rules_path(repo_root: Path, version: int) -> Path:
    """規則のファイルの位置（`<根>/configs/policies/adoption_review/adoption_review_v<版>.yaml`）。"""
    return Path(repo_root) / ADOPTION_REVIEW_DIRECTORY / f"{ADOPTION_REVIEW_ID}_v{version}.yaml"


def adoption_review_registry_path(repo_root: Path) -> Path:
    """規則の登録簿の位置。"""
    return Path(repo_root) / ADOPTION_REVIEW_DIRECTORY / _REGISTRY_FILE


def declaration_path(repo_root: Path, experiment_id: ExperimentId, execution: int) -> Path:
    """申告ファイルの位置（`<根>/research/declarations/<実験の識別子>_<実行番号>.yaml`）。"""
    return Path(repo_root) / DECLARATION_DIRECTORY / f"{experiment_id}_{execution}.yaml"


def _member[EnumT: (WindowAnchor, SwapRule, DsrBasis, ReviewResult, CostModelRow)](
    enum_type: type[EnumT], value: str, label: str
) -> EnumT:
    try:
        return enum_type(value)
    except ValueError:
        allowed = [item.value for item in enum_type]
        raise ConfigError(f"{label}: {value!r} は {allowed} のどれでもない") from None


def load_adoption_review_rules(
    path: Path, *, version: int, text: str | None = None
) -> tuple[ReviewRules, ContentDigest]:
    """規則のファイルを読み、規則と解決済みの値のダイジェストを返す（D16 §10.3）。

    ファイルの `id` / `version` が `adoption_review` と指定の版でなければ設定の誤り。無い・
    読めない・形が違うときも設定の誤り（`ConfigError`。終了コード 2。D16 §3.1 の順 8）。
    """
    payload = load_yaml_mapping(path, text=text)
    model = validate(_RulesModel, payload, path)
    require_schema_version(model.schema_version, _SCHEMA_VERSION, path)
    if (model.id, model.version) != (ADOPTION_REVIEW_ID, version):
        raise ConfigError(
            f"{path}: 規則の id と版が {model.id} v{model.version} だが、{ADOPTION_REVIEW_ID}"
            f" v{version} を読もうとした（D16 §10.3）"
        )
    fit = model.cost_model_fit
    concentration = fit.spread_session_concentration
    length = parse_duration(concentration.window.length, f"{path}: window.length")
    flags = model.deflated_sharpe.assumption_flags
    try:
        rules = ReviewRules(
            rules_id=model.id,
            version=model.version,
            window_anchor=_member(WindowAnchor, concentration.window.anchor, f"{path}: anchor"),
            window_length=length,
            max_concentration_ratio=require_decimal(concentration.max_ratio, f"{path}: max_ratio"),
            max_fixed_cost_share=require_decimal(
                fit.fixed_cost_share.max_share, f"{path}: max_share"
            ),
            swap_rule=_member(SwapRule, fit.swap_rollover.rule, f"{path}: swap_rollover.rule"),
            require_account_currency=fit.currency_conversion.require_account_currency,
            declared_fits=tuple(
                _member(CostModelRow, item, f"{path}: declared_fits") for item in fit.declared_fits
            ),
            dsr_basis=_member(DsrBasis, model.deflated_sharpe.basis, f"{path}: basis"),
            dsr_min_trades=flags.min_trades,
            dsr_max_flat_day_share=require_decimal(
                flags.max_flat_day_share, f"{path}: max_flat_day_share"
            ),
            undetermined_result=_member(
                ReviewResult, model.undetermined_result, f"{path}: undetermined_result"
            ),
        )
    except KernelValueError as exc:
        raise ConfigError(f"{path}: 審査の規則として成立しない: {exc}") from exc
    resolved = {
        "schema_version": model.schema_version,
        "id": rules.rules_id,
        "version": rules.version,
        "cost_model_fit": {
            "spread_session_concentration": {
                "window": {
                    "anchor": rules.window_anchor,
                    "length_seconds": int(rules.window_length.total_seconds()),
                },
                "max_ratio": rules.max_concentration_ratio,
            },
            "fixed_cost_share": {"max_share": rules.max_fixed_cost_share},
            "swap_rollover": {"rule": rules.swap_rule},
            "currency_conversion": {"require_account_currency": rules.require_account_currency},
            "declared_fits": rules.declared_fits,
        },
        "deflated_sharpe": {
            "basis": rules.dsr_basis,
            "assumption_flags": {
                "min_trades": rules.dsr_min_trades,
                "max_flat_day_share": rules.dsr_max_flat_day_share,
            },
        },
        "undetermined_result": rules.undetermined_result,
    }
    return rules, digest(resolved)


def load_adoption_review_registry(path: Path) -> tuple[ReviewRulesEntry, ...]:
    """規則の登録簿を読む（D16 §10.3。R1-10 (a)）。

    - 主キー `(id, version)` の重複・`adoption_review` 以外の id・版 1 が無い・版 1 の
      `effective_after` が `null` でない・版 2 以上の `effective_after` が `null` は登録簿の誤り。
    - `digest`・`effective_after` は 16 進 64 文字（`sha256`）。
    """
    if not path.is_file():
        raise ConfigError(f"審査の規則の登録簿 {path} が無い（D16 §10.3）")
    payload = load_yaml_mapping(path)
    model = validate(_RegistryModel, payload, path)
    require_schema_version(model.schema_version, _REGISTRY_SCHEMA_VERSION, path)
    entries: list[ReviewRulesEntry] = []
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(model.entries):
        label = f"{path}: entries[{index}]"
        key = (item.id, item.version)
        if key in seen:
            raise ConfigError(f"{label}: {item.id} v{item.version} の要素が 2 つある")
        seen.add(key)
        if item.id != ADOPTION_REVIEW_ID:
            raise ConfigError(f"{label}: id は {ADOPTION_REVIEW_ID} だけを載せる（{item.id!r}）")
        if (item.version == 1) != (item.effective_after is None):
            raise ConfigError(
                f"{label}: 版 1 だけが `effective_after: null`（最初から効く）を持ち、版 2 以上は"
                "登録の時点の試行台帳の末尾の行のダイジェストを持つ（R1-10 (a)）"
            )
        for name, value in (("digest", item.digest), ("effective_after", item.effective_after)):
            if value is not None and not _HEX64.fullmatch(value):
                raise ConfigError(f"{label}: {name} は 16 進 64 文字（{value!r}）")
        entries.append(
            ReviewRulesEntry(
                rules_id=item.id,
                version=item.version,
                digest=ContentDigest.sha256(item.digest),
                effective_after=None
                if item.effective_after is None
                else ContentDigest.sha256(item.effective_after),
            )
        )
    if not any(entry.version == 1 for entry in entries):
        raise ConfigError(f"{path}: 版 1 の要素が無い（D16 §10.3）")
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class LoadedDeclaration:
    """申告ファイルの読込の結果（`MISSING` のとき `file_digest` は `None`、`detail` は理由）。"""

    status: DeclarationStatus
    file_digest: ContentDigest | None
    detail: str


def load_declaration(path: Path, experiment_id: ExperimentId, execution: int) -> LoadedDeclaration:
    """申告ファイルを読む（R1-8 (a)。D16 §8.1）。

    無い・読めない・形が違う（未宣言キー・型・実験の識別子と実行番号の食い違い）ときは
    `MISSING`（保留の理由 `DECLARATION_MISSING`）。読めたらファイルのバイト列の SHA-256 を返す。
    """
    if path.is_symlink() or not path.is_file():
        return LoadedDeclaration(DeclarationStatus.MISSING, None, f"{path} does not exist")
    try:
        data = path.read_bytes()
        model = validate(_DeclarationModel, load_yaml_mapping(path), path)
        require_schema_version(model.schema_version, _DECLARATION_SCHEMA_VERSION, path)
    except (ConfigError, OSError) as exc:
        return LoadedDeclaration(DeclarationStatus.MISSING, None, f"{type(exc).__name__}: {exc}")
    if (model.experiment_id, model.execution) != (str(experiment_id), execution):
        return LoadedDeclaration(
            DeclarationStatus.MISSING,
            None,
            f"{path} declares ({model.experiment_id}, {model.execution}), not the reviewed"
            " execution",
        )
    if not model.question.strip() or not model.answer.strip() or not model.recorded_by.strip():
        return LoadedDeclaration(
            DeclarationStatus.MISSING, None, f"{path} has an empty question / answer / recorder"
        )
    status = (
        DeclarationStatus.DECLARED if model.used_outside_viewing else DeclarationStatus.NOT_DECLARED
    )
    return LoadedDeclaration(status, ContentDigest.sha256(hashlib.sha256(data).hexdigest()), "read")
