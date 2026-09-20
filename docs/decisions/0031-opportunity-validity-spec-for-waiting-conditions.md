# ADR-0031: 確認待ち中の条件は固定／継続要求に明示分類し、`OpportunityValiditySpec` を必須にする

- 状態: 承認（2026-09-20）
- 決定者: ユーザー（リポジトリ所有者）
- 関連: [上位設計書](../design/fx_research_platform_greenfield_design.md) §4.3.14・§4.3.5・§4.5・§4.7.14、[全体計画書](../design/fx_research_platform_overall_plan.md) 第5.3節・第7.3節（D05）

## 文脈

取引機会（`Opportunity`）が後続確認を待っている間に、その機会が前提としていた条件（例: 市場状態による取引許可）が崩れたときにどう扱うかが未決定だった。上位設計書 §4.5 は「MarketState を発火時だけ見るか待機中も再検査するか」を要決定として両論併記し、全体計画書 第7.3節（D05）も同じ項目を残していた。

固定と再検査のどちらかを暗黙の既定にすると、戦略の意味が設定を読まないと分からなくなる。また、再検査を過去の判断の再計算と混同すると、先読み（判断時点で利用できない情報の参照）や履歴の書き換えを招く。

## 決定

以下を決定内容の正本とする（ユーザーの文言をそのまま採用）。

> 確認待ちのOpportunityが参照する条件は、発生時に固定する条件と、待機中に継続して成立を要求する条件に明示的に分類する。分類はOpportunityValiditySpecとしてStrategyDefinitionに必須指定し、暗黙の既定値を設けない。
>
> SNAPSHOT_AT_OPPORTUNITYに指定された条件は、Opportunity生成時のOutputRecordを固定し、その後の更新によって再評価しない。
>
> REQUIRE_UNTIL_ORDER_REQUESTに指定された条件は、確認評価のたび、およびOrderRequest生成直前に、そのdecision_timeで利用可能な最新の出力を読み直す。条件が成立しなくなったOpportunityはMARKET_STATE_INVALIDATEDで終端とし、条件が再び成立しても復活させない。新たな取引には新しいTriggerとOpportunityを必要とする。
>
> 再検査によって、Opportunityに固定された方向、signal interval、突破水準、参照値、Trigger時点の根拠を変更してはならない。再検査するのはOpportunityの現在の有効性であり、過去のTriggerを現在値で再計算することではない。
>
> 再検査対象の入力が欠損している場合は、そのValidityBindingに宣言されたMissingInputPolicyを適用する。欠損を不成立や直前値へ暗黙変換してはならない。待機する場合もOpportunity本来の期限は延長しない。

## 影響

- `StrategyDefinition`（上位設計書 §4.3.5）に必須フィールド `opportunity_validity` を追加する。省略時の既定値を持たせない。
- 取引機会の終端理由に `MARKET_STATE_INVALIDATED` を追加する。復活はしない。非終端の状態名を含む完全な状態機械は D05 で設計する。
- 理由コード（上位設計書 §4.7.14）に `MARKET_STATE_INVALIDATED` を追加し、条件未成立（`ConfirmationResult` の False）・期限切れ・入力不足と区別する。
- 再検査単位の入力参照は `ValidityBinding` として持ち、欠損時の動作は既存の `MissingInputPolicy` を再利用する。待機しても機会本来の期限は延長しない。
- D04（宣言モデル）に `OpportunityValiditySpec` / `ValidityBinding` の型を、D05（ランタイム）に再検査の起動点（確認評価時・`OrderRequest` 生成直前）を定める。
