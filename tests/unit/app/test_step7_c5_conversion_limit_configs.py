"""換算の古さの上限を 30 分にした EURUSD の実験設定の版 2 の確認（D06 v1.23 案 §19.5、決定 (32)）。

機構確認 M1 の 3 回目が読む 4 本の実験設定（検証戦略 A・B × 標準・候補の組）が書式 v2 の読込を通り、
版 1 から変えたのが `version`（1 → 2）と換算のポリシーの上限 `max_observation_skew`（15 分 → 30 分）
だけであることを確かめる。版 1 のファイルは変えない（M1 の 1 回目・2 回目の記録票の事前登録の照合と
再現が読む）。

**run はしない**。設定ファイルと銘柄・カレンダーの設定だけを読み、snapshot の実体も成績も読まない。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
NAMES: Final = (
    "strategy_a_eurusd_mt5_standard",
    "strategy_a_eurusd_mt5_candidate",
    "strategy_b_eurusd_mt5_standard",
    "strategy_b_eurusd_mt5_candidate",
)


def _load(name: str) -> ExperimentV2:
    return load_experiment_v2(
        REPO_ROOT / "configs/experiments" / f"{name}.yaml",
        repo_root=REPO_ROOT,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )


@pytest.mark.parametrize("name", NAMES)
def test_version_2_raises_only_the_conversion_limit_to_30_minutes(name: str) -> None:
    """版 2 は同じ実験名の次の版で、換算の上限だけが 30 分（ずれと古さで共有。仮置き C5-1）。"""
    original = _load(name)
    loaded = _load(f"{name}_v2")
    assert (original.values["id"], original.values["version"]) == (name, 1)
    assert (loaded.values["id"], loaded.values["version"]) == (name, 2)
    assert original.body.conversion_policy.max_observation_skew == timedelta(minutes=15)
    assert loaded.body.conversion_policy.max_observation_skew == timedelta(minutes=30)
    assert (
        loaded.body.conversion_policy.pivot_currency
        == original.body.conversion_policy.pivot_currency
    )
    # 換算のポリシーの版参照はダイジェストだけが変わる（`ConfigDigest` と run の識別子が変わる）。
    before, after = original.body.policy_ref("conversion"), loaded.body.policy_ref("conversion")
    assert (after.policy_id, after.version) == (before.policy_id, before.version)
    assert after.digest != before.digest
    for kind in ("risk", "execution", "cost", "delay"):
        assert loaded.body.policy_ref(kind) == original.body.policy_ref(kind)
    # 実行条件のそのほか（戦略・snapshot・換算の snapshot・口座・執行・費用）は版 1 と同じ。
    restored = replace(
        loaded.body,
        version=1,
        conversion_policy=original.body.conversion_policy,
        policy_refs=original.body.policy_refs,
    )
    assert restored == original.body
    assert loaded.run_interval == original.run_interval
    assert loaded.hypothesis == original.hypothesis
    assert loaded.research_policy == original.research_policy
    assert loaded.texts["strategy"] == original.texts["strategy"]
    changed = {key for key in loaded.values if loaded.values[key] != original.values.get(key)}
    assert changed == {"version", "conversion_policy"}
