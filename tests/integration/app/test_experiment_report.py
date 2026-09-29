"""人間向けレポート（`report.md`）と `experiment report` コマンドをコマンド経由で通す（D07 §22）。

実装 PR 4 の統合テスト（D07 §17.2）。人工データ（T02）を受け入れて承認した作業場で、書式 v2 の
実験設定から `experiment run` を1回通し、その成果物に対して次を確かめる。

- `experiment run` が最後にレポートを書き、節の並びが D07 §22.1 の表のとおりである。
- **決定論**: 同じ成果物を別の場所へ写して作り直しても、同じバイト列になる（絶対パス・時刻を
  入れない）。
- `experiment report` の書き込み規則: 内容が同じなら何もしない、違えば `report.<n>.md` へ退避して
  から書く。同じ版の再実行は、旧い結末記録と同じ連番でレポートも退避する（D07 §19.3・§22.1）。
- 結末記録が無い（途中で止まった）版のディレクトリからも記録票だけで作り直せ、先頭に
  「結末記録が無い＝途中で止まった」と書く。
- 引数・読込の誤りは終了コード 2（D07 §21.3 の表）。
"""

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_MANIFEST_FILE,
    EXPERIMENT_OUTCOME_FILE,
    REPORT_FILE,
)
from tests.fixtures.acceptance.t02_workspace import T02Workspace, build_workspace

_CASE = "d1_2s"
_VERSION_DIR = "runs/experiments/strategy_b_t02_d1_2s/v1"

#: D07 §22.1 の表の6つの見出し（この順に並ぶ）。
_HEADINGS = (
    "## 1. 結論",
    "## 2. なぜそうなったか",
    "## 3. 値なしの指標",
    "## 4. 指標",
    "## 5. 集計",
    "## 6. 設定と入力の特定",
)


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _run_argv(workspace: T02Workspace, artifacts: Path) -> list[str]:
    return [
        "experiment",
        "run",
        "--experiment",
        str(workspace.experiment(_CASE)),
        "--snapshots",
        str(workspace.repo / "data/snapshots"),
        "--out",
        str(artifacts),
        "--repo-root",
        str(workspace.repo),
    ]


def _report(directory: Path) -> tuple[int, str, str]:
    return _invoke(["experiment", "report", "--experiment-dir", str(directory)])


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> T02Workspace:
    return build_workspace(tmp_path_factory.mktemp("t02-report"))


@pytest.fixture(scope="module")
def completed(workspace: T02Workspace, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`experiment run` を1回通した成果物の根（書き換えない。書き換えるテストは写してから）。"""
    artifacts = tmp_path_factory.mktemp("report-artifacts")
    code, output, error = _invoke(_run_argv(workspace, artifacts))
    assert code == 0, output + error
    return artifacts


def _copy(completed: Path, target: Path) -> Path:
    shutil.copytree(completed / "runs", target / "runs")
    return target / _VERSION_DIR


def test_the_experiment_run_writes_the_report_in_the_order_of_the_design(completed: Path) -> None:
    """`experiment run` の最後にレポートを書く。見出しは D07 §22.1 の順1〜6 のとおり。"""
    text = (completed / _VERSION_DIR / REPORT_FILE).read_text(encoding="utf-8")

    positions = [text.index(heading) for heading in _HEADINGS]
    assert positions == sorted(positions)
    conclusion = text[positions[0] : positions[1]]
    assert "**採用可**" in conclusion
    assert "- 実験の状態: `COMPLETED`" in conclusion
    assert "- run の状態: `COMPLETED`" in conclusion
    assert "- 評価の状態: `COMPLETED`" in conclusion
    metrics = text[positions[3] : positions[4]]
    # 採用指標と参考値を分けて出す（D07 §5.3）。参考値は採用指標の表に入らない。
    adopted, reference = metrics.split("### 参考値")
    assert "NET_PROFIT" in adopted and "END_EQUITY_MTM" not in adopted
    assert "END_EQUITY_MTM" in reference and "HYPOTHETICAL_CLOSED_PROFIT" in reference


def test_the_report_is_the_same_bytes_wherever_the_artifacts_are(
    completed: Path, tmp_path: Path
) -> None:
    """同じ成果物からは同じバイト列（D07 §22.1 の決定論）。別の場所へ写して作り直しても同じ。"""
    original = (completed / _VERSION_DIR / REPORT_FILE).read_bytes()
    directory = _copy(completed, tmp_path / "elsewhere")
    (directory / REPORT_FILE).unlink()

    code, output, error = _report(directory)

    assert code == 0, output + error
    assert (directory / REPORT_FILE).read_bytes() == original
    assert str(completed) not in original.decode("utf-8")


def test_an_identical_report_is_left_as_is(completed: Path, tmp_path: Path) -> None:
    """既存の `report.md` と内容が同じなら何もしない（退避も作らない）。"""
    directory = _copy(completed, tmp_path)
    before = (directory / REPORT_FILE).stat().st_mtime_ns

    code, output, _ = _report(directory)

    assert code == 0
    assert "内容が同じ" in output
    assert (directory / REPORT_FILE).stat().st_mtime_ns == before
    assert not (directory / "report.1.md").exists()


def test_a_different_report_is_kept_before_it_is_rewritten(completed: Path, tmp_path: Path) -> None:
    """内容が違えば `report.<n>.md` へ退避してから書く（旧い記録は消さない）。"""
    directory = _copy(completed, tmp_path)
    fresh = (directory / REPORT_FILE).read_text(encoding="utf-8")
    (directory / REPORT_FILE).write_text("# 手で書き換えたレポート\n", encoding="utf-8")

    code, output, _ = _report(directory)

    assert code == 0
    assert "退避" in output
    assert (directory / "report.1.md").read_text(encoding="utf-8") == "# 手で書き換えたレポート\n"
    assert (directory / REPORT_FILE).read_text(encoding="utf-8") == fresh


def test_a_stopped_experiment_is_reported_from_the_manifest_alone(
    completed: Path, tmp_path: Path
) -> None:
    """結末記録が無い版のディレクトリは記録票だけから作り直し、先頭に「途中で止まった」と書く。"""
    directory = _copy(completed, tmp_path)
    (directory / EXPERIMENT_OUTCOME_FILE).unlink()
    (directory / REPORT_FILE).unlink()

    code, output, error = _report(directory)

    assert code == 0, output + error
    text = (directory / REPORT_FILE).read_text(encoding="utf-8")
    conclusion = text.split("## 2.")[0]
    assert "**採用不可**" in conclusion
    assert "結末記録が無い＝途中で止まった" in conclusion
    assert "再現する結果（run と評価）が無い" in text


@pytest.mark.parametrize(
    "edit",
    ["empty", "other digest"],
)
def test_an_evaluation_manifest_that_is_not_the_recorded_one_is_unreadable(
    completed: Path, tmp_path: Path, edit: str
) -> None:
    """評価 manifest が結末記録の評価と合わなければ「読めない」とし、採用可にしない。

    項目の欠けた manifest（`{}`）や、結果のダイジェストの違う manifest を読めたことにすると、
    取り違えた成果物の数値が「採用可」として出る（PR #48 の Codex 第3巡）。
    """
    directory = _copy(completed, tmp_path)
    outcome = json.loads((directory / EXPERIMENT_OUTCOME_FILE).read_text(encoding="utf-8"))
    evaluation = (
        tmp_path
        / "runs"
        / str(outcome["run_id"])
        / "eval"
        / str(outcome["run_evaluation_id"])
        / "evaluation.json"
    )
    payload = json.loads(evaluation.read_text(encoding="utf-8"))
    if edit == "empty":
        payload = {}
    else:
        payload["result_digest"] = "0" * 64
    evaluation.write_text(json.dumps(payload), encoding="utf-8")

    code, output, error = _report(directory)

    assert code == 0, output + error
    text = (directory / REPORT_FILE).read_text(encoding="utf-8")
    conclusion = text.split("## 2.")[0]
    assert "**採用不可**" in conclusion
    assert "評価の成果物を読めない" in conclusion
    assert "NET_PROFIT" not in text


@pytest.mark.parametrize("edit", ["missing", "other status"])
def test_an_unreadable_run_manifest_is_not_adoptable(
    completed: Path, tmp_path: Path, edit: str
) -> None:
    """run manifest が無い・結末記録と状態が食い違うなら採用可にしない（PR #48 の Codex 第4巡）。"""
    directory = _copy(completed, tmp_path)
    outcome = json.loads((directory / EXPERIMENT_OUTCOME_FILE).read_text(encoding="utf-8"))
    manifest = tmp_path / "runs" / str(outcome["run_id"]) / "manifest.json"
    if edit == "missing":
        manifest.unlink()
    else:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["status"] = "FAILED_RUNTIME"
        manifest.write_text(json.dumps(payload), encoding="utf-8")

    code, output, error = _report(directory)

    assert code == 0, output + error
    conclusion = (directory / REPORT_FILE).read_text(encoding="utf-8").split("## 2.")[0]
    assert "**採用不可**" in conclusion
    assert "run manifest を読めない" in conclusion


def test_rerunning_the_same_version_keeps_the_report_with_the_outcome(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    """同じ版の再実行は、旧い結末記録と同じ連番で旧いレポートも退避する（D07 §19.3・§22.1）。"""
    artifacts = tmp_path / "artifacts"
    assert _invoke(_run_argv(workspace, artifacts))[0] == 0
    directory = artifacts / _VERSION_DIR
    first = (directory / REPORT_FILE).read_text(encoding="utf-8")

    code, output, error = _invoke(_run_argv(workspace, artifacts))

    assert code == 0, output + error
    assert (directory / "experiment_outcome.1.json").is_file()
    assert (directory / "report.1.md").read_text(encoding="utf-8") == first
    second = (directory / REPORT_FILE).read_text(encoding="utf-8")
    assert "既存の run 成果物を再利用" in second
    assert "既存の run 成果物を再利用" not in first


def test_a_directory_outside_the_experiment_layout_is_an_argument_error(
    completed: Path, tmp_path: Path
) -> None:
    """`runs/experiments/<名前>/v<版>` の形でない場所は終了コード 2（成果物の根を引けない）。"""
    directory = tmp_path / "loose"
    directory.mkdir()
    shutil.copy(completed / _VERSION_DIR / EXPERIMENT_MANIFEST_FILE, directory)

    code, _, error = _report(directory)

    assert code == 2
    assert "runs/experiments" in error
    assert not (directory / REPORT_FILE).exists()


def test_an_unreadable_manifest_is_an_argument_error(completed: Path, tmp_path: Path) -> None:
    """記録票が読めなければ終了コード 2（何の実験のレポートかを決められない）。"""
    directory = _copy(completed, tmp_path)
    (directory / EXPERIMENT_MANIFEST_FILE).write_text("{", encoding="utf-8")
    before = (directory / REPORT_FILE).read_bytes()

    code, _, error = _report(directory)

    assert code == 2
    assert "記録票" in error
    assert (directory / REPORT_FILE).read_bytes() == before
