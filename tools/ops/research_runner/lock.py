"""「1 歩」の排他（`runs/runner/step.lock`。D25 §2.4）。

呼び出しはロックのファイルを排他的に作ってから進め、作れなければ何もせずに終わる。ロックを作った
後に止まったときは、作成の時刻が 30 分より古く、書いたプロセスが居なければ次の呼び出しが外す
（台帳のロックとは別物で、台帳のロックは自動で外さない。D09 §10.12.1）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from tools.ops.research_runner.state import format_time, parse_time

__all__ = ["STALE_AFTER", "StepLock"]

STALE_AFTER: Final = timedelta(minutes=30)


@dataclass
class StepLock:
    path: Path
    now: Callable[[], datetime]
    alive: Callable[[int], bool]
    held: bool = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._try_create():
            return True
        if self._stale():
            self.path.unlink(missing_ok=True)
            return self._try_create()
        return False

    def _try_create(self) -> bool:
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "created_at": format_time(self.now())}, handle)
        self.held = True
        return True

    def _stale(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            created = parse_time(data["created_at"])
            pid = int(data["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            # 書きかけのロック（作成と中身の書き込みの間に止まった）は、時刻を
            # ファイルの更新時刻で見る。
            try:
                created = datetime.fromtimestamp(self.path.stat().st_mtime, tz=self.now().tzinfo)
            except OSError:
                return False
            pid = -1
        return self.now() - created > STALE_AFTER and (pid < 0 or not self.alive(pid))

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False
