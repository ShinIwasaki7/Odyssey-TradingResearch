"""単一実行の評価 manifest と識別（D07 §8.1・§8.3・§9.2）。

段階2から作るのは、単一 run の評価 manifest が**再現性の条件そのもの**であり、実験
manifest（段階4）を足すときに置き場所を動かさないためである（D07 §2）。

**実行時刻を入れない**（D07 §8.3）。壁時計の時刻を入れると同じ判断履歴から同じ成果物が
出なくなり、再現性の条件を評価自身が壊す。いつ評価したかはファイルシステムの更新時刻で
足りる。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.money import CurrencyCode
from odyssey_fx.common.reason import Reason
from odyssey_fx.common.refs import CodeDigest, ContentDigest
from odyssey_fx.evaluation.domain.status import EvaluationStatus

__all__ = [
    "METRIC_SET_VERSION",
    "EvaluationManifest",
    "EvaluationTable",
    "RunEvaluationId",
    "run_evaluation_id",
]

#: 段階2の指標集合の版（D07 §5.2 の15件）。指標を足す・式を変えるときに上げる。
#: `RunEvaluationId` の材料なので、上げれば同じ run の評価結果が別の場所へ書かれる。
METRIC_SET_VERSION: Final = 1


class EvaluationTable(Enum):
    """評価が出す5表（D07 §8.1）。**どの状態でも5表すべてを書く**。

    指標を出さない状態（`FAILED` / `REJECTED`）では4表を0行で書く。表の有無で状態を
    表すと、書き出しが途中で落ちた成果物と区別できない。
    """

    METRICS = "METRICS"
    CATEGORY_COUNTS = "CATEGORY_COUNTS"
    TRADES = "TRADES"
    FILL_DIAGNOSTICS = "FILL_DIAGNOSTICS"
    CONSISTENCY_CHECKS = "CONSISTENCY_CHECKS"


@dataclass(frozen=True, slots=True)
class RunEvaluationId:
    """1回の評価の識別子（D07 §9.2）。

    `digest(run_id, metric_set_version, evaluation_code_digest)`。同じ判断履歴を別の指標
    集合の版で、あるいは別の評価コードで評価した結果が、別の識別子になる。

    D02 §7.1 の `EvaluationId`（部品の1回の評価）とは**別の型**であり、名前も混同しない。
    """

    digest: ContentDigest

    def __post_init__(self) -> None:
        if not isinstance(self.digest, ContentDigest):
            raise KernelValueError("RunEvaluationId.digest must be a ContentDigest")

    def __str__(self) -> str:
        return self.digest.hex


def run_evaluation_id(
    run_id: RunId, metric_set_version: int, evaluation_code_digest: CodeDigest
) -> RunEvaluationId:
    """`RunEvaluationId` を作る（D07 §9.2）。"""
    if not isinstance(run_id, RunId):
        raise KernelValueError("run_evaluation_id requires a RunId")
    if isinstance(metric_set_version, bool) or not isinstance(metric_set_version, int):
        raise KernelValueError("run_evaluation_id requires an int metric_set_version")
    if metric_set_version < 1:
        raise KernelValueError(
            f"metric_set_version starts at 1, got {metric_set_version} (D07 §9.2)"
        )
    if not isinstance(evaluation_code_digest, CodeDigest):
        raise KernelValueError("run_evaluation_id requires a CodeDigest")
    return RunEvaluationId(
        digest=digest(
            {
                "run_id": run_id.hex,
                "metric_set_version": metric_set_version,
                "evaluation_code_digest": evaluation_code_digest.digest.hex,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class EvaluationManifest:
    """評価 manifest（D07 §8.3）。JSON で保存する（ADR-0027）。

    **`swap_modeled` は必須項目**である（D07 §7.2）。省略も `None` も許さない。指標を
    抜き出して比較した時点で注記が消えないよう、manifest と指標の両方に置く。

    **実行の能力検査（`DataCapabilityReport`）を写さない**（D07 §8.1）。正本は run manifest
    と結果 DTO であり、評価 manifest は `run_manifest_ref` と `run_status` /
    `run_failure_reason` でそこへ辿れる形だけを持つ。
    """

    run_evaluation_id: RunEvaluationId
    run_id: RunId
    run_manifest_ref: ContentDigest
    metric_set_version: int
    evaluation_code_digest: CodeDigest
    run_code_digest: CodeDigest
    run_status: RunStatus
    run_failure_reason: Reason | None
    account_currency: CurrencyCode
    swap_modeled: bool
    status: EvaluationStatus
    result_digest: ContentDigest
    input_tables: tuple[TraceTable, ...]
    fatal_failure_count: int
    warning_failure_count: int

    def __post_init__(self) -> None:
        for name, expected in (
            ("run_evaluation_id", RunEvaluationId),
            ("run_id", RunId),
            ("run_manifest_ref", ContentDigest),
            ("evaluation_code_digest", CodeDigest),
            ("run_code_digest", CodeDigest),
            ("run_status", RunStatus),
            ("account_currency", CurrencyCode),
            ("status", EvaluationStatus),
            ("result_digest", ContentDigest),
        ):
            if not isinstance(getattr(self, name), expected):
                raise KernelValueError(f"EvaluationManifest.{name} must be a {expected.__name__}")
        if self.run_failure_reason is not None and not isinstance(self.run_failure_reason, Reason):
            raise KernelValueError("EvaluationManifest.run_failure_reason must be a Reason or None")
        if (self.run_status is RunStatus.COMPLETED) and self.run_failure_reason is not None:
            raise KernelValueError(
                "a completed run carries no failure reason (D06 §9.3); a manifest that records"
                " one would describe a run that both finished and failed"
            )
        if self.swap_modeled is not False:
            raise KernelValueError(
                "EvaluationManifest.swap_modeled must be False in stage 2 (ADR-0029);"
                " it is a required field so that a reader of the artifact alone can tell that"
                " swap / rollover was not modelled (D07 §7.2)"
            )
        if isinstance(self.metric_set_version, bool) or not isinstance(
            self.metric_set_version, int
        ):
            raise KernelValueError("EvaluationManifest.metric_set_version must be an int")
        if self.metric_set_version < 1:
            raise KernelValueError("EvaluationManifest.metric_set_version starts at 1")
        if not isinstance(self.input_tables, tuple) or not all(
            isinstance(table, TraceTable) for table in self.input_tables
        ):
            raise KernelValueError("EvaluationManifest.input_tables must be a tuple of TraceTable")
        for name in ("fatal_failure_count", "warning_failure_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise KernelValueError(f"EvaluationManifest.{name} must be an int")
            if value < 0:
                raise KernelValueError(f"EvaluationManifest.{name} must be >= 0")
        if self.status is EvaluationStatus.FAILED and self.fatal_failure_count == 0:
            raise KernelValueError(
                "an evaluation that FAILED must name at least one failing fatal check"
                " (D07 §10.1); otherwise the artifact cannot explain the failure"
            )
        if self.status is EvaluationStatus.COMPLETED and self.fatal_failure_count > 0:
            raise KernelValueError(
                "an evaluation with a failing fatal check cannot be COMPLETED (D07 §10.1)"
            )
