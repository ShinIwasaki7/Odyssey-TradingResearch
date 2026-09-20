"""完全性検査の単体テスト（D03 §3.9・§4 の 4〜5・§11）。

確かめること:

- 重複した開始時刻・整列に合わない開始時刻が重大な違反（受入れ失敗）になる。
- 存在すべき足の欠落・休場時間帯の足が警告として報告される。
- 報告の並びが検査の実行順に依存しない。
- 報告に価格の統計が含まれない（封印期間の情報漏れを構造的に防ぐ）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time, timedelta

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.integrity import (
    SeriesUnderCheck,
    check_all,
    check_series,
    severity_counts,
)
from odyssey_fx.marketdata.domain.bar import Bar, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import (
    CheckKind,
    CheckResult,
    IntegrityReport,
    Severity,
)
from tests.fixtures.synthetic import market

HOURLY = market.series()
CALENDAR = market.calendar()
WINDOW = Interval(
    start=UtcTime.parse("2026-01-13T22:00:00Z"), end=UtcTime.parse("2026-01-15T22:00:00Z")
)


def _target(bars: Sequence[Bar]) -> SeriesUnderCheck:
    return SeriesUnderCheck(series=HOURLY, timeframe_def=market.TF_1H, bars=tuple(bars))


def _kinds(results: Sequence[CheckResult]) -> set[CheckKind]:
    return {result.kind for result in results}


# --- 構造検査（D03 §4 の 4）-------------------------------------------------


def test_a_clean_series_produces_no_errors() -> None:
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    report = check_all([_target(bars)], CALENDAR)
    assert not report.has_errors()


def test_a_duplicate_bar_start_is_an_error() -> None:
    bars = list(market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW))
    bars.append(bars[0])
    results = check_series(_target(bars), CALENDAR)
    duplicates = [result for result in results if result.kind is CheckKind.DUPLICATE_TIMESTAMP]
    assert duplicates
    assert duplicates[0].severity is Severity.ERROR


def test_a_bar_start_off_the_alignment_is_an_error() -> None:
    """整列に合わない開始時刻は重大な違反（D03 §3.9 の IRREGULAR_INTERVAL）。"""
    offset = Interval(
        start=UtcTime.parse("2026-01-14T10:30:00Z"),
        end=UtcTime.parse("2026-01-14T11:00:00Z"),
    )
    results = check_series(_target([market.make_bar(HOURLY, offset)]), CALENDAR)
    irregular = [result for result in results if result.kind is CheckKind.IRREGULAR_INTERVAL]
    assert irregular
    assert irregular[0].severity is Severity.ERROR


# --- カレンダー照合（D03 §4 の 5）-------------------------------------------


def test_a_missing_expected_bar_is_a_warning() -> None:
    """人間が休場 / 欠損に分類する対象なので警告に留める（D03 §3.4）。"""
    dropped = UtcTime.parse("2026-01-14T10:00:00Z")
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=(dropped,))
    results = check_series(_target(bars), CALENDAR)
    missing = [result for result in results if result.kind is CheckKind.MISSING_EXPECTED_BAR]
    assert len(missing) == 1
    assert missing[0].severity is Severity.WARN
    assert missing[0].interval.start == dropped


def test_a_bar_inside_a_declared_closure_is_a_warning() -> None:
    """カレンダー修正の候補として報告する（D03 §3.9 の UNEXPECTED_BAR）。"""
    calendar = market.calendar(
        closures=(market.closure(date(2026, 1, 14), time(5, 0), time(7, 0)),)
    )
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    results = check_series(_target(bars), calendar)
    unexpected = [result for result in results if result.kind is CheckKind.UNEXPECTED_BAR]
    assert unexpected
    assert all(result.severity is Severity.WARN for result in unexpected)


def test_a_misaligned_symbol_is_reported_across_series() -> None:
    """同じ時間足で銘柄間の足境界がずれたら警告する（D03 §3.9）。"""
    other = market.series(symbol=market.EURUSD)
    reference_bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    shifted = Interval(
        start=UtcTime.parse("2026-01-14T10:30:00Z"),
        end=UtcTime.parse("2026-01-14T11:30:00Z"),
    )
    other_target = SeriesUnderCheck(
        series=other, timeframe_def=market.TF_1H, bars=(market.make_bar(other, shifted),)
    )
    report = check_all([_target(reference_bars), other_target], CALENDAR)
    assert CheckKind.CROSS_SYMBOL_MISALIGNMENT in _kinds(report.results)


# --- 出所の切替と 0 出来高（D03 §3.9 の INFO）-------------------------------


def _bar_at(hours: int, *, volume: str = "1000", source: ProvenanceKind) -> Bar:
    """検査対象の足を1本作る（出所と出来高を指定できる）。"""
    start = UtcTime.parse("2026-01-14T00:00:00Z") + timedelta(hours=hours)
    interval = Interval(start=start, end=start + timedelta(hours=1))
    return market.make_bar(
        HOURLY, interval, volume=volume, provenance_kind=source, source_ref="a.csv"
    )


def test_a_source_change_is_recorded_as_an_information_finding() -> None:
    """出所（`source` 列）の切替点を記録する（D03 §3.9 の SOURCE_TRANSITION）。"""
    bars = [
        _bar_at(0, source=ProvenanceKind.HISTDATA),
        _bar_at(1, source=ProvenanceKind.HISTDATA),
        _bar_at(2, source=ProvenanceKind.DUKASCOPY),
        _bar_at(3, source=ProvenanceKind.DUKASCOPY),
    ]
    transitions = [
        result
        for result in check_series(_target(bars), CALENDAR)
        if result.kind is CheckKind.SOURCE_TRANSITION
    ]
    assert len(transitions) == 1
    assert transitions[0].severity is Severity.INFO
    assert dict(transitions[0].detail) == {"from": "histdata", "to": "dukascopy"}
    # 区間は切替の直前の足の開始から、切替後の足の終了まで。
    assert transitions[0].interval.start == bars[1].bar_start
    assert transitions[0].interval.end == bars[2].bar_end


def test_a_single_source_produces_no_transition_finding() -> None:
    bars = [_bar_at(index, source=ProvenanceKind.HISTDATA) for index in range(4)]
    assert not [
        result
        for result in check_series(_target(bars), CALENDAR)
        if result.kind is CheckKind.SOURCE_TRANSITION
    ]


def test_consecutive_zero_volume_bars_are_reported_as_one_span() -> None:
    """0 出来高の連続区間は1件にまとめる（D03 §3.9 の ZERO_VOLUME_SPAN）。

    HistData 由来の出来高は 0 であり、真の市場出来高ではない（D03 §2）。
    """
    bars = [
        _bar_at(0, volume="1000", source=ProvenanceKind.DUKASCOPY),
        _bar_at(1, volume="0", source=ProvenanceKind.DUKASCOPY),
        _bar_at(2, volume="0", source=ProvenanceKind.DUKASCOPY),
        _bar_at(3, volume="0", source=ProvenanceKind.DUKASCOPY),
        _bar_at(4, volume="1000", source=ProvenanceKind.DUKASCOPY),
    ]
    spans = [
        result
        for result in check_series(_target(bars), CALENDAR)
        if result.kind is CheckKind.ZERO_VOLUME_SPAN
    ]
    assert len(spans) == 1
    assert spans[0].severity is Severity.INFO
    assert dict(spans[0].detail) == {"bars": "3"}
    assert spans[0].interval.start == bars[1].bar_start
    assert spans[0].interval.end == bars[3].bar_end


def test_separate_zero_volume_runs_are_reported_separately() -> None:
    bars = [
        _bar_at(0, volume="0", source=ProvenanceKind.DUKASCOPY),
        _bar_at(1, volume="1000", source=ProvenanceKind.DUKASCOPY),
        _bar_at(2, volume="0", source=ProvenanceKind.DUKASCOPY),
        _bar_at(3, volume="0", source=ProvenanceKind.DUKASCOPY),
    ]
    spans = [
        result
        for result in check_series(_target(bars), CALENDAR)
        if result.kind is CheckKind.ZERO_VOLUME_SPAN
    ]
    assert [dict(span.detail)["bars"] for span in spans] == ["1", "2"]


def test_a_zero_volume_span_reaching_the_end_is_reported() -> None:
    """末尾まで 0 が続く場合も報告する（区切りが来ないまま終わるケース）。"""
    bars = [
        _bar_at(0, volume="1000", source=ProvenanceKind.DUKASCOPY),
        _bar_at(1, volume="0", source=ProvenanceKind.DUKASCOPY),
        _bar_at(2, volume="0", source=ProvenanceKind.DUKASCOPY),
    ]
    spans = [
        result
        for result in check_series(_target(bars), CALENDAR)
        if result.kind is CheckKind.ZERO_VOLUME_SPAN
    ]
    assert len(spans) == 1
    assert dict(spans[0].detail) == {"bars": "2"}


def test_the_information_findings_never_fail_the_acceptance() -> None:
    """出所の切替も 0 出来高も記録のみ。受入れの合否には影響しない（D03 §3.9）。"""
    bars = [
        _bar_at(0, volume="0", source=ProvenanceKind.HISTDATA),
        _bar_at(1, volume="1000", source=ProvenanceKind.DUKASCOPY),
    ]
    report = check_all([_target(bars)], CALENDAR)
    kinds = _kinds(report.results)
    assert CheckKind.SOURCE_TRANSITION in kinds
    assert CheckKind.ZERO_VOLUME_SPAN in kinds
    assert not report.has_errors()


def test_the_information_findings_carry_no_prices() -> None:
    """封印期間の情報漏れを防ぐため、詳細に価格を入れない（D03 §3.9）。"""
    bars = [
        _bar_at(0, volume="0", source=ProvenanceKind.HISTDATA),
        _bar_at(1, volume="1000", source=ProvenanceKind.DUKASCOPY),
    ]
    for result in check_all([_target(bars)], CALENDAR).results:
        for key, value in result.detail:
            assert isinstance(key, str) and isinstance(value, str)
            assert "150" not in value, "価格らしい数値が詳細に含まれてはいけない"


# --- 報告の正規順序（D03 §3.7.1）-------------------------------------------


def test_the_report_order_is_independent_of_the_check_order() -> None:
    dropped = (
        UtcTime.parse("2026-01-14T10:00:00Z"),
        UtcTime.parse("2026-01-14T15:00:00Z"),
    )
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=dropped)
    other = market.series(symbol=market.EURUSD)
    other_bars = market.make_bars(other, market.TF_1H, CALENDAR, WINDOW)
    forward = check_all(
        [
            _target(bars),
            SeriesUnderCheck(series=other, timeframe_def=market.TF_1H, bars=other_bars),
        ],
        CALENDAR,
    )
    backward = check_all(
        [
            SeriesUnderCheck(series=other, timeframe_def=market.TF_1H, bars=other_bars),
            _target(bars),
        ],
        CALENDAR,
    )
    assert forward.results == backward.results


def test_the_report_sorts_by_the_d03_key() -> None:
    dropped = (
        UtcTime.parse("2026-01-14T15:00:00Z"),
        UtcTime.parse("2026-01-14T10:00:00Z"),
    )
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=dropped)
    report = check_all([_target(bars)], CALENDAR)
    keys = [result.sort_key() for result in report.results]
    assert keys == sorted(keys)


# --- 報告に価格を入れない（D03 §3.9）---------------------------------------


def test_a_check_detail_may_only_carry_strings() -> None:
    """価格などの数値を詳細に入れて封印期間の統計が漏れることを構造的に防ぐ。"""
    with pytest.raises(MarketDataValueError, match="structural information only"):
        CheckResult.create(
            CheckKind.MISSING_EXPECTED_BAR,
            HOURLY,
            WINDOW,
            detail={"close": 150.5},  # type: ignore[dict-item]
        )


def test_the_detail_is_normalized_into_a_canonical_order() -> None:
    forward = CheckResult.create(
        CheckKind.MISSING_EXPECTED_BAR, HOURLY, WINDOW, detail={"b": "2", "a": "1"}
    )
    backward = CheckResult.create(
        CheckKind.MISSING_EXPECTED_BAR, HOURLY, WINDOW, detail={"a": "1", "b": "2"}
    )
    assert forward == backward
    assert forward.detail == (("a", "1"), ("b", "2"))


# --- 既定の重大度（D03 §3.9 の表）------------------------------------------


def test_the_default_severities_match_the_design_table() -> None:
    expected = {
        CheckKind.DUPLICATE_TIMESTAMP: Severity.ERROR,
        CheckKind.OHLC_INCONSISTENT: Severity.ERROR,
        CheckKind.NAIVE_OR_FOREIGN_TZ: Severity.ERROR,
        CheckKind.IRREGULAR_INTERVAL: Severity.ERROR,
        CheckKind.MISSING_EXPECTED_BAR: Severity.WARN,
        CheckKind.UNEXPECTED_BAR: Severity.WARN,
        CheckKind.CROSS_SYMBOL_MISALIGNMENT: Severity.WARN,
        CheckKind.SOURCE_TRANSITION: Severity.INFO,
        CheckKind.ZERO_VOLUME_SPAN: Severity.INFO,
        CheckKind.DST_BOUNDARY_ANOMALY: Severity.WARN,
    }
    for kind, severity in expected.items():
        assert CheckResult.create(kind, HOURLY, WINDOW).severity is severity


def test_severity_counts_summarize_the_report() -> None:
    dropped = (UtcTime.parse("2026-01-14T10:00:00Z"),)
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=dropped)
    report = check_all([_target(bars)], CALENDAR)
    assert severity_counts(report)["WARN"] >= 1


def test_an_empty_report_has_no_errors() -> None:
    assert not IntegrityReport().has_errors()


def test_a_series_under_check_rejects_a_foreign_bar() -> None:
    other = market.series(symbol=market.EURUSD)
    interval = Interval(
        start=UtcTime.parse("2026-01-14T10:00:00Z"),
        end=UtcTime.parse("2026-01-14T10:00:00Z") + timedelta(hours=1),
    )
    with pytest.raises(MarketDataValueError, match="does not belong to series"):
        SeriesUnderCheck(
            series=HOURLY,
            timeframe_def=market.TF_1H,
            bars=(market.make_bar(other, interval),),
        )
