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
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import odyssey_fx
from odyssey_fx.app.config import DataSourceConfig
from odyssey_fx.app.config.experiment import ExperimentConfig
from odyssey_fx.backtest.application.run_backtest import RunBacktest
from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.engine.loop import EngineContext, TraceOutputSink
from odyssey_fx.backtest.trace.manifest import config_digest_of
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import IdAllocator, RunId
from odyssey_fx.common.refs import CodeDigest, EnvDigest, LockDigest
from odyssey_fx.common.refs import run_id as run_id_of
from odyssey_fx.common.symbol import Symbol, SymbolSpec, SymbolSpecRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.adapters.fs_store import (
    FileSystemResultRepository,
    FileSystemResultWriter,
    FileSystemTraceSink,
    evaluation_directory,
    run_directory,
)
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, EvaluationReport
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION
from odyssey_fx.marketdata.adapters.csv_source import CsvRawBarSource
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.acceptance import (
    PendingSnapshot,
    RawFile,
    build_pending_snapshot,
    normalize_rows,
)
from odyssey_fx.marketdata.application.aggregation import AGGREGATION_RULE_VERSION, aggregate
from odyssey_fx.marketdata.application.asof import (
    AsOfView,
    BarsWindow,
    DurationWindow,
    ExecutionSeriesView,
    MissingInput,
)
from odyssey_fx.marketdata.application.ports import RawBarSource, SnapshotStore
from odyssey_fx.marketdata.application.publication import build_feed
from odyssey_fx.marketdata.application.snapshot_access import PartitionedBars, ReadableSnapshot
from odyssey_fx.marketdata.domain.access import (
    INITIAL_ACCESS_BOUNDARIES,
    AccessBoundaries,
    AccessClass,
)
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckResult
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    ConversionRecord,
    PartitionId,
    SnapshotManifest,
)
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy, CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.read_spec import BarsWindow as DeclaredBarsWindow
from odyssey_fx.strategy.declarations.read_spec import DurationWindow as DeclaredDurationWindow
from odyssey_fx.strategy.declarations.read_spec import ReadWindow as DeclaredWindow
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator

__all__ = [
    "AcceptanceService",
    "EvaluationOutcome",
    "RunOutcome",
    "SnapshotInputs",
    "acceptance_service",
    "build_conversion_record",
    "calendar_ref_of",
    "code_digest",
    "code_version",
    "compile_experiment_strategy",
    "env_digest",
    "evaluate_saved_run",
    "execute_run",
    "git_state",
    "lock_digest",
    "now_utc",
    "open_snapshot_inputs",
    "raw_bar_source",
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
    ) -> PendingSnapshot:
        """原ファイルを受け入れて暫定 snapshot を組み立てる（D03 §4 の 1〜8）。

        `targets` は `(銘柄, 時間足の id)` の列。読む順は呼び出し側が決めるが、識別子は
        列挙順に依存しない（各列を正規順序へ整列するため、D03 §3.7.1）。

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
class _StrategyMarketDataView:
    """戦略ランタイムへ渡す as-of ビュー（D03 §6.2、D05 §6.3）。

    `MarketDataView`（`strategy.runtime.ports`）を構造的に満たし、読み取りそのものは
    `AsOfView` に委ねる。**足す処理は履歴窓の型の言い換えだけ**である。

    **なぜ言い換えが要るか（仮置き。人間の決定が必要）**: 履歴窓の宣言型は D04 §6.2 が
    `strategy.declarations.read_spec` に定め（本数はコンパイル時に解決するため
    `int | ParameterRef`）、as-of ビューが受ける窓は D03 §6.2 が
    `marketdata.application.asof` に定めている（本数は解決済みの `int`）。**同じ名前の別の型**
    であり、どちらの型が境界を渡るかをどの設計文書も決めていない。`strategy` は
    `marketdata.application` を参照できず（契約 F2）、`marketdata` は `strategy` を参照
    できない（層順序）ため、両方を参照できる合成（`app`）で言い換える。恒久的な形は
    人間の決定を要する（PR 本文の仮置き事項）。

    本数が未解決（`ParameterRef`）の窓は拒否する。解決はコンパイラの仕事であり
    （D04 §12、D05 §5.3「ランタイムが解決済みの窓だけを受け取る」）、ここで既定値を
    当てはめると、宣言に無い本数で履歴を読むことになる。
    """

    view: AsOfView

    def _window(self, window: DeclaredWindow) -> BarsWindow | DurationWindow:
        """宣言の履歴窓を as-of ビューの履歴窓へ言い換える。"""
        if isinstance(window, DeclaredBarsWindow):
            if not isinstance(window.count, int):
                raise MarketDataValueError(
                    "the history window still refers to a parameter"
                    f" ({window.count}); the compiler resolves window sizes before the run"
                    " (D04 §12, D05 §5.3)"
                )
            return BarsWindow(count=window.count)
        if isinstance(window, DeclaredDurationWindow):
            return DurationWindow(duration=window.duration)
        raise MarketDataValueError(
            f"a history window must be a BarsWindow or DurationWindow, got {window!r}"
        )

    def latest_available(self, series: SeriesId, at: UtcTime) -> Bar | MissingInput:
        """判断時刻に対し期待される最新足（D03 §6.2）。"""
        return self.view.latest_available(series, at)

    def history(
        self,
        series: SeriesId,
        window: DeclaredWindow,
        at: UtcTime,
        *,
        end_offset_bars: int = 0,
    ) -> tuple[Bar, ...] | MissingInput:
        """履歴窓を読む（D03 §6.2）。"""
        return self.view.history(series, self._window(window), at, end_offset_bars=end_offset_bars)

    def bar(self, series: SeriesId, bar_start: UtcTime, at: UtcTime) -> Bar | MissingInput:
        """指定した足（D03 §6.2）。"""
        return self.view.bar(series, bar_start, at)

    def expected_latest_key(self, series: SeriesId, at: UtcTime) -> BarKey | None:
        """判断時刻に対し存在すべき最新足の鍵（D03 §6.2）。"""
        return self.view.expected_latest_key(series, at)

    def freshness(self, series: SeriesId, bar: Bar) -> UtcTime:
        """鮮度の基準時刻（D03 §6.2）。"""
        return self.view.freshness(series, bar)


@dataclass(frozen=True, slots=True)
class SnapshotInputs:
    """承認済み snapshot から読んだ、run の入力一式（D03 §6・§7）。"""

    snapshot: ReadableSnapshot
    allowed_partitions: frozenset[PartitionId]
    partition_bars: Mapping[PartitionId, tuple[Bar, ...]]
    schedules: Mapping[SeriesId, SeriesSchedule]

    def bars_of(self, series: SeriesId) -> tuple[Bar, ...]:
        """許可された partition にあるその系列の足（無ければ空）。"""
        return PartitionedBars(self.partition_bars, self.allowed_partitions).bars_or_empty(series)


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


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """1回の run の成果（CLI が表示に使う）。"""

    result: BacktestResult
    run_id: RunId
    directory: Path


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
    execution_schedule = inputs.schedules.get(experiment.execution_series)
    if execution_schedule is None:
        raise MarketDataValueError(
            f"the snapshot does not carry the execution series {experiment.execution_series}"
            " (D03 §6.3)"
        )

    market_data = _StrategyMarketDataView(
        view=AsOfView(
            snapshot=inputs.snapshot,
            allowed_partitions=inputs.allowed_partitions,
            schedules=inputs.schedules,
            partition_bars=inputs.partition_bars,
        )
    )
    execution_view = ExecutionSeriesView(
        snapshot=inputs.snapshot,
        series=experiment.execution_series,
        allowed_partitions=inputs.allowed_partitions,
        partition_bars=inputs.partition_bars,
        schedule=execution_schedule,
    )
    feed = build_feed(
        inputs.snapshot,
        inputs.allowed_partitions,
        inputs.partition_bars,
        inputs.schedules,
        experiment.run_interval,
        execution_series=frozenset({experiment.execution_series}),
    )
    levels = experiment.execution_policy.resolution_hierarchy.levels
    intrabar = (
        None
        if len(levels) < 2
        else _IntrabarBars(bars={level: inputs.bars_of(level) for level in levels})
    )

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
    identifier = run_id_of(config_digest, code, lock, environment)
    allocator = IdAllocator(identifier)

    output_sink = TraceOutputSink()
    context = EngineContext(experiment.account)
    runtime = StrategyEvaluator(
        compiled=compiled,
        registry=INITIAL_CATALOG,
        market_data=market_data,
        context=context,
        sink=output_sink,
        allocator=allocator,
    )
    commit, dirty = git_state(repo_root)
    use_case = RunBacktest(
        runtime=runtime,
        context=context,
        output_sink=output_sink,
        allocator=allocator,
        feed=feed,
        execution_series=execution_view,
        calendar=calendar,
        risk_policy=experiment.risk_policy,
        execution_policy=experiment.execution_policy,
        cost_model=experiment.cost_model,
        conversion_policy=experiment.conversion_policy,
        symbol_spec=spec,
        symbol_spec_ref=symbol_spec_ref,
        calendar_ref=calendar_ref,
        integrity=inputs.snapshot.report,
        trace_sink=FileSystemTraceSink(root=artifacts_root, run_id=identifier, replace=replace),
        result_writer=FileSystemResultWriter(root=artifacts_root),
        code_digest=code,
        lock_digest=lock,
        env_digest=environment,
        git_commit=commit,
        git_dirty=dirty,
        intrabar_series=intrabar,
        timeframe_refs=timeframe_refs,
    )
    result = use_case.run(config, compiled)
    return RunOutcome(
        result=result,
        run_id=identifier,
        directory=run_directory(artifacts_root, identifier),
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
    metric_set_version: int = METRIC_SET_VERSION,
) -> EvaluationOutcome:
    """保存済みの run を評価し、5表と評価 manifest を保存する（D07 §4・§8）。

    評価時のコードのダイジェストは**この層が算出して渡す**（D07 §9.2、Q5 決定）。
    パッケージのソース内容を読むのは入出力であり、`application` は入出力を持たない。
    """
    repository = FileSystemResultRepository(root=artifacts_root)
    result = repository.read_result(run_id)
    use_case = EvaluateRun(evaluation_code_digest=code_digest())
    report = use_case.evaluate(result, repository, metric_set_version)
    repository.write_evaluation(report, report.rows)
    return EvaluationOutcome(
        report=report,
        directory=evaluation_directory(artifacts_root, run_id, report.manifest.run_evaluation_id),
    )
