"""関所の品質確認専用の許可（ADR-0036、D11 §10 の受入条件 25）。人工データだけを使う。

確かめること: 許可の範囲外（道具・版・設定・区分・期間・対象）は許可を出さない、記録の失敗で
読み取りを始めない、品質確認の記録で封印の状態（消費の記録）を変えない、隔離の区分は許可が
あっても読めない、最終評価の許可は実装しない（呼ばれたら拒否する）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.application.holdout_gate import HoldoutGate
from odyssey_fx.evaluation.application.ports import QualityAccessLog
from odyssey_fx.evaluation.domain.errors import EvaluationError
from odyssey_fx.evaluation.domain.quality_permit import (
    QualityCheckDefinition,
    QualityCheckRequest,
    QualityPermitRefusal,
    QualityReadRefused,
    QualityTargetKind,
)
from odyssey_fx.marketdata.adapters.quality_access_log_store import (
    QUALITY_ACCESS_LOCK_PATH,
    QUALITY_ACCESS_LOG_PATH,
    QualityAccessLogFile,
    quality_access_line_bytes,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.quality_access import (
    AccessPeriod,
    PartitionTarget,
    QualityAccessEnd,
    QualityAccessLogContents,
    QualityAccessResult,
    QualityAccessStart,
    QualityCheckKind,
    QualityOutputKind,
    QualityReadPermit,
    QualityRecordKind,
    RawFileTarget,
    ToolRef,
)
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market

T = UtcTime.parse
TOOL = ToolRef(name="accept_mt5_sealed", version="3")
DIGEST = "c" * 64
OUTPUTS = frozenset(
    {
        QualityOutputKind.COUNTS,
        QualityOutputKind.TIMES,
        QualityOutputKind.INTERVALS,
        QualityOutputKind.HASHES,
        QualityOutputKind.PASS_FAIL,
    }
)
SEALED = frozenset({AccessClass.HOLDOUT_SINCE_2024, AccessClass.FORWARD_HOLDOUT})
RAW = RawFileTarget(
    location="data/raw/oanda_mt5_sealed/20301101/USDJPY/2024-01.csv.gz",
    sha256="d" * 64,
    symbol=Symbol("USDJPY"),
)
DEFINITION = QualityCheckDefinition(
    tool=TOOL,
    config_digest=DIGEST,
    datasource="oanda_mt5_m1",
    check_kind=QualityCheckKind.ACCEPTANCE,
    classes=SEALED,
    period=AccessPeriod(start=T("2024-01-01T00:00:00Z"), end=None),
    target_kind=QualityTargetKind.RAW_FILE,
    symbols=frozenset({Symbol("USDJPY")}),
    timeframes=frozenset({"1m"}),
    output_kinds=OUTPUTS,
    source="configs/quality_checks/accept_mt5_sealed.yaml",
)
REQUEST = QualityCheckRequest(
    tool=TOOL,
    config_digest=DIGEST,
    check_kind=QualityCheckKind.ACCEPTANCE,
    classes=frozenset({AccessClass.HOLDOUT_SINCE_2024}),
    period=AccessPeriod(start=T("2024-01-01T00:00:00Z"), end=T("2030-11-03T22:00:00Z")),
    targets=(RAW,),
    output_kinds=frozenset({QualityOutputKind.COUNTS, QualityOutputKind.PASS_FAIL}),
)


def _root(tmp_path: Path) -> Path:
    (tmp_path / "research").mkdir()
    (tmp_path / QUALITY_ACCESS_LOG_PATH).write_bytes(b"")
    return tmp_path


def _gate(root: Path, *definitions: QualityCheckDefinition) -> HoldoutGate:
    log = QualityAccessLogFile(root)
    assert isinstance(log, QualityAccessLog)
    return HoldoutGate(definitions or (DEFINITION,), log)


def _begin(gate: HoldoutGate, request: QualityCheckRequest = REQUEST) -> object:
    return gate.begin_quality_read(
        request, permit_id="p1", access_id="a1", row_id="r1", accessed_at=T("2030-11-05T00:00:00Z")
    )


def _rows(root: Path) -> tuple[object, ...]:
    contents = QualityAccessLogFile(root).read()
    assert isinstance(contents, QualityAccessLogContents)
    return contents.rows


def test_a_matching_request_records_the_start_row_before_the_permit(tmp_path: Path) -> None:
    root = _root(tmp_path)
    permit = _begin(_gate(root))
    assert isinstance(permit, QualityReadPermit)
    assert permit.access_id == "a1" and permit.permit_id == "p1"
    assert permit.covers_raw_file(RAW.location, "d" * 64)
    assert not permit.covers_raw_file(RAW.location, "e" * 64)
    (start,) = _rows(root)
    assert isinstance(start, QualityAccessStart)
    assert start.permit_id == "p1" and start.targets == (RAW,) and start.tool == TOOL


def test_finishing_writes_the_end_row(tmp_path: Path) -> None:
    root = _root(tmp_path)
    gate = _gate(root)
    permit = _begin(gate)
    assert isinstance(permit, QualityReadPermit)
    end = gate.finish_quality_read(
        permit,
        result=QualityAccessResult.COMPLETED,
        row_id="r2",
        finished_at=T("2030-11-05T00:05:00Z"),
    )
    assert isinstance(end, QualityAccessEnd)
    assert _rows(root)[1] == end


@pytest.mark.parametrize(
    ("request_", "kind"),
    [
        (
            replace(REQUEST, tool=ToolRef(name="accept_mt5_sealed", version="4")),
            QualityPermitRefusal.NO_DEFINITION,
        ),
        (
            replace(REQUEST, tool=ToolRef(name="other_tool", version="3")),
            QualityPermitRefusal.NO_DEFINITION,
        ),
        (replace(REQUEST, config_digest="f" * 64), QualityPermitRefusal.DEFINITION_MISMATCH),
        (
            replace(REQUEST, check_kind=QualityCheckKind.GAP_LIST),
            QualityPermitRefusal.DEFINITION_MISMATCH,
        ),
        (
            replace(REQUEST, output_kinds=frozenset({QualityOutputKind.MATCH_COUNTS})),
            QualityPermitRefusal.DEFINITION_MISMATCH,
        ),
        (
            replace(REQUEST, classes=frozenset({AccessClass.LEGACY_HOLDOUT})),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        (
            replace(REQUEST, classes=frozenset({AccessClass.QUARANTINED_UNASSIGNED})),
            QualityPermitRefusal.QUARANTINED,
        ),
        (
            replace(REQUEST, period=AccessPeriod(start=T("2023-12-01T00:00:00Z"), end=None)),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        (
            replace(REQUEST, targets=(replace(RAW, symbol=Symbol("EURUSD")),)),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        (
            replace(REQUEST, targets=(replace(RAW, symbol=None),)),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        # 旧データの CSV など、封印の原ファイルの置き場の外のファイル（ADR-0036 第4項）。
        (
            replace(
                REQUEST, targets=(replace(RAW, location="data/raw/market/USDJPY_1h_merged.csv"),)
            ),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        (
            replace(
                REQUEST, targets=(replace(RAW, location="data/raw/oanda_mt5/USDJPY/2023-12.csv"),)
            ),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        (
            replace(
                REQUEST,
                targets=(
                    replace(
                        RAW, location="data/raw/oanda_mt5_sealed/x/USDJPY/../../../market/a.csv"
                    ),
                ),
            ),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        # 置き場の通貨と対象の銘柄が食い違う。
        (
            replace(
                REQUEST,
                targets=(replace(RAW, location="data/raw/oanda_mt5_sealed/20301101/EURUSD/a.csv"),),
            ),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
    ],
)
def test_a_request_outside_the_approved_definition_gets_no_permit_and_no_row(
    tmp_path: Path, request_: QualityCheckRequest, kind: QualityPermitRefusal
) -> None:
    root = _root(tmp_path)
    refused = _begin(_gate(root), request_)
    assert isinstance(refused, QualityReadRefused)
    assert refused.kind is kind
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == b""


def test_a_partition_target_must_match_the_kind_symbol_timeframe_and_period(tmp_path: Path) -> None:
    week = T("2024-01-07T22:00:00Z")
    pid = PartitionId(
        series=market.series(timeframe_id="1h"),
        access_class=AccessClass.HOLDOUT_SINCE_2024,
        period_start=week,
    )
    assert pid.period is not None
    target = PartitionTarget(partition_id=pid, interval=pid.period)
    partition_definition = replace(DEFINITION, target_kind=QualityTargetKind.PARTITION)
    root = _root(tmp_path)
    # 原ファイルの定義は partition を覆わない。
    refused = _begin(_gate(root), replace(REQUEST, targets=(target,)))
    assert isinstance(refused, QualityReadRefused)
    # 時間足が定義に無い。
    refused = _begin(_gate(root, partition_definition), replace(REQUEST, targets=(target,)))
    assert isinstance(refused, QualityReadRefused)
    assert "timeframe" in refused.detail
    hourly = replace(partition_definition, timeframes=frozenset({"1h"}))
    permit = _begin(_gate(root, hourly), replace(REQUEST, targets=(target,)))
    assert isinstance(permit, QualityReadPermit)
    assert permit.covers_partition(pid)


def test_without_the_log_file_the_read_does_not_start(tmp_path: Path) -> None:
    refused = _begin(_gate(tmp_path))
    assert isinstance(refused, QualityReadRefused)
    assert refused.kind is QualityPermitRefusal.RECORD_FAILED
    assert not (tmp_path / QUALITY_ACCESS_LOG_PATH).exists()


def test_a_structurally_broken_log_stops_every_new_read(tmp_path: Path) -> None:
    root = _root(tmp_path)
    orphan = QualityAccessEnd(
        row_id="x",
        access_id="nobody",
        finished_at=T("2030-01-01T00:00:00Z"),
        result=QualityAccessResult.COMPLETED,
        record_kind=QualityRecordKind.LIVE,
    )
    (root / QUALITY_ACCESS_LOG_PATH).write_bytes(quality_access_line_bytes(orphan))
    refused = _begin(_gate(root))
    assert isinstance(refused, QualityReadRefused)
    assert refused.kind is QualityPermitRefusal.RECORD_FAILED
    assert "END_WITHOUT_START" in refused.detail


def test_a_held_lock_means_the_record_failed_and_no_permit(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / QUALITY_ACCESS_LOCK_PATH).write_text("held\n")
    refused = _begin(_gate(root))
    assert isinstance(refused, QualityReadRefused)
    assert refused.kind is QualityPermitRefusal.RECORD_FAILED
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == b""


def test_an_end_row_that_cannot_be_written_is_reported_as_a_failure(tmp_path: Path) -> None:
    root = _root(tmp_path)
    gate = _gate(root)
    permit = _begin(gate)
    assert isinstance(permit, QualityReadPermit)
    (root / QUALITY_ACCESS_LOCK_PATH).write_text("held\n")
    refused = gate.finish_quality_read(
        permit,
        result=QualityAccessResult.COMPLETED,
        row_id="r2",
        finished_at=T("2030-11-05T00:05:00Z"),
    )
    assert isinstance(refused, QualityReadRefused)
    assert refused.kind is QualityPermitRefusal.RECORD_FAILED
    assert len(_rows(root)) == 1  # 開始の行だけが「結果不明」として残る


def test_the_quality_flow_writes_no_consumption_record(tmp_path: Path) -> None:
    root = _root(tmp_path)
    snapshot = root / "data" / "snapshots" / "s1"
    snapshot.mkdir(parents=True)
    (snapshot / "access_log.jsonl").write_bytes(b"")
    gate = _gate(root)
    permit = _begin(gate)
    assert isinstance(permit, QualityReadPermit)
    gate.finish_quality_read(
        permit,
        result=QualityAccessResult.STOPPED,
        row_id="r2",
        finished_at=T("2030-11-05T00:05:00Z"),
    )
    # 消費の記録は変わらない（封印の状態を変えない。ADR-0036 第7項）。
    assert (snapshot / "access_log.jsonl").read_bytes() == b""
    assert sorted(path.name for path in root.rglob("*") if path.is_file()) == [
        "access_log.jsonl",
        "quality_access_log.jsonl",
    ]


def test_the_quality_permit_and_the_final_evaluation_permit_are_separate(tmp_path: Path) -> None:
    """2 種類の許可は別の型・別の手順（ADR-0036 第3項）。品質確認の関所は最終評価の入口が無い。"""
    from odyssey_fx.evaluation.application import holdout_gate

    gate = _gate(_root(tmp_path))
    assert not hasattr(gate, "request_final_evaluation_permit")
    assert callable(holdout_gate.issue_final_evaluation_permit)


# --- 許可の定義が覆ってよい範囲（ADR-0036 第4項、ADR-0037 第1項・第9項）------------------


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"datasource": "legacy_merged_csv_v3"}, "OANDA MT5"),
        ({"check_kind": QualityCheckKind.GAP_LIST}, "acceptance"),
        ({"classes": frozenset({AccessClass.QUARANTINED_UNASSIGNED})}, "two OANDA MT5 sealed"),
        ({"classes": frozenset({AccessClass.LEGACY_HOLDOUT})}, "two OANDA MT5 sealed"),
        ({"classes": frozenset({AccessClass.RESEARCH_HISTORY})}, "two OANDA MT5 sealed"),
        ({"output_kinds": frozenset({QualityOutputKind.EVIDENCE})}, "ADR-0037"),
        ({"period": AccessPeriod(start=T("2023-01-01T00:00:00Z"), end=None)}, "2024"),
        ({"config_digest": "nothex"}, "hex"),
        ({"symbols": frozenset({Symbol("USDJPY"), Symbol("NZDUSD")})}, "8 currencies"),
        ({"symbols": frozenset({Symbol("USDCAD")})}, "8 currencies"),
    ],
)
def test_a_definition_outside_the_revised_constraint_cannot_be_built(
    changes: dict[str, object], match: str
) -> None:
    with pytest.raises(EvaluationError, match=match):
        replace(DEFINITION, **changes)  # type: ignore[arg-type]
