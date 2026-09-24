"""受入れ結果の要約の数え上げの単体テスト（D03 §10 の `accept` の表示、§4 の 9）。

人間が分類の作業量を見積もるための数え上げを確かめる。報告は同じ系列・同じ区間でも
詳細（`detail`）が違えば別の記録になる（D03 §3.7.1 の整列鍵に詳細が入る）ため、上位足の
欠落はカレンダー照合と上位足の生成が同じ区間に対して2件報告する。一方で分類との
突き合わせは区間全体で取るので、**記入するのは区間ごとに1件**である。数え上げがこの
違いを正しく扱うことを確かめる。
"""

from __future__ import annotations

from odyssey_fx.app.cli.summary import (
    classifiable_intervals,
    merged_warning_spans,
    warning_summary_lines,
)
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from tests.fixtures.synthetic import market

HOURLY = market.series()
QUARTER = market.series(timeframe_id="15m")
MISSING = CheckKind.MISSING_EXPECTED_BAR.value


def _missing(start: str, end: str, series: SeriesId | None = None, **detail: str) -> CheckResult:
    """存在すべき足の欠落の報告を1件作る。"""
    return CheckResult.create(
        CheckKind.MISSING_EXPECTED_BAR,
        HOURLY if series is None else series,
        Interval(start=UtcTime.parse(start), end=UtcTime.parse(end)),
        detail=detail,
    )


def _report(*results: CheckResult) -> IntegrityReport:
    return IntegrityReport(results=results)


# --- 同じ区間の重複した報告（P2-2 の本体）-----------------------------------


def test_two_reports_of_the_same_interval_count_as_one_span() -> None:
    """同じ系列・同じ区間の警告 2 件を、2 区間として数えない。

    上位足の欠落は、カレンダー照合が「足が無い」として1件、上位足の生成が「構成足が
    足りず生成できなかった」として1件、同じ区間に報告する。詳細が違うので報告としては
    別の記録だが、分類の対象としては同じ1区間である。
    """
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T14:00:00Z"),
        _missing("2022-01-06T10:00:00Z", "2022-01-06T14:00:00Z", reason="incomplete_aggregate"),
    )
    assert len(report.results) == 2, "報告としては2件"
    assert merged_warning_spans(report, MISSING) == 1
    assert classifiable_intervals(report, MISSING) == 1


def test_the_summary_line_separates_the_three_numbers() -> None:
    """表示は「報告の件数」「記入が必要な区間」「連続する塊」を別々に出す。"""
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T14:00:00Z"),
        _missing("2022-01-06T10:00:00Z", "2022-01-06T14:00:00Z", reason="incomplete_aggregate"),
    )
    (line,) = warning_summary_lines(report)
    assert "報告 2 件" in line
    assert "分類の記入が必要な区間 1 件" in line
    assert "1 区間" in line


# --- 連続・分離・重なり -----------------------------------------------------


def test_adjacent_intervals_merge_into_one_span() -> None:
    """前の終わりと次の始まりが一致する区間は1つの塊になる。"""
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z"),
        _missing("2022-01-06T11:00:00Z", "2022-01-06T12:00:00Z"),
        _missing("2022-01-06T12:00:00Z", "2022-01-06T13:00:00Z"),
    )
    assert merged_warning_spans(report, MISSING) == 1
    assert classifiable_intervals(report, MISSING) == 3


def test_separated_intervals_stay_separate_spans() -> None:
    """間が空いていれば別の塊として数える。"""
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z"),
        _missing("2022-01-07T05:00:00Z", "2022-01-07T06:00:00Z"),
    )
    assert merged_warning_spans(report, MISSING) == 2


def test_overlapping_intervals_merge_into_one_span() -> None:
    """重なる区間も1つの塊にまとめる。

    1時間足の欠落（10:00〜11:00）と、それを含む4時間足の欠落（10:00〜14:00）のように、
    長さの違う区間が重なることがある。
    """
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T14:00:00Z"),
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z"),
        _missing("2022-01-06T11:00:00Z", "2022-01-06T12:00:00Z"),
    )
    assert merged_warning_spans(report, MISSING) == 1


def test_a_contained_interval_does_not_extend_the_span() -> None:
    """長い区間の内側に収まる区間が、塊の終端を縮めない。"""
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T14:00:00Z"),
        _missing("2022-01-06T11:00:00Z", "2022-01-06T12:00:00Z"),
        _missing("2022-01-06T14:00:00Z", "2022-01-06T15:00:00Z"),
    )
    # 10:00〜14:00 と 11:00〜12:00 と 14:00〜15:00 はすべて繋がって1つの塊。
    assert merged_warning_spans(report, MISSING) == 1


# --- 系列ごとに分けて数える -------------------------------------------------


def test_spans_are_counted_per_series() -> None:
    """系列が違えば、区間が同じでも別の塊として数える。

    分類は系列ごとに記入するので、系列をまたいでまとめてはいけない。
    """
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z"),
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z", series=QUARTER),
    )
    assert merged_warning_spans(report, MISSING) == 2
    assert classifiable_intervals(report, MISSING) == 2


def test_another_kind_is_not_counted() -> None:
    """指定した種別だけを数える。"""
    unexpected = CheckResult.create(
        CheckKind.UNEXPECTED_BAR,
        HOURLY,
        Interval(
            start=UtcTime.parse("2022-01-08T10:00:00Z"),
            end=UtcTime.parse("2022-01-08T11:00:00Z"),
        ),
    )
    report = _report(_missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z"), unexpected)
    assert merged_warning_spans(report, MISSING) == 1
    assert merged_warning_spans(report, CheckKind.UNEXPECTED_BAR.value) == 1


def test_an_empty_report_counts_nothing() -> None:
    assert merged_warning_spans(IntegrityReport(), MISSING) == 0
    assert classifiable_intervals(IntegrityReport(), MISSING) == 0
    assert warning_summary_lines(IntegrityReport()) == []


def test_the_price_basis_is_part_of_the_series_key() -> None:
    """価格基準が違えば別の系列として数える（系列の鍵に含まれるため）。"""
    ask = market.series(basis=PriceBasis.ASK)
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z"),
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z", series=ask),
    )
    assert merged_warning_spans(report, MISSING) == 2


# --- 表示と確定の対象を揃える（D03 §4 の 9）---------------------------------


def test_only_the_classifiable_warning_kinds_appear_in_the_summary() -> None:
    """要約は**分類対象の2種別**だけを「分類が要る」として出す（D03 §3.9 v1.7）。

    確定（`finalize`）が分類を求めるのは存在すべき足の欠落と休場帯の足だけである。
    銘柄間の足境界のずれと夏時間切替週の異常は保存するだけで分類を要しないので、
    「分類が要る」として見せると人間が不要な分類を書いてしまう。
    """
    window = Interval(
        start=UtcTime.parse("2022-01-06T10:00:00Z"),
        end=UtcTime.parse("2022-01-06T11:00:00Z"),
    )
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z"),
        CheckResult.create(CheckKind.UNEXPECTED_BAR, HOURLY, window),
        CheckResult.create(CheckKind.CROSS_SYMBOL_MISALIGNMENT, HOURLY, window),
        CheckResult.create(CheckKind.DST_BOUNDARY_ANOMALY, HOURLY, window),
    )
    lines = warning_summary_lines(report)
    shown = {line.strip().split(":")[0] for line in lines}
    assert shown == {
        CheckKind.MISSING_EXPECTED_BAR.value,
        CheckKind.UNEXPECTED_BAR.value,
    }


def test_informational_findings_are_not_listed_as_needing_classification() -> None:
    """記録のみの結果（INFO）は分類の対象ではないので出さない。

    確定が分類を求めるのは警告（WARN）だけである（`report.warnings`）。
    """
    window = Interval(
        start=UtcTime.parse("2022-01-06T10:00:00Z"),
        end=UtcTime.parse("2022-01-06T11:00:00Z"),
    )
    report = _report(
        _missing("2022-01-06T10:00:00Z", "2022-01-06T11:00:00Z"),
        CheckResult.create(CheckKind.SOURCE_TRANSITION, HOURLY, window),
        CheckResult.create(CheckKind.ZERO_VOLUME_SPAN, HOURLY, window),
    )
    lines = warning_summary_lines(report)
    assert len(lines) == 1
    assert CheckKind.MISSING_EXPECTED_BAR.value in lines[0]
    assert CheckKind.SOURCE_TRANSITION.value not in "".join(lines)
