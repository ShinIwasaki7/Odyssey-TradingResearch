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

from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.evaluation.application.manifest import EvaluationTable

if TYPE_CHECKING:  # pragma: no cover - 型検査のためだけの参照
    # `evaluate_run` は実行時に本モジュールを import する。書き出し口の引数の型を
    # 正しく書くためだけの参照なので、実行時の循環を作らないようここへ置く。
    from odyssey_fx.evaluation.application.evaluate_run import EvaluationReport

__all__ = [
    "ColumnValueKind",
    "ManifestReadFailure",
    "ResultRepository",
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
