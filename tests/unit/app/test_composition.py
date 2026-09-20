"""構成ルートの単体テスト（D01 §7、D03 §3.2・§4）。

確かめること:

- 原ファイルの登録が持つ時間足の**版が設定の定義から来る**（固定値ではない）。
- 設定に定義の無い時間足は、その場で拒否される。
- 宣言していない出所の値を持つ行は受け入れない。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from odyssey_fx.app.composition import AcceptanceService
from odyssey_fx.app.config import load_datasource
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.application.ports import RawRow
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest
from odyssey_fx.marketdata.domain.timeframe_def import FixedUtcAlignment, TimeframeDefinition
from tests.fixtures.synthetic import market

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASOURCE_FILE = REPO_ROOT / "configs/datasources/legacy_merged_csv_v1.yaml"

#: 版 2 の1時間足定義。版が設定から来ることを確かめるために使う。
TF_1H_V2 = TimeframeDefinition(
    ref=TimeframeRef("1h", 2),
    nominal_length=timedelta(hours=1),
    alignment=FixedUtcAlignment(step=timedelta(hours=1)),
)


class _StubSource:
    """原データの読込ポートの代役。`read_rows` が返す行を差し替えられる。"""

    def __init__(self, rows: Sequence[RawRow] = ()) -> None:
        self.rows = tuple(rows)

    def read_rows(self, path: str) -> Sequence[RawRow]:
        return self.rows

    def file_sha256(self, path: str) -> str:
        return "0" * 64


class _StubStore:
    """snapshot の保存ポートの代役。構成の試験では書き出しも読み出しも行わない。

    ポート（`SnapshotStore`）の形をそのまま満たす。呼ばれたら失敗するので、構成の試験が
    知らないうちに入出力へ踏み込んでいれば、その場で分かる。
    """

    def write_partition(
        self, snapshot_dir: str, partition_id: PartitionId, bars: Iterable[Bar]
    ) -> str:
        raise NotImplementedError("構成の試験では partition を書かない")

    def read_partition(self, snapshot_dir: str, partition_id: PartitionId) -> Sequence[Bar]:
        raise NotImplementedError("構成の試験では partition を読まない")

    def write_manifest(self, snapshot_dir: str, manifest: SnapshotManifest) -> None:
        raise NotImplementedError("構成の試験では manifest を書かない")

    def read_manifest(self, snapshot_dir: str) -> SnapshotManifest:
        raise NotImplementedError("構成の試験では manifest を読まない")

    def write_integrity_report(self, snapshot_dir: str, report: IntegrityReport) -> str:
        raise NotImplementedError("構成の試験では検査報告を書かない")


def _service(
    timeframe_defs: dict[str, TimeframeDefinition], rows: Sequence[RawRow] = ()
) -> AcceptanceService:
    """代役のポートで受入れを組み立てる。"""
    return AcceptanceService(
        source=_StubSource(rows),
        store=_StubStore(),
        datasource=load_datasource(DATASOURCE_FILE),
        calendar=market.calendar(),
        timeframe_defs=timeframe_defs,
    )


# --- 時間足の版（D03 §3.2）--------------------------------------------------


def test_the_raw_file_takes_the_timeframe_version_from_the_configuration() -> None:
    """原ファイルの登録が持つ版は、設定の時間足定義の版になる。

    ここで版を固定すると、時間足定義の版を上げても系列の参照が古い版のままになり、
    manifest に記録した系列と実際に使った定義が食い違う。
    """
    service = _service({**market.TIMEFRAME_DEFS, "1h": TF_1H_V2})
    raw_file = service.raw_file(Symbol("USDJPY"), "1h")

    assert raw_file.timeframe == TimeframeRef("1h", 2)
    assert raw_file.timeframe.version == 2
    # 系列の識別も同じ版を持つ。
    assert raw_file.series.timeframe.version == 2


def test_the_raw_file_uses_version_one_when_the_configuration_declares_it() -> None:
    """実物の設定（版 1）ではこれまでどおり版 1 になる（振る舞いを変えていない）。"""
    service = _service(dict(market.TIMEFRAME_DEFS))
    assert service.raw_file(Symbol("USDJPY"), "1h").timeframe == TimeframeRef("1h", 1)


def test_an_undeclared_timeframe_is_rejected() -> None:
    """設定に定義の無い時間足は、原ファイルを登録する時点で拒否される。"""
    without_hourly = {key: value for key, value in market.TIMEFRAME_DEFS.items() if key != "1h"}
    service = _service(without_hourly)
    with pytest.raises(MarketDataValueError, match="1h"):
        service.raw_file(Symbol("USDJPY"), "1h")


def test_the_timeframe_definition_lookup_reports_what_is_available() -> None:
    """失敗の説明に、設定にある時間足の一覧を含める（どれが足りないか分かるように）。"""
    service = _service({"15m": market.TF_15M})
    with pytest.raises(MarketDataValueError, match="15m"):
        service.timeframe_definition("1d_ny17")


# --- 宣言していない出所（D03 §4 の 2）---------------------------------------


def test_a_row_with_an_undeclared_source_is_rejected() -> None:
    """`source` 列の値が宣言に無ければ受け入れない。

    宣言していない出所の行を黙って取り込むと、manifest の出所の記録が実データと食い違う。
    """
    row: RawRow = {
        "timestamp": "2022-01-06 10:00:00+00:00",
        "open": "150.0",
        "high": "150.5",
        "low": "149.5",
        "close": "150.2",
        "volume": "0",
        "source": "unknown_vendor",
    }
    service = _service(dict(market.TIMEFRAME_DEFS), rows=(row,))
    raw_file = service.raw_file(Symbol("USDJPY"), "1h")

    with pytest.raises(MarketDataValueError, match="unknown_vendor"):
        service.read_bars(raw_file, "USDJPY_1h_merged.csv")


def test_a_row_with_a_declared_source_is_accepted() -> None:
    """宣言にある出所の行は受け入れる（上の試験が空虚でないことの確認）。"""
    row: RawRow = {
        "timestamp": "2022-01-06 10:00:00+00:00",
        "open": "150.0",
        "high": "150.5",
        "low": "149.5",
        "close": "150.2",
        "volume": "0",
        "source": "histdata",
    }
    service = _service(dict(market.TIMEFRAME_DEFS), rows=(row,))
    raw_file = service.raw_file(Symbol("USDJPY"), "1h")

    bars = service.read_bars(raw_file, "USDJPY_1h_merged.csv")
    assert len(bars) == 1
    assert str(bars[0].bar_start) == "2022-01-06T10:00:00Z"
