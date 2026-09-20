"""二段階の受入れフローの単体テスト（D03 §3.7.1・§4 の 8〜9・§11）。

確かめること:

- 暫定段階では分類が空で、暫定の識別子が計算できる。
- 未分類の警告が残る状態では確定できない。
- 分類を記入すると識別子が変わり、分類の内容が違えば別の識別子になる。
- 受入れ実行時刻を変えても暫定・最終の識別子は変わらない。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import cast

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.acceptance import (
    FinalizedSnapshot,
    PendingSnapshot,
    RawFile,
    approve,
    build_pending_snapshot,
    finalize,
    provisional_id,
)
from odyssey_fx.marketdata.application.partition_digest import partition_digest_hex
from odyssey_fx.marketdata.application.report_digest import integrity_report_digest_hex
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import IntegrityCheckFailed, MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    ClosureDecision,
    ClosureDecisionKind,
)
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
CALENDAR = market.calendar()
WINDOW = Interval(
    start=UtcTime.parse("2022-01-05T22:00:00Z"), end=UtcTime.parse("2022-01-07T22:00:00Z")
)
DROPPED = UtcTime.parse("2022-01-06T10:00:00Z")

RAW_FILE = snapshots.source("data/raw/market/USDJPY_1h_merged.csv")


def _build(
    created_at: UtcTime,
    *,
    skip_starts: Iterable[UtcTime] = (DROPPED,),
    bars: Sequence[Bar] | None = None,
    aggregated_findings: Sequence[CheckResult] = (),
) -> PendingSnapshot:
    """暫定 snapshot を組み立てる（テスト用の最小の受入れ）。"""
    source_bars = (
        tuple(bars)
        if bars is not None
        else market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW, skip_starts=skip_starts)
    )
    raw_file = RawFile(
        path=RAW_FILE.path,
        sha256=RAW_FILE.sha256,
        symbol=RAW_FILE.symbol,
        timeframe=RAW_FILE.timeframe,
        declared_basis=RAW_FILE.declared_basis,
    )
    return build_pending_snapshot(
        created_at=created_at,
        raw_files=(raw_file,),
        bars_by_file={raw_file.path: source_bars},
        timeframe_defs=market.TIMEFRAME_DEFS,
        calendar=CALENDAR,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        basis_declaration=snapshots.BASIS,
        conversion=snapshots.CONVERSION,
        aggregated_findings=aggregated_findings,
    )


# --- 暫定段階（D03 §3.7.1 の 1）---------------------------------------------


def test_the_pending_snapshot_has_no_closure_decisions() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    assert pending.manifest.closure_decisions == ()
    assert pending.provisional_id == provisional_id(pending.manifest)


def test_the_pending_snapshot_reports_the_missing_bar() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    warnings = pending.report.warnings
    assert warnings
    assert any(result.interval.start == DROPPED for result in warnings)


def test_the_provisional_id_ignores_the_acceptance_time() -> None:
    morning = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    evening = _build(UtcTime.parse("2026-09-20T21:30:00Z"))
    assert morning.provisional_id == evening.provisional_id


def test_a_provisional_id_cannot_be_taken_after_the_decisions_are_recorded() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    decided = pending.manifest.with_closure_decisions(
        (
            ClosureDecision(
                series_id=HOURLY,
                interval=Interval(start=DROPPED, end=DROPPED + market.TF_1H.nominal_length),
                kind=ClosureDecisionKind.DATA_GAP,
            ),
        )
    )
    with pytest.raises(MarketDataValueError, match="already carries decisions"):
        provisional_id(decided)


# --- 確定段階（D03 §3.7.1 の 2、§4 の 9）-----------------------------------


def _decision(kind: ClosureDecisionKind) -> ClosureDecision:
    return ClosureDecision(
        series_id=HOURLY,
        interval=Interval(start=DROPPED, end=DROPPED + market.TF_1H.nominal_length),
        kind=kind,
    )


def test_an_unclassified_warning_blocks_the_finalization() -> None:
    """未分類の警告が残る場合は確定できない（D03 §10 の classify コマンド）。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    with pytest.raises(MarketDataValueError, match="still unclassified"):
        finalize(pending, ())


def test_recording_the_decision_yields_a_different_snapshot_id() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    final = finalize(pending, (_decision(ClosureDecisionKind.DATA_GAP),))
    assert final.snapshot_id != pending.provisional_id


def test_classifying_the_gap_differently_yields_a_different_snapshot_id() -> None:
    """分類が異なれば別 snapshot である（D03 §3.7.1）。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    as_gap = finalize(pending, (_decision(ClosureDecisionKind.DATA_GAP),))
    as_closure = finalize(pending, (_decision(ClosureDecisionKind.CLOSURE),))
    assert as_gap.snapshot_id != as_closure.snapshot_id


def test_the_final_snapshot_id_ignores_the_acceptance_time() -> None:
    morning = finalize(
        _build(UtcTime.parse("2026-09-20T09:00:00Z")),
        (_decision(ClosureDecisionKind.DATA_GAP),),
    )
    evening = finalize(
        _build(UtcTime.parse("2026-09-20T21:30:00Z")),
        (_decision(ClosureDecisionKind.DATA_GAP),),
    )
    assert morning.snapshot_id == evening.snapshot_id


def test_a_clean_series_finalizes_without_decisions() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    assert pending.report.warnings == ()
    final = finalize(pending, ())
    assert final.snapshot_id == pending.provisional_id


# --- partition の分割（D03 §4 の 7）-----------------------------------------


def test_the_pending_snapshot_records_one_partition_per_access_class() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    # 2022 年のデータなのですべて研究履歴に入る。
    assert len(pending.partition_bars) == 1
    (partition_id,) = pending.partition_bars
    assert partition_id.access_class.value == "RESEARCH_HISTORY"
    assert pending.manifest.partition_record(partition_id) is not None


# --- 重大な違反は受入れを失敗させる（D03 §4 の 4）--------------------------


def _duplicated_bars() -> tuple[Bar, ...]:
    """同じ開始時刻の足を2本含む列（重複時刻の重大な違反）。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    return (*bars, bars[0])


def _misaligned_bars() -> tuple[Bar, ...]:
    """整列に合わない開始時刻の足を含む列。"""
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
    offset = Interval(
        start=UtcTime.parse("2022-01-06T10:30:00Z"),
        end=UtcTime.parse("2022-01-06T11:00:00Z"),
    )
    return (*bars, market.make_bar(HOURLY, offset))


def test_a_duplicate_timestamp_stops_the_acceptance() -> None:
    """重大な違反があれば暫定 manifest を作らない（D03 §4 の 4）。

    作ってしまうと、構造的に無効なデータがそのまま確定・承認できてしまう。
    """
    with pytest.raises(IntegrityCheckFailed, match="DUPLICATE_TIMESTAMP"):
        _build(UtcTime.parse("2026-09-20T09:00:00Z"), bars=_duplicated_bars())


def test_a_misaligned_bar_start_stops_the_acceptance() -> None:
    with pytest.raises(IntegrityCheckFailed, match="IRREGULAR_INTERVAL"):
        _build(UtcTime.parse("2026-09-20T09:00:00Z"), bars=_misaligned_bars())


def test_the_failure_message_reports_the_count_and_kinds() -> None:
    """どの検査で何件落ちたかが、実行ログだけで分かる。"""
    with pytest.raises(IntegrityCheckFailed) as raised:
        _build(UtcTime.parse("2026-09-20T09:00:00Z"), bars=_duplicated_bars())
    message = str(raised.value)
    assert "1 error(s)" in message
    assert "DUPLICATE_TIMESTAMP=1" in message
    # 価格は報告に含めない（D03 §3.9）。
    assert "150" not in message


def test_finalize_also_refuses_a_report_carrying_errors() -> None:
    """確定の段階でも重ねて検査する（別経路で組み立てた場合の抜け道を塞ぐ）。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    broken = PendingSnapshot(
        manifest=pending.manifest,
        report=IntegrityReport(
            results=(
                CheckResult.create(
                    CheckKind.DUPLICATE_TIMESTAMP,
                    HOURLY,
                    Interval(start=DROPPED, end=DROPPED + market.TF_1H.nominal_length),
                ),
            )
        ),
        partition_bars=pending.partition_bars,
    )
    with pytest.raises(IntegrityCheckFailed, match="DUPLICATE_TIMESTAMP"):
        finalize(broken, ())


def test_a_warning_alone_does_not_stop_the_acceptance() -> None:
    """人間の判断を要する警告は受入れを止めない（分類の対象、D03 §4 の 9）。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    assert pending.report.warnings
    assert not pending.report.has_errors()


# --- 上位足の報告を取り込む（D03 §5.2）-------------------------------------


def _aggregate_finding() -> CheckResult:
    """上位足が生成できなかった区間の報告（構成足が欠けた区間）。"""
    return CheckResult.create(
        CheckKind.MISSING_EXPECTED_BAR,
        market.series(timeframe_id="4h_ny17"),
        Interval(
            start=UtcTime.parse("2022-01-06T06:00:00Z"),
            end=UtcTime.parse("2022-01-06T10:00:00Z"),
        ),
        detail={"reason": "incomplete_aggregate"},
    )


def test_the_aggregation_findings_are_kept_in_the_report() -> None:
    """不完全な上位足の報告が消えない（D03 §5.2）。

    これを取り込まないと、端の不完全な区間が「生成されなかった」という事実ごと記録から
    消えてしまう。
    """
    finding = _aggregate_finding()
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), aggregated_findings=(finding,))
    assert finding in pending.report.results


def test_a_duplicate_finding_is_recorded_only_once() -> None:
    """同じ結果を2度渡しても記録は1件（同一の整列鍵で1つにまとめる）。"""
    finding = _aggregate_finding()
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), aggregated_findings=(finding, finding))
    matching = [
        result for result in pending.report.results if result.sort_key() == finding.sort_key()
    ]
    assert len(matching) == 1


def test_the_aggregation_findings_reach_the_report() -> None:
    """取り込んだぶんだけ警告が増える（報告まで届いていることの確認）。"""
    plain = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    with_finding = _build(
        UtcTime.parse("2026-09-20T09:00:00Z"), aggregated_findings=(_aggregate_finding(),)
    )
    assert len(with_finding.report.warnings) == len(plain.report.warnings) + 1


def test_the_report_order_is_unaffected_by_the_findings_order() -> None:
    """統合の順序は報告の並びに影響しない（D03 §3.7.1 の正規順序）。"""
    first = _aggregate_finding()
    second = CheckResult.create(
        CheckKind.MISSING_EXPECTED_BAR,
        market.series(timeframe_id="1d_ny17"),
        Interval(
            start=UtcTime.parse("2022-01-05T22:00:00Z"),
            end=UtcTime.parse("2022-01-06T22:00:00Z"),
        ),
    )
    forward = _build(UtcTime.parse("2026-09-20T09:00:00Z"), aggregated_findings=(first, second))
    backward = _build(UtcTime.parse("2026-09-20T09:00:00Z"), aggregated_findings=(second, first))
    assert forward.report.results == backward.report.results


def test_a_non_check_result_in_the_findings_is_refused() -> None:
    with pytest.raises(MarketDataValueError, match="must contain CheckResult"):
        _build(
            UtcTime.parse("2026-09-20T09:00:00Z"),
            aggregated_findings=cast("Sequence[CheckResult]", ("not a finding",)),
        )


# --- 分類と警告の対応（D03 §4 の 9）-----------------------------------------


def _warned_interval() -> Interval:
    """検査が報告した欠落区間（分類の対象）。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    (warning,) = pending.report.warnings
    return warning.interval


def test_a_decision_with_the_wrong_end_leaves_the_warning_unclassified() -> None:
    """分類の対応は区間**全体**で取る（D03 §4 の 9）。

    開始時刻だけで突き合わせると、終端の違う分類（別の足を指す分類）が対応済みとして
    通ってしまう。
    """
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    warned = _warned_interval()
    wrong_end = ClosureDecision(
        series_id=HOURLY,
        interval=Interval(start=warned.start, end=warned.end + market.TF_1H.nominal_length),
        kind=ClosureDecisionKind.DATA_GAP,
    )
    with pytest.raises(MarketDataValueError, match="still unclassified"):
        finalize(pending, (wrong_end,))


def test_a_decision_without_a_matching_warning_is_refused() -> None:
    """検査が報告していない区間の分類は拒否する。

    余分な分類は識別子を変えるので、放置すると内容の同じ snapshot が別物として記録される。
    """
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    extra = ClosureDecision(
        series_id=HOURLY,
        interval=Interval(
            start=UtcTime.parse("2022-01-06T20:00:00Z"),
            end=UtcTime.parse("2022-01-06T21:00:00Z"),
        ),
        kind=ClosureDecisionKind.CLOSURE,
    )
    with pytest.raises(MarketDataValueError, match="do not correspond to any reported"):
        finalize(pending, (_decision(ClosureDecisionKind.DATA_GAP), extra))


def test_a_decision_for_another_series_is_refused() -> None:
    """系列の違う分類は、区間が同じでも対応する警告がないので拒否される。

    本物の警告には正しい分類を添え、系列だけが違う分類を1件足して確かめる。
    """
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    warned = _warned_interval()
    correct = ClosureDecision(series_id=HOURLY, interval=warned, kind=ClosureDecisionKind.DATA_GAP)
    foreign = ClosureDecision(
        series_id=market.series(symbol=market.EURUSD),
        interval=warned,
        kind=ClosureDecisionKind.DATA_GAP,
    )
    with pytest.raises(MarketDataValueError, match="do not correspond to any reported"):
        finalize(pending, (correct, foreign))


def test_a_decision_matching_the_full_interval_is_accepted() -> None:
    """区間が完全に一致する分類は受理される。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    warned = _warned_interval()
    decision = ClosureDecision(series_id=HOURLY, interval=warned, kind=ClosureDecisionKind.DATA_GAP)
    final = finalize(pending, (decision,))
    assert final.manifest.closure_decisions == (decision,)


def test_a_clean_report_refuses_any_decision() -> None:
    """警告が1件も無ければ、分類も1件も受け付けない。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    assert pending.report.warnings == ()
    with pytest.raises(MarketDataValueError, match="do not correspond to any reported"):
        finalize(pending, (_decision(ClosureDecisionKind.DATA_GAP),))


# --- ダイジェストは受入れが自分で計算する（D03 §3.7.1）---------------------


def test_the_partition_digest_is_computed_from_the_recorded_bars() -> None:
    """partition のダイジェストは、その場で分けた足から決まる。

    呼び出し元が渡した値を記録する形だと、実データと食い違う値のまま確定・承認できて
    しまう。
    """
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    for partition_id, bars in pending.partition_bars.items():
        record = pending.manifest.partition_record(partition_id)
        assert record is not None
        assert record.digest.hex == partition_digest_hex(bars)
        assert record.bar_count == len(bars)


def test_the_integrity_report_digest_is_computed_from_the_report() -> None:
    """検査報告のダイジェストも、その場で組み立てた報告から決まる。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    assert pending.manifest.integrity_report_ref.hex == integrity_report_digest_hex(pending.report)


def test_a_different_report_yields_a_different_snapshot_id() -> None:
    """報告が変われば識別子も変わる（報告が識別に結び付いている）。"""
    clean = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    with_gap = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    assert clean.provisional_id != with_gap.provisional_id


def test_the_written_partition_digest_matches_the_manifest(tmp_path: Path) -> None:
    """adapters が書き出して返す値と、manifest に記録された値が一致する。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"))
    store = ParquetSnapshotStore(root=tmp_path)
    for partition_id, bars in pending.partition_bars.items():
        written = store.write_partition("snap", partition_id, bars)
        record = pending.manifest.partition_record(partition_id)
        assert record is not None
        assert written == record.digest.hex
        # 書いたものを読み戻して再計算しても一致する。
        restored = store.read_partition("snap", partition_id)
        assert partition_digest_hex(restored) == record.digest.hex


# --- 承認は確定段階を経たものだけ（D03 §3.7.1 の2）-------------------------


def test_finalize_returns_a_finalized_snapshot() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    final = finalize(pending, ())
    assert isinstance(final, FinalizedSnapshot)
    assert not final.manifest.is_approved
    assert final.directory_name == str(final.snapshot_id)


def test_approving_a_finalized_snapshot_records_the_approval() -> None:
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    final = finalize(pending, ())
    approval = Approval(
        approved_by="reviewer",
        approved_at=UtcTime.parse("2026-09-20T12:00:00Z"),
        comment="確認済み",
    )
    approved = approve(final, approval)
    assert approved.is_approved
    # 承認は識別子に影響しない（D03 §3.7.1）。
    assert approved.snapshot_id() == final.snapshot_id


def test_a_provisional_manifest_cannot_be_approved_through_this_path() -> None:
    """暫定段階の manifest は `FinalizedSnapshot` にならないので承認できない。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    approval = Approval(approved_by="reviewer", approved_at=UtcTime.parse("2026-09-20T12:00:00Z"))
    with pytest.raises(MarketDataValueError, match="requires a FinalizedSnapshot"):
        approve(pending.manifest, approval)  # type: ignore[arg-type]


def test_a_finalized_snapshot_must_not_already_carry_an_approval() -> None:
    """承認は確定のあとに記入する。先に付いている manifest は確定段階の型にできない。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    already = pending.manifest.with_approval(
        Approval(approved_by="a", approved_at=UtcTime.parse("2026-09-20T12:00:00Z"))
    )
    with pytest.raises(MarketDataValueError, match="carries no approval yet"):
        FinalizedSnapshot(manifest=already)


def test_an_approved_snapshot_can_be_opened_for_reading() -> None:
    """確定 → 承認 → 読み取り、という順が通ることを確かめる（D03 §3.7.1）。"""
    pending = _build(UtcTime.parse("2026-09-20T09:00:00Z"), skip_starts=())
    final = finalize(pending, ())
    approved = approve(
        final,
        Approval(approved_by="reviewer", approved_at=UtcTime.parse("2026-09-20T12:00:00Z")),
    )
    readable = ReadableSnapshot(
        manifest=approved, directory_name=final.directory_name, report=IntegrityReport()
    )
    assert readable.snapshot_id == final.snapshot_id
