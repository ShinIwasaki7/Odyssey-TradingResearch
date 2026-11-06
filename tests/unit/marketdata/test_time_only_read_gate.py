"""時刻だけの読み取り（`ParquetSnapshotStore.read_partition_times`。D18 §4.2）の読み取りの関門。

順 7 の残課題 R1（D17 §3.3、D19 §5。PR #147 で見送った軽微な指摘）: 時刻だけの読み取りが
封印・隔離の partition を、snapshot の保存の部品そのものの段で止めることを直接確かめる
（道具の前段の拒否は RP4-17 が確かめている）。

- OANDA MT5 の経路の封印の区分（`HOLDOUT_SINCE_2024`・`FORWARD_HOLDOUT`）は、ファイルが
  あっても開かずに止まる（時刻だけの読み取りは許可を受け取らない）。
- 旧データの 2024 年以降の区分（`LEGACY_HOLDOUT`・`QUARANTINED_UNASSIGNED`）は、ファイルが
  あっても開かずに止まる。manifest が読めず期間を確かめられないときも止まる。
- どの場合も `read_partition`（許可なし）と同じ型の拒否になる（docstring の「許可の検査は
  `read_partition` と同じ」）。
- 研究履歴の partition は時刻の列だけを返す（対照）。

人工の足だけを使う（価格は人工の値で、本テストは価格を読まない）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market
from tests.fixtures.synthetic import snapshots as synthetic

HOURLY = market.series()
CALENDAR = market.calendar()
RESEARCH = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
WEEK = UtcTime.parse("2024-01-07T22:00:00Z")
RESEARCH_WINDOW = Interval(
    start=UtcTime.parse("2023-12-26T00:00:00Z"), end=UtcTime.parse("2023-12-29T00:00:00Z")
)
SINCE_2024_WINDOW = Interval(
    start=UtcTime.parse("2024-01-08T00:00:00Z"), end=UtcTime.parse("2024-01-10T00:00:00Z")
)
TIME_COLUMNS = ("bar_start", "bar_end")


def _bars(window: Interval) -> tuple[Bar, ...]:
    return market.make_bars(HOURLY, market.TF_1H, CALENDAR, window)


def _stored(
    tmp_path: Path, bars: dict[PartitionId, Sequence[Bar]]
) -> tuple[ParquetSnapshotStore, str]:
    """人工の足で snapshot を書く（partition のファイル・検査報告・manifest）。"""
    store = ParquetSnapshotStore(root=tmp_path)
    manifest = synthetic.approved_for(bars)
    snapshot_id = str(manifest.snapshot_id())
    (tmp_path / snapshot_id).mkdir()
    for partition_id, items in bars.items():
        store.write_partition(snapshot_id, partition_id, items)
    store.write_integrity_report(snapshot_id, IntegrityReport())
    store.write_manifest(snapshot_id, manifest)
    return store, snapshot_id


def _spoil(store: ParquetSnapshotStore, snapshot_id: str, partition_id: PartitionId) -> None:
    """partition のファイルを Parquet でないバイト列にする（開こうとすれば別の例外で落ちる）。

    Parquet を開く関数の記録（`opened`）は `polars.read_parquet` だけを見るので、読み方が変わっても
    「開かない」を確かめられるよう、ファイルの側でも開けば失敗するようにしておく。
    """
    path = store.partition_file(snapshot_id, partition_id)
    assert path.is_file()  # ファイルが無いから止まるのではない
    path.write_bytes(b"not a parquet file")


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """保存の部品が partition の Parquet を開いたら、そのパスを記録する。"""
    calls: list[str] = []
    original = pl.read_parquet

    def spy(source: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(str(source))
        return original(source, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", spy)  # 保存の部品は呼ぶ時に `pl.read_parquet` を引く
    return calls


SEALED_CASES = [
    pytest.param(
        PartitionId(series=HOURLY, access_class=AccessClass.HOLDOUT_SINCE_2024, period_start=WEEK),
        id="holdout-since-2024",
    ),
    pytest.param(
        PartitionId(series=HOURLY, access_class=AccessClass.FORWARD_HOLDOUT, period_start=WEEK),
        id="forward-holdout",
    ),
]
FROZEN_CASES = [
    pytest.param(PartitionId(series=HOURLY, access_class=c), id=c.value)
    for c in (AccessClass.LEGACY_HOLDOUT, AccessClass.QUARANTINED_UNASSIGNED)
]


@pytest.mark.parametrize("sealed", SEALED_CASES)
def test_time_reader_stops_at_a_sealed_partition_without_opening_it(
    tmp_path: Path, opened: list[str], sealed: PartitionId
) -> None:
    store, snapshot_id = _stored(tmp_path, {sealed: _bars(SINCE_2024_WINDOW)})
    _spoil(store, snapshot_id, sealed)
    with pytest.raises(HoldoutAccessViolation, match="no permit was given"):
        store.read_partition_times(snapshot_id, sealed, TIME_COLUMNS)
    assert opened == []
    with pytest.raises(HoldoutAccessViolation):  # 許可なしの read_partition と同じ型
        store.read_partition(snapshot_id, sealed)
    assert opened == []


@pytest.mark.parametrize("frozen", FROZEN_CASES)
def test_time_reader_stops_at_legacy_data_from_2024_without_opening_it(
    tmp_path: Path, opened: list[str], frozen: PartitionId
) -> None:
    store, snapshot_id = _stored(tmp_path, {frozen: _bars(SINCE_2024_WINDOW)})
    _spoil(store, snapshot_id, frozen)
    with pytest.raises(HoldoutAccessViolation, match="frozen"):
        store.read_partition_times(snapshot_id, frozen, TIME_COLUMNS)
    assert opened == []
    with pytest.raises(HoldoutAccessViolation, match="frozen"):
        store.read_partition(snapshot_id, frozen)
    assert opened == []


@pytest.mark.parametrize("frozen", FROZEN_CASES)
def test_time_reader_stops_when_the_period_cannot_be_confirmed(
    tmp_path: Path, opened: list[str], frozen: PartitionId
) -> None:
    """manifest が読めなければ、旧データの区分の partition の期間を確かめられないので読まない。"""
    store, snapshot_id = _stored(tmp_path, {frozen: _bars(RESEARCH_WINDOW)})
    (tmp_path / snapshot_id / "manifest.json").unlink()
    with pytest.raises(HoldoutAccessViolation, match="cannot be confirmed"):
        store.read_partition_times(snapshot_id, frozen, TIME_COLUMNS)
    assert opened == []


def test_time_reader_reads_only_the_time_columns_of_a_research_partition(
    tmp_path: Path, opened: list[str]
) -> None:
    """対照: 研究履歴の partition は開いて、時刻の列だけを返す。"""
    bars = _bars(RESEARCH_WINDOW)
    store, snapshot_id = _stored(tmp_path, {RESEARCH: bars})
    times = store.read_partition_times(snapshot_id, RESEARCH, TIME_COLUMNS)
    assert set(times) == set(TIME_COLUMNS)
    assert times["bar_start"] == tuple(bar.bar_start for bar in bars)
    assert times["bar_end"] == tuple(bar.interval.end for bar in bars)
    assert opened == [str(store.partition_file(snapshot_id, RESEARCH))]
