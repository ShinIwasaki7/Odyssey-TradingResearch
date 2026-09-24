"""分類の突き合わせ・確定段階の再実行・除外規則の単体テスト（D03 §3.9・§4 の 9・§11 v1.7）。

確かめること（D03 §11 の「単体」v1.7 の行）:

- 分類の突き合わせ: 未分類・競合・対応なしの拒否、「全系列」の解決、明示集合と「全系列」で
  同じ `snapshot_id`。
- 種別と結果の組合せの拒否。
- セッション外データ異常の除外（除外した足の件数は解決済みの分類にだけ残る）。
- 営業例外による「休場帯の足」の解消（カレンダーの版の引き上げが要る）。
- 分類対象の検査種別は明示集合の2種別だけ。
"""

from __future__ import annotations

from datetime import date, time

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.acceptance import (
    PendingSnapshot,
    RawFile,
    build_pending_snapshot,
    finalize,
    out_of_session_exclusions,
    reaccept_with_calendar,
    requires_rerun,
)
from odyssey_fx.marketdata.application.classification import (
    classifiable_warnings,
    match_classifications,
)
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import OpeningRule, TradingCalendar
from odyssey_fx.marketdata.domain.classification import (
    ClassificationDecision,
    ClassificationOutcome,
    ResolvedClassification,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
EUR_HOURLY = market.series(symbol=market.EURUSD)
CALENDAR = market.calendar()
CREATED_AT = UtcTime.parse("2026-09-24T09:00:00Z")

#: 日曜の開場（ニューヨーク 17:00 = 冬時間の 22:00Z）をまたぐ窓。
WINDOW = Interval(
    start=UtcTime.parse("2022-01-09T22:00:00Z"), end=UtcTime.parse("2022-01-10T22:00:00Z")
)
#: 日曜 16:00 NY（21:00Z）に始まる、開場前の「休場帯の足」。
EARLY = Interval(
    start=UtcTime.parse("2022-01-09T21:00:00Z"), end=UtcTime.parse("2022-01-09T22:00:00Z")
)
#: 月曜 05:00 NY（10:00Z）の足。休場を宣言すると「休場帯の足」になる。
MONDAY_10Z = Interval(
    start=UtcTime.parse("2022-01-10T10:00:00Z"), end=UtcTime.parse("2022-01-10T11:00:00Z")
)
#: 月曜 12:00Z の欠落（間引いた足）。
MISSING = Interval(
    start=UtcTime.parse("2022-01-10T12:00:00Z"), end=UtcTime.parse("2022-01-10T13:00:00Z")
)
#: この試験の暫定 snapshot は1時間足だけを持つので、再実行でも上位足は作らない。上位足を
#: 含む経路（再実行が上位足の欠落を新たに報告する場合）は CLI の統合試験が扱う。
AGGREGATION_TARGETS: tuple[tuple[str, str], ...] = ()


def _raw_file(series: SeriesId) -> RawFile:
    source = snapshots.source(f"data/raw/market/{series.symbol}_1h_merged.csv", str(series.symbol))
    return RawFile(
        path=source.path,
        sha256=source.sha256,
        symbol=source.symbol,
        timeframe=source.timeframe,
        declared_basis=source.declared_basis,
    )


def _bars(series: SeriesId, *, early: bool, missing: bool) -> tuple[Bar, ...]:
    skip = (MISSING.start,) if missing else ()
    bars = market.make_bars(series, market.TF_1H, CALENDAR, WINDOW, skip_starts=skip)
    if early:
        bars = (market.make_bar(series, EARLY), *bars)
    return bars


def _pending(
    *, eur_early: bool = False, usd_early: bool = True, usd_missing: bool = True
) -> PendingSnapshot:
    """USDJPY（休場帯の足1本・欠落1本）と EURUSD の暫定 snapshot。"""
    usd, eur = _raw_file(HOURLY), _raw_file(EUR_HOURLY)
    return build_pending_snapshot(
        created_at=CREATED_AT,
        raw_files=(usd, eur),
        bars_by_file={
            usd.path: _bars(HOURLY, early=usd_early, missing=usd_missing),
            eur.path: _bars(EUR_HOURLY, early=eur_early, missing=False),
        },
        timeframe_defs=market.TIMEFRAME_DEFS,
        calendar=CALENDAR,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        basis_declaration=snapshots.BASIS,
        conversion=snapshots.CONVERSION,
    )


def _decision(
    kind: CheckKind,
    interval: Interval,
    outcome: ClassificationOutcome,
    series: tuple[SeriesId, ...] = (HOURLY,),
) -> ClassificationDecision:
    return ClassificationDecision(kind=kind, interval=interval, series=series, outcome=outcome)


def _gap(series: tuple[SeriesId, ...] = (HOURLY,)) -> ClassificationDecision:
    return _decision(
        CheckKind.MISSING_EXPECTED_BAR, MISSING, ClassificationOutcome.DATA_GAP, series
    )


def _out_of_session(series: tuple[SeriesId, ...] = (HOURLY,)) -> ClassificationDecision:
    return _decision(
        CheckKind.UNEXPECTED_BAR, EARLY, ClassificationOutcome.OUT_OF_SESSION_DATA, series
    )


def _rerun(
    pending: PendingSnapshot,
    decisions: tuple[ClassificationDecision, ...],
    calendar: TradingCalendar = CALENDAR,
) -> PendingSnapshot:
    return reaccept_with_calendar(
        pending,
        calendar=calendar,
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        aggregation_targets=AGGREGATION_TARGETS,
        excluded=out_of_session_exclusions(pending, decisions),
    )


def _with_opening(version: int = 2) -> TradingCalendar:
    """日曜 16:00〜17:00 NY を営業例外として宣言したカレンダー。"""
    return TradingCalendar(
        id=CALENDAR.id,
        version=version,
        tz=CALENDAR.tz,
        weekly_open=CALENDAR.weekly_open,
        weekly_close=CALENDAR.weekly_close,
        openings=(OpeningRule(local_date=date(2022, 1, 9), start=time(16, 0), end=time(17, 0)),),
    )


# --- 分類対象の検査種別（D03 §3.9 v1.7）------------------------------------


def test_only_the_two_classifiable_kinds_require_a_decision() -> None:
    """銘柄間の境界ずれ・夏時間の異常は分類を要しない（明示集合の外）。"""
    report = IntegrityReport(
        results=(
            CheckResult.create(CheckKind.CROSS_SYMBOL_MISALIGNMENT, HOURLY, MISSING),
            CheckResult.create(CheckKind.DST_BOUNDARY_ANOMALY, HOURLY, MISSING),
            CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, HOURLY, MISSING),
        )
    )
    (warning,) = classifiable_warnings((report,))
    assert warning.kind is CheckKind.MISSING_EXPECTED_BAR


def test_duplicate_reports_of_the_same_warning_count_once() -> None:
    """同じ種別・系列・区間の警告は、詳細が違っても1件として突き合わせる。"""
    report = IntegrityReport(
        results=(
            CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, HOURLY, MISSING),
            CheckResult.create(
                CheckKind.MISSING_EXPECTED_BAR,
                HOURLY,
                MISSING,
                detail={"reason": "incomplete_aggregate"},
            ),
        )
    )
    match = match_classifications((report,), (_gap(),))
    assert match.complete
    assert len(match.assignments) == 1


# --- 種別と結果の組合せ（D03 §3.9）-----------------------------------------


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [
        (CheckKind.MISSING_EXPECTED_BAR, ClassificationOutcome.CALENDAR_EXCEPTION),
        (CheckKind.MISSING_EXPECTED_BAR, ClassificationOutcome.OUT_OF_SESSION_DATA),
        (CheckKind.UNEXPECTED_BAR, ClassificationOutcome.CLOSURE),
        (CheckKind.UNEXPECTED_BAR, ClassificationOutcome.DATA_GAP),
    ],
)
def test_a_mismatched_kind_and_outcome_is_rejected(
    kind: CheckKind, outcome: ClassificationOutcome
) -> None:
    with pytest.raises(MarketDataValueError, match="not an outcome for"):
        _decision(kind, MISSING, outcome)


def test_a_kind_outside_the_classifiable_set_cannot_be_declared() -> None:
    with pytest.raises(MarketDataValueError, match="does not require a human classification"):
        _decision(CheckKind.CROSS_SYMBOL_MISALIGNMENT, MISSING, ClassificationOutcome.DATA_GAP)


def test_only_out_of_session_data_may_record_excluded_bars() -> None:
    with pytest.raises(MarketDataValueError, match="only OUT_OF_SESSION_DATA excludes bars"):
        ResolvedClassification(
            kind=CheckKind.MISSING_EXPECTED_BAR,
            series_id=HOURLY,
            interval=MISSING,
            outcome=ClassificationOutcome.DATA_GAP,
            excluded_bar_count=1,
        )


# --- 突き合わせ（D03 §4 の確定時の検査 1〜4・6）------------------------------


def test_conflicting_decisions_are_rejected() -> None:
    """同じ警告を2件の分類が覆えば拒否する（確定時の検査2）。"""
    pending = _pending(usd_early=False)
    wide = _decision(
        CheckKind.MISSING_EXPECTED_BAR, WINDOW, ClassificationOutcome.DATA_GAP, (HOURLY,)
    )
    with pytest.raises(MarketDataValueError, match="more than one classification"):
        finalize(pending, (_gap(), wide))


def test_overlapping_decisions_that_share_no_warning_are_accepted() -> None:
    """区間が重なっても、警告を共有しなければ競合ではない（確定時の検査2）。

    続けて欠けた2本の足について、重なる2件の分類がそれぞれ別の1本だけを完全に含む。
    """
    other = Interval(start=MISSING.end, end=MISSING.end + market.TF_1H.nominal_length)
    usd, eur = _raw_file(HOURLY), _raw_file(EUR_HOURLY)
    bars = market.make_bars(
        HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=(MISSING.start, other.start)
    )
    two_gaps = build_pending_snapshot(
        created_at=CREATED_AT,
        raw_files=(usd, eur),
        bars_by_file={usd.path: bars, eur.path: _bars(EUR_HOURLY, early=False, missing=False)},
        timeframe_defs=market.TIMEFRAME_DEFS,
        calendar=CALENDAR,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        basis_declaration=snapshots.BASIS,
        conversion=snapshots.CONVERSION,
    )
    first = _decision(
        CheckKind.MISSING_EXPECTED_BAR,
        Interval(start=MISSING.start - market.TF_1H.nominal_length, end=MISSING.end),
        ClassificationOutcome.DATA_GAP,
    )
    second = _decision(
        CheckKind.MISSING_EXPECTED_BAR,
        Interval(start=MISSING.start + market.TF_1H.nominal_length / 2, end=other.end),
        ClassificationOutcome.DATA_GAP,
    )
    assert first.interval.overlaps(second.interval)
    final = finalize(two_gaps, (first, second))
    assert len(final.manifest.resolved_classifications) == 2


def test_all_series_and_an_explicit_set_yield_the_same_snapshot_id() -> None:
    """「全系列」と明示集合で、同じ警告集合に同じ結果を与えれば同じ識別子（検査4・6）。"""
    pending = _pending(usd_early=False)
    explicit = finalize(pending, (_gap((HOURLY,)),))
    everything = finalize(pending, (_gap((EUR_HOURLY, HOURLY)),))
    assert explicit.snapshot_id == everything.snapshot_id
    assert explicit.manifest.closure_decisions != everything.manifest.closure_decisions


def test_one_wide_decision_and_narrow_ones_yield_the_same_snapshot_id() -> None:
    """区間のまとめ方によらず同じ識別子（確定時の検査6）。"""
    pending = _pending(usd_early=False)
    narrow = finalize(pending, (_gap(),))
    wide = finalize(
        pending,
        (_decision(CheckKind.MISSING_EXPECTED_BAR, WINDOW, ClassificationOutcome.DATA_GAP),),
    )
    assert narrow.snapshot_id == wide.snapshot_id


def test_a_wide_decision_does_not_invent_warnings() -> None:
    """分類の範囲を広げても、報告されていない警告は解決済みの分類に現れない（検査5）。"""
    pending = _pending(usd_early=False)
    wide = finalize(
        pending,
        (
            _decision(
                CheckKind.MISSING_EXPECTED_BAR,
                WINDOW,
                ClassificationOutcome.DATA_GAP,
                (EUR_HOURLY, HOURLY),
            ),
        ),
    )
    (resolved,) = wide.manifest.resolved_classifications
    assert resolved.series_id == HOURLY
    assert resolved.interval == MISSING


# --- セッション外データ異常の除外（D03 §4 の除外規則）----------------------


def test_out_of_session_data_requires_a_rerun() -> None:
    assert requires_rerun((_out_of_session(),), calendar_changed=False)
    assert not requires_rerun((_gap(),), calendar_changed=False)
    assert requires_rerun((_gap(),), calendar_changed=True)


def test_out_of_session_bars_are_excluded_and_counted() -> None:
    """除外した足は partition に含めず、件数は解決済みの分類にだけ残る。"""
    pending = _pending()
    decisions = (_gap(), _out_of_session())
    final = _rerun(pending, decisions)
    finalized = finalize(final, decisions, provisional=pending)

    # 原系列から除外されている。
    usd_bars = [
        bar
        for partition, bars in final.partition_bars.items()
        for bar in bars
        if partition.series == HOURLY
    ]
    assert EARLY not in {bar.interval for bar in usd_bars}
    # 件数は解決済みの分類にだけ持つ（人間が書く分類には持たない）。
    resolved = {item.outcome: item for item in finalized.manifest.resolved_classifications}
    assert resolved[ClassificationOutcome.OUT_OF_SESSION_DATA].excluded_bar_count == 1
    assert resolved[ClassificationOutcome.DATA_GAP].excluded_bar_count == 0
    # 原ファイルの行数の記録は変えない（D03 §4）。
    assert finalized.manifest.sources == pending.manifest.sources
    # 暫定報告と最終報告が異なるので、暫定報告のダイジェストを別に記録する。
    assert finalized.manifest.provisional_report_ref == pending.manifest.integrity_report_ref
    assert finalized.manifest.integrity_report_ref != pending.manifest.integrity_report_ref


def test_a_wide_out_of_session_decision_excludes_only_reported_bars() -> None:
    """除外は報告された休場帯の足に限る。範囲を広く書いても通常の足は消えない。"""
    pending = _pending()
    wide = _decision(
        CheckKind.UNEXPECTED_BAR,
        Interval(start=EARLY.start, end=WINDOW.end),
        ClassificationOutcome.OUT_OF_SESSION_DATA,
        (EUR_HOURLY, HOURLY),
    )
    assert out_of_session_exclusions(pending, (wide,)) == frozenset({(HOURLY, EARLY)})


def test_out_of_session_data_without_a_rerun_is_rejected() -> None:
    """除外しないまま「セッション外データ異常」と記録することはできない。"""
    pending = _pending()
    with pytest.raises(MarketDataValueError, match="were not excluded"):
        finalize(pending, (_gap(), _out_of_session()))


def test_a_bar_that_disappears_without_a_classification_is_rejected() -> None:
    """除外以外の理由で原系列の足が消えた再実行は確定させない。"""
    pending = _pending()
    # 休場帯の足を営業例外と分類しつつ、除外も指定した（食い違った）再実行。
    decisions = (
        _gap(),
        _decision(CheckKind.UNEXPECTED_BAR, EARLY, ClassificationOutcome.CALENDAR_EXCEPTION),
    )
    final = reaccept_with_calendar(
        pending,
        calendar=_with_opening(),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        aggregation_targets=AGGREGATION_TARGETS,
        excluded=frozenset({(HOURLY, EARLY)}),
    )
    with pytest.raises(MarketDataValueError, match="disappeared in the re-run"):
        finalize(final, decisions, provisional=pending)


# --- 営業例外（D03 §3.4・§3.9）---------------------------------------------


def test_a_calendar_exception_is_settled_by_an_opening() -> None:
    """休場帯の足を営業例外と分類し、カレンダーの新版に宣言すれば確定できる。"""
    pending = _pending()
    decisions = (
        _gap(),
        _decision(CheckKind.UNEXPECTED_BAR, EARLY, ClassificationOutcome.CALENDAR_EXCEPTION),
    )
    final = _rerun(pending, decisions, _with_opening())
    finalized = finalize(final, decisions, provisional=pending)
    outcomes = {item.outcome for item in finalized.manifest.resolved_classifications}
    assert ClassificationOutcome.CALENDAR_EXCEPTION in outcomes
    assert finalized.manifest.conversion.calendar_version == 2


def test_a_calendar_exception_needs_the_calendar_to_be_revised() -> None:
    pending = _pending()
    decisions = (
        _gap(),
        _decision(CheckKind.UNEXPECTED_BAR, EARLY, ClassificationOutcome.CALENDAR_EXCEPTION),
    )
    with pytest.raises(MarketDataValueError, match="calendar was not revised"):
        finalize(pending, decisions)


def test_a_calendar_exception_the_new_calendar_does_not_declare_is_rejected() -> None:
    """版を上げただけで営業例外を宣言しなければ、休場帯の足は最終報告に残る。"""
    pending = _pending()
    decisions = (
        _gap(),
        _decision(CheckKind.UNEXPECTED_BAR, EARLY, ClassificationOutcome.CALENDAR_EXCEPTION),
    )
    final = _rerun(pending, decisions, market.calendar(version=2))
    with pytest.raises(MarketDataValueError, match="still treats them as outside"):
        finalize(final, decisions, provisional=pending)


# --- 再実行が生む新たな警告（D03 §4 の反復的な分類）-----------------------


def _closure_on_monday(version: int = 2) -> TradingCalendar:
    """月曜 05:00〜06:00 NY（10:00〜11:00Z）を休場と宣言したカレンダー。"""
    return market.calendar(
        closures=[market.closure(date(2022, 1, 10), time(5, 0), time(6, 0))], version=version
    )


def test_warnings_created_by_the_rerun_must_be_classified() -> None:
    """カレンダーの修正が別の警告を生むと、未分類として列挙されて失敗する。

    USDJPY の欠落を休場と宣言したことで、同じ時間の EURUSD の足が「休場帯の足」になる。
    """
    pending = _pending(usd_early=False, usd_missing=False)
    # 暫定報告には警告が無い状態から、休場の宣言だけを入れた再実行を作る。
    final = _rerun(pending, (), _closure_on_monday())
    new = [
        result
        for result in classifiable_warnings((final.report,))
        if result.kind is CheckKind.UNEXPECTED_BAR
    ]
    assert {result.interval for result in new} == {MONDAY_10Z}
    with pytest.raises(MarketDataValueError, match="still unclassified"):
        finalize(final, (), provisional=pending)


def test_a_rerun_only_out_of_session_bar_is_not_excluded() -> None:
    """再実行の報告にだけ現れる休場帯の足は除外しない（仮置き。人間の決定待ち）。

    D03 は確定の根拠を暫定報告と最終報告の2つで閉じるので、除外して最終報告から消えた警告の
    分類は2つの報告から検証できない。除外せずに、確定の検査で止める。
    """
    # USDJPY だけ月曜 10:00Z の足が欠ける。これを休場と分類してカレンダーへ宣言すると、
    # 同じ時間の EURUSD の足が再実行で初めて「休場帯の足」になる。
    usd, eur = _raw_file(HOURLY), _raw_file(EUR_HOURLY)
    pending = build_pending_snapshot(
        created_at=CREATED_AT,
        raw_files=(usd, eur),
        bars_by_file={
            usd.path: market.make_bars(
                HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=(MONDAY_10Z.start,)
            ),
            eur.path: _bars(EUR_HOURLY, early=False, missing=False),
        },
        timeframe_defs=market.TIMEFRAME_DEFS,
        calendar=CALENDAR,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        basis_declaration=snapshots.BASIS,
        conversion=snapshots.CONVERSION,
    )
    decisions = (
        _decision(CheckKind.MISSING_EXPECTED_BAR, MONDAY_10Z, ClassificationOutcome.CLOSURE),
        _decision(
            CheckKind.UNEXPECTED_BAR,
            MONDAY_10Z,
            ClassificationOutcome.OUT_OF_SESSION_DATA,
            (EUR_HOURLY,),
        ),
    )
    assert out_of_session_exclusions(pending, decisions) == frozenset()
    final = _rerun(pending, decisions, _closure_on_monday())
    with pytest.raises(MarketDataValueError, match="were not excluded"):
        finalize(final, decisions, provisional=pending)


# --- 分類に裏付けられないカレンダー変更（D03 §3.4・§3.9）-------------------


def test_a_calendar_change_without_a_calendar_changing_outcome_is_rejected() -> None:
    """セッション外データ異常・データ欠損だけの分類で、カレンダーを変えた再実行は拒否する。"""
    pending = _pending()
    decisions = (_gap(), _out_of_session())
    final = _rerun(pending, decisions, market.calendar(version=2))
    with pytest.raises(MarketDataValueError, match="authorize a calendar change"):
        finalize(final, decisions, provisional=pending)


def test_a_different_calendar_identity_without_a_calendar_changing_outcome_is_rejected() -> None:
    pending = _pending()
    decisions = (_gap(), _out_of_session())
    other = TradingCalendar(
        id="other",
        version=1,
        tz=CALENDAR.tz,
        weekly_open=CALENDAR.weekly_open,
        weekly_close=CALENDAR.weekly_close,
    )
    final = _rerun(pending, decisions, other)
    with pytest.raises(MarketDataValueError, match="authorize a calendar change"):
        finalize(final, decisions, provisional=pending)
