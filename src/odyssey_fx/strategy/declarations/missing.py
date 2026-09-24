"""入力が欠けたときの方針（D04 §6.3、Q8 決定。v1.9 で待機を追加）。

動作区分は `SKIP_EVALUATION` / `ERROR` / `WAIT_FOR_INPUT` / `USE_PREVIOUS` の4つである。
段階2の型は前2者だけを定義していた（Q8 決定、選択肢1）。D04 v1.9（2026-09-22）が残る
2区分のフィールドを確定したので、**段階3 の部品カタログが使う待機（`WaitForInput`）を
ここに足す**（D05 §9.2 の v2 契約が `on_missing` に書く）。過去値へ遡る区分
（`UsePrevious`）は、段階3 のカタログにそれを書く契約が無いため、まだ置かない。

待機の宣言を**動かす**のはランタイムの待機の仕組み（D05 §6.8）であり、コンパイラの能力
検査が待機を解禁するのも後続の変更である（D04 §12 の「段階3 で解除するもの」）。本
モジュールが持つのは宣言の形だけである。

- `SkipEvaluation`: 評価を行わず、**評価記録として残す**。False や価格 0 の出力に変換
  しない（上位設計書 §4.3.15）。
- `Error`: 実行失敗として扱う。ランタイムは `Failed(Reason(DATA_ERROR, …))` を記録し、
  以降の評価を行わずに戻る（D05 §6.3）。run を終了させるのはエンジンの責務である。
- `WaitForInput`: 入力が届くまで評価を保留する（D04 §6.3 v1.9、D05 §6.8）。期限・期限に
  到達したときの扱い・対象足が追い越されたときの扱いの3つを持つ。待機できる欠損理由は
  宣言では選ばない（Q15 決定。ランタイムの固定規則）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline, DurationDeadline
from odyssey_fx.strategy.declarations.refs import require_kind

__all__ = [
    "Error",
    "MissingInputPolicy",
    "OnSuperseded",
    "SkipEvaluation",
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


#: 区分タグ付き union（D04 §6.3）。段階2の2区分に、段階3 の待機を足した3区分。
MissingInputPolicy = SkipEvaluation | Error | WaitForInput
