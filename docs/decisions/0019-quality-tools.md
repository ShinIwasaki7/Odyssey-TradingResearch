# ADR-0019: 品質ツールは ruff / mypy strict / pytest / hypothesis / import-linter

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 B-9、[D01](../design/D01_architecture_and_dependency_rules.md)

## 文脈

契約設計を機械検査に落とすため、型検査と依存規則検査を CI で必須にする必要がある。

## 決定

ruff（lint・format）、mypy（strict）、pytest、hypothesis、import-linter を採用し、CI で必須にする（D01 §11）。

## 影響

- dev 依存グループに固定。ランタイム依存には含めない。
