"""審査台帳の読み書き（D16 §10.2・§11.3。試行台帳と同じ規約。受入テスト T10・T15・T16 の層）。

台帳は一時ディレクトリのリポジトリの根に作り、行は人工の値（数値を持たない）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.evaluation.adapters import review_store
from odyssey_fx.evaluation.adapters.review_store import (
    ADOPTION_REVIEW_LEDGER_PATH,
    ADOPTION_REVIEW_LOCK_PATH,
    REVIEW_RECORD_FILE,
    ReviewAppendOutcome,
    ReviewAppendRefusal,
    ReviewAppendRefused,
    ReviewLedgerContents,
    ReviewLedgerReadFailure,
    append_review_ledger_file,
    read_review_ledger_file,
    review_line_bytes,
    write_review_record,
)
from odyssey_fx.evaluation.domain.adoption_review import (
    AdoptionReviewLine,
    ReviewDefect,
    ReviewResult,
)
from tests.fixtures.evaluation.adoption_review import review_entry, review_lines

_ID = ExperimentId(digest("experiment"))


def _ledger(root: Path, data: bytes = b"") -> Path:
    path = root / ADOPTION_REVIEW_LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _read(root: Path) -> ReviewLedgerContents:
    contents = read_review_ledger_file(root / ADOPTION_REVIEW_LEDGER_PATH)
    assert isinstance(contents, ReviewLedgerContents), contents
    return contents


def test_lines_round_trip_through_the_file(tmp_path: Path) -> None:
    _ledger(tmp_path)
    first, second = review_lines(
        [review_entry(_ID), review_entry(_ID, result=ReviewResult.PROCEED, nonce="b" * 32)]
    )
    for line in (first, second):
        outcome = append_review_ledger_file(tmp_path, line)
        assert isinstance(outcome, ReviewAppendOutcome)
        assert not outcome.sync_failed
    assert _read(tmp_path).lines == (first, second)
    assert not (tmp_path / ADOPTION_REVIEW_LOCK_PATH).exists()


def test_a_missing_ledger_is_a_read_failure_and_never_created(tmp_path: Path) -> None:
    failure = read_review_ledger_file(tmp_path / ADOPTION_REVIEW_LEDGER_PATH)
    assert isinstance(failure, ReviewLedgerReadFailure)
    assert failure.kind is ReviewDefect.FILE_MISSING
    refused = append_review_ledger_file(tmp_path, review_lines([review_entry(_ID)])[0])
    assert isinstance(refused, ReviewAppendRefused)
    assert not (tmp_path / ADOPTION_REVIEW_LEDGER_PATH).exists()


def test_a_lock_refuses_the_append_and_writes_nothing(tmp_path: Path) -> None:
    """T16: ロックがあれば追記を断り、行を書かない（ロックは消さない）。"""
    path = _ledger(tmp_path)
    (tmp_path / ADOPTION_REVIEW_LOCK_PATH).write_text("{}\n", encoding="utf-8")
    refused = append_review_ledger_file(tmp_path, review_lines([review_entry(_ID)])[0])
    assert isinstance(refused, ReviewAppendRefused)
    assert refused.kind is ReviewAppendRefusal.LOCKED
    assert path.read_bytes() == b""
    assert (tmp_path / ADOPTION_REVIEW_LOCK_PATH).exists()


def test_a_changed_tail_refuses_the_append(tmp_path: Path) -> None:
    """T16: 読んだ後に末尾が変わっていれば断る（`prev` が最後の行と合わない）。"""
    first, second = review_lines([review_entry(_ID), review_entry(_ID, nonce="b" * 32)])
    _ledger(tmp_path, review_line_bytes(first))
    stale = AdoptionReviewLine.of(second.entry, None)
    refused = append_review_ledger_file(tmp_path, stale)
    assert isinstance(refused, ReviewAppendRefused)
    assert refused.kind is ReviewAppendRefusal.TAIL_CHANGED
    assert _read(tmp_path).lines == (first,)


def test_a_torn_tail_is_ignored_on_read_and_cut_before_the_next_append(tmp_path: Path) -> None:
    (first,) = review_lines([review_entry(_ID)])
    _ledger(tmp_path, review_line_bytes(first)[:40])
    contents = _read(tmp_path)
    assert contents.lines == ()
    assert contents.torn_tail
    assert isinstance(append_review_ledger_file(tmp_path, first), ReviewAppendOutcome)
    assert (tmp_path / ADOPTION_REVIEW_LEDGER_PATH).read_bytes() == review_line_bytes(first)


def test_a_failed_sync_after_the_full_line_is_a_recorded_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T10 の後者: 完全な行を書いた後の同期の失敗は記録あり（次の読込で有効）。"""
    _ledger(tmp_path)
    (line,) = review_lines([review_entry(_ID, result=ReviewResult.PROCEED)])

    def failing(_: int) -> None:
        raise OSError("sync failed")

    monkeypatch.setattr(review_store, "_sync", failing)
    outcome = append_review_ledger_file(tmp_path, line)
    assert isinstance(outcome, ReviewAppendOutcome)
    assert outcome.sync_failed
    assert _read(tmp_path).lines == (line,)


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"not json\n", ReviewDefect.LINE_CORRUPT),
        (b'{"digest": "x", "entry": {}, "prev": null}\n', ReviewDefect.LINE_CORRUPT),
    ],
)
def test_corrupt_lines_make_the_ledger_unreadable(
    tmp_path: Path, data: bytes, kind: ReviewDefect
) -> None:
    """T15 の層: 読めない行があれば審査台帳を読めない（審査は入口の順 0 で止まる）。"""
    failure = read_review_ledger_file(_ledger(tmp_path, data))
    assert isinstance(failure, ReviewLedgerReadFailure)
    assert failure.kind is kind


def test_a_broken_chain_makes_the_ledger_unreadable(tmp_path: Path) -> None:
    first, second = review_lines([review_entry(_ID), review_entry(_ID, nonce="b" * 32)])
    orphan = AdoptionReviewLine.of(second.entry, None)
    failure = read_review_ledger_file(
        _ledger(tmp_path, review_line_bytes(first) + review_line_bytes(orphan))
    )
    assert isinstance(failure, ReviewLedgerReadFailure)
    assert failure.kind is ReviewDefect.CHAIN_BROKEN


def test_the_numeric_record_is_written_once_per_nonce(tmp_path: Path) -> None:
    version = tmp_path / "runs" / "experiments" / "exp" / "v1"
    place, record_digest = write_review_record(tmp_path, version, "c" * 32, {"a": "1"}, "# x\n")
    data = (place / REVIEW_RECORD_FILE).read_bytes()
    assert json.loads(data) == {"a": "1"}
    assert record_digest == digest_of_bytes(data)
    with pytest.raises(FileExistsError):
        write_review_record(tmp_path, version, "c" * 32, {"a": "2"}, "# y\n")
    assert (place / REVIEW_RECORD_FILE).read_bytes() == data


def digest_of_bytes(data: bytes) -> object:
    import hashlib

    from odyssey_fx.common.refs import ContentDigest

    return ContentDigest.sha256(hashlib.sha256(data).hexdigest())
