# ADR-0014: 期間はアクセス状態で三分類する

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 C-2

## 文脈

実データは 2026-04-10 または 2026-05-01 まで存在し、上位文書の「2016〜2023 研究、2024〜2025 封印」より新しい期間を含む。既に観測した期間は holdout に戻らない。

## 決定

| 区間 | 分類 | 扱い |
|---|---|---|
| 2016〜2023 | `RESEARCH_HISTORY` | 初版の開発・研究に使用可能 |
| 2024〜2025 | `LEGACY_HOLDOUT` | 旧基盤のアクセス履歴を引き継ぐ。使用済みなら `CONSUMED` として再選定に使わない |
| 2026年分 | `QUARANTINED_UNASSIGNED` | 自動的に holdout へ昇格させず、アクセス履歴確認まで通常経路から読めなくする |

- 2026年分が未観測だったことを確認できた場合だけ、別 ADR で sealed holdout へ割り当てる。確認できない場合は研究履歴として扱い、将来取得するデータを新しい prospective holdout にする。
- 元 CSV は期間をまたぐため、物理分離は raw CSV の移動ではなく、受入れ処理で生成する snapshot partition に対して行う。

## 影響

- D03（受入れ・partition）と `evaluation.holdout_gate` の設計に反映する。
