"""`odyssey_fx.common.ids` の単体テスト（D02 §7・§11）。"""

from __future__ import annotations

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    AllocationId,
    AttemptId,
    DigestId,
    EvaluationId,
    EventId,
    EvidenceId,
    ExperimentId,
    FillId,
    IdAllocator,
    OpportunityId,
    OrderId,
    OutputId,
    PositionId,
    RequestId,
    ReservationId,
    RunId,
    SequentialId,
    SnapshotId,
    short_digest,
)
from odyssey_fx.common.refs import ContentDigest

HEX = "a" * 64
DIGEST = ContentDigest.sha256(HEX)

SEQUENTIAL_TYPES: list[tuple[type[SequentialId], str]] = [
    (EvaluationId, "EVAL"),
    (RequestId, "REQ"),
    (OutputId, "OUT"),
    (OpportunityId, "OPP"),
    (AttemptId, "ATT"),
    (OrderId, "ORD"),
    (FillId, "FIL"),
    (PositionId, "POS"),
    (ReservationId, "RSV"),
    (AllocationId, "ALC"),
    (EventId, "EVT"),
    (EvidenceId, "EVD"),
]


# --- ダイジェスト系 ---------------------------------------------------------


@pytest.mark.parametrize("id_type", [RunId, SnapshotId, ExperimentId])
def test_digest_ids_print_the_full_hex(id_type: type[DigestId]) -> None:
    value = id_type(DIGEST)
    assert str(value) == HEX
    assert value.hex == HEX
    assert len(str(value)) == 64


@pytest.mark.parametrize("id_type", [RunId, SnapshotId, ExperimentId])
def test_digest_ids_reject_a_bare_string(id_type: type[DigestId]) -> None:
    with pytest.raises(KernelValueError, match="ContentDigest"):
        id_type(HEX)  # type: ignore[arg-type]


def test_digest_ids_of_different_kinds_are_not_equal() -> None:
    assert RunId(DIGEST) != SnapshotId(DIGEST)  # type: ignore[comparison-overlap]


def test_short_digest_is_display_only() -> None:
    assert short_digest(RunId(DIGEST)) == HEX[:12]
    assert short_digest(RunId(DIGEST), 8) == HEX[:8]
    with pytest.raises(KernelValueError, match="length"):
        short_digest(RunId(DIGEST), 0)
    with pytest.raises(KernelValueError, match="length"):
        short_digest(RunId(DIGEST), 65)
    with pytest.raises(KernelValueError, match="DigestId"):
        short_digest(OrderId(1))  # type: ignore[arg-type]


# --- 連番系 -----------------------------------------------------------------


@pytest.mark.parametrize(("id_type", "kind"), SEQUENTIAL_TYPES)
def test_sequential_ids_carry_their_kind(id_type: type[SequentialId], kind: str) -> None:
    assert id_type.KIND == kind
    assert str(id_type(42)) == f"{kind}:00000042"


@pytest.mark.parametrize(("id_type", "kind"), SEQUENTIAL_TYPES)
def test_sequential_id_str_parse_roundtrip(id_type: type[SequentialId], kind: str) -> None:
    value = id_type(8)
    assert id_type.parse(str(value)) == value


def test_sequential_id_parse_rejects_another_kind() -> None:
    with pytest.raises(KernelValueError, match="expects kind 'ORD'"):
        OrderId.parse("FIL:00000042")


@pytest.mark.parametrize("text", ["ORD:42", "ORD-00000042", "00000042", "ord:00000042", ""])
def test_sequential_id_parse_rejects_malformed_literals(text: str) -> None:
    with pytest.raises(KernelValueError, match="invalid OrderId literal|expects kind"):
        OrderId.parse(text)


def test_sequential_id_parse_accepts_more_than_eight_digits() -> None:
    assert OrderId.parse("ORD:123456789") == OrderId(123456789)
    assert str(OrderId(123456789)) == "ORD:123456789"


@pytest.mark.parametrize("seq", [0, -1])
def test_sequential_ids_start_at_one(seq: int) -> None:
    with pytest.raises(KernelValueError, match=">= 1"):
        OrderId(seq)


def test_sequential_ids_reject_bools() -> None:
    with pytest.raises(KernelValueError, match="must be an int"):
        OrderId(True)


def test_the_base_sequential_id_has_no_kind() -> None:
    with pytest.raises(KernelValueError, match="must define a KIND"):
        SequentialId(1)


def test_sequential_ids_of_different_kinds_do_not_compare() -> None:
    assert OrderId(1) != FillId(1)  # type: ignore[comparison-overlap]
    with pytest.raises(TypeError):
        _ = OrderId(1) < FillId(2)


def test_sequential_ids_order_within_a_kind() -> None:
    assert OrderId(1) < OrderId(2) <= OrderId(2)
    assert OrderId(3) > OrderId(2) >= OrderId(2)


# --- IdAllocator ------------------------------------------------------------


def _allocator() -> IdAllocator:
    return IdAllocator(RunId(DIGEST))


def test_allocator_counts_from_one_per_kind() -> None:
    allocator = _allocator()
    assert allocator.next(OrderId) == OrderId(1)
    assert allocator.next(OrderId) == OrderId(2)
    assert allocator.next(FillId) == FillId(1)
    assert allocator.next(OrderId) == OrderId(3)


def test_allocator_snapshot_reports_the_last_value_per_kind() -> None:
    allocator = _allocator()
    allocator.next(OrderId)
    allocator.next(OrderId)
    allocator.next(FillId)
    assert allocator.snapshot() == {"ORD": 2, "FIL": 1}


def test_allocator_snapshot_is_a_copy() -> None:
    allocator = _allocator()
    allocator.next(OrderId)
    taken = allocator.snapshot()
    taken["ORD"] = 999
    assert allocator.snapshot() == {"ORD": 1}


def test_allocator_exposes_its_run_id() -> None:
    assert _allocator().run_id == RunId(DIGEST)


def test_allocator_requires_a_run_id() -> None:
    with pytest.raises(KernelValueError, match="RunId"):
        IdAllocator(SnapshotId(DIGEST))  # type: ignore[arg-type]


def test_allocator_rejects_non_sequential_types() -> None:
    allocator = _allocator()
    with pytest.raises(KernelValueError, match="SequentialId type"):
        allocator.next(RunId)  # type: ignore[type-var]
    with pytest.raises(KernelValueError, match="SequentialId type"):
        allocator.next(OrderId(1))  # type: ignore[arg-type]


def _allocator_run(order: list[type[SequentialId]]) -> list[SequentialId]:
    allocator = _allocator()
    return [allocator.next(id_type) for id_type in order]


def test_two_allocators_with_the_same_run_id_produce_the_same_sequence() -> None:
    """同一入力の再実行で同じ ID 列になること（D02 §7.3）。"""
    order: list[type[SequentialId]] = [OrderId, FillId, OrderId, PositionId, FillId]
    assert _allocator_run(order) == _allocator_run(order)
