"""探索の実験の同じ版の再実行の退避と「退避中」の印（D09 §11.3 の Q13 の1〜5）と記録のファイル名。

実験の版のディレクトリをテストの中に作り、`keep_previous_search_records`（記録票の保存の直後に
呼ぶ退避）を直接当てる。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.adapters.fs_store import (
    RETREAT_MARKER_FILE,
    keep_previous_search_records,
    next_kept_number,
    read_unit_records,
    unit_name,
    unit_of_name,
)
from odyssey_fx.evaluation.domain.errors import ArtifactAlreadyExists
from odyssey_fx.evaluation.domain.search import TrialPhase, TrialUnitKey


def _generation(directory: Path, tag: str) -> None:
    """前の世代の記録（結末記録・レポート・探索の記録）を置く。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "experiment_outcome.json").write_text(tag, encoding="utf-8")
    (directory / "report.md").write_text(tag, encoding="utf-8")
    (directory / "search").mkdir()
    (directory / "search" / "ledger_execution.json").write_text(tag, encoding="utf-8")


def test_the_previous_generation_moves_under_one_number_and_the_marker_is_removed(
    tmp_path: Path,
) -> None:
    _generation(tmp_path, "first")
    keep_previous_search_records(tmp_path)
    assert (tmp_path / "experiment_outcome.1.json").read_text(encoding="utf-8") == "first"
    assert (tmp_path / "report.1.md").is_file()
    assert (tmp_path / "search.1" / "ledger_execution.json").is_file()
    assert not (tmp_path / "search").exists()
    assert not (tmp_path / RETREAT_MARKER_FILE).exists()


def test_a_stopped_run_that_left_only_search_records_takes_the_next_number(tmp_path: Path) -> None:
    """途中で止まった実行は探索の記録だけを残すので、`search.<n>/` も連番に数える（§11.3）。"""
    (tmp_path / "search.1").mkdir(parents=True)
    (tmp_path / "search").mkdir()
    assert next_kept_number(tmp_path) == 2
    keep_previous_search_records(tmp_path)
    assert (tmp_path / "search.2").is_dir() and not (tmp_path / "search").exists()


def test_nothing_to_move_writes_no_marker(tmp_path: Path) -> None:
    keep_previous_search_records(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_marker_left_by_a_stopped_retreat_is_recovered_first(tmp_path: Path) -> None:
    """回復: 印の `n` で、元の場所にあるものだけを移し（移し済みは飛ばす）、印を消す（Q13 の4）。"""
    _generation(tmp_path, "previous")
    (tmp_path / "experiment_outcome.json").rename(tmp_path / "experiment_outcome.3.json")
    (tmp_path / RETREAT_MARKER_FILE).write_text(
        json.dumps({"schema_version": 1, "n": 3}), encoding="utf-8"
    )
    keep_previous_search_records(tmp_path)
    assert (tmp_path / "report.3.md").is_file()
    assert (tmp_path / "search.3").is_dir()
    assert not (tmp_path / RETREAT_MARKER_FILE).exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "experiment_outcome.3.json",
        "report.3.md",
        "search.3",
    ]


def test_the_same_record_both_in_place_and_kept_stops_and_keeps_the_marker(
    tmp_path: Path,
) -> None:
    """回復で元の場所と退避先の両方に同じ記録があれば、何も動かさず印も残して止める（Q13 の5）。"""
    _generation(tmp_path, "previous")
    (tmp_path / "report.2.md").write_text("kept", encoding="utf-8")
    (tmp_path / RETREAT_MARKER_FILE).write_text(
        json.dumps({"schema_version": 1, "n": 2}), encoding="utf-8"
    )
    with pytest.raises(ArtifactAlreadyExists, match="both in place and kept"):
        keep_previous_search_records(tmp_path)
    assert (tmp_path / "experiment_outcome.json").is_file()
    assert (tmp_path / "search").is_dir()
    assert (tmp_path / RETREAT_MARKER_FILE).is_file()


@pytest.mark.parametrize(
    "text", ["{", '{"schema_version": 2, "n": 1}', '{"schema_version": 1, "n": 0}']
)
def test_an_unreadable_marker_stops_without_moving_anything(tmp_path: Path, text: str) -> None:
    _generation(tmp_path, "previous")
    (tmp_path / RETREAT_MARKER_FILE).write_text(text, encoding="utf-8")
    with pytest.raises(KernelValueError):
        keep_previous_search_records(tmp_path)
    assert (tmp_path / "experiment_outcome.json").is_file()
    assert (tmp_path / RETREAT_MARKER_FILE).read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    "unit",
    [
        TrialUnitKey(fold_index=0, phase=TrialPhase.TRAIN, trial_index=0),
        TrialUnitKey(fold_index=12, phase=TrialPhase.VALIDATION, trial_index=305),
    ],
)
def test_the_unit_record_name_and_the_key_map_one_to_one(unit: TrialUnitKey) -> None:
    assert unit_of_name(unit_name(unit)) == unit


@pytest.mark.parametrize(
    "name", ["f00_TRAIN_t0", "f0_TRAIN_t01", "f0_train_t0", "f0_TRAIN_t0\n", "f-1_TRAIN_t0", "x"]
)
def test_a_name_outside_the_pattern_is_a_structural_error(name: str) -> None:
    """名前は `f(0|[1-9][0-9]*)_(TRAIN|VALIDATION)_t(0|[1-9][0-9]*)` に完全一致するものだけ。"""
    with pytest.raises(KernelValueError):
        unit_of_name(name)


def test_a_stray_file_among_the_unit_records_is_a_structural_error(tmp_path: Path) -> None:
    units = tmp_path / "search" / "units"
    units.mkdir(parents=True)
    (units / "f01_TRAIN_t0.json").write_text("{}", encoding="utf-8")
    with pytest.raises(KernelValueError):
        read_unit_records(tmp_path)
