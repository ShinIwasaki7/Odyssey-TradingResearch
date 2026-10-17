# golden テスト

人工データでの固定 trace との突合（全体計画書 §8.4）。
検証戦略 A・B の trace を保存し、変更時に差分を検査する。

## 更新の記録

- 2026-10-17（実装 PR-C、配布 tick 記録を再現するモデル）: 評価の入力表に判断履歴の表20〜22 を足し（D07 §28.3）、`FILL_DIAGNOSTICS` に列 `resumed_after_unrecorded_minutes`・`gap_kind`、集計に `PARTIAL_COVERAGE_PUBLICATION`・`RESUMPTION_FILL`・`UNRECORDED_EVENT` の 3 種を足した。HistData の経路の run でも表の形は同じ（D06 §18.10・D07 §28.3）なので、`category_counts.tsv`（0 件の鍵 8 行）・`fill_diagnostics.tsv`（空の 2 列）・`consistency_checks.tsv`（必須列の検査が読む表の一覧）を更新した。指標・取引・費用の値は変わっていない。
