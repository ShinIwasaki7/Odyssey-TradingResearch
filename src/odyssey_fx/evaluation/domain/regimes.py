"""局面の語彙と、研究ポリシー第 5 版の局面の値の型（D24 §2.1・§5、決定 131・132・134・141）。

局面は日足だけから機械的に付ける相場の区分（向き 3 区分 × 値動きの大きさ 2 区分 = 6）で、
**表示・分析用だけ**である（決定 130・134・135。判定・選定・戦略に使わない）。本モジュールは
実装 PR 4-1 の範囲の語彙と値の型だけを持つ。区分の関数・日足のポート・損益の振り分けは実装
PR 4-3 で足す（D24 §10.1）。

- `RegimeLabel`: 6 局面と、付けられないときの `UNLABELED`（D24 の仮置き 13）。
- `RegimeSpec`: 研究ポリシーの `evaluation_standard.regimes` の値（D24 §2.1）。読込時の検査 E10
  （D24 §8.1）を構築時に当てる。役割の語彙は `DISPLAY_ONLY` の 1 つだけで、判定に使う値を書けない。
  局面をどの条件・選定からも参照する書き方は、研究ポリシーの語彙に無い（型として渡せない）。
- `favored_regimes_of`: 実験設定の任意のキー `favored_regimes`（仮説カードの「得意な局面」の宣言。
  決定 141、D24 の仮置き 16）の検査 E12。判定に使わない。

`evaluation.domain` に置く（決定 164（D24-7 (a)）。戦略（`strategy.*`）は `evaluation` を import
できないので、局面を戦略の入力に使う経路が構造的に無い。D01 §3.2）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError

__all__ = [
    "LABELED_REGIMES",
    "RegimeDirectionRule",
    "RegimeLabel",
    "RegimeLabelAt",
    "RegimeRole",
    "RegimeSpec",
    "RegimeTimeframe",
    "RegimeVolatilityRule",
    "favored_regimes_of",
]


class RegimeLabel(Enum):
    """局面の名前（D24 §5.1、仮置き 13）。向き × 値動きの組と、付けられないとき。"""

    UP_HIGH = "UP_HIGH"
    UP_LOW = "UP_LOW"
    FLAT_HIGH = "FLAT_HIGH"
    FLAT_LOW = "FLAT_LOW"
    DOWN_HIGH = "DOWN_HIGH"
    DOWN_LOW = "DOWN_LOW"
    #: 準備の日足が足りない日と ATR が 0 の日（D24 §5.1、仮置き 12）。
    UNLABELED = "UNLABELED"


#: 付けられた 6 局面（`UNLABELED` を除く。宣言順）。`favored_regimes` に書けるのはこの 6 つだけ。
LABELED_REGIMES: Final[tuple[RegimeLabel, ...]] = tuple(
    label for label in RegimeLabel if label is not RegimeLabel.UNLABELED
)


class RegimeRole(Enum):
    """局面の役割（D24 §2.1。語彙はこの 1 つ。判定に使う値を許さない。決定 134）。"""

    DISPLAY_ONLY = "DISPLAY_ONLY"


class RegimeTimeframe(Enum):
    """局面を付ける足（D24 §2.1。ニューヨーク 17 時の日足だけ）。"""

    DAILY = "1d"


class RegimeLabelAt(Enum):
    """局面を付ける時点（D24 §2.1・§5.1。D23 の仮置き 5、D03 §6.2）。

    取引の入場の時点に**公開済み**（公開時刻 `available_at` が入場の時点以前）の最後の日足の局面。
    """

    LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY = "LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY"


def _require_bars(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise KernelValueError(
            f"{label} は正の整数の本数で書くこと（D24 §8.1 の検査 E10）: {value!r}"
        )


@dataclass(frozen=True, slots=True)
class RegimeDirectionRule:
    """向きの軸の値（D24 §2.1・§5.1。決定 132 の値はファイルが持つ）。

    `g_t = (E_t − E_{t−slope_bars}) ÷ A_t`（`E` は終値の EMA `ema_bars` 本、`A` は ATR `atr_bars`
    本）を `±dead_band_atr_multiple` と比べる。
    """

    ema_bars: int
    slope_bars: int
    atr_bars: int
    dead_band_atr_multiple: Decimal

    def __post_init__(self) -> None:
        for name in ("ema_bars", "slope_bars", "atr_bars"):
            _require_bars(getattr(self, name), f"regimes.direction.{name}")
        value = self.dead_band_atr_multiple
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise KernelValueError(
                "regimes.direction.dead_band_atr_multiple は 0 以上の十進数で書くこと"
                f"（D24 §8.1 の検査 E10）: {value!r}"
            )


@dataclass(frozen=True, slots=True)
class RegimeVolatilityRule:
    """値動きの軸の値（D24 §2.1・§5.1）。`v_t = A_t ÷ C_t` をその日を含む直前
    `median_window_bars` 個の `v` の中央値と比べる（`A` は ATR `atr_bars` 本）。"""

    atr_bars: int
    median_window_bars: int

    def __post_init__(self) -> None:
        for name in ("atr_bars", "median_window_bars"):
            _require_bars(getattr(self, name), f"regimes.volatility.{name}")


@dataclass(frozen=True, slots=True)
class RegimeSpec:
    """研究ポリシーの `evaluation_standard.regimes`（D24 §2.1。版 5 以上で必須）。

    読込時の検査 E10（D24 §8.1）: `role` は `DISPLAY_ONLY` だけ、本数は正の整数、不感帯は 0 以上、
    `timeframe` と `label_at` は語彙の 1 値。語彙の外の値は列挙型の構築で読込が拒否する。
    """

    role: RegimeRole
    timeframe: RegimeTimeframe
    direction: RegimeDirectionRule
    volatility: RegimeVolatilityRule
    label_at: RegimeLabelAt

    def __post_init__(self) -> None:
        for name, expected in (
            ("role", RegimeRole),
            ("timeframe", RegimeTimeframe),
            ("direction", RegimeDirectionRule),
            ("volatility", RegimeVolatilityRule),
            ("label_at", RegimeLabelAt),
        ):
            if not isinstance(getattr(self, name), expected):
                raise KernelValueError(
                    f"regimes.{name} must be a {expected.__name__}（D24 §8.1 の検査 E10）"
                )


def favored_regimes_of(names: Sequence[str], label: str) -> tuple[RegimeLabel, ...]:
    """実験設定の `favored_regimes` を局面の名前の列にする（D24 §8.1 の検査 E12、仮置き 16）。

    局面の名前（`UNLABELED` を除く 6 つ）の重複の無い、空でない列だけを受ける。書いた順を保つ。
    違反は `KernelValueError`（読込が設定の誤りに言い換える）。
    """
    if isinstance(names, str) or not isinstance(names, Sequence):
        raise KernelValueError(f"{label} は局面の名前の列で書くこと（D24 §8.1 の検査 E12）")
    allowed = {item.value: item for item in LABELED_REGIMES}
    found: list[RegimeLabel] = []
    for index, name in enumerate(names):
        member = allowed.get(name) if isinstance(name, str) else None
        if member is None:
            raise KernelValueError(
                f"{label}[{index}] が {name!r} だが、書けるのは局面の名前"
                f" {[item.value for item in LABELED_REGIMES]} だけ（`UNLABELED` は書けない。"
                "D24 §8.1 の検査 E12）"
            )
        if member in found:
            raise KernelValueError(
                f"{label} に {name} が 2 回ある。局面の名前は重複させない（D24 §8.1 の検査 E12）"
            )
        found.append(member)
    if not found:
        raise KernelValueError(
            f"{label} が空である。宣言しないときはキーごと書かない（D24 §8.1 の検査 E12）"
        )
    return tuple(found)
