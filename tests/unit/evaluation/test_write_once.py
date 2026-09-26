"""成果物の書き込みは「存在すれば、何も書かずに失敗する」（R4。D06 §9.1・§10.6、D07 §8.2）。

run の成果物（`runs/<run_id>/`）と評価の成果物（`runs/<run_id>/eval/<run_evaluation_id>/`）の
書き出し口が、同じ書き込み前検査（`create_artifact_directory`）を通ることを確かめる。
空のディレクトリも、前の実行が途中で残した書きかけのディレクトリも「ある」と数える。
置換の指示を持つのは run の成果物だけである（ADR-0006）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultRepository,
    FileSystemTraceSink,
    create_artifact_directory,
    evaluation_directory,
    replaced_manifest_name,
    require_absent,
    require_run_directory_absent,
    reserve_run_directory,
    run_directory,
)
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, EvaluationReport
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.evaluation.domain.errors import ArtifactAlreadyExists, EvaluationError
from tests.fixtures.backtest.harness import run_backtest
from tests.fixtures.backtest.paths import RUN_INTERVAL, execution_bars, signal_bars
from tests.fixtures.evaluation import traces


def _report() -> EvaluationReport:
    """保存に使う評価の結果（人工の判断履歴から作る）。"""
    manifest = traces.manifest_for()
    repository = traces.repository_for(manifest=manifest)
    result = traces.result_for(manifest)
    return EvaluateRun(evaluation_code_digest=manifest.code_digest).evaluate(
        result, repository, METRIC_SET_VERSION, traces.CALENDAR
    )


def _snapshot(directory: Path) -> dict[str, bytes]:
    """ディレクトリの下のファイルとその中身（書かれていないことの確認に使う）。"""
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


# --- 共通の書き込み前検査 ---------------------------------------------------


def test_the_error_is_a_typed_structural_error() -> None:
    """失敗は型で見分けられ、既存の捕捉（`KernelValueError`）にも掛かる。"""
    assert issubclass(ArtifactAlreadyExists, EvaluationError)
    assert issubclass(ArtifactAlreadyExists, KernelValueError)


def test_a_new_directory_is_created_with_its_parents(tmp_path: Path) -> None:
    """無ければ、親も含めて作る。"""
    target = tmp_path / "runs" / "abc" / "eval" / "def"
    assert create_artifact_directory(target, remedy="-") == target
    assert target.is_dir()


def test_an_existing_empty_directory_is_refused(tmp_path: Path) -> None:
    """空でも「ある」。中身を見て続きを書くと新旧の成果物が混ざる。"""
    target = tmp_path / "artifact"
    target.mkdir()
    with pytest.raises(ArtifactAlreadyExists, match="nothing was written"):
        create_artifact_directory(target, remedy="move it away")
    assert list(target.iterdir()) == []


def test_a_half_written_directory_is_refused_and_left_as_it_is(tmp_path: Path) -> None:
    """前の実行が途中で落ちて残した書きかけも、手を付けずに失敗する。"""
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "FILLS.parquet").write_bytes(b"half")
    with pytest.raises(ArtifactAlreadyExists):
        create_artifact_directory(target, remedy="-")
    assert _snapshot(target) == {"FILLS.parquet": b"half"}


def test_the_advance_check_counts_files_and_dangling_links(tmp_path: Path) -> None:
    """事前の検査は作らずに確かめる。ファイルも壊れたリンクも「ある」と数える。"""
    require_absent(tmp_path / "absent", remedy="-")
    assert not (tmp_path / "absent").exists()

    file = tmp_path / "file"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(ArtifactAlreadyExists):
        require_absent(file, remedy="-")

    link = tmp_path / "link"
    link.symlink_to(tmp_path / "nowhere")
    with pytest.raises(ArtifactAlreadyExists):
        require_absent(link, remedy="-")
    with pytest.raises(ArtifactAlreadyExists):
        create_artifact_directory(link, remedy="-")


# --- run の成果物（`runs/<run_id>/`）---------------------------------------


def test_the_run_directory_is_checked_before_the_run(tmp_path: Path) -> None:
    """run を始める前の検査は、保存先があれば置換の指示を案内して失敗する（D06 §10.6）。"""
    run_id = "a" * 64
    require_run_directory_absent(tmp_path, run_id)
    run_directory(tmp_path, run_id).mkdir(parents=True)
    with pytest.raises(ArtifactAlreadyExists, match="replace=True"):
        require_run_directory_absent(tmp_path, run_id)


def test_an_empty_run_directory_is_not_reserved_without_replace(tmp_path: Path) -> None:
    """空の `runs/<run_id>/` でも既定は失敗する（R4）。置換の指示があれば確保できる。"""
    run_id = "b" * 64
    directory = run_directory(tmp_path, run_id)
    directory.mkdir(parents=True)
    with pytest.raises(ArtifactAlreadyExists, match="already holds artifacts"):
        reserve_run_directory(tmp_path, run_id)
    assert reserve_run_directory(tmp_path, run_id, replace=True) == directory


def test_the_trace_sink_writes_nothing_into_an_existing_run_directory(tmp_path: Path) -> None:
    """判断履歴の書き出し口は、保存先があれば1表も書かずに失敗する。"""
    output = run_backtest(
        signal_bars=signal_bars(), execution_bars=execution_bars(), run_interval=RUN_INTERVAL
    )
    directory = run_directory(tmp_path, output.result.run_id)
    directory.mkdir(parents=True)
    sink = FileSystemTraceSink(root=tmp_path, run_id=output.result.run_id)
    with pytest.raises(ArtifactAlreadyExists):
        sink.write(TraceTable.FILLS, output.rows(TraceTable.FILLS))
    assert list(directory.iterdir()) == []


def _replace_with_manifest(root: Path, run_id: str, marker: str) -> Path:
    """`manifest.json` を置いた run を置換する（旧 manifest の中身に目印を入れる）。"""
    directory = run_directory(root, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({"marker": marker}), encoding="utf-8")
    return reserve_run_directory(root, run_id, replace=True)


def test_each_replacement_keeps_its_own_generation_of_the_manifest(tmp_path: Path) -> None:
    """置換を重ねても旧 manifest は世代ごとに残り、前の世代は上書きされない（D06 §9.3）。"""
    run_id = "c" * 64
    _replace_with_manifest(tmp_path, run_id, "first")
    directory = _replace_with_manifest(tmp_path, run_id, "second")

    assert replaced_manifest_name(1) == "manifest.replaced.001.json"
    kept = sorted(path.name for path in directory.glob("manifest.replaced.*"))
    assert kept == ["manifest.replaced.001.json", "manifest.replaced.002.json"]
    first = json.loads((directory / "manifest.replaced.001.json").read_text(encoding="utf-8"))
    second = json.loads((directory / "manifest.replaced.002.json").read_text(encoding="utf-8"))
    assert (first["marker"], second["marker"]) == ("first", "second")


def _run_with_kept(root: Path, run_id: str, kept: tuple[str, ...]) -> Path:
    """旧成果物と、置換で残した世代ファイルを置いた run のディレクトリ。"""
    directory = run_directory(root, run_id)
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    (directory / "FILLS.parquet").write_bytes(b"old")
    for name in kept:
        (directory / name).write_text(name, encoding="utf-8")
    return directory


@pytest.mark.parametrize(
    "kept",
    [
        ("manifest.replaced.002.json",),
        ("manifest.replaced.001.json", "manifest.replaced.004.json"),
        ("manifest.replaced.0001.json",),
        ("manifest.replaced.01.json",),
        ("manifest.replaced.abc.json",),
        ("manifest.replaced.001.json", "manifest.replaced.json.bak"),
    ],
)
def test_a_broken_generation_order_is_refused_before_anything_changes(
    tmp_path: Path, kept: tuple[str, ...]
) -> None:
    """残った世代が 001 から欠番なく並んでいなければ、何も作らず何も消さずに失敗する。

    欠番のある並びに次の世代を足すと、壊れた履歴のまま置換が成功してしまう（D06 §9.3）。
    """
    directory = _run_with_kept(tmp_path, "d" * 64, kept)
    before = _snapshot(directory)

    with pytest.raises(ArtifactAlreadyExists, match="without gaps"):
        reserve_run_directory(tmp_path, "d" * 64, replace=True)
    assert _snapshot(directory) == before


def test_a_broken_generation_order_is_refused_without_a_current_manifest(
    tmp_path: Path,
) -> None:
    """現在の manifest が無い書きかけの run でも、欠番のある世代の並びなら置換しない。

    世代の並びの検査は置換の手順の先頭で、manifest の有無に依らず必ず行う（D06 §9.3）。
    """
    directory = run_directory(tmp_path, "9" * 64)
    directory.mkdir(parents=True)
    (directory / "FILLS.parquet").write_bytes(b"half")
    for name in ("manifest.replaced.001.json", "manifest.replaced.004.json"):
        (directory / name).write_text(name, encoding="utf-8")
    before = _snapshot(directory)

    with pytest.raises(ArtifactAlreadyExists, match="without gaps"):
        reserve_run_directory(tmp_path, "9" * 64, replace=True)
    assert _snapshot(directory) == before


def test_a_half_written_run_without_a_manifest_is_replaced_keeping_its_generations(
    tmp_path: Path,
) -> None:
    """並びが正しければ、manifest の無い書きかけの run も置換でき、残した世代は消えない。"""
    directory = run_directory(tmp_path, "8" * 64)
    directory.mkdir(parents=True)
    (directory / "FILLS.parquet").write_bytes(b"half")
    (directory / "manifest.replaced.001.json").write_text("first", encoding="utf-8")

    reserve_run_directory(tmp_path, "8" * 64, replace=True)
    assert _snapshot(directory) == {"manifest.replaced.001.json": b"first"}


def test_a_file_at_the_run_directory_is_refused_on_replacement(tmp_path: Path) -> None:
    """`runs/<run_id>` がディレクトリでなければ、置換でも型付きで失敗し、触れない。"""
    path = run_directory(tmp_path, "6" * 64)
    path.parent.mkdir(parents=True)
    path.write_text("not a run", encoding="utf-8")
    with pytest.raises(ArtifactAlreadyExists, match="not a directory"):
        reserve_run_directory(tmp_path, "6" * 64, replace=True)
    assert path.read_text(encoding="utf-8") == "not a run"


def test_a_linked_run_directory_is_never_replaced(tmp_path: Path) -> None:
    """`runs/<run_id>` がリンクなら、置換はリンク先に触れずに失敗する（D06 §9.3）。"""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "manifest.json").write_text("{}", encoding="utf-8")
    (elsewhere / "FILLS.parquet").write_bytes(b"other")
    before = _snapshot(elsewhere)
    directory = run_directory(tmp_path, "7" * 64)
    directory.parent.mkdir(parents=True)
    directory.symlink_to(elsewhere)

    with pytest.raises(ArtifactAlreadyExists, match="symbolic link"):
        reserve_run_directory(tmp_path, "7" * 64, replace=True)
    assert _snapshot(elsewhere) == before


def test_an_existing_generation_file_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """次の世代の名前が確かめた後に作られていても、上書きせず何も消さずに失敗する（R4）。

    並びの確認と作成の間に別の置換が同じ世代を残した場合を、作成の直前にファイルを
    置いて再現する。作成は排他的なので、既存の世代ファイルは上書きされない。
    """
    directory = _run_with_kept(tmp_path, "f" * 64, ("manifest.replaced.001.json",))
    target = directory / "manifest.replaced.002.json"
    original_open = Path.open

    def racing_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if self == target and "x" in mode:
            target.write_text("written by another replacement", encoding="utf-8")
        return original_open(self, mode, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", racing_open)
    with pytest.raises(ArtifactAlreadyExists, match="never overwritten"):
        reserve_run_directory(tmp_path, "f" * 64, replace=True)
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "written by another replacement"
    assert (directory / "FILLS.parquet").read_bytes() == b"old"
    assert (directory / "manifest.json").is_file()


def test_a_legacy_replaced_manifest_is_kept(tmp_path: Path) -> None:
    """番号の無い旧形式の `manifest.replaced.json` も消さずに残す。"""
    run_id = "e" * 64
    directory = run_directory(tmp_path, run_id)
    directory.mkdir(parents=True)
    (directory / "manifest.replaced.json").write_text("legacy", encoding="utf-8")
    _replace_with_manifest(tmp_path, run_id, "first")
    assert (directory / "manifest.replaced.json").read_text(encoding="utf-8") == "legacy"
    assert (directory / "manifest.replaced.001.json").is_file()


def test_a_generation_number_below_one_is_refused() -> None:
    """世代番号は 1 から始まる。"""
    with pytest.raises(KernelValueError):
        replaced_manifest_name(0)


# --- 評価の成果物（`runs/<run_id>/eval/<run_evaluation_id>/`）--------------


def test_an_evaluation_is_not_written_twice(tmp_path: Path) -> None:
    """同じ評価の識別子の成果物は上書きしない。置換の指示も持たない（D07 §8.2）。"""
    report = _report()
    repository = FileSystemResultRepository(root=tmp_path)
    repository.write_evaluation(report, report.rows)
    manifest = report.manifest
    directory = evaluation_directory(tmp_path, manifest.run_id, manifest.run_evaluation_id)
    before = _snapshot(directory)

    with pytest.raises(ArtifactAlreadyExists, match="never replaced"):
        repository.write_evaluation(report, report.rows)
    assert _snapshot(directory) == before
    payload = json.loads((directory / "evaluation.json").read_text(encoding="utf-8"))
    assert payload["run_evaluation_id"] == str(manifest.run_evaluation_id)


def test_an_empty_evaluation_directory_is_refused_and_left_empty(tmp_path: Path) -> None:
    """空の評価の保存先があっても、5表も manifest も書かずに失敗する。"""
    report = _report()
    manifest = report.manifest
    directory = evaluation_directory(tmp_path, manifest.run_id, manifest.run_evaluation_id)
    directory.mkdir(parents=True)
    with pytest.raises(ArtifactAlreadyExists):
        FileSystemResultRepository(root=tmp_path).write_evaluation(report, report.rows)
    assert list(directory.iterdir()) == []


def test_another_evaluation_of_the_same_run_is_still_written(tmp_path: Path) -> None:
    """検査は識別子ごとの保存先に掛かる。別の評価の成果物が並んでいても書ける。"""
    report = _report()
    manifest = report.manifest
    sibling = evaluation_directory(tmp_path, manifest.run_id, manifest.run_evaluation_id).parent
    (sibling / ("0" * 64)).mkdir(parents=True)
    FileSystemResultRepository(root=tmp_path).write_evaluation(report, report.rows)
    written = evaluation_directory(tmp_path, manifest.run_id, manifest.run_evaluation_id)
    assert (written / "evaluation.json").is_file()
