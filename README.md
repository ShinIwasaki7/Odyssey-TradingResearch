# Odyssey-TradingResearch

FX 戦略研究のための基盤。戦略基盤・バックテスト基盤・評価基盤と、それらが共有する
共通カーネル・市場データ/時刻基盤を、境界付きコンテキストごとのパッケージとして構築する。

**現状は段階−1（基盤整備）**。骨格・ツール設定・CI のみで、業務ロジックは未実装。
各型と計算規則は設計文書の承認後に実装する。

## セットアップ

Python は uv 管理の 3.12.13 に固定している（ADR-0009 / ADR-0010）。
シェル既定の `python3` は使わず、すべて `uv run` 経由で実行する。

```bash
uv sync
```

## 検査

CI（`.github/workflows/ci.yml`）と同じ並び。

```bash
uv run ruff check .          # lint
uv run ruff format --check . # 整形
uv run mypy                  # 型検査（strict）
uv run lint-imports          # 依存規則の契約検査（ADR-0004）
uv run pytest                # テスト
```

依存規則の契約は `pyproject.toml` の `[tool.importlinter]` にある。
パッケージ間の依存方向とクリーンアーキテクチャの層、外部ライブラリの
adapters/app 限定を機械検査する。

## ディレクトリ

```text
src/odyssey_fx/   common / marketdata / strategy / backtest / evaluation / app
tests/            unit / semantics / property / golden / architecture / fixtures
tools/ops/        リポジトリ運用スクリプト（Codex review poller）
docs/             設計文書・決定記録
data/             原データと snapshot（実体は git 管理外。manifest のみ追跡）
runs/             実行成果物（git 管理外）
```

## 設計文書

実装の前に設計文書を書き、人間の合意を得る（CLAUDE.md）。

| 場所 | 内容 |
|---|---|
| [docs/design/fx_research_platform_greenfield_design.md](docs/design/fx_research_platform_greenfield_design.md) | 上位設計書（要件と意味論） |
| [docs/design/fx_research_platform_overall_plan.md](docs/design/fx_research_platform_overall_plan.md) | 全体構築計画書（アーキテクチャ・構成・段階計画） |
| [docs/design/D01_architecture_and_dependency_rules.md](docs/design/D01_architecture_and_dependency_rules.md) | アーキテクチャ・依存規則・ディレクトリ構成（承認 2026-09-19） |
| [docs/decisions/](docs/decisions/README.md) | ADR。技術選定・構成・運用の決定を1件1ファイルで記録 |
| [docs/pr_review_policy.md](docs/pr_review_policy.md) | PR レビュー方針（Codex review の運用、指摘の分類） |
