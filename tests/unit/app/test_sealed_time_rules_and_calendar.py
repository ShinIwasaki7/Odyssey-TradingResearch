"""2024 年以降の時刻の規則の表とカレンダー。

表は `oanda_mt5_time_rules_sealed` 版 1、カレンダーは `fx_ny17_oanda` 版 2（D03 §16.2）。版管理の
ファイルを読むだけで、市場データは読まない。確かめること: 承認済みの一覧と照合して読める
（T1〜T5・T4）、表の境目が公開の暦の夏時間の切替の日曜と一致する、カレンダー 版 2 は 2023 年以前が
版 1 と同じで 2024 年以降の違いは告知で確かめた 2 日だけ、カレンダーが「fx_ny17 版 2 −
休場の定義」と一致する。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from odyssey_fx.app.config.calendars import load_calendar
from odyssey_fx.app.mt5 import SEALED_COUNTING_RULES, check_calendar_derivation, load_mt5_rules
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant

ROOT = Path(__file__).resolve().parents[3]
EIGHT = {"USDJPY", "USDCHF", "AUDJPY", "EURUSD", "EURJPY", "GBPJPY", "GBPUSD", "AUDUSD"}


def _switch_days(zone: str, year: int) -> list[date]:
    """その年に UTC との差が変わる日（現地の暦日）を zoneinfo から求める。"""
    tz = ZoneInfo(zone)
    days: list[date] = []
    moment = datetime(year, 1, 1, 12, tzinfo=tz)
    previous = moment.utcoffset()
    while moment.year == year:
        moment += timedelta(days=1)
        offset = moment.utcoffset()
        if offset != previous:
            days.append(moment.date())
            previous = offset
    return days


def test_the_sealed_rules_load_against_the_approved_list() -> None:
    rules = load_mt5_rules(ROOT, SEALED_COUNTING_RULES)
    assert (rules.table.table_id, rules.table.version) == ("oanda_mt5_time_rules_sealed", 1)
    assert (rules.calendar.id, rules.calendar.version) == ("fx_ny17_oanda", 2)
    assert set(rules.table.pairs) == EIGHT
    assert (rules.table.coverage_start, rules.table.coverage_end) == (
        date(2024, 1, 1),
        date(2028, 1, 1),
    )
    # 研究期間の規則と同じ値（上位足・2 組）。
    research = load_mt5_rules(ROOT)
    assert rules.targets == research.targets
    assert rules.counting.variants == research.counting.variants


def test_every_row_is_assumed_and_the_gap_weeks_carry_a_european_candidate() -> None:
    rules = load_mt5_rules(ROOT, SEALED_COUNTING_RULES)
    offsets = {
        row.row: (row.standard_offset_hours, row.candidate_offset_hours) for row in rules.table.rows
    }
    assert offsets == {1: (2, ()), 2: (3, (2,)), 3: (3, ()), 4: (3, (2,))}
    assert all(row.mark.value != "confirmed" for row in rules.table.rows)


def test_the_table_boundaries_are_the_public_daylight_saving_sundays() -> None:
    rules = load_mt5_rules(ROOT, SEALED_COUNTING_RULES)
    expected: set[date] = set()
    for year in range(2024, 2028):
        expected.update(_switch_days("America/New_York", year))
        # 欧州の切替は 01:00Z。現地の日付は日曜（ロンドンで確かめる）。
        expected.update(_switch_days("Europe/London", year))
    assert all(day.weekday() == 6 for day in expected)
    assert set(rules.table.boundaries()) == expected


def test_a_trading_minute_maps_to_the_same_row_by_api_or_utc_date() -> None:
    # load_mt5_rules が T4 を通すことに加え、2024-01-01 の始まりの変換を確かめる。
    rules = load_mt5_rules(ROOT, SEALED_COUNTING_RULES)
    row = rules.table.row_for("USDJPY", date(2024, 1, 1))
    assert row is not None and row.offset_hours(TimeRuleVariant.STANDARD) == 2


def test_calendar_v2_equals_v1_before_2024_and_differs_only_on_the_two_announced_days() -> None:
    v1 = load_calendar(ROOT / "configs/calendars/fx_ny17_oanda_v1.yaml")
    v2 = load_calendar(ROOT / "configs/calendars/fx_ny17_oanda_v2.yaml")
    research = Interval(
        start=UtcTime.parse("2017-01-01T00:00:00Z"), end=UtcTime.parse("2024-01-01T00:00:00Z")
    )
    assert v1.sessions(research) == v2.sessions(research)
    sealed = Interval(
        start=UtcTime.parse("2024-01-01T00:00:00Z"), end=UtcTime.parse("2028-01-01T00:00:00Z")
    )

    def minutes(sessions: object) -> set[int]:
        out: set[int] = set()
        for session in sessions:  # type: ignore[attr-defined]
            out.update(
                range(int(session.start.value.timestamp()), int(session.end.value.timestamp()), 60)
            )
        return out

    removed = minutes(v1.sessions(sealed)) - minutes(v2.sessions(sealed))
    assert minutes(v2.sessions(sealed)) <= minutes(v1.sessions(sealed))
    days = {
        datetime.fromtimestamp(minute, ZoneInfo("America/New_York")).date() for minute in removed
    }
    # 取引日 D の休場は NY の D-1 17:00〜D 03:00 を新たに閉じる（D 03:00〜17:00 は版 1 で閉じた）
    assert days == {date(2024, 12, 24), date(2024, 12, 25), date(2025, 12, 24), date(2025, 12, 25)}


def test_calendar_v2_is_fx_ny17_v2_minus_the_announced_closures() -> None:
    rules = load_mt5_rules(ROOT, SEALED_COUNTING_RULES)
    assert check_calendar_derivation(ROOT, rules) > 0
