"""処理点の採番（D02 §3.3、D06 §4.4）。

`ProcessingPoint` の全順序は `(time, phase.rank, sequence)` である。同じ時刻・同じフェーズで
複数の記録が生まれるとき、通し番号で区別しないと判断履歴の順序が一意に決まらない。本モジュ
ールは判断時刻ごとに 0 から始まる通し番号を配り、採番順が処理順に一致することを保証する。

これは `engine` が1 run に1つ持つ**可変の実行コンテキスト**であり、値ではない（D06 §1 が
`IdAllocator` と並べて認めている例外）。記録・ダイジェストの対象には含めない。
"""

from __future__ import annotations

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import PhaseSet, ProcessingPoint, UtcTime

__all__ = ["PhaseClock"]


class PhaseClock:
    """1つの判断時刻の中でフェーズごとの通し番号を配る。"""

    __slots__ = ("_counters", "_phases", "_time")

    def __init__(self, phases: PhaseSet, decision_time: UtcTime) -> None:
        if not isinstance(phases, PhaseSet):
            raise KernelValueError("PhaseClock requires a PhaseSet")
        if not isinstance(decision_time, UtcTime):
            raise KernelValueError("PhaseClock requires a UtcTime")
        self._phases = phases
        self._time = decision_time
        self._counters: dict[str, int] = {}

    @property
    def decision_time(self) -> UtcTime:
        """この時計が配る判断時刻。"""
        return self._time

    def next(self, phase_name: str) -> ProcessingPoint:
        """次の処理点を返す（同じフェーズ内で 0 から単調増加する）。"""
        phase = self._phases.by_name(phase_name)
        sequence = self._counters.get(phase_name, 0)
        self._counters[phase_name] = sequence + 1
        return ProcessingPoint(time=self._time, phase=phase, sequence=sequence)

    def peek(self, phase_name: str) -> ProcessingPoint:
        """採番せずに次の処理点を覗く（記録の突き合わせ用）。"""
        phase = self._phases.by_name(phase_name)
        return ProcessingPoint(
            time=self._time, phase=phase, sequence=self._counters.get(phase_name, 0)
        )
