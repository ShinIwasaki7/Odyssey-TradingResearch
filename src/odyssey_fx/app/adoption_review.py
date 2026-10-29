"""採用審査の 1 回の実行の結線（`odyssey-fx experiment review`。D16 §2.2・§11）。

順序（D16 §2.2・§11.3 の表 2）:

1. 試行台帳・研究ポリシーの登録簿・審査台帳・審査の規則の登録簿を読む（読めなければ読込の誤り。
   終了コード 2）。どれも成績の数値を持たない。
2. 入口の検査（D16 §3.1 の順 1〜8）。不合格なら何も書かずに終える（終了コード 7。D15 の仮置き 7）。
   順 8 の規則のファイルは当てる版が決まってから読む（無い・読めない・形が違えば終了コード 2）。
3. **入口を通った後にだけ** `--experiment-dir` を開き、成果物の束縛を照合する（仮置き 13。違えば
   `ARTIFACT_BINDING_MISMATCH`、終了コード 2、何も書かない）。
4. 組ごとに: 固定した試行の run（研究履歴の検証区間すべて。D16 §4.2）→ 証拠の最低線 → 費用・約定
   モデルの適合（§7）→ 割り引いた指標（§5。表示だけ）。読めない・run が失敗した検査は
   「判定できない」にして続ける（§11.3 の S2）。
5. 台帳の累計（§6）・申告（§8）→ 結果の合わせ方（§9.1）。
6. 数値の記録（版管理の外。§10.4）→ 審査台帳の行（版管理。§10.2）。完全な行が残らなければ
   `ReviewNotRecorded`（終了コード 1。審査は行われていない扱い）。

**標準出力・標準エラーには数値を出さない**（結果の値・理由の識別子・合否・件数の累計・ハッシュだけ。
D16 §11.1、D15 の仮置き 6）。数値は数値の記録だけに書く。
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from odyssey_fx.app import composition
from odyssey_fx.app.config import ConfigError
from odyssey_fx.app.config.adoption_review import (
    adoption_review_registry_path,
    adoption_review_rules_path,
    declaration_path,
    load_adoption_review_registry,
    load_adoption_review_rules,
    load_declaration,
)
from odyssey_fx.app.config.research_policy import (
    load_research_policy,
    load_research_policy_registry,
    research_policy_path,
    research_policy_registry_path,
    verify_registered,
)
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.result import BacktestResult, RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId, RunId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.evaluation.adapters.fs_store import (
    RETREAT_MARKER_FILE,
    TRIAL_LEDGER_PATH,
    FileSystemResultRepository,
    read_experiment_manifest,
    read_ledger_binding,
    read_selections,
    read_trial_ledger_file,
    read_trial_metrics,
    read_unit_records,
    require_plain_experiment_path,
)
from odyssey_fx.evaluation.adapters.review_store import (
    ADOPTION_REVIEW_LEDGER_PATH,
    ReviewAppendRefused,
    ReviewLedgerReadFailure,
    append_review_ledger_file,
    read_review_ledger_file,
    write_review_record,
)
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, equity_points
from odyssey_fx.evaluation.application.ports import TrialLedgerReadFailure
from odyssey_fx.evaluation.domain.adoption_review import (
    ADOPTION_REVIEW_SCHEMA_VERSION,
    EVIDENCE_NOTE,
    AdoptionReviewEntry,
    AdoptionReviewLine,
    CostFitResult,
    CostFitRow,
    DeclarationState,
    DsrSummary,
    EntranceAccepted,
    EntranceRejected,
    EvidenceFloorRecord,
    FixedTrial,
    FloorOutcome,
    LedgerTally,
    ReviewReason,
    ReviewResult,
    ReviewRules,
    check_entrance,
    combine_result,
    cost_model_fit,
    evidence_floor,
    last_review_digest,
    ledger_tally,
    rules_file_mismatch,
)
from odyssey_fx.evaluation.domain.deflated_sharpe import (
    DeflatedSharpeInputs,
    DeflatedSharpeOutcome,
    daily_returns,
    deflated_sharpe,
)
from odyssey_fx.evaluation.domain.experiment import ExperimentManifest
from odyssey_fx.evaluation.domain.metrics import (
    CountValue,
    MetricId,
    MetricRecord,
    RatioValue,
    TradeRecord,
    trading_day_ends,
)
from odyssey_fx.evaluation.domain.search import TrialLedgerLine, TrialPhase, TrialUnitKey
from odyssey_fx.evaluation.domain.splits import SplitSpec
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant
from odyssey_fx.marketdata.domain.calendar import TradingCalendar

__all__ = [
    "ArtifactBindingMismatch",
    "ReviewNotRecorded",
    "ReviewOutcome",
    "review_experiment",
]


class ArtifactBindingMismatch(ConfigError):
    """`--experiment-dir` の成果物が入口で認めた実行に束縛されていない（終了コード 2）。"""


class ReviewNotRecorded(Exception):
    """審査台帳に完全な行を残せなかった（審査は行われていない扱い。終了コード 1。D16 §11.2）。"""


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """1 回の審査の成果（CLI が表示と終了コードに使う。数値を持たない）。

    入口の拒否では `result` が `DO_NOT_PROCEED`・`recorded` が偽・`entry` が `None`。
    """

    result: ReviewResult
    reasons: tuple[ReviewReason, ...]
    entry: AdoptionReviewEntry | None
    line_digest: ContentDigest | None
    record_directory: Path | None
    sync_failed: bool

    @property
    def recorded(self) -> bool:
        return self.entry is not None


# --- 読込（入口の前）-------------------------------------------------------------


def _experiment_id(text: str) -> ExperimentId:
    try:
        return ExperimentId(ContentDigest.sha256(text))
    except KernelValueError as exc:
        raise ConfigError(f"`--experiment-id` は 16 進 64 文字: {text!r}") from exc


def _trial_lines(repo_root: Path) -> tuple[TrialLedgerLine, ...]:
    contents = read_trial_ledger_file(repo_root / TRIAL_LEDGER_PATH)
    if isinstance(contents, TrialLedgerReadFailure):
        line = "" if contents.line_number is None else f"・行 {contents.line_number}"
        raise ConfigError(
            f"試行台帳が読めない（{contents.kind.value}{line}: {contents.detail}。D16 §3.1 の順 0）"
        )
    return contents.lines


def _review_lines(repo_root: Path) -> tuple[AdoptionReviewLine, ...]:
    contents = read_review_ledger_file(repo_root / ADOPTION_REVIEW_LEDGER_PATH)
    if isinstance(contents, ReviewLedgerReadFailure):
        line = "" if contents.line_number is None else f"・行 {contents.line_number}"
        raise ConfigError(
            f"審査台帳が読めない（{contents.kind.value}{line}: {contents.detail}。D16 §3.1 の順 0）"
        )
    return contents.lines


# --- 成果物の束縛（D16 §11.1）----------------------------------------------------------


def _bound_directory(
    experiment_dir: Path | None, accepted: EntranceAccepted
) -> tuple[Path, Path, ExperimentManifest]:
    """`--experiment-dir` の記録票と束縛の記録が、入口で認めた実行を指すことを確かめる。

    返すのは（版のディレクトリ、元の成果物の根、記録票）。違えば `ArtifactBindingMismatch`。
    """
    if experiment_dir is None:
        raise ArtifactBindingMismatch(
            "入口を通った実行の審査には `--experiment-dir`（その実行の実験の版のディレクトリ）が"
            "要る"
        )
    directory = experiment_dir.absolute()
    parents = directory.parents
    if (
        ".." in directory.parts
        or len(parents) < 4
        or parents[1].name != "experiments"
        or parents[2].name != "runs"
    ):
        raise ArtifactBindingMismatch(
            f"`--experiment-dir` は runs/experiments/<名前>/v<版> の形のディレクトリを指す:"
            f" {experiment_dir}"
        )
    root = parents[3]
    try:
        if not require_plain_experiment_path(root, directory):
            raise ArtifactBindingMismatch(f"{experiment_dir} が無い")
        manifest = read_experiment_manifest(directory)
        binding = read_ledger_binding(directory)
    except (KernelValueError, OSError) as exc:
        raise ArtifactBindingMismatch(f"記録票か束縛の記録を読めない: {exc}") from exc
    started = accepted.started
    marker = directory / RETREAT_MARKER_FILE
    if (
        not manifest.is_search
        or manifest.experiment_id != started.entry.experiment_id
        or (directory.parent.name, directory.name)
        != (manifest.experiment_name, f"v{manifest.experiment_version}")
        or marker.exists()
        or marker.is_symlink()
        or binding is None
        or binding.experiment_id != started.entry.experiment_id
        or binding.execution != started.entry.execution
        or binding.started_line_digest != started.digest
    ):
        raise ArtifactBindingMismatch(
            f"{experiment_dir} の記録票か束縛の記録が、入口で認めた実行"
            f" ({started.entry.experiment_id}, {started.entry.execution}) を指していない"
        )
    return directory, root, manifest


# --- 固定した試行の run（D16 §4.2）-------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FoldRun:
    fold_index: int
    run_id: RunId | None
    reused: bool
    trade_count: int | None
    trades: tuple[TradeRecord, ...] | None
    detail: str


def _metric(metrics: Sequence[MetricRecord], metric_id: MetricId) -> MetricRecord | None:
    return next((item for item in metrics if item.metric_id is metric_id), None)


def _trade_count(metrics: Sequence[MetricRecord]) -> int | None:
    record = _metric(metrics, MetricId.TRADE_COUNT)
    return (
        record.value.count if record is not None and isinstance(record.value, CountValue) else None
    )


def _fixed_run(
    *,
    wiring: composition.ReviewRunWiring,
    trial_index: int,
    unit: TrialUnitKey,
    roots: Sequence[Path],
    calendar: TradingCalendar,
) -> _FoldRun:
    """固定した試行の 1 つの検証区間の run（既にあれば再利用）と評価（D16 §4.2 の 1〜4）。

    `roots[0]` は `--out`（新しい run を書く基点）、`roots[1:]` は元の成果物の根（読むだけ）。同じ
    `RunId` の成果物は入力が一致する（`RunId` は完全入力の識別。D02 §7.1）ので、記録票の予測
    `ConfigDigest` との一致を確かめて再利用する。run・評価・事後検査のどれかが失敗すれば件数は
    `None`（判定できない）。
    """
    prepared = wiring.prepared
    trial = prepared.trials[trial_index]
    expected = trial.expected_run_id(unit)
    expected_digest = trial.plan.expected_config_digest(unit)
    version = prepared.manifest.metric_set_version
    evaluator = EvaluateRun(evaluation_code_digest=prepared.code_digest)
    result: BacktestResult | None = None
    repository: FileSystemResultRepository | None = None
    reused = False
    for root in roots:
        candidate = FileSystemResultRepository(root=root)
        if candidate.run_exists(expected):
            read = candidate.read_result(expected)
            if not isinstance(read, BacktestResult):
                return _FoldRun(unit.fold_index, expected, True, None, None, "unreadable result")
            result, repository, reused = read, candidate, True
            break
    if result is None:
        compiled = trial.compiled
        if compiled is None:
            return _FoldRun(unit.fold_index, None, False, None, None, "compile rejected")
        result = wiring.runner.run(trial.run_config(unit), compiled)
        repository = FileSystemResultRepository(root=roots[0])
    assert repository is not None  # noqa: S101  上で決めた
    read_manifest = repository.read_manifest(result.run_id)
    if (
        result.run_id != expected
        or not isinstance(read_manifest, RunManifest)
        or read_manifest.config_digest != expected_digest
    ):
        return _FoldRun(unit.fold_index, result.run_id, reused, None, None, "post-run check")
    if result.status is not RunStatus.COMPLETED:
        return _FoldRun(unit.fold_index, result.run_id, reused, None, None, "run not completed")
    report = evaluator.evaluate(result, repository, version, calendar)
    if not reused:
        repository.write_evaluation(report, report.rows)
    if report.status is not EvaluationStatus.COMPLETED:
        return _FoldRun(unit.fold_index, result.run_id, reused, None, None, "evaluation failed")
    count = _trade_count(report.metrics)
    if count is None:
        return _FoldRun(unit.fold_index, result.run_id, reused, None, None, "no trade count")
    return _FoldRun(unit.fold_index, result.run_id, reused, count, report.trades, "completed")


# --- 割り引いた指標の入力（D16 §5.1。R1-3 (a)）------------------------------------------


def _annual(metrics: Sequence[MetricRecord]) -> Decimal | None:
    record = _metric(metrics, MetricId.ANNUALIZED_SHARPE_RATIO)
    return (
        record.value.ratio if record is not None and isinstance(record.value, RatioValue) else None
    )


def _selection_returns(
    root: Path, run_id: RunId, calendar: TradingCalendar
) -> tuple[Decimal, ...] | None:
    repository = FileSystemResultRepository(root=root)
    manifest = repository.read_manifest(run_id)
    if not isinstance(manifest, RunManifest):
        return None
    points = equity_points(repository, run_id)
    if isinstance(points, str):
        return None
    ends = trading_day_ends(calendar, manifest.config.run_interval)
    if ends is None:
        return None
    return daily_returns(manifest.config.account.initial_balance.amount, points, ends)


def _deflated(
    *,
    directory: Path,
    root: Path,
    variant: Variant,
    last_fold: int,
    selected: int | None,
    calendar: TradingCalendar | None,
    trial_total: int,
    rules: ReviewRules,
) -> DeflatedSharpeOutcome | str:
    """最後の fold の選定区間の値と、同じ区間の全試行の値から割り引いた指標を求める。"""
    if selected is None or calendar is None:
        return "the selection or the calendar cannot be read"
    try:
        table = read_trial_metrics(directory, variant)
        _, runs = read_unit_records(directory, variant)
    except KernelValueError as exc:
        return f"the search records cannot be read: {exc}"
    unit = TrialUnitKey(
        variant=variant, fold_index=last_fold, phase=TrialPhase.TRAIN, trial_index=selected
    )
    metrics = table.get(unit)
    record = runs.get(unit)
    if metrics is None or record is None or record.run_id is None:
        return "the selection unit of the last fold is not recorded"
    trials = tuple(
        _annual(rows)
        for key, rows in sorted(table.items(), key=lambda item: item[0].trial_index)
        if key.fold_index == last_fold and key.phase is TrialPhase.TRAIN
    )
    return deflated_sharpe(
        DeflatedSharpeInputs(
            selected_annual=_annual(metrics),
            returns=_selection_returns(root, record.run_id, calendar),
            trial_annuals=trials,
            trial_total=trial_total,
            trade_count=_trade_count(metrics),
            min_trades=rules.dsr_min_trades,
            max_flat_day_share=rules.dsr_max_flat_day_share,
        )
    )


def _dsr_summary(outcome: DeflatedSharpeOutcome | str) -> DsrSummary:
    if isinstance(outcome, str):
        return DsrSummary(
            state="UNAVAILABLE", reasons=("INPUT_UNREADABLE",), marks=(), assumption_reasons=()
        )
    return DsrSummary(
        state="SHOWN" if outcome.shown else "UNAVAILABLE",
        reasons=tuple(item.value for item in outcome.unavailable) if not outcome.shown else (),
        marks=tuple(item.value for item in outcome.marks),
        assumption_reasons=tuple(item.value for item in outcome.assumption_reasons),
    )


# --- 組ごとの審査 ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _VariantReview:
    fixed: FixedTrial | None
    folds: tuple[_FoldRun, ...]
    minimum: int | None
    floor: FloorOutcome
    fit: CostFitResult
    dsr: DeflatedSharpeOutcome | str
    detail: str


def _minimum_trades(
    repo_root: Path, accepted: EntranceAccepted, variant: Variant
) -> tuple[int | None, str]:
    """研究ポリシーの現行の版の検証の合計の最低件数（仮置き 4。組の頻度区分の値）。"""
    ref = accepted.research_policy_ref
    path = research_policy_path(repo_root, ref.policy_id, ref.version)
    policy = load_research_policy(path, policy_id=ref.policy_id, version=ref.version)
    verify_registered(
        policy,
        load_research_policy_registry(research_policy_registry_path(repo_root)),
        registry_path=research_policy_registry_path(repo_root),
    )
    standard = policy.evaluation_standard
    results = accepted.finished.entry.results
    if standard is None or results is None:
        return None, "the research policy has no evaluation standard"
    name = results.variants.of(variant).frequency_class
    found = next((item for item in standard.sufficiency.classes if item.name == name), None)
    if found is None:
        return None, f"the frequency class {name!r} is not in the research policy"
    return found.min_validation_trades_total, "read"


def _review_variant(
    *,
    variant: Variant,
    manifest: ExperimentManifest,
    directory: Path,
    root: Path,
    out_root: Path,
    wiring: composition.ReviewRunWiring | str,
    calendar: TradingCalendar | None,
    accepted: EntranceAccepted,
    rules: ReviewRules,
    repo_root: Path,
    trial_total: int,
) -> _VariantReview:
    split = manifest.split
    if not isinstance(split, SplitSpec):  # pragma: no cover - 探索の記録票は分割を持つ
        raise ArtifactBindingMismatch("the manifest of a search experiment carries its split")
    folds = split.folds
    last = len(folds) - 1
    intervals = tuple(fold.validation for fold in folds)
    minimum, minimum_detail = _minimum_trades(repo_root, accepted, variant)
    basis = accepted.started.entry.basis.of(variant)
    try:
        selection = read_selections(directory, variant).get(last)
    except KernelValueError:
        selection = None
    selected = None if selection is None else selection.selected_trial_index
    fixed: FixedTrial | None = None
    runs: list[_FoldRun] = []
    detail = minimum_detail
    if selected is not None and 0 <= selected < len(manifest.trials):
        fixed = FixedTrial(
            trial_index=selected, assignment_digest=digest(manifest.trials[selected].assignment)
        )
    if fixed is None:
        detail = "the trial selected in the last fold cannot be read"
    elif isinstance(wiring, str) or calendar is None:
        detail = wiring if isinstance(wiring, str) else "the calendar cannot be read"
    else:
        roots = tuple(dict.fromkeys((out_root.absolute(), root)))
        for fold in folds:
            unit = TrialUnitKey(
                variant=variant,
                fold_index=fold.fold_index,
                phase=TrialPhase.VALIDATION,
                trial_index=fixed.trial_index,
            )
            try:
                runs.append(
                    _fixed_run(
                        wiring=wiring,
                        trial_index=fixed.trial_index,
                        unit=unit,
                        roots=roots,
                        calendar=calendar,
                    )
                )
            except (KernelValueError, OSError, ValueError) as exc:
                runs.append(_FoldRun(fold.fold_index, None, False, None, None, type(exc).__name__))
    counts: list[int | None] = [run.trade_count for run in runs] if runs else [None]
    floor = evidence_floor(counts, minimum)
    trades: tuple[TradeRecord, ...] | None = None
    if runs and all(run.trades is not None for run in runs):
        trades = tuple(trade for run in runs for trade in (run.trades or ()))
    fit = cost_model_fit(
        rules=rules,
        trades=trades,
        intervals=intervals,
        calendar=calendar,
        symbol=basis.execution_series.symbol,
        account_currency=basis.account.currency,
    )
    dsr = _deflated(
        directory=directory,
        root=root,
        variant=variant,
        last_fold=last,
        selected=selected,
        calendar=calendar,
        trial_total=trial_total,
        rules=rules,
    )
    return _VariantReview(
        fixed=fixed,
        folds=tuple(runs),
        minimum=minimum,
        floor=floor,
        fit=fit,
        dsr=dsr,
        detail=detail,
    )


# --- 数値の記録（版管理の外。D16 §10.4）--------------------------------------------------


def _text(value: object) -> object:
    """数値の記録の JSON の値（`Decimal` は十進の文字列、時間は秒の文字列）。"""
    if isinstance(value, Decimal):
        return "0" if value == 0 else str(value)
    if isinstance(value, timedelta):
        return str(value.total_seconds())
    return value


def _dsr_payload(outcome: DeflatedSharpeOutcome | str) -> dict[str, object]:
    if isinstance(outcome, str):
        return {"state": "UNAVAILABLE", "reasons": ["INPUT_UNREADABLE"], "detail": outcome}
    return {
        "state": "SHOWN" if outcome.shown else "UNAVAILABLE",
        "dsr": _text(outcome.dsr),
        "sr0": _text(outcome.sr0),
        "sr_hat": _text(outcome.sr_hat),
        "trial_total_n": outcome.trial_total,
        "variance_v": "NOT_USED (N = 1)" if outcome.trial_total == 1 else _text(outcome.variance),
        "observations_t": outcome.observations,
        "skewness_g3": _text(outcome.skewness),
        "kurtosis_g4": _text(outcome.kurtosis),
        "excluded_trials": outcome.excluded_trials,
        "flat_days": outcome.flat_days,
        "unavailable_reasons": [item.value for item in outcome.unavailable],
        "marks": [item.value for item in outcome.marks],
        "assumption_reasons": [item.value for item in outcome.assumption_reasons],
        "assumption": (
            "N は台帳の数え方 (a) の試行の数の合計（他の実験の試行を含む）で、ばらつき V は"
            "この実験の試行だけから取る。他の実験の試行も同じばらつきを持つと仮定する（D16 §5.2）"
        ),
    }


def _fit_payload(fit: CostFitResult) -> dict[str, object]:
    measures = fit.measures
    return {
        "rows": [
            {
                "row": row.row.value,
                "outcome": row.outcome.value,
                "capabilities": [item.value for item in row.capabilities],
            }
            for row in fit.rows
        ],
        "measures": {
            name: _text(getattr(measures, name)) for name in type(measures).__dataclass_fields__
        },
    }


def _numeric_record(
    *,
    accepted: EntranceAccepted,
    nonce: str,
    rules: ReviewRules,
    reviews: Paired[_VariantReview],
    tally: LedgerTally,
    declaration: DeclarationState,
    declaration_detail: str,
    result: ReviewResult,
    reasons: Sequence[ReviewReason],
) -> dict[str, Any]:
    started = accepted.started.entry

    def variant_payload(review: _VariantReview) -> dict[str, object]:
        counts = [run.trade_count for run in review.folds]
        return {
            "fixed_trial": None
            if review.fixed is None
            else {
                "trial_index": review.fixed.trial_index,
                "assignment_digest": review.fixed.assignment_digest.hex,
            },
            "detail": review.detail,
            "folds": [
                {
                    "fold_index": run.fold_index,
                    "run_id": None if run.run_id is None else run.run_id.hex,
                    "reused": run.reused,
                    "trade_count": run.trade_count,
                    "detail": run.detail,
                }
                for run in review.folds
            ],
            "trade_total": None
            if not counts or any(item is None for item in counts)
            else sum(item for item in counts if item is not None),
            "min_validation_trades_total": review.minimum,
            "evidence_floor": review.floor.value,
            "cost_model_fit": _fit_payload(review.fit),
            "deflated_sharpe": _dsr_payload(review.dsr),
        }

    return {
        "schema_version": 1,
        "experiment_id": str(started.experiment_id),
        "execution": started.execution,
        "experiment_name": started.experiment_name,
        "experiment_version": started.experiment_version,
        "review_nonce": nonce,
        "result": result.value,
        "reasons": [item.value for item in reasons],
        "note": EVIDENCE_NOTE,
        "review_rules": {
            "id": rules.rules_id,
            "version": rules.version,
            "digest": accepted.rules_entry.digest.hex,
            "window_length_seconds": int(rules.window_length.total_seconds()),
            "max_concentration_ratio": str(rules.max_concentration_ratio),
            "max_fixed_cost_share": str(rules.max_fixed_cost_share),
            "swap_rule": rules.swap_rule.value,
            "dsr_min_trades": rules.dsr_min_trades,
            "dsr_max_flat_day_share": str(rules.dsr_max_flat_day_share),
        },
        "variants": {variant.value: variant_payload(reviews.of(variant)) for variant in VARIANTS},
        "ledger_tally": {name: getattr(tally, name) for name in LedgerTally.__dataclass_fields__},
        "declaration": {
            "status": declaration.status.value,
            "file_digest": None if declaration.file_digest is None else declaration.file_digest.hex,
            "detail": declaration_detail,
        },
    }


def _markdown(record: Mapping[str, Any]) -> str:
    """数値の記録を人が読む形にしたもの（`review.md`。読むのは人間と分析役。R1-2 (a)）。"""
    lines = [
        f"# 採用審査の記録（{record['experiment_name']} v{record['experiment_version']}・"
        f"実行 {record['execution']}）",
        "",
        "この記録は版管理の外に置く。数値を読むのは人間と分析役だけ（D16 §10.4。R1-2 (a)）。",
        "",
        f"- 結果: {record['result']}",
        f"- 理由: {', '.join(record['reasons']) or 'なし'}",
        f"- 注記: {record['note']}",
        f"- 審査の規則: {record['review_rules']['id']} 版 {record['review_rules']['version']}",
        "",
    ]
    for variant in VARIANTS:
        item = record["variants"][variant.value]
        lines.extend(
            [
                f"## {variant.value} の組",
                "",
                f"- 固定した試行: {item['fixed_trial']}",
                f"- fold ごとの完了取引の件数: {[run['trade_count'] for run in item['folds']]}",
                f"- 合計 / 最低件数: {item['trade_total']} / {item['min_validation_trades_total']}",
                f"- 証拠の最低線: {item['evidence_floor']}",
                f"- 適合の検査: {item['cost_model_fit']['rows']}",
                f"- 適合の検査の印の値: {item['cost_model_fit']['measures']}",
                f"- 割り引いた指標: {item['deflated_sharpe']}",
                "",
            ]
        )
    lines.extend(
        [
            "## 台帳の累計",
            "",
            f"{record['ledger_tally']}",
            "",
            "## 申告",
            "",
            f"{record['declaration']}",
            "",
        ]
    )
    return "\n".join(lines)


# --- 1 回の審査 ---------------------------------------------------------------------------


def review_experiment(
    *,
    experiment_id: str,
    execution: int,
    repo_root: Path,
    experiment_dir: Path | None,
    snapshots_root: Path | None,
    out_root: Path | None,
) -> ReviewOutcome:
    """`odyssey-fx experiment review` の 1 回（D16 §2.2・§11）。"""
    repo_root = Path(repo_root)
    target = _experiment_id(experiment_id)
    if isinstance(execution, bool) or execution < 1:
        raise ConfigError(f"`--execution` は 1 以上: {execution}")
    trial_lines = _trial_lines(repo_root)
    policy_registry = load_research_policy_registry(research_policy_registry_path(repo_root))
    review_lines = _review_lines(repo_root)
    rules_registry = load_adoption_review_registry(adoption_review_registry_path(repo_root))
    try:
        entrance = check_entrance(
            trial_lines=trial_lines,
            policy_registry=policy_registry,
            review_lines=review_lines,
            rules_registry=rules_registry,
            experiment_id=target,
            execution=execution,
        )
    except KernelValueError as exc:
        raise ConfigError(f"審査の規則の登録簿を試行台帳に当てられない: {exc}") from exc
    if isinstance(entrance, EntranceRejected):
        return ReviewOutcome(
            ReviewResult.DO_NOT_PROCEED, (entrance.reason,), None, None, None, False
        )
    accepted = entrance
    rules, rules_digest = load_adoption_review_rules(
        adoption_review_rules_path(repo_root, accepted.rules_entry.version),
        version=accepted.rules_entry.version,
    )
    prior = accepted.prior_reviews
    if rules_file_mismatch(accepted.rules_entry, rules, rules_digest) or (
        prior and prior[0].entry.review_rules_ref.digest != rules_digest
    ):
        return ReviewOutcome(
            ReviewResult.DO_NOT_PROCEED,
            (ReviewReason.ENTRY_RULES_VERSION_CHANGED,),
            None,
            None,
            None,
            False,
        )
    directory, root, manifest = _bound_directory(experiment_dir, accepted)
    if snapshots_root is None or out_root is None:
        raise ConfigError("入口を通った実行の審査には `--snapshots` と `--out` が要る")
    calendar = composition.manifest_calendar(manifest)
    try:
        wiring: composition.ReviewRunWiring | str = composition.review_run_wiring(
            manifest=manifest,
            snapshots_root=snapshots_root,
            repo_root=repo_root,
            artifacts_root=out_root,
        )
    except (ConfigError, KernelValueError, OSError, ValueError) as exc:
        wiring = f"the search cannot be rebuilt from the manifest: {type(exc).__name__}"
    tally = ledger_tally(trial_lines, accepted.started)
    reviews = Paired.build(
        lambda variant: _review_variant(
            variant=variant,
            manifest=manifest,
            directory=directory,
            root=root,
            out_root=out_root,
            wiring=wiring,
            calendar=calendar,
            accepted=accepted,
            rules=rules,
            repo_root=repo_root,
            trial_total=tally.overlapping_trials,
        )
    )
    loaded = load_declaration(declaration_path(repo_root, target, execution), target, execution)
    declaration = DeclarationState(status=loaded.status, file_digest=loaded.file_digest)
    fits: Paired[tuple[CostFitRow, ...]] = reviews.map(lambda _, item: item.fit.rows)
    result, reasons = combine_result(
        floors=reviews.map(lambda _, item: item.floor),
        fits=fits,
        declaration=declaration.status,
    )
    nonce = secrets.token_hex(16)
    record = _numeric_record(
        accepted=accepted,
        nonce=nonce,
        rules=rules,
        reviews=reviews,
        tally=tally,
        declaration=declaration,
        declaration_detail=loaded.detail,
        result=result,
        reasons=reasons,
    )
    version_directory = (
        Path(out_root).absolute()
        / "runs"
        / "experiments"
        / manifest.experiment_name
        / f"v{manifest.experiment_version}"
    )
    try:
        place, record_digest = write_review_record(
            Path(out_root).absolute(), version_directory, nonce, record, _markdown(record)
        )
    except (OSError, KernelValueError) as exc:
        raise ReviewNotRecorded(
            f"数値の記録を書けなかった（審査台帳の行は書いていない）: {type(exc).__name__}: {exc}"
        ) from exc
    started = accepted.started.entry
    entry = AdoptionReviewEntry(
        schema_version=ADOPTION_REVIEW_SCHEMA_VERSION,
        experiment_id=started.experiment_id,
        execution=started.execution,
        experiment_name=started.experiment_name,
        experiment_version=started.experiment_version,
        strategy_id=started.strategy_id,
        review_nonce=nonce,
        started_line_digest=accepted.started.digest,
        finished_line_digest=accepted.finished.digest,
        trial_ledger_head=trial_lines[-1].digest,
        research_policy_ref=accepted.research_policy_ref,
        review_rules_ref=accepted.rules_entry.ref,
        fixed_trials=reviews.map(lambda _, item: item.fixed),
        evidence_floor=EvidenceFloorRecord(
            outcomes=reviews.map(lambda _, item: item.floor), note=EVIDENCE_NOTE
        ),
        cost_model_fit=fits,
        deflated_sharpe=reviews.map(lambda _, item: _dsr_summary(item.dsr)),
        ledger_tally=tally,
        declaration=declaration,
        result=result,
        reasons=reasons,
        numeric_record_digest=record_digest,
        code_digest=composition.code_digest(),
        lock_digest=composition.lock_digest(repo_root),
    )
    line = AdoptionReviewLine.of(entry, last_review_digest(review_lines))
    appended = append_review_ledger_file(repo_root, line)
    if isinstance(appended, ReviewAppendRefused):
        raise ReviewNotRecorded(
            f"審査台帳に行を残せなかった（{appended.kind.value}: {appended.detail}）。"
            f"数値の記録 {place} は審査として扱わない。原因を直して審査をやり直す"
        )
    return ReviewOutcome(
        result=result,
        reasons=reasons,
        entry=entry,
        line_digest=line.digest,
        record_directory=place,
        sync_failed=appended.sync_failed,
    )
