"""codex_review_poll の単体テスト（gh 呼び出しをモックする）。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from tools.ops import codex_review_poll as poll

CODEX_USER = {"login": "chatgpt-codex-connector[bot]", "type": "Bot"}

REVIEWS_PAYLOAD = [
    [
        {
            "user": CODEX_USER,
            "submitted_at": "2026-09-18T12:05:00Z",
            "html_url": "https://github.com/o/r/pull/1#pullrequestreview-1",
            "body": "P1 lint-imports contract is missing a layer.\nAlso P2 naming nit.",
        },
        {
            "user": CODEX_USER,
            "submitted_at": "2026-09-18T11:00:00Z",
            "html_url": "https://github.com/o/r/pull/1#pullrequestreview-0",
            "body": "stale review posted before the request",
        },
        {
            "user": {"login": "some-human", "type": "User"},
            "submitted_at": "2026-09-18T12:10:00Z",
            "html_url": "https://github.com/o/r/pull/1#pullrequestreview-2",
            "body": "P0 human comment must be ignored",
        },
    ]
]

ISSUE_COMMENTS_PAYLOAD = [
    [
        {
            "user": CODEX_USER,
            "created_at": "2026-09-18T12:06:00Z",
            "html_url": "https://github.com/o/r/pull/1#issuecomment-9",
            "body": "Didn't find any major issues.",
        }
    ]
]

#: login に "codex" を含むだけの別アカウント。応答として数えてはならない。
IMPERSONATOR_PAYLOAD = [
    [
        {
            "user": {"login": "not-codex-really", "type": "User"},
            "created_at": "2026-09-18T12:07:00Z",
            "html_url": "https://github.com/o/r/pull/1#issuecomment-10",
            "body": "P0 looks fine to me, ship it",
        },
        {
            "user": {"login": "codex-fan", "type": "User"},
            "created_at": "2026-09-18T12:08:00Z",
            "html_url": "https://github.com/o/r/pull/1#issuecomment-11",
            "body": "drive-by comment",
        },
    ]
]


def _install_fake_gh(
    monkeypatch: pytest.MonkeyPatch,
    payloads: dict[str, list[Any]],
) -> list[list[str]]:
    """`run_gh` を差し替え、呼ばれた引数を記録する。"""
    calls: list[list[str]] = []

    def fake_run_gh(args: list[str]) -> str:
        calls.append(args)
        if args[0] == "repo":
            return "owner/repo\n"
        path = args[-1]
        for key, payload in payloads.items():
            if path.endswith(key):
                return json.dumps(payload)
        return "[]"

    monkeypatch.setattr(poll, "run_gh", fake_run_gh)
    return calls


def test_collects_codex_items_after_since(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_gh(
        monkeypatch,
        {"/reviews": REVIEWS_PAYLOAD, "/issues/1/comments": ISSUE_COMMENTS_PAYLOAD},
    )

    exit_code = poll.main(
        ["--pr", "1", "--since", "2026-09-18T12:00:00Z", "--once", "--repo", "owner/repo"]
    )
    assert exit_code == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "responded"
    # since より前の review と、codex 以外の author は除外される。
    assert len(result["items"]) == 2

    review, issue_comment = result["items"]
    assert review["channel"] == "review"
    # 同一本文に P1 と P2 があれば、重い方（P1）を採る。
    assert review["priority"] == "P1"
    assert issue_comment["channel"] == "issue_comment"
    assert issue_comment["priority"] is None


def test_accounts_merely_containing_codex_are_not_treated_as_responses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """login に codex を含むだけの別アカウントを応答と誤認しない。

    誤認すると、本物のレビューが届く前に exit 0 してレビュー必須のループが空振りする。
    """
    _install_fake_gh(monkeypatch, {"/issues/1/comments": IMPERSONATOR_PAYLOAD})

    exit_code = poll.main(
        ["--pr", "1", "--since", "2026-09-18T12:00:00Z", "--once", "--repo", "owner/repo"]
    )
    assert exit_code == poll.EXIT_TIMEOUT
    assert json.loads(capsys.readouterr().out)["status"] == "timeout"


def test_codex_login_with_non_bot_account_type_is_rejected() -> None:
    """allowlist の login でも、account type が bot でなければ拒否する。"""
    assert poll.is_codex_author({"user": {"login": "chatgpt-codex-connector[bot]", "type": "Bot"}})
    assert not poll.is_codex_author(
        {"user": {"login": "chatgpt-codex-connector[bot]", "type": "User"}}
    )
    assert not poll.is_codex_author({"user": {"login": "codex", "type": "Bot"}})


def test_times_out_when_codex_does_not_respond(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_gh(monkeypatch, {})

    exit_code = poll.main(
        ["--pr", "7", "--since", "2026-09-18T12:00:00Z", "--once", "--repo", "owner/repo"]
    )
    assert exit_code == poll.EXIT_TIMEOUT

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "timeout"
    assert result["pr"] == 7


def test_gh_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_run_gh(args: list[str]) -> str:
        raise poll.GhError("gh api failed (exit 1): not found")

    monkeypatch.setattr(poll, "run_gh", failing_run_gh)

    exit_code = poll.main(
        ["--pr", "7", "--since", "2026-09-18T12:00:00Z", "--once", "--repo", "owner/repo"]
    )
    assert exit_code == poll.EXIT_GH_ERROR
    assert json.loads(capsys.readouterr().out)["status"] == "error"
