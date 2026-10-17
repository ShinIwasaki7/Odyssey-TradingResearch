"""MT5 経路の原データの読込（D03 §15.3 の 1・2。ADR-0013 の置き場）。

`data/raw/oanda_mt5/<取得日>/manifest.json`（取得記録）と、各月の gzip の CSV を読む。
照合（SHA-256・
行数・最初と最後の足）は application の `verify_fetch_files` が行い、ここは事実（読んだバイト列の
ハッシュと行）だけを返す。原データは読むだけで変えない。
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
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = ["file_sha256", "read_fetch_manifest", "read_month_file", "read_symbol_files"]


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


def read_month_file(root: Path, entry: Mt5FetchEntry, columns: Sequence[str]) -> Mt5ReadFile:
    """月のファイル 1 つを読む。見出しが宣言と違えば `header_ok` を偽にする（照合は
    application）。"""
    path = root / entry.file
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
) -> dict[str, Mt5ReadFile]:
    """取得済みの要求のファイルを読む（`months` を渡せば、その年月の要求だけ）。"""
    wanted = None if months is None else set(months)
    out: dict[str, Mt5ReadFile] = {}
    for entry in entries:
        if not entry.acquired:
            continue
        if wanted is not None and not _month_in(entry, wanted):
            continue
        out[entry.rid] = read_month_file(root, entry, columns)
    return out


def _month_in(entry: Mt5FetchEntry, wanted: set[tuple[int, int]]) -> bool:
    """要求の API の時刻の年月が `wanted` に入るか。"""
    moment = datetime.fromtimestamp(entry.requested_from_api, UTC)
    return (moment.year, moment.month) in wanted
