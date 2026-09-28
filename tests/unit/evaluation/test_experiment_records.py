"""記録票の識別子・結末記録の不変条件・保存の規則（D07 §19.1〜§19.3）。"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId, RunId
from odyssey_fx.common.refs import CodeDigest, EnvDigest, LockDigest
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_MANIFEST_FILE,
    EXPERIMENT_OUTCOME_FILE,
    FileSystemExperimentStore,
    experiment_directory,
    read_experiment_manifest,
    read_experiment_outcome,
    write_reproduction,
)
from odyssey_fx.evaluation.application.ports import ManifestSaveResult
from odyssey_fx.evaluation.domain.errors import ArtifactAlreadyExists
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentOutcome,
    ExperimentStatus,
    ResolvedFile,
    experiment_id_of,
    require_experiment_name,
)
from odyssey_fx.evaluation.domain.research_policy import PolicyCheck, check_preregistration
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from tests.fixtures.evaluation.experiments import EXPERIMENT_VALUES, make_manifest

# --- 識別子（D07 §19.2 の「識別の入力」）-----------------------------------------


def test_paths_do_not_enter_the_experiment_id() -> None:
    """パスを持つキー（`strategy` と `environment`）は識別に入らない（D07 §19.2・§18.2）。"""
    manifest = make_manifest()
    moved = dict(EXPERIMENT_VALUES)
    moved["strategy"] = "elsewhere/strategy_a_v1.yaml"
    moved["environment"] = {"calendar": "x.yaml", "timeframes": "y.yaml", "symbols": "z"}
    assert experiment_id_of(manifest, moved) == manifest.experiment_id


def test_the_environment_group_does_not_enter_the_experiment_id() -> None:
    """コード・lock・環境のダイジェストと git の状態は識別に入らない（D07 §19.2）。"""
    manifest = make_manifest()
    other = make_manifest(code_seed="another code")
    assert other.code_digest != manifest.code_digest
    assert other.experiment_id == manifest.experiment_id


def test_the_hypothesis_and_the_values_enter_the_experiment_id() -> None:
    """事前固定の群と実験設定の値は識別に入る（D07 §19.2 の入力 2・4）。"""
    manifest = make_manifest()
    assert make_manifest(hypothesis="別の仮説").experiment_id != manifest.experiment_id
    changed = dict(EXPERIMENT_VALUES)
    changed["seed"] = 1
    assert experiment_id_of(manifest, changed) != manifest.experiment_id


def test_a_referenced_file_enters_the_id_by_its_sha256_only() -> None:
    """参照先ファイルは `(役割, SHA-256)` で入る。実験設定の本文そのものは入らない（入力 3）。"""
    manifest = make_manifest()
    files = list(manifest.resolved_files)
    edited_experiment = [
        ResolvedFile.of("experiment", "# a comment\n") if item.role == "experiment" else item
        for item in files
    ]
    edited_strategy = [
        ResolvedFile.of("strategy", "schema_version: 1\n# edited\n")
        if item.role == "strategy"
        else item
        for item in files
    ]
    assert (
        experiment_id_of(
            replace(manifest, resolved_files=tuple(edited_experiment)), EXPERIMENT_VALUES
        )
        == manifest.experiment_id
    )
    assert (
        experiment_id_of(
            replace(manifest, resolved_files=tuple(edited_strategy)), EXPERIMENT_VALUES
        )
        != manifest.experiment_id
    )


def test_a_text_edited_without_its_sha256_is_detected() -> None:
    """本文だけを書き換えると、記録された SHA-256 と合わない（D07 §21.2 の手順1 の検出に使う）。"""
    item = ResolvedFile.of("calendar", "schema_version: 1\n")
    assert item.intact
    assert not replace(item, text="schema_version: 1\n# edited\n").intact


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".hidden", "with space"])
def test_an_experiment_name_that_is_not_a_plain_directory_name_is_refused(name: str) -> None:
    """名前は保存先のディレクトリ名になる（D07 §19.1）ので、区切りや `..` を含めない（仮置き）。"""
    with pytest.raises(KernelValueError):
        require_experiment_name(name)


# --- 結末記録の不変条件（D07 §19.3）---------------------------------------------------


def _outcome(**overrides: object) -> ExperimentOutcome:
    values: dict[str, object] = {
        "experiment_id": ExperimentId(digest("experiment")),
        "status": ExperimentStatus.COMPLETED,
        "expected_run_id": RunId(digest("run")),
        "code_digest": CodeDigest(digest("code")),
        "lock_digest": LockDigest(digest("lock")),
        "env_digest": EnvDigest(digest("env")),
        "git_commit": "",
        "git_dirty": True,
        "run_id": RunId(digest("run")),
        "run_status": RunStatus.COMPLETED,
        "run_reused": False,
        "run_evaluation_id": digest("evaluation"),
        "evaluation_status": EvaluationStatus.COMPLETED,
        "result_digest": digest("result"),
        "outcome_checks": (check_preregistration("a" * 64, None),),
        "failed_checks": (),
    }
    values.update(overrides)
    return ExperimentOutcome(**values)  # type: ignore[arg-type]


def test_failed_checks_are_empty_exactly_when_completed() -> None:
    """`failed_checks` は `COMPLETED` なら空、それ以外なら空でない（D07 §19.3）。"""
    _outcome()
    with pytest.raises(KernelValueError):
        _outcome(failed_checks=(PolicyCheck.EVALUATION_RULE_MATCHES,))
    with pytest.raises(KernelValueError):
        _outcome(status=ExperimentStatus.FAILED_POST_RUN_CHECK)


def test_an_experiment_rejected_by_the_policy_has_no_run() -> None:
    """`REJECTED_BY_POLICY` は run を持たない（D07 §19.4）。"""
    with pytest.raises(KernelValueError):
        _outcome(
            status=ExperimentStatus.REJECTED_BY_POLICY,
            failed_checks=(PolicyCheck.HYPOTHESIS_PRESENT,),
        )


# --- 保存（D07 §19.1・§19.3、R4）------------------------------------------------------


def _store(root: Path) -> FileSystemExperimentStore:
    return FileSystemExperimentStore(
        root=root, experiment_name="strategy_a_t01", experiment_version=1
    )


def test_the_manifest_is_saved_under_the_name_and_version_and_round_trips(tmp_path: Path) -> None:
    """保存先は `runs/experiments/<名前>/v<版>/`（D07 §19.1）。読み戻すと同じ値になる。"""
    manifest = make_manifest()
    store = _store(tmp_path)

    assert store.save_manifest(manifest) is ManifestSaveResult.CREATED

    directory = experiment_directory(tmp_path, "strategy_a_t01", 1)
    assert store.directory == directory == tmp_path / "runs/experiments/strategy_a_t01/v1"
    assert read_experiment_manifest(directory) == manifest
    assert sorted(path.name for path in directory.iterdir()) == [EXPERIMENT_MANIFEST_FILE]


def test_saving_the_same_manifest_again_changes_nothing(tmp_path: Path) -> None:
    """同じ識別子の記録票が既にあれば何もしない（同じ版の再実行は正当。D07 §19.3）。"""
    manifest = make_manifest()
    store = _store(tmp_path)
    store.save_manifest(manifest)
    path = store.directory / EXPERIMENT_MANIFEST_FILE
    before = path.read_bytes()

    # 環境の群だけが違う（コードを直して再実行した）記録票も同じ識別子である。
    again = make_manifest(code_seed="edited code")
    assert store.save_manifest(again) is ManifestSaveResult.ALREADY_IDENTICAL
    assert path.read_bytes() == before


def test_a_different_manifest_under_the_same_version_is_not_written(tmp_path: Path) -> None:
    """識別子の違う記録票は書かずに `CONFLICT`。既存の記録票は変えない（検査 P3）。"""
    store = _store(tmp_path)
    store.save_manifest(make_manifest())
    path = store.directory / EXPERIMENT_MANIFEST_FILE
    before = path.read_bytes()

    assert store.save_manifest(make_manifest(hypothesis="別の仮説")) is ManifestSaveResult.CONFLICT
    assert path.read_bytes() == before


def test_an_unreadable_existing_manifest_is_a_conflict_and_is_kept(tmp_path: Path) -> None:
    """同じ版の既存の記録票が壊れて読めなければ、上書きせず `CONFLICT`（仮置き。PR #41 の指摘）。"""
    store = _store(tmp_path)
    store.directory.mkdir(parents=True)
    path = store.directory / EXPERIMENT_MANIFEST_FILE
    path.write_text("{ not json", encoding="utf-8")

    assert store.save_manifest(make_manifest()) is ManifestSaveResult.CONFLICT
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_the_previous_outcome_is_kept_under_the_next_number(tmp_path: Path) -> None:
    """保存が成功したら、前の結末記録を `experiment_outcome.<n>.json` へ退避する（D07 §19.3）。"""
    manifest = make_manifest()
    store = _store(tmp_path)
    store.save_manifest(manifest)
    first = _outcome(experiment_id=manifest.experiment_id)
    store.write_outcome(first)
    current = store.directory / EXPERIMENT_OUTCOME_FILE
    first_text = current.read_text(encoding="utf-8")

    store.save_manifest(manifest)
    assert not current.exists()
    assert (store.directory / "experiment_outcome.1.json").read_text(encoding="utf-8") == first_text

    store.write_outcome(replace(first, run_reused=True))
    store.save_manifest(manifest)
    assert (store.directory / "experiment_outcome.2.json").is_file()
    assert read_experiment_outcome(store.directory) is None


def test_a_conflicting_save_keeps_the_previous_outcome_in_place(tmp_path: Path) -> None:
    """拒否した保存では結末記録を退避しない（前回の結果はそのまま残る。D07 §19.4）。"""
    manifest = make_manifest()
    store = _store(tmp_path)
    store.save_manifest(manifest)
    store.write_outcome(_outcome(experiment_id=manifest.experiment_id))

    assert store.save_manifest(make_manifest(hypothesis="別の仮説")) is ManifestSaveResult.CONFLICT
    assert (store.directory / EXPERIMENT_OUTCOME_FILE).is_file()
    assert not (store.directory / "experiment_outcome.1.json").exists()


def test_the_outcome_round_trips(tmp_path: Path) -> None:
    """結末記録は JSON で読み戻すと同じ値になる（D07 §19.3、ADR-0027）。"""
    manifest = make_manifest()
    store = _store(tmp_path)
    store.save_manifest(manifest)
    outcome = _outcome(experiment_id=manifest.experiment_id)
    store.write_outcome(outcome)
    assert read_experiment_outcome(store.directory) == outcome


def test_an_outcome_of_another_experiment_is_refused(tmp_path: Path) -> None:
    """版のディレクトリの記録票と違う実験の結末記録は書かない（取り違えを作らない）。"""
    store = _store(tmp_path)
    store.save_manifest(make_manifest())
    with pytest.raises(KernelValueError):
        store.write_outcome(_outcome())


def test_a_linked_version_directory_is_not_written_through(tmp_path: Path) -> None:
    """根の下の要素がリンクなら、リンク先に書かずに失敗する（R4 と同じ境界。D06 §9.1）。"""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    experiments = tmp_path / "out/runs/experiments/strategy_a_t01"
    experiments.mkdir(parents=True)
    os.symlink(elsewhere, experiments / "v1")

    with pytest.raises(ArtifactAlreadyExists):
        _store(tmp_path / "out").save_manifest(make_manifest())
    assert list(elsewhere.iterdir()) == []


def test_a_broken_outcome_is_reported_not_ignored(tmp_path: Path) -> None:
    """結末記録が壊れていれば読込の誤りにする（再現は終了コード 2。仮置き。PR #41 の指摘）。"""
    manifest = make_manifest()
    store = _store(tmp_path)
    store.save_manifest(manifest)
    (store.directory / EXPERIMENT_OUTCOME_FILE).write_text(json.dumps({"status": 1}), "utf-8")
    with pytest.raises(KernelValueError):
        read_experiment_outcome(store.directory)


def test_a_reproduction_report_is_never_overwritten(tmp_path: Path) -> None:
    """再現の報告は新しく書く。既にあれば何も書かずに失敗する（R4）。"""
    write_reproduction(tmp_path, {"verdict": "REPRODUCED"})
    with pytest.raises(ArtifactAlreadyExists):
        write_reproduction(tmp_path, {"verdict": "RESULT_MISMATCH"})
    assert json.loads((tmp_path / "reproduction.json").read_text("utf-8")) == {
        "verdict": "REPRODUCED"
    }
