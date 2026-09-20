---
name: model-routing
description: Use this skill whenever work is delegated to a subagent, a child session, or a Workflow in Odyssey-TradingResearch. It decides which model (Fable / Opus / Sonnet / Haiku) does which kind of task and how to keep the orchestrator's context small, so that the 5-hour usage window lasts through an unattended run. Load it before every Agent call and when planning a multi-step task.
---

# model-routing

目的: 5時間の使用量枠を使い切らずに、外出中の無人運転を完走させる。
手段は2つ。**(1) 仕事の種類ごとに最も安いモデルへ振り分ける**、**(2) 司令塔の文脈を太らせない**。

## 1. 役割とモデル（正本）

| 役割 | モデル | やること | やらないこと |
|---|---|---|---|
| **司令塔**（このセッション） | Fable（`claude-fable-5-1`） | 計画の現在地の管理、作業役の起動と再開、人間への質問、報告の統合 | コードを読む、テストを走らせる、ログを読む、自分で実装する |
| **実装役** | Opus（`model: "opus"`） | 承認済み設計文書1本を読んで実装・テスト・PR 作成・Codex レビュー往復（`pr-review` skill） | 設計判断。未承認の判断が要れば止めて「要決定」を報告する |
| **調査・整理役** | Sonnet（`model: "sonnet"`） | コード調査、Codex 指摘の分類（P0/P1/P2）と要約、設計文書と実装の突き合わせ、テスト失敗の一次切り分け | 実装の変更 |
| **機械作業役** | Haiku（`model: "haiku"`） | 手順が完全に決まっている作業: ファイル移動・rename、定型の文書更新、lint/format 修正、進捗ファイルの更新 | 判断を含む作業すべて |

判定の軸は **「仕様がどれだけ具体的か」**。設計文書で型・フィールド・振る舞いが確定している作業ほど下位モデルへ回す。迷ったら1段上のモデル。

## 2. 振り分けの判定表

| 作業の例 | モデル |
|---|---|
| 確定済み設計（D02 の型など）を新規実装し、テストを書く | Opus |
| 既存実装に、設計文書で確定した1フィールドを追加する（差分が明確） | Sonnet |
| Codex の指摘（review / inline / issue comment）を読んで P0/P1/P2 に分類し、修正方針を1行ずつ付ける | Sonnet |
| 分類済みの P0/P1 を修正して push し再レビューを依頼する | Opus（設計に触れる指摘があれば止める） |
| P2 だけの修正（命名・スタイル）を適用する | Haiku |
| 「この関数はどこから呼ばれているか」「この契約はどこで検査されているか」を調べる | Sonnet |
| `docs/status/progress.md` を報告どおりに更新する | Haiku |
| 設計文書の未決定事項を洗い出し、選択肢と推奨を作る | Opus（人間に出す質問は司令塔が整形） |
| 人間に質問する、作業役を起動する、全体の順序を決める | Fable（司令塔のみ） |

## 3. 司令塔を軽く保つ規律

1. **司令塔は Read / Bash でコードやログを開かない**。必要なら Sonnet に読ませて要約を受け取る。
2. **作業役への指示は「ファイルの場所」で渡す**。内容を貼り付けない。例: 「`docs/design/D03_marketdata_and_time.md` §3〜§5 を実装。契約は D02 §4.1」。
3. **作業役の報告は 300 語以内・定型**（§4）。ログ・diff・テスト出力の全文は返させない。
4. **1つの作業役に1つの設計文書（または1つの PR）**。終わったら捨てる。続きは `SendMessage` で同じ作業役を再開するより、新しい作業役に進捗ファイルを読ませる方を優先する（古い文脈を引きずらない）。
5. **並列は最大2**。設計承認と merge が人間待ちなので、それ以上は待ち行列が伸びるだけ。
6. **同じモデルで2回失敗したら1段上へ**。3回目を同じモデルで回さない。
7. **人間への質問はまとめて1回**。作業役が返した「要決定」を司令塔が集約し、選択肢は3つ以内・推奨を先頭に置く。

## 4. 作業役の報告様式（300語以内）

```
## 結果: 完了 / 要決定 / 失敗
- 成果物: PR #<n>（ブランチ名）/ 変更ファイル数
- 検証: ruff / mypy / lint-imports / pytest の合否（数字だけ）
- Codex: 巡数、残 P2 の件数、仮置き事項（あれば1行ずつ）
- 要決定（あれば）: 何を決めるか1行 / 選択肢（推奨を先頭、最大3つ）/ 各選択肢の影響1行
- 次に着手可能な作業: 1行
```

「失敗」のときは、原因の仮説を1行と、試したことを最大3行。全文ログは `docs/status/` 配下にファイルとして置き、報告にはパスだけ書く。

## 5. Agent 呼び出しの型

コミットを生む作業（実装・修正・文書更新など）:

```
Agent(
  subagent_type="general-purpose",
  model="opus" | "sonnet" | "haiku",   # §1 の表に従う
  isolation="worktree",                # コミットを生む作業は必ず隔離（CLAUDE.md）
  prompt=<目的1行 + 読むべきファイルの場所 + 完了条件 + §4 の報告様式>
)
```

read-only の作業（調査・分析・指摘の分類・相談）:

```
Agent(
  subagent_type="general-purpose",
  model="sonnet" | "haiku",            # §1 の表に従う
  # isolation は渡さない（現ディレクトリのまま。CLAUDE.md の二択に従う）
  prompt=<目的1行 + 読むべきファイルの場所 + 完了条件 + §4 の報告様式>
)
```

- **`isolation="worktree"` を無条件に渡さない**。CLAUDE.md の判断は二択（コミットを生む→隔離 / read-only→現ディレクトリのまま）。read-only の作業役を隔離すると、いま作業中の worktree の未コミット状態が見えず、`origin/main` の新しい基点に対して調査結果を返してしまう。
- `effortLevel` は司令塔の既定（high）を作業役に継承させない。Haiku / Sonnet の機械作業は low〜medium で足りる。
- Workflow ツールを使うのは、人間が明示的に「workflow で」と言った場合のみ。

## 6. 使用量が尽きたとき

- セッションは停止しリセット時刻が表示される。自動再開はない（使用量クレジットは使わない方針）。
- 作業状態は `docs/status/progress.md` と PR が正本。リセット後に iPhone から「続けて」と送れば、司令塔は進捗ファイルを読み直して再開する。
- 停止直前の作業役の未報告分は失われる可能性がある。作業役は **PR を作った時点・Codex 1巡が終わった時点** で進捗ファイルを更新すること（Haiku に任せてよい）。
