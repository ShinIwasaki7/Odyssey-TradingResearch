#!/usr/bin/env bash
# 段階 B: SSM で Windows に Python 3.12・MetaTrader5 のパッケージを入れ、試験の道具を C:\probe\ に置く。
# MT5 の端末は、OANDA証券の公式の入手先がログイン後のマイページだけなので、既定では人間が RDP の中で入れる
# （付録 K-2）。人間がマイページで得たインストーラーの URL を MT5_INSTALLER_URL に渡したときだけ、ここで入れる。
#
# 使い方: tools/survey/aws_mt5_probe/20_prepare.sh [--execute]
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
require_profile
load_state
check_deadline
assert_tagged_instance "$INSTANCE_ID"

tool_sha=$(sha256_of "$PROBE_TOOL")
cat >&2 <<INFO
== 準備の計画（${INSTANCE_ID}）
  1. SSM の接続を待つ
  2. Python のインストーラー（${PY_INSTALLER_URL}）を取り、SHA-256 ${PY_INSTALLER_SHA256} を確かめ、全ユーザーに入れる
  3. pip で ${MT5_PY_PACKAGE} を入れ、import できるかを確かめる（端末にはつながない）
  4. 試験の道具 mt5_api_probe.py（SHA-256 ${tool_sha}）を C:\\probe\\ に置き、同じ SHA-256 かを確かめる
  5. MT5 の端末: ${MT5_INSTALLER_URL:-入れない（人間が RDP の中でマイページから入れる）}
INFO
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }

wait_ssm_online "$INSTANCE_ID"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cat >"$work/py.ps1" <<PS
\$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path C:\\probe, C:\\probe\\out | Out-Null
if (Test-Path '${PY_EXE}') {
  & '${PY_EXE}' -c "import MetaTrader5 as m; print('already installed: MetaTrader5', m.__version__)"
  if (\$LASTEXITCODE -eq 0) { exit 0 }
}
\$inst = 'C:\\probe\\python-installer.exe'
Invoke-WebRequest -UseBasicParsing -Uri '${PY_INSTALLER_URL}' -OutFile \$inst
\$h = (Get-FileHash -Algorithm SHA256 \$inst).Hash.ToLower()
if (\$h -ne '${PY_INSTALLER_SHA256}') { throw "Python installer SHA-256 mismatch: \$h" }
\$p = Start-Process -FilePath \$inst -ArgumentList '/quiet','InstallAllUsers=1','PrependPath=1','Include_test=0' -Wait -PassThru
if (\$p.ExitCode -ne 0) { throw "python installer exit \$(\$p.ExitCode)" }
& '${PY_EXE}' --version
& '${PY_EXE}' -m pip install --disable-pip-version-check '${MT5_PY_PACKAGE}'
if (\$LASTEXITCODE -ne 0) { throw "pip exit \$LASTEXITCODE" }
& '${PY_EXE}' -c "import MetaTrader5 as m; print('MetaTrader5', m.__version__)"
if (\$LASTEXITCODE -ne 0) { throw "import MetaTrader5 failed" }
PS
log "Python と MetaTrader5 のパッケージを入れる"
ssm_ps "$INSTANCE_ID" 1800 "$work/py.ps1"

# 道具を base64 で分けて送る（SSM の引数の大きさの制限のため）。
log "試験の道具を置く"
base64 <"$PROBE_TOOL" | tr -d '\n' | fold -w 6000 >"$work/tool.b64"
first=1
# 最後の行は改行で終わらないので、|| [[ -n ]] で取りこぼさない。
while IFS= read -r chunk || [[ -n "$chunk" ]]; do
  if [[ $first == 1 ]]; then op="Set-Content"; first=0; else op="Add-Content"; fi
  printf "%s -NoNewline -Encoding ascii -Path C:\\\\probe\\\\mt5_api_probe.b64 -Value '%s'\n" "$op" "$chunk" >"$work/chunk.ps1"
  ssm_ps "$INSTANCE_ID" 120 "$work/chunk.ps1" >/dev/null
done <"$work/tool.b64"
cat >"$work/decode.ps1" <<PS
\$ErrorActionPreference = 'Stop'
\$b = [Convert]::FromBase64String((Get-Content -Raw C:\\probe\\mt5_api_probe.b64))
[IO.File]::WriteAllBytes('C:\\probe\\mt5_api_probe.py', \$b)
Remove-Item C:\\probe\\mt5_api_probe.b64
\$h = (Get-FileHash -Algorithm SHA256 C:\\probe\\mt5_api_probe.py).Hash.ToLower()
if (\$h -ne '${tool_sha}') { throw "probe SHA-256 mismatch: \$h" }
"probe sha256 ok: \$h"
\$env:PYTHONUTF8 = '1'
& '${PY_EXE}' C:\\probe\\mt5_api_probe.py --help
PS
ssm_ps "$INSTANCE_ID" 120 "$work/decode.ps1"

if [[ -n "${MT5_INSTALLER_URL:-}" ]]; then
  log "MT5 の端末を入れる（入手元: ${MT5_INSTALLER_URL}）"
  cat >"$work/mt5.ps1" <<PS
\$ErrorActionPreference = 'Stop'
Invoke-WebRequest -UseBasicParsing -Uri '${MT5_INSTALLER_URL}' -OutFile C:\\probe\\mt5setup.exe
"mt5setup sha256: " + (Get-FileHash -Algorithm SHA256 C:\\probe\\mt5setup.exe).Hash.ToLower()
\$p = Start-Process -FilePath C:\\probe\\mt5setup.exe -ArgumentList '/auto' -Wait -PassThru
"mt5setup exit: " + \$p.ExitCode
Get-ChildItem 'C:\\Program Files' -Directory | Where-Object { Test-Path (Join-Path \$_.FullName 'terminal64.exe') } | ForEach-Object { \$_.FullName }
PS
  ssm_ps "$INSTANCE_ID" 1200 "$work/mt5.ps1"
fi

ip=$(aws_p ec2 describe-instances --instance-ids "$INSTANCE_ID" --query "Reservations[0].Instances[0].PublicIpAddress" --output text)
cat <<NEXT
== 準備が終わった。人間に伝えること（パスワードはチャットに書かない）
  RDP の接続先: ${ip}（ユーザー名 Administrator）
  パスワードは人間が Mac で次を実行して出す（キーの秘密鍵で復号する）:
    aws ec2 get-password-data --instance-id ${INSTANCE_ID} --priv-launch-key ${KEY_FILE} \\
      --profile ${PROBE_PROFILE} --region ${PROBE_REGION} --query PasswordData --output text
NEXT
