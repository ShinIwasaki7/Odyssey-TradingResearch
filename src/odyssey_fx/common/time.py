"""時刻・区間・処理時点（D02 §3）。

`UtcTime` は tz-aware な UTC 時刻だけを通す専用型（D02 §3.1）、`Interval` は半開区間
`[start, end)`（D02 §3.2）、`PhaseRank` / `ProcessingPoint` は「エンジンがいつ処理したか」
を表す全順序付きの時点（D02 §3.3）。市場で起きた時刻は `UtcTime` / `Interval` で別に持つ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, overload
from zoneinfo import ZoneInfo

from odyssey_fx.common.errors import KernelValueError

__all__ = [
    "Interval",
    "PhaseRank",
    "PhaseSet",
    "ProcessingPoint",
    "UtcTime",
]

#: `PhaseRank.name` に許す字種（D02 §3.3）。
_PHASE_NAME_PATTERN: Final = re.compile(r"^[A-Z_]+$")

#: `UtcTime.__str__` の秒までの書式（D02 §3.1）。
_SECONDS_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"


@dataclass(frozen=True, slots=True)
class UtcTime:
    """UTC の tz-aware 時刻（D02 §3.1）。

    naive な値や他タイムゾーンの値が混入しないよう、構築時に UTC を強制する。
    比較・ハッシュは `value` に従う。精度はマイクロ秒（`datetime` の精度）。
    """

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):  # pragma: no cover - 型検査で防がれる
            raise KernelValueError("UtcTime.value must be a datetime")
        tzinfo = self.value.tzinfo
        if tzinfo is None:
            raise KernelValueError(f"UtcTime requires a tz-aware datetime: {self.value!r}")
        offset = self.value.utcoffset()
        if offset != timedelta(0):
            raise KernelValueError(f"UtcTime requires a UTC offset of 0, got {offset!r}")
        name = tzinfo.tzname(self.value)
        if name != "UTC":
            raise KernelValueError(f"UtcTime requires a UTC timezone, got tzname {name!r}")

    # --- 構築 ---------------------------------------------------------------

    @classmethod
    def from_components(
        cls,
        year: int,
        month: int,
        day: int,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
        microsecond: int = 0,
    ) -> UtcTime:
        """暦要素から UTC 時刻を作る。"""
        return cls(datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC))

    @classmethod
    def from_local(cls, naive: datetime, tz: ZoneInfo, *, fold: int | None = None) -> UtcTime:
        """地域時刻（naive）とタイムゾーンから UTC 時刻を作る（D02 §3.1）。

        夏時間（DST）の切替により、同じ地域時刻が2回現れる「曖昧な時刻」と、1度も現れない
        「不存在の時刻」がある。曖昧なのに `fold` が未指定の場合と、不存在の時刻の場合は
        `KernelValueError` を送出し、呼び出し側に明示させる。`fold=0` は1回目（切替前の
        オフセット）、`fold=1` は2回目（切替後のオフセット）を指す。
        """
        if naive.tzinfo is not None:
            raise KernelValueError(f"UtcTime.from_local requires a naive datetime: {naive!r}")
        if fold is not None and fold not in (0, 1):
            raise KernelValueError(f"fold must be 0 or 1, got {fold!r}")

        first = naive.replace(tzinfo=tz, fold=0)
        second = naive.replace(tzinfo=tz, fold=1)
        offset_first = first.utcoffset()
        offset_second = second.utcoffset()
        if offset_first is None or offset_second is None:  # pragma: no cover - ZoneInfo は常に返す
            raise KernelValueError(f"timezone {tz!r} has no offset for {naive!r}")

        if offset_first != offset_second:
            # 曖昧（オフセットが戻る）か不存在（オフセットが進む）かを往復で判定する。
            roundtrip = first.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
            if roundtrip != naive:
                raise KernelValueError(
                    f"local time {naive.isoformat()} does not exist in {tz.key}"
                    " (skipped by a DST transition)"
                )
            if fold is None:
                raise KernelValueError(
                    f"local time {naive.isoformat()} is ambiguous in {tz.key};"
                    " pass fold=0 (first occurrence) or fold=1 (second occurrence)"
                )

        chosen = naive.replace(tzinfo=tz, fold=0 if fold is None else fold)
        return cls(chosen.astimezone(UTC))

    @classmethod
    def parse(cls, text: str) -> UtcTime:
        """`YYYY-MM-DDTHH:MM:SS[.ffffff]Z` または `+00:00` 形式を読む（D02 §3.1）。

        naive な文字列や 0 以外のオフセットは拒否する。
        """
        if not isinstance(text, str):  # pragma: no cover - 型検査で防がれる
            raise KernelValueError("UtcTime.parse requires a string")
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise KernelValueError(f"invalid UtcTime literal: {text!r}") from exc
        if parsed.tzinfo is None:
            raise KernelValueError(f"UtcTime literal must carry a UTC offset: {text!r}")
        if parsed.utcoffset() != timedelta(0):
            raise KernelValueError(f"UtcTime literal must be UTC: {text!r}")
        return cls(parsed.astimezone(UTC))

    # --- 文字列化 -----------------------------------------------------------

    def __str__(self) -> str:
        base = self.value.strftime(_SECONDS_FORMAT)
        if self.value.microsecond:
            return f"{base}.{self.value.microsecond:06d}Z"
        return f"{base}Z"

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3: `UtcTime` は §3.1 の文字列）。"""
        return str(self)

    # --- 演算 ---------------------------------------------------------------

    def __add__(self, other: timedelta) -> UtcTime:
        if not isinstance(other, timedelta):
            return NotImplemented
        return UtcTime(self.value + other)

    def __radd__(self, other: timedelta) -> UtcTime:
        return self.__add__(other)

    @overload
    def __sub__(self, other: UtcTime) -> timedelta: ...

    @overload
    def __sub__(self, other: timedelta) -> UtcTime: ...

    def __sub__(self, other: UtcTime | timedelta) -> timedelta | UtcTime:
        """`UtcTime - UtcTime` は経過時間、`UtcTime - timedelta` は時刻（D02 §3.1）。"""
        if isinstance(other, UtcTime):
            return self.value - other.value
        if not isinstance(other, timedelta):
            # 型検査では到達しないが、実行時に `UtcTime - 5` のような誤用が来たとき
            # `datetime` の内部エラーを見せず、Python の標準的な TypeError にする。
            return NotImplemented
        return UtcTime(self.value - other)

    # --- 比較 ---------------------------------------------------------------

    def __lt__(self, other: UtcTime) -> bool:
        if not isinstance(other, UtcTime):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: UtcTime) -> bool:
        if not isinstance(other, UtcTime):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other: UtcTime) -> bool:
        if not isinstance(other, UtcTime):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other: UtcTime) -> bool:
        if not isinstance(other, UtcTime):
            return NotImplemented
        return self.value >= other.value


@dataclass(frozen=True, slots=True)
class Interval:
    """半開区間 `[start, end)`（D02 §3.2）。空区間は作れない。"""

    start: UtcTime
    end: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.start, UtcTime) or not isinstance(self.end, UtcTime):
            raise KernelValueError("Interval bounds must be UtcTime")
        if not self.start < self.end:
            raise KernelValueError(f"Interval requires start < end, got [{self.start}, {self.end})")

    @property
    def duration(self) -> timedelta:
        """区間の長さ。"""
        return self.end - self.start

    def contains(self, moment: UtcTime) -> bool:
        """`start <= moment < end` を判定する。"""
        if not isinstance(moment, UtcTime):
            raise TypeError(f"Interval.contains requires UtcTime, got {type(moment).__name__}")
        return self.start <= moment < self.end

    def overlaps(self, other: Interval) -> bool:
        """他の区間と共通部分を持つかを判定する（接するだけは重なりではない）。"""
        if not isinstance(other, Interval):
            raise TypeError(f"Interval.overlaps requires Interval, got {type(other).__name__}")
        return self.start < other.end and other.start < self.end

    def adjacent_to(self, other: Interval) -> bool:
        """他の区間と隙間なく隣接するか（一方の `end` が他方の `start`）を判定する。"""
        if not isinstance(other, Interval):
            raise TypeError(f"Interval.adjacent_to requires Interval, got {type(other).__name__}")
        return self.end == other.start or other.end == self.start

    def __str__(self) -> str:
        return f"[{self.start}, {self.end})"


@dataclass(frozen=True, slots=True, order=False)
class PhaseRank:
    """処理フェーズの順位と名前（D02 §3.3）。

    フェーズの具体的な一覧は `backtest.engine` が定義し、`common` は「順位を持つ段階」
    という構造だけを持つ。
    """

    rank: int
    name: str

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int):
            raise KernelValueError(f"PhaseRank.rank must be an int, got {self.rank!r}")
        if self.rank < 0:
            raise KernelValueError(f"PhaseRank.rank must be >= 0, got {self.rank}")
        if not isinstance(self.name, str) or not _PHASE_NAME_PATTERN.match(self.name):
            raise KernelValueError(f"PhaseRank.name must match ^[A-Z_]+$, got {self.name!r}")

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class PhaseSet:
    """run 内で使うフェーズ集合（D02 §3.3 の一意性）。

    `rank` と `name` はそれぞれ集合内で一意（rank ↔ name は全単射）でなければならない。
    同じ `rank` に異なる `name`、同じ `name` に異なる `rank` を含む集合は構築時に拒否する。
    `ProcessingPoint` の全順序はこの一意性を前提とし、集合の定義は run manifest に記録する。
    実際のフェーズ一覧は `backtest.engine`（D06）が固定の tuple として定義する。
    """

    phases: tuple[PhaseRank, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.phases, tuple):
            raise KernelValueError("PhaseSet.phases must be a tuple")
        if not self.phases:
            raise KernelValueError("PhaseSet must not be empty")
        by_rank: dict[int, str] = {}
        by_name: dict[str, int] = {}
        for phase in self.phases:
            if not isinstance(phase, PhaseRank):
                raise KernelValueError("PhaseSet.phases must contain PhaseRank values")
            existing_name = by_rank.get(phase.rank)
            if existing_name is not None and existing_name != phase.name:
                raise KernelValueError(
                    f"rank {phase.rank} maps to both {existing_name!r} and {phase.name!r}"
                )
            existing_rank = by_name.get(phase.name)
            if existing_rank is not None and existing_rank != phase.rank:
                raise KernelValueError(
                    f"name {phase.name!r} maps to both rank {existing_rank} and {phase.rank}"
                )
            by_rank[phase.rank] = phase.name
            by_name[phase.name] = phase.rank

    def by_name(self, name: str) -> PhaseRank:
        """名前からフェーズを引く。未登録の名前は `KernelValueError`。"""
        for phase in self.phases:
            if phase.name == name:
                return phase
        raise KernelValueError(f"unknown phase name: {name!r}")

    def by_rank(self, rank: int) -> PhaseRank:
        """順位からフェーズを引く。未登録の順位は `KernelValueError`。"""
        for phase in self.phases:
            if phase.rank == rank:
                return phase
        raise KernelValueError(f"unknown phase rank: {rank!r}")

    def ordered(self) -> tuple[PhaseRank, ...]:
        """`rank` の昇順に整列した重複なしのフェーズ列。"""
        unique = {phase.rank: phase for phase in self.phases}
        return tuple(unique[rank] for rank in sorted(unique))


@dataclass(frozen=True, slots=True)
class ProcessingPoint:
    """エンジンがいつ処理したかを表す時点（D02 §3.3）。

    全順序は `(time, phase.rank, sequence)`。同じ `time` でも phase と sequence で区別する。
    市場で起きた時刻ではないので、市場時刻は `Interval` / `UtcTime` で別に持つ。
    """

    time: UtcTime
    phase: PhaseRank
    sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.time, UtcTime):
            raise KernelValueError("ProcessingPoint.time must be a UtcTime")
        if not isinstance(self.phase, PhaseRank):
            raise KernelValueError("ProcessingPoint.phase must be a PhaseRank")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise KernelValueError(
                f"ProcessingPoint.sequence must be an int, got {self.sequence!r}"
            )
        if self.sequence < 0:
            raise KernelValueError(f"ProcessingPoint.sequence must be >= 0, got {self.sequence}")

    @property
    def sort_key(self) -> tuple[datetime, int, int]:
        """全順序の比較キー `(time, phase.rank, sequence)`。"""
        return (self.time.value, self.phase.rank, self.sequence)

    def __lt__(self, other: ProcessingPoint) -> bool:
        if not isinstance(other, ProcessingPoint):
            return NotImplemented
        return self.sort_key < other.sort_key

    def __le__(self, other: ProcessingPoint) -> bool:
        if not isinstance(other, ProcessingPoint):
            return NotImplemented
        return self.sort_key <= other.sort_key

    def __gt__(self, other: ProcessingPoint) -> bool:
        if not isinstance(other, ProcessingPoint):
            return NotImplemented
        return self.sort_key > other.sort_key

    def __ge__(self, other: ProcessingPoint) -> bool:
        if not isinstance(other, ProcessingPoint):
            return NotImplemented
        return self.sort_key >= other.sort_key

    def __str__(self) -> str:
        return f"{self.time}/{self.phase.name}#{self.sequence}"
