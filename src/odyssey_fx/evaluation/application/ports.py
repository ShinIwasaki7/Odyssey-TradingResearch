"""評価が外から受け取る口（D07 §4.3・§8.2、D01 §4）。

判断履歴の表を読むのも、評価結果を書くのも `ResultRepository` 経由である。**Parquet を
開くのは `evaluation.adapters.fs_store` だけ**で、`domain` と `application` に表形式
ライブラリを入れない（D01 §5・ADR-0025、契約 F5a が機械検査する）。

**表の読み出しに新しいポートを足さない**（D07 §3）。D01 §4 が `ResultRepository` を
「結果の読み書き」として既に確定しており、本モジュールはその操作を具体化するだけである。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.refs import ContentDigest, SnapshotRef
from odyssey_fx.evaluation.application.manifest import EvaluationTable, RunEvaluationId
from odyssey_fx.evaluation.domain.experiment import ExperimentManifest, ExperimentOutcome
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy

if TYPE_CHECKING:  # pragma: no cover - 型検査のためだけの参照
    # `evaluate_run` は実行時に本モジュールを import する。書き出し口の引数の型を
    # 正しく書くためだけの参照なので、実行時の循環を作らないようここへ置く。
    from odyssey_fx.evaluation.application.evaluate_run import EvaluationReport

__all__ = [
    "BacktestRunner",
    "ColumnValueKind",
    "EvaluationReadFailure",
    "ExperimentStore",
    "ManifestReadFailure",
    "ManifestSaveResult",
    "ResultReadFailure",
    "ResultRepository",
    "SnapshotCatalog",
    "StoredEvaluation",
    "TableReadResult",
    "TraceColumnSpec",
]


class ColumnValueKind(Enum):
    """読んだ文字列をどの型として解釈するか（D07 §4.3）。

    判断履歴の列はすべて文字列（または `None`）として渡り、解釈は `evaluation.application`
    が行う。十進数の単一の列は人が読める固定小数表記なので `Decimal(文字列)` で厳密に
    往復する（D06 §9.1 v1.3）。
    """

    #: そのまま文字列として読む（ID・銘柄・列挙の値など）。
    STRING = "STRING"
    #: `Decimal(文字列)` で厳密に往復する十進数。
    DECIMAL = "DECIMAL"
    #: 十進整数。
    INT = "INT"
    #: D02 §3.1 の UTC 時刻文字列。
    TIME = "TIME"
    #: 列挙の値（語彙の正本は各設計文書）。
    ENUM = "ENUM"
    #: 正規化エンコード文字列の列（可変長の入れ子）。
    LIST_STRING = "LIST_STRING"


@dataclass(frozen=True, slots=True)
class TraceColumnSpec:
    """読み出す列1件の宣言（D07 §4.2・§4.3）。

    `required=True` の列が表に無ければ、その場で例外にせず**致命の整合検査の不合格**として
    記録する（D07 §4.3、§10.2 の C1）。評価は「なぜ評価できなかったか」を残すことが仕事
    であり、読み出し時に落ちると理由が残らない。
    """

    table: TraceTable
    column: str
    value_kind: ColumnValueKind
    required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.table, TraceTable):
            raise KernelValueError("TraceColumnSpec.table must be a TraceTable")
        if not isinstance(self.column, str) or not self.column:
            raise KernelValueError("TraceColumnSpec.column must be a non-empty str")
        if not isinstance(self.value_kind, ColumnValueKind):
            raise KernelValueError("TraceColumnSpec.value_kind must be a ColumnValueKind")
        if not isinstance(self.required, bool):
            raise KernelValueError("TraceColumnSpec.required must be a bool")


@dataclass(frozen=True, slots=True)
class TableReadResult:
    """表1つの読み出し結果（D07 §4.3）。

    **列が無いことと、行が0件であることを戻り値で区別する**。行の `tuple` だけを返す形に
    すると、0行の表では「必須列はあるが行が無い」と「列自体が無い」を呼び出し側が区別
    できず、整合検査 C1 を実施できない。

    `rows` は**要求した列だけ**を `TraceColumnSpec` の順に並べた、文字列（または `None`）の
    行の `tuple` である。要求した列のうち表に無いものがあれば `rows` は空になる。
    """

    table: TraceTable
    table_present: bool
    missing_columns: tuple[str, ...] = ()
    rows: tuple[tuple[str | None, ...], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.table, TraceTable):
            raise KernelValueError("TableReadResult.table must be a TraceTable")
        if not isinstance(self.table_present, bool):
            raise KernelValueError("TableReadResult.table_present must be a bool")
        if not isinstance(self.missing_columns, tuple) or not all(
            isinstance(name, str) for name in self.missing_columns
        ):
            raise KernelValueError("TableReadResult.missing_columns must be a tuple of str")
        if not isinstance(self.rows, tuple):
            raise KernelValueError("TableReadResult.rows must be a tuple")
        for row in self.rows:
            if not isinstance(row, tuple) or not all(
                item is None or isinstance(item, str) for item in row
            ):
                raise KernelValueError(
                    "TableReadResult.rows must hold tuples of str | None (D07 §4.3)"
                )
        if self.rows and (self.missing_columns or not self.table_present):
            raise KernelValueError(
                "a table read that is missing columns (or the table itself) cannot also carry"
                " rows; the caller could not tell which columns the values belong to"
                " (D07 §4.3)"
            )


@dataclass(frozen=True, slots=True)
class ManifestReadFailure:
    """run manifest を読めなかったこと（D07 v2.0 §3、§10.1.1 の R1-D07-4）。

    `detail` は読めなかった理由（D02 §9.3 の正規化エンコード文字列）。評価はこれを整合検査
    C11 `run_manifest_readable` の観測値として残す。構造エラーとして例外にすると、評価の
    成果物が何も残らず、run のディレクトリが壊れていることを結果から説明できない。
    """

    run_id: RunId
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise KernelValueError("ManifestReadFailure.run_id must be a RunId")
        if not isinstance(self.detail, str) or not self.detail:
            raise KernelValueError("ManifestReadFailure.detail must be a non-empty str")


@dataclass(frozen=True, slots=True)
class ResultReadFailure:
    """保存済みの結果 DTO（`runs/<run_id>/result.json`）を読めなかったこと（D07 v2.0 §3・§4.1）。

    `ManifestReadFailure` と同じ形。結果 DTO は評価の入力1そのものなので、これを受け取った
    呼び出し側は評価を始めない（`evaluate` コマンドは終了コード 2、`RunExperiment` は既存の
    成果物を再利用できないとして拒否。D07 §4.1）。
    """

    run_id: RunId
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise KernelValueError("ResultReadFailure.run_id must be a RunId")
        if not isinstance(self.detail, str) or not self.detail:
            raise KernelValueError("ResultReadFailure.detail must be a non-empty str")


@dataclass(frozen=True, slots=True)
class StoredEvaluation:
    """保存済みの評価 manifest から読んだ、再利用の判断に要る値（D07 §19.6 の手順2）。"""

    run_id: RunId
    run_evaluation_id: RunEvaluationId
    metric_set_version: int
    status: EvaluationStatus
    result_digest: ContentDigest

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise KernelValueError("StoredEvaluation.run_id must be a RunId")
        if not isinstance(self.run_evaluation_id, RunEvaluationId):
            raise KernelValueError("StoredEvaluation.run_evaluation_id must be a RunEvaluationId")
        if isinstance(self.metric_set_version, bool) or not isinstance(
            self.metric_set_version, int
        ):
            raise KernelValueError("StoredEvaluation.metric_set_version must be an int")
        if not isinstance(self.status, EvaluationStatus):
            raise KernelValueError("StoredEvaluation.status must be an EvaluationStatus")
        if not isinstance(self.result_digest, ContentDigest):
            raise KernelValueError("StoredEvaluation.result_digest must be a ContentDigest")


@dataclass(frozen=True, slots=True)
class EvaluationReadFailure:
    """評価の保存先はあるが、評価 manifest を読めなかったこと（D07 §19.6 の手順5）。"""

    run_id: RunId
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise KernelValueError("EvaluationReadFailure.run_id must be a RunId")
        if not isinstance(self.detail, str) or not self.detail:
            raise KernelValueError("EvaluationReadFailure.detail must be a non-empty str")


@runtime_checkable
class ResultRepository(Protocol):
    """run の成果物の読み書き（D01 §4、D07 §4.3・§8.2）。

    実装は `evaluation.adapters.fs_store`。ポート定義は import せず、構造的に満たす
    （D01 §2.2 規則7）。
    """

    def read_manifest(self, run_id: RunId) -> RunManifest | ManifestReadFailure:
        """run manifest を読む（D06 §9.3）。

        **読めないとき（ファイルが無い・壊れている）は例外にせず `ManifestReadFailure` を
        返す**（D07 v2.0 §3、§10.1.1 の R1-D07-4）。評価はそれを整合検査 C11 の不合格として
        残し、manifest を使わない検査は実施する。
        """
        ...

    def read_table(
        self, run_id: RunId, table: TraceTable, columns: tuple[TraceColumnSpec, ...]
    ) -> TableReadResult:
        """判断履歴の表から、要求した列だけを読む（D07 §4.3）。"""
        ...

    def write_evaluation(
        self, report: EvaluationReport, rows: Mapping[EvaluationTable, tuple[object, ...]]
    ) -> None:
        """評価結果の5表と評価 manifest を書く（D07 §8.2）。

        保存先が既にあれば、何も書かずに `ArtifactAlreadyExists` で失敗する（R4）。
        """
        ...

    def read_result(self, run_id: RunId) -> BacktestResult | ResultReadFailure:
        """`runs/<run_id>/result.json` を読む（D07 v2.0 §4.1。形式の正本は D06 §9.1・§9.4）。

        **読めないとき（ファイルが無い・壊れている・中身の `run_id` が引数と違う）は例外に
        せず `ResultReadFailure` を返す**。
        """
        ...

    def run_exists(self, run_id: RunId) -> bool:
        """`runs/<run_id>/` が既にあるか（D07 §19.6 の手順1。空・書きかけ・リンクも「ある」）。

        run manifest が読めないことと、保存先が無いことを区別するための操作である。区別しない
        と、壊れた既存の成果物を「無い」と読んで記録票を保存した後に、run の書き込みが
        `ArtifactAlreadyExists` で落ちる（D07 §19.6 の手順5 は書き込みの失敗を既存の成果物の
        検出に使わないと定める）。
        """
        ...

    def read_evaluation(
        self, run_id: RunId, run_evaluation_id: RunEvaluationId
    ) -> StoredEvaluation | EvaluationReadFailure | None:
        """評価 manifest を読む（D07 §19.6 の手順2・5）。

        読む先は `runs/<run_id>/eval/<run_evaluation_id>/evaluation.json`。

        保存先が無ければ `None`、あるが読めなければ `EvaluationReadFailure`。
        """
        ...


class ManifestSaveResult(Enum):
    """記録票の保存の結果（D07 §19.3）。"""

    #: 保存先に記録票が無かったので書いた。
    CREATED = "CREATED"
    #: 同じ識別子の記録票が既にあったので何もしなかった（同じ版の再実行は正当）。
    ALREADY_IDENTICAL = "ALREADY_IDENTICAL"
    #: 識別子の違う記録票（または読めない記録票）が既にあったので書かずに拒否した。
    CONFLICT = "CONFLICT"


@runtime_checkable
class ExperimentStore(Protocol):
    """実験の記録票と結末記録の保存（D01 §4、D07 §19.3）。実装は `evaluation.adapters.fs_store`。

    1つの実装は1つの実験の版のディレクトリ（`runs/experiments/<name>/v<version>/`）を扱う。
    """

    def save_manifest(self, manifest: ExperimentManifest) -> ManifestSaveResult:
        """記録票を保存する（D07 §19.3 の記録票の書き込み）。

        無ければ書く（一時ファイル＋改名で原子的に）。あり、識別子が同じなら何もしない。
        あり、識別子が違う（または読めない）なら書かずに `CONFLICT`。保存が成功した
        （`CREATED` か `ALREADY_IDENTICAL`）ときは、run を始める前に、同じ版に既にある結末
        記録を `experiment_outcome.<n>.json` へ退避する（同節の結末記録の書き込み）。
        """
        ...

    def read_manifest(self, path: str) -> ExperimentManifest:
        """実験の版のディレクトリ `path` の記録票を読む。読めなければ構造エラー。"""
        ...

    def write_outcome(self, outcome: ExperimentOutcome) -> None:
        """結末記録 `experiment_outcome.json` を書く（D07 §19.3）。"""
        ...


@runtime_checkable
class BacktestRunner(Protocol):
    """単一 run の実行（D01 §4、D07 §19.4）。実装は `app` が `RunBacktest` を適合させる。

    置換の指示を受け取らない（常に「存在すれば失敗」で書く。D07 §19.6 の手順4）。
    """

    def run(self, config: RunConfig, compiled: CompiledStrategy) -> BacktestResult:
        """run を1回行い、判断履歴・run manifest・結果を保存して結果を返す。"""
        ...


@runtime_checkable
class SnapshotCatalog(Protocol):
    """記録票に固定する許可 partition のアクセス分類の取得（D01 §4、D07 §20.3 の P2）。"""

    def access_classes(
        self, snapshot: SnapshotRef, partitions: frozenset[PartitionId]
    ) -> Mapping[PartitionId, AccessClass]:
        """snapshot の manifest が記録する、各 partition のアクセス分類。"""
        ...
