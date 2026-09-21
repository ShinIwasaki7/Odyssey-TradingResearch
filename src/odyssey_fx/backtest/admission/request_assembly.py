"""要求組立と全順序化（D06 §6.1・§6.2・§6.3）。

因果的に準備できた要求の集合を作り、その中を**全順序**で処理する。鍵は
`(decision_time, request_class, strategy_priority, origin_seq, attempt_seq)` の辞書式昇順で、
決済（`CLOSE`）がエントリー（`ENTRY`）より先、`strategy_priority` は小さい値が先になる。

`AttemptId` は**順序を決めた後に採番する**（鍵に `attempt_seq` が入るのに採番が先だと循環
する）。そのため `attempt_seq` は常に同順位内の決着にだけ効き、採番順と処理順が一致する。
ID の比較は連番 `seq` で行い、文字列表現では比較しない（8桁ゼロ詰めは 99,999,999 を超える
と文字列順と数値順が食い違う）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from odyssey_fx.backtest.domain.orders import (
    CloseCause,
    CloseRequest,
    EntryRequest,
    ExitPlanRef,
    InitialProtectionPlan,
    OrderPayload,
    OrderRequest,
    OrderSide,
    OrderType,
    RequestClass,
    RequestOrigin,
)
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AccountId, AttemptId, IdAllocator, OutputId, RunId
from odyssey_fx.common.refs import CompiledStrategyRef, EvidenceRef
from odyssey_fx.common.time import ProcessingPoint, UtcTime
from odyssey_fx.strategy.records.payloads import ClosePosition, OrderIntent, TradeDirection
from odyssey_fx.strategy.runtime.requests import EntryProposal, ManagementRequest

__all__ = [
    "AdmissionKey",
    "build_close_payload",
    "build_entry_payload",
    "build_request",
    "entry_output_ids",
    "order_payloads",
    "side_for_direction",
]


@dataclass(frozen=True, slots=True)
class AdmissionKey:
    """受付の全順序の鍵（D06 §6.3）。辞書式昇順で処理する。"""

    decision_time: UtcTime
    request_class: RequestClass
    strategy_priority: int
    origin_seq: int
    attempt_seq: int

    def __post_init__(self) -> None:
        if not isinstance(self.decision_time, UtcTime):
            raise KernelValueError("AdmissionKey.decision_time must be a UtcTime")
        if not isinstance(self.request_class, RequestClass):
            raise KernelValueError("AdmissionKey.request_class must be a RequestClass")
        for name in ("strategy_priority", "origin_seq", "attempt_seq"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise KernelValueError(f"AdmissionKey.{name} must be an int")

    @property
    def sort_key(self) -> tuple[datetime, int, int, int, int]:
        """比較に使う組（ID は連番で比べる）。"""
        return (
            self.decision_time.value,
            self.request_class.rank,
            self.strategy_priority,
            self.origin_seq,
            self.attempt_seq,
        )

    def __lt__(self, other: AdmissionKey) -> bool:
        if not isinstance(other, AdmissionKey):
            return NotImplemented
        return self.sort_key < other.sort_key


def side_for_direction(direction: TradeDirection) -> OrderSide:
    """戦略側の方向を台帳側の売買方向へ移す（`LONG ↔ BUY` / `SHORT ↔ SELL`）。"""
    if not isinstance(direction, TradeDirection):
        raise KernelValueError("side_for_direction requires a TradeDirection")
    return OrderSide.BUY if direction is TradeDirection.LONG else OrderSide.SELL


def build_entry_payload(
    proposal: EntryProposal,
    *,
    compiled_ref: CompiledStrategyRef,
    exit_instance_id: str | None,
    default_valid_for: timedelta,
) -> EntryRequest:
    """`EntryProposal` から新規エントリーの要求内容を作る（D06 §6.1）。

    `OrderIntent.expiry` が `None` なら実行ポリシーの既定の有効時間に従う（D05 §4.3(3)）。
    根拠の出力 ID は提案がそのまま運んでくる（D05 §3 v1.2）。後から判断履歴を突き合わせて
    復元すると、同じ判断時点に同じ役割の出力が2件出た場合に一意に決まらない。
    """
    if not isinstance(proposal, EntryProposal):
        raise KernelValueError("build_entry_payload requires an EntryProposal")
    intent: OrderIntent = proposal.order_intent
    return EntryRequest(
        opportunity_id=proposal.opportunity_id,
        symbol=intent.symbol,
        side=side_for_direction(intent.direction),
        order_type=OrderType.MARKET,
        protection=InitialProtectionPlan(
            stop_loss=proposal.protection.stop_loss,
            source_output_id=proposal.protection_output_id,
        ),
        exit_plan_ref=ExitPlanRef(compiled_ref=compiled_ref, exit_instance_id=exit_instance_id),
        valid_for=default_valid_for if intent.expiry is None else intent.expiry,
        intent_output_id=proposal.intent_output_id,
    )


def build_close_payload(request: ManagementRequest, *, close_valid_for: timedelta) -> CloseRequest:
    """`ManagementRequest(ClosePosition)` から全数量決済の要求内容を作る（D06 §6.2）。"""
    if not isinstance(request, ManagementRequest):
        raise KernelValueError("build_close_payload requires a ManagementRequest")
    if not isinstance(request.action, ClosePosition):
        raise KernelValueError(
            "only a full-quantity close becomes an order; setting a take profit is applied to the"
            " position instead (D06 §6.2)"
        )
    return CloseRequest(
        position_id=request.position_id,
        cause=CloseCause.STRATEGY_EXIT,
        valid_for=close_valid_for,
        source_output_id=request.source_output_id,
    )


def order_payloads(
    payloads: Sequence[OrderPayload], *, strategy_priority: int = 0
) -> tuple[OrderPayload, ...]:
    """採番前の要求を全順序で並べる（D06 §6.3）。

    同じ判断時点の中では `decision_time` が等しいので、比較に効くのは
    `(request_class, strategy_priority, origin_seq)` である。
    """
    return tuple(
        sorted(
            payloads,
            key=lambda payload: (
                payload.request_class.rank,
                strategy_priority,
                payload.origin_seq,
            ),
        )
    )


def build_request(
    payload: OrderPayload,
    *,
    allocator: IdAllocator,
    run_id: RunId,
    account_id: AccountId,
    strategy_id: str,
    created_at: ProcessingPoint,
    origin: RequestOrigin,
    evidence_ref: EvidenceRef,
) -> OrderRequest:
    """並べ終えた要求へ `AttemptId` を採番して `OrderRequest` にする（D06 §6.1・§6.3）。"""
    return OrderRequest(
        run_id=run_id,
        attempt_id=allocator.next(AttemptId),
        account_id=account_id,
        strategy_id=strategy_id,
        created_at=created_at,
        origin=origin,
        payload=payload,
        evidence_ref=evidence_ref,
    )


def entry_output_ids(payload: OrderPayload) -> tuple[OutputId, ...]:
    """要求の根拠になった出力の識別子（根拠記録に載せる、D06 §9.2）。"""
    if isinstance(payload, EntryRequest):
        return (payload.intent_output_id, payload.protection.source_output_id)
    return () if payload.source_output_id is None else (payload.source_output_id,)
