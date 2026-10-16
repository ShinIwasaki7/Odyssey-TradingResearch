"""oanda_csv_series_check（配布 tick CSV の主系列候補の検査）の単体テスト。人工データだけを使う。"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tools.survey import mt5_api_probe as probe
from tools.survey import oanda_csv_series_check as chk

ROOT = Path(__file__).resolve().parents[2]
CALENDAR = ROOT / "configs/calendars/fx_ny17_v2.yaml"
TIMEFRAMES = ROOT / "configs/calendars/timeframes_v1.yaml"
NEW_YORK = ZoneInfo("America/New_York")
HEADER = "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\n"
#: 出力に出てはいけない価格の目印（人工の値）
MARK_BID = "123.457"


@pytest.fixture(scope="module")
def calendar() -> TradingCalendar:
    return load_calendar(CALENDAR)


@pytest.fixture(scope="module")
def timeframes() -> dict[str, TimeframeDefinition]:
    return load_timeframes(TIMEFRAMES)


def _server(epoch_ms: int) -> tuple[str, str]:
    """UTC のミリ秒を配布 CSV の元の時刻（ニューヨークの現地時刻 + 7 時間）にする。"""
    local = datetime.fromtimestamp(epoch_ms / 1000, UTC).astimezone(NEW_YORK)
    server = local.replace(tzinfo=None) + timedelta(hours=7)
    return server.strftime("%Y.%m.%d"), server.strftime("%H:%M:%S.") + f"{epoch_ms % 1000:03d}"


def _line(epoch_ms: int, bid: str, ask: str) -> str:
    d, t = _server(epoch_ms)
    return f"{d}\t{t}\t{bid}\t{ask}\t\t\n"


def _ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


def _write(tmp_path: Path, name: str, lines: list[str]) -> Path:
    path = tmp_path / "USDJPY" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "".join(lines), encoding="utf-8")
    return path


def _full_month_lines(calendar: TradingCalendar, skip: frozenset[int] = frozenset()) -> list[str]:
    lo, hi = chk.jst_month_window(2019, 3)
    minutes = chk.expected_minutes(chk.session_bounds(calendar, lo, hi))
    return [_line(m * 1000 + 500, MARK_BID, "123.460") for m in minutes if m not in skip]


# --- 時刻 -----------------------------------------------------------------------


def test_jst_month_window_follows_faq_2191() -> None:
    lo, hi = chk.jst_month_window(2019, 3)
    assert chk.iso_ms(lo) == "2019-02-28T15:00:00.000Z"
    assert chk.iso_ms(hi) == "2019-03-31T15:00:00.000Z"


@pytest.mark.parametrize(
    ("d", "t", "utc"),
    [
        ("2019.03.05", "02:00:00.250", "2019-03-05T00:00:00.250Z"),  # 米国の冬: +2
        ("2019.03.12", "03:00:00.000", "2019-03-12T00:00:00.000Z"),  # 米国の夏: +3
        ("2023.03.14", "03:15:30.001", "2023-03-14T00:15:30.001Z"),
    ],
)
def test_server_clock_matches_probe_rule(d: str, t: str, utc: str) -> None:
    clock = chk.ServerClock()
    hh, mm, rest = t.split(":")
    ss, ms = rest.split(".")
    got = clock.to_utc_ms(d.encode(), hh.encode(), mm.encode(), ss.encode(), ms.encode())
    assert chk.iso_ms(got) == utc
    expected = probe.server_time_to_utc(d, t)
    assert got == int(expected.timestamp() * 1000)


# --- 1 ファイルの走査 -------------------------------------------------------------


def test_scan_counts_quality_items(tmp_path: Path, calendar: TradingCalendar) -> None:
    t0 = _ms("2019-03-05T00:00:00")
    lines = [
        _line(t0, "111.000", "111.004"),
        _line(t0, "111.000", "111.004"),  # 同じ内容の連続
        _line(t0, "111.001", "111.004"),  # 同じ時刻の連続（内容は違う）
        _line(t0 + 1500, "111.002", "111.001"),  # ask < bid
        _line(t0 + 2000, "111.002", "111.002"),  # ask = bid
        _line(t0 + 1000, "111.002", "111.006"),  # 逆行
        _line(t0 + 3000, "111.300", "111.304"),  # 30 pip の跳び
        _line(t0 + 3000 + 120_000, "111.300", "111.304"),  # 2 分の間隔
        "2019.03.05\t02:00:09\t111.0\t111.0\t\t\n",  # 時刻の書式の違反
        "2019.03.05\t02:00:10.000\t\t111.004\t\t\n",  # bid が空
        "2019.03.05\t02:00:11.000\t111.000\t111.004\t1\t\n",  # LAST が空でない
        _line(_ms("2019-03-09T12:00:00"), "111.000", "111.004"),  # 土曜（セッションの外）
        _line(_ms("2019-03-31T16:00:00"), "111.000", "111.004"),  # 日本時間の月の外
    ]
    scan = chk.scan_file(_write(tmp_path, "ticks_USDJPY-oj5k_2019-03.csv", lines), calendar)
    assert scan.header_ok
    assert scan.rows == 13
    assert scan.format_violations == 3
    assert scan.empty_or_non_numeric_price == 1
    assert scan.last_or_volume_not_empty == 1
    assert scan.same_time_consecutive == 2
    assert scan.same_content_consecutive == 1
    assert scan.backward == 1
    assert scan.ask_lt_bid == 1 and scan.ask_eq_bid == 1
    assert scan.bid_digits == {3: 10}
    assert scan.point_digits == 3
    assert scan.spread_points[4] >= 3 and min(scan.spread_points) == -1
    assert max(scan.jump_pips) == 29  # 111.002 → 111.300 は 29.8 pip（切り捨て）
    assert sum(v for k, v in scan.interval_ms.items() if k >= 60_000) == 1
    assert scan.outside_session == 2  # 土曜と、月の外の日曜の朝
    assert scan.outside_month == 1
    assert scan.sha256 == chk.sha256_of(tmp_path / "USDJPY" / "ticks_USDJPY-oj5k_2019-03.csv")


def test_scan_refuses_rows_and_files_after_2023(tmp_path: Path, calendar: TradingCalendar) -> None:
    path = _write(
        tmp_path, "ticks_USDJPY-oj5k_2023-12.csv", ["2024.01.02\t03:00:00.000\t1.0\t1.1\t\t\n"]
    )
    with pytest.raises(chk.CheckError):
        chk.scan_file(path, calendar)
    later = _write(tmp_path, "ticks_USDJPY-oj5k_2024-01.csv", [])
    with pytest.raises(chk.CheckError):
        chk.scan_file(later, calendar)


# --- 足の生成と日足の完全性 ----------------------------------------------------------


@pytest.fixture(scope="module")
def full_month(tmp_path_factory: pytest.TempPathFactory, calendar: TradingCalendar) -> chk.FileScan:
    tmp = tmp_path_factory.mktemp("full")
    return chk.scan_file(
        _write(tmp, "ticks_USDJPY-oj5k_2019-03.csv", _full_month_lines(calendar)), calendar
    )


def test_full_month_builds_every_bar_on_the_same_route(
    full_month: chk.FileScan,
    calendar: TradingCalendar,
    timeframes: dict[str, TimeframeDefinition],
) -> None:
    report = chk.analyse(full_month, calendar, timeframes)
    row = chk.file_row(report)
    assert report.missing == []
    assert row["t4_expected_minutes"] == 30660
    for tf_id in chk.CHAIN:
        assert row[f"bars_{tf_id}_complete"] == row[f"bars_{tf_id}_expected"] > 0
    assert row["bars_1d_ny17_expected"] == 21
    assert row["daily_from_1h_equal"] is True
    assert row["daily_longest_complete_run_rule1"] == 21
    assert row["daily_evaluable_after_warmup_rule1"] == 0  # 1 か月では 60 本に届かない
    assert row["t5_k13_exclusions_in_window"] == "なし"


def test_one_missing_minute_breaks_its_day(
    full_month: chk.FileScan,
    calendar: TradingCalendar,
    timeframes: dict[str, TimeframeDefinition],
) -> None:
    scan = copy.copy(full_month)
    scan.minutes = dict(full_month.minutes)
    gap = _ms("2019-03-13T21:01:00") // 1000  # NY 17:01（日替わりの直後）
    del scan.minutes[gap]
    report = chk.analyse(scan, calendar, timeframes)
    row = chk.file_row(report)
    assert report.missing == [gap]
    assert row["t4_missing_in_ny17_hour"] == 1
    assert row["bars_15m_complete"] == row["bars_15m_expected"] - 1
    assert row["bars_1h_complete"] == row["bars_1h_expected"] - 1
    assert row["bars_4h_ny17_complete"] == row["bars_4h_ny17_expected"] - 1
    assert row["bars_1d_ny17_complete"] == 20
    assert row["daily_incomplete_rule1"] == 1
    assert row["daily_incomplete_only_ny17_hour"] == 1
    assert row["daily_longest_complete_run_rule1"] == 11  # 03-14〜03-28
    incomplete = [d for d in report.daily if not d["complete"]]
    assert [d["trading_day"] for d in incomplete] == ["2019-03-14"]

    # 定義 (2): 休場の区間がその分を覆えば、その日は欠けにならない（定義 (1) は変わらない）
    spans = (("test-closure", gap, gap + 60),)
    report2 = chk.analyse(scan, calendar, timeframes, spans)
    row2 = chk.file_row(report2)
    assert row2["daily_incomplete_rule1"] == 1
    assert row2["daily_incomplete_rule2"] == 0
    assert row2["daily_longest_complete_run_rule2"] == 21
    assert row2["t5_k13_exclusions_in_window"] == "test-closure"


def test_complete_runs_and_warmup() -> None:
    assert chk.complete_runs([True, True, False, True, False, False, True]) == [2, 1, 1]
    assert chk.evaluable_after_warmup([70, 59, 61]) == 11
    assert chk.evaluable_after_warmup([]) == 0


def test_exclusion_spans_read_only_times(tmp_path: Path) -> None:
    path = tmp_path / "ex.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {"id": "b", "start_utc": "2019-12-24T22:00Z", "end_utc": "2019-12-25T22:00Z"},
                    {"id": "a", "start_utc": "2018-12-24T22:00Z", "end_utc": "2018-12-25T22:00Z"},
                ]
            }
        ),
        encoding="utf-8",
    )
    spans = chk.read_exclusion_spans(path)
    assert [s[0] for s in spans] == ["a", "b"]
    assert chk.read_exclusion_spans(None) == ()


# --- API との並べ比較 ----------------------------------------------------------------


def _summary(tmp_path: Path, name: str, requests: list[dict[str, object]]) -> None:
    path = tmp_path / "probe" / "2026-10-16" / name / f"summary_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"requests": requests}), encoding="utf-8")


def test_compare_api_reports_both_sides_without_filling(
    tmp_path: Path,
    full_month: chk.FileScan,
    calendar: TradingCalendar,
    timeframes: dict[str, TimeframeDefinition],
) -> None:
    scan = copy.copy(full_month)
    scan.minutes = dict(full_month.minutes)
    csv_gap = _ms("2019-03-05T00:10:00") // 1000
    del scan.minutes[csv_gap]
    report = chk.analyse(scan, calendar, timeframes)
    api_from = _ms("2019-03-05T02:00:00") // 1000  # API の時刻（+2 時間）
    api_bars = [api_from + 60 * i for i in range(120) if i != 30]  # 00:30Z が API の空白
    request: dict[str, object] = {
        "rid": "B2-USDJPY-T2019-03-05",
        "pair": "USDJPY",
        "kind": "tuesday",
        "requested_from_api_time": "2019-03-05T02:00:00+00:00",
        "requested_to_api_time": "2019-03-05T04:00:00+00:00",
        "bar_times": api_bars,
        "close": [1.0],
    }
    _summary(tmp_path, "ts-A", [dict(request, bar_times=[])])  # 空の回（失敗の記録）
    _summary(tmp_path, "ts-B", [request])
    windows = chk.read_api_windows(tmp_path / "probe")
    assert len(windows) == 1 and windows[0].source.endswith("summary_ts-B.json")
    item = chk.compare_api(windows[0], report, calendar, "標準")
    assert item is not None
    assert item["expected_minutes"] == 120
    assert item["api_missing"] == 1 and item["csv_missing"] == 1
    assert item["api_only_missing"] == 1 and item["csv_only_missing"] == 1
    assert item["both_missing"] == 0
    assert item["api_only_runs"] == "2019-03-04 19:30+1"
    # 両方冬の火曜は候補の間で変換が同じなので、候補の行は出さない
    assert chk.compare_api(windows[0], report, calendar, "候補") is None
    # CSV の分の集合は比較で変わらない（API の欠けを CSV で埋めない）
    assert csv_gap not in scan.minutes


def test_main_writes_statistics_without_prices(tmp_path: Path, calendar: TradingCalendar) -> None:
    lines = _full_month_lines(calendar)[:3000]
    _write(tmp_path / "csv", "ticks_USDJPY-oj5k_2019-03.csv", lines)
    _summary(tmp_path, "ts-A", [])
    out = tmp_path / "out"
    code = chk.main(
        [
            "--csv-root",
            str(tmp_path / "csv"),
            "--probe-root",
            str(tmp_path / "probe"),
            "--calendar",
            str(CALENDAR),
            "--timeframes",
            str(TIMEFRAMES),
            "--out-dir",
            str(out),
        ]
    )
    assert code == 0
    names = {p.name for p in out.iterdir()}
    assert {
        "file_metrics.csv",
        "daily_bars.csv",
        "gap_runs.csv",
        "report.md",
        "inputs.json",
    } <= names
    for path in out.iterdir():
        text = path.read_text(encoding="utf-8")
        assert MARK_BID not in text and "123.46" not in text
