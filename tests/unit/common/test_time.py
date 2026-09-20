"""`odyssey_fx.common.time` の単体テスト（D02 §3・§11）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, PhaseRank, PhaseSet, ProcessingPoint, UtcTime

NEW_YORK = ZoneInfo("America/New_York")


# --- UtcTime ----------------------------------------------------------------


def test_utc_time_accepts_a_utc_datetime() -> None:
    moment = UtcTime(datetime(2026, 3, 1, 12, 0, tzinfo=UTC))
    assert str(moment) == "2026-03-01T12:00:00Z"


def test_utc_time_rejects_a_naive_datetime() -> None:
    with pytest.raises(KernelValueError, match="tz-aware"):
        UtcTime(datetime(2026, 3, 1, 12, 0))


def test_utc_time_rejects_another_timezone() -> None:
    with pytest.raises(KernelValueError, match="offset"):
        UtcTime(datetime(2026, 3, 1, 12, 0, tzinfo=NEW_YORK))


def test_utc_time_rejects_a_zero_offset_that_is_not_named_utc() -> None:
    """オフセットが 0 でも tz 名が UTC でない値は拒否する（D02 §3.1）。"""
    reykjavik = ZoneInfo("Atlantic/Reykjavik")
    with pytest.raises(KernelValueError, match="tzname"):
        UtcTime(datetime(2026, 3, 1, 12, 0, tzinfo=reykjavik))


def test_utc_time_accepts_a_fixed_utc_offset_named_utc() -> None:
    assert UtcTime(datetime(2026, 3, 1, tzinfo=timezone(timedelta(0), "UTC")))


def test_utc_time_str_includes_microseconds_only_when_present() -> None:
    assert str(UtcTime.from_components(2026, 3, 1, 12, 0, 0, 1)) == "2026-03-01T12:00:00.000001Z"
    assert str(UtcTime.from_components(2026, 3, 1, 12, 0, 0, 0)) == "2026-03-01T12:00:00Z"


@pytest.mark.parametrize(
    "text",
    [
        "2026-03-01T12:00:00Z",
        "2026-03-01T12:00:00+00:00",
        "2026-03-01T12:00:00.123456Z",
        "2026-03-01T12:00:00.123456+00:00",
    ],
)
def test_utc_time_parse_accepts_utc_literals(text: str) -> None:
    assert UtcTime.parse(text).value.tzinfo is UTC


@pytest.mark.parametrize(
    "text",
    ["2026-03-01T12:00:00", "2026-03-01T12:00:00+09:00", "not-a-time", ""],
)
def test_utc_time_parse_rejects_non_utc_literals(text: str) -> None:
    with pytest.raises(KernelValueError):
        UtcTime.parse(text)


@pytest.mark.parametrize(
    "moment",
    [
        UtcTime.from_components(2026, 3, 1, 12, 0, 0, 0),
        UtcTime.from_components(2026, 3, 1, 12, 0, 0, 123456),
    ],
)
def test_utc_time_str_parse_roundtrip(moment: UtcTime) -> None:
    assert UtcTime.parse(str(moment)) == moment


def test_utc_time_arithmetic() -> None:
    start = UtcTime.from_components(2026, 3, 1, 12, 0)
    later = start + timedelta(hours=2)
    assert str(later) == "2026-03-01T14:00:00Z"
    assert later - start == timedelta(hours=2)
    assert later - timedelta(hours=2) == start


def test_utc_time_ordering_and_hashing() -> None:
    earlier = UtcTime.from_components(2026, 3, 1)
    later = UtcTime.from_components(2026, 3, 2)
    assert earlier < later <= later
    assert later > earlier >= earlier
    assert len({earlier, UtcTime.from_components(2026, 3, 1)}) == 1


def test_utc_time_does_not_compare_with_datetime() -> None:
    with pytest.raises(TypeError):
        _ = UtcTime.from_components(2026, 3, 1) < datetime(2026, 3, 2, tzinfo=UTC)  # type: ignore[operator]


# --- UtcTime.from_local（DST）----------------------------------------------


def test_from_local_converts_an_unambiguous_local_time() -> None:
    moment = UtcTime.from_local(datetime(2026, 1, 15, 9, 0), NEW_YORK)
    assert str(moment) == "2026-01-15T14:00:00Z"


def test_from_local_rejects_an_ambiguous_time_without_fold() -> None:
    """DST 終了で 01:30 が2回現れる日（2026-11-01）は `fold` の指定を要求する。"""
    with pytest.raises(KernelValueError, match="ambiguous"):
        UtcTime.from_local(datetime(2026, 11, 1, 1, 30), NEW_YORK)


def test_from_local_accepts_an_ambiguous_time_with_fold() -> None:
    first = UtcTime.from_local(datetime(2026, 11, 1, 1, 30), NEW_YORK, fold=0)
    second = UtcTime.from_local(datetime(2026, 11, 1, 1, 30), NEW_YORK, fold=1)
    assert str(first) == "2026-11-01T05:30:00Z"
    assert str(second) == "2026-11-01T06:30:00Z"
    assert second - first == timedelta(hours=1)


def test_from_local_rejects_a_nonexistent_time() -> None:
    """DST 開始で 02:30 が存在しない日（2026-03-08）は fold を与えても拒否する。"""
    with pytest.raises(KernelValueError, match="does not exist"):
        UtcTime.from_local(datetime(2026, 3, 8, 2, 30), NEW_YORK)
    with pytest.raises(KernelValueError, match="does not exist"):
        UtcTime.from_local(datetime(2026, 3, 8, 2, 30), NEW_YORK, fold=1)


def test_from_local_rejects_an_aware_datetime() -> None:
    with pytest.raises(KernelValueError, match="naive"):
        UtcTime.from_local(datetime(2026, 1, 15, 9, 0, tzinfo=UTC), NEW_YORK)


def test_from_local_rejects_an_invalid_fold() -> None:
    with pytest.raises(KernelValueError, match="fold"):
        UtcTime.from_local(datetime(2026, 1, 15, 9, 0), NEW_YORK, fold=2)


# --- Interval ---------------------------------------------------------------


def _interval(start_hour: int, end_hour: int) -> Interval:
    return Interval(
        UtcTime.from_components(2026, 3, 1, start_hour),
        UtcTime.from_components(2026, 3, 1, end_hour),
    )


def test_interval_rejects_an_empty_or_reversed_range() -> None:
    same = UtcTime.from_components(2026, 3, 1)
    with pytest.raises(KernelValueError, match="start < end"):
        Interval(same, same)
    with pytest.raises(KernelValueError, match="start < end"):
        Interval(UtcTime.from_components(2026, 3, 2), same)


def test_interval_is_half_open() -> None:
    span = _interval(10, 12)
    assert span.contains(UtcTime.from_components(2026, 3, 1, 10))
    assert span.contains(UtcTime.from_components(2026, 3, 1, 11))
    assert not span.contains(UtcTime.from_components(2026, 3, 1, 12))
    assert not span.contains(UtcTime.from_components(2026, 3, 1, 9))


def test_interval_duration() -> None:
    assert _interval(10, 12).duration == timedelta(hours=2)


def test_interval_overlaps_excludes_touching_ranges() -> None:
    assert _interval(10, 12).overlaps(_interval(11, 13))
    assert not _interval(10, 12).overlaps(_interval(12, 14))
    assert not _interval(10, 12).overlaps(_interval(8, 10))


def test_interval_adjacency_is_symmetric() -> None:
    left, right = _interval(10, 12), _interval(12, 14)
    assert left.adjacent_to(right)
    assert right.adjacent_to(left)
    assert not left.adjacent_to(_interval(13, 14))


def test_interval_rejects_wrong_argument_types() -> None:
    span = _interval(10, 12)
    with pytest.raises(TypeError):
        span.contains(_interval(10, 11))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        span.overlaps(UtcTime.from_components(2026, 3, 1))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        span.adjacent_to(UtcTime.from_components(2026, 3, 1))  # type: ignore[arg-type]


def test_interval_str() -> None:
    assert str(_interval(10, 12)) == "[2026-03-01T10:00:00Z, 2026-03-01T12:00:00Z)"


# --- PhaseRank / PhaseSet ---------------------------------------------------


def test_phase_rank_accepts_uppercase_names() -> None:
    assert str(PhaseRank(0, "ADMISSION")) == "ADMISSION"
    assert PhaseRank(3, "RUN_END").rank == 3


@pytest.mark.parametrize("name", ["admission", "Admission", "PHASE-1", "PHASE1", ""])
def test_phase_rank_rejects_invalid_names(name: str) -> None:
    with pytest.raises(KernelValueError, match="name"):
        PhaseRank(0, name)


def test_phase_rank_rejects_a_negative_rank() -> None:
    with pytest.raises(KernelValueError, match=">= 0"):
        PhaseRank(-1, "ADMISSION")


def test_phase_rank_rejects_a_bool_rank() -> None:
    with pytest.raises(KernelValueError, match="int"):
        PhaseRank(True, "ADMISSION")


def test_phase_set_accepts_a_bijective_set() -> None:
    phases = PhaseSet((PhaseRank(1, "EXECUTION"), PhaseRank(0, "ADMISSION")))
    assert phases.by_name("ADMISSION").rank == 0
    assert phases.by_rank(1).name == "EXECUTION"
    assert [phase.name for phase in phases.ordered()] == ["ADMISSION", "EXECUTION"]


def test_phase_set_ordered_keeps_every_entry() -> None:
    """重複を構築時に拒否するので、`ordered()` は並べ替えるだけで要素を捨てない。"""
    phases = PhaseSet((PhaseRank(2, "TRACE"), PhaseRank(0, "ADMISSION"), PhaseRank(1, "EXECUTION")))
    assert len(phases.ordered()) == len(phases.phases)
    assert [phase.rank for phase in phases.ordered()] == [0, 1, 2]


def test_phase_set_rejects_one_rank_with_two_names() -> None:
    with pytest.raises(KernelValueError, match="rank 0 appears more than once"):
        PhaseSet((PhaseRank(0, "ADMISSION"), PhaseRank(0, "EXECUTION")))


def test_phase_set_rejects_one_name_with_two_ranks() -> None:
    with pytest.raises(KernelValueError, match="appears more than once"):
        PhaseSet((PhaseRank(0, "ADMISSION"), PhaseRank(1, "ADMISSION")))


def test_phase_set_rejects_an_exact_duplicate() -> None:
    """順位も名前も同じ要素の重複も一意性の違反として拒否する（D02 §3.3）。"""
    with pytest.raises(KernelValueError, match="rank 0 appears more than once"):
        PhaseSet((PhaseRank(0, "ADMISSION"), PhaseRank(0, "ADMISSION")))


def test_phase_set_rejects_an_empty_set() -> None:
    with pytest.raises(KernelValueError, match="empty"):
        PhaseSet(())


def test_phase_set_rejects_unknown_lookups() -> None:
    phases = PhaseSet((PhaseRank(0, "ADMISSION"),))
    with pytest.raises(KernelValueError, match="unknown phase name"):
        phases.by_name("EXECUTION")
    with pytest.raises(KernelValueError, match="unknown phase rank"):
        phases.by_rank(9)


# --- ProcessingPoint --------------------------------------------------------


def _point(hour: int, rank: int, sequence: int) -> ProcessingPoint:
    return ProcessingPoint(
        UtcTime.from_components(2026, 3, 1, hour), PhaseRank(rank, "PHASE_" + "X" * rank), sequence
    )


def test_processing_point_rejects_a_negative_sequence() -> None:
    with pytest.raises(KernelValueError, match=">= 0"):
        ProcessingPoint(UtcTime.from_components(2026, 3, 1), PhaseRank(0, "A"), -1)


def test_processing_point_orders_by_time_then_phase_then_sequence() -> None:
    assert _point(10, 0, 0) < _point(11, 0, 0)
    assert _point(10, 0, 0) < _point(10, 1, 0)
    assert _point(10, 1, 0) < _point(10, 1, 1)
    assert _point(10, 1, 1) >= _point(10, 1, 1)
    assert _point(11, 0, 0) > _point(10, 9, 9)


def test_processing_point_str() -> None:
    point = ProcessingPoint(UtcTime.from_components(2026, 3, 1, 10), PhaseRank(2, "EXECUTION"), 7)
    assert str(point) == "2026-03-01T10:00:00Z/EXECUTION#7"


def test_processing_point_does_not_order_against_other_types() -> None:
    with pytest.raises(TypeError):
        _ = _point(10, 0, 0) < UtcTime.from_components(2026, 3, 1)  # type: ignore[operator]
