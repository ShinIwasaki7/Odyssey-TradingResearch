# 基盤の受入れと全期間の集計の道具の突き合わせ（実データ。件数・時刻だけ）

2026-10-17 の指示 3-C「実データでは、集計結果と基盤の足生成・能力検査の結果を突き合わせてください」の記録。
**データ上の数え方の一致の確認**であり、エンジンの正常動作や実際の市場での約定の再現性、戦略の成績は扱わない。
価格は出力していない（件数・時刻・ハッシュだけ）。

## 結果

| 通貨 | 比べた取引日 | 一致 | 不一致 | 全分観測 | 部分観測 | 全分無記録 | 取得不足 | 準備のやり直し（窓 60 本） |
|---|---|---|---|---|---|---|---|---|
| USDJPY | 126 | 126 | 0 | 38 | 87 | 1（2022-12-26） | 0 | 1（2022-12-26。比べた範囲の中では整わない） |
| EURUSD | 126 | 126 | 0 | 35 | 90 | 1（2022-12-26） | 0 | 1（同上） |

- 比べた月（API の時刻）: 2018-12〜2019-02、2022-12〜2023-02。時刻の規則の表で変換が候補の間で同じ期間（行 8 の両方冬）だけを
  選んだ（道具の `daily_coverage.csv` は時計ごとの全期間の値なので、その期間なら標準仮説と同じ）。
- 比べ方は両方向: 基盤の取引日の範囲に入る道具の行はすべて出し、片方にしか無い取引日は不一致にする。年末年始の
  休場の日（2018-12-25・2019-01-01。2022 年は休場の宣言なし）はどちらの側にも取引日として現れず、東京サーバーの
  再開の遅れ 10 分（2018-12-26・2019-01-02 の存在すべき分 1,430）は、基盤の `fx_ny17_oanda` 版 1 と、道具の
  「版 2 ＋休場の定義ファイル」で同じ数になった。
- 比べた項目: 取引日の区分、存在すべき分（被覆率の分母）、記録ありの分、無記録の分、取得不足の分、最初の観測の時刻。

## 何と何を比べたか

| 側 | 入口 | 規則の読み方 |
|---|---|---|
| 基盤 | `odyssey_fx.app.mt5.accept_mt5_in_memory`（本番の受入れ。`build_mt5_pending`） | 時刻の規則の表・承認済みの一覧・`fx_ny17_oanda` 版 1・数え方の規則を `load_mt5_rules` で読む（ハッシュを照合） |
| 道具（PR #85） | `tools/survey/full_period_feasibility.py prelim` の `daily_coverage.csv`（`3180d70`） | カレンダー 版 2 と休場の定義ファイルを別々に読み、時計（欧州式・米国式）ごとに数える |

道具と本番で規則を共有する方法（D12 §11.13 の 15）:

1. **同じファイルを読む**: 時刻の規則の表は `app.config.time_rules.load_time_rule_table`、数え方の規則は
   `app.config.counting_rules.load_counting_rules`。道具の入口 `load_time_rule_table`・`load_counting_rules` は
   これを呼べば本番と同じ値になる（PR #85 の最終集計で差し替える）。
2. **同じ関数で数える**: 1 分ごとの区分は `marketdata.domain.minute_status.classify_minutes`、足の区分は
   `marketdata.application.m1_aggregation.classify_bar`、準備のやり直しは `marketdata.domain.readiness.history_resets`。
3. **共通化できない部分は一致を確かめる**: 道具はカレンダー 版 2 と休場の定義ファイルを別々に読む（PR #85 の先行
   統計の形）。基盤は 1 つのカレンダー `fx_ny17_oanda` 版 1 を読む。両者が 1 分単位で同じことは
   `tests/unit/marketdata/test_mt5_rules.py::test_the_oanda_calendar_equals_version_2_minus_the_holiday_definition`
   （研究期間全体、除かれた分 2,500）と、この突き合わせ（取引日ごとの件数）で確かめる。準備の数え方は道具の側が
   まだ無い（PR #85 の `warmup_resets` は設計の確定待ち）ので、基盤の出力 `history_resets.csv` を同じ形で残し、
   PR #85 の最終集計の後に同じ道具（`--feasibility-csv` に最終集計の CSV）で比べる。

## 再現

```
uv run python -m tools.survey.mt5_platform_crosscheck \
  --root <原データの置き場>/data/raw/oanda_mt5/2026-10-16 \
  --feasibility-csv <PR #85 の docs/data/full_period_feasibility/daily_coverage.csv> \
  --feasibility-ref "PR #85 analysis/full-period-feasibility 3180d70 docs/data/full_period_feasibility/daily_coverage.csv" \
  --pairs USDJPY EURUSD --months 2018-12 2019-01 2019-02 2022-12 2023-01 2023-02 \
  --out-dir docs/data/mt5_platform_crosscheck
```

入力・規則のハッシュと対象のコミットは `run_record.json`。取引日ごとの比較は `daily_crosscheck.csv`、準備の
やり直しの箇所は `history_resets.csv`。原データは読むだけで変えていない。
