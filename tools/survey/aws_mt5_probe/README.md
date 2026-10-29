# AWS 上の Windows で OANDA MT5 Python API の小規模な試験を行うスクリプト

D12（`docs/design/D12_data_source_strategy_review.md`）§11.5 の試験を、人間の代わりに作業役が AWS CLI と
SSM（AWS Systems Manager。インスタンスの中でコマンドを実行する AWS の機能）で行うためのもの。
人間がやる作業と作業の記録は `docs/design/D12_appendix_provider_survey.md` の付録 K。

## 順番

| スクリプト | 課金 | すること |
|---|---|---|
| `00_check.sh` | なし | プロファイルの確認、AMI の特定、人間の事前作業（SSM 用のロール・Budgets）の確認、起動の権限の試し（EC2 の `--dry-run`）、費用の再見積 |
| `10_launch.sh --execute` | **始まる** | セキュリティグループ（RDP を人間の IP だけ）・キーペアを作り、t3.medium を 1 台起動。司令塔の「起動してよい」の後だけ |
| `95_deadline_guard.sh` | — | 起動から 4 時間（全期間取得。前回の最後の調査は 2 時間）で `90_teardown.sh --execute` を実行する見張り（Mac で裏で動かす） |
| `20_prepare.sh --execute` | 続く | 実行のたびに Windows の中の自動の停止を起動 + 4 時間 30 分に予約し直す（再起動で消えた予約を戻す）。Python 3.12.10・MetaTrader5 5.0.6231・AWS CLI v2（2.38.1。既に入っていれば入れない）を入れ、試験の道具を `C:\probe\` に置く。RDP の接続先とパスワードの出し方を表示 |
| （人間） | 続く | RDP で接続し、OANDA のマイページから MT5 の端末を入れてログインし、チャートを閉じ、RDP を「切断」する（付録 K-2） |
| `30_run_probe.sh [段] --execute` | 続く | 試験の道具を実行。段は `usdjpy-time`（既定）・`usdjpy-range`・`others` |
| `40_collect.sh <段> --execute` | 続く | 結果を回収し `data/raw/oanda_mt5_probe/<試験の日付>/<段>/` に置き、SHA-256 の一覧を作る |
| `35_time_survey.sh <段> --execute` | 続く | 時刻の調査（D12 §11.7）の段と空白の測定（§11.9、段 G）を実行し、回収し、Mac で評価して決定木の次の段を示す（付録 K-8）。**最後の調査の起動では動かない** |
| `37_final_survey.sh [--only <要求,...> \| --remaining \| --redo-empty] --execute` | 続く | 最後の調査（付録 K-10）の 9 区間を実行し、回収し、Mac で「確認済み」「仮定付き」を判定する（下の「最後の調査の手順」） |
| `38_full_fetch.sh [--pairs <通貨,...> \| --redo-empty] --execute` | 続く | 全期間取得（付録 K-16）。S3 の一時バケットを作り、通貨ごとに取得 → S3 に置く → `45` で回収（下の「全期間取得の実行手順」） |
| `39_sealed_fetch.sh --plan <計画> [--pairs <通貨,...> \| --redo-empty] --execute` | 続く | 2024 年以降の封印の取得（制約の改定文 ADR-0037。起動の種類 `sealed-bulk`・`sealed-periodic`）。計画は Mac の `tools.survey.sealed_fetch_plan` が窓の設定から作る（下の「封印の取得の運用手順」） |
| `45_collect_s3.sh <通貨,...\|--all> --execute` | S3 だけ | S3 の一時バケットから受け取り、manifest の SHA-256・行数と照合してから `data/raw/oanda_mt5/<取得日>/<通貨>/` に置く。インスタンスの終了の後でも動く |
| `90_teardown.sh --execute` | 止まる | 終了と削除、残りの確認（`--verify` は確認だけ）。S3 の一時バケットは回収が済んでいれば中身ごと消し、head-bucket が 404 になるまで確かめる（下の「全期間取得の後の道具の修正」） |

`--execute` を付けなければ、どのスクリプトも計画を表示するだけで何も変えない。

## 守ること（スクリプトに組み込んだもの）

- AWS CLI は `lib.sh` の `aws_p` でだけ呼び、プロファイル `odyssey-mt5-probe`・リージョン `ap-northeast-1` に固定する。環境変数の認証情報は消してから呼ぶ。認証情報のファイルは読まない。
- 作るものにはタグ `Project=odyssey-mt5-probe` と、`odyssey-mt5-probe` を含む名前を付ける。消すのはこのタグのものだけ。
- 構成: t3.medium、CPU クレジットは標準、gp3 60 GB（終了時に削除。上限。拡張しない）、Elastic IP なし、IMDSv2 必須、受信は RDP を人間の IP（`PROBE_RDP_CIDR`、既定 126.221.39.9/32）からだけ、シャットダウンの動作は停止、Windows の中で 4 時間 30 分後に自動の停止（`PROBE_SHUTDOWN_SECONDS`）。
- 作業上限（全期間取得。付録 K-16.6。人間の第 18 回の決定「案のとおり」）: 起動から 2 時間 30 分で要求を打ち切り（`FULL_TIME_LIMIT_SECONDS`。`38_full_fetch.sh` と道具の `--stop-at-epoch` が止まる）、4 時間で終了（`PROBE_DEADLINE_SECONDS`。`check_deadline` と `95_deadline_guard.sh`）、要求 750（`FULL_MAX_REQUESTS`。道具が manifest で前の回から通して数える）。終了の時刻を過ぎたら `90_teardown.sh` と `45_collect_s3.sh` 以外は動かない。前回（最後の調査。付録 K-10.3）の値は 1 時間 30 分・2 時間・2 時間 30 分だった。
- 起動の種類は `PROBE_RUN_KIND=fullfetch`（`lib.sh`）。この起動では一覧の外の要求を出す段（`30_run_probe.sh`・`35_time_survey.sh`・`36_sync_trigger.sh`）と最後の調査（`37_final_survey.sh`）は動かない（`forbid_in_final_run`・状態の `RUN_KIND`）。
- 回収（SSM の出力で受け取る zip と、S3 から受け取るファイル）の合計は 1 GB まで（`PROBE_COLLECT_LIMIT_BYTES`。送信の上限のうち道具で数えられる分。RDP の通信は数えられない）。
- S3 は全期間取得の回収だけに使う。バケットは名前が `odyssey-mt5-probe-<取得日>-<乱数 8 桁>` の 1 つだけで、スクリプトはこの形の名前以外を触らない（`assert_probe_bucket`）。IAM の権限も `odyssey-mt5-probe-` で始まるバケットだけ。
- 要求の区間は試験の道具の段の名前でだけ指定する。道具は 2024-01-01T00:00Z 以降を含む要求を出さない。`30_run_probe.sh` は実行の前に、注文・売買・口座の関数の呼び出しが道具に無いことを文字列で確かめる。
- 回収するのは道具の出力（`C:\probe\out\<段>`）だけ。MT5 の端末のデータのフォルダは読まない。

## 設計の選択

- **端末とのつなぎ方**: SSM の既定の実行者（SYSTEM）は、人間が RDP でログインした Administrator のセッションの MT5 の端末とつながらない見込みなので、`30_run_probe.sh` は Administrator の対話のセッションで動くタスク（Windows のタスク スケジューラ）を登録して実行する。人間は RDP を「切断」にし、サインアウトしない。
- **結果の回収**: 試験の段（`40_collect.sh`）は、S3 などの別のリソースを作らないため、zip を SSM の出力で 13,500 バイトずつ base64 で受け取り、SHA-256 の一致を確かめる（SSM の出力は 1 回あたり約 24,000 文字で切れる）。この方法は約 1,900 バイト/秒で全期間（約 0.3 GB）には約 43 時間かかるので、全期間取得は S3 の一時バケットで回収する（人間の第 18 回の決定 (A)）。
- **MT5 の端末のインストーラー**: OANDA証券の公式の入手先は、ログイン後のマイページと、サブアカウントの作成のメールのリンクだけ（`https://www.oanda.jp/platform/mt5` の FAQ「MT5を再度ダウンロードしたい」「メール内のMT5ダウンロードリンクよりMT5をダウンロードしてください」。2026-10-15 に作業役が確認）。公開の URL が無いので、既定では人間が RDP の中で入れる。人間がリンクを渡した場合だけ `MT5_INSTALLER_URL=<URL> 20_prepare.sh --execute` で無人で入れる（`/auto`）。

## 固定した入手物（2026-10-15 に作業役が確認）

| もの | 入手元 | SHA-256 |
|---|---|---|
| Python 3.12.10（Windows 64 ビット。3.12 系の最後の Windows のインストーラー。3.12.11 は 404） | `https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe` | `67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb`（作業役が取得して計算。Windows でも一致を確かめる） |
| MetaTrader5 5.0.6231（cp312 の wheel） | PyPI | `b94b1f8f52087ae36dfac5f352a96f5d04fe308704d9515f82cf5d1470bc31d4`（PyPI の表示） |
| AWS CLI v2 2.38.1（Windows の MSI。2026-10-17 に作業役が確認） | `https://awscli.amazonaws.com/AWSCLIV2-2.38.1.msi`（版を固定した公式の配布元。版なしの `AWSCLIV2.msi` は中身が変わる） | `ad07f9acd855f48ad016e63b2f0b4f87783c1b6524b86e966f4a6d21d01f524a`（付録 K-17.4 の 1 で Windows に入れた MSI の値と、作業役が Mac でこの URL から取得して計算した値が一致。Windows では入れる前に SHA-256 と Authenticode の署名（有効・署名者 O=Amazon Web Services, Inc.）を確かめる） |
| AMI | `aws ec2 describe-images --owners amazon` の `Windows_Server-2022-English-Full-Base-*` の最新 | 起動の時に ID を付録 K に記録する |

## 人間が作るもの（スクリプトは作らない・消さない）

- IAM ユーザー `odyssey-mt5-probe-operator` と、その権限 `iam_policy_operator.json`（東京リージョンだけ、t3.medium だけ、タグ付きのものだけを操作できる）
- SSM 用のロール `odyssey-mt5-probe-ssm-role`（AWS 管理ポリシー AmazonSSMManagedInstanceCore。インスタンスプロファイルも同名）
- Budgets の 5 米ドルの予算（通知だけで、支出は止めない）
- 全期間取得のための S3 の権限（下の「IAM の更新（人間。全期間取得の起動の前に 1 回。約 10 分）」）。S3 の一時バケット自体はスクリプトが作り、片付けで消す

## 確かめ方（課金なし）

`bash -n` と shellcheck に加え、偽の `aws` コマンド（呼び出しのたびにプロファイルとリージョンを確かめ、決めた値を返す）を `PATH` の先頭に置いて、`00`→`10`→`20`→`30`→`40`→`90` を通して動かした（2026-10-15。付録 K-3）。この確かめでは `38_full_fetch.sh` のバケットの作成の行を通っておらず、全期間取得で止まった（付録 K-17.4 の 2）。2026-10-17 から、全期間取得で問題になった分岐を実際に通すテストを `tests/unit/test_aws_probe_scripts.py` に置いた（下の「全期間取得の後の道具の修正」）。shellcheck は `cd tools/survey/aws_mt5_probe && uvx --from shellcheck-py shellcheck -x *.sh`。

## 全期間取得の後の道具の修正（付録 K-17.8 の「後続の事項」の 1。2026-10-17）

全期間取得（付録 K-17）で問題になった 3 件を直した。どれも AWS を起動せずに直し、偽の `aws` で確かめた。

| 問題（付録 K-17 の記録） | 直したこと |
|---|---|
| `38_full_fetch.sh` の 62 行目 `tr -dc 'a-z0-9' </dev/urandom \| head -c 8` が、毎回終了コード 141 で止まった（K-17.4 の 2）。head が読み終えると tr が強制終了の合図（SIGPIPE）を受け、`set -o pipefail` で止まる | 乱数 8 桁を `lib.sh` の `random_suffix8` で作る。`od -An -N4 -tx1 /dev/urandom \| tr -d ' \n'`（od が 4 バイトだけ読んで自分で終わり、下流の tr は入力を最後まで読む。下流が先に読むのをやめる箇所が無いので SIGPIPE は起きない）。小文字の 16 進数 8 桁で、バケット名の規則（`assert_probe_bucket` の `[a-z0-9]{8}`）に入る |
| Windows に AWS CLI が無く、AWS Tools for PowerShell の読み込みが 300 秒超で、S3 の道具の確認（120 秒）に間に合わない見込みだった。手作業で AWS CLI v2 を入れた（K-17.4 の 1） | `20_prepare.sh` で AWS CLI v2 を入れる（`lib.sh` の `awscli_install_ps`）。`aws.exe` があり `aws --version` が `aws-cli/2.` で始まれば（正常に動く 2.x なら版を問わず）入れ直さない。無ければ版を固定した MSI（2.38.1）を取り、SHA-256 と Authenticode の署名（状態が Valid、署名者が O=Amazon Web Services, Inc.）を確かめてから `msiexec /i … /qn /norestart` で入れ、`aws --version` を確かめる。どちらの場合も、実際に使う正確な版（例 `aws-cli/2.38.1`）をログに残す。`38_full_fetch.sh` は、この固定の場所（`C:\Program Files\Amazon\AWSCLIV2\aws.exe`）の aws を PATH 上の aws より先に選び（以前は PATH 上の aws が先だった）、S3 の道具の確認で実際に使う aws の場所と版を表示する。msiexec の終了コード 3010 は「導入済み・再起動が必要」と通常の失敗（例 1603「導入に失敗」）と区別して表示し、準備を止める（自動の再起動・続行はしない。人間が Windows を再起動した後に `20_prepare.sh --execute` をもう一度実行する）。`38` の確認の 120 秒は変えていない |
| `90_teardown.sh` の `s3api wait bucket-not-exists`（5 秒 × 20 回）が「Max attempts exceeded」で止まり、直後の head-bucket は 404 だった。もう一度実行して片付けた（K-17.6） | 自分で head-bucket を繰り返す（`lib.sh` の `wait_bucket_absent`。間隔 10 秒・最大 30 回。`S3_DELETE_WAIT_SECONDS`・`S3_DELETE_WAIT_ATTEMPTS`。待ちの合計は約 5 分だが、head-bucket の通信の時間を含まないので、**厳密な「5 分以内」の保証ではない**）。404 で成功とし、毎回の応答（200・403 など）を記録する。上限まで 404 にならなければ、記録 `s3_bucket.env` を残し、残りの確認を表示してから終了コード 1 で終わる（もう一度実行すれば、404 なら記録を退避して終わる） |
| （同じ箇所で見つけた関連の不具合）以前は、片付けの最初と残りの確認で head-bucket の失敗をすべて「バケットは無い」と扱っていた。403（権限の不足など）でも記録を退避していた | `s3_bucket_state` で 200（ある）・404（無い）・それ以外（不明）を分ける。不明のときは消さず、記録を残し、残りの確認に「不明」と出して終了コード 1 |

上の 4 点（削除の後の待ち方、403 などの扱い、msiexec の 3010、導入済みの AWS CLI の版）と、3010 の後の再起動で消える自動の停止の予約を `20_prepare.sh` の実行のたびに起動 + 4 時間 30 分へ予約し直すこと（`lib.sh` の `autostop_ps`。起動の時刻は状態のファイルの `LAUNCHED_AT_EPOCH`、`shutdown /a` の後に `shutdown /s /t`、残り 60 秒未満なら止める）は、2026-10-17 に人間が運用規則として決めた（D12 本文 第0.1.28節に原文、付録 K-18 に規則）。403 などは「バケットの有無が不明」とし、削除の前に不明なら削除せず記録を残して異常終了、削除の後に不明なら上限まで確かめ直し、404 が無ければ記録を残して異常終了する。

`wait bucket-not-exists` がどの応答で終わらなかったのか（S3 の削除の反映の遅れか、403 などか）は、K-17.6 に記録が無く、**原因は確かめていない**。直した道具は毎回の応答を記録するので、次に起きたときは原因が分かる。

### 偽の `aws` で確認した範囲（`tests/unit/test_aws_probe_scripts.py`。AWS につながない）

- `random_suffix8` が `set -euo pipefail` の下で 50 回とも成功し、作った名前が規則と `assert_probe_bucket` を通る（Mac の bash・od・tr で実際に実行）。以前の行が同じ条件で終了コード 141 になることも固定した。
- `38_full_fetch.sh --pairs USDJPY --execute` をバケットの記録が無い状態で実行し、乱数の行を通ってバケットの作成（`s3api create-bucket`、東京リージョン）と `s3_bucket.env` の記録まで進む。修正前のスクリプトでは同じテストが終了コード 141 で失敗する（確認済み）。
- `90_teardown.sh --execute` の分岐: 消した後に 403 → 200 → 404 で成功し記録を退避する／403 が続くと 3 回で止まり、記録を残し、残りの確認に「不明」と出して終了コード 1／最初から 404 なら消す操作をせずに記録を退避する／最初が 403 なら消さずに止まる／`--verify` で 404 を「0」と出す。修正前のスクリプトでは、偽の aws が `wait bucket-not-exists` に K-17.6 と同じ失敗を返すので、成功の分岐のテストが失敗する（確認済み）。
- `20_prepare.sh --execute` が、AWS CLI の導入の PowerShell を SSM で 1 回送り、その中に版を固定した URL・SHA-256・署名の確認・msiexec がこの順にある。`--execute` なしでは何も送らない。
- 自動の停止の予約し直し: 3010 で止まる回にも AWS CLI の導入より前に予約が送られ、再実行で同じ時刻（起動 + 16,200 秒）の予約がもう一度送られる。`pwsh` のテストで、`shutdown /a` の後に残りの秒数で `shutdown /s /t` を呼ぶこと、時刻を過ぎていれば何も呼ばずに止まることを確かめる。
- `20_prepare.sh --execute` の応答の分岐: 3010 の応答では「導入済み・再起動が必要」と表示し、再起動の後の再実行を案内して終了コード 1 で止まり、道具の配置へ進まない／通常の失敗では「導入に失敗」と表示して止まる／成功では実際に使う版（導入済みの 2.31.7、新規の 2.38.1 の 2 例）をログに残して先へ進む。
- AWS CLI の導入の PowerShell の分岐（`pwsh` があるときだけ。GitHub Actions の ubuntu-latest で実行。作業役の Mac には `pwsh` が無いので飛ばす）: 入っていなければ取得 → SHA-256 → 署名 → msiexec の順に進む／入っていれば何もしない／SHA-256 の不一致・署名の状態が Valid でない・署名者が違うときは msiexec を呼ばずに止まる／msiexec が失敗（1603）すれば「install failed」で止まる／3010 では `AWSCLI_RESTART_REQUIRED` の行を出して止まり、版の確認へ進まない。`Invoke-WebRequest`・`Get-FileHash`・`Get-AuthenticodeSignature`・`Start-Process` は偽物、Windows のパスは一時フォルダに置き換える。

### 実環境（AWS・Windows）では未確認の範囲

- Windows Server の本物の `shutdown /a`・`shutdown /s /t` が、SSM の実行者（SYSTEM）から期待どおり予約を置き換えるか。再起動で起動の時の予約が消えることはコードを読んで確かめた事実で、実環境では試していない。
- 実際の S3 の削除の後の head-bucket の応答の並びと、10 秒 × 30 回で 404 を確かめられるか。AWS CLI v2 のエラー文の形（`(404)`・`Not Found`）は偽の aws が真似ただけ。
- Windows Server 2022 の Windows PowerShell 5.1（SSM の `AWS-RunPowerShellScript`）での導入のスクリプトの動作。本物の `Get-AuthenticodeSignature` の Subject の書式（`O=` の値が引用符付きか否かの両方を受けるようにした）、本物の msiexec が 3010 を返す条件と、そのときの SSM の出力に `AWSCLI_RESTART_REQUIRED` の行が残るか、SSM の 900 秒の制限で足りるか。
- MSI の SHA-256 は Mac で取得して計算しただけで、この道具で Windows に入れてはいない（K-17.4 の 1 の手作業の記録と値が一致することだけ確かめた）。
- `38_full_fetch.sh` のバケットの作成より後（SSM・S3 への置き方・回収）は今回変えておらず、今回のテストでも通していない（K-17 の実施で動いた）。

## 最後の調査の手順（付録 K-10。人間の決定 D12-41・第 12 回の決定）

要求は USDJPY の 1 分足だけ、2017・2019 年の 9 区間（一覧は `mt5_api_probe.py` の `FINAL_STAGE`）。上限 12（残り 3 は取り寄せの空振りのやり直しだけ）。2024 年以降は道具が拒む。価格はメモリの中で比べ、保存するのは足の時刻と差の統計だけ。

1. 起動の前（課金なし）: `00_check.sh`。見積は想定 約 0.16・上限 約 0.41 米ドル。必要なら配布 CSV の参照を先に作る: `.venv/bin/python -m tools.survey.mt5_time_survey final-ref --out data/raw/oanda_mt5_probe/_k10/final_ref.json.gz`（置いてあれば `37` が使う。無ければ `37` の初回に作る。置き場は `K10_REF` で変えられる）。
2. 司令塔の「起動してよい」の後: `10_launch.sh --execute` → `95_deadline_guard.sh` を裏で起動 → `20_prepare.sh --execute`。
3. 人間の作業（付録 K-10.4）: 最大バー数を一度別の値にしてから Unlimited に戻し、「ファイル」→「終了」で再起動。「表示」→「銘柄」→ `USDJPY.cl` →「バー」で M1・2017.01.01〜2019.12.31 を Request。「limited by charts settings」が出ないことを確かめ、RDP を「切断」。
4. 取り寄せの確認を兼ねて 1 区間だけ: `37_final_survey.sh --only K10-USDJPY-W2017-03-05 --execute`。0 本なら司令塔に報告する（他の区間で確かめない）。
5. 残りの 8 区間: `37_final_survey.sh --remaining --execute`（4 で 1 本以上返った場合）。0 本の区間が残れば `37_final_survey.sh --redo-empty --execute`（上限 12 の残りの内だけ）。
6. 判定は各回の後に自動で `data/raw/oanda_mt5_probe/<日付>/k10-eval/report.md`・`result.json` に出る。区分は「確認済み」（規則・適用期間・根拠）と「仮定付き」（理由と、候補の規則ごとの解釈）の 2 つだけ。
7. 打ち切り（1 時間 30 分）・上限 12 に達したら要求を出さず、その時点のデータで判定して閉じる。片付けは `90_teardown.sh --execute`。片付けの後に判定し直す場合（AWS は要らない）: `.venv/bin/python -m tools.survey.mt5_time_survey final-evaluate data/raw/oanda_mt5_probe/<日付>/ts-K10-*/summary_ts-K10.json --report <報告> --json-out <結果>`。

## 全期間取得の実行手順（付録 K-16。人間の第 18 回の決定。2026-10-16〜17 に実行し、記録は付録 K-17）

8 通貨（USDJPY・EURJPY・EURUSD・GBPJPY・USDCHF・AUDJPY・GBPUSD・AUDUSD）× 2017-01〜2023-12 の 1 分足を、1 要求 = 1 通貨 × 1 か月（計 672、上限 750）で取り、通貨 × 月ごとに gzip の CSV で保存する。回収は S3 の一時バケット（片付けで消す）。

- **保存するもの**: `<通貨>_<YYYY-MM>_m1.csv.gz`（見出し行あり。列は `time`（API の生の時刻・秒。変換しない）・`open`・`high`・`low`・`close`（bid の足）・`tick_volume`・`spread`・`real_volume`）。価格は研究期間 2017〜2023 年だけ（道具が 2017 年より前・2024 年以降を含む要求を拒む）。区間の終わりちょうどの足と区間の外の足は保存しない（件数だけ記録）。
- **記録**: `manifest.json`（月ごとの状態・行数・最初と最後の足の時刻・出し直しの回数・gzip と展開後の SHA-256。全体に道具の SHA-256・端末の build と `maxbars`・銘柄の `point`・`digits`・`chart_mode`・要求の累計・保存の条件が未確認であること）、`fetch_log.jsonl`（要求ごとの区間・応答の本数・時刻・ディスクの空き。価格は書かない）。
- **置き場**: Mac の `data/raw/oanda_mt5/<取得日>/<通貨>/`（manifest と取得の記録は `<取得日>/` の直下）。`.gitignore` の `data/raw/*` で版管理の外（`45_collect_s3.sh` が置く前に確かめる）。データ配置の決定記録（ADR-0013）の改訂は後続の PR。
- **保存の条件**: 保存・継続利用の条件は未確認のまま、個人の研究利用として保存する。再配布しない（manifest の `storage_terms` に記録）。
- **0 本の月**: 10・30・60・120 秒待って出し直す（要求の数に数えない）。それでも 0 本なら「空」と記録して次へ。**通貨のこの回の最初の月が 0 本なら、その通貨の残りを出さずに止まる**（端末の取り寄せが始まっていない合図）。空の月は `38_full_fetch.sh --redo-empty --execute` でだけ出し直す（上限 750 の内）。
- **止まったときの再開**: 取得済みの月（manifest の状態 ok で SHA-256 が合う）は要求しない。同じ起動の中なら `38_full_fetch.sh --pairs <残りの通貨> --execute` で続ける。終了の後の追加の起動は人間の承認の後。

### IAM の更新（人間。全期間取得の起動の前に 1 回。約 10 分）

作業役は IAM を変えない。PR の merge の後に、人間が AWS のコンソール（ルートではなく管理者のユーザーで）で次の 2 つを行う。どちらも `odyssey-mt5-probe-` で始まる名前のバケットだけに効き、他のバケットは操作できない。

1. **操作ユーザーのポリシー**: IAM →「ポリシー」→ 付録 K-1 の手順 4 で作った `odyssey-mt5-probe-operator-policy` →「編集」→「JSON」→ 中身を、このフォルダの `iam_policy_operator.json` の全文で置き換え →「次へ」→「変更を保存」（新しい版が既定になる。版が 5 つあると保存できないので、その場合は古い版を 1 つ消す）。前の版との差は S3 の 2 つの文（`S3TempBucketOnlyProbePrefix`: バケットの作成・一覧・削除、`S3TempBucketObjectsOnlyProbePrefix`: ファイルを置く・読む・消す。どちらも東京リージョンだけ）だけ。〔約 5 分〕
2. **SSM 用のロールのポリシー**: IAM →「ロール」→ `odyssey-mt5-probe-ssm-role` →「許可を追加」→「インラインポリシーを作成」→「JSON」→ このフォルダの `iam_policy_ssm_role_s3.json` の全文を貼り、名前 `odyssey-mt5-probe-s3-put` で作成。Windows からバケットへ**置くだけ**の権限（読む・消すは無い）。AWS 管理ポリシー AmazonSSMManagedInstanceCore はそのまま。〔約 5 分〕
3. 司令塔に「IAM を更新した」と伝える。

不要になったら（全期間取得の後）、人間が 2 のインラインポリシーと 1 の S3 の 2 つの文を消してよい（スクリプトは IAM を消さない）。

### 人間の作業（RDP。起動の後に作業役の合図で。所要 25〜45 分）

1. 付録 K-8.3 の 1〜4: パスワードを出す、RDP で接続、MT5 の端末を入れる、サブアカウントにログイン。〔10〜15 分〕
2. 「ツール」→「オプション」→「チャート」→「チャートの最大バー数」を**一度別の値（例 100000）にして OK、もう一度開いて Unlimited にして OK**。「ファイル」→「終了」で端末を終了し、起動し直す。〔約 3 分〕
3. 「表示」→「銘柄」で 8 通貨の `<通貨>.cl` を 1 つずつ選び、「バー」で M1・**2017.01.01〜2023.12.31** を Request する。「limited by charts settings」が出ないことと本数が出ることを確かめる。チャートは開かない。〔1 通貨 1〜3 分、計 10〜25 分〕
4. チャートの窓・気配値表示の窓をすべて閉じ、RDP を「切断」にする（サインアウトしない）。司令塔に「設定して切断した」と伝える。〔約 1 分〕

### 作業役の手順

1. 起動の前（課金なし）: `00_check.sh`。S3 の権限は、起動の後の最初のバケットの作成（`38` の初回）で確かめる（起動の前にバケットは作らない）。
2. 司令塔の「起動してよい」の後: `10_launch.sh --execute` → `95_deadline_guard.sh` を裏で起動 → `20_prepare.sh --execute`（新しい道具の SHA-256 を Windows で確かめる）→ 人間に RDP の合図。〔起動・準備 20〜30 分〕
3. 人間の「切断した」の後: `38_full_fetch.sh --pairs USDJPY --execute`（取り寄せの確認を兼ねて 1 通貨だけ。バケットを作り、Windows に S3 の道具（AWS CLI か AWS Tools for PowerShell）があるかを先に確かめる）。最初の月が 0 本なら止まるので、他の通貨で確かめずに司令塔に報告する。
4. 残りの 7 通貨: `38_full_fetch.sh --pairs EURJPY,EURUSD,GBPJPY,USDCHF,AUDJPY,GBPUSD,AUDUSD --execute`。通貨ごとに取得 → S3 に置く → `45_collect_s3.sh <通貨> --execute` で Mac に回収・照合する。〔取得は想定 約 11 分（45 分で打ち切り）、回収は 5〜30 分〕
5. 0 本の月が残れば `38_full_fetch.sh --redo-empty --execute`（上限 750 の内だけ）。打ち切り（起動から 2 時間 30 分）・上限に達したら要求を出さず、その時点の分で閉じる。
6. 片付け: `90_teardown.sh --execute`（インスタンス・セキュリティグループ・キーペアと、回収済みなら S3 のバケットを中身ごと消し、残り 0 を確かめる）。回収が済んでいないと表示されたら `45_collect_s3.sh --all --execute` の後にもう一度。〔5〜10 分〕
7. 片付けの後の照合（AWS は要らない）: `uv run python -m tools.survey.mt5_api_probe fullverify --root data/raw/oanda_mt5/<取得日>`。取得の記録（付録 K-17）は別の PR。

- 見積もり（課金なし。Mac だけ）: `uv run python -m tools.survey.mt5_full_fetch_estimate`（`--json` で同じ数の JSON）。単価はこの `lib.sh` の `PRICE_*`・`TAX_RATE` を読む。想定 約 0.22・上限 約 2.11 米ドル（試験予算 5 米ドルの残りから。人間の第 18 回の決定）。
- 上限: 要求の打ち切り 2 時間 30 分、終了 4 時間、Windows の中の自動の停止 4 時間 30 分、要求 750、ディスク 60 GB（拡張しない。空き 3 GB で止まる）、回収 1 GB（付録 K-16.6）。

## 封印の取得の運用手順（2024 年以降。制約の改定文 ADR-0037。順 6 の S4a で D03 §16.5 の案から移した）

D03 §16.5（運用手順の案。PR #109 で人間が決めた要決定 S2b-1〜S2b-8・仮置き S2b-1〜S2b-14）を、この道具の手順に
した。**取得の実行・起動・費用の発生は、毎回の人間の明示の go の後だけ**（D15-3d (a)・D15-18d (a)）。道具は
価格をファイルに書くだけで、画面・ログ・報告には件数・時刻の範囲・ハッシュだけを出す（ADR-0037 第7項）。
2024 年以降の価格が表示される画面を開かない（ADR-0037 第3項の 1）。

- **窓**: 版管理の設定 1 つ `configs/acquisition/oanda_mt5_m1_sealed_window.yaml` からだけ読む（ADR-0037 第4項）。
  取得を許す期間の始まり `allowed_start` 2024-01-01T00:00Z、取得の開始時点 `acquisition_start`
  2026-11-01T22:00Z（PR #108 の merge の後の最初の NY 日曜 17:00。読込とテストが計算と照合する）、8 通貨。
- **計画**（Mac。AWS は使わない）: `uv run python -m tools.survey.sealed_fetch_plan bulk --out <計画>`（一括取得。
  窓 [2024-01-01T00:00Z, 取得の開始時点)。1 要求 = 1 通貨 × API の時刻の 1 か月、272 要求、上限 300）、
  `... periodic --first --out <計画>`（定期取得の初回。[取得の開始時点, ＋1 週間)、8 要求、上限 9）、
  `... periodic --after <前の回の窓の終わり> --out <計画>`（2 回目以降。前の回の終わりから、前の月の終わり以前の
  最後の取引週の境目（NY 日曜 17:00）まで）。窓の境目は 2024 年以降の時刻の規則の表の標準の組で API の時刻に直す
  （PR #109 の仮置き S2b-9）。計画は道具の側でも窓の設定と照らし、1 つでも外れれば何も要求しない。
- **原ファイルの置き場**: Mac の `data/raw/oanda_mt5_sealed/<取得日>/<通貨>/`（取得記録 `manifest.json`・
  `fetch_log.jsonl` は `<取得日>/` の直下）。研究期間の `data/raw/oanda_mt5/` と分ける（PR #109 の仮置き S2b-6。
  ADR-0013 の 2026-10-29 改訂）。版管理の外。

### 誰が

| 役 | すること |
|---|---|
| 人間 | go の回答、RDP で端末にログインして設定（下の「いつ・どこで」の手順 4）、snapshot の承認の回答、PR の merge |
| 司令塔（Fable） | go と承認の質問（質問ツール）、回答の原文の記録、作業役の起動。成績・価格・ログを読まない |
| 運用役（Opus） | 計画を作る、取得の道具の実行、回収と照合、受入れ・分類・確定、記録の PR。報告は件数・時刻の範囲・ハッシュ・合否だけ |
| 調査役（Sonnet） | 年末年始の告知の確認（OANDA の公開の告知ページだけ。D03 §16.2.5） |

### いつ

| 取得 | 時期 | 窓 | go の質問に添えるもの |
|---|---|---|---|
| 一括取得（S5a。1 回） | S4b の merge の後、人間の go の後（取得の開始時点より後。道具は要求の時点より後を含む要求を出さない） | [2024-01-01T00:00Z, 2026-11-01T22:00Z)。API の時刻の 2024-01〜2026-10 の 34 か月 | 期間・8 通貨・要求の数 272（上限 300）・費用の見積り 約 0.15〜0.3 米ドル・上限 2 米ドル・作業の上限（打ち切り 2 時間・終了 3 時間） |
| 定期取得の初回（S5b） | 2026-11-08T22:00Z の後の最初の週末 | [2026-11-01T22:00Z, 2026-11-08T22:00Z) | 期間・8 通貨・要求の数 8（上限 9）・見積り 約 0.1〜0.2 米ドル・上限 1 米ドル |
| 定期取得の 2 回目以降 | 毎月、前の月の終わりの後の最初の週末 | 前の回の窓の終わりから前の月の終わり以前の最後の取引週の境目まで（例: 2 回目 [2026-11-08T22:00Z, 2026-11-29T22:00Z)） | 同上。年の累計の費用（上限 年 15 米ドル） |

### どこで

- 取得: AWS の東京リージョンの Windows（t3.medium）の MT5 の端末と、この道具（`39_sealed_fetch.sh`。起動の種類は
  `PROBE_RUN_KIND=sealed-bulk` か `sealed-periodic` を `10_launch.sh` の環境変数で選び、状態に記録する）。回収は
  S3 の一時バケット（記録に `S3_PREFIX=sealed` と計画を書く。片付けで消す）。
- 受入れ・分類・確定・承認: Mac の本基盤（`odyssey-fx data accept-mt5 --sealed --fetch-date <取得日> --symbol <通貨>
  --variant standard|candidate --manifest-copy docs/data/sealed/<取得日>_<段>/manifest.json --out data/snapshots`、
  `data classify`・`data approve`。どれも関所の品質確認の許可の下。許可の定義は `configs/quality_checks/`）。
- 記録: `docs/data/sealed/<取得日>_<bulk|periodic-n>/`（D03 §16.3.6）。

### 費用

| 取得 | 見積り | 上限（決定） | 作業の上限（`lib.sh` の起動の種類） |
|---|---|---|---|
| 一括取得 | 約 0.15〜0.3 米ドル（起動から終了まで約 1.5〜2.5 時間） | 1 回 2 米ドル（D15-18c (a)） | 要求の打ち切り 2 時間・終了 3 時間・Windows の自動の停止 3 時間 30 分・要求 300 |
| 定期取得 | 約 0.1〜0.2 米ドル（1〜2 時間） | 1 回 1 米ドル、年 15 米ドル（D15-3c (a)） | 要求の打ち切り 1 時間・終了 2 時間・自動の停止 2 時間 30 分・要求 9 |

上限を超えそうなら止めて報告する。延長は人間の明示の決定（D15 §2.6）。

### 手順（一括取得。定期取得は計画と起動の種類だけが違う）

1. 起動の前（課金なし）: Mac の作業木を main に合わせる（`git pull`。受入れは作業木の消費の記録から初期状態を導く。
   origin との照合は R4 で足す。S4a の PR の要決定 D）。S3・S4a・S4b が merge 済み、表とカレンダー（2024 年以降）が承認済みで期間を覆う、
   許可の定義が merge 済みであることを確かめる。計画を作る（`sealed_fetch_plan`）。`00_check.sh` で見積りを出す。
2. 司令塔が go を問う（上の「いつ」の表の「添えるもの」）。回答の原文を記録する。
3. go の後: `PROBE_RUN_KIND=sealed-bulk 10_launch.sh --execute` → `95_deadline_guard.sh` を裏で起動 →
   `20_prepare.sh --execute` → 人間に RDP の合図。
4. 人間（RDP。約 15〜30 分）: 端末にログインし、最大バー数を一度別の値にしてから Unlimited に戻して再起動する。
   **「表示」→「銘柄」→「バー」の Request は 2023 年以前の範囲だけで行う**（PR #109 の要決定 S2b-8 (a)。2024 年
   以降の範囲を Request しない。2024 年以降は端末の自動の取り寄せと API の要求に任せる）。チャートと気配値表示の窓を
   すべて閉じ、RDP を「切断」にする。2024 年以降の価格が表示される画面を開かない。
5. 運用役: `39_sealed_fetch.sh --plan <計画> --pairs USDJPY --execute`（端末の取り寄せの確認を兼ねる）。最初の
   要求が 0 本なら止めて報告する（他の通貨で確かめない）。
6. 残りの 7 通貨: `39_sealed_fetch.sh --plan <計画> --pairs USDCHF,AUDJPY,EURUSD,EURJPY,GBPJPY,GBPUSD,AUDUSD --execute`。
   通貨ごとに S3 に置き、`45_collect_s3.sh` が Mac に回収・照合する（`sealedverify`。SHA-256・行数・要求の区間）。
7. 0 本の要求が残れば `39_sealed_fetch.sh --plan <計画> --redo-empty --execute`（上限の内だけ）。打ち切り・上限に
   達したら要求を出さずに閉じる。
8. 片付け: `90_teardown.sh --execute` と残り 0 の確認。
9. 受入れ: 通貨 × 組ごとに `data accept-mt5 --sealed`（取得記録の写しを先に `docs/data/sealed/` に置く）、標準の組は
   分類して確定する。
10. 一覧（D03 §16.3.5）を作り、司令塔が承認を問う。承認の後に `data approve`。
11. 記録の PR（D03 §16.3.6）。

- 道具は取得記録に計画の要求の一覧（`planned`）を書き、受入れはその通貨の計画の要求がすべて状態 ok で記録されて
  いることを確かめる（未要求・0 本・失敗の要求が残った通貨のその回は受け入れない。D03 §16.5.8）。
- 再開の時に、記録と合わない（または記録の無い）原ファイルが Windows の出力に既にあれば、道具は上書きせず、要求も
  出さずに止まる（止まった理由 `existing_file`。ADR-0013）。司令塔に報告し、取り直しは新しい取得の回（別の取得日の
  置き場）で行う。
- 封印の取得の起動では、Windows のディスクの使用量・空きを表示・記録しない（空きの差から端末の履歴の大きさが分かる
  ため）。回収の大きさも表示・記録しない（D03 §16.4）。
- 端末の履歴のフォルダ（2024 年以降の自動の取り寄せ分を含む）は、名前・大きさを含めて見ない・記録しない（PR #109 の
  仮置き S2b-11）。取り寄せの完了は道具の最初の要求の結果（0 本かどうかと出し直し）で確かめる。
- 失敗のときの扱いは D03 §16.5.8 の表のとおり（揃った通貨だけを受け入れて承認を問う、受入れの ERROR は種別・件数・
  時刻だけを報告して止める、閲覧の記録の終了の行を書けなかった暫定 snapshot は分類・確定・承認へ進めない）。
