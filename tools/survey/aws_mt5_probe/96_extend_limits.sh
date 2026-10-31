#!/usr/bin/env bash
# 作業上限の延長（人間の明示の決定の後だけ。D15 §2.6「上限の延長は人間の明示の決定」）。
# 動いている起動の 3 つの期限（要求の打ち切り・作業役が必ず終了する時刻・Windows の中の自動の停止）を、
# 人間が決めた時刻に延ばす。記録を偽らないため、起動の時刻（LAUNCHED_AT_EPOCH・LAUNCHED_AT_UTC・DEADLINE_UTC）は
# 書き換えず、状態のファイル（state.env）に「延長」の項目を足す（回数・適用の時刻・延長の前と後の期限・
# 起動の種類・決定の原文（base64）とその SHA-256）。読める形の原文は _state/extend_<回>_decision.txt に写す。
# 以後のスクリプトは lib.sh の effective_limits で延長の値を使う（39・95・20・check_deadline）。
#
# 守ること:
# - 短縮には使えない。3 つとも、既定（起動 + 起動の種類ごとの秒数）と今の期限より前の時刻は拒否する。
#   少なくとも 1 つは今の期限より後であること。打ち切り < 終了 < 自動の停止 の順であること。
# - 今の終了の時刻を過ぎた起動は延ばさない（90_teardown.sh --execute で終了する）。
# - --execute のときだけ、Windows の自動の停止を SSM で取り消して（shutdown /a）新しい時刻に予約し直し、
#   成功した後に状態のファイルへ書く。--execute が無ければ何を書くかと費用の見積りを表示するだけ。
# - 動いている見張り（95_deadline_guard.sh）は起動の時に期限を読むだけなので、延長の後に止めて起動し直す。
# - 取得（39_sealed_fetch.sh）が動いている間は --execute を拒否する（状態のファイルを同時に書かないため）。
#
# 使い方:
#   tools/survey/aws_mt5_probe/96_extend_limits.sh --cutoff-until 2026-10-31T05:00:00Z \
#     --deadline-until 2026-10-31T06:00:00Z --shutdown-until 2026-10-31T06:30:00Z \
#     --decision-file <人間の決定の原文.txt> [--execute]
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
cutoff_iso="" deadline_iso="" shutdown_iso="" decision_file=""
while (( $# > 0 )); do
  case "$1" in
    --cutoff-until) cutoff_iso="${2:?--cutoff-until の後に UTC の時刻}"; shift ;;
    --deadline-until) deadline_iso="${2:?--deadline-until の後に UTC の時刻}"; shift ;;
    --shutdown-until) shutdown_iso="${2:?--shutdown-until の後に UTC の時刻}"; shift ;;
    --decision-file) decision_file="${2:?--decision-file の後に人間の決定の原文のファイル}"; shift ;;
    --execute|--dry-run) ;;
    *) die "知らない引数: $1" ;;
  esac
  shift
done
[[ -n "$cutoff_iso" && -n "$deadline_iso" && -n "$shutdown_iso" ]] \
  || die "--cutoff-until・--deadline-until・--shutdown-until の 3 つを渡す（UTC。例 2026-10-31T05:00:00Z）"
[[ -n "$decision_file" && -f "$decision_file" && -s "$decision_file" ]] \
  || die "--decision-file に人間の決定の原文のファイル（空でない）を渡す"
grep -q '[^[:space:]]' "$decision_file" || die "決定の原文のファイルが空白だけ: ${decision_file}"

epoch_of() {  # epoch_of <YYYY-MM-DDTHH:MM:SSZ> → UNIX 秒（UTC の Z 付きだけ受け付ける）
  [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] \
    || die "時刻は UTC の YYYY-MM-DDTHH:MM:SSZ で渡す: $1"
  "$PY_MAC" -I -c 'import sys, datetime; print(int(datetime.datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()))' "$1" \
    || die "時刻を読めない: $1"
}
new_cutoff=$(epoch_of "$cutoff_iso")
new_deadline=$(epoch_of "$deadline_iso")
new_shutdown=$(epoch_of "$shutdown_iso")

require_profile
load_state
now=$(date +%s)
def_cutoff=$(( LAUNCHED_AT_EPOCH + REQUEST_TIME_LIMIT_SECONDS ))
def_deadline=$(( LAUNCHED_AT_EPOCH + PROBE_DEADLINE_SECONDS ))
def_shutdown=$(( LAUNCHED_AT_EPOCH + PROBE_SHUTDOWN_SECONDS ))
(( now < DEADLINE_AT_EPOCH )) \
  || die "今の終了の時刻（$(utc_of "$DEADLINE_AT_EPOCH")）を過ぎている。延ばさずに 90_teardown.sh --execute で終了する"

check_not_shorter() {  # check_not_shorter <名前> <新> <既定> <今>
  (( $2 >= $3 )) || die "$1 の延長 $(utc_of "$2") が既定（$(utc_of "$3")）より前。延長は短縮に使えない"
  (( $2 >= $4 )) || die "$1 の延長 $(utc_of "$2") が今の期限（$(utc_of "$4")）より前。延長は短縮に使えない"
  (( $2 > now )) || die "$1 の延長 $(utc_of "$2") が今より前"
}
check_not_shorter "打ち切り" "$new_cutoff" "$def_cutoff" "$CUTOFF_AT_EPOCH"
check_not_shorter "終了" "$new_deadline" "$def_deadline" "$DEADLINE_AT_EPOCH"
check_not_shorter "自動の停止" "$new_shutdown" "$def_shutdown" "$SHUTDOWN_AT_EPOCH"
(( new_cutoff > CUTOFF_AT_EPOCH || new_deadline > DEADLINE_AT_EPOCH || new_shutdown > SHUTDOWN_AT_EPOCH )) \
  || die "3 つとも今の期限と同じ。延ばすものが無い"
(( new_cutoff < new_deadline && new_deadline < new_shutdown )) \
  || die "打ち切り < 終了 < 自動の停止 の順にする（$(utc_of "$new_cutoff") / $(utc_of "$new_deadline") / $(utc_of "$new_shutdown")）"
(( new_shutdown - now >= 120 )) || die "自動の停止が今から 2 分未満（Windows に予約できない）"

n=$(( ${EXTEND_COUNT:-0} + 1 ))
applied_utc=$(utc_of "$now")
decision_sha=$(sha256_of "$decision_file")
decision_copy="${STATE_DIR}/extend_${n}_decision.txt"

cat >&2 <<INFO
== 作業上限の延長 ${n} 回目（${INSTANCE_ID}、起動の種類 ${PROBE_RUN_KIND}、起動 ${LAUNCHED_AT_UTC:-$(utc_of "$LAUNCHED_AT_EPOCH")}）
  起動の時刻は書き換えない。状態のファイル ${STATE_FILE} に延長の項目を足す。
  要求の打ち切り: $(utc_of "$CUTOFF_AT_EPOCH") → $(utc_of "$new_cutoff")（起動から $(( (new_cutoff - LAUNCHED_AT_EPOCH) / 60 )) 分。既定 $(utc_of "$def_cutoff")）
  必ず終了する時刻: $(utc_of "$DEADLINE_AT_EPOCH") → $(utc_of "$new_deadline")（起動から $(( (new_deadline - LAUNCHED_AT_EPOCH) / 60 )) 分。既定 $(utc_of "$def_deadline")）
  Windows の自動の停止: $(utc_of "$SHUTDOWN_AT_EPOCH") → $(utc_of "$new_shutdown")（起動から $(( (new_shutdown - LAUNCHED_AT_EPOCH) / 60 )) 分。既定 $(utc_of "$def_shutdown")）
  人間の決定の原文: ${decision_file}（SHA-256 ${decision_sha}。${decision_copy} に写す）
  Windows: SSM で shutdown /a の後に $(utc_of "$new_shutdown") に予約し直す（成功した後に状態へ書く）
INFO
echo "== 費用の見積り（米ドル、税込み。単価は 00_check.sh と同じ。CPU の無制限の追加分は 2 vCPU を全部使い続けた上限）" >&2
"$PY_MAC" -I - "$PRICE_INSTANCE_HOUR" "$PRICE_GP3_GB_MONTH" "$PRICE_IPV4_HOUR" "$PRICE_EGRESS_GB" \
  "$PRICE_CREDIT_VCPU_HOUR" "$TAX_RATE" "$PROBE_ROOT_GB" "$LAUNCHED_AT_EPOCH" "$new_deadline" "$new_shutdown" <<'PY' >&2
import sys
inst, gp3, ip4, egress, credit, tax, gb = map(float, sys.argv[1:8])
launched, deadline, shutdown = map(int, sys.argv[8:11])
def base(hours):
    return (inst * hours + gb * gp3 * hours / 720 + ip4 * hours) * (1 + tax)
def cpu(hours):
    return 2 * credit * max(hours, 0) * (1 + tax)
h_dead = (deadline - launched) / 3600
h_stop = (shutdown - launched) / 3600
print(f"  起動から終了の時刻まで {h_dead:.2f} 時間: インスタンス・ディスク・IPv4 約 {base(h_dead):.2f}")
print(f"  起動から自動の停止まで {h_stop:.2f} 時間（止め忘れの上限）: 約 {base(h_stop):.2f}")
# 無制限に切り替えた時刻は道具では分からないので、起動から数える（既に発生した分を含む保守的な上限。
# 延長を重ねても過小にならない）。
print(f"  CPU の無制限の追加分（起動から自動の停止まで {h_stop:.2f} 時間、上限）: 約 {cpu(h_stop):.2f}")
print(f"  送信（上限 1 GB）: 約 {egress * 1 * (1 + tax):.2f}")
expect = base(h_dead) + cpu(h_dead) + egress * (1 + tax)
total = base(h_stop) + cpu(h_stop) + egress * (1 + tax)
print(f"  この起動の見込み（終了の時刻まで）: 約 {expect:.2f}")
print(f"  この起動の上限の合計（自動の停止まで）: 約 {total:.2f}")
PY
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何も書いていない・何も送っていない。" >&2; exit 0; }

# 動いている取得（39）と状態のファイルを同時に書かない。今動いている 39 はこの PR の前の lib.sh を読み込んで
# いてロックを使わないので、止まる（古い打ち切りで time_limit）のを待ってから延ばす。
fetch_pat="${PROBE_FETCH_PGREP_PATTERN:-39_sealed_fetch[.]sh}"
if pgrep -f "$fetch_pat" >/dev/null 2>&1; then
  die "取得（39_sealed_fetch.sh）が動いている。止まってから延ばす（状態のファイルを同時に書かないため）。何も書いていない・送っていない"
fi
assert_tagged_instance "$INSTANCE_ID"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
autostop_ps "$new_shutdown" >"$work/autostop.ps1"
log "Windows の中の自動の停止を $(utc_of "$new_shutdown") に予約し直す（延長 ${n} 回目）"
ssm_ps "$INSTANCE_ID" 120 "$work/autostop.ps1" \
  || die "Windows の自動の停止を予約し直せなかった。状態は書いていない（期限は延びていない）。予約が消えた可能性があるので 20_prepare.sh --execute で今の時刻に戻す"

cp "$decision_file" "$decision_copy"
# 原文は状態のファイルでは base64（状態のファイルを ASCII に保つ。lib.sh の sed・save_state の grep が
# 多バイトの文字で止まらないように）。読める形は ${decision_copy}。延長の記録と 3 つの期限は 1 回でまとめて書く。
save_state_many \
  "EXTEND_${n}_APPLIED_AT_UTC" "$applied_utc" \
  "EXTEND_${n}_RUN_KIND" "$PROBE_RUN_KIND" \
  "EXTEND_${n}_PREV_CUTOFF_UTC" "$(utc_of "$CUTOFF_AT_EPOCH")" \
  "EXTEND_${n}_PREV_DEADLINE_UTC" "$(utc_of "$DEADLINE_AT_EPOCH")" \
  "EXTEND_${n}_PREV_SHUTDOWN_UTC" "$(utc_of "$SHUTDOWN_AT_EPOCH")" \
  "EXTEND_${n}_CUTOFF_UTC" "$(utc_of "$new_cutoff")" \
  "EXTEND_${n}_DEADLINE_UTC" "$(utc_of "$new_deadline")" \
  "EXTEND_${n}_SHUTDOWN_UTC" "$(utc_of "$new_shutdown")" \
  "EXTEND_${n}_DECISION_SHA256" "$decision_sha" \
  "EXTEND_${n}_DECISION_FILE" "$decision_copy" \
  "EXTEND_${n}_DECISION_B64" "$(base64 <"$decision_file" | tr -d '\n')" \
  EXTEND_CUTOFF_AT_EPOCH "$new_cutoff" \
  EXTEND_DEADLINE_AT_EPOCH "$new_deadline" \
  EXTEND_SHUTDOWN_AT_EPOCH "$new_shutdown" \
  EXTEND_APPLIED_AT_UTC "$applied_utc" \
  EXTEND_COUNT "$n"
# 書いた値を読み直して確かめる（Windows の予約は延長済みなので、合わなければ見張りは古い期限のまま）。
load_state
[[ "${EXTEND_COUNT:-}" == "$n" && "$CUTOFF_AT_EPOCH" == "$new_cutoff" && "$DEADLINE_AT_EPOCH" == "$new_deadline" \
   && "$SHUTDOWN_AT_EPOCH" == "$new_shutdown" ]] \
  || die "状態のファイルの延長を読み直すと合わない。Windows の自動の停止は $(utc_of "$new_shutdown") に延びている。司令塔に報告する"
log "延長 ${n} 回目を状態に書いた。次: 動いている見張りを止めて 95_deadline_guard.sh を起動し直し、39_sealed_fetch.sh で取得を再開する"
