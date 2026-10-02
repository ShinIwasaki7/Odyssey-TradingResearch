"""選定・判定の純粋関数のテストが使う人工の単位の結果（D09 §7.2〜§7.4・§7.8。段階5 実装 PR 3）。

単位の結果（`TrainUnitEvaluation` / `ValidationUnitEvaluation`）と、評価基準・fold を少ない
記述で組み立てる。指標の値は人工の値であり、実データや戦略の成績ではない。

- `metric_rows(overrides)`: 指標集合 v2 の19件の行。既定は全指標が値を持ち、取引件数は 10。
  `overrides` は指標ごとに、比率・金額は `Decimal`、取引件数は `int`、値なしは
  `MetricUnavailableReason` で上書きする。
- `train_unit(...)` / `validation_unit(...)`: 試行済み・正常完走・評価 `COMPLETED`・事後検査合格の
  単位を既定にし、引数で状態を変える。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import timedelta
from decimal import Decimal

from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.money import CurrencyCode, Money, PriceOffset
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import (
    METRIC_KINDS,
    AmountValue,
    CountValue,
    DurationValue,
    MetricId,
    MetricKind,
    MetricRecord,
    MetricUnavailableReason,
    MetricValue,
    PriceOffsetValue,
    RatioValue,
    Unavailable,
)
from odyssey_fx.evaluation.domain.search import (
    AggregateCondition,
    Comparator,
    EvaluationStandard,
    FoldEvidence,
    FoldStatistic,
    FrequencyClass,
    MetricCondition,
    SelectionDirection,
    SelectionRule,
    StandardPurpose,
    SufficiencyRule,
    TrainUnitEvaluation,
    TrialStatus,
    ValidationRule,
    ValidationUnitEvaluation,
    select_trial,
)
from odyssey_fx.evaluation.domain.splits import Fold, SplitStandard, SplitWindow
from odyssey_fx.evaluation.domain.status import EvaluationStatus

JPY = CurrencyCode("JPY")
DAY = 86_400
_START = UtcTime.parse("2018-01-07T22:00:00Z")

Override = Decimal | int | MetricUnavailableReason


def digest_for(seed: int) -> ContentDigest:
    """人工の評価の識別子のダイジェスト（`seed` ごとに別の値）。"""
    return ContentDigest(algorithm="sha256", hex=f"{seed:064x}")


def _value(metric: MetricId, override: Override | None) -> MetricValue:
    kind = METRIC_KINDS[metric]
    if isinstance(override, MetricUnavailableReason):
        return Unavailable(kind=kind, reason=override)
    if kind is MetricKind.COUNT:
        return CountValue(10 if override is None else int(override))
    if kind is MetricKind.RATIO:
        return RatioValue(Decimal("0.1") if override is None else Decimal(override))
    if kind is MetricKind.AMOUNT:
        return AmountValue(Money(Decimal("1000") if override is None else Decimal(override), JPY))
    if kind is MetricKind.DURATION:
        return DurationValue(timedelta(hours=1))
    return PriceOffsetValue(PriceOffset(Decimal("0.01")))


def metric_rows(overrides: Mapping[MetricId, Override] | None = None) -> tuple[MetricRecord, ...]:
    """指標集合 v2 の19件の行（宣言順）。"""
    given = dict(overrides or {})
    return tuple(
        MetricRecord(metric_id=item, value=_value(item, given.get(item))) for item in MetricId
    )


def _unit_fields(
    trial_index: int,
    *,
    status: TrialStatus,
    run_status: RunStatus,
    evaluation_status: EvaluationStatus,
    post_run_checks_passed: bool,
    metrics: Mapping[MetricId, Override] | None,
    seed: int,
) -> dict[str, object]:
    if status is not TrialStatus.COMPLETED:
        return {
            "fold_index": 0,
            "trial_index": trial_index,
            "status": status,
            "run_status": None,
            "run_evaluation_id": None,
            "evaluation_status": None,
            "post_run_checks_passed": False,
            "metrics": (),
        }
    return {
        "fold_index": 0,
        "trial_index": trial_index,
        "status": status,
        "run_status": run_status,
        "run_evaluation_id": digest_for(seed),
        "evaluation_status": evaluation_status,
        "post_run_checks_passed": post_run_checks_passed,
        "metrics": metric_rows(metrics) if evaluation_status is EvaluationStatus.COMPLETED else (),
    }


def train_unit(
    trial_index: int,
    metrics: Mapping[MetricId, Override] | None = None,
    *,
    status: TrialStatus = TrialStatus.COMPLETED,
    run_status: RunStatus = RunStatus.COMPLETED,
    evaluation_status: EvaluationStatus = EvaluationStatus.COMPLETED,
    post_run_checks_passed: bool = True,
) -> TrainUnitEvaluation:
    """選定区間の単位の人工の結果。"""
    return TrainUnitEvaluation(
        **_unit_fields(  # type: ignore[arg-type]
            trial_index,
            status=status,
            run_status=run_status,
            evaluation_status=evaluation_status,
            post_run_checks_passed=post_run_checks_passed,
            metrics=metrics,
            seed=1000 + trial_index,
        )
    )


def validation_unit(
    trial_index: int,
    metrics: Mapping[MetricId, Override] | None = None,
    *,
    status: TrialStatus = TrialStatus.COMPLETED,
    run_status: RunStatus = RunStatus.COMPLETED,
    evaluation_status: EvaluationStatus = EvaluationStatus.COMPLETED,
    post_run_checks_passed: bool = True,
) -> ValidationUnitEvaluation:
    """検証区間の単位の人工の結果。"""
    return ValidationUnitEvaluation(
        **_unit_fields(  # type: ignore[arg-type]
            trial_index,
            status=status,
            run_status=run_status,
            evaluation_status=evaluation_status,
            post_run_checks_passed=post_run_checks_passed,
            metrics=metrics,
            seed=2000 + trial_index,
        )
    )


def make_fold(fold_index: int, *, train_days: int = 365, validation_days: int = 73) -> Fold:
    """人工の fold（選定区間と検証区間が隣接し、fold ごとに検証区間の長さだけ進む）。"""
    train_start = _START + timedelta(days=fold_index * validation_days)
    train_end = train_start + timedelta(days=train_days)
    return Fold(
        fold_index=fold_index,
        train=Interval(start=train_start, end=train_end),
        validation=Interval(start=train_end, end=train_end + timedelta(days=validation_days)),
    )


def standard(
    *,
    purpose: StandardPurpose = StandardPurpose.STANDARD,
    selection_metric: MetricId = MetricId.NET_RETURN_RATE,
    direction: SelectionDirection = SelectionDirection.MAXIMIZE,
    eligibility: Sequence[MetricCondition] = (),
    floors: Sequence[MetricCondition] | None = None,
    aggregate: Sequence[AggregateCondition] | None = None,
    classes: Sequence[FrequencyClass] | None = None,
) -> EvaluationStandard:
    """人工の評価基準。既定の最低条件は「含み込みの最大ドローダウン率 0.2 以下」、集約条件は
    「純収益率の中央値 0 より大」、頻度区分は1つ（fold ごと 5 件、合計 20 件）。"""
    return EvaluationStandard(
        purpose=purpose,
        split=SplitStandard(
            range=Interval(start=_START, end=_START + timedelta(days=730)),
            train_seconds=365 * DAY,
            validation_seconds=73 * DAY,
            window=SplitWindow.ROLLING,
            purge_seconds=0,
            min_folds=1,
        ),
        selection=SelectionRule(
            metric=selection_metric, direction=direction, eligibility=tuple(eligibility)
        ),
        validation=ValidationRule(
            fold_floors=tuple(
                floors
                if floors is not None
                else (
                    MetricCondition(MetricId.MAX_DRAWDOWN_MTM_RATE, Comparator.LE, Decimal("0.2")),
                )
            ),
            aggregate=tuple(
                aggregate
                if aggregate is not None
                else (
                    AggregateCondition(
                        MetricId.NET_RETURN_RATE, FoldStatistic.MEDIAN, Comparator.GT, Decimal("0")
                    ),
                )
            ),
        ),
        sufficiency=SufficiencyRule(
            classes=tuple(
                classes
                if classes is not None
                else (
                    FrequencyClass(
                        name="ALL",
                        min_train_trades_per_365d=Decimal("0"),
                        min_validation_trades_per_fold=5,
                        min_validation_trades_total=20,
                    ),
                )
            )
        ),
    )


def evidence(
    rule: EvaluationStandard,
    fold: Fold,
    train_units: Sequence[TrainUnitEvaluation],
    validation: ValidationUnitEvaluation | None,
) -> FoldEvidence:
    """選定区間の結果から選定記録を作り、fold の判定の入力にまとめる。"""
    trains = in_fold(train_units, fold.fold_index)
    return FoldEvidence(
        fold=fold,
        selection=select_trial(fold.fold_index, rule.selection, trains),
        train_units=trains,
        validation=None
        if validation is None
        else dataclasses.replace(validation, fold_index=fold.fold_index),
    )


def in_fold(
    units: Sequence[TrainUnitEvaluation], fold_index: int
) -> tuple[TrainUnitEvaluation, ...]:
    """選定区間の単位の結果を fold `fold_index` のものとして付け直す（既定は fold 0）。"""
    return tuple(dataclasses.replace(unit, fold_index=fold_index) for unit in units)
