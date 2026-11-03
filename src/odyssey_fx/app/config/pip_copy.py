"""費用の値を pip の数で写す規則（D06 v1.20 案 §19.11。D17-11 (a)。仮置き C1-21）。

通貨ペア `p` の価格の単位の費用の値は `v_p = (v_USDJPY ÷ pip_USDJPY) × pip_p` で決める
（`pip` は銘柄の設定の `pip_size`。D02 §5.2、`configs/symbols/`）。USDJPY の値は研究ポリシー
第 3 版の比較の前提の値で、写すのはスプレッド・入場の滑り・決済の滑り・許容する不利約定の幅の
4 つ。**1 単位の手数料は写さない**（円建てのまま 8 通貨で同じ。D17-11 (a)）ので、本モジュールは
手数料を受け取らない。

**検査**（仮置き C1-21）: 写した値が通貨ペア `p` の価格の刻み（`price_tick`）の整数倍でなければ、
値を丸めずに止める（`ConfigError`）。値を黙って変えないためである。

価格を読まない計算で、銘柄の設定と比較の前提の値だけを使う。値を置く場所（機構確認の実験設定と
研究ポリシー第 4 版の比較の前提）の設計は本モジュールの外（D06 §19.11 の末尾）。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal, Inexact, localcontext

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.common.money import kernel_context
from odyssey_fx.common.symbol import SymbolSpec

__all__ = ["PriceUnitCosts", "copy_costs_by_pips"]


@dataclass(frozen=True, slots=True)
class PriceUnitCosts:
    """価格の単位の費用の値（通貨ペアの価格の刻みで表す 4 つ。D06 §19.11）。

    名前は実験設定の `cost_model`（`spread_offset`・`entry_slippage`・`close_slippage`）と
    `execution_policy.adverse_fill_limits` の値に揃える。手数料（`commission_per_unit`）は
    写さないので持たない。
    """

    spread_offset: Decimal
    entry_slippage: Decimal
    close_slippage: Decimal
    adverse_fill_limit: Decimal

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ConfigError(
                    f"PriceUnitCosts.{item.name} は 0 以上の有限の Decimal（{value!r}）"
                )


def _copy(value: Decimal, name: str, source: SymbolSpec, target: SymbolSpec) -> Decimal:
    """1 つの値を写す: `(value ÷ pip_source) × pip_target`。

    割り切れない・刻みに乗らないなら止める（丸めない）。
    """
    context = kernel_context()
    context.traps[Inexact] = True
    try:
        with localcontext(context):
            copied = value * target.pip_size / source.pip_size
            remainder = copied % target.price_tick
    except Inexact:
        raise ConfigError(
            f"{target.symbol} の {name}: {source.symbol} の値 {value} を pip の数で写すと"
            " 割り切れない（D06 §19.11。丸めない。仮置き C1-21）"
        ) from None
    if remainder != 0:
        raise ConfigError(
            f"{target.symbol} の {name}: pip の数で写した値 {copied} が価格の刻み"
            f" {target.price_tick} の整数倍でない（D06 §19.11。丸めずに止める。仮置き C1-21）"
        )
    return copied


def copy_costs_by_pips(
    source: PriceUnitCosts, *, source_spec: SymbolSpec, target_spec: SymbolSpec
) -> PriceUnitCosts:
    """`source_spec` の通貨ペア（USDJPY）の費用の値を、pip の数を保って `target_spec` へ写す。

    D06 §19.11 の規則 `v_p = (v_USDJPY ÷ pip_USDJPY) × pip_p` を 4 つの値に当てる。写した値が
    `target_spec.price_tick` の整数倍でなければ `ConfigError`（値を丸めない。仮置き C1-21）。
    """
    if not isinstance(source, PriceUnitCosts):
        raise ConfigError("copy_costs_by_pips は PriceUnitCosts を受け取る")
    if not isinstance(source_spec, SymbolSpec) or not isinstance(target_spec, SymbolSpec):
        raise ConfigError("copy_costs_by_pips は 2 つの SymbolSpec を受け取る")
    return PriceUnitCosts(
        **{
            item.name: _copy(getattr(source, item.name), item.name, source_spec, target_spec)
            for item in fields(PriceUnitCosts)
        }
    )
