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
- 要求の終わりは 2024-01-01T00:00Z より前に限る（``REQUEST_END_LIMIT``）。
  超える要求は出さずに止まる。
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
import hashlib
import importlib
import json
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

#: 要求の終わりの上限（この時刻以降を含む要求は出さない）。研究期間 2017〜2023 年（D12）。
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="Windows の MT5 から短い区間を要求する")
    p.add_argument("--phase", choices=sorted(PHASES), required=True)
    p.add_argument("--out-dir", required=True)
    c = sub.add_parser("compare", help="USDJPY の tick を配布 CSV と突き合わせる（Mac）")
    c.add_argument("--probe-dir", required=True)
    c.add_argument("--oanda-csv", required=True)
    args = parser.parse_args(argv)
    if args.command == "probe":
        return _cmd_probe(args)
    return _cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
