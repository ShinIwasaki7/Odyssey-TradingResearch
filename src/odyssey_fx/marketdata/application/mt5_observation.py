"""MT5 経路の snapshot の観測の索引（D03 §15.6 の 2・§15.7・§15.8、D06 §18.3〜§18.5）。

承認済みの snapshot の検査報告から、読み取り経路が使う 3 つの事実を索引にする。

- 無記録の足（`NO_RECORD_BAR`）の開始時刻（系列ごと）: as-of ビューが「準備不足」を返す足（D03
§15.7）。
- 無記録の分の区間（`UNRECORDED_MINUTES`。1 分足の系列）: 約定の側が再開の分を決める（D06 §18.4）。
- 取得不足の区間（`ACQUISITION_SHORTFALL`）: 能力検査が照合する（D06 §18.9）。

値の正本は検査報告（`integrity_report.json`）で、本索引はそれを引きやすくしただけである（D03
§15.8 の
「能力検査が検査報告から読む」）。HistData の経路の snapshot では `from_snapshot` が `None` を返す。
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import CheckKind, IntegrityReport
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.time_rule import TimeRuleTableRef

__all__ = ["Mt5MinuteView", "Mt5Observation", "Resumption"]

_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class Resumption:
    """再開の分 1 つ（D06 §18.4）: 無記録の後の最初の記録のある分と、その直前の無記録の区間。"""

    minute: UtcTime
    unrecorded_from: UtcTime

    @property
    def unrecorded_minutes(self) -> int:
        """直前の無記録の分数。"""
        return int((self.minute - self.unrecorded_from) / _MINUTE)


@dataclass(frozen=True, slots=True)
class Mt5Observation:
    """MT5 経路の snapshot の観測の索引。"""

    table_ref: TimeRuleTableRef
    no_record: Mapping[SeriesId, frozenset[UtcTime]]
    no_record_intervals: Mapping[SeriesId, tuple[Interval, ...]]
    unrecorded: Mapping[SeriesId, tuple[Interval, ...]]
    shortfall: Mapping[SeriesId, tuple[tuple[Interval, str], ...]]
    _unrecorded_starts: Mapping[SeriesId, tuple[UtcTime, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_unrecorded_starts",
            MappingProxyType(
                {
                    series: tuple(item.start for item in runs)
                    for series, runs in self.unrecorded.items()
                }
            ),
        )

    @classmethod
    def from_report(cls, report: IntegrityReport, table_ref: TimeRuleTableRef) -> Mt5Observation:
        """検査報告から索引を作る。"""
        no_record: dict[SeriesId, set[UtcTime]] = {}
        no_record_intervals: dict[SeriesId, list[Interval]] = {}
        unrecorded: dict[SeriesId, list[Interval]] = {}
        shortfall: dict[SeriesId, list[tuple[Interval, str]]] = {}
        for result in report.results:
            if result.kind is CheckKind.NO_RECORD_BAR:
                no_record.setdefault(result.series, set()).add(result.interval.start)
                no_record_intervals.setdefault(result.series, []).append(result.interval)
            elif result.kind is CheckKind.UNRECORDED_MINUTES:
                unrecorded.setdefault(result.series, []).append(result.interval)
            elif result.kind is CheckKind.ACQUISITION_SHORTFALL:
                reasons = dict(result.detail).get("reasons", "")
                shortfall.setdefault(result.series, []).append((result.interval, reasons))
        return cls(
            table_ref=table_ref,
            no_record=MappingProxyType({k: frozenset(v) for k, v in no_record.items()}),
            no_record_intervals=MappingProxyType(
                {
                    k: tuple(sorted(v, key=lambda i: i.start.value))
                    for k, v in no_record_intervals.items()
                }
            ),
            unrecorded=MappingProxyType(
                {k: tuple(sorted(v, key=lambda i: i.start.value)) for k, v in unrecorded.items()}
            ),
            shortfall=MappingProxyType(
                {k: tuple(sorted(v, key=lambda i: i[0].start.value)) for k, v in shortfall.items()}
            ),
        )

    @classmethod
    def from_snapshot(cls, snapshot: ReadableSnapshot) -> Mt5Observation | None:
        """MT5 経路の snapshot なら索引を作る。HistData の経路なら `None`。"""
        mt5 = snapshot.manifest.conversion.mt5
        if mt5 is None:
            return None
        return cls.from_report(snapshot.report, mt5.time_rule_table)

    # --- 読み取り ---------------------------------------------------------------

    def is_no_record(self, series: SeriesId, bar_start: UtcTime) -> bool:
        """その足が全分無記録の足（作られなかった足）か。"""
        return bar_start in self.no_record.get(series, frozenset())

    def unrecorded_in(self, series: SeriesId, interval: Interval) -> tuple[Interval, ...]:
        """`interval` と重なる無記録の区間（切り詰めた区間）。"""
        runs = self.unrecorded.get(series, ())
        starts = self._unrecorded_starts.get(series, ())
        index = max(bisect.bisect_right(starts, interval.start) - 1, 0)
        out: list[Interval] = []
        while index < len(runs) and runs[index].start < interval.end:
            run = runs[index]
            lo = max(run.start, interval.start, key=lambda value: value.value)
            hi = min(run.end, interval.end, key=lambda value: value.value)
            if lo < hi:
                out.append(Interval(start=lo, end=hi))
            index += 1
        return tuple(out)

    def is_unrecorded(self, series: SeriesId, minute: UtcTime) -> bool:
        """分 `minute` が無記録か。"""
        runs = self.unrecorded.get(series, ())
        starts = self._unrecorded_starts.get(series, ())
        index = bisect.bisect_right(starts, minute) - 1
        return index >= 0 and minute < runs[index].end

    def resumption_at(self, series: SeriesId, minute: UtcTime) -> Resumption | None:
        """`minute` が再開の分（直前の分が無記録）なら、その記録。"""
        before = minute - _MINUTE
        runs = self.unrecorded.get(series, ())
        starts = self._unrecorded_starts.get(series, ())
        index = bisect.bisect_right(starts, before) - 1
        if index < 0:
            return None
        run = runs[index]
        if run.end != minute:
            return None
        return Resumption(minute=minute, unrecorded_from=run.start)

    def resumptions_in(self, series: SeriesId, interval: Interval) -> tuple[Resumption, ...]:
        """区間の中の再開の分（無記録の区間の終わりが区間に入るもの）。

        無記録の区間の終わりの分に記録があるかは呼び出し側が 1 分足で確かめる（区間の終わりが
        取得不足・休場に接する場合は再開の分ではない）。
        """
        runs = self.unrecorded.get(series, ())
        starts = self._unrecorded_starts.get(series, ())
        index = max(bisect.bisect_right(starts, interval.start) - 1, 0)
        out: list[Resumption] = []
        while index < len(runs) and runs[index].start < interval.end:
            run = runs[index]
            if interval.start <= run.end < interval.end:
                out.append(Resumption(minute=run.end, unrecorded_from=run.start))
            index += 1
        return tuple(out)

    def shortfall_overlapping(
        self, series: SeriesId, interval: Interval
    ) -> tuple[tuple[Interval, str], ...]:
        """`interval` と重なる取得不足の区間（D06 §18.9 の手順 2）。"""
        return tuple(item for item in self.shortfall.get(series, ()) if item[0].overlaps(interval))


@dataclass(frozen=True, slots=True)
class Mt5MinuteView:
    """執行系列の銘柄の 1 分足と無記録の分（約定の側のポート。D06 §18.3〜§18.5）。

    `IntrabarSeries` と同じく、許可された partition の 1 分足だけを持つ（関門は読み込み側）。
    再開の分は「無記録の区間の終わりで、その分に 1 分足があるもの」（D06 §18.4）。
    """

    observation: Mt5Observation
    minute_series: SeriesId
    bars: tuple[Bar, ...]
    _starts: tuple[UtcTime, ...] = field(init=False, repr=False, compare=False)
    _by_start: Mapping[UtcTime, Bar] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.bars, key=lambda bar: bar.bar_start.value))
        object.__setattr__(self, "bars", ordered)
        object.__setattr__(self, "_starts", tuple(bar.bar_start for bar in ordered))
        object.__setattr__(
            self, "_by_start", MappingProxyType({bar.bar_start: bar for bar in ordered})
        )

    def minute_bars_in(self, interval: Interval) -> tuple[Bar, ...]:
        """区間に入る記録のある 1 分足（時刻順）。"""
        lo = bisect.bisect_left(self._starts, interval.start)
        hi = bisect.bisect_left(self._starts, interval.end)
        return self.bars[lo:hi]

    def minute_bar_at(self, minute: UtcTime) -> Bar | None:
        """その分の 1 分足（無ければ `None`）。"""
        return self._by_start.get(minute)

    def last_bar_before(self, moment: UtcTime) -> Bar | None:
        """`moment` より前に始まる最後の 1 分足。"""
        index = bisect.bisect_left(self._starts, moment) - 1
        return self.bars[index] if index >= 0 else None

    def resumption_at(self, minute: UtcTime) -> Resumption | None:
        """`minute` が再開の分なら、その記録（1 分足がある分だけ）。"""
        if minute not in self._by_start:
            return None
        return self.observation.resumption_at(self.minute_series, minute)

    def resumptions_in(self, interval: Interval) -> tuple[Resumption, ...]:
        """区間の中の再開の分（1 分足がある分だけ）。"""
        return tuple(
            item
            for item in self.observation.resumptions_in(self.minute_series, interval)
            if item.minute in self._by_start
        )

    def is_unrecorded(self, minute: UtcTime) -> bool:
        """分 `minute` が無記録か。"""
        return self.observation.is_unrecorded(self.minute_series, minute)
