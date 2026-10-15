#!/usr/bin/env bash
# 段階 B: Windows の C:\probe\out\<段> を zip にし、SSM の出力で分けて受け取り、
# data/raw/oanda_mt5_probe/<試験の日付>/<段>/ に展開して SHA-256 の一覧を作る。
# 受け取るのは道具の出力（要約の JSON と、2023 年以前の短い区間の CSV）だけ。端末のデータのフォルダは読まない。
#
# 使い方: tools/survey/aws_mt5_probe/40_collect.sh <usdjpy-time|usdjpy-range|others> [--execute]
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
phase=""
for a in "$@"; do
  case "$a" in
    usdjpy-time|usdjpy-range|others|offset-eu-dst|range-8|m1-[A-Z][A-Z][A-Z][A-Z][A-Z][A-Z]|ts-*|sync-trigger) phase="$a" ;;
    --execute|--dry-run) ;;
    *) die "知らない引数: $a" ;;
  esac
done
[[ -n "$phase" ]] || die "段の名前を指定する（usdjpy-time・usdjpy-range・others）"
require_profile
load_state
check_deadline
assert_tagged_instance "$INSTANCE_ID"

dest="${RESULT_ROOT}/${PROBE_DATE}/${phase}"
cat >&2 <<INFO
== 回収の計画（${INSTANCE_ID}）
  Windows: C:\\probe\\out\\${phase} → zip
  Mac: ${dest}（.gitignore の data/raw/* で版管理の外。コミットしない）
INFO
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }
[[ -e "$dest" ]] && die "置き場が既にある: ${dest}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
zip_win="C:\\probe\\out_${phase}.zip"
cat >"$work/zip.ps1" <<PS
\$ErrorActionPreference = 'Stop'
if (Test-Path '${zip_win}') { Remove-Item '${zip_win}' }
Compress-Archive -Path 'C:\\probe\\out\\${phase}\\*' -DestinationPath '${zip_win}'
\$f = Get-Item '${zip_win}'
'{0} {1}' -f \$f.Length, (Get-FileHash -Algorithm SHA256 '${zip_win}').Hash.ToLower()
PS
read -r size zip_sha < <(ssm_ps "$INSTANCE_ID" 300 "$work/zip.ps1" | tr -d "\r" | grep -v "^$" | tail -n 1)
log "zip: ${size} バイト、SHA-256 ${zip_sha}"

# SSM の出力は約 24,000 文字で切れるので、13,500 バイト（base64 で 18,000 文字）ずつ受け取る。
chunk=13500
offset=0
: >"$work/all.b64"
while (( offset < size )); do
  cat >"$work/part.ps1" <<PS
\$ErrorActionPreference = 'Stop'
\$fs = [IO.File]::OpenRead('${zip_win}')
\$fs.Seek(${offset}, 'Begin') | Out-Null
\$buf = New-Object byte[] ${chunk}
\$n = \$fs.Read(\$buf, 0, ${chunk})
\$fs.Close()
[Convert]::ToBase64String(\$buf, 0, \$n)
PS
  ssm_ps "$INSTANCE_ID" 120 "$work/part.ps1" | tr -d '\r\n' | base64 -d >>"$work/out.zip"
  offset=$(( offset + chunk ))
  log "受け取り: $(( offset < size ? offset : size )) / ${size}"
done
got=$(sha256_of "$work/out.zip")
[[ "$got" == "$zip_sha" ]] || die "zip の SHA-256 が一致しない: ${got}"

mkdir -p "$dest"
cp "$work/out.zip" "${dest}/../out_${phase}.zip"
unzip -q "$work/out.zip" -d "$dest"
( cd "$dest" && find . -type f ! -name sha256sums.txt -print0 | sort -z | xargs -0 shasum -a 256 >"${work}/sums.txt" )
mv "${work}/sums.txt" "${dest}/sha256sums.txt"
log "回収した: ${dest}"
echo "zip ${zip_sha}  out_${phase}.zip"
cat "${dest}/sha256sums.txt"
