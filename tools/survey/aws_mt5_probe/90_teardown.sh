#!/usr/bin/env bash
# 終了と削除の確認: 試験のタグの付いたインスタンスを終了し、セキュリティグループ・キーペアを消し、
# 残りが無いことを describe で確かめる。人間が作った IAM のロール・インスタンスプロファイル・ユーザーは消さない。
# 作業上限（4 時間）を超えていても、このスクリプトは動く。
#
# 使い方: tools/survey/aws_mt5_probe/90_teardown.sh            # 何を消すかを表示するだけ
#         tools/survey/aws_mt5_probe/90_teardown.sh --execute  # 終了と削除を行う
#         tools/survey/aws_mt5_probe/90_teardown.sh --verify   # 残りの確認だけ（読み取り）
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
verify_only=0
for a in "$@"; do [[ "$a" == "--verify" ]] && verify_only=1; done
require_profile

verify() {
  echo "== 残りの確認（東京リージョン。どれも 0 のはず）"
  printf '  試験のインスタンス（終了済みを除く）: '
  aws_p ec2 describe-instances --filters "$(tag_filter)" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
    --query "length(Reservations[].Instances[])" --output text
  printf '  試験のボリューム: '; aws_p ec2 describe-volumes --filters "$(tag_filter)" --query "length(Volumes)" --output text
  printf '  使われていないボリューム（全部）: '; aws_p ec2 describe-volumes --filters Name=status,Values=available --query "length(Volumes)" --output text
  printf '  自分のスナップショット（全部）: '; aws_p ec2 describe-snapshots --owner-ids self --query "length(Snapshots)" --output text
  printf '  自分の AMI（全部）: '; aws_p ec2 describe-images --owners self --query "length(Images)" --output text
  printf '  Elastic IP（全部）: '; aws_p ec2 describe-addresses --query "length(Addresses)" --output text
  printf '  試験のネットワークインターフェース: '; aws_p ec2 describe-network-interfaces --filters "$(tag_filter)" --query "length(NetworkInterfaces)" --output text
  printf '  試験のセキュリティグループ: '; aws_p ec2 describe-security-groups --filters "$(tag_filter)" --query "length(SecurityGroups)" --output text
  printf '  試験のキーペア: '; aws_p ec2 describe-key-pairs --filters "$(tag_filter)" --query "length(KeyPairs)" --output text
  echo "  人間が作ったもの（消していない。不要になったら人間が消す）: IAM ロールとインスタンスプロファイル ${PROBE_SSM_ROLE}、IAM ユーザー odyssey-mt5-probe-operator とそのアクセスキー、Budgets の予算"
}
if [[ "$verify_only" == 1 ]]; then verify; exit 0; fi

instances=$(aws_p ec2 describe-instances --filters "$(tag_filter)" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
  --query "Reservations[].Instances[].InstanceId" --output text)
sgs=$(aws_p ec2 describe-security-groups --filters "$(tag_filter)" --query "SecurityGroups[].GroupId" --output text)
keys=$(aws_p ec2 describe-key-pairs --filters "$(tag_filter)" --query "KeyPairs[].KeyName" --output text)
cat >&2 <<INFO
== 終了と削除の計画（タグ ${PROBE_TAG_KEY}=${PROBE_TAG_VALUE} のものだけ）
  終了するインスタンス: ${instances:-なし}
  消すセキュリティグループ: ${sgs:-なし}
  消すキーペア: ${keys:-なし}（Mac の ${KEY_DIR}/<名前>.pem も消す）
INFO
if [[ "$EXECUTE" != 1 ]]; then echo "(dry-run) 何もしていない。" >&2; verify; exit 0; fi

if [[ -n "$instances" ]]; then
  # shellcheck disable=SC2086
  aws_p ec2 terminate-instances --instance-ids $instances --query "TerminatingInstances[].[InstanceId,CurrentState.Name]" --output text
  # shellcheck disable=SC2086
  aws_p ec2 wait instance-terminated --instance-ids $instances
  log "終了した: ${instances}（終了の時刻 $(date -u +%Y-%m-%dT%H:%M:%SZ)）"
fi
for sg in $sgs; do
  for i in 1 2 3 4 5 6; do
    if aws_p ec2 delete-security-group --group-id "$sg" 2>/dev/null; then log "消した: ${sg}"; break; fi
    log "セキュリティグループ ${sg} がまだ使われている。待つ（${i}/6）"; sleep 20
  done
done
for k in $keys; do
  aws_p ec2 delete-key-pair --key-name "$k" >/dev/null && log "消した: キーペア ${k}"
  rm -f "${KEY_DIR}/${k}.pem" && log "消した: ${KEY_DIR}/${k}.pem"
done
if [[ -f "$STATE_FILE" ]]; then
  load_state
  now=$(date +%s)
  hours=$("$PY_MAC" -I -c "import sys; print(f'{(int(sys.argv[1]) - int(sys.argv[2])) / 3600:.2f}')" "$now" "$LAUNCHED_AT_EPOCH")
  echo "== 費用の概算（起動 ${LAUNCHED_AT_UTC} から終了まで ${hours} 時間。Cost Explorer の反映は翌日）"
  "$PY_MAC" -I - "$hours" "$PRICE_INSTANCE_HOUR" "$PRICE_GP3_GB_MONTH" "$PRICE_IPV4_HOUR" "$TAX_RATE" "$PROBE_ROOT_GB" <<'PY'
import sys
h, inst, gp3, ip4, tax, gb = map(float, sys.argv[1:])
sub = inst * h + gb * gp3 * h / 720 + ip4 * h
print(f"  インスタンス・ディスク・IPv4: 約 {sub * (1 + tax):.3f} 米ドル（税込み。送信の量は含まない。自動の停止の後の停止中はインスタンスの分が過大）")
PY
  mv "$STATE_FILE" "${STATE_FILE}.done-$(date +%Y%m%dT%H%M%S)"
fi
verify
