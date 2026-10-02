"""唯一の構成ルート（D01 §7・§7.2）。

設定ディレクトリと `data/` の位置から、原データの読込（`CsvRawBarSource`）・snapshot の
保存（`ParquetSnapshotStore`）・設定済みの受入れユースケース（`AcceptanceService`）を
組み立てる。

**業務ロジックは持たない**（D01 §7）。ここにあるのは「どの実装をどの設定で結線するか」
だけで、何を検査し何を識別子に含めるかは `marketdata.application` が決める。

設定ファイルの解析は `odyssey_fx.app.config` の責務であり、ここでは行わない（D01 §6 の
契約 "F5c: config parsers only in app.config"）。本モジュールは読み込み済みの
domain 型を受け取る。

受入れの実行時刻（`created_at`）は `app` が `datetime.now(UTC)` から作る。識別子の計算
対象ではないので、実行のたびに違っても同じ原ファイル・設定・コード版・分類なら同じ
snapshot になる（D03 §3.7.1）。
"""

from __future__ import annotations

import platform
import secrets
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import odyssey_fx
from odyssey_fx.app.config import ConfigError, DataSourceConfig
from odyssey_fx.app.config.experiment import ExperimentConfig, RunBody
from odyssey_fx.app.config.experiment_v2 import (
    TEXT_ROLES,
    ExperimentV2,
    SearchSetting,
    experiment_v2_from_texts,
)
from odyssey_fx.app.config.loader import load_yaml_mapping
from odyssey_fx.app.config.refill import calendar_from_ref
from odyssey_fx.backtest.application.run_backtest import RunBacktest
from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.engine.loop import EngineContext, TraceOutputSink
from odyssey_fx.backtest.trace.manifest import config_digest_of
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId, IdAllocator, RunId
from odyssey_fx.common.refs import (
    CodeDigest,
    ConfigDigest,
    ContentDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    SnapshotRef,
)
from odyssey_fx.common.refs import run_id as run_id_of
from odyssey_fx.common.symbol import Symbol, SymbolSpec, SymbolSpecRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.adapters.fs_store import (
    REPORT_FILE,
    FileSystemExperimentStore,
    FileSystemResultRepository,
    FileSystemResultWriter,
    FileSystemTraceSink,
    evaluation_directory,
    read_experiment_manifest,
    read_experiment_outcome,
    reproduction_path,
    reproduction_payload,
    require_absent,
    require_plain_experiment_path,
    require_run_directory_absent,
    run_directory,
    write_reproduction,
)
from odyssey_fx.evaluation.adapters.report import ReportWrite, write_report
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, EvaluationReport
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.evaluation.application.ports import ResultReadFailure
from odyssey_fx.evaluation.application.run_experiment import (
    ExperimentRefusal,
    PreparedExperiment,
    PreparedSearch,
    PreparedTrial,
    ReproductionReport,
    ReproductionVerdict,
    RunExperiment,
    TrialLedgerStop,
    judge_reproduction,
)
from odyssey_fx.evaluation.domain.experiment import (
    EXPERIMENT_SCHEMA_VERSION,
    ExperimentManifest,
    ExperimentOutcome,
    ResolvedFile,
    experiment_id_of,
    require_experiment_name,
)
from odyssey_fx.evaluation.domain.research_policy import (
    ComplexityMeasures,
    InstanceProfile,
    check_complexity,
    check_hypothesis,
    check_research_history_only,
    check_trial_count,
    measure_complexity,
    search_complexity,
)
from odyssey_fx.evaluation.domain.search import (
    ComparisonBasis,
    ParameterAssignment,
    TrialPhase,
    TrialPlan,
    TrialUnitKey,
    compile_rejections_of,
    enumerate_assignments,
    trial_units,
)
from odyssey_fx.marketdata.adapters.csv_source import CsvRawBarSource
from odyssey_fx.marketdata.adapters.dukascopy_source import DukascopyTickSource
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore, manifest_payload
from odyssey_fx.marketdata.adapters.refill_store import FsRefillStore
from odyssey_fx.marketdata.application.acceptance import (
    PendingSnapshot,
    RawFile,
    build_pending_snapshot,
    merge_refill_bars,
    normalize_rows,
)
from odyssey_fx.marketdata.application.aggregation import AGGREGATION_RULE_VERSION, aggregate
from odyssey_fx.marketdata.application.asof import AsOfView, ExecutionSeriesView
from odyssey_fx.marketdata.application.ports import RawBarSource, SnapshotStore
from odyssey_fx.marketdata.application.publication import build_feed, build_publication_log
from odyssey_fx.marketdata.application.refill_finalize import (
    REFILL_SOURCE_ROOT,
    FinalizeReport,
    finalize_plan,
    require_refill_set,
    verify_refill_directory,
)
from odyssey_fx.marketdata.application.refill_plan import (
    build_plan,
    derive_target_bars,
    load_raw_bars,
)
from odyssey_fx.marketdata.application.snapshot_access import (
    PartitionedBars,
    ReadableSnapshot,
    VerifiedPartitionBars,
)
from odyssey_fx.marketdata.domain.access import (
    INITIAL_ACCESS_BOUNDARIES,
    AccessBoundaries,
    AccessClass,
)
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError, RefillStoreInconsistent
from odyssey_fx.marketdata.domain.integrity import CheckResult
from odyssey_fx.marketdata.domain.publication_log import PublicationLog
from odyssey_fx.marketdata.domain.refill import (
    CalendarRef,
    ProviderRef,
    RefillFilter,
    RefillPlan,
    require_hex_digest,
)
from odyssey_fx.marketdata.domain.refill_manifest import RefillFileRecord, RefillManifest
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    ConversionRecord,
    PartitionId,
    SnapshotManifest,
)
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.registry import ComponentRegistry, ContractKey
from odyssey_fx.strategy.compiler.compiled import (
    CompiledStrategy,
    CompileFailed,
    CompileSucceeded,
)
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.digest import strategy_ref_for
from odyssey_fx.strategy.declarations.specs import ParameterValue
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator

__all__ = [
    "AcceptanceService",
    "EvaluationOutcome",
    "ExperimentRunOutcome",
    "ReproductionOutcome",
    "RunOutcome",
    "SnapshotInputs",
    "TrialLedgerStop",
    "acceptance_service",
    "build_conversion_record",
    "build_experiment_manifest",
    "calendar_ref_of",
    "code_digest",
    "code_version",
    "compile_experiment_strategy",
    "complexity_profiles",
    "env_digest",
    "evaluate_saved_run",
    "execute_run",
    "git_state",
    "lock_digest",
    "now_utc",
    "open_snapshot_inputs",
    "raw_bar_source",
    "recompute_experiment_id",
    "reproduce_experiment",
    "run_experiment",
    "snapshot_store",
    "symbol_spec_ref_of",
]

#: 上位足を生成する組（D03 §5.1）。1時間足からだけ作る。15分足は執行系列なので
#: 集約元にしない。
AGGREGATION_TARGETS: tuple[tuple[str, str], ...] = (("1h", "4h_ny17"), ("1h", "1d_ny17"))


def now_utc() -> UtcTime:
    """受入れ実行時刻（D03 §3.7）。

    `created_at` は記録のみで識別には使わない（D03 §3.7.1）。時刻を取る場所を `app` の
    1関数に閉じ込めるのは、application 層が現在時刻を読まない（＝決定論的である）ことを
    保つためである。
    """
    return UtcTime(datetime.now(UTC))


def code_version() -> str:
    """変換コード版（D03 §3.7 の `conversion.code_version`）。

    import されたパッケージ配下の `.py` の内容から決まるダイジェストを使う（D02 §9.4）。
    git のコミットや作業ツリーの状態からは決めない。コードが変われば snapshot の識別子も
    変わるので、「どのコードが作った snapshot か」が内容だけから分かる。
    """
    package_dir = Path(str(odyssey_fx.__file__)).resolve().parent
    return CodeDigest.from_package_dir(package_dir).digest.hex


def raw_bar_source(raw_root: Path, time_column: str) -> CsvRawBarSource:
    """原 CSV の読込を組み立てる（D03 §8）。

    `time_column` は列対応の宣言が指す名前。原 CSV の先頭列は無名なので、読込時に
    この名前を与える。
    """
    return CsvRawBarSource(root=raw_root, time_column=time_column)


def snapshot_store(snapshots_root: Path) -> ParquetSnapshotStore:
    """snapshot の保存・読込を組み立てる（D03 §8）。"""
    return ParquetSnapshotStore(root=snapshots_root)


def snapshot_manifest_payload(manifest: SnapshotManifest) -> Mapping[str, Any]:
    """検算済みの snapshot の manifest を `manifest.json` と同じ形にする（D03 §3.7.1）。

    報告（`tools/ops/refill_report.py`）が集計に使う。`snapshot_store(...).read_manifest` が
    識別子を計算し直して確かめた型から作るので、ファイルを別に読み直さない。
    """
    return manifest_payload(manifest)


def build_conversion_record(calendar: TradingCalendar, time_convention: str) -> ConversionRecord:
    """変換の版の記録を作る（D03 §3.7）。

    コード版・時刻規約・集約規則の版・カレンダーの識別と版をまとめる。これらはすべて
    snapshot の識別子の計算対象なので、どれか1つでも変われば別の snapshot になる。
    """
    return ConversionRecord(
        code_version=code_version(),
        time_convention=time_convention,
        aggregation_rule_version=AGGREGATION_RULE_VERSION,
        calendar_id=calendar.id,
        calendar_version=calendar.version,
    )


@dataclass(frozen=True, slots=True)
class AcceptanceService:
    """設定済みの受入れユースケース（D03 §4 の 1〜8）。

    原データの読込ポート・snapshot の保存ポート・設定（列対応、カレンダー、時間足定義、
    期間境界）を結線した状態で持ち、「原ファイルの列を渡せば暫定 snapshot ができる」
    ところまでを1つにまとめる。

    検査・分類・識別子の計算はすべて `marketdata.application` が行う。本型が足すのは
    結線と、ポート越しの入出力（読み・書き）の順序だけである。
    """

    source: RawBarSource
    store: SnapshotStore
    datasource: DataSourceConfig
    calendar: TradingCalendar
    timeframe_defs: Mapping[str, TimeframeDefinition]
    boundaries: AccessBoundaries = INITIAL_ACCESS_BOUNDARIES
    #: 補充した足のファイルの読込（リポジトリの基点から `data/raw/market/refill/…` を読む。
    #: D03 §14.11）。補充分を渡さない受入れでは使わない。
    refill_source: RawBarSource | None = None

    def timeframe_definition(self, timeframe_id: str) -> TimeframeDefinition:
        """設定から時間足定義を引く。定義の無い時間足は拒否する（D03 §3.2）。

        後段で引けずに失敗するより、どの時間足が足りないかが分かる位置で止める。
        """
        definition = self.timeframe_defs.get(timeframe_id)
        if definition is None:
            raise MarketDataValueError(
                f"時間足 {timeframe_id!r} の定義が設定に無い（D03 §3.2）。"
                f" 設定にあるのは {sorted(self.timeframe_defs)}"
            )
        return definition

    def timeframe_ref(self, timeframe_id: str) -> TimeframeRef:
        """時間足の参照を設定の定義から作る（D03 §3.2）。

        **版は設定ファイルが宣言したもの**を使う。ここで版を固定すると、時間足定義の版を
        上げても系列の参照が古い版のままになり、manifest に記録した系列と実際に使った
        定義が食い違う。
        """
        return self.timeframe_definition(timeframe_id).ref

    def read_source_file(
        self, symbol: Symbol, timeframe_id: str
    ) -> tuple[RawFile, tuple[Bar, ...]]:
        """原ファイルを**1回読んで**、その登録と正規化した足を作る（D03 §4 の 1〜3）。

        内容の sha256 と行を同じ読込から得る。別々に読むと、その間にファイルが差し替わった
        ときに manifest の出所の記録（sha256・行数）が実データと食い違い、「記録どおりで
        ない snapshot」ができてしまう（D03 §3.7.1）。

        `source` 列の値が宣言に無ければ拒否する。宣言していない出所の行を黙って取り込むと、
        manifest の出所の記録が実データと食い違う（D03 §3.7 の `provenance_counts`）。
        """
        name = self.datasource.file_name(symbol, timeframe_id)
        content = self.source.read_file(name)
        raw_file = RawFile(
            path=f"{self.datasource.root}/{name}",
            sha256=content.sha256,
            symbol=symbol,
            timeframe=self.timeframe_ref(timeframe_id),
            declared_basis=self.datasource.basis_declaration.value,
        )

        for index, row in enumerate(content.rows):
            value = row.get(self.datasource.mapping.source_column, "")
            if value not in self.datasource.allowed_sources:
                raise MarketDataValueError(
                    f"{raw_file.path} row {index}: 出所 {value!r} は宣言に無い"
                    f"（{sorted(self.datasource.allowed_sources)}）。"
                    " 宣言していない出所の行は受け入れない（D03 §4 の 2）"
                )

        bars = normalize_rows(
            raw_file,
            content.rows,
            self.datasource.mapping,
            self.timeframe_definition(timeframe_id),
            self.calendar,
        )
        return raw_file, bars

    def read_refill_file(
        self, manifest: RefillManifest, record: RefillFileRecord
    ) -> tuple[RawFile, tuple[Bar, ...]]:
        """補充した足のファイルを**1回読んで**、その登録と正規化した足を作る（D03 §4 の v1.15）。

        系列はファイル名からではなく補充の manifest の記録（`(ファイル名, 銘柄, 時間足)`）から
        決める。読んだ内容の sha256 と行数が manifest の記録と一致しなければ、何も書かずに
        食い違いとして止める（D03 §14.11.1 の W5。検算の後に差し替わった場合を含む）。
        `source` 列の値は列対応の宣言の `allowed_sources` に入っていなければならない（補充分を
        受け入れるのは `dukascopy_refill` を足した宣言の版 2。D03 §9）。
        """
        if self.refill_source is None or record.series is None or record.rows is None:
            raise MarketDataValueError("refill files are read only by a refill-aware acceptance")
        path = f"{REFILL_SOURCE_ROOT}/{manifest.refill_id}/{record.name}"
        content = self.refill_source.read_file(path)
        if content.sha256 != record.sha256 or len(content.rows) != record.rows:
            raise RefillStoreInconsistent(
                f"{path}: sha256 {content.sha256} / {len(content.rows)} rows differ from the refill"
                f" manifest ({record.sha256} / {record.rows} rows). Nothing was written"
                " (D03 §14.11.1 W5・W6)"
            )
        series = record.series
        definition = self.timeframe_definition(series.timeframe.id)
        if definition.ref != series.timeframe:
            raise MarketDataValueError(
                f"{path}: the refill records {series.timeframe}, but the timeframe definition is"
                f" {definition.ref} (D03 §3.2)"
            )
        if series.basis is not self.datasource.basis_declaration.value:
            raise MarketDataValueError(
                f"{path}: the refill basis {series.basis.value} differs from the declared basis"
                f" {self.datasource.basis_declaration.value.value} (D03 §2)"
            )
        for index, row in enumerate(content.rows):
            value = row.get(self.datasource.mapping.source_column, "")
            if value not in self.datasource.allowed_sources:
                raise MarketDataValueError(
                    f"{path} row {index}: 出所 {value!r} は宣言に無い"
                    f"（{sorted(self.datasource.allowed_sources)}）。補充分を受け入れるには"
                    " `dukascopy_refill` を足した列対応の宣言（legacy_merged_csv_v2.yaml）を使う"
                    "（D03 §4・§9）"
                )
        raw_file = RawFile(
            path=path,
            sha256=content.sha256,
            symbol=series.symbol,
            timeframe=series.timeframe,
            declared_basis=series.basis,
        )
        bars = normalize_rows(
            raw_file, content.rows, self.datasource.mapping, definition, self.calendar
        )
        return raw_file, bars

    def aggregate_all(
        self, bars_by_series: Mapping[SeriesId, tuple[Bar, ...]]
    ) -> tuple[dict[SeriesId, tuple[Bar, ...]], tuple[CheckResult, ...]]:
        """上位足を生成する（D03 §4 の 6、§5）。

        1時間足から 4時間足・日足を作る。生成できなかった区間（構成足が欠けている区間）
        の報告も返す。渡さないと「生成されなかった」事実が記録から消える。
        """
        generated: dict[SeriesId, tuple[Bar, ...]] = {}
        findings: list[CheckResult] = []
        for source_id, target_id in AGGREGATION_TARGETS:
            source_def = self.timeframe_definition(source_id)
            target_def = self.timeframe_definition(target_id)
            for series, bars in sorted(bars_by_series.items(), key=lambda pair: str(pair[0])):
                if series.timeframe.id != source_id:
                    continue
                target_series = SeriesId(
                    symbol=series.symbol,
                    timeframe=TimeframeRef(id=target_id, version=target_def.ref.version),
                    basis=series.basis,
                )
                result = aggregate(
                    bars,
                    source_timeframe_def=source_def,
                    target_series=target_series,
                    target_timeframe_def=target_def,
                    calendar=self.calendar,
                )
                # 1本も生成できなかった系列は**記録しない**（構成足がすべて不完全な
                # とき）。空の系列を入れると、覆う区間も partition も決められず manifest の
                # 組み立てが壊れる。生成できなかった事実は `findings` が伝える（D03 §5.2）。
                if result.bars:
                    generated[target_series] = result.bars
                findings.extend(result.findings)
        return generated, tuple(findings)

    def accept(
        self,
        targets: Sequence[tuple[Symbol, str]],
        *,
        created_at: UtcTime,
        refills: Sequence[RefillManifest] = (),
    ) -> PendingSnapshot:
        """原ファイルを受け入れて暫定 snapshot を組み立てる（D03 §4 の 1〜8）。

        `targets` は `(銘柄, 時間足の id)` の列。読む順は呼び出し側が決めるが、識別子は
        列挙順に依存しない（各列を正規順序へ整列するため、D03 §3.7.1）。

        `refills` は検算を済ませた補充分の manifest（`data accept --refill`。D03 §4 の v1.15・
        §14.11）。補充した足のファイルも `SourceFile` として登録し、同じ系列の原ファイルの足と
        合わせて 1 つの原系列にする。渡さなければ従来と同じ受入れである。

        実体（partition の Parquet）と検査報告の書き出しは行わない。書く場所は暫定か確定
        かで変わるので、呼び出し側（`app.cli`）が決める。
        """
        raw_files: list[RawFile] = []
        bars_by_file: dict[str, tuple[Bar, ...]] = {}
        bars_by_series: dict[SeriesId, tuple[Bar, ...]] = {}
        for symbol, timeframe_id in targets:
            raw_file, bars = self.read_source_file(symbol, timeframe_id)
            raw_files.append(raw_file)
            bars_by_file[raw_file.path] = bars
            bars_by_series[raw_file.series] = bars
        refill_files: list[tuple[RawFile, tuple[Bar, ...]]] = []
        for manifest in refills:
            for record in manifest.bar_files:
                raw_file, bars = self.read_refill_file(manifest, record)
                refill_files.append((raw_file, bars))
                raw_files.append(raw_file)
                bars_by_file[raw_file.path] = bars
        if refill_files:
            bars_by_series = merge_refill_bars(bars_by_series, refill_files)

        aggregated, findings = self.aggregate_all(bars_by_series)
        return build_pending_snapshot(
            created_at=created_at,
            raw_files=tuple(raw_files),
            bars_by_file=bars_by_file,
            timeframe_defs=self.timeframe_defs,
            calendar=self.calendar,
            boundaries=self.boundaries,
            basis_declaration=self.datasource.basis_declaration,
            conversion=build_conversion_record(
                self.calendar, self.datasource.mapping.time_convention
            ),
            aggregated_bars=aggregated,
            aggregated_findings=findings,
        )


def acceptance_service(
    *,
    repo_root: Path,
    snapshots_root: Path,
    datasource: DataSourceConfig,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    boundaries: AccessBoundaries = INITIAL_ACCESS_BOUNDARIES,
) -> AcceptanceService:
    """受入れユースケースを結線する（D01 §7）。

    `repo_root` はリポジトリの基点。原データの基点は、そこに設定の `root`
    （`data/raw/market`）を継いだ位置になる。`snapshots_root` は snapshot の基点
    （`data/snapshots/`）で、暫定・確定のどちらのディレクトリもこの下に作る。
    """
    return AcceptanceService(
        source=raw_bar_source(repo_root / datasource.root, datasource.mapping.time_column),
        store=snapshot_store(snapshots_root),
        datasource=datasource,
        calendar=calendar,
        timeframe_defs=timeframe_defs,
        boundaries=boundaries,
        refill_source=raw_bar_source(repo_root, datasource.mapping.time_column),
    )


# --- 単一 run の実行と評価（D06 §4.2、D07 §4）---------------------------------


#: 段階2で読める期間の分類（D03 §3.8、ADR-0014）。
#:
#: 研究履歴だけを読む。封印期間（`LEGACY_HOLDOUT`）は探索から隔離する対象であり、閲覧は
#: 記録と許可の手続き（段階4・D07 v0.2 の `holdout_gate`）を通ってからでないと行えない。
#: 未分類の隔離期間（`QUARANTINED_UNASSIGNED`）は**いかなる経路でも読めない**（同節）。
_READABLE_ACCESS_CLASSES: frozenset[AccessClass] = frozenset({AccessClass.RESEARCH_HISTORY})


def lock_digest(repo_root: Path) -> LockDigest:
    """`uv.lock` の内容ダイジェスト（D02 §9.4）。"""
    return LockDigest.from_lock_file(Path(repo_root) / "uv.lock")


def code_digest() -> CodeDigest:
    """import されたパッケージのソース内容のダイジェスト（D02 §9.4）。"""
    package_dir = Path(str(odyssey_fx.__file__)).resolve().parent
    return CodeDigest.from_package_dir(package_dir)


def env_digest() -> EnvDigest:
    """実行環境のダイジェスト（D02 §9.4）。

    同じ `uv.lock` でも環境が違えば別の wheel が選ばれ数値結果が変わりうるため、実行の
    識別子に含める（ADR-0006）。`odyssey_fx` 自身はソース内容のダイジェストが識別するので
    配布物の一覧から外す。
    """
    distributions: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if not name or name.replace("_", "-").lower() == "odyssey-trading-research":
            continue
        distributions[name] = distribution.version or ""
    return EnvDigest.from_environment(
        python_implementation=platform.python_implementation(),
        python_version=".".join(str(part) for part in sys.version_info[:3]),
        sys_platform=sys.platform,
        machine=platform.machine(),
        distributions=distributions,
    )


def git_state(repo_root: Path) -> tuple[str, bool]:
    """git のコミットと作業ツリーの汚れ（D06 §9.3 の識別の群）。

    識別子の算出には使わない（D02 §9.4）。成果物から「どのコミットで走らせたか」を人が
    辿れるようにするための記録である。git が使えない環境では空のコミットと `dirty=True`
    を返す（実際の状態が分からないことを「きれい」と記録しない）。
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return ("", True)
    return (commit, bool(status.strip()))


def symbol_spec_ref_of(spec: SymbolSpec) -> SymbolSpecRef:
    """銘柄仕様の版参照（D06 §9.3 の `ConfigDigest` の対象）。

    価格刻み・数量刻みを変えた実行は丸めが変わるので、内容から作ったダイジェストを参照に
    する。固定の文字列にすると、中身の違う仕様が同じ `ConfigDigest` になる。
    """
    return SymbolSpecRef(symbol=spec.symbol, version=spec.version, digest=digest(spec))


def calendar_ref_of(calendar: TradingCalendar) -> str:
    """カレンダーの版参照（D06 §9.3）。休場を足すと版が上がるので参照も変わる。"""
    return f"{calendar.id}@v{calendar.version}"


@dataclass(frozen=True, slots=True)
class _IntrabarBars:
    """下位足の供給（D06 §7.4）。解像度階層が2段以上のときだけ使う。

    `IntrabarSeries`（`backtest.application.ports`）を構造的に満たす。読めるのは許可された
    partition の足だけで、関門は `PartitionedBars` が持つ（D03 §6.1）。
    """

    bars: Mapping[SeriesId, tuple[Bar, ...]]

    def bars_in(self, series: SeriesId, interval: Interval) -> tuple[Bar, ...]:
        """区間に**収まる**下位足（D06 §7.4）。"""
        return tuple(
            bar
            for bar in self.bars.get(series, ())
            if interval.start <= bar.bar_start and bar.bar_end <= interval.end
        )


@dataclass(frozen=True, slots=True)
class SnapshotInputs:
    """承認済み snapshot から読んだ、run の入力一式（D03 §6・§7）。"""

    snapshot: ReadableSnapshot
    allowed_partitions: frozenset[PartitionId]
    partition_bars: Mapping[PartitionId, tuple[Bar, ...]]
    schedules: Mapping[SeriesId, SeriesSchedule]
    _verified: VerifiedPartitionBars | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def bars_of(self, series: SeriesId) -> tuple[Bar, ...]:
        """許可された partition にあるその系列の足（無ければ空）。"""
        return PartitionedBars(self.partition_bars, self.allowed_partitions).bars_or_empty(series)

    def verified_bars(self, label: str) -> VerifiedPartitionBars:
        """読み取りの関門を通した足の写し（D03 §3.7.1・§6.1）。

        **最初に使う読み取り経路で1度だけ**照合する。

        as-of ビュー・執行系列ビュー・公開フィード（遅延シナリオがあれば公開記録も）は同じ
        snapshot・許可集合・足を受け取り、以前はそれぞれが同じ内容照合を行っていた。照合は
        最初にこれを呼んだ経路（従来と同じく、遅延シナリオがあれば公開記録の組み立て、
        なければ as-of ビューの構築）で行い、以後の経路は結果を共有する。照合の失敗は
        従来と同じ時点・同じ型と文言で起きる。失敗した照合は覚えないので、次に呼べば同じ
        失敗になる。`label` は照合を行う経路の名前（型の検査の失敗の文言に載る）。
        """
        cached = self._verified
        # 覚えた写しは、この入力一式の snapshot・許可集合で関門を通ったものに限って使う。
        # そうでなければ（外から差し替えられたものを含む）照合し直す。
        if cached is not None and cached.verified_for(self.snapshot, self.allowed_partitions):
            return cached
        verified = VerifiedPartitionBars(
            self.snapshot, self.allowed_partitions, self.partition_bars, label=label
        )
        object.__setattr__(self, "_verified", verified)
        return verified


def _require_matching_calendar(
    manifest: SnapshotManifest, calendar: TradingCalendar, snapshot_id: str
) -> None:
    """snapshot が記録したカレンダーと同じものを使うことを確かめる（D03 §3.7）。

    承認済みの足と完全性検査の報告は、受入れのときのカレンダーで作られている
    （`SnapshotManifest.conversion` が版を記録している）。別の版のカレンダーで run を
    組むと、休場・夏時間・セッション境界の違いが公開イベントの予定と入力不足の判定を
    変えてしまう。**受入れをやり直さないまま結果の意味だけが変わる**ので、実行する前に
    止める。

    カレンダーを変えたいときは、そのカレンダーで受入れからやり直す（D03 §4 の 9 が
    分類の段でそれを行う）。
    """
    recorded = manifest.conversion
    if recorded.calendar_id == calendar.id and recorded.calendar_version == calendar.version:
        return
    raise MarketDataValueError(
        f"snapshot {snapshot_id} was accepted with the calendar"
        f" {recorded.calendar_id}@v{recorded.calendar_version} but the run was given"
        f" {calendar.id}@v{calendar.version}; a different calendar changes the publication"
        " schedule and the missing-input decisions without re-running acceptance (D03 §3.7)"
    )


def open_snapshot_inputs(
    *,
    snapshots_root: Path,
    snapshot_id: str,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
) -> SnapshotInputs:
    """承認済み snapshot を開き、読める partition の足と公開予定を揃える（D03 §3.7.1）。

    **読めるのは研究履歴の partition だけ**である（`_READABLE_ACCESS_CLASSES`）。封印期間と
    未分類の隔離期間を許可集合へ入れないので、段階2の実行と評価は封印されたデータへ触れる
    経路を1本も持たない（D07 §14）。
    """
    store = snapshot_store(snapshots_root)
    snapshot = store.open_readable(snapshot_id)
    manifest = snapshot.manifest
    _require_matching_calendar(manifest, calendar, snapshot_id)
    allowed = frozenset(
        record.partition_id
        for record in manifest.partitions
        if record.partition_id.access_class in _READABLE_ACCESS_CLASSES
    )
    if not allowed:
        raise MarketDataValueError(
            f"snapshot {snapshot_id} has no research-history partition;"
            " stage 2 reads only RESEARCH_HISTORY data (D03 §3.8)"
        )
    partition_bars = {
        partition_id: tuple(store.read_partition(snapshot_id, partition_id))
        for partition_id in sorted(allowed, key=str)
    }
    schedules: dict[SeriesId, SeriesSchedule] = {}
    for record in manifest.series:
        series = record.series_id
        definition = timeframe_defs.get(series.timeframe.id)
        if definition is None:
            raise MarketDataValueError(
                f"the snapshot records the series {series} but the configuration has no"
                f" definition for the timeframe {series.timeframe.id!r} (D03 §3.2)"
            )
        if definition.ref != series.timeframe:
            # **版まで揃っていることを確かめる**（D03 §3.1・§3.2）。系列の記録は定義の版を
            # 持っており、承認済みの足はその版の整列規則で作られている。名前だけで引くと、
            # 版を上げた定義で公開イベントの予定と足境界が変わり、**受入れをやり直さない
            # まま評価の対象が変わる**。執行系列だけを見ていても、評価系列の定義が
            # 差し替わっていれば戦略の判断が変わる。
            raise MarketDataValueError(
                f"the snapshot records the series {series} with the timeframe definition"
                f" {series.timeframe} but the configuration supplies {definition.ref};"
                " a different definition changes the bar boundaries the approved bars were"
                " accepted with (D03 §3.1・§3.2)"
            )
        schedules[series] = SeriesSchedule(
            series=series, timeframe_def=definition, calendar=calendar
        )
    return SnapshotInputs(
        snapshot=snapshot,
        allowed_partitions=allowed,
        partition_bars=partition_bars,
        schedules=schedules,
    )


def compile_experiment_strategy(
    experiment: ExperimentConfig, timeframe_defs: Mapping[str, TimeframeDefinition]
) -> CompiledStrategy:
    """実験設定の戦略宣言を解決済み設定へコンパイルする（D04 §12、D05 §5）。

    失敗を結果として返す型（`CompileFailed`）をそのまま外へ出さず、設定の誤りとして
    言い換える。宣言の書き誤りは人間が直すものであり、run を開始する前に止める。
    """
    timeframes = {definition.ref: definition for definition in timeframe_defs.values()}
    outcome = compile_strategy(experiment.strategy, INITIAL_CATALOG, timeframes)
    if not isinstance(outcome, CompileSucceeded):
        raise MarketDataValueError(
            f"the strategy declaration of {experiment.experiment_id!r} does not compile: {outcome}"
        )
    return outcome.compiled


def _publication_log(inputs: SnapshotInputs, experiment: ExperimentConfig) -> PublicationLog:
    """遅延シナリオを当てた実現公開時刻の記録（D07 §18.3、D03 §3.6）。

    遅延の計算そのものは市場データの規則（`build_publication_log` と `DelayScenario`）が持ち、
    ここは「実験設定の遅延シナリオをどの足に当てるか」を結線するだけである。当てる足は
    as-of ビュー・公開フィードと同じ、許可された partition の足である。
    """
    if experiment.delay_scenario is None:
        return PublicationLog()
    return build_publication_log(
        inputs.snapshot,
        inputs.allowed_partitions,
        inputs.verified_bars("build_publication_log"),
        inputs.schedules,
        experiment.delay_scenario,
    )


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """1回の run の成果（CLI が表示に使う）。"""

    result: BacktestResult
    run_id: RunId
    directory: Path


@dataclass(frozen=True, slots=True)
class _RunPlan:
    """run を始める前に決まるもの一式（実行条件・識別子・結線済みの市場データ）。

    `execute_run`（`run` コマンド）と実験の経路（`experiment run` / `experiment reproduce`）が
    同じ組み立てを使う。`RunId` は run の前に決まる（ADR-0006）ので、実験の経路はここで予測
    した識別子で既存の成果物を確かめ、記録票に `ConfigDigest` を固定する（D07 §19.2・§19.6）。
    """

    experiment: ExperimentConfig
    calendar: TradingCalendar
    inputs: SnapshotInputs
    spec: SymbolSpec
    compiled: CompiledStrategy
    config: RunConfig
    config_digest: ConfigDigest
    symbol_spec_ref: SymbolSpecRef
    calendar_ref: str
    timeframe_refs: tuple[TimeframeRef, ...]
    code: CodeDigest
    lock: LockDigest
    environment: EnvDigest
    run_id: RunId
    git_commit: str
    git_dirty: bool
    publication_log: PublicationLog


def _plan_run(
    *,
    experiment: ExperimentConfig,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    symbol_specs: Mapping[Symbol, SymbolSpec],
    snapshots_root: Path,
    repo_root: Path,
) -> _RunPlan:
    """snapshot を開き、戦略をコンパイルし、実行条件と識別子を計算する（run はまだ始めない）。"""
    inputs = open_snapshot_inputs(
        snapshots_root=snapshots_root,
        snapshot_id=str(experiment.snapshot_ref.snapshot_id),
        calendar=calendar,
        timeframe_defs=timeframe_defs,
    )
    symbol = experiment.execution_series.symbol
    spec = symbol_specs.get(symbol)
    if spec is None:
        raise MarketDataValueError(
            f"the configuration has no symbol specification for {symbol} (D02 §5.2)"
        )

    compiled = compile_experiment_strategy(experiment, timeframe_defs)
    if inputs.schedules.get(experiment.execution_series) is None:
        raise MarketDataValueError(
            f"the snapshot does not carry the execution series {experiment.execution_series}"
            " (D03 §6.3)"
        )

    # 遅延シナリオを市場データへ当てる（D07 §18.3、D03 §3.6）。公開フィードと as-of ビューへ
    # 渡す前に、実現した公開時刻（`available_at`）を計算しておく。遅延なし（書式 v1、または
    # 書式 v2 で `delay_scenario` を書かない形）では記録を作らず、通常の公開予定を使う。
    publication_log = _publication_log(inputs, experiment)

    symbol_spec_ref = symbol_spec_ref_of(spec)
    calendar_ref = calendar_ref_of(calendar)
    timeframe_refs = tuple(
        sorted((definition.ref for definition in timeframe_defs.values()), key=str)
    )
    config = RunConfig(
        run_interval=experiment.run_interval,
        snapshot_ref=experiment.snapshot_ref,
        compiled_ref=compiled.compiled_ref,
        account=experiment.account,
        risk_policy_ref=experiment.policy_ref("risk"),
        execution_policy_ref=experiment.policy_ref("execution"),
        cost_model_ref=experiment.policy_ref("cost"),
        conversion_policy_ref=experiment.policy_ref("conversion"),
        delay_scenario_ref=experiment.policy_ref("delay"),
        execution_series=experiment.execution_series,
        seed=experiment.seed,
    )
    config_digest = config_digest_of(
        config,
        symbol_spec_ref=symbol_spec_ref,
        calendar_ref=calendar_ref,
        timeframe_def_refs=timeframe_refs,
    )
    code = code_digest()
    lock = lock_digest(repo_root)
    environment = env_digest()
    commit, dirty = git_state(repo_root)
    return _RunPlan(
        experiment=experiment,
        calendar=calendar,
        inputs=inputs,
        spec=spec,
        compiled=compiled,
        config=config,
        config_digest=config_digest,
        symbol_spec_ref=symbol_spec_ref,
        calendar_ref=calendar_ref,
        timeframe_refs=timeframe_refs,
        code=code,
        lock=lock,
        environment=environment,
        run_id=run_id_of(config_digest, code, lock, environment),
        git_commit=commit,
        git_dirty=dirty,
        publication_log=publication_log,
    )


def _run_use_case(plan: _RunPlan, artifacts_root: Path, *, replace: bool) -> RunBacktest:
    """計画から実行ユースケースを結線する（1回の run ごとに作り直す。状態を持つため）。"""
    experiment = plan.experiment
    inputs = plan.inputs
    # 戦略ランタイムへは as-of ビューをそのまま渡す（D05 §6.3 v1.4、D03 §6.2 v1.5）。
    # 履歴窓は受け口が構造だけを要求するので、合成が層をまたいで言い換える必要はない。
    # 読み取りの関門の内容照合は run の中で1度だけ行い、3つの読み取り経路で共有する
    # （`SnapshotInputs.verified_bars`）。照合の条件と失敗の型・文言は変わらない。
    verified = inputs.verified_bars("AsOfView")
    market_data = AsOfView(
        snapshot=inputs.snapshot,
        allowed_partitions=inputs.allowed_partitions,
        schedules=inputs.schedules,
        partition_bars=verified,
        publication_log=plan.publication_log,
    )
    execution_view = ExecutionSeriesView(
        snapshot=inputs.snapshot,
        series=experiment.execution_series,
        allowed_partitions=inputs.allowed_partitions,
        partition_bars=verified,
        schedule=inputs.schedules[experiment.execution_series],
    )
    feed = build_feed(
        inputs.snapshot,
        inputs.allowed_partitions,
        verified,
        inputs.schedules,
        experiment.run_interval,
        execution_series=frozenset({experiment.execution_series}),
        publication_log=plan.publication_log,
    )
    levels = experiment.execution_policy.resolution_hierarchy.levels
    intrabar = (
        None
        if len(levels) < 2
        else _IntrabarBars(bars={level: inputs.bars_of(level) for level in levels})
    )
    allocator = IdAllocator(plan.run_id)
    output_sink = TraceOutputSink()
    context = EngineContext(experiment.account)
    runtime = StrategyEvaluator(
        compiled=plan.compiled,
        registry=INITIAL_CATALOG,
        market_data=market_data,
        context=context,
        sink=output_sink,
        allocator=allocator,
    )
    return RunBacktest(
        runtime=runtime,
        context=context,
        output_sink=output_sink,
        allocator=allocator,
        feed=feed,
        execution_series=execution_view,
        calendar=plan.calendar,
        risk_policy=experiment.risk_policy,
        execution_policy=experiment.execution_policy,
        cost_model=experiment.cost_model,
        conversion_policy=experiment.conversion_policy,
        symbol_spec=plan.spec,
        symbol_spec_ref=plan.symbol_spec_ref,
        calendar_ref=plan.calendar_ref,
        integrity=inputs.snapshot.report,
        trace_sink=FileSystemTraceSink(root=artifacts_root, run_id=plan.run_id, replace=replace),
        result_writer=FileSystemResultWriter(root=artifacts_root),
        code_digest=plan.code,
        lock_digest=plan.lock,
        env_digest=plan.environment,
        git_commit=plan.git_commit,
        git_dirty=plan.git_dirty,
        intrabar_series=intrabar,
        timeframe_refs=plan.timeframe_refs,
    )


def execute_run(
    *,
    experiment: ExperimentConfig,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    symbol_specs: Mapping[Symbol, SymbolSpec],
    snapshots_root: Path,
    artifacts_root: Path,
    repo_root: Path,
    replace: bool = False,
) -> RunOutcome:
    """実験設定から1回の run を実行し、判断履歴・manifest・結果を保存する（D06 §4.2）。

    **業務ロジックは持たない**（D01 §7）。ここにあるのは「どの実装をどの設定で結線するか」
    だけで、実行の意味論は `backtest.application.run_backtest` が決める。

    識別の4群（コード・依存 lock・環境のダイジェストと git の状態）は**この層が計算する**
    （D06 §9.3）。実行環境の事実であって設定ではないためである。
    """
    plan = _plan_run(
        experiment=experiment,
        calendar=calendar,
        timeframe_defs=timeframe_defs,
        symbol_specs=symbol_specs,
        snapshots_root=snapshots_root,
        repo_root=repo_root,
    )
    if not replace:
        # 保存先が既にあれば、run を始める前に何も書かずに失敗する（D06 §10.6、R4）。
        # 書き出しの直前にもう一度、作ること自体で確かめる（`reserve_run_directory`）。
        require_run_directory_absent(artifacts_root, plan.run_id)
    result = _run_use_case(plan, artifacts_root, replace=replace).run(plan.config, plan.compiled)
    return RunOutcome(
        result=result,
        run_id=plan.run_id,
        directory=run_directory(artifacts_root, plan.run_id),
    )


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """1回の評価の成果（CLI が表示に使う）。"""

    report: EvaluationReport
    directory: Path


def evaluate_saved_run(
    *,
    run_id: RunId,
    artifacts_root: Path,
    calendar: TradingCalendar,
    metric_set_version: int = METRIC_SET_VERSION,
) -> EvaluationOutcome | ResultReadFailure:
    """保存済みの run を評価し、5表と評価 manifest を保存する（D07 §4・§8）。

    評価時のコードのダイジェストは**この層が算出して渡す**（D07 §9.2、Q5 決定）。
    パッケージのソース内容を読むのは入出力であり、`application` は入出力を持たない。

    保存済みの結果 DTO が読めなければ評価を始めず、`ResultReadFailure` を返す（D07 §4.1 の
    `read_result` の段落 (a)。コマンドは読込の誤りとして終了コード 2 で終わる）。

    取引カレンダーは呼び出し側が読み込んで渡す（D07 §4.1 v2.0 のカレンダーの渡し方 (a)）。
    run manifest は `calendar_ref`（識別と版）だけを持ち本文を持たないためである。一致しない
    カレンダーは整合検査 C10 で不合格になり、評価は `FAILED` になる。
    """
    repository = FileSystemResultRepository(root=artifacts_root)
    result = repository.read_result(run_id)
    if isinstance(result, ResultReadFailure):
        # 結果 DTO は評価の入力1そのものなので、読めなければ評価を始めない（D07 §4.1 (a)）。
        return result
    use_case = EvaluateRun(evaluation_code_digest=code_digest())
    report = use_case.evaluate(result, repository, metric_set_version, calendar)
    repository.write_evaluation(report, report.rows)
    return EvaluationOutcome(
        report=report,
        directory=evaluation_directory(artifacts_root, run_id, report.manifest.run_evaluation_id),
    )


# --- 実験の経路（D07 §19〜§21）-------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SnapshotManifestCatalog:
    """`SnapshotCatalog`（`evaluation.application.ports`）を snapshot の manifest で満たす。

    アクセス分類は snapshot の manifest が partition ごとに記録している（D03 §3.8）。記録に
    無い partition は許可集合に入りえないので、構造エラーにする。
    """

    manifest: SnapshotManifest

    def access_classes(
        self, snapshot: SnapshotRef, partitions: frozenset[PartitionId]
    ) -> Mapping[PartitionId, AccessClass]:
        recorded = {record.partition_id for record in self.manifest.partitions}
        missing = sorted(str(partition) for partition in partitions if partition not in recorded)
        if missing:
            raise MarketDataValueError(
                f"the snapshot {snapshot.snapshot_id} does not record the partitions {missing}"
                " (D03 §3.8)"
            )
        return {partition: partition.access_class for partition in partitions}


@dataclass(slots=True)
class _PlannedRunner:
    """`BacktestRunner`（`evaluation.application.ports`）を満たす。

    置換の指示を持たない（常に「存在すれば失敗」で書く。D07 §19.6 の手順4）。実行条件は
    計画と同じものでなければならない（記録票に固定した `ConfigDigest` の run だけを行う）。
    """

    plan: _RunPlan
    artifacts_root: Path

    def run(self, config: RunConfig, compiled: CompiledStrategy) -> BacktestResult:
        if config != self.plan.config or compiled.compiled_ref != self.plan.compiled.compiled_ref:
            raise KernelValueError(
                "the runner only runs the configuration it was planned for (D07 §19.4)"
            )
        return _run_use_case(self.plan, self.artifacts_root, replace=False).run(config, compiled)


def complexity_profiles(
    compiled: CompiledStrategy, registry: ComponentRegistry
) -> tuple[tuple[InstanceProfile, ...], tuple[str, ...], tuple[str, ...]]:
    """複雑性の計測の材料（使用箇所ごとの `InstanceProfile`）を作る（D07 §20.4）。

    評価は部品カタログを参照できない（D01 §3.2 の契約 F8）ので、合成が組み立てて渡す。
    部品の登録が見つからない使用箇所は出力のデータ型を知り得ないので、計測できなかった
    使用箇所と原因として返す（判断を出す使用箇所の数を 0 で埋めない。D07 §20.3）。
    """
    profiles: list[InstanceProfile] = []
    unmeasured: list[str] = []
    causes: list[str] = []
    for component in compiled.components:
        contract = component.contract_ref
        registration = registry.get(ContractKey(contract.component_id, contract.version))
        if registration is None:
            unmeasured.append(component.instance_id)
            causes.append(
                f"{component.instance_id}: the registry has no {contract.component_id}"
                f"@v{contract.version}, so its output data types are unknown"
            )
            output_types: tuple[str, ...] = ()
        else:
            output_types = tuple(
                sorted({spec.data_type.type_id for spec in registration.contract.outputs.values()})
            )
        profiles.append(
            InstanceProfile(
                instance_id=component.instance_id,
                component_id=contract.component_id,
                parameter_count=len(component.parameters),
                output_data_types=output_types,
            )
        )
    return tuple(profiles), tuple(unmeasured), tuple(causes)


def _resolved_files(loaded: ExperimentV2, symbols: Sequence[Symbol]) -> tuple[ResolvedFile, ...]:
    """記録票の `resolved_files`（D07 §19.2）。

    銘柄仕様は**実行する銘柄と、換算の経路に現れる銘柄**のものだけを入れる。換算は段階2 から
    恒等換算だけを通す（D06 §8.5.1。口座通貨と決済通貨が違う run は換算不能で止まる）ので、
    換算の経路に現れる銘柄は無く、入るのは実行する銘柄だけである。
    """
    files = [ResolvedFile.of(role, loaded.texts[role]) for role in TEXT_ROLES]
    for symbol in symbols:
        text = loaded.symbol_texts.get(symbol)
        if text is None:
            raise ConfigError(f"銘柄 {symbol} の仕様の本文が読まれていない（D07 §19.2）")
        files.append(ResolvedFile.of(f"symbol:{symbol}", text))
    return tuple(files)


@dataclass(frozen=True, slots=True)
class ExperimentRunOutcome:
    """`experiment run` の成果（CLI が表示と終了コードに使う）。

    `report` は書いたレポート（`report.md`）の置き場。結末記録を書かずに拒否した場合
    （`ExperimentRefusal`）は何も書かないので `None`（D07 §19.4）。探索の実験は単数の予測
    `RunId` を持たない（`expected_run_id` は `None`。D09 §10.6）。
    """

    manifest: ExperimentManifest
    result: ExperimentOutcome | ExperimentRefusal
    directory: Path
    expected_run_id: RunId | None
    report: Path | None = None


def build_experiment_manifest(
    loaded: ExperimentV2,
    plan: _RunPlan,
    registry: ComponentRegistry,
) -> ExperimentManifest:
    """記録票を組み立てる（D07 §19.2）。事前検査 P1・P2・P6 はここで行って記録票に入れる。"""
    experiment = loaded.experiment
    try:
        require_experiment_name(experiment.experiment_id)
    except KernelValueError as exc:
        raise ConfigError(f"実験設定の `id` を保存先の名前に使えない: {exc}") from exc
    catalog = _SnapshotManifestCatalog(plan.inputs.snapshot.manifest)
    access = catalog.access_classes(experiment.snapshot_ref, plan.inputs.allowed_partitions)
    allowed = {str(partition): access_class for partition, access_class in access.items()}
    profiles, unmeasured, causes = complexity_profiles(plan.compiled, registry)
    measures = measure_complexity(profiles, unmeasured_outputs=unmeasured)
    limits = loaded.policy.limits
    pre_run_checks = (
        check_hypothesis(loaded.hypothesis),
        check_research_history_only(allowed),
        check_complexity(measures, limits, unmeasured_causes=causes),
    )
    policy = loaded.policy
    draft = ExperimentManifest(
        experiment_id=ExperimentId(digest("draft")),
        experiment_name=experiment.experiment_id,
        experiment_version=experiment.version,
        schema_version=EXPERIMENT_SCHEMA_VERSION,
        hypothesis=loaded.hypothesis,
        research_policy_ref=PolicyRef(
            policy_kind="research",
            policy_id=policy.policy_id,
            version=policy.version,
            digest=policy.digest,
        ),
        metric_set_version=loaded.metric_set_version,
        search_plan=loaded.search_plan,
        split=loaded.split,
        resolved_files=_resolved_files(loaded, (experiment.execution_series.symbol,)),
        strategy_ref=plan.compiled.strategy_ref,
        compiled_ref=plan.compiled.compiled_ref,
        expected_config_digest=plan.config_digest,
        snapshot_id=experiment.snapshot_ref.snapshot_id,
        allowed_partitions=allowed,
        complexity=measures,
        complexity_limits=limits,
        pre_run_checks=pre_run_checks,
        code_digest=plan.code,
        lock_digest=plan.lock,
        env_digest=plan.environment,
        git_commit=plan.git_commit,
        git_dirty=plan.git_dirty,
    )
    return replace(draft, experiment_id=experiment_id_of(draft, loaded.values))


def run_experiment(
    *,
    loaded: ExperimentV2,
    snapshots_root: Path,
    artifacts_root: Path,
    repo_root: Path,
) -> ExperimentRunOutcome:
    """書式 v2 の実験設定から1つの実験を進める（D07 §19.4、`experiment run`）。

    記録票を組み立て（事前検査を含む）、`RunExperiment` に渡す。記録票の保存・run・評価・
    事後検査の順序と拒否の判断は `RunExperiment` が持つ（ここは結線だけ）。

    探索の実験（`search_plan` / `split` が `NONE` でない）は `run_search_experiment` へ渡す
    （D09 §4.1・§10.7）。
    """
    if loaded.search is not None:
        return run_search_experiment(
            loaded=loaded,
            snapshots_root=snapshots_root,
            artifacts_root=artifacts_root,
            repo_root=repo_root,
        )
    environment = loaded.environment
    plan = _plan_run(
        experiment=loaded.experiment,
        calendar=environment.calendar,
        timeframe_defs=environment.timeframe_defs,
        symbol_specs=environment.symbol_specs,
        snapshots_root=snapshots_root,
        repo_root=repo_root,
    )
    manifest = build_experiment_manifest(loaded, plan, INITIAL_CATALOG)
    store = FileSystemExperimentStore(
        root=artifacts_root,
        experiment_name=manifest.experiment_name,
        experiment_version=manifest.experiment_version,
        identity_of=recompute_experiment_id,
    )
    use_case = RunExperiment(
        store=store,
        runner=_PlannedRunner(plan=plan, artifacts_root=artifacts_root),
        repository=FileSystemResultRepository(root=artifacts_root),
        evaluator=EvaluateRun(evaluation_code_digest=plan.code),
    )
    prepared = PreparedExperiment(
        manifest=manifest,
        run_config=plan.config,
        compiled=plan.compiled,
        calendar=plan.calendar,
        expected_run_id=plan.run_id,
        code_digest=plan.code,
        lock_digest=plan.lock,
        env_digest=plan.environment,
        git_commit=plan.git_commit,
        git_dirty=plan.git_dirty,
    )
    result = use_case.execute(prepared)
    report: Path | None = None
    if isinstance(result, ExperimentOutcome):
        # **`experiment run` の最後にレポートを作る**（D07 §22.1）。保存済みの成果物だけから
        # 作るので、結末記録を書いた後に呼ぶ。拒否（結末記録を書かない経路）では何も書かない
        # （D07 §19.4 の「検査済み」「保存を試みる」行）。
        write_report(store.directory, artifacts_root)
        report = store.directory / REPORT_FILE
    return ExperimentRunOutcome(
        manifest=manifest,
        result=result,
        directory=store.directory,
        expected_run_id=plan.run_id,
        report=report,
    )


# --- 探索の実験の準備（D09 §5・§6.2・§10.2・§10.7。実装 PR 2）----------------------------


def trial_strategy(
    strategy: StrategyDefinition, assignment: ParameterAssignment
) -> StrategyDefinition:
    """割当の値で戦略ファイルのパラメータを置き換えた戦略宣言（D09 §5.1）。

    軸に挙げたパラメータだけを試行の値で置き換え、軸に無いパラメータは戦略ファイルの値のまま
    （書かれていなければ契約の既定値のまま）にする。置き換えた宣言をコンパイルすると、割当を
    含む解決済み設定の識別 `CompiledStrategyRef` が得られる（D02 §9.2、D05 §5.5）。
    """
    overrides: dict[str, dict[str, ParameterValue]] = {}
    for instance_id, parameter, value in assignment.values:
        overrides.setdefault(instance_id, {})[parameter] = value
    known = {instance.instance_id for instance in strategy.components}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise KernelValueError(f"the assignment names instances {unknown} absent from the strategy")
    return replace(
        strategy,
        components=tuple(
            replace(instance, parameters={**instance.parameters, **overrides[instance.instance_id]})
            if instance.instance_id in overrides
            else instance
            for instance in strategy.components
        ),
    )


def _unit_run_config(
    experiment: RunBody, compiled: CompiledStrategy, interval: Interval
) -> RunConfig:
    """単位の `RunConfig`（D09 §6.2）。単位どうしで違うのは `compiled_ref` と区間だけ。

    `interval` は単位の実行区間（fold の選定区間か検証区間）で、評価範囲ではない。
    """
    return RunConfig(
        run_interval=interval,
        snapshot_ref=experiment.snapshot_ref,
        compiled_ref=compiled.compiled_ref,
        account=experiment.account,
        risk_policy_ref=experiment.policy_ref("risk"),
        execution_policy_ref=experiment.policy_ref("execution"),
        cost_model_ref=experiment.policy_ref("cost"),
        conversion_policy_ref=experiment.policy_ref("conversion"),
        delay_scenario_ref=experiment.policy_ref("delay"),
        execution_series=experiment.execution_series,
        seed=experiment.seed,
    )


def _distinct(items: Sequence[str]) -> tuple[str, ...]:
    """順序を保って重複を除く（試行ごとに同じ原因が出る計測不能の原因をまとめる）。"""
    return tuple(dict.fromkeys(items))


def build_search_manifest(
    loaded: ExperimentV2,
    inputs: SnapshotInputs,
    trials: Sequence[PreparedTrial],
    measures: Sequence[ComplexityMeasures],
    unmeasured_causes: Sequence[str],
    environment: tuple[CodeDigest, LockDigest, EnvDigest, str, bool],
) -> ExperimentManifest:
    """探索の実験の記録票を組み立てる（D09 §10.2）。事前検査 P1・P2・P6・P7 もここで行う。

    - P2 は全単位の許可集合の和（探索の全単位は同じ snapshot の同じ許可集合を読む）。
    - P6 はコンパイルが通った全試行の計測値の最大（`search_complexity`）。1件も通らなければ
      計測できなかった（`UNREADABLE`）。
    - P7 は列挙した試行の数（コンパイル拒否の試行を含む）と研究ポリシーの試行数の上限。
    """
    search = loaded.search
    if search is None:
        raise KernelValueError("build_search_manifest requires a search experiment")
    experiment = loaded.body
    try:
        require_experiment_name(experiment.experiment_id)
    except KernelValueError as exc:
        raise ConfigError(f"実験設定の `id` を保存先の名前に使えない: {exc}") from exc
    policy = loaded.policy
    if policy.trial_limit is None:  # pragma: no cover - 版 3 以上は試行数の上限を持つ
        raise ConfigError("探索の実験は試行数の上限を持つ研究ポリシーの版しか指せない（D09 §10.9）")
    catalog = _SnapshotManifestCatalog(inputs.snapshot.manifest)
    access = catalog.access_classes(experiment.snapshot_ref, inputs.allowed_partitions)
    allowed = {str(partition): access_class for partition, access_class in access.items()}
    complexity = search_complexity(measures)
    causes = list(unmeasured_causes)
    if not measures:
        causes.append("no trial compiled, so there is nothing to measure (D09 §10.2)")
    pre_run_checks = (
        check_hypothesis(loaded.hypothesis),
        check_research_history_only(allowed),
        check_complexity(complexity, policy.limits, unmeasured_causes=_distinct(causes)),
        check_trial_count(len(trials), policy.trial_limit),
    )
    code, lock, env, commit, dirty = environment
    draft = ExperimentManifest(
        experiment_id=ExperimentId(digest("draft")),
        experiment_name=experiment.experiment_id,
        experiment_version=experiment.version,
        schema_version=EXPERIMENT_SCHEMA_VERSION,
        hypothesis=loaded.hypothesis,
        research_policy_ref=PolicyRef(
            policy_kind="research",
            policy_id=policy.policy_id,
            version=policy.version,
            digest=policy.digest,
        ),
        metric_set_version=loaded.metric_set_version,
        search_plan=search.plan,
        split=search.split,
        resolved_files=_resolved_files(loaded, (experiment.execution_series.symbol,)),
        strategy_ref=strategy_ref_for(experiment.strategy),
        compiled_ref=None,
        expected_config_digest=None,
        snapshot_id=experiment.snapshot_ref.snapshot_id,
        allowed_partitions=allowed,
        complexity=complexity,
        complexity_limits=policy.limits,
        pre_run_checks=pre_run_checks,
        code_digest=code,
        lock_digest=lock,
        env_digest=env,
        git_commit=commit,
        git_dirty=dirty,
        evaluation_standard=search.standard,
        final_holdout=search.final_holdout,
        trials=tuple(trial.plan for trial in trials),
    )
    return replace(draft, experiment_id=experiment_id_of(draft, loaded.values))


def _prepare_trial(
    *,
    index: int,
    assignment: ParameterAssignment,
    search: SearchSetting,
    experiment: RunBody,
    timeframes: Mapping[TimeframeRef, TimeframeDefinition],
    registry: ComponentRegistry,
    refs: tuple[SymbolSpecRef, str, tuple[TimeframeRef, ...]],
    environment: tuple[CodeDigest, LockDigest, EnvDigest],
) -> PreparedTrial:
    """試行1つをコンパイルし、全単位の `RunConfig`・予測 `ConfigDigest`・予測 `RunId` を作る。

    コンパイルが拒否した割当は試行の失敗（`FAILED`）として拒否の区分を残し、run を作らない
    （D09 §5.2・§10.2）。設定の誤りとして止めない（探索空間の中の「実行できない点」）。
    """
    definition = trial_strategy(experiment.strategy, assignment)
    outcome = compile_strategy(definition, registry, timeframes)
    if isinstance(outcome, CompileFailed):
        plan = TrialPlan(
            trial_index=index,
            assignment=assignment,
            compiled_ref=None,
            compile_rejections=compile_rejections_of(outcome.errors),
            expected_config_digests=(),
        )
        return PreparedTrial(plan=plan, compiled=None, run_configs=(), expected_run_ids=())
    if not isinstance(outcome, CompileSucceeded):  # pragma: no cover - 区分は2つだけ
        raise KernelValueError("compile_strategy returned an unexpected value")
    compiled = outcome.compiled
    symbol_spec_ref, calendar_ref, timeframe_refs = refs
    code, lock, env = environment
    folds = search.split.folds
    configs: list[tuple[TrialUnitKey, RunConfig]] = []
    digests: list[tuple[TrialUnitKey, ConfigDigest]] = []
    run_ids: list[tuple[TrialUnitKey, RunId]] = []
    for unit in trial_units(len(folds), index):
        fold = folds[unit.fold_index]
        interval = fold.train if unit.phase is TrialPhase.TRAIN else fold.validation
        config = _unit_run_config(experiment, compiled, interval)
        config_digest = config_digest_of(
            config,
            symbol_spec_ref=symbol_spec_ref,
            calendar_ref=calendar_ref,
            timeframe_def_refs=timeframe_refs,
        )
        configs.append((unit, config))
        digests.append((unit, config_digest))
        run_ids.append((unit, run_id_of(config_digest, code, lock, env)))
    plan = TrialPlan(
        trial_index=index,
        assignment=assignment,
        compiled_ref=compiled.compiled_ref,
        compile_rejections=(),
        expected_config_digests=tuple(digests),
    )
    return PreparedTrial(
        plan=plan, compiled=compiled, run_configs=tuple(configs), expected_run_ids=tuple(run_ids)
    )


@dataclass(frozen=True, slots=True)
class _SearchParts:
    """探索の実験の準備の結果と、単位の run の結線に使う材料（snapshot は1回だけ開く）。"""

    prepared: PreparedSearch
    inputs: SnapshotInputs
    spec: SymbolSpec
    refs: tuple[SymbolSpecRef, str, tuple[TimeframeRef, ...]]


def prepare_search(
    *,
    loaded: ExperimentV2,
    snapshots_root: Path,
    repo_root: Path,
    registry: ComponentRegistry = INITIAL_CATALOG,
) -> PreparedSearch:
    """探索の実験の1回分の入力を組み立てる（D09 §4.1 の手順1・2、§10.2・§10.7）。

    snapshot を開き、試行を列挙し（`enumerate_assignments`）、試行ごとにコンパイルし、コンパイルが
    通った試行は全 fold の選定区間と検証区間の単位の `RunConfig`・予測 `ConfigDigest`・予測
    `RunId` を作る。記録票（事前検査 P1・P2・P6・P7 を含む）を組み立てて `PreparedSearch` に
    束ねる。**run はしない**。`registry` はコンパイルと複雑性の計測の両方に使う部品の登録
    （読込に使ったものと同じものを渡す）。
    """
    return _prepare_search(
        loaded=loaded, snapshots_root=snapshots_root, repo_root=repo_root, registry=registry
    ).prepared


def _prepare_search(
    *,
    loaded: ExperimentV2,
    snapshots_root: Path,
    repo_root: Path,
    registry: ComponentRegistry,
) -> _SearchParts:
    search = loaded.search
    if search is None:
        raise ConfigError("prepare_search は探索の実験（search_plan が NONE でない）だけを受ける")
    experiment = loaded.body
    environment = loaded.environment
    inputs = open_snapshot_inputs(
        snapshots_root=snapshots_root,
        snapshot_id=str(experiment.snapshot_ref.snapshot_id),
        calendar=environment.calendar,
        timeframe_defs=environment.timeframe_defs,
    )
    symbol = experiment.execution_series.symbol
    spec = environment.symbol_specs.get(symbol)
    if spec is None:
        raise MarketDataValueError(
            f"the configuration has no symbol specification for {symbol} (D02 §5.2)"
        )
    if inputs.schedules.get(experiment.execution_series) is None:
        raise MarketDataValueError(
            f"the snapshot does not carry the execution series {experiment.execution_series}"
            " (D03 §6.3)"
        )
    refs = (
        symbol_spec_ref_of(spec),
        calendar_ref_of(environment.calendar),
        tuple(sorted((d.ref for d in environment.timeframe_defs.values()), key=str)),
    )
    code = code_digest()
    lock = lock_digest(repo_root)
    env = env_digest()
    commit, dirty = git_state(repo_root)
    timeframes = {definition.ref: definition for definition in environment.timeframe_defs.values()}
    trials: list[PreparedTrial] = []
    measures: list[ComplexityMeasures] = []
    causes: list[str] = []
    for index, assignment in enumerate(enumerate_assignments(search.plan)):
        trial = _prepare_trial(
            index=index,
            assignment=assignment,
            search=search,
            experiment=experiment,
            timeframes=timeframes,
            registry=registry,
            refs=refs,
            environment=(code, lock, env),
        )
        trials.append(trial)
        if trial.compiled is not None:
            profiles, unmeasured, trial_causes = complexity_profiles(trial.compiled, registry)
            measures.append(measure_complexity(profiles, unmeasured_outputs=unmeasured))
            causes.extend(trial_causes)
    manifest = build_search_manifest(
        loaded, inputs, trials, measures, causes, (code, lock, env, commit, dirty)
    )
    prepared = PreparedSearch(
        manifest=manifest,
        trials=tuple(trials),
        calendar=environment.calendar,
        code_digest=code,
        lock_digest=lock,
        env_digest=env,
        git_commit=commit,
        git_dirty=dirty,
    )
    return _SearchParts(prepared=prepared, inputs=inputs, spec=spec, refs=refs)


def comparison_basis(
    prepared: PreparedSearch, refs: tuple[SymbolSpecRef, str, tuple[TimeframeRef, ...]]
) -> ComparisonBasis | None:
    """比較の前提を組み立てる（D09 §3・§10.10）。

    単位の `RunConfig`（run manifest の「入力」と「ポリシー」の群の材料。D06 §9.3）から
    `compiled_ref` と `run_interval` を除いた全項目に、銘柄仕様・カレンダー・時間足定義の参照、
    記録票の研究ポリシーの版参照・指標集合の版・戦略ファイルの戦略の識別、**この実行**の環境の
    ダイジェストを足す。単位ごとに違うのは `compiled_ref` と `run_interval` だけ（D09 §6.2）なので、
    最初の単位の `RunConfig` から作る。コンパイルが通った試行が無ければ `None`（その実験は事前
    検査 P6 で止まり、台帳に書かない）。
    """
    configs = [config for trial in prepared.trials for _, config in trial.run_configs]
    if not configs:
        return None
    config = configs[0]
    symbol_spec_ref, calendar_ref, timeframe_refs = refs
    manifest = prepared.manifest
    return ComparisonBasis(
        research_policy_ref=manifest.research_policy_ref,
        metric_set_version=manifest.metric_set_version,
        snapshot_ref=config.snapshot_ref,
        strategy_ref=manifest.strategy_ref,
        execution_series=config.execution_series,
        seed=config.seed,
        account=config.account,
        risk_policy_ref=config.risk_policy_ref,
        execution_policy_ref=config.execution_policy_ref,
        cost_model_ref=config.cost_model_ref,
        conversion_policy_ref=config.conversion_policy_ref,
        delay_scenario_ref=config.delay_scenario_ref,
        symbol_spec_ref=symbol_spec_ref,
        calendar_ref=calendar_ref,
        timeframe_def_refs=timeframe_refs,
        code_digest=prepared.code_digest,
        lock_digest=prepared.lock_digest,
        env_digest=prepared.env_digest,
    )


@dataclass(slots=True)
class _SearchRunner:
    """探索の単位の run を行う `BacktestRunner`（`evaluation.application.ports`）。

    記録票に固定した単位の `RunConfig` だけを run する（D07 §19.4、D09 §10.2）。単位の区間は
    `RunConfig.run_interval`（fold の選定区間か検証区間）で、それ以外は全単位で同じ（D09 §6.2）。
    置換の指示を持たない（常に「存在すれば失敗」で書く。D07 §19.6 の手順4）。
    """

    loaded: ExperimentV2
    parts: _SearchParts
    artifacts_root: Path
    publication_log: PublicationLog
    planned: dict[RunConfig, CompiledStrategy]

    def run(self, config: RunConfig, compiled: CompiledStrategy) -> BacktestResult:
        expected = self.planned.get(config)
        if expected is None or expected.compiled_ref != compiled.compiled_ref:
            raise KernelValueError(
                "the runner only runs the unit configurations fixed in the manifest (D09 §10.2)"
            )
        prepared = self.parts.prepared
        symbol_spec_ref, calendar_ref, timeframe_refs = self.parts.refs
        config_digest = config_digest_of(
            config,
            symbol_spec_ref=symbol_spec_ref,
            calendar_ref=calendar_ref,
            timeframe_def_refs=timeframe_refs,
        )
        plan = _RunPlan(
            experiment=self.loaded.body.with_run_interval(config.run_interval),
            calendar=prepared.calendar,
            inputs=self.parts.inputs,
            spec=self.parts.spec,
            compiled=compiled,
            config=config,
            config_digest=config_digest,
            symbol_spec_ref=symbol_spec_ref,
            calendar_ref=calendar_ref,
            timeframe_refs=timeframe_refs,
            code=prepared.code_digest,
            lock=prepared.lock_digest,
            environment=prepared.env_digest,
            run_id=run_id_of(
                config_digest, prepared.code_digest, prepared.lock_digest, prepared.env_digest
            ),
            git_commit=prepared.git_commit,
            git_dirty=prepared.git_dirty,
            publication_log=self.publication_log,
        )
        return _run_use_case(plan, self.artifacts_root, replace=False).run(config, compiled)


def run_search_experiment(
    *,
    loaded: ExperimentV2,
    snapshots_root: Path,
    artifacts_root: Path,
    repo_root: Path,
) -> ExperimentRunOutcome:
    """探索の実験の1回の実行（D09 §4.1・§10.7・§10.12、`experiment run`）。

    合成は結線だけを行う: 準備（試行の列挙・コンパイル・記録票）、比較の前提、実行ごとの乱数
    （`execution_nonce`。128 ビット。D09 §10.12.1 の W3）、試行台帳を置くリポジトリの根、成果物の
    基点と走らせている版のディレクトリ（逆照合 L11 に使う）を `RunExperiment.execute_search` へ
    渡す。台帳が読めない・追記が断られたときは `TrialLedgerStop`（終了コード 1）が上がる。

    **探索の実験のレポート（`report.md`）は書かない**: 探索の節の書式（D09 §11.5）は段階5 の実装
    PR 5 で作る。結末記録と台帳の結末の行まで書いた終端した実行で、レポートだけが無い状態
    （D09 §10.7 の終端の書き込みの (3) の後・(4) の前）になる。
    """
    parts = _prepare_search(
        loaded=loaded,
        snapshots_root=snapshots_root,
        repo_root=repo_root,
        registry=INITIAL_CATALOG,
    )
    prepared = parts.prepared
    manifest = prepared.manifest
    store = FileSystemExperimentStore(
        root=artifacts_root,
        experiment_name=manifest.experiment_name,
        experiment_version=manifest.experiment_version,
        identity_of=recompute_experiment_id,
        repo_root=repo_root,
    )
    search = loaded.search
    if search is None:  # pragma: no cover - 呼び出し側が探索の実験だけを渡す
        raise KernelValueError("run_search_experiment requires a search experiment")
    runner = _SearchRunner(
        loaded=loaded,
        parts=parts,
        artifacts_root=artifacts_root,
        publication_log=_publication_log(
            parts.inputs, loaded.body.with_run_interval(search.standard.split.range)
        ),
        planned={
            config: trial.compiled
            for trial in prepared.trials
            if trial.compiled is not None
            for _, config in trial.run_configs
        },
    )
    use_case = RunExperiment(
        store=store,
        runner=runner,
        repository=FileSystemResultRepository(root=artifacts_root),
        evaluator=EvaluateRun(evaluation_code_digest=prepared.code_digest),
    )
    result = use_case.execute_search(
        prepared,
        basis=comparison_basis(prepared, parts.refs),
        execution_nonce=secrets.token_hex(16),
        out_base=str(artifacts_root),
        running=store.directory.relative_to(artifacts_root).as_posix(),
    )
    return ExperimentRunOutcome(
        manifest=manifest,
        result=result,
        directory=store.directory,
        expected_run_id=None,
        report=None,
    )


@dataclass(frozen=True, slots=True)
class ExperimentReportOutcome:
    """`experiment report` の成果（CLI が表示に使う）。"""

    path: Path
    written: ReportWrite


def report_experiment(*, experiment_dir: Path) -> ExperimentReportOutcome:
    """保存済みの成果物だけからレポートを作り直す（D07 §22.1、`experiment report`）。

    run と評価の成果物は、実験の版のディレクトリ `<根>/runs/experiments/<名前>/v<版>` と同じ
    根の `runs/` から読む。引数・読込の誤り（版のディレクトリの形でない、記録票が無い・読めない、
    結末記録があるのに読めない、名前と版がディレクトリと合わない）は `ConfigError`（終了コード 2）。
    """
    # **リンクを解決する前の経路で形と要素を確かめる**（PR #48 の Codex 第2系列の第1巡）。
    # `resolve()` するとリンクの存在が消え、`runs/` より下の `experiments`・名前・版が
    # リンクでも、リンク先を実ディレクトリとして書いてしまう（D07 §19.1 の境界。R4）。
    # 経路は絶対化だけ行い、`..` を含む指定は拒否する（字面の形と実体がずれるため）。
    directory = experiment_dir.absolute()
    if ".." in directory.parts:
        raise ConfigError(f"`--experiment-dir` に `..` を含めない: {experiment_dir}")
    parents = directory.parents
    if len(parents) < 4 or parents[1].name != "experiments" or parents[2].name != "runs":
        raise ConfigError(
            f"`--experiment-dir` は runs/experiments/<名前>/v<版> の形のディレクトリを指す"
            f"（run と評価の成果物を同じ根の runs/ から読むため。D07 §19.1）: {experiment_dir}"
        )
    root = parents[3]
    # 途中がリンク（リンク切れを含む）なら、何も作らず何も書かずに `ArtifactAlreadyExists`
    # （終了コード 1。D07 §22.2）。存在の確かめ（終了コード 2）より先に行う（PR #48 の
    # Codex 第2系列の第2巡。リンク切れは存在しないように見えるため）。
    if not require_plain_experiment_path(root, directory):
        raise ConfigError(f"`--experiment-dir` が実験の版のディレクトリではない: {experiment_dir}")
    try:
        manifest = read_experiment_manifest(directory)
        outcome = read_experiment_outcome(directory)
    except KernelValueError as exc:
        raise ConfigError(f"記録票か結末記録を読めない: {exc}") from exc
    if outcome is not None and outcome.experiment_id != manifest.experiment_id:
        # 別の実験の結末記録を混ぜたレポートは作らない。読込の誤り（終了コード 2。D07 §22.2）。
        raise ConfigError(
            f"{experiment_dir} の結末記録は実験 {outcome.experiment_id} のもので、記録票の"
            f" {manifest.experiment_id} と一致しない"
        )
    if manifest.is_search:
        # 探索の節の書式（D09 §11.5）・`--repo-root`・台帳の照合（R1〜R7）は段階5 の実装 PR 5 で
        # 作る。単一実行の書式で探索の実験のレポートを作らない（引数・読込の誤り。終了コード 2）。
        raise ConfigError(
            f"{experiment_dir} は探索の実験の版のディレクトリである。探索の実験のレポートは段階5 の"
            "実装 PR 5 で作る（D09 §11.5。この段階では未対応）"
        )
    if (directory.parent.name, directory.name) != (
        manifest.experiment_name,
        f"v{manifest.experiment_version}",
    ):
        raise ConfigError(
            f"{experiment_dir} の記録票は {manifest.experiment_name} 版"
            f" {manifest.experiment_version} のもので、ディレクトリの名前と版に合わない"
            "（D07 §19.1）"
        )
    written = write_report(directory, root)
    return ExperimentReportOutcome(path=directory / REPORT_FILE, written=written)


@dataclass(frozen=True, slots=True)
class ReproductionOutcome:
    """`experiment reproduce` の成果（CLI が表示と終了コードに使う）。"""

    report: ReproductionReport
    path: Path
    detail: str


def _original_root(experiment_dir: Path) -> Path | None:
    """実験の版のディレクトリ `<根>/runs/experiments/<名前>/v<版>` から元の成果物の根を引く。"""
    resolved = experiment_dir.resolve()
    if len(resolved.parents) < 4:
        return None
    if resolved.parents[1].name != "experiments" or resolved.parents[2].name != "runs":
        return None
    return resolved.parents[3]


def _same_place(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def reproduce_experiment(
    *,
    experiment_dir: Path,
    snapshots_root: Path,
    out_root: Path,
    repo_root: Path,
) -> ReproductionOutcome:
    """記録票と結末記録だけから run と評価をやり直し、判定を書く（D07 §21.2）。

    受け取るのは実験の版のディレクトリ・snapshot の基点・出力の基点（と、現在の環境の lock を
    読むリポジトリの位置）だけで、元の実験設定ファイル・戦略ファイルは読まない（D07 §21.1）。
    引数・読込の誤り（結末記録が無い、run が行われていない、記録票・結末記録が読めない、
    `--out` が元の成果物と同じ基点）は `ConfigError` で止め、`reproduction.json` を書かない。
    """
    if not experiment_dir.is_dir():
        raise ConfigError(f"`--experiment-dir` が実験の版のディレクトリではない: {experiment_dir}")
    original = _original_root(experiment_dir)
    if original is not None and (
        _same_place(out_root, original) or _same_place(out_root / "runs", original / "runs")
    ):
        raise ConfigError(
            f"`--out` が元の成果物と同じ基点 {original} を指している。再現は元の `runs/` を"
            " 上書きしないよう、別の基点に書く（D07 §21.2 の手順4）"
        )
    require_absent(
        reproduction_path(out_root),
        remedy="Choose another --out, or move the earlier reproduction away",
    )

    # 手順1: 記録票と結末記録を読み、改変と取り違えを確かめる。
    try:
        manifest = read_experiment_manifest(experiment_dir)
        outcome = read_experiment_outcome(experiment_dir)
    except KernelValueError as exc:
        raise ConfigError(f"記録票か結末記録を読めない: {exc}") from exc
    if outcome is None:
        raise ConfigError(
            f"{experiment_dir} に結末記録が無い。途中で止まった実験には再現する結果が無い"
            "（D07 §21.2 の手順1）"
        )
    if outcome.run_id is None or outcome.result_digest is None:
        raise ConfigError(
            f"{experiment_dir} の結末記録は run と評価を持たない（{outcome.status.value}）。"
            "再現する結果が無い（D07 §21.2 の手順1）"
        )
    expected_run_id = outcome.run_id
    expected_digest = outcome.result_digest

    def report(
        verdict: ReproductionVerdict,
        detail: str,
        observed_run_id: RunId | None = None,
        observed_digest: ContentDigest | None = None,
    ) -> ReproductionOutcome:
        result = ReproductionReport(
            experiment_id=manifest.experiment_id,
            verdict=verdict,
            expected_run_id=expected_run_id,
            observed_run_id=observed_run_id,
            expected_result_digest=expected_digest,
            observed_result_digest=observed_digest,
        )
        path = write_reproduction(
            out_root,
            reproduction_payload(
                result.experiment_id,
                result.verdict.value,
                result.expected_run_id,
                result.observed_run_id,
                result.expected_result_digest,
                result.observed_result_digest,
            ),
        )
        return ReproductionOutcome(report=result, path=path, detail=detail)

    tampered = _tampering(manifest, outcome)
    if tampered is not None:
        return report(ReproductionVerdict.MANIFEST_TAMPERED, tampered)

    # 手順2: 現在の環境を結末記録と比べる（違えば run しない。Q12 決定）。
    current = (code_digest(), lock_digest(repo_root), env_digest())
    recorded = (outcome.code_digest, outcome.lock_digest, outcome.env_digest)
    if current != recorded:
        names = [
            name
            for name, now, then in zip(("code", "lock", "env"), current, recorded, strict=True)
            if now != then
        ]
        return report(
            ReproductionVerdict.ENVIRONMENT_MISMATCH,
            f"the current {', '.join(names)} digest differs from the outcome; not run",
        )

    # 手順3: 記録票の本文から設定を組み立て、ConfigDigest と RunId を確かめる。
    loaded = experiment_v2_from_texts(
        {role: manifest.file(role).text for role in TEXT_ROLES},
        {item.role.removeprefix("symbol:"): item.text for item in manifest.symbol_files()},
        registry=INITIAL_CATALOG,
        metric_set_versions=frozenset({METRIC_SET_VERSION}),
    )
    environment = loaded.environment
    plan = _plan_run(
        experiment=loaded.experiment,
        calendar=environment.calendar,
        timeframe_defs=environment.timeframe_defs,
        symbol_specs=environment.symbol_specs,
        snapshots_root=snapshots_root,
        repo_root=repo_root,
    )
    if plan.config_digest != manifest.expected_config_digest or plan.run_id != expected_run_id:
        return report(
            ReproductionVerdict.RUN_ID_MISMATCH,
            "the configuration rebuilt from the manifest does not reach the recorded run; not run",
            observed_run_id=plan.run_id,
        )

    # 手順4: 別の基点で run と評価を行う。
    require_run_directory_absent(out_root, plan.run_id)
    result = _run_use_case(plan, out_root, replace=False).run(plan.config, plan.compiled)
    repository = FileSystemResultRepository(root=out_root)
    evaluation = EvaluateRun(evaluation_code_digest=plan.code).evaluate(
        result, repository, manifest.metric_set_version, plan.calendar
    )
    repository.write_evaluation(evaluation, evaluation.rows)

    # 手順5: 記録と比べる。
    observed_digest = evaluation.manifest.result_digest
    verdict = judge_reproduction(
        expected_run_id=expected_run_id,
        observed_run_id=result.run_id,
        expected_result_digest=expected_digest,
        observed_result_digest=observed_digest,
    )
    return report(
        verdict,
        "re-ran in a separate artifacts root and compared run_id and result_digest",
        observed_run_id=result.run_id,
        observed_digest=observed_digest,
    )


def _tampering(manifest: ExperimentManifest, outcome: ExperimentOutcome) -> str | None:
    """手順1 の改変・取り違えの検出（D07 §21.2）。見つからなければ `None`。

    - 結末記録が別の記録票のもの（識別子が違う）。
    - 記録票の本文の改変（本文の SHA-256 が記録された値と違う）。識別子の入力には本文では
      なく SHA-256 が入る（D07 §19.2 の入力 3）ので、本文だけの改変は識別子の再計算では
      見つからない。
    - 記録票の項目の改変（識別子を再計算すると記録された値と違う）。
    """
    if outcome.experiment_id != manifest.experiment_id:
        return (
            f"the outcome belongs to the experiment {outcome.experiment_id}, not to the manifest"
            f" {manifest.experiment_id}"
        )
    altered = sorted(item.role for item in manifest.resolved_files if not item.intact)
    if altered:
        return f"the text of {altered} does not match its recorded SHA-256"
    if recompute_experiment_id(manifest) != manifest.experiment_id:
        return "the experiment id recomputed from the manifest does not match the recorded one"
    return None


def recompute_experiment_id(manifest: ExperimentManifest) -> ExperimentId:
    """保存済みの記録票の中身から識別子を再計算する（D07 §19.2 の「識別の入力」）。

    入力 2（実験設定の値）は記録票の `experiment` の本文を読み込んで作る。読めなければ
    `ConfigError`（`ValueError` 系）を送出する。記録票の保存時の比較（検査 P3）と再現の手順1 が
    使う（記録された識別子をそのまま信じない）。
    """
    values = load_yaml_mapping(
        Path("resolved_files[experiment]"), text=manifest.file("experiment").text
    )
    return experiment_id_of(manifest, values)


# --- 元データの再取得（補充）（D03 §14、D01 §4 v2.9）--------------------------------


def refill_store(refill_root: Path) -> FsRefillStore:
    """補充の置き場の読み書きを組み立てる（D03 §14.11）。`refill_root` は
    `data/raw/market/refill/`。"""
    return FsRefillStore(root=refill_root)


def tick_archive_source() -> DukascopyTickSource:
    """提供元の時間ファイルの取得を組み立てる（D03 §14.3・§14.9）。"""
    return DukascopyTickSource()


def prepare_refill_plan(
    *,
    snapshot_id: str,
    snapshots_root: Path,
    repo_root: Path,
    datasource: DataSourceConfig,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    calendar: TradingCalendar,
    calendar_ref: CalendarRef,
    provider: ProviderRef,
    refill_filter: RefillFilter,
) -> RefillPlan:
    """承認済み snapshot から取得計画を組み立てる（D03 §14.4・§14.10。まだ書かない）。

    manifest を読み、識別子がディレクトリ名と一致し承認済みであることを確かめてから、対象足の
    ある銘柄の原データだけを読む（sha256 と行数を manifest と照合する）。計画の作成と書き出し
    の規則は `marketdata.application.refill_plan` にある。
    """
    require_hex_digest(snapshot_id, "--snapshot")
    store = snapshot_store(snapshots_root)
    manifest = store.read_manifest(snapshot_id)
    if str(manifest.snapshot_id()) != snapshot_id:
        raise MarketDataValueError(
            f"the manifest under {snapshot_id} describes the snapshot {manifest.snapshot_id()};"
            " only a settled and approved snapshot can be the input of a refill plan (D03 §14.4)"
        )
    targets = derive_target_bars(
        manifest, calendar, timeframe_defs, INITIAL_ACCESS_BOUNDARIES, refill_filter
    )
    symbols = {target.series.symbol for target in targets}
    raw_bars = load_raw_bars(
        manifest,
        raw_bar_source(repo_root, datasource.mapping.time_column),
        datasource.mapping,
        timeframe_defs,
        calendar,
        symbols,
    )
    return build_plan(
        manifest=manifest,
        raw_bars=raw_bars,
        calendar=calendar,
        calendar_ref=calendar_ref,
        timeframe_defs=timeframe_defs,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        provider=provider,
        refill_filter=refill_filter,
    )


class _FinalizeInputs:
    """書き出し（`finalize`）が計画から読むもの（D03 §14.4・§14.7）。

    計画の入力 snapshot の manifest を読み（識別子がディレクトリ名と一致すること）、対象足の
    ある銘柄の原データを読む（sha256 と行数を manifest の `sources` と照合する）。カレンダーは
    計画に記録した正規化内容から組み立て直す。
    """

    def __init__(
        self,
        *,
        snapshots_root: Path,
        repo_root: Path,
        datasource: DataSourceConfig,
        timeframe_defs: Mapping[str, TimeframeDefinition],
    ) -> None:
        self._snapshots_root = snapshots_root
        self._repo_root = repo_root
        self._datasource = datasource
        self._timeframe_defs = timeframe_defs

    def calendar(self, plan: RefillPlan) -> TradingCalendar:
        return calendar_from_ref(plan.calendar)

    def raw_bars(
        self, plan: RefillPlan
    ) -> tuple[tuple[SeriesId, ...], Mapping[SeriesId, Sequence[Bar]]]:
        manifest = snapshot_store(self._snapshots_root).read_manifest(plan.snapshot_id)
        if str(manifest.snapshot_id()) != plan.snapshot_id:
            raise MarketDataValueError(
                f"the manifest under {plan.snapshot_id} describes the snapshot"
                f" {manifest.snapshot_id()}; the plan's input snapshot cannot be read (D03 §14.4)"
            )
        originals = tuple(
            sorted(
                {
                    SeriesId(
                        symbol=record.symbol,
                        timeframe=record.timeframe,
                        basis=record.declared_basis,
                    )
                    for record in manifest.sources
                },
                key=str,
            )
        )
        symbols = {target.series.symbol for target in plan.target_bars}
        bars = load_raw_bars(
            manifest,
            raw_bar_source(self._repo_root, self._datasource.mapping.time_column),
            self._datasource.mapping,
            self._timeframe_defs,
            self.calendar(plan),
            symbols,
        )
        return originals, bars


def finalize_refill_plan(
    *,
    plan_id: str,
    refill_root: Path,
    snapshots_root: Path,
    repo_root: Path,
    datasource: DataSourceConfig,
    timeframe_defs: Mapping[str, TimeframeDefinition],
) -> FinalizeReport:
    """検証して補充分を書き出す（D03 §14.7・§14.11・§14.12 の出来事10・11）。

    規則は `marketdata.application.refill_finalize` にある。ここでは置き場・原データの読込・
    変換コード版・実時計を結線するだけである。
    """
    require_hex_digest(plan_id, "--plan")
    return finalize_plan(
        plan_id,
        store=refill_store(refill_root),
        inputs=_FinalizeInputs(
            snapshots_root=snapshots_root,
            repo_root=repo_root,
            datasource=datasource,
            timeframe_defs=timeframe_defs,
        ),
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        code_version=code_version(),
        clock=now_utc,
    )


def load_refills_for_acceptance(
    *, refill_dirs: Sequence[Path], repo_root: Path, snapshots_root: Path
) -> tuple[RefillManifest, ...]:
    """受入れに渡された補充分を検算して manifest を返す（D03 §14.11・§14.11.1 の W5）。

    1. 置き場所: 引数のパスをシンボリックリンクを解いて正規化したものが、リポジトリの
       `data/raw/market/refill/<名前>` と一致すること（所定の場所の外へ写したものを受けない）。
    2. 補充分の検算: 完成の印、`plan_id`・`refill_id`（計算し直してディレクトリ名と一致）、
       ファイルの集合と sha256・行数。
    3. 集合の検査: 同じ計画の補充分を 2 つ以上渡していない。各補充分の入力 snapshot が含む
       補充分（補充を重ねた前の補充分）をすべて渡している。

    どれかが合わなければ何も書かずに失敗する。
    """
    expected_root = (repo_root / REFILL_SOURCE_ROOT).resolve()
    store = refill_store(repo_root / REFILL_SOURCE_ROOT)
    manifests: list[RefillManifest] = []
    seen: set[str] = set()
    for directory in refill_dirs:
        resolved = Path(directory).resolve()
        if resolved.parent != expected_root:
            raise RefillStoreInconsistent(
                f"--refill {directory} resolves to {resolved}, which is not directly under the"
                f" refill root {expected_root}; a refill is read only from its own place"
                " (D03 §14.11). Nothing was written"
            )
        name = resolved.name
        try:
            require_hex_digest(name, "--refill directory name")
        except MarketDataValueError as exc:
            raise RefillStoreInconsistent(f"--refill {directory}: {exc} (D03 §14.11)") from exc
        if name in seen:
            raise MarketDataValueError(f"--refill {name} was given twice")
        seen.add(name)
        manifests.append(
            verify_refill_directory(
                name, store.read_refill_manifest(name), store.list_refill_files(name)
            )
        )
    snapshots = snapshot_store(snapshots_root)
    input_sources: dict[str, tuple[str, ...]] = {}
    for manifest in manifests:
        if manifest.snapshot_id in input_sources:
            continue
        try:
            source_manifest = snapshots.read_manifest(manifest.snapshot_id)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # 読めない manifest は入力の構造エラー。渡し漏れ（`RefillChainIncomplete`）は、読めた
            # 記録から欠けた補充分が分かったときだけに使う（D03 v1.17 §14.11。PR #59 の仮置き 10
            # への決定 2026-10-01）。
            raise MarketDataValueError(
                f"the manifest of the input snapshot {manifest.snapshot_id} of the refill"
                f" {manifest.refill_id} cannot be read ({exc}); the input is structurally broken"
                " (D03 §14.11). Nothing was written"
            ) from exc
        if str(source_manifest.snapshot_id()) != manifest.snapshot_id:
            raise MarketDataValueError(
                f"the directory of the input snapshot {manifest.snapshot_id} of the refill"
                f" {manifest.refill_id} holds the manifest of {source_manifest.snapshot_id()};"
                " the input is structurally broken (D03 §3.7.1, §14.11). Nothing was written"
            )
        input_sources[manifest.snapshot_id] = tuple(
            record.path for record in source_manifest.sources
        )
    require_refill_set(manifests, input_sources)
    return tuple(sorted(manifests, key=lambda item: item.refill_id))
