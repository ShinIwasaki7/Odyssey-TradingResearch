"""能力検査（D04 §12 #7、D05 §5.1 の段9）。

「宣言としては書けるが、段階2の実装では動かせない構成」をコンパイル時に拒否する。黙って
無視したり、実行時に別の意味で動かしたりしない（D04 §12）。

段階2で拒否する構成は D04 §12 が列挙している。そのうち**型の側で表現できないもの**は、
この検査の前に構築できない。

| 拒否する構成 | 段階2での扱い |
|---|---|
| 後続確認（`AwaitConfirmation`）と `execution_filter` のある戦略 | 本モジュールが拒否する |
| 15m より細かい足 | 本モジュールが拒否する（時間足定義の名目の長さで判定） |
| 出力参照を履歴窓で読む接続 | 本モジュールが拒否する（D05 §6.3） |
| 複数銘柄に跨る使用箇所 | 銘柄の伝播（段4）が拒否する |
| 未約定注文の参照（`PENDING_ORDER`） | 列挙に無いので宣言できない（D04 §4.3） |
| 待機・遡りの欠損方針 | 型に無いので宣言できない（D04 §6.3） |
| `POSITION_OPENED` 以外の実行時イベント | 列挙に無いので宣言できない（D04 §8） |
| 指値・距離型の損切り・トレーリング | 実行時の内容型に無いので部品が返せない（D05 §3） |

内部状態を持つこと自体は拒否の理由にしない（D04 §9.1、Q3 決定）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Final

from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.compiler.compiled import (
    CompileError,
    CompileRejection,
    DeclarationLocation,
)
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.entry_policy import AwaitConfirmation
from odyssey_fx.strategy.declarations.evaluation import OnBarClose
from odyssey_fx.strategy.declarations.read_spec import HistoryWindow
from odyssey_fx.strategy.declarations.refs import MarketDataRef

__all__ = ["MINIMUM_TIMEFRAME_LENGTH", "check_capabilities", "check_output_history_window"]

#: 段階2が扱う最小の足の長さ（D04 §12）。これより細かい足は拒否する。
MINIMUM_TIMEFRAME_LENGTH: Final = timedelta(minutes=15)

_CHECK_ID = "#7"


def _error(instance_id: str | None, field_path: str, message: str) -> CompileError:
    return CompileError(
        check_id=_CHECK_ID,
        rejection=CompileRejection.UNSUPPORTED_CONFIGURATION,
        location=DeclarationLocation(instance_id=instance_id, field_path=field_path),
        message=message,
    )


def _series_in(definition: StrategyDefinition) -> list[tuple[str, str, SeriesId]]:
    """宣言に現れる系列を「どこで使われているか」とともに集める。"""
    found: list[tuple[str, str, SeriesId]] = []
    for instance in definition.components:
        for input_name, binding in instance.inputs.items():
            for index, source in enumerate(binding.sources):
                if isinstance(source, MarketDataRef):
                    found.append(
                        (
                            instance.instance_id,
                            f"inputs.{input_name}.sources[{index}]",
                            source.series,
                        )
                    )
        for trigger in instance.evaluation.triggers:
            if isinstance(trigger, OnBarClose):
                found.append(
                    (instance.instance_id, f"evaluation.{trigger.name}.series", trigger.series)
                )
    return found


def check_capabilities(
    definition: StrategyDefinition,
    timeframes: Mapping[TimeframeRef, TimeframeDefinition],
) -> tuple[CompileError, ...]:
    """段階2で対応しない構成を拒否する（D04 §12 #7）。"""
    errors: list[CompileError] = []

    if isinstance(definition.entry_policy, AwaitConfirmation):
        errors.append(
            _error(
                None,
                "entry_policy",
                "waiting for a follow-up confirmation (AwaitConfirmation) is declared but not"
                " executable in stage 2; it is enabled in stage 3 (D05 v0.2)",
            )
        )
    if definition.execution_filter is not None:
        errors.append(
            _error(
                None,
                "execution_filter",
                "a follow-up confirmation output is declared but not executable in stage 2;"
                " it is enabled in stage 3 (D05 v0.2)",
            )
        )

    for instance_id, field_path, series in _series_in(definition):
        definition_for_series = timeframes.get(series.timeframe)
        if definition_for_series is None:
            errors.append(
                _error(
                    instance_id,
                    field_path,
                    f"timeframe {series.timeframe} has no definition, so its granularity cannot"
                    " be checked",
                )
            )
            continue
        if definition_for_series.nominal_length < MINIMUM_TIMEFRAME_LENGTH:
            errors.append(
                _error(
                    instance_id,
                    field_path,
                    f"timeframe {series.timeframe} is finer than 15 minutes"
                    f" ({definition_for_series.nominal_length}), which stage 2 does not support",
                )
            )

    return tuple(errors)


def check_output_history_window(
    instance_id: str, input_name: str, read_spec: object, has_output_source: bool
) -> CompileError | None:
    """出力参照を履歴窓で読んでいれば拒否の理由を返す（D05 §6.3）。"""
    if has_output_source and isinstance(read_spec, HistoryWindow):
        return _error(
            instance_id,
            f"inputs.{input_name}",
            "reading another component's output through a history window is not supported in"
            " stage 2; the runtime keeps only the latest output per reference (D05 §6.3・§6.5)",
        )
    return None
