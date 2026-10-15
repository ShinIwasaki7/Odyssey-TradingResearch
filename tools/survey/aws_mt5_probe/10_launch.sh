#!/usr/bin/env bash
# 段階 B（課金が始まる）: セキュリティグループ・キーペアを作り、t3.medium の Windows を 1 台起動する。
# 司令塔の「起動してよい」の後にだけ --execute を付けて実行する。--execute が無ければ表示だけ。
#
# 使い方: tools/survey/aws_mt5_probe/10_launch.sh            # 何をするかを表示するだけ
#         tools/survey/aws_mt5_probe/10_launch.sh --execute  # 起動する（課金が始まる）
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
require_profile

[[ -f "$STATE_FILE" ]] && grep -q '^INSTANCE_ID=' "$STATE_FILE" \
  && die "前の試験の状態が残っている（${STATE_FILE}）。90_teardown.sh で片付けてから"

probe_date=$(date +%Y-%m-%d)
key_name="${PROBE_NAME}-${probe_date}"
key_file="${KEY_DIR}/${key_name}.pem"
ami_id=$(aws_p ec2 describe-images --owners amazon \
  --filters "Name=name,Values=${PROBE_AMI_NAME_PATTERN}" "Name=state,Values=available" "Name=architecture,Values=x86_64" \
  --query "sort_by(Images,&CreationDate)[-1].ImageId" --output text)
vpc=$(aws_p ec2 describe-vpcs --filters Name=is-default,Values=true --query "Vpcs[0].VpcId" --output text)
# t3.medium が使えるゾーンの既定のサブネットを 1 つ選ぶ。
azs=$(aws_p ec2 describe-instance-type-offerings --location-type availability-zone \
  --filters "Name=instance-type,Values=${PROBE_INSTANCE_TYPE}" --query "InstanceTypeOfferings[].Location" --output text)
subnet=""
for az in $azs; do
  subnet=$(aws_p ec2 describe-subnets --filters "Name=vpc-id,Values=${vpc}" "Name=default-for-az,Values=true" \
    "Name=availability-zone,Values=${az}" --query "Subnets[0].SubnetId" --output text)
  [[ "$subnet" == subnet-* ]] && break
done
[[ "$subnet" == subnet-* ]] || die "使えるサブネットが無い"

userdata=$(mktemp)
trap 'rm -f "$userdata"' EXIT
# 起動の時に Windows の中で自動の停止（4 時間 30 分後）を入れる。シャットダウンの動作は「停止」。
cat >"$userdata" <<PS
<powershell>
shutdown /s /t ${PROBE_SHUTDOWN_SECONDS} /c "odyssey-mt5-probe auto stop (4h30m)"
</powershell>
PS

cat >&2 <<INFO
== 起動の計画（課金が始まる）
  AMI: ${ami_id}（${PROBE_AMI_NAME_PATTERN} の最新）
  型: ${PROBE_INSTANCE_TYPE}、CPU クレジット: 標準、IMDSv2 必須
  ディスク: gp3 ${PROBE_ROOT_GB} GB、終了時に削除
  ネットワーク: ${vpc} / ${subnet}、自動のパブリック IPv4 を 1 つ（Elastic IP は使わない）
  受信: RDP（TCP 3389）を ${PROBE_RDP_CIDR} からだけ
  インスタンスプロファイル: ${PROBE_SSM_ROLE}
  キーペア: ${key_name}（秘密鍵は ${key_file}、権限 600。リポジトリの外）
  シャットダウンの動作: 停止。Windows の中で ${PROBE_SHUTDOWN_SECONDS} 秒後に自動の停止
  タグ: ${PROBE_TAG_KEY}=${PROBE_TAG_VALUE}
  作業役が必ず終了する時刻: 起動から 4 時間（Windows の中の自動の停止は 4 時間 30 分で、止め忘れの備え）
INFO

if [[ "$EXECUTE" != 1 ]]; then
  echo "(dry-run) 何も作っていない。司令塔の合図の後に --execute を付けて実行する。" >&2
  exit 0
fi

sg_id=$(aws_p ec2 create-security-group --group-name "${PROBE_NAME}-rdp-${probe_date}" \
  --description "odyssey-mt5-probe RDP from operator IP only" --vpc-id "$vpc" \
  --tag-specifications "$(tag_spec security-group "-rdp")" --query GroupId --output text)
save_state SG_ID "$sg_id"
log "セキュリティグループ: ${sg_id}"
aws_p ec2 authorize-security-group-ingress --group-id "$sg_id" \
  --ip-permissions "IpProtocol=tcp,FromPort=3389,ToPort=3389,IpRanges=[{CidrIp=${PROBE_RDP_CIDR},Description=human-rdp}]" >/dev/null

mkdir -p "$KEY_DIR"
umask 077
aws_p ec2 create-key-pair --key-name "$key_name" --key-type rsa --key-format pem \
  --tag-specifications "$(tag_spec key-pair)" --query KeyMaterial --output text >"$key_file"
chmod 600 "$key_file"
save_state KEY_NAME "$key_name"
save_state KEY_FILE "$key_file"
log "キーペア: ${key_name}（秘密鍵は ${key_file}。中身は表示しない）"

instance_id=$(aws_p ec2 run-instances --image-id "$ami_id" --instance-type "$PROBE_INSTANCE_TYPE" --count 1 \
  --key-name "$key_name" \
  --network-interfaces "DeviceIndex=0,SubnetId=${subnet},Groups=${sg_id},AssociatePublicIpAddress=true,DeleteOnTermination=true" \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${PROBE_ROOT_GB},VolumeType=gp3,DeleteOnTermination=true}" \
  --credit-specification CpuCredits=standard \
  --instance-initiated-shutdown-behavior stop \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --iam-instance-profile "Name=${PROBE_SSM_ROLE}" \
  --user-data "file://${userdata}" \
  --tag-specifications "$(tag_spec instance)" "$(tag_spec volume "-root")" "$(tag_spec network-interface)" \
  --query "Instances[0].InstanceId" --output text)
launched=$(date +%s)
save_state INSTANCE_ID "$instance_id"
save_state LAUNCHED_AT_EPOCH "$launched"
save_state LAUNCHED_AT_UTC "$(date -u -r "$launched" +%Y-%m-%dT%H:%M:%SZ)"
save_state DEADLINE_UTC "$(date -u -r $(( launched + PROBE_DEADLINE_SECONDS )) +%Y-%m-%dT%H:%M:%SZ)"
save_state PROBE_DATE "$probe_date"
save_state AMI_ID "$ami_id"
log "起動: ${instance_id}（起動 $(date -u -r "$launched" +%H:%M:%SZ)、必ず終了する時刻 $(date -u -r $(( launched + PROBE_DEADLINE_SECONDS )) +%H:%M:%SZ)）"

aws_p ec2 wait instance-running --instance-ids "$instance_id"
aws_p ec2 describe-instances --instance-ids "$instance_id" \
  --query "Reservations[0].Instances[0].[InstanceId,State.Name,InstanceType,PublicIpAddress,Placement.AvailabilityZone]" --output text
