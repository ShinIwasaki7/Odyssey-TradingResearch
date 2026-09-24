"""段階4 の実データ分類（第 1 弾）で使うカレンダー新版と分類ファイルの単体テスト。

実データ（`data/raw/`・snapshot の実体）には依存しない。`configs/` にある実物の 2 ファイルを
読み、次を確かめる（D03 §3.4.1・§4 の 9・§9・§10）。

- カレンダー新版 `fx_ny17_v2.yaml` は、初版から**版と休場の宣言だけ**を変えたものである
  （週の開閉・時間帯・営業例外は変えない）。休場の形は人間の決定（2026-09-24〜25）どおり、
  元日と 2017 年のクリスマスが取引日単位の休場、クリスマスの部分日が短縮セッションである。
- 分類ファイルは形式版 2 として読め、`calendar` がそのカレンダー新版を指す。
- 休場（`CLOSURE`）の分類はどれも、カレンダー新版の休場の区間とちょうど 1 つ重なり、
  宣言した休場にはすべて全系列の分類がある。休場の宣言の無い区間を休場と分類していない。
- セッション外データ異常（`OUT_OF_SESSION_DATA`）の分類は、カレンダー新版でも市場が
  閉じている時間帯（週の開場前）だけを覆う。
- 原系列（15分足・1時間足）を含むデータ欠損（`DATA_GAP`）の分類は、休場の区間を丸ごとは含まない。
"""

from __future__ import annotations

import dataclasses
from datetime import date, time
from pathlib import Path

import pytest

from odyssey_fx.app.config import (
    ClassificationDecisionFile,
    load_calendar,
    load_classification_decisions,
    load_timeframes,
)
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.classification import ClassificationOutcome
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

REPO_ROOT = Path(__file__).resolve().parents[3]
CALENDARS = REPO_ROOT / "configs/calendars"
CALENDAR_V1 = CALENDARS / "fx_ny17_v1.yaml"
CALENDAR_V2 = CALENDARS / "fx_ny17_v2.yaml"
CLASSIFICATION = CALENDARS / "classification_fx_ny17_v2.yaml"
TIMEFRAMES = CALENDARS / "timeframes_v1.yaml"
SYMBOLS_DIR = REPO_ROOT / "configs/symbols"

#: 元日を取引日単位の休場とした年（元日が平日で、データが元日を含む年）。
NEW_YEAR_TRADING_DAY_YEARS = (2018, 2019, 2020, 2021, 2024, 2025, 2026)
#: クリスマスを取引日単位の休場とした年（終日の欠落だった年）。
CHRISTMAS_TRADING_DAY_YEARS = (2017,)
#: クリスマスを短縮セッションとした年（部分日の欠落だった年）。
CHRISTMAS_PARTIAL_YEARS = (2018, 2019, 2020, 2023, 2024, 2025)


def _calendar_v2() -> TradingCalendar:
    return load_calendar(CALENDAR_V2)


def _all_series() -> list[SeriesId]:
    """受入れが作る系列（10 銘柄 × 時間足 4 種 × bid）。snapshot の実体を読まずに組み立てる。"""
    timeframes = load_timeframes(TIMEFRAMES)
    symbols = sorted(path.stem for path in SYMBOLS_DIR.glob("*.yaml"))
    return [
        SeriesId(symbol=Symbol(symbol), timeframe=definition.ref, basis=PriceBasis.BID)
        for symbol in symbols
        for definition in timeframes.values()
    ]


def _closure_intervals(calendar: TradingCalendar) -> list[tuple[UtcTime, UtcTime]]:
    spans = []
    for closure in calendar.closures:
        interval = closure.utc_interval(
            calendar.tz, trading_day_boundary=calendar.trading_day_boundary
        )
        spans.append((interval.start, interval.end))
    return spans


# --- カレンダー新版 ----------------------------------------------------------


def test_the_new_calendar_changes_only_the_version_and_the_closures() -> None:
    """新版は初版と同じ識別・週の開閉・営業例外で、版と休場だけが違う（D03 §3.4・§9）。"""
    v1 = load_calendar(CALENDAR_V1)
    v2 = _calendar_v2()
    assert v2.id == v1.id == "fx_ny17"
    assert v2.version == 2
    assert v2.openings == ()
    assert dataclasses.replace(v1, version=2, closures=v2.closures) == v2


def test_the_new_calendar_declares_the_decided_closure_forms() -> None:
    """休場の形が人間の決定どおりである（D03 §3.4.1、2026-09-24〜25 の決定 1・2）。"""
    closures = {(c.local_date, c.covers_trading_day): c for c in _calendar_v2().closures}
    trading_days = {d for d, trading_day in closures if trading_day}
    assert trading_days == {date(y, 1, 1) for y in NEW_YEAR_TRADING_DAY_YEARS} | {
        date(y, 12, 25) for y in CHRISTMAS_TRADING_DAY_YEARS
    }
    partial = {d: c for (d, trading_day), c in closures.items() if not trading_day}
    assert set(partial) == {date(y, 12, 25) for y in CHRISTMAS_PARTIAL_YEARS}
    for closure in partial.values():
        assert not closure.covers_whole_day
        # 部分日はどの年も当日 17:00 に閉じ終わる（翌取引日の始まりと接する）。
        assert closure.end == time(17, 0)
    # 終日休場（現地 0〜24 時）は使わない（当日 17 時以降の足が休場帯の足になるため）。
    assert not any(c.covers_whole_day for c in _calendar_v2().closures)


def test_a_new_year_closure_is_the_trading_day_ending_on_new_year() -> None:
    """元日の休場は前日 17:00〜当日 17:00（ニューヨーク時刻）を閉じる（D03 §3.4.1）。"""
    calendar = _calendar_v2()
    before = UtcTime.parse("2018-12-31T21:45:00Z")  # 12-31 16:45 NY（開いている）
    first_closed = UtcTime.parse("2018-12-31T22:00:00Z")  # 12-31 17:00 NY
    last_closed = UtcTime.parse("2019-01-01T21:45:00Z")  # 01-01 16:45 NY
    reopened = UtcTime.parse("2019-01-01T22:00:00Z")  # 01-01 17:00 NY
    assert calendar.is_open(before)
    assert not calendar.is_open(first_closed)
    assert not calendar.is_open(last_closed)
    assert calendar.is_open(reopened)


# --- 分類ファイル ------------------------------------------------------------


@pytest.fixture
def decisions(monkeypatch: pytest.MonkeyPatch) -> ClassificationDecisionFile:
    # 分類ファイルの `calendar` はリポジトリの根からの相対パスで書く（CLI の実行位置）。
    monkeypatch.chdir(REPO_ROOT)
    return load_classification_decisions(CLASSIFICATION, _all_series())


def test_the_classification_file_points_at_the_new_calendar(
    decisions: ClassificationDecisionFile,
) -> None:
    """分類ファイルが読め、カレンダー新版を指す（D03 §10）。"""
    assert decisions.calendar == _calendar_v2()
    assert decisions.decisions


def test_each_closure_decision_matches_exactly_one_declared_closure(
    decisions: ClassificationDecisionFile,
) -> None:
    """休場の分類はどれも、カレンダー新版の休場の区間とちょうど 1 つ重なる。

    全系列（`all`）の分類は休場の区間そのものを含む。系列を限った分類（休場の区間と一部だけ
    重なる上位足）は、その休場の区間と重なる。休場を宣言していない区間を休場と分類すると、
    確定時に「最終報告に警告が残る」として失敗する。ここではそれを実データなしで先に見つける。
    逆に、宣言した休場にはすべて、全系列の分類が 1 件ずつある。
    """
    spans = _closure_intervals(_calendar_v2())
    whole: list[int] = []
    for decision in decisions.decisions:
        if decision.outcome is not ClassificationOutcome.CLOSURE:
            continue
        assert decision.kind is CheckKind.MISSING_EXPECTED_BAR
        start, end = decision.interval.start, decision.interval.end
        overlapping = [i for i, (a, b) in enumerate(spans) if a < end and start < b]
        assert len(overlapping) == 1, decision
        if set(decision.series) == set(_all_series()):
            (index,) = overlapping
            assert spans[index] == (start, end), decision
            whole.append(index)
    assert sorted(whole) == list(range(len(spans)))


def test_out_of_session_decisions_cover_only_closed_hours(
    decisions: ClassificationDecisionFile,
) -> None:
    """セッション外データ異常の分類は、新版でも市場が閉じている時間帯だけを覆う。"""
    calendar = _calendar_v2()
    found = 0
    for decision in decisions.decisions:
        if decision.outcome is not ClassificationOutcome.OUT_OF_SESSION_DATA:
            continue
        found += 1
        assert decision.kind is CheckKind.UNEXPECTED_BAR
        assert calendar.sessions(decision.interval) == ()
    assert found > 0


def test_data_gap_decisions_do_not_swallow_a_closure_of_a_source_series(
    decisions: ClassificationDecisionFile,
) -> None:
    """15分足・1時間足を含むデータ欠損の分類は、宣言した休場の区間を丸ごと含まない。

    原系列（15分足・1時間足）の足は休場の区間の中に丸ごと収まるので、休場の区間を含む
    データ欠損の分類は、休場と分類すべき警告まで欠損として覆ってしまう（確定時の競合）。
    上位足（4時間足・日足）は休場の区間をまたいで切り詰められ、なお欠けていればデータ欠損に
    なるので、ここでは対象にしない。
    """
    spans = _closure_intervals(_calendar_v2())
    for decision in decisions.decisions:
        if decision.outcome is not ClassificationOutcome.DATA_GAP:
            continue
        if not any(s.timeframe.id in ("15m", "1h") for s in decision.series):
            continue
        for start, end in spans:
            assert not (decision.interval.start <= start and end <= decision.interval.end), (
                decision,
                start,
                end,
            )
