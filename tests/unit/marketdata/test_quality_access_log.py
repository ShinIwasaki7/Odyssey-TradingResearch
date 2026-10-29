"""品質確認の閲覧の記録（ADR-0035 第4項・第5項）と partition の読み取りの許可（ADR-0036 第9項）。

人工の行と一時ディレクトリだけを使う（実データ・2024 年以降のデータを読まない）。最後の検査だけ
版管理の記録（`research/quality_access_log.jsonl`。監査 PR #76 の後からの記録 2 行）を読む。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.adapters.quality_access_log_store import (
    QUALITY_ACCESS_LOCK_PATH,
    QUALITY_ACCESS_LOG_PATH,
    append_quality_access_row,
    quality_access_line_bytes,
    read_quality_access_log_file,
)
from odyssey_fx.marketdata.application.quality_access_log import (
    require_partition_read_allowed,
    row_from_payload,
    row_payload,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, MarketDataValueError
from odyssey_fx.marketdata.domain.quality_access import (
    PRE_PERMIT_MECHANISM,
    AccessPeriod,
    PartitionTarget,
    QualityAccessEnd,
    QualityAccessJudgment,
    QualityAccessLogContents,
    QualityAccessLogFailure,
    QualityAccessResult,
    QualityAccessStart,
    QualityCheckKind,
    QualityLogAppendRefusal,
    QualityLogAppendRefused,
    QualityLogDefect,
    QualityOutputKind,
    QualityReadPermit,
    QualityRecordKind,
    RawFileTarget,
    ToolRef,
    completed_access_ids,
    quality_log_defect,
    violation_scopes,
)
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market

T = UtcTime.parse
REPO_ROOT = Path(__file__).resolve().parents[3]
M1 = market.series(timeframe_id="1m")
WEEK = T("2024-01-07T22:00:00Z")
SEALED_PID = PartitionId(series=M1, access_class=AccessClass.HOLDOUT_SINCE_2024, period_start=WEEK)
SEALED_PERIOD = Interval(start=WEEK, end=T("2024-01-14T22:00:00Z"))
assert SEALED_PID.period == SEALED_PERIOD
OUTPUTS = frozenset({QualityOutputKind.COUNTS, QualityOutputKind.HASHES})
RAW = RawFileTarget(
    location="data/raw/oanda_mt5_sealed/x/USDJPY/a.csv.gz", sha256="a" * 64, symbol=Symbol("USDJPY")
)


def _start(access: str = "a1", row: str | None = None, **changes: object) -> QualityAccessStart:
    start = QualityAccessStart(
        row_id=row or f"{access}-s",
        access_id=access,
        accessed_at=T("2030-01-05T00:00:00Z"),
        tool=ToolRef(name="accept_mt5_sealed", version="1"),
        classes=frozenset({AccessClass.HOLDOUT_SINCE_2024}),
        period=AccessPeriod(start=T("2024-01-01T00:00:00Z"), end=None),
        output_kinds=OUTPUTS,
        permit_id=f"permit-{access}",
        targets=(RAW, PartitionTarget(partition_id=SEALED_PID, interval=SEALED_PERIOD)),
        check_kind=QualityCheckKind.ACCEPTANCE,
        record_kind=QualityRecordKind.LIVE,
    )
    return replace(start, **changes)  # type: ignore[arg-type]


def _end(access: str = "a1", row: str | None = None, **changes: object) -> QualityAccessEnd:
    end = QualityAccessEnd(
        row_id=row or f"{access}-e",
        access_id=access,
        finished_at=T("2030-01-05T00:10:00Z"),
        result=QualityAccessResult.COMPLETED,
        record_kind=QualityRecordKind.LIVE,
    )
    return replace(end, **changes)  # type: ignore[arg-type]


def _judgment(access: str = "a1", row: str | None = None) -> QualityAccessJudgment:
    return QualityAccessJudgment(
        row_id=row or f"{access}-j",
        access_id=access,
        violated_conditions=frozenset({1}),
        judged_at=T("2030-02-01T00:00:00Z"),
        judged_by="human",
    )


def _repo(tmp_path: Path, *rows: object, raw: bytes = b"") -> Path:
    (tmp_path / "research").mkdir()
    data = b"".join(quality_access_line_bytes(row) for row in rows) + raw  # type: ignore[arg-type]
    (tmp_path / QUALITY_ACCESS_LOG_PATH).write_bytes(data)
    return tmp_path


# --- 行の型 ------------------------------------------------------------------------------


def test_a_live_access_names_its_permit_and_hashes_every_raw_file() -> None:
    with pytest.raises(MarketDataValueError, match="pre-permit"):
        _start(permit_id=PRE_PERMIT_MECHANISM)
    unhashed = replace(RAW, sha256=None)
    with pytest.raises(MarketDataValueError, match="hash"):
        _start(targets=(unhashed,))
    # 後から記録した行だけはハッシュが無くてよい（記録に無いもの）。
    _start(targets=(unhashed,), record_kind=QualityRecordKind.RETROACTIVE)


def test_the_research_history_is_not_a_quality_access_class() -> None:
    with pytest.raises(MarketDataValueError, match="outside the allowed set"):
        _start(classes=frozenset({AccessClass.RESEARCH_HISTORY}))


def test_a_judgment_names_only_the_conditions_1_3_6() -> None:
    with pytest.raises(MarketDataValueError, match="1, 3, 6"):
        replace(_judgment(), violated_conditions=frozenset({2}))


def test_every_row_kind_survives_the_round_trip() -> None:
    for row in (_start(), _end(), _judgment()):
        payload = json.loads(json.dumps(row_payload(row)))
        assert row_from_payload(payload) == row


# --- 構造の誤り（ADR-0035 第4項の 6）---------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "kind", "line"),
    [
        ((_start(), _start(row="other")), QualityLogDefect.DUPLICATE_START, 2),
        ((_start(), _end(), _end(row="other")), QualityLogDefect.DUPLICATE_END, 3),
        ((_end(),), QualityLogDefect.END_WITHOUT_START, 1),
        ((_judgment(),), QualityLogDefect.JUDGMENT_WITHOUT_START, 1),
        ((_start(), _judgment(), _judgment(row="other")), QualityLogDefect.DUPLICATE_JUDGMENT, 3),
        ((_start(), _start("a2", row="a1-s")), QualityLogDefect.DUPLICATE_ROW_ID, 2),
        (
            (_start(), _end(record_kind=QualityRecordKind.RETROACTIVE)),
            QualityLogDefect.RECORD_KIND_MISMATCH,
            2,
        ),
    ],
)
def test_the_reader_stops_on_every_structural_error(
    rows: tuple[object, ...], kind: QualityLogDefect, line: int
) -> None:
    found = quality_log_defect(rows)  # type: ignore[arg-type]
    assert found is not None
    assert found[0] is kind
    assert found[1] == line


def test_a_start_without_an_end_is_kept_as_unknown_not_dropped() -> None:
    assert quality_log_defect((_start(),)) is None
    assert completed_access_ids((_start(),)) == frozenset()
    assert completed_access_ids((_start(), _end())) == {"a1"}
    stopped = _end(result=QualityAccessResult.STOPPED)
    assert completed_access_ids((_start(), stopped)) == frozenset()


def test_violation_scopes_follow_the_judged_targets() -> None:
    scopes = violation_scopes((_start(), _end(), _judgment(), _start("a2"), _end("a2")))
    assert len(scopes) == 2
    raw_scope, partition_scope = scopes
    assert raw_scope.series is None and raw_scope.symbol == Symbol("USDJPY")
    assert raw_scope.period is None  # 原ファイルの期間が分からないので封印の区分の全体
    assert partition_scope.series == M1 and partition_scope.period == SEALED_PERIOD


# --- 記録のファイル（ADR-0035 第4項の 5）------------------------------------------------


def test_the_file_is_never_created_by_an_append(tmp_path: Path) -> None:
    refused = append_quality_access_row(tmp_path, _start())
    assert isinstance(refused, QualityLogAppendRefused)
    assert refused.kind is QualityLogAppendRefusal.READ_FAILED
    assert refused.read_failure is not None
    assert refused.read_failure.kind is QualityLogDefect.FILE_MISSING
    assert not (tmp_path / QUALITY_ACCESS_LOG_PATH).exists()
    assert not (tmp_path / QUALITY_ACCESS_LOCK_PATH).exists()


def test_an_append_writes_one_line_reads_it_back_and_removes_the_lock(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert append_quality_access_row(root, _start()) == _start()
    assert append_quality_access_row(root, _end()) == _end()
    contents = read_quality_access_log_file(root / QUALITY_ACCESS_LOG_PATH)
    assert isinstance(contents, QualityAccessLogContents)
    assert contents.rows == (_start(), _end())
    assert not (root / QUALITY_ACCESS_LOCK_PATH).exists()


def test_a_held_lock_refuses_the_append_and_leaves_the_file(tmp_path: Path) -> None:
    root = _repo(tmp_path, _start())
    before = (root / QUALITY_ACCESS_LOG_PATH).read_bytes()
    (root / QUALITY_ACCESS_LOCK_PATH).write_text("held\n")
    refused = append_quality_access_row(root, _end())
    assert isinstance(refused, QualityLogAppendRefused)
    assert refused.kind is QualityLogAppendRefusal.LOCKED
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == before
    # 止まって残ったロックは自動で外さない。
    assert (root / QUALITY_ACCESS_LOCK_PATH).exists()


def test_a_write_failure_right_after_creating_the_lock_removes_that_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ロックを作った直後の書き込みが失敗しても、自分で作ったロックを残さない（残 P2 の 3）。"""
    root = _repo(tmp_path, _start())
    before = (root / QUALITY_ACCESS_LOG_PATH).read_bytes()

    def failing_fsync(descriptor: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(
        "odyssey_fx.marketdata.adapters.quality_access_log_store.os.fsync", failing_fsync
    )
    refused = append_quality_access_row(root, _end())
    assert isinstance(refused, QualityLogAppendRefused)
    assert refused.kind is QualityLogAppendRefusal.LOCKED
    assert not (root / QUALITY_ACCESS_LOCK_PATH).exists()
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == before
    monkeypatch.undo()
    # 次の追記はロックに断られない。
    assert append_quality_access_row(root, _end()) == _end()


def test_a_torn_tail_is_cut_before_the_next_append(tmp_path: Path) -> None:
    root = _repo(tmp_path, _start(), raw=b'{"access_id":"a1","fini')
    contents = read_quality_access_log_file(root / QUALITY_ACCESS_LOG_PATH)
    assert isinstance(contents, QualityAccessLogContents)
    assert contents.rows == (_start(),) and contents.torn_tail
    assert append_quality_access_row(root, _end()) == _end()
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == (
        quality_access_line_bytes(_start()) + quality_access_line_bytes(_end())
    )


@pytest.mark.parametrize(
    "line",
    [
        b"not json\n",
        # JSON だが正規化エンコードではない（空白入り）。
        json.dumps(row_payload(_start()), indent=1).replace("\n", "").encode() + b"\n",
        b'{"row_kind":"START"}\n',
    ],
)
def test_a_broken_complete_line_stops_the_reader(tmp_path: Path, line: bytes) -> None:
    root = _repo(tmp_path, raw=line)
    failure = read_quality_access_log_file(root / QUALITY_ACCESS_LOG_PATH)
    assert isinstance(failure, QualityAccessLogFailure)
    assert failure.kind is QualityLogDefect.LINE_CORRUPT
    assert failure.line_number == 1
    refused = append_quality_access_row(root, _start())
    assert isinstance(refused, QualityLogAppendRefused)
    assert refused.kind is QualityLogAppendRefusal.READ_FAILED
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == line


def test_a_row_that_would_break_the_structure_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path, _start())
    before = (root / QUALITY_ACCESS_LOG_PATH).read_bytes()
    refused = append_quality_access_row(root, _start(row="again"))
    assert isinstance(refused, QualityLogAppendRefused)
    assert refused.kind is QualityLogAppendRefusal.ROW_REFUSED
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == before
    end_without_start = append_quality_access_row(root, _end("zz"))
    assert isinstance(end_without_start, QualityLogAppendRefused)


# --- partition の読み取りの許可（ADR-0036 第9項の 3）------------------------------------


def _permit(*targets: object) -> QualityReadPermit:
    return QualityReadPermit(
        permit_id="p",
        access_id="a",
        tool=ToolRef(name="accept_mt5_sealed", version="1"),
        classes=frozenset({AccessClass.HOLDOUT_SINCE_2024}),
        period=AccessPeriod(start=T("2024-01-01T00:00:00Z"), end=None),
        targets=targets or (PartitionTarget(partition_id=SEALED_PID, interval=SEALED_PERIOD),),  # type: ignore[arg-type]
        output_kinds=OUTPUTS,
    )


def test_a_sealed_partition_is_read_only_under_a_covering_permit() -> None:
    research = PartitionId(series=M1, access_class=AccessClass.RESEARCH_HISTORY)
    require_partition_read_allowed(research, None)
    with pytest.raises(HoldoutAccessViolation, match="no permit"):
        require_partition_read_allowed(SEALED_PID, None)
    require_partition_read_allowed(SEALED_PID, _permit())
    other_week = PartitionId(
        series=M1,
        access_class=AccessClass.HOLDOUT_SINCE_2024,
        period_start=T("2024-01-14T22:00:00Z"),
    )
    with pytest.raises(HoldoutAccessViolation, match="does not cover"):
        require_partition_read_allowed(other_week, _permit())
    with pytest.raises(HoldoutAccessViolation, match="does not cover"):
        require_partition_read_allowed(SEALED_PID, _permit(RAW))


def test_a_read_permit_never_covers_quarantine_or_extra_outputs() -> None:
    with pytest.raises(MarketDataValueError, match="outside the allowed set"):
        replace(_permit(), classes=frozenset({AccessClass.QUARANTINED_UNASSIGNED}))
    with pytest.raises(MarketDataValueError, match="ADR-0037"):
        replace(_permit(), output_kinds=frozenset({QualityOutputKind.EVIDENCE}))
    with pytest.raises(MarketDataValueError, match="pre-permit"):
        replace(_permit(), permit_id=PRE_PERMIT_MECHANISM)


def test_the_store_refuses_a_sealed_partition_before_opening_any_file(tmp_path: Path) -> None:
    store = ParquetSnapshotStore(tmp_path)
    # partition のファイルは無い。許可の確かめがファイルを開く前に止める
    # （FileNotFoundError ではない）。
    with pytest.raises(HoldoutAccessViolation):
        store.read_partition("s1", SEALED_PID)
    with pytest.raises(FileNotFoundError):
        store.read_partition("s1", SEALED_PID, _permit())


# --- 版管理の記録（監査 PR #76 の後からの記録。ADR-0035 第5項）---------------------------


def test_the_committed_log_holds_exactly_the_retroactive_audit_access() -> None:
    contents = read_quality_access_log_file(REPO_ROOT / QUALITY_ACCESS_LOG_PATH)
    assert isinstance(contents, QualityAccessLogContents)
    assert not contents.torn_tail
    start, end = contents.rows
    assert isinstance(start, QualityAccessStart) and isinstance(end, QualityAccessEnd)
    assert start.access_id == end.access_id
    assert start.record_kind is end.record_kind is QualityRecordKind.RETROACTIVE
    assert start.permit_id == PRE_PERMIT_MECHANISM
    assert start.check_kind is QualityCheckKind.AUDIT_COUNT
    assert start.accessed_at == T("2026-10-09T00:00:00Z")
    assert start.tool.version.endswith("@1873761")
    assert start.classes == {AccessClass.LEGACY_HOLDOUT, AccessClass.QUARANTINED_UNASSIGNED}
    assert start.period == AccessPeriod(start=T("2024-01-01T00:00:00Z"), end=None)
    assert QualityOutputKind.COUNTS in start.output_kinds
    assert QualityOutputKind.TIMES in start.output_kinds
    raw = [target for target in start.targets if isinstance(target, RawFileTarget)]
    assert len(raw) == len(start.targets) == 36  # 原 CSV 20 本（10 銘柄）と 1 分足 16 本
    assert end.result is QualityAccessResult.COMPLETED
