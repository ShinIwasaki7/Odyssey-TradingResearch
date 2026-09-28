"""人間向けレポート（D07 §22）。

保存済みの成果物（記録票・結末記録・run manifest・評価 manifest・評価の表）**だけ**から、
実験ごとに Markdown のファイル1つ（`runs/experiments/<名前>/v<版>/report.md`）を作る。

- **状態と理由を先頭に置く**（D07 §22.1）。数値の表だけを見て失敗に気づかない読み方を防ぐ
  ため、結論（採用できる結果かどうか）→ なぜそうなったか → 値なしの指標 → 指標 → 集計 →
  設定と入力の特定、の順に並べる。
- **決定論**: 壁時計の時刻・絶対パスを入れない。同じ成果物からは同じバイト列が出る。レポートは
  結果ダイジェストの対象に入れない（成果物から導いた表示であり、正本ではない）。
- **表示の桁**: 比率は小数第6位まで `ROUND_HALF_EVEN` で表示し、金額は保存値をそのまま出す。
  保存値は変えない（D07 §5.1 の Q2 決定: 表示の桁は報告の関心）。

run と評価の成果物は、実験の版のディレクトリと同じ成果物の根（`<根>/runs/<run_id>/`）から
読む。読めない成果物は例外にせず「読めない」とレポートに書く（読めないこと自体が説明の対象で
ある）。記録票が読めないとき、結末記録があるのに読めないときだけ `KernelValueError` で止める
（何の実験のレポートかを決められないため）。

Parquet を開くのはこのモジュールと `fs_store` だけである（D01 §5・ADR-0025）。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN
from enum import Enum
from pathlib import Path
from typing import Any, Final

import polars as pl

from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.money import decimal_from_str, kernel_context
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_OUTCOME_FILE,
    REPORT_FILE,
    FileSystemResultRepository,
    ensure_experiment_directory,
    keep_previous_report,
    read_experiment_manifest,
    read_experiment_outcome,
    write_new_file,
)
from odyssey_fx.evaluation.application.manifest import EvaluationTable
from odyssey_fx.evaluation.application.ports import ManifestReadFailure
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentManifest,
    ExperimentOutcome,
    ExperimentStatus,
)
from odyssey_fx.evaluation.domain.metrics import MetricId
from odyssey_fx.evaluation.domain.research_policy import PolicyCheckResult
from odyssey_fx.evaluation.domain.status import CheckOutcome, EvaluationStatus

__all__ = [
    "ReportWrite",
    "build_report",
    "write_report",
]

#: 参考値の指標（D07 §5.2 の表で「参考値」とした5件。§5.3・§5.4）。残りは採用指標。
_REFERENCE_METRICS: Final = frozenset(
    {
        MetricId.MAX_DRAWDOWN_BALANCE.value,
        MetricId.MAX_DRAWDOWN_BALANCE_RATE.value,
        MetricId.COST_PRICE_EMBEDDED_TOTAL.value,
        MetricId.END_EQUITY_MTM.value,
        MetricId.HYPOTHETICAL_CLOSED_PROFIT.value,
    }
)

#: 指標の日本語名（表示だけに使う。識別子は `MetricId` のまま併記する）。
_METRIC_LABELS: Final[Mapping[str, str]] = {
    "NET_PROFIT": "純損益",
    "CLOSED_TRADE_PROFIT": "完了取引の損益",
    "TRADE_COUNT": "完了取引数",
    "WIN_RATE": "勝率",
    "MAX_DRAWDOWN_MTM": "最大ドローダウン（含み損益込み）",
    "MAX_DRAWDOWN_MTM_RATE": "最大ドローダウン率（含み損益込み）",
    "MAX_DRAWDOWN_BALANCE": "最大ドローダウン（確定損益）",
    "MAX_DRAWDOWN_BALANCE_RATE": "最大ドローダウン率（確定損益）",
    "EXPOSURE_RATE": "建玉保有時間の割合",
    "COST_CHARGED_TOTAL": "控除した費用の合計",
    "COST_PRICE_EMBEDDED_TOTAL": "価格に反映済みの費用の合計",
    "MAX_ADVERSE_FILL_OFFSET": "最大の不利約定幅",
    "END_EQUITY_MTM": "含み損益込みの最終資産",
    "HYPOTHETICAL_CLOSED_PROFIT": "仮決済損益",
    "NET_RETURN_RATE": "純収益率",
    "ANNUALIZED_RETURN": "年率化リターン",
    "ANNUALIZED_SHARPE_RATIO": "年率化シャープレシオ",
    "PROFIT_FACTOR": "プロフィットファクター",
    "AVERAGE_TRADE_PROFIT": "平均取引損益",
}

#: 比率の表示の桁（小数第6位。D07 §22.1）。
_RATIO_QUANTUM: Final = decimal_from_str("0.000001")

#: 評価の表のうちレポートが読む3表（取引と約定の診断は件数の多い明細なので載せない）。
_READ_TABLES: Final = (
    EvaluationTable.METRICS,
    EvaluationTable.CATEGORY_COUNTS,
    EvaluationTable.CONSISTENCY_CHECKS,
)


class ReportWrite(Enum):
    """`write_report` の結果（D07 §22.1 の書き込み規則）。"""

    #: `report.md` が無かったので書いた。
    CREATED = "CREATED"
    #: 既存の `report.md` と内容が同じなので何もしなかった。
    UNCHANGED = "UNCHANGED"
    #: 既存の `report.md` と内容が違うので `report.<n>.md` へ退避してから書いた。
    REPLACED = "REPLACED"


# --- 読み出し -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Evaluation:
    """評価 manifest と3表（読めた場合）。"""

    manifest: Mapping[str, Any]
    tables: Mapping[EvaluationTable, tuple[Mapping[str, str | None], ...]]


def _read_rows(path: Path) -> tuple[Mapping[str, str | None], ...]:
    frame = pl.read_parquet(path)
    rows: list[Mapping[str, str | None]] = []
    for record in frame.iter_rows(named=True):
        rows.append(
            {name: (None if value is None else _cell_text(value)) for name, value in record.items()}
        )
    return tuple(rows)


def _cell_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "|".join(str(item) for item in value)
    return str(value)


def _read_evaluation(root: Path, run_id: str, run_evaluation_id: str) -> _Evaluation | str:
    """評価の成果物を読む。読めなければ理由（絶対パスを含めない文）を返す。"""
    relative = f"runs/{run_id}/eval/{run_evaluation_id}"
    directory = Path(root) / "runs" / run_id / "eval" / run_evaluation_id
    if directory.is_symlink() or not directory.is_dir():
        return f"{relative}/ が無い（またはディレクトリではない）"
    try:
        manifest = json.loads((directory / "evaluation.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, Mapping):
            return f"{relative}/evaluation.json が JSON のオブジェクトではない"
        tables = {table: _read_rows(directory / f"{table.value}.parquet") for table in _READ_TABLES}
    except OSError as exc:
        return f"{relative}/ の成果物を読めない: {type(exc).__name__}: {exc.strerror}"
    except (ValueError, pl.exceptions.PolarsError) as exc:
        return f"{relative}/ の成果物を読めない: {type(exc).__name__}"
    return _Evaluation(manifest=manifest, tables=tables)


def _read_run_manifest(root: Path, run_id: RunId) -> RunManifest | str:
    read = FileSystemResultRepository(root=Path(root)).read_manifest(run_id)
    if isinstance(read, ManifestReadFailure):
        return f"runs/{run_id}/manifest.json を読めない: {read.detail}"
    return read


# --- 表示の小道具 ---------------------------------------------------------------


def _code(text: object) -> str:
    return f"`{text}`"


def _cell(text: object) -> str:
    """表のセル。縦棒と改行を逃がす（Markdown の表を壊さない）。"""
    return str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(_cell(item) for item in row) + " |" for row in rows)
    return lines


def _ratio_text(text: str) -> str:
    """比率の表示（小数第6位まで `ROUND_HALF_EVEN`。保存値は変えない）。"""
    try:
        value = decimal_from_str(text)
    except KernelValueError:
        return text
    shown = value.quantize(_RATIO_QUANTUM, rounding=ROUND_HALF_EVEN, context=kernel_context())
    return format(shown, "f")


def _metric_value_text(row: Mapping[str, str | None]) -> str:
    kind = row.get("value_kind")
    if row.get("value_reason"):
        return f"値なし（{row['value_reason']}）"
    if kind == "AMOUNT":
        return f"{row.get('value_amount_amount')} {row.get('value_amount_currency')}"
    if kind == "RATIO":
        return _ratio_text(str(row.get("value_ratio")))
    if kind == "COUNT":
        return str(row.get("value_count"))
    if kind == "DURATION":
        return str(row.get("value_duration"))
    if kind == "PRICE_OFFSET":
        return str(row.get("value_offset"))
    return "（読めない値）"


def _metric_name(metric_id: str) -> str:
    label = _METRIC_LABELS.get(metric_id)
    return metric_id if label is None else f"{label}（{metric_id}）"


def _caveats(row: Mapping[str, str | None]) -> str:
    text = row.get("caveats") or ""
    return "、".join(item for item in text.split("|") if item) or "なし"


def _policy_rows(checks: Sequence[PolicyCheckResult]) -> list[list[str]]:
    return [
        [item.check.value, item.stage.value, item.outcome.value, item.expected, item.observed]
        for item in checks
        if item.outcome is not CheckOutcome.PASSED
    ]


# --- 本文 -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Inputs:
    manifest: ExperimentManifest
    outcome: ExperimentOutcome | None
    run_manifest: RunManifest | str | None
    evaluation: _Evaluation | str | None
    experiment_dir_text: str


def _conclusion(inputs: _Inputs) -> str:
    """結論の1文（D07 §22.1 の順1。`COMPLETED` かつ評価 `COMPLETED` のときだけ「採用可」）。"""
    outcome = inputs.outcome
    if outcome is None:
        return (
            "**採用不可**: 結末記録が無い＝途中で止まった実験である"
            "（記録票だけが残っている。D07 §19.4）。"
        )
    if outcome.status is ExperimentStatus.REJECTED_BY_POLICY:
        return "**採用不可**: 研究ポリシーの事前検査に合格しなかったので run していない。"
    if outcome.status is ExperimentStatus.FAILED_POST_RUN_CHECK:
        return (
            "**採用不可**: 研究ポリシーの事後検査に合格しなかった"
            "（成果物は残すが採用しない。D07 §19.4）。"
        )
    if outcome.evaluation_status is EvaluationStatus.COMPLETED:
        if isinstance(inputs.evaluation, str):
            return f"**採用不可**: 評価の成果物を読めない（{inputs.evaluation}）。"
        return (
            "**採用可**: 研究ポリシーの検査に合格し、run と評価が正常に完了した"
            "（戦略の採否そのものは判断していない）。"
        )
    if outcome.evaluation_status is EvaluationStatus.REJECTED:
        run_status = None if outcome.run_status is None else outcome.run_status.value
        return (
            f"**採用不可**: run が正常完走していない（`{run_status}`）ので、"
            "評価は指標を出していない（D07 §10.1）。"
        )
    status = None if outcome.evaluation_status is None else outcome.evaluation_status.value
    return f"**採用不可**: 評価が正常に完了していない（`{status}`。D07 §10.1）。"


def _status_lines(inputs: _Inputs) -> list[str]:
    outcome = inputs.outcome
    if outcome is None:
        return [
            "- 実験の状態: 結末記録なし（途中で止まった）",
            "- run の状態: 不明（結末記録が無い）",
            "- 評価の状態: 不明（結末記録が無い）",
        ]
    run = "run していない" if outcome.run_status is None else _code(outcome.run_status.value)
    if outcome.run_reused:
        run += "（既存の run 成果物を再利用）"
    evaluation = (
        "評価していない"
        if outcome.evaluation_status is None
        else _code(outcome.evaluation_status.value)
    )
    return [
        f"- 実験の状態: {_code(outcome.status.value)}",
        f"- run の状態: {run}",
        f"- 評価の状態: {evaluation}",
    ]


def _metric_rows(inputs: _Inputs) -> tuple[Mapping[str, str | None], ...]:
    if not isinstance(inputs.evaluation, _Evaluation):
        return ()
    return inputs.evaluation.tables[EvaluationTable.METRICS]


def _zero_trades(inputs: _Inputs) -> bool:
    for row in _metric_rows(inputs):
        if row.get("metric_id") == MetricId.TRADE_COUNT.value:
            return row.get("value_count") == "0"
    return False


def _capability_lines(run_manifest: RunManifest) -> list[str]:
    """実行前のデータ能力検査の結果（run manifest に記録されたもの。D06 §10.5、D07 §23.3）。"""
    report = run_manifest.capability_report
    reason = None if report.reason is None else report.reason.code.value
    lines = [
        "### 実行前のデータ能力検査（run manifest の記録）",
        "",
        f"- 実行可否: {'実行可' if report.runnable else '実行不可'}"
        + ("" if reason is None else f"（理由 {_code(reason)}）"),
        f"- 戦略のコンパイル結果との一致: {'一致' if report.compiled_match else '不一致'}",
    ]
    counts = Counter(
        (result.kind.value, result.severity.value, str(result.series))
        for result in report.integrity.results
    )
    if counts:
        lines.append("- 完全性検査の記録（種別・重大度・系列ごとの件数）:")
        lines.append("")
        lines.extend(
            _table(
                ["種別", "重大度", "系列", "件数"],
                [
                    [kind, severity, series, count]
                    for (kind, severity, series), count in sorted(counts.items())
                ],
            )
        )
    else:
        lines.append("- 完全性検査の記録: なし")
    failed_levels = [check for check in report.hierarchy_checks if not check.passed]
    if failed_levels:
        lines.append(f"- 下位足の階層の検査で合格でないもの: {len(failed_levels)} 件")
    if report.diagnostics:
        lines.append("- 不合格の理由（能力検査の診断）:")
        lines.extend(f"  - {item}" for item in report.diagnostics)
    return lines


def _why_lines(inputs: _Inputs) -> list[str]:
    """なぜそうなったか（D07 §22.1 の順2）。合格でない検査は全件、期待値と観測値つきで。"""
    lines: list[str] = []
    checks: list[PolicyCheckResult] = list(inputs.manifest.pre_run_checks)
    if inputs.outcome is not None:
        checks.extend(inputs.outcome.outcome_checks)
    policy = _policy_rows(checks)
    lines.append("### 研究ポリシーの検査で合格でないもの")
    lines.append("")
    if policy:
        lines.extend(_table(["検査", "時点", "結果", "期待値", "観測値"], policy))
    else:
        lines.append("なし（記録された検査はすべて合格）。")
    lines.append("")

    lines.append("### run の失敗理由")
    lines.append("")
    run_manifest = inputs.run_manifest
    if run_manifest is None:
        lines.append("run していない。")
    elif isinstance(run_manifest, str):
        lines.append(f"run manifest を読めない: {run_manifest}")
    else:
        if run_manifest.reason is None:
            lines.append(f"なし（run の状態は {_code(run_manifest.status)}）。")
        else:
            lines.append(
                f"run の状態は {_code(run_manifest.status)}、失敗理由は"
                f" {_code(run_manifest.reason.code.value)}。"
            )
        lines.append("")
        lines.extend(_capability_lines(run_manifest))
    lines.append("")

    lines.append("### 整合検査で合格でないもの（読めなかったものを含む）")
    lines.append("")
    evaluation = inputs.evaluation
    if evaluation is None:
        lines.append("評価していない。")
    elif isinstance(evaluation, str):
        lines.append(f"評価の成果物を読めない: {evaluation}")
    else:
        failed = [
            [
                row.get("check"),
                row.get("level"),
                row.get("outcome"),
                row.get("table") or "",
                row.get("expected"),
                row.get("observed"),
            ]
            for row in evaluation.tables[EvaluationTable.CONSISTENCY_CHECKS]
            if row.get("outcome") != CheckOutcome.PASSED.value
        ]
        if failed:
            lines.extend(_table(["検査", "水準", "結果", "表", "期待値", "観測値"], failed))
        else:
            lines.append("なし（整合検査はすべて合格）。")
    if _zero_trades(inputs):
        lines.append("")
        lines.append("### 0取引")
        lines.append("")
        lines.append(
            "**取引が0件だったので値なしの指標がある**（取引に依存する指標は `NO_TRADES`。"
            "次の節を参照）。"
        )
    return lines


def _no_metrics_reason(inputs: _Inputs) -> str | None:
    """指標の表が無い理由（あれば）。"""
    if inputs.outcome is None:
        return "結末記録が無いので、指標は無い。"
    if inputs.evaluation is None:
        return "評価していないので、指標は無い。"
    if isinstance(inputs.evaluation, str):
        return f"評価の成果物を読めない: {inputs.evaluation}"
    if not _metric_rows(inputs):
        status = inputs.evaluation.manifest.get("status")
        return f"評価が指標を出していない（評価の状態 {_code(status)}。D07 §10.1）。"
    return None


def _unavailable_lines(inputs: _Inputs) -> list[str]:
    reason = _no_metrics_reason(inputs)
    if reason is not None:
        return [reason]
    rows = [
        [_metric_name(str(row.get("metric_id"))), row.get("value_reason")]
        for row in _metric_rows(inputs)
        if row.get("value_reason")
    ]
    if not rows:
        return ["なし（すべての指標に値がある）。"]
    return _table(["指標", "理由"], rows)


def _metric_lines(inputs: _Inputs) -> list[str]:
    reason = _no_metrics_reason(inputs)
    if reason is not None:
        return [reason]
    lines: list[str] = []
    for title, reference in (("採用指標", False), ("参考値（採否の判断に使わない）", True)):
        rows = [
            [
                _metric_name(str(row.get("metric_id"))),
                _metric_value_text(row),
                row.get("observation_count"),
                _caveats(row),
            ]
            for row in _metric_rows(inputs)
            if (str(row.get("metric_id")) in _REFERENCE_METRICS) is reference
        ]
        lines.append(f"### {title}")
        lines.append("")
        lines.extend(_table(["指標", "値", "観測数", "注記"], rows))
        lines.append("")
    lines.append(
        "比率は小数第6位まで表示している（`ROUND_HALF_EVEN`）。保存値は丸めていない"
        "（評価の成果物 `metrics.parquet` が正本）。"
    )
    return lines


def _category_lines(inputs: _Inputs) -> list[str]:
    evaluation = inputs.evaluation
    if evaluation is None:
        return ["評価していないので、集計は無い。" if inputs.outcome else "結末記録が無い。"]
    if isinstance(evaluation, str):
        return [f"評価の成果物を読めない: {evaluation}"]
    rows = [
        [row.get("category"), row.get("key"), row.get("count")]
        for row in evaluation.tables[EvaluationTable.CATEGORY_COUNTS]
    ]
    if not rows:
        status = _code(evaluation.manifest.get("status"))
        return [f"評価が集計を出していない（評価の状態 {status}）。"]
    return _table(["集計", "鍵", "件数"], rows)


def _optional_hex(value: ContentDigest | None) -> str:
    return "なし" if value is None else _code(value.hex)


def _identity_lines(inputs: _Inputs) -> list[str]:
    """設定と入力の特定（D07 §22.1 の順6）。"""
    manifest = inputs.manifest
    outcome = inputs.outcome
    policy = manifest.research_policy_ref
    access = Counter(item.value for item in manifest.allowed_partitions.values())
    lines = [
        f"- 記録票の識別子（experiment_id）: {_code(manifest.experiment_id.hex)}",
        f"- 実験: {_code(manifest.experiment_name)} 版 {manifest.experiment_version}"
        f"（書式 v{manifest.schema_version}）",
        f"- 仮説: {manifest.hypothesis}",
        f"- 研究ポリシー: {_code(policy.policy_id)} 版 {policy.version}"
        f"（ダイジェスト {_code(policy.digest.hex)}）",
        f"- 指標集合の版: {manifest.metric_set_version}",
        f"- 探索計画・分割: {_code(manifest.search_plan)}・{_code(manifest.split)}",
        f"- snapshot: {_code(manifest.snapshot_id)}",
        "- 許可した partition: "
        + f"{len(manifest.allowed_partitions)} 件（"
        + "、".join(f"{name} {count} 件" for name, count in sorted(access.items()))
        + "）",
        f"- 設定のダイジェスト（ConfigDigest。記録票の予測値）:"
        f" {_code(manifest.expected_config_digest.digest.hex)}",
        f"- 戦略定義のハッシュ: {_code(manifest.strategy_ref.digest.hex)}",
        f"- コンパイル結果のハッシュ: {_code(manifest.compiled_ref.digest.hex)}",
    ]
    if outcome is None:
        lines.append("- 実行の識別子（run_id）: 結末記録が無いので不明")
    else:
        lines.extend(
            [
                f"- 予測した実行の識別子（expected_run_id）: {_code(outcome.expected_run_id.hex)}",
                "- 実行の識別子（run_id）: "
                + ("run していない" if outcome.run_id is None else _code(outcome.run_id.hex)),
                f"- 評価の識別子（run_evaluation_id）: {_optional_hex(outcome.run_evaluation_id)}",
                f"- 結果のダイジェスト（result_digest）: {_optional_hex(outcome.result_digest)}",
                f"- この実行のコードのダイジェスト: {_code(outcome.code_digest.digest.hex)}",
                f"- この実行の依存 lock のダイジェスト: {_code(outcome.lock_digest.digest.hex)}",
                f"- この実行の環境のダイジェスト: {_code(outcome.env_digest.digest.hex)}",
                f"- git: {_code(outcome.git_commit or '（不明）')}"
                f"（未コミットの変更 {'あり' if outcome.git_dirty else 'なし'}）",
            ]
        )
    run_manifest = inputs.run_manifest
    if isinstance(run_manifest, RunManifest):
        lines.append(
            f"- run manifest の設定のダイジェスト: {_code(run_manifest.config_digest.digest.hex)}"
        )
    lines.append("- 記録票に保存した解決済みの設定ファイル（役割と SHA-256）:")
    lines.append("")
    lines.extend(
        _table(
            ["役割", "SHA-256"],
            [[item.role, _code(item.sha256)] for item in manifest.resolved_files],
        )
    )
    lines.append("")
    if outcome is not None and outcome.result_digest is not None:
        command = (
            "odyssey-fx experiment reproduce --experiment-dir "
            f"{inputs.experiment_dir_text} --snapshots <snapshot の基点>"
            " --out <別の出力の基点> --repo-root <リポジトリの根>"
        )
        lines.append(f"再現のコマンド（成果物の根で実行する）: `{command}`")
    else:
        lines.append(
            "再現のコマンド: 再現する結果（run と評価）が無いので無い（D07 §21.2 の手順1）。"
        )
    return lines


def _render(inputs: _Inputs) -> str:
    manifest = inputs.manifest
    sections: list[list[str]] = [
        [
            f"# 実験レポート: {manifest.experiment_name} 版 {manifest.experiment_version}",
            "",
            "保存済みの成果物（記録票・結末記録・run manifest・評価の成果物）だけから作った"
            "表示である（D07 §22）。正本はそれらの成果物であり、このファイルは結果の"
            "ダイジェストに入らない。",
        ],
        ["## 1. 結論", "", _conclusion(inputs), "", *_status_lines(inputs)],
        ["## 2. なぜそうなったか", "", *_why_lines(inputs)],
        ["## 3. 値なしの指標", "", *_unavailable_lines(inputs)],
        ["## 4. 指標", "", *_metric_lines(inputs)],
        ["## 5. 集計", "", *_category_lines(inputs)],
        ["## 6. 設定と入力の特定", "", *_identity_lines(inputs)],
    ]
    text = "\n\n".join("\n".join(section).rstrip("\n") for section in sections)
    return text + "\n"


def build_report(experiment_dir: Path, artifacts_root: Path) -> str:
    """実験の版のディレクトリの保存済み成果物からレポートの本文を作る（D07 §22.1）。

    `artifacts_root` は run と評価の成果物の根（`<根>/runs/<run_id>/` の `<根>`）。記録票が
    読めないとき、結末記録があるのに読めないときは `KernelValueError`。
    """
    directory = Path(experiment_dir)
    manifest = read_experiment_manifest(directory)
    outcome = read_experiment_outcome(directory)
    if outcome is not None and outcome.experiment_id != manifest.experiment_id:
        raise KernelValueError(
            f"{EXPERIMENT_OUTCOME_FILE} belongs to the experiment {outcome.experiment_id}, not to"
            f" the manifest {manifest.experiment_id}; the report would mix two experiments"
        )
    run_manifest: RunManifest | str | None = None
    evaluation: _Evaluation | str | None = None
    if outcome is not None and outcome.run_id is not None:
        run_manifest = _read_run_manifest(artifacts_root, outcome.run_id)
        if outcome.run_evaluation_id is not None:
            evaluation = _read_evaluation(
                artifacts_root, outcome.run_id.hex, outcome.run_evaluation_id.hex
            )
    return _render(
        _Inputs(
            manifest=manifest,
            outcome=outcome,
            run_manifest=run_manifest,
            evaluation=evaluation,
            experiment_dir_text=(
                f"runs/experiments/{manifest.experiment_name}/v{manifest.experiment_version}"
            ),
        )
    )


def write_report(experiment_dir: Path, artifacts_root: Path) -> ReportWrite:
    """レポートを書く（D07 §22.1）。

    既存の `report.md` が無ければ書く。あり、内容が同じなら何もしない。違えば
    `report.<n>.md` へ退避してから書く（旧い記録は消さない。R4 の書き込み規則）。
    """
    directory = ensure_experiment_directory(Path(artifacts_root), Path(experiment_dir))
    text = build_report(directory, artifacts_root)
    path = directory / REPORT_FILE
    result = ReportWrite.CREATED
    if path.exists() or path.is_symlink():
        if not path.is_symlink() and path.is_file() and path.read_text(encoding="utf-8") == text:
            return ReportWrite.UNCHANGED
        keep_previous_report(directory)
        result = ReportWrite.REPLACED
    try:
        write_new_file(path, text)
    except FileExistsError:
        raise KernelValueError(
            f"{REPORT_FILE} appeared while the report was being written; nothing was overwritten"
        ) from None
    return result
