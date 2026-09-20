"""`ProcessingPoint` と `PhaseSet` のプロパティテスト（D02 §3.3・§11）。

確かめること:

- `ProcessingPoint` が全順序であること。全順序とは、任意の2点が比較でき（完全性）、順序が
  推移的で、反対称で、`(time, phase.rank, sequence)` の辞書式順序と一致することをいう。
- `PhaseSet` が入力の並び順に依らないこと（D02 §3.3 v1.2 の正規化）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from odyssey_fx.common import canonical
from odyssey_fx.common.time import PhaseRank, PhaseSet, ProcessingPoint, UtcTime

_moments = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
).map(lambda naive: UtcTime(naive.replace(tzinfo=UTC)))

_phase_ranks = st.integers(min_value=0, max_value=5)

_points = st.builds(
    ProcessingPoint,
    time=_moments,
    phase=_phase_ranks.map(lambda rank: PhaseRank(rank, f"PHASE_{'A' * (rank + 1)}")),
    sequence=st.integers(min_value=0, max_value=50),
)


@given(_points, _points)
def test_comparison_is_total(left: ProcessingPoint, right: ProcessingPoint) -> None:
    """任意の2点は「より小さい / 等しい / より大きい」のちょうど1つで関係づく。"""
    relations = [left < right, left == right, left > right]
    assert sum(relations) == 1


@given(_points, _points)
def test_comparison_is_antisymmetric(left: ProcessingPoint, right: ProcessingPoint) -> None:
    if left <= right and right <= left:
        assert left == right


@given(_points, _points, _points)
def test_comparison_is_transitive(
    first: ProcessingPoint, second: ProcessingPoint, third: ProcessingPoint
) -> None:
    if first <= second and second <= third:
        assert first <= third


@given(_points, _points)
def test_order_matches_the_documented_sort_key(
    left: ProcessingPoint, right: ProcessingPoint
) -> None:
    """順序は `(time, phase.rank, sequence)` の辞書式順序に一致する（D02 §3.3）。"""
    assert (left < right) == (left.sort_key < right.sort_key)


@given(st.lists(_points, max_size=20))
def test_sorting_is_consistent_with_the_sort_key(points: list[ProcessingPoint]) -> None:
    assert sorted(points) == sorted(points, key=lambda point: point.sort_key)


@given(_points, _points)
def test_equality_implies_equal_hashes(left: ProcessingPoint, right: ProcessingPoint) -> None:
    if left == right:
        assert hash(left) == hash(right)


@given(_moments, _phase_ranks, st.integers(min_value=0, max_value=50))
def test_the_same_time_is_still_split_by_phase_and_sequence(
    moment: UtcTime, rank: int, sequence: int
) -> None:
    """同じ `time` でも phase と sequence で区別される（D02 §3.3）。"""
    phase = PhaseRank(rank, "PHASE_A")
    later_phase = PhaseRank(rank + 1, "PHASE_B")
    assert ProcessingPoint(moment, phase, sequence) < ProcessingPoint(moment, later_phase, 0)
    assert ProcessingPoint(moment, phase, sequence) < ProcessingPoint(moment, phase, sequence + 1)


# --- PhaseSet の正規化（D02 §3.3 v1.2）--------------------------------------
#
# どんな並び順で書いても同じ集合として扱われることを、順列全体にわたって確かめる。

#: 一意な `rank` と `name` を持つフェーズの集合（1〜6個）。
#: `name` は `^[A-Z_]+$` しか許されないので、順位を英字の並びに置き換えて一意名を作る。
_phase_sets = st.lists(
    st.integers(min_value=0, max_value=20), min_size=1, max_size=6, unique=True
).map(lambda ranks: [PhaseRank(rank, "PHASE_" + "A" * (rank + 1)) for rank in ranks])


@given(_phase_sets, st.data())
def test_a_phase_set_is_the_same_however_its_input_is_ordered(
    phases: list[PhaseRank], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(phases))
    original = PhaseSet(tuple(phases))
    permuted = PhaseSet(tuple(shuffled))

    assert original == permuted
    assert hash(original) == hash(permuted)
    assert canonical.encode(original) == canonical.encode(permuted)
    assert canonical.digest(original) == canonical.digest(permuted)


@given(_phase_sets)
def test_a_phase_set_always_holds_its_phases_in_rank_order(phases: list[PhaseRank]) -> None:
    stored = PhaseSet(tuple(phases)).phases
    assert [phase.rank for phase in stored] == sorted(phase.rank for phase in phases)
    assert len(stored) == len(phases)


@given(_phase_sets)
def test_every_phase_remains_reachable_after_normalization(phases: list[PhaseRank]) -> None:
    phase_set = PhaseSet(tuple(phases))
    for phase in phases:
        assert phase_set.by_rank(phase.rank) == phase
        assert phase_set.by_name(phase.name) == phase
