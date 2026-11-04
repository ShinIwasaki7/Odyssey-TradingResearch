"""費用の値を pip の数で写す規則（D06 v1.20 案 §19.11。受入テスト CT-20。仮置き C1-21）。

8 通貨の銘柄の設定（`configs/symbols/`。価格を持たない）から第19.11節の表の値が出ること、写した
値が価格の刻みの整数倍でない人工の設定では丸めずに止まることを確かめる。実データ・成績・価格は
読まない。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final

import pytest

from odyssey_fx.app.config import ConfigError, load_symbol_spec, load_symbol_specs
from odyssey_fx.app.config.loader import load_yaml_mapping
from odyssey_fx.app.config.pip_copy import PriceUnitCosts, copy_costs_by_pips
from odyssey_fx.common.symbol import Symbol, SymbolSpec
from tests.fixtures.evaluation.adoption_review import REPO_ROOT

_SYMBOLS: Final = REPO_ROOT / "configs/symbols"
_USDJPY: Final = Symbol("USDJPY")
#: 研究ポリシー第 3 版の比較の前提の USDJPY の値（D06 §19.11。pip の数にすると 2・1・1・5）。
_SOURCE: Final = PriceUnitCosts(
    spread_offset=Decimal("0.02"),
    entry_slippage=Decimal("0.01"),
    close_slippage=Decimal("0.01"),
    adverse_fill_limit=Decimal("0.05"),
)
#: 第19.11節の表（円の決済通貨の 4 通貨と、それ以外の 4 通貨）。
_JPY_ROW: Final = PriceUnitCosts(
    spread_offset=Decimal("0.02"),
    entry_slippage=Decimal("0.01"),
    close_slippage=Decimal("0.01"),
    adverse_fill_limit=Decimal("0.05"),
)
_OTHER_ROW: Final = PriceUnitCosts(
    spread_offset=Decimal("0.0002"),
    entry_slippage=Decimal("0.0001"),
    close_slippage=Decimal("0.0001"),
    adverse_fill_limit=Decimal("0.0005"),
)
_TABLE: Final = {
    "USDJPY": _JPY_ROW,
    "EURJPY": _JPY_ROW,
    "GBPJPY": _JPY_ROW,
    "AUDJPY": _JPY_ROW,
    "EURUSD": _OTHER_ROW,
    "GBPUSD": _OTHER_ROW,
    "AUDUSD": _OTHER_ROW,
    "USDCHF": _OTHER_ROW,
}


def _specs() -> dict[Symbol, SymbolSpec]:
    return load_symbol_specs(_SYMBOLS)


def test_the_source_values_are_the_research_policy_v3_comparison_basis() -> None:
    """写す元の USDJPY の値は研究ポリシー第 3 版の比較の前提の値（設定ファイルの文字列）。"""
    payload: Any = load_yaml_mapping(
        REPO_ROOT / "configs/policies/research/research_policy_v3.yaml"
    )
    basis = payload["evaluation_standard"]["comparison_basis"]
    cost = basis["cost_model"]
    assert (
        PriceUnitCosts(
            spread_offset=Decimal(cost["spread_offset"]),
            entry_slippage=Decimal(cost["entry_slippage"]),
            close_slippage=Decimal(cost["close_slippage"]),
            adverse_fill_limit=Decimal(basis["execution_policy"]["adverse_fill_limits"]["USDJPY"]),
        )
        == _SOURCE
    )


@pytest.mark.parametrize("symbol", sorted(_TABLE))
def test_the_eight_pairs_reproduce_the_section_19_11_table(symbol: str) -> None:
    specs = _specs()
    copied = copy_costs_by_pips(
        _SOURCE, source_spec=specs[_USDJPY], target_spec=specs[Symbol(symbol)]
    )
    assert copied == _TABLE[symbol]
    for value in (
        copied.spread_offset,
        copied.entry_slippage,
        copied.close_slippage,
        copied.adverse_fill_limit,
    ):
        assert value % specs[Symbol(symbol)].price_tick == 0  # 刻みの整数倍（第19.11節の表の右端）


def test_the_commission_is_not_part_of_the_copy() -> None:
    """1 単位の手数料は写さない（円建てのまま。D17-11 (a)）ので、写す値の組に手数料が無い。"""
    assert set(PriceUnitCosts.__dataclass_fields__) == {
        "spread_offset",
        "entry_slippage",
        "close_slippage",
        "adverse_fill_limit",
    }


def _symbol_text(symbol: str, price_tick: str, pip_size: str) -> str:
    return (
        "schema_version: 1\n"
        f"symbol: {symbol}\n"
        "version: 1\n"
        f'price_tick: "{price_tick}"\n'
        f'pip_size: "{pip_size}"\n'
        'quantity_step: "1000"\n'
        'min_quantity: "1000"\n'
    )


def test_a_pip_size_that_is_not_a_tick_multiple_stops() -> None:
    """人工の銘柄の設定で `pip_size` が価格の刻みの整数倍でなければ、読込の時点で止まる。"""
    with pytest.raises(ConfigError, match="pip_size"):
        load_symbol_spec(_SYMBOLS / "EURUSD.yaml", text=_symbol_text("EURUSD", "0.00003", "0.0001"))


def test_a_copied_value_off_the_target_tick_stops_without_rounding() -> None:
    """写した値（2.5 pip）が刻み（1 pip）の整数倍でない人工の設定では、丸めずに止まる。"""
    coarse = load_symbol_spec(
        _SYMBOLS / "EURUSD.yaml", text=_symbol_text("EURUSD", "0.0001", "0.0001")
    )
    half_pip = PriceUnitCosts(
        spread_offset=Decimal("0.025"),
        entry_slippage=Decimal("0.01"),
        close_slippage=Decimal("0.01"),
        adverse_fill_limit=Decimal("0.05"),
    )
    with pytest.raises(ConfigError, match="spread_offset"):
        copy_costs_by_pips(half_pip, source_spec=_specs()[_USDJPY], target_spec=coarse)
    # 同じ値でも刻みが細かい通貨ペア（0.00001）なら刻みに乗るので写せる。
    copied = copy_costs_by_pips(
        half_pip, source_spec=_specs()[_USDJPY], target_spec=_specs()[Symbol("EURUSD")]
    )
    assert copied.spread_offset == Decimal("0.00025")


def test_a_value_that_does_not_divide_exactly_stops() -> None:
    """pip の数が割り切れない（循環小数になる）写しは止まる（値を丸めない）。"""
    odd_pip = load_symbol_spec(
        _SYMBOLS / "USDJPY.yaml", text=_symbol_text("USDJPY", "0.001", "0.003")
    )
    with pytest.raises(ConfigError, match="割り切れない"):
        copy_costs_by_pips(_SOURCE, source_spec=odd_pip, target_spec=_specs()[Symbol("EURUSD")])


@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("NaN")])
def test_negative_or_non_finite_costs_are_rejected(value: Decimal) -> None:
    with pytest.raises(ConfigError):
        PriceUnitCosts(
            spread_offset=value,
            entry_slippage=Decimal("0.01"),
            close_slippage=Decimal("0.01"),
            adverse_fill_limit=Decimal("0.05"),
        )
