"""研究期間の原ファイルの確認済みハッシュの一覧（ADR-0036 第8項、ADR-0040 第4項、D03 §17.3）。

原ファイル（`data/raw/` 配下）は既定で 2024 年以降を含むものとして扱う。品質確認の許可を要しない
読み取り（研究期間の受入れ `odyssey-fx data accept-mt5`）が読めるのは、この一覧に載り、読む前に
計算したバイト列の SHA-256 が一覧の値と一致するファイルだけである（ADR-0036 第8項。D03 §17.7）。

一覧は `configs/datasources/oanda_mt5_m1_verified_raw_<取得日>.yaml`（D03 §17.3 の 1。A1-2）に
取得日ごとに 1 つ置き、人間が PR の merge で足す（D17 の A3a）。読込（`app.config.verified_raw`）が
YAML を読み、本モジュールの型が置き場・ハッシュ・重複を構築時に確かめる。どれかに反すれば一覧全体を
作らない（許可なしの読み取りは 1 つもできない。fail-closed。D03 §17.3 の 4）。

一覧の行は置き場・銘柄・gzip のままの SHA-256・確認した閲覧の識別子だけを持つ（結果は `PASS` の
行だけを載せるので型には持たない。時刻・件数・価格は書かない）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Final

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.access import SEALED_RESEARCH_UNTIL
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = [
    "RESEARCH_RAW_ROOT",
    "VerifiedRawFile",
    "VerifiedRawList",
    "research_raw_location_problem",
]

#: 研究期間の原ファイルの置き場（`data/raw/oanda_mt5/<取得日>/<通貨>/`。D03 §15.1、ADR-0013、
#: ADR-0040 第1項の 3）。部分ごとに比べる（`oanda_mt5_sealed` を前方一致で取り違えない）。
RESEARCH_RAW_ROOT: Final = ("data", "raw", "oanda_mt5")


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def research_raw_location_problem(
    location: str, symbol: Symbol | None, *, fetch_date: str | None = None
) -> str | None:
    """置き場が `data/raw/oanda_mt5/<取得日>/<通貨>/<ファイル>` の形で、通貨の場所と銘柄が
    一致するか（`fetch_date` を渡せばその取得日か）。合えば `None`、外れれば理由を返す。

    絶対の置き場・逆斜線・`..`・`.`・空の部分を拒む（ADR-0040 第1項の 3。封印の置き場の規則と
    同じ）。
    """
    if not isinstance(location, str) or not location:
        return "the location must be a non-empty str"
    parts = location.split("/")
    root = len(RESEARCH_RAW_ROOT)
    if (
        location.startswith("/")
        or "\\" in location
        or any(part in ("", ".", "..") for part in parts)
        or len(parts) != root + 3
        or tuple(PurePosixPath(location).parts[:root]) != RESEARCH_RAW_ROOT
    ):
        return (
            f"the location {location} is not data/raw/oanda_mt5/<date>/<symbol>/<file>"
            " (D03 §17.3 の 4)"
        )
    if fetch_date is not None and parts[root] != fetch_date:
        return f"the location {location} is not under the fetch date {fetch_date}"
    if symbol is None or parts[root + 1] != str(symbol):
        return f"the location {location} is not in the directory of its symbol {symbol}"
    return None


@dataclass(frozen=True, slots=True)
class VerifiedRawFile:
    """確認済みの原ファイル 1 行（置き場・銘柄・gzip のままの SHA-256・確認した閲覧の識別子）。"""

    location: str
    symbol: Symbol
    sha256: str
    access_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise MarketDataValueError("VerifiedRawFile.symbol must be a Symbol")
        problem = research_raw_location_problem(self.location, self.symbol)
        if problem is not None:
            raise MarketDataValueError(problem)
        if not _is_hex64(self.sha256):
            raise MarketDataValueError(
                f"VerifiedRawFile.sha256 of {self.location} must be 64 lowercase hex digits"
            )
        if (
            not isinstance(self.access_id, str)
            or not self.access_id
            or self.access_id != self.access_id.strip()
        ):
            raise MarketDataValueError(
                f"VerifiedRawFile.access_id of {self.location} must be a non-empty str"
            )


@dataclass(frozen=True, slots=True)
class VerifiedRawList:
    """取得日 1 つの確認済みハッシュの一覧（D03 §17.3）。

    `declared_period_end` はファイルの期間の終わりの宣言（ADR-0036 第8項の条件 1。一覧の全行に
    当たる）。2024-01-01T00:00Z より後なら作らない。`source` は一覧のファイルの置き場（誤りの
    説明に使う）。`source_sha256` は一覧のファイルの SHA-256（受入れの画面出力の照合の行に出す）。
    """

    fetch_date: str
    fetch_manifest_sha256: str
    declared_period_end: UtcTime
    files: tuple[VerifiedRawFile, ...]
    source: str
    source_sha256: str
    _by_location: Mapping[str, VerifiedRawFile] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.fetch_date, str)
            or not self.fetch_date
            or "/" in self.fetch_date
            or "\\" in self.fetch_date
            or self.fetch_date in (".", "..")
        ):
            raise MarketDataValueError(
                f"the fetch date {self.fetch_date!r} cannot name a directory"
            )
        if not _is_hex64(self.fetch_manifest_sha256):
            raise MarketDataValueError(
                "VerifiedRawList.fetch_manifest_sha256 must be 64 lowercase hex digits"
            )
        if not isinstance(self.declared_period_end, UtcTime):
            raise MarketDataValueError("VerifiedRawList.declared_period_end must be a UtcTime")
        if self.declared_period_end > SEALED_RESEARCH_UNTIL:
            raise MarketDataValueError(
                f"the declared period end {self.declared_period_end} is after"
                f" {SEALED_RESEARCH_UNTIL}; a verified list only declares files that end before"
                " 2024 (ADR-0036 第8項の条件 1, D03 §17.3 の 4)"
            )
        if (
            not isinstance(self.files, tuple)
            or not self.files
            or not all(isinstance(item, VerifiedRawFile) for item in self.files)
        ):
            raise MarketDataValueError("VerifiedRawList.files must be a non-empty tuple")
        by_location: dict[str, VerifiedRawFile] = {}
        for item in self.files:
            problem = research_raw_location_problem(
                item.location, item.symbol, fetch_date=self.fetch_date
            )
            if problem is not None:
                raise MarketDataValueError(problem)
            if item.location in by_location:
                raise MarketDataValueError(f"the location {item.location} appears twice")
            by_location[item.location] = item
        if not isinstance(self.source, str) or not self.source:
            raise MarketDataValueError("VerifiedRawList.source must be a non-empty str")
        if not _is_hex64(self.source_sha256):
            raise MarketDataValueError("VerifiedRawList.source_sha256 must be 64 lowercase hex")
        object.__setattr__(self, "_by_location", by_location)

    def sha256_of(self, location: str) -> str | None:
        """一覧のその置き場の SHA-256（載っていなければ `None`）。"""
        item = self._by_location.get(location)
        return None if item is None else item.sha256
