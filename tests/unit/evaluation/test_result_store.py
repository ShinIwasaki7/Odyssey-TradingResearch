"""成果物の読み書き（D07 §4.3・§8.2、ADR-0027）。

run manifest と結果 DTO を**読み戻せる**ことを確かめる。評価は保存済みの run だけを入力に
するので（D07 §4.1）、書いた形をそのまま読めなければ評価は起動できない。
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.backtest.trace.recorder import TraceTable, column_names
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultRepository,
    FileSystemResultWriter,
    FileSystemTraceSink,
    evaluation_directory,
    run_directory,
)
from odyssey_fx.evaluation.application.evaluate_run import COLUMN_SPECS, EvaluateRun
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION, EvaluationTable
from odyssey_fx.evaluation.application.ports import ColumnValueKind, TraceColumnSpec
from odyssey_fx.evaluation.domain.metrics import (
    CategoryCount,
    FillDiagnostic,
    MetricRecord,
    TradeRecord,
)
from odyssey_fx.evaluation.domain.status import ConsistencyCheckResult, EvaluationStatus
from tests.fixtures.backtest.harness import run_backtest
from tests.fixtures.backtest.paths import RUN_INTERVAL, execution_bars, signal_bars
from tests.fixtures.evaluation import traces


@pytest.fixture
def saved_run(tmp_path: Path) -> tuple[Path, object]:
    """バックテストの成果物を1件保存した状態（判断履歴15表・manifest・結果）。"""
    output = run_backtest(
        signal_bars=signal_bars(), execution_bars=execution_bars(), run_interval=RUN_INTERVAL
    )
    sink = FileSystemTraceSink(root=tmp_path, run_id=output.manifest.run_id)
    for table, rows in output.tables.items():
        sink.write(table, rows)
    FileSystemResultWriter(root=tmp_path).write(output.result, output.manifest)
    return tmp_path, output


def test_the_run_manifest_round_trips(saved_run: tuple[Path, object]) -> None:
    """保存した run manifest を読み戻すと同じ値になる（D06 §9.3、D07 §4.1 の入力2）。"""
    root, output = saved_run
    original = output.manifest  # type: ignore[attr-defined]
    restored = FileSystemResultRepository(root=root).read_manifest(original.run_id)

    assert restored.run_id == original.run_id
    assert restored.config_digest == original.config_digest
    assert restored.code_digest == original.code_digest
    assert restored.config.account == original.config.account
    assert restored.config.run_interval == original.config.run_interval
    assert restored.config.execution_series == original.config.execution_series
    assert restored.config.risk_policy_ref == original.config.risk_policy_ref
    assert restored.phases == original.phases
    assert restored.resolution_hierarchy == original.resolution_hierarchy
    assert restored.symbol_spec_ref == original.symbol_spec_ref
    assert restored.timeframe_def_refs == original.timeframe_def_refs
    assert restored.status == original.status
    assert restored.swap_modeled == original.swap_modeled


def test_the_result_round_trips(saved_run: tuple[Path, object]) -> None:
    """保存した結果 DTO を読み戻すと末尾の3集計と費用の内訳が同じになる（D06 §9.4）。"""
    root, output = saved_run
    original = output.result  # type: ignore[attr-defined]
    restored = FileSystemResultRepository(root=root).read_result(original.run_id)

    assert restored.run_id == original.run_id
    assert restored.status is original.status
    assert restored.trade_count == original.trade_count
    assert restored.opportunity_count == original.opportunity_count
    assert restored.swap_modeled == original.swap_modeled
    assert restored.summaries is not None and original.summaries is not None
    assert restored.summaries.realized == original.summaries.realized
    assert restored.summaries.equity_with_mtm == original.summaries.equity_with_mtm
    assert restored.summaries.hypothetical_closed == original.summaries.hypothetical_closed
    assert dict(restored.summaries.cost_breakdown) == dict(original.summaries.cost_breakdown)


def test_reading_a_table_returns_the_requested_columns_in_order(
    saved_run: tuple[Path, object],
) -> None:
    """要求した列だけを宣言順に返す（D07 §4.3）。"""
    root, output = saved_run
    run_id = output.manifest.run_id  # type: ignore[attr-defined]
    specs = (
        TraceColumnSpec(
            table=TraceTable.FILLS, column="fill_id", value_kind=ColumnValueKind.STRING
        ),
        TraceColumnSpec(table=TraceTable.FILLS, column="price", value_kind=ColumnValueKind.DECIMAL),
    )
    read = FileSystemResultRepository(root=root).read_table(run_id, TraceTable.FILLS, specs)
    assert read.table_present
    assert read.missing_columns == ()
    assert read.rows[0][0] == "FIL:00000001"
    assert read.rows[0][1] == "150.08"


def test_reading_a_missing_column_keeps_the_rows_empty(saved_run: tuple[Path, object]) -> None:
    """表に無い列を要求したら `missing_columns` に入れ、行は返さない（D07 §4.3）。"""
    root, output = saved_run
    run_id = output.manifest.run_id  # type: ignore[attr-defined]
    specs = (
        TraceColumnSpec(
            table=TraceTable.FILLS, column="not_a_column", value_kind=ColumnValueKind.STRING
        ),
    )
    read = FileSystemResultRepository(root=root).read_table(run_id, TraceTable.FILLS, specs)
    assert read.table_present
    assert read.missing_columns == ("not_a_column",)
    assert read.rows == ()


def test_reading_an_absent_table_says_so_instead_of_failing(tmp_path: Path) -> None:
    """表そのものが無ければ `table_present=False` を返す（D07 §4.3）。"""
    manifest = traces.manifest_for()
    read = FileSystemResultRepository(root=tmp_path).read_table(
        manifest.run_id, TraceTable.FILLS, COLUMN_SPECS[TraceTable.FILLS]
    )
    assert read.table_present is False
    assert read.rows == ()


def test_reading_a_run_without_a_manifest_says_which_file_is_missing(tmp_path: Path) -> None:
    manifest = traces.manifest_for()
    with pytest.raises(KernelValueError, match="manifest.json"):
        FileSystemResultRepository(root=tmp_path).read_manifest(manifest.run_id)


def test_the_list_column_comes_back_as_one_string(saved_run: tuple[Path, object]) -> None:
    """可変長の列は JSON の配列として1つの文字列に畳む（D07 §4.3 の `LIST_STRING`）。"""
    root, output = saved_run
    run_id = output.manifest.run_id  # type: ignore[attr-defined]
    specs = (
        TraceColumnSpec(
            table=TraceTable.EVALUATIONS,
            column="outcome_diagnoses",
            value_kind=ColumnValueKind.LIST_STRING,
        ),
    )
    read = FileSystemResultRepository(root=root).read_table(run_id, TraceTable.EVALUATIONS, specs)
    assert read.rows
    assert all(value is None or value.startswith("[") for (value,) in read.rows)


def test_the_evaluation_is_saved_under_the_run_and_its_identifier(
    saved_run: tuple[Path, object],
) -> None:
    """保存先は `runs/<run_id>/eval/<run_evaluation_id>/`（D07 §8.2、Q4 決定）。"""
    root, output = saved_run
    repository = FileSystemResultRepository(root=root)
    result = repository.read_result(output.result.run_id)  # type: ignore[attr-defined]
    report = EvaluateRun(
        evaluation_code_digest=output.manifest.code_digest  # type: ignore[attr-defined]
    ).evaluate(result, repository, METRIC_SET_VERSION)
    repository.write_evaluation(report, report.rows)

    directory = evaluation_directory(root, result.run_id, report.manifest.run_evaluation_id)
    assert directory.parent == run_directory(root, result.run_id) / "eval"
    assert (directory / "evaluation.json").is_file()
    for table in EvaluationTable:
        assert (directory / f"{table.value}.parquet").is_file()


def test_every_evaluation_table_keeps_its_columns_when_empty(tmp_path: Path) -> None:
    """行が1件も無い表でも列は落とさない（D07 §8.1）。

    表の有無や列の有無で状態を表すと、書き出しが途中で落ちた成果物と区別できない。
    """
    manifest = traces.manifest_for(status="FAILED_DATA_ERROR")
    repository = traces.repository_for(manifest=manifest)
    result = traces.result_for(manifest, status=RunStatus.FAILED_DATA_ERROR, with_summaries=False)
    report = EvaluateRun(evaluation_code_digest=manifest.code_digest).evaluate(
        result, repository, METRIC_SET_VERSION
    )
    assert report.status is EvaluationStatus.REJECTED

    store = FileSystemResultRepository(root=tmp_path)
    store.write_evaluation(report, report.rows)
    directory = evaluation_directory(tmp_path, manifest.run_id, report.manifest.run_evaluation_id)
    for table, row_type in (
        (EvaluationTable.METRICS, MetricRecord),
        (EvaluationTable.CATEGORY_COUNTS, CategoryCount),
        (EvaluationTable.TRADES, TradeRecord),
        (EvaluationTable.FILL_DIAGNOSTICS, FillDiagnostic),
        (EvaluationTable.CONSISTENCY_CHECKS, ConsistencyCheckResult),
    ):
        frame = pl.read_parquet(directory / f"{table.value}.parquet")
        assert list(frame.columns) == list(column_names(row_type)), table
    # 検査の表だけは行が入る（失敗を成果物だけで説明できるようにするため）。
    assert pl.read_parquet(directory / "CONSISTENCY_CHECKS.parquet").height > 0
    assert pl.read_parquet(directory / "METRICS.parquet").height == 0
