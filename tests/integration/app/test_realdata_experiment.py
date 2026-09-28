"""実データでの実行（D07 §23、2026-09-25 の人間の決定6・Q9 決定）。

**マーカー `realdata`**: 承認済み snapshot `a498b8cf…` の実体（git 管理外の `data/snapshots/`）を
読むので、`pyproject.toml` の既定で除外し、CI でも実行しない（D08 §12）。実行するときは
`uv run pytest -m realdata` と明示する。実体が無い環境では**失敗ではなくスキップ**にする（無いのは
環境の事実であって検査の不合格ではない。D08 §12）。

Q9 決定の2本を `experiment run` で通す。

1. **研究履歴 2016〜2023 の1本**（`strategy_a_usdjpy_research_history.yaml`）: 執行系列 USDJPY
   15分足にデータ欠損と分類した欠落が run 区間の中にあるので、実行前のデータ能力検査で止まる
   （`FAILED_CAPABILITY`。D06 §10.5 の手順2）。評価は `REJECTED`、レポートの先頭に理由が出る。
2. **2018 年の1本**（`strategy_a_usdjpy_2018.yaml`）: run 区間に執行系列の欠落が無いので完走し、
   評価まで `COMPLETED` になる。

1本あたり数分〜数十分かかる（所要時間の実績は `docs/runs/stage4_realdata_run.md`）。
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.evaluation.adapters.fs_store import EXPERIMENT_OUTCOME_FILE, REPORT_FILE

pytestmark = pytest.mark.realdata

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOTS = REPO_ROOT / "data/snapshots"
#: 承認済み snapshot（2026-09-25 承認。PR #39）。
SNAPSHOT_ID = "a498b8cf4f90aeca1fa8af7a1e59112db197413eaa8c0a0d318eb2368a770cb3"
#: 実体があるかを見る partition（執行系列の研究履歴）。manifest は git 管理下にあるので見ない。
_PROBE = SNAPSHOTS / SNAPSHOT_ID / "USDJPY_15m_bid" / "RESEARCH_HISTORY" / "bars.parquet"


def _require_snapshot() -> None:
    if not _PROBE.is_file():
        pytest.skip(f"承認済み snapshot {SNAPSHOT_ID[:8]}… の実体が無い環境（D08 §12）")


def _experiment_run(name: str, artifacts: Path) -> tuple[int, str, Path]:
    _require_snapshot()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(
            [
                "experiment",
                "run",
                "--experiment",
                str(REPO_ROOT / "configs/experiments" / f"{name}.yaml"),
                "--snapshots",
                str(SNAPSHOTS),
                "--out",
                str(artifacts),
                "--repo-root",
                str(REPO_ROOT),
            ]
        )
    return code, out.getvalue() + err.getvalue(), artifacts / "runs/experiments" / name / "v1"


def _json(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


@pytest.fixture(scope="module")
def research_history(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    artifacts = tmp_path_factory.mktemp("realdata-research-history")
    code, output, directory = _experiment_run("strategy_a_usdjpy_research_history", artifacts)
    assert code == 0, output
    return artifacts, directory


@pytest.fixture(scope="module")
def year_2018(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    artifacts = tmp_path_factory.mktemp("realdata-2018")
    code, output, directory = _experiment_run("strategy_a_usdjpy_2018", artifacts)
    assert code == 0, output
    return artifacts, directory


def test_the_research_history_run_stops_at_the_capability_check(
    research_history: tuple[Path, Path],
) -> None:
    """研究履歴 2016〜2023 の1本は実行前のデータ能力検査で止まる（D07 §23.2 の Q9 決定）。"""
    artifacts, directory = research_history
    outcome = _json(directory / EXPERIMENT_OUTCOME_FILE)
    assert outcome["status"] == "COMPLETED"
    assert outcome["run_status"] == "FAILED_CAPABILITY"
    assert outcome["evaluation_status"] == "REJECTED"

    run_manifest = _json(artifacts / "runs" / str(outcome["run_id"]) / "manifest.json")
    capability = run_manifest["capability_report"]
    assert capability["runnable"] is False
    assert any("USDJPY/15m/bid" in line for line in capability["diagnostics"])

    report = (directory / REPORT_FILE).read_text(encoding="utf-8")
    conclusion = report.split("## 2.")[0]
    assert "**採用不可**" in conclusion and "FAILED_CAPABILITY" in conclusion
    assert "missing expected bars" in report


def test_the_2018_run_completes_and_is_evaluated(year_2018: tuple[Path, Path]) -> None:
    """欠落の無い 2018 年の1本は完走し、評価まで `COMPLETED` になる（D07 §23.2 の Q9 決定）。"""
    _, directory = year_2018
    outcome = _json(directory / EXPERIMENT_OUTCOME_FILE)
    assert outcome["status"] == "COMPLETED"
    assert outcome["run_status"] == "COMPLETED"
    assert outcome["evaluation_status"] == "COMPLETED"
    assert outcome["failed_checks"] == []

    report = (directory / REPORT_FILE).read_text(encoding="utf-8")
    assert "**採用可**" in report.split("## 2.")[0]
    assert "SWAP_NOT_MODELED" in report  # swap 未計上の注記（ADR-0029）
