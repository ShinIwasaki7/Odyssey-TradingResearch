"""最終検証の本番の run を人工データで通す（D09 v0.6 案 §9.10.14・§9.10.11 の 1。R4b）。

T02 の人工データの作業場（`tests/fixtures/acceptance/t02_workspace.py`）で検証戦略 B の 2 試行の
探索の実験を 1 回走らせ（研究履歴の成果物）、2024 年以降の封印の区分の partition を持つ人工の
snapshot（標準・候補の 2 組。足は T02 の人工の値の表から作る。**実データではない**。D09 §9.8 の
例外）を置き、一時の bare リポジトリを origin にして、本番の最終検証の run
（`app.final_run.RecordedFinalRunner`）で段 1〜段 5 を通す。

確かめること（成績・指標の値は読まない。終了コード・理由の識別子・識別子・件数だけ）:

- 段 2 (f): 研究履歴の成果物から選定と判定を計算し直し、最終検証に進める試行を返す。成果物を
  書き換えると `RECOMPUTATION_MISMATCH`（何も書かない）。
- 段 2 (g): 組ごとの `RunConfig` は封印の並び（`sealed_reads`）を持ち、予測 `RunId` が実際の run と
  一致する（検査 P4F）。run manifest に並びが書かれる（D06 v1.18 案 §9.3）。
- 段 5: 両組の run と評価が `<out>/runs/<run_id>/` に書かれ、最終検証の記録と台帳の行が書かれる。

人工データの探索は判定が「満たす」にならないので、候補の事実（判定の値）だけを人工の
`MEETS_STANDARD` にする（`_Candidates`）。再計算は記録された結末記録と照合する（書き換えない）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from odyssey_fx.app.final_run import RecordedFinalRunner
from odyssey_fx.app.final_validation import (
    EXIT_OK,
    EXIT_REFUSED,
    CandidateFacts,
    ExperimentCandidates,
    FinalValidationOutcome,
    FinalValidationRequest,
    final_validate,
)
from odyssey_fx.app.final_validation_records import final_directory, read_final_ledger
from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.common.ids import RunId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import FileSystemResultRepository
from odyssey_fx.evaluation.domain.final_validation import FinalReason
from odyssey_fx.evaluation.domain.search import SearchVerdict
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.partition_digest import partition_digest_hex
from odyssey_fx.marketdata.domain.access import AccessClass, sealed_partition_period
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    Mt5AcquisitionRecord,
    Mt5ConversionRecord,
    PartitionId,
    PartitionRecord,
    SeriesManifest,
)
from odyssey_fx.marketdata.domain.time_rule import TimeRuleTableRef, TimeRuleVariant
from tests.fixtures.acceptance import t02_market
from tests.fixtures.acceptance.t02_workspace import T02Workspace, build_workspace, install_candidate
from tests.fixtures.evaluation.research_policies import (
    TEST_BASIS,
    append_registry_entry,
    policy_v3_text,
    write_policy,
)
from tests.fixtures.evaluation.search_experiments import TWO_FOLD_SPLIT, write_search_experiment
from tests.fixtures.final_validation import (
    ACQUISITION_START,
    T_FIX,
    window_config,
    write_fixation,
    write_review,
)
from tests.fixtures.git_origin import isolate_git, publish_repo
from tests.fixtures.synthetic import market
from tests.fixtures.synthetic import snapshots as synthetic

_SPLIT = TWO_FOLD_SPLIT.replace('train_length: "4d"', 'train_length: "2d"').replace(
    'validation_length: "4d"', 'validation_length: "2d"'
)
_SUFFICIENCY = (
    "  sufficiency:\n    classes:\n"
    '      - {name: ALL, min_train_trades_per_365d: "0", min_validation_trades_per_fold: 0,'
    " min_validation_trades_total: 1}"
)
_AXES = "    - {instance: m15_ema, parameter: period, values: [10, 20]}"
_ROOT = Path(__file__).resolve().parents[3]
#: 人工の封印の足を作る区間（[2024-01-01, T_FIX) の取引週を覆う）。
_SEALED = Interval(
    start=UtcTime.parse("2024-01-01T00:00:00Z"), end=UtcTime.parse("2025-01-05T22:00:00Z")
)


@dataclass(frozen=True, slots=True)
class _Env:
    workspace: T02Workspace
    directory: Path
    facts: CandidateFacts
    out: Path
    home: Path
    clean: Path


class _Candidates:
    def __init__(self, facts: CandidateFacts) -> None:
        self.facts = facts

    def read(self, experiment_dir: Path, execution: int) -> CandidateFacts:
        return self.facts


def _weeks() -> list[Interval]:
    out: list[Interval] = []
    start = _SEALED.start
    while True:
        period = sealed_partition_period(start)
        if period.end > _SEALED.end:
            return out
        out.append(period)
        start = period.end


def _sealed_snapshot(
    snapshots: Path, variant: TimeRuleVariant, research: dict[str, SeriesId]
) -> str:
    """T02 の人工の値の表から 2024 年以降の封印の snapshot を作る（足・manifest・検査報告）。"""
    calendar = market.calendar()
    hourly = t02_market.bars_for("1h", market.TF_1H, calendar, _SEALED)
    made: dict[str, tuple[Bar, ...]] = {
        "1h": hourly,
        "15m": t02_market.bars_for("15m", market.TF_15M, calendar, _SEALED),
        "1d_ny17": t02_market.aggregate(hourly, market.TF_1D_NY17, calendar),
    }
    store = ParquetSnapshotStore(root=snapshots)
    records: list[PartitionRecord] = []
    series_records: list[SeriesManifest] = []
    written: dict[PartitionId, tuple[Bar, ...]] = {}
    for timeframe, bars in made.items():
        series = research[timeframe]
        assert all(bar.series == series for bar in bars)
        own: list[PartitionRecord] = []
        for period in _weeks():
            chosen = tuple(bar for bar in bars if period.start <= bar.bar_start < period.end)
            if not chosen:
                continue
            access = (
                AccessClass.HOLDOUT_SINCE_2024
                if period.end <= ACQUISITION_START
                else AccessClass.FORWARD_HOLDOUT
            )
            partition = PartitionId(series=series, access_class=access, period_start=period.start)
            own.append(
                PartitionRecord(
                    partition_id=partition,
                    interval=Interval(start=chosen[0].bar_start, end=chosen[-1].bar_end),
                    bar_count=len(chosen),
                    digest=ContentDigest.sha256(partition_digest_hex(chosen)),
                )
            )
            written[partition] = chosen
        records.extend(own)
        series_records.append(
            SeriesManifest(
                series_id=series,
                covered_interval=Interval(start=own[0].interval.start, end=own[-1].interval.end),
                bar_count=sum(item.bar_count for item in own),
                partitions=tuple(item.partition_id for item in own),
            )
        )
    base = synthetic.manifest(
        partitions=tuple(records),
        series_records=tuple(series_records),
        approval=Approval(
            approved_by="acceptance-test",
            approved_at=UtcTime.parse("2026-11-20T00:00:00Z"),
            comment="人工の封印の snapshot（足つき）",
        ),
    )
    mt5 = Mt5ConversionRecord(
        route="oanda_mt5_m1",
        column_mapping="oanda_mt5_m1@1",
        fetch_manifest_sha256="a" * 64,
        time_rule_table=TimeRuleTableRef(
            table_id="oanda_mt5_time_rules_sealed",
            version=1,
            sha256="f4052b591b663042e0a20e205978a3d55171be6c90e1339f8e12de661daa03e3",
            variant=variant,
        ),
        acquisitions=(
            Mt5AcquisitionRecord(symbol=market.USDJPY, data_interval=_SEALED, acquired=(_SEALED,)),
        ),
    )
    manifest = replace(base, conversion=replace(synthetic.CONVERSION, mt5=mt5))
    snapshot_id = str(manifest.snapshot_id())
    (snapshots / snapshot_id).mkdir(parents=True, exist_ok=True)
    for partition, bars in written.items():
        store.write_partition(snapshot_id, partition, bars)
    store.write_integrity_report(snapshot_id, IntegrityReport())
    store.write_manifest(snapshot_id, manifest)
    return snapshot_id


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Env]:
    root = tmp_path_factory.mktemp("final-run")
    home = root / "githome"
    with pytest.MonkeyPatch.context() as patch:
        isolate_git(patch, home)
        workspace = build_workspace(root)
        candidate = install_candidate(workspace, root / "candidate")
        basis = TEST_BASIS.replace("1" * 64, workspace.snapshot_id).replace("2" * 64, candidate)
        text = policy_v3_text(
            version=3,
            replace={
                "split": _SPLIT,
                "purpose": "  purpose: STANDARD",
                "basis": basis,
                "sufficiency": _SUFFICIENCY,
            },
        )
        repo = workspace.repo
        write_policy(repo, "research_policy", 3, text)
        append_registry_entry(repo, "research_policy", 3)
        (repo / "research").mkdir(exist_ok=True)
        (repo / "research" / "trial_ledger.jsonl").write_bytes(b"")
        experiment = write_search_experiment(
            repo,
            "final_case.yaml",
            experiment_id="final_case",
            axes=_AXES,
            final_holdout=(
                '{interval: {start: "2024-01-01T00:00:00Z", end: "2030-01-01T00:00:00Z"},'
                ' purpose: "人工の候補を封印期間で 1 回だけ確かめる"}'
            ),
        )
        out = root / "out"
        from odyssey_fx.app.cli.main import main

        assert (
            main(
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
            == 0
        )
        directory = out / "runs/experiments/final_case/v1"
        recorded = ExperimentCandidates(repo).read(directory, 1)
        facts = replace(
            recorded,
            completed=True,
            verdicts=Paired(
                standard=SearchVerdict.MEETS_STANDARD, candidate=SearchVerdict.MEETS_STANDARD
            ),
            combined=SearchVerdict.MEETS_STANDARD,
        )
        trials = Paired.build(lambda variant: _int(recorded.final_trials.of(variant)))
        snapshots = repo / "data" / "snapshots"
        research = {
            item.series_id.timeframe.id: item.series_id
            for item in ParquetSnapshotStore(root=snapshots)
            .read_manifest(workspace.snapshot_id)
            .series
        }
        for variant in (TimeRuleVariant.STANDARD, TimeRuleVariant.CANDIDATE):
            _sealed_snapshot(snapshots, variant, research)
        for name in ("final_validations.jsonl", "quality_access_log.jsonl"):
            (repo / "research" / name).write_bytes(b"")
        window_config(repo)
        shutil.copytree(
            _ROOT / "docs/design/time_rules",
            repo / "docs/design/time_rules",
            dirs_exist_ok=True,
        )
        write_review(repo, [facts.key], trials=trials)
        write_fixation(repo, facts.key, trials=trials)
        declaration = repo / "research" / "declarations" / "final_case.yaml"
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text(
            "schema_version: 1\n"
            f"experiment_id: {facts.key.experiment_id}\n"
            "execution: 1\n"
            "used_outside_viewing: false\n"
            "period:\n"
            f'  start: "{ACQUISITION_START}"\n'
            f'  end: "{T_FIX}"\n'
            'question: "研究の外で見た相場を候補の選定・調整に使ったか"\n'
            'answer: "使っていない"\n'
            'recorded_by: "人工の記録者"\n',
            encoding="utf-8",
        )
        publish_repo(repo, root / "origin.git", home=home)
        clean = root / "clean_out"
        shutil.copytree(out, clean)
        yield _Env(
            workspace=workspace, directory=directory, facts=facts, out=out, home=home, clean=clean
        )


def _int(value: int | None) -> int:
    assert value is not None
    return value


def _validate(env: _Env, out_root: Path, directory: Path | None = None) -> FinalValidationOutcome:
    repo = env.workspace.repo
    with pytest.MonkeyPatch.context() as patch:
        isolate_git(patch, env.home)
        target = directory or env.directory
        return final_validate(
            FinalValidationRequest(
                experiment_dir=target,
                execution=1,
                repo_root=repo,
                snapshots_root=repo / "data" / "snapshots",
                out_root=out_root,
                declaration=repo / "research" / "declarations" / "final_case.yaml",
                by="人工の実行者",
            ),
            candidates=_Candidates(env.facts),
            runner=RecordedFinalRunner(
                repo_root=repo, snapshots_root=repo / "data" / "snapshots", out_root=out_root
            ),
        )


def test_a_tampered_research_record_stops_the_recomputation_before_consumption(
    env: _Env, tmp_path: Path
) -> None:
    """段 2 (f): 選定記録を書き換えると再計算が食い違い、何も書かずに止まる。"""
    copy = tmp_path / "out"
    shutil.copytree(env.clean, copy)
    directory = copy / "runs/experiments/final_case/v1"
    selection = next((directory / "search").rglob("selection_f0.json"))
    payload = json.loads(selection.read_text())
    payload["selected_value"] = None
    payload["selected_trial_index"] = None
    payload["selected_train_trade_count"] = None
    selection.write_text(json.dumps(payload))
    outcome = _validate(env, tmp_path / "final_out", directory)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.RECOMPUTATION_MISMATCH,
    ), outcome.detail
    assert not final_directory(directory, 1).exists()


def test_a_wrong_digest_on_a_record_that_shares_its_run_is_caught(
    env: _Env, tmp_path: Path
) -> None:
    """同じ run を共有する 2 つの試行記録の後の方だけの結果のダイジェストの誤りも見つける
    （評価し直した結果を使い回しても、照合は記録ごとに行う。Codex 1 巡目）。"""
    copy = tmp_path / "out"
    shutil.copytree(env.clean, copy)
    directory = copy / "runs/experiments/final_case/v1"
    units = directory / "search" / "standard" / "units"
    shared: dict[str, list[tuple[tuple[int, int, int], Path]]] = {}
    for path in units.glob("*.json"):
        if path.name.endswith(".start.json"):
            continue
        fold, phase, trial = path.stem.split("_")
        order = (int(fold[1:]), 0 if phase == "TRAIN" else 1, int(trial[1:]))
        run = json.loads(path.read_text())["run_id"]
        shared.setdefault(json.dumps(run, sort_keys=True), []).append((order, path))
    groups = [sorted(items) for items in shared.values() if len(items) > 1]
    assert groups, "the synthetic search has no shared run"
    later = groups[0][-1][1]
    payload = json.loads(later.read_text())
    payload["result_digest"]["hex"] = "0" * 64
    later.write_text(json.dumps(payload))
    outcome = _validate(env, tmp_path / "final_out", directory)
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.RECOMPUTATION_MISMATCH,
    ), outcome.detail
    assert not final_directory(directory, 1).exists()


def test_1_the_production_run_validates_with_the_sealed_reads(env: _Env, tmp_path: Path) -> None:
    """受入テスト 1 を本番の run で: 消費・両組の run と評価・記録・台帳。予測 RunId が一致する。"""
    final_out = tmp_path / "final_out"
    outcome = _validate(env, final_out)
    assert outcome.exit_code == EXIT_OK, (outcome.detail, outcome.lines)
    record = json.loads((final_directory(env.directory, 1) / "final_evaluation.json").read_text())
    permit = json.loads((final_directory(env.directory, 1) / "permit.json").read_text())
    repository = FileSystemResultRepository(root=final_out)
    for variant in VARIANTS:
        block = record["variants"][variant.value]
        assert block["run_id"] == permit["predicted"][variant.value]["run_id"]
        manifest = repository.read_manifest(RunId(ContentDigest.sha256(block["run_id"])))
        assert isinstance(manifest, RunManifest), manifest
        reads = manifest.config.sealed_reads
        assert reads and len(reads) == len(permit["reads"][variant.value])
        assert {f"{item.snapshot_id}:{item.partition_id}" for item in reads} == set(
            permit["reads"][variant.value]
        )
        raw = json.loads((final_out / "runs" / block["run_id"] / "manifest.json").read_text())
        assert "sealed_reads" in json.loads(raw["config"])
    assert [row.result.value for row in read_final_ledger(env.workspace.repo).rows] == ["COMPLETED"]


# --- CT-22・CT-23・CT-25・CT-27: 円以外の決済通貨の候補の本番の run（D06 §19.14） ---------------
#
# EURUSD（決済通貨は米ドル）の探索の実験を、研究期間の OANDA MT5 の経路の人工の snapshot（EURUSD
# と、換算の snapshot の USDJPY。両組）で 1 回走らせ、封印の人工の snapshot（EURUSD と USDJPY の
# 1 分足。両組）を置く（`tests/fixtures/final_conversion.py`。**人工データ**）。作業場は 1 回だけ
# 作り、各テストは写しで動かす（写しの origin を指し直す）。

_CONV_COPIED = (
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v2.yaml",
    "configs/symbols/EURUSD.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/policies/research/research_policy_v1.yaml",
)
_CONV_EXPERIMENT = "configs/experiments/final_conversion.yaml"
#: 換算の snapshot（USDJPY）の研究履歴の足の始まり（EURUSD の 1 時間足の始まりより 9 日前。
#: CT-27 のカレンダーの食い違いを換算の snapshot の期間にだけ置く）。
_CONV_RESEARCH_START = UtcTime.parse("2014-09-21T00:00:00Z")


@dataclass(frozen=True, slots=True)
class _ConvEnv:
    base: Path
    facts: CandidateFacts
    research: dict[str, Paired[str]]
    sealed: dict[str, Paired[str]]


@dataclass(frozen=True, slots=True)
class _ConvCopy:
    root: Path
    facts: CandidateFacts

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def home(self) -> Path:
        return self.root / "githome"

    @property
    def snapshots(self) -> Path:
        return self.repo / "data" / "snapshots"

    @property
    def directory(self) -> Path:
        return self.root / "out/runs/experiments/final_conv/v1"

    def git(self, *args: str) -> str:
        from tests.fixtures.git_origin import git

        return git(self.repo, *args, home=self.home)

    def commit_and_push(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)
        self.git("push", "--quiet", "origin", "main")

    def origin_head(self) -> str:
        return self.git("ls-remote", "origin", "refs/heads/main").split()[0]


def _sealed_set(
    snapshots: Path, *, calendar_version: int = 1, fetch: Paired[str] | None = None
) -> dict[str, Paired[str]]:
    from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant as _Variant
    from tests.fixtures import final_conversion as fc

    calendar = market.calendar()
    eur = fc.eurusd_bars(fc.SEALED, fc.SEALED)
    jpy = {"1m": fc.quarter_minutes(fc.USDJPY, fc.SEALED, fc.RATE, calendar)}
    variants = Paired(standard=_Variant.STANDARD, candidate=_Variant.CANDIDATE)
    return {
        "EURUSD": variants.map(
            lambda _, variant: fc.sealed_snapshot(
                snapshots,
                eur,
                variant,
                fc.EURUSD,
                acquisition_start=ACQUISITION_START,
                calendar_version=calendar_version,
            )
        ),
        "USDJPY": variants.map(
            lambda name, variant: fc.sealed_snapshot(
                snapshots,
                jpy,
                variant,
                fc.USDJPY,
                acquisition_start=ACQUISITION_START,
                calendar_version=calendar_version,
                fetch_sha=fc.FETCH_SHA if fetch is None else fetch.of(name),
            )
        ),
    }


@pytest.fixture(scope="module")
def conv_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_ConvEnv]:
    from odyssey_fx.app.cli.main import main
    from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant as _Variant
    from tests.fixtures import final_conversion as fc
    from tests.fixtures.evaluation.research_policies import TEST_COMPARISON_BASIS
    from tests.fixtures.evaluation.search_experiments import BASE_EXPERIMENT, search_experiment_text

    base = tmp_path_factory.mktemp("final-conv")
    home = base / "githome"
    repo = base / "repo"
    with pytest.MonkeyPatch.context() as patch:
        isolate_git(patch, home)
        for name in _CONV_COPIED:
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((_ROOT / name).read_bytes())
        (repo / "uv.lock").write_text("# synthetic uv.lock (conversion final run)\n")
        shutil.copytree(_ROOT / "docs/design/time_rules", repo / "docs/design/time_rules")
        append_registry_entry(repo, "research_policy", 1)
        strategy = repo / "configs/strategies/strategy_b_eurusd.yaml"
        strategy.parent.mkdir(parents=True, exist_ok=True)
        strategy.write_text(
            (_ROOT / "configs/strategies/strategy_b_v1.yaml")
            .read_text(encoding="utf-8")
            .replace("USDJPY/", "EURUSD/"),
            encoding="utf-8",
        )
        snapshots = repo / "data" / "snapshots"
        calendar = market.calendar()
        eur = fc.eurusd_bars(
            t02_market.GENERATION_INTERVAL,
            Interval(start=UtcTime.parse("2014-12-28T22:00:00Z"), end=t02_market.RUN_INTERVAL.end),
            minutes=Interval(
                start=UtcTime.parse("2014-12-28T22:00:00Z"), end=t02_market.RUN_INTERVAL.end
            ),
        )
        jpy = {
            "1m": fc.quarter_minutes(
                fc.USDJPY,
                Interval(start=_CONV_RESEARCH_START, end=t02_market.RUN_INTERVAL.end),
                fc.RATE,
                calendar,
            )
        }
        variants = Paired(standard=_Variant.STANDARD, candidate=_Variant.CANDIDATE)
        research = {
            "EURUSD": variants.map(
                lambda _, variant: fc.research_snapshot(snapshots, eur, variant, fc.EURUSD)
            ),
            "USDJPY": variants.map(
                lambda _, variant: fc.research_snapshot(snapshots, jpy, variant, fc.USDJPY)
            ),
        }
        basis = (
            TEST_BASIS.replace("1" * 64, research["EURUSD"].standard)
            .replace("2" * 64, research["EURUSD"].candidate)
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
        for name in ("trial_ledger.jsonl", "final_validations.jsonl", "quality_access_log.jsonl"):
            (repo / "research" / name).write_bytes(b"")
        single = (
            (_ROOT / BASE_EXPERIMENT)
            .read_text(encoding="utf-8")
            .replace("0" * 64, research["EURUSD"].standard)
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
            experiment_id="final_conv",
            axes=_AXES,
            final_holdout=(
                '{interval: {start: "2024-01-01T00:00:00Z", end: "2030-01-01T00:00:00Z"},'
                ' purpose: "人工の円以外の候補を封印期間で 1 回だけ確かめる"}'
            ),
            candidate_snapshot=research["EURUSD"].candidate,
        )
        marker = f'  candidate: "{research["EURUSD"].candidate}"\n'
        assert searched.count(marker) == 1
        searched = searched.replace(
            marker,
            marker
            + "conversion_snapshots:\n"
            + f'  standard: "{research["USDJPY"].standard}"\n'
            + f'  candidate: "{research["USDJPY"].candidate}"\n',
        )
        experiment = repo / _CONV_EXPERIMENT
        experiment.parent.mkdir(parents=True, exist_ok=True)
        experiment.write_text(searched, encoding="utf-8")
        out = base / "out"
        code = main(
            [
                "experiment",
                "run",
                "--experiment",
                str(experiment),
                "--snapshots",
                str(snapshots),
                "--out",
                str(out),
                "--repo-root",
                str(repo),
            ]
        )
        assert code == 0
        directory = out / "runs/experiments/final_conv/v1"
        recorded = ExperimentCandidates(repo).read(directory, 1)
        assert recorded.conversion_snapshots == research["USDJPY"]
        assert recorded.conversion_symbols == frozenset({"USDJPY"})
        facts = replace(
            recorded,
            completed=True,
            verdicts=Paired(
                standard=SearchVerdict.MEETS_STANDARD, candidate=SearchVerdict.MEETS_STANDARD
            ),
            combined=SearchVerdict.MEETS_STANDARD,
        )
        trials = Paired.build(lambda variant: _int(recorded.final_trials.of(variant)))
        sealed = _sealed_set(snapshots)
        window_config(repo)
        write_review(repo, [facts.key], trials=trials)
        write_fixation(repo, facts.key, trials=trials)
        declaration = repo / "research" / "declarations" / "final_conv.yaml"
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text(
            "schema_version: 1\n"
            f"experiment_id: {facts.key.experiment_id}\n"
            "execution: 1\n"
            "used_outside_viewing: false\n"
            "period:\n"
            f'  start: "{ACQUISITION_START}"\n'
            f'  end: "{T_FIX}"\n'
            'question: "研究の外で見た相場を候補の選定・調整に使ったか"\n'
            'answer: "使っていない"\n'
            'recorded_by: "人工の記録者"\n',
            encoding="utf-8",
        )
        publish_repo(repo, base / "origin.git", home=home)
    yield _ConvEnv(base=base, facts=facts, research=research, sealed=sealed)


def _conv_copy(env: _ConvEnv, tmp_path: Path) -> _ConvCopy:
    """作業場を写し、写しの作業木が写しの origin を指すようにする。"""
    root = tmp_path / "copy"
    shutil.copytree(env.base, root, symlinks=True)
    copy = _ConvCopy(root=root, facts=env.facts)
    copy.git("remote", "set-url", "origin", str(root / "origin.git"))
    return copy


def _conv_validate(copy: _ConvCopy, out_root: Path) -> FinalValidationOutcome:
    with pytest.MonkeyPatch.context() as patch:
        isolate_git(patch, copy.home)
        return final_validate(
            FinalValidationRequest(
                experiment_dir=copy.directory,
                execution=1,
                repo_root=copy.repo,
                snapshots_root=copy.snapshots,
                out_root=out_root,
                declaration=copy.repo / "research" / "declarations" / "final_conv.yaml",
                by="人工の実行者",
            ),
            candidates=_Candidates(copy.facts),
            runner=RecordedFinalRunner(
                repo_root=copy.repo, snapshots_root=copy.snapshots, out_root=out_root
            ),
        )


def test_ct22_ct23_the_sealed_reads_carry_the_usdjpy_leg_and_the_prediction_matches(
    conv_env: _ConvEnv, tmp_path: Path
) -> None:
    """CT-22: その組の `RunConfig` の並びに EURUSD と USDJPY の partition が入り、換算の snapshot は
    記録票のその組の研究期間の USDJPY。予測 `RunId` が実際の run と一致する（P4F）。CT-23 の本番の
    run の側: 換算用の読み取りの行・控え・台帳の別の項目、状態は SEALED のまま。"""
    from odyssey_fx.app.sealed_intake import parse_consumption_bytes
    from odyssey_fx.marketdata.domain.consumption_log import ParsedAccessLog

    copy = _conv_copy(conv_env, tmp_path)
    final_out = tmp_path / "final_out"
    outcome = _conv_validate(copy, final_out)
    assert outcome.exit_code == EXIT_OK, (outcome.detail, outcome.lines)
    final = final_directory(copy.directory, 1)
    record = json.loads((final / "final_evaluation.json").read_text())
    permit = json.loads((final / "permit.json").read_text())
    repository = FileSystemResultRepository(root=final_out)
    for variant in VARIANTS:
        block = record["variants"][variant.value]
        assert block["run_id"] == permit["predicted"][variant.value]["run_id"]
        manifest = repository.read_manifest(RunId(ContentDigest.sha256(block["run_id"])))
        assert isinstance(manifest, RunManifest), manifest
        config = manifest.config
        assert config.conversion_snapshot_ref is not None
        assert str(config.conversion_snapshot_ref.snapshot_id) == conv_env.research["USDJPY"].of(
            variant
        )
        legs = [
            item for item in config.sealed_reads if str(item.partition_id.series.symbol) == "USDJPY"
        ]
        assert legs and {str(item.partition_id.series) for item in legs} == {"USDJPY/1m/bid"}
        assert {item.snapshot_id for item in legs} == {conv_env.sealed["USDJPY"].of(variant)}
        executed = [item for item in config.sealed_reads if item not in legs]
        assert {str(item.partition_id.series.symbol) for item in executed} == {"EURUSD"}
        assert {f"{item.snapshot_id}:{item.partition_id}" for item in config.sealed_reads} == set(
            permit["reads"][variant.value]
        )
    assert not any("USDJPY" in name for name in permit["targets"])
    assert len(permit["conversion_reads"]) == permit["conversion_read_count"] > 0
    store = ParquetSnapshotStore(root=copy.snapshots)
    for _, name in conv_env.sealed["USDJPY"].items():
        data = (copy.snapshots / name / "access_log.jsonl").read_bytes()
        parsed = parse_consumption_bytes(data, store.read_manifest(name))
        assert isinstance(parsed, ParsedAccessLog)
        assert parsed.consumed() == frozenset()  # SEALED のまま
        assert {row.kind.value for row in parsed.rows} == {"CONVERSION_READ"}
    (row,) = read_final_ledger(copy.repo).rows
    assert row.payload["schema_version"] == 2
    assert row.payload["conversion_read_count"] == len(permit["conversion_reads"])


def test_ct25_a_usdjpy_partition_of_another_acquisition_round_stops_before_consumption(
    conv_env: _ConvEnv, tmp_path: Path
) -> None:
    """CT-25（仮置き C1-14）: 同じ週の USDJPY の partition の取得の回が執行の通貨ペアの partition と
    違えば、何も書かずに消費の前に止まる（終了コード 7）。"""
    from tests.fixtures import final_conversion as fc

    copy = _conv_copy(conv_env, tmp_path)
    for _, name in conv_env.sealed["USDJPY"].items():
        shutil.rmtree(copy.snapshots / name)
    calendar = market.calendar()
    jpy = {"1m": fc.quarter_minutes(fc.USDJPY, fc.SEALED, fc.RATE, calendar)}
    from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant as _Variant

    for variant in (_Variant.STANDARD, _Variant.CANDIDATE):
        fc.sealed_snapshot(
            copy.snapshots,
            jpy,
            variant,
            fc.USDJPY,
            acquisition_start=ACQUISITION_START,
            fetch_sha="b" * 64,
        )
    copy.commit_and_push("the USDJPY sealed snapshots of another acquisition round")
    head = copy.origin_head()
    outcome = _conv_validate(copy, tmp_path / "final_out")
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.CONVERSION_PARTITION_MISMATCH,
    ), outcome.detail
    assert "acquisition round" in outcome.detail
    assert not final_directory(copy.directory, 1).exists()
    assert copy.origin_head() == head


def test_ct27_the_research_calendar_check_reaches_the_conversion_snapshot(
    conv_env: _ConvEnv, tmp_path: Path
) -> None:
    """CT-27（人間の決定 (22) (a)）: run のカレンダー（版 2）の研究履歴の期間の取引セッションが、
    換算の snapshot の期間でだけ記録票のカレンダー（版 1）と違えば、段 2 (g) で何も書かずに止まる
    （終了コード 7）。同じなら合格する（CT-22 の run）。"""
    copy = _conv_copy(conv_env, tmp_path)
    for paired in conv_env.sealed.values():
        for _, name in paired.items():
            shutil.rmtree(copy.snapshots / name)
    text = (copy.repo / "configs/calendars/fx_ny17_v1.yaml").read_text(encoding="utf-8")
    assert "\nversion: 1\n" in text and "closures: []\n" in text
    (copy.repo / "configs/calendars/fx_ny17_v2.yaml").write_text(
        text.replace("\nversion: 1\n", "\nversion: 2\n", 1).replace(
            "closures: []\n",
            "closures:\n"
            '  - local_date: "2014-09-24"\n'
            "    covers_trading_day: true\n"
            '    note: "人工の休場（換算の snapshot の研究履歴の期間だけにある食い違い）"\n',
        ),
        encoding="utf-8",
    )
    _sealed_set(copy.snapshots, calendar_version=2)
    copy.commit_and_push("sealed snapshots on an artificial calendar v2")
    head = copy.origin_head()
    outcome = _conv_validate(copy, tmp_path / "final_out")
    assert (outcome.exit_code, outcome.reason) == (
        EXIT_REFUSED,
        FinalReason.DATA_CAPABILITY_NOT_MET,
    ), outcome.detail
    assert "conversion snapshot of the record" in outcome.detail, outcome.detail
    assert not final_directory(copy.directory, 1).exists()
    assert copy.origin_head() == head
