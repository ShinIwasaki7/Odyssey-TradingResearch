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
from odyssey_fx.marketdata.application.aggregation import aggregate
from odyssey_fx.marketdata.application.integrity import SeriesUnderCheck, check_all
from odyssey_fx.marketdata.application.partition_digest import partition_digest_hex
from odyssey_fx.marketdata.application.ports import RawRow
from odyssey_fx.marketdata.application.report_digest import integrity_report_digest_hex
from odyssey_fx.marketdata.application.snapshot_access import classification_mismatch
from odyssey_fx.marketdata.domain.access import AccessBoundaries, AccessClass
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import IntegrityCheckFailed, MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
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
    "reaccept_with_calendar",
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


@dataclass(frozen=True, slots=True)
class FinalizedSnapshot:
    """確定段階を経た snapshot（D03 §3.7.1 の 2）。

    `finalize()` の戻り値だけがこの型になる。承認（`approve()`）はこの型しか受け取らない
    ので、「暫定段階の manifest に承認だけ付ける」ことができない。D03 §3.7.1 は、分類を
    確定してから最終識別子を計算し、そのあとに承認を記入すると定めている。
    """

    manifest: SnapshotManifest

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SnapshotManifest):
            raise MarketDataValueError("FinalizedSnapshot.manifest must be a SnapshotManifest")
        if self.manifest.is_approved:
            raise MarketDataValueError(
                "a finalized snapshot carries no approval yet;"
                " the approval is recorded afterwards (D03 §3.7.1 の 2)"
            )

    @property
    def snapshot_id(self) -> SnapshotId:
        """最終の識別子（分類を確定した後の値）。"""
        return self.manifest.snapshot_id()

    @property
    def directory_name(self) -> str:
        """この snapshot を置くディレクトリ名（最終識別子）。"""
        return str(self.snapshot_id)


def approve(finalized: FinalizedSnapshot, approval: Approval) -> SnapshotManifest:
    """確定した snapshot に承認を記入する（D03 §3.7.1 の 2、§10 の `approve`）。

    確定段階を経た snapshot（`finalize()` の戻り値）だけを受け取る。暫定段階の manifest に
    承認を付ける経路を作らないためで、承認の有無だけを見る読み取り関門が「暫定なのに承認
    済み」の manifest を受け取ることを構造的に防ぐ。承認は識別子に影響しない。
    """
    if not isinstance(finalized, FinalizedSnapshot):
        raise MarketDataValueError("approve() requires a FinalizedSnapshot")
    if not isinstance(approval, Approval):
        raise MarketDataValueError("approve() requires an Approval")
    approved = finalized.manifest.with_approval(approval)
    if approved.snapshot_id() != finalized.snapshot_id:  # pragma: no cover - 承認は対象外
        raise MarketDataValueError(
            "recording the approval changed the snapshot id; the approval must not be part"
            " of the identity (D03 §3.7.1)"
        )
    return approved


def finalize(
    pending: PendingSnapshot,
    decisions: Sequence[ClosureDecision],
    *,
    original_report: IntegrityReport | None = None,
) -> FinalizedSnapshot:
    """人間の分類を記入して最終の manifest を作る（D03 §3.7.1 の 2、§4 の 9）。

    分類が確定した後の識別子が最終の `snapshot_id`。分類が異なれば別 snapshot である。
    未分類の警告が残っている場合は失敗させる（D03 §10 の `classify` コマンド）。

    **カレンダーを変えた分類**（D03 §4 の 9）では `original_report` に暫定段階の報告を
    渡す。分類で「休場だった」と判断してカレンダーへ追加し版を上げると、その欠落は新しい
    カレンダーの下では欠落でなくなるため、**再受入れした報告からは警告が消える**。消えた
    警告に対する分類を「対応する警告がない」と拒否すると、D03 §4 の 9 が定める主たる用途
    （休場 → カレンダーへ追加して版を上げる）がそもそも成立しない。

    そこで突き合わせを2段階にする。

    1. **元の報告**に対して、未分類の警告が無く、余分な分類も無いことを確かめる。分類は
       元の報告を見て人間が書いたものなので、正当性はここで判定する。
    2. **再受入れ後の報告**に対して、残っている警告がすべて分類に含まれることを確かめる。
       新しいカレンダーでも説明できない欠落を見落とさないため。こちらでは「余分な分類」を
       許容する（段階1で正当と確かめた分類だから）。

    `original_report` を渡さない通常の確定では、従来どおり1つの報告に対して両方を見る。

    重大な違反の検査もここで重ねて行う（D03 §4 の 4）。暫定段階で中断しているので通常は
    到達しないが、`PendingSnapshot` を別経路で組み立てた場合に、構造的に無効なデータが
    確定・承認へ進む抜け道を残さないため。
    """
    _require_no_integrity_errors(pending.report)
    if original_report is not None:
        _require_no_integrity_errors(original_report)

    # 分類と警告の対応は**区間全体**で取る。突き合わせの規則は読み取りの関門と共有する
    # （`classification_mismatch`）。片方だけが検査していると、確定を経ずに組み立てた
    # manifest が読み取り側をすり抜ける。
    basis = pending.report if original_report is None else original_report
    undecided, extraneous = classification_mismatch(basis, decisions)
    if undecided:
        raise MarketDataValueError(
            f"{len(undecided)} warning(s) are still unclassified;"
            " every warning must be recorded as a closure or a data gap before the snapshot"
            f" can be finalized (D03 §4 の 9): {list(undecided)}"
        )

    # 対応する警告のない分類も拒否する。余分な分類は識別子（`snapshot_id`）を変えるので、
    # 検査が見つけていない区間を人間が書き足せば、内容の同じ snapshot が別物になってしまう。
    if extraneous:
        raise MarketDataValueError(
            f"{len(extraneous)} closure decision(s) do not correspond to any reported"
            f" warning: {list(extraneous)}; classify only the intervals the integrity check"
            " reported (D03 §4 の 9)"
        )

    if original_report is not None:
        # 再受入れ後も残る警告は、新しいカレンダーでも説明できない欠落である。分類から
        # 漏れていれば確定させない。余分な分類はここでは見ない（段階1で確かめてある）。
        still_undecided, _ = classification_mismatch(pending.report, decisions)
        if still_undecided:
            raise MarketDataValueError(
                f"{len(still_undecided)} warning(s) remain after re-running the acceptance"
                " with the new calendar and are still unclassified; the new calendar does not"
                f" explain them (D03 §4 の 9): {list(still_undecided)}"
            )

    return FinalizedSnapshot(manifest=pending.manifest.with_closure_decisions(tuple(decisions)))


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
    aggregated_bars: Mapping[SeriesId, tuple[Bar, ...]] | None = None,
    aggregated_findings: Sequence[CheckResult] = (),
    legacy_access: Sequence[LegacyAccessRecord] = (),
) -> PendingSnapshot:
    """暫定 manifest を組み立てる（D03 §4 の 1〜8）。

    `created_at` は受入れ実行時刻で、`app` が渡す。記録のみで識別には使わない（D03 §3.7）。

    **ダイジェストは本関数が計算する**（D03 §3.7.1）。partition のダイジェストはその場で
    分けた足から、検査報告のダイジェストはその場で組み立てた報告から計算する。呼び出し元が
    渡した値を記録する形だと、実データや報告と食い違う値のまま確定・承認できてしまう。
    adapters は同じ算法で書き出すので、書いた内容と manifest の記録は必ず一致する。

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

    return _assemble_pending(
        created_at=created_at,
        bars_by_series={series: tuple(bars) for series, bars in bars_by_series.items()},
        sources=tuple(sources),
        timeframe_defs=timeframe_defs,
        calendar=calendar,
        boundaries=boundaries,
        basis_declaration=basis_declaration,
        conversion=conversion,
        aggregated_findings=aggregated_findings,
        legacy_access=legacy_access,
    )


def _assemble_pending(
    *,
    created_at: UtcTime,
    bars_by_series: Mapping[SeriesId, tuple[Bar, ...]],
    sources: tuple[SourceFile, ...],
    timeframe_defs: Mapping[str, TimeframeDefinition],
    calendar: TradingCalendar,
    boundaries: AccessBoundaries,
    basis_declaration: BasisDeclaration,
    conversion: ConversionRecord,
    aggregated_findings: Sequence[CheckResult] = (),
    legacy_access: Sequence[LegacyAccessRecord] = (),
) -> PendingSnapshot:
    """系列ごとの足から検査・partition 分け・暫定 manifest を組み立てる（D03 §4 の 4〜8）。

    受入れ（`build_pending_snapshot`）と、カレンダーを変えた再実行
    （`reaccept_with_calendar`）が共有する。どちらも「足が揃った後」の手順は同じで、違うのは
    足をどこから得るか（原ファイルを読むか、暫定 snapshot の partition から読み戻すか）
    だけである。1箇所にまとめることで、両者の検査・ダイジェストの算法がずれない。
    """
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
            # ダイジェストは**この場の足から**計算する。呼び出し元が渡した値をそのまま
            # 記録すると、実データと食い違う値のまま確定・承認できてしまう（D03 §3.7.1）。
            partitions.append(
                PartitionRecord(
                    partition_id=partition_id,
                    interval=Interval(start=group[0].bar_start, end=group[-1].bar_end),
                    bar_count=len(group),
                    digest=ContentDigest.sha256(partition_digest_hex(group)),
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
        sources=sources,
        conversion=conversion,
        series=tuple(series_manifests),
        partitions=tuple(partitions),
        integrity_report_ref=ContentDigest.sha256(integrity_report_digest_hex(report)),
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


def reaccept_with_calendar(
    pending: PendingSnapshot,
    *,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    boundaries: AccessBoundaries,
    aggregation_targets: Sequence[tuple[str, str]],
) -> PendingSnapshot:
    """暫定 snapshot に対して受入れの 5〜7 だけを新しいカレンダーで再実行する（D03 §4 の 9）。

    分類で「休場だった」と判断してカレンダーへ追加し版を上げたとき、報告と partition を
    作り直す必要がある。そのとき**原ファイルは読み直さない**。読み直すと、人間が分類の
    根拠にした報告には無かった内容——原ファイルの差し替え、価格の書き換え、設定の変更——が
    最終 snapshot に入りうる。とくに価格だけの変更は構造検査もカレンダー照合も素通りする
    ので、警告を1つも出さずに別のデータへ置き換わってしまう。

    そこで**暫定 snapshot の partition から読み戻した原系列の足を再利用**する。D03 §4 の 9
    が「5〜7 を再実行」と定めるのはこの意味であり、1〜4（原ファイルの登録・列対応・
    正規化・構造検査）はやり直さない。

    - 原系列の足（出所が `AGGREGATED` でないもの）だけを引き継ぐ。上位足は新しい
      カレンダーで作り直すので捨てる。
    - `sources`（原ファイルの sha256・行数・出所件数）は暫定 manifest のものをそのまま
      引き継ぐ。原ファイルを読まない以上、記録も変えてはならない。
    - `conversion` はカレンダーの識別と版だけを新しいものに置き換え、コード版・時刻規約・
      集約規則の版は引き継ぐ。
    - `created_at` も引き継ぐ（識別には使わないが、受入れの実行時刻は1つである）。

    `aggregation_targets` は上位足を作る組（`(構成足の id, 上位足の id)`）。構成に属する
    知識なので呼び出し側が渡す。
    """
    if not isinstance(pending, PendingSnapshot):
        raise MarketDataValueError("reaccept_with_calendar requires a PendingSnapshot")
    if not isinstance(calendar, TradingCalendar):
        raise MarketDataValueError("reaccept_with_calendar requires a TradingCalendar")

    source_bars = _source_series_bars(pending)
    if not source_bars:
        raise MarketDataValueError(
            "the provisional snapshot carries no source-series bars; there is nothing to"
            " re-check with the new calendar (D03 §4 の 9)"
        )
    _require_timeframes_match(source_bars, timeframe_defs)

    aggregated, findings = _aggregate_targets(
        source_bars,
        calendar=calendar,
        timeframe_defs=timeframe_defs,
        aggregation_targets=aggregation_targets,
    )

    manifest = pending.manifest
    return _assemble_pending(
        created_at=manifest.created_at,
        bars_by_series={**source_bars, **aggregated},
        sources=manifest.sources,
        timeframe_defs=timeframe_defs,
        calendar=calendar,
        boundaries=boundaries,
        basis_declaration=manifest.basis_declaration,
        conversion=ConversionRecord(
            code_version=manifest.conversion.code_version,
            time_convention=manifest.conversion.time_convention,
            aggregation_rule_version=manifest.conversion.aggregation_rule_version,
            calendar_id=calendar.id,
            calendar_version=calendar.version,
        ),
        aggregated_findings=findings,
        legacy_access=manifest.legacy_access,
    )


def _source_series_bars(pending: PendingSnapshot) -> dict[SeriesId, tuple[Bar, ...]]:
    """暫定 snapshot の partition から原系列の足を集める（上位足は除く）。

    上位足は本基盤が生成したもの（出所が `AGGREGATED`）なので、新しいカレンダーで作り
    直す。原系列の足だけが「原ファイルから来た事実」であり、これを引き継ぐ。
    """
    by_series: dict[SeriesId, list[Bar]] = {}
    for partition_id, bars in pending.partition_bars.items():
        for bar in bars:
            if bar.provenance.kind is ProvenanceKind.AGGREGATED:
                continue
            by_series.setdefault(partition_id.series, []).append(bar)
    return {
        series: tuple(sorted(bars, key=lambda bar: bar.bar_start.value))
        for series, bars in by_series.items()
    }


def _require_timeframes_match(
    source_bars: Mapping[SeriesId, tuple[Bar, ...]],
    timeframe_defs: Mapping[str, TimeframeDefinition],
) -> None:
    """引き継ぐ系列の時間足が、渡された定義と**版まで**一致することを確かめる。

    版が違う定義で再実行すると、足の境界の決め方が変わりうるのに、系列の記録は元の版の
    ままになる。人間が分類の根拠にした snapshot と別物になるので、その場で止める。
    """
    for series in sorted(source_bars, key=str):
        definition = timeframe_defs.get(series.timeframe.id)
        if definition is None:
            raise MarketDataValueError(
                f"no timeframe definition was supplied for {series.timeframe.id!r},"
                " which the provisional snapshot uses (D03 §4 の 9)"
            )
        if definition.ref != series.timeframe:
            raise MarketDataValueError(
                f"the timeframe definition for {series.timeframe.id!r} is"
                f" {definition.ref}, but the provisional snapshot was accepted with"
                f" {series.timeframe}; re-running with a different definition version would"
                " produce a snapshot the classification was not based on (D03 §4 の 9)"
            )


def _aggregate_targets(
    source_bars: Mapping[SeriesId, tuple[Bar, ...]],
    *,
    calendar: TradingCalendar,
    timeframe_defs: Mapping[str, TimeframeDefinition],
    aggregation_targets: Sequence[tuple[str, str]],
) -> tuple[dict[SeriesId, tuple[Bar, ...]], tuple[CheckResult, ...]]:
    """引き継いだ原系列から上位足を作り直す（D03 §4 の 6、§5）。"""
    generated: dict[SeriesId, tuple[Bar, ...]] = {}
    findings: list[CheckResult] = []
    for source_id, target_id in aggregation_targets:
        source_def = timeframe_defs.get(source_id)
        target_def = timeframe_defs.get(target_id)
        if source_def is None or target_def is None:
            raise MarketDataValueError(
                f"no timeframe definition was supplied for the aggregation"
                f" {source_id} -> {target_id} (D03 §5)"
            )
        for series in sorted(source_bars, key=str):
            if series.timeframe.id != source_id:
                continue
            target_series = SeriesId(
                symbol=series.symbol,
                timeframe=target_def.ref,
                basis=series.basis,
            )
            result = aggregate(
                source_bars[series],
                source_timeframe_def=source_def,
                target_series=target_series,
                target_timeframe_def=target_def,
                calendar=calendar,
            )
            generated[target_series] = result.bars
            findings.extend(result.findings)
    return generated, tuple(findings)
