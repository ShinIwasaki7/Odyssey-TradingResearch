"""検証戦略 B（T02）の人工データをコマンド経由で受け入れ、書式 v2 の実験設定で run する作業場。

段階3 の受入テストはエンジンの利用口を直接呼んでいた（`t02_run.py`）。実験設定の書式が検証
戦略 B も遅延シナリオも書けなかったためである。書式 v2（D07 §18）でそれが書けるようになった
ので、本組み立ては**設定ファイル → 受入れ → 分類 → 承認 → run** を `odyssey-fx` のコマンドで
通す（D07 §17.2 の実装 PR 2 の統合テスト）。

素の足は `t02_run.raw_bars()` と同じもの（1時間足・15分足）を原 CSV に書き戻す。日足は
受入れが1時間足から集約する（D03 §5.1）ので、`t02_run` が生成器の集約で作った日足と同じ
規則で作られる。

作業場はリポジトリの根の形をまねる。書式 v2 のパスは**リポジトリの根からの相対パス**で
解決される（D07 §18.2）ので、設定を写し、`uv.lock` も写す（`--repo-root` に作業場を渡す。
`uv.lock` の内容ダイジェストは写しても変わらない）。設定は正本（`configs/`）を写すので、
書き方が変われば本組み立ても追従する。
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from typing import Final

from odyssey_fx.app.cli.main import main
from odyssey_fx.marketdata.application.snapshot_access import PENDING_DIRECTORY
from tests.fixtures.acceptance import t02_market
from tests.fixtures.acceptance.t02_run import raw_bars
from tests.fixtures.strategy.strategy_b import HOURLY_SERIES, M15_SERIES

__all__ = [
    "CASE_EXPERIMENTS",
    "REPO_ROOT",
    "SNAPSHOT_PLACEHOLDER",
    "T02Workspace",
    "build_workspace",
    "call",
]

REPO_ROOT: Final = Path(__file__).resolve().parents[3]

#: 同梱の実験設定が既定値として置く snapshot の識別子（各ファイルの注記）。
SNAPSHOT_PLACEHOLDER: Final = "0" * 64

#: 遅延シナリオ4ケース（T02 §1.4）と、それぞれの実験設定（書式 v2）。
CASE_EXPERIMENTS: Final = {
    "none": "configs/experiments/strategy_b_t02_none.yaml",
    "d1_2s": "configs/experiments/strategy_b_t02_d1_2s.yaml",
    "d1_25h": "configs/experiments/strategy_b_t02_d1_25h.yaml",
    "d1_bar_hold": "configs/experiments/strategy_b_t02_d1_bar_hold.yaml",
}

#: 作業場へ写す設定（正本の相対パス）。
_COPIED: Final = (
    "uv.lock",
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v1.yaml",
    "configs/datasources/legacy_merged_csv_v1.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/strategies/strategy_b_v1.yaml",
    *CASE_EXPERIMENTS.values(),
)


def call(argv: list[str]) -> str:
    """コマンドを実行し、画面へ出た内容を返す。終了コードが 0 でなければ失敗させる。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    output = buffer.getvalue()
    assert code == 0, f"{argv} exited with {code}\n{output}"
    return output


class T02Workspace:
    """受入れと承認を済ませた作業場。"""

    def __init__(self, repo: Path, snapshot_id: str) -> None:
        self.repo = repo
        self.snapshot_id = snapshot_id

    def experiment(self, case: str) -> Path:
        """そのケースの実験設定（snapshot の識別子を承認済みのものへ書き換えたもの）。"""
        return self.repo / CASE_EXPERIMENTS[case]

    def run_argv(self, case: str, artifacts_root: Path) -> list[str]:
        """書式 v2 の `run` コマンドの引数（環境の3つは実験設定の `environment` が指す）。"""
        return [
            "run",
            "--experiment",
            str(self.experiment(case)),
            "--snapshots",
            str(self.repo / "data/snapshots"),
            "--out",
            str(artifacts_root),
            "--repo-root",
            str(self.repo),
        ]

    def run(self, case: str, artifacts_root: Path) -> str:
        """そのケースで run を1回通し、`run_id` を返す。"""
        call(self.run_argv(case, artifacts_root))
        directories = sorted(path.name for path in (artifacts_root / "runs").iterdir())
        assert len(directories) == 1, directories
        return directories[0]


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _only_directory(parent: Path) -> str:
    names = sorted(
        path.name for path in parent.iterdir() if path.is_dir() and path.name != PENDING_DIRECTORY
    )
    assert len(names) == 1, names
    return names[0]


def build_workspace(root: Path) -> T02Workspace:
    """作業場を作り、T02 の人工データを受け入れて承認する。"""
    repo = root / "repo"
    for name in _COPIED:
        _copy(REPO_ROOT / name, repo / name)

    raw = repo / "data/raw/market"
    raw.mkdir(parents=True)
    bars = raw_bars()
    (raw / "USDJPY_1h_merged.csv").write_text(
        t02_market.csv_text(bars[HOURLY_SERIES]), encoding="utf-8"
    )
    (raw / "USDJPY_15m_merged.csv").write_text(
        t02_market.csv_text(bars[M15_SERIES]), encoding="utf-8"
    )

    configs = repo / "configs"
    snapshots = repo / "data/snapshots"
    call(
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
            str(snapshots),
            "--repo-root",
            str(repo),
        ]
    )
    provisional = _only_directory(snapshots / PENDING_DIRECTORY)
    decisions = repo / "decisions.yaml"
    decisions.write_text("schema_version: 2\ndecisions: []\n", encoding="utf-8")
    call(
        [
            "data",
            "classify",
            "--pending",
            provisional,
            "--decisions",
            str(decisions),
            "--out",
            str(snapshots),
            "--timeframes",
            str(configs / "calendars/timeframes_v1.yaml"),
        ]
    )
    snapshot_id = _only_directory(snapshots)
    call(
        [
            "data",
            "approve",
            "--snapshot",
            snapshot_id,
            "--by",
            "integration-test",
            "--comment",
            "人工データ（T02 の値）",
            "--out",
            str(snapshots),
        ]
    )
    for relative in CASE_EXPERIMENTS.values():
        path = repo / relative
        text = path.read_text(encoding="utf-8")
        assert SNAPSHOT_PLACEHOLDER in text, "実験設定の snapshot の既定値が変わっている"
        path.write_text(text.replace(SNAPSHOT_PLACEHOLDER, snapshot_id), encoding="utf-8")
    return T02Workspace(repo=repo, snapshot_id=snapshot_id)
