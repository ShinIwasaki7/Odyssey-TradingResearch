# ADR-0013: データは data/raw/market へ移し実体を git 管理外にする

- 状態: 承認（2026-09-18。2026-09-20 に access log の追跡を追加。同日、検査報告の追跡を追加。2026-10-01 に補充分の置き場を追加）。2026-10-29 改訂（2024 年以降の封印の取り込みの置き場。順 6 の封印レーン S4a の PR の merge で確定。下の改訂履歴）。2026-10-17 改訂案（OANDA MT5 API の取得データ・取得記録の写し・時刻の規則の表の置き場。設計 PR `docs/tick-record-model-design` の承認で確定）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 C-1、[D12](../design/D12_data_source_strategy_review.md) §0.1.25（D12-79 の内訳 3「data/raw/oanda_mt5/<取得日>/<通貨>/（推奨）。データ配置の決定記録（ADR-0013）の改訂が要る」）・§0.1.26・§11.7.10、[D03](../design/D03_marketdata_and_time.md) §15

## 文脈

`data/market/` の20個の CSV（約230MB）は未追跡で、既存の `.gitignore` は `data/*` を一括除外しており snapshot manifest も追跡できない。

## 決定

```text
data/raw/market/            移管した20個の CSV。上書き禁止。git 管理外
data/raw/market/refill/     補充分（提供元から取り直した足）。<refill_id>/ ごとに一度書いたら変えない。
                            <refill_id>/refill_manifest.json と validation.json は git 管理。
                            補充した足のファイル・tick の保管場所 _ticks/・作業ディレクトリ _work/ は git 管理外
data/raw/oanda_mt5_probe/<試験の日付>/
                            MT5 API の試験・時刻の調査・空白の測定の結果（D12-25）。git 管理外
data/raw/oanda_mt5/<取得日>/<通貨>/
                            OANDA MT5 API の 1 分足の全期間の取得（D12-79。gzip の CSV
                            <通貨>_<YYYY-MM>_m1.csv.gz）。一度書いたら変えない。git 管理外
data/raw/oanda_mt5/<取得日>/manifest.json・fetch_log.jsonl
                            取得記録（価格を含まない）。原本は git 管理外。manifest の写しを下の docs/data に置く
docs/data/oanda_mt5_full_fetch_<取得日>/manifest.json
                            取得記録の manifest の写し（git 管理。ファイル名・SHA-256・行数・最初と最後の足の時刻・
                            状態だけで価格を含まない）。受入れは原本と写しのハッシュの一致を確かめる（D03 §15.3）
docs/design/time_rules/time_rule_table_v<版>.yaml
                            期間別の時刻の規則の表（D12-47。git 管理。価格を含まない）。版ごとに別ファイルで、
                            一度承認した版は変えない
docs/design/time_rules/approved_time_rule_tables.yaml
                            承認済みの表の一覧（識別・版・SHA-256・承認の決定）。run はここにある表で作った
                            snapshot だけを受け付ける（D12-48、D06 §18.9）
docs/design/counting_rules/counting_rules_v<版>.yaml
                            数え方の規則（D03 §15.12。git 管理。価格を含まない）。集計の道具と実装が読む
data/raw/oanda_mt5_sealed/<取得日>/<通貨>/
                            2024 年以降の封印の取得（制約の改定文 ADR-0037。一括取得 <通貨>_<YYYY-MM>_m1.csv.gz、
                            定期取得 <通貨>_<窓の始まり>-<窓の終わり>_m1.csv.gz）。一度書いたら変えない。git 管理外。
                            研究期間の置き場と分け、研究期間の道具が 2024 年以降のファイルを拾わないようにする
                            （PR #109 の仮置き S2b-6）。読むのは関所の品質確認の許可の下の受入れだけ（ADR-0036）
data/raw/oanda_mt5_sealed/<取得日>/manifest.json・fetch_log.jsonl
                            封印の取得記録（価格を含まない）。原本は git 管理外。写しを docs/data/sealed/ に置く
docs/data/sealed/<取得日>_<bulk|periodic-n>/
                            封印の取り込みの記録（git 管理。取得記録の写し、snapshot の manifest と検査報告、一覧。
                            件数・時刻・ハッシュだけで価格を含まない。D03 §16.3.6、D15 の仮置き 11）
configs/acquisition/oanda_mt5_m1_sealed_window.yaml
                            封印の取得の許可の窓（ADR-0037 第4項。git 管理）
configs/quality_checks/<道具の名前>.yaml
                            品質確認の許可の定義（ADR-0036 第4項。git 管理。人間が merge で承認する）
docs/design/time_rules/time_rule_table_sealed_v<版>.yaml
                            2024 年以降の時刻の規則の表（識別 oanda_mt5_time_rules_sealed。D03 §16.2。git 管理）。
                            版 1 の SHA-256 は f4052b591b663042e0a20e205978a3d55171be6c90e1339f8e12de661daa03e3
docs/design/time_rules/approved_time_rule_tables_sealed.yaml
                            2024 年以降の表の承認済みの一覧（git 管理。研究期間の一覧は研究ポリシー 第 3 版の前提なので
                            変えない。PR #111 の要決定 C (a)）
docs/design/counting_rules/counting_rules_sealed_v<版>.yaml
                            封印の取り込み用の数え方の規則（D03 §16.2。git 管理）
data/snapshots/<snapshot_id>/ 受入れ後の正規化データ。実体（Parquet）は git 管理外。
                            manifest.json・integrity_report.json・access_log.jsonl の3ファイルは git 管理
                            （確定段階で再実行した snapshot は integrity_report_provisional.json も git 管理）
data/snapshots/_pending/    暫定 snapshot。すべて git 管理外
runs/                       実行結果。git 管理外
```

## 影響

- `.gitignore` を書き換え、snapshot の `manifest.json` と `access_log.jsonl` を再包含する規則を入れる（段階−1 で manifest.json のみ実装済み。`access_log.jsonl` の再包含は D01 v2.1 の F5c と同じ骨格 PR で追加する）。

## 改訂履歴

| 日付 | 内容 |
|---|---|
| 2026-09-18 | 初版承認 |
| 2026-09-20 | ADR-0014 の消費遷移直列化に伴い、`access_log.jsonl` を git 管理対象に追加 |
| 2026-09-20 | D03 v1.7（2026-09-20 の決定。版番号は 2026-09-23 の main 追随で繰り下げ）に伴い、確定 snapshot の `integrity_report.json` を git 管理対象に追加。検査報告は manifest のダイジェスト対象であり承認・読み取り関門が必要とするため、追跡しなければ別クローンで Parquet を復元しても snapshot を検証できない。報告には価格や封印期間の統計値を含めず、検査種別・系列・区間・重大度・構造的な詳細だけを保存する。確定段階で 5〜7 を再実行した snapshot は、人間が分類の根拠にした暫定報告 `integrity_report_provisional.json` も追跡する（再実行で消えた警告に対する分類の根拠）。`_pending/` 配下は引き続き git 管理外 |
| 2026-10-01 | D03 v1.15（2026-10-01 承認、PR #56）§14.11・§14.20 の2 に伴い、`data/raw/market/` の下に補充分のディレクトリ `refill/` を置く。原データの 20 ファイルは上書き禁止のまま、補充分（`refill/<refill_id>/`）は一度書いたら変えない。補充の manifest `refill_manifest.json` と検証記録 `validation.json` を git 管理にする（別のクローンで実体を取り直したときに照合できるようにするため。snapshot の manifest と同じ理由）。補充した足のファイル・tick の保管場所（`_ticks/`）・作業ディレクトリ（`_work/`）は git 管理外。`.gitignore` の再包含の規則は再取得ツールの実装 PR で入れる |
| 2026-10-17（改訂案） | 研究用データの正本を OANDA MT5 API の 1 分足とした決定（D12-32）と、全期間の取得の計画の承認（D12-79。人間の第 18 回の原文「data/raw/oanda_mt5/<取得日>/<通貨>/（推奨）。データ配置の決定記録（ADR-0013）の改訂が要る」）、時刻の規則の表を版付きの 1 ファイルにする決定（D12-47 (a)）と承認済みのハッシュだけを受け付ける決定（D12-48 (a)）に伴い、次を置き場に加える。(1) `data/raw/oanda_mt5_probe/<試験の日付>/`（D12-25。git 管理外）。(2) `data/raw/oanda_mt5/<取得日>/<通貨>/`（gzip の CSV。一度書いたら変えない。git 管理外。既存の `.gitignore` の `data/raw/*` で除外済みで、規則の追加は要らない）と、その直下の取得記録 `manifest.json`・`fetch_log.jsonl`（git 管理外）。(3) 取得記録の manifest の写し `docs/data/oanda_mt5_full_fetch_<取得日>/manifest.json`（git 管理。人間の第 18 回の回答「入れる。取得記録 K-17 の別 PR で docs 配下に置く」（D12 付録 K-17.1）。PR #82 で置いた）。(4) 時刻の規則の表 `docs/design/time_rules/time_rule_table_v<版>.yaml` と承認済みの一覧 `docs/design/time_rules/approved_time_rule_tables.yaml`（git 管理。価格を含まないので版管理の中に置く。D12 §11.8.4 の行 11）と、数え方の規則 `docs/design/counting_rules/counting_rules_v<版>.yaml`（git 管理。D03 §15.12）。**表 版 1 の SHA-256 は `70bb369bc606d8402b172672c07d75506951f86b1295c00344661a68247b3ec3`**（承認: D12-48・D12-70）。保存・継続利用の条件は未確認のまま（個人の研究利用、再配布しない。D12-19 (b)・D12-79 の内訳 4）。原データ（`data/raw/` の下）と確定済みの snapshot は変えない |
| 2026-10-29 | 順 6 の封印レーン S4a（D15 §2.2 の S4a の行、D03 §16.5.4、PR #109 の仮置き S2b-6「data/raw/oanda_mt5_sealed/<取得日>/<通貨>/（このまま）」・D03 §16.2.6 の仮置き S2b-5）に伴い、次を置き場に加える。(1) 2024 年以降の封印の原ファイル `data/raw/oanda_mt5_sealed/<取得日>/<通貨>/` とその直下の取得記録（git 管理外。既存の `.gitignore` の `data/raw/*` で除外済み）。(2) 封印の取り込みの記録 `docs/data/sealed/`（git 管理。価格を含まない。置くのは S5a・S5b）。(3) 窓の設定 `configs/acquisition/` と品質確認の許可の定義 `configs/quality_checks/`（git 管理）。(4) 2024 年以降の時刻の規則の表 `docs/design/time_rules/time_rule_table_sealed_v1.yaml`（**SHA-256 `f4052b591b663042e0a20e205978a3d55171be6c90e1339f8e12de661daa03e3`**。2024 年以降の表の承認済みの一覧 `approved_time_rule_tables_sealed.yaml` に行を置いた。研究期間の一覧は研究ポリシー 第 3 版の前提のファイルなので変えない）と封印用の数え方の規則 `docs/design/counting_rules/counting_rules_sealed_v1.yaml`。2024 年以降のカレンダー `configs/calendars/fx_ny17_oanda_v2.yaml` の **SHA-256 は `6af95a8d0b26f9b68d959f2b70e811eacb436ea92651df95912ca91a78924a7f`**、年末年始の休場の定義 `tools/survey/oanda_holiday_exceptions_sealed_2026-10-29.json` は `74eb57211482574cb6702e604680576f7635d20eb2a76d3241bfb896615793fc`（どちらも数え方の規則に記録）。表とカレンダーは 2026-10-29 に人間が承認した（AskUserQuestion。PR #111 の承認の対象 項目 1・2「承認する（推奨）」。原文は D15 §0.5.7）。2024 年以降の表の承認済みの一覧を別のファイルにしたのは PR #111 の要決定 C「(a) 別の一覧ファイルにする（推奨）」による。研究期間の置き場・表 版 1・カレンダー 版 1 は変えない |

- 検査報告の追跡（2026-09-20 改訂）に伴う `.gitignore` の再包含規則（`!data/snapshots/*/integrity_report.json` と `!data/snapshots/*/integrity_report_provisional.json`）と `tests/architecture/test_gitignore_layout.py` の更新は、D03 v1.7 の実装 PR（分類形式の変更と同じ PR）で行う。それまでは検査報告は追跡されない（既存の確定 snapshot は存在しない）。
- DVC / LFS は初版では使わない。
