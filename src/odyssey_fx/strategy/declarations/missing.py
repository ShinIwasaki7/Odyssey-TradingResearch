"""入力が欠けたときの方針（D04 §6.3、Q8 決定。v1.9 で待機を追加）。

動作区分は `SKIP_EVALUATION` / `ERROR` / `WAIT_FOR_INPUT` / `USE_PREVIOUS` の4つである。
段階2の型は前2者だけを定義していた（Q8 決定、選択肢1）。D04 v1.9（2026-09-22）が残る
2区分のフィールドを確定したので、段階3 で**待機（`WaitForInput`）と遡り（`UsePrevious`）の
両方を**ここに置く（D04 §6.3、D05 §6.8・§6.9）。

待機と遡りを**動かす**のはランタイムの仕組み（D05 §6.8・§6.9）である。コンパイラは能力検査
から両者を外し、読み方と接続元の組合せだけを検査する（D04 §12 #10、D05 §5.6 の検査 c）。
本モジュールが持つのは宣言の形だけである。

- `SkipEvaluation`: 評価を行わず、**評価記録として残す**。False や価格 0 の出力に変換
  しない（上位設計書 §4.3.15）。
- `Error`: 実行失敗として扱う。ランタイムは `Failed(Reason(DATA_ERROR, …))` を記録し、
  以降の評価を行わずに戻る（D05 §6.3）。run を終了させるのはエンジンの責務である。
- `WaitForInput`: 入力が届くまで評価を保留する（D04 §6.3 v1.9、D05 §6.8）。期限・期限に
  到達したときの扱い・対象足が追い越されたときの扱いの3つを持つ。待機できる欠損理由は
  宣言では選ばない（Q15 決定。ランタイムの固定規則）。
- `UsePrevious`: 上限内の過去の有効値を明示的に使う（D04 §6.3 v1.9、D05 §6.9）。遡りを
  許す欠損理由は宣言で選ぶ（Q16 決定）。書けるのは最新1件の読み方で、接続元が市場データ
  参照のときだけである（D04 §12 #10。検査はコンパイラ）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline, DurationDeadline
from odyssey_fx.strategy.declarations.refs import require_kind
from odyssey_fx.strategy.declarations.validation import normalized_unique

if TYPE_CHECKING:
    from odyssey_fx.strategy.declarations.read_spec import BarsWindow, DurationWindow

__all__ = [
    "Error",
    "MissingInputPolicy",
    "OnSuperseded",
    "SkipEvaluation",
    "UsePrevious",
    "WaitDeadlineAction",
    "WaitForInput",
]


class WaitDeadlineAction(Enum):
    """待機の期限に到達したときの扱い（D04 §6.3 v1.9）。"""

    #: 評価を見送り、見送ったことを評価記録に残す。
    SKIP_EVALUATION = "SKIP_EVALUATION"
    #: 実行失敗として扱う。
    ERROR = "ERROR"


class OnSuperseded(Enum):
    """待機中の要求の対象足が、より新しい足に追い越されたときの扱い（D04 §6.3 v1.9）。"""

    #: 待機要求を失効させる（上位設計書 §4.3.14 の Trigger の標準方針）。
    EXPIRE_REQUEST = "EXPIRE_REQUEST"
    #: 期限まで待ち続ける。
    KEEP_WAITING = "KEEP_WAITING"


@dataclass(frozen=True, slots=True)
class SkipEvaluation:
    """評価を見送り、見送ったことを評価記録に残す（D04 §6.3）。"""

    kind: str = "SKIP_EVALUATION"

    def __post_init__(self) -> None:
        require_kind(self.kind, "SKIP_EVALUATION", "SkipEvaluation.kind")


@dataclass(frozen=True, slots=True)
class Error:
    """欠損を実行失敗として扱う（D04 §6.3）。"""

    kind: str = "ERROR"

    def __post_init__(self) -> None:
        require_kind(self.kind, "ERROR", "Error.kind")


@dataclass(frozen=True, slots=True)
class WaitForInput:
    """入力が届くまで評価を保留する（D04 §6.3 v1.9、D05 §6.8）。

    期限型は確認期限と同じ `BarsDeadline` / `DurationDeadline`（D04 §10.1）をそのまま使い、
    新しい期限型を作らない。3つのフィールドはいずれも必須で、暗黙の既定値を置かない
    （追い越しの既定をランタイムに持たせない。Q17 決定）。
    """

    deadline: BarsDeadline | DurationDeadline
    on_deadline: WaitDeadlineAction
    on_superseded: OnSuperseded
    kind: str = "WAIT_FOR_INPUT"

    def __post_init__(self) -> None:
        require_kind(self.kind, "WAIT_FOR_INPUT", "WaitForInput.kind")
        if not isinstance(self.deadline, (BarsDeadline, DurationDeadline)):
            raise KernelValueError(
                "WaitForInput.deadline must be a BarsDeadline or DurationDeadline,"
                f" got {self.deadline!r}"
            )
        if not isinstance(self.on_deadline, WaitDeadlineAction):
            raise KernelValueError(
                f"WaitForInput.on_deadline must be a WaitDeadlineAction, got {self.on_deadline!r}"
            )
        if not isinstance(self.on_superseded, OnSuperseded):
            raise KernelValueError(
                f"WaitForInput.on_superseded must be an OnSuperseded, got {self.on_superseded!r}"
            )


@dataclass(frozen=True, slots=True)
class UsePrevious:
    """上限内の過去の有効値を明示的に使う（D04 §6.3 v1.9、D05 §6.9）。

    窓型は履歴窓と同じ `BarsWindow` / `DurationWindow`（D04 §6.2）をそのまま使い、新しい窓型を
    作らない。遡りを許す欠損理由は1件以上を宣言で選ぶ（Q16 決定。破損データや計算例外まで
    一律に過去値で隠さないため）。

    欠損理由の列は**許す理由の集合**であり並びに意味が無いので、他の順序に意味を持たせない
    コレクションと同じく構築時に値の順へ整列し、重複を拒否する（D04 §3 の一般規則）。
    """

    max_lookback: BarsWindow | DurationWindow
    allowed_reasons: tuple[MissingInputReason, ...]
    kind: str = "USE_PREVIOUS"

    def __post_init__(self) -> None:
        # 窓型は `read_spec` が定義し、`read_spec` は本モジュールの欠損方針を import する。
        # 循環を避けるため、検査に使う型だけをここで読む。
        from odyssey_fx.strategy.declarations.read_spec import BarsWindow, DurationWindow

        require_kind(self.kind, "USE_PREVIOUS", "UsePrevious.kind")
        if not isinstance(self.max_lookback, (BarsWindow, DurationWindow)):
            raise KernelValueError(
                "UsePrevious.max_lookback must be a BarsWindow or DurationWindow,"
                f" got {self.max_lookback!r}"
            )
        if not isinstance(self.allowed_reasons, tuple) or not self.allowed_reasons:
            raise KernelValueError(
                "UsePrevious.allowed_reasons must be a non-empty tuple of MissingInputReason,"
                f" got {self.allowed_reasons!r}"
            )
        for reason in self.allowed_reasons:
            if not isinstance(reason, MissingInputReason):
                raise KernelValueError(
                    f"UsePrevious.allowed_reasons must hold MissingInputReason, got {reason!r}"
                )
        object.__setattr__(
            self,
            "allowed_reasons",
            normalized_unique(
                self.allowed_reasons,
                key=lambda reason: reason.value,
                label="UsePrevious.allowed_reasons",
            ),
        )


#: 区分タグ付き union（D04 §6.3）。段階2の2区分に、段階3 の待機と遡りを足した4区分。
MissingInputPolicy = SkipEvaluation | Error | WaitForInput | UsePrevious
