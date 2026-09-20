# ADR-0025: adapters の表形式ライブラリは polars

- 状態: 承認（2026-09-20）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 B-5、[D01](../design/D01_architecture_and_dependency_rules.md) §5、[D03](../design/D03_marketdata_and_time.md) §8

## 文脈

原 CSV の読込と snapshot の Parquet 入出力に表形式ライブラリが必要。候補は pandas と polars。依存規則（D01 §5、契約 F5a）により、いずれを選んでも adapters と `app` の外では import できない。

## 決定

- 表形式ライブラリは polars を採用する。Parquet の入出力も polars で行う（pyarrow は polars の依存として入るが直接は使わない）。
- 使用は `marketdata.adapters`、`evaluation.adapters`、`app` に限る。DataFrame を adapters の外へ出さない。

## 影響

- `pyproject.toml` のランタイム依存に polars を追加する（段階1の `marketdata` 実装 PR で行う）。
- 型が厳密で高速なため、Decimal 列の扱い（文字列として読み、application 層で Decimal 化）を adapters の責務として明示する。
- adapters に閉じているため、将来 pandas 等へ交換しても domain / application は変更不要。
