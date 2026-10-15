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
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
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
PHASES: dict[str, tuple[tuple[str, Window], ...]] = {
    "usdjpy-time": PHASE_USDJPY_TIME,
    "usdjpy-range": PHASE_USDJPY_RANGE,
    "others": PHASE_OTHERS,
}


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
    kind: str
    window: str
    requested_from_utc: str
    requested_to_utc: str
    returned_count: int
    returned_first_raw: float | None
    returned_last_raw: float | None
    returned_first_as_utc: str | None
    returned_last_as_utc: str | None
    last_error: str
    retried: bool
    output_file: str | None
    output_sha256: str | None
    tick_history: str


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
RATE_FIELDS: tuple[str, ...] = ("time", "open", "high", "low", "close", "tick_volume", "spread")


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


def run_phase(
    api: Mt5Api,
    plan: Iterable[tuple[str, Window]],
    out_dir: Path,
    now: datetime | None = None,
    retry: bool = True,
    retry_wait_seconds: float = 5.0,
) -> list[RequestRecord]:
    """計画の要求を順に出し、記録を返す（CSV は ``out_dir`` に保存する）。"""
    plan = list(plan)
    for _, window in plan:
        check_window(window)  # 1 つでも範囲外があれば、何も要求せずに止まる
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[RequestRecord] = []
    resolved: dict[str, tuple[str | None, list[str]]] = {}
    for pair, window in plan:
        if pair not in resolved:
            resolved[pair] = resolve_symbol(api, pair)
            if resolved[pair][0] is not None:
                api.symbol_select(str(resolved[pair][0]), True)
        symbol, candidates = resolved[pair]
        tick_count = 0
        for kind in (KIND_TICKS, KIND_M1):
            stamp = (now or datetime.now(tz=UTC)).isoformat()
            rows: list[dict[str, Any]] = []
            retried = False
            if symbol is not None:
                rows = request_once(api, kind, symbol, window)
                if not rows and retry:
                    retried = True
                    time.sleep(retry_wait_seconds)  # 端末が履歴を取り寄せる時間を置く
                    rows = request_once(api, kind, symbol, window)
            error = str(api.last_error())
            out_file: str | None = None
            digest: str | None = None
            if rows:
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
                    kind=kind,
                    window=window.label,
                    requested_from_utc=window.start.isoformat(),
                    requested_to_utc=window.end.isoformat(),
                    returned_count=len(rows),
                    returned_first_raw=firsts[0] if firsts else None,
                    returned_last_raw=lasts[0] if lasts else None,
                    returned_first_as_utc=_iso_from_epoch(firsts[0]) if firsts else None,
                    returned_last_as_utc=_iso_from_epoch(lasts[0]) if lasts else None,
                    last_error=error,
                    retried=retried,
                    output_file=out_file,
                    output_sha256=digest,
                    tick_history=status,
                )
            )
    return records


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
        records = run_phase(api, PHASES[args.phase], out_dir)
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
