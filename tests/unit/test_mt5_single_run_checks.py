"""順 3 の整合判定の道具（`tools/survey/mt5_single_run_checks.py`）の単体テスト。

設計の正本は D12 §12.5.1【決定】。

合成した run の成果物（実験の結末記録・run manifest・判断履歴の表・評価の記録・再現の判定）を
一時ディレクトリに書き、判定 1〜4 の合格・不合格・確認不能の各分岐を確かめる。判断履歴の表には
価格の列も置き、道具がそれを読まないこと（読み込みの列の指定から外していること）も確かめる。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from tools.survey import mt5_single_run_checks as checks
from tools.survey.mt5_single_run_checks import FAIL, NOT_EVALUATED, PASS, UNVERIFIABLE

RUN_ID = "a" * 64
EVAL_ID = "b" * 64
RESULT = "c" * 64
SNAPSHOT = "d" * 64
DAILY = "USDJPY/1d_ny17/bid"
HOURLY = "USDJPY/1h/bid"
TABLE = {
    "sha256": "e" * 64,
    "table_id": "oanda_mt5_time_rules",
    "variant": "standard",
    "version": 1,
}
RUN_START, RUN_END = "2022-12-01T22:00:00Z", "2023-03-31T21:00:00Z"
#: 日足 60 本の窓の準備のやり直しの期間（D11 §12.2）。
RESET_FROM, RESET_READY = "2022-12-26T22:00:00Z", "2023-03-20T21:00:00Z"

_STRING_TABLES: Mapping[str, Sequence[str]] = {
    "EVALUATIONS": ("evaluation_id", "decision_time", "outcome_kind"),
    "HISTORY_RESETS": (
        "series",
        "no_record_bar_key_bar_start",
        "window_kind",
        "window_duration",
        "insufficient_from",
        "ready_at",
    ),
    "PUBLICATIONS": ("bar_key_series", "bar_key_bar_start", "available_at"),
    "POSITIONS": ("position_id", "opened_at_time", "status", "close_fill_id", "entry_price"),
    "FILLS": ("fill_id", "processed_at_time", "price"),
    "ORDERS": ("order_id", "accepted_at_time"),
    "ORDER_EVENTS": ("order_id", "to_status", "at_time"),
    "LEDGER_SNAPSHOTS": ("at_time", "balance_amount"),
}
_SCHEMAS: Mapping[str, Mapping[str, Any]] = {
    table: {
        **{name: pl.String for name in names},
        **(
            {"outcome_diagnoses": pl.List(pl.String)}
            if table == "EVALUATIONS"
            else {"window_bars": pl.Int64, "exclude_latest_bars": pl.Int64}
            if table == "HISTORY_RESETS"
            else {"recorded_minutes": pl.Int64}
            if table == "PUBLICATIONS"
            else {}
        ),
    }
    for table, names in _STRING_TABLES.items()
}


def _diagnosis(reason: str) -> str:
    return json.dumps({"input_name": "prices", "reason": reason, "source": {"series": DAILY}})


def _skip(time: str, reason: str = "WARMUP_INSUFFICIENT") -> dict[str, Any]:
    return {
        "evaluation_id": f"EVAL-{time}",
        "decision_time": time,
        "outcome_kind": "SKIPPED",
        "outcome_diagnoses": [_diagnosis(reason)],
    }


def _reset(
    series: str, bar_start: str, start: str, ready: str | None, kind: str = "BARS", bars: int = 60
) -> dict[str, Any]:
    return {
        "series": series,
        "no_record_bar_key_bar_start": bar_start,
        "window_kind": kind,
        "window_bars": bars if kind == "BARS" else None,
        "exclude_latest_bars": 0,
        "window_duration": None,
        "insufficient_from": start,
        "ready_at": ready,
    }


def _publication(
    series: str, bar_start: str, available_at: str, recorded: int = 1440
) -> dict[str, Any]:
    return {
        "bar_key_series": series,
        "bar_key_bar_start": bar_start,
        "available_at": available_at,
        "recorded_minutes": recorded,
    }


def _baseline_tables() -> dict[str, list[dict[str, Any]]]:
    """日足の準備のやり直しが 1 回あり、期間の始まりをまたぐ建玉と注文がある合格の run。"""
    return {
        "EVALUATIONS": [
            _skip("2022-12-02T22:00:00Z"),  # データの開始の準備（窓の 1 本目の公開より前）
            _skip("2022-12-27T22:00:00Z"),
            {
                "evaluation_id": "EVAL-OK",
                "decision_time": "2023-03-21T21:00:00Z",
                "outcome_kind": "EVALUATED",
                "outcome_diagnoses": [],
            },
        ],
        "HISTORY_RESETS": [
            _reset(DAILY, "2022-12-25T22:00:00Z", RESET_FROM, RESET_READY, bars=2),
            _reset(
                DAILY, "2022-12-25T22:00:00Z", RESET_FROM, "2022-12-27T22:00:00Z", kind="LATEST"
            ),
        ],
        "PUBLICATIONS": [
            _publication(DAILY, "2022-12-01T22:00:00Z", "2022-12-02T22:00:00Z"),
            _publication(DAILY, "2022-12-02T22:00:00Z", "2022-12-05T22:00:00Z"),
            _publication(DAILY, "2022-12-26T22:00:00Z", "2022-12-27T22:00:00Z"),
        ],
        "POSITIONS": [
            {
                "position_id": "POS-1",
                "opened_at_time": "2022-12-20T10:00:00Z",
                "status": "CLOSED",
                "close_fill_id": "FIL-2",
                "entry_price": "999",
            }
        ],
        "FILLS": [
            {"fill_id": "FIL-1", "processed_at_time": "2022-12-20T10:00:00Z", "price": "999"},
            {"fill_id": "FIL-2", "processed_at_time": "2023-01-10T10:00:00Z", "price": "999"},
        ],
        "ORDERS": [{"order_id": "ORD-1", "accepted_at_time": "2022-12-26T21:00:00Z"}],
        "ORDER_EVENTS": [
            {"order_id": "ORD-1", "to_status": "PENDING", "at_time": "2022-12-26T21:00:00Z"},
            {"order_id": "ORD-1", "to_status": "EXPIRED", "at_time": "2022-12-26T22:00:00Z"},
        ],
        "LEDGER_SNAPSHOTS": [
            {"at_time": "2022-12-20T10:00:00Z", "balance_amount": "999"},
            {"at_time": "2023-01-10T10:00:00Z", "balance_amount": "999"},
        ],
    }


def _write_run(
    root: Path,
    *,
    tables: Mapping[str, list[dict[str, Any]]] | None = None,
    counts: Mapping[str, int] | None = None,
    history_resets: int = 2,
    consistency: Sequence[str] = ("PASSED", "PASSED"),
    reproduction: str | None = "REPRODUCED",
    run_id: str = RUN_ID,
) -> tuple[Path, Path]:
    """合成した成果物を書き、実験の版のディレクトリと再現の出力の基点を返す。"""
    out = root / "out"
    experiment = out / "runs" / "experiments" / "x" / "v1"
    run_dir = out / "runs" / run_id
    eval_dir = run_dir / "eval" / EVAL_ID
    for path in (experiment, eval_dir):
        path.mkdir(parents=True, exist_ok=True)
    (experiment / "experiment_outcome.json").write_text(
        json.dumps({"run_id": run_id, "run_evaluation_id": EVAL_ID, "result_digest": RESULT})
    )
    config = {
        "run_interval": {"start": RUN_START, "end": RUN_END},
        "snapshot_ref": {"snapshot_id": SNAPSHOT},
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "COMPLETED",
                "config_digest": "f" * 64,
                "config": json.dumps(config),
                "time_rule_table_ref": TABLE,
                "resumption_counts": {"history_resets": history_resets},
            }
        )
    )
    for table, rows in (tables or _baseline_tables()).items():
        pl.DataFrame(rows, schema=dict(_SCHEMAS[table])).write_parquet(run_dir / f"{table}.parquet")
    (eval_dir / "evaluation.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "COMPLETED",
                "result_digest": RESULT,
                "time_rule_table_ref": TABLE,
                "fatal_failure_count": 0,
                "warning_failure_count": 0,
                "unreadable_check_count": 0,
            }
        )
    )
    pl.DataFrame(
        [
            {
                "check": f"C{i}",
                "level": "FATAL",
                "outcome": outcome,
                "expected": "1",
                "observed": "1",
            }
            for i, outcome in enumerate(consistency)
        ]
    ).write_parquet(eval_dir / "CONSISTENCY_CHECKS.parquet")
    resolved = counts or {"HISTORY_RESETS": history_resets, "HISTORY_RESET_MINUTES": 100}
    pl.DataFrame(
        [
            {"category": "UNRECORDED_EVENT", "key": key, "count": value}
            for key, value in resolved.items()
        ]
    ).write_parquet(eval_dir / "CATEGORY_COUNTS.parquet")
    repro = root / "repro"
    if reproduction is not None:
        repro.mkdir(parents=True, exist_ok=True)
        (repro / "reproduction.json").write_text(
            json.dumps(
                {
                    "verdict": reproduction,
                    "observed_run_id": run_id,
                    "observed_result_digest": RESULT,
                }
            )
        )
    return experiment, repro


def _load(tmp_path: Path, **kwargs: Any) -> checks.RunArtifacts:
    experiment, repro = _write_run(tmp_path, **kwargs)
    reproduction = repro if kwargs.get("reproduction", "REPRODUCED") is not None else None
    return checks.load_run("x", experiment, reproduction_dir=reproduction)


# --- 読み込み ------------------------------------------------------------------------


def test_price_columns_are_not_read(tmp_path: Path) -> None:
    run = _load(tmp_path)
    assert "price" not in run.tables["FILLS"][0]
    assert "entry_price" not in run.tables["POSITIONS"][0]
    assert "balance_amount" not in run.tables["LEDGER_SNAPSHOTS"][0]
    assert run.consistency and set(run.consistency[0]) == {"check", "level", "outcome"}


# --- 判定 1 ---------------------------------------------------------------------------


def test_check_1_passes_when_a_position_spans_the_reset(tmp_path: Path) -> None:
    result = checks.check_history_reset_scoring(_load(tmp_path))
    assert result["result"] == PASS
    assert result["detail"]["periods"] == [[RESET_FROM, RESET_READY]]
    assert result["detail"]["target_series"] == DAILY
    assert result["detail"]["continuing_positions"] == 1


def test_check_1_is_unverifiable_without_a_spanning_position_or_order(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["POSITIONS"] = []
    tables["ORDERS"] = []
    tables["ORDER_EVENTS"] = []
    result = checks.check_history_reset_scoring(_load(tmp_path, tables=tables))
    assert result["result"] == UNVERIFIABLE
    assert result["parts"]["e_positions_orders_continue"] == UNVERIFIABLE
    assert (
        "test_c2_the_run_records_history_resets_and_keeps_going"
        in result["detail"]["unverifiable_reason"]
    )


def test_check_1_fails_when_an_order_is_canceled_at_the_reset(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["ORDER_EVENTS"][1]["to_status"] = "CANCELED"
    result = checks.check_history_reset_scoring(_load(tmp_path, tables=tables))
    assert result["result"] == FAIL
    assert result["detail"]["canceled_at_period_start"] == 1


def test_check_1_fails_without_reset_rows_for_the_day(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["HISTORY_RESETS"] = []
    result = checks.check_history_reset_scoring(_load(tmp_path, tables=tables, history_resets=0))
    assert result["result"] == FAIL
    assert result["parts"]["b_reset_rows"] == FAIL


def test_check_1_fails_when_the_reset_minutes_are_not_scored(tmp_path: Path) -> None:
    counts = {"HISTORY_RESETS": 2, "HISTORY_RESET_MINUTES": 0}
    result = checks.check_history_reset_scoring(_load(tmp_path, counts=counts))
    assert result["parts"]["c_scored"] == FAIL


def test_check_1_uses_intraday_rows_for_a_strategy_without_daily_reads(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["HISTORY_RESETS"] = [
        _reset(
            HOURLY, "2022-12-25T22:00:00Z", "2022-12-25T23:00:00Z", "2022-12-27T19:00:00Z", bars=20
        ),
        _reset(
            HOURLY, "2022-12-26T05:00:00Z", "2022-12-26T06:00:00Z", "2022-12-27T19:00:00Z", bars=20
        ),
    ]
    result = checks.check_history_reset_scoring(_load(tmp_path, tables=tables))
    assert result["detail"]["target_series"] == "intraday"
    assert result["detail"]["periods"] == [["2022-12-25T23:00:00Z", "2022-12-27T19:00:00Z"]]


# --- 判定 2 ---------------------------------------------------------------------------


def test_check_2_passes_and_excludes_the_initial_warmup(tmp_path: Path) -> None:
    result = checks.check_skips_match_resets(_load(tmp_path))
    assert result["parts"]["a_skips_inside_periods"] == PASS
    assert result["detail"]["excluded_initial_skips"] == 1
    # 最新足だけの窓の行は、期間の中に日足の公開が無い（整う時刻で次の足が公開される）。
    assert result["detail"]["rows_without_trigger"] == 1
    assert result["detail"]["rows_with_skip"] == 1
    assert result["result"] == UNVERIFIABLE


def test_check_2_passes_when_every_period_has_a_skip(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["HISTORY_RESETS"] = tables["HISTORY_RESETS"][:1]
    result = checks.check_skips_match_resets(_load(tmp_path, tables=tables, history_resets=1))
    assert result["result"] == PASS


def test_check_2_fails_for_a_skip_outside_every_period(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["EVALUATIONS"].append(_skip("2023-03-25T21:00:00Z"))
    result = checks.check_skips_match_resets(_load(tmp_path, tables=tables))
    assert result["parts"]["a_skips_inside_periods"] == FAIL
    assert result["detail"]["skips_outside_periods"] == 1


def test_check_2_fails_when_a_period_with_publications_has_no_skip(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["EVALUATIONS"] = [
        row for row in tables["EVALUATIONS"] if row["decision_time"] != "2022-12-27T22:00:00Z"
    ]
    tables["HISTORY_RESETS"] = tables["HISTORY_RESETS"][:1]
    result = checks.check_skips_match_resets(_load(tmp_path, tables=tables, history_resets=1))
    assert result["parts"]["b_each_period_has_skip"] == FAIL


def test_check_2_ignores_skips_for_other_reasons(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["EVALUATIONS"].append(_skip("2023-03-25T21:00:00Z", reason="LATEST_BAR_UNAVAILABLE"))
    tables["HISTORY_RESETS"] = tables["HISTORY_RESETS"][:1]
    result = checks.check_skips_match_resets(_load(tmp_path, tables=tables, history_resets=1))
    assert result["result"] == PASS


def test_check_2_is_unverifiable_when_the_windows_are_unknown(tmp_path: Path) -> None:
    tables = _baseline_tables()
    tables["HISTORY_RESETS"] = []
    result = checks.check_skips_match_resets(_load(tmp_path, tables=tables, history_resets=0))
    assert result["parts"]["a_skips_inside_periods"] == UNVERIFIABLE


# --- 判定 3 ---------------------------------------------------------------------------


def test_check_3_passes_with_a_reproduction(tmp_path: Path) -> None:
    result = checks.check_integrity(_load(tmp_path))
    assert result["result"] == PASS
    assert result["detail"]["result_digest"] == RESULT


def test_check_3_fails_on_a_result_mismatch(tmp_path: Path) -> None:
    result = checks.check_integrity(_load(tmp_path, reproduction="RESULT_MISMATCH"))
    assert result["parts"]["c_reproduced"] == FAIL
    assert result["result"] == FAIL


def test_check_3_fails_on_a_failed_consistency_check(tmp_path: Path) -> None:
    result = checks.check_integrity(_load(tmp_path, consistency=("PASSED", "FAILED")))
    assert result["parts"]["a_consistency_checks"] == FAIL


def test_check_3_is_not_evaluated_without_a_reproduction(tmp_path: Path) -> None:
    result = checks.check_integrity(_load(tmp_path, reproduction=None))
    assert result["parts"]["c_reproduced"] == NOT_EVALUATED
    assert result["result"] == NOT_EVALUATED


# --- 判定 4 ---------------------------------------------------------------------------


def _daily_run(tmp_path: Path, name: str, recorded: Mapping[str, int]) -> checks.RunArtifacts:
    tables = _baseline_tables()
    tables["PUBLICATIONS"] = [
        _publication(DAILY, start, start, minutes) for start, minutes in recorded.items()
    ]
    return _load(tmp_path / name, tables=tables)


def test_check_4_matches_the_aggregation(tmp_path: Path) -> None:
    standard = _daily_run(
        tmp_path, "s", {"2017-03-12T21:00:00Z": 1374, "2017-03-13T21:00:00Z": 1440}
    )
    candidate = _daily_run(
        tmp_path, "c", {"2017-03-12T21:00:00Z": 1430, "2017-03-13T21:00:00Z": 1440}
    )
    result = checks.check_daily_differences(standard, candidate, {"2017-03-13"})
    assert result["result"] == PASS
    assert result["detail"]["differing_days"] == 1


def test_check_4_fails_on_an_unexpected_difference(tmp_path: Path) -> None:
    standard = _daily_run(
        tmp_path, "s", {"2017-03-12T21:00:00Z": 1374, "2017-03-13T21:00:00Z": 1440}
    )
    candidate = _daily_run(
        tmp_path, "c", {"2017-03-12T21:00:00Z": 1430, "2017-03-13T21:00:00Z": 1400}
    )
    result = checks.check_daily_differences(standard, candidate, {"2017-03-13"})
    assert result["result"] == FAIL
    assert result["detail"]["unexpected_days"] == ["2017-03-14"]


def test_check_4_is_unverifiable_for_an_unpublished_day(tmp_path: Path) -> None:
    standard = _daily_run(tmp_path, "s", {"2017-03-12T21:00:00Z": 1374})
    candidate = _daily_run(tmp_path, "c", {"2017-03-12T21:00:00Z": 1430})
    result = checks.check_daily_differences(standard, candidate, {"2017-03-13", "2017-03-20"})
    assert result["result"] == UNVERIFIABLE
    assert result["detail"]["not_published_days"] == ["2017-03-20"]


def test_read_daily_differences_takes_the_recorded_minutes_rows(tmp_path: Path) -> None:
    path = tmp_path / "diff.csv"
    path.write_text(
        "pair,scope,key,field,standard_value,candidate_value\n"
        "USDJPY,daily_bar,2017-03-13,recorded_minutes,1374,1430\n"
        "USDJPY,daily_bar,2017-03-13,unrecorded_minutes,66,10\n"
        "EURUSD,daily_bar,2017-03-14,recorded_minutes,1,2\n"
        "USDJPY,bar_counts,15m,full_bars,1,2\n",
        encoding="utf-8",
    )
    assert checks.read_daily_differences(path) == {"2017-03-13"}


# --- 入口 -----------------------------------------------------------------------------


@pytest.mark.parametrize("with_pair", [False, True])
def test_main_writes_only_results_counts_and_hashes(tmp_path: Path, with_pair: bool) -> None:
    experiment, repro = _write_run(tmp_path)
    differences = tmp_path / "diff.csv"
    differences.write_text(
        "pair,scope,key,field,standard_value,candidate_value\n", encoding="utf-8"
    )
    out = tmp_path / "checks.json"
    argv = ["--experiment", f"b_standard={experiment}", "--reproduction", f"b_standard={repro}"]
    if with_pair:
        argv += [
            "--experiment",
            f"b_candidate={experiment}",
            "--pair-b",
            "b_standard",
            "b_candidate",
            "--differences",
            str(differences),
        ]
    assert checks.main([*argv, "--out", str(out)]) == 0
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["runs"]["b_standard"]["check_3_integrity"]["result"] == PASS
    expected = PASS if with_pair else NOT_EVALUATED
    assert record["check_4_daily_differences"]["result"] == expected
    assert "999" not in out.read_text(encoding="utf-8")  # 合成の価格の値は出力に現れない
