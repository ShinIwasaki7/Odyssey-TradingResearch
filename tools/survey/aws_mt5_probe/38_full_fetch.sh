#!/usr/bin/env bash
# 全期間取得（D12 付録 K-16。人間の第 18 回の決定）を実行する: 8 通貨 × 2017-01〜2023-12 の 1 分足を、
# 1 要求 = 1 通貨 × 1 か月で取り（道具の段 fullfetch）、通貨ごとに S3 の一時バケットへ置き、
# 45_collect_s3.sh で Mac に回収する（回収の単位は通貨。打ち切りや終了で止まっても、済んだ通貨は Mac に残る）。
#
# 上限（付録 K-16.6）:
#   要求 750（計画 672。残り 78 は 0 本の月のやり直しだけ。道具が manifest で前の回から通して数える）
#   起動から 2 時間 30 分で要求の打ち切り（FULL_TIME_LIMIT_SECONDS。道具にも --stop-at-epoch で渡す）、
#   4 時間で終了（95_deadline_guard.sh）、4 時間 30 分で Windows の自動の停止
#   ディスクの空きが 3 GB を切ったら次の要求を出さない（道具）
# 通貨のこの回の最初の月が 0 本なら、その通貨の残りを出さずに止まる（取り寄せが始まっていない合図）。
# 2024 年以降・2017 年より前は要求しない（道具が区間を確かめる）。価格は Windows の CSV と S3 と
# data/raw/oanda_mt5/ にだけ置き、ログ・標準出力には出さない（件数・時刻・SHA-256 だけ）。
#
# 使い方:
#   tools/survey/aws_mt5_probe/38_full_fetch.sh [--execute]                  # 8 通貨（付録 K-16.1 の順）
#   tools/survey/aws_mt5_probe/38_full_fetch.sh --pairs EURUSD,GBPJPY [--execute]   # 一覧の中の通貨だけ（再開用）
#   tools/survey/aws_mt5_probe/38_full_fetch.sh --redo-empty [--execute]     # 0 本だった月だけやり直す（上限 750 の内）
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
readonly ALL_PAIRS="USDJPY,EURJPY,EURUSD,GBPJPY,USDCHF,AUDJPY,GBPUSD,AUDUSD"
pairs="$ALL_PAIRS"
redo=0
while (( $# > 0 )); do
  case "$1" in
    --pairs) pairs="${2:?--pairs の後に通貨（カンマ区切り）}"; shift ;;
    --redo-empty) redo=1 ;;
    --execute|--dry-run) ;;
    *) die "知らない引数: $1" ;;
  esac
  shift
done
for p in ${pairs//,/ }; do
  [[ ",${ALL_PAIRS}," == *",${p},"* ]] || die "全期間取得の一覧に無い通貨: ${p}"
done
require_profile
load_state
check_deadline
assert_tagged_instance "$INSTANCE_ID"
[[ "${RUN_KIND:-}" == "fullfetch" ]] || die "この起動は全期間取得の起動ではない（状態の RUN_KIND=${RUN_KIND:-なし}）"
elapsed=$(( $(date +%s) - LAUNCHED_AT_EPOCH ))
(( elapsed < FULL_TIME_LIMIT_SECONDS )) \
  || die "要求の打ち切り（起動から $(( FULL_TIME_LIMIT_SECONDS / 60 )) 分）に達した。要求は出さない。45_collect_s3.sh で回収し、90_teardown.sh --execute で終了する"
assert_no_trading_calls
stop_at=$(( LAUNCHED_AT_EPOCH + FULL_TIME_LIMIT_SECONDS ))
win_out='C:\probe\out\full'

bucket=""
if load_s3_state; then bucket="$S3_BUCKET"; fi
cat >&2 <<INFO
== 全期間取得（${INSTANCE_ID}、取得日 ${PROBE_DATE}）
  通貨: ${pairs}$( [[ $redo == 1 ]] && echo "（0 本だった月のやり直しだけ）" )
  要求: 1 通貨 × 1 か月、新しい月から。上限 ${FULL_MAX_REQUESTS}（済み ${FULL_SENT:-0}）
  打ち切り: $(date -u -r "$stop_at" +%H:%M:%SZ)（起動から $(( FULL_TIME_LIMIT_SECONDS / 60 )) 分）。経過 $(( elapsed / 60 )) 分
  Windows の出力: ${win_out}\\<通貨>\\<通貨>_<YYYY-MM>_m1.csv.gz、manifest.json、fetch_log.jsonl
  S3 の一時バケット: ${bucket:-まだ無い（${S3_BUCKET_PREFIX}${PROBE_DATE}-<乱数 8 桁> を作る）}
  Mac の置き場: ${FULL_ROOT}/${PROBE_DATE}/<通貨>/（.gitignore の data/raw/* で版管理の外）
INFO
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }

if [[ -z "$bucket" ]]; then
  bucket="${S3_BUCKET_PREFIX}${PROBE_DATE}-$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 8)"
  assert_probe_bucket "$bucket"
  aws_p s3api create-bucket --bucket "$bucket" \
    --create-bucket-configuration "LocationConstraint=${PROBE_REGION}" >/dev/null
  save_s3_state S3_BUCKET "$bucket"
  save_s3_state FETCH_DATE "$PROBE_DATE"
  save_s3_state S3_CREATED_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log "S3 の一時バケットを作った: ${bucket}（片付けで中身ごと消す）"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# Windows から S3 に置く道具（インスタンスのロールの権限で置く。AWS CLI か AWS Tools for PowerShell）。
# shellcheck disable=SC2016  # PowerShell の変数（$aws）なので bash では展開しない
s3_client_ps='
$aws = $null
$c = Get-Command aws -ErrorAction SilentlyContinue
if ($c) { $aws = $c.Source } elseif (Test-Path "C:\Program Files\Amazon\AWSCLIV2\aws.exe") { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" }
if (-not $aws -and -not (Get-Command Write-S3Object -ErrorAction SilentlyContinue)) { throw "no S3 client (AWS CLI / AWS Tools for PowerShell) on this instance" }
'
# shellcheck disable=SC2016  # 同上
printf '%s\n%s\n%s\n' "\$ErrorActionPreference = 'Stop'" "$s3_client_ps" \
  'if ($aws) { "s3 client: aws-cli $aws" } else { "s3 client: Write-S3Object" }' >"$work/client.ps1"
ssm_ps "$INSTANCE_ID" 120 "$work/client.ps1"

upload_pair() {  # upload_pair <通貨>
  local pair="$1"
  cat >"$work/up.ps1" <<PS
\$ErrorActionPreference = 'Stop'
${s3_client_ps}
\$root = '${win_out}'
\$n = 0
\$dir = Join-Path \$root '${pair}'
if (Test-Path \$dir) {
  if (\$aws) {
    & \$aws s3 cp \$dir 's3://${bucket}/${S3_KEY_PREFIX}/${pair}/' --recursive --exclude '*' --include '*.csv.gz' --region ${PROBE_REGION} --only-show-errors
    if (\$LASTEXITCODE -ne 0) { throw 's3 cp failed: ${pair}' }
  } else {
    Write-S3Object -BucketName '${bucket}' -KeyPrefix '${S3_KEY_PREFIX}/${pair}' -Folder \$dir -SearchPattern '*.csv.gz' -Region ${PROBE_REGION}
  }
  \$n = @(Get-ChildItem -File (Join-Path \$dir '*.csv.gz')).Count
}
foreach (\$name in @('manifest.json', 'fetch_log.jsonl')) {
  \$f = Join-Path \$root \$name
  if (-not (Test-Path \$f)) { continue }
  if (\$aws) {
    & \$aws s3 cp \$f "s3://${bucket}/${S3_KEY_PREFIX}/\$name" --region ${PROBE_REGION} --only-show-errors
    if (\$LASTEXITCODE -ne 0) { throw "s3 cp failed: \$name" }
  } else {
    Write-S3Object -BucketName '${bucket}' -Key "${S3_KEY_PREFIX}/\$name" -File \$f -Region ${PROBE_REGION}
  }
}
"uploaded ${pair}: \$n files + manifest"
PS
  ssm_ps "$INSTANCE_ID" 1800 "$work/up.ps1"
}

stop=""
for pair in ${pairs//,/ }; do
  check_deadline
  if (( $(date +%s) >= stop_at )); then stop="time_limit"; break; fi
  pargs="fullfetch --out-dir ${win_out} --pairs ${pair} --max-total ${FULL_MAX_REQUESTS} --stop-at-epoch ${stop_at}"
  [[ $redo == 1 ]] && pargs="${pargs} --redo-empty"
  log "通貨 ${pair} を取得する"
  out=$(ssm_run_probe_task "$INSTANCE_ID" "full-${pair}" "$win_out" "$pargs") || { printf '%s\n' "$out" >&2; die "${pair} の取得のタスクが失敗した"; }
  printf '%s\n' "$out" | grep -v '^FULLFETCH_RESULT ' >&2 || true
  line=$(printf '%s\n' "$out" | tr -d '\r' | grep '^FULLFETCH_RESULT ' | tail -n 1 || true)
  [[ -n "$line" ]] || die "${pair} の取得の結果の行が無い（run.log を確かめる）"
  read -r sent_total got_stop summary < <("$PY_MAC" -I -c '
import json, sys
r = json.loads(sys.argv[1][len("FULLFETCH_RESULT "):])
print(r["requests_sent_total"], r["stop"] or "-",
      "ok={ok} empty={empty} skipped_done={skipped_done} sent={sent_this_run} remaining={n}".format(n=len(r["remaining"]), **r))
' "$line")
  save_state FULL_SENT "$sent_total"
  log "${pair}: ${summary}（要求の累計 ${sent_total} / ${FULL_MAX_REQUESTS}、止まった理由 ${got_stop}）"
  upload_pair "$pair"
  "$PROBE_SCRIPT_DIR/45_collect_s3.sh" "$pair" --execute
  if [[ "$got_stop" != "-" ]]; then stop="$got_stop"; break; fi
done

case "$stop" in
  "") log "指定の通貨を終えた。0 本の月が残れば 38_full_fetch.sh --redo-empty --execute（上限の内）。終わったら 90_teardown.sh --execute" ;;
  first_month_empty) log "停止: 通貨のこの回の最初の月が 0 本（端末の取り寄せが始まっていない）。他の通貨で確かめずに司令塔に報告する" ;;
  time_limit) log "停止: 要求の打ち切り（起動から $(( FULL_TIME_LIMIT_SECONDS / 60 )) 分）。済んだ分は回収済み。90_teardown.sh --execute" ;;
  request_limit) log "停止: 要求の上限 ${FULL_MAX_REQUESTS}。済んだ分は回収済み。90_teardown.sh --execute" ;;
  *) log "停止: ${stop}。済んだ分は回収済み。司令塔に報告する" ;;
esac
