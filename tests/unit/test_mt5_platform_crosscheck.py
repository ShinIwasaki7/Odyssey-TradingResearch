"""実データの突き合わせの道具の比べ方（D12 §11.13 の 15。第 2 巡のレビューの指摘）。"""

from __future__ import annotations

from odyssey_fx.common.time import Interval, UtcTime
from tools.survey.mt5_platform_crosscheck import _PlatformDay, compare_days


def _tool(start: str, end: str, recorded: int) -> dict[str, str]:
    return {
        "trading_day": end[:10],
        "interval_start_utc": start,
        "interval_end_utc": end,
        "expected_minutes": "1440",
        "recorded_minutes": str(recorded),
        "no_record_minutes": str(1440 - recorded),
        "shortfall_minutes": "0",
        "first_recorded_utc": start if recorded else "",
    }


def test_a_day_only_on_the_tool_side_is_a_mismatch() -> None:
    """道具の側にだけある取引日も出力し、不一致にする（両方向の比較）。"""
    platform = {
        "2018-12-26T22:00Z": _PlatformDay(
            interval=Interval(
                start=UtcTime.parse("2018-12-26T22:00:00Z"),
                end=UtcTime.parse("2018-12-27T22:00:00Z"),
            ),
            bar_class="fully_observed",
            expected=1440,
            recorded=1440,
            shortfall=0,
            first_observed="2018-12-26T22:00Z",
        ),
        "2018-12-24T22:00Z": _PlatformDay(
            interval=Interval(
                start=UtcTime.parse("2018-12-24T22:00:00Z"),
                end=UtcTime.parse("2018-12-25T22:00:00Z"),
            ),
            bar_class="no_record",
            expected=1440,
            recorded=0,
            shortfall=0,
            first_observed="",
        ),
    }
    rows = compare_days(
        "USDJPY",
        platform,
        [
            _tool("2018-12-24T22:00Z", "2018-12-25T22:00Z", 0),
            _tool("2018-12-25T22:00Z", "2018-12-26T22:00Z", 1430),
            _tool("2018-12-26T22:00Z", "2018-12-27T22:00Z", 1440),
        ],
    )
    assert [(row["interval_start_utc"], row["match"]) for row in rows] == [
        ("2018-12-24T22:00Z", True),
        ("2018-12-25T22:00Z", False),
        ("2018-12-26T22:00Z", True),
    ]


def test_a_tool_day_with_no_expected_minutes_matches_a_closure() -> None:
    """道具の行で存在すべき分が 0 の日（休場の定義で全分が外れた日）は、
    基盤に日足が無いことと一致する。"""
    platform = {
        "2018-12-25T22:00Z": _PlatformDay(
            interval=Interval(
                start=UtcTime.parse("2018-12-25T22:00:00Z"),
                end=UtcTime.parse("2018-12-26T22:00:00Z"),
            ),
            bar_class="partial",
            expected=1430,
            recorded=1430 - 5,
            shortfall=0,
            first_observed="2018-12-25T22:10Z",
        ),
        "2018-12-23T22:00Z": _PlatformDay(
            interval=Interval(
                start=UtcTime.parse("2018-12-23T22:00:00Z"),
                end=UtcTime.parse("2018-12-24T22:00:00Z"),
            ),
            bar_class="partial",
            expected=1440,
            recorded=1439,
            shortfall=0,
            first_observed="2018-12-23T22:00Z",
        ),
    }
    closed = _tool("2018-12-24T22:00Z", "2018-12-25T08:00Z", 0)
    closed["expected_minutes"] = "0"
    closed["no_record_minutes"] = "0"
    rows = compare_days("USDJPY", platform, [closed])
    matched = [row for row in rows if row["interval_start_utc"] == "2018-12-24T22:00Z"]
    assert matched[0]["platform_class"] == "closed"
    assert matched[0]["match"] is True
