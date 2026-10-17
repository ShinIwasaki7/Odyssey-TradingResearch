"""MT5 経路の列対応の宣言の読込（D03 §15.3 の 2、ADR-0018）。

`configs/datasources/oanda_mt5_m1_v<版>.yaml` を読む。HistData の経路の宣言
（`app.config.datasources`）とは形が違う（時刻の規約を表で決める、価格の 4 列だけを使う、出来高は
不明）ので、別の読込にした（D12 §11.13 の行 2 の置き場の候補 `app/config/datasources.py` を、
HistData の
宣言の形を変えないために分けた）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.marketdata.application.mt5_acceptance import MT5_ROUTE
from odyssey_fx.marketdata.domain.series import PriceBasis
from odyssey_fx.marketdata.domain.snapshot import BasisDeclaration

__all__ = ["MT5_COLUMNS", "Mt5DataSourceConfig", "load_mt5_datasource"]

_SCHEMA_VERSION = 1

#: 実装が読む列の並び（取得の道具が書いた見出し。D03 §15.3 の 2）。
MT5_COLUMNS: tuple[str, ...] = (
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
)


class _BasisModel(StrictModel):
    value: str
    verified: bool


class _Model(StrictModel):
    schema_version: int
    id: str
    version: int
    route: str
    root: str
    fetch_manifest: str
    docs_manifest_copy: str
    columns: list[str]
    minute_timeframe: str
    basis_declaration: _BasisModel


@dataclass(frozen=True, slots=True)
class Mt5DataSourceConfig:
    """MT5 経路の列対応の宣言。"""

    datasource_id: str
    version: int
    root: Path
    fetch_manifest: str
    docs_manifest_copy: str
    columns: tuple[str, ...]
    minute_timeframe: str
    basis_declaration: BasisDeclaration

    @property
    def ref(self) -> str:
        """manifest に書く列対応の宣言の版（`<id>@v<版>`）。"""
        return f"{self.datasource_id}@v{self.version}"

    def docs_copy_for(self, fetch_date: str) -> Path:
        """取得日の取得記録の版管理の写し。"""
        return Path(self.docs_manifest_copy.format(fetch_date=fetch_date))


def load_mt5_datasource(path: Path) -> Mt5DataSourceConfig:
    """宣言を読む。経路・列・価格基準が実装と食い違えば拒否する。"""
    payload = load_yaml_mapping(path)
    require_schema_version(payload["schema_version"], _SCHEMA_VERSION, path)
    model = validate(_Model, payload, path)
    if model.route != MT5_ROUTE:
        raise ConfigError(f"{path}: route は {MT5_ROUTE} でなければならない")
    if tuple(model.columns) != MT5_COLUMNS:
        raise ConfigError(f"{path}: columns は {list(MT5_COLUMNS)} でなければならない")
    if model.basis_declaration.value != PriceBasis.BID.value:
        raise ConfigError(f"{path}: MT5 経路の価格基準は bid の宣言（D12-26）")
    if model.basis_declaration.verified:
        raise ConfigError(f"{path}: 価格基準は検証済みの事実ではない（verified は偽）")
    if "{fetch_date}" not in model.docs_manifest_copy:
        raise ConfigError(f"{path}: docs_manifest_copy には {{fetch_date}} が要る")
    return Mt5DataSourceConfig(
        datasource_id=model.id,
        version=model.version,
        root=Path(model.root),
        fetch_manifest=model.fetch_manifest,
        docs_manifest_copy=model.docs_manifest_copy,
        columns=tuple(model.columns),
        minute_timeframe=model.minute_timeframe,
        basis_declaration=BasisDeclaration(value=PriceBasis.BID, verified=False),
    )
