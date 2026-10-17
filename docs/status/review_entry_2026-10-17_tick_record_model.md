# レビューの入口: 配布 tick 記録を再現するモデル（2026-10-17）

人間の指示（2026-10-17「PR #82 の後続作業」）§4 の「レビュー用の入口となる短い文書」。数字・事実はすべて main（`8d89c65` 時点。実装 PR #87 の merge コミット。集計の merge は `8c7e6eb`）の成果物と PR の本文から引いた。出典は各行に書いた。

- 「配布 tick 記録を再現するモデル」とは、OANDA MT5 API の 1 分足で**記録の無い分に価格を作らない**約束のこと（D03 §15.2 の末尾）。API の 1 分足が配布 tick 記録と一致すると主張するものではない。
- 戦略の成績（`runs/`・評価結果・取引表）は読んでいない。実価格は載せていない（件数・時刻・被覆率・ハッシュだけ）。人工データの価格は「例示値」と書いた。

## 読む順

1. 本書 §1（どこまで終わったか）→ §4（全期間の結果）
2. 最終報告 [`final_report_2026-10-17_tick_record_model.md`](final_report_2026-10-17_tick_record_model.md)（人間向けの結論 5 項目）
3. 本書 §2（決定 → 実装の対応）・§3（変更前後の例）
4. 詳細: 集計の要約 [`docs/data/full_period_feasibility/summary.md`](../data/full_period_feasibility/summary.md)、PR #87 の本文

## 1. 現在地

### 1.1 段階ごとの状態

| 段階 | 状態 | 出典 |
|---|---|---|
| 取得 | **完了**。8 通貨 × 84 か月（2017-01〜2023-12）、672 ファイル、1 分足 20,800,838 本。取得記録の写し SHA-256 `e4664b01…` | `docs/status/progress.md`（2026-10-17 01:35 JST の行）、[`manifest.json`](../data/oanda_mt5_full_fetch_2026-10-16/manifest.json) |
| 設計 | **完了**（PR #84 merge） | D12 §0.1.26〜§0.1.27 |
| データ上の検証（集計） | **完了**（PR #85 merge）。エンジンの動作・約定の再現性は確かめていない | `summary.md` の冒頭 |
| 実装 | **完了**（PR #87 merge `8d89c65`）。人間レビューで見つかった重大な問題 3 件を修正済み（§1.3） | PR #87 本文 |
| 受入検証 | **合格**（人工データ 7 項目 C-1〜C-7、§1.3 の回帰テスト、実データの突き合わせ一致。最終 head `bd0f7ad`） | PR #87 本文「受入検証」「実データの突き合わせ」 |

### 1.2 PR ごとの状態

レビューの「巡」は、別プロセスの Claude が反対の立場から欠陥を探すレビュー（敵対レビュー）の回数。上限（安全弁）は 2026-10-17 から 3 巡（`docs/pr_review_policy.md` §4.4）。

| 作業 | PR | merge コミット | 敵対レビュー | 人間レビュー・判断 |
|---|---|---|---|---|
| A 設計改訂 | #84 | `5348d58` | 8 巡（第 7・8 巡は人間が追加を許可）。最終巡 a84af32 で P0 0・P1 0 | 人間レビュー 0 回。要決定 9 件（D12-90〜98）の回答と、文言 3 か所を直して merge の判断 |
| B 全期間集計 | #85 | `8c7e6eb` | 2 巡（第 2 巡で P0/P1 0） | 人間レビュー 1 回（1fa49f4。2 件指摘 → 1dcdf68 で修正）→ merge 承認 |
| C 実装 | #87 | `8d89c65` | 4 巡（3 巡＋人間が許可した追加 1 巡）。第 4 巡の P1 1 件は人間の決定で修正 | 人間レビュー 1 回（4d8be21。P1 3 件 → bd0f7ad で修正、§1.3）→ merge 承認。設計判断 2 件・仮置き 14 件・表22 の形 2 件を人間が決定（D12-110〜128） |
| D 道具修正 | #83 | `118be70` | 5 巡（第 6 巡は人間の指示で省略） | 人間レビュー 2 回（b434448 で P1 1 件、cd67e91 で検証のうえ merge 指示） |
| 方針 | #86 | `6813e8f` | なし（人間の指示） | merge 承認 1 回 |

出典: 各 PR の本文（`gh pr view`）、scratchpad の人間の決定の原文（`human_decision_2026-10-17_*.md`・`human_review_2026-10-17_*.md`）。

### 1.3 人間レビューで見つかり修正した重大な問題（PR #87）

必ず直す問題（P1）3 件。出典: PR #87 本文「人間レビュー第 1 回（4d8be21）」、D12 §0.1.30.3、人間の指示（2026-10-18）。

| # | 見つかった問題（PR #87 の head `4d8be21`） | 影響 | 対応（修正 head `bd0f7ad`、merge `8d89c65`） | 回帰テスト |
|---|---|---|---|---|
| 1 | 損切りの更新（将来の時点から有効）の後、再開の分で旧い損切り水準の判定が消える | 再開時に損切りを見逃し、損益が誤る | 置き換えられた水準を、新しい水準が有効になるまで判定に使う（執行足の終わり・途中の再開の分・始値の 3 か所。D06 §8.3・§18.4 の順 1・§18.5）。建玉の表には最新の水準を残す | `test_a_future_stop_update_keeps_the_current_level_at_a_mid_bar_resumption`、`test_a_future_stop_update_takes_effect_from_its_bar` |
| 2 | 表22（準備のやり直しの表）の正当な空値（`window_bars` など）で評価が例外終了 | 全分無記録を含む正常な run でも評価が止まる | 評価の主キー検査で空値を空のまま比べる（D06 §18.10、D07 §28.3） | `test_a_run_with_an_all_unrecorded_hour_is_evaluated` |
| 3 | 同じ無記録の足・同じ窓から不足期間が 2 つ生じ、表22 の主キーが重複 | 記録が主キーとして読めない。単純にまとめると読める期間まで不足扱いになる | 人間の決定 D12-128 で記録形式を決め直した（下記）。主キーに `insufficient_from`（準備不足になる時刻）を足し、読める期間は不足にしない | `test_one_no_record_bar_can_make_two_insufficient_periods`（1 時間足 10:00〜11:00 が無記録・3 本窓・末尾 2 本除外 → 11:00〜12:00 と 13:00〜16:00 の 2 行。実行時の判定と一致） |

**表22 の行の分け方の決定（D12-128、2026-10-18。原文 D12 §0.1.30.3）**: 「(a) 不足期間 1 つに 1 行。主キーに「不足になる時刻」を加える（推奨）」。反映状況: D06 §18.10 の表22 の主キーを `(series, bar_key, window_kind, window_bars, exclude_latest_bars, window_duration, insufficient_from)` に改訂。実装（`marketdata/application/read_readiness.py`・評価の主キー検査）とテスト済み。main `8d89c65` に入っている。

**読み方の注意**: 3 件はいずれも、元の受入検証（C-1〜C-7）がすべて合格していた head `4d8be21` で、人間のレビューが見つけた。C-1〜C-7 の合格は、上の 3 つの条件（損切りの将来の更新と再開の組み合わせ、全分無記録を含む run の評価、1 つの足から複数の不足期間）で正しく動くことの確認ではなかった。今はそれぞれに回帰テストがある。

## 2. 決定 → 実装の対応表

決定は D12 §6 の番号。D12-101〜109 は欠番（PR #87 が PR #85 の番号との衝突を避けて 110 から採番。PR #87 本文）。D12-110〜128 は PR #87 で決まり、main `8d89c65` に入っている。実装・テストの場所は main の `src/odyssey_fx/` と `tests/`。結果はすべて「合格」（PR #87 本文の対応表と受入検証の表。最終 head `bd0f7ad`）。

| 決定 | 1 行要約 | 設計の節 | 実装の場所 | テスト |
|---|---|---|---|---|
| D12-90 | 日中の足（15 分・1 時間・4 時間）の全分無記録も日足と同じく準備不足 | D03 §15.7、D06 §18.8 | `marketdata/application/asof.py`、`marketdata/domain/readiness.py` | `test_c2_the_run_records_history_resets_and_keeps_going` |
| D12-91 | 直前の執行足が全分無記録なら、最後に記録のある分の終値を観測時刻付きで評価に使う（約定には使わない） | D06 §18.9 の 5 | `backtest/engine/loop.py` `_seed_last_complete`、`marketdata/domain/bar.py` `close_observed_at` | `test_reference_quote_after_an_all_unrecorded_bar_keeps_the_last_observation`、`test_unrecorded_tail_keeps_the_position_and_completes` |
| D12-92 | 感度の検証は変換の組ごとの snapshot（標準・候補の 2 つ） | D03 §15.9 の 5・6 | `marketdata/domain/time_rule.py`、CLI の `--variant`（`app/cli/main.py`） | `test_variant_conversion_follows_the_table` |
| D12-93 | 年末年始の休場を含むカレンダーは `fx_ny17_oanda` 版 1 | D03 §15.6 の 1 | `configs/calendars/fx_ny17_oanda_v1.yaml`、`app/mt5.py` `load_mt5_rules`、`marketdata/domain/counting_rules.py` | `test_the_oanda_calendar_equals_version_2_minus_the_holiday_definition`、`test_load_mt5_rules_reads_and_cross_checks_the_files` |
| D12-94 | 標準の組で休場帯に入る 1 分足は `UNEXPECTED_BAR`（休場帯の足）とし、人間の分類まで確定しない | D03 §15.3 の 4 | `marketdata/application/mt5_acceptance.py`（`_build_series`・`reaccept_mt5`） | `test_out_of_session_bars_in_standard_need_classification` |
| D12-95 | 執行足の途中の再開で約定した後の処理は再開の分で行う | D06 §18.4 | `backtest/engine/loop.py` | `test_c3_companion_a_pending_order_fills_at_the_first_recorded_minute_before_expiry` |
| D12-96 | 準備のやり直し期間も採点に含め、期間と回数を表示 | D11 §12.2、D07 §28.2 | 集計: `tools/survey/full_period_final.py`（`research_daily_bars_while_rewarming` 列）。評価: `evaluation/application/evaluate_run.py` `_history_reset_minutes`。長期間の評価（D11 §12）の実装は範囲外 | `test_spans_are_clipped_to_the_run_and_overlaps_are_counted_once`、`test_whole_minutes_are_rounded_down` |
| D12-97 | 候補の組でカレンダーの外に出る足は使わず件数を報告 | D03 §15.6 の 2 | `marketdata/application/mt5_acceptance.py` | `test_candidate_variant_excludes_out_of_calendar_bars_and_reports_them` |
| D12-98 | 版 2 の 2017-12-25・2018-01-01 の休場を引き継ぐ（推奨でない (a)） | D03 §15.6 の 1 (iv) | `configs/calendars/fx_ny17_oanda_v1.yaml` | `test_the_oanda_calendar_equals_version_2_minus_the_holiday_definition` |
| D12-99 | 集計の休場帯の足はセッション外のデータ異常として使わず確定 | D12 §11.7.10 | 集計: `tools/survey/full_period_final.py`。基盤: 分類 `OUT_OF_SESSION_DATA` の後に `reaccept_mt5` が除外 | `test_out_of_session_bars_in_standard_need_classification` |
| D12-100 | 集計の読み方 6 件（準備は日足 60 本だけ、やり直しは予定の公開時刻から、など） | D12 §0.1.29 | `tools/survey/full_period_final.py` | `tests/unit/test_full_period_final.py`（22 件） |
| D12-110 | 足境界の検査は、端の分が無記録でも親の区間に収まり分の境目に揃えば合格 | D06 §18.2 | `backtest/execution/protection_hits.py` `hierarchy_checks`（引数 `excused`）、`app/mt5_run.py` `_excused` | `test_an_unrecorded_first_minute_passes_the_boundary_check_on_the_mt5_route` |
| D12-111 | 1 分足は戦略が宣言したときだけ公開 | D06 §18.2・§18.10 | `app/mt5_run.py`（`Mt5RunParts.internal_series`） | `test_minute_series_is_published_only_when_the_strategy_declares_it` |
| D12-112 | 経過時間の窓だけで読む系列も表22・run manifest・集計に載せる | D06 §18.10 | `app/mt5_run.py` `_history_reset_rows`、`marketdata/application/read_readiness.py` | `test_history_resets_cover_duration_window_reads_in_the_run` |
| D12-113 | 足が作られなかった端も、取得済みで無記録かつ利用許可の期間なら run に含める（取得範囲と閲覧許可範囲を区別） | D03 §15.6 | `marketdata/application/publication.py` `_extended_by_unrecorded_ends`、`app/composition.py` | `test_the_run_may_cover_an_unrecorded_tail_but_not_a_shortfall`、`test_the_unrecorded_tail_is_cut_at_the_permitted_period` |
| D12-114 | run 開始直前の執行足が全分無記録なら D12-91 を最初の判断時点にも当てる | D06 §18.9 | `backtest/engine/loop.py` `_seed_last_complete` | `test_the_last_execution_bar_is_not_carried_over_a_closure` |
| D12-115 | 執行足の途中の再開は、再開の分の判断時点で処理（建玉だけでも判断時点を作る） | D06 §18.5 | `backtest/engine/loop.py`（`_add_mid_bar_resumptions`・`_phase_mid_bar_resumption`） | `test_c4_a_mid_bar_resumption_below_the_stop_closes_at_the_gapped_open`、`test_a_hit_before_a_mid_bar_resumption_is_processed_at_the_resumption` |
| D12-116 | 利確のギャップは再開の分だけで当てる | D06 §18.7 | `backtest/engine/loop.py`（`_gap_close`） | `test_take_profit_gap_uses_the_level_not_the_open` |
| D12-117 | 到達の判定方法の印の意味を固定（同じ分で両方 → 損切り優先、別の分 → 下位足で解決、片側 → 片側到達） | D06 §18.6 | `backtest/engine/loop.py`（`_scan_minutes`） | `test_c5_both_levels_in_one_minute_after_resumption_take_the_stop`、`test_levels_reached_in_different_minutes_are_resolved_by_the_minute_bars`、`test_a_hit_before_a_mid_bar_resumption_is_processed_at_the_resumption` |
| D12-118 | 期限切れが無記録の中で起きたかは期限の直前の分で判定 | D06 §18.10 | `backtest/engine/loop.py`（`_phase_order_expiry`・`expired_in_unrecorded_count`） | `test_c3_an_order_that_expires_while_unrecorded_never_fills_after_resumption` |
| D12-119 | 表22 は実際の読み取りが準備不足になる条件と記録を一致させる（run 開始前から影響するものを含む） | D06 §18.10 | `marketdata/application/read_readiness.py` `read_resets` | `test_history_resets_match_the_runtime_reads` |
| D12-120 | 評価の集計 3 種。準備期間は run 区間と重なりを取り和集合、整数分は切り捨て | D07 §28.3 | `evaluation/application/evaluate_run.py` `_history_reset_minutes` | `test_spans_are_clipped_to_the_run_and_overlaps_are_counted_once`、`test_whole_minutes_are_rounded_down` |
| D12-121 | レポートの節番号（データの被覆 6、設定と入力の特定 7） | D07 §28.3 | `evaluation/adapters/report.py` | `tests/integration/app/test_experiment_report.py`（見出し「## 6. データの被覆」） |
| D12-122 | 部分観測の足の要約（件数・最小被覆率・4 刻み） | D03 §15.6 | `marketdata/application/m1_aggregation.py` `_partial_summary` | `test_c1_partial_daily_bar_is_built_with_coverage_and_survives_the_partition` |
| D12-123 | 候補の組の「週」は NY 暦の日曜〜土曜（集計と同じ） | D03 §15.6 | `marketdata/application/mt5_acceptance.py` `_week_of` | `test_candidate_variant_excludes_out_of_calendar_bars_and_reports_them` |
| D12-124 | MT5 経路の列対応の宣言は `app/config` 配下で読む | D03 §15.3 | `app/config/mt5_datasource.py` `load_mt5_datasource`、`configs/datasources/oanda_mt5_m1_v1.yaml` | 専用のテスト名は PR #87 のテストに見当たらない |
| D12-125 | 受入れの CLI `data accept-mt5` は使った月・変換の組・規則の版・入力ハッシュ・分類を成果物に残す | D03 §15.3 | `app/cli/main.py`、`marketdata/domain/snapshot.py`、`mt5_acceptance.py` `months_of` | `test_the_manifest_records_the_months_variant_rules_and_input_hashes` |
| D12-126 | 区分の印ごとの日数は「実行通貨・run 区間の UTC 暦日数」と明示 | D06 §18.9 | `app/mt5_run.py` `_mark_days`、`backtest/trace/manifest.py`、`evaluation/adapters/report.py` | `test_mark_days_are_the_execution_symbol_utc_calendar_days` |
| D12-127 | 表22 の窓を種類・本数・末尾から除く本数・経過時間で区別し主キーも対応。経過時間は本数に換算しない | D06 §18.10、D07 §28.3 | `marketdata/domain/readiness.py` `ReadWindowSpec`、`read_readiness.py`、`backtest/trace/recorder.py` | `test_history_resets_match_the_runtime_reads`、`test_duration_window_reads_are_checked_for_shortfall` |
| D12-128 | 同じ無記録の足・同じ窓から不足期間が 2 つ以上生じるときは不足期間 1 つに 1 行。表22 の主キーに `insufficient_from` を加える | D06 §18.10（表22） | `marketdata/application/read_readiness.py` `read_resets`、評価の主キー検査 | `test_one_no_record_bar_can_make_two_insufficient_periods` |

テストの置き場: `tests/acceptance/test_tick_record_model.py`・`tests/acceptance/test_tick_record_model_marketdata.py`・`tests/unit/marketdata/test_mt5_rules.py`・`tests/unit/marketdata/test_mt5_acceptance.py`・`tests/unit/backtest/test_mt5_hierarchy_checks.py`・`tests/unit/evaluation/test_history_reset_minutes.py`（PR #87）、`tests/unit/test_full_period_final.py`（main）。規則 → 実装の全 15 行は D12 §11.13 と PR #87 本文の対応表。

## 3. 変更前後の例（人工データ）

「旧」は HistData 経路の規則（D06 §18.1・D03 §15.1 の表。HistData 経路では今も有効）。人工の場面は PR #87 のテストの docstring から引いた。**価格はすべて例示値（人工）**。

| C | 入力の形 | 旧（HistData 経路） | 新（MT5 経路） |
|---|---|---|---|
| C-1 部分観測の足 | 取引日 2026-01-08 に 10:00〜10:07Z と 11:00〜13:00Z が無記録（計 127 分） | 不完全足は作らない（D03 §5.2） | 日足を作り `expected_minutes` 1440・`recorded_minutes` 1313 を持つ。「全分観測」と呼ばない。partition・表20 の Parquet・run manifest の `partial_bars` まで残る |
| C-2 休場と全日無記録 | 2026-01-14 を休場と宣言、2026-01-21 の取引日は 1 分足 0 本。日足の窓 3 本 | 存在すべき足の欠落は人間が休場かデータ欠損かに分類（D03 §15.1 の表） | 休場は飛ばして数え準備は途切れない。全日無記録の日足は作らず、その予定の公開時刻 01-21 22:00Z から準備不足、3 本後の公開 01-26 22:00Z に整う |
| C-3 無記録中の期限切れ | 有効期限 20:20 の注文、20:00〜20:24 が無記録 | 予定した候補の足が無いまま過ぎたら `FAILED_DATA_ERROR`（D06 §18.1 の表） | 再開の分 20:25 で期限切れが約定より先に確定し約定しない。表8 の `expired_in_unrecorded` が真、run は `COMPLETED` |
| C-4 執行足の途中の再開 | 建玉の損切り 149.000（例示値）、20:34〜20:37 が無記録、再開の分 20:38 の始値 148.620（例示値） | 始値のギャップの処理は執行足の始値の段だけ（D06 §18.1 の表） | 20:38 の始値で損切り（区分 `STOP_GAP`、滑りを不利に当てて 148.610）。処理点は 20:45 を待たず 20:38（D12-115） |
| C-5 順序不明 | 再開の分 20:36 の 1 分足が利確と損切りの両方に届く | 損切り優先（ADR-0030） | 同じ（`UNRESOLVED_SL_PRIORITY` で損切り）。最小解像度が 1 分足になっただけ |
| C-6 取得不足 | 2026-01 の要求が失敗、前後の月は取得済み | 欠落は人間の分類の対象 | 1 月の取引時間は取得不足（無記録にしない）。run は `FAILED_CAPABILITY` で約定なし。表の承認を取り消した場合も同じく実行不可 |
| C-7 公開時刻 | 取引日 2026-01-29 は 17:59Z が最後の観測 | — | 日足の公開は区間の終わり 22:00Z のまま（早めない）。18:30Z には読めない。執行足の始値の時刻は最初の観測の分（例 10:07Z）、全分無記録の執行足には出さない |

## 4. 全期間の結果（データ上の実行可能性）

出典: [`summary.md`](../data/full_period_feasibility/summary.md) §1〜§5（main `8c7e6eb`）。**これはデータ上の実行可能性で、エンジンの動作や実際の市場での約定の再現性は確かめていない。**

- **研究対象区間（8 通貨とも、標準・候補の組とも同じ）**: 2017-03-24T21:00Z 〜 2023-12-29T22:00Z、日足 1,755 本（うち準備のやり直し中 60 本）。連続区間は 1 つ（開始 `data_start`、終了 `data_end`）。取得不足 0。
- **全分無記録の日**: 取引日 2022-12-26 の 1 日（全通貨）。準備のやり直し 1 回。再び整うのは 2023-03-20T21:00Z。
- **8 通貨の共通区間**（参考）: 上と同じ。取引日 1,756 日のうち全通貨で日足あり 1,755 日。

| 通貨 | 予定の日足 | 全分観測（標準／候補） | 部分観測（標準／候補） | 被覆率の最小 | 値が変わる日足（候補差） | カレンダーの外の足（標準／候補） |
|---|---|---|---|---|---|---|
| USDJPY | 1,816 | 535／535 | 1,280／1,280 | 0.745139 | 41 | 77／645 |
| EURJPY | 1,816 | 663／661 | 1,152／1,154 | 0.746528 | 48 | 79／755 |
| EURUSD | 1,816 | 460／461 | 1,355／1,354 | 0.740278 | 47 | 75／752 |
| GBPJPY | 1,816 | 666／668 | 1,149／1,147 | 0.370139 | 47 | 77／754 |
| USDCHF | 1,816 | 300／301 | 1,515／1,514 | 0.737500 | 63 | 72／746 |
| AUDJPY | 1,816 | 633／635 | 1,182／1,180 | 0.743056 | 45 | 78／755 |
| GBPUSD | 1,816 | 418／419 | 1,397／1,396 | 0.743750 | 51 | 78／754 |
| AUDUSD | 1,816 | 380／383 | 1,435／1,432 | 0.745139 | 58 | 73／749 |

- 各通貨とも、全分無記録 1・取得不足 0。部分観測の被覆率の分布・連続無記録時間の分布・日中の足の無記録の数は `summary.md` §2〜§3。
- 候補差の「値が変わる日足」は観測の時刻だけが違う日足を含む（PR #85 の人間レビューの修正）。研究対象区間の差はなし。
- 全区間を追える CSV（14 本）: [`segments.csv`](../data/full_period_feasibility/segments.csv)・[`research_windows.csv`](../data/full_period_feasibility/research_windows.csv)・[`warmup_resets.csv`](../data/full_period_feasibility/warmup_resets.csv)・[`daily_bars.csv`](../data/full_period_feasibility/daily_bars.csv)・[`bar_counts.csv`](../data/full_period_feasibility/bar_counts.csv)・[`coverage_distribution.csv`](../data/full_period_feasibility/coverage_distribution.csv)・[`no_record_run_distribution.csv`](../data/full_period_feasibility/no_record_run_distribution.csv)・[`long_no_record_runs.csv`](../data/full_period_feasibility/long_no_record_runs.csv)・[`no_record_bars.csv`](../data/full_period_feasibility/no_record_bars.csv)・[`monthly_stats.csv`](../data/full_period_feasibility/monthly_stats.csv)・[`out_of_calendar_bars.csv`](../data/full_period_feasibility/out_of_calendar_bars.csv)・[`candidate_differences.csv`](../data/full_period_feasibility/candidate_differences.csv)・[`common_windows.csv`](../data/full_period_feasibility/common_windows.csv)・[`coverage_by_pair.csv`](../data/full_period_feasibility/coverage_by_pair.csv)。

## 5. 再現手順

原データ `data/raw/oanda_mt5/2026-10-16`（版管理の外。変更しない）を読む。

### 5.1 入力・規則のハッシュ（SHA-256。main で確認）

| 入力 | ファイル | SHA-256 |
|---|---|---|
| 取得記録 manifest（写し） | `docs/data/oanda_mt5_full_fetch_2026-10-16/manifest.json` | `e4664b011600337d619270986e243d11f467f2be425a841b70e5851807daca53` |
| 取得の記録 | `fetch_log.jsonl` | `0868f4bd0840f41b7bda2c3d7a6b37aaee9dba52af424b7b54da7de53088d9fa` |
| 数え方の規則 版 1 | `docs/design/counting_rules/counting_rules_v1.yaml` | `fa98eef8cec035a4564b849bc4098d1488aee968bc3a5c2d44efb3ba75ca6444` |
| 時刻の規則の表 版 1 | `docs/design/time_rules/time_rule_table_v1.yaml` | `70bb369bc606d8402b172672c07d75506951f86b1295c00344661a68247b3ec3` |
| 承認済みの表の一覧 | `docs/design/time_rules/approved_time_rule_tables.yaml` | `2a4eee10144764ac228e102ef56965615463ff60d2abf54d6240d39e0bf0d90b` |
| カレンダー `fx_ny17_oanda` 版 1 | `configs/calendars/fx_ny17_oanda_v1.yaml` | `a9da0d9ae6b3246b5bb67757a7b6cb4ac50e5a012c96a5c4a075bc1148a79b96` |
| 時間足の定義 版 1（集計） | `configs/calendars/timeframes_v1.yaml` | `231d01c542cf5c152303770ed8832003702850380d0f7386467105c774710c81` |
| 時間足の定義 版 2（PR #87。`1m` を追加） | `configs/calendars/timeframes_v2.yaml` | `86f6d16689f7642b6b845e33df662ee2a67c314d623817848c08f0f37a4685b1` |
| 列対応の宣言（PR #87） | `configs/datasources/oanda_mt5_m1_v1.yaml` | `791b9917e073c8c04faa50ef6bed3906335b4d680bce51dbf96434516f895fb8` |

### 5.2 全期間の集計（PR #85。対象コミット `53e40bc`、merge `8c7e6eb`）

```
uv run python -m tools.survey.full_period_final --root data/raw/oanda_mt5/2026-10-16 --out-dir docs/data/full_period_feasibility --decision-file docs/data/full_period_feasibility/human_decision_2026-10-17_pr85_final_aggregation.md --decision-ref 'D12 §0.1.29（D12-99・D12-100、2026-10-17）'
```

出力先 `docs/data/full_period_feasibility/`。入力・出力・道具のハッシュと実行時刻は [`manifest.json`](../data/full_period_feasibility/manifest.json)。人間レビューで、記録したコマンドをそのまま実行して 14 の CSV が同じに出ることを確かめた（PR #85 本文）。

### 5.3 受入検証（main `8d89c65`）

```
uv run pytest tests/acceptance/test_tick_record_model.py tests/acceptance/test_tick_record_model_marketdata.py
```

全体の検査は `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest -q`（PR #87 本文: 最終 head `bd0f7ad` で 3,256 件合格・14 件スキップ）。本書の作成時には再実行していない。

### 5.4 実データの突き合わせ（PR #87。記録の対象コミット `e3ffdb1`）

```
uv run python -m tools.survey.mt5_platform_crosscheck --root <原データの置き場>/2026-10-16 --feasibility-dir docs/data/full_period_feasibility --pairs USDJPY EURUSD --months 2018-12 2019-01 2019-02 2022-12 2023-01 2023-02 --variants standard candidate --out-dir docs/data/mt5_platform_crosscheck
```

- 比べた集計の出力（main と同じハッシュ）: `daily_bars.csv` `661cc7e6…`、`no_record_bars.csv` `c4cd5758…`、`warmup_resets.csv` `84f63b25…`。
- 記録: `docs/data/mt5_platform_crosscheck/run_record.json`（SHA-256 `34d79cc3…`）。対象コミット `e3ffdb1` はコードの最終コミットで、最終 head `bd0f7ad` との差は突き合わせの記録の更新と文書だけ（PR #87 本文）。
- CLI での受入れの例（PR #87 本文）: `odyssey-fx data accept-mt5 --fetch-date 2026-10-16 --symbol USDJPY --months 2019-01`（ERROR 0・WARN 0）。

## 6. 未解決

設計文書・PR 本文・`progress.md` に書かれているものだけ。影響と、必要な判断を添えた。

| 項目 | 何が未定か | 影響 | 出典 |
|---|---|---|---|
| 時刻の規則の表の仮定付きの行 | 表 版 1 の 9 行のうち確認済みは行 3（USDJPY × 2023-03-12〜03-26）だけ。他の 8 行は仮定付き（行 1・2・4・5・6・7 は候補の間で変換が違う） | 該当期間の結果は標準・候補の 2 組で並べる。採否が組の間で割れたら証拠不足（D12-49） | `time_rule_table_v1.yaml`、D11 §12.3 |
| 表の次の版の検討材料 | 2019-10-27 の週に金曜の閉場の後 57〜58 本/通貨（米国式なら営業時間）。表の改訂の要否は人間が次の版で判断 | 行 5 の標準仮説と食い違う観測。表 版 1 は変えていない | D12 §11.7.10、D12-99 |
| D12 の残る要決定 | 品質の閾値（D12-4 の条件）、最長区間が短い場合の判断（D12-13）、対象通貨、提供元の正式採用、公式 tick CSV を主系列にするか（D12-69） | 研究期間と一次候補 | `progress.md`「人間待ち」、D12 §6 |
| PR #80 | 保留（draft）。仮置き 22 と順 4a の事項 1 が未決定 | 2024 年以降を品質確認で読む規則。今回は使わない | D12 §4.4 |
| 2024 年以降 | 取得していない・参照していない | 研究対象は 2023-12-29T22:00Z まで | 人間の指示 §1 |
| 長期間の評価の実装 | D11 §12 は PR #87 の範囲外 | 区間ごとの採点・表示は未実装 | PR #87 本文、D12 §11.13 の注記 |
| D06 の表3・表16 の追加の列 | 未実装（後続の実装 PR） | 本件の受入検証には関わらない | PR #87 本文 |
| 集計の道具と基盤の規則の読込 | 調査の道具 `mt5_gap_aggregate.time_rule` は置き換えず、全暦日一致の回帰テストで代替。main の `full_period_final.py` も独自の `load_time_rule_table` を持つ（同じファイル・ハッシュを読む）。一致は突き合わせ（§5.4）で確認 | 規則のコードが 2 か所にある。PR #87 本文は「PR #85 の最終集計で差し替える」と書くが、main ではまだ差し替わっていない | PR #87 本文、`tools/survey/full_period_final.py` |
| 区分の印の日数の研究上の補足 | D12-126 の「研究上の補足は別途設計」 | 未設計 | D12 §6 の D12-126 |
| PR #85 の見送った軽微な指摘（P2） | `monthly_stats.csv` の列名が先行統計の名前のまま、`--docs-manifest` の記録のパス、`manifest.json` の出力にハッシュが無い、表の印の語彙の外の値を T5 が拒否しない | 集計の値には影響しない | PR #85 本文「残る指摘」 |

D12-124（列対応の宣言の読込）は専用のテスト名が見当たらない（§2）。

人間レビューで見つかった重大な問題 3 件（§1.3）は、修正と回帰テストの追加が済み、未解決には含めない。
