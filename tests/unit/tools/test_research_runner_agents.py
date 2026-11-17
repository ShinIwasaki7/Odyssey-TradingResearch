"""実行器が起動する作業役の定義（`.claude/agents/`）の形の試験（順 11 の PR 8b。D25 §5.1・§5.2）。

- 定義が 3 つあり、前置き（名前・説明・モデル・道具）が D25 §5.1・§5.2 のとおり。
- 実行器が起動する役の名前（`runner.py` の `_worker` の呼び出し）が、どれかの定義に当たる。
- 各定義に、プロジェクトの絶対の制約の文と、D25 §5.1 の出力のファイルがある。
- `model-routing` の作業役の表（§1.1）のモデルが定義と同じ。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
AGENTS_DIR: Final = REPO_ROOT / ".claude" / "agents"
RUNNER: Final = REPO_ROOT / "tools" / "ops" / "research_runner" / "runner.py"
MODEL_ROUTING: Final = REPO_ROOT / ".claude" / "skills" / "model-routing" / "SKILL.md"

#: D25 §5.1 の表（定義の名前 → モデル）。確かめ役は Sonnet（D25 の仮置き 16）。
MODELS: Final = {"ideator": "opus", "analyst": "opus", "verifier": "sonnet"}

#: 実行器の役（`WorkerRequest.role`）→ 定義の名前。分析役は段 5・段 7 の 2 つの役を持つ
#: （D25 §5.1）。
AGENT_OF_ROLE: Final = {
    "ideator": "ideator",
    "analyst_change": "analyst",
    "analyst_report": "analyst",
    "verifier": "verifier",
}

#: 作業役に許す道具（D25 §5.2: 束の中の読み取りと out/ への書き込みだけ。Bash・ネットワーク・
#: ほかのエージェントの起動は許さない）。
TOOLS: Final = {"Read", "Glob", "Grep", "Write"}

#: D25 §5.1 の出力（束の `out/`）。
OUTPUTS: Final = {
    "ideator": ("out/cards/",),
    "analyst": (
        "out/analysis_memo.md",
        "out/change.yaml",
        "out/comparison.md",
        "out/report_text.yaml",
    ),
    "verifier": ("out/verdict.yaml",),
}

#: プロジェクトの絶対の制約（各定義に必ず入れる文）。第 1 項は ADR-0041 第2項の改定文の形。
CONSTRAINTS: Final = (
    "研究期間（2017〜2023）の結果だけ",
    "封印データと、最終検証・前向き検証の結果は読まない",
    "報告に成績の数値を書かない",
    "価格を作らない",
    "原データと承認済みの snapshot を上書きしない",
    "2024 年以降と封印データを開かない",
    "研究の run は 2017〜2023 だけ",
    "取得・AWS・費用・外部への問い合わせをしない",
    "settings と CLAUDE.md を編集しない",
    "安全・権限の拒否を迂回しない",
    "コマンドを実行しない",
)


def _definition(name: str) -> tuple[dict[str, Any], str]:
    text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    matched = re.match(r"\A---\n(?P<front>.*?)\n---\n(?P<body>.*)\Z", text, re.DOTALL)
    assert matched is not None, f"{name}.md に前置き（--- で囲んだ YAML）が無い"
    front = yaml.safe_load(matched["front"])
    assert isinstance(front, dict)
    return front, matched["body"]


def _runner_roles() -> set[str]:
    """`runner.py` が `self._worker(ctx, <カード>, "<役>", ...)` で起動する役の名前。"""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    roles: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_worker"
            and len(node.args) >= 3
            and isinstance(node.args[2], ast.Constant)
            and isinstance(node.args[2].value, str)
        ):
            roles.add(node.args[2].value)
    return roles


def test_the_runner_launches_only_roles_that_have_a_definition() -> None:
    roles = _runner_roles()
    assert roles == set(AGENT_OF_ROLE)
    assert set(AGENT_OF_ROLE.values()) == set(MODELS)
    for agent in set(AGENT_OF_ROLE.values()):
        assert (AGENTS_DIR / f"{agent}.md").is_file()


@pytest.mark.parametrize("name", sorted(MODELS))
def test_front_matter_matches_the_design(name: str) -> None:
    front, _ = _definition(name)
    assert set(front) == {"name", "description", "model", "tools"}
    assert front["name"] == name
    assert isinstance(front["description"], str) and front["description"].strip()
    assert front["model"] == MODELS[name]
    tools = {item.strip() for item in str(front["tools"]).split(",")}
    assert tools == TOOLS


@pytest.mark.parametrize("name", sorted(MODELS))
def test_each_definition_states_the_absolute_constraints(name: str) -> None:
    _, body = _definition(name)
    missing = [sentence for sentence in CONSTRAINTS if sentence not in body]
    assert missing == []


@pytest.mark.parametrize("name", sorted(MODELS))
def test_each_definition_names_its_outputs_and_the_bundle(name: str) -> None:
    _, body = _definition(name)
    for output in OUTPUTS[name]:
        assert output in body
    assert "manifest.json" in body
    assert "束の外を探さない" in body


def test_the_analyst_knows_both_stages_it_is_launched_for() -> None:
    _, body = _definition("analyst")
    for role, agent in AGENT_OF_ROLE.items():
        if agent == "analyst":
            assert f"`{role}`" in body


def test_model_routing_lists_the_workers_with_the_same_models() -> None:
    text = MODEL_ROUTING.read_text(encoding="utf-8")
    assert "### 1.1 実行器が起動する作業役" in text
    label = {"opus": "Opus", "sonnet": "Sonnet"}
    for name, model in MODELS.items():
        row = next((line for line in text.splitlines() if f"| `{name}` |" in line), None)
        assert row is not None, f"model-routing §1.1 に {name} の行が無い"
        assert f"| {label[model]}" in row
    assert "司令塔はこの 3 つを起動しない" in text
