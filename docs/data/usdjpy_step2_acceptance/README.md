# 順 2: USDJPY 全期間の受入れ・分類（標準の組・候補の組）— 承認前の一覧

人間の指示「新データでの研究開始の準備、順 1〜3」（2026-10-18）の順 2 の記録。OANDA MT5 API の USDJPY 1 分足
（取得日 2026-10-16、2017-01〜2023-12 の 84 か月）を、時刻の変換の組ごとに 1 つずつ snapshot（受け入れたデータの
固定した版）にした。手順は D12 §12.4（決定 D12-136 (a)）。

- **この一覧は人間の承認の材料**。人間は 2026-10-19 に 2 つの snapshot をどちらも「承認する」と決めた（原文は
  D12 §0.1.33）。承認の記録は §5。承認するまでは研究の読み取りの対象にならない（D03 §3.7.1 の 3）。
- 書いてあるのは件数・時刻・被覆・ハッシュ・分類だけ。実価格と戦略の成績は読んでも書いてもいない。2024 年以降の
  データには触れていない。原データは読むだけで変えていない（受入れの前後でファイルの更新時刻と大きさが同じ）。
- 負のスプレッドの件数の行はこの一覧に置かない（D12 §12.8 の仮置き 7 の決定「書かない」）。負のスプレッドが続く
  区間は、あれば各組の検査報告に種別 `NEGATIVE_SPREAD` として 1 区間 1 件で載る。今回の両組の検査報告には
  `NEGATIVE_SPREAD` の項目が無い（該当する区間が無かった）。
- 版管理の外: snapshot の本体（`data/snapshots/<識別子>/*/bars.parquet`。価格を含む）と原データ
  （`data/raw/oanda_mt5/2026-10-16/`）。版管理の中: 各 snapshot の `manifest.json`・`integrity_report.json`
  （標準の組は暫定の報告 `integrity_report_provisional.json` も）、本ディレクトリの分類ファイル・画面出力・本書。

## 1. 組ごとの一覧（D12 §12.4 の手順 5 の項目）

| 項目 | 標準の組（`standard`） | 候補の組（`candidate`） |
|---|---|---|
| 最終の識別子（承認に使う） | `ecaaf53fcdedbc1977184b390c36e2714c658829341eed3ec5d71588d3bfc622` | `f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca` |
| 暫定の識別子（受入れの直後） | `1522e8ed30edc1d4d8961a4a8772af3fd777ef3f050db51bf37eecf02abb7cc9` | `f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca`（分類が 0 件で作り直しが無いので最終と同じ） |
| 受け入れた年月（API の時刻） | 2017-01〜2023-12 の 84 か月（`--months` を省いて解決した値。manifest の `conversion.mt5.months`） | 同左 |
| 変換の組（`--variant` を明示） | `standard`（manifest の `time_rule_table.variant`） | `candidate`（同左） |
| 時刻の規則の表 | `oanda_mt5_time_rules` 版 1、SHA-256 `70bb369bc606d8402b172672c07d75506951f86b1295c00344661a68247b3ec3` | 同左 |
| 承認済みの表の一覧のファイル | `approved_time_rule_tables.yaml` SHA-256 `2a4eee10144764ac228e102ef56965615463ff60d2abf54d6240d39e0bf0d90b` | 同左 |
| カレンダー | `fx_ny17_oanda` 版 1、ファイル SHA-256 `a9da0d9ae6b3246b5bb67757a7b6cb4ac50e5a012c96a5c4a075bc1148a79b96` | 同左 |
| 数え方の規則 | `oanda_mt5_counting_rules` 版 1、ファイル SHA-256 `fa98eef8cec035a4564b849bc4098d1488aee968bc3a5c2d44efb3ba75ca6444` | 同左 |
| 集約規則の版・列対応の宣言 | `m1_coverage_v1`・`oanda_mt5_m1@v1`（ファイル SHA-256 `791b9917e073c8c04faa50ef6bed3906335b4d680bce51dbf96434516f895fb8`） | 同左 |
| 時間足の定義（分類に渡した） | `configs/calendars/timeframes_v2.yaml` SHA-256 `86f6d16689f7642b6b845e33df662ee2a67c314d623817848c08f0f37a4685b1` | 同左 |
| 入力: 取得記録 | `manifest.json` SHA-256 `e4664b011600337d619270986e243d11f467f2be425a841b70e5851807daca53`（原本と版管理の写しが一致） | 同左 |
| 入力: 原ファイル | 84 ファイル・2,602,100 行。ファイルごとの SHA-256 は manifest の `sources`（取得記録と照合済み） | 同左 |
| 取得の範囲（UTC）・取得の失敗 | 2016-12-31T22:00Z〜2023-12-31T22:00Z・0 件 | 同左 |
| 分類（解決した値） | 休場帯の足（`UNEXPECTED_BAR`）77 件 → すべて「セッション外のデータ異常」（`OUT_OF_SESSION_DATA`）。記入 3 件（週ごと）を警告ごとに解決して 77 件（manifest の `resolved_classifications`）。分類ファイルは `standard/classification.yaml`（SHA-256 `7ed5be0fd503eabe6290b745ab73f5c2ffda4f43f8bbfb167020349a43d3521b`） | 分類の対象 0 件（空の分類で確定）。分類ファイルは `candidate/classification.yaml`（SHA-256 `5c541067474323bcabe45a04634d4db493b281f4b6f9a24d4d2928c9c9adde40`） |
| 除いた足と理由 | 77 本。理由: 決定 D12-99「セッション外のデータ異常として今回は使わず確定」（`excluded_bar_count` の合計） | 分類で除いた足は 0 本。カレンダーの外に出た 1 分足 645 本（13 週）は足の生成に使わず件数だけを報告（決定 D12-97 (a)。`CANDIDATE_OUT_OF_CALENDAR`） |
| 週ごとの境界の検査（365 週） | 不合格 0 週（確定の後。暫定の段階の休場帯の足は上の 77 本） | 不合格 13 週（上のカレンダーの外の足の週） |
| 研究対象区間（UTC） | 2017-03-24T21:00Z〜2023-12-29T22:00Z（1 区間）。前回の集計と一致 | 同左。一致 |
| 処理時間（壁時計） | 受入れ 264 秒・分類 321 秒 | 受入れ 268 秒・分類 164 秒 |
| 受入れ・分類を行ったコミット | `a98fa2af6305eff58649bd5cb31e399eff7303b6`（main。負のスプレッドの実装 PR #90 の merge の後） | 同左 |
| 変換コード版（manifest の `code_version`） | `d11ff0aadeb9aface3ec0a5800b36e59ee8479c75490ce1548bc85923ba44d7e`（パッケージの `.py` の内容のダイジェスト） | 同左 |
| 確定の記録の SHA-256 | `manifest.json` `bb9e83b56161bef2a1030c73c81fd8203ed7557054cd68762ab6a2d3260429c6`、`integrity_report.json` `1a35d84fce1d315c38652101f7a2ee5bdc2391c9725cdc438aa2285867c21144`、`integrity_report_provisional.json` `dfcbc16241b1d233489214375f59b4135435a78bd0d30c18530c5778bb2a0898` | `manifest.json` `488df631c1280d9dde7e16553446eda3c0c7c950a5897cfb08ca0a3128f9837f`、`integrity_report.json` `351618f18bb1567875f824f6bcfe7178a3af33592d4ece3b78bddb1f82670eca` |

- 処理時間は、`uv run` の直前と直後の時刻（秒の単位）の差。uv の起動を含む。4 回は 1 回ずつ順に実行した（重ねていない）。
  機械: macOS（x86_64）。
- `manifest.json` は承認で承認の記録が足されるので、上の `manifest.json` の SHA-256 は承認前の値。識別子は承認で変わらない。

### 1.1 系列ごとの件数（確定の後。manifest の `series`）

| 系列 | 組 | 作った足 | 部分観測の足 | 無記録の足（作らない） | 取得不足の足 | 無記録の分 |
|---|---|---|---|---|---|---|
| 1 分足 | standard | 2,602,023 | — | — | 0 | 12,977 |
| 1 分足 | candidate | 2,601,455 | — | — | 0 | 13,545 |
| 15 分足 | standard | 174,068 | 3,008 | 268 | 0 | 〃 |
| 15 分足 | candidate | 174,028 | 3,002 | 308 | 0 | 〃 |
| 1 時間足 | standard | 43,527 | 1,747 | 57 | 0 | 〃 |
| 1 時間足 | candidate | 43,517 | 1,741 | 67 | 0 | 〃 |
| 4 時間足 | standard | 10,887 | 1,604 | 9 | 0 | 〃 |
| 4 時間足 | candidate | 10,887 | 1,597 | 9 | 0 | 〃 |
| 日足 | standard | 1,815 | 1,280 | 1 | 0 | 〃 |
| 日足 | candidate | 1,815 | 1,280 | 1 | 0 | 〃 |

- 1 分足の本数: 原ファイルの 2,602,100 行から、標準の組は分類で除いた 77 本、候補の組はカレンダーの外の 645 本を引いた数。
- 日足の全分観測は両組とも 535 本（部分観測 1,280 本と合わせて 1,815 本）。取得不足は両組とも 0。

### 1.2 検査報告の種別ごとの件数

検査報告の各項目には重さが付く（D03 §3.9・§15.6 の 2）: **INFO** は記録だけ（受入れも確定も止めない）、**WARN** は
注意の記録で、種別によっては人間の分類が済むまで確定しない（下の表では休場帯の足だけが分類必須）、**ERROR** は
受入れの失敗（何も書かずに止まる）。

| 種別（重さ） | 系列 | standard 暫定 | standard 確定 | candidate |
|---|---|---|---|---|
| 無記録の分の連続 `UNRECORDED_MINUTES`（INFO） | 1 分足 | 4,675 | 4,675 | 4,678 |
| 休場帯の足 `UNEXPECTED_BAR`（WARN、分類必須） | 1 分足 | 77 | 0（分類で除いた） | 0 |
| カレンダーの外の足 `CANDIDATE_OUT_OF_CALENDAR`（INFO） | 1 分足 | — | — | 13（週。計 645 本） |
| 無記録の足 `NO_RECORD_BAR`（INFO） | 15 分・1 時間・4 時間・日足 | 268・57・9・1 | 268・57・9・1 | 308・67・9・1 |
| 部分観測の足の要約 `PARTIAL_COVERAGE_BAR`（INFO） | 15 分・1 時間・4 時間・日足 | 各 1 | 各 1 | 各 1 |
| 合計（ERROR／WARN／INFO） | — | 0／77／5,014 | 0／0／5,014 | 0／0／5,080 |

各組の画面出力（観測できない項目の一覧を含む）は `standard/accept_stdout.txt`・`standard/classify_stdout.txt`・
`candidate/accept_stdout.txt`・`candidate/classify_stdout.txt`。受入れの画面出力の「原データの置き場」の行だけは、
作業機のホームディレクトリの絶対パスを `<本体の作業ディレクトリ>` に置き換えた（他の行は出力のまま）。

## 2. 分類ファイルの要約（標準の組。価格なし）

全期間の集計（PR #85）で人間が分類した USDJPY の 3 週（決定 D12-99）と、週・本数とも一致した。新しい種類の警告は無い
（2018-03 の 2 週の足は他の通貨（GBPUSD・EURJPY）のもので、USDJPY には無い）。

| 週（NY 暦の日曜） | 区間（UTC、半開） | 本数 | 分類 | 内容 |
|---|---|---|---|---|
| 2019-10-27 | 2019-11-01T21:00Z〜21:58Z | 57（21:00〜21:55 の 55 本と 21:56〜21:58 の 2 本） | セッション外のデータ異常 | 金曜の閉場の後の約 1 時間。米国式なら営業時間に当たる観測の事実（時刻の規則の表の次の版の検討材料。表 版 1 は変えない） |
| 2019-12-22 | 2019-12-25T22:00Z〜22:10Z | 10 | 同上 | 2019-12-26 の取引日の東京サーバーの再開の遅れ（カレンダーの休場の宣言）の時間帯 |
| 2019-12-29 | 2020-01-01T22:00Z〜22:10Z | 10 | 同上 | 2020-01-02 の取引日の東京サーバーの再開の遅れ（同上） |

候補の組のカレンダーの外の足（分類の対象ではない。件数だけ）: 2019-12-22・2019-12-29 の週 各 10 本、2020-03-08・03-15・
03-22・2020-10-25・2021-03-14・03-21・2021-10-31 の週 各 55 本、2022-03-13・03-20・2022-10-30・2023-10-29 の週 各 60 本。
計 13 週・645 本。

## 3. 前回の集計（`docs/data/full_period_feasibility/`、PR #85）との突き合わせ（件数だけ）

| 項目 | 前回 standard | 今回 standard | 前回 candidate | 今回 candidate | 一致 |
|---|---|---|---|---|---|
| 研究対象区間（UTC） | 2017-03-24T21:00Z〜2023-12-29T22:00Z | 同 | 同 | 同 | 一致 |
| 研究対象の日足（うち準備のやり直し中） | 1,755（60） | 1,755（60） | 1,755（60） | 1,755（60） | 一致 |
| 準備の期間の日足 | 60 | 60 | 60 | 60 | 一致 |
| 全分無記録の日（日足を作らない） | 1（2022-12-26） | 1（2022-12-26） | 1 | 1（2022-12-26） | 一致 |
| 準備のやり直しの期間 | 2022-12-26T22:00Z〜2023-03-20T21:00Z | 同 | 同 | 同 | 一致 |
| 日足の作った足・全分観測・部分観測 | 1,815・535・1,280 | 1,815・535・1,280 | 1,815・535・1,280 | 1,815・535・1,280 | 一致 |
| 日足の被覆率の最小 | 0.745139 | 0.745139 | 0.745139 | 0.745139 | 一致 |
| 休場帯の足（`UNEXPECTED_BAR`） | 77（3 週） | 77（3 週） | — | — | 一致 |
| カレンダーの外の足（`CANDIDATE_OUT_OF_CALENDAR`） | — | — | 645（13 週） | 645（13 週） | 一致（週ごとも一致） |
| 無記録の分・無記録の連続の数 | 12,977・4,675 | 12,977・4,675 | 13,545・4,678 | 13,545・4,678 | 一致 |
| 15 分・1 時間・4 時間足の作った足 | 174,068・43,527・10,887 | 同 | 174,028・43,517・10,887 | 同 | 一致 |
| 同 部分観測の足 | 3,008・1,747・1,604 | 同 | 3,002・1,741・1,597 | 同 | 一致 |
| 同 無記録の足 | 268・57・9 | 同 | 308・67・9 | 同 | 一致 |
| 取得不足 | 0 | 0 | 0 | 0 | 一致 |

- 前回の値の出典: `summary.md` §1・§2・§3・§4、`bar_counts.csv`・`research_windows.csv`・`warmup_resets.csv`・
  `out_of_calendar_bars.csv`。不一致は 0 件。
- 今回の研究対象区間・準備の数は、前回と同じ定義（日足の準備 60 本。準備に必要な日足の最後の 1 本の公開時刻から連続区間の
  終わりまで）で、確定した snapshot の日足 partition の時刻と被覆の列だけ（価格の列は読まない）から数えた。

## 4. 手順の記録

1. 固定ファイル 7 つの SHA-256 を D12 §12.1 の表と照合した（`shasum -a 256`）。7 つとも一致（時刻の規則の表・承認済みの表の一覧・
   カレンダー・数え方の規則・取得記録の写し・列対応の宣言・時間足の定義 版 2）。取得記録の原本も写しと一致。
2. 受入れ（リポジトリの根で。原データはコピーせず、本体の作業ディレクトリの置き場を `--raw-root` で指した）:
   ```
   uv run odyssey-fx data accept-mt5 --fetch-date 2026-10-16 --symbol USDJPY --variant standard \
     --raw-root <本体の作業ディレクトリ>/data/raw/oanda_mt5 --out data/snapshots
   uv run odyssey-fx data accept-mt5 --fetch-date 2026-10-16 --symbol USDJPY --variant candidate \
     --raw-root <本体の作業ディレクトリ>/data/raw/oanda_mt5 --out data/snapshots
   ```
3. 分類と確定:
   ```
   uv run odyssey-fx data classify --pending 1522e8ed30edc1d4d8961a4a8772af3fd777ef3f050db51bf37eecf02abb7cc9 \
     --decisions docs/data/usdjpy_step2_acceptance/standard/classification.yaml \
     --timeframes configs/calendars/timeframes_v2.yaml --out data/snapshots
   uv run odyssey-fx data classify --pending f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca \
     --decisions docs/data/usdjpy_step2_acceptance/candidate/classification.yaml \
     --timeframes configs/calendars/timeframes_v2.yaml --out data/snapshots
   ```
4. 承認（人間の決定「承認する」（D12 §0.1.33）の後。snapshot の本体はこの作業ツリーの `data/snapshots/` にだけあるので、同じ場所で実行する。結果は §5）:
   ```
   uv run odyssey-fx data approve --snapshot ecaaf53fcdedbc1977184b390c36e2714c658829341eed3ec5d71588d3bfc622 --by ShinIwasaki7 --out data/snapshots
   uv run odyssey-fx data approve --snapshot f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca --by ShinIwasaki7 --out data/snapshots
   ```

## 5. 承認の記録

人間の決定（2026-10-19。原文は D12 §0.1.33）: 標準・候補の 2 つの snapshot をどちらも「承認する」。仮置き 5 件もすべて
推奨（現状）で決まった（D12 §12.4 の末尾「順 2 の実行で決めた運用」）。

| 組 | 識別子 | 承認者 | 承認の時刻（UTC） | 承認時の head | 承認後の `manifest.json` の SHA-256 | 識別子の変化 |
|---|---|---|---|---|---|---|
| 標準 | `ecaaf53fcdedbc1977184b390c36e2714c658829341eed3ec5d71588d3bfc622` | ShinIwasaki7 | 2026-10-19T13:04:00.979215Z | `894c47e346e3eb5196dacea72881b9272104af15` | `efb311850c6fcc38d77a0f9fea774949bb56f8ae4ca4efe9d50f3ed63f637ee8` | 無し（manifest から再計算した識別子がディレクトリ名と一致） |
| 候補 | `f5214ed8b5e4bc0ca669b1fdd770b8522cc3e924cb96e60f22b08e238c7f25ca` | ShinIwasaki7 | 2026-10-19T15:02:18.697121Z | `610c975c149093515dd35e5d8d4176fd7959977d` | `1f1c26b4bbbaf3915e0140a6867003bd26967eb4757119ffd5eed17def1340f5` | 無し（manifest から再計算した識別子がディレクトリ名と一致） |

- 承認で manifest に足されたのは、承認の記録（承認者・時刻・コメント（空））と価格基準の宣言の記録（宣言者・時刻。基準は
  bid、検証済みではない）だけ。識別子・検査報告・partition は変わらない。
- 候補の組の承認は、最初の実行が実装役の作業環境の権限の判定で拒否された（拒否を回避する操作はしていない）。人間が
  「権限を与えて再実行」と答えた後に、同じコマンド（§4 の 4）を再実行した。
