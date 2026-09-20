"""完全性検査（D03 §3.9、§4 の 4〜5）。

正規化した足に対して2段階の検査を行う。

1. **構造検査**（D03 §4 の 4）: 重複した開始時刻、OHLC の整合違反、定義の整列に合わない
   開始時刻。いずれも重大な違反（`ERROR`）で、1件でもあれば受入れを失敗させる。時刻の
   タイムゾーン違反（`NAIVE_OR_FOREIGN_TZ`）は正規化の段階で `UtcTime` が拒否するため、
   ここに到達する足は常に UTC である。
2. **カレンダー照合**（D03 §4 の 5）: 存在すべき足の欠落、休場時間帯の足、銘柄間の足境界
   のずれ、DST 切替週の異常。いずれも人間の判断を要する警告（`WARN`）として報告する。

検査結果の並びは `IntegrityReport` が D03 §3.7.1 の整列鍵で正規化するので、検査の実行順は
ダイジェストに影響しない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = ["SeriesUnderCheck", "check_series", "check_all"]


@dataclass(frozen=True, slots=True)
class SeriesUnderCheck:
    """1系列ぶんの検査対象（D03 §4 の 4〜5）。"""

    series: SeriesId
    timeframe_def: TimeframeDefinition
    bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("SeriesUnderCheck.series must be a SeriesId")
        if not isinstance(self.timeframe_def, TimeframeDefinition):
            raise MarketDataValueError(
                "SeriesUnderCheck.timeframe_def must be a TimeframeDefinition"
            )
        if not isinstance(self.bars, tuple):
            raise MarketDataValueError("SeriesUnderCheck.bars must be a tuple")
        for bar in self.bars:
            if not isinstance(bar, Bar):
                raise MarketDataValueError("SeriesUnderCheck.bars must contain Bar")
            if bar.series != self.series:
                raise MarketDataValueError(
                    f"bar of {bar.series} does not belong to series {self.series}"
                )


def _duplicate_findings(target: SeriesUnderCheck) -> list[CheckResult]:
    """同一の開始時刻を持つ足を重複として報告する（`DUPLICATE_TIMESTAMP`）。"""
    counts: dict[UtcTime, int] = {}
    for bar in target.bars:
        counts[bar.bar_start] = counts.get(bar.bar_start, 0) + 1
    findings: list[CheckResult] = []
    for start, count in counts.items():
        if count > 1:
            findings.append(
                CheckResult.create(
                    CheckKind.DUPLICATE_TIMESTAMP,
                    target.series,
                    Interval(start=start, end=start + timedelta(microseconds=1)),
                    detail={"rows": str(count)},
                )
            )
    return findings


def _alignment_findings(target: SeriesUnderCheck, calendar: TradingCalendar) -> list[CheckResult]:
    """定義の整列に合わない足を報告する（`IRREGULAR_INTERVAL`）。

    **区間の両端**を比較する。開始だけを見ていると、開始は整列に合うのに終端が違う足
    （隣の足と重なる、名目より長い・短い）を見逃す。そうした足は履歴窓の本数や集約の
    構成足を狂わせるので、重大な違反として扱う。

    比較する相手は、その足が取るべき期待区間（`expected_interval`、短縮セッションで切り
    詰め済み）である。カレンダー上その時間帯に足が存在しない場合（休場帯）は、期待区間が
    無いので整列上の区間（`boundaries`）と比較する。「休場帯に足がある」こと自体は
    `UNEXPECTED_BAR` が別に報告するので、ここでは区間の形だけを見る。

    DST 切替日の長短は整列規則と期待区間が織り込んでいるので、名目長との一致は要求しない
    （D03 §3.2）。
    """
    findings: list[CheckResult] = []
    for bar in target.bars:
        expected = target.timeframe_def.expected_interval(calendar, bar.bar_start)
        if expected is None:
            expected = target.timeframe_def.boundaries(bar.bar_start)
            reason = "outside_sessions_and_off_alignment"
        elif expected.start != bar.bar_start:
            reason = "bar_start_off_alignment"
        else:
            reason = "bar_end_off_alignment"
        if expected == bar.interval:
            continue
        findings.append(
            CheckResult.create(
                CheckKind.IRREGULAR_INTERVAL,
                target.series,
                bar.interval,
                detail={
                    "expected_interval": str(expected),
                    "reason": reason,
                },
            )
        )
    return findings


def _ohlc_findings(target: SeriesUnderCheck) -> list[CheckResult]:
    """OHLC の整合違反を報告する（`OHLC_INCONSISTENT`）。

    `Bar` の構築時に同じ不変条件を検査しているので、ここへ到達する足は通常すべて整合して
    いる。検査を残すのは、`Bar` を経由しない経路（将来の別 adapters）が入っても報告が
    空にならないようにするためで、報告には価格を入れない（D03 §3.9）。
    """
    findings: list[CheckResult] = []
    for bar in target.bars:
        inconsistent = (
            bar.low > bar.open
            or bar.low > bar.close
            or bar.high < bar.open
            or bar.high < bar.close
            or bar.low > bar.high
        )
        if inconsistent:  # pragma: no cover - Bar の構築時に拒否される
            findings.append(
                CheckResult.create(
                    CheckKind.OHLC_INCONSISTENT,
                    target.series,
                    bar.interval,
                    detail={"reason": "low_high_bounds_violated"},
                )
            )
    return findings


def _calendar_findings(target: SeriesUnderCheck, calendar: TradingCalendar) -> list[CheckResult]:
    """カレンダー照合の結果を返す（`MISSING_EXPECTED_BAR` / `UNEXPECTED_BAR`）。"""
    if not target.bars:
        return []
    ordered = sorted(target.bars, key=lambda bar: bar.bar_start.value)
    covered = Interval(start=ordered[0].bar_start, end=ordered[-1].bar_end)
    expected = set(calendar.expected_bar_starts(target.timeframe_def, covered))
    present = {bar.bar_start for bar in target.bars}

    findings: list[CheckResult] = []
    for start in expected - present:
        interval = target.timeframe_def.expected_interval(calendar, start)
        if interval is None:  # pragma: no cover - expected_bar_starts が除いている
            continue
        findings.append(CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, target.series, interval))
    for start in present - expected:
        bar_interval = next(bar.interval for bar in target.bars if bar.bar_start == start)
        findings.append(
            CheckResult.create(
                CheckKind.UNEXPECTED_BAR,
                target.series,
                bar_interval,
                detail={"reason": "outside_declared_sessions"},
            )
        )
    return findings


def _dst_findings(target: SeriesUnderCheck, calendar: TradingCalendar) -> list[CheckResult]:
    """夏時間の切替に掛かり、名目長と長さが違う足を記録する（`DST_BOUNDARY_ANOMALY`）。

    D03 §3.9 は「切替週の足数・境界が期待と異なる」ことを表す種別としている。区間が期待と
    **食い違う**足は `IRREGULAR_INTERVAL`（重大な違反）がすべて拾うので、この検査は
    「区間は正しいが、名目長と実際の長さが違う足」——つまり切替日の 23時間・25時間の日足、
    3時間・5時間の 4時間足——を人間が確認できるように残す役割に絞る。

    区間の正しさとは別に記録を残すのは、切替週の足数・長さが設定どおりかを目視で確かめ
    たいという運用上の要請があるためで、受入れの合否には影響しない警告である。整列に合う
    足だけを対象にするので、`IRREGULAR_INTERVAL` と二重に報告することはない。
    """
    findings: list[CheckResult] = []
    for bar in target.bars:
        expected = target.timeframe_def.expected_interval(calendar, bar.bar_start)
        if expected is None or expected != bar.interval:
            continue  # 区間の食い違いは IRREGULAR_INTERVAL の担当。
        aligned = target.timeframe_def.boundaries(bar.bar_start)
        if aligned.duration == target.timeframe_def.nominal_length:
            continue  # 切替に掛かっていない足は対象外。
        if bar.interval.duration == target.timeframe_def.nominal_length:
            continue  # 短縮セッションで名目長どおりに切り詰められた足は対象外。
        findings.append(
            CheckResult.create(
                CheckKind.DST_BOUNDARY_ANOMALY,
                target.series,
                bar.interval,
                detail={
                    "bar_duration_seconds": str(int(bar.interval.duration.total_seconds())),
                    "nominal_length_seconds": str(
                        int(target.timeframe_def.nominal_length.total_seconds())
                    ),
                },
            )
        )
    return findings


def _source_transition_findings(target: SeriesUnderCheck) -> list[CheckResult]:
    """出所（`source` 列）が切り替わる点を記録する（`SOURCE_TRANSITION`、INFO）。

    同じ系列の中で HistData 由来と Dukascopy 由来が入れ替わる境目を残す。価格そのものには
    影響しないが、後から「この期間の値はどちらの出所か」を追えるようにするための記録で、
    受入れの合否には影響しない（D03 §3.9 の INFO）。

    報告する区間は「切替の直前の足の開始から、切替後の足の終了まで」。境目がどの2本の間に
    あるかが区間だけで分かる形にしている。
    """
    if len(target.bars) < 2:
        return []
    ordered = sorted(target.bars, key=lambda bar: bar.bar_start.value)
    findings: list[CheckResult] = []
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if earlier.provenance.kind is later.provenance.kind:
            continue
        findings.append(
            CheckResult.create(
                CheckKind.SOURCE_TRANSITION,
                target.series,
                Interval(start=earlier.bar_start, end=later.bar_end),
                detail={
                    "from": earlier.provenance.kind.value,
                    "to": later.provenance.kind.value,
                },
            )
        )
    return findings


def _zero_volume_findings(target: SeriesUnderCheck) -> list[CheckResult]:
    """出来高 0 が連続する区間を記録する（`ZERO_VOLUME_SPAN`、INFO）。

    HistData 由来の出来高は 0 であり、これは真の市場出来高ではない（D03 §2）。0 を出来高
    として解釈してしまわないよう、連続する区間を1件にまとめて記録する。受入れの合否には
    影響しない（D03 §3.9 の INFO）。

    報告する詳細は足の本数だけで、価格は含めない（D03 §3.9）。
    """
    if not target.bars:
        return []
    ordered = sorted(target.bars, key=lambda bar: bar.bar_start.value)

    # 連続する 0 出来高の足を (開始, 終了, 本数) の区間へまとめてから報告する。
    spans: list[tuple[UtcTime, UtcTime, int]] = []
    current: tuple[UtcTime, UtcTime, int] | None = None
    for bar in ordered:
        if bar.volume != 0:
            if current is not None:
                spans.append(current)
                current = None
            continue
        if current is None:
            current = (bar.bar_start, bar.bar_end, 1)
        else:
            current = (current[0], bar.bar_end, current[2] + 1)
    if current is not None:
        spans.append(current)

    return [
        CheckResult.create(
            CheckKind.ZERO_VOLUME_SPAN,
            target.series,
            Interval(start=start, end=end),
            detail={"bars": str(count)},
        )
        for start, end, count in spans
    ]


def check_series(target: SeriesUnderCheck, calendar: TradingCalendar) -> tuple[CheckResult, ...]:
    """1系列の構造検査とカレンダー照合を行う（D03 §4 の 4〜5）。"""
    if not isinstance(calendar, TradingCalendar):
        raise MarketDataValueError("check_series requires a TradingCalendar")
    findings: list[CheckResult] = []
    findings.extend(_duplicate_findings(target))
    findings.extend(_ohlc_findings(target))
    findings.extend(_alignment_findings(target, calendar))
    findings.extend(_calendar_findings(target, calendar))
    findings.extend(_dst_findings(target, calendar))
    findings.extend(_source_transition_findings(target))
    findings.extend(_zero_volume_findings(target))
    return tuple(findings)


def _cross_symbol_findings(
    targets: Sequence[SeriesUnderCheck],
) -> list[CheckResult]:
    """同じ時間足で銘柄間の足境界がずれている場合に報告する（`CROSS_SYMBOL_MISALIGNMENT`）。

    比較は「同じ時間足定義を使う系列どうし」で行い、ある系列の足の開始時刻が、同じ期間を
    覆う**他のどの系列にも現れない**とき、その足を境界のずれとして報告する。

    片方の系列にしかない開始時刻でも、その系列の欠落・余剰は別の検査（存在すべき足の欠落、
    休場時間帯の足）がすでに報告している。ここで見たいのは「同じ時間足なのに銘柄によって
    足の刻み方が違う」ことなので、比較対象は「その時刻を含む期間を覆っている系列」に限り、
    まだデータが始まっていない・もう終わっている系列を比較相手にしない。
    """
    by_timeframe: dict[str, list[SeriesUnderCheck]] = {}
    for target in targets:
        by_timeframe.setdefault(target.timeframe_def.ref.id, []).append(target)

    findings: list[CheckResult] = []
    for group in by_timeframe.values():
        if len(group) < 2:
            continue
        starts_by_series = {id(target): {bar.bar_start for bar in target.bars} for target in group}
        spans: dict[int, Interval | None] = {}
        for target in group:
            starts = starts_by_series[id(target)]
            spans[id(target)] = (
                None
                if not starts
                else Interval(
                    start=min(starts, key=lambda value: value.value),
                    end=max(starts, key=lambda value: value.value) + timedelta(microseconds=1),
                )
            )

        for target in group:
            for bar in target.bars:
                comparable = [
                    other
                    for other in group
                    if other is not target
                    and (span := spans[id(other)]) is not None
                    and span.contains(bar.bar_start)
                ]
                if not comparable:
                    continue
                if any(bar.bar_start in starts_by_series[id(other)] for other in comparable):
                    continue
                findings.append(
                    CheckResult.create(
                        CheckKind.CROSS_SYMBOL_MISALIGNMENT,
                        target.series,
                        bar.interval,
                        detail={
                            "compared_with": ",".join(
                                sorted(str(other.series) for other in comparable)
                            )
                        },
                    )
                )
    return findings


def check_all(targets: Sequence[SeriesUnderCheck], calendar: TradingCalendar) -> IntegrityReport:
    """全系列の検査を行い、報告を組み立てる（D03 §4 の 4〜5）。

    結果は `IntegrityReport` が D03 §3.7.1 の整列鍵で並べ替えるので、`targets` の順序を
    入れ替えても同じ報告になる。
    """
    findings: list[CheckResult] = []
    for target in targets:
        findings.extend(check_series(target, calendar))
    findings.extend(_cross_symbol_findings(targets))
    return IntegrityReport(results=tuple(findings))


def severity_counts(report: IntegrityReport) -> Mapping[str, int]:
    """重大度ごとの件数（人間向けの要約に使う）。"""
    counts: dict[str, int] = {}
    for result in report.results:
        counts[result.severity.value] = counts.get(result.severity.value, 0) + 1
    return counts
