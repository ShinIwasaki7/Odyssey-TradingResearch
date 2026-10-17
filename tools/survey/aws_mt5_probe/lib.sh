#!/usr/bin/env bash
# shellcheck disable=SC2034  # 定数は読み込む側のスクリプトで使う。
# AWS 上の Windows で MT5 API の小規模な試験をするスクリプト群の共通部分（D12 §11.5、付録 K）。
# 各スクリプトの冒頭で `source "$(dirname "$0")/lib.sh"` する。単独では実行しない。
#
# 守ること:
# - AWS CLI は必ずプロファイル odyssey-mt5-probe と東京リージョンで呼ぶ（aws_p を使う）。
# - 認証情報のファイルの中身を読まない・表示しない。キー・パスワードをログに書かない。
# - 作るものには必ずタグ Project=odyssey-mt5-probe を付け、消すのもこのタグのものだけ。
# - 2024-01-01T00:00Z 以降の価格を要求しない（要求は mt5_api_probe.py の段の名前でだけ指定する）。

set -euo pipefail

# --- 固定値（変えない） ---------------------------------------------------------
readonly PROBE_PROFILE="odyssey-mt5-probe"
readonly PROBE_REGION="ap-northeast-1"
readonly PROBE_TAG_KEY="Project"
readonly PROBE_TAG_VALUE="odyssey-mt5-probe"
readonly PROBE_NAME="odyssey-mt5-probe"
readonly PROBE_INSTANCE_TYPE="t3.medium"              # D12-22 (a)
readonly PROBE_ROOT_GB=60                             # gp3、終了時に削除。上限 60 GB（人間の第 10 回の決定）。拡張しない
readonly PROBE_AMI_NAME_PATTERN="Windows_Server-2022-English-Full-Base-*"
readonly PROBE_SSM_ROLE="odyssey-mt5-probe-ssm-role"  # 人間が作る（付録 K-1）。インスタンスプロファイルも同名
readonly PROBE_RDP_CIDR="${PROBE_RDP_CIDR:-126.221.39.9/32}"   # 人間の IP（司令塔が伝えた値）
# 作業上限は全期間取得（付録 K-16.6。人間の第 18 回の決定「案のとおり」で承認）の値。
# 前回（最後の調査。付録 K-10.3）は 2 時間 30 分・2 時間・1 時間 30 分、その前（付録 K-8・K-9）は
# 4 時間 30 分・4 時間・3 時間だった。
readonly PROBE_SHUTDOWN_SECONDS=16200                 # 4 時間 30 分（Windows の中の自動の停止。止め忘れの備え）
readonly PROBE_DEADLINE_SECONDS=14400                 # 4 時間（作業役が必ず終了する時刻。95_deadline_guard.sh と check_deadline）
readonly FULL_TIME_LIMIT_SECONDS=9000                 # 2 時間 30 分（要求の打ち切り。この後は新しい要求を出さず、回収に移る）
readonly FULL_MAX_REQUESTS=750                        # 要求の上限（計画 672。残り 78 は 0 本の月のやり直しだけ。道具の側でも数える）
readonly FINAL_TIME_LIMIT_SECONDS=5400                # 最後の調査（付録 K-10）の打ち切り 1 時間 30 分（前回の段。今回は使わない）
readonly TS_TIME_LIMIT_SECONDS=10800                  # 時刻の調査（D12 §11.7）の作業上限 3 時間（前回の段。今回は使わない）
# この起動の種類。fullfetch は全期間取得（付録 K-16）だけを行う起動で、他の段（30・35・36・37）は動かない。
readonly PROBE_RUN_KIND="fullfetch"
# 回収（SSM の出力と S3 から受け取る分）の合計の上限 1 GB（付録 K-16.6）。道具で数えられる分。
readonly PROBE_COLLECT_LIMIT_BYTES=1073741824
# 全期間取得の回収に使う S3 の一時バケット（人間の第 18 回の決定 (A)）。名前は
# odyssey-mt5-probe-<取得日>-<乱数 8 桁>。IAM で許すのはこの接頭辞のバケットだけ（iam_policy_*.json）。
readonly S3_BUCKET_PREFIX="${PROBE_NAME}-"
readonly S3_KEY_PREFIX="full"
# S3 の一時バケットを消した後、消えた（head-bucket が 404）と確かめるまでの待ち方（90_teardown.sh）。
# 付録 K-17.6: `s3api wait bucket-not-exists`（5 秒 × 20 回）は「Max attempts exceeded」で止まったが、
# 直後の head-bucket は 404 だった。間隔 10 秒 × 30 回（5 分）に延ばし、毎回の応答を記録する。
readonly S3_DELETE_WAIT_SECONDS="${S3_DELETE_WAIT_SECONDS:-10}"
readonly S3_DELETE_WAIT_ATTEMPTS="${S3_DELETE_WAIT_ATTEMPTS:-30}"

# Windows に入れるもの（版と SHA-256 を固定。2026-10-15 に作業役が python.org と PyPI から確認）
readonly PY_INSTALLER_URL="https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
readonly PY_INSTALLER_SHA256="67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
readonly PY_EXE='C:\Program Files\Python312\python.exe'
readonly MT5_PY_PACKAGE="MetaTrader5==5.0.6231"       # cp312 の wheel の SHA-256 は README に記録
# AWS CLI v2（Windows から S3 に置く道具。付録 K-17.4 の 1）。AWS Tools for PowerShell は読み込みに 300 秒超かかり、
# 38_full_fetch.sh の S3 の道具の確認（120 秒）に間に合わないため、20_prepare.sh で入れる。
# 版を固定した公式の配布元（版なしの AWSCLIV2.msi は中身が変わる）。SHA-256 は付録 K-17.4 の 1 で Windows に入れた
# MSI（aws-cli/2.38.1）の値と同じ（2026-10-17 に作業役が Mac でこの URL から取得して計算し、一致を確かめた）。
readonly AWSCLI_VERSION="2.38.1"
readonly AWSCLI_MSI_URL="https://awscli.amazonaws.com/AWSCLIV2-${AWSCLI_VERSION}.msi"
readonly AWSCLI_MSI_SHA256="ad07f9acd855f48ad016e63b2f0b4f87783c1b6524b86e966f4a6d21d01f524a"
readonly AWSCLI_MSI_WIN='C:\probe\AWSCLIV2.msi'
readonly AWSCLI_EXE='C:\Program Files\Amazon\AWSCLIV2\aws.exe'
# Authenticode の署名者（証明書の Subject の O=）。付録 K-17.4 の 1 で確かめた値。
readonly AWSCLI_SIGNER_ORG="Amazon Web Services, Inc."

# 単価（米ドル。D12 §11.5.2 と同じ AWS の公開の価格表、2026-10-12 公開分）
readonly PRICE_INSTANCE_HOUR="0.0728"
readonly PRICE_GP3_GB_MONTH="0.096"
readonly PRICE_IPV4_HOUR="0.005"
readonly PRICE_EGRESS_GB="0.114"
readonly PRICE_CREDIT_VCPU_HOUR="0.096"
readonly TAX_RATE="0.10"

# --- 置き場 -------------------------------------------------------------------
PROBE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROBE_SCRIPT_DIR
readonly PROBE_TOOL="${PROBE_SCRIPT_DIR}/../mt5_api_probe.py"
# 結果の置き場（D12-25 (a)）。リポジトリの .gitignore の data/raw/* で版管理の外。
readonly RESULT_ROOT="${RESULT_ROOT:-/Users/sanchez/Desktop/Odyssey-TradingResearch/data/raw/oanda_mt5_probe}"
readonly STATE_DIR="${RESULT_ROOT}/_state"
readonly STATE_FILE="${STATE_DIR}/state.env"
# S3 の一時バケットの名前と回収の記録。インスタンスの片付け（state.env の退避）の後も、バケットを
# 消すまで残す（回収が終わる前に終了の時刻が来ても、バケットから回収できるようにするため）。
readonly S3_STATE_FILE="${STATE_DIR}/s3_bucket.env"
# 全期間取得の置き場（人間の第 18 回の決定。data/raw/oanda_mt5/<取得日>/<通貨>/。.gitignore の data/raw/* で版管理の外）
readonly FULL_ROOT="${FULL_ROOT:-/Users/sanchez/Desktop/Odyssey-TradingResearch/data/raw/oanda_mt5}"
# キーペアの秘密鍵はリポジトリの外（~/.ssh）に置く。Windows の管理者のパスワードの復号にだけ使う。
readonly KEY_DIR="${HOME}/.ssh"
# Mac の Python（リポジトリの .python-version の影響を受けない、OS の Python を使う。標準ライブラリだけ）
readonly PY_MAC="${PY_MAC:-/usr/bin/python3}"
# 時刻の調査の評価（tools.survey.mt5_time_survey）に使う、リポジトリの仮想環境の Python（3.12）
REPO_PY="${REPO_PY:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/.venv/bin/python}"
readonly REPO_PY

# 環境変数の認証情報や別のプロファイルが紛れ込まないようにする。
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_DEFAULT_PROFILE \
  AWS_REGION AWS_DEFAULT_REGION || true
export AWS_PAGER=""

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "停止: $*"; exit 1; }

# AWS CLI は必ずこの関数で呼ぶ（プロファイルとリージョンを固定する）。
aws_p() { aws --profile "$PROBE_PROFILE" --region "$PROBE_REGION" "$@"; }

# 課金の起きる操作・削除の操作の前の確認。--execute が無ければ、何をするかを表示して止まる。
EXECUTE=0
parse_execute_flag() {
  for a in "$@"; do
    case "$a" in
      --execute) EXECUTE=1 ;;
      --dry-run) EXECUTE=0 ;;
    esac
  done
}
# 変更の操作: --execute のときだけ実行し、そうでなければ表示だけ。
run_or_show() {
  if [[ "$EXECUTE" == 1 ]]; then
    log "実行: $*"
    "$@"
  else
    printf '  (dry-run) %s\n' "$*" >&2
  fi
}

tag_spec() {  # tag_spec <resource-type> [<name-suffix>]
  local rtype="$1" suffix="${2:-}"
  printf 'ResourceType=%s,Tags=[{Key=%s,Value=%s},{Key=Name,Value=%s%s}]' \
    "$rtype" "$PROBE_TAG_KEY" "$PROBE_TAG_VALUE" "$PROBE_NAME" "$suffix"
}
tag_filter() { printf 'Name=tag:%s,Values=%s' "$PROBE_TAG_KEY" "$PROBE_TAG_VALUE"; }

require_profile() {
  aws configure list-profiles 2>/dev/null | grep -qx "$PROBE_PROFILE" \
    || die "プロファイル ${PROBE_PROFILE} が無い（付録 K-1 の人間の事前作業が済んでいない）"
}

# --- 状態（インスタンス ID・起動の時刻など。秘密情報は書かない） -------------------------
load_state() {
  [[ -f "$STATE_FILE" ]] || die "状態のファイルが無い: ${STATE_FILE}（10_launch.sh の前）"
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  : "${INSTANCE_ID:?}" "${LAUNCHED_AT_EPOCH:?}" "${PROBE_DATE:?}"
}
save_state() {  # save_state KEY VALUE
  mkdir -p "$STATE_DIR"
  touch "$STATE_FILE"
  grep -v "^$1=" "$STATE_FILE" >"${STATE_FILE}.tmp" || true
  printf '%s=%q\n' "$1" "$2" >>"${STATE_FILE}.tmp"
  mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

# 起動から終了の時刻（PROBE_DEADLINE_SECONDS）を超えていたら、終了（90_teardown.sh）以外は進めない。
check_deadline() {
  local now elapsed
  now=$(date +%s)
  elapsed=$(( now - LAUNCHED_AT_EPOCH ))
  log "起動からの経過: $(( elapsed / 60 )) 分（終了 $(( PROBE_DEADLINE_SECONDS / 60 )) 分）"
  if (( elapsed >= PROBE_DEADLINE_SECONDS )); then
    die "起動から $(( PROBE_DEADLINE_SECONDS / 60 )) 分を超えた。90_teardown.sh --execute で終了する"
  fi
}

# 最後の調査（PROBE_RUN_KIND=final）・全期間取得（fullfetch）の起動では、一覧の外の要求を出す段を動かさない。
forbid_in_final_run() {
  [[ "$PROBE_RUN_KIND" != "final" && "$PROBE_RUN_KIND" != "fullfetch" ]] \
    || die "この起動（${PROBE_RUN_KIND}）では、この段は動かさない（要求は決めた一覧だけ。付録 K-10.2・K-16.2）"
}

# 道具が注文・売買・口座の関数を呼ばないことを、実行の前に文字列で確かめる（D12 §11.4）。
assert_no_trading_calls() {
  if grep -nE '\b(order_send|order_check|positions_get|positions_total|orders_get|orders_total|history_orders_get|history_orders_total|history_deals_get|history_deals_total|account_info|login)\s*\(' "$PROBE_TOOL"; then
    die "道具に使ってはいけない関数の呼び出しがある"
  fi
}

# --- S3 の一時バケットの記録 -----------------------------------------------------------------
load_s3_state() {
  [[ -f "$S3_STATE_FILE" ]] || return 1
  # shellcheck disable=SC1090
  source "$S3_STATE_FILE"
  : "${S3_BUCKET:?}" "${FETCH_DATE:?}"
  assert_probe_bucket "$S3_BUCKET"
}
save_s3_state() {  # save_s3_state KEY VALUE
  mkdir -p "$STATE_DIR"
  touch "$S3_STATE_FILE"
  grep -v "^$1=" "$S3_STATE_FILE" >"${S3_STATE_FILE}.tmp" || true
  printf '%s=%q\n' "$1" "$2" >>"${S3_STATE_FILE}.tmp"
  mv "${S3_STATE_FILE}.tmp" "$S3_STATE_FILE"
}
# 試験のバケットの名前（odyssey-mt5-probe-YYYY-MM-DD-<小文字の英数字 8 桁>）でなければ止める（他のバケットを触らない）。
assert_probe_bucket() {
  [[ "$1" =~ ^odyssey-mt5-probe-[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]{8}$ ]] \
    || die "試験のバケットの名前ではない: $1"
}

# S3 の一時バケットの名前の末尾の乱数 8 桁（小文字の 16 進数。assert_probe_bucket の [a-z0-9]{8} に入る）。
# 以前の `tr -dc 'a-z0-9' </dev/urandom | head -c 8` は、head が 8 文字で読むのをやめると上流の tr が
# 強制終了の合図（SIGPIPE）を受けて終了コード 141 になり、`set -o pipefail` で毎回止まった（付録 K-17.4 の 2）。
# ここでは od が /dev/urandom から 4 バイトだけ読んで（-N 4）自分で終わり、下流の tr は入力を最後まで読む。
# パイプの下流が上流より先に読むのをやめる箇所が無いので、SIGPIPE は起きない。
random_suffix8() {
  local s
  s=$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')
  [[ "$s" =~ ^[0-9a-f]{8}$ ]] || die "乱数 8 桁を作れなかった: '${s}'"
  printf '%s\n' "$s"
}

# S3 のバケットの有無を head-bucket で調べ、exists（ある）・absent（404。無い）・unknown: <応答>（403 など。
# 有無を確かめられない）のどれかを出力する。403 は「無い」とは扱わない（権限の不足や他人のバケットでも 403 になる）。
s3_bucket_state() {  # s3_bucket_state <bucket>
  local err
  if err=$(aws_p s3api head-bucket --bucket "$1" 2>&1 >/dev/null); then
    echo "exists"
  elif [[ "$err" == *"(404)"* || "$err" == *"Not Found"* || "$err" == *"NoSuchBucket"* ]]; then
    echo "absent"
  else
    err=$(printf '%s' "$err" | tr '\n' ' ')
    err="${err#"${err%%[![:space:]]*}"}"  # AWS CLI のエラーは空行で始まるので、先頭の空白を除く
    printf 'unknown: %s\n' "${err:0:200}"
  fi
}

# 消したバケットが 404 になるまで待つ（S3_DELETE_WAIT_SECONDS 間隔で S3_DELETE_WAIT_ATTEMPTS 回まで）。
# 404 になれば 0、上限まで確かめられなければ 1。
wait_bucket_absent() {  # wait_bucket_absent <bucket>
  local i st
  for (( i = 1; i <= S3_DELETE_WAIT_ATTEMPTS; i++ )); do
    st=$(s3_bucket_state "$1")
    if [[ "$st" == "absent" ]]; then
      log "S3 のバケット $1 が無いことを確かめた（head-bucket が 404。${i}/${S3_DELETE_WAIT_ATTEMPTS} 回目）"
      return 0
    fi
    log "S3 のバケット $1 が消えたとまだ確かめられない（${i}/${S3_DELETE_WAIT_ATTEMPTS} 回目）: ${st}"
    (( i == S3_DELETE_WAIT_ATTEMPTS )) || sleep "$S3_DELETE_WAIT_SECONDS"
  done
  return 1
}

# AWS CLI v2 を Windows に入れる PowerShell（20_prepare.sh が SSM で実行する）。既に入っていれば入れない。
# 入れるときは、版を固定した公式の MSI を取り、SHA-256 と Authenticode の署名（有効で、署名者が Amazon）を
# 確かめてから msiexec で入れ、`aws --version` が aws-cli/2. で始まることを確かめる。
awscli_install_ps() {
  cat <<PS
\$ErrorActionPreference = 'Stop'
\$cli = '${AWSCLI_EXE}'
\$msi = '${AWSCLI_MSI_WIN}'
\$have = \$null
if (Test-Path -LiteralPath \$cli) {
  \$have = (& \$cli --version | Out-String).Trim()
  if (\$LASTEXITCODE -ne 0 -or \$have -notmatch '^aws-cli/2\.') { \$have = \$null }
}
if (\$have) {
  "aws-cli: already installed, skip: \$have"
} else {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent \$msi) | Out-Null
  Invoke-WebRequest -UseBasicParsing -Uri '${AWSCLI_MSI_URL}' -OutFile \$msi
  \$h = (Get-FileHash -Algorithm SHA256 -LiteralPath \$msi).Hash.ToLower()
  if (\$h -ne '${AWSCLI_MSI_SHA256}') { throw "AWS CLI MSI SHA-256 mismatch: \$h" }
  \$sig = Get-AuthenticodeSignature -FilePath \$msi
  if ("\$(\$sig.Status)" -ne 'Valid') { throw "AWS CLI MSI signature status: \$(\$sig.Status)" }
  \$subject = "\$(\$sig.SignerCertificate.Subject)"
  if (-not (\$subject.Contains('O=${AWSCLI_SIGNER_ORG}') -or \$subject.Contains('O="${AWSCLI_SIGNER_ORG}"'))) { throw "AWS CLI MSI signer: \$subject" }
  "aws-cli msi: sha256 ok, signature Valid (\$subject)"
  \$p = Start-Process -FilePath 'msiexec.exe' -ArgumentList '/i', \$msi, '/qn', '/norestart' -Wait -PassThru
  if (\$p.ExitCode -ne 0) { throw "msiexec exit \$(\$p.ExitCode)" }
  Remove-Item -LiteralPath \$msi
  if (-not (Test-Path -LiteralPath \$cli)) { throw "aws.exe not found after install: \$cli" }
  \$have = (& \$cli --version | Out-String).Trim()
  if (\$LASTEXITCODE -ne 0 -or \$have -notmatch '^aws-cli/2\.') { throw "aws --version failed after install: \$have" }
  "aws-cli: installed: \$have"
}
PS
}

# インスタンスが試験のタグを持つことを確かめる（他のインスタンスを触らない）。
assert_tagged_instance() {
  local id="$1" v
  v=$(aws_p ec2 describe-instances --instance-ids "$id" \
    --query "Reservations[0].Instances[0].Tags[?Key=='${PROBE_TAG_KEY}'].Value | [0]" --output text)
  [[ "$v" == "$PROBE_TAG_VALUE" ]] || die "インスタンス ${id} に試験のタグが無い"
}

# --- SSM で PowerShell を実行する ---------------------------------------------------------
# ssm_ps <instance-id> <timeout-seconds> <powershell-file>
# 標準出力を表示し、終了の状態が Success でなければ 1 を返す。
# 出力は 1 回あたり約 24,000 文字で切れるので、大きなものは 40_collect.sh のように分けて受け取る。
ssm_ps() {
  local id="$1" timeout="$2" file="$3" params cmd_id status out err
  params=$("$PY_MAC" -I -c 'import json,sys; print(json.dumps({"commands": open(sys.argv[1], encoding="utf-8").read().splitlines(), "executionTimeout": [sys.argv[2]]}))' "$file" "$timeout")
  cmd_id=$(aws_p ssm send-command --instance-ids "$id" --document-name "AWS-RunPowerShellScript" \
    --timeout-seconds 600 --parameters "$params" --query "Command.CommandId" --output text)
  status="Pending"
  while [[ "$status" == "Pending" || "$status" == "InProgress" || "$status" == "Delayed" ]]; do
    sleep 3
    status=$(aws_p ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" \
      --query Status --output text 2>/dev/null || echo "Pending")
  done
  out=$(aws_p ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" \
    --query StandardOutputContent --output text)
  err=$(aws_p ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" \
    --query StandardErrorContent --output text)
  printf '%s\n' "$out"
  [[ -z "$err" || "$err" == "None" ]] || printf '[stderr] %s\n' "$err" >&2
  [[ "$status" == "Success" ]] || { log "SSM のコマンド ${cmd_id} の状態: ${status}"; return 1; }
}

wait_ssm_online() {
  local id="$1" i ping
  for i in $(seq 1 60); do
    ping=$(aws_p ssm describe-instance-information --filters "Key=InstanceIds,Values=${id}" \
      --query "InstanceInformationList[0].PingStatus" --output text 2>/dev/null || true)
    [[ "$ping" == "Online" ]] && { log "SSM: Online"; return 0; }
    log "SSM の接続を待っている（${i}/60）: ${ping}"
    sleep 15
  done
  die "15 分待っても SSM がつながらない"
}

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }

# Mac のファイルを SSM で Windows に置き、SHA-256 の一致を確かめる（分けて送る。SSM の引数の大きさの制限のため）。
# ssm_put_file <instance-id> <local-file> <windows-path>
ssm_put_file() {
  local id="$1" src="$2" dst="$3" sha w first chunk op
  sha=$(sha256_of "$src")
  w=$(mktemp -d)
  base64 <"$src" | tr -d '\n' | fold -w 6000 >"$w/f.b64"
  first=1
  while IFS= read -r chunk || [[ -n "$chunk" ]]; do
    if [[ $first == 1 ]]; then op="Set-Content"; first=0; else op="Add-Content"; fi
    printf "%s -NoNewline -Encoding ascii -Path '%s.b64' -Value '%s'\n" "$op" "$dst" "$chunk" >"$w/c.ps1"
    ssm_ps "$id" 120 "$w/c.ps1" >/dev/null
  done <"$w/f.b64"
  cat >"$w/d.ps1" <<PS
\$ErrorActionPreference = 'Stop'
[IO.File]::WriteAllBytes('${dst}', [Convert]::FromBase64String((Get-Content -Raw '${dst}.b64')))
Remove-Item '${dst}.b64'
\$h = (Get-FileHash -Algorithm SHA256 '${dst}').Hash.ToLower()
if (\$h -ne '${sha}') { throw "SHA-256 mismatch: \$h" }
"put ok: ${dst} \$h"
PS
  ssm_ps "$id" 120 "$w/d.ps1"
  rm -rf "$w"
}

# 試験の道具を、人間がログインした Administrator の対話のセッションのタスクとして実行する（30_run_probe.sh と同じ方法）。
# ssm_run_probe_task <instance-id> <task-suffix> <windows-out-dir> <probe の引数（Windows のコマンド行の文字列）>
ssm_run_probe_task() {
  local id="$1" suffix="$2" out="$3" pargs="$4" tool_sha w
  tool_sha=$(sha256_of "$PROBE_TOOL")
  w=$(mktemp -d)
  cat >"$w/run.ps1" <<PS
\$ErrorActionPreference = 'Stop'
\$h = (Get-FileHash -Algorithm SHA256 C:\\probe\\mt5_api_probe.py).Hash.ToLower()
if (\$h -ne '${tool_sha}') { throw "probe SHA-256 mismatch: \$h" }
if (-not (Get-Process -Name terminal64 -ErrorAction SilentlyContinue)) { throw 'MT5 terminal is not running (human must log in via RDP first)' }
New-Item -ItemType Directory -Force -Path '${out}' | Out-Null
\$cmd = 'set "PYTHONUTF8=1" && "${PY_EXE}" C:\\probe\\mt5_api_probe.py ${pargs} > ${out}\\run.log 2>&1'
\$task = '${PROBE_NAME}-${suffix}'
Unregister-ScheduledTask -TaskName \$task -Confirm:\$false -ErrorAction SilentlyContinue
\$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c ' + \$cmd)
\$principal = New-ScheduledTaskPrincipal -UserId 'Administrator' -LogonType Interactive -RunLevel Highest
\$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName \$task -Action \$action -Principal \$principal -Settings \$settings | Out-Null
Start-ScheduledTask -TaskName \$task
Start-Sleep -Seconds 5
while ((Get-ScheduledTask -TaskName \$task).State -eq 'Running') { Start-Sleep -Seconds 10 }
\$result = (Get-ScheduledTaskInfo -TaskName \$task).LastTaskResult
Unregister-ScheduledTask -TaskName \$task -Confirm:\$false
"task result: \$result"
Get-Content '${out}\\run.log'
Get-PSDrive C | ForEach-Object { 'C: used GB {0:N2} free GB {1:N2}' -f (\$_.Used/1GB), (\$_.Free/1GB) }
PS
  ssm_ps "$id" 3600 "$w/run.ps1"
  rm -rf "$w"
}
