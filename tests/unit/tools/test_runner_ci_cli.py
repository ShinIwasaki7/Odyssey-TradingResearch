"""CI の段の入口（`python -m tools.ops.runner_ci`）。

一時の git の作業場（全体・システムの git の設定を読まない）と GitHub の出来事のファイルで通す。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from tests.fixtures.git_origin import isolate_git
from tests.fixtures.runner_ci import CARD_ID, TRIAL, card_bytes, ledgers, trial_ledger
from tools.ops.runner_ci.cli import main


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True, text=True
    )
    return completed.stdout.strip()


def _commit(repo: Path, files: dict[str, bytes], message: str) -> str:
    for path, data in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    isolate_git(monkeypatch, tmp_path / "home")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "checkout", "-q", "-b", "main")
    _git(root, "config", "user.email", "ci@example.invalid")
    _git(root, "config", "user.name", "ci")
    _git(root, "config", "commit.gpgsign", "false")
    return root


def _event(tmp_path: Path, payload: Mapping[str, object]) -> str:
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_an_append_passes_and_a_rewrite_fails(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _commit(repo, ledgers(tmp_path / "l"), "base")
    appended = _commit(repo, {TRIAL: trial_ledger(4)}, "append")
    assert main(["--repo", str(repo), "--base", base, "--head", appended]) == 0
    rewritten = _commit(repo, {TRIAL: trial_ledger(4, name="z")}, "rewrite")
    assert main(["--repo", str(repo), "--base", appended, "--head", rewritten]) == 1
    output = capsys.readouterr().out
    assert "追記だけでない" in output
    assert "結果: 不合格" in output


def test_a_pull_request_event_reads_branch_title_and_auto_merge(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _commit(repo, ledgers(tmp_path / "l"), "base")
    _git(repo, "checkout", "-q", "-b", "docs/x")
    _commit(repo, {"docs/design/D99.md": b"# d99\n"}, "design")
    payload = {
        "pull_request": {
            "base": {"sha": base},
            "head": {"ref": "docs/x"},
            "title": "docs: D99",
            "body": "本文",
            "auto_merge": None,
        }
    }
    assert (
        main(
            [
                "--repo",
                str(repo),
                "--event-name",
                "pull_request",
                "--event-path",
                _event(tmp_path, payload),
            ]
        )
        == 0
    )
    payload["pull_request"]["auto_merge"] = {"merge_method": "merge"}
    assert (
        main(
            [
                "--repo",
                str(repo),
                "--event-name",
                "pull_request",
                "--event-path",
                _event(tmp_path, payload),
            ]
        )
        == 1
    )
    assert "定型 PR でない PR に自動 merge" in capsys.readouterr().out


def test_a_card_move_is_seen_through_git(repo: Path, tmp_path: Path) -> None:
    queue = f"research/hypotheses/queue/{CARD_ID}.yaml"
    base = _commit(repo, {**ledgers(tmp_path / "l"), queue: card_bytes()}, "base")
    _git(repo, "checkout", "-q", "-b", "feat/move")
    (repo / "research/hypotheses/taken").mkdir(parents=True)
    _git(repo, "mv", queue, queue.replace("/queue/", "/taken/"))
    _git(repo, "commit", "-q", "-m", "move")
    payload = {
        "pull_request": {
            "base": {"sha": base},
            "head": {"ref": "feat/move"},
            "title": "move",
            "body": "",
            "auto_merge": None,
        }
    }
    # 定型でない PR が taken/ にカードを置くのは凍結の規則に反する（検査 3）。
    assert (
        main(
            [
                "--repo",
                str(repo),
                "--event-name",
                "pull_request",
                "--event-path",
                _event(tmp_path, payload),
            ]
        )
        == 1
    )


def test_a_push_without_a_base_checks_the_ledgers_only(repo: Path, tmp_path: Path) -> None:
    _commit(repo, ledgers(tmp_path / "l"), "base")
    payload = {"before": "0" * 40, "after": _git(repo, "rev-parse", "HEAD")}
    assert (
        main(
            ["--repo", str(repo), "--event-name", "push", "--event-path", _event(tmp_path, payload)]
        )
        == 0
    )


def test_bad_input_exits_with_two(repo: Path, tmp_path: Path) -> None:
    _commit(repo, ledgers(tmp_path / "l"), "base")
    assert main(["--repo", str(repo), "--base", "no-such-commit"]) == 2
    assert (
        main(
            [
                "--repo",
                str(repo),
                "--event-name",
                "issue_comment",
                "--event-path",
                _event(tmp_path, {}),
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "--repo",
                str(repo),
                "--event-name",
                "pull_request",
                "--event-path",
                _event(tmp_path, {}),
            ]
        )
        == 2
    )


def test_the_pull_request_base_is_the_merge_base(repo: Path, tmp_path: Path) -> None:
    """出来事の base.sha が先頭の作られた後に進んでいても、合流の基点と比べる。"""
    start = _commit(repo, ledgers(tmp_path / "l"), "base")
    _git(repo, "checkout", "-q", "-b", "docs/x")
    _commit(repo, {"docs/design/D99.md": b"# d99\n"}, "design")
    _git(repo, "checkout", "-q", "main")
    advanced = _commit(repo, {TRIAL: trial_ledger(4)}, "main advances")
    _git(repo, "checkout", "-q", "docs/x")
    assert _git(repo, "merge-base", advanced, "HEAD") == start
    payload = {
        "pull_request": {
            "base": {"sha": advanced},
            "head": {"ref": "docs/x"},
            "title": "docs: D99",
            "body": "",
            "auto_merge": None,
        }
    }
    event = _event(tmp_path, payload)
    assert main(["--repo", str(repo), "--event-name", "pull_request", "--event-path", event]) == 0


def test_the_workflow_runs_the_trusted_copy_of_the_checks() -> None:
    """検査の道具は基点から取り出したものを走らせ、PR の中身は --repo の入力にするだけ。"""
    workflow = Path(__file__).resolve().parents[3] / ".github/workflows/ledger.yml"
    loaded = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    steps = loaded["jobs"]["ledger"]["steps"]
    trusted = next(step for step in steps if step.get("id") == "trusted")
    assert "merge-base" in trusted["run"] and "worktree add" in trusted["run"]
    check = steps[-1]
    assert check["working-directory"] == "${{ steps.trusted.outputs.dir }}"
    assert '--repo "$GITHUB_WORKSPACE/pr"' in check["run"]
    assert steps[0]["with"]["path"] == "pr"
    events = loaded[True]["pull_request"]["types"]
    assert {"edited", "auto_merge_enabled", "auto_merge_disabled"} <= set(events)
