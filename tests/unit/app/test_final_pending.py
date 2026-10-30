"""同じ版の最終検証の申請が状態 B・C かの確かめ（D09 仮置き R2-29 の (1)・R4b-9）。

`experiment run` が記録票の保存の前に呼ぶ。手元の `final/` と作業木の最終検証の台帳だけを見る。
"""

from __future__ import annotations

import json
from pathlib import Path

from odyssey_fx.app.final_pending import pending_final_validation


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "research").mkdir(parents=True)
    (repo / "research" / "final_validations.jsonl").write_bytes(b"")
    directory = tmp_path / "runs" / "experiments" / "exp" / "v1"
    directory.mkdir(parents=True)
    return repo, directory


def test_no_final_directory_or_a_finished_application_is_not_pending(tmp_path: Path) -> None:
    repo, directory = _setup(tmp_path)
    assert pending_final_validation(directory, repo) is None
    final = directory / "final" / "1"
    final.mkdir(parents=True)
    (final / "permit.json").write_text(json.dumps({"permit_id": "a" * 32}))
    (final / "final_evaluation.json").write_text("{}")
    assert pending_final_validation(directory, repo) is None  # 状態 D


def test_a_lock_is_state_b(tmp_path: Path) -> None:
    repo, directory = _setup(tmp_path)
    final = directory / "final" / "2"
    final.mkdir(parents=True)
    (final / "final.lock").write_text("running\n")
    found = pending_final_validation(directory, repo)
    assert found is not None and "state B" in found


def test_a_copy_without_record_and_ledger_row_is_state_c(tmp_path: Path) -> None:
    repo, directory = _setup(tmp_path)
    final = directory / "final" / "1"
    final.mkdir(parents=True)
    (final / "permit.json").write_text(json.dumps({"permit_id": "a" * 32}))
    found = pending_final_validation(directory, repo)
    assert found is not None and "state C" in found
    ledger = repo / "research" / "final_validations.jsonl"
    ledger.write_text(json.dumps({"permit_id": "a" * 32, "result": "FAILED"}) + "\n")
    assert pending_final_validation(directory, repo) is None  # 閉じた（状態 E）


def test_an_unreadable_copy_or_ledger_counts_as_pending(tmp_path: Path) -> None:
    repo, directory = _setup(tmp_path)
    final = directory / "final" / "1"
    final.mkdir(parents=True)
    (final / "permit.json").write_text("not json")
    assert pending_final_validation(directory, repo) is not None
    (final / "permit.json").write_text(json.dumps({"permit_id": "a" * 32}))
    (repo / "research" / "final_validations.jsonl").write_bytes(b"{broken")
    assert pending_final_validation(directory, repo) is not None
