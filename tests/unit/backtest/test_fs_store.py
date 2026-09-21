"""判断履歴と結果の保存（D06 §9.1、ADR-0027）。

15表が Parquet として `runs/<run_id>/` に並び、manifest と結果が JSON として書かれること、
十進数が文字列列のまま保存されることを確かめる。
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultWriter,
    FileSystemTraceSink,
    run_directory,
)
from tests.fixtures.backtest.harness import run_backtest
from tests.fixtures.backtest.paths import RUN_INTERVAL, execution_bars, signal_bars


def _write(root: Path) -> tuple[object, Path]:
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )
    sink = FileSystemTraceSink(root=root, run_id=output.result.run_id)
    for table in TraceTable:
        sink.write(table, output.rows(table))
    FileSystemResultWriter(root=root).write(output.result, output.manifest)
    return output, run_directory(root, output.result.run_id)


def test_all_fifteen_tables_are_written(tmp_path: Path) -> None:
    """D06 §9.2: 段階2で書き出す表は15件。"""
    _, directory = _write(tmp_path)

    written = sorted(path.name for path in directory.glob("*.parquet"))
    assert written == sorted(f"{table.value}.parquet" for table in TraceTable)
    assert len(written) == 15


def test_the_decimal_columns_stay_strings(tmp_path: Path) -> None:
    """ADR-0012: 十進数を二進浮動小数へ落とさない。"""
    _, directory = _write(tmp_path)

    fills = pl.read_parquet(directory / "FILLS.parquet")
    assert fills.schema["price"] == pl.String
    assert fills.schema["quantity"] == pl.String
    assert fills["price"].to_list() == ["15008e-2", "15123e-2"]


def test_the_cost_columns_are_split_by_kind(tmp_path: Path) -> None:
    """D06 §9.2（Q12 決定）: 区分別の金額列を足し、`costs` 列も残す。"""
    _, directory = _write(tmp_path)

    fills = pl.read_parquet(directory / "FILLS.parquet")
    assert fills["cost_commission_amount"].to_list() == ["32e0", "32e0"]
    assert fills["cost_spread_in_price_amount"].to_list() == ["64e1", None]
    assert "costs" in fills.columns


def test_the_manifest_keeps_the_capability_report_whole(tmp_path: Path) -> None:
    """D06 §9.3: 能力検査の結果を要約に畳まない。"""
    _, directory = _write(tmp_path)

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["capability_report"]["runnable"] is True
    assert manifest["swap_modeled"] is False
    assert len(manifest["phases"]) == 15
    assert manifest["phases"][14] == {"rank": 14, "name": "RUN_END"}


def test_the_result_names_all_the_tables(tmp_path: Path) -> None:
    """D06 §9.4: 結果 DTO は15表すべてのパスを持つ。"""
    _, directory = _write(tmp_path)

    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    assert set(result["trace_tables"]) == {table.value for table in TraceTable}
    assert result["status"] == "COMPLETED"
    assert result["trade_count"] == 1


def test_an_empty_table_is_still_written(tmp_path: Path) -> None:
    """D06 §9.4: 行が1件も無い表も書き出す（読む側が表の有無で分岐しない）。"""
    _, directory = _write(tmp_path)

    resolutions = pl.read_parquet(directory / "INTRABAR_RESOLUTIONS.parquet")
    assert resolutions.height == 1
    empty = pl.read_parquet(directory / "EVALUATIONS.parquet")
    assert empty.height >= 1
