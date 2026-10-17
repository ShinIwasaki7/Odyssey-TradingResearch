"""取得道具（tools/survey/aws_mt5_probe）の、全期間取得（D12 付録 K-17）で問題になった
分岐の回帰テスト。

付録 K-17.4・K-17.6 の 3 件を、偽の ``aws`` コマンド（``PATH`` の先頭に置く。AWS には
一切つながない）で実際に通す。

1. ``38_full_fetch.sh`` のバケット名の乱数の行が、``set -euo pipefail`` の下で止まらないこと
   （以前は終了コード 141）。
2. ``90_teardown.sh`` が、消したバケットの 404 を自分で待って確かめること（以前は
   ``s3api wait bucket-not-exists`` が「Max attempts exceeded」で止まった）。403 を「無い」と
   扱わないこと。
3. ``20_prepare.sh`` が AWS CLI v2 の導入を SSM で送ること。PowerShell の分岐（入っていれば
   入れない・SHA-256・署名・msiexec）は ``pwsh`` があるとき（GitHub Actions の ubuntu-latest
   には入っている）だけ、偽の ``Invoke-WebRequest``・``Get-FileHash``・
   ``Get-AuthenticodeSignature``・``Start-Process``（msiexec）で通す。

実環境（AWS の S3 の応答、Windows Server の PowerShell 5.1・msiexec・Authenticode）での
確認ではない。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "tools" / "survey" / "aws_mt5_probe"
BUCKET = "odyssey-mt5-probe-2026-10-16-abcd1234"
BUCKET_RE = re.compile(r"^odyssey-mt5-probe-2026-10-16-[a-z0-9]{8}$")
MSI_SHA256 = "ad07f9acd855f48ad016e63b2f0b4f87783c1b6524b86e966f4a6d21d01f524a"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")

# 偽の aws。呼び出しを calls.jsonl に記録し、scenario.json の指定で応答する。
# head-bucket は scenario の "head" の列を 1 回ずつ使い、最後の値を繰り返す（"200"・"403"・"404"）。
FAKE_AWS = r"""
import json, os, sys
from pathlib import Path

d = Path(os.environ["FAKE_AWS_DIR"])
argv = sys.argv[1:]
args, i = [], 0
while i < len(argv):
    if argv[i] in ("--profile", "--region"):
        i += 2
        continue
    args.append(argv[i])
    i += 1
with (d / "calls.jsonl").open("a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
sc = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
cmd = " ".join(args[:2])


def opt(name):
    return args[args.index(name) + 1] if name in args else None


if cmd == "configure list-profiles":
    print("odyssey-mt5-probe")
elif args[0] == "ec2":
    q = opt("--query") or ""
    if q.startswith("length("):
        print(0)
    elif cmd == "ec2 describe-instances" and "--instance-ids" in args:
        print("203.0.113.10" if "PublicIpAddress" in q else "odyssey-mt5-probe")
    else:
        print("")
elif cmd == "s3api create-bucket":
    print("{}")
elif cmd == "s3api head-bucket":
    seq = sc.get("head", ["404"])
    n = int((d / "head_n").read_text()) if (d / "head_n").exists() else 0
    (d / "head_n").write_text(str(n + 1))
    code = seq[min(n, len(seq) - 1)]
    if code == "200":
        print("{}")
    else:
        text = {"403": "Forbidden", "404": "Not Found"}[code]
        msg = f"An error occurred ({code}) when calling the HeadBucket operation: {text}"
        print("\n" + msg, file=sys.stderr)
        sys.exit(254)
elif cmd == "s3api wait":
    # 付録 K-17.6 の失敗の再現（以前の 90_teardown.sh が呼んでいた）
    print("\nWaiter BucketNotExists failed: Max attempts exceeded", file=sys.stderr)
    sys.exit(255)
elif cmd == "s3 cp":
    Path(args[3]).write_bytes((d / "manifest.json").read_bytes())
elif cmd == "s3api list-objects-v2":
    print("full/manifest.json")
elif cmd in ("s3 rm", "s3api delete-bucket"):
    pass
elif cmd == "ssm describe-instance-information":
    print("Online")
elif cmd == "ssm send-command":
    n = len(list(d.glob("ssm_*.json")))
    (d / f"ssm_{n:03d}.json").write_text(opt("--parameters"), encoding="utf-8")
    if sc.get("fail_send"):
        print("fake: send-command refused", file=sys.stderr)
        sys.exit(2)
    print(f"cmd-{n}")
elif cmd == "ssm get-command-invocation":
    q = opt("--query")
    print({"Status": "Success", "StandardOutputContent": "ok"}.get(q, "None"))
else:
    print("unexpected aws call: " + " ".join(args), file=sys.stderr)
    sys.exit(2)
"""


def _env(tmp_path: Path, scenario: dict[str, object]) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    aws = bin_dir / "aws"
    aws.write_text(f"#!{sys.executable}\n{FAKE_AWS}", encoding="utf-8")
    aws.chmod(0o755)
    sleep = bin_dir / "sleep"  # ssm_ps の 3 秒の待ちなどを飛ばす
    sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    stub = bin_dir / "repo_py_stub"  # 90 の回収の照合（fullverify）の代わり。必ず成功する
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    fake = tmp_path / "fake_aws"
    fake.mkdir()
    (fake / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    (fake / "manifest.json").write_bytes(b'{"fake": true}\n')
    (tmp_path / "probe_root" / "_state").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    env = dict(os.environ)
    env.pop("MT5_INSTALLER_URL", None)
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        HOME=str(tmp_path / "home"),
        FAKE_AWS_DIR=str(fake),
        RESULT_ROOT=str(tmp_path / "probe_root"),
        FULL_ROOT=str(tmp_path / "oanda_mt5"),
        REPO_PY=str(stub),
        PY_MAC=sys.executable,
        S3_DELETE_WAIT_SECONDS="0",
    )
    return env


def _calls(tmp_path: Path) -> list[list[str]]:
    path = tmp_path / "fake_aws" / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def _state_dir(tmp_path: Path) -> Path:
    return tmp_path / "probe_root" / "_state"


def _write_state(tmp_path: Path) -> None:
    (_state_dir(tmp_path) / "state.env").write_text(
        f"INSTANCE_ID=i-0123\nLAUNCHED_AT_EPOCH={int(datetime.now(tz=UTC).timestamp())}\n"
        "LAUNCHED_AT_UTC=2026-10-16T00:00:00Z\nPROBE_DATE=2026-10-16\nRUN_KIND=fullfetch\n"
        "KEY_FILE=/nonexistent/key.pem\n",
        encoding="utf-8",
    )


def _run(script: str, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


# --- 1. 38_full_fetch.sh のバケット名の乱数（付録 K-17.4 の 2） ---------------------------------


@needs_bash
def test_random_suffix_succeeds_under_pipefail_and_passes_the_bucket_guard(tmp_path: Path) -> None:
    env = _env(tmp_path, {})
    lib = SCRIPTS / "lib.sh"
    # lib.sh は set -euo pipefail を有効にする。その下で 50 回作り、
    # どれも名前の規則を通ることを確かめる。
    script = (
        f'source "{lib}"\n'
        "[[ $- == *e* ]] && shopt -qo pipefail || exit 99\n"
        "for _ in $(seq 1 50); do\n"
        "  s=$(random_suffix8)\n"
        '  b="${S3_BUCKET_PREFIX}2026-10-16-${s}"\n'
        '  assert_probe_bucket "$b"\n'
        '  echo "$b"\n'
        "done\n"
    )
    r = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, check=False, timeout=60
    )
    assert r.returncode == 0, (r.returncode, r.stderr)
    names = r.stdout.split()
    assert len(names) == 50
    assert all(BUCKET_RE.match(n) for n in names)
    assert len(set(names)) > 1  # 乱数であること（50 回がすべて同じにはならない）


@needs_bash
def test_old_random_line_fails_with_sigpipe_under_pipefail(tmp_path: Path) -> None:
    """以前の行が pipefail で 141 になることの確認（このテストが失敗の原因を固定している）。"""
    r = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            "x=\"$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 8)\"\n"
            'echo "reached $x"\n',
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert r.returncode == 141
    assert "reached" not in r.stdout


@needs_bash
def test_full_fetch_execute_creates_a_valid_bucket_without_sigpipe(tmp_path: Path) -> None:
    """--execute でバケットの作成まで実際に通す（バケットの記録が無い状態。付録 K-17.4 の 2）。

    偽の aws は ssm send-command を拒むので、スクリプトはバケットの作成の後、
    最初の SSM の呼び出しで止まる。
    """
    env = _env(tmp_path, {"fail_send": True})
    _write_state(tmp_path)
    r = _run("38_full_fetch.sh", ["--pairs", "USDJPY", "--execute"], env)
    assert r.returncode not in (0, 141), (r.returncode, r.stderr)  # 141 なら乱数の行で止まった
    creates = [c for c in _calls(tmp_path) if c[:2] == ["s3api", "create-bucket"]]
    assert len(creates) == 1, r.stderr
    name = creates[0][creates[0].index("--bucket") + 1]
    assert BUCKET_RE.match(name)
    assert ["--create-bucket-configuration", "LocationConstraint=ap-northeast-1"] == creates[0][-2:]
    s3env = (_state_dir(tmp_path) / "s3_bucket.env").read_text(encoding="utf-8")
    assert f"S3_BUCKET={name}" in s3env and "FETCH_DATE=2026-10-16" in s3env
    assert "S3 の一時バケットを作った" in r.stderr
    assert any(c[:2] == ["ssm", "send-command"] for c in _calls(tmp_path))  # 作成の後の段まで進んだ


# --- 2. 90_teardown.sh の S3 の削除の確認（付録 K-17.6） --------------------------------------


def _teardown_env(tmp_path: Path, head: list[str], attempts: int = 5) -> dict[str, str]:
    env = _env(tmp_path, {"head": head})
    env["S3_DELETE_WAIT_ATTEMPTS"] = str(attempts)
    sha = hashlib.sha256((tmp_path / "fake_aws" / "manifest.json").read_bytes()).hexdigest()
    (_state_dir(tmp_path) / "s3_bucket.env").write_text(
        f"S3_BUCKET={BUCKET}\nFETCH_DATE=2026-10-16\nCOLLECTED_MANIFEST_SHA256={sha}\n",
        encoding="utf-8",
    )
    return env


def _archived(tmp_path: Path) -> bool:
    state = _state_dir(tmp_path)
    return not (state / "s3_bucket.env").exists() and any(state.glob("s3_bucket.env.done-*"))


@needs_bash
def test_teardown_waits_for_404_after_delete_and_retries_through_403(tmp_path: Path) -> None:
    # 1 回目（消す前）は 200、消した後は 403・200・404 の順。404 で成功とし、記録を退避する。
    env = _teardown_env(tmp_path, ["200", "403", "200", "404"])
    r = _run("90_teardown.sh", ["--execute"], env)
    assert r.returncode == 0, r.stderr
    calls = _calls(tmp_path)
    assert ["s3api", "delete-bucket", "--bucket", BUCKET] in calls
    # 以前の待ち（Max attempts exceeded で止まった）は使わない
    assert not any(c[:2] == ["s3api", "wait"] for c in calls)
    heads = [c for c in calls if c[:2] == ["s3api", "head-bucket"]]
    # 消す前 1、待ち 3（403・200・404）。残りの確認は記録を退避した後なので head-bucket を呼ばない
    assert len(heads) == 1 + 3
    assert "（1/5 回目）: unknown: An error occurred (403)" in r.stderr
    assert "（2/5 回目）: exists" in r.stderr
    assert "無いことを確かめた（head-bucket が 404。3/5 回目）" in r.stderr
    assert "消した: S3 の一時バケット" in r.stderr
    assert _archived(tmp_path)
    assert "== 残りの確認" in r.stdout


@needs_bash
def test_teardown_stops_after_the_retry_limit_and_keeps_the_record(tmp_path: Path) -> None:
    env = _teardown_env(tmp_path, ["200", "403"], attempts=3)
    r = _run("90_teardown.sh", ["--execute"], env)
    assert r.returncode == 1, r.stderr
    assert "3 回確かめても 404 にならない" in r.stderr
    assert "（3/3 回目）" in r.stderr and "（4/3 回目）" not in r.stderr
    assert (_state_dir(tmp_path) / "s3_bucket.env").exists()  # もう一度実行できるよう記録を残す
    # 失敗でも残りの確認を表示する。バケットは 403 なので「不明」と出す（0 とは言わない）
    assert "== 残りの確認" in r.stdout
    assert f"試験の S3 の一時バケット: 不明（{BUCKET}" in r.stdout


@needs_bash
def test_teardown_rerun_with_404_archives_without_deleting(tmp_path: Path) -> None:
    # 付録 K-17.6 の 2 回目の実行と同じ状況: バケットは既に無い（404）。
    # 消す操作をせずに記録を退避する。
    env = _teardown_env(tmp_path, ["404"])
    r = _run("90_teardown.sh", ["--execute"], env)
    assert r.returncode == 0, r.stderr
    calls = _calls(tmp_path)
    assert not any(c[:2] in (["s3", "rm"], ["s3api", "delete-bucket"]) for c in calls)
    assert "head-bucket が 404。消し済み" in r.stderr
    assert _archived(tmp_path)


@needs_bash
def test_teardown_does_not_treat_403_as_deleted(tmp_path: Path) -> None:
    # 以前は head-bucket の失敗（403 を含む）をすべて「消し済み」と扱い、記録を退避していた。
    env = _teardown_env(tmp_path, ["403"])
    r = _run("90_teardown.sh", ["--execute"], env)
    assert r.returncode == 1
    assert "有無を確かめられない" in r.stderr
    assert (_state_dir(tmp_path) / "s3_bucket.env").exists()
    assert not any(c[:2] in (["s3", "rm"], ["s3api", "delete-bucket"]) for c in _calls(tmp_path))
    assert "== 残りの確認" in r.stdout


@needs_bash
def test_teardown_verify_reports_404_as_zero(tmp_path: Path) -> None:
    env = _teardown_env(tmp_path, ["404"])
    r = _run("90_teardown.sh", ["--verify"], env)
    assert r.returncode == 0, r.stderr
    assert f"試験の S3 の一時バケット: 0（{BUCKET} は無い。head-bucket が 404）" in r.stdout


# --- 3. 20_prepare.sh の AWS CLI v2 の導入（付録 K-17.4 の 1） ----------------------------------


def _prepare_scripts(tmp_path: Path) -> list[str]:
    """20_prepare.sh --execute が SSM で送った PowerShell（送った順）。"""
    out = []
    for p in sorted((tmp_path / "fake_aws").glob("ssm_*.json")):
        out.append("\n".join(json.loads(p.read_text(encoding="utf-8"))["commands"]))
    return out


def _awscli_ps(tmp_path: Path) -> str:
    env = _env(tmp_path, {})
    _write_state(tmp_path)
    r = _run("20_prepare.sh", ["--execute"], env)
    assert r.returncode == 0, r.stderr
    found = [s for s in _prepare_scripts(tmp_path) if "AWSCLIV2" in s]
    assert len(found) == 1, "AWS CLI の導入のスクリプトがちょうど 1 回送られる"
    assert "AWS CLI v2 を入れる" in r.stderr
    return found[0]


@needs_bash
def test_prepare_sends_the_awscli_install_with_pinned_url_hash_and_signature(
    tmp_path: Path,
) -> None:
    ps = _awscli_ps(tmp_path)
    assert "https://awscli.amazonaws.com/AWSCLIV2-2.38.1.msi" in ps
    assert f"if ($h -ne '{MSI_SHA256}')" in ps
    assert "Get-AuthenticodeSignature" in ps and "'Valid'" in ps
    assert "O=Amazon Web Services, Inc." in ps
    assert r"$cli = 'C:\Program Files\Amazon\AWSCLIV2\aws.exe'" in ps
    # 順序: 入っているかの確認 → 取得 → SHA-256 → 署名 → msiexec
    order = [
        "Test-Path -LiteralPath $cli",
        "Invoke-WebRequest",
        "Get-FileHash",
        "Get-AuthenticodeSignature",
        "msiexec.exe",
    ]
    pos = [ps.index(x) for x in order]
    assert pos == sorted(pos)


@needs_bash
def test_prepare_dry_run_shows_the_awscli_step_and_sends_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path, {})
    _write_state(tmp_path)
    r = _run("20_prepare.sh", [], env)
    assert r.returncode == 0, r.stderr
    assert "AWS CLI v2" in r.stderr and MSI_SHA256 in r.stderr
    assert "  6. MT5 の端末: 入れない" in r.stderr
    assert not any(c[0] == "ssm" for c in _calls(tmp_path))


# PowerShell の分岐を偽の部品で通す（pwsh があるときだけ）。
# Windows のパスを一時フォルダに置き換える。
MOCKS_PS = r"""
function Invoke-WebRequest { param($Uri, $OutFile, [switch]$UseBasicParsing)
  Add-Content -LiteralPath $env:MOCK_LOG "download $Uri"
  Set-Content -LiteralPath $OutFile -Value 'fake msi' }
function Get-FileHash { param($Algorithm, $LiteralPath)
  Add-Content -LiteralPath $env:MOCK_LOG "hash $Algorithm"
  [pscustomobject]@{ Hash = $env:MOCK_HASH } }
function Get-AuthenticodeSignature { param($FilePath)
  Add-Content -LiteralPath $env:MOCK_LOG "signature"
  [pscustomobject]@{ Status = $env:MOCK_SIG_STATUS;
    SignerCertificate = [pscustomobject]@{ Subject = $env:MOCK_SIG_SUBJECT } } }
function Start-Process { param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru)
  Add-Content -LiteralPath $env:MOCK_LOG ("install " + $FilePath + " " + ($ArgumentList -join ' '))
  if ($env:MOCK_MSI_EXIT -eq '0') {
    Set-Content -LiteralPath $env:MOCK_CLI -Value "#!/bin/sh`necho '$env:MOCK_VERSION'"
    & chmod +x $env:MOCK_CLI }
  [pscustomobject]@{ ExitCode = [int]$env:MOCK_MSI_EXIT } }
"""

needs_pwsh = pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh が無い")
VERSION_OUT = "aws-cli/2.38.1 Python/3.13 Windows/2022"


def _run_awscli_ps(
    tmp_path: Path,
    *,
    installed: bool = False,
    hash_: str = MSI_SHA256,
    status: str = "Valid",
    subject: str = 'CN="Amazon Web Services, Inc.", O="Amazon Web Services, Inc.", C=US',
    msi_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    ps = _awscli_ps(tmp_path / "prep")
    win = tmp_path / "win"
    (win / "probe").mkdir(parents=True)
    (win / "cli").mkdir()
    cli = win / "cli" / "aws.exe"
    ps = ps.replace(r"C:\Program Files\Amazon\AWSCLIV2\aws.exe", str(cli))
    ps = ps.replace(r"C:\probe\AWSCLIV2.msi", str(win / "probe" / "AWSCLIV2.msi"))
    assert "C:\\" not in ps
    if installed:
        cli.write_text(f"#!/bin/sh\necho '{VERSION_OUT}'\n", encoding="utf-8")
        cli.chmod(0o755)
    (win / "mocks.ps1").write_text(MOCKS_PS, encoding="utf-8")
    (win / "install.ps1").write_text(ps, encoding="utf-8")
    log = win / "mock.log"
    log.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        MOCK_LOG=str(log),
        MOCK_HASH=hash_.upper(),  # Windows の Get-FileHash は大文字。スクリプトが小文字にして比べる
        MOCK_SIG_STATUS=status,
        MOCK_SIG_SUBJECT=subject,
        MOCK_MSI_EXIT=str(msi_exit),
        MOCK_CLI=str(cli),
        MOCK_VERSION=VERSION_OUT,
    )
    r = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f". '{win / 'mocks.ps1'}'; . '{win / 'install.ps1'}'",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return r, log.read_text(encoding="utf-8").splitlines()


@needs_bash
@needs_pwsh
def test_awscli_ps_installs_after_hash_and_signature_checks(tmp_path: Path) -> None:
    r, log = _run_awscli_ps(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert log[0] == "download https://awscli.amazonaws.com/AWSCLIV2-2.38.1.msi"
    assert log[1:3] == ["hash SHA256", "signature"]
    assert log[3].startswith("install msiexec.exe /i ") and log[3].endswith(" /qn /norestart")
    assert "aws-cli: installed: aws-cli/2.38.1" in r.stdout


@needs_bash
@needs_pwsh
def test_awscli_ps_skips_when_already_installed(tmp_path: Path) -> None:
    r, log = _run_awscli_ps(tmp_path, installed=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert log == []
    assert "aws-cli: already installed, skip: aws-cli/2.38.1" in r.stdout


@needs_bash
@needs_pwsh
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"hash_": "0" * 64}, "SHA-256 mismatch"),
        ({"status": "HashMismatch"}, "signature status: HashMismatch"),
        ({"subject": "CN=Someone Else, O=Someone Else"}, "signer: CN=Someone Else"),
    ],
)
def test_awscli_ps_refuses_to_install_an_unverified_msi(
    tmp_path: Path, kwargs: dict[str, Any], message: str
) -> None:
    r, log = _run_awscli_ps(tmp_path, **kwargs)
    assert r.returncode != 0
    assert message in r.stdout + r.stderr
    assert not any(x.startswith("install ") for x in log)


@needs_bash
@needs_pwsh
def test_awscli_ps_fails_when_msiexec_fails(tmp_path: Path) -> None:
    r, log = _run_awscli_ps(tmp_path, msi_exit=1603)
    assert r.returncode != 0
    assert "msiexec exit 1603" in r.stdout + r.stderr
