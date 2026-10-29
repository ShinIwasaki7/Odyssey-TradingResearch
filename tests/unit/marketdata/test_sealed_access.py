"""OANDA MT5 の経路の区分・取引週の partition・初期状態の規則（ADR-0038、D03 §3.8・§16）。

人工の時刻だけを使う（実データ・2024 年以降のデータを読まない）。取得の開始時点は設定から
読む値なので、ここでは人工の値（ニューヨークの日曜 17:00 のどれか）を渡す。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import (
    _partition_from_payload,
    _partition_payload,
)
from odyssey_fx.marketdata.application.access_log import (
    AccessLogEntry,
    AccessLogEntryKind,
    derive_holdout_state,
    derive_sealed_state,
)
from odyssey_fx.marketdata.domain.access import (
    OANDA_SEALED_CLASSES,
    PROTECTED_CLASSES,
    SEALED_CLASSES,
    SEALED_RESEARCH_UNTIL,
    AccessClass,
    HoldoutState,
    SealedAccessBoundaries,
    is_trading_week_start,
    next_trading_week_start,
    sealed_partition_period,
    trading_week_start,
)
from odyssey_fx.marketdata.domain.errors import (
    AcquisitionStartUnset,
    HoldoutStateUnknown,
    MarketDataValueError,
    SealedBoundaryBar,
)
from odyssey_fx.marketdata.domain.sealed_state import (
    ExistingSealedPartition,
    InitialStateReason,
    ViolationScope,
    initial_sealed_state,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId, PartitionRecord
from tests.fixtures.synthetic import market

T = UtcTime.parse
M1 = market.series(timeframe_id="1m")
H1 = market.series(timeframe_id="1h")
EURUSD_M1 = market.series(symbol=Symbol("EURUSD"), timeframe_id="1m")
#: 人工の取得の開始時点（2025-06-01 は日曜。夏時間なので 17:00 は 21:00Z）。
ACQUISITION_START = T("2025-06-01T21:00:00Z")
BOUNDARIES = SealedAccessBoundaries(acquisition_start=ACQUISITION_START)
FIRST_WEEK = T("2024-01-01T00:00:00Z")
SECOND_WEEK = T("2024-01-07T22:00:00Z")


def _pid(
    series: SeriesId = M1,
    cls: AccessClass = AccessClass.HOLDOUT_SINCE_2024,
    start: UtcTime = SECOND_WEEK,
) -> PartitionId:
    return PartitionId(series=series, access_class=cls, period_start=start)


# --- 区分の集合 --------------------------------------------------------------------------


def test_the_two_new_sealed_classes_carry_a_holdout_state() -> None:
    assert OANDA_SEALED_CLASSES == {AccessClass.HOLDOUT_SINCE_2024, AccessClass.FORWARD_HOLDOUT}
    assert SEALED_CLASSES == OANDA_SEALED_CLASSES | {AccessClass.LEGACY_HOLDOUT}
    assert PROTECTED_CLASSES == SEALED_CLASSES | {AccessClass.QUARANTINED_UNASSIGNED}
    assert AccessClass.RESEARCH_HISTORY not in PROTECTED_CLASSES
    assert SEALED_RESEARCH_UNTIL == T("2024-01-01T00:00:00Z")


# --- 取引週（ニューヨークの日曜 17:00）--------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # 冬（UTC−5）: 日曜 17:00 は 22:00Z。
        ("2024-01-03T12:00:00Z", "2023-12-31T22:00:00Z"),
        # ちょうど境目の時刻は、その境目から始まる週に入る。
        ("2024-01-07T22:00:00Z", "2024-01-07T22:00:00Z"),
        ("2024-01-07T21:59:59Z", "2023-12-31T22:00:00Z"),
        # 夏（UTC−4）: 日曜 17:00 は 21:00Z。
        ("2024-07-10T00:00:00Z", "2024-07-07T21:00:00Z"),
        # 日曜の 17:00 より前は前の週。
        ("2024-07-07T20:59:00Z", "2024-06-30T21:00:00Z"),
    ],
)
def test_the_trading_week_starts_on_new_york_sunday_17(moment: str, expected: str) -> None:
    assert trading_week_start(T(moment)) == T(expected)


def test_the_spring_switch_week_is_167_hours_and_the_autumn_switch_week_169_hours() -> None:
    # 2024-03-10（日）02:00 に夏時間へ。前の週の始まりは 03-03 17:00 EST = 22:00Z。
    before_spring = T("2024-03-03T22:00:00Z")
    after_spring = next_trading_week_start(before_spring)
    assert after_spring == T("2024-03-10T21:00:00Z")
    assert after_spring - before_spring == timedelta(hours=167)
    # 2024-11-03（日）02:00 に標準時へ。前の週の始まりは 10-27 17:00 EDT = 21:00Z。
    before_autumn = T("2024-10-27T21:00:00Z")
    after_autumn = next_trading_week_start(before_autumn)
    assert after_autumn == T("2024-11-03T22:00:00Z")
    assert after_autumn - before_autumn == timedelta(hours=169)
    # 切替の週の中の時刻も、時刻の規則だけで正しい週に入る。
    assert trading_week_start(T("2024-03-10T21:30:00Z")) == after_spring
    assert trading_week_start(T("2024-03-10T20:59:00Z")) == before_spring
    assert trading_week_start(T("2024-11-03T21:30:00Z")) == before_autumn


def test_only_a_sunday_17_is_a_week_start() -> None:
    assert is_trading_week_start(T("2024-01-07T22:00:00Z"))
    assert not is_trading_week_start(T("2024-01-07T21:00:00Z"))
    assert not is_trading_week_start(FIRST_WEEK)
    with pytest.raises(MarketDataValueError, match="not the start of a trading week"):
        next_trading_week_start(FIRST_WEEK)


# --- 区分の判定（ADR-0038 第1項、D03 §16.1）-------------------------------------------


def test_bars_are_classified_by_their_end_into_the_three_classes() -> None:
    assert (
        BOUNDARIES.classify(T("2023-12-29T21:58:00Z"), T("2023-12-29T21:59:00Z"))
        is AccessClass.RESEARCH_HISTORY
    )
    assert (
        BOUNDARIES.classify(T("2024-01-02T00:00:00Z"), T("2024-01-02T00:01:00Z"))
        is AccessClass.HOLDOUT_SINCE_2024
    )
    # 終わりが取得の開始時点ちょうどの足は後ろの区分（前向きの封印）に入る。
    assert (
        BOUNDARIES.classify(T("2025-06-01T20:59:00Z"), ACQUISITION_START)
        is AccessClass.FORWARD_HOLDOUT
    )
    assert (
        BOUNDARIES.classify(T("2025-06-01T20:58:00Z"), T("2025-06-01T20:59:00Z"))
        is AccessClass.HOLDOUT_SINCE_2024
    )
    assert (
        BOUNDARIES.classify(T("2027-02-01T10:00:00Z"), T("2027-02-01T10:01:00Z"))
        is AccessClass.FORWARD_HOLDOUT
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        # 終わりがちょうど 2024-01-01T00:00Z の 1 分足（D03 §16.1。PR #109 の仮置き S2b-7）。
        ("2023-12-31T23:59:00Z", "2024-01-01T00:00:00Z"),
        # 2024-01-01 をまたぐ 1 時間足。
        ("2023-12-31T23:30:00Z", "2024-01-01T00:30:00Z"),
    ],
)
def test_a_bar_straddling_2024_is_a_structural_error(start: str, end: str) -> None:
    with pytest.raises(SealedBoundaryBar, match="straddles"):
        BOUNDARIES.classify(T(start), T(end))
    # 取得の開始時点が空でも同じ（研究期間の snapshot の受入れにも当てる）。
    with pytest.raises(SealedBoundaryBar):
        SealedAccessBoundaries(acquisition_start=None).classify(T(start), T(end))


def test_without_the_acquisition_start_no_bar_from_2024_is_classified() -> None:
    unset = SealedAccessBoundaries(acquisition_start=None)
    assert (
        unset.classify(T("2023-06-01T00:00:00Z"), T("2023-06-01T00:01:00Z"))
        is AccessClass.RESEARCH_HISTORY
    )
    with pytest.raises(AcquisitionStartUnset):
        unset.classify(T("2024-01-02T00:00:00Z"), T("2024-01-02T00:01:00Z"))


@pytest.mark.parametrize(
    "value",
    [
        "2025-06-01T22:00:00Z",  # 夏の日曜 18:00（境目ではない）
        "2025-06-02T21:00:00Z",  # 月曜
        "2023-12-31T22:00:00Z",  # 2024-01-01 より前
    ],
)
def test_the_acquisition_start_must_be_a_week_start_after_2024(value: str) -> None:
    with pytest.raises(MarketDataValueError):
        SealedAccessBoundaries(acquisition_start=T(value))


# --- partition の期間（ADR-0038 第2項）------------------------------------------------


def test_the_first_sealed_partition_starts_on_2024_01_01() -> None:
    period = BOUNDARIES.partition_period(AccessClass.HOLDOUT_SINCE_2024, T("2024-01-01T00:01:00Z"))
    assert period == Interval(start=FIRST_WEEK, end=SECOND_WEEK)
    assert sealed_partition_period(FIRST_WEEK) == period


def test_a_bar_ending_on_a_week_start_belongs_to_the_later_week() -> None:
    period = BOUNDARIES.partition_period(AccessClass.HOLDOUT_SINCE_2024, SECOND_WEEK)
    assert period.start == SECOND_WEEK
    assert period.end == T("2024-01-14T22:00:00Z")


def test_the_two_sealed_classes_split_on_the_acquisition_start() -> None:
    last_holdout = BOUNDARIES.partition_period(
        AccessClass.HOLDOUT_SINCE_2024, T("2025-05-30T20:00:00Z")
    )
    first_forward = BOUNDARIES.partition_period(AccessClass.FORWARD_HOLDOUT, ACQUISITION_START)
    assert last_holdout.end == ACQUISITION_START == first_forward.start


def test_only_the_new_sealed_classes_are_split_into_weeks() -> None:
    with pytest.raises(MarketDataValueError, match="trading weeks"):
        BOUNDARIES.partition_period(AccessClass.LEGACY_HOLDOUT, T("2024-01-02T00:00:00Z"))
    with pytest.raises(MarketDataValueError, match="not the start of a sealed partition"):
        sealed_partition_period(T("2024-01-02T00:00:00Z"))


# --- PartitionId の取引週（ADR-0038 第2項）--------------------------------------------


def test_a_sealed_partition_id_carries_its_week_in_the_directory() -> None:
    pid = _pid()
    assert pid.directory == "USDJPY_1m_bid/HOLDOUT_SINCE_2024/20240107T2200Z"
    assert pid.period == Interval(start=SECOND_WEEK, end=T("2024-01-14T22:00:00Z"))
    assert _partition_from_payload(_partition_payload(pid)) == pid


def test_existing_partition_ids_keep_their_directory_and_payload() -> None:
    pid = PartitionId(series=H1, access_class=AccessClass.RESEARCH_HISTORY)
    assert pid.directory == "USDJPY_1h_bid/RESEARCH_HISTORY"
    assert "period_start" not in _partition_payload(pid)
    assert pid.period is None


def test_the_week_start_is_required_exactly_for_the_new_sealed_classes() -> None:
    with pytest.raises(MarketDataValueError, match="trading-week start"):
        PartitionId(series=M1, access_class=AccessClass.FORWARD_HOLDOUT)
    with pytest.raises(MarketDataValueError, match="only the OANDA MT5 sealed classes"):
        PartitionId(series=M1, access_class=AccessClass.LEGACY_HOLDOUT, period_start=SECOND_WEEK)
    with pytest.raises(MarketDataValueError, match="not the start of a sealed partition"):
        _pid(start=T("2024-01-08T00:00:00Z"))


# --- 初期状態（ADR-0038 第3項）--------------------------------------------------------


def test_a_new_sealed_partition_starts_sealed() -> None:
    decided = initial_sealed_state(_pid(), violations=(), existing=())
    assert decided.state is HoldoutState.SEALED
    assert decided.reasons == frozenset()


def test_a_violation_on_the_same_series_and_period_starts_it_consumed() -> None:
    scope = ViolationScope(
        series=M1, symbol=None, period=Interval(SECOND_WEEK, SECOND_WEEK + timedelta(hours=1))
    )
    decided = initial_sealed_state(_pid(), violations=(scope,), existing=())
    assert decided.state is HoldoutState.CONSUMED
    assert decided.reasons == {InitialStateReason.VIOLATION_JUDGMENT}


def test_a_violation_elsewhere_does_not_touch_the_partition() -> None:
    other_series = ViolationScope(series=EURUSD_M1, symbol=None, period=None)
    other_period = ViolationScope(series=M1, symbol=None, period=Interval(FIRST_WEEK, SECOND_WEEK))
    decided = initial_sealed_state(_pid(), violations=(other_series, other_period), existing=())
    assert decided.state is HoldoutState.SEALED


def test_a_raw_file_violation_with_an_unknown_period_covers_the_whole_symbol() -> None:
    whole = ViolationScope(series=None, symbol=Symbol("USDJPY"), period=None)
    for pid in (
        _pid(),
        _pid(series=H1),
        _pid(cls=AccessClass.FORWARD_HOLDOUT, start=ACQUISITION_START),
    ):
        assert initial_sealed_state(pid, violations=(whole,), existing=()).state is (
            HoldoutState.CONSUMED
        )
    assert (
        initial_sealed_state(_pid(series=EURUSD_M1), violations=(whole,), existing=()).state
        is HoldoutState.SEALED
    )


def test_an_existing_consumed_partition_keeps_the_period_consumed() -> None:
    existing = (ExistingSealedPartition(partition_id=_pid(), state=HoldoutState.CONSUMED),)
    decided = initial_sealed_state(_pid(), violations=(), existing=existing)
    assert decided.state is HoldoutState.CONSUMED
    assert decided.reasons == {InitialStateReason.EXISTING_CONSUMED}
    sealed = (ExistingSealedPartition(partition_id=_pid(), state=HoldoutState.SEALED),)
    assert initial_sealed_state(_pid(), violations=(), existing=sealed).state is HoldoutState.SEALED


def test_existing_partitions_of_other_weeks_series_or_legacy_snapshots_are_not_counted() -> None:
    legacy = PartitionId(series=M1, access_class=AccessClass.LEGACY_HOLDOUT)
    existing = (
        ExistingSealedPartition(partition_id=_pid(start=FIRST_WEEK), state=HoldoutState.CONSUMED),
        ExistingSealedPartition(partition_id=_pid(series=H1), state=HoldoutState.CONSUMED),
        # 旧データの snapshot の LEGACY_HOLDOUT（CONSUMED）は数えない（ADR-0038 第3項の 3）。
        ExistingSealedPartition(partition_id=legacy, state=HoldoutState.CONSUMED),
        ExistingSealedPartition(partition_id=legacy, state=None),
    )
    assert initial_sealed_state(_pid(), violations=(), existing=existing).state is (
        HoldoutState.SEALED
    )


def test_an_underivable_overlapping_state_stops_the_acceptance() -> None:
    existing = (ExistingSealedPartition(partition_id=_pid(), state=None),)
    with pytest.raises(HoldoutStateUnknown):
        initial_sealed_state(_pid(), violations=(), existing=existing)


def test_the_new_rule_applies_only_to_the_new_sealed_classes() -> None:
    legacy = PartitionId(series=M1, access_class=AccessClass.LEGACY_HOLDOUT)
    with pytest.raises(MarketDataValueError, match="ADR-0014"):
        initial_sealed_state(legacy, violations=(), existing=())


# --- 状態の導出（ADR-0038 第3項の 1・第8項）-------------------------------------------


def _record(pid: PartitionId) -> PartitionRecord:
    period = pid.period
    assert period is not None
    return PartitionRecord(
        partition_id=pid, interval=period, bar_count=1, digest=ContentDigest.sha256("0" * 64)
    )


def _entry(pid: PartitionId, kind: AccessLogEntryKind) -> AccessLogEntry:
    return AccessLogEntry(
        kind=kind, partition_id=pid, recorded_at=T("2030-01-01T00:00:00Z"), actor="t"
    )


def test_the_state_moves_to_consumed_only_by_a_consumption_record() -> None:
    record = _record(_pid())
    assert derive_sealed_state(record, HoldoutState.SEALED, ()) is HoldoutState.SEALED
    granted = (_entry(_pid(), AccessLogEntryKind.GRANTED),)
    assert derive_sealed_state(record, HoldoutState.SEALED, granted) is HoldoutState.SEALED
    other_week = (_entry(_pid(start=FIRST_WEEK), AccessLogEntryKind.CONSUMED),)
    assert derive_sealed_state(record, HoldoutState.SEALED, other_week) is HoldoutState.SEALED
    consumed = (*granted, _entry(_pid(), AccessLogEntryKind.CONSUMED))
    assert derive_sealed_state(record, HoldoutState.SEALED, consumed) is HoldoutState.CONSUMED


def test_a_partition_that_starts_consumed_never_returns_to_sealed() -> None:
    assert derive_sealed_state(_record(_pid()), HoldoutState.CONSUMED, ()) is HoldoutState.CONSUMED


def test_the_legacy_derivation_still_refuses_the_new_classes() -> None:
    with pytest.raises(MarketDataValueError, match="LEGACY_HOLDOUT"):
        derive_holdout_state(_record(_pid()), (), ())
    with pytest.raises(MarketDataValueError, match="ADR-0038"):
        derive_sealed_state(
            PartitionRecord(
                partition_id=PartitionId(series=M1, access_class=AccessClass.LEGACY_HOLDOUT),
                interval=Interval(FIRST_WEEK, SECOND_WEEK),
                bar_count=1,
                digest=ContentDigest.sha256("0" * 64),
            ),
            HoldoutState.SEALED,
            (),
        )
