"""候補の固定の記録の書き出しと読込（D09 §9.11.12 の 2。順 11 の PR 4c）。

人工の値だけを使う（ダイジェストは 16 進の並び。成績・価格を持たない）。
"""

from __future__ import annotations

import pytest

from odyssey_fx.app.config import ConfigError
from odyssey_fx.app.config.candidate_fix import (
    candidate_fix_relative_path,
    candidate_fix_text,
    load_candidate_fix,
    parse_candidate_fix_path,
)
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import CompiledStrategyRef, ContentDigest
from odyssey_fx.evaluation.domain.forward_validation import (
    CandidateFix,
    CandidateFixId,
    FixCandidate,
    FixedTrialPin,
    FrequencyMaterialRef,
    VersionedRef,
)
from odyssey_fx.evaluation.domain.search import LineageSource
from odyssey_fx.evaluation.domain.variant import Paired

_EXPERIMENT = ExperimentId(ContentDigest.sha256("1" * 64))


def _d(char: str) -> ContentDigest:
    return ContentDigest.sha256(char * 64)


def _record(*, fix_no: int = 1, supersedes: CandidateFixId | None = None) -> CandidateFix:
    return CandidateFix(
        candidate=FixCandidate(experiment_id=_EXPERIMENT, execution=2),
        fix_no=fix_no,
        fixed_trials=Paired(
            standard=FixedTrialPin(
                trial_index=0, assignment_digest=_d("a"), compiled_ref=CompiledStrategyRef(_d("b"))
            ),
            candidate=FixedTrialPin(
                trial_index=3, assignment_digest=_d("c"), compiled_ref=CompiledStrategyRef(_d("d"))
            ),
        ),
        record_sheet_digest=_d("e"),
        research_policy=VersionedRef(ref_id="research_policy", version=5, digest=_d("f")),
        forward_rules=VersionedRef(ref_id="forward_validation", version=1, digest=_d("0")),
        adoption_review_row_digest=_d("2"),
        length_days=400,
        frequency_source=FrequencyMaterialRef(
            material_id="review/" + "9" * 32 + "/review_record.json", digest=_d("3")
        ),
        derived_from=LineageSource(experiment_id=ExperimentId(_d("4")), execution=1),
        supersedes=supersedes,
        fixed_by='人工の記録者 "引用"',
    )


def _rel(fix_no: int = 1) -> str:
    return candidate_fix_relative_path(_EXPERIMENT, 2, fix_no)


def test_the_text_reads_back_to_the_same_record_and_is_stable() -> None:
    record = _record()
    text = candidate_fix_text(record)
    assert load_candidate_fix(_rel(), text=text) == record
    assert candidate_fix_text(load_candidate_fix(_rel(), text=text)) == text
    assert _rel() == f"research/candidate_fixes/{'1' * 64}/2/1.yaml"
    # 固定時点・開始・期間の終わり・ウォームアップは書かない（D09 §9.11.3・§9.11.6）。
    keys = {line.split(":", 1)[0] for line in text.splitlines() if line and line[0].isalpha()}
    assert keys == {
        "schema_version",
        "candidate",
        "fix_no",
        "fixed_trials",
        "record_sheet_digest",
        "research_policy",
        "forward_rules",
        "adoption_review_row_digest",
        "length_days",
        "frequency_source",
        "derived_from",
        "supersedes",
        "fixed_by",
    }


def test_a_replacing_record_points_to_fix_no_minus_one() -> None:
    previous = CandidateFixId(experiment_id=_EXPERIMENT, execution=2, fix_no=1)
    record = _record(fix_no=2, supersedes=previous)
    assert load_candidate_fix(_rel(2), text=candidate_fix_text(record)) == record
    text = candidate_fix_text(_record()).replace("fix_no: 1", "fix_no: 2")
    with pytest.raises(ConfigError, match="supersedes"):
        load_candidate_fix(_rel(2), text=text)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("length_days: 400", "length_days: 0"),
        ("length_days: 400", 'length_days: 400\nt_fix: "2025-01-10T00:00:00Z"'),
        ('record_sheet_digest: "' + "e" * 64, 'record_sheet_digest: "' + "E" * 64),
        ("  id: forward_validation", "  id: adoption_review"),
        ("schema_version: 1", "schema_version: 2"),
        ("fixed_by: ", "fixed_by_name: "),
    ],
)
def test_structural_errors_stop_the_read(old: str, new: str) -> None:
    text = candidate_fix_text(_record())
    assert old in text
    with pytest.raises(ConfigError):
        load_candidate_fix(_rel(), text=text.replace(old, new, 1))


def test_the_path_must_match_the_contents() -> None:
    text = candidate_fix_text(_record())
    with pytest.raises(ConfigError, match="置き場のパス"):
        load_candidate_fix(candidate_fix_relative_path(_EXPERIMENT, 3, 1), text=text)
    with pytest.raises(ConfigError, match="置き場"):
        load_candidate_fix("research/fixations/x_2.yaml", text=text)
    assert parse_candidate_fix_path("research/candidate_fixes/abc/1/1.yaml") is None
    assert parse_candidate_fix_path(f"research/candidate_fixes/{'1' * 64}/0/1.yaml") is None
    assert parse_candidate_fix_path(_rel(7)) == CandidateFixId(
        experiment_id=_EXPERIMENT, execution=2, fix_no=7
    )
