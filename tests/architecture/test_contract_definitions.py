"""import-linter 契約の定義そのものを固定する回帰テスト（D01 v2 §6・§13）。

`lint-imports` は契約が vacuous（検査対象が空、区切り記号の意味を取り違えている、
そもそも契約が存在しない）でも成功するため、契約定義の側にも検査を置く。

固定する事項:

- L2c の中間4層が `|`（独立）区切りであること。
  import-linter の layers では `:` = 相互 import を許可、`|` = 独立（相互 import 禁止）。
  backtest の中間4層は設計上（D01 §3.3）相互 import を禁止しているので `|` でなければ
  ならず、`:` にすると違反を検出しなくなる。
- 全 layers 契約が `exhaustive = true` であること。
  未宣言のサブパッケージを追加したときに検出できなくなるため。
- F1a〜F8 の forbidden 契約がすべて存在すること。契約が消えても lint-imports は成功する。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

BACKTEST_MIDDLE_LAYER = "admission | execution | portfolio | trace"

#: D01 v2 §6 の forbidden 契約。名前は CI 出力で参照するため変更しない。
EXPECTED_FORBIDDEN_CONTRACTS = [
    "F1a: marketdata.adapters is not importable outside marketdata and app",
    "F1b: evaluation.adapters is not importable outside evaluation and app",
    "F2: strategy may use marketdata.domain only",
    "F3: backtest does not depend on the strategy component catalog",
    "F4: evaluation may use backtest.domain / trace only",
    "F5a: no dataframe / serialization libraries outside adapters and app",
    "F5b: no numpy outside adapters, app and strategy.catalog",
    "F6: backtest may use marketdata.domain only",
    "F7: evaluation may use marketdata.domain only",
    "F8: evaluation may use strategy.declarations / records / compiler only",
]

#: D01 v2 §6 の layers 契約。
EXPECTED_LAYERS_CONTRACT_PREFIXES = ["L1:", "L2a:", "L2b:", "L2c:"]


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


def test_all_layers_contracts_are_exhaustive() -> None:
    """layers 契約は exhaustive。未宣言のサブパッケージ追加を検出するため（D01 §6）。"""
    layers_contracts = [c for c in _contracts() if c.get("type") == "layers"]

    names = sorted(str(c["name"]) for c in layers_contracts)
    assert len(layers_contracts) == len(EXPECTED_LAYERS_CONTRACT_PREFIXES), (
        f"expected {len(EXPECTED_LAYERS_CONTRACT_PREFIXES)} layers contracts, got {names}"
    )

    for prefix in EXPECTED_LAYERS_CONTRACT_PREFIXES:
        contract = _contract_by_name_prefix(prefix)
        assert contract.get("exhaustive") is True, (
            f"{contract['name']} must set exhaustive = true, "
            "otherwise a new undeclared subpackage goes unchecked"
        )


def test_all_forbidden_contracts_are_present() -> None:
    """F1a〜F8 が揃っていること。契約が消えても lint-imports は成功してしまう。"""
    actual = {str(c["name"]) for c in _contracts() if c.get("type") == "forbidden"}

    missing = [name for name in EXPECTED_FORBIDDEN_CONTRACTS if name not in actual]
    assert not missing, f"missing forbidden contracts: {missing}"


def test_external_packages_are_analysed() -> None:
    """F5a / F5b が外部ライブラリを検査できるよう include_external_packages を立てる。"""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    assert config["tool"]["importlinter"]["include_external_packages"] is True
