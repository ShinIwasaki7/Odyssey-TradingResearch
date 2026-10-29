"""全期間取得（D12 付録 K-16。人間の第 18 回の決定）の道具とスクリプトの単体テスト。

MetaTrader5 は Windows 専用なので偽の API を使う。価格は人工の値だけ。S3 は偽の ``aws`` コマンド
（手元のフォルダを S3 に見立てる）で置き換え、AWS には一切つながない。
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.survey import mt5_api_probe as probe
from tools.survey import mt5_full_fetch_estimate as est

SCRIPTS = Path(probe.__file__).resolve().parent / "aws_mt5_probe"
BUCKET = "odyssey-mt5-probe-2026-10-16-abcd1234"


class FakeMt5:
    """要求の区間の中の足 3 本と、区間の終わりちょうどの足 1 本・区間の外の足 1 本を返す偽の API。

    ``empty`` は要求の名前 → 0 本を返す回数（区間の外の 1 本だけ返す。取り寄せ前の端末の振る舞い）。
    """

    COPY_TICKS_ALL = 1
    TIMEFRAME_M1 = 1

    def __init__(self, empty: dict[str, int] | None = None, names: tuple[str, ...] = ()) -> None:
        self.empty = dict(empty or {})
        self.names = names or tuple(f"{p}.cl" for p in probe.FULL_PAIRS)
        self.rate_calls: list[tuple[str, datetime, datetime]] = []

    def initialize(self) -> bool:
        return True

    def version(self) -> Any:
        return (500, 5000, "test")

    def terminal_info(self) -> Any:
        return SimpleNamespace(build=5000, name="t", company="c", maxbars=0, connected=True)

    def symbols_get(self, group: str) -> Any:
        key = group.strip("*")
        return [SimpleNamespace(name=n) for n in self.names if key in n]

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(chart_mode=0, point=0.001, digits=3)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def copy_ticks_range(self, *args: Any) -> Any:
        raise AssertionError("全期間取得は tick を要求しない")

    def copy_rates_range(
        self, symbol: str, timeframe: int, date_from: datetime, date_to: datetime
    ) -> Any:
        self.rate_calls.append((symbol, date_from, date_to))
        s, e = int(date_from.timestamp()), int(date_to.timestamp())
        pair = symbol.split(".")[0]
        rid = f"F-{pair}-{date_from.year:04d}-{date_from.month:02d}"
        outside = {"time": e + 86_400 * 900, "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0}
        if self.empty.get(rid, 0) > 0:
            self.empty[rid] -= 1
            return [outside]
        bars = [
            {
                "time": t,
                "open": 100.001 + i,
                "high": 100.009 + i,
                "low": 100.0 + i,
                "close": 100.005 + i,
                "tick_volume": 10 + i,
                "spread": 3,
                "real_volume": 0,
            }
            for i, t in enumerate((s, s + 60, s + 120, e))
        ]
        return [*bars, outside]

    def last_error(self) -> Any:
        return (1, "Success")

    def shutdown(self) -> Any:
        return None


def _run(api: FakeMt5, out: Path, pair: str = "USDJPY", **kw: Any) -> dict[str, Any]:
    kw.setdefault("sleep", lambda s: None)
    return probe.run_full_fetch(api, pair, out, **kw)


def _manifest(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / probe.FULL_MANIFEST).read_text(encoding="utf-8"))
    return data


# --- 月の分け方・研究期間 ---------------------------------------------------------------------


def test_plan_is_672_months_new_to_old_without_gaps() -> None:
    plan = probe.full_plan()
    assert len(plan) == probe.FULL_PLANNED_REQUESTS == 8 * 84
    assert probe.FULL_PAIRS == (
        "USDJPY",
        "EURJPY",
        "EURUSD",
        "GBPJPY",
        "USDCHF",
        "AUDJPY",
        "GBPUSD",
        "AUDUSD",
    )
    usd = [r for r in plan if r.pair == "USDJPY"]
    assert (usd[0].year, usd[0].month) == (2023, 12)
    assert (usd[-1].year, usd[-1].month) == (2017, 1)
    for newer, older in zip(usd, usd[1:], strict=False):
        assert older.window().end == newer.window().start  # 月の境目に隙間・重なりが無い
    assert usd[0].window().end == probe.REQUEST_END_LIMIT
    assert usd[-1].window().start == probe.FULL_FIRST_START
    assert usd[0].file == "USDJPY/USDJPY_2023-12_m1.csv.gz"


def test_plan_matches_the_estimate_tool() -> None:
    mine = [(r.rid, r.window().start, r.window().end) for r in probe.full_plan()]
    theirs = [(r.rid, r.start, r.end) for r in est.request_plan()]
    assert mine == theirs


def test_2024_and_pre_2017_are_refused() -> None:
    with pytest.raises(probe.RequestOutOfRangeError):
        probe.check_full_window(
            probe.Window("x", datetime(2023, 12, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC))
        )
    with pytest.raises(probe.RequestOutOfRangeError):
        probe.check_full_window(
            probe.Window("x", datetime(2016, 12, 1, tzinfo=UTC), datetime(2017, 1, 1, tzinfo=UTC))
        )
    with pytest.raises(probe.RequestOutOfRangeError):
        probe.check_full_window(probe.MonthRequest("USDJPY", 2024, 1).window())


def test_unknown_pair_and_over_limit_are_refused_before_any_call(tmp_path: Path) -> None:
    api = FakeMt5()
    with pytest.raises(probe.FullFetchLimitError):
        _run(api, tmp_path, pair="XAUUSD")
    with pytest.raises(probe.FullFetchLimitError):
        _run(api, tmp_path, max_total=751)
    assert api.rate_calls == []


# --- gzip の CSV・manifest・取得の記録 ----------------------------------------------------------


def test_month_files_are_gzip_csv_with_raw_time_and_no_boundary_bar(tmp_path: Path) -> None:
    api = FakeMt5()
    r = _run(api, tmp_path, tool_sha256="t" * 64)
    assert r["stop"] is None and r["ok"] == 84 and r["sent_this_run"] == 84
    assert all(s == "USDJPY.cl" for s, _, _ in api.rate_calls)
    path = tmp_path / "USDJPY" / "USDJPY_2017-01_m1.csv.gz"
    lines = gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()
    assert lines[0] == "time,open,high,low,close,tick_volume,spread,real_volume"
    start = int(datetime(2017, 1, 1, tzinfo=UTC).timestamp())
    assert [ln.split(",")[0] for ln in lines[1:]] == [
        str(start),
        str(start + 60),
        str(start + 120),
    ]  # 生の時刻（整数の秒）。区間の終わりちょうどの足・区間の外の足は保存しない
    assert lines[1] == f"{start},100.001,100.009,100.0,100.005,10,3,0"  # float の最短の表記
    m = _manifest(tmp_path)
    e = m["files"]["F-USDJPY-2017-01"]
    assert e["status"] == "ok" and e["rows"] == 3 and e["out_of_window_count"] == 2
    assert e["first_bar_time"] == start and e["file"] == "USDJPY/USDJPY_2017-01_m1.csv.gz"
    assert e["sha256_gz"] == probe._sha256(path)
    assert e["sha256_csv"] == probe._sha256_bytes(gzip.decompress(path.read_bytes()))
    assert m["requests_sent"] == 84 and m["max_requests"] == 750
    assert m["tool_sha256"] == "t" * 64
    assert m["symbols"]["USDJPY"] == {
        "symbol": "USDJPY.cl",
        "chart_mode": 0,
        "point": 0.001,
        "digits": 3,
    }
    assert "再配布しない" in m["storage_terms"] and "未確認" in m["storage_terms"]
    assert m["terminal"]["terminal"]["maxbars"] == 0


def test_gzip_is_deterministic() -> None:
    data = probe.month_csv_bytes([{"time": 1, "open": 1.5}])
    assert probe.gzip_bytes(data) == probe.gzip_bytes(data)


def test_fetch_log_has_counts_and_times_but_no_prices(tmp_path: Path) -> None:
    _run(FakeMt5(empty={"F-USDJPY-2023-11": 1}), tmp_path)
    lines = (tmp_path / probe.FULL_FETCH_LOG).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 84 + 1  # 1 回の出し直しも記録する
    recs = [json.loads(x) for x in lines]
    assert set(recs[0]) == {
        "rid",
        "attempt",
        "redo_empty",
        "requested_from_api_time",
        "requested_to_api_time",
        "returned_in_window",
        "out_of_window",
        "executed_at_utc",
        "disk_free_bytes",
    }
    text = "\n".join(lines)
    assert "100.00" not in text and "9.0" not in text


# --- 0 本の月・出し直し・上限 ---------------------------------------------------------------------


def test_zero_bars_is_retried_with_waits_not_counted(tmp_path: Path) -> None:
    waits: list[float] = []
    r = _run(FakeMt5(empty={"F-USDJPY-2023-12": 2}), tmp_path, sleep=waits.append)
    assert waits == [10.0, 30.0]
    assert r["stop"] is None and r["sent_this_run"] == 84
    assert _manifest(tmp_path)["files"]["F-USDJPY-2023-12"]["retries"] == 2


def test_first_month_empty_stops_the_pair(tmp_path: Path) -> None:
    waits: list[float] = []
    api = FakeMt5(empty={"F-USDJPY-2023-12": 99})
    r = _run(api, tmp_path, sleep=waits.append)
    assert r["stop"] == "first_month_empty"
    assert r["sent_this_run"] == 1 and len(api.rate_calls) == 5
    assert waits == [10.0, 30.0, 60.0, 120.0]
    assert len(r["remaining"]) == 84


def test_later_empty_month_is_recorded_and_redone_only_on_request(tmp_path: Path) -> None:
    api = FakeMt5(empty={"F-USDJPY-2020-06": 5})
    r = _run(api, tmp_path)
    assert r["stop"] is None and r["ok"] == 83 and r["empty"] == 1
    e = _manifest(tmp_path)["files"]["F-USDJPY-2020-06"]
    assert e["status"] == "empty" and e["file"] is None
    assert not (tmp_path / "USDJPY" / "USDJPY_2020-06_m1.csv.gz").exists()
    # 普通の再実行では空の月を出し直さない（取得済みの月も出さない）
    api2 = FakeMt5(empty={"F-USDJPY-2020-06": 5})
    r2 = _run(api2, tmp_path)
    assert api2.rate_calls == [] and r2["skipped_done"] == 83
    # --redo-empty なら空の月だけ（要求の数に数える）
    api3 = FakeMt5()
    r3 = _run(api3, tmp_path, redo_empty=True)
    assert [(c[1].year, c[1].month) for c in api3.rate_calls] == [(2020, 6)]
    assert r3["ok"] == 1 and r3["requests_sent_total"] == 85 and r3["remaining"] == []
    assert _manifest(tmp_path)["files"]["F-USDJPY-2020-06"]["attempts"] == 2


def test_request_limit_750_counts_across_runs(tmp_path: Path) -> None:
    m = probe.load_full_manifest(tmp_path)
    m["requests_sent"] = 748
    (tmp_path / probe.FULL_MANIFEST).write_text(json.dumps(m), encoding="utf-8")
    api = FakeMt5()
    r = _run(api, tmp_path)
    assert r["stop"] == "request_limit" and r["sent_this_run"] == 2
    assert len(api.rate_calls) == 2 and r["requests_sent_total"] == 750


def test_time_limit_and_low_disk_stop_before_the_next_request(tmp_path: Path) -> None:
    api = FakeMt5()
    r = _run(api, tmp_path / "a", stop_at_epoch=100.0, clock=lambda: 100.0)
    assert r["stop"] == "time_limit" and api.rate_calls == []
    r = _run(api, tmp_path / "b", disk_free=lambda: probe.MIN_FREE_BYTES - 1)
    assert r["stop"] == "low_disk" and api.rate_calls == []


def test_tampered_file_is_fetched_again(tmp_path: Path) -> None:
    _run(FakeMt5(), tmp_path)
    (tmp_path / "USDJPY" / "USDJPY_2019-03_m1.csv.gz").write_bytes(b"broken")
    api = FakeMt5()
    _run(api, tmp_path)
    assert [(c[1].year, c[1].month) for c in api.rate_calls] == [(2019, 3)]


# --- 回収の照合 -----------------------------------------------------------------------------------


def test_verify_accepts_a_clean_dir_and_finds_tampering(tmp_path: Path) -> None:
    _run(FakeMt5(), tmp_path)
    _run(FakeMt5(), tmp_path, pair="EURUSD")
    assert probe.verify_full_dir(tmp_path) == []
    assert probe.verify_full_dir(tmp_path, ["EURUSD"]) == []
    bad = tmp_path / "USDJPY" / "USDJPY_2018-05_m1.csv.gz"
    bad.write_bytes(probe.gzip_bytes(b"time,open\n1,2\n"))
    (tmp_path / "USDJPY" / "USDJPY_2099-01_m1.csv.gz").write_bytes(b"x")
    problems = probe.verify_full_dir(tmp_path, ["USDJPY"])
    assert any("F-USDJPY-2018-05" in p and "SHA-256" in p for p in problems)
    assert any("manifest に無いファイル" in p for p in problems)
    assert probe.verify_full_dir(tmp_path, ["EURUSD"]) == []


def test_cli_fullverify_exit_codes(tmp_path: Path) -> None:
    _run(FakeMt5(), tmp_path)
    assert probe.main(["fullverify", "--root", str(tmp_path)]) == 0
    (tmp_path / "USDJPY" / "USDJPY_2017-02_m1.csv.gz").unlink()
    assert probe.main(["fullverify", "--root", str(tmp_path)]) == 1


# --- S3 からの回収のスクリプト（偽の aws） -----------------------------------------------------

FAKE_AWS = r"""#!/usr/bin/env bash
args=()
while (( $# > 0 )); do
  case "$1" in --profile|--region) shift 2 ;; *) args+=("$1"); shift ;; esac
done
set -- "${args[@]}"
echo "$*" >>"$FAKE_S3/calls.log"
if [[ "$1 $2" == "configure list-profiles" ]]; then echo odyssey-mt5-probe; exit 0; fi
if [[ "$1 $2" == "ec2 describe-instances" ]]; then echo odyssey-mt5-probe; exit 0; fi
if [[ "$1 $2" == "s3 cp" ]]; then
  src="$3"; dst="$4"; rec=0
  for a in "$@"; do [[ "$a" == --recursive ]] && rec=1; done
  path="$FAKE_S3/${src#s3://}"
  if [[ $rec == 1 ]]; then
    [[ -d "$path" ]] || exit 0
    mkdir -p "$dst"; cp -R "$path"/. "$dst"/; exit 0
  fi
  [[ -f "$path" ]] || exit 1
  cp "$path" "$dst"; exit 0
fi
echo "unexpected aws call: $*" >&2; exit 2
"""


def _shell_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    aws = bin_dir / "aws"
    aws.write_text(FAKE_AWS, encoding="utf-8")
    aws.chmod(0o755)
    result_root = tmp_path / "probe_root"
    state = result_root / "_state"
    state.mkdir(parents=True)
    (state / "s3_bucket.env").write_text(
        f"S3_BUCKET={BUCKET}\nFETCH_DATE=2026-10-16\n", encoding="utf-8"
    )
    env = dict(os.environ)
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        FAKE_S3=str(tmp_path / "s3"),
        RESULT_ROOT=str(result_root),
        FULL_ROOT=str(tmp_path / "oanda_mt5"),
        REPO_PY=sys.executable,
        PY_MAC=sys.executable,
    )
    return env


def _upload_to_fake_s3(tmp_path: Path) -> Path:
    win = tmp_path / "win_out"
    _run(FakeMt5(), win)
    s3 = tmp_path / "s3" / BUCKET / "full"
    shutil.copytree(win, s3)
    return s3


needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")


@needs_bash
def test_collect_s3_places_verified_files_under_fetch_date(tmp_path: Path) -> None:
    _upload_to_fake_s3(tmp_path)
    env = _shell_env(tmp_path)
    r = subprocess.run(
        ["bash", str(SCRIPTS / "45_collect_s3.sh"), "USDJPY", "--execute"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    dest = tmp_path / "oanda_mt5" / "2026-10-16"
    assert len(list((dest / "USDJPY").glob("*.csv.gz"))) == 84
    assert (dest / "manifest.json").exists() and (dest / "fetch_log.jsonl").exists()
    assert probe.verify_full_dir(dest) == []
    assert "取得済み 84 / 84 か月" in r.stdout
    assert "100.00" not in r.stdout + r.stderr  # 価格を表示しない
    s3env = (tmp_path / "probe_root" / "_state" / "s3_bucket.env").read_text(encoding="utf-8")
    assert "COLLECTED_MANIFEST_SHA256=" in s3env and "COLLECTED_BYTES=" in s3env
    # 2 回目は同じ中身なので上書きせずに通る
    r2 = subprocess.run(
        ["bash", str(SCRIPTS / "45_collect_s3.sh"), "USDJPY", "--execute"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r2.returncode == 0, r2.stderr


@needs_bash
def test_collect_s3_refuses_a_hash_mismatch_and_places_nothing(tmp_path: Path) -> None:
    s3 = _upload_to_fake_s3(tmp_path)
    (s3 / "USDJPY" / "USDJPY_2021-07_m1.csv.gz").write_bytes(probe.gzip_bytes(b"changed"))
    r = subprocess.run(
        ["bash", str(SCRIPTS / "45_collect_s3.sh"), "USDJPY", "--execute"],
        env=_shell_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0
    assert "manifest と合わない" in r.stderr
    assert not (tmp_path / "oanda_mt5" / "2026-10-16").exists()


@needs_bash
def test_collect_s3_dry_run_touches_nothing(tmp_path: Path) -> None:
    _upload_to_fake_s3(tmp_path)
    env = _shell_env(tmp_path)
    r = subprocess.run(
        ["bash", str(SCRIPTS / "45_collect_s3.sh"), "USDJPY"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "oanda_mt5").exists()
    calls = (tmp_path / "s3" / "calls.log").read_text(encoding="utf-8")
    assert "s3 cp" not in calls


@needs_bash
def test_bucket_name_guard_rejects_other_buckets(tmp_path: Path) -> None:
    for name, ok in ((BUCKET, True), ("my-other-bucket", False), ("odyssey-mt5-probe-x", False)):
        r = subprocess.run(
            ["bash", "-c", f'source "{SCRIPTS / "lib.sh"}" && assert_probe_bucket "{name}"'],
            env=_shell_env(tmp_path / name),
            capture_output=True,
            text=True,
            check=False,
        )
        assert (r.returncode == 0) is ok, (name, r.stderr)


@needs_bash
def test_full_fetch_dry_run_creates_nothing_and_refuses_unknown_pairs(tmp_path: Path) -> None:
    env = _shell_env(tmp_path)
    state = tmp_path / "probe_root" / "_state"
    (state / "s3_bucket.env").unlink()
    (state / "state.env").write_text(
        f"INSTANCE_ID=i-0123\nLAUNCHED_AT_EPOCH={int(datetime.now(tz=UTC).timestamp())}\n"
        "PROBE_DATE=2026-10-16\nRUN_KIND=fullfetch\n",
        encoding="utf-8",
    )
    (tmp_path / "s3").mkdir()
    script = str(SCRIPTS / "38_full_fetch.sh")
    r = subprocess.run(["bash", script], env=env, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert "USDJPY,EURJPY,EURUSD,GBPJPY,USDCHF,AUDJPY,GBPUSD,AUDUSD" in r.stderr
    assert "上限 750" in r.stderr and "(dry-run)" in r.stderr
    calls = (tmp_path / "s3" / "calls.log").read_text(encoding="utf-8")
    assert "create-bucket" not in calls and "ssm" not in calls
    bad = subprocess.run(
        ["bash", script, "--pairs", "XAUUSD"], env=env, capture_output=True, text=True, check=False
    )
    assert bad.returncode != 0 and "一覧に無い通貨" in bad.stderr
    (state / "state.env").write_text(
        "INSTANCE_ID=i-0123\nLAUNCHED_AT_EPOCH=1\nPROBE_DATE=2026-10-16\nRUN_KIND=fullfetch\n",
        encoding="utf-8",
    )
    late = subprocess.run(["bash", script], env=env, capture_output=True, text=True, check=False)
    assert late.returncode != 0 and "超えた" in late.stderr  # 終了の時刻（4 時間）の後は動かない


def _lib_values(tmp_path: Path, kind: str | None) -> list[str]:
    env = dict(os.environ, RESULT_ROOT=str(tmp_path / "probe_root"))
    env.pop("PROBE_RUN_KIND", None)
    if kind is not None:
        env["PROBE_RUN_KIND"] = kind
    out = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{SCRIPTS / "lib.sh"}"; echo "$PROBE_RUN_KIND $PROBE_SHUTDOWN_SECONDS'
            ' $PROBE_DEADLINE_SECONDS $REQUEST_TIME_LIMIT_SECONDS"',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def test_limits_in_lib_sh_match_the_approved_values(tmp_path: Path) -> None:
    text = (SCRIPTS / "lib.sh").read_text(encoding="utf-8")
    assert "readonly FULL_TIME_LIMIT_SECONDS=9000" in text  # 2 時間 30 分
    assert "readonly FULL_MAX_REQUESTS=750" in text
    assert "readonly PROBE_ROOT_GB=60" in text
    # 起動の種類ごとの上限（全期間取得は付録 K-16.6、封印の一括取得は D15-18c (a)、定期取得は
    # PR #109 の仮置き S2b-10）。既定は全期間取得。
    assert _lib_values(tmp_path, None) == ["fullfetch", "16200", "14400", "9000"]
    assert _lib_values(tmp_path, "sealed-bulk") == ["sealed-bulk", "12600", "10800", "7200"]
    assert _lib_values(tmp_path, "sealed-periodic") == ["sealed-periodic", "9000", "7200", "3600"]


def test_iam_policies_allow_only_the_probe_bucket_prefix() -> None:
    op = json.loads((SCRIPTS / "iam_policy_operator.json").read_text(encoding="utf-8"))
    role = json.loads((SCRIPTS / "iam_policy_ssm_role_s3.json").read_text(encoding="utf-8"))
    s3_actions: set[str] = set()
    for st in op["Statement"] + role["Statement"]:
        acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        s3 = [a for a in acts if a.startswith("s3:")]
        if not s3:
            continue
        s3_actions |= set(s3)
        res = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
        assert all(r.startswith("arn:aws:s3:::odyssey-mt5-probe-") for r in res)
    assert s3_actions == {
        "s3:CreateBucket",
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:DeleteObject",
        "s3:DeleteBucket",
    }
    assert [a for st in role["Statement"] for a in st["Action"]] == ["s3:PutObject"]
