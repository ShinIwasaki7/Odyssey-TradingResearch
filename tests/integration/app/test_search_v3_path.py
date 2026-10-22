"""研究ポリシー第 3 版の探索の実験の受入テスト（人工データ。D13 §4.5〜§4.7・§6.4・§7.1、D09 の
2026-10-21 改訂。順 4 の実装 PR）。

T02 の人工データを受け入れた作業場に候補の組の snapshot を足し（評価範囲の中は同じ値で、延ばした
週だけが違う）、試験用の研究ポリシー版 3（2 fold。機構の確認）を指す探索の実験を 1 試行で走らせる。
1 試行なので両 fold で同じ試行が選ばれ、連続口座の検証経路は 2 つの fold の検証区間をつないだ
1 区間（1 本の run）になる（D13-22 (a)）。

確かめること:

- 両組（標準 → 候補）の単位・選定・集約表が `search/<組>/` に、経路の区間の開始記録と終端の記録が
  `search/<組>/path/` にそろう（実装 PR の仮置き 47・48）。
- 区間の run は終わり方 `CLOSE_ALL`・初期残高は実験設定の初期残高で、独立口座の run と別の
  `run_id` になる（D13-23 (a)）。区間の終わりの費用を引いた後の資産は残高 + 仮に閉じた損益
  （方式 (ii)。仮置き 28・45）。
- 結末記録は組ごとの結果と組の判定、試行台帳は形式版 2 の 2 行（組ごとの比較の前提・結末の行の
  組ごとの結果）。
- 同じ版の再実行は区間の run を再利用する（仮置き 26）。
- `experiment reproduce` は単位・選定・区間を照合して `REPRODUCED`（D13-25 (a)、仮置き 57）。
- レポートに組の判定・2 軸・保留の印・経路の条件・診断の一覧が出る（仮置き 41 (a)）。

人工データだけを使う（実価格・成績の値は確かめない。件数・識別子・構造だけを見る）。
"""

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config.experiment_v2 import load_experiment_v2
from odyssey_fx.backtest.domain.policies import RunEndMode
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.money import CurrencyCode, Money
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemExperimentStore,
    FileSystemResultRepository,
    read_experiment_outcome,
    read_path_records,
    read_selections,
    read_trial_ledger_file,
    read_unit_records,
)
from odyssey_fx.evaluation.application import run_experiment
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.application.run_experiment import (
    PreparedSearch,
    RecordedSearch,
    RunExperiment,
)
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS, MetricId, RatioValue
from odyssey_fx.evaluation.domain.path import (
    PathEnd,
    PathSegmentEnd,
    PathSegmentKey,
    PathSummary,
    PlannedSegment,
)
from odyssey_fx.evaluation.domain.research_policy import check_evaluation_rule, check_run_matches
from odyssey_fx.evaluation.domain.search import (
    TRIAL_LEDGER_SCHEMA_VERSION,
    CandidateStatus,
    FoldSelection,
    TrialLedgerEvent,
    TrialPhase,
    TrialUnitKey,
)
from odyssey_fx.evaluation.domain.splits import SplitSpec
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.evaluation.domain.variant import VARIANTS, Variant
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.acceptance.t02_workspace import (
    T02Workspace,
    accept_candidate,
    build_workspace,
)
from tests.fixtures.evaluation.search_experiments import install_policy_v3, write_search_experiment

#: 1 試行（両軸とも値 1 つ）。両 fold で同じ試行が選ばれ、経路は 1 区間になる。
ONE_TRIAL = (
    "    - {instance: m15_ema, parameter: period, values: [10]}\n"
    "    - {instance: stop_level, parameter: lookback, values: [10]}"
)

#: 2 試行（片方の軸の値が 2 つ）。選定を与えて試行が変わる境目の経路を作るのに使う。
TWO_TRIALS = (
    "    - {instance: m15_ema, parameter: period, values: [10, 20]}\n"
    "    - {instance: stop_level, parameter: lookback, values: [10]}"
)


@dataclass(frozen=True)
class _Setup:
    workspace: T02Workspace
    candidate: str
    out: Path
    code: int
    output: str


def _call(argv: list[str]) -> tuple[int, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue() + stderr.getvalue()


def _run(workspace: T02Workspace, experiment: Path, out: Path) -> tuple[int, str]:
    return _call(
        [
            "experiment",
            "run",
            "--experiment",
            str(experiment),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(workspace.repo),
        ]
    )


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> _Setup:
    root = tmp_path_factory.mktemp("search-v3")
    workspace = build_workspace(root)
    candidate = accept_candidate(workspace, root / "candidate")
    install_policy_v3(workspace.repo)
    ledger = workspace.repo / "research" / "trial_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"")
    experiment = write_search_experiment(
        workspace.repo,
        "v3_path.yaml",
        experiment_id="v3_path",
        axes=ONE_TRIAL,
        max_trials=1,
        candidate_snapshot=candidate,
    )
    out = root / "out"
    code, output = _run(workspace, experiment, out)
    return _Setup(workspace=workspace, candidate=candidate, out=out, code=code, output=output)


def _directory(setup: _Setup) -> Path:
    return setup.out / "runs/experiments/v3_path/v1"


def test_both_variants_are_recorded_under_their_own_directories(setup: _Setup) -> None:
    assert setup.code == 0, setup.output
    directory = _directory(setup)
    for variant in VARIANTS:
        starts, runs = read_unit_records(directory, variant)
        assert set(starts) == set(runs)
        assert all(unit.variant is variant for unit in runs)
        assert sorted(read_selections(directory, variant)) == [0, 1]
        assert (directory / "search" / variant.value / "trial_units.parquet").is_file()
        assert (directory / "search" / variant.value / "trial_metrics.parquet").is_file()
    standard_runs = read_unit_records(directory, Variant.STANDARD)[1]
    candidate_runs = read_unit_records(directory, Variant.CANDIDATE)[1]
    unit = TrialUnitKey(
        variant=Variant.STANDARD, fold_index=0, phase=TrialPhase.TRAIN, trial_index=0
    )
    other = TrialUnitKey(
        variant=Variant.CANDIDATE, fold_index=0, phase=TrialPhase.TRAIN, trial_index=0
    )
    # 組が違えば snapshot が違うので run も別（D09 §6.2 の改訂 (3)）。
    assert standard_runs[unit].run_id != candidate_runs[other].run_id


def test_the_path_joins_the_two_folds_of_the_same_trial_into_one_close_all_run(
    setup: _Setup,
) -> None:
    directory = _directory(setup)
    repository = FileSystemResultRepository(root=setup.out)
    for variant in VARIANTS:
        starts, ends = read_path_records(directory, variant)
        assert sorted(starts) == sorted(ends) == [0]
        start, end = starts[0], ends[0]
        assert start.fold_indices == (0, 1)
        assert start.run_end is RunEndMode.CLOSE_ALL
        assert start.initial_balance.amount == Decimal("1000000")
        assert end.run_id == start.expected_run_id and not end.run_reused
        assert end.succeeded
        manifest = repository.read_manifest(end.run_id)
        assert isinstance(manifest, RunManifest)
        assert manifest.config.run_end is RunEndMode.CLOSE_ALL
        # 区間の終わりの費用を引いた後の資産 = 残高 + 仮に閉じた損益（方式 (ii)。仮置き 28・45）。
        result = repository.read_result(end.run_id)
        summaries = result.summaries  # type: ignore[union-attr]
        assert summaries is not None and end.final_balance is not None
        assert end.settled_equity == end.final_balance + summaries.hypothetical_closed
        assert len(end.settlement_lines) == len(summaries.hypothetical_close_lines)
        _, runs = read_unit_records(directory, variant)
        assert end.run_id not in {record.run_id for record in runs.values()}


def test_the_outcome_holds_both_variants_and_the_combined_verdict(setup: _Setup) -> None:
    outcome = read_experiment_outcome(_directory(setup))
    assert outcome is not None and outcome.search is not None
    search = outcome.search
    for variant, item in search.variants.items():
        assert item.variant is variant
        path = item.path
        assert path.end is PathEnd.COMPLETED
        assert [row.carried_boundaries for row in path.segments] == [1]
        assert [metric.value for metric, _ in path.metrics] == [
            "NET_RETURN_RATE",
            "MAX_DRAWDOWN_MTM_RATE_PEAK",
        ]
    assert search.combined.verdict is search.verdict


def test_the_ledger_rows_are_of_version_two(setup: _Setup) -> None:
    contents = read_trial_ledger_file(setup.workspace.repo / "research" / "trial_ledger.jsonl")
    assert isinstance(contents, TrialLedgerContents), contents
    started, finished = (line.entry for line in contents.lines[:2])
    assert started.schema_version == TRIAL_LEDGER_SCHEMA_VERSION == 2
    assert started.event is TrialLedgerEvent.STARTED and finished.event is TrialLedgerEvent.FINISHED
    for variant, basis in started.basis.items():
        assert basis.variant is variant
        assert basis.run_end is RunEndMode.MARK_TO_MARKET
    assert str(started.basis.candidate.snapshot_ref.snapshot_id) == setup.candidate
    assert finished.results is not None and finished.frequency_class is None
    outcome = read_experiment_outcome(_directory(setup))
    assert outcome is not None and outcome.search is not None
    assert finished.verdict is outcome.search.verdict


def test_the_report_shows_the_combined_verdict_the_axes_and_the_path(setup: _Setup) -> None:
    text = (_directory(setup) / "report.md").read_text(encoding="utf-8")
    for expected in (
        "組の判定",
        "標準の組（standard）",
        "候補の組（candidate）",
        "収益性・リスクの軸",
        "証拠の軸",
        "保留の印",
        "全体の結果は fold ごとに選び直す手続きの結果",
        "統計的な十分性の保証ではない",
        "連続口座の検証経路の条件",
        "全期間の使用の確認",
        "選ばれた試行の変遷",
        "統計の診断",
        "ブロック・ブートストラップは表示しない",
        "参考: 独立口座の連鎖",
    ):
        assert expected in text, expected


def test_the_search_is_reproduced_in_another_root(setup: _Setup, tmp_path: Path) -> None:
    """探索の実験の再現（D13-25 (a)）: 単位・選定・区間の照合がすべて一致する。"""
    out = tmp_path / "reproduce"
    code, output = _call(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(_directory(setup)),
            "--snapshots",
            str(setup.workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(setup.workspace.repo),
        ]
    )
    assert code == 0, output
    report = json.loads((out / "reproduction.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "REPRODUCED"
    items = {(item["item"], item["variant"]) for item in report["items"]}
    for variant in ("standard", "candidate"):
        assert {("UNIT", variant), ("SELECTION", variant), ("SEGMENT", variant)} <= items
    assert all(item["verdict"] == "REPRODUCED" for item in report["items"])


def test_rerunning_the_same_version_reuses_the_segment_runs(setup: _Setup) -> None:
    """同じ版の再実行は区間の run を再利用する（仮置き 26。D09 §11.3 の退避の後）。"""
    experiment = setup.workspace.repo / "configs/experiments/v3_path.yaml"
    code, output = _run(setup.workspace, experiment, setup.out)
    assert code == 0, output
    for variant in VARIANTS:
        _, ends = read_path_records(_directory(setup), variant)
        assert ends[0].run_reused


# --- 経路の組み立ての分岐（候補なしの区間・資金喪失・失敗した区間。D13 §4.6 の表 2）-------------


def _segment_end(
    prepared: PreparedSearch,
    key: PathSegmentKey,
    settled: str | None,
    *,
    status: RunStatus = RunStatus.COMPLETED,
) -> PathSegmentEnd:
    """区間の run の結果の代わり（run はしない。残高と状態だけを決めた人工の終端の記録）。"""
    run_id = RunId(digest(f"segment {key}"))
    checks = (
        check_run_matches(
            expected_config_digest_hex="0" * 64,
            expected_run_id_hex=run_id.hex,
            observed_config_digest_hex="0" * 64,
            observed_run_id_hex=run_id.hex,
        ),
        check_evaluation_rule(2, 2),
    )
    money = None if settled is None else Money(Decimal(settled), CurrencyCode("JPY"))
    return PathSegmentEnd(
        experiment_id=prepared.manifest.experiment_id,
        key=key,
        expected_run_id=run_id,
        run_id=run_id,
        run_status=status,
        run_reused=False,
        run_evaluation_id=digest("evaluation"),
        evaluation_status=EvaluationStatus.COMPLETED,
        result_digest=digest("result"),
        final_balance=money,
        final_equity=money,
        settlement_lines=(),
        settled_equity=money,
        trade_count=None if money is None else 0,
        outcome_checks=checks,
    )


def _selection(fold: int, trial: int | None) -> FoldSelection:
    if trial is None:
        return FoldSelection(
            fold_index=fold,
            selected_trial_index=None,
            selected_value=None,
            selected_train_trade_count=None,
            inputs=(),
        )
    return FoldSelection(
        fold_index=fold,
        selected_trial_index=trial,
        selected_value=Decimal("0"),
        selected_train_trade_count=0,
        inputs=((trial, None, CandidateStatus.CANDIDATE),),
    )


def _path(
    setup: _Setup,
    monkeypatch: pytest.MonkeyPatch,
    selections: tuple[int | None, ...],
    settled: dict[int, str | None],
    ran: list[int] | None = None,
) -> PathSummary:
    loaded = load_experiment_v2(
        setup.workspace.repo / "configs/experiments/v3_path.yaml",
        repo_root=setup.workspace.repo,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )
    prepared = composition.prepare_search(
        loaded=loaded,
        snapshots_root=setup.workspace.repo / "data/snapshots",
        repo_root=setup.workspace.repo,
    )

    def fake_segment(
        self: RunExperiment,
        prepared_: PreparedSearch,
        variant: Variant,
        segment: PlannedSegment,
        balance: Money,
        planner: object,
        cache: object,
    ) -> PathSegmentEnd:
        if ran is not None:
            ran.append(segment.segment_index)
        value = settled[segment.segment_index]
        key = PathSegmentKey(variant=variant, segment_index=segment.segment_index)
        if value is None:
            return _segment_end(prepared_, key, None, status=RunStatus.FAILED_DATA_ERROR)
        return _segment_end(prepared_, key, value)

    monkeypatch.setattr(RunExperiment, "_run_segment", fake_segment)
    monkeypatch.setattr(
        run_experiment, "equity_series", lambda repository, run_id: (Decimal("1000000"),)
    )
    use_case = RunExperiment(
        store=object(),  # type: ignore[arg-type]
        runner=object(),  # type: ignore[arg-type]
        repository=object(),  # type: ignore[arg-type]
        evaluator=EvaluateRun(evaluation_code_digest=prepared.code_digest),
    )
    run = use_case._run_path(
        prepared,
        Variant.STANDARD,
        tuple(_selection(fold, trial) for fold, trial in enumerate(selections)),
        object(),  # type: ignore[arg-type]
        {},
    )
    return run.summary


def test_a_fold_without_a_candidate_is_a_cash_segment(
    setup: _Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候補なしの fold は単独の区間で、残高を現金のまま渡す（仮置き 15）。"""
    summary = _path(setup, monkeypatch, (0, None), {0: "1050000"})
    assert summary.end is PathEnd.COMPLETED
    assert [row.trial_index for row in summary.segments] == [0, None]
    assert summary.segments[1].initial_balance == summary.segments[1].end_balance
    assert summary.final_balance == Money(Decimal("1050000"), CurrencyCode("JPY"))
    assert summary.metric(MetricId.NET_RETURN_RATE) == RatioValue(Decimal("0.05"))


def test_a_settled_balance_at_or_below_zero_ends_the_path_with_a_capital_loss(
    setup: _Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """区間の終わりの残高が 0 以下なら資金喪失で終える（仮置き 29）。"""
    summary = _path(setup, monkeypatch, (0, 0), {0: "-10"})
    assert summary.end is PathEnd.CAPITAL_LOST and summary.end_segment == 0
    rate = summary.metric(MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK)
    assert isinstance(rate, RatioValue) and rate.ratio >= 1


def test_a_lineage_from_an_execution_not_in_the_ledger_is_a_config_error(
    setup: _Setup, tmp_path: Path
) -> None:
    """系譜の元の実行が台帳に無ければ設定の誤り（終了コード 2。D13 §7.1、仮置き 56）。"""
    experiment = write_search_experiment(
        setup.workspace.repo,
        "v3_lineage.yaml",
        experiment_id="v3_lineage",
        axes=ONE_TRIAL,
        max_trials=1,
        candidate_snapshot=setup.candidate,
    )
    experiment.write_text(
        experiment.read_text(encoding="utf-8")
        + "lineage:\n"
        + f'  derived_from: {{experiment_id: "{"9" * 64}", execution: 1}}\n'
        + '  change_kinds: [RETRY]\n  note: "やり直し"\n',
        encoding="utf-8",
    )
    code, output = _run(setup.workspace, experiment, tmp_path / "out")
    assert code == 2, output
    assert "試行台帳に無い" in output


def test_a_cash_segment_balance_is_compared_with_the_outcome(
    setup: _Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """再現は候補なしの区間（現金の区間）の渡す残高も結末記録の区間の行と照合する（D13 §4.5 の
    「再現」、§10.1 の 20。区間の行だけを書き換えれば、最後の残高が同じでも不一致）。"""
    summary = _path(setup, monkeypatch, (None, None), {})
    assert [row.trial_index for row in summary.segments] == [None, None]
    loaded = load_experiment_v2(
        setup.workspace.repo / "configs/experiments/v3_path.yaml",
        repo_root=setup.workspace.repo,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )
    prepared = composition.prepare_search(
        loaded=loaded,
        snapshots_root=setup.workspace.repo / "data/snapshots",
        repo_root=setup.workspace.repo,
    )
    split = prepared.manifest.split
    assert isinstance(split, SplitSpec)
    use_case = RunExperiment(
        store=object(),  # type: ignore[arg-type]
        runner=object(),  # type: ignore[arg-type]
        repository=object(),  # type: ignore[arg-type]
        evaluator=EvaluateRun(evaluation_code_digest=prepared.code_digest),
    )

    def reproduce(path: PathSummary) -> list[tuple[str, str]]:
        record = RecordedSearch(
            runs={},
            selections={},
            segment_starts={},
            segment_ends={},
            outcome_selections={},
            outcome_path=path,
        )
        rows = use_case._reproduce_path(
            prepared,
            Variant.STANDARD,
            split,
            [None, None],
            record,
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )
        return [(row.key, row.verdict.value) for row in rows]

    assert reproduce(summary) == [("s0", "REPRODUCED"), ("s1", "REPRODUCED"), ("end", "REPRODUCED")]
    changed = Money(Decimal("999"), CurrencyCode("JPY"))
    tampered = replace(
        summary,
        segments=(
            summary.segments[0],
            replace(summary.segments[1], initial_balance=changed, end_balance=changed),
        ),
    )
    assert reproduce(tampered) == [
        ("s0", "REPRODUCED"),
        ("s1", "RESULT_MISMATCH"),
        ("end", "REPRODUCED"),
    ]


def test_a_capital_loss_does_not_run_the_later_segments(
    setup: _Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """資金喪失の区間の後ろの区間は走らせない（仮置き 29。試行が変わる 2 区間の計画で確かめる）。"""
    ran: list[int] = []
    summary = _path(setup, monkeypatch, (0, 1), {0: "0", 1: "1050000"}, ran)
    assert ran == [0]
    assert summary.end is PathEnd.CAPITAL_LOST and summary.end_segment == 0
    assert [row.segment_index for row in summary.segments] == [0]


def test_the_balance_passes_to_the_next_trial_across_real_segment_runs(
    setup: _Setup, tmp_path: Path
) -> None:
    """試行が変わる境目（2 区間）を実際の区間の run で走らせ、区間 0 の終わりの費用を引いた後の
    資産が区間 1 の初期残高になる（D13 §4.5・§4.6 の表 3。仮置き 28）。選定は与える。"""
    experiment = write_search_experiment(
        setup.workspace.repo,
        "v3_two.yaml",
        experiment_id="v3_two",
        axes=TWO_TRIALS,
        max_trials=2,
        candidate_snapshot=setup.candidate,
    )
    loaded = load_experiment_v2(
        experiment,
        repo_root=setup.workspace.repo,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )
    parts = composition._prepare_search(
        loaded=loaded,
        snapshots_root=setup.workspace.repo / "data/snapshots",
        repo_root=setup.workspace.repo,
        registry=INITIAL_CATALOG,
    )
    prepared = parts.prepared
    assert len(prepared.trials) == 2
    out = tmp_path / "out"
    runner, planner = composition._search_wiring(loaded, parts, out)
    use_case = RunExperiment(
        store=FileSystemExperimentStore(
            root=out,
            experiment_name=prepared.manifest.experiment_name,
            experiment_version=prepared.manifest.experiment_version,
            identity_of=composition.recompute_experiment_id,
        ),
        runner=runner,
        repository=FileSystemResultRepository(root=out),
        evaluator=EvaluateRun(evaluation_code_digest=prepared.code_digest),
    )
    run = use_case._run_path(
        prepared, Variant.STANDARD, (_selection(0, 0), _selection(1, 1)), planner, {}
    )
    summary = run.summary
    assert summary.end is PathEnd.COMPLETED
    assert [row.trial_index for row in summary.segments] == [0, 1]
    assert [row.carried_boundaries for row in summary.segments] == [0, 0]
    first, second = run.ends
    assert first.succeeded and second.succeeded
    assert first.run_id != second.run_id
    assert first.settled_equity is not None and first.final_balance is not None
    assert summary.segments[0].initial_balance.amount == Decimal("1000000")
    assert summary.segments[1].initial_balance == first.settled_equity
    assert summary.segments[0].end_balance == first.settled_equity
    assert summary.final_balance == second.settled_equity
    manifest = FileSystemResultRepository(root=out).read_manifest(second.run_id)
    assert isinstance(manifest, RunManifest)
    assert manifest.config.account.initial_balance == first.settled_equity
    assert manifest.config.run_end is RunEndMode.CLOSE_ALL


def test_a_failed_segment_stops_the_path_as_incomplete(
    setup: _Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """区間の run が失敗すれば判定できないで終え、後ろの区間は走らせない（仮置き 16）。"""
    summary = _path(setup, monkeypatch, (0, None), {0: None})
    assert summary.end is PathEnd.INCOMPLETE and summary.end_segment == 0
    assert len(summary.segments) == 1 and summary.metrics == ()


def _copy_out(setup: _Setup, tmp_path: Path) -> Path:
    """成果物の基点を丸ごと写す（元の基点を変えずに改変・衝突を作るため）。"""
    copied = tmp_path / "copied-out"
    shutil.copytree(setup.out, copied, symlinks=True)
    return copied


def test_a_changed_passed_balance_is_not_reproduced(setup: _Setup, tmp_path: Path) -> None:
    """記録の残高を書き換えると、区間の行が RESULT_MISMATCH で終了コード 6（仮置き 20・57）。"""
    copied = _copy_out(setup, tmp_path)
    end_path = copied / "runs/experiments/v3_path/v1/search/standard/path/s0.json"
    record = json.loads(end_path.read_text(encoding="utf-8"))
    record["settled_equity"]["amount"] = "12345e0"
    end_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = tmp_path / "reproduce"
    code, output = _call(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(copied / "runs/experiments/v3_path/v1"),
            "--snapshots",
            str(setup.workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(setup.workspace.repo),
        ]
    )
    assert code == 6, output
    report = json.loads((out / "reproduction.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "RESULT_MISMATCH"
    failed = [item for item in report["items"] if item["verdict"] != "REPRODUCED"]
    assert [(item["item"], item["variant"], item["key"]) for item in failed] == [
        ("SEGMENT", "standard", "s0")
    ]


def _reproduce_copied(setup: _Setup, copied: Path, tmp_path: Path) -> tuple[int, dict[str, Any]]:
    """複製した成果物の基点の実験を再現し、終了コードと再現の報告を返す。"""
    out = tmp_path / "reproduce"
    code, output = _call(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(copied / "runs/experiments/v3_path/v1"),
            "--snapshots",
            str(setup.workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(setup.workspace.repo),
        ]
    )
    assert (out / "reproduction.json").is_file(), output
    report: dict[str, Any] = json.loads((out / "reproduction.json").read_text(encoding="utf-8"))
    return code, report


def _failed_items(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (item["item"], item["variant"], item["key"])
        for item in report["items"]
        if item["verdict"] != "REPRODUCED"
    ]


def test_a_missing_selection_record_is_not_reproduced(setup: _Setup, tmp_path: Path) -> None:
    """選定記録が無ければ、結末記録の選定と一致しても選定の行は RESULT_MISMATCH（D13 §4.5 の
    「再現」: 計算し直した選定は選定記録と照合する）。"""
    copied = _copy_out(setup, tmp_path)
    (copied / "runs/experiments/v3_path/v1/search/standard/selection_f0.json").unlink()
    code, report = _reproduce_copied(setup, copied, tmp_path)
    assert code == 6
    assert report["verdict"] == "RESULT_MISMATCH"
    assert _failed_items(report) == [("SELECTION", "standard", "f0")]


def test_a_changed_balance_in_the_outcome_is_not_reproduced(setup: _Setup, tmp_path: Path) -> None:
    """結末記録の経路の区間の残高と最後の残高を書き換えると、開始・終端の記録が正しくても区間の行と
    経路の終端の行が RESULT_MISMATCH（D13 §4.5 の「再現」: 結末記録の値とも照合する）。"""
    copied = _copy_out(setup, tmp_path)
    outcome_path = copied / "runs/experiments/v3_path/v1/experiment_outcome.json"
    outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
    path = outcome["search"]["variants"]["standard"]["path"]
    path["segments"][0]["end_balance"]["amount"] = "12345e0"
    path["final_balance"]["amount"] = "12345e0"
    outcome_path.write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    code, report = _reproduce_copied(setup, copied, tmp_path)
    assert code == 6
    assert report["verdict"] == "RESULT_MISMATCH"
    assert _failed_items(report) == [
        ("SEGMENT", "standard", "s0"),
        ("SEGMENT", "standard", "end"),
    ]


def test_an_unreadable_existing_segment_run_is_a_structural_error(
    setup: _Setup, tmp_path: Path
) -> None:
    """区間の run の既存の成果物が読めなければ構造エラーで中断（終了コード 1。仮置き 26）。"""
    copied = _copy_out(setup, tmp_path)
    _, ends = read_path_records(copied / "runs/experiments/v3_path/v1", Variant.STANDARD)
    manifest = copied / "runs" / str(ends[0].run_id) / "manifest.json"
    manifest.write_text("{broken", encoding="utf-8")
    experiment = setup.workspace.repo / "configs/experiments/v3_path.yaml"
    code, output = _run(setup.workspace, experiment, copied)
    assert code == 1, output
    assert "path segment standard 0" in output
