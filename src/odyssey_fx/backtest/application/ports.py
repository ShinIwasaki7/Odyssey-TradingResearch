"""バックテストが外から受け取る口（D01 §4、D06 §3・§9.1）。

公開フィード・執行系列・カレンダーは `marketdata.application` の実装を `app` が注入する。
`backtest` は `marketdata.application` を import しない（契約 F6）。記録の書き出しは
`TraceSink` / `ResultWriter` 経由で、実装は `evaluation.adapters` または `app` が持つ。
**`backtest` は polars も Parquet も直接触らない**（契約 F5a）。

公開フィード・執行系列・カレンダーの構造そのものは `engine.loop` が定義している。使うのが
`engine` であり、`engine` は `application` を import できない（D01 §3.3 の層順序）ためで、
ここでは D06 §3 が挙げた名前で公開する。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from odyssey_fx.backtest.engine.loop import (
    Calendar,
    ExecutionSeries,
    IntrabarSeries,
    PublicationEventView,
    PublicationFeed,
)
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import BacktestResult

__all__ = [
    "Calendar",
    "ExecutionSeries",
    "IntrabarSeries",
    "PublicationEventView",
    "PublicationFeed",
    "ResultWriter",
    "TraceSink",
]


@runtime_checkable
class TraceSink(Protocol):
    """判断履歴の書き出し口（D06 §9.1）。書き出し専用で、検索元にはならない。"""

    def write(self, table: TraceTable, rows: tuple[object, ...]) -> None: ...


@runtime_checkable
class ResultWriter(Protocol):
    """結果 DTO と run manifest の保存口（D06 §9.1）。"""

    def write(self, result: BacktestResult, manifest: RunManifest) -> None: ...
