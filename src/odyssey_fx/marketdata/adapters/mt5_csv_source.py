"""MT5 経路の原データの読込（D03 §15.3 の 1・2。ADR-0013 の置き場）。

`data/raw/oanda_mt5/<取得日>/manifest.json`（取得記録）と、各月の gzip の CSV を読む。
照合（SHA-256・
行数・最初と最後の足）は application の `verify_fetch_files` が行い、ここは事実（読んだバイト列の
ハッシュと行）だけを返す。原データは読むだけで変えない。

**関所の下の読み取り**（ADR-0036 第8項・第9項の 3、D11 §10 の受入条件 25。順 6 の S4a）: 月の
ファイル（価格を含む）を行に分けて読む前に、次を確かめる。満たさなければ何も読まずに
`HoldoutAccessViolation` で止まる。

1. 読み取りの許可の値（`QualityReadPermit`。関所が出し、`app` の品質確認専用の入口が渡す）が
   あるときは、そのファイルの置き場（リポジトリの根からの POSIX 相対パス）とバイト列の SHA-256 が
   許可の対象と一致すること（`covers_raw_file`）。ハッシュの計算はバイト列をダイジェストへ畳み込む
   だけで、行に分けない・解析しない（ADR-0036 第8項の 2）。
2. 許可の値が無いとき（研究期間の受入れ）は、封印の原ファイルの置き場
   （`data/raw/oanda_mt5_sealed/`。D03 §16.5.4）の下のファイルを読まない。取得記録で要求の終わりが
   API の時刻の 2024-01-01T00:00 より後の要求のファイルも読まない（研究期間の取得の上限を越える
   要求。原ファイルは既定で 2024 年以降を含むものとして扱う。ADR-0036 第8項）。取得記録
   （`manifest.json`。件数・時刻・ハッシュだけで価格を含まない）は許可なしで読む。
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.marketdata.application.mt5_acceptance import (
    Mt5FetchEntry,
    Mt5RawRow,
    Mt5ReadFile,
)
from odyssey_fx.marketdata.domain.access import SEALED_RESEARCH_UNTIL
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, MarketDataValueError
from odyssey_fx.marketdata.domain.quality_access import QualityReadPermit

__all__ = [
    "SEALED_RAW_DIRECTORY",
    "file_sha256",
    "read_fetch_manifest",
    "read_month_file",
    "read_symbol_files",
    "require_raw_read_allowed",
]

#: 封印の原ファイルの置き場の名前（`data/raw/oanda_mt5_sealed/<取得日>/<通貨>/`。D03 §16.5.4、
#: PR #109 の仮置き S2b-6）。許可の値なしでは、この名前のディレクトリの下のファイルを読まない。
SEALED_RAW_DIRECTORY = "oanda_mt5_sealed"


def require_raw_read_allowed(
    path: Path,
    entry: Mt5FetchEntry,
    *,
    read_permit: QualityReadPermit | None,
    location: str | None,
) -> None:
    """月のファイルを行に分けて読んでよいかを確かめる（読めなければ `HoldoutAccessViolation`）。

    `location` はリポジトリの根からの POSIX 相対パス（許可の対象の置き場と同じ書き方）。許可の値が
    あるときは置き場とハッシュの両方の一致を求める。無いときは封印の置き場の下と、2024-01-01 を
    越える要求のファイルを拒む（モジュールの説明文）。
    """
    if read_permit is not None:
        if not isinstance(read_permit, QualityReadPermit):
            raise MarketDataValueError("read_permit must be a QualityReadPermit")
        if location is None:
            raise MarketDataValueError("a permitted raw read needs the file's repository location")
        digest = file_sha256(path)
        if not read_permit.covers_raw_file(location, digest):
            raise HoldoutAccessViolation(
                f"the quality-check permit {read_permit.permit_id} does not cover the raw file"
                f" {location} with SHA-256 {digest}; nothing was read (ADR-0036 第8項・第9項の 3)"
            )
        return
    if SEALED_RAW_DIRECTORY in Path(path).parts:
        raise HoldoutAccessViolation(
            f"{path} is a sealed raw file; it is read only under a quality-check permit of the"
            " holdout gate (data accept-mt5 --sealed). Nothing was read (ADR-0036 第8項・第9項の 3)"
        )
    limit = int(SEALED_RESEARCH_UNTIL.value.timestamp())
    if entry.requested_to_api > limit:
        raise HoldoutAccessViolation(
            f"the request {entry.rid} reaches past 2024-01-01T00:00 (API time); a raw file is"
            " taken to contain data from 2024 unless proven otherwise, and is read only under a"
            " quality-check permit. Nothing was read (ADR-0036 第8項)"
        )


def file_sha256(path: Path) -> str:
    """ファイルのバイト列の SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _epoch(text: str) -> int:
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise MarketDataValueError(f"the fetch record time {text!r} has no time zone")
    return int(moment.timestamp())


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketDataValueError(f"the fetch record has a non-integer time {value!r}")
    return value


def read_fetch_manifest(path: Path) -> tuple[Mt5FetchEntry, ...]:
    """取得記録の要求をすべて読む（通貨・月の順）。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, Mapping):
        raise MarketDataValueError(f"{path}: the fetch record has no 'files' mapping")
    entries: list[Mt5FetchEntry] = []
    for rid, entry in files.items():
        entries.append(
            Mt5FetchEntry(
                rid=str(rid),
                symbol=Symbol(str(entry["pair"])),
                file=str(entry["file"]),
                status=str(entry["status"]),
                requested_from_api=_epoch(str(entry["requested_from_api_time"])),
                requested_to_api=_epoch(str(entry["requested_to_api_time"])),
                rows=int(entry.get("rows") or 0),
                first_bar_time=_optional_int(entry.get("first_bar_time")),
                last_bar_time=_optional_int(entry.get("last_bar_time")),
                sha256_gz=str(entry.get("sha256_gz") or ""),
                sha256_csv=str(entry.get("sha256_csv") or ""),
            )
        )
    return tuple(sorted(entries, key=lambda item: (str(item.symbol), item.requested_from_api)))


def read_month_file(
    root: Path,
    entry: Mt5FetchEntry,
    columns: Sequence[str],
    *,
    read_permit: QualityReadPermit | None = None,
    location_prefix: str | None = None,
) -> Mt5ReadFile:
    """月のファイル 1 つを読む。見出しが宣言と違えば `header_ok` を偽にする（照合は
    application）。

    読む前に `require_raw_read_allowed` で関所の下の読み取りかを確かめる。`location_prefix` は
    `root` のリポジトリの根からの POSIX 相対パス（許可の値を渡すときに要る）。
    """
    path = root / entry.file
    location = None if location_prefix is None else f"{location_prefix}/{entry.file}"
    require_raw_read_allowed(path, entry, read_permit=read_permit, location=location)
    raw = path.read_bytes()
    text = gzip.decompress(raw)
    lines = text.decode("utf-8").splitlines()
    header_ok = bool(lines) and lines[0] == ",".join(columns)
    rows: list[Mt5RawRow] = []
    if header_ok:
        index = {name: position for position, name in enumerate(columns)}
        for line in lines[1:]:
            cells = line.split(",")
            if len(cells) != len(columns):
                raise MarketDataValueError(f"{path}: a row has {len(cells)} cells")
            rows.append(
                Mt5RawRow(
                    api_time=int(cells[index["time"]]),
                    open=cells[index["open"]],
                    high=cells[index["high"]],
                    low=cells[index["low"]],
                    close=cells[index["close"]],
                    spread=int(cells[index["spread"]]),
                )
            )
    return Mt5ReadFile(
        entry=entry,
        sha256_gz=hashlib.sha256(raw).hexdigest(),
        sha256_csv=hashlib.sha256(text).hexdigest(),
        header_ok=header_ok,
        rows=tuple(rows),
    )


def read_symbol_files(
    root: Path,
    entries: Iterable[Mt5FetchEntry],
    columns: Sequence[str],
    *,
    months: Iterable[tuple[int, int]] | None = None,
    read_permit: QualityReadPermit | None = None,
    location_prefix: str | None = None,
) -> dict[str, Mt5ReadFile]:
    """取得済みの要求のファイルを読む（`months` を渡せば、その年月の要求だけ）。

    どのファイルも読む前に関所の下の読み取りかを確かめる（`require_raw_read_allowed`）。1 つでも
    外れれば、その前に読んだものも返さずに止まる。
    """
    wanted = None if months is None else set(months)
    out: dict[str, Mt5ReadFile] = {}
    for entry in entries:
        if not entry.acquired:
            continue
        if wanted is not None and not _month_in(entry, wanted):
            continue
        out[entry.rid] = read_month_file(
            root, entry, columns, read_permit=read_permit, location_prefix=location_prefix
        )
    return out


def _month_in(entry: Mt5FetchEntry, wanted: set[tuple[int, int]]) -> bool:
    """要求の API の時刻の年月が `wanted` に入るか。"""
    moment = datetime.fromtimestamp(entry.requested_from_api, UTC)
    return (moment.year, moment.month) in wanted
