# ADR-0004: 依存規則は import-linter で CI 必須検査にする

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 A-4

## 文脈

依存規則は文書だけでは崩れる。

## 決定

`import-linter` を CI 必須とし、循環依存・逆依存・他パッケージの adapters 直接参照・外部ライブラリの adapters/app 外での import を検出する。

## 影響

- 契約定義は D01 に含め、`tests/architecture/` で検査する。
