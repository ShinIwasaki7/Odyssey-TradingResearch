"""仮説カードの道具の小さなコマンド（人間がカードを置く前の確かめと、取り出しの順番の表示）。

使い方（リポジトリの根で）::

    uv run python -m tools.ops.hypothesis_cards check research/hypotheses/queue/<カード>.yaml
    uv run python -m tools.ops.hypothesis_cards order --ref origin/main

- `check`: D25 §3.5 の検査 1〜7（準備の確かめを除く）を当て、合否・理由の識別子・食い違いを
  表示する。合格なら版 1 の生成物のパスと SHA-256 も表示する（ファイルは書かない）。
- `order`: 待ち行列のカードを取り出す順に、口と識別子を表示する（決定 151、仮置き 5）。

終了コード: 0 = 合格（`order` は表示できた）、1 = 不合格（棄却の型）、2 = 入力・道具の誤り。
成績の数値は扱わない。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.ops.hypothesis_cards.queue import first_seen_times, order_cards, queue_from, queued_cards
from tools.ops.hypothesis_cards.repo import DirView, ToolFailure
from tools.ops.hypothesis_cards.screening import screen_card


def _check(repo: Path, card: Path) -> int:
    absolute = card if card.is_absolute() else repo / card
    if not absolute.is_file():
        print(f"カードが無い: {card}", file=sys.stderr)
        return 2
    relative = absolute.resolve().relative_to(repo.resolve()).as_posix()
    try:
        result = screen_card(DirView(repo), absolute.read_bytes(), path=relative)
    except ToolFailure as exc:
        print(f"TOOL_FAILURE: {exc}", file=sys.stderr)
        return 2
    if not result.passed:
        print(f"不合格: {result.reason}")
        for problem in result.problems:
            print(f"  - {problem}")
        return 1
    print("合格")
    assert result.prereg is not None  # noqa: S101 - 合格は生成物を持つ
    for path, body in sorted(result.prereg.files.items()):
        print(f"  {path} {hashlib.sha256(body).hexdigest()}")
    return 0


def _order(repo: Path, ref: str) -> int:
    try:
        times = first_seen_times(repo, ref, queued_cards(DirView(repo)))
    except ToolFailure as exc:
        print(f"TOOL_FAILURE: {exc}", file=sys.stderr)
        return 2
    for position, card in enumerate(order_cards(queue_from(times)), start=1):
        print(f"{position}. {card.mouth} {card.card_id}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ops.hypothesis_cards")
    parser.add_argument("--repo", type=Path, default=Path("."), help="リポジトリの根")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="カードの機械の検査（D25 §3.5）")
    check.add_argument("card", type=Path)
    order = sub.add_parser("order", help="取り出しの順番（D25 §3.6）")
    order.add_argument("--ref", default="HEAD", help="main の first-parent の履歴を読む参照")
    args = parser.parse_args(argv)
    if args.command == "check":
        return _check(args.repo, args.card)
    return _order(args.repo, args.ref)
