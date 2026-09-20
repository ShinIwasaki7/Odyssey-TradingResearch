"""銘柄仕様の読込（D02 §5.2、D03 §9、D01 §10.1）。

`configs/symbols/*.yaml` を読み、`common.symbol.SymbolSpec`（frozen dataclass）へ変換する。

**数値は文字列から `Decimal` を作る**（ADR-0012）。設定ファイル側も価格刻み・数量刻みを
文字列で書く決まりで、浮動小数として書かれた値はモデルの検証（`strict=True`）が型不一致
として拒否する。浮動小数を経由すると二進浮動小数の誤差が入り、刻みの整数倍という不変条件
が崩れる。

刻みどうしの整合（1 pip が価格刻みの整数倍であること、最小数量が数量刻みの整数倍で
あること）は `SymbolSpec` が構築時に検査する。ここでは設定ファイルの形だけを見る。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.scalars import require_decimal
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.symbol import Symbol, SymbolSpec

__all__ = ["load_symbol_spec", "load_symbol_specs"]

#: この実装が読む設定ファイルの形式版。未知の版は拒否する（D01 §10.1）。
SYMBOL_SCHEMA_VERSION = 1

#: 銘柄仕様ファイルの拡張子（`configs/symbols/*.yaml`）。
_SYMBOL_FILE_SUFFIX = ".yaml"


class _SymbolSpecModel(StrictModel):
    """`configs/symbols/*.yaml` の形（D02 §5.2）。

    刻みと数量は**文字列**で受ける。浮動小数として書かれた値はここで型不一致として
    拒否され、Decimal へ変換される経路に入らない（ADR-0012）。
    """

    schema_version: int
    symbol: str
    version: Annotated[int, Field(ge=1)]
    price_tick: str
    pip_size: str
    quantity_step: str
    min_quantity: str
    lot_size: str | None = None


def load_symbol_spec(path: Path) -> SymbolSpec:
    """銘柄仕様を1件読む（D02 §5.2、D03 §9）。"""
    payload = load_yaml_mapping(path)
    model = validate(_SymbolSpecModel, payload, path)
    require_schema_version(model.schema_version, SYMBOL_SCHEMA_VERSION, path)

    try:
        symbol = Symbol(model.symbol)
    except KernelValueError as exc:
        raise ConfigError(f"{path}: `symbol` が銘柄として成立しない: {exc}") from exc

    if path.stem != model.symbol:
        raise ConfigError(
            f"{path}: ファイル名 {path.stem!r} と `symbol` {model.symbol!r} が食い違う。"
            " 銘柄仕様はファイル名で引けることを前提に置いているため、一致させること"
        )

    try:
        return SymbolSpec(
            symbol=symbol,
            version=model.version,
            price_tick=require_decimal(model.price_tick, f"{path}: price_tick"),
            pip_size=require_decimal(model.pip_size, f"{path}: pip_size"),
            quantity_step=require_decimal(model.quantity_step, f"{path}: quantity_step"),
            min_quantity=require_decimal(model.min_quantity, f"{path}: min_quantity"),
            lot_size=(
                None
                if model.lot_size is None
                else require_decimal(model.lot_size, f"{path}: lot_size")
            ),
        )
    except KernelValueError as exc:
        raise ConfigError(f"{path}: 銘柄仕様として成立しない: {exc}") from exc


def load_symbol_specs(directory: Path) -> dict[Symbol, SymbolSpec]:
    """ディレクトリ配下の銘柄仕様をすべて読む（D03 §9・§10 の `--symbols`）。

    読む順はファイル名の昇順に固定する。ファイルシステムの列挙順に依存させると、同じ
    ディレクトリでも環境によって読む順が変わり、失敗の出方が揃わない。
    """
    if not directory.is_dir():
        raise ConfigError(f"銘柄仕様のディレクトリが見つからない: {directory}")
    specs: dict[Symbol, SymbolSpec] = {}
    for path in sorted(directory.glob(f"*{_SYMBOL_FILE_SUFFIX}")):
        spec = load_symbol_spec(path)
        if spec.symbol in specs:  # pragma: no cover - ファイル名と銘柄の一致を強制済み
            raise ConfigError(f"{directory}: 銘柄 {spec.symbol} の仕様が2度現れる")
        specs[spec.symbol] = spec
    if not specs:
        raise ConfigError(f"{directory}: 銘柄仕様（`*{_SYMBOL_FILE_SUFFIX}`）が1件も無い（D03 §9）")
    return specs
