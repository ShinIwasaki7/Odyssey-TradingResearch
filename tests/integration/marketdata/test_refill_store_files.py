"""補充の置き場のファイルの書き込み規約（D03 §14.11.1 の W1〜W6）。

一時ディレクトリに置き場を作り、`FsRefillStore` を直接使う。

- W1: 計画のロック・時間のロックは排他的で、作成時刻・ホスト名・プロセス ID を書く。残った
  ロックは自動で外さない。外すのは自分が作ったものだけ。
- W2: `plan.json`・保管場所の 1 件は排他的に作成し、上書きしない。作業ディレクトリも排他的に
  作る（既にあれば `RefillPlanAlreadyExists`）。
- W3: 取得記録の行はダイジェストを持ち、不完全な最後の行・壊れた行を見分けられる。
- シンボリックリンクは辿らない。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.adapters.refill_store import FsRefillStore
from odyssey_fx.marketdata.domain.errors import (
    RefillPlanAlreadyExists,
    RefillPlanLocked,
    RefillPlanNotFound,
    RefillStoreInconsistent,
)
from odyssey_fx.marketdata.domain.refill import ArchiveProvenance, HourKey
from tests.fixtures.refill import BI5_00H, HOUR_00, decoded, provider_ref
from tests.fixtures.synthetic import market

PLAN = "a" * 64
KEY = HourKey(symbol=market.USDJPY, start=HOUR_00)


def _store(tmp_path: Path) -> FsRefillStore:
    return FsRefillStore(root=tmp_path / "refill")


def _provenance(body: bytes = BI5_00H) -> ArchiveProvenance:
    ticks = decoded(body)
    settings = provider_ref().settings
    import hashlib

    return ArchiveProvenance(
        url=settings.url_for(KEY),
        fetched_at=UtcTime.parse("2026-10-01T00:00:00Z"),
        http_status=200,
        attempts=1,
        response_sha256=hashlib.sha256(body).hexdigest(),
        tick_digest=ticks.tick_digest,
        tick_count=len(ticks.ticks),
        provider_id=settings.id,
        provider_version=settings.version,
        provider_content_digest=provider_ref().content_digest,
        source_digest=settings.source_digest(),
    )


def test_the_work_directory_is_created_exclusively(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_work_dir(PLAN)
    assert store.work_dir_exists(PLAN)
    with pytest.raises(RefillPlanAlreadyExists):
        store.create_work_dir(PLAN)


def test_plan_json_is_never_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_work_dir(PLAN)
    store.write_plan(PLAN, {"x": 1})
    with pytest.raises(RefillStoreInconsistent):
        store.write_plan(PLAN, {"x": 2})
    assert store.read_plan(PLAN) == {"x": 1}
    # 一時名のファイルは残らない（W2）。
    assert sorted(path.name for path in (tmp_path / "refill/_work" / PLAN).iterdir()) == [
        "plan.json"
    ]


def test_a_missing_plan_json_reads_as_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_work_dir(PLAN)
    assert store.read_plan(PLAN) is None


def test_the_plan_lock_is_exclusive_and_records_its_holder(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_work_dir(PLAN)
    store.acquire_plan_lock(PLAN)
    lock = tmp_path / "refill/_work" / PLAN / "lock"
    holder = json.loads(lock.read_text(encoding="utf-8"))
    assert {"created_at", "host", "pid"} <= set(holder)
    assert holder["pid"] == os.getpid()
    other = _store(tmp_path)
    with pytest.raises(RefillPlanLocked):
        other.acquire_plan_lock(PLAN)
    # 他人のロックは外さない。
    other.release_plan_lock(PLAN)
    assert lock.exists()
    store.release_plan_lock(PLAN)
    assert not lock.exists()


def test_a_lock_left_by_a_stopped_process_is_not_removed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_work_dir(PLAN)
    lock = tmp_path / "refill/_work" / PLAN / "lock"
    lock.write_text('{"pid": 1}\n', encoding="utf-8")
    with pytest.raises(RefillPlanLocked, match="delete the lock"):
        store.acquire_plan_lock(PLAN)
    assert lock.exists()


def test_a_plan_lock_needs_the_work_directory(tmp_path: Path) -> None:
    with pytest.raises(RefillPlanNotFound):
        _store(tmp_path).acquire_plan_lock(PLAN)


def test_journal_lines_carry_a_digest_and_a_broken_tail_is_seen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_work_dir(PLAN)
    store.append_journal(PLAN, {"kind": "pause_end", "at": "2026-10-01T00:00:00Z"})
    store.append_journal(PLAN, {"kind": "pause_end", "at": "2026-10-01T00:00:01Z"})
    journal = tmp_path / "refill/_work" / PLAN / "journal.jsonl"
    content = journal.read_bytes()
    journal.write_bytes(content + b'{"digest": "x", "en')  # 書き込みの途中で止まった
    lines = store.read_journal(PLAN)
    assert [line.entry is not None for line in lines] == [True, True, False]
    store.truncate_journal(PLAN, lines[-1].offset)
    assert journal.read_bytes() == content


def test_a_journal_line_whose_digest_does_not_match_is_broken(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_work_dir(PLAN)
    store.append_journal(PLAN, {"kind": "pause_end", "at": "2026-10-01T00:00:00Z"})
    journal = tmp_path / "refill/_work" / PLAN / "journal.jsonl"
    journal.write_bytes(journal.read_bytes().replace(b"00:00:00Z", b"00:00:09Z"))
    (line,) = store.read_journal(PLAN)
    assert line.entry is None
    assert "digest" in line.problem


def test_an_archived_hour_round_trips_and_is_never_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    provenance = _provenance()
    path = store.write_archive(KEY, BI5_00H, provenance)
    assert path == (
        f"_ticks/USDJPY/2020/11/30/00h/{provenance.source_digest}/{provenance.tick_digest}.json"
    )
    assert store.list_archive(KEY, provenance.source_digest) == (f"{provenance.tick_digest}.json",)
    read = store.read_archive(KEY, provenance.source_digest, provenance.tick_digest)
    assert read is not None
    assert read.provenance == provenance
    assert read.body_sha256 == provenance.response_sha256
    assert read.decoded is not None and read.decoded.tick_digest == provenance.tick_digest
    with pytest.raises(RefillStoreInconsistent):
        store.write_archive(KEY, BI5_00H, provenance)


def test_the_hour_lock_is_exclusive(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.acquire_hour_lock(KEY)
    assert not _store(tmp_path).acquire_hour_lock(KEY)
    store.release_hour_lock(KEY)
    assert _store(tmp_path).acquire_hour_lock(KEY)


def test_symbolic_links_are_not_followed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    (tmp_path / "refill").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "refill/_ticks").symlink_to(elsewhere)
    with pytest.raises(RefillStoreInconsistent):
        store.write_archive(KEY, BI5_00H, _provenance())
    assert list(elsewhere.iterdir()) == []


def test_a_linked_refill_root_is_not_followed(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "refill").symlink_to(elsewhere)
    store = _store(tmp_path)
    with pytest.raises(RefillStoreInconsistent, match="symbolic link"):
        store.create_work_dir(PLAN)
    with pytest.raises(RefillStoreInconsistent, match="symbolic link"):
        store.list_refills()
    assert list(elsewhere.iterdir()) == []


def test_a_linked_directory_on_the_way_to_the_root_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data/raw").symlink_to(elsewhere)
    monkeypatch.chdir(tmp_path)
    store = FsRefillStore(root=Path("data/raw/market/refill"))
    with pytest.raises(RefillStoreInconsistent, match="symbolic"):
        store.create_work_dir(PLAN)
    assert list(elsewhere.iterdir()) == []


def test_linked_files_in_the_work_directory_are_not_followed(tmp_path: Path) -> None:
    """ロック・取得記録・作業ディレクトリがリンクなら、読まず・書かずに止める（W6）。"""
    store = _store(tmp_path)
    store.create_work_dir(PLAN)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"\xff\xfe secret")
    work = tmp_path / "refill/_work" / PLAN
    (work / "lock").symlink_to(outside)
    with pytest.raises(RefillStoreInconsistent, match="symbolic link"):
        store.acquire_plan_lock(PLAN)
    (work / "lock").unlink()
    (work / "journal.jsonl").symlink_to(outside)
    with pytest.raises(RefillStoreInconsistent, match="symbolic link"):
        store.truncate_journal(PLAN, 0)
    with pytest.raises(RefillStoreInconsistent, match="symbolic link"):
        store.read_journal(PLAN)
    assert outside.read_bytes() == b"\xff\xfe secret"
    other = "b" * 64
    (tmp_path / "refill/_work" / other).symlink_to(tmp_path)
    with pytest.raises(RefillStoreInconsistent, match="symbolic link"):
        store.read_plan(other)


def test_refill_directories_report_their_plan_or_incompleteness(tmp_path: Path) -> None:
    root = tmp_path / "refill"
    complete = root / ("b" * 64)
    complete.mkdir(parents=True)
    (complete / "refill_manifest.json").write_text(json.dumps({"plan_id": PLAN}), encoding="utf-8")
    (root / ("c" * 64)).mkdir()
    (root / ".DS_Store").write_text("", encoding="utf-8")
    listed = {entry.name: entry.plan_id for entry in _store(tmp_path).list_refills()}
    assert listed == {"b" * 64: PLAN, "c" * 64: None}
