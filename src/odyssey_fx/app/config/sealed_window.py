"""封印の取得の許可の窓の設定（ADR-0037 第4項）。

置き場は `configs/acquisition/oanda_mt5_m1_sealed_window.yaml`。

窓の設定は版管理の設定ファイル 1 つに書く（ADR-0037 第4項の 1。PR #108 の仮置き S2a-6）。本基盤は
この設定から、取得を許す期間の始まり（`allowed_start`）と取得の開始時点（`acquisition_start`）を
読み、OANDA MT5 の経路の区分の境界（`SealedAccessBoundaries`。ADR-0038 第1項）を作る。区分の
境界はこの設定からだけ読み、コードに取得の開始時点の日付を書かない（ADR-0038 第1項）。

取得の開始時点の値は S4a で書く（ADR-0037 第2項の 4）。値が空（`null`）の間は境界が決まらない
ので、2024-01-01 以降の足を分類しない（`SealedAccessBoundaries` が `AcquisitionStartUnset` で
止める）。`acquisition_start` が `merge_commit_time` から ADR-0037 第2項の 2 の規則で計算した
値と一致することの単体テストは S4a（ADR-0037 第4項の 4）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.quality_permit import SEALED_INTAKE_SYMBOLS
from odyssey_fx.marketdata.domain.access import SEALED_RESEARCH_UNTIL, SealedAccessBoundaries

__all__ = [
    "SEALED_WINDOW_PATH",
    "SEALED_WINDOW_SYMBOLS",
    "SealedWindowConfig",
    "load_sealed_window",
]

#: 窓の設定の置き場（リポジトリの根からの相対パス。ADR-0037 第4項の 1）。
SEALED_WINDOW_PATH: Final = Path("configs") / "acquisition" / "oanda_mt5_m1_sealed_window.yaml"

#: 改定文が取得を許す 8 通貨（ADR-0037 第1項の 2 の 1）。許可の定義と同じ 1 か所の値を使う。
SEALED_WINDOW_SYMBOLS: Final = SEALED_INTAKE_SYMBOLS

#: この実装が読む設定ファイルの形式版。
SEALED_WINDOW_SCHEMA_VERSION: Final = 1


class _BasisModel(StrictModel):
    merge_commit: str | None
    merge_commit_time: str | None


class _SealedWindowModel(StrictModel):
    schema_version: int
    decision: str
    datasource: str
    price_basis: str
    timeframe: str
    symbols: list[str]
    allowed_start: str
    acquisition_start: str | None
    acquisition_start_basis: _BasisModel


@dataclass(frozen=True, slots=True)
class SealedWindowConfig:
    """窓の設定（ADR-0037 第4項の 2）。`boundaries` が区分の境界（ADR-0038 第1項）。"""

    symbols: frozenset[Symbol]
    allowed_start: UtcTime
    acquisition_start: UtcTime | None
    merge_commit: str | None
    merge_commit_time: UtcTime | None
    boundaries: SealedAccessBoundaries


def _time(text: str | None, label: str, path: Path) -> UtcTime | None:
    if text is None:
        return None
    try:
        return UtcTime.parse(text)
    except KernelValueError as exc:
        raise ConfigError(f"{path}: `{label}` が UTC の時刻として読めない: {exc}") from exc


def load_sealed_window(path: Path) -> SealedWindowConfig:
    """窓の設定を読む（ファイルが無ければ `ConfigError`。境界が決まらないので止める）。"""
    payload = load_yaml_mapping(path)
    model = validate(_SealedWindowModel, payload, path)
    require_schema_version(model.schema_version, SEALED_WINDOW_SCHEMA_VERSION, path)
    if model.decision != "ADR-0037":
        raise ConfigError(
            f"{path}: `decision` は ADR-0037 でなければならない（ADR-0037 第4項の 2）"
        )
    if model.datasource != "oanda_mt5_m1" or model.price_basis != "bid" or model.timeframe != "M1":
        raise ConfigError(
            f"{path}: 窓が覆うのは OANDA MT5 の 1 分足（bid）だけ（datasource: oanda_mt5_m1、"
            "price_basis: bid、timeframe: M1。ADR-0037 第1項の 2 の 1）"
        )
    try:
        symbols = frozenset(Symbol(code) for code in model.symbols)
    except KernelValueError as exc:
        raise ConfigError(f"{path}: `symbols` に銘柄として成立しない値がある: {exc}") from exc
    if not symbols or len(symbols) != len(model.symbols) or not symbols <= SEALED_WINDOW_SYMBOLS:
        raise ConfigError(
            f"{path}: `symbols` は改定文の 8 通貨の中から重複なく 1 つ以上"
            "（ADR-0037 第1項の 2 の 1）"
        )
    allowed_start = _time(model.allowed_start, "allowed_start", path)
    if allowed_start != SEALED_RESEARCH_UNTIL:
        raise ConfigError(
            f"{path}: `allowed_start` は {SEALED_RESEARCH_UNTIL}（改定文の値）でなければならない"
            f"（ADR-0037 第2項の 1）。値: {allowed_start}"
        )
    acquisition_start = _time(model.acquisition_start, "acquisition_start", path)
    merge_commit_time = _time(
        model.acquisition_start_basis.merge_commit_time,
        "acquisition_start_basis.merge_commit_time",
        path,
    )
    try:
        boundaries = SealedAccessBoundaries(acquisition_start=acquisition_start)
    except KernelValueError as exc:
        raise ConfigError(
            f"{path}: `acquisition_start` が区分の境界として成立しない: {exc}"
        ) from exc
    return SealedWindowConfig(
        symbols=symbols,
        allowed_start=allowed_start,
        acquisition_start=acquisition_start,
        merge_commit=model.acquisition_start_basis.merge_commit,
        merge_commit_time=merge_commit_time,
        boundaries=boundaries,
    )
