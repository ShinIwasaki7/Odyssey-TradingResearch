"""品質確認の許可の定義（`configs/quality_checks/<道具の名前>.yaml`。ADR-0036 第4項）。

品質確認の道具ごとに、許可の定義ファイルを版管理に置き、人間が PR の merge で承認する
（PR #80 の仮置き 6 の決定）。定義には、道具の名前と版、設定のダイジェスト、対象の区分と
期間、対象（partition か原ファイルか、銘柄・時間足）、出力の種類を書く。関所は、実行しようと
する道具の名前と版・設定のダイジェスト・要求する区分と期間が承認済みの定義と一致することを
確かめてから許可を出す（`evaluation.application.holdout_gate`）。

ディレクトリが無い・定義が 1 つも無いときは、許可を 1 つも出さない（fail-closed）。定義が
覆ってよい範囲（OANDA MT5 の 1 分足とそれから作る partition、封印の区分、受入れの検査、件数・
時刻・区間・ハッシュ・合否の出力）は `QualityCheckDefinition` が構築時に確かめる。

形:

```yaml
schema_version: 1
tool: {name: <道具の名前>, version: <版>}
config_digest: <設定の SHA-256（小文字の 16 進 64 文字）>
datasource: oanda_mt5_m1
check_kind: ACCEPTANCE
classes: [HOLDOUT_SINCE_2024, FORWARD_HOLDOUT]
period: {start: "2024-01-01T00:00:00Z", end: null}
targets: {kind: RAW_FILE, symbols: [USDJPY], timeframes: [M1]}
output_kinds: [COUNTS, HASHES, INTERVALS, PASS_FAIL, TIMES]
```
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.quality_permit import QualityCheckDefinition, QualityTargetKind
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.quality_access import (
    AccessPeriod,
    QualityCheckKind,
    QualityOutputKind,
    ToolRef,
)

__all__ = [
    "QUALITY_CHECKS_DIRECTORY",
    "load_quality_check_definition",
    "load_quality_check_definitions",
]

#: 許可の定義の置き場（リポジトリの根からの相対パス。ADR-0036 第4項）。
QUALITY_CHECKS_DIRECTORY: Final = Path("configs") / "quality_checks"

QUALITY_CHECK_SCHEMA_VERSION: Final = 1


class _ToolModel(StrictModel):
    name: str
    version: str


class _PeriodModel(StrictModel):
    start: str
    end: str | None


class _TargetsModel(StrictModel):
    kind: str
    symbols: list[str]
    timeframes: list[str]


class _DefinitionModel(StrictModel):
    schema_version: int
    tool: _ToolModel
    config_digest: str
    datasource: str
    check_kind: str
    classes: list[str]
    period: _PeriodModel
    targets: _TargetsModel
    output_kinds: list[str]


def _distinct(values: list[str], label: str, path: Path) -> list[str]:
    if not values or len(set(values)) != len(values):
        raise ConfigError(f"{path}: `{label}` は重複の無い 1 つ以上の値の列")
    return values


def load_quality_check_definition(path: Path) -> QualityCheckDefinition:
    """許可の定義を 1 つ読む。ファイル名（拡張子を除く）は道具の名前と一致しなければならない。"""
    payload = load_yaml_mapping(path)
    model = validate(_DefinitionModel, payload, path)
    require_schema_version(model.schema_version, QUALITY_CHECK_SCHEMA_VERSION, path)
    if path.stem != model.tool.name:
        raise ConfigError(
            f"{path}: ファイル名 {path.stem!r} と道具の名前 {model.tool.name!r} が食い違う"
            "（`configs/quality_checks/<道具の名前>.yaml`。ADR-0036 第4項）"
        )
    try:
        return QualityCheckDefinition(
            tool=ToolRef(name=model.tool.name, version=model.tool.version),
            config_digest=model.config_digest,
            datasource=model.datasource,
            check_kind=QualityCheckKind(model.check_kind),
            classes=frozenset(
                AccessClass(value) for value in _distinct(model.classes, "classes", path)
            ),
            period=AccessPeriod(
                start=UtcTime.parse(model.period.start),
                end=None if model.period.end is None else UtcTime.parse(model.period.end),
            ),
            target_kind=QualityTargetKind(model.targets.kind),
            symbols=frozenset(
                Symbol(code) for code in _distinct(model.targets.symbols, "targets.symbols", path)
            ),
            timeframes=frozenset(_distinct(model.targets.timeframes, "targets.timeframes", path)),
            output_kinds=frozenset(
                QualityOutputKind(value)
                for value in _distinct(model.output_kinds, "output_kinds", path)
            ),
            source=str(path),
        )
    except (KernelValueError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"{path}: 品質確認の許可の定義として成立しない: {exc}") from exc


def load_quality_check_definitions(directory: Path) -> tuple[QualityCheckDefinition, ...]:
    """ディレクトリ配下の許可の定義をすべて読む（ファイル名の昇順。無ければ空）。

    同じ道具の名前と版の定義が 2 つあれば止める（どちらが承認済みか決められない）。
    """
    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise ConfigError(f"品質確認の許可の定義の置き場がディレクトリではない: {directory}")
    definitions = tuple(
        load_quality_check_definition(path) for path in sorted(directory.glob("*.yaml"))
    )
    seen: set[ToolRef] = set()
    for definition in definitions:
        if definition.tool in seen:  # pragma: no cover - ファイル名と道具の名前の一致で防がれる
            raise ConfigError(f"{directory}: 道具 {definition.tool.name} の定義が 2 度現れる")
        seen.add(definition.tool)
    return definitions
