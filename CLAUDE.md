# CLAUDE.md

## 絶対ルール

- **前提を silent に決めない**: 解釈が割れる/不明なら止めて問うこと。
- 設計をドキュメントに起こしてから人間の合意を得た上で実装を行なってください。
- 設計は `docs/design/` 配下にドキュメントを残してください。
-

## 並行作業（worktree 自動隔離）

複数セッションを別ブランチで安全に並行させるため、コミットを生むタスクを始めるなら、開始ブランチが何であれ`EnterWorktree` で `.claude/worktrees/<task>` に隔離する（base ref=origin/main・`worktree.baseRef=fresh`）。判断は二択: **コミットを生む→隔離 / read-only（質問・調査・分析・PR レビュー・相談）→現ディレクトリのまま**。

- 完了/中断時は `ExitWorktree`（keep=再開 / remove=破棄）。PR は worktree から `gh pr create` まで完結可。
- **実装後の Codex review ループは承認停止なしで自走すること。push→PR→`@codex review`→**4分間隔ポーリング**→修正→再依頼を都度承認なしで進め、**人間に確認するのは merge 前のみ**。**基本2巡・round1 で P0/P1 が0件なら2巡目省略**・最大2巡。