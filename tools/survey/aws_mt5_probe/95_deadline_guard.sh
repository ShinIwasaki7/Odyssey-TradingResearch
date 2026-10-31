#!/usr/bin/env bash
# 作業上限の見張り: 終了の時刻（DEADLINE_AT_EPOCH。既定は起動 + PROBE_DEADLINE_SECONDS、延長（96_extend_limits.sh）
# があればその値）になったら 90_teardown.sh --execute で終了する。S3 の一時バケットは、回収が済んでいなければ消さずに残す（90 の説明）。
# 10_launch.sh の後に、作業役が Mac で裏で動かしておく（Mac が眠ると遅れるので、check_deadline も併用する）。
# 終了の時刻は起動の時に 1 回だけ読む。延長の後は、動いている見張りを止めて起動し直す（README「上限の延長」）。
#
# 使い方: tools/survey/aws_mt5_probe/95_deadline_guard.sh
source "$(dirname "$0")/lib.sh"
require_profile
load_state
deadline="$DEADLINE_AT_EPOCH"
log "見張り: ${INSTANCE_ID} を $(utc_of "$deadline") に終了する（${LIMITS_SOURCE}）"
while (( $(date +%s) < deadline )); do
  [[ -f "$STATE_FILE" ]] || { log "状態のファイルが無い（片付け済み）。見張りを終える"; exit 0; }
  sleep 60
done
log "終了の時刻（$(utc_of "$deadline")。起動から $(( (deadline - LAUNCHED_AT_EPOCH) / 60 )) 分）に達した。終了する"
exec "$PROBE_SCRIPT_DIR/90_teardown.sh" --execute
