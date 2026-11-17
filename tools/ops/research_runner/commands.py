"""実行器が起動してよいコマンドの許可の一覧（D25 §4.6 の `COST_RISK`、§2.2・§2.5）。

D25 §4.6 の表: 「実行器が起動するコマンドは許可の一覧（第2.2節の表のコマンドと `git`・`gh`・
`claude`）だけで、一覧に無いコマンドが要る段に来た」なら全体の停止（`COST_RISK`）。費用 0・費用の
発生は人間の go（D25 §2.5 の表）を、起動の手前で機械的に守る。

- 段が組み立てて切り離して起動する処理（審査・固定の前の確かめ・最終検証・再現）は、起動の直前に
  この一覧で確かめる（`Runner._spawn`）。一覧に無ければ起動せず、当たったカードを添えて全体の停止。
- 一覧の `odyssey-fx experiment` の部分命令は、D25 §2.2 の表の `run`（実行の包みの中）・`report`・
  `ledger`・`reproduce`・`review`・`final-validate` と、D25 §6.5（D25-17 (a)、PR #190）で実行器が
  起動する固定のコマンド `fix`。
- 中身は読まない（コマンドの形だけを見る）。費用を発生させない・外に問い合わせない。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

__all__ = ["ALLOWED_EXPERIMENT_COMMANDS", "ALLOWED_PROGRAMS", "unlisted_command"]

#: `odyssey-fx experiment` の許可した部分命令（D25 §2.2・§6.5）。
ALLOWED_EXPERIMENT_COMMANDS: Final = frozenset(
    {"run", "report", "ledger", "reproduce", "review", "final-validate", "fix"}
)
#: `odyssey-fx` の外で許可したプログラム（D25 §4.6 の表の `git`・`gh`・`claude`）。
ALLOWED_PROGRAMS: Final = frozenset({"git", "gh", "claude"})


def unlisted_command(command: Sequence[str], odyssey: Sequence[str]) -> str | None:
    """`command` が許可の一覧に無ければ、その名前（問いの背景に書く短い文字列）を返す。

    `odyssey` は `odyssey-fx` を走らせるコマンドの頭（既定 `uv run --frozen odyssey-fx`）。
    一覧にあれば `None`。
    """
    parts = list(command)
    head = list(odyssey)
    if not parts:
        return "（空のコマンド）"
    if head and parts[: len(head)] == head:
        rest = parts[len(head) :]
        if len(rest) >= 2 and rest[0] == "experiment" and rest[1] in ALLOWED_EXPERIMENT_COMMANDS:
            return None
        return "odyssey-fx " + " ".join(rest[:2])
    if parts[0] in ALLOWED_PROGRAMS:
        return None
    return parts[0]
