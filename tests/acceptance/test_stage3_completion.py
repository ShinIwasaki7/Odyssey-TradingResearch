"""段階3 の完了条件を人工データで確かめる（全体計画 §8.2、紙上トレース T02 §16・§17）。

全体計画 §8.2 の段階3 の完了条件は次の3つである。

1. **検証戦略 B を同じ基盤で記述できる**: 検証戦略 B の宣言（D05 §9.2）がコンパイルを通り、
   段階2 と同じエンジン・同じ評価で run が完走する。
2. **時刻境界と取消理由を trace できる**: 出力の観測区間と鮮度（D05 §6.7）、取引機会の終端
   理由（表3）、損切り水準の更新の適用（表12）、確認試行（表18）が判断履歴に残る。
3. **遅延シナリオ別の差分を追跡できる**: `tests/semantics/backtest/test_delay_scenarios.py`
   （4ケースの trace を1つの意味論テストで突き合わせる。D08 §9.6）。

手計算の正本は紙上トレース [T02](../../docs/traces/T02_paper_trace_strategy_b.md) §16 の
検算値である。本ファイルは遅延なし（ケース1）の run でその値を固定する。

**コマンド経由ではなく、エンジンの利用口を直接呼ぶ**。理由は `tests/fixtures/acceptance/t02_run.py`
の冒頭に書いた（実験設定の書式はまだ検証戦略 B と遅延シナリオを書けない）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from odyssey_fx.backtest.trace.recorder import CompositeRow, TraceTable, flatten_row
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.application.evaluate_run import EvaluationReport
from odyssey_fx.evaluation.domain.metrics import MetricId
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.strategy.records.payloads import UpdateStop
from odyssey_fx.strategy.records.records import Observation, OutputRecord
from odyssey_fx.strategy.runtime.requests import ManagementRequest
from tests.fixtures.acceptance.t02_run import build_case, evaluate_case, run_case
from tests.fixtures.backtest.harness import RunOutput, flatten_all
from tests.fixtures.strategy.strategy_b import DAILY_SERIES, HOURLY_SERIES, M15_SERIES

_CASE = "none"


def _t(text: str) -> UtcTime:
    return UtcTime.parse(text)


def _price(text: str) -> Price:
    return Price(decimal_from_str(text))


def _decimal(value: object) -> Decimal:
    assert isinstance(value, str), value
    return decimal_from_str(value)


@pytest.fixture(scope="module")
def run() -> RunOutput:
    """遅延なし（T02 §1.4 のケース1）の run。"""
    return run_case(_CASE)


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> EvaluationReport:
    """ケース1 の run を保存して評価した結果（D07）。"""
    return evaluate_case(_CASE, tmp_path_factory.mktemp("stage3-evaluation"))


def _output(run: RunOutput, instance_id: str, decision_time: str) -> OutputRecord[object]:
    found = [
        record
        for record in run.rows(TraceTable.OUTPUTS)
        if isinstance(record, OutputRecord)
        and record.producer.instance_id == instance_id
        and record.decision_time == _t(decision_time)
    ]
    assert len(found) == 1, found
    return found[0]


def _observation(run: RunOutput, instance_id: str, decision_time: str) -> Observation[object]:
    payload = _output(run, instance_id, decision_time).payload
    assert isinstance(payload, Observation), payload
    return payload


def _metric(report: EvaluationReport, metric_id: MetricId) -> dict[str, object]:
    (record,) = [item for item in report.metrics if item.metric_id is metric_id]
    return flatten_row(record)


# --- 完了条件1: 検証戦略 B が同じ基盤で完走する ------------------------------------


def test_strategy_b_runs_to_completion_on_the_same_engine(run: RunOutput) -> None:
    """検証戦略 B の run が正常に完走し、段階2 と同じ評価が完了する（全体計画 §8.2）。"""
    assert run.result.status is RunStatus.COMPLETED
    assert run.result.trade_count == 1


def test_the_evaluation_completes_on_a_stage3_trace(report: EvaluationReport) -> None:
    """段階3 の判断履歴（19表）を段階2 の評価（D07）がそのまま読める（D07 §4.2 v1.4）。"""
    assert report.status is EvaluationStatus.COMPLETED


# --- T02 §16 の検算値 ---------------------------------------------------------------


def test_the_daily_ema_matches_the_check_values(run: RunOutput) -> None:
    """T02 §2.4・§16: `daily_ema` は `01-06 22:00Z` に 149.020、`01-07 22:00Z` に 149.000。"""
    jan6 = _observation(run, "daily_ema", "2015-01-06T22:00:00Z")
    jan7 = _observation(run, "daily_ema", "2015-01-07T22:00:00Z")
    assert jan6.value == _price("149.020")
    assert jan7.value == _price("149.000")
    # 日足の鮮度は足の終了時刻（D05 §6.7）。遅延なしでは判断時刻と一致する。
    assert jan6.freshness_time == _t("2015-01-06T22:00:00Z")
    assert jan7.subject == BarKey(series=DAILY_SERIES, bar_start=_t("2015-01-06T22:00:00Z"))


def test_the_m15_ema_matches_the_check_value(run: RunOutput) -> None:
    """T02 §2.4・§16: `m15_ema` は `01-07 09:00Z` に 149.255。"""
    m15 = _observation(run, "m15_ema", "2015-01-07T09:00:00Z")
    assert m15.value == _price("149.255")
    assert m15.subject == BarKey(series=M15_SERIES, bar_start=_t("2015-01-07T08:45:00Z"))


def test_the_stop_level_and_its_freshness_match_the_check_values(run: RunOutput) -> None:
    """T02 §10・§16: `stop_level` は 148.950（鮮度 08:00Z）と 149.150（鮮度 11:00Z）。

    鮮度が判断時刻より1時間古いのは、当該足を除いて読む（`exclude_latest_bars=1`）ので窓の
    末尾が1本手前の足になるためである。観測した足（`subject`）と観測区間は評価を起こした足の
    ものであり、鮮度とは別の足を指す（時刻境界の trace。完了条件2）。
    """
    at_0900 = _observation(run, "stop_level", "2015-01-07T09:00:00Z")
    at_1200 = _observation(run, "stop_level", "2015-01-07T12:00:00Z")
    assert (at_0900.value, at_0900.freshness_time) == (
        _price("148.950"),
        _t("2015-01-07T08:00:00Z"),
    )
    assert (at_1200.value, at_1200.freshness_time) == (
        _price("149.150"),
        _t("2015-01-07T11:00:00Z"),
    )
    assert at_1200.subject == BarKey(series=HOURLY_SERIES, bar_start=_t("2015-01-07T11:00:00Z"))
    assert at_1200.observation_interval == Interval(
        start=_t("2015-01-07T11:00:00Z"), end=_t("2015-01-07T12:00:00Z")
    )


def test_position_p1_matches_the_check_values(run: RunOutput) -> None:
    """T02 §3.5・§16: 建玉 P1 は約定 149.390 / 数量 41,000 / 初期損切り 148.950。

    受付時の予約 19,762 円と約定時の計測リスク 18,040 円も T02 §3.5 の値である。
    """
    (entry, _) = [flatten_row(row) for row in run.rows(TraceTable.FILLS)]
    assert _decimal(entry["price"]) == decimal_from_str("149.390")
    assert _decimal(entry["quantity"]) == decimal_from_str("41000")
    (request, _) = [flatten_row(row) for row in run.rows(TraceTable.ORDER_REQUESTS)]
    assert _decimal(request["payload_protection_stop_loss"]) == decimal_from_str("148.950")
    (position,) = [flatten_row(row) for row in run.rows(TraceTable.POSITIONS)]
    assert _decimal(position["entry_price"]) == decimal_from_str("149.390")
    assert _decimal(position["risk_measurement_measured_amount"]) == decimal_from_str("18040")
    assert _decimal(position["risk_measurement_allocated_amount"]) == decimal_from_str("19762")


def test_the_trailing_stop_update_is_applied_just_before_admission(run: RunOutput) -> None:
    """T02 §10・§16、D06 §4.2 の手順6・§8.3（Q25 決定）: 更新後の損切りは 149.150。

    `01-07 12:00Z` にトレーリング部品が返した `UpdateStop(149.150)` を、受付（rank 10）の直前に
    建玉へ適用する。処理点のフェーズは `ADMISSION`、有効になるのは次の執行足
    `[12:00, 12:15)` からで、表12 に1行残る（段階3 で初めて `UPDATE_STOP` の行が入る）。
    """
    (row,) = run.rows(TraceTable.MANAGEMENT_APPLICATIONS)
    assert isinstance(row, CompositeRow)
    request = row.primary
    assert isinstance(request, ManagementRequest)
    assert request.action == UpdateStop(stop_loss=_price("149.150"))
    assert request.decision_time == _t("2015-01-07T12:00:00Z")
    trailing = _output(run, "trailing", "2015-01-07T12:00:00Z")
    assert request.source_output_id == trailing.output_id

    columns = flatten_row(row)
    assert columns["application_applied"] is True
    assert columns["application_at_time"] == "2015-01-07T12:00:00Z"
    assert columns["application_at_phase"] == "ADMISSION"
    assert columns["application_protection_version"] == 2
    assert _decimal(columns["application_rounded_stop_loss"]) == decimal_from_str("149.150")

    (position,) = [flatten_row(item) for item in run.rows(TraceTable.POSITIONS)]
    assert position["protection_version"] == 2
    assert _decimal(position["protection_stop_loss"]) == decimal_from_str("149.150")
    assert position["protection_effective_from_bar_start"] == "2015-01-07T12:00:00Z"


def test_the_exit_matches_the_check_values(run: RunOutput) -> None:
    """T02 §10・§16: `01-07 13:15Z` に更新後の損切り 149.150 で決済する。

    約定 149.140、確定損益 −10,291。
    """
    (_, exit_fill) = [flatten_row(row) for row in run.rows(TraceTable.FILLS)]
    assert _decimal(exit_fill["price"]) == decimal_from_str("149.140")
    assert exit_fill["processed_at_time"] == "2015-01-07T13:15:00Z"
    (resolution,) = [flatten_row(row) for row in run.rows(TraceTable.INTRABAR_RESOLUTIONS)]
    assert (resolution["method"], resolution["verdict"]) == ("SINGLE_HIT", "STOP_LOSS")
    (position,) = [flatten_row(row) for row in run.rows(TraceTable.POSITIONS)]
    assert position["status"] == "CLOSED"
    assert _decimal(position["realized_amount"]) == decimal_from_str("-10291")


def test_the_final_balance_and_metrics_match_the_check_values(
    run: RunOutput, report: EvaluationReport
) -> None:
    """T02 §10・§16: 末尾の残高と指標。

    `balance = 989,668`、`NET_PROFIT = −10,332`、`TRADE_COUNT = 1`、`WIN_RATE = 0`。
    """
    final = flatten_row(run.rows(TraceTable.LEDGER_SNAPSHOTS)[-1])
    assert _decimal(final["balance_amount"]) == decimal_from_str("989668")

    net = _metric(report, MetricId.NET_PROFIT)
    assert _decimal(net["value_amount_amount"]) == decimal_from_str("-10332")
    assert _metric(report, MetricId.TRADE_COUNT)["value_count"] == 1
    assert _decimal(_metric(report, MetricId.WIN_RATE)["value_ratio"]) == decimal_from_str("0")
    # 指標集合 v2 では完了取引の損益に入場手数料 41 円を含める（D07 §7.3）。建玉は決済済みの
    # 1件だけなので、`−10,291 − 41 = −10,332` となり純損益と一致する。
    closed = _metric(report, MetricId.CLOSED_TRADE_PROFIT)
    assert _decimal(closed["value_amount_amount"]) == decimal_from_str("-10332")


# --- 完了条件2: 時刻境界と取消理由を trace できる -------------------------------------


def test_the_confirmation_attempt_and_rechecks_are_traced(run: RunOutput) -> None:
    """T02 §3.3・§3.6: 表18 に確認試行1件（開始足で成立）、表19 に再検査2件（P4・P5 とも成立）。"""
    (attempt,) = [flatten_row(row) for row in run.rows(TraceTable.CONFIRMATION_ATTEMPTS)]
    assert attempt["bar_key_bar_start"] == "2015-01-07T08:45:00Z"
    assert attempt["outcome"] == "CONFIRMED"
    rechecks = [flatten_row(row) for row in run.rows(TraceTable.VALIDITY_RECHECKS)]
    assert [(row["at_phase"], row["outcome"]) for row in rechecks] == [
        ("P4_CONFIRMATION", "SATISFIED"),
        ("P5_ORDER_INTENT", "SATISFIED"),
    ]
    # 待機も遡りも起きない（T02 §3.6）。
    assert run.rows(TraceTable.WAIT_EVENTS) == ()
    assert run.rows(TraceTable.INPUT_SUBSTITUTIONS) == ()


def test_the_opportunity_terminal_reasons_are_traced(run: RunOutput) -> None:
    """取引機会の終端理由が表3 から読める（取消理由の trace。完了条件2）。

    - `01-06 15:00Z`: 1時間足の終値 149.250 が前日の日足高値 149.050 を超えて発火するが、
      `01-05 22:00Z` の市場状態（日足終値 149.000 が日足 EMA 149.000 を超えない＝買い不可）が
      許さないので遷移12（`MARKET_STATE_INVALIDATED`）で終端する。**T02 v1.1 はこの発火を
      見落としていた**（T02 v1.2 で訂正。§1.6・§13）。
    - `01-07 09:00Z`: T02 経路1。遷移1 → 10 → 4 → 5（受付で `FULFILLED_BY_ORDER_ACCEPTANCE`）。
    - `01-08 09:00Z`: T02 経路9。市場状態が買いを許さず遷移12。
    """
    transitions = [flatten_row(row) for row in run.rows(TraceTable.OPPORTUNITY_TRANSITIONS)]
    assert [
        (row["at_time"], row["from_state"], row["to_state"], row["reason_code"])
        for row in transitions
    ] == [
        ("2015-01-06T15:00:00Z", None, "TERMINATED", "MARKET_STATE_INVALIDATED"),
        ("2015-01-07T09:00:00Z", None, "OPEN", None),
        ("2015-01-07T09:00:00Z", "OPEN", "CONFIRMED", None),
        ("2015-01-07T09:00:00Z", "CONFIRMED", "ORDER_PENDING", None),
        ("2015-01-07T09:00:00Z", "ORDER_PENDING", "TERMINATED", "FULFILLED_BY_ORDER_ACCEPTANCE"),
        ("2015-01-08T09:00:00Z", None, "TERMINATED", "MARKET_STATE_INVALIDATED"),
    ]


def test_the_evaluation_counts_show_all_five_outcome_keys(report: EvaluationReport) -> None:
    """D07 §6.1 v1.4: 評価の結果区分の集計は5語で、0件の鍵も行として出る。"""
    keys = [
        (item.key, item.count)
        for item in report.categories
        if item.category.value == "EVALUATION_OUTCOME"
    ]
    assert [key for key, _ in keys] == ["EVALUATED", "SKIPPED", "FAILED", "WAITING", "SUPERSEDED"]
    assert dict(keys)["WAITING"] == 0
    assert dict(keys)["SUPERSEDED"] == 0


# --- 決定論: 同じ入力の再実行で trace が一致する ----------------------------------------


def test_the_rerun_produces_the_same_trace(run: RunOutput) -> None:
    """同じ入力をもう1度通すと、19表すべてが同じ行になる（D08 §11、ADR-0006）。"""
    again = build_case(_CASE)
    assert again.result.run_id == run.result.run_id
    assert flatten_all(again) == flatten_all(run)


def test_the_run_id_depends_on_the_delay_scenario() -> None:
    """遅延シナリオの参照は完全入力に入る（ADR-0006）。ケースが違えば実行の識別子も違う。"""
    assert run_case("none").result.run_id != run_case("d1_2s").result.run_id
