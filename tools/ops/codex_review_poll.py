"""Codex review の応答を待つ最小ポーラー（ADR-0017）。

`gh` CLI 経由で PR の3チャネル（reviews / inline comments / issue comments）を照会し、
`@codex review` の投稿時刻（``--since``）以降に Codex が返した投稿を収集する。

出力（stdout, JSON）と終了コード:

- 応答あり: ``{"status": "responded", "items": [...]}`` / exit 0
- 無応答（timeout）: ``{"status": "timeout", ...}`` / exit 2
- `gh` の失敗: ``{"status": "error", ...}`` / exit 1

標準ライブラリのみを使う。レビューの方針そのものは docs/pr_review_policy.md を参照。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

#: Codex の投稿とみなす author login の allowlist（完全一致・小文字で比較）。
#:
#: 部分一致（`"codex" in login`）にしてはいけない。login に codex を含む任意の
#: アカウントが依頼後にコメントしただけで「レビュー応答あり」と誤判定し、
#: 本物のレビューが届く前に exit 0 してレビュー必須のループを空振りさせるため。
CODEX_LOGINS: frozenset[str] = frozenset(
    {
        "chatgpt-codex-connector[bot]",
        "chatgpt-codex-connector",
    }
)

#: allowlist に加えて要求する account type（GitHub App / bot であること）。
#: 同名の人間アカウントによるなりすましを防ぐ。
CODEX_ACCOUNT_TYPE = "bot"

#: 本文から P0 / P1 / P2 タグを抽出する。最も重い（数字の小さい）ものを採る。
PRIORITY_PATTERN = re.compile(r"\bP([012])\b")

#: 3チャネル。GitHub API のパスと、本文/作成時刻/URL のフィールド名は共通。
CHANNELS: tuple[tuple[str, str], ...] = (
    ("review", "repos/{repo}/pulls/{pr}/reviews"),
    ("inline_comment", "repos/{repo}/pulls/{pr}/comments"),
    ("issue_comment", "repos/{repo}/issues/{pr}/comments"),
)

EXIT_OK = 0
EXIT_GH_ERROR = 1
EXIT_TIMEOUT = 2


class GhError(RuntimeError):
    """`gh` の呼び出しに失敗した。"""


def parse_iso8601(value: str) -> datetime:
    """ISO8601 (UTC) 文字列を tz-aware な datetime にする。

    GitHub API が返す ``2026-09-18T12:00:00Z`` 形式と、``+00:00`` 形式の両方を受ける。
    """
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def run_gh(args: list[str]) -> str:
    """`gh` を実行し stdout を返す。失敗は GhError にする。"""
    try:
        completed = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - 環境依存
        raise GhError("gh CLI not found on PATH") from exc
    if completed.returncode != 0:
        raise GhError(
            f"gh {' '.join(args)} failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout


def detect_repo() -> str:
    """現在のディレクトリから `owner/name` を求める。"""
    out = run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    repo = out.strip()
    if not repo:
        raise GhError("could not determine repository (gh repo view returned nothing)")
    return repo


def fetch_channel(repo: str, pr: int, path_template: str) -> list[dict[str, Any]]:
    """1チャネル分の投稿を取得する。

    `--paginate --slurp` は「ページ（JSON 配列）の配列」を返すため、1段平坦化する。
    """
    path = path_template.format(repo=repo, pr=pr)
    raw = run_gh(["api", "--paginate", "--slurp", path])
    if not raw.strip():
        return []
    pages = json.loads(raw)
    if not isinstance(pages, list):
        raise GhError(f"unexpected payload from {path}: not a list")
    entries: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, list):
            entries.extend(item for item in page if isinstance(item, dict))
        elif isinstance(page, dict):
            entries.append(page)
    return entries


def extract_priority(body: str) -> str | None:
    """本文から P0/P1/P2 を抽出する。複数あれば最も重いものを返す。"""
    found: list[str] = PRIORITY_PATTERN.findall(body or "")
    if not found:
        return None
    return "P" + min(found)


def is_codex_author(entry: dict[str, Any]) -> bool:
    """投稿者が Codex の GitHub App 本体かを判定する。

    login の完全一致（allowlist）と account type の両方を要求する。部分一致にすると、
    login に codex を含む別アカウントの投稿を応答と誤認し、本物のレビューを待たずに
    exit 0 してしまう。
    """
    user = entry.get("user") or {}
    login = str(user.get("login") or "").lower()
    if login not in CODEX_LOGINS:
        return False
    account_type = str(user.get("type") or "").lower()
    # type が取得できない応答も許容するが、値があるときは bot でなければ拒否する。
    return account_type in ("", CODEX_ACCOUNT_TYPE)


def collect_items(
    repo: str,
    pr: int,
    since: datetime,
) -> list[dict[str, Any]]:
    """3チャネルを横断して、`since` 以降の Codex 投稿を集める。"""
    items: list[dict[str, Any]] = []
    for channel, path_template in CHANNELS:
        for entry in fetch_channel(repo, pr, path_template):
            if not is_codex_author(entry):
                continue
            # review は submitted_at、comment は created_at。
            raw_time = entry.get("submitted_at") or entry.get("created_at")
            if not raw_time:
                continue
            created_at = parse_iso8601(str(raw_time))
            if created_at < since:
                continue
            body = str(entry.get("body") or "")
            items.append(
                {
                    "channel": channel,
                    "author": str((entry.get("user") or {}).get("login") or ""),
                    "created_at": created_at.isoformat().replace("+00:00", "Z"),
                    "url": str(entry.get("html_url") or ""),
                    "body": body,
                    "priority": extract_priority(body),
                }
            )
    items.sort(key=lambda item: (item["created_at"], item["channel"]))
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex_review_poll",
        description="Poll a PR for Codex review responses posted after a given time.",
    )
    parser.add_argument("--pr", type=int, required=True, help="pull request number")
    parser.add_argument(
        "--since",
        required=True,
        help="ISO8601 UTC timestamp of the '@codex review' comment (e.g. 2026-09-18T12:00:00Z)",
    )
    parser.add_argument("--interval", type=int, default=240, help="poll interval in seconds")
    parser.add_argument("--timeout", type=int, default=1200, help="give up after this many seconds")
    parser.add_argument(
        "--once",
        action="store_true",
        help="query the three channels once and exit (dry run; no waiting)",
    )
    parser.add_argument("--repo", default=None, help="owner/name (default: detected via gh)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        since = parse_iso8601(args.since)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": f"invalid --since: {exc}"}))
        return EXIT_GH_ERROR

    deadline = time.monotonic() + max(args.timeout, 0)
    polls = 0

    try:
        repo = args.repo or detect_repo()
        while True:
            polls += 1
            items = collect_items(repo, args.pr, since)
            if items:
                print(json.dumps({"status": "responded", "items": items}, ensure_ascii=False))
                return EXIT_OK
            if args.once or time.monotonic() >= deadline:
                break
            time.sleep(min(args.interval, max(deadline - time.monotonic(), 0)))
    except GhError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return EXIT_GH_ERROR

    print(
        json.dumps(
            {
                "status": "timeout",
                "pr": args.pr,
                "since": since.isoformat().replace("+00:00", "Z"),
                "polls": polls,
            },
            ensure_ascii=False,
        )
    )
    return EXIT_TIMEOUT


if __name__ == "__main__":
    sys.exit(main())
