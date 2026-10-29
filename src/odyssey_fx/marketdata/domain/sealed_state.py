"""OANDA MT5 の経路の封印の区分の partition の初期状態（ADR-0038 第3項、ADR-0035 第3項）。

2 つの封印の区分（`HOLDOUT_SINCE_2024`・`FORWARD_HOLDOUT`）の partition は、受入れで作る時に
`SEALED` で始める（ADR-0038 第3項の 1。旧基盤での閲覧の有無は問わず、`legacy_access` を
使わない。第3項の 4）。例外は 2 つで、どちらも `CONSUMED` で始める。

1. 品質確認の閲覧の記録の判定の行に、同じ系列・重なる期間の違反の判定がある（ADR-0035
   第3項。PR #107 の要決定 S1-1）。
2. 既にある OANDA MT5 の経路の snapshot に、同じ系列・重なる期間の 2 つの封印の区分の
   partition があり、その状態が `CONSUMED`（PR #108 の仮置き S2a-9）。旧データの snapshot の
   `LEGACY_HOLDOUT` の状態は数えない。

状態を導けないとき（記録が読めない・構造の誤り・既にある partition の状態が分からない）は
`HoldoutStateUnknown` で止め、受入れを始めない（fail-closed）。記録を読み、既にある partition の
状態を導くのは呼び出し側（`app`）で、本モジュールは渡された事実から決めるだけである。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval
from odyssey_fx.marketdata.domain.access import OANDA_SEALED_CLASSES, HoldoutState
from odyssey_fx.marketdata.domain.errors import HoldoutStateUnknown, MarketDataValueError
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId

__all__ = [
    "ExistingSealedPartition",
    "InitialSealedState",
    "InitialStateReason",
    "ViolationScope",
    "initial_sealed_state",
]


class InitialStateReason(Enum):
    """`CONSUMED` で始めた理由（D03 §16.3.1 の検査 11 の「理由（例外 1・2）」）。"""

    #: 例外 1: 品質確認の閲覧の記録に、同じ系列・重なる期間の違反の判定がある。
    VIOLATION_JUDGMENT = "VIOLATION_JUDGMENT"
    #: 例外 2: 既にある OANDA MT5 の経路の snapshot の同じ系列・重なる期間の partition が
    #: `CONSUMED`。
    EXISTING_CONSUMED = "EXISTING_CONSUMED"


@dataclass(frozen=True, slots=True)
class ViolationScope:
    """違反と判定された閲覧が覆う範囲（ADR-0035 第3項）。

    `series` が `None` なら系列で絞らない（`symbol` で絞るか、すべての系列）。`symbol` が
    `None` なら銘柄で絞らない。`period` が `None` なら封印の区分の全体（原ファイルの期間が
    分からないとき。ADR-0035 第3項の「期間が分からなければ、封印の区分の全体と重なるとみなす」）。
    """

    series: SeriesId | None
    symbol: Symbol | None
    period: Interval | None

    def __post_init__(self) -> None:
        if self.series is not None and not isinstance(self.series, SeriesId):
            raise MarketDataValueError("ViolationScope.series must be a SeriesId or None")
        if self.symbol is not None and not isinstance(self.symbol, Symbol):
            raise MarketDataValueError("ViolationScope.symbol must be a Symbol or None")
        if self.period is not None and not isinstance(self.period, Interval):
            raise MarketDataValueError("ViolationScope.period must be an Interval or None")

    def covers(self, partition_id: PartitionId) -> bool:
        """この範囲が partition（同じ系列・重なる期間）に当たるか。"""
        if self.series is not None and partition_id.series != self.series:
            return False
        if self.symbol is not None and partition_id.series.symbol != self.symbol:
            return False
        if self.period is None:
            return True
        period = partition_id.period
        return period is None or period.overlaps(self.period)


@dataclass(frozen=True, slots=True)
class ExistingSealedPartition:
    """既にある snapshot の partition と、その導いた状態（`None` は導けなかった）。"""

    partition_id: PartitionId
    state: HoldoutState | None

    def __post_init__(self) -> None:
        if not isinstance(self.partition_id, PartitionId):
            raise MarketDataValueError("ExistingSealedPartition.partition_id must be a PartitionId")
        if self.state is not None and not isinstance(self.state, HoldoutState):
            raise MarketDataValueError("ExistingSealedPartition.state must be a HoldoutState")


@dataclass(frozen=True, slots=True)
class InitialSealedState:
    """初期状態と、`CONSUMED` で始めた理由（`SEALED` なら理由は空）。"""

    state: HoldoutState
    reasons: frozenset[InitialStateReason]

    def __post_init__(self) -> None:
        if not isinstance(self.state, HoldoutState):
            raise MarketDataValueError("InitialSealedState.state must be a HoldoutState")
        if not isinstance(self.reasons, frozenset) or not all(
            isinstance(reason, InitialStateReason) for reason in self.reasons
        ):
            raise MarketDataValueError("InitialSealedState.reasons must be a frozenset")
        if (self.state is HoldoutState.CONSUMED) != bool(self.reasons):
            raise MarketDataValueError(
                "InitialSealedState carries reasons exactly when it starts CONSUMED"
            )


def initial_sealed_state(
    partition_id: PartitionId,
    violations: Sequence[ViolationScope],
    existing: Sequence[ExistingSealedPartition],
) -> InitialSealedState:
    """OANDA MT5 の経路の封印の区分の partition の初期状態を決める（ADR-0038 第3項）。

    `existing` には既にあるすべての snapshot の partition を渡してよい。数えるのは 2 つの
    封印の区分（`OANDA_SEALED_CLASSES`）の、同じ系列・重なる期間のものだけで、旧データの
    snapshot の `LEGACY_HOLDOUT` などは数えない（ADR-0038 第3項の 3）。数える partition の
    状態が `None`（導けなかった）なら `HoldoutStateUnknown`（受入れを始めない）。
    """
    if not isinstance(partition_id, PartitionId):
        raise MarketDataValueError("initial_sealed_state requires a PartitionId")
    if partition_id.access_class not in OANDA_SEALED_CLASSES:
        raise MarketDataValueError(
            f"only the OANDA MT5 sealed classes start by this rule, got"
            f" {partition_id.access_class.value} (ADR-0038 第3項。旧データの snapshot は ADR-0014)"
        )
    period = partition_id.period
    assert period is not None  # noqa: S101  封印の区分の PartitionId は期間を持つ
    reasons: set[InitialStateReason] = set()
    for scope in violations:
        if not isinstance(scope, ViolationScope):
            raise MarketDataValueError("violations must contain ViolationScope")
        if scope.covers(partition_id):
            reasons.add(InitialStateReason.VIOLATION_JUDGMENT)
    unknown: list[str] = []
    for item in existing:
        if not isinstance(item, ExistingSealedPartition):
            raise MarketDataValueError("existing must contain ExistingSealedPartition")
        other = item.partition_id
        if other.access_class not in OANDA_SEALED_CLASSES or other.series != partition_id.series:
            continue
        other_period = other.period
        assert other_period is not None  # noqa: S101
        if not other_period.overlaps(period):
            continue
        if item.state is None:
            unknown.append(str(other))
        elif item.state is HoldoutState.CONSUMED:
            reasons.add(InitialStateReason.EXISTING_CONSUMED)
    if unknown:
        raise HoldoutStateUnknown(
            f"the state of the existing partitions {sorted(unknown)} that overlap {partition_id}"
            " cannot be derived; the acceptance does not start (ADR-0038 第3項の 3)"
        )
    if reasons:
        return InitialSealedState(state=HoldoutState.CONSUMED, reasons=frozenset(reasons))
    return InitialSealedState(state=HoldoutState.SEALED, reasons=frozenset())
