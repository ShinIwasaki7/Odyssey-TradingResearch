"""設定ファイルの解析層（D01 §10.1、D03 §9、ADR-0018）。

設定ファイルを各パッケージの宣言型（frozen dataclass）へ変換し、スキーマ版・未宣言キー・
型不一致を検査する。**外部の検証ライブラリを使ってよいのはこの境界だけ**で、契約
"F5c: config parsers only in app.config"（D01 §6、ADR-0026）が `app` 配下全体からの
直接 import を禁じて機械検査する。

読込条件（D01 §10.1）は `loader` が1箇所で強制する。安全な読込、カスタムタグ禁止、
重複キーはエラー、merge key と anchor / alias の禁止、`schema_version` 必須。未宣言キーと
型不一致は Pydantic モデル（`models.StrictModel`）が拒否する。

**Pydantic モデルはこの層の外へ出さない**。外へ出るのは domain / application の型だけで
ある。

| 関数 | 読むもの | 返すもの |
|---|---|---|
| `load_calendar` | `configs/calendars/fx_ny17_v1.yaml` | `TradingCalendar` |
| `load_timeframes` | `configs/calendars/timeframes_v1.yaml` | `id` → `TimeframeDefinition` |
| `load_datasource` | `configs/datasources/legacy_merged_csv_v1.yaml` | `DataSourceConfig` |
| `load_symbol_specs` | `configs/symbols/` | `Symbol` → `SymbolSpec` |
| `load_closure_decisions` | 利用者が指定する分類ファイル | `ClosureDecisionFile` |
"""

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.app.config.datasources import DataSourceConfig, load_datasource, parse_file_name
from odyssey_fx.app.config.decisions import (
    ClosureDecisionFile,
    load_closure_decisions,
    parse_series_id,
)
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.symbols import load_symbol_spec, load_symbol_specs

__all__ = [
    "ClosureDecisionFile",
    "ConfigError",
    "DataSourceConfig",
    "load_calendar",
    "load_closure_decisions",
    "load_datasource",
    "load_symbol_spec",
    "load_symbol_specs",
    "load_timeframes",
    "parse_file_name",
    "parse_series_id",
]
