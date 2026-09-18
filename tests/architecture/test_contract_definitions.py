"""import-linter 契約の書き方そのものを固定する回帰テスト。

`lint-imports` は契約が vacuous（検査対象が空、または区切り記号の意味を取り違えていて
違反を検出しない）でも成功するため、契約定義の側にも検査を置く。

背景: import-linter の layers では、同じ段に並べた層の区切りが
`:` = 相互 import を許可、`|` = 独立（相互 import を禁止）を意味する。
backtest の中間4層は設計上（全体計画書 §3.3）相互 import を禁止しているので
`|` でなければならない。`:` にすると違反を検出しなくなる。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

BACKTEST_MIDDLE_LAYER = "admission | execution | portfolio | trace"


def _contracts() -> list[dict[str, Any]]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    contracts = config["tool"]["importlinter"]["contracts"]
    assert isinstance(contracts, list)
    return contracts


def _contract_by_name_prefix(prefix: str) -> dict[str, Any]:
    matches = [c for c in _contracts() if str(c.get("name", "")).startswith(prefix)]
    assert len(matches) == 1, f"expected exactly one contract starting with {prefix!r}"
    return matches[0]


def test_backtest_middle_layers_are_independent() -> None:
    """L2c の中間4層は `|`（独立）で区切る。`:` は相互 import を許してしまう。"""
    layers = _contract_by_name_prefix("L2c:")["layers"]

    assert layers == ["application", "engine", BACKTEST_MIDDLE_LAYER, "domain"]

    middle = layers[2]
    assert ":" not in middle, (
        "backtest の admission / execution / portfolio / trace は独立でなければならない。"
        "`:` は同一層内の相互 import を許可するため、違反を検出できなくなる。"
    )
