# AWS 上の Windows で OANDA MT5 Python API の小規模な試験を行うスクリプト

D12（`docs/design/D12_data_source_strategy_review.md`）§11.5 の試験を、人間の代わりに作業役が AWS CLI と
SSM（AWS Systems Manager。インスタンスの中でコマンドを実行する AWS の機能）で行うためのもの。
人間がやる作業と作業の記録は `docs/design/D12_appendix_provider_survey.md` の付録 K。

## 順番

| スクリプト | 課金 | すること |
|---|---|---|
| `00_check.sh` | なし | プロファイルの確認、AMI の特定、人間の事前作業（SSM 用のロール・Budgets）の確認、起動の権限の試し（EC2 の `--dry-run`）、費用の再見積 |
| `10_launch.sh --execute` | **始まる** | セキュリティグループ（RDP を人間の IP だけ）・キーペアを作り、t3.medium を 1 台起動。司令塔の「起動してよい」の後だけ |
| `95_deadline_guard.sh` | — | 起動から 6 時間で `90_teardown.sh --execute` を実行する見張り（Mac で裏で動かす） |
| `20_prepare.sh --execute` | 続く | Python 3.12.10・MetaTrader5 5.0.6231 を入れ、試験の道具を `C:\probe\` に置く。RDP の接続先とパスワードの出し方を表示 |
| （人間） | 続く | RDP で接続し、OANDA のマイページから MT5 の端末を入れてログインし、チャートを閉じ、RDP を「切断」する（付録 K-2） |
| `30_run_probe.sh [段] --execute` | 続く | 試験の道具を実行。段は `usdjpy-time`（既定）・`usdjpy-range`・`others` |
| `40_collect.sh <段> --execute` | 続く | 結果を回収し `data/raw/oanda_mt5_probe/<試験の日付>/<段>/` に置き、SHA-256 の一覧を作る |
| `90_teardown.sh --execute` | 止まる | 終了と削除、残りの確認（`--verify` は確認だけ） |

`--execute` を付けなければ、どのスクリプトも計画を表示するだけで何も変えない。

## 守ること（スクリプトに組み込んだもの）

- AWS CLI は `lib.sh` の `aws_p` でだけ呼び、プロファイル `odyssey-mt5-probe`・リージョン `ap-northeast-1` に固定する。環境変数の認証情報は消してから呼ぶ。認証情報のファイルは読まない。
- 作るものにはタグ `Project=odyssey-mt5-probe` と、`odyssey-mt5-probe` を含む名前を付ける。消すのはこのタグのものだけ。
- 構成: t3.medium、CPU クレジットは標準、gp3 30 GB（終了時に削除）、Elastic IP なし、IMDSv2 必須、受信は RDP を人間の IP（`PROBE_RDP_CIDR`、既定 126.221.39.9/32）からだけ、シャットダウンの動作は停止、Windows の中で 5 時間 30 分後に自動の停止。
- 起動から 6 時間を超えたら、`90_teardown.sh` 以外は動かない（`check_deadline`）。
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
