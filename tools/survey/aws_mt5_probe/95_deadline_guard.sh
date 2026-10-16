#!/usr/bin/env bash
# 作業上限の見張り: 起動から PROBE_DEADLINE_SECONDS（全期間取得は 4 時間。付録 K-16.6。前回の最後の調査は 2 時間）
# になったら 90_teardown.sh --execute で終了する。S3 の一時バケットは、回収が済んでいなければ消さずに残す（90 の説明）。
# 10_launch.sh の後に、作業役が Mac で裏で動かしておく（Mac が眠ると遅れるので、check_deadline も併用する）。
#
# 使い方: tools/survey/aws_mt5_probe/95_deadline_guard.sh
source "$(dirname "$0")/lib.sh"
require_profile
load_state
deadline=$(( LAUNCHED_AT_EPOCH + PROBE_DEADLINE_SECONDS ))
log "見張り: ${INSTANCE_ID} を ${DEADLINE_UTC} に終了する"
while (( $(date +%s) < deadline )); do
  [[ -f "$STATE_FILE" ]] || { log "状態のファイルが無い（片付け済み）。見張りを終える"; exit 0; }
  sleep 60
done
log "終了の時刻（起動から $(( PROBE_DEADLINE_SECONDS / 60 )) 分）に達した。終了する"
exec "$PROBE_SCRIPT_DIR/90_teardown.sh" --execute
