"""前向き検証の規則の値と固定の記録の書式の型（D09 §9.11.12。順 6 の F2）。

すべて人工の値（ダイジェストは文字列から作る）。成績・価格・封印のデータを読まない。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import CompiledStrategyRef
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.forward_validation import (
    FORWARD_VALIDATION_ID,
    AcquisitionStartRef,
    CandidateFix,
    CandidateFixId,
    DeferralExclusion,
    FinalWindowEnd,
    FinalWindowRule,
    FixCandidate,
    FixDeferralRule,
    FixedTrialPin,
    FixWeekTreatment,
    ForwardRulesEntry,
    FrequencyMaterialRef,
    FrequencySource,
    LengthMethod,
    LengthRule,
    MinTradesSource,
    PairCombination,
    UsableWindow,
    VersionedRef,
)
from odyssey_fx.evaluation.domain.search import LineageSource
from odyssey_fx.evaluation.domain.variant import Paired

_EXPERIMENT = ExperimentId(digest("experiment"))
_OTHER = ExperimentId(digest("other experiment"))


def _pin(name: str) -> FixedTrialPin:
    return FixedTrialPin(
        trial_index=3,
        assignment_digest=digest(f"{name} assignment"),
        compiled_ref=CompiledStrategyRef(digest(f"{name} compiled")),
    )


def _fix(**changes: Any) -> CandidateFix:
    fix = CandidateFix(
        candidate=FixCandidate(experiment_id=_EXPERIMENT, execution=1),
        fix_no=1,
        fixed_trials=Paired(standard=_pin("standard"), candidate=_pin("candidate")),
        record_sheet_digest=digest("record sheet"),
        research_policy=VersionedRef(ref_id="research_policy", version=4, digest=digest("p4")),
        forward_rules=VersionedRef(ref_id=FORWARD_VALIDATION_ID, version=1, digest=digest("f1")),
        adoption_review_row_digest=digest("proceed row"),
        length_days=400,
        frequency_source=FrequencyMaterialRef(material_id="review-run", digest=digest("material")),
        derived_from=None,
        supersedes=None,
        fixed_by="人間",
    )
    return replace(fix, **changes) if changes else fix


def test_a_first_fix_record_needs_no_supersedes() -> None:
    fix = _fix()
    assert fix.fix_id == CandidateFixId(experiment_id=_EXPERIMENT, execution=1, fix_no=1)


def test_a_replacement_points_to_the_previous_number_of_the_same_execution() -> None:
    previous = CandidateFixId(experiment_id=_EXPERIMENT, execution=1, fix_no=1)
    fix = _fix(fix_no=2, supersedes=previous)
    assert fix.supersedes == previous


def test_another_version_may_supersede_a_record_of_another_execution() -> None:
    """別の版（別の実行）の固定は、元の版の識別子を指す（D09 §9.11.9 の 2）。"""
    original = CandidateFixId(experiment_id=_EXPERIMENT, execution=1, fix_no=2)
    fix = _fix(
        candidate=FixCandidate(experiment_id=_EXPERIMENT, execution=2),
        supersedes=original,
        derived_from=LineageSource(experiment_id=_EXPERIMENT, execution=1),
    )
    assert fix.supersedes == original
    other_experiment = _fix(
        candidate=FixCandidate(experiment_id=_OTHER, execution=1), supersedes=original
    )
    assert other_experiment.supersedes == original


@pytest.mark.parametrize(
    "changes",
    [
        # 2 以上の番号は、同じ実行の 1 つ前の番号を置き換えるときだけ
        {"fix_no": 2},
        {"fix_no": 3, "supersedes": CandidateFixId(_EXPERIMENT, 1, 1)},
        {"fix_no": 2, "supersedes": CandidateFixId(_EXPERIMENT, 2, 1)},
        # 同じ実行を指すなら fix_no − 1
        {"supersedes": CandidateFixId(_EXPERIMENT, 1, 1)},
        {"fix_no": 0},
        {"length_days": 0},
        {"length_days": True},
        {"fixed_by": " "},
        {"forward_rules": VersionedRef(ref_id="adoption_review", version=1, digest=digest("x"))},
        {"record_sheet_digest": "abc"},
        {"fixed_trials": Paired(standard=_pin("s"), candidate="not a pin")},
        {"derived_from": CandidateFixId(_EXPERIMENT, 1, 1)},
    ],
)
def test_malformed_fix_records_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(KernelValueError):
        _fix(**changes)


@pytest.mark.parametrize(
    "build",
    [
        lambda: FixCandidate(experiment_id=_EXPERIMENT, execution=0),
        lambda: CandidateFixId(experiment_id=_EXPERIMENT, execution=1, fix_no=0),
        lambda: FixedTrialPin(
            trial_index=-1,
            assignment_digest=digest("a"),
            compiled_ref=CompiledStrategyRef(digest("c")),
        ),
        lambda: FrequencyMaterialRef(material_id="", digest=digest("m")),
        lambda: VersionedRef(ref_id="", version=1, digest=digest("v")),
        lambda: VersionedRef(ref_id="research_policy", version=0, digest=digest("v")),
    ],
)
def test_malformed_fix_record_parts_are_rejected(build: object) -> None:
    assert callable(build)
    with pytest.raises(KernelValueError):
        build()


def test_registry_entries_belong_to_the_forward_validation_rules() -> None:
    entry = ForwardRulesEntry(
        rules_id=FORWARD_VALIDATION_ID, version=1, digest=digest("rules"), effective_after=None
    )
    assert entry.ref == VersionedRef(
        ref_id=FORWARD_VALIDATION_ID, version=1, digest=digest("rules")
    )
    with pytest.raises(KernelValueError):
        replace(entry, rules_id="adoption_review")
    with pytest.raises(KernelValueError):
        replace(entry, version=0)


def _length(**changes: Any) -> LengthRule:
    rule = LengthRule(
        method=LengthMethod.POISSON,
        probability=Decimal("0.95"),
        min_days=364,
        min_trades_source=MinTradesSource.RESEARCH_POLICY_MIN_VALIDATION_TRADES_TOTAL,
        frequency_source=FrequencySource.ADOPTION_REVIEW_FIXED_TRIAL_VALIDATION_FOLDS,
        pair_combination=PairCombination.MAX,
        precision_digits=50,
    )
    return replace(rule, **changes) if changes else rule


@pytest.mark.parametrize(
    "changes",
    [
        {"probability": Decimal("NaN")},
        {"probability": 0.95},
        {"probability": Decimal("1.0")},
        {"min_days": 0},
        {"precision_digits": 0},
        {"method": "POISSON"},
    ],
)
def test_length_rules_outside_the_range_are_rejected(changes: dict[str, object]) -> None:
    assert _length().min_days == 364
    with pytest.raises(KernelValueError):
        _length(**changes)


def test_set_valued_rules_must_be_the_decided_set() -> None:
    both = (
        DeferralExclusion.OTHER_CANDIDATES_RESERVED_FORWARD_PERIODS,
        DeferralExclusion.EARLIER_CANDIDATES_PLANNED_FINAL_WINDOWS,
    )
    assert FixDeferralRule(usable_window=UsableWindow.LATEST_CONTIGUOUS, exclude=both)
    with pytest.raises(KernelValueError):
        FixDeferralRule(usable_window=UsableWindow.LATEST_CONTIGUOUS, exclude=both[:1])
    with pytest.raises(KernelValueError):
        FixDeferralRule(usable_window=UsableWindow.LATEST_CONTIGUOUS, exclude=list(both))  # type: ignore[arg-type]


def test_the_final_window_starts_at_the_end_of_the_research_history() -> None:
    rule = FinalWindowRule(
        start=UtcTime.parse("2024-01-01T00:00:00Z"),
        end=FinalWindowEnd.FIX_TIME,
        fix_week=FixWeekTreatment.EXCLUDED,
    )
    with pytest.raises(KernelValueError):
        replace(rule, start=UtcTime.parse("2024-01-08T00:00:00Z"))


@pytest.mark.parametrize("path", ["/abs/file.yaml", "a/../b.yaml", "a//b.yaml", "./a.yaml", ""])
def test_acquisition_start_refs_stay_inside_the_repository(path: str) -> None:
    with pytest.raises(KernelValueError):
        AcquisitionStartRef(path=path, key="acquisition_start")
