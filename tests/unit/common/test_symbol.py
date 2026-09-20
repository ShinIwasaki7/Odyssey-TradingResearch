"""`odyssey_fx.common.symbol` の単体テスト（D02 §5・§11）。"""

from __future__ import annotations

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import CurrencyCode, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol, SymbolSpec, SymbolSpecRef

D = decimal_from_str
DIGEST = ContentDigest.sha256("a" * 64)
USDJPY = Symbol("USDJPY")


# --- Symbol -----------------------------------------------------------------


def test_symbol_splits_into_base_and_quote() -> None:
    assert USDJPY.base == CurrencyCode("USD")
    assert USDJPY.quote == CurrencyCode("JPY")
    assert str(USDJPY) == "USDJPY"


@pytest.mark.parametrize("code", ["usdjpy", "USD/JPY", "USDJPYX", "USDJP", "USDJP1", ""])
def test_symbol_rejects_invalid_codes(code: str) -> None:
    with pytest.raises(KernelValueError, match="Symbol"):
        Symbol(code)


# --- SymbolSpec -------------------------------------------------------------


def _spec(**overrides: object) -> SymbolSpec:
    fields: dict[str, object] = {
        "symbol": USDJPY,
        "version": 1,
        "price_tick": D("0.001"),
        "pip_size": D("0.01"),
        "quantity_step": D("1000"),
        "min_quantity": D("1000"),
        "lot_size": D("100000"),
    }
    fields.update(overrides)
    return SymbolSpec(**fields)  # type: ignore[arg-type]


def test_symbol_spec_accepts_a_usdjpy_style_specification() -> None:
    spec = _spec()
    assert spec.price_tick == D("0.001")
    assert spec.pip_size == D("0.01")
    assert spec.lot_size == D("100000")


def test_lot_size_is_optional() -> None:
    assert _spec(lot_size=None).lot_size is None


@pytest.mark.parametrize(
    "field",
    ["price_tick", "pip_size", "quantity_step", "min_quantity", "lot_size"],
)
def test_all_decimal_fields_must_be_positive(field: str) -> None:
    with pytest.raises(KernelValueError, match="> 0"):
        _spec(**{field: D("0")})


def test_pip_size_must_be_an_integral_multiple_of_price_tick() -> None:
    with pytest.raises(KernelValueError, match="pip_size"):
        _spec(price_tick=D("0.003"), pip_size=D("0.01"))
    assert _spec(price_tick=D("0.001"), pip_size=D("0.001")).pip_size == D("0.001")


def test_min_quantity_must_be_at_least_one_step() -> None:
    with pytest.raises(KernelValueError, match="must be >="):
        _spec(quantity_step=D("1000"), min_quantity=D("500"))


def test_min_quantity_must_be_an_integral_multiple_of_the_step() -> None:
    with pytest.raises(KernelValueError, match="integral multiple of"):
        _spec(quantity_step=D("1000"), min_quantity=D("1500"))
    assert _spec(quantity_step=D("1000"), min_quantity=D("5000")).min_quantity == D("5000")


@pytest.mark.parametrize("version", [0, -1])
def test_symbol_spec_version_must_be_at_least_one(version: int) -> None:
    with pytest.raises(KernelValueError, match=">= 1"):
        _spec(version=version)


def test_symbol_spec_requires_a_symbol() -> None:
    with pytest.raises(KernelValueError, match="Symbol"):
        _spec(symbol="USDJPY")


def test_symbol_spec_rejects_non_decimal_fields() -> None:
    with pytest.raises(KernelValueError, match="must be a Decimal"):
        _spec(price_tick=0.001)


# --- SymbolSpecRef ----------------------------------------------------------


def test_symbol_spec_ref_pins_symbol_version_and_content() -> None:
    ref = SymbolSpecRef(symbol=USDJPY, version=3, digest=DIGEST)
    assert (ref.symbol, ref.version, ref.digest) == (USDJPY, 3, DIGEST)


def test_symbol_spec_ref_validates_its_fields() -> None:
    with pytest.raises(KernelValueError, match="Symbol"):
        SymbolSpecRef(symbol="USDJPY", version=1, digest=DIGEST)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError, match=">= 1"):
        SymbolSpecRef(symbol=USDJPY, version=0, digest=DIGEST)
    with pytest.raises(KernelValueError, match="ContentDigest"):
        SymbolSpecRef(symbol=USDJPY, version=1, digest="a" * 64)  # type: ignore[arg-type]
