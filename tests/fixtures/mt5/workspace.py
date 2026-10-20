"""MT5 経路の人工データを受け入れ・確定・承認し、書式 v2 の実験設定で run する作業場。

`experiment run` の経路（`app.composition` の結線。`_IntrabarBars`・`SnapshotInputs`・
読み取りの関門・parquet の読み込みを含む）で MT5 経路の run を通すための組み立てである。
`tests/fixtures/mt5/run.py` はエンジンの利用口を直接結線するので、composition の部品を通らない。

- 1 分足の行は `tests/fixtures/mt5/market.py` の人工の値（実データの価格ではない）。受入れの
  関数（`build_mt5_pending`）で暫定 snapshot を作り、CLI の書き出し（`_write_snapshot`）で
  `_pending/` に書き、`data classify`・`data approve` を CLI で通す。
- 時刻の規則の表と承認済みの一覧は人工の YAML（`market.write_rule_files`）。
- 設定は正本（`configs/`）を写す。`uv.lock` は写さず**固定の文面**を置く（依存の更新で lock の
  ダイジェストが変わらないようにする）。

価格は人工の値で、実データの価格ではない。
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from contextlib import redirect_stdout
from pathlib import Path
from typing import Final

from odyssey_fx.app.cli.main import _write_snapshot, main
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.snapshot_access import PENDING_DIRECTORY
from tests.fixtures.evaluation.research_policies import append_registry_entry
from tests.fixtures.mt5 import market as m5
from tests.fixtures.synthetic import market

__all__ = [
    "EXPERIMENT_FILE",
    "FIXED_LOCK_TEXT",
    "Mt5Workspace",
    "build_mt5_workspace",
]

REPO_ROOT: Final = Path(__file__).resolve().parents[3]

#: 作業場に置く `uv.lock` の固定の文面（lock のダイジェストを依存の更新から切り離す）。
FIXED_LOCK_TEXT: Final = "# synthetic uv.lock for the MT5 workspace fixture\n"

#: 作業場の実験設定の置き場（リポジトリの根からの相対パス）。
EXPERIMENT_FILE: Final = "configs/experiments/strategy_a_mt5_synthetic.yaml"

#: 作業場へ写す設定（正本の相対パス）。
_COPIED: Final = (
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v2.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/strategies/strategy_a_v1.yaml",
    "configs/policies/research/research_policy_v1.yaml",
)

#: 実験設定の本文（検証戦略 A・MT5 経路の解像度階層。値は既存の単一実行の設定と同じ）。
_EXPERIMENT_TEXT: Final = """schema_version: 2

id: strategy_a_mt5_synthetic
version: 1

hypothesis: "人工データの MT5 経路で検証戦略 A の run を通す（識別子の固定値の確認用）"

research_policy: {{id: "research_policy", version: 1}}

strategy: "configs/strategies/strategy_a_v1.yaml"

environment:
  calendar: "configs/calendars/fx_ny17_v1.yaml"
  timeframes: "configs/calendars/timeframes_v2.yaml"
  symbols: "configs/symbols"

snapshot: "{snapshot}"

run_interval:
  start: "{start}"
  end: "{end}"

execution_series: "USDJPY/15m/bid"

resolution_hierarchy:
  - "USDJPY/15m/bid"
  - "USDJPY/1m/bid"

seed: 0

account:
  account_id: "ACC1"
  currency: "JPY"
  initial_balance: "1000000"

risk_policy:
  trial_risk_rate: "0.02"
  account_risk_cap: "0.20"

execution_policy:
  entry_delay_bars: 0
  entry_valid_for: "PT20M"
  close_valid_for: "PT20M"
  adverse_fill_limits:
    USDJPY: "0.05"
  reference_quote_source: "EXECUTION_SERIES_LAST_CLOSE"

cost_model:
  commission_per_unit: "0.001"
  entry_slippage: "0.01"
  close_slippage: "0.01"
  spread_offset: "0.02"

conversion_policy:
  pivot_currency: "USD"
  max_observation_skew: "PT15M"

evaluation: {{metric_set_version: 2}}

search_plan: "NONE"
split: "NONE"
"""


def _call(argv: list[str]) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    output = buffer.getvalue()
    assert code == 0, f"{argv} exited with {code}\n{output}"
    return output


class Mt5Workspace:
    """受入れ・確定・承認を済ませた MT5 経路の作業場。"""

    def __init__(self, repo: Path, snapshot_id: str) -> None:
        self.repo = repo
        self.snapshot_id = snapshot_id

    @property
    def snapshots(self) -> Path:
        return self.repo / "data/snapshots"

    @property
    def experiment(self) -> Path:
        return self.repo / EXPERIMENT_FILE

    def run_argv(self, artifacts_root: Path) -> list[str]:
        """`experiment run` の引数。"""
        return [
            "experiment",
            "run",
            "--experiment",
            str(self.experiment),
            "--snapshots",
            str(self.snapshots),
            "--out",
            str(artifacts_root),
            "--repo-root",
            str(self.repo),
        ]


def build_mt5_workspace(
    root: Path,
    *,
    data: Interval,
    run: Interval,
    unrecorded: tuple[Interval, ...] = (),
    prices: Mapping[UtcTime, tuple[str, str, str, str]] | None = None,
) -> Mt5Workspace:
    """作業場を作り、人工の 1 分足（`data` の区間）を受け入れて承認し、`run` の実験設定を書く。"""
    repo = root / "repo"
    for name in _COPIED:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / name).read_bytes())
    (repo / "uv.lock").write_text(FIXED_LOCK_TEXT, encoding="utf-8")
    append_registry_entry(repo, "research_policy", 1)
    table = m5.write_rule_files(repo)

    calendar = market.calendar()
    rows = m5.minute_rows(calendar, data, unrecorded=unrecorded, prices=prices)
    year, month = data.start.value.year, data.start.value.month
    pending = m5.build_pending(calendar, table, [m5.MonthSpec(year, month, rows)])
    snapshots = repo / "data/snapshots"
    store = ParquetSnapshotStore(root=snapshots)
    provisional = str(pending.provisional_id)
    _write_snapshot(store, f"{PENDING_DIRECTORY}/{provisional}", pending, pending.manifest)
    decisions = repo / "decisions.yaml"
    decisions.write_text("schema_version: 2\ndecisions: []\n", encoding="utf-8")
    _call(
        [
            "data",
            "classify",
            "--pending",
            provisional,
            "--decisions",
            str(decisions),
            "--timeframes",
            str(repo / "configs/calendars/timeframes_v2.yaml"),
            "--out",
            str(snapshots),
        ]
    )
    created = sorted(
        path.name
        for path in snapshots.iterdir()
        if path.is_dir() and path.name != PENDING_DIRECTORY
    )
    assert len(created) == 1, created
    snapshot_id = created[0]
    _call(
        [
            "data",
            "approve",
            "--snapshot",
            snapshot_id,
            "--by",
            "integration-test",
            "--comment",
            "人工データ（MT5 経路）",
            "--out",
            str(snapshots),
        ]
    )
    experiment = repo / EXPERIMENT_FILE
    experiment.parent.mkdir(parents=True, exist_ok=True)
    experiment.write_text(
        _EXPERIMENT_TEXT.format(snapshot=snapshot_id, start=str(run.start), end=str(run.end)),
        encoding="utf-8",
    )
    return Mt5Workspace(repo=repo, snapshot_id=snapshot_id)
