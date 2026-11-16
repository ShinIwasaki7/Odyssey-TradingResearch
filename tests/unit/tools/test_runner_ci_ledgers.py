"""検査 1: 3 つの台帳の読込の検査と追記だけ（D25 §6.3 の 1。関門 AU3 の「壊した台帳・途中を
書き換えた台帳で CI が落ちる試験」）。人工の台帳で確かめ、本物の台帳は合否だけを見る。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.runner_ci import FINAL, REVIEW, TRIAL, ledgers, make_input, trial_ledger
from tools.ops.runner_ci.ledgers import LEDGERS, check_ledgers
from tools.ops.runner_ci.model import Status

REPO_ROOT = Path(__file__).resolve().parents[3]


def _corrupt(data: bytes) -> bytes:
    """最初の行の中の 1 文字を変える（JSON のまま、ダイジェストが合わなくなる）。"""
    index = data.index(b'"execution":') if b'"execution":' in data else data.index(b'"prev":') - 3
    return data[:index] + data[index:].replace(b"1", b"2", 1)


def test_the_real_ledgers_pass_the_checks() -> None:
    files = {path: (REPO_ROOT / path).read_bytes() for _, path, _ in LEDGERS}
    checked = check_ledgers(make_input(files, files))
    assert checked.status is Status.PASS


def test_appending_lines_to_every_ledger_passes(tmp_path: Path) -> None:
    base = ledgers(tmp_path / "base", trial=2, review=1, final=1)
    head = ledgers(tmp_path / "head", trial=4, review=2, final=2)
    assert all(head[path].startswith(base[path]) for path in base)
    assert check_ledgers(make_input(base, head)).status is Status.PASS


@pytest.mark.parametrize("path", [TRIAL, REVIEW, FINAL])
def test_a_broken_line_fails(tmp_path: Path, path: str) -> None:
    base = ledgers(tmp_path)
    head = dict(base)
    lines = head[path].splitlines(keepends=True)
    broken = lines[0][:10] + b"x" + lines[0][11:]
    head[path] = broken + b"".join(lines[1:])
    checked = check_ledgers(make_input(base, head))
    assert checked.status is Status.FAIL
    assert any(path in message and "読込の検査" in message for message in checked.messages)


def test_a_changed_digest_in_the_trial_ledger_is_named_by_kind_and_line(tmp_path: Path) -> None:
    base = ledgers(tmp_path)
    head = dict(base, **{TRIAL: _corrupt(base[TRIAL])})
    checked = check_ledgers(make_input(head, head))
    assert checked.status is Status.FAIL
    assert any("LINE_CORRUPT（行 1）" in message for message in checked.messages)


def test_a_ledger_rewritten_in_the_middle_fails_the_append_only_check(tmp_path: Path) -> None:
    base = ledgers(tmp_path)
    # 鎖の整合した別の台帳（同じ行の数・読込の検査は通る）に置き換える = 途中の書き換え。
    rewritten = trial_ledger(2, name="b")
    head = dict(base, **{TRIAL: rewritten})
    checked = check_ledgers(make_input(base, head))
    assert checked.status is Status.FAIL
    assert any("追記だけでない" in message and "行 1" in message for message in checked.messages)


def test_removing_the_last_line_fails(tmp_path: Path) -> None:
    base = ledgers(tmp_path, trial=4)
    lines = base[TRIAL].splitlines(keepends=True)
    head = dict(base, **{TRIAL: b"".join(lines[:-1])})
    checked = check_ledgers(make_input(base, head))
    assert checked.status is Status.FAIL


def test_a_torn_tail_fails_in_ci(tmp_path: Path) -> None:
    base = ledgers(tmp_path)
    head = dict(base, **{TRIAL: base[TRIAL] + b'{"digest":'})
    checked = check_ledgers(make_input(base, head))
    assert checked.status is Status.FAIL
    assert any("TORN_TAIL" in message for message in checked.messages)


def test_a_missing_ledger_fails(tmp_path: Path) -> None:
    base = ledgers(tmp_path)
    head = {path: data for path, data in base.items() if path != REVIEW}
    checked = check_ledgers(make_input(base, head))
    assert checked.status is Status.FAIL
    assert any("FILE_MISSING" in message for message in checked.messages)


def test_a_new_ledger_may_only_start_empty(tmp_path: Path) -> None:
    head = ledgers(tmp_path)
    base = {path: data for path, data in head.items() if path != FINAL}
    assert check_ledgers(make_input(base, head)).status is Status.FAIL
    assert check_ledgers(make_input(base, dict(head, **{FINAL: b""}))).status is Status.PASS


def test_without_a_base_only_the_read_checks_apply(tmp_path: Path) -> None:
    head = ledgers(tmp_path)
    checked = check_ledgers(make_input(None, head))
    assert checked.status is Status.PASS
    assert any("基点が無い" in message for message in checked.messages)


def test_messages_do_not_carry_ledger_contents(tmp_path: Path) -> None:
    base = ledgers(tmp_path)
    head = dict(base, **{REVIEW: base[REVIEW].replace(b"DEFERRED", b"PROCEED", 1)})
    checked = check_ledgers(make_input(base, head))
    assert checked.status is Status.FAIL
    assert not any("DEFERRED" in message or "PROCEED" in message for message in checked.messages)
