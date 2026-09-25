"""claude_review の単体テスト（gh・git・claude の呼び出しをモックする）。"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tools.ops import claude_review as cr

HEAD = "a" * 40

PR_BODY = """## 概要

作者の説明。レビュー役には渡さない。

## レビュー対象範囲

| 対象 | 箇所 |
|---|---|
| tools/ops/claude_review.py | 全体 |

### 補足

表の外は対象外。

## 仮置き

なし

## 検証

uv run pytest
"""

VERIFIED_FINDING: dict[str, Any] = {
    "priority": "P1",
    "title": "timeout is ignored",
    "file": "tools/ops/claude_review.py",
    "line": 42,
    "failure_scenario": "--timeout 1 -> claude keeps running",
    "design_ref": "",
    "verified": True,
}


def _envelope(result: str, **extra: Any) -> str:
    return json.dumps(
        {"type": "result", "subtype": "success", "is_error": False, "result": result, **extra}
    )


def _install_fake_commands(
    monkeypatch: pytest.MonkeyPatch,
    *,
    claude_stdout: str = "",
    local_head: str = HEAD,
    claude_error: BaseException | None = None,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run_command(
        args: list[str],
        cwd: Path,
        *,
        input_text: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        calls.append(args)
        if args[:3] == ["gh", "pr", "view"]:
            return json.dumps({"body": PR_BODY, "headRefOid": HEAD, "baseRefName": "main"})
        if args[:2] == ["git", "rev-parse"]:
            return local_head + "\n"
        if args[:2] == ["git", "fetch"]:
            return ""
        if args[:2] == ["git", "diff"]:
            return "diff --git a/x b/x\n+new line\n"
        if args[0] == "claude":
            assert input_text is not None
            if claude_error is not None:
                raise claude_error
            return claude_stdout
        if args[:3] == ["gh", "pr", "comment"]:
            return "https://github.com/o/r/pull/9#issuecomment-1\n"
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(cr, "run_command", fake_run_command)
    return calls


# --- プロンプトの組み立て -------------------------------------------------------


def test_extract_section_stops_at_same_level_heading() -> None:
    section = cr.extract_section(PR_BODY, "レビュー対象範囲")
    assert section is not None
    assert section.startswith("## レビュー対象範囲")
    # 下位の見出し（###）は節に含め、同格の次の見出し（## 仮置き）で止まる。
    assert "表の外は対象外" in section
    assert "## 仮置き" not in section


def test_extract_section_ignores_hash_lines_inside_code_fence() -> None:
    body = (
        "## レビュー対象範囲\n\n説明文\n\n```bash\n# 例: コメント\nuv run pytest\n```\n\n"
        "| 対象 | 箇所 |\n|---|---|\n| a.py | 全体 |\n\n## 仮置き\n\nなし\n"
    )
    section = cr.extract_section(body, "レビュー対象範囲")
    assert section is not None
    assert "| a.py | 全体 |" in section
    assert "## 仮置き" not in section


def test_build_prompt_passes_only_scope_provisional_and_diff() -> None:
    pr = cr.PullRequest(number=9, body=PR_BODY, head=HEAD, base="main")
    prompt = cr.build_prompt(pr, 3, PR_BODY, "+new line\n")

    assert "PR #9 の第3巡" in prompt
    assert HEAD in prompt
    assert "| tools/ops/claude_review.py | 全体 |" in prompt
    assert "## 仮置き" in prompt
    assert "+new line" in prompt
    assert "AGENTS.md" in prompt
    # 作者の説明文は渡さない。
    assert "作者の説明" not in prompt


def test_build_prompt_requires_scope_table() -> None:
    pr = cr.PullRequest(number=9, body="## 概要\n\nno scope\n", head=HEAD, base="main")
    with pytest.raises(cr.ReviewError, match="レビュー対象範囲"):
        cr.build_prompt(pr, 1, pr.body, "")


def test_build_prompt_truncates_large_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "MAX_DIFF_CHARS", 10)
    pr = cr.PullRequest(number=9, body=PR_BODY, head=HEAD, base="main")
    prompt = cr.build_prompt(pr, 1, PR_BODY, "x" * 50)
    assert "x" * 11 not in prompt
    assert f"git diff origin/main...{HEAD}" in prompt


def test_claude_command_is_read_only_and_non_interactive() -> None:
    command = cr.build_claude_command()
    assert command[:2] == ["claude", "-p"]
    assert command[command.index("--agent") + 1] == "adversarial-reviewer"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert "--dangerously-skip-permissions" not in command
    assert "Edit" not in command[command.index("--tools") + 1]
    assert "Bash(git diff:*)" in command


# --- 結果 JSON の解析 ----------------------------------------------------------


def test_parse_claude_output_accepts_fenced_json() -> None:
    payload = {"findings": [VERIFIED_FINDING], "summary": "s"}
    stdout = _envelope("```json\n" + json.dumps(payload) + "\n```")
    assert cr.parse_claude_output(stdout) == payload


def test_parse_claude_output_accepts_preamble_text() -> None:
    stdout = _envelope('Here is the result:\n{"findings": [], "summary": "ok"}')
    assert cr.parse_claude_output(stdout) == {"findings": [], "summary": "ok"}


def test_parse_claude_output_rejects_error_envelope() -> None:
    stdout = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True})
    with pytest.raises(cr.ReviewError, match="error"):
        cr.parse_claude_output(stdout)


def test_parse_claude_output_rejects_non_json_result() -> None:
    with pytest.raises(cr.ReviewError, match="not a JSON object"):
        cr.parse_claude_output(_envelope("no json here"))


def test_normalize_findings_drops_unverified_and_sorts() -> None:
    p2 = {**VERIFIED_FINDING, "priority": "p2", "title": "minor"}
    unverified = {**VERIFIED_FINDING, "priority": "P0", "verified": False}
    findings, summary, dropped = cr.normalize_findings(
        {"findings": [p2, unverified, VERIFIED_FINDING], "summary": "done"}
    )
    assert [item["priority"] for item in findings] == ["P1", "P2"]
    assert dropped == 1
    assert summary == "done"


def test_normalize_findings_rejects_malformed_finding() -> None:
    broken = {k: v for k, v in VERIFIED_FINDING.items() if k != "failure_scenario"}
    with pytest.raises(cr.ReviewError, match="failure_scenario"):
        cr.normalize_findings({"findings": [broken], "summary": ""})
    with pytest.raises(cr.ReviewError, match="priority"):
        cr.normalize_findings({"findings": [{**VERIFIED_FINDING, "priority": "P3"}]})


# --- コメント本文 -------------------------------------------------------------


def test_format_comment_heading_and_findings() -> None:
    body = cr.format_comment(2, HEAD, [dict(VERIFIED_FINDING)], "summary text", 1)
    assert body.splitlines()[0] == f"[claude-review 第2巡] head {HEAD}"
    assert "**P1** timeout is ignored" in body
    assert "`tools/ops/claude_review.py:42`" in body
    assert "失敗シナリオ: --timeout 1" in body
    assert "根拠節" not in body  # design_ref が空なら行を出さない
    assert "除いた候補: 1 件" in body


def test_format_comment_without_findings() -> None:
    body = cr.format_comment(1, HEAD, [], "", 0)
    assert "指摘 0 件" in body
    assert "P0" not in body and "P1" not in body


def test_build_output_matches_poller_shape() -> None:
    out = cr.build_output(
        1, HEAD, "body", "url", [dict(VERIFIED_FINDING)], "s", datetime(2026, 9, 25, tzinfo=UTC)
    )
    assert out["status"] == "responded"
    (item,) = out["items"]
    assert {"channel", "author", "created_at", "url", "body", "priority"} <= item.keys()
    assert item["created_at"] == "2026-09-25T00:00:00Z"
    assert item["priority"] == "P1"


# --- 終了コード ---------------------------------------------------------------


def test_main_posts_comment_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    stdout = _envelope(json.dumps({"findings": [VERIFIED_FINDING], "summary": "s"}))
    calls = _install_fake_commands(monkeypatch, claude_stdout=stdout)

    exit_code = cr.main(["--pr", "9", "--round", "2", "--base-dir", str(tmp_path)])
    assert exit_code == cr.EXIT_OK

    result = json.loads(capsys.readouterr().out)
    assert result["head"] == HEAD
    assert result["items"][0]["url"].endswith("issuecomment-1")
    assert any(call[:3] == ["gh", "pr", "comment"] for call in calls)


def test_main_no_post_skips_comment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    stdout = _envelope(json.dumps({"findings": [], "summary": "s"}))
    calls = _install_fake_commands(monkeypatch, claude_stdout=stdout)

    exit_code = cr.main(["--pr", "9", "--round", "1", "--base-dir", str(tmp_path), "--no-post"])
    assert exit_code == cr.EXIT_OK
    assert json.loads(capsys.readouterr().out)["items"][0]["url"] == ""
    assert not any(call[:3] == ["gh", "pr", "comment"] for call in calls)


def test_main_dry_run_prints_prompt_without_claude(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    calls = _install_fake_commands(monkeypatch)

    exit_code = cr.main(["--pr", "9", "--round", "1", "--base-dir", str(tmp_path), "--dry-run"])
    assert exit_code == cr.EXIT_OK
    assert "PR #9 の第1巡" in capsys.readouterr().out
    assert not any(call[0] == "claude" for call in calls)


def test_main_head_mismatch_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _install_fake_commands(monkeypatch, local_head="b" * 40)

    exit_code = cr.main(["--pr", "9", "--round", "1", "--base-dir", str(tmp_path)])
    assert exit_code == cr.EXIT_FAILURE
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_main_claude_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _install_fake_commands(monkeypatch, claude_error=cr.ReviewError("claude failed"))

    exit_code = cr.main(["--pr", "9", "--round", "1", "--base-dir", str(tmp_path)])
    assert exit_code == cr.EXIT_FAILURE
    assert "claude failed" in json.loads(capsys.readouterr().out)["error"]


def test_main_timeout_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _install_fake_commands(
        monkeypatch, claude_error=subprocess.TimeoutExpired(cmd="claude", timeout=5)
    )

    exit_code = cr.main(
        ["--pr", "9", "--round", "1", "--base-dir", str(tmp_path), "--timeout", "5"]
    )
    assert exit_code == cr.EXIT_TIMEOUT
    assert json.loads(capsys.readouterr().out)["status"] == "timeout"
