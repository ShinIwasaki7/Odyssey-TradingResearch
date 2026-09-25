"""受入れ結果の要約の組み立て（D03 §10 の `accept` の表示、§3.9）。

CLI が画面へ出す文言を作る。要約に載せるのは**構造情報だけ**（系列・件数・区間・種別）
であり、価格の統計は載せない。封印期間・未分類の隔離期間の partition について価格の統計
を出さないという D03 §3.9 の条件を、表示の側でも守るためである。

集計そのものはここで行い、表示の体裁だけを呼び出し側が決める。人間が「何がどれだけ
見つかったか」を1画面で把握できるよう、系列ごとに種別の件数を並べる。
"""

from __future__ import annotations

from collections.abc import Sequence

from odyssey_fx.evaluation.domain.metrics import (
    AmountValue,
    CategoryCount,
    CountValue,
    DurationValue,
    MetricRecord,
    MetricValue,
    PriceOffsetValue,
    RatioValue,
    TradeRecord,
)
from odyssey_fx.evaluation.domain.status import CheckOutcome, ConsistencyCheckResult
from odyssey_fx.marketdata.domain.classification import CLASSIFIABLE_KINDS
from odyssey_fx.marketdata.domain.integrity import IntegrityReport, Severity
from odyssey_fx.marketdata.domain.snapshot import SnapshotManifest

__all__ = [
    "category_lines",
    "classifiable_intervals",
    "consistency_lines",
    "findings_lines",
    "merged_warning_spans",
    "metric_lines",
    "partition_lines",
    "resolved_lines",
    "series_lines",
    "severity_totals",
    "trade_lines",
    "warning_summary_lines",
]


def series_lines(manifest: SnapshotManifest) -> list[str]:
    """系列ごとの足数と区間（D03 §3.7 の `series`）。"""
    lines = ["系列ごとの足数:"]
    for record in manifest.series:
        lines.append(
            f"  {record.series_id}: {record.bar_count} 本"
            f"  [{record.covered_interval.start}, {record.covered_interval.end})"
        )
    return lines


def partition_lines(manifest: SnapshotManifest) -> list[str]:
    """アクセス分類ごとの partition の件数（D03 §3.8）。"""
    lines = ["partition（アクセス分類ごと）:"]
    for record in manifest.partitions:
        lines.append(
            f"  {record.partition_id}: {record.bar_count} 本"
            f"  [{record.interval.start}, {record.interval.end})"
        )
    return lines


def severity_totals(report: IntegrityReport) -> dict[str, int]:
    """重大度ごとの合計件数（画面と記録で同じ数を使うため）。

    重大な違反（`ERROR`）が1件でもあれば受入れは失敗しているので、ここに出る `ERROR` は
    常に 0 である。0 であることを明示するために数えて出す（D03 §4 の 4）。
    """
    totals = {severity.value: 0 for severity in Severity}
    for result in report.results:
        totals[result.severity.value] += 1
    return totals


def findings_lines(report: IntegrityReport) -> list[str]:
    """検査結果の種別ごとの件数を系列ごとに並べる（D03 §3.9）。

    価格の統計は含めない。載せるのは系列・種別・重大度・件数だけである。
    """
    counts: dict[tuple[str, str, str], int] = {}
    for result in report.results:
        key = (str(result.series), result.severity.value, result.kind.value)
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        return ["完全性検査: 報告なし"]

    lines = ["完全性検査（系列 × 種別の件数）:"]
    for (series, severity, kind), count in sorted(counts.items()):
        lines.append(f"  {series}  {severity:<5} {kind}: {count} 件")

    totals = severity_totals(report)
    breakdown = "、".join(f"{name} {totals[name]} 件" for name in ("ERROR", "WARN", "INFO"))
    lines.append(f"  合計: {len(report.results)} 件（{breakdown}）")
    return lines


def merged_warning_spans(report: IntegrityReport, kind_value: str) -> int:
    """同じ系列で隣り合う・重なる警告の区間をつないだときの区間数を数える。

    「存在すべき足の欠落が何件あり、連続する区間にまとめると何区間か」を人間へ伝える
    ための数え上げである。分類（休場か欠損か）の判断そのものは人間が行うので、ここでは
    事実だけを数える（D03 §4 の 9）。

    数える前に**同じ系列・同じ区間を1件にまとめる**。報告は同じ区間でも詳細（`detail`）が
    違えば別の記録になる（D03 §3.7.1 の整列鍵に詳細が入る）ので、上位足の欠落は
    カレンダー照合と上位足の生成が同じ区間に対して2件報告する。重複を残したまま数えると、
    同じ1区間が2区間として数えられてしまう。

    隣り合う区間（前の終わりと次の始まりが一致）だけでなく、**重なる区間**も1つにまとめる。
    分類は区間ごとに記入するので、人間が見積もりたいのは「いくつの連続した塊があるか」で
    ある。
    """
    by_series: dict[str, set[tuple[str, str]]] = {}
    for result in report.results:
        if result.kind.value != kind_value:
            continue
        # 集合に入れることで、同じ系列・同じ区間の重複を落とす。
        by_series.setdefault(str(result.series), set()).add(
            (str(result.interval.start), str(result.interval.end))
        )

    spans = 0
    for intervals in by_series.values():
        # 時刻は ISO 8601 の固定長表記なので、文字列の順序が時刻の順序に一致する。
        previous_end: str | None = None
        for interval_start, interval_end in sorted(intervals):
            if previous_end is None or interval_start > previous_end:
                # 直前の塊と隣接も重複もしない = 新しい塊。
                spans += 1
            previous_end = interval_end if previous_end is None else max(previous_end, interval_end)
    return spans


def classifiable_intervals(report: IntegrityReport, kind_value: str) -> int:
    """分類の記入が必要な「系列 × 区間」の数を返す。

    報告の件数そのものではない。同じ系列・同じ区間に対する警告は、詳細が違えば別の記録に
    なる（上位足の欠落はカレンダー照合と上位足の生成が1件ずつ報告する）が、分類との
    突き合わせは区間全体で取るので、**記入するのは区間ごとに1件**である。人間が作業量を
    見積もれるよう、記入が必要な数を出す。
    """
    return len(
        {
            (str(result.series), str(result.interval))
            for result in report.results
            if result.kind.value == kind_value
        }
    )


def warning_summary_lines(report: IntegrityReport) -> list[str]:
    """分類が必要な警告の件数規模を種別ごとに示す行。

    対象は**分類対象の検査種別**（`CLASSIFIABLE_KINDS`: 存在すべき足の欠落と休場帯の足、
    D03 §3.9 v1.7）の警告だけである。確定（`acceptance.finalize`）が分類を求めるのもこの
    2種別だけなので、表示と確定の対象が揃う。他の警告（銘柄間の足境界のずれ、夏時間切替週の
    異常）は検査報告に保存するだけで分類を要しない（`findings_lines` の件数には現れる）。

    各行は「報告の件数」「分類の記入が必要な区間」「連続する塊」の3つを出す（同じ区間に
    対する重複した報告があるため、3つは一致しない）。
    """
    classifiable = [result for result in report.warnings if result.kind in CLASSIFIABLE_KINDS]
    lines: list[str] = []
    for kind_value in sorted({result.kind.value for result in classifiable}):
        reported = sum(1 for result in classifiable if result.kind.value == kind_value)
        lines.append(
            f"  {kind_value}: 報告 {reported} 件"
            f" / 分類の記入が必要な区間 {classifiable_intervals(report, kind_value)} 件"
            f" / 連続する区間にまとめると {merged_warning_spans(report, kind_value)} 区間"
        )
    return lines


def resolved_lines(manifest: SnapshotManifest) -> list[str]:
    """確定した分類の内訳（検査種別 × 分類結果ごとの警告の件数と、除外した足の本数）。

    `resolved_classifications`（警告1件ごとに解決した分類、D03 §3.7 v1.7）から数える。
    """
    counts: dict[tuple[str, str], int] = {}
    excluded: dict[tuple[str, str], int] = {}
    for resolved in manifest.resolved_classifications:
        key = (resolved.kind.value, resolved.outcome.value)
        counts[key] = counts.get(key, 0) + 1
        excluded[key] = excluded.get(key, 0) + resolved.excluded_bar_count
    lines: list[str] = []
    for kind_value, outcome_value in sorted(counts):
        key = (kind_value, outcome_value)
        line = f"  {kind_value} -> {outcome_value}: 警告 {counts[key]} 件"
        if excluded[key]:
            line += f"（除外した足 {excluded[key]} 本）"
        lines.append(line)
    return lines


# --- 評価結果の表示（D07 §5・§6・§10）----------------------------------------


def metric_lines(metrics: Sequence[MetricRecord]) -> list[str]:
    """指標15件（D07 §5.2）。値なしは理由をそのまま出す。

    **値なしを 0 や空欄に見せない**（D07 §5.1）。「計算できなかった」と「0 だった」を
    画面でも区別できるようにする。注記（swap 未計上・参考値など）も1行に添える。
    """
    lines = ["指標:"]
    for record in metrics:
        caveats = (
            ""
            if not record.caveats
            else "  [" + ", ".join(item.value for item in record.caveats) + "]"
        )
        lines.append(
            f"  {record.metric_id.value}: {_metric_text(record.value)}"
            f"  （観測 {record.observation_count} 件）{caveats}"
        )
    return lines


def _metric_text(value: MetricValue) -> str:
    """指標の値1件の表示。"""
    if isinstance(value, AmountValue):
        return str(value.amount)
    if isinstance(value, RatioValue):
        return str(value.ratio)
    if isinstance(value, CountValue):
        return str(value.count)
    if isinstance(value, DurationValue):
        return str(value.duration)
    if isinstance(value, PriceOffsetValue):
        return str(value.offset)
    return f"値なし（{value.reason.value}）"


def consistency_lines(checks: Sequence[ConsistencyCheckResult]) -> list[str]:
    """整合検査の結果（D07 §10.2・§10.4）。3区分のどれでも全件出す。

    不合格だけを出すと、検査が実施されたのかどうかが画面から分からない。読めなかった検査
    （`UNREADABLE`）は不合格と分けて出す（検査して食い違いを見つけたのではない）。
    """
    lines = ["整合検査:"]
    for check in checks:
        if check.outcome is CheckOutcome.PASSED:
            mark = "合格"
        elif check.outcome is CheckOutcome.UNREADABLE:
            mark = f"読めなかった（{check.level.value}）"
        else:
            mark = f"不合格（{check.level.value}）"
        lines.append(f"  {check.check}: {mark}")
        if not check.passed:
            lines.append(f"    期待: {check.expected}")
            lines.append(f"    観測: {check.observed}")
    return lines


def trade_lines(trades: Sequence[TradeRecord]) -> list[str]:
    """完了取引の一覧（D07 §5.2 の `TradeRecord`）。"""
    lines = ["完了取引:"]
    for trade in trades:
        cause = "—" if trade.close_cause is None else trade.close_cause.value
        lines.append(
            f"  #{trade.trade_seq} {trade.symbol} {trade.side.value} {trade.quantity}"
            f"  {trade.entry_price} → {trade.exit_price}"
            f"  {trade.realized}  取引損益 {trade.trade_profit}  {trade.outcome.value}"
            f"  {cause}  保有 {trade.holding}"
        )
    return lines


def category_lines(categories: Sequence[CategoryCount]) -> list[str]:
    """集計8種（D07 §6.1・§6.3）。**0件の鍵も出す**。

    出さないと「一度も起きなかった」と「集計していない」を画面から区別できない。
    """
    lines = ["集計:"]
    current: str | None = None
    for count in categories:
        if count.category.value != current:
            current = count.category.value
            lines.append(f"  {current}:")
        lines.append(f"    {count.key}: {count.count}")
    return lines
