"""最終検証の段 2 (f): 選定と判定の再計算（D09 §9.10.2 の「選定と判定の再計算」。R4b）。

研究履歴の成果物（記録票・結末記録・試行記録・選定記録・経路の区間の記録と、run・評価の成果物）
だけから次を確かめ、最終検証に進める試行（最後の fold で選んだ試行。Q12。組ごと）を返す。
**run はしない**（保存済みの run の結果と判断履歴を評価し直すだけ。封印の partition を読まない）。

1. 環境と `RunId` の束縛: 各単位の試行記録の `run_id` が、記録票の予測 `ConfigDigest` と結末記録の
   環境（コード・lock・環境のダイジェスト）から計算した `RunId` と一致し、run manifest の
   `ConfigDigest` と環境のダイジェストが記録と一致する。経路の区間は、受け渡した残高から組み立て
   直した区間の `ConfigDigest` で同じことを確かめる。
2. 選定区間の評価を確かめて選定を計算し直し、選定記録と結末記録の選定に一致する。
3. 検証区間の評価と経路の区間から判定を計算し直し、結末記録の探索の項目に一致する。
4. 保存済みの run の結果を評価し直して結果のダイジェストを計算し直し、試行記録・区間の終端の
   記録の値と一致する（評価の成果物は書かない）。
5. 最終検証に進める試行の `compiled_ref` が記録票の試行の計画と一致する（記録票の本文から
   コンパイルし直した値。合成が `SegmentConfigs.compiled_ref` で渡す）。

食い違い・読めない成果物は理由の文（件数・識別子だけ。指標の値を含めない）を返す。呼び出し側
（`app.final_run`）が終了コード 7 `RECOMPUTATION_MISMATCH` にする。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.money import Money
from odyssey_fx.common.refs import CompiledStrategyRef, ConfigDigest, ContentDigest
from odyssey_fx.common.refs import run_id as run_id_of
from odyssey_fx.common.time import Interval
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, equity_series
from odyssey_fx.evaluation.application.manifest import RunEvaluationId
from odyssey_fx.evaluation.application.ports import ResultRepository, StoredEvaluation
from odyssey_fx.evaluation.application.run_experiment import segment_failure
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentManifest,
    ExperimentOutcome,
    ExperimentStatus,
)
from odyssey_fx.evaluation.domain.metrics import MetricRecord
from odyssey_fx.evaluation.domain.path import (
    PathEnd,
    PathSegmentEnd,
    PathSegmentRow,
    PathSegmentStart,
    PathSummary,
    path_metrics,
    plan_segments,
)
from odyssey_fx.evaluation.domain.search import (
    FoldEvidence,
    FoldSelection,
    TrainUnitEvaluation,
    TrialPhase,
    TrialRunRecord,
    TrialStatus,
    TrialUnitKey,
    ValidationUnitEvaluation,
    build_search_outcome,
    build_variant_outcome,
    select_trial,
)
from odyssey_fx.evaluation.domain.splits import SplitSpec
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant
from odyssey_fx.marketdata.domain.calendar import TradingCalendar

__all__ = [
    "RecordedResearch",
    "SegmentConfigs",
    "recompute_research",
]


@dataclass(frozen=True, slots=True)
class RecordedResearch:
    """組の研究の記録（実験の版のディレクトリから読んだもの）。"""

    runs: Mapping[TrialUnitKey, TrialRunRecord]
    selections: Mapping[int, FoldSelection]
    segment_starts: Mapping[int, PathSegmentStart]
    segment_ends: Mapping[int, PathSegmentEnd]


class SegmentConfigs(Protocol):
    """記録票の本文から組み立て直す値（合成が実装する。記録票の実験設定と試行の割当から）。"""

    def initial_balance(self, variant: Variant) -> Money:
        """組の経路の最初の残高（実験設定の初期残高）。"""
        ...

    def compiled_ref(self, trial_index: int) -> CompiledStrategyRef:
        """試行をコンパイルし直した `compiled_ref`。"""
        ...

    def segment_config_digest(
        self, variant: Variant, trial_index: int, interval: Interval, initial_balance: Money
    ) -> ConfigDigest:
        """経路の区間の run の `ConfigDigest`（`CLOSE_ALL`。D13 §4.5）。"""
        ...


class _Mismatch(Exception):
    """再計算の食い違い（理由の文）。"""


@dataclass(frozen=True, slots=True)
class _Evaluated:
    result: BacktestResult
    metrics: tuple[MetricRecord, ...]
    status: EvaluationStatus


class _Research:
    def __init__(
        self,
        *,
        manifest: ExperimentManifest,
        outcome: ExperimentOutcome,
        repository: ResultRepository,
        evaluator: EvaluateRun,
        calendar: TradingCalendar,
        segments: SegmentConfigs,
    ) -> None:
        self.manifest = manifest
        self.outcome = outcome
        self.repository = repository
        self.evaluator = evaluator
        self.calendar = calendar
        self.segments = segments
        self.environment = (outcome.code_digest, outcome.lock_digest, outcome.env_digest)
        self._cache: dict[RunId, _Evaluated] = {}

    def expected_run_id(self, config_digest: ConfigDigest) -> RunId:
        code, lock, env = self.environment
        return run_id_of(config_digest, code, lock, env)

    def evaluated(
        self,
        label: str,
        run_id: RunId,
        config_digest: ConfigDigest,
        run_evaluation_id: ContentDigest,
        result_digest: ContentDigest,
    ) -> _Evaluated:
        """保存済みの run を評価し直し、束縛と結果のダイジェストを確かめる（手順 1・4）。"""
        cached = self._cache.get(run_id)
        if cached is None:
            manifest = self.repository.read_manifest(run_id)
            if not isinstance(manifest, RunManifest):
                raise _Mismatch(f"{label}: the run manifest of {run_id} cannot be read")
            code, lock, env = self.environment
            if (
                manifest.config_digest != config_digest
                or manifest.code_digest != code
                or manifest.lock_digest != lock
                or manifest.env_digest != env
            ):
                raise _Mismatch(
                    f"{label}: the run manifest of {run_id} is not bound to the recorded"
                    " configuration and environment"
                )
            result = self.repository.read_result(run_id)
            if not isinstance(result, BacktestResult):
                raise _Mismatch(f"{label}: the result of {run_id} cannot be read")
            report = self.evaluator.evaluate(
                result, self.repository, self.manifest.metric_set_version, self.calendar
            )
            cached = _Evaluated(result=result, metrics=report.metrics, status=report.status)
            stored = self.repository.read_evaluation(run_id, RunEvaluationId(run_evaluation_id))
            if not isinstance(stored, StoredEvaluation):
                raise _Mismatch(f"{label}: the stored evaluation of {run_id} cannot be read")
            if (
                report.manifest.result_digest != result_digest
                or stored.result_digest != result_digest
                or report.status is not stored.status
            ):
                raise _Mismatch(
                    f"{label}: the result digest recomputed from {run_id} differs from the record"
                )
            self._cache[run_id] = cached
        return cached

    def unit(self, record: TrialRunRecord, config_digest: ConfigDigest) -> _Evaluated:
        unit = record.unit
        label = f"{unit.variant.value} fold {unit.fold_index} {unit.phase.value} trial"
        label += f" {unit.trial_index}"
        expected = self.expected_run_id(config_digest)
        if record.expected_run_id != expected or record.run_id != expected:
            raise _Mismatch(f"{label}: the run id is not bound to the recorded configuration")
        return self.evaluated(
            label, record.run_id, config_digest, record.run_evaluation_id, record.result_digest
        )


def _unit_fields(record: TrialRunRecord, evaluated: _Evaluated) -> dict[str, object]:
    return {
        "fold_index": record.unit.fold_index,
        "trial_index": record.unit.trial_index,
        "status": record.status,
        "run_status": record.run_status,
        "run_evaluation_id": record.run_evaluation_id,
        "evaluation_status": record.evaluation_status,
        "post_run_checks_passed": record.post_run_checks_passed,
        "metrics": evaluated.metrics
        if record.evaluation_status is EvaluationStatus.COMPLETED
        else (),
    }


def recompute_research(
    *,
    manifest: ExperimentManifest,
    outcome: ExperimentOutcome,
    recorded: Paired[RecordedResearch],
    repository: ResultRepository,
    evaluator: EvaluateRun,
    calendar: TradingCalendar,
    segments: SegmentConfigs,
) -> Paired[int] | str:
    """選定と判定を研究履歴の成果物から計算し直す（D09 §9.10.2 の段 2 (f)）。

    一致すれば最終検証に進める試行（組ごと）、食い違えば理由の文を返す。
    """
    try:
        return _recompute(
            _Research(
                manifest=manifest,
                outcome=outcome,
                repository=repository,
                evaluator=evaluator,
                calendar=calendar,
                segments=segments,
            ),
            recorded,
        )
    except _Mismatch as mismatch:
        return str(mismatch)
    except KernelValueError as exc:
        return f"the research artifacts cannot be checked: {type(exc).__name__}: {exc}"


def _recompute(research: _Research, recorded: Paired[RecordedResearch]) -> Paired[int]:
    manifest = research.manifest
    outcome = research.outcome
    search = outcome.search
    split = manifest.split
    standard = manifest.evaluation_standard
    if (
        outcome.status is not ExperimentStatus.COMPLETED
        or search is None
        or not isinstance(split, SplitSpec)
        or standard is None
    ):
        raise _Mismatch("the outcome is not a COMPLETED search outcome")
    outcomes = {}
    finals: dict[Variant, int] = {}
    for variant in VARIANTS:
        record = recorded.of(variant)
        evidence: list[FoldEvidence] = []
        selected: list[int | None] = []
        for fold in split.folds:
            train_units: list[TrainUnitEvaluation] = []
            for plan in manifest.trials:
                if plan.compiled_ref is None:
                    train_units.append(
                        TrainUnitEvaluation(
                            fold_index=fold.fold_index,
                            trial_index=plan.trial_index,
                            status=TrialStatus.FAILED,
                            run_status=None,
                            run_evaluation_id=None,
                            evaluation_status=None,
                            post_run_checks_passed=False,
                            metrics=(),
                        )
                    )
                    continue
                unit = TrialUnitKey(
                    variant=variant,
                    fold_index=fold.fold_index,
                    phase=TrialPhase.TRAIN,
                    trial_index=plan.trial_index,
                )
                run = record.runs.get(unit)
                if run is None:
                    raise _Mismatch(f"the trial record of {unit} is missing")
                done = research.unit(run, plan.expected_config_digest(unit))
                train_units.append(TrainUnitEvaluation(**_unit_fields(run, done)))  # type: ignore[arg-type]
            selection = select_trial(fold.fold_index, standard.selection, train_units)
            if record.selections.get(fold.fold_index) != selection:
                raise _Mismatch(
                    f"the {variant.value} selection of fold {fold.fold_index} recomputed from the"
                    " selection intervals differs from the selection record"
                )
            validation: ValidationUnitEvaluation | None = None
            chosen = selection.selected_trial_index
            if chosen is not None:
                plan = manifest.trials[chosen]
                unit = TrialUnitKey(
                    variant=variant,
                    fold_index=fold.fold_index,
                    phase=TrialPhase.VALIDATION,
                    trial_index=chosen,
                )
                run = record.runs.get(unit)
                if run is None:
                    raise _Mismatch(f"the trial record of {unit} is missing")
                done = research.unit(run, plan.expected_config_digest(unit))
                validation = ValidationUnitEvaluation(**_unit_fields(run, done))  # type: ignore[arg-type]
            evidence.append(
                FoldEvidence(
                    fold=fold,
                    selection=selection,
                    train_units=tuple(train_units),
                    validation=validation,
                )
            )
            selected.append(chosen)
        path = _recompute_path(research, variant, split, selected, record)
        outcomes[variant] = build_variant_outcome(standard, variant, evidence, path)
        last = selected[-1] if selected else None
        if last is None:
            raise _Mismatch(f"the last fold of {variant.value} selected no trial (Q12)")
        planned = manifest.trials[last].compiled_ref
        if planned is None or research.segments.compiled_ref(last) != planned:
            raise _Mismatch(
                f"the compiled_ref of the {variant.value} trial {last} rebuilt from the manifest"
                " differs from the recorded trial plan"
            )
        finals[variant] = last
    rebuilt = build_search_outcome(
        outcomes[Variant.STANDARD], outcomes[Variant.CANDIDATE], search.ledger_execution
    )
    if rebuilt != search:
        differing = [
            variant.value
            for variant in VARIANTS
            if rebuilt.variants.of(variant) != search.variants.of(variant)
        ]
        raise _Mismatch(
            "the verdicts recomputed from the validation intervals and the path segments differ"
            f" from the outcome record (variants {differing or ['combined']})"
        )
    return Paired(standard=finals[Variant.STANDARD], candidate=finals[Variant.CANDIDATE])


def _recompute_path(
    research: _Research,
    variant: Variant,
    split: SplitSpec,
    selected: list[int | None],
    record: RecordedResearch,
) -> PathSummary:
    """組の連続口座の検証経路を記録と成果物から組み立て直す（run はしない。D13 §4.5〜§4.7）。"""
    planned = plan_segments(split.folds, selected)
    initial = research.segments.initial_balance(variant)
    balance = initial
    rows: list[PathSegmentRow] = []
    series: list[Decimal] = []
    trade_count = 0
    end = PathEnd.COMPLETED
    end_segment: int | None = None
    end_reason: str | None = None
    for segment in planned:
        if segment.trial_index is None:
            rows.append(
                PathSegmentRow(
                    segment_index=segment.segment_index,
                    fold_indices=segment.fold_indices,
                    trial_index=None,
                    interval=segment.interval,
                    initial_balance=balance,
                    end_balance=balance,
                    carried_boundaries=segment.carried_boundaries,
                    settlement_count=0,
                    settlement_cost=None,
                    trade_count=None,
                    run_id=None,
                    run_reused=None,
                )
            )
            continue
        index = segment.segment_index
        label = f"{variant.value} path segment {index}"
        start = record.segment_starts.get(index)
        done = record.segment_ends.get(index)
        if start is None or done is None:
            raise _Mismatch(f"{label}: the start or end record is missing")
        digest = research.segments.segment_config_digest(
            variant, segment.trial_index, segment.interval, balance
        )
        expected = research.expected_run_id(digest)
        if (
            start.trial_index != segment.trial_index
            or start.interval != segment.interval
            or start.initial_balance != balance
            or start.config_digest != digest
            or start.expected_run_id != expected
            or done.expected_run_id != expected
            or done.run_id != expected
        ):
            raise _Mismatch(f"{label}: the records are not bound to the rebuilt configuration")
        settled: Money | None = None
        if done.run_evaluation_id is not None and done.result_digest is not None:
            evaluated = research.evaluated(
                label, done.run_id, digest, done.run_evaluation_id, done.result_digest
            )
            summaries = evaluated.result.summaries
            if summaries is not None and evaluated.status is EvaluationStatus.COMPLETED:
                settled = balance + summaries.realized + summaries.hypothetical_closed
        elif done.succeeded:  # pragma: no cover - 成功した区間は評価を持つ
            raise _Mismatch(f"{label}: a succeeded segment has no evaluation")
        if done.succeeded and settled != done.settled_equity:
            raise _Mismatch(f"{label}: the settled balance recomputed from the run differs")
        rows.append(
            PathSegmentRow(
                segment_index=index,
                fold_indices=segment.fold_indices,
                trial_index=segment.trial_index,
                interval=segment.interval,
                initial_balance=balance,
                end_balance=done.settled_equity if done.succeeded else None,
                carried_boundaries=segment.carried_boundaries,
                settlement_count=len(done.settlement_lines),
                settlement_cost=done.settlement_cost if done.succeeded else None,
                trade_count=done.trade_count if done.succeeded else None,
                run_id=done.run_id,
                run_reused=done.run_reused,
            )
        )
        if not done.succeeded:
            end = PathEnd.INCOMPLETE
            end_segment = index
            end_reason = segment_failure(done)
            break
        settled_equity = done.settled_equity
        if settled_equity is None or done.trade_count is None:  # pragma: no cover - succeeded
            raise _Mismatch(f"{label}: a succeeded segment carries its settled equity")
        equities = equity_series(research.repository, done.run_id)
        if isinstance(equities, str):
            raise _Mismatch(f"{label}: {equities}")
        series.extend(equities)
        series.append(settled_equity.amount)
        trade_count += done.trade_count
        balance = settled_equity
        if settled_equity.amount <= 0:
            end = PathEnd.CAPITAL_LOST
            end_segment = index
            end_reason = (
                f"the settled balance {settled_equity} at the end of segment {index} is not"
                " positive (D13 §4.5。仮置き 29)"
            )
            break
    if end is PathEnd.INCOMPLETE:
        return PathSummary(
            segments=tuple(rows),
            end=end,
            end_segment=end_segment,
            end_reason=end_reason,
            metrics=(),
            trade_count=None,
            final_balance=None,
        )
    return PathSummary(
        segments=tuple(rows),
        end=end,
        end_segment=end_segment,
        end_reason=end_reason,
        metrics=path_metrics(initial, series, balance),
        trade_count=trade_count,
        final_balance=balance,
    )
