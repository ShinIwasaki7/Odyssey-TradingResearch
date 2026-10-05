"""探索の実験の記録票の項目と識別の入力（D09 §10.2、D07 §19.2 v2.9）。

単一実行の実験の識別の入力は段階4 のまま変わらないこと（足した項目をキーごと省く）を、段階4
の入力の組み立てを写した式と比べて確かめる（段階4 で保存した実験を同じ版で再実行しても
検査 P3 に当たらない）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import CompiledStrategyRef, ConfigDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemExperimentStore,
    experiment_manifest_from_payload,
    experiment_manifest_payload,
)
from odyssey_fx.evaluation.domain.errors import ArtifactAlreadyExists
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentManifest,
    experiment_id_of,
    identity_roles,
)
from odyssey_fx.evaluation.domain.metrics import MetricId
from odyssey_fx.evaluation.domain.research_policy import (
    PolicyCheck,
    check_trial_count,
)
from odyssey_fx.evaluation.domain.search import (
    CandidateStatus,
    Comparator,
    EvaluationStandard,
    FoldSelection,
    FrequencyClass,
    MetricCondition,
    ParameterAxis,
    SearchPlan,
    SearchPlanKind,
    SelectionDirection,
    SelectionRule,
    StandardPurpose,
    SufficiencyRule,
    TrialPhase,
    TrialPlan,
    TrialUnitKey,
    ValidationRule,
    compile_rejections_of,
    enumerate_assignments,
    trial_units,
)
from odyssey_fx.evaluation.domain.splits import (
    FinalHoldoutSpec,
    SplitStandard,
    SplitWindow,
    split_spec_of,
)
from odyssey_fx.strategy.compiler.compiled import (
    CompileError,
    CompileRejection,
    DeclarationLocation,
)
from odyssey_fx.strategy.declarations.specs import IntValue
from tests.fixtures.evaluation.experiments import EXPERIMENT_VALUES, make_manifest

_START = UtcTime.parse("2015-01-04T22:00:00Z")


def _day(offset: int) -> UtcTime:
    return _START + timedelta(days=offset)


def _standard(train_days: int = 4) -> EvaluationStandard:
    return EvaluationStandard(
        purpose=StandardPurpose.MECHANISM_CHECK,
        split=SplitStandard(
            range=Interval(start=_day(0), end=_day(12)),
            train_seconds=train_days * 86400,
            validation_seconds=4 * 86400,
            window=SplitWindow.ROLLING,
            purge_seconds=0,
            min_folds=2,
        ),
        selection=SelectionRule(
            metric=MetricId.NET_RETURN_RATE, direction=SelectionDirection.MAXIMIZE, eligibility=()
        ),
        validation=ValidationRule(
            fold_floors=(
                MetricCondition(
                    metric=MetricId.MAX_DRAWDOWN_MTM_RATE,
                    comparator=Comparator.LE,
                    threshold=Decimal("0.2"),
                ),
            ),
            aggregate=(),
        ),
        sufficiency=SufficiencyRule(
            classes=(
                FrequencyClass(
                    name="LOW",
                    min_train_trades_per_365d=Decimal(0),
                    min_validation_trades_per_fold=1,
                    min_validation_trades_total=1,
                ),
            )
        ),
    )


_PLAN = SearchPlan(
    kind=SearchPlanKind.GRID,
    axes=(
        ParameterAxis(instance_id="ema", parameter="period", values=(IntValue(10), IntValue(40))),
    ),
    max_trials=2,
)

#: 探索の実験設定の値（YAML を読み込んだ形）。
_SEARCH_VALUES: Mapping[str, Any] = {
    **EXPERIMENT_VALUES,
    "search_plan": {
        "kind": "GRID",
        "max_trials": 2,
        "axes": [{"instance": "ema", "parameter": "period", "values": [10, 40]}],
    },
    "split": "STANDARD",
    "final_holdout": "NONE",
}


def _trials(plan: SearchPlan = _PLAN, folds: int = 2) -> tuple[TrialPlan, ...]:
    """試行 0 はコンパイルが通り、試行 1 はコンパイル拒否（`FAILED`）。"""
    assignments = enumerate_assignments(plan)
    rejection = CompileError(
        check_id="C12",
        rejection=CompileRejection.PARAMETER_INVALID,
        location=DeclarationLocation(instance_id="ema", field_path="parameters"),
        message="window_bars >= 2 * period",
    )
    return (
        TrialPlan(
            trial_index=0,
            assignment=assignments[0],
            compiled_ref=CompiledStrategyRef(digest("compiled 0")),
            compile_rejections=(),
            expected_config_digests=tuple(
                (unit, ConfigDigest(digest(f"config {unit}"))) for unit in trial_units(folds, 0)
            ),
        ),
        TrialPlan(
            trial_index=1,
            assignment=assignments[1],
            compiled_ref=None,
            compile_rejections=compile_rejections_of((rejection,)),
            expected_config_digests=(),
        ),
    )


def _search_manifest(
    values: Mapping[str, Any] = _SEARCH_VALUES, **overrides: Any
) -> ExperimentManifest:
    single = make_manifest()
    fields: dict[str, Any] = {
        "search_plan": _PLAN,
        "split": split_spec_of(_standard().split),
        "compiled_ref": None,
        "expected_config_digest": None,
        "evaluation_standard": _standard(),
        "final_holdout": None,
        "trials": _trials(),
        "pre_run_checks": (*single.pre_run_checks, check_trial_count(2, 100)),
    }
    fields.update(overrides)
    draft = replace(single, **fields)
    return replace(draft, experiment_id=experiment_id_of(draft, values))


# --- 単一実行の実験の識別は段階4 のまま（D09 §10.2 の「キーごと省く」）---------------


def _stage4_identity(manifest: ExperimentManifest, values: Mapping[str, Any]) -> ExperimentId:
    """段階4 の識別の入力（D07 §19.2 の1〜4）をそのまま写した式。"""
    payload = {
        "experiment_name": manifest.experiment_name,
        "experiment_version": manifest.experiment_version,
        "schema_version": manifest.schema_version,
        "experiment_values": {
            key: value for key, value in values.items() if key not in ("strategy", "environment")
        },
        "referenced_files": [list(pair) for pair in identity_roles(manifest.resolved_files)],
        "hypothesis": manifest.hypothesis,
        "research_policy_ref": manifest.research_policy_ref,
        "metric_set_version": manifest.metric_set_version,
        "search_plan": manifest.search_plan,
        "split": manifest.split,
        "strategy_ref": manifest.strategy_ref,
        "compiled_ref": manifest.compiled_ref,
        "expected_config_digest": manifest.expected_config_digest,
        "snapshot_id": manifest.snapshot_id,
        "allowed_partitions": {
            partition: access.value for partition, access in manifest.allowed_partitions.items()
        },
        "complexity": manifest.complexity,
        "complexity_limits": manifest.complexity_limits,
        "pre_run_checks": list(manifest.pre_run_checks),
    }
    return ExperimentId(digest(payload))


def test_the_single_run_identity_is_unchanged_from_stage_4() -> None:
    """単一実行の実験では足した項目を識別の入力に入れない（キーごと省く。D09 §10.2）。"""
    manifest = make_manifest()
    assert not manifest.is_search
    assert manifest.experiment_id == _stage4_identity(manifest, EXPERIMENT_VALUES)


def test_the_single_run_manifest_payload_has_no_search_keys() -> None:
    """単一実行の記録票の保存の形も段階4 のまま（探索の項目のキーを書かない）。"""
    payload = experiment_manifest_payload(make_manifest())
    assert payload["search_plan"] == "NONE" and payload["split"] == "NONE"
    assert not {"evaluation_standard", "final_holdout", "trials"} & set(payload)


# --- 探索の実験の記録票（D09 §10.2）----------------------------------------------------


def test_a_search_manifest_carries_the_resolved_search_items() -> None:
    manifest = _search_manifest()
    assert manifest.is_search
    assert manifest.compiled_ref is None and manifest.expected_config_digest is None
    assert [check.check for check in manifest.pre_run_checks][-1] is (
        PolicyCheck.TRIAL_COUNT_WITHIN_LIMIT
    )
    assert manifest.trials[1].compile_rejections and not manifest.trials[1].compiled


def test_the_written_search_keys_do_not_enter_the_identity() -> None:
    """解決前の `search_plan` / `split` / `final_holdout` は解決済みの形だけで識別する。"""
    respelled = dict(_SEARCH_VALUES)
    respelled["search_plan"] = {
        "kind": "GRID",
        "max_trials": 2,
        "axes": [{"instance": "ema", "parameter": "period", "values": [10, 40]}],
        "note": "書き方の揺れ",
    }
    respelled["final_holdout"] = "none-spelled-differently"
    assert _search_manifest(respelled).experiment_id == _search_manifest().experiment_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"final_holdout": FinalHoldoutSpec(Interval(start=_day(12), end=_day(14)), "確かめ")},
        {
            "evaluation_standard": _standard(train_days=3),
            "split": split_spec_of(_standard(train_days=3).split),
        },
        {
            "trials": (
                _trials()[0],
                replace(_trials()[1], compile_rejections=("other",)),
            )
        },
    ],
    ids=["final-holdout", "standard", "trials"],
)
def test_the_resolved_search_items_enter_the_identity(overrides: dict[str, Any]) -> None:
    assert _search_manifest(**overrides).experiment_id != _search_manifest().experiment_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"compiled_ref": CompiledStrategyRef(digest("single"))},
        {"pre_run_checks": make_manifest().pre_run_checks},
        {"split": split_spec_of(_standard(train_days=3).split)},
        {"trials": _trials()[:1]},
        {"trials": _trials(folds=1)},
        {"evaluation_standard": None},
    ],
    ids=[
        "single-compiled-ref",
        "without-p7",
        "split-not-from-standard",
        "missing-trial",
        "units-of-another-split",
        "no-standard",
    ],
)
def test_an_inconsistent_search_manifest_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(KernelValueError):
        _search_manifest(**overrides)


def test_a_single_run_manifest_refuses_search_items() -> None:
    with pytest.raises(KernelValueError):
        replace(make_manifest(), trials=_trials())
    with pytest.raises(KernelValueError):
        replace(make_manifest(), search_plan="GRID")


def _holdout() -> FinalHoldoutSpec:
    return FinalHoldoutSpec(
        interval=Interval(
            start=UtcTime.parse("2015-02-01T00:00:00Z"),
            end=UtcTime.parse("2015-03-01T00:00:00Z"),
        ),
        purpose="最終検証の試験",
    )


@pytest.mark.parametrize("final_holdout", [None, "holdout"])
def test_a_search_manifest_is_saved_and_read_back_unchanged(final_holdout: str | None) -> None:
    """探索の記録票の保存と再読込（D09 §17.7.2 の9。実装 PR 4 の完了条件）。

    探索の項目（探索計画・分割・評価基準・最終検証・全試行）を落とさず保存し、読み戻した記録票は
    元と等しく、識別子を再計算しても同じになる（保存時の検査 P3 が同じ版の再実行を通す）。
    """
    manifest = (
        _search_manifest() if final_holdout is None else _search_manifest(final_holdout=_holdout())
    )
    payload = json.loads(json.dumps(experiment_manifest_payload(manifest)))
    assert {"evaluation_standard", "final_holdout", "trials"} <= set(payload)
    restored = experiment_manifest_from_payload(payload)
    assert restored == manifest
    assert experiment_id_of(restored, _SEARCH_VALUES) == manifest.experiment_id


def test_a_search_manifest_with_a_respelled_value_is_not_read_back() -> None:
    """保存した正規化形と違う表し方の値（例: 十進数の別の書き方）は読み戻さない（識別子が
    再計算で同じ値になることを保証するため）。"""
    payload = json.loads(json.dumps(experiment_manifest_payload(_search_manifest())))
    floors = payload["evaluation_standard"]["validation"]["fold_floors"]
    floors[0]["threshold"] = "0.20"
    with pytest.raises(KernelValueError, match="canonical form"):
        experiment_manifest_from_payload(payload)


def test_the_aggregate_tables_keep_their_columns_and_types_with_no_rows(tmp_path: Path) -> None:
    """集約表は 0 行でも列とその型を残して両方を書く（D09 §11.2、AGENTS.md）。

    全試行がコンパイル拒否の実験（選定区間の単位は失敗の行だけ、指標の行は 0 件）で確かめる。
    """
    trials = _trials()
    failed = replace(
        trials[0],
        compiled_ref=None,
        compile_rejections=trials[1].compile_rejections,
        expected_config_digests=(),
    )
    manifest = _search_manifest(trials=(failed, trials[1]))
    selections = tuple(
        FoldSelection(
            fold_index=fold,
            selected_trial_index=None,
            selected_value=None,
            selected_train_trade_count=None,
            inputs=(
                (0, None, CandidateStatus.EXCLUDED_TRIAL_FAILED),
                (1, None, CandidateStatus.EXCLUDED_TRIAL_FAILED),
            ),
        )
        for fold in (0, 1)
    )
    store = FileSystemExperimentStore(
        root=tmp_path,
        experiment_name=manifest.experiment_name,
        experiment_version=manifest.experiment_version,
        identity_of=lambda item: item.experiment_id,
    )
    store.write_aggregate_tables(manifest, selections, (), {})
    search = store.directory / "search"
    units = pl.read_parquet(search / "trial_units.parquet")
    assert units.height == 4
    assert set(units.get_column("status").to_list()) == {"FAILED"}
    assert units.schema["fold_index"] == pl.Int64()
    assert units.schema["selected"] == pl.Boolean()
    assert units.get_column("run_id").null_count() == 4
    metrics = pl.read_parquet(search / "trial_metrics.parquet")
    assert metrics.height == 0
    assert metrics.columns[:4] == ["fold_index", "phase", "trial_index", "metric_id"]
    assert metrics.schema["fold_index"] == pl.Int64()
    # 指標は試行記録の単位とちょうど同じ単位について渡す（D09 §17.7.4 の4）。
    stray = TrialUnitKey(fold_index=0, phase=TrialPhase.TRAIN, trial_index=0)
    with pytest.raises(KernelValueError, match="cover exactly the units"):
        store.write_aggregate_tables(manifest, selections, (), {stray: ()})
    with pytest.raises(ArtifactAlreadyExists):
        store.write_aggregate_tables(manifest, selections, (), {})
