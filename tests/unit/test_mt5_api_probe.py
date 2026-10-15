"""mt5_api_probe（MT5 Python API の小規模な試験の道具）の単体テスト。

MetaTrader5 は Windows 専用なので、ここでは偽の API を使う。人工データだけを使う。
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.survey import mt5_api_probe as probe

SOURCE = Path(probe.__file__)


class FakeMt5:
    COPY_TICKS_ALL = 1
    TIMEFRAME_M1 = 1

    def __init__(
        self,
        ticks: list[dict[str, Any]] | None = None,
        rates: list[dict[str, Any]] | None = None,
        names: tuple[str, ...] = ("USDJPY",),
    ) -> None:
        self.ticks = ticks or []
        self.rates = rates or []
        self.names = names
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def initialize(self) -> bool:
        return True

    def version(self) -> Any:
        return (500, 5000, "test")

    def terminal_info(self) -> Any:
        return SimpleNamespace(build=5000, name="t", company="c", maxbars=100000, connected=True)

    def symbols_get(self, group: str) -> Any:
        self.calls.append(("symbols_get", (group,)))
        key = group.strip("*")
        return [SimpleNamespace(name=n) for n in self.names if key in n]

    def symbol_info(self, symbol: str) -> Any:
        return None

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.calls.append(("symbol_select", (symbol, enable)))
        return True

    def copy_ticks_range(
        self, symbol: str, date_from: datetime, date_to: datetime, flags: int
    ) -> Any:
        self.calls.append(("copy_ticks_range", (symbol, date_from, date_to, flags)))
        return list(self.ticks)

    def copy_rates_range(
        self, symbol: str, timeframe: int, date_from: datetime, date_to: datetime
    ) -> Any:
        self.calls.append(("copy_rates_range", (symbol, timeframe, date_from, date_to)))
        return list(self.rates)

    def last_error(self) -> Any:
        return (1, "Success")

    def shutdown(self) -> Any:
        return None


def _tick(t: int, bid: str = "110.001", ask: str = "110.004") -> dict[str, Any]:
    return {"time": t, "time_msc": t * 1000, "bid": float(bid), "ask": float(ask), "flags": 6}


def test_plans_never_request_2024_or_later() -> None:
    for plan in probe.PHASES.values():
        for _, window in plan:
            probe.check_window(window)
            assert window.end <= probe.REQUEST_END_LIMIT
            assert window.start.tzinfo is UTC


def test_phase_sizes_match_the_design() -> None:
    assert len(probe.PHASE_USDJPY_TIME) == 2
    assert len(probe.PHASE_USDJPY_RANGE) == 2
    assert len(probe.PHASE_OTHERS) == 7 * 4
    assert len(probe.PHASE_OFFSET_EU_DST) == 1
    assert len(probe.PHASE_RANGE8) == 8 * 3
    assert len(set(probe.RANGE8_PAIRS)) == 8


def test_window_reaching_2024_is_refused_before_any_call(tmp_path: Path) -> None:
    api = FakeMt5()
    bad = probe.Window(
        "bad", datetime(2023, 12, 31, 23, tzinfo=UTC), datetime(2024, 1, 1, 1, tzinfo=UTC)
    )
    with pytest.raises(probe.RequestOutOfRangeError):
        probe.run_phase(api, [("USDJPY", bad)], tmp_path, retry=False)
    assert api.calls == []


def test_naive_window_is_refused() -> None:
    naive = probe.Window("n", datetime(2019, 3, 5), datetime(2019, 3, 5, 2))
    with pytest.raises(ValueError):
        probe.check_window(naive)


def test_only_allowed_api_calls_are_used() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "api"
    }
    assert called <= probe.ALLOWED_API_CALLS
    text = SOURCE.read_text(encoding="utf-8")
    for forbidden in ("order_send", "order_check", "positions_get", "account_info("):
        assert f"api.{forbidden}" not in text


def test_run_phase_records_counts_and_hashes(tmp_path: Path) -> None:
    base = int(datetime(2019, 3, 5, tzinfo=UTC).timestamp())
    api = FakeMt5(ticks=[_tick(base), _tick(base + 1)], rates=[{"time": base, "open": 1.0}])
    window = probe.PHASE_USDJPY_TIME[0][1]
    records = probe.run_phase(api, [("USDJPY", window)], tmp_path, retry=False)
    assert [r.kind for r in records] == ["ticks", "m1"]
    ticks, m1 = records
    assert ticks.returned_count == 2 and ticks.output_sha256 is not None
    assert ticks.returned_first_as_utc == "2019-03-05T00:00:00+00:00"
    assert ticks.tick_history == "ticks_returned"
    assert m1.returned_count == 1
    assert (tmp_path / "USDJPY_2019-03-winter_ticks.csv").exists()
    sent = [c for c in api.calls if c[0] == "copy_ticks_range"][0][1]
    assert sent[1] == window.start and sent[2] == window.end


def test_m1_without_ticks_is_not_tick_history(tmp_path: Path) -> None:
    base = int(datetime(2019, 3, 5, tzinfo=UTC).timestamp())
    api = FakeMt5(ticks=[], rates=[{"time": base, "open": 1.0}])
    records = probe.run_phase(
        api, [("USDJPY", probe.PHASE_USDJPY_TIME[0][1])], tmp_path, retry=True, retry_wait_seconds=0
    )
    assert records[0].tick_history == "no_ticks" and records[0].retried
    assert records[1].tick_history == "m1_only_not_tick_history"


def test_symbol_with_suffix_is_resolved_and_ambiguous_is_not() -> None:
    assert probe.resolve_symbol(FakeMt5(names=("USDCHF-x",)), "USDCHF")[0] == "USDCHF-x"
    assert probe.resolve_symbol(FakeMt5(names=("USDCHF.a", "USDCHF.b")), "USDCHF")[0] is None
    assert probe.resolve_symbol(FakeMt5(names=("USDCHF", "USDCHF.b")), "USDCHF")[0] == "USDCHF"


def test_unresolved_symbol_makes_no_data_request(tmp_path: Path) -> None:
    api = FakeMt5(names=())
    records = probe.run_phase(api, [("AUDUSD", probe.OTHER_WINDOWS[0])], tmp_path, retry=False)
    assert all(r.symbol is None and r.returned_count == 0 for r in records)
    assert not [c for c in api.calls if c[0].startswith("copy_")]


def test_server_time_conversion_follows_faq_2190() -> None:
    # 冬時間（GMT+2）: サーバー 2019.03.05 02:00 = UTC 00:00。夏時間（GMT+3）: 03:00 = UTC 00:00。
    assert probe.server_time_to_utc("2019.03.05", "02:00:00.000") == datetime(
        2019, 3, 5, tzinfo=UTC
    )
    assert probe.server_time_to_utc("2019.03.19", "03:00:00.000") == datetime(
        2019, 3, 19, tzinfo=UTC
    )
    # 月の区切り: 2017-03 のファイルの最初の時刻 2/28 17:00 は日本時間 3/1 00:00（UTC 2/28 15:00）。
    assert probe.server_time_to_utc("2017.02.28", "17:00:00.000") == datetime(
        2017, 2, 28, 15, tzinfo=UTC
    )


def test_compare_finds_the_shift_without_printing_prices(tmp_path: Path) -> None:
    csv_path = tmp_path / "oanda.csv"
    lines = ["<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>"]
    lines.append("2019.03.05\t02:00:01.500\t111.100\t111.103\t\t")
    lines.append("2019.03.05\t02:00:02.000\t111.101\t111.104\t\t")
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    start = datetime(2019, 3, 5, tzinfo=UTC)
    csv_ticks = list(
        probe.read_oanda_csv(csv_path, start - timedelta(hours=4), start + timedelta(hours=6))
    )
    assert len(csv_ticks) == 2
    ms = int(start.timestamp() * 1000)
    # API の時刻が UTC より 2 時間進んでいる場合（サーバー時刻のまま）を作る。
    api_ticks = [
        (ms + 1500 + 7_200_000, "111.1", "111.103"),
        (ms + 2000 + 7_200_000, "111.101", "111.104"),
    ]
    by_shift = {r["shift_hours"]: r["matched"] for r in probe.compare_ticks(api_ticks, csv_ticks)}
    assert by_shift[-2] == 2 and by_shift[0] == 0
    assert "111" not in json.dumps(probe.compare_ticks(api_ticks, csv_ticks))


def test_reading_csv_beyond_2023_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("h\n", encoding="utf-8")
    with pytest.raises(probe.RequestOutOfRangeError):
        list(
            probe.read_oanda_csv(
                path, datetime(2023, 12, 31, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
            )
        )


def test_rows_outside_the_window_are_not_saved(tmp_path: Path) -> None:
    # 端末が区間の外（2024 年以降を含む）の足を返しても、保存せず時刻と件数だけを記録する。
    window = probe.PHASE_USDJPY_TIME[0][1]
    base = int(window.start.timestamp())
    late = int(datetime(2026, 7, 10, 1, 30, tzinfo=UTC).timestamp())
    api = FakeMt5(
        ticks=[_tick(base - 60), _tick(base + 1)],
        rates=[{"time": late, "open": 157.123}],
    )
    records = probe.run_phase(api, [("USDJPY", window)], tmp_path, retry=True, retry_wait_seconds=0)
    ticks, m1 = records
    assert ticks.returned_count == 1 and ticks.out_of_window_count == 1
    assert ticks.out_of_window_min_utc == "2019-03-04T23:59:00+00:00"
    assert m1.returned_count == 0 and m1.out_of_window_count == 1 and m1.retried
    assert m1.out_of_window_min_utc == m1.out_of_window_max_utc == "2026-07-10T01:30:00+00:00"
    assert m1.output_file is None and m1.output_sha256 is None
    assert m1.tick_history == "ticks_returned"
    assert not (tmp_path / "USDJPY_2019-03-winter_m1.csv").exists()
    saved = (tmp_path / "USDJPY_2019-03-winter_ticks.csv").read_text(encoding="utf-8")
    assert str(base + 1) in saved and str(base - 60) not in saved
    assert "157.123" not in json.dumps([r.__dict__ for r in records])


def test_only_out_of_window_rows_count_as_nothing_returned(tmp_path: Path) -> None:
    window = probe.PHASE_USDJPY_TIME[1][1]
    late = int(datetime(2026, 7, 10, 1, 30, tzinfo=UTC).timestamp())
    api = FakeMt5(ticks=[], rates=[{"time": late, "open": 1.0}])
    records = probe.run_phase(api, [("USDJPY", window)], tmp_path, retry=False)
    assert [r.tick_history for r in records] == ["no_ticks", "no_ticks"]
    assert list(tmp_path.iterdir()) == []


def test_summary_only_saves_no_price_rows(tmp_path: Path) -> None:
    # range-8 の段は件数と時刻だけを記録し、価格の行を保存しない。
    window = probe.RANGE8_WINDOWS[0]
    base = int(window.start.timestamp())
    api = FakeMt5(ticks=[_tick(base)], rates=[{"time": base, "open": 123.456}])
    records = probe.run_phase(api, [("USDJPY", window)], tmp_path, retry=False, save_rows=False)
    assert [r.returned_count for r in records] == [1, 1]
    assert all(r.output_file is None and r.output_sha256 is None for r in records)
    assert list(tmp_path.iterdir()) == []
    assert "range-8" in probe.SUMMARY_ONLY_PHASES
    assert "offset-eu-dst" not in probe.SUMMARY_ONLY_PHASES


def test_symbol_meta_is_recorded(tmp_path: Path) -> None:
    class WithInfo(FakeMt5):
        def symbol_info(self, symbol: str) -> Any:
            return SimpleNamespace(chart_mode=0, point=0.001, digits=3)

    window = probe.PHASE_OFFSET_EU_DST[0][1]
    records = probe.run_phase(WithInfo(), [("USDJPY", window)], tmp_path, retry=False)
    assert all(
        (r.symbol_chart_mode, r.symbol_point, r.symbol_digits) == (0, 0.001, 3) for r in records
    )


def test_m1_phases_are_new_to_old_and_m1_only() -> None:
    assert [w.label for _, w in probe.PHASES["m1-EURUSD"]] == [
        "2023-12-05",
        "2019-03-05",
        "2017-01-10",
    ]
    assert [w.label for _, w in probe.PHASES["m1-USDJPY"]] == ["2017-01-10"]
    assert len(probe.M1_ONLY_PHASES) == 8
    assert probe.M1_ONLY_PHASES <= probe.SUMMARY_ONLY_PHASES


def test_low_disk_stops_before_the_next_request(tmp_path: Path) -> None:
    plan = probe.PHASES["m1-EURUSD"]
    base = int(plan[0][1].start.timestamp())
    api = FakeMt5(rates=[{"time": base, "open": 1.0}], names=("EURUSD.cl",))
    frees = iter([10 * 1024**3, 9 * 1024**3, 2 * 1024**3])
    records = probe.run_phase(
        api,
        plan,
        tmp_path,
        retry=False,
        save_rows=False,
        kinds=(probe.KIND_M1,),
        disk_free=lambda: next(frees),
    )
    assert [r.skipped_low_disk for r in records] == [False, True]
    assert records[0].disk_free_after_bytes == 9 * 1024**3
    assert len([c for c in api.calls if c[0] == "copy_rates_range"]) == 1
    assert not [c for c in api.calls if c[0] == "copy_ticks_range"]


def test_terminal_files_are_listed_by_name_size_and_time_only(tmp_path: Path) -> None:
    d = tmp_path / "bases" / "Srv" / "history" / "EURUSD.cl"
    d.mkdir(parents=True)
    (d / "2017.hcc").write_bytes(b"x" * 10)
    before = probe.list_symbol_files(tmp_path, "EURUSD.cl")
    assert list(before) == ["Srv/history/EURUSD.cl/2017.hcc"]
    assert before["Srv/history/EURUSD.cl/2017.hcc"][0] == 10
    (d / "2018.hcc").write_bytes(b"y" * 5)
    after = probe.list_symbol_files(tmp_path, "EURUSD.cl")
    changed = probe.files_changed(before, after)
    assert [c[0] for c in changed] == ["Srv/history/EURUSD.cl/2018.hcc"]
    assert changed[0][1] is None and changed[0][2] == 5
