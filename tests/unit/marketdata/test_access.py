"""アクセス分類と封印状態の単体テスト（D03 §3.8・§11、ADR-0014）。

確かめること:

- partition の所属が **`bar_end` 基準**で決まる。区間をまたぐ足の後半の情報が前の区分へ
  漏れない（2023-12-31 に始まり 2024-01-01 に終わる日足は封印期間に入る）。
- 封印状態は追記専用の閲覧記録から導出され、`SEALED → CONSUMED` が不可逆である。
- 未観測と確認できた場合だけ `SEALED` になり、履歴不明は `CONSUMED` に倒れる。
"""

from __future__ import annotations

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.access_log import (
    AccessLogEntry,
    AccessLogEntryKind,
    append_entry,
    derive_holdout_state,
    initial_holdout_state,
    serialize_entry,
)
from odyssey_fx.marketdata.domain.access import (
    INITIAL_ACCESS_BOUNDARIES,
    AccessBoundaries,
    AccessClass,
    HoldoutState,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.snapshot import (
    LegacyAccessRecord,
    LegacyObservation,
    PartitionId,
)
from tests.fixtures.synthetic import market

SERIES = market.series()
HOLDOUT_PARTITION = PartitionId(series=SERIES, access_class=AccessClass.LEGACY_HOLDOUT)
RESEARCH_PARTITION = PartitionId(series=SERIES, access_class=AccessClass.RESEARCH_HISTORY)
RECORDED_AT = UtcTime.parse("2026-09-20T00:00:00Z")


# --- 期間境界（D03 §3.8）----------------------------------------------------


def test_the_initial_boundaries_split_research_holdout_and_quarantine() -> None:
    boundaries = INITIAL_ACCESS_BOUNDARIES
    assert (
        boundaries.classify(UtcTime.parse("2023-06-01T00:00:00Z")) is AccessClass.RESEARCH_HISTORY
    )
    assert boundaries.classify(UtcTime.parse("2025-06-01T00:00:00Z")) is AccessClass.LEGACY_HOLDOUT
    assert (
        boundaries.classify(UtcTime.parse("2026-06-01T00:00:00Z"))
        is AccessClass.QUARANTINED_UNASSIGNED
    )


def test_a_bar_ending_exactly_on_a_boundary_falls_into_the_later_class() -> None:
    """境界以上の `bar_end` は後ろの区分に入る（D03 §3.8）。"""
    assert (
        INITIAL_ACCESS_BOUNDARIES.classify(UtcTime.parse("2024-01-01T00:00:00Z"))
        is AccessClass.LEGACY_HOLDOUT
    )


def test_a_daily_bar_spanning_the_year_boundary_is_classified_by_its_end() -> None:
    """2023-12-31 22:00Z に始まり 2024-01-01 22:00Z に終わる日足は封印期間に入る。

    `bar_start` 基準にすると 2024 年の情報が研究区分へ漏れる（D03 §3.8 の理由）。
    """
    spanning = Interval(
        start=UtcTime.parse("2023-12-31T22:00:00Z"),
        end=UtcTime.parse("2024-01-01T22:00:00Z"),
    )
    assert INITIAL_ACCESS_BOUNDARIES.classify(spanning.end) is AccessClass.LEGACY_HOLDOUT
    # 開始時刻で分類していたら研究区分になってしまう。
    assert INITIAL_ACCESS_BOUNDARIES.classify(spanning.start) is AccessClass.RESEARCH_HISTORY


def test_the_boundaries_require_research_to_end_before_the_holdout() -> None:
    with pytest.raises(MarketDataValueError, match="research_until < holdout_until"):
        AccessBoundaries(
            research_until=UtcTime.from_components(2026, 1, 1),
            holdout_until=UtcTime.from_components(2024, 1, 1),
        )


# --- 封印状態の初期値（ADR-0014）--------------------------------------------


def _legacy(observation: LegacyObservation) -> LegacyAccessRecord:
    return LegacyAccessRecord(
        series_id=SERIES,
        interval=Interval(
            start=UtcTime.parse("2024-01-01T00:00:00Z"),
            end=UtcTime.parse("2026-01-01T00:00:00Z"),
        ),
        observation=observation,
    )


def test_only_a_confirmed_unobserved_history_yields_sealed() -> None:
    state = initial_holdout_state(HOLDOUT_PARTITION, (_legacy(LegacyObservation.NOT_OBSERVED),))
    assert state is HoldoutState.SEALED


def test_an_observed_history_yields_consumed() -> None:
    state = initial_holdout_state(HOLDOUT_PARTITION, (_legacy(LegacyObservation.OBSERVED),))
    assert state is HoldoutState.CONSUMED


def test_an_unknown_history_yields_consumed() -> None:
    """履歴が確認できない partition は `SEALED` にしない（fail-closed、ADR-0014）。"""
    state = initial_holdout_state(HOLDOUT_PARTITION, (_legacy(LegacyObservation.UNKNOWN),))
    assert state is HoldoutState.CONSUMED


def test_a_missing_history_yields_consumed() -> None:
    assert initial_holdout_state(HOLDOUT_PARTITION, ()) is HoldoutState.CONSUMED


def test_only_holdout_partitions_carry_a_state() -> None:
    with pytest.raises(MarketDataValueError, match="only LEGACY_HOLDOUT"):
        initial_holdout_state(RESEARCH_PARTITION, ())


# --- 閲覧記録からの導出（D03 §3.8）-----------------------------------------


def _entry(kind: AccessLogEntryKind) -> AccessLogEntry:
    return AccessLogEntry(
        kind=kind,
        partition_id=HOLDOUT_PARTITION,
        recorded_at=RECORDED_AT,
        actor="tester",
        purpose="final evaluation",
    )


def test_a_grant_alone_leaves_the_partition_sealed() -> None:
    """許可だけでは公開しない。消費の記録が永続化されて初めて `CONSUMED`（ADR-0014）。"""
    legacy = (_legacy(LegacyObservation.NOT_OBSERVED),)
    entries = append_entry((), _entry(AccessLogEntryKind.GRANTED))
    assert derive_holdout_state(HOLDOUT_PARTITION, legacy, entries) is HoldoutState.SEALED


def test_a_consumption_record_moves_the_partition_to_consumed() -> None:
    legacy = (_legacy(LegacyObservation.NOT_OBSERVED),)
    entries = append_entry(
        append_entry((), _entry(AccessLogEntryKind.GRANTED)),
        _entry(AccessLogEntryKind.CONSUMED),
    )
    assert derive_holdout_state(HOLDOUT_PARTITION, legacy, entries) is HoldoutState.CONSUMED


def test_the_transition_to_consumed_is_irreversible() -> None:
    """`CONSUMED` の後にどんな記録を追記しても `SEALED` へは戻らない（ADR-0014）。"""
    legacy = (_legacy(LegacyObservation.NOT_OBSERVED),)
    entries = append_entry((), _entry(AccessLogEntryKind.CONSUMED))
    entries = append_entry(entries, _entry(AccessLogEntryKind.GRANTED))
    entries = append_entry(entries, _entry(AccessLogEntryKind.RESEARCH_OPT_IN))
    assert derive_holdout_state(HOLDOUT_PARTITION, legacy, entries) is HoldoutState.CONSUMED


def test_a_record_for_another_partition_does_not_consume_this_one() -> None:
    legacy = (_legacy(LegacyObservation.NOT_OBSERVED),)
    other = PartitionId(
        series=market.series(symbol=market.EURUSD),
        access_class=AccessClass.LEGACY_HOLDOUT,
    )
    entries = (
        AccessLogEntry(
            kind=AccessLogEntryKind.CONSUMED,
            partition_id=other,
            recorded_at=RECORDED_AT,
            actor="tester",
        ),
    )
    assert derive_holdout_state(HOLDOUT_PARTITION, legacy, entries) is HoldoutState.SEALED


def test_appending_never_reorders_or_drops_existing_records() -> None:
    """閲覧記録は追記専用。既存の行は並べ替えも削除もしない（ADR-0014）。"""
    first = _entry(AccessLogEntryKind.GRANTED)
    second = _entry(AccessLogEntryKind.CONSUMED)
    assert append_entry((first,), second) == (first, second)


def test_a_serialized_entry_carries_only_strings() -> None:
    payload = serialize_entry(_entry(AccessLogEntryKind.CONSUMED))
    assert all(isinstance(value, str) for value in payload.values())
    assert payload["partition"] == "USDJPY_1h_bid/LEGACY_HOLDOUT"
    assert payload["kind"] == "CONSUMED"
