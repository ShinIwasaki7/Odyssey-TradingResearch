"""補充分の書き出しの規則の単体テスト（D03 §14.6・§14.7・§14.10・§14.11、§4 の v1.15）。

- 補充した足のファイルの書式（原データと同じ列、桁の分だけの小数、出来高 0、出所）。
- 補充分のファイルの記録の形（足のファイルの名前と系列、`validation.json`）。
- 取得記録の検証の結果の行が構造的な記録を持ち、読み戻せる。
- 受入れが補充した足を原系列に合わせる規則（重複は失敗、補充分だけの系列・別の出所は拒否）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.acceptance import RawFile, merge_refill_bars
from odyssey_fx.marketdata.application.refill_finalize import bars_csv, refill_ids_in_sources
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.errors import IntegrityCheckFailed, MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.refill import ValidationRecord, journal_entry_from_payload
from odyssey_fx.marketdata.domain.refill_manifest import (
    RefillFileRecord,
    RefillManifest,
    RefillValidationRecord,
    bar_file_name,
    refill_id_of,
)
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from tests.fixtures.refill import USDJPY_1H, USDJPY_15M, provider_ref, raw_bars
from tests.fixtures.synthetic import market

AT = UtcTime.parse("2026-10-01T00:00:00Z")


def _bar(
    series: SeriesId, start: str, *, kind: ProvenanceKind = ProvenanceKind.DUKASCOPY_REFILL
) -> Bar:
    definition = market.TF_15M if series.timeframe.id == "15m" else market.TF_1H
    interval = definition.boundaries(UtcTime.parse(start))
    return Bar(
        series=series,
        interval=interval,
        open=Price(decimal_from_str("1.10001")),
        high=Price(decimal_from_str("1.1002")),
        low=Price(decimal_from_str("1.1")),
        close=Price(decimal_from_str("1.10010")),
        volume=decimal_from_str("0"),
        available_at=interval.end,
        provenance=Provenance(kind=kind, source_ref="x"),
    )


def test_the_bar_file_uses_the_raw_columns_and_fixed_decimals() -> None:
    eurusd = market.series(market.EURUSD, "15m")
    content = bars_csv(
        [_bar(eurusd, "2020-11-30T01:15:00Z"), _bar(eurusd, "2020-11-30T01:00:00Z")],
        provider_ref().settings.symbol(market.EURUSD),
    ).decode("utf-8")
    assert content.splitlines() == [
        ",open,high,low,close,volume,source",
        "2020-11-30 01:00:00+00:00,1.10001,1.10020,1.10000,1.10010,0,dukascopy_refill",
        "2020-11-30 01:15:00+00:00,1.10001,1.10020,1.10000,1.10010,0,dukascopy_refill",
    ]
    assert content.endswith("\n")


def test_bar_files_are_named_by_series_and_must_match_it() -> None:
    assert bar_file_name(USDJPY_15M) == "USDJPY_15m_refill.csv"
    with pytest.raises(MarketDataValueError, match="not the bar file name"):
        RefillFileRecord(name="USDJPY_1h_refill.csv", sha256="0" * 64, series=USDJPY_15M, rows=1)
    with pytest.raises(MarketDataValueError, match="no series"):
        RefillFileRecord(name="validation.json", sha256="0" * 64, series=USDJPY_15M, rows=1)
    with pytest.raises(MarketDataValueError):
        RefillFileRecord(name="refill_manifest.json", sha256="0" * 64, series=None, rows=None)


def test_the_refill_id_depends_on_the_code_and_rule_versions() -> None:
    plan_id = "1" * 64
    assert refill_id_of(plan_id, [], "refill_ticks_v1", "a") != refill_id_of(
        plan_id, [], "refill_ticks_v1", "b"
    )
    assert refill_id_of(plan_id, [], "refill_ticks_v1", "a") != refill_id_of(
        plan_id, [], "refill_ticks_v2", "a"
    )


def test_a_failed_validation_row_keeps_its_details() -> None:
    record = ValidationRecord(
        passed=False,
        reasons=("no bar was built",),
        at=AT,
        details={"not_built": [{"series": "USDJPY/1h/bid", "reason": "PROVIDER_EMPTY"}]},
    )
    assert journal_entry_from_payload(record.payload()) == record
    with pytest.raises(TypeError):
        record.details["x"] = 1  # type: ignore[index]


def test_refill_ids_are_read_from_source_paths() -> None:
    paths = (
        "data/raw/market/USDJPY_1h_merged.csv",
        f"data/raw/market/refill/{'b' * 64}/USDJPY_1h_refill.csv",
        f"data/raw/market/refill/{'a' * 64}/USDJPY_15m_refill.csv",
        f"data/raw/market/refill/{'a' * 64}/USDJPY_1h_refill.csv",
    )
    assert refill_ids_in_sources(paths) == ("a" * 64, "b" * 64)


# --- 受入れが補充した足を合わせる（D03 §4 の v1.15）-------------------------------------------


def _raw_file(series: SeriesId, name: str = "refill.csv") -> RawFile:
    return RawFile(
        path=f"data/raw/market/refill/{'a' * 64}/{name}",
        sha256="0" * 64,
        symbol=series.symbol,
        timeframe=series.timeframe,
        declared_basis=PriceBasis.BID,
    )


def test_refilled_bars_join_their_raw_series_in_time_order() -> None:
    original = {series: tuple(bars) for series, bars in raw_bars().items()}
    added = _bar(USDJPY_1H, "2020-11-30T01:00:00Z")
    merged = merge_refill_bars(original, [(_raw_file(USDJPY_1H), (added,))])
    assert len(merged[USDJPY_1H]) == len(original[USDJPY_1H]) + 1
    starts = [bar.bar_start.value for bar in merged[USDJPY_1H]]
    assert starts == sorted(starts)
    assert merged[USDJPY_15M] == original[USDJPY_15M]


def test_a_refilled_bar_overlapping_the_raw_data_fails_as_a_duplicate() -> None:
    original = {series: tuple(bars) for series, bars in raw_bars().items()}
    overlapping = _bar(USDJPY_1H, "2020-11-30T00:00:00Z")
    with pytest.raises(IntegrityCheckFailed, match="DUPLICATE_TIMESTAMP") as raised:
        merge_refill_bars(original, [(_raw_file(USDJPY_1H), (overlapping,))])
    # 文字列だけでなく、系列・時刻・件数を持つ検査結果として残る（D03 v1.17 §14.11）。
    report = raised.value.report
    assert report is not None
    (finding,) = report.results
    assert finding.kind is CheckKind.DUPLICATE_TIMESTAMP
    assert finding.series == USDJPY_1H
    assert str(finding.interval.start) == "2020-11-30T00:00:00Z"
    assert dict(finding.detail)["rows"] == "2"
    twice = _bar(USDJPY_1H, "2020-11-30T01:00:00Z")
    with pytest.raises(IntegrityCheckFailed, match="DUPLICATE_TIMESTAMP"):
        merge_refill_bars(
            original,
            [(_raw_file(USDJPY_1H), (twice,)), (_raw_file(USDJPY_1H, "other.csv"), (twice,))],
        )


def test_a_refill_only_fills_raw_series_with_refilled_bars() -> None:
    original = {series: tuple(bars) for series, bars in raw_bars().items()}
    eurusd = market.series(market.EURUSD, "1h")
    with pytest.raises(MarketDataValueError, match="not among the accepted raw series"):
        merge_refill_bars(original, [(_raw_file(eurusd), (_bar(eurusd, "2020-11-30T01:00:00Z"),))])
    histdata = _bar(USDJPY_1H, "2020-11-30T01:00:00Z", kind=ProvenanceKind.HISTDATA)
    with pytest.raises(MarketDataValueError, match="dukascopy_refill"):
        merge_refill_bars(original, [(_raw_file(USDJPY_1H), (histdata,))])


def test_refills_written_before_v1_19_are_still_read() -> None:
    """形式 v1（配信元の値の差の記録を持たない。代表例の試行の補充分）もそのまま読む。"""
    root = Path(__file__).resolve().parents[3] / "data/raw/market/refill"
    directories = [
        path
        for path in sorted(root.iterdir())
        if not path.name.startswith("_")
        and json.loads((path / "refill_manifest.json").read_text())["format"]
        == "refill_manifest_v1"
    ]
    assert directories  # 代表例の試行（2026-10-01）の補充分
    for directory in directories:
        manifest_payload = json.loads((directory / "refill_manifest.json").read_text())
        manifest = RefillManifest.from_payload(manifest_payload)
        assert manifest.source_differences == ()
        validation = RefillValidationRecord.from_payload(
            json.loads((directory / "validation.json").read_text())
        )
        assert (validation.rounding_matched_count, validation.source_differences) == (None, ())
