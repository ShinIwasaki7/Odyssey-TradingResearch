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

選定・判定・頻度区分の関数（D09 §7.2〜§7.5・§7.8）は後続の実装 PR が足す。閾値は `Decimal` で
持つ（D09 §7.1。`float` を使わない。ADR-0012）。

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
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

from odyssey_fx.common.canonical import encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import CompiledStrategyRef, ConfigDigest
from odyssey_fx.evaluation.domain.metrics import METRIC_KINDS, MetricId, MetricKind
from odyssey_fx.evaluation.domain.splits import SplitStandard
from odyssey_fx.strategy.compiler.compiled import CompileError
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    FloatValue,
    IntValue,
    ParameterValue,
    StrValue,
)

__all__ = [
    "REFERENCE_METRICS",
    "SELECTABLE_METRIC_KINDS",
    "AggregateCondition",
    "Comparator",
    "EvaluationStandard",
    "FoldStatistic",
    "FrequencyClass",
    "MetricCondition",
    "ParameterAssignment",
    "ParameterAxis",
    "SearchPlan",
    "SearchPlanKind",
    "SelectionDirection",
    "SelectionRule",
    "StandardPurpose",
    "SufficiencyRule",
    "TrialPhase",
    "TrialPlan",
    "TrialUnitKey",
    "ValidationRule",
    "compile_rejections_of",
    "enumerate_assignments",
    "is_selectable_metric",
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
