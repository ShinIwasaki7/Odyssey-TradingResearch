"""封印の取得のスクリプト（`39_sealed_fetch.sh`・起動の種類・回収の置き場）の分岐。

AWS にはつながない。偽の ``aws``（`test_aws_probe_scripts.py` と同じもの）を ``PATH`` の先頭に
置いて動かす。スクリプトと道具は一時のリポジトリに写し、窓の設定は人工の値（取得の開始時点を
過去の 2025-11-02 にした窓）を置く（本物の取得の開始時点 2026-11-01 はテストの実行時点より後で、
窓の検査「要求の時点より前だけ」で止まるため）。確かめること: 計画が窓の設定と合わなければ AWS を
1 度も呼ばずに止まる、起動の種類が封印の取得でなければ止まる、dry-run は何も作らない、--execute は
バケットの記録に封印の取得の印（S3_PREFIX=sealed と計画）を書く。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.unit.test_aws_probe_scripts import SCRIPTS, _calls, _env, _state_dir, needs_bash
from tools.survey import mt5_api_probe as probe


def _state(tmp_path: Path, kind: str) -> None:
    (_state_dir(tmp_path) / "state.env").write_text(
        f"INSTANCE_ID=i-0123\nLAUNCHED_AT_EPOCH={int(datetime.now(tz=UTC).timestamp())}\n"
        "LAUNCHED_AT_UTC=2026-11-14T00:00:00Z\nPROBE_DATE=2026-11-14\n"
        f"RUN_KIND={kind}\nKEY_FILE=/nonexistent/key.pem\n",
        encoding="utf-8",
    )


WINDOW_TEXT = """schema_version: 1
decision: ADR-0037
datasource: oanda_mt5_m1
price_basis: bid
timeframe: M1
symbols: [USDJPY, USDCHF, AUDJPY, EURUSD, EURJPY, GBPJPY, GBPUSD, AUDUSD]
allowed_start: "2024-01-01T00:00:00Z"
acquisition_start: "2025-11-02T22:00:00Z"
acquisition_start_basis:
  merge_commit: null
  merge_commit_time: null
"""


def _repo(tmp_path: Path) -> Path:
    """スクリプトと道具と人工の窓の設定を一時のリポジトリに写す。"""
    repo = tmp_path / "repo"
    shutil.copytree(SCRIPTS, repo / "tools" / "survey" / "aws_mt5_probe")
    shutil.copy(Path(probe.__file__), repo / "tools" / "survey" / "mt5_api_probe.py")
    (repo / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tools" / "survey" / "__init__.py").write_text("", encoding="utf-8")
    window = repo / "configs" / "acquisition" / "oanda_mt5_m1_sealed_window.yaml"
    window.parent.mkdir(parents=True)
    window.write_text(WINDOW_TEXT, encoding="utf-8")
    return repo


def _plan(tmp_path: Path, repo: Path, *, broken: bool = False) -> Path:
    """人工の窓の定期取得の初回（[2025-11-02T22:00Z, 2025-11-09T22:00Z)、8 通貨）の計画。"""
    window = probe.read_sealed_window(repo / "configs/acquisition/oanda_mt5_m1_sealed_window.yaml")
    start, end = datetime(2025, 11, 2, 22, tzinfo=UTC), datetime(2025, 11, 9, 22, tzinfo=UTC)
    requests = [
        probe.SealedRequest(
            rid=f"P-{pair}-20251102T2200Z",
            pair="USDCAD" if broken and pair == "USDJPY" else pair,
            stage=probe.SEALED_STAGE_PERIODIC,
            api_from=start + timedelta(hours=2),
            api_to=end + timedelta(hours=2),
            utc_from=start,
            utc_to=end,
            offset_from_hours=2,
            offset_to_hours=2,
            file=f"{pair}/{pair}_20251102T2200Z-20251109T2200Z_m1.csv.gz",
        ).payload()
        for pair in probe.SEALED_PAIRS
    ]
    payload = {
        "kind": probe.SEALED_PLAN_KIND,
        "schema": 1,
        "stage": probe.SEALED_STAGE_PERIODIC,
        "window_config_sha256": window.sha256,
        "time_rule_table": {"id": "t", "version": 1, "sha256": "0" * 64, "variant": "standard"},
        "window": {"utc_from": start.isoformat(), "utc_to": end.isoformat()},
        "planned_requests": len(requests),
        "max_requests": probe.sealed_max_requests(len(requests)),
        "requests": requests,
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_in(repo: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "tools/survey/aws_mt5_probe/39_sealed_fetch.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _sealed_env(tmp_path: Path, scenario: dict[str, object]) -> dict[str, str]:
    env = _env(tmp_path, scenario)
    env["REPO_PY"] = sys.executable  # 計画の照合は本物の Python で行う
    env["SEALED_ROOT"] = str(tmp_path / "oanda_mt5_sealed")
    return env


@needs_bash
def test_dry_run_checks_the_plan_and_creates_nothing(tmp_path: Path) -> None:
    env = _sealed_env(tmp_path, {})
    _state(tmp_path, "sealed-periodic")
    repo = _repo(tmp_path)
    r = _run_in(repo, ["--plan", str(_plan(tmp_path, repo))], env)
    assert r.returncode == 0, r.stderr
    assert "要求 8、上限 9" in r.stderr and "(dry-run) 何もしていない" in r.stderr
    called = {" ".join(c[:2]) for c in _calls(tmp_path)}
    assert not any(c.startswith(("s3", "ssm")) for c in called)


@needs_bash
def test_a_plan_outside_the_window_stops_before_any_aws_call(tmp_path: Path) -> None:
    env = _sealed_env(tmp_path, {})
    _state(tmp_path, "sealed-periodic")
    repo = _repo(tmp_path)
    r = _run_in(repo, ["--plan", str(_plan(tmp_path, repo, broken=True)), "--execute"], env)
    assert r.returncode != 0
    assert "計画が窓の設定と合わない" in r.stderr
    assert _calls(tmp_path) == []


@needs_bash
@pytest.mark.parametrize("kind", ["fullfetch", "sealed-bulk"])
def test_a_launch_of_another_kind_refuses_the_plan(tmp_path: Path, kind: str) -> None:
    env = _sealed_env(tmp_path, {})
    _state(tmp_path, kind)
    repo = _repo(tmp_path)
    r = _run_in(repo, ["--plan", str(_plan(tmp_path, repo)), "--execute"], env)
    assert r.returncode != 0
    assert "封印の取得の起動ではない" in r.stderr or "起動の種類" in r.stderr
    assert not any(c[0] in ("s3", "s3api", "ssm") for c in _calls(tmp_path))


@needs_bash
def test_the_run_kind_in_the_environment_must_match_the_state(tmp_path: Path) -> None:
    env = _sealed_env(tmp_path, {})
    env["PROBE_RUN_KIND"] = "sealed-bulk"
    _state(tmp_path, "sealed-periodic")
    repo = _repo(tmp_path)
    r = _run_in(repo, ["--plan", str(_plan(tmp_path, repo))], env)
    assert r.returncode != 0 and "が違う" in r.stderr


@needs_bash
def test_execute_records_a_sealed_bucket_with_its_plan(tmp_path: Path) -> None:
    env = _sealed_env(tmp_path, {"fail_send": True})
    _state(tmp_path, "sealed-periodic")
    repo = _repo(tmp_path)
    plan = _plan(tmp_path, repo)
    r = _run_in(repo, ["--plan", str(plan), "--execute"], env)
    assert r.returncode != 0  # 偽の aws は ssm send-command を拒むので、計画を置く段で止まる
    s3env = (_state_dir(tmp_path) / "s3_bucket.env").read_text(encoding="utf-8")
    assert "S3_PREFIX=sealed" in s3env and f"SEALED_PLAN={plan}" in s3env
    assert "FETCH_DATE=2026-11-14" in s3env
    puts = [
        "\n".join(json.loads(p.read_text(encoding="utf-8"))["commands"])
        for p in sorted((tmp_path / "fake_aws").glob("ssm_*.json"))
    ]
    assert any("sealed_plan.json.b64" in text for text in puts)  # 計画を Windows に置く段まで進んだ
    assert any(c[:2] == ["s3api", "create-bucket"] for c in _calls(tmp_path))
