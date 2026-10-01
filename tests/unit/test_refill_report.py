"""refill_report（補充の後の残存欠落と連続期間の報告。D03 v1.17 §14.15）の単体テスト。

人工の旧・新 snapshot の manifest、補充分の manifest と検証記録、作業ディレクトリの計画と取得
記録から、残存欠落の区間ごとの状態・履歴・休場の候補の印・根拠の計画と報告の本文を作る。実データ
には触れない。
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.common import canonical
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.classification import (
    ClassificationOutcome,
    ResolvedClassification,
)
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    PartitionId,
    PartitionRecord,
    SeriesManifest,
    SnapshotManifest,
)
from tests.fixtures.synthetic import snapshots as synthetic
from tools.ops import refill_report as rr
from tools.ops import research_history_gaps as rhg

WINDOW = Interval(
    start=UtcTime.parse("2016-01-01T00:00:00Z"), end=UtcTime.parse("2021-01-01T00:00:00Z")
)
APPROVAL = Approval(approved_by="human", approved_at=UtcTime.parse("2026-10-02T00:00:00Z"))


def _sid(symbol: str, tf: str) -> SeriesId:
    return SeriesId(symbol=Symbol(symbol), timeframe=TimeframeRef.parse(tf), basis=PriceBasis.BID)


def _record(symbol: str, tf: str, start: str, end: str) -> ResolvedClassification:
    return ResolvedClassification(
        kind=CheckKind.MISSING_EXPECTED_BAR,
        series_id=_sid(symbol, tf),
        interval=Interval(start=UtcTime.parse(start), end=UtcTime.parse(end)),
        outcome=ClassificationOutcome.DATA_GAP,
    )


def _snapshot(
    records: list[ResolvedClassification], refill_paths: list[str], *, approved: bool = True
) -> SnapshotManifest:
    """20 系列の研究履歴の partition と分類を持つ manifest（識別子は内容から決まる）。"""
    partitions = [
        PartitionRecord(
            partition_id=PartitionId(series=_sid(s, tf), access_class=AccessClass.RESEARCH_HISTORY),
            interval=WINDOW,
            bar_count=1,
            digest=synthetic.digest_for(f"{s}{tf}"),
        )
        for s in rhg.SYMBOLS
        for tf in rhg.TIMEFRAMES
    ]
    return synthetic.manifest(
        sources=[
            synthetic.source("data/raw/market/USDJPY_15m_merged.csv", "USDJPY", "15m"),
            *(synthetic.source(path, "USDJPY", "15m") for path in refill_paths),
        ],
        series_records=[
            SeriesManifest(
                series_id=record.series_id,
                covered_interval=WINDOW,
                bar_count=1,
                partitions=(record.partition_id,),
            )
            for record in partitions
        ],
        partitions=partitions,
        resolved_classifications=records,
        approval=APPROVAL if approved else None,
    )


# 旧: USDJPY 15m に 06-01 10:00〜10:45（3 本）と 07-01 の 1 本、AUDJPY 1h に 1 本、
#     USDJPY 1h に休場の候補 2（2017 年元日）の 9 本。
OLD_RECORDS = [
    _record("USDJPY", "15m@v1", "2020-06-01T10:00:00Z", "2020-06-01T10:15:00Z"),
    _record("USDJPY", "15m@v1", "2020-06-01T10:15:00Z", "2020-06-01T10:30:00Z"),
    _record("USDJPY", "15m@v1", "2020-06-01T10:30:00Z", "2020-06-01T10:45:00Z"),
    _record("USDJPY", "15m@v1", "2020-07-01T10:00:00Z", "2020-07-01T10:15:00Z"),
    _record("AUDJPY", "1h@v1", "2020-08-03T10:00:00Z", "2020-08-03T11:00:00Z"),
] + [
    _record("USDJPY", "1h@v1", f"2017-01-0{d}T{h:02d}:00:00Z", f"2017-01-0{d2}T{h2:02d}:00:00Z")
    for d, h, d2, h2 in (
        (1, 22, 1, 23),
        (1, 23, 2, 0),
        *((2, h, 2, h + 1) for h in range(7)),
    )
]
# 新: 06-01 10:00 は補充できた。10:15 は取得できず（計画 A）、10:30 はどの計画にも入って
#     いない。07-01 は未照合、AUDJPY は不合格の計画。元日は前半だけ補充できた（部分補充）。
NEW_RECORDS = [OLD_RECORDS[1], OLD_RECORDS[2], OLD_RECORDS[3], OLD_RECORDS[4]] + [
    _record("USDJPY", "1h@v1", f"2017-01-02T{h:02d}:00:00Z", f"2017-01-02T{h + 1:02d}:00:00Z")
    for h in range(3, 7)
]
OLD = str(_snapshot(OLD_RECORDS, []).snapshot_id())


def _target(series: str, start: str, end: str) -> dict[str, Any]:
    return {"series": series, "timeframe_version": 1, "start": start, "end": end}


def _line(entry: dict[str, Any]) -> str:
    """取得記録の 1 行（W3: 内容の正規化エンコードの sha256 を添える）。"""
    digest = hashlib.sha256(canonical.encode(entry)).hexdigest()
    return json.dumps({"digest": digest, "entry": entry}) + "\n"


def _failed(at: str, not_built: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "kind": "validation",
        "passed": False,
        "at": at,
        "reasons": ["1 reconciliation bar(s) differ from the raw data"],
        "details": {"not_built": not_built or [], "unreconciled": [], "mismatches": []},
    }


USDJPY_15M = "USDJPY/15m/bid"
TARGETS_A = [
    _target(USDJPY_15M, "2020-06-01T10:00:00Z", "2020-06-01T10:15:00Z"),
    _target(USDJPY_15M, "2020-06-01T10:15:00Z", "2020-06-01T10:30:00Z"),
    _target(USDJPY_15M, "2020-07-01T10:00:00Z", "2020-07-01T10:15:00Z"),
]
SERIES = {"series": USDJPY_15M, "timeframe_version": 1}
PLAN_A_PAYLOAD = {"snapshot_id": OLD, "target_bars": TARGETS_A}
#: 補充分 REFILL の計画（一度不合格になった後に取り直して書き出した）。識別子は中身から計算する。
PLAN_A = canonical.digest(PLAN_A_PAYLOAD).hex
REJECTED_PAYLOAD = {
    "snapshot_id": OLD,
    "target_bars": [_target("AUDJPY/1h/bid", "2020-08-03T10:00:00Z", "2020-08-03T11:00:00Z")],
}
REJECTED = canonical.digest(REJECTED_PAYLOAD).hex
VALIDATION = json.dumps(
    {
        "reconciled_count": 8,
        "matched_count": 8,
        "out_of_range_tick_count": 0,
        "bid_above_ask_tick_count": 0,
        "neighbors": [
            {
                **SERIES,
                "chunk_start": "2020-06-01T10:00:00Z",
                "side": "BEFORE",
                "difference_pips": "12.5",
                "needs_review": True,
            }
        ],
    }
).encode("utf-8")


def _refill_manifest() -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "plan_id": PLAN_A,
        "plan": PLAN_A_PAYLOAD,
        "snapshot_id": OLD,
        "aggregation_rule_version": "refill_ticks_v1",
        "code_version": "test",
        "hours": [
            {
                "hour": {"symbol": "USDJPY", "start": "2020-06-01T10:00:00Z"},
                "outcome": "NOT_FETCHED",
                "tick_digest": None,
            }
        ],
        "files": [{"name": "validation.json", "sha256": hashlib.sha256(VALIDATION).hexdigest()}],
        "created_at": "2026-10-03T00:00:00Z",
        "series_counts": [{**SERIES, "targets": 3, "built": 1, "not_built": 2}],
        "not_built": [
            {
                **SERIES,
                "start": "2020-06-01T10:15:00Z",
                "reason": "HOUR_NOT_FETCHED",
                "detail": "HTTP_404",
            },
            {**SERIES, "start": "2020-07-01T10:00:00Z", "reason": "UNRECONCILED", "detail": ""},
        ],
        "unreconciled": [{**SERIES, "chunk_start": "2020-07-01T10:00:00Z", "target_count": 1}],
    }
    manifest["refill_id"] = rr.refill_id_from(manifest)
    return manifest


REFILL = _refill_manifest()["refill_id"]
REFILL_PATH = f"data/raw/market/refill/{REFILL}/USDJPY_15m_refill.csv"
NEW = str(_snapshot(NEW_RECORDS, [REFILL_PATH]).snapshot_id())


def _write(tmp_path: Path, *, approved: bool = True) -> list[str]:
    snapshots = tmp_path / "snapshots"
    store = ParquetSnapshotStore(root=snapshots)
    store.write_manifest(OLD, _snapshot(OLD_RECORDS, []))
    store.write_manifest(NEW, _snapshot(NEW_RECORDS, [REFILL_PATH], approved=approved))
    refill = tmp_path / "refill" / REFILL
    refill.mkdir(parents=True)
    (refill / "refill_manifest.json").write_text(json.dumps(_refill_manifest()))
    (refill / "validation.json").write_bytes(VALIDATION)
    # 計画 A の作業ディレクトリ: 一度不合格になり、取り直して（最終結果の行）書き出した。
    work_a = tmp_path / "refill/_work" / PLAN_A
    work_a.mkdir(parents=True)
    (work_a / "plan.json").write_text(json.dumps(PLAN_A_PAYLOAD))
    (work_a / "journal.jsonl").write_text(
        _line(_failed("2026-10-02T00:00:00Z"))
        + _line({"kind": "final", "at": "2026-10-02T01:00:00Z"})
    )
    # 不合格のままの計画（補充分なし）。手で指定しなくても自動で集める。
    work = tmp_path / "refill/_work" / REJECTED
    work.mkdir(parents=True)
    (work / "plan.json").write_text(json.dumps(REJECTED_PAYLOAD))
    (work / "journal.jsonl").write_text(_line(_failed("2026-10-02T02:00:00Z")))
    # 関係しない snapshot を入力にした計画は集めない。
    other_plan = {
        "snapshot_id": "z" * 64,
        "target_bars": [_target(USDJPY_15M, "2020-06-01T10:30:00Z", "2020-06-01T10:45:00Z")],
    }
    other = tmp_path / "refill/_work" / canonical.digest(other_plan).hex
    other.mkdir(parents=True)
    (other / "plan.json").write_text(json.dumps(other_plan))
    return [
        "--snapshot-root",
        str(snapshots),
        "--snapshot-id",
        NEW,
        "--previous-snapshot-id",
        OLD,
        "--candidates-snapshot-id",
        OLD,
        "--refill-root",
        str(tmp_path / "refill"),
        "--out",
        str(tmp_path / "out"),
    ]


def _rows(path: Path) -> dict[tuple[str, str, str], dict[str, str]]:
    with path.open() as f:
        return {(r["symbol"], r["timeframe"], r["start_utc"]): r for r in csv.DictReader(f)}


def test_residual_gaps_carry_their_states_from_the_collected_records(tmp_path: Path) -> None:
    assert rr.main(_write(tmp_path)) == 0
    rows = _rows(tmp_path / "out/residual_gaps.csv")
    states = {key: row["states"] for key, row in rows.items()}
    assert states == {
        ("AUDJPY", "1h@v1", "2020-08-03T10:00Z"): rr.VALIDATION_REJECTED,
        ("USDJPY", "15m@v1", "2020-06-01T10:15Z"): f"{rr.NOT_FETCHED}|{rr.NOT_PLANNED}",
        ("USDJPY", "15m@v1", "2020-07-01T10:00Z"): rr.UNRECONCILED,
        ("USDJPY", "1h@v1", "2017-01-02T03:00Z"): rr.NOT_PLANNED,
    }
    audjpy = rows[("AUDJPY", "1h@v1", "2020-08-03T10:00Z")]
    assert audjpy["basis_plan_ids"] == REJECTED
    # 履歴は上書きしない: 計画 A の古い不合格の行も、書き出した補充分の結果も残る。
    mixed = rows[("USDJPY", "15m@v1", "2020-06-01T10:15Z")]
    assert mixed["basis_plan_ids"] == PLAN_A
    assert f"plan={PLAN_A} journal:1 {rr.VALIDATION_REJECTED}×1" in mixed["history"]
    assert f"plan={PLAN_A} refill:{REFILL} {rr.NOT_FETCHED}×1" in mixed["history"]
    assert mixed["history"].index("journal:1") < mixed["history"].index(f"refill:{REFILL}")


def test_each_state_has_its_own_count_column(tmp_path: Path) -> None:
    """(v) 複数の状態を含む区間を 1 つの理由として数えない（状態ごとの足の数の列）。"""
    assert rr.main(_write(tmp_path)) == 0
    mixed = _rows(tmp_path / "out/residual_gaps.csv")[("USDJPY", "15m@v1", "2020-06-01T10:15Z")]
    assert mixed["bar_count"] == "2"
    assert mixed[f"bars_{rr.NOT_FETCHED}"] == "1"
    assert mixed[f"bars_{rr.NOT_PLANNED}"] == "1"
    assert mixed[f"bars_{rr.VALIDATION_REJECTED}"] == "0"
    report = (tmp_path / "out/refill_report.md").read_text(encoding="utf-8")
    assert f"| {rr.STATE_LABELS[rr.NOT_FETCHED]}（`{rr.NOT_FETCHED}`） | 1 | 1 | 0.25h |" in report
    assert f"| {rr.STATE_LABELS[rr.NOT_PLANNED]}（`{rr.NOT_PLANNED}`） | 2 | 5 | 4.25h |" in report
    assert "- 複数の状態の足を含む区間: 1 / 区間の総数 4" in report


def test_a_partial_refill_keeps_the_holiday_candidate_mark(tmp_path: Path) -> None:
    """(ii) 候補の印は候補区間との重なりで付ける。部分補充で欠落が短くなっても消えない。"""
    assert rr.main(_write(tmp_path)) == 0
    rows = _rows(tmp_path / "out/residual_gaps.csv")
    shortened = rows[("USDJPY", "1h@v1", "2017-01-02T03:00Z")]
    assert shortened["holiday_candidate_pending"] == "True"
    assert shortened["holiday_candidates"] == "2"
    # 状態とは別の列: 状態は計画の記録から決まる（ここでは計画に入っていない）。
    assert shortened["states"] == rr.NOT_PLANNED
    assert rows[("AUDJPY", "1h@v1", "2020-08-03T10:00Z")]["holiday_candidate_pending"] == "False"
    # 短くなった区間だけを見ると、PR #55 の帰属の規則では候補 2 に当たらない。
    gap = rhg.GapInterval(
        "USDJPY",
        "1h@v1",
        rhg.parse_utc("2017-01-02T03:00:00Z"),
        rhg.parse_utc("2017-01-02T07:00:00Z"),
    )
    assert rr.candidate_number(gap) is None


def test_an_unapproved_snapshot_gives_only_a_draft(tmp_path: Path) -> None:
    """(iii) 承認されていない新 snapshot からの出力は「下書き」と明示する。"""
    assert rr.main(_write(tmp_path, approved=False)) == 0
    out = tmp_path / "out"
    assert sorted(path.name for path in out.iterdir()) == [
        "refill_report_draft.md",
        "residual_gaps_draft.csv",
    ]
    report = (out / "refill_report_draft.md").read_text(encoding="utf-8")
    assert report.startswith("# 【下書き】")
    assert "未承認（この出力は下書き）" in report


def test_an_unreadable_store_turns_not_planned_into_undetermined(tmp_path: Path) -> None:
    """(iv) 自動収集で網羅性を確かめられなければ「計画されていない」と言わない。"""
    args = _write(tmp_path)
    broken = tmp_path / "refill/_work" / ("e" * 64)
    broken.mkdir()
    (broken / "plan.json").write_text("{not json")
    assert rr.main(args) == 0
    rows = _rows(tmp_path / "out/residual_gaps.csv")
    assert rows[("USDJPY", "1h@v1", "2017-01-02T03:00Z")]["states"] == rr.UNDETERMINED
    assert rows[("USDJPY", "15m@v1", "2020-06-01T10:15Z")]["states"] == (
        f"{rr.NOT_FETCHED}|{rr.UNDETERMINED}"
    )
    # 記録のある足の状態は変わらない。
    assert rows[("AUDJPY", "1h@v1", "2020-08-03T10:00Z")]["states"] == rr.VALIDATION_REJECTED
    report = (tmp_path / "out/refill_report.md").read_text(encoding="utf-8")
    assert "網羅性を確かめられない" in report and str(broken) in report


def test_an_incomplete_refill_also_leaves_coverage_unknown(tmp_path: Path) -> None:
    args = _write(tmp_path)
    (tmp_path / "refill" / ("f" * 64)).mkdir()  # 完成の印の無い書きかけの補充分
    assert rr.main(args) == 0
    rows = _rows(tmp_path / "out/residual_gaps.csv")
    assert rows[("USDJPY", "1h@v1", "2017-01-02T03:00Z")]["states"] == rr.UNDETERMINED


# --- 前後関係と併記（(i)）----------------------------------------------------------------

KEY = ("USDJPY", "15m@v1", rhg.parse_utc("2020-06-01T10:15:00Z"))


def _state_record(plan_id: str, snapshot_id: str, result: str, refill_id: str | None) -> rr.Record:
    return rr.Record(
        plan_id=plan_id,
        input_snapshot=snapshot_id,
        source=f"refill:{refill_id}" if refill_id else "journal:1",
        refill_id=refill_id,
        recorded_at="",
        is_state=True,
        results={KEY: result},
    )


def _states(records: list[rr.Record], chain: dict[str, frozenset[str]]) -> rr.BarState:
    return rr.bar_states([KEY], rr.Collection(records, []), chain)[KEY]


def test_a_record_whose_refill_is_in_the_later_input_is_earlier() -> None:
    """補充分 A を含む snapshot から作った計画 B の記録が後（参照関係で確かめる）。"""
    first = _state_record("1" * 64, "s0", rr.NOT_FETCHED, "f" * 64)
    second = _state_record("2" * 64, "s1", rr.PROVIDER_NO_TICKS, "0" * 64)
    chain = {"s0": frozenset(), "s1": frozenset({"f" * 64})}
    for order in ([first, second], [second, first]):
        state = _states(order, chain)
        assert state == rr.BarState((rr.PROVIDER_NO_TICKS,), ("2" * 64,), False)


def test_records_that_cannot_be_ordered_are_shown_side_by_side() -> None:
    """(i) 同じ足を比較できない複数の計画が記録したら、1 つを選ばずに併記する。"""
    # 計画 X は補充分 2 つを含む snapshot から、計画 Y は補充分 1 つを含む snapshot から作った。
    # 補充分の数では X が後に見えるが、参照関係では比べられない。
    rejected = _state_record("9" * 64, "s1", rr.VALIDATION_REJECTED, None)
    refilled = _state_record("8" * 64, "s2", rr.NOT_FETCHED, "e" * 64)
    chain = {"s1": frozenset({"1" * 64}), "s2": frozenset({"2" * 64, "3" * 64})}
    for order in ([rejected, refilled], [refilled, rejected]):
        state = _states(order, chain)
        assert state.states == (rr.NOT_FETCHED, rr.VALIDATION_REJECTED)
        assert state.basis == ("8" * 64, "9" * 64)
        assert state.unordered


def test_a_refill_not_in_the_snapshot_is_not_hidden() -> None:
    """足を作ったのに新 snapshot に入っていない補充分は、状態を「理由未確定」にして示す。"""
    record = _state_record("7" * 64, "s0", rr.BUILT_NOT_IN_SNAPSHOT, "d" * 64)
    assert _states([record], {"s0": frozenset()}).states == (rr.UNDETERMINED,)


def test_a_bar_without_records_is_not_planned_only_when_coverage_is_known() -> None:
    complete = rr.bar_states([KEY], rr.Collection([], []), {})[KEY]
    unknown = rr.bar_states([KEY], rr.Collection([], ["読めない計画"]), {})[KEY]
    assert complete.states == (rr.NOT_PLANNED,)
    assert unknown.states == (rr.UNDETERMINED,)


def test_a_journal_line_whose_digest_does_not_match_is_not_used(tmp_path: Path) -> None:
    """取得記録の行のダイジェストを検算する（W3）。合わない行の記録を状態に使わない。"""
    args = _write(tmp_path)
    journal = tmp_path / "refill/_work" / REJECTED / "journal.jsonl"
    record = json.loads(journal.read_text(encoding="utf-8"))
    record["entry"]["reasons"] = ["altered"]  # 中身だけを書き換え、ダイジェストは古いまま
    journal.write_text(
        json.dumps(record) + "\n" + _line({"kind": "pause_end", "at": "2026-10-02T03:00:00Z"})
    )
    assert rr.main(args) == 0
    rows = _rows(tmp_path / "out/residual_gaps.csv")
    # 最後以外の行が壊れた取得記録は読めないので網羅性不明。その計画の足は「理由未確定」。
    assert rows[("AUDJPY", "1h@v1", "2020-08-03T10:00Z")]["states"] == rr.UNDETERMINED
    report = (tmp_path / "out/refill_report.md").read_text(encoding="utf-8")
    assert "the digest does not match the entry" in report


def test_a_refill_whose_identity_does_not_recompute_is_refused(tmp_path: Path) -> None:
    """補充分の識別子は記録から計算し直して確かめる（W5）。合わなければ何も書かずに止まる。"""
    args = _write(tmp_path)
    path = tmp_path / "refill" / REFILL / "refill_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["code_version"] = "something-else"
    path.write_text(json.dumps(payload))
    assert rr.main(args) == 1
    assert not (tmp_path / "out").exists()


def test_an_altered_snapshot_manifest_is_refused(tmp_path: Path) -> None:
    """snapshot の識別子は内容から計算し直す（D03 §3.7.1）。改変した manifest は使わない。"""
    args = _write(tmp_path)
    path = tmp_path / "snapshots" / NEW / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["resolved_classifications"] = payload["resolved_classifications"][1:]
    path.write_text(json.dumps(payload))
    assert rr.main(args) == 1
    assert not (tmp_path / "out").exists()


def test_a_not_built_record_outside_the_plan_is_refused(tmp_path: Path) -> None:
    """作らなかった足の記録が計画の対象足を指さなければ、その補充分を使わない（D03 §14.11）。"""
    args = _write(tmp_path)
    path = tmp_path / "refill" / REFILL / "refill_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["not_built"][0]["start"] = "2020-06-01T11:00:00Z"  # 識別子に入らない項目だけを変える
    path.write_text(json.dumps(payload))
    assert rr.main(args) == 1
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("stray", ["file", "link", "dir", "work_file"])
def test_anything_unexpected_in_the_store_leaves_coverage_unknown(
    tmp_path: Path, stray: str
) -> None:
    """補充分・_ticks・_work・計画以外のものを黙って飛ばさない（D03 v1.17 §14.15 の R4）。"""
    args = _write(tmp_path)
    root = tmp_path / "refill"
    if stray == "file":
        (root / ("9" * 64)).write_text("not a refill")
    elif stray == "link":
        (root / ("8" * 64)).symlink_to(root / REFILL)
    elif stray == "dir":
        (root / "_orphan").mkdir()
    else:
        (root / "_work" / "notes.txt").write_text("x")
    assert rr.main(args) == 0
    rows = _rows(tmp_path / "out/residual_gaps.csv")
    assert rows[("USDJPY", "1h@v1", "2017-01-02T03:00Z")]["states"] == rr.UNDETERMINED


def test_an_existing_report_is_never_overwritten(tmp_path: Path) -> None:
    """出力は「存在すれば失敗」: どちらかが既にあれば、どちらも書かない。"""
    args = _write(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "refill_report.md").write_text("earlier report")
    assert rr.main(args) == 1
    assert (out / "refill_report.md").read_text() == "earlier report"
    assert not (out / "residual_gaps.csv").exists()


# --- 本文・入力・再現 ------------------------------------------------------------------


def test_the_report_has_the_five_sections_and_no_prices(tmp_path: Path) -> None:
    assert rr.main(_write(tmp_path)) == 0
    report = (tmp_path / "out/refill_report.md").read_text(encoding="utf-8")
    for heading in (
        "## 1. 入力と出力の識別",
        "## 2. 補充の結果",
        "## 3. 残存欠落",
        "## 4. 実行可能な連続期間",
        "## 5. 再現",
    ):
        assert heading in report
    assert not report.startswith("# 【下書き】")
    assert f"`{REFILL}`" in report and f"`{REJECTED}`" in report
    assert "計画 2 件・補充分 1 件" in report
    assert "| USDJPY/15m/bid | 3 | 1 | 2 | HOUR_NOT_FETCHED 1, UNRECONCILED 1 |" in report
    assert "要確認: USDJPY/15m/bid 2020-06-01T10:00:00Z BEFORE 差 12.5 pip" in report
    assert "| USDJPY 15m@v1 | 2 / 1.00h | 2 / 0.75h |" in report
    assert "戦略の成績" in report


def test_missing_inputs_fail_without_writing(tmp_path: Path) -> None:
    args = _write(tmp_path)
    (tmp_path / "refill" / REFILL / "validation.json").unlink()
    assert rr.main(args) == 1
    assert not (tmp_path / "out").exists()


def test_the_reproduction_command_runs_from_the_repository_root(tmp_path: Path) -> None:
    """報告の「再現」に書くコマンドの形（``-m tools.ops.refill_report``）で起動できる。"""
    args = _write(tmp_path)
    assert rr.main(args) == 0
    report = (tmp_path / "out/refill_report.md").read_text(encoding="utf-8")
    assert "uv run python -m tools.ops.refill_report" in report
    assert "--rejected-plan" not in report
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "tools.ops.refill_report", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
