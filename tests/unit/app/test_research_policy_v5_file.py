"""登録した研究ポリシー第 5 版のファイルと、登録の後の版の選び方・読込・判定（D24 §7.1・§8.2・§9.1、
§10.2 の受入テスト RP5-1・RP5-17。順 11 の登録の PR）。

- 第 5 版のファイルは登録簿の 1 行（用途 STANDARD）と一致し、D24 §7.1 の表の値を持つ。第 4 版から
  引き継ぐ値（評価範囲・fold・選定・証拠の要件・前提の記録・比較の前提・上限）は第 4 版と同じ
  （D23 の仮置き 10）。足した値は判定の試験が使う人工の評価基準（`loss_side_units.v5_standard`）と
  同じなので、RP5-6〜RP5-11 の試験は登録した値で判定を確かめている。
- 登録の後は、現行の版（用途 STANDARD の最大の版）が第 5 版になり、仮説カードの道具は第 5 版と
  指標集合 v4 を選ぶ。第 5 版を指す探索の実験は指標集合 v4 でだけ読め、判定は第 5 版の条件を当てる。
- 第 4 版・第 3 版のファイル・ダイジェストと、それを指す既存の実験設定の読込は変わらない。第 4 版を
  指す実行が審査の入口で止まる（`ENTRY_POLICY_NOT_CURRENT`）のは決定 142 の帰結で、その規則の試験は
  審査の入口の試験にある。

テストは設定ファイルと人工の実験設定・人工の単位の結果だけを読み、snapshot も価格も戦略の成績も
読まない。
"""

from __future__ import annotations

import dataclasses
import difflib
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.research_policy import (
    load_research_policy,
    load_research_policy_registry,
    research_policy_path,
    research_policy_registry_path,
    verify_registered,
)
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS, MetricId
from odyssey_fx.evaluation.domain.regimes import RegimeLabelAt, RegimeRole, RegimeTimeframe
from odyssey_fx.evaluation.domain.research_policy import ResearchPolicy, current_standard_version
from odyssey_fx.evaluation.domain.search import (
    Comparator,
    ConditionScope,
    DeflatedDownsideResult,
    DeflationMethod,
    EvaluationStandard,
    EvaluationStandardV5,
    LossConcentrationResult,
    MetricCondition,
    NeighborConditionResult,
    Neighborhood,
    NeighborInterval,
    PathBoundary,
    PathRuleV5,
    ProfitFactorResult,
    SearchVerdict,
    StandardPurpose,
    TrialCorrelation,
    ValidationRuleV5,
)
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.evaluation.loss_side_units import loss_side, v5_path, v5_standard
from tests.fixtures.evaluation.search_units import (
    evidence,
    judge,
    make_fold,
    path_summary,
    standard,
    train_unit,
    validation_unit,
)
from tests.unit.app.test_research_policy_v4_file import (
    _COPIED as V4_COPIED,
)
from tests.unit.app.test_research_policy_v4_file import (
    EIGHT,
    JPY_PAIRS,
    _experiment_text,
    _premise_workspace,
)
from tools.ops.hypothesis_cards.generate import _metric_set_version, select_policy
from tools.ops.hypothesis_cards.repo import DirView

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
POLICY_DIR: Final = Path("configs/policies/research")
POLICY_V4: Final = POLICY_DIR / "research_policy_v4.yaml"
POLICY_V5: Final = POLICY_DIR / "research_policy_v5.yaml"
#: 登録簿の各版のダイジェスト（版 3・4 は変えない。版 5 は読込が計算した値を登録した）。
V3_DIGEST: Final = "f2e5d8f47489beac23d340438684aafc20c83ff7c7a1619e2aa6eb4b94ef1c3b"
V4_DIGEST: Final = "5e9ac02b3e8102479ad843852833757acc0b92747501af823b2f940aecfc7149"
V5_DIGEST: Final = "3aff86c5c46f3848e060dfa2ace1d9971e84146c4c7a84190c28eefb8ef8f663"
#: 第 4 版を指す既存の探索の実験設定（版管理の設定ファイル。結果は読まない）。
V4_SEARCHES: Final = (
    "h6_h1_breakout_m15_ema_confirm_eurusd.yaml",
    "h6_h1_breakout_m15_ema_confirm_eurusd_v2.yaml",
    "h7_h1_dual_ema_cross_eurusd.yaml",
    "h7_h1_dual_ema_cross_eurusd_v2.yaml",
)
_D = Decimal


def _policy(version: int, *, root: Path = REPO_ROOT, text: str | None = None) -> ResearchPolicy:
    return load_research_policy(
        research_policy_path(root, "research_policy", version),
        policy_id="research_policy",
        version=version,
        text=text,
        repo_root=root,
    )


def _v5_standard() -> EvaluationStandardV5:
    standard_v5 = _policy(5).evaluation_standard
    assert isinstance(standard_v5, EvaluationStandardV5)
    return standard_v5


def _v4_standard() -> EvaluationStandard:
    standard_v4 = _policy(4).evaluation_standard
    assert standard_v4 is not None and not isinstance(standard_v4, EvaluationStandardV5)
    return standard_v4


def _replaced(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


# --- RP5-1: 第 5 版のファイルと登録簿の行 ------------------------------------------------------


def test_rp5_1_the_v5_file_matches_its_registry_line() -> None:
    policy = _policy(5)
    registry = research_policy_registry_path(REPO_ROOT)
    entry = verify_registered(
        policy, load_research_policy_registry(registry), registry_path=registry
    )
    assert entry.version == 5 and entry.purpose is StandardPurpose.STANDARD
    assert policy.digest.hex == entry.digest.hex == V5_DIGEST


def test_the_registry_holds_versions_one_to_five_and_the_old_lines_are_unchanged() -> None:
    entries = load_research_policy_registry(research_policy_registry_path(REPO_ROOT))
    assert [(item.policy_id, item.version) for item in entries] == [
        ("research_policy", version) for version in range(1, 6)
    ]
    digests = {item.version: item.digest.hex for item in entries}
    assert (digests[3], digests[4], digests[5]) == (V3_DIGEST, V4_DIGEST, V5_DIGEST)


def test_a_changed_v5_file_is_refused_by_the_registry() -> None:
    text = _replaced(
        (REPO_ROOT / POLICY_V5).read_text(encoding="utf-8"),
        'min_probability: "0.95"',
        'min_probability: "0.90"',
    )
    policy = _policy(5, text=text)
    registry = research_policy_registry_path(REPO_ROOT)
    with pytest.raises(ConfigError, match="digest|ダイジェスト"):
        verify_registered(policy, load_research_policy_registry(registry), registry_path=registry)


# --- D24 §7.1 の値 --------------------------------------------------------------------------


def test_the_added_values_are_those_of_d24_table_7_1() -> None:
    standard_v5 = _v5_standard()
    validation = standard_v5.validation
    assert isinstance(validation, ValidationRuleV5)
    assert isinstance(validation.path, PathRuleV5)
    # fold の床: 第 4 版のドローダウン率 ≤ 0.30 と、区間末の評価を含む純収益率 ≥ −0.15（決定 133）。
    assert validation.fold_floors == (
        MetricCondition(MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, Comparator.LE, _D("0.30")),
        MetricCondition(MetricId.NET_RETURN_RATE_CLOSED_OUT, Comparator.GE, _D("-0.15")),
    )
    assert validation.aggregate == ()
    assert validation.path.boundary is PathBoundary.CARRY_IF_SAME_TRIAL
    assert validation.path.floors == (
        MetricCondition(MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, Comparator.LE, _D("0.30")),
    )
    # 経路の全体: 純収益率 > 0（第 4 版）と PROFIT_FACTOR ≥ 1.2（決定 137・159）。
    assert validation.path.aggregate == (
        MetricCondition(MetricId.NET_RETURN_RATE, Comparator.GT, _D("0")),
        MetricCondition(MetricId.PROFIT_FACTOR, Comparator.GE, _D("1.2")),
    )
    # 損失の集中: 倍数 1.0（決定 136）、当てる閾 0.10（決定 155・160）。
    concentration = validation.loss_concentration
    assert concentration.worst_fold_metric is MetricId.NET_RETURN_RATE_CLOSED_OUT
    assert concentration.max_multiple_of_path_net == _D("1.0")
    assert concentration.applies_when_path_net_return_at_least == _D("0.10")
    # 近傍の床: 選定の区間でドローダウン率 ≤ 0.30（決定 139）・純収益率 ≥ −0.30（決定 158）。
    neighbor = validation.neighbor_floors
    assert neighbor.neighborhood is Neighborhood.ONE_STEP_IN_WRITTEN_ORDER
    assert neighbor.interval is NeighborInterval.TRAIN
    assert neighbor.floors == (
        MetricCondition(MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, Comparator.LE, _D("0.30")),
        MetricCondition(MetricId.NET_RETURN_RATE_CLOSED_OUT, Comparator.GE, _D("-0.30")),
    )
    # 割り引いた確率: 方式（決定 163）・目標値 0（決定 161）・相関（決定 162）・確率（決定 140）。
    deflated = validation.deflated_downside_ratio
    assert deflated.method is DeflationMethod.DEFLATED_SHARPE_RATIO_FORM
    assert deflated.downside_target == _D("0")
    assert deflated.correlation is TrialCorrelation.MEAN_PAIRWISE_PEARSON
    assert deflated.min_probability == _D("0.95")
    # 局面（決定 131・132・134）: 表示・分析用だけ。
    regimes = standard_v5.regimes
    assert regimes.role is RegimeRole.DISPLAY_ONLY
    assert regimes.timeframe is RegimeTimeframe.DAILY
    assert (
        regimes.direction.ema_bars,
        regimes.direction.slope_bars,
        regimes.direction.atr_bars,
        regimes.direction.dead_band_atr_multiple,
    ) == (50, 20, 20, _D("0.5"))
    assert (regimes.volatility.atr_bars, regimes.volatility.median_window_bars) == (20, 250)
    assert regimes.label_at is RegimeLabelAt.LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY


def test_the_v4_values_are_carried_over_unchanged() -> None:
    v4 = _policy(4)
    v5 = _policy(5)
    assert (v5.limits, v5.trial_limit) == (v4.limits, v4.trial_limit)
    old = _v4_standard()
    new = _v5_standard()
    for name in ("purpose", "basis", "comparison_basis", "split", "selection", "sufficiency"):
        assert getattr(new, name) == getattr(old, name), name
    assert new.validation.fold_floors[:1] == old.validation.fold_floors
    assert new.validation.aggregate == old.validation.aggregate
    assert (new.validation.path.boundary, new.validation.path.floors) == (
        old.validation.path.boundary,
        old.validation.path.floors,
    )
    assert new.validation.path.aggregate[:1] == old.validation.path.aggregate


def test_the_v5_body_is_the_v4_body_plus_the_d24_lines_only() -> None:
    """注記を除く本文は、版の番号と D24 §2.1 の新しい行のほかは第 4 版と 1 行も違わない。"""

    def body(path: Path) -> list[str]:
        lines = path.read_text(encoding="utf-8").splitlines()
        return [line for line in lines if line and not line.startswith("#")]

    old = body(REPO_ROOT / POLICY_V4)
    new = body(REPO_ROOT / POLICY_V5)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    changes = [
        (tag, old[i1:i2], new[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]
    assert changes[0] == ("replace", ["version: 4"], ["version: 5"])
    # 残りは足した行だけで、第 4 版の行を消しも書き換えもしない。
    assert {tag for tag, _, _ in changes[1:]} == {"insert"}
    added = [line.strip() for _, _, lines in changes[1:] for line in lines]
    assert added == [
        # fold の純損失の床
        '- {metric: NET_RETURN_RATE_CLOSED_OUT, comparator: GE, threshold: "-0.15"}',
        # 経路の PROFIT_FACTOR
        '- {metric: PROFIT_FACTOR, comparator: GE, threshold: "1.2"}',
        # 損失の集中
        "loss_concentration:",
        "worst_fold_metric: NET_RETURN_RATE_CLOSED_OUT",
        'max_multiple_of_path_net: "1.0"',
        'applies_when_path_net_return_at_least: "0.10"',
        # 近傍の床
        "neighbor_floors:",
        "neighborhood: ONE_STEP_IN_WRITTEN_ORDER",
        "interval: TRAIN",
        "floors:",
        '- {metric: MAX_DRAWDOWN_MTM_RATE_PEAK, comparator: LE, threshold: "0.30"}',
        '- {metric: NET_RETURN_RATE_CLOSED_OUT, comparator: GE, threshold: "-0.30"}',
        # 試行数で割り引いた確率
        "deflated_downside_ratio:",
        "method: DEFLATED_SHARPE_RATIO_FORM",
        'downside_target: "0"',
        "correlation: MEAN_PAIRWISE_PEARSON",
        'min_probability: "0.95"',
        # 局面（表示・分析用だけ）
        "regimes:",
        "role: DISPLAY_ONLY",
        'timeframe: "1d"',
        'direction: {ema_bars: 50, slope_bars: 20, atr_bars: 20, dead_band_atr_multiple: "0.5"}',
        "volatility: {atr_bars: 20, median_window_bars: 250}",
        "label_at: LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY",
    ]


def test_the_test_standard_of_the_judgement_tests_has_the_registered_values() -> None:
    """判定の試験（RP5-6〜RP5-11）が使う人工の評価基準の条件と局面は、登録した値と同じ。"""
    registered = _v5_standard()
    fixture = v5_standard()
    assert fixture.validation == registered.validation
    assert fixture.sufficiency == registered.sufficiency
    assert fixture.regimes == registered.regimes


# --- 登録の後の版の選び方 -------------------------------------------------------------------


def test_the_current_version_is_v5_after_the_registration() -> None:
    entries = load_research_policy_registry(research_policy_registry_path(REPO_ROOT))
    assert current_standard_version(entries, "research_policy") == 5
    # 第 4 版を指す記録は「現行でない」側になる（審査の入口の検査 6 の比べ方。ADR-0044 第3項）。
    assert current_standard_version(entries, "research_policy") != 4


def test_the_hypothesis_card_tool_selects_v5_and_metric_set_four() -> None:
    choice = select_policy(DirView(REPO_ROOT))
    assert (choice.version, choice.path) == (5, str(POLICY_V5))
    assert choice.policy.digest.hex == V5_DIGEST
    assert _metric_set_version(choice.policy) == 4


# --- 第 5 版を指す探索の実験の読込 -----------------------------------------------------------


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = _premise_workspace(tmp_path_factory.mktemp("policy-v5"))
    for relative in (*V4_COPIED, str(POLICY_V5)):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    shutil.copytree(REPO_ROOT / "configs/symbols", root / "configs/symbols", dirs_exist_ok=True)
    strategy = (REPO_ROOT / "configs/strategies/strategy_b_v1.yaml").read_text(encoding="utf-8")
    for symbol in EIGHT:
        (root / f"configs/strategies/strategy_b_{symbol.lower()}.yaml").write_text(
            strategy.replace("USDJPY/", f"{symbol}/"), encoding="utf-8"
        )
    return root


def _v5_experiment_text(symbol: str, *, metric_set: int = 4) -> str:
    text = _replaced(
        _experiment_text(symbol),
        'research_policy: {id: "research_policy", version: 4}',
        'research_policy: {id: "research_policy", version: 5}',
    )
    return _replaced(
        text,
        "evaluation: {metric_set_version: 3}",
        f"evaluation: {{metric_set_version: {metric_set}}}",
    )


def _load(root: Path, name: str, text: str) -> ExperimentV2:
    path = root / "configs/experiments" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return load_experiment_v2(
        path, repo_root=root, registry=INITIAL_CATALOG, metric_set_versions=METRIC_SET_VERSIONS
    )


@pytest.mark.parametrize("symbol", ["USDJPY", "EURUSD"])
def test_a_search_on_v5_with_metric_set_four_loads(workspace: Path, symbol: str) -> None:
    loaded = _load(workspace, f"v5_ok_{symbol}", _v5_experiment_text(symbol))
    assert loaded.search is not None
    assert (loaded.policy.version, loaded.policy.digest.hex) == (5, V5_DIGEST)
    assert isinstance(loaded.policy.evaluation_standard, EvaluationStandardV5)
    assert len(loaded.search.split.folds) == 5
    assert (symbol in JPY_PAIRS) is (loaded.search.conversion_snapshots is None)


def test_a_search_on_v5_with_metric_set_three_is_refused(workspace: Path) -> None:
    with pytest.raises(ConfigError, match="指標集合|metric_set"):
        _load(workspace, "v5_ms3", _v5_experiment_text("USDJPY", metric_set=3))


# --- 判定: 第 5 版の条件が当たり、第 4 版の判定は変わらない ------------------------------------


def _folds(rule: EvaluationStandard) -> list:  # type: ignore[type-arg]
    trains = [
        train_unit(0, {MetricId.NET_RETURN_RATE: _D("0.05")}),
        train_unit(1, {MetricId.NET_RETURN_RATE: _D("0.20")}),
        train_unit(2, {MetricId.NET_RETURN_RATE: _D("0.10")}),
    ]
    return [evidence(rule, make_fold(index), trains, validation_unit(1, {})) for index in range(2)]


def test_the_registered_v5_conditions_are_applied_by_the_judgement() -> None:
    registered = _v5_standard()
    # 人工の分割・選定（`search_units.standard`）に、登録した第 5 版の条件・証拠・局面を載せる。
    rule = dataclasses.replace(
        v5_standard(),
        validation=registered.validation,
        sufficiency=registered.sufficiency,
        regimes=registered.regimes,
    )
    outcome = judge(rule, _folds(rule), v5_path(2), loss_side=loss_side())
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    kinds = {type(item) for item in outcome.condition_results}
    assert {
        ProfitFactorResult,
        LossConcentrationResult,
        NeighborConditionResult,
        DeflatedDownsideResult,
    } <= kinds
    fold_metrics = {
        item.metric for item in outcome.condition_results if item.scope is ConditionScope.FOLD_FLOOR
    }
    assert MetricId.NET_RETURN_RATE_CLOSED_OUT in fold_metrics


def test_the_v4_judgement_has_no_v5_conditions() -> None:
    registered = _v4_standard()
    rule = dataclasses.replace(
        standard(),
        validation=registered.validation,
        sufficiency=registered.sufficiency,
    )
    outcome = judge(rule, _folds(rule), path_summary(2))
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    scopes = {item.scope for item in outcome.condition_results}
    assert not scopes & {
        ConditionScope.NEIGHBOR_FLOOR,
        ConditionScope.LOSS_CONCENTRATION,
        ConditionScope.DEFLATED_DOWNSIDE_RATIO,
    }
    assert not any(isinstance(item, ProfitFactorResult) for item in outcome.condition_results)


# --- RP5-17: 第 4 版・第 3 版と、それを指す既存の実験設定 ---------------------------------------


def test_rp5_17_the_v4_and_v3_files_and_digests_are_unchanged() -> None:
    assert _policy(4).digest.hex == V4_DIGEST
    assert _policy(3).digest.hex == V3_DIGEST


@pytest.mark.parametrize("name", V4_SEARCHES)
def test_rp5_17_the_existing_searches_on_v4_still_load(name: str) -> None:
    path = REPO_ROOT / "configs/experiments" / name
    assert load_yaml_mapping(path)["research_policy"]["version"] == 4
    loaded = load_experiment_v2(
        path, repo_root=REPO_ROOT, registry=INITIAL_CATALOG, metric_set_versions=METRIC_SET_VERSIONS
    )
    assert loaded.search is not None
    assert (loaded.policy.version, loaded.policy.digest.hex) == (4, V4_DIGEST)
