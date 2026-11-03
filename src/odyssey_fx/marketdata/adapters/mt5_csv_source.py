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
3. 許可の値が無いときは、さらに、ファイルの正規の置き場（`data/raw/oanda_mt5/<取得日>/<通貨>/
   <ファイル>`。実際に読む場所に依らない）が確認済みハッシュの一覧（`VerifiedRawList`。
   D03 §17.3）に載り、読む前に計算したバイト列の SHA-256 が一覧の値と一致することを求める
   （ADR-0036 第8項、ADR-0040 第4項、D03 §17.7）。一覧が無ければ何も読まない。2 の確かめは
   一覧に加えて続けて当てる（A1-8）。受入れは全ファイルを読む前に
   `require_listed_before_reading` で全件を照らし、読むときにも同じバイト列で照らし直す。

**確認の読み取り**（ADR-0036 第8項の 2、ADR-0040 第3項、D03 §17.4）: `read_month_times` は
許可の値（研究履歴の区分の確認の読み取り）が覆うファイルを、時刻の列（`time`）だけを返す形で
読む。価格・スプレッド・出来高の列は数値にしない・保持しない。判定は
`marketdata.application.raw_verification`。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import zlib
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
from odyssey_fx.marketdata.application.raw_verification import MonthTimes, RawFileUnreadable
from odyssey_fx.marketdata.domain.access import SEALED_RESEARCH_UNTIL
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, MarketDataValueError
from odyssey_fx.marketdata.domain.quality_access import RESEARCH_QUALITY_CLASSES, QualityReadPermit
from odyssey_fx.marketdata.domain.verified_raw import VerifiedRawList

__all__ = [
    "SEALED_RAW_DIRECTORY",
    "file_sha256",
    "read_fetch_manifest",
    "read_month_file",
    "read_month_times",
    "read_symbol_files",
    "require_listed_before_reading",
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
    verified: VerifiedRawList | None = None,
    digest: str | None = None,
) -> None:
    """月のファイルを行に分けて読んでよいかを確かめる（読めなければ `HoldoutAccessViolation`）。

    `location` はリポジトリの根からの POSIX 相対パス（許可の対象・一覧の行の置き場と同じ書き方。
    許可なしの読み取りでは正規の置き場）。`digest` は読む前に計算したバイト列の SHA-256（渡さな
    ければここで計算する）。許可の値があるときは置き場とハッシュの両方の一致を求める。無いときは
    封印の置き場の下と、2024-01-01 を越える要求のファイルを拒み、確認済みハッシュの一覧
    `verified` に置き場が載りハッシュが一致することを求める（モジュールの説明文）。
    """
    if read_permit is not None:
        if not isinstance(read_permit, QualityReadPermit):
            raise MarketDataValueError("read_permit must be a QualityReadPermit")
        if location is None:
            raise MarketDataValueError("a permitted raw read needs the file's repository location")
        digest = file_sha256(path) if digest is None else digest
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
    if verified is None:
        raise HoldoutAccessViolation(
            f"{path} is read without a quality-check permit, but no verified hash list"
            " (configs/datasources/oanda_mt5_m1_verified_raw_<fetch date>.yaml) was given; a raw"
            " file is taken to contain data from 2024 unless it is listed. Nothing was read"
            " (ADR-0036 第8項, D03 §17.7)"
        )
    if not isinstance(verified, VerifiedRawList):
        raise MarketDataValueError("verified must be a VerifiedRawList")
    if location is None:
        raise MarketDataValueError(
            "a raw read without a permit needs the file's canonical location"
        )
    listed = verified.sha256_of(location)
    if listed is None:
        raise HoldoutAccessViolation(
            f"the raw file {location} is not in the verified hash list {verified.source}; it is"
            " read only under a quality-check permit. Nothing was read (ADR-0036 第8項,"
            " D03 §17.7 の 2)"
        )
    digest = file_sha256(path) if digest is None else digest
    if digest != listed:
        raise HoldoutAccessViolation(
            f"the raw file {location} has SHA-256 {digest}, but the verified hash list"
            f" {verified.source} records {listed}. Nothing was read (ADR-0036 第8項,"
            " D03 §17.7 の 3)"
        )


def require_listed_before_reading(
    root: Path,
    entries: Iterable[Mt5FetchEntry],
    *,
    location_prefix: str,
    verified: VerifiedRawList | None,
    months: Iterable[tuple[int, int]] | None = None,
) -> int:
    """許可なしで読む前に、読むすべてのファイルを確認済みハッシュの一覧で照らす（D03 §17.7）。

    ファイルのバイト列をダイジェストへ畳み込むだけで、行に分けない・解析しない（ADR-0036 第8項の
    2）。1 つでも外れれば `HoldoutAccessViolation`（何も読まない）。照らしたファイルの数を返す。
    `location_prefix` は `root` の正規の置き場（`data/raw/oanda_mt5/<取得日>`）。
    """
    wanted = None if months is None else set(months)
    count = 0
    for entry in entries:
        if not entry.acquired:
            continue
        if wanted is not None and not _month_in(entry, wanted):
            continue
        require_raw_read_allowed(
            root / entry.file,
            entry,
            read_permit=None,
            location=f"{location_prefix}/{entry.file}",
            verified=verified,
        )
        count += 1
    return count


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
    verified: VerifiedRawList | None = None,
) -> Mt5ReadFile:
    """月のファイル 1 つを読む。見出しが宣言と違えば `header_ok` を偽にする（照合は
    application）。

    読む前に `require_raw_read_allowed` で関所の下の読み取りか（許可なしなら一覧に載りハッシュが
    一致するか）を確かめる。確かめに使うハッシュは、行に分けるのと同じバイト列から計算する（確かめた
    後にファイルが替わっても、確かめていないバイト列を読まない）。`location_prefix` は `root` の
    リポジトリの根からの POSIX 相対パス（許可の値・一覧を渡すときに要る）。
    """
    path = root / entry.file
    location = None if location_prefix is None else f"{location_prefix}/{entry.file}"
    raw = path.read_bytes()
    require_raw_read_allowed(
        path,
        entry,
        read_permit=read_permit,
        location=location,
        verified=verified,
        digest=hashlib.sha256(raw).hexdigest(),
    )
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
    verified: VerifiedRawList | None = None,
) -> dict[str, Mt5ReadFile]:
    """取得済みの要求のファイルを読む（`months` を渡せば、その年月の要求だけ）。

    どのファイルも読む前に関所の下の読み取りかを確かめる（`require_raw_read_allowed`）。1 つでも
    外れれば、その前に読んだものも返さずに止まる。許可なしの受入れは、呼ぶ前に
    `require_listed_before_reading` で全件を照らす（何も読まずに止まるため）。
    """
    wanted = None if months is None else set(months)
    out: dict[str, Mt5ReadFile] = {}
    for entry in entries:
        if not entry.acquired:
            continue
        if wanted is not None and not _month_in(entry, wanted):
            continue
        out[entry.rid] = read_month_file(
            root,
            entry,
            columns,
            read_permit=read_permit,
            location_prefix=location_prefix,
            verified=verified,
        )
    return out


def read_month_times(
    path: Path,
    entry: Mt5FetchEntry,
    columns: Sequence[str],
    *,
    read_permit: QualityReadPermit,
    location: str,
) -> MonthTimes:
    """確認の読み取りの 1 ファイル（D03 §17.4 の 3 の 3）。時刻の列だけを遅延の列で返す。

    バイト列を 1 回読み、SHA-256 が取得記録の `sha256_gz` と違えば展開せずに返す（不合格の判定は
    application）。一致すれば、研究履歴の区分の読み取りの許可の値がその置き場とハッシュを覆うことを
    確かめてから展開する（覆わなければ `HoldoutAccessViolation`。何も展開しない）。行は 1 行ずつ
    展開し、時刻の列の文字列だけを整数にする（価格の列は分けた文字列を捨てる。数値にしない）。
    例外の文に行の中身を入れない（`RawFileUnreadable`）。
    """
    if not isinstance(read_permit, QualityReadPermit):
        raise MarketDataValueError("a verification read needs a QualityReadPermit")
    if read_permit.classes != RESEARCH_QUALITY_CLASSES:
        raise MarketDataValueError("a verification read is only under a research-history permit")
    try:
        raw = path.read_bytes()
    except OSError:
        # 欠けた・読めないファイルはファイル単位の不合格（その回を止めない。ハッシュは無い）。
        return MonthTimes(sha256="", readable=False, header_ok=False, times=())
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry.sha256_gz:
        return MonthTimes(sha256=digest, readable=True, header_ok=False, times=())
    if not read_permit.covers_raw_file(location, digest):
        raise HoldoutAccessViolation(
            f"the quality-check permit {read_permit.permit_id} does not cover the raw file"
            f" {location} with SHA-256 {digest}; nothing was read (ADR-0036 第8項・第9項の 3)"
        )
    stream = gzip.GzipFile(fileobj=io.BytesIO(raw))
    try:
        header = stream.readline().decode("utf-8").rstrip("\r\n")
    except (OSError, EOFError, UnicodeDecodeError, zlib.error):
        return MonthTimes(sha256=digest, readable=False, header_ok=False, times=())
    if header != ",".join(columns):
        return MonthTimes(sha256=digest, readable=True, header_ok=False, times=())
    width = len(columns)
    index = list(columns).index("time")

    def times() -> Iterable[int | None]:
        try:
            for line in stream:
                cells = line.decode("utf-8").rstrip("\r\n").split(",")
                cell = cells[index] if len(cells) == width else ""
                yield int(cell) if cell.isascii() and cell.isdigit() else None
        except (OSError, EOFError, UnicodeDecodeError, zlib.error):
            raise RawFileUnreadable("the raw file cannot be read to its end") from None

    return MonthTimes(sha256=digest, readable=True, header_ok=True, times=times())


def _month_in(entry: Mt5FetchEntry, wanted: set[tuple[int, int]]) -> bool:
    """要求の API の時刻の年月が `wanted` に入るか。"""
    moment = datetime.fromtimestamp(entry.requested_from_api, UTC)
    return (moment.year, moment.month) in wanted
