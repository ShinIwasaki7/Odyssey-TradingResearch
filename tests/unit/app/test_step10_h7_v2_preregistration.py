"""順 10 の改善後の実験（研究仮説 H7 の版 2、EURUSD）の事前登録の確認（D22 §2.1 の手順 4）。

順 10 の版 1 の確認（`test_step10_h7_preregistration.py`。D22 の仮置き 27）と、順 8 の版 2 の確認
（`test_step8_h6_v2_preregistration.py`）と同じ形。

`experiment run` は設定の読込の検査と系譜の台帳との照合を run の開始時にしか当てないので、
事前登録の PR の merge の前に、登録する版 2 の実験設定がそのまま読め、系譜の元の実行が台帳に
あり、格子の全試行の戦略がコンパイルを通ることをここで確かめる。

- 実験設定 `configs/experiments/h7_h1_dual_ema_cross_eurusd_v2.yaml` が書式 v2 の読込
  （`load_experiment_v2`。研究ポリシー第 4 版の照合 C1〜C7 と `final_holdout` の検査 4 を含む）を
  通る。実験名は版 1 と同じで `version: 2`。
- 系譜は版 1 の実行番号 1 を指し、変更の種類は `STRATEGY_CODE` の 1 つ、理由の文は分析メモの
  SHA-256 を含む。元の実行の開始の行と結末の行が版管理された台帳にある。
- 版 1 から変えたのは戦略宣言だけ（人間の決定 127: 日足の局面を足す）。仮説の文・格子・研究
  ポリシー・両組の snapshot・換算の snapshot・分割・`final_holdout` の宣言・実行条件の本体の
  戦略以外は版 1 と同じ（変更が 1 種類であることの機械的な確認）。
- 戦略宣言の版 2 は、版 1 の 7 部品をそのまま持ち、日足の局面の 4 段（H2・検証戦略 B と同じ部品と
  版）と役割 `market_state` だけを足したもの。日足の部品の値と接続は分析メモ「3. 変更の候補」の
  1 位の項のとおり。
- 全 4 試行の戦略がコンパイルを通り、複雑性（事前検査 P6）と試行の数（事前検査 P7）が研究ポリシー
  第 4 版の上限以下。どの試行も版 1 のどの試行ともコンパイル結果が違う（戦略が変わったため）。
- 注記 `docs/data/eurusd_step10_research_loop/README.md` の第 3 節（変更の記録）に、判定の値
  （4 値・2 軸・fold の判定）の識別子と表示の語が無く（決定 104・110・113）、決定 127 の 3 行目の
  分析メモの SHA-256 がある。

**run はしない**。テストは設定ファイルと台帳だけを読み、snapshot の実体も戦略の成績も読まない
（台帳の結末の行の判定の値は検査しない）。
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app.composition import complexity_profiles, trial_strategy
from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import TRIAL_LEDGER_PATH, read_trial_ledger_file
from odyssey_fx.evaluation.adapters.search_report import (
    _EVIDENCE_NAMES,
    _FOLD_VERDICT_NAMES,
    _PROFITABILITY_NAMES,
    _VERDICT_NAMES,
)
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.research_policy import (
    check_complexity,
    check_trial_count,
    measure_complexity,
)
from odyssey_fx.evaluation.domain.search import (
    ChangeKind,
    EvidenceAxis,
    FoldVerdict,
    ProfitabilityAxis,
    SearchVerdict,
    enumerate_assignments,
    has_finished_line,
    started_line_of,
)
from odyssey_fx.evaluation.domain.splits import FinalHoldoutSpec
from odyssey_fx.evaluation.domain.status import CheckOutcome
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.entry_policy import ImmediateEntry
from odyssey_fx.strategy.declarations.evaluation import OnBarClose
from odyssey_fx.strategy.declarations.refs import MarketDataRef, OutputRef
from odyssey_fx.strategy.declarations.specs import BoolValue, IntValue, StrValue

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
EXPERIMENT_V1: Final = REPO_ROOT / "configs/experiments/h7_h1_dual_ema_cross_eurusd.yaml"
EXPERIMENT_V2: Final = REPO_ROOT / "configs/experiments/h7_h1_dual_ema_cross_eurusd_v2.yaml"
STRATEGY_V1: Final = REPO_ROOT / "configs/strategies/h7_h1_dual_ema_cross_eurusd_v1.yaml"
STRATEGY_V2: Final = REPO_ROOT / "configs/strategies/h7_h1_dual_ema_cross_eurusd_v2.yaml"
README: Final = REPO_ROOT / "docs/data/eurusd_step10_research_loop/README.md"
#: 版 1 の記録票の識別子と実行番号（台帳の行。README §2.1）。
SOURCE_EXPERIMENT_ID: Final = "6a9a3c8a28076e881243ca76babc94470eaf8c92ae51c15eccb43a7a9cf9b78a"
SOURCE_EXECUTION: Final = 1
#: 分析メモの SHA-256（人間の決定 127 の 3 行目。理由の文に書く）。
ANALYSIS_MEMO_SHA256: Final = "fa64326f3af8e381aa49544468babaef3d554ad36428c9f5ec3cc85636ea28a8"
#: D22 §1.4 の宣言の形（決定 114・115、仮置き 13）。版 1 と同じ値（D22 §7.1.2）。
EXPECTED_FINAL_HOLDOUT: Final = FinalHoldoutSpec(
    interval=Interval(
        start=UtcTime.parse("2024-01-01T00:00:00Z"),
        end=UtcTime.parse("2100-01-01T00:00:00Z"),
    ),
    purpose="研究 3 巡目（順 10）の研究候補の最終検証（D09 §9.10）",
)
#: 版 1 の部品（取引機会と出口）。版 2 でも同じ宣言のまま残る。
V1_INSTANCES: Final = frozenset(
    {
        "fast_ema",
        "slow_ema",
        "stop_level",
        "entry_trigger",
        "entry_order",
        "initial_stop",
        "take_profit",
    }
)
#: 版 2 で足した日足の局面の 4 段（分析メモ「3. 変更の候補」の 1 位の項。H2・検証戦略 B と同じ）。
DAILY_VERSIONS: Final = {
    "daily_ema": ("ema", 2),
    "daily_above_ema": ("price_compare", 2),
    "no_short": ("constant_condition", 1),
    "market_state": ("permission_from_condition", 2),
}
DAILY_SERIES: Final = "EURUSD/1d_ny17/bid"


def _load(path: Path) -> ExperimentV2:
    return load_experiment_v2(
        path,
        repo_root=REPO_ROOT,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )


@pytest.fixture(scope="module")
def loaded() -> ExperimentV2:
    return _load(EXPERIMENT_V2)


@pytest.fixture(scope="module")
def original() -> ExperimentV2:
    return _load(EXPERIMENT_V1)


def _compiled_refs(experiment: ExperimentV2) -> list[object]:
    search = experiment.search
    assert search is not None
    timeframes = {
        definition.ref: definition for definition in experiment.environment.timeframe_defs.values()
    }
    refs: list[object] = []
    for assignment in enumerate_assignments(search.plan):
        definition = trial_strategy(experiment.body.strategy, assignment)
        outcome = compile_strategy(definition, INITIAL_CATALOG, timeframes)
        assert isinstance(outcome, CompileSucceeded), outcome
        refs.append(outcome.compiled.compiled_ref)
    return refs


def test_version_2_changes_only_the_strategy_declaration(
    loaded: ExperimentV2, original: ExperimentV2
) -> None:
    """同じ実験名の版 2 で、仮説・研究ポリシー・両組の snapshot・換算・分割・`final_holdout` は
    版 1 と同じ。

    実行条件の本体は版の番号と戦略宣言だけが違う。戦略宣言は同じ識別子の版 2。
    """
    search = loaded.search
    assert search is not None and original.search is not None
    assert (loaded.values["id"], loaded.values["version"]) == ("h7_h1_dual_ema_cross_eurusd", 2)
    assert (original.values["id"], original.values["version"]) == (
        "h7_h1_dual_ema_cross_eurusd",
        1,
    )
    assert loaded.hypothesis == original.hypothesis
    assert loaded.research_policy == original.research_policy
    assert loaded.policy.version == 4
    assert loaded.metric_set_version == original.metric_set_version == 3
    for variant in (Variant.STANDARD, Variant.CANDIDATE):
        # 実行条件の本体（snapshot・換算・口座・リスク・執行・費用など）は版の番号と戦略だけが違う。
        assert replace(
            loaded.body_of(variant), version=1, strategy=original.body.strategy
        ) == original.body_of(variant)
    assert loaded.strategy_path is not None and loaded.strategy_path.resolve() == STRATEGY_V2
    assert original.strategy_path is not None and original.strategy_path.resolve() == STRATEGY_V1
    assert loaded.texts["strategy"] != original.texts["strategy"]
    strategy = loaded.body.strategy
    assert (strategy.strategy_id, strategy.version) == ("h7_h1_dual_ema_cross_eurusd", 2)
    assert original.body.strategy.version == 1
    assert search.split == original.search.split
    assert search.final_holdout == original.search.final_holdout == EXPECTED_FINAL_HOLDOUT
    assert search.final_holdout is not None
    assert search.final_holdout.interval.start >= search.standard.split.range.end


def test_the_lineage_points_at_execution_1_of_version_1_with_one_change_kind(
    loaded: ExperimentV2,
) -> None:
    """系譜は版 1 の実行番号 1、変更の種類は STRATEGY_CODE の 1 つ。

    理由の文は分析メモの SHA-256 を持つ。
    """
    search = loaded.search
    assert search is not None and search.lineage is not None
    lineage = search.lineage
    assert lineage.derived_from is not None
    assert lineage.derived_from.experiment_id.digest.hex == SOURCE_EXPERIMENT_ID
    assert lineage.derived_from.execution == SOURCE_EXECUTION
    assert lineage.change_kinds == (ChangeKind.STRATEGY_CODE,)
    assert ANALYSIS_MEMO_SHA256 in lineage.note


def test_the_source_execution_is_in_the_versioned_trial_ledger(loaded: ExperimentV2) -> None:
    """元の実行の開始の行（run の開始時の照合と同じ条件。仮置き 56・65）と結末の行が台帳にある。"""
    search = loaded.search
    assert search is not None and search.lineage is not None
    source = search.lineage.derived_from
    assert source is not None
    contents = read_trial_ledger_file(REPO_ROOT / TRIAL_LEDGER_PATH)
    assert isinstance(contents, TrialLedgerContents), contents
    assert started_line_of(contents.lines, source.experiment_id, source.execution) is not None
    assert has_finished_line(contents.lines, source.experiment_id, source.execution)


def test_the_grid_is_the_same_as_version_1(loaded: ExperimentV2, original: ExperimentV2) -> None:
    """格子は版 1 と同じ（S {50, 100} × R {1.5, 3.0}、4 試行、試行番号の順も同じ）。"""
    search = loaded.search
    assert search is not None and original.search is not None
    assert search.plan == original.search.plan
    assert search.plan.max_trials == 4
    assignments = enumerate_assignments(search.plan)
    assert assignments == enumerate_assignments(original.search.plan)
    limit = loaded.policy.trial_limit
    assert limit is not None
    assert check_trial_count(len(assignments), limit).outcome is CheckOutcome.PASSED


def test_every_trial_compiles_within_the_complexity_limits(
    loaded: ExperimentV2, original: ExperimentV2
) -> None:
    """全 4 試行がコンパイルを通り P6 が合格。どの試行も版 1 の試行とコンパイル結果が違う。"""
    search = loaded.search
    assert search is not None
    timeframes = {
        definition.ref: definition for definition in loaded.environment.timeframe_defs.values()
    }
    for assignment in enumerate_assignments(search.plan):
        definition = trial_strategy(loaded.body.strategy, assignment)
        outcome = compile_strategy(definition, INITIAL_CATALOG, timeframes)
        assert isinstance(outcome, CompileSucceeded), outcome
        profiles, unmeasured, causes = complexity_profiles(outcome.compiled, INITIAL_CATALOG)
        measures = measure_complexity(profiles, unmeasured_outputs=unmeasured)
        result = check_complexity(measures, loaded.policy.limits, unmeasured_causes=causes)
        assert result.outcome is CheckOutcome.PASSED, result
    refs_v2 = _compiled_refs(loaded)
    assert len(set(refs_v2)) == 4
    assert set(refs_v2).isdisjoint(_compiled_refs(original))


def test_the_version_1_components_and_settings_are_kept(
    loaded: ExperimentV2, original: ExperimentV2
) -> None:
    """版 1 の 7 部品（取引機会と出口）は宣言のまま残り、入場・束縛・同時の保持・他の役割も同じ。"""
    v2 = loaded.body.strategy
    v1 = original.body.strategy
    v1_components = {c.instance_id: c for c in v1.components}
    v2_components = {c.instance_id: c for c in v2.components}
    assert set(v1_components) == V1_INSTANCES
    assert set(v2_components) == V1_INSTANCES | set(DAILY_VERSIONS)
    for instance_id in V1_INSTANCES:
        assert v2_components[instance_id] == v1_components[instance_id]
    assert (v2.trigger, v2.order, v2.protection, v2.exit, v2.execution_filter) == (
        v1.trigger,
        v1.order,
        v1.protection,
        v1.exit,
        v1.execution_filter,
    )
    assert isinstance(v2.entry_policy, ImmediateEntry)
    assert v2.entry_policy == v1.entry_policy
    assert v2.opportunity_validity == v1.opportunity_validity
    assert v2.opportunity_validity.bindings == ()
    assert v2.opportunity_concurrency == v1.opportunity_concurrency
    assert v1.market_state is None


def test_the_daily_regime_is_the_four_stages_of_the_analysis_memo(loaded: ExperimentV2) -> None:
    """足した日足の局面は分析メモ「3. 変更の候補」の 1 位の項のとおり（H2 の 4 段を EURUSD に
    写した）。

    日足の EMA は期間 20・窓 60（日足 60 本を超えない）、比較は日足の終値 > 日足の EMA、売りは常に
    許さない。4 段とも EURUSD の日足（`1d_ny17`）の確定で評価し、役割 `market_state` は許可の出力。
    """
    strategy = loaded.body.strategy
    components = {c.instance_id: c for c in strategy.components}
    versions = {
        instance_id: (
            components[instance_id].contract_ref.component_id,
            components[instance_id].contract_ref.version,
        )
        for instance_id in DAILY_VERSIONS
    }
    assert versions == DAILY_VERSIONS
    parameters = {instance_id: dict(components[instance_id].parameters) for instance_id in versions}
    assert parameters == {
        "daily_ema": {"period": IntValue(20), "window_bars": IntValue(60)},
        "daily_above_ema": {"operator": StrValue("GT")},
        "no_short": {"value": BoolValue(False)},
        "market_state": {},
    }
    sources = {
        instance_id: {
            name: binding.sources for name, binding in components[instance_id].inputs.items()
        }
        for instance_id in versions
    }
    daily_close = f"{DAILY_SERIES}:CLOSE"
    market_inputs = sorted(
        (instance_id, name, str(source))
        for instance_id, inputs in sources.items()
        for name, refs in inputs.items()
        for source in refs
        if isinstance(source, MarketDataRef)
    )
    assert market_inputs == [
        ("daily_above_ema", "left", daily_close),
        ("daily_ema", "prices", daily_close),
    ]
    assert sources["daily_above_ema"]["right"] == (OutputRef("daily_ema", "value"),)
    assert sources["no_short"] == {}
    assert sources["market_state"] == {
        "long_allowed": (OutputRef("daily_above_ema", "condition"),),
        "short_allowed": (OutputRef("no_short", "condition"),),
    }
    for instance_id in versions:
        triggers = components[instance_id].evaluation.triggers
        assert [str(t.series) for t in triggers if isinstance(t, OnBarClose)] == [DAILY_SERIES]
        assert len(triggers) == 1
    assert strategy.market_state == OutputRef("market_state", "permission")


def _section_3() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("\n## 3. ")
    end = text.find("\n## 4. ", start)
    return text[start:] if end < 0 else text[start:end]


def test_section_3_carries_the_memo_hash_and_no_verdict_values() -> None:
    """第 3 節に分析メモの SHA-256（決定 127 の 3 行目）があり、判定の値の識別子と表示の語が
    無い。"""
    section = _section_3()
    assert f"> 3. メモの SHA-256: {ANALYSIS_MEMO_SHA256}" in section
    identifiers = {
        member.value
        for enum in (SearchVerdict, ProfitabilityAxis, EvidenceAxis, FoldVerdict)
        for member in enum
    }
    for identifier in identifiers:
        assert re.search(rf"(?<![A-Z_]){identifier}(?![A-Z_])", section) is None, identifier
    labels = {
        *_VERDICT_NAMES.values(),
        *_FOLD_VERDICT_NAMES.values(),
        *_PROFITABILITY_NAMES.values(),
        *_EVIDENCE_NAMES.values(),
    }
    for label in labels:
        assert label not in section, label
