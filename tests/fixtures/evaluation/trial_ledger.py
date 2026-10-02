"""試行台帳の行と台帳のファイルをテストの中で組み立てる（D09 §10.12、§13 の v0.3 の意味論テスト）。

台帳はテストの中のリポジトリの根に作り、ロック・途中停止・合流は、ファイルを直接組み立てて
作る（実データも git も使わない。D09 §13）。行の値は人工のもので、比較の前提は形だけを満たす。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final

from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import AccountId, ExperimentId, SnapshotId
from odyssey_fx.common.money import CurrencyCode, Money, decimal_from_str
from odyssey_fx.common.refs import (
    CodeDigest,
    ContentDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    SnapshotRef,
    StrategyRef,
)
from odyssey_fx.common.symbol import Symbol, SymbolSpecRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.adapters.fs_store import TRIAL_LEDGER_PATH, ledger_line_bytes
from odyssey_fx.evaluation.domain.experiment import ExperimentStatus
from odyssey_fx.evaluation.domain.search import (
    TRIAL_LEDGER_SCHEMA_VERSION,
    ComparisonBasis,
    SearchVerdict,
    StandardPurpose,
    TrialLedgerEntry,
    TrialLedgerEvent,
    TrialLedgerLine,
    finished_entry,
)
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

__all__ = [
    "basis",
    "chain",
    "experiment_id",
    "finished",
    "ledger_path",
    "started",
    "with_nonce",
    "write_ledger",
]

_M15: Final = TimeframeRef(id="15m", version=1)


def _policy(kind: str) -> PolicyRef:
    return PolicyRef(policy_kind=kind, policy_id=kind, version=1, digest=digest(kind))


def basis() -> ComparisonBasis:
    """形だけを満たす比較の前提（値はテスト用）。"""
    return ComparisonBasis(
        research_policy_ref=_policy("research"),
        metric_set_version=2,
        snapshot_ref=SnapshotRef(snapshot_id=SnapshotId(digest("snapshot"))),
        strategy_ref=StrategyRef(strategy_id="strategy_b", version=1, digest=digest("strategy")),
        execution_series=SeriesId(symbol=Symbol("USDJPY"), timeframe=_M15, basis=PriceBasis.BID),
        seed=0,
        account=AccountSpec(
            account_id=AccountId("ACC1"),
            currency=CurrencyCode("JPY"),
            initial_balance=Money(decimal_from_str("1000000"), CurrencyCode("JPY")),
        ),
        risk_policy_ref=_policy("risk"),
        execution_policy_ref=_policy("execution"),
        cost_model_ref=_policy("cost"),
        conversion_policy_ref=_policy("conversion"),
        delay_scenario_ref=_policy("delay"),
        symbol_spec_ref=SymbolSpecRef(symbol=Symbol("USDJPY"), version=1, digest=digest("spec")),
        calendar_ref="fx_ny17@v1",
        timeframe_def_refs=(_M15,),
        code_digest=CodeDigest(digest("code")),
        lock_digest=LockDigest(digest("lock")),
        env_digest=EnvDigest(digest("env")),
    )


def experiment_id(name: str) -> ExperimentId:
    return ExperimentId(digest(f"experiment {name}"))


def started(
    name: str = "a",
    execution: int = 1,
    *,
    nonce: str = "0" * 32,
    purpose: StandardPurpose = StandardPurpose.MECHANISM_CHECK,
) -> TrialLedgerEntry:
    """開始の行（`name` ごとに別の実験）。"""
    return TrialLedgerEntry(
        schema_version=TRIAL_LEDGER_SCHEMA_VERSION,
        event=TrialLedgerEvent.STARTED,
        experiment_id=experiment_id(name),
        execution=execution,
        execution_nonce=nonce,
        experiment_name=f"exp_{name}",
        experiment_version=1,
        strategy_id="strategy_b",
        basis=basis(),
        trial_count=4,
        search_plan_digest=digest(f"plan {name}"),
        validation_intervals=(
            Interval(
                start=UtcTime.parse("2015-01-08T22:00:00Z"),
                end=UtcTime.parse("2015-01-12T22:00:00Z"),
            ),
        ),
        final_holdout=None,
        purpose=purpose,
        status=None,
        verdict=None,
        frequency_class=None,
    )


def finished(
    opening: TrialLedgerEntry,
    *,
    verdict: SearchVerdict = SearchVerdict.INSUFFICIENT_EVIDENCE,
    status: ExperimentStatus = ExperimentStatus.COMPLETED,
) -> TrialLedgerEntry:
    """開始の行に対応する結末の行（不変項目は開始の行から写す）。"""
    return finished_entry(opening, status=status, verdict=verdict, frequency_class="LOW")


def chain(
    entries: Sequence[TrialLedgerEntry], prev: ContentDigest | None = None
) -> list[TrialLedgerLine]:
    """台帳の行を鎖でつなぐ（各行の `prev` に直前の行の `digest`）。"""
    lines: list[TrialLedgerLine] = []
    for entry in entries:
        line = TrialLedgerLine.of(entry, prev)
        lines.append(line)
        prev = line.digest
    return lines


def ledger_path(root: Path) -> Path:
    return Path(root) / TRIAL_LEDGER_PATH


def write_ledger(
    root: Path, lines: Sequence[TrialLedgerLine | bytes], *, tail: bytes = b""
) -> Path:
    """台帳のファイルを書く（行の包みは符号化し、`bytes` はそのまま書く。`tail` は末尾の断片）。"""
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(item if isinstance(item, bytes) else ledger_line_bytes(item) for item in lines)
    path.write_bytes(data + tail)
    return path


def with_nonce(entry: TrialLedgerEntry, nonce: str) -> TrialLedgerEntry:
    return replace(entry, execution_nonce=nonce)
