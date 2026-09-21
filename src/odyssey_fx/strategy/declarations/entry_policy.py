"""発注方針（D04 §10.1）。

取引機会が生まれてから発注試行へ進むまでの扱いを2区分で表す。

| 区分 | 意味 | 段階 |
|---|---|---|
| `ImmediateEntry` | 後続確認なしで、そのまま発注試行へ進む | 2 |
| `AwaitConfirmation` | 後続確認の成立を期限付きで待つ | 宣言は段階2、実行は段階3 |

**再発火は `EntryPolicy` の責務から外す**（ADR-0032）。条件が成立し続けるときに再発火と
するかは取引機会を出す出力の `retrigger_mode`（D04 §10.4）が、複数の機会の関係は
`OpportunityConcurrencySpec`（D04 §10.3）が持つ。同じ規則を2か所に書かない。

`execution_filter`（後続確認の参照）が `None` なら `ImmediateEntry`、参照があれば
`AwaitConfirmation` であることをコンパイル時に照合する（D04 §12 #5）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.validation import require_int

__all__ = [
    "AwaitConfirmation",
    "BarsDeadline",
    "ConfirmationDeadline",
    "DeadlineAction",
    "DurationDeadline",
    "EntryPolicy",
    "ImmediateEntry",
]


class DeadlineAction(Enum):
    """確認期限に到達したときの扱い（D04 §10.1）。段階2の値は1つだけ。"""

    #: 期限切れの取引機会は終端理由 `EXPIRED`（上位設計書 §4.5）で終わる。
    EXPIRE = "EXPIRE"


@dataclass(frozen=True, slots=True)
class BarsDeadline:
    """確認期限を本数で数える（D04 §10.1）。Trigger の系列の確定足で数える。"""

    bars: int
    kind: str = "BARS"

    def __post_init__(self) -> None:
        require_kind(self.kind, "BARS", "BarsDeadline.kind")
        bars = require_int(self.bars, "BarsDeadline.bars")
        if bars < 1:
            raise KernelValueError(f"BarsDeadline.bars must be >= 1, got {bars}")


@dataclass(frozen=True, slots=True)
class DurationDeadline:
    """確認期限を経過時間で数える（D04 §10.1）。"""

    duration: timedelta
    kind: str = "DURATION"

    def __post_init__(self) -> None:
        require_kind(self.kind, "DURATION", "DurationDeadline.kind")
        if not isinstance(self.duration, timedelta):
            raise KernelValueError(
                f"DurationDeadline.duration must be a timedelta, got {self.duration!r}"
            )
        if self.duration <= timedelta(0):
            raise KernelValueError(
                f"DurationDeadline.duration must be positive, got {self.duration!r}"
            )


#: 確認期限の2区分（D04 §10.1）。
ConfirmationDeadline = BarsDeadline | DurationDeadline


@dataclass(frozen=True, slots=True)
class ImmediateEntry:
    """後続確認なしで発注試行へ進む（D04 §10.1）。段階2で使う区分。"""

    kind: str = "IMMEDIATE"

    def __post_init__(self) -> None:
        require_kind(self.kind, "IMMEDIATE", "ImmediateEntry.kind")


@dataclass(frozen=True, slots=True)
class AwaitConfirmation:
    """後続確認の成立を期限付きで待つ（D04 §10.1）。

    宣言としては段階2で定義するが、能力検査が拒否するため段階2の実行には現れない
    （D04 §12 の拒否一覧）。期限の数え方と確認評価の起動は D05 v0.2。
    """

    deadline: ConfirmationDeadline
    on_deadline: DeadlineAction = DeadlineAction.EXPIRE
    kind: str = "AWAIT_CONFIRMATION"

    def __post_init__(self) -> None:
        require_kind(self.kind, "AWAIT_CONFIRMATION", "AwaitConfirmation.kind")
        if not isinstance(self.deadline, (BarsDeadline, DurationDeadline)):
            raise KernelValueError(
                "AwaitConfirmation.deadline must be a BarsDeadline or DurationDeadline,"
                f" got {self.deadline!r}"
            )
        if not isinstance(self.on_deadline, DeadlineAction):
            raise KernelValueError(
                f"AwaitConfirmation.on_deadline must be a DeadlineAction, got {self.on_deadline!r}"
            )


#: 区分タグ付き union（D04 §10.1）。
EntryPolicy = ImmediateEntry | AwaitConfirmation
