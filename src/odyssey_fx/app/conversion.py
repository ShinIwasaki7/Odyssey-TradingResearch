"""換算の snapshot の結線（D06 v1.20 案 §19.2〜§19.4・§19.8）。

決済通貨が口座通貨と違う run（EURUSD・GBPUSD・AUDUSD・USDCHF の円口座）は、執行の snapshot に
加えて換算の snapshot（同じ組の承認済みの USDJPY の snapshot）を読む。本モジュールは

1. 2 つの snapshot が同じ組であることの照合（§19.2 の 6 項目。仮置き C1-4）と、
2. 換算の脚の 1 分足の供給（`backtest.portfolio.conversion.ConversionQuotes` の実装）と、その
   能力検査の材料（`backtest.application.run_backtest.ConversionSupply`）の組み立て

を持つ。業務の規則（経路・率・古さ）は `backtest.portfolio.conversion` が持つ（D01 §7）。
読むのは承認済みの snapshot の manifest と、読み取りの関門を通した研究履歴の partition の足だけで、
価格は記録にも理由の文にも出さない。
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType

from odyssey_fx.backtest.application.run_backtest import ConversionSupply
from odyssey_fx.common.refs import SnapshotRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.application.mt5_observation import Mt5Observation
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot, readable_surface
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest

__all__ = [
    "ConversionSnapshotMismatch",
    "SnapshotConversionQuotes",
    "conversion_supply",
    "require_conversion_pairing",
]

#: 換算の脚の時間足（決定 (18) (a): 1 分足）。
_MINUTE_TIMEFRAME = "1m"
_MINUTE = timedelta(minutes=1)


class ConversionSnapshotMismatch(MarketDataValueError):
    """換算の snapshot が執行の snapshot と同じ組でない（D06 v1.20 案 §19.2 の照合。仮置き C1-4）。

    run を始めず、何も書かずに止める（run の保存先を作らない）。CLI では今の設定の誤り
    （カレンダーの食い違いと同じ `MarketDataValueError`）と同じく終了コード 1 になる。理由には
    食い違った項目（識別子・版・組の名前だけ）を出し、価格は出さない。
    """


def require_conversion_pairing(
    execution: SnapshotManifest,
    conversion: SnapshotManifest,
    *,
    calendar_id: str,
    calendar_version: int,
    execution_id: str,
    conversion_id: str,
) -> None:
    """執行の snapshot と換算の snapshot が同じ組であることを照合する（D06 v1.20 案 §19.2）。

    | # | 項目 | 合格の条件 |
    |---|---|---|
    | 1 | 経路 | どちらも MT5 経路（`conversion.mt5` がある） |
    | 2 | 組 | 時刻の規則の表の `variant` が同じ |
    | 3 | 時刻の規則の表 | 識別・版・SHA-256 が同じ |
    | 4 | 取引カレンダー | 識別と版が同じで、run に渡したカレンダーとも同じ |
    | 5 | 集約規則の版 | `aggregation_rule_version` が同じ |
    | 6 | 承認と読める区分 | 換算の snapshot が承認済み（研究履歴だけ読むのは読込側） |

    変換のコード版・取得記録の SHA-256・列対応の宣言の版・取得の範囲は照合しない（仮置き C1-4）。
    経路と系列の有無は能力検査（§19.8 の 1）が扱う。食い違いは `ConversionSnapshotMismatch`。
    """
    problems: list[str] = []
    exec_mt5 = execution.conversion.mt5
    conv_mt5 = conversion.conversion.mt5
    if exec_mt5 is None or conv_mt5 is None:
        problems.append(
            "route: both snapshots must be OANDA MT5 snapshots"
            f" (execution {'MT5' if exec_mt5 is not None else 'HistData'},"
            f" conversion {'MT5' if conv_mt5 is not None else 'HistData'})"
        )
    else:
        exec_table = exec_mt5.time_rule_table
        conv_table = conv_mt5.time_rule_table
        if exec_table.variant != conv_table.variant:
            problems.append(
                f"variant: execution {exec_table.variant.value},"
                f" conversion {conv_table.variant.value}"
            )
        if (exec_table.table_id, exec_table.version, exec_table.sha256) != (
            conv_table.table_id,
            conv_table.version,
            conv_table.sha256,
        ):
            problems.append(
                f"time rule table: execution {exec_table.table_id} v{exec_table.version}"
                f" ({exec_table.sha256}), conversion {conv_table.table_id} v{conv_table.version}"
                f" ({conv_table.sha256})"
            )
    exec_record = execution.conversion
    conv_record = conversion.conversion
    exec_calendar = (exec_record.calendar_id, exec_record.calendar_version)
    conv_calendar = (conv_record.calendar_id, conv_record.calendar_version)
    if exec_calendar != conv_calendar or conv_calendar != (calendar_id, calendar_version):
        problems.append(
            f"calendar: execution {exec_calendar[0]}@v{exec_calendar[1]}, conversion"
            f" {conv_calendar[0]}@v{conv_calendar[1]}, run {calendar_id}@v{calendar_version}"
        )
    if exec_record.aggregation_rule_version != conv_record.aggregation_rule_version:
        problems.append(
            f"aggregation rule: execution {exec_record.aggregation_rule_version},"
            f" conversion {conv_record.aggregation_rule_version}"
        )
    if not conversion.is_approved:
        problems.append("approval: the conversion snapshot is not approved")
    if problems:
        raise ConversionSnapshotMismatch(
            f"the conversion snapshot {conversion_id} does not pair with the execution snapshot"
            f" {execution_id}: {'; '.join(problems)} (D06 §19.2)"
        )


@dataclass(frozen=True, slots=True)
class _MinuteBars:
    """1 通貨ペアの 1 分足（開始時刻の昇順）と、それを読む snapshot。"""

    series: SeriesId
    snapshot_ref: SnapshotRef
    bars: tuple[Bar, ...]
    _starts: tuple[UtcTime, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.bars, key=lambda bar: bar.bar_start.value))
        object.__setattr__(self, "bars", ordered)
        object.__setattr__(self, "_starts", tuple(bar.bar_start for bar in ordered))

    def last_ending_by(self, moment: UtcTime) -> Bar | None:
        """終わりが `moment` 以下の最後の記録のある 1 分足。"""
        index = bisect.bisect_right(self._starts, moment - _MINUTE) - 1
        while index >= 0:
            bar = self.bars[index]
            if bar.bar_end <= moment:
                return bar
            index -= 1  # pragma: no cover - 1 分足の終わりは開始の 1 分後
        return None


@dataclass(frozen=True, slots=True)
class SnapshotConversionQuotes:
    """換算の脚の 1 分足の供給（`ConversionQuotes` の実装。D06 v1.20 案 §19.3・§19.4）。

    `symbols` は経路を探す集合（執行の snapshot と換算の snapshot の系列の通貨ペアの和集合）。
    脚の足は、換算の snapshot にある通貨ペアは換算の snapshot から、執行の通貨ペア（USDCHF の
    1 本目の脚）は執行の snapshot から、読み取りの関門を通した研究履歴の partition の 1 分足だけを
    読む（仮置き C1-19: 執行足・参照価格を流用しない）。

    `snapshots` は通貨ペアからその系列を持つ snapshot への対応で、1 分足の有無によらず持つ
    （1 分足の無い通貨ペアの脚も、能力検査の結果にどの snapshot を見たかを残す。D06 §19.8 の 1）。
    """

    symbols: frozenset[Symbol]
    minutes: Mapping[Symbol, _MinuteBars]
    fallback_timeframe: TimeframeRef
    snapshots: Mapping[Symbol, SnapshotRef] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "minutes", MappingProxyType(dict(self.minutes)))
        object.__setattr__(self, "snapshots", MappingProxyType(dict(self.snapshots)))

    def last_minute(self, symbol: Symbol, moment: UtcTime) -> Bar | None:
        found = self.minutes.get(symbol)
        return None if found is None else found.last_ending_by(moment)

    def series_of(self, symbol: Symbol) -> SeriesId:
        found = self.minutes.get(symbol)
        if found is not None:
            return found.series
        return SeriesId(symbol=symbol, timeframe=self.fallback_timeframe, basis=PriceBasis.BID)

    def snapshot_of(self, symbol: Symbol) -> SnapshotRef:
        found = self.minutes.get(symbol)
        if found is not None:
            return found.snapshot_ref
        ref = self.snapshots.get(symbol)
        if ref is None:
            raise MarketDataValueError(f"no snapshot supplies the series of {symbol} (D06 §19.4)")
        return ref


@dataclass(frozen=True, slots=True)
class ConversionSource:
    """換算の脚を読む snapshot 1 つ（読み取りの関門を通した足と許可集合）。

    `observation` は取得不足の区間を引く検査報告の索引（`None` なら `snapshot` の検査報告から
    作る）。最終検証の run は、研究履歴の snapshot の検査報告に、関所の許可の並びが指す封印の
    snapshot の検査報告を合わせた索引を渡す（足は研究履歴の partition と並びの partition の両方を
    `partition_bars` に持つ。D06 v1.20 案 §19.14）。
    """

    snapshot: ReadableSnapshot
    allowed_partitions: frozenset[PartitionId]
    partition_bars: Mapping[PartitionId, Sequence[Bar]]
    observation: Mt5Observation | None = None


def _minute_series(manifest: SnapshotManifest, symbol: Symbol) -> SeriesId | None:
    for record in manifest.series:
        series = record.series_id
        if (
            series.symbol == symbol
            and series.timeframe.id == _MINUTE_TIMEFRAME
            and series.basis is PriceBasis.BID
        ):
            return series
    return None


def conversion_supply(
    *,
    execution: ConversionSource,
    conversion: ConversionSource,
    fallback_timeframe: TimeframeRef,
) -> ConversionSupply:
    """執行の snapshot と換算の snapshot から、換算の脚の供給と能力検査の材料を作る。

    換算の snapshot にある通貨ペアは換算の snapshot から、それ以外（執行の通貨ペア）は執行の
    snapshot から 1 分足を読む。取得不足は各 snapshot の検査報告（`integrity_report.json`）から、
    その通貨ペアのすべての系列について区間と重なるものを返す（§19.8 の 2）。
    """
    sources: dict[Symbol, ConversionSource] = {}
    for source in (execution, conversion):
        for record in source.snapshot.manifest.series:
            sources[record.series_id.symbol] = source
    minutes: dict[Symbol, _MinuteBars] = {}
    observations: dict[SeriesId, Mt5Observation | None] = {}
    symbol_series: dict[SeriesId, tuple[SeriesId, ...]] = {}
    for symbol, source in sorted(sources.items(), key=lambda item: str(item[0])):
        manifest = source.snapshot.manifest
        series = _minute_series(manifest, symbol)
        if series is None:
            continue
        surface = readable_surface(source.partition_bars, source.allowed_partitions)
        minutes[symbol] = _MinuteBars(
            series=series,
            snapshot_ref=SnapshotRef(snapshot_id=manifest.snapshot_id()),
            bars=surface.bars_or_empty(series),
        )
        observations[series] = (
            source.observation
            if source.observation is not None
            else Mt5Observation.from_snapshot(source.snapshot)
        )
        symbol_series[series] = tuple(
            record.series_id for record in manifest.series if record.series_id.symbol == symbol
        )
    quotes = SnapshotConversionQuotes(
        symbols=frozenset(sources),
        minutes=minutes,
        fallback_timeframe=fallback_timeframe,
        snapshots={
            symbol: SnapshotRef(snapshot_id=source.snapshot.manifest.snapshot_id())
            for symbol, source in sources.items()
        },
    )

    def shortfall(series: SeriesId, interval: Interval) -> tuple[tuple[Interval, str], ...]:
        observation = observations.get(series)
        if observation is None:
            return ()
        seen: dict[tuple[str, str, str], tuple[Interval, str]] = {}
        for item in symbol_series.get(series, (series,)):
            for span, reasons in observation.shortfall_overlapping(item, interval):
                seen.setdefault((str(span.start), str(span.end), reasons), (span, reasons))
        return tuple(seen[key] for key in sorted(seen))

    return ConversionSupply(
        quotes=quotes,
        present=frozenset(item.series for item in minutes.values()),
        shortfall=shortfall,
    )
