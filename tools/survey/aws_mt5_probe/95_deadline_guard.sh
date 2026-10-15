#!/usr/bin/env bash
# 作業上限の見張り: 起動から 4 時間（時刻の調査 3 時間＋空白の測定 1 時間。D12-39）になったら 90_teardown.sh --execute で終了する。
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
log "作業上限（4 時間）に達した。終了する"
exec "$PROBE_SCRIPT_DIR/90_teardown.sh" --execute
