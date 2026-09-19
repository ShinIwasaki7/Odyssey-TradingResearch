# ADR-0018: 設定は YAML、機械生成 manifest は JSON

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 B-6、[D01](../design/D01_architecture_and_dependency_rules.md)

## 文脈

設定形式の候補は YAML / TOML / JSON。人間が書く入れ子の宣言には可読性、機械生成の manifest には厳密さが要る。

## 決定

- 人間が編集する宣言（戦略・実験・ポリシー・銘柄・カレンダー）は YAML。
- 機械生成の manifest（snapshot / run / 実験）は JSON。

YAML の読込条件（`app.config` が強制）:

- YAML 1.2 相当の安全な読込。カスタムタグ禁止。
- 重複キーはエラー。
- merge key など、展開後の内容がレビュー時に分かりにくい機能は禁止。
- 全設定に `schema_version` 必須。
- Pydantic v2 で検証した後、frozen dataclass へ変換する（ADR-0011）。

## 影響

- YAML パーサと Pydantic は `app.config` 以外で import 不可（D01 §5、F5a）。
