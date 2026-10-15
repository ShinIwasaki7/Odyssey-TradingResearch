#!/usr/bin/env bash
# 段階 A（課金なし）: プロファイルの確認、AMI の特定、事前の作業の確認、権限の試し（--dry-run）、費用の再見積。
# 何も作らない。読み取りと、EC2 の --dry-run（権限の確認だけで、起動しない）だけを行う。
#
# 使い方: tools/survey/aws_mt5_probe/00_check.sh
source "$(dirname "$0")/lib.sh"

echo "== 1. プロファイル（${PROBE_PROFILE}・${PROBE_REGION}）"
require_profile
arn=$(aws_p sts get-caller-identity --query Arn --output text)
account=$(aws_p sts get-caller-identity --query Account --output text)
# アカウント番号は記録に残さない（表示は末尾 4 桁だけ）。
echo "  呼び出し元: ${arn/${account}/********${account: -4}}"
[[ "$arn" == *":user/odyssey-mt5-probe-operator" ]] \
  || echo "  注意: IAM ユーザー名が付録 K-1 の odyssey-mt5-probe-operator と違う"

echo "== 2. AMI（Amazon が出している最新の ${PROBE_AMI_NAME_PATTERN}）"
read -r ami_id ami_name ami_date < <(aws_p ec2 describe-images --owners amazon \
  --filters "Name=name,Values=${PROBE_AMI_NAME_PATTERN}" "Name=state,Values=available" \
            "Name=architecture,Values=x86_64" \
  --query "sort_by(Images,&CreationDate)[-1].[ImageId,Name,CreationDate]" --output text)
[[ "$ami_id" == ami-* ]] || die "AMI が見つからない"
root_gb=$(aws_p ec2 describe-images --image-ids "$ami_id" \
  --query "Images[0].BlockDeviceMappings[?DeviceName=='/dev/sda1'].Ebs.VolumeSize | [0]" --output text)
echo "  ${ami_id}  ${ami_name}  作成 ${ami_date}  既定のディスク ${root_gb} GB"

echo "== 3. SSM 用のロールとインスタンスプロファイル（人間が作る: ${PROBE_SSM_ROLE}）"
if aws_p iam get-instance-profile --instance-profile-name "$PROBE_SSM_ROLE" \
     --query "InstanceProfile.Roles[0].RoleName" --output text 2>/dev/null; then
  echo "  あり"
else
  echo "  無い、または読む権限が無い（付録 K-1 の手順を確かめる）"
fi

echo "== 4. 既定の VPC と、パブリック IP が自動で付くサブネット"
vpc=$(aws_p ec2 describe-vpcs --filters Name=is-default,Values=true --query "Vpcs[0].VpcId" --output text)
echo "  VPC: ${vpc}"
aws_p ec2 describe-subnets --filters "Name=vpc-id,Values=${vpc}" "Name=default-for-az,Values=true" \
  --query "Subnets[].[AvailabilityZone,SubnetId,MapPublicIpOnLaunch]" --output text | sed 's/^/  /'
aws_p ec2 describe-instance-type-offerings --location-type availability-zone \
  --filters "Name=instance-type,Values=${PROBE_INSTANCE_TYPE}" \
  --query "InstanceTypeOfferings[].Location" --output text | sed "s/^/  ${PROBE_INSTANCE_TYPE} が使えるゾーン: /"

echo "== 5. 試験のタグ（${PROBE_TAG_KEY}=${PROBE_TAG_VALUE}）が付いた既存のもの（0 件のはず）"
printf '  インスタンス（終了済みを除く）: '
aws_p ec2 describe-instances --filters "$(tag_filter)" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
  --query "length(Reservations[].Instances[])" --output text
printf '  ボリューム: ';   aws_p ec2 describe-volumes --filters "$(tag_filter)" --query "length(Volumes)" --output text
printf '  セキュリティグループ: '; aws_p ec2 describe-security-groups --filters "$(tag_filter)" --query "length(SecurityGroups)" --output text
printf '  キーペア: '; aws_p ec2 describe-key-pairs --filters "$(tag_filter)" --query "length(KeyPairs)" --output text
printf '  自分のスナップショット（全部）: '; aws_p ec2 describe-snapshots --owner-ids self --query "length(Snapshots)" --output text
printf '  自分の AMI（全部）: '; aws_p ec2 describe-images --owners self --query "length(Images)" --output text
printf '  Elastic IP（全部）: '; aws_p ec2 describe-addresses --query "length(Addresses)" --output text

echo "== 6. 料金の通知（Budgets。通知は支出を止めない）"
if budgets=$(aws_p budgets describe-budgets --account-id "$account" \
     --query "Budgets[].[BudgetName,BudgetLimit.Amount,BudgetLimit.Unit,TimeUnit]" --output text 2>/dev/null); then
  if [[ -z "$budgets" || "$budgets" == "None" ]]; then
    echo "  0 件（警告: 料金の通知が無い。人間の確認か明示の許可が要る）"
  else
    while IFS= read -r line; do echo "  $line"; done <<<"$budgets"
  fi
else
  echo "  読めない（警告: 権限が無い）"
fi

echo "== 7. 起動の権限の試し（EC2 の --dry-run。起動しない・課金なし）"
dry=$(aws_p ec2 run-instances --dry-run --image-id "$ami_id" --instance-type "$PROBE_INSTANCE_TYPE" \
  --count 1 --credit-specification CpuCredits=standard \
  --iam-instance-profile "Name=${PROBE_SSM_ROLE}" \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --tag-specifications "$(tag_spec instance)" "$(tag_spec volume)" 2>&1 || true)
if grep -q DryRunOperation <<<"$dry"; then echo "  起動の権限: あり（DryRunOperation）"; else echo "  起動の権限の試しの結果: ${dry}"; fi
dry=$(aws_p ec2 create-security-group --dry-run --group-name "${PROBE_NAME}-check" \
  --description "dry-run only" --vpc-id "$vpc" --tag-specifications "$(tag_spec security-group)" 2>&1 || true)
if grep -q DryRunOperation <<<"$dry"; then echo "  セキュリティグループの作成の権限: あり"; else echo "  結果: ${dry}"; fi

echo "== 8. Windows に入れるものの入手元（HEAD だけ）"
printf '  Python のインストーラー: '; curl -sIL -m 20 -o /dev/null -w "%{http_code}\n" "$PY_INSTALLER_URL"
echo "  固定した SHA-256: ${PY_INSTALLER_SHA256}"
echo "  MetaTrader5 のパッケージ: ${MT5_PY_PACKAGE}"
echo "  MT5 の端末のインストーラー: OANDA証券の公式の入手先はログイン後のマイページ（とサブアカウントの作成のメール）だけ。"
echo "    公開の URL は無い（付録 K-2）。既定では人間が RDP の中でマイページから入れる。"

echo "== 9. 費用の再見積（米ドル、税込み。単価は D12 §11.5.2 と同じ）"
"$PY_MAC" -I - "$PRICE_INSTANCE_HOUR" "$PRICE_GP3_GB_MONTH" "$PRICE_IPV4_HOUR" "$PRICE_EGRESS_GB" \
  "$PRICE_CREDIT_VCPU_HOUR" "$TAX_RATE" "$root_gb" <<'PY'
import sys
inst, gp3, ip4, egress, credit, tax, root = map(float, sys.argv[1:])
def total(hours, gb, egress_gb, credit_hours):
    sub = inst * hours + gb * gp3 * hours / 720 + ip4 * hours + egress * egress_gb + 2 * credit * credit_hours
    return sub * (1 + tax)
print(f"  想定（4 時間＝作業上限、ディスク 60 GB、送信 0.1 GB）: 約 {total(4, 60, 0.1, 0):.2f}")
print(f"  上限（6 時間＝自動の停止まで止め忘れ、60 GB、送信 5 GB、クレジットの標準を外した場合）: 約 {total(6, 60, 5, 6):.2f}")
print(f"  止め忘れの 1 時間あたり（標準のクレジット、60 GB）: 約 {(inst + 60 * gp3 / 720 + ip4) * (1 + tax):.3f}")
print("  使用済み（前回の試験。付録 K-4）: 約 0.24")
print("  人間の目安: 総額 5 米ドル以内（D12-20）")
PY
echo "  AMI（記録用）: ${ami_id} ${ami_name}"
