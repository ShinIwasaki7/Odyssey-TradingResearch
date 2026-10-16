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
| `20_prepare.sh --execute` | 続く | Python 3.12.10・MetaTrader5 5.0.6231 を入れ、試験の道具を `C:\probe\` に置く。RDP の接続先とパスワードの出し方を表示 |
| （人間） | 続く | RDP で接続し、OANDA のマイページから MT5 の端末を入れてログインし、チャートを閉じ、RDP を「切断」する（付録 K-2） |
| `30_run_probe.sh [段] --execute` | 続く | 試験の道具を実行。段は `usdjpy-time`（既定）・`usdjpy-range`・`others` |
| `40_collect.sh <段> --execute` | 続く | 結果を回収し `data/raw/oanda_mt5_probe/<試験の日付>/<段>/` に置き、SHA-256 の一覧を作る |
| `35_time_survey.sh <段> --execute` | 続く | 時刻の調査（D12 §11.7）の段と空白の測定（§11.9、段 G）を実行し、回収し、Mac で評価して決定木の次の段を示す（付録 K-8）。**最後の調査の起動では動かない** |
| `37_final_survey.sh [--only <要求,...> \| --remaining \| --redo-empty] --execute` | 続く | 最後の調査（付録 K-10）の 9 区間を実行し、回収し、Mac で「確認済み」「仮定付き」を判定する（下の「最後の調査の手順」） |
| `38_full_fetch.sh [--pairs <通貨,...> \| --redo-empty] --execute` | 続く | 全期間取得（付録 K-16）。S3 の一時バケットを作り、通貨ごとに取得 → S3 に置く → `45` で回収（下の「全期間取得の実行手順」） |
| `45_collect_s3.sh <通貨,...\|--all> --execute` | S3 だけ | S3 の一時バケットから受け取り、manifest の SHA-256・行数と照合してから `data/raw/oanda_mt5/<取得日>/<通貨>/` に置く。インスタンスの終了の後でも動く |
| `90_teardown.sh --execute` | 止まる | 終了と削除、残りの確認（`--verify` は確認だけ）。S3 の一時バケットは回収が済んでいれば中身ごと消す |

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
| AMI | `aws ec2 describe-images --owners amazon` の `Windows_Server-2022-English-Full-Base-*` の最新 | 起動の時に ID を付録 K に記録する |

## 人間が作るもの（スクリプトは作らない・消さない）

- IAM ユーザー `odyssey-mt5-probe-operator` と、その権限 `iam_policy_operator.json`（東京リージョンだけ、t3.medium だけ、タグ付きのものだけを操作できる）
- SSM 用のロール `odyssey-mt5-probe-ssm-role`（AWS 管理ポリシー AmazonSSMManagedInstanceCore。インスタンスプロファイルも同名）
- Budgets の 5 米ドルの予算（通知だけで、支出は止めない）
- 全期間取得のための S3 の権限（下の「IAM の更新（人間。全期間取得の起動の前に 1 回。約 10 分）」）。S3 の一時バケット自体はスクリプトが作り、片付けで消す

## 確かめ方（課金なし）

`bash -n` と shellcheck に加え、偽の `aws` コマンド（呼び出しのたびにプロファイルとリージョンを確かめ、決めた値を返す）を `PATH` の先頭に置いて、`00`→`10`→`20`→`30`→`40`→`90` を通して動かした（2026-10-15。付録 K-3）。

## 最後の調査の手順（付録 K-10。人間の決定 D12-41・第 12 回の決定）

要求は USDJPY の 1 分足だけ、2017・2019 年の 9 区間（一覧は `mt5_api_probe.py` の `FINAL_STAGE`）。上限 12（残り 3 は取り寄せの空振りのやり直しだけ）。2024 年以降は道具が拒む。価格はメモリの中で比べ、保存するのは足の時刻と差の統計だけ。

1. 起動の前（課金なし）: `00_check.sh`。見積は想定 約 0.16・上限 約 0.41 米ドル。必要なら配布 CSV の参照を先に作る: `.venv/bin/python -m tools.survey.mt5_time_survey final-ref --out data/raw/oanda_mt5_probe/_k10/final_ref.json.gz`（置いてあれば `37` が使う。無ければ `37` の初回に作る。置き場は `K10_REF` で変えられる）。
2. 司令塔の「起動してよい」の後: `10_launch.sh --execute` → `95_deadline_guard.sh` を裏で起動 → `20_prepare.sh --execute`。
3. 人間の作業（付録 K-10.4）: 最大バー数を一度別の値にしてから Unlimited に戻し、「ファイル」→「終了」で再起動。「表示」→「銘柄」→ `USDJPY.cl` →「バー」で M1・2017.01.01〜2019.12.31 を Request。「limited by charts settings」が出ないことを確かめ、RDP を「切断」。
4. 取り寄せの確認を兼ねて 1 区間だけ: `37_final_survey.sh --only K10-USDJPY-W2017-03-05 --execute`。0 本なら司令塔に報告する（他の区間で確かめない）。
5. 残りの 8 区間: `37_final_survey.sh --remaining --execute`（4 で 1 本以上返った場合）。0 本の区間が残れば `37_final_survey.sh --redo-empty --execute`（上限 12 の残りの内だけ）。
6. 判定は各回の後に自動で `data/raw/oanda_mt5_probe/<日付>/k10-eval/report.md`・`result.json` に出る。区分は「確認済み」（規則・適用期間・根拠）と「仮定付き」（理由と、候補の規則ごとの解釈）の 2 つだけ。
7. 打ち切り（1 時間 30 分）・上限 12 に達したら要求を出さず、その時点のデータで判定して閉じる。片付けは `90_teardown.sh --execute`。片付けの後に判定し直す場合（AWS は要らない）: `.venv/bin/python -m tools.survey.mt5_time_survey final-evaluate data/raw/oanda_mt5_probe/<日付>/ts-K10-*/summary_ts-K10.json --report <報告> --json-out <結果>`。

## 全期間取得の実行手順（付録 K-16。人間の第 18 回の決定。まだ実行していない）

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
