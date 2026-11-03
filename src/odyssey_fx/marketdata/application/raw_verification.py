"""研究期間の原ファイルの確認の読み取り（ADR-0036 第8項の 2、ADR-0040 第3項、D03 §17.4）。

確認済みハッシュの一覧（`marketdata.domain.verified_raw`）に原ファイルを載せる前に、品質確認の
許可（`RAW_FILE_VERIFICATION`。研究履歴の区分だけ）の下で各ファイルを 1 回読み、次を確かめる。

1. **読む前の照合**（`pre_read_problems`。読まない段）: 取得記録の状態が `ok`、要求の終わり
   （`requested_to_api_time`）が API の時刻の 2024-01-01T00:00 以前、`last_bar_time` ＋ 60 秒が
   同じ時刻以前。1 つでも外れたら許可を求めずに止める（呼び出し側。D03 §17.4 の 3 の 1）。
2. **読み取り**（`verify_raw_files`。ファイルごと）: バイト列の SHA-256 を取得記録の `sha256_gz`
   と照合し（一致しなければ展開しない）、展開して行ごとに**時刻の列（`time`）だけ**を整数として
   受け取る（価格・スプレッド・出来高の列は数値にしない・保持しない。読むのは adapters）。各行の
   API の時刻を時刻の規則の表で**標準と候補の両方の変換**で UTC に直し、足の終わり（UTC の分の
   開始 ＋ 1 分）が両方とも 2024-01-01T00:00Z より前であることを確かめる。あわせて行数・最初と
   最後の行の時刻が取得記録と一致することを確かめる（A1-5）。

**止まる場合**（ADR-0040 第3項の 4、A1-6）: 足の終わりが 2024-01-01T00:00Z 以降の行に当たったら
その場で止め、そのファイルを `FAIL`（`ROW_AT_OR_AFTER_2024`）にし、同じ通貨の残りのファイルも
読まない。表が覆わない暦日の行（表の検査 T2 の外）も、UTC に直せず 2024 年より前と言えないので、
同じくその場で止める（`TIME_RULE_NOT_COVERED`。表 版 1 は API の暦日 2024-01-01 以降を覆わない
ので、2024 年以降の行はまずこの形で現れる）。どちらも呼び出し側が「途中で停止」の終了の行を書き、
置き場と SHA-256 だけを人間に報告する。

**出力**（ADR-0040 第1項の 5）: ファイルごとの置き場・SHA-256・合否と、不合格の理由の種類の名前、
合格と不合格の数だけ。時刻・区間・価格は結果に入れない（読んだ時刻は判定にだけ使い、保持しない）。

application は I/O を持たない（D01 §2）。ファイルを読むのは adapters
（`marketdata.adapters.mt5_csv_source.read_month_times`）で、本モジュールは読んだ事実
（`MonthTimes`）を受け取って判定する。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.mt5_acceptance import Mt5FetchEntry
from odyssey_fx.marketdata.domain.access import SEALED_RESEARCH_UNTIL
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.time_rule import TimeRuleTable, TimeRuleVariant, api_date_of

__all__ = [
    "STOPPING_REASONS",
    "MonthTimes",
    "RawFileUnreadable",
    "RawFileVerdict",
    "RawVerification",
    "RawVerifyReason",
    "pre_read_problems",
    "verify_raw_files",
]

_MINUTE: Final = 60


class RawVerifyReason(Enum):
    """確認の読み取りの不合格の理由の種類（名前だけを出す。D03 §17.4 の 4）。"""

    #: バイト列の SHA-256 が取得記録の `sha256_gz` と違う（展開しない）。
    HASH_MISMATCH = "HASH_MISMATCH"
    #: ファイルを読めない・展開できない。
    FILE_UNREADABLE = "FILE_UNREADABLE"
    #: 見出しの行が列対応の宣言の列と違う。
    HEADER_MISMATCH = "HEADER_MISMATCH"
    #: 列の数が違う・時刻の列が整数でない行がある。
    ROW_MALFORMED = "ROW_MALFORMED"
    #: 時刻の規則の表が覆わない暦日の行がある（UTC に直せない。その場で止める）。
    TIME_RULE_NOT_COVERED = "TIME_RULE_NOT_COVERED"
    #: 足の終わりが 2024-01-01T00:00Z 以降の行がある（その場で止める。ADR-0040 第3項の 4）。
    ROW_AT_OR_AFTER_2024 = "ROW_AT_OR_AFTER_2024"
    #: 行数が取得記録の `rows` と違う。
    ROW_COUNT_MISMATCH = "ROW_COUNT_MISMATCH"
    #: 最初か最後の行の時刻が取得記録の `first_bar_time`・`last_bar_time` と違う。
    FIRST_LAST_MISMATCH = "FIRST_LAST_MISMATCH"


#: 当たったらその回の読み取りを止める理由（同じ通貨の残りのファイルも読まない）。
STOPPING_REASONS: Final = frozenset(
    {RawVerifyReason.ROW_AT_OR_AFTER_2024, RawVerifyReason.TIME_RULE_NOT_COVERED}
)


class RawFileUnreadable(Exception):  # noqa: N818 - 「読めない」という事実の名前
    """adapters が時刻の列を読む途中でファイルを読めなくなった（文に値を含めない）。"""


@dataclass(frozen=True, slots=True)
class MonthTimes:
    """adapters が読んだ月のファイル 1 つ（バイト列の SHA-256 と、時刻の列だけの行の列）。

    `times` は遅延の列で、1 行ごとに時刻の列の整数（列の数が違う・整数でない行は `None`）を返す。
    途中で読めなくなれば `RawFileUnreadable` を送出する。ファイルが無い・読めないときは
    `readable` を偽にし、`sha256` は空（ファイル単位の不合格。その回は止めない）。`sha256` が
    取得記録と違うときや
    `readable` が偽・`header_ok` が偽のときは読まない（`times` を消費しない）。
    """

    sha256: str
    readable: bool
    header_ok: bool
    times: Iterable[int | None]


@dataclass(frozen=True, slots=True)
class RawFileVerdict:
    """1 ファイルの合否（置き場・銘柄・SHA-256・不合格の理由の種類）。"""

    location: str
    symbol: Symbol
    sha256: str
    reason: RawVerifyReason | None

    @property
    def passed(self) -> bool:
        """合格か。"""
        return self.reason is None


@dataclass(frozen=True, slots=True)
class RawVerification:
    """1 通貨の確認の読み取りの結果。

    `stopped` は止まる理由（`STOPPING_REASONS`）に当たって途中で止めたか。止めたときは
    `verdicts` の最後がそのファイルで、`unread` はその後の読まなかったファイルの数。
    """

    symbol: Symbol
    verdicts: tuple[RawFileVerdict, ...]
    stopped: bool
    unread: int

    @property
    def passed(self) -> int:
        """合格のファイルの数。"""
        return sum(1 for item in self.verdicts if item.passed)

    @property
    def failed(self) -> int:
        """不合格のファイルの数。"""
        return sum(1 for item in self.verdicts if not item.passed)


def pre_read_problems(
    entries: Sequence[Mt5FetchEntry], *, until: UtcTime = SEALED_RESEARCH_UNTIL
) -> tuple[str, ...]:
    """読む前の照合（D03 §17.4 の 3 の 1）。外れた要求の識別と理由の名前を返す（無ければ空）。

    状態が `ok`、要求の終わりが API の時刻の `until` 以前、`last_bar_time` ＋ 60 秒が同じ時刻
    以前（行のあるファイル）。取得記録の値だけを見る（原ファイルを読まない）。
    """
    limit = int(until.value.timestamp())
    problems: list[str] = []
    for entry in entries:
        if not entry.acquired:
            problems.append(f"{entry.rid}: NOT_ACQUIRED")
            continue
        if entry.requested_to_api > limit:
            problems.append(f"{entry.rid}: REQUEST_PAST_2024")
        if entry.rows and (entry.last_bar_time is None or entry.last_bar_time + _MINUTE > limit):
            problems.append(f"{entry.rid}: LAST_BAR_PAST_2024")
    return tuple(problems)


def _read_problem(
    entry: Mt5FetchEntry, read: MonthTimes, table: TimeRuleTable, symbol: Symbol, limit: int
) -> RawVerifyReason | None:
    if not read.readable:
        return RawVerifyReason.FILE_UNREADABLE
    if read.sha256 != entry.sha256_gz:
        return RawVerifyReason.HASH_MISMATCH
    if not read.header_ok:
        return RawVerifyReason.HEADER_MISMATCH
    pair = str(symbol)
    count = 0
    first: int | None = None
    last: int | None = None
    try:
        for api_time in read.times:
            if api_time is None:
                return RawVerifyReason.ROW_MALFORMED
            count += 1
            if first is None:
                first = api_time
            last = api_time
            rule = table.row_for(pair, api_date_of(api_time))
            if rule is None:
                return RawVerifyReason.TIME_RULE_NOT_COVERED
            minute = api_time - api_time % _MINUTE
            for variant in (TimeRuleVariant.STANDARD, TimeRuleVariant.CANDIDATE):
                bar_end = minute - rule.offset_hours(variant) * 3600 + _MINUTE
                if bar_end >= limit:
                    return RawVerifyReason.ROW_AT_OR_AFTER_2024
    except RawFileUnreadable:
        return RawVerifyReason.FILE_UNREADABLE
    if count != entry.rows:
        return RawVerifyReason.ROW_COUNT_MISMATCH
    if count and (first != entry.first_bar_time or last != entry.last_bar_time):
        return RawVerifyReason.FIRST_LAST_MISMATCH
    return None


def verify_raw_files(
    entries: Sequence[Mt5FetchEntry],
    *,
    symbol: Symbol,
    locations: Mapping[str, str],
    open_times: Callable[[Mt5FetchEntry], MonthTimes],
    table: TimeRuleTable,
    until: UtcTime = SEALED_RESEARCH_UNTIL,
) -> RawVerification:
    """1 通貨のファイルを順に確かめる（取得記録の順。モジュールの説明文の 2）。

    `locations` は要求の識別 → 置き場（リポジトリの根からの正規の置き場）。`open_times` は
    adapters の読み取り（品質確認の許可の値が覆うことを確かめてから展開する）。止まる理由に
    当たったら、残りのファイルを開かずに返す。
    """
    if any(entry.symbol != symbol for entry in entries):
        raise MarketDataValueError("verify_raw_files reads the files of one symbol")
    limit = int(until.value.timestamp())
    verdicts: list[RawFileVerdict] = []
    for index, entry in enumerate(entries):
        read = open_times(entry)
        reason = _read_problem(entry, read, table, symbol, limit)
        verdicts.append(
            RawFileVerdict(
                location=locations[entry.rid], symbol=symbol, sha256=read.sha256, reason=reason
            )
        )
        if reason in STOPPING_REASONS:
            return RawVerification(
                symbol=symbol,
                verdicts=tuple(verdicts),
                stopped=True,
                unread=len(entries) - index - 1,
            )
    return RawVerification(symbol=symbol, verdicts=tuple(verdicts), stopped=False, unread=0)
