"""研究ポリシーの版 5 の読込の型と読込時の検査 E6〜E12（研究ポリシー第 5 版の設計 D24 §2・§8.1、
§10.2 の受入テスト RP5-1〜RP5-4・RP5-17。実装 PR 4-1）。

- 第 5 版のファイルと登録簿の行はこの PR に入れない（決定 142、D24 の仮置き 20）。RP5-1 は試験用の
  研究ポリシーと登録簿（テストの中のリポジトリの根。`tests/fixtures/evaluation/research_policies.py`）
  で確かめる。値は D24 §7.1 の方針の値を写した仮の値で、研究ポリシーではない。
- 版 4 以下のファイル・ダイジェスト・登録簿の行と、版 4 を指す既存の実験設定の読込は変わらない
  （RP5-17）。

テストは設定ファイルと人工の実験設定だけを読み、snapshot も価格も戦略の成績も読まない。
"""

from __future__ import annotations

import json
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
from odyssey_fx.common.canonical import encode
from odyssey_fx.evaluation.adapters.fs_store import _evaluation_standard_of
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS, MetricId
from odyssey_fx.evaluation.domain.regimes import RegimeLabel, RegimeRole, RegimeTimeframe
from odyssey_fx.evaluation.domain.research_policy import ResearchPolicy
from odyssey_fx.evaluation.domain.search import (
    Comparator,
    DeflationMethod,
    EvaluationStandard,
    EvaluationStandardV5,
    Neighborhood,
    NeighborInterval,
    PathRuleV5,
    TrialCorrelation,
    ValidationRuleV5,
)
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.evaluation.research_policies import (
    TEST_REGIMES,
    TEST_VALIDATION_V5,
    TEST_VALIDATION_V5_GROUPS,
    append_registry_entry,
    policy_v3_text,
    write_policy,
)
from tests.fixtures.evaluation.search_experiments import BASE_EXPERIMENT, write_search_experiment

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
#: 登録簿の版 4 のダイジェスト（`configs/policies/research/registry.yaml`。変えない）。
V4_DIGEST: Final = "5e9ac02b3e8102479ad843852833757acc0b92747501af823b2f940aecfc7149"

_COPIED: Final = (
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v1.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/strategies/strategy_b_v1.yaml",
    "configs/policies/research/research_policy_v1.yaml",
    "configs/policies/research/research_policy_v2.yaml",
    BASE_EXPERIMENT,
)


def _workspace(tmp_path: Path) -> Path:
    """試験用の作業場（版 1・2 は正本の写し、版 5 は試験用。登録簿は作業場で作る）。"""
    for relative in _COPIED:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative).read_bytes())
    append_registry_entry(tmp_path, "research_policy", 1)
    append_registry_entry(tmp_path, "research_policy", 2)
    return tmp_path


def _install_v5(root: Path, text: str | None = None) -> Path:
    path = write_policy(root, "research_policy", 5, text or policy_v3_text(version=5))
    append_registry_entry(root, "research_policy", 5)
    return path


def _load_policy(root: Path, version: int, text: str) -> ResearchPolicy:
    write_policy(root, "research_policy", version, text)
    return load_research_policy(
        research_policy_path(root, "research_policy", version),
        policy_id="research_policy",
        version=version,
        repo_root=root,
    )


def _refused_policy(root: Path, version: int, text: str) -> str:
    with pytest.raises(ConfigError) as caught:
        _load_policy(root, version, text)
    return str(caught.value)


def _replaced(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def _v5(**replace: str | None) -> str:
    return policy_v3_text(version=5, replace=dict(replace) if replace else None)


def _v5_standard(root: Path, text: str | None = None) -> EvaluationStandardV5:
    standard = _load_policy(root, 5, text or _v5()).evaluation_standard
    assert isinstance(standard, EvaluationStandardV5)
    return standard


def _group_block(group: str) -> str:
    """試験用の版 5 の 3 群のうち 1 群の行（`validation:` の下の字下げ込み）。"""
    found: list[str] = []
    inside = False
    for line in TEST_VALIDATION_V5_GROUPS.split("\n"):
        if line.startswith("    ") and not line.startswith("     "):
            inside = line == f"    {group}:"
        if inside:
            found.append(line)
    assert found, group
    return "\n".join(found)


def _load_experiment(path: Path, root: Path) -> ExperimentV2:
    return load_experiment_v2(
        path, repo_root=root, registry=INITIAL_CATALOG, metric_set_versions=METRIC_SET_VERSIONS
    )


def _v5_experiment(root: Path, *, metric_set: int = 4, extra: str = "") -> Path:
    path = write_search_experiment(root, policy_version=5, metric_set_version=metric_set)
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return path


def _refused_experiment(path: Path, root: Path) -> str:
    with pytest.raises(ConfigError) as caught:
        _load_experiment(path, root)
    return str(caught.value)


# --- RP5-1: 試験用の版 5 を読み、試験用の登録簿のダイジェストと一致する ---


def test_rp5_1_a_test_v5_policy_matches_its_test_registry_line(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    path = _install_v5(root)
    policy = load_research_policy(path, policy_id="research_policy", version=5, repo_root=root)
    registry = research_policy_registry_path(root)
    entry = verify_registered(
        policy, load_research_policy_registry(registry), registry_path=registry
    )
    assert entry.version == 5 and entry.digest == policy.digest


def test_rp5_1_the_v5_groups_are_read_into_the_v5_types(tmp_path: Path) -> None:
    standard = _v5_standard(_workspace(tmp_path))
    validation = standard.validation
    assert isinstance(validation, ValidationRuleV5)
    assert isinstance(validation.path, PathRuleV5)
    assert [item.metric for item in validation.fold_floors] == [
        MetricId.MAX_DRAWDOWN_MTM_RATE,
        MetricId.NET_RETURN_RATE_CLOSED_OUT,
    ]
    concentration = validation.loss_concentration
    assert concentration.worst_fold_metric is MetricId.NET_RETURN_RATE_CLOSED_OUT
    assert concentration.max_multiple_of_path_net == Decimal("1.0")
    assert concentration.applies_when_path_net_return_at_least == Decimal("0.10")
    neighbor = validation.neighbor_floors
    assert neighbor.neighborhood is Neighborhood.ONE_STEP_IN_WRITTEN_ORDER
    assert neighbor.interval is NeighborInterval.TRAIN
    assert [(item.metric, item.comparator) for item in neighbor.floors] == [
        (MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, Comparator.LE),
        (MetricId.NET_RETURN_RATE_CLOSED_OUT, Comparator.GE),
    ]
    deflated = validation.deflated_downside_ratio
    assert deflated.method is DeflationMethod.DEFLATED_SHARPE_RATIO_FORM
    assert deflated.correlation is TrialCorrelation.MEAN_PAIRWISE_PEARSON
    assert (deflated.downside_target, deflated.min_probability) == (Decimal(0), Decimal("0.95"))
    regimes = standard.regimes
    assert regimes.role is RegimeRole.DISPLAY_ONLY and regimes.timeframe is RegimeTimeframe.DAILY
    assert (regimes.direction.ema_bars, regimes.direction.slope_bars) == (50, 20)
    assert regimes.direction.dead_band_atr_multiple == Decimal("0.5")
    assert (regimes.volatility.atr_bars, regimes.volatility.median_window_bars) == (20, 250)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('min_probability: "0.95"', 'min_probability: "0.96"'),
        ('max_multiple_of_path_net: "1.0"', 'max_multiple_of_path_net: "1.5"'),
        ('threshold: "-0.30"}', 'threshold: "-0.29"}'),
        ("median_window_bars: 250", "median_window_bars: 251"),
    ],
)
def test_rp5_1_every_new_group_is_in_the_digest(tmp_path: Path, old: str, new: str) -> None:
    """新しい群はすべてダイジェストの入力に入る（D24 §2.1、D09 §10.9）。値を 1 つ変えると登録簿と
    食い違い、読めない（版を上げずに中身を変えない）。"""
    root = _workspace(tmp_path)
    path = _install_v5(root)
    path.write_text(_replaced(path.read_text(encoding="utf-8"), old, new), encoding="utf-8")
    policy = load_research_policy(path, policy_id="research_policy", version=5, repo_root=root)
    registry = research_policy_registry_path(root)
    with pytest.raises(ConfigError, match="ダイジェスト"):
        verify_registered(policy, load_research_policy_registry(registry), registry_path=registry)


def test_rp5_1_a_v5_standard_survives_the_manifest_round_trip(tmp_path: Path) -> None:
    """記録票の正規化形から版 5 の型へ戻せる（保存した記録票の読み直し。D07 §19.2）。"""
    standard = _v5_standard(_workspace(tmp_path))
    restored = _evaluation_standard_of(json.loads(encode(standard)))
    assert isinstance(restored, EvaluationStandardV5)
    assert restored == standard
    assert encode(restored) == encode(standard)


# --- RP5-2: E6（版の番号でキーの形を選ぶ） ---


@pytest.mark.parametrize(
    "group", ["loss_concentration", "neighbor_floors", "deflated_downside_ratio"]
)
def test_rp5_2_a_v5_without_a_validation_group_is_refused(tmp_path: Path, group: str) -> None:
    block = _group_block(group)
    validation = TEST_VALIDATION_V5.replace("\n" + block, "")
    assert validation != TEST_VALIDATION_V5
    message = _refused_policy(_workspace(tmp_path), 5, _v5(validation=validation))
    assert group in message and "Field required" in message


def test_rp5_2_a_v5_without_regimes_is_refused(tmp_path: Path) -> None:
    message = _refused_policy(_workspace(tmp_path), 5, _v5(regimes=None))
    assert "regimes" in message and "Field required" in message


@pytest.mark.parametrize(
    "group", ["loss_concentration", "neighbor_floors", "deflated_downside_ratio"]
)
def test_rp5_2_a_v4_with_a_v5_validation_group_is_refused(tmp_path: Path, group: str) -> None:
    base = policy_v3_text(version=4)
    validation_end = base.index("  sufficiency:\n")
    text = base[:validation_end] + _group_block(group) + "\n" + base[validation_end:]
    message = _refused_policy(_workspace(tmp_path), 4, text)
    assert group in message and "Extra inputs are not permitted" in message


def test_rp5_2_a_v4_with_regimes_is_refused(tmp_path: Path) -> None:
    text = policy_v3_text(version=4) + TEST_REGIMES + "\n"
    message = _refused_policy(_workspace(tmp_path), 4, text)
    assert "regimes" in message and "Extra inputs are not permitted" in message


def test_rp5_2_a_v4_test_policy_still_reads_into_the_v4_types(tmp_path: Path) -> None:
    standard = _load_policy(_workspace(tmp_path), 4, policy_v3_text(version=4)).evaluation_standard
    assert type(standard) is EvaluationStandard
    assert type(standard.validation).__name__ == "ValidationRule"


# --- RP5-3: E7〜E10 の違反は理由に項目名を出して拒否する ---


@pytest.mark.parametrize(
    ("old", "new", "words"),
    [
        # E7: 近傍の床に参考値（#22）。
        (
            '        - {metric: MAX_DRAWDOWN_MTM_RATE_PEAK, comparator: LE, threshold: "0.30"}\n',
            '        - {metric: AVERAGE_WIN_LOSS_RATIO, comparator: GE, threshold: "1"}\n',
            ("neighbor_floors.floors[0]", "参考値"),
        ),
        # E7: 近傍の床の純収益率を LE で書いた。
        (
            '        - {metric: NET_RETURN_RATE_CLOSED_OUT, comparator: GE, threshold: "-0.30"}',
            '        - {metric: NET_RETURN_RATE_CLOSED_OUT, comparator: LE, threshold: "-0.30"}',
            ("neighbor_floors.floors[1]", "`GE`"),
        ),
        # E7: 選定の区間でない区間。
        ("      interval: TRAIN\n", "      interval: VALIDATION\n", ("neighbor_floors.interval",)),
        # E7: fold の床のドローダウンを GE で書いた。
        (
            '      - {metric: MAX_DRAWDOWN_MTM_RATE, comparator: LE, threshold: "0.2"}\n',
            '      - {metric: MAX_DRAWDOWN_MTM_RATE, comparator: GE, threshold: "0.2"}\n',
            ("validation.fold_floors[0]", "`LE`"),
        ),
        # E8: 倍数 0。
        (
            'max_multiple_of_path_net: "1.0"',
            'max_multiple_of_path_net: "0"',
            ("loss_concentration.max_multiple_of_path_net",),
        ),
        # E8: 閾が負。
        (
            'applies_when_path_net_return_at_least: "0.10"',
            'applies_when_path_net_return_at_least: "-0.01"',
            ("loss_concentration.applies_when_path_net_return_at_least",),
        ),
        # E8: 最悪の fold の指標が比率でない。
        (
            "worst_fold_metric: NET_RETURN_RATE_CLOSED_OUT",
            "worst_fold_metric: NET_PROFIT",
            ("loss_concentration.worst_fold_metric",),
        ),
        # E9: 確率 1.0。
        (
            'min_probability: "0.95"',
            'min_probability: "1.0"',
            ("deflated_downside_ratio.min_probability",),
        ),
        # E9: 語彙の外の方式。
        (
            "method: DEFLATED_SHARPE_RATIO_FORM",
            "method: SIDAK",
            ("deflated_downside_ratio.method",),
        ),
        # E9: 語彙の外の相関の推定。
        (
            "correlation: MEAN_PAIRWISE_PEARSON",
            "correlation: SPEARMAN",
            ("deflated_downside_ratio.correlation",),
        ),
        # E10: 役割に判定。
        ("    role: DISPLAY_ONLY\n", "    role: JUDGEMENT\n", ("regimes.role",)),
        # E10: 本数 0。
        ("ema_bars: 50", "ema_bars: 0", ("regimes.direction.ema_bars",)),
        # E10: 不感帯が負。
        (
            'dead_band_atr_multiple: "0.5"',
            'dead_band_atr_multiple: "-0.5"',
            ("regimes.direction.dead_band_atr_multiple",),
        ),
        # E10: 日足でない足。
        ('    timeframe: "1d"\n', '    timeframe: "1h"\n', ("regimes.timeframe",)),
        # E10: 語彙の外の付ける時点。
        (
            "label_at: LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY",
            "label_at: BAR_END_AT_ENTRY",
            ("regimes.label_at",),
        ),
    ],
)
def test_rp5_3_each_violation_of_e7_to_e10_is_refused_with_the_item_name(
    tmp_path: Path, old: str, new: str, words: tuple[str, ...]
) -> None:
    message = _refused_policy(_workspace(tmp_path), 5, _replaced(_v5(), old, new))
    for word in words:
        assert word in message, message


@pytest.mark.parametrize(
    ("old", "new", "word"),
    [
        # 経路の条件に #23。
        (
            '        - {metric: NET_RETURN_RATE, comparator: GE, threshold: "-1"}',
            '        - {metric: ANNUALIZED_DOWNSIDE_RATIO, comparator: GT, threshold: "0"}',
            "validation.path.aggregate[0]",
        ),
        # fold の床に #23。
        (
            '      - {metric: MAX_DRAWDOWN_MTM_RATE, comparator: LE, threshold: "0.2"}\n',
            '      - {metric: ANNUALIZED_DOWNSIDE_RATIO, comparator: GE, threshold: "0"}\n',
            "validation.fold_floors[0]",
        ),
        # 選定に #23。
        (
            "    metric: NET_RETURN_RATE\n    direction: MAXIMIZE",
            "    metric: ANNUALIZED_DOWNSIDE_RATIO\n    direction: MAXIMIZE",
            "ANNUALIZED_DOWNSIDE_RATIO",
        ),
    ],
)
def test_rp5_3_the_downside_ratio_cannot_be_written_in_any_condition(
    tmp_path: Path, old: str, new: str, word: str
) -> None:
    """E9: #23 を経路・fold の床・選定に書かない（割り引いた指標の材料。D24 §6・§8.1）。"""
    message = _refused_policy(_workspace(tmp_path), 5, _replaced(_v5(), old, new))
    assert word in message and "E9" in message, message


def test_rp5_3_the_downside_ratio_is_refused_in_a_v4_policy_as_well(tmp_path: Path) -> None:
    text = _replaced(
        policy_v3_text(version=4),
        "    metric: NET_RETURN_RATE\n    direction: MAXIMIZE",
        "    metric: ANNUALIZED_DOWNSIDE_RATIO\n    direction: MAXIMIZE",
    )
    assert "E9" in _refused_policy(_workspace(tmp_path), 4, text)


def test_the_path_profit_factor_is_written_with_ge_only_in_v5(tmp_path: Path) -> None:
    """経路の `PROFIT_FACTOR` は版 5 の `path.aggregate` に `GE` でだけ書ける（D24 §2.1・§3.3）。
    版 4 の経路には書けない（経路で計れる指標ではない）。"""
    root = _workspace(tmp_path)
    line = '        - {metric: NET_RETURN_RATE, comparator: GE, threshold: "-1"}'
    factor = line + '\n        - {metric: PROFIT_FACTOR, comparator: GE, threshold: "1.2"}'
    standard = _v5_standard(root, _replaced(_v5(), line, factor))
    assert [item.metric for item in standard.validation.path.aggregate] == [
        MetricId.NET_RETURN_RATE,
        MetricId.PROFIT_FACTOR,
    ]
    greater = factor.replace("PROFIT_FACTOR, comparator: GE", "PROFIT_FACTOR, comparator: GT")
    assert "`GE` だけ" in _refused_policy(root, 5, _replaced(_v5(), line, greater))
    v4 = _replaced(policy_v3_text(version=4), line, factor)
    assert "経路で計れない" in _refused_policy(root, 4, v4)


# --- RP5-4: E11（版 5 を指す探索の実験は指標集合 v4）・E12（得意な局面の宣言） ---


def test_rp5_4_a_search_on_v5_with_metric_set_v4_loads(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _install_v5(root)
    loaded = _load_experiment(_v5_experiment(root), root)
    assert loaded.search is not None
    assert isinstance(loaded.search.standard, EvaluationStandardV5)
    assert loaded.metric_set_version == 4
    assert loaded.search.favored_regimes is None


@pytest.mark.parametrize("metric_set", [2, 3])
def test_rp5_4_a_search_on_v5_with_an_older_metric_set_is_refused(
    tmp_path: Path, metric_set: int
) -> None:
    root = _workspace(tmp_path)
    _install_v5(root)
    message = _refused_experiment(_v5_experiment(root, metric_set=metric_set), root)
    assert "E11" in message and "NET_RETURN_RATE_CLOSED_OUT" in message


def test_e12_favored_regimes_are_read_in_the_written_order(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _install_v5(root)
    path = _v5_experiment(root, extra="favored_regimes: [DOWN_HIGH, UP_HIGH]\n")
    loaded = _load_experiment(path, root)
    assert loaded.search is not None
    assert loaded.search.favored_regimes == (RegimeLabel.DOWN_HIGH, RegimeLabel.UP_HIGH)
    # 宣言は実験設定の値として記録票の識別の入力に入る（判定には使わない）。
    assert list(loaded.values["favored_regimes"]) == ["DOWN_HIGH", "UP_HIGH"]


@pytest.mark.parametrize(
    ("declared", "word"),
    [
        ("[UP]", "UP"),
        ("[UNLABELED]", "UNLABELED"),
        ("[UP_HIGH, UP_HIGH]", "2 回"),
        ("[]", "空"),
    ],
)
def test_e12_a_wrong_favored_regimes_declaration_is_refused(
    tmp_path: Path, declared: str, word: str
) -> None:
    root = _workspace(tmp_path)
    _install_v5(root)
    path = _v5_experiment(root, extra=f"favored_regimes: {declared}\n")
    message = _refused_experiment(path, root)
    assert "favored_regimes" in message and "E12" in message and word in message


def test_e12_favored_regimes_on_a_v4_search_are_refused(tmp_path: Path) -> None:
    """局面を定義しない版を指す実験には書けない（D24 §5.4）。"""
    root = _workspace(tmp_path)
    write_policy(root, "research_policy", 4, policy_v3_text(version=4))
    append_registry_entry(root, "research_policy", 4)
    path = write_search_experiment(root, policy_version=4)
    path.write_text(
        path.read_text(encoding="utf-8") + "favored_regimes: [UP_HIGH]\n", encoding="utf-8"
    )
    assert "版 5 以上" in _refused_experiment(path, root)


# --- RP5-17: 第 4 版を指す既存の実験設定の読込とダイジェストが変わらない ---


def test_rp5_17_the_registered_v1_to_v4_digests_are_unchanged() -> None:
    entries = load_research_policy_registry(research_policy_registry_path(REPO_ROOT))
    assert [item.version for item in entries] == [1, 2, 3, 4]
    for entry in entries:
        policy = load_research_policy(
            research_policy_path(REPO_ROOT, "research_policy", entry.version),
            policy_id="research_policy",
            version=entry.version,
            repo_root=REPO_ROOT,
        )
        assert policy.digest == entry.digest
    assert entries[-1].digest.hex == V4_DIGEST


@pytest.mark.parametrize(
    "name",
    [
        "h6_h1_breakout_m15_ema_confirm_eurusd.yaml",
        "h6_h1_breakout_m15_ema_confirm_eurusd_v2.yaml",
        "h7_h1_dual_ema_cross_eurusd.yaml",
        "h7_h1_dual_ema_cross_eurusd_v2.yaml",
    ],
)
def test_rp5_17_the_existing_searches_on_v4_still_load_as_v4(name: str) -> None:
    loaded = _load_experiment(REPO_ROOT / "configs/experiments" / name, REPO_ROOT)
    assert loaded.search is not None
    assert (loaded.policy.version, loaded.policy.digest.hex) == (4, V4_DIGEST)
    assert type(loaded.search.standard) is EvaluationStandard
    assert loaded.search.favored_regimes is None
