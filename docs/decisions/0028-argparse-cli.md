# ADR-0028: CLI ライブラリは argparse

- 状態: 承認（2026-09-20）
- 決定者: ユーザー（リポジトリ所有者）
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 B-8、[D01](../design/D01_architecture_and_dependency_rules.md) §14.2

## 文脈

`app.cli` の実装に使う CLI（コマンドライン操作）ライブラリが未決定だった（要決定 B-8）。初版のコマンド案は `data accept`、`strategy compile`、`backtest run`、`eval run`、`experiment run` で、いずれも構成は単純である。全体計画書・D01 とも「依存を増やさない」ことを理由に標準ライブラリの argparse を推奨案として仮置きしていた。決定時期は段階2としていたが、他の選択肢が挙がっておらず、推奨案のまま人間が承認した。

## 決定

- `app.cli` の実装には Python 標準ライブラリの argparse を使う。Click や Typer 等の外部 CLI ライブラリは採用しない。
- サブコマンドの追加・引数定義の具体はコード実装時に決める。本決定はライブラリの選択のみを確定する。

## 影響

- `pyproject.toml` のランタイム依存に CLI 用ライブラリを追加する必要がない（依存を増やさない方針に合致）。
- D01 の依存規則（外部ライブラリの許可表）に argparse を追加する変更は不要（標準ライブラリのため）。
- app の CLI 実装（コマンド追加時）は argparse の作法（`ArgumentParser`、サブパーサー）に従う。
