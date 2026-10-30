"""最終検証の受入テストの人工の作業場（D09 §9.10.11。人工データだけ）。

- **封印の snapshot**: OANDA MT5 の経路の形の manifest だけを人工に作る（足のファイルは書かない。
  段 1〜段 4 は partition の足を読まない。段 5 の run は人工の `FinalRunner` が受け持つ）。
  partition の期間は取引週（ADR-0038 第2項）、区分は人工の取得の開始時点で 2 つに分かれる。
  人工データの封印の partition は D09 §9.8（D08 §9.5 の例外。D09 §16.4 の 7 で 2 つの封印の区分にも
  当てる）。
- **origin**: 一時ディレクトリの bare リポジトリ（実際の origin に触れない。D09 §9.8）。
- **候補**: 人工の候補の事実（`CandidateFacts`）、試行台帳の 2 行、採用審査の `PROCEED` の行、
  候補の固定の記録、申告のファイル。

価格・成績の値は持たない（指標は人工の値で、出力と記録に価格が現れないことを確かめる）。
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from odyssey_fx.app.final_validation import (
    CandidateFacts,
    FinalRunOutput,
    FinalRunPlan,
    FinalRunPreparation,
)
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.review_store import review_line_bytes
from odyssey_fx.evaluation.domain.adoption_review import FixedTrial, ReviewResult
from odyssey_fx.evaluation.domain.final_validation import (
    CandidateKey,
    FinalEvaluationPermit,
    FinalRunSummary,
    FinalWindow,
    PredictedRun,
    WindowRefusal,
)
from odyssey_fx.evaluation.domain.metrics import MetricId, RatioValue
from odyssey_fx.evaluation.domain.search import SearchVerdict, StandardPurpose
from odyssey_fx.evaluation.domain.splits import FinalHoldoutSpec
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired, Variant
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.partition_digest import partition_digest_hex
from odyssey_fx.marketdata.domain.access import (
    AccessClass,
    acquisition_start_after,
    sealed_partition_period,
)
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    ConversionRecord,
    Mt5AcquisitionRecord,
    Mt5ConversionRecord,
    PartitionId,
    PartitionRecord,
    SeriesManifest,
    SnapshotManifest,
)
from odyssey_fx.marketdata.domain.time_rule import TimeRuleTableRef, TimeRuleVariant
from tests.fixtures.evaluation import trial_ledger
from tests.fixtures.evaluation.adoption_review import review_entry, review_lines
from tests.fixtures.evaluation.search_units import standard as evaluation_standard
from tests.fixtures.git_origin import git, publish_repo
from tests.fixtures.synthetic import market
from tests.fixtures.synthetic import snapshots as synthetic

__all__ = [
    "ACQUISITION_START",
    "window_config",
    "T_FIX",
    "FakeRunner",
    "FinalWorkspace",
    "build_workspace",
]

_ROOT: Final = Path(__file__).resolve().parents[2]

#: 人工の取得の開始時点（ニューヨークの日曜 17:00。夏時間なので 21:00Z）と、それを導く人工の
#: merge の時刻（ADR-0037 第2項の 2 の規則）。実際の値とは別（窓の設定はテストの中で書く）。
MERGE_TIME: Final = UtcTime.parse("2024-05-29T00:00:00Z")
ACQUISITION_START: Final = acquisition_start_after(MERGE_TIME)
#: 人工の候補の固定時点。窓は [2024-01-01, T_FIX) の中の取引週。
T_FIX: Final = UtcTime.parse("2025-01-10T00:00:00Z")
#: 人工の封印の partition を作る終わり（T_FIX の後の数週も作る。窓に入らないことを確かめる）。
GENERATION_END: Final = UtcTime.parse("2025-02-09T22:00:00Z")
#: 人工の封印の系列（1 時間足だけ）。
SERIES: Final = market.series()
#: 同じ銘柄の別の時間足（一緒に消費する。要決定 R2-2 (a)）。
SERIES_D1: Final = market.series(timeframe_id="1d")
#: 封印の snapshot の時刻の規則の表（承認済みの一覧の値。設定ファイルの値で、価格を含まない）。
_SEALED_TABLE_ID: Final = "oanda_mt5_time_rules_sealed"
_SEALED_TABLE_SHA: Final = "f4052b591b663042e0a20e205978a3d55171be6c90e1339f8e12de661daa03e3"
#: 人工の指標（価格ではない比率。出力に現れないことを確かめる印）。
NET_RETURN: Final = Decimal("0.0731")
DRAWDOWN: Final = Decimal("0.1234")


def _class_of(period: Interval) -> AccessClass:
    return (
        AccessClass.HOLDOUT_SINCE_2024
        if period.end <= ACQUISITION_START
        else AccessClass.FORWARD_HOLDOUT
    )


def weeks(until: UtcTime = GENERATION_END) -> list[Interval]:
    """2024-01-01 から `until` までの封印の partition の期間（取引週）。"""
    out: list[Interval] = []
    start = UtcTime.parse("2024-01-01T00:00:00Z")
    while True:
        period = sealed_partition_period(start)
        if period.end > until:
            return out
        out.append(period)
        start = period.end


def sealed_manifest(
    variant: TimeRuleVariant,
    periods: Sequence[Interval],
    *,
    approved_at: str = "2026-11-20T00:00:00Z",
    digest_seed: str = "",
    series: Sequence[SeriesId] = (),
) -> SnapshotManifest:
    """人工の OANDA MT5 の経路の封印の snapshot の manifest（承認済み。既定は 1 時間足と日足）。"""
    chosen = tuple(series) or (SERIES, SERIES_D1)
    digest = (
        ContentDigest.sha256(partition_digest_hex(()))
        if not digest_seed
        else ContentDigest.sha256(f"{digest_seed:0>64}"[:64])
    )
    covered = Interval(start=periods[0].start, end=periods[-1].end)
    records: list[PartitionRecord] = []
    series_records: list[SeriesManifest] = []
    for item in chosen:
        own = tuple(
            PartitionRecord(
                partition_id=PartitionId(
                    series=item, access_class=_class_of(period), period_start=period.start
                ),
                interval=period,
                bar_count=0,
                digest=digest,
            )
            for period in periods
        )
        records.extend(own)
        series_records.append(
            SeriesManifest(
                series_id=item,
                covered_interval=covered,
                bar_count=0,
                partitions=tuple(record.partition_id for record in own),
            )
        )
    base = synthetic.manifest(
        partitions=tuple(records),
        series_records=tuple(series_records),
        approval=Approval(
            approved_by="acceptance-test",
            approved_at=UtcTime.parse(approved_at),
            comment="人工の封印の snapshot",
        ),
    )
    mt5 = Mt5ConversionRecord(
        route="oanda_mt5_m1",
        column_mapping="oanda_mt5_m1@1",
        fetch_manifest_sha256="a" * 64,
        time_rule_table=TimeRuleTableRef(
            table_id=_SEALED_TABLE_ID, version=1, sha256=_SEALED_TABLE_SHA, variant=variant
        ),
        acquisitions=(
            Mt5AcquisitionRecord(
                symbol=chosen[0].symbol, data_interval=covered, acquired=(covered,)
            ),
        ),
    )
    conversion: ConversionRecord = replace(synthetic.CONVERSION, mt5=mt5)
    return replace(base, conversion=conversion)


def write_snapshot(snapshots_root: Path, manifest: SnapshotManifest) -> str:
    """manifest と空の検査報告を確定済みの snapshot のディレクトリに書き、識別子を返す。"""
    store = ParquetSnapshotStore(root=snapshots_root)
    snapshot_id = str(manifest.snapshot_id())
    (snapshots_root / snapshot_id).mkdir(parents=True, exist_ok=True)
    store.write_integrity_report(snapshot_id, IntegrityReport())
    store.write_manifest(snapshot_id, manifest)
    return snapshot_id


@dataclass
class FakeRunner:
    """人工の最終検証の run（段 2 (f)(g) と段 5）。価格を読まず、人工の指標を返す。"""

    research: Paired[str]
    final_trials: Paired[int] = field(default_factory=lambda: Paired(standard=2, candidate=2))
    warmup: timedelta = timedelta(days=3)
    fail_run: bool = False
    extra_allowed: tuple[tuple[str, PartitionId], ...] = ()
    calls: list[str] = field(default_factory=list)
    trade_count: int = 40
    #: 予測の `RunId` に足す文字（入力が変わったやり直しを作る。仮置き R2-22）。
    run_suffix: str = ""
    #: run の成果物の置き場（`plan.run_directories`。やり直しの条件 R2-23・R2-24 の確かめに使う）。
    run_root: Path | None = None

    def prepare(self, facts: CandidateFacts, experiment_dir: Path) -> FinalRunPreparation:
        self.calls.append("prepare")
        return FinalRunPreparation(final_trials=self.final_trials, warmup=self.warmup)

    def _research_partitions(self, variant: Variant) -> tuple[tuple[str, PartitionId], ...]:
        return (
            (
                self.research.of(variant),
                PartitionId(series=SERIES, access_class=AccessClass.RESEARCH_HISTORY),
            ),
        )

    def plan(
        self, facts: CandidateFacts, window: FinalWindow, preparation: FinalRunPreparation
    ) -> FinalRunPlan | WindowRefusal:
        self.calls.append("plan")
        research = Paired.build(self._research_partitions)
        return FinalRunPlan(
            predicted=Paired.build(
                lambda variant: PredictedRun(
                    config_digest=f"config-{variant.value}",
                    run_id=f"run-{variant.value}{self.run_suffix}",
                )
            ),
            research_partitions=research,
            allowed=Paired.build(
                lambda variant: frozenset(
                    {
                        *research.of(variant),
                        *(
                            (item.snapshot_id, item.partition_id)
                            for item in window.reads.of(variant)
                        ),
                        *self.extra_allowed,
                    }
                )
            ),
            run_directories=()
            if self.run_root is None
            else tuple(
                self.run_root / f"run-{variant.value}{self.run_suffix}" for variant in VARIANTS
            ),
        )

    def run(
        self,
        facts: CandidateFacts,
        plan: FinalRunPlan,
        permit: FinalEvaluationPermit,
        variant: Variant,
    ) -> FinalRunOutput:
        self.calls.append(f"run:{variant.value}")
        if self.fail_run:
            raise RuntimeError("synthetic failure of the final-validation run")
        predicted = permit.predicted.of(variant)
        return FinalRunOutput(
            run_id=predicted.run_id,
            run_evaluation_id=f"eval-{variant.value}",
            result_digest=f"result-{variant.value}",
            config_digest=predicted.config_digest,
            metric_set_version=facts.metric_set_version,
            summary=FinalRunSummary(
                metrics=(
                    (MetricId.NET_RETURN_RATE, RatioValue(NET_RETURN)),
                    (MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, RatioValue(DRAWDOWN)),
                ),
                trade_count=self.trade_count,
            ),
        )


@dataclass
class FinalWorkspace:
    """人工の作業場（一時のリポジトリと origin と人工の候補）。"""

    repo: Path
    origin: Path
    home: Path
    experiment_dir: Path
    sealed: Paired[str]
    research: Paired[str]
    key: CandidateKey

    @property
    def snapshots(self) -> Path:
        return self.repo / "data" / "snapshots"

    def facts(self, **changes: object) -> CandidateFacts:
        base = CandidateFacts(
            key=self.key,
            completed=True,
            verdicts=Paired(
                standard=SearchVerdict.MEETS_STANDARD, candidate=SearchVerdict.MEETS_STANDARD
            ),
            combined=SearchVerdict.MEETS_STANDARD,
            policy_id="research_policy",
            policy_version=3,
            current_policy_version=3,
            purpose=StandardPurpose.STANDARD,
            final_holdout=FinalHoldoutSpec(
                interval=Interval(
                    start=UtcTime.parse("2024-01-01T00:00:00Z"),
                    end=UtcTime.parse("2030-01-01T00:00:00Z"),
                ),
                purpose="人工の候補を封印期間で 1 回だけ確かめる",
            ),
            symbols=frozenset({"USDJPY"}),
            started_line_digest=self.started_digest,
            standard=evaluation_standard(),
            requirements=Paired.build(lambda variant: evaluation_standard().sufficiency.classes[0]),
            final_trials=Paired(standard=2, candidate=2),
            metric_set_version=3,
            record_snapshots=self.research,
            manifest_sha256="m" * 64,
            outcome_sha256="o" * 64,
        )
        return replace(base, **changes)  # type: ignore[arg-type]

    started_digest: str = ""

    def git(self, *args: str) -> str:
        return git(self.repo, *args, home=self.home)

    def commit_and_push(self, message: str = "change") -> None:
        self.git("add", "-A")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        self.git("push", "--quiet", "origin", "main")

    def declaration(
        self,
        *,
        used: bool = False,
        start: str | None = None,
        end: str | None = None,
        key: CandidateKey | None = None,
    ) -> Path:
        target = key or self.key
        path = (
            self.repo
            / "research"
            / "declarations"
            / f"{target.experiment_id}_{target.execution}.yaml"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "schema_version: 1\n"
            f"experiment_id: {target.experiment_id}\n"
            f"execution: {target.execution}\n"
            f"used_outside_viewing: {'true' if used else 'false'}\n"
            "period:\n"
            f'  start: "{start or str(ACQUISITION_START)}"\n'
            f'  end: "{end or str(T_FIX)}"\n'
            'question: "研究の外で見た相場を候補の選定・調整に使ったか"\n'
            f'answer: "{"使った" if used else "使っていない"}"\n'
            'recorded_by: "人工の記録者"\n',
            encoding="utf-8",
        )
        return path


def window_config(repo: Path) -> None:
    path = repo / "configs" / "acquisition" / "oanda_mt5_m1_sealed_window.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 1\n"
        "decision: ADR-0037\n"
        "datasource: oanda_mt5_m1\n"
        "price_basis: bid\n"
        "timeframe: M1\n"
        "symbols: [USDJPY]\n"
        'allowed_start: "2024-01-01T00:00:00Z"\n'
        f'acquisition_start: "{ACQUISITION_START}"\n'
        "acquisition_start_basis:\n"
        f"  merge_commit: {'b' * 40}\n"
        f'  merge_commit_time: "{MERGE_TIME}"\n',
        encoding="utf-8",
    )


def _fixed(trials: Paired[int] | None, variant: Variant) -> FixedTrial:
    index = 2 if trials is None else trials.of(variant)
    return FixedTrial(trial_index=index, assignment_digest=ContentDigest.sha256("d" * 64))


def write_review(
    repo: Path,
    keys: Sequence[CandidateKey],
    *,
    result: str = "PROCEED",
    candidate_fixed: bool = True,
    trials: Paired[int] | None = None,
) -> None:
    """採用審査の台帳（R3 の形。行の鎖つき。D16 v0.2 §10.2）に候補ごとの結果の行を書く。"""
    path = repo / "research" / "adoption_reviews.jsonl"
    entries = [
        replace(
            review_entry(
                ExperimentId(ContentDigest.sha256(key.experiment_id)),
                key.execution,
                result=ReviewResult(result),
                nonce=f"{index + 1:x}" * 32,
            ),
            fixed_trials=Paired(
                standard=_fixed(trials, Variant.STANDARD),
                candidate=_fixed(trials, Variant.CANDIDATE) if candidate_fixed else None,
            ),
        )
        for index, key in enumerate(keys)
    ]
    path.write_bytes(b"".join(review_line_bytes(line) for line in review_lines(entries)))


def write_fixation(
    repo: Path,
    key: CandidateKey,
    *,
    t_fix: UtcTime = T_FIX,
    length_days: int = 364,
    trials: Paired[int] | None = None,
) -> None:
    chosen = trials or Paired(standard=2, candidate=2)
    path = repo / "research" / "fixations" / f"{key.experiment_id}_{key.execution}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: 1\n"
        f"experiment_id: {key.experiment_id}\n"
        f"execution: {key.execution}\n"
        f't_fix: "{t_fix}"\n'
        f"length_days: {length_days}\n"
        "fixed_trials:\n"
        f"  standard: {chosen.standard}\n"
        f"  candidate: {chosen.candidate}\n",
        encoding="utf-8",
    )


def build_workspace(
    tmp_path: Path, *, review: bool = True, fixation: bool = True
) -> FinalWorkspace:
    """人工の作業場を作り、origin へ push する。"""
    repo = tmp_path / "repo"
    home = tmp_path / "githome"
    repo.mkdir(parents=True)
    shutil.copy(_ROOT / ".gitignore", repo / ".gitignore")
    (repo / "research").mkdir()
    (repo / "research" / "final_validations.jsonl").write_bytes(b"")
    (repo / "research" / "quality_access_log.jsonl").write_bytes(b"")
    window_config(repo)
    shutil.copytree(
        _ROOT / "docs" / "design" / "time_rules", repo / "docs" / "design" / "time_rules"
    )
    snapshots = repo / "data" / "snapshots"
    periods = weeks()
    sealed = Paired(
        standard=write_snapshot(snapshots, sealed_manifest(TimeRuleVariant.STANDARD, periods)),
        candidate=write_snapshot(snapshots, sealed_manifest(TimeRuleVariant.CANDIDATE, periods)),
    )
    opening = trial_ledger.started("cand", purpose=StandardPurpose.STANDARD)
    lines = trial_ledger.chain(
        [opening, trial_ledger.finished(opening, verdict=SearchVerdict.MEETS_STANDARD)]
    )
    trial_ledger.write_ledger(repo, lines)
    key = CandidateKey(
        experiment_id=trial_ledger.experiment_id("cand").hex,
        execution=1,
        experiment_name="exp_cand",
        experiment_version=1,
    )
    if review:
        write_review(repo, [key])
    else:
        (repo / "research" / "adoption_reviews.jsonl").write_bytes(b"")
    if fixation:
        write_fixation(repo, key)
    experiment_dir = repo / "runs" / "experiments" / "exp_cand" / "v1"
    experiment_dir.mkdir(parents=True)
    origin = publish_repo(repo, tmp_path / "origin.git", home=home)
    workspace = FinalWorkspace(
        repo=repo,
        origin=origin,
        home=home,
        experiment_dir=experiment_dir,
        sealed=sealed,
        research=Paired(standard="r" * 64, candidate="s" * 64),
        key=key,
    )
    workspace.started_digest = lines[0].digest.hex
    return workspace
