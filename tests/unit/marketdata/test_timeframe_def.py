"""時間足定義と DST 境界の単体テスト（D03 §3.2・§11）。

2026 年の米国夏時間: 3月8日（日）に始まり、11月1日（日）に終わる。切替はニューヨーク現地
02:00 で、春は 02:00〜02:59 が存在せず、秋は 01:00〜01:59 が2度現れる。

確かめること:

- 固定 UTC 整列（15m・1h）は常に名目長どおり。
- 現地起点の整列（4h・1d）は DST 切替日に伸縮し、境界は UTC の固定値と一致する。
- 切替日の日足は 23 時間または 25 時間、4h 足は 3 時間または 5 時間になる。
- 秋の重複は最初の出現、春の不存在は次に存在する瞬間を起点にする。
"""

from __future__ import annotations

from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.timeframe_def import (
    FixedUtcAlignment,
    SessionAlignment,
    TimeframeDefinition,
)
from tests.fixtures.synthetic import market

NEW_YORK = ZoneInfo("America/New_York")


# --- 固定 UTC 整列 -----------------------------------------------------------


def test_fixed_alignment_always_matches_the_nominal_length() -> None:
    for moment in (
        "2026-03-08T06:30:00Z",  # 春の切替日
        "2026-11-01T05:30:00Z",  # 秋の切替日
        "2026-07-04T12:00:00Z",
    ):
        interval = market.TF_1H.boundaries(UtcTime.parse(moment))
        assert interval.duration == timedelta(hours=1)


def test_fixed_alignment_snaps_to_the_epoch_multiple() -> None:
    interval = market.TF_15M.boundaries(UtcTime.parse("2026-03-08T07:37:12Z"))
    assert interval == Interval(
        start=UtcTime.parse("2026-03-08T07:30:00Z"),
        end=UtcTime.parse("2026-03-08T07:45:00Z"),
    )


def test_fixed_alignment_requires_the_step_to_equal_the_nominal_length() -> None:
    """固定 UTC 整列では足の長さが常に名目長なので、食い違う定義は構築時に拒否する。"""
    with pytest.raises(MarketDataValueError, match="must equal"):
        TimeframeDefinition(
            ref=TimeframeRef("1h", 1),
            nominal_length=timedelta(hours=1),
            alignment=FixedUtcAlignment(step=timedelta(minutes=30)),
        )


# --- 現地起点の整列（通常日）------------------------------------------------


def test_daily_boundary_is_22z_in_winter_and_21z_in_summer() -> None:
    """ニューヨーク 17 時は冬時間 22:00Z、夏時間 21:00Z に対応する（上位設計書 §4.3.13）。"""
    winter = market.TF_1D_NY17.boundaries(UtcTime.parse("2026-01-15T00:00:00Z"))
    assert winter == Interval(
        start=UtcTime.parse("2026-01-14T22:00:00Z"),
        end=UtcTime.parse("2026-01-15T22:00:00Z"),
    )
    summer = market.TF_1D_NY17.boundaries(UtcTime.parse("2026-07-15T00:00:00Z"))
    assert summer == Interval(
        start=UtcTime.parse("2026-07-14T21:00:00Z"),
        end=UtcTime.parse("2026-07-15T21:00:00Z"),
    )


# --- DST 切替日（日足）------------------------------------------------------


def test_daily_bar_is_23_hours_on_the_spring_forward_day() -> None:
    """春の切替日（2026-03-08）を含む日足は 23 時間になる（D03 §3.2）。"""
    interval = market.TF_1D_NY17.boundaries(UtcTime.parse("2026-03-08T06:00:00Z"))
    assert interval == Interval(
        start=UtcTime.parse("2026-03-07T22:00:00Z"),
        end=UtcTime.parse("2026-03-08T21:00:00Z"),
    )
    assert interval.duration == timedelta(hours=23)


def test_daily_bar_is_25_hours_on_the_fall_back_day() -> None:
    """秋の切替日（2026-11-01）を含む日足は 25 時間になる（D03 §3.2）。"""
    interval = market.TF_1D_NY17.boundaries(UtcTime.parse("2026-11-01T06:00:00Z"))
    assert interval == Interval(
        start=UtcTime.parse("2026-10-31T21:00:00Z"),
        end=UtcTime.parse("2026-11-01T22:00:00Z"),
    )
    assert interval.duration == timedelta(hours=25)


# --- DST 切替日（4時間足）---------------------------------------------------


def test_four_hour_bar_is_three_hours_on_the_spring_forward_day() -> None:
    """春の切替に掛かる 4時間足は 3 時間になる（現地 01:00 起点 → 05:00 起点）。

    現地 01:00 は EST（UTC-5）で 06:00Z、現地 05:00 は EDT（UTC-4）で 09:00Z。
    """
    interval = market.TF_4H_NY17.boundaries(UtcTime.parse("2026-03-08T07:00:00Z"))
    assert interval == Interval(
        start=UtcTime.parse("2026-03-08T06:00:00Z"),
        end=UtcTime.parse("2026-03-08T09:00:00Z"),
    )
    assert interval.duration == timedelta(hours=3)


def test_four_hour_bar_is_five_hours_on_the_fall_back_day() -> None:
    """秋の切替に掛かる 4時間足は 5 時間になる（現地 01:00 起点 → 05:00 起点）。

    現地 01:00 は最初の出現（EDT、UTC-4）で 05:00Z、現地 05:00 は EST（UTC-5）で 10:00Z。
    """
    interval = market.TF_4H_NY17.boundaries(UtcTime.parse("2026-11-01T06:00:00Z"))
    assert interval == Interval(
        start=UtcTime.parse("2026-11-01T05:00:00Z"),
        end=UtcTime.parse("2026-11-01T10:00:00Z"),
    )
    assert interval.duration == timedelta(hours=5)


def test_the_four_hour_bars_of_the_spring_forward_day_tile_without_gaps() -> None:
    """切替週の 4時間足は隙間も重なりもなく並ぶ（D03 §11 の DST 境界照合）。"""
    boundaries = []
    probe = UtcTime.parse("2026-03-07T22:00:00Z")
    for _ in range(8):
        interval = market.TF_4H_NY17.boundaries(probe)
        boundaries.append(interval)
        probe = interval.end
    for earlier, later in zip(boundaries, boundaries[1:], strict=False):
        assert earlier.end == later.start
    assert [interval.duration for interval in boundaries[:6]] == [
        timedelta(hours=4),
        timedelta(hours=4),
        timedelta(hours=3),
        timedelta(hours=4),
        timedelta(hours=4),
        timedelta(hours=4),
    ]


# --- 起点の解決規則（D03 §3.2）----------------------------------------------


def test_the_ambiguous_local_anchor_uses_the_first_occurrence() -> None:
    """秋の切り戻しで2度現れる現地 01:00 は、最初の出現（EDT、05:00Z）を起点にする。"""
    alignment = SessionAlignment(tz=NEW_YORK, anchors_local=(time(1, 0),))
    interval = alignment.boundaries(UtcTime.parse("2026-11-01T06:00:00Z"))
    assert interval.start == UtcTime.parse("2026-11-01T05:00:00Z")


def test_the_nonexistent_local_anchor_moves_to_the_next_existing_instant() -> None:
    """春の切り替えで存在しない現地 02:30 は、次に存在する瞬間（03:00 EDT）を起点にする。"""
    alignment = SessionAlignment(tz=NEW_YORK, anchors_local=(time(2, 30),))
    interval = alignment.boundaries(UtcTime.parse("2026-03-08T08:00:00Z"))
    assert interval.start == UtcTime.parse("2026-03-08T07:00:00Z")


def test_session_alignment_normalizes_the_anchor_order() -> None:
    """起点の宣言順は境界列に影響しない（同じ定義を別の順で書いても同じ結果）。"""
    ascending = SessionAlignment(tz=NEW_YORK, anchors_local=(time(1, 0), time(17, 0)))
    descending = SessionAlignment(tz=NEW_YORK, anchors_local=(time(17, 0), time(1, 0)))
    assert ascending == descending


def test_session_alignment_rejects_repeated_anchors() -> None:
    with pytest.raises(MarketDataValueError, match="must not repeat"):
        SessionAlignment(tz=NEW_YORK, anchors_local=(time(17, 0), time(17, 0)))


def test_session_alignment_rejects_aware_anchor_times() -> None:
    with pytest.raises(MarketDataValueError, match="naive local times"):
        SessionAlignment(tz=NEW_YORK, anchors_local=(time(17, 0, tzinfo=NEW_YORK),))
