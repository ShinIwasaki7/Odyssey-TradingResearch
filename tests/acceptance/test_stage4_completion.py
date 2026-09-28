"""段階4 の完了条件を人工データで確かめる（全体計画 §8.2、D07 §17.1・§21.4、D08 §2.3）。

全体計画 §8.2 の段階4 の完了条件は次の3つである。

1. **結果から設定と入力を特定できる**: 結末記録から記録票・`run_id`・評価の識別子へ辿れ、
   記録票の `resolved_files` の本文から同じ `ConfigDigest` を再計算できる。
2. **別プロセスで再現可能**: `experiment reproduce` を別の OS のプロセスで起こし、判定が
   `REPRODUCED`（`run_id` と `result_digest` が結末記録と一致）になる。
3. **失敗 / 0取引も説明できる**: (a) 研究ポリシーの違反（複雑性の上限を1つ超える設定）で run
   せず、結末記録とレポートに検査名と観測値が出る。(b) run の失敗（`FAILED_CAPABILITY`）で評価が
   `REJECTED` になり、レポートの先頭に理由が出る。(c) 取引が0件の run で評価が `COMPLETED`、
   取引に依存する指標が `NO_TRADES` の値なしになり、レポートが「取引が0件」と書く。

D08 §2.3 の規約に従う。

- **コマンドを `subprocess` で起こす**。`odyssey-fx experiment run` と `experiment reproduce` を
  それぞれ別の OS のプロセスとして起こす（D07 §21.1 の「別プロセス」）。
- **検証戦略 B をコマンド経由で通す**。T02 の人工データを受け入れて承認した作業場
  （`tests/fixtures/acceptance/t02_workspace.py`）で、書式 v2 の実験設定から通す。
- **実データは使わない**（実データの run は `realdata` マーカーのテストと
  `docs/runs/stage4_realdata_run.md`）。

失敗と0取引の3経路の実験設定は、同梱の設定（`strategy_b_t02_d1_2s.yaml`）の1か所だけを書き
換えて作る。書き換えた場所が失敗の原因であることを読み取れるようにするためである。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.config.experiment_v2 import TEXT_ROLES, experiment_v2_from_texts
from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.trace.manifest import config_digest_of
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_MANIFEST_FILE,
    EXPERIMENT_OUTCOME_FILE,
    REPORT_FILE,
    REPRODUCTION_FILE,
    read_experiment_manifest,
)
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.acceptance.t02_workspace import T02Workspace, build_workspace

#: 完走する実験（検証戦略 B・遅延シナリオ d1_2s。待機と再開を含む経路）。
_COMPLETED = "configs/experiments/strategy_b_t02_d1_2s.yaml"


@dataclass(frozen=True, slots=True)
class _Run:
    """`experiment run` を別プロセスで1回起こした結果。"""

    returncode: int
    output: str
    artifacts: Path
    directory: Path

    def json(self, name: str) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads((self.directory / name).read_text(encoding="utf-8"))
        return payload

    @property
    def report(self) -> str:
        return (self.directory / REPORT_FILE).read_text(encoding="utf-8")


def _odyssey(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """`odyssey-fx` を**別の OS のプロセス**として起こす（D08 §2.3 の 1）。"""
    return subprocess.run(
        [sys.executable, "-m", "odyssey_fx.app.cli.main", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def _variant(workspace: T02Workspace, name: str, replacements: list[tuple[str, str]]) -> Path:
    """同梱の設定の `id` と、指定した箇所だけを書き換えた実験設定を作業場に置く。"""
    text = (workspace.repo / _COMPLETED).read_text(encoding="utf-8")
    for old, new in [("id: strategy_b_t02_d1_2s", f"id: {name}"), *replacements]:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = workspace.repo / "configs/experiments" / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _experiment_run(workspace: T02Workspace, experiment: Path, artifacts: Path) -> _Run:
    artifacts.mkdir(parents=True, exist_ok=True)
    process = _odyssey(
        [
            "experiment",
            "run",
            "--experiment",
            str(experiment),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(artifacts),
            "--repo-root",
            str(workspace.repo),
        ],
        cwd=artifacts,
    )
    name = experiment.stem
    return _Run(
        returncode=process.returncode,
        output=process.stdout + process.stderr,
        artifacts=artifacts,
        directory=artifacts / "runs/experiments" / name / "v1",
    )


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> T02Workspace:
    """T02 の人工データを受け入れて承認した作業場（テスト間で共有する）。"""
    return build_workspace(tmp_path_factory.mktemp("stage4-acceptance"))


@pytest.fixture(scope="module")
def completed(workspace: T02Workspace, tmp_path_factory: pytest.TempPathFactory) -> _Run:
    """完走する実験を別プロセスで1回通す。"""
    run = _experiment_run(
        workspace, workspace.repo / _COMPLETED, tmp_path_factory.mktemp("completed")
    )
    assert run.returncode == 0, run.output
    return run


# --- 1. 結果から設定と入力を特定できる --------------------------------------------


def test_the_outcome_leads_to_the_manifest_the_run_and_the_evaluation(completed: _Run) -> None:
    """結末記録から記録票・`run_id`・評価の識別子へ辿れる（D07 §19.1・§19.5）。"""
    manifest = completed.json(EXPERIMENT_MANIFEST_FILE)
    outcome = completed.json(EXPERIMENT_OUTCOME_FILE)

    assert outcome["experiment_id"] == manifest["experiment_id"]
    assert outcome["status"] == "COMPLETED"
    run_directory = completed.artifacts / "runs" / str(outcome["run_id"])
    run_manifest = json.loads((run_directory / "manifest.json").read_text(encoding="utf-8"))
    evaluation = json.loads(
        (run_directory / "eval" / str(outcome["run_evaluation_id"]) / "evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_manifest["run_id"] == outcome["run_id"]
    assert run_manifest["config_digest"] == manifest["expected_config_digest"]
    assert evaluation["run_id"] == outcome["run_id"]
    assert evaluation["result_digest"] == outcome["result_digest"]
    assert evaluation["status"] == outcome["evaluation_status"] == "COMPLETED"


def test_the_config_digest_is_rebuilt_from_the_saved_texts_alone(completed: _Run) -> None:
    """記録票の `resolved_files` の本文だけから同じ `ConfigDigest` を再計算できる（D07 §19.2）。

    元の実験設定ファイル・戦略ファイルは読まない（ファイルを消したり書き換えたりした後でも、
    記録票から設定を特定できる）。
    """
    manifest = read_experiment_manifest(completed.directory)
    loaded = experiment_v2_from_texts(
        {role: manifest.file(role).text for role in TEXT_ROLES},
        {item.role.removeprefix("symbol:"): item.text for item in manifest.symbol_files()},
        registry=INITIAL_CATALOG,
        metric_set_versions=frozenset({METRIC_SET_VERSION}),
    )
    experiment = loaded.experiment
    environment = loaded.environment
    compiled = composition.compile_experiment_strategy(experiment, dict(environment.timeframe_defs))
    config = RunConfig(
        run_interval=experiment.run_interval,
        snapshot_ref=experiment.snapshot_ref,
        compiled_ref=compiled.compiled_ref,
        account=experiment.account,
        risk_policy_ref=experiment.policy_ref("risk"),
        execution_policy_ref=experiment.policy_ref("execution"),
        cost_model_ref=experiment.policy_ref("cost"),
        conversion_policy_ref=experiment.policy_ref("conversion"),
        delay_scenario_ref=experiment.policy_ref("delay"),
        execution_series=experiment.execution_series,
        seed=experiment.seed,
    )
    rebuilt = config_digest_of(
        config,
        symbol_spec_ref=composition.symbol_spec_ref_of(
            environment.symbol_specs[experiment.execution_series.symbol]
        ),
        calendar_ref=composition.calendar_ref_of(environment.calendar),
        timeframe_def_refs=tuple(
            sorted((item.ref for item in environment.timeframe_defs.values()), key=str)
        ),
    )
    assert rebuilt == manifest.expected_config_digest
    assert all(item.intact for item in manifest.resolved_files)


def test_the_report_identifies_the_settings_and_inputs(completed: _Run) -> None:
    """レポートの「設定と入力の特定」に識別子と再現のコマンドの1行が出る（D07 §22.1 の順6）。"""
    manifest = completed.json(EXPERIMENT_MANIFEST_FILE)
    outcome = completed.json(EXPERIMENT_OUTCOME_FILE)
    report = completed.report

    assert report.startswith("# 実験レポート: strategy_b_t02_d1_2s 版 1\n")
    assert "**採用可**" in report
    for value in (
        manifest["experiment_id"],
        manifest["snapshot_id"],
        manifest["expected_config_digest"],
        outcome["run_id"],
        outcome["result_digest"],
        outcome["code_digest"],
        outcome["lock_digest"],
        outcome["env_digest"],
    ):
        assert value in report, value
    assert (
        "odyssey-fx experiment reproduce --experiment-dir runs/experiments/strategy_b_t02_d1_2s/v1"
        in report
    )
    # 決定論（D07 §22.1）: 絶対パスを入れない。
    assert str(completed.artifacts) not in report


# --- 2. 別プロセスで再現可能 --------------------------------------------------------


def test_the_experiment_is_reproduced_in_a_separate_process(
    workspace: T02Workspace, completed: _Run, tmp_path: Path
) -> None:
    """`experiment reproduce` を別の OS のプロセスで起こし、判定が `REPRODUCED`（D07 §21.4）。"""
    out = tmp_path / "reproduction"
    process = _odyssey(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(completed.directory),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(workspace.repo),
        ],
        cwd=tmp_path,
    )

    assert process.returncode == 0, process.stdout + process.stderr
    reproduction = json.loads((out / REPRODUCTION_FILE).read_text(encoding="utf-8"))
    outcome = completed.json(EXPERIMENT_OUTCOME_FILE)
    assert reproduction["verdict"] == "REPRODUCED"
    assert reproduction["observed_run_id"] == outcome["run_id"]
    assert reproduction["observed_result_digest"] == outcome["result_digest"]


# --- 3. 失敗 / 0取引も説明できる ------------------------------------------------------


def test_a_policy_violation_is_explained_without_running(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """(a) 複雑性の上限を1つ超えると run せず、結末記録とレポートに検査名と観測値が出る。

    検証戦略 B の使用箇所は 12（D07 §20.4）。上限を 11 にした研究ポリシーの版 2 を置いて
    指す（同じ版の中身を書き換えると、同じ作業場の他の実験の前提が変わるため）。
    """
    policy = workspace.repo / "configs/policies/research/research_policy_v2.yaml"
    policy.write_text(
        (workspace.repo / "configs/policies/research/research_policy_v1.yaml")
        .read_text(encoding="utf-8")
        .replace("\nversion: 1\n", "\nversion: 2\n")
        .replace("instances: 36", "instances: 11"),
        encoding="utf-8",
    )
    experiment = _variant(
        workspace,
        "stage4_policy_violation",
        [
            (
                'research_policy: {id: "research_policy", version: 1}',
                'research_policy: {id: "research_policy", version: 2}',
            )
        ],
    )

    run = _experiment_run(workspace, experiment, tmp_path / "artifacts")

    assert run.returncode == 3, run.output
    outcome = run.json(EXPERIMENT_OUTCOME_FILE)
    assert outcome["status"] == "REJECTED_BY_POLICY"
    assert outcome["failed_checks"] == ["complexity_within_limits"]
    assert outcome["run_id"] is None
    assert [path.name for path in (run.artifacts / "runs").iterdir()] == ["experiments"]
    manifest = run.json(EXPERIMENT_MANIFEST_FILE)
    check = next(
        item for item in manifest["pre_run_checks"] if item["check"] == "complexity_within_limits"
    )
    report = run.report
    first_lines = report.split("## 2.")[0]
    assert "**採用不可**" in first_lines and "事前検査" in first_lines
    assert "complexity_within_limits" in report
    assert check["observed"] in report.replace("\\|", "|")
    assert check["expected"] in report.replace("\\|", "|")


def test_a_run_that_fails_the_capability_check_is_explained_at_the_top(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """(b) run の失敗（`FAILED_CAPABILITY`）で評価が `REJECTED`、レポートの先頭に理由が出る。

    執行する銘柄の許容不利約定幅を書かない（別の銘柄の値だけを書く）と、実行前のデータ能力
    検査が実行不可と判定する（D06 §10.5 の手順2a）。
    """
    experiment = _variant(
        workspace,
        "stage4_capability_failure",
        [('    USDJPY: "0.05"', '    EURUSD: "0.05"')],
    )

    run = _experiment_run(workspace, experiment, tmp_path / "artifacts")

    assert run.returncode == 0, run.output  # 実験としては最後まで進んだ（D07 §21.3）
    outcome = run.json(EXPERIMENT_OUTCOME_FILE)
    assert outcome["status"] == "COMPLETED"
    assert outcome["run_status"] == "FAILED_CAPABILITY"
    assert outcome["evaluation_status"] == "REJECTED"
    report = run.report
    conclusion = report.split("## 2.")[0]
    assert "**採用不可**" in conclusion
    assert "FAILED_CAPABILITY" in conclusion
    why = report.split("## 2.")[1].split("## 3.")[0]
    assert "DATA_ERROR" in why
    assert "adverse fill limit for USDJPY" in why
    assert "指標は" not in conclusion  # 結論は状態と理由だけで、数値の表は後ろ


def test_a_run_without_trades_is_explained(workspace: T02Workspace, tmp_path: Path) -> None:
    """(c) 取引0件の run で評価が `COMPLETED`、取引に依存する指標が `NO_TRADES` の値なし。

    run 区間を最初の2日（最初の注文より前）に縮める。レポートは「取引が0件」と書く。
    """
    experiment = _variant(
        workspace,
        "stage4_zero_trades",
        [('end: "2015-01-16T22:00:00Z"', 'end: "2015-01-06T22:00:00Z"')],
    )

    run = _experiment_run(workspace, experiment, tmp_path / "artifacts")

    assert run.returncode == 0, run.output
    outcome = run.json(EXPERIMENT_OUTCOME_FILE)
    assert outcome["status"] == "COMPLETED"
    assert outcome["run_status"] == "COMPLETED"
    assert outcome["evaluation_status"] == "COMPLETED"
    report = run.report
    why = report.split("## 2.")[1].split("## 3.")[0]
    assert "取引が0件だったので値なしの指標がある" in why
    unavailable = report.split("## 3.")[1].split("## 4.")[0]
    for metric in (
        "CLOSED_TRADE_PROFIT",
        "WIN_RATE",
        "PROFIT_FACTOR",
        "AVERAGE_TRADE_PROFIT",
    ):
        line = next(item for item in unavailable.splitlines() if metric in item)
        assert "NO_TRADES" in line, line
