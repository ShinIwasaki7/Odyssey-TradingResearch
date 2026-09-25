"""Codex が利用上限の巡だけ、別プロセスの Claude 敵対レビューで代替する起動スクリプト。

レビューの規則は docs/pr_review_policy.md（§4.4・§4.6 の状態3）が正本。本スクリプトは
その「Claude レーン」を1巡分だけ実行する。

1. ``gh pr view`` で PR 本文・head・base を取り、作業ディレクトリが head と一致するか確かめる。
2. ``git diff origin/<base>...<head>`` で差分を取る。
3. PR 本文から「レビュー対象範囲」と「仮置き」の節だけを抜き出してプロンプトを組み立てる
   （作者の説明文や作業セッションの会話は渡さない）。
4. ``claude -p --agent adversarial-reviewer`` を別プロセスで起動する。権限の確認で止まらないよう
   ``--permission-mode dontAsk`` とし、``--allowedTools`` で読み取り専用の道具に絞る。
5. 結果の JSON を解析し、``[claude-review 第n巡] head <sha>`` の見出しで PR にコメントする。

出力（stdout, JSON）は codex_review_poll.py と同じ形（``items`` の各要素が
``channel`` / ``author`` / ``created_at`` / ``url`` / ``body`` / ``priority`` を持つ）。

終了コード: 0 = 結果あり / 1 = gh・git・claude の失敗 / 2 = タイムアウト。
標準ライブラリのみを使う。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AGENT_NAME = "adversarial-reviewer"
AUTHOR = "claude-adversarial-reviewer"
CHANNEL = "claude_review"

#: claude に見せる道具（組み込みの道具の集合から、これ以外を外す）。
AVAILABLE_TOOLS = "Read,Grep,Glob,Bash,Agent"

#: 確認なしで許可する道具。Bash は読み取り・限定テストの形だけ。
#: ``--permission-mode dontAsk`` と組み合わせ、ここに無い呼び出しは確認を出さずに拒否させる。
ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Agent",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(gh pr view:*)",
    "Bash(uv run pytest:*)",
    "Bash(uv run python -c:*)",
)

DEFAULT_TIMEOUT_SECONDS = 1800

#: プロンプトに埋め込む差分の上限（文字数）。超えた分は reviewer に ``git diff`` で読ませる。
MAX_DIFF_CHARS = 200_000

SCOPE_KEYWORD = "レビュー対象範囲"
PROVISIONAL_KEYWORD = "仮置き"

PRIORITIES: tuple[str, ...] = ("P0", "P1", "P2")
REQUIRED_FINDING_KEYS: tuple[str, ...] = ("priority", "title", "file", "line", "failure_scenario")

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
FENCE_PATTERN = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_TIMEOUT = 2


class ReviewError(RuntimeError):
    """gh / git / claude の呼び出し、または結果の解釈に失敗した。"""


@dataclass(frozen=True)
class PullRequest:
    number: int
    body: str
    head: str
    base: str


def run_command(
    args: list[str],
    cwd: Path,
    *,
    input_text: str | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """コマンドを実行して stdout を返す。失敗は ReviewError にする。

    タイムアウト（``subprocess.TimeoutExpired``）はそのまま呼び出し元へ伝える。
    """
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ReviewError(f"{args[0]} not found on PATH") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ReviewError(
            f"{' '.join(args[:3])} failed (exit {completed.returncode}): {detail[:2000]}"
        )
    return completed.stdout


def fetch_pull_request(pr: int, cwd: Path) -> PullRequest:
    raw = run_command(["gh", "pr", "view", str(pr), "--json", "body,headRefOid,baseRefName"], cwd)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"gh pr view returned non-JSON output: {exc}") from exc
    head = str(data.get("headRefOid") or "")
    base = str(data.get("baseRefName") or "")
    if not head or not base:
        raise ReviewError("gh pr view did not return headRefOid / baseRefName")
    return PullRequest(number=pr, body=str(data.get("body") or ""), head=head, base=base)


def ensure_checkout_matches(head: str, cwd: Path) -> None:
    """作業ディレクトリが PR の head を指しているかを確かめる。

    違う head を読ませると、レビューした内容と見出しの head が食い違う（方針 §4.6 規則5）。
    """
    local = run_command(["git", "rev-parse", "HEAD"], cwd).strip()
    if local != head:
        raise ReviewError(
            f"working directory is at {local}, but PR head is {head}; "
            "check out the PR head before running the Claude review"
        )


def fetch_diff(base: str, head: str, cwd: Path) -> str:
    run_command(["git", "fetch", "--quiet", "origin", base], cwd)
    return run_command(["git", "diff", f"origin/{base}...{head}"], cwd)


def extract_section(body: str, keyword: str) -> str | None:
    """見出しに ``keyword`` を含む Markdown 節を、次の同格以上の見出しの手前まで返す。

    該当する節が複数あればすべてを連結する。無ければ None。
    """
    lines = body.splitlines()
    sections: list[str] = []
    index = 0
    while index < len(lines):
        match = HEADING_PATTERN.match(lines[index])
        if match and keyword in match.group(2):
            level = len(match.group(1))
            end = index + 1
            while end < len(lines):
                other = HEADING_PATTERN.match(lines[end])
                if other and len(other.group(1)) <= level:
                    break
                end += 1
            sections.append("\n".join(lines[index:end]).strip())
            index = end
            continue
        index += 1
    if not sections:
        return None
    return "\n\n".join(sections)


def build_prompt(pr: PullRequest, round_no: int, body: str, diff: str) -> str:
    """reviewer に渡すプロンプトを組み立てる。

    PR 本文からは範囲表と仮置きの節だけを渡す。範囲表が無ければレビューできないので失敗にする
    （方針 §3.1: 範囲を伝えずに依頼すると収束しない）。
    """
    scope = extract_section(body, SCOPE_KEYWORD)
    if scope is None:
        raise ReviewError(
            f"PR body has no heading containing '{SCOPE_KEYWORD}'; "
            "the Claude review needs the scope table (docs/pr_review_policy.md §3.1)"
        )
    provisional = extract_section(body, PROVISIONAL_KEYWORD) or "（PR 本文に仮置きの節が無い）"

    if len(diff) > MAX_DIFF_CHARS:
        diff_block = (
            diff[:MAX_DIFF_CHARS]
            + f"\n\n[差分が {len(diff)} 文字あるため {MAX_DIFF_CHARS} 文字で打ち切った。"
            + f"残りは `git diff origin/{pr.base}...{pr.head}` で自分で読むこと]"
        )
    else:
        diff_block = diff

    return "\n".join(
        [
            f"PR #{pr.number} の第{round_no}巡を敵対レビューせよ。"
            "Codex が利用上限だったため、その代わりの独立レビューである。",
            f"head: {pr.head}",
            f"base: origin/{pr.base}",
            "",
            "## 指示",
            "- `AGENTS.md` の `## Code Review Rules` と `docs/pr_review_policy.md` §3 は"
            "自分で読むこと。範囲表や差分が参照する設計文書も自分で読むこと。",
            "- 下の範囲表の外は報告しない。候補は1件ずつ review-verifier に検証させ、"
            "再現できたものだけを出力する。",
            "- 最後のメッセージは、エージェント定義に書かれた JSON オブジェクトだけにする。",
            "",
            "## PR 本文: レビュー対象範囲",
            scope,
            "",
            "## PR 本文: 仮置き",
            provisional,
            "",
            "## 差分",
            "```diff",
            diff_block,
            "```",
        ]
    )


def build_claude_command() -> list[str]:
    return [
        "claude",
        "-p",
        "--agent",
        AGENT_NAME,
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--tools",
        AVAILABLE_TOOLS,
        "--allowedTools",
        *ALLOWED_TOOLS,
    ]


def run_claude(prompt: str, cwd: Path, timeout: float) -> str:
    """claude を別プロセスで起動し、stdout（``--output-format json``）を返す。

    プロンプトは引数の長さ制限を避けるため標準入力で渡す。
    """
    env = dict(os.environ)
    # 親の Claude Code セッションの印を消し、独立したセッションとして起動する。
    env.pop("CLAUDECODE", None)
    return run_command(build_claude_command(), cwd, input_text=prompt, timeout=timeout, env=env)


def extract_json_object(text: str) -> dict[str, Any]:
    """reviewer の最終メッセージから JSON オブジェクトを取り出す。

    指示どおり JSON だけが返る場合に加え、コードフェンスや前置きが付いた場合も受ける。
    """
    stripped = text.strip()
    fenced = FENCE_PATTERN.match(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ReviewError(f"reviewer output is not a JSON object: {text[:500]!r}")


def parse_claude_output(stdout: str) -> dict[str, Any]:
    """``claude -p --output-format json`` の出力から reviewer の JSON を取り出す。"""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"claude output is not JSON: {stdout[:500]!r}") from exc
    if not isinstance(envelope, dict):
        raise ReviewError("claude output is not a JSON object")
    if envelope.get("is_error") or envelope.get("subtype") not in (None, "success"):
        raise ReviewError(
            f"claude reported an error (subtype={envelope.get('subtype')!r}): "
            f"{str(envelope.get('result') or '')[:500]}"
        )
    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = envelope.get("result")
    if not isinstance(result, str) or not result.strip():
        raise ReviewError("claude output has no result text")
    return extract_json_object(result)


def normalize_findings(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str, int]:
    """指摘を検証し、(検証済みの指摘, summary, 検証されずに除いた件数) を返す。

    形が崩れた指摘は reviewer の失敗として ReviewError にする（黙って捨てない）。
    """
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise ReviewError("reviewer JSON has no 'findings' list")
    summary = str(payload.get("summary") or "")
    findings: list[dict[str, Any]] = []
    dropped = 0
    for index, raw in enumerate(raw_findings):
        if not isinstance(raw, dict):
            raise ReviewError(f"finding #{index} is not an object")
        missing = [key for key in REQUIRED_FINDING_KEYS if key not in raw]
        if missing:
            raise ReviewError(f"finding #{index} lacks {', '.join(missing)}")
        priority = str(raw["priority"]).strip().upper()
        if priority not in PRIORITIES:
            raise ReviewError(f"finding #{index} has invalid priority {raw['priority']!r}")
        if raw.get("verified") is not True:
            dropped += 1
            continue
        findings.append(
            {
                "priority": priority,
                "title": str(raw["title"]),
                "file": str(raw["file"]),
                "line": raw["line"],
                "failure_scenario": str(raw["failure_scenario"]),
                "design_ref": str(raw.get("design_ref") or ""),
                "verified": True,
            }
        )
    findings.sort(key=lambda item: PRIORITIES.index(item["priority"]))
    return findings, summary, dropped


def highest_priority(findings: list[dict[str, Any]]) -> str | None:
    if not findings:
        return None
    return min((str(item["priority"]) for item in findings), key=PRIORITIES.index)


def comment_heading(round_no: int, head: str) -> str:
    return f"[claude-review 第{round_no}巡] head {head}"


def format_comment(
    round_no: int,
    head: str,
    findings: list[dict[str, Any]],
    summary: str,
    dropped: int,
) -> str:
    """PR コメントの本文を作る。見出し行は方針 §4.6.5 手順1 の照合に使う。"""
    lines = [
        comment_heading(round_no, head),
        "",
        "Codex が利用上限だったため、この巡は別プロセスの Claude 敵対レビュー"
        "（代替レビュー）で実施した（docs/pr_review_policy.md §4.4）。"
        "範囲は PR 本文のレビュー対象範囲の表。"
        "検証役が再現できた指摘だけを載せている。",
        "",
    ]
    if findings:
        lines.append(f"### 指摘 {len(findings)} 件")
        for number, item in enumerate(findings, start=1):
            lines.append("")
            lines.append(f"{number}. **{item['priority']}** {item['title']}")
            lines.append(f"   - 場所: `{item['file']}:{item['line']}`")
            lines.append(f"   - 失敗シナリオ: {item['failure_scenario']}")
            if item["design_ref"]:
                lines.append(f"   - 根拠節: {item['design_ref']}")
    else:
        lines.append("### 指摘 0 件")
        lines.append("")
        lines.append("レビュー対象範囲の中で、再現できた指摘はありません。")
    lines.append("")
    lines.append(f"要約: {summary or '（なし）'}")
    if dropped:
        lines.append(f"（検証で再現できず除いた候補: {dropped} 件）")
    return "\n".join(lines) + "\n"


def post_comment(pr: int, body: str, cwd: Path) -> str:
    """``gh pr comment --body-file`` で投稿し、コメントの URL を返す。"""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        path = handle.name
    try:
        out = run_command(["gh", "pr", "comment", str(pr), "--body-file", path], cwd)
    finally:
        os.unlink(path)
    return out.strip()


def build_output(
    round_no: int,
    head: str,
    body: str,
    url: str,
    findings: list[dict[str, Any]],
    summary: str,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "status": "responded",
        "round": round_no,
        "head": head,
        "items": [
            {
                "channel": CHANNEL,
                "author": AUTHOR,
                "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "url": url,
                "body": body,
                "priority": highest_priority(findings),
                "findings": findings,
                "summary": summary,
            }
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude_review",
        description="Run the adversarial Claude review for one round when Codex is rate-limited.",
    )
    parser.add_argument("--pr", type=int, required=True, help="pull request number")
    parser.add_argument("--round", type=int, required=True, help="review round number n")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path.cwd(),
        help="working directory with the PR head checked out (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the prompt and exit without launching claude",
    )
    parser.add_argument("--no-post", action="store_true", help="do not comment on the PR")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="give up on claude after this many seconds (default: 1800)",
    )
    return parser


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cwd: Path = args.base_dir

    try:
        pr = fetch_pull_request(args.pr, cwd)
        ensure_checkout_matches(pr.head, cwd)
        diff = fetch_diff(pr.base, pr.head, cwd)
        prompt = build_prompt(pr, args.round, pr.body, diff)
        if args.dry_run:
            print(prompt)
            return EXIT_OK
        stdout = run_claude(prompt, cwd, args.timeout)
        payload = parse_claude_output(stdout)
        findings, summary, dropped = normalize_findings(payload)
        body = format_comment(args.round, pr.head, findings, summary, dropped)
        url = "" if args.no_post else post_comment(args.pr, body, cwd)
    except subprocess.TimeoutExpired:
        _print_json(
            {"status": "timeout", "pr": args.pr, "round": args.round, "timeout": args.timeout}
        )
        return EXIT_TIMEOUT
    except ReviewError as exc:
        _print_json({"status": "error", "pr": args.pr, "round": args.round, "error": str(exc)})
        return EXIT_FAILURE

    _print_json(build_output(args.round, pr.head, body, url, findings, summary, datetime.now(UTC)))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
