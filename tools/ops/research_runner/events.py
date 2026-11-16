"""実行器の動きの記録 `runs/runner/log/events.jsonl`（版管理の外。週の要約の件数の材料。D25 §7）。

1 行が 1 つの出来事（`at`・`kind` と、識別子・件数・時間だけ）。成績の数値・判定の値を書かない。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from tools.ops.research_runner.state import format_time, parse_time

__all__ = ["EVENT_KINDS", "EventLog"]

#: 出来事の種類（週の要約の行に対応する）。
EVENT_KINDS: Final = frozenset(
    {
        "card_taken",
        "card_finished",
        "card_rejected",
        "run_done",
        "run_failed",
        "run_paused",
        "gate_pass",
        "gate_fail",
        "entrance_pass",
        "entrance_fail",
        "review_advance",
        "review_not",
        "review_deferred",
        "stop_new",
        "ideator_run",
        "cards_written",
        "cards_discarded",
        "deleted",
    }
)


@dataclass(frozen=True, slots=True)
class EventLog:
    path: Path

    def add(self, at: datetime, kind: str, **fields: Any) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"出来事の種類でない: {kind}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": format_time(at), "kind": kind, **fields}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def between(self, start: datetime, end: datetime) -> Iterator[Mapping[str, Any]]:
        """`[start, end)` の出来事（読めない行は飛ばす）。"""
        if not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                at = parse_time(item["at"])
            except (ValueError, KeyError, TypeError):
                continue
            if start <= at < end:
                yield item
