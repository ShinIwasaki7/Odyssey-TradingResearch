---
name: pr-review
description: Use this skill when creating a PR in Odyssey-TradingResearch, running the Codex independent review loop on one, classifying review findings, or reporting whether a PR is ready for a human merge decision. It keeps code review separate from the merge decision, which always stays with the human.
---

# PR Review

このリポジトリの PR レビューのレーン。方針の正本は
[docs/pr_review_policy.md](../../../docs/pr_review_policy.md)、
Codex 応答の取得は [tools/ops/codex_review_poll.py](../../../tools/ops/codex_review_poll.py)。

## When to use

- PR を作る / PR 完了報告を作るとき。
- Codex の独立レビューを依頼し、その応答を待って修正するとき。
- レビュー指摘を分類し、merge 可否の判断材料を人間へ渡すとき。

## 手順

1. **PR 種別を判定する**（設計文書のみ / 骨格・ツール / 実装）。
   Codex review の要否は方針文書の §2 の表による。実装 PR と骨格・ツール PR は必須。
2. **PR を作る**。`gh pr create --base main`。本文には成果物一覧・検証結果・
   仮置きした事項を書く。
3. **検証を通す**。`uv sync` → `uv run ruff check .` → `uv run ruff format --check .`
   → `uv run mypy` → `uv run lint-imports` → `uv run pytest`。CI と同じ並び。
4. **Codex にレビューを依頼する**。`gh pr comment <PR#> --body "@codex review"` を投稿し、
   その投稿時刻（UTC, ISO8601）を控える。
5. **応答を待つ**。

   ```
   uv run python tools/ops/codex_review_poll.py \
       --pr <PR#> --since <投稿時刻> --interval 240 --timeout 1200
   ```

   exit 0 = 応答あり（JSON）、1 = gh の失敗（報告して停止）、
   2 = 20分無応答（「Codex 未応答（GitHub App 未設定の可能性）」として報告し、待たない）。
   動作確認だけしたいときは `--once`。
6. **指摘を分類する**（P0 / P1 / P2。定義は方針文書 §3）。
   poller は本文中の P0/P1/P2 タグを `priority` として返すが、タグがない指摘は
   自分で分類し、その分類を PR に残す。
7. **P0 / P1 を修正して push し、再度 `@codex review` を投稿する**。
   round 1 で P0 / P1 が0件なら 2巡目を行わない。最大2巡。
8. **設計適合レビュー**（方針文書 §5）を別レーンとして行う。Codex が clean でも省略しない。
9. **報告する**。実施した巡数、残った P2、仮置きした事項を書く。

## 規律

- 4 の依頼から 7 の再依頼までのループは、**承認停止なしで自走する**。
  止めて確認を取るのは **merge の前だけ**（CLAUDE.md）。
- **merge はしない**。merge 可否は人間が判断する。
- 3チャネル（review / inline comment / issue comment）の照会は poller が正本。
  `gh` を手で叩いて再実装しない。
- 承認外の設計判断が必要になったら、その場で決めずに実装を止め、設計文書へ差し戻す。
