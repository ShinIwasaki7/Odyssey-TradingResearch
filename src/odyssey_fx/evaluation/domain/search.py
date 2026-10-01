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

選定・判定・頻度区分の関数（D09 §7.2〜§7.5・§7.8）は後続の実装 PR が足す（本モジュールは
型だけ）。閾値は `Decimal` で持つ（D09 §7.1。`float` を使わない。ADR-0012）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.evaluation.domain.metrics import METRIC_KINDS, MetricId, MetricKind
from odyssey_fx.evaluation.domain.splits import SplitStandard

__all__ = [
    "REFERENCE_METRICS",
    "SELECTABLE_METRIC_KINDS",
    "AggregateCondition",
    "Comparator",
    "EvaluationStandard",
    "FoldStatistic",
    "FrequencyClass",
    "MetricCondition",
    "SelectionDirection",
    "SelectionRule",
    "StandardPurpose",
    "SufficiencyRule",
    "ValidationRule",
    "is_selectable_metric",
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
