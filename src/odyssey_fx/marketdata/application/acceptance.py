"""受入れユースケース（D03 §4）。

原ファイルから snapshot を作る手順を、決定論的な純粋関数として組み立てる。実際のファイル
読込・書き出しはポート（`RawBarSource` / `SnapshotStore`）の実装が行い、本モジュールは
「読んだ行をどう解釈し、何を検査し、どう分類し、どの識別子になるか」だけを決める。

手順（D03 §4）:

```text
1. 原ファイルの登録        sha256、行数、銘柄・時間足の推定（ファイル名から）
2. 列対応の適用            列対応の宣言で列を対応付ける。時刻の見た目から規約を推測しない
3. 正規化                  時刻: オフセット必須、UTC へ。価格: 文字列から Decimal
4. 構造検査                重複 / OHLC / タイムゾーン / 整列。重大な違反があれば受入れ失敗
5. カレンダー照合          欠落・余剰・銘柄間ずれ・DST を警告として報告
6. 上位足の生成            1h → 4h_ny17 / 1d_ny17
7. アクセス分類と partition  bar_end 基準で partition に分ける
8. 暫定 manifest 生成      暫定の識別子を計算する
9. 分類と確定              人間が警告を休場 / 欠損に分類し、最終の識別子を計算する
```

**決定論**（D03 §4）: 同じ原ファイル・設定・コード版・分類なら同じ最終識別子になる。
受入れ実行時刻（`created_at`）は引数で受け、識別には含めない（D03 §3.7.1）。原ファイルの
列挙順・検査の実行順を入れ替えても識別子は変わらない（各列を正規順序へ整列するため）。

**二段階フロー**（D03 §3.7.1）: 分類が空の状態で計算するのが暫定の識別子
（`provisional_id`）、人間の分類を記入した後に計算するのが最終の識別子（`snapshot_id`）。
分類が異なれば別 snapshot である。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from odyssey_fx.common.ids import SnapshotId
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.application.integrity import SeriesUnderCheck, check_all
from odyssey_fx.marketdata.application.ports import RawRow
from odyssey_fx.marketdata.domain.access import AccessBoundaries, AccessClass
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import IntegrityCheckFailed, MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    BasisDeclaration,
    ClosureDecision,
    ConversionRecord,
    LegacyAccessRecord,
    PartitionId,
    PartitionRecord,
    SeriesManifest,
    SnapshotManifest,
    SourceFile,
)
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition

__all__ = [
    "ColumnMapping",
    "PendingSnapshot",
    "RawFile",
    "build_pending_snapshot",
    "classify_partitions",
    "finalize",
    "normalize_rows",
    "provisional_id",
]


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """列対応の宣言（D03 §4 の 2、`configs/datasources/`）。

    原 CSV の先頭列は無名なので、`time_column` には adapters が付ける名前（既定は
    `timestamp`）を書く。時刻の規約は `time_convention` として明示し、値の見た目からは
    推測しない（上位設計書 §3.2）。初版が受ける規約は `explicit_offset_utc`（オフセットが
    明示された UTC）のみ。
    """

    time_column: str
    open_column: str
    high_column: str
    low_column: str
    close_column: str
    volume_column: str
    source_column: str
    time_convention: str = "explicit_offset_utc"

    def __post_init__(self) -> None:
        for name in (
            "time_column",
            "open_column",
            "high_column",
            "low_column",
            "close_column",
            "volume_column",
            "source_column",
            "time_convention",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise MarketDataValueError(f"ColumnMapping.{name} must be a non-empty str")
        if self.time_convention != "explicit_offset_utc":
            raise MarketDataValueError(
                f"unsupported time convention {self.time_convention!r};"
                " the initial version only accepts 'explicit_offset_utc' (D03 §4)"
            )


@dataclass(frozen=True, slots=True)
class RawFile:
    """受け入れる原ファイル1件（D03 §4 の 1）。

    銘柄と時間足はファイル名から推定した結果を呼び出し側が渡す（`app.cli` の責務）。
    `declared_basis` は人間の宣言であり、ファイルから検証できる事実ではない（D03 §2）。
    """

    path: str
    sha256: str
    symbol: Symbol
    timeframe: TimeframeRef
    declared_basis: PriceBasis

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise MarketDataValueError("RawFile.path must be a non-empty str")
        ContentDigest.sha256(self.sha256)  # 形式検査
        if not isinstance(self.symbol, Symbol):
            raise MarketDataValueError("RawFile.symbol must be a Symbol")
        if not isinstance(self.timeframe, TimeframeRef):
            raise MarketDataValueError("RawFile.timeframe must be a TimeframeRef")
        if not isinstance(self.declared_basis, PriceBasis):
            raise MarketDataValueError("RawFile.declared_basis must be a PriceBasis")

    @property
    def series(self) -> SeriesId:
        """このファイルが与える系列。"""
        return SeriesId(symbol=self.symbol, timeframe=self.timeframe, basis=self.declared_basis)


def _provenance_kind(raw_value: str) -> ProvenanceKind:
    """`source` 列の値を出所区分へ対応付ける（D03 §3.3）。"""
    try:
        kind = ProvenanceKind(raw_value)
    except ValueError as exc:
        raise MarketDataValueError(
            f"unknown source value {raw_value!r};"
            f" expected one of {[member.value for member in ProvenanceKind]}"
        ) from exc
    if kind is ProvenanceKind.AGGREGATED:
        raise MarketDataValueError(
            "raw files must not declare 'aggregated' as their source;"
            " that provenance is reserved for bars this platform generates (D03 §5)"
        )
    return kind


def _price(raw_value: str, label: str) -> Price:
    """文字列から価格を作る（float を経由しない、ADR-0012）。"""
    try:
        return Price(decimal_from_str(raw_value))
    except MarketDataValueError:  # pragma: no cover - Price は KernelValueError を出す
        raise
    except Exception as exc:
        raise MarketDataValueError(f"invalid {label} value {raw_value!r}: {exc}") from exc


def normalize_rows(
    raw_file: RawFile,
    rows: Sequence[RawRow],
    mapping: ColumnMapping,
    timeframe_def: TimeframeDefinition,
    calendar: TradingCalendar,
) -> tuple[Bar, ...]:
    """原の行を足へ正規化する（D03 §4 の 2〜3）。

    時刻はオフセット必須で UTC へ、価格と出来高は文字列から Decimal へ変換する
    （float を経由しない、ADR-0012）。足の区間は定義とカレンダーが決める期待区間であり、
    CSV の次の行との差からは推定しない（上位設計書 §3.3）。

    `available_at` は足の終了時刻に置く。通常の公開遅延と遅延シナリオの適用は、公開予定
    （`SeriesSchedule`）と遅延シナリオが別に行う（D03 §3.5・§3.6）。
    """
    if not isinstance(mapping, ColumnMapping):
        raise MarketDataValueError("normalize_rows requires a ColumnMapping")
    series = raw_file.series
    bars: list[Bar] = []
    for index, row in enumerate(rows):
        for column in (
            mapping.time_column,
            mapping.open_column,
            mapping.high_column,
            mapping.low_column,
            mapping.close_column,
            mapping.volume_column,
            mapping.source_column,
        ):
            if column not in row:
                raise MarketDataValueError(
                    f"{raw_file.path} row {index}: declared column {column!r} is missing;"
                    " the column mapping does not match the file (D03 §4 の 2)"
                )
        raw_time = row[mapping.time_column]
        # 原 CSV は `YYYY-MM-DD HH:MM:SS+00:00`（空白区切り）。`UtcTime.parse` は `T`
        # 区切りだけを受けるので、区切りだけを置き換えて厳密な検査に掛ける。値の見た目
        # からタイムゾーンを補うことはしない（オフセットがなければそのまま拒否される）。
        bar_start = UtcTime.parse(raw_time.replace(" ", "T", 1))
        interval = timeframe_def.expected_interval(calendar, bar_start)
        if interval is None:
            # カレンダー上存在しない時間帯の足。区間を切り詰められないので、整列上の区間
            # のまま取り込み、検査が `UNEXPECTED_BAR` として報告する（D03 §3.9）。
            interval = timeframe_def.boundaries(bar_start)
        if interval.start != bar_start:
            # 整列に合わない開始時刻。`IRREGULAR_INTERVAL` として報告できるよう、区間は
            # 「その時刻から整列上の終端まで」にする。
            interval = Interval(start=bar_start, end=interval.end)
        volume: Decimal = decimal_from_str(row[mapping.volume_column])
        bars.append(
            Bar(
                series=series,
                interval=interval,
                open=_price(row[mapping.open_column], "open"),
                high=_price(row[mapping.high_column], "high"),
                low=_price(row[mapping.low_column], "low"),
                close=_price(row[mapping.close_column], "close"),
                volume=volume,
                available_at=interval.end,
                provenance=Provenance(
                    kind=_provenance_kind(row[mapping.source_column]),
                    source_ref=raw_file.path,
                ),
            )
        )
    return tuple(bars)


def _provenance_counts(bars: Sequence[Bar]) -> tuple[tuple[str, int], ...]:
    """出所ごとの行数（manifest の `sources` に記録する）。"""
    counts: dict[str, int] = {}
    for bar in bars:
        counts[bar.provenance.kind.value] = counts.get(bar.provenance.kind.value, 0) + 1
    return tuple(sorted(counts.items(), key=lambda pair: pair[0]))


def classify_partitions(
    bars: Sequence[Bar], boundaries: AccessBoundaries
) -> Mapping[AccessClass, tuple[Bar, ...]]:
    """足をアクセス分類ごとに分ける（D03 §3.8、§4 の 7）。

    所属は **`bar_end` 基準**。区間をまたぐ足の後半の情報が前の区分へ漏れないようにする
    （2023-12-31 に始まり 2024-01-01 に終わる日足は封印期間に入る）。
    """
    grouped: dict[AccessClass, list[Bar]] = {}
    for bar in bars:
        grouped.setdefault(boundaries.classify(bar.bar_end), []).append(bar)
    return {
        access_class: tuple(sorted(group, key=lambda bar: bar.bar_start.value))
        for access_class, group in grouped.items()
    }


@dataclass(frozen=True, slots=True)
class PendingSnapshot:
    """暫定段階の成果（D03 §3.7.1 の 1）。

    分類（`closure_decisions`）が空の manifest、完全性検査の報告、partition ごとの足。
    `provisional_id` は分類が空の状態で計算した識別子であり、暫定 snapshot は as-of ビュー・
    公開フィードから読めない（D03 §3.7.1）。
    """

    manifest: SnapshotManifest
    report: IntegrityReport
    partition_bars: Mapping[PartitionId, tuple[Bar, ...]]

    @property
    def provisional_id(self) -> SnapshotId:
        """暫定の識別子（D03 §3.7.1）。"""
        return self.manifest.snapshot_id()


def _merge_findings(report: IntegrityReport, extra: Sequence[CheckResult]) -> IntegrityReport:
    """報告に追加の結果を統合する（D03 §4 の 5〜6）。

    同じ結果（D03 §3.7.1 の整列鍵が等しいもの）は1件にまとめる。上位足の生成と
    カレンダー照合が同じ欠落を別々に報告しても、記録は1件になる。並びは
    `IntegrityReport` が正規順序へ整えるので、統合の順序はダイジェストに影響しない。
    """
    merged: dict[tuple[str, str, str, str], CheckResult] = {
        result.sort_key(): result for result in report.results
    }
    for result in extra:
        if not isinstance(result, CheckResult):
            raise MarketDataValueError(
                f"aggregated_findings must contain CheckResult values, got {type(result).__name__}"
            )
        merged.setdefault(result.sort_key(), result)
    return IntegrityReport(results=tuple(merged.values()))


def _require_no_integrity_errors(report: IntegrityReport) -> None:
    """重大な違反が1件でもあれば受入れを中止する（D03 §4 の 4）。

    件数と種別をメッセージに含め、どの検査で落ちたかが実行ログだけで分かるようにする。
    価格は含めない（D03 §3.9）。
    """
    errors = report.errors
    if not errors:
        return
    counts: dict[str, int] = {}
    for result in errors:
        counts[result.kind.value] = counts.get(result.kind.value, 0) + 1
    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    raise IntegrityCheckFailed(
        f"the integrity check reported {len(errors)} error(s) ({summary});"
        " acceptance fails and no snapshot is produced (D03 §4 の 4)"
    )


def provisional_id(manifest: SnapshotManifest) -> SnapshotId:
    """分類が空の manifest から暫定の識別子を計算する（D03 §3.7.1 の 1）。"""
    if manifest.closure_decisions:
        raise MarketDataValueError(
            "a provisional id is computed before the closure decisions are recorded;"
            " this manifest already carries decisions (D03 §3.7.1)"
        )
    return manifest.snapshot_id()


def finalize(
    pending: PendingSnapshot,
    decisions: Sequence[ClosureDecision],
) -> SnapshotManifest:
    """人間の分類を記入して最終の manifest を作る（D03 §3.7.1 の 2、§4 の 9）。

    分類が確定した後の識別子が最終の `snapshot_id`。分類が異なれば別 snapshot である。
    未分類の警告が残っている場合は失敗させる（D03 §10 の `classify` コマンド）。

    重大な違反の検査もここで重ねて行う（D03 §4 の 4）。暫定段階で中断しているので通常は
    到達しないが、`PendingSnapshot` を別経路で組み立てた場合に、構造的に無効なデータが
    確定・承認へ進む抜け道を残さないため。
    """
    _require_no_integrity_errors(pending.report)
    decided = {(str(decision.series_id), str(decision.interval.start)) for decision in decisions}
    undecided = [
        result
        for result in pending.report.warnings
        if (str(result.series), str(result.interval.start)) not in decided
    ]
    if undecided:
        raise MarketDataValueError(
            f"{len(undecided)} warning(s) are still unclassified;"
            " every warning must be recorded as a closure or a data gap before the snapshot"
            " can be finalized (D03 §4 の 9)"
        )
    return pending.manifest.with_closure_decisions(tuple(decisions))


def build_pending_snapshot(
    *,
    created_at: UtcTime,
    raw_files: Sequence[RawFile],
    bars_by_file: Mapping[str, tuple[Bar, ...]],
    timeframe_defs: Mapping[str, TimeframeDefinition],
    calendar: TradingCalendar,
    boundaries: AccessBoundaries,
    basis_declaration: BasisDeclaration,
    conversion: ConversionRecord,
    partition_digests: Mapping[PartitionId, str],
    integrity_report_digest: ContentDigest,
    aggregated_bars: Mapping[SeriesId, tuple[Bar, ...]] | None = None,
    aggregated_findings: Sequence[CheckResult] = (),
    legacy_access: Sequence[LegacyAccessRecord] = (),
) -> PendingSnapshot:
    """暫定 manifest を組み立てる（D03 §4 の 1〜8）。

    `created_at` は受入れ実行時刻で、`app` が渡す。記録のみで識別には使わない
    （D03 §3.7）。`partition_digests` と `integrity_report_digest` は、実体を書き出した
    adapters が返したダイジェストで、本関数はそれを manifest に写すだけである。

    `bars_by_file` は原ファイルのパスごとの正規化済み足、`aggregated_bars` は生成した
    上位足（D03 §5）。どちらもアクセス分類ごとの partition へ分けて記録する。

    `aggregated_findings` は上位足の生成が返した報告（構成足が欠けて生成できなかった区間の
    `MISSING_EXPECTED_BAR`、D03 §5.2）。これを渡さないと、端の不完全な区間が報告から消え、
    「生成されなかった」という事実が記録に残らない。構造検査・カレンダー照合の結果と
    統合して1つの報告にする。

    **重大な違反があれば受入れを失敗させる**（D03 §4 の 4）。重複した開始時刻、OHLC の
    整合違反、整列に合わない開始時刻が1件でもあれば `IntegrityCheckFailed` で中断し、
    暫定 manifest を作らない。作ってしまうと、構造的に無効なデータがそのまま確定・承認
    できてしまう。
    """
    if not isinstance(created_at, UtcTime):
        raise MarketDataValueError("build_pending_snapshot requires a UtcTime created_at")

    sources: list[SourceFile] = []
    bars_by_series: dict[SeriesId, list[Bar]] = {}
    for raw_file in raw_files:
        bars = bars_by_file.get(raw_file.path)
        if bars is None:
            raise MarketDataValueError(f"no normalized bars were supplied for {raw_file.path}")
        sources.append(
            SourceFile(
                path=raw_file.path,
                sha256=raw_file.sha256,
                rows=len(bars),
                symbol=raw_file.symbol,
                timeframe=raw_file.timeframe,
                declared_basis=raw_file.declared_basis,
                provenance_counts=_provenance_counts(bars),
            )
        )
        bars_by_series.setdefault(raw_file.series, []).extend(bars)
    for aggregated_series, generated in (aggregated_bars or {}).items():
        bars_by_series.setdefault(aggregated_series, []).extend(generated)

    targets = [
        SeriesUnderCheck(
            series=checked_series,
            timeframe_def=_timeframe_def_for(checked_series, timeframe_defs),
            bars=tuple(checked_bars),
        )
        for checked_series, checked_bars in bars_by_series.items()
    ]
    report = _merge_findings(check_all(targets, calendar), aggregated_findings)
    _require_no_integrity_errors(report)

    series_manifests: list[SeriesManifest] = []
    partitions: list[PartitionRecord] = []
    partition_bars: dict[PartitionId, tuple[Bar, ...]] = {}
    for series, series_bars in bars_by_series.items():
        ordered = tuple(sorted(series_bars, key=lambda bar: bar.bar_start.value))
        grouped = classify_partitions(ordered, boundaries)
        partition_ids: list[PartitionId] = []
        for access_class, group in grouped.items():
            partition_id = PartitionId(series=series, access_class=access_class)
            partition_ids.append(partition_id)
            partition_bars[partition_id] = group
            digest_hex = partition_digests.get(partition_id)
            if digest_hex is None:
                raise MarketDataValueError(f"no partition digest was supplied for {partition_id}")
            partitions.append(
                PartitionRecord(
                    partition_id=partition_id,
                    interval=Interval(start=group[0].bar_start, end=group[-1].bar_end),
                    bar_count=len(group),
                    digest=ContentDigest.sha256(digest_hex),
                )
            )
        series_manifests.append(
            SeriesManifest(
                series_id=series,
                covered_interval=Interval(start=ordered[0].bar_start, end=ordered[-1].bar_end),
                bar_count=len(ordered),
                partitions=tuple(partition_ids),
            )
        )

    manifest = SnapshotManifest(
        created_at=created_at,
        basis_declaration=basis_declaration,
        sources=tuple(sources),
        conversion=conversion,
        series=tuple(series_manifests),
        partitions=tuple(partitions),
        integrity_report_ref=integrity_report_digest,
        closure_decisions=(),
        legacy_access=tuple(legacy_access),
    )
    return PendingSnapshot(manifest=manifest, report=report, partition_bars=partition_bars)


def _timeframe_def_for(
    series: SeriesId, timeframe_defs: Mapping[str, TimeframeDefinition]
) -> TimeframeDefinition:
    """系列に対応する時間足定義を引く。未登録なら構造エラー。"""
    definition = timeframe_defs.get(series.timeframe.id)
    if definition is None:
        raise MarketDataValueError(
            f"no timeframe definition was supplied for {series.timeframe.id!r}"
        )
    return definition


# 封印期間 partition の初期状態の判定は `application.access_log.initial_holdout_state` に
# 一本化した。以前ここにあった同趣旨の関数は、系列が一致するだけで「未観測」と判定し、
# partition の区間を完全に覆っているかを見ていなかった（ADR-0014 の fail-closed 違反）。
