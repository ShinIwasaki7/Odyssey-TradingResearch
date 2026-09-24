"""能力検査と、段階3 の組合せ検査（D04 §12 #7・#10・#13、D05 §5.1 の段9・§5.6）。

「宣言としては書けるが、この段階の実装では動かせない構成」をコンパイル時に拒否する。黙って
無視したり、実行時に別の意味で動かしたりしない（D04 §12）。

**段階3 で解除したもの**（D04 §12・D05 §5.6。能力検査から外しただけで、宣言型は変えない）

- 後続確認（`AwaitConfirmation`）と `execution_filter` のある戦略: 確認の計画
  （`ConfirmationPlan`）に載せる（D05 §7.7）。
- 欠損方針の待機（`WaitForInput`）・遡り（`UsePrevious`）: 読み方と接続元の組合せだけを
  検査 c で見る（D04 §12 #10）。
- 発注要求まで有効であり続けることを求める束縛に `Error` を書いた宣言: 再検査の記録
  （`ValidityRecheck`）が失敗の書き先になる（D05 §7.3）。
- 空でない時刻制約のうち観測区間の一致（`AlignmentRequirement`）: 守らせる仕組みは D05 §6.7。
- 出力参照を本数で数える履歴窓（`BarsWindow`）で読む接続: 保持本数と主キーが定まる条件を
  検査 f で見る（D04 §12 #13）。
- 損切り水準の更新（`UPDATE_STOP`）: コンパイラに拒否の経路は無かった（実行時の内容型への
  追加は段階3 の後続の変更）。

**段階3 でも拒否を続けるもの**

- 15m より細かい足: 本モジュールが拒否する（時間足定義の名目の長さで判定）。
- 空でない時刻制約のうちウォームアップ本数（`WarmupSpec`）: 本モジュールが拒否する（下記）。
- 出力参照を経過時間の履歴窓（`DurationWindow`）で読む接続: 本モジュールが拒否する
  （検査 f の (1)）。
- 複数銘柄に跨る使用箇所: 銘柄の伝播（段4）が拒否する。
- 未約定注文の参照（`PENDING_ORDER`）: 列挙に無いので宣言できない（D04 §4.3）。
- `POSITION_OPENED` 以外の実行時イベント: 列挙に無いので宣言できない（D04 §8）。
- 指値・距離型の損切り: 実行時の内容型に無いので部品が返せない（D05 §3）。

内部状態を持つこと自体は拒否の理由にしない（D04 §9.1、Q3 決定）。

**ウォームアップ本数を拒否し続ける理由**: `WarmupSpec.series` は具体的な系列を持つため、系列を
使用箇所が選ぶ再利用可能な契約には書けない。ウォームアップ不足は履歴窓が
`WARMUP_INSUFFICIENT` として返す経路で成立している（D05 §5.6・§11 の2）。宣言を通して黙って
無視すると、ウォームアップ中に出力を出す部品が宣言どおりに動いていないまま結果を変える。

**検査 c・f の拒否区分**はどちらも `UNSUPPORTED_CONFIGURATION` である（D05 §5.6 の表）。
検査の番号（`check_id`）は正本である D04 §12 の番号（#10・#13）を使う。
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
    InputPlan,
)
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.evaluation import OnBarClose, OnRuntimeEvent
from odyssey_fx.strategy.declarations.instance import ComponentInstance
from odyssey_fx.strategy.declarations.missing import UsePrevious
from odyssey_fx.strategy.declarations.read_spec import (
    BarsWindow,
    HistoryWindow,
    LatestAvailable,
)
from odyssey_fx.strategy.declarations.refs import MarketDataRef, OutputRef

__all__ = [
    "MINIMUM_TIMEFRAME_LENGTH",
    "check_capabilities",
    "check_missing_policies",
    "check_output_history_windows",
    "check_temporal_constraints",
]

#: 扱う最小の足の長さ（D04 §12）。これより細かい足は拒否する（段階6 まで拒否を続ける）。
MINIMUM_TIMEFRAME_LENGTH: Final = timedelta(minutes=15)

_CHECK_ID = "#7"


def _error(
    instance_id: str | None, field_path: str, message: str, *, check_id: str = _CHECK_ID
) -> CompileError:
    return CompileError(
        check_id=check_id,
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
    """対応しない足の細かさを拒否する（D04 §12 #7）。

    段階2 はここで後続確認（`AwaitConfirmation`・`execution_filter`）も拒否していたが、段階3
    で解除した（D05 §5.6）。確認に要る値は検査 a・b が確かめ、確認の計画に載せる。
    """
    errors: list[CompileError] = []
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
                    f" ({definition_for_series.nominal_length}), which is not supported"
                    " before stage 6",
                )
            )
    return tuple(errors)


def check_temporal_constraints(
    contracts: Mapping[str, ComponentContract],
) -> tuple[CompileError, ...]:
    """宣言されたウォームアップ本数を拒否する（D04 §9.2・§12 #7、D05 §5.6）。

    観測区間の一致（`alignment`）は段階3 で解除した。守らせる仕組みはランタイムにある
    （D05 §6.7）。
    """
    errors: list[CompileError] = []
    for instance_id in sorted(contracts):
        constraints = contracts[instance_id].temporal_constraints
        if constraints.warmup is not None:
            errors.append(
                _error(
                    instance_id,
                    "contract.temporal_constraints.warmup",
                    "a warmup requirement is declared but cannot be enforced: its series is"
                    " fixed in a reusable contract; warmup is covered by history windows"
                    " instead (D05 §5.6・§11)",
                )
            )
    return tuple(errors)


# --- 検査 c（D04 §12 #10）: 待機・遡りと読み方・接続元の組合せ ----------------


def check_missing_policies(
    definition: StrategyDefinition, contracts: Mapping[str, ComponentContract]
) -> tuple[CompileError, ...]:
    """遡りを書ける読み方と接続元を確かめる（D04 §12 #10、D05 §5.6 の検査 c）。

    待機（`WaitForInput`）は最新1件（`LatestAvailable`）と履歴窓（`HistoryWindow`）に書ける。
    欠損方針を持つ読み方はこの2つだけなので、待機の側で拒否する組合せは型の上で生じない。
    遡り（`UsePrevious`）は**最新1件の読み方で、かつ接続元がすべて市場データ参照のとき**に
    だけ書ける。最新1件の読み方が保持するのは最新1件だけであり、出力参照は遡る先の履歴を
    持たないからである（D04 §12 #10、D05 §6.9）。
    """
    errors: list[CompileError] = []
    for instance in definition.components:
        contract = contracts[instance.instance_id]
        for input_name, spec in contract.inputs.items():
            on_missing = getattr(spec.read_spec, "on_missing", None)
            if not isinstance(on_missing, UsePrevious):
                continue
            field_path = f"inputs.{input_name}.read_spec.on_missing"
            if not isinstance(spec.read_spec, LatestAvailable):
                errors.append(
                    _error(
                        instance.instance_id,
                        field_path,
                        "going back to a previous value (UsePrevious) may only be declared on a"
                        " latest-value read (LatestAvailable), not on"
                        f" {type(spec.read_spec).__name__} (D05 §6.9)",
                        check_id="#10",
                    )
                )
                continue
            binding = instance.inputs.get(input_name)
            if binding is None:  # pragma: no cover - 段5 が先に拒否する
                continue
            if not all(isinstance(source, MarketDataRef) for source in binding.sources):
                errors.append(
                    _error(
                        instance.instance_id,
                        field_path,
                        "going back to a previous value (UsePrevious) may only be declared when"
                        " every source is market data; a latest-value read keeps only the latest"
                        " output of another component, so there is nothing to go back to"
                        " (D05 §6.9)",
                        check_id="#10",
                    )
                )
    return tuple(errors)


# --- 検査 f（D04 §12 #13）: 出力参照を履歴窓で読む接続 ------------------------


def _upstream_trigger_problem(upstream: ComponentInstance) -> str | None:
    """上流の起動条件が保持の主キー（観測した足）を定められない理由。定まるなら `None`。"""
    triggers = upstream.evaluation.triggers
    if any(isinstance(trigger, OnRuntimeEvent) for trigger in triggers):
        return (
            f"the upstream {upstream.instance_id!r} starts from a runtime event, whose output"
            " has no observed bar to key the history by (condition 3)"
        )
    if not all(isinstance(trigger, OnBarClose) for trigger in triggers):
        return (
            f"the upstream {upstream.instance_id!r} does not start only from bar closes, so its"
            " outputs have no single observed bar to key the history by (condition 2)"
        )
    series = {trigger.series for trigger in triggers if isinstance(trigger, OnBarClose)}
    if len(series) != 1:
        names = ", ".join(sorted(str(item) for item in series))
        return (
            f"the upstream {upstream.instance_id!r} starts from bar closes of {len(series)}"
            f" series ({names}); 'the last N bars' is only defined for a single series"
            " (condition 2)"
        )
    return None


def check_output_history_windows(
    definition: StrategyDefinition,
    input_plans: Mapping[str, Mapping[str, InputPlan]],
) -> tuple[CompileError, ...]:
    """出力参照を履歴窓で読む接続で、保持本数と主キーが定まることを確かめる（D04 §12 #13）。

    D05 §5.6 の検査 f の4条件を見る。

    1. 窓が本数で数える窓（`BarsWindow`）で、解決済みの本数が 1 以上であること。経過時間の
       窓（`DurationWindow`）は上流の出力が出る間隔が宣言から分からず、保持本数を導けない。
    2. 上流の使用箇所が足の確定（`OnBarClose`）だけで起動し、その系列がただ1つであること。
    3. 上流が実行時イベント（`OnRuntimeEvent`）で起動しないこと。
    4. 読む側の使用箇所も足の確定だけで起動すること（窓の末尾を決める対象区間が要る）。

    解決済みの窓は入力の計画（段5）から読む。パラメータ参照の解決は段3 が済ませている。
    """
    instances = {instance.instance_id: instance for instance in definition.components}
    errors: list[CompileError] = []
    for instance in definition.components:
        plans = input_plans.get(instance.instance_id, {})
        for input_name in sorted(plans):
            plan = plans[input_name]
            if not isinstance(plan.read_spec, HistoryWindow):
                continue
            binding = instance.inputs.get(input_name)
            if binding is None:  # pragma: no cover - 段5 が先に拒否する
                continue
            upstream_ids = [
                source.instance_id for source in binding.sources if isinstance(source, OutputRef)
            ]
            if not upstream_ids:
                continue
            field_path = f"inputs.{input_name}"
            window = plan.resolved_window
            if not isinstance(window, BarsWindow):
                errors.append(
                    _error(
                        instance.instance_id,
                        field_path,
                        "reading another component's output through a duration window is not"
                        " supported; how many outputs to keep cannot be derived at compile time"
                        " (D05 §6.12, condition 1)",
                        check_id="#13",
                    )
                )
                continue
            if not all(isinstance(trigger, OnBarClose) for trigger in instance.evaluation.triggers):
                errors.append(
                    _error(
                        instance.instance_id,
                        field_path,
                        "a component that reads another component's output through a history"
                        " window must start only from bar closes; otherwise the window has no"
                        " target interval to end at (D05 §6.12, condition 4)",
                        check_id="#13",
                    )
                )
            for upstream_id in dict.fromkeys(upstream_ids):
                upstream = instances.get(upstream_id)
                if upstream is None:  # pragma: no cover - 段2 が先に拒否する
                    continue
                problem = _upstream_trigger_problem(upstream)
                if problem is not None:
                    errors.append(
                        _error(
                            instance.instance_id,
                            field_path,
                            "reading another component's output through a history window:"
                            f" {problem} (D05 §6.12)",
                            check_id="#13",
                        )
                    )
    return tuple(errors)
