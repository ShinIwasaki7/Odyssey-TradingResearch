"""封印の取得の道具（一括取得・定期取得の 2 段。ADR-0037 第4項、D03 §16.5、D15 §2.2 の S4a）。

計画は版管理の窓の設定と 2024 年以降の時刻の規則の表から作る（データは読まない）。取得は偽の MT5 の
API（人工の値）で動かし、AWS・端末にはつながない。確かめること: 窓は設定からだけ読む、2024-01-01
より前・窓の外・8 通貨以外の要求は何も要求せずに止まる、一括取得は 272 要求（上限 300）、定期取得の
窓は途切れも重なりも無い、一括取得の各通貨の最後の要求の終わりは週末の休場の中なら金曜の閉場に切り詰める
（D15 §0.5.12）、出力（標準出力・記録）に価格が無い。
"""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from odyssey_fx.app.config.sealed_window import SEALED_WINDOW_PATH, load_sealed_window
from odyssey_fx.app.mt5 import SEALED_COUNTING_RULES, load_mt5_rules
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.access import is_trading_week_start
from tools.survey import mt5_api_probe as probe
from tools.survey import sealed_fetch_plan as planner

ROOT = Path(__file__).resolve().parents[2]
T = UtcTime.parse
NY = ZoneInfo("America/New_York")
RUN_AT = T("2026-12-01T00:00:00Z")


@pytest.fixture(scope="module")
def bulk() -> planner.SealedPlan:
    return planner.build_plan(ROOT, probe.SEALED_STAGE_BULK, run_at=RUN_AT)


@pytest.fixture(scope="module")
def first() -> planner.SealedPlan:
    return planner.build_plan(ROOT, probe.SEALED_STAGE_PERIODIC, run_at=T("2026-11-14T00:00:00Z"))


# --- 計画（Mac）----------------------------------------------------------------------------


def test_the_bulk_plan_is_272_monthly_requests_inside_the_window(bulk: planner.SealedPlan) -> None:
    assert len(bulk.requests) == 272 and bulk.payload["max_requests"] == 300
    assert (bulk.window_start, bulk.window_end) == (
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2026, 11, 1, 22, tzinfo=UTC),
    )
    assert bulk.requests[0].pair == "USDJPY"
    usdjpy = [r for r in bulk.requests if r.pair == "USDJPY"]
    assert len(usdjpy) == 34
    # 最初の月は API の時刻 2024-01-01 02:00（= 2024-01-01T00:00Z。両方冬 +2）から。
    assert usdjpy[0].api_from == datetime(2024, 1, 1, 2, tzinfo=UTC)
    assert usdjpy[0].utc_from == datetime(2024, 1, 1, tzinfo=UTC)
    # 最後の月は API の時刻の 2026-10（2026-11 の切れ端は週末の休場だけなので要求しない）。
    # その終わりは週末の休場の中（API の時刻の 10 月末 = 2026-10-31T21:00Z、土曜）なので、金曜の
    # 閉場 2026-10-30T21:00Z（API の時刻 2026-10-31 00:00。米国式 +3）に切り詰める（D15 §0.5.12）。
    assert usdjpy[-1].rid == "S-USDJPY-2026-10"
    assert usdjpy[-1].api_to == datetime(2026, 10, 31, tzinfo=UTC)
    assert usdjpy[-1].utc_to == datetime(2026, 10, 30, 21, tzinfo=UTC)
    assert usdjpy[-1].offset_to_hours == 3
    for a, b in zip(usdjpy, usdjpy[1:], strict=False):
        assert a.api_to == b.api_from  # API の時刻で途切れも重なりも無い
    for r in bulk.requests:
        assert datetime(2024, 1, 1, tzinfo=UTC) <= r.utc_from < r.utc_to
        assert r.utc_to <= datetime(2026, 11, 1, 22, tzinfo=UTC)


def test_the_bulk_plan_can_start_at_the_friday_close_before_the_last_request_end() -> None:
    """(i) 金曜の閉場ちょうど（2026-10-30T21:00Z = 10/31 06:00 JST）に計画が作れる。"""
    plan = planner.build_plan(ROOT, probe.SEALED_STAGE_BULK, run_at=T("2026-10-30T21:00:00Z"))
    assert len(plan.requests) == 272 and plan.payload["max_requests"] == 300
    last = {r.pair: r for r in plan.requests}  # 通貨ごとの最後の要求
    assert len(last) == 8
    for r in last.values():
        assert r.rid.endswith("-2026-10")
        assert r.utc_to == datetime(2026, 10, 30, 21, tzinfo=UTC)
        assert r.api_to == datetime(2026, 10, 31, tzinfo=UTC)
    assert max(r.utc_to for r in plan.requests) == datetime(2026, 10, 30, 21, tzinfo=UTC)
    # 窓の記録は設定の値のまま（窓の終わりは取得の開始時点）。
    assert plan.payload["window"]["utc_to"] == "2026-11-01T22:00:00Z"


def test_the_bulk_plan_is_refused_before_the_friday_close() -> None:
    """(ii) 1 分前は要求の時点より後を含むので作らない（ADR-0037 第4項の 3 はそのまま）。"""
    with pytest.raises(probe.SealedWindowError, match="要求の時点より後"):
        planner.build_plan(ROOT, probe.SEALED_STAGE_BULK, run_at=T("2026-10-30T20:59:00Z"))


def test_month_ends_other_than_the_last_are_not_clipped(bulk: planner.SealedPlan) -> None:
    """(iii) 平日の月末も、途中の月の週末の月末も変わらない（切り詰めは各通貨の最後の要求だけ）。"""
    by_rid = {r.rid: r for r in bulk.requests}
    # 平日の月末（2026-09-30（水）。API の時刻の 10-01 00:00 = 2026-09-30T21:00Z）。
    september = by_rid["S-USDJPY-2026-09"]
    assert september.api_to == datetime(2026, 10, 1, tzinfo=UTC)
    assert september.utc_to == datetime(2026, 9, 30, 21, tzinfo=UTC)
    # 途中の月の週末の月末（2026-01-31（土）22:00Z。両方冬 +2）も API の時刻の月の境目のまま。
    january = by_rid["S-USDJPY-2026-01"]
    assert january.api_to == datetime(2026, 2, 1, tzinfo=UTC)
    assert january.utc_to == datetime(2026, 1, 31, 22, tzinfo=UTC)
    # 要求の始まりは変えない（API の時刻の 2026-08-01（土）00:00 = 2026-07-31T21:00Z。金曜の閉場）。
    august = by_rid["S-USDJPY-2026-08"]
    assert august.api_from == datetime(2026, 8, 1, tzinfo=UTC)
    assert august.utc_from == datetime(2026, 7, 31, 21, tzinfo=UTC)


def test_a_last_request_ending_at_the_sunday_open_is_not_clipped() -> None:
    """窓の終わりが日曜の開場で、最後の要求がそこで終わるなら変えない（休場の中ではない）。"""
    window = replace(
        load_sealed_window(ROOT / SEALED_WINDOW_PATH),
        acquisition_start=T("2026-10-04T21:00:00Z"),
    )
    table = load_mt5_rules(ROOT, SEALED_COUNTING_RULES).table
    requests = planner.bulk_requests(window, table)
    last = [r for r in requests if r.pair == "USDJPY"][-1]
    assert last.rid == "S-USDJPY-2026-10"
    assert (last.api_to, last.utc_to) == (
        datetime(2026, 10, 5, tzinfo=UTC),
        datetime(2026, 10, 4, 21, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("utc_to", "expected"),
    [
        # 夏時間の土曜 → 金曜 17:00 EDT = 21:00Z。
        (datetime(2026, 10, 31, 21, tzinfo=UTC), datetime(2026, 10, 30, 21, tzinfo=UTC)),
        # 冬時間の土曜 → 金曜 17:00 EST = 22:00Z。
        (datetime(2026, 1, 31, 22, tzinfo=UTC), datetime(2026, 1, 30, 22, tzinfo=UTC)),
        # 日曜の開場の 1 分前 → 金曜の閉場。
        (datetime(2026, 11, 1, 21, 59, tzinfo=UTC), datetime(2026, 10, 30, 21, tzinfo=UTC)),
        # 平日・金曜の閉場ちょうど・日曜の開場ちょうどはそのまま。
        (datetime(2026, 9, 30, 21, tzinfo=UTC), datetime(2026, 9, 30, 21, tzinfo=UTC)),
        (datetime(2026, 10, 30, 21, tzinfo=UTC), datetime(2026, 10, 30, 21, tzinfo=UTC)),
        (datetime(2026, 11, 1, 22, tzinfo=UTC), datetime(2026, 11, 1, 22, tzinfo=UTC)),
    ],
)
def test_the_friday_close_clip(utc_to: datetime, expected: datetime) -> None:
    assert planner.clip_to_friday_close(utc_to) == expected


def test_the_first_periodic_plan_is_one_week_from_the_acquisition_start(
    first: planner.SealedPlan,
) -> None:
    assert (first.window_start, first.window_end) == (
        datetime(2026, 11, 1, 22, tzinfo=UTC),
        datetime(2026, 11, 8, 22, tzinfo=UTC),
    )
    assert len(first.requests) == 8 and first.payload["max_requests"] == 9
    assert {r.api_from for r in first.requests} == {datetime(2026, 11, 2, tzinfo=UTC)}


def test_periodic_windows_chain_without_gaps_or_overlaps_to_month_ends() -> None:
    window = load_sealed_window(ROOT / SEALED_WINDOW_PATH)
    start, end = planner.periodic_window(window, after=None, run_at=T("2026-11-14T00:00:00Z"))
    seen = [(start, end)]
    run = datetime(2026, 12, 5, tzinfo=UTC)
    while run.year < 2028:
        lo, hi = planner.periodic_window(window, after=seen[-1][1], run_at=UtcTime(run))
        assert lo == seen[-1][1]  # 前の回の終わりから（途切れも重なりも無い）
        assert is_trading_week_start(hi)
        month_start = run.replace(day=1)
        # 前の月の終わり以前の最後の取引週の境目（次の境目は月の始まりより後）。
        assert hi.value <= month_start < hi.value + timedelta(days=7, hours=1)
        assert hi.value.astimezone(NY).strftime("%a %H:%M") == "Sun 17:00"
        seen.append((lo, hi))
        run = (run.replace(day=1) + timedelta(days=32)).replace(day=5)
    assert seen[1] == (T("2026-11-08T22:00:00Z"), T("2026-11-29T22:00:00Z"))
    assert seen[2] == (T("2026-11-29T22:00:00Z"), T("2026-12-27T22:00:00Z"))


def test_a_periodic_window_that_is_not_over_or_is_empty_is_not_planned() -> None:
    window = load_sealed_window(ROOT / SEALED_WINDOW_PATH)
    with pytest.raises(probe.SealedWindowError, match="より後"):
        planner.periodic_window(window, after=None, run_at=T("2026-11-05T00:00:00Z"))
    with pytest.raises(probe.SealedWindowError, match="取る期間が無い"):
        planner.periodic_window(
            window, after=T("2026-11-29T22:00:00Z"), run_at=T("2026-12-02T00:00:00Z")
        )
    with pytest.raises(probe.SealedWindowError, match="取引週の境目"):
        planner.periodic_window(
            window, after=T("2026-11-01T00:00:00Z"), run_at=T("2026-12-02T00:00:00Z")
        )


def test_the_weekend_only_rule() -> None:
    sat = datetime(2026, 10, 31, 22, tzinfo=UTC)
    assert planner.weekend_only(sat, datetime(2026, 11, 1, 22, tzinfo=UTC))
    # 金曜の閉場の前を含めば週末だけではない。
    assert not planner.weekend_only(datetime(2026, 10, 30, 20, tzinfo=UTC), sat)


# --- 窓の設定の読み直しと要求の検査（Windows の道具）----------------------------------------


def test_the_tool_reads_the_same_window_as_the_platform() -> None:
    tool = probe.read_sealed_window(ROOT / SEALED_WINDOW_PATH)
    platform = load_sealed_window(ROOT / SEALED_WINDOW_PATH)
    assert tool.allowed_start == platform.allowed_start.value
    assert platform.acquisition_start is not None
    assert tool.acquisition_start == platform.acquisition_start.value
    assert set(tool.symbols) == {str(s) for s in platform.symbols}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("symbols: [USDJPY,", "symbols: [USDCAD,"),
        ("timeframe: M1", "timeframe: M5"),
        ("price_basis: bid", "price_basis: ask"),
        ("datasource: oanda_mt5_m1", "datasource: histdata"),
        ('allowed_start: "2024-01-01T00:00:00Z"', 'allowed_start: "2024-01-01T00:00:00"'),
    ],
)
def test_the_tool_refuses_a_window_file_of_another_shape(
    tmp_path: Path, old: str, new: str
) -> None:
    text = (ROOT / SEALED_WINDOW_PATH).read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "window.yaml"
    path.write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(probe.SealedWindowError):
        probe.read_sealed_window(path)


def _window(**kw: Any) -> probe.SealedWindow:
    base = probe.read_sealed_window(ROOT / SEALED_WINDOW_PATH)
    return replace(base, **kw)


def _req(stage: str = probe.SEALED_STAGE_BULK, **kw: Any) -> probe.SealedRequest:
    base = probe.SealedRequest(
        rid="S-USDJPY-2024-01",
        pair="USDJPY",
        stage=stage,
        api_from=datetime(2024, 1, 1, 2, tzinfo=UTC),
        api_to=datetime(2024, 2, 1, tzinfo=UTC),
        utc_from=datetime(2024, 1, 1, tzinfo=UTC),
        utc_to=datetime(2024, 1, 31, 22, tzinfo=UTC),
        offset_from_hours=2,
        offset_to_hours=2,
        file="USDJPY/USDJPY_2024-01_m1.csv.gz",
    )
    return replace(base, **kw)


NOW = datetime(2026, 12, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("request_change", "window_change", "stage", "message"),
    [
        # 2024-01-01 より前（API の時刻 2024-01-01 00:00 = 2023-12-31T22:00Z）。
        (
            {
                "api_from": datetime(2024, 1, 1, tzinfo=UTC),
                "utc_from": datetime(2023, 12, 31, 22, tzinfo=UTC),
            },
            {},
            probe.SEALED_STAGE_BULK,
            "より前",
        ),
        # 一括取得が取得の開始時点を越える。
        (
            {
                "api_to": datetime(2026, 11, 9, tzinfo=UTC),
                "utc_to": datetime(2026, 11, 8, 22, tzinfo=UTC),
            },
            {},
            probe.SEALED_STAGE_BULK,
            "取得の開始時点より前だけ",
        ),
        # 定期取得が取得の開始時点より前から始まる。
        ({}, {}, probe.SEALED_STAGE_PERIODIC, "取得の開始時点から"),
        # 8 通貨の外（窓の設定の銘柄に無い）。
        ({"pair": "USDCAD"}, {}, probe.SEALED_STAGE_BULK, "銘柄に無い"),
        # 表の値（2・3 時間）でない差。
        (
            {"offset_from_hours": 4, "api_from": datetime(2024, 1, 1, 4, tzinfo=UTC)},
            {},
            probe.SEALED_STAGE_BULK,
            "差",
        ),
        # 取得の開始時点が空。
        ({}, {"acquisition_start": None}, probe.SEALED_STAGE_BULK, "空"),
    ],
)
def test_requests_outside_the_window_are_refused(
    request_change: dict[str, Any], window_change: dict[str, Any], stage: str, message: str
) -> None:
    request = _req(stage=stage, **request_change)
    with pytest.raises(probe.SealedWindowError, match=message):
        probe.check_sealed_request(request, _window(**window_change), stage, NOW)


def test_a_request_reaching_past_now_is_refused() -> None:
    request = _req(
        stage=probe.SEALED_STAGE_PERIODIC,
        api_from=datetime(2026, 11, 2, tzinfo=UTC),
        api_to=datetime(2026, 11, 9, tzinfo=UTC),
        utc_from=datetime(2026, 11, 1, 22, tzinfo=UTC),
        utc_to=datetime(2026, 11, 8, 22, tzinfo=UTC),
    )
    probe.check_sealed_request(request, _window(), probe.SEALED_STAGE_PERIODIC, NOW)
    with pytest.raises(probe.SealedWindowError, match="要求の時点"):
        probe.check_sealed_request(
            request, _window(), probe.SEALED_STAGE_PERIODIC, datetime(2026, 11, 5, tzinfo=UTC)
        )


# --- 取得（偽の API）------------------------------------------------------------------------


class FakeMt5:
    """区間の中の足 3 本と区間の外の足 1 本を返す偽の API（価格は人工の値）。"""

    COPY_TICKS_ALL = 1
    TIMEFRAME_M1 = 1

    def __init__(self) -> None:
        self.initialized = 0
        self.rate_calls: list[tuple[str, datetime, datetime]] = []

    def initialize(self) -> bool:
        self.initialized += 1
        return True

    def version(self) -> Any:
        return (500, 5000, "test")

    def terminal_info(self) -> Any:
        return SimpleNamespace(build=5000, name="t", company="c", maxbars=0, connected=True)

    def symbols_get(self, group: str) -> Any:
        key = group.strip("*")
        return [SimpleNamespace(name=f"{p}.cl") for p in probe.SEALED_PAIRS if key in p]

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(chart_mode=0, point=0.001, digits=3)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def copy_ticks_range(self, *args: Any) -> Any:
        raise AssertionError("封印の取得は tick を要求しない")

    def copy_rates_range(
        self, symbol: str, timeframe: int, date_from: datetime, date_to: datetime
    ) -> Any:
        self.rate_calls.append((symbol, date_from, date_to))
        s, e = int(date_from.timestamp()), int(date_to.timestamp())
        bars = [
            {
                "time": t,
                "open": 123.456,
                "high": 123.789,
                "low": 123.123,
                "close": 123.654,
                "tick_volume": 7,
                "spread": 3,
                "real_volume": 0,
            }
            for t in (s, s + 60, s + 120, e)
        ]
        return bars

    def last_error(self) -> Any:
        return (1, "Success")

    def shutdown(self) -> Any:
        return None


def _write_plan(tmp_path: Path, plan: planner.SealedPlan) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan.payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_periodic_fetch_writes_prices_only_into_the_files_and_verifies(
    tmp_path: Path, first: planner.SealedPlan, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = _write_plan(tmp_path, first)
    window = probe.read_sealed_window(ROOT / SEALED_WINDOW_PATH)
    plan, requests = probe.load_sealed_plan(plan_path, window, now=NOW)
    api = FakeMt5()
    out = tmp_path / "out"
    result = probe.run_sealed_fetch(
        api,
        "USDJPY",
        out,
        plan=plan,
        plan_sha256=probe._sha256(plan_path),
        requests=requests,
        window=window,
        sleep=lambda s: None,
        now=lambda: NOW,
    )
    assert result["ok"] == 1 and result["stop"] is None and result["requests_sent_total"] == 1
    assert api.rate_calls == [
        ("USDJPY.cl", datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC))
    ]
    assert probe.verify_sealed_dir(out, plan_path, ["USDJPY"]) == []
    # 価格はファイルの中だけ。取得の記録・結果の行には無い。
    manifest_text = (out / probe.FULL_MANIFEST).read_text(encoding="utf-8")
    log_text = (out / probe.FULL_FETCH_LOG).read_text(encoding="utf-8")
    shown = json.dumps(result) + capsys.readouterr().out
    for text in (manifest_text, log_text, shown):
        assert "123.4" not in text and "123.7" not in text and "123.6" not in text
    assert "disk_free" not in log_text  # 空きの差から端末の履歴の大きさが分かるので記録しない
    entry = json.loads(manifest_text)["files"]["P-USDJPY-20261101T2200Z"]
    data = gzip.decompress((out / entry["file"]).read_bytes()).decode()
    assert len(data.splitlines()) == 1 + 3  # 区間の終わりちょうどの足は保存しない


def test_an_existing_raw_file_is_never_overwritten(
    tmp_path: Path, first: planner.SealedPlan
) -> None:
    """記録と合わない既存の原ファイルは上書きせず、要求も出さずに止まる（ADR-0013）。"""
    plan_path = _write_plan(tmp_path, first)
    window = probe.read_sealed_window(ROOT / SEALED_WINDOW_PATH)
    plan, requests = probe.load_sealed_plan(plan_path, window, now=NOW)
    out = tmp_path / "out"
    (usdjpy,) = [r for r in requests if r.pair == "USDJPY"]
    target = out / usdjpy.file
    target.parent.mkdir(parents=True)
    target.write_bytes(b"left by a stopped run")
    api = FakeMt5()
    result = probe.run_sealed_fetch(
        api,
        "USDJPY",
        out,
        plan=plan,
        plan_sha256=probe._sha256(plan_path),
        requests=requests,
        window=window,
        sleep=lambda s: None,
        now=lambda: NOW,
    )
    assert result["stop"] == "existing_file" and api.rate_calls == []
    assert target.read_bytes() == b"left by a stopped run"
    with pytest.raises(probe.SealedWindowError, match="上書きしない"):
        probe._write_new(target, b"new")
    assert target.read_bytes() == b"left by a stopped run"
    manifest = json.loads((out / probe.FULL_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["planned"]["USDJPY"] == [usdjpy.rid]


def test_a_plan_that_breaks_the_window_requests_nothing(
    tmp_path: Path, first: planner.SealedPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads(json.dumps(first.payload))
    # 窓の外（取得の開始時点より前）へずらした要求を 1 つ混ぜる。
    bad = payload["requests"][0]
    bad["utc_from"] = "2026-10-25T21:00:00+00:00"
    bad["api_from"] = "2026-10-26T00:00:00+00:00"
    bad["offset_from_hours"] = 3
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    api = FakeMt5()
    monkeypatch.setattr(probe, "load_mt5", lambda: api)
    with pytest.raises(probe.SealedWindowError):
        probe.main(
            [
                "sealedfetch",
                "--plan",
                str(plan_path),
                "--window",
                str(ROOT / SEALED_WINDOW_PATH),
                "--out-dir",
                str(tmp_path / "out"),
                "--pairs",
                "USDJPY",
            ]
        )
    assert api.initialized == 0 and api.rate_calls == []


def test_a_plan_made_from_another_window_file_requests_nothing(
    tmp_path: Path, first: planner.SealedPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = _write_plan(tmp_path, first)
    other = tmp_path / "window.yaml"
    other.write_text(
        (ROOT / SEALED_WINDOW_PATH).read_text(encoding="utf-8") + "# changed\n", encoding="utf-8"
    )
    api = FakeMt5()
    monkeypatch.setattr(probe, "load_mt5", lambda: api)
    with pytest.raises(probe.SealedWindowError, match="窓の設定"):
        probe.main(
            [
                "sealedfetch",
                "--plan",
                str(plan_path),
                "--window",
                str(other),
                "--out-dir",
                str(tmp_path / "out"),
                "--pairs",
                "USDJPY",
            ]
        )
    assert api.initialized == 0 and api.rate_calls == []


def test_the_request_limit_is_the_plan_limit(tmp_path: Path, first: planner.SealedPlan) -> None:
    plan_path = _write_plan(tmp_path, first)
    window = probe.read_sealed_window(ROOT / SEALED_WINDOW_PATH)
    plan, requests = probe.load_sealed_plan(plan_path, window, now=NOW)
    with pytest.raises(probe.SealedWindowError, match="まで"):
        probe.run_sealed_fetch(
            FakeMt5(),
            "USDJPY",
            tmp_path / "out",
            plan=plan,
            plan_sha256="x",
            requests=requests,
            window=window,
            max_total=10,
            now=lambda: NOW,
        )
    assert probe.sealed_max_requests(272) == 300 and probe.sealed_max_requests(8) == 9


def test_the_sealed_stage_never_calls_trading_or_account_functions() -> None:
    source = Path(probe.__file__).read_text(encoding="utf-8")
    section = source[source.index("# --- 封印の取得") : source.index("def main(")]
    for name in ("order_send", "positions_get", "account_info", "copy_ticks_range", "login("):
        assert name not in section
