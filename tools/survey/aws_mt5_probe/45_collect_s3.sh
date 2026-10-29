#!/usr/bin/env bash
# 全期間取得（付録 K-16）の回収: S3 の一時バケットから通貨ごとに受け取り、manifest の SHA-256・行数と
# 照合してから data/raw/oanda_mt5/<取得日>/<通貨>/ に置く（人間の第 18 回の決定。gzip の CSV）。
# 照合が合わなければ何も置かない。置き場の既存のファイルは上書きしない（同じ中身なら飛ばし、違えば止まる）。
# manifest.json と fetch_log.jsonl は <取得日>/ の直下に置く（この取得の記録。回収のたびに新しい版にする）。
# インスタンスは使わない（S3 だけ）ので、終了の時刻の後でも、インスタンスの終了の後でも動く。
# 封印の取得（39_sealed_fetch.sh。バケットの記録に S3_PREFIX=sealed）のバケットなら、置き場は
# data/raw/oanda_mt5_sealed/<取得日>/<通貨>/、照合は記録の計画（SEALED_PLAN）との sealedverify（ADR-0037。D03 §16.5.4）。
#
# 使い方: tools/survey/aws_mt5_probe/45_collect_s3.sh <通貨,...|--all> [--execute]
source "$(dirname "$0")/lib.sh"
parse_execute_flag "$@"
pairs=""
for a in "$@"; do
  case "$a" in
    --all) pairs="--all" ;;
    --execute|--dry-run) ;;
    [A-Z][A-Z][A-Z][A-Z][A-Z][A-Z]*) pairs="$a" ;;
    *) die "知らない引数: $a" ;;
  esac
done
[[ -n "$pairs" ]] || die "通貨（カンマ区切り）か --all を指定する"
require_profile
load_s3_state || die "S3 の一時バケットの記録が無い（${S3_STATE_FILE}。38_full_fetch.sh の前）"
if [[ "$S3_KEY_PREFIX" == "$SEALED_S3_KEY_PREFIX" ]]; then
  [[ -n "${SEALED_PLAN:-}" && -f "$SEALED_PLAN" ]] || die "封印の取得の計画が無い（記録の SEALED_PLAN=${SEALED_PLAN:-なし}）"
  data_root="$SEALED_ROOT"
  verify_args=(sealedverify --plan "$SEALED_PLAN")
  sample_rel="USDJPY/USDJPY_2024-01_m1.csv.gz"
else
  data_root="$FULL_ROOT"
  verify_args=(fullverify)
  sample_rel="USDJPY/USDJPY_2017-01_m1.csv.gz"
fi
dest_root="${data_root}/${FETCH_DATE}"
cat >&2 <<INFO
== S3 からの回収の計画
  バケット: s3://${S3_BUCKET}/${S3_KEY_PREFIX}/
  通貨: ${pairs}
  Mac: ${dest_root}/<通貨>/（manifest の SHA-256 と照合してから置く）
INFO
[[ "$EXECUTE" == 1 ]] || { echo "(dry-run) 何もしていない。" >&2; exit 0; }

# 置き場が版管理の外であることを確かめる（リポジトリの中に置く場合）。
if repo=$(git -C "$(dirname "$data_root")" rev-parse --show-toplevel 2>/dev/null); then
  git -C "$repo" check-ignore -q "${dest_root}/${sample_rel}" \
    || die "置き場 ${dest_root} が .gitignore の外。置かない"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
stage="$work/stage"
mkdir -p "$stage"
aws_p s3 cp "s3://${S3_BUCKET}/${S3_KEY_PREFIX}/manifest.json" "$stage/manifest.json" --only-show-errors
aws_p s3 cp "s3://${S3_BUCKET}/${S3_KEY_PREFIX}/fetch_log.jsonl" "$stage/fetch_log.jsonl" --only-show-errors \
  || log "fetch_log.jsonl が無い（要求を出していない）"
if [[ "$pairs" == "--all" ]]; then
  pairs=$("$PY_MAC" -I -c 'import json,sys; print(",".join(sorted({e["pair"] for e in json.load(open(sys.argv[1]))["files"].values()})))' "$stage/manifest.json")
  [[ -n "$pairs" ]] || die "manifest に月が無い"
fi
for p in ${pairs//,/ }; do
  aws_p s3 cp "s3://${S3_BUCKET}/${S3_KEY_PREFIX}/${p}/" "$stage/${p}/" --recursive --only-show-errors
done

size=$("$PY_MAC" -I -c 'import os,sys; print(sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(sys.argv[1]) for f in fs))' "$stage")
if [[ "$S3_KEY_PREFIX" == "$SEALED_S3_KEY_PREFIX" ]]; then
  # 封印の原ファイルの大きさは表示も記録もしない（D03 §16.4「見てはいけない値」。中身で決まる値）。上限は
  # この回の回収だけで確かめる（通貨ごとの回収の合計は記録しない。PR #111 の仮置き S4a-13「このまま」）。
  collected=0
  (( size <= PROBE_COLLECT_LIMIT_BYTES )) \
    || die "この回の回収が上限 ${PROBE_COLLECT_LIMIT_BYTES} バイトを超える。置かない（大きさは表示しない）"
else
  collected="${COLLECTED_BYTES:-0}"
  (( collected + size <= PROBE_COLLECT_LIMIT_BYTES )) \
    || die "回収の合計が上限 ${PROBE_COLLECT_LIMIT_BYTES} バイトを超える（済み ${collected}、今回 ${size}）。置かない"
fi

(cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -m tools.survey.mt5_api_probe "${verify_args[@]}" --root "$stage" --pairs "$pairs") \
  || die "S3 から受け取ったファイルが manifest と合わない。置かない（S3 には残っている）"

# 置く前に、既存のファイルとの食い違いを全部確かめる（途中まで置いて止まらないように）。
new_files=()
while IFS= read -r rel; do
  dst="${dest_root}/${rel}"
  if [[ -e "$dst" ]]; then
    [[ "$(sha256_of "$dst")" == "$(sha256_of "$stage/$rel")" ]] \
      || die "置き場に違う中身のファイルがある: ${dst}（上書きしない）"
  else
    new_files+=("$rel")
  fi
done < <(cd "$stage" && find . -mindepth 2 -type f -name '*.csv.gz' | sed 's|^\./||' | sort)
for rel in ${new_files[@]+"${new_files[@]}"}; do
  mkdir -p "$(dirname "${dest_root}/${rel}")"
  cp -p "$stage/$rel" "${dest_root}/${rel}"
done
mkdir -p "$dest_root"
cp "$stage/manifest.json" "${dest_root}/manifest.json"
[[ -f "$stage/fetch_log.jsonl" ]] && cp "$stage/fetch_log.jsonl" "${dest_root}/fetch_log.jsonl"
(cd "$PROBE_SCRIPT_DIR/../../.." && "$REPO_PY" -m tools.survey.mt5_api_probe "${verify_args[@]}" --root "$dest_root" --pairs "$pairs") \
  || die "置いた後の照合が合わない: ${dest_root}"

save_s3_state COLLECTED_MANIFEST_SHA256 "$(sha256_of "${dest_root}/manifest.json")"
if [[ "$S3_KEY_PREFIX" == "$SEALED_S3_KEY_PREFIX" ]]; then
  log "回収した: ${#new_files[@]} ファイルを新しく置いた（${dest_root}）"
else
  save_s3_state COLLECTED_BYTES $(( collected + size ))
  log "回収した: ${#new_files[@]} ファイルを新しく置いた（${dest_root}。回収の合計 $(( collected + size )) バイト）"
fi
# 件数だけを表示する（価格は表示しない）。
"$PY_MAC" -I - "${dest_root}/manifest.json" "$pairs" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
for pair in sys.argv[2].split(","):
    es = [e for e in m["files"].values() if e["pair"] == pair]
    ok = [e for e in es if e["status"] == "ok"]
    empty = [e["rid"] for e in es if e["status"] == "empty"]
    unit = "84 か月"
    if m.get("kind") == "oanda_mt5_sealed_fetch":
        unit = f"{len(es)} 要求（取得済みの記録の数）"
    print(f"  {pair}: 取得済み {len(ok)} / {unit}、行 {sum(e['rows'] for e in ok)}、0 本 {len(empty)} {empty if empty else ''}")
print(f"  要求の累計 {m['requests_sent']} / {m['max_requests']}")
PY
