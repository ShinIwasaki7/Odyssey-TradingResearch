"""探索の実験の標準と候補の組を 2 つの子プロセスで同時に走らせる仕組み（D26 §3。改善 2）。

本モジュールは**プロセスの起こし方・待ち方・止め方**だけを持つ（業務の規則は持たない）。組の中で
何をどの順に走らせるかは `RunExperiment.execute_variant`・`reproduce_variant` が決め、子に何を
渡すかは合成（`app.composition`）が決める。

- **方式**（D26-2 (a)）: `VariantMode`。コマンドの引数の既定は今の順番（`SERIAL`）。
- **空きメモリの確かめ**（D26 §3.3、仮置き 5。親の状態 S0）: `parallel_memory_shortfall`。足りない・
  確かめられないときは今の順番へ切り替え、その旨を表示する。
- **実行中の印のロック**（D26 仮置き 11・§3.8 の 3。親の状態 SP の最初）: `execution_lock`。版の
  ディレクトリの `execution.lock` に OS の排他ロック（`flock`）をかけ、子はそのロックを持った
  ファイル記述子を引き継ぐ。ロックは親と全部の子が終わるまで外れない（OS がプロセスの終わりで
  外す）。取れなければ `ExecutionLocked`（コマンドは何も書かずに専用の終了コードで終わる）。
- **親の状態機械**（D26 §3.4 の親の表。S1a〜S3）: `run_variant_jobs`。
- **子の状態機械**（D26 §3.4 の子の表。K0〜K3）: `_child_main`。単位の境目の確かめは
  `_Boundary`（中断の信号 → 停止の指示・親の消滅 → 残りの有無、の順）。

子は `spawn` で起こす（macOS の既定と同じ。親のメモリを引き継がない）。子は親と同じプロセス
グループに入る（新しいグループ・セッションを作らない。D26 §3.8 の 2）。
"""

from __future__ import annotations

import copyreg
import fcntl
import io
import multiprocessing
import os
import pickle
import signal
import subprocess
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from multiprocessing import connection, reduction
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext, SpawnProcess
from multiprocessing.synchronize import Event
from pathlib import Path
from types import FrameType, MappingProxyType
from typing import Any, Final, TextIO

from odyssey_fx.evaluation.application.run_experiment import VariantStopped
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant

__all__ = [
    "EXECUTION_LOCK_FILE",
    "PARALLEL_MEMORY_BYTES",
    "ExecutionLock",
    "ExecutionLocked",
    "ParallelVariantsFailed",
    "VariantJob",
    "VariantMode",
    "dumps",
    "execution_lock",
    "free_memory_bytes",
    "loads",
    "parallel_memory_shortfall",
    "run_variant_jobs",
]


class VariantMode(Enum):
    """組の走らせ方（D26-2 (a)。`experiment run`・`experiment reproduce` の引数）。"""

    SERIAL = "serial"
    PARALLEL = "parallel"


#: 実行中の印のファイル名（版のディレクトリの直下。中身は持たず、ロックだけが意味を持つ。
#: D26 §3.5）。
#: 同じ版の再実行の退避（D09 §11.3）の対象ではない（退避するのは結末記録・レポート・`search/`）。
EXECUTION_LOCK_FILE: Final = "execution.lock"

_GIB: Final = 1024**3
#: 組 1 つの子の最大メモリ（D26 §1.4・§3.3 の手順 0 の実測。H7 版 2 の 4 本で 4.42〜4.64 GiB）。
CHILD_PEAK_BYTES: Final = int(4.6 * _GIB)
#: 子を起こす前に読んだ足を手放した後の親の最大メモリ（D26 §3.3。PR-B の実測: H7 版 2 の実験設定で
#: 準備だけを行い、手放した後の常駐メモリが 0.75 GiB（手放す前 4.87 GiB、準備の最大 6.67 GiB）。
#: 切り上げて 0.8 GiB）。
PARENT_PEAK_BYTES: Final = int(0.8 * _GIB)
#: 並列で走らせるのに要る空きメモリ（組 1 つの最大メモリ × 2 + 手放した後の親の最大メモリ。D26
#: 仮置き 5。実行ごとの値ではなく既定値）。
PARALLEL_MEMORY_BYTES: Final = 2 * CHILD_PEAK_BYTES + PARENT_PEAK_BYTES


# --- 空きメモリ（親の状態 S0）---------------------------------------------------------------


def _vm_stat_bytes() -> int | None:
    """macOS: `vm_stat` の free・inactive・speculative のページの合計（D25 の仮置き 18 と同じ）。"""
    try:
        completed = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return parse_vm_stat(completed.stdout)


def parse_vm_stat(text: str) -> int | None:
    """`vm_stat` の出力から free・inactive・speculative のページの合計のバイト数を読む。"""
    lines = text.splitlines()
    if not lines or "page size of" not in lines[0]:
        return None
    try:
        page = int(lines[0].split("page size of")[1].split()[0])
    except (IndexError, ValueError):
        return None
    counts: dict[str, int] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        try:
            counts[name.strip()] = int(value.strip().rstrip("."))
        except ValueError:
            continue
    wanted = ("Pages free", "Pages inactive", "Pages speculative")
    if not all(name in counts for name in wanted):
        return None
    return page * sum(counts[name] for name in wanted)


def _meminfo_bytes() -> int | None:
    """Linux: `/proc/meminfo` の `MemAvailable`。"""
    try:
        text = Path("/proc/meminfo").read_text(encoding="ascii")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            try:
                return int(parts[1]) * 1024
            except (IndexError, ValueError):
                return None
    return None


def free_memory_bytes() -> int | None:
    """今の空きメモリ（バイト）。確かめられなければ `None`。"""
    if sys.platform == "darwin":
        return _vm_stat_bytes()
    return _meminfo_bytes()


def parallel_memory_shortfall(required: int = PARALLEL_MEMORY_BYTES) -> str | None:
    """並列で走らせるのに空きメモリが足りるか（D26 §3.3。S0 の E1・E2）。

    足りれば `None`、足りない・確かめられなければ表示の文（今の順番へ切り替える理由）。記録票の
    保存より前（何も書く前）に 1 回だけ呼ぶ。
    """
    free = free_memory_bytes()
    if free is None:
        return (
            "空きメモリを確かめられないので、組を順番に走らせる（D26 §3.3。並列には"
            f" {required / _GIB:.1f} GiB が要る）"
        )
    if free < required:
        return (
            f"空きメモリが足りない（空き {free / _GIB:.1f} GiB、並列には {required / _GIB:.1f} GiB"
            " が要る）ので、組を順番に走らせる（D26 §3.3）"
        )
    return None


# --- 実行中の印のロック（親の状態 SP の最初。仮置き 11）---------------------------------------


class ExecutionLocked(Exception):
    """同じ版の実行中の印のロックが取れない（同じ版の実行か、その子がまだ動いている）。

    コマンドは何も書かずに専用の終了コードで終わる（D26 §3.8 の 3）。
    """


@dataclass(frozen=True, slots=True)
class ExecutionLock:
    """取った実行中の印のロック（`fd` を子へ引き継ぐ）。"""

    path: Path
    fd: int


@contextmanager
def execution_lock(directory: Path) -> Iterator[ExecutionLock]:
    """版のディレクトリの実行中の印に排他ロックをかけ、終わったら自分の分を外す。

    版のディレクトリが無ければ作る（拒否では何も書かない決まり（D09 §10.7、D07 §19.4）の例外。
    印のファイルは中身を持たず、消さない。D26 §3.4 の後の注記）。ロックが取れなければ
    `ExecutionLocked`。子へ引き継いだ記述子は子が終わるまでロックを保つ。
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / EXECUTION_LOCK_FILE
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ExecutionLocked(
            f"{path} is locked: an execution of this experiment version (or one of its variant"
            " child processes) is still running; nothing was written. Wait until it stops at a"
            " unit boundary, then run again (D26 仮置き 11, §3.8 の 3)"
        ) from None
    except BaseException:
        os.close(fd)
        raise
    try:
        yield ExecutionLock(path=path, fd=fd)
    finally:
        os.close(fd)


# --- 子へ渡す値の直列化 --------------------------------------------------------------------


def _mapping_proxy(items: dict[Any, Any]) -> MappingProxyType[Any, Any]:
    return MappingProxyType(items)


def _reduce_mapping_proxy(proxy: MappingProxyType[Any, Any]) -> tuple[Any, ...]:
    return (_mapping_proxy, (dict(proxy),))


def dumps(value: object) -> bytes:
    """子との受け渡しの値を直列化する（読み取り専用の対応表 `MappingProxyType` も運ぶ）。"""
    buffer = io.BytesIO()
    pickler = pickle.Pickler(buffer, protocol=pickle.HIGHEST_PROTOCOL)
    table = copyreg.dispatch_table.copy()
    table[MappingProxyType] = _reduce_mapping_proxy
    pickler.dispatch_table = table
    pickler.dump(value)
    return buffer.getvalue()


def loads(data: bytes) -> Any:
    """`dumps` で直列化した値を戻す（自分の親・子が作った値だけを読む）。"""
    return pickle.loads(data)  # noqa: S301 - 同じ実行の親と子の間だけの受け渡し


# --- 子（D26 §3.4 の子の表）------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VariantJob:
    """子 1 つの仕事。`work(payload, boundary)` が組の結果を返す（D26 §3.5 の「子の入力」）。

    `work` はモジュールの関数（子で名前から読み込む）、`payload` は `dumps` で直列化した入力。
    """

    variant: Variant
    work: Callable[[bytes, Callable[[], None]], object]
    payload: bytes


class _ParentGone(VariantStopped):
    """親のプロセスが居ない（子の表の F3）。返す相手が居ないので何も返さずに終わる。"""


@dataclass(frozen=True, slots=True)
class _Boundary:
    """単位の境目の確かめ（子の表の K2）。中断の信号は Python が先に例外にする。"""

    stop: Event
    parent_pid: int

    def __call__(self) -> None:
        if self.stop.is_set():
            raise VariantStopped("the parent asked this variant to stop at a unit boundary")
        if os.getppid() != self.parent_pid:
            raise _ParentGone("the parent process is gone")


class _InheritedFd:
    """子へ引き継ぐファイル記述子（実行中の印のロック）。子では同じ開いたファイルを指す。"""

    __slots__ = ("fd",)

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __reduce__(self) -> tuple[Any, ...]:
        # 子を起こす最中にだけ直列化される（`spawn` の起動が記述子を子へ渡す）。
        return (_rebuild_fd, (reduction.DupFd(self.fd),))


def _rebuild_fd(duplicated: Any) -> int:
    fd = duplicated.detach()
    if not isinstance(fd, int):  # pragma: no cover - 標準ライブラリの約束
        raise TypeError("the inherited descriptor is not an int")
    return fd


def _send(conn: Connection, message: tuple[str, object]) -> None:
    """親へ知らせを 1 つ送る。親が居なければ（管が切れていれば）何もしない。"""
    try:
        conn.send_bytes(dumps(message))
    except OSError:
        return


def _child_main(
    job: VariantJob, conn: Connection, stop: Event, parent_pid: int, lock_fd: int | None
) -> None:
    """子の入口（K0 から始まる）。`lock_fd` は引き継いだロックの記述子（持っているだけでよい）。"""
    del lock_fd  # 開いたまま持つ（プロセスが終わると OS が閉じ、ロックが外れる）
    # 親は起動の間だけ中断の合図を保留する。子が保留を受け継いでいても、ここで外す（保留中に
    # 届いた合図はここで効く）。
    signal.pthread_sigmask(signal.SIG_UNBLOCK, _INTERRUPTS)
    boundary = _Boundary(stop=stop, parent_pid=parent_pid)
    try:
        result = job.work(job.payload, boundary)
    except _ParentGone:
        return
    except VariantStopped:
        _send(conn, ("stopped", None))
        return
    except KeyboardInterrupt:
        # 中断の信号（F4）: その場で止まる。親は自分の中断として扱う（親の表の E6）。
        return
    except BaseException as exc:  # noqa: BLE001 - 例外の型と文言を親へ返す（親の表の E4）
        _send(conn, ("error", _describe(exc)))
        return
    try:
        data = dumps(("ok", result))
    except BaseException as exc:  # noqa: BLE001 - 結果を返す途中の失敗も E4
        _send(conn, ("error", f"returning the result failed: {_describe(exc)}"))
        return
    try:
        conn.send_bytes(data)
    except OSError:
        # 親が居ない（返す相手が居ない。子の表の F3 と同じく何も返さずに終わる）。
        return


def _describe(exc: BaseException) -> str:
    text = "".join(traceback.format_exception(exc)).rstrip()
    return f"{type(exc).__name__}: {exc}\n{text}"


# --- 親（D26 §3.4 の親の表）------------------------------------------------------------


class ParallelVariantsFailed(Exception):
    """並列の実行が、子の失敗・停止・起動の失敗で終わった（親の状態 S6。終了コード 1）。

    送出するのは、起動できた子がすべて終わったことを確かめた後だけである。結末記録も台帳の
    結末の行も書いていない（D09 §10.12.4 の d）。
    """


class _Interrupted(BaseException):
    """親が受けた終了の合図（`SIGTERM`。親の表の E6）。"""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@dataclass(slots=True)
class _Child:
    variant: Variant
    process: SpawnProcess
    conn: Connection
    message: tuple[str, object] | None = None
    closed: bool = False
    finished: bool = False


#: 子を起こす関数（試験で起動の失敗を起こすために差し替える。親の表の E13）。
ChildStarter = Callable[
    [SpawnContext, VariantJob, Event, int | None], tuple[SpawnProcess, Connection]
]


def start_child(
    context: SpawnContext, job: VariantJob, stop: Event, lock_fd: int | None
) -> tuple[SpawnProcess, Connection]:
    """子を 1 つ起こす（`spawn`。親と同じプロセスグループ）。"""
    receiver, sender = context.Pipe(duplex=False)
    inherited = None if lock_fd is None else _InheritedFd(lock_fd)
    process = context.Process(
        target=_child_main,
        args=(job, sender, stop, os.getpid(), inherited),
        name=f"odyssey-fx-{job.variant.value}",
        daemon=False,
    )
    try:
        process.start()
    finally:
        sender.close()
    return process, receiver


@dataclass(slots=True)
class _Parent:
    """親の状態（S1a・S1・S2・S3）と、子から届いた出来事の扱い。"""

    stop: Event
    notify: Callable[[str], None]
    children: dict[Variant, _Child] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def fail(self, text: str) -> None:
        """失敗を表示し、動いている子に「境目で止める」を指示する（→ S3）。"""
        self.failures.append(text)
        self.notify(f"失敗: {text}")
        self.stop.set()

    def receive(self, child: _Child) -> None:
        """子からの知らせを 1 つ読む（E3・E4・E7）。読めずに閉じていれば何もしない（E5 は後で）。"""
        if child.closed:
            return
        try:
            data = child.conn.recv_bytes()
        except (EOFError, OSError):
            child.closed = True
            child.conn.close()
            return
        child.message = loads(data)
        child.closed = True
        child.conn.close()
        kind, value = child.message
        if kind == "error":
            self.fail(f"{child.variant.value} の組の子が例外で終わった: {value}")
        elif kind == "stopped":
            self.notify(f"{child.variant.value} の組の子は次の単位を始めずに止まった")

    def finish(self, child: _Child) -> None:
        """子のプロセスの終わり（E3・E4・E5・E7 のどれか）。"""
        if not child.closed and child.conn.poll():
            self.receive(child)
        child.process.join()
        child.finished = True
        if not child.closed:
            child.closed = True
            child.conn.close()
        if child.message is None:
            self.fail(
                f"{child.variant.value} の組の子が結果を返さずに終わった（終了コード"
                f" {child.process.exitcode}）"
            )

    def poll_started(self) -> None:
        """起動中（S1a）に、起こした子から既に届いた知らせを読む（待たない）。"""
        for child in self.children.values():
            if child.finished:
                continue
            if not child.closed and child.conn.poll():
                self.receive(child)
            if not child.process.is_alive():
                self.finish(child)

    def wait(self) -> None:
        """起こした子がすべて終わるまで待つ（S1・S2・S3）。"""
        while True:
            alive = [child for child in self.children.values() if not child.finished]
            if not alive:
                return
            waits: list[Any] = [child.process.sentinel for child in alive]
            waits.extend(child.conn for child in alive if not child.closed)
            ready = connection.wait(waits)
            for child in alive:
                if not child.closed and child.conn in ready:
                    self.receive(child)
                if child.process.sentinel in ready:
                    self.finish(child)

    def stop_all(self) -> None:
        """動いている子に「境目で止める」を指示し、全員の終わりを待つ（知らせは読まない）。

        先に受け口を閉じる（結果を送りかけの子が、親が読まないまま待ち続けないように）。
        """
        self.stop.set()
        self._close_and_join()

    def _close_and_join(self) -> None:
        for child in self.children.values():
            if not child.closed:
                child.closed = True
                child.conn.close()
        for child in self.children.values():
            if not child.finished:
                child.process.join()
                child.finished = True

    def interrupt(self, signum: int) -> None:
        """人間の中断（E6）: 動いている子に同じ中断を送り、終わりを待つ（境目を待たない）。"""
        for child in self.children.values():
            if child.finished or child.process.pid is None:
                continue
            try:
                os.kill(child.process.pid, signum)
            except ProcessLookupError:
                pass
        self._close_and_join()


#: 人間の中断の合図（親の表の E6）。
_INTERRUPTS: Final = frozenset({signal.SIGINT, signal.SIGTERM})


def _raise_interrupted(signum: int, frame: FrameType | None) -> None:
    del frame
    raise _Interrupted(signum)


def run_variant_jobs(
    jobs: Paired[VariantJob],
    *,
    lock: ExecutionLock | None,
    notify: Callable[[str], None] | None = None,
    starter: ChildStarter = start_child,
) -> Paired[object]:
    """両組の子を起こし、両方の結果を組の順で返す（D26 §3.4 の親の表の S1a〜S3）。

    - 子は標準 → 候補の順に 1 つずつ起こす（S1a）。起動に失敗したら（E13）、または起動中に届いた
      失敗（E4・E5）があれば、残りを起こさない。
    - 子の例外（E4）・結果も例外も返さない終わり（E5）・起動の失敗（E13）を受けたら、動いている子に
      「境目で止める」を指示し（S3）、すべての子が終わるのを待って `ParallelVariantsFailed`（S6）。
    - 親が `SIGINT`（Ctrl-C）か `SIGTERM` を受けたら（E6）、動いている子に同じ中断を送り、終わりを
      待ってから、同じ中断で親も終わる（`KeyboardInterrupt` を送出するか、`SIGTERM` で終わる）。
    - 両方の子が結果を返したときだけ、結果を返す（→ S4。終端の書き込みは呼び出し側）。
    """
    for variant, job in jobs.items():
        if job.variant is not variant:
            raise ValueError(f"the job for {variant.value} names {job.variant.value}")
    shown = notify if notify is not None else _stderr_line
    context = multiprocessing.get_context("spawn")
    parent = _Parent(stop=context.Event(), notify=shown)
    lock_fd = None if lock is None else lock.fd
    previous = signal.signal(signal.SIGTERM, _raise_interrupted)
    try:
        try:
            for variant, job in jobs.items():
                parent.poll_started()
                if parent.failures:
                    break
                # 子の起動から管理表への登録までは中断の合図を保留する（起動した子を必ず止めて
                # 待てるように。D26 §3.4 の S1a + E6）。保留した合図は登録の後に届く。
                previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _INTERRUPTS)
                try:
                    try:
                        process, conn = starter(context, job, parent.stop, lock_fd)
                    except Exception as exc:  # noqa: BLE001 - 起動の失敗（E13）
                        parent.fail(f"{variant.value} の組の子を起動できなかった: {exc}")
                        break
                    parent.children[variant] = _Child(variant=variant, process=process, conn=conn)
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            parent.wait()
        except _Interrupted as stopped:
            parent.interrupt(stopped.signum)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.raise_signal(stopped.signum)
            raise  # pragma: no cover - 上の合図で親は終わる
        except KeyboardInterrupt:
            parent.interrupt(signal.SIGINT)
            raise
        except BaseException:
            # 親の想定外の失敗（知らせが読めないなど）: 子を境目で止め、全員の終わりを待ってから
            # 送出する（親は子を残して終わらない。D26 §3.4 の表の後の注記）。
            parent.stop_all()
            raise
    finally:
        signal.signal(signal.SIGTERM, previous)
    if parent.failures:
        raise ParallelVariantsFailed(
            "the parallel execution stopped: "
            + " / ".join(parent.failures)
            + " (D26 §3.4。結末記録と試行台帳の結末の行は書いていない)"
        )
    results: dict[Variant, object] = {}
    for variant in VARIANTS:
        child = parent.children.get(variant)
        if child is None or child.message is None or child.message[0] != "ok":
            raise ParallelVariantsFailed(
                f"the {variant.value} variant returned no result (D26 §3.4)"
            )
        results[variant] = child.message[1]
    return Paired(standard=results[Variant.STANDARD], candidate=results[Variant.CANDIDATE])


def _stderr_line(text: str, stream: TextIO | None = None) -> None:
    target = sys.stderr if stream is None else stream
    target.write(text + "\n")
    target.flush()
