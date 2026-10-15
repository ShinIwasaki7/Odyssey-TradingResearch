#!/usr/bin/env bash
# 時刻の調査（D12 §11.7。人間の決定 D12-34・D12-35）の 1 段を実行し、回収し、Mac で評価する。
# 1 分足だけ。2024 年以降は要求しない（道具が区間を確かめる）。価格の値は保存しない（足の時刻・件数と、
# 火曜の区間の配布 CSV との照合の統計だけ。照合は Windows の上でメモリの中で行う）。
#
# 段の順序（D12 §11.7.3）: A2 → （決定木の追加: extra）→ A1 → B1 → B2 → （extra）→ G。
# 段 G は空白の測定（D12 §11.9.2。人間の決定 D12-38・D12-39）の代表週 112 要求。足の時刻だけを保存し、
# 集計は時刻の規則が確定した後に Mac で行う（時刻が未確定の年・通貨は未集計）。
# 評価が「司令塔の判断が要る事項」を出したら止まり、司令塔に報告する（T3、上限を超える追加）。
# 上限（T7）: 要求 67 ＋ 追加 20、起動から 3 時間、ディスクの空き 3 GB（道具が止まる）。
# 段 G の上限: 要求 112、起動から 4 時間（check_deadline）。時刻の調査が 3 時間で止まった後も段 G は行う。
#
# 使い方: tools/survey/aws_mt5_probe/35_time_survey.sh <A2|A1|B1|B2|extra|G> [--execute]
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
stage=""
for a in "$@"; do
  case "$a" in
    A2|A1|B1|B2|extra|G) stage="$a" ;;
    --execute|--dry-run) ;;
    *) die "知らない引数: $a" ;;
  esac
done
[[ -n "$stage" ]] || die "段を指定する（A2・A1・B1・B2・extra・G）"
require_profile
load_state
check_deadline
assert_tagged_instance "$INSTANCE_ID"
if [[ "$stage" != "G" ]] && (( $(date +%s) - LAUNCHED_AT_EPOCH >= TS_TIME_LIMIT_SECONDS )); then
  die "T7: 時刻の調査の作業上限（起動から 3 時間）に達した。残りの時間で段 G（空白の測定）だけを行う"
fi

readonly TS_BASE_LIMIT=67 TS_EXTRA_LIMIT=20 GAP_LIMIT=112
sent="${TS_SENT:-0}"
extra_sent="${TS_EXTRA_SENT:-0}"
eval_dir="${RESULT_ROOT}/${PROBE_DATE}/ts-eval"
mkdir -p "$eval_dir"
run_no=$(( ${TS_RUNS:-0} + 1 ))
phase="ts-${stage}-${run_no}"
if [[ "$stage" == "G" ]]; then
  n=$GAP_LIMIT
  max=$(( GAP_LIMIT - ${TS_GAP_SENT:-0} ))
elif [[ "$stage" == "extra" ]]; then
  plan="${eval_dir}/extra_plan.json"
  [[ -s "$plan" ]] || die "決定木の追加の計画が無い（先に段を実行して評価する）"
  n=$("$PY_MAC" -I -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$plan")
  max=$(( TS_EXTRA_LIMIT - extra_sent ))
else
  n=$(cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -c "from tools.survey import mt5_api_probe as p; print(len(p.TIME_SURVEY_STAGES['${stage}']))")
  max=$(( TS_BASE_LIMIT + TS_EXTRA_LIMIT - sent - (TS_EXTRA_LIMIT - extra_sent) ))
fi
(( max > 0 )) || die "T7: 要求の上限に達した（済み ${sent}、追加済み ${extra_sent}）。報告して終了する"
cat >&2 <<INFO
== 時刻の調査の段 ${stage}（${INSTANCE_ID}、出力 ${phase}）
  この段の要求: ${n}（この回に出せる上限 ${max}）。済み ${sent}（うち追加 ${extra_sent}）
  1 分足だけ。価格の値は保存しない。ディスクの空きが 3 GB を切ったら道具が止まる
INFO
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }

if [[ "${TS_E1_REF_SHA:-}" == "" ]]; then
  ref="${eval_dir}/e1_ref.json.gz"
  (cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -m tools.survey.mt5_time_survey e1-ref --out "$ref") >&2
  ssm_put_file "$INSTANCE_ID" "$ref" 'C:\probe\e1_ref.json.gz'
  save_state TS_E1_REF_SHA "$(sha256_of "$ref")"
fi
pargs="timesurvey --stage ${stage} --out-dir C:\\probe\\out\\${phase} --max-requests ${max} --e1-ref C:\\probe\\e1_ref.json.gz"
if [[ "$stage" == "extra" ]]; then
  ssm_put_file "$INSTANCE_ID" "$plan" 'C:\probe\extra_plan.json'
  pargs="${pargs} --plan-file C:\\probe\\extra_plan.json"
fi
ssm_run_probe_task "$INSTANCE_ID" "$phase" "C:\\probe\\out\\${phase}" "$pargs"
save_state TS_RUNS "$run_no"
"$PROBE_SCRIPT_DIR/40_collect.sh" "$phase" --execute

summary="${RESULT_ROOT}/${PROBE_DATE}/${phase}/summary_ts-${stage}.json"
done_now=$("$PY_MAC" -I -c 'import json,sys; print(sum(1 for r in json.load(open(sys.argv[1]))["requests"] if not r["skipped"]))' "$summary")
save_state TS_SENT $(( sent + done_now ))
if [[ "$stage" == "extra" ]]; then save_state TS_EXTRA_SENT $(( extra_sent + done_now )); fi
if [[ "$stage" == "G" ]]; then
  save_state TS_GAP_SENT $(( ${TS_GAP_SENT:-0} + done_now ))
  save_state TS_SENT "$sent"  # 段 G は時刻の調査の要求の数に数えない
  log "空白の測定: ${done_now} 要求（足の時刻だけ）。集計は時刻の確定の後に Mac で行う"
  exit 0
fi
extra_used="$extra_sent"
if [[ "$stage" == "extra" ]]; then extra_used=$(( extra_sent + done_now )); fi
all=("${RESULT_ROOT}/${PROBE_DATE}"/ts-*/summary_ts-*.json)
(cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -m tools.survey.mt5_time_survey evaluate "${all[@]}" \
  --extra-used "$extra_used" --report "${eval_dir}/report.md" --plan-out "${eval_dir}/extra_plan.json")
log "評価: ${eval_dir}/report.md（済み $(( sent + done_now )) 要求）"
