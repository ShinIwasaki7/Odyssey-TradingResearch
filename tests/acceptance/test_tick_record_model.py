"""配布 tick 記録を再現するモデルの受入検証（2026-10-17 の指示 3-C の 7 項目。D12 §11.13 の
C-1〜C-7）。

人工の 1 分足（API の時刻 = UTC + 2 時間）を本番の受入れ（`build_mt5_pending`）で snapshot
にし、本番の
部品（as-of ビュー・公開フィード・`build_mt5_run_parts`・`RunBacktest`）で検証戦略 A の run を通す。

共通の場面（`tests/fixtures/mt5/run.py`。値は人工）: 金曜 2026-01-23 20:00Z に検証戦略 A
が買いの突破を
判断する。1 時間足は 1 分足から作られ、窓 20 本の高値 150.000・安値 149.000、突破の足の終値
150.040。

- 受付: 参照価格は直前に作られた執行足 `[19:45,20:00)` の終値 150.040（ask 150.060）。損切り
149.000。
  注文の有効期限は 20 分（20:20）。
- 約定: 候補の始値の分 20:00 の始値 bid 150.000 → ask 150.020 → 滑り 0.010 → 150.030。
  利確は `150.030 + 2 × (150.030 − 149.000) = 152.090`。
- 20:00 以後の 1 分足は既定の 4 値（150.000 / 150.100 / 149.900 / 150.000）で、損切りにも利確にも
  触れない。場面ごとに無記録の分と 4 値を差し替える。

各テストの docstring に対応する受入検証の番号（C-1〜C-7）を書く。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.backtest.domain.events import OrderEvent
from odyssey_fx.backtest.domain.fills import (
    BarExecutionInterval,
    ExactExecutionTime,
    FillRecord,
    GapKind,
)
from odyssey_fx.backtest.domain.orders import AcceptedEntryTerms, AcceptedOrder, OrderStatus
from odyssey_fx.backtest.execution.protection_hits import IntrabarResolution, ResolutionMethod
from odyssey_fx.backtest.trace.recorder import PublicationRow, ResumptionRow, TraceTable
from odyssey_fx.backtest.trace.result import BacktestResult, RunStatus
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultRepository,
    FileSystemResultWriter,
    FileSystemTraceSink,
)
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, EvaluationReport
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.backtest.harness import CODE_DIGEST, run_backtest
from tests.fixtures.mt5 import market as m5
from tests.fixtures.mt5.run import Mt5RunOutput, run_mt5
from tests.fixtures.synthetic import market

CALENDAR = market.calendar()
DECISION = UtcTime.parse("2026-01-23T20:00:00Z")
RUN = Interval(start=DECISION, end=UtcTime.parse("2026-01-23T21:00:00Z"))
#: 1 分足を作る区間（窓の 20 時間＋余裕。木曜 NY 17:00 から金曜の閉場まで）。
DATA = Interval(
    start=UtcTime.parse("2026-01-22T22:00:00Z"), end=UtcTime.parse("2026-01-23T22:00:00Z")
)
FLAT = ("149.005", "149.005", "149.005", "149.005")


def _at(text: str) -> UtcTime:
    return UtcTime.parse(f"2026-01-23T{text}:00Z")


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _signal_prices() -> dict[UtcTime, tuple[str, str, str, str]]:
    """20:00 より前の 1 分足の 4 値（1 時間足の窓と突破の足を作る）。"""
    prices: dict[UtcTime, tuple[str, str, str, str]] = {}
    moment = DATA.start
    while moment < DECISION:
        prices[moment] = FLAT
        moment = moment + timedelta(minutes=1)
    hour = UtcTime.parse("2026-01-22T23:00:00Z")
    while hour < _at("19:00"):
        prices[hour] = ("149.005", "150.000", "149.000", "149.005")
        hour = hour + timedelta(hours=1)
    moment = _at("19:00")
    while moment < DECISION:
        prices[moment] = ("149.900", "149.900", "149.900", "149.900")
        moment = moment + timedelta(minutes=1)
    prices[_at("19:00")] = ("149.900", "150.100", "149.800", "149.900")
    prices[_at("19:59")] = ("149.900", "150.040", "149.900", "150.040")
    return prices


def _run(
    tmp_path: Path,
    *,
    unrecorded: tuple[Interval, ...] = (),
    prices: dict[UtcTime, tuple[str, str, str, str]] | None = None,
    artifacts: bool = False,
) -> Mt5RunOutput:
    table = m5.write_rule_files(tmp_path)
    merged = _signal_prices()
    merged.update(prices or {})
    rows = m5.minute_rows(CALENDAR, DATA, unrecorded=unrecorded, prices=merged)
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])
    return run_mt5(
        m5.readable(pending),
        pending,
        CALENDAR,
        RUN,
        tmp_path,
        artifacts_root=tmp_path if artifacts else None,
    )


def _fills(output: Mt5RunOutput) -> list[FillRecord]:
    return [row for row in output.rows(TraceTable.FILLS) if isinstance(row, FillRecord)]


def _span(start: str, end: str) -> Interval:
    return Interval(start=_at(start), end=_at(end))


def test_baseline_fills_at_the_scheduled_open(tmp_path: Path) -> None:
    """場面の基準: 無記録が無ければ、候補の始値 20:00 で 150.030 に約定し、run
    の末尾まで建玉が残る。"""
    output = _run(tmp_path)
    assert output.result.status is RunStatus.COMPLETED
    (entry,) = _fills(output)
    assert entry.price == _price("150.030")
    assert entry.execution_time == ExactExecutionTime(time=DECISION)
    assert entry.gap_kind is None
    assert output.manifest.resumption_counts.resumptions == 0
    # D12-91 (a): 受付の参照価格は観測時刻つき（MT5 経路は最後に記録のある分の開始）。
    (order,) = [row for row in output.rows(TraceTable.ORDERS) if isinstance(row, AcceptedOrder)]
    assert isinstance(order.terms, AcceptedEntryTerms)
    assert order.terms.reference_quote.observed_at == _at("19:59")


def test_reference_quote_after_an_all_unrecorded_bar_keeps_the_last_observation(
    tmp_path: Path,
) -> None:
    """D12-91 (a)（D06 §18.9 の 5）: 直前の執行足が全分無記録なら、最後に記録のある分の終値を、
    その観測
    時刻とともに参照価格に使う（約定には使わない）。19:45〜19:59 を無記録にすると、
    参照価格の出どころは
    `[19:30,19:45)` の足で、観測時刻は 19:44（突破の終値は 19:44 の分に置く）。"""
    output = _run(
        tmp_path,
        unrecorded=(_span("19:45", "20:00"),),
        prices={_at("19:44"): ("149.900", "150.040", "149.900", "150.040")},
    )
    orders = [row for row in output.rows(TraceTable.ORDERS) if isinstance(row, AcceptedOrder)]
    assert orders
    assert isinstance(orders[0].terms, AcceptedEntryTerms)
    assert orders[0].terms.reference_quote.observed_at == _at("19:44")
    source = orders[0].terms.reference_quote.source_bar
    assert source is not None and source.bar_start == _at("19:30")


def test_c3_an_order_that_expires_while_unrecorded_never_fills_after_resumption(
    tmp_path: Path,
) -> None:
    """C-3（D12-86、D06 §18.4 の順 0）: 無記録の中で期限（20:20）が来た注文は、再開の分（20:25）
    でも約定しない。

    20:00〜20:24 を無記録にする。執行足 `[20:00,20:15)` は全分無記録で作られず、`[20:15,20:30)` の
    `ExecutionOpen` は最初の観測の分 20:25 に来る。その判断時点の期限切れ（rank 2）が約定（rank
    11）より
    先に注文を `EXPIRED` にし、表8 の `expired_in_unrecorded` が真になる。run
    は止まらない（D12-85）。
    """
    output = _run(tmp_path, unrecorded=(_span("20:00", "20:25"),))
    assert output.result.status is RunStatus.COMPLETED
    assert _fills(output) == []
    expiries = [
        row
        for row in output.rows(TraceTable.ORDER_EVENTS)
        if isinstance(row, OrderEvent) and row.to_status is OrderStatus.EXPIRED
    ]
    assert len(expiries) == 1
    assert expiries[0].expired_in_unrecorded is True
    assert expiries[0].at.time == _at("20:25")
    counts = output.manifest.resumption_counts
    assert counts.expired_in_unrecorded == 1
    assert counts.deferred_fills == 0
    resumptions = [
        row for row in output.rows(TraceTable.RESUMPTIONS) if isinstance(row, ResumptionRow)
    ]
    assert [(row.minute, row.unrecorded_minutes) for row in resumptions] == [(_at("20:25"), 25)]


def test_c3_companion_a_pending_order_fills_at_the_first_recorded_minute_before_expiry(
    tmp_path: Path,
) -> None:
    """C-3 の対（D06 §18.4 の順 2、D12-95 (a)）: 期限の前に再開すれば、
    待っていた成行は再開の分の始値で約定する。

    20:00〜20:07 を無記録にする。執行足 `[20:00,20:15)` の始値は最初の観測の分 20:08 の始値で、
    約定の時刻は
    20:08、区分は `DEFERRED_FILL`、無記録の分数は 8。約定の後の処理（約定通知と初期の利確）は
    20:08 の
    判断時点で行う。
    """
    output = _run(tmp_path, unrecorded=(_span("20:00", "20:08"),))
    (entry,) = _fills(output)
    assert entry.execution_time == ExactExecutionTime(time=_at("20:08"))
    assert entry.processed_at.time == _at("20:08")
    assert entry.gap_kind is GapKind.DEFERRED_FILL
    assert entry.resumed_after_unrecorded_minutes == 8
    assert entry.price == _price("150.030")
    assert output.manifest.resumption_counts.deferred_fills == 1


def test_c4_a_mid_bar_resumption_below_the_stop_closes_at_the_gapped_open(tmp_path: Path) -> None:
    """C-4（D06 §18.4 の順 1・§18.5・§18.7）: 執行足の途中の再開でも、
    損切りを飛び越えた始値で処理する。

    建玉（20:00 に 150.030）の後、執行足 `[20:30,20:45)` の 20:34〜20:37 を無記録にし、再開の分
    20:38 の
    始値を 148.620（損切り 149.000 より下）にする。決済は到達できない損切りの価格ではなく、始値
    bid 148.620 に
    決済の滑り 0.010 を不利に当てて 148.610。約定の時刻は 20:38、区分は `STOP_GAP`。
    """
    output = _run(
        tmp_path,
        unrecorded=(_span("20:34", "20:38"),),
        prices={_at("20:38"): ("148.620", "148.700", "148.600", "148.650")},
    )
    entry, close = _fills(output)
    assert close.price == _price("148.610")
    assert close.execution_time == ExactExecutionTime(time=_at("20:38"))
    assert close.gap_kind is GapKind.STOP_GAP
    assert close.resumed_after_unrecorded_minutes == 4
    assert output.manifest.resumption_counts.stop_gap_fills == 1


def test_c4_resumption_at_the_start_of_an_execution_bar_uses_the_first_observed_open(
    tmp_path: Path,
) -> None:
    """C-4（D03 §15.5、D06 §18.5）: 執行足の始値の分が無記録なら、最初の記録のある分（20:33）
    の始値で処理する。"""
    output = _run(
        tmp_path,
        unrecorded=(_span("20:30", "20:33"),),
        prices={_at("20:33"): ("148.620", "148.700", "148.600", "148.650")},
    )
    entry, close = _fills(output)
    assert close.price == _price("148.610")
    assert close.execution_time == ExactExecutionTime(time=_at("20:33"))
    assert close.processed_at.time == _at("20:33")
    assert close.gap_kind is GapKind.STOP_GAP


def test_c5_both_levels_in_one_minute_after_resumption_take_the_stop(tmp_path: Path) -> None:
    """C-5（ADR-0030、D06 §18.6）: 再開の後の 1 分足で利確・損切りの両方に届き順序が不明なら損切り。

    20:34〜20:35 を無記録にし、再開の分 20:36 の始値は 150.000（ギャップなし）、高値 152.500・
    安値 148.500
    （利確 152.090 と損切り 149.000 の両方）。最小解像度の 1 分足でも順序が分からないので
    `UNRESOLVED_SL_PRIORITY` で損切り。約定は損切り 149.000 に滑り 0.010 で 148.990。
    """
    output = _run(
        tmp_path,
        unrecorded=(_span("20:34", "20:36"),),
        prices={_at("20:36"): ("150.000", "152.500", "148.500", "150.000")},
    )
    entry, close = _fills(output)
    assert close.price == _price("148.990")
    assert close.gap_kind is None
    assert isinstance(close.execution_time, BarExecutionInterval)
    assert close.execution_time.interval.start == _at("20:36")
    (resolution,) = [
        row
        for row in output.rows(TraceTable.INTRABAR_RESOLUTIONS)
        if isinstance(row, IntrabarResolution)
    ]
    assert resolution.method is ResolutionMethod.UNRESOLVED_SL_PRIORITY
    assert output.manifest.unresolved_intrabar_count == 1


def test_take_profit_gap_uses_the_level_not_the_open(tmp_path: Path) -> None:
    """D06 §18.7（D12-77 (a)）: 再開の分の始値が利確を越えたら、
    利確の水準を基準価格にする（有利な滑りを取らない）。"""
    output = _run(
        tmp_path,
        unrecorded=(_span("20:34", "20:38"),),
        prices={_at("20:38"): ("153.000", "153.100", "152.900", "153.000")},
    )
    entry, close = _fills(output)
    assert close.gap_kind is GapKind.TAKE_PROFIT_GAP
    assert close.price == _price("152.080")  # 利確 152.090 に決済の滑り 0.010 を不利に


def test_unrecorded_tail_keeps_the_position_and_completes(tmp_path: Path) -> None:
    """D12-85・D12-91 (a)（D06 §18.11）: 末尾の執行足が全分無記録でも建玉を消さず、
    最後に記録のある分の
    終値で評価して `COMPLETED`。架空の決済をしない。"""
    output = _run(tmp_path, unrecorded=(_span("20:40", "22:00"),))
    assert output.result.status is RunStatus.COMPLETED
    assert len(_fills(output)) == 1
    assert len(output.context.ledger.open_positions()) == 1


def test_c6_an_acquisition_shortfall_is_rejected_not_read_as_unrecorded(tmp_path: Path) -> None:
    """C-6（D12-80、D06 §18.9）: 取得不足は無記録に読み替えられず、
    必要入力不足として実行前に拒否される。

    2026-01 の要求を「取得していない」（状態 `failed`）にし、前後の月は取得済みにする。1
    月の取引時間の分は
    取得不足 (b) で、無記録（`UNRECORDED_MINUTES`）には一切ならず、その分を含む上位足は
    `NO_RECORD_BAR`
    ではなく `ACQUISITION_SHORTFALL`。run は `FAILED_CAPABILITY` で、理由に取得不足の区間が出る。
    """
    table = m5.write_rule_files(tmp_path)
    december = m5.minute_rows(
        CALENDAR,
        Interval(
            start=UtcTime.parse("2025-12-29T22:00:00Z"), end=UtcTime.parse("2025-12-30T22:00:00Z")
        ),
    )
    february = m5.minute_rows(
        CALENDAR,
        Interval(
            start=UtcTime.parse("2026-02-02T22:00:00Z"), end=UtcTime.parse("2026-02-03T22:00:00Z")
        ),
    )
    pending = m5.build_pending(
        CALENDAR,
        table,
        [
            m5.MonthSpec(2025, 12, december),
            m5.MonthSpec(2026, 1, (), status="failed"),
            m5.MonthSpec(2026, 2, february),
        ],
    )
    january = Interval(
        start=UtcTime.parse("2026-01-01T00:00:00Z"), end=UtcTime.parse("2026-01-31T00:00:00Z")
    )
    in_january = [r for r in pending.report.results if r.interval.overlaps(january)]
    assert not [r for r in in_january if r.kind is CheckKind.UNRECORDED_MINUTES]
    assert not [r for r in in_january if r.kind is CheckKind.NO_RECORD_BAR]
    shortfall = [r for r in in_january if r.kind is CheckKind.ACQUISITION_SHORTFALL]
    assert shortfall
    assert {dict(r.detail)["reasons"] for r in shortfall} == {"request_failed_or_not_fetched"}

    output = run_mt5(m5.readable(pending), pending, CALENDAR, RUN, tmp_path)
    assert output.result.status is RunStatus.FAILED_CAPABILITY
    assert any(
        "acquisition shortfall" in text for text in output.result.capability_report.diagnostics
    )
    assert _fills(output) == []


def test_c6_a_revoked_time_rule_table_is_not_runnable(tmp_path: Path) -> None:
    """D12-48（D06 §18.9 の 3）: 承認を取り消した時刻の規則の表で作った snapshot の run
    は実行前に止まる。"""
    table = m5.write_rule_files(tmp_path, revoked="2026-10-17 取り消し（人工）")
    rows = m5.minute_rows(CALENDAR, DATA, prices=_signal_prices())
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])
    output = run_mt5(m5.readable(pending), pending, CALENDAR, RUN, tmp_path)
    assert output.result.status is RunStatus.FAILED_CAPABILITY
    coverage = output.result.capability_report.coverage
    assert coverage is not None and coverage.table_approved is False


def test_c1_partial_bar_coverage_reaches_the_run_artifacts(tmp_path: Path) -> None:
    """C-1（D12-82・D12-87、D06 §18.10、D07 §28.3）: 部分観測の足を作り、被覆情報が run
    の成果物まで残る。

    20:34〜20:37 の無記録で、1 時間足 `[20:00,21:00)` は 60 分のうち 56
    分の部分観測の足になる（作られ、
    「完全」とは呼ばない）。その公開（21:00 = run の末尾）が表20 の Parquet に
    `expected_minutes` 60・
    `recorded_minutes` 56・最初と最後の観測の時刻つきで残り、run manifest
    の能力検査の報告にも部分観測の
    足の数が残る。
    """
    output = _run(tmp_path, unrecorded=(_span("20:34", "20:38"),), artifacts=True)
    rows = [row for row in output.rows(TraceTable.PUBLICATIONS) if isinstance(row, PublicationRow)]
    hourly = [
        row
        for row in rows
        if row.bar_key.series.timeframe.id == "1h" and row.bar_key.bar_start == DECISION
    ]
    assert len(hourly) == 1
    assert (hourly[0].expected_minutes, hourly[0].recorded_minutes) == (60, 56)
    run_dir = tmp_path / "runs" / str(output.result.run_id)
    frame = pl.read_parquet(run_dir / "PUBLICATIONS.parquet")
    record = frame.filter(
        (pl.col("bar_key_bar_start") == str(DECISION))
        & (pl.col("bar_key_series") == "USDJPY/1h/bid")
    ).to_dicts()
    assert len(record) == 1
    assert record[0]["expected_minutes"] == 60
    assert record[0]["recorded_minutes"] == 56
    assert record[0]["first_observed_at"] == str(DECISION)
    assert record[0]["last_observed_at"] == str(_at("20:59"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    coverage = manifest["capability_report"]["coverage"]
    execution = [item for item in coverage["series"] if item["series"] == "USDJPY/15m/bid"]
    assert execution[0]["partial_bars"] == 1
    assert execution[0]["unrecorded_minutes"] == 4
    assert manifest["time_rule_table_ref"]["variant"] == "standard"


@pytest.mark.parametrize("minute", ["20:05", "20:44"])
def test_unrecorded_minutes_do_not_trigger_protection(tmp_path: Path, minute: str) -> None:
    """D06 §18.3（D12-76）: 無記録の分では判定しない。
    無記録の分の価格は入力に無いので結果に効かない。"""
    start = _at(minute)
    output = _run(tmp_path, unrecorded=(Interval(start=start, end=start + timedelta(minutes=2)),))
    assert len(_fills(output)) == 1


def test_fully_recorded_mt5_run_matches_the_single_level_run(tmp_path: Path) -> None:
    """D06 §18.13 の意味論: 全分記録ありの 1 分足から作った 15 分足の MT5 経路の run と、同じ
    15 分足を
    15 分足 1 段で回した run は、損切りと利確の両方に届く足が無ければ約定（時刻・価格）
    と損益が同じ。

    比べるのは同じ人工データから同じテストで作る 2 つの run（既存の固定出力とは比べない）。損切りに
    届く足を 20:40 に置く。
    """
    prices = {_at("20:40"): ("150.000", "150.000", "148.900", "149.000")}
    mt5 = _run(tmp_path, prices=prices)
    table = m5.write_rule_files(tmp_path / "plain")
    merged = _signal_prices()
    merged.update(prices)
    pending = m5.build_pending(
        CALENDAR, table, [m5.MonthSpec(2026, 1, m5.minute_rows(CALENDAR, DATA, prices=merged))]
    )

    def plain(series: SeriesId) -> list[Bar]:
        return [
            replace(bar, coverage=None)
            for pid, bars in pending.partition_bars.items()
            if pid.series == series
            for bar in sorted(bars, key=lambda item: item.bar_start.value)
        ]

    single = run_backtest(
        signal_bars=plain(market.series(timeframe_id="1h")),
        execution_bars=plain(market.series(timeframe_id="15m")),
        run_interval=RUN,
    )
    mt5_fills = [(fill.execution_time, fill.price) for fill in _fills(mt5)]
    single_fills = [
        (fill.execution_time, fill.price)
        for fill in single.rows(TraceTable.FILLS)
        if isinstance(fill, FillRecord)
    ]
    assert len(mt5_fills) == 2
    assert [price for _, price in mt5_fills] == [price for _, price in single_fills]
    assert mt5_fills[0][0] == single_fills[0][0]
    assert mt5.context.ledger.balance == single.context.ledger.balance


def _evaluate(output: Mt5RunOutput, root: Path) -> EvaluationReport:
    """run の成果物をファイルに書き、単一 run の評価（D07）を本物の読み出し口で通す。"""
    sink = FileSystemTraceSink(root=root, run_id=output.result.run_id)
    for table in TraceTable:
        sink.write(table, output.rows(table))
    FileSystemResultWriter(root=root).write(output.result, output.manifest)
    repository = FileSystemResultRepository(root=root)
    result = repository.read_result(output.result.run_id)
    assert isinstance(result, BacktestResult)
    return EvaluateRun(evaluation_code_digest=CODE_DIGEST).evaluate(
        result, repository, METRIC_SET_VERSION, CALENDAR
    )


def test_c1_coverage_reaches_the_evaluation(tmp_path: Path) -> None:
    """C-1（D07 §28.3）: 部分観測の足の公開・再開の分・
    再開の分での約定が評価の集計と約定の診断まで残る。

    評価 manifest は時刻の規則の表の参照を写す（結果ダイジェストには入れない）。
    """
    output = _run(
        tmp_path,
        unrecorded=(_span("20:34", "20:38"),),
        prices={_at("20:38"): ("148.620", "148.700", "148.600", "148.650")},
    )
    report = _evaluate(output, tmp_path / "eval")
    assert report.status is EvaluationStatus.COMPLETED
    counts = {(row.category.value, row.key): row.count for row in report.categories}
    assert counts[("PARTIAL_COVERAGE_PUBLICATION", "ALL")] >= 1
    assert counts[("PARTIAL_COVERAGE_PUBLICATION", "USDJPY/15m/bid")] == 1
    assert counts[("RESUMPTION_FILL", "STOP_GAP")] == 1
    assert counts[("UNRECORDED_EVENT", "RESUMPTIONS")] == 1
    gaps = [row for row in report.fill_diagnostics if row.gap_kind is not None]
    assert [(row.gap_kind, row.resumed_after_unrecorded_minutes) for row in gaps] == [
        ("STOP_GAP", 4)
    ]
    assert report.manifest.time_rule_table_ref is not None
    assert report.manifest.time_rule_table_ref.variant.value == "standard"


def test_c6_a_shortfall_run_is_rejected_by_the_evaluation(tmp_path: Path) -> None:
    """C-6（D07 §28.1）: 取得不足で `FAILED_CAPABILITY` の run は評価が
    `REJECTED`（指標を作らない）。"""
    table = m5.write_rule_files(tmp_path)
    rows = m5.minute_rows(CALENDAR, DATA, prices=_signal_prices())
    pending = m5.build_pending(
        CALENDAR,
        table,
        [m5.MonthSpec(2026, 1, rows), m5.MonthSpec(2026, 2, (), status="failed")],
    )
    run = Interval(start=DECISION, end=UtcTime.parse("2026-02-02T01:00:00Z"))
    output = run_mt5(m5.readable(pending), pending, CALENDAR, run, tmp_path)
    assert output.result.status is RunStatus.FAILED_CAPABILITY
    report = _evaluate(output, tmp_path / "eval")
    assert report.status is EvaluationStatus.REJECTED
    assert report.metrics == ()


def test_the_last_execution_bar_is_not_carried_over_a_closure() -> None:
    """D06 §18.9 の 5（第 2 巡のレビューの指摘）: run の開始の直前の分が休場（週末）なら、
    前の週の執行足を
    「最新の執行足」に補わない（全分無記録の執行足ではないため。15 分足 1 段の run と同じ）。
    直前の分が
    無記録なら補う。"""
    from types import SimpleNamespace
    from typing import Any

    from odyssey_fx.backtest.engine.loop import BacktestEngine

    friday_bar = SimpleNamespace(
        bar_start=_at("21:45"), bar_end=_at("22:00"), key=SimpleNamespace(bar_start=_at("21:45"))
    )
    minute = SimpleNamespace(bar_start=_at("21:59"))

    def engine(start: UtcTime, unrecorded: bool) -> object:
        made: Any = object.__new__(BacktestEngine)
        made._config = SimpleNamespace(run_interval=SimpleNamespace(start=start))
        made._minutes = SimpleNamespace(
            is_unrecorded=lambda moment: unrecorded,
            last_bar_before=lambda moment: minute,
        )
        made._execution = SimpleNamespace(
            next_bar_key_after=lambda moment: (
                friday_bar.key if moment < friday_bar.bar_start else None
            ),
            bar=lambda key: friday_bar,
        )
        made._last_complete = None
        made._seed_last_complete()
        return made._last_complete

    sunday_open = UtcTime.parse("2026-01-25T22:00:00Z")
    assert engine(sunday_open, unrecorded=False) is None
    assert engine(_at("22:30"), unrecorded=True) is friday_bar
