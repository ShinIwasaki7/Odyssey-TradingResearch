"""読み取りの関門を1度だけ通して共有しても、検査の意味が変わらないこと（D03 §3.7.1・§6.1）。

1回の run では as-of ビュー・執行系列ビュー・公開フィード・公開記録が同じ snapshot・同じ
許可集合・同じ足を受け取る。関門を1度通した不変の写し（`VerifiedPartitionBars`）を共有しても、

- 照合に失敗する足では、同じ型・同じ文言の構造エラーになる（写しそのものが作られない）。
- 照合に通る足では、各経路の答えが「生の足を渡して各経路で照合した場合」と同じになる。
- 条件の合わない組み合わせ（別の snapshot・別の許可集合）で渡せば、ふつうの足と同じく
  全件を照合し直す（照合を素通りする経路を作らない）。
- 写しは後から変更できない。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.asof import AsOfView, BarsWindow, ExecutionSeriesView
from odyssey_fx.marketdata.application.publication import build_feed, build_publication_log
from odyssey_fx.marketdata.application.snapshot_access import (
    ReadableSnapshot,
    VerifiedPartitionBars,
    readable_surface,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.errors import (
    HoldoutAccessViolation,
    MarketDataValueError,
    PartitionContentMismatch,
)
from odyssey_fx.marketdata.domain.schedule import DelayScenario, FixedSeriesDelay, SeriesSchedule
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
DAILY = market.series(timeframe_id="1d_ny17")
CALENDAR = market.calendar()
WINDOW = Interval(
    start=UtcTime.parse("2026-01-12T22:00:00Z"), end=UtcTime.parse("2026-01-16T22:00:00Z")
)
SCHEDULES = {
    HOURLY: SeriesSchedule(series=HOURLY, timeframe_def=market.TF_1H, calendar=CALENDAR),
    DAILY: SeriesSchedule(series=DAILY, timeframe_def=market.TF_1D_NY17, calendar=CALENDAR),
}
HOURLY_PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
DAILY_PARTITION = PartitionId(series=DAILY, access_class=AccessClass.RESEARCH_HISTORY)
ALLOWED = frozenset({HOURLY_PARTITION, DAILY_PARTITION})
RUN = Interval(
    start=UtcTime.parse("2026-01-13T22:00:00Z"), end=UtcTime.parse("2026-01-15T22:00:00Z")
)


def _bars() -> dict[PartitionId, tuple[Bar, ...]]:
    return {
        HOURLY_PARTITION: market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW),
        DAILY_PARTITION: market.make_bars(DAILY, market.TF_1D_NY17, CALENDAR, WINDOW),
    }


def _tampered() -> dict[PartitionId, tuple[Bar, ...]]:
    """1本だけ出来高を差し替えた足（内容ダイジェストが合わない）。"""
    bars = _bars()
    hourly = list(bars[HOURLY_PARTITION])
    hourly[5] = market.make_bar(HOURLY, hourly[5].interval, volume="999")
    return {**bars, HOURLY_PARTITION: tuple(hourly)}


def _message(action: Callable[[], object]) -> tuple[type[BaseException], str]:
    with pytest.raises(MarketDataValueError) as caught:
        action()
    return type(caught.value), str(caught.value)


# --- 失敗は同じ型・同じ文言 --------------------------------------------------


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(_tampered, id="replaced-bar"),
        pytest.param(
            lambda: {**_bars(), HOURLY_PARTITION: _bars()[HOURLY_PARTITION][:-1]},
            id="removed-bar",
        ),
    ],
)
def test_the_shared_gate_fails_exactly_like_each_view(
    broken: Callable[[], Mapping[PartitionId, Sequence[Bar]]],
) -> None:
    snapshot = snapshots.readable_for(_bars())
    by_view = _message(
        lambda: AsOfView(
            snapshot=snapshot,
            allowed_partitions=ALLOWED,
            schedules=SCHEDULES,
            partition_bars=broken(),
        )
    )
    by_feed = _message(lambda: build_feed(snapshot, ALLOWED, broken(), SCHEDULES, RUN))
    shared = _message(lambda: VerifiedPartitionBars(snapshot, ALLOWED, broken(), label="AsOfView"))
    assert by_view[0] is PartitionContentMismatch
    assert shared == by_view == by_feed


def test_a_quarantined_partition_is_refused_by_the_shared_gate() -> None:
    quarantined = PartitionId(series=HOURLY, access_class=AccessClass.QUARANTINED_UNASSIGNED)
    snapshot = snapshots.readable_for(_bars())
    with pytest.raises(HoldoutAccessViolation, match="quarantined partitions"):
        VerifiedPartitionBars(snapshot, ALLOWED | {quarantined}, _bars(), label="AsOfView")


# --- 通る足では、どの経路の答えも同じ ------------------------------------------


def test_every_reader_answers_the_same_with_the_shared_bars() -> None:
    snapshot = snapshots.readable_for(_bars())
    shared = VerifiedPartitionBars(snapshot, ALLOWED, _bars(), label="AsOfView")
    scenario = DelayScenario(
        id="d1_2s", version=1, rules=(FixedSeriesDelay(series=DAILY, delay=timedelta(seconds=2)),)
    )

    raw_log = build_publication_log(snapshot, ALLOWED, _bars(), SCHEDULES, scenario)
    shared_log = build_publication_log(snapshot, ALLOWED, shared, SCHEDULES, scenario)
    assert shared_log == raw_log

    raw_feed = build_feed(
        snapshot,
        ALLOWED,
        _bars(),
        SCHEDULES,
        RUN,
        execution_series=frozenset({HOURLY}),
        publication_log=raw_log,
    )
    shared_feed = build_feed(
        snapshot,
        ALLOWED,
        shared,
        SCHEDULES,
        RUN,
        execution_series=frozenset({HOURLY}),
        publication_log=shared_log,
    )
    assert shared_feed.events == raw_feed.events

    raw_view = AsOfView(
        snapshot=snapshot,
        allowed_partitions=ALLOWED,
        schedules=SCHEDULES,
        partition_bars=_bars(),
        publication_log=raw_log,
    )
    shared_view = AsOfView(
        snapshot=snapshot,
        allowed_partitions=ALLOWED,
        schedules=SCHEDULES,
        partition_bars=shared,
        publication_log=shared_log,
    )
    assert shared_view == raw_view
    for at in (
        UtcTime.parse("2026-01-14T12:00:00Z"),
        UtcTime.parse("2026-01-15T22:00:01Z"),
        UtcTime.parse("2026-01-15T22:00:02Z"),
    ):
        for series in (HOURLY, DAILY):
            assert shared_view.latest_available(series, at) == raw_view.latest_available(series, at)
            assert shared_view.history(series, BarsWindow(2), at) == raw_view.history(
                series, BarsWindow(2), at
            )

    raw_execution = ExecutionSeriesView(
        snapshot=snapshot,
        series=HOURLY,
        allowed_partitions=ALLOWED,
        partition_bars=_bars(),
        schedule=SCHEDULES[HOURLY],
    )
    shared_execution = ExecutionSeriesView(
        snapshot=snapshot,
        series=HOURLY,
        allowed_partitions=ALLOWED,
        partition_bars=shared,
        schedule=SCHEDULES[HOURLY],
    )
    for bar in _bars()[HOURLY_PARTITION]:
        assert shared_execution.bar(bar.key) == raw_execution.bar(bar.key)
        assert shared_execution.next_bar_key_after(
            bar.bar_start
        ) == raw_execution.next_bar_key_after(bar.bar_start)
    missing = BarKey(series=HOURLY, bar_start=UtcTime.parse("2026-01-17T12:00:00Z"))
    assert shared_execution.bar(missing) is raw_execution.bar(missing) is None


# --- 条件が合わなければ全件を照合し直す --------------------------------------


def test_bars_verified_for_another_snapshot_are_checked_again() -> None:
    """別の snapshot の関門を通した写しでも、この snapshot の記録と照合する。"""
    shared = VerifiedPartitionBars(
        snapshots.readable_for(_tampered()), ALLOWED, _tampered(), label="AsOfView"
    )
    other: ReadableSnapshot = snapshots.readable_for(_bars())
    with pytest.raises(PartitionContentMismatch, match="does not match the digest"):
        AsOfView(
            snapshot=other, allowed_partitions=ALLOWED, schedules=SCHEDULES, partition_bars=shared
        )
    with pytest.raises(PartitionContentMismatch, match="does not match the digest"):
        build_feed(other, ALLOWED, shared, SCHEDULES, RUN)


def test_bars_verified_for_another_allowed_set_are_checked_again() -> None:
    """許可集合が違えば、写しに無い partition は足0本として照合され、拒否される。"""
    snapshot = snapshots.readable_for(_bars())
    hourly_only = frozenset({HOURLY_PARTITION})
    shared = VerifiedPartitionBars(
        snapshot, hourly_only, {HOURLY_PARTITION: _bars()[HOURLY_PARTITION]}, label="AsOfView"
    )
    with pytest.raises(PartitionContentMismatch, match="holds 0 bar\\(s\\)"):
        ExecutionSeriesView(
            snapshot=snapshot,
            series=HOURLY,
            allowed_partitions=ALLOWED,
            partition_bars=shared,
            schedule=SCHEDULES[HOURLY],
        )


# --- 写しは変更できない ------------------------------------------------------


def test_the_shared_bars_cannot_be_changed_after_the_gate() -> None:
    source = dict(_bars())
    snapshot = snapshots.readable_for(source)
    shared = VerifiedPartitionBars(snapshot, ALLOWED, source, label="AsOfView")
    source[HOURLY_PARTITION] = _tampered()[HOURLY_PARTITION]
    assert shared[HOURLY_PARTITION] == _bars()[HOURLY_PARTITION]
    assert not hasattr(shared, "__setitem__")
    with pytest.raises(AttributeError):
        shared.extra = 1


@pytest.mark.parametrize("name", ["_snapshot", "_allowed", "_bars", "_readable"])
def test_the_verified_pairing_cannot_be_rebound(name: str) -> None:
    """照合を通った組（snapshot・許可集合・足）は付け替えも削除もできない（D03 §6.1）。

    snapshot A で照合した写しの snapshot を B へ付け替えられると、B の照合を素通りする。
    """
    snapshot = snapshots.readable_for(_bars())
    shared = VerifiedPartitionBars(snapshot, ALLOWED, _bars(), label="AsOfView")
    other = snapshots.readable_for(_tampered())
    with pytest.raises(AttributeError):
        setattr(shared, name, other)
    with pytest.raises(AttributeError):
        delattr(shared, name)
    assert shared.verified_for(snapshot, ALLOWED)
    assert not shared.verified_for(other, ALLOWED)


def test_the_shared_read_surface_cannot_be_changed() -> None:
    """共有する読み取り面も、付け替え・中身の差し替えができない（D03 §6.1）。"""
    snapshot = snapshots.readable_for(_bars())
    shared = VerifiedPartitionBars(snapshot, ALLOWED, _bars(), label="AsOfView")
    surface = readable_surface(shared, ALLOWED)
    assert readable_surface(shared, ALLOWED) is surface
    with pytest.raises(AttributeError):
        surface._by_series = {}
    with pytest.raises(TypeError):
        surface._by_series[HOURLY] = ()  # type: ignore[index]
    assert surface.require_series(HOURLY) == _bars()[HOURLY_PARTITION]


def test_the_verified_copy_cannot_be_subclassed() -> None:
    """派生型で照合済みの判定を差し替える経路を塞ぐ（D03 §3.7.1）。"""
    with pytest.raises(TypeError, match="cannot be subclassed"):
        type("Forged", (VerifiedPartitionBars,), {"verified_for": lambda *_: True})
