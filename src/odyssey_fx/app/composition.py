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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import odyssey_fx
from odyssey_fx.app.config import DataSourceConfig
from odyssey_fx.common.refs import CodeDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.adapters.csv_source import CsvRawBarSource
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.acceptance import (
    PendingSnapshot,
    RawFile,
    build_pending_snapshot,
    normalize_rows,
)
from odyssey_fx.marketdata.application.aggregation import AGGREGATION_RULE_VERSION, aggregate
from odyssey_fx.marketdata.application.ports import RawBarSource, SnapshotStore
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES, AccessBoundaries
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckResult
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import ConversionRecord
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = [
    "AcceptanceService",
    "acceptance_service",
    "build_conversion_record",
    "code_version",
    "now_utc",
    "raw_bar_source",
    "snapshot_store",
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

    def raw_file(self, symbol: Symbol, timeframe_id: str) -> RawFile:
        """1件の原ファイルの登録を作る（D03 §4 の 1）。

        内容の sha256 はポート越しに読んで記録する。同じ原ファイルなら同じ snapshot の
        識別子になることを保証するため（D03 §3.7.1）。
        """
        name = self.datasource.file_name(symbol, timeframe_id)
        return RawFile(
            path=f"{self.datasource.root}/{name}",
            sha256=self.source.file_sha256(name),
            symbol=symbol,
            timeframe=self.timeframe_ref(timeframe_id),
            declared_basis=self.datasource.basis_declaration.value,
        )

    def read_bars(self, raw_file: RawFile, file_name: str) -> tuple[Bar, ...]:
        """1件の原ファイルを読んで足へ正規化する（D03 §4 の 2〜3）。

        `source` 列の値が宣言に無ければ拒否する。宣言していない出所の行を黙って取り込む
        と、manifest の出所の記録が実データと食い違う（D03 §3.7 の `provenance_counts`）。
        """
        rows = self.source.read_rows(file_name)
        for index, row in enumerate(rows):
            value = row.get(self.datasource.mapping.source_column, "")
            if value not in self.datasource.allowed_sources:
                raise MarketDataValueError(
                    f"{raw_file.path} row {index}: 出所 {value!r} は宣言に無い"
                    f"（{sorted(self.datasource.allowed_sources)}）。"
                    " 宣言していない出所の行は受け入れない（D03 §4 の 2）"
                )
        return normalize_rows(
            raw_file,
            rows,
            self.datasource.mapping,
            self.timeframe_definition(raw_file.timeframe.id),
            self.calendar,
        )

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
            raw_file = self.raw_file(symbol, timeframe_id)
            bars = self.read_bars(raw_file, self.datasource.file_name(symbol, timeframe_id))
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
