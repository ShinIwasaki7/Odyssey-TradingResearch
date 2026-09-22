"""評価基盤へ渡す正規化 DTO と末尾の3集計（D06 §9.4・§10.3）。

評価側はこの DTO を改変しない。**集計前のレコードは `trace_tables` 経由で渡し、この DTO の
中で集計しない**（終端理由別・診断理由別の集計は D07 の責務であり、件数に畳んだ値だけを
渡すと集計規則が2つの文書に割れる）。

資産推移（balance と equity の系列）も持たない。台帳 snapshot の表が各判断時点の値を全件
持っており、そこから導いた系列を DTO にも置くと、同じ推移が2経路で読めて正本がどちらか
決める規則がもう1つ要る（D06 §9.4 v1.1）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from odyssey_fx.backtest.domain.fills import CostKind
from odyssey_fx.backtest.trace.manifest import DataCapabilityReport
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.money import Money

__all__ = ["BacktestResult", "FinalSummaries", "RunStatus"]


class RunStatus(Enum):
    """run の結末（D06 §9.4・§10.4）。"""

    COMPLETED = "COMPLETED"
    FAILED_DATA_ERROR = "FAILED_DATA_ERROR"
    FAILED_CAPABILITY = "FAILED_CAPABILITY"


@dataclass(frozen=True, slots=True)
class FinalSummaries:
    """末尾の3集計（D06 §10.3、上位設計書 §4.7.13 E）。

    `hypothetical_closed` は**計算だけ**行い、注文・約定・完了取引数・balance・リスク枠を
    変更しない。最終評価価格が無い場合はこの集計を組み立てず、`BacktestResult.summaries` を
    `None` にする（架空の価格で埋めない）。
    """

    realized: Money
    equity_with_mtm: Money
    hypothetical_closed: Money
    cost_breakdown: Mapping[CostKind, Money]

    def __post_init__(self) -> None:
        for name in ("realized", "equity_with_mtm", "hypothetical_closed"):
            if not isinstance(getattr(self, name), Money):
                raise KernelValueError(f"FinalSummaries.{name} must be a Money")
        if not isinstance(self.cost_breakdown, Mapping):
            raise KernelValueError("FinalSummaries.cost_breakdown must be a Mapping")
        for kind, amount in self.cost_breakdown.items():
            if not isinstance(kind, CostKind):
                raise KernelValueError("FinalSummaries.cost_breakdown keys must be CostKind")
            if not isinstance(amount, Money):
                raise KernelValueError("FinalSummaries.cost_breakdown values must be Money")


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """評価基盤へ渡す正規化 DTO（D06 §9.4）。"""

    run_id: RunId
    status: RunStatus
    trace_tables: Mapping[TraceTable, str]
    capability_report: DataCapabilityReport
    manifest_ref: str
    swap_modeled: bool = False
    unresolved_intrabar_count: int = 0
    trade_count: int = 0
    opportunity_count: int = 0
    summaries: FinalSummaries | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise KernelValueError("BacktestResult.run_id must be a RunId")
        if not isinstance(self.status, RunStatus):
            raise KernelValueError("BacktestResult.status must be a RunStatus")
        if not isinstance(self.trace_tables, Mapping):
            raise KernelValueError("BacktestResult.trace_tables must be a Mapping")
        missing = [table.value for table in TraceTable if table not in self.trace_tables]
        if missing:
            raise KernelValueError(
                f"BacktestResult.trace_tables must name all 15 tables (D06 §9.4); missing {missing}"
            )
        if not isinstance(self.capability_report, DataCapabilityReport):
            raise KernelValueError(
                "BacktestResult.capability_report must be a DataCapabilityReport"
            )
        if self.swap_modeled is not False:
            raise KernelValueError("BacktestResult.swap_modeled must be False (ADR-0029)")
        if (self.status is RunStatus.COMPLETED) != (self.summaries is not None):
            raise KernelValueError(
                "FinalSummaries is present exactly when the run completed (D06 §9.4)"
            )
