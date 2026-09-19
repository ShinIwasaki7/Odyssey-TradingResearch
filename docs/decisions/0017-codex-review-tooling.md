# ADR-0017: pr-review スキルを書き換え、最小 poller を新規作成する

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 D-3

## 文脈

現在の pr-review スキルは旧リポジトリ（claude-trading-system）向けで、旧リポジトリ名の description、存在しないレビュー方針文書、存在しない `tools/ops/codex_review_poll.py`、旧プロジェクト固有の Gate 分類を含む。

## 決定

旧ツールをそのまま移植せず、次の構成へ置き換える。

- 本リポジトリ用のレビュー方針を新規定義する。
- pr-review スキルから旧プロジェクト固有語彙を除去する。
- GitHub 上の review、inline comment、issue comment を取得する最小 poller を本リポジトリ用に作る。
- 4分間隔、初回 P0/P1 なしなら終了、最大2巡という運用は維持する。
- merge 判断は人間に残す。

## 影響

- 段階−1の作業項目。
