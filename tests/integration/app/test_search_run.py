"""探索の実験の実行と記録（D09 §4.1・§10.3〜§10.8・§10.12・§11。段階5 実装 PR 4）。

T02 の人工データを受け入れて承認した作業場（`tests/fixtures/acceptance/t02_workspace.py`）に、
試験用の研究ポリシー版 3（2 fold。テスト用であり研究ポリシーではない）・空の試行台帳
（`research/trial_ledger.jsonl`）・探索の実験設定（検証戦略 B の2軸 × 2値。4 試行とも
コンパイルが通る）を置き、`odyssey-fx experiment run` を同じプロセスで呼ぶ。

確かめること（D08 §7.4 の行の番号を括弧に書いた）:

- 2 × 2 × 2 fold を最後まで通し、全単位の開始記録と試行記録・選定記録・集約表・結末記録・台帳の
  2行（開始と結末）がそろう。記録票は保存して読み戻せる（PR 2 の完了条件9）。
- 書く順序: 台帳の開始の行 → 束縛の記録 → 最初の単位の開始記録（#22）、選定記録 → 検証区間の
  単位の開始記録（#4）、結末記録 → 台帳の結末の行（#15）。
- 途中で止まると、記録票・束縛の記録・開始記録と台帳の開始の行だけが残る（#15・#23 の d）。
- 同じ版の再実行は前回の記録を退避し、実行番号 2 の開始と結末の行を足す。
- 台帳の読込検査 L0〜L11 のどれかに当たる・ロックがあると run を1つも始めずに終了コード 1。
- 研究ポリシーの版の登録簿が読めなければ終了コード 2（Q37。読込の照合で止まる）。
- 数え直し待ち（Q39）: 数え直し待ちの版の再実行だけが通り、ほかの版は止まる（#27〜#29）。
- 候補なしの fold の後も次の fold を実行する（#6）。事後検査が合格でない単位は候補から外れ、
  実験は `FAILED_POST_RUN_CHECK`（#7）。`experiment run` は封印期間の許可判定を呼ばない（#8）。
- 結末の行の前に自分の開始の行が台帳から消えていれば、結末の行を書かずに終了コード 1（#24）。
- 保存済みの評価の再利用（D09 §17.7.4 の2・3、§13 の22）: 指標は保存済みの `METRICS` 表から読み、
  評価をやり直さない。読めなければ記録票の保存の前に拒否（終了コード 5）、実行中に読めなく
  なれば中断（終了コード 1）。

最後まで通す実行は1回だけ行って複数のテストで確かめる（`full`）。ほかのテストは、その実行が
作った run と評価の成果物（`runs/<run_id>/`）を成果物の基点へ写してから走らせる（同じ設定の
単位は既存の成果物を再利用する。D07 §19.6）。実験の記録・台帳はテストごとに別である。
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main
from odyssey_fx.common.canonical import digest, encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.evaluation.adapters.fs_store import (
    TRIAL_LEDGER_LOCK_PATH,
    FileSystemExperimentStore,
    FileSystemResultRepository,
    read_experiment_manifest,
    read_experiment_outcome,
    read_ledger_binding,
    read_selections,
    read_trial_ledger_file,
    read_unit_records,
    unit_name,
)
from odyssey_fx.evaluation.application import run_experiment
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun
from odyssey_fx.evaluation.application.manifest import RunEvaluationId
from odyssey_fx.evaluation.application.ports import EvaluationReadFailure, TrialLedgerContents
from odyssey_fx.evaluation.domain import research_policy
from odyssey_fx.evaluation.domain.experiment import ExperimentStatus
from odyssey_fx.evaluation.domain.research_policy import PolicyCheck
from odyssey_fx.evaluation.domain.search import (
    CandidateStatus,
    TrialLedgerEvent,
    TrialLedgerLine,
    TrialPhase,
    TrialUnitKey,
)
from odyssey_fx.evaluation.domain.status import CheckOutcome
from tests.fixtures.acceptance.t02_workspace import T02Workspace, build_workspace
from tests.fixtures.evaluation.research_policies import (
    append_registry_entry,
    policy_v3_text,
    write_policy,
)
from tests.fixtures.evaluation.search_experiments import (
    TWO_FOLD_SPLIT,
    install_policy_v3,
    write_search_experiment,
)
from tests.fixtures.evaluation.trial_ledger import chain, finished, started, write_ledger

#: 4 試行ともコンパイルが通る2軸（15分足 EMA の期間 × 損切りの安値の本数）。
AXES = (
    "    - {instance: m15_ema, parameter: period, values: [10, 20]}\n"
    "    - {instance: stop_level, parameter: lookback, values: [10, 20]}"
)
#: 足切りを通る試行が無い評価基準（候補なしの fold を作る。テスト用）。
_NO_CANDIDATE = (
    "  selection:\n    metric: NET_RETURN_RATE\n    direction: MAXIMIZE\n"
    '    eligibility:\n      - {metric: NET_RETURN_RATE, comparator: GT, threshold: "1000"}'
)


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> T02Workspace:
    """試験用の研究ポリシー版 3（2 fold）・版 4（試行数の上限 3）・版 5（足切りを通らない）。"""
    built = build_workspace(tmp_path_factory.mktemp("t02-search-run"))
    install_policy_v3(built.repo)
    install_policy_v3(built.repo, version=4, trials=3)
    text = policy_v3_text(version=5, replace={"split": TWO_FOLD_SPLIT, "selection": _NO_CANDIDATE})
    write_policy(built.repo, "research_policy", 5, text)
    append_registry_entry(built.repo, "research_policy", 5)
    return built


def _reset_ledger(workspace: T02Workspace) -> Path:
    ledger = workspace.repo / "research" / "trial_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"")
    (workspace.repo / TRIAL_LEDGER_LOCK_PATH).unlink(missing_ok=True)
    return ledger


@pytest.fixture(autouse=True)
def empty_ledger(workspace: T02Workspace) -> Iterator[Path]:
    """各テストは空の台帳（版管理で置かれた空のファイルと同じ）から始める。"""
    ledger = _reset_ledger(workspace)
    yield ledger
    (workspace.repo / TRIAL_LEDGER_LOCK_PATH).unlink(missing_ok=True)


def _experiment(workspace: T02Workspace, name: str, **kwargs: Any) -> Path:
    return write_search_experiment(
        workspace.repo, f"{name}.yaml", experiment_id=name, axes=AXES, **kwargs
    )


def _run(workspace: T02Workspace, out: Path, experiment: Path) -> tuple[int, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(
            [
                "experiment",
                "run",
                "--experiment",
                str(experiment),
                "--snapshots",
                str(workspace.repo / "data/snapshots"),
                "--out",
                str(out),
                "--repo-root",
                str(workspace.repo),
            ]
        )
    return code, stdout.getvalue() + stderr.getvalue()


def _version(out: Path, name: str, version: int = 1) -> Path:
    return out / "runs" / "experiments" / name / f"v{version}"


def _ledger(workspace: T02Workspace) -> tuple[TrialLedgerLine, ...]:
    contents = read_trial_ledger_file(workspace.repo / "research" / "trial_ledger.jsonl")
    assert isinstance(contents, TrialLedgerContents), contents
    return contents.lines


def _unit(fold: int, phase: TrialPhase, trial: int) -> TrialUnitKey:
    return TrialUnitKey(fold_index=fold, phase=phase, trial_index=trial)


# --- 最後まで通す（2 × 2 × 2 fold）-------------------------------------------------------


class _RecordingStore(FileSystemExperimentStore):
    """書き込みの順序を記録する（D09 §7.5・§10.12.3 の2・§10.7 の終端の書き込みの順序）。"""

    calls: list[str] = []

    def append_trial_ledger(self, line: Any) -> Any:
        type(self).calls.append(f"ledger {line.entry.event.value}")
        return super().append_trial_ledger(line)

    def write_ledger_binding(self, binding: Any) -> None:
        type(self).calls.append("binding")
        super().write_ledger_binding(binding)

    def write_trial_start(self, record: Any) -> None:
        type(self).calls.append(f"start {unit_name(record.unit)}")
        super().write_trial_start(record)

    def write_selection(self, selection: Any) -> None:
        type(self).calls.append(f"selection {selection.fold_index}")
        super().write_selection(selection)

    def write_aggregate_tables(self, *args: Any) -> None:
        type(self).calls.append("tables")
        super().write_aggregate_tables(*args)

    def write_outcome(self, outcome: Any) -> None:
        type(self).calls.append("outcome")
        super().write_outcome(outcome)


@dataclass(frozen=True)
class _Full:
    out: Path
    code: int
    output: str
    calls: tuple[str, ...]
    lines: tuple[TrialLedgerLine, ...]


@pytest.fixture(scope="module")
def full(workspace: T02Workspace, tmp_path_factory: pytest.TempPathFactory) -> _Full:
    """2 × 2 × 2 fold を何も無い基点で最後まで通した1回の実行（書き込みの順序も記録する）。"""
    _reset_ledger(workspace)
    out = tmp_path_factory.mktemp("full-out")
    _RecordingStore.calls = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(composition, "FileSystemExperimentStore", _RecordingStore)
        code, output = _run(workspace, out, _experiment(workspace, "full"))
    return _Full(
        out=out,
        code=code,
        output=output,
        calls=tuple(_RecordingStore.calls),
        lines=_ledger(workspace),
    )


def _seeded(full: _Full, tmp_path: Path) -> Path:
    """`full` が作った run と評価の成果物だけを写した成果物の基点（実験の記録は写さない）。"""
    out = tmp_path / "out"
    (out / "runs").mkdir(parents=True)
    for entry in (full.out / "runs").iterdir():
        if entry.name != "experiments":
            shutil.copytree(entry, out / "runs" / entry.name)
    return out


def test_a_full_search_records_every_unit_selection_table_outcome_and_two_ledger_lines(
    full: _Full,
) -> None:
    assert full.code == 0, full.output
    directory = _version(full.out, "full")

    manifest = read_experiment_manifest(directory)
    assert manifest.is_search and len(manifest.trials) == 4
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.status is ExperimentStatus.COMPLETED
    assert outcome.expected_run_id is None and outcome.run_id is None
    assert [item.check for item in outcome.outcome_checks] == [
        PolicyCheck.PREREGISTRATION_UNCHANGED
    ]
    search = outcome.search
    assert search is not None and search.ledger_execution == 1

    starts, runs = read_unit_records(directory)
    selections = read_selections(directory)
    assert sorted(selections) == [0, 1]
    expected_units = {_unit(fold, TrialPhase.TRAIN, trial) for fold in (0, 1) for trial in range(4)}
    for fold, selection in selections.items():
        assert selection.selected_trial_index is not None
        expected_units.add(_unit(fold, TrialPhase.VALIDATION, selection.selected_trial_index))
    assert set(starts) == set(runs) == expected_units
    assert sum(count for _, count in search.trial_counts) == len(expected_units)
    assert search.selections == tuple(selections[fold] for fold in (0, 1))
    for record in runs.values():
        assert [check.outcome for check in record.outcome_checks] == [CheckOutcome.PASSED] * 2

    # fold 0 の検証区間と fold 1 の選定区間は同じ区間なので、fold 0 で選んだ試行の2単位は
    # 1つの run を共有し、後の単位は前の単位の成果物を再利用する（D09 §6.2・§10.5）。
    chosen = selections[0].selected_trial_index
    assert chosen is not None
    shared = runs[_unit(1, TrialPhase.TRAIN, chosen)]
    assert shared.run_id == runs[_unit(0, TrialPhase.VALIDATION, chosen)].run_id
    assert shared.run_reused

    units = pl.read_parquet(directory / "search" / "trial_units.parquet")
    assert units.height == len(expected_units)
    assert units.columns[:3] == ["fold_index", "phase", "trial_index"]
    assert units.schema["run_reused"] == pl.Boolean()
    assert set(units.get_column("status").to_list()) == {"COMPLETED"}
    metrics = pl.read_parquet(directory / "search" / "trial_metrics.parquet")
    assert metrics.columns[:4] == ["fold_index", "phase", "trial_index", "metric_id"]
    assert (
        not metrics.select(["fold_index", "phase", "trial_index", "metric_id"])
        .is_duplicated()
        .any()
    )

    lines = full.lines
    assert [line.entry.event for line in lines] == [
        TrialLedgerEvent.STARTED,
        TrialLedgerEvent.FINISHED,
    ]
    assert lines[1].entry.verdict is search.verdict
    assert lines[1].entry.status is ExperimentStatus.COMPLETED
    binding = read_ledger_binding(directory)
    assert binding is not None
    assert (binding.execution, binding.started_line_digest) == (1, lines[0].digest)
    assert lines[0].entry.search_plan_digest == digest(manifest.search_plan)
    assert lines[0].entry.trial_count == 4
    assert "探索の実験のレポートは段階5 の実装 PR 5 で作る" in full.output
    assert not (directory / "report.md").exists()


def test_4_22_the_records_are_written_in_the_designed_order(full: _Full) -> None:
    """台帳の開始の行 → 束縛の記録 → 最初の開始記録（#22）。各 fold の選定記録は検証区間の単位の
    開始記録より前（#4）。集約表 → 結末記録 → 台帳の結末の行（D09 §10.7）。"""
    calls = list(full.calls)
    assert calls[:3] == ["ledger STARTED", "binding", "start f0_TRAIN_t0"]
    for fold in (0, 1):
        validation = next(c for c in calls if c.startswith(f"start f{fold}_VALIDATION"))
        assert calls.index(f"selection {fold}") < calls.index(validation)
        assert all(
            calls.index(f"start f{fold}_TRAIN_t{trial}") < calls.index(f"selection {fold}")
            for trial in range(4)
        )
    assert calls[-3:] == ["tables", "outcome", "ledger FINISHED"]


def test_15_23_an_interrupted_search_leaves_the_start_records_and_the_started_line_only(
    workspace: T02Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """途中で止まった実行（§10.12.4 の d）: 記録票・束縛の記録・開始記録と台帳の開始の行だけ。"""

    def stop(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the process stopped")

    monkeypatch.setattr(composition, "_run_use_case", stop)
    with pytest.raises(RuntimeError, match="the process stopped"):
        _run(workspace, tmp_path, _experiment(workspace, "stopped"))
    directory = _version(tmp_path, "stopped")
    assert (directory / "experiment_manifest.json").is_file()
    assert read_experiment_outcome(directory) is None
    starts, runs = read_unit_records(directory)
    assert list(starts) == [_unit(0, TrialPhase.TRAIN, 0)] and runs == {}
    assert read_selections(directory) == {}
    assert read_ledger_binding(directory) is not None
    assert [line.entry.event for line in _ledger(workspace)] == [TrialLedgerEvent.STARTED]


def test_the_same_version_rerun_retreats_the_records_and_counts_execution_2(
    workspace: T02Workspace, tmp_path: Path, full: _Full
) -> None:
    out = _seeded(full, tmp_path)
    experiment = _experiment(workspace, "again")
    assert _run(workspace, out, experiment)[0] == 0
    code, output = _run(workspace, out, experiment)
    assert code == 0, output
    directory = _version(out, "again")
    assert (directory / "experiment_outcome.1.json").is_file()
    assert (directory / "search.1" / "ledger_execution.json").is_file()
    assert not (directory / "retreat_in_progress.json").exists()
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.search is not None
    assert outcome.search.ledger_execution == 2
    _, runs = read_unit_records(directory)
    assert all(record.run_reused for record in runs.values())
    lines = _ledger(workspace)
    assert [(line.entry.execution, line.entry.event) for line in lines] == [
        (1, TrialLedgerEvent.STARTED),
        (1, TrialLedgerEvent.FINISHED),
        (2, TrialLedgerEvent.STARTED),
        (2, TrialLedgerEvent.FINISHED),
    ]
    assert lines[0].entry.execution_nonce != lines[2].entry.execution_nonce


def test_a_search_rejected_by_the_pre_run_checks_writes_nothing_to_the_ledger(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """事前検査（P7）で止まった実験は台帳に書かず、探索の記録 `search/` も作らない（§10.10）。"""
    code, output = _run(workspace, tmp_path, _experiment(workspace, "p7", policy_version=4))
    assert code == 3, output
    directory = _version(tmp_path, "p7")
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.status is ExperimentStatus.REJECTED_BY_POLICY
    assert outcome.search is None
    assert outcome.failed_checks == (PolicyCheck.TRIAL_COUNT_WITHIN_LIMIT,)
    assert not (directory / "search").exists()
    assert _ledger(workspace) == ()


# --- 台帳が読めない・追記できない（D09 §10.12.2・§10.12.4 の a〜c）------------------------


def _corrupt(path: Path) -> None:
    write_ledger(path.parent.parent, [b"not json\n"])


def _broken_chain(path: Path) -> None:
    write_ledger(
        path.parent.parent, [*chain([started("x")]), *chain([started("y")], prev=digest("z"))]
    )


def _gap(path: Path) -> None:
    write_ledger(path.parent.parent, chain([started("x", 2)]))


def _orphan(path: Path) -> None:
    write_ledger(path.parent.parent, chain([finished(started("x"))]))


def _missing(path: Path) -> None:
    path.unlink()


def _locked(path: Path) -> None:
    (path.parent / "trial_ledger.lock").write_text("held\n", encoding="utf-8")


_DEFECTS = {
    "L0_missing": (_missing, "FILE_MISSING"),
    "L2_corrupt": (_corrupt, "LINE_CORRUPT"),
    "L3_chain": (_broken_chain, "CHAIN_BROKEN"),
    "L7_gap": (_gap, "EXECUTION_GAP"),
    "L8_orphan": (_orphan, "ORPHAN_FINISHED"),
    "locked": (_locked, "LOCKED"),
}


@pytest.mark.parametrize("case", sorted(_DEFECTS))
def test_a_ledger_that_cannot_be_read_or_appended_stops_before_any_run(
    workspace: T02Workspace, tmp_path: Path, empty_ledger: Path, case: str
) -> None:
    """台帳の読込検査に当たる・ロックがあると、run を1つも始めずに終了コード 1（開始の行を
    書けなければ run しない。D09 §10.7・§10.12.4 の a〜c）。記録票は残り、結末記録は無い。"""
    damage, kind = _DEFECTS[case]
    damage(empty_ledger)
    code, output = _run(workspace, tmp_path, _experiment(workspace, f"ledger_{case.lower()}"))
    assert code == 1, output
    assert kind in output
    directory = _version(tmp_path, f"ledger_{case.lower()}")
    assert (directory / "experiment_manifest.json").is_file()
    assert read_experiment_outcome(directory) is None
    assert not (directory / "search").exists()
    run_dirs = [p for p in (tmp_path / "runs").iterdir() if p.name != "experiments"]
    assert run_dirs == []


def test_l11_a_discarded_execution_stops_other_experiments_until_it_is_re_run(
    workspace: T02Workspace, tmp_path: Path, empty_ledger: Path, full: _Full
) -> None:
    """数え直し待ち（Q38・Q39。#27〜#29）。

    実験 A を走らせた後に台帳を空へ戻す（合流の衝突を片方の側だけ残して解消した状態）と、A の
    束縛の記録が台帳と合わず A は数え直し待ちになる。ほかの実験 B は採番の読込で止まり（終了
    コード 1）、A の再実行は通る。再実行が退避の後・開始の行の前で止まっても（ロック）A は
    数え直し待ちのまま。A を数え直すと B が走れる。
    """
    tmp_path = _seeded(full, tmp_path)
    a = _experiment(workspace, "pending_a")
    b = _experiment(workspace, "pending_b")
    assert _run(workspace, tmp_path, a)[0] == 0
    empty_ledger.write_bytes(b"")

    code, output = _run(workspace, tmp_path, b)
    assert code == 1 and "UNMATCHED_BINDING" in output, output
    assert "runs/experiments/pending_a/v1/search/ledger_execution.json" in output

    lock = workspace.repo / TRIAL_LEDGER_LOCK_PATH
    lock.write_text("held\n", encoding="utf-8")
    code, output = _run(workspace, tmp_path, a)
    assert code == 1 and "LOCKED" in output, output
    assert (_version(tmp_path, "pending_a") / "search.1" / "ledger_execution.json").is_file()
    lock.unlink()
    assert _run(workspace, tmp_path, b)[0] == 1  # 退避されただけでは解けない

    code, output = _run(workspace, tmp_path, a)
    assert code == 0, output
    code, output = _run(workspace, tmp_path, b)
    assert code == 0, output
    executions = [(line.entry.experiment_name, line.entry.execution) for line in _ledger(workspace)]
    assert executions == [("pending_a", 1), ("pending_a", 1), ("pending_b", 1), ("pending_b", 1)]


def test_24_the_finished_line_is_not_written_when_the_started_line_is_gone(
    workspace: T02Workspace,
    tmp_path: Path,
    empty_ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
    full: _Full,
) -> None:
    """結末記録を書いた後、台帳から自分の開始の行が消えていれば、結末の行を書かずに終了コード 1
    （後から足さない。D09 §10.12.3 の3・§10.12.4 の e）。"""

    class _Reverting(FileSystemExperimentStore):
        def write_outcome(self, outcome: Any) -> None:
            super().write_outcome(outcome)
            empty_ledger.write_bytes(b"")  # 人間が台帳を履歴の版へ戻した

    monkeypatch.setattr(composition, "FileSystemExperimentStore", _Reverting)
    out = _seeded(full, tmp_path)
    code, output = _run(workspace, out, _experiment(workspace, "reverted"))
    assert code == 1, output
    assert "FINISHED" in output
    assert read_experiment_outcome(_version(out, "reverted")) is not None
    assert _ledger(workspace) == ()


# --- 途中停止の位置と回復（D09 §10.12.4 の c・e・f、#23・#27・#28）--------------------------


def _executions(workspace: T02Workspace) -> list[tuple[int, TrialLedgerEvent]]:
    return [(line.entry.execution, line.entry.event) for line in _ledger(workspace)]


def test_23_c_a_binding_that_cannot_be_written_stops_before_any_run_and_counts_the_execution(
    workspace: T02Workspace, tmp_path: Path, full: _Full, monkeypatch: pytest.MonkeyPatch
) -> None:
    """位置 c: 開始の行の後、束縛の記録を書けずに止まる。run は1つも始まらず（開始記録なし）、
    終了コード 1。開始の行は数えられたままで、同じ版の再実行は実行番号 2 になる。"""

    class _NoBinding(FileSystemExperimentStore):
        def write_ledger_binding(self, binding: Any) -> None:
            raise KernelValueError("the binding record could not be written")

    out = _seeded(full, tmp_path)
    experiment = _experiment(workspace, "stop_c")
    with monkeypatch.context() as patch:
        patch.setattr(composition, "FileSystemExperimentStore", _NoBinding)
        code, output = _run(workspace, out, experiment)
    assert code == 1, output
    directory = _version(out, "stop_c")
    starts, runs = read_unit_records(directory)
    assert starts == {} and runs == {}
    assert read_experiment_outcome(directory) is None
    assert _executions(workspace) == [(1, TrialLedgerEvent.STARTED)]

    code, output = _run(workspace, out, experiment)
    assert code == 0, output
    assert _executions(workspace) == [
        (1, TrialLedgerEvent.STARTED),
        (2, TrialLedgerEvent.STARTED),
        (2, TrialLedgerEvent.FINISHED),
    ]


def test_23_e_a_stop_after_the_outcome_is_recounted_by_a_rerun(
    workspace: T02Workspace, tmp_path: Path, full: _Full, monkeypatch: pytest.MonkeyPatch
) -> None:
    """位置 e: 結末記録の後・結末の行の前で止まる。結末の行は後から足さず、同じ版の再実行が
    実行番号 + 1 の開始と結末の行をそろえる。"""

    class _StopAfterOutcome(FileSystemExperimentStore):
        def write_outcome(self, outcome: Any) -> None:
            super().write_outcome(outcome)
            raise RuntimeError("the process stopped after the outcome")

    out = _seeded(full, tmp_path)
    experiment = _experiment(workspace, "stop_e")
    with monkeypatch.context() as patch:
        patch.setattr(composition, "FileSystemExperimentStore", _StopAfterOutcome)
        with pytest.raises(RuntimeError, match="after the outcome"):
            _run(workspace, out, experiment)
    assert read_experiment_outcome(_version(out, "stop_e")) is not None
    assert _executions(workspace) == [(1, TrialLedgerEvent.STARTED)]

    code, output = _run(workspace, out, experiment)
    assert code == 0, output
    assert (_version(out, "stop_e") / "experiment_outcome.1.json").is_file()
    assert _executions(workspace) == [
        (1, TrialLedgerEvent.STARTED),
        (2, TrialLedgerEvent.STARTED),
        (2, TrialLedgerEvent.FINISHED),
    ]


def test_23_f_a_torn_finished_line_is_cut_and_a_rerun_counts_the_next_execution(
    workspace: T02Workspace,
    tmp_path: Path,
    full: _Full,
    empty_ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """位置 f: 結末の行の書きかけで止まる。次の読込は書きかけを無視し、次の追記がそれを切り詰める。
    同じ版の再実行は実行番号 2 の開始と結末の行をそろえる（1 の結末の行は足されない）。"""

    class _TornFinish(FileSystemExperimentStore):
        def append_trial_ledger(self, line: Any) -> Any:
            if line.entry.event is TrialLedgerEvent.FINISHED:
                with empty_ledger.open("ab") as stream:
                    stream.write(b'{"entry": {"event": "FINI')
                raise RuntimeError("the process stopped while appending the finished line")
            return super().append_trial_ledger(line)

    out = _seeded(full, tmp_path)
    experiment = _experiment(workspace, "stop_f")
    with monkeypatch.context() as patch:
        patch.setattr(composition, "FileSystemExperimentStore", _TornFinish)
        with pytest.raises(RuntimeError, match="finished line"):
            _run(workspace, out, experiment)
    contents = read_trial_ledger_file(empty_ledger)
    assert isinstance(contents, TrialLedgerContents) and contents.torn_tail
    assert _executions(workspace) == [(1, TrialLedgerEvent.STARTED)]

    code, output = _run(workspace, out, experiment)
    assert code == 0, output
    assert _executions(workspace) == [
        (1, TrialLedgerEvent.STARTED),
        (2, TrialLedgerEvent.STARTED),
        (2, TrialLedgerEvent.FINISHED),
    ]
    assert empty_ledger.read_bytes().endswith(b"\n")


def test_27_a_running_execution_finishes_while_another_version_waits_for_a_recount(
    workspace: T02Workspace,
    tmp_path: Path,
    full: _Full,
    empty_ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#27: 数え直し待ちの版が実行の途中で現れても、すでに始まった実行の結末の行の追記は通る
    （逆照合 L11 は採番の読込にだけ掛かる）。その後、ほかの版は止まる。"""
    out = _seeded(full, tmp_path)
    assert _run(workspace, out, _experiment(workspace, "late_a"))[0] == 0
    aside = tmp_path / "aside"
    shutil.move(_version(out, "late_a"), aside)
    empty_ledger.write_bytes(b"")

    class _PendingAppears(FileSystemExperimentStore):
        def write_ledger_binding(self, binding: Any) -> None:
            super().write_ledger_binding(binding)
            if not _version(out, "late_a").exists():
                shutil.move(aside, _version(out, "late_a"))

    with monkeypatch.context() as patch:
        patch.setattr(composition, "FileSystemExperimentStore", _PendingAppears)
        code, output = _run(workspace, out, _experiment(workspace, "late_c"))
    assert code == 0, output
    assert [(line.entry.experiment_name, line.entry.event) for line in _ledger(workspace)] == [
        ("late_c", TrialLedgerEvent.STARTED),
        ("late_c", TrialLedgerEvent.FINISHED),
    ]
    code, output = _run(workspace, out, _experiment(workspace, "late_b"))
    assert code == 1 and "UNMATCHED_BINDING" in output, output


def test_28_a_rerun_stopped_after_the_retreat_by_an_exception_stays_pending(
    workspace: T02Workspace,
    tmp_path: Path,
    full: _Full,
    empty_ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#28: 数え直し待ちの版の再実行が、退避の後・開始の行の前に例外で止まっても、その版は数え
    直し待ちのまま（退避されただけでは解けない）。ロックで止まる場合は上の L11 のテスト。"""
    out = _seeded(full, tmp_path)
    a = _experiment(workspace, "crash_a")
    assert _run(workspace, out, a)[0] == 0
    empty_ledger.write_bytes(b"")

    class _CrashBeforeStart(FileSystemExperimentStore):
        def append_trial_ledger(self, line: Any) -> Any:
            raise RuntimeError("the process stopped before the started line")

    with monkeypatch.context() as patch:
        patch.setattr(composition, "FileSystemExperimentStore", _CrashBeforeStart)
        with pytest.raises(RuntimeError, match="before the started line"):
            _run(workspace, out, a)
    assert (_version(out, "crash_a") / "search.1" / "ledger_execution.json").is_file()
    assert _ledger(workspace) == ()
    code, output = _run(workspace, out, _experiment(workspace, "crash_b"))
    assert code == 1 and "UNMATCHED_BINDING" in output, output
    assert _run(workspace, out, a)[0] == 0


# --- 読込の誤り（Q37）-----------------------------------------------------------------


def test_an_unreadable_policy_registry_stops_the_search_with_exit_code_2(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """研究ポリシーの版の登録簿が読めなければ、読込の照合で止まり終了コード 2（何も書かない）。"""
    registry = workspace.repo / "configs/policies/research/registry.yaml"
    kept = registry.read_bytes()
    try:
        registry.write_text("entries: [\n", encoding="utf-8")
        code, output = _run(workspace, tmp_path, _experiment(workspace, "registry"))
    finally:
        registry.write_bytes(kept)
    assert code == 2, output
    assert not (tmp_path / "runs").exists()
    assert _ledger(workspace) == ()


# --- 意味論の行 #6・#7・#8 ----------------------------------------------------------------


def test_6_the_next_fold_runs_after_a_fold_without_a_candidate(
    workspace: T02Workspace, tmp_path: Path, full: _Full
) -> None:
    out = _seeded(full, tmp_path)
    code, output = _run(workspace, out, _experiment(workspace, "nocand", policy_version=5))
    assert code == 0, output
    directory = _version(out, "nocand")
    selections = read_selections(directory)
    assert sorted(selections) == [0, 1]
    assert all(item.selected_trial_index is None for item in selections.values())
    starts, _ = read_unit_records(directory)
    assert {unit.fold_index for unit in starts} == {0, 1}
    assert all(unit.phase is TrialPhase.TRAIN for unit in starts)
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.search is not None
    assert outcome.search.verdict.value == "BELOW_STANDARD"


def test_7_a_unit_failing_a_post_run_check_is_excluded_and_fails_the_search(
    workspace: T02Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, full: _Full
) -> None:
    """事後検査（P5）が合格でない単位は候補から外れ、実験は `FAILED_POST_RUN_CHECK`（終了 4）。
    合格でない検査の詳細は試行記録に残る（D09 §10.8・§17.7.3 の3）。"""
    original = research_policy.check_evaluation_rule

    def failing(expected: int, observed: int) -> Any:
        return original(expected, observed + 1)

    monkeypatch.setattr(run_experiment, "check_evaluation_rule", failing)
    out = _seeded(full, tmp_path)
    code, output = _run(workspace, out, _experiment(workspace, "postrun"))
    assert code == 4, output
    directory = _version(out, "postrun")
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.status is ExperimentStatus.FAILED_POST_RUN_CHECK
    assert outcome.failed_checks == (PolicyCheck.EVALUATION_RULE_MATCHES,)
    _, runs = read_unit_records(directory)
    record = runs[_unit(0, TrialPhase.TRAIN, 0)]
    assert record.outcome_checks[1].outcome is CheckOutcome.FAILED
    assert '"metric_set_version":3' in record.outcome_checks[1].observed
    for selection in read_selections(directory).values():
        assert {entry[2] for entry in selection.inputs} == {CandidateStatus.EXCLUDED_POST_RUN_CHECK}
    assert _ledger(workspace)[-1].entry.status is ExperimentStatus.FAILED_POST_RUN_CHECK


def test_8_experiment_run_does_not_call_the_holdout_gate(full: _Full) -> None:
    """探索の経路は封印期間の許可判定（`holdout_gate`。後続版）を持たず、許可集合は研究履歴だけ。"""
    assert full.code == 0, full.output
    assert not [name for name in sys.modules if "holdout_gate" in name]
    manifest = read_experiment_manifest(_version(full.out, "full"))
    assert {access.value for access in manifest.allowed_partitions.values()} == {"RESEARCH_HISTORY"}
    checks = {item.check: item.outcome for item in manifest.pre_run_checks}
    assert checks[PolicyCheck.RESEARCH_HISTORY_ONLY] is CheckOutcome.PASSED


def test_24_the_finished_line_copies_the_started_line(full: _Full) -> None:
    """結末の行は開始の行の項目を写し、`status` / `verdict` / `frequency_class` だけが結末記録の値
    になる（D09 §10.12.3 の3）。"""
    opening, closing = full.lines
    outcome = read_experiment_outcome(_version(full.out, "full"))
    assert outcome is not None and outcome.search is not None
    frequency = outcome.search.frequency
    assert (closing.entry.status, closing.entry.verdict, closing.entry.frequency_class) == (
        outcome.status,
        outcome.search.verdict,
        None if frequency is None else frequency.class_name,
    )
    restored = replace(
        closing.entry,
        event=TrialLedgerEvent.STARTED,
        status=None,
        verdict=None,
        frequency_class=None,
    )
    assert encode(restored) == encode(opening.entry)
    assert closing.prev == opening.digest


def test_experiment_report_does_not_write_a_single_run_report_for_a_search(full: _Full) -> None:
    """探索の実験のレポート（D09 §11.5）は段階5 の実装 PR 5 で作る。それまで `experiment report` は
    単一実行の書式で探索の実験のレポートを作らず、引数・読込の誤り（終了コード 2）で止める。"""
    directory = _version(full.out, "full")
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["experiment", "report", "--experiment-dir", str(directory)])
    assert code == 2, stdout.getvalue() + stderr.getvalue()
    assert "探索の実験" in stderr.getvalue()
    assert not (directory / "report.md").exists()


# --- 保存済みの評価の再利用（D07 v2.11 §3・§19.6、D09 §17.7.4 の2・3、§13 の22）---------------


def _metrics_files(out: Path) -> list[Path]:
    return sorted((out / "runs").glob("*/eval/*/METRICS.parquet"))


def test_22_a_reused_evaluation_reads_the_stored_metrics_without_re_evaluating(
    workspace: T02Workspace, tmp_path: Path, full: _Full, monkeypatch: pytest.MonkeyPatch
) -> None:
    """別の実行で保存した評価を再利用する単位は、指標を保存済みの `METRICS` 表から読み、評価を
    やり直さない。選定と判定には読んだ値が渡り、何も無い基点で通した実行と同じ記録になる。"""
    out = _seeded(full, tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a reused evaluation must not be evaluated again (D09 §17.7.4)")

    monkeypatch.setattr(EvaluateRun, "evaluate", refuse)
    code, output = _run(workspace, out, _experiment(workspace, "reused"))
    assert code == 0, output
    directory = _version(out, "reused")
    _, runs = read_unit_records(directory)
    assert all(record.run_reused for record in runs.values())
    original = _version(full.out, "full")
    assert read_selections(directory) == read_selections(original)
    # 集約表は `run_reused`（この実行では全単位が再利用）だけが違う。
    for name in ("trial_units.parquet", "trial_metrics.parquet"):
        new, old = (
            pl.read_parquet(base / "search" / name).drop("run_reused", strict=False)
            for base in (directory, original)
        )
        assert new.equals(old), name
    outcome, expected = read_experiment_outcome(directory), read_experiment_outcome(original)
    assert outcome is not None and outcome.search is not None
    assert expected is not None and expected.search is not None
    assert outcome.search.verdict is expected.search.verdict


def _drop(path: Path) -> None:
    path.unlink()


def _garble(path: Path) -> None:
    path.write_bytes(b"not a parquet file")


def _swap_columns(path: Path) -> None:
    frame = pl.read_parquet(path)
    frame.select(list(reversed(frame.columns))).write_parquet(path)


def _reverse_rows(path: Path) -> None:
    pl.read_parquet(path).reverse().write_parquet(path)


_UNREADABLE_METRICS = {
    "missing": _drop,
    "garbled": _garble,
    "columns": _swap_columns,
    "order": _reverse_rows,
}


@pytest.mark.parametrize("case", sorted(_UNREADABLE_METRICS))
def test_an_unreadable_stored_metrics_table_refuses_the_search_before_anything_is_written(
    workspace: T02Workspace, tmp_path: Path, full: _Full, case: str
) -> None:
    """D09 §17.7.4 の3（選択肢 A）: 記録票の保存の前に確かめ、実験全体を拒否する（終了コード 5。
    記録票・台帳の行を書かない）。再評価で補わない。"""
    out = _seeded(full, tmp_path)
    target = _metrics_files(out)[0]
    _UNREADABLE_METRICS[case](target)
    code, output = _run(workspace, out, _experiment(workspace, f"metrics-{case}"))
    assert code == 5, output
    assert "cannot be read" in output
    assert not _version(out, f"metrics-{case}").exists()
    assert _ledger(workspace) == ()
    if case != "missing":
        assert target.is_file()


def test_the_evaluation_manifest_must_name_the_metrics_it_holds(
    full: _Full, tmp_path: Path
) -> None:
    """識別子が合わない評価 manifest の下の表は読まない（`EvaluationReadFailure`）。"""
    out = _seeded(full, tmp_path)
    target = _metrics_files(out)[0]
    evaluation_dir = target.parent
    run_id = RunId(ContentDigest.sha256(evaluation_dir.parent.parent.name))
    evaluation_id = RunEvaluationId(ContentDigest.sha256(evaluation_dir.name))
    repository = FileSystemResultRepository(root=out)
    read = repository.read_evaluation_metrics(run_id, evaluation_id)
    assert isinstance(read, tuple) and read
    payload = json.loads((evaluation_dir / "evaluation.json").read_text(encoding="utf-8"))
    other = next(p for p in _metrics_files(out) if p.parent != evaluation_dir).parent.name
    payload["run_evaluation_id"] = other
    (evaluation_dir / "evaluation.json").write_text(json.dumps(payload), encoding="utf-8")
    assert isinstance(
        repository.read_evaluation_metrics(run_id, evaluation_id), EvaluationReadFailure
    )


def test_metrics_that_become_unreadable_during_the_search_interrupt_it(
    workspace: T02Workspace, tmp_path: Path, full: _Full, monkeypatch: pytest.MonkeyPatch
) -> None:
    """確認の後に実行中に読めなくなった場合は中断する（終了コード 1）。開始記録と台帳の開始の行
    だけが残り、結末記録も試行記録も無い（D09 §17.7.4 の3、§10.12.4 の d）。"""
    out = _seeded(full, tmp_path)
    started_ledger: list[bool] = []
    original_append = FileSystemExperimentStore.append_trial_ledger
    original_read = FileSystemResultRepository.read_evaluation_metrics

    def append(self: FileSystemExperimentStore, line: Any) -> Any:
        started_ledger.append(True)
        return original_append(self, line)

    def read(self: FileSystemResultRepository, run_id: Any, evaluation_id: Any) -> Any:
        if started_ledger:
            return EvaluationReadFailure(run_id=run_id, detail="the table vanished")
        return original_read(self, run_id, evaluation_id)

    monkeypatch.setattr(FileSystemExperimentStore, "append_trial_ledger", append)
    monkeypatch.setattr(FileSystemResultRepository, "read_evaluation_metrics", read)
    code, output = _run(workspace, out, _experiment(workspace, "vanished"))
    assert code == 1, output
    directory = _version(out, "vanished")
    assert read_experiment_outcome(directory) is None
    starts, runs = read_unit_records(directory)
    assert list(starts) == [_unit(0, TrialPhase.TRAIN, 0)] and runs == {}
    assert [line.entry.event for line in _ledger(workspace)] == [TrialLedgerEvent.STARTED]
