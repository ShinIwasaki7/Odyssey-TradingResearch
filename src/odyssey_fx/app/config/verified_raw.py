"""研究期間の原ファイルの確認済みハッシュの一覧の読込（ADR-0036 第8項、D03 §17.3。A1-2）。

置き場は `configs/datasources/oanda_mt5_m1_verified_raw_<取得日>.yaml`（取得日ごとに 1 ファイル）。
人間が PR の merge で足す（D17 の A3a）。形:

```yaml
schema_version: 1
id: oanda_mt5_m1_verified_raw
version: 1
datasource: oanda_mt5_m1
fetch_date: "2026-10-16"
fetch_manifest_sha256: <取得記録の SHA-256>
verified_by: {name: oanda_mt5_research_raw_verify, version: "1"}
permit_definition_sha256: <許可の定義のファイルの SHA-256>
declared_period_end: "2024-01-01T00:00:00Z"
files:
  - {location: data/raw/oanda_mt5/2026-10-16/EURJPY/EURJPY_2017-01_m1.csv.gz, symbol: EURJPY,
     sha256: <gzip のままの SHA-256>, access_id: <確認した閲覧の識別子>, result: PASS}
```

読込の検査（D03 §17.3 の 4）:

- 項目の欠け・型の誤り（日付と時刻は文字列で書く）
- `declared_period_end` が 2024-01-01T00:00:00Z より後
- `location` の重複、`location` が `data/raw/oanda_mt5/<fetch_date>/<symbol>/` の下でない・
  `..` を含む・絶対の置き場
- `sha256` が 64 桁の小文字の 16 進でない、`result` が `PASS` でない行、`symbol` が 8 通貨の外

どれかに反すれば一覧全体を読み込まない
（`ConfigError`。許可なしの読み取りは 1 つもできない）。

受入れの時の取得記録との照合（D03 §17.3 の 5）は、取得記録を読む `app.mt5` が行う。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.quality_permit import (
    SEALED_INTAKE_DATASOURCE,
    SEALED_INTAKE_SYMBOLS,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.verified_raw import VerifiedRawFile, VerifiedRawList

__all__ = [
    "VERIFIED_RAW_ID",
    "load_verified_raw_list",
    "verified_raw_path",
]

#: 一覧の識別（D03 §17.3 の 2）。
VERIFIED_RAW_ID: Final = "oanda_mt5_m1_verified_raw"

_SCHEMA_VERSION: Final = 1
_DIRECTORY: Final = Path("configs") / "datasources"
_PASS: Final = "PASS"


class _ToolModel(StrictModel):
    name: str
    version: str


class _FileModel(StrictModel):
    location: str
    symbol: str
    sha256: str
    access_id: str
    result: str


class _ListModel(StrictModel):
    schema_version: int
    id: str
    version: int
    datasource: str
    fetch_date: str
    fetch_manifest_sha256: str
    verified_by: _ToolModel
    permit_definition_sha256: str
    declared_period_end: str
    files: list[_FileModel]


def verified_raw_path(fetch_date: str) -> Path:
    """取得日の一覧の置き場（リポジトリの根からの相対パス。D03 §17.3 の 1）。"""
    if not fetch_date or "/" in fetch_date or "\\" in fetch_date or fetch_date in (".", ".."):
        raise ConfigError(f"取得日 {fetch_date!r} は一覧のファイルの名前に使えない")
    return _DIRECTORY / f"{VERIFIED_RAW_ID}_{fetch_date}.yaml"


def _require_hex64(value: str, label: str, path: Path) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ConfigError(f"{path}: `{label}` は 64 桁の小文字の 16 進")


def load_verified_raw_list(path: Path, *, fetch_date: str) -> VerifiedRawList:
    """一覧を読み、読込の検査（モジュールの説明文）を当てる。`fetch_date` は受入れの取得日。"""
    payload = load_yaml_mapping(path)
    model = validate(_ListModel, payload, path)
    require_schema_version(model.schema_version, _SCHEMA_VERSION, path)
    if model.id != VERIFIED_RAW_ID:
        raise ConfigError(f"{path}: `id` は {VERIFIED_RAW_ID}")
    if model.version < 1:
        raise ConfigError(f"{path}: `version` は 1 以上")
    if model.datasource != SEALED_INTAKE_DATASOURCE:
        raise ConfigError(f"{path}: `datasource` は {SEALED_INTAKE_DATASOURCE}")
    if model.fetch_date != fetch_date:
        raise ConfigError(
            f"{path}: `fetch_date` {model.fetch_date!r} が受入れの取得日 {fetch_date!r} と違う"
        )
    if not model.verified_by.name or not model.verified_by.version:
        raise ConfigError(f"{path}: `verified_by` は道具の名前と版")
    _require_hex64(model.fetch_manifest_sha256, "fetch_manifest_sha256", path)
    _require_hex64(model.permit_definition_sha256, "permit_definition_sha256", path)
    if not model.files:
        raise ConfigError(f"{path}: `files` が空")
    files: list[VerifiedRawFile] = []
    for number, item in enumerate(model.files, start=1):
        if item.result != _PASS:
            raise ConfigError(
                f"{path}: files の {number} 行目の `result` が {item.result!r}。一覧には PASS の"
                "行だけを載せる（D03 §17.3 の 3）"
            )
        try:
            symbol = Symbol(item.symbol)
        except (KernelValueError, ValueError) as exc:
            raise ConfigError(f"{path}: files の {number} 行目の `symbol` が読めない") from exc
        if symbol not in SEALED_INTAKE_SYMBOLS:
            raise ConfigError(f"{path}: files の {number} 行目の `symbol` {symbol} は 8 通貨の外")
        try:
            files.append(
                VerifiedRawFile(
                    location=item.location,
                    symbol=symbol,
                    sha256=item.sha256,
                    access_id=item.access_id,
                )
            )
        except MarketDataValueError as exc:
            raise ConfigError(f"{path}: files の {number} 行目: {exc}") from exc
    try:
        return VerifiedRawList(
            fetch_date=model.fetch_date,
            fetch_manifest_sha256=model.fetch_manifest_sha256,
            declared_period_end=UtcTime.parse(model.declared_period_end),
            files=tuple(files),
            source=path.as_posix(),
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    except (KernelValueError, ValueError) as exc:
        if isinstance(exc, ConfigError):  # pragma: no cover - 上で送出済み
            raise
        raise ConfigError(f"{path}: 確認済みハッシュの一覧として成立しない: {exc}") from exc
