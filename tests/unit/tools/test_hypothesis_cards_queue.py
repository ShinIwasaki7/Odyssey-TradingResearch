"""置き場と移動・取り出しの順番・AI のカードの上限と、小さなコマンドの試験。

設計は D25 §3.3・§3.6・§3.7（決定 151・153、仮置き 5）。git の履歴はテストの中の一時の
リポジトリで作る。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from odyssey_fx.app.config.loader import load_yaml_mapping
from tests.fixtures.hypothesis_cards import card_bytes, card_data, workspace
from tools.ops.hypothesis_cards import cli
from tools.ops.hypothesis_cards.queue import (
    AI_CARD_LIMIT,
    QueuedCard,
    ai_capacity,
    available_card_id,
    card_path,
    first_seen_times,
    may_take,
    move,
    next_card,
    order_cards,
    outcome_bytes,
    queue_from,
    queued_cards,
)
from tools.ops.hypothesis_cards.repo import DictView, ToolFailure


def test_humans_first_then_oldest_then_identifier() -> None:
    cards = [
        QueuedCard("a-20261101-old-ai", "ai", 100),
        QueuedCard("h-20261130-new-human", "human", 300),
        QueuedCard("h-20261101-old-human", "human", 200),
        QueuedCard("h-20261102-same-b", "human", 200),
    ]
    ordered = [card.card_id for card in order_cards(cards)]
    assert ordered == [
        "h-20261101-old-human",
        "h-20261102-same-b",
        "h-20261130-new-human",
        "a-20261101-old-ai",
    ]
    assert next_card([]) is None


def test_taking_needs_a_free_slot_no_waiting_card_and_no_global_stop() -> None:
    assert may_take(free_slots=1, waiting_to_start=0, globally_stopped=False)
    assert not may_take(free_slots=0, waiting_to_start=0, globally_stopped=False)
    assert not may_take(free_slots=2, waiting_to_start=1, globally_stopped=False)
    assert not may_take(free_slots=2, waiting_to_start=0, globally_stopped=True)


def test_the_ai_cap_counts_only_queued_ai_cards() -> None:
    files = {
        card_path("queue", "a-20261101-one"): b"",
        card_path("queue", "a-20261102-two"): b"",
        card_path("queue", "h-20261102-human"): b"",
        card_path("taken", "a-20261103-three"): b"",
        "research/hypotheses/queue/notes.txt": b"",
    }
    view = DictView(files)
    assert queued_cards(view) == ("a-20261101-one", "a-20261102-two", "h-20261102-human")
    assert ai_capacity(view) == AI_CARD_LIMIT - 2
    files[card_path("queue", "a-20261104-four")] = b""
    files[card_path("queue", "a-20261105-five")] = b""
    assert ai_capacity(DictView(files)) == 0


def test_moves_follow_the_table_and_outcomes_read_back() -> None:
    assert move("h-20261101-x-y", "queue", "taken") == (
        "research/hypotheses/queue/h-20261101-x-y.yaml",
        "research/hypotheses/taken/h-20261101-x-y.yaml",
    )
    with pytest.raises(ValueError):
        move("h-20261101-x-y", "done", "queue")
    body = outcome_bytes(
        "h-20261101-x-y", reason="CARD_GRID_SIZE", at="2026-11-21T00:00:00Z", last_version=0
    )
    loaded = load_yaml_mapping(Path("x.outcome.yaml"), text=body.decode())
    assert loaded == {
        "schema_version": 1,
        "card": "h-20261101-x-y",
        "reason": "CARD_GRID_SIZE",
        "at": "2026-11-21T00:00:00Z",
        "last_version": 0,
    }


def test_ai_identifiers_get_a_number_but_human_ones_do_not() -> None:
    view = DictView(
        {card_path("done", "a-20261101-ema"): b"", card_path("queue", "a-20261101-ema-2"): b""}
    )
    assert available_card_id(view, "a-20261101-ema") == "a-20261101-ema-3"
    assert available_card_id(view, "a-20261101-new") == "a-20261101-new"
    with pytest.raises(ToolFailure):
        available_card_id(DictView({card_path("done", "h-20261101-ema"): b""}), "h-20261101-ema")


def _git(repo: Path, *args: str, when: int | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        stamp = f"@{when} +0000"
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,  # noqa: S607
    )
    return completed.stdout


def _add(repo: Path, card_id: str, when: int) -> None:
    target = repo / card_path("queue", card_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", card_id, when=when)


def test_first_seen_times_follow_the_first_parent_history(tmp_path: Path) -> None:
    """枝で先に足しても、main に merge した時刻が鍵になる（first-parent の履歴。仮置き 5）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _add(repo, "h-20261101-first", 1_700_000_100)
    _git(repo, "checkout", "-q", "-b", "side")
    _add(repo, "h-20261101-side", 1_700_000_200)
    _git(repo, "checkout", "-q", "main")
    _add(repo, "h-20261101-third", 1_700_000_300)
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge side", when=1_700_000_400)
    times = first_seen_times(
        repo, "main", ["h-20261101-first", "h-20261101-side", "h-20261101-third"]
    )
    assert times == {
        "h-20261101-first": 1_700_000_100,
        "h-20261101-side": 1_700_000_400,
        "h-20261101-third": 1_700_000_300,
    }
    ordered = [card.card_id for card in order_cards(queue_from(times))]
    assert ordered == ["h-20261101-first", "h-20261101-third", "h-20261101-side"]
    with pytest.raises(ToolFailure):
        first_seen_times(repo, "main", ["h-20261101-missing"])


def test_the_check_command_reports_pass_and_rejection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = workspace(tmp_path)
    good = root / card_path("queue", "h-20261120-dual-ema")
    good.parent.mkdir(parents=True)
    good.write_bytes(card_bytes(card_data()))
    assert cli.main(["--repo", str(root), "check", str(good)]) == 0
    assert "合格" in capsys.readouterr().out
    data = card_data(title="big-grid")
    data["grid"]["axes"][0]["values"] = [50, 100, 150, 200]
    bad = root / card_path("queue", "h-20261120-big-grid")
    bad.write_bytes(card_bytes(data))
    assert cli.main(["--repo", str(root), "check", str(bad)]) == 1
    assert "CARD_GRID_SIZE" in capsys.readouterr().out
    assert cli.main(["--repo", str(root), "check", str(root / "missing.yaml")]) == 2
