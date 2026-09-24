"""遅延シナリオ4ケースの trace を1つの意味論テストで突き合わせる（D08 §9.6、T02 §7〜§9・§16）。

同じ**素の足**（T02 再現生成器）に4つの遅延シナリオ（T02 §1.4）を当て、検証戦略 B の run を
エンジンの全フェーズまで4回通す。golden にはしない（D08 §9.6 の3。4ケース分の固定出力は差分が
読みにくく、遅延が変わるたびに4本とも書き換わる）。

| ケース | 遅延 | T02 | 確かめる検算値（T02 §16） |
|---|---|---|---|
| 1 `none` | なし | §3〜§5・§10・§11 | （受入れテスト） |
| 2 `d1_2s` | 日足だけ2秒 | §7 | 再開した `daily_ema` の出力が `01-07T22:00:02Z` |
| 3 `d1_25h` | 日足全体が25時間 | §8 | 期限到達が `01-08T22:00Z` のライフサイクル検査 |
| 4 `d1_bar_hold` | D(Jan7) だけ25時間 | §9 | 追い越しが `01-07T23:00Z` のライフサイクル検査 |

突き合わせは3段で行う。

1. **入力**: 4ケースの足は OHLC と対象区間が1つも違わず、日足の `available_at` だけが違う
   （T02 §16 の不変条件2）。
2. **最初の遅れた公開まで**: 判断履歴は4ケースで1行も違わない（遅延は判断時点の見え方だけを
   変え、それより前の判断を変えない）。
3. **遅れた公開の後**: 差がどこに現れるか。ケース2 は取引の結果が1つも変わらず、差が
   **公開時刻（`available_at`）・配送順（判断時点の増加）・待機の出来事（表16）・評価記録の
   結果区分**に限られることを表ごとに確かめる。ケース3・4 は待機の期限切れと追い越しが
   判断を変える（ケース3 は注文 0 件、ケース4 は T02 経路9 の発火が起きない）ので、T02 が
   追った判断時点の記録と、評価の結果区分の集計（D07 §6.1 の5語）で差を読む。
"""

from __future__ import annotations

import collections
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

import pytest

from odyssey_fx.backtest.trace.recorder import TraceTable, flatten_row
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.strategy.records.records import Observation, OutputRecord
from odyssey_fx.strategy.runtime.requests import EvaluationRecord, Skipped, Superseded
from tests.fixtures.acceptance.t02_run import (
    CASES,
    RUN_INTERVAL,
    delayed_bars,
    evaluate_case,
    raw_bars,
    run_case,
)
from tests.fixtures.strategy.strategy_b import DAILY_SERIES

_DELAYED = ("d1_2s", "d1_25h", "d1_bar_hold")

#: 表ごとの「その行が起きた時刻」の列（最初の遅れた公開までの一致を見るために使う）。
_TIME_COLUMNS: Mapping[TraceTable, str] = {
    TraceTable.OUTPUTS: "decision_time",
    TraceTable.EVALUATIONS: "decision_time",
    TraceTable.OPPORTUNITY_TRANSITIONS: "at_time",
    TraceTable.ORDER_REQUESTS: "created_at_time",
    TraceTable.ORDER_EVENTS: "at_time",
    TraceTable.FILLS: "processed_at_time",
    TraceTable.MANAGEMENT_APPLICATIONS: "application_at_time",
    TraceTable.LEDGER_SNAPSHOTS: "at_time",
    TraceTable.EVIDENCE: "at_time",
    TraceTable.WAIT_EVENTS: "at_time",
    TraceTable.VALIDITY_RECHECKS: "at_time",
}

#: 取引の結果を表す表（ケース2 で1つも変わらないことを確かめる）。
_TRADE_TABLES = (
    TraceTable.OPPORTUNITY_TRANSITIONS,
    TraceTable.CONFIRMATION_ATTEMPTS,
    TraceTable.VALIDITY_RECHECKS,
    TraceTable.ORDER_REQUESTS,
    TraceTable.ATTEMPT_DECISIONS,
    TraceTable.RISK_ASSESSMENTS,
    TraceTable.ORDERS,
    TraceTable.FILLS,
    TraceTable.RESERVATIONS,
    TraceTable.POSITIONS,
    TraceTable.MANAGEMENT_APPLICATIONS,
    TraceTable.INTRABAR_RESOLUTIONS,
)

#: 採番の順が判断時点の数や評価の順に依存する識別子の列。遅れた公開は判断時点を1つ増やし
#: （公開バッチの `EventId`）、評価の順を入れ替える（出力・評価・評価要求の識別子）ので、
#: 取引の結果を比べるときはこれらの列を外す。取引側の識別子（取引機会・発注試行・注文・
#: 約定・建玉・予約・根拠）は外さない。
_ORDER_DEPENDENT_SUFFIXES = ("output_id", "output_ids", "evaluation_id", "request_id", "event_id")

#: 日足の連鎖（日足の確定で起動する使用箇所と、市場状態の連鎖で待機する突破 Trigger）。
#: ケース2 で出力の判断時刻が2秒ずれてよいのはこの5件だけである（T02 §7.2）。
_DAILY_CHAIN = ("daily_ema", "daily_above_ema", "market_state", "entry_trigger", "no_short")


def _t(text: str) -> UtcTime:
    return UtcTime.parse(text)


def _flat(case: str, table: TraceTable) -> list[dict[str, object]]:
    """表の行を平坦化し、実行の識別子（ケースごとに違う）を外す。"""
    rows = []
    for row in run_case(case).rows(table):
        columns = flatten_row(row)
        columns.pop("run_id", None)
        rows.append(columns)
    return rows


def _without_order_dependent_ids(row: Mapping[str, object]) -> dict[str, object]:
    return {
        name: value
        for name, value in row.items()
        if not name.endswith(_ORDER_DEPENDENT_SUFFIXES) and name != "costs"
    }


def _first_delayed_publication(case: str) -> UtcTime:
    """そのケースで公開が最初に遅れる足の終了時刻（run 区間の中）。"""
    raw = {bar.key: bar for bar in raw_bars()[DAILY_SERIES]}
    ends = [
        bar.bar_end
        for bar in delayed_bars(case)[DAILY_SERIES]
        if bar.available_at != raw[bar.key].available_at and RUN_INTERVAL.start <= bar.bar_end
    ]
    return min(ends, key=lambda moment: moment.value)


# --- 1. 入力: 同じ素の足に遅延だけを当てている -----------------------------------------


@pytest.mark.parametrize("case", list(CASES))
def test_the_four_cases_share_the_same_raw_bars(case: str) -> None:
    """T02 §16 の不変条件2: 遅延を当てても OHLC と対象区間は1つも変わらない。

    違うのは日足の `available_at` だけで、その差はシナリオの遅延と一致する。1時間足と
    15分足は `available_at` まで同じである。
    """
    raw = raw_bars()
    delayed = delayed_bars(case)
    assert set(delayed) == set(raw)
    for series, bars in raw.items():
        shifted = delayed[series]
        assert [(b.interval, b.open, b.high, b.low, b.close) for b in shifted] == [
            (b.interval, b.open, b.high, b.low, b.close) for b in bars
        ], series
        delays = {
            s.available_at.value - b.available_at.value for s, b in zip(shifted, bars, strict=True)
        }
        if series != DAILY_SERIES or case == "none":
            assert delays == {timedelta(0)}, series
        elif case == "d1_2s":
            assert delays == {timedelta(seconds=2)}
        elif case == "d1_25h":
            assert delays == {timedelta(hours=25)}
        else:
            assert delays == {timedelta(0), timedelta(hours=25)}
            (held,) = [
                s for s, b in zip(shifted, bars, strict=True) if s.available_at != b.available_at
            ]
            assert held.bar_start == _t("2015-01-06T22:00:00Z")


# --- 2. 最初の遅れた公開まで、判断履歴は1行も違わない --------------------------------------


@pytest.mark.parametrize("case", _DELAYED)
def test_the_traces_agree_until_the_first_delayed_publication(case: str) -> None:
    """遅延は判断時点の見え方だけを変える。最初の遅れた公開より前の判断は4ケースで同じである。

    ケース4 では最初の遅れた公開が `01-07 22:00Z` なので、T02 経路1 の発注から経路8 の
    決済（`01-07 13:15Z`）までがこの範囲に入り、取引の記録が識別子まで一致する。
    """
    cutoff = _first_delayed_publication(case)
    expected_cutoff = {
        "d1_2s": "2015-01-05T22:00:00Z",
        "d1_25h": "2015-01-05T22:00:00Z",
        "d1_bar_hold": "2015-01-07T22:00:00Z",
    }[case]
    assert cutoff == _t(expected_cutoff)
    for table, column in _TIME_COLUMNS.items():

        def before(rows: list[dict[str, object]], column: str = column) -> list[dict[str, object]]:
            return [row for row in rows if _t(str(row[column])).value < cutoff.value]

        assert before(_flat(case, table)) == before(_flat("none", table)), table.value


# --- 3. 遅れた公開の後: 差がどこに現れるか -------------------------------------------------


def test_case_2_changes_no_trading_outcome() -> None:
    """ケース2（日足2秒）: 取引の結果（表3〜13・18・19）は遅延なしと同じである（T02 §7）。

    2秒の遅延は待機と同じ判断時点での再開で吸収され（D05 §6.8 の手順1〜5）、市場状態・
    取引機会・確認・発注・約定・損切りの更新・決済のどれも変わらない。
    """
    for table in _TRADE_TABLES:
        assert [_without_order_dependent_ids(row) for row in _flat("d1_2s", table)] == [
            _without_order_dependent_ids(row) for row in _flat("none", table)
        ], table.value


def test_case_2_shifts_only_the_publication_times_of_the_daily_chain() -> None:
    """ケース2: 出力の中身は同じで、日足の連鎖の出力だけが2秒遅れて出る（T02 §7.2・§16）。

    出力の中身（観測した足・観測区間・鮮度・値）は1件も変わらない。鮮度は足の終了時刻で
    決まり、公開時刻では決まらない（D05 §6.7）。
    """

    def by_content(case: str) -> dict[tuple[str, object], list[UtcTime]]:
        # 中身は判断履歴の列（正規化エンコード文字列）で比べる。
        found: dict[tuple[str, object], list[UtcTime]] = collections.defaultdict(list)
        for record in run_case(case).rows(TraceTable.OUTPUTS):
            assert isinstance(record, OutputRecord)
            assert record.available_at == record.decision_time
            content = flatten_row(record)["payload"]
            found[(record.producer.instance_id, content)].append(record.decision_time)
        return found

    none = by_content("none")
    delayed = by_content("d1_2s")
    # run 末尾（`01-16 22:00Z`）の日足は2秒後に届くので、ケース2 では末尾で見送りに閉じ、
    # 出力を出さない（D05 §6.1。評価の結果区分の差として次のテストが確かめる）。
    at_run_end = {
        key for key, times in none.items() if times == [RUN_INTERVAL.end] and key not in delayed
    }
    assert {key[0] for key in at_run_end} == {"daily_ema", "daily_above_ema", "market_state"}
    for key in at_run_end:
        del none[key]
    assert set(delayed) == set(none)
    shifted = 0
    for key, times in none.items():
        for before, after in zip(times, delayed[key], strict=True):
            if after == before:
                continue
            assert key[0] in _DAILY_CHAIN, key
            assert after.value - before.value == timedelta(seconds=2), key
            shifted += 1
    assert shifted > 0

    # T02 §16 のケース2 の検算値。
    (resumed,) = [
        record
        for record in run_case("d1_2s").rows(TraceTable.OUTPUTS)
        if isinstance(record, OutputRecord)
        and record.producer.instance_id == "daily_ema"
        and record.decision_time == _t("2015-01-07T22:00:02Z")
    ]
    payload = resumed.payload
    assert isinstance(payload, Observation)
    assert payload.freshness_time == _t("2015-01-07T22:00:00Z")
    assert payload.subject == BarKey(series=DAILY_SERIES, bar_start=_t("2015-01-06T22:00:00Z"))


def test_case_2_adds_decision_points_only_at_the_delayed_publications() -> None:
    """ケース2: 配送順の差として、遅れた日足の公開時刻に判断時点が1つずつ増える。

    遅れた公開は足の終了予定が1つも無い判断時点を作り、そこでも `step` を呼ぶ（T02 §1.5 の
    末尾。D06 §4.2 の「公開も足の終了も無い判断時点」には当たらない）。増えた判断時点の台帳
    snapshot の残高・資産は直前と同じで、それ以外の snapshot は遅延なしと同じである。

    通し番号（`at_sequence`）は比べない。run 末尾では待ったまま閉じた要求の待機の出来事が
    同じ `RUN_END` の処理点の番号を先に使うので、最終 snapshot の番号がずれる（待機の出来事の
    差の現れであり、処理点の時刻とフェーズは変わらない）。
    """

    def without_sequence(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {name: value for name, value in row.items() if name != "at_sequence"} for row in rows
        ]

    none = without_sequence(_flat("none", TraceTable.LEDGER_SNAPSHOTS))
    delayed = without_sequence(_flat("d1_2s", TraceTable.LEDGER_SNAPSHOTS))
    none_times = {row["at_time"] for row in none}
    publications = {
        str(bar.available_at)
        for bar in delayed_bars("d1_2s")[DAILY_SERIES]
        if RUN_INTERVAL.start.value < bar.available_at.value < RUN_INTERVAL.end.value
    }
    extra = [index for index, row in enumerate(delayed) if row["at_time"] not in none_times]
    assert {delayed[index]["at_time"] for index in extra} == publications
    for index in extra:
        for column in ("balance_amount", "equity_amount", "consumed_amount"):
            assert delayed[index][column] == delayed[index - 1][column], delayed[index]
    assert [row for row in delayed if row["at_time"] in none_times] == none


def test_case_2_differs_in_the_wait_events_and_the_evaluation_outcomes() -> None:
    """ケース2: 待機の出来事（表16）と評価記録の結果区分にだけ差が出る（T02 §7・§17）。

    遅延なしは待機を1度もしない。ケース2 は日足の確定ごとに日足の連鎖が待機し、同じ判断
    時点の2秒後に届いて再開する。追い越しも期限切れも起きない。評価要求ごとの**最後の**
    結末は遅延なしと同じで、違うのは run 末尾の日足（`01-16 22:00Z` の2秒後に届く）を待った
    まま末尾で見送りに閉じた要求だけである（D05 §6.1）。
    """
    assert _flat("none", TraceTable.WAIT_EVENTS) == []
    kinds = collections.Counter(row["kind"] for row in _flat("d1_2s", TraceTable.WAIT_EVENTS))
    assert set(kinds) == {"WAIT_STARTED", "INPUT_ARRIVED", "RESUMED", "RUN_END_CLOSED"}

    def final_outcomes(case: str) -> dict[tuple[object, ...], tuple[str, object]]:
        final: dict[tuple[object, ...], tuple[str, object]] = {}
        for record in run_case(case).rows(TraceTable.EVALUATIONS):
            assert isinstance(record, EvaluationRecord)
            key = (
                record.instance_id,
                record.target_interval,
                record.opportunity_id,
                record.position_id,
            )
            if record.outcome.kind != "WAITING":
                assert key not in final, key
                final[key] = (record.outcome.kind, record.decision_time)
        return final

    none = final_outcomes("none")
    delayed = final_outcomes("d1_2s")
    assert set(delayed) == set(none)
    closed_at_run_end = {
        key for key, (kind, decision_time) in delayed.items() if kind != none[key][0]
    }
    for key in closed_at_run_end:
        assert delayed[key] == ("SKIPPED", RUN_INTERVAL.end), key
        assert key[0] in _DAILY_CHAIN, key
    assert closed_at_run_end
    outcomes = {row["outcome_kind"] for row in _flat("d1_2s", TraceTable.EVALUATIONS)}
    assert "WAITING" in outcomes
    assert "SUPERSEDED" not in outcomes


def test_case_3_reaches_the_wait_deadline_and_places_no_order() -> None:
    """ケース3（日足全体が25時間）: 期限到達で見送り、注文は 0 件（T02 §8・§16）。

    `01-07 22:00Z` に待機を始めた日足の連鎖の要求は、`01-08 22:00Z` の予定境界で期限（日足1本）
    に到達し、`OPPORTUNITY_LIFECYCLE` の処理点で `DEADLINE_REACHED` と見送り（`Skipped`）で
    決着する。日足がその後に届いても**復活しない**（再開の記録が無い）。期限は予定境界で数え、
    公開では数えない（D05 §6.8）。
    """
    events = _flat("d1_25h", TraceTable.WAIT_EVENTS)
    started = {
        row["request_id"]
        for row in events
        if row["kind"] == "WAIT_STARTED" and row["at_time"] == "2015-01-07T22:00:00Z"
    }
    deadline = [
        row for row in events if row["kind"] == "DEADLINE_REACHED" and row["request_id"] in started
    ]
    assert deadline
    assert {(row["at_time"], row["at_phase"]) for row in deadline} == {
        ("2015-01-08T22:00:00Z", "OPPORTUNITY_LIFECYCLE")
    }
    assert not [row for row in events if row["kind"] == "RESUMED" and row["request_id"] in started]
    closed = {
        str(record.request_id): record
        for record in run_case("d1_25h").rows(TraceTable.EVALUATIONS)
        if isinstance(record, EvaluationRecord)
        and record.decision_time == _t("2015-01-08T22:00:00Z")
        and str(record.request_id) in {row["request_id"] for row in deadline}
    }
    assert {type(record.outcome) for record in closed.values()} == {Skipped}
    assert {record.instance_id for record in closed.values()} >= {
        "daily_ema",
        "daily_above_ema",
        "market_state",
    }
    # 取引機会も注文も1件も生まれない（T02 §8「この判断時点の注文数は 0」）。
    assert run_case("d1_25h").rows(TraceTable.ORDER_REQUESTS) == ()
    assert run_case("d1_25h").result.opportunity_count == 0


def test_case_4_supersedes_the_waiting_trigger_at_the_next_hour() -> None:
    """ケース4（D(Jan7) だけ25時間）: 1時間足の待機要求が次の1時間足で追い越される（T02 §9・§16）。

    `01-07 22:00Z` に待機を始めた `entry_trigger` の要求は、`01-07 23:00Z` の
    `OPPORTUNITY_LIFECYCLE` で追い越され（理由 `REQUEST_SUPERSEDED`）、評価記録は
    `Superseded(by_request_id=新しい要求)` になる。新しい要求（`[22:00, 23:00)` の足）もまた
    待機に入る。古い突破を後から実行しない（上位設計書 §4.3.14）。
    """
    events = _flat("d1_bar_hold", TraceTable.WAIT_EVENTS)
    records = [
        record
        for record in run_case("d1_bar_hold").rows(TraceTable.EVALUATIONS)
        if isinstance(record, EvaluationRecord) and record.instance_id == "entry_trigger"
    ]
    (waiting,) = [
        record
        for record in records
        if record.decision_time == _t("2015-01-07T22:00:00Z") and record.outcome.kind == "WAITING"
    ]
    request_id = str(waiting.request_id)
    (superseded,) = [
        row for row in events if row["request_id"] == request_id and row["kind"] == "SUPERSEDED"
    ]
    assert (superseded["at_time"], superseded["at_phase"], superseded["reason_code"]) == (
        "2015-01-07T23:00:00Z",
        "OPPORTUNITY_LIFECYCLE",
        "REQUEST_SUPERSEDED",
    )
    (closed,) = [
        record
        for record in records
        if record.request_id == waiting.request_id and isinstance(record.outcome, Superseded)
    ]
    assert closed.decision_time == _t("2015-01-07T23:00:00Z")
    assert isinstance(closed.outcome, Superseded)
    by = str(closed.outcome.by_request_id)
    (new_wait,) = [
        row for row in events if row["request_id"] == by and row["kind"] == "WAIT_STARTED"
    ]
    assert new_wait["at_time"] == "2015-01-07T23:00:00Z"
    # 遅延なしの T02 経路9（`01-08 09:00Z` の発火）は、日足 D(Jan7) が届かないので起きない。
    assert run_case("none").result.opportunity_count == 3
    assert run_case("d1_bar_hold").result.opportunity_count == 2


def test_the_four_cases_line_up_in_the_evaluation_outcome_counts(tmp_path: Path) -> None:
    """4ケースの評価の結果区分の集計は同じ5語の行を持ち、並べて比べられる（D07 §6.1 v1.4）。

    段階3 の評価は語彙を5語にし、0件の鍵も行として出す。そうしないと待機だけが起きた run で
    追い越しの行が出ず、ケースごとに行の集合が変わる（段階3 の完了条件「遅延シナリオ別の
    差分を追跡できる」。全体計画 §8.2）。
    """
    counts: dict[str, dict[str, int]] = {}
    for case in CASES:
        report = evaluate_case(case, tmp_path / case)
        counts[case] = {
            item.key: item.count
            for item in report.categories
            if item.category.value == "EVALUATION_OUTCOME"
        }
    keys = ["EVALUATED", "SKIPPED", "FAILED", "WAITING", "SUPERSEDED"]
    assert all(list(value) == keys for value in counts.values()), counts
    assert (counts["none"]["WAITING"], counts["none"]["SUPERSEDED"]) == (0, 0)
    assert counts["d1_2s"]["WAITING"] > 0
    assert counts["d1_2s"]["SUPERSEDED"] == 0
    for case in ("d1_25h", "d1_bar_hold"):
        assert counts[case]["WAITING"] > 0, case
        assert counts[case]["SUPERSEDED"] > 0, case
    assert all(value["FAILED"] == 0 for value in counts.values())
