"""入力の読み取り条件（D04 §6.1・§6.2、上位設計書 §4.3.10）。

「いつの値を、どれだけ、どこまで古いものまで許して読むか」を入力ごとに宣言する。読み取り
条件を持つのは `InputSpec` だけで、接続先を表す `InputBinding` は上書きしない
（上位設計書 §4.3.5）。

| 区分 | 読み方 |
|---|---|
| `LatestAvailable` | 判断時刻までに利用可能な最新の1件 |
| `HistoryWindow` | 窓の本数・期間ぶんの確定足の列 |
| `DeliveredEvent` | その評価で配送されたイベント |
| `CurrentContext` | エンジンが供給する現在の建玉・口座 |

**「当該足を除く」の宣言**（D04 §6.2）: 直近 N 本高値のように、判断のもとになった足自身を
窓から外したい場合がある。D03 §6.2 の `end_offset_bars` に対応する宣言側の名前を
`exclude_latest_bars` とし、`HistoryWindow(BarsWindow(N), exclude_latest_bars=1)` と書く。

部分履歴は許さない（`min_samples` を持たない）。窓内の期待足がすべて存在しなければ欠損
として扱い、足りないまま計算しない（D03 §6.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.missing import (
    Error,
    MissingInputPolicy,
    SkipEvaluation,
    UsePrevious,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.validation import require_identifier, require_int

__all__ = [
    "BarsWindow",
    "CurrentContext",
    "DeliveredEvent",
    "DurationWindow",
    "HistoryWindow",
    "InputReadSpec",
    "LatestAvailable",
    "ParameterRef",
    "ReadWindow",
    "require_max_age",
    "require_missing_policy",
]


def require_missing_policy(value: object, label: str) -> MissingInputPolicy:
    """欠損方針の区分のいずれかであることを要求する（D04 §6.3）。

    段階2の2区分に、段階3 の待機（`WaitForInput`）と遡り（`UsePrevious`）を足した4区分を
    受け付ける（D04 v1.9）。どの読み方・接続元に書けるかはコンパイラが検査する
    （D04 §12 #10）。
    """
    if not isinstance(value, (SkipEvaluation, Error, WaitForInput, UsePrevious)):
        raise KernelValueError(
            f"{label} must be SkipEvaluation(), Error(), WaitForInput(...) or UsePrevious(...),"
            f" got {value!r}"
        )
    return value


def require_max_age(value: object, label: str) -> timedelta | None:
    """鮮度上限が `None` か正の `timedelta` であることを要求する（D04 §6.1）。

    0 と負の値を拒否するのは、「どの足も古すぎる」ことになり、宣言として意味を持たない
    ためである。判定そのもの（`decision_time - freshness_time > max_age`）はランタイムが
    行う（D03 §6.2 が委ねた）。
    """
    if value is None:
        return None
    if not isinstance(value, timedelta):
        raise KernelValueError(f"{label} must be a timedelta or None, got {value!r}")
    if value <= timedelta(0):
        raise KernelValueError(f"{label} must be positive, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class ParameterRef:
    """同じ部品のパラメータへの参照（上位設計書 §4.3.10）。

    窓の本数をパラメータで選べるようにするためのもので、コンパイル時に具体値へ解決する
    （D04 §12 #3）。ランタイムが解決済みの窓だけを受け取るのは D05 §5.3。
    """

    parameter_name: str

    def __post_init__(self) -> None:
        require_identifier(self.parameter_name, "ParameterRef.parameter_name")

    def __str__(self) -> str:
        return f"${self.parameter_name}"


@dataclass(frozen=True, slots=True)
class BarsWindow:
    """本数で指定する窓（D04 §6.2）。"""

    count: int | ParameterRef
    kind: str = "BARS"

    def __post_init__(self) -> None:
        require_kind(self.kind, "BARS", "BarsWindow.kind")
        if isinstance(self.count, ParameterRef):
            return
        count = require_int(self.count, "BarsWindow.count")
        if count < 1:
            raise KernelValueError(f"BarsWindow.count must be >= 1, got {count}")


@dataclass(frozen=True, slots=True)
class DurationWindow:
    """経過時間で指定する窓（D04 §6.2）。"""

    duration: timedelta
    kind: str = "DURATION"

    def __post_init__(self) -> None:
        require_kind(self.kind, "DURATION", "DurationWindow.kind")
        if not isinstance(self.duration, timedelta):
            raise KernelValueError(
                f"DurationWindow.duration must be a timedelta, got {self.duration!r}"
            )
        if self.duration <= timedelta(0):
            raise KernelValueError(
                f"DurationWindow.duration must be positive, got {self.duration!r}"
            )


#: 窓の2区分（D04 §6.2）。
ReadWindow = BarsWindow | DurationWindow


@dataclass(frozen=True, slots=True)
class LatestAvailable:
    """判断時刻までに利用可能な最新の1件を読む（D04 §6.1）。"""

    max_age: timedelta | None = None
    on_missing: MissingInputPolicy = SkipEvaluation()
    kind: str = "LATEST_AVAILABLE"

    def __post_init__(self) -> None:
        require_kind(self.kind, "LATEST_AVAILABLE", "LatestAvailable.kind")
        require_max_age(self.max_age, "LatestAvailable.max_age")
        require_missing_policy(self.on_missing, "LatestAvailable.on_missing")


@dataclass(frozen=True, slots=True)
class HistoryWindow:
    """窓ぶんの確定足を読む（D04 §6.1・§6.2）。"""

    window: ReadWindow
    max_age: timedelta | None = None
    on_missing: MissingInputPolicy = SkipEvaluation()
    exclude_latest_bars: int = 0
    kind: str = "HISTORY_WINDOW"

    def __post_init__(self) -> None:
        require_kind(self.kind, "HISTORY_WINDOW", "HistoryWindow.kind")
        if not isinstance(self.window, (BarsWindow, DurationWindow)):
            raise KernelValueError(
                f"HistoryWindow.window must be a BarsWindow or DurationWindow, got {self.window!r}"
            )
        require_max_age(self.max_age, "HistoryWindow.max_age")
        require_missing_policy(self.on_missing, "HistoryWindow.on_missing")
        exclude = require_int(self.exclude_latest_bars, "HistoryWindow.exclude_latest_bars")
        if exclude < 0:
            raise KernelValueError(f"HistoryWindow.exclude_latest_bars must be >= 0, got {exclude}")


@dataclass(frozen=True, slots=True)
class DeliveredEvent:
    """その評価で配送されたイベントを読む（D04 §6.1）。"""

    kind: str = "DELIVERED_EVENT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "DELIVERED_EVENT", "DeliveredEvent.kind")


@dataclass(frozen=True, slots=True)
class CurrentContext:
    """エンジンが供給する現在の建玉・口座を読む（D04 §6.1）。

    1回の評価の中で参照する時点情報であり、配送イベントでも履歴でもない。
    """

    kind: str = "CURRENT_CONTEXT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "CURRENT_CONTEXT", "CurrentContext.kind")


#: 区分タグ付き union（D04 §6.1）。
InputReadSpec = LatestAvailable | HistoryWindow | DeliveredEvent | CurrentContext
