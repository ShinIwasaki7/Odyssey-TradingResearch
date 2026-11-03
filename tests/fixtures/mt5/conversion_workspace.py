"""換算の受入テストの作業場（D06 v1.20 案 §19.2・§19.16 の CT-9・CT-10・CT-17・CT-26）。

MT5 経路の人工の 1 分足で、執行の通貨ペア（EURUSD）と換算の通貨ペア（USDJPY）の snapshot を
受け入れ・確定・承認し、`experiment run`（`app.composition` の結線）で通す。値は人工で、実データの
価格ではない。EURUSD の値は 1.5 前後、USDJPY は 150 前後（どちらも刻みの整数倍）。

- 時刻の規則の表は 3 通貨（USDJPY・EURUSD・USDCHF）を対象にした人工の表（既存の固定値テストの
  表とは別の本文）。
- `snapshots` は `(通貨ペア, 組)` から snapshot の識別子への対応。
- 実験設定は `experiment_text(...)` で作る（換算の snapshot のキーを書く・書かない・別の組にする）。
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from odyssey_fx.app.cli.main import _write_snapshot, main
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.snapshot_access import PENDING_DIRECTORY
from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant
from tests.fixtures.evaluation.research_policies import append_registry_entry
from tests.fixtures.mt5 import market as m5
from tests.fixtures.mt5.workspace import FIXED_LOCK_TEXT, REPO_ROOT
from tests.fixtures.synthetic import market

__all__ = ["ConversionWorkspace", "build_conversion_workspace", "experiment_text"]

EURUSD: Final = Symbol("EURUSD")
USDJPY: Final = Symbol("USDJPY")

_COPIED: Final = (
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v2.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/symbols/EURUSD.yaml",
    "configs/strategies/strategy_a_v1.yaml",
    "configs/policies/research/research_policy_v1.yaml",
)

#: 作業場の EURUSD の戦略（検証戦略 A の系列を EURUSD に替えた写し）。
STRATEGY_FILE: Final = "configs/strategies/strategy_a_eurusd.yaml"

DECISION: Final = UtcTime.parse("2026-01-23T20:00:00Z")
RUN: Final = Interval(start=DECISION, end=UtcTime.parse("2026-01-23T21:00:00Z"))
DATA: Final = Interval(
    start=UtcTime.parse("2026-01-22T22:00:00Z"), end=UtcTime.parse("2026-01-23T22:00:00Z")
)


def _at(text: str) -> UtcTime:
    return UtcTime.parse(f"2026-01-23T{text}:00Z")


def _prices_150() -> dict[UtcTime, tuple[str, str, str, str]]:
    """`test_mt5_run_identity` と同じ突破の筋書き（150 前後）。"""
    flat = ("149.005", "149.005", "149.005", "149.005")
    prices: dict[UtcTime, tuple[str, str, str, str]] = {}
    moment = DATA.start
    while moment < DECISION:
        prices[moment] = flat
        moment = moment + timedelta(minutes=1)
    hour = UtcTime.parse("2026-01-22T23:00:00Z")
    while hour < _at("19:00"):
        prices[hour] = ("149.005", "150.000", "149.000", "149.005")
        hour = hour + timedelta(hours=1)
    moment = _at("19:00")
    while moment < DECISION:
        prices[moment] = ("149.900", "149.900", "149.900", "149.900")
        moment = moment + timedelta(minutes=1)
    prices[_at("19:00")] = ("149.900", "150.100", "149.800", "149.900")
    prices[_at("19:59")] = ("149.900", "150.040", "149.900", "150.040")
    prices[_at("20:36")] = ("150.000", "152.500", "148.500", "150.000")
    # 残りの分は受入れの既定の値（`m5.BASE`）。1/100 にするために全部の分を書く。
    moment = DATA.start
    while moment < DATA.end:
        prices.setdefault(moment, m5.BASE)
        moment = moment + timedelta(minutes=1)
    return prices


def _scaled(values: Mapping[UtcTime, tuple[str, str, str, str]]) -> dict[UtcTime, tuple[str, ...]]:
    """値を 1/100 にする（EURUSD の 1.5 前後の人工の値。刻み 0.00001 の整数倍）。"""
    hundred = Decimal(100)
    return {
        moment: tuple(str((Decimal(item) / hundred).quantize(Decimal("0.00001"))) for item in four)
        for moment, four in values.items()
    }


@dataclass(frozen=True, slots=True)
class ConversionWorkspace:
    """受入れ・確定・承認を済ませた作業場。"""

    repo: Path
    snapshots: Mapping[tuple[str, TimeRuleVariant], str]

    @property
    def snapshots_root(self) -> Path:
        return self.repo / "data/snapshots"

    def write_experiment(self, name: str, text: str) -> Path:
        path = self.repo / "configs/experiments" / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_argv(self, experiment: Path, artifacts_root: Path) -> list[str]:
        return [
            "experiment",
            "run",
            "--experiment",
            str(experiment),
            "--snapshots",
            str(self.snapshots_root),
            "--out",
            str(artifacts_root),
            "--repo-root",
            str(self.repo),
        ]


def _call(argv: list[str]) -> None:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    assert code == 0, f"{argv} exited with {code}\n{buffer.getvalue()}"


def _accept(repo: Path, symbol: Symbol, variant: TimeRuleVariant, prices: Mapping) -> str:  # type: ignore[type-arg]
    table = m5.write_rule_files(repo, pairs=("USDJPY", "EURUSD", "USDCHF"))
    calendar = market.calendar()
    rows = m5.minute_rows(calendar, DATA, prices=prices)
    year, month = DATA.start.value.year, DATA.start.value.month
    pending = m5.build_pending(
        calendar, table, [m5.MonthSpec(year, month, rows)], variant=variant, symbol=symbol
    )
    snapshots = repo / "data/snapshots"
    store = ParquetSnapshotStore(root=snapshots)
    provisional = str(pending.provisional_id)
    _write_snapshot(store, f"{PENDING_DIRECTORY}/{provisional}", pending, pending.manifest)
    before = {path.name for path in snapshots.iterdir() if path.is_dir()}
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
        if path.is_dir() and path.name != PENDING_DIRECTORY and path.name not in before
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
            "人工データ（換算）",
            "--out",
            str(snapshots),
        ]
    )
    return snapshot_id


def build_conversion_workspace(root: Path) -> ConversionWorkspace:
    """EURUSD（標準の組）・USDJPY（標準の組と候補の組）の snapshot を承認した作業場を作る。"""
    repo = root / "repo"
    for name in _COPIED:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / name).read_bytes())
    strategy = (REPO_ROOT / "configs/strategies/strategy_a_v1.yaml").read_text(encoding="utf-8")
    (repo / STRATEGY_FILE).write_text(strategy.replace("USDJPY", "EURUSD"), encoding="utf-8")
    (repo / "uv.lock").write_text(FIXED_LOCK_TEXT, encoding="utf-8")
    append_registry_entry(repo, "research_policy", 1)
    yen = _prices_150()
    snapshots = {
        ("EURUSD", TimeRuleVariant.STANDARD): _accept(
            repo, EURUSD, TimeRuleVariant.STANDARD, _scaled(yen)
        ),
        ("USDJPY", TimeRuleVariant.STANDARD): _accept(repo, USDJPY, TimeRuleVariant.STANDARD, {}),
        ("USDJPY", TimeRuleVariant.CANDIDATE): _accept(repo, USDJPY, TimeRuleVariant.CANDIDATE, {}),
    }
    return ConversionWorkspace(repo=repo, snapshots=snapshots)


def experiment_text(
    *,
    name: str,
    snapshot: str,
    symbol: str = "EURUSD",
    conversion_line: str = "",
    strategy: str = STRATEGY_FILE,
) -> str:
    """単一実行の書式 v2 の実験設定（`conversion_line` に換算の snapshot のキーを書く）。"""
    tick = "0.0005" if symbol == "EURUSD" else "0.05"
    slip = "0.0001" if symbol == "EURUSD" else "0.01"
    spread = "0.0002" if symbol == "EURUSD" else "0.02"
    return f"""schema_version: 2

id: {name}
version: 1

hypothesis: "人工データで円以外の決済通貨の換算を通す"

research_policy: {{id: "research_policy", version: 1}}

strategy: "{strategy}"

environment:
  calendar: "configs/calendars/fx_ny17_v1.yaml"
  timeframes: "configs/calendars/timeframes_v2.yaml"
  symbols: "configs/symbols"

snapshot: "{snapshot}"
{conversion_line}
run_interval:
  start: "{RUN.start}"
  end: "{RUN.end}"

execution_series: "{symbol}/15m/bid"

resolution_hierarchy:
  - "{symbol}/15m/bid"
  - "{symbol}/1m/bid"

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
    {symbol}: "{tick}"
  reference_quote_source: "EXECUTION_SERIES_LAST_CLOSE"

cost_model:
  commission_per_unit: "0.001"
  entry_slippage: "{slip}"
  close_slippage: "{slip}"
  spread_offset: "{spread}"

conversion_policy:
  pivot_currency: "USD"
  max_observation_skew: "PT15M"

evaluation: {{metric_set_version: 2}}

search_plan: "NONE"
split: "NONE"
"""
