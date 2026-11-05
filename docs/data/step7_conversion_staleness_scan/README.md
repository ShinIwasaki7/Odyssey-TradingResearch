# 研究ポリシー第 4 版の事前確認: 換算の古さとずれの集計（D18 §4.2、決定 37）

円以外の決済通貨のペア（GBPUSD・AUDUSD・USDCHF。道具の照合用に EURUSD）を研究期間で動かしたとき、建玉を持っていれば換算の上限（古さ・ずれとも 30 分。D06 v1.23 §19.5、決定 36）で run が止まりうる時点があるかを、**戦略を走らせず、価格を読まずに**数えた記録です。順 8 の研究の試行で初めて換算の停止を知ることを避けるための確認で、G1〜G5 のどれの条件でもありません（D18 §4.2）。

書いてあるのは件数・時刻・長さ（分）・ハッシュ・処理時間だけです。価格・換算の率・戦略の成績は読んでいないし、書いていません。2024 年以降のデータと封印・隔離の partition は開いていません。機械が読む正本は `scan.json` です。

## 0. 結論

- **8 通貨とも上限内**（集計の対象の円以外の 4 通貨 × 2 組のすべてで、エンジンと同じ判断時点（集合 (1)）に、古さもずれも 30 分を超える処理点は 0 件）。止まる条件 (a)（D18 §4.2）は当たらない。円のペア 4 通貨は換算が率 1（恒等）で古さとずれが生じない（D06 §19.3）ので数えていない。
- 参考の上限側の集合（集合 (2)。営業時間のすべての分の境目）でも 30 分超は 0 件。止まる条件 (c) も当たらない。
- 古さの最大は GBPUSD の 20 分（2021-07-18 の日曜の週の始まり）。15 分を超えたのは、どれも日曜の週の始まり（2020-10-25、2021-07-18、2021-09-26）。USDCHF の 2 本の脚のずれの最大は 14 分。
- 道具の照合（EURUSD）: 15 分を超える区間の日付と長さが、機構確認の記録（`docs/data/step7_mechanism_check/README.md` 第 8.7 節。両方の組とも 3 区間、2020-10-25・2021-07-18・2021-09-26、17〜19 分）と一致し、30 分超は 0（同 第 9 節）。両方の組とも **合格**（`scan.json` の `crosscheck_eurusd`）。

## 1. 測り方（D18 §4.2）

| 項目 | 内容 |
|---|---|
| 道具 | `tools/survey/conversion_staleness_scan.py`（SHA-256 `d466afbacb90847d77f9720e2acf774c6bca86c868b71f8753a945e1b54dba19`） |
| 実行したコミット | `739f17b090748a8ff56c28e364da4220ba7c1336`（PR #147。`src`・`tools`・`configs` に未コミットの変更なし） |
| 実行の時刻 | 2026-11-05T13:10:52Z 開始、877 秒（4 プロセス。1 通貨 × 1 組あたり 395〜476 秒。最大メモリ 約 1.3 GB） |
| 取引カレンダー | `configs/calendars/fx_ny17_oanda_v1.yaml`（`fx_ny17_oanda` 版 1） |
| 時間足の定義 | `configs/calendars/timeframes_v2.yaml` |
| 上限 | 30 分（上限ちょうどは許す）。参考に 15 分も数えた |
| 数え方 | 本番の関数を呼んだ（計算を道具の側で別に書いていない）: 経路は `resolve_route`、脚の観測は `last_minute_index`、古さは `observation_staleness`、ずれは `legs_skew`（どれも `backtest.portfolio.conversion`。エンジンの `session_staleness`・`session_skew` が同じ関数を呼ぶ）。休場の時間と、取引の通貨ペアの 1 分足に記録の無い分を除く（規則 9。ずれも同じ） |
| 集合 (1) | エンジンと同じ判断時点: 公開イベントの時刻（公開フィードと同じ `scheduled_boundary_intervals`・`bar_event_times`。15 分足・1 時間足・4 時間足・日足）、執行足の途中の再開の分（`recorded_resumptions`・`mid_bar_resumption_minutes`）、判断時点の列挙（エンジンの `decision_times`）。建玉の有無によらず数えた |
| 集合 (2) | 営業時間の中のすべての分の境目（セッションの始まりと終わりを含む）と run の終わり。8 組とも集合 (1) を見落としなく覆う（`set1_not_in_set2` が 0 件） |
| 区間 | 執行の snapshot の 15 分足の研究履歴の partition の区間（8 組とも 2017-01-01T22:00Z〜2023-12-29T22:00Z） |
| 読んだ列 | 15 分足・1 時間足・4 時間足・日足の `bar_start`・`bar_end`・`first_observed_at`、取引の通貨ペアと USDJPY の 1 分足の `bar_start` だけ（`ParquetSnapshotStore.read_partition_times`。価格の列は開く前に拒否する） |
| 観測なし | run の始まりの直後で、換算の脚の 1 分足がまだ 1 本も終わっていない点（集合 (1) で 1 点、集合 (2) で 2 点）。古さを数えずに件数だけ記録した |

## 2. 件数と最大値

| 通貨 | 組 | 集合 | 処理点の数 | 観測なし | 古さの最大（分） | 30 分超の点 | 30 分超の区間 | 15 分超の点 | 15 分超の区間 | ずれの最大（分） | ずれ 30 分超の点 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| EURUSD | standard | (1) | 180,164 | 1 | 19 | 0 | 0 | 3 | 3 | ― | ― |
| EURUSD | standard | (2) | 2,615,369 | 2 | 19 | 0 | 0 | 10 | 3 | ― | ― |
| EURUSD | candidate | (1) | 180,158 | 1 | 19 | 0 | 0 | 3 | 3 | ― | ― |
| EURUSD | candidate | (2) | 2,615,369 | 2 | 19 | 0 | 0 | 10 | 3 | ― | ― |
| GBPUSD | standard | (1) | 181,058 | 1 | 20 | 0 | 0 | 4 | 2 | ― | ― |
| GBPUSD | standard | (2) | 2,615,369 | 2 | 20 | 0 | 0 | 12 | 2 | ― | ― |
| GBPUSD | candidate | (1) | 181,053 | 1 | 20 | 0 | 0 | 4 | 2 | ― | ― |
| GBPUSD | candidate | (2) | 2,615,369 | 2 | 20 | 0 | 0 | 12 | 2 | ― | ― |
| AUDUSD | standard | (1) | 181,068 | 1 | 15 | 0 | 0 | 0 | 0 | ― | ― |
| AUDUSD | standard | (2) | 2,615,369 | 2 | 19 | 0 | 0 | 5 | 1 | ― | ― |
| AUDUSD | candidate | (1) | 181,062 | 1 | 15 | 0 | 0 | 0 | 0 | ― | ― |
| AUDUSD | candidate | (2) | 2,615,369 | 2 | 19 | 0 | 0 | 5 | 1 | ― | ― |
| USDCHF | standard | (1) | 183,449 | 1 | 15 | 0 | 0 | 0 | 0 | 14 | 0 |
| USDCHF | standard | (2) | 2,615,369 | 2 | 15 | 0 | 0 | 0 | 0 | 14 | 0 |
| USDCHF | candidate | (1) | 183,440 | 1 | 15 | 0 | 0 | 0 | 0 | 14 | 0 |
| USDCHF | candidate | (2) | 2,615,369 | 2 | 15 | 0 | 0 | 0 | 0 | 14 | 0 |


- 「ずれ」は 2 本の脚の経路（USDCHF の逆数と USDJPY）だけにある（D06 §19.3）。米ドル建ての 3 通貨は USDJPY の 1 本なので「―」。
- 区間は、処理点の並びで隣り合う超過の点を 1 つにまとめたもの。

## 3. 古さの分布（集合 (1)。処理点の数、分）

| 通貨 | 組 | 0 | 1 | 2〜5 | 6〜10 | 11〜15 | 16〜20 | 21〜30 | 31 以上 |
|---|---|---|---|---|---|---|---|---|---|
| EURUSD | standard | 5,328 | 174,235 | 568 | 27 | 2 | 3 | 0 | 0 |
| EURUSD | candidate | 5,339 | 174,218 | 565 | 30 | 2 | 3 | 0 | 0 |
| GBPUSD | standard | 6,345 | 174,124 | 551 | 31 | 2 | 4 | 0 | 0 |
| GBPUSD | candidate | 6,356 | 174,095 | 561 | 34 | 2 | 4 | 0 | 0 |
| AUDUSD | standard | 6,356 | 174,117 | 564 | 24 | 6 | 0 | 0 | 0 |
| AUDUSD | candidate | 6,367 | 174,091 | 570 | 27 | 6 | 0 | 0 | 0 |
| USDCHF | standard | 0 | 182,842 | 571 | 28 | 7 | 0 | 0 | 0 |
| USDCHF | candidate | 0 | 182,830 | 571 | 31 | 7 | 0 | 0 | 0 |


- 古さ 0 分の点は、取引の通貨ペアの無記録の直後（再開の分など）で、USDJPY の観測の後の分に取引の通貨ペアの記録が無く、数える分が無い点（規則 9 の除き方）。USDCHF は 1 本目の脚が取引の通貨ペア自身で、観測した分そのものが記録のある分なので 0 分の点が無い。
- 集合 (2) の分布は `scan.json` の `set2_reference_points.staleness.distribution_minutes`。

## 4. 15 分を超えた区間（時刻と長さだけ。30 分を超えた区間は無い）

| 通貨 | 組 | 集合 | 始まり（UTC） | 終わり（UTC） | 処理点 | 最大の古さ（分） |
|---|---|---|---|---|---|---|
| EURUSD | standard | (1) | 2020-10-25T22:00:00Z | 2020-10-25T22:00:00Z | 1 | 19 |
| EURUSD | standard | (1) | 2021-07-18T21:38:00Z | 2021-07-18T21:38:00Z | 1 | 17 |
| EURUSD | standard | (1) | 2021-09-26T21:45:00Z | 2021-09-26T21:45:00Z | 1 | 17 |
| EURUSD | standard | (2) | 2020-10-25T21:57:00Z | 2020-10-25T22:00:00Z | 4 | 19 |
| EURUSD | standard | (2) | 2021-07-18T21:35:00Z | 2021-07-18T21:38:00Z | 4 | 17 |
| EURUSD | standard | (2) | 2021-09-26T21:44:00Z | 2021-09-26T21:45:00Z | 2 | 17 |
| EURUSD | candidate | (1) | 2020-10-25T23:00:00Z | 2020-10-25T23:00:00Z | 1 | 19 |
| EURUSD | candidate | (1) | 2021-07-18T21:38:00Z | 2021-07-18T21:38:00Z | 1 | 17 |
| EURUSD | candidate | (1) | 2021-09-26T21:45:00Z | 2021-09-26T21:45:00Z | 1 | 17 |
| EURUSD | candidate | (2) | 2020-10-25T22:57:00Z | 2020-10-25T23:00:00Z | 4 | 19 |
| EURUSD | candidate | (2) | 2021-07-18T21:35:00Z | 2021-07-18T21:38:00Z | 4 | 17 |
| EURUSD | candidate | (2) | 2021-09-26T21:44:00Z | 2021-09-26T21:45:00Z | 2 | 17 |
| GBPUSD | standard | (1) | 2020-10-25T22:00:00Z | 2020-10-25T22:00:00Z | 1 | 18 |
| GBPUSD | standard | (1) | 2021-07-18T21:30:00Z | 2021-07-18T21:38:00Z | 3 | 20 |
| GBPUSD | standard | (2) | 2020-10-25T21:58:00Z | 2020-10-25T22:00:00Z | 3 | 18 |
| GBPUSD | standard | (2) | 2021-07-18T21:30:00Z | 2021-07-18T21:38:00Z | 9 | 20 |
| GBPUSD | candidate | (1) | 2020-10-25T23:00:00Z | 2020-10-25T23:00:00Z | 1 | 18 |
| GBPUSD | candidate | (1) | 2021-07-18T21:30:00Z | 2021-07-18T21:38:00Z | 3 | 20 |
| GBPUSD | candidate | (2) | 2020-10-25T22:58:00Z | 2020-10-25T23:00:00Z | 3 | 18 |
| GBPUSD | candidate | (2) | 2021-07-18T21:30:00Z | 2021-07-18T21:38:00Z | 9 | 20 |
| AUDUSD | standard | (2) | 2021-07-18T21:34:00Z | 2021-07-18T21:38:00Z | 5 | 19 |
| AUDUSD | candidate | (2) | 2021-07-18T21:34:00Z | 2021-07-18T21:38:00Z | 5 | 19 |


## 5. 入力（ハッシュ）

| 通貨 | 組 | 執行の snapshot | 換算の snapshot | 読んだ partition の Parquet の SHA-256（先頭 12 桁） | 処理時間（秒） |
|---|---|---|---|---|---|
| EURUSD | standard | `4e6eb8d40df0…` | `ecaaf53fcded…` | `EURUSD_15m_bid/RESEARCH_HISTORY` `7b21d7750a1e…`<br>`EURUSD_1d_ny17_bid/RESEARCH_HISTORY` `7b3bd60e4d2e…`<br>`EURUSD_1h_bid/RESEARCH_HISTORY` `ba23a5856bc5…`<br>`EURUSD_4h_ny17_bid/RESEARCH_HISTORY` `75472e4d4748…`<br>`EURUSD_1m_bid/RESEARCH_HISTORY` `4380a6cf9577…`<br>`ecaaf53f…/USDJPY_1m_bid/RESEARCH_HISTORY` `ae124da45151…` | 397.4 |
| EURUSD | candidate | `9c03bc1283e5…` | `f5214ed8b5e4…` | `EURUSD_15m_bid/RESEARCH_HISTORY` `6705f75920a8…`<br>`EURUSD_1d_ny17_bid/RESEARCH_HISTORY` `e46f55f5eb4a…`<br>`EURUSD_1h_bid/RESEARCH_HISTORY` `e36242db5343…`<br>`EURUSD_4h_ny17_bid/RESEARCH_HISTORY` `6e48136db5f0…`<br>`EURUSD_1m_bid/RESEARCH_HISTORY` `869af50b769a…`<br>`f5214ed8…/USDJPY_1m_bid/RESEARCH_HISTORY` `0aff1e2f6d27…` | 396.8 |
| GBPUSD | standard | `b9ca3a2142bf…` | `ecaaf53fcded…` | `GBPUSD_15m_bid/RESEARCH_HISTORY` `e11796e0fde9…`<br>`GBPUSD_1d_ny17_bid/RESEARCH_HISTORY` `9e84ee715328…`<br>`GBPUSD_1h_bid/RESEARCH_HISTORY` `ede7a464fd1a…`<br>`GBPUSD_4h_ny17_bid/RESEARCH_HISTORY` `45c3f5391d13…`<br>`GBPUSD_1m_bid/RESEARCH_HISTORY` `a06902cc3514…`<br>`ecaaf53f…/USDJPY_1m_bid/RESEARCH_HISTORY` `ae124da45151…` | 397.7 |
| GBPUSD | candidate | `35bcd732a0d6…` | `f5214ed8b5e4…` | `GBPUSD_15m_bid/RESEARCH_HISTORY` `9061cd46bec3…`<br>`GBPUSD_1d_ny17_bid/RESEARCH_HISTORY` `bb057d823445…`<br>`GBPUSD_1h_bid/RESEARCH_HISTORY` `d6531d045246…`<br>`GBPUSD_4h_ny17_bid/RESEARCH_HISTORY` `c2bf955ed136…`<br>`GBPUSD_1m_bid/RESEARCH_HISTORY` `e4ee442b8e49…`<br>`f5214ed8…/USDJPY_1m_bid/RESEARCH_HISTORY` `0aff1e2f6d27…` | 395.3 |
| AUDUSD | standard | `07362e2223e9…` | `ecaaf53fcded…` | `AUDUSD_15m_bid/RESEARCH_HISTORY` `61e48d31d30b…`<br>`AUDUSD_1d_ny17_bid/RESEARCH_HISTORY` `d034fad345ac…`<br>`AUDUSD_1h_bid/RESEARCH_HISTORY` `1d207e5b1bfa…`<br>`AUDUSD_4h_ny17_bid/RESEARCH_HISTORY` `76571918614b…`<br>`AUDUSD_1m_bid/RESEARCH_HISTORY` `8b22eafc0cd1…`<br>`ecaaf53f…/USDJPY_1m_bid/RESEARCH_HISTORY` `ae124da45151…` | 450.2 |
| AUDUSD | candidate | `b9d213ea6fd9…` | `f5214ed8b5e4…` | `AUDUSD_15m_bid/RESEARCH_HISTORY` `c929049c1cce…`<br>`AUDUSD_1d_ny17_bid/RESEARCH_HISTORY` `4e785f033c1a…`<br>`AUDUSD_1h_bid/RESEARCH_HISTORY` `8d9775eedf29…`<br>`AUDUSD_4h_ny17_bid/RESEARCH_HISTORY` `c80647fa1af1…`<br>`AUDUSD_1m_bid/RESEARCH_HISTORY` `bc54f36aed82…`<br>`f5214ed8…/USDJPY_1m_bid/RESEARCH_HISTORY` `0aff1e2f6d27…` | 445.5 |
| USDCHF | standard | `1139abd01c3d…` | `ecaaf53fcded…` | `USDCHF_15m_bid/RESEARCH_HISTORY` `987e13607873…`<br>`USDCHF_1d_ny17_bid/RESEARCH_HISTORY` `8a6a9a9e50c8…`<br>`USDCHF_1h_bid/RESEARCH_HISTORY` `1992e75c52a8…`<br>`USDCHF_4h_ny17_bid/RESEARCH_HISTORY` `c8056246ccd9…`<br>`USDCHF_1m_bid/RESEARCH_HISTORY` `52a6005c9346…`<br>`ecaaf53f…/USDJPY_1m_bid/RESEARCH_HISTORY` `ae124da45151…` | 476.0 |
| USDCHF | candidate | `3d5e44b84807…` | `f5214ed8b5e4…` | `USDCHF_15m_bid/RESEARCH_HISTORY` `496b5232e827…`<br>`USDCHF_1d_ny17_bid/RESEARCH_HISTORY` `e3c649141aaa…`<br>`USDCHF_1h_bid/RESEARCH_HISTORY` `3904d6e7a6a9…`<br>`USDCHF_4h_ny17_bid/RESEARCH_HISTORY` `4ccba428afc8…`<br>`USDCHF_1m_bid/RESEARCH_HISTORY` `bd9cf97da49a…`<br>`f5214ed8…/USDJPY_1m_bid/RESEARCH_HISTORY` `0aff1e2f6d27…` | 476.0 |

- 8 個の執行の snapshot と 2 個の換算の snapshot は、研究ポリシー第 4 版の basis（`configs/policies/research/research_policy_v4.yaml`）の組。完全な識別子と Parquet の SHA-256 は `scan.json` の `results[].inputs`。
- 読む前に、2 つの snapshot が同じ組であることを本番の照合（`app.conversion.require_conversion_pairing`。D06 §19.2）で確かめた。

## 6. 手順の記録

```
uv run --frozen python -m tools.survey.conversion_staleness_scan \
  --snapshots <本体>/data/snapshots \
  --out docs/data/step7_conversion_staleness_scan/scan.json --workers 4
```

`scan.json` の SHA-256: `bec0da80aea121b4cadbbcb36b96aeff90459d931834fa8ea25b21a3890102fe`
