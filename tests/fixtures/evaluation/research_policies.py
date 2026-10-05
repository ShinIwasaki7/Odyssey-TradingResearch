"""試験用の研究ポリシーと版の登録簿をテストの中のリポジトリの根に作る（D09 §10.9・§13、D08 §2.3）。

テストは評価基準の値（D09 §17.3 の Q19〜Q32。未決定）に依存させず、試験用の研究ポリシーと
登録簿を**テストの中のリポジトリの根**に作って使う。リポジトリの `registry.yaml` には試験用の
版を載せない（D09 §10.9）。

- `policy_v3_text`: 試験用の版 3 以上の研究ポリシーの本文（評価基準の群を持つ形。値は人工
  データ向けの仮の値で、研究ポリシーではない）。キーごとに差し替えられる。
- `append_registry_entry`: 作業場の登録簿に1行足す（無ければ作る）。ダイジェストは作業場の
  研究ポリシーファイルを読込と同じ関数で読んで計算する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from odyssey_fx.app.config.research_policy import (
    load_research_policy,
    research_policy_path,
    research_policy_registry_path,
)
from odyssey_fx.common.time import UtcTime

__all__ = [
    "DEFAULT_STANDARD_LINES",
    "append_registry_entry",
    "policy_v3_text",
    "write_policy",
]

#: 試験用の評価基準の群の既定の行（`evaluation_standard:` の下。字下げ込み）。キー名で差し替える。
DEFAULT_STANDARD_LINES: Final[dict[str, str]] = {
    "purpose": "  purpose: MECHANISM_CHECK",
    "split": (
        "  split:\n"
        '    range: {start: "2015-01-04T22:00:00Z", end: "2015-01-16T22:00:00Z"}\n'
        '    train_length: "4d"\n'
        '    validation_length: "2d"\n'
        "    window: ROLLING\n"
        '    purge: "0s"\n'
        "    min_folds: 2"
    ),
    "selection": (
        "  selection:\n    metric: NET_RETURN_RATE\n    direction: MAXIMIZE\n    eligibility: []"
    ),
    "validation": (
        "  validation:\n"
        "    fold_floors:\n"
        '      - {metric: MAX_DRAWDOWN_MTM_RATE, comparator: LE, threshold: "0.2"}\n'
        "    aggregate:\n"
        '      - {metric: NET_RETURN_RATE, statistic: MEDIAN, comparator: GE, threshold: "0"}'
    ),
    "sufficiency": (
        "  sufficiency:\n"
        "    classes:\n"
        '      - {name: HIGH, min_train_trades_per_365d: "50",'
        " min_validation_trades_per_fold: 5, min_validation_trades_total: 20}\n"
        '      - {name: LOW, min_train_trades_per_365d: "0",'
        " min_validation_trades_per_fold: 1, min_validation_trades_total: 5}"
    ),
}


def policy_v3_text(
    *,
    policy_id: str = "research_policy",
    version: int = 3,
    trials: int = 100,
    replace: dict[str, str | None] | None = None,
) -> str:
    """試験用の版 3 以上の研究ポリシーの本文。

    `replace` はキー名（`purpose` / `split` / `selection` / `validation` / `sufficiency`）から
    差し替える行への対応。`None` を渡すとそのキーを書かない（省略の拒否を確かめるため）。
    """
    lines = dict(DEFAULT_STANDARD_LINES)
    for key, value in (replace or {}).items():
        if value is None:
            lines.pop(key, None)
        else:
            lines[key] = value
    body = "\n".join(lines.values())
    return (
        "# テスト用。研究ポリシーではない（評価基準の値は人工データ向けの仮の値）。\n"
        "schema_version: 1\n"
        f"id: {policy_id}\n"
        f"version: {version}\n"
        "complexity_limits:\n"
        "  component_kinds: 30\n"
        "  instances: 36\n"
        "  parameters: 33\n"
        "  decision_outputs: 18\n"
        "search_limits:\n"
        f"  trials: {trials}\n"
        "evaluation_standard:\n"
        f"{body}\n"
    )


def write_policy(repo: Path, policy_id: str, version: int, text: str) -> Path:
    """作業場に研究ポリシーファイルを書く（置き場は `research_policy_path` と同じ規則）。"""
    path = research_policy_path(repo, policy_id, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def append_registry_entry(
    repo: Path,
    policy_id: str,
    version: int,
    *,
    purpose: str | None = None,
    digest_hex: str | None = None,
    research_until: UtcTime | None = None,
) -> str:
    """作業場の登録簿に1行足し、足した行を返す（無ければ作る。D09 §10.9）。

    ダイジェストは作業場の研究ポリシーファイルから計算する（`digest_hex` で上書きできる）。
    `purpose` を省くと、評価基準の群を持つ版ではファイルの用途を写し、版 1・2 では書かない。
    `research_until` は計算のためにファイルを読むときの研究履歴の期間境界（省くと読込の既定）。
    評価範囲が既定の境界を超える版（読込が拒否する版。D09 §6.1 の検査6）を登録簿に載せるときに
    渡す（ダイジェストの入力に境界は入らない）。
    """
    registry = research_policy_registry_path(repo)
    registry.parent.mkdir(parents=True, exist_ok=True)
    path = research_policy_path(repo, policy_id, version)
    if research_until is None:
        policy = load_research_policy(path, policy_id=policy_id, version=version)
    else:
        policy = load_research_policy(
            path, policy_id=policy_id, version=version, research_until=research_until
        )
    digest = digest_hex or policy.digest.hex
    if purpose is None and policy.evaluation_standard is not None:
        purpose = policy.evaluation_standard.purpose.value
    fields = f"id: {policy_id}, version: {version}, digest: {digest!r}"
    if purpose is not None:
        fields += f", purpose: {purpose}"
    line = f"  - {{{fields}}}\n"
    if not registry.is_file():
        registry.write_text(
            "# テスト用の登録簿（テストの中のリポジトリの根に作る。D09 §10.9）\n"
            "schema_version: 1\nentries:\n",
            encoding="utf-8",
        )
    with registry.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return line
