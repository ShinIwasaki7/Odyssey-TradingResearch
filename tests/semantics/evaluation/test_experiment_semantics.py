"""D07 v2.0 §17.1 の意味論の行 #8〜#10（D08 §7.2 の対応表。実装 PR 3）。

1行1テストで名前を付けて固定する（D01 §9、D08 §2.1）。

- #8（`test_8_*`）: 記録票は run より前に保存され、同じ版で内容が違えば記録票を書き換えず
  run もしない。
- #9（`test_9_*`）: 研究ポリシーの事前検査に1件でも合格しないものがあれば run しない（結末
  記録 `REJECTED_BY_POLICY`。複雑性の上限を1つ超えた場合を含む）。
- #10（`test_10_*`）: 研究履歴以外の partition が許可集合に入っていれば run しない（検査 P2）。

`RunExperiment`（`evaluation.application.run_experiment`）を、偽物のポート（記録票の保存・
run・結果の読み書き）で通す。偽物は呼ばれた順を1本の記録に残すので、「保存が run より前」を
順序として確かめられる（`tests/fixtures/evaluation/experiments.py`）。
"""

from __future__ import annotations

from odyssey_fx.evaluation.application.run_experiment import ExperimentRefusal, RefusalKind
from odyssey_fx.evaluation.domain.experiment import ExperimentOutcome, ExperimentStatus
from odyssey_fx.evaluation.domain.research_policy import ComplexityMeasures, PolicyCheck
from odyssey_fx.marketdata.domain.access import AccessClass
from tests.fixtures.evaluation.experiments import LIMITS, Workbench, make_manifest


def test_8_the_manifest_is_saved_before_the_run() -> None:
    """#8 前半: 記録票の保存が成功するまで run を始めない（D07 §19.4）。"""
    bench = Workbench()
    outcome = bench.use_case.execute(bench.prepared(make_manifest()))

    assert isinstance(outcome, ExperimentOutcome)
    assert outcome.status is ExperimentStatus.COMPLETED
    assert bench.log.index("save_manifest") < bench.log.index("run")
    assert bench.log[-1] == "write_outcome"


def test_8_a_different_content_under_the_same_version_neither_rewrites_nor_runs() -> None:
    """#8 後半: 同じ版で内容が違えば記録票を書き換えず、run もしない（検査 P3。D07 §19.4）。"""
    bench = Workbench()
    first = make_manifest()
    assert isinstance(bench.use_case.execute(bench.prepared(first)), ExperimentOutcome)
    bench.log.clear()

    changed = make_manifest(hypothesis="書き直した仮説")
    refusal = bench.use_case.execute(bench.prepared(changed))

    assert isinstance(refusal, ExperimentRefusal)
    assert refusal.kind is RefusalKind.MANIFEST_CONFLICT
    assert bench.store.existing == first.experiment_id
    assert bench.store.saved == [first]
    assert "run" not in bench.log
    assert "write_outcome" not in bench.log


def test_9_a_complexity_one_over_the_limit_does_not_run() -> None:
    """#9: 複雑性の上限を1つ超えると run せず、結末記録は `REJECTED_BY_POLICY`（D07 §20.3）。"""
    bench = Workbench()
    over = ComplexityMeasures(
        component_kinds=LIMITS.component_kinds,
        instances=LIMITS.instances + 1,
        parameters=LIMITS.parameters,
        decision_outputs=LIMITS.decision_outputs,
    )
    outcome = bench.use_case.execute(bench.prepared(make_manifest(measures=over)))

    assert isinstance(outcome, ExperimentOutcome)
    assert outcome.status is ExperimentStatus.REJECTED_BY_POLICY
    assert outcome.failed_checks == (PolicyCheck.COMPLEXITY_WITHIN_LIMITS,)
    assert outcome.run_id is None
    assert "run" not in bench.log
    # 不合格も記録票に残す（D07 §19.4 の「保存へ（不合格も記録票に残す）」）。
    assert bench.log == ["save_manifest", "write_outcome"]


def test_9_an_unmeasured_complexity_does_not_run() -> None:
    """#9: 計測できなかった複雑性（`UNREADABLE`）も合格ではないので run しない（D07 §20.3）。"""
    bench = Workbench()
    unmeasured = ComplexityMeasures(
        component_kinds=5, instances=6, parameters=6, decision_outputs=None
    )
    outcome = bench.use_case.execute(bench.prepared(make_manifest(measures=unmeasured)))

    assert isinstance(outcome, ExperimentOutcome)
    assert outcome.status is ExperimentStatus.REJECTED_BY_POLICY
    assert "run" not in bench.log


def test_10_a_holdout_partition_in_the_allowed_set_does_not_run() -> None:
    """#10: 研究履歴以外の partition が許可集合にあれば run しない（検査 P2。D07 §20.3）。"""
    bench = Workbench()
    manifest = make_manifest(
        allowed_partitions={
            "USDJPY_15m_bid/RESEARCH_HISTORY": AccessClass.RESEARCH_HISTORY,
            "USDJPY_15m_bid/LEGACY_HOLDOUT": AccessClass.LEGACY_HOLDOUT,
        }
    )
    outcome = bench.use_case.execute(bench.prepared(manifest))

    assert isinstance(outcome, ExperimentOutcome)
    assert outcome.status is ExperimentStatus.REJECTED_BY_POLICY
    assert outcome.failed_checks == (PolicyCheck.RESEARCH_HISTORY_ONLY,)
    assert "run" not in bench.log
