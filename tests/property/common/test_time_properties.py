"""`ProcessingPoint` の全順序性のプロパティテスト（D02 §3.3・§11）。

全順序であるとは、任意の2点が比較でき（完全性）、順序が推移的で、反対称で、
`(time, phase.rank, sequence)` の辞書式順序と一致することをいう。
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from odyssey_fx.common.time import PhaseRank, ProcessingPoint, UtcTime

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
