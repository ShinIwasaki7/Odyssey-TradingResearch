"""検証戦略 B を書式 v2 の実験設定から `run` コマンドで通す（D07 §17.2 の実装 PR 2・§18）。

D07 §17.2 が実装 PR 2 の統合テストに求めるのは、**検証戦略 B を `run` コマンド経由で通し、
段階3 のエンジン直呼びの判断履歴と `run_id` 列以外が一致すること**である。

- コマンド経由: 人工データ（T02）を `data accept` → `classify` → `approve` で承認済み
  snapshot にし、書式 v2 の実験設定（`configs/experiments/strategy_b_t02_*.yaml`）を
  `run` に渡す（`tests/fixtures/acceptance/t02_workspace.py`）。遅延シナリオは合成
  （`app.composition`）が市場データへ当てる（D07 §18.3）。
- エンジン直呼び: 段階3 の受入テストが使う組み立て（`tests/fixtures/acceptance/t02_run.py`）。
  同じ素の足に遅延を生成器の側で当て、エンジンの利用口を直接呼ぶ。

判断履歴には snapshot の識別子とポリシーの版参照が載る（根拠記録・リスク評価の列）。エンジン
直呼びの組み立てはそれらを固定の値で作るので、突き合わせでは**実験設定から解決した同じ参照を
渡して**走らせ直す（`t02_run.build_case` の `snapshot_ref` / `policy_refs`）。コードと環境の
ダイジェストは経路ごとに違うままなので、`run_id` は違う。それ以外の列（判断履歴の19表の
すべての列）が一致すれば、書式 v2 が段階3 の宣言と遅延シナリオを同じ意味に解決し、合成が
遅延を同じ規則で当てたことになる。

ケースは遅延なし（`delay_scenario` を書かない形）と、系列全体の遅延・1本だけの遅延の3つを
通す。4ケースの差分そのものは段階3 の意味論テスト（`tests/semantics/backtest/
test_delay_scenarios.py`）が確かめている。待機期限を超える遅延（`d1_25h`）は規則の型が
`d1_2s` と同じ（系列全体の固定遅延）で値だけが違うので、読込の単体テストで宣言の一致だけを
確かめる（`tests/unit/app/test_config_experiment_v2.py`）。
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.app.config.experiment_v2 import load_experiment_v2
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.evaluation.adapters.fs_store import FileSystemTraceSink, run_directory
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.acceptance.t02_run import build_case
from tests.fixtures.acceptance.t02_workspace import T02Workspace, build_workspace
from tests.fixtures.backtest.harness import RunOutput

#: 統合テストで通すケース（上の docstring）。
_CASES = ("none", "d1_2s", "d1_bar_hold")


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> T02Workspace:
    """T02 の人工データを受け入れて承認した作業場（ケース間で共有する）。"""
    return build_workspace(tmp_path_factory.mktemp("t02-workspace"))


def _tables(directory: Path) -> dict[str, list[dict[str, object]]]:
    """判断履歴の19表を、`run_id` 列を除いた辞書の列として読む。"""
    tables: dict[str, list[dict[str, object]]] = {}
    for table in TraceTable:
        frame = pl.read_parquet(directory / f"{table.value}.parquet")
        assert "run_id" in frame.columns, table
        tables[table.value] = frame.drop("run_id").to_dicts()
    return tables


def _engine_run(case: str, workspace: T02Workspace) -> RunOutput:
    """エンジン直呼びの run（判断履歴に載る識別を、実験設定から解決したものに揃える）。"""
    experiment = load_experiment_v2(
        workspace.experiment(case),
        repo_root=workspace.repo,
        registry=INITIAL_CATALOG,
        metric_set_versions=frozenset({METRIC_SET_VERSION}),
    ).experiment
    return build_case(
        case, snapshot_ref=experiment.snapshot_ref, policy_refs=experiment.policy_refs
    )


def _saved_tables(output: RunOutput, root: Path) -> dict[str, list[dict[str, object]]]:
    """エンジン直呼びの run を同じ書き出し口で保存して読み戻す。"""
    sink = FileSystemTraceSink(root=root, run_id=output.result.run_id)
    for table in TraceTable:
        sink.write(table, output.rows(table))
    return _tables(run_directory(root, output.result.run_id))


@pytest.mark.parametrize("case", _CASES)
def test_the_run_command_reproduces_the_engine_trace(
    case: str, workspace: T02Workspace, tmp_path: Path
) -> None:
    """コマンド経由の判断履歴が、`run_id` 列を除いてエンジン直呼びと一致する（D07 §17.2）。"""
    artifacts = tmp_path / "artifacts"
    run_id = workspace.run(case, artifacts)
    command = _tables(artifacts / "runs" / run_id)
    output = _engine_run(case, workspace)
    engine = _saved_tables(output, tmp_path / "engine")

    assert run_id != str(output.result.run_id)
    assert output.result.status is RunStatus.COMPLETED
    for table in TraceTable:
        assert command[table.value] == engine[table.value], table
    # 比較が空の表どうしで成り立っただけにならないよう、検証戦略 B の記録が出ていることも見る。
    assert command[TraceTable.EVALUATIONS.value]
    assert command[TraceTable.WAIT_EVENTS.value] or case == "none"
