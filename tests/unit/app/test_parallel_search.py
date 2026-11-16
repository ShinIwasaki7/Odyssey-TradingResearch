"""組ごとの並列の実行の親と子の状態機械の試験（D26 §3.4 の親の表・子の表、§3.6 の 2）。

子の仕事は `tests.fixtures.evaluation.parallel_jobs` の小さな関数に差し替える（本物の探索の
実験は結合試験 `tests/integration/app/test_search_parallel_variants.py`）。親が合図を受ける場合
（E6）と親が強制終了される場合（子の表の F3）は、親を別プロセス
（`tests.fixtures.evaluation.parallel_harness`）で走らせる。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext, SpawnProcess
from multiprocessing.synchronize import Event
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from odyssey_fx.app import parallel_search
from odyssey_fx.app.parallel_search import (
    EXECUTION_LOCK_FILE,
    PARALLEL_MEMORY_BYTES,
    ExecutionLocked,
    ParallelVariantsFailed,
    VariantJob,
    dumps,
    execution_lock,
    loads,
    parallel_memory_shortfall,
    parse_vm_stat,
    run_variant_jobs,
    start_child,
)
from odyssey_fx.evaluation.domain.variant import Paired, Variant
from tests.fixtures.evaluation import parallel_jobs

Work = Callable[[bytes, Callable[[], None]], object]
REPO = Path(__file__).resolve().parents[3]


def _jobs(tmp_path: Path, standard: Work, candidate: Work, **options: object) -> Paired[VariantJob]:
    def job(variant: Variant) -> VariantJob:
        work = standard if variant is Variant.STANDARD else candidate
        payload: dict[str, object] = {
            "pid_file": str(tmp_path / f"{variant.value}.pid"),
            "count_file": str(tmp_path / f"{variant.value}.count"),
            "value": f"{variant.value}-result",
            **options,
        }
        return VariantJob(variant=variant, work=work, payload=dumps(payload))

    return Paired.build(job)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _pid(tmp_path: Path, variant: Variant) -> int:
    return int((tmp_path / f"{variant.value}.pid").read_text(encoding="ascii"))


def _run(jobs: Paired[VariantJob], **kwargs: Any) -> tuple[Paired[object] | None, list[str], str]:
    shown: list[str] = []
    try:
        result = run_variant_jobs(jobs, lock=None, notify=shown.append, **kwargs)
    except ParallelVariantsFailed as exc:
        return None, shown, str(exc)
    return result, shown, ""


# --- 子へ渡す値・空きメモリ・ロック -------------------------------------------------------


def test_read_only_mappings_survive_the_round_trip() -> None:
    value = {"inner": MappingProxyType({"a": 1}), "items": (1, 2)}
    back = loads(dumps(value))
    assert back == value
    assert isinstance(back["inner"], MappingProxyType)


def test_vm_stat_free_inactive_and_speculative_pages_are_counted() -> None:
    text = (
        "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
        "Pages free:                               100.\n"
        "Pages active:                             999.\n"
        "Pages inactive:                           200.\n"
        "Pages speculative:                         10.\n"
    )
    assert parse_vm_stat(text) == 4096 * 310
    assert parse_vm_stat("unexpected") is None
    assert parse_vm_stat(text.replace("Pages inactive", "Pages unknown")) is None


@pytest.mark.parametrize(
    ("free", "expected"),
    [
        (None, "確かめられない"),
        (PARALLEL_MEMORY_BYTES - 1, "足りない"),
        (PARALLEL_MEMORY_BYTES, None),
    ],
)
def test_parallel_needs_two_children_and_the_released_parent(
    monkeypatch: pytest.MonkeyPatch, free: int | None, expected: str | None
) -> None:
    """S0: 空きが「組 1 つ × 2 + 手放した後の親」に足りなければ順番へ（D26 §3.3、仮置き 5）。"""
    monkeypatch.setattr(parallel_search, "free_memory_bytes", lambda: free)
    shortfall = parallel_memory_shortfall()
    if expected is None:
        assert shortfall is None
    else:
        assert shortfall is not None and expected in shortfall and "順番" in shortfall
    assert PARALLEL_MEMORY_BYTES == 2 * parallel_search.CHILD_PEAK_BYTES + (
        parallel_search.PARENT_PEAK_BYTES
    )


def test_the_execution_lock_refuses_a_second_holder(tmp_path: Path) -> None:
    with execution_lock(tmp_path / "v1") as lock:
        assert lock.path == tmp_path / "v1" / EXECUTION_LOCK_FILE
        with pytest.raises(ExecutionLocked, match="still running"):
            with execution_lock(tmp_path / "v1"):
                pass
    with execution_lock(tmp_path / "v1"):
        pass
    assert (tmp_path / "v1" / EXECUTION_LOCK_FILE).read_bytes() == b""


def test_a_child_keeps_the_inherited_lock_after_the_parent_lets_go(tmp_path: Path) -> None:
    """子は親のロックを引き継ぎ、親が外しても子が終わるまで外れない（仮置き 11）。"""
    stop: Event | None = None
    with execution_lock(tmp_path / "v1") as lock:
        import multiprocessing

        context = multiprocessing.get_context("spawn")
        stop = context.Event()
        payload = dumps({"pid_file": str(tmp_path / "child.pid"), "timeout": 30})
        job = VariantJob(
            variant=Variant.STANDARD, work=parallel_jobs.wait_for_stop, payload=payload
        )
        process, conn = start_child(context, job, stop, lock.fd)
    try:
        deadline = time.monotonic() + 30
        while not (tmp_path / "child.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        with pytest.raises(ExecutionLocked):
            with execution_lock(tmp_path / "v1"):
                pass
    finally:
        stop.set()
        process.join(30)
        conn.close()
    assert process.exitcode == 0
    with execution_lock(tmp_path / "v1"):
        pass


# --- 親の表（S1a〜S3 と E3・E4・E5・E7・E13）---------------------------------------------


def test_both_children_return_their_results_in_variant_order(tmp_path: Path) -> None:
    """S1 → S2 → S4: 両方の結果が組の順にそろう。子は残らない。"""
    result, _, _ = _run(_jobs(tmp_path, parallel_jobs.return_value, parallel_jobs.return_value))
    assert result == Paired(standard="standard-result", candidate="candidate-result")
    for variant in Variant:
        assert not _alive(_pid(tmp_path, variant))


def test_an_exception_in_one_child_stops_the_other_at_a_unit_boundary(tmp_path: Path) -> None:
    """S1 + E4 → S3 → E7 → S6: もう片方は次の単位を始めずに止まる（D26-4 (a)）。"""
    result, shown, failure = _run(
        _jobs(tmp_path, parallel_jobs.wait_for_stop, parallel_jobs.raise_now)
    )
    assert result is None
    assert "candidate の組の子が例外で終わった" in failure
    assert "injected failure (test)" in failure
    assert any("standard の組の子は次の単位を始めずに止まった" in line for line in shown)
    assert not _alive(_pid(tmp_path, Variant.STANDARD))


def test_an_exception_after_the_other_child_succeeded_ends_the_execution(tmp_path: Path) -> None:
    """S2 + E4 → S6: 止める相手が居ない。成功した組の結果は使わない。"""
    result, _, failure = _run(
        _jobs(tmp_path, parallel_jobs.return_value, parallel_jobs.raise_later, delay=0.5)
    )
    assert result is None
    assert "candidate の組の子が例外で終わった" in failure


def test_a_child_that_vanishes_stops_the_other(tmp_path: Path) -> None:
    """S1 + E5 → S3 → S6: 結果も例外も返さずに消えた子は終了コードを表示する。"""
    result, _, failure = _run(_jobs(tmp_path, parallel_jobs.exit_hard, parallel_jobs.wait_for_stop))
    assert result is None
    assert "standard の組の子が結果を返さずに終わった（終了コード 3）" in failure
    assert not _alive(_pid(tmp_path, Variant.CANDIDATE))


def test_a_result_that_arrives_while_stopping_is_not_used(tmp_path: Path) -> None:
    """S3 + E3 → S6: 止める指示の前に最後の単位を終えた子の結果は使わない。"""
    result, _, failure = _run(
        _jobs(tmp_path, parallel_jobs.ignore_stop_and_return, parallel_jobs.raise_now, delay=1.0)
    )
    assert result is None
    assert "candidate" in failure


def test_a_failed_launch_of_the_second_child_stops_the_first(tmp_path: Path) -> None:
    """S1a + E13 → S3 → S6: 2 つ目の子を起こせなければ、起こした子を境目で止める。"""

    def starter(
        context: SpawnContext, job: VariantJob, stop: Event, lock_fd: int | None
    ) -> tuple[SpawnProcess, Connection]:
        if job.variant is Variant.CANDIDATE:
            raise OSError("no more processes (test)")
        return start_child(context, job, stop, lock_fd)

    result, _, failure = _run(
        _jobs(tmp_path, parallel_jobs.wait_for_stop, parallel_jobs.return_value), starter=starter
    )
    assert result is None
    assert "candidate の組の子を起動できなかった: no more processes (test)" in failure
    assert not (tmp_path / "candidate.pid").exists()
    assert not _alive(_pid(tmp_path, Variant.STANDARD))


def test_a_child_that_fails_at_once_is_noticed_before_the_second_launch(tmp_path: Path) -> None:
    """S1a + E4: 起動中に届いた失敗があれば、残りの子を起こさない。"""
    started: list[Variant] = []

    def starter(
        context: SpawnContext, job: VariantJob, stop: Event, lock_fd: int | None
    ) -> tuple[SpawnProcess, Connection]:
        process, conn = start_child(context, job, stop, lock_fd)
        started.append(job.variant)
        if job.variant is Variant.STANDARD:
            process.join(30)  # 最初の子が失敗し終えてから次の起動に進む
        return process, conn

    result, _, failure = _run(
        _jobs(tmp_path, parallel_jobs.raise_now, parallel_jobs.return_value), starter=starter
    )
    assert result is None
    assert started == [Variant.STANDARD]
    assert "standard の組の子が例外で終わった" in failure


# --- 親が合図を受ける・強制終了される（E6、子の表の F3）-------------------------------------


def _harness(tmp_path: Path, work: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.fixtures.evaluation.parallel_harness", str(tmp_path), work],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if all((tmp_path / f"{variant.value}.pid").exists() for variant in Variant):
            return process
        if process.poll() is not None:
            break
        time.sleep(0.05)
    process.kill()
    raise AssertionError("the harness did not start both children")


def _wait_gone(pids: Mapping[Variant, int], seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in pids.values()):
            return
        time.sleep(0.05)
    raise AssertionError(f"children still alive: {pids}")


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_a_human_interrupt_is_passed_to_both_children(tmp_path: Path, signum: int) -> None:
    """S1 + E6 → S6: 親は両方の子に同じ中断を送り、子が終わってから同じ中断で終わる。"""
    process = _harness(tmp_path, "wait_for_stop")
    pids = {variant: _pid(tmp_path, variant) for variant in Variant}
    process.send_signal(signum)
    code = process.wait(60)
    assert code != 0
    if signum == signal.SIGTERM:
        assert code == -signal.SIGTERM
    for pid in pids.values():
        assert not _alive(pid)
    with execution_lock(tmp_path / "lock"):
        pass


def test_children_stop_at_a_boundary_when_the_parent_is_killed(tmp_path: Path) -> None:
    """子の表の K2 + F3: 親が強制終了されたら、子は次の境目で何も返さずに終わる。"""
    process = _harness(tmp_path, "wait_for_stop")
    pids = {variant: _pid(tmp_path, variant) for variant in Variant}
    process.send_signal(signal.SIGKILL)
    process.wait(60)
    _wait_gone(pids)
    with execution_lock(tmp_path / "lock"):
        pass


def test_the_lock_stays_until_the_orphaned_children_end(tmp_path: Path) -> None:
    """仮置き 11: 親が強制終了された後も、子が残る間は同じ版の実行を拒否する。"""
    process = _harness(tmp_path, "ignore_stop_and_return")
    pids = {variant: _pid(tmp_path, variant) for variant in Variant}
    process.send_signal(signal.SIGKILL)
    process.wait(60)
    if any(_alive(pid) for pid in pids.values()):
        with pytest.raises(ExecutionLocked):
            with execution_lock(tmp_path / "lock"):
                pass
    _wait_gone(pids)
    with execution_lock(tmp_path / "lock"):
        pass
