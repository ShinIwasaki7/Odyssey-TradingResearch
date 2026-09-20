# ADR-0032: 再発火は常に新しい Opportunity を生成し、`OpportunityConcurrencySpec` を必須にする

- 状態: 承認（2026-09-20）
- 決定者: ユーザー（リポジトリ所有者）
- 関連: [上位設計書](../design/fx_research_platform_greenfield_design.md) §4.5・§4.3.5・§4.3.14・§4.7.12・§4.7.14、[全体計画書](../design/fx_research_platform_overall_plan.md) 第5.3節・第7.3節（D05）

## 文脈

取引機会（`Opportunity`）の確認待ち中に、同じ Trigger が再び発火したときの扱いが未決定だった。上位設計書 §4.5 と全体計画書 第7.3節（D05）は「同方向/逆方向の再発火を破棄・置換・並行待機のどれで扱うか」「並行機会数」を要決定として両論併記していた。

既存の機会を新しい発火の内容で上書きする「置換」を許すと、過去に記録した市場事実が後から書き換わり、判断履歴の追跡（trace）が成立しない。一方で、ランタイムが暗黙に古い機会を捨てる方式も、戦略の意味が設定に現れない。

## 決定

以下を決定内容の正本とする（ユーザーの文言をそのまま採用）。

> Triggerの各発火は、それぞれ固有のopportunity_idを持つ不変のOpportunityを生成する。同方向・逆方向という理由だけで、ランタイムが既存Opportunityを暗黙に破棄、更新または統合してはならない。
>
> 複数Opportunityの関係はOpportunityConcurrencySpecとしてStrategyDefinitionに必須指定する。ランタイムは複数の有効なOpportunityを同時に保持できなければならない。
>
> 既存Opportunityの内容を新しいTriggerの内容で上書きする「置換」は禁止する。新しいTriggerを優先する設定であっても、既存OpportunityをSUPERSEDEDで終端にし、新しいopportunity_idを持つOpportunityを生成する。
>
> 同一event_idの再配送は冪等性検査で除外し、新しいOpportunityを生成しない。異なるTriggerイベントは、方向・価格・内容が同じでも別の市場事実として記録する。条件が連続してtrueであることによる毎足の再発火は、Trigger部品の遷移検出および再武装規則で制御する。
>
> 各Opportunityは独立した確認開始区間、期限、ValiditySpec、確認履歴を持つ。あるOpportunityの確認、失効、無効化または発注試行によって、別のOpportunityを暗黙に変更してはならない。
>
> 複数のOpportunityが同時にOrderRequestへ到達した場合は、決定論的な受付順とRiskPolicyで審査する。ある注文が受け付けられたことを理由に他のOpportunityを終了する場合は、on_order_acceptedへその規則を明示する。

## 影響

- `StrategyDefinition`（上位設計書 §4.3.5）に必須フィールド `opportunity_concurrency` を追加する。省略時の既定値を持たせない。
- 取引機会のライフサイクルに終端状態 `SUPERSEDED` を追加する。既存の評価要求の「追い越し（supersession、§4.3.14）」とは対象が異なる（評価要求ではなく取引機会）ため、D05 で語彙を区別して定義する。
- 理由コード（上位設計書 §4.7.14）に `SUPERSEDED` を追加する。
- 同時到達時の受付順は、上位設計書 §4.7.12 で既に確定している全順序化（`(decision_time, strategy_priority, opportunity_id, attempt_id)`）と `RiskPolicy` の審査に従う。新しい順序規則は導入しない。
- 受付済み注文を理由に他の機会を終了させる規則は `OpportunityConcurrencySpec` の `on_order_accepted` に明示する。暗黙には終了させない。
- 同一 `event_id` の再配送除外は、全体計画書 第5.4節で既に合意済みの冪等性検査（`event_id` と処理済み記録を状態更新と同じ確定単位に含める）を取引機会生成にも適用することを意味する。
- 毎足の再発火抑止は Trigger 部品側の遷移検出・再武装規則の責務であり、ランタイムが暗黙に間引くことではない。D05 の合成部品（遷移検出）契約に再武装規則を含める。
