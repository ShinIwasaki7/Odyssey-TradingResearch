"""D09 v0.3 の試行台帳の運用規則の意味論の行（D08 §7.4 の #18〜#29 の台帳の部分）。

1行1テストで名前を付けて固定する（D01 §9、D08 §2.1）。台帳はテストの中のリポジトリの根に作り、
ロック・途中停止・合流は、ファイルを直接組み立てるか、追記の操作の途中で例外を起こす偽の書き込みで
作る（実データも git も使わない。D09 §13）。

- #18（`test_18_*`）: 書きかけの末尾（D09 §10.12.1 の W3・§10.12.2 の L0〜L2）。
- #19（`test_19_*`）: 行の鎖と合流（§10.12.5・L3）。
- #20（`test_20_*`）: 読込の検査の種類（L4〜L10。§10.12.2）。
- #21（`test_21_*`）: 採番とロックと末尾の照合（§10.12.1・§10.12.3 の1）。
- #23 の台帳の部分（`test_23_*`）: 途中停止の位置 a・b・c の台帳の状態（§10.12.4）。
- #24 の台帳の部分（`test_24_*`）: 結末の行は開始の行の項目を写す。不変項目が違えば L9。
- #27〜#29 の照合の部分（`test_27_*`〜`test_29_*`）: 成果物からの逆照合 L11 と数え直し待ち
  （§10.12.2。Q38・Q39）。実行の経路での振る舞いは `tests/integration/app/test_search_run.py`。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.common.canonical import digest
from odyssey_fx.evaluation.adapters import fs_store
from odyssey_fx.evaluation.adapters.fs_store import (
    TRIAL_LEDGER_LOCK_PATH,
    append_trial_ledger_file,
    ledger_line_bytes,
    read_ledger_binding_records,
    read_trial_ledger_file,
)
from odyssey_fx.evaluation.application.ports import (
    TrialLedgerAppendRefused,
    TrialLedgerContents,
    TrialLedgerReadFailure,
    TrialLedgerRefusal,
)
from odyssey_fx.evaluation.domain.experiment import ExperimentStatus
from odyssey_fx.evaluation.domain.search import (
    SearchVerdict,
    StandardPurpose,
    TrialLedgerBinding,
    TrialLedgerDefect,
    TrialLedgerEvent,
    TrialLedgerLine,
    last_digest,
    next_execution,
    unmatched_binding,
)
from tests.fixtures.evaluation.trial_ledger import (
    chain,
    experiment_id,
    finished,
    ledger_path,
    started,
    with_nonce,
    write_ledger,
)


def _read(root: Path) -> TrialLedgerContents:
    contents = read_trial_ledger_file(ledger_path(root))
    assert isinstance(contents, TrialLedgerContents), contents
    return contents


def _failure(root: Path) -> TrialLedgerReadFailure:
    contents = read_trial_ledger_file(ledger_path(root))
    assert isinstance(contents, TrialLedgerReadFailure), contents
    return contents


# --- #18 書きかけの末尾 ------------------------------------------------------------


@pytest.mark.parametrize("fragment", [b'{"digest":"ab', b"not json at all", b"{}"])
def test_18_a_torn_tail_is_not_a_failure_and_the_next_append_trims_only_it(
    tmp_path: Path, fragment: bytes
) -> None:
    """改行で終わらない最後の断片は書きかけ: 完全な行だけと `torn_tail = true` を返す。次の追記は
    その断片だけを切り詰めてから足す（改行で終わる行は消えない）。"""
    lines = chain([started("a")])
    write_ledger(tmp_path, lines, tail=fragment)
    contents = _read(tmp_path)
    assert contents.lines == tuple(lines) and contents.torn_tail

    nxt = chain([started("b")], prev=lines[-1].digest)[0]
    assert append_trial_ledger_file(tmp_path, nxt) == nxt
    assert ledger_path(tmp_path).read_bytes() == ledger_line_bytes(lines[0]) + ledger_line_bytes(
        nxt
    )
    assert _read(tmp_path) == TrialLedgerContents(lines=(lines[0], nxt), torn_tail=False)


@pytest.mark.parametrize(
    "broken", [b"not json\n", b'{"digest":"x","entry":{},"prev":null}\n'], ids=["json", "digest"]
)
def test_18_a_broken_line_ending_with_a_newline_is_corrupt_even_when_last(
    tmp_path: Path, broken: bytes
) -> None:
    """改行で終わる行の壊れ（JSON でない・`digest` が合わない）は最後の行でも切り詰めず L2。"""
    write_ledger(tmp_path, [*chain([started("a")]), broken])
    failure = _failure(tmp_path)
    assert (failure.kind, failure.line_number) == (TrialLedgerDefect.LINE_CORRUPT, 2)
    refused = append_trial_ledger_file(tmp_path, chain([started("b")])[0])
    assert isinstance(refused, TrialLedgerAppendRefused)
    assert refused.kind is TrialLedgerRefusal.READ_FAILED
    assert ledger_path(tmp_path).read_bytes().endswith(broken)


def test_18_a_line_written_in_full_counts_even_when_the_sync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """行が完全に書けた後に同期が失敗すると追記は `WRITE_FAILED` を返すが、その行は次の読込で
    有効な行として数えられる（書き手の側は「書けたかどうか分からない」）。"""
    write_ledger(tmp_path, [])
    original = fs_store._append_bytes

    def write_then_fail(path: Path, keep: int, data: bytes) -> None:
        original(path, keep, data)
        raise OSError("fsync failed")

    monkeypatch.setattr(fs_store, "_append_bytes", write_then_fail)
    line = chain([started("a")])[0]
    refused = append_trial_ledger_file(tmp_path, line)
    assert isinstance(refused, TrialLedgerAppendRefused)
    assert refused.kind is TrialLedgerRefusal.WRITE_FAILED
    assert _read(tmp_path).lines == (line,)
    assert not (tmp_path / TRIAL_LEDGER_LOCK_PATH).exists()


def test_18_a_missing_ledger_is_never_created_and_an_empty_one_has_no_lines(
    tmp_path: Path,
) -> None:
    """無い台帳は `FILE_MISSING` で止まり、追記は何も作らない（ロックも作らない）。空のファイルは
    正当な0行の台帳（L0）。"""
    failure = _failure(tmp_path)
    assert (failure.kind, failure.line_number) == (TrialLedgerDefect.FILE_MISSING, None)
    refused = append_trial_ledger_file(tmp_path, chain([started("a")])[0])
    assert isinstance(refused, TrialLedgerAppendRefused)
    assert refused.kind is TrialLedgerRefusal.READ_FAILED
    assert refused.read_failure is not None
    assert refused.read_failure.kind is TrialLedgerDefect.FILE_MISSING
    assert not ledger_path(tmp_path).exists()
    assert not (tmp_path / TRIAL_LEDGER_LOCK_PATH).exists()

    write_ledger(tmp_path, [])
    assert _read(tmp_path) == TrialLedgerContents(lines=(), torn_tail=False)


# --- #19 行の鎖と合流 ---------------------------------------------------------------


def test_19_merging_two_copies_extended_separately_is_stopped_by_the_chain(
    tmp_path: Path,
) -> None:
    """同じ末尾 X から2つの複製に別々に1行ずつ足して両方を並べると L3（`CHAIN_BROKEN`）で止まる。
    片方だけを伸ばした台帳（早送り）は通る。"""
    base = chain([started("x")])
    left = chain([started("a")], prev=base[-1].digest)
    right = chain([started("b")], prev=base[-1].digest)
    write_ledger(tmp_path, [*base, *left, *right])
    failure = _failure(tmp_path)
    assert (failure.kind, failure.line_number) == (TrialLedgerDefect.CHAIN_BROKEN, 3)

    write_ledger(tmp_path, [*base, *left])
    assert _read(tmp_path).lines == (*base, *left)


def test_19_the_same_execution_in_two_copies_never_becomes_one_line(tmp_path: Path) -> None:
    """同じ実験を同じ入力で2つの複製に追記した開始の行は `execution_nonce` が違うのでバイト列が
    一致しない（git の合流が1行にまとめない）。並べれば鎖か主キーで止まる。"""
    base = chain([started("x")])
    first = chain([with_nonce(started("a"), "1" * 32)], prev=base[-1].digest)
    second = chain([with_nonce(started("a"), "2" * 32)], prev=base[-1].digest)
    assert ledger_line_bytes(first[0]) != ledger_line_bytes(second[0])
    write_ledger(tmp_path, [*base, *first, *second])
    assert _failure(tmp_path).kind in (
        TrialLedgerDefect.CHAIN_BROKEN,
        TrialLedgerDefect.DUPLICATE_KEY,
    )


def test_19_a_complete_last_line_whose_prev_disagrees_is_broken_not_torn(tmp_path: Path) -> None:
    """最後の行が完全で `prev` だけが直前の行と合わない台帳は書きかけではなく L3。"""
    lines = chain([started("a")])
    stray = chain([started("b")], prev=digest("elsewhere"))
    write_ledger(tmp_path, [*lines, *stray])
    failure = _failure(tmp_path)
    assert (failure.kind, failure.line_number) == (TrialLedgerDefect.CHAIN_BROKEN, 2)


# --- #20 読込の検査の種類 --------------------------------------------------------------


def _entry_invalid(tmp_path: Path) -> list[TrialLedgerLine | bytes]:
    good = chain([started("a")])
    entry_json = json.loads(ledger_line_bytes(chain([started("b")], prev=good[-1].digest)[0]))
    entry_json["entry"]["unknown"] = 1
    raw = entry_json["entry"]
    bad = {
        "digest": digest({"entry": raw, "prev": good[-1].digest.hex}).hex,
        "entry": raw,
        "prev": good[-1].digest.hex,
    }
    from odyssey_fx.common.canonical import encode

    return [*good, encode(bad) + b"\n"]


def _cases() -> dict[str, tuple[Sequence[TrialLedgerLine], TrialLedgerDefect, int]]:
    a = started("a")
    finished_a = finished(a)
    return {
        "L5": (
            chain([replace(a, verdict=SearchVerdict.INCOMPLETE)]),
            TrialLedgerDefect.EVENT_SHAPE,
            1,
        ),
        "L6": (chain([a, finished_a, finished_a]), TrialLedgerDefect.DUPLICATE_KEY, 3),
        "L7": (chain([started("a", 2)]), TrialLedgerDefect.EXECUTION_GAP, 1),
        "L8": (chain([started("b"), finished_a]), TrialLedgerDefect.ORPHAN_FINISHED, 2),
        "L9": (
            chain([a, replace(finished_a, trial_count=5)]),
            TrialLedgerDefect.INVARIANT_MISMATCH,
            2,
        ),
        "L10": (
            chain([a, finished(a, verdict=SearchVerdict.MEETS_STANDARD)]),
            TrialLedgerDefect.VERDICT_PURPOSE,
            2,
        ),
    }


@pytest.mark.parametrize("case", ["L5", "L6", "L7", "L8", "L9", "L10"])
def test_20_each_check_reports_its_kind_and_line(tmp_path: Path, case: str) -> None:
    """L5〜L10 のそれぞれに当たる台帳で、種類と行番号がその検査のものになる。"""
    lines, kind, number = _cases()[case]
    write_ledger(tmp_path, lines)
    failure = _failure(tmp_path)
    assert (failure.kind, failure.line_number) == (kind, number)


def test_20_an_entry_that_does_not_fit_the_type_is_entry_invalid(tmp_path: Path) -> None:
    """L4: 未知のキーを持つ行（ダイジェストは合う）は `ENTRY_INVALID`。"""
    write_ledger(tmp_path, _entry_invalid(tmp_path))
    failure = _failure(tmp_path)
    assert (failure.kind, failure.line_number) == (TrialLedgerDefect.ENTRY_INVALID, 2)


def test_20_the_defect_nearest_the_top_of_the_file_is_returned(tmp_path: Path) -> None:
    """複数に当たる台帳では、ファイルの先頭に近い行の食い違いが返る。"""
    a = started("a")
    lines = chain([started("b", 2), a, replace(finished(a), trial_count=9)])
    write_ledger(tmp_path, [*lines, b"broken\n"])
    failure = _failure(tmp_path)
    assert (failure.kind, failure.line_number) == (TrialLedgerDefect.EXECUTION_GAP, 1)


# --- #21 採番とロックと末尾の照合 -------------------------------------------------------


def test_21_the_execution_is_the_count_of_started_lines_plus_one(tmp_path: Path) -> None:
    a1 = started("a")
    lines = chain([a1, finished(a1), started("b"), started("a", 2)])
    assert next_execution(lines, experiment_id("a")) == 3
    assert next_execution(lines, experiment_id("b")) == 2
    assert next_execution(lines, experiment_id("c")) == 1


def test_21_a_lock_refuses_the_append_and_is_never_removed_automatically(tmp_path: Path) -> None:
    write_ledger(tmp_path, [])
    lock = tmp_path / TRIAL_LEDGER_LOCK_PATH
    lock.write_text("left by a stopped writer\n", encoding="utf-8")
    refused = append_trial_ledger_file(tmp_path, chain([started("a")])[0])
    assert isinstance(refused, TrialLedgerAppendRefused)
    assert refused.kind is TrialLedgerRefusal.LOCKED
    assert lock.read_text(encoding="utf-8") == "left by a stopped writer\n"
    assert ledger_path(tmp_path).read_bytes() == b""


def test_21_a_tail_that_changed_after_reading_refuses_and_leaves_no_lock(tmp_path: Path) -> None:
    """読んだ後に他の追記が入れば `TAIL_CHANGED` で何も書かない。断ったときはロックが残らない。"""
    write_ledger(tmp_path, [])
    stale = chain([started("a")])[0]  # 空の台帳を読んで組み立てた行（prev = null）
    other = chain([started("b")])[0]
    assert append_trial_ledger_file(tmp_path, other) == other
    before = ledger_path(tmp_path).read_bytes()
    refused = append_trial_ledger_file(tmp_path, stale)
    assert isinstance(refused, TrialLedgerAppendRefused)
    assert refused.kind is TrialLedgerRefusal.TAIL_CHANGED
    assert ledger_path(tmp_path).read_bytes() == before
    assert not (tmp_path / TRIAL_LEDGER_LOCK_PATH).exists()


# --- #23 途中停止の位置（台帳の側）---------------------------------------------------------


def test_23_a_and_c_a_complete_started_line_is_counted_and_a_left_lock_blocks(
    tmp_path: Path,
) -> None:
    """a: ロックを作った後に止まれば台帳は変わらずロックが残る（以後の追記は断られる）。
    c: 開始の行が完全に書けた後に止まれば、その行は数えられたまま残り、次の実行の採番は + 1。"""
    a1 = started("a")
    lines = chain([a1])
    write_ledger(tmp_path, lines)
    (tmp_path / TRIAL_LEDGER_LOCK_PATH).write_text("stopped\n", encoding="utf-8")
    refused = append_trial_ledger_file(tmp_path, chain([started("a", 2)], lines[-1].digest)[0])
    assert isinstance(refused, TrialLedgerAppendRefused)
    assert refused.kind is TrialLedgerRefusal.LOCKED
    (tmp_path / TRIAL_LEDGER_LOCK_PATH).unlink()
    contents = _read(tmp_path)
    assert next_execution(contents.lines, experiment_id("a")) == 2


# --- #24 結末の行の照合と不変項目 -------------------------------------------------------


def test_24_the_finished_line_copies_the_started_line_except_the_outcome_items() -> None:
    opening = started("a")
    closing = finished(opening, status=ExperimentStatus.FAILED_POST_RUN_CHECK)
    assert closing.event is TrialLedgerEvent.FINISHED
    assert (closing.status, closing.verdict, closing.frequency_class) == (
        ExperimentStatus.FAILED_POST_RUN_CHECK,
        SearchVerdict.INSUFFICIENT_EVIDENCE,
        "LOW",
    )
    assert (
        replace(
            closing, event=TrialLedgerEvent.STARTED, status=None, verdict=None, frequency_class=None
        )
        == opening
    )


# --- #27〜#29 成果物からの逆照合と数え直し待ち ---------------------------------------------


def _binding(lines: list[TrialLedgerLine], index: int) -> TrialLedgerBinding:
    entry = lines[index].entry
    return TrialLedgerBinding(
        schema_version=1,
        experiment_id=entry.experiment_id,
        execution=entry.execution,
        started_line_digest=lines[index].digest,
    )


def test_27_a_binding_whose_started_line_is_gone_stops_l11() -> None:
    """片方の側だけを残して合流を解消した台帳は L0〜L10 を通るが、捨てた側の束縛の記録が
    残っていれば L11（数え直し待ち）で止まる。"""
    kept = chain([started("a")])
    dropped = chain([started("b")])
    bindings = [
        ("runs/experiments/exp_a/v1/search/ledger_execution.json", _binding(kept, 0)),
        ("runs/experiments/exp_b/v1/search/ledger_execution.json", _binding(dropped, 0)),
    ]
    assert unmatched_binding(kept, bindings, None) is not None
    assert unmatched_binding(kept, bindings[:1], None) is None
    unreadable = [("runs/experiments/exp_c/v1/search/ledger_execution.json", "not JSON")]
    assert unmatched_binding(kept, unreadable, None) is not None


def test_28_the_pending_versions_themselves_pass_and_do_not_block_each_other() -> None:
    """数え直し待ちの版自身の再実行は通る（ほかの版が数え直し待ちでも）。"""
    ledger: list[TrialLedgerLine] = []
    x = chain([started("x")])
    y = chain([started("y")])
    bindings = [
        ("runs/experiments/exp_x/v1/search.1/ledger_execution.json", _binding(x, 0)),
        ("runs/experiments/exp_y/v1/search/ledger_execution.json", _binding(y, 0)),
    ]
    assert unmatched_binding(ledger, bindings, "runs/experiments/exp_x/v1") is None
    assert unmatched_binding(ledger, bindings, "runs/experiments/exp_y/v1") is None
    found = unmatched_binding(ledger, bindings, "runs/experiments/exp_z/v1")
    assert found is not None and found[0].startswith("runs/experiments/exp_x/v1/")


def _write_binding(path: Path, binding: TrialLedgerBinding) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from odyssey_fx.common.canonical import encode

    path.write_text(json.dumps(json.loads(encode(binding))), encoding="utf-8")


def test_29_only_the_newest_generation_with_a_binding_is_compared(tmp_path: Path) -> None:
    """束縛の記録がある最も新しい世代だけを照合する: 今の世代に記録があればそれ、無ければ退避した
    世代のうち最大の番号のもの。古い世代の記録が合わなくても止まらない（限界の (c)）。"""
    ledger = chain([started("a"), started("a", 2)])
    version = tmp_path / "runs/experiments/exp_a/v1"
    stale = TrialLedgerBinding(
        schema_version=1,
        experiment_id=experiment_id("a"),
        execution=1,
        started_line_digest=digest("discarded"),
    )
    _write_binding(version / "search.1/ledger_execution.json", stale)
    _write_binding(version / "search.2/ledger_execution.json", _binding(ledger, 1))
    (version / "search.3").mkdir()  # 束縛の記録の無い世代（開始の行の前に止まった）
    records = read_ledger_binding_records(tmp_path)
    assert [path for path, _ in records] == [
        "runs/experiments/exp_a/v1/search.2/ledger_execution.json"
    ]
    bindings = [
        (path, record if isinstance(record, TrialLedgerBinding) else record.detail)
        for path, record in records
    ]
    assert unmatched_binding(ledger, bindings, None) is None

    _write_binding(version / "search/ledger_execution.json", stale)
    records = read_ledger_binding_records(tmp_path)
    assert [path for path, _ in records] == [
        "runs/experiments/exp_a/v1/search/ledger_execution.json"
    ]


def test_29_an_unreadable_binding_is_a_pending_version(tmp_path: Path) -> None:
    path = tmp_path / "runs/experiments/exp_a/v1/search/ledger_execution.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    records = read_ledger_binding_records(tmp_path)
    assert len(records) == 1
    _, record = records[0]
    assert isinstance(record, TrialLedgerReadFailure)
    assert record.kind is TrialLedgerDefect.UNMATCHED_BINDING


def test_20_a_verdict_made_by_step_4_for_its_purpose_passes_l10(tmp_path: Path) -> None:
    """用途が `STANDARD` の実行の結末の行の `MEETS_STANDARD` は L10 を通る（正の例）。"""
    opening = started("a", purpose=StandardPurpose.STANDARD)
    lines = chain([opening, finished(opening, verdict=SearchVerdict.MEETS_STANDARD)])
    write_ledger(tmp_path, lines)
    contents = _read(tmp_path)
    assert last_digest(contents.lines) == lines[-1].digest
