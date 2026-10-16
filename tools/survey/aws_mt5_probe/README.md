# AWS 上の Windows で OANDA MT5 Python API の小規模な試験を行うスクリプト

D12（`docs/design/D12_data_source_strategy_review.md`）§11.5 の試験を、人間の代わりに作業役が AWS CLI と
SSM（AWS Systems Manager。インスタンスの中でコマンドを実行する AWS の機能）で行うためのもの。
人間がやる作業と作業の記録は `docs/design/D12_appendix_provider_survey.md` の付録 K。

## 順番

| スクリプト | 課金 | すること |
|---|---|---|
| `00_check.sh` | なし | プロファイルの確認、AMI の特定、人間の事前作業（SSM 用のロール・Budgets）の確認、起動の権限の試し（EC2 の `--dry-run`）、費用の再見積 |
| `10_launch.sh --execute` | **始まる** | セキュリティグループ（RDP を人間の IP だけ）・キーペアを作り、t3.medium を 1 台起動。司令塔の「起動してよい」の後だけ |
| `95_deadline_guard.sh` | — | 起動から 2 時間（最後の調査。前回は 4 時間）で `90_teardown.sh --execute` を実行する見張り（Mac で裏で動かす） |
| `20_prepare.sh --execute` | 続く | Python 3.12.10・MetaTrader5 5.0.6231 を入れ、試験の道具を `C:\probe\` に置く。RDP の接続先とパスワードの出し方を表示 |
| （人間） | 続く | RDP で接続し、OANDA のマイページから MT5 の端末を入れてログインし、チャートを閉じ、RDP を「切断」する（付録 K-2） |
| `30_run_probe.sh [段] --execute` | 続く | 試験の道具を実行。段は `usdjpy-time`（既定）・`usdjpy-range`・`others` |
| `40_collect.sh <段> --execute` | 続く | 結果を回収し `data/raw/oanda_mt5_probe/<試験の日付>/<段>/` に置き、SHA-256 の一覧を作る |
| `35_time_survey.sh <段> --execute` | 続く | 時刻の調査（D12 §11.7）の段と空白の測定（§11.9、段 G）を実行し、回収し、Mac で評価して決定木の次の段を示す（付録 K-8）。**最後の調査の起動では動かない** |
| `37_final_survey.sh [--only <要求,...> \| --remaining \| --redo-empty] --execute` | 続く | 最後の調査（付録 K-10）の 9 区間を実行し、回収し、Mac で「確認済み」「仮定付き」を判定する（下の「最後の調査の手順」） |
| `90_teardown.sh --execute` | 止まる | 終了と削除、残りの確認（`--verify` は確認だけ） |

`--execute` を付けなければ、どのスクリプトも計画を表示するだけで何も変えない。

## 守ること（スクリプトに組み込んだもの）

- AWS CLI は `lib.sh` の `aws_p` でだけ呼び、プロファイル `odyssey-mt5-probe`・リージョン `ap-northeast-1` に固定する。環境変数の認証情報は消してから呼ぶ。認証情報のファイルは読まない。
- 作るものにはタグ `Project=odyssey-mt5-probe` と、`odyssey-mt5-probe` を含む名前を付ける。消すのはこのタグのものだけ。
- 構成: t3.medium、CPU クレジットは標準、gp3 60 GB（終了時に削除。上限。拡張しない）、Elastic IP なし、IMDSv2 必須、受信は RDP を人間の IP（`PROBE_RDP_CIDR`、既定 126.221.39.9/32）からだけ、シャットダウンの動作は停止、Windows の中で 2 時間 30 分後に自動の停止（`PROBE_SHUTDOWN_SECONDS`）。
- 作業上限（最後の調査。付録 K-10.3）: 起動から 1 時間 30 分で要求を打ち切り（`FINAL_TIME_LIMIT_SECONDS`。`37_final_survey.sh` が止まる）、2 時間で終了（`PROBE_DEADLINE_SECONDS`。`check_deadline` と `95_deadline_guard.sh`）。終了の時刻を過ぎたら `90_teardown.sh` 以外は動かない。前回（付録 K-8・K-9）の値は 3 時間・4 時間・4 時間 30 分だった。
- 起動の種類は `PROBE_RUN_KIND=final`（`lib.sh`）。この起動では一覧の外の要求を出す段（`30_run_probe.sh`・`35_time_survey.sh`・`36_sync_trigger.sh`）は動かない（`forbid_in_final_run`）。
- 回収（SSM の出力で受け取る zip）の合計は 1 GB まで（`PROBE_COLLECT_LIMIT_BYTES`。送信の上限 1 GB のうち道具で数えられる分。RDP の通信は数えられない）。
- 要求の区間は試験の道具の段の名前でだけ指定する。道具は 2024-01-01T00:00Z 以降を含む要求を出さない。`30_run_probe.sh` は実行の前に、注文・売買・口座の関数の呼び出しが道具に無いことを文字列で確かめる。
- 回収するのは道具の出力（`C:\probe\out\<段>`）だけ。MT5 の端末のデータのフォルダは読まない。

## 設計の選択

- **端末とのつなぎ方**: SSM の既定の実行者（SYSTEM）は、人間が RDP でログインした Administrator のセッションの MT5 の端末とつながらない見込みなので、`30_run_probe.sh` は Administrator の対話のセッションで動くタスク（Windows のタスク スケジューラ）を登録して実行する。人間は RDP を「切断」にし、サインアウトしない。
- **結果の回収**: S3 などの別のリソースを作らないため、zip を SSM の出力で 13,500 バイトずつ base64 で受け取り、SHA-256 の一致を確かめる（SSM の出力は 1 回あたり約 24,000 文字で切れる）。
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

## 全期間取得（未実行。付録 K-16 の計画。人間の承認待ち）

8 通貨 × 2017-01〜2023-12 の 1 分足を、1 要求 = 1 通貨 × 1 か月（計 672）で取り、gzip の CSV と manifest で保存する計画。**まだ実行しない。スクリプトと道具（段 `fullfetch`）は承認の後に作業役が足す**（今のスクリプトの挙動は変えていない）。

- 見積もり（課金なし。Mac だけ）: `uv run python -m tools.survey.mt5_full_fetch_estimate`（`--json` で同じ数の JSON）。単価はこの `lib.sh` の `PRICE_*`・`TAX_RATE` を読む。
- 今の回収（`40_collect.sh`。SSM の出力で 13,500 バイトずつ）は実測 約 1,900 バイト/秒で、全期間（圧縮後 約 0.3 GB）では約 43 時間かかる。回収の経路（S3・SSM のポート転送・RDP のフォルダの共有）は人間が決める（付録 K-16.3）。
- 上限の案: 要求の打ち切り 2 時間 30 分、終了 4 時間、Windows の中の自動の停止 4 時間 30 分、要求 750、ディスク 60 GB（拡張しない）、回収 1 GB（付録 K-16.6）。
