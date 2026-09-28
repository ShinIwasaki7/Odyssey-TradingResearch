"""`RunExperiment` の各経路（D07 §19.4 の状態×出来事表・§19.6 の再実行と衝突・§20.3 の事後検査）。

記録票が run より前に保存されることと、事前検査で止まることは意味論テスト
（`tests/semantics/evaluation/test_experiment_semantics.py`）が固定する。ここでは残りの経路を
1つずつ確かめる。
"""

from __future__ import annotations

from dataclasses import replace

from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.refs import ConfigDigest
from odyssey_fx.evaluation.application.manifest import run_evaluation_id
from odyssey_fx.evaluation.application.ports import EvaluationReadFailure, StoredEvaluation
from odyssey_fx.evaluation.application.run_experiment import (
    ExperimentRefusal,
    RefusalKind,
    ReproductionVerdict,
    judge_reproduction,
)
from odyssey_fx.evaluation.domain.experiment import ExperimentOutcome, ExperimentStatus
from odyssey_fx.evaluation.domain.research_policy import PolicyCheck
from odyssey_fx.evaluation.domain.status import CheckOutcome, EvaluationStatus
from tests.fixtures.evaluation import traces
from tests.fixtures.evaluation.experiments import Workbench, make_manifest


def _completed(bench: Workbench) -> ExperimentOutcome:
    outcome = bench.use_case.execute(bench.prepared(make_manifest()))
    assert isinstance(outcome, ExperimentOutcome), outcome
    return outcome


def test_a_completed_experiment_records_the_run_the_evaluation_and_every_check() -> None:
    """`COMPLETED` の結末記録は run と評価の値と、P3・P4・P5 の全件を持つ（D07 §19.3）。"""
    bench = Workbench()
    outcome = _completed(bench)

    assert outcome.run_id == bench.result.run_id
    assert outcome.run_status is RunStatus.COMPLETED
    assert outcome.run_reused is False
    assert outcome.evaluation_status is EvaluationStatus.COMPLETED
    assert outcome.result_digest is not None
    assert [item.check for item in outcome.outcome_checks] == [
        PolicyCheck.PREREGISTRATION_UNCHANGED,
        PolicyCheck.RUN_MATCHES_PREREGISTRATION,
        PolicyCheck.EVALUATION_RULE_MATCHES,
    ]
    assert all(item.outcome is CheckOutcome.PASSED for item in outcome.outcome_checks)
    assert outcome.failed_checks == ()
    assert bench.log == ["save_manifest", "run", "write_evaluation", "write_outcome"]


def test_rerunning_the_same_version_reuses_the_run_and_the_evaluation() -> None:
    """同じ実験の同じ版の再実行は、run も評価もし直さずに再利用する（D07 §19.6 の手順2）。"""
    bench = Workbench()
    first = _completed(bench)
    bench.log.clear()

    second = bench.use_case.execute(bench.prepared(make_manifest()))

    assert isinstance(second, ExperimentOutcome)
    assert second.status is ExperimentStatus.COMPLETED
    assert second.run_reused is True
    assert second.result_digest == first.result_digest
    assert second.run_evaluation_id == first.run_evaluation_id
    assert bench.log == ["save_manifest", "write_outcome"]


def test_a_reused_run_without_an_evaluation_is_evaluated_once() -> None:
    """run を再利用し、評価の保存先が無ければ評価だけを書く（D07 §19.6 の手順5）。"""
    bench = Workbench()
    _completed(bench)
    bench.repository.evaluations.clear()
    bench.log.clear()

    outcome = bench.use_case.execute(bench.prepared(make_manifest()))

    assert isinstance(outcome, ExperimentOutcome)
    assert outcome.run_reused is True
    assert bench.log == ["save_manifest", "write_evaluation", "write_outcome"]


def test_an_existing_run_that_cannot_be_read_is_refused_before_anything_is_written() -> None:
    """`runs/<expected_run_id>/` があって読めなければ、記録票も結末記録も書かない（手順3）。"""
    bench = Workbench()
    bench.repository.placeholders.add(str(bench.run_manifest.run_id))

    refusal = bench.use_case.execute(bench.prepared(make_manifest()))

    assert isinstance(refusal, ExperimentRefusal)
    assert refusal.kind is RefusalKind.RUN_ARTIFACT_CONFLICT
    assert "cannot be read" in refusal.detail
    assert bench.log == []


def test_an_existing_run_with_another_config_digest_is_refused() -> None:
    """既存の run manifest の `ConfigDigest` が予測と違えば再利用せず拒否する（手順2・3）。"""
    bench = Workbench()
    bench.repository.runs[str(bench.run_manifest.run_id)] = bench.result
    manifest = replace(
        make_manifest(), expected_config_digest=ConfigDigest(digest("another config"))
    )

    refusal = bench.use_case.execute(bench.prepared(manifest))

    assert isinstance(refusal, ExperimentRefusal)
    assert refusal.kind is RefusalKind.RUN_ARTIFACT_CONFLICT
    assert bench.log == []


def test_an_unreadable_existing_evaluation_is_refused() -> None:
    """評価の保存先があるのに読めなければ、手順3 と同じく拒否する（D07 §19.6 の手順5）。"""
    bench = Workbench()
    bench.repository.runs[str(bench.run_manifest.run_id)] = bench.result
    evaluation_id = run_evaluation_id(
        bench.run_manifest.run_id,
        2,
        bench.evaluator.code_digest,
        (traces.CALENDAR.id, traces.CALENDAR.version),
    )
    bench.repository.evaluations[str(evaluation_id)] = EvaluationReadFailure(
        run_id=bench.run_manifest.run_id, detail="evaluation.json is broken"
    )

    refusal = bench.use_case.execute(bench.prepared(make_manifest()))

    assert isinstance(refusal, ExperimentRefusal)
    assert refusal.kind is RefusalKind.RUN_ARTIFACT_CONFLICT
    assert "evaluation.json is broken" in refusal.detail
    assert bench.log == []


def test_an_existing_evaluation_of_another_run_is_refused() -> None:
    """評価の保存先に別の run の評価 manifest があれば再利用しない（D07 §19.6 の手順2・5）。"""
    bench = Workbench()
    bench.repository.runs[str(bench.run_manifest.run_id)] = bench.result
    evaluation_id = run_evaluation_id(
        bench.run_manifest.run_id,
        2,
        bench.evaluator.code_digest,
        (traces.CALENDAR.id, traces.CALENDAR.version),
    )
    bench.repository.evaluations[str(evaluation_id)] = StoredEvaluation(
        run_id=RunId(digest("another run")),
        run_evaluation_id=evaluation_id,
        metric_set_version=2,
        status=EvaluationStatus.COMPLETED,
        result_digest=digest("result"),
    )

    refusal = bench.use_case.execute(bench.prepared(make_manifest()))

    assert isinstance(refusal, ExperimentRefusal)
    assert refusal.kind is RefusalKind.RUN_ARTIFACT_CONFLICT


def test_a_run_id_other_than_predicted_fails_the_post_run_check() -> None:
    """実際の `run_id` が予測と違えば P4 が不合格で `FAILED_POST_RUN_CHECK`（D07 §20.3）。

    記録票の保存から run までの間に合成の入力が変わった場合に当たる。成果物は残す。
    """
    bench = Workbench()
    predicted = RunId(digest("predicted before the inputs changed"))

    outcome = bench.use_case.execute(bench.prepared(make_manifest(), expected_run_id=predicted))

    assert isinstance(outcome, ExperimentOutcome)
    assert outcome.status is ExperimentStatus.FAILED_POST_RUN_CHECK
    assert outcome.failed_checks == (PolicyCheck.RUN_MATCHES_PREREGISTRATION,)
    assert outcome.expected_run_id == predicted
    assert outcome.run_id == bench.result.run_id
    assert "run" in bench.log


def test_an_unreadable_run_manifest_after_the_run_is_not_counted_as_passed() -> None:
    """run の後に run manifest が読めなければ P4 は `UNREADABLE` で、合格として扱わない。"""
    bench = Workbench()

    class _Runner:
        def __init__(self) -> None:
            self.inner = bench.runner

        def run(self, config: object, compiled: object) -> object:
            result = self.inner.run(config, compiled)  # type: ignore[arg-type]
            bench.repository.manifest_failure = "manifest.json is broken"
            return result

    bench.use_case._runner = _Runner()  # type: ignore[assignment]  # noqa: SLF001

    outcome = bench.use_case.execute(bench.prepared(make_manifest()))

    assert isinstance(outcome, ExperimentOutcome)
    assert outcome.status is ExperimentStatus.FAILED_POST_RUN_CHECK
    p4 = next(
        item
        for item in outcome.outcome_checks
        if item.check is PolicyCheck.RUN_MATCHES_PREREGISTRATION
    )
    assert p4.outcome is CheckOutcome.UNREADABLE


def test_a_run_that_did_not_complete_still_completes_the_experiment() -> None:
    """run や評価そのものの失敗は実験の結末を変えない（D07 §19.4。状態は各自が表す）。"""
    bench = Workbench(run_manifest=traces.manifest_for(status="FAILED_DATA_ERROR"))
    bench.runner.result = traces.result_for(
        bench.run_manifest, status=RunStatus.FAILED_DATA_ERROR, with_summaries=False
    )
    outcome = bench.use_case.execute(bench.prepared(make_manifest(run_manifest=bench.run_manifest)))

    assert isinstance(outcome, ExperimentOutcome)
    assert outcome.status is ExperimentStatus.COMPLETED
    assert outcome.run_status is RunStatus.FAILED_DATA_ERROR
    assert outcome.evaluation_status is EvaluationStatus.REJECTED


def test_an_unreadable_existing_manifest_is_a_conflict() -> None:
    """同じ版の既存の記録票が読めなければ、上書きせず内容違いと同じく拒否する（仮置き）。"""
    bench = Workbench()
    bench.store.unreadable = True

    refusal = bench.use_case.execute(bench.prepared(make_manifest()))

    assert isinstance(refusal, ExperimentRefusal)
    assert refusal.kind is RefusalKind.MANIFEST_CONFLICT
    assert "run" not in bench.log


def test_the_reproduction_verdict_compares_the_run_id_before_the_result() -> None:
    """再現の判定（D07 §21.2 の手順5）: `run_id` が違えば結果を比べず `RUN_ID_MISMATCH`。"""
    run_a, run_b = RunId(digest("a")), RunId(digest("b"))
    result_a, result_b = digest("result a"), digest("result b")
    assert (
        judge_reproduction(
            expected_run_id=run_a,
            observed_run_id=run_a,
            expected_result_digest=result_a,
            observed_result_digest=result_a,
        )
        is ReproductionVerdict.REPRODUCED
    )
    assert (
        judge_reproduction(
            expected_run_id=run_a,
            observed_run_id=run_a,
            expected_result_digest=result_a,
            observed_result_digest=result_b,
        )
        is ReproductionVerdict.RESULT_MISMATCH
    )
    assert (
        judge_reproduction(
            expected_run_id=run_a,
            observed_run_id=run_b,
            expected_result_digest=result_a,
            observed_result_digest=result_b,
        )
        is ReproductionVerdict.RUN_ID_MISMATCH
    )
