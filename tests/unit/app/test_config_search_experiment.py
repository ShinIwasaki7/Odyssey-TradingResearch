"""探索の実験設定の読込と拒否（D09 §5.1・§5.5・§5.6・§6.1、D07 §18.2・§18.6 v2.9）。

探索の実験設定は同梱の検証戦略 B の単一実行の実験設定を複製して書き換えたもの
（`tests/fixtures/evaluation/search_experiments.py`）。研究ポリシーは試験用の版 3（2 fold）を
作業場に作り、作業場の登録簿に載せる。拒否の試験は1か所だけ書き換えた実験設定を読む。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.evaluation.domain.search import SearchPlanKind
from odyssey_fx.evaluation.domain.splits import FinalHoldoutSpec, SplitKind
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.declarations.specs import IntValue
from tests.fixtures.evaluation.search_experiments import (
    BASE_EXPERIMENT,
    DEFAULT_AXES,
    install_policy_v3,
    write_search_experiment,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

_COPIED = (
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v1.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/strategies/strategy_b_v1.yaml",
    "configs/policies/research/research_policy_v1.yaml",
    "configs/policies/research/research_policy_v2.yaml",
    "configs/policies/research/registry.yaml",
    BASE_EXPERIMENT,
)

_START = UtcTime.parse("2015-01-04T22:00:00Z")


def _day(offset: int) -> UtcTime:
    return _START + timedelta(days=offset)


def _workspace(tmp_path: Path) -> Path:
    for relative in _COPIED:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative).read_bytes())
    install_policy_v3(tmp_path)
    return tmp_path


def _load(path: Path, root: Path) -> ExperimentV2:
    return load_experiment_v2(
        path,
        repo_root=root,
        registry=INITIAL_CATALOG,
        metric_set_versions=frozenset({METRIC_SET_VERSION}),
    )


def _refused(path: Path, root: Path) -> str:
    with pytest.raises(ConfigError) as caught:
        _load(path, root)
    return str(caught.value)


def _replaced(path: Path, old: str, new: str) -> Path:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")
    return path


# --- 読める形 -----------------------------------------------------------------


def test_a_search_experiment_loads_its_plan_folds_and_standard(tmp_path: Path) -> None:
    """探索計画・研究ポリシーから生成した fold・評価基準が読込結果に入る（D09 §5.1・§6.1）。"""
    root = _workspace(tmp_path)
    loaded = _load(write_search_experiment(root), root)

    search = loaded.search
    assert search is not None
    assert loaded.search_plan == "GRID" and loaded.split == "STANDARD"
    assert search.plan.kind is SearchPlanKind.GRID
    assert [axis.key for axis in search.plan.axes] == [
        ("m15_ema", "period"),
        ("stop_level", "lookback"),
    ]
    assert search.plan.axes[0].values == (IntValue(10), IntValue(40))
    assert search.plan.trial_count == 4 and search.plan.max_trials == 4
    assert search.split.kind is SplitKind.STANDARD
    assert [(fold.train, fold.validation) for fold in search.split.folds] == [
        (Interval(start=_day(0), end=_day(4)), Interval(start=_day(4), end=_day(8))),
        (Interval(start=_day(4), end=_day(8)), Interval(start=_day(8), end=_day(12))),
    ]
    assert search.final_holdout is None
    assert search.standard == loaded.policy.evaluation_standard
    # 探索の実験の run 区間は評価範囲を仮に持つ（単位ごとの区間は合成が fold から決める）。
    assert loaded.experiment.run_interval == search.standard.split.range


def test_the_axes_are_ordered_by_instance_and_parameter(tmp_path: Path) -> None:
    """軸の書き順を入れ替えても同じ探索計画になる（`(instance, parameter)` の昇順。D09 §5.2）。"""
    root = _workspace(tmp_path)
    reversed_axes = "\n".join(reversed(DEFAULT_AXES.split("\n")))
    first = _load(write_search_experiment(root, "a.yaml"), root)
    second = _load(write_search_experiment(root, "b.yaml", axes=reversed_axes), root)
    assert first.search is not None and second.search is not None
    assert first.search.plan == second.search.plan


def test_fewer_trials_than_max_trials_are_accepted(tmp_path: Path) -> None:
    """列挙した試行の数が `max_trials` を下回るのは許す（D09 §5.5 の3）。"""
    root = _workspace(tmp_path)
    loaded = _load(write_search_experiment(root, max_trials=12), root)
    assert loaded.search is not None and loaded.search.plan.trial_count == 4


def test_a_final_holdout_right_after_the_range_is_accepted(tmp_path: Path) -> None:
    """`final_holdout` の開始が評価範囲の終わり＋purge 以上なら読める（D09 §6.1 の検査4）。"""
    root = _workspace(tmp_path)
    holdout = (
        '{interval: {start: "2015-01-16T22:00:00Z", end: "2015-01-18T22:00:00Z"},'
        ' purpose: "人工データでの確かめ"}'
    )
    loaded = _load(write_search_experiment(root, final_holdout=holdout), root)
    assert loaded.search is not None
    assert loaded.search.final_holdout == FinalHoldoutSpec(
        interval=Interval(start=_day(12), end=_day(14)), purpose="人工データでの確かめ"
    )


# --- 拒否（D09 §5.5）-------------------------------------------------------------

_AXIS_REFUSALS: list[tuple[str, str, str]] = [
    # (名前, 軸の書き方, 理由に含まれる語)
    (
        "2: missing instance",
        "    - {instance: nowhere, parameter: period, values: [10]}",
        "戦略に無い",
    ),
    (
        "2: missing parameter",
        "    - {instance: m15_ema, parameter: length, values: [10]}",
        "契約に無い",
    ),
    (
        "2: duplicated axis",
        "    - {instance: m15_ema, parameter: period, values: [10]}\n"
        "    - {instance: m15_ema, parameter: period, values: [20]}",
        "2つある",
    ),
    (
        "2: value type",
        '    - {instance: m15_ema, parameter: period, values: ["10"]}',
        "INT",
    ),
    ("2: empty values", "    - {instance: m15_ema, parameter: period, values: []}", "空"),
    (
        "2: duplicated value",
        "    - {instance: m15_ema, parameter: period, values: [10, 10]}",
        "2回",
    ),
]


@pytest.mark.parametrize(
    ("axes", "expected"),
    [item[1:] for item in _AXIS_REFUSALS],
    ids=[item[0] for item in _AXIS_REFUSALS],
)
def test_invalid_axes_are_refused_on_load(tmp_path: Path, axes: str, expected: str) -> None:
    """軸の誤記は読込で拒否する（全試行が失敗として記録されて探索の結果に見えないように。§5.2）。"""
    root = _workspace(tmp_path)
    assert expected in _refused(write_search_experiment(root, axes=axes), root)


@pytest.mark.parametrize(
    ("max_trials", "expected"), [(3, "超える"), (0, "正の整数")], ids=["over", "zero"]
)
def test_max_trials_is_checked_on_load(tmp_path: Path, max_trials: int, expected: str) -> None:
    """列挙した試行の数が `max_trials` を超える・`max_trials` が正でないなら拒否（§5.5 の3）。

    超えた分を切り捨てて一部だけ試す動作はしない（D09 §5.1）。
    """
    root = _workspace(tmp_path)
    assert expected in _refused(write_search_experiment(root, max_trials=max_trials), root)


_KEY_REFUSALS: list[tuple[str, str, str, str]] = [
    # (名前, 書き換え前, 書き換え後, 理由に含まれる語)
    ("1: kind", "  kind: GRID\n", "  kind: RANDOM\n", "GRID"),
    ("4: split only", "split: STANDARD\n", 'split: "NONE"\n', "片方"),
    ("5: split vocabulary", "split: STANDARD\n", "split: WALK_FORWARD\n", "STANDARD"),
    (
        "6: run interval",
        "split: STANDARD\n",
        'split: STANDARD\nrun_interval: {start: "2015-01-04T22:00:00Z",'
        ' end: "2015-01-16T22:00:00Z"}\n',
        "run_interval",
    ),
    ("11: final holdout omitted", "final_holdout: NONE\n", "", "final_holdout"),
    ("11: final holdout null", "final_holdout: NONE\n", "final_holdout: null\n", "final_holdout"),
    ("11: final holdout word", "final_holdout: NONE\n", "final_holdout: LATER\n", "final_holdout"),
    (
        "7: selection key",
        "split: STANDARD\n",
        "split: STANDARD\nselection: {metric: NET_RETURN_RATE, direction: MAXIMIZE}\n",
        "研究ポリシーの版で決まる",
    ),
    (
        "6.1 check 4: holdout before the range end",
        "final_holdout: NONE\n",
        'final_holdout: {interval: {start: "2015-01-15T22:00:00Z", end: "2015-01-18T22:00:00Z"},'
        ' purpose: "x"}\n',
        "検査4",
    ),
    (
        "6.1 check 4: blank purpose",
        "final_holdout: NONE\n",
        'final_holdout: {interval: {start: "2015-01-16T22:00:00Z", end: "2015-01-18T22:00:00Z"},'
        ' purpose: "  "}\n',
        "検査4",
    ),
]


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [item[1:] for item in _KEY_REFUSALS],
    ids=[item[0] for item in _KEY_REFUSALS],
)
def test_invalid_search_keys_are_refused_on_load(
    tmp_path: Path, old: str, new: str, expected: str
) -> None:
    """探索計画・分割・最終検証・run 区間のキーの誤りは読込で拒否する（D09 §5.5・§6.1）。"""
    root = _workspace(tmp_path)
    path = _replaced(write_search_experiment(root), old, new)
    assert expected in _refused(path, root)


def test_a_split_without_a_search_plan_is_refused(tmp_path: Path) -> None:
    """`search_plan` が `NONE` で `split` だけが `STANDARD` の実験は作らない（D09 §5.5 の4）。"""
    root = _workspace(tmp_path)
    path = _replaced(root / BASE_EXPERIMENT, 'split: "NONE"', "split: STANDARD")
    assert "片方" in _refused(path, root)


@pytest.mark.parametrize("version", [1, 2])
def test_a_search_experiment_cannot_point_at_a_policy_without_a_standard(
    tmp_path: Path, version: int
) -> None:
    """探索の実験は評価基準の群を持つ版（版 3 以上）しか指せない（D09 §5.5 の10・§10.9）。"""
    root = _workspace(tmp_path)
    message = _refused(write_search_experiment(root, policy_version=version), root)
    assert "版 3 以上" in message


def test_a_single_run_experiment_refuses_a_final_holdout(tmp_path: Path) -> None:
    """単一実行の実験に `final_holdout` を書けば拒否する（D09 §5.5 の11）。"""
    root = _workspace(tmp_path)
    path = _replaced(
        root / BASE_EXPERIMENT, 'split: "NONE"\n', 'split: "NONE"\nfinal_holdout: NONE\n'
    )
    assert "単一実行" in _refused(path, root)


def test_a_single_run_experiment_needs_its_run_interval(tmp_path: Path) -> None:
    """単一実行の実験の `run_interval` の省略は拒否する（D07 §18.2）。"""
    root = _workspace(tmp_path)
    path = _replaced(
        root / BASE_EXPERIMENT,
        'run_interval:\n  start: "2015-01-04T22:00:00Z"\n  end: "2015-01-16T22:00:00Z"\n',
        "",
    )
    assert "run_interval" in _refused(path, root)
