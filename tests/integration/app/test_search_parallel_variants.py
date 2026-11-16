"""標準と候補の組を 2 つの子プロセスで並列に走らせる探索の実験の結合試験（人工データ。D26 §3.6）。

T02 の人工データの作業場（`test_search_v3_path.py` と同じ。2 fold・両組・経路あり）で、同じ実験を
順番と並列で走らせて比べる。

確かめること（D26 §3.6 の番号）:

1. 順番と並列で、`runs/` 配下の全ファイル（`search/`・結末記録・レポート・run と評価の成果物）の
   バイト列と、試行台帳の行が一致する（実行ごとの乱数の文字列は固定する）。研究ポリシー版 5 の
   実験では、子が書く局面の成果物も一致する（D26 §3.8 の 4）。
2. 表の遷移: 片方の子の例外（S1/S2 + E4。終了コード 1、結末記録と台帳の結末の行が無い）、
   結末記録の後に台帳の結末の行が書けない（S4 + E9 → S7）、レポートの作成の失敗（S4 + E9 → S8）、
   同じ版の実行中の印のロックが取れない（E11。専用の終了コード、何も書かない）。子だけの遷移
   （E5・E13・E6・F3）は `tests/unit/app/test_parallel_search.py`。
3. 同じ版の再実行で、止まる前に終わった単位が再利用される（`run_reused`）。
4. 空きメモリが足りないとき、今の順番で走り、その旨を表示する（S0 + E2）。
5. `experiment reproduce` でも 1. と同じ一致（D26-3 (a)）。

人工データだけを使う（実価格・成績の値は確かめない。バイト列・件数・識別子・構造だけを見る）。
"""

from __future__ import annotations

import hashlib
import io
import secrets
import shutil
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app import composition, parallel_search
from odyssey_fx.app.cli.main import main
from odyssey_fx.app.parallel_search import EXECUTION_LOCK_FILE, execution_lock
from odyssey_fx.evaluation.adapters.fs_store import (
    read_experiment_outcome,
    read_trial_ledger_file,
    read_unit_records,
)
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.application.run_experiment import RunExperiment, TrialLedgerStop
from odyssey_fx.evaluation.domain.search import TrialLedgerEvent
from odyssey_fx.evaluation.domain.variant import Variant
from tests.fixtures.acceptance.t02_workspace import T02Workspace, accept_candidate, build_workspace
from tests.fixtures.evaluation import parallel_jobs
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

#: 1 試行（両軸とも値 1 つ）。両 fold で同じ試行が選ばれ、経路は 1 区間になる。
ONE_TRIAL: Final = (
    "    - {instance: m15_ema, parameter: period, values: [10]}\n"
    "    - {instance: stop_level, parameter: lookback, values: [10]}"
)
#: 局面の窓を人工データに合わせて短くした試験用の値（`test_search_v5_regimes.py` と同じ）。
SHORT_REGIMES: Final = (
    "  regimes:\n"
    "    role: DISPLAY_ONLY\n"
    '    timeframe: "1d"\n'
    '    direction: {ema_bars: 2, slope_bars: 1, atr_bars: 2, dead_band_atr_multiple: "0.5"}\n'
    "    volatility: {atr_bars: 2, median_window_bars: 3}\n"
    "    label_at: LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY"
)


@dataclass(frozen=True)
class _Workspace:
    workspace: T02Workspace
    experiment: Path
    name: str
    root: Path

    @property
    def ledger(self) -> Path:
        return self.workspace.repo / "research" / "trial_ledger.jsonl"

    def directory(self, out: Path) -> Path:
        return out / "runs" / "experiments" / self.name / "v1"


def _workspace(root: Path, *, policy_version: int) -> _Workspace:
    workspace = build_workspace(root)
    candidate = accept_candidate(workspace, root / "candidate")
    name = f"par_v{policy_version}"
    if policy_version == 3:
        install_policy_v3(workspace.repo)
    else:
        text = policy_v3_text(
            version=policy_version, replace={"split": TWO_FOLD_SPLIT, "regimes": SHORT_REGIMES}
        )
        write_policy(workspace.repo, "research_policy", policy_version, text)
        append_registry_entry(workspace.repo, "research_policy", policy_version)
    ledger = workspace.repo / "research" / "trial_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"")
    experiment = write_search_experiment(
        workspace.repo,
        f"{name}.yaml",
        experiment_id=name,
        policy_version=policy_version,
        axes=ONE_TRIAL,
        max_trials=1,
        candidate_snapshot=candidate,
    )
    return _Workspace(workspace=workspace, experiment=experiment, name=name, root=root)


@pytest.fixture(scope="module")
def v3(tmp_path_factory: pytest.TempPathFactory) -> _Workspace:
    return _workspace(tmp_path_factory.mktemp("parallel-v3"), policy_version=3)


@pytest.fixture(scope="module")
def v5(tmp_path_factory: pytest.TempPathFactory) -> _Workspace:
    return _workspace(tmp_path_factory.mktemp("parallel-v5"), policy_version=5)


@pytest.fixture(autouse=True)
def _fixed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """実行ごとの乱数を固定し（D26 §3.6 の 1）、空きメモリは足りることにする（S0 + E1）。"""
    monkeypatch.setattr(secrets, "token_hex", lambda size: "ab" * size)
    monkeypatch.setattr(parallel_search, "free_memory_bytes", lambda: 1 << 50)
    yield


def _call(argv: list[str]) -> tuple[int, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue() + stderr.getvalue()


def _run(setup: _Workspace, out: Path, mode: str) -> tuple[int, str]:
    return _call(
        [
            "experiment",
            "run",
            "--experiment",
            str(setup.experiment),
            "--snapshots",
            str(setup.workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(setup.workspace.repo),
            "--variant-mode",
            mode,
        ]
    )


def _reproduce(setup: _Workspace, directory: Path, out: Path, mode: str) -> tuple[int, str]:
    return _call(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(directory),
            "--snapshots",
            str(setup.workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(setup.workspace.repo),
            "--variant-mode",
            mode,
        ]
    )


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _ledger_events(setup: _Workspace) -> list[TrialLedgerEvent]:
    contents = read_trial_ledger_file(setup.ledger)
    assert isinstance(contents, TrialLedgerContents), contents
    return [line.entry.event for line in contents.lines]


def _same_bytes_in_both_modes(setup: _Workspace) -> tuple[dict[str, str], Path]:
    """順番 → 並列の順に同じ置き場で走らせ、両方の成果物の木（相対パス → SHA-256）を比べる。"""
    out = setup.root / "out-compare"
    setup.ledger.write_bytes(b"")
    code, output = _run(setup, out, "serial")
    assert code == 0, output
    serial_tree, serial_ledger = _tree(out), setup.ledger.read_bytes()
    shutil.move(out, setup.root / "out-compare-serial")
    setup.ledger.write_bytes(b"")
    code, output = _run(setup, out, "parallel")
    assert code == 0, output
    assert "2 つの子プロセスで並列に走らせる" in output
    parallel_tree = _tree(out)
    assert parallel_tree == serial_tree
    assert setup.ledger.read_bytes() == serial_ledger
    return parallel_tree, out


# --- 1・5: 順番と並列でバイト列が一致する --------------------------------------------------


def test_parallel_and_serial_write_the_same_bytes(v3: _Workspace) -> None:
    tree, out = _same_bytes_in_both_modes(v3)
    directory = f"runs/experiments/{v3.name}/v1"
    for variant in Variant:
        assert any(key.startswith(f"{directory}/search/{variant.value}/") for key in tree)
    assert f"{directory}/experiment_outcome.json" in tree
    assert f"{directory}/{EXECUTION_LOCK_FILE}" in tree
    assert _ledger_events(v3) == [TrialLedgerEvent.STARTED, TrialLedgerEvent.FINISHED]
    # 終わった後はロックが外れている（子が残っていない）。
    with execution_lock(v3.directory(out)):
        pass


def test_the_children_write_the_same_regime_artifacts(v5: _Workspace) -> None:
    """研究ポリシー版 5: 局面の成果物を子が書き、結末記録のダイジェストも一致する（§3.8 の 4）。"""
    tree, _ = _same_bytes_in_both_modes(v5)
    directory = f"runs/experiments/{v5.name}/v1/search"
    assert {f"{directory}/regimes_standard.json", f"{directory}/regimes_candidate.json"} <= set(
        tree
    )


def test_a_parallel_reproduction_writes_the_same_report(v3: _Workspace) -> None:
    """D26-3 (a): 並列の再現の記録と run の成果物は、順番の再現と同じバイト列。"""
    out = v3.root / "out-reproduce-source"
    v3.ledger.write_bytes(b"")
    code, output = _run(v3, out, "serial")
    assert code == 0, output
    serial_out = v3.root / "reproduce"
    code, output = _reproduce(v3, v3.directory(out), serial_out, "serial")
    assert code == 0, output
    serial_tree = _tree(serial_out)
    shutil.move(serial_out, v3.root / "reproduce-serial")
    code, output = _reproduce(v3, v3.directory(out), serial_out, "parallel")
    assert code == 0, output
    assert "並列に再現する" in output
    assert _tree(serial_out) == serial_tree
    assert "reproduction.json" in serial_tree
    # 元の版の実行中の印のロックが取れなければ、再現も何も書かずに拒否する（仮置き 11）。
    with execution_lock(v3.directory(out)):
        code, output = _reproduce(v3, v3.directory(out), v3.root / "reproduce-locked", "serial")
    assert code == 10, output
    assert not (v3.root / "reproduce-locked").exists()


# --- 4: 空きメモリが足りない（S0 + E2）---------------------------------------------------


def test_too_little_free_memory_falls_back_to_the_serial_order(
    v3: _Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parallel_search, "free_memory_bytes", lambda: 1024)
    out = v3.root / "out-memory"
    v3.ledger.write_bytes(b"")
    code, output = _run(v3, out, "parallel")
    assert code == 0, output
    assert "空きメモリが足りない" in output and "順番に走らせる" in output
    assert "2 つの子プロセスで並列に走らせる" not in output


# --- 2: 表の遷移 -------------------------------------------------------------------------


def test_a_held_execution_lock_refuses_without_writing(v3: _Workspace) -> None:
    """E11: 同じ版の実行中の印のロックが取れなければ、何も書かずに専用の終了コード 10。"""
    out = v3.root / "out-locked"
    directory = v3.directory(out)
    v3.ledger.write_bytes(b"")
    with execution_lock(directory):
        code, output = _run(v3, out, "parallel")
        assert code == 10, output
        assert "拒否" in output and "still running" in output
        assert sorted(path.name for path in directory.iterdir()) == [EXECUTION_LOCK_FILE]
        assert v3.ledger.read_bytes() == b""
        code, output = _run(v3, out, "serial")
        assert code == 10, output


def test_a_failed_child_leaves_an_execution_without_outcome_that_a_rerun_reuses(
    v3: _Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1/S2 + E4 → S6（終了コード 1。結末記録と台帳の結末の行が無い）→ 再実行で再利用（3）。"""
    out = v3.root / "out-failed"
    directory = v3.directory(out)
    v3.ledger.write_bytes(b"")
    monkeypatch.setattr(
        composition, "search_variant_work", parallel_jobs.failing_candidate_after_first_unit
    )
    code, output = _run(v3, out, "parallel")
    assert code == 1, output
    assert "candidate の組の子が例外で終わった" in output
    assert read_experiment_outcome(directory) is None
    assert (directory / "search" / "ledger_execution.json").is_file()
    assert _ledger_events(v3) == [TrialLedgerEvent.STARTED]
    _, failed_runs = read_unit_records(directory, Variant.CANDIDATE)
    assert len(failed_runs) == 1
    (first_unit,) = failed_runs

    monkeypatch.undo()
    monkeypatch.setattr(secrets, "token_hex", lambda size: "cd" * size)
    monkeypatch.setattr(parallel_search, "free_memory_bytes", lambda: 1 << 50)
    code, output = _run(v3, out, "parallel")
    assert code == 0, output
    _, runs = read_unit_records(directory, Variant.CANDIDATE)
    assert runs[first_unit].run_reused
    assert _ledger_events(v3) == [
        TrialLedgerEvent.STARTED,
        TrialLedgerEvent.STARTED,
        TrialLedgerEvent.FINISHED,
    ]


def test_a_ledger_stop_after_the_outcome_keeps_the_outcome(
    v3: _Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4 + E9 → S7: 結末記録は残り、台帳の結末の行は無い（終了コード 1。今と同じ）。"""

    def refuse(self: RunExperiment, *args: object, **kwargs: object) -> None:
        raise TrialLedgerStop("injected ledger refusal (test)")

    monkeypatch.setattr(RunExperiment, "_finish_ledger", refuse)
    out = v3.root / "out-ledger"
    v3.ledger.write_bytes(b"")
    code, output = _run(v3, out, "parallel")
    assert code == 1, output
    assert "injected ledger refusal" in output
    assert read_experiment_outcome(v3.directory(out)) is not None
    assert _ledger_events(v3) == [TrialLedgerEvent.STARTED]


def test_a_failed_report_keeps_the_outcome_and_the_finished_line(
    v3: _Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4 + E9 → S8: 結末記録と台帳の結末の行は残り、レポートだけが無い（終了コード 1）。"""

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected report failure (test)")

    monkeypatch.setattr(composition, "write_search_report", fail)
    out = v3.root / "out-report"
    v3.ledger.write_bytes(b"")
    code, output = _run(v3, out, "parallel")
    assert code == 1, output
    assert "レポートを書けなかった" in output
    assert read_experiment_outcome(v3.directory(out)) is not None
    assert not (v3.directory(out) / "report.md").exists()
    assert _ledger_events(v3) == [TrialLedgerEvent.STARTED, TrialLedgerEvent.FINISHED]
