"""レポートの表示の規則と、退避の連番（D07 §19.3・§22.1）。

レポート全体はコマンドの経路（`tests/integration/app/test_experiment_report.py`）で確かめる。
ここは1つの関数の範囲で閉じる規則だけを固定する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.evaluation.adapters import report
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_OUTCOME_FILE,
    REPORT_FILE,
    keep_previous_records,
    keep_previous_report,
    next_kept_number,
)
from odyssey_fx.evaluation.domain.errors import ArtifactAlreadyExists


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("0.0000125", "0.000012"),  # 偶数への丸め（ROUND_HALF_EVEN）
        ("0.0000135", "0.000014"),
        ("6.873950262920658775071972376", "6.873950"),
        ("1", "1.000000"),
        ("-0.2686320000", "-0.268632"),
    ],
)
def test_a_ratio_is_shown_to_six_decimals_half_even(stored: str, shown: str) -> None:
    """比率は小数第6位まで `ROUND_HALF_EVEN` で表示する（D07 §22.1。保存値は変えない）。"""
    assert report._ratio_text(stored) == shown


def test_a_markdown_cell_escapes_the_table_delimiters() -> None:
    """期待値・観測値に縦棒や改行があっても表を壊さない。"""
    assert report._cell("a|b\nc") == "a\\|b c"


def test_the_outcome_and_the_report_are_kept_under_the_same_number(tmp_path: Path) -> None:
    """結末記録とレポートは同じ連番で退避する（D07 §22.1「結末記録と同じ連番」）。"""
    (tmp_path / EXPERIMENT_OUTCOME_FILE).write_text("outcome-1", encoding="utf-8")
    (tmp_path / REPORT_FILE).write_text("report-1", encoding="utf-8")

    keep_previous_records(tmp_path)

    assert (tmp_path / "experiment_outcome.1.json").read_text(encoding="utf-8") == "outcome-1"
    assert (tmp_path / "report.1.md").read_text(encoding="utf-8") == "report-1"
    assert not (tmp_path / EXPERIMENT_OUTCOME_FILE).exists()
    assert not (tmp_path / REPORT_FILE).exists()


def test_the_numbers_are_shared_between_outcomes_and_reports(tmp_path: Path) -> None:
    """レポートだけを退避したあとも、次の退避は2つの系列の最大の次の番号から取る。"""
    (tmp_path / REPORT_FILE).write_text("rewritten", encoding="utf-8")
    keep_previous_report(tmp_path)
    assert (tmp_path / "report.1.md").is_file()

    (tmp_path / EXPERIMENT_OUTCOME_FILE).write_text("outcome", encoding="utf-8")
    (tmp_path / REPORT_FILE).write_text("report", encoding="utf-8")
    assert next_kept_number(tmp_path) == 2
    keep_previous_records(tmp_path)

    assert (tmp_path / "experiment_outcome.2.json").read_text(encoding="utf-8") == "outcome"
    assert (tmp_path / "report.2.md").read_text(encoding="utf-8") == "report"


def test_nothing_is_kept_when_there_is_nothing(tmp_path: Path) -> None:
    """初回の実行（結末記録もレポートも無い）では何もしない。"""
    keep_previous_records(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_linked_report_is_not_kept_through_the_link(tmp_path: Path) -> None:
    """レポートがリンクなら退避せずに失敗する（リンク越しに書かない。R4）。"""
    target = tmp_path / "elsewhere.md"
    target.write_text("x", encoding="utf-8")
    (tmp_path / REPORT_FILE).symlink_to(target)

    with pytest.raises(ArtifactAlreadyExists):
        keep_previous_records(tmp_path)
    assert (tmp_path / REPORT_FILE).is_symlink()
