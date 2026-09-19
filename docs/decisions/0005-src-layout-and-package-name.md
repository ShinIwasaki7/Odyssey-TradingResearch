# ADR-0005: src レイアウトと odyssey_fx を採用する

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 A-5・パッケージ名

## 文脈

`odyssey` だけでは用途が広すぎ、`fxresearch` は一般名すぎる。

## 決定

```text
distribution name: odyssey-trading-research
import package:    odyssey_fx
source directory:  src/odyssey_fx/
```

使用例: `from odyssey_fx.strategy import StrategyDefinition`、`from odyssey_fx.backtest import RunBacktest`。

## 影響

- `src/` レイアウトにより、インストール済みパッケージとソースの混同を防ぐ。
- 計画書のディレクトリ構成を `src/odyssey_fx/` に統一した。
