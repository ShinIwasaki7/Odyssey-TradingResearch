"""判断履歴と結果の保存（D06 §9.1、ADR-0027）。

15表が Parquet として `runs/<run_id>/` に並び、manifest と結果が JSON として書かれること、
十進数が文字列列のまま保存されることを確かめる。
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.backtest.trace.recorder import (
    TraceTable,
    flatten_row,
    table_column_kinds,
    table_columns,
)
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultWriter,
    FileSystemTraceSink,
    run_directory,
)
from tests.fixtures.backtest.harness import RunOutput, run_backtest
from tests.fixtures.backtest.paths import (
    CONFLICT_BAR,
    RUN_INTERVAL,
    execution_bars,
    signal_bars,
)


def _write(root: Path) -> tuple[RunOutput, Path]:
    """15表すべてに行が入る run を通してから保存する（足内競合も1件起こす）。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(conflict=CONFLICT_BAR),
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
    assert fills["price"].to_list() == ["15008e-2", "14949e-2"]


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
    assert result["unresolved_intrabar_count"] == 1


def test_an_empty_table_is_still_written(tmp_path: Path) -> None:
    """D06 §9.4: 行が1件も無い表も書き出す（読む側が表の有無で分岐しない）。"""
    _, directory = _write(tmp_path)

    resolutions = pl.read_parquet(directory / "INTRABAR_RESOLUTIONS.parquet")
    assert resolutions.height == 1
    empty = pl.read_parquet(directory / "EVALUATIONS.parquet")
    assert empty.height >= 1


def test_every_row_carries_the_run_id(tmp_path: Path) -> None:
    """上位設計書 §4.7.15: 全行が `run_id` を持つ。

    連番 ID は run 内でのみ一意なので、永続参照は `(run_id, ID)` の組になる。行そのものが
    `run_id` を持たない表（受付結果・建玉・足内競合・台帳 snapshot）でも列として入る。
    """
    output, directory = _write(tmp_path)

    for table in TraceTable:
        frame = pl.read_parquet(directory / f"{table.value}.parquet")
        assert frame.columns[0] == "run_id", table.value
        assert set(frame.columns) == set(table_columns(table)), table.value
        if frame.height:
            assert frame["run_id"].to_list() == [str(output.result.run_id)] * frame.height


def test_an_empty_table_keeps_its_columns(tmp_path: Path) -> None:
    """D06 §9.2: 行が1件も無い表でも列は落とさない。

    取引が1件も無かった正常な run と、必須の列を欠いた壊れた表とを読む側が区別できなく
    なるためである。
    """
    sink = FileSystemTraceSink(root=tmp_path, run_id="RUN")
    sink.write(TraceTable.FILLS, ())

    frame = pl.read_parquet(run_directory(tmp_path, "RUN") / "FILLS.parquet")
    assert frame.height == 0
    assert list(frame.columns) == list(table_columns(TraceTable.FILLS))
    assert "cost_commission_amount" in frame.columns


def test_the_declared_columns_match_the_real_rows(tmp_path: Path) -> None:
    """D06 §9.2: 書き写した7表の列名が、実際の行と一致する。

    記録層は受付層・執行層・台帳層を import できず、書き出し実装は戦略ランタイムを参照
    できないため、その7表の列名だけは名前を書き写している。食い違うと、行の有無で表の形が
    変わってしまう。
    """
    output, _ = _write(tmp_path)

    for table in TraceTable:
        rows = output.rows(table)
        assert rows, f"{table.value} should have at least one row in this run"
        for row in rows:
            assert set(flatten_row(row)) | {"run_id"} == set(table_columns(table)), table.value


def test_an_empty_table_keeps_the_same_column_types(tmp_path: Path) -> None:
    """行の有無で列の型が変わらない（空の表と行のある表を一緒に読める）。

    すべて文字列にすると、行のある表では `list` や整数だった列が空の表では文字列になり、
    読み込みで型が食い違う。
    """
    _, directory = _write(tmp_path)
    empty_root = tmp_path / "empty"
    sink = FileSystemTraceSink(root=empty_root, run_id="RUN")
    for table in TraceTable:
        sink.write(table, ())

    for table in TraceTable:
        populated = pl.read_parquet(directory / f"{table.value}.parquet")
        empty = pl.read_parquet(run_directory(empty_root, "RUN") / f"{table.value}.parquet")
        assert empty.height == 0, table.value
        assert dict(empty.schema) == dict(populated.schema), table.value


def test_the_declared_types_match_the_real_rows(tmp_path: Path) -> None:
    """宣言した列の型が、行のある表の型と一致する（書き写した7表を含む）。"""
    _, directory = _write(tmp_path)
    expected = {
        "string": pl.String,
        "int": pl.Int64,
        "bool": pl.Boolean,
        "list": pl.List(pl.String),
    }

    for table in TraceTable:
        frame = pl.read_parquet(directory / f"{table.value}.parquet")
        for name, kind in table_column_kinds(table).items():
            assert frame.schema[name] == expected[kind], f"{table.value}.{name}"


def test_an_existing_run_directory_is_not_overwritten(tmp_path: Path) -> None:
    """ADR-0006: 既存の成果物を無条件に上書きしない（既定は失敗）。

    同じ完全入力の再実行は同じ識別子になるので、黙って上書きすると前回の再現性の証拠が
    消える。途中まで書いてから失敗すると新旧の表が混ざるため、書き始める前に判断する。
    """
    _write(tmp_path)
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(conflict=CONFLICT_BAR),
        run_interval=RUN_INTERVAL,
    )

    with pytest.raises(KernelValueError, match="already holds artifacts"):
        FileSystemTraceSink(root=tmp_path, run_id=output.result.run_id).write(
            TraceTable.FILLS, output.rows(TraceTable.FILLS)
        )


def test_replacing_a_run_keeps_the_previous_manifest(tmp_path: Path) -> None:
    """ADR-0006: 置換するときも旧成果物の manifest を記録に残す。"""
    output, directory = _write(tmp_path)

    sink = FileSystemTraceSink(root=tmp_path, run_id=output.result.run_id, replace=True)
    for table in TraceTable:
        sink.write(table, output.rows(table))

    assert (directory / "manifest.replaced.json").exists()
    assert json.loads((directory / "manifest.replaced.json").read_text(encoding="utf-8"))[
        "run_id"
    ] == str(output.result.run_id)


def test_a_blocked_run_explains_itself(tmp_path: Path) -> None:
    """D06 §10.5 の手順6: 不合格の個別結果を落とさない。"""
    from odyssey_fx.backtest.trace.manifest import DataCapabilityReport
    from odyssey_fx.common.reason import Reason, ReasonCode
    from odyssey_fx.marketdata.domain.integrity import IntegrityReport

    report = DataCapabilityReport(
        compiled_match=True,
        integrity=IntegrityReport(),
        runnable=False,
        reason=Reason(ReasonCode.DATA_ERROR),
        diagnostics=("the symbol spec does not describe the traded symbol",),
    )

    assert report.diagnostics
    with pytest.raises(KernelValueError, match="individual findings"):
        DataCapabilityReport(
            compiled_match=True,
            integrity=IntegrityReport(),
            runnable=False,
            reason=Reason(ReasonCode.DATA_ERROR),
        )
