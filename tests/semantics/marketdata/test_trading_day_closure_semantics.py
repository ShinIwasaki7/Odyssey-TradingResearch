"""取引日単位の休場の意味論テスト（D03 §3.4.1・§11「意味論」v1.9）。

D03 §11 の契約行: 取引日単位の休場で前日 17:00〜当日 17:00 の足が期待されず、当日 17:00
以降の足が休場帯の足にならない（同じ日の終日休場では休場帯の足になることとの対比）。

実データの元日の欠落は、全系列・全年で「前日 17:00〜当日 17:00（NY）」の形をしている。
この形の欠落を休場と分類したとき、

1. 取引日単位の休場で宣言すれば、再実行後の報告に警告が残らず確定できる。
2. 終日休場（現地 0〜24 時）で宣言すると、当日 17〜24 時に実在する足が「再実行で初めて
   現れた休場帯の足」になり、未分類として確定が止まる（v1.9 で本形を足した理由）。

を、受入れ（暫定）→ 分類 → 再実行 → 確定の経路で確かめる。
"""

from __future__ import annotations

from datetime import date

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.acceptance import (
    PendingSnapshot,
    RawFile,
    build_pending_snapshot,
    finalize,
    out_of_session_exclusions,
    reaccept_with_calendar,
)
from odyssey_fx.marketdata.application.integrity import SeriesUnderCheck, check_series
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import ClosureRule, TradingCalendar
from odyssey_fx.marketdata.domain.classification import (
    ClassificationDecision,
    ClassificationOutcome,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
#: 元日（2020-01-01、水曜）を含む週の月曜 17:00 NY〜金曜 17:00 NY。
WINDOW = Interval(
    start=UtcTime.parse("2019-12-30T22:00:00Z"), end=UtcTime.parse("2020-01-03T22:00:00Z")
)
NEW_YEAR = date(2020, 1, 1)
#: 取引日 2020-01-01 = 2019-12-31 17:00 NY 〜 2020-01-01 17:00 NY。
BAND = Interval(
    start=UtcTime.parse("2019-12-31T22:00:00Z"), end=UtcTime.parse("2020-01-01T22:00:00Z")
)
#: 当日 17:00〜24:00 NY（22:00Z〜翌 05:00Z）。終日休場なら閉じ、取引日単位なら開いている。
EVENING = Interval(
    start=UtcTime.parse("2020-01-01T22:00:00Z"), end=UtcTime.parse("2020-01-02T05:00:00Z")
)

TRADING_DAY = market.closure(NEW_YEAR, trading_day=True, note="元日")
WHOLE_DAY = ClosureRule(local_date=NEW_YEAR, covers_whole_day=True, note="元日")


def _real_like_bars() -> tuple[Bar, ...]:
    """実データと同じ形: 前日 17:00〜当日 17:00 の足だけが無い 1 時間足。"""
    return market.make_bars(HOURLY, market.TF_1H, market.calendar(closures=(TRADING_DAY,)), WINDOW)


def _check(calendar: TradingCalendar) -> dict[CheckKind, list[Interval]]:
    target = SeriesUnderCheck(series=HOURLY, timeframe_def=market.TF_1H, bars=_real_like_bars())
    found: dict[CheckKind, list[Interval]] = {}
    for result in check_series(target, calendar):
        found.setdefault(result.kind, []).append(result.interval)
    return found


# --- カレンダー照合 ---------------------------------------------------------


def test_the_bars_of_a_closed_trading_day_are_not_expected() -> None:
    """取引日単位の休場では、前日 17:00〜当日 17:00 の足は期待されない。"""
    calendar = market.calendar(closures=(TRADING_DAY,))
    starts = calendar.expected_bar_starts(market.TF_1H, WINDOW)
    assert not [start for start in starts if BAND.contains(start)]
    assert BAND.end in starts
    assert UtcTime.parse("2019-12-31T21:00:00Z") in starts
    # 実データと同じ形の系列には、欠落も休場帯の足も報告されない。
    found = _check(calendar)
    assert CheckKind.MISSING_EXPECTED_BAR not in found
    assert CheckKind.UNEXPECTED_BAR not in found


def test_the_bars_after_17_are_not_out_of_session_under_a_trading_day_closure() -> None:
    """当日 17:00 以降の足は休場帯の足にならない。終日休場では休場帯の足になる（対比）。"""
    assert CheckKind.UNEXPECTED_BAR not in _check(market.calendar(closures=(TRADING_DAY,)))

    unexpected = _check(market.calendar(closures=(WHOLE_DAY,)))[CheckKind.UNEXPECTED_BAR]
    assert len(unexpected) == 7
    assert all(EVENING.contains(interval.start) for interval in unexpected)


# --- 受入れ → 分類 → 再実行 → 確定 ------------------------------------------


def _pending() -> PendingSnapshot:
    source = snapshots.source("data/raw/market/USDJPY_1h_merged.csv")
    raw_file = RawFile(
        path=source.path,
        sha256=source.sha256,
        symbol=source.symbol,
        timeframe=source.timeframe,
        declared_basis=source.declared_basis,
    )
    return build_pending_snapshot(
        created_at=UtcTime.parse("2026-09-24T09:00:00Z"),
        raw_files=(raw_file,),
        bars_by_file={raw_file.path: _real_like_bars()},
        timeframe_defs=market.TIMEFRAME_DEFS,
        calendar=market.calendar(),
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        basis_declaration=snapshots.BASIS,
        conversion=snapshots.CONVERSION,
    )


def _closure_decision() -> ClassificationDecision:
    return ClassificationDecision(
        kind=CheckKind.MISSING_EXPECTED_BAR,
        interval=BAND,
        series=(HOURLY,),
        outcome=ClassificationOutcome.CLOSURE,
        note="元日",
    )


def _rerun(provisional: PendingSnapshot, closure: ClosureRule) -> PendingSnapshot:
    decisions = (_closure_decision(),)
    return reaccept_with_calendar(
        provisional,
        calendar=market.calendar(closures=(closure,), version=2),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        aggregation_targets=(),
        excluded=out_of_session_exclusions(provisional, decisions),
    )


def test_a_new_year_gap_classified_as_a_trading_day_closure_finalizes() -> None:
    """欠落を取引日単位の休場と宣言すれば、再実行後に警告が残らず確定できる。"""
    provisional = _pending()
    missing = [
        result
        for result in provisional.report.results
        if result.kind is CheckKind.MISSING_EXPECTED_BAR
    ]
    assert len(missing) == 24
    assert all(BAND.contains(result.interval.start) for result in missing)

    final = _rerun(provisional, TRADING_DAY)
    assert not final.report.warnings
    finalized = finalize(final, (_closure_decision(),), provisional=provisional)
    assert finalized.manifest.conversion.calendar_version == 2


def test_the_same_gap_declared_as_a_whole_day_closure_cannot_finalize() -> None:
    """終日休場で宣言すると当日 17〜24 時の足が新たな休場帯の足になり、確定が止まる。"""
    provisional = _pending()
    final = _rerun(provisional, WHOLE_DAY)
    new_unexpected = [
        result for result in final.report.results if result.kind is CheckKind.UNEXPECTED_BAR
    ]
    assert len(new_unexpected) == 7
    with pytest.raises(MarketDataValueError, match=r"7 warning\(s\) .* still unclassified"):
        finalize(final, (_closure_decision(),), provisional=provisional)
