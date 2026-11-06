"""円以外の決済通貨の人工の候補を `odyssey-fx experiment review` に通す（D06 §19.16 の CT-19）。

順 7 の残課題 R4（D17 §3.3、D19 §5。D08 §7.5 の #72・§13.1 の未整備 1 件）: 採用審査の規則の
版 2（D16 §7.2 の行 5 の版 2。決済通貨が米ドル・スイスフランなら「合う」）を、規則の層だけでなく
審査の道具の結合の経路で確かめる。

作業場は `tests/integration/app/test_final_run.py` の CT-22 の作業場と同じ作り方（**人工データ**。
`tests/fixtures/final_conversion.py`）: EURUSD（決済通貨は米ドル）の研究期間の OANDA MT5 の経路の
人工の snapshot と、換算の snapshot（USDJPY。一定の人工の率）を標準・候補の 2 組置き、検証戦略 B の
2 試行の探索の実験（`conversion_snapshots` つき）を `experiment run` で 1 回走らせる。封印の
snapshot・origin は置かない（審査は封印を読まない）。

人工データの判定は「満たす」にならないので、試行台帳の結末の行の判定だけを人工の
`MEETS_STANDARD` にする（`test_experiment_review.py` の `_promote` と同じ。開始の行は変えない）。

確かめること（数値は読まない。終了コード・理由の識別子・審査台帳の行の項目だけ）:

- 版 1 が当たる実行（版 2 の登録より前に開始）: 行 5 が `NEEDS_CAPABILITY`
  （`CURRENCY_CONVERSION`）で進めない（終了コード 7、理由に `COST_MODEL_NEEDS_CAPABILITY`）。
- 版 2 が当たる実行（版 2 の登録より後に開始した同じ版の再実行）: 行 5 が `FITS`（理由に
  `COST_MODEL_NEEDS_CAPABILITY` が無い）。
- どちらでも、審査の中の固定した試行の再実行（換算の snapshot を使う円以外の run）は最後まで走る
  （行 3 の日またぎが `FITS` で、完了取引が読めたことを示す。run が失敗すれば行 2・行 3 は
  `UNDETERMINED`。D16 §7.2）。

この人工の候補は、固定した試行の検証区間に完了取引が無いので、証拠の最低線（`EVIDENCE_FLOOR_NOT_MET`）
と行 2 の固定のスプレッド（`UNDETERMINED`。約定が無いと集中を測れない）で、版 2 でも「進める」には
ならない。行 5 と関係の無いこの 2 つは人工データの性質なので、本テストは行 5・当たる版・再実行の
完走だけを確かめ、全体の結果（進める）は確かめない（PR の仮置き）。
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Iterator, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config.adoption_review import load_adoption_review_rules
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    TRIAL_LEDGER_PATH,
    ledger_line_bytes,
    read_trial_ledger_file,
)
from odyssey_fx.evaluation.adapters.review_store import (
    ADOPTION_REVIEW_LEDGER_PATH,
    ADOPTION_REVIEW_LOCK_PATH,
    ReviewLedgerContents,
    read_review_ledger_file,
)
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.domain.adoption_review import (
    AdoptionReviewEntry,
    Capability,
    CostFitRow,
    CostModelRow,
    FitOutcome,
    ReviewReason,
    ReviewResult,
)
from odyssey_fx.evaluation.domain.search import (
    EvidenceAxis,
    LedgerResults,
    ProfitabilityAxis,
    SearchVerdict,
    TrialLedgerEvent,
    TrialLedgerLine,
)
from odyssey_fx.evaluation.domain.variant import Paired, Variant
from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant
from tests.fixtures import final_conversion as fc
from tests.fixtures.acceptance import t02_market
from tests.fixtures.evaluation.adoption_review import REPO_ROOT, install_version_1_rules
from tests.fixtures.evaluation.research_policies import (
    TEST_BASIS,
    TEST_COMPARISON_BASIS,
    append_registry_entry,
    policy_v3_text,
    write_policy,
)
from tests.fixtures.evaluation.search_experiments import (
    BASE_EXPERIMENT,
    TWO_FOLD_SPLIT,
    search_experiment_text,
)
from tests.fixtures.synthetic import market

#: 選定 2 日・検証 2 日の 5 fold（試験用。`test_experiment_review.py` と同じ）。
_SPLIT = TWO_FOLD_SPLIT.replace('train_length: "4d"', 'train_length: "2d"').replace(
    'validation_length: "4d"', 'validation_length: "2d"'
)
#: 証拠の要件（試験用）: 区分 `ALL` だけ、検証の合計の最低件数 1。
_SUFFICIENCY = (
    "  sufficiency:\n    classes:\n"
    '      - {name: ALL, min_train_trades_per_365d: "0", min_validation_trades_per_fold: 0,'
    " min_validation_trades_total: 1}"
)
_AXES = "    - {instance: m15_ema, parameter: period, values: [10, 20]}"
_RULES = Path("configs/policies/adoption_review")
_COPIED = (
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v2.yaml",
    "configs/symbols/EURUSD.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/policies/research/research_policy_v1.yaml",
)
_EXPERIMENT = "configs/experiments/review_conversion.yaml"
#: 換算の snapshot（USDJPY）の研究履歴の足の始まり（EURUSD の足より前から覆う）。
_JPY_START = UtcTime.parse("2014-09-21T00:00:00Z")
_EUR_START = UtcTime.parse("2014-12-28T22:00:00Z")


@dataclass(frozen=True, slots=True)
class _Env:
    repo: Path
    out: Path
    directory: Path
    experiment: Path
    experiment_id: ExperimentId
    ledger: bytes


def _cli(args: Sequence[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


def _trial_lines(repo: Path) -> tuple[TrialLedgerLine, ...]:
    contents = read_trial_ledger_file(repo / TRIAL_LEDGER_PATH)
    assert isinstance(contents, TrialLedgerContents), contents
    return contents.lines


def _promote(repo: Path, experiment_id: ExperimentId, execution: int) -> None:
    """その実行の結末の行（台帳の最後の行）の判定を人工の `MEETS_STANDARD` にする。"""
    lines = list(_trial_lines(repo))
    last = lines[-1].entry
    assert (last.experiment_id, last.execution, last.event) == (
        experiment_id,
        execution,
        TrialLedgerEvent.FINISHED,
    )
    assert last.results is not None
    variants = last.results.variants.map(
        lambda _, item: replace(
            item,
            verdict=SearchVerdict.MEETS_STANDARD,
            frequency_class="ALL",
            profitability=ProfitabilityAxis.MET,
            evidence=EvidenceAxis.SUFFICIENT,
            hold=None,
        )
    )
    entry = replace(
        last,
        verdict=SearchVerdict.MEETS_STANDARD,
        results=LedgerResults(split=False, hold=None, variants=variants),
    )
    lines[-1] = TrialLedgerLine.of(entry, lines[-1].prev)
    (repo / TRIAL_LEDGER_PATH).write_bytes(b"".join(ledger_line_bytes(line) for line in lines))


def _run(repo: Path, experiment: Path, out: Path) -> None:
    code, stdout, stderr = _cli(
        [
            "experiment",
            "run",
            "--experiment",
            str(experiment),
            "--snapshots",
            str(repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(repo),
        ]
    )
    assert code == 0, stdout + stderr


def _declare(repo: Path, experiment_id: ExperimentId, execution: int) -> None:
    path = repo / "research/declarations" / f"{experiment_id}_{execution}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 1\n"
        f'experiment_id: "{experiment_id}"\n'
        f"execution: {execution}\n"
        'question: "この候補の選定・調整に、研究の外で見た相場を使ったか（テスト）"\n'
        'answer: "使っていない"\n'
        'recorded_by: "テスト"\n'
        "used_outside_viewing: false\n",
        encoding="utf-8",
    )


def _write_workspace(repo: Path) -> Path:
    """EURUSD の人工の探索の実験の作業場（CT-22 の作業場と同じ作り方）。実験設定のパスを返す。"""
    for name in _COPIED:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / name).read_bytes())
    (repo / "uv.lock").write_text("# synthetic uv.lock (conversion review)\n")
    shutil.copytree(REPO_ROOT / "docs/design/time_rules", repo / "docs/design/time_rules")
    append_registry_entry(repo, "research_policy", 1)
    strategy = repo / "configs/strategies/strategy_b_eurusd.yaml"
    strategy.parent.mkdir(parents=True, exist_ok=True)
    strategy.write_text(
        (REPO_ROOT / "configs/strategies/strategy_b_v1.yaml")
        .read_text(encoding="utf-8")
        .replace("USDJPY/", "EURUSD/"),
        encoding="utf-8",
    )
    snapshots = repo / "data" / "snapshots"
    eur_window = Interval(start=_EUR_START, end=t02_market.RUN_INTERVAL.end)
    eur = fc.eurusd_bars(t02_market.GENERATION_INTERVAL, eur_window, minutes=eur_window)
    jpy = {
        "1m": fc.quarter_minutes(
            fc.USDJPY,
            Interval(start=_JPY_START, end=t02_market.RUN_INTERVAL.end),
            fc.RATE,
            market.calendar(),
        )
    }
    variants = Paired(standard=TimeRuleVariant.STANDARD, candidate=TimeRuleVariant.CANDIDATE)
    eur_ids = variants.map(lambda _, v: fc.research_snapshot(snapshots, eur, v, fc.EURUSD))
    jpy_ids = variants.map(lambda _, v: fc.research_snapshot(snapshots, jpy, v, fc.USDJPY))
    basis = (
        TEST_BASIS.replace("1" * 64, eur_ids.standard)
        .replace("2" * 64, eur_ids.candidate)
        .replace("USDJPY/", "EURUSD/")
    )
    text = policy_v3_text(
        version=3,
        replace={
            "split": _SPLIT,
            "purpose": "  purpose: STANDARD",
            "basis": basis,
            "sufficiency": _SUFFICIENCY,
            "comparison_basis": TEST_COMPARISON_BASIS.replace("USDJPY", "EURUSD"),
        },
    )
    write_policy(repo, "research_policy", 3, text)
    append_registry_entry(repo, "research_policy", 3)
    (repo / "research").mkdir(exist_ok=True)
    (repo / TRIAL_LEDGER_PATH).write_bytes(b"")
    single = (
        (REPO_ROOT / BASE_EXPERIMENT)
        .read_text(encoding="utf-8")
        .replace("0" * 64, eur_ids.standard)
        .replace('execution_series: "USDJPY/15m/bid"', 'execution_series: "EURUSD/15m/bid"')
        .replace('  - "USDJPY/15m/bid"', '  - "EURUSD/15m/bid"')
        .replace('    USDJPY: "0.05"', '    EURUSD: "0.05"')
        .replace(
            "configs/strategies/strategy_b_v1.yaml", "configs/strategies/strategy_b_eurusd.yaml"
        )
        .replace("configs/calendars/timeframes_v1.yaml", "configs/calendars/timeframes_v2.yaml")
    )
    searched = search_experiment_text(
        single,
        experiment_id="review_conv",
        axes=_AXES,
        candidate_snapshot=eur_ids.candidate,
    )
    marker = f'  candidate: "{eur_ids.candidate}"\n'
    assert searched.count(marker) == 1
    searched = searched.replace(
        marker,
        marker
        + "conversion_snapshots:\n"
        + f'  standard: "{jpy_ids.standard}"\n'
        + f'  candidate: "{jpy_ids.candidate}"\n',
    )
    experiment = repo / _EXPERIMENT
    experiment.parent.mkdir(parents=True, exist_ok=True)
    experiment.write_text(searched, encoding="utf-8")
    return experiment


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> _Env:
    root = tmp_path_factory.mktemp("review-conversion")
    repo = root / "repo"
    experiment = _write_workspace(repo)
    out = root / "out"
    _run(repo, experiment, out)
    target = _trial_lines(repo)[-1].entry.experiment_id
    _promote(repo, target, 1)
    return _Env(
        repo=repo,
        out=out,
        directory=out / "runs/experiments/review_conv/v1",
        experiment=experiment,
        experiment_id=target,
        ledger=(repo / TRIAL_LEDGER_PATH).read_bytes(),
    )


@pytest.fixture(autouse=True)
def restore(env: _Env) -> Iterator[None]:
    """各テストは同じ出発点から始める（判定を書き換えた試行台帳・空の審査台帳・版 1 の規則）。"""
    repo = env.repo
    (repo / TRIAL_LEDGER_PATH).write_bytes(env.ledger)
    (repo / ADOPTION_REVIEW_LEDGER_PATH).write_bytes(b"")
    (repo / ADOPTION_REVIEW_LOCK_PATH).unlink(missing_ok=True)
    shutil.rmtree(repo / _RULES, ignore_errors=True)
    install_version_1_rules(repo)
    shutil.rmtree(repo / "research/declarations", ignore_errors=True)
    _declare(repo, env.experiment_id, 1)
    yield
    (repo / ADOPTION_REVIEW_LOCK_PATH).unlink(missing_ok=True)


def _review(env: _Env, *, execution: int = 1, out: Path | None = None) -> tuple[int, str, str]:
    root = out or env.out
    return _cli(
        [
            "experiment",
            "review",
            "--experiment-id",
            str(env.experiment_id),
            "--execution",
            str(execution),
            "--repo-root",
            str(env.repo),
            "--experiment-dir",
            str(root / "runs/experiments/review_conv/v1"),
            "--snapshots",
            str(env.repo / "data/snapshots"),
            "--out",
            str(root),
        ]
    )


def _reviews(env: _Env) -> tuple[AdoptionReviewEntry, ...]:
    contents = read_review_ledger_file(env.repo / ADOPTION_REVIEW_LEDGER_PATH)
    assert isinstance(contents, ReviewLedgerContents), contents
    return tuple(line.entry for line in contents.lines)


def _row(
    entry: AdoptionReviewEntry, which: CostModelRow
) -> Paired[tuple[FitOutcome, tuple[Capability, ...]]]:
    """両組の適合の検査の 1 行の結果と、要る能力。"""

    def pick(_: Variant, rows: tuple[CostFitRow, ...]) -> tuple[FitOutcome, tuple[Capability, ...]]:
        (found,) = [row for row in rows if row.row is which]
        return found.outcome, tuple(found.capabilities)

    return entry.cost_model_fit.map(pick)


def _row_5(entry: AdoptionReviewEntry) -> Paired[tuple[FitOutcome, tuple[Capability, ...]]]:
    return _row(entry, CostModelRow.CURRENCY_CONVERSION)


def _rerun_finished(entry: AdoptionReviewEntry) -> bool:
    """固定した試行の再実行が両組とも最後まで走った（行 3 が `UNDETERMINED` でない）。"""
    swap = _row(entry, CostModelRow.SWAP_ROLLOVER)
    return all(outcome is not FitOutcome.UNDETERMINED for _, (outcome, _caps) in swap.items())


def _register_v2(repo: Path) -> None:
    """規則の版 2 を、人工の試行台帳の今の末尾の行の後から効く版として登録する（T7c と同じ形）。"""
    shutil.copyfile(
        REPO_ROOT / _RULES / "adoption_review_v2.yaml", repo / _RULES / "adoption_review_v2.yaml"
    )
    _, rules_digest = load_adoption_review_rules(
        repo / _RULES / "adoption_review_v2.yaml", version=2
    )
    tail = _trial_lines(repo)[-1].digest.hex
    registry = repo / _RULES / "registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8")
        + f'  - {{id: adoption_review, version: 2, digest: "{rules_digest.hex}",'
        f' effective_after: "{tail}"}}\n',
        encoding="utf-8",
    )


_NEEDS = (FitOutcome.NEEDS_CAPABILITY, (Capability.CURRENCY_CONVERSION,))
_FITS = (FitOutcome.FITS, ())


def test_ct19_version_1_needs_conversion_for_a_usd_settled_candidate(env: _Env) -> None:
    code, stdout, stderr = _review(env)
    assert code == 7, stdout + stderr
    assert "COST_MODEL_NEEDS_CAPABILITY" in stdout
    (entry,) = _reviews(env)
    assert entry.result is ReviewResult.DO_NOT_PROCEED
    assert entry.review_rules_ref.version == 1
    assert ReviewReason.COST_MODEL_NEEDS_CAPABILITY in entry.reasons
    assert _row_5(entry) == Paired(standard=_NEEDS, candidate=_NEEDS)
    assert _rerun_finished(entry)


def test_ct19_version_2_fits_a_usd_settled_candidate_started_after_it(
    env: _Env, tmp_path: Path
) -> None:
    """版 2 の登録より前に開始した実行には版 1、後に開始した実行には版 2（T7c と同じ当て方）。"""
    repo = env.repo
    _register_v2(repo)
    out = tmp_path / "out"
    shutil.copytree(env.out, out, symlinks=True)
    code, stdout, stderr = _review(env, out=out)
    assert code == 7, stdout + stderr  # 実行番号 1 は版 2 の登録より前に開始した
    _run(repo, env.experiment, out)  # 同じ版の再実行（実行番号 2）は登録より後に開始する
    _promote(repo, env.experiment_id, 2)
    _declare(repo, env.experiment_id, 2)
    code, stdout, stderr = _review(env, execution=2, out=out)
    assert code == 7, stdout + stderr  # 行 5 と関係の無い人工データの性質で進めない（冒頭の注記）
    assert "CURRENCY_CONVERSION=FITS" in stdout
    assert "CURRENCY_CONVERSION=NEEDS_CAPABILITY" not in stdout
    first, second = _reviews(env)
    assert (first.execution, first.review_rules_ref.version) == (1, 1)
    assert _row_5(first) == Paired(standard=_NEEDS, candidate=_NEEDS)
    assert (second.execution, second.review_rules_ref.version) == (2, 2)
    assert ReviewReason.COST_MODEL_NEEDS_CAPABILITY not in second.reasons
    assert _row_5(second) == Paired(standard=_FITS, candidate=_FITS)
    assert _rerun_finished(second)
