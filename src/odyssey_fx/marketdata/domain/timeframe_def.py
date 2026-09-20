"""時間足定義と足境界の計算（D03 §3.2）。

`common.TimeframeRef` が指す定義の本体。足境界の決め方は2種類ある。

- `FixedUtcAlignment`: UTC のエポックから名目長の整数倍で区切る（15m、1h）。この整列では
  足の長さは常に名目長に等しい。
- `SessionAlignment`: ニューヨーク現地時刻の起点（4h は 17/21/01/05/09/13 時、1d は 17 時）
  から区切り、夏時間（DST）を含む `zoneinfo` の規則で UTC へ変換する（D03 §3.2、
  上位設計書 §3.2）。固定 UTC 時刻は埋め込まない。**足の長さは固定ではない**: DST 切替日を
  含む日足は 23 時間または 25 時間、4h 足は 3 時間または 5 時間になる。

**DST 切替日の起点の解決規則**（D03 §3.2 の確定事項）:

- 現地時刻が2度現れる日（秋の切り戻し）は**最初の出現**（`fold=0`）を起点とする。
- 現地時刻が存在しない日（春の切り替え）は**次に存在する瞬間**を起点とする。

いずれも `UtcTime.from_local` に `fold` を明示して渡し、曖昧な変換を残さない。

足の妥当性は「区間の両端が整列規則の計算した境界に一致すること」で検証し、名目長との
一致は要求しない（D03 §3.2・§3.3）。カレンダーの短縮セッションを考慮した「その足が実際に
取るべき区間」は `expected_interval()` が返す（D03 §3.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

if TYPE_CHECKING:
    from odyssey_fx.marketdata.domain.calendar import TradingCalendar

__all__ = [
    "Alignment",
    "FixedUtcAlignment",
    "SessionAlignment",
    "TimeframeDefinition",
]

#: `FixedUtcAlignment` の整列の起点（UTC エポック）。
_EPOCH = UtcTime.from_components(1970, 1, 1)

#: 現地起点を探索するときに前後に見る日数。1日ぶんの前後で足りるが、日付変更線と
#: DST の組合せで起点が前日へずれる場合に備えて2日ぶん見る。
_LOCAL_SEARCH_DAYS = 2


class Alignment(Protocol):
    """足境界の決め方（D03 §3.2）。

    `boundaries(t)` は `t` を含む足の**整列上の**区間を返す。カレンダーは考慮しない。
    """

    def boundaries(self, moment: UtcTime) -> Interval:
        """`moment` を含む整列上の区間 `[bar_start, bar_end)` を返す。"""
        ...


@dataclass(frozen=True, slots=True)
class FixedUtcAlignment:
    """UTC のエポックから名目長の整数倍で整列する（D03 §3.2）。

    15m・1h が使う。この整列では足の長さは常に `step` に等しい。
    """

    step: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.step, timedelta):
            raise MarketDataValueError("FixedUtcAlignment.step must be a timedelta")
        if self.step <= timedelta(0):
            raise MarketDataValueError(f"FixedUtcAlignment.step must be > 0, got {self.step}")
        if self.step.microseconds:
            raise MarketDataValueError(
                f"FixedUtcAlignment.step must be a whole number of seconds, got {self.step}"
            )

    def boundaries(self, moment: UtcTime) -> Interval:
        """エポックからの整数倍で `moment` を含む区間を求める（D03 §3.2）。"""
        if not isinstance(moment, UtcTime):
            raise MarketDataValueError("boundaries() requires a UtcTime")
        elapsed = moment - _EPOCH
        step_seconds = int(self.step.total_seconds())
        elapsed_seconds = int(elapsed.total_seconds())
        # `//` は負の経過時間でも下向き（過去方向）に丸まるので、エポック以前でも
        # 「その時刻を含む区間」が得られる。
        index = elapsed_seconds // step_seconds
        start = _EPOCH + timedelta(seconds=index * step_seconds)
        return Interval(start=start, end=start + self.step)


def _resolve_local_anchor(local_date: date, anchor: time, tz: ZoneInfo) -> UtcTime:
    """現地の日付と起点時刻から UTC の境界を1つ求める（D03 §3.2 の解決規則）。

    夏時間の切替により、指定した現地時刻が2度現れる日と1度も現れない日がある。

    - 2度現れる日（秋の切り戻し）は最初の出現（`fold=0`）を採る。
    - 存在しない日（春の切り替え）は次に存在する瞬間を採る。1分ずつ進めて最初に存在する
      瞬間を探す（NY では 02:00〜02:59 が存在せず、03:00 が最初に存在する瞬間になる）。

    `UtcTime.from_local` は曖昧な時刻に `fold` の明示を要求し、存在しない時刻を拒否する
    （D02 §3.1）ので、その2つの拒否をこの規則で埋める。
    """
    naive = datetime.combine(local_date, anchor)
    try:
        return UtcTime.from_local(naive, tz, fold=0)
    except KernelValueError:
        # 存在しない現地時刻。次に存在する瞬間まで1分ずつ進める。
        # DST の進みは最大でも数時間なので、1日ぶん探せば必ず見つかる。
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
class SessionAlignment:
    """現地時刻の起点から境界を作る整列（D03 §3.2）。

    `anchors_local` は現地時刻の起点の列（4h は 17:00, 21:00, 01:00, 05:00, 09:00, 13:00、
    1d は 17:00）。境界は「現地の各日付 × 各起点」を UTC へ変換した時刻の列であり、
    固定 UTC 時刻は持たない。足の長さは DST 切替日に 23h/25h（日足）・3h/5h（4h 足）と
    変わる。
    """

    tz: ZoneInfo
    anchors_local: tuple[time, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.tz, ZoneInfo):
            raise MarketDataValueError("SessionAlignment.tz must be a ZoneInfo")
        if not isinstance(self.anchors_local, tuple) or not self.anchors_local:
            raise MarketDataValueError("SessionAlignment.anchors_local must be a non-empty tuple")
        for anchor in self.anchors_local:
            if not isinstance(anchor, time):
                raise MarketDataValueError(
                    "SessionAlignment.anchors_local must contain time values"
                )
            if anchor.tzinfo is not None:
                raise MarketDataValueError(
                    "SessionAlignment.anchors_local must contain naive local times"
                )
            if anchor.microsecond:
                raise MarketDataValueError(
                    f"SessionAlignment anchors must be whole seconds, got {anchor}"
                )
        if len(set(self.anchors_local)) != len(self.anchors_local):
            raise MarketDataValueError(
                f"SessionAlignment.anchors_local must not repeat a time, got {self.anchors_local}"
            )

        # 起点は現地時刻の昇順に正規化する。境界列の生成順が宣言順に左右されると、
        # 同じ定義を別の順で書いた設定が別の境界列を作りうるため。
        normalized = tuple(sorted(self.anchors_local))
        if normalized != self.anchors_local:
            object.__setattr__(self, "anchors_local", normalized)

    def _boundary_candidates(self, moment: UtcTime) -> list[UtcTime]:
        """`moment` の前後数日ぶんの境界を昇順で返す。"""
        local_day = moment.value.astimezone(self.tz).date()
        boundaries: list[UtcTime] = []
        for offset in range(-_LOCAL_SEARCH_DAYS, _LOCAL_SEARCH_DAYS + 1):
            day = local_day + timedelta(days=offset)
            for anchor in self.anchors_local:
                boundaries.append(_resolve_local_anchor(day, anchor, self.tz))
        # 春の切り替えで「次に存在する瞬間」へ寄せた結果、隣接する起点が同じ UTC 時刻へ
        # 潰れることがある。重複を落としてから昇順に並べる。
        return sorted(set(boundaries), key=lambda value: value.value)

    def boundaries(self, moment: UtcTime) -> Interval:
        """`moment` を含む整列上の区間を現地起点の列から求める（D03 §3.2）。"""
        if not isinstance(moment, UtcTime):
            raise MarketDataValueError("boundaries() requires a UtcTime")
        candidates = self._boundary_candidates(moment)
        for index in range(len(candidates) - 1):
            start = candidates[index]
            end = candidates[index + 1]
            if start <= moment < end:
                return Interval(start=start, end=end)
        raise MarketDataValueError(  # pragma: no cover - 探索幅が十分なため到達しない
            f"could not locate a session boundary containing {moment}"
        )


@dataclass(frozen=True, slots=True)
class TimeframeDefinition:
    """時間足の定義本体（D03 §3.2）。

    `nominal_length` は表示と履歴窓の概算にだけ使い、足の検証には使わない（DST 切替日の
    足は名目長と一致しないため）。
    """

    ref: TimeframeRef
    nominal_length: timedelta
    alignment: FixedUtcAlignment | SessionAlignment

    def __post_init__(self) -> None:
        if not isinstance(self.ref, TimeframeRef):
            raise MarketDataValueError("TimeframeDefinition.ref must be a TimeframeRef")
        if not isinstance(self.nominal_length, timedelta):
            raise MarketDataValueError("TimeframeDefinition.nominal_length must be a timedelta")
        if self.nominal_length <= timedelta(0):
            raise MarketDataValueError(
                f"TimeframeDefinition.nominal_length must be > 0, got {self.nominal_length}"
            )
        if not isinstance(self.alignment, (FixedUtcAlignment, SessionAlignment)):
            raise MarketDataValueError(
                "TimeframeDefinition.alignment must be a FixedUtcAlignment or SessionAlignment"
            )
        if (
            isinstance(self.alignment, FixedUtcAlignment)
            and self.alignment.step != self.nominal_length
        ):
            raise MarketDataValueError(
                f"FixedUtcAlignment.step ({self.alignment.step}) must equal"
                f" nominal_length ({self.nominal_length});"
                " a fixed-UTC bar is always exactly its nominal length (D03 §3.2)"
            )

    def boundaries(self, moment: UtcTime) -> Interval:
        """`moment` を含む足の整列上の区間（D03 §3.2）。カレンダーは考慮しない。"""
        return self.alignment.boundaries(moment)

    def expected_interval(self, calendar: TradingCalendar, moment: UtcTime) -> Interval | None:
        """`moment` を含む足が実際に取るべき区間（D03 §3.2）。

        整列上の区間をカレンダーの取引セッションで切り詰める。

        - 区間全体が休場なら、その足は存在しない（`None`）。
        - 区間の途中から休場が始まるなら、末尾を休場開始で切り詰める（短縮セッション）。
        - 区間の途中で開場する場合は、足の開始は整列上の開始のまま扱う。足の自然キーは
          `bar_start`（D03 §3.3）であり、開場遅れで開始ラベルを動かすと同じ足が別の鍵を
          持ってしまうため（上位設計書 §3.3「既存 index だけから本来の足境界を無条件に
          推定しない」）。

        足の検証と期待足の判定はこの区間を使う（D03 §3.2・§3.3・§5.2）。
        """
        aligned = self.boundaries(moment)
        sessions = calendar.sessions(aligned)
        if not sessions:
            return None
        # 区間内で市場が開いている最後の瞬間までが、その足が取るべき範囲。
        end = max(session.end for session in sessions)
        if end <= aligned.start:  # pragma: no cover - sessions() は重なりのみ返す
            return None
        return Interval(start=aligned.start, end=min(end, aligned.end))
