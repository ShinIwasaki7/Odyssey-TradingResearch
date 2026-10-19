"""MT5 経路の負のスプレッド（D03 §15.3 の 2・4、§15.6 の 2・3 v1.25、D12-131、D12 §12.8 の決定）。

確かめること:

- 負のスプレッドは受入れを失敗させず、`NEGATIVE_SPREAD`（WARN、記録のみ。分類は要求しない）として
  連続区間ごとに 1 件載る。隣り合う UTC の分の足がどちらも負のときだけつなぐ。
- 数えるのは snapshot に入る 1 分足だけ（候補の組でカレンダーの外に出た足、セッション外のデータ
  異常として除いた足は数えない。確定段階の作り直しで数え直す）。
- 1 分足の partition に印の列 `negative_spread` が入り、`spread` の原値は残る。印は読み込みで
  `spread` と突き合わせる。
- 負のスプレッドが無いときの検査報告と上位足の partition のダイジェストは v1.24 の実装と同じで、
  HistData 経路の partition のダイジェストも変わらない。

人工データだけを使う（実価格・成績は読まない）。
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.acceptance import PendingSnapshot
from odyssey_fx.marketdata.application.mt5_acceptance import Mt5RawRow, reaccept_mt5
from odyssey_fx.marketdata.application.partition_digest import (
    MINUTE_COLUMNS,
    partition_digest_hex,
    partition_row,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import MinuteSource
from odyssey_fx.marketdata.domain.classification import CLASSIFIABLE_KINDS
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind, Severity
from odyssey_fx.marketdata.domain.time_rule import TimeRuleVariant
from tests.fixtures.mt5 import market as m5
from tests.fixtures.synthetic import market

CALENDAR = market.calendar()
WEEK = Interval(
    start=UtcTime.parse("2026-01-04T22:00:00Z"), end=UtcTime.parse("2026-01-09T22:00:00Z")
)


def _at(text: str) -> UtcTime:
    return UtcTime.parse(f"2026-01-06T{text}:00Z")


def _span(start: str, end: str) -> Interval:
    return Interval(start=_at(start), end=_at(end))


def _negative(pending: PendingSnapshot) -> list[tuple[Interval, str]]:
    return [
        (r.interval, dict(r.detail)["minutes"])
        for r in pending.report.results
        if r.kind is CheckKind.NEGATIVE_SPREAD
    ]


def _minute_sources(pending: PendingSnapshot) -> dict[UtcTime, MinuteSource]:
    return {
        bar.bar_start: bar.minute
        for pid, bars in pending.partition_bars.items()
        if pid.series.timeframe.id == "1m"
        for bar in bars
        if bar.minute is not None
    }


def _out_of_session_row(moment: UtcTime, spread: int) -> Mt5RawRow:
    return Mt5RawRow(
        api_time=int(moment.value.timestamp()) + m5.OFFSET_HOURS * 3600,
        open="150.000",
        high="150.000",
        low="150.000",
        close="150.000",
        spread=spread,
    )


def test_negative_spreads_are_reported_per_run_without_failing_the_acceptance(
    tmp_path: Path,
) -> None:
    """D12-131: 負のスプレッドだけでは受入れを失敗させず、連続区間ごとに WARN（記録のみ）で残す。

    10:00・10:01 は 1 区間（2 分）。10:02 は負でないので 10:03 は別の区間。10:05 と 10:07 の間の
    10:06 は無記録なので、つながず別の区間にする（D03 §15.6 の 2）。
    """
    table = m5.write_rule_files(tmp_path)
    spreads = {_at(t): -2 for t in ("10:00", "10:01", "10:03", "10:05", "10:07")}
    rows = m5.minute_rows(CALENDAR, WEEK, unrecorded=(_span("10:06", "10:07"),), spreads=spreads)
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])

    assert _negative(pending) == [
        (_span("10:00", "10:02"), "2"),
        (_span("10:03", "10:04"), "1"),
        (_span("10:05", "10:06"), "1"),
        (_span("10:07", "10:08"), "1"),
    ]
    found = [r for r in pending.report.results if r.kind is CheckKind.NEGATIVE_SPREAD]
    assert {r.severity for r in found} == {Severity.WARN}
    assert {r.series.timeframe.id for r in found} == {"1m"}
    # spread の値は報告に書かない（detail は分数だけ）。
    assert {tuple(key for key, _ in r.detail) for r in found} == {("minutes",)}
    assert CheckKind.NEGATIVE_SPREAD not in CLASSIFIABLE_KINDS
    assert not pending.report.errors
    # 分類が空のまま確定・承認・読み取りの関門を通る（分類を要求しない）。
    m5.readable(pending)
    # 原値は 1 分足に残り、上位足は今のまま作られる（負の分も記録のある分）。
    sources = _minute_sources(pending)
    assert sources[_at("10:00")].spread == -2
    assert sources[_at("10:00")].negative_spread
    assert not sources[_at("10:02")].negative_spread


def test_zero_spread_is_not_negative(tmp_path: Path) -> None:
    """0 は負ではない（`spread` < 0 だけが印の対象）。"""
    table = m5.write_rule_files(tmp_path)
    rows = m5.minute_rows(CALENDAR, WEEK, spreads={_at("10:00"): 0})
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])
    assert _negative(pending) == []
    assert not _minute_sources(pending)[_at("10:00")].negative_spread


def test_only_bars_in_the_snapshot_are_counted_and_the_rerun_recounts_them(
    tmp_path: Path,
) -> None:
    """追加 A の決定（snapshot に入る 1 分足だけ）: 標準の組の休場帯の足は暫定 snapshot に入るので
    数え、セッション外のデータ異常として除いた作り直しの最終の報告では数えない。"""
    table = m5.write_rule_files(tmp_path)
    saturday = UtcTime.parse("2026-01-10T03:00:00Z")
    rows = (*m5.minute_rows(CALENDAR, WEEK), _out_of_session_row(saturday, -1))
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])
    saturday_run = Interval(start=saturday, end=UtcTime.parse("2026-01-10T03:01:00Z"))
    assert _negative(pending) == [(saturday_run, "1")]
    (unexpected,) = [r for r in pending.report.results if r.kind is CheckKind.UNEXPECTED_BAR]
    rerun = reaccept_mt5(
        pending,
        calendar=CALENDAR,
        timeframe_defs=m5.TIMEFRAME_DEFS,
        targets=m5.TARGETS,
        boundaries=m5.BOUNDARIES,
        excluded={(unexpected.series, unexpected.interval)},
    )
    assert _negative(rerun) == []


def test_bars_left_out_by_the_candidate_variant_are_not_counted(tmp_path: Path) -> None:
    """追加 A の決定: 候補の組でカレンダーの外に出た足は partition に入らないので数えない。"""
    table = m5.write_rule_files(tmp_path)
    friday_after_close = UtcTime.parse("2026-01-09T22:30:00Z")
    rows = (
        *m5.minute_rows(CALENDAR, WEEK, spreads={_at("10:00"): -1}),
        _out_of_session_row(friday_after_close, -1),
    )
    pending = m5.build_pending(
        CALENDAR, table, [m5.MonthSpec(2026, 1, rows)], variant=TimeRuleVariant.CANDIDATE
    )
    assert _negative(pending) == [(_span("10:00", "10:01"), "1")]


def test_the_minute_partition_carries_the_negative_spread_column(tmp_path: Path) -> None:
    """D03 §15.6 の 3 v1.25: 1 分足の partition の列の最後に `negative_spread` が入り、書いて
    読み戻すと同じ足（原値 `spread` を含む）になる。印が `spread` と食い違う行・印の列が無い
    partition は読み込みで拒否する。"""
    table = m5.write_rule_files(tmp_path)
    rows = m5.minute_rows(CALENDAR, WEEK, spreads={_at("10:00"): -4})
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])
    (pid,) = [pid for pid in pending.partition_bars if pid.series.timeframe.id == "1m"]
    bars = pending.partition_bars[pid]
    assert MINUTE_COLUMNS == ("api_time", "time_rule_row", "spread", "negative_spread")
    negative_row = partition_row(next(bar for bar in bars if bar.bar_start == _at("10:00")))
    assert negative_row[-2:] == ("-4", "true")
    assert partition_row(bars[0])[-1] == "false"

    store = ParquetSnapshotStore(root=tmp_path / "snapshots")
    digest = store.write_partition("p", pid, bars)
    assert store.read_partition("p", pid) == tuple(bars)
    assert digest == partition_digest_hex(bars)

    (path,) = (tmp_path / "snapshots" / "p").rglob("*.parquet")
    frame = pl.read_parquet(path)
    assert frame.columns[-1] == "negative_spread"
    flipped = frame.with_columns(
        pl.when(pl.col("negative_spread") == "true")
        .then(pl.lit("false"))
        .otherwise(pl.col("negative_spread"))
        .alias("negative_spread")
    )
    flipped.write_parquet(path)
    with pytest.raises(MarketDataValueError, match="does not match"):
        store.read_partition("p", pid)
    frame.drop("negative_spread").write_parquet(path)
    with pytest.raises(MarketDataValueError, match="no negative_spread column"):
        store.read_partition("p", pid)


#: v1.24 の実装（main 22861ec）で、負のスプレッドの無い人工データ（`WEEK` の 1 週、spread 3）
#: から作った暫定 snapshot の検査報告のダイジェストと、上位足の partition のダイジェスト。
_V124_REPORT = "b559172cbd83ec994cf6513da94f2a357664811476968c88718f169cd2163dbe"
_V124_UPPER = {
    "15m": "f29236cd52982e4ac6142bcf6214c06774c458cabe4e9f0c18f3388c792b8d39",
    "1h": "6f570fcdc272ec86be2d9d36128a031c968af7e4f25415ae1eb5402716e0eefd",
    "4h_ny17": "db4044799c18762120b8fefda7459bea79894fd2d79e163c66b7b9adbb4cbd8e",
    "1d_ny17": "ca11bd78834e2ab6018b262d49bed5bd1f64dda387326bba484bdf1bc8addeb5",
}
#: 同じ人工データの 1 分足の partition の v1.24 のダイジェスト（印の列が無い）。v1.25 で変わる。
_V124_MINUTE = "b90430203042b1e478c54022047a2ac52d6dc629ce0eb97cef489d6e3778f5c1"


def test_without_negative_spreads_only_the_minute_partition_digest_changes(
    tmp_path: Path,
) -> None:
    """D12 §12.8 の影響: 負のスプレッドが無ければ検査報告は v1.24 と同じバイト列（ダイジェスト）で、
    上位足の partition も同じ。変わるのは印の列を足した 1 分足の partition のダイジェストだけ。"""
    table = m5.write_rule_files(tmp_path)
    pending = m5.build_pending(
        CALENDAR, table, [m5.MonthSpec(2026, 1, m5.minute_rows(CALENDAR, WEEK))]
    )
    assert pending.manifest.integrity_report_ref.hex == _V124_REPORT
    digests = {
        record.partition_id.series.timeframe.id: record.digest.hex
        for record in pending.manifest.partitions
        if record.partition_id.access_class is AccessClass.RESEARCH_HISTORY
    }
    assert {tf: digests[tf] for tf in _V124_UPPER} == _V124_UPPER
    assert digests["1m"] != _V124_MINUTE


#: HistData 経路の人工の 1 時間足の partition のダイジェスト（v1.24 の実装で計算した値）。
_HISTDATA_DIGEST = "bc4b358ca8f09780c86c63588758039d273927a2d4befe7b1ad6e20b72931744"


def test_the_histdata_partition_digest_does_not_change() -> None:
    """1 分足の属性を持たない足（HistData 経路）の行とダイジェストは変わらない。"""
    window = Interval(
        start=UtcTime.parse("2026-01-13T22:00:00Z"), end=UtcTime.parse("2026-01-14T22:00:00Z")
    )
    bars = market.make_bars(market.series(), market.TF_1H, CALENDAR, window)
    assert all(bar.minute is None for bar in bars)
    assert partition_digest_hex(bars) == _HISTDATA_DIGEST
