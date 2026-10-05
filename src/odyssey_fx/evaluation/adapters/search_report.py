"""探索の実験のレポートと試行台帳の一覧（D09 §11.5・§10.4・§8・§10.12.3・§11.4、D07 §22）。

探索の実験の版のディレクトリの保存済みの成果物（記録票・結末記録・開始記録・試行記録・選定記録・
束縛の記録・集約表 `trial_metrics`・評価の成果物・run の成果物）と、試行台帳と研究ポリシーの版の
登録簿**だけ**から、9つの見出し（D09 §11.5 の表）を同じ順・同じ見出し語で並べたレポートを作る。
**評価を求め直さない**（指標の値・条件の結果・判定は保存済みのものを写す。表示のための導出値
（最長の無取引期間・中央値・最小・最大）だけを domain の純粋関数で求める）。

- 単一実行の実験のレポート（`evaluation.adapters.report`）とは別の書式であり、あちらの出力は
  変えない。書き込みの規則（同じなら何もしない・違えば `report.<n>.md` へ退避してから書く）は
  同じ関数（`write_report_text`）を使う。
- **決定論**: 壁時計の時刻・絶対パスを入れない。読み出しの失敗の理由に絶対パスが含まれうる
  ものは、成果物の根を `<成果物の根>` に置き換えて出す（D07 §22.1）。
- 読めない表は例外にせず「読めない（理由）」と書く。ただし、同じ世代の記録が食い違う場合
  （束縛の照合の R2・R4。D09 §10.12.3）は記録を一世代として読めないので
  `SearchReportReadError`（読込の誤り。合成が終了コード 2 にする）で止める。
- 研究ポリシーの版の登録簿は合成が読んで照合し、要素を渡す（`app.config` は `evaluation` から
  参照できない。D01 §3）。

Parquet を開くのは `fs_store`・`report` とこのモジュールだけである（D01 §5・ADR-0025）。
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Final

import polars as pl

from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int, decimal_from_str, kernel_context
from odyssey_fx.common.refs import (
    CodeDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    SnapshotRef,
    StrategyRef,
)
from odyssey_fx.common.symbol import SymbolSpecRef
from odyssey_fx.common.time import UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.adapters.fs_store import (
    EXPERIMENT_OUTCOME_FILE,
    TRIAL_LEDGER_PATH,
    FileSystemResultRepository,
    evaluation_directory,
    read_experiment_manifest,
    read_experiment_outcome,
    read_ledger_binding,
    read_selections,
    read_trial_ledger_file,
    read_trial_metrics,
    read_unit_records,
    run_directory,
    unit_name,
)
from odyssey_fx.evaluation.adapters.report import ReportWrite, write_report_text
from odyssey_fx.evaluation.application.manifest import EvaluationTable, RunEvaluationId
from odyssey_fx.evaluation.application.ports import (
    EvaluationReadFailure,
    ManifestReadFailure,
    TrialLedgerContents,
    TrialLedgerReadFailure,
)
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentManifest,
    ExperimentOutcome,
    ExperimentStatus,
)
from odyssey_fx.evaluation.domain.metrics import (
    METRIC_KINDS,
    AmountValue,
    CountValue,
    DurationValue,
    MetricId,
    MetricKind,
    MetricRecord,
    MetricUnavailableReason,
    MetricValue,
    PriceOffsetValue,
    RatioValue,
    Unavailable,
)
from odyssey_fx.evaluation.domain.research_policy import RegistryEntry, current_standard_version
from odyssey_fx.evaluation.domain.search import (
    CandidateStatus,
    ComparisonBasis,
    ConditionOutcome,
    ConditionResult,
    ConditionScope,
    EvaluationStandard,
    FoldSelection,
    FoldVerdict,
    MetricCondition,
    ParameterAssignment,
    SearchOutcome,
    SearchPlan,
    SearchVerdict,
    StandardPurpose,
    SufficiencyShortfallKind,
    TrialLedgerBinding,
    TrialLedgerEvent,
    TrialLedgerLine,
    TrialPhase,
    TrialPlan,
    TrialRunRecord,
    TrialStatus,
    TrialUnitKey,
    ValidationUnitEvaluation,
    count_prior_executions,
    count_trial_statuses,
    derive_trial_status,
    finished_line_of,
    frequency_class_of,
    interim_floor_results,
    longest_idle_period,
    median_of,
    started_line_of,
)
from odyssey_fx.evaluation.domain.splits import Fold, SplitSpec
from odyssey_fx.evaluation.domain.status import CheckOutcome, EvaluationStatus
from odyssey_fx.marketdata.domain.integrity import CheckKind

__all__ = [
    "SEARCH_REPORT_HEADINGS",
    "SearchReportReadError",
    "basis_items",
    "build_search_report",
    "ledger_listing",
    "policy_standing",
    "write_search_report",
]

#: 探索の実験のレポートの見出し（D09 §11.5 の表。同じ順・同じ見出し語）。
SEARCH_REPORT_HEADINGS: Final = (
    "## 1. 判定",
    "## 2. 判定の理由",
    "## 3. fold ごとの成績",
    "## 4. 指標の計算可否",
    "## 5. fold 全体の水準",
    "## 6. 証拠の十分さ",
    "## 7. 比較の前提",
    "## 8. 試行台帳",
    "## 9. 診断（判定に使わない）",
)


class SearchReportReadError(KernelValueError):
    """記録を一世代として読めない（読込の誤り。終了コード 2。D09 §10.12.3 の R2・R4）。"""


# --- 語彙の表示 ---------------------------------------------------------------------

_VERDICT_NAMES: Final[Mapping[SearchVerdict, str]] = {
    SearchVerdict.MEETS_STANDARD: "共通基準を満たす",
    SearchVerdict.BELOW_STANDARD: "共通基準を満たさない",
    SearchVerdict.INSUFFICIENT_EVIDENCE: "証拠不足",
    SearchVerdict.INCOMPLETE: "判定できない",
    SearchVerdict.MET_IN_MECHANISM_CHECK: "条件をすべて満たした",
}

_FOLD_VERDICT_NAMES: Final[Mapping[FoldVerdict, str]] = {
    FoldVerdict.FLOORS_MET: "最低条件をすべて満たした",
    FoldVerdict.FLOOR_BREACHED: "最低条件を割った",
    FoldVerdict.INSUFFICIENT_EVIDENCE: "証拠不足",
    FoldVerdict.NO_ELIGIBLE_TRIAL: "足切りを通る試行が無い",
    FoldVerdict.INCOMPLETE: "判定できない",
}

_STATUS_NAMES: Final[Mapping[TrialStatus, str]] = {
    TrialStatus.NOT_STARTED: "未試行",
    TrialStatus.COMPLETED: "試行済み",
    TrialStatus.FAILED: "失敗",
    TrialStatus.ABORTED: "中断",
}

#: 用途の1行目（D09 §11.5 の順1）。
_PURPOSE_LINES: Final[Mapping[StandardPurpose, str]] = {
    StandardPurpose.STANDARD: "用途: 共通基準の判定",
    StandardPurpose.MECHANISM_CHECK: (
        "用途: 機構の確認（共通基準の判定ではない。最終検証に進めない）"
    ),
}

#: 判定の直後の定型の注記（D09 §11.5 の順1 の文言そのまま）。
_FIXED_NOTES: Final[Mapping[StandardPurpose, str]] = {
    StandardPurpose.STANDARD: (
        "この判定は研究履歴の中の検証区間で共通基準を満たしたかどうかを示すもので、"
        "実運用で利益が出ることを示すものではない"
    ),
    StandardPurpose.MECHANISM_CHECK: (
        "この判定は仕組みの動作を確かめたもので、共通基準の判定ではなく、"
        "実運用で利益が出ることも示さない"
    ),
}

#: 順3 の表に出す指標（D09 §11.5 の順3）。
_TABLE_METRICS: Final = (
    MetricId.TRADE_COUNT,
    MetricId.EXPOSURE_RATE,
    MetricId.NET_PROFIT,
    MetricId.MAX_DRAWDOWN_MTM,
    MetricId.MAX_DRAWDOWN_MTM_RATE,
    MetricId.COST_CHARGED_TOTAL,
    MetricId.COST_PRICE_EMBEDDED_TOTAL,
)

#: 比率の表示の桁（小数第6位。D07 §22.1）。
_QUANTUM: Final = decimal_from_str("0.000001")
_SECONDS_PER_DAY: Final = 86_400

_STOPPED: Final = "未確定（途中で止まった）"
_NOT_SEARCHED: Final = "探索を行っていない（事前検査で止めた）。"


def _code(text: object) -> str:
    return f"`{text}`"


def _cell(text: object) -> str:
    """表のセル。縦棒と改行を逃がす（Markdown の表を壊さない）。"""
    return str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = [
        "| " + " | ".join(_cell(item) for item in header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(_cell(item) for item in row) + " |" for row in rows)
    return lines


def _fixed(value: Decimal) -> str:
    """表示の丸め（小数第6位まで `ROUND_HALF_EVEN`。保存値は変えない）。末尾の 0 は落とす。"""
    context = kernel_context()
    context.prec = max(context.prec, value.adjusted() + 1 + 6 + 1)
    shown = value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN, context=context)
    text = format(shown, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _days(duration: timedelta) -> str:
    """長さを日で（小数第6位まで）。"""
    micro = (duration.days * _SECONDS_PER_DAY + duration.seconds) * 1_000_000 + (
        duration.microseconds
    )
    with localcontext(kernel_context()):
        value = decimal_from_int(micro) / decimal_from_int(_SECONDS_PER_DAY * 1_000_000)
    return f"{_fixed(value)} 日"


def _value_text(value: MetricValue) -> str:
    """指標の値の表示（金額は保存値のまま、比率は小数第6位まで。値なしは理由つき）。"""
    if isinstance(value, Unavailable):
        return f"値なし（{value.reason.value}）"
    if isinstance(value, AmountValue):
        return f"{format(value.amount.amount, 'f')} {value.amount.currency}"
    if isinstance(value, RatioValue):
        return _fixed(value.ratio)
    if isinstance(value, CountValue):
        return str(value.count)
    if isinstance(value, DurationValue):
        return _days(value.duration)
    if isinstance(value, PriceOffsetValue):
        return str(value.offset)
    return str(value)  # pragma: no cover - 値の区分は上で尽くしている


def _decimal_text(metric: MetricId, value: Decimal) -> str:
    """条件・選定の数値（比率は小数第6位まで、金額は保存値のまま）。"""
    if METRIC_KINDS[metric] is MetricKind.RATIO:
        return _fixed(value)
    return format(value, "f")


def _assignment_text(assignment: ParameterAssignment) -> str:
    return "、".join(
        f"{instance}.{parameter}={json.dumps(value.value, ensure_ascii=False)}"
        for instance, parameter, value in assignment.values
    )


def _verdict_text(verdict: SearchVerdict, purpose: StandardPurpose) -> str:
    """判定の表示（D09 §11.5 の順1。機構の確認では「機構の確認: 」を前に付ける）。"""
    prefix = "機構の確認: " if purpose is StandardPurpose.MECHANISM_CHECK else ""
    return f"{prefix}{_VERDICT_NAMES[verdict]}（{_code(verdict.value)}）"


def _fold_verdict_text(verdict: FoldVerdict, floor_breached_with_few_trades: bool) -> str:
    if floor_breached_with_few_trades:
        return f"証拠不足（最低条件を割った）（{_code(verdict.value)}）"
    return f"{_FOLD_VERDICT_NAMES[verdict]}（{_code(verdict.value)}）"


def _status_text(status: TrialStatus) -> str:
    return f"{_STATUS_NAMES[status]}（{_code(status.value)}）"


def _condition_label(condition: MetricCondition) -> str:
    return (
        f"{condition.metric.value} {condition.comparator.value}"
        f" {_decimal_text(condition.metric, condition.threshold)}"
    )


def _result_text(result: ConditionResult) -> str:
    if result.outcome is ConditionOutcome.UNCOMPUTABLE:
        reason = None if result.unavailable_reason is None else result.unavailable_reason.value
        return f"値なし（{reason}）→ {_code(result.outcome.value)}"
    observed = "" if result.observed is None else _decimal_text(result.metric, result.observed)
    return f"{observed} → {_code(result.outcome.value)}"


def _scrub(text: str, roots: Sequence[Path]) -> str:
    """読み出しの失敗の理由から絶対パスを除く（決定論。D07 §22.1）。"""
    for root in roots:
        for form in {str(Path(root).resolve()), str(Path(root).absolute()), str(root)}:
            if form and form != ".":
                text = text.replace(form, "<成果物の根>")
    return text


def policy_standing(
    manifest_policy: PolicyRef, purpose: StandardPurpose, registry: Sequence[RegistryEntry]
) -> str:
    """「現行／旧版／機構確認用」（D09 §10.11 の2。レポートを作った時点の登録簿での表示）。"""
    if purpose is StandardPurpose.MECHANISM_CHECK:
        return "機構確認用"
    current = current_standard_version(registry, manifest_policy.policy_id)
    return "現行" if current == manifest_policy.version else "旧版"


# --- 比較の前提の表示（レポートの順7 と台帳の一覧で共有。D09 §11.5・§11.4）-----------------


def _basis_value_text(value: object) -> str:
    if isinstance(value, PolicyRef):
        return f"{value.policy_kind}:{value.policy_id} v{value.version}（{value.digest.hex}）"
    if isinstance(value, SnapshotRef):
        return str(value.snapshot_id)
    if isinstance(value, StrategyRef):
        return f"{value.strategy_id} v{value.version}（{value.digest.hex}）"
    if isinstance(value, SymbolSpecRef):
        return f"{value.symbol} v{value.version}（{value.digest.hex}）"
    if isinstance(value, AccountSpec):
        amount = format(value.initial_balance.amount, "f")
        return f"{value.account_id}・{value.currency}・{amount} {value.initial_balance.currency}"
    if isinstance(value, (CodeDigest, LockDigest, EnvDigest)):
        return value.digest.hex
    if isinstance(value, tuple) and all(isinstance(item, TimeframeRef) for item in value):
        return "、".join(f"{item.id} v{item.version}" for item in value)
    return str(value)


def basis_items(basis: ComparisonBasis) -> tuple[tuple[str, str], ...]:
    """比較の前提の全項目を、固定の名前（`ComparisonBasis` のフィールド名）と順で並べる。"""
    return tuple(
        (field.name, _basis_value_text(getattr(basis, field.name)))
        for field in dataclasses.fields(ComparisonBasis)
    )


# --- 読み出し ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Holdings:
    """検証区間の単位の保有区間（完了取引と未決済の建玉）。"""

    spans: tuple[tuple[UtcTime, UtcTime | None], ...]
    open_positions: int


def _read_holdings(root: Path, record: TrialRunRecord) -> _Holdings | str:
    """評価の取引表（入場・決済時刻）と run の建玉の表（未決済の建玉）を読む。

    D09 §11.5 の注記（最長の無取引期間と未決済の建玉の併記）。
    """
    evaluation = evaluation_directory(
        root, record.run_id, RunEvaluationId(record.run_evaluation_id)
    )
    trades_path = evaluation / f"{EvaluationTable.TRADES.value}.parquet"
    positions_path = run_directory(root, record.run_id) / "POSITIONS.parquet"
    relative = f"runs/{record.run_id.hex}"
    spans: list[tuple[UtcTime, UtcTime | None]] = []
    try:
        for path, label in ((trades_path, "取引表"), (positions_path, "建玉の表")):
            if path.is_symlink() or not path.is_file():
                return f"{label}（{relative} の下）が無い"
        trades = pl.read_parquet(trades_path, columns=["entry_at_time", "exit_at_time"])
        for start, end in trades.iter_rows():
            spans.append((UtcTime.parse(str(start)), UtcTime.parse(str(end))))
        positions = pl.read_parquet(positions_path, columns=["opened_at_time", "status"])
        open_count = 0
        for opened, status in positions.iter_rows():
            if status == "OPEN":
                open_count += 1
                spans.append((UtcTime.parse(str(opened)), None))
    except (OSError, ValueError, KernelValueError, pl.exceptions.PolarsError) as exc:
        return f"取引表か建玉の表（{relative} の下）を読めない: {type(exc).__name__}"
    return _Holdings(spans=tuple(spans), open_positions=open_count)


@dataclass(frozen=True, slots=True)
class _LedgerView:
    """順7・順8 の材料（D09 §10.12.3 の R1〜R7 のうち例外にしない状況）。"""

    case: str
    text: str
    binding: TrialLedgerBinding | None
    contents: TrialLedgerContents | None
    started: TrialLedgerLine | None


def _ledger_view(
    directory: Path,
    manifest: ExperimentManifest,
    outcome: ExperimentOutcome | None,
    has_starts: bool,
    ledger_path: Path,
) -> _LedgerView:
    """束縛の記録と台帳を照合する（D09 §10.12.3 の4。上から順に最初に当たったもの）。

    R2・R4（同じ世代の記録が食い違う）は `SearchReportReadError`。
    """
    if outcome is not None and outcome.status is ExperimentStatus.REJECTED_BY_POLICY:
        return _LedgerView(
            "R1", "事前検査で止めたので台帳に書いていない（照合しない）。", None, None, None
        )
    try:
        binding = read_ledger_binding(directory)
    except KernelValueError as exc:
        raise SearchReportReadError(
            f"実行番号の束縛の記録が読めない（同じ世代の記録が食い違う。D09 §10.12.3 の R4）: {exc}"
        ) from exc
    if binding is None:
        if has_starts:
            raise SearchReportReadError(
                "束縛の記録 search/ledger_execution.json が無いのに開始記録がある（世代が混ざった"
                "構造の誤り。D09 §10.12.3 の R2）"
            )
        return _LedgerView(
            "R3",
            "この実行と台帳の行を対応付けられない（実行番号の記録が無い）。"
            "数え方 (a)(b) は出さない。",
            None,
            None,
            None,
        )
    if binding.experiment_id != manifest.experiment_id:
        raise SearchReportReadError(
            f"束縛の記録の experiment_id {binding.experiment_id} が記録票の"
            f" {manifest.experiment_id} と違う（D09 §10.12.3 の R4）"
        )
    search = None if outcome is None else outcome.search
    if search is not None and search.ledger_execution != binding.execution:
        raise SearchReportReadError(
            f"束縛の記録の実行番号 {binding.execution} が結末記録の search.ledger_execution"
            f" {search.ledger_execution} と違う（D09 §10.12.3 の R4）"
        )
    contents = read_trial_ledger_file(ledger_path)
    if isinstance(contents, TrialLedgerReadFailure):
        line = "なし" if contents.line_number is None else str(contents.line_number)
        return _LedgerView(
            "R5",
            f"台帳が読めない（種類 {_code(contents.kind.value)}・行 {line}・{contents.detail}）。"
            "数え方 (a)(b) は出さない。",
            binding,
            None,
            None,
        )
    started = started_line_of(contents.lines, binding.experiment_id, binding.execution)
    if started is None:
        return _LedgerView(
            "R6",
            "台帳にこの実行の開始の行が無い。数え方 (a)(b) は出さない。",
            binding,
            contents,
            None,
        )
    if started.digest != binding.started_line_digest:
        return _LedgerView(
            "R6",
            "台帳のこの実行の開始の行が成果物の記録と違う。数え方 (a)(b) は出さない。",
            binding,
            contents,
            None,
        )
    return _LedgerView("R7", "", binding, contents, started)


# --- 本文の材料 -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Inputs:
    manifest: ExperimentManifest
    standard: EvaluationStandard
    folds: tuple[Fold, ...]
    plan: SearchPlan
    trials: Mapping[int, TrialPlan]
    outcome: ExperimentOutcome | None
    starts: frozenset[TrialUnitKey]
    runs: Mapping[TrialUnitKey, TrialRunRecord]
    selections: Mapping[int, FoldSelection]
    #: 単位ごとの指標の行（読めなければ理由）。完了した実行は集約表、途中で止まった実行は評価の
    #: `METRICS` 表から読む。
    metrics: Mapping[TrialUnitKey, tuple[MetricRecord, ...] | str]
    holdings: Mapping[int, _Holdings | str]
    ledger: _LedgerView
    standing: str
    roots: tuple[Path, ...]
    artifacts_root: Path

    @property
    def purpose(self) -> StandardPurpose:
        return self.standard.purpose

    @property
    def search(self) -> SearchOutcome | None:
        return None if self.outcome is None else self.outcome.search

    @property
    def stopped(self) -> bool:
        """結末記録が無い＝途中で止まった実行（D07 §19.4）。"""
        return self.outcome is None

    @property
    def rejected(self) -> bool:
        return (
            self.outcome is not None and self.outcome.status is ExperimentStatus.REJECTED_BY_POLICY
        )


def _unit(fold: int, phase: TrialPhase, trial: int) -> TrialUnitKey:
    return TrialUnitKey(fold_index=fold, phase=phase, trial_index=trial)


def _metric(inputs: _Inputs, unit: TrialUnitKey, metric: MetricId) -> MetricValue | str:
    """単位の指標の値。行が無ければ理由を返す（空欄にしない）。"""
    rows = inputs.metrics.get(unit)
    if rows is None:
        record = inputs.runs.get(unit)
        if record is None:
            return "値なし（試行記録が無い）"
        return f"値なし（評価の状態 {record.evaluation_status.value} で指標の行が無い）"
    if isinstance(rows, str):
        return f"読めない（{rows}）"
    for item in rows:
        if item.metric_id is metric:
            return item.value
    record = inputs.runs.get(unit)
    status = "不明" if record is None else record.evaluation_status.value
    return f"値なし（評価の状態 {status} で指標の行が無い）"


def _metric_text(inputs: _Inputs, unit: TrialUnitKey, metric: MetricId) -> str:
    value = _metric(inputs, unit, metric)
    return value if isinstance(value, str) else _value_text(value)


def _units_of(inputs: _Inputs) -> tuple[TrialUnitKey, ...]:
    """数える単位（D09 §10.4: 選定区間は全試行、検証区間は選んだ試行の分だけ）。"""
    units: list[TrialUnitKey] = []
    for fold in inputs.folds:
        units.extend(_unit(fold.fold_index, TrialPhase.TRAIN, index) for index in inputs.trials)
        selection = inputs.selections.get(fold.fold_index)
        if selection is not None and selection.selected_trial_index is not None:
            units.append(
                _unit(fold.fold_index, TrialPhase.VALIDATION, selection.selected_trial_index)
            )
    return tuple(units)


def _derived_statuses(inputs: _Inputs) -> tuple[tuple[TrialUnitKey, TrialStatus], ...]:
    units = _units_of(inputs)
    extra = (set(inputs.starts) | set(inputs.runs)) - set(units)
    if extra:
        raise SearchReportReadError(
            f"単位として数えない記録がある: {sorted(unit_name(unit) for unit in extra)}"
            "（D09 §10.4。選定記録の無い fold の検証区間の記録など）"
        )
    return tuple(
        (
            unit,
            derive_trial_status(
                compile_rejected=not inputs.trials[unit.trial_index].compiled,
                started=unit in inputs.starts,
                recorded=unit in inputs.runs,
            ),
        )
        for unit in units
    )


def _valid(record: TrialRunRecord | None) -> bool:
    """検証結果のある単位か（D09 §7.3 の用語）。"""
    return (
        record is not None
        and record.run_status is RunStatus.COMPLETED
        and record.evaluation_status is EvaluationStatus.COMPLETED
        and record.post_run_checks_passed
    )


def _invalid_cause(record: TrialRunRecord | None) -> str:
    if record is None:
        return "検証区間の試行記録が無い"
    if record.run_status is not RunStatus.COMPLETED:
        if record.run_status is RunStatus.FAILED_CAPABILITY:
            return (
                f"run が正常完走していない（{_code(record.run_status.value)}。"
                "実行前の能力検査で止まった）"
            )
        return f"run が正常完走していない（{_code(record.run_status.value)}）"
    if record.evaluation_status is not EvaluationStatus.COMPLETED:
        return f"評価の状態が COMPLETED でない（{_code(record.evaluation_status.value)}）"
    if not record.post_run_checks_passed:
        return "事後検査（P4・P5）が合格でない"
    return ""


def _status_counts(selection: FoldSelection) -> str:
    counts = Counter(entry[2] for entry in selection.inputs)
    return "、".join(
        f"{status.value} {counts[status]} 件" for status in CandidateStatus if counts[status]
    )


def _selected_unit(inputs: _Inputs, fold: int) -> TrialUnitKey | None:
    selection = inputs.selections.get(fold)
    if selection is None or selection.selected_trial_index is None:
        return None
    return _unit(fold, TrialPhase.VALIDATION, selection.selected_trial_index)


def _fold_verdicts(inputs: _Inputs) -> Mapping[int, FoldVerdict]:
    search = inputs.search
    return {} if search is None else dict(search.fold_verdicts)


def _few_trades_breach(inputs: _Inputs) -> frozenset[int]:
    search = inputs.search
    if search is None:
        return frozenset()
    return frozenset(
        item.fold_index
        for item in search.shortfalls
        if item.kind is SufficiencyShortfallKind.FLOOR_NOT_MET_TRADES_BELOW
        and item.fold_index is not None
    )


def _floors_of(inputs: _Inputs, fold: int) -> tuple[ConditionResult, ...]:
    search = inputs.search
    if search is None:
        return ()
    return tuple(
        item
        for item in search.condition_results
        if item.scope is ConditionScope.FOLD_FLOOR and item.fold_index == fold
    )


# --- 順1 判定 ------------------------------------------------------------------------


def _final_validation_line(inputs: _Inputs) -> str:
    """最終検証に進める条件（D09 §9.3 の原則2）を満たすかの1行。"""
    tail = "（レポートを作った時点の登録簿での表示であり、最終検証の申請の判断ではない）"
    label = "最終検証に進める条件（実験の状態 COMPLETED・判定 MEETS_STANDARD・現行の版。D09 §9.3）"
    outcome = inputs.outcome
    if outcome is None:
        return f"{label}: 満たさない（結末記録が無い＝途中で止まった）{tail}"
    search = outcome.search
    reasons: list[str] = []
    if outcome.status is not ExperimentStatus.COMPLETED:
        reasons.append(f"実験の状態が {outcome.status.value}")
    if search is None or search.verdict is not SearchVerdict.MEETS_STANDARD:
        reasons.append("判定が無い" if search is None else f"判定が {search.verdict.value}")
    if inputs.standing != "現行":
        reasons.append(f"研究ポリシーの版が{inputs.standing}")
    if not reasons:
        return f"{label}: 満たす{tail}"
    return f"{label}: 満たさない（{'・'.join(reasons)}）{tail}"


def _verdict_lines(inputs: _Inputs) -> list[str]:
    manifest = inputs.manifest
    policy = manifest.research_policy_ref
    lines = [_PURPOSE_LINES[inputs.purpose], ""]
    outcome = inputs.outcome
    if inputs.rejected and outcome is not None:
        lines.append("**探索は行っていない（事前検査で止めた）**。合格でない事前検査:")
        lines.append("")
        lines.extend(
            _table(
                ["検査", "期待値", "観測値"],
                [
                    [item.check.value, item.expected, item.observed]
                    for item in manifest.pre_run_checks
                    if item.outcome is not CheckOutcome.PASSED
                ],
            )
        )
        lines.append("")
    if outcome is not None and outcome.status is ExperimentStatus.FAILED_POST_RUN_CHECK:
        lines.append("**記録の検査（事後検査）に合格でない単位がある**:")
        lines.append("")
        rows = [
            [unit_name(unit), item.check.value, item.outcome.value, item.expected, item.observed]
            for unit, record in sorted(inputs.runs.items(), key=lambda pair: pair[0].order)
            for item in record.outcome_checks
            if item.outcome is not CheckOutcome.PASSED
        ]
        lines.extend(_table(["単位", "検査", "結果", "期待値", "観測値"], rows))
        lines.append("")
    search = inputs.search
    if inputs.stopped:
        lines.append(f"- 判定: {_STOPPED}")
    elif search is None:
        lines.append("- 判定: なし（探索を行っていないので判定は無い。「満たさない」ではない）")
    else:
        lines.append(f"- 判定: {_verdict_text(search.verdict, search.purpose)}")
    lines.append(
        f"- 研究ポリシー: {_code(policy.policy_id)} 版 {policy.version}（{inputs.standing}）"
    )
    lines.append(f"- {_FIXED_NOTES[inputs.purpose]}。")
    lines.append(f"- {_final_validation_line(inputs)}")
    breached = sorted(_few_trades_breach(inputs))
    if search is not None and breached:
        lines.append("")
        lines.append("**最低条件を割った fold がある（取引が少ないため判定は証拠不足）**:")
        lines.append("")
        rows = []
        for item in search.shortfalls:
            if item.kind is not SufficiencyShortfallKind.FLOOR_NOT_MET_TRADES_BELOW:
                continue
            for result in _floors_of(inputs, item.fold_index or 0):
                if result.outcome is not ConditionOutcome.NOT_MET:
                    continue
                observed = (
                    "" if result.observed is None else _decimal_text(result.metric, result.observed)
                )
                rows.append(
                    [
                        item.fold_index,
                        result.metric.value,
                        observed,
                        f"{result.comparator.value}"
                        f" {_decimal_text(result.metric, result.threshold)}",
                        item.observed,
                        item.required,
                    ]
                )
        lines.extend(
            _table(["fold", "指標", "観測値", "閾値", "検証区間の取引件数", "取引件数の要件"], rows)
        )
    return lines


# --- 順2 判定の理由 -------------------------------------------------------------------


def _capability_lines(inputs: _Inputs, record: TrialRunRecord) -> list[str]:
    """能力検査で止まった run の、run 区間と重なった欠落の区間（D09 §7.11 の表示）。"""
    read = FileSystemResultRepository(root=inputs.artifacts_root).read_manifest(record.run_id)
    label = f"単位 {unit_name(record.unit)}（run {_code(record.run_id.hex)}）"
    if isinstance(read, ManifestReadFailure):
        return [f"- {label}: run manifest を読めない（{_scrub(read.detail, inputs.roots)}）"]
    interval = read.config.run_interval
    gaps = [
        result
        for result in read.capability_report.integrity.results
        if result.kind is CheckKind.MISSING_EXPECTED_BAR and result.interval.overlaps(interval)
    ]
    lines = [
        f"- {label}: 実行前の能力検査で止まった。run 区間 {interval} と重なった欠落の区間"
        f" {len(gaps)} 件:"
    ]
    lines.extend(f"  - {result.series}: {result.interval}" for result in gaps)
    return lines


def _reason_lines(inputs: _Inputs) -> list[str]:
    if inputs.rejected:
        return [_NOT_SEARCHED]
    search = inputs.search
    if search is None:
        return [_STOPPED + "。"]
    lines = ["### 満たさなかった条件", ""]
    few = _few_trades_breach(inputs)
    failed = [
        [
            item.scope.value,
            "全体" if item.fold_index is None else item.fold_index,
            item.metric.value,
            "" if item.observed is None else _decimal_text(item.metric, item.observed),
            f"{item.comparator.value} {_decimal_text(item.metric, item.threshold)}",
            "取引が少ないため証拠不足として扱った" if item.fold_index in few else "なし",
        ]
        for item in search.condition_results
        if item.outcome is ConditionOutcome.NOT_MET
    ]
    if failed:
        lines.extend(_table(["範囲", "fold", "指標", "値", "閾値", "注記"], failed))
    else:
        lines.append("なし。")
    lines.extend(["", "### 証拠不足の理由", ""])
    if search.shortfalls:
        lines.extend(
            _table(
                ["種類", "fold", "試行", "指標", "値なしの理由", "要件", "観測値"],
                [
                    [
                        item.kind.value,
                        "全体" if item.fold_index is None else item.fold_index,
                        "なし" if item.trial_index is None else item.trial_index,
                        "なし" if item.metric is None else item.metric.value,
                        "なし" if item.reason is None else item.reason.value,
                        "なし" if item.required is None else item.required,
                        "なし" if item.observed is None else item.observed,
                    ]
                    for item in search.shortfalls
                ],
            )
        )
    else:
        lines.append("なし。")
    lines.extend(["", "### 判定できない fold とその原因", ""])
    verdicts = _fold_verdicts(inputs)
    incomplete = [
        fold for fold in inputs.folds if verdicts.get(fold.fold_index) is FoldVerdict.INCOMPLETE
    ]
    if not incomplete:
        lines.append("なし。")
    for fold in incomplete:
        index = fold.fold_index
        selection = inputs.selections[index]
        if selection.selected_trial_index is None:
            statuses = {entry[2] for entry in selection.inputs}
            if statuses & {
                CandidateStatus.EXCLUDED_NOT_COMPLETED,
                CandidateStatus.EXCLUDED_POST_RUN_CHECK,
            }:
                cause = "候補なし: run・評価・事後検査の失敗で結果が欠けた試行がある"
            else:
                cause = "候補なしで全試行が失敗（コンパイル拒否）"
            lines.append(f"- fold {index}: {cause}（{_status_counts(selection)}）")
            for entry in selection.inputs:
                if entry[2] is not CandidateStatus.EXCLUDED_NOT_COMPLETED:
                    continue
                record = inputs.runs.get(_unit(index, TrialPhase.TRAIN, entry[0]))
                if record is not None and record.run_status is RunStatus.FAILED_CAPABILITY:
                    lines.extend("  " + line for line in _capability_lines(inputs, record))
            continue
        unit = _unit(index, TrialPhase.VALIDATION, selection.selected_trial_index)
        record = inputs.runs.get(unit)
        cause = _invalid_cause(record)
        if not cause:
            missing = [
                metric.value
                for metric in _judged_metrics(inputs.standard)
                if isinstance(value := _metric(inputs, unit, metric), Unavailable)
                and value.reason is MetricUnavailableReason.INPUT_NOT_AVAILABLE
            ]
            cause = f"判定に使う指標が入力の無いことによる値なし（{'、'.join(missing)}）"
        lines.append(f"- fold {index}: {cause}")
        if record is not None and record.run_status is RunStatus.FAILED_CAPABILITY:
            lines.extend("  " + line for line in _capability_lines(inputs, record))
    if inputs.standard.validation.aggregate and not any(
        item.scope is ConditionScope.AGGREGATE for item in search.condition_results
    ):
        lines.extend(
            [
                "",
                "集約条件は判定していない（理由: 実験の判定が手順1〜3 で決まったため。D09 §7.3）。",
            ]
        )
    return lines


def _judged_metrics(standard: EvaluationStandard) -> tuple[MetricId, ...]:
    rule = standard.validation
    return tuple(
        dict.fromkeys(
            (*(item.metric for item in rule.fold_floors), *(item.metric for item in rule.aggregate))
        )
    )


# --- 順3 fold ごとの成績 -------------------------------------------------------------


def _exposure_text(inputs: _Inputs, unit: TrialUnitKey, fold: int) -> str:
    text = _metric_text(inputs, unit, MetricId.EXPOSURE_RATE)
    holdings = inputs.holdings.get(fold)
    if isinstance(holdings, _Holdings) and holdings.open_positions:
        text += (
            f"（区間の終わりに未決済の建玉 {holdings.open_positions} 件が残っている。"
            "この値に入らない）"
        )
    elif isinstance(holdings, str):
        text += f"（未決済の建玉の有無は読めない: {holdings}）"
    return text


def _idle_text(inputs: _Inputs, fold: Fold) -> str:
    holdings = inputs.holdings.get(fold.fold_index)
    if holdings is None:
        return "値なし（検証区間の試行記録が無い）"
    if isinstance(holdings, str):
        return f"読めない（{holdings}）"
    return _days(longest_idle_period(fold.validation, holdings.spans))


def _fold_rows(inputs: _Inputs) -> list[list[str]]:
    verdicts = _fold_verdicts(inputs)
    few = _few_trades_breach(inputs)
    floors = inputs.standard.validation.fold_floors
    rows: list[list[str]] = []
    for fold in inputs.folds:
        index = fold.fold_index
        selection = inputs.selections[index]
        verdict = verdicts.get(index)
        verdict_text = "なし" if verdict is None else _fold_verdict_text(verdict, index in few)
        head = [str(index), str(fold.train), str(fold.validation)]
        if selection.selected_trial_index is None:
            rows.append(
                [
                    *head,
                    f"候補なし（{_status_counts(selection)}）",
                    verdict_text,
                    *(["候補なし"] * (7 + len(floors))),
                ]
            )
            continue
        trial = selection.selected_trial_index
        unit = _unit(index, TrialPhase.VALIDATION, trial)
        results = {(item.metric, item.comparator): item for item in _floors_of(inputs, index)}
        floor_cells = []
        for condition in floors:
            result = results.get((condition.metric, condition.comparator))
            floor_cells.append(
                "判定していない（fold は判定できない）" if result is None else _result_text(result)
            )
        rows.append(
            [
                *head,
                f"試行 {trial}（{_assignment_text(inputs.trials[trial].assignment)}）",
                verdict_text,
                _metric_text(inputs, unit, MetricId.TRADE_COUNT),
                _days(fold.validation.duration),
                _idle_text(inputs, fold),
                _exposure_text(inputs, unit, index),
                _metric_text(inputs, unit, MetricId.NET_PROFIT),
                f"{_metric_text(inputs, unit, MetricId.MAX_DRAWDOWN_MTM)} / "
                f"{_metric_text(inputs, unit, MetricId.MAX_DRAWDOWN_MTM_RATE)}",
                f"{_metric_text(inputs, unit, MetricId.COST_CHARGED_TOTAL)}（参考: "
                f"{_metric_text(inputs, unit, MetricId.COST_PRICE_EMBEDDED_TOTAL)}）",
                *floor_cells,
            ]
        )
    return rows


def _fold_lines(inputs: _Inputs) -> list[str]:
    if inputs.rejected:
        return [_NOT_SEARCHED]
    if inputs.stopped:
        return _stopped_fold_lines(inputs)
    floors = inputs.standard.validation.fold_floors
    header = [
        "fold",
        "選定区間",
        "検証区間",
        "選んだ試行（割当）",
        "fold の判定",
        "検証区間の取引件数",
        "観測期間（日）",
        "取引しなかった期間（最長）",
        "完了取引の保有割合（EXPOSURE_RATE）",
        "損益（NET_PROFIT）",
        "最大ドローダウン（MAX_DRAWDOWN_MTM / MAX_DRAWDOWN_MTM_RATE）",
        "費用（COST_CHARGED_TOTAL。参考: COST_PRICE_EMBEDDED_TOTAL）",
        *(f"最低条件 {_condition_label(condition)}" for condition in floors),
    ]
    lines = _table(header, _fold_rows(inputs))
    lines.extend(
        [
            "",
            "取引しなかった期間は、検証区間の中の保有区間（完了取引の入場から決済まで、未決済の建玉は"
            "建てた時刻から区間の終わりまで）の和集合の補集合のうち最長の長さで、表示のための導出値で"
            "ある（指標ではなく、判定に使わない。D09 §11.5）。完了取引の保有割合は完了取引だけの値"
            "であり、1 から引いた値を「建玉を持っていなかった割合」として読まない。",
        ]
    )
    return lines


def _fold_state(inputs: _Inputs, fold: int) -> str:
    """途中で止まった実行の fold の状態（D09 §6.6 の導出）。"""
    selection = inputs.selections.get(fold)
    if selection is not None:
        if selection.selected_trial_index is None:
            return "候補なし"
        unit = _unit(fold, TrialPhase.VALIDATION, selection.selected_trial_index)
        return "検証済み" if unit in inputs.runs else "中断"
    touched = any(unit.fold_index == fold for unit in (*inputs.starts, *inputs.runs))
    return "中断" if touched else "未着手"


def _interim_cells(inputs: _Inputs, fold: int) -> tuple[str, str]:
    """検証済みの fold の最低条件の結果と検証区間の取引件数（D09 §10.4。Q36 決定）。"""
    unit = _selected_unit(inputs, fold)
    if unit is None:  # pragma: no cover - 検証済みの fold は選んだ試行を持つ
        return ("対象外", "対象外")
    record = inputs.runs[unit]
    trades = _metric_text(inputs, unit, MetricId.TRADE_COUNT)
    rows = inputs.metrics.get(unit)
    if record.evaluation_status is not EvaluationStatus.COMPLETED:
        return (
            f"比べられない（評価の状態 {_code(record.evaluation_status.value)} で指標が無い）",
            trades,
        )
    if not isinstance(rows, tuple):
        return (f"読めない（{rows}）", trades)
    try:
        evaluation = ValidationUnitEvaluation(
            fold_index=fold,
            trial_index=unit.trial_index,
            status=TrialStatus.COMPLETED,
            run_status=record.run_status,
            run_evaluation_id=record.run_evaluation_id,
            evaluation_status=record.evaluation_status,
            post_run_checks_passed=record.post_run_checks_passed,
            metrics=rows,
        )
        results = interim_floor_results(fold, inputs.standard.validation, evaluation)
    except KernelValueError as exc:
        return (f"読めない（{exc}）", trades)
    parts = []
    for condition, result in results:
        if isinstance(result, ConditionResult):
            parts.append(f"{_condition_label(condition)}: {_result_text(result)}")
        else:
            parts.append(f"{_condition_label(condition)}: 値なし（{result.value}）")
    return ("；".join(parts), trades)


def _stopped_fold_lines(inputs: _Inputs) -> list[str]:
    lines = [
        "結末記録が無い＝途中で止まった実行である（D07 §19.4）。記録票・開始記録・試行記録・選定"
        "記録から各 fold と各単位の状態を導いて表示する（D09 §10.4・§6.6）。"
        "fold の判定は出さない。",
        "",
        "### 各 fold の状態",
        "",
    ]
    rows = []
    for fold in inputs.folds:
        index = fold.fold_index
        state = _fold_state(inputs, index)
        selection = inputs.selections.get(index)
        chosen = "対象外"
        if selection is not None:
            if selection.selected_trial_index is None:
                chosen = f"候補なし（{_status_counts(selection)}）"
            else:
                trial = selection.selected_trial_index
                chosen = f"試行 {trial}（{_assignment_text(inputs.trials[trial].assignment)}）"
        if state == "検証済み":
            floors, trades = _interim_cells(inputs, index)
            note = "証拠の要件は未確定（頻度区分が決まっていない）"
        else:
            floors, trades, note = "対象外", "対象外", "なし"
        rows.append([index, fold.train, fold.validation, state, chosen, floors, trades, note])
    lines.extend(
        _table(
            [
                "fold",
                "選定区間",
                "検証区間",
                "状態",
                "選んだ試行（割当）",
                "最低条件の結果",
                "検証区間の取引件数（TRADE_COUNT の写し）",
                "注記",
            ],
            rows,
        )
    )
    lines.extend(["", "### 各単位の状態", ""])
    unit_rows = []
    for unit, status in _derived_statuses(inputs):
        record = inputs.runs.get(unit)
        if status is TrialStatus.ABORTED:
            evaluation = _code(EvaluationStatus.ABORTED.value)
        elif record is not None:
            evaluation = _code(record.evaluation_status.value)
        else:
            evaluation = "なし"
        unit_rows.append(
            [
                unit_name(unit),
                _assignment_text(inputs.trials[unit.trial_index].assignment),
                _status_text(status),
                evaluation,
            ]
        )
    lines.extend(_table(["単位", "割当", "状態", "評価の状態"], unit_rows))
    return lines


# --- 順4〜6 ---------------------------------------------------------------------------


def _availability_lines(inputs: _Inputs) -> list[str]:
    if inputs.rejected:
        return [_NOT_SEARCHED]
    metrics = tuple(dict.fromkeys((*_judged_metrics(inputs.standard), *_TABLE_METRICS)))
    lines: list[str] = []
    rows = []
    for fold in inputs.folds:
        index = fold.fold_index
        unit = _selected_unit(inputs, index)
        if unit is None:
            lines.append(f"- fold {index}: 候補なし（検証区間の単位が無い）")
            continue
        if inputs.stopped and _fold_state(inputs, index) != "検証済み":
            lines.append(f"- fold {index}: 検証区間の単位が終わっていない（{_STOPPED}）")
            continue
        for metric in metrics:
            value = _metric(inputs, unit, metric)
            if isinstance(value, str):
                rows.append([index, metric.value, value])
            elif isinstance(value, Unavailable):
                rows.append([index, metric.value, value.reason.value])
    if rows:
        lines.extend(["", *_table(["fold", "指標", "値なしの理由"], rows)])
    else:
        lines.append("値なしの指標は無い（判定に使う指標と順3 の表の指標）。")
    return lines


def _aggregate_lines(inputs: _Inputs) -> list[str]:
    if inputs.rejected:
        return [_NOT_SEARCHED]
    search = inputs.search
    if search is None:
        return [_STOPPED + "。"]
    conditions = inputs.standard.validation.aggregate
    if not conditions:
        return ["集約条件は無い（評価基準の aggregate が空。D09 §7.3 の手順4 は無条件）。"]
    results = [item for item in search.condition_results if item.scope is ConditionScope.AGGREGATE]
    rows = []
    for index, condition in enumerate(conditions):
        threshold = (
            f"{condition.comparator.value} {_decimal_text(condition.metric, condition.threshold)}"
        )
        if index < len(results):
            result = results[index]
            observed = (
                "" if result.observed is None else _decimal_text(result.metric, result.observed)
            )
            rows.append(
                [
                    condition.metric.value,
                    condition.statistic.value,
                    observed,
                    threshold,
                    result.outcome.value,
                ]
            )
        else:
            rows.append(
                [
                    condition.metric.value,
                    condition.statistic.value,
                    "判定していない",
                    threshold,
                    "判定していない（実験の判定が手順1〜3 で決まったため）",
                ]
            )
    return _table(["指標", "統計", "値", "閾値", "結果"], rows)


def _evidence_lines(inputs: _Inputs) -> list[str]:
    if inputs.rejected:
        return [_NOT_SEARCHED]
    search = inputs.search
    if search is None:
        return [f"{_STOPPED}（頻度区分が決まっていない）。"]
    frequency = search.frequency
    if frequency is None:
        return ["頻度区分は無い（選んだ試行のある fold が無い。D09 §7.8）。"]
    requirement = frequency_class_of(frequency, inputs.standard.sufficiency)
    lines = [
        f"- 頻度区分: {_code(frequency.class_name)}",
        f"- 根拠: 選んだ試行の選定区間の取引件数の合計 {frequency.train_trade_count}、選定区間の"
        f"長さの合計 {_days(timedelta(seconds=frequency.train_seconds))}、365 日あたりの頻度"
        f" {_fixed(frequency.trades_per_365d)}",
        f"- 区分の要件: fold ごとの検証区間の取引件数 {requirement.min_validation_trades_per_fold}"
        f" 以上、合計 {requirement.min_validation_trades_total} 以上",
        "",
    ]
    rows = []
    total = 0
    for fold in inputs.folds:
        if not _floors_of(inputs, fold.fold_index):
            continue
        unit = _selected_unit(inputs, fold.fold_index)
        if unit is None:  # pragma: no cover - 最低条件の結果がある fold は選んだ試行を持つ
            continue
        value = _metric(inputs, unit, MetricId.TRADE_COUNT)
        if isinstance(value, CountValue):
            total += value.count
        rows.append([fold.fold_index, value if isinstance(value, str) else _value_text(value)])
    if rows:
        lines.extend(_table(["fold（検証結果のある fold）", "検証区間の取引件数"], rows))
        lines.append("")
        lines.append(f"- 合計: {total}")
    else:
        lines.append("検証結果のある fold が無い。")
    trade_kinds = {
        SufficiencyShortfallKind.FOLD_TRADES_BELOW,
        SufficiencyShortfallKind.FLOOR_NOT_MET_TRADES_BELOW,
        SufficiencyShortfallKind.TOTAL_TRADES_BELOW,
    }
    shortfalls = [item for item in search.shortfalls if item.kind in trade_kinds]
    lines.append(
        "- 不足の理由: "
        + (
            "、".join(
                f"{item.kind.value}（fold {'全体' if item.fold_index is None else item.fold_index}"
                f"・要件 {item.required}・観測 {item.observed}）"
                for item in shortfalls
            )
            or "なし"
        )
    )
    return lines


# --- 順7・順8 ------------------------------------------------------------------------


def _basis_lines(inputs: _Inputs) -> list[str]:
    if inputs.rejected:
        return [_NOT_SEARCHED]
    view = inputs.ledger
    if view.started is None:
        return [f"台帳のこの実行の開始の行と照合できないので出せない（理由: {view.text}）"]
    lines = [
        "台帳のこの実行の開始の行の比較の前提（D09 §12 の伝播。項目名と順序は固定）:",
        "",
    ]
    lines.extend(_table(["項目", "値"], basis_items(view.started.entry.basis)))
    return lines


def _ledger_lines(inputs: _Inputs) -> list[str]:
    view = inputs.ledger
    lines: list[str] = []
    plan_digest = digest(inputs.plan).hex
    if view.case == "R1":
        return [view.text]
    binding = view.binding
    lines.append(
        "- 実行番号: " + ("なし（束縛の記録が無い）" if binding is None else str(binding.execution))
    )
    lines.append(f"- 探索計画のダイジェスト（search_plan_digest）: {_code(plan_digest)}")
    if view.case != "R7" or view.started is None or view.contents is None:
        lines.append(f"- 照合: {view.text}")
        return lines
    contents = view.contents
    entry = view.started.entry
    closing = finished_line_of(contents.lines, entry.experiment_id, entry.execution)
    if closing is None or closing.entry.verdict is None:
        lines.append("- この番号の結末の行: なし（結末の行なし）")
    else:
        lines.append(f"- この番号の結末の行: あり（判定 {_code(closing.entry.verdict.value)}）")
    counts = count_prior_executions(contents.lines, view.started)
    lines.append(
        f"- (a) この実行より前に同じ検証区間を見た実行: {counts.overlapping_executions} 件、"
        f"試行の累計 {counts.overlapping_trials}（用途によらず数える。D09 §10.10）"
    )
    lines.append(f"- (b) 同じ戦略（{_code(entry.strategy_id)}）の先行の実行:")
    if counts.same_strategy:
        lines.append("")
        lines.extend(
            _table(
                [
                    "実験",
                    "版",
                    "実行番号",
                    "研究ポリシーの版",
                    "用途",
                    "探索計画のダイジェスト",
                    "判定",
                ],
                [
                    [
                        item.experiment_name,
                        item.experiment_version,
                        item.execution,
                        f"{item.research_policy_ref.policy_id}"
                        f" 版 {item.research_policy_ref.version}",
                        item.purpose.value,
                        item.search_plan_digest.hex,
                        "結末の行なし" if item.verdict is None else item.verdict.value,
                    ]
                    for item in counts.same_strategy
                ],
            )
        )
    else:
        lines.append("  なし。")
    if contents.torn_tail:
        lines.append("")
        lines.append("末尾に書きかけの行がある（数えていない。次の追記で切り詰められる）。")
    lines.append("")
    lines.append("数えるのはこの実行の開始の行より前の完全な行だけである（D09 §10.10）。")
    return lines


# --- 順9 診断 ------------------------------------------------------------------------


def _spread_lines(inputs: _Inputs) -> list[str]:
    """fold 間のばらつき（D09 §8 の1）。"""
    selection_metric = inputs.standard.selection.metric
    metrics = tuple(dict.fromkeys((selection_metric, *_judged_metrics(inputs.standard))))
    valid: list[int] = []
    excluded: Counter[str] = Counter()
    for fold in inputs.folds:
        unit = _selected_unit(inputs, fold.fold_index)
        if unit is None:
            continue
        record = inputs.runs.get(unit)
        if _valid(record):
            valid.append(fold.fold_index)
            continue
        if record is None or record.run_status is not RunStatus.COMPLETED:
            excluded["run が正常完走していない"] += 1
        elif record.evaluation_status is not EvaluationStatus.COMPLETED:
            excluded["評価の状態が COMPLETED でない"] += 1
        else:
            excluded["事後検査が合格でない"] += 1
    lines = [
        f"値の計算に使った fold（検証結果のある fold）: {', '.join(map(str, valid)) or 'なし'}。"
        "除いた fold: "
        + ("、".join(f"{cause} {count} 件" for cause, count in sorted(excluded.items())) or "なし")
        + "。",
        "",
    ]
    rows = []
    for metric in metrics:
        for phase, label in (
            (TrialPhase.TRAIN, "選定区間（選んだ試行）"),
            (TrialPhase.VALIDATION, "検証区間"),
        ):
            values: list[Decimal] = []
            missing: Counter[str] = Counter()
            for index in valid:
                selection = inputs.selections[index]
                trial = selection.selected_trial_index
                if trial is None:  # pragma: no cover - valid は選んだ試行のある fold だけ
                    continue
                value = _metric(inputs, _unit(index, phase, trial), metric)
                if isinstance(value, (RatioValue, AmountValue)):
                    values.append(
                        value.ratio if isinstance(value, RatioValue) else value.amount.amount
                    )
                elif isinstance(value, Unavailable):
                    missing[value.reason.value] += 1
                else:
                    missing[str(value)] += 1
            if values:
                low, mid, high = (
                    _decimal_text(metric, min(values)),
                    _decimal_text(metric, median_of(values)),
                    _decimal_text(metric, max(values)),
                )
            else:
                low = mid = high = "値なし"
            rows.append(
                [
                    metric.value,
                    label,
                    low,
                    mid,
                    high,
                    "、".join(f"{reason} {count} 件" for reason, count in sorted(missing.items()))
                    or "なし",
                ]
            )
    lines.extend(
        _table(["指標", "区間", "最小", "中央値", "最大", "値なしの fold（理由ごと）"], rows)
    )
    return lines


def _neighbour_lines(inputs: _Inputs) -> list[str]:
    """選んだ試行の近傍（D09 §8 の2。書いた順で直前・直後の値）。"""
    by_assignment = {digest(trial.assignment).hex: trial for trial in inputs.trials.values()}
    metric = inputs.standard.selection.metric
    rows = []
    for fold in inputs.folds:
        selection = inputs.selections[fold.fold_index]
        trial_index = selection.selected_trial_index
        if trial_index is None:
            continue
        status_of = {entry[0]: entry[2] for entry in selection.inputs}
        chosen = inputs.trials[trial_index].assignment
        current = {(item[0], item[1]): item[2] for item in chosen.values}
        for axis in inputs.plan.axes:
            values = axis.values
            position = next(
                index
                for index, value in enumerate(values)
                if digest(value) == digest(current[axis.key])
            )
            for offset, side in ((-1, "直前"), (1, "直後")):
                neighbour_position = position + offset
                if not 0 <= neighbour_position < len(values):
                    continue
                neighbour_value = values[neighbour_position]
                assignment = ParameterAssignment(
                    values=tuple(
                        (key[0], key[1], neighbour_value if key == axis.key else value)
                        for key, value in current.items()
                    )
                )
                trial = by_assignment.get(digest(assignment).hex)
                if trial is None:  # pragma: no cover - 格子の点は全部列挙されている
                    continue
                status = status_of.get(trial.trial_index)
                if not trial.compiled:
                    shown = _code(CandidateStatus.EXCLUDED_TRIAL_FAILED.value)
                else:
                    shown = _metric_text(
                        inputs, _unit(fold.fold_index, TrialPhase.TRAIN, trial.trial_index), metric
                    )
                rows.append(
                    [
                        fold.fold_index,
                        f"{axis.instance_id}.{axis.parameter}",
                        f"{side}: {json.dumps(neighbour_value.value, ensure_ascii=False)}",
                        trial.trial_index,
                        shown,
                        "なし" if status is None else status.value,
                    ]
                )
    if not rows:
        return ["近傍の試行は無い（選んだ試行のある fold が無い、または軸の値が1つだけ）。"]
    return _table(
        ["fold", "軸", "隣の値", "試行", f"選定区間の選定の指標（{metric.value}）", "候補の区分"],
        rows,
    )


def _selection_rows(inputs: _Inputs) -> list[str]:
    metric = inputs.standard.selection.metric
    rows = []
    for fold in inputs.folds:
        selection = inputs.selections.get(fold.fold_index)
        if selection is None:
            rows.append([fold.fold_index, "選定記録なし", "なし", "なし"])
            continue
        if selection.selected_trial_index is None or selection.selected_value is None:
            rows.append([fold.fold_index, "候補なし", "なし", _status_counts(selection)])
            continue
        rows.append(
            [
                fold.fold_index,
                selection.selected_trial_index,
                _decimal_text(metric, selection.selected_value),
                _status_counts(selection),
            ]
        )
    return _table(
        ["fold", "選んだ試行", f"選定の指標（{metric.value}）の値", "候補の区分の件数"], rows
    )


_INDEPENDENT_RUN_NOTE: Final = (
    "fold の独立な run という方式の注記: 各 fold の選定区間と検証区間は、それぞれ独立な run として"
    "実行した。run の中で積み上がる状態（取引機会・Trigger の記憶・評価要求）は各 run の開始時に"
    "空であり、採点区間の最初のしばらくは、区間の外から続く連続した run と違う判断をしうる"
    "（D09 §6.5・§6.4）。"
)


def _diagnosis_lines(inputs: _Inputs) -> list[str]:
    if inputs.rejected:
        return [_NOT_SEARCHED]
    lines = ["判定には使わない表示である（D09 §8・§7.10）。", ""]
    if inputs.stopped:
        lines.append(f"頑健性の表（fold 間のばらつき・近傍）: {_STOPPED}。頑健性の表は出さない。")
        counts = count_trial_statuses(status for _, status in _derived_statuses(inputs))
    else:
        search = inputs.search
        if search is None:  # pragma: no cover - 結末記録のある探索の実験は判定を持つ
            return [_NOT_SEARCHED]
        lines.extend(["### fold 間のばらつき", ""])
        lines.extend(_spread_lines(inputs))
        lines.extend(["", "### 選んだ試行の近傍", ""])
        lines.extend(_neighbour_lines(inputs))
        counts = search.trial_counts
    lines.extend(["", "### 試行の状態の件数（単位で数える）", ""])
    lines.extend(
        _table(["状態", "件数"], [[_status_text(status), count] for status, count in counts])
    )
    lines.extend(["", "### fold ごとの選定", ""])
    lines.extend(_selection_rows(inputs))
    lines.extend(["", _INDEPENDENT_RUN_NOTE])
    return lines


# --- 組み立て ------------------------------------------------------------------------


def _render(inputs: _Inputs) -> str:
    manifest = inputs.manifest
    bodies = (
        _verdict_lines(inputs),
        _reason_lines(inputs),
        _fold_lines(inputs),
        _availability_lines(inputs),
        _aggregate_lines(inputs),
        _evidence_lines(inputs),
        _basis_lines(inputs),
        _ledger_lines(inputs),
        _diagnosis_lines(inputs),
    )
    sections: list[list[str]] = [
        [
            f"# 実験レポート: {manifest.experiment_name} 版 {manifest.experiment_version}",
            "",
            "保存済みの成果物だけから作った表示である（記録票・結末記録・探索の記録・評価と run の"
            "成果物と、試行台帳・研究ポリシーの版の登録簿。D09 §11.5、D07 §22）。正本はそれらの"
            "成果物であり、このファイルは結果のダイジェストに入らない。比率は小数第6位まで表示する"
            "（保存値は丸めていない）。",
        ]
    ]
    for heading, body in zip(SEARCH_REPORT_HEADINGS, bodies, strict=True):
        sections.append([heading, "", *body])
    text = "\n\n".join("\n".join(section).rstrip("\n") for section in sections)
    return text + "\n"


def _metrics_of(
    directory: Path,
    root: Path,
    outcome: ExperimentOutcome | None,
    runs: Mapping[TrialUnitKey, TrialRunRecord],
    roots: tuple[Path, ...],
) -> dict[TrialUnitKey, tuple[MetricRecord, ...] | str]:
    """単位の指標の行（完了した実行は集約表、途中で止まった実行は評価の `METRICS` 表）。"""
    found: dict[TrialUnitKey, tuple[MetricRecord, ...] | str] = {}
    if outcome is not None:
        try:
            table = read_trial_metrics(directory)
        except KernelValueError as exc:
            reason = _scrub(str(exc), roots)
            return {
                unit: reason
                for unit, record in runs.items()
                if record.evaluation_status is EvaluationStatus.COMPLETED
            }
        found.update(table)
        return found
    repository = FileSystemResultRepository(root=root)
    for unit, record in runs.items():
        if record.evaluation_status is not EvaluationStatus.COMPLETED:
            continue
        read = repository.read_evaluation_metrics(
            record.run_id, RunEvaluationId(record.run_evaluation_id)
        )
        found[unit] = (
            _scrub(read.detail, roots) if isinstance(read, EvaluationReadFailure) else read
        )
    return found


def build_search_report(
    experiment_dir: Path,
    artifacts_root: Path,
    *,
    repo_root: Path,
    registry: Sequence[RegistryEntry],
) -> str:
    """探索の実験の版のディレクトリの保存済みの成果物からレポートの本文を作る（D09 §11.5）。

    `artifacts_root` は run と評価の成果物の根、`repo_root` は試行台帳を置くリポジトリの根、
    `registry` は合成が読んで記録票の版参照と照合した研究ポリシーの版の登録簿の要素。記録票・結末
    記録・探索の記録が読めない・食い違うときは `KernelValueError`（R2・R4 は
    `SearchReportReadError`）。
    """
    directory = Path(experiment_dir)
    root = Path(artifacts_root)
    roots = (root, Path(repo_root))
    manifest = read_experiment_manifest(directory)
    outcome = read_experiment_outcome(directory)
    if outcome is not None and outcome.experiment_id != manifest.experiment_id:
        raise KernelValueError(
            f"{EXPERIMENT_OUTCOME_FILE} belongs to the experiment {outcome.experiment_id}, not to"
            f" the manifest {manifest.experiment_id}; the report would mix two experiments"
        )
    standard = manifest.evaluation_standard
    split = manifest.split
    plan = manifest.search_plan
    if standard is None or not isinstance(split, SplitSpec) or not isinstance(plan, SearchPlan):
        raise KernelValueError("build_search_report requires a search experiment (D09 §10.2)")
    starts, runs = read_unit_records(directory)
    if outcome is not None and outcome.search is not None:
        selections = {item.fold_index: item for item in outcome.search.selections}
    else:
        selections = read_selections(directory)
    ledger = _ledger_view(
        directory, manifest, outcome, bool(starts), Path(repo_root) / TRIAL_LEDGER_PATH
    )
    holdings: dict[int, _Holdings | str] = {}
    for fold in split.folds:
        selection = selections.get(fold.fold_index)
        if selection is None or selection.selected_trial_index is None:
            continue
        record = runs.get(
            _unit(fold.fold_index, TrialPhase.VALIDATION, selection.selected_trial_index)
        )
        if record is not None and record.evaluation_status is EvaluationStatus.COMPLETED:
            read = _read_holdings(root, record)
            holdings[fold.fold_index] = read if isinstance(read, _Holdings) else _scrub(read, roots)
        elif record is not None:
            holdings[fold.fold_index] = (
                f"評価の状態 {record.evaluation_status.value} で取引表が無い"
            )
    inputs = _Inputs(
        manifest=manifest,
        standard=standard,
        folds=split.folds,
        plan=plan,
        trials={trial.trial_index: trial for trial in manifest.trials},
        outcome=outcome,
        starts=frozenset(starts),
        runs=runs,
        selections=selections,
        metrics=_metrics_of(directory, root, outcome, runs, roots),
        holdings=holdings,
        ledger=ledger,
        standing=policy_standing(manifest.research_policy_ref, standard.purpose, registry),
        roots=roots,
        artifacts_root=root,
    )
    if inputs.stopped:
        _derived_statuses(inputs)  # 単位として数えない記録があれば読込の誤り（表示の前に止める）
    return _render(inputs)


def write_search_report(
    experiment_dir: Path,
    artifacts_root: Path,
    *,
    repo_root: Path,
    registry: Sequence[RegistryEntry],
) -> ReportWrite:
    """探索の実験のレポートを書く（D07 §22.1 の書き込み規則。D09 §10.7 の終端の書き込みの (4)）。"""
    directory = Path(experiment_dir)
    text = build_search_report(directory, artifacts_root, repo_root=repo_root, registry=registry)
    return write_report_text(directory, text)


# --- 試行台帳の一覧（`experiment ledger`。D09 §11.4）--------------------------------------


def ledger_listing(
    contents: TrialLedgerContents,
    registry: Sequence[RegistryEntry],
    *,
    strategy: str | None = None,
) -> list[str]:
    """試行台帳を研究ポリシーの版ごとにまとめた一覧の行（D09 §11.4。標準出力に出す）。

    1行が1つの実行（開始の行と、同じ番号の結末の行）。まとまりは `(id, 版)` の昇順、まとまりの
    中は台帳の順。比較の前提がまとまりの最初の行と違うセルの先頭に `*` を付ける。累計は表示する
    だけで、実験を止めたり判定を変えたりしない（Q17 決定）。
    """
    lines = ["# 試行台帳の一覧", ""]
    groups: dict[tuple[str, int], list[TrialLedgerLine]] = {}
    for line in contents.lines:
        entry = line.entry
        if entry.event is not TrialLedgerEvent.STARTED:
            continue
        if strategy is not None and entry.strategy_id != strategy:
            continue
        policy = entry.basis.research_policy_ref
        groups.setdefault((policy.policy_id, policy.version), []).append(line)
    if not groups:
        lines.append(
            "台帳に行が無い。"
            if strategy is None
            else f"台帳に行が無い（戦略 {_code(strategy)} の実行が無い）。"
        )
    basis_names = [field.name for field in dataclasses.fields(ComparisonBasis)]
    header = [
        "実験",
        "版",
        "実行番号",
        "戦略",
        "判定",
        "頻度区分",
        "試行の数",
        "探索計画のダイジェスト",
        *basis_names,
    ]
    for (policy_id, version), members in sorted(groups.items()):
        first = members[0].entry
        if first.purpose is StandardPurpose.MECHANISM_CHECK:
            standing = "機構確認用"
        else:
            current = current_standard_version(registry, policy_id)
            standing = "現行" if current == version else "旧版"
        lines.extend([f"## 研究ポリシー {policy_id} 版 {version}（{standing}）", ""])
        reference = dict(basis_items(first.basis))
        rows = []
        for member in members:
            entry = member.entry
            closing = finished_line_of(contents.lines, entry.experiment_id, entry.execution)
            if closing is None or closing.entry.verdict is None:
                verdict, frequency = "結末の行なし", "結末の行なし"
            else:
                verdict = closing.entry.verdict.value
                frequency = closing.entry.frequency_class or "なし"
            cells = [
                ("*" if reference[name] != value else "") + value
                for name, value in basis_items(entry.basis)
            ]
            rows.append(
                [
                    entry.experiment_name,
                    entry.experiment_version,
                    entry.execution,
                    entry.strategy_id,
                    verdict,
                    frequency,
                    entry.trial_count,
                    entry.search_plan_digest.hex,
                    *cells,
                ]
            )
        lines.extend(_table(header, rows))
        lines.extend(["", "`*` はまとまりの最初の行と比較の前提が違う項目。", ""])
    if contents.torn_tail:
        lines.append("末尾に書きかけの行がある（数えていない。次の追記で切り詰められる）。")
    lines.append(
        "累計は表示するだけで、実験を止めたり判定を変えたりしない（D09 §10.10。Q17 決定）。"
    )
    return lines
