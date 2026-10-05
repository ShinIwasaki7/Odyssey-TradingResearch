"""D09 v0.2 の規則の意味論の行 #9・#10・#17 の読込の部分（D08 §7.4 の対応表。段階5 実装 PR 1）。

1行1テストで名前を付けて固定する（D01 §9、D08 §2.1）。

- #9（`test_9_*`）: 同じ研究ポリシーの版からは、どの実験でも同じ fold が生成される（D09 §6.1）。
- #10（`test_10_*`）: 実験設定に `selection` / `acceptance` を書くと読込で拒否される。取引件数
  （#3）を成績の条件に書いた評価基準は研究ポリシーの読込で拒否される（D09 §5.5 の7・§7.1 の E1）。
- #17 の読込の部分（`test_17_*`）: 用途を省いた研究ポリシーは読込で拒否され（E5）、登録簿の要素
  の `purpose` とファイルの `purpose` が違えば拒否される。「現行の版」は `STANDARD` の要素の最大
  の版で、番号の大きい `MECHANISM_CHECK` の版があっても変わらない（D09 §10.9・§10.11 の2）。
  判定の部分（`MET_IN_MECHANISM_CHECK`）とレポートの部分は後続の実装 PR で足す。

探索の実験（`search_plan: GRID`）の読込は後続の実装 PR が足すので、#9 は同じ版を指す2つの実験
設定から読んだ評価基準の期間分割の標準規則で fold を生成して比べる（fold の生成の入力は標準規則
だけ。D09 §6.1）。試験用の版 3 と登録簿はテストの中のリポジトリの根に作る（D09 §10.9・§13）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.research_policy import (
    load_research_policy_registry,
    research_policy_registry_path,
)
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.evaluation.domain.research_policy import current_standard_version
from odyssey_fx.evaluation.domain.splits import generate_folds
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.evaluation.research_policies import (
    DEFAULT_STANDARD_LINES,
    append_registry_entry,
    policy_v3_text,
    write_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

#: 2つの別の実験設定（遅延シナリオだけが違う。同じ研究ポリシーの版を指させる）。
_EXPERIMENTS = (
    "configs/experiments/strategy_b_t02_d1_2s.yaml",
    "configs/experiments/strategy_b_t02_none.yaml",
)


def _workspace(root: Path) -> Path:
    """正本の設定を写し、試験用の版 3 を置いて作業場の登録簿に載せる。"""
    for relative in (
        "configs/calendars/fx_ny17_v1.yaml",
        "configs/calendars/timeframes_v1.yaml",
        "configs/symbols/USDJPY.yaml",
        "configs/strategies/strategy_b_v1.yaml",
        "configs/policies/research/research_policy_v1.yaml",
        *_EXPERIMENTS,
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative).read_bytes())
    append_registry_entry(root, "research_policy", 1)
    write_policy(root, "research_policy", 3, policy_v3_text())
    append_registry_entry(root, "research_policy", 3)
    for relative in _EXPERIMENTS:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        assert text.count('research_policy: {id: "research_policy", version: 1}') == 1
        path.write_text(text.replace("version: 1}", "version: 3}"), encoding="utf-8")
    return root


def _load(path: Path, root: Path) -> ExperimentV2:
    return load_experiment_v2(
        path,
        repo_root=root,
        registry=INITIAL_CATALOG,
        metric_set_versions=frozenset({METRIC_SET_VERSION}),
    )


def test_9_the_same_policy_version_generates_the_same_folds_for_every_experiment(
    tmp_path: Path,
) -> None:
    """#9: 同じ版を指す別の実験は、同じ評価基準と同じ fold を得る（実験ごとに期間を選べない）。"""
    root = _workspace(tmp_path)
    first, second = (_load(root / relative, root) for relative in _EXPERIMENTS)

    assert first.experiment.experiment_id != second.experiment.experiment_id
    assert first.policy.digest == second.policy.digest
    standard = first.policy.evaluation_standard
    assert standard is not None
    assert second.policy.evaluation_standard == standard
    second_standard = second.policy.evaluation_standard
    folds = generate_folds(standard.split)
    assert len(folds) >= standard.split.min_folds
    assert generate_folds(second_standard.split) == folds


@pytest.mark.parametrize("key", ["selection", "acceptance"])
def test_10_selection_or_acceptance_in_the_experiment_is_refused(tmp_path: Path, key: str) -> None:
    """#10 前半: 実験設定に評価基準のキーを書くと読込で拒否する。

    理由は「評価基準は研究ポリシーの版で決まる」（D09 §5.5 の7）。
    """
    root = _workspace(tmp_path)
    path = root / _EXPERIMENTS[0]
    path.write_text(
        path.read_text(encoding="utf-8") + f"{key}: {{metric: NET_RETURN_RATE}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="評価基準は研究ポリシーの版で決まる"):
        _load(path, root)


def test_10_trade_count_as_a_performance_condition_is_refused_on_policy_load(
    tmp_path: Path,
) -> None:
    """#10 後半: 取引件数（#3）を成績の条件に書いた研究ポリシーは読込で拒否（E1）。"""
    root = _workspace(tmp_path)
    validation = DEFAULT_STANDARD_LINES["validation"].replace(
        "MAX_DRAWDOWN_MTM_RATE, comparator: LE", "TRADE_COUNT, comparator: GE"
    )
    write_policy(root, "research_policy", 3, policy_v3_text(replace={"validation": validation}))
    with pytest.raises(ConfigError, match="取引件数"):
        _load(root / _EXPERIMENTS[0], root)


def test_17_a_policy_without_a_purpose_is_refused(tmp_path: Path) -> None:
    """#17（読込）: 用途を省いた研究ポリシーは読込で拒否（E5。既定値を置かない）。"""
    root = _workspace(tmp_path)
    write_policy(root, "research_policy", 3, policy_v3_text(replace={"purpose": None}))
    with pytest.raises(ConfigError, match="purpose"):
        _load(root / _EXPERIMENTS[0], root)


def test_17_a_purpose_different_from_the_registry_is_refused(tmp_path: Path) -> None:
    """#17（読込）: 登録簿の要素の `purpose` とファイルの `purpose` が違えば拒否（照合 (5)）。"""
    root = _workspace(tmp_path)
    write_policy(root, "research_policy", 4, policy_v3_text(version=4))
    append_registry_entry(root, "research_policy", 4, purpose="STANDARD")
    path = root / _EXPERIMENTS[0]
    path.write_text(
        path.read_text(encoding="utf-8").replace("version: 3}", "version: 4}"), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="用途"):
        _load(path, root)


def test_17_the_current_version_is_the_largest_standard_one(tmp_path: Path) -> None:
    """#17（登録簿）: 現行の版は `STANDARD` の最大の版。

    番号の大きい機構確認用（`MECHANISM_CHECK`）の版が後から登録されても変わらない。
    """
    root = _workspace(tmp_path)
    registry = research_policy_registry_path(root)
    assert (
        current_standard_version(load_research_policy_registry(registry), "research_policy") is None
    )

    write_policy(
        root,
        "research_policy",
        4,
        policy_v3_text(version=4, replace={"purpose": "  purpose: STANDARD"}),
    )
    append_registry_entry(root, "research_policy", 4)
    # 版 5（機構確認用）を足す前の現行の版。足した後も同じ（下の最後の確かめ）。
    assert current_standard_version(load_research_policy_registry(registry), "research_policy") == 4
    write_policy(root, "research_policy", 5, policy_v3_text(version=5))
    append_registry_entry(root, "research_policy", 5)

    entries = load_research_policy_registry(registry)
    assert [(item.version, item.purpose and item.purpose.value) for item in entries] == [
        (1, None),
        (3, "MECHANISM_CHECK"),
        (4, "STANDARD"),
        (5, "MECHANISM_CHECK"),
    ]
    assert current_standard_version(entries, "research_policy") == 4
