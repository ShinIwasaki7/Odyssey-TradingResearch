"""受入れテストの土台: 人工データを受け入れ、run を通し、評価まで走らせる。

**コマンド経由で通す**（`odyssey-fx data accept` → `classify` → `approve` → `run` →
`evaluate`）。実装の内部関数を直接呼ぶと、設定ファイルの読込・承認済み snapshot の関門・
成果物の保存という、段階2の完了条件が求める経路が抜ける。

同じ入力で2回通すのは、全体計画 §8.2 の「同一入力の再実行で trace が一致」を確かめる
ためである。成果物の基点を分けるのは、同じ完全入力の再実行が同じ実行の識別子になり、
既存の成果物を無条件に上書きしないためである（ADR-0006）。
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.marketdata.application.snapshot_access import PENDING_DIRECTORY
from tests.fixtures.acceptance import t01_market
from tests.fixtures.synthetic import market

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = REPO_ROOT / "configs"

#: 受入れの対象にする時間足（データソースの宣言と同じ2件）。
_TIMEFRAMES = (("1h", market.TF_1H), ("15m", market.TF_15M))

#: `configs/experiments/strategy_a_t01.yaml` が既定値として置く snapshot の識別子。
#: 環境ごとに実際の識別子へ書き換える（同ファイルの注記）。
_SNAPSHOT_PLACEHOLDER = "0" * 64


@dataclass(frozen=True, slots=True)
class Artifacts:
    """1回通した結果の置き場所と、画面へ出た内容。"""

    repo: Path
    artifacts_root: Path
    snapshot_id: str
    run_id: str
    run_output: str
    evaluate_output: str

    @property
    def run_directory(self) -> Path:
        """判断履歴と run manifest の置き場所。"""
        return self.artifacts_root / "runs" / self.run_id

    def trace(self, table: str) -> list[dict[str, object]]:
        """判断履歴の表を辞書の列として読む。"""
        return pl.read_parquet(self.run_directory / f"{table}.parquet").to_dicts()

    @property
    def evaluation_directory(self) -> Path:
        """評価結果の置き場所（`runs/<run_id>/eval/<評価 ID>/`）。"""
        directories = sorted((self.run_directory / "eval").iterdir())
        assert len(directories) == 1, directories
        return directories[0]

    def evaluation_manifest(self) -> dict[str, object]:
        """評価 manifest（JSON）。"""
        payload: dict[str, object] = json.loads(
            (self.evaluation_directory / "evaluation.json").read_text(encoding="utf-8")
        )
        return payload

    def evaluation(self, table: str) -> list[dict[str, object]]:
        """評価結果の表を辞書の列として読む。"""
        return pl.read_parquet(self.evaluation_directory / f"{table}.parquet").to_dicts()

    def run_manifest(self) -> dict[str, object]:
        """run manifest（JSON）。"""
        payload: dict[str, object] = json.loads(
            (self.run_directory / "manifest.json").read_text(encoding="utf-8")
        )
        return payload


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _workspace(root: Path) -> Path:
    """原 CSV と設定を置いた作業場を作る。

    設定は実物を写し、銘柄仕様だけ USDJPY に絞る（受入れの対象を1銘柄にして実行時間を
    短くするため）。実物を写すので、設定ファイルの書き方が変わればこのテストも追従する。
    """
    repo = root / "repo"
    raw = repo / "data/raw/market"
    raw.mkdir(parents=True)

    for name in ("calendars/fx_ny17_v1.yaml", "calendars/timeframes_v1.yaml"):
        _copy(CONFIGS / name, repo / "configs" / name)
    _copy(
        CONFIGS / "datasources/legacy_merged_csv_v1.yaml",
        repo / "configs/datasources/legacy_merged_csv_v1.yaml",
    )
    _copy(CONFIGS / "symbols/USDJPY.yaml", repo / "configs/symbols/USDJPY.yaml")
    _copy(
        CONFIGS / "experiments/strategy_a_t01.yaml",
        repo / "configs/experiments/strategy_a_t01.yaml",
    )

    calendar = market.calendar()
    for timeframe_id, definition in _TIMEFRAMES:
        bars = t01_market.bars_for(timeframe_id, definition, calendar)
        (raw / f"USDJPY_{timeframe_id}_merged.csv").write_text(
            t01_market.csv_text(bars), encoding="utf-8"
        )
    return repo


def _call(argv: list[str]) -> str:
    """コマンドを実行し、画面へ出た内容を返す。終了コードが 0 でなければ失敗させる。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    output = buffer.getvalue()
    assert code == 0, f"{argv} exited with {code}\n{output}"
    return output


def _accept(repo: Path) -> str:
    configs = repo / "configs"
    return _call(
        [
            "data",
            "accept",
            "--datasource",
            str(configs / "datasources/legacy_merged_csv_v1.yaml"),
            "--calendar",
            str(configs / "calendars/fx_ny17_v1.yaml"),
            "--timeframes",
            str(configs / "calendars/timeframes_v1.yaml"),
            "--symbols",
            str(configs / "symbols"),
            "--out",
            str(repo / "data/snapshots"),
            "--repo-root",
            str(repo),
        ]
    )


def _provisional_id(repo: Path) -> str:
    pending = repo / "data/snapshots" / PENDING_DIRECTORY
    directories = sorted(path.name for path in pending.iterdir() if path.is_dir())
    assert len(directories) == 1, directories
    return directories[0]


def _classify(repo: Path, provisional: str) -> str:
    decisions = repo / "decisions.yaml"
    # 欠落を1本も作っていないので、人間の分類は1件も要らない（D03 §4 の 9）。
    decisions.write_text("schema_version: 1\ndecisions: []\n", encoding="utf-8")
    return _call(
        [
            "data",
            "classify",
            "--pending",
            provisional,
            "--decisions",
            str(decisions),
            "--out",
            str(repo / "data/snapshots"),
            "--timeframes",
            str(repo / "configs/calendars/timeframes_v1.yaml"),
        ]
    )


def _snapshot_id(repo: Path) -> str:
    snapshots = repo / "data/snapshots"
    directories = sorted(
        path.name
        for path in snapshots.iterdir()
        if path.is_dir() and path.name != PENDING_DIRECTORY
    )
    assert len(directories) == 1, directories
    return directories[0]


def _approve(repo: Path, snapshot_id: str) -> str:
    return _call(
        [
            "data",
            "approve",
            "--snapshot",
            snapshot_id,
            "--by",
            "acceptance-test",
            "--comment",
            "人工データ（T01 の値）",
            "--out",
            str(repo / "data/snapshots"),
        ]
    )


def _experiment_path(repo: Path, snapshot_id: str) -> Path:
    """実験設定の snapshot の識別子を、確定した識別子へ書き換える。

    設定ファイルそのものは正本（`configs/experiments/`）から写しているので、宣言の書き方が
    変わればこのテストも追従する。
    """
    path = repo / "configs/experiments/strategy_a_t01.yaml"
    text = path.read_text(encoding="utf-8")
    assert _SNAPSHOT_PLACEHOLDER in text, "実験設定の snapshot の既定値が変わっている"
    path.write_text(text.replace(_SNAPSHOT_PLACEHOLDER, snapshot_id), encoding="utf-8")
    return path


def _run(repo: Path, artifacts_root: Path, snapshot_id: str) -> str:
    configs = repo / "configs"
    return _call(
        [
            "run",
            "--experiment",
            str(_experiment_path(repo, snapshot_id)),
            "--calendar",
            str(configs / "calendars/fx_ny17_v1.yaml"),
            "--timeframes",
            str(configs / "calendars/timeframes_v1.yaml"),
            "--symbols",
            str(configs / "symbols"),
            "--snapshots",
            str(repo / "data/snapshots"),
            "--out",
            str(artifacts_root),
            "--repo-root",
            str(REPO_ROOT),
        ]
    )


def _run_id_of(artifacts_root: Path) -> str:
    directories = sorted(path.name for path in (artifacts_root / "runs").iterdir())
    assert len(directories) == 1, directories
    return directories[0]


def _evaluate(artifacts_root: Path, run_id: str) -> str:
    return _call(["evaluate", "--run", run_id, "--out", str(artifacts_root)])


def build(root: Path) -> Artifacts:
    """受入れから評価までを1度通す。"""
    repo = _workspace(root)
    _accept(repo)
    _classify(repo, _provisional_id(repo))
    snapshot_id = _snapshot_id(repo)
    _approve(repo, snapshot_id)

    artifacts_root = root / "artifacts"
    run_output = _run(repo, artifacts_root, snapshot_id)
    run_id = _run_id_of(artifacts_root)
    evaluate_output = _evaluate(artifacts_root, run_id)
    return Artifacts(
        repo=repo,
        artifacts_root=artifacts_root,
        snapshot_id=snapshot_id,
        run_id=run_id,
        run_output=run_output,
        evaluate_output=evaluate_output,
    )


@pytest.fixture(scope="session")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> Artifacts:
    """人工データを受け入れ、run を通し、評価まで走らせた成果物（1回目）。"""
    return build(tmp_path_factory.mktemp("stage2-first"))


@pytest.fixture(scope="session")
def rerun(tmp_path_factory: pytest.TempPathFactory) -> Artifacts:
    """同じ入力をもう1度通した成果物（再実行の一致を確かめるため）。"""
    return build(tmp_path_factory.mktemp("stage2-second"))
