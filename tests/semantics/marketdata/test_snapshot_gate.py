"""読み取り関門の意味論テスト（D03 §3.7.1・§6.1）。

確かめること:

- **構築後に渡した列を変更しても、読み取り結果は変わらない**。照合は構築時の一度きりなので、
  読むのはその時点で固定した写しでなければならない（D03 §6.1）。
- 暫定段階（`_pending/`）の snapshot は、承認が付いていても読めない（D03 §3.7.1 の1）。
- ディレクトリ名が最終識別子と違う snapshot は読めない（D03 §3.7.1 の2）。
- 実行区間が許可 partition の期間を越える公開フィードは作れない（D03 §6.1・§7.1）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.asof import (
    AsOfView,
    ExecutionSeriesView,
    MissingInput,
)
from odyssey_fx.marketdata.application.publication import (
    PublicationKind,
    build_feed,
    build_publication_log,
)
from odyssey_fx.marketdata.application.report_digest import integrity_report_digest_hex
from odyssey_fx.marketdata.application.snapshot_access import (
    ReadableSnapshot,
    classification_mismatch,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import (
    HoldoutAccessViolation,
    MarketDataValueError,
    SnapshotNotApproved,
)
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    ClosureDecision,
    ClosureDecisionKind,
    PartitionId,
    SnapshotManifest,
)
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
CALENDAR = market.calendar()
SCHEDULES = {HOURLY: SeriesSchedule(series=HOURLY, timeframe_def=market.TF_1H, calendar=CALENDAR)}
RESEARCH = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)

#: 研究期間（2023年）の窓。許可するのはこの期間だけ。
RESEARCH_WINDOW = Interval(
    start=UtcTime.parse("2023-06-01T00:00:00Z"), end=UtcTime.parse("2023-06-01T06:00:00Z")
)

#: 封印期間（2024年）の窓。許可していないので読めてはいけない。
HOLDOUT_WINDOW = Interval(
    start=UtcTime.parse("2024-06-03T00:00:00Z"), end=UtcTime.parse("2024-06-03T06:00:00Z")
)


def _research_bars() -> tuple[Bar, ...]:
    return market.make_bars(HOURLY, market.TF_1H, CALENDAR, RESEARCH_WINDOW)


def _holdout_bars() -> tuple[Bar, ...]:
    return market.make_bars(HOURLY, market.TF_1H, CALENDAR, HOLDOUT_WINDOW)


# --- 構築後の変更が効かないこと（D03 §6.1）---------------------------------


def test_mutating_the_supplied_list_does_not_change_the_as_of_view() -> None:
    """ビューを作った後に同じ列へ封印期間の足を足しても、読み取り結果は変わらない。

    照合は構築時の一度きりなので、呼び出し元の可変な列をそのまま保持すると、照合を
    すり抜けたあとに中身を差し替えられてしまう。
    """
    research = _research_bars()
    mutable: list[Bar] = list(research)
    view = AsOfView(
        snapshot=snapshots.readable_for({RESEARCH: research}),
        allowed_partitions=frozenset({RESEARCH}),
        schedules=SCHEDULES,
        partition_bars={RESEARCH: mutable},
    )
    before = view.latest_available(HOURLY, RESEARCH_WINDOW.end)

    mutable.extend(_holdout_bars())

    # 研究期間の読み取りは変わらない。
    assert view.latest_available(HOURLY, RESEARCH_WINDOW.end) == before
    # 封印期間は読めないまま（構造エラーで止まる）。
    with pytest.raises(HoldoutAccessViolation):
        view.latest_available(HOURLY, HOLDOUT_WINDOW.end)


def test_mutating_the_supplied_list_does_not_change_the_execution_view() -> None:
    """執行系列のビューも同じ。"""
    research = _research_bars()
    mutable: list[Bar] = list(research)
    view = ExecutionSeriesView(
        snapshot=snapshots.readable_for({RESEARCH: research}),
        series=HOURLY,
        allowed_partitions=frozenset({RESEARCH}),
        partition_bars={RESEARCH: mutable},
    )
    before = view.next_bar_key_after(RESEARCH_WINDOW.start)

    mutable.extend(_holdout_bars())

    assert view.next_bar_key_after(RESEARCH_WINDOW.start) == before
    # 封印期間の足は執行系列からも見えない。
    assert view.bar(_holdout_bars()[0].key) is None


def test_mutating_the_supplied_list_does_not_change_the_publication_feed() -> None:
    """公開フィードも同じ。後から足しても公開イベントは増えない。"""
    research = _research_bars()
    mutable: list[Bar] = list(research)
    readable = snapshots.readable_for({RESEARCH: research})
    feed = build_feed(
        readable,
        frozenset({RESEARCH}),
        {RESEARCH: mutable},
        SCHEDULES,
        RESEARCH_WINDOW,
    )
    before = len(feed.of_kind(PublicationKind.PUBLICATION))

    mutable.extend(_holdout_bars())

    assert len(feed.of_kind(PublicationKind.PUBLICATION)) == before


def test_mutating_the_supplied_list_does_not_change_the_publication_log() -> None:
    research = _research_bars()
    mutable: list[Bar] = list(research)
    log = build_publication_log(
        snapshots.readable_for({RESEARCH: research}),
        frozenset({RESEARCH}),
        {RESEARCH: mutable},
        SCHEDULES,
    )
    before = len(log.records)

    mutable.extend(_holdout_bars())

    assert len(log.records) == before


def test_the_view_exposes_its_bars_as_immutable() -> None:
    """ビューが持つ足の写しそのものも書き換えられない。"""
    research = _research_bars()
    view = AsOfView(
        snapshot=snapshots.readable_for({RESEARCH: research}),
        allowed_partitions=frozenset({RESEARCH}),
        schedules=SCHEDULES,
        partition_bars={RESEARCH: list(research)},
    )
    assert isinstance(view.partition_bars[RESEARCH], tuple)
    with pytest.raises(TypeError):
        view.partition_bars[RESEARCH] = ()  # type: ignore[index]


# --- 暫定と最終の区別（D03 §3.7.1 の1・2）----------------------------------


def test_a_provisional_snapshot_is_never_readable_even_when_approved() -> None:
    """暫定ディレクトリ配下は、承認を付けても読めない（D03 §3.7.1 の1）。"""
    approved = snapshots.approved_for({RESEARCH: _research_bars()})
    with pytest.raises(SnapshotNotApproved, match="provisional snapshot"):
        ReadableSnapshot(
            manifest=approved,
            directory_name=f"_pending/{approved.snapshot_id()}",
            report=IntegrityReport(),
        )


def test_a_nested_pending_path_is_also_refused() -> None:
    approved = snapshots.approved_for({RESEARCH: _research_bars()})
    with pytest.raises(SnapshotNotApproved, match="provisional snapshot"):
        ReadableSnapshot(
            manifest=approved,
            directory_name=f"data/_pending/{approved.snapshot_id()}",
            report=IntegrityReport(),
        )


def test_the_directory_name_must_be_the_final_snapshot_id() -> None:
    """ディレクトリ名は最終識別子でなければならない（D03 §3.7.1 の2）。"""
    approved = snapshots.approved_for({RESEARCH: _research_bars()})
    with pytest.raises(MarketDataValueError, match="does not match the manifest"):
        ReadableSnapshot(
            manifest=approved, directory_name="2024-acceptance", report=IntegrityReport()
        )


def test_an_unapproved_manifest_cannot_become_readable() -> None:
    approved = snapshots.approved_for({RESEARCH: _research_bars()})
    pending = snapshots.manifest(series_records=approved.series, partitions=approved.partitions)
    with pytest.raises(SnapshotNotApproved, match="has not been approved"):
        ReadableSnapshot(
            manifest=pending, directory_name=str(pending.snapshot_id()), report=IntegrityReport()
        )


def test_a_correctly_placed_approved_snapshot_is_readable() -> None:
    """条件を満たす snapshot は開ける（関門が正しいものまで拒まないことの確認）。"""
    approved = snapshots.approved_for({RESEARCH: _research_bars()})
    readable = ReadableSnapshot(
        manifest=approved, directory_name=str(approved.snapshot_id()), report=IntegrityReport()
    )
    assert readable.snapshot_id == approved.snapshot_id()


# --- 実行区間は許可期間の内側（D03 §6.1・§7.1）-----------------------------


def test_a_run_interval_outside_the_allowed_period_is_refused() -> None:
    """研究期間だけ許可した状態で封印期間を実行区間にすると拒否される。

    予定境界はデータ到着と独立に出るので、これを許すと禁止期間で戦略評価が起動する。
    """
    research = _research_bars()
    with pytest.raises(HoldoutAccessViolation, match="not covered by the partitions granted"):
        build_feed(
            snapshots.readable_for({RESEARCH: research}),
            frozenset({RESEARCH}),
            {RESEARCH: research},
            SCHEDULES,
            HOLDOUT_WINDOW,
        )


def test_a_run_interval_partly_outside_the_allowed_period_is_refused() -> None:
    """一部でもはみ出せば拒否する。"""
    research = _research_bars()
    overlapping = Interval(start=RESEARCH_WINDOW.start, end=HOLDOUT_WINDOW.end)
    with pytest.raises(HoldoutAccessViolation, match="not covered by the partitions granted"):
        build_feed(
            snapshots.readable_for({RESEARCH: research}),
            frozenset({RESEARCH}),
            {RESEARCH: research},
            SCHEDULES,
            overlapping,
        )


def test_a_run_interval_inside_the_allowed_period_is_accepted() -> None:
    """許可期間の内側なら通る（欠損した足の予定境界も従来どおり出る）。"""
    missing = UtcTime.parse("2023-06-01T02:00:00Z")
    bars = market.make_bars(HOURLY, market.TF_1H, CALENDAR, RESEARCH_WINDOW, skip_starts=(missing,))
    feed = build_feed(
        snapshots.readable_for({RESEARCH: bars}),
        frozenset({RESEARCH}),
        {RESEARCH: bars},
        SCHEDULES,
        RESEARCH_WINDOW,
    )
    boundaries = {
        event.bar_key.bar_start for event in feed.of_kind(PublicationKind.SCHEDULED_BOUNDARY)
    }
    assert missing in boundaries


def test_boundaries_are_produced_only_for_series_with_a_granted_partition() -> None:
    """許可 partition を持たない系列には予定境界を出さない（D03 §6.1）。"""
    research = _research_bars()
    daily = market.series(timeframe_id="1d_ny17")
    schedules = {
        HOURLY: SCHEDULES[HOURLY],
        daily: SeriesSchedule(series=daily, timeframe_def=market.TF_1D_NY17, calendar=CALENDAR),
    }
    feed = build_feed(
        snapshots.readable_for({RESEARCH: research}),
        frozenset({RESEARCH}),
        {RESEARCH: research},
        schedules,
        RESEARCH_WINDOW,
    )
    series_with_boundaries = {
        event.series for event in feed.of_kind(PublicationKind.SCHEDULED_BOUNDARY)
    }
    assert series_with_boundaries == {HOURLY}


def test_a_feed_without_any_granted_partition_is_refused() -> None:
    research = _research_bars()
    readable = snapshots.readable_for({RESEARCH: research})
    with pytest.raises(HoldoutAccessViolation, match="no partition was granted"):
        build_feed(readable, frozenset(), {}, SCHEDULES, RESEARCH_WINDOW)


# --- 報告と分類の突き合わせ（D03 §3.7.1 の2・§4 の 9）-----------------------


def _warned_report() -> IntegrityReport:
    """未分類の警告を1件持つ報告。"""
    return IntegrityReport(
        results=(CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, HOURLY, RESEARCH_WINDOW),)
    )


def _decision_for(report: IntegrityReport) -> ClosureDecision:
    """その報告の警告に対応する分類。"""
    (warning,) = report.warnings
    return ClosureDecision(
        series_id=HOURLY,
        interval=warning.interval,
        kind=ClosureDecisionKind.DATA_GAP,
    )


def test_an_unclassified_warning_makes_the_snapshot_unreadable() -> None:
    """未分類の警告が残る manifest は、最終識別子の名前で置いても読めない。

    確定段階（`finalize`）を飛ばして承認だけ付けた manifest がここで止まる（D03 §3.7.1 の2）。
    """
    report = _warned_report()
    manifest = _manifest_for(report)
    with pytest.raises(SnapshotNotApproved, match="still unclassified"):
        ReadableSnapshot(
            manifest=manifest,
            directory_name=str(manifest.snapshot_id()),
            report=report,
        )


def test_a_classified_warning_makes_the_snapshot_readable() -> None:
    """分類が記入されていれば読める（関門が正しいものまで拒まないことの確認）。"""
    report = _warned_report()
    decided = _manifest_for(report, (_decision_for(report),))
    readable = ReadableSnapshot(
        manifest=decided, directory_name=str(decided.snapshot_id()), report=report
    )
    assert readable.snapshot_id == decided.snapshot_id()


def test_a_decision_without_a_matching_warning_makes_it_unreadable() -> None:
    """報告に無い区間の分類が付いた manifest も拒否する（`finalize` と同じ規則）。"""
    extra = ClosureDecision(
        series_id=HOURLY,
        interval=HOLDOUT_WINDOW,
        kind=ClosureDecisionKind.CLOSURE,
    )
    decided = snapshots.approved_for({RESEARCH: _research_bars()}).with_closure_decisions((extra,))
    with pytest.raises(MarketDataValueError, match="do not correspond to any warning"):
        ReadableSnapshot(
            manifest=decided,
            directory_name=str(decided.snapshot_id()),
            report=IntegrityReport(),
        )


def test_the_matching_rule_is_shared_with_finalize() -> None:
    """突き合わせは共通の純粋関数で行う（確定と読み取りで規則がずれない）。"""
    report = _warned_report()
    (warning,) = report.warnings
    # 終端の違う分類は対応しない（`finalize` と同じ判定）。
    wrong_end = ClosureDecision(
        series_id=HOURLY,
        interval=Interval(start=warning.interval.start, end=HOLDOUT_WINDOW.end),
        kind=ClosureDecisionKind.DATA_GAP,
    )
    undecided, extraneous = classification_mismatch(report, (wrong_end,))
    assert undecided and extraneous

    # 区間が完全に一致すれば対応する。
    undecided, extraneous = classification_mismatch(report, (_decision_for(report),))
    assert not undecided and not extraneous


def _manifest_for(
    report: IntegrityReport, decisions: tuple[ClosureDecision, ...] = ()
) -> SnapshotManifest:
    """その報告を参照する承認済み manifest（ダイジェストを報告から作る）。

    `ReadableSnapshot` は報告のダイジェストが manifest の記録と一致することを要求する
    （D03 §3.7.1）ので、テストの manifest も報告から作る。
    """
    base = snapshots.approved_for({RESEARCH: _research_bars()})
    return SnapshotManifest(
        created_at=base.created_at,
        basis_declaration=base.basis_declaration,
        sources=base.sources,
        conversion=base.conversion,
        series=base.series,
        partitions=base.partitions,
        integrity_report_ref=ContentDigest.sha256(integrity_report_digest_hex(report)),
        closure_decisions=decisions,
        approval=base.approval,
    )


# --- 重大な違反を含む報告は読めない（D03 §4 の 4）--------------------------


def test_a_report_carrying_an_error_makes_the_snapshot_unreadable() -> None:
    """重大な違反を含む報告の snapshot は読めない。

    受入れは重大な違反で中断するが、報告ごと保存された snapshot を読む経路が残っていると、
    構造的に無効なデータがバックテストの入力になりうる。
    """
    report = IntegrityReport(
        results=(CheckResult.create(CheckKind.DUPLICATE_TIMESTAMP, HOURLY, RESEARCH_WINDOW),)
    )
    manifest = _manifest_for(report)
    with pytest.raises(SnapshotNotApproved, match="DUPLICATE_TIMESTAMP=1"):
        ReadableSnapshot(
            manifest=manifest, directory_name=str(manifest.snapshot_id()), report=report
        )


def test_the_error_message_reports_the_count_and_kinds() -> None:
    """どの検査で何件落ちたかが分かる（価格は含めない）。"""
    report = IntegrityReport(
        results=(
            CheckResult.create(CheckKind.DUPLICATE_TIMESTAMP, HOURLY, RESEARCH_WINDOW),
            CheckResult.create(CheckKind.IRREGULAR_INTERVAL, HOURLY, HOLDOUT_WINDOW),
        )
    )
    manifest = _manifest_for(report)
    with pytest.raises(SnapshotNotApproved) as raised:
        ReadableSnapshot(
            manifest=manifest, directory_name=str(manifest.snapshot_id()), report=report
        )
    message = str(raised.value)
    assert "2 error(s)" in message
    assert "DUPLICATE_TIMESTAMP=1" in message
    assert "IRREGULAR_INTERVAL=1" in message
    assert "150" not in message


def test_an_information_only_report_is_readable() -> None:
    """記録のみの結果（INFO）は読み取りを妨げない。"""
    report = IntegrityReport(
        results=(CheckResult.create(CheckKind.SOURCE_TRANSITION, HOURLY, RESEARCH_WINDOW),)
    )
    manifest = _manifest_for(report)
    readable = ReadableSnapshot(
        manifest=manifest, directory_name=str(manifest.snapshot_id()), report=report
    )
    assert readable.report == report


# --- 報告のダイジェストは型の中で検査する（D03 §3.7.1）--------------------


def test_a_report_that_is_not_this_snapshots_is_refused() -> None:
    """manifest が参照していない報告を直接渡しても拒否される。

    検査を呼び出し側（`open_readable`）にだけ置くと、型を直接組み立てる経路が素通りする。
    """
    manifest = snapshots.approved_for({RESEARCH: _research_bars()})
    other = IntegrityReport(
        results=(CheckResult.create(CheckKind.ZERO_VOLUME_SPAN, HOURLY, RESEARCH_WINDOW),)
    )
    with pytest.raises(MarketDataValueError, match="not this snapshot's report"):
        ReadableSnapshot(
            manifest=manifest, directory_name=str(manifest.snapshot_id()), report=other
        )


# --- 公開予定も差し替えられない（D03 §3.5・§6.2）---------------------------


def _delayed_schedule(minutes: int) -> SeriesSchedule:
    return SeriesSchedule(
        series=HOURLY,
        timeframe_def=market.TF_1H,
        calendar=CALENDAR,
        normal_publication_delay=timedelta(minutes=minutes),
    )


def test_swapping_the_schedule_does_not_change_the_as_of_view() -> None:
    """ビューを作った後に公開予定を差し替えても、読み取り結果は変わらない。

    通常遅延 5 分の予定で作ったビューの辞書を遅延 0 の予定へ差し替えると、足の終了と同時に
    その足が見えてしまう——先読み（D03 §6.2）。
    """
    research = _research_bars()
    mutable: dict[SeriesId, SeriesSchedule] = {HOURLY: _delayed_schedule(5)}
    view = AsOfView(
        snapshot=snapshots.readable_for({RESEARCH: research}),
        allowed_partitions=frozenset({RESEARCH}),
        schedules=mutable,
        partition_bars={RESEARCH: research},
    )
    bar_end = UtcTime.parse("2023-06-01T03:00:00Z")
    before = view.latest_available(HOURLY, bar_end)
    assert isinstance(before, MissingInput)  # 5 分遅れなのでまだ見えない。

    mutable[HOURLY] = _delayed_schedule(0)

    after = view.latest_available(HOURLY, bar_end)
    assert isinstance(after, MissingInput), "差し替えで先読みが起きてはいけない"
    # 本来の公開予定に達すれば見える。
    later = view.latest_available(HOURLY, bar_end + timedelta(minutes=5))
    assert not isinstance(later, MissingInput)


def test_the_view_exposes_its_schedules_as_immutable() -> None:
    """ビューが持つ公開予定の写しそのものも書き換えられない。"""
    research = _research_bars()
    view = AsOfView(
        snapshot=snapshots.readable_for({RESEARCH: research}),
        allowed_partitions=frozenset({RESEARCH}),
        schedules={HOURLY: _delayed_schedule(5)},
        partition_bars={RESEARCH: research},
    )
    with pytest.raises(TypeError):
        view.schedules[HOURLY] = _delayed_schedule(0)  # type: ignore[index]


def test_swapping_the_schedule_does_not_change_the_publication_feed() -> None:
    """公開フィードも同じ。構築中に差し替えても結果は変わらない。"""
    research = _research_bars()
    mutable: dict[SeriesId, SeriesSchedule] = {HOURLY: _delayed_schedule(5)}
    readable = snapshots.readable_for({RESEARCH: research})
    feed = build_feed(
        readable,
        frozenset({RESEARCH}),
        {RESEARCH: research},
        mutable,
        RESEARCH_WINDOW,
    )
    delayed_times = sorted(str(event.at) for event in feed.of_kind(PublicationKind.PUBLICATION))

    mutable[HOURLY] = _delayed_schedule(0)

    assert (
        sorted(str(event.at) for event in feed.of_kind(PublicationKind.PUBLICATION))
        == delayed_times
    )
