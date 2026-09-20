"""`IdAllocator` の再現性のプロパティテスト（D02 §7.3・§11、ADR-0006）。

同一入力の再実行で同じ ID 列になることを確かめる。採番順は処理順（`ProcessingPoint` の順）
に一致させるので、「同じ順序で同じ種別を要求すれば同じ列が出る」ことが再現性の核になる。
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from odyssey_fx.common.ids import (
    AttemptId,
    EventId,
    FillId,
    IdAllocator,
    OrderId,
    PositionId,
    RunId,
    SequentialId,
)
from odyssey_fx.common.refs import ContentDigest

_ID_TYPES: list[type[SequentialId]] = [OrderId, FillId, PositionId, AttemptId, EventId]

_requests = st.lists(st.sampled_from(_ID_TYPES), max_size=40)

_run_ids = st.integers(min_value=0, max_value=2**32).map(
    lambda seed: RunId(ContentDigest.sha256(f"{seed:064x}"))
)


def _allocate(run_id: RunId, requests: list[type[SequentialId]]) -> list[SequentialId]:
    allocator = IdAllocator(run_id)
    return [allocator.next(id_type) for id_type in requests]


@given(_run_ids, _requests)
def test_the_same_request_order_yields_the_same_ids(
    run_id: RunId, requests: list[type[SequentialId]]
) -> None:
    assert _allocate(run_id, requests) == _allocate(run_id, requests)


@given(_run_ids, _requests)
def test_each_kind_counts_from_one_without_gaps(
    run_id: RunId, requests: list[type[SequentialId]]
) -> None:
    issued = _allocate(run_id, requests)
    for id_type in _ID_TYPES:
        of_kind = [value.seq for value in issued if type(value) is id_type]
        assert of_kind == list(range(1, len(of_kind) + 1))


@given(_run_ids, _requests)
def test_ids_are_unique_within_a_run(run_id: RunId, requests: list[type[SequentialId]]) -> None:
    issued = _allocate(run_id, requests)
    assert len(set(issued)) == len(issued)


@given(_run_ids, _requests)
def test_the_snapshot_reports_the_last_value_per_kind(
    run_id: RunId, requests: list[type[SequentialId]]
) -> None:
    allocator = IdAllocator(run_id)
    for id_type in requests:
        allocator.next(id_type)
    expected = {
        id_type.KIND: requests.count(id_type)
        for id_type in _ID_TYPES
        if requests.count(id_type) > 0
    }
    assert allocator.snapshot() == expected


@given(_run_ids, _requests)
def test_allocation_does_not_depend_on_the_run_id(
    run_id: RunId, requests: list[type[SequentialId]]
) -> None:
    """連番は run 内で一意なので、run が違っても列そのものは同じになる。

    run をまたいで区別する責務は `(RunId, ID)` の組（D02 §7.2）が負う。
    """
    other = RunId(ContentDigest.sha256("f" * 64))
    assert _allocate(run_id, requests) == _allocate(other, requests)
