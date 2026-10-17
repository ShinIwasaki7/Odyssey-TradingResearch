"""期間別の時刻の規則の表（D03 §15.9、D12-47・D12-48・D12-50・D12-92）。

OANDA MT5 API の 1 分足は、API の生の時刻（epoch 秒。サーバーの時計の値を UTC と見なした数）で
届く。UTC へ直す変換 `UTC = API の時刻 − offset_hours` は、通貨と API の時刻の暦日で表の行を
引いて決める（D03 §15.3 の 3）。表そのものは人間が承認した宣言（`docs/design/time_rules/`）で、
読込と承認済みハッシュの照合（検査 T1）は `app.config.time_rules` が行う。本モジュールは表の型と、
読み込んだ表の構造の検査（T2・T3・T5）、変換の組（`standard` / `candidate`）ごとの変換を持つ。

**規則をコードに持たない**: 行・期間・標準仮説・候補の変換はすべてファイルから来る。本モジュールが
決めているのは「表の引き方」（D03 §15.9 の 3）と「変換の組の作り方」（同 5・6。D12-92 (a)）だけで、
どちらも設計書が定めた手順である。

検査 T4（取引時間の中の分で、API の時刻の暦日と UTC の暦日が同じ行を引く）はカレンダーが要るので
`check_lookup_agreement` に分けた（受入れが呼ぶ）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = [
    "TimeRuleMark",
    "TimeRulePeriod",
    "TimeRuleRow",
    "TimeRuleTable",
    "TimeRuleTableRef",
    "TimeRuleVariant",
    "api_date_of",
    "check_lookup_agreement",
]


class TimeRuleMark(Enum):
    """区分の印（D12 §11.7.10 の表 版 1 の `mark`）。

    表示と照合にだけ使い、判定に使わない（D07 §28.4）。
    """

    CONFIRMED = "confirmed"
    ASSUMED_SAME = "assumed_same"
    ASSUMED_DIFFERS = "assumed_differs"


class TimeRuleVariant(Enum):
    """変換の組（D03 §15.9 の 6、D12-92 (a)）。

    - `STANDARD`: すべての行で標準仮説の変換。
    - `CANDIDATE`: 候補を持つ行（仮定付き（候補の間で変換が違う））をすべて候補の変換に替え、
      他の行は標準のまま。
    """

    STANDARD = "standard"
    CANDIDATE = "candidate"


@dataclass(frozen=True, slots=True)
class TimeRulePeriod:
    """行の期間 1 つ。API の時刻の暦日の半開区間 `[start, end)`。"""

    start: date
    end: date

    def __post_init__(self) -> None:
        for name in ("start", "end"):
            value = getattr(self, name)
            if not isinstance(value, date) or isinstance(value, datetime):
                raise MarketDataValueError(f"TimeRulePeriod.{name} must be a date")
        if not self.start < self.end:
            raise MarketDataValueError(
                f"TimeRulePeriod requires start < end, got {self.start} .. {self.end}"
            )

    def contains(self, day: date) -> bool:
        """暦日 `day` がこの期間に入るか。"""
        return self.start <= day < self.end


@dataclass(frozen=True, slots=True)
class TimeRuleRow:
    """表の 1 行（D03 §15.9 の 1）。

    `candidate_offsets` は感度の検証の候補の変換（時間）。確認済みの行と、仮定付き（候補の間で
    変換が同じ）の行は空、仮定付き（候補の間で変換が違う）の行は 1 つ以上（検査 T5）。
    """

    row: int
    pairs: tuple[str, ...]
    periods: tuple[TimeRulePeriod, ...]
    mark: TimeRuleMark
    standard_offset_hours: int
    candidate_offset_hours: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.row, bool) or not isinstance(self.row, int) or self.row < 1:
            raise MarketDataValueError(f"TimeRuleRow.row must be a positive int, got {self.row!r}")
        if not isinstance(self.pairs, tuple) or not self.pairs:
            raise MarketDataValueError("TimeRuleRow.pairs must be a non-empty tuple")
        if not isinstance(self.periods, tuple) or not self.periods:
            raise MarketDataValueError("TimeRuleRow.periods must be a non-empty tuple")
        if not isinstance(self.mark, TimeRuleMark):
            raise MarketDataValueError("TimeRuleRow.mark must be a TimeRuleMark")
        for value in (self.standard_offset_hours, *self.candidate_offset_hours):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MarketDataValueError(f"TimeRuleRow offsets must be ints, got {value!r}")
        # 検査 T5（D03 §15.9 の 4）。
        if self.mark is TimeRuleMark.ASSUMED_DIFFERS:
            if not self.candidate_offset_hours:
                raise MarketDataValueError(
                    f"time rule row {self.row} is assumed (the candidates differ) but declares no"
                    " candidate conversion (D03 §15.9 T5)"
                )
        elif self.candidate_offset_hours:
            raise MarketDataValueError(
                f"time rule row {self.row} is {self.mark.value} and must not declare candidate"
                " conversions (D03 §15.9 T5)"
            )

    def offset_hours(self, variant: TimeRuleVariant) -> int:
        """変換の組での「API の時刻 − UTC」（時間。D03 §15.9 の 5）。"""
        if variant is TimeRuleVariant.STANDARD or not self.candidate_offset_hours:
            return self.standard_offset_hours
        if len(self.candidate_offset_hours) != 1:
            # D12-92 (a) は「候補の行をすべて替えた組」の 1 つだけを決めている。候補が 2 つ以上ある
            # 行の組み方は決まっていないので、既定値で埋めずに止める。
            raise MarketDataValueError(
                f"time rule row {self.row} declares {len(self.candidate_offset_hours)} candidate"
                " conversions; the candidate variant (D12-92 (a)) is defined for one candidate"
                " per row only"
            )
        return self.candidate_offset_hours[0]

    def covers(self, pair: str, day: date) -> bool:
        """通貨 `pair` の暦日 `day` をこの行が覆うか。"""
        return pair in self.pairs and any(period.contains(day) for period in self.periods)


@dataclass(frozen=True, slots=True)
class TimeRuleTableRef:
    """snapshot・run が記録する表の参照（D03 §15.6 の 4、D06 §18.10 の `time_rule_table_ref`）。"""

    table_id: str
    version: int
    sha256: str
    variant: TimeRuleVariant

    def __post_init__(self) -> None:
        if not isinstance(self.table_id, str) or not self.table_id:
            raise MarketDataValueError("TimeRuleTableRef.table_id must be a non-empty str")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise MarketDataValueError("TimeRuleTableRef.version must be a positive int")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise MarketDataValueError("TimeRuleTableRef.sha256 must be 64 lowercase hex chars")
        if not isinstance(self.variant, TimeRuleVariant):
            raise MarketDataValueError("TimeRuleTableRef.variant must be a TimeRuleVariant")

    def payload(self) -> Mapping[str, object]:
        """manifest と識別子に書く形。"""
        return {
            "sha256": self.sha256,
            "table_id": self.table_id,
            "variant": self.variant.value,
            "version": self.version,
        }


def api_date_of(api_epoch_seconds: int) -> date:
    """API の生の時刻の暦日（表を引く鍵。D03 §15.9 の 3）。"""
    return datetime.fromtimestamp(api_epoch_seconds, UTC).date()


@dataclass(frozen=True, slots=True)
class TimeRuleTable:
    """期間別の時刻の規則の表（D03 §15.9）。

    構築時に検査 T2（通貨ごとに期間が重ならず、対象期間を隙間なく覆う）・T3（境目が日曜）・
    T5（行の候補と区分）を行う。T1（承認済みハッシュ）は読込（`app.config`）、T4 は
    `check_lookup_agreement` が行う。
    """

    table_id: str
    version: int
    sha256: str
    pairs: tuple[str, ...]
    coverage_start: date
    coverage_end: date
    rows: tuple[TimeRuleRow, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.table_id, str) or not self.table_id:
            raise MarketDataValueError("TimeRuleTable.table_id must be a non-empty str")
        if not isinstance(self.rows, tuple) or not self.rows:
            raise MarketDataValueError("TimeRuleTable.rows must be a non-empty tuple")
        numbers = [row.row for row in self.rows]
        if len(set(numbers)) != len(numbers):
            raise MarketDataValueError("TimeRuleTable row numbers must be unique")
        self._check_coverage()

    def _check_coverage(self) -> None:
        """検査 T2・T3（D03 §15.9 の 4）。"""
        for pair in self.pairs:
            spans = sorted(
                (period for row in self.rows if pair in row.pairs for period in row.periods),
                key=lambda period: period.start,
            )
            cursor = self.coverage_start
            for period in spans:
                if period.start != cursor:
                    kind = "overlap" if period.start < cursor else "gap"
                    raise MarketDataValueError(
                        f"the time rule table has a {kind} for {pair} at"
                        f" {min(cursor, period.start)} (D03 §15.9 T2)"
                    )
                cursor = period.end
            if cursor != self.coverage_end:
                raise MarketDataValueError(
                    f"the time rule table does not cover {pair} up to {self.coverage_end}"
                    f" (it ends at {cursor}; D03 §15.9 T2)"
                )
            for period in spans:
                for boundary in (period.start, period.end):
                    if boundary in (self.coverage_start, self.coverage_end):
                        continue
                    if boundary.weekday() != 6:
                        raise MarketDataValueError(
                            f"the time rule table has a period boundary on {boundary}, which is"
                            " not a Sunday (D03 §15.9 T3)"
                        )
        for row in self.rows:
            for pair in row.pairs:
                if pair not in self.pairs:
                    raise MarketDataValueError(
                        f"time rule row {row.row} names {pair}, which the table does not cover"
                    )

    def row_for(self, pair: str, day: date) -> TimeRuleRow | None:
        """通貨と暦日の行。覆わなければ `None`（時刻を解釈できない。取得不足 (d)。D03 §15.2）。"""
        if pair not in self.pairs or not self.coverage_start <= day < self.coverage_end:
            return None
        for row in self.rows:
            if row.covers(pair, day):
                return row
        return None  # pragma: no cover - T2 が覆いを保証する

    def ref(self, variant: TimeRuleVariant) -> TimeRuleTableRef:
        """この表と変換の組の参照。"""
        return TimeRuleTableRef(
            table_id=self.table_id, version=self.version, sha256=self.sha256, variant=variant
        )

    def boundaries(self) -> tuple[date, ...]:
        """期間の境目（対象期間の両端を除く）。"""
        found = {
            boundary
            for row in self.rows
            for period in row.periods
            for boundary in (period.start, period.end)
            if boundary not in (self.coverage_start, self.coverage_end)
        }
        return tuple(sorted(found))


def check_lookup_agreement(
    table: TimeRuleTable,
    sessions: Iterable[Interval],
    *,
    pairs: Iterable[str] | None = None,
) -> int:
    """検査 T4: 取引時間の中の分で、API の時刻の暦日と UTC の暦日が同じ行を引くか。

    `sessions` はカレンダーの取引セッション（UTC）で、表の境目の前後を含むもの。調べた分の数を
    返す。食い違いがあれば `MarketDataValueError`。調べるのは境目の前後 2 日の分（それ以外の分は
    両方の暦日が同じ期間の中にあるので行は変わらない）。
    """
    boundaries = table.boundaries()
    windows = [
        (
            datetime.combine(boundary - timedelta(days=2), datetime.min.time(), UTC),
            datetime.combine(boundary + timedelta(days=2), datetime.min.time(), UTC),
        )
        for boundary in boundaries
    ]
    checked = 0
    targets = tuple(table.pairs if pairs is None else pairs)
    minute = timedelta(minutes=1)
    for session in sessions:
        for lo, hi in windows:
            start = max(session.start.value, lo)
            end = min(session.end.value, hi)
            moment = start
            while moment < end:
                utc_day = moment.date()
                for pair in targets:
                    utc_row = table.row_for(pair, utc_day)
                    if utc_row is None:
                        continue
                    api_moment = moment + timedelta(
                        hours=utc_row.offset_hours(TimeRuleVariant.STANDARD)
                    )
                    api_row = table.row_for(pair, api_moment.date())
                    if api_row is not utc_row:
                        raise MarketDataValueError(
                            f"the time rule table looks up row {utc_row.row} by the UTC date but"
                            f" {api_row.row if api_row else None} by the API date at"
                            f" {UtcTime(moment)} for {pair} (D03 §15.9 T4)"
                        )
                checked += 1
                moment += minute
    return checked
