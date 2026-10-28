"""実行の包み（tools/ops/run_experiment.py。D14 §2.3.1）のテスト。

`experiment run` と研究の画面の道具は、偽物のコマンド（小さな Python の 1 行）に差し替える。
実データ・snapshot・成績には触れない。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from tools.ops import run_experiment as re_mod

EXPERIMENT = "configs/experiments/synthetic.yaml"
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    (worktree / "configs" / "experiments").mkdir(parents=True)
    (worktree / "research").mkdir()
    (worktree / "research" / "trial_ledger.jsonl").write_text('{"a": 1}\n{"b": 2}\n')
    (worktree / "uv.lock").write_text("lock\n")
    (worktree / EXPERIMENT).write_text("schema_version: 2\nid: synthetic\nversion: 3\n")
    repo.mkdir()
    return repo, worktree


def _args(repo: Path, worktree: Path, *extra: str) -> list[str]:
    return ["--experiment", EXPERIMENT, "--repo", str(repo), "--worktree", str(worktree), *extra]


def _version_dir(repo: Path) -> Path:
    return repo / "runs" / "experiments" / "synthetic" / "v3"


def _fake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, run_exit: int, viewer_exit: int
) -> Path:
    marker = tmp_path / "viewer_called"
    monkeypatch.setattr(
        re_mod,
        "build_run_command",
        lambda plan: _py(f"import sys; print('run output'); sys.exit({run_exit})"),
    )
    monkeypatch.setattr(
        re_mod,
        "build_viewer_command",
        lambda plan: _py(
            f"import sys; open({str(marker)!r}, 'w').write('x'); print('viewer out'); "
            f"print('viewer err', file=sys.stderr); sys.exit({viewer_exit})"
        ),
    )
    return marker


def test_foreground_writes_every_file_and_returns_run_exit_code(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, worktree = dirs
    marker = _fake(monkeypatch, tmp_path, run_exit=0, viewer_exit=0)

    assert re_mod.main(_args(repo, worktree, "--foreground")) == 0

    out = _version_dir(repo)
    assert ISO.match((out / "started_at_utc.txt").read_text().strip())
    assert ISO.match((out / "finished_at_utc.txt").read_text().strip())
    assert (out / "exit_code.txt").read_text() == "0\n"
    assert (out / "console.log").read_text() == "run output\n"
    assert (out / "viewer_exit_code.txt").read_text() == "0\n"
    assert marker.exists()
    launch = json.loads((out / "launch.json").read_text())
    assert launch["experiment"] == EXPERIMENT
    assert launch["experiment_version"] == 3
    assert launch["mode"] == "foreground"
    assert isinstance(launch["launcher_pid"], int)
    assert isinstance(launch["run_pid"], int)
    assert launch["ledger_lines_before"] == 2
    assert launch["uv_lock_sha256"] is not None
    assert launch["worktree_commit"] is None  # git の外なので取れない


def test_viewer_runs_even_when_run_fails(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, worktree = dirs
    marker = _fake(monkeypatch, tmp_path, run_exit=1, viewer_exit=0)

    assert re_mod.main(_args(repo, worktree, "--foreground")) == 1

    assert (_version_dir(repo) / "exit_code.txt").read_text() == "1\n"
    assert marker.exists()


def test_viewer_failure_does_not_change_exit_code(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, worktree = dirs
    _fake(monkeypatch, tmp_path, run_exit=0, viewer_exit=3)

    assert re_mod.main(_args(repo, worktree, "--foreground")) == 0

    out = _version_dir(repo)
    assert (out / "viewer_exit_code.txt").read_text() == "3\n"
    assert (out / "viewer.log").read_text() == "[stdout]\nviewer out\n[stderr]\nviewer err\n"


def test_refuses_when_launch_json_exists(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, worktree = dirs
    marker = _fake(monkeypatch, tmp_path, run_exit=0, viewer_exit=0)
    out = _version_dir(repo)
    out.mkdir(parents=True)
    (out / "launch.json").write_text("{}\n")

    assert re_mod.main(_args(repo, worktree, "--foreground")) == re_mod.EXIT_REFUSED

    assert sorted(path.name for path in out.iterdir()) == ["launch.json"]
    assert (out / "launch.json").read_text() == "{}\n"
    assert not marker.exists()


def test_concurrent_launch_is_refused_while_first_is_preparing(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1 本目が事実を集めている途中（予約の後・書き込みの前）に 2 本目が来ても拒否される。
    repo, worktree = dirs
    args = re_mod._parser().parse_args(_args(repo, worktree))
    plan = re_mod.plan_from_args(args)
    real_facts = re_mod.launch_facts
    refused: list[str] = []

    def facts_with_second_launch(
        plan: re_mod.Plan, started_at: str, *, mode: str
    ) -> dict[str, object]:
        with pytest.raises(re_mod.LaunchError):
            re_mod.prepare(plan, mode="detached")
        refused.append("second")
        return real_facts(plan, started_at, mode=mode)

    monkeypatch.setattr(re_mod, "launch_facts", facts_with_second_launch)

    re_mod.prepare(plan, mode="foreground")

    assert refused == ["second"]
    assert json.loads(plan.launch_path.read_text())["mode"] == "foreground"


@pytest.mark.parametrize(
    "text",
    [
        "schema_version: 2\nversion: 3\n",
        "schema_version: 2\nid: synthetic\n",
        "id: s\nversion: 0\n",
    ],
)
def test_refuses_experiment_without_id_or_version(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str
) -> None:
    repo, worktree = dirs
    _fake(monkeypatch, tmp_path, run_exit=0, viewer_exit=0)
    (worktree / EXPERIMENT).write_text(text)

    assert re_mod.main(_args(repo, worktree, "--foreground")) == re_mod.EXIT_REFUSED

    assert not (repo / "runs").exists()


def test_run_that_cannot_start_is_recorded(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, worktree = dirs
    marker = _fake(monkeypatch, tmp_path, run_exit=0, viewer_exit=0)
    monkeypatch.setattr(re_mod, "build_run_command", lambda plan: [str(tmp_path / "missing")])

    assert re_mod.main(_args(repo, worktree, "--foreground")) == re_mod.EXIT_NOT_STARTED

    assert (_version_dir(repo) / "exit_code.txt").read_text() == "127\n"
    assert marker.exists()


def test_detached_mode_prepares_and_spawns_child(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, worktree = dirs
    spawned: list[re_mod.Plan] = []

    def fake_spawn(plan: re_mod.Plan) -> int:
        spawned.append(plan)
        return 4321

    monkeypatch.setattr(re_mod, "spawn_child", fake_spawn)

    assert re_mod.main(_args(repo, worktree)) == 0

    out = _version_dir(repo)
    assert [plan.version_dir for plan in spawned] == [out]
    assert json.loads((out / "launch.json").read_text())["mode"] == "detached"
    assert not (out / "exit_code.txt").exists()


def test_spawn_failure_removes_reservation_so_relaunch_works(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, worktree = dirs
    monkeypatch.setattr(re_mod, "child_command", lambda plan: [str(worktree / "missing-python")])

    assert re_mod.main(_args(repo, worktree)) == re_mod.EXIT_REFUSED

    out = _version_dir(repo)
    assert list(out.iterdir()) == []

    monkeypatch.setattr(re_mod, "spawn_child", lambda plan: 4321)
    assert re_mod.main(_args(repo, worktree)) == 0


def test_child_command_uses_absolute_paths(dirs: tuple[Path, Path]) -> None:
    repo, worktree = dirs
    args = re_mod._parser().parse_args(_args(repo, worktree))
    command = re_mod.child_command(re_mod.plan_from_args(args))

    assert command[:3] == [sys.executable, "-m", "tools.ops.run_experiment"]
    assert command[-1] == "--child"
    for flag in ("--repo", "--worktree", "--snapshots"):
        assert Path(command[command.index(flag) + 1]).is_absolute()


def test_child_without_launch_json_is_refused(dirs: tuple[Path, Path]) -> None:
    repo, worktree = dirs

    assert re_mod.main(_args(repo, worktree, "--child")) == re_mod.EXIT_REFUSED


def test_run_command_matches_the_recorded_form(dirs: tuple[Path, Path]) -> None:
    repo, worktree = dirs
    args = re_mod._parser().parse_args(_args(repo, worktree))
    plan = re_mod.plan_from_args(args)

    command = re_mod.build_run_command(plan)

    assert command[:6] == [
        "caffeinate",
        "-i",
        "/usr/bin/time",
        "-l",
        "-o",
        str(plan.version_dir / "time.log"),
    ]
    assert command[6:12] == ["uv", "run", "--frozen", "odyssey-fx", "experiment", "run"]
    assert command[command.index("--snapshots") + 1] == str(repo.resolve() / "data" / "snapshots")
    assert re_mod.build_viewer_command(plan)[-1] == "--open"
