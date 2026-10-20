"""順 3 の整合判定（D12 §12.5.1【決定】）: MT5 経路の単一実行の成果物を、価格を読まずに照合する。

検証戦略 A・B × 標準・候補の組の run（`odyssey-fx experiment run`）と、その別プロセスでの再現
（`odyssey-fx experiment reproduce`）の成果物を読み、次の 4 つを判定する。

1. 準備のやり直しの期間が採点に含まれ、建玉・注文が続く（D11 §12.2、D06 §18.8、D07 §28.2）。
2. 表22（準備のやり直し）と、実行時に記録された「準備不足で見送り」が一致する（D06 §18.10）。
3. 損益・記録・manifest・再現が整合する（D07 §10.2・§21.1）。
4. 2 組の間で被覆が変わる日足が、戦略 B の公開記録の差として現れる（D12 §12.5 の 3 行目）。

判定は 3 値（`PASS` 合格／`FAIL` 不合格／`UNVERIFIABLE` 確認不能（仮定付き））。確認不能は合格に
数えない（D12 §0.1.34 の決定 3）。道具に入力が渡されなかった判定は `NOT_EVALUATED`（未判定）とする。

**読むのは価格を含まない列だけ**（時刻・識別子・区分・件数・被覆）。判断履歴は列を指定して読み、
価格・数量・損益・残高の列（表9 の `price`・`quantity`・費用、表11 の `entry_price`・`quantity`・
`realized_amount`、表14 の残高・時価、表7 の参照価格、表21 の価格など）は開かない。run の結果
（`result.json`）の要約（損益）と評価の指標・取引表（`METRICS`・`TRADES`）も読まない。整合検査の
表は `check`・`level`・`outcome` の 3 列だけを読む。出力は合否・件数・時刻・ハッシュ・処理時間だけ。

実行（リポジトリの根で）::

    uv run python -m tools.survey.mt5_single_run_checks \\
        --experiment a_standard=<out>/runs/experiments/strategy_a_usdjpy_mt5_standard/v1 \\
        --reproduction a_standard=<再現の out> \\
        ... \\
        --pair-b b_standard b_candidate \\
        --differences docs/data/full_period_feasibility/candidate_differences.csv \\
        --snapshots data/snapshots \\
        --calendar configs/calendars/fx_ny17_oanda_v1.yaml \\
        --out docs/data/usdjpy_step3_ab_runs/checks.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Final

import polars as pl

from odyssey_fx.app.config.calendars import load_calendar

__all__ = [
    "FAIL",
    "NOT_EVALUATED",
    "PASS",
    "UNVERIFIABLE",
    "RunArtifacts",
    "check_daily_differences",
    "check_history_reset_scoring",
    "check_integrity",
    "check_skips_match_resets",
    "load_run",
    "main",
]

PASS: Final = "PASS"
FAIL: Final = "FAIL"
UNVERIFIABLE: Final = "UNVERIFIABLE"
NOT_EVALUATED: Final = "NOT_EVALUATED"

#: 2022-12-26 の全分無記録の取引日（NY 17 時区切り。D11 §12.2、D12 §12.2 の行 2）。
RESET_DAY: Final = ("2022-12-25T22:00:00Z", "2022-12-26T22:00:00Z")
#: 判定 4 で比べる日足の系列（D12 §12.5.1 の判定 4）。
DAILY_SERIES: Final = "USDJPY/1d_ny17/bid"
#: 判定 4 で比べる被覆の列（D12 §12.5.1 の決定 4「記録のある分数だけ」）。
COVERAGE_COLUMN: Final = "recorded_minutes"
#: システムによる強制決済の決済の契機（D06 §7.5 の緊急決済。D12 §12.5.1、決定 24）。
FORCED_CLOSE_CAUSES: Final = frozenset({"EMERGENCY"})
#: 通常の規則による決済の契機（保護水準・戦略の決済。D06 §7.3）。
NORMAL_CLOSE_CAUSES: Final = frozenset({"STOP_LOSS", "TAKE_PROFIT", "STRATEGY_EXIT"})

_WARMUP: Final = "WARMUP_INSUFFICIENT"
_SKIPPED: Final = "SKIPPED"
#: 期間の始まりに起きてよい注文の終了（D12 §12.5.1 の判定 1 (e)。決定 14・17）。
_ALLOWED_ENDS: Final = frozenset({"CANCELED", "EXPIRED"})
_COMPLETED: Final = "COMPLETED"
_REPRODUCED: Final = "REPRODUCED"
_PASSED: Final = "PASSED"

#: 判断履歴の表ごとに読む列（価格・数量・損益・残高の列を含めない）。
TRACE_COLUMNS: Final[Mapping[str, tuple[str, ...]]] = {
    "EVALUATIONS": ("evaluation_id", "decision_time", "outcome_kind", "outcome_diagnoses"),
    "HISTORY_RESETS": (
        "series",
        "no_record_bar_key_bar_start",
        "window_kind",
        "window_bars",
        "exclude_latest_bars",
        "window_duration",
        "insufficient_from",
        "ready_at",
    ),
    "PUBLICATIONS": (
        "bar_key_series",
        "bar_key_bar_start",
        "bar_end",
        "available_at",
        "recorded_minutes",
    ),
    "POSITIONS": ("position_id", "opened_at_time", "status", "close_fill_id"),
    "FILLS": ("fill_id", "order_id", "processed_at_time"),
    "ORDERS": ("order_id", "accepted_at_time", "terms_kind", "terms_cause"),
    "ORDER_EVENTS": ("order_id", "to_status", "at_time"),
    "LEDGER_SNAPSHOTS": ("at_time",),
}
#: 評価の整合検査の表から読む列（`expected`・`observed` は値を含みうるので読まない）。
CONSISTENCY_COLUMNS: Final = ("check", "level", "outcome")


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class RunArtifacts:
    """1 本の run の成果物のうち、判定に使う部分（価格を含まない）。"""

    name: str
    run_id: str
    status: str
    config_digest: str
    snapshot_id: str
    run_interval: tuple[datetime, datetime]
    time_rule_table_ref: Mapping[str, Any]
    history_reset_count: int
    tables: Mapping[str, list[dict[str, Any]]]
    evaluation: Mapping[str, Any] | None = None
    consistency: list[dict[str, Any]] = field(default_factory=list)
    unrecorded_event_counts: Mapping[str, int] = field(default_factory=dict)
    outcome: Mapping[str, Any] | None = None
    reproduction: Mapping[str, Any] | None = None
    #: 実験の記録票の snapshot の識別子（D12 §12.5.1 の判定 3 (b)、決定 26）。
    experiment_snapshot_id: str | None = None
    #: snapshot の manifest の識別子と時刻の規則の表の参照（読めなければ `None`）。
    snapshot_manifest_id: str | None = None
    snapshot_time_rule_table: Mapping[str, Any] | None = None


def _read_table(path: Path, columns: Sequence[str]) -> list[dict[str, Any]]:
    return pl.read_parquet(path, columns=list(columns)).to_dicts()


def load_run(
    name: str,
    experiment_dir: Path,
    *,
    reproduction_dir: Path | None = None,
    snapshots: Path,
) -> RunArtifacts:
    """実験の版のディレクトリから run・評価・結末記録・再現の判定を読む（価格の列は読まない）。"""
    outcome = json.loads((experiment_dir / "experiment_outcome.json").read_text(encoding="utf-8"))
    run_id = str(outcome["run_id"])
    run_dir = experiment_dir.parents[2] / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    config = (
        json.loads(manifest["config"])
        if isinstance(manifest["config"], str)
        else manifest["config"]
    )
    interval = config["run_interval"]
    tables = {
        table: _read_table(run_dir / f"{table}.parquet", columns)
        for table, columns in TRACE_COLUMNS.items()
    }
    evaluation: Mapping[str, Any] | None = None
    consistency: list[dict[str, Any]] = []
    unrecorded: dict[str, int] = {}
    evaluation_id = outcome.get("run_evaluation_id")
    if evaluation_id:
        eval_dir = run_dir / "eval" / str(evaluation_id)
        evaluation = json.loads((eval_dir / "evaluation.json").read_text(encoding="utf-8"))
        consistency = _read_table(eval_dir / "CONSISTENCY_CHECKS.parquet", CONSISTENCY_COLUMNS)
        counts = (
            pl.scan_parquet(eval_dir / "CATEGORY_COUNTS.parquet")
            .select("category", "key", "count")
            .filter(pl.col("category") == "UNRECORDED_EVENT")
            .collect()
        )
        unrecorded = {str(row["key"]): int(row["count"]) for row in counts.to_dicts()}
    reproduction = None
    if reproduction_dir is not None:
        reproduction = json.loads(
            (reproduction_dir / "reproduction.json").read_text(encoding="utf-8")
        )
    snapshot_id = str(config["snapshot_ref"]["snapshot_id"])
    experiment_manifest = experiment_dir / "experiment_manifest.json"
    experiment_snapshot = (
        json.loads(experiment_manifest.read_text(encoding="utf-8")).get("snapshot_id")
        if experiment_manifest.is_file()
        else None
    )
    snapshot_manifest_id = None
    snapshot_table = None
    snapshot_path = snapshots / snapshot_id / "manifest.json"
    if snapshot_path.is_file():  # 読めなければ判定 3 (b) を不合格にする（D12 §12.5.1）
        snapshot_manifest = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot_manifest_id = snapshot_manifest.get("snapshot_id")
        conversion = snapshot_manifest.get("conversion") or {}
        snapshot_table = (conversion.get("mt5") or {}).get("time_rule_table")
    return RunArtifacts(
        name=name,
        run_id=str(manifest["run_id"]),
        status=str(manifest["status"]),
        config_digest=str(manifest["config_digest"]),
        snapshot_id=snapshot_id,
        run_interval=(_time(interval["start"]), _time(interval["end"])),
        time_rule_table_ref=manifest.get("time_rule_table_ref") or {},
        history_reset_count=int((manifest.get("resumption_counts") or {}).get("history_resets", 0)),
        tables=tables,
        evaluation=evaluation,
        consistency=consistency,
        unrecorded_event_counts=unrecorded,
        outcome=outcome,
        reproduction=reproduction,
        experiment_snapshot_id=None if experiment_snapshot is None else str(experiment_snapshot),
        snapshot_manifest_id=None if snapshot_manifest_id is None else str(snapshot_manifest_id),
        snapshot_time_rule_table=snapshot_table,
    )


# --- 共通 -------------------------------------------------------------------------


def _combine(results: Iterable[str]) -> str:
    """部分の結果をまとめる: 1 つでも不合格なら不合格、すべて合格なら合格、他は確認不能。"""
    values = list(results)
    if FAIL in values:
        return FAIL
    if values and all(value == PASS for value in values):
        return PASS
    return UNVERIFIABLE


def _reset_periods(run: RunArtifacts) -> list[tuple[datetime, datetime, dict[str, Any]]]:
    """表22 の各行の期間 `[insufficient_from, ready_at)`（整わなければ run の終わりまで）。"""
    end = run.run_interval[1]
    periods = []
    for row in run.tables["HISTORY_RESETS"]:
        start = _time(row["insufficient_from"])
        stop = _time(row["ready_at"]) if row.get("ready_at") else end
        periods.append((start, stop, row))
    return periods


def _merge(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, stop in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
        else:
            merged.append((start, stop))
    return merged


def _is_warmup_skip(row: Mapping[str, Any]) -> bool:
    if row.get("outcome_kind") != _SKIPPED:
        return False
    for item in row.get("outcome_diagnoses") or ():
        payload = json.loads(item) if isinstance(item, str) else item
        if isinstance(payload, dict) and payload.get("reason") == _WARMUP:
            return True
    return False


# --- 判定 1 -----------------------------------------------------------------------


def check_history_reset_scoring(
    run: RunArtifacts, reset_day: tuple[str, str] = RESET_DAY
) -> dict[str, Any]:
    """判定 1: 2022-12-26 の無記録の足による準備のやり直しの期間が採点に含まれ、建玉・注文が続く。

    D11 §12.2、D06 §18.8。
    """
    run_start, run_end = run.run_interval
    day_start, day_end = _time(reset_day[0]), _time(reset_day[1])
    on_day = [
        (start, stop, row)
        for start, stop, row in _reset_periods(run)
        if day_start <= _time(row["no_record_bar_key_bar_start"]) < day_end
    ]
    # 日足を読む戦略（B）は日足の窓の行、読まない戦略（A）はその日の無記録の足の行（決定 1）。
    daily = [(start, stop, row) for start, stop, row in on_day if row["series"] == DAILY_SERIES]
    chosen = daily or on_day
    targets = [(start, stop) for start, stop, _ in chosen]
    parts: dict[str, str] = {"a_completed": PASS if run.status == _COMPLETED else FAIL}
    detail: dict[str, Any] = {"table22_rows": len(run.tables["HISTORY_RESETS"])}
    if not targets:
        parts["b_reset_rows"] = FAIL
        detail["target_rows"] = 0
        return {"result": FAIL, "parts": parts, "detail": detail}
    # 期間は run 区間に切り詰めない。run 区間の中に無ければ (b) は不合格（第 3 巡）。
    periods = _merge(targets)
    detail["target_rows"] = len(targets)
    detail["target_series"] = sorted({row["series"] for _, _, row in chosen})
    detail["periods"] = [[_iso(s), _iso(e)] for s, e in periods]
    inside = all(run_start <= s and e <= run_end for s, e in periods)
    parts["b_reset_rows"] = PASS if inside else FAIL

    # 決定 31: 期間ごとの準備のやり直しの分数は評価の集計に無く、表22 は戦略が読むすべての系列の
    # 無記録の足から出るので、run 全体の分数では 2022-12-26 の期間が採点に含まれるかを判定できない。
    counts = run.unrecorded_event_counts
    parts["c_scored"] = UNVERIFIABLE
    detail["history_resets_match_manifest"] = (
        counts.get("HISTORY_RESETS") == run.history_reset_count
    )
    detail["history_reset_minutes_total"] = counts.get("HISTORY_RESET_MINUTES")
    detail["c_unverifiable_reason"] = (
        "期間ごとの準備のやり直しの分数が評価の集計に無く、run 全体の分数では 2022-12-26 の期間を"
        "判定できない（D12 §12.5.1、決定 31）。根拠: tests/acceptance/test_tick_record_model.py::"
        "test_a_run_with_an_all_unrecorded_hour_is_evaluated"
    )

    ledger_times = [_time(row["at_time"]) for row in run.tables["LEDGER_SNAPSHOTS"]]
    parts["d_ledger_continues"] = (
        PASS if periods and all(any(s <= t < e for t in ledger_times) for s, e in periods) else FAIL
    )

    continuation = [_continuation(run, start) for start, _ in periods]
    parts["e_positions_orders_continue"] = _combine(item["result"] for item in continuation)
    detail["continuing_positions"] = sum(item["positions"] for item in continuation)
    detail["continuing_orders"] = sum(item["orders"] for item in continuation)
    detail["ended_at_period_start"] = sum(item["ended"] for item in continuation)
    detail["canceled_or_expired_at_period_start"] = sum(
        item["canceled_or_expired"] for item in continuation
    )
    detail["positions_with_unknown_close_cause"] = sum(item["unknown"] for item in continuation)
    detail["close_orders_judged_by_positions"] = sum(item["close_orders"] for item in continuation)
    detail["filled_orders_at_period_start"] = sum(item["filled"] for item in continuation)
    detail["orders_with_unknown_end"] = sum(item["unknown_orders"] for item in continuation)
    if parts["e_positions_orders_continue"] == UNVERIFIABLE:
        detail["unverifiable_reason"] = (
            "やり直しの期間の始まりをまたぐ建玉・注文が無いか、始まりの時刻の決済が強制決済か"
            "どうかを記録から区別できない。根拠: tests/acceptance/"
            "test_tick_record_model_marketdata.py::test_c2_the_run_records_history_resets_and_keeps_going"
        )
    return {"result": _combine(parts.values()), "parts": parts, "detail": detail}


def _continuation(run: RunArtifacts, start: datetime) -> dict[str, Any]:
    """期間の始まり `start` をまたぐ建玉・注文と、始まりの時刻での終了の有無（判定 1 (e)）。

    D12 §12.5.1（決定 14・17・24・25・28）: 期間の始まりの時刻に、取消・期限切れ以外の終了の出来事
    （強制決済など）が無いこと。「消された」に数えるのは、システムによる強制決済（決済の契機
    `EMERGENCY`。決定 28）だけ。同じ出来事を二重に数えない。

    - 建玉: 始まりより前に開き、始まりの時点で閉じていない建玉を数える。始まりの時刻の決済のうち
      強制決済だけを「消された」に数え、通常の規則による決済（保護水準・戦略の決済）は継続に数える。
      決済の契機・決済の約定を記録から読めなければ、その建玉は確認不能（決定 24）。
    - 注文: 決済の注文（`CLOSE_TERMS`）は建玉の側で判定し、注文の側では数えない（強制決済を
      二重に数えない。決定 24・28 を注文にも当てる）。それ以外の注文は、始まりより前に受け付け、
      始まりの時点で終わっていないものを数える。始まりの時刻の約定（決定 29）と取消・期限切れ
      （決定 25）は、継続にも終了にも数えず件数だけを出す（約定で生じた建玉は建玉の側で判定する）。
      それ以外の状態で終わった注文は確認不能（建玉の「契機が読めない」件数とは分けて数える）。
    """
    fills = {row["fill_id"]: row for row in run.tables["FILLS"]}
    causes = {
        row["order_id"]: row.get("terms_cause")
        for row in run.tables["ORDERS"]
        if row.get("terms_kind") == "CLOSE_TERMS"
    }
    positions = ended = unknown = 0
    for row in run.tables["POSITIONS"]:
        opened = _time(row["opened_at_time"])
        close_id = row.get("close_fill_id")
        close_fill = fills.get(close_id) if close_id else None
        if (close_id and close_fill is None) or (row.get("status") == "CLOSED" and not close_id):
            # 決済の約定が読めない建玉は、閉じた時刻が分からないので継続に数えない（確認不能）。
            if opened < start:
                unknown += 1
            continue
        closed = None if close_fill is None else _time(close_fill["processed_at_time"])
        if not (opened < start and (closed is None or closed >= start)):
            continue
        if closed == start and close_fill is not None:
            cause = causes.get(close_fill.get("order_id") or "")
            if cause in FORCED_CLOSE_CAUSES:
                ended += 1
                continue
            if cause not in NORMAL_CLOSE_CAUSES:
                unknown += 1
                continue
        positions += 1
    events: dict[str, list[tuple[datetime, str]]] = {}
    for row in run.tables["ORDER_EVENTS"]:
        events.setdefault(row["order_id"], []).append((_time(row["at_time"]), row["to_status"]))
    orders = canceled_or_expired = close_orders = filled = unknown_orders = 0
    for row in run.tables["ORDERS"]:
        accepted = _time(row["accepted_at_time"])
        history = sorted(events.get(row["order_id"], []))
        terminal = [(at, status) for at, status in history if status != "PENDING"]
        finished = terminal[0] if terminal else None
        if not (accepted < start and (finished is None or finished[0] >= start)):
            continue
        if row.get("terms_kind") == "CLOSE_TERMS":
            close_orders += 1
            continue
        if finished is not None and finished[0] == start:
            if finished[1] in _ALLOWED_ENDS:
                canceled_or_expired += 1
                continue
            if finished[1] == "FILLED":
                filled += 1
            else:
                unknown_orders += 1
            continue
        orders += 1
    if ended:
        result = FAIL
    elif unknown or unknown_orders or not (positions or orders):
        result = UNVERIFIABLE
    else:
        result = PASS
    return {
        "result": result,
        "positions": positions,
        "orders": orders,
        "ended": ended,
        "canceled_or_expired": canceled_or_expired,
        "unknown": unknown,
        "close_orders": close_orders,
        "filled": filled,
        "unknown_orders": unknown_orders,
    }


# --- 判定 2 -----------------------------------------------------------------------


def _initial_readiness(run: RunArtifacts) -> datetime | None:
    """データの開始の準備が整う時刻（D12 §12.5.1 の決定 2）。窓が分からなければ `None`。"""
    windows = set()
    for row in run.tables["HISTORY_RESETS"]:
        kind = row["window_kind"]
        if kind == "BARS":
            windows.add(
                (row["series"], int(row["window_bars"]) + int(row["exclude_latest_bars"] or 0))
            )
        elif kind == "LATEST":
            windows.add((row["series"], 1))
        else:  # 経過時間の窓は本数に換算しない（D12-127）。確認不能にする（D12 §12.5.1、決定 18）。
            return None
    if not windows:
        return None
    published: dict[str, list[datetime]] = {}
    for row in run.tables["PUBLICATIONS"]:
        published.setdefault(row["bar_key_series"], []).append(_time(row["available_at"]))
    latest: datetime | None = None
    for series, count in windows:
        times = sorted(published.get(series, []))
        if len(times) < count:
            return None
        moment = times[count - 1]
        latest = moment if latest is None or moment > latest else latest
    return latest


def check_skips_match_resets(run: RunArtifacts) -> dict[str, Any]:
    """判定 2: 記録された「準備不足で見送り」と表22 の期間の一致。

    時刻だけで結び付ける（D12 §12.5.1 の決定 3）。
    """
    run_start, run_end = run.run_interval
    skips = [
        _time(row["decision_time"]) for row in run.tables["EVALUATIONS"] if _is_warmup_skip(row)
    ]
    readiness = _initial_readiness(run)
    periods = _reset_periods(run)
    detail: dict[str, Any] = {
        "warmup_skips": len(skips),
        "table22_rows": len(periods),
        "initial_readiness_at": None if readiness is None else _iso(readiness),
    }
    parts: dict[str, str] = {}
    if readiness is None and skips:
        # 窓が表22 から分からない（行が無い・経過時間の窓がある・公開が窓の本数に満たない）。
        # (a) だけを確認不能にし、(b) は期間の中の見送りをすべて数える（D12 §12.5.1、決定 18・21）。
        parts["a_skips_inside_periods"] = UNVERIFIABLE
        detail["unverifiable_reason"] = (
            "窓が表22 から分からず、データの開始の準備の範囲を決められない"
        )
        considered = skips
    else:
        considered = [t for t in skips if readiness is None or t >= readiness]
        outside = [t for t in considered if not any(s <= t < e for s, e, _ in periods)]
        detail["considered_skips"] = len(considered)
        detail["excluded_initial_skips"] = len(skips) - len(considered)
        detail["skips_outside_periods"] = len(outside)
        parts["a_skips_inside_periods"] = FAIL if outside else PASS

    publications: dict[str, list[datetime]] = {}
    for row in run.tables["PUBLICATIONS"]:
        publications.setdefault(row["bar_key_series"], []).append(_time(row["available_at"]))
    with_skip = without_trigger = missing = outside_run = 0
    for start, stop, row in periods:
        start, stop = max(start, run_start), min(stop, run_end)
        if start >= stop:
            outside_run += 1
            continue
        # (b) はデータの開始の準備の除外を当てず、見送りをすべて数える（除外は (a) だけ。決定 11）。
        if any(start <= t < stop for t in skips):
            with_skip += 1
        elif not any(start <= t < stop for t in publications.get(row["series"], [])):
            without_trigger += 1
        else:
            missing += 1
    detail.update(
        rows_with_skip=with_skip,
        rows_without_trigger=without_trigger,
        rows_missing_skip=missing,
        rows_outside_run=outside_run,
    )
    if missing:
        parts["b_each_period_has_skip"] = FAIL
    elif without_trigger:
        parts["b_each_period_has_skip"] = UNVERIFIABLE
        detail.setdefault(
            "unverifiable_reason",
            "期間の中にその系列の足の公開が無い行がある。根拠: tests/acceptance/"
            "test_tick_record_model_marketdata.py::test_history_resets_match_the_runtime_reads",
        )
    else:
        parts["b_each_period_has_skip"] = PASS
    return {"result": _combine(parts.values()), "parts": parts, "detail": detail}


# --- 判定 3 -----------------------------------------------------------------------


def check_integrity(run: RunArtifacts) -> dict[str, Any]:
    """判定 3: 評価の整合検査・manifest の突き合わせ・再現の判定（D07 §10.2・§21.1）。"""
    parts: dict[str, str] = {}
    evaluation = run.evaluation or {}
    failed = [row for row in run.consistency if row.get("outcome") != _PASSED]
    parts["a_consistency_checks"] = (
        PASS
        if evaluation
        and evaluation.get("status") == _COMPLETED
        and run.consistency
        and not failed
        and not evaluation.get("fatal_failure_count")
        and not evaluation.get("warning_failure_count")
        and not evaluation.get("unreadable_check_count")
        else FAIL
    )
    outcome = run.outcome or {}
    # 決定 26: 評価 manifest とは run_id と時刻の規則の表の参照を照合する。snapshot の識別子は
    # run manifest・実験の記録票・snapshot の manifest の間で照合する。読めなければ不合格。
    matches = (
        evaluation.get("run_id") == run.run_id
        and outcome.get("run_id") == run.run_id
        and outcome.get("result_digest") == evaluation.get("result_digest")
        and evaluation.get("time_rule_table_ref") == run.time_rule_table_ref
        and run.experiment_snapshot_id == run.snapshot_id
        and run.snapshot_manifest_id == run.snapshot_id
        and run.snapshot_time_rule_table is not None
        and dict(run.snapshot_time_rule_table) == dict(run.time_rule_table_ref)
    )
    parts["b_manifest_matches"] = PASS if matches else FAIL
    if run.reproduction is None:
        parts["c_reproduced"] = NOT_EVALUATED
    else:
        reproduced = (
            run.reproduction.get("verdict") == _REPRODUCED
            and run.reproduction.get("observed_run_id") == run.run_id
            and run.reproduction.get("observed_result_digest") == outcome.get("result_digest")
        )
        parts["c_reproduced"] = PASS if reproduced else FAIL
    evaluated = [value for value in parts.values() if value != NOT_EVALUATED]
    result = _combine(evaluated)
    if result == PASS and len(evaluated) < len(parts):
        result = NOT_EVALUATED
    return {
        "result": result,
        "parts": parts,
        "detail": {
            "consistency_checks": len(run.consistency),
            "consistency_failed": len(failed),
            "run_id": run.run_id,
            "config_digest": run.config_digest,
            "result_digest": evaluation.get("result_digest"),
            "snapshot_id": run.snapshot_id,
            "time_rule_variant": run.time_rule_table_ref.get("variant"),
        },
    }


# --- 判定 4 -----------------------------------------------------------------------


def trading_day_label(bar_end: datetime, tz: tzinfo) -> str:
    """日足の取引日（D12 §12.5.1、決定 27）。

    区間の終わりの直前（1 マイクロ秒前）のカレンダーの時間帯の日付。順 2 の集計
    （`tools/survey/full_period_feasibility.py` の `trading_days`）の取引日と同じ規則で、
    どちらも `fx_ny17_oanda` 版 1 の取引日を指す。
    """
    return (bar_end.astimezone(tz) - timedelta(microseconds=1)).date().isoformat()


def read_daily_differences(path: Path, pair: str = "USDJPY") -> set[str]:
    """順 2 の集計の、組の間で被覆が変わる日足の取引日（`candidate_differences.csv`）。"""
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            row["key"]
            for row in csv.DictReader(handle)
            if row["pair"] == pair
            and row["scope"] == "daily_bar"
            and row["field"] == "recorded_minutes"
        }


def check_daily_differences(
    standard: RunArtifacts, candidate: RunArtifacts, expected_days: set[str], tz: tzinfo
) -> dict[str, Any]:
    """判定 4: 戦略 B の日足の `recorded_minutes` が違う取引日と、順 2 の集計の取引日の一致。"""

    def daily(run: RunArtifacts) -> dict[str, int]:
        return {
            trading_day_label(_time(row["bar_end"]), tz): int(row[COVERAGE_COLUMN])
            for row in run.tables["PUBLICATIONS"]
            if row["bar_key_series"] == DAILY_SERIES
        }

    left, right = daily(standard), daily(candidate)
    same_keys = set(left) == set(right)
    differing = {day for day in set(left) & set(right) if left[day] != right[day]}
    not_published = {day for day in expected_days if day not in left or day not in right}
    parts = {"a_same_bars": PASS if same_keys else FAIL}
    if differing - expected_days or (expected_days - not_published) - differing:
        parts["b_matches_aggregation"] = FAIL
    elif not_published:
        parts["b_matches_aggregation"] = UNVERIFIABLE
    else:
        parts["b_matches_aggregation"] = PASS
    return {
        "result": _combine(parts.values()),
        "parts": parts,
        "detail": {
            "daily_bars_standard": len(left),
            "daily_bars_candidate": len(right),
            "differing_days": len(differing),
            "expected_days": len(expected_days),
            "matched_days": len(differing & expected_days),
            "unexpected_days": sorted(differing - expected_days),
            "missing_days": sorted((expected_days - not_published) - differing),
            "not_published_days": sorted(not_published),
        },
    }


# --- 入口 -------------------------------------------------------------------------


def _pairs(values: Sequence[str] | None) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for value in values or ():
        name, _, path = value.partition("=")
        if not name or not path:
            raise SystemExit(f"NAME=PATH の形で渡すこと: {value!r}")
        out[name] = Path(path)
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument(
        "--experiment", action="append", required=True, help="NAME=実験の版のディレクトリ"
    )
    parser.add_argument(
        "--reproduction", action="append", help="NAME=再現の出力の基点（reproduction.json の場所）"
    )
    parser.add_argument(
        "--pair-b", nargs=2, metavar=("STANDARD", "CANDIDATE"), help="判定 4 の戦略 B の 2 本"
    )
    parser.add_argument("--differences", help="順 2 の集計 candidate_differences.csv")
    parser.add_argument(
        "--snapshots", required=True, help="snapshot の基点（判定 3 の snapshot の照合に使う）"
    )
    parser.add_argument(
        "--calendar",
        help="判定 4 の取引日を決めるカレンダー（fx_ny17_oanda 版 1。D12 §12.5.1、決定 27）",
    )
    parser.add_argument("--out", required=True, help="結果の JSON の書き出し先")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    experiments = _pairs(args.experiment)
    reproductions = _pairs(args.reproduction)
    snapshots = Path(args.snapshots)
    runs = {
        name: load_run(name, path, reproduction_dir=reproductions.get(name), snapshots=snapshots)
        for name, path in experiments.items()
    }
    results: dict[str, Any] = {}
    for name, artifacts in runs.items():
        results[name] = {
            "run_id": artifacts.run_id,
            "snapshot_id": artifacts.snapshot_id,
            "run_interval": [_iso(artifacts.run_interval[0]), _iso(artifacts.run_interval[1])],
            "check_1_history_reset_scoring": check_history_reset_scoring(artifacts),
            "check_2_skips_match_resets": check_skips_match_resets(artifacts),
            "check_3_integrity": check_integrity(artifacts),
        }
    check_4: dict[str, Any] = {"result": NOT_EVALUATED}
    if args.pair_b and args.differences:
        if not args.calendar:  # 判定 4 を出すときだけ要る（既定値は置かない）
            raise SystemExit("判定 4 を出すには --calendar（fx_ny17_oanda 版 1）を渡すこと")
        standard, candidate = (runs[name] for name in args.pair_b)
        calendar_path = Path(args.calendar)
        check_4 = check_daily_differences(
            standard,
            candidate,
            read_daily_differences(Path(args.differences)),
            load_calendar(calendar_path).tz,
        )
        check_4["calendar_sha256"] = _sha256(calendar_path)
        check_4["differences_sha256"] = _sha256(Path(args.differences))
    record = {
        "tool": "tools/survey/mt5_single_run_checks.py",
        "tool_sha256": _sha256(Path(__file__)),
        "runs": results,
        "check_4_daily_differences": check_4,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def main(argv: Sequence[str] | None = None) -> int:
    record = run(build_parser().parse_args(argv))
    summary = {
        name: {key: value["result"] for key, value in item.items() if key.startswith("check_")}
        for name, item in record["runs"].items()
    }
    summary["check_4_daily_differences"] = record["check_4_daily_differences"]["result"]
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
