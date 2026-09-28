"""研究ポリシーファイルの読込（D07 §20.2、2026-09-25 の人間の決定5・Q7 決定）。

`configs/policies/research/<id>_v<version>.yaml` を読み、`ResearchPolicy`（上限4件と版参照）へ
変換する。**検査の規則はコードに置き、ファイルは上限の値だけを持つ**（D07 §20.2）。

実験設定の書式 v2 は研究ポリシーを版参照 `{id, version}` だけで指す（D07 §18.2）。ファイルの
置き場は D07 §20.2 の例（`research_policy_v1.yaml` に `id: research_policy`・`version: 1`）と
同じ規則で `<id>_v<version>.yaml` とし、読んだファイルの `id` / `version` が参照と一致する
ことを確かめる（食い違えば設定の誤り）。

版参照のダイジェストは**解決済みの内容**（形式版・`id`・版・上限4件）のダイジェストである
（D07 §20.2）。上限だけを書き換えたポリシーは別のダイジェストになるので、同じ実験の同じ版を
その書き換えたポリシーで再実行すると、記録票の識別子が変わって検査 P3 が拒否する。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.domain.research_policy import ComplexityLimits, ResearchPolicy

__all__ = [
    "RESEARCH_POLICY_DIRECTORY",
    "RESEARCH_POLICY_SCHEMA_VERSION",
    "load_research_policy",
    "research_policy_path",
]

#: 研究ポリシーファイルの形式版（D07 §20.2）。
RESEARCH_POLICY_SCHEMA_VERSION: Final = 1

#: 研究ポリシーファイルの置き場（リポジトリの根からの相対パス。D01 §10.1 の `policies/`）。
RESEARCH_POLICY_DIRECTORY: Final = Path("configs/policies/research")

#: 研究ポリシーの `id` の字種（ファイル名に使うため、区切り文字や `..` を含めない）。
_POLICY_ID: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")


class _LimitsModel(StrictModel):
    component_kinds: int
    instances: int
    parameters: int
    decision_outputs: int


class _ResearchPolicyModel(StrictModel):
    schema_version: int
    id: str
    version: int
    complexity_limits: _LimitsModel


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


def load_research_policy(
    path: Path,
    *,
    policy_id: str,
    version: int,
    text: str | None = None,
) -> ResearchPolicy:
    """研究ポリシーファイルを読む（D07 §20.2）。`text` は `load_yaml_mapping` と同じ。

    読んだファイルの `id` / `version` が、実験設定の版参照 `{policy_id, version}` と一致する
    ことを確かめる。
    """
    payload = load_yaml_mapping(path, text=text)
    model = validate(_ResearchPolicyModel, payload, path)
    require_schema_version(model.schema_version, RESEARCH_POLICY_SCHEMA_VERSION, path)
    if (model.id, model.version) != (policy_id, version):
        raise ConfigError(
            f"{path}: 研究ポリシーの id と版が {model.id} v{model.version} だが、実験設定は"
            f" {policy_id} v{version} を指している（D07 §20.2）"
        )
    limits = model.complexity_limits
    resolved = {
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
        )
    except KernelValueError as exc:
        raise ConfigError(f"{path}: 研究ポリシーとして成立しない: {exc}") from exc
