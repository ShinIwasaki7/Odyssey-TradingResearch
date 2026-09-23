"""取引機会の状態機械（D05 §7）。

取引機会は「この条件で入る価値がある」という市場事実の記録である。生成されてから終わる
までの間に、確認・発注試行・失効といった段階を通る。状態と理由は別のフィールドで持ち、
**終端は1状態にまとめて区別は理由が担う**。状態名を増やすと終端理由の語彙が2系統になる
（語彙の正本は上位設計書 §4.5）。

| 状態 | 区分 | 意味 |
|---|---|---|
| `OPEN` | 非終端 | 生成され有効。確認待ち、または発注試行が可能 |
| `CONFIRMED` | 非終端 | 後続確認が成立した中間状態（段階3） |
| `ORDER_PENDING` | 非終端 | 注文要求をエンジンへ渡し、受付の可否が返っていない |
| `TERMINATED` | 終端 | 終端理由を伴って終わった |

**非終端のすべてを「有効な取引機会」と数える**（D05 §7.1）。受付待ちの機会を数から外すと、
同じ判断時点で同時保持の上限を超えた発注試行が並ぶ。

**終端した機会は復活させない**（ADR-0031）。終端済みの機会への遷移要求は拒否する。

遷移の一覧（D05 §7.2）:

| # | 遷移 | 発火条件 | 記録する理由 |
|---|---|---|---|
| 1 | （生成）→ `OPEN` | 発火し、有効な機会数が上限未満 | なし |
| 2 | （生成）→ `TERMINATED` | 上限に達していて既存を残す設定 | `CONCURRENCY_LIMIT_REACHED` |
| 3 | `OPEN`/`CONFIRMED` → `TERMINATED` | 上限に達していて新しい発火を優先する設定 | `SUPERSEDED` |
| 4 | `OPEN`/`CONFIRMED` → `ORDER_PENDING` | 注文意図と保護水準が揃った | なし |
| 5 | `ORDER_PENDING` → `TERMINATED` | 自身の注文が受け付けられた | 成立して役目を終えた |
| 6 | `ORDER_PENDING` → `TERMINATED` | 自身の注文が審査で拒否された | 発注試行が拒否された |
| 7 | 非終端 → `TERMINATED` | 他の機会の注文が受け付けられた | 他の受付により閉じた |
| 8 | `OPEN`/`CONFIRMED` → `TERMINATED` | 継続成立の条件が崩れた | 市場状態が無効になった |
| 9 | 非終端 → `TERMINATED` | run 末尾に残った | `RUN_END` |
| 10 | `OPEN` → `CONFIRMED` | 後続確認が成立した（段階3） | なし |
| 11 | `OPEN`/`CONFIRMED` → `TERMINATED` | 確認期限に到達した（段階3） | `EXPIRED` |

**生成時点を2つで表す**（D05 §7.1）: 処理点（どのフェーズで処理されたか）と判断時刻。置換
する相手を選ぶ鍵には**判断時刻**を使う。処理点を鍵にすると、フェーズ順位（D06）が決まるまで
鍵が定まらない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AttemptId, OpportunityId, OutputId
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.time import PhaseRank, ProcessingPoint, UtcTime
from odyssey_fx.strategy.declarations.refs import OutputRef
from odyssey_fx.strategy.declarations.validation import (
    require_bool,
    require_instance,
    require_tuple_of,
)
from odyssey_fx.strategy.records.payloads import Opportunity

__all__ = [
    "NON_TERMINAL_STATES",
    "TERMINAL_REASONS",
    "OpportunityLifecycle",
    "OpportunityState",
    "OpportunityTerminal",
    "OpportunityTransition",
    "ValiditySnapshot",
]


class OpportunityState(Enum):
    """取引機会の状態（D05 §7.1、Q1・Q2 決定）。"""

    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    ORDER_PENDING = "ORDER_PENDING"
    TERMINATED = "TERMINATED"


#: 「有効な取引機会」として数える状態（D05 §7.1）。
NON_TERMINAL_STATES = frozenset(
    {OpportunityState.OPEN, OpportunityState.CONFIRMED, OpportunityState.ORDER_PENDING}
)

#: 取引機会の終端に使ってよい理由コード（上位設計書 §4.5 が正本）。
TERMINAL_REASONS = frozenset(
    {
        ReasonCode.EXPIRED,
        ReasonCode.MARKET_STATE_INVALIDATED,
        ReasonCode.SUPERSEDED,
        ReasonCode.CLOSED_BY_ORDER_ACCEPTANCE,
        ReasonCode.CONCURRENCY_LIMIT_REACHED,
        ReasonCode.ORDER_ATTEMPT_REJECTED,
        ReasonCode.FULFILLED_BY_ORDER_ACCEPTANCE,
        ReasonCode.RUN_END,
    }
)


@dataclass(frozen=True, slots=True)
class ValiditySnapshot:
    """取引機会の生成時点で固定した条件の成否（D05 §7.3）。

    対象区間束縛（`SNAPSHOT_AT_OPPORTUNITY`）はここに固定し、以後更新しない。
    """

    source: OutputRef
    output_id: OutputId
    satisfied: bool

    def __post_init__(self) -> None:
        require_instance(self.source, OutputRef, "ValiditySnapshot.source")
        require_instance(self.output_id, OutputId, "ValiditySnapshot.output_id")
        require_bool(self.satisfied, "ValiditySnapshot.satisfied")


@dataclass(frozen=True, slots=True)
class OpportunityTerminal:
    """終端の理由と、終端した処理点（D05 §7.2）。"""

    reason: Reason
    at: ProcessingPoint

    def __post_init__(self) -> None:
        require_instance(self.reason, Reason, "OpportunityTerminal.reason")
        require_instance(self.at, ProcessingPoint, "OpportunityTerminal.at")
        if self.reason.code not in TERMINAL_REASONS:
            raise KernelValueError(
                f"{self.reason.code.value} is not a terminal reason for a trading opportunity"
                " (the vocabulary is fixed by the upstream design §4.5)"
            )


@dataclass(frozen=True, slots=True)
class OpportunityTransition:
    """遷移1件の記録（D05 §7.2）。

    生成は `from_state=None` で表す。置き換えた相手の機会や発注試行の識別子は `counterpart`
    と `attempt_id` に入れる。共通の理由（D02 §8.2）は1つの理由コードに固定して対応付く
    詳細型しか持てず、5つの終端理由を1つの詳細型では表せないためである。
    """

    opportunity_id: OpportunityId
    from_state: OpportunityState | None
    to_state: OpportunityState
    at: ProcessingPoint
    phase: PhaseRank
    reason: Reason | None = None
    counterpart: OpportunityId | None = None
    attempt_id: AttemptId | None = None

    def __post_init__(self) -> None:
        require_instance(self.opportunity_id, OpportunityId, "OpportunityTransition.opportunity_id")
        if self.from_state is not None:
            require_instance(self.from_state, OpportunityState, "OpportunityTransition.from_state")
        require_instance(self.to_state, OpportunityState, "OpportunityTransition.to_state")
        require_instance(self.at, ProcessingPoint, "OpportunityTransition.at")
        require_instance(self.phase, PhaseRank, "OpportunityTransition.phase")
        if self.reason is not None:
            require_instance(self.reason, Reason, "OpportunityTransition.reason")
        if self.counterpart is not None:
            require_instance(self.counterpart, OpportunityId, "OpportunityTransition.counterpart")
        if self.attempt_id is not None:
            require_instance(self.attempt_id, AttemptId, "OpportunityTransition.attempt_id")
        if self.from_state is OpportunityState.TERMINATED:
            raise KernelValueError(
                "a terminated opportunity is never revived (ADR-0031), so it cannot be the"
                " source of a transition"
            )
        if self.to_state is OpportunityState.TERMINATED and self.reason is None:
            raise KernelValueError("a terminating transition must record why it ended")
        if self.to_state is not OpportunityState.TERMINATED and self.reason is not None:
            raise KernelValueError(
                "a non-terminating transition must not record a terminal reason"
                f" (got {self.reason.code.value})"
            )
        if self.at.phase != self.phase:
            raise KernelValueError(
                "OpportunityTransition.phase must be the phase of its processing point"
                f" ({self.phase} != {self.at.phase})"
            )


@dataclass(frozen=True, slots=True)
class OpportunityLifecycle:
    """1つの取引機会の現在の姿（D05 §7.1）。

    **内容（`opportunity`）は不変**である。再検査で方向・対象区間・根拠値を変更しない
    （ADR-0031）。変わるのは状態・発注試行の識別子・終端だけで、状態が変わるたびに新しい
    `OpportunityLifecycle` へ差し替える。
    """

    opportunity: Opportunity
    state: OpportunityState
    created_at: ProcessingPoint
    created_decision_time: UtcTime
    snapshots: tuple[ValiditySnapshot, ...] = ()
    attempt_id: AttemptId | None = None
    terminal: OpportunityTerminal | None = None

    def __post_init__(self) -> None:
        require_instance(self.opportunity, Opportunity, "OpportunityLifecycle.opportunity")
        require_instance(self.state, OpportunityState, "OpportunityLifecycle.state")
        require_instance(self.created_at, ProcessingPoint, "OpportunityLifecycle.created_at")
        require_instance(
            self.created_decision_time, UtcTime, "OpportunityLifecycle.created_decision_time"
        )
        require_tuple_of(self.snapshots, ValiditySnapshot, "OpportunityLifecycle.snapshots")
        if self.attempt_id is not None:
            require_instance(self.attempt_id, AttemptId, "OpportunityLifecycle.attempt_id")
        if self.state is OpportunityState.TERMINATED:
            if self.terminal is None:
                raise KernelValueError(
                    "a terminated opportunity must carry the reason it ended (D05 §7.2)"
                )
        elif self.terminal is not None:
            raise KernelValueError(
                "a non-terminal opportunity must not carry a terminal reason"
                f" (state={self.state.value})"
            )

    @property
    def opportunity_id(self) -> OpportunityId:
        """この機会の識別子。"""
        return self.opportunity.opportunity_id

    @property
    def is_active(self) -> bool:
        """同時保持の数に入るか（D05 §7.1）。非終端はすべて数える。"""
        return self.state in NON_TERMINAL_STATES

    @property
    def is_replaceable(self) -> bool:
        """置換（`SUPERSEDED`）の対象になりうるか（D05 §7.4 の 3a）。

        発注試行中の機会は対象にしない。注文要求は既にエンジンへ渡っており、受付の可否が
        返る前に機会だけを終端させると、受け付けられた注文に対応する機会が無くなる。
        """
        return self.state in (OpportunityState.OPEN, OpportunityState.CONFIRMED)

    @property
    def supersession_key(self) -> tuple[str, int]:
        """置換する相手を選ぶ鍵（D04 §10.3、D05 §7.1）。

        `(生成時の判断時刻, 機会の連番)` の辞書式順序で**最小＝最も古い**を選ぶ。同じ判断
        時点に生成された機会が並んでも、連番が一意な決着を与える。
        """
        return (str(self.created_decision_time), self.opportunity_id.seq)

    def moved_to(
        self,
        state: OpportunityState,
        *,
        at: ProcessingPoint,
        reason: Reason | None = None,
        attempt_id: AttemptId | None = None,
    ) -> OpportunityLifecycle:
        """状態を移した新しい姿を返す（D05 §7.2）。

        終端済みからの遷移は拒否する（ADR-0031）。終端へ移すときは理由を必ず伴う。
        """
        if self.state is OpportunityState.TERMINATED:
            raise KernelValueError(
                f"opportunity {self.opportunity_id} is already terminated"
                f" ({self.terminal.reason.code.value if self.terminal else '?'});"
                " a terminated opportunity is never revived (ADR-0031)"
            )
        terminal: OpportunityTerminal | None = None
        if state is OpportunityState.TERMINATED:
            if reason is None:
                raise KernelValueError("terminating an opportunity requires a reason")
            terminal = OpportunityTerminal(reason=reason, at=at)
        elif reason is not None:
            raise KernelValueError(
                "only a terminating transition carries a reason"
                f" (state={state.value}, reason={reason.code.value})"
            )
        return OpportunityLifecycle(
            opportunity=self.opportunity,
            state=state,
            created_at=self.created_at,
            created_decision_time=self.created_decision_time,
            snapshots=self.snapshots,
            attempt_id=self.attempt_id if attempt_id is None else attempt_id,
            terminal=terminal,
        )
