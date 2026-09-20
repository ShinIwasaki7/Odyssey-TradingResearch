# ADR-0001: パッケージ分割は5つとする

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 A-1

## 文脈

責務分解は戦略・バックテスト・評価の3基盤だが、時刻・ID・金額等の共通型と、snapshot・カレンダー・as-of 参照を扱う市場データは、3基盤のすべてが使う。市場データを `backtest` 配下に置くと `strategy → backtest` の逆依存が生じる。

## 決定

`common` / `marketdata` / `strategy` / `backtest` / `evaluation` の5パッケージとし、`app` を構成ルートとする。

## 影響

- 依存方向は `app → evaluation → backtest → strategy → marketdata → common`。
- 各パッケージの公開型と許可参照は D01 で確定する。
