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
    PartitionRecord,
)
from tests.fixtures.synthetic import market, snapshots

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


#: 封印期間 partition が覆う区間（2024年・2025年の2年ぶん）。
HOLDOUT_INTERVAL = Interval(
    start=UtcTime.parse("2024-01-01T00:00:00Z"),
    end=UtcTime.parse("2026-01-01T00:00:00Z"),
)


def _record(partition_id: PartitionId = HOLDOUT_PARTITION) -> PartitionRecord:
    """封印期間 partition の記録（区間を持つので被覆の判定ができる）。"""
    return PartitionRecord(
        partition_id=partition_id,
        interval=HOLDOUT_INTERVAL,
        bar_count=100,
        digest=snapshots.digest_for(partition_id.access_class.value),
    )


def _legacy(
    observation: LegacyObservation,
    *,
    start: str = "2024-01-01T00:00:00Z",
    end: str = "2026-01-01T00:00:00Z",
) -> LegacyAccessRecord:
    """旧基盤の閲覧履歴を1件作る。既定では partition の区間を完全に覆う。"""
    return LegacyAccessRecord(
        series_id=SERIES,
        interval=Interval(start=UtcTime.parse(start), end=UtcTime.parse(end)),
        observation=observation,
    )


def test_a_fully_covering_unobserved_history_yields_sealed() -> None:
    """未観測の記録が partition の区間を完全に覆う場合だけ `SEALED`（ADR-0014）。"""
    state = initial_holdout_state(_record(), (_legacy(LegacyObservation.NOT_OBSERVED),))
    assert state is HoldoutState.SEALED


def test_several_records_that_together_cover_the_partition_yield_sealed() -> None:
    """複数の記録の和集合が隙間なく覆えば `SEALED`。"""
    state = initial_holdout_state(
        _record(),
        (
            _legacy(
                LegacyObservation.NOT_OBSERVED,
                start="2024-01-01T00:00:00Z",
                end="2025-01-01T00:00:00Z",
            ),
            _legacy(
                LegacyObservation.NOT_OBSERVED,
                start="2025-01-01T00:00:00Z",
                end="2026-01-01T00:00:00Z",
            ),
        ),
    )
    assert state is HoldoutState.SEALED


def test_the_record_order_does_not_change_the_coverage_decision() -> None:
    records = (
        _legacy(
            LegacyObservation.NOT_OBSERVED,
            start="2025-01-01T00:00:00Z",
            end="2026-01-01T00:00:00Z",
        ),
        _legacy(
            LegacyObservation.NOT_OBSERVED,
            start="2024-01-01T00:00:00Z",
            end="2025-01-01T00:00:00Z",
        ),
    )
    assert initial_holdout_state(_record(), records) is HoldoutState.SEALED


def test_a_partially_covering_history_yields_consumed() -> None:
    """一部しか覆わない記録では `SEALED` にしない（ADR-0014 の fail-closed）。

    「2024年の1日だけ未観測と確認した」記録で2年ぶんの封印期間が開いてはいけない。
    """
    state = initial_holdout_state(
        _record(),
        (
            _legacy(
                LegacyObservation.NOT_OBSERVED,
                start="2024-01-01T00:00:00Z",
                end="2024-01-02T00:00:00Z",
            ),
        ),
    )
    assert state is HoldoutState.CONSUMED


def test_a_history_with_a_gap_yields_consumed() -> None:
    """記録の間に隙間があれば、その時間は未観測と確認できていない。"""
    state = initial_holdout_state(
        _record(),
        (
            _legacy(
                LegacyObservation.NOT_OBSERVED,
                start="2024-01-01T00:00:00Z",
                end="2024-06-01T00:00:00Z",
            ),
            _legacy(
                LegacyObservation.NOT_OBSERVED,
                start="2024-07-01T00:00:00Z",
                end="2026-01-01T00:00:00Z",
            ),
        ),
    )
    assert state is HoldoutState.CONSUMED


def test_a_history_that_starts_late_yields_consumed() -> None:
    """partition の先頭が覆われていなければ `SEALED` にしない。"""
    state = initial_holdout_state(
        _record(),
        (
            _legacy(
                LegacyObservation.NOT_OBSERVED,
                start="2024-02-01T00:00:00Z",
                end="2026-01-01T00:00:00Z",
            ),
        ),
    )
    assert state is HoldoutState.CONSUMED


def test_a_history_for_another_series_does_not_seal_this_partition() -> None:
    """系列が違う記録は被覆に数えない。"""
    other = LegacyAccessRecord(
        series_id=market.series(symbol=market.EURUSD),
        interval=HOLDOUT_INTERVAL,
        observation=LegacyObservation.NOT_OBSERVED,
    )
    assert initial_holdout_state(_record(), (other,)) is HoldoutState.CONSUMED


def test_an_observed_history_yields_consumed() -> None:
    state = initial_holdout_state(_record(), (_legacy(LegacyObservation.OBSERVED),))
    assert state is HoldoutState.CONSUMED


def test_an_unknown_history_yields_consumed() -> None:
    """履歴が確認できない partition は `SEALED` にしない（fail-closed、ADR-0014）。"""
    state = initial_holdout_state(_record(), (_legacy(LegacyObservation.UNKNOWN),))
    assert state is HoldoutState.CONSUMED


def test_a_mixed_history_yields_consumed() -> None:
    """完全に覆えていても、観測済みが1件でも重なれば `CONSUMED`。"""
    state = initial_holdout_state(
        _record(),
        (
            _legacy(LegacyObservation.NOT_OBSERVED),
            _legacy(
                LegacyObservation.OBSERVED,
                start="2024-03-01T00:00:00Z",
                end="2024-04-01T00:00:00Z",
            ),
        ),
    )
    assert state is HoldoutState.CONSUMED


def test_a_missing_history_yields_consumed() -> None:
    assert initial_holdout_state(_record(), ()) is HoldoutState.CONSUMED


def test_only_holdout_partitions_carry_a_state() -> None:
    research = PartitionRecord(
        partition_id=RESEARCH_PARTITION,
        interval=HOLDOUT_INTERVAL,
        bar_count=100,
        digest=snapshots.digest_for("research"),
    )
    with pytest.raises(MarketDataValueError, match="only LEGACY_HOLDOUT"):
        initial_holdout_state(research, ())


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
    assert derive_holdout_state(_record(), legacy, entries) is HoldoutState.SEALED


def test_a_consumption_record_moves_the_partition_to_consumed() -> None:
    legacy = (_legacy(LegacyObservation.NOT_OBSERVED),)
    entries = append_entry(
        append_entry((), _entry(AccessLogEntryKind.GRANTED)),
        _entry(AccessLogEntryKind.CONSUMED),
    )
    assert derive_holdout_state(_record(), legacy, entries) is HoldoutState.CONSUMED


def test_the_transition_to_consumed_is_irreversible() -> None:
    """`CONSUMED` の後にどんな記録を追記しても `SEALED` へは戻らない（ADR-0014）。"""
    legacy = (_legacy(LegacyObservation.NOT_OBSERVED),)
    entries = append_entry((), _entry(AccessLogEntryKind.CONSUMED))
    entries = append_entry(entries, _entry(AccessLogEntryKind.GRANTED))
    entries = append_entry(entries, _entry(AccessLogEntryKind.RESEARCH_OPT_IN))
    assert derive_holdout_state(_record(), legacy, entries) is HoldoutState.CONSUMED


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
    assert derive_holdout_state(_record(), legacy, entries) is HoldoutState.SEALED


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
