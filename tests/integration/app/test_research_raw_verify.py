"""研究期間の原ファイルの確認の読み取りと、受入れの前の一覧との照合（D03 §17.4・§17.6・§17.7）。

順 7 の A2。**人工データだけ**を使う: 一時のリポジトリに、規則のファイル（時刻の規則の表 版 1・
承認済みの一覧・列対応の宣言・数え方の規則 版 1 と、それが指すカレンダー・休場の定義・時間足の
定義）を版管理のものから写し、人工の取得記録と人工の価格の gzip の CSV を
`data/raw/oanda_mt5/<取得日>/` に置く。許可の定義（研究期間用）は人工のものを、今の固定の
ファイルから計算した設定のダイジェストで書く（実際の定義は D17 の AP で人間が承認する）。
実データ・封印データは読まない。

確かめること（D03 §17.6 の 6、D17 の G2 (8)）:

- (a) 研究期間の置き場と研究履歴の区分を覆う定義で照合が通り、確認の読み取りが合否を出し、閲覧の
  記録に開始と「完了」の終了の行が残る。
- (b) 定義が覆わない要求（別の通貨・定義が無い・読む前の照合に外れる）は読み取りを始めない
  （開始の行も書かない）。
- (d) 2024 年以降の行を含む人工の原ファイルで、`FAIL` と「途中で停止」の終了の行になり、価格を
  出さず、残りのファイルを読まない。
- 受入れ（`data accept-mt5`）は、一覧が無い・載っていない・ハッシュが違う・取得記録と食い違う一覧の
  ときは何も読まずに止まり、一覧に載りハッシュが一致すれば受け入れて照合の行を出す（D03 §17.7）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.app.research_raw_verify import research_config_digest
from odyssey_fx.marketdata.adapters.quality_access_log_store import QualityAccessLogFile
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.quality_access import (
    QualityAccessEnd,
    QualityAccessLogContents,
    QualityAccessResult,
    QualityAccessStart,
    QualityCheckKind,
)

ROOT = Path(__file__).resolve().parents[3]
FETCH_DATE = "2030-01-02"
#: 人工の価格（画面出力・記録・結果の JSON に現れないことを確かめる印）。
PRICE = "1.13579"
COPIED = (
    "docs/design/time_rules/time_rule_table_v1.yaml",
    "docs/design/time_rules/approved_time_rule_tables.yaml",
    "configs/datasources/oanda_mt5_m1_v1.yaml",
    "docs/design/counting_rules/counting_rules_v1.yaml",
    "configs/calendars/fx_ny17_oanda_v1.yaml",
    "configs/calendars/fx_ny17_v2.yaml",
    "configs/calendars/timeframes_v2.yaml",
    "tools/survey/oanda_holiday_exceptions_2026-10-16.json",
)
HEADER = "time,open,high,low,close,tick_volume,spread,real_volume"
DEFINITION = """schema_version: 1
tool: {{name: oanda_mt5_research_raw_verify, version: "1"}}
config_digest: {digest}
datasource: oanda_mt5_m1
check_kind: RAW_FILE_VERIFICATION
classes: [RESEARCH_HISTORY]
period: {{start: "2016-01-01T00:00:00Z", end: "2024-01-01T00:00:00Z"}}
targets: {{kind: RAW_FILE, symbols: [{symbols}], timeframes: [1m]}}
output_kinds: [COUNTS, HASHES, PASS_FAIL]
"""


def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def _month_rows(year: int, month: int, *, extra: tuple[int, ...] = ()) -> list[int]:
    """その月の最初の月曜の API 12:00 から 30 分の行（API の時刻）。`extra` を真ん中に足す。"""
    day = datetime(year, month, 1, 12, 0, tzinfo=UTC)
    while day.weekday() != 0:
        day += timedelta(days=1)
    times = [int((day + timedelta(minutes=step)).timestamp()) for step in range(30)]
    return times[:15] + list(extra) + times[15:]


def _gz(times: list[int]) -> bytes:
    lines = [HEADER] + [f"{t},{PRICE},1.13600,1.13500,{PRICE},3,2,0" for t in times]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"), mtime=0)


def _csv_bytes(data: bytes) -> bytes:
    try:
        return gzip.decompress(data)
    except (OSError, EOFError):
        return data  # 壊れた人工の gzip（展開できない）


def _repo(
    tmp_path: Path,
    months: tuple[tuple[int, int], ...] = ((2019, 3), (2019, 4)),
    *,
    symbol: str = "EURUSD",
    extra: dict[tuple[int, int], tuple[int, ...]] | None = None,
    definition_symbols: str = "EURUSD, USDJPY",
    definition: bool = True,
    raw_bytes: dict[tuple[int, int], bytes] | None = None,
) -> Path:
    repo = tmp_path / "repo"
    for rel in COPIED:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, repo / rel)
    (repo / "research").mkdir()
    (repo / "research" / "quality_access_log.jsonl").write_bytes(b"")
    raw = repo / "data" / "raw" / "oanda_mt5" / FETCH_DATE
    files: dict[str, object] = {}
    for year, month in months:
        times = _month_rows(year, month, extra=(extra or {}).get((year, month), ()))
        name = f"{symbol}/{symbol}_{year}-{month:02d}_m1.csv.gz"
        data = (raw_bytes or {}).get((year, month), _gz(times))
        (raw / symbol).mkdir(parents=True, exist_ok=True)
        (raw / name).write_bytes(data)
        start = datetime(year, month, 1, tzinfo=UTC)
        end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
        files[f"{symbol}-{year}-{month:02d}"] = {
            "pair": symbol,
            "file": name,
            "status": "ok",
            "requested_from_api_time": start.isoformat(),
            "requested_to_api_time": end.isoformat(),
            "rows": len(times),
            "first_bar_time": times[0],
            "last_bar_time": max(times[0], times[-1]),
            "sha256_gz": hashlib.sha256(data).hexdigest(),
            "sha256_csv": hashlib.sha256(_csv_bytes(data)).hexdigest(),
        }
    manifest = json.dumps({"files": files}, indent=1).encode("utf-8")
    (raw / "manifest.json").write_bytes(manifest)
    copy = repo / "docs" / "data" / f"oanda_mt5_full_fetch_{FETCH_DATE}" / "manifest.json"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(manifest)
    if definition:
        _write_definition(repo, definition_symbols)
    return repo


def _write_definition(repo: Path, symbols: str) -> None:
    path = repo / "configs" / "quality_checks" / "oanda_mt5_research_raw_verify.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        DEFINITION.format(digest=research_config_digest(repo), symbols=symbols), encoding="utf-8"
    )


def _verify(repo: Path, out: Path | None = None, symbol: str = "EURUSD") -> int:
    argv = [
        "data",
        "verify-raw-mt5",
        "--fetch-date",
        FETCH_DATE,
        "--symbol",
        symbol,
        "--repo-root",
        str(repo),
    ]
    if out is not None:
        argv += ["--out", str(out)]
    return main(argv)


def _rows(repo: Path) -> tuple[object, ...]:
    contents = QualityAccessLogFile(repo).read()
    assert isinstance(contents, QualityAccessLogContents)
    return contents.rows


def _manifest(repo: Path) -> dict[str, dict[str, object]]:
    path = repo / "data" / "raw" / "oanda_mt5" / FETCH_DATE / "manifest.json"
    files: dict[str, dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))["files"]
    return files


# --- 確認の読み取り（D03 §17.4・§17.6 の 6） --------------------------------------------------


def test_a_the_research_definition_permits_the_read_and_the_files_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    out = tmp_path / "verify.json"
    capsys.readouterr()
    assert _verify(repo, out) == 0
    printed = capsys.readouterr().out
    start, end = _rows(repo)
    assert isinstance(start, QualityAccessStart) and isinstance(end, QualityAccessEnd)
    assert start.classes == frozenset({AccessClass.RESEARCH_HISTORY})
    assert start.check_kind is QualityCheckKind.RAW_FILE_VERIFICATION
    assert end.result is QualityAccessResult.COMPLETED and end.access_id == start.access_id
    payload = json.loads(out.read_text(encoding="utf-8"))
    files = _manifest(repo)
    assert [(row["location"], row["sha256"], row["result"]) for row in payload["files"]] == [
        (f"data/raw/oanda_mt5/{FETCH_DATE}/{entry['file']}", entry["sha256_gz"], "PASS")
        for entry in files.values()
    ]
    assert {row["access_id"] for row in payload["files"]} == {start.access_id}
    assert payload["counts"] == {"pass": 2, "fail": 0, "unread": 0}
    targets = [(target.location, target.sha256) for target in start.targets]  # type: ignore[union-attr]
    assert targets == [(row["location"], row["sha256"]) for row in payload["files"]]
    log_text = (repo / "research" / "quality_access_log.jsonl").read_text(encoding="utf-8")
    for text in (printed, out.read_text(encoding="utf-8"), log_text):
        assert PRICE not in text  # 価格を出さない
        assert "2019-03-04T12" not in text  # 時刻を出さない


def test_b_a_symbol_outside_the_definition_is_never_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, definition_symbols="USDJPY")
    assert _verify(repo) == 1
    assert "始めなかった" in capsys.readouterr().out
    assert _rows(repo) == ()


def test_b_without_an_approved_definition_nothing_is_read(tmp_path: Path) -> None:
    repo = _repo(tmp_path, definition=False)
    assert _verify(repo) == 1
    assert _rows(repo) == ()


def test_b_a_changed_rule_file_changes_the_digest_and_stops_the_permit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "configs" / "datasources" / "oanda_mt5_m1_v1.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert _verify(repo) == 1
    assert _rows(repo) == ()


def test_b_the_pre_read_check_stops_before_asking_for_a_permit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    path = repo / "data" / "raw" / "oanda_mt5" / FETCH_DATE / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    first = next(iter(payload["files"].values()))
    first["requested_to_api_time"] = "2024-01-01T00:01:00+00:00"
    text = json.dumps(payload).encode("utf-8")
    path.write_bytes(text)
    (repo / "docs" / "data" / f"oanda_mt5_full_fetch_{FETCH_DATE}" / "manifest.json").write_bytes(
        text
    )
    assert _verify(repo) == 1
    assert "REQUEST_PAST_2024" in capsys.readouterr().err
    assert _rows(repo) == ()
    # 取得記録と版管理の写しが違うときも許可を求めない。
    path.write_bytes(text + b"\n")
    assert _verify(repo) == 1
    assert _rows(repo) == ()


def test_d_a_row_from_2024_stops_the_read_without_showing_prices(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """2024 年以降の行（API の時刻 2024-01-01T05:00）を 2023-11 のファイルの真ん中に入れる。

    表 版 1 は API の暦日 2024-01-01 以降を覆わないので、行は UTC に直せず 2024 年より前と
    言えない。その場で止め（`TIME_RULE_NOT_COVERED`）、残りの 2023-12 のファイルを読まない。
    """
    late = _epoch("2024-01-01T05:00:00+00:00")
    repo = _repo(tmp_path, months=((2023, 11), (2023, 12)), extra={(2023, 11): (late,)})
    out = tmp_path / "verify.json"
    capsys.readouterr()
    assert _verify(repo, out) == 1
    printed = capsys.readouterr().out
    assert "途中で停止" in printed
    start, end = _rows(repo)
    assert isinstance(end, QualityAccessEnd) and end.result is QualityAccessResult.STOPPED
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["stopped"] is True
    assert [(row["result"], row["reason"]) for row in payload["files"]] == [
        ("FAIL", "TIME_RULE_NOT_COVERED")
    ]
    assert payload["counts"] == {"pass": 0, "fail": 1, "unread": 1}
    for text in (printed, out.read_text(encoding="utf-8")):
        assert PRICE not in text and "2024-01-01T05" not in text


def test_a_file_whose_bytes_differ_from_the_record_fails_without_being_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    entry = next(iter(_manifest(repo).values()))
    path = repo / "data" / "raw" / "oanda_mt5" / FETCH_DATE / str(entry["file"])
    path.write_bytes(_gz(_month_rows(2019, 3)[:-1]))
    capsys.readouterr()
    assert _verify(repo) == 1
    printed = capsys.readouterr().out
    assert "HASH_MISMATCH" in printed and "合格 1 件、不合格 1 件" in printed
    _, end = _rows(repo)
    assert isinstance(end, QualityAccessEnd) and end.result is QualityAccessResult.COMPLETED


def test_a_missing_or_broken_file_fails_alone_and_the_rest_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """読めないファイル（欠けた・記録のハッシュと一致する壊れた gzip）はそのファイルだけ不合格。"""
    broken = _gz(_month_rows(2019, 4))[:40]  # gzip の頭だけ（展開できない）
    repo = _repo(tmp_path, months=((2019, 3), (2019, 4), (2019, 5)), raw_bytes={(2019, 4): broken})
    entry = next(iter(_manifest(repo).values()))
    (repo / "data" / "raw" / "oanda_mt5" / FETCH_DATE / str(entry["file"])).unlink()
    out = tmp_path / "verify.json"
    capsys.readouterr()
    assert _verify(repo, out) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [(row["result"], row["reason"]) for row in payload["files"]] == [
        ("FAIL", "FILE_UNREADABLE"),
        ("FAIL", "FILE_UNREADABLE"),
        ("PASS", None),
    ]
    assert payload["stopped"] is False
    _, end = _rows(repo)
    assert isinstance(end, QualityAccessEnd) and end.result is QualityAccessResult.COMPLETED


# --- 受入れの前の一覧との照合（D03 §17.7） -----------------------------------------------------


def _list_path(repo: Path) -> Path:
    return repo / "configs" / "datasources" / f"oanda_mt5_m1_verified_raw_{FETCH_DATE}.yaml"


def _write_list(repo: Path, rows: list[dict[str, object]], **header: str) -> Path:
    raw_manifest = repo / "data" / "raw" / "oanda_mt5" / FETCH_DATE / "manifest.json"
    values = {
        "fetch_manifest_sha256": hashlib.sha256(raw_manifest.read_bytes()).hexdigest(),
        "permit_definition_sha256": "a" * 64,
        "declared_period_end": "2024-01-01T00:00:00Z",
        "fetch_date": FETCH_DATE,
    }
    values.update(header)
    lines = [
        "schema_version: 1",
        "id: oanda_mt5_m1_verified_raw",
        "version: 1",
        "datasource: oanda_mt5_m1",
        f'fetch_date: "{values["fetch_date"]}"',
        f"fetch_manifest_sha256: {values['fetch_manifest_sha256']}",
        'verified_by: {name: oanda_mt5_research_raw_verify, version: "1"}',
        f"permit_definition_sha256: {values['permit_definition_sha256']}",
        f'declared_period_end: "{values["declared_period_end"]}"',
        "files:",
    ]
    for row in rows:
        lines.append(
            f"  - {{location: {row['location']}, symbol: {row['symbol']}, sha256: {row['sha256']},"
            f" access_id: {row['access_id']}, result: {row['result']}}}"
        )
    path = _list_path(repo)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _verified_rows(repo: Path, tmp_path: Path) -> list[dict[str, object]]:
    out = tmp_path / "verify.json"
    assert _verify(repo, out) == 0
    rows: list[dict[str, object]] = json.loads(out.read_text(encoding="utf-8"))["files"]
    return rows


def _accept(repo: Path, *extra: str) -> int:
    return main(
        [
            "data",
            "accept-mt5",
            "--fetch-date",
            FETCH_DATE,
            "--symbol",
            "EURUSD",
            "--variant",
            "standard",
            "--out",
            str(repo / "data" / "snapshots"),
            "--repo-root",
            str(repo),
            *extra,
        ]
    )


def _pending(repo: Path) -> list[Path]:
    pending = repo / "data" / "snapshots" / "_pending"
    return sorted(pending.iterdir()) if pending.is_dir() else []


def test_without_the_verified_list_the_acceptance_reads_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    capsys.readouterr()
    assert _accept(repo) == 1
    assert "no verified hash list" in capsys.readouterr().err
    assert _pending(repo) == []


def test_a_listed_file_with_the_same_hash_is_accepted_with_the_matching_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    list_path = _write_list(repo, _verified_rows(repo, tmp_path))
    capsys.readouterr()
    assert _accept(repo) == 0
    printed = capsys.readouterr().out
    assert "確認済みハッシュの一覧との照合: 一致（受け入れた原ファイル 2 件" in printed
    assert hashlib.sha256(list_path.read_bytes()).hexdigest() in printed
    (pending,) = _pending(repo)
    manifest = json.loads((pending / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["quality_read"]["permit_required"] is False


def test_a_file_changed_after_the_list_is_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    _write_list(repo, _verified_rows(repo, tmp_path))
    entry = next(iter(_manifest(repo).values()))
    (repo / "data" / "raw" / "oanda_mt5" / FETCH_DATE / str(entry["file"])).write_bytes(
        _gz(_month_rows(2019, 3)[:-1])
    )
    capsys.readouterr()
    assert _accept(repo) == 1
    assert "but the verified hash list" in capsys.readouterr().err
    assert _pending(repo) == []


def test_a_file_missing_from_the_list_is_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    _write_list(repo, _verified_rows(repo, tmp_path)[:1])
    capsys.readouterr()
    assert _accept(repo) == 1
    assert "is not in the verified hash list" in capsys.readouterr().err
    assert _pending(repo) == []
    # --months でその月だけを受け入れるなら、一覧に載る月だけを読む。
    assert _accept(repo, "--months", "2019-03") == 0
    assert len(_pending(repo)) == 1


def test_a_list_that_disagrees_with_the_fetch_record_stops_the_acceptance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    rows = _verified_rows(repo, tmp_path)
    _write_list(repo, rows, fetch_manifest_sha256="b" * 64)
    capsys.readouterr()
    assert _accept(repo) == 1
    assert "was made for the fetch record" in capsys.readouterr().err
    wrong = [dict(rows[0], sha256="c" * 64), rows[1]]
    _write_list(repo, wrong)
    assert _accept(repo) == 1
    assert "does not match the fetch record" in capsys.readouterr().err
    assert _pending(repo) == []


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"declared_period_end": "2024-01-01T00:01:00Z"}, "declared period end"),
        ({"fetch_date": "2030-01-03"}, "fetch_date"),
    ],
)
def test_a_list_that_fails_its_load_checks_reads_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: dict[str, str], match: str
) -> None:
    repo = _repo(tmp_path)
    _write_list(repo, _verified_rows(repo, tmp_path), **change)
    capsys.readouterr()
    assert _accept(repo) == 1
    assert match in capsys.readouterr().err
    assert _pending(repo) == []
