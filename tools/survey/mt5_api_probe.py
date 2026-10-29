"""OANDA の MT5 Python API で、過去の tick と 1 分足が取れるかを小さく確かめる道具（D12 §11.5）。

2 つの使い方がある。

- ``probe``（Windows 上で人間が実行する）: ログイン済みの MT5 の端末に ``MetaTrader5`` パッケージで
  つなぎ、決めた短い区間の tick（``copy_ticks_range``・``COPY_TICKS_ALL``）と 1 分足
  （``copy_rates_range``・``TIMEFRAME_M1``）を別々に要求する。要求ごとに、要求の期間・返った期間・件数・
  エラーを要約の JSON に書き、返った行を CSV に保存して SHA-256 を記録する。
- ``compare``（Mac で実行する。MT5 は要らない）: ``probe`` が保存した USDJPY の tick と、
  マイページの配布 CSV の同じ区間を、時刻のずれの候補ごとに突き合わせ、一致した件数だけを出す
  （価格は出さない）。

守ること（D12 §11.4・人間の決定 D12-21）:

- 注文・売買の機能は使わない。使う API は ``ALLOWED_API_CALLS`` だけ（テストで確かめる）。
- 研究期間の段（``probe``・``timesurvey``・``fullfetch`` など）の要求の終わりは
  2024-01-01T00:00Z より前に限る（``REQUEST_END_LIMIT``。研究期間の上限）。超える要求は出さずに
  止まる。2024 年以降の取得は制約の改定文 ADR-0037 の窓（``sealedfetch`` の段。窓は版管理の
  設定 1 つから読む）だけで行う。
- 要求の時刻は UTC の ``datetime`` で作る（MetaQuotes の公式の仕様:
  "MetaTrader 5 stores tick and bar open time in UTC time zone (without the shift)"）。
- API の時刻と配布 CSV の時刻の変換を混同しない。配布 CSV は MT5 のサーバー時刻
  （OANDA の FAQ 2190。ニューヨークの現地時刻 + 7 時間）で、``compare`` が UTC に直す。
  API の時刻にはこの変換を当てず、ずれの候補（``CANDIDATE_SHIFTS_HOURS``）ごとの
  一致の件数で関係を確かめる。
- 1 分足が返っても、tick が 0 件なら「tick の履歴が取れた」とは記録しない（``tick_history`` の欄）。
- 返った行のうち要求の区間の外のものは保存しない（``split_by_window``）。端末は区間に足が無いと
  区間の外の足を返すことがある（2026-10-15 の試験で、2019 年の要求に 2026 年の足が 1 本返った）。
  区間の外の行は、件数と最小・最大の時刻だけを要約に書き、価格は保存しない。
- 口座の情報（``account_info``）は読まない。端末が自動で保存した最近の履歴のファイルは読まない。

実行の例::

    python -m tools.survey.mt5_api_probe probe --phase usdjpy-time --out-dir probe_out
    python -m tools.survey.mt5_api_probe compare --probe-dir probe_out \
        --oanda-csv data/raw/oanda/USDJPY/ticks_USDJPY-oj5k_2019-03.csv
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib
import io
import json
import os
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

#: 研究期間の段の要求の終わりの上限（この時刻以降を含む要求は出さない）。研究期間
#: 2017〜2023 年（D12）。研究期間の上限で、許可の窓ではない。2024 年以降の取得は ADR-0037 の窓
#: （``sealedfetch``。ADR-0037 第4項の 6）。
REQUEST_END_LIMIT = datetime(2024, 1, 1, tzinfo=UTC)

#: この道具が呼んでよい MetaTrader5 の関数（注文・売買・口座の関数は含めない）。
ALLOWED_API_CALLS: frozenset[str] = frozenset(
    {
        "initialize",
        "version",
        "terminal_info",
        "symbols_get",
        "symbol_info",
        "symbol_select",
        "copy_ticks_range",
        "copy_rates_range",
        "last_error",
        "shutdown",
    }
)

#: 配布 CSV と API の tick を突き合わせるときの、API の時刻に足すずれの候補（時間）。
CANDIDATE_SHIFTS_HOURS: tuple[int, ...] = (-3, -2, 0, 2, 3)

#: 配布 CSV の時刻（MT5 のサーバー時刻）= ニューヨークの現地時刻 + 7 時間（FAQ 2190・D12 §9.2）。
NEW_YORK_TZ_NAME = "America/New_York"
SERVER_MINUS_NY_LOCAL = timedelta(hours=7)

USDJPY = "USDJPY"
OTHER_PAIRS: tuple[str, ...] = (
    "EURJPY",
    "EURUSD",
    "GBPJPY",
    "USDCHF",
    "AUDJPY",
    "GBPUSD",
    "AUDUSD",
)

KIND_TICKS = "ticks"
KIND_M1 = "m1"


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@dataclass(frozen=True)
class Window:
    """要求する区間（UTC、半開区間 [start, end)）。"""

    label: str
    start: datetime
    end: datetime


def _two_hours(label: str, start: str) -> Window:
    s = _utc(start)
    return Window(label, s, s + timedelta(hours=2))


#: 段 1: USDJPY の時刻の照合（米国の冬時間と夏時間の 1 区間ずつ）。
#: 取得済みの配布 CSV 2019-03 と比べる。
PHASE_USDJPY_TIME: tuple[tuple[str, Window], ...] = (
    (USDJPY, _two_hours("2019-03-winter", "2019-03-05T00:00")),
    (USDJPY, _two_hours("2019-03-summer", "2019-03-19T00:00")),
)
#: 段 2: USDJPY の研究期間の始めと終わりの短い区間。
PHASE_USDJPY_RANGE: tuple[tuple[str, Window], ...] = (
    (USDJPY, _two_hours("2017-01-first", "2017-01-03T00:00")),
    (USDJPY, _two_hours("2023-12-last", "2023-12-27T00:00")),
)
#: 段 3: 他の 7 通貨の 2017・2019・2023 年の短い区間と、研究期間の最初の営業日の 2 時間。
OTHER_WINDOWS: tuple[Window, ...] = (
    _two_hours("2017-01-first", "2017-01-03T00:00"),
    _two_hours("2017-03", "2017-03-07T00:00"),
    _two_hours("2019-03", "2019-03-05T00:00"),
    _two_hours("2023-03", "2023-03-07T00:00"),
)
PHASE_OTHERS: tuple[tuple[str, Window], ...] = tuple(
    (pair, w) for pair in OTHER_PAIRS for w in OTHER_WINDOWS
)
#: 時刻のずれの原因を見分ける追加の 1 区間（人間の第 9 回の指示の範囲。D12 付録 K-5.2）。
#: 欧州の夏時間の切り替え（2023-03-26）の後で、米国の夏時間の間。配布 CSV 2023-03 と比べる。
PHASE_OFFSET_EU_DST: tuple[tuple[str, Window], ...] = (
    (USDJPY, _two_hours("2023-03-28-eu-dst", "2023-03-28T00:00")),
)
#: 8 通貨の取得できる範囲の小さな確認（人間の第 9 回の指示）。
#: 要約だけを保存し、価格の行は保存しない。
RANGE8_PAIRS: tuple[str, ...] = (USDJPY, *OTHER_PAIRS)
RANGE8_WINDOWS: tuple[Window, ...] = (
    _two_hours("2017-01-10", "2017-01-10T00:00"),
    _two_hours("2019-03-05", "2019-03-05T00:00"),
    _two_hours("2023-12-05", "2023-12-05T00:00"),
)
PHASE_RANGE8: tuple[tuple[str, Window], ...] = tuple(
    (pair, w) for pair in RANGE8_PAIRS for w in RANGE8_WINDOWS
)
PHASES: dict[str, tuple[tuple[str, Window], ...]] = {
    "usdjpy-time": PHASE_USDJPY_TIME,
    "usdjpy-range": PHASE_USDJPY_RANGE,
    "others": PHASE_OTHERS,
    "offset-eu-dst": PHASE_OFFSET_EU_DST,
    "range-8": PHASE_RANGE8,
}
#: 1 分足だけを 1 通貨ずつ、新しい年から要求する段（人間の第 10 回の決定。tick は要求しない）。
#: USDJPY は 2019-03・2023-03 を確かめ済みなので 2017-01 だけ。
M1_WINDOWS_NEW_TO_OLD: tuple[Window, ...] = (
    _two_hours("2023-12-05", "2023-12-05T00:00"),
    _two_hours("2019-03-05", "2019-03-05T00:00"),
    _two_hours("2017-01-10", "2017-01-10T00:00"),
)
M1_ORDER: tuple[str, ...] = (
    USDJPY,
    "EURUSD",
    "EURJPY",
    "GBPJPY",
    "USDCHF",
    "AUDJPY",
    "GBPUSD",
    "AUDUSD",
)
for _pair in M1_ORDER:
    _windows = M1_WINDOWS_NEW_TO_OLD[2:] if _pair == USDJPY else M1_WINDOWS_NEW_TO_OLD
    PHASES[f"m1-{_pair}"] = tuple((_pair, w) for w in _windows)
#: 返った行（価格）を保存せず、要約だけを書く段。
SUMMARY_ONLY_PHASES: frozenset[str] = frozenset({"range-8", *(f"m1-{pair}" for pair in M1_ORDER)})
#: 1 分足だけを要求する段。
M1_ONLY_PHASES: frozenset[str] = frozenset(f"m1-{pair}" for pair in M1_ORDER)
#: ディスクの空きがこれを切ったら、次の要求を出さずに止まる（人間の第 10 回の決定。60 GB が上限）。
MIN_FREE_BYTES = 3 * 1024**3


class Mt5Api(Protocol):
    """この道具が使う ``MetaTrader5`` モジュールの部分（型のため。注文の関数は含めない）。"""

    COPY_TICKS_ALL: int
    TIMEFRAME_M1: int

    def initialize(self) -> bool: ...
    def version(self) -> Any: ...
    def terminal_info(self) -> Any: ...
    def symbols_get(self, group: str) -> Any: ...
    def symbol_info(self, symbol: str) -> Any: ...
    def symbol_select(self, symbol: str, enable: bool) -> bool: ...
    def copy_ticks_range(
        self, symbol: str, date_from: datetime, date_to: datetime, flags: int
    ) -> Any: ...
    def copy_rates_range(
        self, symbol: str, timeframe: int, date_from: datetime, date_to: datetime
    ) -> Any: ...
    def last_error(self) -> Any: ...
    def shutdown(self) -> Any: ...


class RequestOutOfRangeError(ValueError):
    """研究期間の外（2024-01-01T00:00Z 以降）を含む要求。"""


def check_window(window: Window) -> None:
    """要求の区間が UTC で、終わりが上限より前であることを確かめる。"""
    if window.start.tzinfo is None or window.end.tzinfo is None:
        raise ValueError(f"区間は UTC の時刻で作る: {window.label}")
    if not window.start < window.end:
        raise ValueError(f"区間の始めが終わりより前でない: {window.label}")
    if window.end > REQUEST_END_LIMIT:
        raise RequestOutOfRangeError(f"2024-01-01T00:00Z 以降を含む要求は出さない: {window.label}")


def rows_of(result: Any) -> list[dict[str, Any]]:
    """API の戻り値（numpy の構造化配列か None）を辞書の列にする。"""
    if result is None:
        return []
    names = getattr(getattr(result, "dtype", None), "names", None)
    if names:
        return [
            {n: row[n].item() if hasattr(row[n], "item") else row[n] for n in names}
            for row in result
        ]
    return [dict(r) for r in result]


def resolve_symbol(api: Mt5Api, pair: str) -> tuple[str | None, list[str]]:
    """端末での銘柄名を探す（接尾辞が付く場合がある）。一意に決まらなければ None。"""
    found = api.symbols_get(group=f"*{pair}*") or ()
    names = sorted(str(getattr(s, "name", s)) for s in found)
    exact = [n for n in names if n == pair]
    if exact:
        return exact[0], names
    prefixed = [n for n in names if n.startswith(pair)]
    if len(prefixed) == 1:
        return prefixed[0], names
    return None, names


def _iso_from_epoch(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


@dataclass
class RequestRecord:
    """1 要求 1 行の記録（D12 §11.4 の様式）。価格の値は持たない。"""

    executed_at_utc: str
    pair: str
    symbol: str | None
    symbol_candidates: list[str]
    symbol_chart_mode: int | None
    symbol_point: float | None
    symbol_digits: int | None
    kind: str
    window: str
    requested_from_utc: str
    requested_to_utc: str
    returned_count: int
    out_of_window_count: int
    out_of_window_min_utc: str | None
    out_of_window_max_utc: str | None
    returned_first_raw: float | None
    returned_last_raw: float | None
    returned_first_as_utc: str | None
    returned_last_as_utc: str | None
    last_error: str
    retried: bool
    output_file: str | None
    output_sha256: str | None
    tick_history: str
    disk_free_before_bytes: int | None = None
    disk_free_after_bytes: int | None = None
    terminal_files_changed: list[list[Any]] | None = None
    skipped_low_disk: bool = False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for r in rows:
            w.writerow([r.get(k) for k in fields])


TICK_FIELDS: tuple[str, ...] = ("time", "time_msc", "bid", "ask", "last", "volume", "flags")
RATE_FIELDS: tuple[str, ...] = (
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
)


def split_by_window(
    rows: Sequence[dict[str, Any]], window: Window
) -> tuple[list[dict[str, Any]], list[float]]:
    """返った行を、区間 [start, end) の中の行と、区間の外の行の時刻（秒）に分ける。

    区間の外の行は価格を持ち出さないよう、時刻だけを返す。
    """
    lo, hi = window.start.timestamp(), window.end.timestamp()
    inside: list[dict[str, Any]] = []
    outside: list[float] = []
    for r in rows:
        t = float(r["time"])
        if lo <= t < hi:
            inside.append(r)
        else:
            outside.append(t)
    return inside, outside


def request_once(api: Mt5Api, kind: str, symbol: str, window: Window) -> list[dict[str, Any]]:
    """1 回の要求。区間を確かめてから API を呼ぶ。"""
    check_window(window)
    if kind == KIND_TICKS:
        result = api.copy_ticks_range(symbol, window.start, window.end, api.COPY_TICKS_ALL)
    elif kind == KIND_M1:
        result = api.copy_rates_range(symbol, api.TIMEFRAME_M1, window.start, window.end)
    else:
        raise ValueError(kind)
    return rows_of(result)


def symbol_meta(api: Mt5Api, symbol: str) -> tuple[int | None, float | None, int | None]:
    """銘柄の足の価格の種類（chart_mode。0 が bid、1 が last）・最小単位（point）・桁数。"""
    info = api.symbol_info(symbol)
    if info is None:
        return None, None, None
    mode = getattr(info, "chart_mode", None)
    point = getattr(info, "point", None)
    digits = getattr(info, "digits", None)
    return (
        int(mode) if mode is not None else None,
        float(point) if point is not None else None,
        int(digits) if digits is not None else None,
    )


#: 端末のデータのフォルダの、銘柄ごとの履歴のファイルの一覧。
#: 名前・大きさ・更新時刻だけで、中身は読まない。
FileListing = dict[str, tuple[int, str]]


def list_symbol_files(data_path: Path, symbol: str) -> FileListing:
    """``bases/<サーバー>/<種類>/<銘柄>/`` のファイルの名前・大きさ・更新時刻（UTC）を返す。

    種類は ``history``（足）か ``ticks``（tick）。ファイルの中身は開かない。
    """
    out: FileListing = {}
    for f in sorted(data_path.glob(f"bases/*/*/{symbol}/*")):
        if f.is_file():
            st = f.stat()
            rel = f.relative_to(data_path / "bases").as_posix()
            out[rel] = (st.st_size, _iso_from_epoch(st.st_mtime))
    return out


def files_changed(before: FileListing, after: FileListing) -> list[list[Any]]:
    """要求の前後で増えた・変わったファイル（名前、前の大きさ、後の大きさ、更新時刻）。"""
    changed: list[list[Any]] = []
    for name, (size, mtime) in after.items():
        old = before.get(name)
        if old is None or old != (size, mtime):
            changed.append([name, old[0] if old else None, size, mtime])
    return changed


def run_phase(
    api: Mt5Api,
    plan: Iterable[tuple[str, Window]],
    out_dir: Path,
    now: datetime | None = None,
    retry: bool = True,
    retry_wait_seconds: float = 5.0,
    save_rows: bool = True,
    kinds: Sequence[str] = (KIND_TICKS, KIND_M1),
    disk_free: Callable[[], int] | None = None,
    min_free_bytes: int = MIN_FREE_BYTES,
    lister: Callable[[str], FileListing] | None = None,
) -> list[RequestRecord]:
    """計画の要求を順に出し、記録を返す。

    ``save_rows`` が真なら区間の中の行を CSV に保存する。偽なら件数と時刻だけを記録する。
    """
    plan = list(plan)
    for _, window in plan:
        check_window(window)  # 1 つでも範囲外があれば、何も要求せずに止まる
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[RequestRecord] = []
    resolved: dict[str, tuple[str | None, list[str]]] = {}
    meta: dict[str, tuple[int | None, float | None, int | None]] = {}
    for pair, window in plan:
        if pair not in resolved:
            resolved[pair] = resolve_symbol(api, pair)
            meta[pair] = (None, None, None)
            if resolved[pair][0] is not None:
                api.symbol_select(str(resolved[pair][0]), True)
                meta[pair] = symbol_meta(api, str(resolved[pair][0]))
        symbol, candidates = resolved[pair]
        chart_mode, point, digits = meta[pair]
        tick_count = 0
        for kind in kinds:
            stamp = (now or datetime.now(tz=UTC)).isoformat()
            rows: list[dict[str, Any]] = []
            outside: list[float] = []
            retried = False
            free_before = disk_free() if disk_free else None
            if free_before is not None and free_before < min_free_bytes:
                # ディスクの空きが足りない。この要求も、この後の要求も出さない。
                records.append(_skipped(stamp, pair, symbol, candidates, kind, window, free_before))
                return records
            files_before = lister(symbol) if (lister and symbol) else None
            if symbol is not None:
                rows, outside = split_by_window(request_once(api, kind, symbol, window), window)
                if not rows and retry:
                    retried = True
                    time.sleep(retry_wait_seconds)  # 端末が履歴を取り寄せる時間を置く
                    rows, outside = split_by_window(request_once(api, kind, symbol, window), window)
            error = str(api.last_error())
            free_after = disk_free() if disk_free else None
            changed = (
                files_changed(files_before, lister(symbol))
                if (lister and symbol and files_before is not None)
                else None
            )
            out_file: str | None = None
            digest: str | None = None
            if rows and save_rows:
                fields = TICK_FIELDS if kind == KIND_TICKS else RATE_FIELDS
                path = out_dir / f"{pair}_{window.label}_{kind}.csv"
                _write_rows(path, rows, fields)
                out_file, digest = path.name, _sha256(path)
            firsts = [float(r["time"]) for r in rows[:1]]
            lasts = [float(r["time"]) for r in rows[-1:]]
            if kind == KIND_TICKS:
                tick_count = len(rows)
                status = "ticks_returned" if rows else "no_ticks"
            else:
                status = (
                    "ticks_returned"
                    if tick_count
                    else ("m1_only_not_tick_history" if rows else "no_ticks")
                )
            records.append(
                RequestRecord(
                    executed_at_utc=stamp,
                    pair=pair,
                    symbol=symbol,
                    symbol_candidates=candidates,
                    symbol_chart_mode=chart_mode,
                    symbol_point=point,
                    symbol_digits=digits,
                    kind=kind,
                    window=window.label,
                    requested_from_utc=window.start.isoformat(),
                    requested_to_utc=window.end.isoformat(),
                    returned_count=len(rows),
                    out_of_window_count=len(outside),
                    out_of_window_min_utc=_iso_from_epoch(min(outside)) if outside else None,
                    out_of_window_max_utc=_iso_from_epoch(max(outside)) if outside else None,
                    returned_first_raw=firsts[0] if firsts else None,
                    returned_last_raw=lasts[0] if lasts else None,
                    returned_first_as_utc=_iso_from_epoch(firsts[0]) if firsts else None,
                    returned_last_as_utc=_iso_from_epoch(lasts[0]) if lasts else None,
                    last_error=error,
                    retried=retried,
                    output_file=out_file,
                    output_sha256=digest,
                    tick_history=status,
                    disk_free_before_bytes=free_before,
                    disk_free_after_bytes=free_after,
                    terminal_files_changed=changed,
                )
            )
    return records


def _skipped(
    stamp: str,
    pair: str,
    symbol: str | None,
    candidates: list[str],
    kind: str,
    window: Window,
    free: int,
) -> RequestRecord:
    return RequestRecord(
        executed_at_utc=stamp,
        pair=pair,
        symbol=symbol,
        symbol_candidates=candidates,
        symbol_chart_mode=None,
        symbol_point=None,
        symbol_digits=None,
        kind=kind,
        window=window.label,
        requested_from_utc=window.start.isoformat(),
        requested_to_utc=window.end.isoformat(),
        returned_count=0,
        out_of_window_count=0,
        out_of_window_min_utc=None,
        out_of_window_max_utc=None,
        returned_first_raw=None,
        returned_last_raw=None,
        returned_first_as_utc=None,
        returned_last_as_utc=None,
        last_error="not requested: low disk",
        retried=False,
        output_file=None,
        output_sha256=None,
        tick_history="not_requested",
        disk_free_before_bytes=free,
        skipped_low_disk=True,
    )


def terminal_summary(api: Mt5Api) -> dict[str, Any]:
    """端末の版と「Max. bars in chart」だけを記録する（口座の情報は読まない）。"""
    info = api.terminal_info()
    keep = ("build", "name", "company", "maxbars", "connected")
    return {
        "version": str(api.version()),
        "terminal": {k: getattr(info, k, None) for k in keep} if info is not None else None,
    }


def load_mt5() -> Mt5Api:
    """Windows 上の ``MetaTrader5`` パッケージを読む（Mac には無い）。"""
    module: Any = importlib.import_module("MetaTrader5")
    return module  # type: ignore[no-any-return]


def server_time_to_utc(date_text: str, time_text: str) -> datetime:
    """配布 CSV の時刻（MT5 のサーバー時刻 = ニューヨークの現地時刻 + 7 時間）を UTC にする。"""
    naive = datetime.strptime(f"{date_text} {time_text}", "%Y.%m.%d %H:%M:%S.%f")
    # Windows の Python には時刻帯のデータが無い場合があるので、compare（Mac）でだけ読む。
    ny_local = (naive - SERVER_MINUS_NY_LOCAL).replace(tzinfo=ZoneInfo(NEW_YORK_TZ_NAME))
    return ny_local.astimezone(UTC)


def _price_key(value: Any) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def read_oanda_csv(path: Path, start: datetime, end: datetime) -> Iterator[tuple[int, str, str]]:
    """配布 CSV から [start, end) の tick を (UTC のミリ秒, bid, ask) で返す。

    時刻は UTC に直してから選ぶ。
    """
    if end > REQUEST_END_LIMIT:
        raise RequestOutOfRangeError("2024-01-01T00:00Z 以降は読まない")
    with path.open(encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)
        for row in reader:
            if len(row) < 4:
                continue
            t = server_time_to_utc(row[0], row[1])
            if start <= t < end:
                yield round(t.timestamp() * 1000), _price_key(row[2]), _price_key(row[3])


def read_probe_ticks(path: Path) -> list[tuple[int, str, str]]:
    with path.open(encoding="utf-8") as f:
        return [
            (int(r["time_msc"]), _price_key(r["bid"]), _price_key(r["ask"]))
            for r in csv.DictReader(f)
        ]


def compare_ticks(
    api_ticks: Sequence[tuple[int, str, str]],
    csv_ticks: Sequence[tuple[int, str, str]],
    shifts_hours: Sequence[int] = CANDIDATE_SHIFTS_HOURS,
) -> list[dict[str, int]]:
    """API の時刻にずれを足したとき、(時刻, bid, ask) が配布 CSV と一致する件数。

    価格の値は出さない。
    """
    csv_counter = Counter(csv_ticks)
    out: list[dict[str, int]] = []
    for h in shifts_hours:
        shifted = Counter((t + h * 3_600_000, b, a) for t, b, a in api_ticks)
        matched = sum(min(n, csv_counter[k]) for k, n in shifted.items())
        out.append(
            {
                "shift_hours": h,
                "api_ticks": len(api_ticks),
                "csv_ticks": len(csv_ticks),
                "matched": matched,
            }
        )
    return out


# --- 時刻の調査（D12 §11.7。人間の決定 D12-34・D12-35） -----------------------------------------
#
# 1 分足だけを要求する。週末の区間は API の時刻の値で金曜 18:00〜月曜 02:00、
# 火曜の区間は 00:00〜02:00。保存するのは足の時刻の並び・件数と、
# 火曜の区間の配布 CSV との照合の統計（E1）だけ。
# 価格の値はメモリの中で比べるだけで、ファイルにも標準出力にも出さない。

#: 時刻の調査の要求の上限（D12 §11.7.3。段の 67 と決定木の追加 20）。
TIME_SURVEY_BASE_REQUESTS = 67
TIME_SURVEY_EXTRA_REQUESTS = 20
#: E1 の照合で試すずれ（時間。API の時刻 − UTC の候補を引く向き）。
E1_SHIFTS_HOURS: tuple[int, ...] = (-4, -3, -2, -1, 0, 1, 2, 3, 4)
#: 区間の中の足が 0 本のときの再試行の待ち時間（秒）。新しい端末は最初の要求で履歴を取り寄せ始め、
#: 取り寄せが終わるまで区間の外の 1 本だけを返す（2026-10-16 の段 A2 の 1 回目。付録 K-9）。
SURVEY_RETRY_WAITS: tuple[float, ...] = (10.0, 30.0, 60.0, 120.0)
STATES: tuple[str, ...] = ("S1", "S2", "S3", "S4")


@dataclass(frozen=True)
class SurveyRequest:
    """時刻の調査の 1 要求。

    ``kind`` は ``weekend``（``date`` は日曜）か ``tuesday``（``date`` は火曜）。
    """

    rid: str
    stage: str
    pair: str
    kind: str
    date: str
    state: str

    def window(self) -> Window:
        d = datetime.fromisoformat(self.date).replace(tzinfo=UTC)
        if self.kind == "weekend":
            if d.weekday() != 6:
                raise ValueError(f"週末の区間の日付は日曜: {self.rid}")
            return Window(
                self.rid,
                d - timedelta(days=2) + timedelta(hours=18),
                d + timedelta(days=1, hours=2),
            )
        if self.kind == "gapweek":
            # 空白の測定の代表週（D12 §11.9.2）。API の時刻の値で日曜 00:00〜土曜 03:00
            # （ずれ +2〜+3 時間でも日曜の開場の前から金曜の閉場の後までを含む）。
            if d.weekday() != 6:
                raise ValueError(f"代表週の日付は日曜: {self.rid}")
            return Window(self.rid, d, d + timedelta(days=6, hours=3))
        if self.kind == "tuesday":
            if d.weekday() != 1:
                raise ValueError(f"火曜の区間の日付は火曜: {self.rid}")
            return Window(self.rid, d, d + timedelta(hours=2))
        raise ValueError(self.kind)


_YEAR_SUNDAYS: dict[int, tuple[str, str, str, str]] = {
    # 年: (S1 両方冬, S2 米国だけ夏・春, S3 両方夏, S4 米国だけ夏・秋) の日曜（D12 §11.7.2）
    2017: ("2017-01-15", "2017-03-12", "2017-07-16", "2017-10-29"),
    2018: ("2018-01-14", "2018-03-11", "2018-07-15", "2018-10-28"),
    2019: ("2019-01-13", "2019-03-10", "2019-07-14", "2019-10-27"),
    2020: ("2020-01-12", "2020-03-08", "2020-07-12", "2020-10-25"),
    2021: ("2021-01-17", "2021-03-14", "2021-07-11", "2021-10-31"),
    2022: ("2022-01-16", "2022-03-13", "2022-07-17", "2022-10-30"),
    2023: ("2023-01-15", "2023-03-12", "2023-07-16", "2023-10-29"),
}


def _weekends(
    stage: str, pair: str, years: Sequence[int], states: Sequence[str]
) -> list[SurveyRequest]:
    out = []
    for y in years:
        for s in states:
            date = _YEAR_SUNDAYS[y][STATES.index(s)]
            out.append(SurveyRequest(f"{stage}-{pair}-W{date}", stage, pair, "weekend", date, s))
    return out


def _tuesdays(stage: str, pair: str, items: Sequence[tuple[str, str]]) -> list[SurveyRequest]:
    return [SurveyRequest(f"{stage}-{pair}-T{d}", stage, pair, "tuesday", d, s) for d, s in items]


#: D12 §11.7.2 の段（新しい年から）。A2 10・A1 23・B1 28・B2 6 の計 67。
TIME_SURVEY_STAGES: dict[str, tuple[SurveyRequest, ...]] = {
    "A2": tuple(
        _weekends("A2", USDJPY, (2023, 2019), STATES)
        + _tuesdays("A2", USDJPY, (("2023-03-07", "S1"), ("2023-03-14", "S2")))
    ),
    "A1": tuple(
        _weekends("A1", USDJPY, (2022, 2021, 2020, 2018, 2017), STATES)
        + _tuesdays(
            "A1", USDJPY, (("2017-03-07", "S1"), ("2017-03-14", "S2"), ("2017-03-28", "S3"))
        )
    ),
    "B1": tuple(
        r for pair in OTHER_PAIRS for r in _weekends("B1", pair, (2023, 2019), ("S2", "S3"))
    ),
    "B2": tuple(
        r
        for pair in ("EURJPY", "EURUSD", "GBPJPY")
        for r in _tuesdays("B2", pair, (("2019-03-05", "S1"), ("2019-03-19", "S2")))
    ),
}


#: 空白の測定（D12 §11.9.2。人間の決定 D12-38・D12-39）の代表週の日曜。
#: W1 は 6 月の第 1 金曜を含む週、
#: W2 は日曜が 12 月 22〜28 日にある週。8 通貨 × 7 年 × 2 週 = 112 要求。保存するのは足の時刻だけ。
GAP_WEEK_SUNDAYS: dict[int, tuple[str, str]] = {
    2017: ("2017-05-28", "2017-12-24"),
    2018: ("2018-05-27", "2018-12-23"),
    2019: ("2019-06-02", "2019-12-22"),
    2020: ("2020-05-31", "2020-12-27"),
    2021: ("2021-05-30", "2021-12-26"),
    2022: ("2022-05-29", "2022-12-25"),
    2023: ("2023-05-28", "2023-12-24"),
}
GAP_PAIRS: tuple[str, ...] = (USDJPY, *OTHER_PAIRS)
GAP_STAGE: tuple[SurveyRequest, ...] = tuple(
    SurveyRequest(f"G-{pair}-{wk}-{d}", "G", pair, "gapweek", d, wk)
    for pair in GAP_PAIRS
    for year in sorted(GAP_WEEK_SUNDAYS, reverse=True)
    for wk, d in zip(("W1", "W2"), GAP_WEEK_SUNDAYS[year], strict=True)
)
GAP_REQUESTS = 112


#: 最後の調査（D12 付録 K-10。人間の決定 D12-41 と第 12 回の決定）の要求の一覧。USDJPY だけ、
#: 1 分足だけ、2017・2019 年だけ。状態 S2M は「米欧の間の週末」（金曜・日曜とも米国だけ夏時間）、
#: S2E は「米欧の間で、日曜が欧州の切替日の週末」（金曜は米国だけ夏時間、日曜の開場は両方夏時間）。
FINAL_STAGE_NAME = "K10"
FINAL_STAGE: tuple[SurveyRequest, ...] = (
    SurveyRequest("K10-USDJPY-W2017-03-05", "K10", USDJPY, "weekend", "2017-03-05", "S1"),
    SurveyRequest("K10-USDJPY-W2019-03-03", "K10", USDJPY, "weekend", "2019-03-03", "S1"),
    SurveyRequest("K10-USDJPY-W2017-03-19", "K10", USDJPY, "weekend", "2017-03-19", "S2M"),
    SurveyRequest("K10-USDJPY-W2017-03-26", "K10", USDJPY, "weekend", "2017-03-26", "S2E"),
    SurveyRequest("K10-USDJPY-W2019-03-17", "K10", USDJPY, "weekend", "2019-03-17", "S2M"),
    SurveyRequest("K10-USDJPY-W2019-03-24", "K10", USDJPY, "weekend", "2019-03-24", "S2M"),
    SurveyRequest("K10-USDJPY-W2019-03-31", "K10", USDJPY, "weekend", "2019-03-31", "S2E"),
    SurveyRequest("K10-USDJPY-T2017-03-21", "K10", USDJPY, "tuesday", "2017-03-21", "S2"),
    SurveyRequest("K10-USDJPY-T2017-03-14-re", "K10", USDJPY, "tuesday", "2017-03-14", "S2"),
)
#: 最後の調査の要求の数の上限（計画 9。残り 3 は取り寄せの空振りのやり直しだけ。付録 K-10.2）。
FINAL_MAX_REQUESTS = 12
#: 最後の調査で要求してよい年（付録 K-10.2。2024 年以降は REQUEST_END_LIMIT でも止める）。
FINAL_YEARS: frozenset[int] = frozenset({2017, 2019})


class FinalLimitError(ValueError):
    """最後の調査の上限（要求 12・年・一覧の外の区間）を超える指定。"""


def final_plan(
    only: Sequence[str] | None, already_sent: int, max_requests: int
) -> list[SurveyRequest]:
    """最後の調査の計画を作り、上限を確かめる。``only`` は一覧の中の要求の名前だけ（やり直し用）。

    ``already_sent`` は前の回までに出した要求の数（同じ区間のやり直しも数える）。上限を超える
    ``max_requests`` や、一覧の外の名前は拒む。
    """
    if max_requests > FINAL_MAX_REQUESTS - already_sent:
        raise FinalLimitError(
            f"最後の調査の要求は合計 {FINAL_MAX_REQUESTS} まで（済み {already_sent}、"
            f"指定 {max_requests}）"
        )
    known = {r.rid: r for r in FINAL_STAGE}
    if only:
        unknown = [rid for rid in only if rid not in known]
        if unknown:
            raise FinalLimitError(f"最後の調査の一覧に無い要求: {unknown}")
        plan = [known[rid] for rid in only]
    else:
        plan = list(FINAL_STAGE)
    for r in plan:
        w = r.window()
        check_window(w)
        if w.start.year not in FINAL_YEARS or w.end.year not in FINAL_YEARS:
            raise FinalLimitError(f"最後の調査は 2017・2019 年だけ: {r.rid}")
    return plan


def prior_final_requests(out_root: Path) -> int:
    """前の回までに出した最後の調査の要求の数（``<out_root>/ts-K10-*/summary_ts-K10.json``）。"""
    n = 0
    for p in sorted(out_root.glob(f"ts-{FINAL_STAGE_NAME}-*/summary_ts-{FINAL_STAGE_NAME}.json")):
        n += sum(
            1 for r in json.loads(p.read_text(encoding="utf-8"))["requests"] if not r["skipped"]
        )
    return n


def load_plan_file(path: Path) -> list[SurveyRequest]:
    """決定木の追加の要求（JSON の配列）を読む。区間を確かめる。"""
    items = json.loads(path.read_text(encoding="utf-8"))
    reqs = [SurveyRequest(**item) for item in items]
    for r in reqs:
        check_window(r.window())
    return reqs


def _bar_key(row: Mapping[str, Any], digits: int) -> tuple[str, str, str, str]:
    return tuple(f"{float(row[k]):.{digits}f}" for k in ("open", "high", "low", "close"))  # type: ignore[return-value]


def e1_stats(
    api_rows: Sequence[Mapping[str, Any]],
    ref_bars: Mapping[int, Sequence[str]],
    digits: int,
    point: float,
    ref_digits: int | None = None,
) -> list[dict[str, Any]]:
    """API の 1 分足と、配布 CSV で作った 1 分足（UTC の分の始め → 4 値の文字列）を、
    ずれの候補ごとに比べた統計。価格の値は返さない。

    ``ref_digits`` は参照の足の桁数（mid は bid と ask の平均なので 1 桁多い）。省くと ``digits``。
    符号付きの差（API − 参照）の中央値も返す（補助。point 単位）。
    """
    out: list[dict[str, Any]] = []
    rd = digits if ref_digits is None else ref_digits
    api_bars = {int(r["time"]): _bar_key(r, digits) for r in api_rows}
    ref = {int(t): tuple(f"{float(v):.{rd}f}" for v in vals) for t, vals in ref_bars.items()}
    for h in E1_SHIFTS_HOURS:
        pairs = [(a, ref[t - h * 3600]) for t, a in api_bars.items() if (t - h * 3600) in ref]
        exact = sum(
            all(abs(float(x) - float(y)) < point / 2 for x, y in zip(a, b, strict=True))
            for a, b in pairs
        )
        signed = sorted((float(a[3]) - float(b[3])) / point for a, b in pairs)
        diffs = sorted(abs(d) for d in signed)
        med = diffs[len(diffs) // 2] if diffs else None
        smed = signed[len(signed) // 2] if signed else None
        out.append(
            {
                "shift_hours": h,
                "compared": len(pairs),
                "ohlc_exact": exact,
                "median_abs_close_diff_points": round(med, 1) if med is not None else None,
                "median_signed_close_diff_points": round(smed, 1) if smed is not None else None,
            }
        )
    return out


#: 照合に使う配布 CSV の価格の種類（人間の第 11 回の決定の 3。ask・mid は補助）。
REF_BASES: tuple[str, ...] = ("bid", "ask", "mid")
#: 参照（配布 CSV の 1 分足）。旧の形は bid だけ（UTC の分の始め → 4 値）、新しい形は種類ごと。
RefBars = Mapping[int, Sequence[str]]


def load_ref(raw: Mapping[str, Any]) -> dict[str, dict[str, dict[int, list[str]]]]:
    """参照のファイル（JSON）を、要求 → 価格の種類 → 足 の形に読む。旧の形（bid だけ）も読む。"""
    out: dict[str, dict[str, dict[int, list[str]]]] = {}
    for rid, bars in raw.items():
        if isinstance(bars, Mapping) and set(bars) <= set(REF_BASES) and bars:
            out[rid] = {b: {int(t): list(v) for t, v in bb.items()} for b, bb in bars.items()}
        else:
            out[rid] = {"bid": {int(t): list(v) for t, v in bars.items()}}
    return out


def aux_stats(
    rows: Sequence[Mapping[str, Any]],
    ref: Mapping[str, RefBars],
    digits: int,
    point: float,
    split_at: float | None = None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """bid・ask・mid の照合の統計（補助）。価格はメモリの中だけで比べ、差の統計だけを返す。

    ``split_at``（API の時刻の値、秒）があれば、週末の区間を金曜の側（``close_side``）と
    日曜・月曜の側（``open_side``）に分けて比べる（閉場と開場で期待されるずれが違う状態があるため）。
    無ければ区間の全体（``all``）。
    """
    if split_at is None:
        parts: dict[str, Sequence[Mapping[str, Any]]] = {"all": rows}
    else:
        parts = {
            "close_side": [r for r in rows if float(r["time"]) < split_at],
            "open_side": [r for r in rows if float(r["time"]) >= split_at],
        }
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for side, part in parts.items():
        out[side] = {
            basis: e1_stats(part, bars, digits, point, digits + 1 if basis == "mid" else digits)
            for basis, bars in ref.items()
        }
    return out


@dataclass
class SurveyRecord:
    """時刻の調査の 1 要求の記録（価格の値は持たない）。"""

    rid: str
    stage: str
    pair: str
    kind: str
    date: str
    state: str
    executed_at_utc: str
    symbol: str | None
    symbol_chart_mode: int | None
    symbol_point: float | None
    symbol_digits: int | None
    requested_from_api_time: str
    requested_to_api_time: str
    returned_count: int
    out_of_window_count: int
    bar_times: list[int]
    last_error: str
    disk_free_before_bytes: int | None
    disk_free_after_bytes: int | None
    e1: list[dict[str, Any]] | None
    skipped: str | None = None
    retries: int = 0
    #: 照合の統計（補助）。区間の側 → 価格の種類 → ずれごとの差の統計。価格の値は持たない。
    e1_aux: dict[str, dict[str, list[dict[str, Any]]]] | None = None


def run_time_survey(
    api: Mt5Api,
    plan: Sequence[SurveyRequest],
    max_requests: int,
    e1_ref: Mapping[str, Mapping[Any, Any]] | None = None,
    disk_free: Callable[[], int] | None = None,
    min_free_bytes: int = MIN_FREE_BYTES,
    retry_waits: Sequence[float] = SURVEY_RETRY_WAITS,
) -> list[SurveyRecord]:
    """時刻の調査の要求を順に出す。上限（要求の数・ディスクの空き）に達したら、残りは出さずに記録する。

    ``e1_ref`` は要求 → 足（旧の形。bid だけ）か、要求 → 価格の種類 → 足（``load_ref`` の形）。
    火曜の区間の bid は従来どおり ``e1`` に、種類ごとの統計は ``e1_aux`` に書く。
    週末の区間は日曜 00:00（API の時刻の値）で金曜の側と月曜の側に分けて ``e1_aux`` に書く
    （``e1`` は書かない）。
    """
    windows = [r.window() for r in plan]
    for w in windows:
        check_window(w)  # 1 つでも 2024 年以降を含めば、何も要求せずに止まる
    resolved: dict[str, tuple[str | None, tuple[int | None, float | None, int | None]]] = {}
    out: list[SurveyRecord] = []
    sent = 0
    stop: str | None = None
    for req, w in zip(plan, windows, strict=True):
        stamp = datetime.now(tz=UTC).isoformat()
        free_before = disk_free() if disk_free else None
        if stop is None and sent >= max_requests:
            stop = "request_limit"
        if stop is None and free_before is not None and free_before < min_free_bytes:
            stop = "low_disk"
        if req.pair not in resolved:
            sym = resolve_symbol(api, req.pair)[0] if stop is None else None
            meta: tuple[int | None, float | None, int | None] = (None, None, None)
            if sym is not None:
                api.symbol_select(sym, True)
                meta = symbol_meta(api, sym)
            resolved[req.pair] = (sym, meta)
        symbol, (mode, point, digits) = resolved[req.pair]
        rows: list[dict[str, Any]] = []
        outside: list[float] = []
        error = ""
        e1 = None
        aux = None
        retries = 0
        if stop is None and symbol is not None:
            rows, outside = split_by_window(request_once(api, KIND_M1, symbol, w), w)
            for wait in retry_waits:  # 同じ区間の再試行（要求の数には数えない）
                if rows:
                    break
                time.sleep(wait)
                retries += 1
                rows, outside = split_by_window(request_once(api, KIND_M1, symbol, w), w)
            sent += 1
            error = str(api.last_error())
            ref = (e1_ref or {}).get(req.rid)
            if ref is not None and digits is not None and point:
                by_basis = ref if ref and set(ref) <= set(REF_BASES) else {"bid": ref}
                if req.kind == "tuesday":
                    if "bid" in by_basis:
                        e1 = e1_stats(rows, by_basis["bid"], digits, point)
                    if len(by_basis) > 1:
                        aux = aux_stats(rows, by_basis, digits, point)
                elif req.kind == "weekend":
                    sunday = datetime.fromisoformat(req.date).replace(tzinfo=UTC).timestamp()
                    aux = aux_stats(rows, by_basis, digits, point, split_at=sunday)
        out.append(
            SurveyRecord(
                rid=req.rid,
                stage=req.stage,
                pair=req.pair,
                kind=req.kind,
                date=req.date,
                state=req.state,
                executed_at_utc=stamp,
                symbol=symbol,
                symbol_chart_mode=mode,
                symbol_point=point,
                symbol_digits=digits,
                requested_from_api_time=w.start.isoformat(),
                requested_to_api_time=w.end.isoformat(),
                returned_count=len(rows),
                out_of_window_count=len(outside),
                bar_times=[int(r["time"]) for r in rows],
                last_error=error,
                disk_free_before_bytes=free_before,
                disk_free_after_bytes=disk_free() if disk_free else None,
                e1=e1,
                skipped=stop if stop is not None else (None if symbol else "symbol_not_found"),
                retries=retries,
                e1_aux=aux,
            )
        )
    return out


#: 端末の履歴の取り寄せを起こすためだけの tick の要求（人間の決定、2026-10-16。1 回だけ）。
SYNC_TRIGGER_WINDOW = Window(
    "sync-trigger", datetime(2023, 12, 27, tzinfo=UTC), datetime(2023, 12, 27, 0, 5, tzinfo=UTC)
)


def sync_trigger(api: Mt5Api, pair: str = USDJPY) -> dict[str, Any]:
    """tick を 1 回だけ要求し、件数と最初・最後の時刻だけを返す（tick の値は保存しない）。"""
    check_window(SYNC_TRIGGER_WINDOW)
    symbol = resolve_symbol(api, pair)[0]
    if symbol is None:
        return {"symbol": None, "count": 0}
    api.symbol_select(symbol, True)
    rows = request_once(api, KIND_TICKS, symbol, SYNC_TRIGGER_WINDOW)
    times = [int(r["time"]) for r in rows]
    return {
        "symbol": symbol,
        "requested_from": SYNC_TRIGGER_WINDOW.start.isoformat(),
        "requested_to": SYNC_TRIGGER_WINDOW.end.isoformat(),
        "count": len(rows),
        "first_as_utc": _iso_from_epoch(min(times)) if times else None,
        "last_as_utc": _iso_from_epoch(max(times)) if times else None,
        "last_error": str(api.last_error()),
        "executed_at_utc": datetime.now(tz=UTC).isoformat(),
    }


def _cmd_synctrigger(args: argparse.Namespace) -> int:
    api = load_mt5()
    if not api.initialize():
        print(json.dumps({"initialize": False, "last_error": str(api.last_error())}))
        return 2
    try:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result = {**sync_trigger(api), **terminal_summary(api)}
        path = out_dir / "summary_sync-trigger.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(path)
    finally:
        api.shutdown()
    return 0


def _cmd_timesurvey(args: argparse.Namespace) -> int:
    if args.stage == FINAL_STAGE_NAME:
        if args.plan_file:
            raise FinalLimitError(
                "最後の調査には一覧の外の区間を足さない（--plan-file は使えない）"
            )
        only = [x for x in (args.only or "").split(",") if x]
        already = prior_final_requests(Path(args.out_dir).parent)
        plan = final_plan(only, already, args.max_requests)
    elif args.only:
        raise ValueError("--only は最後の調査（K10）だけ")
    elif args.stage == "G":
        plan = list(GAP_STAGE)
    else:
        plan = list(TIME_SURVEY_STAGES[args.stage]) if args.stage != "extra" else []
    if args.plan_file:
        plan += load_plan_file(Path(args.plan_file))
    e1_ref: dict[str, dict[str, dict[int, list[str]]]] | None = None
    if args.e1_ref:
        raw = json.loads(gzip.decompress(Path(args.e1_ref).read_bytes()).decode("utf-8"))
        e1_ref = load_ref(raw)
    api = load_mt5()
    if not api.initialize():
        print(json.dumps({"initialize": False, "last_error": str(api.last_error())}))
        return 2
    try:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        info = api.terminal_info()
        data_path = Path(str(getattr(info, "data_path", "") or out_dir))
        records = run_time_survey(
            api,
            plan,
            max_requests=args.max_requests,
            e1_ref=e1_ref,
            disk_free=lambda: shutil.disk_usage(data_path).free,
        )
        summary = {"stage": args.stage, **terminal_summary(api)}
        summary["requests"] = [asdict(r) for r in records]
        path = out_dir / f"summary_ts-{args.stage}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(path)
    finally:
        api.shutdown()
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    api = load_mt5()
    if not api.initialize():
        print(json.dumps({"initialize": False, "last_error": str(api.last_error())}))
        return 2
    try:
        out_dir = Path(args.out_dir)
        info = api.terminal_info()
        data_path = Path(str(getattr(info, "data_path", "") or out_dir))
        records = run_phase(
            api,
            PHASES[args.phase],
            out_dir,
            save_rows=args.phase not in SUMMARY_ONLY_PHASES,
            kinds=(KIND_M1,) if args.phase in M1_ONLY_PHASES else (KIND_TICKS, KIND_M1),
            disk_free=lambda: shutil.disk_usage(data_path).free,
            lister=lambda symbol: list_symbol_files(data_path, symbol),
        )
        summary = {"phase": args.phase, **terminal_summary(api)}
        summary["requests"] = [asdict(r) for r in records]
        path = out_dir / f"summary_{args.phase}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)
    finally:
        api.shutdown()
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    probe_dir = Path(args.probe_dir)
    result: list[dict[str, Any]] = []
    for _, window in PHASE_USDJPY_TIME:
        path = probe_dir / f"{USDJPY}_{window.label}_{KIND_TICKS}.csv"
        api_ticks = read_probe_ticks(path) if path.exists() else []
        # API の時刻の関係は分からないので、CSV は前後 4 時間を広めに読む（ずれの候補を覆う）。
        wide = Window(
            window.label, window.start - timedelta(hours=4), window.end + timedelta(hours=4)
        )
        csv_ticks = list(read_oanda_csv(Path(args.oanda_csv), wide.start, wide.end))
        result.append({"window": window.label, "by_shift": compare_ticks(api_ticks, csv_ticks)})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# --- 全期間取得（付録 K-16。人間の第 18 回の決定） -----------------------------------------------
# 8 通貨 × 2017-01〜2023-12 の 1 分足を、1 要求 = 1 通貨 × 1 か月（API の時刻の値で
# [月初 00:00, 翌月初 00:00)）で取り、通貨 × 月ごとに gzip の CSV で保存する。
# 時刻は API の生の値（秒）のまま保存し、変換しない（D12-47）。価格は bid の足（chart_mode 0）。
# 価格を保存するのは研究期間 2017〜2023 年だけ（``FULL_FIRST_START``・``REQUEST_END_LIMIT``）。
# 保存・継続利用の条件は未確認（D12-19 (b)）。個人の研究利用で、再配布しない（manifest に記録）。

FULL_STAGE_NAME = "fullfetch"
#: 取得する通貨（付録 K-16.1 の順）。端末の銘柄名は ``<通貨>.cl``（``resolve_symbol`` で探す）。
FULL_PAIRS: tuple[str, ...] = (USDJPY, *OTHER_PAIRS)
#: 研究期間の最初（API の時刻の値）。これより前を含む要求は出さない。
FULL_FIRST_START = datetime(2017, 1, 1, tzinfo=UTC)
FULL_FIRST_MONTH = (2017, 1)
FULL_LAST_MONTH = (2023, 12)
#: 計画の要求の数（8 × 84）と上限（残り 78 は 0 本の月のやり直しだけ。付録 K-16.2）。
FULL_PLANNED_REQUESTS = 672
FULL_MAX_REQUESTS = 750
FULL_MANIFEST = "manifest.json"
FULL_FETCH_LOG = "fetch_log.jsonl"
FULL_RESULT_PREFIX = "FULLFETCH_RESULT "
FULL_STORAGE_TERMS = (
    "保存・継続利用・解約後の扱いの条件は未確認（D12-19 (b)）。人間の第 18 回の決定により、"
    "個人の研究利用として保存する。再配布しない。"
)
STATUS_OK = "ok"
STATUS_EMPTY = "empty"


class FullFetchLimitError(ValueError):
    """全期間取得の上限（要求 750・研究期間・一覧の外の通貨）を超える指定。"""


@dataclass(frozen=True)
class MonthRequest:
    """全期間取得の 1 要求 = 1 通貨 × 1 か月。"""

    pair: str
    year: int
    month: int

    @property
    def rid(self) -> str:
        return f"F-{self.pair}-{self.year:04d}-{self.month:02d}"

    @property
    def file(self) -> str:
        """保存のファイルの、出力の置き場からの相対の名前（``<通貨>/<通貨>_<YYYY-MM>_m1.csv.gz``）。"""
        return f"{self.pair}/{self.pair}_{self.year:04d}-{self.month:02d}_m1.csv.gz"

    def window(self) -> Window:
        start = datetime(self.year, self.month, 1, tzinfo=UTC)
        ny, nm = (self.year + 1, 1) if self.month == 12 else (self.year, self.month + 1)
        return Window(self.rid, start, datetime(ny, nm, 1, tzinfo=UTC))


def check_full_window(window: Window) -> None:
    """全期間取得の区間が研究期間 2017-01-01〜2024-01-01（API の時刻の値）の中かを確かめる。"""
    check_window(window)
    if window.start < FULL_FIRST_START:
        raise RequestOutOfRangeError(f"2017-01-01 より前を含む要求は出さない: {window.label}")


def full_plan(pairs: Sequence[str] = FULL_PAIRS) -> list[MonthRequest]:
    """通貨の順（``pairs``）、各通貨の中は新しい月から（付録 K-16.2）。一覧の外の通貨は拒む。"""
    unknown = [p for p in pairs if p not in FULL_PAIRS]
    if unknown:
        raise FullFetchLimitError(f"全期間取得の一覧に無い通貨: {unknown}")
    months: list[tuple[int, int]] = []
    y, m = FULL_FIRST_MONTH
    while (y, m) <= FULL_LAST_MONTH:
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    plan = [MonthRequest(p, yy, mm) for p in pairs for yy, mm in reversed(months)]
    for r in plan:
        check_full_window(r.window())
    return plan


def month_csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """1 か月の足の CSV（見出し行あり、列は ``RATE_FIELDS``）。時刻は API の生の値（整数の秒）。

    価格は Python の float の最短の表記で、丸めない。
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(RATE_FIELDS)
    for r in rows:
        w.writerow([int(r["time"]), *(r.get(k) for k in RATE_FIELDS[1:])])
    return buf.getvalue().encode("utf-8")


def gzip_bytes(data: bytes) -> bytes:
    """同じ中身なら同じ SHA-256 になる gzip（ファイル名・時刻を入れない）。"""
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as g:
        g.write(data)
    return out.getvalue()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def load_full_manifest(out_dir: Path) -> dict[str, Any]:
    """出力の置き場の manifest を読む（無ければ空の形）。"""
    path = out_dir / FULL_MANIFEST
    if path.exists():
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    return {
        "kind": "oanda_mt5_full_fetch",
        "schema": 1,
        "stage": FULL_STAGE_NAME,
        "time_basis": "API の生の時刻（秒）。変換しない（D12-47）",
        "price_basis": "bid の足（chart_mode 0）",
        "columns": list(RATE_FIELDS),
        "request_first_start": FULL_FIRST_START.isoformat(),
        "request_end_limit": REQUEST_END_LIMIT.isoformat(),
        "planned_requests": FULL_PLANNED_REQUESTS,
        "max_requests": FULL_MAX_REQUESTS,
        "requests_sent": 0,
        "storage_terms": FULL_STORAGE_TERMS,
        "symbols": {},
        "files": {},
        "runs": [],
    }


def _entry_is_done(out_dir: Path, entry: Mapping[str, Any] | None) -> bool:
    """取得済み（状態 ok で、ファイルがあり gzip の SHA-256 が合う）か。"""
    if not entry or entry.get("status") != STATUS_OK:
        return False
    path = out_dir / str(entry["file"])
    return path.exists() and _sha256(path) == entry["sha256_gz"]


def run_full_fetch(
    api: Mt5Api,
    pair: str,
    out_dir: Path,
    *,
    redo_empty: bool = False,
    max_total: int = FULL_MAX_REQUESTS,
    stop_at_epoch: float | None = None,
    disk_free: Callable[[], int] | None = None,
    min_free_bytes: int = MIN_FREE_BYTES,
    retry_waits: Sequence[float] = SURVEY_RETRY_WAITS,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    tool_sha256: str | None = None,
) -> dict[str, Any]:
    """1 通貨の全期間取得（新しい月から）。上限・打ち切り・空きに達したら、次の要求を出さない。

    - 取得済みの月（manifest の状態 ok で SHA-256 が合う）は要求しない。
    - 区間の中の足が 0 本なら ``retry_waits`` の秒数だけ待って出し直す（要求の数に数えない）。
      それでも 0 本なら状態 ``empty`` と記録して次へ進む。ただし **この回で最初に出した月が
      0 本なら、その通貨の残りを出さずに止める**（取り寄せが始まっていない合図。付録 K-16.2）。
    - ``redo_empty`` なら状態 ``empty`` の月だけを出し直す（要求の数に数える）。
    - 要求の数は manifest の ``requests_sent`` に、前の回から通して数える（上限 ``max_total``）。
    - 価格は CSV の中だけに書き、取得の記録（``fetch_log.jsonl``）・標準出力には書かない。
    """
    if max_total > FULL_MAX_REQUESTS:
        raise FullFetchLimitError(
            f"全期間取得の要求は合計 {FULL_MAX_REQUESTS} まで（指定 {max_total}）"
        )
    plan = full_plan((pair,))  # 一覧の外の通貨・研究期間の外は、何も要求せずに止まる
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_full_manifest(out_dir)
    if tool_sha256:
        manifest["tool_sha256"] = tool_sha256
    manifest["terminal"] = terminal_summary(api)
    files: dict[str, Any] = manifest["files"]
    log_path = out_dir / FULL_FETCH_LOG
    result: dict[str, Any] = {
        "pair": pair,
        "redo_empty": redo_empty,
        "sent_this_run": 0,
        "ok": 0,
        "empty": 0,
        "skipped_done": 0,
        "stop": None,
    }

    def save() -> None:
        _atomic_write(
            out_dir / FULL_MANIFEST,
            json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"),
        )

    symbol, _candidates = resolve_symbol(api, pair)
    if symbol is None:
        result["stop"] = "symbol_not_found"
    else:
        api.symbol_select(symbol, True)
        mode, point, digits = symbol_meta(api, symbol)
        manifest["symbols"][pair] = {
            "symbol": symbol,
            "chart_mode": mode,
            "point": point,
            "digits": digits,
        }
    first_request = True
    for req in plan if result["stop"] is None else []:
        assert symbol is not None
        entry = files.get(req.rid)
        if redo_empty:
            if not entry or entry.get("status") != STATUS_EMPTY:
                continue
        elif _entry_is_done(out_dir, entry):
            result["skipped_done"] += 1
            continue
        elif entry and entry.get("status") == STATUS_EMPTY:
            continue  # 空の月は --redo-empty のときだけ出し直す
        if int(manifest["requests_sent"]) >= max_total:
            result["stop"] = "request_limit"
            break
        if stop_at_epoch is not None and clock() >= stop_at_epoch:
            result["stop"] = "time_limit"
            break
        free = disk_free() if disk_free else None
        if free is not None and free < min_free_bytes:
            result["stop"] = "low_disk"
            break
        w = req.window()
        check_full_window(w)
        rows: list[dict[str, Any]] = []
        outside: list[float] = []
        retries = 0
        for attempt in range(len(retry_waits) + 1):
            if attempt:
                sleep(retry_waits[attempt - 1])
                retries += 1
            stamp = datetime.now(tz=UTC).isoformat()
            rows, outside = split_by_window(request_once(api, KIND_M1, symbol, w), w)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "rid": req.rid,
                            "attempt": attempt,
                            "redo_empty": redo_empty,
                            "requested_from_api_time": w.start.isoformat(),
                            "requested_to_api_time": w.end.isoformat(),
                            "returned_in_window": len(rows),
                            "out_of_window": len(outside),
                            "executed_at_utc": stamp,
                            "disk_free_bytes": free,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            if rows:
                break
        manifest["requests_sent"] = int(manifest["requests_sent"]) + 1
        result["sent_this_run"] += 1
        attempts = int(entry.get("attempts", 0)) + 1 if entry else 1
        new: dict[str, Any] = {
            "rid": req.rid,
            "pair": pair,
            "symbol": symbol,
            "year": req.year,
            "month": req.month,
            "file": req.file if rows else None,
            "status": STATUS_OK if rows else STATUS_EMPTY,
            "requested_from_api_time": w.start.isoformat(),
            "requested_to_api_time": w.end.isoformat(),
            "rows": len(rows),
            "first_bar_time": int(rows[0]["time"]) if rows else None,
            "last_bar_time": int(rows[-1]["time"]) if rows else None,
            "out_of_window_count": len(outside),
            "retries": retries,
            "attempts": attempts,
            "last_error": str(api.last_error()),
            "executed_at_utc": datetime.now(tz=UTC).isoformat(),
            "sha256_gz": None,
            "sha256_csv": None,
        }
        if rows:
            data = month_csv_bytes(rows)
            gz = gzip_bytes(data)
            _atomic_write(out_dir / req.file, gz)
            new["sha256_csv"] = _sha256_bytes(data)
            new["sha256_gz"] = _sha256_bytes(gz)
            result["ok"] += 1
        else:
            result["empty"] += 1
        files[req.rid] = new
        save()
        if not rows and first_request and not redo_empty:
            result["stop"] = "first_month_empty"
            break
        first_request = False
    result["requests_sent_total"] = int(manifest["requests_sent"])
    result["remaining"] = [r.rid for r in plan if not _entry_is_done(out_dir, files.get(r.rid))]
    manifest["runs"].append(
        {
            "pair": pair,
            "finished_at_utc": datetime.now(tz=UTC).isoformat(),
            **{k: v for k, v in result.items() if k != "remaining"},
        }
    )
    save()
    return result


def verify_full_dir(root: Path, pairs: Sequence[str] | None = None) -> list[str]:
    """回収した置き場（manifest と ``<通貨>/`` のファイル）を manifest と照合し、問題の一覧を返す。

    gzip の SHA-256・展開後の SHA-256・行数・見出し行・時刻が要求の区間の中にあること・
    manifest に無いファイルが無いことを確かめる。価格の列は読まない（時刻の列だけ）。
    """
    problems: list[str] = []
    manifest_path = root / FULL_MANIFEST
    if not manifest_path.exists():
        return [f"manifest が無い: {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    want = set(pairs) if pairs else None
    listed: set[str] = set()
    for rid, e in sorted(manifest["files"].items()):
        if want is not None and e["pair"] not in want:
            continue
        if e["status"] != STATUS_OK:
            continue
        listed.add(str(e["file"]))
        path = root / str(e["file"])
        if not path.exists():
            problems.append(f"{rid}: ファイルが無い")
            continue
        if _sha256(path) != e["sha256_gz"]:
            problems.append(f"{rid}: gzip の SHA-256 が合わない")
            continue
        data = gzip.decompress(path.read_bytes())
        if _sha256_bytes(data) != e["sha256_csv"]:
            problems.append(f"{rid}: 展開後の SHA-256 が合わない")
            continue
        lines = data.decode("utf-8").splitlines()
        if not lines or lines[0] != ",".join(RATE_FIELDS):
            problems.append(f"{rid}: 見出し行が違う")
            continue
        if len(lines) - 1 != e["rows"]:
            problems.append(
                f"{rid}: 行数が合わない（manifest {e['rows']}、ファイル {len(lines) - 1}）"
            )
            continue
        lo = datetime.fromisoformat(e["requested_from_api_time"]).timestamp()
        hi = datetime.fromisoformat(e["requested_to_api_time"]).timestamp()
        if hi > REQUEST_END_LIMIT.timestamp() or lo < FULL_FIRST_START.timestamp():
            problems.append(f"{rid}: 要求の区間が研究期間の外")
        times = [int(line.split(",", 1)[0]) for line in lines[1:]]
        if any(not (lo <= t < hi) for t in times):
            problems.append(f"{rid}: 区間の外の時刻がある")
    for path in sorted(root.glob("*/*.csv.gz")):
        rel = path.relative_to(root).as_posix()
        if (want is None or rel.split("/", 1)[0] in want) and rel not in listed:
            problems.append(f"manifest に無いファイル: {rel}")
    return problems


def _cmd_fullfetch(args: argparse.Namespace) -> int:
    pairs = [x for x in args.pairs.split(",") if x]
    full_plan(pairs)  # 一覧の外の通貨なら、つなぐ前に止まる
    api = load_mt5()
    if not api.initialize():
        print(json.dumps({"initialize": False, "last_error": str(api.last_error())}))
        return 2
    try:
        out_dir = Path(args.out_dir)
        info = api.terminal_info()
        data_path = Path(str(getattr(info, "data_path", "") or out_dir))
        tool_sha = _sha256(Path(__file__))
        for pair in pairs:
            result = run_full_fetch(
                api,
                pair,
                out_dir,
                redo_empty=args.redo_empty,
                max_total=args.max_total,
                stop_at_epoch=args.stop_at_epoch,
                disk_free=lambda: shutil.disk_usage(data_path).free,
                tool_sha256=tool_sha,
            )
            # 件数・状態だけ（価格は出さない）。38_full_fetch.sh がこの行を読む。
            print(FULL_RESULT_PREFIX + json.dumps(result, ensure_ascii=True), flush=True)
            if result["stop"] is not None:
                break
    finally:
        api.shutdown()
    return 0


def _cmd_fullverify(args: argparse.Namespace) -> int:
    pairs = [x for x in (args.pairs or "").split(",") if x] or None
    problems = verify_full_dir(Path(args.root), pairs)
    for p in problems:
        print(p)
    print(f"fullverify: 問題 {len(problems)} 件")
    return 1 if problems else 0


# --- 封印の取得（制約の改定文 ADR-0037。一括取得と定期取得の 2 段。順 6 の S4a） ------------
# 2024-01-01 以降の OANDA MT5 の 1 分足（bid）を、許可の窓の中だけ要求し、gzip の CSV に書く
# （ADR-0037 第1項の 2 の 1）。窓（取得を許す期間の始まり・取得の開始時点・8 通貨）は版管理の
# 設定 1 つ configs/acquisition/oanda_mt5_m1_sealed_window.yaml からだけ読み、この道具に日付を
# 書かない（ADR-0037 第4項の 1）。要求の一覧（計画）は Mac の tools/survey/sealed_fetch_plan.py が
# 窓の設定と 2024 年以降の時刻の規則の表から作る。Windows ではこの道具が、同じ窓の設定のファイル
# （SHA-256 が計画の記録と一致するもの）を読み直し、計画の要求をすべて窓と照らしてから（1 つでも
# 外れれば何も要求せずに止まる）要求する（ADR-0037 第4項の 3・5）。価格は CSV の中だけに書き、
# 取得の記録（fetch_log.jsonl・manifest.json）・標準出力には件数・時刻・SHA-256 だけを出す
# （ADR-0037 第7項）。研究期間の段の上限 REQUEST_END_LIMIT は研究期間の上限で、許可の窓ではない
# （ADR-0037 第4項の 6）。この段は REQUEST_END_LIMIT を使わない。

SEALED_KIND = "oanda_mt5_sealed_fetch"
SEALED_PLAN_KIND = "oanda_mt5_sealed_fetch_plan"
SEALED_STAGE_BULK = "bulk"
SEALED_STAGE_PERIODIC = "periodic"
SEALED_STAGES: tuple[str, ...] = (SEALED_STAGE_BULK, SEALED_STAGE_PERIODIC)
SEALED_RESULT_PREFIX = "SEALEDFETCH_RESULT "
#: 改定文が取得を許す 8 通貨（ADR-0037 第1項の 2 の 1）。窓の設定の銘柄はこの中に限る。
SEALED_PAIRS: tuple[str, ...] = (USDJPY, *OTHER_PAIRS)
#: API の時刻 − UTC の時間として表が取りうる値（2024 年以降の表 版 1 の標準と候補。D03 §16.2.3）。
SEALED_OFFSETS_HOURS: frozenset[int] = frozenset({2, 3})
#: 窓の設定の固定の値（ADR-0037 第4項の 2）。
_SEALED_FIXED: dict[str, str] = {
    "schema_version": "1",
    "decision": "ADR-0037",
    "datasource": "oanda_mt5_m1",
    "price_basis": "bid",
    "timeframe": "M1",
}
SEALED_STORAGE_TERMS = (
    "制約の改定文 ADR-0037 の下で取得した 2024 年以降の封印のデータ。誰も開かない。"
    "読む経路は関所の下の受入れと最終検証のコマンドだけ（ADR-0037 第1項の 3 の 3）。再配布しない。"
)


class SealedWindowError(ValueError):
    """許可の窓の外の要求・窓の設定の誤り・計画と設定の食い違い（何も要求せずに止まる）。"""


@dataclass(frozen=True)
class SealedWindow:
    """窓の設定のうち道具が使う値（ADR-0037 第4項の 2）。"""

    allowed_start: datetime
    acquisition_start: datetime | None
    symbols: tuple[str, ...]
    sha256: str


def _sealed_time(text: str, label: str) -> datetime:
    value = text.strip().strip('"').strip("'")
    if not value.endswith("Z"):
        raise SealedWindowError(f"{label} は UTC の時刻（末尾 Z）で書く: {text!r}")
    moment = datetime.fromisoformat(value[:-1] + "+00:00")
    return moment.astimezone(UTC)


def read_sealed_window(path: Path) -> SealedWindow:
    """窓の設定を読む（Windows の Python には YAML の読込が無いので、決まった形の行だけ読む）。

    最上位の ``key: value`` の行（字下げなし、``#`` から後は注記）だけを読み、固定の値・銘柄・
    期間の始まり・取得の開始時点を取り出す。形が違えば止まる（本基盤の読込
    ``app.config.sealed_window`` が正本で、こちらは同じファイルを道具の側で読み直すだけ）。
    """
    raw = path.read_bytes()
    found: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line or line[0] in " #\t":
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise SealedWindowError(f"{path}: 読めない行: {line!r}")
        value = value.split(" #", 1)[0].strip()
        if key in found:
            raise SealedWindowError(f"{path}: {key} が 2 度ある")
        found[key] = value
    for key, want in _SEALED_FIXED.items():
        if found.get(key) != want:
            raise SealedWindowError(
                f"{path}: {key} は {want} でなければならない（ADR-0037 第4項の 2）"
            )
    symbols_text = found.get("symbols", "")
    if not (symbols_text.startswith("[") and symbols_text.endswith("]")):
        raise SealedWindowError(f"{path}: symbols は [..] の列で書く")
    symbols = tuple(s.strip() for s in symbols_text[1:-1].split(",") if s.strip())
    if not symbols or len(set(symbols)) != len(symbols) or not set(symbols) <= set(SEALED_PAIRS):
        raise SealedWindowError(
            f"{path}: symbols は改定文の 8 通貨の中から重複なく 1 つ以上（ADR-0037 第1項の 2 の 1）"
        )
    if "allowed_start" not in found or "acquisition_start" not in found:
        raise SealedWindowError(f"{path}: allowed_start と acquisition_start が要る")
    allowed = _sealed_time(found["allowed_start"], "allowed_start")
    acq_text = found["acquisition_start"]
    acquisition = None if acq_text == "null" else _sealed_time(acq_text, "acquisition_start")
    if acquisition is not None and not allowed < acquisition:
        raise SealedWindowError(f"{path}: acquisition_start は allowed_start より後")
    return SealedWindow(
        allowed_start=allowed,
        acquisition_start=acquisition,
        symbols=symbols,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


@dataclass(frozen=True)
class SealedRequest:
    """封印の取得の 1 要求 = 1 通貨 × 1 区間（一括取得は API の時刻の 1 か月、定期取得は 1 窓）。

    ``api_*`` は API の時刻の値（MT5 に渡す。時刻の規則の表の標準の組で UTC から直した値）、
    ``utc_*`` はその区間の UTC（標準の組）。``offset_*_hours`` は始まり・終わりで使った
    「API の時刻 − UTC」。
    """

    rid: str
    pair: str
    stage: str
    api_from: datetime
    api_to: datetime
    utc_from: datetime
    utc_to: datetime
    offset_from_hours: int
    offset_to_hours: int
    file: str

    def window(self) -> Window:
        return Window(self.rid, self.api_from, self.api_to)

    def payload(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "pair": self.pair,
            "stage": self.stage,
            "api_from": self.api_from.isoformat(),
            "api_to": self.api_to.isoformat(),
            "utc_from": self.utc_from.isoformat(),
            "utc_to": self.utc_to.isoformat(),
            "offset_from_hours": self.offset_from_hours,
            "offset_to_hours": self.offset_to_hours,
            "file": self.file,
        }

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> SealedRequest:
        def moment(key: str) -> datetime:
            value = datetime.fromisoformat(str(raw[key]))
            if value.tzinfo is None:
                raise SealedWindowError(f"計画の {key} に時刻帯が無い: {raw[key]!r}")
            return value.astimezone(UTC)

        return cls(
            rid=str(raw["rid"]),
            pair=str(raw["pair"]),
            stage=str(raw["stage"]),
            api_from=moment("api_from"),
            api_to=moment("api_to"),
            utc_from=moment("utc_from"),
            utc_to=moment("utc_to"),
            offset_from_hours=int(raw["offset_from_hours"]),
            offset_to_hours=int(raw["offset_to_hours"]),
            file=str(raw["file"]),
        )


def check_sealed_request(
    request: SealedRequest, window: SealedWindow, stage: str, now: datetime
) -> None:
    """要求が許可の窓の中かを確かめる（外れれば ``SealedWindowError``。何も要求しない）。

    ADR-0037 第4項の 3: 要求の区間が [allowed_start, 要求の時点) の中、銘柄が設定の中、時間足と
    価格基準は設定の固定の値（この段は 1 分足の bid だけを要求する）。窓は要求する足の始まりの
    時刻（UTC。標準の組）で判定する。一括取得は [allowed_start, acquisition_start)、定期取得は
    [acquisition_start, 要求の時点)。取得の開始時点が空の間は、どちらの段も始めない。
    """
    label = request.rid
    if stage not in SEALED_STAGES or request.stage != stage:
        raise SealedWindowError(f"{label}: 段 {request.stage} は {stage} の要求ではない")
    if window.acquisition_start is None:
        raise SealedWindowError(
            "窓の設定の acquisition_start が空。一括取得も定期取得も始めない（ADR-0037 第4項の 3）"
        )
    if request.pair not in window.symbols:
        raise SealedWindowError(f"{label}: 通貨 {request.pair} は窓の設定の銘柄に無い")
    for moment in (request.api_from, request.api_to, request.utc_from, request.utc_to):
        if moment.tzinfo is None:
            raise SealedWindowError(f"{label}: 区間は時刻帯つきの時刻で作る")
    if not (request.api_from < request.api_to and request.utc_from < request.utc_to):
        raise SealedWindowError(f"{label}: 区間の始めが終わりより前でない")
    for api, utc, hours in (
        (request.api_from, request.utc_from, request.offset_from_hours),
        (request.api_to, request.utc_to, request.offset_to_hours),
    ):
        if hours not in SEALED_OFFSETS_HOURS or api - utc != timedelta(hours=hours):
            raise SealedWindowError(
                f"{label}: API の時刻と UTC の差が表の値（2・3 時間）と合わない"
            )
    if request.utc_from < window.allowed_start:
        raise SealedWindowError(
            f"{label}: {window.allowed_start.isoformat()} より前を含む要求は出さない"
            "（ADR-0037 第2項の 1）"
        )
    if request.utc_to > now:
        raise SealedWindowError(
            f"{label}: 要求の時点より後を含む要求は出さない（ADR-0037 第4項の 3）"
        )
    if stage == SEALED_STAGE_BULK and request.utc_to > window.acquisition_start:
        raise SealedWindowError(
            f"{label}: 一括取得は取得の開始時点より前だけ（ADR-0037 第2項の 3）"
        )
    if stage == SEALED_STAGE_PERIODIC and request.utc_from < window.acquisition_start:
        raise SealedWindowError(f"{label}: 定期取得は取得の開始時点から（ADR-0037 第2項の 3）")


def sealed_max_requests(planned: int) -> int:
    """要求の上限 = 計画の数 ＋ 1 割の切り上げ。

    D15-18c (a)、PR #109 の仮置き S2b-10（272 → 300、8 → 9）。
    """
    if planned < 1:
        raise SealedWindowError("計画の要求が 0")
    return -(-planned * 11 // 10)


def load_sealed_plan(
    plan_path: Path, window: SealedWindow, *, now: datetime
) -> tuple[dict[str, Any], list[SealedRequest]]:
    """計画を読み、窓の設定と照らして、すべての要求を確かめる（1 つでも外れれば何も要求しない）。"""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("kind") != SEALED_PLAN_KIND or plan.get("schema") != 1:
        raise SealedWindowError(f"{plan_path}: 封印の取得の計画ではない")
    if plan.get("window_config_sha256") != window.sha256:
        raise SealedWindowError(
            f"{plan_path}: 計画を作った窓の設定（{plan.get('window_config_sha256')}）と、"
            "渡した窓の設定"
            f"（{window.sha256}）が違う"
        )
    stage = str(plan.get("stage"))
    requests = [SealedRequest.from_payload(item) for item in plan["requests"]]
    if not requests:
        raise SealedWindowError(f"{plan_path}: 要求が無い")
    rids = [r.rid for r in requests]
    if len(set(rids)) != len(rids):
        raise SealedWindowError(f"{plan_path}: 同じ要求の名前が 2 度ある")
    for r in requests:
        check_sealed_request(r, window, stage, now)
    by_pair: dict[str, list[SealedRequest]] = {}
    for r in requests:
        by_pair.setdefault(r.pair, []).append(r)
    for pair, items in by_pair.items():
        ordered = sorted(items, key=lambda r: r.api_from)
        for a, b in zip(ordered, ordered[1:], strict=False):
            if a.api_to > b.api_from:
                raise SealedWindowError(f"{plan_path}: {pair} の要求の区間が重なる")
    if int(plan.get("planned_requests", -1)) != len(requests):
        raise SealedWindowError(f"{plan_path}: planned_requests が要求の数と違う")
    if int(plan.get("max_requests", -1)) != sealed_max_requests(len(requests)):
        raise SealedWindowError(f"{plan_path}: max_requests が計画の数 ＋ 1 割の切り上げと違う")
    return plan, requests


def load_sealed_manifest(
    out_dir: Path, plan: Mapping[str, Any], plan_sha256: str
) -> dict[str, Any]:
    """出力の置き場の manifest を読む（無ければ空の形。別の計画の manifest なら止まる）。"""
    path = out_dir / FULL_MANIFEST
    if path.exists():
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if data.get("plan_sha256") != plan_sha256:
            raise SealedWindowError(f"{path}: 別の計画の取得の記録がある（計画の SHA-256 が違う）")
        return data
    return {
        "kind": SEALED_KIND,
        "schema": 1,
        "stage": plan["stage"],
        "decision": "ADR-0037",
        "time_basis": (
            "API の生の時刻（秒）。変換しない（D12-47）。"
            "要求の区間は時刻の規則の表の標準の組で UTC から直した"
        ),
        "price_basis": "bid の足（chart_mode 0）",
        "columns": list(RATE_FIELDS),
        "window_config_sha256": plan["window_config_sha256"],
        "time_rule_table": plan["time_rule_table"],
        "window": plan["window"],
        "plan_sha256": plan_sha256,
        "planned_requests": plan["planned_requests"],
        "max_requests": plan["max_requests"],
        "requests_sent": 0,
        "storage_terms": SEALED_STORAGE_TERMS,
        "symbols": {},
        "files": {},
        "runs": [],
    }


def run_sealed_fetch(
    api: Mt5Api,
    pair: str,
    out_dir: Path,
    *,
    plan: Mapping[str, Any],
    plan_sha256: str,
    requests: Sequence[SealedRequest],
    window: SealedWindow,
    redo_empty: bool = False,
    max_total: int | None = None,
    stop_at_epoch: float | None = None,
    disk_free: Callable[[], int] | None = None,
    min_free_bytes: int = MIN_FREE_BYTES,
    retry_waits: Sequence[float] = SURVEY_RETRY_WAITS,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    tool_sha256: str | None = None,
) -> dict[str, Any]:
    """1 通貨の封印の取得（計画の順）。上限・打ち切り・空きに達したら次の要求を出さない。

    全期間取得（``run_full_fetch``）と同じ扱い: 取得済みの要求は出さない、0 本なら待って出し直す
    （数えない）、この回の最初の要求が 0 本ならその通貨の残りを出さずに止まる、``redo_empty`` なら
    0 本だった要求だけを出す。要求の直前にも窓を確かめる（``check_sealed_request``）。価格は CSV
    にだけ書く。ディスクの空きは低ディスクの停止の判定にだけ使い、記録しない（空きの差から端末の
    履歴の大きさが分かるため。PR #109 の仮置き S2b-11・D03 §16.4）。
    """
    stage = str(plan["stage"])
    limit = int(plan["max_requests"])
    if max_total is None:
        max_total = limit
    if max_total > limit:
        raise SealedWindowError(f"要求は合計 {limit} まで（指定 {max_total}）")
    if pair not in window.symbols:
        raise SealedWindowError(f"通貨 {pair} は窓の設定の銘柄に無い")
    own = [r for r in requests if r.pair == pair]
    for r in own:
        check_sealed_request(r, window, stage, now())
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_sealed_manifest(out_dir, plan, plan_sha256)
    if tool_sha256:
        manifest["tool_sha256"] = tool_sha256
    manifest["terminal"] = terminal_summary(api)
    files: dict[str, Any] = manifest["files"]
    log_path = out_dir / FULL_FETCH_LOG
    result: dict[str, Any] = {
        "pair": pair,
        "stage": stage,
        "redo_empty": redo_empty,
        "sent_this_run": 0,
        "ok": 0,
        "empty": 0,
        "skipped_done": 0,
        "stop": None,
    }

    def save() -> None:
        _atomic_write(
            out_dir / FULL_MANIFEST,
            json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"),
        )

    symbol, _candidates = resolve_symbol(api, pair)
    if symbol is None:
        result["stop"] = "symbol_not_found"
    else:
        api.symbol_select(symbol, True)
        mode, point, digits = symbol_meta(api, symbol)
        manifest["symbols"][pair] = {
            "symbol": symbol,
            "chart_mode": mode,
            "point": point,
            "digits": digits,
        }
    first_request = True
    for req in own if result["stop"] is None else []:
        assert symbol is not None
        entry = files.get(req.rid)
        if redo_empty:
            if not entry or entry.get("status") != STATUS_EMPTY:
                continue
        elif _entry_is_done(out_dir, entry):
            result["skipped_done"] += 1
            continue
        elif entry and entry.get("status") == STATUS_EMPTY:
            continue
        if int(manifest["requests_sent"]) >= max_total:
            result["stop"] = "request_limit"
            break
        if stop_at_epoch is not None and clock() >= stop_at_epoch:
            result["stop"] = "time_limit"
            break
        free = disk_free() if disk_free else None
        if free is not None and free < min_free_bytes:
            result["stop"] = "low_disk"
            break
        check_sealed_request(req, window, stage, now())
        w = req.window()
        rows: list[dict[str, Any]] = []
        outside: list[float] = []
        retries = 0
        for attempt in range(len(retry_waits) + 1):
            if attempt:
                sleep(retry_waits[attempt - 1])
                retries += 1
            stamp = datetime.now(tz=UTC).isoformat()
            returned = rows_of(api.copy_rates_range(symbol, api.TIMEFRAME_M1, w.start, w.end))
            rows, outside = split_by_window(returned, w)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "rid": req.rid,
                            "attempt": attempt,
                            "redo_empty": redo_empty,
                            "requested_from_api_time": w.start.isoformat(),
                            "requested_to_api_time": w.end.isoformat(),
                            "returned_in_window": len(rows),
                            "out_of_window": len(outside),
                            "executed_at_utc": stamp,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            if rows:
                break
        manifest["requests_sent"] = int(manifest["requests_sent"]) + 1
        result["sent_this_run"] += 1
        attempts = int(entry.get("attempts", 0)) + 1 if entry else 1
        new: dict[str, Any] = {
            "rid": req.rid,
            "pair": pair,
            "symbol": symbol,
            "stage": stage,
            "file": req.file if rows else None,
            "status": STATUS_OK if rows else STATUS_EMPTY,
            "requested_from_api_time": w.start.isoformat(),
            "requested_to_api_time": w.end.isoformat(),
            "requested_from_utc": req.utc_from.isoformat(),
            "requested_to_utc": req.utc_to.isoformat(),
            "rows": len(rows),
            "first_bar_time": int(rows[0]["time"]) if rows else None,
            "last_bar_time": int(rows[-1]["time"]) if rows else None,
            "out_of_window_count": len(outside),
            "retries": retries,
            "attempts": attempts,
            "last_error": str(api.last_error()),
            "executed_at_utc": datetime.now(tz=UTC).isoformat(),
            "sha256_gz": None,
            "sha256_csv": None,
        }
        if rows:
            data = month_csv_bytes(rows)
            gz = gzip_bytes(data)
            _atomic_write(out_dir / req.file, gz)
            new["sha256_csv"] = _sha256_bytes(data)
            new["sha256_gz"] = _sha256_bytes(gz)
            result["ok"] += 1
        else:
            result["empty"] += 1
        files[req.rid] = new
        save()
        if not rows and first_request and not redo_empty:
            result["stop"] = "first_request_empty"
            break
        first_request = False
    result["requests_sent_total"] = int(manifest["requests_sent"])
    result["remaining"] = [r.rid for r in own if not _entry_is_done(out_dir, files.get(r.rid))]
    manifest["runs"].append(
        {
            "pair": pair,
            "finished_at_utc": datetime.now(tz=UTC).isoformat(),
            **{k: v for k, v in result.items() if k != "remaining"},
        }
    )
    save()
    return result


def verify_sealed_dir(root: Path, plan_path: Path, pairs: Sequence[str] | None = None) -> list[str]:
    """回収した封印の取得の置き場を manifest と計画と照合し、問題の一覧を返す（Mac）。

    要求の区間が計画の要求と同じこと・ファイルの置き場・gzip のバイト列の SHA-256・manifest に
    無いファイルが無いことだけを確かめる。**ファイルを展開しない・行に分けない**（封印の原ファイル
    の中身を読むのは関所の品質確認の許可の下の受入れだけ。ADR-0036 第8項・D11 §10 の受入条件 25。
    展開後の SHA-256・行数・見出し・時刻が要求の区間の中にあることは、受入れが許可の下で確かめる。
    D03 §16.3.1 の検査 1・2）。全期間取得の ``verify_full_dir`` と違い、時刻の列も読まない。
    """
    problems: list[str] = []
    manifest_path = root / FULL_MANIFEST
    if not manifest_path.exists():
        return [f"manifest が無い: {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_sha = _sha256(plan_path)
    if manifest.get("kind") != SEALED_KIND or manifest.get("plan_sha256") != plan_sha:
        return [f"manifest が計画 {plan_path} の封印の取得の記録ではない"]
    planned = {str(item["rid"]): SealedRequest.from_payload(item) for item in plan["requests"]}
    want = set(pairs) if pairs else None
    listed: set[str] = set()
    for rid, e in sorted(manifest["files"].items()):
        if want is not None and e["pair"] not in want:
            continue
        req = planned.get(rid)
        if req is None:
            problems.append(f"{rid}: 計画に無い要求")
            continue
        lo = datetime.fromisoformat(e["requested_from_api_time"])
        hi = datetime.fromisoformat(e["requested_to_api_time"])
        if lo != req.api_from or hi != req.api_to:
            problems.append(f"{rid}: 要求の区間が計画と違う")
            continue
        if e["status"] != STATUS_OK:
            continue
        listed.add(str(e["file"]))
        path = root / str(e["file"])
        if str(e["file"]) != req.file or not path.exists():
            problems.append(f"{rid}: ファイルが無い")
            continue
        if _sha256(path) != e["sha256_gz"]:
            problems.append(f"{rid}: gzip の SHA-256 が合わない")
            continue
    for path in sorted(root.glob("*/*.csv.gz")):
        rel = path.relative_to(root).as_posix()
        if (want is None or rel.split("/", 1)[0] in want) and rel not in listed:
            problems.append(f"manifest に無いファイル: {rel}")
    return problems


def _cmd_sealedfetch(args: argparse.Namespace) -> int:
    window = read_sealed_window(Path(args.window))
    plan_path = Path(args.plan)
    plan, requests = load_sealed_plan(plan_path, window, now=datetime.now(tz=UTC))
    pairs = [x for x in args.pairs.split(",") if x]
    for pair in pairs:
        if pair not in window.symbols:
            raise SealedWindowError(f"通貨 {pair} は窓の設定の銘柄に無い")
    api = load_mt5()
    if not api.initialize():
        print(json.dumps({"initialize": False, "last_error": str(api.last_error())}))
        return 2
    try:
        out_dir = Path(args.out_dir)
        info = api.terminal_info()
        data_path = Path(str(getattr(info, "data_path", "") or out_dir))
        tool_sha = _sha256(Path(__file__))
        for pair in pairs:
            result = run_sealed_fetch(
                api,
                pair,
                out_dir,
                plan=plan,
                plan_sha256=_sha256(plan_path),
                requests=requests,
                window=window,
                redo_empty=args.redo_empty,
                max_total=args.max_total,
                stop_at_epoch=args.stop_at_epoch,
                disk_free=lambda: shutil.disk_usage(data_path).free,
                tool_sha256=tool_sha,
            )
            # 件数・状態だけ（価格は出さない）。39_sealed_fetch.sh がこの行を読む。
            print(SEALED_RESULT_PREFIX + json.dumps(result, ensure_ascii=True), flush=True)
            if result["stop"] is not None:
                break
    finally:
        api.shutdown()
    return 0


def _cmd_sealedverify(args: argparse.Namespace) -> int:
    pairs = [x for x in (args.pairs or "").split(",") if x] or None
    problems = verify_sealed_dir(Path(args.root), Path(args.plan), pairs)
    for p in problems:
        print(p)
    print(f"sealedverify: 問題 {len(problems)} 件")
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="Windows の MT5 から短い区間を要求する")
    p.add_argument("--phase", choices=sorted(PHASES), required=True)
    p.add_argument("--out-dir", required=True)
    t = sub.add_parser("timesurvey", help="時刻の調査の段（D12 §11.7）を Windows の MT5 で要求する")
    t.add_argument(
        "--stage", choices=[*TIME_SURVEY_STAGES, "extra", "G", FINAL_STAGE_NAME], required=True
    )
    t.add_argument("--out-dir", required=True)
    t.add_argument("--max-requests", type=int, required=True)
    t.add_argument("--plan-file")
    t.add_argument("--e1-ref")
    t.add_argument(
        "--only", help="最後の調査（K10）の一覧の中の要求の名前（カンマ区切り。やり直し用）"
    )
    s = sub.add_parser(
        "synctrigger", help="履歴の取り寄せを起こす tick の要求（1 回だけ。値は保存しない）"
    )
    s.add_argument("--out-dir", required=True)
    f = sub.add_parser(
        "fullfetch",
        help="全期間取得（付録 K-16）を Windows の MT5 で要求し、gzip の CSV で保存する",
    )
    f.add_argument("--out-dir", required=True)
    f.add_argument("--pairs", required=True, help="通貨（カンマ区切り。付録 K-16.1 の 8 通貨の中）")
    f.add_argument("--max-total", type=int, default=FULL_MAX_REQUESTS)
    f.add_argument("--stop-at-epoch", type=float, help="この時刻（UNIX 秒）以降は要求を出さない")
    f.add_argument("--redo-empty", action="store_true", help="0 本だった月だけを出し直す")
    v = sub.add_parser("fullverify", help="回収した全期間取得の置き場を manifest と照合する（Mac）")
    v.add_argument("--root", required=True)
    v.add_argument("--pairs")
    sf = sub.add_parser(
        "sealedfetch",
        help="封印の取得（ADR-0037。一括・定期）を Windows の MT5 で要求し、gzip の CSV で保存する",
    )
    sf.add_argument(
        "--plan", required=True, help="Mac の sealed_fetch_plan.py が作った計画（JSON）"
    )
    sf.add_argument(
        "--window", required=True, help="窓の設定（oanda_mt5_m1_sealed_window.yaml の写し）"
    )
    sf.add_argument("--out-dir", required=True)
    sf.add_argument("--pairs", required=True, help="通貨（カンマ区切り。窓の設定の銘柄の中）")
    sf.add_argument("--max-total", type=int, help="要求の上限（既定は計画の上限。超える値は拒む）")
    sf.add_argument("--stop-at-epoch", type=float, help="この時刻（UNIX 秒）以降は要求を出さない")
    sf.add_argument("--redo-empty", action="store_true", help="0 本だった要求だけを出し直す")
    sv = sub.add_parser(
        "sealedverify", help="回収した封印の取得の置き場を manifest と計画と照合する（Mac）"
    )
    sv.add_argument("--root", required=True)
    sv.add_argument("--plan", required=True)
    sv.add_argument("--pairs")
    c = sub.add_parser("compare", help="USDJPY の tick を配布 CSV と突き合わせる（Mac）")
    c.add_argument("--probe-dir", required=True)
    c.add_argument("--oanda-csv", required=True)
    args = parser.parse_args(argv)
    if args.command == "probe":
        return _cmd_probe(args)
    if args.command == "timesurvey":
        return _cmd_timesurvey(args)
    if args.command == "synctrigger":
        return _cmd_synctrigger(args)
    if args.command == "fullfetch":
        return _cmd_fullfetch(args)
    if args.command == "fullverify":
        return _cmd_fullverify(args)
    if args.command == "sealedfetch":
        return _cmd_sealedfetch(args)
    if args.command == "sealedverify":
        return _cmd_sealedverify(args)
    return _cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
