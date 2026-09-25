"""判断履歴から指標・集計・診断・状態を作る（D07 §4〜§10）。

**指標集合 v2 の19件を紙上トレース T01 の検算値と突き合わせる**（D07 §11、§5.2・§5.5 の
「T01 検算」の列。#17 は T01 では検算できないので、日次の資産を与えた例で別に確かめる）。
判断履歴は `tests/fixtures/evaluation/traces.py` が T01 の数値から手で組み立てる。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.money import CurrencyCode, decimal_from_str
from odyssey_fx.common.refs import CodeDigest, ContentDigest
from odyssey_fx.evaluation.application.evaluate_run import (
    EvaluateRun,
    EvaluationReport,
)
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION, EvaluationTable
from odyssey_fx.evaluation.domain.metrics import (
    AmountValue,
    CategoryKind,
    CountValue,
    MetricId,
    MetricUnavailableReason,
    PriceOffsetValue,
    RatioValue,
    TradeOutcome,
    Unavailable,
)
from odyssey_fx.evaluation.domain.status import (
    CHECK_ALL_VALUES_READABLE,
    CHECK_ID_CHAIN_COMPLETE,
    CHECK_INPUT_KEYS_UNIQUE,
    CHECK_OPPORTUNITY_COUNT_MATCHES,
    CHECK_ORDER,
    CHECK_REALIZED_MATCHES_BALANCE,
    CHECK_REQUIRED_COLUMNS_PRESENT,
    CHECK_RUN_ID_CONSISTENT,
    CHECK_SINGLE_ACCOUNT_CURRENCY,
    CHECK_TRADE_COUNT_MATCHES,
    CheckLevel,
    CheckOutcome,
    ConsistencyCheckResult,
    EvaluationStatus,
)
from tests.fixtures.evaluation import traces

_CODE_DIGEST = CodeDigest(digest=ContentDigest.sha256("b" * 64))


def _evaluate(repository: traces.FakeRepository, **result_options: object) -> EvaluationReport:
    result = traces.result_for(repository.manifest, **result_options)  # type: ignore[arg-type]
    use_case = EvaluateRun(evaluation_code_digest=_CODE_DIGEST)
    return use_case.evaluate(result, repository, METRIC_SET_VERSION, traces.CALENDAR)


def _value(report: EvaluationReport, metric_id: MetricId) -> object:
    records = [record for record in report.metrics if record.metric_id is metric_id]
    assert len(records) == 1, records
    return records[0].value


def _amount(report: EvaluationReport, metric_id: MetricId) -> Decimal:
    value = _value(report, metric_id)
    assert isinstance(value, AmountValue), value
    return value.amount.amount


def _ratio(report: EvaluationReport, metric_id: MetricId) -> Decimal:
    value = _value(report, metric_id)
    assert isinstance(value, RatioValue), value
    return value.ratio


def _outcomes(report: EvaluationReport) -> dict[str, CheckOutcome]:
    return {check.check: check.outcome for check in report.checks}


def _failed(report: EvaluationReport) -> set[str]:
    """不合格（検査を実施して食い違いを見つけた）検査の名前。読めなかった検査は含めない。"""
    return {check.check for check in report.checks if check.outcome is CheckOutcome.FAILED}


def _check(report: EvaluationReport, name: str) -> ConsistencyCheckResult:
    found = [check for check in report.checks if check.check == name]
    assert len(found) == 1, found
    return found[0]


@pytest.fixture
def report() -> EvaluationReport:
    """T01 第9節の判断履歴を評価した結果。"""
    return _evaluate(traces.repository_for())


# --- 指標19件の検算（D07 §5.2・§5.5 の「T01 検算」）-------------------------


def test_the_metrics_are_the_nineteen_in_declaration_order(report: EvaluationReport) -> None:
    """指標集合 v2 の19件すべてが1行ずつ、宣言順に並ぶ（D07 §5.5・§8.1）。"""
    assert [record.metric_id for record in report.metrics] == list(MetricId)
    assert len(report.metrics) == 19


def test_net_profit_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#1: `1,036,706 − 1,000,000 = 36,706 JPY`（T01 §9.3）。"""
    assert _amount(report, MetricId.NET_PROFIT) == decimal_from_str("36706")


def test_closed_trade_profit_includes_the_entry_commission(report: EvaluationReport) -> None:
    """#2: 入場費用込みの取引損益の合計 `36,768 − 32 = 36,736 JPY`（D07 §7.3）。

    指標集合 v1 の `36,768`（決済側の手数料だけを含む確定損益）から、P1 の入場手数料
    32 円を引いた値になる。残存建玉 P2 は数えない。
    """
    assert _amount(report, MetricId.CLOSED_TRADE_PROFIT) == decimal_from_str("36736")
    # #1 純損益との差 −30 JPY は、未決済の建玉 P2 の入場手数料である（D07 §7.3）。
    difference = _amount(report, MetricId.NET_PROFIT) - _amount(
        report, MetricId.CLOSED_TRADE_PROFIT
    )
    assert difference == decimal_from_str("-30")


def test_trade_count_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#3: 完了取引は1件（T01 §9.3）。"""
    assert _value(report, MetricId.TRADE_COUNT) == CountValue(1)


def test_win_rate_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#4: `1 ÷ 1 = 1`（T01 §2.6 の1取引が勝ち）。"""
    assert _ratio(report, MetricId.WIN_RATE) == decimal_from_str("1")


def test_the_mtm_drawdown_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#5・#6: `1,312 JPY` と `0.001312`（T01 §9.4 の行#2）。"""
    assert _amount(report, MetricId.MAX_DRAWDOWN_MTM) == decimal_from_str("1312")
    assert _ratio(report, MetricId.MAX_DRAWDOWN_MTM_RATE) == decimal_from_str("0.001312")


def test_the_balance_drawdown_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#7・#8: `32 JPY` と `0.000032`（T01 §9.4 の参考値）。"""
    assert _amount(report, MetricId.MAX_DRAWDOWN_BALANCE) == decimal_from_str("32")
    assert _ratio(report, MetricId.MAX_DRAWDOWN_BALANCE_RATE) == decimal_from_str("0.000032")


def test_the_exposure_rate_of_the_closed_trade_matches_the_paper_trace() -> None:
    """#9: 完了取引だけを数えて `8,100 ÷ 1,036,800 = 0.0078125`（D07 §5.2 の検算値）。"""
    manifest = traces.manifest_for()
    repository = traces.repository_for(
        traces.closed_trade_only(str(manifest.run_id)), manifest=manifest
    )
    report = _evaluate(repository, trade_count=1, opportunity_count=2)
    assert _ratio(report, MetricId.EXPOSURE_RATE) == decimal_from_str("0.0078125")


def test_the_exposure_rate_ignores_the_open_position(report: EvaluationReport) -> None:
    """#9: 未決済建玉は数えない（D07 §5.2、2026-09-22 の人間の決定）。

    T01 第9節の run は完了取引1件と残存建玉1件を持つが、残存建玉の 734,400 秒
    （木曜 10:00Z → 金曜 22:00Z）は分子に入らないので、完了取引だけの run と同じ値になる。
    """
    assert _ratio(report, MetricId.EXPOSURE_RATE) == decimal_from_str("0.0078125")


def test_the_cost_metrics_match_the_paper_trace(report: EvaluationReport) -> None:
    """#10・#11: 手数料 `94 JPY`、価格反映済み `940 + 1,240 = 2,180 JPY`（T01 §9.3）。"""
    assert _amount(report, MetricId.COST_CHARGED_TOTAL) == decimal_from_str("94")
    assert _amount(report, MetricId.COST_PRICE_EMBEDDED_TOTAL) == decimal_from_str("2180")


def test_the_adverse_fill_offset_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#12: `+1 × (150.080 − 150.060) = 0.020`（T01 §2.4）。

    2件目の入場は参照価格より有利（`151.000 − 151.020 = −0.020`）なので、最大値には
    影響しない（式は `max(0, …)` である）。
    """
    value = _value(report, MetricId.MAX_ADVERSE_FILL_OFFSET)
    assert isinstance(value, PriceOffsetValue)
    assert value.offset.value == decimal_from_str("0.020")


def test_the_reference_summaries_match_the_paper_trace(report: EvaluationReport) -> None:
    """#13・#14: 含み込み資産 `1,051,706 JPY`、仮決済損益 `14,670 JPY`（T01 §9.3）。"""
    assert _amount(report, MetricId.END_EQUITY_MTM) == decimal_from_str("1051706")
    assert _amount(report, MetricId.HYPOTHETICAL_CLOSED_PROFIT) == decimal_from_str("14670")


def test_the_net_return_rate_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#15: `36,706 ÷ 1,000,000 = 0.036706`（T01 §9.3）。"""
    assert _ratio(report, MetricId.NET_RETURN_RATE) == decimal_from_str("0.036706")


def test_the_annualized_return_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#16: `N = 10`（2015-01-05〜09・12〜16）で `0.036706 × 260 ÷ 10 = 0.954356`（D07 §5.5）。"""
    assert _ratio(report, MetricId.ANNUALIZED_RETURN) == decimal_from_str("0.954356")
    counts = {record.metric_id: record.observation_count for record in report.metrics}
    assert counts[MetricId.ANNUALIZED_RETURN] == 10


def test_the_sharpe_ratio_uses_the_daily_equity_of_the_trading_days(
    report: EvaluationReport,
) -> None:
    """#17: 取引日の終わり以前で最後の `equity` の日次系列から作る（D07 §5.5）。

    T01 の `equity` は取引日の終わりには次の値になる（無い日は前日を持ち越す）。
    1/5 は snapshot が無いので初期残高、1/6 は 11:15Z の決済後、1/8 は P2 入場後、
    1/16 は run 末尾の `RUN_END`（取引日の終わりちょうど。「以前」に含む）。
    """
    from odyssey_fx.evaluation.domain.metrics import annualized_sharpe_ratio

    daily = [
        "1000000",  # E_0 初期残高
        "1000000",  # 1/5
        "1036736",  # 1/6
        "1036736",  # 1/7
        "1036706",  # 1/8
        "1036706",  # 1/9
        "1036706",  # 1/12
        "1036706",  # 1/13
        "1036706",  # 1/14
        "1036706",  # 1/15
        "1051706",  # 1/16
    ]
    expected = annualized_sharpe_ratio([decimal_from_str(value) for value in daily])
    assert isinstance(expected, Decimal)
    assert _ratio(report, MetricId.ANNUALIZED_SHARPE_RATIO) == expected


def test_the_profit_factor_is_undefined_without_a_losing_trade(report: EvaluationReport) -> None:
    """#18: 負け取引が無いので値なし（無限大を値にしない。D07 §5.5）。"""
    value = _value(report, MetricId.PROFIT_FACTOR)
    assert isinstance(value, Unavailable)
    assert value.reason is MetricUnavailableReason.UNDEFINED_DENOMINATOR


def test_the_average_trade_profit_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#19: `36,736 ÷ 1 = 36,736 JPY`（D07 §5.5）。"""
    assert _amount(report, MetricId.AVERAGE_TRADE_PROFIT) == decimal_from_str("36736")


def test_the_new_metrics_carry_the_caveats_of_the_design(report: EvaluationReport) -> None:
    """#16〜#19 の注記（D07 §5.5・§7.2）。`ENTRY_COST_EXCLUDED` はどの指標にも付かない。"""
    from odyssey_fx.evaluation.domain.metrics import MetricCaveat

    caveats = {record.metric_id: record.caveats for record in report.metrics}
    assert caveats[MetricId.ANNUALIZED_RETURN] == (MetricCaveat.SWAP_NOT_MODELED,)
    assert caveats[MetricId.ANNUALIZED_SHARPE_RATIO] == (MetricCaveat.SWAP_NOT_MODELED,)
    for metric_id in (MetricId.PROFIT_FACTOR, MetricId.AVERAGE_TRADE_PROFIT):
        assert caveats[metric_id] == (
            MetricCaveat.SWAP_NOT_MODELED,
            MetricCaveat.OPEN_POSITION_EXCLUDED,
        )
    assert "ENTRY_COST_EXCLUDED" not in {item.value for item in MetricCaveat}


def test_a_losing_trade_after_the_entry_commission_is_a_loss() -> None:
    """勝敗は入場費用込みの取引損益の符号で決める（D07 §7.3、Q10 決定）。

    決済側だけ見ると 10 円の勝ちでも、入場の手数料 32 円を含めると負けになる。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.POSITIONS] = [
        {**tables[TraceTable.POSITIONS][0], "realized_amount": "10"},
        tables[TraceTable.POSITIONS][1],
    ]
    # 末尾の確定損益と台帳が合うように、最後の残高を 10 − 32 − 30 だけ動かした値にする。
    ledger = tables[TraceTable.LEDGER_SNAPSHOTS]
    tables[TraceTable.LEDGER_SNAPSHOTS] = [
        *ledger[:-1],
        {**ledger[-1], "balance_amount": "999948"},
    ]
    repository = traces.repository_for(tables, manifest=manifest)
    result = traces.result_for(manifest)
    from dataclasses import replace

    from odyssey_fx.common.money import Money

    assert result.summaries is not None
    result = replace(
        result,
        summaries=replace(
            result.summaries, realized=Money(decimal_from_str("-52"), CurrencyCode("JPY"))
        ),
    )
    report = EvaluateRun(evaluation_code_digest=_CODE_DIGEST).evaluate(
        result, repository, METRIC_SET_VERSION, traces.CALENDAR
    )
    assert report.status is EvaluationStatus.COMPLETED, report.checks
    trade = report.trades[0]
    assert trade.realized.amount == decimal_from_str("10")
    assert trade.trade_profit.amount == decimal_from_str("-22")
    assert trade.outcome is TradeOutcome.LOSS
    assert _ratio(report, MetricId.WIN_RATE) == decimal_from_str("0")
    # 勝ち取引が無く負けが1件なので、プロフィットファクターは 0 ÷ 22 = 0。
    assert _ratio(report, MetricId.PROFIT_FACTOR) == decimal_from_str("0")


def test_every_metric_records_how_many_observations_it_used(report: EvaluationReport) -> None:
    """観測件数を持つ（D07 §5.1）。1取引から出た比率と多数から出た比率を区別するため。"""
    counts = {record.metric_id: record.observation_count for record in report.metrics}
    assert counts[MetricId.NET_PROFIT] == 5  # 台帳 snapshot の件数
    assert counts[MetricId.WIN_RATE] == 1  # 完了取引の件数
    assert counts[MetricId.MAX_ADVERSE_FILL_OFFSET] == 2  # 参照価格を持つ約定の件数


# --- 取引と診断（D07 §5.2・§6.2）---------------------------------------------


def test_the_trade_record_matches_the_paper_trace(report: EvaluationReport) -> None:
    """完了取引1件の内容が T01 §2 と一致する。"""
    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.trade_seq == 1
    assert trade.entry_price.value == decimal_from_str("150.08")
    assert trade.exit_price.value == decimal_from_str("151.23")
    assert trade.realized.amount == decimal_from_str("36768")
    assert trade.outcome is TradeOutcome.WIN
    assert trade.holding == timedelta(hours=2, minutes=15)
    assert str(trade.opportunity_id) == "OPP:00000001"


def test_the_trade_record_carries_the_costs_of_both_fills(report: EvaluationReport) -> None:
    """取引単位の費用6列と取引損益（D07 §7.3 の T01 の表）。

    決済約定は売りで bid 基準なので提示価格の幅の記録が無く、**0 円ではなく `None`** の
    まま残す（「費用が0円だった」と「費用記録が無い」を区別する。D06 §9.2）。
    """
    trade = report.trades[0]

    def amount(value: object) -> Decimal:
        from odyssey_fx.common.money import Money

        assert isinstance(value, Money), value
        return value.amount

    assert amount(trade.entry_commission) == decimal_from_str("32")
    assert amount(trade.entry_slippage_in_price) == decimal_from_str("320")
    assert amount(trade.entry_spread_in_price) == decimal_from_str("640")
    assert amount(trade.close_commission) == decimal_from_str("32")
    assert amount(trade.close_slippage_in_price) == decimal_from_str("320")
    assert trade.close_spread_in_price is None
    assert amount(trade.trade_profit) == decimal_from_str("36736")


def test_the_fill_diagnostics_cover_every_fill(report: EvaluationReport) -> None:
    """約定1件につき1行。決済約定は参照価格を持たない（D07 §6.2）。"""
    assert [str(item.fill_id) for item in report.fill_diagnostics] == [
        "FIL:00000001",
        "FIL:00000002",
        "FIL:00000003",
    ]
    entry, close, second = report.fill_diagnostics
    assert entry.adverse_fill_offset is not None
    assert entry.adverse_fill_offset.value == decimal_from_str("0.020")
    assert entry.reference_to_fill == timedelta(0)
    assert entry.acceptance_to_fill == timedelta(0)
    # 決済約定には参照価格が無いので、置き換えずに値なしにする（上位 §4.7.12）。
    assert close.adverse_fill_offset is None
    assert close.reference_to_fill is None
    assert second.adverse_fill_offset is not None
    assert second.adverse_fill_offset.value == decimal_from_str("-0.020")


# --- 集計（D07 §6.1）----------------------------------------------------------


def test_every_category_key_gets_a_row_even_at_zero(report: EvaluationReport) -> None:
    """語彙が有限な集計は0件の鍵も行として出す（D07 §6.1）。"""
    from odyssey_fx.evaluation.domain.metrics import CATEGORY_KEYS

    for kind, keys in CATEGORY_KEYS.items():
        emitted = [row.key for row in report.categories if row.category is kind]
        assert emitted[: len(keys)] == list(keys), kind


def test_the_categories_count_the_paper_trace_events(report: EvaluationReport) -> None:
    """T01 の履歴で起きた事象が数えられる（D07 §6.1）。"""
    counts = {(row.category, row.key): row.count for row in report.categories}
    assert counts[(CategoryKind.OPPORTUNITY_TERMINAL_REASON, "FULFILLED_BY_ORDER_ACCEPTANCE")] == 2
    assert counts[(CategoryKind.EVALUATION_OUTCOME, "EVALUATED")] == 1
    assert counts[(CategoryKind.EVALUATION_OUTCOME, "SKIPPED")] == 1
    assert counts[(CategoryKind.MISSING_INPUT_REASON, "WARMUP_INSUFFICIENT")] == 1
    assert counts[(CategoryKind.CLOSE_CAUSE, "TAKE_PROFIT")] == 1
    assert counts[(CategoryKind.INTRABAR_METHOD, "SINGLE_HIT")] == 1
    # 受付前拒否は1件も無い（T01 第9節の run はすべて受け付けられる）。
    assert counts[(CategoryKind.ENTRY_REJECTION_REASON, "RISK")] == 0
    # 待機が無いので、要求単位の集計は記録単位と同じ（D07 §6.3）。
    assert counts[(CategoryKind.EVALUATION_REQUEST_FINAL_OUTCOME, "EVALUATED")] == 1
    assert counts[(CategoryKind.EVALUATION_REQUEST_FINAL_OUTCOME, "SKIPPED")] == 1


def test_a_waiting_request_counts_once_by_its_final_record() -> None:
    """待機をはさんだ要求は、要求単位の集計では最後の記録で1件と数える（D07 §6.3、Q13）。

    記録単位の集計（`EVALUATION_OUTCOME`）は2件のまま変えない。最後の記録は
    `decision_time` の昇順、同じ時刻なら `evaluation_id` の昇順で最後のもの。行の並びを
    入れ替えても結果は同じである。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    evaluated, skipped = tables[TraceTable.EVALUATIONS]
    waiting = {
        **evaluated,
        # 待機の記録は診断を必ず持つ（D05 §6.8）。
        "outcome_diagnoses": skipped["outcome_diagnoses"],
        "evaluation_id": "EVAL:00000003",
        "request_id": "REQ:00000001",
        "decision_time": "2015-01-06T08:30:00Z",
        "outcome_kind": "WAITING",
    }
    tie_first = {
        **skipped,
        "evaluation_id": "EVAL:00000004",
        "request_id": "REQ:00000003",
        "decision_time": "2015-01-07T10:00:00Z",
        "outcome_kind": "WAITING",
    }
    tie_last = {
        **evaluated,
        "evaluation_id": "EVAL:00000005",
        "request_id": "REQ:00000003",
        "decision_time": "2015-01-07T10:00:00Z",
        "outcome_kind": "SUPERSEDED",
    }
    rows = [evaluated, skipped, waiting, tie_first, tie_last]
    reports = []
    for ordering in (rows, list(reversed(rows))):
        tables[TraceTable.EVALUATIONS] = ordering
        reports.append(_evaluate(traces.repository_for(tables, manifest=manifest)))
    first, second = reports
    assert first.categories == second.categories
    counts = {(row.category, row.key): row.count for row in first.categories}
    request = CategoryKind.EVALUATION_REQUEST_FINAL_OUTCOME
    assert counts[(CategoryKind.EVALUATION_OUTCOME, "WAITING")] == 2
    assert counts[(request, "EVALUATED")] == 1
    assert counts[(request, "SKIPPED")] == 1
    assert counts[(request, "SUPERSEDED")] == 1
    assert counts[(request, "WAITING")] == 0
    assert sum(row.count for row in first.categories if row.category is request) == 3


def test_the_rejection_categories_split_entry_from_close() -> None:
    """拒否をエントリーと決済に分ける（D07 §6.1）。分けるのは要求の種別で行う。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.ATTEMPT_DECISIONS] = [
        {
            **tables[TraceTable.ATTEMPT_DECISIONS][0],
            "kind": "REJECTED",
            "order_id": None,
            "reason_code": "RISK",
        },
        {
            **tables[TraceTable.ATTEMPT_DECISIONS][1],
            "kind": "REJECTED",
            "order_id": None,
            "reason_code": "RUN_END",
        },
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    counts = {(row.category, row.key): row.count for row in report.categories}
    assert counts[(CategoryKind.ENTRY_REJECTION_REASON, "RISK")] == 1
    assert counts[(CategoryKind.CLOSE_REJECTION_REASON, "RUN_END")] == 1
    assert counts[(CategoryKind.ENTRY_REJECTION_REASON, "RUN_END")] == 0


# --- 状態と整合検査（D07 §10）------------------------------------------------


def test_a_healthy_evaluation_completes_with_all_checks_run(report: EvaluationReport) -> None:
    """検査13件が宣言順に全件残り、状態は完了になる（D07 §10.1・§10.4）。"""
    assert report.status is EvaluationStatus.COMPLETED
    assert [check.check for check in report.checks] == list(CHECK_ORDER)
    assert len(report.checks) == 13
    assert all(check.outcome is CheckOutcome.PASSED for check in report.checks)
    assert report.manifest.fatal_failure_count == 0
    assert report.manifest.warning_failure_count == 0
    assert report.manifest.unreadable_check_count == 0


def test_zero_trades_is_not_a_failure() -> None:
    """0取引は完了とし、取引に依存する指標を値なしにする（D07 §10.1）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.POSITIONS] = []
    tables[TraceTable.FILLS] = []
    tables[TraceTable.INTRABAR_RESOLUTIONS] = []
    report = _evaluate(
        traces.repository_for(tables, manifest=manifest), trade_count=0, opportunity_count=2
    )
    assert report.status is EvaluationStatus.COMPLETED
    assert _value(report, MetricId.TRADE_COUNT) == CountValue(0)
    for metric_id in (
        MetricId.CLOSED_TRADE_PROFIT,
        MetricId.WIN_RATE,
        MetricId.PROFIT_FACTOR,
        MetricId.AVERAGE_TRADE_PROFIT,
    ):
        value = _value(report, metric_id)
        assert isinstance(value, Unavailable)
        assert value.reason is MetricUnavailableReason.NO_TRADES
    # 建玉が1件も無ければ保有時間0は観測された事実であり、値なしにしない（D07 §5.2 の #9）。
    assert _ratio(report, MetricId.EXPOSURE_RATE) == decimal_from_str("0")
    # エントリー約定が無ければ不利約定幅は値なしになる。
    offset = _value(report, MetricId.MAX_ADVERSE_FILL_OFFSET)
    assert isinstance(offset, Unavailable)
    assert offset.reason is MetricUnavailableReason.NO_OBSERVATIONS


def test_an_empty_ledger_makes_the_balance_metrics_unavailable() -> None:
    """台帳 snapshot が空なら純損益と最大ドローダウンは値なしになる（D07 §5.2）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.LEDGER_SNAPSHOTS] = []
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    # 末尾の確定損益と比べられないので致命検査 C5 が不合格になり、指標は出さない。
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    failed = [check.check for check in report.checks if not check.passed]
    assert CHECK_REALIZED_MATCHES_BALANCE in failed


def test_a_run_that_did_not_complete_is_rejected_without_metrics() -> None:
    """正常完走していない run は指標を出さず、状態と診断だけを出す（D07 §10.1、Q6 決定）。"""
    manifest = traces.manifest_for(status="FAILED_DATA_ERROR")
    repository = traces.repository_for(manifest=manifest)
    report = _evaluate(
        repository, status=RunStatus.FAILED_DATA_ERROR, with_summaries=False, trade_count=1
    )
    assert report.status is EvaluationStatus.REJECTED
    assert report.metrics == ()
    assert report.categories == ()
    assert report.trades == ()
    assert report.fill_diagnostics == ()
    # 末尾の集計が無い run では、その集計と比べる検査（C5）を実施しない。
    assert CHECK_REALIZED_MATCHES_BALANCE not in {check.check for check in report.checks}
    assert report.checks, "検査結果が1件も残らないと失敗を説明できない"
    assert report.manifest.run_status is RunStatus.FAILED_DATA_ERROR


def test_a_missing_table_is_a_fatal_check_not_an_exception() -> None:
    """表が無いことは致命検査の不合格として残す（D07 §4.3・§10.2 の C1）。

    その表を読む検査は実施できないので `UNREADABLE` として残し、表を読まない検査は実施する
    （D07 §10.4）。13件すべての結果が残る。
    """
    repository = traces.repository_for(absent=frozenset({TraceTable.POSITIONS}))
    report = _evaluate(repository)
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    assert _failed(report) == {CHECK_REQUIRED_COLUMNS_PRESENT}
    assert "POSITIONS" in report.checks[0].observed
    outcomes = _outcomes(report)
    assert outcomes[CHECK_TRADE_COUNT_MATCHES] is CheckOutcome.UNREADABLE
    assert outcomes[CHECK_OPPORTUNITY_COUNT_MATCHES] is CheckOutcome.PASSED
    # C9 は9表すべての値を読む検査なので、表が無ければ合格にせず読めなかったとする。
    assert outcomes[CHECK_ALL_VALUES_READABLE] is CheckOutcome.UNREADABLE
    assert "POSITIONS" in _check(report, CHECK_ALL_VALUES_READABLE).observed
    assert "POSITIONS" in _check(report, CHECK_TRADE_COUNT_MATCHES).observed
    assert [check.check for check in report.checks] == list(CHECK_ORDER)
    assert report.manifest.unreadable_check_count == sum(
        1 for outcome in outcomes.values() if outcome is CheckOutcome.UNREADABLE
    )


def test_a_missing_column_is_distinguished_from_an_empty_table() -> None:
    """必須列の欠落と行0件を区別する（D07 §4.3）。"""
    repository = traces.repository_for(
        dropped_columns={TraceTable.POSITIONS: frozenset({"realized_amount"})}
    )
    report = _evaluate(repository)
    assert report.status is EvaluationStatus.FAILED
    assert "realized_amount" in report.checks[0].observed


def test_a_trade_count_mismatch_is_fatal() -> None:
    """完了取引の件数が結果 DTO と違えば致命の不合格（D07 §10.2 の C3）。"""
    report = _evaluate(traces.repository_for(), trade_count=2)
    assert report.status is EvaluationStatus.FAILED
    failing = {check.check for check in report.checks if not check.passed}
    assert failing == {CHECK_TRADE_COUNT_MATCHES}
    assert _check(report, CHECK_TRADE_COUNT_MATCHES).outcome is CheckOutcome.FAILED


def test_a_broken_id_chain_is_fatal() -> None:
    """建玉から試行までの連鎖が切れていれば致命の不合格（D07 §10.2 の C4）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.ORDER_REQUESTS] = [
        row for row in tables[TraceTable.ORDER_REQUESTS] if row["attempt_id"] != "ATT:00000001"
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    failing = {check.check for check in report.checks if not check.passed}
    assert CHECK_ID_CHAIN_COMPLETE in failing


def test_a_foreign_currency_column_is_fatal() -> None:
    """口座通貨と違う通貨の金額列があれば致命の不合格（D07 §10.2 の C8・§7.1）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.POSITIONS] = [
        {**tables[TraceTable.POSITIONS][0], "realized_currency": "USD"},
        tables[TraceTable.POSITIONS][1],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    failing = {check.check for check in report.checks if not check.passed}
    assert CHECK_SINGLE_ACCOUNT_CURRENCY in failing


def test_an_opportunity_count_mismatch_is_only_a_warning() -> None:
    """終端の件数の食い違いは警告で、評価は完了する（D07 §10.2 の C6）。"""
    report = _evaluate(traces.repository_for(), opportunity_count=3)
    assert report.status is EvaluationStatus.COMPLETED
    warning = [check for check in report.checks if check.check == CHECK_OPPORTUNITY_COUNT_MATCHES]
    assert warning[0].passed is False
    assert warning[0].level is CheckLevel.WARNING
    assert report.manifest.warning_failure_count == 1
    assert report.metrics, "警告では指標を落とさない"


def test_the_snapshot_order_warning_does_not_change_the_metrics() -> None:
    """台帳 snapshot の並びが崩れていても、整列し直して続行する（D07 §10.2 の C7）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.LEDGER_SNAPSHOTS] = list(reversed(tables[TraceTable.LEDGER_SNAPSHOTS]))
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.COMPLETED
    assert report.manifest.warning_failure_count == 1
    assert _amount(report, MetricId.NET_PROFIT) == decimal_from_str("36706")
    assert _amount(report, MetricId.MAX_DRAWDOWN_MTM) == decimal_from_str("1312")


# --- 評価 manifest（D07 §8.3・§9.2）------------------------------------------


def test_the_manifest_records_the_identity_and_the_swap_note(report: EvaluationReport) -> None:
    """識別・コード・入力・明記・状態・再現性の6群が揃う（D07 §8.3）。"""
    manifest = report.manifest
    assert manifest.metric_set_version == METRIC_SET_VERSION
    assert manifest.evaluation_code_digest == _CODE_DIGEST
    assert manifest.run_code_digest == report.manifest.run_code_digest
    assert manifest.account_currency == CurrencyCode("JPY")
    assert manifest.swap_modeled is False
    assert manifest.input_tables == traces.all_input_tables()
    assert manifest.run_failure_reason is None


def test_the_evaluation_identifier_changes_with_the_metric_set_version() -> None:
    """指標集合の版を変えると評価の識別子が変わる（D07 §9.2）。"""
    repository = traces.repository_for()
    result = traces.result_for(repository.manifest)
    use_case = EvaluateRun(evaluation_code_digest=_CODE_DIGEST)
    first = use_case.evaluate(result, repository, 1, traces.CALENDAR)
    second = use_case.evaluate(result, repository, 2, traces.CALENDAR)
    assert first.manifest.run_evaluation_id != second.manifest.run_evaluation_id


def test_the_evaluation_identifier_changes_with_the_evaluating_code() -> None:
    """評価コードを変えると評価の識別子が変わる（D07 §9.2、Q5 決定）。"""
    repository = traces.repository_for()
    result = traces.result_for(repository.manifest)
    first = EvaluateRun(evaluation_code_digest=_CODE_DIGEST).evaluate(
        result, repository, METRIC_SET_VERSION, traces.CALENDAR
    )
    other = CodeDigest(digest=ContentDigest.sha256("c" * 64))
    second = EvaluateRun(evaluation_code_digest=other).evaluate(
        result, repository, METRIC_SET_VERSION, traces.CALENDAR
    )
    assert first.manifest.run_evaluation_id != second.manifest.run_evaluation_id
    assert first.manifest.result_digest == second.manifest.result_digest


def test_the_report_carries_all_five_tables_in_every_state(report: EvaluationReport) -> None:
    """どの状態でも5表すべてを出す（D07 §8.1）。"""
    assert set(report.rows) == set(EvaluationTable)
    rejected = _evaluate(
        traces.repository_for(manifest=traces.manifest_for(status="FAILED_CAPABILITY")),
        status=RunStatus.FAILED_CAPABILITY,
        with_summaries=False,
    )
    assert set(rejected.rows) == set(EvaluationTable)
    assert rejected.rows[EvaluationTable.METRICS] == ()
    assert rejected.rows[EvaluationTable.CONSISTENCY_CHECKS] != ()


def test_a_fill_without_an_order_is_reported_not_silently_dropped() -> None:
    """注文の無い約定は診断を作れないので、落としたことを検査に載せる（D07 §10.2 の C4）。

    黙って落とすと、診断の表の行数が少ない理由が成果物から読めなくなる。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.ORDERS] = [
        row for row in tables[TraceTable.ORDERS] if row["order_id"] != "ORD:00000003"
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    chain = [check for check in report.checks if check.check == CHECK_ID_CHAIN_COMPLETE]
    assert chain[0].passed is False
    assert "FIL:00000003" in chain[0].observed


def test_an_incomplete_closed_position_is_reported_not_silently_dropped() -> None:
    """完了取引の行を組み立てられない建玉も検査に載せる（D07 §10.2 の C4）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.POSITIONS] = [
        # 金額と通貨の両方が空なら読める値（決済前と同じ形）であり、決済済みなのに確定損益が
        # 無いという食い違いは C3・C4 が見る。
        {**tables[TraceTable.POSITIONS][0], "realized_amount": None, "realized_currency": None},
        tables[TraceTable.POSITIONS][1],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    chain = [check for check in report.checks if check.check == CHECK_ID_CHAIN_COMPLETE]
    assert chain[0].passed is False
    assert "POS:00000001" in chain[0].observed


def test_a_close_side_chain_break_is_fatal() -> None:
    """決済側の試行が判断履歴から落ちていても致命の不合格になる（D07 §10.2 の C4）。

    注文の有無だけを見ていると、決済注文の試行が表4 から落ちた判断履歴でも検査が通り、
    指標が採用してよい数値として出てしまう。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.ORDER_REQUESTS] = [
        row for row in tables[TraceTable.ORDER_REQUESTS] if row["attempt_id"] != "ATT:00000002"
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    chain = [check for check in report.checks if check.check == CHECK_ID_CHAIN_COMPLETE]
    assert chain[0].passed is False
    assert "ORD:00000002" in chain[0].observed


def test_a_foreign_ledger_currency_is_reported_not_raised() -> None:
    """台帳の通貨が口座通貨と違っても、例外にせず検査の不合格として残す（D07 §10.2）。

    通貨をまたぐ引き算に入ると金額の型が例外を投げ、通貨の食い違いを指す検査（C8）の
    結果も、失敗を説明する成果物も残らない。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.LEDGER_SNAPSHOTS] = [
        *tables[TraceTable.LEDGER_SNAPSHOTS][:-1],
        {**tables[TraceTable.LEDGER_SNAPSHOTS][-1], "balance_currency": "USD"},
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    failing = {check.check for check in report.checks if not check.passed}
    assert CHECK_REALIZED_MATCHES_BALANCE in failing
    assert CHECK_SINGLE_ACCOUNT_CURRENCY in failing
    # 13件すべての検査結果が残り、どの検査が落ちたかを成果物だけで説明できる。
    assert [check.check for check in report.checks] == list(CHECK_ORDER)


def test_a_row_without_a_run_identifier_is_fatal() -> None:
    """`run_id` が空の行は読めない値として致命の不合格になる（D07 §10.4 の C9）。

    全行が `run_id` を持つことは D06 §9.1 が確定しているので、空は読めない値である。
    読み飛ばすと、実行に紐付いていない行が観測値から消えて検査が通り、その行が集計と
    指標へそのまま入る。`run_id` を読む C2 は実施できないので `UNREADABLE` になる。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.LEDGER_SNAPSHOTS] = [
        {**tables[TraceTable.LEDGER_SNAPSHOTS][0], "run_id": None},
        *tables[TraceTable.LEDGER_SNAPSHOTS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    assert _failed(report) == {CHECK_ALL_VALUES_READABLE}
    assert "LEDGER_SNAPSHOTS.run_id" in _check(report, CHECK_ALL_VALUES_READABLE).observed
    consistency = _check(report, CHECK_RUN_ID_CONSISTENT)
    assert consistency.outcome is CheckOutcome.UNREADABLE
    assert "LEDGER_SNAPSHOTS.run_id" in consistency.observed


def test_a_foreign_run_identifier_is_a_mismatch_not_unreadable() -> None:
    """別の実行の識別子を持つ行は読める値であり、C2 の不合格になる（D07 §10.2）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.LEDGER_SNAPSHOTS] = [
        {**tables[TraceTable.LEDGER_SNAPSHOTS][0], "run_id": "f" * 64},
        *tables[TraceTable.LEDGER_SNAPSHOTS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert _failed(report) == {CHECK_RUN_ID_CONSISTENT}


def test_a_fill_without_an_order_identifier_is_reported_not_raised() -> None:
    """注文の識別子が空の約定は読めない値として残す（D07 §10.4 の C9）。

    例外にすると評価が中断し、失敗を説明する検査の表そのものが残らない。約定の注文を
    辿る C4 は実施できないので `UNREADABLE` になる。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.FILLS] = [
        {**tables[TraceTable.FILLS][0], "order_id": None},
        *tables[TraceTable.FILLS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    readable = _check(report, CHECK_ALL_VALUES_READABLE)
    assert readable.outcome is CheckOutcome.FAILED
    assert "FILLS.order_id[FIL:00000001] = <empty>" in readable.observed
    assert _check(report, CHECK_ID_CHAIN_COMPLETE).outcome is CheckOutcome.UNREADABLE
    assert [check.check for check in report.checks] == list(CHECK_ORDER)


def test_a_cost_amount_without_its_currency_is_unreadable() -> None:
    """費用の金額と通貨は2列で1つ。片方だけ空なら読めない値である（D07 §4.2・§10.4）。

    どちらも空なのは「その区分の費用記録が無い」ことで、読めない値ではない（決済約定の
    提示価格の幅がその例）。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.FILLS] = [
        {**tables[TraceTable.FILLS][0], "cost_commission_currency": None},
        *tables[TraceTable.FILLS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert _failed(report) == {CHECK_ALL_VALUES_READABLE}
    assert "FILLS.cost_commission_currency" in _check(report, CHECK_ALL_VALUES_READABLE).observed
    # 通貨の列を読む C8 は実施できない。
    assert _check(report, CHECK_SINGLE_ACCOUNT_CURRENCY).outcome is CheckOutcome.UNREADABLE


@pytest.mark.parametrize(
    ("table", "index", "column"),
    [
        (TraceTable.OPPORTUNITY_TRANSITIONS, 0, "reason_code"),
        (TraceTable.ORDERS, 1, "terms_cause"),
        (TraceTable.ORDERS, 1, "terms_position_id"),
        (TraceTable.ORDERS, 0, "terms_reference_quote_price"),
        (TraceTable.ATTEMPT_DECISIONS, 0, "order_id"),
        (TraceTable.ORDER_REQUESTS, 0, "payload_opportunity_id"),
        (TraceTable.ORDER_REQUESTS, 1, "payload_position_id"),
        (TraceTable.EVALUATIONS, 1, "outcome_diagnoses"),
    ],
)
def test_a_column_required_by_the_row_kind_is_unreadable_when_empty(
    table: TraceTable, index: int, column: str
) -> None:
    """行の区分で必ず埋まる列が空なら読めない値にする（D07 §10.4）。

    見逃すと、終端理由・決済契機などの集計の鍵が黙って捨てられ、入場約定が不利約定幅の
    対象から外れたまま評価が完了する（第4巡の代替レビューの指摘）。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    rows = list(tables[table])
    changed = {**rows[index], column: None}
    if column == "terms_reference_quote_price":
        # 時刻の列と対なので、2列とも空にしても区分の規則で読めない値になることを確かめる。
        changed["terms_reference_quote_observed_at"] = None
    rows[index] = changed
    tables[table] = rows
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    readable = _check(report, CHECK_ALL_VALUES_READABLE)
    assert readable.outcome is CheckOutcome.FAILED
    assert f"{table.value}.{column}" in readable.observed


def test_one_unreadable_cell_is_counted_once() -> None:
    """同じセルを2つの規則（2列で1つ・区分で必ず埋まる）が見つけても1件と数える（C9）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.ORDERS] = [
        {**tables[TraceTable.ORDERS][0], "terms_reference_quote_price": None},
        *tables[TraceTable.ORDERS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert '"count":1' in _check(report, CHECK_ALL_VALUES_READABLE).observed


def test_a_skipped_evaluation_with_an_empty_diagnosis_list_is_unreadable() -> None:
    """見送りの行の診断が `[]`（要素0件）でも読めない値にする（第5巡の代替レビューの指摘）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.EVALUATIONS] = [
        tables[TraceTable.EVALUATIONS][0],
        {**tables[TraceTable.EVALUATIONS][1], "outcome_diagnoses": "[]"},
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    readable = _check(report, CHECK_ALL_VALUES_READABLE)
    assert "EVALUATIONS.outcome_diagnoses" in readable.observed


def test_a_rejected_attempt_without_a_reason_is_unreadable() -> None:
    """拒否した試行は拒否理由を必ず持つ（空なら拒否の集計から黙って消える）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.ATTEMPT_DECISIONS] = [
        {
            **tables[TraceTable.ATTEMPT_DECISIONS][0],
            "kind": "REJECTED",
            "order_id": None,
            "reason_code": None,
        },
        *tables[TraceTable.ATTEMPT_DECISIONS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    readable = _check(report, CHECK_ALL_VALUES_READABLE)
    assert readable.outcome is CheckOutcome.FAILED
    assert "ATTEMPT_DECISIONS.reason_code" in readable.observed


def test_a_realized_amount_without_its_currency_is_unreadable() -> None:
    """確定損益の金額と通貨も2列で1つ（D07 §10.4）。

    通貨だけが空の決済済み建玉を通すと、通貨の検査（C8）は空を数えず、取引の組み立てで
    例外になって検査の表が残らない。読めない値として C9 で止める。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.POSITIONS] = [
        {**tables[TraceTable.POSITIONS][0], "realized_currency": None},
        *tables[TraceTable.POSITIONS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.trades == ()
    assert _failed(report) == {CHECK_ALL_VALUES_READABLE}
    assert "POSITIONS.realized_currency" in _check(report, CHECK_ALL_VALUES_READABLE).observed


def test_a_foreign_cost_currency_is_fatal() -> None:
    """v2.0 で足した費用の列の通貨も口座通貨と比べる（D07 §7.3 の C8）。"""
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.FILLS] = [
        {**tables[TraceTable.FILLS][0], "cost_commission_currency": "USD"},
        *tables[TraceTable.FILLS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert _failed(report) == {CHECK_SINGLE_ACCOUNT_CURRENCY}


def test_an_unreadable_value_is_counted_in_the_manifest() -> None:
    """読めなかった検査の件数を評価 manifest に残す（D07 §10.4 の `unreadable_check_count`）。

    水準ごとの件数は `PASSED` でない検査（不合格と読めなかったの両方）を数える。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.LEDGER_SNAPSHOTS] = [
        {**tables[TraceTable.LEDGER_SNAPSHOTS][0], "at_time": "yesterday"},
        *tables[TraceTable.LEDGER_SNAPSHOTS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    outcomes = _outcomes(report)
    unreadable = {name for name, outcome in outcomes.items() if outcome is CheckOutcome.UNREADABLE}
    # 台帳の処理点を読む C5・C7 と、主キーを読む C12 が実施できない。
    assert unreadable == {
        CHECK_REALIZED_MATCHES_BALANCE,
        "snapshot_order_monotonic",
        CHECK_INPUT_KEYS_UNIQUE,
    }
    assert report.manifest.unreadable_check_count == 3
    # 致命: C9（不合格）＋ C5・C12（読めなかった）。警告: C7（読めなかった）。
    assert report.manifest.fatal_failure_count == 3
    assert report.manifest.warning_failure_count == 1


def test_keys_outside_the_vocabulary_are_emitted_in_a_fixed_order() -> None:
    """語彙に無い鍵は宣言済みの鍵の後ろに符号順で並ぶ（D07 §6.1・§9.1 の条件1）。

    見つけた順に出すと、判断履歴の行の並びが変わるだけで結果のダイジェストが変わる。
    """
    manifest = traces.manifest_for()

    def tables_with(order: tuple[str, str]) -> dict[TraceTable, list[traces.Row]]:
        built = traces.t01_tables(str(manifest.run_id))
        base = built[TraceTable.INTRABAR_RESOLUTIONS][0]
        # 主キー（`fill_id`）は表ごとに一意なので、行ごとに別の約定を指す（D06 §9.2）。
        built[TraceTable.INTRABAR_RESOLUTIONS] = [
            {**base, "fill_id": fill_id, "method": method}
            for fill_id, method in zip(("FIL:00000001", "FIL:00000002"), order, strict=True)
        ]
        return built

    first = _evaluate(
        traces.repository_for(tables_with(("ZZZ_UNKNOWN", "AAA_UNKNOWN")), manifest=manifest)
    )
    second = _evaluate(
        traces.repository_for(tables_with(("AAA_UNKNOWN", "ZZZ_UNKNOWN")), manifest=manifest)
    )

    emitted = [row.key for row in first.categories if row.category is CategoryKind.INTRABAR_METHOD]
    assert emitted[-2:] == ["AAA_UNKNOWN", "ZZZ_UNKNOWN"]
    assert first.categories == second.categories
    assert first.manifest.result_digest == second.manifest.result_digest


def test_a_zero_peak_makes_the_drawdown_rate_undefined() -> None:
    """最大値が 0 なら率は定まらない（D07 §5.2 の #6・#8、§10.3）。

    0 を返すと「沈みが無かった」と「率が定まらない」を区別できない。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.LEDGER_SNAPSHOTS] = [
        {
            **row,
            "balance_amount": "0",
            "equity_amount": "0" if index == 0 else "-1",
        }
        for index, row in enumerate(tables[TraceTable.LEDGER_SNAPSHOTS])
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    # 末尾の確定損益と合わないので致命の不合格になり、検査の表だけが出る。
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()

    # 率の分岐そのものは、走査の結果を直接与えて確かめる。
    from odyssey_fx.evaluation.domain.metrics import max_drawdown

    fall = max_drawdown([decimal_from_str("0"), decimal_from_str("-1")])
    assert fall is not None
    assert fall.peak == decimal_from_str("0")


def test_an_empty_table_with_all_its_columns_is_accepted() -> None:
    """必須列が揃っていれば行0件の表は不整合ではない（D07 §4.3）。

    列が無いことと行が0件であることを区別できないと、取引が1件も無かった正常な run と
    壊れた表を同じに扱ってしまう。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.INTRABAR_RESOLUTIONS] = []
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.COMPLETED
    assert report.checks[0].passed is True
    counts = {(row.category, row.key): row.count for row in report.categories}
    # 0件でも鍵は行として出る（D07 §6.1）。
    assert counts[(CategoryKind.INTRABAR_METHOD, "SINGLE_HIT")] == 0


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        (TraceTable.LEDGER_SNAPSHOTS, "at_time", None),
        (TraceTable.LEDGER_SNAPSHOTS, "at_phase", "NOT_A_PHASE"),
        (TraceTable.LEDGER_SNAPSHOTS, "balance_amount", "not-a-number"),
        # 指数表記・カーネル精度を超える桁は、計算で桁あふれや黙った丸めになる（D06 §9.1）。
        (TraceTable.LEDGER_SNAPSHOTS, "balance_amount", "1E+9999999"),
        (TraceTable.LEDGER_SNAPSHOTS, "balance_amount", "1e3"),
        (TraceTable.LEDGER_SNAPSHOTS, "balance_amount", "1" * 29),
        (TraceTable.POSITIONS, "side", "SIDEWAYS"),
        (TraceTable.POSITIONS, "position_id", None),
        (TraceTable.FILLS, "processed_at_sequence", None),
    ],
)
def test_a_value_that_cannot_be_read_is_reported_not_raised(
    table: TraceTable, column: str, value: str | None
) -> None:
    """読めない値で評価を中断しない（D07 §10.4 の C9、根本対処 R5）。

    列は揃っていても、常に埋まるはずの値が空・語彙に無い列挙・十進数として読めない
    文字列は起こりうる。例外のまま外へ出すと、失敗を説明する検査の表も評価 manifest も
    残らず、「なぜ評価できなかったか」が成果物から消える。段階2 の仮置き（C1 へ寄せる）は
    C9 と `UNREADABLE` に置き換わり、C1 は表と列の有無だけを見る。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[table] = [{**tables[table][0], column: value}, *tables[table][1:]]

    report = _evaluate(traces.repository_for(tables, manifest=manifest))

    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    assert report.trades == ()
    assert report.fill_diagnostics == ()
    # 失敗の理由が成果物だけで説明できる（不合格は C9 の1件、残りは合格か読めなかった）。
    assert _failed(report) == {CHECK_ALL_VALUES_READABLE}
    readable = _check(report, CHECK_ALL_VALUES_READABLE)
    assert f"{table.value}.{column}" in readable.observed
    assert '"count":1' in readable.observed
    assert _check(report, CHECK_REQUIRED_COLUMNS_PRESENT).passed
    assert report.manifest.fatal_failure_count >= 1


@pytest.mark.parametrize(
    "encoded",
    ["[not json", '["{}"]', '["123"]', "{}", '["{\\"reason\\":\\"\\"}"]'],
)
def test_a_malformed_diagnosis_column_is_reported_not_raised(encoded: str) -> None:
    """評価見送りの診断が読めなくても、失敗として完了する（D07 §10.4 の C9）。

    集計だけが読む列であり、C9 以外の検査はどれも触らない。読めない値を検査の段階で
    見つけないと、検査がすべて合格したあとで集計が例外になり、失敗を説明する成果物が
    残らない。JSON としては読めても診断の形（`reason` を持つ辞書の列）でなければ読めない
    値とする。読み飛ばすと見送りの理由が0件として集計される。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.EVALUATIONS] = [
        tables[TraceTable.EVALUATIONS][0],
        {**tables[TraceTable.EVALUATIONS][1], "outcome_diagnoses": encoded},
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    assert report.categories == ()
    failing = [check for check in report.checks if not check.passed]
    assert [check.check for check in failing] == [CHECK_ALL_VALUES_READABLE]


@pytest.mark.parametrize(
    ("table", "column"),
    [
        (TraceTable.FILLS, "fill_id"),
        (TraceTable.ORDERS, "order_id"),
        (TraceTable.ORDER_REQUESTS, "attempt_id"),
    ],
)
def test_a_duplicate_primary_key_is_reported_not_silently_resolved(
    table: TraceTable, column: str
) -> None:
    """主キーが重複した判断履歴は失敗として残す（D06 §9.2、D07 §9.1 の条件1）。

    後に現れた行で上書きすると、同じ判断履歴でも行の並びによって辿り着く行が変わり、
    取引・集計・指標・結果のダイジェストが変わる。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    first = tables[table][0]
    tables[table] = [first, {**tables[table][1], column: first[column]}, *tables[table][2:]]

    report = _evaluate(traces.repository_for(tables, manifest=manifest))

    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    # 主キーの重複は C12 の不合格として残す（D07 §10.4、R1-D07-5）。表と列は揃っている。
    assert CHECK_INPUT_KEYS_UNIQUE in _failed(report)
    assert '"first":' in _check(report, CHECK_INPUT_KEYS_UNIQUE).observed
    assert _check(report, CHECK_REQUIRED_COLUMNS_PRESENT).passed
    assert _check(report, CHECK_ALL_VALUES_READABLE).passed


@pytest.mark.parametrize(
    "table",
    [
        TraceTable.EVALUATIONS,
        TraceTable.OPPORTUNITY_TRANSITIONS,
        TraceTable.ORDER_REQUESTS,
        TraceTable.ATTEMPT_DECISIONS,
        TraceTable.ORDERS,
        TraceTable.FILLS,
        TraceTable.POSITIONS,
        TraceTable.INTRABAR_RESOLUTIONS,
        TraceTable.LEDGER_SNAPSHOTS,
    ],
)
def test_every_table_rejects_a_duplicated_primary_key(table: TraceTable) -> None:
    """9表すべてで主キーの重複を不整合として扱う（D06 §9.2、D07 §9.1 の条件1）。

    台帳 snapshot がとくに危うい。同じ処理点に違う残高の行が2つあると、Parquet の格納順で
    最終残高も最大ドローダウンも変わる。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    rows = tables[table]
    assert rows, table
    tables[table] = [*rows, dict(rows[0])]

    report = _evaluate(traces.repository_for(tables, manifest=manifest))

    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    # 主キーの重複は C12 の不合格として残す（D07 §10.4、R1-D07-5）。表と列は揃っている。
    assert CHECK_INPUT_KEYS_UNIQUE in _failed(report)
    assert '"first":' in _check(report, CHECK_INPUT_KEYS_UNIQUE).observed
    assert _check(report, CHECK_REQUIRED_COLUMNS_PRESENT).passed
    assert _check(report, CHECK_ALL_VALUES_READABLE).passed


def test_two_ledger_snapshots_at_the_same_point_are_refused() -> None:
    """同じ処理点に違う残高の台帳 snapshot があれば失敗させる（D06 §9.2）。

    並びによって最終残高が変わるので、どちらを採るかを決める規則を作る代わりに、
    不整合として止める。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    last = tables[TraceTable.LEDGER_SNAPSHOTS][-1]
    tables[TraceTable.LEDGER_SNAPSHOTS] = [
        *tables[TraceTable.LEDGER_SNAPSHOTS],
        {**last, "balance_amount": "999999", "equity_amount": "999999"},
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()


def test_the_same_moment_written_two_ways_counts_as_one_key() -> None:
    """主キーの構成要素は宣言した型に直してから比べる（D06 §9.2、D07 §9.1 の条件1）。

    文字列のまま比べると `2015-01-06T09:00:00Z` と `2015-01-06T09:00:00+00:00` が別の鍵に
    見えるが、読み出したあとは同じ処理点になる。判定だけ文字列で行うと、**判定は通るのに
    使う側では同じ値**になる行が残り、並びで最大ドローダウンが変わる。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    first = tables[TraceTable.LEDGER_SNAPSHOTS][0]
    tables[TraceTable.LEDGER_SNAPSHOTS] = [
        first,
        {
            **first,
            "at_time": "2015-01-06T09:00:00+00:00",
            "balance_amount": "999999",
            "equity_amount": "999999",
        },
        *tables[TraceTable.LEDGER_SNAPSHOTS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    # 主キーの重複は C12 の不合格として残す（D07 §10.4、R1-D07-5）。表と列は揃っている。
    assert CHECK_INPUT_KEYS_UNIQUE in _failed(report)
    assert '"first":' in _check(report, CHECK_INPUT_KEYS_UNIQUE).observed
    assert _check(report, CHECK_REQUIRED_COLUMNS_PRESENT).passed
    assert _check(report, CHECK_ALL_VALUES_READABLE).passed


def test_the_same_identifier_written_two_ways_counts_as_one_key() -> None:
    """連番 ID の書き方の揺れも同じ鍵として扱う（D02 §7.1、D06 §9.2）。

    `FIL:00000001` と `FIL:000000001` は同じ約定を指すが、文字列のまま比べると別の鍵に
    見える。判定だけ通ると、同じ約定の診断が2行になり、並びで結果のダイジェストが変わる。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    first = tables[TraceTable.FILLS][0]
    tables[TraceTable.FILLS] = [
        first,
        {**first, "fill_id": "FIL:000000001"},
        *tables[TraceTable.FILLS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    # 主キーの重複は C12 の不合格として残す（D07 §10.4、R1-D07-5）。表と列は揃っている。
    assert CHECK_INPUT_KEYS_UNIQUE in _failed(report)
    assert '"first":' in _check(report, CHECK_INPUT_KEYS_UNIQUE).observed
    assert _check(report, CHECK_REQUIRED_COLUMNS_PRESENT).passed
    assert _check(report, CHECK_ALL_VALUES_READABLE).passed


def test_a_row_without_a_primary_key_is_refused() -> None:
    """主キーの構成要素が空の行は読めない値として止める（D06 §9.2、D07 §10.4）。

    欠けたまま数えると、主キーを持たない行が集計と指標へ入る。必須列の検査は列の有無しか
    見ないので、ここで止めないと評価が完了してしまう。主キーを読む C12 は実施できない。
    """
    manifest = traces.manifest_for()
    tables = traces.t01_tables(str(manifest.run_id))
    tables[TraceTable.EVALUATIONS] = [
        {**tables[TraceTable.EVALUATIONS][0], "evaluation_id": None},
        *tables[TraceTable.EVALUATIONS][1:],
    ]
    report = _evaluate(traces.repository_for(tables, manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    assert report.categories == ()
    assert _failed(report) == {CHECK_ALL_VALUES_READABLE}
    assert "EVALUATIONS.evaluation_id" in _check(report, CHECK_ALL_VALUES_READABLE).observed
    assert _check(report, CHECK_INPUT_KEYS_UNIQUE).outcome is CheckOutcome.UNREADABLE


# --- 段階4 の入力: run manifest の読み取りと取引カレンダー（D07 §4.1・§10.4）--


def test_an_unreadable_run_manifest_is_a_fatal_check_not_an_exception() -> None:
    """run manifest が読めなくても例外にせず、C11 の不合格として残す（R1-D07-4）。

    manifest の値を使う検査（C2・C5・C8・C10。処理点の順位を使う C7 も）は
    `UNREADABLE`、manifest を使わない検査は実施する。評価 manifest の run 由来の5項目は
    `None` になる。
    """
    repository = traces.repository_for(manifest_failure='"manifest.json is not JSON"')
    report = _evaluate(repository)
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    outcomes = _outcomes(report)
    assert outcomes["run_manifest_readable"] is CheckOutcome.FAILED
    assert _check(report, "run_manifest_readable").observed == '"manifest.json is not JSON"'
    for name in (
        CHECK_RUN_ID_CONSISTENT,
        CHECK_REALIZED_MATCHES_BALANCE,
        "snapshot_order_monotonic",
        CHECK_SINGLE_ACCOUNT_CURRENCY,
        "calendar_matches_run",
    ):
        assert outcomes[name] is CheckOutcome.UNREADABLE, name
        assert "run manifest" in _check(report, name).observed
    for name in (
        CHECK_REQUIRED_COLUMNS_PRESENT,
        CHECK_TRADE_COUNT_MATCHES,
        CHECK_ID_CHAIN_COMPLETE,
        CHECK_OPPORTUNITY_COUNT_MATCHES,
        CHECK_ALL_VALUES_READABLE,
        CHECK_INPUT_KEYS_UNIQUE,
    ):
        assert outcomes[name] is CheckOutcome.PASSED, name
    manifest = report.manifest
    assert manifest.run_manifest_ref is None
    assert manifest.run_code_digest is None
    assert manifest.run_status is None
    assert manifest.run_failure_reason is None
    assert manifest.account_currency is None


def test_an_unreadable_manifest_of_a_failed_run_is_still_rejected() -> None:
    """run が正常完走していなければ、致命の不合格があっても `REJECTED` を優先する（R1-D07-1）。"""
    repository = traces.repository_for(
        manifest=traces.manifest_for(status="FAILED_DATA_ERROR"),
        manifest_failure='"broken"',
    )
    report = _evaluate(repository, status=RunStatus.FAILED_DATA_ERROR, with_summaries=False)
    assert report.status is EvaluationStatus.REJECTED
    assert report.manifest.fatal_failure_count >= 1


def test_a_result_and_manifest_that_disagree_on_the_run_status_fail_not_raise() -> None:
    """結果 DTO は完走、run manifest は失敗と記録していても例外にしない（D07 v2.2 の C13）。

    2つの保存済み成果物の食い違いは入力の欠陥なので、不合格として残す（2026-09-25 の人間の
    決定）。評価 manifest には食い違う片方の失敗理由を写さない（完走と失敗理由の組は作らない）。
    """
    manifest = traces.manifest_for(status="FAILED_DATA_ERROR")
    report = _evaluate(traces.repository_for(manifest=manifest))
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    check = _check(report, "run_status_consistent")
    assert check.outcome is CheckOutcome.FAILED
    assert check.expected == '{"failure_reason":false,"status":"COMPLETED"}'
    assert check.observed == '{"failure_reason":true,"status":"FAILED_DATA_ERROR"}'
    assert report.manifest.run_status is RunStatus.COMPLETED
    assert report.manifest.run_failure_reason is None


def test_a_rejected_run_whose_manifest_agrees_passes_the_status_check() -> None:
    """正常完走していない run でも、2つの成果物が一致していれば C13 は合格する。"""
    manifest = traces.manifest_for(status="FAILED_DATA_ERROR")
    report = _evaluate(
        traces.repository_for(manifest=manifest),
        status=RunStatus.FAILED_DATA_ERROR,
        with_summaries=False,
    )
    assert report.status is EvaluationStatus.REJECTED
    assert _check(report, "run_status_consistent").outcome is CheckOutcome.PASSED
    assert report.manifest.run_failure_reason is not None


def test_a_calendar_that_the_run_did_not_use_fails_the_evaluation() -> None:
    """run manifest の `calendar_ref` と違うカレンダーでは数えない（D07 §10.4 の C10、Q8）。"""
    from tests.fixtures.synthetic import market

    repository = traces.repository_for()
    result = traces.result_for(repository.manifest)
    other = market.calendar(version=2)
    report = EvaluateRun(evaluation_code_digest=_CODE_DIGEST).evaluate(
        result, repository, METRIC_SET_VERSION, other
    )
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    assert _failed(report) == {"calendar_matches_run"}
    check = _check(report, "calendar_matches_run")
    assert check.expected == '"fx_ny17@v1"'
    assert check.observed == '"fx_ny17@v2"'
    assert report.manifest.calendar_ref == ("fx_ny17", 2)


def test_the_evaluation_identifier_changes_with_the_calendar() -> None:
    """受け取ったカレンダーの識別と版は評価の識別子の算出元に入る（D07 §9.2 の v2.0）。

    入れないと、同じ run を違うカレンダーで評価した2つの結果（一方は C10 不合格で
    `FAILED`）が同じ識別子・同じ保存先になる。
    """
    from tests.fixtures.synthetic import market

    repository = traces.repository_for()
    result = traces.result_for(repository.manifest)
    use_case = EvaluateRun(evaluation_code_digest=_CODE_DIGEST)
    first = use_case.evaluate(result, repository, METRIC_SET_VERSION, traces.CALENDAR)
    second = use_case.evaluate(result, repository, METRIC_SET_VERSION, market.calendar(version=2))
    assert first.manifest.run_evaluation_id != second.manifest.run_evaluation_id
    assert first.manifest.calendar_ref == ("fx_ny17", 1)
