"""円以外の決済通貨の候補の最終検証の人工の作業場（D06 v1.20 案 §19.14 の受入テスト CT-22〜CT-28）。

**人工データだけ**（実データ・成績・価格を読まない。D17 §6、D09 §9.8 の例外）。

- **研究期間の snapshot**（OANDA MT5 の経路の形。標準・候補の 2 組。研究履歴の区分）: 執行の通貨ペア
  （EURUSD）は T02 の人工の値の表の足（1 時間足・15 分足・日足）の系列だけを EURUSD に付け替えた
  もの（値は人工）と、MT5 の経路の run が求める 1 分足（15 分ごとの 1 本だけの疎な足）。換算の
  snapshot（USDJPY）は人工の一定の率の 1 分足（15 分ごとの 1 本。換算の古さ 15 分以内に収まる）。
- **封印の snapshot**（2024 年以降の 2 つの封印の区分。取引週で切る。標準・候補）: 執行の通貨ペアは
  同じ表の足、USDJPY は同じ一定の率の 1 分足（疎な足）。
- 研究期間の探索の実験は `experiment run` で 1 回走らせる（研究履歴の成果物。段 2 (f) が
  計算し直す）。

足の作り方（`quarter_minutes`）: 1 分足は各 15 分の最後の 1 分（:14・:29・:44・:59 に始まる分）
だけを作る。執行足（15 分足）の境目の処理点では、直前に終わった 1 分足の古さが 0 になる（D06 §19.5
の規則 9 の上限 15 分に収まる）。
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.partition_digest import partition_digest_hex
from odyssey_fx.marketdata.application.report_digest import integrity_report_digest_hex
from odyssey_fx.marketdata.domain.access import AccessClass, sealed_partition_period
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    Mt5AcquisitionRecord,
    Mt5ConversionRecord,
    PartitionId,
    PartitionRecord,
    SeriesManifest,
    SnapshotManifest,
)
from odyssey_fx.marketdata.domain.time_rule import TimeRuleTableRef, TimeRuleVariant
from tests.fixtures.acceptance import t02_market
from tests.fixtures.synthetic import market
from tests.fixtures.synthetic import snapshots as synthetic

__all__ = [
    "EURUSD",
    "FETCH_SHA",
    "MINUTE",
    "RATE",
    "RESEARCH_TABLE",
    "SEALED",
    "SEALED_TABLE",
    "USDJPY",
    "eurusd_bars",
    "quarter_minutes",
    "research_snapshot",
    "sealed_snapshot",
    "sealed_weeks",
    "table_ref",
]

EURUSD: Final = Symbol("EURUSD")
USDJPY: Final = market.USDJPY
#: 1 分足の時間足の参照（`configs/calendars/timeframes_v2.yaml` の `1m` 版 1）。
MINUTE: Final = TimeframeRef("1m", 1)
#: 人工の USDJPY の率（実データの価格ではない）。
RATE: Final = "150.000"
#: 人工の取得記録の SHA-256（同じ取得の回の印。仮置き C1-14 の照合の項目）。
FETCH_SHA: Final = "a" * 64
#: 人工の封印の足を作る区間（[2024-01-01, T_FIX) の取引週を覆う。`test_final_run` と同じ）。
SEALED: Final = Interval(
    start=UtcTime.parse("2024-01-01T00:00:00Z"), end=UtcTime.parse("2025-01-05T22:00:00Z")
)
#: 研究期間の時刻の規則の表（承認済みの一覧 `approved_time_rule_tables.yaml` の値）。
RESEARCH_TABLE: Final = (
    "oanda_mt5_time_rules",
    "70bb369bc606d8402b172672c07d75506951f86b1295c00344661a68247b3ec3",
)
#: 封印の時刻の規則の表（承認済みの一覧 `approved_time_rule_tables_sealed.yaml` の値）。
SEALED_TABLE: Final = (
    "oanda_mt5_time_rules_sealed",
    "f4052b591b663042e0a20e205978a3d55171be6c90e1339f8e12de661daa03e3",
)
_ONE_MINUTE: Final = timedelta(minutes=1)


def table_ref(table: tuple[str, str], variant: TimeRuleVariant) -> TimeRuleTableRef:
    return TimeRuleTableRef(table_id=table[0], version=1, sha256=table[1], variant=variant)


def sealed_weeks(interval: Interval = SEALED) -> list[Interval]:
    """`interval` の中の封印の partition の期間（取引週）。"""
    out: list[Interval] = []
    start = interval.start
    while True:
        period = sealed_partition_period(start)
        if period.end > interval.end:
            return out
        out.append(period)
        start = period.end


def _relabel(bars: Sequence[Bar], symbol: Symbol) -> tuple[Bar, ...]:
    return tuple(
        replace(
            bar,
            series=SeriesId(symbol=symbol, timeframe=bar.series.timeframe, basis=bar.series.basis),
        )
        for bar in bars
    )


def quarter_minutes(
    symbol: Symbol,
    interval: Interval,
    rate: str,
    calendar: TradingCalendar,
    *,
    holes: Sequence[Interval] = (),
) -> tuple[Bar, ...]:
    """区間のセッションの各 15 分の最後の 1 分に、4 本値が `rate` の 1 分足を作る（人工の値）。"""
    value = Price(decimal_from_str(rate))
    series = SeriesId(symbol=symbol, timeframe=MINUTE, basis=PriceBasis.BID)
    made: list[Bar] = []
    for session in calendar.sessions(interval):
        moment = session.start
        while moment < session.end:
            if moment.value.minute % 15 == 14 and not any(hole.contains(moment) for hole in holes):
                span = Interval(start=moment, end=moment + _ONE_MINUTE)
                made.append(
                    Bar(
                        series=series,
                        interval=span,
                        open=value,
                        high=value,
                        low=value,
                        close=value,
                        volume=Decimal(0),
                        available_at=span.end,
                        provenance=Provenance(
                            kind=ProvenanceKind.HISTDATA, source_ref="tests/fixtures/conversion"
                        ),
                    )
                )
            moment = moment + _ONE_MINUTE
    return tuple(made)


def eurusd_bars(
    hourly_window: Interval, quarter_window: Interval, *, minutes: Interval | None = None
) -> dict[str, tuple[Bar, ...]]:
    """T02 の人工の値の表から EURUSD の足（1 時間足・15 分足・日足と、`minutes` の疎な 1 分足）。"""
    calendar = market.calendar()
    hourly = t02_market.bars_for("1h", market.TF_1H, calendar, hourly_window)
    made = {
        "1h": hourly,
        "15m": t02_market.bars_for("15m", market.TF_15M, calendar, quarter_window),
        "1d_ny17": t02_market.aggregate(hourly, market.TF_1D_NY17, calendar),
    }
    out = {name: _relabel(bars, EURUSD) for name, bars in made.items()}
    if minutes is not None:
        out["1m"] = quarter_minutes(EURUSD, minutes, "149.000", calendar)
    return out


def _write(
    snapshots: Path,
    *,
    bars: Mapping[str, tuple[Bar, ...]],
    partition_of: object,
    variant: TimeRuleVariant,
    table: tuple[str, str],
    symbol: Symbol,
    acquired: Interval,
    fetch_sha: str,
    calendar_version: int,
    report: IntegrityReport,
    approved_at: str,
) -> str:
    """足・manifest・検査報告を確定済みの snapshot のディレクトリに書き、識別子を返す。"""
    store = ParquetSnapshotStore(root=snapshots)
    records: list[PartitionRecord] = []
    series_records: list[SeriesManifest] = []
    written: dict[PartitionId, tuple[Bar, ...]] = {}
    for timeframe in sorted(bars):
        series_bars = bars[timeframe]
        series = series_bars[0].series
        own: list[PartitionRecord] = []
        for partition, chosen in partition_of(series, series_bars):  # type: ignore[operator]
            if not chosen:
                continue
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
            approved_at=UtcTime.parse(approved_at),
            comment="人工の snapshot（足つき。換算の受入テスト）",
        ),
    )
    mt5 = Mt5ConversionRecord(
        route="oanda_mt5_m1",
        column_mapping="oanda_mt5_m1@1",
        fetch_manifest_sha256=fetch_sha,
        time_rule_table=table_ref(table, variant),
        acquisitions=(
            Mt5AcquisitionRecord(symbol=symbol, data_interval=acquired, acquired=(acquired,)),
        ),
    )
    manifest: SnapshotManifest = replace(
        base,
        conversion=replace(synthetic.CONVERSION, mt5=mt5, calendar_version=calendar_version),
        integrity_report_ref=ContentDigest.sha256(integrity_report_digest_hex(report)),
        provisional_report_ref=ContentDigest.sha256(integrity_report_digest_hex(report)),
    )
    snapshot_id = str(manifest.snapshot_id())
    (snapshots / snapshot_id).mkdir(parents=True, exist_ok=True)
    for partition, chosen in written.items():
        store.write_partition(snapshot_id, partition, chosen)
    store.write_integrity_report(snapshot_id, report)
    store.write_manifest(snapshot_id, manifest)
    return snapshot_id


def research_snapshot(
    snapshots: Path,
    bars: Mapping[str, tuple[Bar, ...]],
    variant: TimeRuleVariant,
    symbol: Symbol,
) -> str:
    """研究期間の OANDA MT5 の経路の snapshot（系列ごとに研究履歴の partition 1 つ）。"""

    def partition_of(
        series: SeriesId, series_bars: tuple[Bar, ...]
    ) -> list[tuple[PartitionId, tuple[Bar, ...]]]:
        return [
            (PartitionId(series=series, access_class=AccessClass.RESEARCH_HISTORY), series_bars)
        ]

    covered = [bar.interval for items in bars.values() for bar in items]
    acquired = Interval(
        start=min((item.start for item in covered), key=lambda value: value.value),
        end=max((item.end for item in covered), key=lambda value: value.value),
    )
    return _write(
        snapshots,
        bars=bars,
        partition_of=partition_of,
        variant=variant,
        table=RESEARCH_TABLE,
        symbol=symbol,
        acquired=acquired,
        fetch_sha="e" * 64,
        calendar_version=1,
        report=IntegrityReport(),
        approved_at="2026-10-20T00:00:00Z",
    )


def sealed_snapshot(
    snapshots: Path,
    bars: Mapping[str, tuple[Bar, ...]],
    variant: TimeRuleVariant,
    symbol: Symbol,
    *,
    acquisition_start: UtcTime,
    fetch_sha: str = FETCH_SHA,
    calendar_version: int = 1,
    report: IntegrityReport | None = None,
    skip_weeks: Sequence[UtcTime] = (),
    approved_at: str = "2026-11-20T00:00:00Z",
) -> str:
    """2024 年以降の封印の snapshot（系列ごと・取引週ごとの partition）。

    `skip_weeks` の週の partition は作らない。
    """

    def partition_of(
        series: SeriesId, series_bars: tuple[Bar, ...]
    ) -> list[tuple[PartitionId, tuple[Bar, ...]]]:
        out: list[tuple[PartitionId, tuple[Bar, ...]]] = []
        for period in sealed_weeks():
            if period.start in skip_weeks:
                continue
            access = (
                AccessClass.HOLDOUT_SINCE_2024
                if period.end <= acquisition_start
                else AccessClass.FORWARD_HOLDOUT
            )
            chosen = tuple(bar for bar in series_bars if period.start <= bar.bar_start < period.end)
            out.append(
                (PartitionId(series=series, access_class=access, period_start=period.start), chosen)
            )
        return out

    return _write(
        snapshots,
        bars=bars,
        partition_of=partition_of,
        variant=variant,
        table=SEALED_TABLE,
        symbol=symbol,
        acquired=SEALED,
        fetch_sha=fetch_sha,
        calendar_version=calendar_version,
        report=IntegrityReport() if report is None else report,
        approved_at=approved_at,
    )


def copy_tree(source: Path, target: Path) -> None:
    """作業場の写し（`shutil.copytree` の薄い包み。シンボリックリンクはそのまま写す）。"""
    shutil.copytree(source, target, symlinks=True)
