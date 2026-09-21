"""判断履歴と結果の保存（D06 §9.1、ADR-0027）。

`TraceSink` と `ResultWriter`（`backtest.application.ports`）を実装する。ポート定義は
import せず、構造的に満たす（D01 §2.2 規則7）。保存先は `runs/<run_id>/` で、表形式データは
Parquet、manifest は JSON である。

**平坦化の規則は `backtest.trace.recorder` が持つ**（D06 §9.1 が決めたのは backtest 側の
責務）。ここは受け取った行を `flatten_row` で列の辞書にしてから書くだけで、規則を持たない。
DataFrame はこのモジュールの外へ出さない（D01 §2.2 規則1）。

十進数はすべて**文字列列**として保存する。二進浮動小数へ落とすと再現性が壊れる
（ADR-0012）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from odyssey_fx.backtest.trace.manifest import DataCapabilityReport, RunManifest
from odyssey_fx.backtest.trace.recorder import (
    TraceTable,
    canonical_text,
    flatten_row,
    table_column_kinds,
    table_columns,
)
from odyssey_fx.backtest.trace.result import BacktestResult

__all__ = ["FileSystemResultWriter", "FileSystemTraceSink", "run_directory"]


def run_directory(root: Path, run_id: object) -> Path:
    """`runs/<run_id>/`（ADR-0027）。"""
    return Path(root) / "runs" / str(run_id)


def _columns(
    rows: Sequence[Mapping[str, object]], declared: Sequence[str]
) -> dict[str, list[object]]:
    """行の並びを保ったまま、列ごとの値へ組み替える。

    行ごとに列が欠けることは無い（平坦化の規則が「その行の変種に無い列は `None`」と定めて
    いる）。**行が1件も無い表でも列は落とさない**。取引が1件も無かった正常な run と、必須の
    列を欠いた壊れた表とを読む側が区別できなくなるためである。
    """
    names: list[str] = list(declared)
    for row in rows:
        for name in row:
            if name not in names:
                names.append(name)
    return {name: [row.get(name) for row in rows] for name in names}


@dataclass(frozen=True, slots=True)
class FileSystemTraceSink:
    """15表を `runs/<run_id>/<TABLE>.parquet` へ書く（D06 §9.1・§9.2）。"""

    root: Path
    run_id: object

    def write(self, table: TraceTable, rows: tuple[object, ...]) -> None:
        """1つの表を書き出す。書き出し専用で、検索元にはならない。

        **全行が `run_id` を持つ**（上位設計書 §4.7.15）。連番 ID は run 内でのみ一意なので、
        永続参照は `(run_id, ID)` の組になる。行そのものが `run_id` を持たない表でも、
        ここで必ず列として足す。
        """
        directory = run_directory(self.root, self.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        run_id = str(self.run_id)
        flattened = [{"run_id": run_id, **flatten_row(row)} for row in rows]
        columns = _columns(flattened, table_columns(table))
        kinds = table_column_kinds(table)
        frame = pl.DataFrame(
            columns,
            schema={name: _dtype_of(kinds.get(name, "string")) for name in columns},
            strict=False,
        )
        frame.write_parquet(directory / f"{table.value}.parquet")


#: 列の物理的な型（`backtest.trace.recorder` が宣言した名前から引く）。
_DTYPES: Mapping[str, pl.DataType] = {
    "string": pl.String(),
    "int": pl.Int64(),
    "bool": pl.Boolean(),
    "list": pl.List(pl.String()),
}


def _dtype_of(kind: str) -> pl.DataType:
    """宣言された型に対応する物理的な型。

    行の有無で型が変わらないよう、**推論せず宣言に従う**。十進数は文字列で保存する決まり
    なので（ADR-0012）、実際に文字列以外になるのは整数・真偽・`list` の列だけである。
    """
    return _DTYPES.get(kind, pl.String())


@dataclass(frozen=True, slots=True)
class FileSystemResultWriter:
    """run manifest と結果 DTO を JSON で書く（ADR-0027）。"""

    root: Path

    def write(self, result: BacktestResult, manifest: RunManifest) -> None:
        """`manifest.json` と `result.json` を書き出す。"""
        directory = run_directory(self.root, manifest.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(
            json.dumps(_manifest_payload(manifest), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (directory / "result.json").write_text(
            json.dumps(_result_payload(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _manifest_payload(manifest: RunManifest) -> dict[str, Any]:
    """run manifest の7群を JSON へ落とす（D06 §9.3）。

    能力検査の結果は**全体のまま**入れる（要約に畳まない）。入れ子のレコードは D02 §9.3 の
    正規化エンコード文字列にし、外部ツールが同じ文字列からダイジェストを再計算できるように
    する。
    """
    return {
        "run_id": str(manifest.run_id),
        "config_digest": str(manifest.config_digest.digest),
        "code_digest": manifest.code_digest.digest.hex,
        "lock_digest": manifest.lock_digest.digest.hex,
        "env_digest": manifest.env_digest.digest.hex,
        "git_commit": manifest.git_commit,
        "git_dirty": manifest.git_dirty,
        "config": canonical_text(manifest.config),
        "phases": [{"rank": phase.rank, "name": phase.name} for phase in manifest.phases.ordered()],
        "id_allocator_snapshot": dict(manifest.id_allocator_snapshot),
        "symbol_spec_ref": canonical_text(manifest.symbol_spec_ref),
        "calendar_ref": manifest.calendar_ref,
        "timeframe_def_refs": [str(ref) for ref in manifest.timeframe_def_refs],
        "resolution_hierarchy": [str(level) for level in manifest.resolution_hierarchy.levels],
        "unresolved_intrabar_count": manifest.unresolved_intrabar_count,
        "unresolved_intrabar_ratio": str(manifest.unresolved_intrabar_ratio),
        "swap_modeled": manifest.swap_modeled,
        "capability_report": _capability_payload(manifest.capability_report),
        "status": manifest.status,
        "reason": None if manifest.reason is None else manifest.reason.code.value,
        "warnings": list(manifest.warnings),
    }


def _capability_payload(report: DataCapabilityReport) -> dict[str, Any]:
    """データ能力検査の結果を**全体のまま** JSON へ落とす（D06 §9.3 の能力検査の群）。

    要約に畳まず、合格・不合格のどちらでも個別の結果を残す。run manifest と結果 DTO の
    両方が同じ内容を持つのは、`FAILED_CAPABILITY` の run では判断履歴の表が空になりうる
    ため、結果からも直接読めるようにするという D06 §9.4 の要求による。
    """
    return {
        "compiled_match": report.compiled_match,
        "runnable": report.runnable,
        "reason": None if report.reason is None else report.reason.code.value,
        "integrity": [canonical_text(result) for result in report.integrity.results],
        "hierarchy_checks": [canonical_text(check) for check in report.hierarchy_checks],
    }


def _result_payload(result: BacktestResult) -> dict[str, Any]:
    """結果 DTO を JSON へ落とす（D06 §9.4）。"""
    summaries = result.summaries
    return {
        "run_id": str(result.run_id),
        "status": result.status.value,
        "manifest_ref": result.manifest_ref,
        "trace_tables": {table.value: path for table, path in result.trace_tables.items()},
        "capability_report": _capability_payload(result.capability_report),
        "swap_modeled": result.swap_modeled,
        "unresolved_intrabar_count": result.unresolved_intrabar_count,
        "trade_count": result.trade_count,
        "opportunity_count": result.opportunity_count,
        "summaries": None
        if summaries is None
        else {
            "realized": canonical_text(summaries.realized),
            "equity_with_mtm": canonical_text(summaries.equity_with_mtm),
            "hypothetical_closed": canonical_text(summaries.hypothetical_closed),
            "cost_breakdown": {
                kind.value: canonical_text(amount)
                for kind, amount in summaries.cost_breakdown.items()
            },
        },
    }
