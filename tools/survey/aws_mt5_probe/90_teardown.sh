#!/usr/bin/env bash
# 終了と削除の確認: 試験のタグの付いたインスタンスを終了し、セキュリティグループ・キーペアを消し、
# 残りが無いことを describe で確かめる。人間が作った IAM のロール・インスタンスプロファイル・ユーザーは消さない。
# 作業上限（4 時間）を超えていても、このスクリプトは動く。
# 全期間取得（付録 K-16）の S3 の一時バケットは、回収が済んでいれば（S3 の manifest が Mac の manifest と同じで、
# S3 の全ファイルが Mac の置き場にあり、照合が合えば）中身ごと消し、消えたことを確かめる。済んでいなければ
# 消さずに残し、45_collect_s3.sh --all --execute の後にもう一度このスクリプトを実行するよう表示する
# （見張りの自動の終了で、回収の前のデータを失わないため）。
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
  printf '  試験の S3 の一時バケット: '
  if [[ -f "$S3_STATE_FILE" ]] && load_s3_state; then
    local st
    st=$(s3_bucket_state "$S3_BUCKET")
    case "$st" in
      exists) echo "1（${S3_BUCKET}。残っている）" ;;
      absent) echo "0（${S3_BUCKET} は無い。head-bucket が 404）" ;;
      *) echo "不明（${S3_BUCKET}。有無を確かめられない: ${st#unknown: }）" ;;
    esac
  else
    echo "0（記録なし）"
  fi
  echo "  人間が作ったもの（消していない。不要になったら人間が消す）: IAM ロールとインスタンスプロファイル ${PROBE_SSM_ROLE}、IAM ユーザー odyssey-mt5-probe-operator とそのアクセスキー、Budgets の予算"
}
if [[ "$verify_only" == 1 ]]; then verify; exit 0; fi

# S3 の一時バケットの回収が済んだか（済んでいれば 0）。価格は読まない（ファイルの有無と SHA-256 だけ）。
s3_collected() {
  local w key missing=0 example=""
  w=$(mktemp -d)
  aws_p s3 cp "s3://${S3_BUCKET}/${S3_KEY_PREFIX}/manifest.json" "$w/manifest.json" --only-show-errors 2>/dev/null \
    || { rm -rf "$w"; log "S3 に manifest が無い"; [[ -z "$(s3_keys)" ]]; return; }
  if [[ "$(sha256_of "$w/manifest.json")" != "${COLLECTED_MANIFEST_SHA256:-}" ]]; then
    rm -rf "$w"; log "S3 の manifest が、最後に回収した manifest と違う（回収が済んでいない）"; return 1
  fi
  rm -rf "$w"
  for key in $(s3_keys); do
    case "$key" in
      "${S3_KEY_PREFIX}/manifest.json"|"${S3_KEY_PREFIX}/fetch_log.jsonl") ;;
      "${S3_KEY_PREFIX}/"*.csv.gz) [[ -f "${FULL_ROOT}/${FETCH_DATE}/${key#"${S3_KEY_PREFIX}"/}" ]] || { missing=$(( missing + 1 )); example="$key"; } ;;
      *) log "知らないキー: ${key}"; missing=$(( missing + 1 )); example="$key" ;;
    esac
  done
  (( missing == 0 )) || { log "S3 にあって Mac に無いファイル: ${missing} 件（例 ${example}）"; return 1; }
  (cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -m tools.survey.mt5_api_probe fullverify --root "${FULL_ROOT}/${FETCH_DATE}") >&2
}
s3_keys() {
  aws_p s3api list-objects-v2 --bucket "$S3_BUCKET" --query "Contents[].Key" --output text | tr '\t' '\n' | grep -v '^None$' || true
}
# S3 の一時バケットを片付ける。済んだ（無い・消した・回収が済んでいないので残した）なら 0、
# 有無を確かめられない・消した後に 404 を確かめられないなら 1（記録 s3_bucket.env は残し、もう一度実行できるようにする）。
# 呼び出し側が `|| bucket_rc=$?` で受けるので、この中では set -e が効かない。失敗は各行で受けて 1 を返す。
teardown_bucket() {
  local st
  [[ -f "$S3_STATE_FILE" ]] || return 0
  load_s3_state
  st=$(s3_bucket_state "$S3_BUCKET")
  case "$st" in
    absent)
      log "S3 の一時バケット ${S3_BUCKET} は無い（head-bucket が 404。消し済み）"
      mv "$S3_STATE_FILE" "${S3_STATE_FILE}.done-$(date +%Y%m%dT%H%M%S)" || return 1
      return 0 ;;
    exists) ;;
    *)
      # 以前は head-bucket の失敗をすべて「無い」と扱っていたが、403 などは無いとは限らない。
      log "注意: S3 の一時バケット ${S3_BUCKET} の有無を確かめられない（${st}）。記録は残した。原因を確かめてからもう一度 90_teardown.sh --execute"
      return 1 ;;
  esac
  if ! s3_collected; then
    log "注意: S3 の一時バケット ${S3_BUCKET} は回収が済んでいないので消さない。45_collect_s3.sh --all --execute の後に、もう一度 90_teardown.sh --execute"
    return 0
  fi
  aws_p s3 rm "s3://${S3_BUCKET}" --recursive --only-show-errors \
    || { log "S3 の一時バケット ${S3_BUCKET} の中身を消せなかった。記録は残した"; return 1; }
  aws_p s3api delete-bucket --bucket "$S3_BUCKET" \
    || { log "S3 の一時バケット ${S3_BUCKET} を消せなかった。記録は残した"; return 1; }
  # 以前の `s3api wait bucket-not-exists`（5 秒 × 20 回）は「Max attempts exceeded」で止まり、直後の head-bucket は
  # 404 だった（付録 K-17.6）。どの応答で待ちが終わらなかったかは記録が無い。ここでは自分で head-bucket を
  # 繰り返し、404 で成功とし、毎回の応答を記録する（wait_bucket_absent。lib.sh）。
  if ! wait_bucket_absent "$S3_BUCKET"; then
    log "停止: S3 の一時バケット ${S3_BUCKET} を消したが、${S3_DELETE_WAIT_ATTEMPTS} 回確かめても 404 にならない。記録（${S3_STATE_FILE}）は残した。少し待ってもう一度 90_teardown.sh --execute（404 なら記録を退避して終わる）"
    return 1
  fi
  log "消した: S3 の一時バケット ${S3_BUCKET}（中身ごと）"
  mv "$S3_STATE_FILE" "${S3_STATE_FILE}.done-$(date +%Y%m%dT%H%M%S)" || return 1
}

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
  S3 の一時バケット: $( [[ -f "$S3_STATE_FILE" ]] && grep '^S3_BUCKET=' "$S3_STATE_FILE" | cut -d= -f2 || echo なし)（回収が済んでいれば中身ごと消す）
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
# バケットの片付けが失敗しても、残りの確認は必ず表示してから、失敗の終了コードで終わる。
bucket_rc=0
teardown_bucket || bucket_rc=$?
verify
if (( bucket_rc != 0 )); then
  log "停止: S3 の一時バケットの片付けが済んでいない（上の表示を確かめる）。インスタンスなどの片付けは済んだ"
  exit "$bucket_rc"
fi
