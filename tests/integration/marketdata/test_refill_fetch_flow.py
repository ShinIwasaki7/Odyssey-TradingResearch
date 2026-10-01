"""取得の流れ（D03 §14.9・§14.11.1・§14.12 の出来事1〜9）。

固定の応答を返す偽の取得元（仮想の時計）と、一時ディレクトリの補充の置き場で確かめる
（D03 §14.16）。実際の提供元へは通信しない。

- 計画を作る（存在すれば失敗）。取得して保管場所に置き、最終結果を追記する。
- 中断と再開（有効な最終結果のある時間は取らない。不完全な最後の行は切り詰める）。
- 間隔・再試行・指数バックオフ・連続失敗の一時停止。HTTP 404 は再試行せず `NOT_FETCHED`。
- `--retry-failed` は取得できなかった時間を取り直しの対象に戻す。
- 保管場所の再利用（通信しない）・消えた保管場所の無効化と取り直し・検算の食い違い。
- 二重起動の拒否（計画のロック）。書き出し済み・書きかけの補充分での拒否。
- 認証・権限・要求の誤りを示す 4xx で計画を止める。時間のロックの競合は未取得のまま残す。
- 書き出し済みの計画: 消えた保管場所のファイルの時間だけを取り直す（manifest と照合）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from odyssey_fx.marketdata.adapters.refill_store import FsRefillStore
from odyssey_fx.marketdata.application.refill_fetch import (
    PlanState,
    classify_status,
    derive_state,
    fetch_plan,
    read_journal,
)
from odyssey_fx.marketdata.application.refill_plan import build_plan, create_plan
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.errors import (
    RefillAlreadyFinalized,
    RefillPlanAlreadyExists,
    RefillPlanLocked,
    RefillPlanNotFound,
    RefillSourceRefused,
    RefillStoreInconsistent,
    RefillValidationFailed,
)
from odyssey_fx.marketdata.domain.refill import (
    AttemptRecord,
    FailureKind,
    FinalResult,
    HourOutcome,
    Invalidation,
    JournalEntry,
    ManifestHour,
    PauseStart,
    RefillFilter,
    RefillManifestCore,
    RefillPlan,
    RetryMark,
    ValidationRecord,
)
from tests.fixtures.refill import (
    BI5_00H,
    BI5_01H,
    HOUR_00,
    HOUR_01,
    REFILL_CALENDAR,
    FakeTickSource,
    calendar_ref,
    communication,
    gap_resolutions,
    manifest_for,
    provider_ref,
    raw_bars,
    url_of,
)
from tests.fixtures.synthetic import market

URL_00 = url_of(HOUR_00)
URL_01 = url_of(HOUR_01)


def _plan(**comm: int) -> RefillPlan:
    return build_plan(
        manifest=manifest_for(gap_resolutions()),
        raw_bars=raw_bars(),
        calendar=REFILL_CALENDAR,
        calendar_ref=calendar_ref(),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        provider=provider_ref(comm=communication(**comm)),
        refill_filter=RefillFilter(),
    )


def _setup(tmp_path: Path, **comm: int) -> tuple[FsRefillStore, str, RefillPlan]:
    store = FsRefillStore(root=tmp_path / "refill")
    plan = _plan(**comm)
    return store, create_plan(plan, store), plan


def _entries(store: FsRefillStore, plan_id: str, plan: RefillPlan) -> list[JournalEntry]:
    return list(read_journal(plan_id, store.read_journal(plan_id), plan).entries)


def _finals(store: FsRefillStore, plan_id: str, plan: RefillPlan) -> list[FinalResult]:
    return [entry for entry in _entries(store, plan_id, plan) if isinstance(entry, FinalResult)]


# --- 計画（出来事1）--------------------------------------------------------------------


def test_the_plan_is_written_once(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    assert plan_id == plan.plan_id()
    plan_json = tmp_path / "refill/_work" / plan_id / "plan.json"
    assert json.loads(plan_json.read_text(encoding="utf-8"))["snapshot_id"] == plan.snapshot_id
    with pytest.raises(RefillPlanAlreadyExists):
        create_plan(plan, store)


def test_an_incomplete_work_directory_blocks_the_plan(tmp_path: Path) -> None:
    store = FsRefillStore(root=tmp_path / "refill")
    plan = _plan()
    (tmp_path / "refill/_work" / plan.plan_id()).mkdir(parents=True)
    with pytest.raises(RefillStoreInconsistent, match="no plan.json"):
        create_plan(plan, store)


# --- 取得と再開（出来事2〜5・8）---------------------------------------------------------


def test_fetching_archives_each_hour_and_appends_its_final_result(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    source = FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]})
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    assert report.state_before is PlanState.PLANNED
    assert report.state_after is PlanState.FETCH_DONE
    finals = _finals(store, plan_id, plan)
    assert [(str(final.hour.start), final.outcome) for final in finals] == [
        ("2020-11-30T00:00:00Z", HourOutcome.FETCHED),
        ("2020-11-30T01:00:00Z", HourOutcome.FETCHED),
    ]
    for final in finals:
        assert final.archive_file is not None
        assert (tmp_path / "refill" / final.archive_file).is_file()
    # 計画のロックは外れている。
    assert not (tmp_path / "refill/_work" / plan_id / "lock").exists()


def test_requests_keep_the_interval(tmp_path: Path) -> None:
    store, plan_id, _ = _setup(tmp_path)
    source = FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]})
    fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    (first, _), (second, _) = source.requests
    # 偽の要求は 1 秒かかるので、終わりから次の始まりまでがちょうど 8 秒になる。
    assert (second - first).total_seconds() == 1 + 8


def test_resuming_fetches_only_the_hours_without_a_final_result(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path, max_retries=0)
    first = FakeTickSource({URL_00: [BI5_00H], URL_01: [FailureKind.TIMEOUT]})
    fetch_plan(plan_id, store=store, source=first, retry_failed=False)
    second = FakeTickSource({})
    report = fetch_plan(plan_id, store=store, source=second, retry_failed=False)
    assert second.requests == []  # NOT_FETCHED も最終結果なので取り直さない
    assert report.skipped == 2
    assert report.state_after is PlanState.FETCH_DONE


def test_the_journal_summary_spans_interruptions(tmp_path: Path) -> None:
    """代表例の試行の集計は取得記録全体から作る（中断・再開をまたぐ。D03 §14.9）。"""
    store, plan_id, _ = _setup(tmp_path, max_retries=1)
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [429, BI5_00H], URL_01: [404]}),
        retry_failed=False,
    )
    report = fetch_plan(
        plan_id, store=store, source=FakeTickSource({URL_01: [BI5_01H]}), retry_failed=True
    )
    assert report.requests == 1  # この回の要求だけ
    journal = report.journal
    assert journal is not None
    assert journal.requests == 4  # 429・00 時の成功・404・01 時の成功
    assert dict(journal.failures) == {FailureKind.HTTP_429: 1, FailureKind.HTTP_404: 1}
    assert dict(journal.statuses) == {429: 1, 404: 1}
    assert dict(journal.outcomes) == {HourOutcome.FETCHED: 2}


def test_http_statuses_are_counted_one_by_one(tmp_path: Path) -> None:
    """503 は 500 と分けて数える（D03 §14.9 の試行の集計）。"""
    store, plan_id, _ = _setup(tmp_path, max_retries=2, pause_after_consecutive_failures=10)
    report = fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [500, 503, BI5_00H], URL_01: [503, BI5_01H]}),
        retry_failed=False,
    )
    assert dict(report.statuses) == {500: 1, 503: 2}
    assert report.journal is not None
    assert dict(report.journal.statuses) == {500: 1, 503: 2}
    assert report.failures[FailureKind.HTTP_5XX] == 3


def test_a_broken_last_line_is_truncated_and_the_hour_fetched_again(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    source = FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]})
    fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    journal = tmp_path / "refill/_work" / plan_id / "journal.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    # 最後の最終結果（01 時）の行を書き込みの途中で止まった形にする。
    journal.write_bytes(b"".join(lines[:-1]) + lines[-1][:30])
    again = FakeTickSource({})
    report = fetch_plan(plan_id, store=store, source=again, retry_failed=False)
    # 01 時は保管場所に完成したものがあるので、通信せずに読んで最終結果を書き直す。
    assert again.requests == []
    assert report.from_archive == 1
    assert [final.from_archive for final in _finals(store, plan_id, plan)] == [False, True]


def test_a_broken_middle_line_stops_with_inconsistency(tmp_path: Path) -> None:
    store, plan_id, _ = _setup(tmp_path)
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]}),
        retry_failed=False,
    )
    journal = tmp_path / "refill/_work" / plan_id / "journal.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    journal.write_bytes(b"garbage\n" + b"".join(lines))
    with pytest.raises(RefillStoreInconsistent, match="line 1"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)


# --- 再試行・バックオフ・一時停止・404（出来事4〜7）---------------------------------------


def test_retries_back_off_and_end_in_not_fetched(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path, max_retries=2, pause_after_consecutive_failures=10)
    source = FakeTickSource(
        {URL_00: [BI5_00H], URL_01: [FailureKind.TIMEOUT, 503, FailureKind.CONNECTION]}
    )
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    times = [moment for moment, url in source.requests if url == URL_01]
    gaps = [
        (later - earlier).total_seconds() - 1
        for earlier, later in zip(times, times[1:], strict=False)
    ]
    assert gaps == [30, 60]  # 指数バックオフ（間隔 8 秒より長い待ちが優先）
    final = _finals(store, plan_id, plan)[-1]
    assert final.outcome is HourOutcome.NOT_FETCHED
    assert final.attempts == 3
    assert final.failure is FailureKind.CONNECTION
    attempts = [
        entry for entry in _entries(store, plan_id, plan) if isinstance(entry, AttemptRecord)
    ]
    assert [entry.failure for entry in attempts] == [
        FailureKind.TIMEOUT,
        FailureKind.HTTP_5XX,
        FailureKind.CONNECTION,
    ]
    assert report.failures[FailureKind.HTTP_5XX] == 1


def test_http_404_is_not_retried(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    source = FakeTickSource({URL_00: [BI5_00H], URL_01: [404]})
    fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    final = _finals(store, plan_id, plan)[-1]
    assert (final.outcome, final.failure, final.http_status) == (
        HourOutcome.NOT_FETCHED,
        FailureKind.HTTP_404,
        404,
    )
    assert source.remaining() == {}


def test_consecutive_failures_pause_across_hours(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path, max_retries=1, pause_after_consecutive_failures=3)
    source = FakeTickSource(
        {URL_00: [429, FailureKind.TIMEOUT], URL_01: [FailureKind.TIMEOUT, BI5_01H]}
    )
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    pauses = [entry for entry in _entries(store, plan_id, plan) if isinstance(entry, PauseStart)]
    assert len(pauses) == 1 and pauses[0].seconds == 180
    assert report.pauses == 1
    assert 180 in source.waits
    assert [final.outcome for final in _finals(store, plan_id, plan)] == [
        HourOutcome.NOT_FETCHED,
        HourOutcome.FETCHED,
    ]


def test_the_last_failed_retry_still_pauses_before_the_next_hour(tmp_path: Path) -> None:
    """上限に達した最後の失敗で連続失敗が数に達したら、次の時間ファイルの前に一時停止する。"""
    store, plan_id, plan = _setup(tmp_path, max_retries=2, pause_after_consecutive_failures=3)
    source = FakeTickSource(
        {URL_00: [FailureKind.TIMEOUT, FailureKind.TIMEOUT, FailureKind.TIMEOUT], URL_01: [BI5_01H]}
    )
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    assert report.pauses == 1
    last_00 = [moment for moment, url in source.requests if url == URL_00][-1]
    (first_01,) = [moment for moment, url in source.requests if url == URL_01]
    assert (first_01 - last_00).total_seconds() >= 1 + 180
    finals = _finals(store, plan_id, plan)
    assert [final.outcome for final in finals] == [HourOutcome.NOT_FETCHED, HourOutcome.FETCHED]


def test_an_empty_response_is_fetched_empty(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    source = FakeTickSource({URL_00: [BI5_00H], URL_01: [b""]})
    fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    assert _finals(store, plan_id, plan)[-1].outcome is HourOutcome.FETCHED_EMPTY


# --- 取り直し（出来事9）----------------------------------------------------------------


def test_retry_failed_marks_and_refetches_the_failed_hours(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path, max_retries=0)
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [FailureKind.TIMEOUT]}),
        retry_failed=False,
    )
    source = FakeTickSource({URL_01: [BI5_01H]})
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=True)
    assert report.retry_marks == 1
    entries = _entries(store, plan_id, plan)
    assert any(isinstance(entry, RetryMark) for entry in entries)
    assert derive_state(plan, entries, finalized=False) is PlanState.FETCH_DONE
    assert _finals(store, plan_id, plan)[-1].outcome is HourOutcome.FETCHED


def test_retry_failed_on_a_planned_plan_does_nothing(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    source = FakeTickSource({})
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=True)
    assert report.state_before is PlanState.PLANNED
    assert source.requests == []
    assert _entries(store, plan_id, plan) == []


def test_retry_failed_after_a_rejection_without_failed_hours_is_refused(tmp_path: Path) -> None:
    store, plan_id, _ = _setup(tmp_path)
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]}),
        retry_failed=False,
    )
    rejected = ValidationRecord(passed=False, reasons=("mismatch",), at=FakeTickSource({}).now())
    store.append_journal(plan_id, rejected.payload())
    with pytest.raises(RefillValidationFailed):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=True)


# --- 保管場所（D03 §14.5・W5）------------------------------------------------------------


def test_another_plan_reuses_the_archive_without_requests(tmp_path: Path) -> None:
    store, plan_id, _ = _setup(tmp_path)
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]}),
        retry_failed=False,
    )
    # 通信の値だけを変えた別の計画（別の plan_id、同じ <source_digest>）。
    other = _plan(request_interval_seconds=20)
    other_id = create_plan(other, store)
    assert other_id != plan_id
    source = FakeTickSource({})
    report = fetch_plan(other_id, store=store, source=source, retry_failed=False)
    assert source.requests == []
    assert report.from_archive == 2
    assert all(final.from_archive for final in _finals(store, other_id, other))


def test_a_deleted_archive_file_invalidates_its_final_result(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]}),
        retry_failed=False,
    )
    archived = _finals(store, plan_id, plan)[-1].archive_file
    assert archived is not None
    (tmp_path / "refill" / archived).unlink()
    source = FakeTickSource({URL_01: [BI5_01H]})
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    assert report.invalidations == 1
    assert [url for _, url in source.requests] == [URL_01]
    assert report.state_after is PlanState.FETCH_DONE


def test_an_archive_moved_to_another_hour_is_inconsistent(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]}),
        retry_failed=False,
    )
    # 01 時のファイルを 00 時のディレクトリへ写して 00 時のものと差し替える。
    finals = _finals(store, plan_id, plan)
    assert finals[0].archive_file and finals[1].archive_file
    zero = tmp_path / "refill" / finals[0].archive_file
    one = tmp_path / "refill" / finals[1].archive_file
    zero.unlink()
    shutil.copy(one, zero.parent / one.name)
    other = _plan(request_interval_seconds=20)
    other_id = create_plan(other, store)
    with pytest.raises(RefillStoreInconsistent, match="copied or moved"):
        fetch_plan(other_id, store=store, source=FakeTickSource({}), retry_failed=False)


def test_a_tampered_archive_is_inconsistent(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]}),
        retry_failed=False,
    )
    archived = _finals(store, plan_id, plan)[0].archive_file
    assert archived is not None
    path = tmp_path / "refill" / archived
    record = json.loads(path.read_text(encoding="utf-8"))
    record["provenance"]["tick_count"] += 1
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RefillStoreInconsistent, match="tick count"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)


def test_a_locked_hour_stays_unfetched_without_using_retries(tmp_path: Path) -> None:
    """時間のロックの競合は再試行の回数を使わず、別の理由で記録して未取得のまま残す。"""
    store, plan_id, plan = _setup(tmp_path, max_retries=1)
    holder = FsRefillStore(root=tmp_path / "refill")
    locked_hour = plan.hours[1].hour
    assert holder.acquire_hour_lock(locked_hour)
    source = FakeTickSource({URL_00: [BI5_00H]})
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    assert [url for _, url in source.requests] == [URL_00]
    assert report.hour_locked == 1
    assert source.waits == []  # バックオフで待たない
    finals = _finals(store, plan_id, plan)
    assert [final.hour for final in finals] == [plan.hours[0].hour]  # NOT_FETCHED にしない
    attempts = [
        entry for entry in _entries(store, plan_id, plan) if isinstance(entry, AttemptRecord)
    ]
    assert [(entry.hour, entry.failure, entry.attempt) for entry in attempts] == [
        (locked_hour, FailureKind.HOUR_LOCKED, 0)
    ]
    assert report.state_after is PlanState.FETCHING
    # ロックが外れた後に同じ計画をもう一度取得すれば取る。
    holder.release_hour_lock(locked_hour)
    again = FakeTickSource({URL_01: [BI5_01H]})
    report = fetch_plan(plan_id, store=store, source=again, retry_failed=False)
    assert report.state_after is PlanState.FETCH_DONE
    assert _finals(store, plan_id, plan)[-1].outcome is HourOutcome.FETCHED


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, FailureKind.HTTP_AUTH),
        (403, FailureKind.HTTP_AUTH),
        (400, FailureKind.HTTP_CLIENT),
        (410, FailureKind.HTTP_CLIENT),
    ],
)
def test_an_auth_or_request_error_stops_the_plan(
    tmp_path: Path, status: int, kind: FailureKind
) -> None:
    """認証・権限・要求の誤りを示す 4xx は欠落として続けず、計画を止めて原因を表示する。"""
    store, plan_id, plan = _setup(tmp_path)
    source = FakeTickSource({URL_00: [status], URL_01: [BI5_01H]})
    with pytest.raises(RefillSourceRefused, match=f"HTTP {status}"):
        fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    assert [url for _, url in source.requests] == [URL_00]  # 次の時間へ進まない
    assert _finals(store, plan_id, plan) == []  # 最終結果を書かない
    attempts = [
        entry for entry in _entries(store, plan_id, plan) if isinstance(entry, AttemptRecord)
    ]
    assert [(entry.failure, entry.http_status) for entry in attempts] == [(kind, status)]
    assert not (tmp_path / "refill/_work" / plan_id / "lock").exists()
    for hour in plan.hour_keys:
        assert not (tmp_path / "refill" / hour.archive_directory / "lock").exists()
    # 原因を直してから同じ計画で再開すれば取り直す。
    report = fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]}),
        retry_failed=False,
    )
    assert report.state_after is PlanState.FETCH_DONE


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (200, None),
        (404, FailureKind.HTTP_404),
        (429, FailureKind.HTTP_429),
        (401, FailureKind.HTTP_AUTH),
        (403, FailureKind.HTTP_AUTH),
        (400, FailureKind.HTTP_CLIENT),
        (451, FailureKind.HTTP_CLIENT),
        (500, FailureKind.HTTP_5XX),
        (503, FailureKind.HTTP_5XX),
        (302, FailureKind.HTTP_OTHER),
    ],
)
def test_statuses_are_classified(status: int, kind: FailureKind | None) -> None:
    assert classify_status(status) is kind


def test_unreadable_content_after_the_retries_is_invalid_content(tmp_path: Path) -> None:
    """読めない中身の再試行が上限に達したら理由は INVALID_CONTENT（tick 不在と区別する）。"""
    store, plan_id, plan = _setup(tmp_path, max_retries=1, pause_after_consecutive_failures=10)
    source = FakeTickSource({URL_00: [BI5_00H], URL_01: [b"not bi5", b"still not bi5"]})
    fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    final = _finals(store, plan_id, plan)[-1]
    assert (final.outcome, final.failure, final.attempts) == (
        HourOutcome.NOT_FETCHED,
        FailureKind.INVALID_CONTENT,
        2,
    )
    assert final.failure is not FailureKind.HTTP_404


# --- 拒否（W1・W6・書き出し済み）----------------------------------------------------------


def test_a_second_fetch_of_the_same_plan_is_refused(tmp_path: Path) -> None:
    store, plan_id, _ = _setup(tmp_path)
    holder = FsRefillStore(root=tmp_path / "refill")
    holder.acquire_plan_lock(plan_id)
    with pytest.raises(RefillPlanLocked):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)


def test_an_unknown_plan_is_not_found(tmp_path: Path) -> None:
    store = FsRefillStore(root=tmp_path / "refill")
    with pytest.raises(RefillPlanNotFound):
        fetch_plan("d" * 64, store=store, source=FakeTickSource({}), retry_failed=False)


def test_an_incomplete_refill_directory_blocks_fetching(tmp_path: Path) -> None:
    store, plan_id, _ = _setup(tmp_path)
    (tmp_path / "refill" / ("e" * 64)).mkdir()
    with pytest.raises(RefillStoreInconsistent, match="incomplete refill"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)
    # 拒否してもロックは残らない。
    assert not (tmp_path / "refill/_work" / plan_id / "lock").exists()


# --- 書き出し済みの計画（D03 §14.12 の書き出し済み×出来事2。仮置きの 14・15 への決定）-----


def _fetch_all(store: FsRefillStore, plan_id: str) -> None:
    fetch_plan(
        plan_id,
        store=store,
        source=FakeTickSource({URL_00: [BI5_00H], URL_01: [BI5_01H]}),
        retry_failed=False,
    )


def _write_manifest(
    tmp_path: Path,
    store: FsRefillStore,
    plan_id: str,
    plan: RefillPlan,
    name: str = "f" * 64,
    payload: object | None = None,
) -> Path:
    """取得記録の有効な最終結果から、PR 1 の範囲の補充分の manifest を置く（書き出しの代わり）。"""
    if payload is None:
        finals = {final.hour: final for final in _finals(store, plan_id, plan)}
        core = RefillManifestCore(
            plan_id=plan_id,
            hours=tuple(
                ManifestHour(
                    hour=hour, outcome=finals[hour].outcome, tick_digest=finals[hour].tick_digest
                )
                for hour in sorted(plan.hour_keys, key=lambda key: key.sort_key())
            ),
        )
        payload = {**core.payload(), "refill_id": name, "extended_by_pr2": True}
    refill = tmp_path / "refill" / name
    refill.mkdir()
    manifest = refill / "refill_manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "lock"
    }


def test_a_finalized_plan_without_missing_files_is_not_fetched_again(tmp_path: Path) -> None:
    """欠落なし: 保管場所の tick が揃っていれば `RefillAlreadyFinalized` で何も書かない。"""
    store, plan_id, plan = _setup(tmp_path)
    _fetch_all(store, plan_id)
    _write_manifest(tmp_path, store, plan_id, plan)
    before = _snapshot_tree(tmp_path / "refill")
    with pytest.raises(RefillAlreadyFinalized, match="no archive file is missing"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)
    with pytest.raises(RefillAlreadyFinalized, match="retry-failed"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=True)
    # 作業ディレクトリが残っていれば、計画の作り直しは RefillPlanAlreadyExists（D03 §14.12）。
    with pytest.raises(RefillPlanAlreadyExists):
        create_plan(plan, store)
    assert _snapshot_tree(tmp_path / "refill") == before


def test_a_finalized_plan_restores_only_the_missing_hour(tmp_path: Path) -> None:
    """1 時間だけ消失し同じ内容を再取得: 無効化の行を記録し、その時間だけを取り直す。"""
    store, plan_id, plan = _setup(tmp_path)
    _fetch_all(store, plan_id)
    manifest = _write_manifest(tmp_path, store, plan_id, plan)
    finals = _finals(store, plan_id, plan)
    kept, lost = finals[0].archive_file, finals[1].archive_file
    assert kept is not None and lost is not None
    kept_bytes = (tmp_path / "refill" / kept).read_bytes()
    manifest_bytes = manifest.read_bytes()
    (tmp_path / "refill" / lost).unlink()

    source = FakeTickSource({URL_01: [BI5_01H]})
    report = fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    assert [url for _, url in source.requests] == [URL_01]
    assert (report.state_before, report.state_after) == (PlanState.FINALIZED, PlanState.FINALIZED)
    assert report.restoring and report.invalidations == 1 and report.skipped == 1
    entries = _entries(store, plan_id, plan)
    invalidations = [entry for entry in entries if isinstance(entry, Invalidation)]
    assert [entry.hour for entry in invalidations] == [plan.hours[1].hour]
    assert (tmp_path / "refill" / lost).is_file()  # 同じ場所に元に戻った
    # 既存の補充分と残っている保管場所のファイルは上書きしない。
    assert (tmp_path / "refill" / kept).read_bytes() == kept_bytes
    assert manifest.read_bytes() == manifest_bytes
    # 揃った後は再び拒否する。
    with pytest.raises(RefillAlreadyFinalized):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)


def test_a_finalized_plan_refuses_different_ticks(tmp_path: Path) -> None:
    """異なる内容が返る: 保存せずに `RefillStoreInconsistent`（訂正版を取り込まない）。"""
    store, plan_id, plan = _setup(tmp_path)
    _fetch_all(store, plan_id)
    _write_manifest(tmp_path, store, plan_id, plan)
    lost = _finals(store, plan_id, plan)[1].archive_file
    assert lost is not None
    lost_dir = (tmp_path / "refill" / lost).parent
    (tmp_path / "refill" / lost).unlink()
    source = FakeTickSource({URL_01: [BI5_00H]})  # 別の時間の tick（内容が違う）
    with pytest.raises(RefillStoreInconsistent, match="provider correction is not taken in"):
        fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    assert list(lost_dir.iterdir()) == []  # 保管しない
    assert not (tmp_path / "refill/_work" / plan_id / "lock").exists()
    assert not (lost_dir.parent / "lock").exists()
    # 同じ内容が返れば、その後に元に戻せる。
    report = fetch_plan(
        plan_id, store=store, source=FakeTickSource({URL_01: [BI5_01H]}), retry_failed=False
    )
    assert report.state_after is PlanState.FINALIZED
    assert (tmp_path / "refill" / lost).is_file()


def test_a_failed_restore_leaves_the_hour_unfetched(tmp_path: Path) -> None:
    """取り直しで取得できなければ `NOT_FETCHED` を書かず、未取得のまま次の fetch に残す。"""
    store, plan_id, plan = _setup(tmp_path, max_retries=0)
    _fetch_all(store, plan_id)
    _write_manifest(tmp_path, store, plan_id, plan)
    lost = _finals(store, plan_id, plan)[1].archive_file
    assert lost is not None
    (tmp_path / "refill" / lost).unlink()
    report = fetch_plan(
        plan_id, store=store, source=FakeTickSource({URL_01: [404]}), retry_failed=False
    )
    assert report.not_restored == 1
    assert [final.outcome for final in _finals(store, plan_id, plan)] == [
        HourOutcome.FETCHED,
        HourOutcome.FETCHED,
    ]
    report = fetch_plan(
        plan_id, store=store, source=FakeTickSource({URL_01: [BI5_01H]}), retry_failed=False
    )
    assert report.not_restored == 0
    assert (tmp_path / "refill" / lost).is_file()


@pytest.mark.parametrize(
    "case",
    [
        "plan_id_only",  # 時間の記録が無い（plan_id の鍵だけ）
        "no_hours",
        "hours_differ_from_plan",
        "bad_digest",
        "not_json",  # 書きかけ・壊れた manifest
    ],
)
def test_an_invalid_manifest_is_inconsistent(tmp_path: Path, case: str) -> None:
    """manifest が不正: `RefillStoreInconsistent` で何も書かない（plan_id だけで判定しない）。"""
    store, plan_id, plan = _setup(tmp_path)
    _fetch_all(store, plan_id)
    hours = [
        {
            "hour": final.hour.payload(),
            "outcome": final.outcome.value,
            "tick_digest": final.tick_digest,
        }
        for final in _finals(store, plan_id, plan)
    ]
    payloads: dict[str, object] = {
        "plan_id_only": {"plan_id": plan_id},
        "no_hours": {"plan_id": plan_id, "hours": []},
        "hours_differ_from_plan": {"plan_id": plan_id, "hours": hours[:1]},
        "bad_digest": {"plan_id": plan_id, "hours": [hours[0], {**hours[1], "tick_digest": "0"}]},
        "not_json": {},
    }
    manifest = _write_manifest(tmp_path, store, plan_id, plan, payload=payloads[case])
    if case == "not_json":
        manifest.write_text('{"plan_id": "' + plan_id + '", "hours": [', encoding="utf-8")
    before = _snapshot_tree(tmp_path / "refill")
    with pytest.raises(RefillStoreInconsistent, match="refill_manifest.json|incomplete refill"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)
    with pytest.raises(RefillStoreInconsistent, match="refill_manifest.json|incomplete refill"):
        create_plan(plan, store)
    assert _snapshot_tree(tmp_path / "refill") == before


def test_several_refills_of_the_plan_must_agree(tmp_path: Path) -> None:
    """同じ計画の補充分が複数: 記録が同じなら取り直しに使い、違えば食い違いで止める。"""
    store, plan_id, plan = _setup(tmp_path)
    _fetch_all(store, plan_id)
    first = _write_manifest(tmp_path, store, plan_id, plan, name="a" * 64)
    second = _write_manifest(tmp_path, store, plan_id, plan, name="b" * 64)
    lost = _finals(store, plan_id, plan)[1].archive_file
    assert lost is not None
    (tmp_path / "refill" / lost).unlink()
    report = fetch_plan(
        plan_id, store=store, source=FakeTickSource({URL_01: [BI5_01H]}), retry_failed=False
    )
    assert report.state_after is PlanState.FINALIZED and report.invalidations == 1
    # 2 つ目の記録だけが違えば止める。
    record = json.loads(second.read_text(encoding="utf-8"))
    record["hours"][1]["tick_digest"] = record["hours"][0]["tick_digest"]
    second.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RefillStoreInconsistent, match="record different hour results"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)
    assert first.is_file()


def test_a_journal_that_disagrees_with_the_manifest_is_inconsistent(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    _fetch_all(store, plan_id)
    manifest = _write_manifest(tmp_path, store, plan_id, plan)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["hours"][0]["tick_digest"] = "0" * 64
    manifest.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RefillStoreInconsistent, match="differs from the finalized refill"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)


def test_a_finalized_plan_without_its_work_directory_is_refused(tmp_path: Path) -> None:
    """作業ディレクトリも消えていれば取り直さずに拒否する（復旧の手順は D03 で未定）。"""
    store, plan_id, plan = _setup(tmp_path)
    _fetch_all(store, plan_id)
    _write_manifest(tmp_path, store, plan_id, plan)
    shutil.rmtree(tmp_path / "refill/_work" / plan_id)
    with pytest.raises(RefillAlreadyFinalized, match="work directory is gone"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)
    with pytest.raises(RefillAlreadyFinalized):
        create_plan(plan, store)


def test_a_broken_last_line_is_kept_when_an_archive_is_inconsistent(tmp_path: Path) -> None:
    """検算で止まるときは、壊れた最後の行の切り詰めも含めて何も書かない（W5・W6）。"""
    store, plan_id, plan = _setup(tmp_path)
    _fetch_all(store, plan_id)
    journal = tmp_path / "refill/_work" / plan_id / "journal.jsonl"
    journal.write_bytes(journal.read_bytes() + b'{"broken')
    archived = _finals(store, plan_id, plan)[0].archive_file
    assert archived is not None
    path = tmp_path / "refill" / archived
    record = json.loads(path.read_text(encoding="utf-8"))
    record["provenance"]["tick_count"] += 1
    path.write_text(json.dumps(record), encoding="utf-8")
    before = journal.read_bytes()
    with pytest.raises(RefillStoreInconsistent, match="tick count"):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)
    assert journal.read_bytes() == before
