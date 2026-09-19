# ADR-0002: パッケージ内はコンテキスト優先で層化する

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 A-2

## 文脈

レイヤ優先（トップに domain/application/adapters）とコンテキスト優先（各パッケージ内に層）の二択。

## 決定

コンテキスト優先を採用し、各パッケージ内を domain / application / adapters に分ける。

## 影響

- 変更理由がコンテキストごとに閉じる。境界を跨ぐ依存が import パスで判別できる。
- 層の規則（domain は標準ライブラリと `common` のみ、adapters は `app` からのみ結線）は D01 で確定する。
