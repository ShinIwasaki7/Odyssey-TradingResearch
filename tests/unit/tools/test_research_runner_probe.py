"""作業役の守りの実機の確かめの道具（`probe`）の試験。

本物の `claude` は起動しない（偽の起動の口だけで試す）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tools.ops.research_runner import probe as probe_module


def _behaving(command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
    """守りが効いている作業役のつもり: 束の中だけを読み、out/ にだけ書く。"""
    out = cwd / "out"
    (out / "inside.txt").write_text((cwd / "rules" / "inside.txt").read_text())
    (out / "outside.txt").write_text("DENIED\n")
    (out / "write_attempt.txt").write_text("TRIED\n")
    (out / "tools.txt").write_text("Read\nGlob\nGrep\nWrite\n")
    (out / "canary.txt").write_text("NONE\n")
    definition = (cwd / ".claude" / "agents" / "probe.md").read_text()
    word = next(part for part in definition.split() if part.startswith("PROBE-AGENT-"))
    (out / "agent.txt").write_text(word + "\n")
    return 0


def _leaking(command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
    """守りが破れた作業役のつもり: 束の外を読み、束の外と束の直下に書き、Bash を使う。"""
    _behaving(command, cwd, env)
    out = cwd / "out"
    (out / "outside.txt").write_text((cwd.parent / "outside_secret.txt").read_text())
    (cwd.parent / "written_outside.txt").write_text("x")
    (cwd / "not_in_out.txt").write_text("x")
    (out / "bash.txt").write_text("hi\n")
    canary = (cwd.parent / "CLAUDE.md").read_text().split()[-2]
    (out / "canary.txt").write_text(canary)
    (out / "agent.txt").write_text("NONE\n")
    return 0


def test_without_launch_nothing_is_started(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[object] = []

    def launcher(command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
        calls.append(command)
        return 0

    code = probe_module.main(["--parent", str(tmp_path)], launcher=launcher)
    assert code == 0 and calls == []
    assert "起動しない" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []  # 一時のディレクトリは消す


def test_a_worker_that_keeps_the_boundary_passes_every_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}

    def launcher(command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
        seen.update(command=list(command), env=dict(env), cwd=cwd)
        return _behaving(command, cwd, env)

    assert probe_module.main(["--launch", "--parent", str(tmp_path)], launcher=launcher) == 0
    command = seen["command"]
    assert isinstance(command, list)
    assert command[command.index("--agent") + 1] == "probe"
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep,Write"
    assert "--max-turns" in command
    assert seen["env"] == {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    printed = capsys.readouterr().out
    assert printed.count(") 合格") == 7 and ") 不合格" not in printed
    assert "PROBE-" not in printed  # 語・中身を出さない


def test_a_leaking_worker_fails_the_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert probe_module.main(["--launch", "--parent", str(tmp_path)], launcher=_leaking) == 1
    printed = capsys.readouterr().out
    for key in ("a", "b", "d", "e", "f", "g"):
        assert f"({key}) 不合格" in printed
    assert "(c) 合格" in printed


def test_the_candidate_env_is_only_added_on_request(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def launcher(command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
        seen.update(env)
        return _behaving(command, cwd, env)

    probe_module.main(
        ["--launch", "--disable-claude-mds", "--parent", str(tmp_path)], launcher=launcher
    )
    assert seen["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"


def _lazy(command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
    """手順を省いた作業役のつもり: 束の中の語と定義の語だけを書き、ほかを試さない。"""
    out = cwd / "out"
    (out / "inside.txt").write_text((cwd / "rules" / "inside.txt").read_text())
    definition = (cwd / ".claude" / "agents" / "probe.md").read_text()
    word = next(part for part in definition.split() if part.startswith("PROBE-AGENT-"))
    (out / "agent.txt").write_text(word + "\n")
    return 0


def test_skipped_steps_are_not_counted_as_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """試した証拠（決まった出力のファイル）が無い検査は不合格（Codex 1 巡目の指摘）。"""
    assert probe_module.main(["--launch", "--parent", str(tmp_path)], launcher=_lazy) == 1
    printed = capsys.readouterr().out
    for key in ("a", "b", "d", "e"):
        assert f"({key}) 不合格" in printed


def test_a_non_zero_exit_fails_the_whole_probe(tmp_path: Path) -> None:
    def failing(command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
        _behaving(command, cwd, env)
        return 1

    assert probe_module.main(["--launch", "--parent", str(tmp_path)], launcher=failing) == 1
