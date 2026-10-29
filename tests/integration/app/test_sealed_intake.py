"""封印の取り込みの経路（取得 → 品質確認の許可の下の受入れ → 取引週の partition → 分類・承認）。

順 6 の S4a（D15 §2.2 の S4a の行の注記の 4 点、ADR-0036 第7項〜第9項、ADR-0038 第2項・第3項、
D03 §16.3、D11 §10 の受入条件 25）。**人工データだけ**を使う: 取得は偽の MT5 の API（人工の価格）で
`mt5_api_probe.run_sealed_fetch` を動かし、その出力（gzip の CSV と取得記録）を一時のリポジトリの
`data/raw/oanda_mt5_sealed/<取得日>/` に置く。規則のファイル（2024 年以降の表・カレンダー 版 2・
封印用の数え方の規則・許可の定義・窓の設定・銘柄の設定）は版管理のものを写す。実データ・2024 年
以降の実際のデータは読まない。

順 6 の S4b（D15 §2.2 の S4b の行）: 7 通貨（USDCHF・AUDJPY・EURUSD・EURJPY・GBPJPY・GBPUSD・
AUDUSD）にも同じ経路を当てる。偽の API は通貨ごとに銘柄仕様（`point`・`digits`）と桁の違う人工の
価格を返す。検査 3（銘柄の検査。D03 §16.3.1）と、8 通貨以外の拒否も確かめる。
"""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.adapters.mt5_csv_source import read_fetch_manifest, read_symbol_files
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.adapters.quality_access_log_store import QualityAccessLogFile
from odyssey_fx.marketdata.domain.access import HoldoutState
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation
from odyssey_fx.marketdata.domain.quality_access import (
    QualityAccessEnd,
    QualityAccessLogContents,
    QualityAccessResult,
    QualityAccessStart,
    completed_access_ids,
)
from tools.survey import mt5_api_probe as probe
from tools.survey import sealed_fetch_plan as planner

ROOT = Path(__file__).resolve().parents[3]
FETCH_DATE = "2026-11-14"
#: 人工の価格（取得の道具と受入れの画面出力・記録に現れないことを確かめる印）。
PRICE = "157.4321"
#: 通貨ごとの人工の銘柄仕様（`point`・`digits`）と人工の価格（S4b。桁の違いを含む。7 通貨は刻みの
#: 上の値）。銘柄仕様の値は銘柄の設定（`configs/symbols/`）の価格の刻みと同じにする（検査 3 が合格
#: する組）。
SPECS: dict[str, tuple[float, int, str]] = {
    "USDJPY": (0.001, 3, PRICE),
    "USDCHF": (0.00001, 5, "0.91357"),
    "AUDJPY": (0.001, 3, "97.531"),
    "EURUSD": (0.00001, 5, "1.08642"),
    "EURJPY": (0.001, 3, "163.208"),
    "GBPJPY": (0.001, 3, "191.864"),
    "GBPUSD": (0.00001, 5, "1.27531"),
    "AUDUSD": (0.00001, 5, "0.65791"),
}
SEVEN = ("USDCHF", "AUDJPY", "EURUSD", "EURJPY", "GBPJPY", "GBPUSD", "AUDUSD")

COPIED = (
    "docs/design/counting_rules/counting_rules_sealed_v1.yaml",
    "docs/design/time_rules/time_rule_table_sealed_v1.yaml",
    "docs/design/time_rules/approved_time_rule_tables_sealed.yaml",
    "docs/design/time_rules/time_rule_table_v1.yaml",
    "configs/calendars/fx_ny17_oanda_v2.yaml",
    "configs/calendars/fx_ny17_v2.yaml",
    "configs/calendars/timeframes_v2.yaml",
    "configs/datasources/oanda_mt5_m1_v1.yaml",
    "configs/acquisition/oanda_mt5_m1_sealed_window.yaml",
    "configs/quality_checks/oanda_mt5_sealed_accept.yaml",
    "configs/quality_checks/oanda_mt5_sealed_finalize.yaml",
    "tools/survey/oanda_holiday_exceptions_sealed_2026-10-29.json",
    *sorted(
        path.relative_to(ROOT).as_posix() for path in (ROOT / "configs/symbols").glob("*.yaml")
    ),
)


class FakeMt5:
    """要求の区間の取引時間の 1 分ごとに足を返す偽の API（人工の価格。通貨ごとの銘柄仕様）。"""

    COPY_TICKS_ALL = 1
    TIMEFRAME_M1 = 1

    def __init__(self, pair: str = "USDJPY") -> None:
        self.point, self.digits, self.price = SPECS[pair]

    def initialize(self) -> bool:
        return True

    def version(self) -> Any:
        return (500, 5000, "test")

    def terminal_info(self) -> Any:
        return SimpleNamespace(build=5000, name="t", company="c", maxbars=0, connected=True)

    def symbols_get(self, group: str) -> Any:
        return [SimpleNamespace(name=f"{group.strip('*')}.cl")]

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(chart_mode=0, point=self.point, digits=self.digits)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def copy_ticks_range(self, *args: Any) -> Any:
        raise AssertionError("封印の取得は tick を要求しない")

    def copy_rates_range(
        self, symbol: str, timeframe: int, date_from: datetime, date_to: datetime
    ) -> Any:
        # 両方冬（+2 時間）の週: 取引時間は API の時刻の月曜 00:00〜土曜 00:00。
        rows = []
        t = date_from
        price = float(self.price)
        step = 10 * self.point
        while t < date_to:
            if t.weekday() < 5:
                rows.append(
                    {
                        "time": int(t.timestamp()),
                        "open": price,
                        "high": price + step,
                        "low": price - step,
                        "close": price,
                        "tick_volume": 3,
                        "spread": 2,
                        "real_volume": 0,
                    }
                )
            t += timedelta(minutes=1)
        return rows

    def last_error(self) -> Any:
        return (1, "Success")

    def shutdown(self) -> Any:
        return None


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for rel in COPIED:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, repo / rel)
    (repo / "research").mkdir()
    (repo / "research" / "quality_access_log.jsonl").write_bytes(b"")
    return repo


def _fetch(repo: Path, tmp_path: Path, pairs: tuple[str, ...] = ("USDJPY",)) -> Path:
    """定期取得の初回（[2026-11-01T22:00Z, 2026-11-08T22:00Z)）を偽の API で `pairs` だけ取る。"""
    plan = planner.build_plan(
        ROOT, probe.SEALED_STAGE_PERIODIC, run_at=UtcTime.parse("2026-11-14T00:00:00Z")
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan.payload), encoding="utf-8")
    window = probe.read_sealed_window(repo / "configs/acquisition/oanda_mt5_m1_sealed_window.yaml")
    loaded, requests = probe.load_sealed_plan(
        plan_path, window, now=datetime(2026, 11, 14, tzinfo=UTC)
    )
    raw = repo / "data" / "raw" / "oanda_mt5_sealed" / FETCH_DATE
    for pair in pairs:
        result = probe.run_sealed_fetch(
            FakeMt5(pair),
            pair,
            raw,
            plan=loaded,
            plan_sha256=probe._sha256(plan_path),
            requests=requests,
            window=window,
            sleep=lambda s: None,
            now=lambda: datetime(2026, 11, 14, tzinfo=UTC),
        )
        assert result["ok"] == 1 and SPECS[pair][2] not in json.dumps(result)
    copy = repo / "docs" / "data" / "sealed" / f"{FETCH_DATE}_periodic-1" / "manifest.json"
    copy.parent.mkdir(parents=True)
    shutil.copy(raw / "manifest.json", copy)
    return copy


def _accept(repo: Path, copy: Path, variant: str = "standard", symbol: str = "USDJPY") -> int:
    return main(
        [
            "data",
            "accept-mt5",
            "--sealed",
            "--fetch-date",
            FETCH_DATE,
            "--symbol",
            symbol,
            "--variant",
            variant,
            "--manifest-copy",
            str(copy),
            "--out",
            str(repo / "data" / "snapshots"),
            "--repo-root",
            str(repo),
        ]
    )


def _log(repo: Path) -> QualityAccessLogContents:
    contents = QualityAccessLogFile(repo).read()
    assert isinstance(contents, QualityAccessLogContents)
    return contents


def _pending(repo: Path) -> list[Path]:
    root = repo / "data" / "snapshots" / "_pending"
    return sorted(path for path in root.iterdir() if path.is_dir()) if root.exists() else []


def _classify(repo: Path, provisional: str, tmp_path: Path) -> int:
    decisions = tmp_path / "decisions.yaml"
    decisions.write_text("schema_version: 2\ndecisions: []\n", encoding="utf-8")
    return main(
        [
            "data",
            "classify",
            "--pending",
            provisional,
            "--decisions",
            str(decisions),
            "--timeframes",
            str(repo / "configs/calendars/timeframes_v2.yaml"),
            "--out",
            str(repo / "data" / "snapshots"),
            "--repo-root",
            str(repo),
        ]
    )


def _approve(repo: Path, snapshot: str) -> int:
    return main(
        [
            "data",
            "approve",
            "--snapshot",
            snapshot,
            "--by",
            "test",
            "--out",
            str(repo / "data" / "snapshots"),
            "--repo-root",
            str(repo),
        ]
    )


# --- 経路 ----------------------------------------------------------------------------------


def test_the_usdjpy_path_runs_from_fetch_to_an_approved_sealed_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    capsys.readouterr()
    assert _accept(repo, copy) == 0
    printed = capsys.readouterr().out
    assert PRICE not in printed and "157.4" not in printed  # 画面には件数・時刻・ハッシュだけ
    (pending_dir,) = _pending(repo)
    store = ParquetSnapshotStore(root=repo / "data" / "snapshots")
    manifest = store.read_manifest(f"_pending/{pending_dir.name}")
    # 取引週の partition（前向きの封印の区分。週は 2026-11-01T22:00Z から）と初期状態。
    sealed = [record for record in manifest.partitions if record.partition_id.period_start]
    assert sealed and all(
        record.access_class.value == "FORWARD_HOLDOUT"
        and str(record.partition_id.period_start) == "2026-11-01T22:00:00Z"
        for record in sealed
    )
    assert len(sealed) == len(manifest.series) == 5  # 1 分足と上位足 4 つ
    assert {item.state for item in manifest.sealed_initial_states} == {HoldoutState.SEALED}
    # 閲覧の識別子と「許可を要したか」。閲覧の記録は開始と「完了」の終了の 2 行。
    assert manifest.quality_read is not None and manifest.quality_read.permit_required
    start, end = _log(repo).rows
    assert isinstance(start, QualityAccessStart) and isinstance(end, QualityAccessEnd)
    assert start.access_id == end.access_id == manifest.quality_read.access_id
    assert end.result is QualityAccessResult.COMPLETED
    assert start.tool.name == "oanda_mt5_sealed_accept"
    # 消費の記録は書かない。
    assert not (pending_dir / "access_log.jsonl").exists()

    assert _classify(repo, pending_dir.name, tmp_path) == 0
    (final,) = [p for p in (repo / "data" / "snapshots").iterdir() if p.name != "_pending"]
    assert _approve(repo, final.name) == 0
    approved = store.read_manifest(final.name)
    assert approved.is_approved
    assert approved.sealed_initial_states == manifest.sealed_initial_states
    assert approved.quality_read == manifest.quality_read
    # 受入れ・分類・承認の 3 回の閲覧がどれも「完了」。
    rows = _log(repo).rows
    assert len(rows) == 6 and len(completed_access_ids(rows)) == 3
    assert PRICE not in (final / "manifest.json").read_text(encoding="utf-8")
    assert PRICE not in (final / "integrity_report.json").read_text(encoding="utf-8")


def test_without_an_approved_definition_nothing_is_read_or_written(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    (repo / "configs/quality_checks/oanda_mt5_sealed_accept.yaml").unlink()
    assert _accept(repo, copy) != 0
    assert _pending(repo) == [] and _log(repo).rows == ()


def test_a_changed_rule_file_breaks_the_config_digest_and_nothing_is_read(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    timeframes = repo / "configs/calendars/timeframes_v2.yaml"
    timeframes.write_text(
        timeframes.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8"
    )
    assert _accept(repo, copy) != 0
    assert _pending(repo) == [] and _log(repo).rows == ()


def test_a_raw_file_that_is_not_the_recorded_one_is_never_parsed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    raw = repo / "data/raw/oanda_mt5_sealed" / FETCH_DATE
    (gz,) = list((raw / "USDJPY").glob("*.csv.gz"))
    gz.write_bytes(gzip.compress(b"time,open,high,low,close,tick_volume,spread,real_volume\n"))
    assert _accept(repo, copy) != 0
    assert "does not cover the raw file" in capsys.readouterr().err
    assert _pending(repo) == []
    start, end = _log(repo).rows
    assert isinstance(end, QualityAccessEnd) and end.result is QualityAccessResult.STOPPED


def _edit_record(repo: Path, copy: Path, change: Any) -> None:
    """取得記録（原本と写し）を人工に書き換える（価格は含まない記録だけ）。"""
    raw = repo / "data/raw/oanda_mt5_sealed" / FETCH_DATE / "manifest.json"
    payload = json.loads(raw.read_text(encoding="utf-8"))
    change(payload)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    raw.write_text(text, encoding="utf-8")
    copy.write_text(text, encoding="utf-8")


def test_a_currency_with_an_empty_request_is_not_accepted_this_time(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """0 本の要求が残った通貨のその回は受け入れない（D03 §16.5.8、PR #109 の仮置き S2b-13）。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)

    def empty(payload: dict[str, Any]) -> None:
        (entry,) = payload["files"].values()
        entry.update(status="empty", file=None, rows=0, first_bar_time=None, last_bar_time=None)

    _edit_record(repo, copy, empty)
    assert _accept(repo, copy) != 0
    assert "取得済みでない" in capsys.readouterr().err
    assert _pending(repo) == [] and _log(repo).rows == ()


def test_a_currency_with_an_unrequested_planned_request_is_not_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """計画の要求が記録に無い（途中で止まった）通貨は受け入れない（D03 §16.5.8。Codex 1 巡目）。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)

    def unrequested(payload: dict[str, Any]) -> None:
        payload["planned"]["USDJPY"].append("P-USDJPY-20261108T2200Z")

    _edit_record(repo, copy, unrequested)
    assert _accept(repo, copy) != 0
    assert "未要求" in capsys.readouterr().err
    assert _pending(repo) == [] and _log(repo).rows == ()


def test_a_periodic_record_reaching_before_the_acquisition_start_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """取得記録の段に合わない要求の区間（検査 2）は、読む前に止める。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)

    def earlier(payload: dict[str, Any]) -> None:
        (entry,) = payload["files"].values()
        entry["requested_from_api_time"] = "2026-10-26T00:00:00+00:00"

    _edit_record(repo, copy, earlier)
    assert _accept(repo, copy) != 0
    assert "取得の開始時点" in capsys.readouterr().err
    assert _pending(repo) == [] and _log(repo).rows == ()


def test_a_failure_while_parsing_the_sealed_prices_never_shows_a_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """価格の解析の失敗（正でない価格）の文に値を出さない（D03 §16.4）。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    raw = repo / "data/raw/oanda_mt5_sealed" / FETCH_DATE
    (gz,) = list((raw / "USDJPY").glob("*.csv.gz"))
    lines = gzip.decompress(gz.read_bytes()).decode("utf-8").splitlines()
    cells = lines[1].split(",")
    cells[1] = "-123.456"
    lines[1] = ",".join(cells)
    data = ("\n".join(lines) + "\n").encode("utf-8")
    packed = probe.gzip_bytes(data)
    gz.write_bytes(packed)

    def rehash(payload: dict[str, Any]) -> None:
        (entry,) = payload["files"].values()
        entry.update(sha256_gz=probe._sha256_bytes(packed), sha256_csv=probe._sha256_bytes(data))

    _edit_record(repo, copy, rehash)
    assert _accept(repo, copy) != 0
    err = capsys.readouterr().err
    assert "-123.456" not in err and "123.456" not in err and "withheld" in err
    assert _pending(repo) == []
    start, end = _log(repo).rows
    assert isinstance(end, QualityAccessEnd) and end.result is QualityAccessResult.STOPPED


def test_a_consumed_period_does_not_return_to_sealed_on_a_new_intake(tmp_path: Path) -> None:
    """例外 2（ADR-0038 第3項の 3）。

    既にある snapshot の同じ期間の partition が使用済みなら、新しい partition も使用済みで始める。
    """
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    assert _accept(repo, copy) == 0
    (pending_dir,) = _pending(repo)
    assert _classify(repo, pending_dir.name, tmp_path) == 0
    snapshots = repo / "data" / "snapshots"
    (final,) = [p for p in snapshots.iterdir() if p.name != "_pending"]
    store = ParquetSnapshotStore(root=snapshots)
    one = next(
        item.partition_id
        for item in store.read_manifest(final.name).sealed_initial_states
        if item.partition_id.series.timeframe.id == "1m"
    )
    (final / "access_log.jsonl").write_text(
        json.dumps(
            {
                "actor": "t",
                "kind": "CONSUMED",
                "partition": str(one),
                "purpose": "",
                "recorded_at": "x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert _accept(repo, copy, variant="candidate") == 0
    (again,) = _pending(repo)
    states = {
        str(item.partition_id): (item.state, item.reasons)
        for item in store.read_manifest(f"_pending/{again.name}").sealed_initial_states
    }
    assert states[str(one)] == (HoldoutState.CONSUMED, ("EXISTING_CONSUMED",))
    others = [value for key, value in states.items() if key != str(one)]
    assert others and all(value == (HoldoutState.SEALED, ()) for value in others)


def test_an_unknown_state_of_an_overlapping_partition_stops_before_reading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """重なる既にある partition の消費の記録が壊れていれば、原ファイルを読む前に止める。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    assert _accept(repo, copy) == 0
    (pending_dir,) = _pending(repo)
    assert _classify(repo, pending_dir.name, tmp_path) == 0
    snapshots = repo / "data" / "snapshots"
    (final,) = [p for p in snapshots.iterdir() if p.name != "_pending"]
    (final / "access_log.jsonl").write_text("{broken\n", encoding="utf-8")
    before = len(_log(repo).rows)
    assert _accept(repo, copy, variant="candidate") != 0
    assert "cannot be derived" in capsys.readouterr().err
    assert len(_log(repo).rows) == before  # 閲覧の記録の開始の行も書いていない（読んでいない）
    assert _pending(repo) == []


def test_classify_refuses_an_acceptance_without_a_completed_end_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    assert _accept(repo, copy) == 0
    (pending_dir,) = _pending(repo)
    # 終了の行を書けなかった受入れを作る（記録から終了の行を除く。人工の状況）。
    log = repo / "research" / "quality_access_log.jsonl"
    first = log.read_bytes().splitlines(keepends=True)[0]
    log.write_bytes(first)
    assert _classify(repo, pending_dir.name, tmp_path) != 0
    assert "「完了」の終了の行が無い" in capsys.readouterr().err
    assert [p.name for p in (repo / "data" / "snapshots").iterdir()] == ["_pending"]


# --- 7 通貨への当てはめ（順 6 の S4b。D15 §2.2 の S4b の行・§2.7、D03 §16.3）--------------------


def _printed_has_no_price(printed: str, pair: str) -> None:
    price = SPECS[pair][2]
    assert price not in printed and price[:-1] not in printed  # 画面には件数・時刻・ハッシュだけ


@pytest.mark.parametrize("pair", SEVEN)
def test_each_of_the_seven_currencies_is_accepted_under_the_permit(
    pair: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """7 通貨それぞれ、USDJPY と同じ経路・同じ規則で受け入れる（D03 §16.3.2 の S2b-4 (a)）。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path, (pair,))
    capsys.readouterr()
    assert _accept(repo, copy, symbol=pair) == 0
    printed = capsys.readouterr().out
    _printed_has_no_price(printed, pair)
    assert "銘柄の検査（D03 §16.3.1 の検査 3）: 合格" in printed
    assert f"configs/symbols/{pair}.yaml" in printed
    (pending_dir,) = _pending(repo)
    store = ParquetSnapshotStore(root=repo / "data" / "snapshots")
    manifest = store.read_manifest(f"_pending/{pending_dir.name}")
    # snapshot は取得の回 × 通貨 × 組で 1 つ（S2b-5 (a)）: 系列はすべてこの通貨。
    assert {str(series.series_id.symbol) for series in manifest.series} == {pair}
    sealed = [record for record in manifest.partitions if record.partition_id.period_start]
    assert len(sealed) == len(manifest.series) == 5
    assert all(
        record.access_class.value == "FORWARD_HOLDOUT"
        and str(record.partition_id.series.symbol) == pair
        for record in sealed
    )
    assert {item.state for item in manifest.sealed_initial_states} == {HoldoutState.SEALED}
    assert manifest.quality_read is not None and manifest.quality_read.permit_required
    start, end = _log(repo).rows
    assert isinstance(start, QualityAccessStart) and isinstance(end, QualityAccessEnd)
    assert end.result is QualityAccessResult.COMPLETED
    assert {str(getattr(target, "symbol", None)) for target in start.targets} == {pair}
    for name in ("manifest.json", "integrity_report.json"):
        _printed_has_no_price((pending_dir / name).read_text(encoding="utf-8"), pair)


def test_a_five_digit_currency_runs_to_an_approved_sealed_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """小数 5 桁の通貨でも、分類・承認まで品質確認の許可の下で通る（USDJPY は 3 桁）。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path, ("EURUSD",))
    assert _accept(repo, copy, symbol="EURUSD") == 0
    (pending_dir,) = _pending(repo)
    assert _classify(repo, pending_dir.name, tmp_path) == 0
    (final,) = [p for p in (repo / "data" / "snapshots").iterdir() if p.name != "_pending"]
    assert _approve(repo, final.name) == 0
    printed = capsys.readouterr().out
    _printed_has_no_price(printed, "EURUSD")
    approved = ParquetSnapshotStore(root=repo / "data" / "snapshots").read_manifest(final.name)
    assert approved.is_approved
    rows = _log(repo).rows
    assert len(rows) == 6 and len(completed_access_ids(rows)) == 3
    for name in ("manifest.json", "integrity_report.json"):
        _printed_has_no_price((final / name).read_text(encoding="utf-8"), "EURUSD")


def test_two_currencies_of_one_fetch_become_two_snapshots(tmp_path: Path) -> None:
    """同じ取得の回の 2 通貨は、別々の受入れ・別々の snapshot・別々の閲覧になる（S2b-5 (a)）。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path, ("USDJPY", "GBPUSD"))
    assert _accept(repo, copy, symbol="USDJPY") == 0
    assert _accept(repo, copy, symbol="GBPUSD") == 0
    pendings = _pending(repo)
    assert len(pendings) == 2
    store = ParquetSnapshotStore(root=repo / "data" / "snapshots")
    symbols = [
        {
            str(series.series_id.symbol)
            for series in store.read_manifest(f"_pending/{p.name}").series
        }
        for p in pendings
    ]
    assert sorted(map(sorted, symbols)) == [["GBPUSD"], ["USDJPY"]]
    assert len(completed_access_ids(_log(repo).rows)) == 2


def test_a_currency_outside_the_eight_is_refused_before_anything_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """8 通貨以外（銘柄の設定はある EURGBP）は、窓の設定と許可の定義の外として止める。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path)
    assert (repo / "configs/symbols/EURGBP.yaml").is_file()
    assert _accept(repo, copy, symbol="EURGBP") != 0
    assert "窓の設定の銘柄に無い" in capsys.readouterr().err
    assert _pending(repo) == [] and _log(repo).rows == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [("point", 0.001), ("digits", 3), ("chart_mode", 1), ("point", None), ("digits", None)],
)
def test_a_symbol_spec_that_differs_from_the_settings_is_refused_before_reading(
    field: str, value: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """検査 3（銘柄の検査）: 取得記録の point・digits・chart_mode が設定と違えば読まずに止める。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path, ("USDCHF",))

    def differ(payload: dict[str, Any]) -> None:
        payload["symbols"]["USDCHF"][field] = value

    _edit_record(repo, copy, differ)
    assert _accept(repo, copy, symbol="USDCHF") != 0
    err = capsys.readouterr().err
    assert "検査 3（銘柄の検査）の不合格" in err and field in err
    assert _pending(repo) == [] and _log(repo).rows == ()


def test_a_fetch_record_without_the_symbol_spec_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path, ("AUDJPY",))

    def drop(payload: dict[str, Any]) -> None:
        del payload["symbols"]["AUDJPY"]

    _edit_record(repo, copy, drop)
    assert _accept(repo, copy, symbol="AUDJPY") != 0
    assert "銘柄の記録（symbols）が無い" in capsys.readouterr().err
    assert _pending(repo) == [] and _log(repo).rows == ()


def test_a_changed_symbol_setting_fails_the_symbol_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """設定の側が取得の時の銘柄仕様と違っても止める（価格の刻みを 3 桁に変えた人工の設定）。"""
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path, ("GBPUSD",))
    setting = repo / "configs/symbols/GBPUSD.yaml"
    text = setting.read_text(encoding="utf-8")
    setting.write_text(
        text.replace('price_tick: "0.00001"', 'price_tick: "0.001"').replace(
            'pip_size: "0.0001"', 'pip_size: "0.01"'
        ),
        encoding="utf-8",
    )
    assert _accept(repo, copy, symbol="GBPUSD") != 0
    err = capsys.readouterr().err
    assert "point" in err and "digits" in err and "chart_mode" not in err
    assert _pending(repo) == [] and _log(repo).rows == ()


def test_a_missing_symbol_setting_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    copy = _fetch(repo, tmp_path, ("EURJPY",))
    (repo / "configs/symbols/EURJPY.yaml").unlink()
    assert _accept(repo, copy, symbol="EURJPY") != 0
    assert "銘柄の設定 configs/symbols/EURJPY.yaml が無い" in capsys.readouterr().err
    assert _pending(repo) == [] and _log(repo).rows == ()


# --- 関所の外から原ファイルを読む経路が無いこと（受入条件 25）----------------------------------


def test_no_reader_parses_a_sealed_raw_file_without_a_permit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fetch(repo, tmp_path)
    raw = repo / "data/raw/oanda_mt5_sealed" / FETCH_DATE
    entries = read_fetch_manifest(raw / "manifest.json")
    columns = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")
    with pytest.raises(HoldoutAccessViolation, match="sealed raw file"):
        read_symbol_files(raw, entries, columns)


def test_the_research_acceptance_refuses_the_sealed_raw_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    _fetch(repo, tmp_path)
    shutil.copy(
        ROOT / "docs/design/counting_rules/counting_rules_v1.yaml",
        repo / "docs/design/counting_rules/",
    )
    shutil.copy(ROOT / "configs/calendars/fx_ny17_oanda_v1.yaml", repo / "configs/calendars/")
    shutil.copy(
        ROOT / "tools/survey/oanda_holiday_exceptions_2026-10-16.json", repo / "tools/survey/"
    )
    shutil.copy(
        ROOT / "docs/design/time_rules/approved_time_rule_tables.yaml",
        repo / "docs/design/time_rules/",
    )
    research_copy = repo / f"docs/data/oanda_mt5_full_fetch_{FETCH_DATE}/manifest.json"
    research_copy.parent.mkdir(parents=True)
    shutil.copy(repo / "data/raw/oanda_mt5_sealed" / FETCH_DATE / "manifest.json", research_copy)
    assert (
        main(
            [
                "data",
                "accept-mt5",
                "--fetch-date",
                FETCH_DATE,
                "--symbol",
                "USDJPY",
                "--raw-root",
                str(repo / "data/raw/oanda_mt5_sealed"),
                "--out",
                str(repo / "data" / "snapshots"),
                "--repo-root",
                str(repo),
            ]
        )
        != 0
    )
    assert "sealed raw file" in capsys.readouterr().err
    assert _pending(repo) == [] and _log(repo).rows == ()
