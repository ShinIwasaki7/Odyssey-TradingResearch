"""実験設定の書式 v2 と戦略ファイルの読込（D07 §18、D04 §13.1、ADR-0018）。

**正本の設定ファイルをそのまま読む**。拒否の試験は正本を作業場へ写し、1か所だけ書き換えた
ものを読む（書き方が変われば試験も追従する）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.config import load_calendar, load_symbol_specs, load_timeframes
from odyssey_fx.app.config.experiment import NO_DELAY_REF, ExperimentConfig, load_experiment
from odyssey_fx.app.config.experiment_v2 import (
    ExperimentV2,
    experiment_schema_version,
    load_experiment_v2,
)
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.strategy_file import load_strategy_file
from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.trace.manifest import config_digest_of
from odyssey_fx.common.refs import ConfigDigest
from odyssey_fx.common.symbol import Symbol, SymbolSpec
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.declarations.entry_policy import AwaitConfirmation, ImmediateEntry
from tests.fixtures.acceptance.t02_run import CASES
from tests.fixtures.strategy.strategy_a import strategy_a
from tests.fixtures.strategy.strategy_b import strategy_b

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS = REPO_ROOT / "configs"
V1_EXPERIMENT = CONFIGS / "experiments/strategy_a_t01.yaml"
V2_EXPERIMENT = CONFIGS / "experiments/strategy_a_t01_v2.yaml"
B_EXPERIMENTS = {
    case: CONFIGS / f"experiments/strategy_b_t02_{case}.yaml"
    for case in ("none", "d1_2s", "d1_25h", "d1_bar_hold")
}
B_STRATEGY = CONFIGS / "strategies/strategy_b_v1.yaml"

#: この試験が「この実装が持つ」とみなす指標集合の版（呼び出し側が渡す値。CLI は評価の実装
#: から渡す）。同梱の設定が書く版と揃える。
_METRIC_SET_VERSIONS = frozenset({1})


def _load(path: Path, repo_root: Path = REPO_ROOT) -> ExperimentV2:
    return load_experiment_v2(
        path,
        repo_root=repo_root,
        registry=INITIAL_CATALOG,
        metric_set_versions=_METRIC_SET_VERSIONS,
    )


def _timeframe_defs() -> dict[str, TimeframeDefinition]:
    return dict(load_timeframes(CONFIGS / "calendars/timeframes_v1.yaml"))


# --- 同梱の設定 -------------------------------------------------------------


def test_the_strategy_files_declare_the_validation_strategies() -> None:
    """戦略ファイルが検証戦略 A・B の宣言そのものに読める（D04 §13.1、D07 §18.4）。

    正本の宣言は段階2・3 の試験が使う組み立て（`tests/fixtures/strategy/`）である。
    """
    timeframes = _timeframe_defs()
    loaded_a = load_strategy_file(
        CONFIGS / "strategies/strategy_a_v1.yaml", INITIAL_CATALOG, timeframes
    )
    loaded_b = load_strategy_file(B_STRATEGY, INITIAL_CATALOG, timeframes)
    assert loaded_a == strategy_a()
    assert loaded_b == strategy_b()
    assert isinstance(loaded_a.entry_policy, ImmediateEntry)
    assert isinstance(loaded_b.entry_policy, AwaitConfirmation)
    assert len(loaded_b.opportunity_validity.bindings) == 1


@pytest.mark.parametrize("case", sorted(B_EXPERIMENTS))
def test_the_strategy_b_experiments_load_with_their_delay_scenarios(case: str) -> None:
    """検証戦略 B の4ケースが書け、遅延シナリオが T02 §1.4 の宣言に一致する（D07 §18.3）。

    遅延なしは `delay_scenario` を書かない形であり、解決結果は「シナリオなし」になる
    （T02 の組み立てが置く規則0件のシナリオと同じ意味）。
    """
    loaded = _load(B_EXPERIMENTS[case])
    experiment = loaded.experiment
    assert experiment.strategy == strategy_b()
    assert loaded.strategy_path == B_STRATEGY
    assert loaded.search_plan == "NONE"
    assert loaded.split == "NONE"
    assert loaded.hypothesis.strip()
    assert loaded.research_policy.policy_id == "research_policy"
    if case == "none":
        assert experiment.delay_scenario is None
        assert experiment.policy_ref("delay") == NO_DELAY_REF
    else:
        assert experiment.delay_scenario == CASES[case]
        assert experiment.policy_ref("delay") != NO_DELAY_REF


def test_the_delay_references_differ_between_the_cases() -> None:
    """遅延シナリオの版参照は宣言の内容から作るので、4ケースで互いに違う（D07 §18.3）。"""
    refs = {_load(path).experiment.policy_ref("delay") for path in B_EXPERIMENTS.values()}
    assert len(refs) == len(B_EXPERIMENTS)


def test_the_strategy_b_experiments_compile() -> None:
    """読んだ宣言が部品カタログと整合し、コンパイルを通る（D04 §12、D05 §5）。"""
    loaded = _load(B_EXPERIMENTS["d1_2s"])
    compiled = composition.compile_experiment_strategy(
        loaded.experiment, dict(loaded.environment.timeframe_defs)
    )
    assert compiled.strategy_ref.strategy_id == "strategy_b"


def _config_digest(
    experiment: ExperimentConfig,
    calendar: TradingCalendar,
    timeframe_defs: dict[str, TimeframeDefinition],
    symbol_specs: dict[Symbol, SymbolSpec],
) -> ConfigDigest:
    """合成（`execute_run`）と同じ材料で `ConfigDigest` を作る（D06 §9.3）。"""
    compiled = composition.compile_experiment_strategy(experiment, timeframe_defs)
    config = RunConfig(
        run_interval=experiment.run_interval,
        snapshot_ref=experiment.snapshot_ref,
        compiled_ref=compiled.compiled_ref,
        account=experiment.account,
        risk_policy_ref=experiment.policy_ref("risk"),
        execution_policy_ref=experiment.policy_ref("execution"),
        cost_model_ref=experiment.policy_ref("cost"),
        conversion_policy_ref=experiment.policy_ref("conversion"),
        delay_scenario_ref=experiment.policy_ref("delay"),
        execution_series=experiment.execution_series,
        seed=experiment.seed,
    )
    return config_digest_of(
        config,
        symbol_spec_ref=composition.symbol_spec_ref_of(
            symbol_specs[experiment.execution_series.symbol]
        ),
        calendar_ref=composition.calendar_ref_of(calendar),
        timeframe_def_refs=tuple(sorted((item.ref for item in timeframe_defs.values()), key=str)),
    )


def test_strategy_a_resolves_to_the_same_config_digest_in_v1_and_v2() -> None:
    """検証戦略 A の書式 v1 と v2 は同じ `ConfigDigest` になる（D07 §18.5）。

    書式を v2 へ書き換えただけで `run_id` が変わると、同じ実行が別の識別子になる。
    仮説・研究ポリシーの参照・指標集合の版は `ConfigDigest` に入らない（D07 §18.2）。
    """
    timeframes = _timeframe_defs()
    v1 = load_experiment(V1_EXPERIMENT, timeframes, INITIAL_CATALOG)
    v1_digest = _config_digest(
        v1,
        load_calendar(CONFIGS / "calendars/fx_ny17_v1.yaml"),
        timeframes,
        dict(load_symbol_specs(CONFIGS / "symbols")),
    )
    v2 = _load(V2_EXPERIMENT)
    environment = v2.environment
    v2_digest = _config_digest(
        v2.experiment,
        environment.calendar,
        dict(environment.timeframe_defs),
        dict(environment.symbol_specs),
    )
    assert v2.experiment == v1
    assert v2_digest == v1_digest


def test_the_schema_version_selects_the_reader() -> None:
    """`schema_version` で書式を分岐する（D07 §18.5）。"""
    assert experiment_schema_version(V1_EXPERIMENT) == 1
    assert experiment_schema_version(V2_EXPERIMENT) == 2


# --- 拒否（D07 §18.6）-------------------------------------------------------


def _workspace(tmp_path: Path) -> Path:
    """正本の設定を写した作業場（書式 v2 のパスはこの根から解決される）。"""
    for relative in (
        "configs/calendars/fx_ny17_v1.yaml",
        "configs/calendars/timeframes_v1.yaml",
        "configs/symbols/USDJPY.yaml",
        "configs/strategies/strategy_b_v1.yaml",
        "configs/experiments/strategy_b_t02_d1_2s.yaml",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative).read_bytes())
    return tmp_path


def _variant(tmp_path: Path, old: str, new: str, *, relative: str | None = None) -> Path:
    """作業場の1ファイルの1か所だけを書き換え、実験設定の位置を返す。"""
    root = _workspace(tmp_path)
    target = root / (relative or "configs/experiments/strategy_b_t02_d1_2s.yaml")
    text = target.read_text(encoding="utf-8")
    assert text.count(old) == 1, (old, target)
    target.write_text(text.replace(old, new), encoding="utf-8")
    return root / "configs/experiments/strategy_b_t02_d1_2s.yaml"


def _refused(path: Path, root: Path) -> str:
    with pytest.raises(ConfigError) as caught:
        _load(path, root)
    return str(caught.value)


def test_the_unchanged_workspace_loads(tmp_path: Path) -> None:
    """書き換えない写しは読める（以下の拒否が書き換えた1か所によることの確認）。"""
    root = _workspace(tmp_path)
    _load(root / "configs/experiments/strategy_b_t02_d1_2s.yaml", root)


_REFUSALS: list[tuple[str, str, str, str | None, str]] = [
    # (名前, 書き換え前, 書き換え後, 書き換えるファイル, 理由に含まれる語)
    ("undeclared key", "seed: 0", "seed: 0\nextra_key: 1", None, "extra_key"),
    ("type mismatch", "seed: 0", 'seed: "0"', None, "seed"),
    ("empty hypothesis", 'hypothesis: "日足の', 'hypothesis: "   "\nnote: "日足の', None, "note"),
    ("search plan", 'search_plan: "NONE"', 'search_plan: "GRID"', None, "search_plan"),
    ("split", 'split: "NONE"', 'split: "WALK_FORWARD"', None, "split"),
    (
        "empty rules",
        '  rules:\n    - {kind: "FIXED_SERIES_DELAY", series: "USDJPY/1d_ny17/bid", delay: "2s"}',
        "  rules: []",
        None,
        "rules",
    ),
    (
        "seeded random delay",
        '{kind: "FIXED_SERIES_DELAY", series: "USDJPY/1d_ny17/bid", delay: "2s"}',
        '{kind: "SEEDED_RANDOM_DELAY", series: "USDJPY/1d_ny17/bid", seed: 1, max_delay: "2s"}',
        None,
        "SEEDED_RANDOM_DELAY",
    ),
    (
        "delay on the execution series",
        'series: "USDJPY/1d_ny17/bid", delay: "2s"}',
        'series: "USDJPY/15m/bid", delay: "2s"}',
        None,
        "執行系列",
    ),
    ("negative delay", 'delay: "2s"', 'delay: "-2s"', None, "rules[0]"),
    ("iso delay", 'delay: "2s"', 'delay: "PT2S"', None, "rules[0]"),
    (
        "missing strategy file",
        'strategy: "configs/strategies/strategy_b_v1.yaml"',
        'strategy: "configs/strategies/missing.yaml"',
        None,
        "strategy",
    ),
    (
        "missing calendar",
        "calendars/fx_ny17_v1.yaml",
        "calendars/missing.yaml",
        None,
        "environment.calendar",
    ),
    (
        "absolute path",
        '"configs/strategies/strategy_b_v1.yaml"',
        '"/etc/strategy_b_v1.yaml"',
        None,
        "相対パス",
    ),
    (
        "path outside the root",
        '"configs/strategies/strategy_b_v1.yaml"',
        '"../strategy_b_v1.yaml"',
        None,
        "外を指している",
    ),
    ("metric set version", "metric_set_version: 1", "metric_set_version: 99", None, "99"),
    ("schema version", "schema_version: 2", "schema_version: 3", None, "schema_version"),
    (
        "v1 entry policy string",
        'entry_policy:\n  kind: "AWAIT_CONFIRMATION"\n  deadline: {kind: "BARS", bars: 4}\n'
        '  on_deadline: "EXPIRE"',
        'entry_policy: "IMMEDIATE"',
        "configs/strategies/strategy_b_v1.yaml",
        "entry_policy",
    ),
    (
        "omitted validity",
        'opportunity_validity:\n  bindings:\n    - source: "daily_above_ema.condition"\n'
        '      mode: "REQUIRE_UNTIL_ORDER_REQUEST"\n      on_missing: {kind: "SKIP_EVALUATION"}\n',
        "",
        "configs/strategies/strategy_b_v1.yaml",
        "opportunity_validity",
    ),
    (
        "omitted binding policy",
        '      on_missing: {kind: "SKIP_EVALUATION"}\n',
        "",
        "configs/strategies/strategy_b_v1.yaml",
        "on_missing",
    ),
    (
        "iso deadline in the strategy",
        'deadline: {kind: "BARS", bars: 4}',
        'deadline: {kind: "DURATION", duration: "PT1H"}',
        "configs/strategies/strategy_b_v1.yaml",
        "duration",
    ),
    (
        "strategy schema version",
        "schema_version: 1\n\nstrategy_id",
        "schema_version: 2\n\nstrategy_id",
        "configs/strategies/strategy_b_v1.yaml",
        "schema_version",
    ),
]


@pytest.mark.parametrize(
    ("old", "new", "relative", "expected"),
    [item[1:] for item in _REFUSALS],
    ids=[item[0] for item in _REFUSALS],
)
def test_invalid_settings_are_refused_on_load(
    tmp_path: Path, old: str, new: str, relative: str | None, expected: str
) -> None:
    """D07 §18.6 の拒否を読込の時点で `ConfigError` にする（検査を後段へ回さない）。"""
    path = _variant(tmp_path, old, new, relative=relative)
    assert expected in _refused(path, tmp_path)


def test_a_blank_hypothesis_is_refused(tmp_path: Path) -> None:
    """空白だけの仮説は拒否する（D07 §18.2・§18.6 の2）。"""
    root = _workspace(tmp_path)
    path = root / "configs/experiments/strategy_b_t02_d1_2s.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    rewritten = ['hypothesis: "  "' if line.startswith("hypothesis:") else line for line in lines]
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    assert "hypothesis" in _refused(path, root)


def test_a_null_delay_scenario_is_refused(tmp_path: Path) -> None:
    """遅延なしを表すのは `delay_scenario` を書かない形だけ（D07 §18.3）。"""
    root = _workspace(tmp_path)
    path = root / "configs/experiments/strategy_b_t02_d1_2s.yaml"
    text = path.read_text(encoding="utf-8")
    start = text.index("delay_scenario:")
    end = text.index("evaluation:")
    path.write_text(text[:start] + "delay_scenario: null\n\n" + text[end:], encoding="utf-8")
    assert "null" in _refused(path, root)


def _without_delay(text: str) -> str:
    start = text.index("delay_scenario:")
    end = text.index("evaluation:")
    return text[:start] + text[end:]


def test_omitting_the_delay_scenario_gives_the_v1_no_delay_reference(tmp_path: Path) -> None:
    """`delay_scenario` を消すと、版参照は書式 v1 の遅延なしと同じ値になる（D07 §18.3）。"""
    root = _workspace(tmp_path)
    path = root / "configs/experiments/strategy_b_t02_d1_2s.yaml"
    path.write_text(_without_delay(path.read_text(encoding="utf-8")), encoding="utf-8")
    experiment = _load(path, root).experiment
    assert experiment.delay_scenario is None
    assert experiment.policy_ref("delay") == NO_DELAY_REF
