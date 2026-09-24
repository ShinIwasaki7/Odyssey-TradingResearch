"""判断履歴と結果の保存（D06 §9.1、ADR-0027）。

19表（段階2 の15表と段階3 の4表）が Parquet として `runs/<run_id>/` に並び、manifest と結果が
JSON として書かれること、十進数が文字列列のまま保存されることを確かめる。

検証戦略 A の run は段階3 の4表（待機の出来事・遡った入力・確認試行・有効性の再検査）に
行を出さず、損切り水準の更新（`UpdateStop`）も出さない。そこで、その5種の行は戦略ランタイム
の型から1件ずつ組み立てて同じ run の表に足し、**書き写した列の名前と型が実際の行と一致する
こと**を全表で確かめる（書き出し実装は戦略ランタイムの型を参照できないので列名が写しになる。
`backtest.trace.recorder` の `_BORROWED_COLUMNS`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.backtest.trace.recorder import (
    CompositeRow,
    ManagementApplication,
    SubstitutionOwner,
    TraceTable,
    flatten_row,
    table_column_kinds,
    table_columns,
)
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import (
    EvaluationId,
    OpportunityId,
    OutputId,
    PositionId,
    RequestId,
)
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.reason import MissingInputReason, Reason, ReasonCode
from odyssey_fx.common.time import ProcessingPoint, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultWriter,
    FileSystemTraceSink,
    run_directory,
)
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.strategy.compiler.compiled import ResolvedMarketSource
from odyssey_fx.strategy.declarations.opportunity import ValidityMode
from odyssey_fx.strategy.declarations.refs import MarketDataField, OutputRef
from odyssey_fx.strategy.records.payloads import UpdateStop
from odyssey_fx.strategy.runtime.confirmation import (
    ConfirmationAttempt,
    ConfirmationAttemptOutcome,
)
from odyssey_fx.strategy.runtime.opportunities import ValidityRecheck, ValidityRecheckOutcome
from odyssey_fx.strategy.runtime.requests import ManagementRequest, SubstitutedInput
from odyssey_fx.strategy.runtime.waiting import WaitEvent, WaitEventKind
from tests.fixtures.backtest.harness import EXECUTION_SERIES, RunOutput, run_backtest
from tests.fixtures.backtest.paths import (
    CONFLICT_BAR,
    RUN_INTERVAL,
    execution_bars,
    signal_bars,
)
from tests.fixtures.strategy.phases import BACKTEST_PHASES

_T = UtcTime.parse("2026-01-05T09:00:00Z")


def _point(phase: str, sequence: int = 0) -> ProcessingPoint:
    return ProcessingPoint(time=_T, phase=BACKTEST_PHASES.by_name(phase), sequence=sequence)


def _stage3_rows() -> dict[TraceTable, tuple[object, ...]]:
    """検証戦略 A の run が出さない行（段階3 の4表と、損切り水準の更新の表12 の行）。"""
    bar_key = BarKey(series=EXECUTION_SERIES, bar_start=_T)
    return {
        TraceTable.WAIT_EVENTS: (
            WaitEvent(
                request_id=RequestId(1),
                kind=WaitEventKind.INPUT_ARRIVED,
                at=_point("OPPORTUNITY_LIFECYCLE"),
                arrived=("prices",),
            ),
            WaitEvent(
                request_id=RequestId(2),
                kind=WaitEventKind.SUPERSEDED,
                at=_point("OPPORTUNITY_LIFECYCLE", 1),
                reason=Reason(ReasonCode.REQUEST_SUPERSEDED),
            ),
        ),
        TraceTable.INPUT_SUBSTITUTIONS: (
            CompositeRow(
                primary=SubstitutionOwner(evaluation_id=EvaluationId(3)),
                parts=(
                    (
                        "",
                        SubstitutedInput,
                        SubstitutedInput(
                            input_name="prices",
                            source_index=0,
                            source=ResolvedMarketSource(
                                series=EXECUTION_SERIES, field=MarketDataField.CLOSE
                            ),
                            freshness_time=_T,
                            reason=MissingInputReason.LATEST_BAR_UNAVAILABLE,
                            used_bar_key=bar_key,
                        ),
                    ),
                ),
            ),
        ),
        TraceTable.CONFIRMATION_ATTEMPTS: (
            ConfirmationAttempt(
                opportunity_id=OpportunityId(1),
                bar_key=bar_key,
                request_id=RequestId(4),
                outcome=ConfirmationAttemptOutcome.CONFIRMED,
            ),
        ),
        TraceTable.VALIDITY_RECHECKS: (
            ValidityRecheck(
                opportunity_id=OpportunityId(1),
                source=OutputRef("daily_above_ema", "condition"),
                mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST,
                at=_point("P4_CONFIRMATION"),
                outcome=ValidityRecheckOutcome.SATISFIED,
                output_id=OutputId(5),
            ),
        ),
        TraceTable.MANAGEMENT_APPLICATIONS: (
            CompositeRow(
                primary=ManagementRequest(
                    position_id=PositionId(1),
                    action=UpdateStop(stop_loss=Price(decimal_from_str("149.150"))),
                    decision_time=_T,
                    source_output_id=OutputId(6),
                ),
                parts=(
                    (
                        "application",
                        ManagementApplication,
                        ManagementApplication(
                            at=_point("ADMISSION"),
                            applied=True,
                            protection_version=2,
                            rounded_stop_loss=Price(decimal_from_str("149.150")),
                        ),
                    ),
                ),
            ),
        ),
    }


def _rows(output: RunOutput, table: TraceTable) -> tuple[object, ...]:
    """その表の行（検証戦略 A の run の行に、段階3 の行を足したもの）。"""
    return (*output.rows(table), *_stage3_rows().get(table, ()))


def _write(root: Path) -> tuple[RunOutput, Path]:
    """19表すべてに行が入る状態で保存する（足内競合も1件起こす）。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(conflict=CONFLICT_BAR),
        run_interval=RUN_INTERVAL,
    )
    sink = FileSystemTraceSink(root=root, run_id=output.result.run_id)
    for table in TraceTable:
        sink.write(table, _rows(output, table))
    FileSystemResultWriter(root=root).write(output.result, output.manifest)
    return output, run_directory(root, output.result.run_id)


def test_all_nineteen_tables_are_written(tmp_path: Path) -> None:
    """D06 §9.2: 書き出す表は段階2 の15件と段階3 の4件（表16〜19）の19件。"""
    _, directory = _write(tmp_path)

    written = sorted(path.name for path in directory.glob("*.parquet"))
    assert written == sorted(f"{table.value}.parquet" for table in TraceTable)
    assert len(written) == 19


def test_strategy_a_writes_no_stage3_rows() -> None:
    """段階2 の宣言は段階3 の4表に1行も出さない（D06 §9.2 v1.5「段階2 の15表は1つも変えない」）。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(conflict=CONFLICT_BAR),
        run_interval=RUN_INTERVAL,
    )
    for table in (
        TraceTable.WAIT_EVENTS,
        TraceTable.INPUT_SUBSTITUTIONS,
        TraceTable.CONFIRMATION_ATTEMPTS,
        TraceTable.VALIDITY_RECHECKS,
    ):
        assert output.rows(table) == (), table.value


def test_the_decimal_columns_stay_strings(tmp_path: Path) -> None:
    """ADR-0012: 十進数を二進浮動小数へ落とさない。"""
    _, directory = _write(tmp_path)

    fills = pl.read_parquet(directory / "FILLS.parquet")
    assert fills.schema["price"] == pl.String
    assert fills.schema["quantity"] == pl.String
    assert fills["price"].to_list() == ["150.08", "149.49"]


def test_the_cost_columns_are_split_by_kind(tmp_path: Path) -> None:
    """D06 §9.2（Q12 決定）: 区分別の金額列を足し、`costs` 列も残す。"""
    _, directory = _write(tmp_path)

    fills = pl.read_parquet(directory / "FILLS.parquet")
    assert fills["cost_commission_amount"].to_list() == ["32", "32"]
    assert fills["cost_spread_in_price_amount"].to_list() == ["640", None]
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
    """D06 §9.4: 結果 DTO は19表すべてのパスを持つ。"""
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
    """D06 §9.2: 書き写した11表の列名が、実際の行と一致する。

    記録層は受付層・執行層・台帳層を import できず、書き出し実装は戦略ランタイムを参照
    できないため、その11表の列名だけは名前を書き写している。食い違うと、行の有無で表の形が
    変わってしまう。
    """
    output, _ = _write(tmp_path)

    for table in TraceTable:
        rows = _rows(output, table)
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
    """宣言した列の型が、行のある表の型と一致する（書き写した11表を含む）。"""
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
        sink.write(table, _rows(output, table))

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
