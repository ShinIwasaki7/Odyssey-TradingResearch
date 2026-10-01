"""再取得（補充）の設定の読込（D03 §9・§14.5・§14.9・§14.10、D01 §10.1）。

- `load_refill_provider`: 提供元の設定 `configs/datasources/dukascopy_tick_v1.yaml` を読み、
  `ProviderRef`（設定の中身と、設定ファイルの正規化内容のダイジェスト）を返す。
- `load_calendar_ref`: 計画が使うカレンダーの設定ファイルを読み、`TradingCalendar` と
  `CalendarRef`（識別と版と正規化内容のダイジェストと正規化内容）を返す。

**設定ファイルの正規化内容のダイジェスト**は、YAML を読んだ mapping（コメント・書式は
含まない）の正規化エンコード（D02 §9.3）の sha256 である。同じ版のまま中身を変えれば別の
値になり、取得計画の識別子 `plan_id` も変わる（D03 §14.10）。

数値の価格は文字列で書く（`pip_size: "0.01"`。浮動小数を経由しない。ADR-0012）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from odyssey_fx.app.config.calendars import load_calendar
from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import (
    CalendarRef,
    CommunicationSettings,
    ProviderRef,
    ProviderSettings,
    ProviderSymbol,
    content_digest_of,
)

__all__ = ["load_calendar_ref", "load_refill_provider"]

#: この実装が読む提供元の設定の形式版。未知の版は拒否する（D01 §10.1）。
PROVIDER_SCHEMA_VERSION = 1


class _SymbolModel(StrictModel):
    """銘柄ごとの価格の桁と pip の大きさ（D03 §14.5）。"""

    symbol: str
    price_scale: Annotated[int, Field(ge=1)]
    pip_size: str


class _CommunicationModel(StrictModel):
    """通信の間隔と再試行の値（D03 §14.9。秒）。"""

    request_interval_seconds: Annotated[int, Field(ge=0)]
    request_timeout_seconds: Annotated[int, Field(ge=1)]
    max_retries: Annotated[int, Field(ge=0)]
    backoff_initial_seconds: Annotated[int, Field(ge=0)]
    backoff_max_seconds: Annotated[int, Field(ge=0)]
    pause_after_consecutive_failures: Annotated[int, Field(ge=1)]
    pause_seconds: Annotated[int, Field(ge=0)]


class _ProviderModel(StrictModel):
    """`configs/datasources/dukascopy_tick_v1.yaml` の形（D03 §9・§14.5）。"""

    schema_version: int
    id: str
    version: Annotated[int, Field(ge=1)]
    url_template: str
    symbols: Annotated[list[_SymbolModel], Field(min_length=1)]
    communication: _CommunicationModel


def _content_digest(payload: dict[str, Any], path: Path) -> str:
    try:
        return content_digest_of(payload)
    except (MarketDataValueError, KernelValueError) as exc:
        raise ConfigError(f"{path}: 設定の正規化内容のダイジェストを作れない: {exc}") from exc


def load_refill_provider(path: Path) -> ProviderRef:
    """提供元の設定を読む（D03 §14.5）。"""
    payload = load_yaml_mapping(path)
    model = validate(_ProviderModel, payload, path)
    require_schema_version(model.schema_version, PROVIDER_SCHEMA_VERSION, path)
    if not model.url_template.startswith("https://"):
        raise ConfigError(f"{path}: `url_template` は https の URL でなければならない")
    try:
        symbols = tuple(
            ProviderSymbol(
                symbol=Symbol(entry.symbol),
                price_scale=entry.price_scale,
                pip_size=decimal_from_str(entry.pip_size),
            )
            for entry in model.symbols
        )
        settings = ProviderSettings(
            id=model.id,
            version=model.version,
            url_template=model.url_template,
            symbols=symbols,
            communication=CommunicationSettings(
                request_interval_seconds=model.communication.request_interval_seconds,
                request_timeout_seconds=model.communication.request_timeout_seconds,
                max_retries=model.communication.max_retries,
                backoff_initial_seconds=model.communication.backoff_initial_seconds,
                backoff_max_seconds=model.communication.backoff_max_seconds,
                pause_after_consecutive_failures=(
                    model.communication.pause_after_consecutive_failures
                ),
                pause_seconds=model.communication.pause_seconds,
            ),
        )
    except (MarketDataValueError, KernelValueError) as exc:
        raise ConfigError(f"{path}: 提供元の設定として成立しない: {exc}") from exc
    return ProviderRef(content_digest=_content_digest(payload, path), settings=settings)


def load_calendar_ref(path: Path) -> tuple[TradingCalendar, CalendarRef]:
    """カレンダーと、その識別・版・正規化内容のダイジェストを読む（D03 §14.10）。"""
    calendar = load_calendar(path)
    payload = load_yaml_mapping(path)
    try:
        reference = CalendarRef(
            id=calendar.id,
            version=calendar.version,
            content_digest=_content_digest(payload, path),
            content=payload,
        )
    except (MarketDataValueError, KernelValueError) as exc:
        raise ConfigError(f"{path}: カレンダーの記録を作れない: {exc}") from exc
    return calendar, reference
