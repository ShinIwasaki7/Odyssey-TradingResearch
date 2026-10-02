"""探索の評価基準の型（D09 §3・§7.1〜§7.3・§7.8・§7.11。v0.2）。

研究ポリシーの版 3 以上は、評価基準の群 `evaluation_standard`（用途・期間分割の標準規則・
選定規則・検証の条件・証拠の要件）を持つ（D09 §7.6・§10.9）。本モジュールはその**型と、
研究ポリシーを読むときの検査 E1〜E4** を持つ（E5 の「用途の省略は拒否」は読込が、語彙は
`StandardPurpose` が強制する）。検査は各型の構築時に当て、違反は構造エラー
（`KernelValueError`）として読込側（`app.config`）が設定の誤りに言い換える。

- E1（`MetricCondition`・`AggregateCondition`・`SelectionRule`）: 選定・足切り・最低条件・
  集約条件の指標は、種類が比率か金額の**採用指標**。参考値（#7・#8・#11・#13・#14）、
  取引件数（#3）、時間・価格差の指標は書けない。
- E2（`ValidationRule`）: 各 fold の最低条件（`fold_floors`）が空でない。
- E3（`SelectionRule`・`ValidationRule`）: 同じ一覧の中に `(metric, comparator)`（集約条件は
  `(metric, statistic, comparator)`）が同じ条件を2つ書かない。
- E4（`FrequencyClass`・`SufficiencyRule`）: 頻度区分は1つ以上、名前が一意で
  `[A-Z][A-Z0-9_]*`、下限が厳密に降順、最後の下限が 0、要件の整数が 0 以上。

閾値は `Decimal` で持つ（D09 §7.1。`float` を使わない。ADR-0012）。

**選定・判定・頻度区分の純粋関数**（D09 §7.2〜§7.5・§7.8・§10.4。段階5 実装 PR 3）:

- 試行の状態の導出（`derive_trial_status`・`count_trial_statuses`。§10.4）。
- 候補の区分と選定（`candidate_status`・`select_trial`。§7.2・§7.4）。入力は選定区間の単位の
  結果（`TrainUnitEvaluation`）だけで、検証区間の結果（`ValidationUnitEvaluation`）は型として
  渡せない（段階5 の完了条件1「選定が train 内で閉じる」）。
- 頻度区分（`assess_frequency`。§7.8。Q15 決定）。入力は選定記録と fold の区間だけ。
- fold の判定と実験の判定（`build_search_outcome`。§7.3 の手順。Q16・Q33 決定）。
- 表示用の導出値（`longest_idle_period`。§11.5 の最長の無取引期間。指標ではない）。

どれも実時計・乱数・ファイルを読まず、同じ入力から同じ出力を返す（D01 §2.2）。D07 の指標を
計算し直さない（D07 §14）。

**探索計画と試行の列挙**（D09 §5.1・§5.2・§6.2・§10.2。実装 PR 2）: 探索計画 `SearchPlan` は
格子（`GRID`）だけを持ち（Q6 決定）、軸 `ParameterAxis` を `(instance_id, parameter)` の昇順に
並べ、各軸の値は書いた順のまま、**後ろの軸ほど速く変わる**直積の順に試行を列挙する
（`enumerate_assignments`。先頭が `trial_index = 0`）。試行は割当 `ParameterAssignment` 1つで、
fold × 局面（選定区間 `TRAIN` / 検証区間 `VALIDATION`）ごとに実行単位 `TrialUnitKey` を持つ。
記録票には試行ごとの `TrialPlan`（割当、コンパイル結果の識別かコンパイル拒否、単位ごとの予測
`ConfigDigest`）を全試行ぶん入れる。
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from enum import Enum
from typing import Final

from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.canonical import encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int, kernel_context
from odyssey_fx.common.refs import CompiledStrategyRef, ConfigDigest, ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import (
    METRIC_KINDS,
    AmountValue,
    CountValue,
    MetricId,
    MetricKind,
    MetricRecord,
    MetricUnavailableReason,
    MetricValue,
    RatioValue,
    Unavailable,
)
from odyssey_fx.evaluation.domain.splits import Fold, SplitStandard
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.strategy.compiler.compiled import CompileError
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    FloatValue,
    IntValue,
    ParameterValue,
    StrValue,
)

__all__ = [
    "OBSERVATION_SHORTFALL_REASONS",
    "REFERENCE_METRICS",
    "SECONDS_PER_365_DAYS",
    "SELECTABLE_METRIC_KINDS",
    "AggregateCondition",
    "CandidateStatus",
    "Comparator",
    "ConditionOutcome",
    "ConditionResult",
    "ConditionScope",
    "EvaluationStandard",
    "FoldEvidence",
    "FoldSelection",
    "FoldStatistic",
    "FoldVerdict",
    "FrequencyAssessment",
    "FrequencyClass",
    "MetricCondition",
    "ParameterAssignment",
    "ParameterAxis",
    "SearchOutcome",
    "SearchPlan",
    "SearchPlanKind",
    "SearchVerdict",
    "SelectionDirection",
    "SelectionRule",
    "StandardPurpose",
    "SufficiencyRule",
    "SufficiencyShortfall",
    "SufficiencyShortfallKind",
    "TrainUnitEvaluation",
    "TrialPhase",
    "TrialPlan",
    "TrialStatus",
    "TrialUnitKey",
    "ValidationRule",
    "ValidationUnitEvaluation",
    "assess_frequency",
    "build_search_outcome",
    "candidate_status",
    "compile_rejections_of",
    "count_trial_statuses",
    "derive_trial_status",
    "enumerate_assignments",
    "frequency_class_of",
    "is_selectable_metric",
    "longest_idle_period",
    "select_trial",
    "trades_per_365d",
    "trial_units",
]

#: 参考値（D07 §5.3・§22.2 の #7・#8・#11・#13・#14）。採否の判断に使わない（D09 §7.1）。
REFERENCE_METRICS: Final[frozenset[MetricId]] = frozenset(
    {
        MetricId.MAX_DRAWDOWN_BALANCE,
        MetricId.MAX_DRAWDOWN_BALANCE_RATE,
        MetricId.COST_PRICE_EMBEDDED_TOTAL,
        MetricId.END_EQUITY_MTM,
        MetricId.HYPOTHETICAL_CLOSED_PROFIT,
    }
)

#: 条件と選定に使える指標の種類（D09 §7.1。比率と金額だけ）。
SELECTABLE_METRIC_KINDS: Final[frozenset[MetricKind]] = frozenset(
    {MetricKind.RATIO, MetricKind.AMOUNT}
)

#: 頻度区分の名前の字種（D09 §7.8 の検査 E4）。照合は `fullmatch`。
_CLASS_NAME: Final = re.compile(r"[A-Z][A-Z0-9_]*")


def is_selectable_metric(metric: MetricId) -> bool:
    """選定と条件に書ける指標か（D09 §7.1 の検査 E1）。

    採用指標（参考値でない）で、種類が比率か金額のもの。取引件数（#3。件数）は成績の条件に
    書けず、証拠の要件でだけ使う。時間と価格差の指標も書けない。
    """
    return metric not in REFERENCE_METRICS and METRIC_KINDS[metric] in SELECTABLE_METRIC_KINDS


def _require_selectable(metric: object, label: str) -> None:
    if not isinstance(metric, MetricId):
        raise KernelValueError(f"{label} must be a MetricId")
    if not is_selectable_metric(metric):
        if metric is MetricId.TRADE_COUNT:
            reason = (
                "取引件数（#3）は成績の条件に書けない。証拠の要件（sufficiency）でだけ使う"
                "（D09 §7.1 の検査 E1・§7.8）"
            )
        elif metric in REFERENCE_METRICS:
            reason = "参考値は選定と判定に使わない（D09 §7.1 の検査 E1。D07 §5.3）"
        else:
            reason = (
                f"種類が {METRIC_KINDS[metric].value} の指標は書けない。書けるのは比率か金額の"
                "採用指標だけ（D09 §7.1 の検査 E1）"
            )
        raise KernelValueError(f"{label}: {metric.value} — {reason}")


def _require_decimal(value: object, label: str) -> None:
    if not isinstance(value, Decimal):
        raise KernelValueError(f"{label} must be a Decimal")
    if not value.is_finite():
        raise KernelValueError(f"{label} must be a finite Decimal, got {value}")


def _require_count(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"{label} must be an int, got {value!r}")
    if value < 0:
        raise KernelValueError(f"{label} must be >= 0, got {value} (D09 §7.8 の検査 E4)")


class StandardPurpose(Enum):
    """評価基準の用途（D09 §7.11・§10.9。2026-09-30 の人間の決定 Q33）。"""

    #: 共通基準の判定。
    STANDARD = "STANDARD"
    #: 機構の確認。「共通基準を満たす」を出さず、最終検証に進めない。
    MECHANISM_CHECK = "MECHANISM_CHECK"


class SelectionDirection(Enum):
    """選定の向き（D09 §7.2）。"""

    MAXIMIZE = "MAXIMIZE"
    MINIMIZE = "MINIMIZE"


class Comparator(Enum):
    """条件の比較（D09 §7.2・§7.3）。観測値 `比較` 閾値 が成り立てば満たす。"""

    GE = "GE"
    GT = "GT"
    LE = "LE"
    LT = "LT"


class FoldStatistic(Enum):
    """fold 全体の集約の語彙（D09 §7.3。v0.2 は `MEDIAN` だけ）。"""

    MEDIAN = "MEDIAN"


@dataclass(frozen=True, slots=True)
class MetricCondition:
    """指標の条件1件（D09 §7.2 の足切り・§7.3 の各 fold の最低条件）。"""

    metric: MetricId
    comparator: Comparator
    threshold: Decimal

    def __post_init__(self) -> None:
        _require_selectable(self.metric, "MetricCondition.metric")
        if not isinstance(self.comparator, Comparator):
            raise KernelValueError("MetricCondition.comparator must be a Comparator")
        _require_decimal(self.threshold, "MetricCondition.threshold")


@dataclass(frozen=True, slots=True)
class AggregateCondition:
    """fold 全体の水準の条件1件（D09 §7.3。v0.2）。"""

    metric: MetricId
    statistic: FoldStatistic
    comparator: Comparator
    threshold: Decimal

    def __post_init__(self) -> None:
        _require_selectable(self.metric, "AggregateCondition.metric")
        if not isinstance(self.statistic, FoldStatistic):
            raise KernelValueError("AggregateCondition.statistic must be a FoldStatistic")
        if not isinstance(self.comparator, Comparator):
            raise KernelValueError("AggregateCondition.comparator must be a Comparator")
        _require_decimal(self.threshold, "AggregateCondition.threshold")


def _require_conditions[ConditionT](
    items: object, item_type: type[ConditionT], label: str
) -> tuple[ConditionT, ...]:
    if not isinstance(items, tuple) or not all(isinstance(item, item_type) for item in items):
        raise KernelValueError(f"{label} must be a tuple of {item_type.__name__}")
    return items


def _reject_duplicates(keys: list[tuple[str, ...]], label: str) -> None:
    """同じ一覧の中で同じ鍵の条件を2つ書かない（D09 §7.1 の検査 E3）。"""
    seen: set[tuple[str, ...]] = set()
    for key in keys:
        if key in seen:
            raise KernelValueError(
                f"{label}: 同じ条件 {'/'.join(key)} が2つある。同じ指標と比較を2通りの閾値で"
                "書かない（D09 §7.1 の検査 E3）"
            )
        seen.add(key)


@dataclass(frozen=True, slots=True)
class SelectionRule:
    """選定規則（D09 §7.2）。`eligibility` は選定区間での成績の足切り（空を許す）。"""

    metric: MetricId
    direction: SelectionDirection
    eligibility: tuple[MetricCondition, ...]

    def __post_init__(self) -> None:
        _require_selectable(self.metric, "SelectionRule.metric")
        if not isinstance(self.direction, SelectionDirection):
            raise KernelValueError("SelectionRule.direction must be a SelectionDirection")
        conditions = _require_conditions(
            self.eligibility, MetricCondition, "SelectionRule.eligibility"
        )
        _reject_duplicates(
            [(item.metric.value, item.comparator.value) for item in conditions],
            "selection.eligibility",
        )


@dataclass(frozen=True, slots=True)
class ValidationRule:
    """検証の条件（D09 §7.3。v0.2）。

    `fold_floors` は各 fold の検証区間に当てる最低条件（空でない。E2）、`aggregate` は fold 全体
    の水準の条件（空を許す）。条件は書いた順に当て、結果も書いた順に並べる（E3 の注記）。
    """

    fold_floors: tuple[MetricCondition, ...]
    aggregate: tuple[AggregateCondition, ...]

    def __post_init__(self) -> None:
        floors = _require_conditions(
            self.fold_floors, MetricCondition, "ValidationRule.fold_floors"
        )
        aggregate = _require_conditions(
            self.aggregate, AggregateCondition, "ValidationRule.aggregate"
        )
        if not floors:
            raise KernelValueError(
                "validation.fold_floors が空である。最低条件の無い評価基準は、重大な損失を出した"
                " fold を落とせない（D09 §7.1 の検査 E2・§7.3）"
            )
        _reject_duplicates(
            [(item.metric.value, item.comparator.value) for item in floors],
            "validation.fold_floors",
        )
        _reject_duplicates(
            [
                (item.metric.value, item.statistic.value, item.comparator.value)
                for item in aggregate
            ],
            "validation.aggregate",
        )


@dataclass(frozen=True, slots=True)
class FrequencyClass:
    """取引頻度の区分1つと、その区分の証拠の要件（D09 §7.8。v0.2）。

    区分が持つのは証拠の要件だけで、成績の基準は持てない（区分によって成績の基準は緩まない）。
    """

    name: str
    min_train_trades_per_365d: Decimal
    min_validation_trades_per_fold: int
    min_validation_trades_total: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _CLASS_NAME.fullmatch(self.name):
            raise KernelValueError(
                f"頻度区分の名前 {self.name!r} は `[A-Z][A-Z0-9_]*` で書くこと"
                "（D09 §7.8 の検査 E4）"
            )
        _require_decimal(self.min_train_trades_per_365d, "FrequencyClass.min_train_trades_per_365d")
        if self.min_train_trades_per_365d < 0:
            raise KernelValueError(
                f"頻度区分 {self.name} の下限が負である（D09 §7.8 の検査 E4）:"
                f" {self.min_train_trades_per_365d}"
            )
        _require_count(
            self.min_validation_trades_per_fold, "FrequencyClass.min_validation_trades_per_fold"
        )
        _require_count(
            self.min_validation_trades_total, "FrequencyClass.min_validation_trades_total"
        )


@dataclass(frozen=True, slots=True)
class SufficiencyRule:
    """証拠の要件（D09 §7.8。v0.2）。区分は選定区間の取引頻度の下限の降順に並ぶ。"""

    classes: tuple[FrequencyClass, ...]

    def __post_init__(self) -> None:
        classes = _require_conditions(self.classes, FrequencyClass, "SufficiencyRule.classes")
        if not classes:
            raise KernelValueError("sufficiency.classes が空である（D09 §7.8 の検査 E4）")
        names = [item.name for item in classes]
        if len(set(names)) != len(names):
            raise KernelValueError(
                f"sufficiency.classes の区分名が重複している: {names}（D09 §7.8 の検査 E4）"
            )
        for previous, current in zip(classes, classes[1:], strict=False):
            if not current.min_train_trades_per_365d < previous.min_train_trades_per_365d:
                raise KernelValueError(
                    "sufficiency.classes の min_train_trades_per_365d は厳密に降順に並べること:"
                    f" {previous.name} の {previous.min_train_trades_per_365d} の次が"
                    f" {current.name} の {current.min_train_trades_per_365d}（D09 §7.8 の検査 E4）"
                )
        if classes[-1].min_train_trades_per_365d != 0:
            raise KernelValueError(
                f"sufficiency.classes の最後の区分 {classes[-1].name} の下限が"
                f" {classes[-1].min_train_trades_per_365d} で 0 でない。どの頻度もいずれかの区分に"
                "入るよう、最後の下限は 0 にする（D09 §7.8 の検査 E4）"
            )


@dataclass(frozen=True, slots=True)
class EvaluationStandard:
    """研究ポリシーの版 3 以上の評価基準の群（D09 §3・§7.6・§10.9。v0.2）。"""

    purpose: StandardPurpose
    split: SplitStandard
    selection: SelectionRule
    validation: ValidationRule
    sufficiency: SufficiencyRule

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, StandardPurpose):
            raise KernelValueError("EvaluationStandard.purpose must be a StandardPurpose")
        if not isinstance(self.split, SplitStandard):
            raise KernelValueError("EvaluationStandard.split must be a SplitStandard")
        if not isinstance(self.selection, SelectionRule):
            raise KernelValueError("EvaluationStandard.selection must be a SelectionRule")
        if not isinstance(self.validation, ValidationRule):
            raise KernelValueError("EvaluationStandard.validation must be a ValidationRule")
        if not isinstance(self.sufficiency, SufficiencyRule):
            raise KernelValueError("EvaluationStandard.sufficiency must be a SufficiencyRule")


# --- 探索計画と試行の列挙（D09 §5.1・§5.2・§6.2・§10.2。実装 PR 2）-------------------


def _require_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise KernelValueError(f"{label} must be a non-empty str")
    return value


def _require_index(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"{label} must be an int, got {value!r}")
    if value < 0:
        raise KernelValueError(f"{label} must be >= 0, got {value}")
    return value


def _is_parameter_value(value: object) -> bool:
    return isinstance(value, (BoolValue, IntValue, FloatValue, StrValue))


class SearchPlanKind(Enum):
    """探索計画の語彙（D09 §3・§5.1。段階5 は格子だけ。2026-09-29 の人間の決定 Q6）。"""

    #: 探索しない（単一実行の実験）。
    NONE = "NONE"
    #: 全軸の値の直積をすべて試す。
    GRID = "GRID"


@dataclass(frozen=True, slots=True)
class ParameterAxis:
    """探索の軸1本（D09 §3・§5.1）。

    戦略ファイルの使用箇所 `instance_id` のパラメータ `parameter` について、試す値を**書いた順**に
    並べる。値は D04 §7 の `ParameterValue` で、空の列・同じ値の2回（正規化エンコードで比べる。
    D02 §9.3）・区分の混在は拒否する（D09 §5.5 の2）。値の型が部品の契約の `value_type` と合うか
    は、契約を引ける読込（`app.config`）が確かめる。
    """

    instance_id: str
    parameter: str
    values: tuple[ParameterValue, ...]

    def __post_init__(self) -> None:
        _require_name(self.instance_id, "ParameterAxis.instance_id")
        _require_name(self.parameter, "ParameterAxis.parameter")
        if not isinstance(self.values, tuple) or not all(
            _is_parameter_value(value) for value in self.values
        ):
            raise KernelValueError("ParameterAxis.values must be a tuple of ParameterValue")
        label = f"{self.instance_id}.{self.parameter}"
        if not self.values:
            raise KernelValueError(f"軸 {label} の値の列が空である（D09 §5.5 の2）")
        if len({type(value) for value in self.values}) != 1:
            raise KernelValueError(f"軸 {label} の値の型が混在している（D09 §5.5 の2）")
        encoded = [encode(value) for value in self.values]
        if len(set(encoded)) != len(encoded):
            raise KernelValueError(f"軸 {label} に同じ値が2回ある（D09 §5.5 の2）")

    @property
    def key(self) -> tuple[str, str]:
        """軸の鍵 `(instance_id, parameter)`（並べる順と重複の判定に使う）。"""
        return (self.instance_id, self.parameter)


@dataclass(frozen=True, slots=True)
class SearchPlan:
    """探索計画（D09 §3・§5.1）。

    軸は `(instance_id, parameter)` の文字列の昇順に並べ直して持つ（列挙の順序の規則。D09 §5.2）。
    各軸の値の書き順はそのまま残す（番号と同点の解き方を決める事前固定の一部）。`max_trials` は
    実験が自分で書く探索空間の大きさの上限で、列挙した試行の数がこれを超えれば構造エラー
    （読込が設定の誤りとして示す。D09 §5.5 の3）。下回るのは許す。
    """

    kind: SearchPlanKind
    axes: tuple[ParameterAxis, ...]
    max_trials: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SearchPlanKind):
            raise KernelValueError("SearchPlan.kind must be a SearchPlanKind")
        if not isinstance(self.axes, tuple) or not all(
            isinstance(axis, ParameterAxis) for axis in self.axes
        ):
            raise KernelValueError("SearchPlan.axes must be a tuple of ParameterAxis")
        if isinstance(self.max_trials, bool) or not isinstance(self.max_trials, int):
            raise KernelValueError("max_trials は整数で書くこと（D09 §5.5 の3）")
        if self.max_trials < 1:
            raise KernelValueError(
                f"max_trials は正の整数で書くこと（{self.max_trials}。D09 §5.5 の3）"
            )
        if self.kind is SearchPlanKind.GRID and not self.axes:
            raise KernelValueError("GRID の探索計画の axes が空である（D09 §5.5 の1）")
        if self.kind is SearchPlanKind.NONE and self.axes:
            raise KernelValueError("NONE の探索計画に axes がある（D09 §5.5 の1）")
        keys = [axis.key for axis in self.axes]
        if len(set(keys)) != len(keys):
            duplicated = sorted({key for key in keys if keys.count(key) > 1})
            raise KernelValueError(
                f"同じ使用箇所とパラメータの軸が2つある: {duplicated}（D09 §5.5 の2）"
            )
        object.__setattr__(self, "axes", tuple(sorted(self.axes, key=lambda axis: axis.key)))
        if self.trial_count > self.max_trials:
            raise KernelValueError(
                f"列挙した試行の数 {self.trial_count} が max_trials {self.max_trials} を超える。"
                "超えた分を切り捨てて一部だけ試すことはしない（D09 §5.1・§5.5 の3）"
            )

    @property
    def trial_count(self) -> int:
        """列挙する試行の数（全軸の値の数の積。軸が無ければ 1）。"""
        count = 1
        for axis in self.axes:
            count *= len(axis.values)
        return count


@dataclass(frozen=True, slots=True)
class ParameterAssignment:
    """試行1つのパラメータ割当（D09 §3・§5.2）。

    `values` は `(instance_id, parameter, 値)` を `(instance_id, parameter)` の昇順に並べたもの
    （構築時に並べ直す）。同じ鍵の2回は拒否する。
    """

    values: tuple[tuple[str, str, ParameterValue], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple):
            raise KernelValueError("ParameterAssignment.values must be a tuple")
        for item in self.values:
            if (
                not isinstance(item, tuple)
                or len(item) != 3
                or not isinstance(item[0], str)
                or not item[0]
                or not isinstance(item[1], str)
                or not item[1]
                or not _is_parameter_value(item[2])
            ):
                raise KernelValueError(
                    "ParameterAssignment.values holds (instance_id, parameter, ParameterValue)"
                )
        keys = [(item[0], item[1]) for item in self.values]
        if len(set(keys)) != len(keys):
            raise KernelValueError("ParameterAssignment assigns the same parameter twice")
        object.__setattr__(
            self, "values", tuple(sorted(self.values, key=lambda item: (item[0], item[1])))
        )


def enumerate_assignments(plan: SearchPlan) -> tuple[ParameterAssignment, ...]:
    """探索計画から試行の割当を列挙する（D09 §5.2）。

    軸は `(instance_id, parameter)` の昇順（`SearchPlan` が並べ済み）、各軸の値は書いた順のまま、
    **後ろの軸ほど速く変わる**直積の順に並べる。戻り値の位置がそのまま `trial_index`（先頭が 0）。
    同じ計画からは常に同じ列が出る（乱数を使わない。D09 §5.4）。
    """
    if not isinstance(plan, SearchPlan):
        raise KernelValueError("enumerate_assignments requires a SearchPlan")
    if plan.kind is not SearchPlanKind.GRID:
        raise KernelValueError("only a GRID search plan enumerates trials (D09 §5.3)")
    return tuple(
        ParameterAssignment(
            values=tuple(
                (axis.instance_id, axis.parameter, value)
                for axis, value in zip(plan.axes, combination, strict=True)
            )
        )
        for combination in itertools.product(*(axis.values for axis in plan.axes))
    )


class TrialPhase(Enum):
    """単位の局面（D09 §3・§6.2）。宣言順が実行の順（選定区間 → 検証区間。D09 §5.4）。"""

    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"


#: 局面の実行の順（D09 §5.4）。
_PHASE_ORDER: Final = {phase: index for index, phase in enumerate(TrialPhase)}


@dataclass(frozen=True, slots=True)
class TrialUnitKey:
    """試行の実行単位の鍵 `(fold_index, phase, trial_index)`（D09 §3・§6.2）。"""

    fold_index: int
    phase: TrialPhase
    trial_index: int

    def __post_init__(self) -> None:
        _require_index(self.fold_index, "TrialUnitKey.fold_index")
        if not isinstance(self.phase, TrialPhase):
            raise KernelValueError("TrialUnitKey.phase must be a TrialPhase")
        _require_index(self.trial_index, "TrialUnitKey.trial_index")

    @property
    def order(self) -> tuple[int, int, int]:
        """実行の順の鍵（fold の昇順 → 局面 → 試行番号の昇順。D09 §5.4）。"""
        return (self.fold_index, _PHASE_ORDER[self.phase], self.trial_index)


def trial_units(fold_count: int, trial_index: int) -> tuple[TrialUnitKey, ...]:
    """1つの試行が記録票に予測ダイジェストを持つ単位（D09 §10.2・§10.5 の注記）。

    コンパイルが通った試行は、全 fold の選定区間の単位と、**選ばれるかどうかによらず**全 fold の
    検証区間の単位を持つ。fold の昇順、局面は選定区間 → 検証区間の順に並べる。
    """
    _require_index(fold_count, "fold_count")
    _require_index(trial_index, "trial_index")
    return tuple(
        TrialUnitKey(fold_index=fold, phase=phase, trial_index=trial_index)
        for fold in range(fold_count)
        for phase in TrialPhase
    )


def _rejection_sort_key(error: CompileError) -> tuple[bool, str, str, str, str]:
    """`(location, check_id)` の昇順の鍵。location は使用箇所 → フィールド経路の順に比べ、
    使用箇所を持たない拒否（戦略全体の宣言の拒否）を先に置く。"""
    location = error.location
    return (
        location.instance_id is not None,
        location.instance_id or "",
        location.field_path,
        error.check_id,
        error.rejection.value,
    )


def compile_rejections_of(errors: Sequence[CompileError]) -> tuple[str, ...]:
    """コンパイル拒否を記録票の `TrialPlan.compile_rejections` の形にする（D09 §3・§5.2）。

    1件ごとに `check_id`・拒否の区分（`CompileRejection`）・`location`（使用箇所とフィールド経路）
    の3つを正規化エンコードした文字列にし、`(location, check_id)` の昇順に並べる（D02 §9.3）。
    拒否の文言（`message`）は入れない（文言の書き換えで記録票の識別子が変わらないようにする）。
    """
    if not all(isinstance(error, CompileError) for error in errors):
        raise KernelValueError("compile_rejections_of requires CompileError values")
    return tuple(
        encode(
            {
                "check_id": error.check_id,
                "rejection": error.rejection.value,
                "location": {
                    "instance_id": error.location.instance_id,
                    "field_path": error.location.field_path,
                },
            }
        ).decode("utf-8")
        for error in sorted(errors, key=_rejection_sort_key)
    )


@dataclass(frozen=True, slots=True)
class TrialPlan:
    """試行1つの事前固定の内容（D09 §3・§5.2・§10.2）。

    - コンパイルが通った試行: `compiled_ref` を持ち、`compile_rejections` は空、
      `expected_config_digests` は全単位（`trial_units`）の予測 `ConfigDigest`。
    - コンパイルが拒否した試行（`FAILED`）: `compiled_ref = None`、`compile_rejections` は空でない、
      `expected_config_digests` は空（`RunConfig` を組み立てられないため）。

    `expected_config_digests` は単位の実行の順（`TrialUnitKey.order`）に並べ直して持つ。
    """

    trial_index: int
    assignment: ParameterAssignment
    compiled_ref: CompiledStrategyRef | None
    compile_rejections: tuple[str, ...]
    expected_config_digests: tuple[tuple[TrialUnitKey, ConfigDigest], ...]

    def __post_init__(self) -> None:
        _require_index(self.trial_index, "TrialPlan.trial_index")
        if not isinstance(self.assignment, ParameterAssignment):
            raise KernelValueError("TrialPlan.assignment must be a ParameterAssignment")
        if self.compiled_ref is not None and not isinstance(self.compiled_ref, CompiledStrategyRef):
            raise KernelValueError("TrialPlan.compiled_ref must be a CompiledStrategyRef or None")
        if not isinstance(self.compile_rejections, tuple) or not all(
            isinstance(item, str) and item for item in self.compile_rejections
        ):
            raise KernelValueError("TrialPlan.compile_rejections must be a tuple of str")
        if not isinstance(self.expected_config_digests, tuple) or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], TrialUnitKey)
            and isinstance(item[1], ConfigDigest)
            for item in self.expected_config_digests
        ):
            raise KernelValueError(
                "TrialPlan.expected_config_digests must hold (TrialUnitKey, ConfigDigest) pairs"
            )
        if (self.compiled_ref is None) == (not self.compile_rejections):
            raise KernelValueError(
                "a trial either compiles (compiled_ref) or is rejected (compile_rejections),"
                " not both and not neither (D09 §5.2)"
            )
        if self.compiled_ref is None and self.expected_config_digests:
            raise KernelValueError(
                "a trial rejected by the compiler has no expected config digests (D09 §10.2)"
            )
        if self.compiled_ref is not None and not self.expected_config_digests:
            raise KernelValueError(
                "a compiled trial carries the expected config digest of every unit (D09 §10.2)"
            )
        units = [unit for unit, _ in self.expected_config_digests]
        if any(unit.trial_index != self.trial_index for unit in units):
            raise KernelValueError("TrialPlan.expected_config_digests holds another trial's units")
        if len(set(units)) != len(units):
            raise KernelValueError("TrialPlan.expected_config_digests holds a unit twice")
        object.__setattr__(
            self,
            "expected_config_digests",
            tuple(sorted(self.expected_config_digests, key=lambda item: item[0].order)),
        )

    @property
    def compiled(self) -> bool:
        """コンパイルが通った試行か（拒否された試行は `FAILED`。D09 §10.4）。"""
        return self.compiled_ref is not None

    def expected_config_digest(self, unit: TrialUnitKey) -> ConfigDigest:
        """単位の予測 `ConfigDigest`。無い単位は構造エラー。"""
        for key, value in self.expected_config_digests:
            if key == unit:
                return value
        raise KernelValueError(f"the trial {self.trial_index} has no unit {unit}")


# ---------------------------------------------------------------------------
# 選定・判定・頻度区分・試行の状態の導出（D09 §7.2〜§7.5・§7.8・§10.4。段階5 実装 PR 3）
# ---------------------------------------------------------------------------

#: 365 日の秒数（D09 §7.8 の頻度 `r` の換算）。
SECONDS_PER_365_DAYS: Final = 31_536_000

#: 観測が足りないことによる値なしの理由（D09 §7.3 の値なしの理由の分け方）。証拠の不足に数える。
OBSERVATION_SHORTFALL_REASONS: Final[frozenset[MetricUnavailableReason]] = frozenset(
    {
        MetricUnavailableReason.NO_TRADES,
        MetricUnavailableReason.NO_OBSERVATIONS,
        MetricUnavailableReason.UNDEFINED_DENOMINATOR,
    }
)

_ONE_SECOND: Final = timedelta(seconds=1)


class TrialStatus(Enum):
    """試行の実行単位の状態（D09 §10.4）。宣言順が `SearchOutcome.trial_counts` の並び。"""

    #: 未試行（開始記録も試行記録も無い）。
    NOT_STARTED = "NOT_STARTED"
    #: 試行済み（試行記録がある）。
    COMPLETED = "COMPLETED"
    #: 失敗（記録票の `compile_rejections` が空でない試行の全単位）。
    FAILED = "FAILED"
    #: 中断（開始記録はあるが試行記録が無い）。
    ABORTED = "ABORTED"


def derive_trial_status(*, compile_rejected: bool, started: bool, recorded: bool) -> TrialStatus:
    """単位の状態を記録から導く（D09 §10.4 の表。上の行から順に最初に当てはまるもの）。

    `compile_rejected` は記録票の `TrialPlan.compile_rejections` が空でないこと、`started` は
    開始記録があること、`recorded` は試行記録があること。コンパイル拒否の試行は `RunConfig`
    を組み立てられない（D09 §5.2）ので開始記録も試行記録も持たず、持てば構造エラー。
    """
    for name, value in (
        ("compile_rejected", compile_rejected),
        ("started", started),
        ("recorded", recorded),
    ):
        if not isinstance(value, bool):
            raise KernelValueError(f"derive_trial_status.{name} must be a bool")
    if compile_rejected:
        if started or recorded:
            raise KernelValueError(
                "コンパイル拒否の試行の単位に開始記録か試行記録がある（D09 §10.4。"
                "コンパイル拒否の試行は run を始めない）"
            )
        return TrialStatus.FAILED
    if recorded:
        return TrialStatus.COMPLETED
    if started:
        return TrialStatus.ABORTED
    return TrialStatus.NOT_STARTED


def count_trial_statuses(statuses: Iterable[TrialStatus]) -> tuple[tuple[TrialStatus, int], ...]:
    """単位の状態の件数を `TrialStatus` の宣言順に、0 件も含めて数える（D09 §3）。"""
    items = tuple(statuses)
    if not all(isinstance(item, TrialStatus) for item in items):
        raise KernelValueError("count_trial_statuses requires TrialStatus values")
    return tuple((status, sum(1 for item in items if item is status)) for status in TrialStatus)


class CandidateStatus(Enum):
    """選定の入力での試行の区分（D09 §7.4）。上から順に最初に当てはまるもの。"""

    CANDIDATE = "CANDIDATE"
    EXCLUDED_TRIAL_FAILED = "EXCLUDED_TRIAL_FAILED"
    EXCLUDED_NOT_COMPLETED = "EXCLUDED_NOT_COMPLETED"
    EXCLUDED_POST_RUN_CHECK = "EXCLUDED_POST_RUN_CHECK"
    EXCLUDED_METRIC_UNAVAILABLE = "EXCLUDED_METRIC_UNAVAILABLE"
    EXCLUDED_INELIGIBLE = "EXCLUDED_INELIGIBLE"


@dataclass(frozen=True, slots=True)
class _UnitEvaluation:
    """試行の実行単位1つの、実行と評価の結果（選定と判定の入力。D09 §7.2〜§7.4）。

    `status` は単位の状態（D09 §10.4）。試行済み（`COMPLETED`）の単位だけが run と評価の
    項目を持ち、それ以外は `None`・`False`・空の指標を持つ。`post_run_checks_passed` は単位の
    事後検査（P4・P5。D09 §10.8）がすべて合格したか。`metrics` は評価の `METRICS` 表の行
    （D07 §8.1）で、評価が `COMPLETED` のときだけ行を持つ（`REJECTED` / `FAILED` は0行）。

    `run_evaluation_id` は D07 §9.2 の評価の識別子のダイジェスト（`RunEvaluationId.digest`）で
    ある。`RunEvaluationId` は `evaluation.application` にあり domain から参照できない
    （D01 §3 の層の規則。結末記録の `ExperimentOutcome.run_evaluation_id` と同じ扱い）。
    """

    trial_index: int
    status: TrialStatus
    run_status: RunStatus | None
    run_evaluation_id: ContentDigest | None
    evaluation_status: EvaluationStatus | None
    post_run_checks_passed: bool
    metrics: tuple[MetricRecord, ...]

    def __post_init__(self) -> None:
        _require_index(self.trial_index, "unit trial_index")
        if not isinstance(self.status, TrialStatus):
            raise KernelValueError("unit status must be a TrialStatus")
        if not isinstance(self.post_run_checks_passed, bool):
            raise KernelValueError("unit post_run_checks_passed must be a bool")
        if not isinstance(self.metrics, tuple) or not all(
            isinstance(item, MetricRecord) for item in self.metrics
        ):
            raise KernelValueError("unit metrics must be a tuple of MetricRecord")
        ids = [item.metric_id for item in self.metrics]
        if len(set(ids)) != len(ids):
            raise KernelValueError(
                f"unit {self.trial_index} の指標の行が重複している（D07 §8.1 の主キー）"
            )
        if self.status is TrialStatus.COMPLETED:
            if not isinstance(self.run_status, RunStatus):
                raise KernelValueError("a COMPLETED unit must carry its RunStatus")
            if not isinstance(self.run_evaluation_id, ContentDigest):
                raise KernelValueError("a COMPLETED unit must carry its run evaluation digest")
            if not isinstance(self.evaluation_status, EvaluationStatus):
                raise KernelValueError("a COMPLETED unit must carry its EvaluationStatus")
            if self.evaluation_status is EvaluationStatus.ABORTED:
                raise KernelValueError(
                    "評価の状態 ABORTED は試行記録に現れない（D09 §10.4。評価 manifest は"
                    " COMPLETED / REJECTED / FAILED のどれか）"
                )
            if self.evaluation_status is not EvaluationStatus.COMPLETED and self.metrics:
                raise KernelValueError(
                    "評価が COMPLETED でない単位は指標の行を持たない（D07 §10.1）"
                )
        elif (
            self.run_status is not None
            or self.run_evaluation_id is not None
            or self.evaluation_status is not None
            or self.metrics
            or self.post_run_checks_passed
        ):
            raise KernelValueError(
                f"試行済みでない単位（{self.status.value}）は run と評価の結果を持たない"
                "（D09 §10.4）"
            )

    @property
    def has_valid_result(self) -> bool:
        """試行済み・run が正常完走・評価が `COMPLETED`・事後検査に合格（D09 §7.3 の用語）。"""
        return (
            self.status is TrialStatus.COMPLETED
            and self.run_status is RunStatus.COMPLETED
            and self.evaluation_status is EvaluationStatus.COMPLETED
            and self.post_run_checks_passed
        )

    def value_of(self, metric: MetricId) -> MetricValue:
        """指標の値（評価が `COMPLETED` の単位は全指標の行を持つ。無ければ構造エラー）。"""
        for item in self.metrics:
            if item.metric_id is metric:
                return item.value
        raise KernelValueError(
            f"unit {self.trial_index} の評価に指標 {metric.value} の行が無い（D07 §8.1）"
        )


@dataclass(frozen=True, slots=True)
class TrainUnitEvaluation(_UnitEvaluation):
    """選定区間の単位の結果。**選定の関数はこの型だけを受け取る**（D09 §7.2。完了条件1）。"""


@dataclass(frozen=True, slots=True)
class ValidationUnitEvaluation(_UnitEvaluation):
    """検証区間の単位の結果。選定と頻度区分の関数には渡せない（D09 §7.2・§7.8）。"""


@dataclass(frozen=True, slots=True)
class FoldSelection:
    """fold の選定記録（D09 §3・§7.2・§7.5・§7.8）。

    `inputs` は `(trial_index, 選定区間の評価の識別子のダイジェスト, 候補の区分)` を
    `trial_index` の昇順に全試行（評価の識別子が無い単位は `None`）。
    """

    fold_index: int
    selected_trial_index: int | None
    selected_value: Decimal | None
    selected_train_trade_count: int | None
    inputs: tuple[tuple[int, ContentDigest | None, CandidateStatus], ...]

    def __post_init__(self) -> None:
        _require_index(self.fold_index, "FoldSelection.fold_index")
        selected = (
            self.selected_trial_index,
            self.selected_value,
            self.selected_train_trade_count,
        )
        if any(item is None for item in selected) and not all(item is None for item in selected):
            raise KernelValueError(
                "FoldSelection: 選んだ試行・値・取引件数は、そろって値を持つかそろって None"
            )
        if not isinstance(self.inputs, tuple):
            raise KernelValueError("FoldSelection.inputs must be a tuple")
        indices: list[int] = []
        for entry in self.inputs:
            if (
                not isinstance(entry, tuple)
                or len(entry) != 3
                or isinstance(entry[0], bool)
                or not isinstance(entry[0], int)
                or not (entry[1] is None or isinstance(entry[1], ContentDigest))
                or not isinstance(entry[2], CandidateStatus)
            ):
                raise KernelValueError(
                    "FoldSelection.inputs must be (trial_index, ContentDigest | None,"
                    " CandidateStatus) triples"
                )
            indices.append(entry[0])
        if indices != sorted(set(indices)):
            raise KernelValueError("FoldSelection.inputs は trial_index の昇順で重複なし（D09 §3）")
        if self.selected_trial_index is not None:
            _require_index(self.selected_trial_index, "FoldSelection.selected_trial_index")
            _require_decimal(self.selected_value, "FoldSelection.selected_value")
            _require_count(
                self.selected_train_trade_count, "FoldSelection.selected_train_trade_count"
            )
            status_of = {entry[0]: entry[2] for entry in self.inputs}
            if status_of.get(self.selected_trial_index) is not CandidateStatus.CANDIDATE:
                raise KernelValueError(
                    "FoldSelection: 選んだ試行は inputs の中で候補（CANDIDATE）でなければならない"
                )
        elif any(entry[2] is CandidateStatus.CANDIDATE for entry in self.inputs):
            raise KernelValueError(
                "FoldSelection: 候補（CANDIDATE）があるのに選んだ試行が無い（D09 §7.2）"
            )


class ConditionScope(Enum):
    """条件の当て先（D09 §7.3。v0.2）。"""

    FOLD_FLOOR = "FOLD_FLOOR"
    AGGREGATE = "AGGREGATE"


class ConditionOutcome(Enum):
    """条件1件の結果（D09 §7.3。v0.2）。"""

    MET = "MET"
    NOT_MET = "NOT_MET"
    #: 観測が足りないことによる値なしで比べられない（理由は `unavailable_reason`）。
    UNCOMPUTABLE = "UNCOMPUTABLE"


@dataclass(frozen=True, slots=True)
class ConditionResult:
    """条件1件の結果（D09 §3・§7.3。v0.2）。"""

    scope: ConditionScope
    fold_index: int | None
    metric: MetricId
    statistic: FoldStatistic | None
    comparator: Comparator
    threshold: Decimal
    observed: Decimal | None
    outcome: ConditionOutcome
    unavailable_reason: MetricUnavailableReason | None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ConditionScope):
            raise KernelValueError("ConditionResult.scope must be a ConditionScope")
        if self.scope is ConditionScope.FOLD_FLOOR:
            _require_index(self.fold_index, "ConditionResult.fold_index")
            if self.statistic is not None:
                raise KernelValueError("a FOLD_FLOOR ConditionResult has no statistic")
        else:
            if self.fold_index is not None:
                raise KernelValueError("an AGGREGATE ConditionResult has no fold_index")
            if not isinstance(self.statistic, FoldStatistic):
                raise KernelValueError("an AGGREGATE ConditionResult needs a FoldStatistic")
        if not isinstance(self.metric, MetricId):
            raise KernelValueError("ConditionResult.metric must be a MetricId")
        if not isinstance(self.comparator, Comparator):
            raise KernelValueError("ConditionResult.comparator must be a Comparator")
        _require_decimal(self.threshold, "ConditionResult.threshold")
        if not isinstance(self.outcome, ConditionOutcome):
            raise KernelValueError("ConditionResult.outcome must be a ConditionOutcome")
        if self.outcome is ConditionOutcome.UNCOMPUTABLE:
            if self.observed is not None:
                raise KernelValueError("an UNCOMPUTABLE ConditionResult has no observed value")
            if self.unavailable_reason not in OBSERVATION_SHORTFALL_REASONS:
                raise KernelValueError(
                    "an UNCOMPUTABLE ConditionResult carries an observation-shortfall reason"
                    " (D09 §7.3)"
                )
        else:
            _require_decimal(self.observed, "ConditionResult.observed")
            if self.unavailable_reason is not None:
                raise KernelValueError("a MET / NOT_MET ConditionResult has no unavailable_reason")
            assert self.observed is not None  # noqa: S101  直前で確かめた
            met = _compare(self.observed, self.comparator, self.threshold)
            if met is not (self.outcome is ConditionOutcome.MET):
                raise KernelValueError(
                    f"ConditionResult.outcome {self.outcome.value} contradicts"
                    f" {self.observed} {self.comparator.value} {self.threshold}"
                )


class SufficiencyShortfallKind(Enum):
    """証拠不足の理由の種類（D09 §3・§7.8。v0.2）。"""

    FOLD_TRADES_BELOW = "FOLD_TRADES_BELOW"
    #: 取引が少ない fold で最低条件を割ったが、観測不足のため判定できない（Q16 決定）。
    FLOOR_NOT_MET_TRADES_BELOW = "FLOOR_NOT_MET_TRADES_BELOW"
    TOTAL_TRADES_BELOW = "TOTAL_TRADES_BELOW"
    METRIC_UNCOMPUTABLE = "METRIC_UNCOMPUTABLE"
    NO_CANDIDATE_METRIC_UNAVAILABLE = "NO_CANDIDATE_METRIC_UNAVAILABLE"


_TRADE_SHORTFALLS: Final = frozenset(
    {
        SufficiencyShortfallKind.FOLD_TRADES_BELOW,
        SufficiencyShortfallKind.FLOOR_NOT_MET_TRADES_BELOW,
        SufficiencyShortfallKind.TOTAL_TRADES_BELOW,
    }
)


@dataclass(frozen=True, slots=True)
class SufficiencyShortfall:
    """証拠不足の理由1件（D09 §3・§7.8。v0.2）。

    主キーは `(kind, fold_index, trial_index, metric, reason)`（D09 §12）。件数の不足は
    `required` と `observed` を、指標の値なしは `metric` と `reason` を持つ。
    """

    kind: SufficiencyShortfallKind
    fold_index: int | None
    trial_index: int | None
    metric: MetricId | None
    reason: MetricUnavailableReason | None
    required: int | None
    observed: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SufficiencyShortfallKind):
            raise KernelValueError("SufficiencyShortfall.kind must be a SufficiencyShortfallKind")
        if self.kind is SufficiencyShortfallKind.TOTAL_TRADES_BELOW:
            if self.fold_index is not None or self.trial_index is not None:
                raise KernelValueError("TOTAL_TRADES_BELOW has no fold_index / trial_index")
        else:
            _require_index(self.fold_index, "SufficiencyShortfall.fold_index")
            _require_index(self.trial_index, "SufficiencyShortfall.trial_index")
        if self.kind in _TRADE_SHORTFALLS:
            if self.metric is not None or self.reason is not None:
                raise KernelValueError(f"{self.kind.value} has no metric / reason")
            _require_count(self.required, "SufficiencyShortfall.required")
            _require_count(self.observed, "SufficiencyShortfall.observed")
            assert self.required is not None and self.observed is not None  # noqa: S101
            if not self.observed < self.required:
                raise KernelValueError(
                    f"{self.kind.value}: observed {self.observed} is not below the requirement"
                    f" {self.required}"
                )
        else:
            if not isinstance(self.metric, MetricId):
                raise KernelValueError(f"{self.kind.value} needs a MetricId")
            if self.reason not in OBSERVATION_SHORTFALL_REASONS:
                raise KernelValueError(
                    f"{self.kind.value} needs an observation-shortfall reason (D09 §7.3)"
                )
            if self.required is not None or self.observed is not None:
                raise KernelValueError(f"{self.kind.value} has no required / observed count")

    @property
    def key(self) -> tuple[int, int, int, int, int]:
        """主キーの整列鍵（D09 §12）。列挙は宣言順、`None` は先頭。"""
        return (
            _ordinal(self.kind),
            -1 if self.fold_index is None else self.fold_index,
            -1 if self.trial_index is None else self.trial_index,
            -1 if self.metric is None else _ordinal(self.metric),
            -1 if self.reason is None else _ordinal(self.reason),
        )


@dataclass(frozen=True, slots=True)
class FrequencyAssessment:
    """頻度区分の結果（D09 §3・§7.8。v0.2）。"""

    class_name: str
    train_trade_count: int
    train_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.class_name, str) or not _CLASS_NAME.fullmatch(self.class_name):
            raise KernelValueError("FrequencyAssessment.class_name must be a frequency class name")
        _require_count(self.train_trade_count, "FrequencyAssessment.train_trade_count")
        _require_count(self.train_seconds, "FrequencyAssessment.train_seconds")
        if self.train_seconds == 0:
            raise KernelValueError("FrequencyAssessment.train_seconds must be > 0")

    @property
    def trades_per_365d(self) -> Decimal:
        """365 日あたりの取引頻度 `r`（D09 §7.8。カーネル精度で1回割る）。"""
        return trades_per_365d(self.train_trade_count, self.train_seconds)


class FoldVerdict(Enum):
    """fold の判定（D09 §3・§7.3。v0.2）。"""

    FLOORS_MET = "FLOORS_MET"
    FLOOR_BREACHED = "FLOOR_BREACHED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_ELIGIBLE_TRIAL = "NO_ELIGIBLE_TRIAL"
    INCOMPLETE = "INCOMPLETE"


class SearchVerdict(Enum):
    """実験の判定（D09 §0.2・§3・§7.3。v0.2）。「合格」と呼ばない。"""

    #: 共通基準を満たす（用途が `STANDARD` のときだけ）。
    MEETS_STANDARD = "MEETS_STANDARD"
    #: 共通基準を満たさない。
    BELOW_STANDARD = "BELOW_STANDARD"
    #: 証拠不足。
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    #: 判定できない。
    INCOMPLETE = "INCOMPLETE"
    #: 機構の確認で条件をすべて満たした（用途が `MECHANISM_CHECK` のときだけ。Q33 決定）。
    MET_IN_MECHANISM_CHECK = "MET_IN_MECHANISM_CHECK"


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """結末記録の探索の項目（D09 §3・§10.6。v0.2）。"""

    selections: tuple[FoldSelection, ...]
    fold_verdicts: tuple[tuple[int, FoldVerdict], ...]
    verdict: SearchVerdict
    frequency: FrequencyAssessment | None
    condition_results: tuple[ConditionResult, ...]
    shortfalls: tuple[SufficiencyShortfall, ...]
    trial_counts: tuple[tuple[TrialStatus, int], ...]
    ledger_execution: int
    purpose: StandardPurpose

    def __post_init__(self) -> None:
        if not isinstance(self.selections, tuple) or not all(
            isinstance(item, FoldSelection) for item in self.selections
        ):
            raise KernelValueError("SearchOutcome.selections must be a tuple of FoldSelection")
        fold_indices = [item.fold_index for item in self.selections]
        if not fold_indices or fold_indices != list(range(len(fold_indices))):
            raise KernelValueError("SearchOutcome.selections は fold_index 0 からの連番")
        if not isinstance(self.fold_verdicts, tuple) or not all(
            isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], FoldVerdict)
            for entry in self.fold_verdicts
        ):
            raise KernelValueError("SearchOutcome.fold_verdicts must be (int, FoldVerdict) pairs")
        if [entry[0] for entry in self.fold_verdicts] != fold_indices:
            raise KernelValueError("SearchOutcome.fold_verdicts は selections と同じ fold の順")
        if not isinstance(self.verdict, SearchVerdict):
            raise KernelValueError("SearchOutcome.verdict must be a SearchVerdict")
        if not isinstance(self.purpose, StandardPurpose):
            raise KernelValueError("SearchOutcome.purpose must be a StandardPurpose")
        if (
            self.verdict is SearchVerdict.MEETS_STANDARD
            and self.purpose is not StandardPurpose.STANDARD
        ):
            raise KernelValueError(
                "MEETS_STANDARD は用途が STANDARD の評価基準でだけ出る（D09 §7.3 の手順4。Q33）"
            )
        if (
            self.verdict is SearchVerdict.MET_IN_MECHANISM_CHECK
            and self.purpose is not StandardPurpose.MECHANISM_CHECK
        ):
            raise KernelValueError(
                "MET_IN_MECHANISM_CHECK は用途が MECHANISM_CHECK の評価基準でだけ出る（Q33）"
            )
        has_selected = any(item.selected_trial_index is not None for item in self.selections)
        if self.frequency is None:
            if has_selected:
                raise KernelValueError(
                    "SearchOutcome.frequency は選んだ試行のある fold があれば値を持つ（D09 §3）"
                )
        elif not isinstance(self.frequency, FrequencyAssessment) or not has_selected:
            raise KernelValueError(
                "SearchOutcome.frequency は選んだ試行のある fold が無ければ None（D09 §3）"
            )
        if not isinstance(self.condition_results, tuple) or not all(
            isinstance(item, ConditionResult) for item in self.condition_results
        ):
            raise KernelValueError("SearchOutcome.condition_results must be ConditionResult")
        if not isinstance(self.shortfalls, tuple) or not all(
            isinstance(item, SufficiencyShortfall) for item in self.shortfalls
        ):
            raise KernelValueError("SearchOutcome.shortfalls must be SufficiencyShortfall")
        keys = [item.key for item in self.shortfalls]
        if keys != sorted(set(keys)):
            raise KernelValueError(
                "SearchOutcome.shortfalls は主キー (kind, fold_index, trial_index, metric,"
                " reason) の昇順で重複なし（D09 §12）"
            )
        if not isinstance(self.trial_counts, tuple) or not all(
            isinstance(entry, tuple) and len(entry) == 2 for entry in self.trial_counts
        ):
            raise KernelValueError("SearchOutcome.trial_counts must be (TrialStatus, int) pairs")
        if [entry[0] for entry in self.trial_counts] != list(TrialStatus):
            raise KernelValueError(
                "SearchOutcome.trial_counts は TrialStatus の宣言順に全状態（0件も出す。D09 §3）"
            )
        for entry in self.trial_counts:
            _require_count(entry[1], "SearchOutcome.trial_counts")
        _require_count(self.ledger_execution, "SearchOutcome.ledger_execution")


@dataclass(frozen=True, slots=True)
class FoldEvidence:
    """fold 1つの判定の入力（D09 §7.3）。全 fold が終端した後に組み立てる。

    `selection` は保存済みの選定記録、`train_units` はその fold の選定区間の全試行の結果
    （候補なしの fold の証拠不足の理由に、観測不足の指標と理由を出すために使う）、
    `validation` は選んだ試行の検証区間の単位の結果（候補なしの fold は `None`）。
    """

    fold: Fold
    selection: FoldSelection
    train_units: tuple[TrainUnitEvaluation, ...]
    validation: ValidationUnitEvaluation | None

    def __post_init__(self) -> None:
        if not isinstance(self.fold, Fold):
            raise KernelValueError("FoldEvidence.fold must be a Fold")
        if not isinstance(self.selection, FoldSelection):
            raise KernelValueError("FoldEvidence.selection must be a FoldSelection")
        if self.selection.fold_index != self.fold.fold_index:
            raise KernelValueError("FoldEvidence: 選定記録と fold の番号が違う")
        if not isinstance(self.train_units, tuple) or not all(
            isinstance(item, TrainUnitEvaluation) for item in self.train_units
        ):
            raise KernelValueError("FoldEvidence.train_units must be TrainUnitEvaluation")
        selected = self.selection.selected_trial_index
        if selected is None:
            if self.validation is not None:
                raise KernelValueError("候補なしの fold は検証区間の単位を持たない（D09 §10.4）")
        elif not isinstance(self.validation, ValidationUnitEvaluation):
            raise KernelValueError("選んだ試行のある fold は検証区間の単位の結果を持つ（D09 §7.7）")
        elif self.validation.trial_index != selected:
            raise KernelValueError("検証区間の単位は選んだ試行のもの（D09 §7.7）")


# --- 内部の小道具 -------------------------------------------------------------


def _ordinal(member: Enum) -> int:
    return list(type(member)).index(member)


def _compare(observed: Decimal, comparator: Comparator, threshold: Decimal) -> bool:
    """`observed comparator threshold` が成り立つか（D09 §7.2・§7.3。`Decimal` で比べる）。"""
    if comparator is Comparator.GE:
        return observed >= threshold
    if comparator is Comparator.GT:
        return observed > threshold
    if comparator is Comparator.LE:
        return observed <= threshold
    return observed < threshold


def _numeric(value: MetricValue) -> Decimal | None:
    """比較に使う数値（D09 §7.1）。比率はその値、金額は金額の数値。値なしは `None`。"""
    if isinstance(value, RatioValue):
        return value.ratio
    if isinstance(value, AmountValue):
        return value.amount.amount
    if isinstance(value, Unavailable):
        return None
    # E1 で比率か金額の指標しか条件と選定に書けないので、ここには来ない。
    raise KernelValueError(f"the metric kind {value.kind.value} cannot be compared (D09 §7.1)")


def _trade_count(unit: _UnitEvaluation) -> int:
    value = unit.value_of(MetricId.TRADE_COUNT)
    if not isinstance(value, CountValue):
        raise KernelValueError(f"unit {unit.trial_index} の TRADE_COUNT が件数でない（D07 §5.2）")
    return value.count


def _unavailable_reason(value: MetricValue) -> MetricUnavailableReason | None:
    return value.reason if isinstance(value, Unavailable) else None


def _selection_metrics(rule: SelectionRule) -> tuple[MetricId, ...]:
    """選定の `metric` と足切りの指標（書いた順。重複なし）。"""
    return tuple(dict.fromkeys((rule.metric, *(item.metric for item in rule.eligibility))))


def _judged_metrics(rule: ValidationRule) -> tuple[MetricId, ...]:
    """判定に使う指標（最低条件と集約条件に現れる指標。書いた順。重複なし。D09 §7.3）。"""
    return tuple(
        dict.fromkeys(
            (*(item.metric for item in rule.fold_floors), *(item.metric for item in rule.aggregate))
        )
    )


def _median(values: Sequence[Decimal]) -> Decimal:
    """中央値（D09 §7.3 の `MEDIAN`）。偶数個なら中央の2つの平均をカーネル精度で1回割る。"""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    with localcontext(kernel_context()):
        return (ordered[middle - 1] + ordered[middle]) / decimal_from_int(2)


def _whole_seconds(interval: Interval) -> int:
    seconds, remainder = divmod(interval.duration, _ONE_SECOND)
    if remainder:
        raise KernelValueError(
            f"fold の区間 {interval} の長さが秒の整数でない（D09 §6.1。長さは秒数で持つ）"
        )
    return int(seconds)


# --- 選定（D09 §7.2・§7.4） -----------------------------------------------------


def candidate_status(rule: SelectionRule, unit: TrainUnitEvaluation) -> CandidateStatus:
    """選定の入力での試行の区分（D09 §7.4 の表。足切りの前の区分）。

    足切り（`eligibility`）で外れる `EXCLUDED_INELIGIBLE` はここでは出さず、`select_trial` が
    `CANDIDATE` の試行に足切りを当てて決める（D09 §7.2 の手順2）。
    """
    if not isinstance(rule, SelectionRule):
        raise KernelValueError("candidate_status requires a SelectionRule")
    if not isinstance(unit, TrainUnitEvaluation):
        raise KernelValueError("candidate_status requires a TrainUnitEvaluation (D09 §7.2)")
    if unit.status is TrialStatus.FAILED:
        return CandidateStatus.EXCLUDED_TRIAL_FAILED
    if (
        unit.status is not TrialStatus.COMPLETED
        or unit.run_status is not RunStatus.COMPLETED
        or unit.evaluation_status is not EvaluationStatus.COMPLETED
    ):
        return CandidateStatus.EXCLUDED_NOT_COMPLETED
    reasons = [_unavailable_reason(unit.value_of(metric)) for metric in _selection_metrics(rule)]
    if MetricUnavailableReason.INPUT_NOT_AVAILABLE in reasons:
        return CandidateStatus.EXCLUDED_NOT_COMPLETED
    if not unit.post_run_checks_passed:
        return CandidateStatus.EXCLUDED_POST_RUN_CHECK
    if any(reason is not None for reason in reasons):
        return CandidateStatus.EXCLUDED_METRIC_UNAVAILABLE
    return CandidateStatus.CANDIDATE


def select_trial(
    fold_index: int, rule: SelectionRule, units: Sequence[TrainUnitEvaluation]
) -> FoldSelection:
    """fold の選定（D09 §7.2）。入力はその fold の**選定区間の単位の結果だけ**である。

    1. 全試行を候補の区分に分ける（D09 §7.4）。
    2. 候補のうち足切りの条件をすべて満たすものだけを残す（満たさないものは
       `EXCLUDED_INELIGIBLE`）。
    3. 残った候補の `metric` の値が最大（`MAXIMIZE`）か最小（`MINIMIZE`）のものを選ぶ。
    4. 同点（`Decimal` として等しい）は `trial_index` の最も小さいものを選ぶ。

    入力の並び順によらず同じ結果になる（`trial_index` の昇順に並べ直してから当てる）。
    """
    _require_index(fold_index, "select_trial.fold_index")
    if not isinstance(rule, SelectionRule):
        raise KernelValueError("select_trial requires a SelectionRule")
    items = tuple(units)
    if not all(isinstance(item, TrainUnitEvaluation) for item in items):
        raise KernelValueError(
            "select_trial は選定区間の単位の結果（TrainUnitEvaluation）だけを受け取る"
            "（D09 §7.2。選定は train 内で閉じる）"
        )
    ordered = sorted(items, key=lambda item: item.trial_index)
    indices = [item.trial_index for item in ordered]
    if len(set(indices)) != len(indices):
        raise KernelValueError(f"select_trial: trial_index が重複している: {indices}")

    statuses: dict[int, CandidateStatus] = {}
    best: tuple[Decimal, TrainUnitEvaluation] | None = None
    for unit in ordered:
        status = candidate_status(rule, unit)
        if status is CandidateStatus.CANDIDATE:
            if not all(
                _compare(_present(unit, item.metric), item.comparator, item.threshold)
                for item in rule.eligibility
            ):
                status = CandidateStatus.EXCLUDED_INELIGIBLE
            else:
                value = _present(unit, rule.metric)
                better = (
                    best is None
                    or (rule.direction is SelectionDirection.MAXIMIZE and value > best[0])
                    or (rule.direction is SelectionDirection.MINIMIZE and value < best[0])
                )
                if better:
                    # 昇順に見ているので、同点は先に見た（番号の小さい）試行が残る。
                    best = (value, unit)
        statuses[unit.trial_index] = status

    inputs = tuple(
        (unit.trial_index, unit.run_evaluation_id, statuses[unit.trial_index]) for unit in ordered
    )
    if best is None:
        return FoldSelection(
            fold_index=fold_index,
            selected_trial_index=None,
            selected_value=None,
            selected_train_trade_count=None,
            inputs=inputs,
        )
    value, chosen = best
    return FoldSelection(
        fold_index=fold_index,
        selected_trial_index=chosen.trial_index,
        selected_value=value,
        selected_train_trade_count=_trade_count(chosen),
        inputs=inputs,
    )


def _present(unit: _UnitEvaluation, metric: MetricId) -> Decimal:
    """候補の試行の指標の数値（候補は選定の指標と足切りの指標に値なしを持たない）。"""
    value = _numeric(unit.value_of(metric))
    if value is None:
        raise KernelValueError(f"unit {unit.trial_index} の {metric.value} が値なし")
    return value


# --- 頻度区分（D09 §7.8） ---------------------------------------------------------


def trades_per_365d(train_trade_count: int, train_seconds: int) -> Decimal:
    """365 日あたりの取引頻度 `r = (件数 × 31536000) ÷ 秒数`（D09 §7.8。カーネル精度で1回割る）。"""
    _require_count(train_trade_count, "train_trade_count")
    _require_count(train_seconds, "train_seconds")
    if train_seconds == 0:
        raise KernelValueError("train_seconds must be > 0")
    with localcontext(kernel_context()):
        return decimal_from_int(train_trade_count * SECONDS_PER_365_DAYS) / decimal_from_int(
            train_seconds
        )


def assess_frequency(
    selections: Sequence[FoldSelection], folds: Sequence[Fold], rule: SufficiencyRule
) -> FrequencyAssessment | None:
    """頻度区分を選定記録と fold の区間だけから決める（D09 §7.8。Q15 決定）。

    選んだ試行のある fold について、選定区間の取引件数と選定区間の長さを合計し、頻度 `r` が
    `r >= min_train_trades_per_365d` を満たす最初の区分にする。選んだ試行のある fold が
    1つも無ければ `None`。**検証区間の結果は型として受け取らない**。
    """
    if not isinstance(rule, SufficiencyRule):
        raise KernelValueError("assess_frequency requires a SufficiencyRule")
    chosen = tuple(selections)
    if not all(isinstance(item, FoldSelection) for item in chosen):
        raise KernelValueError(
            "assess_frequency は選定記録（FoldSelection）だけを受け取る（D09 §7.8）"
        )
    fold_by_index: dict[int, Fold] = {}
    for fold in folds:
        if not isinstance(fold, Fold):
            raise KernelValueError("assess_frequency requires Fold values")
        if fold.fold_index in fold_by_index:
            raise KernelValueError(f"assess_frequency: fold {fold.fold_index} が重複している")
        fold_by_index[fold.fold_index] = fold
    chosen_indices = [item.fold_index for item in chosen]
    if len(set(chosen_indices)) != len(chosen_indices) or set(chosen_indices) != set(fold_by_index):
        raise KernelValueError("assess_frequency: 選定記録と fold の番号の集合が違う")
    trade_count = 0
    seconds = 0
    for selection in chosen:
        if selection.selected_train_trade_count is None:
            continue
        trade_count += selection.selected_train_trade_count
        seconds += _whole_seconds(fold_by_index[selection.fold_index].train)
    if seconds == 0:
        return None
    rate = trades_per_365d(trade_count, seconds)
    for item in rule.classes:
        if rate >= item.min_train_trades_per_365d:
            return FrequencyAssessment(
                class_name=item.name, train_trade_count=trade_count, train_seconds=seconds
            )
    # 最後の区分の下限は 0（検査 E4）なので、ここには来ない。
    raise KernelValueError("no frequency class matched; the last lower bound must be 0")


def frequency_class_of(assessment: FrequencyAssessment, rule: SufficiencyRule) -> FrequencyClass:
    """頻度区分の結果から、その区分の証拠の要件を引く（D09 §7.8）。"""
    for item in rule.classes:
        if item.name == assessment.class_name:
            return item
    raise KernelValueError(f"the frequency class {assessment.class_name} is not in the rule")


# --- 判定（D09 §7.3） -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FoldJudgement:
    verdict: FoldVerdict
    conditions: tuple[ConditionResult, ...]
    shortfalls: tuple[SufficiencyShortfall, ...]
    #: 検証結果のある fold の検証区間の取引件数（手順1・2 で決まった fold は `None`）。
    validation_trades: int | None


def _floor_results(
    fold_index: int, rule: ValidationRule, unit: ValidationUnitEvaluation
) -> tuple[ConditionResult, ...]:
    results: list[ConditionResult] = []
    for condition in rule.fold_floors:
        value = unit.value_of(condition.metric)
        observed = _numeric(value)
        if observed is None:
            outcome = ConditionOutcome.UNCOMPUTABLE
        elif _compare(observed, condition.comparator, condition.threshold):
            outcome = ConditionOutcome.MET
        else:
            outcome = ConditionOutcome.NOT_MET
        results.append(
            ConditionResult(
                scope=ConditionScope.FOLD_FLOOR,
                fold_index=fold_index,
                metric=condition.metric,
                statistic=None,
                comparator=condition.comparator,
                threshold=condition.threshold,
                observed=observed,
                outcome=outcome,
                unavailable_reason=_unavailable_reason(value),
            )
        )
    return tuple(results)


def _uncomputable_shortfalls(
    fold_index: int, rule: ValidationRule, unit: ValidationUnitEvaluation
) -> tuple[SufficiencyShortfall, ...]:
    """判定に使う指標のうち観測不足で値なしのもの（指標と理由ごとに1件。D09 §7.3 の手順3・5）。"""
    found: list[SufficiencyShortfall] = []
    for metric in _judged_metrics(rule):
        reason = _unavailable_reason(unit.value_of(metric))
        if reason in OBSERVATION_SHORTFALL_REASONS:
            found.append(
                SufficiencyShortfall(
                    kind=SufficiencyShortfallKind.METRIC_UNCOMPUTABLE,
                    fold_index=fold_index,
                    trial_index=unit.trial_index,
                    metric=metric,
                    reason=reason,
                    required=None,
                    observed=None,
                )
            )
    return tuple(found)


def _no_candidate_shortfalls(
    evidence: FoldEvidence, rule: SelectionRule
) -> tuple[SufficiencyShortfall, ...]:
    """候補なしの fold で観測不足により除外された試行ごと・指標ごと・理由ごとに1件（D09 §7.8）。"""
    status_of = {entry[0]: entry[2] for entry in evidence.selection.inputs}
    found: list[SufficiencyShortfall] = []
    for unit in sorted(evidence.train_units, key=lambda item: item.trial_index):
        if status_of[unit.trial_index] is not CandidateStatus.EXCLUDED_METRIC_UNAVAILABLE:
            continue
        for metric in _selection_metrics(rule):
            reason = _unavailable_reason(unit.value_of(metric))
            if reason in OBSERVATION_SHORTFALL_REASONS:
                found.append(
                    SufficiencyShortfall(
                        kind=SufficiencyShortfallKind.NO_CANDIDATE_METRIC_UNAVAILABLE,
                        fold_index=evidence.fold.fold_index,
                        trial_index=unit.trial_index,
                        metric=metric,
                        reason=reason,
                        required=None,
                        observed=None,
                    )
                )
    return tuple(found)


def _judge_fold(
    evidence: FoldEvidence,
    standard: EvaluationStandard,
    requirement: FrequencyClass | None,
) -> _FoldJudgement:
    """fold の判定（D09 §7.3 の fold の判定の手順1〜6。最初に当てはまるもの）。"""
    fold_index = evidence.fold.fold_index
    selection = evidence.selection
    rule = standard.validation
    # 手順1: 候補なし。
    if selection.selected_trial_index is None:
        statuses = {entry[2] for entry in selection.inputs}
        if statuses & {
            CandidateStatus.EXCLUDED_NOT_COMPLETED,
            CandidateStatus.EXCLUDED_POST_RUN_CHECK,
        }:
            return _FoldJudgement(FoldVerdict.INCOMPLETE, (), (), None)
        if CandidateStatus.EXCLUDED_METRIC_UNAVAILABLE in statuses:
            return _FoldJudgement(
                FoldVerdict.INSUFFICIENT_EVIDENCE,
                (),
                _no_candidate_shortfalls(evidence, standard.selection),
                None,
            )
        if CandidateStatus.EXCLUDED_INELIGIBLE in statuses:
            return _FoldJudgement(FoldVerdict.NO_ELIGIBLE_TRIAL, (), (), None)
        return _FoldJudgement(FoldVerdict.INCOMPLETE, (), (), None)
    # 手順2: 検証結果のある fold でない、または判定に使う指標が入力の無いことによる値なし。
    unit = evidence.validation
    if unit is None:  # FoldEvidence が保証する
        raise KernelValueError("a fold with a selected trial needs its validation unit")
    if not unit.has_valid_result or any(
        _unavailable_reason(unit.value_of(metric)) is MetricUnavailableReason.INPUT_NOT_AVAILABLE
        for metric in _judged_metrics(rule)
    ):
        return _FoldJudgement(FoldVerdict.INCOMPLETE, (), (), None)
    if requirement is None:
        raise KernelValueError("選んだ試行のある fold があるのに頻度区分が無い（D09 §7.8）")
    floors = _floor_results(fold_index, rule, unit)
    trades = _trade_count(unit)
    uncomputable = _uncomputable_shortfalls(fold_index, rule, unit)
    breached = any(item.outcome is ConditionOutcome.NOT_MET for item in floors)
    # 手順3: 取引が少ない fold（Q16 決定。最低条件を割っても証拠不足。結果は NOT_MET のまま残す）。
    if trades < requirement.min_validation_trades_per_fold:
        shortfall = SufficiencyShortfall(
            kind=(
                SufficiencyShortfallKind.FLOOR_NOT_MET_TRADES_BELOW
                if breached
                else SufficiencyShortfallKind.FOLD_TRADES_BELOW
            ),
            fold_index=fold_index,
            trial_index=unit.trial_index,
            metric=None,
            reason=None,
            required=requirement.min_validation_trades_per_fold,
            observed=trades,
        )
        return _FoldJudgement(
            FoldVerdict.INSUFFICIENT_EVIDENCE, floors, (shortfall, *uncomputable), trades
        )
    # 手順4: 最低条件を割った（取引件数が要件以上の fold だけがここに来る）。
    if breached:
        return _FoldJudgement(FoldVerdict.FLOOR_BREACHED, floors, (), trades)
    # 手順5: 判定に使う指標が観測不足で値なし。
    if uncomputable:
        return _FoldJudgement(FoldVerdict.INSUFFICIENT_EVIDENCE, floors, uncomputable, trades)
    # 手順6。
    return _FoldJudgement(FoldVerdict.FLOORS_MET, floors, (), trades)


def build_search_outcome(
    standard: EvaluationStandard,
    folds: Sequence[FoldEvidence],
    trial_statuses: Iterable[TrialStatus],
    ledger_execution: int,
) -> SearchOutcome:
    """全 fold が終端した後に、頻度区分・fold の判定・実験の判定を決める（D09 §7.3・§7.8）。

    実験の判定は次の順で最初に当てはまるもの（D09 §7.3）:

    1. `FLOOR_BREACHED` か `NO_ELIGIBLE_TRIAL` の fold がある: `BELOW_STANDARD`。
    2. `INCOMPLETE` の fold がある: `INCOMPLETE`。
    3. `INSUFFICIENT_EVIDENCE` の fold がある、または検証結果のある fold の検証区間の取引件数の
       合計が `min_validation_trades_total` 未満: `INSUFFICIENT_EVIDENCE`。
    4. 集約条件を当てる。1つでも `NOT_MET` なら `BELOW_STANDARD`。すべて `MET` なら、用途が
       `STANDARD` なら `MEETS_STANDARD`、`MECHANISM_CHECK` なら `MET_IN_MECHANISM_CHECK`。
       **用途を見るのはここだけ**（Q33 決定）。

    各 fold の選定記録は、渡された選定区間の結果から `select_trial` で作り直した値と一致する
    ことを確かめる（一致しなければ構造エラー）。`trial_statuses` は全単位の状態（D09 §10.4）、
    `ledger_execution` は試行台帳の実行番号で、呼び出し側が渡す（採番は D09 の後続版。§19 の13）。
    """
    if not isinstance(standard, EvaluationStandard):
        raise KernelValueError("build_search_outcome requires an EvaluationStandard")
    items = tuple(folds)
    if not all(isinstance(item, FoldEvidence) for item in items):
        raise KernelValueError("build_search_outcome requires FoldEvidence values")
    evidence = tuple(sorted(items, key=lambda item: item.fold.fold_index))
    if not evidence or [item.fold.fold_index for item in evidence] != list(range(len(evidence))):
        raise KernelValueError("build_search_outcome: fold は 0 からの連番で1つ以上")
    for item in evidence:
        recomputed = select_trial(item.fold.fold_index, standard.selection, item.train_units)
        if recomputed != item.selection:
            raise KernelValueError(
                f"fold {item.fold.fold_index} の選定記録が、選定区間の結果から作り直した値と違う"
                "（D09 §7.2・§7.5）"
            )
    selections = tuple(item.selection for item in evidence)
    frequency = assess_frequency(
        selections, tuple(item.fold for item in evidence), standard.sufficiency
    )
    requirement = None if frequency is None else frequency_class_of(frequency, standard.sufficiency)
    judgements = [_judge_fold(item, standard, requirement) for item in evidence]
    verdicts = [item.verdict for item in judgements]
    conditions: list[ConditionResult] = [c for item in judgements for c in item.conditions]
    shortfalls: list[SufficiencyShortfall] = [s for item in judgements for s in item.shortfalls]

    verdict: SearchVerdict
    if FoldVerdict.FLOOR_BREACHED in verdicts or FoldVerdict.NO_ELIGIBLE_TRIAL in verdicts:
        verdict = SearchVerdict.BELOW_STANDARD
    elif FoldVerdict.INCOMPLETE in verdicts:
        verdict = SearchVerdict.INCOMPLETE
    else:
        total_short = False
        if requirement is not None:
            total = sum(item.validation_trades or 0 for item in judgements)
            if total < requirement.min_validation_trades_total:
                total_short = True
                shortfalls.append(
                    SufficiencyShortfall(
                        kind=SufficiencyShortfallKind.TOTAL_TRADES_BELOW,
                        fold_index=None,
                        trial_index=None,
                        metric=None,
                        reason=None,
                        required=requirement.min_validation_trades_total,
                        observed=total,
                    )
                )
        if FoldVerdict.INSUFFICIENT_EVIDENCE in verdicts or total_short:
            verdict = SearchVerdict.INSUFFICIENT_EVIDENCE
        else:
            verdict = _aggregate_verdict(standard, evidence, conditions)

    return SearchOutcome(
        selections=selections,
        fold_verdicts=tuple(
            (item.fold.fold_index, judgement.verdict)
            for item, judgement in zip(evidence, judgements, strict=True)
        ),
        verdict=verdict,
        frequency=frequency,
        condition_results=tuple(conditions),
        shortfalls=tuple(sorted(shortfalls, key=lambda item: item.key)),
        trial_counts=count_trial_statuses(trial_statuses),
        ledger_execution=ledger_execution,
        purpose=standard.purpose,
    )


def _aggregate_verdict(
    standard: EvaluationStandard,
    evidence: Sequence[FoldEvidence],
    conditions: list[ConditionResult],
) -> SearchVerdict:
    """実験の判定の手順4（D09 §7.3）。この時点で全 fold が `FLOORS_MET`。"""
    all_met = True
    for condition in standard.validation.aggregate:
        values: list[Decimal] = []
        for item in evidence:
            if item.validation is None:  # 全 fold が FLOORS_MET なので来ない
                raise KernelValueError("every fold must have a validation unit at step 4")
            values.append(_present(item.validation, condition.metric))
        median = _median(values)
        met = _compare(median, condition.comparator, condition.threshold)
        all_met = all_met and met
        conditions.append(
            ConditionResult(
                scope=ConditionScope.AGGREGATE,
                fold_index=None,
                metric=condition.metric,
                statistic=condition.statistic,
                comparator=condition.comparator,
                threshold=condition.threshold,
                observed=median,
                outcome=ConditionOutcome.MET if met else ConditionOutcome.NOT_MET,
                unavailable_reason=None,
            )
        )
    if not all_met:
        return SearchVerdict.BELOW_STANDARD
    # 用途を見るのはこの1か所だけ（D09 §7.3 の手順4。Q33 決定）。
    if standard.purpose is StandardPurpose.STANDARD:
        return SearchVerdict.MEETS_STANDARD
    return SearchVerdict.MET_IN_MECHANISM_CHECK


# --- 表示用の導出値（D09 §11.5 の順3。指標ではない） ---------------------------------


def longest_idle_period(
    interval: Interval, holdings: Sequence[tuple[UtcTime, UtcTime | None]]
) -> timedelta:
    """最長の無取引期間（建玉を1つも持っていなかった最長の連続期間。D09 §11.5 の注記）。

    `interval` の中の**保有区間の和集合の補集合**のうち最長の区間の長さ。保有区間は
    `(始まり, 終わり)` で、未決済の建玉は終わりを `None` として区間の終わりまでとする。
    区間の外にはみ出す部分は切り落とす。建玉が0件なら区間の長さそのもの。判定に使わない
    表示のための導出値であり、D07 の指標を作り直さない。
    """
    if not isinstance(interval, Interval):
        raise KernelValueError("longest_idle_period requires an Interval")
    spans: list[tuple[UtcTime, UtcTime]] = []
    for entry in holdings:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise KernelValueError("holdings must be (start, end | None) pairs")
        start, end = entry
        if not isinstance(start, UtcTime) or not (end is None or isinstance(end, UtcTime)):
            raise KernelValueError("holdings must be (UtcTime, UtcTime | None) pairs")
        stop = interval.end if end is None else end
        if stop < start:
            raise KernelValueError(f"a holding ends before it starts: [{start}, {stop})")
        clipped_start = max(start, interval.start)
        clipped_stop = min(stop, interval.end)
        if clipped_start < clipped_stop:
            spans.append((clipped_start, clipped_stop))
    spans.sort(key=lambda span: (span[0], span[1]))
    longest = timedelta(0)
    cursor = interval.start
    for start, stop in spans:
        if start > cursor:
            longest = max(longest, start - cursor)
        cursor = max(cursor, stop)
    return max(longest, interval.end - cursor)
