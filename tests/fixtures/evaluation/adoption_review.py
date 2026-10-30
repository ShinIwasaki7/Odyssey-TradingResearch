"""採用審査のテスト用の人工の値（D16 §13。実データも成績も使わない）。

審査台帳の行・完了取引の記録・試行台帳の行は、形だけを満たす人工の値で組み立てる。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Final

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId, FillId, PositionId
from odyssey_fx.common.money import CurrencyCode, Money, Price, Quantity, decimal_from_str
from odyssey_fx.common.refs import CodeDigest, ContentDigest, LockDigest, PolicyRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import PhaseRank, ProcessingPoint, UtcTime
from odyssey_fx.evaluation.domain.adoption_review import (
    ADOPTION_REVIEW_SCHEMA_VERSION,
    EVIDENCE_NOTE,
    AdoptionReviewEntry,
    AdoptionReviewLine,
    CostFitRow,
    CostModelRow,
    DeclarationState,
    DeclarationStatus,
    DsrSummary,
    EvidenceFloorRecord,
    FitOutcome,
    FloorOutcome,
    LedgerTally,
    ReviewReason,
    ReviewResult,
    ReviewRulesRef,
)
from odyssey_fx.evaluation.domain.metrics import TradeRecord, trade_outcome, trade_profit
from odyssey_fx.evaluation.domain.variant import Paired

__all__ = ["REPO_ROOT", "rules_ref", "review_entry", "review_lines", "trade"]

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
_JPY: Final = CurrencyCode("JPY")
_PHASE: Final = PhaseRank(rank=0, name="EXECUTION_OPEN")


def rules_ref(version: int = 1, name: str = "rules") -> ReviewRulesRef:
    return ReviewRulesRef(rules_id="adoption_review", version=version, digest=digest(name))


def review_entry(
    experiment_id: ExperimentId,
    execution: int = 1,
    *,
    result: ReviewResult = ReviewResult.DEFERRED,
    ref: ReviewRulesRef | None = None,
    nonce: str = "a" * 32,
) -> AdoptionReviewEntry:
    """審査台帳の 1 行（人工の値。数値は持たない）。"""
    reasons = {
        ReviewResult.PROCEED: (),
        ReviewResult.DO_NOT_PROCEED: (ReviewReason.EVIDENCE_FLOOR_NOT_MET,),
        ReviewResult.DEFERRED: (ReviewReason.DECLARATION_MISSING,),
    }[result]
    rows = tuple(
        CostFitRow(row=row, outcome=FitOutcome.FITS, capabilities=()) for row in CostModelRow
    )
    summary = DsrSummary(state="SHOWN", reasons=(), marks=(), assumption_reasons=())
    return AdoptionReviewEntry(
        schema_version=ADOPTION_REVIEW_SCHEMA_VERSION,
        experiment_id=experiment_id,
        execution=execution,
        experiment_name="exp",
        experiment_version=1,
        strategy_id="strategy_b",
        review_nonce=nonce,
        started_line_digest=digest("started"),
        finished_line_digest=digest("finished"),
        trial_ledger_head=digest("head"),
        research_policy_ref=PolicyRef(
            policy_kind="research", policy_id="research", version=3, digest=digest("policy")
        ),
        review_rules_ref=ref or rules_ref(),
        fixed_trials=Paired(standard=None, candidate=None),
        evidence_floor=EvidenceFloorRecord(
            outcomes=Paired(standard=FloorOutcome.MET, candidate=FloorOutcome.MET),
            note=EVIDENCE_NOTE,
        ),
        cost_model_fit=Paired(standard=rows, candidate=rows),
        deflated_sharpe=Paired(standard=summary, candidate=summary),
        ledger_tally=LedgerTally(1, 4, 1, 4, 1, 0, 0),
        declaration=DeclarationState(
            status=DeclarationStatus.MISSING
            if result is ReviewResult.DEFERRED
            else DeclarationStatus.NOT_DECLARED,
            file_digest=None if result is ReviewResult.DEFERRED else digest("declaration"),
        ),
        result=result,
        reasons=reasons,
        numeric_record_digest=digest("record"),
        code_digest=CodeDigest(digest("code")),
        lock_digest=LockDigest(digest("lock")),
    )


def review_lines(entries: list[AdoptionReviewEntry]) -> list[AdoptionReviewLine]:
    lines: list[AdoptionReviewLine] = []
    prev: ContentDigest | None = None
    for entry in entries:
        line = AdoptionReviewLine.of(entry, prev)
        lines.append(line)
        prev = line.digest
    return lines


def _money(text: str | None) -> Money | None:
    return None if text is None else Money(decimal_from_str(text), _JPY)


def trade(
    entry_at: str,
    exit_at: str,
    *,
    entry_price: str = "150.00",
    exit_price: str = "150.50",
    quantity: str = "10000",
    entry_spread: str | None = "200",
    close_spread: str | None = None,
    entry_slippage: str | None = "100",
    close_slippage: str | None = "100",
    seq: int = 1,
) -> TradeRecord:
    """完了取引 1 件（人工の値。費用は価格の分 × 数量の円。例: 0.02 × 10000 = 200 円）。"""
    entry = UtcTime.parse(entry_at)
    close = UtcTime.parse(exit_at)
    realized = Money(
        (decimal_from_str(exit_price) - decimal_from_str(entry_price)) * decimal_from_str(quantity),
        _JPY,
    )
    commission = Money(decimal_from_str("10"), _JPY)
    profit = trade_profit(realized, commission)
    return TradeRecord(
        trade_seq=seq,
        position_id=PositionId(seq=seq),
        opportunity_id=None,
        symbol=Symbol("USDJPY"),
        side=OrderSide.BUY,
        quantity=Quantity(decimal_from_str(quantity)),
        entry_fill_id=FillId(seq=2 * seq - 1),
        entry_price=Price(decimal_from_str(entry_price)),
        entry_at=ProcessingPoint(time=entry, phase=_PHASE, sequence=0),
        close_fill_id=FillId(seq=2 * seq),
        exit_price=Price(decimal_from_str(exit_price)),
        exit_at=ProcessingPoint(time=close, phase=_PHASE, sequence=0),
        close_cause=None,
        realized=realized,
        holding=timedelta(seconds=(close.value - entry.value).total_seconds()),
        outcome=trade_outcome(profit),
        entry_commission=commission,
        entry_slippage_in_price=_money(entry_slippage),
        entry_spread_in_price=_money(entry_spread),
        close_commission=None,
        close_slippage_in_price=_money(close_slippage),
        close_spread_in_price=_money(close_spread),
        trade_profit=profit,
    )
