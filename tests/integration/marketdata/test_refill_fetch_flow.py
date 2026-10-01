"""取得の流れ（D03 §14.9・§14.11.1・§14.12 の出来事1〜9）。

固定の応答を返す偽の取得元（仮想の時計）と、一時ディレクトリの補充の置き場で確かめる
（D03 §14.16）。実際の提供元へは通信しない。

- 計画を作る（存在すれば失敗）。取得して保管場所に置き、最終結果を追記する。
- 中断と再開（有効な最終結果のある時間は取らない。不完全な最後の行は切り詰める）。
- 間隔・再試行・指数バックオフ・連続失敗の一時停止。HTTP 404 は再試行せず `NOT_FETCHED`。
- `--retry-failed` は取得できなかった時間を取り直しの対象に戻す。
- 保管場所の再利用（通信しない）・消えた保管場所の無効化と取り直し・検算の食い違い。
- 二重起動の拒否（計画のロック）。書き出し済み・書きかけの補充分での拒否。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from odyssey_fx.marketdata.adapters.refill_store import FsRefillStore
from odyssey_fx.marketdata.application.refill_fetch import (
    PlanState,
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
    RefillStoreInconsistent,
    RefillValidationFailed,
)
from odyssey_fx.marketdata.domain.refill import (
    AttemptRecord,
    FailureKind,
    FinalResult,
    HourOutcome,
    JournalEntry,
    PauseStart,
    RefillFilter,
    RefillPlan,
    RetryMark,
    ValidationRecord,
)
from tests.fixtures.refill import (
    BI5_00H,
    BI5_01H,
    HOUR_00,
    HOUR_01,
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
        calendar=market.calendar(),
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


def test_a_locked_hour_is_retried_without_requests(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path, max_retries=1)
    holder = FsRefillStore(root=tmp_path / "refill")
    assert holder.acquire_hour_lock(plan.hours[1].hour)
    source = FakeTickSource({URL_00: [BI5_00H]})
    fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    final = _finals(store, plan_id, plan)[-1]
    assert (final.outcome, final.failure) == (HourOutcome.NOT_FETCHED, FailureKind.HOUR_LOCKED)
    assert [url for _, url in source.requests] == [URL_00]


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


def test_a_finalized_plan_is_not_fetched_again(tmp_path: Path) -> None:
    store, plan_id, plan = _setup(tmp_path)
    refill = tmp_path / "refill" / ("f" * 64)
    refill.mkdir()
    (refill / "refill_manifest.json").write_text(json.dumps({"plan_id": plan_id}), encoding="utf-8")
    with pytest.raises(RefillAlreadyFinalized):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)
    # 作業ディレクトリを消していても、計画を作る前に補充分を探して拒否する。
    shutil.rmtree(tmp_path / "refill/_work" / plan_id)
    with pytest.raises(RefillAlreadyFinalized):
        create_plan(plan, store)
    with pytest.raises(RefillAlreadyFinalized):
        fetch_plan(plan_id, store=store, source=FakeTickSource({}), retry_failed=False)
