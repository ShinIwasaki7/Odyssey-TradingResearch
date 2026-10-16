#!/usr/bin/env bash
# 最後の調査（D12 付録 K-10。人間の決定 D12-41 と第 12 回の決定）を実行し、回収し、Mac で判定する。
# USDJPY だけ、1 分足だけ、2017・2019 年の 9 区間だけ（一覧は mt5_api_probe.py の FINAL_STAGE）。
# 2024 年以降は要求しない（道具が区間を確かめる）。価格の値は保存しない（足の時刻・件数と、
# 配布 CSV の bid・ask・mid との照合の差の統計だけ。照合は Windows の上でメモリの中で行う）。
#
# 上限（付録 K-10.2・K-10.3）:
#   要求 12（計画 9。残り 3 は取り寄せの空振りのやり直しだけ。他の区間は足さない。道具の側でも数える）
#   起動から 1 時間 30 分で要求を打ち切り、2 時間で終了（95_deadline_guard.sh）、2 時間 30 分で Windows の自動の停止
# 判定の区分は「確認済み」「仮定付き」の 2 つだけ（人間の第 12 回の決定）。上限に達したら、その時点の
# データで判定して閉じる（追加の再開はしない）。
#
# 使い方:
#   tools/survey/aws_mt5_probe/37_final_survey.sh [--execute]                     # 9 区間を出す
#   tools/survey/aws_mt5_probe/37_final_survey.sh --only <要求,...> [--execute]   # 一覧の中の区間だけ
#   tools/survey/aws_mt5_probe/37_final_survey.sh --remaining [--execute]         # まだ出していない区間だけ
#   tools/survey/aws_mt5_probe/37_final_survey.sh --redo-empty [--execute]        # 0 本だった区間だけやり直す
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
only=""
redo_empty=0
remaining=0
while (( $# > 0 )); do
  case "$1" in
    --only) only="${2:?--only の後に要求の名前}"; shift ;;
    --redo-empty) redo_empty=1 ;;
    --remaining) remaining=1 ;;
    --execute|--dry-run) ;;
    *) die "知らない引数: $1" ;;
  esac
  shift
done
require_profile
load_state
check_deadline
assert_tagged_instance "$INSTANCE_ID"
[[ "${RUN_KIND:-}" == "final" ]] || die "この起動は最後の調査の起動ではない（状態の RUN_KIND=${RUN_KIND:-なし}）"
elapsed=$(( $(date +%s) - LAUNCHED_AT_EPOCH ))
if (( elapsed >= FINAL_TIME_LIMIT_SECONDS )); then
  die "作業上限（起動から $(( FINAL_TIME_LIMIT_SECONDS / 60 )) 分）に達した。要求は出さない。その時点のデータで判定し、90_teardown.sh --execute で終了する"
fi

readonly FINAL_LIMIT=12 FINAL_PLANNED=9
sent="${K10_SENT:-0}"
max=$(( FINAL_LIMIT - sent ))
(( max > 0 )) || die "要求の上限 ${FINAL_LIMIT} に達した（済み ${sent}）。その時点のデータで判定して閉じる"
eval_dir="${RESULT_ROOT}/${PROBE_DATE}/k10-eval"
mkdir -p "$eval_dir"
shopt -s nullglob
summaries=("${RESULT_ROOT}/${PROBE_DATE}"/ts-K10-*/summary_ts-K10.json)
shopt -u nullglob

if [[ "$redo_empty" == 1 ]]; then
  (( ${#summaries[@]} > 0 )) || die "まだ 1 回も実行していない（やり直す区間が無い）"
  # 最後に実行した記録で、区間の中の足が 0 本だった要求だけ（取り寄せの空振り）。
  only=$("$PY_MAC" -I - "${summaries[@]}" <<'PY'
import json, sys
latest = {}
for p in sys.argv[1:]:
    for r in json.load(open(p, encoding="utf-8"))["requests"]:
        if r["skipped"]:
            continue
        if r["rid"] not in latest or r["executed_at_utc"] >= latest[r["rid"]]["executed_at_utc"]:
            latest[r["rid"]] = r
print(",".join(rid for rid, r in latest.items() if r["returned_count"] == 0))
PY
)
  [[ -n "$only" ]] || die "0 本の区間は無い（やり直さない）"
fi
if [[ "$remaining" == 1 ]]; then
  (( ${#summaries[@]} > 0 )) || die "まだ 1 回も実行していない（--remaining は 2 回目から。初回は --only か引数なし）"
  # 一覧（FINAL_STAGE）のうち、まだ出していない（記録が無いか、上限で出さなかった）区間。
  only=$(cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" - "${summaries[@]}" <<'PY'
import json, sys
from tools.survey import mt5_api_probe as p
done = {
    r["rid"]
    for f in sys.argv[1:]
    for r in json.load(open(f, encoding="utf-8"))["requests"]
    if not r["skipped"]
}
print(",".join(r.rid for r in p.FINAL_STAGE if r.rid not in done))
PY
)
  [[ -n "$only" ]] || die "一覧の区間は全部出し済み"
fi
if [[ -n "$only" ]]; then
  n=$(awk -F, '{print NF}' <<<"$only")
else
  n=$FINAL_PLANNED
  (( sent == 0 )) || die "9 区間は出し済み（済み ${sent}）。やり直しは --redo-empty か --only で、一覧の中の区間だけ"
fi
(( n <= max )) || die "この回の要求 ${n} が残り ${max} を超える（上限 ${FINAL_LIMIT}、済み ${sent}）"
run_no=$(( ${K10_RUNS:-0} + 1 ))
phase="ts-K10-${run_no}"
cat >&2 <<INFO
== 最後の調査（${INSTANCE_ID}、出力 ${phase}）
  この回の要求: ${n}${only:+（${only}）}。残り ${max}（上限 ${FINAL_LIMIT}、済み ${sent}）
  USDJPY の 1 分足だけ。2017・2019 年だけ。価格の値は保存しない（差の統計と時刻だけ）
  起動からの経過 $(( elapsed / 60 )) 分（打ち切り $(( FINAL_TIME_LIMIT_SECONDS / 60 )) 分、終了 $(( PROBE_DEADLINE_SECONDS / 60 )) 分）
INFO
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }

if [[ "${K10_REF_SHA:-}" == "" ]]; then
  # 配布 CSV の bid・ask・mid の 1 分足（最後の調査の 9 区間の前後 5 時間）。置いてあれば使う。
  ref="${K10_REF:-${RESULT_ROOT}/_k10/final_ref.json.gz}"
  if [[ ! -s "$ref" ]]; then
    mkdir -p "$(dirname "$ref")"
    (cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -m tools.survey.mt5_time_survey final-ref --out "$ref") >&2
  fi
  ssm_put_file "$INSTANCE_ID" "$ref" 'C:\probe\final_ref.json.gz'
  save_state K10_REF_SHA "$(sha256_of "$ref")"
fi
pargs="timesurvey --stage K10 --out-dir C:\\probe\\out\\${phase} --max-requests ${max} --e1-ref C:\\probe\\final_ref.json.gz"
[[ -z "$only" ]] || pargs="${pargs} --only ${only}"
ssm_run_probe_task "$INSTANCE_ID" "$phase" "C:\\probe\\out\\${phase}" "$pargs"
save_state K10_RUNS "$run_no"
"$PROBE_SCRIPT_DIR/40_collect.sh" "$phase" --execute

summary="${RESULT_ROOT}/${PROBE_DATE}/${phase}/summary_ts-K10.json"
done_now=$("$PY_MAC" -I -c 'import json,sys; print(sum(1 for r in json.load(open(sys.argv[1]))["requests"] if not r["skipped"]))' "$summary")
save_state K10_SENT $(( sent + done_now ))
all=("${RESULT_ROOT}/${PROBE_DATE}"/ts-K10-*/summary_ts-K10.json)
(cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -m tools.survey.mt5_time_survey final-evaluate "${all[@]}" \
  --report "${eval_dir}/report.md" --json-out "${eval_dir}/result.json")
log "判定: ${eval_dir}/report.md（済み $(( sent + done_now )) / ${FINAL_LIMIT} 要求）"
