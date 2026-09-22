"""判断履歴から指標・集計・診断・状態を作る（D07 §4〜§10）。

**指標15件すべてを紙上トレース T01 の検算値と突き合わせる**（D07 §11、§5.2 の「T01 検算」
の列）。判断履歴は `tests/fixtures/evaluation/traces.py` が T01 の数値から手で組み立てる。
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
    CHECK_ID_CHAIN_COMPLETE,
    CHECK_OPPORTUNITY_COUNT_MATCHES,
    CHECK_ORDER,
    CHECK_REALIZED_MATCHES_BALANCE,
    CHECK_REQUIRED_COLUMNS_PRESENT,
    CHECK_SINGLE_ACCOUNT_CURRENCY,
    CHECK_TRADE_COUNT_MATCHES,
    CheckLevel,
    EvaluationStatus,
)
from tests.fixtures.evaluation import traces

_CODE_DIGEST = CodeDigest(digest=ContentDigest.sha256("b" * 64))


def _evaluate(repository: traces.FakeRepository, **result_options: object) -> EvaluationReport:
    result = traces.result_for(repository.manifest, **result_options)  # type: ignore[arg-type]
    use_case = EvaluateRun(evaluation_code_digest=_CODE_DIGEST)
    return use_case.evaluate(result, repository, METRIC_SET_VERSION)


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


@pytest.fixture
def report() -> EvaluationReport:
    """T01 第9節の判断履歴を評価した結果。"""
    return _evaluate(traces.repository_for())


# --- 指標15件の検算（D07 §5.2 の「T01 検算」）--------------------------------


def test_the_metrics_are_the_fifteen_in_declaration_order(report: EvaluationReport) -> None:
    """15件すべてが1行ずつ、宣言順に並ぶ（D07 §8.1）。"""
    assert [record.metric_id for record in report.metrics] == list(MetricId)


def test_net_profit_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#1: `1,036,706 − 1,000,000 = 36,706 JPY`（T01 §9.3）。"""
    assert _amount(report, MetricId.NET_PROFIT) == decimal_from_str("36706")


def test_closed_trade_profit_matches_the_paper_trace(report: EvaluationReport) -> None:
    """#2: 完了取引の確定損益は `36,768 JPY`（残存建玉は数えない、T01 §2.6）。"""
    assert _amount(report, MetricId.CLOSED_TRADE_PROFIT) == decimal_from_str("36768")


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
    """#9: 完了取引だけなら `8,100 ÷ 1,036,800 = 0.0078125`（D07 §5.2 の検算値）。

    D07 §5.2 の検算値は、T01 が残存建玉の入場時刻を定めていないため**完了取引の保有時間
    だけ**を数えた値である。実装は同じ節の式の本文どおり「未決済建玉は run 末尾までを
    数える」ので、残存建玉があると値が変わる（次のテスト）。どちらを正本にするかは人間の
    決定を要する。
    """
    manifest = traces.manifest_for()
    repository = traces.repository_for(
        traces.closed_trade_only(str(manifest.run_id)), manifest=manifest
    )
    report = _evaluate(repository, trade_count=1, opportunity_count=2)
    assert _ratio(report, MetricId.EXPOSURE_RATE) == decimal_from_str("0.0078125")


def test_the_exposure_rate_counts_the_open_position_to_the_run_end(
    report: EvaluationReport,
) -> None:
    """#9: 残存建玉は run 末尾までを数える（D07 §5.2 の式の本文）。

    完了取引 8,100 秒 ＋ 残存建玉 734,400 秒（木曜 10:00Z → 金曜 22:00Z）を 12 日で割る。
    """
    expected = decimal_from_str("742500") / decimal_from_str("1036800")
    assert _ratio(report, MetricId.EXPOSURE_RATE) == expected


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
    """検査8件が宣言順に全件残り、状態は完了になる（D07 §10.1・§10.2）。"""
    assert report.status is EvaluationStatus.COMPLETED
    assert [check.check for check in report.checks] == list(CHECK_ORDER)
    assert all(check.passed for check in report.checks)
    assert report.manifest.fatal_failure_count == 0
    assert report.manifest.warning_failure_count == 0


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
    for metric_id in (MetricId.CLOSED_TRADE_PROFIT, MetricId.WIN_RATE):
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
    """表が無いことは致命検査の不合格として残す（D07 §4.3・§10.2 の C1）。"""
    repository = traces.repository_for(absent=frozenset({TraceTable.POSITIONS}))
    report = _evaluate(repository)
    assert report.status is EvaluationStatus.FAILED
    assert report.metrics == ()
    failing = {check.check for check in report.checks if not check.passed}
    assert failing == {CHECK_REQUIRED_COLUMNS_PRESENT}
    assert "POSITIONS" in report.checks[0].observed


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
    first = use_case.evaluate(result, repository, 1)
    second = use_case.evaluate(result, repository, 2)
    assert first.manifest.run_evaluation_id != second.manifest.run_evaluation_id


def test_the_evaluation_identifier_changes_with_the_evaluating_code() -> None:
    """評価コードを変えると評価の識別子が変わる（D07 §9.2、Q5 決定）。"""
    repository = traces.repository_for()
    result = traces.result_for(repository.manifest)
    first = EvaluateRun(evaluation_code_digest=_CODE_DIGEST).evaluate(
        result, repository, METRIC_SET_VERSION
    )
    other = CodeDigest(digest=ContentDigest.sha256("c" * 64))
    second = EvaluateRun(evaluation_code_digest=other).evaluate(
        result, repository, METRIC_SET_VERSION
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
