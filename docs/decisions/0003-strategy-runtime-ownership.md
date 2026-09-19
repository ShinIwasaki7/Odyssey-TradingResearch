# ADR-0003: 戦略ランタイムは strategy に置く

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 A-3

## 文脈

部品評価・WAIT_FOR_INPUT・追い越し・取引機会のライフサイクルは戦略の意味論であり、将来のライブ実行や別エンジンでも同じ意味で動く必要がある。

## 決定

- `strategy`: 起動対象の決定、依存順の部品評価、WAIT・追い越し・取引機会の更新、注文意図等の返却。
- `backtest`: 時刻 T の進行、公開イベントと現在状態の `strategy` への受け渡し、リスク審査・受付・約定・台帳更新。

```text
backtest: 時刻 T を進める → 公開イベントと現在状態を strategy へ渡す
strategy: 起動対象を決める → 依存順に部品を評価 → WAIT・追い越し・取引機会を更新 → 注文意図等を返す
backtest: リスク審査・受付・約定・台帳更新
```

## 影響

- `strategy.runtime` のポート（`MarketDataView`、`RuntimeContextView`、`OutputSink`）は `strategy` が定義し、`backtest` が満たす。
- 詳細は D05・D06。
