#!/usr/bin/env bash
# 封印の取得（制約の改定文 ADR-0037。D15 §2.2 の S4a、D03 §16.5）: 2024 年以降の OANDA MT5 の 1 分足（bid）を、
# Mac で作った計画（tools/survey/sealed_fetch_plan.py）の要求だけ取り（道具の段 sealedfetch）、通貨ごとに S3 の
# 一時バケットへ置き、45_collect_s3.sh で Mac の data/raw/oanda_mt5_sealed/<取得日>/<通貨>/ に回収する。
#
# 窓は版管理の設定 configs/acquisition/oanda_mt5_m1_sealed_window.yaml からだけ読む。Mac でも Windows でも、
# 計画の要求をすべて窓と照らし、1 つでも外れれば何も要求せずに止まる（ADR-0037 第4項の 3）。
# 起動の種類（PROBE_RUN_KIND）は sealed-bulk（一括取得）か sealed-periodic（定期取得）で、計画の段と一致する
# こと。上限（lib.sh）: 一括は打ち切り 2 時間・終了 3 時間・要求 300、定期は打ち切り 1 時間・終了 2 時間・
# 要求 9（計画の max_requests。道具が manifest で数える）。通貨のこの回の最初の要求が 0 本なら、その通貨の
# 残りを出さずに止まる。価格は Windows の CSV と S3 と data/raw/oanda_mt5_sealed/ にだけ置き、ログ・標準出力
# には件数・時刻・SHA-256 だけを出す（ADR-0037 第7項）。取得の実行・起動は毎回の人間の go の後だけ。
#
# 使い方:
#   tools/survey/aws_mt5_probe/39_sealed_fetch.sh --plan <計画.json> [--execute]                 # 計画の全通貨
#   tools/survey/aws_mt5_probe/39_sealed_fetch.sh --plan <計画.json> --pairs USDJPY [--execute]  # 一部の通貨
#   tools/survey/aws_mt5_probe/39_sealed_fetch.sh --plan <計画.json> --redo-empty [--execute]    # 0 本の要求だけ
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
plan=""
pairs=""
redo=0
while (( $# > 0 )); do
  case "$1" in
    --plan) plan="${2:?--plan の後に計画の JSON}"; shift ;;
    --pairs) pairs="${2:?--pairs の後に通貨（カンマ区切り）}"; shift ;;
    --redo-empty) redo=1 ;;
    --execute|--dry-run) ;;
    *) die "知らない引数: $1" ;;
  esac
  shift
done
[[ -n "$plan" && -f "$plan" ]] || die "--plan に計画の JSON を渡す（tools.survey.sealed_fetch_plan で作る）"
plan=$(cd "$(dirname "$plan")" && pwd)/$(basename "$plan")
readonly WINDOW_FILE="${PROBE_SCRIPT_DIR}/../../../configs/acquisition/oanda_mt5_m1_sealed_window.yaml"
[[ -f "$WINDOW_FILE" ]] || die "窓の設定が無い: ${WINDOW_FILE}"
case "$PROBE_RUN_KIND" in
  sealed-bulk) stage="bulk" ;;
  sealed-periodic) stage="periodic" ;;
  *) die "この起動は封印の取得の起動ではない（起動の種類 ${PROBE_RUN_KIND}）" ;;
esac

# 計画を窓の設定と照らす（Mac。道具と同じ検査。外れれば何もしない）。件数・時刻だけを表示する。
read -r plan_stage planned max_requests plan_pairs < <(cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -I - "$plan" "$WINDOW_FILE" <<'PY'
import sys
from datetime import UTC, datetime
from pathlib import Path
sys.path.insert(0, ".")
from tools.survey.mt5_api_probe import load_sealed_plan, read_sealed_window
window = read_sealed_window(Path(sys.argv[2]))
plan, requests = load_sealed_plan(Path(sys.argv[1]), window, now=datetime.now(tz=UTC))
pairs = []
for r in requests:
    if r.pair not in pairs:
        pairs.append(r.pair)
print(plan["stage"], plan["planned_requests"], plan["max_requests"], ",".join(pairs))
PY
) || die "計画が窓の設定と合わない（何も要求しない）"
[[ "$plan_stage" == "$stage" ]] || die "計画の段 ${plan_stage} が起動の種類 ${PROBE_RUN_KIND} と違う"
[[ -n "$pairs" ]] || pairs="$plan_pairs"
for p in ${pairs//,/ }; do
  [[ ",${plan_pairs}," == *",${p},"* ]] || die "計画に無い通貨: ${p}"
done

require_profile
load_state
check_deadline
assert_tagged_instance "$INSTANCE_ID"
elapsed=$(( $(date +%s) - LAUNCHED_AT_EPOCH ))
(( elapsed < REQUEST_TIME_LIMIT_SECONDS )) \
  || die "要求の打ち切り（起動から $(( REQUEST_TIME_LIMIT_SECONDS / 60 )) 分）に達した。要求は出さない。45_collect_s3.sh で回収し、90_teardown.sh --execute で終了する"
assert_no_trading_calls
stop_at=$(( LAUNCHED_AT_EPOCH + REQUEST_TIME_LIMIT_SECONDS ))
win_out='C:\probe\out\sealed'
win_plan='C:\probe\sealed_plan.json'
win_window='C:\probe\sealed_window.yaml'

bucket=""
if load_s3_state; then
  bucket="$S3_BUCKET"
  [[ "${S3_PREFIX:-}" == "$SEALED_S3_KEY_PREFIX" ]] || die "S3 の一時バケットの記録が封印の取得のものではない（${S3_STATE_FILE}）"
  [[ "${SEALED_PLAN:-}" == "$plan" ]] || die "S3 の一時バケットの記録の計画（${SEALED_PLAN:-なし}）と --plan が違う"
fi
cat >&2 <<INFO
== 封印の取得（${INSTANCE_ID}、取得日 ${PROBE_DATE}、段 ${stage}）
  計画: ${plan}（要求 ${planned}、上限 ${max_requests}。SHA-256 $(sha256_of "$plan")）
  窓の設定: ${WINDOW_FILE}（SHA-256 $(sha256_of "$WINDOW_FILE")）
  通貨: ${pairs}$( [[ $redo == 1 ]] && echo "（0 本だった要求のやり直しだけ）" )
  打ち切り: $(date -u -r "$stop_at" +%H:%M:%SZ)（起動から $(( REQUEST_TIME_LIMIT_SECONDS / 60 )) 分）。経過 $(( elapsed / 60 )) 分
  Windows の出力: ${win_out}\\<通貨>\\*.csv.gz、manifest.json、fetch_log.jsonl
  S3 の一時バケット: ${bucket:-まだ無い（${S3_BUCKET_PREFIX}${PROBE_DATE}-<乱数 8 桁> を作る）}
  Mac の置き場: ${SEALED_ROOT}/${PROBE_DATE}/<通貨>/（.gitignore の data/raw/* で版管理の外）
INFO
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }

if [[ -z "$bucket" ]]; then
  suffix=$(random_suffix8)
  bucket="${S3_BUCKET_PREFIX}${PROBE_DATE}-${suffix}"
  assert_probe_bucket "$bucket"
  aws_p s3api create-bucket --bucket "$bucket" \
    --create-bucket-configuration "LocationConstraint=${PROBE_REGION}" >/dev/null
  save_s3_state S3_BUCKET "$bucket"
  save_s3_state FETCH_DATE "$PROBE_DATE"
  save_s3_state S3_PREFIX "$SEALED_S3_KEY_PREFIX"
  save_s3_state SEALED_PLAN "$plan"
  save_s3_state S3_CREATED_AT_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log "S3 の一時バケットを作った: ${bucket}（片付けで中身ごと消す）"
fi

# 計画と窓の設定を Windows に置く（SHA-256 の一致を確かめる）。道具は窓の設定を読み直して計画と照らす。
ssm_put_file "$INSTANCE_ID" "$plan" "$win_plan"
ssm_put_file "$INSTANCE_ID" "$WINDOW_FILE" "$win_window"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# shellcheck disable=SC2016  # PowerShell の変数（$aws）なので bash では展開しない
s3_client_ps='
$aws = $null
$c = Get-Command aws -ErrorAction SilentlyContinue
if (Test-Path "C:\Program Files\Amazon\AWSCLIV2\aws.exe") { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" } elseif ($c) { $aws = $c.Source }
if (-not $aws -and -not (Get-Command Write-S3Object -ErrorAction SilentlyContinue)) { throw "no S3 client (AWS CLI / AWS Tools for PowerShell) on this instance" }
'
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
    & \$aws s3 cp \$dir 's3://${bucket}/${SEALED_S3_KEY_PREFIX}/${pair}/' --recursive --exclude '*' --include '*.csv.gz' --region ${PROBE_REGION} --only-show-errors
    if (\$LASTEXITCODE -ne 0) { throw 's3 cp failed: ${pair}' }
  } else {
    Write-S3Object -BucketName '${bucket}' -KeyPrefix '${SEALED_S3_KEY_PREFIX}/${pair}' -Folder \$dir -SearchPattern '*.csv.gz' -Region ${PROBE_REGION}
  }
  \$n = @(Get-ChildItem -File (Join-Path \$dir '*.csv.gz')).Count
}
foreach (\$name in @('manifest.json', 'fetch_log.jsonl')) {
  \$f = Join-Path \$root \$name
  if (-not (Test-Path \$f)) { continue }
  if (\$aws) {
    & \$aws s3 cp \$f "s3://${bucket}/${SEALED_S3_KEY_PREFIX}/\$name" --region ${PROBE_REGION} --only-show-errors
    if (\$LASTEXITCODE -ne 0) { throw "s3 cp failed: \$name" }
  } else {
    Write-S3Object -BucketName '${bucket}' -Key "${SEALED_S3_KEY_PREFIX}/\$name" -File \$f -Region ${PROBE_REGION}
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
  pargs="sealedfetch --plan ${win_plan} --window ${win_window} --out-dir ${win_out} --pairs ${pair} --stop-at-epoch ${stop_at}"
  [[ $redo == 1 ]] && pargs="${pargs} --redo-empty"
  log "通貨 ${pair} を取得する（段 ${stage}）"
  out=$(ssm_run_probe_task "$INSTANCE_ID" "sealed-${pair}" "$win_out" "$pargs") || { printf '%s\n' "$out" >&2; die "${pair} の取得のタスクが失敗した"; }
  printf '%s\n' "$out" | grep -v '^SEALEDFETCH_RESULT ' >&2 || true
  line=$(printf '%s\n' "$out" | tr -d '\r' | grep '^SEALEDFETCH_RESULT ' | tail -n 1 || true)
  [[ -n "$line" ]] || die "${pair} の取得の結果の行が無い（run.log を確かめる）"
  read -r sent_total got_stop summary < <("$PY_MAC" -I -c '
import json, sys
r = json.loads(sys.argv[1][len("SEALEDFETCH_RESULT "):])
print(r["requests_sent_total"], r["stop"] or "-",
      "ok={ok} empty={empty} skipped_done={skipped_done} sent={sent_this_run} remaining={n}".format(n=len(r["remaining"]), **r))
' "$line")
  save_state SEALED_SENT "$sent_total"
  log "${pair}: ${summary}（要求の累計 ${sent_total} / ${max_requests}、止まった理由 ${got_stop}）"
  upload_pair "$pair"
  "$PROBE_SCRIPT_DIR/45_collect_s3.sh" "$pair" --execute
  if [[ "$got_stop" != "-" ]]; then stop="$got_stop"; break; fi
done

case "$stop" in
  "") log "指定の通貨を終えた。0 本の要求が残れば 39_sealed_fetch.sh --plan ${plan} --redo-empty --execute（上限の内）。終わったら 90_teardown.sh --execute" ;;
  first_request_empty) log "停止: 通貨のこの回の最初の要求が 0 本（端末の取り寄せが始まっていない）。他の通貨で確かめずに司令塔に報告する" ;;
  time_limit) log "停止: 要求の打ち切り（起動から $(( REQUEST_TIME_LIMIT_SECONDS / 60 )) 分）。済んだ分は回収済み。90_teardown.sh --execute" ;;
  request_limit) log "停止: 要求の上限 ${max_requests}。済んだ分は回収済み。90_teardown.sh --execute" ;;
  *) log "停止: ${stop}。済んだ分は回収済み。司令塔に報告する" ;;
esac
