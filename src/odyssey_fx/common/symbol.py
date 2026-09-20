"""銘柄と銘柄仕様（D02 §5）。

`Symbol` は区切りなし6文字表記（上位設計書 §3.1）、`SymbolSpec` は価格刻み・数量刻みなどの
取引単位の仕様、`SymbolSpecRef` はその版と内容を固定する参照。spread・許容不利約定幅・費用は
銘柄仕様ではなく実行ポリシー（D06）に置く。

銘柄仕様の実体は `configs/symbols/` に置き、`app.config` が読み込む（D02 §5.2）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import CurrencyCode, kernel_context
from odyssey_fx.common.refs import ContentDigest

__all__ = ["Symbol", "SymbolSpec", "SymbolSpecRef"]

#: `Symbol.code` に許す字種（D02 §5.1）。照合は `fullmatch`（`$` は末尾の改行を許すため）。
_SYMBOL_PATTERN: Final = re.compile(r"^[A-Z]{6}$")


@dataclass(frozen=True, slots=True)
class Symbol:
    """通貨ペア（D02 §5.1）。区切りなし6文字表記（例: `USDJPY`）。"""

    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _SYMBOL_PATTERN.fullmatch(self.code):
            raise KernelValueError(f"Symbol must match ^[A-Z]{{6}}$, got {self.code!r}")

    @property
    def base(self) -> CurrencyCode:
        """基軸通貨（先頭3文字）。"""
        return CurrencyCode(self.code[:3])

    @property
    def quote(self) -> CurrencyCode:
        """決済通貨（末尾3文字）。"""
        return CurrencyCode(self.code[3:])

    def __str__(self) -> str:
        return self.code

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3: `Symbol` は `__str__`）。"""
        return self.code


def _require_positive(value: Decimal, label: str) -> Decimal:
    """有限かつ正の `Decimal` であることを確かめる。"""
    if not isinstance(value, Decimal):
        raise KernelValueError(f"{label} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise KernelValueError(f"{label} must be finite, got {value}")
    if value <= 0:
        raise KernelValueError(f"{label} must be > 0, got {value}")
    return value


def _is_integral_multiple(value: Decimal, unit: Decimal) -> bool:
    """`value` が `unit` の整数倍かを判定する。"""
    with localcontext(kernel_context()):
        return value % unit == 0


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    """銘柄の取引単位の仕様（D02 §5.2）。

    `lot_size` は表示・換算のための補助であり、計算の正本にはしない。
    """

    symbol: Symbol
    version: int
    price_tick: Decimal
    pip_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    lot_size: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise KernelValueError("SymbolSpec.symbol must be a Symbol")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise KernelValueError(f"SymbolSpec.version must be an int, got {self.version!r}")
        if self.version < 1:
            raise KernelValueError(f"SymbolSpec.version must be >= 1, got {self.version}")

        _require_positive(self.price_tick, "SymbolSpec.price_tick")
        _require_positive(self.pip_size, "SymbolSpec.pip_size")
        _require_positive(self.quantity_step, "SymbolSpec.quantity_step")
        _require_positive(self.min_quantity, "SymbolSpec.min_quantity")

        if not _is_integral_multiple(self.pip_size, self.price_tick):
            raise KernelValueError(
                f"SymbolSpec.pip_size ({self.pip_size}) must be an integral multiple of"
                f" price_tick ({self.price_tick})"
            )
        if self.min_quantity < self.quantity_step:
            raise KernelValueError(
                f"SymbolSpec.min_quantity ({self.min_quantity}) must be >="
                f" quantity_step ({self.quantity_step})"
            )
        if not _is_integral_multiple(self.min_quantity, self.quantity_step):
            raise KernelValueError(
                f"SymbolSpec.min_quantity ({self.min_quantity}) must be an integral multiple of"
                f" quantity_step ({self.quantity_step})"
            )
        if self.lot_size is not None:
            _require_positive(self.lot_size, "SymbolSpec.lot_size")


@dataclass(frozen=True, slots=True)
class SymbolSpecRef:
    """銘柄仕様の版と内容を固定する参照（D02 §5.2）。"""

    symbol: Symbol
    version: int
    digest: ContentDigest

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise KernelValueError("SymbolSpecRef.symbol must be a Symbol")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise KernelValueError(f"SymbolSpecRef.version must be an int, got {self.version!r}")
        if self.version < 1:
            raise KernelValueError(f"SymbolSpecRef.version must be >= 1, got {self.version}")
        if not isinstance(self.digest, ContentDigest):
            raise KernelValueError("SymbolSpecRef.digest must be a ContentDigest")
