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
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
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


#: 16 進 64 文字のダイジェスト（run_id・result_digest・snapshot の識別子など）。
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
#: 時刻の規則の表の変換の組（D03 §15.9 の 6）。
_VARIANTS: Final = frozenset({"standard", "candidate"})
#: 判定が成り立つには壊れていてはならない共通の入力（結末記録・run manifest）。
_COMMON_SOURCES: Final = ("outcome", "manifest")
#: 判定ごとに要る成果物（D12 §12.5.1 の共通の約束: 読めなければその判定は不合格）。
REQUIRED_SOURCES: Final[Mapping[str, tuple[str, ...]]] = {
    "check_1": (
        *_COMMON_SOURCES,
        "table:HISTORY_RESETS",
        "table:LEDGER_SNAPSHOTS",
        "table:POSITIONS",
        "table:FILLS",
        "table:ORDERS",
        "table:ORDER_EVENTS",
        "category_counts",
    ),
    "check_2": (
        *_COMMON_SOURCES,
        "table:EVALUATIONS",
        "table:HISTORY_RESETS",
        "table:PUBLICATIONS",
    ),
    "check_3": (
        *_COMMON_SOURCES,
        "evaluation",
        "consistency",
        "experiment_manifest",
        "snapshot_manifest",
        "reproduction",
    ),
    "check_4": (*_COMMON_SOURCES, "table:PUBLICATIONS"),
}
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _is_table_ref(value: object) -> bool:
    """時刻の規則の表の参照の形（識別・版・SHA-256・変換の組。D06 §18.10）。"""
    return (
        isinstance(value, Mapping)
        and _is_hex64(value.get("sha256"))
        and isinstance(value.get("table_id"), str)
        and bool(value.get("table_id"))
        and isinstance(value.get("version"), int)
        and not isinstance(value.get("version"), bool)
        and value.get("variant") in _VARIANTS
    )


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class RunArtifacts:
    """1 本の run の成果物のうち、判定に使う部分（価格を含まない）。

    読めなかった成果物は `errors`（成果物の名前 → 理由）に残し、例外で止めない。その成果物を要る
    判定は不合格になる（D12 §12.5.1 の共通の約束）。
    """

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
    errors: Mapping[str, str] = field(default_factory=dict)


#: 読込みで起きうる失敗（ファイル無し・JSON 不正・列不足・型の誤り）。
_READ_ERRORS: Final = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    pl.exceptions.PolarsError,
)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} が JSON の対象（辞書）でない")
    return payload


def _read_table(path: Path, columns: Sequence[str]) -> list[dict[str, Any]]:
    return pl.read_parquet(path, columns=list(columns)).to_dicts()


def load_run(
    name: str,
    experiment_dir: Path,
    *,
    reproduction_dir: Path | None = None,
    snapshots: Path,
) -> RunArtifacts:
    """実験の版のディレクトリから run・評価・結末記録・再現の判定を読む（価格の列は読まない）。

    読めない成果物は例外で止めず、`errors` に理由を残す。
    """
    errors: dict[str, str] = {}
    outcome: dict[str, Any] | None = None
    run_dir: Path | None = None
    try:
        outcome = _read_json(experiment_dir / "experiment_outcome.json")
        if not _is_hex64(outcome.get("run_id")):
            raise ValueError("結末記録の run_id が 16 進 64 文字でない")
        run_dir = experiment_dir.parents[2] / str(outcome["run_id"])
    except _READ_ERRORS as exc:
        errors["outcome"] = _describe(exc)

    run_id = status = config_digest = snapshot_id = ""
    run_interval = (_EPOCH, _EPOCH)
    table_ref: Mapping[str, Any] = {}
    reset_count = 0
    if run_dir is None:
        errors["manifest"] = "結末記録が読めず run の場所が分からない"
    else:
        try:
            manifest = _read_json(run_dir / "manifest.json")
            raw = manifest["config"]
            config = json.loads(raw) if isinstance(raw, str) else raw
            interval = config["run_interval"]
            run_interval = (_time(interval["start"]), _time(interval["end"]))
            run_id, status = manifest["run_id"], manifest["status"]
            config_digest = manifest["config_digest"]
            snapshot_id = config["snapshot_ref"]["snapshot_id"]
            table_ref = manifest["time_rule_table_ref"]
            reset_count = manifest["resumption_counts"]["history_resets"]
            invalid = [
                label
                for label, ok in (
                    ("run_id", _is_hex64(run_id)),
                    ("status", isinstance(status, str) and bool(status)),
                    ("config_digest", _is_hex64(config_digest)),
                    ("snapshot_id", _is_hex64(snapshot_id)),
                    ("time_rule_table_ref", _is_table_ref(table_ref)),
                    ("resumption_counts.history_resets", _is_count(reset_count)),
                    ("run_interval", run_interval[0] < run_interval[1]),
                )
                if not ok
            ]
            if invalid:
                raise ValueError("run manifest の項目の形式が違う: " + ", ".join(invalid))
        except _READ_ERRORS as exc:
            errors["manifest"] = _describe(exc)

    tables: dict[str, list[dict[str, Any]]] = {}
    for table, columns in TRACE_COLUMNS.items():
        try:
            if run_dir is None:
                raise FileNotFoundError("run の場所が分からない")
            tables[table] = _read_table(run_dir / f"{table}.parquet", columns)
        except _READ_ERRORS as exc:
            errors[f"table:{table}"] = _describe(exc)
            tables[table] = []

    evaluation: dict[str, Any] | None = None
    consistency: list[dict[str, Any]] = []
    unrecorded: dict[str, int] = {}
    evaluation_id = None if outcome is None else outcome.get("run_evaluation_id")
    eval_dir = None
    if run_dir is None or not isinstance(evaluation_id, str) or not evaluation_id:
        reason = "結末記録に評価の識別子が無い"
        for key in ("evaluation", "consistency", "category_counts"):
            errors[key] = reason
    else:
        eval_dir = run_dir / "eval" / evaluation_id
        try:
            evaluation = _read_json(eval_dir / "evaluation.json")
        except _READ_ERRORS as exc:
            errors["evaluation"] = _describe(exc)
        try:
            consistency = _read_table(eval_dir / "CONSISTENCY_CHECKS.parquet", CONSISTENCY_COLUMNS)
        except _READ_ERRORS as exc:
            errors["consistency"] = _describe(exc)
        try:
            counts = (
                pl.scan_parquet(eval_dir / "CATEGORY_COUNTS.parquet")
                .select("category", "key", "count")
                .filter(pl.col("category") == "UNRECORDED_EVENT")
                .collect()
            )
            unrecorded = {str(row["key"]): int(row["count"]) for row in counts.to_dicts()}
        except _READ_ERRORS as exc:
            errors["category_counts"] = _describe(exc)

    reproduction = None
    if reproduction_dir is not None:
        try:
            reproduction = _read_json(reproduction_dir / "reproduction.json")
        except _READ_ERRORS as exc:
            errors["reproduction"] = _describe(exc)

    experiment_snapshot = None
    try:
        experiment_snapshot = _read_json(experiment_dir / "experiment_manifest.json")["snapshot_id"]
    except _READ_ERRORS as exc:
        errors["experiment_manifest"] = _describe(exc)

    snapshot_manifest_id = None
    snapshot_table = None
    try:
        if not _is_hex64(snapshot_id):
            raise ValueError("run manifest の snapshot の識別子が読めない")
        snapshot_manifest = _read_json(snapshots / snapshot_id / "manifest.json")
        snapshot_manifest_id = snapshot_manifest["snapshot_id"]
        snapshot_table = snapshot_manifest["conversion"]["mt5"]["time_rule_table"]
    except _READ_ERRORS as exc:
        errors["snapshot_manifest"] = _describe(exc)

    return RunArtifacts(
        name=name,
        run_id=str(run_id),
        status=str(status),
        config_digest=str(config_digest),
        snapshot_id=str(snapshot_id),
        run_interval=run_interval,
        time_rule_table_ref=table_ref if isinstance(table_ref, Mapping) else {},
        history_reset_count=reset_count if _is_count(reset_count) else 0,
        tables=tables,
        evaluation=evaluation,
        consistency=consistency,
        unrecorded_event_counts=unrecorded,
        outcome=outcome,
        reproduction=reproduction,
        experiment_snapshot_id=None if experiment_snapshot is None else str(experiment_snapshot),
        snapshot_manifest_id=None if snapshot_manifest_id is None else str(snapshot_manifest_id),
        snapshot_time_rule_table=snapshot_table,
        errors=errors,
    )


def _guarded(
    check: str, runs: Sequence[RunArtifacts], body: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """判定の前に要る成果物が読めたかを確かめ、判定の中の形式の誤りも不合格として記録する。

    D12 §12.5.1 の共通の約束（v0.37）: 必須項目の存在・形式を確かめてから比べ、読めなければ不合格。
    例外で止めず、残りの判定を続ける。
    """
    failures = {
        f"{run.name}:{source}": run.errors[source]
        for run in runs
        for source in REQUIRED_SOURCES[check]
        if source in run.errors
    }
    if failures:
        return {"result": FAIL, "parts": {}, "detail": {"read_errors": failures}}
    try:
        return body()
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return {"result": FAIL, "parts": {}, "detail": {"invalid_values": _describe(exc)}}


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


def _check_history_reset_scoring(
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


def check_history_reset_scoring(
    run: RunArtifacts, reset_day: tuple[str, str] = RESET_DAY
) -> dict[str, Any]:
    """判定 1（読めない成果物・形式の誤りは不合格。`_guarded`）。"""
    return _guarded("check_1", [run], lambda: _check_history_reset_scoring(run, reset_day))


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


def _check_skips_match_resets(run: RunArtifacts) -> dict[str, Any]:
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


def check_skips_match_resets(run: RunArtifacts) -> dict[str, Any]:
    """判定 2（読めない成果物・形式の誤りは不合格。`_guarded`）。"""
    return _guarded("check_2", [run], lambda: _check_skips_match_resets(run))


# --- 判定 3 -----------------------------------------------------------------------


def check_integrity(run: RunArtifacts) -> dict[str, Any]:
    """判定 3（読めない成果物は不合格。`_guarded`）。再現の判定を渡さなければ (c) は未判定。"""
    required = REQUIRED_SOURCES["check_3"]
    if run.reproduction is None and "reproduction" not in run.errors:
        required = tuple(source for source in required if source != "reproduction")
    failures = {f"{run.name}:{s}": run.errors[s] for s in required if s in run.errors}
    if failures:
        return {"result": FAIL, "parts": {}, "detail": {"read_errors": failures}}
    try:
        return _check_integrity(run)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return {"result": FAIL, "parts": {}, "detail": {"invalid_values": _describe(exc)}}


def _check_integrity(run: RunArtifacts) -> dict[str, Any]:
    """判定 3: 評価の整合検査・manifest の突き合わせ・再現の判定（D07 §10.2・§21.1）。

    比べる前に必須項目の存在と形式（16 進 64 文字のダイジェスト・時刻の規則の表の参照の形・件数）を
    確かめる。欠けた項目・形式の違う項目は、その部分の不合格として名前を記録する（None 同士を一致と
    扱わない。D12 §12.5.1 の共通の約束 v0.37）。
    """
    parts: dict[str, str] = {}
    evaluation = run.evaluation or {}
    outcome = run.outcome or {}
    invalid: list[str] = []

    def need(label: str, ok: bool) -> bool:
        if not ok:
            invalid.append(label)
        return ok

    failed = [row for row in run.consistency if row.get("outcome") != _PASSED]
    a_ok = all(
        [
            need("evaluation.status", evaluation.get("status") == _COMPLETED),
            need("consistency_checks", bool(run.consistency)),
            need(
                "evaluation.fatal_failure_count", _is_count(evaluation.get("fatal_failure_count"))
            ),
            need(
                "evaluation.warning_failure_count",
                _is_count(evaluation.get("warning_failure_count")),
            ),
            need(
                "evaluation.unreadable_check_count",
                _is_count(evaluation.get("unreadable_check_count")),
            ),
        ]
    )
    counts_zero = all(
        evaluation.get(key) == 0
        for key in ("fatal_failure_count", "warning_failure_count", "unreadable_check_count")
    )
    parts["a_consistency_checks"] = PASS if a_ok and not failed and counts_zero else FAIL

    # 決定 26: 評価 manifest とは run_id と時刻の規則の表の参照を照合する。snapshot の識別子は
    # run manifest・実験の記録票・snapshot の manifest の間で照合する。読めなければ不合格。
    b_ok = all(
        [
            need("run_manifest.run_id", _is_hex64(run.run_id)),
            need("evaluation.run_id", _is_hex64(evaluation.get("run_id"))),
            need("outcome.run_id", _is_hex64(outcome.get("run_id"))),
            need("outcome.result_digest", _is_hex64(outcome.get("result_digest"))),
            need("evaluation.result_digest", _is_hex64(evaluation.get("result_digest"))),
            need("run_manifest.time_rule_table_ref", _is_table_ref(run.time_rule_table_ref)),
            need(
                "evaluation.time_rule_table_ref",
                _is_table_ref(evaluation.get("time_rule_table_ref")),
            ),
            need("run_manifest.snapshot_id", _is_hex64(run.snapshot_id)),
            need("experiment_manifest.snapshot_id", _is_hex64(run.experiment_snapshot_id)),
            need("snapshot_manifest.snapshot_id", _is_hex64(run.snapshot_manifest_id)),
            need("snapshot_manifest.time_rule_table", _is_table_ref(run.snapshot_time_rule_table)),
        ]
    )
    matches = b_ok and (
        evaluation["run_id"] == run.run_id
        and outcome["run_id"] == run.run_id
        and outcome["result_digest"] == evaluation["result_digest"]
        and dict(evaluation["time_rule_table_ref"]) == dict(run.time_rule_table_ref)
        and run.experiment_snapshot_id == run.snapshot_id
        and run.snapshot_manifest_id == run.snapshot_id
        and dict(run.snapshot_time_rule_table or {}) == dict(run.time_rule_table_ref)
    )
    parts["b_manifest_matches"] = PASS if matches else FAIL

    if run.reproduction is None:
        parts["c_reproduced"] = NOT_EVALUATED
    else:
        reproduction = run.reproduction
        c_ok = all(
            [
                need("reproduction.verdict", isinstance(reproduction.get("verdict"), str)),
                need(
                    "reproduction.observed_run_id", _is_hex64(reproduction.get("observed_run_id"))
                ),
                need(
                    "reproduction.observed_result_digest",
                    _is_hex64(reproduction.get("observed_result_digest")),
                ),
                need("outcome.result_digest", _is_hex64(outcome.get("result_digest"))),
            ]
        )
        reproduced = c_ok and (
            reproduction["verdict"] == _REPRODUCED
            and reproduction["observed_run_id"] == run.run_id
            and reproduction["observed_result_digest"] == outcome["result_digest"]
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
            "invalid_fields": sorted(set(invalid)),
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
    """判定 4（読めない成果物・形式の誤りは不合格。`_guarded`）。"""
    return _guarded(
        "check_4",
        [standard, candidate],
        lambda: _check_daily_differences(standard, candidate, expected_days, tz),
    )


def _check_daily_differences(
    standard: RunArtifacts, candidate: RunArtifacts, expected_days: set[str], tz: tzinfo
) -> dict[str, Any]:
    """判定 4: 戦略 B の日足の `recorded_minutes` が違う取引日と、順 2 の集計の取引日の一致。"""

    def daily(run: RunArtifacts) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in run.tables["PUBLICATIONS"]:
            if row["bar_key_series"] != DAILY_SERIES:
                continue
            minutes = row[COVERAGE_COLUMN]
            if not _is_count(minutes):  # 欠けた・形式の違う被覆は比べない（不合格）
                raise ValueError(f"{run.name} の日足の {COVERAGE_COLUMN} が欠けているか形式が違う")
            out[trading_day_label(_time(row["bar_end"]), tz)] = minutes
        return out

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
            "read_errors": dict(artifacts.errors),
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
        calendar_path, differences_path = Path(args.calendar), Path(args.differences)
        try:
            expected = read_daily_differences(differences_path)
            tz = load_calendar(calendar_path).tz
        except Exception as exc:  # 集計・カレンダーが読めなければ判定 4 は不合格（止めない）
            check_4 = {"result": FAIL, "parts": {}, "detail": {"read_errors": _describe(exc)}}
        else:
            check_4 = check_daily_differences(standard, candidate, expected, tz)
            check_4["calendar_sha256"] = _sha256(calendar_path)
            check_4["differences_sha256"] = _sha256(differences_path)
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
    # 読めない成果物があったときは、判定を書き終えたうえで 0 以外で終える。
    unreadable = any(item["read_errors"] for item in record["runs"].values()) or bool(
        record["check_4_daily_differences"].get("detail", {}).get("read_errors")
    )
    return 1 if unreadable else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
