# ADR-0011: domain は frozen dataclass、設定境界だけ Pydantic v2

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 B-3

## 文脈

宣言の検証規則（arity、`PortKind` と読み方の組合せ等）は自前で書く必要があり、ライブラリで代替できない。domain を外部ライブラリから独立させる。

## 決定

- domain（宣言型・実行記録・注文/約定/予約の型）は frozen dataclass と自前の検証関数。
- 設定ファイル境界（`app.config`）でのみ Pydantic v2 を使う。
- Pydantic モデルを domain へ流入させない。

## 影響

- `app.config` は Pydantic モデルから domain の dataclass へ明示的に変換する。
