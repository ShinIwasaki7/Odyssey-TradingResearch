# ADR-0008: 部品実装は純粋関数＋明示状態とする

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 A-8

## 文脈

状態が明示されていれば `state_spec` との照合・保存・復元が単純になる。

## 決定

部品実装は次の2形式へ統一し、部品クラス内部に可変状態を隠さない。

```text
evaluate(inputs, parameters) -> outputs
evaluate(inputs, parameters, state) -> (outputs, new_state)
```

状態の保存場所はランタイムであり、部品実装オブジェクトではない。

## 影響

- カタログの登録形式と状態型の照合は D04・D05 で確定する。
