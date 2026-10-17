"""実データの突き合わせの道具の比べ方（D12 §11.13 の 15）。道具の側は PR #85 の最終集計の形。"""

from __future__ import annotations

from odyssey_fx.common.time import Interval, UtcTime
from tools.survey.mt5_platform_crosscheck import _PlatformDay, compare_days, compare_no_record


def _tool(start: str, end: str, recorded: int, day_class: str = "partial") -> dict[str, str]:
    return {
        "trading_day": end[:10],
        "interval_start_utc": start,
        "interval_end_utc": end,
        "published_at_utc": end,
        "time_rule_row": "8",
        "time_rule_mark": "assumed_same",
        "expected_minutes": "1440",
        "recorded_minutes": str(recorded),
        "unrecorded_minutes": str(1440 - recorded),
        "shortfall_minutes": "0",
        "first_observed_utc": start if recorded else "",
        "last_observed_utc": "" if not recorded else end[:11] + "21:59Z",
        "day_class": day_class,
    }


def _day(start: str, end: str, bar_class: str, recorded: int) -> _PlatformDay:
    built = bar_class in ("fully_observed", "partial")
    return _PlatformDay(
        interval=Interval(
            start=UtcTime.parse(start[:-1] + ":00Z"), end=UtcTime.parse(end[:-1] + ":00Z")
        ),
        bar_class=bar_class,
        expected=1440,
        recorded=recorded,
        shortfall=0,
        first_observed=start if recorded else "",
        last_observed=end[:11] + "21:59Z" if built else "",
        published_at=end,
        time_rule_row="8" if built else "",
        time_rule_mark="assumed_same" if built else "",
    )


def test_a_day_only_on_the_tool_side_is_a_mismatch() -> None:
    """道具の側にだけある取引日も出力し、不一致にする（両方向の比較）。全分無記録の日は観測の時刻・
    表の行を持たない（比べない）。"""
    platform = {
        "2018-12-26T22:00Z": _day("2018-12-26T22:00Z", "2018-12-27T22:00Z", "fully_observed", 1440),
        "2018-12-24T22:00Z": _day("2018-12-24T22:00Z", "2018-12-25T22:00Z", "no_record", 0),
    }
    rows = compare_days(
        "USDJPY",
        "standard",
        platform,
        [
            _tool("2018-12-24T22:00Z", "2018-12-25T22:00Z", 0, "no_record"),
            _tool("2018-12-25T22:00Z", "2018-12-26T22:00Z", 1430),
            _tool("2018-12-26T22:00Z", "2018-12-27T22:00Z", 1440, "full"),
        ],
    )
    assert [(row["interval_start_utc"], row["match"]) for row in rows] == [
        ("2018-12-24T22:00Z", True),
        ("2018-12-25T22:00Z", False),
        ("2018-12-26T22:00Z", True),
    ]


def test_a_different_last_observation_or_table_row_is_a_mismatch() -> None:
    """最後の観測の時刻・時刻の規則の表の行も比べる（PR #85 の最終集計の列）。"""
    platform = {
        "2018-12-26T22:00Z": _day("2018-12-26T22:00Z", "2018-12-27T22:00Z", "partial", 1400),
    }
    tool = _tool("2018-12-26T22:00Z", "2018-12-27T22:00Z", 1400)
    tool["last_observed_utc"] = "2018-12-27T20:59Z"
    (row,) = compare_days("USDJPY", "candidate", platform, [tool])
    assert row["match"] is False
    tool = _tool("2018-12-26T22:00Z", "2018-12-27T22:00Z", 1400)
    tool["time_rule_row"] = "9"
    (row,) = compare_days("USDJPY", "candidate", platform, [tool])
    assert row["match"] is False
    (row,) = compare_days(
        "USDJPY", "candidate", platform, [_tool("2018-12-26T22:00Z", "2018-12-27T22:00Z", 1400)]
    )
    assert row["match"] is True


def test_no_record_bars_are_compared_both_ways_inside_the_scope() -> None:
    """全分無記録の足は時間足ごとに両方向で比べ、比べた範囲の外の道具の行は数えない。"""
    scope = Interval(
        start=UtcTime.parse("2022-12-25T22:00:00Z"), end=UtcTime.parse("2022-12-27T22:00:00Z")
    )
    rows = compare_no_record(
        "USDJPY",
        "standard",
        {"1h": {"2022-12-26T00:00Z", "2022-12-26T01:00Z"}},
        [
            {
                "timeframe": "1h",
                "interval_start_utc": "2022-12-26T00:00Z",
                "interval_end_utc": "2022-12-26T01:00Z",
            },
            {
                "timeframe": "1h",
                "interval_start_utc": "2023-01-02T00:00Z",
                "interval_end_utc": "2023-01-02T01:00Z",
            },
        ],
        scope,
    )
    assert [(row["interval_start_utc"], row["match"]) for row in rows] == [
        ("2022-12-26T00:00Z", True),
        ("2022-12-26T01:00Z", False),
    ]
