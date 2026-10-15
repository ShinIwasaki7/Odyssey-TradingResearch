#!/usr/bin/env bash
# 端末の 1 分足の履歴の取り寄せを起こすためだけの tick の要求（1 回だけ）。人間の決定（2026-10-16）:
# 「(a) tick 要求を 1 回だけ出す（USDJPY 2023-12-27 00:00〜00:05。返った tick は保存しない）（推奨）」。
# 返った tick の値は保存しない（件数と最初・最後の時刻だけ）。2024 年以降は要求しない。
#
# 使い方: tools/survey/aws_mt5_probe/36_sync_trigger.sh [--execute]
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
require_profile
load_state
check_deadline
assert_tagged_instance "$INSTANCE_ID"
[[ -z "${SYNC_TRIGGER_DONE:-}" ]] || die "tick の要求は 1 回だけ（済み: ${SYNC_TRIGGER_DONE}）"
echo "== tick の要求 1 回（USDJPY 2023-12-27 00:00〜00:05）。値は保存しない" >&2
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }
save_state SYNC_TRIGGER_DONE "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ssm_run_probe_task "$INSTANCE_ID" "sync-trigger" 'C:\probe\out\sync-trigger' 'synctrigger --out-dir C:\probe\out\sync-trigger'
"$PROBE_SCRIPT_DIR/40_collect.sh" sync-trigger --execute
