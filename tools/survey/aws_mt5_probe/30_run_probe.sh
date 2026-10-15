#!/usr/bin/env bash
# 段階 B: SSM で試験の道具（mt5_api_probe.py probe）を Windows で実行する。
#
# 要求する通貨と区間は、道具の「段」の名前で指定する（区間は道具の中に固定。どれも 2023-12-31 まで）:
#   usdjpy-time  … USDJPY 2019-03-05・2019-03-19 の 00:00〜02:00 UTC（既定。最初はこれだけ）
#   usdjpy-range … USDJPY 2017-01-03・2023-12-27 の 00:00〜02:00 UTC
#   others       … 他の 7 通貨 × 2017-01-03・2017-03-07・2019-03-05・2023-03-07 の 00:00〜02:00 UTC
#   offset-eu-dst … USDJPY 2023-03-28 の 00:00〜02:00 UTC（時刻のずれの原因の確認）
#   range-8      … 8 通貨 × 2017-01-10・2019-03-05・2023-12-05 の 00:00〜02:00 UTC（要約だけ。価格の行は保存しない）
#   m1-<通貨>    … 1 分足だけ、2023-12-05・2019-03-05・2017-01-10 の新しい順（USDJPY は 2017-01-10 だけ。要約だけ。空きが 3 GB を切ると止まる）
# 道具は要求の終わりが 2024-01-01T00:00Z を超えると何も要求せずに止まる。
#
# MT5 の端末は、人間が RDP でログインした Administrator のセッションで動いている。SSM の既定の実行者
# （SYSTEM）からは端末につながらないので、Administrator の対話のセッションで動くタスクを登録して実行する。
# RDP は「切断」でよい（サインアウトするとタスクが動かない）。
#
# 使い方: tools/survey/aws_mt5_probe/30_run_probe.sh [usdjpy-time|usdjpy-range|others] [--execute]
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
phase="usdjpy-time"
for a in "$@"; do
  case "$a" in
    usdjpy-time|usdjpy-range|others|offset-eu-dst|range-8|m1-[A-Z][A-Z][A-Z][A-Z][A-Z][A-Z]) phase="$a" ;;
    --execute|--dry-run) ;;
    *) die "知らない引数: $a（段の名前は usdjpy-time・usdjpy-range・others）" ;;
  esac
done
require_profile
load_state
check_deadline
assert_tagged_instance "$INSTANCE_ID"
tool_sha=$(sha256_of "$PROBE_TOOL")

# 道具が注文・売買・口座の関数を呼ばないことを、実行の前に文字列で確かめる（D12 §11.4）。
if grep -nE '\b(order_send|order_check|positions_get|positions_total|orders_get|orders_total|history_orders_get|history_orders_total|history_deals_get|history_deals_total|account_info|login)\s*\(' "$PROBE_TOOL"; then
  die "道具に使ってはいけない関数の呼び出しがある"
fi

cat >&2 <<INFO
== 試験の実行の計画（${INSTANCE_ID}）
  段: ${phase}
  実行: C:\\probe\\mt5_api_probe.py probe --phase ${phase} --out-dir C:\\probe\\out\\${phase}
  道具の SHA-256（Mac と Windows で一致を確かめる）: ${tool_sha}
  実行者: Administrator の対話のセッション（タスク ${PROBE_NAME}-${phase}）
  読まないもの: 端末のデータのフォルダ（自動で保存された最近の履歴）。結果は C:\\probe\\out だけ
INFO
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cat >"$work/run.ps1" <<PS
\$ErrorActionPreference = 'Stop'
\$h = (Get-FileHash -Algorithm SHA256 C:\\probe\\mt5_api_probe.py).Hash.ToLower()
if (\$h -ne '${tool_sha}') { throw "probe SHA-256 mismatch: \$h" }
if (-not (Get-Process -Name terminal64 -ErrorAction SilentlyContinue)) { throw 'MT5 terminal is not running (human must log in via RDP first)' }
\$out = 'C:\\probe\\out\\${phase}'
New-Item -ItemType Directory -Force -Path \$out | Out-Null
\$cmd = 'set "PYTHONUTF8=1" && "${PY_EXE}" C:\\probe\\mt5_api_probe.py probe --phase ${phase} --out-dir ' + \$out + ' > ' + \$out + '\\run.log 2>&1'
\$task = '${PROBE_NAME}-${phase}'
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
Get-Content \$out\\run.log
\$summary = Join-Path \$out 'summary_${phase}.json'
if (-not (Test-Path \$summary)) { throw 'summary json not created' }
# 件数・状態だけを表示する（価格は表示しない）。
\$j = Get-Content -Raw \$summary | ConvertFrom-Json
\$j.requests | ForEach-Object { '{0} {1} {2} count={3} retried={4} {5} err={6}' -f \$_.symbol, \$_.window, \$_.kind, \$_.returned_count, \$_.retried, \$_.tick_history, \$_.last_error }
PS
log "段 ${phase} を実行する"
ssm_ps "$INSTANCE_ID" 3600 "$work/run.ps1"
log "終わった。40_collect.sh ${phase} --execute で回収する"
