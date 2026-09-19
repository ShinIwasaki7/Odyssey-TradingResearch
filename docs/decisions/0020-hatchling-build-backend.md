# ADR-0020: ビルドバックエンドは hatchling

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 仮置き（D01 §14）、[D01](../design/D01_architecture_and_dependency_rules.md)

## 文脈

`src/` レイアウト（ADR-0005）を正式にサポートするビルドバックエンドが必要。

## 決定

hatchling を採用する。`src/` レイアウトを正式にサポートしているため。wheel 対象は `[tool.hatch.build.targets.wheel] packages = ["src/odyssey_fx"]` と明示する。

## 影響

- `pyproject.toml` の `[build-system]` と wheel 設定を固定する。他のバックエンドへの変更は ADR を要する。
