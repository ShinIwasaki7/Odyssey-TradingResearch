"""置き場と移動・取り出しの順番・AI のカードの上限・終わり方の記録（D25 §3.3・§3.6・§3.7）。

- **置き場**（D23 §5.2）: `research/hypotheses/queue/`（待ち）・`taken/`（一巡の途中）・`done/`
  （一巡が終わった）。移動は定型 PR の中で行う（`queue/` → `taken/` は事前登録の PR、→ `done/` は
  一巡の報告の PR）。移動ではカードのバイト列を変えない（凍結。D25 §3.4。CI の検査 3 が見る）。
- **取り出しの順番**（決定 151、仮置き 5）: 人間の口のカードを古い順に先に、無ければ AI の口の
  カードを古い順に。「古い順」の鍵は、カードのファイルが main の first-parent の履歴で初めて現れた
  コミットの時刻（同じなら識別子の辞書順）。`created_at` は順番に使わない。
- **いつ取り出すか**（第3.6節の 4・5）: 実行の空きが 1 つあり、起動待ちのカードが無く、全体の
  停止の間でないときに 1 枚だけ（`may_take`）。
- **AI のカードの上限**（決定 153、仮置き 15）: 待ち行列の AI の口のカードは同時に 3 枚まで。
  発想役が 1 回に書けるのは空きの枚数まで。
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.ops.hypothesis_cards.card import CARD_ID_PATTERN, MOUTHS
from tools.ops.hypothesis_cards.emit import dump_document
from tools.ops.hypothesis_cards.repo import CARD_PLACES, HYPOTHESES_DIR, RepoView, ToolFailure

__all__ = [
    "AI_CARD_LIMIT",
    "QueuedCard",
    "ai_capacity",
    "available_card_id",
    "card_path",
    "first_seen_times",
    "may_take",
    "move",
    "next_card",
    "order_cards",
    "outcome_bytes",
    "outcome_path",
    "queue_from",
    "queued_cards",
]

#: 待ち行列の AI の口のカードの上限（決定 153）。
AI_CARD_LIMIT: Final = 3


def card_path(place: str, card_id: str) -> str:
    if place not in CARD_PLACES:
        raise ValueError(f"置き場は {CARD_PLACES} のどれか（{place!r}）")
    return f"{HYPOTHESES_DIR}/{place}/{card_id}.yaml"


def outcome_path(card_id: str) -> str:
    """終わり方の短い追記の別ファイル（D25 §2.3）。"""
    return f"{HYPOTHESES_DIR}/done/{card_id}.outcome.yaml"


_MOVES: Final = {("queue", "taken"), ("queue", "done"), ("taken", "done")}


def move(card_id: str, source: str, target: str) -> tuple[str, str]:
    """移動の前後のパス（第3.3節の表の移動だけ。中身は変えない）。"""
    if (source, target) not in _MOVES:
        raise ValueError(f"第3.3節に無い移動（{source} → {target}）")
    return card_path(source, card_id), card_path(target, card_id)


def outcome_bytes(card_id: str, *, reason: str, at: str, last_version: int) -> bytes:
    """`done/<カード>.outcome.yaml` の本文（理由の識別子・時刻・最後の版）。

    項目は PR 6 の検査 5（記録の書式）の形に合わせる。
    """
    return dump_document(
        {
            "schema_version": 1,
            "card": card_id,
            "reason": reason,
            "at": at,
            "last_version": last_version,
        }
    )


@dataclass(frozen=True, slots=True)
class QueuedCard:
    """待ち行列のカード 1 枚（`first_seen` は first-parent で初めて現れた時刻。UNIX 秒）。"""

    card_id: str
    mouth: str
    first_seen: int


def queued_cards(view: RepoView) -> tuple[str, ...]:
    """待ち行列のカードの識別子（ファイル名から。辞書順）。"""
    prefix = f"{HYPOTHESES_DIR}/queue/"
    found = []
    for path in view.list(prefix):
        name = Path(path).name
        if "/" in path[len(prefix) :] or not name.endswith(".yaml"):
            continue
        card_id = name[: -len(".yaml")]
        if CARD_ID_PATTERN.fullmatch(card_id):
            found.append(card_id)
    return tuple(sorted(found))


def _mouth_of(card_id: str) -> str:
    letter = card_id[0]
    for mouth, short in MOUTHS.items():
        if short == letter:
            return mouth
    raise ValueError(f"カードの識別子の口の文字でない: {card_id!r}")


def first_seen_times(repo: Path, ref: str, card_ids: Iterable[str]) -> dict[str, int]:
    """待ち行列のカードのファイルが `ref` の first-parent の履歴で初めて現れたコミットの時刻。

    カードのファイルごとに `git log --first-parent --reverse --format=%ct <ref> -- <パス>` の最初の
    行を読む（merge のコミットは最初の親との差分で現れる）。履歴に無ければ道具の失敗。
    """
    times: dict[str, int] = {}
    for card_id in card_ids:
        path = card_path("queue", card_id)
        completed = subprocess.run(  # noqa: S603 - 固定の git の部分命令とカードのパス
            [
                "git",
                "-C",
                str(repo),
                "log",
                "--first-parent",
                "--reverse",
                "--format=%ct",
                ref,
                "--",
                path,
            ],  # noqa: S607, E501
            capture_output=True,
            text=True,
            check=False,
        )
        lines = completed.stdout.split()
        if completed.returncode != 0 or not lines:
            raise ToolFailure(f"{path} が {ref} の first-parent の履歴に無い")
        times[card_id] = int(lines[0])
    return times


def order_cards(cards: Sequence[QueuedCard]) -> tuple[QueuedCard, ...]:
    """取り出す順（人間の口を先に、各口の中は初めて現れた時刻の古い順、同じなら識別子の辞書順）。"""
    priority = {"human": 0, "ai": 1}
    return tuple(
        sorted(cards, key=lambda card: (priority[card.mouth], card.first_seen, card.card_id))
    )


def next_card(cards: Sequence[QueuedCard]) -> QueuedCard | None:
    ordered = order_cards(cards)
    return ordered[0] if ordered else None


def queue_from(times: Mapping[str, int]) -> tuple[QueuedCard, ...]:
    """識別子 → 初めて現れた時刻 から待ち行列のカードの並びを作る（口は識別子の 1 文字目）。"""
    return tuple(QueuedCard(card_id, _mouth_of(card_id), seen) for card_id, seen in times.items())


def may_take(*, free_slots: int, waiting_to_start: int, globally_stopped: bool) -> bool:
    """取り出してよいか（第3.6節の 4・5）。"""
    return free_slots >= 1 and waiting_to_start == 0 and not globally_stopped


def ai_capacity(view: RepoView) -> int:
    """待ち行列の AI の口のカードの空き（3 − 枚数。0 未満にしない）。"""
    count = sum(1 for card_id in queued_cards(view) if card_id.startswith("a-"))
    return max(0, AI_CARD_LIMIT - count)


def available_card_id(view: RepoView, card_id: str) -> str:
    """AI の口のカードの識別子が既にあれば、`title` の後ろに `-2`・`-3` … を足す（第3.2節）。

    人間の口のカードは足さない（CI が落とす）。足した結果が短い名前の上限（24 字）を超えるなら
    道具の失敗。返す識別子では、どの置き場にもカードが無い。実験と戦略の識別子の重なりは
    `screening` の検査 1 が見る。
    """
    matched = CARD_ID_PATTERN.fullmatch(card_id)
    if matched is None:
        raise ValueError(f"カードの識別子の形でない: {card_id!r}")

    def taken(candidate: str) -> bool:
        return any(view.read(card_path(place, candidate)) is not None for place in CARD_PLACES)

    if not taken(card_id):
        return card_id
    if matched["mouth"] != "a":
        raise ToolFailure(f"人間の口のカードの識別子 {card_id} が既にある（CI が落とす）")
    for suffix in range(2, 100):
        title = f"{matched['title']}-{suffix}"
        candidate = f"a-{matched['date']}-{title}"
        if CARD_ID_PATTERN.fullmatch(candidate) is None:
            raise ToolFailure(f"{card_id} の短い名前に番号を足すと 24 字を超える")
        if not taken(candidate):
            return candidate
    raise ToolFailure(f"{card_id} に足せる番号が無い")  # pragma: no cover
