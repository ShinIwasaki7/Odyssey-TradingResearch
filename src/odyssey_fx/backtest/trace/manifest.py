"""run manifest と実行前のデータ能力検査の結果（D06 §9.3・§10.5）。

manifest は JSON で保存する（ADR-0027）。識別・入力・ポリシー・実行の構造・足内競合・
能力検査・状態の7群を持ち、**能力検査の結果は全体のまま**入れる（要約に畳まない）。
合格・不合格のどちらでも保存し、不合格の個別結果を落とさない。

`ConfigDigest` の対象は「識別」を除く入力とポリシーの群である。口座仕様を入力群に含めるのは、
初期残高だけを変えた実行が数量・損益・資産推移をすべて変えるのに、含めないと同じ
`ConfigDigest` と `RunId` になり、別の結果が同じ `runs/<run_id>/` を指すためである。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from odyssey_fx.backtest.domain.policies import HierarchyCheckResult, ResolutionHierarchy, RunConfig
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.reason import Reason
from odyssey_fx.common.refs import ConfigDigest
from odyssey_fx.common.symbol import SymbolSpecRef
from odyssey_fx.common.time import PhaseSet
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.integrity import IntegrityReport

__all__ = ["DataCapabilityReport", "RunManifest", "config_digest_of"]


@dataclass(frozen=True, slots=True)
class DataCapabilityReport:
    """実行前のデータ能力検査の結果（D06 §10.5）。

    **置き場所の差異**: D06 §3 の型表は `engine` としているが、この報告は run manifest と
    `BacktestResult` の項目でもある（D06 §9.3・§9.4）。`trace` は `engine` を import
    できないため（D01 §3.3 の層順序）、manifest 側に置いてエンジンが参照する。項目は
    D06 §10.5 のままで、内容の差異はない。
    """

    compiled_match: bool
    integrity: IntegrityReport
    hierarchy_checks: tuple[HierarchyCheckResult, ...] = ()
    runnable: bool = True
    reason: Reason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.compiled_match, bool):
            raise KernelValueError("DataCapabilityReport.compiled_match must be a bool")
        if not isinstance(self.integrity, IntegrityReport):
            raise KernelValueError("DataCapabilityReport.integrity must be an IntegrityReport")
        if not isinstance(self.hierarchy_checks, tuple):
            raise KernelValueError("DataCapabilityReport.hierarchy_checks must be a tuple")
        if not isinstance(self.runnable, bool):
            raise KernelValueError("DataCapabilityReport.runnable must be a bool")
        if self.runnable and self.reason is not None:
            raise KernelValueError(
                "a runnable capability report carries no rejection reason (D06 §10.5)"
            )
        if not self.runnable and self.reason is None:
            raise KernelValueError(
                "a capability report that blocks the run must say why (D06 §10.5)"
            )


def config_digest_of(config: RunConfig) -> ConfigDigest:
    """入力とポリシーの群から `ConfigDigest` を作る（D06 §9.3）。

    `RunConfig` は入力（snapshot・戦略・区間・執行系列・seed・口座）とポリシーの参照を
    ちょうど持ち、識別の群（`RunId` や git の状態）は持たない。そのため設定そのものの
    正規化エンコードがこのダイジェストの対象になる。
    """
    if not isinstance(config, RunConfig):
        raise KernelValueError("config_digest_of requires a RunConfig")
    return ConfigDigest(digest=digest(config))


@dataclass(frozen=True, slots=True)
class RunManifest:
    """1回の run の固定 manifest（D06 §9.3）。"""

    run_id: RunId
    config: RunConfig
    config_digest: ConfigDigest
    phases: PhaseSet
    id_allocator_snapshot: Mapping[str, int]
    capability_report: DataCapabilityReport
    resolution_hierarchy: ResolutionHierarchy
    unresolved_intrabar_count: int
    unresolved_intrabar_ratio: Decimal
    swap_modeled: bool
    status: str
    symbol_spec_ref: SymbolSpecRef
    calendar_ref: str
    timeframe_def_refs: tuple[TimeframeRef, ...] = ()
    git_commit: str = ""
    git_dirty: bool = False
    reason: Reason | None = None
    code_digest: str = ""
    lock_digest: str = ""
    env_digest: str = ""
    warnings: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise KernelValueError("RunManifest.run_id must be a RunId")
        if not isinstance(self.config, RunConfig):
            raise KernelValueError("RunManifest.config must be a RunConfig")
        if not isinstance(self.config_digest, ConfigDigest):
            raise KernelValueError("RunManifest.config_digest must be a ConfigDigest")
        if not isinstance(self.phases, PhaseSet):
            raise KernelValueError("RunManifest.phases must be a PhaseSet")
        if not isinstance(self.id_allocator_snapshot, Mapping):
            raise KernelValueError("RunManifest.id_allocator_snapshot must be a Mapping")
        if not isinstance(self.capability_report, DataCapabilityReport):
            raise KernelValueError("RunManifest.capability_report must be a DataCapabilityReport")
        if isinstance(self.unresolved_intrabar_count, bool) or not isinstance(
            self.unresolved_intrabar_count, int
        ):
            raise KernelValueError("RunManifest.unresolved_intrabar_count must be an int")
        if self.swap_modeled is not False:
            raise KernelValueError("RunManifest.swap_modeled must be False in stage 2 (ADR-0029)")
