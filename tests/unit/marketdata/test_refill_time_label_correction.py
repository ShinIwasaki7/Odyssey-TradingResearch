"""再取得ツールが原データを補正後のラベルで読むこと（D03 §14.4 の v1.19）。

入力 snapshot の manifest の `conversion` に時刻ラベルの補正規則が記録されていれば、再取得ツールは
原ファイルの行にだけ同じ規則を当てた足を使う（補充した足のファイルの行には当てない）。原ファイルの
行で数えた系列ごとの当てた本数が manifest の記録と違えば、何も書かずに止める。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from odyssey_fx.app.config import load_datasource
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.ports import RawFileContent, RawRow
from odyssey_fx.marketdata.application.refill_plan import REFILL_SOURCE_ROOT, load_raw_bars
from odyssey_fx.marketdata.domain.bar import Bar, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import TimeLabelCorrectionFailed
from odyssey_fx.marketdata.domain.series import PriceBasis
from odyssey_fx.marketdata.domain.snapshot import SnapshotManifest, SourceFile
from odyssey_fx.marketdata.domain.time_label_correction import (
    TimeLabelCorrectionRecord,
    TimeLabelCorrectionRule,
)
from tests.fixtures.refill import manifest_for
from tests.fixtures.synthetic import market
from tests.unit.marketdata.test_time_label_correction import (
    DATASOURCE_V3,
    HOUR,
    TRUE_WEEK,
    USDJPY_1H,
    WEEK,
    WINDOW,
    _raw_rows,
)

ORIGINAL = "data/raw/market/USDJPY_1h_merged.csv"
REFILLED = f"{REFILL_SOURCE_ROOT}/{'a' * 64}/USDJPY_1h_refill.csv"
RULE = TimeLabelCorrectionRule(
    rule_id="histdata_us_only_dst_weeks",
    rule_version=1,
    shift=HOUR,
    series=("USDJPY/1h/bid",),
    weeks=(WEEK,),
    daylight_zone="America/New_York",
    standard_zone="Europe/London",
)


class _Source:
    def __init__(self, files: dict[str, Sequence[RawRow]]) -> None:
        self.files = files

    def read_file(self, path: str) -> RawFileContent:
        return RawFileContent(sha256="0" * 64, rows=tuple(self.files[path]))


def _scene(
    recorded: int | None,
) -> tuple[SnapshotManifest, _Source, tuple[Bar, ...], Bar]:
    """原ファイル（対象の週を 1 時間早いラベルで持ち、水曜 12:00Z の足が欠ける）と、その欠落を
    補った補充した足のファイル（正しいラベル）を持つ manifest。"""
    truth = market.make_bars(USDJPY_1H, market.TF_1H, market.calendar(), WINDOW)
    missing = UtcTime.parse("2019-03-13T12:00:00Z")
    kept = tuple(bar for bar in truth if bar.bar_start != missing)
    (filled,) = (bar for bar in truth if bar.bar_start == missing)
    original_rows = _raw_rows(kept)
    refill_rows = [
        {**row, "source": ProvenanceKind.DUKASCOPY_REFILL.value}
        for row in market.csv_rows((filled,))
    ]
    sources = (
        SourceFile(
            path=ORIGINAL,
            sha256="0" * 64,
            rows=len(original_rows),
            symbol=market.USDJPY,
            timeframe=USDJPY_1H.timeframe,
            declared_basis=PriceBasis.BID,
        ),
        SourceFile(
            path=REFILLED,
            sha256="0" * 64,
            rows=1,
            symbol=market.USDJPY,
            timeframe=USDJPY_1H.timeframe,
            declared_basis=PriceBasis.BID,
        ),
    )
    manifest = manifest_for((), sources=sources)
    if recorded is not None:
        manifest = replace(
            manifest,
            conversion=replace(
                manifest.conversion,
                time_label_correction=TimeLabelCorrectionRecord(
                    rule=RULE, shifted_bar_counts=(("USDJPY/1h/bid", recorded),)
                ),
            ),
        )
    source = _Source({ORIGINAL: original_rows, REFILLED: refill_rows})
    return manifest, source, kept, filled


def _load(manifest: SnapshotManifest, source: _Source) -> tuple[Bar, ...]:
    bars = load_raw_bars(
        manifest,
        source,
        load_datasource(DATASOURCE_V3).mapping,
        market.TIMEFRAME_DEFS,
        market.calendar(),
        {market.USDJPY},
    )
    return bars[USDJPY_1H]


def _in_week(bars: Sequence[Bar]) -> int:
    return sum(1 for bar in bars if TRUE_WEEK.contains(bar.bar_start))


def test_raw_files_are_read_with_the_recorded_correction_and_refills_are_not() -> None:
    _, _, kept, filled = _scene(None)
    manifest, source, _, _ = _scene(_in_week(kept))
    bars = _load(manifest, source)
    # 原ファイルの足は正しいラベルに戻り、補充した足（正しいラベル）は動かない。
    assert [bar.bar_start for bar in bars] == sorted(
        [bar.bar_start for bar in (*kept, filled)], key=lambda moment: moment.value
    )
    by_start = {bar.bar_start: bar for bar in bars}
    assert by_start[filled.bar_start].provenance.kind is ProvenanceKind.DUKASCOPY_REFILL


def test_without_a_recorded_correction_the_labels_are_read_as_written() -> None:
    manifest, source, kept, _ = _scene(None)
    bars = _load(manifest, source)
    starts = {bar.bar_start for bar in bars}
    first_true = min(bar.bar_start for bar in kept if TRUE_WEEK.contains(bar.bar_start))
    assert first_true - HOUR in starts


def test_a_count_differing_from_the_manifest_stops_without_writing() -> None:
    _, _, kept, _ = _scene(None)
    manifest, source, _, _ = _scene(_in_week(kept) + 1)
    with pytest.raises(TimeLabelCorrectionFailed, match="different number"):
        _load(manifest, source)
