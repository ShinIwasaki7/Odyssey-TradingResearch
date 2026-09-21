"""紙上トレース T01 の8経路を、人工データで実際に通す（T01 §2〜§9）。

T01 は「どの型のどのフィールドに何が入るか」を人間が手で追った文書であり、**期待値の正本**
である。本テストは同じ数値になることを確かめる。

| 経路 | 内容 | ここでの検証 |
|---|---|---|
| 1 | 正常エントリー → 利確 | 約定 150.080・利確 151.240・決済 151.230・残高 1,036,736 |
| 2 | 正常エントリー → 損切り | 決済 149.490・残高 981,056 |
| 3a | 足内競合を下位足で解決 | `tests/unit/backtest/test_execution.py` の単体テスト |
| 3b | 足内競合を解決できない | 損切り優先・`UNRESOLVED_SL_PRIORITY` が1件 |
| 4 | 保護水準の妥当性違反 | 2系列の終値の食い違いで受付前拒否 |
| 5 | 建玉枠による受付前拒否 | 2件目の試行が `RISK` で拒否される |
| 6 | 有効時間切れ | 候補が期限外になり `NO_CANDIDATE` の受付前拒否になる |
| 7 | 週末持ち越し禁止 | 候補が週明けになり `CARRY_NOT_ALLOWED` になる |
| 8 | run 末尾の残存処理 | 残存注文の取消・機会の終端・最終 snapshot |
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from odyssey_fx.backtest.admission.admission import AttemptRejected
from odyssey_fx.backtest.admission.risk_assessment import RiskAssessment
from odyssey_fx.backtest.domain.fills import CostKind, FillRecord
from odyssey_fx.backtest.domain.orders import (
    AcceptedOrder,
    CloseCause,
    EntryRequest,
    OrderRequest,
    OrderStatus,
)
from odyssey_fx.backtest.execution.protection_hits import IntrabarResolution, ResolutionMethod
from odyssey_fx.backtest.portfolio.ledger import LedgerSnapshot
from odyssey_fx.backtest.trace.recorder import CompositeRow, ManagementApplication, TraceTable
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.money import Money, Price, decimal_from_str
from odyssey_fx.common.reason import ReasonCode
from odyssey_fx.common.time import Interval, UtcTime
from tests.fixtures.backtest.harness import (
    EXECUTION_POLICY,
    EXECUTION_SERIES,
    JPY,
    RunOutput,
    bars,
    flatten_all,
    run_backtest,
)
from tests.fixtures.backtest.paths import (
    CONFLICT_BAR,
    DECISION_TIME,
    QUIET_BAR,
    RUN_INTERVAL,
    STOP_LOSS_BAR,
    execution_bars,
    second_breakout_bars,
    signal_bars,
)
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _money(text: str) -> Money:
    return Money(decimal_from_str(text), JPY)


def _rows_of[T](output: RunOutput, table: TraceTable, kind: type[T]) -> tuple[T, ...]:
    """表の行のうち、期待した型のものだけを取り出す。"""
    return tuple(row for row in output.rows(table) if isinstance(row, kind))


def _fills(output: RunOutput) -> tuple[FillRecord, ...]:
    return _rows_of(output, TraceTable.FILLS, FillRecord)


def _assessments(output: RunOutput) -> tuple[RiskAssessment, ...]:
    return _rows_of(output, TraceTable.RISK_ASSESSMENTS, RiskAssessment)


def _snapshots(output: RunOutput) -> tuple[LedgerSnapshot, ...]:
    return _rows_of(output, TraceTable.LEDGER_SNAPSHOTS, LedgerSnapshot)


def _reject_reasons(output: RunOutput) -> list[ReasonCode]:
    rejected = _rows_of(output, TraceTable.ATTEMPT_DECISIONS, AttemptRejected)
    return [row.reason.code for row in rejected]


def _cost(fill: FillRecord, kind: CostKind) -> Money:
    """約定に紐付く費用（無ければテストを失敗させる）。"""
    entry = fill.cost_of(kind)
    assert entry is not None, kind
    return entry.account


# --- 経路1: 正常エントリー → 利確 ------------------------------------------


def _run_take_profit() -> RunOutput:
    return run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
    )


def test_path1_fills_at_the_paper_trace_price() -> None:
    """T01 §2.4: 始値 bid 150.050 → ask 150.070 → 滑り 0.010 → 150.080。"""
    output = _run_take_profit()

    fills = _fills(output)
    assert fills[0].price == _price("150.080")
    assert fills[0].quantity.units == decimal_from_str("32000")


def test_path1_reserves_the_paper_trace_amount() -> None:
    """T01 §2.3 の手順6・7: `R(32000) = 0.622 x 32000 = 19904`。"""
    output = _run_take_profit()

    rows = _assessments(output)
    assessment = rows[0]
    assert assessment.reached_step == 8
    assert assessment.quantity is not None
    assert assessment.quantity.units == decimal_from_str("32000")
    assert assessment.reservation_amount == _money("19904")
    assert assessment.cost_budget == _money("384")
    assert assessment.reference_quote.price == _price("150.060")


def test_path1_closes_at_the_take_profit() -> None:
    """T01 §2.6: 利確 151.240 を基準に滑り 0.010 を不利方向へ → 151.230。"""
    output = _run_take_profit()

    fills = _fills(output)
    assert len(fills) == 2
    assert fills[1].price == _price("151.230")
    assert output.result.trade_count == 1


def test_path1_balance_matches_the_hand_calculation() -> None:
    """T01 §2.6: `999968 + 36800 - 32 = 1,036,736`。"""
    output = _run_take_profit()

    assert output.context.ledger.balance == _money("1036736")


def test_path1_equity_after_the_fill_is_the_paper_trace_value() -> None:
    """T01 §2.4・§9.4 の行#2: 直前に完了した執行足の終値 150.040 で評価して 998,688。"""
    output = _run_take_profit()

    rows = [
        row for row in output.rows(TraceTable.LEDGER_SNAPSHOTS) if isinstance(row, LedgerSnapshot)
    ]
    entry_snapshot = next(
        row
        for row in rows
        if row.at.time == DECISION_TIME and row.at.phase.name == "EXECUTION_OPEN"
    )
    assert entry_snapshot.balance == _money("999968")
    assert entry_snapshot.equity == _money("998688")
    assert entry_snapshot.consumed == _money("19904")


def test_path1_maximum_drawdown_is_the_paper_trace_value() -> None:
    """T01 §9.4: 含み損益込みの最大ドローダウンは 1,312 円。"""
    output = _run_take_profit()

    equities = [
        row.equity.amount
        for row in output.rows(TraceTable.LEDGER_SNAPSHOTS)
        if isinstance(row, LedgerSnapshot)
    ]
    peak = equities[0]
    drawdown = decimal_from_str("0")
    for value in equities:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    assert drawdown == decimal_from_str("1312")


def test_path1_take_profit_is_placed_from_the_realised_risk() -> None:
    """T01 §2.5: `risk = 150.080 - 149.500 = 0.580` から利確 151.240。"""
    output = _run_take_profit()

    rows = _rows_of(output, TraceTable.MANAGEMENT_APPLICATIONS, CompositeRow)
    assert len(rows) == 1
    application = rows[0].parts[0][2]
    assert isinstance(application, ManagementApplication)
    assert application.applied is True
    assert application.rounded_take_profit == _price("151.240")
    assert application.realized_reward_risk == decimal_from_str("2")


def test_path1_costs_are_split_by_kind() -> None:
    """T01 §2.4: 手数料 32・滑り 320・提示幅 640。幅は買い側の約定にだけ立つ。"""
    output = _run_take_profit()

    entry, close = _fills(output)
    assert _cost(entry, CostKind.COMMISSION) == _money("32")
    assert _cost(entry, CostKind.SLIPPAGE_IN_PRICE) == _money("320")
    assert _cost(entry, CostKind.SPREAD_IN_PRICE) == _money("640")
    assert close.cost_of(CostKind.SPREAD_IN_PRICE) is None


def test_path1_writes_all_fifteen_tables() -> None:
    """D06 §9.4: 15表すべてのパスを結果 DTO が持つ。"""
    output = _run_take_profit()

    assert set(output.result.trace_tables) == set(TraceTable)
    assert output.result.status is RunStatus.COMPLETED


def test_the_same_input_produces_the_same_trace() -> None:
    """D06 §4.4: 同一入力の再実行で判断履歴が完全一致する（許容誤差なし）。"""
    first = flatten_all(_run_take_profit())
    second = flatten_all(_run_take_profit())

    assert first == second


# --- 経路2: 正常エントリー → 損切り ----------------------------------------


def test_path2_closes_at_the_stop_loss() -> None:
    """T01 §3: 損切り 149.500 を基準に滑り 0.010 → 149.490、残高 981,056。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(conflict=STOP_LOSS_BAR),
        run_interval=RUN_INTERVAL,
    )

    fills = _fills(output)
    assert fills[1].price == _price("149.490")
    assert output.context.ledger.balance == _money("981056")


# --- 経路3b: 足内競合を解決できない ----------------------------------------


def test_path3b_prefers_the_stop_when_the_order_cannot_be_observed() -> None:
    """T01 §4.2: 階層が1段なので両側に触れたら `UNRESOLVED_SL_PRIORITY` で損切りを採る。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(conflict=CONFLICT_BAR),
        run_interval=RUN_INTERVAL,
    )

    resolutions = [
        row
        for row in output.rows(TraceTable.INTRABAR_RESOLUTIONS)
        if isinstance(row, IntrabarResolution)
    ]
    assert len(resolutions) == 1
    assert resolutions[0].method is ResolutionMethod.UNRESOLVED_SL_PRIORITY
    assert resolutions[0].verdict is CloseCause.STOP_LOSS
    assert output.result.unresolved_intrabar_count == 1
    assert _fills(output)[1].price == _price("149.490")


# --- 経路4: 保護水準の妥当性違反 --------------------------------------------


def test_path4_rejects_when_the_two_series_disagree() -> None:
    """T01 §5: 執行系列の終値 149.980 に対し損切り 149.990 は不正（`PROTECTION_INVALID`）。"""
    output = run_backtest(
        signal_bars=signal_bars(stop_low="149.990"),
        execution_bars=execution_bars(reference_close="149.980"),
        run_interval=RUN_INTERVAL,
    )

    assert ReasonCode.PROTECTION_INVALID in _reject_reasons(output)
    assert _fills(output) == ()
    rows = _assessments(output)
    assert rows[0].reached_step == 4
    assert rows[0].stop_after_rounding is None
    assert rows[0].quantity is None


def test_path4_keeps_the_chain_from_opportunity_to_attempt() -> None:
    """T01 §5.2: 受付前拒否でも要求（表4）が残るので連鎖が切れない。"""
    output = run_backtest(
        signal_bars=signal_bars(stop_low="149.990"),
        execution_bars=execution_bars(reference_close="149.980"),
        run_interval=RUN_INTERVAL,
    )

    requests = _rows_of(output, TraceTable.ORDER_REQUESTS, OrderRequest)
    assert len(requests) == 1
    payload = requests[0].payload
    assert isinstance(payload, EntryRequest)
    assert payload.opportunity_id is not None
    assert output.rows(TraceTable.ORDERS) == ()


# --- 経路5b: 建玉枠による受付前拒否 -----------------------------------------


def test_path5b_rejects_the_second_entry_while_a_position_is_open() -> None:
    """T01 §6.2: 未終端のエントリー注文0 ＋ 開いている建玉1 は上限1 未満ではない。"""
    signal = signal_bars()
    extra = second_breakout_bars()
    output = run_backtest(
        signal_bars=signal + extra,
        execution_bars=execution_bars(conflict=QUIET_BAR),
        run_interval=RUN_INTERVAL,
    )

    assert ReasonCode.RISK in _reject_reasons(output)
    assessments = _assessments(output)
    slot = [
        check
        for assessment in assessments
        for check in assessment.checks
        if check.check == "position_slot"
    ]
    assert any(not check.passed for check in slot)


# --- 経路6: 有効時間切れ -----------------------------------------------------


def test_path6_rejects_when_the_candidate_falls_outside_the_deadline() -> None:
    """T01 §7: 1本見送った候補が期限外なら `NO_CANDIDATE` の受付前拒否になる。

    注文は作られないので、実行の中で期限切れ（遷移3）は起きない。
    """
    policy = replace(EXECUTION_POLICY, entry_delay_bars=1, entry_valid_for=timedelta(minutes=10))
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=RUN_INTERVAL,
        execution_policy=policy,
    )

    assert _reject_reasons(output) == [ReasonCode.NO_CANDIDATE]
    assert _fills(output) == ()


# --- 経路7: 週末持ち越し禁止 -------------------------------------------------


def test_path7_rejects_when_the_candidate_crosses_the_weekend() -> None:
    """T01 §8: 金曜 22:00Z の判断で候補が週明けになると `CARRY_NOT_ALLOWED`。

    週末の識別はカレンダーで行い、UTC の土日判定には置き換えない。金曜の週の終わり以降に
    執行足が無いため、候補は週明けの始値になる。
    """
    friday = UtcTime.from_components(2026, 1, 9, 22, 0)
    specs: list[tuple[str, str, str, str]] = []
    for index in range(20):
        high = "150.000" if index == 3 else "149.900"
        low = "149.500" if index == 7 else "149.700"
        specs.append(("149.800", high, low, "149.850"))
    specs.append(("149.900", "150.100", "149.800", "150.040"))
    signal = bars(SIGNAL_SERIES, friday - timedelta(hours=21), timedelta(hours=1), specs)
    execution = bars(
        EXECUTION_SERIES,
        friday - timedelta(minutes=15),
        timedelta(minutes=15),
        [("150.030", "150.060", "150.020", "150.040")],
    ) + bars(
        EXECUTION_SERIES,
        # 週明け（日曜 NY 17:00 = 冬時間の 22:00Z）の最初の執行足。
        friday + timedelta(days=2),
        timedelta(minutes=15),
        [("150.030", "150.060", "150.020", "150.040")],
    )
    output = run_backtest(
        signal_bars=signal,
        execution_bars=execution,
        run_interval=Interval(start=friday, end=friday + timedelta(minutes=30)),
    )

    assert _reject_reasons(output) == [ReasonCode.CARRY_NOT_ALLOWED]


# --- 経路8: run 末尾の残存処理 -----------------------------------------------


def test_path8_cancels_the_orders_that_remain_at_the_end() -> None:
    """T01 §9.1: 末尾に残った受付済み注文は `CANCELED`（理由 `RUN_END`）になる。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=Interval(start=DECISION_TIME, end=UtcTime.from_components(2026, 1, 6, 9, 15)),
    )

    states = output.context.ledger.order_states
    assert all(state.status is not OrderStatus.PENDING for state in states.values())
    assert output.result.status is RunStatus.COMPLETED


def test_path8_keeps_the_open_position_and_values_it() -> None:
    """T01 §9.3: 残存建玉は未決済のまま MTM 評価し、最終 snapshot に残す。"""
    output = run_backtest(
        signal_bars=signal_bars(),
        execution_bars=execution_bars(),
        run_interval=Interval(start=DECISION_TIME, end=UtcTime.from_components(2026, 1, 6, 9, 30)),
    )

    summaries = output.result.summaries
    assert summaries is not None
    assert summaries.realized == _money("-32")
    assert summaries.cost_breakdown[CostKind.COMMISSION] == _money("32")
    assert len(output.context.ledger.open_positions()) == 1
    final = _snapshots(output)[-1]
    assert final.at.phase.name == "RUN_END"


def test_the_accepted_order_is_recorded_once_per_primary_key() -> None:
    """D06 §9.2: 表7・表10・表11 の主キーは1行ずつになる。"""
    output = _run_take_profit()

    orders = [row for row in output.rows(TraceTable.ORDERS) if isinstance(row, AcceptedOrder)]
    assert len({order.order_id for order in orders}) == len(orders)
    assert len(output.rows(TraceTable.RESERVATIONS)) == 1
    assert len(output.rows(TraceTable.POSITIONS)) == 1
