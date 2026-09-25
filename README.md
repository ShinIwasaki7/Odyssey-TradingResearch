# Odyssey-TradingResearch

FX 戦略研究のための基盤。戦略基盤・バックテスト基盤・評価基盤と、それらが共有する
共通カーネル・市場データ/時刻基盤を、境界付きコンテキストごとのパッケージとして構築する。

**現状は段階2（最小縦断）**。市場データの受入れから、戦略定義・注文・約定・単一実行の評価
までがコマンドとして通る。実データでの実行と複数実行の比較は段階4以降であり、まだ無い。
各型と計算規則は設計文書の承認後に実装する（CLAUDE.md）。

## セットアップ

Python は uv 管理の 3.12.13 に固定している（ADR-0009 / ADR-0010）。
シェル既定の `python3` は使わず、すべて `uv run` 経由で実行する。

```bash
uv sync
```

## コマンド

入口は `odyssey-fx`（`uv run odyssey-fx --help`）。市場データの受入れ3つと、単一実行の
実行・評価2つがある。

```bash
# 1. 原ファイルを受け入れて暫定 snapshot を作る（D03 §4 の 1〜8）
uv run odyssey-fx data accept \
    --datasource configs/datasources/legacy_merged_csv_v1.yaml \
    --calendar configs/calendars/fx_ny17_v1.yaml \
    --timeframes configs/calendars/timeframes_v1.yaml \
    --symbols configs/symbols --out data/snapshots

# 2. 分類対象の警告（存在すべき足の欠落・休場帯の足）の分類を記入して確定する
#    （D03 §4 の 9。分類ファイルは形式版 2、D03 §10）
uv run odyssey-fx data classify --pending <暫定 ID> \
    --decisions <分類ファイル> \
    --timeframes configs/calendars/timeframes_v1.yaml --out data/snapshots

# 3. 承認を記入する。承認するまで読み取り対象にならない（D03 §3.7.1）
uv run odyssey-fx data approve --snapshot <最終 ID> --by <名前> --out data/snapshots

# 4. 実験設定から1回の run を実行する（D06 §4.2）。判断履歴と manifest を
#    runs/<run_id>/ へ書く。実験設定の `snapshot` は 3 で承認した識別子に書き換える
#    書式 v1（段階2）は取引カレンダー・時間足定義・銘柄仕様を引数で渡す
uv run odyssey-fx run --experiment configs/experiments/strategy_a_t01.yaml \
    --calendar configs/calendars/fx_ny17_v1.yaml \
    --timeframes configs/calendars/timeframes_v1.yaml \
    --symbols configs/symbols --snapshots data/snapshots
#    書式 v2（D07 §18）は実験設定の `environment` がそれらを指し、戦略は
#    configs/strategies/ の戦略ファイルを指す。遅延シナリオも実験設定に書ける
uv run odyssey-fx run --experiment configs/experiments/strategy_b_t02_d1_2s.yaml \
    --snapshots data/snapshots

# 5. 保存済みの run を評価する（D07 §4・§8）。指標・集計・取引・診断・整合検査の5表と
#    評価 manifest を runs/<run_id>/eval/<評価 ID>/ へ書く
uv run odyssey-fx evaluate --run <run_id>
```

**評価は run を実行し直さない**。保存された判断履歴と実行条件だけを読むので、同じ run を
別の指標集合の版で評価し直しても `runs/` の下の判断履歴は変わらない（D07 §4.1）。

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
data/             原データと snapshot（実体は git 管理外。manifest.json と access_log.jsonl を追跡）
runs/             実行成果物（git 管理外）
```

## 設計文書

実装の前に設計文書を書き、人間の合意を得る（CLAUDE.md）。

| 場所 | 内容 |
|---|---|
| [docs/design/fx_research_platform_greenfield_design.md](docs/design/fx_research_platform_greenfield_design.md) | 上位設計書（要件と意味論） |
| [docs/design/fx_research_platform_overall_plan.md](docs/design/fx_research_platform_overall_plan.md) | 全体構築計画書（アーキテクチャ・構成・段階計画） |
| [docs/design/D01_architecture_and_dependency_rules.md](docs/design/D01_architecture_and_dependency_rules.md) | アーキテクチャ・依存規則・ディレクトリ構成（承認 2026-09-19） |
| [docs/design/D02_common_kernel.md](docs/design/D02_common_kernel.md) | 共通カーネル型設計（承認 2026-09-20） |
| [docs/design/D03_marketdata_and_time.md](docs/design/D03_marketdata_and_time.md) | 市場データ・時刻基盤設計（承認 2026-09-20） |
| [docs/design/D04_strategy_declarations.md](docs/design/D04_strategy_declarations.md) | 戦略宣言の型設計（承認 2026-09-21） |
| [docs/design/D05_strategy_runtime.md](docs/design/D05_strategy_runtime.md) | 戦略ランタイム設計（承認 2026-09-21） |
| [docs/design/D06_backtest_vertical_slice.md](docs/design/D06_backtest_vertical_slice.md) | バックテストの最小縦断設計（承認 2026-09-21） |
| [docs/design/D07_single_run_evaluation.md](docs/design/D07_single_run_evaluation.md) | 単一実行の評価境界設計（承認 2026-09-21） |
| [docs/traces/T01_paper_trace.md](docs/traces/T01_paper_trace.md) | 紙上トレース（検証戦略 A・B の期待値の正本） |
| [docs/decisions/](docs/decisions/README.md) | ADR。技術選定・構成・運用の決定を1件1ファイルで記録 |
| [docs/pr_review_policy.md](docs/pr_review_policy.md) | PR レビュー方針（Codex review の運用、指摘の分類） |
