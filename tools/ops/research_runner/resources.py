"""空き（スロット）・メモリ・ディスク・見積りの計算（D25 §4.4・§4.5・§4.6、決定 146・147）。

- **空き**: 計算の重い処理は同時に 2 つまで（決定 146）。並列を要求して起動した探索の run は 2 つと
  数え、順番を要求したものと再現・審査・最終検証は 1 つと数える（D26 の決定「① (a)、」）。
- **並列の条件**（仮置き 37）: 並列の実験は、走っている重い処理が 0 のときだけ起動する。1 つ走って
  いれば順番（1 つ）で、空きメモリ 8 GiB 以上のときだけ起動する。
- **空きメモリ**（仮置き 18）: `vm_stat` の free・inactive・speculative のページの合計。
- **ディスク**（決定 147）: 重い処理の起動の前に 25 GB 以上。
- **見積り**（仮置き 17）: 模擬する日数 2 組 ×（5 × N × 728 + 1,743 + 1,743）日 × 約 1.40 秒/日。
  上限は見積りの 2 倍（探索の run だけ。仮置き 31）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DISK_FLOOR_BYTES",
    "MAX_SLOTS",
    "MEMORY_FLOOR_BYTES",
    "PARALLEL",
    "SEQUENTIAL",
    "SlotPlan",
    "estimate_seconds",
    "free_memory_from_vm_stat",
    "plan_slots",
]

MAX_SLOTS: Final = 2
MEMORY_FLOOR_BYTES: Final = 8 * 1024**3
DISK_FLOOR_BYTES: Final = 25 * 1000**3
PARALLEL: Final = "parallel"
SEQUENTIAL: Final = "sequential"
#: 仮置き 17 の式の値（D22 §2.4）。
_DAYS_PER_TRIAL: Final = 5 * 728
_FIXED_DAYS: Final = 1743 + 1743
_VARIANTS: Final = 2
_SECONDS_PER_DAY: Final = 1.40


def estimate_seconds(trials: int) -> int:
    """1 実験の見積り（秒。成績を使わずに決まる）。"""
    if trials < 1:
        raise ValueError("試行の数は 1 以上")
    days = _VARIANTS * (_DAYS_PER_TRIAL * trials + _FIXED_DAYS)
    return int(days * _SECONDS_PER_DAY)


@dataclass(frozen=True, slots=True)
class SlotPlan:
    """起動してよいか・何を要求するか（`mode` は探索の run の方式。他の処理は `None`）。"""

    allowed: bool
    slots: int
    mode: str | None
    why: str = ""


def plan_slots(*, used: int, heavy_running: int, free_memory: int, run: bool) -> SlotPlan:
    """起動の前の確かめの 1（D25 §4.4）。`run` は探索の run か（並列を要求できるもの）。"""
    if used >= MAX_SLOTS:
        return SlotPlan(False, 0, None, "空きが無い")
    if run and heavy_running == 0 and used == 0:
        return SlotPlan(True, 2, PARALLEL)
    if heavy_running >= 1 and free_memory < MEMORY_FLOOR_BYTES:
        return SlotPlan(False, 0, None, "空きメモリが 8 GiB 未満")
    return SlotPlan(True, 1, SEQUENTIAL if run else None)


_PAGE_SIZE: Final = re.compile(r"page size of ([0-9]+) bytes")
_PAGES: Final = re.compile(r"^Pages (free|inactive|speculative):\s+([0-9]+)\.", re.MULTILINE)


def free_memory_from_vm_stat(text: str) -> int:
    """`vm_stat` の出力から再利用できるメモリの近似（バイト。仮置き 18）。読めなければ 0。"""
    page = _PAGE_SIZE.search(text)
    if page is None:
        return 0
    pages = sum(int(count) for _, count in _PAGES.findall(text))
    return pages * int(page.group(1))
