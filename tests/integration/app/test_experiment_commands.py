"""`experiment run` と `experiment reproduce` をコマンド経由で通す（D07 §19〜§21）。

実装 PR 3 の統合テスト（D07 §17.2）。

D07 §17.2 が実装 PR 3 の統合テストに求めるのは**別プロセスでの再現一致**である。人工データ
（T02）を受け入れて承認した作業場（`tests/fixtures/acceptance/t02_workspace.py`）で、書式 v2 の
実験設定から `experiment run` を通し、続けて `experiment reproduce` を**別の OS のプロセス**
（`subprocess`）で起こして判定が `REPRODUCED` になることを確かめる（D07 §21.1 の「別プロセス」）。

あわせて、コマンドの終了コード（D07 §21.3 の表）と、記録票・結末記録の置き場と書き込み規則
（D07 §19.1・§19.3・§19.6）をコマンドの経路で確かめる。段階4 の完了条件そのものの受入テスト
（`tests/acceptance/test_stage4_completion.py`）は実装 PR 4 の範囲である（D07 §17.2）。
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_MANIFEST_FILE,
    EXPERIMENT_OUTCOME_FILE,
    REPRODUCTION_FILE,
)
from tests.fixtures.acceptance.t02_workspace import T02Workspace, build_workspace

#: 通すケース（検証戦略 B・遅延シナリオ d1_2s。待機と再開を含む経路）。
_CASE = "d1_2s"


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> T02Workspace:
    """T02 の人工データを受け入れて承認した作業場（テスト間で共有する。書き換えない）。"""
    return build_workspace(tmp_path_factory.mktemp("t02-experiment"))


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    """コマンドを同じプロセスで実行し、終了コードと画面への出力を返す。"""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _run_argv(
    workspace: T02Workspace, artifacts: Path, experiment: Path | None = None
) -> list[str]:
    return [
        "experiment",
        "run",
        "--experiment",
        str(experiment or workspace.experiment(_CASE)),
        "--snapshots",
        str(workspace.repo / "data/snapshots"),
        "--out",
        str(artifacts),
        "--repo-root",
        str(workspace.repo),
    ]


def _experiment_dir(artifacts: Path) -> Path:
    return artifacts / "runs/experiments/strategy_b_t02_d1_2s/v1"


def _reproduce_in_subprocess(
    workspace: T02Workspace, experiment_dir: Path, out: Path
) -> subprocess.CompletedProcess[str]:
    """再現コマンドを**別の OS のプロセス**として起こす（D07 §21.1、D08 §2.3）。"""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "odyssey_fx.app.cli.main",
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(experiment_dir),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(workspace.repo),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=out.parent,
    )


def _json(path: Path) -> dict[str, object]:
    payload: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return payload


@pytest.fixture(scope="module")
def completed(workspace: T02Workspace, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`experiment run` を1回通した成果物の基点（`COMPLETED`）。"""
    artifacts = tmp_path_factory.mktemp("experiment-artifacts")
    code, output, error = _invoke(_run_argv(workspace, artifacts))
    assert code == 0, output + error
    return artifacts


def test_the_experiment_run_records_the_manifest_the_outcome_and_the_run(
    completed: Path,
) -> None:
    """記録票と結末記録が版のディレクトリに残り、結末記録から run と評価へ辿れる（D07 §19）。"""
    directory = _experiment_dir(completed)
    manifest = _json(directory / EXPERIMENT_MANIFEST_FILE)
    outcome = _json(directory / EXPERIMENT_OUTCOME_FILE)

    assert outcome["experiment_id"] == manifest["experiment_id"]
    assert outcome["status"] == "COMPLETED"
    assert outcome["failed_checks"] == []
    assert outcome["run_reused"] is False
    assert outcome["run_id"] == outcome["expected_run_id"]
    run_directory = completed / "runs" / str(outcome["run_id"])
    evaluation = _json(
        run_directory / "eval" / str(outcome["run_evaluation_id"]) / "evaluation.json"
    )
    assert evaluation["result_digest"] == outcome["result_digest"]
    assert evaluation["metric_set_version"] == manifest["metric_set_version"]
    assert (
        _json(run_directory / "manifest.json")["config_digest"]
        == manifest["expected_config_digest"]
    )
    roles = [item["role"] for item in manifest["resolved_files"]]  # type: ignore[attr-defined]
    assert roles == [
        "calendar",
        "experiment",
        "research_policy",
        "strategy",
        "symbol:USDJPY",
        "timeframes",
    ]
    checks = {item["check"]: item["outcome"] for item in manifest["pre_run_checks"]}  # type: ignore[attr-defined]
    assert checks == {
        "hypothesis_present": "PASSED",
        "research_history_only": "PASSED",
        "complexity_within_limits": "PASSED",
    }


def test_the_experiment_is_reproduced_in_a_separate_process(
    workspace: T02Workspace, completed: Path, tmp_path: Path
) -> None:
    """別プロセスの再現で `run_id` と `result_digest` が結末記録と一致する（D07 §21.1〜§21.3）。"""
    out = tmp_path / "reproduction"
    process = _reproduce_in_subprocess(workspace, _experiment_dir(completed), out)

    assert process.returncode == 0, process.stdout + process.stderr
    report = _json(out / REPRODUCTION_FILE)
    outcome = _json(_experiment_dir(completed) / EXPERIMENT_OUTCOME_FILE)
    assert report["verdict"] == "REPRODUCED"
    assert report["observed_run_id"] == outcome["run_id"]
    assert report["observed_result_digest"] == outcome["result_digest"]
    # 再現は元の `runs/` を上書きせず、別の基点に run と評価を書く（D07 §21.2 の手順4）。
    assert (out / "runs" / str(outcome["run_id"]) / "manifest.json").is_file()


def test_a_manifest_whose_text_was_edited_is_reported_as_tampered(
    workspace: T02Workspace, completed: Path, tmp_path: Path
) -> None:
    """記録票の本文だけを書き換えると、再現は `MANIFEST_TAMPERED` と判定して run しない。

    識別子の入力には本文ではなく SHA-256 が入る（D07 §19.2 の入力 3）ので、本文の SHA-256 を
    記録された値と照合する（D07 §21.2 の手順1。PR #41 のレビューで PR 3 へ送られた指摘）。
    """
    copy = tmp_path / "runs/experiments/strategy_b_t02_d1_2s/v1"
    copy.mkdir(parents=True)
    source = _experiment_dir(completed)
    manifest = _json(source / EXPERIMENT_MANIFEST_FILE)
    for item in manifest["resolved_files"]:  # type: ignore[attr-defined]
        if item["role"] == "calendar":
            item["text"] = item["text"] + "\n# edited after the run\n"
    (copy / EXPERIMENT_MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    (copy / EXPERIMENT_OUTCOME_FILE).write_bytes((source / EXPERIMENT_OUTCOME_FILE).read_bytes())

    out = tmp_path / "reproduction"
    code, output, error = _invoke(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(copy),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(workspace.repo),
        ]
    )
    assert code == 6, output + error
    report = _json(out / REPRODUCTION_FILE)
    assert report["verdict"] == "MANIFEST_TAMPERED"
    assert report["observed_run_id"] is None
    assert not (out / "runs").exists()


def _copy_with_outcome(completed: Path, tmp_path: Path, **changes: object) -> Path:
    """実験の版のディレクトリを写し、結末記録の項目だけを書き換える。"""
    copy = tmp_path / "runs/experiments/strategy_b_t02_d1_2s/v1"
    copy.mkdir(parents=True)
    source = _experiment_dir(completed)
    (copy / EXPERIMENT_MANIFEST_FILE).write_bytes((source / EXPERIMENT_MANIFEST_FILE).read_bytes())
    outcome = _json(source / EXPERIMENT_OUTCOME_FILE)
    outcome.update(changes)
    (copy / EXPERIMENT_OUTCOME_FILE).write_text(json.dumps(outcome), encoding="utf-8")
    return copy


def _reproduce(workspace: T02Workspace, directory: Path, out: Path) -> tuple[int, str, str]:
    return _invoke(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(directory),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(workspace.repo),
        ]
    )


def test_a_different_environment_is_not_run(
    workspace: T02Workspace, completed: Path, tmp_path: Path
) -> None:
    """環境のダイジェストが結末記録と違えば run せず `ENVIRONMENT_MISMATCH`（§21.2 の手順2）。"""
    copy = _copy_with_outcome(completed, tmp_path, env_digest="0" * 64)
    out = tmp_path / "reproduction"

    code, output, error = _reproduce(workspace, copy, out)

    assert code == 6, output + error
    assert _json(out / REPRODUCTION_FILE)["verdict"] == "ENVIRONMENT_MISMATCH"
    assert not (out / "runs").exists()


def test_a_manifest_that_does_not_reach_the_recorded_run_is_not_run(
    workspace: T02Workspace, completed: Path, tmp_path: Path
) -> None:
    """記録票の入力から組み立てた `RunId` が結末記録と違えば run せず `RUN_ID_MISMATCH`（手順3）。

    検査 P4 が不合格だった実験（記録票の保存から run までの間に入力が変わった）に当たる。
    """
    copy = _copy_with_outcome(completed, tmp_path, run_id="f" * 64)
    out = tmp_path / "reproduction"

    code, output, error = _reproduce(workspace, copy, out)

    assert code == 6, output + error
    report = _json(out / REPRODUCTION_FILE)
    assert report["verdict"] == "RUN_ID_MISMATCH"
    assert report["expected_run_id"] == "f" * 64
    assert report["observed_result_digest"] is None
    assert not (out / "runs").exists()


def test_an_experiment_without_an_outcome_is_an_argument_error(
    workspace: T02Workspace, completed: Path, tmp_path: Path
) -> None:
    """結末記録が無い（途中で止まった）実験の再現は引数の誤りで終了コード 2（§21.2 の手順1）。"""
    copy = tmp_path / "runs/experiments/strategy_b_t02_d1_2s/v1"
    copy.mkdir(parents=True)
    source = _experiment_dir(completed)
    (copy / EXPERIMENT_MANIFEST_FILE).write_bytes((source / EXPERIMENT_MANIFEST_FILE).read_bytes())
    out = tmp_path / "reproduction"

    code, _, error = _reproduce(workspace, copy, out)

    assert code == 2
    assert "結末記録が無い" in error
    assert not (out / REPRODUCTION_FILE).exists()


def test_rerunning_the_same_version_reuses_the_run_and_keeps_the_previous_outcome(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """同じ版の再実行は既存の成果物を再利用し、前の結末記録を退避する（D07 §19.3・§19.6）。"""
    artifacts = tmp_path / "artifacts"
    first = _invoke(_run_argv(workspace, artifacts))
    assert first[0] == 0, first
    directory = _experiment_dir(artifacts)
    previous = (directory / EXPERIMENT_OUTCOME_FILE).read_text(encoding="utf-8")

    second = _invoke(_run_argv(workspace, artifacts))

    assert second[0] == 0, second
    assert (directory / "experiment_outcome.1.json").read_text(encoding="utf-8") == previous
    outcome = _json(directory / EXPERIMENT_OUTCOME_FILE)
    assert outcome["run_reused"] is True
    assert outcome["result_digest"] == json.loads(previous)["result_digest"]
    assert len(list((artifacts / "runs").iterdir())) == 2  # experiments と run 1つ


def test_a_changed_hypothesis_under_the_same_version_is_refused(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """同じ版で内容を変えると、記録票を書き換えず run もせずに終了コード 5（D07 §19.4・§21.3）。"""
    artifacts = tmp_path / "artifacts"
    assert _invoke(_run_argv(workspace, artifacts))[0] == 0
    directory = _experiment_dir(artifacts)
    manifest_before = (directory / EXPERIMENT_MANIFEST_FILE).read_bytes()
    outcome_before = (directory / EXPERIMENT_OUTCOME_FILE).read_bytes()
    edited = tmp_path / "edited.yaml"
    text = workspace.experiment(_CASE).read_text(encoding="utf-8")
    edited.write_text(text.replace('hypothesis: "', 'hypothesis: "（書き直し）'), encoding="utf-8")

    code, output, _ = _invoke(_run_argv(workspace, artifacts, edited))

    assert code == 5, output
    assert "MANIFEST_CONFLICT" in output
    assert (directory / EXPERIMENT_MANIFEST_FILE).read_bytes() == manifest_before
    assert (directory / EXPERIMENT_OUTCOME_FILE).read_bytes() == outcome_before
    assert not (directory / "experiment_outcome.1.json").exists()


def test_an_unreusable_run_directory_is_refused_before_anything_is_written(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """予測した `runs/<run_id>/` が空で残っていると何も書かずに終了コード 5（D07 §19.6）。"""
    artifacts = tmp_path / "artifacts"
    first_dir = tmp_path / "first"
    code, output, _ = _invoke(_run_argv(workspace, first_dir))
    assert code == 0, output
    run_id = _json(_experiment_dir(first_dir) / EXPERIMENT_OUTCOME_FILE)["expected_run_id"]
    (artifacts / "runs" / str(run_id)).mkdir(parents=True)

    code, output, _ = _invoke(_run_argv(workspace, artifacts))

    assert code == 5, output
    assert "RUN_ARTIFACT_CONFLICT" in output
    assert not (artifacts / "runs/experiments").exists()
    assert list((artifacts / "runs" / str(run_id)).iterdir()) == []


def test_a_policy_violation_does_not_run_and_exits_with_three(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """複雑性の上限を1つ超えると run せず、結末 `REJECTED_BY_POLICY`・終了コード 3（D07 §20.3）。"""
    repo = tmp_path / "repo"
    for relative in (
        "uv.lock",
        "configs/calendars/fx_ny17_v1.yaml",
        "configs/calendars/timeframes_v1.yaml",
        "configs/symbols/USDJPY.yaml",
        "configs/strategies/strategy_b_v1.yaml",
        "configs/policies/research/research_policy_v1.yaml",
        "configs/experiments/strategy_b_t02_d1_2s.yaml",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((workspace.repo / relative).read_bytes())
    policy = repo / "configs/policies/research/research_policy_v1.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("instances: 36", "instances: 11"),
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    argv = _run_argv(workspace, artifacts, repo / "configs/experiments/strategy_b_t02_d1_2s.yaml")
    argv[argv.index("--repo-root") + 1] = str(repo)

    code, output, _ = _invoke(argv)

    assert code == 3, output
    outcome = _json(_experiment_dir(artifacts) / EXPERIMENT_OUTCOME_FILE)
    assert outcome["status"] == "REJECTED_BY_POLICY"
    assert outcome["failed_checks"] == ["complexity_within_limits"]
    assert outcome["run_id"] is None
    assert [path.name for path in (artifacts / "runs").iterdir()] == ["experiments"]


def test_a_v1_experiment_is_a_configuration_error_with_exit_code_two(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """`experiment run` は書式 v2 だけを受ける（D07 §18.5）。設定の誤りは終了コード 2（§21.3）。"""
    v1 = Path(__file__).resolve().parents[3] / "configs/experiments/strategy_a_t01.yaml"
    code, _, error = _invoke(_run_argv(workspace, tmp_path / "artifacts", v1))
    assert code == 2
    assert "書式 v2" in error


def test_reproducing_into_the_original_artifacts_root_is_refused(
    workspace: T02Workspace, completed: Path
) -> None:
    """`--out` が元の成果物と同じ基点なら拒否する（D07 §21.2 の手順4）。終了コード 2。"""
    code, _, error = _invoke(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(_experiment_dir(completed)),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(completed),
            "--repo-root",
            str(workspace.repo),
        ]
    )
    assert code == 2
    assert "--out" in error
    assert not (completed / REPRODUCTION_FILE).exists()
