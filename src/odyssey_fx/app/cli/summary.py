"""受入れ結果の要約の組み立て（D03 §10 の `accept` の表示、§3.9）。

CLI が画面へ出す文言を作る。要約に載せるのは**構造情報だけ**（系列・件数・区間・種別）
であり、価格の統計は載せない。封印期間・未分類の隔離期間の partition について価格の統計
を出さないという D03 §3.9 の条件を、表示の側でも守るためである。

集計そのものはここで行い、表示の体裁だけを呼び出し側が決める。人間が「何がどれだけ
見つかったか」を1画面で把握できるよう、系列ごとに種別の件数を並べる。
"""

from __future__ import annotations

from collections.abc import Sequence

from odyssey_fx.marketdata.domain.integrity import IntegrityReport, Severity
from odyssey_fx.marketdata.domain.snapshot import SnapshotManifest

__all__ = [
    "findings_lines",
    "merged_warning_spans",
    "partition_lines",
    "series_lines",
    "severity_totals",
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
    """同じ系列で隣り合う警告の区間をつないだときの区間数を数える。

    「存在すべき足の欠落が何件あり、連続する区間にまとめると何区間か」を人間へ伝える
    ための数え上げである。分類（休場か欠損か）の判断そのものは人間が行うので、ここでは
    事実だけを数える（D03 §4 の 9）。
    """
    by_series: dict[str, list[tuple[str, str]]] = {}
    for result in report.results:
        if result.kind.value != kind_value:
            continue
        by_series.setdefault(str(result.series), []).append(
            (str(result.interval.start), str(result.interval.end))
        )

    spans = 0
    for intervals in by_series.values():
        ordered = sorted(intervals)
        previous_end: str | None = None
        for start, end in ordered:
            if previous_end is None or start != previous_end:
                spans += 1
            previous_end = max(previous_end or end, end)
    return spans


def warning_summary_lines(report: IntegrityReport, kinds: Sequence[str]) -> list[str]:
    """警告の件数規模を示す行（件数と、連続する区間にまとめたときの区間数）。"""
    lines: list[str] = []
    for kind_value in kinds:
        count = sum(1 for result in report.results if result.kind.value == kind_value)
        if not count:
            continue
        lines.append(
            f"  {kind_value}: {count} 件"
            f"（連続する区間にまとめると {merged_warning_spans(report, kind_value)} 区間）"
        )
    return lines
