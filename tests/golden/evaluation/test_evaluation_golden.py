"""紙上トレース T01 の判断履歴から出る評価結果5表の固定出力（D07 §11 の golden）。

指標の式・注記の付け方・集計の鍵の並び・整列鍵が意図せず変わったことを、固定の期待値との
突合で検出する。値が変わる変更は、この期待値の更新と設計文書の改訂を伴うべきものである。

入力は T01 第9節の run（完了取引1件と残存建玉1件）が9表へ残す行で、値はすべて T01 の
数値である（`tests/fixtures/evaluation/traces.py`）。

**実行の識別子は固定値に置き換えて比べる**。整合検査の期待値・観測値には実行の識別子が
入り、その識別子はコードのダイジェストから決まる（ADR-0006）ので、コードを直すたびに
変わってしまう。識別子そのものの一致は受入れテストが確かめている。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from odyssey_fx.backtest.trace.recorder import column_names, flatten_row
from odyssey_fx.common.refs import CodeDigest, ContentDigest
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, EvaluationReport
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION, EvaluationTable
from odyssey_fx.evaluation.domain.metrics import (
    CategoryCount,
    FillDiagnostic,
    MetricRecord,
    TradeRecord,
)
from odyssey_fx.evaluation.domain.status import ConsistencyCheckResult
from tests.fixtures.evaluation import traces

GOLDEN_DIR = Path(__file__).parent / "expected"

#: 固定のダイジェスト。評価コードの実際の内容に依らない値にして、golden を安定させる。
_CODE_DIGEST = CodeDigest(digest=ContentDigest.sha256("e" * 64))

#: 表ごとの行の型（列の宣言を引くために使う）。
_ROW_TYPES = {
    EvaluationTable.METRICS: MetricRecord,
    EvaluationTable.CATEGORY_COUNTS: CategoryCount,
    EvaluationTable.TRADES: TradeRecord,
    EvaluationTable.FILL_DIAGNOSTICS: FillDiagnostic,
    EvaluationTable.CONSISTENCY_CHECKS: ConsistencyCheckResult,
}

#: 実行の識別子を置き換える印。
_RUN_ID_PLACEHOLDER = "<run_id>"


def _report() -> tuple[EvaluationReport, str]:
    manifest = traces.manifest_for()
    repository = traces.repository_for(manifest=manifest)
    result = traces.result_for(manifest)
    report = EvaluateRun(evaluation_code_digest=_CODE_DIGEST).evaluate(
        result, repository, METRIC_SET_VERSION, traces.CALENDAR
    )
    return report, str(manifest.run_id)


def _render(table: EvaluationTable, rows: Sequence[object], run_id: str) -> str:
    """表を1行1レコードのテキストへ書き出す（比較と差分の読みやすさのため）。

    列は行の型の宣言から引く（D06 §9.1 の平坦化規則）。行が1件も無い表でも見出しは出る。
    区切りにタブを使うのは、整合検査の期待値・観測値が正規化エンコード（読点を含む JSON
    互換のテキスト、D02 §9.3）だからである。読点で区切ると列の境目が読めなくなる。
    """
    names = column_names(_ROW_TYPES[table])
    lines = ["\t".join(names)]
    for row in rows:
        columns = flatten_row(row)
        values: list[str] = []
        for name in names:
            value = columns.get(name)
            if value is None:
                text = ""
            elif isinstance(value, list):
                # 可変長の列は `|` でつなぐ。Python の一覧表記のまま書くと引用符と読点が
                # 入り、CSV として読めない固定出力になる。
                text = "|".join(str(item) for item in value)
            else:
                text = str(value)
            values.append(text.replace(run_id, _RUN_ID_PLACEHOLDER))
        lines.append("\t".join(values))
    return "\n".join(lines) + "\n"


def _assert_matches_golden(name: str, actual: str) -> None:
    path = GOLDEN_DIR / name
    assert path.is_file(), (
        f"golden file {path} is missing; regenerate it deliberately and review the diff"
    )
    assert actual == path.read_text(encoding="utf-8"), (
        f"the evaluation output no longer matches {name}; a change here means the metric"
        " formulas, the caveats, the category vocabulary or a sort key changed (D07 §5・§6・§8.1)"
    )


def test_the_five_evaluation_tables_match_the_golden_output() -> None:
    """5表すべてを固定する（D07 §8.1 の整列鍵を含む）。"""
    report, run_id = _report()
    for table in EvaluationTable:
        _assert_matches_golden(
            f"{table.value.lower()}.tsv", _render(table, report.rows[table], run_id)
        )


def test_the_golden_output_is_the_paper_trace_scenario() -> None:
    """固定出力が意図どおりの入力から出ていること（T01 第9節の run であること）。"""
    report, _ = _report()
    assert len(report.metrics) == 19
    assert len(report.trades) == 1
    assert len(report.fill_diagnostics) == 3
    assert len(report.checks) == 13
    assert all(check.passed for check in report.checks)
