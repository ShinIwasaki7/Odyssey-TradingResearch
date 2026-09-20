"""取引カレンダー（D03 §3.4）。

週の開閉（ニューヨーク現地の日曜 17:00 開始・金曜 17:00 終了）と、明示的に宣言した休場・
短縮を持ち、「いつ市場が開いているか」「ある時間足でどの足が存在すべきか」を答える。

**休場は宣言制**（D03 §3.4）。土日を UTC で一律に除外しない。受入れの検査で見つかった
「足が存在すべきなのに無い区間」は、人間が「休場（カレンダーへ追加して版を上げる）」か
「データ欠損（そのまま欠損として扱う）」に分類し、結果を manifest に残す。

`Calendar` ポート（`backtest.application.ports`、D01 §4）はこの domain 型そのものが
満たす。ポート定義は import せず、構造的に満たす（D01 §2.2 規則7）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

if TYPE_CHECKING:
    from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = ["ClosureRule", "TradingCalendar", "WeeklyMoment"]

#: 週境界の探索で前後に見る日数。週の開閉の両端を必ず含むよう8日ぶん見る。
_WEEK_SEARCH_DAYS = 8


def _resolve_local(local_date: date, moment: time, tz: ZoneInfo) -> UtcTime:
    """現地の日付・時刻を UTC へ変換する（D03 §3.2 の解決規則と同じ）。

    2度現れる現地時刻は最初の出現、存在しない現地時刻は次に存在する瞬間を採る。
    """
    naive = datetime.combine(local_date, moment)
    try:
        return UtcTime.from_local(naive, tz, fold=0)
    except KernelValueError:
        candidate = naive
        for _ in range(24 * 60):
            candidate = candidate + timedelta(minutes=1)
            try:
                return UtcTime.from_local(candidate, tz, fold=0)
            except KernelValueError:
                continue
        raise MarketDataValueError(  # pragma: no cover - DST の飛びは高々数時間
            f"no existing local instant at or after {naive.isoformat()} in {tz.key}"
        ) from None


@dataclass(frozen=True, slots=True)
class WeeklyMoment:
    """週内の現地時点（D03 §3.4）。

    `weekday` は `date.weekday()` と同じ 0=月曜 … 6=日曜。
    """

    weekday: int
    at: time

    def __post_init__(self) -> None:
        if isinstance(self.weekday, bool) or not isinstance(self.weekday, int):
            raise MarketDataValueError(f"WeeklyMoment.weekday must be an int, got {self.weekday!r}")
        if not 0 <= self.weekday <= 6:
            raise MarketDataValueError(
                f"WeeklyMoment.weekday must be 0 (Monday) .. 6 (Sunday), got {self.weekday}"
            )
        if not isinstance(self.at, time) or self.at.tzinfo is not None:
            raise MarketDataValueError("WeeklyMoment.at must be a naive local time")


@dataclass(frozen=True, slots=True)
class ClosureRule:
    """宣言した休場・短縮（D03 §3.4）。

    `local_date` は現地日付、`closed` はその日のうち市場が閉じている現地時刻の区間
    （`[start, end)`）。終日休場は `time(0, 0)` から `time(0, 0)` の翌日までではなく、
    `covers_whole_day=True` で表す（日付をまたぐ表現を避けるため）。
    """

    local_date: date
    covers_whole_day: bool = False
    start: time | None = None
    end: time | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.local_date, date) or isinstance(self.local_date, datetime):
            raise MarketDataValueError("ClosureRule.local_date must be a date")
        if not isinstance(self.covers_whole_day, bool):
            raise MarketDataValueError("ClosureRule.covers_whole_day must be a bool")
        if self.covers_whole_day:
            if self.start is not None or self.end is not None:
                raise MarketDataValueError(
                    "ClosureRule with covers_whole_day=True must not carry start/end"
                )
        else:
            if not isinstance(self.start, time) or not isinstance(self.end, time):
                raise MarketDataValueError(
                    "ClosureRule requires naive local start and end times"
                    " unless covers_whole_day is True"
                )
            if self.start.tzinfo is not None or self.end.tzinfo is not None:
                raise MarketDataValueError("ClosureRule.start / end must be naive local times")
            if not self.start < self.end:
                raise MarketDataValueError(
                    f"ClosureRule requires start < end, got {self.start} .. {self.end}"
                )
        if not isinstance(self.note, str):
            raise MarketDataValueError("ClosureRule.note must be a str")

    def utc_interval(self, tz: ZoneInfo) -> Interval:
        """休場区間を UTC の半開区間として返す。"""
        if self.covers_whole_day:
            start = _resolve_local(self.local_date, time(0, 0), tz)
            end = _resolve_local(self.local_date + timedelta(days=1), time(0, 0), tz)
        else:
            assert self.start is not None and self.end is not None  # noqa: S101 - 構築時に保証
            start = _resolve_local(self.local_date, self.start, tz)
            end = _resolve_local(self.local_date, self.end, tz)
        return Interval(start=start, end=end)

    def sort_key(self) -> tuple[str, str, str]:
        """整列鍵（現地日付、開始、終了）。宣言順に依存しない記録のため。"""
        return (
            self.local_date.isoformat(),
            "" if self.start is None else self.start.isoformat(),
            "" if self.end is None else self.end.isoformat(),
        )


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """取引カレンダー（D03 §3.4）。

    `weekly_open` から `weekly_close` までを開場とし、`closures` で宣言した区間をそこから
    取り除く。土日の扱いも `weekly_open` / `weekly_close` から導かれるのであり、UTC の
    曜日判定では決めない（D03 §3.4、上位設計書 §4.3.10）。
    """

    id: str
    version: int
    tz: ZoneInfo
    weekly_open: WeeklyMoment
    weekly_close: WeeklyMoment
    closures: tuple[ClosureRule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise MarketDataValueError(
                f"TradingCalendar.id must be a non-empty str, got {self.id!r}"
            )
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise MarketDataValueError(
                f"TradingCalendar.version must be an int, got {self.version!r}"
            )
        if self.version < 1:
            raise MarketDataValueError(f"TradingCalendar.version must be >= 1, got {self.version}")
        if not isinstance(self.tz, ZoneInfo):
            raise MarketDataValueError("TradingCalendar.tz must be a ZoneInfo")
        if not isinstance(self.weekly_open, WeeklyMoment):
            raise MarketDataValueError("TradingCalendar.weekly_open must be a WeeklyMoment")
        if not isinstance(self.weekly_close, WeeklyMoment):
            raise MarketDataValueError("TradingCalendar.weekly_close must be a WeeklyMoment")
        if not isinstance(self.closures, tuple):
            raise MarketDataValueError("TradingCalendar.closures must be a tuple")
        for closure in self.closures:
            if not isinstance(closure, ClosureRule):
                raise MarketDataValueError("TradingCalendar.closures must contain ClosureRule")

        # 休場の宣言順は意味を持たないので、現地日付・区間の順に正規化する。
        # ダイジェスト対象（カレンダー版）の内容が宣言順に左右されないようにするため。
        normalized = tuple(sorted(self.closures, key=lambda rule: rule.sort_key()))
        if normalized != self.closures:
            object.__setattr__(self, "closures", normalized)

    # --- 週の開閉 -----------------------------------------------------------

    def _weekly_sessions(self, window: Interval) -> list[Interval]:
        """`window` と重なる「週の開場区間」を昇順で返す（休場は未適用）。"""
        local_day = window.start.value.astimezone(self.tz).date()
        sessions: list[Interval] = []
        for offset in range(-_WEEK_SEARCH_DAYS, _WEEK_SEARCH_DAYS + 1):
            day = local_day + timedelta(days=offset)
            if day.weekday() != self.weekly_open.weekday:
                continue
            open_at = _resolve_local(day, self.weekly_open.at, self.tz)
            close_day = day + timedelta(days=self._days_to_close())
            close_at = _resolve_local(close_day, self.weekly_close.at, self.tz)
            if close_at <= open_at:  # pragma: no cover - 構築時の曜日組合せで排除される
                continue
            session = Interval(start=open_at, end=close_at)
            if session.overlaps(window):
                sessions.append(session)
        return sorted(sessions, key=lambda interval: interval.start.value)

    def _days_to_close(self) -> int:
        """週の開始曜日から終了曜日までの日数（同じ曜日なら7日後）。"""
        delta = (self.weekly_close.weekday - self.weekly_open.weekday) % 7
        return delta if delta else 7

    def _closure_intervals(self, window: Interval) -> list[Interval]:
        """`window` と重なる宣言済み休場を返す。"""
        intervals: list[Interval] = []
        for closure in self.closures:
            interval = closure.utc_interval(self.tz)
            if interval.overlaps(window):
                intervals.append(interval)
        return sorted(intervals, key=lambda interval: interval.start.value)

    # --- 問い合わせ ---------------------------------------------------------

    def is_open(self, moment: UtcTime) -> bool:
        """`moment` に市場が開いているか（D03 §3.4）。"""
        if not isinstance(moment, UtcTime):
            raise MarketDataValueError("TradingCalendar.is_open requires a UtcTime")
        probe = Interval(start=moment, end=moment + timedelta(microseconds=1))
        for session in self._weekly_sessions(probe):
            if not session.contains(moment):
                continue
            for closure in self._closure_intervals(probe):
                if closure.contains(moment):
                    return False
            return True
        return False

    def sessions(self, window: Interval) -> tuple[Interval, ...]:
        """`window` 内の取引セッション（休場を取り除いた開場区間）を昇順で返す。"""
        if not isinstance(window, Interval):
            raise MarketDataValueError("TradingCalendar.sessions requires an Interval")
        open_parts: list[Interval] = []
        for session in self._weekly_sessions(window):
            start = max(session.start, window.start, key=lambda value: value.value)
            end = min(session.end, window.end, key=lambda value: value.value)
            if start < end:
                open_parts.append(Interval(start=start, end=end))

        for closure in self._closure_intervals(window):
            remaining: list[Interval] = []
            for part in open_parts:
                if not part.overlaps(closure):
                    remaining.append(part)
                    continue
                if part.start < closure.start:
                    remaining.append(Interval(start=part.start, end=closure.start))
                if closure.end < part.end:
                    remaining.append(Interval(start=closure.end, end=part.end))
            open_parts = remaining

        return tuple(sorted(open_parts, key=lambda interval: interval.start.value))

    def expected_bar_starts(
        self, timeframe_def: TimeframeDefinition, window: Interval
    ) -> tuple[UtcTime, ...]:
        """`window` 内でその時間足の足が存在すべき開始時刻を昇順で返す（D03 §3.4）。

        判定は `TimeframeDefinition.expected_interval()` による。整列上の区間が丸ごと
        休場に入る足は存在しないものとして除き、末尾だけが休場に掛かる足（短縮セッション）
        は切り詰めた区間で存在するものとして数える（D03 §3.2）。

        `window` に部分的にしか入らない足も、その開始時刻が `window` 内にあれば返す。
        """
        if not isinstance(window, Interval):
            raise MarketDataValueError("expected_bar_starts requires an Interval")
        starts: list[UtcTime] = []
        current = timeframe_def.boundaries(window.start).start
        while current < window.end:
            aligned = timeframe_def.boundaries(current)
            if window.start <= aligned.start:
                if timeframe_def.expected_interval(self, aligned.start) is not None:
                    starts.append(aligned.start)
            current = aligned.end
        return tuple(starts)
