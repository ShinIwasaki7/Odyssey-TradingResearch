"""最終検証の台帳 `research/final_validations.jsonl` の書き込み規約（D09 §9.10.5。仮置き R2-5）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.final_validation_records import (
    FINAL_LEDGER_LOCK_PATH,
    FINAL_LEDGER_PATH,
    FinalLedgerError,
    append_ledger_row,
    candidate_payload,
    parse_final_ledger,
    read_final_ledger,
)
from odyssey_fx.evaluation.domain.final_validation import CandidateKey, FinalLedgerResult

KEY = CandidateKey(experiment_id="a" * 64, execution=1, experiment_name="exp", experiment_version=1)


def _values(result: FinalLedgerResult = FinalLedgerResult.WITHDRAWN) -> dict[str, object]:
    return {
        "result": result.value,
        "candidate": candidate_payload(KEY),
        "permit_id": "",
        "t_fix": "",
        "consumption_start": "",
        "consumption_end": "",
        "partition_count": 0,
        "partitions_digest": "",
        "consumption_commit": "",
        "combined_verdict": "",
        "resume_count": 0,
        "prior_final_validations": 0,
        "reason": "WITHDRAWN",
        "final_evaluation_sha256": "",
        "declaration_sha256": "",
        "recorded_at": "2026-11-20T00:00:00Z",
        "actor": "人間",
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "research").mkdir()
    (tmp_path / FINAL_LEDGER_PATH).write_bytes(b"")
    return tmp_path


def test_rows_are_chained_and_read_back(repo: Path) -> None:
    append_ledger_row(repo, _values())
    append_ledger_row(repo, _values(FinalLedgerResult.DECLARED_USED))
    ledger = read_final_ledger(repo)
    assert [row.result for row in ledger.rows] == [
        FinalLedgerResult.WITHDRAWN,
        FinalLedgerResult.DECLARED_USED,
    ]
    assert ledger.rows[0].payload["prev"] == ""
    assert ledger.rows[1].payload["prev"] != ""
    assert not (repo / FINAL_LEDGER_LOCK_PATH).exists()


def test_a_broken_chain_or_a_non_canonical_line_is_refused(repo: Path) -> None:
    append_ledger_row(repo, _values())
    append_ledger_row(repo, _values())
    lines = (repo / FINAL_LEDGER_PATH).read_bytes().splitlines(keepends=True)
    with pytest.raises(FinalLedgerError, match="prev"):
        parse_final_ledger(lines[1])
    with pytest.raises(FinalLedgerError, match="canonical"):
        parse_final_ledger(lines[0].replace(b'":', b'": ', 1))
    with pytest.raises(FinalLedgerError, match="fragment"):
        parse_final_ledger(lines[0][:-1])


def test_the_ledger_is_never_created_by_an_append_and_a_lock_stops_it(tmp_path: Path) -> None:
    with pytest.raises(FinalLedgerError, match="does not exist"):
        append_ledger_row(tmp_path, _values())
    (tmp_path / "research").mkdir(exist_ok=True)
    (tmp_path / FINAL_LEDGER_PATH).write_bytes(b"")
    (tmp_path / FINAL_LEDGER_LOCK_PATH).write_text("other\n")
    with pytest.raises(FinalLedgerError, match="exists"):
        append_ledger_row(tmp_path, _values())
    assert (tmp_path / FINAL_LEDGER_PATH).read_bytes() == b""
