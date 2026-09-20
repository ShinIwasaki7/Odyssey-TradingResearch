# ADR-0027: 成果物保存形式はファイルシステムに Parquet（表）＋ JSON（manifest）

- 状態: 承認（2026-09-20）
- 決定者: ユーザー（リポジトリ所有者）
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 B-7、[D01](../design/D01_architecture_and_dependency_rules.md) §10.3・§14.2

## 文脈

`runs/<run_id>/` の run manifest・trace・result、および `runs/experiments/<experiment_id>/` の実験成果物をどう保存するかが未決定だった（要決定 B-7）。全体計画書・D01 とも「ファイルシステムに Parquet（表）＋ JSON（manifest）」を推奨案として仮置きしていた。決定時期は段階2（D06・D07 の確定時）としていたが、他の選択肢が挙がっておらず、推奨案のまま人間が承認した。

## 決定

- 成果物の保存先はファイルシステムとする（外部データベースを導入しない）。
- 表形式データ（trace、result の系列データなど）は Parquet で保存する。
- manifest（run の実行条件、identifiers、集計の要約など機械可読なメタデータ）は JSON で保存する。
- 具体的なディレクトリ構成・スキーマ・ファイル命名は D06（バックテストエンジン設計）・D07（単一実行評価設計）で定める。本決定はファイル形式のみを確定する。

## 影響

- D06 の trace・result の保存形式、D07 の指標出力形式は、本決定に従い Parquet ＋ JSON を前提に設計する。
- 集計は後から DuckDB 等で Parquet を直接読める。
- app 層の CLI がこれらの成果物を書き出す際の保存処理も、この形式に従う。
