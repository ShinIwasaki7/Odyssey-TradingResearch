"""取引カレンダー（D03 §3.4）。

週の開閉（ニューヨーク現地の日曜 17:00 開始・金曜 17:00 終了）と、明示的に宣言した休場・
短縮、および営業例外（v1.7）を持ち、「いつ市場が開いているか」「ある時間足でどの足が存在
すべきか」を答える。

**休場は宣言制**（D03 §3.4）。土日を UTC で一律に除外しない。受入れの検査で見つかった
「足が存在すべきなのに無い区間」は、人間が「休場（カレンダーへ追加して版を上げる）」か
「データ欠損（そのまま欠損として扱う）」に分類し、結果を manifest に残す。

**営業例外も宣言制**（D03 §3.4 v1.7）。週の休場時間帯（例: 日曜 17:00 前）に足がある区間は
「休場帯の足」として報告され、人間が「カレンダー側の営業例外（`openings` へ追加して版を
上げる）」か「セッション外データ異常（足を除外する）」に分類する。営業例外は通常の週の
開場区間に**接するか重なる**ものだけを許し、離れたものと休場に重なるものは構築時に拒否する
（2026-09-24 の人間の決定）。

`Calendar` ポート（`backtest.application.ports`、D01 §4）はこの domain 型そのものが
満たす。ポート定義は import せず、構造的に満たす（D01 §2.2 規則7）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

if TYPE_CHECKING:
    from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = ["ClosureRule", "OpeningRule", "TradingCalendar", "WeeklyMoment"]

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


#: 週の開場区間の覚え書きの大きさ。1銘柄10年ぶんで約 540 週、10銘柄でも同じ週を共有する
#: ので、数千件あれば受入れ全体を覆える。上限を設けるのは、長期運用で際限なく増えない
#: ようにするためで、溢れても計算結果は変わらない（単に計算し直すだけ）。
_SESSION_CACHE_SIZE = 8192


@lru_cache(maxsize=_SESSION_CACHE_SIZE)
def _weekly_session(
    tz_key: str,
    open_weekday: int,
    open_at: time,
    close_weekday: int,
    close_at: time,
    open_day: date,
) -> Interval | None:
    """週の開始日1つぶんの開場区間を求める（休場は未適用）。

    **純粋関数**である。同じ引数なら常に同じ結果を返すので、結果を覚えておける。現地時刻
    から UTC への変換（`_resolve_local`）は夏時間の解決を含んで重く、受入れでは同じ週が
    何万回も引かれるため、ここで覚えることで受入れ全体の時間が実用的になる。

    引数を `TradingCalendar` そのものではなく**ハッシュ可能な値に分解して**受けるのは、
    覚え書きをカレンダーの外に置くためである。カレンダーは不変の値型（D02 §3）であり、
    正規化エンコード（D02 §9.3）は全フィールドを符号化するので、覚え書きをフィールドに
    すると正規形が問い合わせの有無で変わってしまう。

    `closures`（宣言した休場）は鍵に含めない。本関数が返すのは休場を**適用する前**の週の
    開場区間であり、休場の取り除きは `sessions()` が別に行うためである。休場が違うだけの
    カレンダーどうしは同じ週の開場区間を持つので、覚え書きを共有してよい。
    """
    tz = ZoneInfo(tz_key)
    open_moment = _resolve_local(open_day, open_at, tz)
    # 週の開始曜日から終了曜日までの日数（同じ曜日なら7日後）。
    delta = (close_weekday - open_weekday) % 7
    close_moment = _resolve_local(open_day + timedelta(days=delta or 7), close_at, tz)
    if close_moment <= open_moment:  # pragma: no cover - 構築時の曜日組合せで排除される
        return None
    return Interval(start=open_moment, end=close_moment)


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
class OpeningRule:
    """宣言した営業例外（D03 §3.4 v1.7）。

    週の休場時間帯のうち、市場が開いていた現地日付と区間（`[start, end)`）。人間が受入れの
    「休場帯の足」（`UNEXPECTED_BAR`）を「カレンダー側の営業例外」（`CALENDAR_EXCEPTION`）と
    分類したときにカレンダーへ追加し、版を上げる。

    D03 §3.4 は営業例外を「現地日付と区間」と定めるので、休場（`ClosureRule`）の短縮の形と
    同じく `start < end` の現地時刻で書く。終日の形は持たない。通常の週の開場区間に接する
    ことが条件（`TradingCalendar` の構築時検証）なので、日付をまたがない1日内の区間で足りる。
    """

    local_date: date
    start: time
    end: time
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.local_date, date) or isinstance(self.local_date, datetime):
            raise MarketDataValueError("OpeningRule.local_date must be a date")
        if not isinstance(self.start, time) or not isinstance(self.end, time):
            raise MarketDataValueError("OpeningRule requires naive local start and end times")
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise MarketDataValueError("OpeningRule.start / end must be naive local times")
        if not self.start < self.end:
            raise MarketDataValueError(
                f"OpeningRule requires start < end, got {self.start} .. {self.end}"
            )
        if not isinstance(self.note, str):
            raise MarketDataValueError("OpeningRule.note must be a str")

    def utc_interval(self, tz: ZoneInfo) -> Interval:
        """営業例外の区間を UTC の半開区間として返す。"""
        return Interval(
            start=_resolve_local(self.local_date, self.start, tz),
            end=_resolve_local(self.local_date, self.end, tz),
        )

    def sort_key(self) -> tuple[str, str, str]:
        """整列鍵（現地日付、開始、終了）。宣言順に依存しない記録のため。"""
        return (self.local_date.isoformat(), self.start.isoformat(), self.end.isoformat())


@lru_cache(maxsize=_SESSION_CACHE_SIZE)
def _opening_interval(tz_key: str, rule: OpeningRule) -> Interval:
    """営業例外の UTC 区間（純粋関数なので覚えておける）。

    `sessions()` は受入れで足1本ごとに呼ばれ、そのたびに全営業例外を UTC へ変換すると
    重い。`_weekly_session` と同じく、覚え書きはカレンダーの外に置く（不変の値型の正規形を
    問い合わせの有無で変えないため）。
    """
    return rule.utc_interval(ZoneInfo(tz_key))


def _touches(first: Interval, second: Interval) -> bool:
    """2つの半開区間が重なるか、端で接するか（あいだに隙間が無いか）。"""
    return first.start <= second.end and second.start <= first.end


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """取引カレンダー（D03 §3.4）。

    `weekly_open` から `weekly_close` までを開場とし、`openings` で宣言した営業例外を
    加え、`closures` で宣言した区間を取り除く。土日の扱いも `weekly_open` /
    `weekly_close` から導かれるのであり、UTC の曜日判定では決めない（D03 §3.4、上位設計書
    §4.3.10）。

    営業例外は構築時に2点を検証する（D03 §3.4 v1.7、`MarketDataValueError`）。

    1. 休場（`closures`）と重ならない。同じ時刻を「開いている」と「閉じている」の両方で
       宣言すると、どちらを採るかが宣言から決まらない。
    2. 通常の週の開場区間（`weekly_open`〜`weekly_close`）に接するか重なる。離れた営業例外を
       許すと、`weekly_session_at` の戻り値が単一の区間に定まらない（2026-09-24 の人間の
       決定。必要になれば D03 の改訂で緩める）。
    """

    id: str
    version: int
    tz: ZoneInfo
    weekly_open: WeeklyMoment
    weekly_close: WeeklyMoment
    closures: tuple[ClosureRule, ...] = ()
    openings: tuple[OpeningRule, ...] = ()

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

        if not isinstance(self.openings, tuple):
            raise MarketDataValueError("TradingCalendar.openings must be a tuple")
        for opening in self.openings:
            if not isinstance(opening, OpeningRule):
                raise MarketDataValueError("TradingCalendar.openings must contain OpeningRule")
        normalized_openings = tuple(sorted(self.openings, key=lambda rule: rule.sort_key()))
        if normalized_openings != self.openings:
            object.__setattr__(self, "openings", normalized_openings)
        self._validate_openings()

    def _validate_openings(self) -> None:
        """営業例外の宣言を検証する（D03 §3.4 v1.7）。

        休場と重なる宣言と、通常の週の開場区間に接しも重なりもしない（離れた）宣言を
        拒否する。どちらも `closures` の不正な宣言と同じ `MarketDataValueError` である。
        """
        closure_intervals = [closure.utc_interval(self.tz) for closure in self.closures]
        for opening in self.openings:
            interval = opening.utc_interval(self.tz)
            for closure, closure_interval in zip(self.closures, closure_intervals, strict=True):
                if interval.overlaps(closure_interval):
                    raise MarketDataValueError(
                        f"the opening on {opening.local_date} {opening.start}..{opening.end}"
                        f" overlaps the closure on {closure.local_date}; a moment cannot be"
                        " declared both open and closed (D03 §3.4)"
                    )
            if not self._regular_near(interval):
                raise MarketDataValueError(
                    f"the opening on {opening.local_date} {opening.start}..{opening.end}"
                    " neither touches nor overlaps the regular weekly session; a detached"
                    " opening is rejected so that weekly_session_at stays a single interval"
                    " (D03 §3.4)"
                )

    # --- 週の開閉 -----------------------------------------------------------

    def _session_for_open_day(self, open_day: date) -> Interval | None:
        """週の開始日（現地日付）1つぶんの開場区間を返す（休場は未適用）。

        計算そのものはモジュールレベルの純粋関数（`_weekly_session`）が行い、その関数が
        結果を覚えている。覚え書きを**カレンダーの中に持たない**のは、`TradingCalendar`
        が不変の値型だからである（D02 §3）。フィールドとして持つと、(1) 構築時に外から
        細工した覚え書きを渡せてしまい、(2) 正規化エンコード（D02 §9.3）が全フィールドを
        符号化するため、同じ宣言のカレンダーでも「問い合わせ済みかどうか」で正規形が
        変わってしまう。
        """
        return _weekly_session(
            self.tz.key,
            self.weekly_open.weekday,
            self.weekly_open.at,
            self.weekly_close.weekday,
            self.weekly_close.at,
            open_day,
        )

    def _weekly_sessions(self, window: Interval) -> list[Interval]:
        """`window` と重なる「週の開場区間」を昇順で返す（休場は未適用）。

        探索範囲は**窓の長さから決める**。固定日数で探索すると、長い窓の後半にある週が
        まるごと抜け落ちる（30日の窓で5件あるはずのセッションが2件しか返らない）。
        `sessions()` は取引カレンダーの公開 API（D03 §3.4、バックテスト側の `Calendar`
        ポート）なので、窓の長さによらず全体を覆う必要がある。

        前後に `_WEEK_SEARCH_DAYS` の余裕を取るのは、窓の開始より前に始まって窓に掛かる
        週と、窓の終了後に終わる週の両方を拾うため。

        走査する日のうち週の開始曜日に当たるものだけを見るのは以前と同じで、その1日ぶんの
        区間の計算を `_session_for_open_day` に任せて覚えさせている。返す内容は変わらない。
        """
        local_day = window.start.value.astimezone(self.tz).date()
        window_days = int(window.duration.total_seconds() // 86400) + 1
        sessions: list[Interval] = []
        # 走査の起点を週の開始曜日へ寄せ、当たらない日を1日ずつ見る無駄を省く。範囲の
        # 両端は以前と同じで、拾う週も同じになる。
        first = local_day - timedelta(days=_WEEK_SEARCH_DAYS)
        last = local_day + timedelta(days=window_days + _WEEK_SEARCH_DAYS)
        first += timedelta(days=(self.weekly_open.weekday - first.weekday()) % 7)
        day = first
        while day <= last:
            session = self._session_for_open_day(day)
            if session is not None and session.overlaps(window):
                sessions.append(session)
            day += timedelta(days=7)
        return sorted(sessions, key=lambda interval: interval.start.value)

    def _regular_near(self, interval: Interval) -> list[Interval]:
        """`interval` と重なるか端で接する通常の週の開場区間（休場・営業例外は未適用）。

        `_weekly_sessions` は重なる区間だけを返すので、探索窓を前後に 1 マイクロ秒ずつ
        広げて端で接する区間も拾い、そのうえで接するか重なるものに絞る。
        """
        probe = Interval(
            start=interval.start - timedelta(microseconds=1),
            end=interval.end + timedelta(microseconds=1),
        )
        return [session for session in self._weekly_sessions(probe) if _touches(session, interval)]

    def _opening_intervals(self, window: Interval) -> list[Interval]:
        """`window` と重なるか端で接する宣言済み営業例外を返す。"""
        intervals: list[Interval] = []
        for opening in self.openings:
            interval = _opening_interval(self.tz.key, opening)
            if _touches(interval, window):
                intervals.append(interval)
        return sorted(intervals, key=lambda interval: interval.start.value)

    def _open_hull(self, anchor: Interval) -> Interval:
        """`anchor`（通常の週の開場区間）に、接するか重なる営業例外を連結した区間。

        営業例外は通常の週の開場区間に接するか重なることを構築時に検証してあるので、連結の
        結果は常に1つの区間になる（D03 §3.4 v1.7）。
        """
        start, end = anchor.start, anchor.end
        for opening in self._opening_intervals(anchor):
            start = min(start, opening.start, key=lambda value: value.value)
            end = max(end, opening.end, key=lambda value: value.value)
        return Interval(start=start, end=end)

    def _closure_intervals(self, window: Interval) -> list[Interval]:
        """`window` と重なる宣言済み休場を返す。"""
        intervals: list[Interval] = []
        for closure in self.closures:
            interval = closure.utc_interval(self.tz)
            if interval.overlaps(window):
                intervals.append(interval)
        return sorted(intervals, key=lambda interval: interval.start.value)

    def _sessions_with_openings(self, window: Interval) -> list[Interval]:
        """`window` と重なる「営業例外で広げた週の開場区間」を昇順で返す（休場は未適用）。

        営業例外は週の開場区間の外側へ広がるので、窓の外にある週の開場区間に接する営業例外
        が窓に掛かる場合がある。そのため週の開場区間は、窓と重なる営業例外が接するものも
        拾う。
        """
        anchors = {session.start.value: session for session in self._weekly_sessions(window)}
        for opening in self._opening_intervals(window):
            for session in self._regular_near(opening):
                anchors.setdefault(session.start.value, session)
        hulls = [self._open_hull(session) for session in anchors.values()]
        return sorted(
            (hull for hull in hulls if hull.overlaps(window)),
            key=lambda interval: interval.start.value,
        )

    # --- 問い合わせ ---------------------------------------------------------

    def is_open(self, moment: UtcTime) -> bool:
        """`moment` に市場が開いているか（D03 §3.4）。"""
        if not isinstance(moment, UtcTime):
            raise MarketDataValueError("TradingCalendar.is_open requires a UtcTime")
        probe = Interval(start=moment, end=moment + timedelta(microseconds=1))
        in_session = any(session.contains(moment) for session in self._weekly_sessions(probe))
        in_opening = any(opening.contains(moment) for opening in self._opening_intervals(probe))
        if not (in_session or in_opening):
            return False
        return not any(closure.contains(moment) for closure in self._closure_intervals(probe))

    def weekly_session_at(self, moment: UtcTime) -> Interval | None:
        """`moment` を含む**休場を適用する前の**週の開場区間を返す（D03 §3.4）。

        週の開閉（日曜 17:00 開始・金曜 17:00 終了）で決まる区間であり、宣言した休場や
        短縮は取り除かない。**営業例外（`openings`）は含める**（D03 §3.4 v1.7、2026-09-24
        の人間の決定）。営業例外が週の開場区間に接するか重なるなら両者を連結した区間を返し、
        `moment` が営業例外の区間に入るときもその連結した区間を返す。`moment` がどの週の
        開場区間（営業例外で広げたものを含む）にも入らないなら `None`。

        `sessions()` との違いが本操作の存在理由である。`sessions()` は休場を取り除くので、
        祝日を1日宣言すると週が2つに割れて見える。**週末をまたぐかどうかだけ**を知りたい
        利用側（バックテストの週末持ち越し禁止の判定、D06 §5.3）がそれを使うと、祝日や
        短縮セッションをまたぐ注文まで週末扱いになる。週の開閉は本型が持つ知識なので、
        利用側が曜日と時刻から計算し直すのではなく、本型が答える（同じ計算が2か所に割れると
        カレンダーの版を上げたときに片方だけ古くなる）。
        """
        if not isinstance(moment, UtcTime):
            raise MarketDataValueError("TradingCalendar.weekly_session_at requires a UtcTime")
        probe = Interval(start=moment, end=moment + timedelta(microseconds=1))
        anchors = [session for session in self._weekly_sessions(probe) if session.contains(moment)]
        for opening in self._opening_intervals(probe):
            if opening.contains(moment):
                # 営業例外の区間に入る時刻は、その営業例外が接する週の開場区間へ連結して
                # 答える（接することは構築時に検証済み）。
                anchors.extend(self._regular_near(opening))
        for anchor in sorted(anchors, key=lambda interval: interval.start.value):
            hull = self._open_hull(anchor)
            if hull.contains(moment):
                return hull
        return None

    def sessions(self, window: Interval) -> tuple[Interval, ...]:
        """`window` 内の取引セッションを昇順で返す（D03 §3.4）。

        `weekly_open`〜`weekly_close` に営業例外（`openings`）を加え、休場（`closures`）を
        取り除いた区間である。営業例外は接するか重なる週の開場区間へ連結する。週の開場区間
        どうしは（営業例外を介して接しない限り）連結しない。
        """
        if not isinstance(window, Interval):
            raise MarketDataValueError("TradingCalendar.sessions requires an Interval")
        open_parts: list[Interval] = []
        for session in self._sessions_with_openings(window):
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
