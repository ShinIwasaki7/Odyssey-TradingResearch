"""登録した研究ポリシー第 3 版のファイルと、それを指す探索の実験設定の読込（D13 §6.1・§6.4・§6.6・
§9、D09 §10.9・§5.5 の 12 の 2026-10-21 改訂、D07 §18.6 の 8。順 4 の実装 PR）。

- 版 3 のファイルは登録簿の 1 行と一致し（ダイジェストは読込が計算した値）、値は D13 §9 の表 12-B の
  とおりで、生成した fold は D13 §2.2 の候補 A の表と一致する。
- 前提の記録の SHA-256 はリポジトリのファイルの実際の SHA-256 と一致する（読込は形だけを確かめる
  ので、ここで確かめる。実装 PR の仮置き 51）。
- 版 3 を指す探索の実験は、指標集合 v3・前提の記録と役割ごとに一致する 2 つの異なる snapshot・
  比較の前提と一致する口座・リスク・費用・執行の値でだけ読める（仮置き 21・53・54、D13-16 (a)・
  D13-21 (a)）。

テストは設定ファイルだけを読み、snapshot も戦略の成績も読まない。
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.research_policy import (
    load_research_policy,
    load_research_policy_registry,
    research_policy_path,
    research_policy_registry_path,
    verify_registered,
)
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS, MetricId
from odyssey_fx.evaluation.domain.search import (
    Comparator,
    PathBoundary,
    StandardPurpose,
)
from odyssey_fx.evaluation.domain.splits import PartialFold, SplitWindow, folds_for_policy
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.evaluation.search_experiments import BASE_EXPERIMENT, search_experiment_text

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
STANDARD_SNAPSHOT: Final = "ecaaf53fcdedbc1977184b390c36e2714c658829341eed3ec5d71588d3bfc622"
CANDIDATE_SNAPSHOT: Final = "f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca"
DAY: Final = 86_400

_COPIED: Final = (
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v1.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/strategies/strategy_b_v1.yaml",
    "configs/policies/research/research_policy_v1.yaml",
    "configs/policies/research/research_policy_v2.yaml",
    "configs/policies/research/research_policy_v3.yaml",
    "configs/policies/research/registry.yaml",
    BASE_EXPERIMENT,
)

AXES: Final = "    - {instance: m15_ema, parameter: period, values: [10, 20]}"


def _policy_v3_path() -> Path:
    return research_policy_path(REPO_ROOT, "research_policy", 3)


def test_the_registered_v3_file_matches_its_registry_line() -> None:
    policy = load_research_policy(_policy_v3_path(), policy_id="research_policy", version=3)
    entry = verify_registered(
        policy,
        load_research_policy_registry(research_policy_registry_path(REPO_ROOT)),
        registry_path=research_policy_registry_path(REPO_ROOT),
    )
    assert entry.purpose is StandardPurpose.STANDARD


def test_the_v3_values_are_those_of_table_12b() -> None:
    """D13 §9 の表 12-B の値（評価範囲・長さ・分割・選定・条件・証拠の要件）。"""
    policy = load_research_policy(_policy_v3_path(), policy_id="research_policy", version=3)
    standard = policy.evaluation_standard
    assert standard is not None and policy.trial_limit == 100
    split = standard.split
    assert (str(split.range.start), str(split.range.end)) == (
        "2017-03-25T12:00:00Z",
        "2023-12-30T12:00:00Z",
    )
    assert (split.train_seconds, split.validation_seconds) == (728 * DAY, 364 * DAY)
    assert split.window is SplitWindow.ROLLING and split.purge_seconds == 0
    assert split.partial_fold is PartialFold.AT_START and split.min_folds == 5
    assert standard.selection.metric is MetricId.ANNUALIZED_SHARPE_RATIO
    assert standard.selection.eligibility == ()
    floors = [
        (item.metric, item.comparator, item.threshold) for item in standard.validation.fold_floors
    ]
    assert floors == [(MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, Comparator.LE, Decimal("0.30"))]
    assert standard.validation.aggregate == ()
    path = standard.validation.path
    assert path.boundary is PathBoundary.CARRY_IF_SAME_TRIAL
    assert [(item.metric, item.comparator, item.threshold) for item in path.floors] == floors
    assert [(item.metric, item.comparator, item.threshold) for item in path.aggregate] == [
        (MetricId.NET_RETURN_RATE, Comparator.GT, Decimal("0"))
    ]
    (only,) = standard.sufficiency.classes
    assert (
        only.name,
        only.min_train_trades_per_365d,
        only.min_validation_trades_per_fold,
        only.min_validation_trades_total,
    ) == ("ALL", Decimal("0"), 0, 8)
    snapshots = standard.basis.snapshots
    assert (str(snapshots.standard), str(snapshots.candidate)) == (
        STANDARD_SNAPSHOT,
        CANDIDATE_SNAPSHOT,
    )
    assert standard.basis.target_series == ("USDJPY/15m/bid", "USDJPY/1h/bid")
    fixed = standard.comparison_basis
    assert (fixed.currency, fixed.initial_balance, fixed.trial_risk_rate) == (
        "JPY",
        Decimal("1000000"),
        Decimal("0.02"),
    )
    folds = folds_for_policy(split, research_until=UtcTime.parse("2024-01-01T00:00:00Z"))
    assert [fold.partial for fold in folds] == [True, False, False, False, False]
    assert str(folds[0].validation) == "[2019-03-23T12:00:00Z, 2020-01-04T12:00:00Z)"


def test_the_premise_hashes_are_those_of_the_files_in_the_repository() -> None:
    """前提の記録の SHA-256 は、リポジトリのファイルの SHA-256 と一致する（仮置き 51）。"""
    policy = load_research_policy(_policy_v3_path(), policy_id="research_policy", version=3)
    assert policy.evaluation_standard is not None
    premises = policy.evaluation_standard.basis.premises
    assert len(premises) == 7
    for premise in premises:
        data = (REPO_ROOT / premise.path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == premise.sha256, premise.name


# --- 版 3 を指す探索の実験設定の照合 -----------------------------------------------------


def _workspace(tmp_path: Path) -> Path:
    for relative in _COPIED:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative).read_bytes())
    return tmp_path


def _experiment(root: Path, *replacements: tuple[str, str]) -> Path:
    base = (root / BASE_EXPERIMENT).read_text(encoding="utf-8").replace("0" * 64, STANDARD_SNAPSHOT)
    text = search_experiment_text(
        base,
        experiment_id="v3_search",
        policy_version=3,
        axes=AXES,
        max_trials=2,
        candidate_snapshot=CANDIDATE_SNAPSHOT,
    ).replace("evaluation: {metric_set_version: 2}", "evaluation: {metric_set_version: 3}")
    for old, new in replacements:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = root / "configs/experiments/v3_search.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _load(path: Path, root: Path) -> ExperimentV2:
    return load_experiment_v2(
        path, repo_root=root, registry=INITIAL_CATALOG, metric_set_versions=METRIC_SET_VERSIONS
    )


def _refused(path: Path, root: Path) -> str:
    with pytest.raises(ConfigError) as caught:
        _load(path, root)
    return str(caught.value)


def test_a_search_on_v3_with_the_basis_snapshots_and_the_fixed_values_loads(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    loaded = _load(_experiment(root), root)
    search = loaded.search
    assert search is not None
    assert str(search.snapshots.standard.snapshot_id) == STANDARD_SNAPSHOT
    assert str(loaded.body_of(Variant.CANDIDATE).snapshot_ref.snapshot_id) == CANDIDATE_SNAPSHOT
    assert len(search.split.folds) == 5 and search.split.folds[0].partial


def test_metric_set_two_cannot_judge_the_v3_conditions(tmp_path: Path) -> None:
    """版 3 の条件の #20 は指標集合 v3 にしか無い（実装 PR の仮置き 53）。"""
    root = _workspace(tmp_path)
    path = _experiment(
        root, ("evaluation: {metric_set_version: 3}", "evaluation: {metric_set_version: 2}")
    )
    assert "MAX_DRAWDOWN_MTM_RATE_PEAK" in _refused(path, root)


def test_swapped_snapshots_are_refused(tmp_path: Path) -> None:
    """役割ごとの一致（D13-16 (a)・仮置き 21）: 入れ替えて書けば拒否。"""
    root = _workspace(tmp_path)
    path = _experiment(
        root,
        (f'  standard: "{STANDARD_SNAPSHOT}"', f'  standard: "{CANDIDATE_SNAPSHOT}x"'),
        (f'  candidate: "{CANDIDATE_SNAPSHOT}"', f'  candidate: "{STANDARD_SNAPSHOT}"'),
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(f"{CANDIDATE_SNAPSHOT}x", CANDIDATE_SNAPSHOT),
        encoding="utf-8",
    )
    assert "basis.snapshots.standard" in _refused(path, root)


def test_the_same_snapshot_twice_is_refused(tmp_path: Path) -> None:
    """両組は別の snapshot（実装 PR の仮置き 54）。"""
    root = _workspace(tmp_path)
    path = _experiment(
        root, (f'  candidate: "{CANDIDATE_SNAPSHOT}"', f'  candidate: "{STANDARD_SNAPSHOT}"')
    )
    assert "同じ snapshot" in _refused(path, root)


def test_a_different_trial_risk_rate_is_refused(tmp_path: Path) -> None:
    """比較の前提の固定（D13-21 (a)）: 1 取引の損失予算を変えた実験は拒否。"""
    root = _workspace(tmp_path)
    path = _experiment(root, ('  trial_risk_rate: "0.02"', '  trial_risk_rate: "0.03"'))
    assert "risk_policy.trial_risk_rate" in _refused(path, root)


def test_a_different_adverse_fill_limit_is_refused(tmp_path: Path) -> None:
    """数量に効く執行ポリシーの値も比較の前提に入る（仮置き 18）。"""
    root = _workspace(tmp_path)
    path = _experiment(root, ('    USDJPY: "0.05"', '    USDJPY: "0.06"'))
    assert "adverse_fill_limits" in _refused(path, root)


def test_a_single_snapshot_key_is_refused_for_a_search(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    path = _experiment(root)
    text = path.read_text(encoding="utf-8")
    path.write_text(text + f'snapshot: "{STANDARD_SNAPSHOT}"\n', encoding="utf-8")
    assert "単数の `snapshot`" in _refused(path, root)


def test_lineage_is_read_for_a_search(tmp_path: Path) -> None:
    """系譜は任意の項目（実装 PR の仮置き 56）。"""
    root = _workspace(tmp_path)
    path = _experiment(root)
    path.write_text(
        path.read_text(encoding="utf-8")
        + 'lineage:\n  derived_from: NONE\n  change_kinds: [HYPOTHESIS]\n  note: "最初の仮説"\n',
        encoding="utf-8",
    )
    search = _load(path, root).search
    assert search is not None and search.lineage is not None
    assert search.lineage.derived_from is None
