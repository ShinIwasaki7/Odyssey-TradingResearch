---
name: pr-review
description: Use this skill when creating a PR in Odyssey-TradingResearch, running the Codex independent review loop on one, classifying review findings, or reporting whether a PR is ready for a human merge decision. It keeps code review separate from the merge decision, which always stays with the human.
---

# PR Review

このリポジトリの PR レビューのレーン。**規則の正本は
[docs/pr_review_policy.md](../../../docs/pr_review_policy.md) だけ**であり、本スキルは
**手順の並びと、各手順で読む節の番号**を示す。規則そのものをここに写さない（写すと片方が古くなる）。
Codex 応答の取得は [tools/ops/codex_review_poll.py](../../../tools/ops/codex_review_poll.py)。

## When to use

- PR を作る / PR 完了報告を作るとき。
- Codex の独立レビューを依頼し、その応答を待って修正するとき。
- レビュー指摘を分類し、merge 可否の判断材料を人間へ渡すとき。

## 手順

1. **PR 種別を判定する**（設計文書のみ / 骨格・ツール / 実装）。**Codex review はどの種別でも必須**。
   → 方針文書 §2
2. **PR を作る**。`gh pr create --base main`。本文に成果物一覧・検証結果・仮置き事項・レビュー対象範囲を書く。
   → 方針文書 §3.1（範囲の書き方）・§6（仮置き）
3. **検証を通す**。`uv sync` → `uv run ruff check .` → `uv run ruff format --check .`
   → `uv run mypy` → `uv run lint-imports` → `uv run pytest`。CI と同じ並び。
4. **Codex にレビューを依頼する**。`gh pr comment <PR#> --body "@codex review <対象範囲の1文>"` を投稿し、
   投稿時刻（UTC, ISO8601）を控える。→ 方針文書 §3.1（対象範囲の正本の選び方と添える1文）
5. **応答を待つ**。

   ```
   uv run python tools/ops/codex_review_poll.py \
       --pr <PR#> --since <投稿時刻> --interval 240 --timeout 1200
   ```

   exit 0 = 応答あり（JSON）、1 = gh の失敗（報告して停止）、
   2 = 20分無応答（「Codex 未応答（GitHub App 未設定の可能性）」として報告し、待たない）。
   動作確認だけしたいときは `--once`。→ 方針文書 §4 の終了コード表
6. **指摘を分類する**（対象内 / 対象外 / 不採用 → 対象内に P0 / P1 / P2）。3分類とも PR コメントに残す。
   → 方針文書 §3（優先度の定義）・§3.1（3分類）
7. **対象内の P0 / P1 を直して push し、再依頼する**。毎巡の傾向の1行、同種の指摘への根本対処、
   設計を曲げないこと、終了条件（対象内 P0=0 かつ P1=0）、0件の判定が指すのは head であること。
   → 方針文書 §4.1・§4.2・§4.3
8. **安全弁と利用上限**。6巡で収束しなければ止めて「要決定」として報告する（merge は問わない）。
   利用上限の応答が返ったら `uv run python tools/ops/claude_review.py --pr <PR#> --round <巡番号>` で
   Claude 敵対レビューに切り替え、その結果を手順6以降に流す。→ 方針文書 §4.4・§4.6・§7
9. **設計適合レビュー**を別レーンとして行う。Codex が clean でも省略しない。→ 方針文書 §5
10. **仮置きを閉じる**。merge 前に人間が1件ずつ決め、決定は設計文書の改訂として同じ PR に含める。
    → 方針文書 §6
11. **報告する**。巡数と各巡の件数、残った P2、仮置き事項、head を書く。
    手順 9・10 で PR を変えたら、2つのレーンが同じ最終 head で新たな修正を生まなくなるまで戻る。
    → 方針文書 §4.1（最終 head）・無人運転プロトコル §3（報告様式）

## 規律

- 4 の依頼から 7 の再依頼までのループは、**巡数によらず、承認停止なしで自走する**。
  止めて確認を取るのは **merge の前だけ**（CLAUDE.md）。
- **merge はしない**。merge 可否は人間が判断する。
- 3チャネル（review / inline comment / issue comment）の照会は poller が正本。
  `gh` を手で叩いて再実装しない。
- 承認外の設計判断が必要になったら、その場で決めずに実装を止め、設計文書へ差し戻す。
- **規則の中身を本スキルに書き足さない**。手順が足りないと感じたら方針文書を直し、ここは参照を増やすだけにする。
