"""原データの列対応の読込（D03 §4 の 2・§9、D01 §10.1）。

`configs/datasources/legacy_merged_csv_v1.yaml` を読み、受入れが使う
`marketdata.application.acceptance.ColumnMapping` と、その周辺の宣言（原データの基点、
ファイル名の規則、宣言した価格基準、受け入れる出所の値）へ変換する。

**時刻の規約は宣言であり推測ではない**（上位設計書 §3.2）。設定ファイルの
`time_convention` をそのまま `ColumnMapping` へ渡し、初版が受ける
`explicit_offset_utc`（オフセットが明示された UTC）以外は `ColumnMapping` が拒否する。

**価格基準は検証済みの事実ではなく人間の宣言**（D03 §2）。`verified` は常に偽として
`BasisDeclaration` に載せる。宣言者・宣言日時は識別子の計算対象外の別記録
（`DeclarationRecord`）に分ける（D03 §3.7.1）ので、この設定ファイルには書かない。

ファイル名の規則（`{symbol}_{timeframe}_merged.csv`）から銘柄と時間足を取り出すのは、
受入れの前段として `app` が行う仕事である（D03 §4 の 1）。規則の解釈をこの層に置くのは、
規則が設定ファイルの宣言だからである。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.marketdata.application.acceptance import ColumnMapping
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import PriceBasis
from odyssey_fx.marketdata.domain.snapshot import BasisDeclaration

__all__ = ["DataSourceConfig", "load_datasource", "parse_file_name"]

#: この実装が読む設定ファイルの形式版。未知の版は拒否する（D01 §10.1）。
DATASOURCE_SCHEMA_VERSION = 1

#: ファイル名の規則に置ける差し込み（`{symbol}` と `{timeframe}` のみ）。
_PLACEHOLDERS: Final = ("{symbol}", "{timeframe}")

#: 足の時刻ラベルの解釈。初版は足の**開始**ラベルだけを受ける（D03 §2）。
_SUPPORTED_TIME_LABEL: Final = "bar_start"


class _ColumnsModel(StrictModel):
    """原 CSV の列名（D03 §4 の 2）。先頭の無名列に与える名前を `time` に書く。"""

    time: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    source: str


class _BasisDeclarationModel(StrictModel):
    """宣言する価格基準（D03 §2・§3.7）。

    `verified` は「ファイルから検証できた事実か」を表す。初版は検証手段がないので偽しか
    書けない。真を書いた設定は、検証していない宣言を検証済みとして記録することになるので
    拒否する。
    """

    value: str
    verified: bool = False


class _DataSourceModel(StrictModel):
    """`configs/datasources/*.yaml` の形（D03 §9）。"""

    schema_version: int
    id: str
    version: Annotated[int, Field(ge=1)]
    root: str
    filename_pattern: str
    timeframes: Annotated[list[str], Field(min_length=1)]
    time_convention: str
    time_label: str
    columns: _ColumnsModel
    basis_declaration: _BasisDeclarationModel
    allowed_sources: Annotated[list[str], Field(min_length=1)]
    volume_note: str = ""


@dataclass(frozen=True, slots=True)
class DataSourceConfig:
    """原データの列対応とその周辺の宣言（D03 §4 の 2・§9）。

    `app` が受入れを組み立てるために必要な宣言をまとめて持つ。Pydantic モデルはこの型へ
    変換した時点で捨てられ、`app.config` の外へは出ない（D01 §10.1）。

    - `mapping`: 受入れに渡す列対応（`ColumnMapping`）。
    - `root`: 原データの基点（`data/raw/market`）。リポジトリからの相対パス。
    - `timeframes`: 受け入れる時間足の `id`（`15m`、`1h`）。
    - `basis_declaration`: 宣言した価格基準（識別に関わる部分、D03 §3.7）。
    - `allowed_sources`: `source` 列に現れてよい値。これ以外は受入れで拒否する。
    """

    id: str
    version: int
    root: str
    filename_pattern: str
    timeframes: tuple[str, ...]
    mapping: ColumnMapping
    basis_declaration: BasisDeclaration
    allowed_sources: frozenset[str]
    volume_note: str = ""

    def file_name(self, symbol: Symbol, timeframe_id: str) -> str:
        """銘柄と時間足から原ファイル名を作る（D03 §4 の 1）。"""
        return self.filename_pattern.format(symbol=str(symbol), timeframe=timeframe_id)


def _require_filename_pattern(pattern: str, path: Path) -> str:
    """ファイル名の規則が両方の差し込みをちょうど1度ずつ持つことを確かめる。

    片方しか無い規則では、同じ名前が複数の銘柄・時間足に対応してしまい、原ファイルと
    系列の対応が一意に決まらない。差し込み以外の `{` `}` も拒否する（`str.format` が
    予期しない展開をしないようにするため）。
    """
    for placeholder in _PLACEHOLDERS:
        if pattern.count(placeholder) != 1:
            raise ConfigError(
                f"{path}: `filename_pattern` は {placeholder} をちょうど1度含む必要がある"
                f"（{pattern!r} が与えられた）"
            )
    stripped = pattern
    for placeholder in _PLACEHOLDERS:
        stripped = stripped.replace(placeholder, "")
    if "{" in stripped or "}" in stripped:
        raise ConfigError(
            f"{path}: `filename_pattern` に使える差し込みは {list(_PLACEHOLDERS)} だけである"
            f"（{pattern!r} が与えられた）"
        )
    return pattern


def load_datasource(path: Path) -> DataSourceConfig:
    """原データの列対応を読む（D03 §4 の 2・§9）。"""
    payload = load_yaml_mapping(path)
    model = validate(_DataSourceModel, payload, path)
    require_schema_version(model.schema_version, DATASOURCE_SCHEMA_VERSION, path)

    if model.time_label != _SUPPORTED_TIME_LABEL:
        raise ConfigError(
            f"{path}: `time_label` が {model.time_label!r} だが、初版が受けるのは"
            f" {_SUPPORTED_TIME_LABEL!r}（足の開始ラベル）だけである（D03 §2）"
        )
    if model.basis_declaration.verified:
        raise ConfigError(
            f"{path}: `basis_declaration.verified` は真にできない。価格基準はファイルから"
            " 検証できる事実ではなく人間の宣言であり、検証済みとして記録してはならない"
            "（D03 §2・§3.7）"
        )

    try:
        basis = PriceBasis(model.basis_declaration.value)
    except ValueError as exc:
        raise ConfigError(
            f"{path}: `basis_declaration.value` が {model.basis_declaration.value!r} だが、"
            f" 受けるのは {[member.value for member in PriceBasis]} のいずれかである"
        ) from exc

    duplicate_timeframes = sorted(
        {entry for entry in model.timeframes if model.timeframes.count(entry) > 1}
    )
    if duplicate_timeframes:
        raise ConfigError(f"{path}: `timeframes` に重複がある: {duplicate_timeframes}")
    duplicate_sources = sorted(
        {entry for entry in model.allowed_sources if model.allowed_sources.count(entry) > 1}
    )
    if duplicate_sources:
        raise ConfigError(f"{path}: `allowed_sources` に重複がある: {duplicate_sources}")

    try:
        mapping = ColumnMapping(
            time_column=model.columns.time,
            open_column=model.columns.open,
            high_column=model.columns.high,
            low_column=model.columns.low,
            close_column=model.columns.close,
            volume_column=model.columns.volume,
            source_column=model.columns.source,
            time_convention=model.time_convention,
        )
    except MarketDataValueError as exc:
        raise ConfigError(f"{path}: 列対応として成立しない: {exc}") from exc

    try:
        declaration = BasisDeclaration(value=basis, verified=False)
    except MarketDataValueError as exc:  # pragma: no cover - 値はここまでで検査済み
        raise ConfigError(f"{path}: 価格基準の宣言として成立しない: {exc}") from exc

    return DataSourceConfig(
        id=model.id,
        version=model.version,
        root=model.root,
        filename_pattern=_require_filename_pattern(model.filename_pattern, path),
        timeframes=tuple(model.timeframes),
        mapping=mapping,
        basis_declaration=declaration,
        allowed_sources=frozenset(model.allowed_sources),
        volume_note=model.volume_note,
    )


def parse_file_name(pattern: str, file_name: str) -> tuple[Symbol, str] | None:
    """ファイル名から銘柄と時間足を取り出す（D03 §4 の 1）。

    規則に合わない名前には `None` を返す。銘柄は6文字の大文字、時間足は `TimeframeRef`
    が許す字種（小文字・数字・下線）に限る。合致しない名前を受入れの対象に含めると、
    どの系列の足なのかが宣言から決まらなくなる。
    """
    escaped = re.escape(pattern)
    regex = escaped.replace(re.escape("{symbol}"), "(?P<symbol>[A-Z]{6})").replace(
        re.escape("{timeframe}"), "(?P<timeframe>[a-z0-9_]+)"
    )
    match = re.fullmatch(regex, file_name)
    if match is None:
        return None
    try:
        symbol = Symbol(match.group("symbol"))
    except KernelValueError:  # pragma: no cover - 正規表現が字種を保証する
        return None
    return symbol, match.group("timeframe")
