# ADR-0014: 期間はアクセス状態で三分類し、LEGACY_HOLDOUT は SEALED / CONSUMED の状態を持つ

- 状態: 承認（2026-09-18。2026-09-20 に HoldoutState を追加改訂、同日 消費遷移の直列化を追加）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 C-2、[D03](../design/D03_marketdata_and_time.md) §3.8、[D01](../design/D01_architecture_and_dependency_rules.md) §10.2、PR #2 の Codex 指摘（CONSUMED が状態モデルに未定義）

## 文脈

実データは 2026-04-10 または 2026-05-01 まで存在し、上位文書の「2016〜2023 研究、2024〜2025 封印」より新しい期間を含む。既に観測した期間は holdout に戻らない。

初版の本 ADR は三分類だけを定義し、「使用済みなら CONSUMED として再選定に使わない」と書いていたが、`CONSUMED` の状態・遷移・除外規則を定義していなかった。実装が従う正本がないと、観測済み holdout を再利用する余地が残る（PR #2 Codex 指摘、P1）。

## 決定

### アクセス分類 `AccessClass`（変更なし）

| 区間 | 分類 | 扱い |
|---|---|---|
| 2016〜2023 | `RESEARCH_HISTORY` | 初版の開発・研究に使用可能 |
| 2024〜2025 | `LEGACY_HOLDOUT` | 旧基盤の holdout。下記 `HoldoutState` を持つ |
| 2026年分 | `QUARANTINED_UNASSIGNED` | 自動的に holdout へ昇格させず、アクセス履歴確認まで通常経路から読めなくする |

- 2026年分が未観測だったことを確認できた場合だけ、別 ADR で sealed holdout へ割り当てる。確認できない場合は研究履歴として扱い、将来取得するデータを新しい prospective holdout にする。
- 元 CSV は期間をまたぐため、物理分離は raw CSV の移動ではなく、受入れ処理で生成する snapshot partition に対して行う。

### `HoldoutState`（2026-09-20 追加）

`LEGACY_HOLDOUT` の partition だけが `HoldoutState` を持つ。他の分類には状態がない。

| 状態 | 意味 | 読める条件 |
|---|---|---|
| `SEALED` | 旧基盤で未観測であることを確認できた partition | `holdout_gate` を通る最終評価でのみ。読んだ時点で `CONSUMED` へ遷移 |
| `CONSUMED` | 使用済み、または履歴が確認できない partition | 研究・開発用途で、明示的な opt-in がある場合のみ。既定では読めない |

- 受入れ時の初期状態: 旧基盤の使用履歴（manifest の `legacy_access`）により**未観測と確認できた partition だけを `SEALED`** とする。使用済み、または履歴不明の partition は `CONSUMED`（または `QUARANTINED_UNASSIGNED` への再分類）とし、`SEALED` にしない。
- `SEALED → CONSUMED` は不可逆。逆遷移は存在しない。
- `CONSUMED` は holdout としての再選定・最終評価に**永久に使用禁止**。
- `CONSUMED` を研究・開発用途で読む場合、run manifest に「`CONSUMED` partition を使用したこと」と「利用目的」を記録し、その結果を holdout 成績として扱うことを禁止する（評価基盤は holdout 成績としての集計を拒否する）。
- 状態は**追記専用の access log**（`data/snapshots/<snapshot_id>/access_log.jsonl`、git 管理。下記「消費遷移の直列化」）から導出する。manifest 内に状態を直接書き換えるフィールドは持たない。

### fail-closed の手順

`SEALED` partition の読み取りは次の順で行い、どの段階で失敗しても データを返さない。

1. アクセス許可の発行（`holdout_gate` が実験 manifest と利用目的を検査）。
2. 消費記録の追記（access log に `CONSUMED` 遷移を永続化）。
3. データ公開（as-of ビュー / snapshot 読み取りに partition を許可）。

消費記録の永続化が確認できる前にデータを返さない。許可発行だけで記録がない状態、記録があるのに公開されない状態はいずれも「読めない」に倒す。

### 消費遷移の直列化（2026-09-20 追加）

複数のクローンやプロセスが同じコミット済み manifest から `SEALED` を導出し、それぞれローカルで `CONSUMED` を追記して同じ holdout を公開することを防ぐため、**origin への push を compare-and-set とする**（PR #2 Codex round 3 指摘）。

- access log は manifest 内ではなく独立ファイル `data/snapshots/<snapshot_id>/access_log.jsonl`（追記専用、git 管理）に置く。`HoldoutState` はこのファイルから導出する。`.gitignore` は snapshot 実体を除外しつつ `manifest.json` と `access_log.jsonl` の両方を再包含する（ADR-0013 改訂）。追跡されていない access log への追記は「未記録」であり、gate は公開前に当該ファイルが git で追跡され origin に到達したことを検証する。
- 消費の手順: (1) `git fetch origin` し、origin の既定ブランチ上の access log に自分の知らない追記がないことを確認する（あれば状態を再導出し、既に `CONSUMED` なら拒否）。(2) 消費記録を追記してコミットする。(3) origin の既定ブランチへ push する。(4) push が成功した後にだけデータを公開する。
- push が non-fast-forward で拒否された場合は、状態を再導出して拒否する。再試行は人間の判断による。
- origin に到達できない環境では `SEALED` partition を読めない（fail-closed）。ローカルだけの記録で公開することは許可しない。
- 正本は origin の既定ブランチ上の access log であり、ローカルの作業ツリーやクローンの状態ではない。

## 影響

- D03 §3.8（partition の状態）、§6.1（as-of ビューの許可 partition）に反映する。
- `evaluation.application.holdout_gate`（D07）が許可発行・消費記録・公開の順序と、`CONSUMED` 使用時の manifest 記録、holdout 成績としての集計拒否を実装する。
- `access_log.jsonl` は追記専用とし、`HoldoutState` はそこから導出する。ADR-0013 と D01 §7.1・§10.2 の「manifest のみ追跡」を「manifest と access log を追跡」に改訂する。

## 改訂履歴

| 日付 | 内容 |
|---|---|
| 2026-09-18 | 初版承認（三分類） |
| 2026-09-20 | `HoldoutState`（SEALED / CONSUMED）、初期状態の判定、不可逆遷移、opt-in と manifest 記録、fail-closed 手順、access log からの導出を追加 |
| 2026-09-20 | 消費遷移の直列化: access log を独立ファイルにし、origin への push を compare-and-set とする |
| 2026-09-25 | 注記（決定の内容は変えない）: 「影響」の `holdout_gate`（D07）の担当を **D09（段階5）** と読み替える。人間の決定（2026-09-25）による。旧基盤の閲覧履歴が0件で `SEALED` の partition が存在せず、段階4 は研究履歴だけで完了条件を満たせるため（D07 v2.0 §24、D03 v1.12 §3.8） |
