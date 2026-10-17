"""1 分ごとの区分: 休場・取得不足・記録あり・無記録（D03 §15.2、D12-75・D12-80）。

MT5 経路の受入れは、取引カレンダーのセッションの分すべてに次の順で区分を 1 つ付ける
（数え方の規則 `minute_status_order`。D03 §15.12）。

1. 休場（`CLOSED`）: セッションの外。足が存在すべき分にも数えない。
2. 取得不足（`ACQUISITION_SHORTFALL`）: セッションの中で、取得の範囲の外・要求の失敗や未取得・
   時刻を解釈できない区間の分。**無記録に読み替えない**（D12-80）。取得の範囲の最初より前は
   データの開始前として扱い、取得不足に数えない（その分は台帳の外）。
3. 記録あり（`RECORDED`）: 取得済みの系列にその分の 1 分足がある。
4. 無記録（`UNRECORDED`）: 取得済みの範囲の内側で 1 分足が無い。原因を表さない（D12-71）。

区分は分の連続（半開区間 `[start, end)`。epoch 秒、分の境目）として持つ。全期間の 1 分足は
通貨あたり数百万本あり、分ごとの値を持つと重いが、記録ありの分はほとんどが長い連続なので、
区間で持てば小さく、件数・最初と最後の観測・再開の分を二分探索で引ける。

台帳は受入れ（足の生成と検査報告）と、全期間の集計の道具（PR-B）が同じ関数で作る
（D12 §11.13 の 15。規則を道具と本番で別々に持たない）。
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = [
    "MINUTE",
    "MinuteCounts",
    "MinuteLedger",
    "MinuteStatus",
    "ShortfallReason",
    "ShortfallSpan",
    "classify_minutes",
    "merge_spans",
]

#: 1 分（秒）。
MINUTE = 60


class MinuteStatus(Enum):
    """1 分の区分（D03 §15.2 の表。判定の順もこの並び）。"""

    CLOSED = "closed"
    ACQUISITION_SHORTFALL = "acquisition_shortfall"
    RECORDED = "recorded"
    UNRECORDED = "unrecorded"


#: 実装が判定する区分の順（数え方の規則 `minute_status_order` と照合する。D03 §15.12）。
IMPLEMENTED_STATUS_ORDER: tuple[str, ...] = tuple(status.value for status in MinuteStatus)


class ShortfallReason(Enum):
    """取得不足の理由（D03 §15.2 の順 2 の (a)〜(d)）。

    数え方の規則の `acquisition_shortfall.reasons` と同じ語。

    (c) 破損は受入れ全体を失敗させるので、台帳の区間には現れない。
    """

    OUTSIDE_ACQUIRED_RANGE = "outside_acquired_range"
    REQUEST_FAILED_OR_NOT_FETCHED = "request_failed_or_not_fetched"
    CORRUPT = "corrupt"
    TIME_NOT_INTERPRETABLE = "time_not_interpretable"


@dataclass(frozen=True, slots=True)
class ShortfallSpan:
    """取得不足の区間 1 つ（epoch 秒の半開区間と理由）。"""

    start: int
    end: int
    reason: ShortfallReason

    def __post_init__(self) -> None:
        _require_minute_span(self.start, self.end, "ShortfallSpan")
        if not isinstance(self.reason, ShortfallReason):
            raise MarketDataValueError("ShortfallSpan.reason must be a ShortfallReason")


@dataclass(frozen=True, slots=True)
class MinuteCounts:
    """区間の中の分の件数（D03 §15.4 の被覆の属性の材料）。

    `expected` は足が存在すべき分（休場でない分。取得不足を含む）。
    """

    expected: int
    recorded: int
    unrecorded: int
    shortfall: int


def _require_minute_span(start: int, end: int, label: str) -> None:
    for value in (start, end):
        if isinstance(value, bool) or not isinstance(value, int):
            raise MarketDataValueError(f"{label} bounds must be ints, got {value!r}")
        if value % MINUTE:
            raise MarketDataValueError(f"{label} bounds must be on minute boundaries, got {value}")
    if not start < end:
        raise MarketDataValueError(f"{label} requires start < end, got {start} .. {end}")


def merge_spans(spans: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """重なる・接する区間をつないで昇順に並べる。"""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
            continue
        merged.append((start, end))
    return tuple(merged)


def _overlap_total(runs: Sequence[tuple[int, int]], starts: Sequence[int], lo: int, hi: int) -> int:
    """昇順で重ならない区間の列 `runs` と `[lo, hi)` の重なりの秒数。"""
    if lo >= hi or not runs:
        return 0
    total = 0
    index = max(bisect.bisect_right(starts, lo) - 1, 0)
    while index < len(runs) and runs[index][0] < hi:
        start, end = runs[index]
        overlap = min(end, hi) - max(start, lo)
        if overlap > 0:
            total += overlap
        index += 1
    return total


def _runs_in(
    runs: Sequence[tuple[int, int]], starts: Sequence[int], lo: int, hi: int
) -> list[tuple[int, int]]:
    """`[lo, hi)` に切り詰めた区間の列。"""
    out: list[tuple[int, int]] = []
    if lo >= hi:
        return out
    index = max(bisect.bisect_right(starts, lo) - 1, 0)
    while index < len(runs) and runs[index][0] < hi:
        start, end = max(runs[index][0], lo), min(runs[index][1], hi)
        if start < end:
            out.append((start, end))
        index += 1
    return out


@dataclass(frozen=True, slots=True)
class MinuteLedger:
    """1 系列の分の区分（D03 §15.2）。区間はすべて昇順で重ならない。

    台帳が扱うのは `[data_start, data_end)`（取得の範囲の最初から最後まで。UTC）の中の分だけで、
    その外はデータの開始前・終了後（台帳の外）。休場の分は `sessions` の外として暗黙に表す。
    """

    data_start: int
    data_end: int
    sessions: tuple[tuple[int, int], ...]
    recorded: tuple[tuple[int, int], ...]
    unrecorded: tuple[tuple[int, int], ...]
    shortfall: tuple[ShortfallSpan, ...]
    _session_starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _recorded_starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _unrecorded_starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _shortfall_runs: tuple[tuple[int, int], ...] = field(init=False, repr=False, compare=False)
    _shortfall_starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _recorded_start_set: frozenset[int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_minute_span(self.data_start, self.data_end, "MinuteLedger")
        if not isinstance(self.shortfall, tuple) or not all(
            isinstance(span, ShortfallSpan) for span in self.shortfall
        ):
            raise MarketDataValueError("MinuteLedger.shortfall must be a tuple of ShortfallSpan")
        for name in ("sessions", "recorded", "unrecorded"):
            runs = getattr(self, name)
            if not isinstance(runs, tuple):
                raise MarketDataValueError(f"MinuteLedger.{name} must be a tuple")
            previous_end = None
            for start, end in runs:
                _require_minute_span(start, end, f"MinuteLedger.{name}")
                if previous_end is not None and start < previous_end:
                    raise MarketDataValueError(f"MinuteLedger.{name} must not overlap")
                previous_end = end
        # 索引（区間の列から決まる派生値。比較の対象にしない）。
        shortfall_runs = tuple((span.start, span.end) for span in self.shortfall)
        object.__setattr__(self, "_session_starts", tuple(s for s, _ in self.sessions))
        object.__setattr__(self, "_recorded_starts", tuple(s for s, _ in self.recorded))
        object.__setattr__(self, "_unrecorded_starts", tuple(s for s, _ in self.unrecorded))
        object.__setattr__(self, "_shortfall_runs", shortfall_runs)
        object.__setattr__(self, "_shortfall_starts", tuple(s for s, _ in shortfall_runs))
        object.__setattr__(self, "_recorded_start_set", frozenset(self._recorded_starts))

    def counts(self, lo: int, hi: int) -> MinuteCounts:
        """`[lo, hi)` の中の分の件数（台帳の外の分は数えない）。"""
        lo = max(lo, self.data_start)
        hi = min(hi, self.data_end)
        if lo >= hi:
            return MinuteCounts(0, 0, 0, 0)
        expected = _overlap_total(self.sessions, self._session_starts, lo, hi)
        recorded = _overlap_total(self.recorded, self._recorded_starts, lo, hi)
        unrecorded = _overlap_total(self.unrecorded, self._unrecorded_starts, lo, hi)
        shortfall = _overlap_total(self._shortfall_runs, self._shortfall_starts, lo, hi)
        return MinuteCounts(
            expected=expected // MINUTE,
            recorded=recorded // MINUTE,
            unrecorded=unrecorded // MINUTE,
            shortfall=shortfall // MINUTE,
        )

    def shortfall_in(self, lo: int, hi: int) -> tuple[ShortfallSpan, ...]:
        """`[lo, hi)` と重なる取得不足の区間（切り詰めた区間）。"""
        out: list[ShortfallSpan] = []
        index = max(bisect.bisect_right(self._shortfall_starts, lo) - 1, 0)
        while index < len(self.shortfall) and self.shortfall[index].start < hi:
            span = self.shortfall[index]
            start, end = max(span.start, lo), min(span.end, hi)
            if start < end:
                out.append(ShortfallSpan(start=start, end=end, reason=span.reason))
            index += 1
        return tuple(out)

    def unrecorded_in(self, lo: int, hi: int) -> tuple[tuple[int, int], ...]:
        """`[lo, hi)` に切り詰めた無記録の区間。"""
        return tuple(_runs_in(self.unrecorded, self._unrecorded_starts, lo, hi))

    def recorded_in(self, lo: int, hi: int) -> tuple[tuple[int, int], ...]:
        """`[lo, hi)` に切り詰めた記録ありの区間。"""
        return tuple(_runs_in(self.recorded, self._recorded_starts, lo, hi))

    def first_recorded(self, lo: int, hi: int) -> int | None:
        """`[lo, hi)` で最初に記録のある分の開始（無ければ `None`）。"""
        runs = self.recorded_in(lo, hi)
        return runs[0][0] if runs else None

    def last_recorded(self, lo: int, hi: int) -> int | None:
        """`[lo, hi)` で最後に記録のある分の開始（無ければ `None`）。"""
        runs = self.recorded_in(lo, hi)
        return runs[-1][1] - MINUTE if runs else None

    def resumptions_in(self, lo: int, hi: int) -> tuple[tuple[int, int], ...]:
        """`[lo, hi)` の再開の分と、その直前の無記録の始まり（D06 §18.4）。

        再開の分は、1 つ以上の無記録の分の直後にある最初の記録のある分（無記録の区間の終わりが
        記録ありの区間の始まりと一致するもの）。戻り値は `(再開の分, 無記録の始まり)` の列。
        休場や取得不足の後の最初の記録は再開の分ではない（無記録の後ではないため）。
        """
        out: list[tuple[int, int]] = []
        index = max(bisect.bisect_right(self._unrecorded_starts, lo) - 1, 0)
        while index < len(self.unrecorded):
            start, end = self.unrecorded[index]
            index += 1
            if end < lo:
                continue
            if end >= hi:
                if start >= hi:
                    break
                continue
            if end in self._recorded_start_set:
                out.append((end, start))
        return tuple(out)

    def is_recorded(self, minute: int) -> bool:
        """分 `minute`（開始の epoch 秒）が記録ありか。"""
        index = bisect.bisect_right(self._recorded_starts, minute) - 1
        return index >= 0 and minute < self.recorded[index][1]

    def is_unrecorded(self, minute: int) -> bool:
        """分 `minute` が無記録か。"""
        index = bisect.bisect_right(self._unrecorded_starts, minute) - 1
        return index >= 0 and minute < self.unrecorded[index][1]

    def unrecorded_runs_total(self) -> tuple[int, int, int]:
        """（無記録の分数、無記録の区間の数、最長の分数）。"""
        lengths = [(end - start) // MINUTE for start, end in self.unrecorded]
        return sum(lengths), len(lengths), max(lengths, default=0)


def _subtract(
    parts: Sequence[tuple[int, int]], cuts: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """区間の列から区間の列を取り除く（どちらも昇順で重ならない）。"""
    out: list[tuple[int, int]] = []
    cut_starts = [start for start, _ in cuts]
    for start, end in parts:
        cursor = start
        index = max(bisect.bisect_right(cut_starts, start) - 1, 0)
        while index < len(cuts) and cuts[index][0] < end:
            cut_start, cut_end = cuts[index]
            if cut_end > cursor:
                if cut_start > cursor:
                    out.append((cursor, min(cut_start, end)))
                cursor = max(cursor, cut_end)
            index += 1
        if cursor < end:
            out.append((cursor, end))
    return out


def _intersect(
    parts: Sequence[tuple[int, int]], masks: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """区間の列どうしの共通部分（どちらも昇順で重ならない）。"""
    out: list[tuple[int, int]] = []
    mask_starts = [start for start, _ in masks]
    for start, end in parts:
        out.extend(_runs_in(masks, mask_starts, start, end))
    return out


def classify_minutes(
    *,
    sessions: Iterable[tuple[int, int]],
    recorded_minutes: Sequence[int],
    acquired: Iterable[tuple[int, int]],
    failed: Iterable[tuple[int, int, ShortfallReason]] = (),
    data_start: int,
    data_end: int,
) -> MinuteLedger:
    """セッションの分に区分を付けて台帳を作る（D03 §15.2）。

    - `sessions`: 取引カレンダーのセッション（UTC の epoch 秒の半開区間）。外は休場。
    - `recorded_minutes`: 取得済みの系列の 1 分足の開始（UTC、昇順、重複なし）。セッションの外の
      分（休場帯の足）は区分に使わない（足が存在すべき分ではない）。
    - `acquired`: 取得の範囲（取得記録の要求区間を表で UTC に直したものの和。状態が取得済みの
      要求だけ）。
    - `failed`: 取得できなかった要求の区間と理由（(b) 要求の失敗・未取得、(d) 時刻を解釈できない）。
      取得の範囲の外でこの区間に入る分はその理由、どれにも入らない分は (a) 取得の範囲の外。
    - `data_start` / `data_end`: 台帳が扱う範囲（取得の範囲の最初から最後まで）。

    判定の順（休場 → 取得不足 → 記録あり → 無記録）はこの関数の手順そのもので、数え方の規則の
    `minute_status_order` と照合済みの順（`IMPLEMENTED_STATUS_ORDER`）。
    """
    _require_minute_span(data_start, data_end, "classify_minutes")
    in_range = [(max(start, data_start), min(end, data_end)) for start, end in sessions]
    session_runs = merge_spans(run for run in in_range if run[0] < run[1])
    acquired_runs = merge_spans(acquired)
    for value in recorded_minutes:
        if value % MINUTE:
            raise MarketDataValueError(f"a recorded minute is not on a minute boundary: {value}")

    # 1. 休場: セッションの外（暗黙）。
    # 2. 取得不足: セッションのうち取得の範囲の外。
    outside = _subtract(session_runs, acquired_runs)
    failed_spans = sorted(failed, key=lambda item: item[0])
    shortfall: list[ShortfallSpan] = []
    for start, end in outside:
        cursor = start
        for f_start, f_end, reason in failed_spans:
            if f_end <= cursor or f_start >= end:
                continue
            if f_start > cursor:
                shortfall.append(
                    ShortfallSpan(cursor, f_start, ShortfallReason.OUTSIDE_ACQUIRED_RANGE)
                )
            lo, hi = max(f_start, cursor), min(f_end, end)
            shortfall.append(ShortfallSpan(lo, hi, reason))
            cursor = hi
        if cursor < end:
            shortfall.append(ShortfallSpan(cursor, end, ShortfallReason.OUTSIDE_ACQUIRED_RANGE))

    # 3・4. 取得済みのセッションの分を、記録あり・無記録に分ける。
    observable = _intersect(session_runs, acquired_runs)
    recorded_runs: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for minute in recorded_minutes:
        if current is not None and minute == current[1]:
            current = (current[0], minute + MINUTE)
            continue
        if current is not None:
            recorded_runs.append(current)
        current = (minute, minute + MINUTE)
    if current is not None:
        recorded_runs.append(current)
    recorded_in = merge_spans(_intersect(merge_spans(recorded_runs), observable))
    unrecorded = merge_spans(_subtract(observable, recorded_in))
    return MinuteLedger(
        data_start=data_start,
        data_end=data_end,
        sessions=session_runs,
        recorded=recorded_in,
        unrecorded=unrecorded,
        shortfall=tuple(shortfall),
    )
