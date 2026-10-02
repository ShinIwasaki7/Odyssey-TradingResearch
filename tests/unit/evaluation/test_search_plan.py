"""探索計画・試行の列挙・試行の事前固定・記録票の分割（D09 §3・§5.1・§5.2・§6.1・§6.2・§10.2）。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import CompiledStrategyRef, ConfigDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.research_policy import ComplexityMeasures, search_complexity
from odyssey_fx.evaluation.domain.search import (
    ParameterAssignment,
    ParameterAxis,
    SearchPlan,
    SearchPlanKind,
    TrialPhase,
    TrialPlan,
    TrialUnitKey,
    compile_rejections_of,
    enumerate_assignments,
    trial_units,
)
from odyssey_fx.evaluation.domain.splits import (
    FinalHoldoutSpec,
    SplitKind,
    SplitSpec,
    SplitStandard,
    SplitStandardViolation,
    SplitWindow,
    check_final_holdout,
    generate_folds,
    split_spec_of,
)
from odyssey_fx.strategy.compiler.compiled import (
    CompileError,
    CompileRejection,
    DeclarationLocation,
)
from odyssey_fx.strategy.declarations.specs import FloatValue, IntValue, StrValue

_START = UtcTime.parse("2015-01-04T22:00:00Z")


def _day(offset: int) -> UtcTime:
    return _START + timedelta(days=offset)


def _axis(instance: str, parameter: str, *values: int | float) -> ParameterAxis:
    return ParameterAxis(
        instance_id=instance,
        parameter=parameter,
        values=tuple(
            FloatValue(value) if isinstance(value, float) else IntValue(value) for value in values
        ),
    )


def _example_plan() -> SearchPlan:
    """D09 §5.1 の例（`breakout.lookback_bars` × `take_profit.rr`）。軸は逆順に書く。"""
    return SearchPlan(
        kind=SearchPlanKind.GRID,
        axes=(
            _axis("take_profit", "rr", 1.5, 2.0),
            _axis("breakout", "lookback_bars", 10, 20, 30),
        ),
        max_trials=12,
    )


# --- 探索計画（D09 §5.1・§5.5 の1〜3）---------------------------------------------


def test_the_axes_are_sorted_and_the_values_keep_their_order() -> None:
    """軸は `(instance, parameter)` の昇順に並び、値は書いた順のまま（D09 §5.2）。"""
    plan = _example_plan()
    assert [axis.key for axis in plan.axes] == [
        ("breakout", "lookback_bars"),
        ("take_profit", "rr"),
    ]
    assert plan.axes[0].values == (IntValue(10), IntValue(20), IntValue(30))
    assert plan.trial_count == 6


def test_the_trials_are_enumerated_with_the_last_axis_fastest() -> None:
    """後ろの軸ほど速く変わる直積の順で `trial_index` を振る（D09 §5.2 の例）。"""
    assignments = enumerate_assignments(_example_plan())
    pairs = [tuple(value for _, _, value in item.values) for item in assignments]
    assert pairs[:3] == [
        (IntValue(10), FloatValue(1.5)),
        (IntValue(10), FloatValue(2.0)),
        (IntValue(20), FloatValue(1.5)),
    ]
    assert len(pairs) == 6
    assert enumerate_assignments(_example_plan()) == assignments


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"kind": SearchPlanKind.GRID, "axes": (), "max_trials": 1}, "axes が空"),
        (
            {"kind": SearchPlanKind.NONE, "axes": (_axis("a", "p", 1),), "max_trials": 1},
            "axes がある",
        ),
        (
            {
                "kind": SearchPlanKind.GRID,
                "axes": (_axis("a", "p", 1), _axis("a", "p", 2)),
                "max_trials": 4,
            },
            "2つある",
        ),
        (
            {"kind": SearchPlanKind.GRID, "axes": (_axis("a", "p", 1, 2),), "max_trials": 1},
            "超える",
        ),
        ({"kind": SearchPlanKind.GRID, "axes": (_axis("a", "p", 1),), "max_trials": 0}, "正の整数"),
        ({"kind": SearchPlanKind.GRID, "axes": (_axis("a", "p", 1),), "max_trials": True}, "整数"),
    ],
    ids=["grid-without-axes", "none-with-axes", "duplicate-axis", "over", "zero", "bool"],
)
def test_invalid_search_plans_are_refused(kwargs: dict[str, object], expected: str) -> None:
    with pytest.raises(KernelValueError, match=expected):
        SearchPlan(**kwargs)  # type: ignore[arg-type]


def test_max_trials_may_exceed_the_trial_count() -> None:
    """`max_trials` は上限であり、試行の数がそれを下回るのは許す（D09 §5.5 の3）。"""
    plan = SearchPlan(kind=SearchPlanKind.GRID, axes=(_axis("a", "p", 1),), max_trials=100)
    assert plan.trial_count == 1


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), "空"),
        ((IntValue(1), IntValue(1)), "2回"),
        ((FloatValue(2), FloatValue(2.0)), "2回"),
        ((IntValue(1), StrValue("1")), "混在"),
    ],
    ids=["empty", "duplicate", "duplicate-float-spelling", "mixed"],
)
def test_invalid_axes_are_refused(values: tuple[object, ...], expected: str) -> None:
    with pytest.raises(KernelValueError, match=expected):
        ParameterAxis(instance_id="a", parameter="p", values=values)  # type: ignore[arg-type]


def test_an_assignment_is_sorted_and_rejects_a_repeated_parameter() -> None:
    assignment = ParameterAssignment(values=(("b", "p", IntValue(1)), ("a", "q", IntValue(2))))
    assert [item[:2] for item in assignment.values] == [("a", "q"), ("b", "p")]
    with pytest.raises(KernelValueError):
        ParameterAssignment(values=(("a", "p", IntValue(1)), ("a", "p", IntValue(2))))


# --- 単位と試行の事前固定（D09 §6.2・§10.2）----------------------------------------


def test_the_units_of_a_trial_cover_both_phases_of_every_fold() -> None:
    """コンパイルが通った試行は全 fold の選定区間と検証区間の単位を持つ（D09 §10.2・§10.5）。"""
    units = trial_units(2, 3)
    assert [(unit.fold_index, unit.phase) for unit in units] == [
        (0, TrialPhase.TRAIN),
        (0, TrialPhase.VALIDATION),
        (1, TrialPhase.TRAIN),
        (1, TrialPhase.VALIDATION),
    ]
    assert {unit.trial_index for unit in units} == {3}
    assert sorted(units, key=lambda unit: unit.order) == list(units)


def _config_digest(seed: str) -> ConfigDigest:
    return ConfigDigest(digest(seed))


def _assignment() -> ParameterAssignment:
    return ParameterAssignment(values=(("a", "p", IntValue(1)),))


def test_a_compiled_trial_plan_orders_its_unit_digests() -> None:
    units = trial_units(1, 0)
    plan = TrialPlan(
        trial_index=0,
        assignment=_assignment(),
        compiled_ref=CompiledStrategyRef(digest("compiled")),
        compile_rejections=(),
        expected_config_digests=tuple(
            (unit, _config_digest(str(unit))) for unit in reversed(units)
        ),
    )
    assert tuple(unit for unit, _ in plan.expected_config_digests) == units
    assert plan.compiled
    assert plan.expected_config_digest(units[1]) == _config_digest(str(units[1]))


@pytest.mark.parametrize(
    ("compiled", "rejections", "digests"),
    [
        (True, ("x",), True),
        (False, (), False),
        (False, ("x",), True),
        (True, (), False),
    ],
    ids=["both", "neither", "rejected-with-digests", "compiled-without-digests"],
)
def test_a_trial_either_compiles_or_is_rejected(
    compiled: bool, rejections: tuple[str, ...], digests: bool
) -> None:
    """コンパイル拒否の試行は `compiled_ref = None` で予測ダイジェストは空（D09 §10.2）。"""
    with pytest.raises(KernelValueError):
        TrialPlan(
            trial_index=0,
            assignment=_assignment(),
            compiled_ref=CompiledStrategyRef(digest("c")) if compiled else None,
            compile_rejections=rejections,
            expected_config_digests=(
                tuple((unit, _config_digest("d")) for unit in trial_units(1, 0)) if digests else ()
            ),
        )


def test_a_trial_plan_refuses_another_trials_unit() -> None:
    with pytest.raises(KernelValueError, match="another trial"):
        TrialPlan(
            trial_index=0,
            assignment=_assignment(),
            compiled_ref=CompiledStrategyRef(digest("c")),
            compile_rejections=(),
            expected_config_digests=(
                (
                    TrialUnitKey(fold_index=0, phase=TrialPhase.TRAIN, trial_index=1),
                    _config_digest("d"),
                ),
            ),
        )


def _error(check_id: str, instance: str | None, field: str, message: str) -> CompileError:
    return CompileError(
        check_id=check_id,
        rejection=CompileRejection.PARAMETER_INVALID,
        location=DeclarationLocation(instance_id=instance, field_path=field),
        message=message,
    )


def test_compile_rejections_are_encoded_without_the_message_and_sorted() -> None:
    """拒否1件は `check_id`・区分・`location` の正規化エンコードで、`(location, check_id)` の昇順。

    文言は入れない（D09 §3 の `TrialPlan.compile_rejections`）。
    """
    errors = (
        _error("C12", "m15_ema", "parameters", "window too short"),
        _error("C07", "daily_ema", "parameters.period", "out of range"),
        _error("C03", "daily_ema", "parameters.period", "another"),
    )
    encoded = compile_rejections_of(errors)
    assert len(encoded) == 3
    assert ["C03" in encoded[0], "C07" in encoded[1], "C12" in encoded[2]] == [True, True, True]
    assert all("range" not in item and "short" not in item for item in encoded)
    renamed = (_error("C12", "m15_ema", "parameters", "other words"),)
    assert compile_rejections_of(renamed)[0] == encoded[2]


# --- 複雑性（D09 §10.2）------------------------------------------------------------


def test_the_search_complexity_is_the_maximum_over_compiled_trials() -> None:
    small = ComplexityMeasures(component_kinds=3, instances=4, parameters=5, decision_outputs=1)
    large = ComplexityMeasures(component_kinds=3, instances=6, parameters=7, decision_outputs=1)
    assert search_complexity((small, large)) == large


def test_the_search_complexity_is_unmeasured_without_a_compiled_trial() -> None:
    """コンパイルが通った試行が無ければ4件とも `None`（P6 は UNREADABLE。D09 §10.2）。"""
    assert search_complexity(()) == ComplexityMeasures(None, None, None, None)
    partial = ComplexityMeasures(
        component_kinds=3, instances=4, parameters=5, decision_outputs=None
    )
    full = ComplexityMeasures(component_kinds=3, instances=4, parameters=5, decision_outputs=1)
    assert search_complexity((partial, full)).decision_outputs is None


# --- 記録票の分割と最終検証（D09 §6.1 の検査4・§10.2）---------------------------------


def _standard(purge_seconds: int = 0) -> SplitStandard:
    return SplitStandard(
        range=Interval(start=_day(0), end=_day(12)),
        train_seconds=4 * 86400,
        validation_seconds=4 * 86400,
        window=SplitWindow.ROLLING,
        purge_seconds=purge_seconds,
        min_folds=2,
    )


def test_the_split_spec_expands_the_generated_folds() -> None:
    """記録票の `SplitSpec` は標準規則から生成した fold と purge（D09 §6.1・§10.2）。"""
    spec = split_spec_of(_standard())
    assert spec.kind is SplitKind.STANDARD
    assert spec.folds == generate_folds(_standard())
    assert spec.purge_seconds == 0
    assert split_spec_of(_standard()) == spec


def test_a_split_spec_checks_its_folds() -> None:
    with pytest.raises(KernelValueError):
        SplitSpec(kind=SplitKind.STANDARD, folds=(), purge_seconds=0)
    with pytest.raises(KernelValueError):
        SplitSpec(kind=SplitKind.NONE, folds=generate_folds(_standard()), purge_seconds=0)
    folds = generate_folds(_standard())
    with pytest.raises(KernelValueError, match="検査2"):
        SplitSpec(kind=SplitKind.STANDARD, folds=folds, purge_seconds=60)


@pytest.mark.parametrize(
    ("start_day", "purge_seconds", "accepted"),
    [(12, 0, True), (11, 0, False), (12, 3600, False), (13, 3600, True)],
)
def test_the_final_holdout_starts_after_the_range_and_the_purge(
    start_day: int, purge_seconds: int, accepted: bool
) -> None:
    """検査4: 区間の開始が評価範囲の終わり＋purge 以上（D09 §6.1）。"""
    holdout = FinalHoldoutSpec(
        interval=Interval(start=_day(start_day), end=_day(start_day + 2)), purpose="確かめ"
    )
    standard = _standard(purge_seconds)
    if accepted:
        check_final_holdout(holdout, standard)
    else:
        with pytest.raises(SplitStandardViolation, match="検査4"):
            check_final_holdout(holdout, standard)


def test_a_final_holdout_needs_a_purpose() -> None:
    with pytest.raises(KernelValueError, match="検査4"):
        FinalHoldoutSpec(interval=Interval(start=_day(12), end=_day(13)), purpose=" ")
